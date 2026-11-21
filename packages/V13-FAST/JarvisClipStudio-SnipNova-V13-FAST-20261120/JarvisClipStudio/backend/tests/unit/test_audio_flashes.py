"""Audio-reactive flashes (Viral Scout Pack item 3).

Covers: the profile opt-in config (stock declarations, validation and
clamps), transient detection on planted level curves (labels, the
loudness gates, the drop's own evidence, the clip-edge guard), the
spacing / per-clip / rolling-rate restraint caps on a dense fixture,
the source-level projection, the EDL stamp (including rendered-time
mapping through ramps and crossfades), Plain stripping, the renderer
fragments, and real FFmpeg renders proving the pulse lands on the
planted hits — and that a Plain render stays flat.
"""

import json
import math
import shutil
import subprocess
import sys
import wave
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from src.auto_director.audio_flashes import (
    detect_flash_points,
    plan_audio_flashes,
    profile_flash_config,
    project_source_levels,
)
from src.auto_director.beat_grid import energy_levels_db
from src.auto_director.edl import _rendered_time_in_segments, build_edl, load_profile
from src.auto_director.reference_style import _decode_audio
from src.auto_director.template_mode import plainify_edl
from src.media import edl_render

HOP = 0.05


def _levels(duration=10.0, base=-40.0):
    return [base] * int(duration / HOP)


def _plant_hit(levels, at_sec, peak=-6.0):
    """A drum-like hit: two attack windows, the peak, two decay windows."""
    centre = int(at_sec / HOP)
    levels[centre - 2] = peak - 16.0
    levels[centre - 1] = peak - 8.0
    levels[centre] = peak
    levels[centre + 1] = peak - 6.0
    levels[centre + 2] = peak - 15.0
    return levels


def _opted_profile(**overrides):
    flashes = {"enabled": True, "intensity": 0.5}
    flashes.update(overrides)
    return {"effects": {"audio_flashes": flashes}}


# ------------------------------------------------------------------ config


def test_stock_profiles_opt_in_and_out():
    opted = {
        "gaming", "meme_comedy", "sports",
        "music_artist", "football_edit", "anime_funk_edit",
    }
    from src.auto_director.edl import _NICHES

    assert len(_NICHES) == 14
    for niche in sorted(_NICHES):
        cfg = profile_flash_config(load_profile(niche))
        if niche in opted:
            assert cfg is not None, niche
            assert cfg["intensity"] == 0.5, niche
            assert cfg["brightness"] == pytest.approx(0.08), niche
            assert cfg["gamma"] == pytest.approx(1.15), niche
            assert cfg["duration_frames"] == 3, niche
        else:
            assert cfg is None, niche


def test_profile_flash_config_validation():
    assert profile_flash_config(_opted_profile())["intensity"] == 0.5
    # Intensity clamps; zero intensity is an opt-out in effect.
    assert profile_flash_config(_opted_profile(intensity=7))["intensity"] == 1.0
    assert profile_flash_config(_opted_profile(intensity=0)) is None
    assert profile_flash_config(_opted_profile(intensity=-1)) is None
    assert profile_flash_config(_opted_profile(intensity="loud"))["intensity"] == 0.5
    # Disabled / absent / junk sections never opt in.
    assert profile_flash_config(_opted_profile(enabled=False)) is None
    assert profile_flash_config({"effects": {}}) is None
    assert profile_flash_config({"effects": {"audio_flashes": "yes"}}) is None
    assert profile_flash_config({}) is None
    assert profile_flash_config(None) is None
    # Tuning overrides clamp into sane bands.
    cfg = profile_flash_config(_opted_profile(duration_frames=99))
    assert cfg["duration_frames"] == 5
    cfg = profile_flash_config(_opted_profile(min_gap_sec=0.01))
    assert cfg["min_gap_sec"] == 0.2
    cfg = profile_flash_config(_opted_profile(max_per_clip=0))
    assert cfg["max_per_clip"] == 1


# --------------------------------------------------------------- detection


