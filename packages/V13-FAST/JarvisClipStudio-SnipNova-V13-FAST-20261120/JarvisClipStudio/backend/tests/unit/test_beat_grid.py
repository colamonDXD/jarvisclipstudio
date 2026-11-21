"""Unit tests for the beat-grid core (auto_director.beat_grid).

The DSP tests run on synthetic click trains synthesised in numpy — a
140 BPM train must come back at 140 +/- 2 BPM with beats located
within ~30 ms, per the lane's contract.
"""

import json
import sys
import wave
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from src.auto_director import beat_grid
from src.auto_director.beat_grid import (
    analyse_audio_beats,
    analyse_beats_from_samples,
    assign_speed_ramps,
    consume_beat_grid,
    prepare_clip_grid,
    profile_beat_config,
    sanitize_speed_ramps,
    snap_segments_to_beats,
    tighten_keep_ranges,
)

RATE = 16000


def click_train(bpm: float, duration: float, *, loud_after: float = None,
                quiet_amp: float = 1.0, loud_amp: float = 1.0,
                start_at: float = 0.0, seed: int = 7) -> np.ndarray:
    """A click train: short decaying bursts exactly on the beat grid."""
    rng = np.random.default_rng(seed)
    samples = rng.normal(0, 0.002, int(duration * RATE))
    period = 60.0 / bpm
    burst_len = int(0.03 * RATE)
    t_axis = np.arange(burst_len) / RATE
    burst = np.exp(-t_axis * 90.0) * np.sin(2 * np.pi * 1800 * t_axis)
    k = 0
    while True:
        t = start_at + k * period
        if t + 0.05 >= duration:
            break
        amp = loud_amp if (loud_after is not None and t >= loud_after) else quiet_amp
        start = int(t * RATE)
        samples[start:start + burst_len] += amp * burst
        k += 1
    return samples.astype(np.float64)


def assert_beats_match(detected, bpm, duration, start_at=0.0, tol=0.03):
    period = 60.0 / bpm
    truth = []
    k = 0
    while start_at + k * period + 0.05 < duration:
        truth.append(start_at + k * period)
        k += 1
    assert detected, "no beats detected"
    for t in truth:
        nearest = min(detected, key=lambda b: abs(b - t))
        assert abs(nearest - t) <= tol, f"true beat {t:.3f} missed (nearest {nearest:.3f})"
    assert abs(len(detected) - len(truth)) <= 1


@pytest.mark.parametrize("bpm", [140.0, 120.0])
def test_click_train_tempo_and_beats(bpm):
    # Clicks start at 0.5 s: the first ~100 ms of any STFT onset
    # analysis is warm-up (a click at exactly t=0 has no preceding
    # frame to diff against), so the honest grid starts after it.
    samples = click_train(bpm, 20.0, start_at=0.5)
    result = analyse_beats_from_samples(samples)
    assert result["tempo_bpm"] is not None
    assert abs(result["tempo_bpm"] - bpm) <= 2.0, result["tempo_bpm"]
    assert_beats_match(result["beat_times"], bpm, 20.0, start_at=0.5)


def test_click_train_drop_detection():
    # Quiet build for 10 s, then the loud payoff — the drop is the
    # first loud beat.
    samples = click_train(140.0, 20.0, loud_after=10.0, quiet_amp=0.12, loud_amp=1.0)
    result = analyse_beats_from_samples(samples)
    assert result["drop_time"] is not None
    assert abs(result["drop_time"] - 10.0) <= 0.65, result["drop_time"]


def test_no_drop_on_flat_train():
    samples = click_train(140.0, 20.0)
    result = analyse_beats_from_samples(samples)
    assert result["drop_time"] is None


def test_analyse_audio_beats_uses_cache(tmp_path):
    samples = click_train(140.0, 8.0)
    path = tmp_path / "bed.wav"
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(RATE)
        pcm = np.clip(samples, -1, 1)
        handle.writeframes((pcm * 32767).astype(np.int16).tobytes())
    first = analyse_audio_beats(path)
    assert first is not None and first["tempo_bpm"] is not None
    assert abs(first["tempo_bpm"] - 140.0) <= 2.0
    cache_file = beat_grid.beat_cache_path(path)
    assert cache_file.is_file()
    payload = json.loads(cache_file.read_text())
    assert payload["version"] == beat_grid.BEAT_CACHE_SCHEMA_VERSION
    second = analyse_audio_beats(path)
    assert second["beat_times"] == first["beat_times"]


def test_prepare_clip_grid_track_loops():
    grid = {
        "tempo_bpm": 120.0,
        "beat_times": [0.5, 1.0, 1.5],
        "drop_time": 1.0,
        "downbeat_offset": 0.5,
        "duration": 2.0,
        "energy_curve": {"hop_sec": 0.05, "levels_db": [-10.0] * 40},
    }
    clip = prepare_clip_grid(grid, mode="track", clip_duration=5.0)
    assert clip is not None
    assert clip["source"] == "music"
    # Beats repeat every 2 s of track.
    assert 2.5 in clip["beat_times"] and 4.5 in clip["beat_times"]
    assert clip["drop_time"] == 1.0


def test_prepare_clip_grid_source_window():
    grid = {
        "tempo_bpm": 120.0,
        "beat_times": [10.5, 11.0, 11.5, 20.0],
        "drop_time": 11.0,
        "downbeat_offset": 10.5,
        "duration": 30.0,
        "energy_curve": {"hop_sec": 0.05, "levels_db": [-10.0] * 600},
    }
    clip = prepare_clip_grid(
        grid, mode="source", clip_start=10.0, clip_duration=3.0
    )
    assert clip is not None
    assert clip["beat_times"] == [0.5, 1.0, 1.5]
    assert clip["drop_time"] == 1.0
    assert clip["source"] == "source"


