"""Retention structure analysis + opening strategies (Workstreams B+C).

Fixtures are synthetic transcripts with KNOWN structure: a setup whose
payoff is present, a setup whose payoff is absent (the overpromise
defect), a question opening, and a reaction opening. The analysis must
flag each correctly, selection must demote the overpromise case, and a
model judgement must never upgrade a deterministic honesty verdict.
No live LLM: the judge seam is faked.
"""

import re

import pytest

from src.auto_director import hook_variants as hv
from src.auto_director import retention as rt
from src.auto_director.clip_plan import ClipPlan, HookDecision, Phase
from src.auto_director.retention import (
    analyse_retention,
    apply_retention_to_segments,
)
from src.services.video_service import VideoService


def _words(text, *, start_at=0.0, step=0.4):
    out = []
    t = start_at
    for tok in text.split():
        out.append({"text": tok, "start": round(t, 3), "end": round(t + 0.35, 3)})
        t += step
    return out


def _analyse(text, **kwargs):
    words = _words(text)
    return analyse_retention(
        text=text, words=words, duration=words[-1]["end"], **kwargs
    )


SETUP_PAYOFF = (
    "I am going to show you how I fixed the car engine today. "
    "First I removed the old battery from the bay. "
    "Then I cleaned every contact with a small brush. "
    "The engine started on the first try and the car drove perfectly."
)
SETUP_NO_PAYOFF = (
    "I am going to show you how I fixed the car engine today. "
    "We talked about the weather for a while at lunch. "
    "Then everyone went home and watched television quietly. "
    "Anyway that is all for today folks."
)
QUESTION = (
    "Why did the old bridge collapse last winter? "
    "Engineers inspected the cables for several months carefully. "
    "The bridge collapsed because the main cables snapped under ice. "
    "That is why the town rebuilt it stronger."
)
REACTION = (
    "No way, he actually scored from there! "
    "The crowd jumped up shouting and waving their arms wildly. "
    "The goalkeeper just stood frozen near his line. "
    "What a finish to an incredible match tonight."
)
MISSING_CONTEXT = (
    "But the main reason it failed was obvious to everyone involved. "
    "We had warned them about the numbers many times before. "
    "The report came out the following week quietly."
)


# --- Structure fields ---------------------------------------------------------


def test_setup_with_payoff_is_flagged_delivered_with_evidence():
    record = _analyse(SETUP_PAYOFF).to_dict()
    assert record["payoff"]["status"] == "present"
    assert record["payoff"]["at_seconds"] is not None
    assert record["payoff"]["evidence"][0]["quote"].startswith("The engine started")
    assert record["hook_honesty"]["verdict"] == "delivers"
    assert record["curiosity_created"]["created"] is True
    assert record["build_and_escalation"]["verdict"] == "builds"
    assert record["stop_scroll_reason"]["kind"] == "announced_event"
    assert record["context_sufficiency"]["level"] == "sufficient"
    assert record["selection"] == {"action": "keep", "penalty": 0, "reasons": []}
    # The first-second field is grounded in the opening words (the
    # words whose start falls inside the first second).
    assert record["first_second"]["evidence"][0]["quote"] == "I am going"


def test_setup_without_payoff_is_an_overpromise_defect():
    record = _analyse(SETUP_NO_PAYOFF).to_dict()
    assert record["payoff"]["status"] == "absent"
    assert record["hook_honesty"]["verdict"] == "overpromises"
    # The claim and the footage evidence are both on the record.
    assert "fixed the car engine" in record["hook_honesty"]["claim"]
    assert record["hook_honesty"]["claim_evidence"][0]["quote"].startswith(
        "I am going to show you"
    )
    assert "engine" in record["hook_honesty"]["why"]
    assert record["selection"]["action"] == "demote"
    assert record["selection"]["penalty"] == rt.PENALTY_OVERPROMISE


def test_question_opening_creates_curiosity_and_is_answered():
    record = _analyse(QUESTION).to_dict()
    assert record["stop_scroll_reason"]["kind"] == "question"
    assert record["curiosity_created"]["created"] is True
    assert record["payoff"]["status"] == "present"
    assert record["payoff"]["evidence"][0]["quote"].startswith("The bridge collapsed because")
    assert record["hook_honesty"]["verdict"] == "delivers"
    assert record["ending_type"]["type"] == "resolved"


