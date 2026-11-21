"""Audio-peaks merge: one full-source audio decode per task, not two.

detect_audio_peak_times used to decode the source's audio with astats
even though transcription had already extracted a mono 16 kHz MP3 of
the same selected track beside the source. The peaks pass now reads
that MP3 when it exists. The unit tests pin the file selection; the
real-ffmpeg test proves peak parity -- the loud moments the clip
signal summary consumes land at the same times from either file.
"""

import shutil
import subprocess
from pathlib import Path

import pytest

from src.media import timeline, transcription

_STDERR = """\
pts_time:1.000
lavfi.astats.Overall.RMS_level=-10.0
pts_time:9.000
lavfi.astats.Overall.RMS_level=-5.0
pts_time:20.000
lavfi.astats.Overall.RMS_level=-30.0
"""


def _capture(monkeypatch):
    commands = []

    def fake_run(command, timeout=600):
        commands.append(list(command))
        return subprocess.CompletedProcess(command, 0, stdout="", stderr=_STDERR)

    monkeypatch.setattr(timeline, "run_ffmpeg_command", fake_run)
    monkeypatch.setattr(timeline, "select_audio_stream_index", lambda _p: 0)
    return commands


def test_transcription_audio_path_is_the_shared_convention():
    assert transcription.transcription_audio_path(Path("/x/clip.mp4")) == Path(
        "/x/clip.transcription.mp3"
    )


def test_peaks_decode_the_source_when_no_transcription_mp3(tmp_path, monkeypatch):
    source = tmp_path / "source.mp4"
    source.write_bytes(b"video")
    commands = _capture(monkeypatch)

    peaks = timeline.detect_audio_peak_times(source)

    assert peaks == [1.0, 9.0, 20.0]
    target = commands[0][commands[0].index("-i") + 1]
    assert target == str(source)


def test_peaks_decode_the_transcription_mp3_when_present(tmp_path, monkeypatch):
    source = tmp_path / "source.mp4"
    source.write_bytes(b"video")
    mp3 = transcription.transcription_audio_path(source)
    mp3.write_bytes(b"mp3")
    commands = _capture(monkeypatch)

    peaks = timeline.detect_audio_peak_times(source)

    # Same peaks, cheaper file.
    assert peaks == [1.0, 9.0, 20.0]
    target = commands[0][commands[0].index("-i") + 1]
    assert target == str(mp3)


def test_empty_transcription_mp3_falls_back_to_source(tmp_path, monkeypatch):
    source = tmp_path / "source.mp4"
    source.write_bytes(b"video")
    transcription.transcription_audio_path(source).write_bytes(b"")
    commands = _capture(monkeypatch)

    timeline.detect_audio_peak_times(source)

    target = commands[0][commands[0].index("-i") + 1]
    assert target == str(source)


# --- Real-ffmpeg parity ----------------------------------------------------

ffmpeg_missing = (
    shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None
)


@pytest.mark.skipif(ffmpeg_missing, reason="ffmpeg/ffprobe not installed")
def test_peak_times_match_between_source_and_transcription_mp3(tmp_path):
    source = tmp_path / "bursts.mp4"
    # A quiet 440 Hz floor with two loud bursts at 5-8s and 16-19s.
    expr = (
        "0.85*sin(2*PI*440*t)*(between(t,5,8)+between(t,16,19))"
        "+0.04*sin(2*PI*440*t)"
    )
    built = subprocess.run(
        [
            "ffmpeg", "-y", "-f", "lavfi",
            "-i", f"aevalsrc=exprs='{expr}':duration=24:sample_rate=48000",
            "-c:a", "aac", str(source),
        ],
        capture_output=True,
    )
    assert built.returncode == 0

    peaks_from_source = timeline.detect_audio_peak_times(source)
    assert any(abs(p - 6.5) <= 1.5 for p in peaks_from_source)
    assert any(abs(p - 17.5) <= 1.5 for p in peaks_from_source)

    mp3 = transcription._prepare_audio_for_transcription(source)
    assert mp3.name.endswith(".transcription.mp3") and mp3.exists()

    peaks_from_mp3 = timeline.detect_audio_peak_times(source)
    assert any(abs(p - 6.5) <= 1.5 for p in peaks_from_mp3)
    assert any(abs(p - 17.5) <= 1.5 for p in peaks_from_mp3)
    # The dominant peaks agree across the two decodes.
    source_burst = min(peaks_from_source, key=lambda p: abs(p - 6.5))
    mp3_burst = min(peaks_from_mp3, key=lambda p: abs(p - 6.5))
    assert abs(source_burst - mp3_burst) <= 2.0
