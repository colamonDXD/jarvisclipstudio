"""Eco render mode (RENDER_ECO_MODE): scheduling only, never quality.

Pins the eco behaviours — concurrency resolves to 1, render-path
ffmpeg commands are thread-bounded at the eco bound and nice-prefixed
— plus the always-on thread budget (RTX 4050 performance lane: ffmpeg's
unbounded defaults oversubscribe the target laptop even with the flag
off, so the bound applies in both modes and only the nice prefix and
the tighter bound are eco-only), and the quality constants and
encoder-argument builders scheduling must never move.
"""

import os
import subprocess

import pytest

from src.media import common, edl_render, ffmpeg, reframing
from src.services import task_service as task_service_module
from src.services.task_service import clip_render_concurrency

BASE_COMMAND = [
    "ffmpeg", "-y", "-i", "in.mp4",
    "-filter_complex", "[0:v]scale=1080:1920[v]",
    "-map", "[v]",
    "-c:v", "libx264", "-preset", "medium", "-crf", "19",
    "-movflags", "+faststart", "out.mp4",
]


@pytest.fixture(autouse=True)
def _clean_eco_env(monkeypatch):
    monkeypatch.delenv("RENDER_ECO_MODE", raising=False)
    monkeypatch.delenv("CLIP_RENDER_CONCURRENCY", raising=False)


def _eco_on(monkeypatch):
    monkeypatch.setenv("RENDER_ECO_MODE", "true")


def _patch_budget_inputs(monkeypatch, cores=12, gpu=False):
    """Deterministic inputs for render_thread_budget: core count + probe."""
    monkeypatch.setattr(os, "cpu_count", lambda: cores)
    monkeypatch.setattr(ffmpeg, "check_nvenc_available", lambda: gpu)
    monkeypatch.delenv("FORCE_CPU_ENCODE", raising=False)


# --- the flag itself -------------------------------------------------------


@pytest.mark.parametrize("value", ["1", "true", "TRUE", "yes", "on", " On "])
def test_render_eco_mode_truthy_values(monkeypatch, value):
    monkeypatch.setenv("RENDER_ECO_MODE", value)
    assert ffmpeg.render_eco_mode() is True


@pytest.mark.parametrize("value", ["", "0", "false", "no", "off", "garbage"])
def test_render_eco_mode_falsy_values(monkeypatch, value):
    monkeypatch.setenv("RENDER_ECO_MODE", value)
    assert ffmpeg.render_eco_mode() is False


def test_render_eco_mode_unset_is_off():
    assert ffmpeg.render_eco_mode() is False


# --- the command transform -------------------------------------------------


def test_eco_command_bounds_threads_when_off(monkeypatch):
    # Flag off: still filter-thread-bounded (12 cores -> budget 8),
    # but NO nice prefix even when the platform provides one.
    _patch_budget_inputs(monkeypatch, cores=12, gpu=False)
    monkeypatch.setattr(ffmpeg.shutil, "which", lambda name: "/usr/bin/nice")
    wrapped = ffmpeg.eco_render_command(list(BASE_COMMAND))
    assert wrapped[0] == "ffmpeg"
    assert wrapped[1:4] == ["-y", "-filter_threads", "8"]
    assert "-threads" not in wrapped
    assert wrapped[-1] == "out.mp4"


def test_eco_command_bounds_threads_and_keeps_quality_args(monkeypatch):
    _eco_on(monkeypatch)
    monkeypatch.setattr(ffmpeg.shutil, "which", lambda name: None)
    wrapped = ffmpeg.eco_render_command(list(BASE_COMMAND))
    # Filter threads: global option, right after the leading -y.
    assert wrapped[1:4] == ["-y", "-filter_threads", str(ffmpeg.ECO_FFMPEG_THREADS)]
    # No output -threads, ever: an explicit encoder thread count
    # silently breaks zoompan-driven zooms (see ffmpeg.py).
    assert "-threads" not in wrapped
    assert wrapped[-1] == "out.mp4"
    # Every quality-bearing argument survives, in order.
    quality = ["-c:v", "libx264", "-preset", "medium", "-crf", "19"]
    positions = [wrapped.index(arg) for arg in quality]
    assert positions == sorted(positions)


def test_eco_command_prefixes_nice_when_available(monkeypatch):
    _eco_on(monkeypatch)
    monkeypatch.setattr(ffmpeg.shutil, "which", lambda name: "/usr/bin/nice")
    wrapped = ffmpeg.eco_render_command(list(BASE_COMMAND))
    assert wrapped[:3] == ["/usr/bin/nice", "-n", str(ffmpeg.ECO_NICE_LEVEL)]
    assert wrapped[3] == "ffmpeg"


