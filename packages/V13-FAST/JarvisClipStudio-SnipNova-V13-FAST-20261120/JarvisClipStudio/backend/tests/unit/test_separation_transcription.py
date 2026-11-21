"""Separation → transcription wiring (Use A).

What is stubbed, honestly: the ffmpeg runner (records its argv and
materialises the output file), the faster-whisper engine (a fake model
that records the path it was asked to transcribe) and the separator
itself (returns a prepared stem). What is real: every line of the
wiring under test — the gate checks, the stem resolution in
get_video_transcript, the stem branch of _prepare_audio_for_transcription
and the path handed to the whisper engine.
"""

import wave
from pathlib import Path
from types import SimpleNamespace

import pytest

from src.media import separation, transcription


def _write_wav(path: Path, seconds: float = 0.5) -> Path:
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(16000)
        handle.writeframes(b"\x10\x00" * int(16000 * seconds))
    return path


def _fake_word(text=" Hello", start=0.0, end=0.5, probability=0.9):
    return SimpleNamespace(word=text, start=start, end=end, probability=probability)


def _fake_segment():
    return SimpleNamespace(
        id=3,
        start=0.0,
        end=1.5,
        text=" Hello world.",
        words=[_fake_word(" Hello", 0.0, 0.5, 0.9), _fake_word(" world.", 0.5, 1.5, 0.8)],
    )


def _fake_info():
    return SimpleNamespace(language="en", language_probability=0.99)


class _FakeFfmpeg:
    """Records commands; writes the output file like ffmpeg would."""

    def __init__(self, fail_for: str | None = None):
        self.commands = []
        self.fail_for = fail_for

    def __call__(self, command, timeout=None):
        self.commands.append(list(command))
        source = command[command.index("-i") + 1]
        if self.fail_for is not None and source == self.fail_for:
            return SimpleNamespace(returncode=1)
        Path(command[-1]).write_bytes(b"fake-audio")
        return SimpleNamespace(returncode=0)

    def inputs(self):
        return [cmd[cmd.index("-i") + 1] for cmd in self.commands]


class _FakeModel:
    def __init__(self):
        self.calls = []

    def transcribe(self, audio, **kwargs):
        self.calls.append(audio)
        return iter([_fake_segment()]), _fake_info()


def _patch_engine(monkeypatch):
    """Fake faster-whisper engine; _prepare_audio stays REAL here."""
    model = _FakeModel()
    monkeypatch.setattr(
        transcription,
        "_faster_whisper",
        SimpleNamespace(WhisperModel=object),  # no BatchedInferencePipeline
    )
    monkeypatch.setattr(transcription, "_FASTER_WHISPER_AVAILABLE", True)
    monkeypatch.setattr(transcription, "_get_faster_whisper_model", lambda name: model)
    return model


# ------------------------------------------------- parity: feature unused


def test_prepare_without_stem_is_the_legacy_path(monkeypatch, tmp_path):
    video = _write_wav(tmp_path / "clip.wav")
    ffmpeg = _FakeFfmpeg()
    monkeypatch.setattr(transcription, "run_ffmpeg_command", ffmpeg)

    result = transcription._prepare_audio_for_transcription(video)

    assert result == transcription.transcription_audio_path(video)
    assert len(ffmpeg.commands) == 1
    command = ffmpeg.commands[0]
    assert command[command.index("-i") + 1] == str(video)
    # The legacy extraction arguments, in order.
    tail = command[command.index("-vn") :]
    assert tail[:-1] == ["-vn", "-ac", "1", "-ar", "16000", "-b:a", "64k"]
    assert tail[-1] == str(result)


def test_dispatch_without_opt_in_never_separates(monkeypatch, tmp_path):
    monkeypatch.setattr(
        transcription,
        "get_config",
        lambda: SimpleNamespace(
            transcription_provider="faster_whisper",
            whisper_model="tiny",
            transcription_language="",
        ),
    )
    monkeypatch.setattr(
        separation,
        "separate_vocals",
        lambda p: (_ for _ in ()).throw(AssertionError("must not separate")),
    )
    seen = {}

    def handler(path, cfg, **kwargs):
        seen.update(kwargs)
        return "ok"

    monkeypatch.setattr(transcription, "_get_transcript_with_faster_whisper", handler)
    # Even with the deployment flag ON, a task that did not opt in is
    # dispatched with the historical argument shape.
    monkeypatch.setenv("SEPARATION_ENABLED", "true")
    assert transcription.get_video_transcript(tmp_path / "v.mp4") == "ok"
    assert seen == {}


