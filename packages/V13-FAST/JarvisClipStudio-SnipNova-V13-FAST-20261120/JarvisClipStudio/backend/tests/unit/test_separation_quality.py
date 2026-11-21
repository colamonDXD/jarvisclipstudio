"""Real-model separation quality proof — OPT-IN, never in the default run.

Skipped unless SEPARATION_QUALITY_TEST=1 is set AND the demucs
package is importable in the test environment: the test downloads the
htdemucs weights (~84 MB, into SEPARATION_MODELS_DIR) on first use and
runs real CPU separation, which takes ~1–2 minutes for the 30 s
fixture on this sandbox. This mirrors the voiceover suite's discipline
— the default suite must never depend on a model download.

Method (the scoping lane's, research_notes/wave4-separation/test/
measure.py): a synthetic 30 s stereo mix with KNOWN ground truth —
flite speech in windows 2–9 s and 18–25 s over a constant generated
chord bed. Assertions use the build thresholds from the scoping, set
for real-model behaviour rather than the synthetic best case:

- music attenuation in the vocals stem (music-only window 10–16 s)
  must be >= 20 dB (mean volume, ffmpeg volumedetect);
- the vocals stem's level in a speech window (3–8 s) must sit within
  +/-3 dB of the true voice alone.
"""

import importlib.util
import os
import re
import subprocess
from pathlib import Path

import pytest

from src.media import separation

pytestmark = pytest.mark.skipif(
    os.environ.get("SEPARATION_QUALITY_TEST") != "1"
    or importlib.util.find_spec("demucs") is None,
    reason="real-model separation proof is opt-in (SEPARATION_QUALITY_TEST=1)",
)

SPEECH_1 = "The quick brown fox jumps over the lazy dog near the river bank"
SPEECH_2 = "A second spoken passage about markets, weather and travel plans"


def _run(command: list[str]) -> None:
    result = subprocess.run(command, capture_output=True, text=True)
    assert result.returncode == 0, f"{command[0]} failed: {result.stderr[-500:]}"


def _mean_volume_db(path: Path, start: float, duration: float) -> float:
    result = subprocess.run(
        [
            "ffmpeg", "-v", "info", "-ss", str(start), "-t", str(duration),
            "-i", str(path), "-af", "volumedetect", "-f", "null", "-",
        ],
        capture_output=True,
        text=True,
    )
    matches = re.findall(r"mean_volume:\s*(-?[\d.]+) dB", result.stderr)
    assert matches, f"no volumedetect reading for {path}"
    return float(matches[-1])


def _build_fixture(work: Path) -> tuple[Path, Path]:
    """30 s mix + the true voice track, following the scoping method."""
    sp1, sp2 = work / "sp1.wav", work / "sp2.wav"
    _run(["flite", "-t", SPEECH_1, "-o", str(sp1)])
    _run(["flite", "-t", SPEECH_2, "-o", str(sp2)])
    voice = work / "voice.wav"
    _run(
        [
            "ffmpeg", "-y",
            "-f", "lavfi", "-i", "anullsrc=r=44100:cl=stereo",
            "-i", str(sp1), "-i", str(sp2),
            "-filter_complex",
            "[1:a]adelay=2000|2000[a1];[2:a]adelay=18000|18000[a2];"
            "[0:a][a1][a2]amix=inputs=3:duration=first:dropout_transition=0,"
            "atrim=0:30,asetpts=N/SR/TB[out]",
            "-map", "[out]", str(voice),
        ]
    )
    music = work / "music.wav"
    _run(
        [
            "ffmpeg", "-y",
            "-f", "lavfi", "-i", "sine=frequency=220:duration=30",
            "-f", "lavfi", "-i", "sine=frequency=277.18:duration=30",
            "-f", "lavfi", "-i", "sine=frequency=329.63:duration=30",
            "-filter_complex",
            "[0:a][1:a][2:a]amix=inputs=3:duration=first:dropout_transition=0,"
            "volume=0.5,aresample=44100,aformat=channel_layouts=stereo[out]",
            "-map", "[out]", str(music),
        ]
    )
    mix = work / "mix.wav"
    _run(
        [
            "ffmpeg", "-y", "-i", str(voice), "-i", str(music),
            "-filter_complex",
            "[0:a][1:a]amix=inputs=2:duration=first:dropout_transition=0[out]",
            "-map", "[out]", str(mix),
        ]
    )
    return mix, voice


def test_real_model_separation_quality(monkeypatch, tmp_path):
    if not Path("/usr/bin/flite").exists():
        pytest.skip("flite is not available to build the speech fixture")
    monkeypatch.setenv("SEPARATION_ENABLED", "true")
    monkeypatch.setenv(
        "SEPARATION_MODELS_DIR", str(tmp_path / "models" / "huggingface")
    )
    mix, voice = _build_fixture(tmp_path)

    stem = separation.separate_vocals(mix)
    assert stem is not None and stem.exists()

    attenuation = _mean_volume_db(mix, 10, 6) - _mean_volume_db(stem, 10, 6)
    preservation = _mean_volume_db(stem, 3, 5) - _mean_volume_db(voice, 3, 5)
    print(f"\nmusic attenuation in vocals stem: {attenuation:.1f} dB")
    print(f"speech-window level vs true voice:  {preservation:+.1f} dB")
    assert attenuation >= 20.0
    assert abs(preservation) <= 3.0
