"""Transcript analysis survives provider rate limits via pacing and rotation."""

import re
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from pydantic_ai.exceptions import ModelHTTPError

from src import ai

BUDGET = 1500
MODELS = "groq:model-a,groq:model-b"


def _label(seconds: int) -> str:
    minutes, secs = divmod(seconds, 60)
    return f"{minutes:02d}:{secs:02d}"


def _transcript(total_seconds: int, line_seconds: int = 10) -> str:
    filler = (
        "Speaker A: This line exists to carry a complete thought with enough detail "
        "for a realistic transcript segment."
    )
    return "\n".join(
        f"[{_label(start)} - {_label(start + line_seconds)}] {filler}"
        for start in range(0, total_seconds, line_seconds)
    )


def _segment(start: int, end: int) -> ai.TranscriptSegment:
    return ai.TranscriptSegment(
        start_time=_label(start),
        end_time=_label(end),
        text="Some useful words here.",
        relevance_score=0.8,
        virality=ai.ViralityAnalysis(
            hook_score=20,
            engagement_score=20,
            value_score=20,
            shareability_score=20,
            total_score=80,
        ),
    )


def _analysis(segment: ai.TranscriptSegment) -> SimpleNamespace:
    return SimpleNamespace(
        output=ai.TranscriptAnalysis(
            most_relevant_segments=[segment],
            summary="chunk",
            key_topics=["topic"],
        )
    )


def _first_line_seconds(prompt: str) -> int:
    body = prompt.split("Transcript:", 1)[1]
    match = re.search(r"\[(\d{2,}):(\d{2}) - ", body)
    assert match is not None
    return int(match.group(1)) * 60 + int(match.group(2))


class _ProviderCause(Exception):
    """Stands in for the Groq SDK error that carries the HTTP response."""

    def __init__(self, headers: dict[str, str]):
        self.response = SimpleNamespace(headers=headers)


def _rate_limit_error(headers: dict[str, str] | None = None, status: int = 429):
    error = ModelHTTPError(status, "groq:test")
    if headers is not None:
        # The Groq SDK wraps its APIStatusError, which carries the response.
        error.__cause__ = _ProviderCause(headers)
    return error


@pytest.fixture
def retry_sleep(monkeypatch):
    sleep = AsyncMock()
    monkeypatch.setattr(ai.asyncio, "sleep", sleep)
    return sleep


@pytest.mark.asyncio
async def test_rate_limit_with_retry_after_recovers_and_returns_the_chunk(
    monkeypatch, retry_sleep
):
    transcript = _transcript(5 * 60)
    monkeypatch.delenv("ANALYSIS_MODELS", raising=False)
    monkeypatch.delenv("ANALYSIS_MIN_INTERVAL_SEC", raising=False)
    analysis = _analysis(_segment(30, 90))
    agent = SimpleNamespace(
        run=AsyncMock(
            side_effect=[
                _rate_limit_error({"retry-after": "0.25"}),
                _rate_limit_error({"x-ratelimit-reset-tokens": "0.5s"}),
                analysis,
            ]
        )
    )
    monkeypatch.setattr(ai, "get_transcript_agent", lambda clip_length="auto": agent)

    result = await ai.get_most_relevant_parts_by_transcript(transcript)

    assert [segment.start_time for segment in result.most_relevant_segments] == [
        "00:30"
    ]
    assert agent.run.await_count == 3
    # First wait honours retry-after, the second the reset-tokens header.
    assert [call.args[0] for call in retry_sleep.await_args_list] == [0.25, 0.5]


@pytest.mark.asyncio
async def test_chunks_rotate_round_robin_across_configured_models(monkeypatch):
    transcript = _transcript(14 * 60)
    monkeypatch.setenv("TRANSCRIPT_ANALYSIS_MAX_TOKENS", str(BUDGET))
    monkeypatch.setenv("ANALYSIS_MODELS", MODELS)
    monkeypatch.setenv("ANALYSIS_MIN_INTERVAL_SEC", "0")
    monkeypatch.setenv("ANALYSIS_TPM", "8000")

    def analysis_for_prompt(prompt: str):
        first = _first_line_seconds(prompt)
        return _analysis(_segment(first, first + 60))

    agents = {
        name: SimpleNamespace(run=AsyncMock(side_effect=analysis_for_prompt))
        for name in ("groq:model-a", "groq:model-b")
    }
    construction_calls: list[str | None] = []

    def build_agent(clip_length="auto", model_name=None):
        construction_calls.append(model_name)
        return agents[model_name]

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
    monkeypatch.setattr(ai, "get_transcript_agent", build_agent)
    monkeypatch.setattr(ai, "get_rerank_agent", lambda: rerank_agent)

    result = await ai.get_most_relevant_parts_by_transcript(transcript)

    # Four token-budget chunks alternate between the two configured models.
    assert construction_calls == [
        "groq:model-a",
        "groq:model-b",
        "groq:model-a",
        "groq:model-b",
    ]
    assert agents["groq:model-a"].run.await_count == 2
    assert agents["groq:model-b"].run.await_count == 2
    starts = [segment.start_time for segment in result.most_relevant_segments]
    assert len(starts) == len(set(starts))
    assert len(starts) >= 3


@pytest.mark.asyncio
async def test_rate_limit_retries_are_bounded_and_raise_the_original_error(
    retry_sleep,
):
    error = _rate_limit_error()
    agent = SimpleNamespace(run=AsyncMock(side_effect=error))

    with pytest.raises(ModelHTTPError) as caught:
        await ai._run_transcript_analysis(agent, "prompt")

    assert caught.value is error
    assert agent.run.await_count == ai.ANALYSIS_RATE_LIMIT_MAX_ATTEMPTS
    # Exponential backoff starts at 20s and doubles for each retry.
    assert [call.args[0] for call in retry_sleep.await_args_list] == [
        20,
        40,
        80,
        160,
        320,
    ]


def test_pacing_interval_uses_the_token_bucket_when_enabled():
    assert ai._AnalysisPacer(0, 8000).interval_for(1000) == 0
    # 1000 tokens at 8000 TPM needs 7.5s between starts.
    assert ai._AnalysisPacer(1, 8000).interval_for(1000) == 7.5
    # An explicit larger floor still wins.
    assert ai._AnalysisPacer(10, 8000).interval_for(1000) == 10