def test_reaction_opening_needs_no_payoff_promise():
    record = _analyse(REACTION).to_dict()
    assert record["stop_scroll_reason"]["kind"] == "reaction"
    assert record["hook_honesty"]["verdict"] == "delivers"
    assert record["payoff"]["status"] == "present"
    assert record["ending_type"]["type"] == "resolved"


def test_missing_context_opening_is_flagged():
    record = _analyse(MISSING_CONTEXT).to_dict()
    assert record["context_sufficiency"]["level"] == "missing"
    assert record["selection"]["penalty"] >= rt.PENALTY_MISSING_CONTEXT


def test_hook_title_the_footage_never_shows_is_an_overpromise():
    record = _analyse(
        "We reviewed the match in the studio for an hour. "
        "The panel discussed the weather and the travel plans calmly.",
        hook_title="He reveals the secret formula",
    ).to_dict()
    assert record["hook_honesty"]["verdict"] == "overpromises"
    assert record["hook_honesty"]["claim"] == "He reveals the secret formula"


def test_loopable_ending_is_detected():
    record = _analyse(
        "The drums started and the crowd started moving together. "
        "The singer raised both hands high above the stage. "
        "The drums started and the crowd started moving together."
    ).to_dict()
    assert record["ending_type"]["type"] == "loopable"


def test_every_field_is_grounded_and_sources_are_deterministic():
    record = _analyse(QUESTION).to_dict()
    for field_name in (
        "first_second", "stop_scroll_reason", "curiosity_created",
        "context_sufficiency", "build_and_escalation", "payoff",
        "ending_type",
    ):
        assert record[field_name]["evidence"], field_name
    assert set(record["field_sources"].values()) == {"deterministic"}
    assert record["source"] == "deterministic"


def test_no_performance_prediction_language_anywhere_in_the_record():
    banned = re.compile(r"viral|views|will go|guaranteed|prediction", re.I)
    for text in (SETUP_PAYOFF, SETUP_NO_PAYOFF, QUESTION, REACTION):
        record = _analyse(text).to_dict()
        assert not banned.search(str(record)), text


# --- Model-judge seam (faked; no live LLM) -------------------------------------


def test_model_judge_can_rephrase_but_never_upgrade_honesty():
    def fake_judge(prompt, deterministic):
        assert "fixed the car engine" in prompt
        return {
            "payoff_why": "A model phrasing of the missing payoff.",
            "curiosity_description": "A model phrasing of the open loop.",
            # Attempted verdict tampering — must be ignored (not a
            # narrative field).
            "hook_honesty": {"verdict": "delivers"},
            "payoff": {"status": "present"},
        }

    words = _words(SETUP_NO_PAYOFF)
    record = analyse_retention(
        text=SETUP_NO_PAYOFF, words=words, duration=words[-1]["end"],
        llm_judge=fake_judge,
    ).to_dict()
    assert record["hook_honesty"]["verdict"] == "overpromises"
    assert record["payoff"]["status"] == "absent"
    assert record["payoff"]["why"] == "A model phrasing of the missing payoff."
    assert record["field_sources"]["payoff"] == "model_judgement"
    assert record["field_sources"]["curiosity_created"] == "model_judgement"
    assert record["field_sources"]["hook_honesty"] == "deterministic"
    assert record["source"] == "llm_assisted"
    # Selection is recomputed from the deterministic verdicts, unchanged.
    assert record["selection"]["penalty"] == rt.PENALTY_OVERPROMISE


def test_model_judge_failure_keeps_the_deterministic_record():
    def broken_judge(prompt, deterministic):
        raise RuntimeError("provider down")

    words = _words(QUESTION)
    record = analyse_retention(
        text=QUESTION, words=words, duration=words[-1]["end"],
        llm_judge=broken_judge,
    ).to_dict()
    assert record["source"] == "deterministic"
    assert record["payoff"]["status"] == "present"


# --- Bounded selection influence -------------------------------------------------


def _segment(text, score=80, hook=20):
    return {
        "start_time": "00:00", "end_time": "00:20", "text": text,
        "virality_score": score, "hook_score": hook,
    }


def test_selection_demotes_the_overpromise_case_below_a_delivered_one():
    good = _segment(SETUP_PAYOFF, score=70)
    bad = _segment(SETUP_NO_PAYOFF, score=80)
    out = apply_retention_to_segments(
        [bad, good], [_analyse(SETUP_NO_PAYOFF), _analyse(SETUP_PAYOFF)],
        keep_at_least=1,
    )
    assert [s["text"] for s in out] == [SETUP_PAYOFF, SETUP_NO_PAYOFF]
    demoted = out[1]
    assert demoted["hook_score"] == 0
    assert demoted["virality_score"] == 60
    assert demoted["retention"]["hook_honesty"]["verdict"] == "overpromises"
    # The delivered clip is untouched.
    assert out[0]["virality_score"] == 70


