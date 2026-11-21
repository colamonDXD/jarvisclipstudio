"""Hook finish: the first seconds of a clip shaped like the Shorts that go viral.

Measured 2026-11-19 (tools/live/study_viral_hooks.py): 13 Shorts with 10M-110M
views (MrBeast, Zach King, iShowSpeed, KSI...) against 10 SnipNova clips.

| first seconds            | viral            | ours      |
|--------------------------|------------------|-----------|
| first cut                | ~2.7 s, 7/min    | never     |
| motion in first 2 s      | 10.9             | 6.8       |
| brightness (0-255)       | 125              | 93        |

``finish_hook(clip)`` adds, in one GPU pass over the finished clip:

- a punch-in at ``HOOK_PUNCH_AT`` (2.6 s): the frame jumps to ``HOOK_PUNCH_ZOOM``
  (1.08) for ``HOOK_PUNCH_SECONDS`` (1.2 s), the "cut" the viral openings have,
  with a whoosh from the licence-checked SFX library just before it;
- a brightness lift when the clip is dark (mean luma under 108): eq brightness
  towards ~120, slight contrast; a bright clip is left alone.

With ``HOOK_FINISH_V2=on`` (media.hook_v2, default off) the opening is
composed as one segment: a face-anchored settle-zoom (1.3x easing back
to 1.0 over ~1.7 s) runs before the punch-in, and the EDL audio plan
guarantees a first-second SFX hit. With the switch off the filter
chain below is exactly the pre-v2 one, byte for byte.

Skipped for Plain ("just cuts") EDLs and clips shorter than 6 s.
``HOOK_FINISH=off`` turns it off.
"""
from __future__ import annotations

import logging
import os
import shutil
import subprocess
from pathlib import Path
from typing import Any, Dict, Optional

from .hook_v2 import (
    hook_finish_v2_enabled,
    settle_anchor,
    settle_seconds_for,
    settle_zoom_filter,
)

logger = logging.getLogger(__name__)

TARGET_LUMA = 120.0
DARK_LUMA = 108.0

# Trained from the viral Shorts study (tools/live/study_viral_hooks.py profile):
# medians over 10M+ view Shorts. The file overrides these defaults.
PROFILE_FILE = Path(__file__).with_name("hook_profile.json")
DEFAULT_PROFILE = {"first_cut_s": 2.6, "cuts_per_min": 7.0, "target_brightness": 125.0, "target_saturation": 66.0,
                   "max_punches": 6, "punch_seconds": 1.2, "punch_zoom": 1.08}


def load_profile() -> Dict[str, float]:
    import json

    profile = dict(DEFAULT_PROFILE)
    try:
        data = json.loads(PROFILE_FILE.read_text(encoding="utf-8"))
        profile.update({k: float(v) for k, v in (data.get("profile") or {}).items() if k in DEFAULT_PROFILE})
    except (OSError, ValueError):
        pass
    return profile


def punch_times(duration: float, profile: Dict[str, float]) -> list:
    """Punch-in moments: the viral first cut, then the viral cut rate, never in the last 2 s."""
    first = float(profile["first_cut_s"])
    rate = max(1.0, float(profile["cuts_per_min"]))
    step = max(3.0, 60.0 / rate)
    times, t = [], first
    while t < duration - 2.0 and len(times) < int(profile["max_punches"]):
        times.append(round(t, 2))
        t += step
    return times


def mean_saturation(clip: Path, duration: float) -> float:
    import numpy as np

    values = []
    for frac in (0.2, 0.5, 0.8):
        raw = subprocess.run(["ffmpeg", "-v", "error", "-ss", f"{duration * frac:.2f}", "-i", str(clip), "-frames:v", "1",
                              "-vf", "scale=90:160", "-f", "rawvideo", "-pix_fmt", "rgb24", "-"], capture_output=True).stdout
        if len(raw) >= 90 * 160 * 3:
            rgb = np.frombuffer(raw[:90 * 160 * 3], np.uint8).reshape(-1, 3).astype(float)
            mx, mn = rgb.max(axis=1), rgb.min(axis=1)
            values.append(float(np.mean(np.where(mx > 0, (mx - mn) / np.maximum(mx, 1) * 255, 0))))
    return sum(values) / len(values) if values else 66.0


