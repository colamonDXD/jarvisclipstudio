"""Layout templates for streamer clips: "Face on top, game below".

The look short-form gaming pages use for streamer reactions (Luffy's
reference reel, 2026-11-17): the streamer's facecam, cropped tight, fills
the top of the 9:16 frame; the gameplay, cropped to fill, sits below; a
thin coloured divider marks the seam and the captions sit on that seam,
one to three punchy words at a time; a small "credit @creator" line at the
bottom. Two styles, matching the two reference cards:

- ``facecam_red``  — red divider, white condensed captions with a red
  outline (the iShowSpeed card).
- ``facecam_gold`` — gold divider, white condensed captions with a black
  outline and the active word in yellow (the Techno Gamerz card).

A task chooses one with ``layout_template``; ``None`` keeps the automatic
layout. The template only applies when the clip has a facecam (found by
``reaction_layout.find_reactor_face`` + inset, or ``facecam_rect``); other
clips keep the normal planner and just get the template's caption style.
"""

from __future__ import annotations

import atexit
import os
import tempfile
import threading
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

from .ffmpeg import clamp_even, ffmpeg_escape_filter_path, round_to_even

Box = Tuple[float, float, float, float]

OUT_W, OUT_H = 1080, 1920
FACE_PANEL_H = 864            # 45 % of the frame, like the reference cards
GAME_PANEL_H = OUT_H - FACE_PANEL_H
DIVIDER_H = 8
CREDIT_MAX_CHARS = 40

LAYOUT_TEMPLATES: Dict[str, Dict[str, Any]] = {
    "facecam_red": {
        "name": "Face on top — red",
        "divider_color": "0xE5262B",
        "caption_template": "seam_red",
    },
    "facecam_gold": {
        "name": "Face on top — gold",
        "divider_color": "0xF5B700",
        "caption_template": "seam_gold",
    },
}


def validate_layout_template(value: object) -> Optional[str]:
    """Normalise a create-form template choice; None when absent or "auto"."""
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError("Invalid layout_template")
    cleaned = value.strip().lower()
    if cleaned in ("", "auto", "none"):
        return None
    if cleaned not in LAYOUT_TEMPLATES:
        raise ValueError(
            "Invalid layout_template: expected auto or one of "
            + ", ".join(LAYOUT_TEMPLATES)
        )
    return cleaned


def clean_layout_credit(value: object) -> Optional[str]:
    """The credit line text ("@iShowSpeed" -> "credit @iShowSpeed")."""
    if not isinstance(value, str):
        return None
    text = " ".join(value.split())[:CREDIT_MAX_CHARS]
    if not text:
        return None
    return text if text.lower().startswith("credit") else f"credit {text}"


def caption_template_for(layout_template: Optional[str]) -> Optional[str]:
    spec = LAYOUT_TEMPLATES.get(layout_template or "")
    return spec["caption_template"] if spec else None


def _face_crop(face: Box, inset: Box, width: int, height: int) -> Tuple[int, int, int, int]:
    """Tight crop of the camera window around the face, at the panel aspect."""
    ix, iy, iw, ih = inset
    fx, fy, fw, fh = face
    aspect = OUT_W / FACE_PANEL_H
    ch = min(ih, max(fh * 3.0, ih * 0.6))
    cw = ch * aspect
    if cw > iw:
        cw = iw
        ch = cw / aspect
    cw, ch = round_to_even(int(cw)), round_to_even(int(ch))
    cx = fx + fw / 2
    cy = fy + fh * 0.55
    x = clamp_even(int(cx - cw / 2), int(ix), max(int(ix), int(ix + iw - cw)))
    y = clamp_even(int(cy - ch * 0.45), int(iy), max(int(iy), int(iy + ih - ch)))
    x = min(max(0, x), max(0, width - cw))
    y = min(max(0, y), max(0, height - ch))
    return cw, ch, x, y


