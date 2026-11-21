"""Visual moment selection: drop/peak/cut candidates, snapping,
overlap discipline, and the scene-cut detector on a synthetic video."""

import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from src.auto_director.visual_moments import (
    detect_scene_cut_times,
    select_visual_moments,
)


def _grid():
    return {
        "tempo_bpm": 140.0,
        "beat_times": [round(k * 60.0 / 140.0, 3) for k in range(280)],
        "drop_time": 60.0,
        "energy_curve": {"hop_sec": 0.05, "levels_db": [-20.0] * 2400},
    }


def _starts(moments):
    out = []
    for m in moments:
        mm, ss = m["start_time"].split(":")
        out.append(int(mm) * 60 + int(ss))
    return out


def test_drop_peak_cut_selection():
    moments = select_visual_moments(
        "unused.mp4",
        120.0,
        target_count=3,
        clip_seconds=10.0,
        beat_grid=_grid(),
        scene_cuts=[30.2, 88.0],
        energy_peaks=[(20.0, -8.0), (60.5, -6.0), (90.0, -10.0)],
    )
    assert len(moments) == 3
    starts = _starts(moments)
    assert starts == sorted(starts)
    # The drop window: moment at 60 lands ~40% into a 10s window.
    assert any(abs(s - 56) <= 1 for s in starts)
    # Every moment explains itself with a timestamp (restraint law).
    assert all("no usable transcript" in m["reasoning"] for m in moments)
    assert all(m["hook_type"] == "visual_energy" for m in moments)
    # Windows do not meaningfully overlap.
    for a, b in zip(starts, starts[1:]):
        assert b - a >= 7


def test_window_snaps_to_scene_cut():
    moments = select_visual_moments(
        "unused.mp4",
        120.0,
        target_count=1,
        clip_seconds=10.0,
        beat_grid=None,
        scene_cuts=[16.8],
        energy_peaks=[(20.0, -8.0)],
    )
    assert len(moments) == 1
    # Raw start would be 20 - 4 = 16; the cut at 16.8 is within 1.25s.
    assert moments[0]["start_time"] == "00:17"


def _curve(duration_s, base, loud_span=None, loud_level=-7.0, burst=None):
    """Synthetic energy curve at the production 0.05s hop.

    ``loud_span`` (start, end) oscillates around ``loud_level`` (a
    sustained loud scene); ``burst`` (time, level) plants one short
    burst on the quiet baseline.
    """
    hop = 0.05
    n = int(duration_s / hop)
    levels = [base] * n
    if loud_span is not None:
        a, b = int(loud_span[0] / hop), int(loud_span[1] / hop)
        for i in range(a, b):
            levels[i] = loud_level + (2.0 if i % 40 < 20 else -2.0)
    if burst is not None:
        t, level = burst
        i = int(t / hop)
        for j in range(i, i + 10):
            levels[j] = level
    return {"hop_sec": hop, "levels_db": levels}


def test_prominence_outranks_sustained_loudness():
    # Two peaks at the SAME level (-8 dB): one inside a sustained
    # loud scene (its surroundings are nearly as loud, prominence
    # ~2 dB), one in a quiet stretch (prominence ~16 dB). File-wide
    # loudness cannot separate them — the old scoring tied and took
    # the earlier one. Local prominence must pick the quiet-scene
    # burst: a reaction in a quiet stretch is the moment.
    grid = {
        "tempo_bpm": None, "beat_times": [], "drop_time": None,
        "energy_curve": _curve(240.0, -24.0, loud_span=(0.0, 120.0),
                               loud_level=-10.0),
    }
    moments = select_visual_moments(
        "unused.mp4", 240.0, target_count=1, clip_seconds=30.0,
        beat_grid=grid, scene_cuts=[],
        energy_peaks=[(60.0, -8.0), (180.0, -8.0)],
    )
    assert len(moments) == 1
    start = _starts(moments)[0]
    # The window covers the quiet-scene burst, and (decaying burst)
    # ends just after it instead of centring it.
    assert start <= 180 <= start + 30
    assert start + 30 - 180 <= 4
    assert "above its surroundings" in moments[0]["reasoning"]


