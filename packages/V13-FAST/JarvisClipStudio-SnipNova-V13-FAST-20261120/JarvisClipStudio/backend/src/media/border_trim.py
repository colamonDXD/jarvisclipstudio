"""Source-border trim detection for split-screen tile windows.

Split tiles are sized from the face (2.8x face width) and can outgrow
the camera feed they were aimed at, capturing the source's own border
furniture inside the tile: the frame line around a podcast feed, a
letterbox edge, the decorative strip outside it. In the rendered clip
that furniture shows as hard slivers at the tile edges (the Bert /
2 Bears clip: pure-white frame lines at both edges of the top tile,
measured in CAPTION_FACE_OVERLAP_REPORT.md).

This module detects that furniture per tile window, from the source
frames themselves:

- A *frame line* is a run of rows/columns that is near-uniform across
  its whole span (at least ``UNIFORM_FRACTION`` of its pixels within
  ``UNIFORM_TOLERANCE`` of the median colour) around an extreme median
  colour (near-white or near-black), persistent across the sampled
  frames (present in at least ``PERSISTENCE`` of them).
- The trim for an edge is everything from the edge through the far
  side of the outermost qualifying line band (plus at most
  ``EXTENSION_MAX_PX`` of anti-alias transition), because the border
  design *outside* the line is furniture by construction.
- Safety bounds, in order: the band must start and end inside the
  outer ``CAP_FRACTION`` of the window (a band that cannot be cleared
  whole inside the cap is left whole — half-eating a wide band looks
  like a render bug, and a band that wide is usually content); the
  trim never crosses the shot's known face box (it stops at the
  face's first pixel, and is dropped entirely when the face overlaps
  the line band itself).

Detection failures degrade to zero trims: planning must never cost a
clip. ``BORDER_TRIM_ENABLED=0`` (or false/off/no) disables detection
entirely, restoring the pre-trim geometry exactly.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

from .common import logger

# A column/row counts as uniform when this share of its pixels sits
# within UNIFORM_TOLERANCE of its median colour (per channel).
UNIFORM_FRACTION = 0.90
UNIFORM_TOLERANCE = 14
# Extreme median colours: frame lines and letterbox bars, not walls.
WHITE_MIN = 235
BLACK_MAX = 20
# A line band must be at least this many pixels wide to count.
MIN_LINE_PX = 3
# Anti-alias transition columns past the band that may join the trim.
EXTENSION_MAX_PX = 3
# Trims live in the outer 6% of the window, per side.
CAP_FRACTION = 0.06
# A line/uniform column must appear in at least this share of the
# sampled frames to count as persistent furniture.
PERSISTENCE = 0.75

Box = Tuple[float, float, float, float]


def border_trim_enabled() -> bool:
    """BORDER_TRIM_ENABLED: on unless explicitly switched off.

    Unlike the opt-in model features this is a deterministic geometry
    fix for a measured defect, so it defaults on; the env var is the
    kill switch (0/false/off/no) that restores the old geometry.
    """
    raw = os.environ.get("BORDER_TRIM_ENABLED", "").strip().lower()
    return raw not in {"0", "false", "off", "no"}


def _line_and_uniform_masks(frame: Any) -> Tuple[Any, Any, Any, Any]:
    """Per-column and per-row (line_mask, uniform_mask) for one frame.

    ``frame`` is an (H, W, 3) uint8 array. A line is uniform around an
    extreme median colour; uniform alone ignores the colour.
    """
    import numpy as np

    pixels = frame.astype(np.int32)
    col_med = np.median(pixels, axis=0)  # (W, 3)
    col_dev = np.abs(pixels - col_med).max(axis=2)  # (H, W)
    col_unif = (col_dev <= UNIFORM_TOLERANCE).mean(axis=0) >= UNIFORM_FRACTION
    col_extreme = ((col_med >= WHITE_MIN).all(axis=1)) | (
        (col_med <= BLACK_MAX).all(axis=1)
    )
    row_med = np.median(pixels, axis=1)  # (H, 3)
    row_dev = np.abs(pixels - row_med[:, None, :]).max(axis=2)  # (H, W)
    row_unif = (row_dev <= UNIFORM_TOLERANCE).mean(axis=1) >= UNIFORM_FRACTION
    row_extreme = ((row_med >= WHITE_MIN).all(axis=1)) | (
        (row_med <= BLACK_MAX).all(axis=1)
    )
    return col_unif & col_extreme, col_unif, row_unif & row_extreme, row_unif


def _persistent(frames_masks: List[Any]) -> Any:
    """Boolean mask of positions present in >= PERSISTENCE of frames."""
    import numpy as np

    if not frames_masks:
        return None
    stacked = np.stack([np.asarray(mask, dtype=bool) for mask in frames_masks])
    return stacked.mean(axis=0) >= PERSISTENCE


def _edge_trim(
    line_mask: Any,
    uniform_mask: Any,
    length: int,
    face_lo: Optional[float],
) -> int:
    """Trim in pixels for one edge, scanned from index 0 (the edge).

    ``line_mask``/``uniform_mask`` are persistent boolean arrays along
    the edge's axis, index 0 at the edge. ``face_lo`` is the distance
    from this edge to the face box's near side, or None.
    """
    cap = int(length * CAP_FRACTION)
    if cap < MIN_LINE_PX or length <= 0:
        return 0
    # Outermost qualifying line band: first run of >= MIN_LINE_PX
    # line columns starting inside the cap zone.
    band_start = band_end = None
    index = 0
    while index < cap:
        if not line_mask[index]:
            index += 1
            continue
        run_end = index
        while run_end < length and line_mask[run_end]:
            run_end += 1
        if run_end - index >= MIN_LINE_PX:
            band_start, band_end = index, run_end
            break
        index = run_end
    if band_start is None or band_end > cap:
        # No line, or a band too wide to clear inside the cap: leave
        # it whole rather than half-eating content-width colour.
        return 0
    trim = band_end
    # Anti-alias transition: a few uniform columns past the band are
    # part of the frame's edge treatment, not content.
    while (
        trim < length
        and trim - band_end < EXTENSION_MAX_PX
        and uniform_mask[trim]
    ):
        trim += 1
    trim = min(trim, cap)
    if face_lo is not None and face_lo < trim:
        # Never cross the face: stop at its first pixel when the whole
        # band still clears before it; when the face overlaps the band
        # itself the geometry is not furniture-shaped — leave it.
        trim = int(face_lo) if face_lo >= band_end else 0
    return max(0, trim)


def detect_window_trim(
    frames: Sequence[Any],
    face_box: Optional[Sequence[float]] = None,
) -> Dict[str, int]:
    """Border trims {left, right, top, bottom} in pixels for a window.

    ``frames`` are (H, W, 3) uint8 arrays of the SAME window (source
    pixels) sampled across a shot; ``face_box`` is the subject's face
    box (x, y, w, h) in window coordinates, or None. All-zero trims
    mean no furniture was found — the common case, and the guarantee
    that furniture-free sources plan exactly as before.
    """
    zero = {"left": 0, "right": 0, "top": 0, "bottom": 0}
    frames = [frame for frame in frames if frame is not None]
    if not frames:
        return zero
    height, width = int(frames[0].shape[0]), int(frames[0].shape[1])
    if height <= 0 or width <= 0:
        return zero
    masks = [_line_and_uniform_masks(f) for f in frames]
    col_line = _persistent([m[0] for m in masks])
    col_unif = _persistent([m[1] for m in masks])
    row_line = _persistent([m[2] for m in masks])
    row_unif = _persistent([m[3] for m in masks])
    if col_line is None or col_unif is None or row_line is None or row_unif is None:
        return zero

    face: Optional[Box] = None
    if face_box is not None:
        try:
            fx, fy, fw, fh = (float(v) for v in face_box)
        except (TypeError, ValueError):
            fx = fy = fw = fh = 0.0
        if fw > 0 and fh > 0:
            face = (fx, fy, fw, fh)

    # Face spans measured from each edge, for the face-stop.
    left_lo = face[0] if face else None
    right_lo = (width - (face[0] + face[2])) if face else None
    top_lo = face[1] if face else None
    bottom_lo = (height - (face[1] + face[3])) if face else None

    return {
        "left": _edge_trim(col_line, col_unif, width, left_lo),
        "right": _edge_trim(
            col_line[::-1], col_unif[::-1], width, right_lo
        ),
        "top": _edge_trim(row_line, row_unif, height, top_lo),
        "bottom": _edge_trim(
            row_line[::-1], row_unif[::-1], height, bottom_lo
        ),
    }


def grab_window_frames(
    source_path: Path,
    times: Sequence[float],
    window: Dict[str, Any],
) -> List[Any]:
    """Decode ``window``-cropped frames of the source at ``times``.

    Mirrors the broll_vision frame-grab pattern (ffmpeg does the seek
    and crop; no image library needed). Failed grabs are skipped —
    detection works with whatever frames it could get, including
    none (which yields zero trims).
    """
    import numpy as np

    tile_w = int(window.get("tile_w") or 0)
    tile_h = int(window.get("tile_h") or 0)
    tile_x = int(window.get("tile_x") or 0)
    tile_y = int(window.get("tile_y") or 0)
    if tile_w <= 0 or tile_h <= 0:
        return []
    frames: List[Any] = []
    expected = tile_w * tile_h * 3
    for at in times:
        try:
            result = subprocess.run(
                [
                    "ffmpeg", "-v", "error",
                    "-ss", f"{max(0.0, float(at)):.3f}",
                    "-i", str(source_path),
                    "-vf", f"crop={tile_w}:{tile_h}:{tile_x}:{tile_y}",
                    "-frames:v", "1",
                    "-f", "rawvideo", "-pix_fmt", "rgb24", "pipe:1",
                ],
                capture_output=True,
                timeout=30,
            )
        except Exception as exc:
            logger.debug("Border trim: frame grab failed: %s", exc)
            continue
        if result.returncode != 0 or len(result.stdout) < expected:
            continue
        frames.append(
            np.frombuffer(result.stdout[:expected], dtype=np.uint8).reshape(
                tile_h, tile_w, 3
            )
        )
    return frames
