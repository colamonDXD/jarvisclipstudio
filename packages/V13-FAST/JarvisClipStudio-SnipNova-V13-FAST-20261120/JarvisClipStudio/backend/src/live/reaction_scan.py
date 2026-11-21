"""Run the existing streamer reaction picker on rolling windows of the live buffer.

Plain loudness cannot find reactions in a stream whose game audio is
constantly loud (fake-live demo on the Kai sample: max loudness z 1.6, no
candidate). The Phase 2 picker (auto_director.reaction_moments) uses the mic
channel, pitch/scream, laughter, facecam activity and game events, and
names the on-screen trigger. Every ``scan_every`` seconds the last
``window_sec`` of the buffer is cut (stream copy) and analysed; its moments
come back on the wall clock for the fuser. Cost on this laptop (CPU):
~110 s per 170 s window, so a 120 s window every 45-60 s keeps up.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Dict, List

from .buffer import SegmentBuffer

logger = logging.getLogger(__name__)


def _mmss(value: Any) -> float:
    parts = [float(p) for p in str(value).split(":")]
    seconds = 0.0
    for part in parts:
        seconds = seconds * 60 + part
    return seconds


def scan_reactions(buffer: SegmentBuffer, work_dir: Path, window_sec: float = 120.0,
                   now: float | None = None, target_count: int = 3) -> List[Dict[str, Any]]:
    """Reaction moments in the last ``window_sec`` of the buffer, on the wall clock."""
    from ..auto_director.reaction_moments import select_streamer_moments

    span = buffer.span()
    if not span:
        return []
    end = span[1] if now is None else min(now, span[1])
    window = buffer.cut_window(max(span[0], end - window_sec), end, Path(work_dir) / "scan_window.mp4")
    if not window or window["duration"] < 20:
        return []
    try:
        moments = select_streamer_moments(window["path"], window["offset"] + window["duration"],
                                          target_count=target_count, clip_seconds=30.0)
    except Exception as exc:
        logger.warning("Live reaction scan failed: %s", exc)
        return []
    base = window["file_start_wall"]
    out = []
    for m in moments:
        if m.get("hook_type") == "silent_action":
            continue  # "something happens but the streamer does not react": not a clip
        start, end_m = _mmss(m["start_time"]), _mmss(m["end_time"])
        if end_m <= window["offset"]:
            continue
        # The picker's window keeps a few seconds before the reaction; the
        # reaction itself is near the start of its window's last third.
        onset = start + (end_m - start) * 0.55
        out.append({"onset": base + onset, "start": base + start, "end": base + end_m,
                    "score": float(m.get("fused_score") or m.get("virality_score") or 50) / 100.0,
                    "reason": m.get("reasoning") or m.get("reason") or "streamer reaction",
                    "hook_title": m.get("hook_title")})
    return out