def test_detects_planted_hits_with_labels():
    levels = _levels(10.0)
    _plant_hit(levels, 2.0)
    _plant_hit(levels, 4.5, peak=-3.0)
    _plant_hit(levels, 7.0)
    points = detect_flash_points(
        levels, HOP, duration=10.0, beats=[2.0], drop_time=4.5
    )
    assert [p["at_sec"] for p in points] == [2.025, 4.525, 7.025]
    # On a beat -> beat; on the drop -> drop; otherwise transient.
    assert [p["source"] for p in points] == ["beat", "drop", "transient"]
    assert all(p["rise_db"] >= 30.0 for p in points)


def test_flat_and_quiet_curves_yield_nothing():
    assert detect_flash_points(_levels(10.0, base=-30.0), HOP, duration=10.0) == []
    # A sharp blip that stays quiet in absolute terms is a click, not
    # a hit: rise is huge, the peak is still only -45 dBFS.
    levels = _levels(10.0, base=-70.0)
    _plant_hit(levels, 5.0, peak=-45.0)
    assert detect_flash_points(levels, HOP, duration=10.0) == []
    # Too little evidence to judge a baseline at all.
    assert detect_flash_points([-20.0] * 5, HOP, duration=0.25) == []
    assert detect_flash_points([], HOP, duration=10.0) == []


def test_drop_without_a_candidate_is_added_on_its_own_evidence():
    # A 4 dB burst: under the 5 dB detection bar, so no candidate —
    # but it is the grid's drop and its local burst is real (>= 2 dB).
    levels = _levels(10.0, base=-20.0)
    centre = int(5.0 / HOP)
    levels[centre] = -16.0
    levels[centre + 1] = -18.0
    points = detect_flash_points(levels, HOP, duration=10.0, drop_time=5.0)
    assert len(points) == 1
    assert points[0]["source"] == "drop"
    assert points[0]["rise_db"] == pytest.approx(4.0)
    # A nominal drop with no burst at all earns nothing.
    assert detect_flash_points(
        _levels(10.0, base=-20.0), HOP, duration=10.0, drop_time=5.0
    ) == []


def test_hit_at_the_clip_edge_is_dropped_not_clipped():
    levels = _levels(10.0)
    centre = len(levels) - 1
    levels[centre - 2] = -24.0
    levels[centre - 1] = -14.0
    levels[centre] = -5.0
    # The peak centre (9.975s) sits inside the last half-pulse of the
    # 10s clip, so the flash is refused outright.
    assert detect_flash_points(levels, HOP, duration=10.0) == []


def test_dense_fixture_obeys_spacing_and_rate_caps():
    levels = _levels(20.0)
    for k in range(60):
        _plant_hit(levels, 1.0 + 0.3 * k)
    cfg = profile_flash_config(_opted_profile())
    points = detect_flash_points(levels, HOP, duration=20.0, config=cfg)
    times = [p["at_sec"] for p in points]
    assert 0 < len(times) <= cfg["max_per_clip"]
    for earlier, later in zip(times, times[1:]):
        assert later - earlier >= cfg["min_gap_sec"] - 1e-9
    # No 3-second window holds more than the rate cap.
    for start in times:
        inside = [t for t in times if start <= t < start + 3.0]
        assert len(inside) <= 3


def test_strongest_hits_win_the_per_clip_cap():
    levels = _levels(12.0)
    _plant_hit(levels, 2.0, peak=-20.0)   # rise 20
    _plant_hit(levels, 5.0, peak=-4.0)    # rise 36 — strongest
    _plant_hit(levels, 8.0, peak=-12.0)   # rise 28
    cfg = profile_flash_config(_opted_profile(max_per_clip=2))
    points = detect_flash_points(levels, HOP, duration=12.0, config=cfg)
    assert [p["at_sec"] for p in points] == [5.025, 8.025]


# --------------------------------------------------------------- planning


def _grid(levels, **extra):
    grid = {"energy_levels_db": levels, "energy_hop_sec": HOP, "source": "music"}
    grid.update(extra)
    return grid


