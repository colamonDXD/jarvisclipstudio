"""D-1: an unbuildable analysis candidate advances down the chain.

Transcript analysis used to let an LLM client-BUILD failure (a
provider client rejecting a malformed base URL from settings/env)
propagate and kill the task, where niche classification treated the
identical failure as skippable. These tests pin the repaired
asymmetry: build failures advance through the failover chain, and
the first build error surfaces only when NO candidate builds.
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from src import ai
from tests.unit.test_ai_rate_limit import _analysis, _segment, _transcript

BUILD_ERROR = "Invalid port: ':1]'"


def _working_agent(segment_start=30, segment_end=90):
    return SimpleNamespace(
        run=AsyncMock(return_value=_analysis(_segment(segment_start, segment_end)))
    )


@pytest.mark.asyncio
async def test_default_build_failure_advances_to_fallback_model(monkeypatch):
    transcript = _transcript(5 * 60)
    monkeypatch.delenv("ANALYSIS_MODELS", raising=False)
    monkeypatch.delenv("ANALYSIS_MIN_INTERVAL_SEC", raising=False)
    agent = _working_agent()
    construction_calls: list[str | None] = []

    def build_agent(clip_length="auto", model_name=None):
        construction_calls.append(model_name)
        if model_name is None:
            raise RuntimeError(BUILD_ERROR)
        return agent

    monkeypatch.setattr(ai, "get_transcript_agent", build_agent)

    result = await ai.get_most_relevant_parts_by_transcript(transcript)

    assert [s.start_time for s in result.most_relevant_segments] == ["00:30"]
    # The default build was attempted first, then a chain candidate.
    assert construction_calls[0] is None
    assert len(construction_calls) >= 2
    assert construction_calls[1] is not None


@pytest.mark.asyncio
async def test_all_builds_fail_raises_the_first_build_error(monkeypatch):
    transcript = _transcript(5 * 60)
    monkeypatch.delenv("ANALYSIS_MODELS", raising=False)
    monkeypatch.delenv("ANALYSIS_MIN_INTERVAL_SEC", raising=False)

    def build_agent(clip_length="auto", model_name=None):
        if model_name is None:
            raise RuntimeError(BUILD_ERROR)
        raise RuntimeError(f"other build failure for {model_name}")

    monkeypatch.setattr(ai, "get_transcript_agent", build_agent)

    with pytest.raises(RuntimeError) as excinfo:
        await ai.get_most_relevant_parts_by_transcript(transcript)
    # The wrapped error names the FIRST build failure, not a later
    # candidate's — a fully broken configuration fails as it did.
    assert BUILD_ERROR in str(excinfo.value)
    assert "other build failure" not in str(excinfo.value)


@pytest.mark.asyncio
async def test_window_model_build_failure_advances(monkeypatch):
    transcript = _transcript(14 * 60)
    monkeypatch.setenv("TRANSCRIPT_ANALYSIS_MAX_TOKENS", "1500")
    monkeypatch.setenv("ANALYSIS_MODELS", "groq:model-a,groq:model-b")
    monkeypatch.setenv("ANALYSIS_MIN_INTERVAL_SEC", "0")
    monkeypatch.setenv("ANALYSIS_TPM", "8000")

    def analysis_for_prompt(prompt: str):
        import re

        body = prompt.split("Transcript:", 1)[1]
        match = re.search(r"\[(\d{2,}):(\d{2}) - ", body)
        first = int(match.group(1)) * 60 + int(match.group(2))
        return _analysis(_segment(first, first + 60))

    good_agent = SimpleNamespace(run=AsyncMock(side_effect=analysis_for_prompt))
    construction_calls: list[str | None] = []

    def build_agent(clip_length="auto", model_name=None):
        construction_calls.append(model_name)
        if model_name == "groq:model-b":
            raise RuntimeError(BUILD_ERROR)
        return good_agent

    monkeypatch.setattr(ai, "get_transcript_agent", build_agent)
    rerank_agent = SimpleNamespace(
        run=AsyncMock(
            side_effect=lambda prompt: SimpleNamespace(
                output=ai.CandidateRanking(
                    ranking=[
                        ai.RankedCandidate(candidate_id=i, score=90 - i)
                        for i in reversed(range(prompt.count("candidate_id:")))
                    ]
                )
            )
        )
    )
    monkeypatch.setattr(ai, "get_rerank_agent", lambda: rerank_agent)

    result = await ai.get_most_relevant_parts_by_transcript(transcript)

    assert len(result.most_relevant_segments) >= 3
    # model-b windows were attempted, failed to build, and advanced:
    # a later build (the chain's next configured model) supplied the
    # route instead of the window dying with the build error.
    assert "groq:model-b" in construction_calls
    first_b = construction_calls.index("groq:model-b")
    assert any(call != "groq:model-b" for call in construction_calls[first_b + 1:])


def test_helper_without_failover_reraises_first_error(monkeypatch):
    def build_agent(clip_length="auto", model_name=None):
        raise RuntimeError(BUILD_ERROR)

    monkeypatch.setattr(ai, "get_transcript_agent", build_agent)
    with pytest.raises(RuntimeError, match="Invalid port"):
        ai._build_transcript_agent_advancing("auto", None, None)
