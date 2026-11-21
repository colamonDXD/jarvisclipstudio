"""Audio Studio: per-clip audio overrides (music / cleanup / loudness / fades).

Unit tests pin the validation contract, the plan->EDL resolution and
the exact audio-graph fragments; the render tests run real FFmpeg
against synthetic sources, mirroring test_edl_render_audio.
"""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from src.auto_director.audio_studio import (
    build_edl_audio_studio,
    fade_filters,
    loudnorm_filter,
    noise_reduction_filter,
    normalize_audio_overrides,
    studio_needs_finishing,
    voice_cleanup_chain,
)
from src.auto_director.clip_edits import (
    build_edit_plan,
    normalize_segments,
)
from src.auto_director.audio_studio import audio_overrides_from_plan
from src.media import edl_render
from src.media.common import LOUDNORM_FILTER


def _segments():
    return [
        {"start": 0, "end": 5, "transition_in": "none", "transition_duration": 0},
        {"start": 5, "end": 10, "transition_in": "cut", "transition_duration": 0},
    ]


def _plain_edl():
    return {
        "duration": 3,
        "segments": [{"start": 0, "end": 3, "transition_in": "none", "transition_duration": 0}],
    }


def _make_assets(tmp_path: Path) -> Path:
    assets = tmp_path / "assets"
    (assets / "music").mkdir(parents=True)
    (assets / "sfx").mkdir(parents=True)
    (assets / "music" / "bed.wav").touch()
    return assets


# ------------------------------------------------------------- validation


def test_normalize_keeps_only_set_fields():
    assert normalize_audio_overrides({"music_volume": 40}) == {"music_volume": 40}
    assert normalize_audio_overrides(None) is None
    assert normalize_audio_overrides({}) is None


def test_normalize_drops_zero_cleanup_and_fades_but_keeps_zero_volume():
    result = normalize_audio_overrides(
        {
            "music_volume": 0,
            "ducking_db": 0,
            "noise_reduction": 0,
            "voice_clarity": 0,
            "fade_in": 0,
            "fade_out": 0.0,
        }
    )
    assert result == {"music_volume": 0, "ducking_db": 0}


def test_normalize_rounds_fades_to_tenths():
    assert normalize_audio_overrides({"fade_in": 0.26}) == {"fade_in": 0.3}


@pytest.mark.parametrize(
    "raw",
    [
        {"music_volume": 101},
        {"music_volume": -1},
        {"ducking_db": 31},
        {"noise_reduction": 101},
        {"voice_clarity": -5},
        {"loudness": "-12"},
        {"fade_in": 5.1},
        {"fade_out": -0.5},
        {"music_track": ""},
        {"music_track": "../escape.mp3"},
        {"music_track": "sub/dir.mp3"},
        {"bass_boost": 10},
        "loud",
        {"music_volume": "40"},
    ],
)
def test_normalize_rejects_impossible_values(raw):
    with pytest.raises(ValueError):
        normalize_audio_overrides(raw)


def test_normalize_accepts_track_sentinels_and_filenames():
    assert normalize_audio_overrides({"music_track": "auto"}) == {
        "music_track": "auto"
    }
    assert normalize_audio_overrides({"music_track": "none"}) == {
        "music_track": "none"
    }
    assert normalize_audio_overrides({"music_track": "bed.wav"}) == {
        "music_track": "bed.wav"
    }


def test_plan_round_trip():
    plan = build_edit_plan(
        _segments(),
        audio={"noise_reduction": 30, "loudness": "-16"},
        voiceover={"script": "Hi", "voice": "af_heart", "mode": "hook", "file": "vo_x.wav"},
    )
    assert plan["audio"] == {"noise_reduction": 30, "loudness": "-16"}
    assert plan["voiceover"]["file"] == "vo_x.wav"
    assert audio_overrides_from_plan(plan) == {
        "noise_reduction": 30,
        "loudness": "-16",
    }
    assert audio_overrides_from_plan(None) is None
    assert audio_overrides_from_plan({"audio": {"bogus": 1}}) is None
    # normalize_segments is untouched by the extra plan blocks.
    assert normalize_segments(plan["segments"])[0]["start"] == 0


# ------------------------------------------------------------- resolution