def test_plan_audio_flashes_gates():
    levels = _plant_hit(_levels(10.0), 5.0)
    plan = plan_audio_flashes(_opted_profile(), _grid(levels), 10.0)
    assert plan is not None
    assert plan["grid_source"] == "music"
    assert plan["points"][0]["at_sec"] == 5.025
    # No opt-in, no grid, no levels, no hits -> no stamp, in that order.
    assert plan_audio_flashes({}, _grid(levels), 10.0) is None
    assert plan_audio_flashes(_opted_profile(), None, 10.0) is None
    assert plan_audio_flashes(_opted_profile(), _grid([]), 10.0) is None
    assert plan_audio_flashes(_opted_profile(), _grid(_levels(10.0)), 10.0) is None


def test_project_source_levels_slices_the_window():
    full = _levels(30.0)
    _plant_hit(full, 15.0)
    analysis = {
        "energy_curve": {"hop_sec": HOP, "levels_db": full},
        "beat_times": [3.0, 15.0, 29.0],
        "drop_time": 15.0,
        "tempo_bpm": None,  # no tempo: levels still project (the point)
    }
    grid = project_source_levels(analysis, clip_start=10.0, clip_duration=10.0)
    assert grid is not None
    assert grid["source"] == "source"
    assert len(grid["energy_levels_db"]) == 201  # 10s at 50ms, inclusive end
    assert grid["beat_times"] == [5.0]
    assert grid["drop_time"] == 5.0
    points = detect_flash_points(
        grid["energy_levels_db"], HOP, duration=10.0,
        beats=grid["beat_times"], drop_time=grid["drop_time"],
    )
    assert [p["source"] for p in points] == ["drop"]
    # A window starting past the analysis' end, and a missing
    # analysis, give None.
    assert project_source_levels(analysis, clip_start=35.0, clip_duration=10.0) is None
    assert project_source_levels(None, clip_start=0.0, clip_duration=10.0) is None


# -------------------------------------------------------------------- EDL


_WORDS = [
    {"word": f"w{i}", "start": i * 0.5, "end": i * 0.5 + 0.4}
    for i in range(24)
]


def _clip(duration=12.0):
    return {"id": "clip", "start": 0.0, "end": duration, "duration": duration}


def test_build_edl_stamps_flashes_from_beat_grid():
    levels = _plant_hit(_levels(12.0), 6.0)
    edl = build_edl(_clip(), _WORDS, load_profile("gaming"), [], beat_grid=_grid(levels))
    stamp = edl["audio_flashes"]
    assert stamp["grid_source"] == "music"
    assert stamp["brightness"] == pytest.approx(0.08)
    assert len(stamp["points"]) == 1
    point = stamp["points"][0]
    assert point["at_sec"] == 6.025
    assert "rendered_at_sec" in point


def test_build_edl_stamps_flashes_from_flash_grid_for_non_beat_profile():
    levels = _plant_hit(_levels(12.0), 6.0)
    grid = _grid(levels, source="source")
    edl = build_edl(_clip(), _WORDS, load_profile("meme_comedy"), [], flash_grid=grid)
    assert edl["audio_flashes"]["grid_source"] == "source"
    # The beat grid wins when both are passed: it describes the mix.
    edl = build_edl(
        _clip(), _WORDS, load_profile("meme_comedy"), [],
        beat_grid=_grid(levels), flash_grid=_grid(_levels(12.0), source="source"),
    )
    assert edl["audio_flashes"]["grid_source"] == "music"


def test_build_edl_without_opt_in_or_grid_stamps_nothing():
    levels = _plant_hit(_levels(12.0), 6.0)
    # Not opted in (podcast), even with a grid full of hits.
    edl = build_edl(_clip(), _WORDS, load_profile("podcast_news"), [], beat_grid=_grid(levels))
    assert "audio_flashes" not in edl
    # Opted in but no grid at all (every pre-existing caller).
    edl = build_edl(_clip(), _WORDS, load_profile("gaming"), [])
    assert "audio_flashes" not in edl
    # Opted in with a grid but a silent curve.
    edl = build_edl(_clip(), _WORDS, load_profile("gaming"), [], beat_grid=_grid(_levels(12.0)))
    assert "audio_flashes" not in edl


