"""Velocity-curve wiring: a profile-selectable curve in beat_grid.

``beat_grid.velocity_curve`` names one of the six CapCut curves. When
set, the curve replaces the drop-anchored build/rush ramps as the
clip's speed treatment (stamped onto the snapped/quantised segments,
drop anchor still holds); when unset or unknown, the EDL is exactly
today's — this file pins that byte-identical default. The curve needs
a resolved beat grid (no grid, no consume call, no curve) but it does
not need a drop.
"""

import copy
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from src.auto_director.beat_grid import (
    consume_beat_grid,
    velocity_curve_name,
)
from src.auto_director.edl import build_edl, load_profile
from src.auto_director.velocity_curves import (
    CURVE_NAMES,
    predicted_duration,
    realised_profile,
)

DURATION = 12.0


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


def _grid(*, drop=6.0):
    beats = [round(k * 0.5, 3) for k in range(25)]
    return {
        "tempo_bpm": 120.0,
        "beat_times": beats,
        "drop_time": drop,
        "energy_hop_sec": 0.05,
        "energy_levels_db": [-12.0] * 240,
        "source": "music",
    }


def _football_cfg(**overrides):
    cfg = {
        "enabled": True, "source": "music", "drop_anchor": True,
        "snap_tolerance_sec": 0.12, "build_speed": 0.7, "rush_speed": 1.35,
        "silence_tighten": True, "rehook_interval_sec": 10,
    }
    cfg.update(overrides)
    return cfg


def _profile():
    return {"pacing": {"target_cuts_per_min": [25, 40]}}


def test_velocity_curve_name_validation():
    assert velocity_curve_name(None) is None
    assert velocity_curve_name({}) is None
    assert velocity_curve_name({"velocity_curve": ""}) is None
    assert velocity_curve_name({"velocity_curve": "warp_drive"}) is None
    assert velocity_curve_name({"velocity_curve": 42}) is None
    for name in CURVE_NAMES:
        assert velocity_curve_name({"velocity_curve": name}) == name
    assert velocity_curve_name({"velocity_curve": " bullet "}) == "bullet"


def test_curve_replaces_build_rush_but_keeps_drop_anchor():
    segments = _segments([0.0, 2.0, 4.0, 6.0, 8.0, 10.0, 12.0])
    out, info = consume_beat_grid(
        segments, _grid(), _football_cfg(velocity_curve="bullet"), _profile(), DURATION
    )
    assert info["velocity_curve"] == "bullet"
    assert info["drop_anchored"] is True
    assert info["drop_time"] == 6.0
    stamped = [s for s in out if "speed" in s]
    assert stamped
    assert all(s["speed_reason"] == "velocity curve Bullet" for s in stamped)
    assert not any("drop" in s.get("speed_reason", "") for s in out)
    # The curve shape is on the segments: fast edges, slow centre.
    profile = realised_profile(out)
    assert profile[0] >= 1.4 and profile[-1] >= 1.4
    assert min(profile[4], profile[5]) <= 0.65
    # Beat-snapped/quantised boundaries survive the stamping: every
    # internal boundary still sits on a beat.
    bounds = [out[0]["start"]] + [s["end"] for s in out]
    beats = _grid()["beat_times"]
    for b in bounds[1:-1]:
        assert min(abs(b - beat) for beat in beats) <= 0.002, b


def test_unset_or_unknown_curve_is_byte_identical_to_today():
    segments = _segments([0.0, 2.0, 4.0, 6.0, 8.0, 10.0, 12.0])
    base_out, base_info = consume_beat_grid(
        copy.deepcopy(segments), _grid(), _football_cfg(), _profile(), DURATION
    )
    for cfg in (
        _football_cfg(velocity_curve=None),
        _football_cfg(velocity_curve=""),
        _football_cfg(velocity_curve="warp_drive"),
    ):
        out, info = consume_beat_grid(
            copy.deepcopy(segments), _grid(), cfg, _profile(), DURATION
        )
        assert out == base_out
        assert info == base_info
    # And the default still carries the build/rush ramps.
    assert any(
        s.get("speed_reason", "").startswith("Slowed build") for s in base_out
    )


def test_curve_applies_without_drop_anchor():
    cfg = _football_cfg(drop_anchor=False, velocity_curve="hero")
    segments = _segments([0.0, 3.0, 6.0, 9.0, 12.0])
    out, info = consume_beat_grid(
        segments, _grid(), cfg, _profile(), DURATION
    )
    assert info["velocity_curve"] == "hero"
    assert "drop_anchored" not in info
    stamped = [s for s in out if "speed" in s]
    assert stamped
    assert all(s["speed_reason"] == "velocity curve Hero" for s in stamped)
    # Hero: near-normal edges, slow centre. (With only four coarse
    # segments the edge midpoints sit 12.5% into the curve, where Hero
    # has already dipped to 0.833 — the 20-step D14 profile reads 0.934
    # at the same position; the shape claim is the edge/centre gap.)
    profile = realised_profile(out)
    assert profile[0] >= 0.8 and profile[-1] >= 0.8
    assert min(profile[4], profile[5]) <= 0.65
    assert profile[0] - min(profile[4], profile[5]) >= 0.15


def test_build_edl_wired_path_records_curve_and_shape():
    profile = load_profile("football_edit")
    profile = copy.deepcopy(profile)
    profile["beat_grid"]["velocity_curve"] = "bullet"
    words = [
        {"word": f"w{i}", "start": i * 0.5, "end": i * 0.5 + 0.4}
        for i in range(24)
    ]
    clip = {"id": "clip", "start": 0, "end": 12, "duration": 12}
    edl = build_edl(clip, words, profile, [], beat_grid=_grid())
    assert edl["beat_grid"]["velocity_curve"] == "bullet"
    assert edl["beat_grid"]["drop_anchored"] is True
    stamped = [s for s in edl["segments"] if "speed" in s]
    assert stamped
    assert all(s["speed_reason"] == "velocity curve Bullet" for s in stamped)
    # The stamped speeds change the clip's rendered length: Bullet's
    # slow centre outweighs its fast edges over this span.
    assert predicted_duration(edl["segments"]) > 12.0


def test_build_edl_without_grid_never_curves():
    profile = load_profile("football_edit")
    profile = copy.deepcopy(profile)
    profile["beat_grid"]["velocity_curve"] = "bullet"
    words = [{"word": "test", "start": 0, "end": 0.5}]
    clip = {"id": "clip", "start": 0, "end": 12, "duration": 12}
    edl = build_edl(clip, words, profile, [], beat_grid=None)
    assert "beat_grid" not in edl
    assert not any("speed" in s for s in edl["segments"])


def test_only_football_edit_selects_a_curve():
    """Integration decision (Nov 12 RC): exactly one shipped profile
    opts in — football_edit selects the Bullet curve, per the velocity
    lane's recommendation and render proof. Every other profile still
    selects no curve, so its behaviour is byte-identical to before."""
    from src.auto_director.edl import load_profile as _load

    for niche in (
        "podcast_news", "travel_vlog", "meme_comedy", "gaming",
        "sports", "education_tutorial", "finance_business", "vlog_lifestyle",
        "true_crime", "hindi_podcast", "music_artist",
        "japan_travel_cinematic", "anime_funk_edit",
    ):
        profile = _load(niche)
        cfg = profile.get("beat_grid") or {}
        assert velocity_curve_name(cfg) is None, niche
    football = _load("football_edit")
    assert velocity_curve_name(football.get("beat_grid") or {}) == "bullet"
