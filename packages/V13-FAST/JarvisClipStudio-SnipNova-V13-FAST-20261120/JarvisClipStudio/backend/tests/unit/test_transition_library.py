"""The extended transition registry renders and honours the join contract.

Every entry in ``src.media.transition_library.TRANSITIONS`` is a filter
graph that takes two equal-size clips ``[a]``/``[b]`` and produces
``[out]``. These tests render each one for real at 108x192 / 10 fps and
pin the contract the renderer will rely on:

* the graph needs no external assets and no GPU,
* the output lasts ``2 * card - duration`` (the same shape as ``xfade``),
* the first frame is the outgoing clip and the last frame is the
  incoming one.

Rendering at 108x192 keeps the matrix cheap; the proof reel in
``tools/transitions-proof/make_library_reel.py`` renders the same entries
at 1080x1920 and measures how distinct they look.
"""

from __future__ import annotations

import json
import shutil
import subprocess

import pytest

from src.media.transition_library import TRANSITIONS

WIDTH = 108
HEIGHT = 192
FPS = 10.0
CARD_SECONDS = 1.0
TRANSITION_SECONDS = 0.5
OFFSET_SECONDS = CARD_SECONDS - TRANSITION_SECONDS
EXPECTED_DURATION = 2 * CARD_SECONDS - TRANSITION_SECONDS
EXPECTED_FRAMES = int(round(EXPECTED_DURATION * FPS))

A_HEX = "0x1E3A8A"
B_HEX = "0xD97706"
A_RGB = (0x1E, 0x3A, 0x8A)
B_RGB = (0xD9, 0x77, 0x06)

REQUIRED_FAMILIES = {
    "barn",
    "blend",
    "blinds",
    "blocks",
    "channel",
    "checker",
    "clock",
    "corner",
    "diagonal-blinds",
    "diamond",
    "frame",
    "glitch",
    "grade",
    "gradient",
    "grid",
    "hue",
    "iris",
    "light",
    "mirror",
    "mosaic",
    "motion",
    "multi-iris",
    "pixel",
    "pinwheel",
    "polygon",
    "ripple",
    "rotate",
    "shutter",
    "spiral",
    "star",
    "strobe",
    "wave",
    "zigzag",
}

requires_ffmpeg = pytest.mark.skipif(
    shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None,
    reason="ffmpeg and ffprobe are required",
)


def _centre_offset() -> int:
    return ((HEIGHT // 2) * WIDTH + WIDTH // 2) * 3


def _distance(left: tuple[int, int, int], right: tuple[int, int, int]) -> int:
    return sum((a - b) ** 2 for a, b in zip(left, right))


def _render(output, name: str) -> tuple[float, bytes]:
    fragment = TRANSITIONS[name].builder(
        TRANSITION_SECONDS, OFFSET_SECONDS, WIDTH, HEIGHT, FPS
    )
    assert "[a]" in fragment and "[b]" in fragment
    assert fragment.rstrip().endswith("[out]")
    graph = f"[0:v]null[a];[1:v]null[b];{fragment}"
    command = [
        "ffmpeg",
        "-y",
        "-v",
        "error",
        "-f",
        "lavfi",
        "-i",
        f"color=c={A_HEX}:s={WIDTH}x{HEIGHT}:r={FPS}:d={CARD_SECONDS}",
        "-f",
        "lavfi",
        "-i",
        f"color=c={B_HEX}:s={WIDTH}x{HEIGHT}:r={FPS}:d={CARD_SECONDS}",
        "-filter_complex",
        graph,
        "-map",
        "[out]",
        "-c:v",
        "libx264",
        "-preset",
        "ultrafast",
        "-pix_fmt",
        "yuv420p",
        str(output),
    ]
    rendered = subprocess.run(
        command, capture_output=True, text=True, timeout=120
    )
    assert rendered.returncode == 0, (
        f"{name} did not render:\n{rendered.stderr[-1200:]}"
    )

    probe = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "json",
            str(output),
        ],
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert probe.returncode == 0, probe.stderr[-600:]
    duration = float(
        json.loads(probe.stdout or "{}")["format"]["duration"]
    )

    raw = subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-i",
            str(output),
            "-f",
            "rawvideo",
            "-pix_fmt",
            "rgb24",
            "-",
        ],
        capture_output=True,
        timeout=120,
    )
    assert raw.returncode == 0, raw.stderr.decode("utf-8", "replace")[-600:]
    return duration, raw.stdout


def test_every_name_is_unique_and_documented():
    described = {}
    for key, spec in TRANSITIONS.items():
        assert spec.name == key
        assert key.strip(), "empty transition name"
        assert spec.family.strip(), f"{key}: empty family"
        assert spec.description.strip(), f"{key}: empty description"
        assert callable(spec.builder), f"{key}: builder is not callable"
        assert described.setdefault(spec.name, spec.family) == spec.family, (
            f"{key}: duplicate name"
        )


def test_the_registry_covers_the_requested_families():
    families = {spec.family for spec in TRANSITIONS.values()}
    missing = REQUIRED_FAMILIES - families
    assert not missing, f"missing transition families: {sorted(missing)}"


def test_builders_are_deterministic():
    for name, spec in TRANSITIONS.items():
        first = spec.builder(
            TRANSITION_SECONDS, OFFSET_SECONDS, WIDTH, HEIGHT, FPS
        )
        second = spec.builder(
            TRANSITION_SECONDS, OFFSET_SECONDS, WIDTH, HEIGHT, FPS
        )
        assert first == second, f"{name}: builder output changed"
        assert "[out]" in first


@requires_ffmpeg
@pytest.mark.parametrize("name", sorted(TRANSITIONS))
def test_transition_renders_with_the_expected_timing_and_sides(
    name: str, tmp_path
):
    output = tmp_path / f"{name}.mp4"
    duration, raw = _render(output, name)

    assert abs(duration - EXPECTED_DURATION) <= 0.15, (
        f"{name}: duration {duration:.3f}s, expected "
        f"{EXPECTED_DURATION:.3f}s"
    )

    frame_bytes = WIDTH * HEIGHT * 3
    assert len(raw) % frame_bytes == 0, f"{name}: partial frame in raw output"
    frames = len(raw) // frame_bytes
    assert frames == EXPECTED_FRAMES, (
        f"{name}: {frames} frames, expected {EXPECTED_FRAMES}"
    )

    middle = _centre_offset()
    first = tuple(raw[middle:middle + 3])
    last_offset = (frames - 1) * frame_bytes + middle
    last = tuple(raw[last_offset:last_offset + 3])

    assert _distance(first, A_RGB) < _distance(first, B_RGB), (
        f"{name}: first frame {first} is not the outgoing card"
    )
    assert _distance(last, B_RGB) < _distance(last, A_RGB), (
        f"{name}: last frame {last} is not the incoming card"
    )
