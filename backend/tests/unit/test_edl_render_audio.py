"""Lane 4: EDL renderer audio finishing (music ducking, SFX events, loudnorm).

Unit tests pin the graph/command shape; the render tests run real FFmpeg
against synthetic sources and a temp asset library.
"""

import json
import shutil
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from src.media import edl_render
from src.media.ffmpeg import build_audio_output_args


def _encoder(*_args, **_kwargs):
    return "libx264", ["-c:v", "libx264"]


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
    (assets / "sfx" / "whoosh.wav").touch()
    (assets / "sfx" / "pop.wav").touch()
    return assets


# ---------------------------------------------------------------- graph level


def test_no_audio_fields_means_no_audio_plan(tmp_path):
    edl = _plain_edl()
    assert edl_render.build_edl_audio_graph(edl, 1, tmp_path / "assets") is None


def test_music_and_sfx_graph_plan(tmp_path):
    assets = _make_assets(tmp_path)
    edl = _plain_edl()
    edl["audio"] = {"music_track": "bed", "music_volume_0_to_1": 0.2, "ducking_db": 8}
    edl["sfx_events"] = [
        {"name": "whoosh", "at_sec": 1.5, "gain_db": -3},
        {"name": "pop", "at_sec": 2.0, "gain_db": 0},
        {"name": "not_in_library", "at_sec": 1.0, "gain_db": 0},
    ]

    plan = edl_render.build_edl_audio_graph(edl, 1, assets)

    assert plan is not None
    inputs, graph, label = plan
    assert label == "[a_out]"
    # Music loops from input 1; the two resolvable SFX take inputs 2 and 3.
    assert inputs[0:3] == ["-stream_loop", "-1", "-i"]
    assert str(assets / "music" / "bed.wav") in inputs
    assert str(assets / "sfx" / "whoosh.wav") in inputs
    assert str(assets / "sfx" / "pop.wav") in inputs
    assert inputs.count("-i") == 3
    assert "[1:a]" in graph and "[2:a]" in graph and "[3:a]" in graph
    assert "volume=0.200000" in graph
    # Ducking: ducking_db=8 drives sidechaincompress ratio to 2 + 8.
    assert "sidechaincompress" in graph
    assert "ratio=10.00" in graph
    # SFX placement and gain (gain_db=-3 -> linear ~0.708).
    assert "adelay=1500:all=1" in graph
    assert "adelay=2000:all=1" in graph
    assert "volume=0.707946" in graph
    assert "amix=inputs=4" in graph
    assert "normalize=0" in graph
    # Loudnorm (-14 LUFS) is the last audio step, inside the graph.
    assert graph.endswith("[a_mix]loudnorm=I=-14:TP=-1.5:LRA=11[a_out]")


def test_music_by_mood_and_no_ducking_when_zero_db(tmp_path):
    assets = _make_assets(tmp_path)
    edl = _plain_edl()
    edl["audio"] = {"music_mood": "bed", "music_volume": 0.25, "ducking_db": 0}

    plan = edl_render.build_edl_audio_graph(edl, 1, assets)

    assert plan is not None
    _, graph, _ = plan
    assert "sidechaincompress" not in graph
    assert "volume=0.250000" in graph
    assert "amix=inputs=2" in graph


def test_missing_or_disabled_music_means_no_audio_plan(tmp_path):
    assets = _make_assets(tmp_path)
    for audio in (
        {"music_track": "ghost", "music_volume_0_to_1": 0.2, "ducking_db": 6},
        {"music_track": "bed", "music_volume_0_to_1": 0.0, "ducking_db": 6},
        {"music_mood": "none", "music_volume_0_to_1": 0.2, "ducking_db": 6},
    ):
        edl = _plain_edl()
        edl["audio"] = audio
        assert edl_render.build_edl_audio_graph(edl, 1, assets) is None


def test_no_audio_fields_render_command_is_the_legacy_command(monkeypatch, tmp_path):
    monkeypatch.setattr(edl_render, "get_video_encoder", _encoder)
    captured = []
    monkeypatch.setattr(
        edl_render,
        "run_ffmpeg_command",
        lambda command, timeout=0: captured.append(command) or SimpleNamespace(returncode=0),
    )
    edl = _plain_edl()
    source, output = tmp_path / "source.mp4", tmp_path / "out.mp4"

    ok = edl_render.render_edl_clip_ffmpeg(source, output, edl, assets_dir=tmp_path / "assets")

    assert ok is True
    extra_inputs, graph, encoder_args, video_label = edl_render.build_edl_filter_graph(edl, source)
    expected = [
        "ffmpeg", "-y", "-i", str(source), *extra_inputs,
        "-filter_complex", graph, "-map", video_label, "-map", "0:a?",
        *encoder_args, *build_audio_output_args(True),
        "-shortest", "-movflags", "+faststart", str(output),
    ]
    assert captured == [expected]


# ------------------------------------------------------------- real FFmpeg runs

ffmpeg_missing = shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None
pytestmark_real = pytest.mark.skipif(ffmpeg_missing, reason="ffmpeg/ffprobe not installed")


