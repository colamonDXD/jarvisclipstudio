"""View sequencing for streamer clips: the layout follows the moment.

One layout for the whole clip is the amateur tell (viral comparison,
2026-11-20, Pattern #13): around a big moment, pro streamer edits
*sequence* the views —

    SPLIT (setup) → GAMEPLAY-FULL (the cause, just before the peak)
    → FACE-FULL (the reaction peak) → SPLIT (settle)

This module owns the decision: whether sequencing is on
(``VIEW_SEQUENCING``, default off), which niches it applies to, where
the clip's peak is, and the segment table the layout builder consumes.
The graph itself is built in ``media.gaming_templates``
(``build_sequenced_stack_layout``) from the existing stack crops — this
module never touches pixels.

Peak resolution, in order:
1. the peak the moment/fusion pipeline recorded on the clip's EDL
   (``peak_from_edl`` — the same carriers ``deliverable_bundle`` reads);
2. the clip file's own loudest-reaction second, measured with the live
   pipeline's voice/shout series (``live.signals`` per-half-second RMS,
   overall and >1 kHz, scored against the clip's own median/MAD
   baseline exactly like ``live.signals.AudioTrack.zscores``).
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional

logger = logging.getLogger(__name__)

# The cause: gameplay fills the frame for this long before the peak, so a
# stranger sees what the streamer is reacting to.
GAME_FULL_LEAD_S = 3.0
# The reaction: the face fills the frame for this long from the peak.
FACE_FULL_TAIL_S = 4.0
# A full-view segment shorter than this is not worth a cut; it folds back
# into the surrounding split view.
MIN_VIEW_SEGMENT_S = 1.0
# Below this the sequenced shape cannot breathe; the clip keeps one layout.
MIN_SEQUENCE_DURATION_S = 10.0
# The winning half-second must be at least this much louder than the
# clip's own median before it is called a reaction peak. The clip was
# already picked as containing a reaction; the fallback only locates
# its loudest second, and the lift is what separates a peak from a
# flat track (a constant tone, constant loudness: lift ~0, no peak).
# Measured on the streamer fixture's R3 window: the scream bucket sits
# 5.7 dB over the window median — real speech content, not the 6+ dB
# of a synthetic burst, so the bar sits at 3 dB.
PEAK_MIN_LIFT_DB = 3.0

SEQUENCED_NICHES = frozenset({"gaming", "streamer"})

VIEW_SPLIT = "split"
VIEW_GAME_FULL = "game_full"
VIEW_FACE_FULL = "face_full"


def view_sequencing_enabled() -> bool:
    """VIEW_SEQUENCING (default off): sequence views around the peak."""
    return os.getenv("VIEW_SEQUENCING", "off").strip().lower() in ("on", "1", "true", "yes")


def niche_is_sequenced(niche: Optional[str]) -> bool:
    """Sequencing is a streamer-layout behaviour: gaming/streamer only."""
    return isinstance(niche, str) and niche.strip().lower() in SEQUENCED_NICHES


def plan_view_segments(duration: Optional[float], peak: Optional[float]) -> Optional[List[Dict[str, Any]]]:
    """The layout timeline for one clip, or None to keep a single layout.

    Segments tile ``[0, duration]`` exactly and are clip-relative:
    split ``[0, peak-lead)``, game-full ``[peak-lead, peak)``, face-full
    ``[peak, peak+tail)``, split ``[peak+tail, duration]`` — clamped to
    the clip, with any full-view segment shorter than
    ``MIN_VIEW_SEGMENT_S`` folded back into the split. The face-full
    segment is the point of the sequence: without it there is no
    sequence. Duration is never changed, so the 30 s floor and every
    other duration rule upstream are untouched.
    """
    if duration is None or peak is None:
        return None
    try:
        duration, peak = float(duration), float(peak)
    except (TypeError, ValueError):
        return None
    if duration < MIN_SEQUENCE_DURATION_S or not 0.0 <= peak <= duration:
        return None
    game_start = max(0.0, peak - GAME_FULL_LEAD_S)
    face_end = min(duration, peak + FACE_FULL_TAIL_S)
    raw = [
        (VIEW_SPLIT, 0.0, game_start),
        (VIEW_GAME_FULL, game_start, peak),
        (VIEW_FACE_FULL, peak, face_end),
        (VIEW_SPLIT, face_end, duration),
    ]
    folded = [
        (view if view == VIEW_SPLIT or end - start >= MIN_VIEW_SEGMENT_S else VIEW_SPLIT, start, end)
        for view, start, end in raw
    ]
    segments: List[Dict[str, Any]] = []
    for view, start, end in folded:
        if end - start <= 1e-9:
            continue
        if segments and segments[-1]["view"] == view and abs(segments[-1]["end"] - start) < 1e-9:
            segments[-1]["end"] = end
        else:
            segments.append({"view": view, "start": start, "end": end})
    if len(segments) < 2 or not any(s["view"] == VIEW_FACE_FULL for s in segments):
        return None
    for segment in segments:
        segment["start"] = round(segment["start"], 3)
        segment["end"] = round(segment["end"], 3)
    return segments


def peak_from_edl(edl: Optional[Mapping[str, Any]]) -> Optional[float]:
    """The clip-relative peak already represented by an EDL, or None.

    Reads the same carriers as ``deliverable_bundle._peak_timestamp``:
    an explicit peak stamp, the energy-peak list, or the first punch-in
    zoom keyframe (which ``build_edl`` derives from the energy peaks).
    """
    if not edl:
        return None
    for key in ("peak_energy_timestamp", "peak_energy_time"):
        value = edl.get(key)
        if isinstance(value, (int, float)) and value >= 0:
            return float(value)
    peaks = edl.get("energy_peaks")
    if isinstance(peaks, list):
        for peak in peaks:
            if isinstance(peak, (int, float)) and peak >= 0:
                return float(peak)
    for keyframe in edl.get("zoom_keyframes") or []:
        if (
            isinstance(keyframe, Mapping)
            and keyframe.get("type") == "punch_in"
            and isinstance(keyframe.get("time"), (int, float))
        ):
            return max(0.0, float(keyframe["time"]))
    return None


def loudest_reaction_second(path: Path) -> Optional[float]:
    """The clip's loudest-reaction second, from its own audio.

    Uses the live pipeline's voice/shout measurement
    (``live.signals._levels``: per-half-second RMS overall and in the
    >1 kHz band shouting pushes up), scored against the clip's own
    median/MAD baseline — the ``AudioTrack.zscores`` measure, taken over
    the whole clip instead of a rolling window. Returns the winning
    bucket's centre, or None when nothing stands out (a flat clip has
    no peak worth sequencing) or the measurement is unavailable.
    """
    try:
        from ..live.signals import STEP_SEC, _levels
    except Exception:
        return None
    try:
        level = _levels(Path(path), False)
        high = _levels(Path(path), True)
    except Exception as exc:
        logger.debug("View sequencing peak scan failed: %s", exc)
        return None
    n = min(len(level), len(high))
    if n < 4:
        return None
    import numpy as np

    def z(values: List[float]) -> Any:
        v = np.asarray(values[:n], dtype=float)
        med = float(np.median(v))
        mad = float(np.median(np.abs(v - med)))
        return (v - med) / (1.4826 * mad) if mad > 1e-9 else v * 0.0

    score = np.maximum(z(level), z(high))
    idx = int(np.argmax(score))
    lift = max(
        level[idx] - float(np.median(level[:n])),
        high[idx] - float(np.median(high[:n])),
    )
    if lift < PEAK_MIN_LIFT_DB:
        return None
    return (idx + 0.5) * STEP_SEC
