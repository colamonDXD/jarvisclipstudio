"""Subject-aware reframe planning for the EDL (auto-director) render path.

The EDL renderer historically framed every segment with one fixed centre
crop (``scale=...:force_original_aspect_ratio=increase,crop=1080:1920``), so
anyone standing outside the middle third of the frame simply vanished from
the clip. The plain (non-EDL) path has a full subject-aware stack — YuNet
face tracking, spring-smoothed crop trajectories, per-shot face/fit layout
decisions — but the EDL route bypassed all of it.

This module is the wiring between the two:

* :func:`get_source_analysis` runs the existing single-pass analysis
  (``analyze_vertical_clip``) once per source video over the union of the
  keep ranges a task actually renders, and caches the raw evidence —
  per-sample face boxes (ALL of them, not just the largest), scene cuts and
  per-sample context evidence — in a ``<source>.reframe_cache.json``
  sidecar, mirroring the transcript cache. Later clips and re-renders of
  the same source pay ~zero detection cost.
* :func:`build_reframe_plan` turns the cached evidence into a per-shot
  plan on the clip's compacted (keep-range) timeline: greedy-IoU identity
  association inside each shot (the analysis' largest-face-per-sample pick
  can hop between people), a subject-switch hysteresis, the existing
  context layout reasons for face-vs-fit, and the existing trajectory
  builder for the crop path.
* :func:`attach_reframe_plan` stores the plan in the EDL (a ``reframe``
  section with per-shot modes/reasons, plus resolved per-segment crop
  data) for ``media.edl_render`` to consume as per-segment crop chains.

Everything here is CPU-only analysis at 480 px wide; no GPU/VRAM is used.
Planning is defensive: any failure leaves the EDL untouched, and the clip
renders exactly as it did before this module existed.
"""

from __future__ import annotations

import bisect
import dataclasses
import json
import os
import threading
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

from .common import logger
from .ffmpeg import clamp_even, ffprobe_duration, output_offsets_for_ranges, rendered_ranges_duration, round_to_even
from .reframing import (
    build_crop_trajectory,
    build_fit_layers,
    build_smooth_pan_expression,
    compute_vertical_crop_dims,
    trajectory_has_movement,
)
from .scene_context import SceneEvidence, build_context_layout_plan, context_reason

# --- Cache -----------------------------------------------------------------

# v2: SceneEvidence gained motion_center; a v1 cache would silently
# read it as the 0.5 default and mis-anchor no-face fallback crops.
# v3: SceneEvidence gained subject_box / subject_cov / subject_source;
# a v2 cache would silently read them as absent and lose the bright
# (non-face) subject the fallback anchor and amputation audit now use.
# v4: subject evidence may now come from the gated YOLOX person pass
# (subject_source "yolox"); a v3 cache holds bright-only evidence and
# would silently read no-face samples as subject-less again.
# v5: the reframing face-presence floor dropped 0.002 -> 0.0005
# (REFRAME_FACE_PRESENCE_MIN_AREA, MIN_AREA_SPIKE.md); a v4 cache
# holds evidence computed under the old floor and would silently read
# small real faces (wide-shot presenters, helmet visors, night
# groups) as absent.
# v6: SceneEvidence gained depicted_count and the depicted grid
# screen (DEPICTED_FACES_SPIKE.md) demotes printed portrait-row
# boxes out of the significant set; a v5 cache holds evidence where
# wall portraits inflate face_count / face_span, join the protect
# spans and close the bright / YOLOX gates.
REFRAME_CACHE_SCHEMA_VERSION = 6

# Analysis is scoped to the keep ranges plus this much padding on each side,
# so a first task on a long source does not pay for the whole file.
ANALYSIS_PAD_SECONDS = 2.0

# Sources longer than this are sampled at 2 fps instead of 3 fps; the
# trajectory math is time-based, so the sparser grid costs nothing visually.
LONG_SOURCE_SECONDS = 1200.0

# --- Association / planning constants ---------------------------------------

# Greedy IoU linking: at 3 fps from a 480 px pass, faces move slowly and
# boxes are large, so 0.3 links reliably; matches may bridge one missed
# detection (two sample steps).
IOU_LINK_MIN = 0.3
IOU_MAX_GAP_STEPS = 2

# A challenger track must outscore the current subject continuously for
# this long before the crop retargets; shorter wins are treated as noise
# (someone leaning toward the camera is not a new subject).
SUBJECT_SWITCH_HYSTERESIS_SECONDS = 0.8

# A track counts as a shot's subject candidate when it is on screen for at
# least this share of the shot with a non-trivial mean face size.
TRACK_MIN_PRESENCE_SHARE = 0.35
TRACK_MIN_MEAN_AREA_FRAC = 0.004

# Two faces whose combined span fits the crop window share a pair crop
# instead of being split. The margin is exactly 1.0: a span even slightly
# wider than the window would make the crop bisect one of the two faces,
# so anything wider must split (or stay full-frame), never pair-crop.
PAIR_SPAN_MARGIN = 1.0

# Split-stack is only worth its visual cost on shots at least this long;
# shorter two-shots fall through to a full-frame fit.
SPLIT_MIN_SECONDS = 2.0

# Fit reasons that say "a crop centred on the dominant face would lose the
# interaction", NOT "this frame's content must be preserved whole". The
# context planner cannot express pair/split/single-subject layouts, so it
# answers fit for exactly the wide shots this planner exists to reframe;
# honouring those answers verbatim turned every two-host wide shot into a
# blurred-pad strip (the composition defect in real exports). Fit entries
# with one of these reasons are re-decided here from the face tracks.
RESCUABLE_FIT_REASONS = {
    "interaction_exceeds_crop",
    "subjects_spread_across_frame",
    "subject_exceeds_crop",
    "no_reliable_close_subject",
    "peripheral_presenter",
    "stable_context",
}

# Canonical explicit reasons for fit shots that survive re-deciding. A fit
# is a deliberate fallback with a named cause, never a silent default.
FIT_REASON_ALIASES = {
    "text_cut_by_crop": "text_heavy",
}

# Relaxed subject thresholds for rescuing a fit entry: a lone speaker in a
# wide shot (face well under the context planner's 0.8% close-subject bar)
# is still a subject a 9:16 crop can frame at ~3x the fitted size, as long
# as the track is persistent and it is the only candidate.
RESCUE_MIN_MEAN_AREA_FRAC = 0.002
RESCUE_MIN_PRESENCE_SHARE = 0.5

# --- Fit-blur guard -----------------------------------------------------------
#
# A fit composition renders the source twice: sharp, decrease-fitted into
# the centred foreground strip, and blurred as a cover-scaled backdrop
# (``edl_render``'s reframe backdrop + ``reframing.FIT_CHAIN``). The
# backdrop magnifies faces near the top or bottom of the source frame
# into the pad bands as large blurred faces (self-run audit issue #5:
# the wide-group fit at r1 12.5-20s shows the middle subject's blurred
# head filling the top band). A fit may only ship when no grounded
# face's backdrop copy lands in the bands; the share of the copy visible
# outside the foreground strip decides, so a hairline straddle of the
# strip edge does not flip an otherwise good layout.
FIT_BLUR_BAND_MIN_SHARE = 0.2

# Marker reason stamped on shots the fit-blur guard re-decided, so logs
# and QC can tell a guard decision from a context/rescue decision.
FIT_BLUR_GUARD_REASON = "face_blur_avoided"

# Deliberate content-preservation fits are exempt from the guard only
# while they carry no grounded face track: their whole point is keeping
# the text / screen content a crop would cut, and with no face in the
# shot there is nothing for the pad bands to blur. The moment a grounded
# face track exists the face is the shot's subject and the exemption
# ends — frame-edge text does not outrank the speaker (the TJR framing
# flip). The guard governs subject shots, where the face IS the content.
FIT_BLUR_EXEMPT_REASONS = {"text_cut_by_crop", "screen_content_outside_crop"}

Box = Tuple[float, float, float, float]
Sample = Tuple[float, List[Box]]

_ANALYSIS_LOCK = threading.Lock()


def _probe_mode() -> str:
    from .reframing import hires_probe_enabled

    return "hires" if hires_probe_enabled() else "std"


def reframe_cache_path(source_path: Path) -> Path:
    return Path(source_path).with_suffix(".reframe_cache.json")


def _source_stamp(source_path: Path) -> Optional[Tuple[int, int]]:
    try:
        stat = Path(source_path).stat()
    except OSError:
        return None
    return stat.st_size, stat.st_mtime_ns


def _empty_cache(source_path: Path, width: int, height: int, sample_fps: float) -> Dict[str, Any]:
    stamp = _source_stamp(source_path)
    return {
        "version": REFRAME_CACHE_SCHEMA_VERSION,
        "source_size": stamp[0] if stamp else -1,
        "source_mtime_ns": stamp[1] if stamp else -1,
        "width": width,
        "height": height,
        "sample_fps": sample_fps,
        "probe": _probe_mode(),
        "covered_spans": [],
        "samples": [],  # [{"t": float, "boxes": [[x, y, w, h], ...]}]
        "cuts": [],
        "context": [],  # [SceneEvidence as dict]
    }


def _load_cache(source_path: Path) -> Optional[Dict[str, Any]]:
    """The stored cache when it still describes this exact source file."""
    path = reframe_cache_path(source_path)
    if not path.is_file():
        return None
    stamp = _source_stamp(source_path)
    if stamp is None:
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(payload, dict):
        return None
    if payload.get("version") != REFRAME_CACHE_SCHEMA_VERSION:
        return None
    if payload.get("probe", "std") != _probe_mode():
        # Analysed at the other resolution (REFRAME_HIRES_PROBE flipped):
        # its face boxes would not describe what this mode detects.
        return None
    if payload.get("source_size") != stamp[0] or payload.get("source_mtime_ns") != stamp[1]:
        logger.info(
            "Reframe cache for %s is stale (source changed); re-analyzing",
            source_path,
        )
        return None
    return payload


def _save_cache(source_path: Path, payload: Dict[str, Any]) -> None:
    """Best-effort atomic write; a failed cache write never fails a render."""
    path = reframe_cache_path(source_path)
    stamp = _source_stamp(source_path)
    if stamp:
        payload["source_size"], payload["source_mtime_ns"] = stamp
    tmp = path.with_name(path.name + ".tmp")
    try:
        tmp.write_text(json.dumps(payload), encoding="utf-8")
        os.replace(tmp, path)
    except OSError as exc:
        logger.warning("Could not write reframe cache %s: %s", path, exc)
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass


def _merge_spans(spans: Sequence[Tuple[float, float]]) -> List[Tuple[float, float]]:
    merged: List[List[float]] = []
    for start, end in sorted(spans):
        if merged and start <= merged[-1][1] + 0.05:
            merged[-1][1] = max(merged[-1][1], end)
        else:
            merged.append([start, end])
    return [(start, end) for start, end in merged]


def _uncovered(
    wanted: Sequence[Tuple[float, float]], covered: Sequence[Tuple[float, float]]
) -> List[Tuple[float, float]]:
    """The parts of ``wanted`` not already inside ``covered`` (0.05s slack)."""
    missing: List[Tuple[float, float]] = []
    for start, end in wanted:
        cursor = start
        for cov_start, cov_end in covered:
            if cov_end <= cursor + 0.05:
                continue
            if cov_start >= end - 0.05:
                break
            if cov_start > cursor + 0.05:
                missing.append((cursor, min(cov_start, end)))
            cursor = max(cursor, cov_end)
            if cursor >= end - 0.05:
                break
        if cursor < end - 0.05:
            missing.append((cursor, end))
    return [(s, e) for s, e in missing if e - s > 0.05]


def _serialize_analysis(
    cache: Dict[str, Any],
    samples: List[Sample],
    cuts: List[float],
    context: List[SceneEvidence],
    span: Tuple[float, float],
) -> None:
    """Merge one analyzed window into the cache payload (dedupe by time)."""
    by_time: Dict[float, List[Box]] = {}
    for entry in cache.get("samples") or []:
        try:
            by_time[round(float(entry["t"]), 3)] = [
                tuple(float(v) for v in box) for box in entry.get("boxes") or []
            ]
        except (TypeError, ValueError):
            continue
    for t, boxes in samples:
        by_time[round(float(t), 3)] = [tuple(float(v) for v in box) for box in boxes]
    cache["samples"] = [
        {"t": t, "boxes": [[round(v, 1) for v in box] for box in by_time[t]]}
        for t in sorted(by_time)
    ]
    cache["cuts"] = sorted({round(float(c), 3) for c in (cache.get("cuts") or [])} | {round(float(c), 3) for c in cuts})
    context_by_time: Dict[float, Dict[str, Any]] = {}
    for entry in cache.get("context") or []:
        try:
            context_by_time[round(float(entry["time"]), 3)] = entry
        except (TypeError, ValueError, KeyError):
            continue
    for evidence in context:
        context_by_time[round(float(evidence.time), 3)] = dataclasses.asdict(evidence)
    cache["context"] = [context_by_time[t] for t in sorted(context_by_time)]
    cache["covered_spans"] = [
        list(span) for span in _merge_spans(
            [tuple(map(float, s)) for s in (cache.get("covered_spans") or [])] + [span]
        )
    ]


def _cache_view(cache: Dict[str, Any]) -> Dict[str, Any]:
    """The cache payload as typed analysis data."""
    samples: List[Sample] = []
    for entry in cache.get("samples") or []:
        boxes = [tuple(float(v) for v in box) for box in entry.get("boxes") or []]
        samples.append((float(entry["t"]), boxes))
    samples.sort(key=lambda item: item[0])
    context: List[SceneEvidence] = []
    for entry in cache.get("context") or []:
        try:
            context.append(SceneEvidence(**entry))
        except (TypeError, ValueError):
            continue
    context.sort(key=lambda item: item.time)
    return {
        "width": int(cache.get("width") or 0),
        "height": int(cache.get("height") or 0),
        "samples": samples,
        "cuts": sorted(float(c) for c in cache.get("cuts") or []),
        "context": context,
    }


def get_source_analysis(
    source_path: Path, spans: Sequence[Tuple[float, float]]
) -> Optional[Dict[str, Any]]:
    """Face/context evidence for ``spans`` of the source, cached per source.

    Returns {"width", "height", "samples", "cuts", "context"} with samples
    as (t, [boxes]) in source pixels and source time, or None when the
    source cannot be probed at all (callers then skip reframe planning).
    Analysis is CPU-only and runs under a process lock so concurrent clips
    of one task extend the cache instead of duplicating the decode.
    """
    from .reframing import analyze_vertical_clip

    source_path = Path(source_path)
    with _ANALYSIS_LOCK:
        try:
            duration = ffprobe_duration(source_path)
        except Exception:
            duration = 0.0
        if duration <= 0:
            return None
        sample_fps = 2.0 if duration > LONG_SOURCE_SECONDS else 3.0
        cache = _load_cache(source_path)
        if cache is not None and float(cache.get("sample_fps") or 0) != sample_fps:
            cache = None  # sampling grid changed; rebuild rather than mix grids
        wanted = _merge_spans(
            [
                (max(0.0, start - ANALYSIS_PAD_SECONDS), min(duration, end + ANALYSIS_PAD_SECONDS))
                for start, end in spans
                if end > start
            ]
        )
        if cache is not None:
            covered = [tuple(map(float, s)) for s in cache.get("covered_spans") or []]
            missing = _uncovered(wanted, covered)
            if not missing:
                logger.info(
                    "Reframe analysis cache hit for %s (spans %s)",
                    source_path,
                    [(round(s, 1), round(e, 1)) for s, e in wanted],
                )
                return _attach_speaking(_cache_view(cache), source_path, wanted)
        else:
            missing = wanted
        failed_spans = False
        for start, end in missing:
            logger.info(
                "Analyzing source span %.1f-%.1fs of %s for reframe planning",
                start,
                end,
                source_path,
            )
            try:
                _track, cuts, face_samples, context = analyze_vertical_clip(
                    source_path,
                    sample_fps=sample_fps,
                    start_time=start,
                    duration=end - start,
                )
            except Exception as exc:
                # Analysis failure degrades to an evidence-less plan (the
                # planner answers all-fit), never to a failed render. The
                # span is NOT marked covered, so a later task retries it.
                logger.warning("Reframe analysis failed for %s: %s", source_path, exc)
                failed_spans = True
                continue
            if cache is None:
                width, height = _analysis_geometry(source_path)
                if width <= 0 or height <= 0:
                    return None
                cache = _empty_cache(source_path, width, height, sample_fps)
            _serialize_analysis(cache, face_samples, cuts, context, (start, end))
        if cache is None:
            if not failed_spans:
                return None
            width, height = _analysis_geometry(source_path)
            if width <= 0 or height <= 0:
                return None
            return {
                "width": width,
                "height": height,
                "samples": [],
                "cuts": [],
                "context": [],
            }
        _save_cache(source_path, cache)
        return _attach_speaking(_cache_view(cache), source_path, wanted)


def _attach_speaking(
    view: Dict[str, Any],
    source_path: Path,
    spans: Sequence[Tuple[float, float]],
) -> Dict[str, Any]:
    """Attach dense speaking evidence when the deployment opted in.

    Flag off (the default) or any failure: the view is returned
    untouched, so plans stay byte-identical to today's.
    """
    try:
        from .active_speaker import active_speaker_enabled, get_speaking_analysis

        if not active_speaker_enabled():
            return view
        speaking = get_speaking_analysis(source_path, spans)
        if speaking:
            view["speaking"] = speaking
    except Exception as exc:  # evidence is advisory; never break planning
        logger.debug("Active-speaker analysis unavailable: %s", exc)
    return view


