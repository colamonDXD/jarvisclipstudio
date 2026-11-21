"""NVDEC wiring: opt-in hardware decode on the render paths.

The default is the regression guard: with the gate closed (flag off or
probe failed), every render command must be byte-identical to the
software-decode command the renderer has always built -- the prefix
assertions below are that legacy shape. With the gate open,
``-hwaccel cuda`` lands on the SOURCE input only, and a decode-shaped
failure triggers the escape hatch: NVDEC is disabled for the process
and the clip retries once with software decode, mirroring the NVENC
hatch. These tests drive render_edl_clip_ffmpeg with a recording
_run_render_command, so no GPU and no real ffmpeg run are involved.
"""

import subprocess
from pathlib import Path

import pytest

from src.media import edl_render
from src.media import ffmpeg as ffmpeg_module


@pytest.fixture(autouse=True)
def _clean_state(monkeypatch):
    ffmpeg_module._reset_nvdec_probe_state()
    monkeypatch.delenv("ENABLE_NVDEC", raising=False)
    monkeypatch.setattr(
        edl_render,
        "get_video_encoder",
        lambda *a, **k: ("libx264", ["-c:v", "libx264", "-crf", "20"]),
    )
    yield
    ffmpeg_module._reset_nvdec_probe_state()


def _edl():
    return {
        "duration": 8,
        "segments": [
            {"start": 0, "end": 4, "transition_in": "none", "transition_duration": 0},
            {"start": 4, "end": 8, "transition_in": "cut", "transition_duration": 0},
        ],
        "overlays": [],
    }


def _recorder(monkeypatch, results):
    calls = []

    def fake_run(command):
        calls.append(list(command))
        return results[min(len(calls) - 1, len(results) - 1)]

    monkeypatch.setattr(edl_render, "_run_render_command", fake_run)
    return calls


def _gate(monkeypatch, args):
    monkeypatch.setattr(edl_render, "nvdec_input_args", lambda: list(args))


def test_default_edl_command_is_the_legacy_software_command(tmp_path, monkeypatch):
    _gate(monkeypatch, [])
    calls = _recorder(monkeypatch, [(True, "")])

    assert edl_render.render_edl_clip_ffmpeg(
        Path("source.mp4"), tmp_path / "clip.mp4", _edl()
    ) is True

    assert len(calls) == 1
    command = calls[0]
    assert "-hwaccel" not in command
    # Legacy shape: no seek window on this EDL, so the source input
    # follows -y directly, exactly as before the NVDEC wiring existed.
    assert command[:4] == ["ffmpeg", "-y", "-i", "source.mp4"]


def test_enabled_edl_command_hwaccels_the_source_input(tmp_path, monkeypatch):
    _gate(monkeypatch, ["-hwaccel", "cuda"])
    calls = _recorder(monkeypatch, [(True, "")])

    assert edl_render.render_edl_clip_ffmpeg(
        Path("source.mp4"), tmp_path / "clip.mp4", _edl()
    ) is True

    assert len(calls) == 1
    command = calls[0]
    assert command.count("-hwaccel") == 1
    i = command.index("-hwaccel")
    assert command[i + 1] == "cuda"
    assert command[i + 2] == "-i"
    assert command[i + 3] == "source.mp4"


def test_nvdec_shaped_failure_retries_once_with_software_decode(
    tmp_path, monkeypatch
):
    _gate(monkeypatch, ["-hwaccel", "cuda"])
    calls = _recorder(
        monkeypatch,
        [
            (False, "[h264_cuvid @ 0x55] No decoder for stream #0:0"),
            (True, ""),
        ],
    )

    assert edl_render.render_edl_clip_ffmpeg(
        Path("source.mp4"), tmp_path / "clip.mp4", _edl()
    ) is True

    assert len(calls) == 2
    assert "-hwaccel" in calls[0]
    assert "-hwaccel" not in calls[1]
    # The downgrade sticks for the rest of the process.
    assert ffmpeg_module.last_nvdec_probe_error() is not None
    assert "NVDEC" in ffmpeg_module.last_nvdec_probe_error()


def test_unrelated_failure_neither_retries_nor_disables(tmp_path, monkeypatch):
    _gate(monkeypatch, ["-hwaccel", "cuda"])
    calls = _recorder(monkeypatch, [(False, "Error: filter graph exploded")])

    assert edl_render.render_edl_clip_ffmpeg(
        Path("source.mp4"), tmp_path / "clip.mp4", _edl()
    ) is False

    assert len(calls) == 1
    assert ffmpeg_module.last_nvdec_probe_error() is None


# --- Plain path (render_source_ranges_ffmpeg, single range) ---------------


def _plain_recorder(monkeypatch):
    calls = []

    def fake_run(command, timeout=None):
        calls.append(list(command))
        if command[0] == "ffprobe":
            return subprocess.CompletedProcess(command, 1, stdout="", stderr="")
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    monkeypatch.setattr(ffmpeg_module, "run_ffmpeg_command", fake_run)
    return calls


def test_plain_path_default_command_has_no_hwaccel(monkeypatch):
    calls = _plain_recorder(monkeypatch)
    assert ffmpeg_module.render_source_ranges_ffmpeg(
        Path("source.mp4"), [(10.0, 20.0)], Path("out.mp4")
    ) is True
    render = next(cmd for cmd in calls if "libx264" in cmd)
    assert "-hwaccel" not in render


def test_plain_path_enabled_command_hwaccels_the_source(monkeypatch):
    calls = _plain_recorder(monkeypatch)
    monkeypatch.setattr(
        ffmpeg_module, "nvdec_input_args", lambda: ["-hwaccel", "cuda"]
    )
    assert ffmpeg_module.render_source_ranges_ffmpeg(
        Path("source.mp4"), [(10.0, 20.0)], Path("out.mp4")
    ) is True
    render = next(cmd for cmd in calls if "libx264" in cmd)
    i = render.index("-hwaccel")
    assert render[i + 1] == "cuda"
    assert render[i + 2] == "-i"
