"""Viral Intelligence Engine v1: ClipPlan schema, sanitising, fail-safe.

The planner's LLM output is untrusted: these tests pin the contract that
unknown enums/SFX names degrade instead of failing, that planned SFX obey
the profile gate and the placement/density discipline, that the EDL merge
never stacks a planned accent on a native one, and that ANY planner
failure degrades to the deterministic profile-default plan.
"""

import asyncio
import copy
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from src.auto_director.clip_plan import (
    ClipPlan,
    apply_plan_opening_punch,
    apply_plan_transitions,
    merge_plan_sfx_events,
    minimal_plan,
    plan_hook_report,
    sanitize_sfx_events,
    validate_plan_dict,
    PlannedSfxEvent,
)
from src.auto_director.edl import load_profile
from src.auto_director import plan_service
from src.auto_director.plan_service import ClipPlanDraft, generate_clip_plan, viral_plan_enabled


SFX_PROFILE = load_profile("meme_comedy")  # sfx_from_user_library: true
NO_SFX_PROFILE = load_profile("podcast_news")  # sfx_from_user_library: false


def _global_caps_profile():
    """SFX_PROFILE without its scene-rule caps (Workstream D): the global
    restraint budgets apply. meme_comedy's tightened caps are pinned in
    test_scene_rules.py; the budget tests below pin the global defaults."""
    profile = copy.deepcopy(SFX_PROFILE)
    profile.pop("scene_rules", None)
    return profile


def _llm_dict(**overrides):
    raw = {
        "hook": {
            "type": "punchline",
            "timestamp": 4.5,
            "confidence": 0.8,
            "reason": "The deadpan answer lands out of nowhere.",
        },
        "opening_treatment": {
            "punch_zoom": "subtle",
            "first_caption_emphasis": True,
            "sfx_accent": "pop",
            "at_offset": 4.5,
            "reason": "The deadpan punchline lands in the first seconds.",
        },
        "phases": [
            {"start_offset": 0.0, "end_offset": 4.5, "intent": "context"},
            {"start_offset": 4.5, "end_offset": 9.0, "intent": "payoff"},
        ],
        "sfx_events": [{"at_offset": 4.5, "name": "impact", "reason": "punchline hit"}],
        "music_decision": {"use_profile_music": True, "reason": "Profile default fits."},
        "plan_confidence": 0.7,
    }
    raw.update(overrides)
    return raw


class TestValidatePlanDict:
    def test_valid_output_validates(self):
        plan = validate_plan_dict(
            _llm_dict(), niche="meme_comedy", duration=30.0, profile=SFX_PROFILE
        )
        assert plan.source == "llm"
        assert plan.niche == "meme_comedy"
        assert plan.hook.type == "punchline"
        assert plan.hook.timestamp == 4.5
        assert plan.opening_treatment.sfx_accent == "pop"
        assert [(e.name, e.at_offset) for e in plan.sfx_events] == [("impact", 4.5)]
        assert plan.plan_confidence == 0.7

    def test_non_dict_raises(self):
        with pytest.raises(ValueError):
            validate_plan_dict(["not", "a", "dict"], niche="meme_comedy", duration=10.0, profile=SFX_PROFILE)

    def test_unknown_enums_degrade_not_fail(self):
        raw = _llm_dict(
            hook={"type": "mind_blown", "timestamp": 1.0, "confidence": 2.5, "reason": "x"},
            opening_treatment={"punch_zoom": "extreme", "first_caption_emphasis": False, "sfx_accent": "airhorn"},
        )
        plan = validate_plan_dict(raw, niche="meme_comedy", duration=30.0, profile=SFX_PROFILE)
        assert plan.hook.type == "none_strong"
        assert plan.hook.confidence == 1.0  # clamped
        assert plan.opening_treatment.punch_zoom == "none"
        assert plan.opening_treatment.sfx_accent is None

    def test_unknown_sfx_names_dropped_known_kept(self):
        raw = _llm_dict(
            sfx_events=[
                {"at_offset": 5.0, "name": "airhorn", "reason": "not canonical"},
                {"at_offset": 8.0, "name": "ding", "reason": "canonical"},
            ]
        )
        plan = validate_plan_dict(raw, niche="meme_comedy", duration=30.0, profile=SFX_PROFILE)
        assert [(e.name, e.at_offset) for e in plan.sfx_events] == [("ding", 8.0)]

    def test_hook_timestamp_clamped_to_duration(self):
        raw = _llm_dict(hook={"type": "reveal", "timestamp": 999.0, "confidence": 0.5, "reason": "late"})
        plan = validate_plan_dict(raw, niche="meme_comedy", duration=20.0, profile=SFX_PROFILE)
        assert plan.hook.timestamp == 20.0

    def test_niche_comes_from_caller_not_output(self):
        raw = _llm_dict()
        raw["niche"] = "gaming"
        plan = validate_plan_dict(raw, niche="meme_comedy", duration=30.0, profile=SFX_PROFILE)
        assert plan.niche == "meme_comedy"

    def test_bad_phases_dropped(self):
        raw = _llm_dict(
            phases=[
                {"start_offset": 0.0, "end_offset": 5.0, "intent": "vibes"},
                {"start_offset": 5.0, "end_offset": 5.05, "intent": "hook"},
                {"start_offset": 5.0, "end_offset": 9.0, "intent": "payoff"},
            ]
        )
        plan = validate_plan_dict(raw, niche="meme_comedy", duration=30.0, profile=SFX_PROFILE)
        assert [(p.start_offset, p.end_offset, p.intent) for p in plan.phases] == [(5.0, 9.0, "payoff")]


