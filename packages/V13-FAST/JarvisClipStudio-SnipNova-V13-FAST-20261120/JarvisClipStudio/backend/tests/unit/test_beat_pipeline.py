"""End-to-end pipeline wiring for the beat-grid lane: profile-gated
silence tightening inside create_single_clip, proven on real renders
(a fast-pace profile cuts the dead air; a calm profile does not)."""

import json
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from src.services.video_service import VideoService


def _make_source(path: Path) -> None:
    subprocess.run(
        [
            "ffmpeg", "-v", "error", "-y",
            "-f", "lavfi", "-i", "testsrc2=size=320x568:rate=30",
            "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=44100",
            "-t", "10",
            "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
            "-c:a", "aac",
            str(path),
        ],
        check=True, capture_output=True, timeout=300,
    )
    # Words at 0-1s and 4-5s (millisecond stamps, the cache format):
    # a 3-second silence inside the clip and 5 dead seconds after.
    words = [
        {"text": "alpha", "start": 0, "end": 500, "confidence": 1.0, "speaker": None},
        {"text": "bravo", "start": 500, "end": 1000, "confidence": 1.0, "speaker": None},
        {"text": "charlie", "start": 4000, "end": 4500, "confidence": 1.0, "speaker": None},
        {"text": "delta", "start": 4500, "end": 5000, "confidence": 1.0, "speaker": None},
    ]
    cache = {
        "version": 2,
        "words": words,
        "utterances": [],
        "text": "alpha bravo charlie delta",
        "language_code": "en",
    }
    path.with_suffix(".transcript_cache.json").write_text(json.dumps(cache))


def _duration(path: Path) -> float:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_format", "-of", "json", str(path)],
        check=True, capture_output=True, text=True, timeout=60,
    )
    return float(json.loads(out.stdout)["format"]["duration"])


SEGMENT = {
    "start_time": "00:00",
    "end_time": "00:10",
    "text": "alpha bravo charlie delta",
}


@pytest.mark.asyncio
async def test_fast_profile_tightens_silence_calm_profile_does_not(tmp_path):
    source = tmp_path / "source.mp4"
    _make_source(source)
    (tmp_path / "out").mkdir()

    gaming = await VideoService.create_single_clip(
        source, dict(SEGMENT), 0, tmp_path / "out", niche_override="gaming"
    )
    assert gaming is not None
    gaming_duration = _duration(Path(gaming["path"]))
    # Kept speech is ~2.5s of ranges; the 3s gap and trailing 5s of
    # dead air must be gone (an untightened render is ~10s).
    assert gaming_duration < 6.0

    podcast = await VideoService.create_single_clip(
        source, dict(SEGMENT), 1, tmp_path / "out", niche_override="podcast_news"
    )
    assert podcast is not None
    podcast_duration = _duration(Path(podcast["path"]))
    assert podcast_duration > 8.5