def test_build_edl_audio_studio_resolution():
    assert build_edl_audio_studio(None) is None
    assert build_edl_audio_studio({}) is None
    # "auto" loudness IS today's graph, so it stamps nothing.
    assert build_edl_audio_studio({"loudness": "auto"}) is None
    assert build_edl_audio_studio(
        {
            "music_volume": 35,
            "ducking_db": 18,
            "noise_reduction": 40,
            "voice_clarity": 60,
            "loudness": "-16",
            "fade_in": 0.5,
            "fade_out": 1.0,
            "music_track": "bed.wav",
        }
    ) == {
        "music_volume": 0.35,
        "ducking_db": 18.0,
        "noise_reduction": 40,
        "voice_clarity": 60,
        "loudness": "-16",
        "fade_in": 0.5,
        "fade_out": 1.0,
    }


def test_studio_needs_finishing():
    assert studio_needs_finishing(None) is False
    assert studio_needs_finishing({}) is False
    # Music retunes alone cannot conjure a plan.
    assert studio_needs_finishing({"music_volume": 0.4, "ducking_db": 10.0}) is False
    assert studio_needs_finishing({"noise_reduction": 10}) is True
    assert studio_needs_finishing({"loudness": "off"}) is True
    assert studio_needs_finishing({"fade_in": 0.5}) is True


# ------------------------------------------------------------- filter mapping


def test_filter_mapping_pins():
    assert noise_reduction_filter(100) == "afftdn=nr=30.00:nf=-25"
    assert noise_reduction_filter(40) == "afftdn=nr=12.00:nf=-25"
    assert voice_cleanup_chain(None) == ""
    assert voice_cleanup_chain({}) == ""
    chain = voice_cleanup_chain({"voice_clarity": 50, "noise_reduction": 40})
    assert chain == (
        ",highpass=f=80"
        ",equalizer=f=3000:width_type=o:width=1.5:g=3.00"
        ",afftdn=nr=12.00:nf=-25"
    )
    # The default loudnorm string must never drift from the shared one.
    assert loudnorm_filter(None) == LOUDNORM_FILTER
    assert loudnorm_filter({"loudness": "auto"}) == LOUDNORM_FILTER
    assert loudnorm_filter({"loudness": "-16"}) == "loudnorm=I=-16:TP=-1.5:LRA=11"
    assert loudnorm_filter({"loudness": "off"}) is None
    assert fade_filters(None, 10.0) == []
    assert fade_filters({"fade_in": 0.5, "fade_out": 1.0}, 3.0) == [
        "afade=t=in:st=0:d=0.500",
        "afade=t=out:st=2.000:d=1.000",
    ]
    # A fade longer than the clip clamps instead of erroring.
    assert fade_filters({"fade_out": 5.0}, 3.0) == ["afade=t=out:st=0.000:d=3.000"]


# ------------------------------------------------------------- audio graph


def test_no_studio_graph_is_byte_identical(tmp_path):
    assets = _make_assets(tmp_path)
    edl = _plain_edl()
    edl["audio"] = {"music_track": "bed", "music_volume_0_to_1": 0.2, "ducking_db": 8}
    baseline = edl_render.build_edl_audio_graph(edl, 1, assets)
    assert baseline is not None
    for stamp in ({}, {"music_volume": 0.2, "ducking_db": 8.0}):
        # An empty stamp, or one restating the EDL's own values, must
        # not move a single character of the graph.
        stamped = dict(edl, audio_studio=stamp)
        assert edl_render.build_edl_audio_graph(stamped, 1, assets) == baseline


def test_cleanup_only_studio_forces_a_plan(tmp_path):
    assets = _make_assets(tmp_path)
    edl = _plain_edl()
    assert edl_render.build_edl_audio_graph(edl, 1, assets) is None
    edl["audio_studio"] = {"noise_reduction": 40, "voice_clarity": 50}
    plan = edl_render.build_edl_audio_graph(edl, 1, assets)
    assert plan is not None
    _inputs, graph, label = plan
    assert label == "[a_out]"
    assert "afftdn=nr=12.00:nf=-25" in graph
    assert "highpass=f=80" in graph
    assert "equalizer=f=3000" in graph
    # The finished path still ends on the -14 loudnorm.
    assert graph.rstrip().endswith(f"{LOUDNORM_FILTER}[a_out]")


def test_music_retune_overrides_beat_the_edl_values(tmp_path):
    assets = _make_assets(tmp_path)
    edl = _plain_edl()
    edl["audio"] = {"music_track": "bed", "music_volume_0_to_1": 0.2, "ducking_db": 8}
    edl["audio_studio"] = {"music_volume": 0.3, "ducking_db": 18.0}
    plan = edl_render.build_edl_audio_graph(edl, 1, assets)
    assert plan is not None
    _inputs, graph, _label = plan
    assert "volume=0.300000" in graph
    # Ducking amount 18 drives the ratio to its 20.00 cap.
    assert "ratio=20.00" in graph