class TestReasonedDecisions:
    """Acceptance upgrades #2/#3: transitions + caption style are carried
    and validated; every effect decision needs a real content reason."""

    def test_valid_transition_choice_kept_with_reason(self):
        raw = _llm_dict(
            transition_choices=[
                {
                    "name": "smoothleft",
                    "at_offset": 6.6,
                    "reason": "The topic shifts from disbelief to the reveal at this join.",
                }
            ]
        )
        plan = validate_plan_dict(raw, niche="meme_comedy", duration=30.0, profile=SFX_PROFILE)
        assert [(t.name, t.at_offset) for t in plan.transition_choices] == [("smoothleft", 6.6)]

    def test_unknown_transition_name_dropped(self):
        raw = _llm_dict(
            transition_choices=[
                {"name": "spinny_zoom", "at_offset": 6.6, "reason": "The scene changes here."}
            ]
        )
        plan = validate_plan_dict(raw, niche="meme_comedy", duration=30.0, profile=SFX_PROFILE)
        assert plan.transition_choices == []

    def test_reasonless_transition_dropped(self):
        raw = _llm_dict(
            transition_choices=[{"name": "smoothleft", "at_offset": 6.6, "reason": ""}]
        )
        plan = validate_plan_dict(raw, niche="meme_comedy", duration=30.0, profile=SFX_PROFILE)
        assert plan.transition_choices == []

    def test_boilerplate_reason_transition_dropped(self):
        raw = _llm_dict(
            transition_choices=[
                {"name": "smoothleft", "at_offset": 6.6, "reason": "for engagement"}
            ]
        )
        plan = validate_plan_dict(raw, niche="meme_comedy", duration=30.0, profile=SFX_PROFILE)
        assert plan.transition_choices == []

    def test_default_transition_needs_no_reason(self):
        # Naming the profile's own default changes nothing, so it is not
        # an effect decision and survives without a reason.
        raw = _llm_dict(transition_choices=[{"name": "cut", "at_offset": 6.6, "reason": ""}])
        plan = validate_plan_dict(raw, niche="meme_comedy", duration=30.0, profile=SFX_PROFILE)
        assert [t.name for t in plan.transition_choices] == ["cut"]

    def test_transition_budget_keeps_earliest_two(self):
        raw = _llm_dict(
            transition_choices=[
                {"name": "fade", "at_offset": 10.0, "reason": "The final segment turns reflective."},
                {"name": "smoothleft", "at_offset": 2.0, "reason": "The guest interrupts with the story."},
                {"name": "dissolve", "at_offset": 6.0, "reason": "The topic changes to the results."},
            ]
        )
        plan = validate_plan_dict(raw, niche="meme_comedy", duration=30.0, profile=_global_caps_profile())
        assert [(t.name, t.at_offset) for t in plan.transition_choices] == [
            ("smoothleft", 2.0),
            ("dissolve", 6.0),
        ]

    def test_caption_style_valid_and_reasoned_kept(self):
        raw = _llm_dict(
            caption_style={
                "name": "mrbeast",
                "reason": "Short punchy lines suit the joke-dense delivery.",
            }
        )
        plan = validate_plan_dict(raw, niche="meme_comedy", duration=30.0, profile=SFX_PROFILE)
        assert plan.caption_style is not None
        assert plan.caption_style.name == "mrbeast"

    def test_caption_style_unknown_name_dropped(self):
        raw = _llm_dict(
            caption_style={"name": "comic_sans", "reason": "The clip is playful throughout."}
        )
        plan = validate_plan_dict(raw, niche="meme_comedy", duration=30.0, profile=SFX_PROFILE)
        assert plan.caption_style is None

    def test_caption_style_reasonless_deviation_dropped(self):
        raw = _llm_dict(caption_style={"name": "mrbeast", "reason": ""})
        plan = validate_plan_dict(raw, niche="meme_comedy", duration=30.0, profile=SFX_PROFILE)
        assert plan.caption_style is None

    def test_caption_style_matching_profile_default_needs_no_reason(self):
        profile = load_profile("education_tutorial")  # captions preset: hormozi
        raw = _llm_dict(caption_style={"name": "hormozi", "reason": ""})
        plan = validate_plan_dict(
            raw, niche="education_tutorial", duration=30.0, profile=profile
        )
        assert plan.caption_style is not None
        assert plan.caption_style.name == "hormozi"

    def test_opening_punch_reasonless_dropped(self):
        raw = _llm_dict(
            opening_treatment={
                "punch_zoom": "strong",
                "first_caption_emphasis": False,
                "sfx_accent": None,
            }
        )
        plan = validate_plan_dict(raw, niche="meme_comedy", duration=30.0, profile=SFX_PROFILE)
        assert plan.opening_treatment.punch_zoom == "none"

    def test_opening_punch_boilerplate_reason_dropped(self):
        raw = _llm_dict(
            opening_treatment={
                "punch_zoom": "strong",
                "first_caption_emphasis": False,
                "sfx_accent": None,
                "reason": "to make it viral",
            }
        )
        plan = validate_plan_dict(raw, niche="meme_comedy", duration=30.0, profile=SFX_PROFILE)
        assert plan.opening_treatment.punch_zoom == "none"

    def test_opening_accent_reasonless_dropped(self):
        raw = _llm_dict(
            opening_treatment={
                "punch_zoom": "none",
                "first_caption_emphasis": False,
                "sfx_accent": "pop",
            }
        )
        plan = validate_plan_dict(raw, niche="meme_comedy", duration=30.0, profile=SFX_PROFILE)
        assert plan.opening_treatment.sfx_accent is None

    def test_sfx_event_reasonless_dropped(self):
        raw = _llm_dict(sfx_events=[{"at_offset": 5.0, "name": "pop", "reason": ""}])
        plan = validate_plan_dict(raw, niche="meme_comedy", duration=30.0, profile=SFX_PROFILE)
        assert plan.sfx_events == []

    def test_sfx_event_boilerplate_reason_dropped(self):
        raw = _llm_dict(
            sfx_events=[{"at_offset": 5.0, "name": "pop", "reason": "for engagement"}]
        )
        plan = validate_plan_dict(raw, niche="meme_comedy", duration=30.0, profile=SFX_PROFILE)
        assert plan.sfx_events == []

    def test_music_off_reasonless_dropped(self):
        raw = _llm_dict(music_decision={"use_profile_music": False, "reason": ""})
        plan = validate_plan_dict(raw, niche="meme_comedy", duration=30.0, profile=SFX_PROFILE)
        assert plan.music_decision.use_profile_music is True

    def test_music_off_with_reason_kept(self):
        raw = _llm_dict(
            music_decision={
                "use_profile_music": False,
                "reason": "The confession is whispered; a music bed would bury it.",
            }
        )
        plan = validate_plan_dict(raw, niche="meme_comedy", duration=30.0, profile=SFX_PROFILE)
        assert plan.music_decision.use_profile_music is False

    def test_fully_reasoned_plan_passes_untouched(self):
        raw = _llm_dict(
            transition_choices=[
                {
                    "name": "smoothleft",
                    "at_offset": 6.6,
                    "reason": "The topic shifts from disbelief to the reveal at this join.",
                }
            ],
            caption_style={
                "name": "mrbeast",
                "reason": "Short punchy lines suit the joke-dense delivery.",
            },
            music_decision={
                "use_profile_music": False,
                "reason": "The whispered confession would be buried by a music bed.",
            },
        )
        plan = validate_plan_dict(raw, niche="meme_comedy", duration=30.0, profile=SFX_PROFILE)
        assert plan.opening_treatment.punch_zoom == "subtle"
        assert plan.opening_treatment.sfx_accent == "pop"
        assert plan.opening_treatment.at_offset == 4.5
        assert [(t.name, t.at_offset) for t in plan.transition_choices] == [("smoothleft", 6.6)]
        assert plan.caption_style is not None and plan.caption_style.name == "mrbeast"
        assert [(e.name, e.at_offset) for e in plan.sfx_events] == [("impact", 4.5)]
        assert plan.music_decision.use_profile_music is False
        # And the whole plan survives a store/load roundtrip unchanged.
        restored = ClipPlan.from_dict(json.loads(json.dumps(plan.to_dict())))
        assert restored.to_dict() == plan.to_dict()