def colour_filter(luma: float, sat: float, profile: Dict[str, float]) -> Optional[str]:
    """eq towards the viral brightness/saturation; None when already close."""
    parts = []
    target_l = float(profile["target_brightness"])
    if luma < target_l - 8:
        parts.append(f"brightness={min(0.12, (target_l - luma) / 255.0 * 0.9):.3f}:contrast=1.05")
    target_s = float(profile["target_saturation"])
    if sat > 1 and abs(sat - target_s) / target_s > 0.12:
        parts.append(f"saturation={max(0.92, min(1.2, target_s / sat)):.3f}")
    return ("eq=" + ":".join(parts)) if parts else None


def hook_finish_enabled() -> bool:
    return os.getenv("HOOK_FINISH", "on").strip().lower() not in ("off", "0", "false", "no")


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, str(default)))
    except ValueError:
        return default


def _probe(clip: Path) -> Dict[str, Any]:
    out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration:stream=codec_type,width,height",
                          "-of", "json", str(clip)], capture_output=True, text=True).stdout
    import json

    try:
        data = json.loads(out or "{}")
    except ValueError:
        data = {}
    streams = data.get("streams") or []
    video = next((s for s in streams if s.get("codec_type") == "video"), {})
    return {"duration": float((data.get("format") or {}).get("duration") or 0),
            "width": int(video.get("width") or 0), "height": int(video.get("height") or 0),
            "audio": any(s.get("codec_type") == "audio" for s in streams)}


def mean_luma(clip: Path, duration: float) -> float:
    import numpy as np

    values = []
    for frac in (0.1, 0.3, 0.5, 0.7, 0.9):
        raw = subprocess.run(["ffmpeg", "-v", "error", "-ss", f"{duration * frac:.2f}", "-i", str(clip), "-frames:v", "1",
                              "-vf", "scale=90:160,format=gray", "-f", "rawvideo", "-"], capture_output=True).stdout
        if len(raw) >= 90 * 160:
            values.append(float(np.frombuffer(raw[:90 * 160], np.uint8).mean()))
    return sum(values) / len(values) if values else 128.0


def brightness_filter(luma: float) -> Optional[str]:
    """eq filter that lifts a dark clip towards TARGET_LUMA, or None."""
    if luma >= DARK_LUMA:
        return None
    lift = min(0.12, (TARGET_LUMA - luma) / 255.0 * 0.9)
    return f"eq=brightness={lift:.3f}:contrast=1.05:saturation=0.97"


def punch_filter(width: int, height: int, at, seconds: float, zoom: float) -> str:
    """Scale the frame up by ``zoom`` during each [at, at+seconds] and crop back to size (centre)."""
    w2, h2 = int(width * zoom) // 2 * 2, int(height * zoom) // 2 * 2
    starts = at if isinstance(at, (list, tuple)) else [at]
    cond = "+".join(f"between(t,{a:.2f},{a + seconds:.2f})" for a in starts)
    return (f"scale=w='if({cond},{w2},{width})':h='if({cond},{h2},{height})':eval=frame,"
            f"crop={width}:{height}:(iw-{width})/2:(ih-{height})/2")


QUOTE_Y_FRAC = 0.025         # the very top edge (owner: "always on the very top", never over a face)
EMOJI_PX = 54


