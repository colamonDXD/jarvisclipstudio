"""Music and sound effects for clips rendered on the plain layout path.

The face-on-top gaming layout (and every reaction/facecam layout) is
composed by the plain path, which renders the picture without the EDL
graph. The EDL's music bed and SFX were therefore dropped on exactly the
clips streamers post most (found 2026-11-19: two face-on-top renders had
byte-identical audio to the source).

``finish_plain_path_audio`` runs the EDL's own audio plan
(``edl_render.build_edl_audio_graph``: music variant, ducking, SFX events,
-14 LUFS) over the finished clip in one audio-only pass; the video stream
is copied, not re-encoded. A Plain ("just cuts") task has no music or SFX
in its EDL, so the plan is None and the clip is left exactly as it was.
``PLAIN_PATH_MUSIC=off`` turns it off.
"""
from __future__ import annotations

import logging
import os
import shutil
import subprocess
from pathlib import Path
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)


def plain_path_music_enabled() -> bool:
    return os.getenv("PLAIN_PATH_MUSIC", "on").strip().lower() not in ("off", "0", "false", "no")


def finish_plain_path_audio(clip_path: Path, edl: Optional[Dict[str, Any]], assets_dir: Optional[Path] = None) -> bool:
    """Mix the EDL's music/SFX into ``clip_path`` in place. True when mixed."""
    if not edl or not plain_path_music_enabled():
        return False
    from .edl_render import build_edl_audio_graph
    from .ffmpeg import build_audio_output_args

    try:
        plan = build_edl_audio_graph(edl, 1, assets_dir, audio_source_label="[0:a]")
    except Exception as exc:
        logger.warning("Plain-path music plan failed (%s); keeping the clip's own audio", exc)
        return False
    if plan is None:
        return False
    inputs, graph, label = plan
    clip_path = Path(clip_path)
    mixed = clip_path.with_name(clip_path.stem + ".music" + clip_path.suffix)
    cmd = ["ffmpeg", "-v", "error", "-y", "-i", str(clip_path), *inputs, "-filter_complex", graph,
           "-map", "0:v:0", "-map", label, "-c:v", "copy", *build_audio_output_args(True, loudnorm=False),
           "-shortest", "-movflags", "+faststart", str(mixed)]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0 or not mixed.is_file() or mixed.stat().st_size == 0:
        logger.warning("Plain-path music mix failed; keeping the clip's own audio: %s", (result.stderr or "")[-300:])
        mixed.unlink(missing_ok=True)
        return False
    shutil.move(str(mixed), str(clip_path))
    music = ((edl.get("audio") or {}).get("music_track") or (edl.get("audio") or {}).get("music_mood"))
    logger.info("Plain-path clip finished with music/SFX (%s)", music)
    return True