def test_sustained_peak_keeps_default_anchor():
    # Post-peak energy stays high (decay ~1 dB): the peak is a
    # beginning, not a climax — the window keeps the 40% anchor.
    grid = {
        "tempo_bpm": None, "beat_times": [], "drop_time": None,
        "energy_curve": _curve(30.0, -3.0),
    }
    moments = select_visual_moments(
        "unused.mp4", 30.0, target_count=1, clip_seconds=10.0,
        beat_grid=grid, scene_cuts=[], energy_peaks=[(15.0, -2.0)],
    )
    assert moments[0]["start_time"] == "00:11"


def test_decaying_peak_gets_late_anchor():
    # The burst decays into quiet (decay ~18 dB): the clip ends just
    # after the payoff.
    grid = {
        "tempo_bpm": None, "beat_times": [], "drop_time": None,
        "energy_curve": _curve(30.0, -20.0),
    }
    moments = select_visual_moments(
        "unused.mp4", 30.0, target_count=1, clip_seconds=10.0,
        beat_grid=grid, scene_cuts=[], energy_peaks=[(15.0, -2.0)],
    )
    assert moments[0]["start_time"] == "00:06"


def test_cut_candidates_rank_scene_transitions_first_without_energy():
    # No grid, no peaks: cuts are ranked by scene-transition strength
    # (a cut after a long shot), not by file order — the old code
    # marched from the start of the file.
    moments = select_visual_moments(
        "unused.mp4", 300.0, target_count=2, clip_seconds=30.0,
        beat_grid=None, scene_cuts=[10.0, 100.0, 200.0], energy_peaks=[],
    )
    assert _starts(moments) == [100, 200]
    assert "opening a new scene" in moments[0]["reasoning"]


def test_cut_candidate_borrows_following_peak_score():
    grid = {
        "tempo_bpm": None, "beat_times": [], "drop_time": None,
        "energy_curve": _curve(120.0, -20.0),
    }
    moments = select_visual_moments(
        "unused.mp4", 120.0, target_count=2, clip_seconds=30.0,
        beat_grid=grid, scene_cuts=[10.0, 48.0],
        energy_peaks=[(50.0, -4.0)],
    )
    starts = _starts(moments)
    assert starts == sorted(starts) and len(moments) == 2
    cut_moment = next(m for m in moments if m["start_time"] == "00:48")
    assert "scene cut" in cut_moment["reasoning"]


def test_snap_never_pushes_moment_outside_window():
    # Tiny clip: the nearest cut to the raw start sits just AFTER the
    # peak; snapping to it would leave the peak outside its window.
    moments = select_visual_moments(
        "unused.mp4", 120.0, target_count=1, clip_seconds=2.5,
        beat_grid=None, scene_cuts=[20.1], energy_peaks=[(20.0, -8.0)],
    )
    assert moments[0]["start_time"] == "00:19"


def test_reason_context_is_stamped():
    moments = select_visual_moments(
        "unused.mp4", 120.0, target_count=1, clip_seconds=10.0,
        beat_grid=None, scene_cuts=[30.0], energy_peaks=[],
        reason_context="AI analysis unavailable",
    )
    assert "Visual selection (AI analysis unavailable)" in moments[0]["reasoning"]


def test_no_signals_no_moments():
    assert select_visual_moments(
        "unused.mp4", 120.0, target_count=3, clip_seconds=10.0,
        beat_grid=None, scene_cuts=[], energy_peaks=[],
    ) == []
    assert select_visual_moments(
        "unused.mp4", 0.0, target_count=3, clip_seconds=10.0,
        beat_grid=_grid(), scene_cuts=[10.0], energy_peaks=[(5.0, -8.0)],
    ) == []


def test_scene_cut_detector_on_synthetic_video(tmp_path):
    video = tmp_path / "two_scenes.mp4"
    subprocess.run(
        [
            "ffmpeg", "-v", "error", "-y",
            "-f", "lavfi", "-i", "color=c=red:size=320x240:duration=2:rate=10",
            "-f", "lavfi", "-i", "color=c=blue:size=320x240:duration=2:rate=10",
            "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=44100",
            "-filter_complex", "[0:v][1:v]concat=n=2:v=1:a=0[v]",
            "-map", "[v]", "-map", "2:a",
            "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-shortest",
            str(video),
        ],
        check=True, capture_output=True, timeout=120,
    )
    cuts = detect_scene_cut_times(video)
    assert any(abs(c - 2.0) <= 0.35 for c in cuts), cuts
