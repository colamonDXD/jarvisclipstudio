"""NVENC probe regression: the health-check frame must clear NVENC minimums.

The probe used a 64x64 one-frame encode. NVENC rejects frames below its
minimum supported dimensions (176x144) with "Frame Dimension less than the
minimum supported value" — on healthy GPUs too — so the probe always
failed, every render silently fell back to libx264, and the concurrency
cap for CPU-only machines applied. These tests pin the probe size in both
copies of the check (src/media/ffmpeg.py and src/media/edl_render.py).
"""

import subprocess

from src.media import edl_render, ffmpeg as ffmpeg_module


def _fake_run_factory(captured):
    def fake_run(command, timeout=30):
        captured.append(command)
        if "-encoders" in command:
            return subprocess.CompletedProcess(
                command, 0, stdout=" V..... h264_nvenc  NVIDIA NVENC H.264 encoder"
            )
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    return fake_run


def _probe_size(commands):
    probe = next(cmd for cmd in commands if "h264_nvenc" in cmd and "-encoders" not in cmd)
    lavfi = probe[probe.index("-i") + 1]
    assert lavfi.startswith("color=size=")
    size = lavfi.split("size=")[1].split(":")[0]
    width, height = (int(part) for part in size.split("x"))
    return width, height


def test_ffmpeg_module_probe_frame_clears_nvenc_minimum(monkeypatch):
    captured = []
    monkeypatch.setattr(ffmpeg_module, "run_ffmpeg_command", _fake_run_factory(captured))
    ffmpeg_module.check_nvenc_available.cache_clear()
    try:
        assert ffmpeg_module.check_nvenc_available() is True
    finally:
        ffmpeg_module.check_nvenc_available.cache_clear()
    width, height = _probe_size(captured)
    assert width >= 176 and height >= 144


def test_edl_render_probe_frame_clears_nvenc_minimum(monkeypatch):
    captured = []
    monkeypatch.setattr(edl_render, "run_ffmpeg_command", _fake_run_factory(captured))
    edl_render.check_nvenc_available.cache_clear()
    try:
        assert edl_render.check_nvenc_available() is True
    finally:
        edl_render.check_nvenc_available.cache_clear()
    width, height = _probe_size(captured)
    assert width >= 176 and height >= 144


def test_probe_still_fails_closed_without_gpu(monkeypatch):
    def fake_run(command, timeout=30):
        if "-encoders" in command:
            return subprocess.CompletedProcess(
                command, 0, stdout=" V..... h264_nvenc  NVIDIA NVENC H.264 encoder"
            )
        return subprocess.CompletedProcess(command, 1, stdout="", stderr="Cannot load libnvidia-encode.so.1")

    monkeypatch.setattr(ffmpeg_module, "run_ffmpeg_command", fake_run)
    ffmpeg_module.check_nvenc_available.cache_clear()
    try:
        assert ffmpeg_module.check_nvenc_available() is False
    finally:
        ffmpeg_module.check_nvenc_available.cache_clear()
