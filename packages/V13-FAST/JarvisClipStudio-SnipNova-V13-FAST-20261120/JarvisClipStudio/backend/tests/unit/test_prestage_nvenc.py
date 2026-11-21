"""F-P1: the range pre-stage stitch uses NVENC when probed healthy.

The pre-stage stitch (render_source_ranges_ffmpeg, the producer behind
edl_render._materialise_range_prestage) used to hard-code libx264 CPU
even on a machine whose NVENC probe passed. Now it selects its encoder
through select_prestage_video_encoder: h264_nvenc preset p5 / CQ 18
when the probe the main encode already consumes says NVENC is healthy,
else the unchanged libx264 veryfast / CRF 16.

The contract these tests pin, through the fake-ffmpeg PATH shim (see
fake_ffmpeg_shim.py) with no monkeypatched internals:

- the NVENC command carries the pinned quality (p5 / CQ 18) and the
  CPU command is byte-identical to the pre-change intermediate
  (veryfast / CRF 16);
- the filter graph -- the only thing that positions the joins -- is
  identical between the two encoders, so the join anchors cannot move
  with the encoder choice (the sync guard);
- an NVENC failure whose stderr implicates the encoder retries once
  on libx264 and takes NVENC away for the process (the EDL escape-hatch
  discipline); a filter/input failure never retries;
- FORCE_CPU_ENCODE and a failed probe both pin the stitch to libx264;
- RENDER_PROFILE=speed does not cheapen the pre-stage: it is
  re-encoded by the main pass, so its quality is pinned.

A final real-ffmpeg test runs the CPU fallback leg end to end on a
synthetic source (this sandbox has no GPU, so the probe fails here and
the stitch must take the CPU path and still produce a correct-duration
intermediate).
"""

import logging
import shutil
import subprocess
from pathlib import Path

import pytest

from fake_ffmpeg_shim import (
    ffmpeg_calls,
    install_fake_ffmpeg,
    render_calls,
    set_scenario,
)
from src.media import ffmpeg as ffmpeg_module
from src.media.ffmpeg import render_source_ranges_ffmpeg


@pytest.fixture(autouse=True)
def _clean_probe_state():
    ffmpeg_module._reset_nvenc_probe_state()
    yield
    ffmpeg_module._reset_nvenc_probe_state()


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    monkeypatch.delenv("FORCE_CPU_ENCODE", raising=False)
    monkeypatch.delenv("RENDER_PROFILE", raising=False)


def _video_encode_args(call: list) -> list:
    """The video-encoder args of a render call: '-c:v' up to the audio args."""
    start = call.index("-c:v")
    end = len(call)
    for stop in ("-c:a", "-movflags"):
        if stop in call[start:]:
            end = min(end, call.index(stop, start))
    return call[start:end]


def _filter_complex(call: list) -> str:
    return call[call.index("-filter_complex") + 1]


def _render_for_output(state, output: Path) -> list:
    """The production render call(s) that wrote ``output``."""
    return [c for c in ffmpeg_calls(state) if c[-1] == str(output)]


def test_crossfade_prestage_uses_nvenc_when_probe_healthy(tmp_path, monkeypatch):
    state = install_fake_ffmpeg(tmp_path, "ok", monkeypatch)
    output = tmp_path / "prestage.mp4"

    assert render_source_ranges_ffmpeg(
        Path("source.mp4"), [(0.0, 5.0), (6.0, 11.0)], output
    )

    calls = render_calls(state)
    assert len(calls) == 1
    assert _video_encode_args(calls[0]) == [
        "-c:v", "h264_nvenc",
        "-preset", "p5",
        "-rc", "vbr",
        "-cq", "18",
        "-pix_fmt", "yuv420p",
        "-profile:v", "high",
    ]
    # Audio handling is untouched by the encoder swap.
    assert "-c:a" in calls[0] and "aac" in calls[0]
    assert "xfade=" in _filter_complex(calls[0])


