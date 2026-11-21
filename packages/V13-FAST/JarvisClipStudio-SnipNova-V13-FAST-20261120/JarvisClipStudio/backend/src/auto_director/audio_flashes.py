"""Audio-reactive flashes (Viral Scout Pack item 3).

Viral shorts punch the loudest moments of the audio with a 1-3 frame
brightness flash so a drop or a shouted punchline FEELS louder. This
module plans those flashes; the renderer (``media.edl_render``) turns
the plan into timed ``eq`` pulses on the joined picture.

What already existed, and what this module reuses (Step-0 premise
check, 2026-11-11):

* ``auto_director.beat_grid`` owns the audio analysis — spectral-flux
  onset DSP over a shared 16 kHz decode, 50 ms energy levels, beat
  tracking and drop detection — and its projected clip grid (beats,
  drop, levels on the clip timeline) already reaches ``build_edl``
  for the five beat-grid profiles. Detection here reads those levels;
  no second onset detector was written.
* What did NOT exist: any timed brightness effect (``fadewhite`` is
  only a crossfade name; "Flash In/Out" are speed ramps), any
  transient-event selection, and any route for the levels to reach
  non-beat profiles — ``prepare_clip_grid`` refuses a grid with no
  tempo, and speech transients live in the levels, not the beats.
  :func:`project_source_levels` / :func:`resolve_flash_grid` are that
  route for opted-in profiles without a beat grid.

Profile opt-in is a new additive section::

    "effects": {"audio_flashes": {"enabled": true, "intensity": 0.5}}

``intensity`` (0..1) scales the pulse's brightness/gamma. Detection
tuning (rise threshold, spacing, caps) lives in module constants; a
profile may override individual keys in the same dict. Profiles
without the section — every stock profile except gaming, meme_comedy,
sports, music_artist, football_edit and anime_funk_edit, and every
user/reference profile — produce exactly the EDL they produced
before. Plain mode needs no branch here beyond the stamp pop in
``plainify_edl``: the pipeline never resolves a flash grid for a
Plain task, and plainify strips the stamp regardless.

Restraint is the feature: flashes are sparse by construction
(strongest rises first, a minimum gap, a per-clip cap and a rolling
rate cap), the pulse is a punch — at full intensity +0.16 brightness
for 3 frames — never a white-out, and a flash whose window would hang
off either end of the clip is dropped rather than clipped.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)

# Detection defaults (the profile's audio_flashes dict may override
# min_rise_db / min_gap_sec / max_per_clip / duration_frames).
FLASH_MIN_RISE_DB = 5.0        # rise over the trailing baseline that counts as a hit
FLASH_BASELINE_WINDOWS = 6     # trailing windows (x 50 ms hop) forming the baseline
FLASH_LOUD_PERCENTILE = 55.0   # the hit itself must be at least this loud (clip-relative)
FLASH_MIN_LEVEL_DB = -30.0     # ...and at least this loud absolutely (dBFS)
FLASH_PEAK_LOOKAHEAD = 2       # windows after the rise to search for the hit's peak
FLASH_MIN_GAP_SEC = 0.8        # no two flashes closer than this — never a strobe
FLASH_MAX_PER_CLIP = 6         # hard per-clip cap
FLASH_RATE_WINDOW_SEC = 3.0    # rolling window for the rate cap
FLASH_RATE_MAX = 3             # at most this many flashes inside one rate window
FLASH_DROP_SNAP_SEC = 0.15     # a candidate this close to the drop IS the drop
FLASH_BEAT_SNAP_SEC = 0.06     # a point this close to a beat is labelled a beat hit
FLASH_DURATION_FRAMES = 3      # pulse length at the renderer's 30 fps (0.1 s)
FLASH_MIN_DURATION_FRAMES = 1
FLASH_MAX_DURATION_FRAMES = 5
DEFAULT_INTENSITY = 0.5
# Pulse amplitude at intensity 1.0 — a punch, not a white-out.
FLASH_MAX_BRIGHTNESS = 0.16
FLASH_MAX_GAMMA_LIFT = 0.30


def profile_flash_config(
    profile: Optional[dict[str, Any]],
) -> Optional[dict[str, Any]]:
    """The profile's normalised audio-flash config, or None.

    Reads ``effects.audio_flashes``. Anything absent, disabled,
    malformed or zero-intensity returns None and the clip simply gets
    no flashes — a hand-built or learned profile can never break the
    EDL by carrying a junk section.
    """
    if not isinstance(profile, dict):
        return None
    effects = profile.get("effects")
    if not isinstance(effects, dict):
        return None
    raw = effects.get("audio_flashes")
    if not isinstance(raw, dict) or not raw.get("enabled"):
        return None

    def _float(key: str, default: float) -> float:
        try:
            return float(raw.get(key, default))
        except (TypeError, ValueError):
            return default

    intensity = min(1.0, max(0.0, _float("intensity", DEFAULT_INTENSITY)))
    if intensity <= 0:
        return None
    min_rise_db = min(20.0, max(1.0, _float("min_rise_db", FLASH_MIN_RISE_DB)))
    min_gap_sec = min(5.0, max(0.2, _float("min_gap_sec", FLASH_MIN_GAP_SEC)))
    try:
        max_per_clip = int(raw.get("max_per_clip", FLASH_MAX_PER_CLIP))
    except (TypeError, ValueError):
        max_per_clip = FLASH_MAX_PER_CLIP
    max_per_clip = min(30, max(1, max_per_clip))
    try:
        duration_frames = int(raw.get("duration_frames", FLASH_DURATION_FRAMES))
    except (TypeError, ValueError):
        duration_frames = FLASH_DURATION_FRAMES
    duration_frames = min(
        FLASH_MAX_DURATION_FRAMES,
        max(FLASH_MIN_DURATION_FRAMES, duration_frames),
    )
    return {
        "intensity": round(intensity, 3),
        "brightness": round(FLASH_MAX_BRIGHTNESS * intensity, 4),
        "gamma": round(1.0 + FLASH_MAX_GAMMA_LIFT * intensity, 4),
        "min_rise_db": min_rise_db,
        "min_gap_sec": min_gap_sec,
        "max_per_clip": max_per_clip,
        "duration_frames": duration_frames,
    }


def _percentile(values: list[float], pct: float) -> float:
    """Small deterministic percentile (linear interpolation)."""
    if not values:
        return 0.0
    ordered = sorted(values)
    if len(ordered) == 1:
        return float(ordered[0])
    rank = (pct / 100.0) * (len(ordered) - 1)
    low = int(rank)
    high = min(low + 1, len(ordered) - 1)
    frac = rank - low
    return float(ordered[low] * (1.0 - frac) + ordered[high] * frac)


def detect_flash_points(
    levels_db: list[float],
    hop_sec: float,
    *,
    duration: float,
    beats: Optional[list[float]] = None,
    drop_time: Optional[float] = None,
    config: Optional[dict[str, Any]] = None,
) -> list[dict[str, Any]]:
    """Pick the sparse flash points from a clip's 50 ms level curve.

    A candidate is a window whose level jumps at least
    ``min_rise_db`` above the median of the trailing
    ``FLASH_BASELINE_WINDOWS`` windows AND lands at or above the
    clip's loud percentile and the absolute ``FLASH_MIN_LEVEL_DB``
    floor — a sharp rise into still-quiet audio is a click, not a
    hit. The flash centre is the loudest window within
    ``FLASH_PEAK_LOOKAHEAD`` windows after the rise (the hit's peak,
    not its attack edge). The beat grid's drop, when it lands in the
    clip, is always a candidate: a nearby detection takes the
    ``drop`` label, and an undetected drop is added on its own
    evidence (``find_drop_time`` only fires on a >= 4 dB burst into a
    loud payoff). Selection is strongest-first under the spacing,
    per-clip and rolling-rate caps, so a dense passage yields its few
    biggest hits instead of a strobe.

    Returns ``[{at_sec, source, rise_db}]`` sorted by time, where
    source is ``drop`` / ``beat`` / ``transient``. Pure: same inputs,
    same points.
    """
    cfg = config or {}
    min_rise = float(cfg.get("min_rise_db", FLASH_MIN_RISE_DB))
    min_gap = float(cfg.get("min_gap_sec", FLASH_MIN_GAP_SEC))
    max_per_clip = int(cfg.get("max_per_clip", FLASH_MAX_PER_CLIP))
    try:
        duration_frames = int(cfg.get("duration_frames", FLASH_DURATION_FRAMES))
    except (TypeError, ValueError):
        duration_frames = FLASH_DURATION_FRAMES
    if (
        not levels_db
        or hop_sec <= 0
        or duration <= 0
        or len(levels_db) < FLASH_BASELINE_WINDOWS + 2
    ):
        return []
    levels = [float(v) for v in levels_db]
    loud_floor = max(
        _percentile(levels, FLASH_LOUD_PERCENTILE), FLASH_MIN_LEVEL_DB
    )
    half_window = (duration_frames * (1.0 / 30.0)) / 2.0

    def _usable(at_sec: float) -> bool:
        # The pulse must fit inside the clip: a window hanging off an
        # edge would flash a partial frame at the boundary.
        return half_window <= at_sec <= duration - half_window

    candidates: dict[int, float] = {}  # peak window index -> rise dB
    for i in range(FLASH_BASELINE_WINDOWS, len(levels)):
        baseline_windows = levels[i - FLASH_BASELINE_WINDOWS:i]
        baseline = sorted(baseline_windows)[len(baseline_windows) // 2]
        rise = levels[i] - baseline
        if rise < min_rise or levels[i] < loud_floor:
            continue
        peak = i
        for j in range(i + 1, min(len(levels), i + FLASH_PEAK_LOOKAHEAD + 1)):
            if levels[j] > levels[peak]:
                peak = j
        # One candidate per peak: a sustained loud passage re-triggers
        # on every window of its attack, but it is one hit.
        if peak not in candidates or rise > candidates[peak]:
            candidates[peak] = rise

    def _at(window_index: int) -> float:
        return (window_index + 0.5) * hop_sec

    ranked = sorted(candidates.items(), key=lambda item: (-item[1], item[0]))

    drop_index: Optional[int] = None
    if drop_time is not None and 0.0 <= float(drop_time) <= duration:
        drop_t = float(drop_time)
        near = [idx for idx in candidates if abs(_at(idx) - drop_t) <= FLASH_DROP_SNAP_SEC]
        if near:
            drop_index = max(near, key=lambda idx: candidates[idx])
        else:
            # The window containing the drop (and its successor —
            # the burst can straddle the boundary): whichever shows
            # the larger measured rise speaks for the drop.
            first = min(len(levels) - 1, max(0, int(drop_t / hop_sec)))
            options = [first] + ([first + 1] if first + 1 < len(levels) else [])
            idx = max(options, key=lambda j: _drop_rise(levels, j))
            measured = _drop_rise(levels, idx)
            # The drop earns its own flash only on a real local
            # burst — find_drop_time's own bar is 4 dB on the track
            # timeline; the clip-window read can land lower when the
            # burst straddles the window edge, so the floor is 2 dB.
            if measured >= 2.0 and _usable(_at(idx)):
                drop_index = idx
                ranked = [(idx, measured)] + [
                    item for item in ranked if item[0] != idx
                ]

    accepted: list[tuple[int, float]] = []
    for idx, rise in ranked:
        at = _at(idx)
        if not _usable(at):
            continue
        if any(abs(at - _at(other)) < min_gap for other, _ in accepted):
            continue
        # Rolling rate cap, exact under strongest-first selection:
        # after adding this point, no 3 s window may hold more than
        # FLASH_RATE_MAX flashes. Checking the windows that start at
        # each accepted point (and at this one) covers every window
        # whose membership would change.
        trial = [_at(other) for other, _ in accepted] + [at]
        if any(
            sum(1 for t in trial if start <= t < start + FLASH_RATE_WINDOW_SEC)
            > FLASH_RATE_MAX
            for start in trial
        ):
            continue
        accepted.append((idx, rise))
        if len(accepted) >= max_per_clip:
            break

    beat_list = [float(b) for b in beats or []]
    points: list[dict[str, Any]] = []
    for idx, rise in sorted(accepted, key=lambda item: _at(item[0])):
        at = _at(idx)
        if idx == drop_index:
            source = "drop"
        elif any(abs(at - b) <= FLASH_BEAT_SNAP_SEC for b in beat_list):
            source = "beat"
        else:
            source = "transient"
        points.append({
            "at_sec": round(at, 3),
            "source": source,
            "rise_db": round(float(rise), 1),
        })
    return points


def _drop_rise(levels: list[float], idx: int) -> float:
    """The measured rise into the drop window (baseline -> peak)."""
    start = max(0, idx - FLASH_BASELINE_WINDOWS)
    if idx <= start:
        return 0.0
    baseline_windows = levels[start:idx]
    baseline = sorted(baseline_windows)[len(baseline_windows) // 2]
    return float(levels[idx]) - baseline


def plan_audio_flashes(
    profile: Optional[dict[str, Any]],
    grid: Optional[dict[str, Any]],
    clip_duration: float,
) -> Optional[dict[str, Any]]:
    """The EDL's ``audio_flashes`` stamp (minus rendered times).

    ``grid`` is a clip-projected grid — the beat grid ``build_edl``
    already received, or the source projection from
    :func:`resolve_flash_grid` — carrying ``energy_levels_db`` at
    ``energy_hop_sec`` plus optional ``beat_times`` / ``drop_time``.
    Returns None when the profile does not opt in, the grid carries
    no usable levels, or no point survives detection: in every such
    case the EDL is exactly what it was without this feature.
    """
    cfg = profile_flash_config(profile)
    if cfg is None or not grid or clip_duration <= 0:
        return None
    levels = grid.get("energy_levels_db") or grid.get("energy_levels") or []
    try:
        hop = float(grid.get("energy_hop_sec") or 0.05)
    except (TypeError, ValueError):
        hop = 0.05
    if not levels:
        return None
    points = detect_flash_points(
        [float(v) for v in levels],
        hop,
        duration=float(clip_duration),
        beats=[float(b) for b in grid.get("beat_times") or []],
        drop_time=(
            float(grid["drop_time"]) if grid.get("drop_time") is not None else None
        ),
        config=cfg,
    )
    if not points:
        return None
    return {
        "points": points,
        "intensity": cfg["intensity"],
        "brightness": cfg["brightness"],
        "gamma": cfg["gamma"],
        "duration_frames": cfg["duration_frames"],
        "grid_source": grid.get("source") or "source",
    }


def project_source_levels(
    analysis: Optional[dict[str, Any]],
    *,
    clip_start: float,
    clip_duration: float,
) -> Optional[dict[str, Any]]:
    """Project a whole-source beat analysis onto one clip, levels-first.

    Unlike ``beat_grid.prepare_clip_grid`` this keeps the energy
    curve even when the source has no detectable tempo: flashes read
    the levels, and speech-led clips (the meme_comedy case) may have
    no beat grid at all. Beats and the drop ride along when they land
    inside the window, purely as labels for the stamp.
    """
    if not analysis or clip_duration <= 0:
        return None
    curve = analysis.get("energy_curve") or {}
    try:
        hop = float(curve.get("hop_sec") or 0.05)
    except (TypeError, ValueError):
        hop = 0.05
    levels = list(curve.get("levels_db") or [])
    if len(levels) < FLASH_BASELINE_WINDOWS + 2:
        return None
    end = clip_start + clip_duration
    i0 = max(0, int(clip_start / hop))
    i1 = min(len(levels), int(end / hop) + 1)
    window_levels = levels[i0:i1]
    if len(window_levels) < FLASH_BASELINE_WINDOWS + 2:
        return None
    beats = [
        round(float(b) - clip_start, 3)
        for b in analysis.get("beat_times") or []
        if clip_start - hop <= float(b) <= end + hop
    ]
    drop: Optional[float] = None
    raw_drop = analysis.get("drop_time")
    if raw_drop is not None and clip_start <= float(raw_drop) <= end:
        drop = round(float(raw_drop) - clip_start, 3)
    return {
        "energy_levels_db": window_levels,
        "energy_hop_sec": hop,
        "beat_times": beats,
        "drop_time": drop,
        "source": "source",
    }


def resolve_flash_grid(
    profile: Optional[dict[str, Any]],
    *,
    source_path: Optional[Path],
    clip_start: float,
    clip_duration: float,
) -> Optional[dict[str, Any]]:
    """The pipeline seam's flash grid: analyse the source, project it.

    Mirrors ``beat_grid.resolve_clip_grid``'s contract — best-effort,
    cached analysis (``analyse_audio_beats`` sidecar), and ANY failure
    returns None so the EDL is exactly what it was. Only called for
    profiles that opt into flashes and have no beat grid; Plain tasks
    never call it at all.
    """
    if profile_flash_config(profile) is None or source_path is None:
        return None
    try:
        from .beat_grid import analyse_audio_beats

        analysis = analyse_audio_beats(Path(source_path))
        return project_source_levels(
            analysis, clip_start=clip_start, clip_duration=clip_duration
        )
    except Exception as exc:
        logger.warning("Flash grid unavailable for this clip: %s", exc)
        return None
