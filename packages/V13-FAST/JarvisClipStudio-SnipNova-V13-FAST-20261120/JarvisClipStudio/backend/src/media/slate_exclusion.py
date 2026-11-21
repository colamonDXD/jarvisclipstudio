"""Ad-break / BRB-slate exclusion map (one pass over the whole video).

Adapted from PeakCut (``studio/quality.py``, MIT — see NOTICE). Twitch
and YouTube stitch commercials into streams, and a "be right back"
slate can sit exactly where a good moment should be; a clip taken from
there is worthless.

Metric: mean absolute luma difference between consecutive frames
(ffmpeg ``tblend=difference`` + ``signalstats``). Measured on PeakCut's
real captures: an ad slate reads ~0.19, gameplay ~1.6–2.5 — a wide
margin, so no OCR, no reference image and no model is needed. One pass
builds a whole-video exclusion map; candidates are then checked
against that map. This complements (does not replace) the per-segment
still-picture check in ``media/visual_suitability.py``: that one probes
each selected segment, this one is a single pass that can pre-filter
candidates on long sources before any per-segment work happens.

Thresholds are re-measured on our own fixtures before this filter is
trusted (see the fixture validation in the build report); the defaults
below start from PeakCut's config (0.4 / 6 s) and are env-tunable.
"""

from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

_FFMPEG = "ffmpeg"

_PTS = re.compile(r"pts_time:([0-9.]+)")
_YAVG = re.compile(r"lavfi\.signalstats\.YAVG=([0-9.]+)")

# A candidate whose slate overlap reaches this share of its length is
# excluded. Half, not "any overlap": a clip that merely touches a slate
# edge keeps its real content.
DEFAULT_OVERLAP_FRACTION = 0.5


def slate_exclusion_enabled() -> bool:
    return os.getenv("SLATE_EXCLUSION", "on").strip().lower() not in (
        "off",
        "0",
        "false",
        "no",
    )


def _threshold() -> float:
    try:
        return float(os.getenv("SLATE_STATIC_THRESHOLD", "0.4"))
    except ValueError:
        return 0.4


def _min_seconds() -> float:
    try:
        return float(os.getenv("SLATE_STATIC_MIN_SECONDS", "6"))
    except ValueError:
        return 6.0


def motion_map(path: str | Path, bucket: float = 2.0) -> List[Tuple[float, float]]:
    """(time, motion) for every bucket of the video, or [] if unreadable.

    Motion is the mean luma difference between consecutive frames: near
    zero for a frozen screen, high for anything actually moving.
    """
    try:
        proc = subprocess.run(
            [
                _FFMPEG,
                "-hide_banner",
                "-loglevel",
                "error",
                "-i",
                str(path),
                "-vf",
                "tblend=all_mode=difference,signalstats,metadata=print:file=-",
                "-an",
                "-f",
                "null",
                "-",
            ],
            capture_output=True,
            text=True,
            timeout=900,
        )
    except Exception:  # noqa: BLE001 - a missing ffmpeg must not stop a job
        return []
    times: List[float] = []
    vals: List[float] = []
    at = 0.0
    for line in (proc.stdout or "").splitlines():
        m = _PTS.search(line)
        if m:
            at = float(m.group(1))
            continue
        m = _YAVG.search(line)
        if m:
            times.append(at)
            vals.append(float(m.group(1)))
    if len(vals) > 1:
        times, vals = times[1:], vals[1:]  # first blended frame is against black
    if not vals:
        return []
    buckets: Dict[int, List[float]] = {}
    for t, v in zip(times, vals):
        buckets.setdefault(int(t / bucket), []).append(v)
    return [(i * bucket, sum(v) / len(v)) for i, v in sorted(buckets.items())]


