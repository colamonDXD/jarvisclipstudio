"""Visual moment selection for sources with no usable transcript.

Anime edits, music-only videos and montage sources have no words to
analyse, so the transcript-driven highlight picker has nothing to
rank and the pipeline used to fall back to one blind window at the
start of the file. This module picks candidate moments from what
those sources DO have: scene cuts, audio energy peaks, and the beat
grid (auto_director.beat_grid) — the drop, the loudest stretches,
the cuts into them.

Every selected moment carries its reason and the exact timestamp it
came from, in the pipeline's segment shape, so downstream stages
(ClipPlans, QC, the dashboard) treat them like any other segment.
Deterministic: the same source always yields the same slate.
"""

from __future__ import annotations

import logging
import re
import subprocess
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)

_SCENE_CUT_THRESHOLD = 0.4
_PEAK_MIN_SEPARATION_SEC = 2.0
_SNAP_TO_CUT_WINDOW_SEC = 1.25
_SNAP_TO_BEAT_WINDOW_SEC = 0.35
_MAX_OVERLAP_FRACTION = 0.25

# Peak scoring blends absolute level with LOCAL PROMINENCE: how far a
# peak stands above the median level of its own surroundings (±60 s,
# computed over 1-second block means). A reaction burst in a quiet
# stretch is a moment even when a sustained loud scene elsewhere in
# the file is louder in absolute terms; ranking by file-wide loudness
# alone fills the slate with the file's loudest scenes and misses
# exactly those bursts (measured: the deterministic floor scored
# recall 0.00 on the labelled Japan source before this blend).
_PROMINENCE_HALF_WINDOW_SEC = 60.0
# A peak whose level decays quickly (the mean level over the 10 s
# starting 2 s after it sits far below the peak) is a CLIMAX — a
# laugh, a reaction, an impact — so its window is anchored late and
# the clip ends just after the payoff. A peak that sustains is a
# BEGINNING (a drop, a chorus) and keeps the classic anchor.
_DECAY_SPAN_START_SEC = 2.0
_DECAY_SPAN_SEC = 10.0
_DECAY_THRESHOLD_DB = 6.0
_LATE_ANCHOR_FRACTION = 0.92
_DEFAULT_ANCHOR_FRACTION = 0.4
# A scene cut preceded by a long shot is a genuine scene transition —
# the kind of boundary moments start at — unlike the rapid camera
# cuts inside one scene. The boundary bonus rewards that, capped so
# it can nudge a ranking, never dominate it.
_BOUNDARY_BONUS_CAP_SEC = 20.0
# A cut candidate borrows the score of the strongest peak inside the
# window it opens, minus this offset: the peak's own anchored window
# usually frames that moment better, so the cut yields to it.
_CUT_PEAK_SCORE_OFFSET = 8.0


def _mmss(seconds: float) -> str:
    total = max(0, int(round(seconds)))
    return f"{total // 60:02d}:{total % 60:02d}"


def detect_scene_cut_times(
    video_path: Path | str, threshold: float = _SCENE_CUT_THRESHOLD
) -> list[float]:
    """Scene-cut timestamps via ffmpeg's scene score (showinfo).

    Mirrors media.reframing.count_scene_cuts, but keeps the times.
    Any failure returns an empty list — selection degrades to energy
    peaks alone rather than failing.
    """
    try:
        result = subprocess.run(
            [
                "ffmpeg",
                "-i",
                str(video_path),
                "-filter:v",
                f"select='gt(scene,{threshold})',showinfo",
                "-f",
                "null",
                "-",
            ],
            capture_output=True,
            text=True,
            timeout=300,
        )
    except Exception:
        logger.warning("Scene-cut detection failed for %s", video_path, exc_info=True)
        return []
    if result.returncode != 0:
        return []
    return sorted(
        float(match) for match in re.findall(r"pts_time:([0-9.]+)", result.stderr)
    )


