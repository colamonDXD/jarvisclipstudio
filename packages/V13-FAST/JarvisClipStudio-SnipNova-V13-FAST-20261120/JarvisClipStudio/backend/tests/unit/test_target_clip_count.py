"""Explicit per-task clip-count target (create-form selector).

Covers the three layers that must agree for the selector to be honest:
- validation: only whole numbers in [5, 30] (or None for Auto);
- the selection limit: a set target replaces the duration formula, an
  unset target keeps the formula byte-for-byte;
- end-to-end against the fake LLM server: a target of 30 on a two-hour
  transcript really yields 30 distinct segments (the per-window ask is
  raised so the candidate pool can reach it), a target of 5 caps a long
  video at 5, and when the model can only find a few distinct moments
  the result is those few — targets are never padded with duplicates.
"""

from __future__ import annotations

import json
import re

import pytest

from fake_llm_server import FakeLLMServer, success_payload
from src import ai, provider_manager


# --- formula + validation (no network) ------------------------------------


@pytest.mark.parametrize(
    ("seconds", "max_clips", "expected"),
    [
        (9 * 60, 20, 5),  # the 9-minute acceptance video: 5, as today
        (20 * 60, 20, 5),
        (45 * 60, 20, 9),
        (2 * 3600, 20, 20),  # the formula's own ceiling
        (2 * 3600, 10, 10),  # a lower configured MAX_CLIPS still wins
    ],
)
def test_formula_is_unchanged_without_a_target(seconds, max_clips, expected):
    assert ai.max_final_clips_for_duration(seconds, max_clips) == expected
    assert (
        ai.max_final_clips_for_duration(seconds, max_clips, target_clip_count=None)
        == expected
    )


@pytest.mark.parametrize(
    ("seconds", "target", "expected"),
    [
        (2 * 3600, 30, 30),  # target above the formula ceiling: allowed
        (2 * 3600, 5, 5),  # target below the formula: caps the task
        (9 * 60, 30, 30),  # target replaces the formula outright
        (9 * 60, 5, 5),
        (2 * 3600, 45, 30),  # defensive clamp at the hard ceiling
    ],
)
def test_target_replaces_the_formula(seconds, target, expected):
    assert (
        ai.max_final_clips_for_duration(seconds, 20, target_clip_count=target)
        == expected
    )


def test_validator_accepts_none_and_the_full_range():
    assert ai.validate_target_clip_count(None) is None
    assert ai.validate_target_clip_count(5) == 5
    assert ai.validate_target_clip_count(17) == 17
    assert ai.validate_target_clip_count(30) == 30


@pytest.mark.parametrize(
    "bad", [0, 4, 31, 100, -5, 30.0, 5.5, "30", "five", True, False, [30], {"n": 30}]
)
def test_validator_rejects_out_of_range_and_non_integers(bad):
    with pytest.raises(ValueError):
        ai.validate_target_clip_count(bad)


@pytest.mark.parametrize(
    ("target", "calls", "expected"),
    [
        # Brain upgrade (2026-11-11): per-call headroom is +3 (the N+3
        # over-ask), up from +2, so the merged pool always exceeds the
        # target by at least three before dedupe and rerank.
        (30, 7, "5-8"),  # two-hour long-form route: seven windows
        (30, 1, "9-12"),  # a single call carries the whole ask, capped
        (5, 7, "2-5"),
        (10, 3, "4-7"),
        (30, 6, "5-8"),
    ],
)
def test_segment_target_spreads_the_ask_across_calls(target, calls, expected):
    assert ai._segment_target_for(target, calls) == expected


# --- end-to-end against the fake LLM server --------------------------------

_SPAN_RE = re.compile(r"\[(\d+):(\d{2}) - (\d+):(\d{2})\]")


def _label(seconds: int) -> str:
    return f"{seconds // 60:02d}:{seconds % 60:02d}"


