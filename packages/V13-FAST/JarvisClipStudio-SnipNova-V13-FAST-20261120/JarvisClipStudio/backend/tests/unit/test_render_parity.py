"""Render parity: a neutral re-render must match the first render.

The pipeline's first render consumes the clip's stored viral plan into
its EDL (plan transitions, opening punch, plan SFX — see
``VideoService._consume_viral_plan``). Before this lane, the re-render
seam never re-consumed that plan, so re-rendering an untouched clip
silently dropped the plan's decisions. These tests pin the parity:
the EDL the re-render seam hands the renderer must equal, on every
render-affecting key the plan touches, the EDL the pipeline path
produces from the same builder output + the same plan.
"""

import copy
import json

import pytest

from src.auto_director.clip_plan import (
    CaptionStyleChoice,
    ClipPlan,
    OpeningTreatment,
    PlannedSfxEvent,
    TransitionChoice,
)
from src.auto_director.edl import load_profile
from src.clip_source_map import save_clip_source_ranges
from src.services.video_service import VideoService

from test_clip_edit_endpoint import _service, edit_boundary  # noqa: F401

COMPARE_KEYS = ("segments", "sfx_events", "zoom_keyframes", "captions")


def _llm_plan() -> ClipPlan:
    return ClipPlan(
        niche="gaming",
        opening_treatment=OpeningTreatment(
            punch_zoom="strong", at_offset=0.5, reason="hook lands at 0.5s"
        ),
        transition_choices=[
            TransitionChoice(name="wipeleft", at_offset=5.0, reason="topic change")
        ],
        caption_style=CaptionStyleChoice(name="default", reason="clean"),
        sfx_events=[PlannedSfxEvent(at_offset=2.0, name="pop", reason="accent")],
        source="llm",
    )


def _two_range_service(tmp_path, monkeypatch, *, with_plan=True):
    service, _clip_repo, clip_path = _service(tmp_path, monkeypatch)
    # Two kept ranges -> the rerender EDL has two segments and one
    # internal join for the plan's transition choice to land on.
    save_clip_source_ranges(clip_path, [(10.0, 15.0), (16.0, 20.0)])
    if with_plan:
        service.clip_repo.clip["clip_plan"] = json.dumps(_llm_plan().to_dict())
    return service


def _expected_edl(service):
    """The pipeline path: builder EDL + the stored plan consumed."""
    clip = service.clip_repo.clip
    edl = service._build_rerender_edl(clip, 10.0, 20.0, "gaming", None, None, None)
    plan = ClipPlan.from_dict(json.loads(clip["clip_plan"]))
    VideoService._consume_viral_plan(edl, plan, load_profile("gaming"))
    return edl


def _assert_same_render_keys(actual, expected):
    for key in COMPARE_KEYS:
        assert actual.get(key) == expected.get(key), f"EDL key {key!r} diverged"
    # A neutral re-render stamps no studio blocks of its own.
    assert "look" not in actual
    assert "audio_studio" not in actual
    assert "voiceover" not in actual


@pytest.mark.asyncio
async def test_neutral_rerender_consumes_the_stored_plan_like_the_pipeline(
    edit_boundary, monkeypatch, tmp_path
):
    service = _two_range_service(tmp_path, monkeypatch)
    expected = _expected_edl(service)

    await service.rerender_clip("task-1", "clip-1", "gaming")

    render = service.video_service.render_call
    assert render is not None
    _assert_same_render_keys(render["edl"], expected)
    # And the plan visibly landed (the comparison above is not vacuous):
    joins = render["edl"]["segments"][1:]
    assert joins and joins[0]["transition_in"] == "wipeleft"
    assert any(
        event.get("name") == "pop" and event.get("at_sec") == 2.0
        for event in render["edl"]["sfx_events"]
    )
    assert any(
        keyframe.get("type") == "punch_in"
        for keyframe in render["edl"].get("zoom_keyframes") or []
    )


@pytest.mark.asyncio
async def test_rerender_without_a_stored_plan_matches_the_plain_builder(
    edit_boundary, monkeypatch, tmp_path
):
    service = _two_range_service(tmp_path, monkeypatch, with_plan=False)
    clip = service.clip_repo.clip
    expected = service._build_rerender_edl(
        clip, 10.0, 20.0, "gaming", None, None, None
    )

    await service.rerender_clip("task-1", "clip-1", "gaming")

    render = service.video_service.render_call
    assert render is not None
    _assert_same_render_keys(render["edl"], expected)


@pytest.mark.asyncio
async def test_explicit_transition_beats_the_plans_transition(
    edit_boundary, monkeypatch, tmp_path
):
    service = _two_range_service(tmp_path, monkeypatch)
    expected = _expected_edl(service)

    await service.rerender_clip("task-1", "clip-1", "gaming", transition="fade")

    render = service.video_service.render_call
    assert render is not None
    joins = render["edl"]["segments"][1:]
    assert joins and all(s["transition_in"] == "fade" for s in joins)
    # The rest of the plan (punch + SFX) still applies.
    assert render["edl"]["sfx_events"] == expected["sfx_events"]
    assert render["edl"]["zoom_keyframes"] == expected["zoom_keyframes"]


@pytest.mark.asyncio
async def test_opening_override_suppresses_the_plans_punch_only(
    edit_boundary, monkeypatch, tmp_path
):
    from dataclasses import replace

    from src.auto_director.hook_variants import apply_opening_punch

    service = _two_range_service(tmp_path, monkeypatch)
    # Expected: builder EDL + plan consumed with its punch suppressed +
    # the picked variant's own punch applied through its helper — the
    # exact sequence the seam performs.
    clip = service.clip_repo.clip
    expected = service._build_rerender_edl(
        clip, 10.0, 20.0, "gaming", None, None, None
    )
    plan = ClipPlan.from_dict(json.loads(clip["clip_plan"]))
    plan = replace(plan, opening_treatment=OpeningTreatment())
    profile = load_profile("gaming")
    VideoService._consume_viral_plan(expected, plan, profile)
    apply_opening_punch(
        expected, level="subtle", at_offset=0.0, profile=profile
    )

    await service.rerender_clip(
        "task-1",
        "clip-1",
        "gaming",
        opening_override={"punch_zoom": "subtle", "punch_at_offset": 0.0},
    )

    render = service.video_service.render_call
    assert render is not None
    _assert_same_render_keys(render["edl"], expected)


def test_consume_is_deterministic_on_the_same_edl():
    plan = _llm_plan()
    profile = load_profile("gaming")
    base = {
        "duration": 10.0,
        "segments": [
            {"start": 0.0, "end": 5.0, "transition_in": "none", "transition_duration": 0.0},
            {"start": 5.0, "end": 10.0, "transition_in": "fade", "transition_duration": 0.4},
        ],
        "sfx_events": [],
        "zoom_keyframes": [],
        "captions": {},
    }
    first = copy.deepcopy(base)
    second = copy.deepcopy(base)
    VideoService._consume_viral_plan(first, plan, profile)
    VideoService._consume_viral_plan(second, plan, profile)
    assert first == second