def test_crossfade_prestage_graph_identical_between_encoders(tmp_path, monkeypatch):
    """The sync guard: only the encoder args may differ, never the graph.

    The filter graph is the only thing that positions the joins; if the
    graph is byte-identical under NVENC and under CPU, the join anchors
    cannot move with the encoder choice.
    """
    state = install_fake_ffmpeg(tmp_path, "ok", monkeypatch)
    nvenc_out = tmp_path / "prestage-nvenc.mp4"
    assert render_source_ranges_ffmpeg(
        Path("source.mp4"), [(0.0, 5.0), (6.0, 11.0)], nvenc_out
    )
    nvenc_graph = _filter_complex(render_calls(state)[0])

    ffmpeg_module._reset_nvenc_probe_state()
    set_scenario(state, "realistic_fail")
    cpu_out = tmp_path / "prestage-cpu.mp4"
    assert render_source_ranges_ffmpeg(
        Path("source.mp4"), [(0.0, 5.0), (6.0, 11.0)], cpu_out
    )
    cpu_calls = [c for c in render_calls(state) if c[-1] == str(cpu_out)]
    assert len(cpu_calls) == 1
    assert _filter_complex(cpu_calls[0]) == nvenc_graph
    assert _video_encode_args(cpu_calls[0]) == [
        "-c:v", "libx264",
        "-preset", "veryfast",
        "-crf", "16",
        "-pix_fmt", "yuv420p",
    ]


def test_single_range_prestage_uses_nvenc_when_healthy(tmp_path, monkeypatch):
    state = install_fake_ffmpeg(tmp_path, "ok", monkeypatch)
    output = tmp_path / "single.mp4"

    assert render_source_ranges_ffmpeg(Path("source.mp4"), [(2.0, 7.0)], output)

    calls = _render_for_output(state, output)
    assert len(calls) == 1
    assert "-filter_complex" not in calls[0]
    assert _video_encode_args(calls[0])[:8] == [
        "-c:v", "h264_nvenc",
        "-preset", "p5",
        "-rc", "vbr",
        "-cq", "18",
    ]
    # The seek window is unchanged by the encoder swap.
    assert calls[0][calls[0].index("-ss") + 1] == "2.000"


def test_hard_concat_prestage_uses_nvenc_when_healthy(tmp_path, monkeypatch):
    # Nine ranges: crossfade_fade_for_ranges refuses >8 ranges, so the
    # stitch takes the hard-concat producer instead of the crossfade one.
    state = install_fake_ffmpeg(tmp_path, "ok", monkeypatch)
    output = tmp_path / "concat.mp4"
    ranges = [(i * 10.0, i * 10.0 + 5.0) for i in range(9)]

    assert render_source_ranges_ffmpeg(Path("source.mp4"), ranges, output)

    calls = render_calls(state)
    assert len(calls) == 1
    assert "xfade=" not in _filter_complex(calls[0])
    assert "concat=n=9" in _filter_complex(calls[0])
    assert _video_encode_args(calls[0])[:8] == [
        "-c:v", "h264_nvenc",
        "-preset", "p5",
        "-rc", "vbr",
        "-cq", "18",
    ]


def test_prestage_uses_cpu_when_probe_fails(tmp_path, monkeypatch):
    state = install_fake_ffmpeg(tmp_path, "realistic_fail", monkeypatch)
    output = tmp_path / "prestage.mp4"

    assert render_source_ranges_ffmpeg(
        Path("source.mp4"), [(0.0, 5.0), (6.0, 11.0)], output
    )

    calls = render_calls(state)
    assert len(calls) == 1
    assert _video_encode_args(calls[0]) == [
        "-c:v", "libx264",
        "-preset", "veryfast",
        "-crf", "16",
        "-pix_fmt", "yuv420p",
    ]


def test_prestage_uses_cpu_when_force_cpu_encode(tmp_path, monkeypatch):
    monkeypatch.setenv("FORCE_CPU_ENCODE", "1")
    state = install_fake_ffmpeg(tmp_path, "ok", monkeypatch)
    output = tmp_path / "prestage.mp4"

    assert render_source_ranges_ffmpeg(
        Path("source.mp4"), [(0.0, 5.0), (6.0, 11.0)], output
    )

    calls = render_calls(state)
    assert len(calls) == 1
    assert _video_encode_args(calls[0]) == [
        "-c:v", "libx264",
        "-preset", "veryfast",
        "-crf", "16",
        "-pix_fmt", "yuv420p",
    ]


def test_prestage_speed_profile_does_not_cheapen_quality(tmp_path, monkeypatch):
    monkeypatch.setenv("RENDER_PROFILE", "speed")
    state = install_fake_ffmpeg(tmp_path, "ok", monkeypatch)
    output = tmp_path / "prestage.mp4"

    assert render_source_ranges_ffmpeg(
        Path("source.mp4"), [(0.0, 5.0), (6.0, 11.0)], output
    )

    calls = render_calls(state)
    assert len(calls) == 1
    # Pinned: p5 / CQ 18 even under the speed profile.
    assert "-preset" in calls[0] and calls[0][calls[0].index("-preset") + 1] == "p5"
    assert calls[0][calls[0].index("-cq") + 1] == "18"


