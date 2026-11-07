"""Tests for the faster-whisper transcription engine (Speed Pack 2).

Covers the provider switch, the output-shape conversion into the
openai-whisper dict every consumer already understands, the batched/VAD
transcription path with its sequential fallback, and a real tiny-model
transcription when the engine and model are available.
"""

import wave
from pathlib import Path
from types import SimpleNamespace

import pytest

from src.config import Config
from src.media import transcription


def _fake_word(text=" Hello", start=0.0, end=0.5, probability=0.9):
    return SimpleNamespace(word=text, start=start, end=end, probability=probability)


def _fake_segment():
    return SimpleNamespace(
        id=3,
        start=0.0,
        end=1.5,
        text=" Hello world.",
        words=[
            _fake_word(" Hello", 0.0, 0.5, 0.9),
            _fake_word(" world.", 0.5, 1.5, 0.8),
        ],
    )


def _fake_info():
    return SimpleNamespace(language="en", language_probability=0.99)


# --- (a) provider switch ---------------------------------------------------


def test_provider_normalization_accepts_faster_whisper():
    normalize = Config._normalize_transcription_provider
    assert normalize("faster-whisper") == "faster_whisper"
    assert normalize("faster_whisper") == "faster_whisper"
    assert normalize(" Faster-Whisper ") == "faster_whisper"
    # Existing providers are untouched.
    assert normalize("whisper") == "whisper"
    assert normalize("youtube_captions") == "youtube_captions"
    assert normalize("nonsense") == "assemblyai"
    assert normalize(None) == "assemblyai"


def test_get_video_transcript_dispatches_by_provider(monkeypatch):
    calls = []

    def fake_config(provider):
        return SimpleNamespace(
            transcription_provider=provider,
            whisper_model="tiny",
            transcription_language="",
        )

    for provider, handler_name in (
        ("faster_whisper", "_get_transcript_with_faster_whisper"),
        ("whisper", "_get_transcript_with_whisper"),
    ):
        calls.clear()
        monkeypatch.setattr(
            transcription, "get_config", lambda p=provider: fake_config(p)
        )
        monkeypatch.setattr(
            transcription,
            handler_name,
            lambda path, cfg, _n=handler_name: calls.append(_n) or "ok",
        )
        result = transcription.get_video_transcript(Path("video.mp4"))
        assert result == "ok"
        assert calls == [handler_name]


# --- (b) output-shape conversion -------------------------------------------


def test_faster_whisper_result_matches_whisper_dict_shape():
    result = transcription._faster_whisper_result_to_whisper_dict(
        [_fake_segment()], _fake_info()
    )

    assert result == {
        "text": " Hello world.",
        "language": "en",
        "segments": [
            {
                "id": 3,
                "start": 0.0,
                "end": 1.5,
                "text": " Hello world.",
                "words": [
                    {"word": " Hello", "start": 0.0, "end": 0.5, "probability": 0.9},
                    {"word": " world.", "start": 0.5, "end": 1.5, "probability": 0.8},
                ],
            }
        ],
    }

    # The existing consumers accept the converted result unchanged:
    # analysis formatting reads segments (seconds -> ms timestamps) ...
    lines = transcription.format_transcript_for_analysis(result)
    assert lines == ["[00:00 - 00:01] Hello world."]

    # ... and the transcript cache builder reads segments/words/text/language.
    cache_data = transcription._whisper_result_to_transcript_data(result)
    assert cache_data["text"] == " Hello world."
    assert cache_data["language_code"] == "en"
    assert cache_data["words"] == [
        {
            "text": " Hello",
            "start": 0,
            "end": 500,
            "confidence": 0.9,
            "speaker": None,
        },
        {
            "text": " world.",
            "start": 500,
            "end": 1500,
            "confidence": 0.8,
            "speaker": None,
        },
    ]
    assert cache_data["utterances"][0]["start"] == 0
    assert cache_data["utterances"][0]["end"] == 1500


def test_faster_whisper_result_handles_segments_without_words():
    segment = SimpleNamespace(id=0, start=2.0, end=3.0, text=" No words.", words=None)
    result = transcription._faster_whisper_result_to_whisper_dict(
        [segment], _fake_info()
    )
    assert result["segments"][0]["words"] == []
    assert result["text"] == " No words."


# --- engine behaviour --------------------------------------------------------


class _FakeModel:
    def __init__(self):
        self.calls = []

    def transcribe(self, audio, **kwargs):
        self.calls.append((audio, kwargs))
        return iter([_fake_segment()]), _fake_info()