def _game_crop(inset: Box, width: int, height: int, aspect: Optional[float] = None) -> Tuple[int, int, int, int]:
    """Full-height gameplay crop at the panel aspect, kept clear of the camera."""
    ix, _iy, iw, _ih = inset
    if aspect is None:
        aspect = OUT_W / GAME_PANEL_H
    ch = round_to_even(height)
    cw = round_to_even(int(ch * aspect))
    if cw > width:
        cw = round_to_even(width)
        ch = round_to_even(int(cw / aspect))
    x = int((width - cw) / 2)
    if ix + iw / 2 < width / 2:          # camera on the left
        x = max(x, int(ix + iw))
    else:                                # camera on the right
        x = min(x, int(ix) - cw)
    x = clamp_even(x, 0, max(0, width - cw))
    y = clamp_even(int((height - ch) / 2), 0, max(0, height - ch))
    return cw, ch, x, y


def _credit_filter(credit: str) -> str:
    """drawtext reading a textfile (user text never enters the graph)."""
    fd, name = tempfile.mkstemp(prefix="credit_", suffix=".txt")
    path = Path(name)
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        handle.write(credit.replace("%", r"\%"))
    # Removed an hour later (the render has long finished) or at exit. The
    # timer must be a daemon: a plain Timer keeps a short-lived process
    # (a test run, the auto-edit harness) alive for the full hour.
    timer = threading.Timer(3600.0, lambda: path.unlink(missing_ok=True))
    timer.daemon = True
    timer.start()
    atexit.register(lambda: path.unlink(missing_ok=True))
    font = Path(__file__).resolve().parents[2] / "fonts" / "Montserrat-ExtraBold.ttf"
    font_arg = f":fontfile={ffmpeg_escape_filter_path(font)}" if font.exists() else ""
    return (
        f"drawtext=textfile={ffmpeg_escape_filter_path(path)}{font_arg}"
        ":fontsize=30:fontcolor=white@0.85:borderw=2:bordercolor=black@0.6"
        f":x=(w-text_w)/2:y=h-{GAME_PANEL_H // 14}"
    )


def seam_caption_y(template: str) -> float:
    """Caption position that centres the template's captions ON the seam.

    The caption builder treats the position as the caption's bottom edge
    (centre = position - half the estimated text height, which is 1.5 x
    the font size), so the seam is raised by half that height.
    """
    from ..caption_templates import get_template
    from .captions import get_scaled_font_size

    spec = LAYOUT_TEMPLATES[template]
    font_px = get_scaled_font_size(int(get_template(spec["caption_template"])["font_size"]), OUT_W)
    return (FACE_PANEL_H + font_px * 1.5 / 2) / OUT_H


def build_facecam_stack_layout(
    width: int,
    height: int,
    duration: float,
    face: Box,
    inset: Box,
    template: str,
    credit: Optional[str] = None,
) -> Dict[str, Any]:
    """The "face on top, game below" vertical graph for one facecam clip."""
    spec = LAYOUT_TEMPLATES[template]
    fw_, fh_, fx_, fy_ = _face_crop(face, inset, width, height)
    gw, gh, gx, gy = _game_crop(inset, width, height)
    seam = FACE_PANEL_H - DIVIDER_H // 2
    game_chain = (
        f"[game_src]crop={gw}:{gh}:{gx}:{gy},scale={OUT_W}:{GAME_PANEL_H}:flags=lanczos,setsar=1"
    )
    if credit:
        game_chain += "," + _credit_filter(credit)
    graph = [
        "[0:v]split=2[face_src][game_src]",
        f"[face_src]crop={fw_}:{fh_}:{fx_}:{fy_},scale={OUT_W}:{FACE_PANEL_H}:flags=lanczos,"
        "unsharp=5:5:0.6:5:5:0.0,setsar=1[face]",
        game_chain + "[game]",
        "[face][game]vstack=inputs=2,"
        f"drawbox=x=0:y={seam}:w={OUT_W}:h={DIVIDER_H}:color={spec['divider_color']}:t=fill,"
        "format=yuv420p[vout]",
    ]
    return {
        "kind": "reaction",
        "template": template,
        "video_filter": ";".join(graph),
        "caption_y": seam_caption_y(template),
        "inset": inset,
        "face_crop": (fw_, fh_, fx_, fy_),
        "game_crop": (gw, gh, gx, gy),
    }


