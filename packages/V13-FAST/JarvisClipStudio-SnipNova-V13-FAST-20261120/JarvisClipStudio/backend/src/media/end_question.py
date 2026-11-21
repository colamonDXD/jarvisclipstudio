"""End-question overlay: the clip's comment-bait question as an end card.

The render half of ``auto_director/comment_bait`` (viral build queue
#4): when a clip was finished with ``COMMENT_BAIT`` on, its EDL carries
``comment_question`` — the same question the post carries as its pinned
comment — and this pass burns it as a small white text card over the
final ``END_QUESTION_SECONDS`` of the clip, so the loop closes on the
question instead of a fade or a logo.

Discipline mirrors ``hook_finish`` (the pass it sits next to in
``video_utils.create_optimized_clip``):

- the question text travels in a textfile, never inside the filter
  graph string (the gaming-credit pattern);
- Plain ("just cuts") EDLs are skipped — Plain means no added
  graphics, the owner edits those himself;
- any failure keeps the clip exactly as rendered (returns False);
- with the switch off or no question on the EDL, the file is not
  touched at all, byte for byte.

Placement follows the owner pins for burned text: small (the hook
title's scale family), white with a black outline over a soft dark
box, centred, in the bottom strip — below the caption band and clear
of the face, which sits centre-frame or in the top panel.
"""
from __future__ import annotations

import logging
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

END_QUESTION_SECONDS = 2.0
MIN_CLIP_SECONDS = 8.0
MAX_LINE_CHARS = 44
BOTTOM_FRAC = 0.94  # the text block's bottom edge, as a fraction of height


def wrap_question_lines(text: str, max_chars: int = MAX_LINE_CHARS) -> List[str]:
    """Wrap a question onto at most two lines at word boundaries."""
    words = " ".join(str(text or "").split()).split()
    if not words:
        return []
    lines: List[str] = []
    current = ""
    for word in words:
        candidate = f"{current} {word}".strip()
        if len(candidate) <= max_chars or not current:
            current = candidate
        else:
            lines.append(current)
            current = word
        if len(lines) == 2:
            break
    if current and len(lines) < 2:
        lines.append(current)
    if len(lines) == 2 and " ".join(lines) != " ".join(words):
        # Words remain past the second line: close the card honestly
        # rather than silently dropping the tail of the question.
        lines[1] = lines[1].rstrip(" ,;:") + "…"
    return lines


def end_question_text(edl: Optional[Dict[str, Any]]) -> Optional[str]:
    """The EDL's sanitised comment question, or None."""
    if not edl:
        return None
    text = " ".join(str(edl.get("comment_question") or "").split()).strip()
    return text or None


def _probe(clip: Path) -> Dict[str, Any]:
    import json

    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries",
         "format=duration:stream=codec_type,width,height",
         "-of", "json", str(clip)],
        capture_output=True, text=True,
    ).stdout
    try:
        data = json.loads(out or "{}")
    except ValueError:
        data = {}
    streams = data.get("streams") or []
    video = next((s for s in streams if s.get("codec_type") == "video"), {})
    return {
        "duration": float((data.get("format") or {}).get("duration") or 0),
        "width": int(video.get("width") or 0),
        "height": int(video.get("height") or 0),
        "audio": any(s.get("codec_type") == "audio" for s in streams),
    }


def apply_end_question(clip_path: Path, edl: Optional[Dict[str, Any]] = None) -> bool:
    """Burn ``edl['comment_question']`` over the clip's final seconds,
    in place. True when applied; False (clip untouched) otherwise."""
    from ..auto_director.comment_bait import comment_bait_enabled

    if not comment_bait_enabled():
        return False
    question = end_question_text(edl)
    if question is None:
        return False
    if edl is not None and edl.get("template") == "plain":
        return False
    clip_path = Path(clip_path)
    if not clip_path.is_file():
        return False
    info = _probe(clip_path)
    if info["duration"] < MIN_CLIP_SECONDS or not info["width"] or not info["height"]:
        return False
    lines = wrap_question_lines(question)
    if not lines:
        return False

    from .edl_render import CPU_FALLBACK_CRF, CPU_FALLBACK_PRESET, get_video_encoder
    from .ffmpeg import build_final_video_encode_args, ffmpeg_escape_filter_path

    font = Path(__file__).resolve().parents[2] / "fonts" / "Anton-Regular.ttf"
    handle = tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False, encoding="utf-8")
    handle.write("\n".join(lines))
    handle.close()
    text_file = Path(handle.name)
    size = max(26, int(info["width"] * 0.036))  # small, the title's scale family
    start = max(0.0, info["duration"] - END_QUESTION_SECONDS)
    drawtext = (
        f"drawtext=fontfile={ffmpeg_escape_filter_path(font)}"
        f":textfile={ffmpeg_escape_filter_path(text_file)}"
        f":fontsize={size}:fontcolor=white:borderw={max(3, size // 12)}:bordercolor=black"
        f":box=1:boxcolor=black@0.45:boxborderw={max(6, size // 3)}"
        f":x=(w-text_w)/2:y=h*{BOTTOM_FRAC}-text_h"
        f":enable='gte(t,{start:.2f})'"
    )
    out = clip_path.with_name(clip_path.stem + ".endq" + clip_path.suffix)

    def command(encoder_args):
        if not any(a == "-c:v" for a in encoder_args):
            encoder_args = ["-c:v", "libx264", *encoder_args]
        return [
            "ffmpeg", "-v", "error", "-y", "-i", str(clip_path),
            "-vf", drawtext, "-map", "0:v", "-map", "0:a?",
            "-c:a", "copy", *encoder_args,
            "-pix_fmt", "yuv420p", "-movflags", "+faststart", str(out),
        ]

    try:
        encoder, encoder_args = get_video_encoder(True)
        result = subprocess.run(command(encoder_args), capture_output=True, text=True)
        if (result.returncode != 0 or not out.is_file() or out.stat().st_size == 0) and encoder != "libx264":
            # NVENC refused (no GPU in this container): one CPU retry.
            cpu_args = build_final_video_encode_args(crf=CPU_FALLBACK_CRF, preset=CPU_FALLBACK_PRESET)
            result = subprocess.run(command(cpu_args), capture_output=True, text=True)
        if result.returncode != 0 or not out.is_file() or out.stat().st_size == 0:
            logger.warning("End-question overlay failed; keeping the clip as rendered: %s",
                           (result.stderr or "")[-300:])
            out.unlink(missing_ok=True)
            return False
        shutil.move(str(out), str(clip_path))
        logger.info("End question burned for the final %.1fs: %s", END_QUESTION_SECONDS, question)
        return True
    finally:
        text_file.unlink(missing_ok=True)
