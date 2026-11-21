"""Voice/music separation (Demucs): the opt-in gate, the stem cache,
the subprocess discipline and the never-fail contract.

The Demucs subprocess boundary is stubbed throughout — these tests
must never depend on an 84 MB weights download. The real-model
quality proof lives in test_separation_quality.py (opt-in) and the
lane's proof script.
"""

import json
import subprocess
import wave
from pathlib import Path
from types import SimpleNamespace

import pytest

from src.media import separation


@pytest.fixture
def enabled(monkeypatch):
    monkeypatch.setenv("SEPARATION_ENABLED", "true")


def _write_wav(path: Path, seconds: float = 0.5) -> Path:
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(2)
        handle.setsampwidth(2)
        handle.setframerate(44100)
        handle.writeframes(b"\x10\x00\x10\x00" * int(44100 * seconds))
    return path


def _fake_run_ok(input_audio, out_dir, device, timeout):
    """Stand in for a successful Demucs run: lay down both stems."""
    produced = Path(out_dir) / separation.MODEL_NAME / Path(input_audio).stem
    produced.mkdir(parents=True, exist_ok=True)
    _write_wav(produced / "vocals.wav")
    _write_wav(produced / "no_vocals.wav")
    return True


# ------------------------------------------------------------- the gate


def test_disabled_by_default(monkeypatch):
    monkeypatch.delenv("SEPARATION_ENABLED", raising=False)
    assert separation.separation_enabled() is False


@pytest.mark.parametrize("value", ["1", "true", "TRUE", "on", "yes"])
def test_enabled_values(monkeypatch, value):
    monkeypatch.setenv("SEPARATION_ENABLED", value)
    assert separation.separation_enabled() is True


@pytest.mark.parametrize("value", ["", "0", "false", "off", "no"])
def test_disabled_values(monkeypatch, value):
    monkeypatch.setenv("SEPARATION_ENABLED", value)
    assert separation.separation_enabled() is False


# --------------------------------------------------- the opt-in validator


def test_validate_opt_in_defaults_off(monkeypatch):
    monkeypatch.delenv("SEPARATION_ENABLED", raising=False)
    assert separation.validate_separation_opt_in(None) is False
    assert separation.validate_separation_opt_in(False) is False


def test_validate_opt_in_rejected_while_disabled(monkeypatch):
    # The voiceover door pattern: an opt-in sent to a deployment with
    # the feature off fails loudly instead of being silently stored.
    monkeypatch.delenv("SEPARATION_ENABLED", raising=False)
    with pytest.raises(ValueError, match="SEPARATION_ENABLED"):
        separation.validate_separation_opt_in(True)


def test_validate_opt_in_accepted_while_enabled(enabled):
    assert separation.validate_separation_opt_in(True) is True


@pytest.mark.parametrize("value", ["true", 1, 0.5, ["yes"]])
def test_validate_opt_in_rejects_non_booleans(enabled, value):
    with pytest.raises(ValueError, match="boolean"):
        separation.validate_separation_opt_in(value)


# --------------------------------------------------------- stem path rules


def test_stem_paths_beside_source(tmp_path):
    source = tmp_path / "episode 12.mp4"
    assert separation.vocals_stem_path(source) == tmp_path / "episode 12.vocals.flac"
    assert (
        separation.accompaniment_stem_path(source)
        == tmp_path / "episode 12.accompaniment.flac"
    )
    assert (
        separation.separation_sidecar_path(source)
        == tmp_path / "episode 12.separation.json"
    )


def test_stem_companion_paths_cover_every_artifact(tmp_path):
    # Deletion paths rely on this list being exhaustive: it must name
    # exactly the artifacts the individual path helpers produce,
    # including for dotted source names (Path.stem drops one suffix).
    source = tmp_path / "my.video.mp4"
    assert separation.stem_companion_paths(source) == [
        separation.vocals_stem_path(source),
        separation.accompaniment_stem_path(source),
        separation.separation_sidecar_path(source),
    ]
    assert separation.stem_companion_names("my.video.mp4") == [
        "my.video.vocals.flac",
        "my.video.accompaniment.flac",
        "my.video.separation.json",
    ]


def test_models_dir_override(monkeypatch, tmp_path):
    target = tmp_path / "models"
    monkeypatch.setenv("SEPARATION_MODELS_DIR", str(target))
    assert separation.separation_models_dir() == target
    assert target.exists()


# -------------------------------------------------------- never-fail paths


def test_separate_vocals_disabled_never_runs(monkeypatch, tmp_path):
    monkeypatch.delenv("SEPARATION_ENABLED", raising=False)
    source = _write_wav(tmp_path / "mix.wav")
    monkeypatch.setattr(
        separation,
        "_run_demucs",
        lambda *a: (_ for _ in ()).throw(AssertionError("must not run")),
    )
    assert separation.separate_vocals(source) is None


def test_separate_vocals_missing_source(enabled, tmp_path):
    assert separation.separate_vocals(tmp_path / "nope.mp4") is None


def test_separate_vocals_subprocess_failure(enabled, monkeypatch, tmp_path):
    source = _write_wav(tmp_path / "mix.wav")
    monkeypatch.setattr(separation, "_cuda_available", lambda: False)
    monkeypatch.setattr(separation, "_run_demucs", lambda *a: False)
    assert separation.separate_vocals(source) is None
    # A failed run leaves no cache artifacts behind.
    assert not separation.vocals_stem_path(source).exists()
    assert not separation.separation_sidecar_path(source).exists()


