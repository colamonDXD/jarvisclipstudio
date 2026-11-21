"""The realistic NVENC probe: production-shaped, single-sourced, never silent.

The old check (an encoder listing plus one 256x256 frame into the null
muxer) passed on machines where every real 1080x1920 graph encode then
failed, and when it failed itself nothing recorded why. These tests drive
the composed probe (listing, one-frame pre-filter, realistic encode)
through a PATH shim for ffmpeg/ffprobe -- see fake_ffmpeg_shim.py -- and
pin the stage order, the recorded error, and the cache semantics.
"""

import shutil
import subprocess
from pathlib import Path

import pytest

from src.media import ffmpeg as ffmpeg_module

from fake_ffmpeg_shim import (
    ffmpeg_calls,
    install_fake_ffmpeg,
    read_calls,
    tool_name,
)


@pytest.fixture(autouse=True)
def _clean_probe_state():
    ffmpeg_module._reset_nvenc_probe_state()
    yield
    ffmpeg_module._reset_nvenc_probe_state()


def _assert_subsequence(haystack, needle):
    for start in range(len(haystack) - len(needle) + 1):
        if haystack[start : start + len(needle)] == needle:
            return
    raise AssertionError(f"{needle} is not a contiguous part of {haystack}")


def test_probe_graph_mirrors_the_production_skeleton():
    graph = ffmpeg_module._nvenc_probe_graph(Path("/tmp/probe.ass"), None)

    # Per-segment production chain: trim, fps, scale/crop to 1080x1920.
    assert "trim=start=0:end=3.2" in graph
    assert "scale=1080:1920:force_original_aspect_ratio=increase" in graph
    assert "crop=1080:1920" in graph
    # Punch-in zoompan on the first segment.
    assert "zoompan" in graph
    # Speed-pack-3 timebase normalisation before the join.
    assert "fps=30,settb=AVTB" in graph
    # An xfade join with the production fade/offset arithmetic.
    assert "xfade=transition=fade:duration=0.400000:offset=2.800000" in graph
    # A real libass subtitles burn and the loudnorm audio finish.
    assert "subtitles=filename=" in graph
    assert "loudnorm=I=-14:TP=-1.5:LRA=11" in graph


def test_probe_passes_and_uses_the_production_encoder_args(tmp_path, monkeypatch):
    state = install_fake_ffmpeg(tmp_path, "ok", monkeypatch)
    monkeypatch.delenv("RENDER_PROFILE", raising=False)

    assert ffmpeg_module.check_nvenc_available() is True
    assert ffmpeg_module.last_nvenc_probe_error() is None

    calls = ffmpeg_calls(state)
    realistic = next(c for c in calls if c[-1].endswith("nvenc_probe.mp4"))
    # The probe validates the exact arguments the EDL path renders with.
    _assert_subsequence(realistic, ffmpeg_module.build_nvenc_edl_encode_args())
    # The graph goes inline: FFmpeg 8-era builds no longer accept
    # -filter_complex_script, which made the probe fail and forced CPU.
    assert "-filter_complex_script" not in realistic
    graph = realistic[realistic.index("-filter_complex") + 1]
    assert "xfade=transition=fade" in graph
    assert "subtitles=filename=" in graph
    # And the output was validated with ffprobe, not taken on faith.
    assert any(tool_name(c) == "ffprobe" for c in read_calls(state))


def test_probe_stops_at_the_listing_and_records_why(tmp_path, monkeypatch):
    state = install_fake_ffmpeg(tmp_path, "no_listing", monkeypatch)

    assert ffmpeg_module.check_nvenc_available() is False

    error = ffmpeg_module.last_nvenc_probe_error()
    assert error is not None and "encoder listing" in error
    calls = ffmpeg_calls(state)
    assert len(calls) == 1  # no encode of any kind was attempted


def test_probe_stops_at_the_one_frame_prefilter(tmp_path, monkeypatch):
    state = install_fake_ffmpeg(tmp_path, "oneframe_fail", monkeypatch)

    assert ffmpeg_module.check_nvenc_available() is False

    error = ffmpeg_module.last_nvenc_probe_error()
    assert error is not None and "one-frame" in error
    calls = ffmpeg_calls(state)
    assert not any(c[-1].endswith("nvenc_probe.mp4") for c in calls)