def _energy_peaks_from_grid(
    beat_grid: dict[str, Any],
) -> list[tuple[float, float]]:
    """(time, level_db) local maxima of the grid's energy curve."""
    curve = beat_grid.get("energy_curve") or {}
    levels = curve.get("levels_db") or []
    hop = float(curve.get("hop_sec") or 0.05)
    if len(levels) < 3:
        return []
    ordered = sorted(levels)
    floor = ordered[int(0.7 * (len(ordered) - 1))]
    peaks: list[tuple[float, float]] = []
    for i in range(1, len(levels) - 1):
        if levels[i] < floor or levels[i] < levels[i - 1] or levels[i] <= levels[i + 1]:
            continue
        time = i * hop
        if peaks and time - peaks[-1][0] < _PEAK_MIN_SEPARATION_SEC:
            if levels[i] > peaks[-1][1]:
                peaks[-1] = (time, float(levels[i]))
            continue
        peaks.append((time, float(levels[i])))
    return peaks


def _energy_at(beat_grid: Optional[dict[str, Any]], time: float) -> Optional[float]:
    if not beat_grid:
        return None
    curve = beat_grid.get("energy_curve") or {}
    levels = curve.get("levels_db") or []
    hop = float(curve.get("hop_sec") or 0.05)
    if not levels:
        return None
    index = min(len(levels) - 1, max(0, int(time / hop)))
    return float(levels[index])


def _curve_levels(beat_grid: Optional[dict[str, Any]]):
    """(levels, hop) of the grid's energy curve, or (None, 0.05)."""
    if not beat_grid:
        return None, 0.05
    curve = beat_grid.get("energy_curve") or {}
    levels = curve.get("levels_db") or []
    if len(levels) < 3:
        return None, 0.05
    return [float(x) for x in levels], float(curve.get("hop_sec") or 0.05)


def _block_means(levels: list[float], hop: float) -> list[float]:
    """Mean level per 1-second block — the cheap robust baseline grid
    the prominence measure reads (a median over these blocks resists
    the bursts themselves, unlike a plain mean)."""
    per_block = max(1, int(round(1.0 / hop)))
    return [
        sum(levels[i:i + per_block]) / len(levels[i:i + per_block])
        for i in range(0, len(levels), per_block)
    ]


