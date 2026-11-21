"""Render encoder diagnostics stamp (RTX 4050 performance lane).

Before this stamp, a failed NVENC probe was visible only in the worker
log (``last_nvenc_probe_error()`` had no consumers): a machine whose
probe failed rendered every clip on libx264 with nothing in the app
saying so. These tests pin the stamp — ffmpeg.render_encoder_diagnostics
— and its ride through the F-P3 timings channel into the task's
persisted stage_timings_json (_collect_clip_render_timings).
"""

import pytest

from src import video_utils
from src.media import edl_render, ffmpeg
from src.services.task_service import _collect_clip_render_timings


_ORIGINAL_CHECK_NVENC_AVAILABLE = ffmpeg.check_nvenc_available


@pytest.fixture(autouse=True)
def _clean_probe_state(monkeypatch):
    monkeypatch.delenv("FORCE_CPU_ENCODE", raising=False)
    ffmpeg._reset_nvenc_probe_state()
    yield
    # Tests replace check_nvenc_available with a plain lambda, which has
    # no cache_clear for the reset helper to call. Restore the cached
    # original first (monkeypatch will restore it again at teardown).
    ffmpeg.check_nvenc_available = _ORIGINAL_CHECK_NVENC_AVAILABLE
    ffmpeg._reset_nvenc_probe_state()


def test_diagnostics_names_nvenc_when_probe_passes(monkeypatch):
    monkeypatch.setattr(ffmpeg, "check_nvenc_available", lambda: True)
    assert ffmpeg.render_encoder_diagnostics() == {"encoder": "h264_nvenc"}


def test_diagnostics_names_cpu_and_carries_probe_error(monkeypatch):
    monkeypatch.setattr(ffmpeg, "check_nvenc_available", lambda: False)
    ffmpeg._record_nvenc_probe_error("realistic encode", "driver exploded")
    diagnostics = ffmpeg.render_encoder_diagnostics()
    assert diagnostics["encoder"] == "libx264"
    assert "driver exploded" in diagnostics["nvenc_probe_error"]


def test_diagnostics_force_cpu_wins_over_passing_probe(monkeypatch):
    monkeypatch.setattr(ffmpeg, "check_nvenc_available", lambda: True)
    monkeypatch.setenv("FORCE_CPU_ENCODE", "true")
    assert ffmpeg.render_encoder_diagnostics() == {"encoder": "libx264"}


def test_stamp_render_timings_publishes_the_stamp(monkeypatch):
    monkeypatch.setattr(
        video_utils,
        "render_encoder_diagnostics",
        lambda: {"encoder": "libx264", "nvenc_probe_error": "probe said no"},
    )
    layout_report = {}
    video_utils._stamp_render_timings(layout_report, {"encode_seconds": 1.25})
    published = layout_report["render_timings"]
    assert published["encode_seconds"] == 1.25
    assert published["encoder"] == "libx264"
    assert published["nvenc_probe_error"] == "probe said no"


def test_edl_render_stamps_timings(monkeypatch, tmp_path):
    monkeypatch.setattr(
        edl_render, "_render_edl_clip_ffmpeg_impl", lambda *a, **k: True
    )
    monkeypatch.setattr(
        edl_render,
        "render_encoder_diagnostics",
        lambda: {"encoder": "h264_nvenc"},
    )
    timings = {}
    ok = edl_render.render_edl_clip_ffmpeg(
        tmp_path / "in.mp4",
        tmp_path / "out.mp4",
        {"segments": [{"start": 0, "end": 1}]},
        timings=timings,
    )
    assert ok
    assert timings["encoder"] == "h264_nvenc"
    assert "total_seconds" in timings


def test_collector_carries_the_stamp_into_stage_timings():
    stage_timings = {}
    clip_info = {
        "filename": "clip.mp4",
        "render_timings": {
            "encode_seconds": 3.25,
            "encoder": "libx264",
            "nvenc_probe_error": "NVENC probe failed at the realistic encode stage",
        },
    }
    _collect_clip_render_timings(stage_timings, 1, clip_info)
    (entry,) = stage_timings["clips"]
    assert entry["encode_seconds"] == 3.25
    assert entry["encoder"] == "libx264"
    assert entry["nvenc_probe_error"].startswith("NVENC probe failed")


def test_collector_ignores_non_string_stamp_values():
    stage_timings = {}
    clip_info = {
        "filename": "clip.mp4",
        "render_timings": {"encode_seconds": 1.0, "encoder": 42},
    }
    _collect_clip_render_timings(stage_timings, 1, clip_info)
    (entry,) = stage_timings["clips"]
    assert "encoder" not in entry