def test_penalty_is_capped_and_never_promotes():
    record = _analyse(SETUP_NO_PAYOFF)
    assert 0 < record.selection["penalty"] <= rt.MAX_RETENTION_PENALTY
    clean = _analyse(SETUP_PAYOFF)
    assert clean.selection["penalty"] == 0


def test_rejection_needs_overpromise_and_missing_context_and_surplus():
    both_bad = (
        "But I am going to show you how I fixed the car engine today. "
        "We talked about the weather for a while at lunch. "
        "Anyway that is all for today folks."
    )
    analysis = _analyse(both_bad)
    assert analysis.hook_honesty["verdict"] == "overpromises"
    assert analysis.context_sufficiency["level"] == "missing"
    assert analysis.selection["action"] == "reject"

    segments = [_segment(both_bad), _segment(SETUP_PAYOFF), _segment(QUESTION)]
    analyses = [analysis, _analyse(SETUP_PAYOFF), _analyse(QUESTION)]
    out = apply_retention_to_segments(segments, analyses, keep_at_least=2)
    assert len(out) == 2
    assert all(s["text"] != both_bad for s in out)
    # With no surplus the same candidate is demoted, never dropped:
    # rejection can never shrink the slate below its target.
    out = apply_retention_to_segments(segments, analyses, keep_at_least=3)
    assert len(out) == 3


def test_video_service_applies_retention_to_segment_dicts():
    transcript_data = {
        "words": [
            {"text": tok, "start": int(i * 400), "end": int(i * 400 + 350)}
            for i, tok in enumerate(SETUP_NO_PAYOFF.split())
        ]
    }
    # _segment_words reads the segment's own range; give the segment the
    # full range of the synthetic words (0 .. ~19.6s -> "00:20").
    segment = {
        "start_time": "00:00", "end_time": "00:20", "text": SETUP_NO_PAYOFF,
        "virality_score": 80, "hook_score": 20, "hook_title": None,
    }
    out = VideoService._apply_retention_analysis(
        [segment], transcript_data, keep_at_least=1
    )
    assert out[0]["retention"]["hook_honesty"]["verdict"] == "overpromises"
    assert out[0]["virality_score"] == 60


def test_retention_can_be_disabled(monkeypatch):
    monkeypatch.setenv("RETENTION_ANALYSIS_ENABLED", "false")
    assert rt.retention_enabled() is False
    segment = _segment(SETUP_NO_PAYOFF)
    assert VideoService._apply_retention_analysis([segment], None) == [segment]


# --- Opening strategies -----------------------------------------------------------

SEGMENTS = [
    {
        "start": 0.0, "end": 10.0, "duration": 10.0,
        "transition_in": "none", "transition_duration": 0.0,
        "source_offset": 0.0, "source_share": 0.5,
    },
    {
        "start": 10.0, "end": 20.0, "duration": 10.0,
        "transition_in": "cut", "transition_duration": 0.0,
        "source_offset": 10.0, "source_share": 0.5,
    },
]

STRATEGY_TEXT = (
    "Welcome back to the show everyone today. "
    "Why did the bridge collapse last winter suddenly? "
    "Wow, I cannot believe the footage we captured! "
    "The data shows 42 percent growth this year. "
    "He then revealed everything about the plan."
)


def _availability(text, plan=None, **kwargs):
    words = _words(text, step=0.5)
    return {
        entry["strategy"]: entry
        for entry in hv.strategy_availability(
            words, plan, duration=20.0, **kwargs
        )
    }


def test_strategies_are_found_in_the_clips_own_footage():
    avail = _availability(STRATEGY_TEXT)
    assert avail["strongest_statement"]["available"] is True
    assert avail["strongest_statement"]["sentence_text"].startswith("The data shows 42")
    assert avail["genuine_reaction"]["available"] is True
    assert avail["genuine_reaction"]["sentence_text"].startswith("Wow,")
    assert avail["question"]["available"] is True
    assert avail["question"]["sentence_text"].startswith("Why did the bridge")