def _analysis_geometry(source_path: Path) -> Tuple[int, int]:
    from .ffmpeg import ffprobe_video_size

    try:
        return ffprobe_video_size(source_path)
    except Exception:
        return 0, 0


# --- Identity association (spec: greedy IoU + switch hysteresis) ------------


def _iou(a: Box, b: Box) -> float:
    ax, ay, aw, ah = a
    bx, by, bw, bh = b
    x0, y0 = max(ax, bx), max(ay, by)
    x1, y1 = min(ax + aw, bx + bw), min(ay + ah, by + bh)
    if x1 <= x0 or y1 <= y0:
        return 0.0
    intersection = (x1 - x0) * (y1 - y0)
    union = aw * ah + bw * bh - intersection
    return intersection / union if union > 0 else 0.0


class _Track:
    """One identity inside a shot: the samples where its face was seen."""

    __slots__ = ("observations", "last_index", "last_box")

    def __init__(self) -> None:
        self.observations: Dict[int, Box] = {}
        self.last_index = -1
        self.last_box: Optional[Box] = None

    def add(self, index: int, box: Box) -> None:
        self.observations[index] = box
        self.last_index = index
        self.last_box = box

    @property
    def present_count(self) -> int:
        return len(self.observations)

    @property
    def mean_area(self) -> float:
        if not self.observations:
            return 0.0
        return sum(b[2] * b[3] for b in self.observations.values()) / len(self.observations)

    @property
    def score(self) -> float:
        """Persistence score: time on screen weighted by face size."""
        return self.present_count * self.mean_area

    def median_box(self) -> Optional[Box]:
        if not self.observations:
            return None
        boxes = sorted(self.observations.values())
        mid = len(boxes) // 2
        columns = list(zip(*boxes))
        return tuple(sorted(col)[mid] for col in columns)  # type: ignore[return-value]

    def union_box(self) -> Optional[Box]:
        """Envelope of every observed box in the shot.

        This is the text guard's input: a moving face must be covered
        at its extremes, not at its median — a title placed clear of
        the median box still lands on the face the moment the subject
        leans into the edge of their range.
        """
        if not self.observations:
            return None
        boxes = list(self.observations.values())
        x0 = min(b[0] for b in boxes)
        y0 = min(b[1] for b in boxes)
        x1 = max(b[0] + b[2] for b in boxes)
        y1 = max(b[1] + b[3] for b in boxes)
        return (x0, y0, x1 - x0, y1 - y0)


def associate_tracks(samples: Sequence[Sample]) -> List[_Track]:
    """Link per-sample face boxes into identities by greedy IoU matching.

    Boxes are considered largest-first so the dominant face claims its
    track before smaller background faces compete for it. A match may
    bridge a gap of up to ``IOU_MAX_GAP_STEPS`` samples (one or two missed
    detections) when the overlap is still convincing.
    """
    tracks: List[_Track] = []
    for index, (_t, boxes) in enumerate(samples):
        matched: set[int] = set()
        for box in sorted(boxes, key=lambda b: b[2] * b[3], reverse=True):
            best: Optional[int] = None
            best_iou = IOU_LINK_MIN
            for track_index, track in enumerate(tracks):
                if track_index in matched or track.last_box is None:
                    continue
                if index - track.last_index > IOU_MAX_GAP_STEPS:
                    continue
                value = _iou(box, track.last_box)
                if value >= best_iou:
                    best, best_iou = track_index, value
            if best is None:
                track = _Track()
                track.add(index, box)
                tracks.append(track)
                matched.add(len(tracks) - 1)
            else:
                tracks[best].add(index, box)
                matched.add(best)
    return tracks


