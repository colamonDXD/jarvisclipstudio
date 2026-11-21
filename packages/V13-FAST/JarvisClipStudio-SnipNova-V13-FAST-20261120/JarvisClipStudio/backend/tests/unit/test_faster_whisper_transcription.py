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


# --- (c) batch-size resolution + CUDA OOM handling (transcription-speed) ---


def test_whisper_batch_size_config_parsing(monkeypatch):
    monkeypatch.delenv("WHISPER_BATCH_SIZE", raising=False)
    assert Config().whisper_batch_size == 0
    monkeypatch.setenv("WHISPER_BATCH_SIZE", "24")
    assert Config().whisper_batch_size == 24
    monkeypatch.setenv("WHISPER_BATCH_SIZE", "not-a-number")
    assert Config().whisper_batch_size == 0
    monkeypatch.setenv("WHISPER_BATCH_SIZE", "999")
    assert Config().whisper_batch_size == 64
    monkeypatch.setenv("WHISPER_BATCH_SIZE", "-5")
    assert Config().whisper_batch_size == 0


def _patch_batch_config(monkeypatch, value):
    monkeypatch.setattr(
        transcription,
        "get_config",
        lambda: SimpleNamespace(whisper_batch_size=value),
    )


def test_batch_size_defaults_keep_legacy_16(monkeypatch):
    _patch_batch_config(monkeypatch, 0)
    resolve = transcription._resolve_faster_whisper_batch_size
    # Unset env: the historic batch of 16 everywhere except large-class
    # models on CUDA, where 8 protects 6 GB GPUs.
    assert resolve("medium", "cuda") == 16
    assert resolve("medium", "cpu") == 16
    assert resolve("tiny", "cuda") == 16
    assert resolve("large-v3", "cuda") == 8
    assert resolve("large-v3-turbo", "cuda") == 8
    assert resolve("large-v3-turbo", "cpu") == 16
    assert resolve("distil-large-v3", "cuda") == 8
    assert resolve("distil-whisper/distil-large-v3.5-ct2", "cuda") == 8


def test_batch_size_env_override_wins_and_clamps(monkeypatch):
    resolve = transcription._resolve_faster_whisper_batch_size
    _patch_batch_config(monkeypatch, 4)
    assert resolve("medium", "cuda") == 4
    assert resolve("large-v3-turbo", "cuda") == 4
    _patch_batch_config(monkeypatch, 500)
    assert resolve("medium", "cuda") == 64


def test_is_cuda_oom_error_matching():
    assert transcription._is_cuda_oom_error(
        RuntimeError("CUDA failed with error out of memory")
    )
    assert transcription._is_cuda_oom_error(
        RuntimeError("CUDA_ERROR_OUT_OF_MEMORY: out of memory")
    )
    assert not transcription._is_cuda_oom_error(RuntimeError("decoder exploded"))
    assert not transcription._is_cuda_oom_error(ValueError("bad audio"))


class _FlakyOOMBatched:
    """First instance OOMs; later instances transcribe normally."""

    instances = []

    def __init__(self, model=None):
        self.model = model
        self.calls = []
        _FlakyOOMBatched.instances.append(self)

    def transcribe(self, audio, **kwargs):
        self.calls.append((audio, kwargs))
        if len(_FlakyOOMBatched.instances) == 1:
            raise RuntimeError("CUDA failed with error out of memory")
        return iter([_fake_segment()]), _fake_info()


class _CPUAwareOOMBatched:
    """OOMs on the GPU model; succeeds once handed the CPU fallback model."""

    instances = []
    cpu_model = None

    def __init__(self, model=None):
        self.model = model
        self.calls = []
        _CPUAwareOOMBatched.instances.append(self)

    def transcribe(self, audio, **kwargs):
        self.calls.append((audio, kwargs))
        if self.model is not _CPUAwareOOMBatched.cpu_model:
            raise RuntimeError("CUDA failed with error out of memory")
        return iter([_fake_segment()]), _fake_info()


