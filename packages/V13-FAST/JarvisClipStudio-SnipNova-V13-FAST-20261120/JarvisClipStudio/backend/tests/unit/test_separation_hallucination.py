"""Hallucination probe — music-only audio must yield NO stem-fed words.

OPT-IN, never in the default run: skipped unless
SEPARATION_HALLUCINATION_TEST=1 is set AND both demucs and
faster-whisper are importable in the test environment. The run
separates a 24 s synthetic fixture with the real htdemucs model (CPU,
~1 min) and transcribes it twice with the real faster-whisper engine,
so it belongs to the same family as test_separation_quality.py — the
default suite must never depend on model downloads or minutes of CPU.

Why this test exists (separation Stage 3): the feature's founding
claim is that Whisper invents words during music-only stretches and
that feeding it the separated vocals stem stops that. Stages 1–2
proved attenuation (dB) and word counts on a real source whose
music-only windows were labelled by ear; this harness is the decisive
synthetic version — a fixture that contains NO speech anywhere, so
every word either leg emits is a hallucination by construction:

- the stem-fed leg must emit ZERO words (the decisive assertion);
- the stem leg must not emit MORE words than the raw leg;
- an attenuation floor (>= 15 dB, whole file) proves the separator
  actually engaged, so a silent-by-accident pass is impossible: if
  Demucs failed to remove the music, the word assertions would be
  measuring the wrong stem and the floor fails first.

The transcription legs use the production composition —
separation.separate_vocals() for the stem, then
transcription.transcribe_with_faster_whisper(..., vocals_stem=stem),
which is exactly what get_video_transcript() dispatches to for an
opted-in task on the faster-whisper provider.

Model choice: SEPARATION_HALLUCINATION_WHISPER_MODEL (default
"base") — deliberately the small cached model so the probe stays
cheap; hallucination counts are model-dependent and the report must
name the model used. The user-machine check at the app's configured
model size is the benchmark pack's separation-scenario.ps1.
"""

import importlib.util
import math
import os
import re
import struct
import subprocess
import wave
from pathlib import Path

import pytest

from src.media import separation, transcription

pytestmark = pytest.mark.skipif(
    os.environ.get("SEPARATION_HALLUCINATION_TEST") != "1"
    or importlib.util.find_spec("demucs") is None
    or importlib.util.find_spec("faster_whisper") is None,
    reason=(
        "real-model hallucination probe is opt-in "
        "(SEPARATION_HALLUCINATION_TEST=1, demucs + faster-whisper required)"
    ),
)

SAMPLE_RATE = 44100
DURATION_SECONDS = 24
# Am - F - C - G, one chord per 6 s: fundamentals of a pad stack.
CHORDS = [
    (220.00, 261.63, 329.63),
    (174.61, 220.00, 261.63),
    (261.63, 329.63, 392.00),
    (196.00, 246.94, 392.00),
]
# A simple melody line over the top (pentatonic steps, 1.5 s each).
MELODY = [440.00, 523.25, 659.25, 523.25, 440.00, 349.23, 440.00, 523.25,
          587.33, 659.25, 783.99, 659.25, 587.33, 523.25, 440.00, 392.00]


def _build_music_only_fixture(path: Path) -> Path:
    """24 s of instrumental music: chord pads + a melody line, no voice.

    Pure-Python synthesis (stdlib wave) so the fixture carries zero
    speech-like content by construction — no TTS, no samples, nothing
    a recogniser could legitimately transcribe.
    """
    frames = bytearray()
    chord_len = SAMPLE_RATE * 6
    total = SAMPLE_RATE * DURATION_SECONDS
    for i in range(total):
        t = i / SAMPLE_RATE
        chord = CHORDS[min(3, i // chord_len)]
        local = (i % chord_len) / chord_len
        # Slow attack/release per chord so the pad has no clicks.
        envelope = min(1.0, local * 8.0, (1.0 - local) * 8.0)
        pad = sum(math.sin(2.0 * math.pi * f * t) for f in chord) / len(chord)
        melody_note = MELODY[min(len(MELODY) - 1, int(t // 1.5))]
        melody_phase = t % 1.5
        melody_env = math.sin(math.pi * min(1.0, melody_phase / 1.5))
        melody = math.sin(2.0 * math.pi * melody_note * t) * melody_env
        sample = 0.45 * envelope * pad + 0.25 * melody
        # A whisper of stereo width: right channel detuned by 1.001x.
        right = 0.45 * envelope * sum(
            math.sin(2.0 * math.pi * f * 1.001 * t) for f in chord
        ) / len(chord) + 0.25 * melody
        frames += struct.pack(
            "<hh",
            int(max(-1.0, min(1.0, sample)) * 32767),
            int(max(-1.0, min(1.0, right)) * 32767),
        )
    with wave.open(str(path), "wb") as wav:
        wav.setnchannels(2)
        wav.setsampwidth(2)
        wav.setframerate(SAMPLE_RATE)
        wav.writeframes(bytes(frames))
    return path


def _mean_volume_db(path: Path) -> float:
    result = subprocess.run(
        [
            "ffmpeg", "-v", "info", "-i", str(path),
            "-af", "volumedetect", "-f", "null", "-",
        ],
        capture_output=True,
        text=True,
    )
    matches = re.findall(r"mean_volume:\s*(-?[\d.]+) dB", result.stderr)
    assert matches, f"no volumedetect reading for {path}"
    return float(matches[-1])


def _words(result: dict) -> list[str]:
    return [
        word["word"]
        for segment in result.get("segments") or []
        for word in segment.get("words") or []
    ]


def test_music_only_fixture_produces_no_stem_fed_words(monkeypatch, tmp_path):
    monkeypatch.setenv("SEPARATION_ENABLED", "true")
    # Honour an operator-provided models dir (e.g. a pre-seeded cache);
    # otherwise isolate the download into the test's tmp tree.
    if not os.environ.get("SEPARATION_MODELS_DIR"):
        monkeypatch.setenv(
            "SEPARATION_MODELS_DIR", str(tmp_path / "models" / "huggingface")
        )
    model_name = os.environ.get("SEPARATION_HALLUCINATION_WHISPER_MODEL", "base")

    music = _build_music_only_fixture(tmp_path / "music_only.wav")

    stem = separation.separate_vocals(music)
    assert stem is not None and stem.exists(), "separation must engage"

    attenuation = _mean_volume_db(music) - _mean_volume_db(stem)
    print(f"\nmusic-only fixture attenuation in vocals stem: {attenuation:.1f} dB")
    assert attenuation >= 15.0, (
        "separator did not materially remove the music "
        f"({attenuation:.1f} dB); word counts would be meaningless"
    )

    raw_words = _words(
        transcription.transcribe_with_faster_whisper(music, model_name=model_name)
    )
    stem_words = _words(
        transcription.transcribe_with_faster_whisper(
            music, model_name=model_name, vocals_stem=stem
        )
    )
    print(f"whisper model: {model_name}")
    print(f"raw-mix leg:  {len(raw_words)} words {raw_words}")
    print(f"stem-fed leg: {len(stem_words)} words {stem_words}")

    assert len(stem_words) <= len(raw_words)
    assert stem_words == [], (
        f"stem-fed transcription hallucinated {len(stem_words)} word(s) "
        f"into pure music: {stem_words}"
    )