def test_rendered_time_in_segments_walks_ramps_and_fades():
    segments = [
        {"start": 0.0, "end": 4.0, "speed": 2.0, "transition_in": "none", "transition_duration": 0},
        {"start": 4.0, "end": 8.0, "transition_in": "cut", "transition_duration": 0},
    ]
    # Inside the 2x segment: compressed. After it: shifted by the 2s saved.
    assert _rendered_time_in_segments(2.0, segments) == 1.0
    assert _rendered_time_in_segments(6.0, segments) == 4.0
    segments[1]["transition_in"] = "fade"
    segments[1]["transition_duration"] = 0.4
    # The crossfade pulls the second segment's content 0.4s earlier.
    assert _rendered_time_in_segments(6.0, segments) == 3.6
    assert _rendered_time_in_segments(2.0, []) == 2.0


def test_plainify_strips_audio_flashes():
    levels = _plant_hit(_levels(12.0), 6.0)
    edl = build_edl(_clip(), _WORDS, load_profile("gaming"), [], beat_grid=_grid(levels))
    assert "audio_flashes" in edl
    plainify_edl(edl)
    assert "audio_flashes" not in edl


# --------------------------------------------------------------- renderer


def test_build_flash_fragments():
    edl = {
        "audio_flashes": {
            "points": [
                {"at_sec": 2.0, "rendered_at_sec": 1.9, "source": "transient", "rise_db": 30.0},
                {"at_sec": 5.0, "rendered_at_sec": 5.0, "source": "drop", "rise_db": 34.0},
            ],
            "brightness": 0.08, "gamma": 1.15, "duration_frames": 3,
        }
    }
    fragments, current, next_id = edl_render.build_flash_fragments(edl, "[v]", 0)
    assert len(fragments) == 2
    assert "eq=brightness=0.080:gamma=1.150" in fragments[0]
    assert "between(t\\,1.850\\,1.950)" in fragments[0]
    assert "between(t\\,4.950\\,5.050)" in fragments[1]
    assert current != "[v]" and next_id == 2
    # Absent / empty / zero stamps leave the graph untouched.
    for empty in ({}, {"audio_flashes": {}}, {"audio_flashes": {"points": []}},
                  {"audio_flashes": {"points": [{"at_sec": 1.0}], "brightness": 0}}):
        fragments, current, next_id = edl_render.build_flash_fragments(empty, "[v]", 7)
        assert fragments == [] and current == "[v]" and next_id == 7


# ------------------------------------------------------------- real FFmpeg

ffmpeg_missing = shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None
pytestmark_real = pytest.mark.skipif(ffmpeg_missing, reason="ffmpeg/ffprobe not installed")

_RENDER_PROFILE = {
    "niche": "flash_fixture",
    "pacing": {"target_cuts_per_min": [5, 8]},
    "transitions": {"default": "cut", "duration_sec": 0},
    "captions": {"preset_id": "default", "words_per_line": 3, "position": "bottom"},
    "audio": {"music_mood": "none", "music_volume_0_to_1": 0.0, "ducking_db": 0.0},
    "color": {"lut": "none"},
    "hook": {},
    "effects": {"audio_flashes": {"enabled": True, "intensity": 0.5}},
}

_HITS = (2.0, 4.5, 7.0)
_FIXTURE_DURATION = 12.0


def _run(command):
    return subprocess.run(command, capture_output=True, text=True, timeout=600)


def _make_hit_wav(path: Path, hits=_HITS, duration=_FIXTURE_DURATION) -> None:
    """Quiet noise bed + short loud drum bursts at the planted times."""
    rate = 48000
    rng = np.random.default_rng(20261111)
    samples = rng.normal(0.0, 0.008, int(rate * duration))
    for hit in hits:
        start = int(hit * rate)
        length = int(0.25 * rate)
        t = np.arange(length) / rate
        burst = 0.85 * np.sin(2 * np.pi * 150 * t) * np.exp(-t / 0.04)
        samples[start:start + length] += burst
    stereo = np.repeat(np.clip(samples, -1.0, 1.0)[:, None], 2, axis=1)
    pcm = (stereo * 32767).astype("<i2")
    with wave.open(str(path), "wb") as wav:
        wav.setnchannels(2)
        wav.setsampwidth(2)
        wav.setframerate(rate)
        wav.writeframes(pcm.tobytes())


