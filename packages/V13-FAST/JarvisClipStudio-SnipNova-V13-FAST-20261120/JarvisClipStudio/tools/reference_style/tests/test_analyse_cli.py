"""Smoke test for the reference-style CLI (tools/reference_style/analyse.py).

Builds a tiny two-shot reference, runs the CLI entry point, and checks the
measurements + profile JSON pair it writes - including that the profile's
niche is one the app's loader knows.
"""

import json
import subprocess
import sys
import wave
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import analyse  # noqa: E402

FONT = (Path(__file__).resolve().parents[3]
        / "backend" / "fonts" / "ArchivoBlack-Regular.ttf")
NICHES = {
    "podcast_news", "travel_vlog", "meme_comedy", "gaming", "sports",
    "education_tutorial", "finance_business", "vlog_lifestyle", "true_crime",
    "hindi_podcast", "music_artist", "football_edit", "japan_travel_cinematic",
    "anime_funk_edit",
}


def _tiny_reference(path: Path):
    sr = 44100
    t = np.arange(sr * 4) / sr
    samples = (0.3 * np.sin(2 * np.pi * 220 * t)).astype(np.float64)
    wav_path = path.with_suffix(".wav")
    with wave.open(str(wav_path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes((samples * 32767).astype(np.int16).tobytes())
    cmd = [
        "ffmpeg", "-v", "error", "-y",
        "-f", "lavfi", "-i", "color=c=0x2255AA:s=320x568:r=25:d=2",
        "-f", "lavfi", "-i", "color=c=0xAA5522:s=320x568:r=25:d=2",
        "-i", str(wav_path),
        "-filter_complex",
        f"[0:v][1:v]concat=n=2:v=1:a=0,drawtext=fontfile={FONT}:text='HI':"
        "fontsize=36:fontcolor=white:x=(w-text_w)/2:y=h-100[v]",
        "-map", "[v]", "-map", "2:a", "-c:v", "libx264", "-preset", "ultrafast",
        "-crf", "28", "-c:a", "aac", "-shortest", str(path),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr[-1000:]


def test_cli_writes_measurements_and_profile(tmp_path):
    video = tmp_path / "tiny.mp4"
    _tiny_reference(video)
    out_dir = tmp_path / "out"

    assert analyse.main([str(video), "--name", "smoke style",
                         "--out-dir", str(out_dir)]) == 0

    measurements = json.loads(
        (out_dir / "smoke-style.measurements.json").read_text(encoding="utf-8"))
    profile = json.loads(
        (out_dir / "smoke-style.profile.json").read_text(encoding="utf-8"))
    assert {"cuts", "captions", "colour", "audio", "hook"} <= set(
        measurements["measurements"].keys())
    assert profile["niche"] in NICHES
    assert profile["reference_style"]["name"] == "smoke style"
    for section in ("pacing", "transitions", "zooms", "captions", "overlays",
                    "broll", "audio", "color", "hook"):
        assert section in profile


def test_cli_rejects_missing_file(tmp_path):
    assert analyse.main([str(tmp_path / "nope.mp4")]) == 2
