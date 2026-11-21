"""RENDER_PROFILE matrix over both encoder-selection points.

The balanced expectations below are the exact argument lists the code
produced before RENDER_PROFILE existed (captured from the pre-change
tree). They are the regression guard: the balanced profile -- the
default, and the fallback for unknown values -- must stay byte-identical
so the speed profile can never silently change default quality. The
speed profile's values are the contract from the speed lane: NVENC
p2/CQ 24, libx264 veryfast/CRF 21, on both selection points.
"""

import pytest

from src.media import edl_render
from src.media import ffmpeg as ffmpeg_module
from src.media.common import get_render_profile

X264_TAIL = [
    "-pix_fmt", "yuv420p",
    "-profile:v", "high",
    "-level", "4.1",
    "-r", "30",
    "-x264-params", "keyint=120:min-keyint=30:scenecut=40",
]

# --- Balanced: byte-identical to the pre-profile code ---------------------

A_NVENC_BALANCED = [
    "-c:v", "h264_nvenc", "-preset", "p4", "-rc", "vbr", "-cq", "22",
    "-pix_fmt", "yuv420p", "-profile:v", "high",
]
A_CPU_BALANCED = ["-c:v", "libx264", "-preset", "veryfast", "-crf", "20", *X264_TAIL]
B_NVENC_BALANCED = [*A_NVENC_BALANCED, "-level", "4.1", "-r", "30"]
B_CPU_BALANCED = ["-c:v", "libx264", "-preset", "medium", "-crf", "19", *X264_TAIL]

# --- Speed ----------------------------------------------------------------

A_NVENC_SPEED = [
    "-c:v", "h264_nvenc", "-preset", "p2", "-rc", "vbr", "-cq", "24",
    "-pix_fmt", "yuv420p", "-profile:v", "high",
]
A_CPU_SPEED = ["-c:v", "libx264", "-preset", "veryfast", "-crf", "21", *X264_TAIL]
B_NVENC_SPEED = [*A_NVENC_SPEED, "-level", "4.1", "-r", "30"]
B_CPU_SPEED = ["-c:v", "libx264", "-preset", "veryfast", "-crf", "21", *X264_TAIL]


@pytest.fixture(autouse=True)
def _no_force_cpu(monkeypatch):
    monkeypatch.delenv("FORCE_CPU_ENCODE", raising=False)


def _set_profile(monkeypatch, value):
    if value is None:
        monkeypatch.delenv("RENDER_PROFILE", raising=False)
    else:
        monkeypatch.setenv("RENDER_PROFILE", value)


def _set_probe(monkeypatch, available):
    monkeypatch.setattr(ffmpeg_module, "check_nvenc_available", lambda: available)
    monkeypatch.setattr(edl_render, "check_nvenc_available", lambda: available)


@pytest.mark.parametrize("profile", [None, "balanced", "BALANCED", "turbo", ""])
def test_balanced_and_unknown_profiles_keep_todays_args(monkeypatch, profile):
    _set_profile(monkeypatch, profile)

    _set_probe(monkeypatch, True)
    assert edl_render.get_video_encoder() == ("h264_nvenc", A_NVENC_BALANCED)
    assert ffmpeg_module.build_final_video_encode_args() == B_NVENC_BALANCED
    assert ffmpeg_module.build_nvenc_edl_encode_args() == A_NVENC_BALANCED

    _set_probe(monkeypatch, False)
    assert edl_render.get_video_encoder() == ("libx264", A_CPU_BALANCED)
    assert ffmpeg_module.build_final_video_encode_args() == B_CPU_BALANCED
    # Caller-supplied quality settings pass through untouched.
    assert ffmpeg_module.build_final_video_encode_args(crf=24, preset="fast") == [
        "-c:v", "libx264", "-preset", "fast", "-crf", "24", *X264_TAIL
    ]


def test_balanced_nvenc_keeps_caller_fps_and_level(monkeypatch):
    _set_profile(monkeypatch, None)
    _set_probe(monkeypatch, True)
    args = ffmpeg_module.build_final_video_encode_args(fps=60, level="4.2")
    assert args == [*A_NVENC_BALANCED, "-level", "4.2", "-r", "60"]


def test_speed_profile_args_on_both_paths(monkeypatch):
    _set_profile(monkeypatch, "speed")

    _set_probe(monkeypatch, True)
    assert edl_render.get_video_encoder() == ("h264_nvenc", A_NVENC_SPEED)
    assert ffmpeg_module.build_final_video_encode_args() == B_NVENC_SPEED
    assert ffmpeg_module.build_nvenc_edl_encode_args() == A_NVENC_SPEED
    # fps/level are not quality knobs; the speed profile leaves them alone.
    assert ffmpeg_module.build_final_video_encode_args(fps=60, level="4.2") == [
        *A_NVENC_SPEED, "-level", "4.2", "-r", "60"
    ]

    _set_probe(monkeypatch, False)
    assert edl_render.get_video_encoder() == ("libx264", A_CPU_SPEED)
    assert ffmpeg_module.build_final_video_encode_args() == B_CPU_SPEED
    # The profile overrides the caller's quality settings by design.
    assert (
        ffmpeg_module.build_final_video_encode_args(crf=24, preset="fast")
        == B_CPU_SPEED
    )


def test_get_render_profile_values(monkeypatch):
    _set_profile(monkeypatch, None)
    assert get_render_profile() == "balanced"
    _set_profile(monkeypatch, "speed")
    assert get_render_profile() == "speed"
    _set_profile(monkeypatch, " SPEED ")
    assert get_render_profile() == "speed"
    _set_profile(monkeypatch, "balanced")
    assert get_render_profile() == "balanced"
    _set_profile(monkeypatch, "ludicrous")
    assert get_render_profile() == "balanced"