def test_loudness_off_and_quiet_graphs(tmp_path):
    assets = _make_assets(tmp_path)
    edl = _plain_edl()
    edl["audio_studio"] = {"loudness": "-16", "voice_clarity": 10}
    plan = edl_render.build_edl_audio_graph(edl, 1, assets)
    assert plan is not None
    assert "loudnorm=I=-16:TP=-1.5:LRA=11" in plan[1]
    assert "loudnorm=I=-14" not in plan[1]

    edl["audio_studio"] = {"loudness": "off", "voice_clarity": 10}
    plan = edl_render.build_edl_audio_graph(edl, 1, assets)
    assert plan is not None
    assert "loudnorm" not in plan[1]
    assert plan[1].rstrip().endswith("anull[a_out]")


def test_fades_ride_the_tail_after_loudnorm(tmp_path):
    assets = _make_assets(tmp_path)
    edl = _plain_edl()
    edl["audio_studio"] = {"fade_in": 0.5, "fade_out": 1.0}
    plan = edl_render.build_edl_audio_graph(edl, 1, assets)
    assert plan is not None
    graph = plan[1]
    loudnorm_at = graph.index("loudnorm=")
    fade_at = graph.index("afade=t=in:st=0:d=0.500")
    assert loudnorm_at < fade_at
    assert "afade=t=out:st=2.000:d=1.000" in graph


# ------------------------------------------------------------- real FFmpeg runs

ffmpeg_missing = shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None
pytestmark_real = pytest.mark.skipif(
    ffmpeg_missing, reason="ffmpeg/ffprobe not installed"
)


def _run(command: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(command, capture_output=True, text=True, timeout=600)


def _make_source(path: Path, duration: int = 3) -> None:
    result = _run([
        "ffmpeg", "-y", "-v", "error",
        "-f", "lavfi", "-i", f"testsrc2=size=320x240:rate=30:duration={duration}",
        "-f", "lavfi", "-i", f"sine=frequency=440:sample_rate=48000:duration={duration}",
        "-t", str(duration), "-c:v", "libx264", "-pix_fmt", "yuv420p",
        "-c:a", "aac", str(path),
    ])
    assert result.returncode == 0, result.stderr


def _probe(path: Path) -> dict:
    result = _run([
        "ffprobe", "-v", "error", "-show_streams", "-show_format",
        "-of", "json", str(path),
    ])
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def _window_mean_volume_db(path: Path, start: float, length: float):
    result = _run([
        "ffmpeg", "-ss", str(start), "-t", str(length), "-i", str(path),
        "-af", "volumedetect", "-f", "null", "-",
    ])
    for line in result.stderr.splitlines():
        if "mean_volume:" in line:
            value = line.rsplit("mean_volume:", 1)[1].strip().replace(" dB", "")
            try:
                return float(value)
            except ValueError:
                return None
    return None


@pytestmark_real
def test_real_render_cleanup_only_edl(tmp_path):
    source, output = tmp_path / "source.mp4", tmp_path / "out.mp4"
    _make_source(source)
    edl = _plain_edl()
    edl["audio_studio"] = {"noise_reduction": 40, "voice_clarity": 50}

    ok = edl_render.render_edl_clip_ffmpeg(
        source, output, edl, assets_dir=tmp_path / "assets"
    )

    assert ok is True
    probe = _probe(output)
    assert {s["codec_type"] for s in probe["streams"]} == {"video", "audio"}
    assert abs(float(probe["format"]["duration"]) - 3.0) < 0.4


@pytestmark_real
def test_real_render_fade_in_silences_the_opening(tmp_path):
    source, output = tmp_path / "source.mp4", tmp_path / "out.mp4"
    _make_source(source)
    edl = _plain_edl()
    edl["audio_studio"] = {"fade_in": 1.0, "fade_out": 1.0}

    ok = edl_render.render_edl_clip_ffmpeg(
        source, output, edl, assets_dir=tmp_path / "assets"
    )

    assert ok is True
    opening = _window_mean_volume_db(output, 0.0, 0.25)
    middle = _window_mean_volume_db(output, 1.4, 0.5)
    ending = _window_mean_volume_db(output, 2.75, 0.25)
    assert opening is not None and middle is not None and ending is not None
    # The faded edges sit far below the untouched middle of the clip.
    assert opening < middle - 12
    assert ending < middle - 12
