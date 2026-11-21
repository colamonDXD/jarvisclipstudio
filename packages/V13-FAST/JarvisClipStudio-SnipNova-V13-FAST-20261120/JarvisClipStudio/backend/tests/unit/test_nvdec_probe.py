"""NVDEC probe + opt-in gate, mirroring the NVENC probe's discipline.

A listing is not evidence: the composed check requires the cuda
hwaccel in `ffmpeg -hwaccels` AND a real hardware decode of a
production-shaped 1920x1080 file. The gate (nvdec_input_args) must
return [] unless ENABLE_NVDEC is set and the probe passed -- the
default render command stays byte-identical to the software-decode
command, which the render tests pin separately.
"""

import subprocess

import pytest

from src.media import ffmpeg as ffmpeg_module


@pytest.fixture(autouse=True)
def _clean_nvdec_state():
    ffmpeg_module._reset_nvdec_probe_state()
    yield
    ffmpeg_module._reset_nvdec_probe_state()


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    monkeypatch.delenv("ENABLE_NVDEC", raising=False)


def _fake_run_factory(captured, hwaccels="vdpau\ncuda\nvaapi\n", decode_rc=0):
    def fake_run(command, timeout=30):
        captured.append(command)
        if "-hwaccels" in command:
            return subprocess.CompletedProcess(
                command, 0, stdout=f"Hardware acceleration methods:\n{hwaccels}"
            )
        if "-hwaccel" in command:
            return subprocess.CompletedProcess(
                command, decode_rc, stdout="", stderr="decode exploded" if decode_rc else ""
            )
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    return fake_run


def test_probe_passes_when_cuda_listed_and_real_decode_works(monkeypatch):
    captured = []
    monkeypatch.setattr(
        ffmpeg_module, "run_ffmpeg_command", _fake_run_factory(captured)
    )
    assert ffmpeg_module.check_nvdec_available() is True
    assert ffmpeg_module.last_nvdec_probe_error() is None
    # The decode stage ran a real -hwaccel cuda decode of a real file
    # (not a lavfi source, not a listing).
    decode = next(cmd for cmd in captured if "-hwaccel" in cmd)
    assert decode[decode.index("-hwaccel") + 1] == "cuda"
    source = decode[decode.index("-i") + 1]
    assert source.endswith("nvdec_probe_src.mp4")


def test_probe_fails_in_plain_words_when_cuda_not_listed(monkeypatch):
    captured = []
    monkeypatch.setattr(
        ffmpeg_module,
        "run_ffmpeg_command",
        _fake_run_factory(captured, hwaccels="vdpau\nvaapi\n"),
    )
    assert ffmpeg_module.check_nvdec_available() is False
    error = ffmpeg_module.last_nvdec_probe_error()
    assert error is not None and "hwaccel listing" in error


def test_probe_fails_when_real_decode_fails(monkeypatch):
    captured = []
    monkeypatch.setattr(
        ffmpeg_module,
        "run_ffmpeg_command",
        _fake_run_factory(captured, decode_rc=1),
    )
    assert ffmpeg_module.check_nvdec_available() is False
    error = ffmpeg_module.last_nvdec_probe_error()
    assert error is not None and "realistic decode" in error


def test_disable_nvdec_takes_it_away_for_the_process(monkeypatch):
    captured = []
    monkeypatch.setattr(
        ffmpeg_module, "run_ffmpeg_command", _fake_run_factory(captured)
    )
    assert ffmpeg_module.check_nvdec_available() is True
    ffmpeg_module.disable_nvdec("a production decode failed")
    assert ffmpeg_module.check_nvdec_available() is False
    assert ffmpeg_module.last_nvdec_probe_error() == "a production decode failed"


def test_input_args_empty_by_default_even_when_probe_would_pass(monkeypatch):
    captured = []
    monkeypatch.setattr(
        ffmpeg_module, "run_ffmpeg_command", _fake_run_factory(captured)
    )
    # Flag unset: no probe is even run, and the args are empty.
    assert ffmpeg_module.nvdec_input_args() == []
    assert captured == []


def test_input_args_present_only_when_enabled_and_probed(monkeypatch):
    captured = []
    monkeypatch.setattr(
        ffmpeg_module, "run_ffmpeg_command", _fake_run_factory(captured)
    )
    monkeypatch.setenv("ENABLE_NVDEC", "true")
    assert ffmpeg_module.nvdec_input_args() == ["-hwaccel", "cuda"]


def test_input_args_empty_when_enabled_but_probe_fails(monkeypatch):
    captured = []
    monkeypatch.setattr(
        ffmpeg_module,
        "run_ffmpeg_command",
        _fake_run_factory(captured, hwaccels="vdpau\n"),
    )
    monkeypatch.setenv("ENABLE_NVDEC", "1")
    assert ffmpeg_module.nvdec_input_args() == []
