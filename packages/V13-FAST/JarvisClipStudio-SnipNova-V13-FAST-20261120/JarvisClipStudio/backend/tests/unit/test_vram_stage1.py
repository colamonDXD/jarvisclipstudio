"""Wave-4 VRAM build-lite: Stage-0 telemetry + Stage-1 seam fixes.

Every VRAM figure in this file is STUBBED. These tests pin behaviour —
who calls whom, in what order, and what the fallback is when the card
says no — never real measurements. Real RTX 4050 numbers are
user-machine validation via tools/benchmark-pack/vram-scenario.ps1.
"""

import logging
from pathlib import Path
from types import SimpleNamespace

import pytest

from src.auto_director import classify as classify_module
from src.media import separation, transcription, vram


# --- Stage 0: telemetry ---------------------------------------------------


def test_log_vram_emits_structured_line(monkeypatch, caplog):
    monkeypatch.setattr(
        vram, "vram_snapshot", lambda: (3812 * 1024 * 1024, 6144 * 1024 * 1024)
    )
    with caplog.at_level(logging.INFO, logger="src.media.vram"):
        vram.log_vram("transcription_start")
    messages = [r.getMessage() for r in caplog.records if r.levelno == logging.INFO]
    assert messages == ["vram stage=transcription_start free_mb=3812 total_mb=6144"]


def test_log_vram_no_info_line_when_snapshot_unavailable(monkeypatch, caplog):
    monkeypatch.setattr(vram, "vram_snapshot", lambda: None)
    with caplog.at_level(logging.INFO, logger="src.media.vram"):
        vram.log_vram("transcription_start")
    assert [r for r in caplog.records if r.levelno >= logging.INFO] == []


def test_log_vram_never_raises(monkeypatch):
    def boom():
        raise RuntimeError("probe exploded")

    monkeypatch.setattr(vram, "vram_snapshot", boom)
    vram.log_vram("separation_start")  # must not raise


# --- Stage 1, fix 1: faster-whisper cache eviction -------------------------


@pytest.fixture
def empty_whisper_cache():
    transcription._FASTER_WHISPER_MODEL_CACHE.clear()
    yield transcription._FASTER_WHISPER_MODEL_CACHE
    transcription._FASTER_WHISPER_MODEL_CACHE.clear()


def test_release_faster_whisper_models_drops_every_cached_model(empty_whisper_cache):
    empty_whisper_cache["medium"] = object()
    empty_whisper_cache["medium@cpu/int8"] = object()
    released = transcription.release_faster_whisper_models()
    assert released == 2
    assert dict(empty_whisper_cache) == {}


def test_release_faster_whisper_models_empty_cache_releases_zero(empty_whisper_cache):
    assert transcription.release_faster_whisper_models() == 0


def _fake_transcription_config(provider="faster_whisper"):
    return SimpleNamespace(
        transcription_provider=provider,
        whisper_model="tiny",
        transcription_language="",
    )


def test_transcript_releases_whisper_before_separation(monkeypatch, tmp_path):
    events = []
    monkeypatch.setattr(
        transcription, "get_config", lambda: _fake_transcription_config()
    )
    monkeypatch.setattr(
        transcription,
        "release_faster_whisper_models",
        lambda: events.append("release") or 0,
    )
    monkeypatch.setattr(separation, "separation_enabled", lambda: True)
    monkeypatch.setattr(
        separation,
        "separate_vocals",
        lambda path: events.append("separate") or None,
    )
    monkeypatch.setattr(
        transcription,
        "_get_transcript_with_faster_whisper",
        lambda path, cfg: "ok",
    )
    result = transcription.get_video_transcript(
        tmp_path / "video.mp4", separation_opt_in=True
    )
    assert result == "ok"
    assert events == ["release", "separate"]


def test_transcript_no_release_when_separation_not_running(monkeypatch, tmp_path):
    events = []
    monkeypatch.setattr(
        transcription, "get_config", lambda: _fake_transcription_config()
    )
    monkeypatch.setattr(
        transcription,
        "release_faster_whisper_models",
        lambda: events.append("release") or 0,
    )
    monkeypatch.setattr(
        transcription,
        "_get_transcript_with_faster_whisper",
        lambda path, cfg: "ok",
    )
    result = transcription.get_video_transcript(tmp_path / "video.mp4")
    assert result == "ok"
    assert events == []


