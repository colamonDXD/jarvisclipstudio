"""Repair lane: main-pipeline EDL wiring, ducking sign, loud music warning.

Before this lane, no code in the main task pipeline ever called build_edl:
task_service read ``segment.get("edl")`` and nothing ever set it, so every
pipeline clip rendered through the plain path — no profile transitions,
zooms, music or SFX. These tests pin the wiring seam
(``VideoService.build_pipeline_edl``), the ducking sign fix (profiles store
ducking_db negative) and the loud warning for declared-but-missing music,
and finish with a real FFmpeg end-to-end proof: a clip with a silent gap
rendered through the wiring + the pipeline's render entry must come out
with an audible music floor in the gap.
"""

import logging
import shutil
import subprocess
from pathlib import Path

import pytest

from src.auto_director.edl import load_profile
from src.media import edl_render
from src.media.reframing import render_reframed_clip_ffmpeg
from src.services.video_service import VideoService


# --------------------------------------------------------------------- seam


def _segment(start="00:00", end="00:30", text="welcome back to the show"):
    return {"start_time": start, "end_time": end, "text": text}


def _timed_words():
    # The shape media.timeline helpers produce (text/start/end).
    return [
        {"text": "welcome", "start": 0.0, "end": 0.4},
        {"text": "back", "start": 0.4, "end": 0.7},
        {"text": "friends", "start": 0.7, "end": 1.1},
    ]


def test_pipeline_edl_built_from_segment_words_and_override():
    edl = VideoService.build_pipeline_edl(
        _segment(), _timed_words(), niche_override="travel_vlog"
    )

    assert edl is not None
    assert edl["segments"], "EDL must carry renderable segments"
    assert edl["duration"] == 30.0
    # The audio block carries the profile's music settings verbatim —
    # travel_vlog asks for the "calm" mood at 0.25 with -12 dB ducking.
    profile = load_profile("travel_vlog")
    assert edl["audio"] == {
        "music_mood": profile["audio"]["music_mood"],
        "music_volume": profile["audio"]["music_volume_0_to_1"],
        "ducking_db": profile["audio"]["ducking_db"],
    }
    assert edl["audio"]["music_mood"] == "calm"


def test_pipeline_edl_accepts_rerender_style_words():
    words = [{"word": "hello", "start": 0.0, "end": 0.3}]
    edl = VideoService.build_pipeline_edl(
        _segment(), words, niche_override="travel_vlog"
    )
    assert edl is not None and edl["segments"]


def test_pipeline_edl_honours_classifier_and_fallback():
    by_classifier = VideoService.build_pipeline_edl(
        _segment(), _timed_words(), classifier_niche="travel_vlog"
    )
    assert by_classifier is not None
    assert by_classifier["audio"]["music_mood"] == "calm"

    fallback = VideoService.build_pipeline_edl(_segment(), _timed_words())
    assert fallback is not None
    # No override and no classifier result -> FALLBACK_NICHE (podcast_news).
    assert fallback["audio"]["music_mood"] == load_profile("podcast_news")["audio"]["music_mood"]


def test_pipeline_edl_duration_override_wins():
    edl = VideoService.build_pipeline_edl(
        _segment(), _timed_words(), niche_override="travel_vlog", duration=22.5
    )
    assert edl is not None
    assert edl["duration"] == 22.5


def test_pipeline_edl_build_failure_degrades_to_none(monkeypatch):
    import src.auto_director.edl as edl_module

    def _boom(*_args, **_kwargs):
        raise RuntimeError("synthetic build failure")

    monkeypatch.setattr(edl_module, "build_edl", _boom)
    assert (
        VideoService.build_pipeline_edl(
            _segment(), _timed_words(), niche_override="travel_vlog"
        )
        is None
    )


def test_pipeline_edl_malformed_segment_degrades_to_none():
    assert VideoService.build_pipeline_edl({}, _timed_words()) is None
    assert (
        VideoService.build_pipeline_edl(
            {"start_time": "00:10", "end_time": "00:05"}, _timed_words()
        )
        is None
    )


# ------------------------------------------------------------- ducking sign


def _music_assets(tmp_path: Path) -> Path:
    assets = tmp_path / "assets"
    (assets / "music").mkdir(parents=True)
    (assets / "sfx").mkdir(parents=True, exist_ok=True)
    (assets / "music" / "bed.wav").touch()
    return assets


def _music_edl(ducking_db):
    return {
        "duration": 3,
        "segments": [
            {"start": 0, "end": 3, "transition_in": "none", "transition_duration": 0}
        ],
        "audio": {
            "music_track": "bed",
            "music_volume_0_to_1": 0.25,
            "ducking_db": ducking_db,
        },
    }


def test_negative_ducking_db_engages_sidechain(tmp_path):
    plan = edl_render.build_edl_audio_graph(
        _music_edl(-12.0), 1, _music_assets(tmp_path)
    )
    assert plan is not None
    _, graph, _ = plan
    assert "sidechaincompress" in graph
    assert "ratio=14.00" in graph


def test_positive_ducking_db_behaves_identically(tmp_path):
    assets = _music_assets(tmp_path)
    _, graph_negative, _ = edl_render.build_edl_audio_graph(
        _music_edl(-12.0), 1, assets
    )
    _, graph_positive, _ = edl_render.build_edl_audio_graph(
        _music_edl(12.0), 1, assets
    )
    assert graph_positive == graph_negative


def test_zero_ducking_db_disables_ducking(tmp_path):
    plan = edl_render.build_edl_audio_graph(
        _music_edl(0), 1, _music_assets(tmp_path)
    )
    assert plan is not None
    _, graph, _ = plan
    assert "sidechaincompress" not in graph