class TestApplyConsumers:
    def _edl(self):
        return {
            "duration": 20.0,
            "segments": [
                {"index": 0, "start": 0.0, "end": 5.0, "transition_in": "none", "transition_duration": 0},
                {"index": 1, "start": 5.0, "end": 10.0, "transition_in": "cut", "transition_duration": 0.0},
                {"index": 2, "start": 10.0, "end": 20.0, "transition_in": "cut", "transition_duration": 0.0},
            ],
            "zoom_keyframes": [],
        }

    def _plan(self, **overrides):
        raw = _llm_dict(**overrides)
        return validate_plan_dict(raw, niche="meme_comedy", duration=20.0, profile=SFX_PROFILE)

    def test_transition_applied_to_nearest_join(self):
        plan = self._plan(
            transition_choices=[
                {"name": "smoothleft", "at_offset": 5.2, "reason": "The guest starts the story here."}
            ]
        )
        edl = self._edl()
        applied = apply_plan_transitions(edl, plan, SFX_PROFILE)
        assert applied == 1
        assert edl["segments"][1]["transition_in"] == "smoothleft"
        # meme_comedy's joins are cut-length (0.0s): a real crossfade
        # gets the visible default duration instead.
        assert edl["segments"][1]["transition_duration"] == 0.4
        assert edl["segments"][2]["transition_in"] == "cut"

    def test_transition_no_joins_no_application(self):
        plan = self._plan(
            transition_choices=[
                {"name": "smoothleft", "at_offset": 5.2, "reason": "The guest starts the story here."}
            ]
        )
        edl = {"duration": 5.0, "segments": [{"index": 0, "start": 0.0, "end": 5.0}], "zoom_keyframes": []}
        assert apply_plan_transitions(edl, plan, SFX_PROFILE) == 0

    def test_opening_punch_inserts_keyframe(self):
        plan = self._plan()  # subtle punch at 4.5 from the fixture
        edl = self._edl()
        assert apply_plan_opening_punch(edl, plan, SFX_PROFILE) is True
        assert edl["zoom_keyframes"] == [
            {"type": "punch_in", "trigger": "hook", "time": 4.5, "scale": 1.25, "duration": 0.5}
        ]

    def test_opening_punch_rescales_existing_keyframe(self):
        plan = self._plan()
        edl = self._edl()
        edl["zoom_keyframes"] = [
            {"type": "punch_in", "trigger": "emphasis", "time": 4.6, "scale": 1.35, "duration": 0.5}
        ]
        assert apply_plan_opening_punch(edl, plan, SFX_PROFILE) is True
        assert len(edl["zoom_keyframes"]) == 1
        assert edl["zoom_keyframes"][0]["scale"] == 1.25

    def test_opening_punch_none_is_noop(self):
        plan = self._plan(
            opening_treatment={
                "punch_zoom": "none",
                "first_caption_emphasis": False,
                "sfx_accent": None,
            }
        )
        edl = self._edl()
        assert apply_plan_opening_punch(edl, plan, SFX_PROFILE) is False
        assert edl["zoom_keyframes"] == []


