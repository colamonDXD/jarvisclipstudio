"""Per-clip quality control for finished renders (Final Directive Part I).

Every rendered clip is inspected before it is presented as done. The checks
are measurements, not vibes: each returns pass / warn / fail / skip plus the
numbers it measured, and the report is stored on the clip row so a bad render
is visible instead of silently shipped.

The lesson this module exists for: on 2026-11-07 the pipeline shipped clips
with no background music at all and nobody noticed until the user watched
one. The music check below is the one whose absence caused that — when a
profile or an explicit user pick says a clip should have music, QC measures
the audio level inside the speech gaps and warns loudly when those gaps are
digital silence.

Hard failures (the file is not a watchable clip: missing, unprobeable,
wrong duration, wrong resolution, all-black, frozen-and-silent) trigger
exactly one automatic re-render in the pipeline seam; warnings never do.
Caption synchronisation is intentionally NOT here — see docs/qc.md for the
honest limits. The subject-in-frame check below is the face-framing
counterpart: it verifies that shots the reframe planner framed around a
subject actually show a face, and it is warn-only by design — a sampled
detector can miss a turned or occluded head that a human would still call
in-frame, so it flags for review and never triggers a re-render.
"""

from __future__ import annotations

import hashlib
import os
import subprocess
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional, Sequence

import numpy as np

from .common import logger
from .ffmpeg import ffprobe_duration, ffprobe_video_size, select_audio_stream_index
from .gap_activity import _window_levels_db

# --- Thresholds (see docs/qc.md for how each was chosen) -------------------

DURATION_TOLERANCE_SECONDS = 0.5

# Mean luma (0-255, sampled at 64x36 gray) at or below this counts as a
# near-black frame: a true black frame measures 0-2 even after H.264 noise,
# while the darkest legitimate scene content sits well above it.
BLACK_LUMA_MAX = 10.0
BLACK_SAMPLE_FRACTIONS = (0.10, 0.25, 0.50, 0.75, 0.90)

# Largest frame-to-frame mean absolute difference (0-255) across the whole
# sampled clip for the picture to count as frozen. A static slate measures
# ~0; any real camera or screen motion is far above 1.
FROZEN_DIFF_MAX = 1.0
FROZEN_SAMPLE_FPS = 2
FROZEN_MAX_SAMPLE_SECONDS = 300.0

# Overall RMS at or below this (dBFS) is digital silence: loudnorm targets
# -14 LUFS, so real content — even a quiet room tone — sits far above -55.
AUDIO_SILENCE_RMS_DB = -55.0

# Music presence is judged inside speech gaps: stretches where the level
# drops at least MUSIC_SPEECH_DROP_DB under the speech level. Cleanup keeps
# ~0.4s of silence at junctions, so a 0.3s run is a real gap, and a clip
# needs ~0.8s of total gap before the judgement is meaningful.
MUSIC_SPEECH_DROP_DB = 18.0
MUSIC_GAP_RUN_MIN_SECONDS = 0.3
MUSIC_GAP_TOTAL_MIN_SECONDS = 0.8
# Gap RMS at or below this means the gaps carry no music bed: a ducked bed
# under silence plays at the profile volume (typically -25..-40 dBFS RMS),
# while a music-less render leaves the gaps at digital silence.
MUSIC_GAP_SILENCE_DB = -50.0
# Fallback when a clip has no measurable gaps: the 5th-percentile window
# level at or above this is consistent with a music bed under the speech.
MUSIC_FLOOR_DB = -42.0
# Below this speech reference the clip is too quiet overall to judge music.
MUSIC_SPEECH_REFERENCE_MIN_DB = -45.0

# Two clips of one task whose source ranges overlap by at least this IoU
# are the same moment picked twice.
DUPLICATE_IOU_WARN = 0.5

_FRAME_W = 64
_FRAME_H = 36

# Matches gap_activity's 50ms windowing (its levels helper is reused).
_AUDIO_WINDOW_SECONDS = 0.05

_VERTICAL_FORMATS = {"vertical", "vertical_fit", "vertical_pan", "vertical_split"}
_VERTICAL_WIDTH = 1080
_VERTICAL_HEIGHT = 1920


