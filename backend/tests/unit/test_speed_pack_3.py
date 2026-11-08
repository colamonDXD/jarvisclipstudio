"""Speed Pack 3: xfade timebase fix, CPU-fallback encode sanity, CPU-aware
render concurrency, render retry/timeout discipline, planning-time B-roll
resolution, and the single-encode (direct-from-source) EDL render path.

Real-render tests run actual FFmpeg against synthetic sources, following
the patterns in test_edl_pipeline_wiring.py / test_pixabay_broll.py.
"""

import shutil
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from src.media import edl_render, ffmpeg as ffmpeg_module, pixabay_broll
from src.media.ffmpeg import (
    ffprobe_duration,
    render_source_ranges_ffmpeg,
    rendered_ranges_duration,
)
from src.services import task_service as task_service_module
from src.services.task_service import clip_render_concurrency
from src.services.video_service import VideoService
from src import video_utils

pytestmark_real = pytest.mark.skipif(
    shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None,
    reason="ffmpeg/ffprobe not installed",
)


def _run(command: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(command, capture_output=True, text=True, timeout=600)


def _make_video(path: Path, duration: int = 12, with_gap: bool = False) -> None:
    """testsrc2 video; audio is a 440 Hz tone, optionally silent 5-8s."""
    if with_gap:
        audio = [
            "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000:duration=5",
            "-f", "lavfi", "-i", "anullsrc=channel_layout=stereo:sample_rate=48000",
            "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000:duration=7",
            "-filter_complex",
            "[1:a]aformat=channel_layouts=stereo[t0];"
            "[2:a]atrim=0:3[t1];"
            "[3:a]aformat=channel_layouts=stereo[t2];"
            "[t0][t1][t2]concat=n=3:v=0:a=1[a]",
            "-map", "0:v", "-map", "[a]", "-t", "15",
        ]
        duration = 15
    else:
        audio = [
            "-f", "lavfi", "-i",
            f"sine=frequency=440:sample_rate=48000:duration={duration}",
            "-map", "0:v", "-map", "1:a",
        ]
    result = _run(
        [
            "ffmpeg", "-y", "-v", "error",
            "-f", "lavfi", "-i",
            f"testsrc2=size=320x240:rate=30:duration={duration}",
            *audio,
            "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", str(path),
        ]
    )
    assert result.returncode == 0, result.stderr


def _make_calm_mp3(path: Path) -> None:
    result = _run(
        [
            "ffmpeg", "-y", "-v", "error",
            "-f", "lavfi", "-i", "sine=frequency=220:sample_rate=48000:duration=30",
            "-ac", "2", "-c:a", "libmp3lame", "-b:a", "128k", str(path),
        ]
    )
    assert result.returncode == 0, result.stderr


def _music_assets(tmp_path: Path) -> Path:
    assets = tmp_path / "assets"
    (assets / "music").mkdir(parents=True)
    (assets / "sfx").mkdir(parents=True)
    _make_calm_mp3(assets / "music" / "calm.mp3")
    return assets


def _window_rms_db(path: Path, start: float, end: float) -> float:
    result = _run(
        [
            "ffmpeg", "-v", "info", "-i", str(path),
            "-af",
            f"atrim=start={start}:end={end},"
            "astats=metadata=1:reset=0,"
            "ametadata=print:key=lavfi.astats.Overall.RMS_level",
            "-f", "null", "-",
        ]
    )
    values = []
    for line in (result.stdout + result.stderr).splitlines():
        if "RMS_level=" in line:
            raw = line.rsplit("RMS_level=", 1)[1].strip()
            try:
                values.append(float(raw))
            except ValueError:
                pass
    assert values, "astats produced no RMS_level metadata"
    return values[-1]


def _frame_mean_luma(path: Path, at: float) -> float:
    result = subprocess.run(
        [
            "ffmpeg", "-v", "error", "-ss", str(at), "-i", str(path),
            "-frames:v", "1", "-f", "rawvideo", "-pix_fmt", "gray", "-s", "64x64", "-",
        ],
        check=True, capture_output=True, timeout=120,
    )
    data = result.stdout
    assert data, "no frame decoded"
    return sum(data) / len(data)


def _arg_after(args, flag):
    return args[args.index(flag) + 1]


# ---------------------------------------------------------------------------
# 1. xfade timebase normalisation
# ---------------------------------------------------------------------------


def _mixed_edl() -> dict:
    """Concat joins mixed with fadefast xfades plus zoompan segments.

    Segments are source-timeline ranges (direct render call, bench style).
    Expected output: 2 + (2-0.4) + 2 + (2-0.4) = 7.2s.
    """
    return {
        "duration": 12,
        "segments": [
            {"start": 0.0, "end": 2.0, "transition_in": "cut", "transition_duration": 0},
            {"start": 2.5, "end": 4.5, "transition_in": "fadefast", "transition_duration": 0.4},
            {"start": 5.0, "end": 7.0, "transition_in": "cut", "transition_duration": 0},
            {"start": 7.5, "end": 9.5, "transition_in": "fadefast", "transition_duration": 0.4},
        ],
        "zoom_keyframes": [
            {"type": "punch_in", "time": 5.5, "scale": 1.25, "duration": 0.5},
            {"type": "ken_burns", "start": 2.5, "end": 4.5, "scale_start": 1.0, "scale_end": 1.05},
        ],
        "overlays": [],
        "sfx_events": [],
        "broll": [],
        "audio": {"music_track": "none", "music_volume_0_to_1": 0.0, "ducking_db": 0},
    }


def test_every_segment_stream_is_timebase_normalised(monkeypatch):
    monkeypatch.setattr(
        edl_render, "get_video_encoder", lambda: ("libx264", ["-c:v", "libx264"])
    )
    _, graph, _, _ = edl_render.build_edl_filter_graph(_mixed_edl(), Path("source.mp4"))
    assert graph.count("settb=AVTB") == 4  # one per segment, before any join
    assert "xfade=transition=fadefast" in graph
    assert "concat=n=2:v=1:a=0" in graph


@pytestmark_real
def test_mixed_concat_xfade_edl_renders(tmp_path):
    """Regression for the audit §3c hard-fail on FFmpeg 8.1.2."""
    source = tmp_path / "source.mp4"
    _make_video(source, duration=12)
    output = tmp_path / "mixed.mp4"
    assert edl_render.render_edl_clip_ffmpeg(source, output, _mixed_edl()) is True
    assert output.is_file() and output.stat().st_size > 0
    assert abs(ffprobe_duration(output) - 7.2) <= 0.4


# ---------------------------------------------------------------------------
# 2. CPU-fallback encode sanity
# ---------------------------------------------------------------------------


def test_edl_encoder_cpu_fallback_is_veryfast_crf20(monkeypatch):
    monkeypatch.setattr(edl_render, "check_nvenc_available", lambda: False)
    monkeypatch.setattr(ffmpeg_module, "check_nvenc_available", lambda: False)
    monkeypatch.delenv("FORCE_CPU_ENCODE", raising=False)
    name, args = edl_render.get_video_encoder()
    assert name == "libx264"
    assert _arg_after(args, "-preset") == "veryfast"
    assert _arg_after(args, "-crf") == "20"


def test_edl_encoder_nvenc_args_unchanged(monkeypatch):
    monkeypatch.setattr(edl_render, "check_nvenc_available", lambda: True)
    name, args = edl_render.get_video_encoder()
    assert name == "h264_nvenc"
    assert _arg_after(args, "-preset") == "p4"
    assert _arg_after(args, "-cq") == "22"


# ---------------------------------------------------------------------------
# 3. CPU-aware render concurrency
# ---------------------------------------------------------------------------


def test_concurrency_capped_at_two_without_nvenc(monkeypatch):
    monkeypatch.setattr(task_service_module, "check_nvenc_available", lambda: False)
    monkeypatch.setenv("CLIP_RENDER_CONCURRENCY", "5")
    assert clip_render_concurrency() == 2
    monkeypatch.delenv("CLIP_RENDER_CONCURRENCY")
    assert clip_render_concurrency() == 2  # default 3 is capped too
    monkeypatch.setenv("CLIP_RENDER_CONCURRENCY", "1")
    assert clip_render_concurrency() == 1  # a lower explicit value stands


def test_concurrency_uncapped_with_nvenc(monkeypatch):
    monkeypatch.setattr(task_service_module, "check_nvenc_available", lambda: True)
    monkeypatch.setenv("CLIP_RENDER_CONCURRENCY", "5")
    assert clip_render_concurrency() == 5
    monkeypatch.delenv("CLIP_RENDER_CONCURRENCY")
    assert clip_render_concurrency() == 3


# ---------------------------------------------------------------------------
# 4. Retry + timeout discipline
# ---------------------------------------------------------------------------


def test_render_timeout_parsing(monkeypatch):
    monkeypatch.delenv("RENDER_TIMEOUT_SEC", raising=False)
    assert edl_render.render_timeout_sec() == 900
    monkeypatch.setenv("RENDER_TIMEOUT_SEC", "120")
    assert edl_render.render_timeout_sec() == 120
    monkeypatch.setenv("RENDER_TIMEOUT_SEC", "0")
    assert edl_render.render_timeout_sec() == 900
    monkeypatch.setenv("RENDER_TIMEOUT_SEC", "soon")
    assert edl_render.render_timeout_sec() == 900


@pytest.mark.parametrize(
    "stderr,expected",
    [
        ("Error initializing filter 'sidechaincompress': Invalid argument", True),
        ("[a_mix] Channel layout change is not supported", True),
        ("Parsed_loudnorm_0 Failed to configure output pad", True),
        ("[0:a] matches no streams", True),
        ("adelay: Unable to find a suitable output format", True),
        ("xfade timebase (1/30) do not match the timebase of the first input", False),
        ("libx264 @ 0x55: ratecontrol_init: can't open stats file", False),
        ("Conversion failed!", False),
        ("", False),
        (None, False),
    ],
)
def test_failure_classifier(stderr, expected):
    assert edl_render._failure_implicates_audio(stderr) is expected


def _finished_edl() -> dict:
    return {
        "duration": 3,
        "segments": [
            {"start": 0, "end": 3, "transition_in": "none", "transition_duration": 0}
        ],
        "overlays": [],
        "sfx_events": [],
        "broll": [],
        "audio": {"music_track": "calm", "music_volume_0_to_1": 0.25, "ducking_db": -12},
    }


def _fake_runner(monkeypatch, finished_stderr: str):
    calls: list[list[str]] = []

    def fake_run(command, timeout=None):
        calls.append(command)
        if "-encoders" in command:  # NVENC probe: report unavailable
            return SimpleNamespace(returncode=1, stdout="", stderr="")
        graph = (
            command[command.index("-filter_complex") + 1]
            if "-filter_complex" in command
            else ""
        )
        if "sidechaincompress" in graph:  # the audio-finished attempt
            return SimpleNamespace(returncode=1, stdout="", stderr=finished_stderr)
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(edl_render, "run_ffmpeg_command", fake_run)
    return calls


def test_non_audio_failure_fails_fast_without_retry(tmp_path, monkeypatch):
    monkeypatch.delenv("PIXABAY_API_KEY", raising=False)
    assets = _music_assets(tmp_path)
    calls = _fake_runner(
        monkeypatch, "xfade timebase (1/30) do not match the first input"
    )
    ok = edl_render.render_edl_clip_ffmpeg(
        Path("in.mp4"), Path("out.mp4"), _finished_edl(), assets_dir=assets
    )
    assert ok is False
    render_calls = [c for c in calls if "-filter_complex" in c]
    assert len(render_calls) == 1  # no plain-path second encode


def test_audio_failure_still_retries_on_plain_path(tmp_path, monkeypatch):
    monkeypatch.delenv("PIXABAY_API_KEY", raising=False)
    assets = _music_assets(tmp_path)
    calls = _fake_runner(
        monkeypatch, "Error initializing filter 'sidechaincompress'"
    )
    ok = edl_render.render_edl_clip_ffmpeg(
        Path("in.mp4"), Path("out.mp4"), _finished_edl(), assets_dir=assets
    )
    assert ok is True
    render_calls = [c for c in calls if "-filter_complex" in c]
    assert len(render_calls) == 2
    plain_graph = render_calls[1][render_calls[1].index("-filter_complex") + 1]
    assert "sidechaincompress" not in plain_graph


# ---------------------------------------------------------------------------
# 5. B-roll resolution moves to EDL planning
# ---------------------------------------------------------------------------


def test_resolve_is_noop_when_entries_preresolved(monkeypatch):
    monkeypatch.setattr(pixabay_broll, "_api_key", lambda: "test-key")

    def _boom(*_args, **_kwargs):
        raise AssertionError("HTTP client constructed for pre-resolved EDL")

    monkeypatch.setattr(pixabay_broll, "_new_client", _boom)
    edl = {"broll": [
        {"query": "ocean", "local_path": "/cache/pixabay_1.mp4"},
        {"query": "beach", "local_path": "/cache/pixabay_2.mp4"},
    ]}
    assert pixabay_broll.resolve_edl_broll(edl) == 0


def test_planning_resolves_and_stamps_broll(monkeypatch):
    monkeypatch.setattr(pixabay_broll, "_api_key", lambda: "test-key")
    # ensure_pixabay_asset is stubbed, so the client resolve_edl_broll
    # constructs is never used for HTTP; give it an inert one (a bare
    # httpx.Client() reads proxy env, which is not portable in CI).
    import httpx

    def _unused_transport(request):
        raise AssertionError("planning test must not perform HTTP")

    monkeypatch.setattr(
        pixabay_broll,
        "_new_client",
        lambda: httpx.Client(transport=httpx.MockTransport(_unused_transport)),
    )
    monkeypatch.setattr(
        pixabay_broll,
        "ensure_pixabay_asset",
        lambda query, **_kw: {
            "asset_id": 7,
            "page_url": "https://pixabay.com/videos/7",
            "local_path": "/cache/pixabay_7.mp4",
        },
    )
    words = [
        {"word": w, "start": float(i), "end": float(i) + 0.5}
        for i, w in enumerate(["mountain", "beach", "city", "river", "road"])
    ]
    segment = {"id": "c1", "start_time": "00:00", "end_time": "01:00"}
    edl = VideoService.build_pipeline_edl(
        segment, words, niche_override="travel_vlog", duration=60.0
    )
    assert edl is not None
    assert edl["broll"], "travel_vlog should plan B-roll entries"
    for entry in edl["broll"]:
        assert entry["local_path"] == "/cache/pixabay_7.mp4"
        assert entry["asset_id"] == 7
        assert entry["provider"] == "pixabay"


@pytestmark_real
def test_render_with_preresolved_broll_does_no_http(tmp_path, monkeypatch):
    monkeypatch.setattr(pixabay_broll, "_api_key", lambda: "test-key")

    def _boom(*_args, **_kwargs):
        raise AssertionError("B-roll HTTP attempted at render time")

    monkeypatch.setattr(pixabay_broll, "_new_client", _boom)
    monkeypatch.setattr(pixabay_broll, "ensure_pixabay_asset", _boom)

    source = tmp_path / "source.mp4"
    _make_video(source, duration=6)
    stock = tmp_path / "pixabay_123.mp4"
    _make_video(stock, duration=6)
    edl = {
        "duration": 6,
        "segments": [
            {"start": 0, "end": 6, "transition_in": "none", "transition_duration": 0}
        ],
        "overlays": [],
        "sfx_events": [],
        "broll": [{
            "query": "ocean", "start": 2.0, "end": 5.0, "source_start": 0,
            "local_path": str(stock), "asset_id": 123,
        }],
        "audio": {"music_track": "none", "music_volume_0_to_1": 0.0, "ducking_db": 0},
    }
    output = tmp_path / "out.mp4"
    assert edl_render.render_edl_clip_ffmpeg(source, output, edl) is True
    assert output.is_file() and output.stat().st_size > 0


# ---------------------------------------------------------------------------
# 6. Single-encode EDL path (direct from source)
# ---------------------------------------------------------------------------


def test_source_prestage_graph_shape(monkeypatch, tmp_path):
    monkeypatch.setattr(
        edl_render, "get_video_encoder", lambda: ("libx264", ["-c:v", "libx264"])
    )
    edl = {
        "duration": 12,
        "segments": [
            {"start": 0, "end": 6, "transition_in": "none", "transition_duration": 0},
            {"start": 6, "end": 12, "transition_in": "cut", "transition_duration": 0},
        ],
        "overlays": [],
        "source_ranges": [[0.0, 5.0], [8.0, 15.0]],
    }
    _, graph, _, _ = edl_render.build_edl_filter_graph(edl, Path("source.mp4"))
    # Two substantial ranges crossfade in the intermediate (fade 0.15,
    # offset = first range length - fade); the pre-stage must mirror it.
    assert "xfade=transition=fade:duration=0.150000:offset=4.850000" in graph
    assert "[srcv]trim=start=0.000:end=6.000" in graph

    plan = edl_render.build_edl_audio_graph(
        {**_finished_edl(), "duration": 12},
        1,
        assets_dir=_music_assets(tmp_path),
        audio_source_label="[srca]",
    )
    assert plan is not None
    assert "[srca]atrim" in plan[1]
    assert "[0:a]atrim" not in plan[1]


def _equivalence_edl() -> dict:
    return {
        "duration": 15,
        "segments": [
            {"start": 0, "end": 7.5, "transition_in": "none", "transition_duration": 0},
            {"start": 7.5, "end": 15, "transition_in": "cross_dissolve", "transition_duration": 0.8},
        ],
        "zoom_keyframes": [
            {"type": "punch_in", "time": 9.0, "scale": 1.25, "duration": 0.5},
        ],
        "overlays": [],
        "sfx_events": [],
        "broll": [],
        "audio": {"music_track": "calm", "music_volume_0_to_1": 0.25, "ducking_db": -12},
    }


@pytestmark_real
def test_direct_from_source_matches_intermediate_render(tmp_path):
    """Same EDL, two routes: pre-rendered intermediate vs in-graph ranges."""
    source = tmp_path / "source.mp4"
    _make_video(source, with_gap=True)  # tone 0-5, silence 5-8, tone 8-15
    assets = _music_assets(tmp_path)
    edl = _equivalence_edl()
    ranges = [(0.0, 15.0)]

    intermediate = tmp_path / "intermediate.mp4"
    assert render_source_ranges_ffmpeg(source, ranges, intermediate) is True
    out_legacy = tmp_path / "legacy.mp4"
    assert (
        edl_render.render_edl_clip_ffmpeg(intermediate, out_legacy, edl, assets_dir=assets)
        is True
    )

    out_direct = tmp_path / "direct.mp4"
    direct_edl = {**edl, "source_ranges": [list(r) for r in ranges]}
    assert (
        edl_render.render_edl_clip_ffmpeg(source, out_direct, direct_edl, assets_dir=assets)
        is True
    )

    legacy_duration = ffprobe_duration(out_legacy)
    direct_duration = ffprobe_duration(out_direct)
    assert abs(legacy_duration - direct_duration) <= 0.1
    # Music floor audible inside the source's silence gap on both routes
    # (window 5.5-6.5 sits inside the gap, before the 6.7s xfade start).
    for out in (out_legacy, out_direct):
        assert _window_rms_db(out, 5.5, 6.5) > -45.0
    # Mid-clip frame is real picture content, not black.
    assert _frame_mean_luma(out_direct, 3.0) > 10.0


@pytestmark_real
def test_direct_from_source_multi_range_durations_match(tmp_path):
    """Cleanup-style ranges (a cut in the middle) through both routes."""
    source = tmp_path / "source.mp4"
    _make_video(source, with_gap=True)
    assets = _music_assets(tmp_path)
    ranges = [(0.0, 5.0), (8.0, 15.0)]  # 12s kept, 11.85s after the stitch
    duration = rendered_ranges_duration(ranges)
    edl = {
        "duration": duration,
        "segments": [
            {"start": 0, "end": duration / 2, "transition_in": "none", "transition_duration": 0},
            {"start": duration / 2, "end": duration, "transition_in": "cross_dissolve", "transition_duration": 0.8},
        ],
        "zoom_keyframes": [],
        "overlays": [],
        "sfx_events": [],
        "broll": [],
        "audio": {"music_track": "calm", "music_volume_0_to_1": 0.25, "ducking_db": -12},
    }

    intermediate = tmp_path / "intermediate.mp4"
    assert render_source_ranges_ffmpeg(source, ranges, intermediate) is True
    out_legacy = tmp_path / "legacy.mp4"
    assert (
        edl_render.render_edl_clip_ffmpeg(intermediate, out_legacy, edl, assets_dir=assets)
        is True
    )

    out_direct = tmp_path / "direct.mp4"
    direct_edl = {**edl, "source_ranges": [list(r) for r in ranges]}
    assert (
        edl_render.render_edl_clip_ffmpeg(source, out_direct, direct_edl, assets_dir=assets)
        is True
    )

    assert abs(ffprobe_duration(out_legacy) - ffprobe_duration(out_direct)) <= 0.1
    legacy_rms = _window_rms_db(out_legacy, 1.0, 4.0)
    direct_rms = _window_rms_db(out_direct, 1.0, 4.0)
    assert abs(legacy_rms - direct_rms) <= 3.0
    assert _frame_mean_luma(out_direct, 3.0) > 10.0


@pytestmark_real
def test_create_optimized_clip_skips_intermediate_for_edl(tmp_path, monkeypatch):
    """The wiring: an EDL vertical render never builds source.mp4."""
    source = tmp_path / "source.mp4"
    _make_video(source, with_gap=True)
    assets = _music_assets(tmp_path)
    monkeypatch.setattr(edl_render, "ASSETS_DIR", assets)

    def _boom(*_args, **_kwargs):
        raise AssertionError("intermediate encode ran on the direct EDL path")

    monkeypatch.setattr(video_utils, "render_source_ranges_ffmpeg", _boom)

    edl = _equivalence_edl()
    output = tmp_path / "clip.mp4"
    ok = video_utils.create_optimized_clip(
        source, 0.0, 15.0, output,
        add_subtitles=False,
        output_format="vertical",
        keep_ranges=[(0.0, 15.0)],
        extend_to_sentence=False,
        edl=edl,
    )
    assert ok is True
    assert output.is_file() and output.stat().st_size > 0
    assert "source_ranges" not in edl  # caller's EDL is never mutated
    assert _window_rms_db(output, 5.5, 6.5) > -45.0  # music reached the gap