def test_prestage_nvenc_failure_retries_once_on_cpu(tmp_path, monkeypatch, caplog):
    state = install_fake_ffmpeg(tmp_path, "nvenc_render_fail", monkeypatch)
    output = tmp_path / "prestage.mp4"

    with caplog.at_level(logging.WARNING, logger="src.media.common"):
        assert render_source_ranges_ffmpeg(
            Path("source.mp4"), [(0.0, 5.0), (6.0, 11.0)], output
        )

    calls = render_calls(state)
    assert len(calls) == 2  # the NVENC attempt and the single CPU retry
    assert "h264_nvenc" in calls[0]
    assert _video_encode_args(calls[1]) == [
        "-c:v", "libx264",
        "-preset", "veryfast",
        "-crf", "16",
        "-pix_fmt", "yuv420p",
    ]
    assert "disabling NVENC for this process" in caplog.text
    assert ffmpeg_module.check_nvenc_available() is False
    assert "pre-stage stitch" in (ffmpeg_module.last_nvenc_probe_error() or "")


def test_prestage_nvenc_failure_then_cpu_failure_is_a_hard_error(tmp_path, monkeypatch):
    state = install_fake_ffmpeg(tmp_path, "nvenc_render_fail_cpu_fail", monkeypatch)
    output = tmp_path / "prestage.mp4"

    assert not render_source_ranges_ffmpeg(
        Path("source.mp4"), [(0.0, 5.0), (6.0, 11.0)], output
    )

    calls = render_calls(state)
    assert len(calls) == 3
    # Crossfade on NVENC, its single CPU retry, then the caller's
    # hard-concat fallback (CPU now, since NVENC was taken away).
    assert "h264_nvenc" in calls[0] and "libx264" in calls[1]
    assert "libx264" in calls[2] and "h264_nvenc" not in calls[2]
    assert ffmpeg_module.check_nvenc_available() is False


def test_prestage_filter_failure_does_not_retry(tmp_path, monkeypatch):
    state = install_fake_ffmpeg(tmp_path, "filter_fail", monkeypatch)
    output = tmp_path / "prestage.mp4"

    assert not render_source_ranges_ffmpeg(
        Path("source.mp4"), [(0.0, 5.0), (6.0, 11.0)], output
    )

    calls = render_calls(state)
    # Two attempts, both on NVENC: the crossfade stitch, then the
    # caller's hard-concat fallback for a failed crossfade. Neither
    # retried on CPU -- the error names no encoder, so no retry is due.
    assert len(calls) == 2
    assert all("h264_nvenc" in c for c in calls)
    assert all("libx264" not in c for c in calls)
    # The failure did not implicate NVENC, so the probe verdict stands.
    assert ffmpeg_module.check_nvenc_available() is True


pytestmark = pytest.mark.skipif(
    shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None,
    reason="ffmpeg/ffprobe not installed",
)


def _make_real_source(path: Path) -> None:
    subprocess.run(
        [
            "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
            "-f", "lavfi", "-i", "testsrc2=size=320x180:rate=30:duration=6",
            "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000:duration=6",
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "16",
            "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-b:a", "192k",
            "-shortest", str(path),
        ],
        check=True,
    )


def _probe_duration(path: Path) -> float:
    out = subprocess.run(
        [
            "ffprobe", "-v", "error",
            "-show_entries", "format=duration",
            "-of", "csv=p=0", str(path),
        ],
        capture_output=True, text=True, check=True,
    ).stdout.strip()
    return float(out)


def test_prestage_cpu_leg_renders_real_source(tmp_path):
    """Sandbox leg: no GPU here, so the probe fails and the CPU path runs.

    A two-range crossfade stitch of a real source must still produce a
    correct-duration intermediate -- the fallback leg end to end.
    """
    source = tmp_path / "source.mp4"
    _make_real_source(source)
    output = tmp_path / "prestage.mp4"

    # The stitch must take the CPU fallback, not fail, when NVENC is not
    # healthy. The probe now works on GPU hosts (the -filter_complex_script
    # fix), so take NVENC away the way production does after a failed
    # encode; the autouse fixture restores the probe state afterwards.
    ffmpeg_module.disable_nvenc("test: simulate a host without healthy NVENC")
    assert ffmpeg_module.check_nvenc_available() is False
    assert render_source_ranges_ffmpeg(source, [(0.5, 2.5), (3.0, 5.0)], output)
    assert output.exists()
    # Two 2.0 s ranges joined by the 0.15 s crossfade: 4.0 - 0.15.
    assert _probe_duration(output) == pytest.approx(3.85, abs=0.15)
