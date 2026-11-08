"""FORCE_CPU_ENCODE must pin the EDL final encode to libx264 too.

The shared kill-switch (media.ffmpeg.force_cpu_encode) originally only
covered the plain render path: get_video_encoder chose h264_nvenc whenever
the probe passed, with no way to take NVENC out of an EDL render while
debugging a failing clip.
"""

from src.media import edl_render


def test_force_cpu_encode_overrides_nvenc_probe(monkeypatch):
    monkeypatch.setattr(edl_render, "check_nvenc_available", lambda: True)
    monkeypatch.setenv("FORCE_CPU_ENCODE", "true")
    encoder, args = edl_render.get_video_encoder()
    assert encoder == "libx264"
    assert "h264_nvenc" not in args


def test_nvenc_still_chosen_without_the_switch(monkeypatch):
    monkeypatch.setattr(edl_render, "check_nvenc_available", lambda: True)
    monkeypatch.delenv("FORCE_CPU_ENCODE", raising=False)
    encoder, _args = edl_render.get_video_encoder()
    assert encoder == "h264_nvenc"


def test_cpu_fallback_when_probe_fails(monkeypatch):
    monkeypatch.setattr(edl_render, "check_nvenc_available", lambda: False)
    monkeypatch.delenv("FORCE_CPU_ENCODE", raising=False)
    encoder, _args = edl_render.get_video_encoder()
    assert encoder == "libx264"