def _face_full_crop(face: Box, inset: Box, width: int, height: int) -> Tuple[int, int, int, int]:
    """The largest 9:16 crop inside the camera window, centred on the face.

    The face-full view of a sequenced clip: the facecam alone fills the
    output frame, so the crop stays inside the inset (anything wider
    would show gameplay around the face).
    """
    ix, iy, iw, ih = inset
    fx, fy, fw, fh = face
    aspect = OUT_W / OUT_H
    ch = min(ih, iw / aspect)
    cw = ch * aspect
    if cw > iw:
        cw = iw
        ch = cw / aspect
    cw, ch = round_to_even(int(cw)), round_to_even(int(ch))
    cx = fx + fw / 2
    cy = fy + fh / 2
    x = clamp_even(int(cx - cw / 2), int(ix), max(int(ix), int(ix + iw - cw)))
    y = clamp_even(int(cy - ch * 0.45), int(iy), max(int(iy), int(iy + ih - ch)))
    x = min(max(0, x), max(0, width - cw))
    y = min(max(0, y), max(0, height - ch))
    return cw, ch, x, y


def build_sequenced_stack_layout(
    width: int,
    height: int,
    duration: float,
    face: Box,
    inset: Box,
    template: str,
    credit: Optional[str] = None,
    segments: Optional[Any] = None,
) -> Dict[str, Any]:
    """The face/game stack, sequenced around the clip's peak.

    Same crops as ``build_facecam_stack_layout`` for the split view;
    the gameplay-full and face-full views are the same two crops at the
    full 9:16 aspect, overlaid on the split for their segments
    (``media.view_sequencing.plan_view_segments``). Hard cuts at the
    segment boundaries; the seam captions keep the stack's band
    (``caption_y`` is unchanged) and the credit line is drawn once on
    the composed frame, at the same position the stack draws it.
    """
    from .view_sequencing import VIEW_FACE_FULL, VIEW_GAME_FULL

    spec = LAYOUT_TEMPLATES[template]
    fw_, fh_, fx_, fy_ = _face_crop(face, inset, width, height)
    gw, gh, gx, gy = _game_crop(inset, width, height)
    segments = [dict(s) for s in segments or []]
    views = {s["view"] for s in segments}
    names = ["sq_face", "sq_game"]
    if VIEW_GAME_FULL in views:
        names.append("sq_gfull")
    if VIEW_FACE_FULL in views:
        names.append("sq_ffull")
    seam = FACE_PANEL_H - DIVIDER_H // 2
    graph = [
        "[0:v]split=%d%s" % (len(names), "".join(f"[{n}]" for n in names)),
        f"[sq_face]crop={fw_}:{fh_}:{fx_}:{fy_},scale={OUT_W}:{FACE_PANEL_H}:flags=lanczos,"
        "unsharp=5:5:0.6:5:5:0.0,setsar=1[sq_face_p]",
        f"[sq_game]crop={gw}:{gh}:{gx}:{gy},scale={OUT_W}:{GAME_PANEL_H}:flags=lanczos,setsar=1[sq_game_p]",
        f"[sq_face_p][sq_game_p]vstack=inputs=2,"
        f"drawbox=x=0:y={seam}:w={OUT_W}:h={DIVIDER_H}:color={spec['divider_color']}:t=fill[view_split]",
    ]
    current = "view_split"
    game_full_crop: Optional[Tuple[int, int, int, int]] = None
    face_full_crop: Optional[Tuple[int, int, int, int]] = None
    if VIEW_GAME_FULL in views:
        game_full_crop = _game_crop(inset, width, height, aspect=OUT_W / OUT_H)
        gfw, gfh, gfx, gfy = game_full_crop
        graph.append(
            f"[sq_gfull]crop={gfw}:{gfh}:{gfx}:{gfy},scale={OUT_W}:{OUT_H}:flags=lanczos,setsar=1[view_game]"
        )
        seg = next(s for s in segments if s["view"] == VIEW_GAME_FULL)
        graph.append(
            f"[{current}][view_game]overlay=0:0:enable='gte(t\\,{seg['start']:.3f})*lt(t\\,{seg['end']:.3f})'[seq_g]"
        )
        current = "seq_g"
    if VIEW_FACE_FULL in views:
        face_full_crop = _face_full_crop(face, inset, width, height)
        ffw, ffh, ffx, ffy = face_full_crop
        graph.append(
            f"[sq_ffull]crop={ffw}:{ffh}:{ffx}:{ffy},scale={OUT_W}:{OUT_H}:flags=lanczos,"
            "unsharp=5:5:0.6:5:5:0.0,setsar=1[view_face]"
        )
        seg = next(s for s in segments if s["view"] == VIEW_FACE_FULL)
        graph.append(
            f"[{current}][view_face]overlay=0:0:enable='gte(t\\,{seg['start']:.3f})*lt(t\\,{seg['end']:.3f})'[seq_f]"
        )
        current = "seq_f"
    tail = f"[{current}]"
    if credit:
        tail += _credit_filter(credit) + ","
    tail += "format=yuv420p[vout]"
    graph.append(tail)
    return {
        "kind": "reaction",
        "template": template,
        "video_filter": ";".join(graph),
        "caption_y": seam_caption_y(template),
        "inset": inset,
        "face_crop": (fw_, fh_, fx_, fy_),
        "game_crop": (gw, gh, gx, gy),
        "game_full_crop": game_full_crop,
        "face_full_crop": face_full_crop,
        "layout_timeline": segments,
        "sequenced": True,
    }