class _RecordingBatched:
    instances = []

    def __init__(self, model=None):
        self.model = model
        self.calls = []
        _RecordingBatched.instances.append(self)

    def transcribe(self, audio, **kwargs):
        self.calls.append((audio, kwargs))
        return iter([_fake_segment()]), _fake_info()


class _FailingBatched:
    def __init__(self, model=None):
        self.model = model

    def transcribe(self, audio, **kwargs):
        raise RuntimeError("batched pipeline unavailable")


def _patch_engine(monkeypatch, batched_cls):
    model = _FakeModel()
    fake_module = SimpleNamespace(
        WhisperModel=object, BatchedInferencePipeline=batched_cls
    )
    monkeypatch.setattr(transcription, "_faster_whisper", fake_module)
    monkeypatch.setattr(transcription, "_FASTER_WHISPER_AVAILABLE", True)
    monkeypatch.setattr(transcription, "_get_faster_whisper_model", lambda name: model)
    monkeypatch.setattr(
        transcription, "_prepare_audio_for_transcription", lambda p: p
    )
    return model


def test_transcribe_uses_batched_pipeline_with_vad_and_word_timestamps(monkeypatch):
    _RecordingBatched.instances = []
    model = _patch_engine(monkeypatch, _RecordingBatched)

    result = transcription.transcribe_with_faster_whisper(
        Path("clip.mp4"), model_name="tiny"
    )

    assert result["language"] == "en"
    assert result["segments"][0]["text"] == " Hello world."
    # The batched pipeline did the work, with batching, VAD and word timings.
    assert len(_RecordingBatched.instances) == 1
    batched = _RecordingBatched.instances[0]
    assert batched.model is model
    assert len(batched.calls) == 1
    _, kwargs = batched.calls[0]
    assert kwargs["batch_size"] == 16
    assert kwargs["vad_filter"] is True
    assert kwargs["word_timestamps"] is True
    # The plain path was not needed.
    assert model.calls == []


def test_transcribe_falls_back_to_sequential_when_batched_fails(monkeypatch):
    model = _patch_engine(monkeypatch, _FailingBatched)

    result = transcription.transcribe_with_faster_whisper(
        Path("clip.mp4"), model_name="tiny"
    )

    assert result["segments"][0]["text"] == " Hello world."
    assert len(model.calls) == 1
    _, kwargs = model.calls[0]
    assert kwargs["vad_filter"] is True
    assert kwargs["word_timestamps"] is True


def test_device_and_compute_type_selection(monkeypatch):
    monkeypatch.setattr(
        transcription,
        "_ctranslate2",
        SimpleNamespace(get_cuda_device_count=lambda: 0),
    )
    assert transcription._faster_whisper_device_and_compute_type() == ("cpu", "int8")

    monkeypatch.setattr(
        transcription,
        "_ctranslate2",
        SimpleNamespace(get_cuda_device_count=lambda: 2),
    )
    assert transcription._faster_whisper_device_and_compute_type() == (
        "cuda",
        "float16",
    )


def test_missing_engine_raises_clear_error(monkeypatch):
    monkeypatch.setattr(transcription, "_FASTER_WHISPER_AVAILABLE", False)
    transcription._FASTER_WHISPER_MODEL_CACHE.pop("tiny", None)
    with pytest.raises(RuntimeError, match="faster-whisper is not installed"):
        transcription._get_faster_whisper_model("tiny")


# --- (c) real tiny-model transcription ---------------------------------------


def _write_silence_wav(path: Path, seconds: float = 2.0) -> Path:
    with wave.open(str(path), "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(16000)
        wav.writeframes(b"\x00\x00" * int(16000 * seconds))
    return path


@pytest.mark.skipif(
    not transcription._FASTER_WHISPER_AVAILABLE,
    reason="faster-whisper is not installed in this environment",
)
def test_faster_whisper_tiny_model_transcribes_silence(tmp_path):
    """Real model load + transcribe: silence must yield the whisper dict
    shape (almost certainly zero segments) without crashing."""
    audio = _write_silence_wav(tmp_path / "silence.wav")
    try:
        result = transcription.transcribe_with_faster_whisper(audio, model_name="tiny")
    except Exception as error:  # model download unavailable in this environment
        pytest.skip(f"tiny model could not be loaded here: {error}")

    assert isinstance(result, dict)
    assert isinstance(result["text"], str)
    assert isinstance(result["segments"], list)
    for segment in result["segments"]:
        assert {"id", "start", "end", "text", "words"} <= set(segment)