class TestSfxGuards:
    def test_profile_gate_blocks_all_sfx(self):
        plan = validate_plan_dict(
            _llm_dict(), niche="podcast_news", duration=30.0, profile=NO_SFX_PROFILE
        )
        assert plan.sfx_events == []
        assert plan.opening_treatment.sfx_accent is None

    def test_edge_guard(self):
        events = [
            PlannedSfxEvent(at_offset=0.2, name="pop", reason="too early"),
            PlannedSfxEvent(at_offset=29.9, name="pop", reason="too late"),
            PlannedSfxEvent(at_offset=15.0, name="pop", reason="fine"),
        ]
        kept = sanitize_sfx_events(events, duration=30.0, profile=SFX_PROFILE)
        assert [e.at_offset for e in kept] == [15.0]

    def test_min_gap_cluster_keeps_stronger_reason(self):
        events = [
            PlannedSfxEvent(at_offset=10.0, name="pop", reason="hit"),
            PlannedSfxEvent(at_offset=10.4, name="impact", reason="the reveal lands here"),
            PlannedSfxEvent(at_offset=12.0, name="ding", reason="card"),
        ]
        kept = sanitize_sfx_events(events, duration=30.0, profile=SFX_PROFILE)
        assert [(e.at_offset, e.name) for e in kept] == [(10.4, "impact"), (12.0, "ding")]

    def test_density_cap_keeps_strongest_four(self):
        events = [
            PlannedSfxEvent(at_offset=float(2 * i), name="pop", reason="r" * (i + 1))
            for i in range(1, 8)
        ]
        kept = sanitize_sfx_events(events, duration=40.0, profile=_global_caps_profile())
        assert len(kept) == 4
        # The four longest reasons survive, in time order.
        assert [len(e.reason) for e in kept] == [5, 6, 7, 8]


