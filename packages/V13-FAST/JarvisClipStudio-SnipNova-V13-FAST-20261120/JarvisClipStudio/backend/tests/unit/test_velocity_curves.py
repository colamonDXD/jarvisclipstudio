"""Velocity curve presets (D14): the name must match the realised curve.

Each CapCut name is pinned against its pre-registered shape definition
on the REALISED profile (the speeds stamped onto EDL segments — what
the renderer will actually do), plus a distinctness check: six names
whose profiles are not measurably different would be six labels for
one curve, i.e. a fake feature.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from src.auto_director.velocity_curves import (
    CURVE_NAMES,
    apply_velocity_curve,
    curve_speed_at,
    predicted_duration,
    realised_profile,
    stamp_velocity_curve,
)

BASE = [{"index": 0, "start": 0.0, "end": 12.0, "duration": 12.0,
         "transition_in": "none", "transition_duration": 0}]


def _profile(name):
    return realised_profile(apply_velocity_curve([dict(BASE[0])], name), samples=10)


def test_flash_in_fast_start_settles_to_normal():
    p = _profile("flash_in")
    assert p[0] >= 1.5
    assert abs(p[-1] - 1.0) <= 0.15
    assert p[0] > p[2] > p[4]  # decaying toward normal


def test_flash_out_normal_start_fast_end():
    p = _profile("flash_out")
    assert abs(p[0] - 1.0) <= 0.15
    assert p[-1] >= 1.5
    assert p[-1] > p[-3] > p[-5]


def test_hero_normal_edges_slow_centre():
    p = _profile("hero")
    assert p[0] >= 0.9 and p[-1] >= 0.9
    assert min(p[4], p[5]) <= 0.65


def test_bullet_fast_edges_ultra_slow_centre():
    p = _profile("bullet")
    assert p[0] >= 1.4 and p[-1] >= 1.4
    assert min(p[4], p[5]) <= 0.6


def test_montage_alternates():
    p = _profile("montage")
    # Direction changes of the sampled profile. Zero diffs (a sampled
    # plateau at a peak/trough) are compressed out first: a plateau at
    # the top of a swing is still a turn, and symmetric sampling of a
    # smooth curve produces them routinely.
    signs = []
    for i in range(9):
        d = p[i + 1] - p[i]
        if abs(d) > 1e-9:
            signs.append(1 if d > 0 else -1)
    turns = sum(1 for a, b in zip(signs, signs[1:]) if a != b)
    assert turns >= 3
    assert max(p) - min(p) >= 0.8


def test_jump_cut_stepped_values():
    p = _profile("jump_cut")
    assert len(set(p)) <= 5
    assert max(abs(p[i + 1] - p[i]) for i in range(9)) >= 0.5


def test_all_six_profiles_measurably_distinct():
    profiles = {name: _profile(name) for name in CURVE_NAMES}
    names = list(profiles)
    for i, a in enumerate(names):
        for b in names[i + 1:]:
            distance = sum(abs(x - y) for x, y in zip(profiles[a], profiles[b]))
            assert distance > 1.0, f"{a} and {b} are not distinct (L1={distance})"


def test_apply_preserves_span_and_stamps_reasons():
    segs = apply_velocity_curve([dict(BASE[0])], "bullet")
    assert segs[0]["start"] == 0.0 and segs[-1]["end"] == 12.0
    assert abs(sum(s["end"] - s["start"] for s in segs) - 12.0) < 0.01
    ramped = [s for s in segs if "speed" in s]
    assert ramped and all(s["speed_reason"] == "velocity curve Bullet" for s in ramped)
    assert all(0.5 <= s["speed"] <= 2.0 for s in ramped)


def test_unknown_curve_and_degenerate_input_unchanged():
    assert apply_velocity_curve([dict(BASE[0])], "warp_drive") == BASE
    assert apply_velocity_curve([], "bullet") == []
    zero = [{"index": 0, "start": 5.0, "end": 5.0, "duration": 0.0}]
    assert apply_velocity_curve(zero, "bullet") == zero


def test_speed_bounds_and_duration_math():
    for name in CURVE_NAMES:
        for pos in (0.0, 0.25, 0.5, 0.75, 1.0):
            assert 0.5 <= curve_speed_at(name, pos) <= 2.0
    bullet = apply_velocity_curve([dict(BASE[0])], "bullet")
    # Fast edges + slow centre over 12 s nets out shorter than real time
    # is wrong — it nets LONGER than the fast-only parts suggest; the
    # exact claim is just: predicted duration follows sum(len/speed).
    manual = sum((s["end"] - s["start"]) / s.get("speed", 1.0) for s in bullet)
    assert abs(predicted_duration(bullet) - round(manual, 3)) < 0.01
    assert predicted_duration(bullet) != 12.0


# --- stamp_velocity_curve: the beat-grid seam keeps the caller's
# segmentation (beat-snapped boundaries survive) and only stamps speeds.


def _tiled(boundaries):
    return [
        {
            "index": i, "start": boundaries[i], "end": boundaries[i + 1],
            "duration": boundaries[i + 1] - boundaries[i],
            "transition_in": "none" if i == 0 else "cut",
            "transition_duration": 0,
        }
        for i in range(len(boundaries) - 1)
    ]


def test_stamp_keeps_boundaries_and_matches_curve_shape():
    segs = _tiled([0.0, 2.0, 4.0, 6.0, 8.0, 10.0, 12.0])
    out = stamp_velocity_curve(segs, "bullet")
    # Boundaries, transitions and count untouched.
    assert [s["start"] for s in out] == [0.0, 2.0, 4.0, 6.0, 8.0, 10.0]
    assert [s["end"] for s in out] == [2.0, 4.0, 6.0, 8.0, 10.0, 12.0]
    assert all(s["transition_in"] == t["transition_in"] for s, t in zip(out, segs))
    # Speed at each segment's midpoint, from the same curve function.
    for seg in out:
        mid = ((seg["start"] + seg["end"]) / 2.0) / 12.0
        expected = curve_speed_at("bullet", mid)
        if expected == 1.0:
            assert "speed" not in seg
        else:
            assert seg["speed"] == round(expected, 3)
            assert seg["speed_reason"] == "velocity curve Bullet"
            assert seg["speed_at"] == seg["start"]
    # Bullet shape survives the coarse tiling: fast edges, slow centre.
    profile = realised_profile(out)
    assert profile[0] >= 1.4 and profile[-1] >= 1.4
    assert min(profile[4], profile[5]) <= 0.65


def test_stamp_replaces_previous_ramps():
    segs = _tiled([0.0, 4.0, 8.0, 12.0])
    segs[0]["speed"] = 0.7
    segs[0]["speed_reason"] = "Slowed build into the drop at 6.0s"
    segs[0]["speed_at"] = 0.0
    segs[2]["speed"] = 1.35
    segs[2]["speed_reason"] = "Low-energy span rushed"
    segs[2]["speed_at"] = 8.0
    out = stamp_velocity_curve(segs, "hero")
    assert all(
        s.get("speed_reason", "").startswith("velocity curve ")
        for s in out if "speed" in s
    )
    assert not any("drop" in s.get("speed_reason", "") for s in out)
    # Input list untouched (pure function).
    assert segs[0]["speed"] == 0.7
    assert segs[2]["speed"] == 1.35


def test_stamp_one_point_zero_steps_stay_unstamped():
    # Flash In settles at 1.0 for the whole second half.
    segs = _tiled([0.0, 3.0, 6.0, 9.0, 12.0])
    out = stamp_velocity_curve(segs, "flash_in")
    assert "speed" in out[0]
    assert out[0]["speed"] > 1.5
    for seg in out[2:]:
        assert "speed" not in seg
        assert "speed_reason" not in seg
        assert "speed_at" not in seg


def test_stamp_unknown_or_degenerate_unchanged():
    segs = _tiled([0.0, 6.0, 12.0])
    assert stamp_velocity_curve(segs, "warp_drive") == segs
    assert stamp_velocity_curve([], "bullet") == []
    zero = [{"index": 0, "start": 5.0, "end": 5.0, "duration": 0.0}]
    assert stamp_velocity_curve(zero, "bullet") == zero