def test_separate_vocals_corrupt_output(enabled, monkeypatch, tmp_path):
    # The runner claims success but produced nothing usable.
    source = _write_wav(tmp_path / "mix.wav")
    monkeypatch.setattr(separation, "_cuda_available", lambda: False)
    monkeypatch.setattr(separation, "_run_demucs", lambda *a: True)
    assert separation.separate_vocals(source) is None
    assert not separation.separation_sidecar_path(source).exists()


# ------------------------------------------------------------- the runner


def test_demucs_command_shape(tmp_path):
    command = separation._demucs_command(
        tmp_path / "input.flac", tmp_path / "out", "cpu"
    )
    assert command[1:3] == ["-m", "demucs"]
    assert command[command.index("-n") + 1] == "htdemucs"
    assert command[command.index("--two-stems") + 1] == "vocals"
    assert command[command.index("--segment") + 1] == "7"
    assert command[command.index("-d") + 1] == "cpu"
    assert "--flac" in command


def test_run_demucs_env_and_success(monkeypatch, tmp_path):
    seen = {}

    def fake_run(command, **kwargs):
        seen.update(kwargs)
        seen["command"] = command
        return SimpleNamespace(returncode=0, stderr="")

    monkeypatch.setattr(separation.subprocess, "run", fake_run)
    monkeypatch.setattr(separation, "separation_models_dir", lambda: tmp_path)
    assert separation._run_demucs(tmp_path / "in.flac", tmp_path / "out", "cpu", 60)
    assert seen["env"]["PYTORCH_NO_CUDA_MEMORY_CACHING"] == "1"
    assert seen["env"]["HF_HOME"] == str(tmp_path)
    assert seen["timeout"] == 60


def test_run_demucs_timeout_is_a_soft_failure(monkeypatch, tmp_path):
    def fake_run(command, **kwargs):
        raise subprocess.TimeoutExpired(cmd=command, timeout=kwargs.get("timeout"))

    monkeypatch.setattr(separation.subprocess, "run", fake_run)
    monkeypatch.setattr(separation, "separation_models_dir", lambda: tmp_path)
    assert not separation._run_demucs(tmp_path / "in.flac", tmp_path / "out", "cpu", 5)


def test_run_demucs_nonzero_exit_is_a_soft_failure(monkeypatch, tmp_path):
    monkeypatch.setattr(
        separation.subprocess,
        "run",
        lambda command, **kwargs: SimpleNamespace(returncode=1, stderr="boom"),
    )
    monkeypatch.setattr(separation, "separation_models_dir", lambda: tmp_path)
    assert not separation._run_demucs(tmp_path / "in.flac", tmp_path / "out", "cpu", 5)


def test_timeout_scales_with_duration(monkeypatch):
    monkeypatch.setattr(separation, "ffprobe_duration", lambda p: 30.0)
    assert separation._separation_timeout(Path("x.mp4")) == 420
    monkeypatch.setattr(separation, "ffprobe_duration", lambda p: 3600.0)
    assert separation._separation_timeout(Path("x.mp4")) == 36120
    monkeypatch.setattr(separation, "ffprobe_duration", lambda p: None)
    assert separation._separation_timeout(Path("x.mp4")) == 1800
    # Tiny clips still get the floor (model load alone takes a while).
    monkeypatch.setattr(separation, "ffprobe_duration", lambda p: 2.0)
    assert separation._separation_timeout(Path("x.mp4")) == 300


# --------------------------------------------------------------- the cache


def test_separate_vocals_runs_then_caches(enabled, monkeypatch, tmp_path):
    source = _write_wav(tmp_path / "mix.wav")
    calls = []

    def counting_run(*args):
        calls.append(args)
        return _fake_run_ok(*args)

    monkeypatch.setattr(separation, "_cuda_available", lambda: False)
    monkeypatch.setattr(separation, "_run_demucs", counting_run)

    stem = separation.separate_vocals(source)
    assert stem == separation.vocals_stem_path(source)
    assert stem.exists() and stem.stat().st_size > 0
    assert separation.accompaniment_stem_path(source).exists()

    meta = json.loads(separation.separation_sidecar_path(source).read_text())
    assert meta["model"] == "htdemucs"
    assert meta["source_sha256"] == separation._source_sha256(source)
    assert meta["weights_version"]
    assert meta["created_at"]

    # Second call is a pure cache hit — no second separation run.
    assert separation.separate_vocals(source) == stem
    assert len(calls) == 1
    # The device chosen on this CUDA-less path is CPU.
    assert calls[0][2] == "cpu"


def test_separate_vocals_cache_miss_on_hash_mismatch(enabled, monkeypatch, tmp_path):
    source = _write_wav(tmp_path / "mix.wav")
    calls = []
    monkeypatch.setattr(separation, "_cuda_available", lambda: False)
    monkeypatch.setattr(
        separation,
        "_run_demucs",
        lambda *a: calls.append(a) or _fake_run_ok(*a),
    )
    assert separation.separate_vocals(source) is not None
    # Tamper with the sidecar: the recorded hash no longer matches the
    # source, so the stems must not be trusted.
    sidecar = separation.separation_sidecar_path(source)
    meta = json.loads(sidecar.read_text())
    meta["source_sha256"] = "0" * 64
    sidecar.write_text(json.dumps(meta))
    assert separation.separate_vocals(source) is not None
    assert len(calls) == 2
