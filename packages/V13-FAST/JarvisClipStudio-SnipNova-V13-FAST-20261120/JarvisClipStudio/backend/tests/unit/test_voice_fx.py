"""Funny-voice effect keeps the clip length and changes the pitch."""
import subprocess

import numpy as np

from src.media.voice_fx import apply_voice_fx, voice_fx_filter


def _dur(p):
    return float(subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(p)],
                                capture_output=True, text=True).stdout)


def _peak_hz(p):
    raw = subprocess.run(["ffmpeg", "-v", "error", "-i", str(p), "-t", "2", "-ac", "1", "-ar", "16000", "-f", "s16le", "-"],
                         capture_output=True).stdout
    a = np.frombuffer(raw, np.int16).astype(float)
    return np.argmax(np.abs(np.fft.rfft(a))) * 16000 / len(a)


def test_high_voice_raises_pitch_and_keeps_length(tmp_path):
    clip = tmp_path / "c.mp4"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "sine=frequency=300:duration=4",
                    "-f", "lavfi", "-i", "color=c=black:s=160x284:d=4", "-shortest", "-c:v", "libx264", "-c:a", "aac", str(clip)],
                   check=True)
    d0, f0 = _dur(clip), _peak_hz(clip)
    assert apply_voice_fx(clip, "high") is True
    assert abs(_dur(clip) - d0) < 0.15
    assert _peak_hz(clip) > f0 * 1.25


def test_off_and_unknown_do_nothing(tmp_path):
    assert apply_voice_fx(tmp_path / "x.mp4", None) is False
    assert apply_voice_fx(tmp_path / "x.mp4", "robot") is False
    assert "asetrate=" in voice_fx_filter("deep") and "atempo=" in voice_fx_filter("deep")
