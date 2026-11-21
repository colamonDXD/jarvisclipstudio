"""NVENC probe regression: the health-check frame must clear NVENC minimums.

The probe's cheap stage used a 64x64 one-frame encode. NVENC rejects
frames below its minimum supported dimensions (176x144) with "Frame
Dimension less than the minimum supported value" — on healthy GPUs too —
so the probe always failed and every render silently fell back to
libx264. These tests pin the probe frame size.

The probe itself now lives in exactly one place (src/media/ffmpeg.py)
and edl_render imports it from there: two cached copies evaluated at
different moments could disagree inside one task. The composed check
also runs a realistic production-shaped encode after the cheap stages,
so the fakes below answer its ffprobe validation too; the stage-by-stage
behaviour is covered by test_nvenc_probe_realistic.py.
"""

import json
import subprocess

from src.media import edl_render, ffmpeg as ffmpeg_module

_PROBE_OUTPUT_JSON = json.dumps(
    {
        "streams": [
            {"codec_type": "video", "codec_name": "h264", "width": 1080, "height": 1920},
            {"codec_type": "audio", "codec_name": "aac"},
        ],
        "format": {"duration": "5.600"},
    }
)


def _fake_run_factory(captured):
    def fake_run(command, timeout=30):
        captured.append(command)
        if command[0] == "ffprobe":
            return subprocess.CompletedProcess(command, 0, stdout=_PROBE_OUTPUT_JSON)
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


def test_edl_render_uses_the_same_single_probe(monkeypatch):
    # One probe for the whole process: edl_render re-exports ffmpeg's.
    assert edl_render.check_nvenc_available is ffmpeg_module.check_nvenc_available

    captured = []
    monkeypatch.setattr(ffmpeg_module, "run_ffmpeg_command", _fake_run_factory(captured))
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