def _stub_classify_chain(monkeypatch, candidates, model_used):
    """Drive classify_niche's production chain with a stubbed walk."""
    monkeypatch.setattr(classify_module, "get_config", lambda: SimpleNamespace())
    monkeypatch.setattr(
        classify_module, "_classify_candidates", lambda cfg: list(candidates)
    )

    async def fake_walk(prompt, runtime_config, chain_candidates):
        classification = classify_module.NicheClassification(
            niche="gaming", confidence=0.9, reasons=["stub"]
        )
        return classification, {
            "attempts": [],
            "model_used": model_used,
            "last_error": None,
        }

    monkeypatch.setattr(classify_module, "_classify_with_candidates", fake_walk)


async def test_classify_releases_whisper_when_local_candidate_present(monkeypatch):
    releases = []
    monkeypatch.setattr(
        transcription,
        "release_faster_whisper_models",
        lambda: releases.append(1) or 0,
    )
    _stub_classify_chain(
        monkeypatch,
        ["groq:openai/gpt-oss-120b", "openai:local-qwen"],
        model_used="openai:local-qwen",
    )
    result = await classify_module.classify_niche(
        "ranked match gameplay, clutch round, headshot", []
    )
    assert result["niche"] == "gaming"
    assert result["is_fallback"] is False
    assert releases == [1]


async def test_classify_keeps_whisper_when_no_local_candidate(monkeypatch):
    releases = []
    monkeypatch.setattr(
        transcription,
        "release_faster_whisper_models",
        lambda: releases.append(1) or 0,
    )
    _stub_classify_chain(
        monkeypatch,
        ["groq:openai/gpt-oss-120b"],
        model_used="groq:openai/gpt-oss-120b",
    )
    result = await classify_module.classify_niche(
        "ranked match gameplay, clutch round, headshot", []
    )
    assert result["niche"] == "gaming"
    assert releases == []


async def test_classify_releases_whisper_for_direct_ollama_candidate(monkeypatch):
    releases = []
    monkeypatch.setattr(
        transcription,
        "release_faster_whisper_models",
        lambda: releases.append(1) or 0,
    )
    _stub_classify_chain(
        monkeypatch, ["ollama:qwen3:8b"], model_used="ollama:qwen3:8b"
    )
    result = await classify_module.classify_niche(
        "ranked match gameplay, clutch round, headshot", []
    )
    assert result["niche"] == "gaming"
    assert releases == [1]


# --- Stage 1, fix 2: Ollama keep_alive:0 unload handshake -------------------


def _fake_vram_config(
    base_url="http://host.docker.internal:11434/v1",
    direct_fallback="ollama:qwen3:8b",
    local_llm="openai:local-qwen",
):
    return SimpleNamespace(
        analysis_direct_fallback_model=direct_fallback,
        local_llm=local_llm,
        resolve_ollama_base_url=lambda: base_url,
    )


def test_request_ollama_unload_posts_keep_alive_zero(monkeypatch):
    calls = []

    def fake_post(url, json=None, timeout=None):
        calls.append((url, json, timeout))
        return SimpleNamespace(status_code=200)

    monkeypatch.setattr(vram, "get_config", lambda: _fake_vram_config())
    monkeypatch.setattr(vram.httpx, "post", fake_post)
    assert vram.request_ollama_unload() is True
    assert calls == [
        (
            "http://host.docker.internal:11434/api/generate",
            {"model": "qwen3:8b", "keep_alive": 0},
            vram.OLLAMA_UNLOAD_TIMEOUT_SECONDS,
        )
    ]


def test_request_ollama_unload_silent_on_transport_failure(monkeypatch):
    def failing_post(url, json=None, timeout=None):
        raise vram.httpx.ConnectError("connection refused", request=None)

    monkeypatch.setattr(vram, "get_config", lambda: _fake_vram_config())
    monkeypatch.setattr(vram.httpx, "post", failing_post)
    assert vram.request_ollama_unload() is False  # never raises


def test_request_ollama_unload_false_on_http_error(monkeypatch):
    monkeypatch.setattr(vram, "get_config", lambda: _fake_vram_config())
    monkeypatch.setattr(
        vram.httpx, "post", lambda url, json=None, timeout=None: SimpleNamespace(
            status_code=500
        ),
    )
    assert vram.request_ollama_unload() is False


def test_request_ollama_unload_skips_when_no_model_name(monkeypatch):
    calls = []
    monkeypatch.setattr(
        vram,
        "get_config",
        lambda: _fake_vram_config(direct_fallback="", local_llm="openai:local-qwen"),
    )
    monkeypatch.setattr(
        vram.httpx, "post", lambda *a, **k: calls.append(1) or SimpleNamespace(
            status_code=200
        ),
    )
    assert vram.request_ollama_unload() is False
    assert calls == []


