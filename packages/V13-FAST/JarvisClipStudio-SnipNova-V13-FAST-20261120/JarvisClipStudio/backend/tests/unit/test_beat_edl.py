"""Beat-grid consumption in build_edl: drop anchoring, snapping,
speed ramps, re-hook punches — and the byte-identity pins proving
profiles that do not opt in are untouched by the grid."""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from src.auto_director.edl import build_edl, load_profile

PERIOD = 60.0 / 140.0


def _clip_grid(duration=30.0, drop_beat=28, quiet=None):
    beats = [round(k * PERIOD, 3) for k in range(int(duration / PERIOD) + 1)]
    levels = [-12.0] * int(duration / 0.05 + 1)
    if quiet:
        for i in range(int(quiet[0] / 0.05), int(quiet[1] / 0.05)):
            levels[i] = -40.0
    return {
        "tempo_bpm": 140.0,
        "beat_times": beats,
        "drop_time": beats[drop_beat] if drop_beat is not None else None,
        "energy_hop_sec": 0.05,
        "energy_levels_db": levels,
        "source": "music",
    }


def _clip(duration=30.0):
    return {"id": "c1", "start": 0.0, "end": duration, "duration": duration}


def _bounds(edl):
    return [edl["segments"][0]["start"]] + [s["end"] for s in edl["segments"]]


def test_football_drop_anchors_and_ramps():
    profile = load_profile("football_edit")
    # This test pins the beat grid's own build/rush ramps. The shipped
    # profile opts into the Bullet velocity curve (integration decision,
    # Nov 12 RC), and a selected curve REPLACES those ramps by design
    # (see test_beat_velocity_curve.py, which covers the curve path on
    # this same profile) — so pin the ramp behaviour with the curve
    # explicitly off here.
    profile["beat_grid"] = {
        k: v for k, v in profile["beat_grid"].items() if k != "velocity_curve"
    }
    grid = _clip_grid(quiet=(16.0, 19.0))
    edl = build_edl(_clip(), [], profile, [], beat_grid=grid)
    assert edl["beat_grid"]["drop_anchored"] is True
    drop = grid["drop_time"]
    bounds = _bounds(edl)
    assert any(abs(b - drop) < 1e-6 for b in bounds)
    # Post-drop boundaries sit on beats.
    for b in bounds:
        if drop < b < 30.0:
            nearest = min(grid["beat_times"], key=lambda x: abs(x - b))
            assert abs(nearest - b) <= 0.002
    # Slowed build ramps exist, carry reasons, and stay in the build.
    ramped = [s for s in edl["segments"] if "speed" in s]
    assert ramped, "expected speed ramps"
    build = [s for s in ramped if s["end"] <= drop + 1e-6]
    assert build and all(s["speed"] == 0.7 for s in build)
    assert all(s.get("speed_reason") and "drop" in s["speed_reason"] for s in build)


def test_gaming_snaps_without_drop_anchor():
    profile = load_profile("gaming")
    grid = _clip_grid()
    grid["drop_time"] = None
    edl = build_edl(_clip(20.0), [], profile, [], beat_grid=grid)
    assert "beat_grid" in edl
    assert "drop_anchored" not in edl["beat_grid"]
    assert not any("speed" in s for s in edl["segments"])


def test_non_opted_profile_ignores_grid_byte_identical():
    profile = load_profile("podcast_news")
    words = [
        {"word": "The", "start": 0.5, "end": 0.6},
        {"word": "truth", "start": 0.6, "end": 0.8},
        {"word": "is", "start": 0.8, "end": 0.9},
    ]
    plain = build_edl(_clip(), words, profile, [2.5, 10.0])
    with_grid = build_edl(_clip(), words, profile, [2.5, 10.0], beat_grid=_clip_grid())
    assert json.dumps(with_grid, sort_keys=True) == json.dumps(plain, sort_keys=True)


def test_opted_profile_without_grid_has_no_beat_key_but_rehooks():
    profile = load_profile("football_edit")
    edl = build_edl(_clip(45.0), [], profile, [])
    assert "beat_grid" not in edl
    assert not any("speed" in s for s in edl["segments"])
    rehooks = [
        z for z in edl["zoom_keyframes"]
        if z.get("type") == "punch_in" and z.get("trigger") == "rehook"
    ]
    assert len(rehooks) >= 3
    times = sorted(z["time"] for z in rehooks)
    assert all(b - a >= 0.75 for a, b in zip(times, times[1:]))


def test_rehook_punches_absent_for_calm_profiles():
    profile = load_profile("travel_vlog")
    edl = build_edl(_clip(45.0), [], profile, [])
    assert not any(
        z.get("trigger") == "rehook" for z in edl["zoom_keyframes"]
    )
