"""R1/R2 render clamp (design §2.5): real tiny renders via create_single_clip.

The leak case matters most: the sentence-boundary extension wants to
push a clip end past the allowed boundary, and the sidecar (render
truth) must still show 100% in-range footage.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from src.clip_source_map import load_clip_source_ranges
from src.services.video_service import VideoService

ffmpeg_missing = shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None
pytestmark = pytest.mark.skipif(ffmpeg_missing, reason="ffmpeg/ffprobe not installed")


def _make_source(path: Path, seconds: int = 20) -> None:
    result = subprocess.run(
        [
            "ffmpeg", "-v", "error", "-y",
            "-f", "lavfi", "-i", f"testsrc2=size=640x360:rate=10:duration={seconds}",
            "-f", "lavfi", "-i", f"sine=frequency=440:sample_rate=44100:duration={seconds}",
            "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
            "-c:a", "aac", str(path),
        ],
        capture_output=True, text=True, timeout=300,
    )
    assert result.returncode == 0, result.stderr


def _seed_words(path: Path, upto: float = 20.0) -> None:
    words = []
    t = 0.0
    while t < upto:
        text = "word"
        if abs(t - 17.5) < 0.26:
            text = "done."
        words.append(
            {"text": text, "start": int(t * 1000), "end": int((t + 0.5) * 1000),
             "confidence": 1.0, "speaker": None}
        )
        t += 0.5
    path.with_suffix(".transcript_cache.json").write_text(
        json.dumps({"words": words, "utterances": []}), encoding="utf-8"
    )


async def _render(tmp_path, segment, allowed):
    source = tmp_path / "source.mp4"
    _make_source(source)
    _seed_words(source)
    out = tmp_path / "out"
    out.mkdir(exist_ok=True)
    return await VideoService.create_single_clip(
        source, segment, 0, out,
        niche_override="podcast_news",
        allowed_ranges=allowed,
    )


async def test_r1_intersects_provided_ranges(tmp_path):
    segment = {
        "start_time": "00:00", "end_time": "00:20",
        "text": "word " * 40, "keep_ranges": [[0.0, 20.0]],
    }
    clip_info = await _render(tmp_path, segment, [(5.0, 15.0)])
    assert clip_info is not None
    ranges = load_clip_source_ranges(Path(clip_info["path"]))
    assert ranges
    for start, end in ranges:
        assert start >= 5.0 - 0.05 and end <= 15.0 + 0.05


async def test_r1_blocks_sentence_extension_leak(tmp_path):
    # Keep range ends at 14s mid-sentence; the extension mutator wants
    # to run to the sentence end at ~18s. Allowed range ends at 15s.
    segment = {
        "start_time": "00:00", "end_time": "00:14",
        "text": "word " * 30, "keep_ranges": [[0.0, 14.0]],
    }
    clip_info = await _render(tmp_path, segment, [(0.0, 15.0)])
    assert clip_info is not None
    ranges = load_clip_source_ranges(Path(clip_info["path"]))
    assert ranges
    for start, end in ranges:
        assert end <= 15.0 + 0.05, f"extension leaked out-of-range footage: {ranges}"


async def test_r1_empty_intersection_renders_nothing(tmp_path):
    # A 12s segment entirely outside the allowed range, with the allowed
    # range beyond even the sentence-boundary extension's reach (the
    # seeded sentence ends at ~18s), so the intersection is truly empty.
    segment = {
        "start_time": "00:00", "end_time": "00:12",
        "text": "word " * 24, "keep_ranges": [[0.0, 12.0]],
    }
    clip_info = await _render(tmp_path, segment, [(19.0, 20.0)])
    assert clip_info is None
    assert not list((tmp_path / "out").glob("*.mp4"))