class TestMinimalPlan:
    def test_minimal_plan_is_todays_behaviour(self):
        plan = minimal_plan("podcast_news", NO_SFX_PROFILE, 30.0)
        assert plan.source == "fallback"
        assert plan.hook.type == "none_strong"
        assert plan.sfx_events == []
        assert plan.music_decision.use_profile_music is True
        # Phases tile the whole clip.
        assert plan.phases[0].start_offset == 0.0
        assert plan.phases[-1].end_offset == 30.0

    def test_roundtrip_through_dict(self):
        plan = minimal_plan("gaming", load_profile("gaming"), 12.0)
        restored = ClipPlan.from_dict(json.loads(json.dumps(plan.to_dict())))
        assert restored.to_dict() == plan.to_dict()


class TestMerge:
    def test_edl_native_events_win_within_window(self):
        edl_events = [{"name": "impact", "at_sec": 4.6, "gain_db": 0.0}]
        plan_events = [
            PlannedSfxEvent(at_offset=4.5, name="pop", reason="too close to native"),
            PlannedSfxEvent(at_offset=9.0, name="ding", reason="clear"),
        ]
        merged = merge_plan_sfx_events(edl_events, plan_events)
        assert [(e["name"], e["at_sec"]) for e in merged] == [("impact", 4.6), ("ding", 9.0)]
        assert merged[1]["gain_db"] == -6.0  # emitter's gain for ding

    def test_merge_does_not_mutate_inputs(self):
        edl_events = [{"name": "whoosh", "at_sec": 2.0, "gain_db": -6.0}]
        plan_events = [PlannedSfxEvent(at_offset=5.0, name="pop", reason="x")]
        merged = merge_plan_sfx_events(edl_events, plan_events)
        assert len(edl_events) == 1
        assert len(merged) == 2
        assert merged == sorted(merged, key=lambda e: e["at_sec"])


class TestHookReport:
    def test_report_shape(self):
        plan = validate_plan_dict(
            _llm_dict(), niche="meme_comedy", duration=30.0, profile=SFX_PROFILE
        )
        report = plan.hook_report()
        assert report["type"] == "punchline"
        assert report["timestamp"] == 4.5
        assert report["why"] == "The deadpan answer lands out of nowhere."
        assert "subtle punch zoom" in report["opening_strategy"]
        assert report["confidence"] == 0.8

    def test_report_from_stored_json(self):
        plan = minimal_plan("sports", load_profile("sports"), 20.0)
        report = plan_hook_report(json.dumps(plan.to_dict()))
        assert report is not None
        assert report["type"] == "none_strong"

    def test_report_from_garbage_is_none(self):
        assert plan_hook_report(None) is None
        assert plan_hook_report("") is None
        assert plan_hook_report("{not json") is None
        assert plan_hook_report('["a", "list"]') is None