def subject_series(
    samples: Sequence[Sample],
    tracks: Sequence[_Track],
    *,
    sample_weights=None,
) -> List[Optional[Box]]:
    """The subject's box per sample, with switch hysteresis.

    The initial subject is the highest-scoring track. A challenger takes
    over only after outscoring the incumbent continuously for at least
    ``SUBJECT_SWITCH_HYSTERESIS_SECONDS`` — the window is evaluated from
    suffix scores (presence from that sample onward x mean area), so a
    face that merely leans closer for a moment never steals the frame,
    while a genuinely persistent new subject does. An incumbent who
    vanishes for the hysteresis window hands over the same way.

    ``sample_weights`` (active-speaker targeting, flag-gated) is an
    optional ``(track_index, sample_index) -> float`` multiplier on a
    track's per-sample presence contribution: speaking evidence can
    reorder preference among present faces, nothing more. With the
    default None the arithmetic is the original, term for term.
    """
    count = len(samples)
    if count == 0 or not tracks:
        return [None] * count
    times = [t for t, _ in samples]
    if count > 1:
        spacings = sorted(
            b - a for a, b in zip(times, times[1:]) if b > a
        )
        dt = spacings[len(spacings) // 2] if spacings else 1.0 / 3.0
    else:
        dt = 1.0 / 3.0
    window = max(1, int(round(SUBJECT_SWITCH_HYSTERESIS_SECONDS / max(dt, 0.05))))

    # suffix[track][i] = score of the track's remaining presence from i on.
    suffix: List[List[float]] = []
    for track_index, track in enumerate(tracks):
        row = [0.0] * (count + 1)
        for i in range(count - 1, -1, -1):
            contribution = track.mean_area if i in track.observations else 0.0
            if contribution and sample_weights is not None:
                contribution *= sample_weights(track_index, i)
            row[i] = row[i + 1] + contribution
        suffix.append(row)

    def present(track: _Track, i: int) -> bool:
        return i in track.observations

    def best_at(i: int, *, require_window: bool) -> Optional[int]:
        """Highest suffix-scored track present at i (optionally: and holding)."""
        best: Optional[int] = None
        for track_index, track in enumerate(tracks):
            if not present(track, i):
                continue
            if require_window:
                horizon = min(count, i + window)
                if any(not present(track, j) for j in range(i, horizon)):
                    continue
            if best is None or suffix[track_index][i] > suffix[best][i]:
                best = track_index
        return best

    series: List[Optional[Box]] = []
    current = best_at(0, require_window=False)
    if current is None:
        # Nobody in the first sample: adopt the first track that appears.
        for i in range(count):
            current = best_at(i, require_window=False)
            if current is not None:
                break
    for i in range(count):
        if current is None:
            current = best_at(i, require_window=False)
        if current is not None and present(tracks[current], i):
            # Incumbent on screen: only a sustained outscoring challenger
            # takes over, effective from the start of its winning window.
            challenger: Optional[int] = None
            for track_index, track in enumerate(tracks):
                if track_index == current or not present(track, i):
                    continue
                if suffix[track_index][i] <= suffix[current][i]:
                    continue
                horizon = min(count, i + window)
                if all(
                    present(track, j) and suffix[track_index][j] >= suffix[current][j]
                    for j in range(i, horizon)
                ):
                    if challenger is None or suffix[track_index][i] > suffix[challenger][i]:
                        challenger = track_index
            if challenger is not None:
                current = challenger
        elif current is not None:
            # Incumbent missing: a replacement must prove it stays.
            replacement = best_at(i, require_window=True)
            if replacement is not None:
                # Switch only once the absence itself spans the window, so
                # a single missed detection never retargets the frame.
                absent_from = i
                while absent_from > 0 and not present(tracks[current], absent_from - 1):
                    absent_from -= 1
                if i - absent_from + 1 >= window or replacement == current:
                    current = replacement
        series.append(
            tracks[current].observations.get(i) if current is not None else None
        )
    return series


# --- Active-speaker targeting (flag-gated, advisory) --------------------------
#
# When ACTIVE_SPEAKER_TARGETING_ENABLED is on and the analysis carries
# dense speaking evidence (media/active_speaker.py), the single-subject
# series may prefer the face that is actually talking: windowed
# mouth-aperture/audio-envelope correlations become bounded per-sample
# weights for subject_series. The bias only reorders preference among
# faces the tracks already grounded — split/pair/fit decisions, the
# significant-track rules and Plain's geometry never see it — and any
# missing or failed evidence leaves today's behaviour byte-identical.


def _track_aperture_series(
    sample_times: Sequence[float],
    track: _Track,
    asd_samples: Sequence[Tuple[float, float, Sequence]],
) -> List[Optional[float]]:
    """This track's mouth aperture per dense (ASD) sample, IoU-matched."""
    observed = sorted(track.observations.items())
    obs_times = [sample_times[i] for i, _box in observed]
    series: List[Optional[float]] = []
    for t, _env, faces in asd_samples:
        aperture: Optional[float] = None
        pos = bisect.bisect_left(obs_times, t)
        nearest: Optional[Box] = None
        nearest_dt = 0.34  # bounded by the ~3 fps shot-sample spacing
        for cand in (pos - 1, pos):
            if 0 <= cand < len(observed):
                dt = abs(obs_times[cand] - t)
                if dt <= nearest_dt:
                    nearest_dt = dt
                    nearest = observed[cand][1]
        if nearest is not None and faces:
            best_iou = 0.25
            for face_box, face_aperture in faces:
                value = _iou(tuple(face_box[:4]), nearest)
                if value >= best_iou:
                    best_iou = value
                    aperture = float(face_aperture)
        series.append(aperture)
    return series


def _speaking_bias(
    analysis: Dict[str, Any],
    shot_samples: Sequence[Sample],
    tracks: Sequence[_Track],
    pieces: Sequence[Sequence[float]],
) -> Optional[Dict[str, Any]]:
    """Speaking-evidence bias for one shot, or None for "no evidence".

    Returns {"weights": callable, "windows": [(src_t0, src_t1,
    winner_track_index, corr, margin)], "winner_track": int} — windows
    in SOURCE time; the caller maps the stamp into compacted time.
    """
    try:
        from . import active_speaker

        if not active_speaker.active_speaker_enabled():
            return None
    except Exception:
        return None
    if len(tracks) < 2 or not shot_samples:
        return None
    speaking = analysis.get("speaking") if isinstance(analysis, dict) else None
    samples = speaking.get("samples") if isinstance(speaking, dict) else None
    if not samples:
        return None
    # pieces are (compact_start, compact_end, source_start, source_end,
    # offset, base) — the bias works in source time like the samples do.
    spans = [(piece[2], piece[3]) for piece in pieces]
    if not spans:
        return None
    asd = [
        sample
        for sample in samples
        if any(start - 0.05 <= sample[0] < end + 0.05 for start, end in spans)
    ]
    if len(asd) < active_speaker.ASD_MIN_WINDOW_PAIRS:
        return None
    times = [float(sample[0]) for sample in asd]
    envelope = [float(sample[1]) for sample in asd]
    sample_times = [t for t, _boxes in shot_samples]
    apertures = {
        track_index: _track_aperture_series(sample_times, track, asd)
        for track_index, track in enumerate(tracks)
    }
    # A window's pull is its winner's correlation MARGIN over the
    # runner-up, not the raw correlation: a listener who mouths along
    # or reacts on the beat correlates too, and contrast is what
    # separates the source from the mimic. On the fixture this beats
    # raw-correlation weighting (53% vs 50% through this seam) and is
    # insensitive to the admission bar below it.
    windows: List[Tuple[float, float, int, float, float]] = []
    win_counts: Dict[int, int] = {}
    win_corr: Dict[int, float] = {}
    for t0, t1, scores in active_speaker.windowed_correlations(
        times, apertures, envelope
    ):
        ranked = sorted(scores.values(), reverse=True)
        winner = max(scores, key=lambda k: scores[k])
        corr = scores[winner]
        if corr < active_speaker.ASD_MIN_CORRELATION:
            continue
        margin = corr - ranked[1] if len(ranked) > 1 else corr
        windows.append((t0, t1, int(winner), corr, max(0.0, margin)))
        win_counts[winner] = win_counts.get(winner, 0) + 1
        win_corr[winner] = win_corr.get(winner, 0.0) + corr
    if not windows:
        return None
    winner_track = max(
        win_counts, key=lambda k: (win_counts[k], win_corr.get(k, 0.0))
    )

    def weights(track_index: int, sample_index: int) -> float:
        t = sample_times[sample_index]
        weight = 1.0
        for t0, t1, winner, _corr, margin in windows:
            if winner == track_index and t0 <= t < t1:
                weight = max(
                    weight, 1.0 + active_speaker.ASD_BIAS_STRENGTH * margin
                )
        return weight

    return {"weights": weights, "windows": windows, "winner_track": winner_track}


def _speaking_stamp(
    bias: Dict[str, Any],
    tracks: Sequence[_Track],
    ranges: Sequence[Tuple[float, float]],
    offsets: Sequence[float],
) -> Optional[Dict[str, Any]]:
    """Plan-facing evidence record: windows in compacted time.

    The winner is identified by its track's median-box centre so the
    stamp stays meaningful after the plan leaves the planner.
    """
    stamped = []
    for t0, t1, winner, corr, _margin in bias["windows"]:
        start = _map_source_time(t0, ranges, offsets)
        end = _map_source_time(t1, ranges, offsets)
        if start is None or end is None:
            continue
        median = tracks[winner].median_box() if 0 <= winner < len(tracks) else None
        stamped.append(
            {
                "start": round(start, 3),
                "end": round(end, 3),
                "winner_cx": round(median[0] + median[2] / 2.0, 1) if median else None,
                "winner_cy": round(median[1] + median[3] / 2.0, 1) if median else None,
                "corr": round(corr, 3),
            }
        )
    if not stamped:
        return None
    return {"windows": stamped}


# --- Time mapping ------------------------------------------------------------

def _map_source_time(
    t: float,
    ranges: Sequence[Tuple[float, float]],
    offsets: Sequence[float],
) -> Optional[float]:
    """Source time -> compacted clip time (None outside the keep ranges)."""
    last_index = len(ranges) - 1
    for index, ((start, end), offset) in enumerate(zip(ranges, offsets)):
        if start <= t < end or (t == end and index == last_index):
            return offset + (t - start)
    return None


def _entry_source_pieces(
    start: float,
    end: float,
    ranges: Sequence[Tuple[float, float]],
    offsets: Sequence[float],
) -> List[Tuple[float, float, float, float, float, float]]:
    """Compacted [start, end] split by keep-range junctions.

    Returns [(compact_start, compact_end, source_start, source_end,
    range_offset, range_start)] — the last two map the piece's source
    times back to compacted time.
    """
    pieces: List[Tuple[float, float, float, float, float, float]] = []
    for (range_start, range_end), offset in zip(ranges, offsets):
        piece_start = max(start, offset)
        piece_end = min(end, offset + (range_end - range_start))
        if piece_end - piece_start > 0.01:
            pieces.append(
                (
                    piece_start,
                    piece_end,
                    range_start + (piece_start - offset),
                    range_start + (piece_end - offset),
                    offset,
                    range_start,
                )
            )
    return pieces


# --- Per-shot planning ---------------------------------------------------------

def _split_entries_at_turnovers(
    entries: List[Dict[str, Any]],
    samples: Sequence[Sample],
    ranges: Sequence[Tuple[float, float]],
    offsets: Sequence[float],
) -> List[Dict[str, Any]]:
    """Split layout entries where the face stream turns over completely.

    The context analyzer's cut detector is difference-based and can miss
    a hard change on a static background (one speaker replaced by two on
    the same set: the frame difference is small, but every face is a new
    face). For framing, the tracker losing every identity at one step —
    no box on either side links across it — is a shot boundary in its own
    right: identity association and the mode decision must not span it,
    or the merged "shot" plans one crop for two different scenes.
    """
    turnovers: List[float] = []
    ordered = sorted(samples)
    for (_t_prev, boxes_prev), (t_cur, boxes_cur) in zip(ordered, ordered[1:]):
        if not boxes_prev or not boxes_cur:
            continue
        if max(_iou(a, b) for a in boxes_prev for b in boxes_cur) >= IOU_LINK_MIN:
            continue
        mapped = _map_source_time(t_cur, ranges, offsets)
        if mapped is not None:
            turnovers.append(mapped)
    if not turnovers:
        return entries
    out: List[Dict[str, Any]] = []
    for entry in entries:
        start, end = float(entry["start"]), float(entry["end"])
        inside = sorted(t for t in turnovers if start + 0.3 < t < end - 0.3)
        points = [start, *inside, end]
        for sub_start, sub_end in zip(points, points[1:]):
            out.append({**entry, "start": sub_start, "end": sub_end})
    return out


def _split_region(median_box: Box, width: int, height: int) -> Dict[str, int]:
    """Stacked-tile crop for one face, mirroring cluster_two_face_regions."""
    from .ffmpeg import round_to_even

    cx = median_box[0] + median_box[2] / 2
    cy = median_box[1] + median_box[3] / 2
    face_size = max(1.0, (median_box[2] * median_box[3]) ** 0.5)
    tile_w = min(width, max(160, int(face_size * 2.8)))
    tile_h = min(height, max(160, int(face_size * 2.4)))
    tile_x = clamp_even(int(cx - tile_w // 2), 0, max(0, width - tile_w))
    tile_y = clamp_even(int(cy - int(tile_h * 0.42)), 0, max(0, height - tile_h))
    return {
        "tile_x": tile_x,
        "tile_y": tile_y,
        "tile_w": round_to_even(min(tile_w, width - tile_x)),
        "tile_h": round_to_even(min(tile_h, height - tile_y)),
    }


def _avoid_face_bisection(
    crop_x: int,
    crop_w: int,
    boxes: Sequence[Box],
    width: int,
    protect: Optional[Box] = None,
) -> int:
    """Nudge a crop window so its edges never slice through a face box.

    A face straddling an edge is either included whole (window shifted to
    the face's far side) or excluded whole (shifted past it), whichever
    moves the window least; when ``protect`` (the subject's box) is given,
    only shifts that keep the subject fully inside are accepted. Faces at
    least as wide as the window cannot be contained and are left alone —
    callers decide those shots are not crops at all.
    """
    max_x = max(0, width - crop_w)
    x = clamp_even(int(crop_x), 0, max_x)
    if crop_w >= width or not boxes:
        return x
    for _ in range(3):
        moved = False
        for box in sorted(boxes):
            bx, bw = float(box[0]), float(box[2])
            if bw >= crop_w:
                continue
            for edge, is_left in ((x, True), (x + crop_w, False)):
                if not (bx < edge < bx + bw):
                    continue
                if is_left:
                    candidates = (int(round(bx)), int(round(bx + bw)))
                else:
                    candidates = (
                        int(round(bx + bw - crop_w)),
                        int(round(bx - crop_w)),
                    )
                valid = []
                for candidate in candidates:
                    candidate = clamp_even(candidate, 0, max_x)
                    if bx < candidate < bx + bw or bx < candidate + crop_w < bx + bw:
                        continue  # clamping re-bisected this face
                    if protect is not None:
                        px, pw = float(protect[0]), float(protect[2])
                        if pw < crop_w and not (
                            candidate <= px and px + pw <= candidate + crop_w
                        ):
                            continue
                    valid.append(candidate)
                if valid:
                    best = min(valid, key=lambda c: abs(c - x))
                    if best != x:
                        x = best
                        moved = True
                break
        if not moved:
            break
    return x


def _expand_stable_context(
    entries: List[Dict[str, Any]],
    compact_context: Sequence[SceneEvidence],
    compact_cuts: Sequence[float],
    crop_fraction: float,
) -> List[Dict[str, Any]]:
    """Undo a whole-plan ``stable_context`` collapse at the shot level.

    ``stabilize_layout_plan`` can replace a mixed plan with one fit entry
    spanning the whole clip, erasing the per-shot decisions this planner
    needs (a two-host shot and a food close-up are not the same shot).
    Re-derive each sub-shot between the known cuts from its own context
    evidence, mirroring ``build_context_layout_plan``'s per-shot rule.
    Entries the stabilizer did not collapse pass through untouched.
    """
    from collections import Counter

    out: List[Dict[str, Any]] = []
    for entry in entries:
        if entry.get("kind") != "fit" or entry.get("reason") != "stable_context":
            out.append(entry)
            continue
        start, end = float(entry["start"]), float(entry["end"])
        inside_cuts = [c for c in compact_cuts if start + 0.3 < c < end - 0.3]
        if not inside_cuts:
            out.append(entry)
            continue
        points = [start, *inside_cuts, end]
        for sub_start, sub_end in zip(points, points[1:]):
            shot = [
                e for e in compact_context if sub_start <= e.time < sub_end
            ]
            risks = [
                r
                for r in (context_reason(e, crop_fraction) for e in shot)
                if r is not None
            ]
            if shot and len(risks) / len(shot) >= 0.3:
                out.append(
                    {
                        "start": sub_start,
                        "end": sub_end,
                        "kind": "fit",
                        "reason": Counter(risks).most_common(1)[0][0],
                        "soft": False,
                    }
                )
            elif shot:
                out.append(
                    {
                        "start": sub_start,
                        "end": sub_end,
                        "kind": "face",
                        "reason": "confident_subject",
                        "soft": False,
                    }
                )
            else:
                out.append(
                    {
                        "start": sub_start,
                        "end": sub_end,
                        "kind": "fit",
                        "reason": "analysis_unavailable",
                        "soft": False,
                    }
                )
    return out


def _trajectory_keys(
    series: Sequence[Tuple[float, Optional[Box]]],
    piece_start: float,
    piece_end: float,
    width: int,
    crop_w: int,
    frame_area: float,
    guard: Optional[Sequence[Tuple[float, Sequence[Box]]]] = None,
) -> List[Tuple[float, int]]:
    """Crop-x keyframes (compacted time) for one piece of a shot.

    ``series`` is the subject's (compacted_time, box|None) over the piece.
    The existing spring trajectory builder runs on piece-local times; a
    trajectory that barely moves collapses to one static key.
    """
    local = [
        (
            t - piece_start,
            (box[0] + box[2] / 2.0) if box is not None else None,
            (box[2] * box[3] / frame_area) if box is not None else 0.0,
        )
        for t, box in series
    ]
    keys = build_crop_trajectory(local, width, crop_w)
    if not keys:
        centers = [c for _t, c, _a in local if c is not None]
        if not centers:
            return []
        centers.sort()
        static_x = clamp_even(
            int(centers[len(centers) // 2] - crop_w / 2), 0, max(0, width - crop_w)
        )
        raw_keys = [(piece_start, static_x)]
    elif not trajectory_has_movement(keys, crop_w):
        xs = sorted(x for _t, x in keys)
        raw_keys = [(piece_start, xs[len(xs) // 2])]
    else:
        raw_keys = [(t + piece_start, x) for t, x in keys]
    if not guard:
        return raw_keys
    # Face-bisection guard: no key's window may slice through any face
    # detected at that moment; the subject's own box is protected first.
    subject_by_time = {round(t, 3): box for t, box in series}
    guarded: List[Tuple[float, int]] = []
    for key_t, key_x in raw_keys:
        nearest = min(guard, key=lambda item: abs(item[0] - key_t), default=None)
        if nearest is None or abs(nearest[0] - key_t) > 1.0 or not nearest[1]:
            guarded.append((key_t, key_x))
            continue
        protect = subject_by_time.get(round(nearest[0], 3))
        guarded.append(
            (key_t, _avoid_face_bisection(key_x, crop_w, nearest[1], width, protect))
        )
    return guarded


def _tracks_above(
    tracks: Sequence[_Track],
    sample_count: int,
    frame_area: float,
    min_presence_share: float,
    min_area_frac: float,
) -> List[_Track]:
    """Tracks persistent and large enough to count as grounded subjects."""
    return [
        track
        for track in tracks
        if track.present_count >= max(3, min_presence_share * max(1, sample_count))
        and track.mean_area / frame_area >= min_area_frac
    ]


def _rect_intersection_area(
    a: Tuple[float, float, float, float], b: Tuple[float, float, float, float]
) -> float:
    x0, y0 = max(a[0], b[0]), max(a[1], b[1])
    x1, y1 = min(a[0] + a[2], b[0] + b[2]), min(a[1] + a[3], b[1] + b[3])
    if x1 <= x0 or y1 <= y0:
        return 0.0
    return (x1 - x0) * (y1 - y0)


def _fit_blur_share(box: Box, width: int, height: int) -> float:
    """Share of a face's blurred-backdrop copy that lands in the fit pads.

    Mirrors the two layers a fit segment is composed from on the
    1080x1920 canvas: the backdrop cover-scales the source (centred), the
    foreground decrease-fits it (centred). The share of the backdrop copy
    that is visible in the frame but outside the foreground rect is the
    part of the face a viewer sees blurred. 0.0 when the copy is not
    visible at all or lands entirely inside the sharp foreground.
    """
    if width <= 0 or height <= 0 or box[2] <= 0 or box[3] <= 0:
        return 0.0
    bg_scale = max(1080.0 / width, 1920.0 / height)
    copy = (
        box[0] * bg_scale - (width * bg_scale - 1080.0) / 2,
        box[1] * bg_scale - (height * bg_scale - 1920.0) / 2,
        box[2] * bg_scale,
        box[3] * bg_scale,
    )
    visible = _rect_intersection_area(copy, (0.0, 0.0, 1080.0, 1920.0))
    if visible <= 0:
        return 0.0
    fg_scale = min(1080.0 / width, 1920.0 / height)
    fg_w, fg_h = width * fg_scale, height * fg_scale
    foreground = ((1080.0 - fg_w) / 2, (1920.0 - fg_h) / 2, fg_w, fg_h)
    sharp = _rect_intersection_area(copy, foreground)
    return max(0.0, (visible - sharp) / visible)


def _faces_blurred_by_fit(
    boxes: Sequence[Box], width: int, height: int
) -> bool:
    """True when any grounded face would land in a blurred pad band."""
    return any(
        _fit_blur_share(box, width, height) >= FIT_BLUR_BAND_MIN_SHARE
        for box in boxes
    )


def _track_blur_share(track: _Track, width: int, height: int) -> float:
    """A track's blur share: the median over its observed boxes.

    The per-coordinate median box is a synthetic box that may never
    occur (a drifting face's median sits lower and smaller than most
    real observations), so the guard judges the boxes the detector
    actually saw: the track blurs when at least half of its observed
    boxes land in the bands.
    """
    shares = sorted(
        _fit_blur_share(box, width, height)
        for box in track.observations.values()
    )
    if not shares:
        return 0.0
    return shares[len(shares) // 2]


def _tracks_blurred_by_fit(
    tracks: Sequence[_Track], width: int, height: int
) -> bool:
    """True when any grounded track would land in a blurred pad band."""
    return any(
        _track_blur_share(track, width, height) >= FIT_BLUR_BAND_MIN_SHARE
        for track in tracks
    )


def reactor_needs_inset() -> bool:
    """REFRAME_REACTOR_NEEDS_INSET (default on): only delegate a clip to the
    plain path when the reactor's camera rectangle can actually be measured.

    Off (``0``/``false``/``no``/``off``) restores the face-only check.
    """
    return os.getenv("REFRAME_REACTOR_NEEDS_INSET", "1").strip().lower() not in {
        "0", "false", "no", "off",
    }


def _inset_probe_times(
    ranges: Sequence[Tuple[float, float]], count: int = 6
) -> List[float]:
    """``count`` source times spread evenly over the kept ranges."""
    from .facecam_rect import probe_times

    return probe_times(ranges, count)


def _reactor_inset_measurable(
    source_path: Path,
    width: int,
    height: int,
    ranges: Sequence[Tuple[float, float]],
    face: Tuple[float, float, float, float],
) -> bool:
    from .reaction_layout import measure_reaction_inset

    times = _inset_probe_times(ranges)
    duration = max((end for _, end in ranges), default=0.0)
    return (
        measure_reaction_inset(source_path, width, height, duration, face, times=times)
        is not None
    )


def build_reframe_plan(
    analysis: Dict[str, Any],
    keep_ranges: Sequence[Tuple[float, float]],
    clip_duration: float,
    source_path: Optional[Path] = None,
) -> Optional[Dict[str, Any]]:
    """Per-shot reframe plan on the compacted timeline, or None to skip.

    Returns {"delegate": None|"plain", "shots": [...]} where each shot is
    {"start", "end", "mode", "reason", ...mode data...} with times on the
    compacted clip timeline. ``delegate == "plain"`` marks a
    reaction-dominant clip: the EDL graph cannot compose a reactor inset,
    so the clip keeps rendering through the plain path's layout stack
    instead of being centre-cropped by the EDL.

    With ``source_path`` (and REFRAME_REACTOR_NEEDS_INSET on), a
    reactor-like corner face only delegates when the inset rectangle is
    measurable, the same two-part test the plain path's reaction layout
    applies. A two-pane video call has a still corner face too, but no
    facecam rectangle; delegating it only bought a small-faces fit.
    """
    width = int(analysis.get("width") or 0)
    height = int(analysis.get("height") or 0)
    if width <= 0 or height <= 0 or clip_duration <= 0 or not keep_ranges:
        return None
    crop_w, crop_h = compute_vertical_crop_dims(width, height)
    if crop_w >= width:
        # No horizontal room to frame with (portrait/narrow source): the
        # legacy chain's behaviour is the honest answer for this geometry.
        logger.info("Reframe plan skipped: crop window spans the full source width")
        return None
    ranges = [(float(s), float(e)) for s, e in keep_ranges if e > s]
    offsets = output_offsets_for_ranges(ranges)
    frame_area = float(width * height)

    samples: List[Sample] = analysis.get("samples") or []
    context: List[SceneEvidence] = analysis.get("context") or []
    cuts: List[float] = analysis.get("cuts") or []

    # Reaction seam: a persistent reactor inset inside this clip's ranges
    # delegates the whole clip to the plain path (which composes the
    # reactor over the source) rather than centre-cropping it here. The
    # composed gate (Sanji x Phase 2): this seam routes on the corner
    # face plus, when a source path is available, a measurable inset
    # rectangle (REFRAME_REACTOR_NEEDS_INSET) — a still corner face with
    # no facecam rectangle (a two-pane video call) stays with the
    # regular planner. The plain path's choose_reaction_layout makes
    # the final call with pixel evidence (source faces, or sustained
    # change outside the inset) before any reaction layout is composed.
    clip_samples = [
        (t, boxes)
        for t, boxes in samples
        if any(start - 0.05 <= t <= end + 0.05 for start, end in ranges)
    ]
    if clip_samples:
        try:
            from .reaction_layout import find_reactor_corner

            reactor = find_reactor_corner(width, height, clip_samples)
            if reactor is not None and (
                source_path is None
                or not reactor_needs_inset()
                or _reactor_inset_measurable(source_path, width, height, ranges, reactor)
            ):
                logger.info(
                    "Reframe plan: persistent reactor inset detected; "
                    "delegating this clip to the plain layout path"
                )
                return {"delegate": "plain", "shots": []}
            if reactor is not None:
                logger.info(
                    "Reframe plan: still corner face but no measurable inset "
                    "rectangle; keeping the regular planner"
                )
        except Exception as exc:
            logger.debug("Reactor check unavailable: %s", exc)

    # A facecam the face-first check cannot see (small face after the
    # analysis downscale, a camera half-way down a side, gameplay with no
    # faces): find the camera window itself on the source, inside this
    # clip's ranges. The plain path's reaction layout composes it.
    if source_path is not None:
        from .gaming_templates import MANUAL_BOXES

        if MANUAL_BOXES.get():
            logger.info("Reframe plan: hand-drawn boxes; delegating to the plain layout path")
            return {"delegate": "plain", "shots": []}
        try:
            from .facecam_rect import facecam_rect_enabled, find_facecam_rect, probe_times

            if facecam_rect_enabled() and find_facecam_rect(
                source_path, width, height, probe_times(ranges)
            ) is not None:
                logger.info(
                    "Reframe plan: facecam window found; delegating this "
                    "clip to the plain layout path"
                )
                return {"delegate": "plain", "shots": []}
        except Exception as exc:
            logger.debug("Reactor check unavailable: %s", exc)

    # Map evidence to the compacted timeline for the layout planner.
    compact_context: List[SceneEvidence] = []
    for evidence in context:
        mapped = _map_source_time(evidence.time, ranges, offsets)
        if mapped is not None:
            compact_context.append(dataclasses.replace(evidence, time=mapped))
    compact_context.sort(key=lambda evidence: evidence.time)
    compact_cuts = set()
    for cut in cuts:
        mapped = _map_source_time(cut, ranges, offsets)
        if mapped is not None and 0 < mapped < clip_duration:
            compact_cuts.add(round(mapped, 3))
    for offset in offsets[1:]:
        if 0 < offset < clip_duration:
            compact_cuts.add(round(offset, 3))

    if compact_context:
        entries = build_context_layout_plan(
            compact_context, sorted(compact_cuts), clip_duration, crop_w / width
        )
    else:
        entries = [
            {
                "start": 0.0,
                "end": clip_duration,
                "kind": "fit",
                "reason": "analysis_unavailable",
                "soft": False,
            }
        ]

    entries = _expand_stable_context(
        entries, compact_context, sorted(compact_cuts), crop_w / width
    )
    entries = _split_entries_at_turnovers(entries, samples, ranges, offsets)

    # Prepare every entry's evidence up front: the clip-level layout
    # reconciliation in the fit branch needs to know, before ANY shot is
    # decided, whether this is a subject clip — one where a grounded face
    # track anchors at least one face-classified shot. In a subject clip
    # a context fit carrying the same subject may not flip the clip into
    # a second layout (see the fit branch and the post-loop record).
    prepared: List[Optional[Dict[str, Any]]] = []
    for entry in entries:
        start, end = float(entry["start"]), float(entry["end"])
        if end - start <= 0.01:
            prepared.append(None)
            continue
        pieces = _entry_source_pieces(start, end, ranges, offsets)
        entry_samples: List[Sample] = [
            (t, boxes)
            for t, boxes in samples
            if any(
                src_start - 1e-6 <= t < src_end + 1e-6
                for _cs, _ce, src_start, src_end, _off, _base in pieces
            )
        ]
        # Associate identities for EVERY entry, fit-classified ones too:
        # the fit verdict is re-decided below when its reason is one the
        # face tracks can answer better than the context planner could.
        entry_tracks = associate_tracks(entry_samples)
        entry_significant = _tracks_above(
            entry_tracks, len(entry_samples), frame_area,
            TRACK_MIN_PRESENCE_SHARE, TRACK_MIN_MEAN_AREA_FRAC,
        )
        entry_significant.sort(key=lambda track: track.score, reverse=True)
        entry_relaxed = _tracks_above(
            entry_tracks, len(entry_samples), frame_area,
            RESCUE_MIN_PRESENCE_SHARE, RESCUE_MIN_MEAN_AREA_FRAC,
        )
        entry_relaxed.sort(key=lambda track: track.score, reverse=True)
        prepared.append(
            {
                "entry": entry,
                "start": start,
                "end": end,
                "kind": entry.get("kind", "fit"),
                "reason": str(entry.get("reason") or ""),
                "pieces": pieces,
                "shot_samples": entry_samples,
                "tracks": entry_tracks,
                "significant": entry_significant,
                "relaxed": entry_relaxed,
                # The fit-blur guard's grounded faces for this shot: the
                # same significance tiers the rest of the planner uses,
                # strict first.
                "guard_tracks": entry_significant or entry_relaxed,
            }
        )
    subject_clip = any(
        prep is not None
        and prep["kind"] != "fit"
        and bool(prep["guard_tracks"])
        for prep in prepared
    )

    shots: List[Dict[str, Any]] = []
    guard_by_shot: Dict[int, List[Box]] = {}
    union_by_shot: Dict[int, List[Box]] = {}
    previous_crop_x: Optional[int] = None
    for prep in prepared:
        if prep is None:
            continue
        entry = prep["entry"]
        start, end = prep["start"], prep["end"]
        kind, reason = prep["kind"], prep["reason"]
        pieces = prep["pieces"]
        shot_samples = prep["shot_samples"]
        tracks = prep["tracks"]
        significant = prep["significant"]
        relaxed = prep["relaxed"]
        guard_tracks = prep["guard_tracks"]
        shot: Dict[str, Any] = {"start": start, "end": end}

        guard_boxes = [
            box
            for box in (track.median_box() for track in guard_tracks)
            if box is not None
        ]
        guard_by_shot[id(shot)] = guard_boxes
        # The caption/title face guard's view of the same tracks: union
        # envelopes, because text must clear the face's whole observed
        # range, not just its median position.
        union_by_shot[id(shot)] = [
            box
            for box in (track.union_box() for track in guard_tracks)
            if box is not None
        ]

        def _guard_blurs() -> bool:
            return bool(significant) and _tracks_blurred_by_fit(
                significant, width, height
            )

        rescued = False
        blur_guard = False
        reconciled = False
        if kind == "fit":
            if reason not in RESCUABLE_FIT_REASONS:
                # A deliberate full-frame decision (text, screen content,
                # distributed action, no analysis): keep it, with its
                # explicit canonical reason, and say so in the log —
                # unless the face tracks veto it. Two vetoes, in order:
                # (1) the fit-blur guard: the fit would put a grounded
                # face in a blurred pad band. The content-preservation
                # exemption (FIT_BLUR_EXEMPT_REASONS) covers only
                # FACELESS fits — when a grounded face track exists, the
                # face is the shot's subject and frame-edge text does
                # not outrank it (the TJR framing flip: tight crop,
                # then a 25 s blur-band fit, because trading boards at
                # the frame edge tripped screen_content_outside_crop).
                # (2) clip layout reconciliation: in a subject clip, a
                # fit carrying the subject may not flip the clip into a
                # second layout even when the face would stay sharp
                # inside the strip.
                fit_reason = FIT_REASON_ALIASES.get(reason, reason or "context_fit")
                blurred = bool(guard_tracks) and _tracks_blurred_by_fit(
                    guard_tracks, width, height
                )
                exempt = reason in FIT_BLUR_EXEMPT_REASONS and not guard_tracks
                if exempt or (
                    not blurred and not (subject_clip and guard_tracks)
                ):
                    logger.info(
                        "Reframe plan: shot %.2f-%.2fs stays full-frame fit (%s)",
                        start, end, fit_reason,
                    )
                    shot.update(mode="fit", reason=fit_reason)
                    shots.append(shot)
                    continue
                if blurred:
                    logger.info(
                        "Reframe plan: shot %.2f-%.2fs fit (%s) would blur a "
                        "grounded face in the pad bands; re-deciding from "
                        "the face tracks",
                        start, end, fit_reason,
                    )
                    blur_guard = True
                else:
                    logger.info(
                        "Reframe plan: shot %.2f-%.2fs fit (%s) carries "
                        "the clip's subject; reconciling it to the "
                        "face-track layout so the clip keeps one framing",
                        start, end, fit_reason,
                    )
                    reconciled = True
                significant = guard_tracks
            else:
                # Re-decide from the tracks. Relaxed thresholds apply here
                # only: a persistent lone subject (or pair) in a wide shot
                # is exactly who a subject-aware crop exists for.
                if not significant:
                    significant = relaxed
                if not significant:
                    logger.info(
                        "Reframe plan: shot %.2f-%.2fs fit (no_subject; "
                        "context reason was %s)",
                        start, end, reason or "unknown",
                    )
                    shot.update(mode="fit", reason="no_subject")
                    shots.append(shot)
                    continue
                rescued = True

        decided = False
        if len(significant) >= 2:
            if len(significant) >= 3:
                # A group: pair-crop only when EVERY significant face fits
                # the window (extremes decide); a wider group stays whole
                # rather than cropping the outer people out of existence —
                # unless the fit would blur a grounded face, in which case
                # the dominant subject takes the crop (fit-blur guard).
                by_x = sorted(
                    significant,
                    key=lambda track: (track.median_box() or (0, 0, 0, 0))[0],
                )
                left_track, right_track = by_x[0], by_x[-1]
                left_box_g = left_track.median_box()
                right_box_g = right_track.median_box()
                if left_box_g and right_box_g:
                    group_span = (right_box_g[0] + right_box_g[2]) - left_box_g[0]
                    if group_span > crop_w * PAIR_SPAN_MARGIN:
                        if _guard_blurs():
                            significant = significant[:1]
                            blur_guard = True
                        else:
                            logger.info(
                                "Reframe plan: shot %.2f-%.2fs fit (wide_group: "
                                "%d significant faces span %.0fpx > crop %dpx)",
                                start, end, len(significant), group_span, crop_w,
                            )
                            shot.update(mode="fit", reason="wide_group")
                            shots.append(shot)
                            decided = True
            else:
                left_track, right_track = sorted(
                    significant[:2],
                    key=lambda track: (track.median_box() or (0, 0, 0, 0))[0],
                )
            if not decided and len(significant) >= 2:
                left_box = left_track.median_box()
                right_box = right_track.median_box()
                if left_box and right_box:
                    span = (right_box[0] + right_box[2]) - left_box[0]
                    if span <= crop_w * PAIR_SPAN_MARGIN:
                        series = _pair_series(shot_samples, left_track, right_track)
                        shot.update(
                            mode="pair",
                            reason=(
                                FIT_BLUR_GUARD_REASON if blur_guard
                                else "pair_fits_crop"
                            ),
                            pieces=_piece_keys(
                                series, pieces, width, crop_w, frame_area,
                                all_samples=shot_samples,
                            ),
                            y=_crop_y((left_box, right_box), crop_h, height),
                        )
                        shots.append(shot)
                        previous_crop_x = _shot_static_x(shot)
                        decided = True
                    elif end - start >= SPLIT_MIN_SECONDS:
                        ordered = sorted(
                            (left_box, right_box), key=lambda b: b[0] + b[2] / 2
                        )
                        shot.update(
                            mode="split",
                            reason=(
                                FIT_BLUR_GUARD_REASON if blur_guard
                                else "interaction_exceeds_crop"
                            ),
                            split={
                                "left": _split_region(ordered[0], width, height),
                                "right": _split_region(ordered[1], width, height),
                            },
                            # Tile assignment is positional: index 0 is
                            # the left subject (top tile), 1 the right.
                            # Guard boxes are the tracks' union
                            # envelopes, ordered by the same median
                            # centres that ordered the tiles.
                            face_src=[
                                [float(v) for v in box]
                                for box in (
                                    track.union_box() or median
                                    for track, median in sorted(
                                        (
                                            (left_track, left_box),
                                            (right_track, right_box),
                                        ),
                                        key=lambda pair: (
                                            pair[1][0] + pair[1][2] / 2
                                        ),
                                    )
                                )
                            ],
                        )
                        shots.append(shot)
                        decided = True
                if not decided:
                    # Too short to split (or boxes unusable): keep the
                    # full frame rather than cutting one of the two
                    # people in half — unless the fit would blur a face,
                    # in which case the dominant subject takes the crop.
                    if _guard_blurs():
                        significant = significant[:1]
                        blur_guard = True
                    else:
                        shot.update(mode="fit", reason="interaction_exceeds_crop")
                        shots.append(shot)
                        decided = True

        if not decided and len(significant) == 1:
            subject_box = significant[0].median_box()
            oversized = subject_box is not None and subject_box[2] >= crop_w
            if oversized and not _guard_blurs():
                # The face itself is wider than the window: any crop would
                # bisect it, so the full frame is the honest layout.
                logger.info(
                    "Reframe plan: shot %.2f-%.2fs fit (subject_exceeds_crop: "
                    "face %.0fpx wide vs crop %dpx)",
                    start, end, subject_box[2], crop_w,
                )
                shot.update(mode="fit", reason="subject_exceeds_crop")
                shots.append(shot)
                decided = True
            else:
                if oversized:
                    # The fit would magnify this close-up into the pad
                    # bands blurred; cropping into the close-up is the
                    # standard 9:16 treatment and keeps the face sharp.
                    blur_guard = True
                # The series runs over ALL tracks: association + hysteresis is
                # what stops a background face that leans in for a moment from
                # stealing the frame the way the largest-face pick did.
                # Speaking evidence (flag-gated) may bias which present
                # face the series prefers; it never changes this branch's
                # decision to be here.
                asd_bias = _speaking_bias(analysis, shot_samples, tracks, pieces)
                series_boxes = subject_series(
                    shot_samples,
                    tracks,
                    sample_weights=asd_bias["weights"] if asd_bias else None,
                )
                series = [
                    (t, box) for (t, _boxes), box in zip(shot_samples, series_boxes)
                ]
                shot.update(
                    mode="single",
                    reason=(
                        FIT_BLUR_GUARD_REASON
                        if blur_guard
                        else "wide_single_subject"
                        if rescued and reason in ("no_reliable_close_subject", "stable_context")
                        else reason or "confident_subject"
                    ),
                    pieces=_piece_keys(
                        series, pieces, width, crop_w, frame_area,
                        all_samples=shot_samples,
                    ),
                    y=_crop_y(
                        (significant[0].median_box(),), crop_h, height
                    ),
                )
                if asd_bias:
                    stamp = _speaking_stamp(asd_bias, tracks, ranges, offsets)
                    if stamp:
                        shot["speaking"] = stamp
                shots.append(shot)
                previous_crop_x = _shot_static_x(shot)
                decided = True

        if not decided:
            # Fallback ladder for a face-classified shot with no usable track:
            # saliency-lite static crop, then the previous shot's crop, then
            # the (logged) centre crop.
            fallback = _fallback_crop(
                entry, compact_context, width, crop_w, previous_crop_x
            )
            fallback_x = _avoid_face_bisection(
                fallback[0],
                crop_w,
                [
                    box
                    for box in (track.median_box() for track in tracks)
                    if box is not None
                ],
                width,
            )
            shot.update(
                mode="static",
                reason=fallback[1],
                pieces=[
                    {
                        "start": piece_start,
                        "end": piece_end,
                        "keys": [(piece_start, fallback_x)],
                    }
                    for piece_start, piece_end, _s, _e, _off, _base in pieces
                ],
                y=_default_crop_y(crop_h, height),
            )
            if fallback[1] == "fallback_center":
                logger.warning(
                    "Reframe plan: shot %.2f-%.2fs has no subject evidence; "
                    "using the centre crop (fallback_center)",
                    start,
                    end,
                )
            shots.append(shot)
            previous_crop_x = fallback_x

        if reconciled and shot.get("mode") != "fit":
            # Entered as a context fit, re-decided to keep the clip's
            # one face layout; the stamp lets QC tell a reconciled crop
            # from a planned one. (A reconciled shot the track ladder
            # still decided as a fit — a group too wide to crop — keeps
            # its ladder reason and is recorded by the sweep below.)
            shot["layout_reconciled"] = True

    # Amputation audit over the DECIDED shots: measure the chosen
    # window against every prominent face the detector saw, repair the
    # avoidable cuts (re-anchor a single as a pair when the cut face
    # fits alongside the subject; convert to fit when the whole group
    # cannot fit one window and the fit would not blur anyone), and
    # stamp the rest. Also flags shots with no subject and near-zero
    # motion. Runs before the face-envelope and fit-window passes so a
    # shot converted to fit here is treated as a fit by both.
    decided_preps = [prep for prep in prepared if prep is not None]
    if len(decided_preps) == len(shots):
        for shot, prep in zip(shots, decided_preps):
            flag = _content_flag(
                prep["shot_samples"], compact_context,
                shot["start"], shot["end"],
            )
            if flag:
                shot["content_flag"] = flag
            if shot.get("mode") not in ("single", "pair", "static"):
                continue
            audit = _audit_crop_amputation(
                shot, prep["shot_samples"], prep["pieces"], prep["tracks"],
                crop_w, frame_area,
            )
            if audit is None or audit["samples_cut"] < AMPUTATION_MIN_SAMPLES_CUT:
                continue
            persistent = _persistent_cut_tracks(audit)
            tracks = prep["tracks"]
            subject_tracks = (prep["significant"] or prep["relaxed"])[
                : 1 if shot["mode"] == "single" else 2
            ]
            subject_ids = {id(track) for track in subject_tracks}
            others_cut = [
                tracks[ti]
                for ti in persistent
                if id(tracks[ti]) not in subject_ids
            ]
            repaired = False
            if others_cut and subject_tracks:
                involved = list(subject_tracks) + others_cut
                medians = [
                    box for box in (t.median_box() for t in involved) if box
                ]
                if medians:
                    span = (
                        max(b[0] + b[2] for b in medians)
                        - min(b[0] for b in medians)
                    )
                    if (
                        shot["mode"] == "single"
                        and len(others_cut) == 1
                        and span <= crop_w * PAIR_SPAN_MARGIN
                    ):
                        left_track, right_track = sorted(
                            (subject_tracks[0], others_cut[0]),
                            key=lambda tr: (tr.median_box() or (0, 0, 0, 0))[0],
                        )
                        shot["mode"] = "pair"
                        shot["reason"] = "pair_fits_crop"
                        shot["pieces"] = _piece_keys(
                            _pair_series(
                                prep["shot_samples"], left_track, right_track
                            ),
                            prep["pieces"], width, crop_w, frame_area,
                            all_samples=prep["shot_samples"],
                        )
                        shot["y"] = _crop_y(
                            (left_track.median_box(), right_track.median_box()),
                            crop_h, height,
                        )
                        shot["amputation_avoided"] = {
                            "how": "pair_anchor",
                            "tracks_cut": len(persistent),
                        }
                        repaired = True
                        logger.info(
                            "Reframe plan: shot %.2f-%.2fs crop persistently "
                            "cut a second face; re-anchored as a pair crop",
                            shot["start"], shot["end"],
                        )
                    elif span > crop_w * PAIR_SPAN_MARGIN and not (
                        _tracks_blurred_by_fit(involved, width, height)
                    ):
                        shot["mode"] = "fit"
                        shot["reason"] = "amputation_avoided_fit"
                        shot.pop("pieces", None)
                        shot.pop("y", None)
                        shot["amputation_avoided"] = {
                            "how": "fit",
                            "tracks_cut": len(persistent),
                        }
                        repaired = True
                        logger.info(
                            "Reframe plan: shot %.2f-%.2fs crop persistently "
                            "cut %d face(s) and the group spans %.0fpx > "
                            "crop %dpx; converted to fit (amputation_avoided_fit)",
                            shot["start"], shot["end"], len(persistent),
                            span, crop_w,
                        )
            if not repaired:
                shot["amputation"] = {
                    "samples_cut": audit["samples_cut"],
                    "samples": audit["samples"],
                    "tracks_cut": len(persistent),
                }
                logger.warning(
                    "Reframe plan: shot %.2f-%.2fs crop slices a prominent "
                    "face in %d/%d samples and no whole-subject layout "
                    "avoids it; stamped on the plan (amputation)",
                    shot["start"], shot["end"],
                    audit["samples_cut"], audit["samples"],
                )

    # Bright-subject audit over the same decided shots, in its own
    # loop so the face audit's early exits cannot skip it: a window
    # that persistently slices the non-face subject is repaired the
    # same way (a static window re-anchors on the subject; a face +
    # subject span no window can hold converts to fit when the fit
    # would not blur the faces) and stamped when it cannot.
    if len(decided_preps) == len(shots):
        for shot, prep in zip(shots, decided_preps):
            if shot.get("mode") not in ("single", "pair", "static"):
                continue
            saudit = _audit_subject_amputation(
                shot, compact_context, crop_w, width
            )
            if saudit is None or not _subject_persistent(saudit):
                continue
            envelope = _subject_envelope(
                compact_context, shot["start"], shot["end"], width, height
            )
            repaired = False
            if shot["mode"] == "static" and envelope:
                centre = envelope[0][0] + envelope[0][2] / 2.0
                new_x = clamp_even(
                    int(round(centre - crop_w / 2)), 0, max(0, width - crop_w)
                )
                new_x = _avoid_face_bisection(
                    new_x,
                    crop_w,
                    [
                        box
                        for box in (t.median_box() for t in prep["tracks"])
                        if box is not None
                    ],
                    width,
                )
                for piece in shot.get("pieces") or []:
                    piece["keys"] = [(piece["start"], new_x)]
                shot["subject_amputation_avoided"] = {"how": "subject_anchor"}
                repaired = True
                logger.info(
                    "Reframe plan: shot %.2f-%.2fs static crop persistently "
                    "cut the bright subject; re-anchored on it",
                    shot["start"], shot["end"],
                )
            elif shot["mode"] in ("single", "pair") and envelope:
                subject_tracks = (prep["significant"] or prep["relaxed"])[
                    : 1 if shot["mode"] == "single" else 2
                ]
                medians = [
                    box
                    for box in (t.median_box() for t in subject_tracks)
                    if box
                ]
                involved = medians + envelope
                span = (
                    max(b[0] + b[2] for b in involved)
                    - min(b[0] for b in involved)
                )
                if span > crop_w * PAIR_SPAN_MARGIN and not (
                    _tracks_blurred_by_fit(subject_tracks, width, height)
                ):
                    shot["mode"] = "fit"
                    shot["reason"] = "amputation_avoided_fit"
                    shot.pop("pieces", None)
                    shot.pop("y", None)
                    shot["subject_amputation_avoided"] = {"how": "fit"}
                    repaired = True
                    logger.info(
                        "Reframe plan: shot %.2f-%.2fs crop persistently "
                        "cut the bright subject and face + subject span "
                        "%.0fpx > crop %dpx; converted to fit "
                        "(amputation_avoided_fit)",
                        shot["start"], shot["end"], span, crop_w,
                    )
            if not repaired:
                shot["subject_amputation"] = {
                    "samples_cut": saudit["samples_cut"],
                    "samples": saudit["samples"],
                }
                logger.warning(
                    "Reframe plan: shot %.2f-%.2fs crop slices the bright "
                    "subject in %d/%d samples and no whole-subject layout "
                    "avoids it; stamped on the plan (subject_amputation)",
                    shot["start"], shot["end"],
                    saudit["samples_cut"], saudit["samples"],
                )

    # Every decided shot records its subjects' observed source-box
    # envelopes for the caption/title face guard; segment resolution
    # transforms them into output coordinates. Split shots already
    # stamped their own positionally-ordered pair at the decision site.
    for shot in shots:
        shot.setdefault(
            "face_src",
            [
                [float(v) for v in box]
                for box in union_by_shot.get(id(shot)) or []
            ],
        )
        shot.setdefault(
            "subject_src",
            _subject_envelope(
                compact_context, shot["start"], shot["end"], width, height
            ),
        )

    # Layout reconciliation record: in a subject clip, a fit that
    # survives next to face crops must say why in the plan — either it
    # has no face track to preserve (a genuine scene change), or the
    # face tracks themselves decided the fit (a group too wide to crop,
    # a subject wider than the window). A context fit carrying the
    # subject can never reach this loop unrecorded: the fit branch
    # re-decided it, so a mixed plan without a justification on every
    # surviving fit cannot be constructed.
    if subject_clip:
        for shot in shots:
            if shot["mode"] != "fit":
                continue
            shot["layout_kept"] = (
                "decided_from_face_tracks"
                if shot.get("face_src")
                else "no_face_track"
            )

    # Readable-fit windows for the fit shots (slight scale toward content,
    # never a crop), computed by the existing fit planner.
    try:
        from .reframing import plan_fit_windows

        fit_plan = [
            {
                "start": shot["start"],
                "end": shot["end"],
                "kind": "fit" if shot["mode"] == "fit" else "face",
                "reason": shot.get("reason", ""),
            }
            for shot in shots
        ]
        windows = plan_fit_windows(fit_plan, compact_context, width, height)
        for window_start, window_end, window_crop_w, window_x in windows:
            for shot in shots:
                if shot["mode"] != "fit":
                    continue
                if window_start >= shot["start"] - 0.01 and window_end <= shot["end"] + 0.01:
                    if window_crop_w is not None and any(
                        box[0] < window_x - 1
                        or box[0] + box[2] > window_x + window_crop_w + 1
                        for box in guard_by_shot.get(id(shot)) or []
                    ):
                        # The zoomed window would cut this grounded face
                        # out of the sharp layer entirely, leaving only
                        # its blurred backdrop copy: keep the full-frame
                        # fit for this shot instead (fit-blur guard).
                        logger.info(
                            "Reframe plan: fit window %.2f-%.2fs would "
                            "exclude a grounded face; keeping the "
                            "full-frame fit",
                            window_start, window_end,
                        )
                        continue
                    shot.setdefault("fit_windows", []).append(
                        [window_start, window_end, window_crop_w, window_x]
                    )
    except Exception as exc:
        logger.debug("Readable fit planning unavailable: %s", exc)

    return {
        "delegate": None,
        "shots": shots,
        "width": width,
        "height": height,
        "crop_w": crop_w,
        "crop_h": crop_h,
    }


def _pair_series(
    samples: Sequence[Sample], left: _Track, right: _Track
) -> List[Tuple[float, Optional[Box]]]:
    """Combined bounding box of the pair per sample (one may be missing)."""
    series: List[Tuple[float, Optional[Box]]] = []
    for index, (t, _boxes) in enumerate(samples):
        present = [
            track.observations[index]
            for track in (left, right)
            if index in track.observations
        ]
        if not present:
            series.append((t, None))
            continue
        x0 = min(b[0] for b in present)
        y0 = min(b[1] for b in present)
        x1 = max(b[0] + b[2] for b in present)
        y1 = max(b[1] + b[3] for b in present)
        series.append((t, (x0, y0, x1 - x0, y1 - y0)))
    return series


def _piece_keys(
    series: Sequence[Tuple[float, Optional[Box]]],
    pieces: Sequence[Tuple[float, float, float, float, float, float]],
    width: int,
    crop_w: int,
    frame_area: float,
    all_samples: Optional[Sequence[Sample]] = None,
) -> List[Dict[str, Any]]:
    """Trajectory keys per compacted piece of a shot.

    ``series`` is (source_time, box|None); each piece maps its samples to
    compacted time and runs the trajectory builder for that piece.
    ``all_samples`` (every face box per source time) feeds the trajectory's
    face-bisection guard.
    """
    out: List[Dict[str, Any]] = []
    for piece_start, piece_end, src_start, src_end, offset, base in pieces:
        piece_series = [
            (offset + (t - base), box)
            for t, box in series
            if src_start - 1e-6 <= t < src_end + 1e-6
        ]
        piece_guard = [
            (offset + (t - base), boxes)
            for t, boxes in (all_samples or [])
            if src_start - 1e-6 <= t < src_end + 1e-6
        ]
        keys = _trajectory_keys(
            piece_series, piece_start, piece_end, width, crop_w, frame_area,
            guard=piece_guard or None,
        )
        if not keys:
            continue
        out.append(
            {
                "start": piece_start,
                "end": piece_end,
                "keys": [[round(t, 3), int(x)] for t, x in keys],
            }
        )
    return out


def _crop_y(boxes: Sequence[Optional[Box]], crop_h: int, height: int) -> int:
    """Static crop y placing the subject's face centre at ~38% of the crop."""
    if crop_h >= height:
        return 0
    centers = [
        box[1] + box[3] / 2 for box in boxes if box is not None
    ]
    if not centers:
        return _default_crop_y(crop_h, height)
    centers.sort()
    cy = centers[len(centers) // 2]
    return clamp_even(int(round(cy - 0.38 * crop_h)), 0, max(0, height - crop_h))


def _default_crop_y(crop_h: int, height: int) -> int:
    if crop_h >= height:
        return 0
    return clamp_even(int(round((height - crop_h) * 0.35)), 0, max(0, height - crop_h))


def _shot_static_x(shot: Dict[str, Any]) -> Optional[int]:
    xs = [
        x
        for piece in shot.get("pieces") or []
        for _t, x in piece.get("keys") or []
    ]
    if not xs:
        return None
    xs.sort()
    return int(xs[len(xs) // 2])


# A sample's motion share at or above this counts as "the shot actually
# moves": its crop anchor is the motion centroid (where the movement is).
# Below it the frame is effectively still and the detail span — where the
# visible content is — is the better anchor.
MOTION_CENTER_MIN_MOTION = 0.004


def _fallback_crop(
    entry: Dict[str, Any],
    compact_context: Sequence[SceneEvidence],
    width: int,
    crop_w: int,
    previous_crop_x: Optional[int],
) -> Tuple[int, str]:
    """The fallback ladder's static crop for an evidence-less face shot.

    Order (first success wins): saliency-lite centroid of the shot's
    motion/edge evidence, the previous shot's crop, the centre crop.
    A sample with a measured bright subject anchors on the subject's
    own centre — a located subject beats both indirect anchors. A
    moving sample otherwise anchors on its motion centroid; a still
    sample on its detail span's midpoint (the span midpoint of a wide
    detailed scene sits between its subjects — the drone light-wall
    failure — while its motion, when anything moves, sits ON a
    subject). This ladder only runs when no face track was usable,
    so a face anchor always wins over the bright subject.
    """
    inside = [
        evidence
        for evidence in compact_context
        if entry["start"] - 1e-6 <= evidence.time < entry["end"] + 1e-6
    ]
    if inside:
        weight_total = 0.0
        weighted = 0.0
        for evidence in inside:
            weight = max(0.0, evidence.motion) + 0.002
            if evidence.subject_box is not None:
                centre = (
                    float(evidence.subject_box[0])
                    + float(evidence.subject_box[2])
                ) / 2.0
            elif evidence.motion >= MOTION_CENTER_MIN_MOTION:
                centre = evidence.motion_center
            else:
                centre = (evidence.detail_left + evidence.detail_right) / 2.0
            weighted += centre * weight
            weight_total += weight
        centroid = (weighted / weight_total) * width if weight_total > 0 else width / 2
        x = clamp_even(int(round(centroid - crop_w / 2)), 0, max(0, width - crop_w))
        return x, "saliency_centroid"
    if previous_crop_x is not None:
        return previous_crop_x, "previous_shot_crop"
    return (
        clamp_even((width - crop_w) // 2, 0, max(0, width - crop_w)),
        "fallback_center",
    )


# --- Amputation audit ---------------------------------------------------------
#
# The decision ladder checks spans and medians, but until now nothing
# audited the DECIDED window against the faces the detector actually
# saw, sample by sample. The MrBeast exports shipped exactly that gap:
# a subject half out of the frame at the crop edge, group members split
# across both edges. The audit measures it on every crop shot; the
# caller repairs what it can (pair re-anchor, fit conversion) and
# stamps what it cannot — an amputation is never silent again.

# A sliced face counts as a defect worth recording from this many
# samples up (one stray sample is trajectory noise at a shot boundary).
AMPUTATION_MIN_SAMPLES_CUT = 2
# A track sliced in at least this share of the samples it is present in
# is persistently cut — a framing failure, not a passer-by.
AMPUTATION_PERSISTENT_SHARE = 0.3


def _window_x_at(shot: Dict[str, Any], t: float) -> Optional[float]:
    """The shot's crop-window x at compacted time ``t`` (keys interpolated)."""
    keys = sorted(
        (float(kt), float(kx))
        for piece in shot.get("pieces") or []
        for kt, kx in piece.get("keys") or []
    )
    if not keys:
        return None
    if t <= keys[0][0]:
        return keys[0][1]
    if t >= keys[-1][0]:
        return keys[-1][1]
    for (t0, x0), (t1, x1) in zip(keys, keys[1:]):
        if t0 <= t <= t1:
            if t1 - t0 < 1e-9:
                return x1
            return x0 + (x1 - x0) * (t - t0) / (t1 - t0)
    return keys[-1][1]


def _box_sliced_by_window(box: Box, x: float, crop_w: int) -> bool:
    """A crop edge passes strictly through the box (same test the
    bisection guard uses; a box at least as wide as the window cannot
    be contained and is a layout decision, not an amputation)."""
    bx, bw = float(box[0]), float(box[2])
    if bw >= crop_w:
        return False
    return (bx < x < bx + bw) or (bx < x + crop_w < bx + bw)


def _audit_crop_amputation(
    shot: Dict[str, Any],
    shot_samples: Sequence[Sample],
    pieces: Sequence[Tuple[float, float, float, float, float, float]],
    tracks: Sequence[_Track],
    crop_w: int,
    frame_area: float,
) -> Optional[Dict[str, Any]]:
    """Measure prominent faces the shot's decided window slices.

    Returns {"samples", "samples_cut", "cut", "present"} where ``cut``
    and ``present`` map track index -> sample counts, or None when no
    sample could be evaluated (no keys / no mappable samples).
    Prominence is the planner's own subject bar
    (TRACK_MIN_MEAN_AREA_FRAC): specks and background faces do not
    count, the people the shot is about do.
    """
    min_area = TRACK_MIN_MEAN_AREA_FRAC * frame_area
    evaluated = 0
    samples_cut = 0
    cut: Dict[int, int] = {}
    present: Dict[int, int] = {}
    for index, (t, boxes) in enumerate(shot_samples):
        compacted: Optional[float] = None
        for _ps, _pe, src_start, src_end, offset, base in pieces:
            if src_start - 1e-6 <= t < src_end + 1e-6:
                compacted = offset + (t - base)
                break
        if compacted is None:
            continue
        x = _window_x_at(shot, compacted)
        if x is None:
            continue
        evaluated += 1
        sliced_here = False
        for box in boxes:
            if box[2] * box[3] < min_area:
                continue
            owner: Optional[int] = None
            for track_index, track in enumerate(tracks):
                observed = track.observations.get(index)
                if observed is not None and _iou(box, observed) >= 0.5:
                    owner = track_index
                    break
            if owner is not None:
                present[owner] = present.get(owner, 0) + 1
            if _box_sliced_by_window(box, x, crop_w):
                sliced_here = True
                if owner is not None:
                    cut[owner] = cut.get(owner, 0) + 1
        if sliced_here:
            samples_cut += 1
    if not evaluated:
        return None
    return {
        "samples": evaluated,
        "samples_cut": samples_cut,
        "cut": cut,
        "present": present,
    }


def _persistent_cut_tracks(audit: Dict[str, Any]) -> List[int]:
    """Track indices the audit found persistently sliced."""
    return [
        track_index
        for track_index, count in (audit.get("cut") or {}).items()
        if count >= AMPUTATION_MIN_SAMPLES_CUT
        and count / max(1, (audit.get("present") or {}).get(track_index, count))
        >= AMPUTATION_PERSISTENT_SHARE
    ]


# --- Bright-subject (non-face) extent -----------------------------------------
#
# SceneEvidence.subject_box is the one signal that sees subjects no
# face detector can (the drone light-portrait). It feeds the same
# three consumers face boxes feed: the caption/title guard envelopes
# (as ``subject_src``, alongside ``face_src``), the fallback anchor
# (in ``_fallback_crop`` above), and this audit — a decided window
# that persistently slices the bright subject is repaired or stamped
# exactly like a sliced face, under subject-named stamps so QC can
# tell the two apart.


def _subject_envelope(
    compact_context: Sequence[SceneEvidence],
    start: float,
    end: float,
    width: int,
    height: int,
) -> List[List[float]]:
    """Union of the shot's bright-subject boxes, as one [x, y, w, h]
    source-pixel box (the ``face_src`` shape), or [] when no sample in
    the shot carries a subject."""
    boxes = [
        evidence.subject_box
        for evidence in compact_context
        if start - 1e-6 <= evidence.time < end + 1e-6
        and evidence.subject_box is not None
    ]
    if not boxes:
        return []
    x0 = min(float(box[0]) for box in boxes) * width
    y0 = min(float(box[1]) for box in boxes) * height
    x1 = max(float(box[2]) for box in boxes) * width
    y1 = max(float(box[3]) for box in boxes) * height
    return [[x0, y0, x1 - x0, y1 - y0]]


def _audit_subject_amputation(
    shot: Dict[str, Any],
    compact_context: Sequence[SceneEvidence],
    crop_w: int,
    width: int,
) -> Optional[Dict[str, Any]]:
    """Measure samples whose bright subject the shot's window slices.

    Returns {"samples", "samples_cut"} over the samples that carry a
    subject box, or None when no sample could be evaluated (no
    subject in the shot / no window keys). The slicing test is the
    face audit's own: an edge strictly through the box.
    """
    present = 0
    samples_cut = 0
    for evidence in compact_context:
        if evidence.subject_box is None:
            continue
        if not (shot["start"] - 1e-6 <= evidence.time < shot["end"] + 1e-6):
            continue
        x = _window_x_at(shot, evidence.time)
        if x is None:
            continue
        present += 1
        box = evidence.subject_box
        if _box_sliced_by_window(
            (float(box[0]) * width, 0.0, (float(box[2]) - float(box[0])) * width, 0.0),
            x,
            crop_w,
        ):
            samples_cut += 1
    if not present:
        return None
    return {"samples": present, "samples_cut": samples_cut}


def _subject_persistent(audit: Dict[str, Any]) -> bool:
    """The face audit's persistence bar, applied to the subject."""
    return (
        audit["samples_cut"] >= AMPUTATION_MIN_SAMPLES_CUT
        and audit["samples_cut"] / max(1, audit["samples"])
        >= AMPUTATION_PERSISTENT_SHARE
    )


# A shot with no detected face in any sample and mean motion below this
# is flagged, not silently kept: near-empty seconds (bare ground, near-
# black frames) reached exports because nothing ever said they were
# empty. Flagging only — whether such a shot is cut is a separate
# editorial decision this planner does not make.
EMPTY_SHOT_MOTION_MAX = 0.002


def _content_flag(
    shot_samples: Sequence[Sample],
    compact_context: Sequence[SceneEvidence],
    start: float,
    end: float,
) -> Optional[str]:
    """'no_subject_low_motion' when the shot shows nothing and nothing moves."""
    if any(boxes for _t, boxes in shot_samples):
        return None
    inside = [
        evidence
        for evidence in compact_context
        if start - 1e-6 <= evidence.time < end + 1e-6
    ]
    if not inside:
        return None
    mean_motion = sum(evidence.motion for evidence in inside) / len(inside)
    if mean_motion < EMPTY_SHOT_MOTION_MAX:
        return "no_subject_low_motion"
    return None


# --- Segment resolution ---------------------------------------------------------

def _restrict_keys(
    keys: Sequence[Tuple[float, float]], start: float, end: float
) -> List[Tuple[float, float]]:
    """Trajectory keys covering [start, end], interpolated at the edges."""
    if not keys:
        return []
    ordered = sorted(keys)

    def interp(t: float) -> float:
        if t <= ordered[0][0]:
            return ordered[0][1]
        if t >= ordered[-1][0]:
            return ordered[-1][1]
        for (t0, x0), (t1, x1) in zip(ordered, ordered[1:]):
            if t0 <= t <= t1:
                if t1 - t0 < 1e-9:
                    return x1
                return x0 + (x1 - x0) * (t - t0) / (t1 - t0)
        return ordered[-1][1]

    out = [(start, interp(start))]
    out.extend((t, x) for t, x in ordered if start < t < end)
    out.append((end, interp(end)))
    deduped: List[Tuple[float, float]] = []
    for t, x in out:
        if deduped and abs(t - deduped[-1][0]) < 1e-6:
            deduped[-1] = (t, x)
        else:
            deduped.append((t, x))
    return deduped


def _piece_expression(
    keys: Sequence[Tuple[float, float]], seg_start: float
) -> str:
    """ffmpeg crop-x expression for one piece, in segment-local time."""
    local = [(t - seg_start, x) for t, x in keys]
    if not local:
        return "0"
    xs = {x for _t, x in local}
    if len(xs) == 1:
        return str(int(local[0][1]))
    return build_smooth_pan_expression([(t, int(x)) for t, x in local])


# --- Output-space face boxes for the caption/title face guard -----------------
#
# The caption builder's face guard (media.face_text_guard) works in OUTPUT
# pixels, so each decided shot records its subjects' observed source-box
# envelopes (``shot["face_src"]``) and segment resolution transforms them through
# the very geometry the renderer will use: the crop window's swept x
# range at the shot's static y for crop modes, the tile rects for split
# shots, the letterbox transform for full-frame fits. The result is
# stamped on the segment as ``reframe["face_boxes"]``: a list of
# {"start", "end", "boxes"} entries in segment-local time with boxes as
# [x, y, w, h] in the 1080x1920 output frame, clipped to the frame.

OUT_W = 1080
OUT_H = 1920


def _clip_output_box(
    x0: float, y0: float, x1: float, y1: float
) -> Optional[List[int]]:
    cx0 = max(0.0, min(float(OUT_W), x0))
    cy0 = max(0.0, min(float(OUT_H), y0))
    cx1 = max(0.0, min(float(OUT_W), x1))
    cy1 = max(0.0, min(float(OUT_H), y1))
    if cx1 - cx0 < 1 or cy1 - cy0 < 1:
        return None
    return [
        int(round(cx0)),
        int(round(cy0)),
        int(round(cx1 - cx0)),
        int(round(cy1 - cy0)),
    ]


def _crop_output_boxes(
    face_src: Sequence[Sequence[float]],
    win_x_lo: float,
    win_x_hi: float,
    win_y: float,
    crop_w: int,
    crop_h: int,
) -> List[List[int]]:
    """Face boxes through a crop window whose x sweeps [win_x_lo, win_x_hi]."""
    if crop_w <= 0 or crop_h <= 0:
        return []
    scale_x = OUT_W / crop_w
    scale_y = OUT_H / crop_h
    boxes: List[List[int]] = []
    for box in face_src:
        clipped = _clip_output_box(
            (box[0] - win_x_hi) * scale_x,
            (box[1] - win_y) * scale_y,
            (box[0] + box[2] - win_x_lo) * scale_x,
            (box[1] + box[3] - win_y) * scale_y,
        )
        if clipped is not None:
            boxes.append(clipped)
    return boxes


def _tile_output_box(
    box: Sequence[float], tile: Dict[str, Any], pane_top: int
) -> Optional[List[int]]:
    tile_w = int(tile.get("tile_w") or 0)
    tile_h = int(tile.get("tile_h") or 0)
    if tile_w <= 0 or tile_h <= 0:
        return None
    scale_x = OUT_W / tile_w
    scale_y = (OUT_H // 2) / tile_h
    return _clip_output_box(
        (box[0] - int(tile.get("tile_x") or 0)) * scale_x,
        pane_top + (box[1] - int(tile.get("tile_y") or 0)) * scale_y,
        (box[0] + box[2] - int(tile.get("tile_x") or 0)) * scale_x,
        pane_top + (box[1] + box[3] - int(tile.get("tile_y") or 0)) * scale_y,
    )


def _fit_output_boxes(
    face_src: Sequence[Sequence[float]], width: int, height: int
) -> List[List[int]]:
    """Face boxes through the plain letterbox fit (FIT_CHAIN geometry)."""
    if width <= 0 or height <= 0:
        return []
    scale = min(OUT_W / width, OUT_H / height)
    out_w = int(width * scale) // 2 * 2
    out_h = int(height * scale) // 2 * 2
    off_x = (OUT_W - out_w) / 2
    off_y = (OUT_H - out_h) / 2
    boxes: List[List[int]] = []
    for box in face_src:
        clipped = _clip_output_box(
            off_x + box[0] * scale,
            off_y + box[1] * scale,
            off_x + (box[0] + box[2]) * scale,
            off_y + (box[1] + box[3]) * scale,
        )
        if clipped is not None:
            boxes.append(clipped)
    return boxes


def _fit_strip_rect(
    width: int, height: int, window_crop_w: Optional[int] = None
) -> Optional[Tuple[int, int]]:
    """Output-space (top, bottom) of a fit shot's sharp foreground strip.

    A fit renders the source sharp only inside this centred rect;
    everything above and below it on the 1080x1920 canvas is the blurred
    backdrop. Plain fits decrease-fit the whole source (FIT_CHAIN
    geometry, the same math as ``_fit_output_boxes``); a windowed fit
    crops a horizontal window of the source and scales it to the full
    output width, so its strip is taller. Text placed outside the strip
    sits on blur — the TJR fit's captions landed at ~74% height, below
    the strip's ~66% bottom edge — so segment resolution stamps the
    strip for the caption builder to constrain placement with.
    """
    if width <= 0 or height <= 0:
        return None
    if window_crop_w:
        strip_h = int(OUT_W * height / window_crop_w) // 2 * 2
    else:
        scale = min(OUT_W / width, OUT_H / height)
        strip_h = int(height * scale) // 2 * 2
    if strip_h <= 0:
        return None
    top = (OUT_H - strip_h) // 2
    return top, top + strip_h


def collect_face_zones(
    edl: Optional[Dict[str, Any]],
) -> List[Tuple[float, float, List[Tuple[float, float, float, float]]]]:
    """Clip-time face zones from an EDL's resolved segment reframe data.

    Returns [(start, end, boxes)] on the clip timeline, boxes as
    (x, y, w, h) tuples in output pixels — the input the caption
    builder's face guard consumes. Segments without resolved face data
    contribute nothing.
    """
    zones: List[Tuple[float, float, List[Tuple[float, float, float, float]]]] = []
    for segment in (edl or {}).get("segments") or []:
        if not isinstance(segment, dict):
            continue
        reframe = segment.get("reframe")
        if not isinstance(reframe, dict):
            continue
        seg_start = float(segment.get("start") or 0)
        for entry in reframe.get("face_boxes") or []:
            if not isinstance(entry, dict):
                continue
            boxes = []
            for box in entry.get("boxes") or []:
                try:
                    boxes.append(
                        (float(box[0]), float(box[1]), float(box[2]), float(box[3]))
                    )
                except (TypeError, ValueError, IndexError):
                    continue
            if not boxes:
                continue
            zones.append(
                (
                    seg_start + float(entry.get("start") or 0),
                    seg_start + float(entry.get("end") or 0),
                    boxes,
                )
            )
    zones.sort(key=lambda zone: zone[0])
    return zones


def collect_fit_strips(
    edl: Optional[Dict[str, Any]],
) -> List[Tuple[float, float, float, float]]:
    """Clip-time sharp-strip rects of an EDL's fit shots.

    Returns [(start, end, top, bottom)] on the clip timeline, top and
    bottom in output pixels — the input the caption builder uses to
    keep captions inside a fit's sharp strip and out of its blurred
    pad bands. Segments without resolved fit data contribute nothing.
    """
    strips: List[Tuple[float, float, float, float]] = []
    for segment in (edl or {}).get("segments") or []:
        if not isinstance(segment, dict):
            continue
        reframe = segment.get("reframe")
        if not isinstance(reframe, dict):
            continue
        seg_start = float(segment.get("start") or 0)
        for entry in reframe.get("fit_strips") or []:
            if not isinstance(entry, dict):
                continue
            try:
                start = seg_start + float(entry.get("start") or 0)
                end = seg_start + float(entry.get("end") or 0)
                top = float(entry["top"])
                bottom = float(entry["bottom"])
            except (TypeError, ValueError, KeyError):
                continue
            if end > start and bottom > top:
                strips.append((start, end, top, bottom))
    strips.sort(key=lambda strip: strip[0])
    return strips


def _resolve_segment(
    segment: Dict[str, Any],
    shots: Sequence[Dict[str, Any]],
    crop_w: int,
    crop_h: int,
    width: int,
    height: int,
) -> None:
    """Attach the segment-local reframe data the EDL renderer consumes."""
    seg_start = float(segment.get("start", 0))
    seg_end = float(segment.get("end", seg_start))
    if seg_end <= seg_start:
        return
    overlapping = [
        shot
        for shot in shots
        if shot["end"] > seg_start + 0.001 and shot["start"] < seg_end - 0.001
    ]
    if not overlapping:
        return

    crop_modes = {"single", "pair", "static"}
    crop_pieces: List[Tuple[float, float, str, int]] = []  # (start, end, expr, y)
    fit_intervals: List[Tuple[float, float]] = []
    fit_windows: List[Tuple[float, float, Optional[int], int]] = []
    split_layers: Dict[Tuple, Dict[str, Any]] = {}
    face_entries: List[Dict[str, Any]] = []
    strip_entries: List[Dict[str, Any]] = []

    def add_face_entry(boxes: List[List[int]]) -> None:
        if boxes:
            face_entries.append(
                {
                    "start": round(local_start, 3),
                    "end": round(local_end, 3),
                    "boxes": boxes,
                }
            )

    for shot in overlapping:
        local_start = max(seg_start, shot["start"]) - seg_start
        local_end = min(seg_end, shot["end"]) - seg_start
        if local_end - local_start <= 0.001:
            continue
        mode = shot["mode"]
        face_src = shot.get("face_src") or []
        subject_src = shot.get("subject_src") or []
        # The bright subject joins the faces in the caption/title
        # guard's protect set: same envelopes, same transforms.
        guard_src = face_src + subject_src
        if mode in crop_modes:
            keys: List[Tuple[float, float]] = []
            for piece in shot.get("pieces") or []:
                piece_keys = [(float(t), float(x)) for t, x in piece.get("keys") or []]
                keys.extend(
                    _restrict_keys(
                        piece_keys,
                        max(seg_start, shot["start"], piece["start"]),
                        min(seg_end, shot["end"], piece["end"]),
                    )
                )
            if not keys:
                # The shot planned a subject but no trajectory survived
                # resolution; the last resort centres on the shot's own
                # faces when it has any (a dead-centre crop beside a
                # known face is the failure this fallback used to ship),
                # else on its bright subject, and is logged either way.
                centre_x = (width - crop_w) // 2
                anchor = "centre"
                anchor_src = face_src or subject_src
                if anchor_src:
                    centres = sorted(
                        float(box[0]) + float(box[2]) / 2.0 for box in anchor_src
                    )
                    centre_x = clamp_even(
                        int(round(centres[len(centres) // 2] - crop_w / 2)),
                        0,
                        max(0, width - crop_w),
                    )
                    anchor = (
                        "the shot's median face"
                        if face_src
                        else "the shot's bright subject"
                    )
                logger.warning(
                    "Reframe plan: shot %.2f-%.2fs produced no crop keys; "
                    "using the %s crop (fallback_center)",
                    shot["start"],
                    shot["end"],
                    anchor,
                )
                keys = [(max(seg_start, shot["start"]), centre_x)]
            restricted = _restrict_keys(
                keys, max(seg_start, shot["start"]), min(seg_end, shot["end"])
            )
            expr = _piece_expression(restricted, seg_start)
            crop_pieces.append(
                (
                    max(seg_start, shot["start"]) - seg_start,
                    min(seg_end, shot["end"]) - seg_start,
                    expr,
                    int(shot.get("y", 0)),
                )
            )
            if guard_src and restricted:
                xs = [x for _t, x in restricted]
                add_face_entry(
                    _crop_output_boxes(
                        guard_src,
                        min(xs),
                        max(xs),
                        int(shot.get("y", 0)),
                        crop_w,
                        crop_h,
                    )
                )
        elif mode == "fit":
            fit_intervals.append((local_start, local_end))
            shot_windows = shot.get("fit_windows") or []
            for win_start, win_end, win_crop_w, win_x in shot_windows:
                clipped_start = max(seg_start, float(win_start))
                clipped_end = min(seg_end, float(win_end))
                if clipped_end - clipped_start > 0.01:
                    fit_windows.append(
                        (
                            clipped_start - seg_start,
                            clipped_end - seg_start,
                            win_crop_w,
                            int(win_x),
                        )
                    )
                    strip = _fit_strip_rect(width, height, win_crop_w)
                    if strip is not None:
                        strip_entries.append(
                            {
                                "start": round(clipped_start - seg_start, 3),
                                "end": round(clipped_end - seg_start, 3),
                                "top": strip[0],
                                "bottom": strip[1],
                            }
                        )
            if not shot_windows:
                # The plain letterbox strip — the only sharp band of an
                # unwindowed fit (windowed fits stamp theirs above).
                strip = _fit_strip_rect(width, height)
                if strip is not None:
                    strip_entries.append(
                        {
                            "start": round(local_start, 3),
                            "end": round(local_end, 3),
                            "top": strip[0],
                            "bottom": strip[1],
                        }
                    )
            # Windowed fits crop into the frame, so the plain letterbox
            # transform no longer describes where a face lands; only the
            # unwindowed fit gets guard boxes.
            if guard_src and not shot_windows:
                add_face_entry(_fit_output_boxes(guard_src, width, height))
        elif mode == "split":
            split = shot["split"]
            # Source-border trims (media.border_trim): inset the tile
            # windows so the source's own frame furniture falls
            # outside them. No stamp -> the tiles pass through
            # untouched and resolution is exactly as before.
            trims = shot.get("border_trim") or {}
            left_tile = _inset_tile(split["left"], trims.get("left"))
            right_tile = _inset_tile(split["right"], trims.get("right"))
            key = (
                tuple(sorted(left_tile.items())),
                tuple(sorted(right_tile.items())),
            )
            layer = split_layers.setdefault(
                key,
                {"left": left_tile, "right": right_tile, "intervals": []},
            )
            layer["intervals"].append([round(local_start, 3), round(local_end, 3)])
            applied: Dict[str, Any] = {}
            if left_tile is not split["left"] and trims.get("left"):
                applied["left"] = trims["left"]
            if right_tile is not split["right"] and trims.get("right"):
                applied["right"] = trims["right"]
            if applied and "border_trim" not in layer:
                layer["border_trim"] = applied
            if len(face_src) >= 2:
                add_face_entry(
                    [
                        box
                        for box in (
                            _tile_output_box(face_src[0], left_tile, 0),
                            _tile_output_box(
                                face_src[1], right_tile, OUT_H // 2
                            ),
                        )
                        if box is not None
                    ]
                )
            if subject_src:
                # The tiles are positional for the two faces; the
                # bright subject belongs to neither position, but it
                # is visible in exactly one tile — the one whose
                # source span contains its centre.
                sbox = subject_src[0]
                centre_x = float(sbox[0]) + float(sbox[2]) / 2.0
                for tile, pane_top in (
                    (left_tile, 0),
                    (right_tile, OUT_H // 2),
                ):
                    tile_x = int(tile.get("tile_x") or 0)
                    tile_w = int(tile.get("tile_w") or 0)
                    if tile_w > 0 and tile_x <= centre_x < tile_x + tile_w:
                        mapped = _tile_output_box(sbox, tile, pane_top)
                        if mapped is not None:
                            add_face_entry([mapped])
                        break

    if not crop_pieces and not fit_intervals and not split_layers:
        return
    resolved: Dict[str, Any] = {}
    if crop_pieces:
        # Compose the per-piece expressions into one segment expression:
        # each piece's expression is selected until its end time.
        crop_pieces.sort(key=lambda piece: piece[0])
        expr = crop_pieces[-1][2]
        for _start, end, sub_expr, _y in reversed(crop_pieces[:-1]):
            expr = f"if(lt(t\\,{end:.3f})\\,{sub_expr}\\,{expr})"
        longest = max(crop_pieces, key=lambda piece: piece[1] - piece[0])
        resolved["crop"] = {
            "w": crop_w,
            "h": crop_h,
            "x_expr": expr,
            "y": longest[3],
            "intervals": [
                [round(start, 3), round(end, 3)] for start, end, _e, _y in crop_pieces
            ],
        }
    if fit_intervals:
        layers = build_fit_layers(fit_intervals, fit_windows or None)
        resolved["fit_layers"] = [
            {
                "chain": chain,
                "intervals": [[round(a, 3), round(b, 3)] for a, b in intervals],
            }
            for chain, intervals in layers
        ]
    if split_layers:
        resolved["split_layers"] = list(split_layers.values())
    if face_entries:
        resolved["face_boxes"] = face_entries
    if strip_entries:
        resolved["fit_strips"] = strip_entries
    segment["reframe"] = resolved


def _inset_tile(
    tile: Dict[str, Any], trim: Optional[Dict[str, int]]
) -> Dict[str, Any]:
    """A split tile inset by a detected source-border trim.

    Returns the original dict untouched when there is nothing to trim
    (callers identity-check this), and also when the inset would leave
    a degenerate tile — a trim must never cost the shot its subject.
    """
    if not trim:
        return tile
    left = int(trim.get("left") or 0)
    right = int(trim.get("right") or 0)
    top = int(trim.get("top") or 0)
    bottom = int(trim.get("bottom") or 0)
    if not (left or right or top or bottom):
        return tile
    from .ffmpeg import round_to_even

    tile_w = round_to_even(int(tile["tile_w"]) - left - right)
    tile_h = round_to_even(int(tile["tile_h"]) - top - bottom)
    if tile_w < 96 or tile_h < 96:
        return tile
    return {
        "tile_x": int(tile["tile_x"]) + left,
        "tile_y": int(tile["tile_y"]) + top,
        "tile_w": tile_w,
        "tile_h": tile_h,
    }


def _stamp_border_trims(
    plan: Dict[str, Any],
    source_path: Path,
    ranges: Sequence[Tuple[float, float]],
) -> None:
    """Detect source-border furniture per split shot and stamp trims.

    For every split shot, frames are sampled across the shot's source
    span and each tile window is checked with
    :mod:`media.border_trim`; a shot with furniture gets
    ``shot["border_trim"] = {"left": {...}, "right": {...}}`` (tile
    key, trim dict per tile), which segment resolution consumes like
    it consumes ``face_src``. Furniture-free shots keep no stamp, so
    their plans resolve exactly as before. Never raises: detection
    failure simply means no trims.
    """
    try:
        from .border_trim import (
            border_trim_enabled,
            detect_window_trim,
            grab_window_frames,
        )

        if not border_trim_enabled():
            return
        offsets = output_offsets_for_ranges(ranges)
        for shot in plan.get("shots") or []:
            if shot.get("mode") != "split":
                continue
            try:
                split = shot.get("split") or {}
                face_src = shot.get("face_src") or []
                pieces = _entry_source_pieces(
                    float(shot["start"]), float(shot["end"]), ranges, offsets
                )
                times: List[float] = []
                for _cs, _ce, src_start, src_end, _off, _base in pieces:
                    span = src_end - src_start
                    for fraction in (0.25, 0.5, 0.75):
                        times.append(src_start + span * fraction)
                if not times:
                    continue
                trims: Dict[str, Dict[str, int]] = {}
                for index, side in enumerate(("left", "right")):
                    tile = split.get(side) or {}
                    if not tile:
                        continue
                    frames = grab_window_frames(source_path, times, tile)
                    face = None
                    if len(face_src) > index:
                        box = face_src[index]
                        face = (
                            float(box[0]) - int(tile.get("tile_x") or 0),
                            float(box[1]) - int(tile.get("tile_y") or 0),
                            float(box[2]),
                            float(box[3]),
                        )
                    trim = detect_window_trim(frames, face)
                    if any(trim.values()):
                        trims[side] = trim
                if trims:
                    shot["border_trim"] = trims
                    logger.info(
                        "Reframe plan: shot %.2f-%.2fs source-border "
                        "trims %s",
                        shot["start"],
                        shot["end"],
                        trims,
                    )
            except Exception:
                logger.warning(
                    "Border trim detection failed for shot %.2f-%.2fs; "
                    "planning without trims",
                    float(shot.get("start") or 0),
                    float(shot.get("end") or 0),
                    exc_info=True,
                )
    except Exception:
        logger.warning(
            "Border trim detection unavailable; planning without trims",
            exc_info=True,
        )


# --- Plain — just cuts planning --------------------------------------------------
#
# The Plain template's planner. Same evidence, same shot segmentation as
# the auto planner above, one different contract: every shot resolves to
# a full-bleed 9:16 crop — never the blurred-pad fit, never a split
# stack, and no reactor delegation (the plain path IS the render path
# here; a reactor face is just one more tracked subject to frame).
#
# Tightness: the auto planner's window is always the full-height
# standard crop (606 px wide on a 1080p source), which is exactly the
# wide, headroom-heavy framing Luffy rejected. The plain window is sized
# per shot from the subject itself: the dominant face should fill about
# PLAIN_FACE_WIDTH_SHARE of the window's width (head-and-shoulders),
# and a group region should fill about PLAIN_GROUP_WIDTH_SHARE. The
# window never goes below PLAIN_MIN_WINDOW_WIDTH source pixels where
# the standard window allows it — past that the 1080-wide output would
# upscale more than ~3.5x and trade the tight framing for mush — and
# never exceeds the standard window.

PLAIN_FACE_WIDTH_SHARE = 0.40
PLAIN_GROUP_WIDTH_SHARE = 0.85
PLAIN_MIN_WINDOW_WIDTH = 310


def _plain_window_width(subject_width: float, share: float, std_w: int) -> int:
    """9:16 window width that makes the subject fill ``share`` of it."""
    floor_w = min(std_w, PLAIN_MIN_WINDOW_WIDTH)
    if subject_width <= 0:
        return std_w
    wanted = round_to_even(int(round(subject_width / share)))
    return max(floor_w, min(std_w, wanted))


def _plain_window_dims(window_w: int, width: int, height: int) -> Tuple[int, int]:
    """(crop_w, crop_h) for a plain window of the given width."""
    crop_w = max(2, min(round_to_even(window_w), round_to_even(width)))
    crop_h = round_to_even(int(round(crop_w * 16 / 9)))
    if crop_h > height:
        crop_h = round_to_even(height)
        crop_w = max(2, min(round_to_even(int(round(crop_h * 9 / 16))), crop_w))
    return crop_w, crop_h


def _union_series(
    samples: Sequence[Sample], tracks: Sequence[_Track]
) -> List[Tuple[float, Optional[Box]]]:
    """Combined bounding box of all given tracks per sample."""
    series: List[Tuple[float, Optional[Box]]] = []
    for index, (t, _boxes) in enumerate(samples):
        present = [
            track.observations[index]
            for track in tracks
            if index in track.observations
        ]
        if not present:
            series.append((t, None))
            continue
        x0 = min(b[0] for b in present)
        y0 = min(b[1] for b in present)
        x1 = max(b[0] + b[2] for b in present)
        y1 = max(b[1] + b[3] for b in present)
        series.append((t, (x0, y0, x1 - x0, y1 - y0)))
    return series


def build_plain_reframe_plan(
    analysis: Dict[str, Any],
    keep_ranges: Sequence[Tuple[float, float]],
    clip_duration: float,
    framing: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    """Always-full-frame reframe plan for a Plain — just cuts task.

    Mirrors :func:`build_reframe_plan`'s front half (same compacted
    context entries, same turnover splits, so shots line up with the
    auto planner's), then decides every shot as mode ``"plain"`` with
    its own tight crop window: a group region when the significant
    faces' union fits the standard window, the dominant subject
    otherwise, and the saliency/static fallback ladder at standard
    dims when no face is usable. Returns None for the same geometry
    the auto planner skips (portrait/narrow sources, where the legacy
    chain is already full-bleed).

    ``framing="crop"`` builds the identical geometry for a task whose
    framing_mode asks for always-crop WITHOUT the Plain template: the
    plan is stamped ``"framing": "crop"`` instead of ``"plain": True``
    so no downstream consumer can mistake it for a Plain task — only
    the crop geometry is shared, none of Plain's other restrictions.
    """
    width = int(analysis.get("width") or 0)
    height = int(analysis.get("height") or 0)
    if width <= 0 or height <= 0 or clip_duration <= 0 or not keep_ranges:
        return None
    std_w, std_h = compute_vertical_crop_dims(width, height)
    if std_w >= width:
        logger.info("Plain reframe plan skipped: crop window spans the full source width")
        return None
    ranges = [(float(s), float(e)) for s, e in keep_ranges if e > s]
    offsets = output_offsets_for_ranges(ranges)
    frame_area = float(width * height)

    samples: List[Sample] = analysis.get("samples") or []
    context: List[SceneEvidence] = analysis.get("context") or []
    cuts: List[float] = analysis.get("cuts") or []

    compact_context: List[SceneEvidence] = []
    for evidence in context:
        mapped = _map_source_time(evidence.time, ranges, offsets)
        if mapped is not None:
            compact_context.append(dataclasses.replace(evidence, time=mapped))
    compact_context.sort(key=lambda evidence: evidence.time)
    compact_cuts = set()
    for cut in cuts:
        mapped = _map_source_time(cut, ranges, offsets)
        if mapped is not None and 0 < mapped < clip_duration:
            compact_cuts.add(round(mapped, 3))
    for offset in offsets[1:]:
        if 0 < offset < clip_duration:
            compact_cuts.add(round(offset, 3))

    if compact_context:
        entries = build_context_layout_plan(
            compact_context, sorted(compact_cuts), clip_duration, std_w / width
        )
    else:
        entries = [
            {
                "start": 0.0,
                "end": clip_duration,
                "kind": "crop",
                "reason": "analysis_unavailable",
                "soft": False,
            }
        ]
    entries = _expand_stable_context(
        entries, compact_context, sorted(compact_cuts), std_w / width
    )
    entries = _split_entries_at_turnovers(entries, samples, ranges, offsets)

    shots: List[Dict[str, Any]] = []
    previous_crop_x: Optional[int] = None
    for entry in entries:
        start, end = float(entry["start"]), float(entry["end"])
        if end - start <= 0.01:
            continue
        pieces = _entry_source_pieces(start, end, ranges, offsets)
        shot_samples: List[Sample] = [
            (t, boxes)
            for t, boxes in samples
            if any(
                src_start - 1e-6 <= t < src_end + 1e-6
                for _cs, _ce, src_start, src_end, _off, _base in pieces
            )
        ]
        shot: Dict[str, Any] = {"start": start, "end": end, "mode": "plain"}
        tracks = associate_tracks(shot_samples)
        candidates = _tracks_above(
            tracks, len(shot_samples), frame_area,
            TRACK_MIN_PRESENCE_SHARE, TRACK_MIN_MEAN_AREA_FRAC,
        ) or _tracks_above(
            tracks, len(shot_samples), frame_area,
            RESCUE_MIN_PRESENCE_SHARE, RESCUE_MIN_MEAN_AREA_FRAC,
        )
        candidates.sort(key=lambda track: track.score, reverse=True)
        # Subjects' observed source-box envelopes for the caption/
        # title face guard; the fallback ladder (no usable candidate)
        # falls back to every tracked face in the shot.
        face_src = [
            box
            for box in (track.union_box() for track in candidates)
            if box is not None
        ]
        if not face_src:
            face_src = [
                box
                for box in (track.union_box() for track in tracks)
                if box is not None
            ]
        shot["face_src"] = [[float(v) for v in box] for box in face_src]
        shot["subject_src"] = _subject_envelope(
            compact_context, start, end, width, height
        )

        series: Optional[List[Tuple[float, Optional[Box]]]] = None
        subject_width = 0.0
        share = PLAIN_FACE_WIDTH_SHARE
        reason = "plain_single_subject"
        if len(candidates) >= 2:
            median_boxes = [
                box
                for box in (track.median_box() for track in candidates)
                if box is not None
            ]
            if median_boxes:
                union_w = max(b[0] + b[2] for b in median_boxes) - min(
                    b[0] for b in median_boxes
                )
                if union_w <= std_w * PLAIN_GROUP_WIDTH_SHARE:
                    # The group fits one region: frame the region, never
                    # a split stack.
                    series = _union_series(shot_samples, candidates)
                    subject_width = union_w
                    share = PLAIN_GROUP_WIDTH_SHARE
                    reason = "plain_group_region"
        asd_bias = None
        if series is None and candidates:
            # Speaking evidence (flag-gated) may steer the series to the
            # talking face; the window then sizes from that face, not
            # the largest one. Flag off / no evidence: today's rules.
            asd_bias = _speaking_bias(analysis, shot_samples, tracks, pieces)
            series_boxes = subject_series(
                shot_samples,
                tracks,
                sample_weights=asd_bias["weights"] if asd_bias else None,
            )
            series = [
                (t, box) for (t, _boxes), box in zip(shot_samples, series_boxes)
            ]
            if asd_bias is not None:
                winner_box = tracks[asd_bias["winner_track"]].median_box()
                subject_width = float(winner_box[2]) if winner_box else 0.0
            if subject_width <= 0.0:
                dominant_box = candidates[0].median_box()
                subject_width = float(dominant_box[2]) if dominant_box else 0.0

        if series is not None and any(box is not None for _t, box in series):
            window_w = _plain_window_width(subject_width, share, std_w)
            if window_w >= std_w:
                crop_w, crop_h = std_w, std_h
            else:
                crop_w, crop_h = _plain_window_dims(window_w, width, height)
            shot.update(
                reason=reason,
                crop_w=crop_w,
                crop_h=crop_h,
                pieces=_piece_keys(
                    series, pieces, width, crop_w, frame_area,
                    all_samples=shot_samples,
                ),
                y=_crop_y([box for _t, box in series], crop_h, height),
            )
            if asd_bias:
                stamp = _speaking_stamp(asd_bias, tracks, ranges, offsets)
                if stamp:
                    shot["speaking"] = stamp
        else:
            # No usable face: the existing fallback ladder (saliency
            # centroid, previous shot's crop, centre) at standard dims —
            # still a full-bleed crop, never a pad.
            fallback_x, fallback_reason = _fallback_crop(
                entry, compact_context, width, std_w, previous_crop_x
            )
            fallback_x = _avoid_face_bisection(
                fallback_x,
                std_w,
                [
                    box
                    for box in (track.median_box() for track in tracks)
                    if box is not None
                ],
                width,
            )
            shot.update(
                reason=f"plain_{fallback_reason}",
                crop_w=std_w,
                crop_h=std_h,
                pieces=[
                    {
                        "start": piece_start,
                        "end": piece_end,
                        "keys": [(piece_start, fallback_x)],
                    }
                    for piece_start, piece_end, _s, _e, _off, _base in pieces
                ],
                y=_default_crop_y(std_h, height),
            )
        # Amputation measurement, stamp-only: Plain never letterboxes
        # by design, so there is no fit to convert to — but a sliced
        # face is still recorded on the plan, never silent. Same for a
        # subject-less, motionless shot.
        audit = _audit_crop_amputation(
            shot, shot_samples, pieces, tracks, shot["crop_w"], frame_area
        )
        if audit is not None and audit["samples_cut"] >= AMPUTATION_MIN_SAMPLES_CUT:
            shot["amputation"] = {
                "samples_cut": audit["samples_cut"],
                "samples": audit["samples"],
                "tracks_cut": len(_persistent_cut_tracks(audit)),
            }
        # Same stamp-only treatment for the bright subject: Plain
        # never re-anchors or letterboxes, but a sliced non-face
        # subject is still recorded, never silent.
        saudit = _audit_subject_amputation(
            shot, compact_context, shot["crop_w"], width
        )
        if saudit is not None and _subject_persistent(saudit):
            shot["subject_amputation"] = {
                "samples_cut": saudit["samples_cut"],
                "samples": saudit["samples"],
            }
        flag = _content_flag(shot_samples, compact_context, start, end)
        if flag:
            shot["content_flag"] = flag
        shots.append(shot)
        static_x = _shot_static_x(shot)
        if static_x is not None:
            previous_crop_x = static_x

    if not shots:
        return None
    plan: Dict[str, Any] = {
        "delegate": None,
        "shots": shots,
        "width": width,
        "height": height,
    }
    if framing == "crop":
        plan["framing"] = "crop"
    else:
        plan["plain"] = True
    return plan


def build_fit_reframe_plan(
    analysis: Dict[str, Any],
    keep_ranges: Sequence[Tuple[float, float]],
    clip_duration: float,
) -> Optional[Dict[str, Any]]:
    """Always-fit reframe plan for a task whose framing_mode is ``"fit"``.

    Every shot is the plain letterbox fit — the whole source frame
    inside the output, blurred bands filling the rest — in the exact
    shot shape :func:`build_reframe_plan` emits for its own fit
    decisions (mode ``"fit"``, no ``fit_windows``, so segment
    resolution produces the unwindowed FIT_CHAIN layers). Shot spans
    follow the same compacted context entries and turnover splits the
    other planners use, so boundaries line up with the auto plan's.

    Face envelopes ride along as ``face_src`` exactly as the auto
    planner stamps them (union boxes of the significant tracks,
    strict tier first): segment resolution maps them through the
    letterbox transform (``_fit_output_boxes``), so the caption/title
    face guards keep working on a fitted clip. The plan never
    delegates: a reaction inset is part of the source frame, and a
    full-frame fit keeps it visible, so the EDL graph can compose
    every clip itself. Returns None for the same geometry the auto
    planner skips (portrait/narrow sources, where the legacy chain
    already shows the whole frame).
    """
    width = int(analysis.get("width") or 0)
    height = int(analysis.get("height") or 0)
    if width <= 0 or height <= 0 or clip_duration <= 0 or not keep_ranges:
        return None
    crop_w, crop_h = compute_vertical_crop_dims(width, height)
    if crop_w >= width:
        logger.info("Fit reframe plan skipped: crop window spans the full source width")
        return None
    ranges = [(float(s), float(e)) for s, e in keep_ranges if e > s]
    offsets = output_offsets_for_ranges(ranges)
    frame_area = float(width * height)

    samples: List[Sample] = analysis.get("samples") or []
    context: List[SceneEvidence] = analysis.get("context") or []
    cuts: List[float] = analysis.get("cuts") or []

    compact_context: List[SceneEvidence] = []
    for evidence in context:
        mapped = _map_source_time(evidence.time, ranges, offsets)
        if mapped is not None:
            compact_context.append(dataclasses.replace(evidence, time=mapped))
    compact_context.sort(key=lambda evidence: evidence.time)
    compact_cuts = set()
    for cut in cuts:
        mapped = _map_source_time(cut, ranges, offsets)
        if mapped is not None and 0 < mapped < clip_duration:
            compact_cuts.add(round(mapped, 3))
    for offset in offsets[1:]:
        if 0 < offset < clip_duration:
            compact_cuts.add(round(offset, 3))

    if compact_context:
        entries = build_context_layout_plan(
            compact_context, sorted(compact_cuts), clip_duration, crop_w / width
        )
    else:
        entries = [
            {
                "start": 0.0,
                "end": clip_duration,
                "kind": "fit",
                "reason": "analysis_unavailable",
                "soft": False,
            }
        ]
    entries = _expand_stable_context(
        entries, compact_context, sorted(compact_cuts), crop_w / width
    )
    entries = _split_entries_at_turnovers(entries, samples, ranges, offsets)

    shots: List[Dict[str, Any]] = []
    for entry in entries:
        start, end = float(entry["start"]), float(entry["end"])
        if end - start <= 0.01:
            continue
        pieces = _entry_source_pieces(start, end, ranges, offsets)
        entry_samples: List[Sample] = [
            (t, boxes)
            for t, boxes in samples
            if any(
                src_start - 1e-6 <= t < src_end + 1e-6
                for _cs, _ce, src_start, src_end, _off, _base in pieces
            )
        ]
        entry_tracks = associate_tracks(entry_samples)
        guard_tracks = _tracks_above(
            entry_tracks, len(entry_samples), frame_area,
            TRACK_MIN_PRESENCE_SHARE, TRACK_MIN_MEAN_AREA_FRAC,
        ) or _tracks_above(
            entry_tracks, len(entry_samples), frame_area,
            RESCUE_MIN_PRESENCE_SHARE, RESCUE_MIN_MEAN_AREA_FRAC,
        )
        shots.append(
            {
                "start": start,
                "end": end,
                "mode": "fit",
                "reason": "user_fit",
                "face_src": [
                    [float(v) for v in box]
                    for box in (track.union_box() for track in guard_tracks)
                    if box is not None
                ],
                "subject_src": _subject_envelope(
                    compact_context, start, end, width, height
                ),
            }
        )

    if not shots:
        return None
    return {
        "delegate": None,
        "framing": "fit",
        "shots": shots,
        "width": width,
        "height": height,
        "crop_w": crop_w,
        "crop_h": crop_h,
    }


def _resolve_segment_plain(
    segment: Dict[str, Any],
    shots: Sequence[Dict[str, Any]],
    width: int,
    height: int,
) -> None:
    """Attach one full-bleed crop to a segment from its plain shots.

    A segment renders through exactly one crop window, so the plain
    shot overlapping it longest supplies the window (its own tight
    dims and y); its trajectory keys are interpolated across the whole
    segment, which makes the renderer's single-crop fast path apply —
    no backdrop, no layers, nothing composited.
    """
    seg_start = float(segment.get("start", 0))
    seg_end = float(segment.get("end", seg_start))
    if seg_end <= seg_start:
        return
    overlapping = [
        shot
        for shot in shots
        if shot["end"] > seg_start + 0.001 and shot["start"] < seg_end - 0.001
    ]
    if not overlapping:
        return
    dominant = max(
        overlapping,
        key=lambda shot: min(seg_end, shot["end"]) - max(seg_start, shot["start"]),
    )
    crop_w = int(dominant.get("crop_w") or 0)
    crop_h = int(dominant.get("crop_h") or 0)
    if crop_w <= 0 or crop_h <= 0:
        return
    keys: List[Tuple[float, float]] = []
    for piece in dominant.get("pieces") or []:
        piece_keys = [(float(t), float(x)) for t, x in piece.get("keys") or []]
        keys.extend(
            _restrict_keys(
                piece_keys,
                max(seg_start, dominant["start"], piece["start"]),
                min(seg_end, dominant["end"], piece["end"]),
            )
        )
    if not keys:
        logger.warning(
            "Plain reframe plan: shot %.2f-%.2fs produced no crop keys; "
            "using the centre crop (fallback_center)",
            dominant["start"], dominant["end"],
        )
        keys = [(seg_start, (width - crop_w) // 2)]
    restricted = _restrict_keys(keys, seg_start, seg_end)
    expr = _piece_expression(restricted, seg_start)
    resolved_plain: Dict[str, Any] = {
        "crop": {
            "w": crop_w,
            "h": crop_h,
            "x_expr": expr,
            "y": int(dominant.get("y", 0)),
            "intervals": [[0, round(seg_end - seg_start, 3)]],
        }
    }
    guard_src = (dominant.get("face_src") or []) + (
        dominant.get("subject_src") or []
    )
    if guard_src and restricted:
        xs = [x for _t, x in restricted]
        boxes = _crop_output_boxes(
            guard_src,
            min(xs),
            max(xs),
            int(dominant.get("y", 0)),
            crop_w,
            crop_h,
        )
        if boxes:
            resolved_plain["face_boxes"] = [
                {
                    "start": 0,
                    "end": round(seg_end - seg_start, 3),
                    "boxes": boxes,
                }
            ]
    segment["reframe"] = resolved_plain


# --- EDL attachment -------------------------------------------------------------

def attach_reframe_plan(
    edl: Dict[str, Any],
    source_path: Path,
    keep_ranges: Sequence[Tuple[float, float]],
    plain: bool = False,
    framing: Optional[str] = None,
    layout_template: Optional[str] = None,
) -> bool:
    """Compute the reframe plan for an EDL and store it on the EDL.

    Adds ``edl["reframe"]`` (per-shot modes/reasons for QC and debugging,
    or a ``delegate: "plain"`` decision) and resolved per-segment crop
    data under ``segment["reframe"]``. Returns True when a plan was
    attached. Never raises: planning must never cost the user a clip, so
    any failure logs and leaves the EDL exactly as it was.

    ``plain=True`` (a Plain — just cuts task) plans with
    :func:`build_plain_reframe_plan` instead: every shot is a tight
    full-bleed crop, the section is stamped ``"plain": True``, and
    segments resolve through :func:`_resolve_segment_plain`.

    ``framing`` is the task's explicit framing_mode (see
    ``auto_director.framing_mode``); when given it wins over ``plain``:
    ``"fit"`` plans every shot as the letterbox fit
    (:func:`build_fit_reframe_plan`, section stamped
    ``"framing": "fit"``), ``"crop"`` plans the plain crop geometry
    without the Plain stamp (section stamped ``"framing": "crop"``),
    and ``"auto"`` plans with the regular planner even on a Plain
    task. ``framing=None`` (every task that predates the control, and
    every caller that does not know the task) reproduces the historic
    behaviour exactly: ``plain`` alone decides.
    """
    try:
        return _attach_reframe_plan(edl, Path(source_path), keep_ranges, plain, framing,
                                    layout_template=layout_template)
    except Exception:
        logger.warning(
            "Reframe planning failed; rendering without a reframe plan",
            exc_info=True,
        )
        return False


_CROP_FAMILY = frozenset({"single", "pair", "static", "plain", "crop"})
# SCREEN_SUBJECT only re-frames a clip whose screen runs cover this share.
SCREEN_SUBJECT_MIN_SHARE = 0.25


def consistent_layout_enabled() -> bool:
    """REFRAME_CONSISTENT (default on): one layout family per clip."""
    return os.getenv("REFRAME_CONSISTENT", "1").strip().lower() not in {"0", "false", "no", "off"}


def unify_layout_plan(
    plan: Optional[Dict[str, Any]],
    analysis: Dict[str, Any],
    ranges: Sequence[Tuple[float, float]],
    duration: float,
) -> Optional[Dict[str, Any]]:
    """One layout for the whole clip instead of switching shot by shot.

    The planner decides per shot (fit for wide or content shots, a face crop
    for close shots, split for two people). On a handheld vlog that meant the
    picture jumped between a zoomed crop and a small fit with blurred bands
    every second or two (Luffy, 2026-11-18: "too much zoom in zoom out ...
    make one type for the whole short"). When a clip mixes families, the
    family covering the most screen time wins and the whole clip is
    re-planned with it: the always-fit plan, or the always-crop face-tracked
    plan. A clip containing any split (two-person) shots keeps the planner's
    plan: a split segment is a deliberate two-person layout, not a zoom jump,
    and flattening it into a single-face plan loses the second person.
    Delegated (facecam) clips are untouched.
    """
    if plan is None or plan.get("delegate") or not consistent_layout_enabled():
        return plan
    shots = plan.get("shots") or []
    if len(shots) < 2:
        return plan
    totals = {"fit": 0.0, "split": 0.0, "crop": 0.0}
    for shot in shots:
        span = max(0.0, float(shot.get("end", 0)) - float(shot.get("start", 0)))
        mode = str(shot.get("mode") or "")
        family = "fit" if mode == "fit" else "split" if mode == "split" else "crop"
        totals[family] += span
    if totals["split"] > 0:
        return plan
    if sum(1 for value in totals.values() if value > 0) < 2:
        return plan
    winner = max(totals, key=totals.get)
    unified = (
        build_fit_reframe_plan(analysis, ranges, duration)
        if winner == "fit"
        else build_plain_reframe_plan(analysis, ranges, duration, framing="crop")
    )
    if unified is None:
        return plan
    logger.info(
        "Reframe plan: one layout for the whole clip (%s); shot time was %s",
        winner, {k: round(v, 1) for k, v in totals.items()},
    )
    unified["unified_from"] = {k: round(v, 2) for k, v in totals.items()}
    return unified


def _layout_facecam_found(source_path: Path, analysis: Dict[str, Any], ranges) -> bool:
    from .gaming_templates import MANUAL_BOXES

    if MANUAL_BOXES.get():
        return True
    try:
        from .facecam_rect import find_facecam_rect, probe_times

        width, height = int(analysis.get("width") or 0), int(analysis.get("height") or 0)
        if width <= 0 or height <= 0:
            return False
        return find_facecam_rect(source_path, width, height, probe_times(ranges)) is not None
    except Exception as exc:
        logger.debug("Layout facecam check unavailable: %s", exc)
        return False


def _attach_reframe_plan(
    edl: Dict[str, Any],
    source_path: Path,
    keep_ranges: Sequence[Tuple[float, float]],
    plain: bool = False,
    framing: Optional[str] = None,
    layout_template: Optional[str] = None,
) -> bool:
    if not edl or not edl.get("segments"):
        return False
    ranges = [(float(s), float(e)) for s, e in keep_ranges if e > s]
    if not ranges:
        return False
    analysis = get_source_analysis(source_path, ranges)
    if analysis is None:
        return False
    duration = float(edl.get("duration") or 0) or rendered_ranges_duration(ranges)
    if layout_template and _layout_facecam_found(source_path, analysis, ranges):
        # The user picked "face on top, game below": it wins over the
        # framing choice whenever the clip has a facecam. Only the plain
        # path composes the stack (the EDL graph cannot), so the clip is
        # delegated to it; without a facecam the framing choice stands.
        logger.info("Reframe plan: %s chosen and a facecam found; delegating to the layout path", layout_template)
        edl["reframe"] = {"version": 1, "delegate": "plain", "shots": [], "layout_template": layout_template}
        return True
    if framing == "fit":
        plan = build_fit_reframe_plan(analysis, ranges, duration)
    elif framing == "crop":
        plan = build_plain_reframe_plan(analysis, ranges, duration, framing="crop")
    elif plain and framing != "auto":
        plan = build_plain_reframe_plan(analysis, ranges, duration)
    else:
        raw_plan = build_reframe_plan(analysis, ranges, duration, source_path=source_path)
        plan = unify_layout_plan(raw_plan, analysis, ranges, duration)
        from .screen_subject import apply_screen_windows, screen_subject_enabled

        if (
            screen_subject_enabled()
            and raw_plan is not None
            and not raw_plan.get("delegate")
            # Same guard as unify_layout_plan: two corroborated people in
            # a split shot are never re-planned away.
            and not any(sh.get("mode") == "split" for sh in raw_plan.get("shots") or [])
        ):
            # A lit screen carrying the story (clip1 Step 3). When the clip
            # shows one, the whole clip uses ONE stable full-frame layout and
            # switches to the screen-on-top / person-below stack only while
            # the screen is on (layout changes only where content changes;
            # the shot-by-shot subject plan would switch every few seconds).
            try:
                from .screen_subject import screen_runs

                width = int(raw_plan.get("width") or 0)
                height = int(raw_plan.get("height") or 0)
                runs = screen_runs(source_path, ranges, duration, width, height)
                share = sum(r["end"] - r["start"] for r in runs) / max(duration, 1e-6)
                if runs and share < SCREEN_SUBJECT_MIN_SHARE:
                    # A bright patch seen briefly (a window, a lamp, a lit
                    # monitor in a cutaway) is not the clip's subject: the
                    # normal layout stands (validation 2026-11-18: vlog 21%,
                    # talking head 13% were false screens; clip1's real
                    # nominees screen was 40%).
                    logger.info("Screen subject: screen in %.0f%% of the clip; layout unchanged", share * 100)
                    runs = []
                if runs:
                    fit_plan = build_fit_reframe_plan(analysis, ranges, duration)
                    if fit_plan is not None and apply_screen_windows(
                        fit_plan, source_path, ranges, runs=runs
                    ):
                        plan = fit_plan
            except Exception as exc:
                logger.warning("Screen subject framing unavailable: %s", exc)
    if plan is None:
        return False
    # The plain GEOMETRY (a real Plain plan, or a framing="crop" plan)
    # resolves through the plain segment path and skips border trims,
    # exactly as Plain always has; every other plan keeps the trim pass.
    plain_geometry = bool(plan.get("plain")) or plan.get("framing") == "crop"
    if not plain_geometry and not plan.get("delegate"):
        _stamp_border_trims(plan, source_path, ranges)
    shot_entries: List[Dict[str, Any]] = []
    for shot in plan.get("shots") or []:
        shot_entry: Dict[str, Any] = {
            "start": round(float(shot["start"]), 3),
            "end": round(float(shot["end"]), 3),
            "mode": shot["mode"],
            "reason": shot.get("reason", ""),
        }
        if shot.get("border_trim"):
            shot_entry["border_trim"] = shot["border_trim"]
        shot_entries.append(shot_entry)
    section: Dict[str, Any] = {
        "version": 1,
        "delegate": plan.get("delegate"),
        "shots": shot_entries,
    }
    if plan.get("plain"):
        section["plain"] = True
    if plan.get("framing"):
        section["framing"] = plan["framing"]
    edl["reframe"] = section
    if plan.get("delegate"):
        return True
    if plain_geometry:
        for segment in edl["segments"]:
            _resolve_segment_plain(
                segment,
                plan["shots"],
                int(plan["width"]),
                int(plan["height"]),
            )
        modes = [shot["mode"] for shot in plan.get("shots") or []]
        logger.info(
            "Plain reframe plan for EDL %s: %d shot(s) — %s",
            edl.get("clip_id", "unknown"),
            len(modes),
            ", ".join(modes) if modes else "none",
        )
        return True
    for segment in edl["segments"]:
        _resolve_segment(
            segment,
            plan["shots"],
            int(plan["crop_w"]),
            int(plan["crop_h"]),
            int(plan["width"]),
            int(plan["height"]),
        )
    modes = [shot["mode"] for shot in plan.get("shots") or []]
    logger.info(
        "Reframe plan for EDL %s: %d shot(s) — %s",
        edl.get("clip_id", "unknown"),
        len(modes),
        ", ".join(modes) if modes else "none",
    )
    return True
