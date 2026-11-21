"""Live AI clipping (MVP, approved by Luffy 2026-11-18; off by default).

A live session records a stream into a rolling buffer of short stream-copied
segments (``recorder``), keeps an index that maps every segment to wall-clock
time (``buffer``), and cuts windows out of footage that is already on disk,
so a clip can start 30 s *before* the moment that triggered it. Moment
detection (chat + audio + visual confirmation) and rendering through the
existing clip pipeline build on these two pieces.

Scope and rules (handoff/LIVE_CLIPPING_FEASIBILITY.md):
- only streams the user owns or is authorised to clip;
- ₹0: ffmpeg + yt-dlp locally, no paid services;
- ``LIVE_CLIPPING`` (default off) gates every entry point.
"""
from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path


def live_clipping_enabled() -> bool:
    return os.getenv("LIVE_CLIPPING", "off").strip().lower() in ("on", "1", "true", "yes")


def yt_dlp_binary() -> str:
    """yt-dlp on PATH, else the one installed next to this Python (the app
    image keeps it in ``/app/.venv/bin``, which is not on PATH)."""
    found = shutil.which("yt-dlp")
    if found:
        return found
    for name in ("yt-dlp", "yt-dlp.exe"):
        candidate = Path(sys.executable).parent / name
        if candidate.is_file():
            return str(candidate)
    return "yt-dlp"