def _run(command: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(command, capture_output=True, text=True, timeout=600)


def _make_source(path: Path, silent: bool) -> None:
    audio = (
        "anullsrc=channel_layout=stereo:sample_rate=48000"
        if silent
        else "sine=frequency=440:sample_rate=48000:duration=3"
    )
    result = _run([
        "ffmpeg", "-y", "-v", "error",
        "-f", "lavfi", "-i", "testsrc2=size=320x240:rate=30:duration=3",
        "-f", "lavfi", "-i", audio,
        "-t", "3", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", str(path),
    ])
    assert result.returncode == 0, result.stderr


def _make_wav(path: Path, frequency: int, duration: float, channels: int = 2) -> None:
    result = _run([
        "ffmpeg", "-y", "-v", "error",
        "-f", "lavfi", "-i", f"sine=frequency={frequency}:sample_rate=48000:duration={duration}",
        "-ac", str(channels), "-c:a", "pcm_s16le", str(path),
    ])
    assert result.returncode == 0, result.stderr


def _make_real_assets(tmp_path: Path) -> Path:
    assets = tmp_path / "assets"
    (assets / "music").mkdir(parents=True)
    (assets / "sfx").mkdir(parents=True)
    _make_wav(assets / "music" / "bed.wav", 220, 1.0)
    _make_wav(assets / "sfx" / "whoosh.wav", 880, 0.3, channels=1)
    _make_wav(assets / "sfx" / "pop.wav", 660, 0.2, channels=1)
    return assets


def _probe(path: Path) -> dict:
    result = _run([
        "ffprobe", "-v", "error", "-show_streams", "-show_format",
        "-of", "json", str(path),
    ])
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def _stream_kinds(probe: dict) -> set:
    return {stream["codec_type"] for stream in probe["streams"]}


def _mean_volume_db(path: Path):
    result = _run(["ffmpeg", "-i", str(path), "-af", "volumedetect", "-f", "null", "-"])
    for line in result.stderr.splitlines():
        if "mean_volume:" in line:
            value = line.rsplit("mean_volume:", 1)[1].strip().replace(" dB", "")
            try:
                return float(value)
            except ValueError:
                return None
    return None


@pytestmark_real
def test_real_render_with_music_and_two_sfx(tmp_path):
    source, output = tmp_path / "source.mp4", tmp_path / "out.mp4"
    _make_source(source, silent=False)
    assets = _make_real_assets(tmp_path)
    edl = _plain_edl()
    edl["audio"] = {"music_track": "bed", "music_volume_0_to_1": 0.3, "ducking_db": 6}
    edl["sfx_events"] = [
        {"name": "whoosh", "at_sec": 0.5, "gain_db": -3},
        {"name": "pop", "at_sec": 2.0, "gain_db": 0},
    ]

    ok = edl_render.render_edl_clip_ffmpeg(source, output, edl, assets_dir=assets)

    assert ok is True
    probe = _probe(output)
    assert _stream_kinds(probe) == {"video", "audio"}
    assert abs(float(probe["format"]["duration"]) - 3.0) < 0.4
    mean_volume = _mean_volume_db(output)
    assert mean_volume is not None and mean_volume > -50


@pytestmark_real
def test_real_render_music_is_audible_over_silent_source(tmp_path):
    """Silent source + music bed: non-silent output proves the music was mixed
    in (the legacy fallback path would have produced silence)."""
    source, output = tmp_path / "source.mp4", tmp_path / "out.mp4"
    _make_source(source, silent=True)
    assets = _make_real_assets(tmp_path)
    edl = _plain_edl()
    edl["audio"] = {"music_track": "bed", "music_volume_0_to_1": 0.5, "ducking_db": 6}

    ok = edl_render.render_edl_clip_ffmpeg(source, output, edl, assets_dir=assets)

    assert ok is True
    assert "audio" in _stream_kinds(_probe(output))
    mean_volume = _mean_volume_db(output)
    assert mean_volume is not None and mean_volume > -45


@pytestmark_real
def test_real_render_missing_music_file_renders_without_music(tmp_path):
    source, output = tmp_path / "source.mp4", tmp_path / "out.mp4"
    _make_source(source, silent=False)
    assets = _make_real_assets(tmp_path)
    edl = _plain_edl()
    edl["audio"] = {"music_track": "ghost_track", "music_volume_0_to_1": 0.3, "ducking_db": 6}

    ok = edl_render.render_edl_clip_ffmpeg(source, output, edl, assets_dir=assets)

    assert ok is True
    probe = _probe(output)
    assert _stream_kinds(probe) == {"video", "audio"}
    assert abs(float(probe["format"]["duration"]) - 3.0) < 0.4


@pytestmark_real
def test_real_render_no_audio_fields_uses_old_path(tmp_path):
    source, output = tmp_path / "source.mp4", tmp_path / "out.mp4"
    _make_source(source, silent=False)
    assets = _make_real_assets(tmp_path)

    ok = edl_render.render_edl_clip_ffmpeg(source, output, _plain_edl(), assets_dir=assets)

    assert ok is True
    probe = _probe(output)
    assert _stream_kinds(probe) == {"video", "audio"}
    assert abs(float(probe["format"]["duration"]) - 3.0) < 0.4