@dataclass
class ClipQcContext:
    """What a correct render of this clip was supposed to look like."""

    expected_duration: Optional[float] = None
    output_format: str = "vertical"
    # Plan cap applied after rendering (cap_video_short_side); the finished
    # file's short side must match it when set.
    max_short_side: Optional[int] = None
    # Source-timeline range (seconds) the clip was cut from, for duplicate
    # detection against its siblings.
    source_start: Optional[float] = None
    source_end: Optional[float] = None
    # Music expectation: an explicit user pick (music_override) or a profile
    # mood with volume > 0. ``music_label`` names what was expected (track
    # filename or mood) so the warning can say exactly what is missing.
    music_expected: bool = False
    music_label: Optional[str] = None
    # Other clips of the same task: {"start", "end", "sha256", "label"}.
    siblings: Sequence[dict[str, Any]] = field(default_factory=tuple)
    # Per-shot reframe plan modes on the clip timeline ({"start", "end",
    # "mode"}), from the clip's source-map sidecar. The subject-in-frame
    # check verifies only shots the planner framed around a subject;
    # fit-mode shots and clips without a plan are skipped.
    reframe_shots: Sequence[dict[str, Any]] = field(default_factory=tuple)
    # Quality Director compliance inputs (all optional; the matching
    # check skips when its input is absent):
    # - hook_outcome: the hook enforcement record from the clip's plan
    #   ({"status", "reason", "hook_timestamp_after", ...}).
    # - cut_rate_band: the profile's target_cuts_per_min (low, high).
    # - rendered_segment_count: EDL segments the render joined (from the
    #   clip's render-facts sidecar).
    # - caption_timings: the caption chunks the render actually burned
    #   ([{"start", "end"}]), same sidecar.
    # - caption_hold_cap_seconds: the pacing cap those chunks must obey.
    hook_outcome: Optional[dict[str, Any]] = None
    cut_rate_band: Optional[tuple] = None
    rendered_segment_count: Optional[int] = None
    caption_timings: Optional[Sequence[dict[str, Any]]] = None
    caption_hold_cap_seconds: Optional[float] = None
    # Phase 2 checks (optional; skipped when absent, so older reports are
    # unchanged): the platform loudness target the finished clip should
    # meet (short-form platforms normalise to about -14 LUFS), and the
    # source transcript words (seconds, {"text","start","end"}) used with
    # source_start/source_end to judge whether the clip starts and ends on
    # a complete thought.
    loudness_target_lufs: Optional[float] = None
    transcript_words: Optional[Sequence[dict[str, Any]]] = None
    # The beat-density record the render stored (media/beat_density.py:
    # {"audit", "repair", "after"}), from the clip's render-facts
    # sidecar. The beat_density check surfaces it; absent (the pass is
    # off by default) the check does not appear in the report at all.
    beat_density: Optional[dict[str, Any]] = None


def _check(name: str, status: str, detail: str, **evidence: Any) -> dict[str, Any]:
    return {"name": name, "status": status, "detail": detail, "evidence": evidence}


def _decode(command: list[str], timeout: int = 120) -> bytes:
    result = subprocess.run(command, capture_output=True, timeout=timeout)
    if result.returncode != 0:
        raise RuntimeError(result.stderr.decode("utf-8", "replace")[-500:])
    return result.stdout


def _gray_frame(path: Path, at_sec: float) -> Optional[np.ndarray]:
    """One frame at ``at_sec`` as a 64x36 float gray array, or None."""
    try:
        raw = _decode(
            [
                "ffmpeg", "-v", "error", "-ss", f"{max(0.0, at_sec):.3f}",
                "-i", str(path), "-frames:v", "1",
                "-vf", f"scale={_FRAME_W}:{_FRAME_H},format=gray",
                "-f", "rawvideo", "-",
            ],
            timeout=60,
        )
    except Exception:
        return None
    frame = np.frombuffer(raw, dtype=np.uint8)
    if frame.size < _FRAME_W * _FRAME_H:
        return None
    return frame[: _FRAME_W * _FRAME_H].reshape(_FRAME_H, _FRAME_W).astype(np.float32)


_COLOR_FRAME_W = 480