# ---------------------------------------------------------------------------
# Hand-drawn boxes (idea from NeonClipping, MIT): the user draws the facecam
# box and the gameplay box on a frame of the stream, so the layout never
# depends on auto-detection. Boxes are fractions of the source frame
# ({"x", "y", "w", "h"} in 0..1). ``arrange`` "top" puts the face above the
# game (the templates' look), "bottom" puts the game above the face.
# The render reads them from a context variable the live finisher sets, so
# no signature on the long render path changes.

import contextvars

MANUAL_BOXES: "contextvars.ContextVar[Optional[Dict[str, Any]]]" = contextvars.ContextVar(
    "manual_layout_boxes", default=None
)
from . import neon_layouts

ARRANGEMENTS = ("top", *(key for key, _, _ in neon_layouts.CATALOG if key != "stack"))


def validate_boxes(value: object) -> Optional[Dict[str, Any]]:
    """Clean ``{"cam": box, "game": box, "arrange"?}``; None when absent. Raises ValueError."""
    if value is None:
        return None
    if not isinstance(value, dict):
        raise ValueError("boxes must be an object")
    out: Dict[str, Any] = {}
    for key in ("cam", "game"):
        box = value.get(key)
        if not isinstance(box, dict):
            raise ValueError(f"boxes.{key} is required")
        try:
            x, y, w, h = (float(box[k]) for k in ("x", "y", "w", "h"))
        except (KeyError, TypeError, ValueError):
            raise ValueError(f"boxes.{key} needs numbers x, y, w, h")
        if not (0 <= x < 1 and 0 <= y < 1 and 0.03 <= w <= 1 and 0.03 <= h <= 1):
            raise ValueError(f"boxes.{key} must be inside the frame")
        w, h = min(w, 1 - x), min(h, 1 - y)
        if min(round(w, 4), round(h, 4)) < 0.03:
            raise ValueError(f"boxes.{key} has too little area inside the frame")
        out[key] = {"x": round(x, 4), "y": round(y, 4), "w": round(w, 4), "h": round(h, 4)}
    arrange = value.get("arrange") or "top"
    if arrange not in ARRANGEMENTS:
        raise ValueError(f"boxes.arrange must be one of {list(ARRANGEMENTS)}")
    out["arrange"] = arrange
    return out