def test_eco_command_leaves_non_ffmpeg_commands_alone(monkeypatch):
    _eco_on(monkeypatch)
    probe = ["ffprobe", "-v", "error", "in.mp4"]
    assert ffmpeg.eco_render_command(list(probe)) == probe


# --- concurrency resolution --------------------------------------------------


def _set_encoder(monkeypatch, gpu):
    monkeypatch.setattr(task_service_module, "check_nvenc_available", lambda: gpu)
    monkeypatch.setattr(task_service_module, "force_cpu_encode", lambda: False)


@pytest.mark.parametrize(
    "gpu,env,expected",
    [
        (True, None, 1),   # eco outranks the GPU default of 3
        (True, "5", 1),    # eco outranks an explicit CLIP_RENDER_CONCURRENCY
        (False, None, 1),
        (False, "7", 1),
    ],
)
def test_eco_forces_single_render_concurrency(monkeypatch, gpu, env, expected):
    _eco_on(monkeypatch)
    _set_encoder(monkeypatch, gpu)
    if env is not None:
        monkeypatch.setenv("CLIP_RENDER_CONCURRENCY", env)
    assert clip_render_concurrency() == expected


@pytest.mark.parametrize(
    "gpu,env,expected",
    [
        (True, None, 3),   # today's GPU default
        (True, "5", 5),    # today's explicit override
        (False, None, 1),  # today's CPU default
    ],
)
def test_concurrency_unchanged_when_eco_off(monkeypatch, gpu, env, expected):
    _set_encoder(monkeypatch, gpu)
    if env is not None:
        monkeypatch.setenv("CLIP_RENDER_CONCURRENCY", env)
    assert clip_render_concurrency() == expected


# --- quality is out of eco's reach ------------------------------------------


def test_quality_constants_are_the_shipped_values():
    assert common.FINAL_VIDEO_CRF == 19
    assert common.FINAL_VIDEO_PRESET == "medium"
    assert common.FINAL_VIDEO_NVENC_PRESET == "p4"
    assert common.FINAL_VIDEO_NVENC_CQ_OFFSET == 3
    assert common.NVENC_BALANCED_CQ == 22
    assert edl_render.CPU_FALLBACK_PRESET == "veryfast"
    assert edl_render.CPU_FALLBACK_CRF == 20


@pytest.mark.parametrize("gpu", [True, False])
def test_encoder_args_identical_with_eco_on_and_off(monkeypatch, gpu):
    monkeypatch.setattr(ffmpeg, "check_nvenc_available", lambda: gpu)
    monkeypatch.setattr(edl_render, "check_nvenc_available", lambda: gpu)
    off = (
        ffmpeg.build_final_video_encode_args(),
        ffmpeg.select_prestage_video_encoder(),
        edl_render.get_video_encoder(),
    )
    _eco_on(monkeypatch)
    on = (
        ffmpeg.build_final_video_encode_args(),
        ffmpeg.select_prestage_video_encoder(),
        edl_render.get_video_encoder(),
    )
    assert on == off


# --- the transform is actually applied on the render paths -------------------


def _recorder(captured, returncode=0):
    def fake(command, timeout=900):
        captured.append(list(command))
        return subprocess.CompletedProcess(command, returncode, "", "")

    return fake


def test_edl_render_command_goes_through_the_transform(monkeypatch):
    captured = []
    monkeypatch.setattr(edl_render, "run_ffmpeg_command", _recorder(captured))
    _patch_budget_inputs(monkeypatch, cores=12, gpu=False)
    ok, _ = edl_render._run_render_command(list(BASE_COMMAND))
    assert ok
    # Eco off: thread-bounded at the budget, no nice prefix.
    assert captured[0][0] == "ffmpeg"
    assert "-threads" not in captured[0]
    assert captured[0][-1] == "out.mp4"

    captured.clear()
    _eco_on(monkeypatch)
    monkeypatch.setattr(ffmpeg.shutil, "which", lambda name: None)
    ok, _ = edl_render._run_render_command(list(BASE_COMMAND))
    assert ok
    assert "-threads" not in captured[0]
    assert captured[0][-1] == "out.mp4"


def test_prestage_encode_goes_through_the_transform(monkeypatch):
    captured = []
    monkeypatch.setattr(ffmpeg, "run_ffmpeg_command", _recorder(captured))
    _patch_budget_inputs(monkeypatch, cores=12, gpu=False)
    build = lambda args: ["ffmpeg", "-y", "-i", "in.mp4", *args, "mid.mp4"]
    assert ffmpeg._run_prestage_encode(build, "libx264", ["-c:v", "libx264"], 60)
    assert "-threads" not in captured[0]

    captured.clear()
    _eco_on(monkeypatch)
    monkeypatch.setattr(ffmpeg.shutil, "which", lambda name: None)
    assert ffmpeg._run_prestage_encode(build, "libx264", ["-c:v", "libx264"], 60)
    assert "-threads" not in captured[0]


