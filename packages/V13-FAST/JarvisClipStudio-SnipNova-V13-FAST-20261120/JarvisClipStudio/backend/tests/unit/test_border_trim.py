"""Source-border trim detector — unit proof on synthetic windows.

The detector (media.border_trim) reads sampled frames of ONE tile
window and answers how many pixels to inset each edge so the source's
own border furniture (a podcast feed's frame line, a letterbox edge)
falls outside the window. These tests pin the geometry rules with
numpy windows whose columns/rows are built to known statistics:

- an extreme, full-span, temporally persistent line band is trimmed,
  together with everything outside it (the border design beyond the
  line is furniture by construction);
- textured edges, bands that start or end beyond the 6% cap, and
  one-frame wonders are left whole;
- the trim never crosses the subject's face box: it stops at the
  face's first pixel when only the anti-alias extension would reach
  it, and is dropped when the face overlaps the line band itself.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import numpy as np
import pytest

from src.media.border_trim import (
    border_trim_enabled,
    detect_window_trim,
    grab_window_frames,
)

H, W = 300, 1000  # cap = 60 px per side


def _content(rng_seed: int = 7) -> np.ndarray:
    """Textured content: per-column means/stds like real footage."""
    rng = np.random.default_rng(rng_seed)
    return rng.integers(30, 220, (H, W, 3), dtype=np.uint8)


def _frames(base: np.ndarray, count: int = 4) -> list:
    return [base.copy() for _ in range(count)]


def _paint_col(frame: np.ndarray, x0: int, x1: int, colour) -> None:
    frame[:, x0:x1] = colour


def _paint_row(frame: np.ndarray, y0: int, y1: int, colour) -> None:
    frame[y0:y1, :] = colour


WHITE = (255, 255, 255)
BLACK = (0, 0, 0)


def test_white_band_behind_art_strip_trims_through_the_band():
    frame = _content()
    _paint_col(frame, 0, 20, (40, 45, 40))  # border art strip (not a line)
    _paint_col(frame, 20, 32, WHITE)  # the feed's frame line
    result = detect_window_trim(_frames(frame))
    # Everything from the edge through the line's far side (32) goes;
    # the noisy content column at 32 stops any extension.
    assert result["left"] == 32
    assert result["right"] == 0
    assert result["top"] == 0
    assert result["bottom"] == 0


def test_flush_white_band_at_the_edge():
    frame = _content()
    _paint_col(frame, 0, 8, WHITE)
    assert detect_window_trim(_frames(frame))["left"] == 8


def test_flush_black_band_on_the_right():
    frame = _content()
    _paint_col(frame, W - 8, W, BLACK)
    result = detect_window_trim(_frames(frame))
    assert result["right"] == 8
    assert result["left"] == 0


def test_black_letterbox_rows_top_and_bottom():
    frame = _content()
    _paint_row(frame, 0, 12, BLACK)
    _paint_row(frame, H - 10, H, BLACK)
    result = detect_window_trim(_frames(frame))
    assert result["top"] == 12
    assert result["bottom"] == 10
    assert result["left"] == 0


def test_textured_edges_yield_zero():
    assert detect_window_trim(_frames(_content())) == {
        "left": 0,
        "right": 0,
        "top": 0,
        "bottom": 0,
    }
    # A smooth dark gradient (podcast curtain at a tile edge, the Bert
    # bottom tile's shape) is content, not furniture.
    gradient = np.tile(
        np.linspace(40, 90, W, dtype=np.uint8)[None, :, None], (H, 1, 3)
    )
    gradient = np.concatenate(
        [gradient, gradient + 5, gradient + 2], axis=2
    ).astype(np.uint8)
    assert detect_window_trim(_frames(gradient))["left"] == 0
    assert detect_window_trim(_frames(gradient))["right"] == 0


def test_band_starting_beyond_the_cap_is_left_whole():
    frame = _content()
    _paint_col(frame, 70, 82, WHITE)  # starts past the 60 px cap zone
    assert detect_window_trim(_frames(frame))["left"] == 0


def test_band_ending_beyond_the_cap_is_left_whole():
    frame = _content()
    _paint_col(frame, 50, 66, WHITE)  # starts inside, ends past the cap
    assert detect_window_trim(_frames(frame))["left"] == 0


def test_one_frame_wonder_is_not_persistent():
    frames = _frames(_content())
    _paint_col(frames[0], 20, 32, WHITE)
    assert detect_window_trim(frames)["left"] == 0


def test_band_in_three_of_four_frames_is_persistent():
    frames = _frames(_content())
    for frame in frames[:3]:
        _paint_col(frame, 20, 32, WHITE)
    assert detect_window_trim(frames)["left"] == 32


def test_face_stop_extension_clamps_at_the_face():
    frame = _content()
    _paint_col(frame, 20, 32, WHITE)  # line band ends at 32
    _paint_col(frame, 32, 60, (120, 120, 120))  # flat transition after it
    # Face starts at x=34: the +3 extension (to 35) would cross it, so
    # the trim stops at the face's first pixel instead.
    result = detect_window_trim(_frames(frame), face_box=(34, 80, 90, 110))
    assert result["left"] == 34


def test_face_overlapping_the_band_drops_the_trim():
    frame = _content()
    _paint_col(frame, 20, 32, WHITE)
    result = detect_window_trim(_frames(frame), face_box=(25, 80, 90, 110))
    assert result["left"] == 0


def test_face_before_the_band_drops_the_trim():
    frame = _content()
    _paint_col(frame, 20, 32, WHITE)
    result = detect_window_trim(_frames(frame), face_box=(5, 80, 10, 110))
    assert result["left"] == 0


def test_face_far_from_the_band_keeps_the_trim():
    frame = _content()
    _paint_col(frame, 20, 32, WHITE)
    result = detect_window_trim(_frames(frame), face_box=(300, 80, 90, 110))
    assert result["left"] == 32


def test_no_frames_and_malformed_face_yield_zero():
    assert detect_window_trim([]) == {"left": 0, "right": 0, "top": 0, "bottom": 0}
    frame = _content()
    _paint_col(frame, 20, 32, WHITE)
    assert detect_window_trim(_frames(frame), face_box=("a", "b"))["left"] == 32


def test_enabled_unless_explicitly_switched_off(monkeypatch):
    monkeypatch.delenv("BORDER_TRIM_ENABLED", raising=False)
    assert border_trim_enabled() is True
    for value in ("0", "false", "off", "no", "OFF"):
        monkeypatch.setenv("BORDER_TRIM_ENABLED", value)
        assert border_trim_enabled() is False
    monkeypatch.setenv("BORDER_TRIM_ENABLED", "1")
    assert border_trim_enabled() is True


def test_grab_window_frames_decodes_the_crop(tmp_path):
    source = tmp_path / "tiny.mp4"
    subprocess.run(
        [
            "ffmpeg", "-v", "error", "-y",
            "-f", "lavfi", "-i", "color=c=0x336699:size=320x240:rate=10:duration=1",
            "-f", "lavfi", "-i", "sine=frequency=440:duration=1",
            "-c:v", "libx264", "-preset", "veryfast", "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-shortest", str(source),
        ],
        check=True,
        capture_output=True,
    )
    frames = grab_window_frames(
        Path(source), [0.2, 0.6], {"tile_x": 10, "tile_y": 20, "tile_w": 100, "tile_h": 80}
    )
    assert len(frames) == 2
    assert frames[0].shape == (80, 100, 3)
    # A window outside the frame yields nothing, never an exception.
    assert grab_window_frames(
        Path(source), [0.2], {"tile_x": 0, "tile_y": 0, "tile_w": 0, "tile_h": 0}
    ) == []