def emoji_file(char: str) -> Optional[Path]:
    """The Noto emoji PNG for ``char`` (assets/stickers, Apache-2.0), or None."""
    import json

    from .edl_render import ASSETS_DIR

    manifest = Path(ASSETS_DIR) / "stickers" / "STICKERS_MANIFEST.json"
    try:
        entries = json.loads(manifest.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    for entry in entries if isinstance(entries, list) else []:
        if (entry.get("emoji") or "").replace("️", "") == (char or "").replace("️", ""):
            path = Path(ASSETS_DIR).parent / entry["file"] if not Path(entry["file"]).is_absolute() else Path(entry["file"])
            if not path.is_file():
                path = Path(ASSETS_DIR) / "stickers" / "emoji" / Path(entry["file"]).name
            return path if path.is_file() else None
    return None


def quote_title_text(text: str, creator: Optional[str]) -> Optional[str]:
    """Third person, masked, short: never "I/me/my" (owner rule 2026-11-19)."""
    from ..auto_director.title_person import ensure_third_person_title
    from .profanity import mask_profanity

    text = " ".join(str(text or "").split()).strip().strip('"').strip()
    if not text:
        return None
    text = ensure_third_person_title(text, creator_name=creator, fallback_text=text) or text
    words = text.split()
    if len(words) > 9:
        text = " ".join(words[:9])
    return mask_profanity(text)


def finish_hook(clip_path: Path, edl: Optional[Dict[str, Any]] = None, seed: str = "") -> bool:
    """Apply the hook finish to ``clip_path`` in place. True when applied."""
    if not hook_finish_enabled():
        return False
    if edl is not None and edl.get("template") == "plain":
        return False
    clip_path = Path(clip_path)
    info = _probe(clip_path)
    if info["duration"] < 6.0 or not info["width"] or not info["height"]:
        return False
    profile = load_profile()
    times = punch_times(info["duration"], profile)
    at = times[0] if times else float(profile["first_cut_s"])
    seconds = float(profile["punch_seconds"])
    zoom = float(profile["punch_zoom"])
    filters = []
    settle = None
    if hook_finish_v2_enabled():
        # The settle-zoom owns the opening; it must be back at 1.0
        # before the first punch-in lands (hook_v2.settle_seconds_for
        # clamps or skips it), so the two zooms never stack.
        settle_seconds = settle_seconds_for(times[0] if times else None)
        if settle_seconds:
            anchor_x, anchor_y = settle_anchor(edl, info["width"], info["height"])
            settle = settle_zoom_filter(info["width"], info["height"], anchor_x, anchor_y, settle_seconds)
            filters.append(settle)
    luma = mean_luma(clip_path, info["duration"])
    lift = colour_filter(luma, mean_saturation(clip_path, info["duration"]), profile)
    if lift:
        filters.append(lift)
    filters.append(punch_filter(info["width"], info["height"], times or [at], seconds, zoom))
    # A visible cut: a quick white flash (~2 frames) on each punch-in.
    flash = "+".join(f"between(t,{t:.2f},{t + 0.07:.2f})" for t in (times or [at]))
    filters.append(f"drawbox=x=0:y=0:w=iw:h=ih:color=white@0.55:t=fill:enable='{flash}'")
    video_chain = ",".join(filters)

    # Quote title with emoji ("Lacy swore he never crashes" 😭✌️🥀): the text
    # via drawtext (Anton, white, black outline), the emoji as PNGs on a row
    # under it - the container has no emoji font, so they cannot be text.
    quote = (edl or {}).get("quote_title") or {}
    quote_text = quote_title_text(quote.get("text"), quote.get("creator")) if quote else None
    # Emoji under the title: OFF (owner 2026-11-19: "stop adding emoji, I will add them myself").
    use_emoji = os.getenv("HOOK_EMOJI", "off").strip().lower() in ("on", "1", "true", "yes")
    emoji_paths = ([p for p in (emoji_file(e) for e in (quote.get("emojis") or [])[:3]) if p is not None]
                   if use_emoji else [])
    text_file = None
    if quote_text:
        import tempfile

        from .ffmpeg import ffmpeg_escape_filter_path

        font = Path(__file__).resolve().parents[2] / "fonts" / "Anton-Regular.ttf"
        handle = tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False, encoding="utf-8")
        handle.write(f"\u201c{quote_text}\u201d" if False else f'"{quote_text}"')
        handle.close()
        text_file = Path(handle.name)
        size = max(26, int(info["width"] * 0.042))  # small (owner: "make title smaller")
        y = int(info["height"] * QUOTE_Y_FRAC)
        video_chain += (f",drawtext=fontfile={ffmpeg_escape_filter_path(font)}:textfile={ffmpeg_escape_filter_path(text_file)}"
                        f":fontsize={size}:fontcolor=white:borderw={max(3, size // 12)}:bordercolor=black"
                        f":x=(w-text_w)/2:y={y}")

    whoosh = None
    try:
        from .asset_library import pick_variant
        from .edl_render import ASSETS_DIR

        whoosh = pick_variant(Path(ASSETS_DIR) / "sfx", "whoosh", f"{seed or clip_path.stem}|hook")
        if whoosh is None:
            candidate = Path(ASSETS_DIR) / "sfx" / "whoosh.wav"
            whoosh = candidate if candidate.is_file() else None
    except Exception as exc:
        logger.debug("Hook whoosh unavailable: %s", exc)

    from .edl_render import CPU_FALLBACK_CRF, CPU_FALLBACK_PRESET, get_video_encoder
    from .ffmpeg import build_final_video_encode_args

    out = clip_path.with_name(clip_path.stem + ".hook" + clip_path.suffix)

    def command(encoder_args):
        if not any(a == "-c:v" for a in encoder_args):
            encoder_args = ["-c:v", "libx264", *encoder_args]
        cmd = ["ffmpeg", "-v", "error", "-y", "-i", str(clip_path)]
        graph = f"[0:v]{video_chain}[v0]"
        last = "v0"
        if emoji_paths and quote_text:
            size = max(26, int(info["width"] * 0.042))  # small (owner: "make title smaller")
            row_y = int(info["height"] * QUOTE_Y_FRAC) + int(size * 1.25)
            row_w = len(emoji_paths) * EMOJI_PX + (len(emoji_paths) - 1) * 10
            x0 = (info["width"] - row_w) // 2
            for i, path in enumerate(emoji_paths):
                cmd += ["-i", str(path)]
                idx = 1 + i
                graph += f";[{idx}:v]scale={EMOJI_PX}:{EMOJI_PX}[e{i}];[{last}][e{i}]overlay=x={x0 + i * (EMOJI_PX + 10)}:y={row_y}[v{i + 1}]"
                last = f"v{i + 1}"
        audio_index = 1 + (len(emoji_paths) if (emoji_paths and quote_text) else 0)
        if whoosh is not None and info["audio"]:
            hits = (times or [at])[:4]  # a whoosh on each of the first cuts
            split = f"[{audio_index}:a]asplit={len(hits)}" + "".join(f"[w{i}]" for i in range(len(hits)))
            delayed = ";".join(f"[w{i}]adelay={max(0, int((h - 0.15) * 1000))}|{max(0, int((h - 0.15) * 1000))},"
                               f"volume=0.95[d{i}]" for i, h in enumerate(hits))
            mix = "[0:a]" + "".join(f"[d{i}]" for i in range(len(hits)))
            cmd += ["-i", str(whoosh), "-filter_complex",
                    f"{graph};{split};{delayed};{mix}amix=inputs={len(hits) + 1}:duration=first:normalize=0[a]",
                    "-map", f"[{last}]", "-map", "[a]", "-c:a", "aac", "-b:a", "192k"]
        else:
            cmd += ["-filter_complex", graph, "-map", f"[{last}]", "-map", "0:a?", "-c:a", "copy"]
        return cmd + [*encoder_args, "-pix_fmt", "yuv420p", "-movflags", "+faststart", str(out)]

    encoder, encoder_args = get_video_encoder(True)
    result = subprocess.run(command(encoder_args), capture_output=True, text=True)
    if (result.returncode != 0 or not out.is_file() or out.stat().st_size == 0) and encoder != "libx264":
        # NVENC refused (no GPU in this container): one CPU retry.
        cpu_args = build_final_video_encode_args(crf=CPU_FALLBACK_CRF, preset=CPU_FALLBACK_PRESET)
        result = subprocess.run(command(cpu_args), capture_output=True, text=True)
    if text_file is not None:
        text_file.unlink(missing_ok=True)
    if result.returncode != 0 or not out.is_file() or out.stat().st_size == 0:
        logger.warning("Hook finish failed; keeping the clip as rendered: %s", (result.stderr or "")[-300:])
        out.unlink(missing_ok=True)
        return False
    shutil.move(str(out), str(clip_path))
    logger.info("Hook finish: punch-in at %.1fs%s%s%s", at, f", brightness lift (luma {luma:.0f})" if lift else "",
                ", whoosh" if whoosh is not None else "",
                ", settle-zoom" if settle else "")
    return True
