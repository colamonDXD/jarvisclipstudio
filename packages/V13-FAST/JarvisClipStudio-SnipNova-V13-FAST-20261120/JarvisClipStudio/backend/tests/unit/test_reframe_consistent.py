"""One layout per clip (REFRAME_CONSISTENT, 2026-11-18).

Luffy's test clip (a handheld awards-night vlog) jumped between a zoomed
face crop and a small fit with blurred bands every second or two. A clip
that mixes layout families is now re-planned with the family covering the
most screen time; split-dominant (two-person) and delegated clips keep the
planner's plan.
"""

import pytest

from src.media import reframe_plan


@pytest.fixture(autouse=True)
def _stubs(monkeypatch):
    monkeypatch.delenv("REFRAME_CONSISTENT", raising=False)
    monkeypatch.setattr(reframe_plan, "build_fit_reframe_plan",
                        lambda analysis, ranges, duration: {"delegate": None, "shots": [], "framing": "fit"})
    monkeypatch.setattr(reframe_plan, "build_plain_reframe_plan",
                        lambda analysis, ranges, duration, framing=None: {"delegate": None, "shots": [], "framing": framing})


def _plan(*shots):
    return {"delegate": None, "shots": [{"start": s, "end": e, "mode": m} for s, e, m in shots]}


def _unify(plan):
    return reframe_plan.unify_layout_plan(plan, {}, [(0.0, 60.0)], 60.0)


def test_crop_dominant_mix_becomes_one_face_follow_crop():
    out = _unify(_plan((0, 10, "fit"), (10, 40, "single"), (40, 50, "fit"), (50, 60, "static")))
    assert out["framing"] == "crop"
    assert out["unified_from"] == {"fit": 20.0, "split": 0.0, "crop": 40.0}


def test_fit_dominant_mix_becomes_one_whole_frame_fit():
    out = _unify(_plan((0, 35, "fit"), (35, 45, "single"), (45, 60, "fit")))
    assert out["framing"] == "fit"


def test_split_dominant_two_person_clip_keeps_the_planner_plan():
    plan = _plan((0, 40, "split"), (40, 60, "single"))
    assert _unify(plan) is plan


def test_single_family_and_delegated_plans_are_untouched():
    one = _plan((0, 30, "single"), (30, 60, "static"))
    assert _unify(one) is one
    delegated = {"delegate": "plain", "shots": []}
    assert _unify(delegated) is delegated
    assert _unify(None) is None


def test_off_switch(monkeypatch):
    monkeypatch.setenv("REFRAME_CONSISTENT", "0")
    plan = _plan((0, 10, "fit"), (10, 60, "single"))
    assert _unify(plan) is plan


def test_split_shots_are_never_unified_away(monkeypatch):
    """Sanji's 2026-11-18 report: crop outvoted split and deleted two-person shots."""
    from src.media import reframe_plan as rp

    monkeypatch.delenv("REFRAME_CONSISTENT", raising=False)
    plan = {"delegate": None, "shots": [
        {"start": 0.0, "end": 21.0, "mode": "fit"},
        {"start": 21.0, "end": 25.5, "mode": "split"},
        {"start": 25.5, "end": 50.0, "mode": "single"},
    ]}
    called = []
    monkeypatch.setattr(rp, "build_plain_reframe_plan", lambda *a, **k: called.append("crop") or {"shots": []})
    monkeypatch.setattr(rp, "build_fit_reframe_plan", lambda *a, **k: called.append("fit") or {"shots": []})
    assert rp.unify_layout_plan(plan, {}, [(0.0, 50.0)], 50.0) is plan
    assert called == []