def _color_frame(path: Path, at_sec: float) -> Optional[np.ndarray]:
    """One frame at ``at_sec`` as a BGR array (480 px wide), or None.

    Colour, unlike the 64x36 gray measurement frames: the face detector
    needs real pixels. 480 px wide matches the size the detector itself
    caps at, keeping the whole check inside its time budget.
    """
    try:
        raw = _decode(
            [
                "ffmpeg", "-v", "error", "-ss", f"{max(0.0, at_sec):.3f}",
                "-i", str(path), "-frames:v", "1",
                "-vf", f"scale={_COLOR_FRAME_W}:-2,format=bgr24",
                "-f", "rawvideo", "-",
            ],
            timeout=30,
        )
    except Exception:
        return None
    frame = np.frombuffer(raw, dtype=np.uint8)
    row = _COLOR_FRAME_W * 3
    if frame.size < row * 8 or frame.size % row:
        return None
    return frame.reshape(frame.size // row, _COLOR_FRAME_W, 3)


def _gray_sequence_max_diff(path: Path, duration: float) -> Optional[float]:
    """Largest frame-to-frame change in the clip, or None if unmeasurable."""
    sample_seconds = min(duration, FROZEN_MAX_SAMPLE_SECONDS)
    raw = _decode(
        [
            "ffmpeg", "-v", "error", "-i", str(path),
            "-t", f"{sample_seconds:.3f}", "-an",
            "-vf", f"fps={FROZEN_SAMPLE_FPS},scale={_FRAME_W}:{_FRAME_H},format=gray",
            "-f", "rawvideo", "-",
        ],
        timeout=180,
    )
    frames = np.frombuffer(raw, dtype=np.uint8)
    count = len(frames) // (_FRAME_W * _FRAME_H)
    if count < 2:
        return None
    frames = frames[: count * _FRAME_W * _FRAME_H].reshape(
        count, _FRAME_H, _FRAME_W
    ).astype(np.float32)
    changes = np.abs(np.diff(frames, axis=0)).mean(axis=(1, 2))
    return float(changes.max()) if len(changes) else None


def _power_mean_db(levels: np.ndarray) -> float:
    if len(levels) == 0:
        return -120.0
    return float(10.0 * np.log10(np.mean(np.power(10.0, levels / 10.0))))


def check_duration(actual: float, expected: Optional[float]) -> dict[str, Any]:
    if expected is None or expected <= 0:
        return _check(
            "duration", "skip", "No expected duration recorded for this clip",
            actual_seconds=round(actual, 3),
        )
    delta = abs(actual - expected)
    status = "pass" if delta <= DURATION_TOLERANCE_SECONDS else "fail"
    return _check(
        "duration", status,
        f"Clip is {actual:.2f}s, expected {expected:.2f}s "
        f"(delta {delta:.2f}s, tolerance {DURATION_TOLERANCE_SECONDS}s)",
        actual_seconds=round(actual, 3), expected_seconds=round(expected, 3),
        delta_seconds=round(delta, 3),
    )


def check_resolution(
    width: int, height: int, context: ClipQcContext
) -> dict[str, Any]:
    evidence = {"width": width, "height": height, "output_format": context.output_format}
    if context.output_format == "original":
        return _check(
            "resolution", "pass",
            f"Original format keeps the source size ({width}x{height})",
            **evidence,
        )
    if context.output_format not in _VERTICAL_FORMATS:
        return _check(
            "resolution", "skip",
            f"No fixed canvas known for format '{context.output_format}'",
            **evidence,
        )
    expected_short = _VERTICAL_WIDTH
    if context.max_short_side:
        expected_short = min(expected_short, int(context.max_short_side))
    expected_long = round(_VERTICAL_HEIGHT * expected_short / _VERTICAL_WIDTH)
    short_side, long_side = min(width, height), max(width, height)
    aspect = long_side / short_side if short_side else 0.0
    expected_aspect = _VERTICAL_HEIGHT / _VERTICAL_WIDTH
    aspect_ok = abs(aspect - expected_aspect) / expected_aspect <= 0.02
    size_ok = abs(short_side - expected_short) <= 4
    evidence.update(
        expected_width=expected_short, expected_height=expected_long,
        aspect=round(aspect, 4),
    )
    if aspect_ok and size_ok:
        return _check(
            "resolution", "pass",
            f"Clip is {width}x{height}, expected {expected_short}x{expected_long}",
            **evidence,
        )
    return _check(
        "resolution", "fail",
        f"Clip is {width}x{height}, expected {expected_short}x{expected_long} "
        f"(9:16 canvas)",
        **evidence,
    )


def check_black_frames(path: Path, duration: float) -> dict[str, Any]:
    lumas: list[Optional[float]] = []
    for fraction in BLACK_SAMPLE_FRACTIONS:
        frame = _gray_frame(path, duration * fraction)
        lumas.append(None if frame is None else float(frame.mean()))
    measured = [value for value in lumas if value is not None]
    evidence = {
        "sample_luma": [None if v is None else round(v, 2) for v in lumas],
        "black_threshold": BLACK_LUMA_MAX,
    }
    if not measured:
        return _check("black_frames", "skip", "Could not extract sample frames", **evidence)
    black_count = sum(1 for value in measured if value <= BLACK_LUMA_MAX)
    if black_count == len(measured):
        return _check(
            "black_frames", "fail",
            f"All {len(measured)} sampled frames are near-black "
            f"(mean luma <= {BLACK_LUMA_MAX})",
            **evidence,
        )
    if black_count:
        return _check(
            "black_frames", "warn",
            f"{black_count} of {len(measured)} sampled frames are near-black",
            **evidence,
        )
    return _check(
        "black_frames", "pass",
        f"No near-black frames in {len(measured)} samples",
        **evidence,
    )


def check_audio(
    levels: Optional[np.ndarray], has_audio_stream: bool
) -> tuple[dict[str, Any], bool]:
    """Audio presence + level. Returns (check, is_silent)."""
    if not has_audio_stream:
        return _check(
            "audio", "warn", "Clip has no audio stream",
            has_audio_stream=False,
        ), True
    if levels is None or len(levels) == 0:
        return _check(
            "audio", "warn", "Audio stream exists but could not be measured",
            has_audio_stream=True,
        ), True
    rms_db = _power_mean_db(levels)
    silent = rms_db <= AUDIO_SILENCE_RMS_DB
    if silent:
        return _check(
            "audio", "warn",
            f"Audio is digital silence (overall RMS {rms_db:.1f} dBFS)",
            has_audio_stream=True, rms_db=round(rms_db, 2),
        ), True
    return _check(
        "audio", "pass",
        f"Audio present (overall RMS {rms_db:.1f} dBFS)",
        has_audio_stream=True, rms_db=round(rms_db, 2),
    ), False


def check_music(levels: Optional[np.ndarray], context: ClipQcContext) -> dict[str, Any]:
    """Judge an expected music bed by the level inside speech gaps.

    Only called when the context says music was intended. A clip whose gaps
    are digital silence has no audible bed no matter what the EDL asked
    for — that is the 2026-11-07 failure this check exists to catch.
    """
    label = context.music_label or "music"
    base = {"expected_track": label}
    if levels is None or len(levels) == 0:
        return _check(
            "music", "skip",
            f"Music expected ({label}) but the audio could not be measured",
            **base,
        )
    speech_db = float(np.percentile(levels, 75))
    if speech_db < MUSIC_SPEECH_REFERENCE_MIN_DB:
        return _check(
            "music", "skip",
            f"Music expected ({label}) but the clip is too quiet overall "
            f"(speech reference {speech_db:.1f} dB) to judge the gaps",
            speech_reference_db=round(speech_db, 2), **base,
        )
    gap_threshold = speech_db - MUSIC_SPEECH_DROP_DB
    is_gap = levels < gap_threshold
    # Collect the windows belonging to qualifying gap runs.
    gap_indices: list[int] = []
    run_start: Optional[int] = None
    min_run = max(1, int(MUSIC_GAP_RUN_MIN_SECONDS / _AUDIO_WINDOW_SECONDS))
    for index, flag in enumerate(is_gap):
        if flag and run_start is None:
            run_start = index
        elif not flag and run_start is not None:
            if index - run_start >= min_run:
                gap_indices.extend(range(run_start, index))
            run_start = None
    if run_start is not None and len(is_gap) - run_start >= min_run:
        gap_indices.extend(range(run_start, len(is_gap)))
    gap_seconds = len(gap_indices) * _AUDIO_WINDOW_SECONDS
    if gap_seconds >= MUSIC_GAP_TOTAL_MIN_SECONDS:
        gap_rms = _power_mean_db(levels[np.array(gap_indices)])
        if gap_rms <= MUSIC_GAP_SILENCE_DB:
            return _check(
                "music", "warn",
                f"MUSIC MISSING: expected {label}, but the speech gaps are "
                f"silent (gap RMS {gap_rms:.1f} dB across {gap_seconds:.1f}s "
                f"of gaps) — the render has no audible music bed",
                gap_rms_db=round(gap_rms, 2), gap_seconds=round(gap_seconds, 2),
                speech_reference_db=round(speech_db, 2),
                # An expectation failure (the effective decision said
                # music) — and mechanically fixable: one corrective
                # re-render with the bed applied.
                expectation_failed=True, fixable=True, **base,
            )
        return _check(
            "music", "pass",
            f"Music bed audible in speech gaps (gap RMS {gap_rms:.1f} dB "
            f"across {gap_seconds:.1f}s of gaps)",
            gap_rms_db=round(gap_rms, 2), gap_seconds=round(gap_seconds, 2),
            speech_reference_db=round(speech_db, 2), **base,
        )
    floor_db = float(np.percentile(levels, 5))
    if floor_db >= MUSIC_FLOOR_DB:
        return _check(
            "music", "pass",
            f"No silent gaps to judge; the audio floor ({floor_db:.1f} dB) "
            f"is consistent with a music bed under the speech",
            floor_db=round(floor_db, 2), gap_seconds=round(gap_seconds, 2),
            speech_reference_db=round(speech_db, 2), **base,
        )
    return _check(
        "music", "skip",
        f"Music expected ({label}) but the clip has only "
        f"{gap_seconds:.1f}s of speech gaps — not enough to judge whether "
        f"a bed is playing",
        floor_db=round(floor_db, 2), gap_seconds=round(gap_seconds, 2),
        speech_reference_db=round(speech_db, 2), **base,
    )


def check_hook_window(context: ClipQcContext) -> dict[str, Any]:
    """Compliance: the clip's hook lands inside the first ~2 s.

    Judged from the hook enforcement record on the clip's plan, not by
    re-deriving anything: ``already_leading``/``cold_opened`` must show
    a final hook timestamp inside the window; ``kept_original_context``
    is a deliberate, reasoned exception (context preservation beats the
    2-second rule) and is reported as a warning with that reason, never
    hidden; ``no_hook`` means the planner found nothing to enforce.
    """
    outcome = context.hook_outcome
    if not outcome:
        return _check(
            "hook_window", "skip",
            "No hook decision was recorded for this clip",
        )
    from ..auto_director.hook_enforcement import HOOK_WINDOW_SECONDS

    status = str(outcome.get("status") or "")
    reason = str(outcome.get("reason") or "")
    after = outcome.get("hook_timestamp_after")
    evidence = {"hook_status": status, "hook_timestamp": after}
    if status == "kept_original_context":
        return _check(
            "hook_window", "warn",
            f"Hook stays late on purpose (context preserved): {reason}",
            **evidence,
        )
    if status == "no_hook":
        return _check(
            "hook_window", "warn",
            f"No hook moment was identified for this clip: {reason}",
            **evidence,
        )
    if status in ("already_leading", "cold_opened"):
        if after is not None and float(after) <= HOOK_WINDOW_SECONDS + 0.05:
            return _check(
                "hook_window", "pass",
                f"Hook lands at {float(after):.1f}s, inside the first "
                f"{HOOK_WINDOW_SECONDS:.0f}s ({status})",
                **evidence,
            )
        return _check(
            "hook_window", "warn",
            f"Hook enforcement recorded '{status}' but the hook lands at "
            f"{after}s — outside the first {HOOK_WINDOW_SECONDS:.0f}s",
            **evidence,
        )
    return _check(
        "hook_window", "skip",
        f"Unrecognised hook outcome status: {status or 'missing'}",
        **evidence,
    )


def check_cut_rate(context: ClipQcContext, duration: float) -> dict[str, Any]:
    """Compliance: rendered segments/minute inside the profile's band.

    The segment count is what the render actually joined (render-facts
    sidecar). A tolerance around the band absorbs tiling rounding; the
    check is a warning only — segment count approximates the perceived
    cut rate (transitions soften joins), it does not define it.
    """
    band = context.cut_rate_band
    count = context.rendered_segment_count
    if not band or not count or duration <= 0:
        return _check(
            "cut_rate", "skip",
            "No cut-rate band or rendered segment count recorded",
        )
    low, high = float(band[0]), float(band[1])
    rate = count / duration * 60.0
    evidence = {
        "segments": count,
        "cuts_per_min": round(rate, 1),
        "band": [low, high],
    }
    low_ok = low - max(1.0, 0.1 * low)
    high_ok = high + max(1.0, 0.1 * high)
    if low_ok <= rate <= high_ok:
        return _check(
            "cut_rate", "pass",
            f"Cut rate {rate:.0f}/min is inside the profile band "
            f"{low:.0f}-{high:.0f}/min",
            **evidence,
        )
    return _check(
        "cut_rate", "warn",
        f"Cut rate {rate:.0f}/min is outside the profile band "
        f"{low:.0f}-{high:.0f}/min",
        **evidence,
    )


def check_caption_pacing(
    context: ClipQcContext, audio_silent: bool
) -> list[dict[str, Any]]:
    """Compliance: captions exist, and no chunk outstays the pacing cap.

    Measured from the caption timings the render actually burned
    (render-facts sidecar) — not from the transcript — so a chunk the
    builder split is seen split, and one it could not split (a single
    word longer than the cap) is flagged honestly. An over-cap hold is
    mechanically fixable: the corrective re-render re-chunks.
    """
    timings = context.caption_timings
    if timings is None:
        return [
            _check(
                "caption_presence", "skip",
                "No caption timings were recorded for this render",
            ),
            _check(
                "caption_hold", "skip",
                "No caption timings were recorded for this render",
            ),
        ]
    chunks = [
        (float(entry.get("start") or 0.0), float(entry.get("end") or 0.0))
        for entry in timings
        if isinstance(entry, dict)
    ]
    if not chunks:
        presence = (
            _check(
                "caption_presence", "pass",
                "No captions were burned and the clip has no audible "
                "speech to caption",
            )
            if audio_silent
            else _check(
                "caption_presence", "warn",
                "The clip has audible speech but no captions were "
                "recorded for the render",
            )
        )
        return [
            presence,
            _check(
                "caption_hold", "skip", "No caption chunks to measure",
            ),
        ]
    presence = _check(
        "caption_presence", "pass",
        f"{len(chunks)} caption chunks were burned into the render",
        chunks=len(chunks),
    )
    cap = context.caption_hold_cap_seconds
    if not cap or cap <= 0:
        return [
            presence,
            _check(
                "caption_hold", "skip",
                "No caption hold cap was recorded for this clip",
            ),
        ]
    holds = [end - start for start, end in chunks]
    worst = max(holds)
    worst_index = int(np.argmax(holds))
    evidence = {
        "max_hold_seconds": round(worst, 2),
        "cap_seconds": cap,
        "worst_chunk": worst_index,
    }
    if worst <= cap + 0.05:
        return [
            presence,
            _check(
                "caption_hold", "pass",
                f"Longest caption hold is {worst:.1f}s, within the "
                f"{cap:.1f}s pacing cap",
                **evidence,
            ),
        ]
    return [
        presence,
        _check(
            "caption_hold", "warn",
            f"Caption chunk {worst_index} stays on screen for "
            f"{worst:.1f}s — over the {cap:.1f}s pacing cap",
            fixable=True, **evidence,
        ),
    ]


def check_beat_density(context: ClipQcContext) -> dict[str, Any]:
    """Retention: the finished clip's longest no-change stretch.

    Judged from the beat-density record the render itself stored
    (media/beat_density.py measured the finished file), not re-derived
    here. Warn-only: a dead stretch is an editing-quality flag, never
    a hard failure, and the one-punch repair — when it ran — already
    happened at render time; this check reports the outcome, including
    the post-repair longest stretch when a repair was applied.
    """
    record = context.beat_density or {}
    audit = record.get("audit") or {}
    longest = audit.get("longest_static_s")
    if longest is None:
        return _check(
            "beat_density", "skip",
            "Beat-density record carried no audit measurement",
        )
    longest = float(longest)
    limit = float(audit.get("max_static_s") or 0.0)
    score = audit.get("beat_score")
    evidence = {
        "longest_static_seconds": round(longest, 2),
        "max_static_seconds": limit,
        "beat_score": score,
    }
    repair = record.get("repair") or {}
    if repair.get("applied"):
        after = record.get("after") or {}
        after_longest = after.get("longest_static_s")
        evidence["repair_applied"] = True
        evidence["repair_at_seconds"] = repair.get("at")
        evidence["longest_static_after_seconds"] = (
            None if after_longest is None else round(float(after_longest), 2)
        )
        return _check(
            "beat_density", "warn",
            f"Longest static stretch was {longest:.1f}s (limit "
            f"{limit:.1f}s); one repair punch-in was applied at "
            f"{float(repair.get('at') or 0):.1f}s, longest stretch now "
            f"{float(after_longest or 0):.1f}s",
            **evidence,
        )
    if audit.get("ok"):
        return _check(
            "beat_density", "pass",
            f"Longest static stretch is {longest:.1f}s, within the "
            f"{limit:.1f}s beat limit (beat score {score})",
            **evidence,
        )
    return _check(
        "beat_density", "warn",
        f"Longest static stretch is {longest:.1f}s — over the "
        f"{limit:.1f}s beat limit (beat score {score}); no repair was "
        f"applied",
        **evidence,
    )


def check_frozen(
    path: Path, duration: float, audio_silent: bool
) -> dict[str, Any]:
    try:
        max_diff = _gray_sequence_max_diff(path, duration)
    except Exception as exc:
        return _check("frozen_frame", "skip", f"Could not sample frames: {exc}")
    if max_diff is None:
        return _check(
            "frozen_frame", "skip", "Too few frames to judge motion",
        )
    evidence = {"max_frame_diff": round(max_diff, 3), "frozen_threshold": FROZEN_DIFF_MAX}
    if max_diff > FROZEN_DIFF_MAX:
        return _check(
            "frozen_frame", "pass",
            f"Picture moves (max frame-to-frame change {max_diff:.2f})",
            **evidence,
        )
    if audio_silent:
        return _check(
            "frozen_frame", "fail",
            f"Picture never changes (max frame-to-frame change "
            f"{max_diff:.2f}) and the audio is silent — this is a frozen, "
            f"silent render, not a watchable clip",
            **evidence,
        )
    return _check(
        "frozen_frame", "warn",
        f"Picture never changes (max frame-to-frame change {max_diff:.2f}); "
        f"static content can be legitimate, so this is a warning only",
        **evidence,
    )


def _range_iou(
    a_start: float, a_end: float, b_start: float, b_end: float
) -> float:
    intersection = max(0.0, min(a_end, b_end) - max(a_start, b_start))
    union = max(a_end, b_end) - min(a_start, b_start)
    return intersection / union if union > 0 else 0.0


def check_duplicate(
    context: ClipQcContext, file_sha256: Optional[str]
) -> dict[str, Any]:
    for sibling in context.siblings:
        label = sibling.get("label") or "a sibling clip"
        sibling_hash = sibling.get("sha256")
        if file_sha256 and sibling_hash and file_sha256 == sibling_hash:
            return _check(
                "duplicate", "warn",
                f"File is byte-identical to {label} (same SHA-256)",
                duplicate_of=label, match="sha256",
            )
        start, end = sibling.get("start"), sibling.get("end")
        if (
            context.source_start is not None and context.source_end is not None
            and start is not None and end is not None
        ):
            iou = _range_iou(
                context.source_start, context.source_end, float(start), float(end)
            )
            if iou >= DUPLICATE_IOU_WARN:
                return _check(
                    "duplicate", "warn",
                    f"Source range overlaps {label} by {iou:.0%} (IoU) — "
                    f"the same moment was likely picked twice",
                    duplicate_of=label, match="source_range",
                    iou=round(iou, 3),
                )
    return _check("duplicate", "pass", "No duplicate sibling clip detected")


# --- Subject in frame (warn-only) -------------------------------------------
#
# The reframe planner's modes that promise a framed subject. Fit shots show
# the whole frame by design (a face may legitimately be tiny or absent), so
# they are never sampled; this is the check the centre-crop era needed —
# it would have flagged every clip where the subject stood outside the
# middle of the frame. "plain" is the Plain — just cuts planner's mode: a
# full-bleed face-tracked crop, which promises the subject exactly like
# "single" does, so plain clips get the same framing verification.
_SUBJECT_EXPECTED_MODES = {"single", "pair", "split", "static", "plain"}
_SUBJECT_SAMPLE_FRACTIONS = (0.25, 0.50, 0.75)


def _detect_faces_bgr(frame: np.ndarray) -> Optional[list[tuple[float, float, float, float]]]:
    """Face boxes in a BGR frame, or None when no detector is available.

    YuNet first (the detector the reframing stack uses), Haar as its
    documented fallback. Kept as one module-level seam so tests can
    substitute detection results without real faces.
    """
    try:
        from .face_detection import open_haar_cascade, open_yunet_detector

        detector = open_yunet_detector()
        if detector is not None:
            return [tuple(map(float, box[:4])) for box in detector.detect(frame)]
        import cv2

        cascade = open_haar_cascade()
        if cascade is None or cascade.empty():
            return None
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        boxes = cascade.detectMultiScale(
            gray, scaleFactor=1.1, minNeighbors=5, minSize=(20, 20)
        )
        return [tuple(map(float, box)) for box in boxes]
    except Exception:
        return None


def check_subject_in_frame(
    path: Path, duration: float, context: ClipQcContext
) -> dict[str, Any]:
    """Verify sampled subject-framed moments actually show a face.

    Warn-only: returns pass / warn / skip, never fail, so it can never
    trigger the pipeline's automatic re-render. Skipped entirely (before
    any frame is decoded) for clips without a reframe plan and for sample
    points that land in fit-mode shots.
    """
    shots = [shot for shot in context.reframe_shots or [] if isinstance(shot, dict)]
    if not shots:
        return _check(
            "subject_in_frame", "skip",
            "No reframe plan recorded for this clip; framing not verified",
        )

    def mode_at(t: float) -> Optional[str]:
        for shot in shots:
            try:
                start, end = float(shot["start"]), float(shot["end"])
            except (TypeError, ValueError, KeyError):
                continue
            if start <= t < end or (shot is shots[-1] and t == end):
                return str(shot.get("mode") or "")
        return None

    sample_times = [
        duration * fraction
        for fraction in _SUBJECT_SAMPLE_FRACTIONS
        if mode_at(duration * fraction) in _SUBJECT_EXPECTED_MODES
    ]
    if not sample_times:
        return _check(
            "subject_in_frame", "skip",
            "Reframe plan frames no subject at the sample points "
            "(fit-mode shots only); framing not verified",
        )
    found = 0
    measured = 0
    for at_sec in sample_times:
        frame = _color_frame(path, at_sec)
        if frame is None:
            continue
        faces = _detect_faces_bgr(frame)
        if faces is None:
            return _check(
                "subject_in_frame", "skip",
                "No face detector available; framing not verified",
            )
        measured += 1
        if faces:
            found += 1
    if measured == 0:
        return _check(
            "subject_in_frame", "skip",
            "Could not extract frames to verify framing",
        )
    evidence = {"found": found, "measured": measured}
    if found / measured >= 2 / 3:
        return _check(
            "subject_in_frame", "pass",
            f"Subject face visible in {found}/{measured} sampled "
            "subject-framed moments",
            **evidence,
        )
    return _check(
        "subject_in_frame", "warn",
        f"Subject may be out of frame: face visible in only "
        f"{found}/{measured} sampled subject-framed moments",
        **evidence,
    )


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


LOUDNESS_TOLERANCE_LU = 2.0
_SENTENCE_END = (".", "?", "!")
_THOUGHT_PAUSE_SEC = 0.5


_OFF_VALUES = {"", "0", "off", "false", "no", "none"}


def qc_loudness_target() -> Optional[float]:
    """CLIP_QC_LOUDNESS_LUFS: the loudness target QC compares against.

    Default -14 (the short-form platforms' normalisation level). ``off``
    (or 0/false/no/none/empty) switches the loudness check off; a number
    such as ``-16`` changes the target.
    """
    raw = os.getenv("CLIP_QC_LOUDNESS_LUFS", "-14").strip().lower()
    if raw in _OFF_VALUES:
        return None
    try:
        return float(raw)
    except ValueError:
        return None


def qc_thought_boundaries_enabled() -> bool:
    """CLIP_QC_THOUGHT_BOUNDARIES (default on): store and check the words
    around each clip's start and end."""
    return os.getenv("CLIP_QC_THOUGHT_BOUNDARIES", "1").strip().lower() not in _OFF_VALUES


def transcript_boundary_words(
    video_path: Path, source_start: float, source_end: float, pad: float = 4.0
) -> list[dict[str, Any]]:
    """Words near a clip's start and end from the source's transcript cache.

    Returned in seconds as ``{"text", "start", "end"}`` -- only the words
    within ``pad`` seconds of either boundary, so the render-facts sidecar
    stays small. Empty when there is no cached transcript.
    """
    from .transcription import load_cached_transcript_data

    data = load_cached_transcript_data(Path(video_path)) or {}
    words: list[dict[str, Any]] = []
    for word in data.get("words") or []:
        try:
            start = float(word["start"]) / 1000.0
            end = float(word["end"]) / 1000.0
        except (KeyError, TypeError, ValueError):
            continue
        near_start = source_start - pad <= start <= source_start + pad
        near_end = source_end - pad <= end and start <= source_end + pad
        if near_start or near_end:
            words.append({
                "text": str(word.get("text", "")).strip(),
                "start": round(start, 3),
                "end": round(end, 3),
            })
    return words


def measure_integrated_lufs(path: Path) -> Optional[float]:
    """EBU R128 integrated loudness of the whole file (ffmpeg ebur128)."""
    import re as _re

    try:
        result = subprocess.run(
            ["ffmpeg", "-hide_banner", "-nostats", "-i", str(path), "-af", "ebur128=framelog=quiet", "-f", "null", "-"],
            capture_output=True, text=True, timeout=180,
        )
    except Exception:
        return None
    found = _re.findall(r"I:\s+(-?[\d.]+) LUFS", result.stderr or "")
    return float(found[-1]) if found else None


def check_loudness(path: Path, target: float, measured: Optional[float] = None) -> dict[str, Any]:
    """Warn when the finished clip is far from the platform loudness target."""
    value = measured if measured is not None else measure_integrated_lufs(path)
    if value is None:
        return _check("loudness", "skip", "Integrated loudness could not be measured")
    off = round(value - target, 1)
    if abs(off) <= LOUDNESS_TOLERANCE_LU:
        return _check("loudness", "pass", f"{value:.1f} LUFS (target {target:.0f})", lufs=value, target=target)
    side = "too quiet" if off < 0 else "too loud"
    return _check("loudness", "warn", f"{value:.1f} LUFS is {side} for the {target:.0f} LUFS target ({off:+.1f} LU)",
                  lufs=value, target=target)


def check_thought_boundaries(
    words: Sequence[dict[str, Any]], source_start: float, source_end: float
) -> dict[str, Any]:
    """Warn when the clip opens mid-sentence, ends mid-sentence or cuts a word.

    A clip "starts clean" when the word before it ends a sentence or a pause
    of >= 0.5 s precedes it; it "ends clean" when its last word ends a
    sentence or a pause follows it. Speech-free clips are skipped.
    """
    ordered = sorted(
        (w for w in words if isinstance(w, dict) and "start" in w and "end" in w),
        key=lambda w: float(w["start"]),
    )
    inside = [w for w in ordered if source_start - 0.05 <= float(w["start"]) < source_end]
    if not inside:
        return _check("thought_boundaries", "skip", "No speech inside the clip")
    first, last = inside[0], inside[-1]
    before = [w for w in ordered if float(w["start"]) < float(first["start"])]
    after = [w for w in ordered if float(w["start"]) >= source_end]
    starts_clean = (
        not before
        or str(before[-1].get("text", "")).rstrip().endswith(_SENTENCE_END)
        or float(first["start"]) - float(before[-1]["end"]) >= _THOUGHT_PAUSE_SEC
    )
    ends_clean = (
        str(last.get("text", "")).rstrip().endswith(_SENTENCE_END)
        or not after
        or float(after[0]["start"]) - float(last["end"]) >= _THOUGHT_PAUSE_SEC
    )
    cuts_word = any(float(w["start"]) < source_end < float(w["end"]) for w in ordered)
    evidence = {
        "starts_on_sentence": starts_clean, "ends_on_sentence": ends_clean, "cuts_a_word": cuts_word,
        "first_words": " ".join(str(w.get("text", "")) for w in inside[:5]),
        "last_words": " ".join(str(w.get("text", "")) for w in inside[-5:]),
    }
    if starts_clean and ends_clean and not cuts_word:
        return _check("thought_boundaries", "pass", "Starts and ends on a complete thought", **evidence)
    problems = [label for label, bad in (("opens mid-sentence", not starts_clean),
                                         ("ends mid-sentence", not ends_clean),
                                         ("cuts a word at the end", cuts_word)) if bad]
    return _check("thought_boundaries", "warn", "Clip " + ", ".join(problems), **evidence)


def run_clip_qc(path: Path, context: ClipQcContext) -> dict[str, Any]:
    """Run every check over one rendered clip file and build the report.

    Pure with respect to the database and the pipeline: the caller stores
    the returned report. Never raises for a bad file — a bad file is the
    input this function exists to describe.
    """
    path = Path(path)
    checked_at = datetime.now(timezone.utc).isoformat()
    if not path.is_file():
        return {
            "version": 1,
            "status": "fail",
            "hard_fail": True,
            "checked_at": checked_at,
            "file": {"path": str(path)},
            "checks": [
                _check("file", "fail", f"Rendered file is missing: {path}")
            ],
        }

    try:
        duration = ffprobe_duration(path)
    except Exception:
        duration = 0.0
    try:
        width, height = ffprobe_video_size(path)
    except Exception:
        width, height = 0, 0
    if duration <= 0 or width <= 0 or height <= 0:
        return {
            "version": 1,
            "status": "fail",
            "hard_fail": True,
            "checked_at": checked_at,
            "file": {"path": str(path), "duration": duration, "width": width, "height": height},
            "checks": [
                _check(
                    "file", "fail",
                    "File cannot be probed (corrupt or not a video): "
                    f"duration={duration}, size={width}x{height}",
                    duration=duration, width=width, height=height,
                )
            ],
        }

    file_sha256 = _sha256(path)
    audio_stream = select_audio_stream_index(path)
    levels: Optional[np.ndarray] = None
    if audio_stream is not None:
        try:
            levels = _window_levels_db(path, audio_stream, 0.0, duration)
        except Exception as exc:
            logger.warning("QC audio measurement failed for %s: %s", path, exc)
            levels = None

    checks = [
        _check(
            "file", "pass", "File exists and probes as a valid video",
            duration=round(duration, 3), width=width, height=height,
        ),
        check_duration(duration, context.expected_duration),
        check_resolution(width, height, context),
    ]
    audio_check, audio_silent = check_audio(levels, audio_stream is not None)
    checks.append(audio_check)
    if context.music_expected:
        checks.append(check_music(levels, context))
    checks.append(check_black_frames(path, duration))
    checks.append(check_frozen(path, duration, audio_silent))
    checks.append(check_duplicate(context, file_sha256))
    checks.append(check_subject_in_frame(path, duration, context))
    if context.loudness_target_lufs is not None and audio_stream is not None:
        checks.append(check_loudness(path, float(context.loudness_target_lufs)))
    if (
        context.transcript_words
        and context.source_start is not None
        and context.source_end is not None
    ):
        checks.append(check_thought_boundaries(
            context.transcript_words, float(context.source_start), float(context.source_end)
        ))
    # Quality Director per-niche compliance: hook window, cut-rate band,
    # caption presence + pacing. All warn-level — they flag and (for the
    # mechanically fixable ones) trigger the pipeline's one corrective
    # re-render; they never hard-fail a clip on their own.
    checks.append(check_hook_window(context))
    checks.append(check_cut_rate(context, duration))
    checks.extend(check_caption_pacing(context, audio_silent))
    # Beat density appears only when the render recorded it (the pass
    # is off by default), so reports for other renders are unchanged.
    if context.beat_density is not None:
        checks.append(check_beat_density(context))

    statuses = {entry["status"] for entry in checks}
    overall = "fail" if "fail" in statuses else "warn" if "warn" in statuses else "pass"
    fixable_reasons = [
        entry["name"]
        for entry in checks
        if (entry.get("evidence") or {}).get("fixable")
    ]
    return {
        "version": 1,
        "status": overall,
        "hard_fail": overall == "fail",
        "fixable": bool(fixable_reasons),
        "fixable_reasons": fixable_reasons,
        "checked_at": checked_at,
        "file": {
            "path": str(path),
            "sha256": file_sha256,
            "duration": round(duration, 3),
            "width": width,
            "height": height,
        },
        "checks": checks,
    }