def test_transcription_requests_unload_before_local_transcription(
    monkeypatch, tmp_path
):
    calls = []
    monkeypatch.setattr(
        transcription, "get_config", lambda: _fake_transcription_config()
    )
    monkeypatch.setattr(
        transcription, "request_ollama_unload", lambda: calls.append(1) or True
    )
    monkeypatch.setattr(
        transcription, "_get_transcript_with_faster_whisper", lambda path, cfg: "ok"
    )
    assert transcription.get_video_transcript(tmp_path / "video.mp4") == "ok"
    assert calls == [1]


def test_transcription_skips_unload_for_cloud_provider(monkeypatch, tmp_path):
    calls = []
    monkeypatch.setattr(
        transcription,
        "get_config",
        lambda: _fake_transcription_config(provider="assemblyai"),
    )
    monkeypatch.setattr(
        transcription, "request_ollama_unload", lambda: calls.append(1) or True
    )
    monkeypatch.setattr(
        transcription,
        "_get_transcript_with_assemblyai",
        lambda path, model, cfg: "ok",
    )
    assert transcription.get_video_transcript(tmp_path / "video.mp4") == "ok"
    assert calls == []


async def test_classify_unloads_after_local_model_answers(monkeypatch):
    calls = []
    monkeypatch.setattr(
        vram, "request_ollama_unload", lambda: calls.append(1) or True
    )
    _stub_classify_chain(
        monkeypatch, ["openai:local-qwen"], model_used="openai:local-qwen"
    )
    result = await classify_module.classify_niche(
        "ranked match gameplay, clutch round, headshot", []
    )
    assert result["niche"] == "gaming"
    assert calls == [1]


async def test_classify_no_unload_after_cloud_model_answers(monkeypatch):
    calls = []
    monkeypatch.setattr(
        vram, "request_ollama_unload", lambda: calls.append(1) or True
    )
    _stub_classify_chain(
        monkeypatch,
        ["groq:openai/gpt-oss-120b"],
        model_used="groq:openai/gpt-oss-120b",
    )
    result = await classify_module.classify_niche(
        "ranked match gameplay, clutch round, headshot", []
    )
    assert result["niche"] == "gaming"
    assert calls == []


async def test_classify_unload_failure_is_silent(monkeypatch):
    def boom():
        raise RuntimeError("ollama exploded")

    monkeypatch.setattr(vram, "request_ollama_unload", boom)
    _stub_classify_chain(
        monkeypatch, ["ollama:qwen3:8b"], model_used="ollama:qwen3:8b"
    )
    result = await classify_module.classify_niche(
        "ranked match gameplay, clutch round, headshot", []
    )
    assert result["niche"] == "gaming"
    assert result["is_fallback"] is False


# --- Stage 1, fix 3: Demucs free-VRAM gate + CPU retry ----------------------

import wave


@pytest.fixture
def sep_enabled(monkeypatch):
    monkeypatch.setenv("SEPARATION_ENABLED", "true")


def _write_wav(path: Path, seconds: float = 0.5) -> Path:
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(2)
        handle.setsampwidth(2)
        handle.setframerate(44100)
        handle.writeframes(b"\x10\x00\x10\x00" * int(44100 * seconds))
    return path


def _fake_run_ok(input_audio, out_dir, device, timeout):
    produced = Path(out_dir) / separation.MODEL_NAME / Path(input_audio).stem
    produced.mkdir(parents=True, exist_ok=True)
    _write_wav(produced / "vocals.wav")
    _write_wav(produced / "no_vocals.wav")
    return True


_GB = 1024 * 1024 * 1024


def test_separation_device_cuda_when_roomy(monkeypatch):
    monkeypatch.setattr(separation, "_cuda_available", lambda: True)
    monkeypatch.setattr(separation, "_cuda_free_vram_bytes", lambda: 4 * _GB)
    assert separation._separation_device() == "cuda"


def test_separation_device_cpu_when_starved(monkeypatch):
    monkeypatch.setattr(separation, "_cuda_available", lambda: True)
    monkeypatch.setattr(separation, "_cuda_free_vram_bytes", lambda: 1 * _GB)
    assert separation._separation_device() == "cpu"


def test_separation_device_cuda_when_probe_unknown(monkeypatch):
    # A failed probe must not reroute a healthy machine to CPU.
    monkeypatch.setattr(separation, "_cuda_available", lambda: True)
    monkeypatch.setattr(separation, "_cuda_free_vram_bytes", lambda: None)
    assert separation._separation_device() == "cuda"


