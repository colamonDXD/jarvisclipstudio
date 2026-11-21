"""Emoji sticker overlay module for SnipNova.

Feature is OFF by default. Set env var ``STICKERS=1`` or ``STICKERS=true``
(case-insensitive) to enable.

Public API
----------
stickers_enabled() -> bool
choose_stickers(...) -> list[dict]
sticker_overlay_args(...) -> tuple[list[str], str, str]
"""
from __future__ import annotations

import json
import os
import random
import re
from pathlib import Path
from typing import Optional

# ---------------------------------------------------------------------------
# Repo roots and assets
# ---------------------------------------------------------------------------
_REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent
_DEFAULT_ASSETS = _REPO_ROOT / "assets"

# ---------------------------------------------------------------------------
# Emoji → filename map (Noto Emoji 512px PNGs)
# ---------------------------------------------------------------------------
EMOJI_TO_FILENAME: dict[str, str] = {
    "😂": "emoji_u1f602.png",
    "🤣": "emoji_u1f923.png",
    "💀": "emoji_u1f480.png",
    "🔥": "emoji_u1f525.png",
    "😱": "emoji_u1f631.png",
    "😭": "emoji_u1f62d.png",
    "😡": "emoji_u1f621.png",
    "🤯": "emoji_u1f92f.png",
    "👀": "emoji_u1f440.png",
    "💯": "emoji_u1f4af.png",
    "🙏": "emoji_u1f64f.png",
    "😳": "emoji_u1f633.png",
    "🥶": "emoji_u1f976.png",
    "🤡": "emoji_u1f921.png",
    "👑": "emoji_u1f451.png",
    "💸": "emoji_u1f4b8.png",
    "🚨": "emoji_u1f6a8.png",
    "❗": "emoji_u2757.png",
    "❓": "emoji_u2753.png",
    "✅": "emoji_u2705.png",
    "❌": "emoji_u274c.png",
    "🎯": "emoji_u1f3af.png",
    "🏆": "emoji_u1f3c6.png",
    "💥": "emoji_u1f4a5.png",
    "⚡": "emoji_u26a1.png",
    "🫡": "emoji_u1fae1.png",
    "😤": "emoji_u1f624.png",
    "😎": "emoji_u1f60e.png",
    "🤔": "emoji_u1f914.png",
    "🙄": "emoji_u1f644.png",
    "😬": "emoji_u1f62c.png",
    "🥲": "emoji_u1f972.png",
    "😈": "emoji_u1f608.png",
    "👻": "emoji_u1f47b.png",
    "🧠": "emoji_u1f9e0.png",
    "🫠": "emoji_u1fae0.png",
    "🗿": "emoji_u1f5ff.png",
}

# ---------------------------------------------------------------------------
# Keyword rules: (regex_pattern, emoji)
# ---------------------------------------------------------------------------
_KEYWORD_RULES: list[tuple[re.Pattern, str]] = [
    (re.compile(r"\b(dead|died|rip|fail(ed|s)?)\b", re.IGNORECASE), "💀"),
    (re.compile(r"\b(fire|insane|crazy|let'?s?\s*go)\b", re.IGNORECASE), "🔥"),
    (re.compile(r"\b(money|cash|paid)\b", re.IGNORECASE), "💸"),
    (re.compile(r"\?"), "🤔"),
]

# Signal rules: signal_name -> list of candidate emojis
_SIGNAL_RULES: dict[str, list[str]] = {
    "laugh": ["😂", "🤣"],
    "scream": ["😱"],
    "f0": ["🔥"],
    "vocal": ["🤔"],
}


def stickers_enabled() -> bool:
    """Return True only if STICKERS=1 or STICKERS=true (case-insensitive)."""
    val = os.environ.get("STICKERS", "").strip().lower()
    return val in ("1", "true")


def _safe_y(rng: random.Random) -> float:
    """Return a y coordinate in 0..1 that avoids the caption band 0.40..0.60."""
    # Top zone: [0.05, 0.35], Bottom zone: [0.65, 0.90]
    if rng.choice([True, False]):
        return rng.uniform(0.08, 0.35)
    else:
        return rng.uniform(0.65, 0.88)


def _resolve_emoji_file(emoji: str, assets_dir: Optional[Path] = None) -> Optional[str]:
    """Find PNG path on disk for the given emoji character."""
    filename = EMOJI_TO_FILENAME.get(emoji)
    if not filename:
        cp = "_".join(f"{ord(c):x}" for c in emoji if ord(c) != 0xFE0F)
        filename = f"emoji_u{cp}.png"

    roots = []
    if assets_dir:
        roots.append(assets_dir)
    roots.extend([_DEFAULT_ASSETS, Path("assets"), _REPO_ROOT / "assets"])

    for root in roots:
        p = root / "stickers" / "emoji" / filename
        if p.exists():
            return str(p)
    return None


