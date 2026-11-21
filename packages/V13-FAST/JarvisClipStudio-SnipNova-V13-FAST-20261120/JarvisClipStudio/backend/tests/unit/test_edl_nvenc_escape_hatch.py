"""The NVENC escape hatch: a real encode's verdict beats the probe's.

A probe that passed once used to sentence every clip in the process to
the same death when real encodes failed anyway. Now, when an encode
that selected NVENC fails with the encoder's own fingerprints on the
error, the render path takes NVENC away for the rest of the process and
retries the clip once on libx264. These tests drive the whole path --
probe, selection, render, hatch -- through the fake-ffmpeg PATH shim
(see fake_ffmpeg_shim.py), with no monkeypatched internals.
"""

import logging
from pathlib import Path

import pytest

from fake_ffmpeg_shim import install_fake_ffmpeg, render_calls
from src.media import edl_render
from src.media import ffmpeg as ffmpeg_module


@pytest.fixture(autouse=True)
def _clean_probe_state():
    ffmpeg_module._reset_nvenc_probe_state()
    yield
    ffmpeg_module._reset_nvenc_probe_state()


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    monkeypatch.delenv("FORCE_CPU_ENCODE", raising=False)
    monkeypatch.delenv("RENDER_PROFILE", raising=False)


def _edl():
    return {
        "duration": 8,
        "segments": [
            {"start": 0, "end": 4, "transition_in": "none", "transition_duration": 0},
            {"start": 4, "end": 8, "transition_in": "cut", "transition_duration": 0},
        ],
        "overlays": [],
    }


def test_nvenc_render_failure_retries_once_on_cpu(tmp_path, monkeypatch, caplog):
    state = install_fake_ffmpeg(tmp_path, "nvenc_render_fail", monkeypatch)
    output = tmp_path / "clip.mp4"

    with caplog.at_level(logging.ERROR, logger="src.media.common"):
        assert (
            edl_render.render_edl_clip_ffmpeg(Path("source.mp4"), output, _edl())
            is True
        )

    calls = render_calls(state)
    assert len(calls) == 2
    assert "h264_nvenc" in calls[0]
    assert "libx264" in calls[1] and "h264_nvenc" not in calls[1]
    # The downgrade is announced loudly, in plain words.
    assert "disabling NVENC for this process" in caplog.text
    # And the process now believes NVENC is gone, with the reason kept.
    assert ffmpeg_module.check_nvenc_available() is False
    assert "production encode" in (ffmpeg_module.last_nvenc_probe_error() or "")


def test_nvenc_failure_then_cpu_failure_is_a_hard_error(tmp_path, monkeypatch):
    state = install_fake_ffmpeg(tmp_path, "nvenc_render_fail_cpu_fail", monkeypatch)
    output = tmp_path / "clip.mp4"

    assert (
        edl_render.render_edl_clip_ffmpeg(Path("source.mp4"), output, _edl())
        is False
    )

    calls = render_calls(state)
    assert len(calls) == 2  # the NVENC attempt and the single CPU retry
    assert "h264_nvenc" in calls[0] and "libx264" in calls[1]
    assert ffmpeg_module.check_nvenc_available() is False


def test_filter_failure_does_not_trigger_the_hatch(tmp_path, monkeypatch):
    state = install_fake_ffmpeg(tmp_path, "filter_fail", monkeypatch)
    output = tmp_path / "clip.mp4"

    assert (
        edl_render.render_edl_clip_ffmpeg(Path("source.mp4"), output, _edl())
        is False
    )

    # One attempt only: a graph error would fail identically on libx264,
    # so the clip keeps the fail-fast behaviour and NVENC stays trusted.
    assert len(render_calls(state)) == 1
    assert ffmpeg_module.check_nvenc_available() is True
    assert ffmpeg_module.last_nvenc_probe_error() is None


@pytest.mark.parametrize(
    "stderr",
    [
        "[nvenc @ 0x55] OpenEncodeSessionEx failed: unsupported device",
        "Cannot load libnvidia-encode.so.1",
        "Error while opening encoder for output stream #0:0",
        "[h264_nvenc @ 0x55] Cannot get the preset configuration",
        "cuda error: out of memory",
    ],
)
def test_marker_detection_finds_encoder_failures(stderr):
    assert edl_render._nvenc_failure_marker(stderr) is not None


@pytest.mark.parametrize(
    "stderr",
    [
        None,
        "",
        "No such filter: 'bogusfilter'",
        "Invalid argument",
        "Conversion failed!",
        "Error opening input file source.mp4",
    ],
)
def test_marker_detection_ignores_other_failures(stderr):
    assert edl_render._nvenc_failure_marker(stderr) is None