def _segments(boundaries):
    return [
        {
            "index": i,
            "start": boundaries[i],
            "end": boundaries[i + 1],
            "duration": boundaries[i + 1] - boundaries[i],
            "transition_in": "none" if i == 0 else "cut",
            "transition_duration": 0,
        }
        for i in range(len(boundaries) - 1)
    ]


def test_snap_moves_only_nearby_boundaries():
    segments = _segments([0.0, 3.03, 6.4, 9.0])
    beats = [3.0, 6.0, 9.0]
    snapped, count = snap_segments_to_beats(segments, beats, tolerance=0.12)
    assert count == 1
    bounds = [snapped[0]["start"]] + [s["end"] for s in snapped]
    assert bounds == [0.0, 3.0, 6.4, 9.0]


def _football_cfg():
    return {
        "enabled": True, "source": "music", "drop_anchor": True,
        "snap_tolerance_sec": 0.12, "build_speed": 0.7, "rush_speed": 1.35,
        "silence_tighten": True, "rehook_interval_sec": 10,
    }


def test_consume_anchors_drop_and_quantises():
    period = 60.0 / 140.0
    beats = [round(k * period, 3) for k in range(48)]
    drop = beats[20]
    clip_grid = {
        "tempo_bpm": 140.0,
        "beat_times": beats,
        "drop_time": drop,
        "energy_hop_sec": 0.05,
        "energy_levels_db": [-12.0] * 420,
        "source": "music",
    }
    segments = _segments([0.0, 2.0, 4.0, 6.0, 8.0, 10.0, 12.0, 14.0, 16.0, 18.0, 20.57])
    profile = {"pacing": {"target_cuts_per_min": [25, 40]}}
    out, info = consume_beat_grid(segments, clip_grid, _football_cfg(), profile, 20.57)
    bounds = [out[0]["start"]] + [s["end"] for s in out]
    assert info["drop_anchored"] is True
    assert any(abs(b - drop) < 1e-6 for b in bounds), bounds
    # Post-drop boundaries sit on beats.
    for b in bounds:
        if b > drop and b < 20.57:
            nearest = min(beats, key=lambda x: abs(x - b))
            assert abs(nearest - b) <= 0.002, (b, nearest)
    # Post-drop pacing is inside the profile band.
    post = [b for b in bounds if b >= drop]
    lengths = [b2 - b1 for b1, b2 in zip(post, post[1:])]
    cpm = 60.0 / (sum(lengths) / len(lengths))
    assert 25 * 0.9 <= cpm <= 40 * 1.1, cpm


def test_assign_ramps_and_sanitize():
    period = 60.0 / 140.0
    beats = [round(k * period, 3) for k in range(48)]
    drop = beats[20]
    # Energy: loud everywhere except one quiet post-drop stretch.
    levels = [-10.0] * 420
    for i in range(int(12.0 / 0.05), int(14.5 / 0.05)):
        levels[i] = -40.0
    clip_grid = {
        "tempo_bpm": 140.0, "beat_times": beats, "drop_time": drop,
        "energy_hop_sec": 0.05, "energy_levels_db": levels, "source": "music",
    }
    segments = _segments([0.0, 4.0, drop, 12.0, 14.5, 18.0, 20.57])
    out = assign_speed_ramps(segments, clip_grid, _football_cfg(), 20.57)
    build = [s for s in out if s["end"] <= drop + 1e-6 and "speed" in s]
    assert build and all(s["speed"] == 0.7 for s in build)
    assert all(s.get("speed_reason") for s in out if "speed" in s)
    rush = [s for s in out if s.get("speed", 1.0) > 1.0]
    assert rush and abs(rush[0]["start"] - 12.0) < 1e-6

    bad = [{"start": 0.0, "end": 3.0, "speed": 0.6, "speed_reason": "for engagement", "speed_at": 0.0}]
    sanitize_speed_ramps(bad)
    assert "speed" not in bad[0]
    worse = [{"start": 0.0, "end": 3.0, "speed": 0.6}]
    sanitize_speed_ramps(worse)
    assert "speed" not in worse[0]


def test_profile_beat_config_opt_in_only():
    assert profile_beat_config({"niche": "x"}) is None
    assert profile_beat_config({"beat_grid": {"enabled": False}}) is None
    assert profile_beat_config({"beat_grid": {"enabled": True}}) == {"enabled": True}
    assert profile_beat_config(None) is None


def test_tighten_keep_ranges_cuts_long_gaps_only():
    words = [
        {"word": "hello", "start": 0.2, "end": 0.6},
        {"word": "world", "start": 0.7, "end": 1.1},
        {"word": "again", "start": 3.4, "end": 3.9},
        {"word": "now", "start": 4.3, "end": 4.6},
    ]
    ranges = tighten_keep_ranges([(0.0, 5.0)], words)
    # The 2.3 s gap between "world" and "again" is cut to breaths.
    assert ranges == [(0.0, 1.25), (3.25, 5.0)], ranges
    # A 0.4 s gap is under the threshold: untouched.
    snug = [
        {"word": "a", "start": 0.1, "end": 0.5},
        {"word": "b", "start": 0.9, "end": 1.3},
    ]
    assert tighten_keep_ranges([(0.0, 1.5)], snug) == [(0.0, 1.5)]
    # No word evidence: unchanged.
    assert tighten_keep_ranges([(0.0, 9.0)], []) == [(0.0, 9.0)]