# ------------------------------------------------------------ loud warning

ffmpeg_missing = shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None
pytestmark_real = pytest.mark.skipif(
    ffmpeg_missing, reason="ffmpeg/ffprobe not installed"
)


def _run(command: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(command, capture_output=True, text=True, timeout=600)


def _make_tone_source(path: Path, duration: int = 3) -> None:
    result = _run(
        [
            "ffmpeg", "-y", "-v", "error",
            "-f", "lavfi", "-i", f"testsrc2=size=320x240:rate=30:duration={duration}",
            "-f", "lavfi", "-i", f"sine=frequency=440:sample_rate=48000:duration={duration}",
            "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", str(path),
        ]
    )
    assert result.returncode == 0, result.stderr


def test_missing_music_file_returns_no_plan_and_warns(tmp_path, caplog):
    assets = _music_assets(tmp_path)  # has bed.wav, but no "calm" file
    edl = {
        "duration": 3,
        "segments": [
            {"start": 0, "end": 3, "transition_in": "none", "transition_duration": 0}
        ],
        "audio": {"music_mood": "calm", "music_volume": 0.25, "ducking_db": -12.0},
    }
    with caplog.at_level(logging.WARNING):
        plan = edl_render.build_edl_audio_graph(edl, 1, assets)
    assert plan is None
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert any(
        "EDL expects music" in r.getMessage() and "calm" in r.getMessage()
        for r in warnings
    ), [r.getMessage() for r in warnings]


@pytestmark_real
def test_missing_music_file_render_still_succeeds_and_warns(tmp_path, caplog):
    source, output = tmp_path / "source.mp4", tmp_path / "out.mp4"
    _make_tone_source(source)
    assets = _music_assets(tmp_path)
    edl = {
        "duration": 3,
        "segments": [
            {"start": 0, "end": 3, "transition_in": "none", "transition_duration": 0}
        ],
        "audio": {"music_mood": "calm", "music_volume": 0.25, "ducking_db": -12.0},
    }
    with caplog.at_level(logging.WARNING):
        ok = edl_render.render_edl_clip_ffmpeg(source, output, edl, assets_dir=assets)
    assert ok is True
    assert output.is_file()
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert any(
        "EDL expects music" in r.getMessage() and "calm" in r.getMessage()
        for r in warnings
    ), [r.getMessage() for r in warnings]


# ------------------------------------------------------------- end-to-end


def _make_gap_source(path: Path) -> None:
    """15s clip: tone 0-5s, silence 5-8s, tone 8-15s."""
    result = _run(
        [
            "ffmpeg", "-y", "-v", "error",
            "-f", "lavfi", "-i", "testsrc2=size=320x240:rate=30:duration=15",
            "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000:duration=5",
            "-f", "lavfi", "-i", "anullsrc=channel_layout=stereo:sample_rate=48000",
            "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000:duration=7",
            "-filter_complex",
            "[1:a]aformat=channel_layouts=stereo[t0];"
            "[2:a]atrim=0:3[t1];"
            "[3:a]aformat=channel_layouts=stereo[t2];"
            "[t0][t1][t2]concat=n=3:v=0:a=1[a]",
            "-map", "0:v", "-map", "[a]", "-t", "15",
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


def _gap_rms_db(path: Path, start: float = 5.5, end: float = 7.5) -> float:
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
    # ametadata=print writes to stderr, not stdout.
    for line in (result.stdout + result.stderr).splitlines():
        if "RMS_level=" in line:
            raw = line.rsplit("RMS_level=", 1)[1].strip()
            try:
                values.append(float(raw))
            except ValueError:
                pass
    assert values, "astats produced no RMS_level metadata"
    return values[-1]


@pytestmark_real
def test_end_to_end_pipeline_edl_puts_music_in_the_gap(tmp_path, monkeypatch):
    """The point of the lane: seam -> render entry -> audible music floor.

    On the unpatched tree this fails at the first line — the wiring seam
    does not exist, so the pipeline could never produce this EDL at all.
    """
    monkeypatch.delenv("PIXABAY_API_KEY", raising=False)
    source = tmp_path / "source.mp4"
    _make_gap_source(source)

    assets = tmp_path / "assets"
    (assets / "music").mkdir(parents=True)
    (assets / "sfx").mkdir(parents=True)
    _make_calm_mp3(assets / "music" / "calm.mp3")
    monkeypatch.setattr(edl_render, "ASSETS_DIR", assets)

    words = [
        {"text": word, "start": float(i), "end": float(i) + 0.4}
        for i, word in enumerate(
            "welcome back to the show today we walk along the river "
            "and tell stories about the road".split()
        )
    ]
    edl = VideoService.build_pipeline_edl(
        _segment("00:00", "00:15"), words, niche_override="travel_vlog", duration=15.0
    )
    assert edl is not None
    assert edl["audio"]["music_mood"] == "calm"

    out_music = tmp_path / "with_music.mp4"
    ok, _, _ = render_reframed_clip_ffmpeg(source, out_music, "vertical", edl=edl)
    assert ok is True

    # Control: the same render with the EDL omitted takes the plain path.
    out_plain = tmp_path / "plain.mp4"
    ok, _, _ = render_reframed_clip_ffmpeg(source, out_plain, "vertical")
    assert ok is True

    rms_music = _gap_rms_db(out_music)
    rms_plain = _gap_rms_db(out_plain)
    assert rms_music > rms_plain + 10.0, (
        f"gap RMS with music {rms_music:.1f} dB must clear the plain "
        f"render's {rms_plain:.1f} dB by ~10 dB"
    )
