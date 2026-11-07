"""Transcript analysis stays under a configurable per-request token budget."""

import re
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from src import ai

BUDGET = 1500


def _label(seconds: int) -> str:
    minutes, secs = divmod(seconds, 60)
    return f"{minutes:02d}:{secs:02d}"


def _transcript(total_seconds: int, line_seconds: int = 10, detail: str = "") -> str:
    filler = detail or (
        "Speaker A: This line exists to carry a complete thought with enough detail "
        "for a realistic transcript segment."
    )
    return "\n".join(
        f"[{_label(start)} - {_label(start + line_seconds)}] {filler}"
        for start in range(0, total_seconds, line_seconds)
    )


def _segment(start: int, end: int, total: int = 80) -> ai.TranscriptSegment:
    return ai.TranscriptSegment(
        start_time=_label(start),
        end_time=_label(end),
        text="Some useful words here.",
        relevance_score=0.8,
        virality=ai.ViralityAnalysis(
            hook_score=total // 4,
            engagement_score=total // 4,
            value_score=total // 4,
            shareability_score=total - 3 * (total // 4),
            total_score=total,
        ),
    )


def _line_starts(prompt: str) -> list[int]:
    body = prompt.split("Transcript:", 1)[1]
    return [
        int(match.group(1)) * 60 + int(match.group(2))
        for match in re.finditer(r"\[(\d{2,}):(\d{2}) - ", body)
    ]


@pytest.mark.asyncio
async def test_long_transcript_is_chunked_merged_and_deduplicated(monkeypatch):
    # 14 minutes in a single duration window: exactly the request that produced
    # a ~10k-token Groq call before the token budget existed.
    transcript = _transcript(14 * 60)
    monkeypatch.setenv("TRANSCRIPT_ANALYSIS_MAX_TOKENS", str(BUDGET))
    prompts: list[str] = []

    def analysis_for_prompt(prompt: str):
        prompts.append(prompt)
        first = _line_starts(prompt)[0]
        segments = [_segment(first, first + 60)]
        # Models can repeat a moment across chunks; the merge must keep it once.
        segments.append(_segment(10 * 60, 11 * 60, total=92))
        return SimpleNamespace(
            output=ai.TranscriptAnalysis(
                most_relevant_segments=segments,
                summary="chunk",
                key_topics=["topic"],
            )
        )

    agent = SimpleNamespace(run=AsyncMock(side_effect=analysis_for_prompt))
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
    monkeypatch.setattr(ai, "get_transcript_agent", lambda clip_length="auto": agent)
    monkeypatch.setattr(ai, "get_rerank_agent", lambda: rerank_agent)

    result = await ai.get_most_relevant_parts_by_transcript(transcript)

    assert agent.run.await_count >= 3
    # Every individual mocked provider request stays under the budget.
    assert all(ai._estimate_tokens(prompt) <= BUDGET for prompt in prompts)
    rerank_agent.run.assert_awaited_once()

    starts = [segment.start_time for segment in result.most_relevant_segments]
    assert len(starts) == len(set(starts))
    # The repeated 10:00 moment is merged into one absolute timestamp.
    assert "10:00" in starts
    # Chunks keep absolute timestamps instead of restarting at 00:00.
    assert max(
        int(start.split(":")[0]) * 60 + int(start.split(":")[1]) for start in starts
    ) >= 10 * 60
    assert result.key_topics == ["topic"]


@pytest.mark.asyncio
async def test_short_transcript_uses_the_single_request_path_unchanged(monkeypatch):
    transcript = _transcript(5 * 60)
    monkeypatch.delenv("TRANSCRIPT_ANALYSIS_MAX_TOKENS", raising=False)
    prompts: list[str] = []

    def analysis(prompt: str):
        prompts.append(prompt)
        return SimpleNamespace(
            output=ai.TranscriptAnalysis(
                most_relevant_segments=[_segment(30, 90)],
                summary="short",
                key_topics=["short"],
            )
        )

    agent = SimpleNamespace(run=AsyncMock(side_effect=analysis))
    monkeypatch.setattr(ai, "get_transcript_agent", lambda clip_length="auto": agent)
    monkeypatch.setattr(
        ai,
        "get_rerank_agent",
        lambda: (_ for _ in ()).throw(AssertionError("single path must not rerank")),
    )

    result = await ai.get_most_relevant_parts_by_transcript(transcript)

    assert agent.run.await_count == 1
    assert prompts[0].endswith(transcript)
    assert [segment.start_time for segment in result.most_relevant_segments] == ["00:30"]
    assert result.key_topics == ["short"]