def test_separation_device_cpu_without_cuda(monkeypatch):
    monkeypatch.setattr(separation, "_cuda_available", lambda: False)
    assert separation._separation_device() == "cpu"


def test_demucs_cuda_threshold_env_override(monkeypatch):
    monkeypatch.setenv("SEPARATION_CUDA_MIN_FREE_MB", "100")
    assert separation._demucs_cuda_min_free_bytes() == 100 * 1024 * 1024
    monkeypatch.setenv("SEPARATION_CUDA_MIN_FREE_MB", "banana")
    assert (
        separation._demucs_cuda_min_free_bytes()
        == separation.DEMUCS_CUDA_MIN_FREE_MB_DEFAULT * 1024 * 1024
    )
    monkeypatch.delenv("SEPARATION_CUDA_MIN_FREE_MB")
    assert (
        separation._demucs_cuda_min_free_bytes()
        == separation.DEMUCS_CUDA_MIN_FREE_MB_DEFAULT * 1024 * 1024
    )


def _recording_run(monkeypatch, behaviour):
    """Patch _run_demucs with a device recorder; behaviour(device)->bool
    may also lay down stems via _fake_run_ok when it returns True."""
    devices = []

    def run(input_audio, out_dir, device, timeout):
        devices.append(device)
        if behaviour(device):
            return _fake_run_ok(input_audio, out_dir, device, timeout)
        return False

    monkeypatch.setattr(separation, "_run_demucs", run)
    return devices


def test_separate_vocals_gated_to_cpu_when_starved(
    sep_enabled, monkeypatch, tmp_path
):
    source = _write_wav(tmp_path / "mix.wav")
    monkeypatch.setattr(separation, "_cuda_available", lambda: True)
    monkeypatch.setattr(separation, "_cuda_free_vram_bytes", lambda: 1 * _GB)
    devices = _recording_run(monkeypatch, lambda device: True)
    result = separation.separate_vocals(source)
    assert result == separation.vocals_stem_path(source)
    assert devices == ["cpu"]


def test_separate_vocals_cuda_failure_retries_once_on_cpu(
    sep_enabled, monkeypatch, tmp_path
):
    source = _write_wav(tmp_path / "mix.wav")
    monkeypatch.setattr(separation, "_cuda_available", lambda: True)
    monkeypatch.setattr(separation, "_cuda_free_vram_bytes", lambda: 4 * _GB)
    devices = _recording_run(monkeypatch, lambda device: device == "cpu")
    result = separation.separate_vocals(source)
    assert result == separation.vocals_stem_path(source)
    assert devices == ["cuda", "cpu"]


def test_separate_vocals_double_failure_returns_none(
    sep_enabled, monkeypatch, tmp_path
):
    source = _write_wav(tmp_path / "mix.wav")
    monkeypatch.setattr(separation, "_cuda_available", lambda: True)
    monkeypatch.setattr(separation, "_cuda_free_vram_bytes", lambda: 4 * _GB)
    devices = _recording_run(monkeypatch, lambda device: False)
    assert separation.separate_vocals(source) is None
    assert devices == ["cuda", "cpu"]
    # The never-fail contract: no false cache entry left behind.
    assert not separation.separation_sidecar_path(source).exists()


def test_separate_vocals_cpu_failure_not_retried(
    sep_enabled, monkeypatch, tmp_path
):
    source = _write_wav(tmp_path / "mix.wav")
    monkeypatch.setattr(separation, "_cuda_available", lambda: False)
    devices = _recording_run(monkeypatch, lambda device: False)
    assert separation.separate_vocals(source) is None
    assert devices == ["cpu"]


# --- Stage 1, fix 4: sequential + load CUDA-OOM catch -----------------------


def _fw_segment():
    return SimpleNamespace(
        id=0, start=0.0, end=0.5, text=" Hello world.", words=[]
    )


def _fw_info():
    return SimpleNamespace(language="en")


_OOM = "CUDA failed with error out of memory"


def _patch_fake_engine(monkeypatch, whisper_model_cls):
    """Install a fake faster-whisper module (no batched pipeline, so the
    sequential path is exercised directly) with the real model loader."""
    fake_module = SimpleNamespace(WhisperModel=whisper_model_cls)
    monkeypatch.setattr(transcription, "_faster_whisper", fake_module)
    monkeypatch.setattr(transcription, "_FASTER_WHISPER_AVAILABLE", True)
    monkeypatch.setattr(
        transcription, "_prepare_audio_for_transcription", lambda p: p
    )
    monkeypatch.setattr(
        transcription,
        "_faster_whisper_device_and_compute_type",
        lambda: ("cuda", "float16"),
    )


