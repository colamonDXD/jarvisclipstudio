"""LLM focus pass: denylist guard, verbatim-evidence gate, fallback labelling."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from src.auto_director import content_requirements_llm as llm
from src.auto_director.content_requirements import (
    _merge_llm_judgement,
    evaluate_candidate_requirements,
    validate_content_brief,
)


@pytest.mark.parametrize(
    "model",
    ["google-gla:gemini-3-flash-preview", "gemini-2.0-flash", "openai:llama-3.3-70b-versatile", "groq/llama-3.3-70b-versatile"],
)
def test_banned_models_detected(model):
    assert llm.is_banned_model(model) is True


@pytest.mark.parametrize(
    "model",
    ["openai:gpt-oss-120b", "groq/openai/gpt-oss-120b", "openai:clip-assured", "openai:groq-20b", "ollama:qwen3:8b"],
)
def test_permitted_models_pass_guard(model):
    assert llm.is_banned_model(model) is False


async def test_banned_model_skips_pass(monkeypatch):
    monkeypatch.setattr(llm, "resolved_focus_model", lambda: None)
    assert await llm.judge_focus([{"candidate_id": 0, "text": "x"}], {"requirements": []}) is None


async def test_stubbed_agent_judgements_flow(monkeypatch):
    output = llm.FocusJudgements(
        judgements=[
            llm.RequirementJudgement(
                candidate_id=0, requirement_id="r1", focus="major",
                confidence=0.9, evidence_excerpt="Expedia is the whole trip", reason="central",
            )
        ]
    )
    agent = SimpleNamespace(run=AsyncMock(return_value=SimpleNamespace(output=output)))
    monkeypatch.setattr(llm, "resolved_focus_model", lambda: "openai:clip-assured")
    monkeypatch.setattr(llm, "get_focus_agent", lambda: agent)
    monkeypatch.setattr(llm.provider_manager, "manager_enabled", lambda *a, **k: False)
    result = await llm.judge_focus(
        [{"candidate_id": 0, "text": "Expedia is the whole trip"}], {"requirements": []}
    )
    assert result["0"]["r1"]["focus"] == "major"


async def test_agent_failure_returns_none(monkeypatch):
    agent = SimpleNamespace(run=AsyncMock(side_effect=RuntimeError("provider down")))
    monkeypatch.setattr(llm, "resolved_focus_model", lambda: "openai:clip-assured")
    monkeypatch.setattr(llm, "get_focus_agent", lambda: agent)
    monkeypatch.setattr(llm.provider_manager, "manager_enabled", lambda *a, **k: False)
    assert await llm.judge_focus([{"candidate_id": 0, "text": "x"}], {"requirements": []}) is None


def _entry(focus="passing"):
    return {
        "id": "r1", "type": "topic_focus", "kind": "mandatory", "focus": focus,
        "status": "failed", "requires_major": True, "grey_zone": focus == "passing",
        "judgement_source": "deterministic", "decided_by": "deterministic",
    }


def test_invented_evidence_is_discarded():
    merged = _merge_llm_judgement(
        _entry(),
        {"focus": "major", "confidence": 0.95, "evidence_excerpt": "words that were never said", "reason": "trust me"},
        "the actual candidate transcript text is here",
    )
    assert merged is None


def test_low_confidence_promotion_of_zero_hit_is_discarded():
    merged = _merge_llm_judgement(
        _entry(focus="absent"),
        {"focus": "major", "confidence": 0.4, "evidence_excerpt": "actual text", "reason": "maybe"},
        "actual text here",
    )
    assert merged is None


def test_valid_promotion_and_downgrade_are_recorded():
    merged = _merge_llm_judgement(
        _entry(),
        {"focus": "major", "confidence": 0.9, "evidence_excerpt": "expedia everywhere", "reason": "central"},
        "expedia everywhere in this clip",
    )
    assert merged["focus"] == "major" and merged["status"] == "satisfied"
    assert merged["decided_by"] == "llm"
    downgraded = _merge_llm_judgement(
        {**_entry(), "focus": "major", "status": "satisfied"},
        {"focus": "passing", "confidence": 0.8, "evidence_excerpt": "expedia everywhere", "reason": "just a mention"},
        "expedia everywhere in this clip",
    )
    assert downgraded["focus"] == "passing" and downgraded["status"] == "failed"


def test_llm_unavailable_grey_zone_labelled_uncertain():
    brief = validate_content_brief(
        {"requirements": [{"text": "Expedia must be a major focus of the content.", "kind": "mandatory"}]}
    )
    lines = [
        {"start_seconds": float(s), "end_seconds": float(s + 10),
         "text": "Expedia is honestly the whole reason this trip worked out well" if s == 0 else "generic chatter about lunch and the drive and weather today"}
        for s in range(0, 50, 10)
    ]
    verdict = evaluate_candidate_requirements(lines, 0, 50, brief, llm_available=False)
    entry = verdict["requirements"][0]
    assert entry["status"] == "uncertain"
    assert entry["judgement_source"] == "deterministic_only"