def _make_hit_source(path: Path, wav_path: Path) -> None:
    """Static mid-grey picture over the planted-hit audio."""
    result = _run([
        "ffmpeg", "-y", "-v", "error",
        "-f", "lavfi", "-i",
        f"color=c=0x808080:size=320x240:rate=30:duration={int(_FIXTURE_DURATION)}",
        "-i", str(wav_path),
        "-map", "0:v", "-map", "1:a", "-t", str(int(_FIXTURE_DURATION)),
        "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", str(path),
    ])
    assert result.returncode == 0, result.stderr


def _fixture_edl(source: Path):
    """The production path: decode -> levels -> grid -> build_edl."""
    samples = _decode_audio(source)
    assert samples is not None and len(samples) > 0
    levels = [round(float(v), 1) for v in energy_levels_db(samples)]
    grid = {
        "energy_levels_db": levels,
        "energy_hop_sec": HOP,
        "beat_times": [],
        "drop_time": None,
        "source": "source",
    }
    clip = {"id": "clip", "start": 0.0, "end": _FIXTURE_DURATION, "duration": _FIXTURE_DURATION}
    return build_edl(clip, [], _RENDER_PROFILE, [], flash_grid=grid)


def _frame_mean_luma(path: Path, at_sec: float, tmp_path: Path) -> float:
    import cv2

    frame_path = tmp_path / f"luma_{at_sec}.png"
    result = _run([
        "ffmpeg", "-y", "-v", "error", "-ss", str(at_sec), "-i", str(path),
        "-frames:v", "1", str(frame_path),
    ])
    assert result.returncode == 0, result.stderr
    image = cv2.imread(str(frame_path), cv2.IMREAD_GRAYSCALE)
    assert image is not None
    return float(image.mean())


@pytestmark_real
def test_real_render_flashes_land_on_the_planted_hits(tmp_path):
    source, output = tmp_path / "source.mp4", tmp_path / "out.mp4"
    wav_path = tmp_path / "hits.wav"
    _make_hit_wav(wav_path)
    _make_hit_source(source, wav_path)

    edl = _fixture_edl(source)
    stamp = edl["audio_flashes"]
    points = stamp["points"]
    # Every planted hit — and only the planted hits — earned a flash.
    assert len(points) == len(_HITS)
    for point, hit in zip(points, _HITS):
        assert abs(point["rendered_at_sec"] - hit) < 0.15

    # The graph carries one timed eq pulse per point.
    _, graph, _, _ = edl_render.build_edl_filter_graph(edl, source)
    assert graph.count("eq=brightness=0.080:gamma=1.150") == len(points)

    ok = edl_render.render_edl_clip_ffmpeg(source, output, edl, assets_dir=tmp_path)
    assert ok is True

    baseline = _frame_mean_luma(output, 0.5, tmp_path)
    deltas = []
    for point in points:
        flashed = _frame_mean_luma(output, point["rendered_at_sec"], tmp_path)
        deltas.append(flashed - baseline)
        # A punch, measurably: brightness 0.08 alone is +20 levels.
        assert flashed - baseline >= 10.0
    # Between hits the picture is back at baseline: no stray flashes.
    for control in (1.25, 3.25, 5.75, 10.5):
        luma = _frame_mean_luma(output, control, tmp_path)
        assert abs(luma - baseline) <= 4.0
    print(f"\nflash luma deltas: {[round(d, 1) for d in deltas]} over baseline {baseline:.1f}")


@pytestmark_real
def test_real_render_plain_stays_flat(tmp_path):
    source, output = tmp_path / "source.mp4", tmp_path / "out.mp4"
    wav_path = tmp_path / "hits.wav"
    _make_hit_wav(wav_path)
    _make_hit_source(source, wav_path)

    edl = _fixture_edl(source)
    points = edl["audio_flashes"]["points"]
    plainify_edl(edl)
    assert "audio_flashes" not in edl

    _, graph, _, _ = edl_render.build_edl_filter_graph(edl, source)
    assert "eq=brightness" not in graph

    ok = edl_render.render_edl_clip_ffmpeg(source, output, edl, assets_dir=tmp_path)
    assert ok is True
    baseline = _frame_mean_luma(output, 0.5, tmp_path)
    for point in points:
        luma = _frame_mean_luma(output, point["rendered_at_sec"], tmp_path)
        assert abs(luma - baseline) <= 4.0