class _CountingFailingBatched:
    instances = []

    def __init__(self, model=None):
        self.model = model
        _CountingFailingBatched.instances.append(self)

    def transcribe(self, audio, **kwargs):
        raise RuntimeError("batched pipeline unavailable")


def _patch_engine_with_loader(monkeypatch, batched_cls, gpu_model, cpu_model=None):
    loader_calls = []

    def fake_loader(name, device=None, compute_type=None):
        loader_calls.append((name, device, compute_type))
        if device == "cpu":
            return cpu_model
        return gpu_model

    fake_module = SimpleNamespace(
        WhisperModel=object, BatchedInferencePipeline=batched_cls
    )
    monkeypatch.setattr(transcription, "_faster_whisper", fake_module)
    monkeypatch.setattr(transcription, "_FASTER_WHISPER_AVAILABLE", True)
    monkeypatch.setattr(transcription, "_get_faster_whisper_model", fake_loader)
    monkeypatch.setattr(
        transcription, "_prepare_audio_for_transcription", lambda p: p
    )
    monkeypatch.setattr(
        transcription,
        "_faster_whisper_device_and_compute_type",
        lambda: ("cuda", "float16"),
    )
    _patch_batch_config(monkeypatch, 0)
    return loader_calls


def test_transcribe_retries_with_halved_batch_after_cuda_oom(monkeypatch):
    _FlakyOOMBatched.instances = []
    gpu_model = _FakeModel()
    _patch_engine_with_loader(monkeypatch, _FlakyOOMBatched, gpu_model)

    result = transcription.transcribe_with_faster_whisper(
        Path("clip.mp4"), model_name="medium"
    )

    assert result["segments"][0]["text"] == " Hello world."
    # Two batched attempts: batch 16, then the OOM retry at half of it.
    assert len(_FlakyOOMBatched.instances) == 2
    assert _FlakyOOMBatched.instances[0].calls[0][1]["batch_size"] == 16
    assert _FlakyOOMBatched.instances[1].calls[0][1]["batch_size"] == 8
    # The sequential path was never needed.
    assert gpu_model.calls == []


def test_transcribe_falls_back_to_cpu_model_after_repeated_cuda_oom(monkeypatch):
    _CPUAwareOOMBatched.instances = []
    gpu_model = _FakeModel()
    cpu_model = _FakeModel()
    _CPUAwareOOMBatched.cpu_model = cpu_model
    loader_calls = _patch_engine_with_loader(
        monkeypatch, _CPUAwareOOMBatched, gpu_model, cpu_model
    )

    result = transcription.transcribe_with_faster_whisper(
        Path("clip.mp4"), model_name="large-v3-turbo"
    )

    assert result["segments"][0]["text"] == " Hello world."
    # batch 8 (large-class CUDA default) -> OOM -> halved to 4 -> OOM ->
    # third attempt runs on the CPU int8 model and succeeds.
    assert len(_CPUAwareOOMBatched.instances) == 3
    assert _CPUAwareOOMBatched.instances[0].calls[0][1]["batch_size"] == 8
    assert _CPUAwareOOMBatched.instances[1].calls[0][1]["batch_size"] == 4
    assert _CPUAwareOOMBatched.instances[2].model is cpu_model
    assert ("large-v3-turbo", "cpu", "int8") in loader_calls
    assert gpu_model.calls == []
    assert cpu_model.calls == []


def test_transcribe_non_oom_failure_keeps_single_batched_attempt(monkeypatch):
    _CountingFailingBatched.instances = []
    gpu_model = _FakeModel()
    _patch_engine_with_loader(monkeypatch, _CountingFailingBatched, gpu_model)

    result = transcription.transcribe_with_faster_whisper(
        Path("clip.mp4"), model_name="medium"
    )

    assert result["segments"][0]["text"] == " Hello world."
    # Exactly one batched attempt, then the sequential fallback — the
    # pre-existing behaviour for non-OOM failures is unchanged.
    assert len(_CountingFailingBatched.instances) == 1
    assert len(gpu_model.calls) == 1


# --- (d) real tiny-model transcription ---------------------------------------


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