def make_transcript(total_seconds: int, line_seconds: int = 10) -> str:
    lines = []
    for start in range(0, total_seconds, line_seconds):
        n = start // line_seconds
        lines.append(
            f"[{_label(start)} - {_label(start + line_seconds)}] Speaker A: point "
            f"number {n} develops one concrete idea with a specific example, a "
            f"number, and a clear takeaway so the segment stands on its own."
        )
    return "\n".join(lines)


def _clear_agent_caches() -> None:
    ai._transcript_agent = None
    ai._transcript_agent_signature = None
    ai._transcript_agents_by_model.clear()
    ai._rerank_agent = None
    ai._rerank_agent_signature = None


@pytest.fixture(autouse=True)
def _reset_agent_caches():
    _clear_agent_caches()
    provider_manager.reset_manager_for_tests()
    yield
    _clear_agent_caches()
    provider_manager.reset_manager_for_tests()


def _configure_gateway(monkeypatch, base_url: str) -> None:
    env = {
        "LLM": "openai:clip-fast",
        "OPENAI_API_KEY": "shim-key",
        "OPENAI_BASE_URL": base_url,
        "ANALYSIS_MODELS": "openai:clip-fast,openai:clip-assured",
        "ANALYSIS_FALLBACK_MODEL": "openai:local-qwen",
        "ANALYSIS_DIRECT_FALLBACK_MODEL": "openai:local-qwen",
        # Explicit 0 disables request pacing so these tests stay fast.
        "ANALYSIS_MIN_INTERVAL_SEC": "0",
        "no_proxy": "localhost,127.0.0.1",
        "NO_PROXY": "localhost,127.0.0.1",
    }
    for key, value in env.items():
        monkeypatch.setenv(key, value)


def _request_text(body: dict) -> str:
    parts = []
    for message in body.get("messages") or []:
        content = message.get("content")
        if isinstance(content, str):
            parts.append(content)
        elif isinstance(content, list):
            for item in content:
                if isinstance(item, dict) and isinstance(item.get("text"), str):
                    parts.append(item["text"])
    return "\n".join(parts)


def _is_rerank(body: dict) -> bool:
    tools = body.get("tools") or []
    if not tools:
        return False
    function = tools[0].get("function") or {}
    properties = (function.get("parameters") or {}).get("properties") or {}
    return "ranking" in properties


def _payload(body: dict, arguments: dict) -> dict:
    tools = body.get("tools") or []
    tool_name = "final_result"
    if tools:
        tool_name = (tools[0].get("function") or {}).get("name") or tool_name
    return {
        "id": "chatcmpl-shim",
        "object": "chat.completion",
        "created": 0,
        "model": body.get("model", "shim"),
        "choices": [
            {
                "index": 0,
                "message": {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [
                        {
                            "id": "call_shim_1",
                            "type": "function",
                            "function": {
                                "name": tool_name,
                                "arguments": json.dumps(arguments),
                            },
                        }
                    ],
                },
                "finish_reason": "tool_calls",
            }
        ],
        "usage": {"prompt_tokens": 100, "completion_tokens": 10, "total_tokens": 110},
    }


def _segment_dict(start: int, end: int) -> dict:
    return {
        "start_time": _label(start),
        "end_time": _label(end),
        "text": "A self-contained moment from the transcript.",
        "relevance_score": 0.9,
        "reasoning": "Chosen by the fake LLM server for testing.",
        "hook_title": "A shim hook title",
        "virality": {
            "hook_score": 20,
            "engagement_score": 20,
            "value_score": 20,
            "shareability_score": 20,
            "total_score": 80,
            "hook_type": "statement",
            "virality_reasoning": "Shim reasoning.",
        },
    }


def _transcript_lines(text: str) -> list[tuple[int, int]]:
    body_text = text.split("Transcript:", 1)[1] if "Transcript:" in text else text
    lines = []
    for raw in body_text.splitlines():
        match = _SPAN_RE.match(raw.strip())
        if match:
            lines.append(
                (
                    int(match.group(1)) * 60 + int(match.group(2)),
                    int(match.group(3)) * 60 + int(match.group(4)),
                )
            )
    return lines


