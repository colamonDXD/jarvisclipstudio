"""Funny-voice effect for clips (owner request 2026-11-19: "voice changer to make it funnier").

``apply_voice_fx(clip, "high")`` pitches the whole soundtrack up (chipmunk);
``"deep"`` pitches it down. The duration stays the same: the pitch is shifted
with asetrate and the tempo is put back with atempo, so captions stay in sync.
The video stream is copied, not re-encoded.
"""
from __future__ import annotations

import logging
import shutil
import subprocess
from pathlib import Path

logger = logging.getLogger(__name__)

VOICE_FX = {"high": 1.35, "deep": 0.78}


def voice_fx_filter(mode: str, rate: int = 48000) -> str:
    factor = VOICE_FX[mode]
    tempo = 1.0 / factor
    # atempo accepts 0.5-2.0 per stage.
    return f"asetrate={int(rate * factor)},aresample={rate},atempo={tempo:.4f}"


def apply_voice_fx(clip_path: Path, mode: str | None) -> bool:
    if not mode or mode not in VOICE_FX:
        return False
    clip_path = Path(clip_path)
    out = clip_path.with_name(clip_path.stem + ".voice" + clip_path.suffix)
    cmd = ["ffmpeg", "-v", "error", "-y", "-i", str(clip_path), "-map", "0:v:0", "-map", "0:a:0",
           "-c:v", "copy", "-af", voice_fx_filter(mode), "-c:a", "aac", "-b:a", "192k",
           "-movflags", "+faststart", str(out)]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0 or not out.is_file() or out.stat().st_size == 0:
        logger.warning("Voice effect failed; keeping the original voice: %s", (result.stderr or "")[-200:])
        out.unlink(missing_ok=True)
        return False
    shutil.move(str(out), str(clip_path))
    logger.info("Voice effect applied: %s", mode)
    return True