def test_dispatch_opt_in_with_flag_off_never_separates(monkeypatch, tmp_path):
    monkeypatch.delenv("SEPARATION_ENABLED", raising=False)
    monkeypatch.setattr(
        transcription,
        "get_config",
        lambda: SimpleNamespace(
            transcription_provider="faster_whisper",
            whisper_model="tiny",
            transcription_language="",
        ),
    )
    monkeypatch.setattr(
        separation,
        "separate_vocals",
        lambda p: (_ for _ in ()).throw(AssertionError("must not separate")),
    )
    seen = {}
    monkeypatch.setattr(
        transcription,
        "_get_transcript_with_faster_whisper",
        lambda path, cfg, **kw: seen.update(kw) or "ok",
    )
    assert (
        transcription.get_video_transcript(tmp_path / "v.mp4", separation_opt_in=True)
        == "ok"
    )
    assert seen == {}


# ------------------------------------------------------ the stem-fed path


def test_dispatch_opt_in_passes_the_stem_down(monkeypatch, tmp_path):
    monkeypatch.setenv("SEPARATION_ENABLED", "true")
    stem = tmp_path / "v.vocals.flac"
    monkeypatch.setattr(
        transcription,
        "get_config",
        lambda: SimpleNamespace(
            transcription_provider="faster_whisper",
            whisper_model="tiny",
            transcription_language="",
        ),
    )
    monkeypatch.setattr(separation, "separate_vocals", lambda p: stem)
    seen = {}
    monkeypatch.setattr(
        transcription,
        "_get_transcript_with_faster_whisper",
        lambda path, cfg, **kw: seen.update(kw) or "ok",
    )
    assert (
        transcription.get_video_transcript(tmp_path / "v.mp4", separation_opt_in=True)
        == "ok"
    )
    assert seen == {"vocals_stem": stem}


def test_dispatch_opt_in_separation_failure_uses_original(monkeypatch, tmp_path):
    # separate_vocals returned None (its failure contract): dispatch
    # proceeds with the historical shape — original audio.
    monkeypatch.setenv("SEPARATION_ENABLED", "true")
    monkeypatch.setattr(
        transcription,
        "get_config",
        lambda: SimpleNamespace(
            transcription_provider="faster_whisper",
            whisper_model="tiny",
            transcription_language="",
        ),
    )
    monkeypatch.setattr(separation, "separate_vocals", lambda p: None)
    seen = {}
    monkeypatch.setattr(
        transcription,
        "_get_transcript_with_faster_whisper",
        lambda path, cfg, **kw: seen.update(kw) or "ok",
    )
    assert (
        transcription.get_video_transcript(tmp_path / "v.mp4", separation_opt_in=True)
        == "ok"
    )
    assert seen == {}


def test_stem_reaches_the_whisper_engine(monkeypatch, tmp_path):
    video = _write_wav(tmp_path / "clip.wav")
    stem = _write_wav(tmp_path / "clip.vocals.flac")
    ffmpeg = _FakeFfmpeg()
    monkeypatch.setattr(transcription, "run_ffmpeg_command", ffmpeg)
    model = _patch_engine(monkeypatch)

    result = transcription.transcribe_with_faster_whisper(
        video, "tiny", None, vocals_stem=stem
    )

    expected_audio = transcription.stem_transcription_audio_path(video)
    assert model.calls == [str(expected_audio)]
    assert ffmpeg.inputs() == [str(stem)]
    assert result["text"] == " Hello world."


def test_stem_extraction_failure_falls_back_to_source(monkeypatch, tmp_path):
    video = _write_wav(tmp_path / "clip.wav")
    stem = _write_wav(tmp_path / "clip.vocals.flac")
    ffmpeg = _FakeFfmpeg(fail_for=str(stem))
    monkeypatch.setattr(transcription, "run_ffmpeg_command", ffmpeg)

    result = transcription._prepare_audio_for_transcription(video, vocals_stem=stem)

    assert result == transcription.transcription_audio_path(video)
    assert ffmpeg.inputs() == [str(stem), str(video)]


def test_stem_cache_is_separate_from_the_raw_cache(monkeypatch, tmp_path):
    video = _write_wav(tmp_path / "clip.wav")
    stem = _write_wav(tmp_path / "clip.vocals.flac")
    # A raw-mix MP3 already cached must not satisfy a stem request.
    raw = transcription.transcription_audio_path(video)
    raw.write_bytes(b"raw-mix")
    ffmpeg = _FakeFfmpeg()
    monkeypatch.setattr(transcription, "run_ffmpeg_command", ffmpeg)

    result = transcription._prepare_audio_for_transcription(video, vocals_stem=stem)

    assert result == transcription.stem_transcription_audio_path(video)
    assert ffmpeg.inputs() == [str(stem)]
    # ...and a stem MP3 must not satisfy a raw request either.
    ffmpeg2 = _FakeFfmpeg()
    monkeypatch.setattr(transcription, "run_ffmpeg_command", ffmpeg2)
    raw.unlink()
    assert transcription._prepare_audio_for_transcription(video) == raw
    assert ffmpeg2.inputs() == [str(video)]
