"""Burned-in caption detection on synthetic clips with known captions.

Each fixture is a real H.264 file built with ffmpeg ``drawtext``:

* changing words in a bottom band,
* changing words in a top band,
* static channel text in both bands,
* a static caption in the bottom band alone,
* moving test video with no text,
* moving test video with heavy noise (a busy texture),
* moving test video with a static channel tag.

The detector must find the two changing bands and reject every
non-changing case. That is the contract the render relies on: only a
band whose glyphs actually change may push our own captions away.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from src.media.burned_captions import (
    CaptionBand,
    candidates_in_frame,
    detect_burned_caption_band,
)

WIDTH = 320
HEIGHT = 240
RATE = 20
SECONDS = 6.0
WORDS = [
    "ONE", "TWO", "THREE", "FOUR", "FIVE", "SIX",
    "SEVEN", "EIGHT", "NINE", "TEN", "ELEVEN", "TWELVE",
]
FONT = Path(__file__).resolve().parents[2] / "fonts" / "Anton-Regular.ttf"
SAMPLE_TIMES = [SECONDS * (index + 0.5) / 12 for index in range(12)]

requires_ffmpeg = pytest.mark.skipif(
    shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None,
    reason="ffmpeg and ffprobe are required",
)


def _font_option() -> str:
    # FFmpeg's filter parser treats ':' as an option separator; a
    # Windows drive letter has to be escaped even inside quotes.
    return str(FONT).replace("\\", "/").replace(":", "\\:")


def _caption(word: str, y: int, size: int = 30) -> str:
    return (
        f"drawtext=fontfile='{_font_option()}':text='{word}':"
        f"fontcolor=white:fontsize={size}:borderw=3:bordercolor=black:"
        f"x=(w-text_w)/2:y={y}"
    )


def _changing_words(y: int) -> str:
    parts = []
    for index, word in enumerate(WORDS):
        start = index * 0.5
        parts.append(
            _caption(word, y)
            + f":enable=between(t\\,{start}\\,{start + 0.5})"
        )
    return ",".join(parts)


def _build(
    directory: Path,
    name: str,
    source: str,
    filters: str = "",
) -> Path:
    target = directory / f"{name}.mp4"
    command = [
        "ffmpeg", "-y", "-v", "error",
        "-f", "lavfi", "-i", source,
    ]
    if filters:
        command += ["-vf", filters]
    command += [
        "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
        str(target),
    ]
    rendered = subprocess.run(
        command, capture_output=True, text=True, timeout=120
    )
    assert rendered.returncode == 0, (
        f"could not build {name}:\n{rendered.stderr[-1200:]}"
    )
    return target


def _colour() -> str:
    return f"color=c=0x24344E:s={WIDTH}x{HEIGHT}:r={RATE}:d={SECONDS}"


def _motion() -> str:
    return f"testsrc2=size={WIDTH}x{HEIGHT}:rate={RATE}:duration={SECONDS}"


@pytest.fixture(scope="module")
def clips(tmp_path_factory) -> dict[str, Path]:
    directory = tmp_path_factory.mktemp("burned-captions")
    assert FONT.is_file(), f"missing test font: {FONT}"
    return {
        "bottom": _build(directory, "bottom", _colour(), _changing_words(190)),
        "top": _build(directory, "top", _colour(), _changing_words(10)),
        "static_both": _build(
            directory,
            "static_both",
            _colour(),
            _caption("FOLLOW US", 12, 28) + "," + _caption("@channel", 200, 22),
        ),
        "static_bottom": _build(
            directory, "static_bottom", _colour(), _caption("FOLLOW US", 190, 30)
        ),
        "no_text": _build(directory, "no_text", _motion()),
        "texture": _build(
            directory, "texture", _motion(), "noise=alls=45:allf=t"
        ),
        "moving_static": _build(
            directory,
            "moving_static",
            _motion(),
            _caption("@channel", 8, 26),
        ),
    }


@requires_ffmpeg
def test_finds_a_changing_bottom_band(clips):
    band = detect_burned_caption_band(
        clips["bottom"], SAMPLE_TIMES, WIDTH, HEIGHT
    )
    assert isinstance(band, CaptionBand)
    assert abs(band.y - 187) <= 8, band
    assert abs(band.h - 33) <= 10, band
    assert band.frames_with_text >= 10, band
    assert band.confidence >= 0.7, band


@requires_ffmpeg
def test_finds_a_changing_top_band(clips):
    band = detect_burned_caption_band(
        clips["top"], SAMPLE_TIMES, WIDTH, HEIGHT
    )
    assert isinstance(band, CaptionBand)
    assert abs(band.y - 7) <= 8, band
    assert band.h <= 48, band
    assert band.frames_with_text >= 10, band


@requires_ffmpeg
def test_rejects_static_text_in_both_bands(clips):
    assert (
        detect_burned_caption_band(
            clips["static_both"], SAMPLE_TIMES, WIDTH, HEIGHT
        )
        is None
    )


@requires_ffmpeg
def test_rejects_static_text_in_one_band(clips):
    assert (
        detect_burned_caption_band(
            clips["static_bottom"], SAMPLE_TIMES, WIDTH, HEIGHT
        )
        is None
    )


@requires_ffmpeg
def test_rejects_video_without_text(clips):
    assert (
        detect_burned_caption_band(
            clips["no_text"], SAMPLE_TIMES, WIDTH, HEIGHT
        )
        is None
    )


@requires_ffmpeg
def test_rejects_a_busy_texture(clips):
    assert (
        detect_burned_caption_band(
            clips["texture"], SAMPLE_TIMES, WIDTH, HEIGHT
        )
        is None
    )


@requires_ffmpeg
def test_rejects_static_text_over_moving_video(clips):
    assert (
        detect_burned_caption_band(
            clips["moving_static"], SAMPLE_TIMES, WIDTH, HEIGHT
        )
        is None
    )


def test_a_blank_frame_has_no_candidates():
    import numpy as np

    assert candidates_in_frame(np.zeros((HEIGHT, WIDTH, 3), np.uint8)) == []