def test_model_load_cuda_oom_falls_back_to_cpu_int8(
    monkeypatch, empty_whisper_cache
):
    constructed = []

    class LoadOOMModel:
        def __init__(self, model_name, device=None, compute_type=None):
            constructed.append(device)
            if device == "cuda":
                raise RuntimeError(_OOM)
            self.device = device

        def transcribe(self, audio, **kwargs):
            return iter([_fw_segment()]), _fw_info()

    _patch_fake_engine(monkeypatch, LoadOOMModel)
    result = transcription.transcribe_with_faster_whisper(
        Path("clip.mp4"), model_name="medium"
    )
    assert "Hello world." in result["text"]
    assert constructed == ["cuda", "cpu"]


def test_sequential_cuda_oom_retries_on_cpu_model(
    monkeypatch, empty_whisper_cache
):
    attempted = []

    class SequentialOOMModel:
        def __init__(self, model_name, device=None, compute_type=None):
            self.device = device

        def transcribe(self, audio, **kwargs):
            attempted.append(self.device)
            if self.device == "cuda":
                raise RuntimeError(_OOM)
            return iter([_fw_segment()]), _fw_info()

    _patch_fake_engine(monkeypatch, SequentialOOMModel)
    result = transcription.transcribe_with_faster_whisper(
        Path("clip.mp4"), model_name="medium"
    )
    assert "Hello world." in result["text"]
    assert attempted == ["cuda", "cpu"]


def test_sequential_oom_on_cpu_model_fails_the_stage(
    monkeypatch, empty_whisper_cache
):
    class AlwaysOOMModel:
        def __init__(self, model_name, device=None, compute_type=None):
            self.device = device

        def transcribe(self, audio, **kwargs):
            raise RuntimeError(_OOM)

    _patch_fake_engine(monkeypatch, AlwaysOOMModel)
    monkeypatch.setattr(
        transcription,
        "_faster_whisper_device_and_compute_type",
        lambda: ("cpu", "int8"),
    )
    with pytest.raises(RuntimeError, match="out of memory"):
        transcription.transcribe_with_faster_whisper(
            Path("clip.mp4"), model_name="medium"
        )


def test_model_load_non_oom_error_propagates_without_cpu_attempt(
    monkeypatch, empty_whisper_cache
):
    constructed = []

    class BrokenModel:
        def __init__(self, model_name, device=None, compute_type=None):
            constructed.append(device)
            raise RuntimeError("disk full")

    _patch_fake_engine(monkeypatch, BrokenModel)
    with pytest.raises(RuntimeError, match="disk full"):
        transcription.transcribe_with_faster_whisper(
            Path("clip.mp4"), model_name="medium"
        )
    assert constructed == ["cuda"]


def test_sequential_non_oom_error_propagates(monkeypatch, empty_whisper_cache):
    class CorruptAudioModel:
        def __init__(self, model_name, device=None, compute_type=None):
            pass

        def transcribe(self, audio, **kwargs):
            raise ValueError("corrupt audio")

    _patch_fake_engine(monkeypatch, CorruptAudioModel)
    with pytest.raises(ValueError, match="corrupt audio"):
        transcription.transcribe_with_faster_whisper(
            Path("clip.mp4"), model_name="medium"
        )


def test_sequential_oom_after_batched_cpu_repoint_does_not_retry(
    monkeypatch, empty_whisper_cache
):
    """The batched ladder already fell back to the CPU model and
    re-pointed the sequential path at it; an OOM there must fail, not
    loop through a second CPU retry."""
    cpu_calls = []

    class CPUModel:
        def transcribe(self, audio, **kwargs):
            cpu_calls.append(1)
            raise RuntimeError(_OOM)

    class GPUModel:
        def transcribe(self, audio, **kwargs):
            raise AssertionError("sequential path must use the CPU model")

    gpu_model, cpu_model = GPUModel(), CPUModel()

    class OOMBatched:
        def __init__(self, model=None):
            self.model = model

        def transcribe(self, audio, **kwargs):
            raise RuntimeError(_OOM)

    def fake_loader(name, device=None, compute_type=None):
        return cpu_model if device == "cpu" else gpu_model

    fake_module = SimpleNamespace(
        WhisperModel=object, BatchedInferencePipeline=OOMBatched
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
    with pytest.raises(RuntimeError, match="out of memory"):
        transcription.transcribe_with_faster_whisper(
            Path("clip.mp4"), model_name="medium"
        )
    assert cpu_calls == [1]