def static_ranges(
    path: str | Path,
    bucket: float = 2.0,
    threshold: Optional[float] = None,
    min_seconds: Optional[float] = None,
) -> List[Tuple[float, float]]:
    """The stretches that look like an ad break, a slate or a frozen screen."""
    threshold = _threshold() if threshold is None else threshold
    min_seconds = _min_seconds() if min_seconds is None else min_seconds
    pairs = motion_map(path, bucket)
    if not pairs:
        return []
    runs: List[Tuple[float, float]] = []
    start: Optional[float] = None
    for t, v in pairs:
        if v < threshold:
            if start is None:
                start = t
        elif start is not None:
            runs.append((start, t))
            start = None
    if start is not None:
        runs.append((start, pairs[-1][0] + bucket))
    return [(a, b) for a, b in runs if b - a >= min_seconds]


def ranges_from_motion(
    pairs: Sequence[Tuple[float, float]],
    bucket: float = 2.0,
    threshold: Optional[float] = None,
    min_seconds: Optional[float] = None,
) -> List[Tuple[float, float]]:
    """Static ranges from an already-computed motion map (testable core)."""
    threshold = _threshold() if threshold is None else threshold
    min_seconds = _min_seconds() if min_seconds is None else min_seconds
    runs: List[Tuple[float, float]] = []
    start: Optional[float] = None
    for t, v in pairs:
        if v < threshold:
            if start is None:
                start = t
        elif start is not None:
            runs.append((start, t))
            start = None
    if start is not None and pairs:
        runs.append((start, pairs[-1][0] + bucket))
    return [(a, b) for a, b in runs if b - a >= min_seconds]


def static_overlap(start: float, end: float, ranges: Sequence[Tuple[float, float]]) -> float:
    """How much of a clip (in seconds) sits inside a static stretch."""
    total = 0.0
    for a, b in ranges:
        lo, hi = max(a, start), min(b, end)
        if hi > lo:
            total += hi - lo
    return total


def filter_segments_by_slate(
    segments: Sequence[Dict[str, Any]],
    ranges: Sequence[Tuple[float, float]],
    *,
    overlap_fraction: float = DEFAULT_OVERLAP_FRACTION,
    bounds_of=None,
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """Pre-filter candidate segment dicts against a slate map.

    A segment is excluded when slate overlap covers at least
    ``overlap_fraction`` of its length. This is an exclusion only —
    order and scores are untouched (no re-ranking). Safety rule shared
    with ``VideoService._drop_static_segments``: if EVERY segment would
    be excluded (an audio podcast over cover art), all are kept and the
    excluded list is empty.
    """
    if not ranges or not segments:
        return list(segments), []

    def bounds(segment: Dict[str, Any]) -> Optional[Tuple[float, float]]:
        if bounds_of is not None:
            try:
                custom = bounds_of(segment)
            except Exception:  # noqa: BLE001 - unparseable bounds keep the segment
                return None
            if custom is None or custom[1] <= custom[0]:
                return None
            return float(custom[0]), float(custom[1])
        start = _seconds(segment.get("start_time"))
        end = _seconds(segment.get("end_time"))
        if start is None or end is None or end <= start:
            return None
        return start, end

    kept: List[Dict[str, Any]] = []
    excluded: List[Dict[str, Any]] = []
    for segment in segments:
        seg_bounds = bounds(segment)
        if seg_bounds is None:
            kept.append(segment)
            continue
        start, end = seg_bounds
        overlap = static_overlap(start, end, ranges)
        if overlap >= overlap_fraction * (end - start):
            excluded.append(
                {**segment, "slate_overlap_seconds": round(overlap, 2)}
            )
        else:
            kept.append(segment)
    if not kept:
        return list(segments), []
    return kept, excluded


def _seconds(value: Any) -> Optional[float]:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip()
    if not text:
        return None
    try:
        return float(text)
    except ValueError:
        pass
    try:
        total = 0.0
        for part in text.split(":"):
            total = total * 60 + float(part)
        return total
    except ValueError:
        return None