def test_cap_pass_goes_through_the_transform(monkeypatch, tmp_path):
    video = tmp_path / "clip.mp4"
    video.write_bytes(b"original")
    captured = []

    def fake(command, timeout=900):
        captured.append(list(command))
        (tmp_path / ".clip.capped.mp4").write_bytes(b"capped")
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(ffmpeg, "run_ffmpeg_command", fake)
    monkeypatch.setattr(ffmpeg, "ffprobe_video_size", lambda path: (1440, 2560))
    _patch_budget_inputs(monkeypatch, cores=12, gpu=False)
    assert ffmpeg.cap_video_short_side(video, 1080) is True
    assert "-threads" not in captured[0]

    video.write_bytes(b"original")
    captured.clear()
    _eco_on(monkeypatch)
    monkeypatch.setattr(ffmpeg.shutil, "which", lambda name: None)
    assert ffmpeg.cap_video_short_side(video, 1080) is True
    assert "-threads" not in captured[0]


def test_reframed_render_goes_through_the_transform(monkeypatch, tmp_path):
    captured = []
    monkeypatch.setattr(reframing, "run_ffmpeg_command", _recorder(captured))
    # Oversized for the 1080p cap, so the original-format branch must
    # actually encode (an in-size source takes a no-encode shortcut).
    monkeypatch.setattr(reframing, "ffprobe_video_size", lambda path: (2560, 1440))
    monkeypatch.setattr(reframing, "ffprobe_has_audio", lambda path: False)
    monkeypatch.setattr(reframing, "probe_video_geometry", lambda path: None)
    source = tmp_path / "in.mp4"
    source.write_bytes(b"x")
    _patch_budget_inputs(monkeypatch, cores=12, gpu=False)
    ok, _, _ = reframing.render_reframed_clip_ffmpeg(
        source, tmp_path / "out.mp4", "original"
    )
    assert ok
    assert captured and "-threads" not in captured[0] and "-filter_threads" in captured[0]

    captured.clear()
    _eco_on(monkeypatch)
    monkeypatch.setattr(ffmpeg.shutil, "which", lambda name: None)
    ok, _, _ = reframing.render_reframed_clip_ffmpeg(
        source, tmp_path / "out.mp4", "original"
    )
    assert ok
    assert captured and "-threads" not in captured[0] and "-filter_threads" in captured[0]


# --- the always-on thread budget (RTX 4050 performance lane) -----------------


@pytest.mark.parametrize(
    "cores,gpu,env,expected",
    [
        (12, False, None, 8),   # the target laptop, CPU encoder: 12/1 clamped to 8
        (12, True, None, 4),    # GPU default fan-out 3: 12/3
        (12, True, "1", 8),     # explicit serial rendering: 12/1 clamped to 8
        (12, False, "2", 6),    # explicit pair: 12/2
        (4, False, None, 4),    # small machine, CPU: 4/1
        (2, False, None, 2),    # floor: never below RENDER_THREAD_MIN
        (64, False, None, 8),   # cap: never above RENDER_THREAD_MAX
    ],
)
def test_render_thread_budget_matrix(monkeypatch, cores, gpu, env, expected):
    _patch_budget_inputs(monkeypatch, cores=cores, gpu=gpu)
    if env is not None:
        monkeypatch.setenv("CLIP_RENDER_CONCURRENCY", env)
    assert ffmpeg.render_thread_budget() == expected


def test_render_thread_budget_eco_wins_over_cores(monkeypatch):
    _patch_budget_inputs(monkeypatch, cores=64, gpu=True)
    _eco_on(monkeypatch)
    assert ffmpeg.render_thread_budget() == ffmpeg.ECO_FFMPEG_THREADS


@pytest.mark.parametrize(
    "gpu,env",
    [(True, None), (True, "5"), (False, None), (False, "2")],
)
def test_resolved_concurrency_mirrors_task_service(monkeypatch, gpu, env):
    # ffmpeg._resolved_render_concurrency is the documented mirror of
    # task_service.clip_render_concurrency (import cycle prevents
    # sharing the function); pin the two to the same answers.
    _patch_budget_inputs(monkeypatch, gpu=gpu)
    _set_encoder(monkeypatch, gpu)
    if env is not None:
        monkeypatch.setenv("CLIP_RENDER_CONCURRENCY", env)
    assert ffmpeg._resolved_render_concurrency() == clip_render_concurrency()