def test_unexpected_visual_is_unavailable_without_a_visual_signal():
    avail = _availability(STRATEGY_TEXT)
    assert avail["unexpected_visual"]["available"] is False
    assert "no visual-change signal" in avail["unexpected_visual"]["reason"]
    avail = _availability(STRATEGY_TEXT, visual_change_seconds=[7.5])
    assert avail["unexpected_visual"]["available"] is True


def test_unsatisfiable_strategies_report_unavailable_not_faked():
    avail = _availability(
        "The panel discussed the schedule for next week calmly. "
        "They agreed on the plan for the morning session."
    )
    assert avail["question"]["available"] is False
    assert avail["genuine_reaction"]["available"] is False
    assert avail["strongest_statement"]["available"] is False


def test_context_guard_blocks_a_pronoun_led_strategy_sentence():
    avail = _availability(
        "Welcome back to the show everyone today. "
        "He never told anybody the truth about it. "
        "The room went quiet after that moment."
    )
    assert avail["strongest_statement"]["available"] is False
    assert "context guard" in avail["strongest_statement"]["reason"]
    assert "'he'" in avail["strongest_statement"]["reason"]


def test_context_guard_blocks_a_payoff_whose_setup_plays_earlier():
    plan = ClipPlan(
        niche="podcast_news", source="llm",
        hook=HookDecision(type="strong_statement", timestamp=11.5),
        phases=[
            Phase(0.0, 10.0, "build"),
            Phase(10.0, 20.0, "payoff"),
        ],
    )
    avail = _availability(STRATEGY_TEXT, plan)
    # The strongest statement sits inside the plan's payoff phase.
    assert avail["strongest_statement"]["available"] is False
    assert "payoff" in avail["strongest_statement"]["reason"]


def test_before_the_event_opens_on_the_sentence_before_the_payoff():
    words = _words(
        "We waited in the tunnel for the signal. "
        "The coach checked his notes one final time. "
        "Then the captain scored the winning goal late.",
        step=0.5,
    )
    plan = ClipPlan(
        niche="podcast_news", source="llm",
        hook=HookDecision(type="strong_statement", timestamp=8.0),
        phases=[
            Phase(0.0, 4.0, "context"),
            Phase(4.0, 8.0, "build"),
            Phase(8.0, 12.5, "payoff"),
        ],
    )
    avail = {
        entry["strategy"]: entry
        for entry in hv.strategy_availability(words, plan, duration=12.5)
    }
    assert avail["before_the_event"]["available"] is True
    assert avail["before_the_event"]["sentence_text"].startswith("The coach checked")
    specs = {
        spec["variant"]: spec
        for spec in hv.derive_strategy_variants(SEGMENTS, plan, words=words)
    }
    before = specs["before_the_event"]
    assert before["trims_head"] is True
    assert before["reorders"] is False
    # The layout starts at the pre-event sentence (source 4.0s in the
    # first segment window) and plays in the clip's own order.
    assert before["segments"][0]["source_offset"] == pytest.approx(4.0)


def test_strategy_variants_reorder_whole_sentences_only():
    words = _words(STRATEGY_TEXT, step=0.5)
    specs = hv.derive_variants(SEGMENTS, None, {}, words=words)
    variants = {spec["variant"]: spec for spec in specs}
    # Legacy variants are untouched, strategies are added, and the
    # signal-less unexpected_visual is absent.
    assert {"current", "punch"} <= set(variants)
    assert "unexpected_visual" not in variants
    question = variants["question"]
    assert question["strategy"] == "question"
    assert question["reorders"] is True
    # The question sentence (clip 3.5s, inside segment 1) leads, and the
    # total footage is preserved by the reordering.
    assert question["segments"][0]["source_offset"] == pytest.approx(3.5)
    total = sum(s["end"] - s["start"] for s in question["segments"])
    assert total == pytest.approx(20.0)
    for spec in variants.values():
        assert spec["label"]
        assert spec["description"]


def test_no_timed_words_means_no_strategy_variants():
    specs = hv.derive_variants(SEGMENTS, None, {})
    assert [spec["variant"] for spec in specs] == ["current", "punch"]
    avail = hv.strategy_availability([], None, duration=20.0)
    assert all(entry["available"] is False for entry in avail)


def test_strategy_variant_is_not_offered_twice_after_any_pick():
    words = _words(STRATEGY_TEXT, step=0.5)
    specs = hv.derive_variants(
        SEGMENTS, None, {}, words=words, hook_variant_applied="question"
    )
    assert not any(spec.get("strategy") for spec in specs)