def _prominence_db(
    block_means: list[float], time: float, level: float
) -> Optional[float]:
    """How far ``level`` stands above the median block level within
    ±``_PROMINENCE_HALF_WINDOW_SEC`` of ``time``. None when there is
    no baseline to compare against."""
    if not block_means:
        return None
    half = int(_PROMINENCE_HALF_WINDOW_SEC)
    centre = int(time)
    window = block_means[max(0, centre - half): centre + half + 1]
    if not window:
        return None
    ordered = sorted(window)
    median = ordered[len(ordered) // 2]
    return level - median


def _decay_db(
    levels: list[float], hop: float, time: float, level: float
) -> Optional[float]:
    """Peak level minus the mean level over the span starting
    ``_DECAY_SPAN_START_SEC`` after the peak. High decay = the burst
    ends the moment; low/negative = the energy sustains. None when
    the curve does not extend far enough past the peak to tell."""
    start = int((time + _DECAY_SPAN_START_SEC) / hop)
    end = int((time + _DECAY_SPAN_START_SEC + _DECAY_SPAN_SEC) / hop)
    span = levels[start:end]
    if len(span) < max(1, int(0.5 * _DECAY_SPAN_SEC / hop)):
        return None
    return level - (sum(span) / len(span))


def select_visual_moments(
    video_path: Path | str,
    duration: float,
    *,
    target_count: int,
    clip_seconds: float,
    beat_grid: Optional[dict[str, Any]] = None,
    scene_cuts: Optional[list[float]] = None,
    energy_peaks: Optional[list[tuple[float, float]]] = None,
    reason_context: str = "no usable transcript",
    frame_evidence: bool = False,
) -> list[dict[str, Any]]:
    """Pick up to ``target_count`` clip windows without a transcript.

    With ``frame_evidence=True`` the picture is read first
    (``gameplay_moments``): moments come from several on-screen signals
    agreeing, with audio only supporting them. Audio peaks and scene cuts
    below are then used only when the frames yield nothing — a loud sound
    with no visible event never outranks a real on-screen moment.

    Candidates, strongest first: the beat grid's drop, audio energy
    peaks (scored by loudness blended with local prominence, and
    anchored by shape — a decaying burst ends its clip, a sustained
    peak starts it), then scene cuts, scored by the strongest content
    inside the window they open plus a bonus for cuts that genuinely
    open a new scene. Windows are snapped to a nearby scene cut and
    then to a nearby beat — but only while the snap keeps the
    candidate moment inside the window — and rejected if they mostly
    duplicate an already-accepted window.

    ``reason_context`` names WHY the deterministic floor is selecting
    (no usable transcript, or the AI analysis was unavailable); it is
    stamped into every moment's reasoning so a degraded selection can
    never masquerade as an AI pick.
    """
    duration = float(duration or 0)
    if duration <= 0 or target_count <= 0:
        return []
    clip_seconds = max(1.0, min(float(clip_seconds or 30), duration))
    if frame_evidence:
        from .gameplay_moments import select_gameplay_moments
        from .reaction_moments import select_streamer_moments

        streamer = select_streamer_moments(
            video_path,
            duration,
            target_count=target_count,
            clip_seconds=clip_seconds,
            reason_context=reason_context,
        )
        streamer = [m for m in streamer if m.get("hook_type") != "silent_action"]
        if streamer:
            return streamer

        fused = select_gameplay_moments(
            video_path,
            duration,
            target_count=target_count,
            clip_seconds=clip_seconds,
            beat_grid=beat_grid,
            reason_context=reason_context,
        )
        if fused:
            return fused
    if scene_cuts is None:
        scene_cuts = detect_scene_cut_times(video_path)
    if energy_peaks is None and beat_grid is not None:
        energy_peaks = _energy_peaks_from_grid(beat_grid)
    energy_peaks = energy_peaks or []
    scene_cuts = sorted(float(c) for c in scene_cuts or [])

    levels, hop = _curve_levels(beat_grid)
    block_means = _block_means(levels, hop) if levels is not None else []

    candidates: list[dict[str, Any]] = []
    # (time, score, level) of every peak-like event, for cut scoring.
    content_peaks: list[tuple[float, float, float]] = []
    drop = beat_grid.get("drop_time") if beat_grid else None
    if drop is not None and 0 < float(drop) < duration:
        drop_level = _energy_at(beat_grid, float(drop))
        candidates.append({
            "time": float(drop),
            "score": 100.0,
            "anchor": _DEFAULT_ANCHOR_FRACTION,
            "reason": (
                f"the track's drop lands at {_mmss(float(drop))} — the "
                "strongest energy jump in the source"
            ),
        })
        content_peaks.append(
            (float(drop), 100.0, drop_level if drop_level is not None else 0.0)
        )
    if energy_peaks:
        peak_levels = [level for _, level in energy_peaks]
        low, high = min(peak_levels), max(peak_levels)
        span = max(1e-6, high - low)
        prominences: list[Optional[float]] = [
            _prominence_db(block_means, float(time), float(level))
            for time, level in energy_peaks
        ]
        known_prom = [p for p in prominences if p is not None]
        prom_low = min(known_prom) if known_prom else 0.0
        prom_span = max(1e-6, (max(known_prom) - prom_low)) if known_prom else 1e-6
        for (time, level), prominence in zip(energy_peaks, prominences):
            time, level = float(time), float(level)
            if drop is not None and abs(time - float(drop)) < _PEAK_MIN_SEPARATION_SEC:
                continue
            if prominence is not None:
                score = (
                    50.0
                    + 20.0 * (level - low) / span
                    + 20.0 * (prominence - prom_low) / prom_span
                )
                reason = (
                    f"audio energy peak at {_mmss(time)} "
                    f"({level:.1f} dB, standing {prominence:.1f} dB "
                    "above its surroundings)"
                )
            else:
                score = 50.0 + 40.0 * (level - low) / span
                reason = (
                    f"audio energy peak at {_mmss(time)} "
                    f"({level:.1f} dB, among the loudest stretches)"
                )
            decay = (
                _decay_db(levels, hop, time, level) if levels is not None else None
            )
            anchor = (
                _LATE_ANCHOR_FRACTION
                if decay is not None and decay >= _DECAY_THRESHOLD_DB
                else _DEFAULT_ANCHOR_FRACTION
            )
            candidates.append({
                "time": time,
                "score": score,
                "anchor": anchor,
                "reason": reason,
            })
            content_peaks.append((time, score, level))
    for index, cut in enumerate(scene_cuts):
        prev_gap = cut - scene_cuts[index - 1] if index > 0 else cut
        boundary = min(prev_gap, _BOUNDARY_BONUS_CAP_SEC)
        following = [
            (score, level)
            for t, score, level in content_peaks
            if cut <= t < cut + clip_seconds
        ]
        if following:
            best_score, best_level = max(following)
            # The boundary term is a nudge among near-equal cuts: at
            # 0.25x (max +5) it can never lift a cut above the peak
            # whose content it borrows (offset 8) — a cut is weaker
            # evidence for that moment than the peak itself.
            score = max(30.0, best_score - _CUT_PEAK_SCORE_OFFSET) + 0.25 * boundary
            reason = (
                f"scene cut at {_mmss(cut)} into an energetic stretch "
                f"(peak {best_level:.1f} dB inside the window)"
            )
        elif content_peaks:
            score = 30.0 + 0.5 * boundary
            reason = f"scene cut at {_mmss(cut)}"
        else:
            # No energy signal at all: the only structure left is the
            # edit itself. A cut after a long shot opens a new scene —
            # rank those first instead of marching from the file start.
            score = 30.0 + 2.0 * boundary
            reason = (
                f"scene cut at {_mmss(cut)} opening a new scene "
                f"(previous shot lasted {prev_gap:.0f}s)"
            )
        candidates.append({
            "time": cut,
            "score": score,
            "anchor": 0.0,
            "reason": reason,
        })

    beats = [float(b) for b in (beat_grid or {}).get("beat_times") or []]
    accepted: list[tuple[float, float]] = []
    moments: list[dict[str, Any]] = []
    for candidate in sorted(candidates, key=lambda c: (-c["score"], c["time"])):
        start = candidate["time"] - candidate["anchor"] * clip_seconds
        start = max(0.0, min(start, duration - clip_seconds))
        if scene_cuts:
            near_cut = min(scene_cuts, key=lambda c: abs(c - start))
            if abs(near_cut - start) <= _SNAP_TO_CUT_WINDOW_SEC:
                snapped = max(0.0, min(near_cut, duration - clip_seconds))
                # A snap must never push the candidate moment itself
                # outside the window it named.
                if snapped <= candidate["time"] <= snapped + clip_seconds:
                    start = snapped
        if beats:
            near_beat = min(beats, key=lambda b: abs(b - start))
            if abs(near_beat - start) <= _SNAP_TO_BEAT_WINDOW_SEC:
                snapped = max(0.0, min(near_beat, duration - clip_seconds))
                if snapped <= candidate["time"] <= snapped + clip_seconds:
                    start = snapped
        end = start + clip_seconds
        overlaps = any(
            min(end, a_end) - max(start, a_start)
            > _MAX_OVERLAP_FRACTION * clip_seconds
            for a_start, a_end in accepted
        )
        if overlaps:
            continue
        accepted.append((start, end))
        score = candidate["score"]
        moments.append({
            "start_time": _mmss(start),
            "end_time": _mmss(end),
            "text": "",
            "relevance_score": round(min(0.95, score / 100.0), 2),
            "reasoning": (
                f"Visual selection ({reason_context}): {candidate['reason']}; "
                f"window {_mmss(start)}–{_mmss(end)}."
            ),
            "virality_score": int(round(score)),
            "hook_score": int(round(score * 0.9)),
            "engagement_score": int(round(score * 0.7)),
            "value_score": 0,
            "shareability_score": int(round(score * 0.6)),
            "hook_type": "visual_energy",
            "hook_title": None,
        })
        if len(moments) >= target_count:
            break
    moments.sort(key=lambda m: m["start_time"])
    return moments