def choose_stickers(
    signals_by_time: list[dict],
    words: list[dict],
    duration: float,
    max_per_clip: int = 4,
    min_gap: float = 4.0,
    seed: str = "",
) -> list[dict]:
    """Select emoji sticker events for a clip.

    Parameters
    ----------
    signals_by_time : list of {"t": sec, "signals": list[str], "strength": float}
    words           : list of {"text": str, "start": float, "end": float}
    duration        : total clip duration in seconds
    max_per_clip    : maximum number of stickers in the clip
    min_gap         : minimum seconds between adjacent stickers
    seed            : determinism seed string

    Returns
    -------
    list of {"t", "emoji", "file", "x", "y", "scale", "dur"}
    """
    rng = random.Random(seed)
    candidates: list[dict] = []

    # 1. Evaluate audio signals
    for item in signals_by_time:
        t = float(item["t"])
        strength = float(item.get("strength", 0.5))
        signals = item.get("signals", [])
        for sig in signals:
            emojis = _SIGNAL_RULES.get(sig)
            if emojis:
                chosen = rng.choice(emojis)
                candidates.append({"t": t, "emoji": chosen, "strength": strength})

    # 2. Evaluate transcript words
    for w in words:
        text = w.get("text", "")
        start = float(w.get("start", 0.0))
        for pattern, emoji in _KEYWORD_RULES:
            if pattern.search(text):
                candidates.append({"t": start, "emoji": emoji, "strength": 0.75})
                break

    # Sort candidates strongest first (with stable deterministic secondary sort)
    candidates.sort(key=lambda c: (-c["strength"], c["t"]))

    placed: list[dict] = []

    for c in candidates:
        if len(placed) >= max_per_clip:
            break

        t = c["t"]
        # Enforce min_gap from all already placed stickers
        if any(abs(t - p["t"]) < min_gap for p in placed):
            continue

        emoji = c["emoji"]
        file_path = _resolve_emoji_file(emoji)

        x = round(rng.uniform(0.12, 0.88), 4)
        y = round(_safe_y(rng), 4)
        scale = round(rng.uniform(0.15, 0.25), 4)
        dur = round(min(rng.uniform(1.5, 2.5), max(0.5, duration - t)), 3)

        placed.append({
            "t": round(t, 3),
            "emoji": emoji,
            "file": file_path,
            "x": x,
            "y": y,
            "scale": scale,
            "dur": dur,
        })

    # Return sorted chronologically
    placed.sort(key=lambda p: p["t"])
    return placed


def sticker_overlay_args(
    events: list[dict],
    width: int = 1080,
    height: int = 1920,
    assets_dir: Optional[str] = None,
) -> tuple[list[str], str, str]:
    """Build ffmpeg input arguments and filter_complex fragment for sticker overlays.

    Animation per sticker:
      - pop-in: scale 0.6 -> 1.1 -> 1.0 over 0.25s
      - fade-out: last 0.2s
      - active from t to t + dur

    Returns
    -------
    (inputs_list, filter_complex_fragment, out_label)
    """
    if not events:
        return [], "", "[0:v]"

    dir_path = Path(assets_dir) if assets_dir else None
    inputs: list[str] = []
    filters: list[str] = []
    base_label = "[0:v]"

    for idx, ev in enumerate(events):
        file_path = ev.get("file")
        if not file_path or not Path(file_path).exists():
            emoji = ev.get("emoji", "")
            file_path = _resolve_emoji_file(emoji, dir_path)

        # If file is missing, create a pass-through so the graph doesn't break
        if not file_path or not Path(file_path).exists():
            continue

        inputs.append(file_path)
        in_idx = len(inputs)  # 1-based index (0 is base video)

        t_start = ev["t"]
        dur = max(ev.get("dur", 2.0), 0.5)
        t_end = t_start + dur
        fade_dur = 0.20
        fade_start = max(t_start, t_end - fade_dur)
        pop_dur = 0.25

        x_frac = ev.get("x", 0.5)
        y_frac = ev.get("y", 0.2)
        scale_frac = ev.get("scale", 0.2)

        sticker_w = max(16, int(width * scale_frac))
        cx = int(width * x_frac)
        cy = int(height * y_frac)

        pop1 = t_start + pop_dur * 0.60
        pop2 = t_start + pop_dur

        scale_expr = (
            f"max(2,trunc(if(lt(t,{pop1:.4f}),"
            f"{sticker_w}*(0.6+0.5*(t-{t_start:.4f})/{pop_dur * 0.60:.4f}),"
            f"if(lt(t,{pop2:.4f}),"
            f"{sticker_w}*(1.1-0.1*(t-{pop1:.4f})/{pop_dur * 0.40:.4f}),"
            f"{sticker_w}))/2)*2)"
        )

        st_lbl = f"[stk{idx}]"
        out_lbl = f"[vstk{idx}]"

        filters.append(
            f"[{in_idx}:v]loop=loop=-1:size=1:start=0,"
            f"settb=AVTB,setpts=PTS-STARTPTS+{t_start:.4f}/TB,"
            f"scale=w='{scale_expr}':h='{scale_expr}':eval=frame,"
            f"format=rgba,"
            f"fade=t=out:st={fade_start:.4f}:d={fade_dur:.4f}:alpha=1"
            f"{st_lbl}"
        )
        filters.append(
            f"{base_label}{st_lbl}overlay=x='{cx}-w/2':y='{cy}-h/2':eval=frame:"
            f"enable='between(t,{t_start:.4f},{t_end:.4f})'"
            f"{out_lbl}"
        )
        base_label = out_lbl

    filter_complex = ";\n".join(filters)
    return inputs, filter_complex, base_label