def _mock_agent(output=None, error=None):
    if error is not None:
        return SimpleNamespace(run=AsyncMock(side_effect=error))
    return SimpleNamespace(run=AsyncMock(return_value=SimpleNamespace(output=output)))


def _draft(**overrides) -> ClipPlanDraft:
    values = {
        "hook_type": "strong_statement",
        "hook_timestamp": 0.0,
        "hook_confidence": 0.9,
        "hook_reason": "He opens with the conclusion most people bury.",
        "punch_zoom": "subtle",
        "first_caption_emphasis": True,
        "sfx_accent": None,
        "opening_at_offset": 0.0,
        "opening_reason": "The opening line is the claim the whole clip then proves.",
        "phases": [
            {"start_offset": 0.0, "end_offset": 6.0, "intent": "hook"},
            {"start_offset": 6.0, "end_offset": 20.0, "intent": "context"},
        ],
        "sfx_events": [],
        "use_profile_music": True,
        "music_reason": "Calm bed fits a tutorial voice.",
        "plan_confidence": 0.85,
    }
    values.update(overrides)
    return ClipPlanDraft(**values)


class TestGenerateClipPlan:
    @pytest.mark.asyncio
    async def test_success_returns_validated_llm_plan(self, monkeypatch):
        monkeypatch.setattr(plan_service, "get_plan_agent", lambda: _mock_agent(output=_draft()))
        profile = load_profile("education_tutorial")
        plan = await generate_clip_plan(
            niche="education_tutorial",
            profile=profile,
            duration=20.0,
            text="Here is the one step everyone skips.",
            words=[{"word": "Here", "start": 0.0, "end": 0.3}],
        )
        assert plan.source == "llm"
        assert plan.hook.type == "strong_statement"
        assert plan.plan_confidence == 0.85

    @pytest.mark.asyncio
    async def test_llm_exception_degrades_to_minimal_plan(self, monkeypatch):
        monkeypatch.setattr(
            plan_service, "get_plan_agent", lambda: _mock_agent(error=RuntimeError("LLM down"))
        )
        plan = await generate_clip_plan(
            niche="gaming", profile=load_profile("gaming"), duration=15.0, text="clip", words=[]
        )
        assert plan.source == "fallback"
        assert plan.hook.type == "none_strong"
        assert plan.sfx_events == []

    @pytest.mark.asyncio
    async def test_timeout_degrades_to_minimal_plan(self, monkeypatch):
        async def slow_run(_prompt):
            await asyncio.sleep(30)
            return SimpleNamespace(output=_draft())

        monkeypatch.setattr(plan_service, "get_plan_agent", lambda: SimpleNamespace(run=slow_run))
        monkeypatch.setattr(plan_service, "_plan_timeout_sec", lambda: 0.2)
        plan = await generate_clip_plan(
            niche="gaming", profile=load_profile("gaming"), duration=15.0, text="clip", words=[]
        )
        assert plan.source == "fallback"

    @pytest.mark.asyncio
    async def test_agent_construction_failure_degrades(self, monkeypatch):
        def boom():
            raise RuntimeError("no LLM configured")

        monkeypatch.setattr(plan_service, "get_plan_agent", boom)
        plan = await generate_clip_plan(
            niche="sports", profile=load_profile("sports"), duration=15.0, text="clip", words=[]
        )
        assert plan.source == "fallback"
        assert plan.niche == "sports"


class TestKillSwitch:
    def test_enabled_by_default(self, monkeypatch):
        monkeypatch.delenv("VIRAL_PLAN_ENABLED", raising=False)
        assert viral_plan_enabled() is True

    @pytest.mark.parametrize("value", ["false", "0", "no", "off", "FALSE"])
    def test_disabled_values(self, monkeypatch, value):
        monkeypatch.setenv("VIRAL_PLAN_ENABLED", value)
        assert viral_plan_enabled() is False

    def test_other_values_stay_enabled(self, monkeypatch):
        monkeypatch.setenv("VIRAL_PLAN_ENABLED", "true")
        assert viral_plan_enabled() is True