def test_probe_realistic_failure_records_the_driver_stderr(tmp_path, monkeypatch):
    install_fake_ffmpeg(tmp_path, "realistic_fail", monkeypatch)

    assert ffmpeg_module.check_nvenc_available() is False

    error = ffmpeg_module.last_nvenc_probe_error()
    assert error is not None
    assert "realistic encode" in error
    # The failing stage's stderr tail is kept -- the probe is never silent.
    assert "libnvidia-encode" in error


def test_probe_rejects_output_with_the_wrong_geometry(tmp_path, monkeypatch):
    install_fake_ffmpeg(tmp_path, "probe_bad_output", monkeypatch)

    assert ffmpeg_module.check_nvenc_available() is False

    error = ffmpeg_module.last_nvenc_probe_error()
    assert error is not None and "output validation" in error


def test_probe_verdict_is_cached_until_cleared(tmp_path, monkeypatch):
    state = install_fake_ffmpeg(tmp_path, "ok", monkeypatch)

    assert ffmpeg_module.check_nvenc_available() is True
    calls_after_first = len(ffmpeg_calls(state))
    assert ffmpeg_module.check_nvenc_available() is True
    assert len(ffmpeg_calls(state)) == calls_after_first  # nothing respawned

    ffmpeg_module.check_nvenc_available.cache_clear()
    assert ffmpeg_module.check_nvenc_available() is True
    assert len(ffmpeg_calls(state)) > calls_after_first  # a fresh probe ran


def test_realistic_stage_itself_is_not_cached(tmp_path, monkeypatch):
    state = install_fake_ffmpeg(tmp_path, "ok", monkeypatch)

    assert ffmpeg_module.check_nvenc_realistic() is True
    assert ffmpeg_module.check_nvenc_realistic() is True

    realistic = [c for c in ffmpeg_calls(state) if c[-1].endswith("nvenc_probe.mp4")]
    assert len(realistic) == 2


def test_disable_nvenc_takes_the_encoder_away(tmp_path, monkeypatch):
    install_fake_ffmpeg(tmp_path, "ok", monkeypatch)

    assert ffmpeg_module.check_nvenc_available() is True
    ffmpeg_module.disable_nvenc("a production encode failed with an NVENC error")
    assert ffmpeg_module.check_nvenc_available() is False
    assert (
        ffmpeg_module.last_nvenc_probe_error()
        == "a production encode failed with an NVENC error"
    )


@pytest.mark.skipif(
    not shutil.which("ffmpeg") or not shutil.which("ffprobe"),
    reason="needs a real ffmpeg/ffprobe",
)
def test_probe_graph_cpu_twin_renders_for_real(tmp_path):
    """The section-2 graph, executed by the real ffmpeg with CPU args.

    This VM has no GPU, so the NVENC leg cannot run here; the identical
    fixtures and graph with the production CPU arguments substituted must
    produce a valid 1080x1920, 5.6s clip -- the same validation the
    research lane ran by hand.
    """
    ass_path = tmp_path / "probe.ass"
    ass_path.write_text(ffmpeg_module._NVENC_PROBE_ASS, encoding="utf-8")
    fonts_dir = ffmpeg_module.FONTS_DIR if ffmpeg_module.FONTS_DIR.is_dir() else None
    output_path = tmp_path / "twin.mp4"
    command = [
        "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
        "-f", "lavfi", "-i",
        f"testsrc2=size=1920x1080:rate={ffmpeg_module.OUTPUT_FPS}:duration=6",
        "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000:duration=6",
        "-filter_complex", ffmpeg_module._nvenc_probe_graph(ass_path, fonts_dir),
        "-map", "[finalv]", "-map", "[aout]",
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
        "-pix_fmt", "yuv420p", "-profile:v", "high",
        "-c:a", "aac", "-b:a", "192k", "-ar", "48000",
        "-shortest", "-movflags", "+faststart", str(output_path),
    ]
    result = subprocess.run(command, capture_output=True, text=True, timeout=300)
    assert result.returncode == 0, result.stderr[-2000:]

    assert ffmpeg_module._validate_nvenc_probe_output(output_path) is True
    assert ffmpeg_module.ffprobe_video_size(output_path) == (1080, 1920)
    assert abs(ffmpeg_module.ffprobe_duration(output_path) - 5.6) <= 0.3
