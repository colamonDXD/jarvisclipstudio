"""Composite user-placed still images into an editor export.

Overlay items sit above the B-roll lane and below the captions inside the
same filter graph, so the clip is still encoded once. Each item is a still
image shown at a fixed size and position for its window on the output
timeline; the image's own transparency (PNG/WebP alpha) is preserved.
"""

from __future__ import annotations

from typing import Any, Dict, List, Tuple


def image_input_args(items: List[Dict[str, Any]]) -> List[str]:
    """ffmpeg input args: each still looped only as long as its window needs."""
    args: List[str] = []
    for item in items:
        end = float(item["start"]) + float(item["duration"])
        args += ["-loop", "1", "-framerate", "30", "-t", f"{end + 0.1:.3f}", "-i", str(item["path"])]
    return args


def image_overlay_graph(
    base_label: str,
    items: List[Dict[str, Any]],
    width: int,
    height: int,
    first_input: int = 1,
) -> Tuple[str, str]:
    """Filter graph fragment that overlays each image on ``base_label``.

    Items are in play order and never overlap, so chaining them in order is
    the defined composite order. Returns the fragment (without a leading
    ``;``) and its output label. Each item needs ``path`` (unused here beyond
    input ordering), ``start``/``duration`` in output seconds, ``x``/``y`` as
    the centre position in frame fractions and ``scale`` as the width in
    frame-width fractions.
    """
    parts: List[str] = []
    current = base_label
    for index, item in enumerate(items):
        start = float(item["start"])
        end = start + float(item["duration"])
        # Even width so the yuva420p conversion keeps chroma aligned.
        target_width = max(2, round(float(item["scale"]) * width / 2) * 2)
        image_label = f"[img{index}]"
        out_label = f"[withimg{index}]"
        parts.append(
            f"[{first_input + index}:v]scale={target_width}:-2,format=yuva420p,"
            f"settb=AVTB,setpts=PTS-STARTPTS{image_label}"
        )
        parts.append(
            f"{current}{image_label}overlay="
            f"x='main_w*{float(item['x']):.4f}-overlay_w/2':"
            f"y='main_h*{float(item['y']):.4f}-overlay_h/2':"
            f"eof_action=repeat:enable='between(t,{start:.3f},{end:.3f})'{out_label}"
        )
        current = out_label
    return ";".join(parts), current