def _cover_crop(box: Dict[str, float], width: int, height: int, aspect: float) -> Tuple[int, int, int, int]:
    """The largest ``aspect`` (w/h) crop centred inside the drawn box, in source pixels."""
    bx, by, bw, bh = box["x"] * width, box["y"] * height, box["w"] * width, box["h"] * height
    cw, ch = bw, bw / aspect
    if ch > bh:
        ch, cw = bh, bh * aspect
    cw, ch = max(2, round_to_even(int(cw))), max(2, round_to_even(int(ch)))
    x = clamp_even(int(bx + (bw - cw) / 2), 0, max(0, width - cw))
    y = clamp_even(int(by + (bh - ch) / 2), 0, max(0, height - ch))
    return cw, ch, x, y


def build_manual_stack_layout(
    width: int, height: int, boxes: Dict[str, Any], template: str, credit: Optional[str] = None,
) -> Dict[str, Any]:
    """The face/game stack from hand-drawn boxes (no detection)."""
    spec = LAYOUT_TEMPLATES[template]
    bottom = boxes.get("arrange") == "bottom"
    arrangement = boxes.get("arrange", "top")
    if arrangement not in ("top", "bottom"):
        rects = []
        for key in ("cam", "game"):
            box = boxes[key]
            aspect = width * box["w"] / (height * box["h"])
            w, h, x, y = _cover_crop(box, width, height, aspect)
            rects.append((x, y, w, h))
        graph = neon_layouts.render_graph(arrangement, rects)
        finish = (_credit_filter(credit) + ",") if credit else ""
        return {
            "kind": "reaction", "template": template, "manual": True,
            "video_filter": graph + ";[base]" + finish + "format=yuv420p[vout]",
            "caption_y": neon_layouts.text_positions(arrangement)["caption_y"] / OUT_H,
            "inset": rects[0],
            "face_crop": (*rects[0][2:], *rects[0][:2]),
            "game_crop": (*rects[1][2:], *rects[1][:2]),
        }
    fw_, fh_, fx_, fy_ = _cover_crop(boxes["cam"], width, height, OUT_W / FACE_PANEL_H)
    gw, gh, gx, gy = _cover_crop(boxes["game"], width, height, OUT_W / GAME_PANEL_H)
    game_chain = f"[game_src]crop={gw}:{gh}:{gx}:{gy},scale={OUT_W}:{GAME_PANEL_H}:flags=lanczos,setsar=1"
    if credit:
        game_chain += "," + _credit_filter(credit)
    seam = (GAME_PANEL_H if bottom else FACE_PANEL_H) - DIVIDER_H // 2
    order = "[game][face]" if bottom else "[face][game]"
    graph = [
        "[0:v]split=2[face_src][game_src]",
        f"[face_src]crop={fw_}:{fh_}:{fx_}:{fy_},scale={OUT_W}:{FACE_PANEL_H}:flags=lanczos,"
        "unsharp=5:5:0.6:5:5:0.0,setsar=1[face]",
        game_chain + "[game]",
        f"{order}vstack=inputs=2,"
        f"drawbox=x=0:y={seam}:w={OUT_W}:h={DIVIDER_H}:color={spec['divider_color']}:t=fill,"
        "format=yuv420p[vout]",
    ]
    caption_y = seam_caption_y(template)
    if bottom:
        caption_y += (GAME_PANEL_H - FACE_PANEL_H) / OUT_H
    return {
        "kind": "reaction", "template": template, "video_filter": ";".join(graph), "caption_y": caption_y,
        "inset": (fx_, fy_, fw_, fh_), "face_crop": (fw_, fh_, fx_, fy_), "game_crop": (gw, gh, gx, gy),
        "manual": True,
    }
