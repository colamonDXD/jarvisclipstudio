"""Editorial restraint in build_edl (auto_director.content_cuts, EDL_CONTENT_CUTS).

Pinned against the clip1.mp4 failure: Gaming sliced 58 s into 21 equal
pieces, punched in every 12 s, pushed in over the opening and snapped cuts
to the added music bed (handoff/quality-clip1/ROOT_CAUSE.md).
"""
from __future__ import annotations

import pytest

from src.auto_director import content_cuts as cc
from src.auto_director.edl import build_edl, load_profile


def _words(*items):
    return [{"text": t, "word": t, "start": s, "end": e} for t, s, e in items]


@pytest.fixture(autouse=True)
def _on(monkeypatch):
    monkeypatch.setenv("EDL_CONTENT_CUTS", "on")


def test_no_reason_means_one_segment():
    profile = load_profile("gaming")
    edl = build_edl({"id": "c", "start": 0, "end": 58, "duration": 58.0}, [], profile, [])
    assert len(edl["segments"]) == 1
    assert edl["segments"][0]["transition_in"] == "none"


def test_cuts_land_only_on_shot_changes_and_sentence_ends():
    profile = load_profile("gaming")
    words = _words(("Hello", 1.0, 1.4), ("there.", 1.5, 2.0), ("Next", 9.0, 9.4), ("bit.", 9.5, 10.0),
                   ("And", 20.0, 20.3), ("more", 20.4, 20.8))
    clip = {"id": "c", "start": 0, "end": 30, "duration": 30.0, "scene_cuts": [14.0, 14.1]}
    edl = build_edl(clip, words, profile, [])
    reasons = [(round(s["start"], 2), s["boundary_reason"]) for s in edl["segments"]]
    # "there." ends at 2.0 and speech resumes at 9.0: the cut sits mid-pause (5.5).
    # "bit." -> mid-pause 15.0 is within the minimum shot of the 14.0 shot change: dropped.
    # 14.1 reports the same shot change and is dropped the same way.
    assert reasons == [(0.0, "clip_start"), (5.5, "sentence_end"), (14.0, "scene_cut")]
    assert all(s["transition_in"] in ("none", "cut") for s in edl["segments"])
    assert edl["edit_restraint"]["segments"] == len(edl["segments"])


def test_cuts_per_minute_is_a_ceiling():
    profile = {"pacing": {"target_cuts_per_min": [2, 4], "min_shot_sec": 1.0}}
    cuts = [5.0 * k for k in range(1, 12)]  # 11 shot changes in 60 s
    segments, _ = cc.plan_content_segments(profile, 60.0, [], cuts)
    assert len(segments) - 1 == 4


def test_zoom_restraint():
    words = _words(("big", 3.0, 3.4), ("news", 3.5, 3.9), ("wow", 6.0, 6.4), ("yes", 20.0, 20.5))
    keyframes = [
        {"type": "punch_in", "trigger": "rehook", "time": 12.0},
        {"type": "punch_in", "trigger": "action", "time": 3.2},
        {"type": "punch_in", "trigger": "action", "time": 6.1},   # < 8 s after 3.2
        {"type": "punch_in", "trigger": "action", "time": 15.0},  # crowd peak, no speech
        {"type": "punch_in", "trigger": "action", "time": 20.2},
        {"type": "punch_in", "trigger": "action", "time": 0.4},   # first second
        {"type": "ken_burns", "start": 0.0, "end": 30.0},
    ]
    kept, dropped = cc.restrain_zooms(keyframes, words, 30.0)
    assert sorted(k.get("time", -1) for k in kept) == [-1, 3.2, 20.2]
    whys = {d["time"]: d["why"] for d in dropped}
    assert whys[12.0].startswith("interval punch")
    assert whys[15.0] == "loudness peak without speech"
    assert "8s" in whys[6.1]
    assert whys[0.4] == "inside first/last second"


def test_gaming_clip_has_no_interval_or_opening_zoom_and_no_bed_grid():
    profile = load_profile("gaming")
    grid = {"tempo_bpm": 120, "beats": [i * 0.5 for i in range(120)], "energy_curve": {}}
    edl = build_edl({"id": "c", "start": 0, "end": 58, "duration": 58.0}, [], profile, [], beat_grid=grid)
    assert not [z for z in edl["zoom_keyframes"] if z.get("type") == "punch_in"]
    assert not [z for z in edl["zoom_keyframes"] if z.get("trigger") == "opening_punch"]
    assert "beat_grid" not in edl
    assert edl["edit_restraint"]["beat_grid"].startswith("skipped")


def test_signature_only_on_a_shot_change():
    segments = [{"boundary_reason": "clip_start"}, {"boundary_reason": "sentence_end"},
                {"boundary_reason": "scene_cut"}]
    assert not cc.signature_on_shot_change(segments, 1)
    assert cc.signature_on_shot_change(segments, 2)


def test_off_switch_restores_quota_slicing(monkeypatch):
    monkeypatch.setenv("EDL_CONTENT_CUTS", "off")
    profile = load_profile("gaming")
    edl = build_edl({"id": "c", "start": 0, "end": 58, "duration": 58.0}, [], profile, [])
    assert len(edl["segments"]) == 21
    assert "edit_restraint" not in edl