def honour_ask_behaviour(body: dict, _count: int):
    """Fake LLM that returns as many distinct segments as the prompt asks.

    Segments are 40-second chunks taken from the start of the window, so
    overlapping windows agree on the ranges they share and dedupe has
    something real to merge.
    """
    if _is_rerank(body):
        return 200, {}, success_payload(body)
    text = _request_text(body)
    ask = re.search(r"Choose (\d+)-(\d+) segments total", text)
    high = int(ask.group(2)) if ask else 5
    lines = _transcript_lines(text)
    segments = []
    for index in range(0, len(lines) - 3, 4):
        chunk = lines[index : index + 4]
        segments.append(_segment_dict(chunk[0][0], chunk[-1][1]))
        if len(segments) >= high:
            break
    return 200, {}, _payload(
        body,
        {
            "most_relevant_segments": segments,
            "summary": "Shim summary.",
            "key_topics": ["shim"],
        },
    )


def _window_prompts(server) -> list[str]:
    return [
        _request_text(request["body"])
        for request in server.requests
        if "one window" in _request_text(request["body"])
    ]


async def test_target_30_on_a_two_hour_transcript_yields_30_distinct(
    monkeypatch,
):
    with FakeLLMServer(honour_ask_behaviour) as server:
        _configure_gateway(monkeypatch, server.base_url)
        result = await ai.get_most_relevant_parts_by_transcript(
            make_transcript(2 * 3600), target_clip_count=30
        )

    segments = result.most_relevant_segments
    assert len(segments) == 30
    ranges = [(s.start_time, s.end_time) for s in segments]
    assert len(set(ranges)) == 30, "selected segments must all be distinct"
    # The ask per window was raised above the built-in "3-5" so the pool
    # could reach the target (seven long-form windows for two hours).
    prompts = _window_prompts(server)
    assert len(prompts) == 7
    # Brain upgrade (2026-11-11): the per-call ask carries +3 headroom
    # (was +2), so seven windows are asked for 5-8 each, not 4-7.
    assert all("Choose 5-8 segments total." in prompt for prompt in prompts)


async def test_target_5_caps_a_two_hour_transcript_at_5(monkeypatch):
    with FakeLLMServer(honour_ask_behaviour) as server:
        _configure_gateway(monkeypatch, server.base_url)
        result = await ai.get_most_relevant_parts_by_transcript(
            make_transcript(2 * 3600), target_clip_count=5
        )

    segments = result.most_relevant_segments
    assert len(segments) == 5
    ranges = [(s.start_time, s.end_time) for s in segments]
    assert len(set(ranges)) == 5
    prompts = _window_prompts(server)
    assert all("Choose 2-5 segments total." in prompt for prompt in prompts)


def sparse_behaviour(body: dict, _count: int):
    """Fake LLM that can only find a handful of moments, however many are
    asked for: one at its window's start, plus one fixed moment that two
    overlapping windows both report (a duplicate for dedupe to merge)."""
    if _is_rerank(body):
        return 200, {}, success_payload(body)
    lines = _transcript_lines(_request_text(body))
    segments = []
    if lines:
        segments.append(_segment_dict(lines[0][0], lines[3][1]))
    if any(start == 630 for start, _end in lines):
        segments.append(_segment_dict(630, 670))
    return 200, {}, _payload(
        body,
        {
            "most_relevant_segments": segments,
            "summary": "Shim summary.",
            "key_topics": ["shim"],
        },
    )


async def test_target_is_never_padded_with_duplicates(monkeypatch):
    with FakeLLMServer(sparse_behaviour) as server:
        _configure_gateway(monkeypatch, server.base_url)
        result = await ai.get_most_relevant_parts_by_transcript(
            make_transcript(30 * 60), target_clip_count=30
        )

    segments = result.most_relevant_segments
    # Three window openers plus the one shared moment, reported twice and
    # deduplicated once: exactly 4 distinct clips, not 30 padded ones.
    assert len(segments) == 4
    assert sorted(s.start_time for s in segments) == [
        "00:00",
        "10:00",
        "10:30",
        "20:00",
    ]
    ranges = [(s.start_time, s.end_time) for s in segments]
    assert len(set(ranges)) == len(ranges)
