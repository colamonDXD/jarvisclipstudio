"""Brain upgrade: premium reasoning effort, gated rubric, richer context.

Proofs for the 2026-11-11 selection-brain lane:

- ``reasoning_effort=high`` rides ONLY the final selection pass (the
  rerank, or the single analysis call when there is no rerank) and only
  on OpenAI-compatible / direct Groq routes;
- a route that refuses the parameter degrades to a plain call — the
  analysis never fails because of the knob;
- the analysis and rerank prompts carry the gated /100 rubric, the
  hook-archetype calibration, the gold few-shot examples, the source
  context block and the scene-boundary hints — and stay free of the
  new sections when no context exists;
- end to end against the fake gateway: window (volume) request bodies
  carry no reasoning_effort, the premium bodies do.
"""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from fake_llm_server import FakeLLMServer, success_payload
from src import ai, provider_manager
from src.ai import (
    CandidateRanking,
    RankedCandidate,
    TranscriptSegment,
    ViralityAnalysis,
    build_candidate_rerank_prompt,
    build_transcript_analysis_prompt,
    build_transcript_system_prompt,
)


# --- premium reasoning settings -------------------------------------------


def test_premium_settings_only_for_openai_compatible_and_groq_routes():
    expected = {"extra_body": {"reasoning_effort": "high"}}
    assert ai._premium_reasoning_settings("openai:clip-fast") == expected
    assert ai._premium_reasoning_settings("openai:clip-assured") == expected
    # The canonical openai-chat: spelling names the same provider, so the
    # premium pass keeps its reasoning effort after the prefix migration.
    assert ai._premium_reasoning_settings("openai-chat:clip-fast") == expected
    assert ai._premium_reasoning_settings("openai-chat:gpt-5.2") == expected
    assert ai._premium_reasoning_settings("groq:openai/gpt-oss-120b") == expected


def test_premium_settings_absent_for_other_providers():
    # Other providers keep their own configuration untouched: the direct
    # OpenRouter path already pins its reasoning effort in model
    # settings, Ollama/Anthropic routes take no such parameter here.
    assert ai._premium_reasoning_settings("ollama:qwen3:8b") is None
    assert ai._premium_reasoning_settings("openrouter:anthropic/claude-sonnet-5-5") is None
    assert ai._premium_reasoning_settings("anthropic:claude-sonnet-5-5") is None
    assert ai._premium_reasoning_settings("google:gemini-2.5-pro") is None
    assert ai._premium_reasoning_settings(None) is None
    assert ai._premium_reasoning_settings("") is None


def test_params_rejection_recognition():
    class UnsupportedParamsError(Exception):
        pass

    class FakeHTTPError(Exception):
        status_code = 400

    assert ai._is_params_rejection_error(UnsupportedParamsError("nope"))
    assert ai._is_params_rejection_error(
        FakeHTTPError("groq does not support parameters: ['reasoning_effort']")
    )
    # A plain 400 about something else is not a parameter refusal.
    assert not ai._is_params_rejection_error(FakeHTTPError("bad transcript"))
    assert not ai._is_params_rejection_error(ValueError("reasoning_effort"))


class _RecordingAgent:
    """Agent stub: records run kwargs, replays a scripted behaviour."""

    def __init__(self, behaviours):
        self._behaviours = list(behaviours)
        self.calls: list[dict] = []

    async def run(self, prompt, **kwargs):
        self.calls.append(kwargs)
        behaviour = self._behaviours.pop(0)
        if isinstance(behaviour, BaseException):
            raise behaviour
        return behaviour


async def test_run_analysis_passes_premium_settings_through():
    result = SimpleNamespace(output="ok")
    agent = _RecordingAgent([result])
    settings = {"extra_body": {"reasoning_effort": "high"}}
    out = await ai._run_transcript_analysis(
        agent, "prompt", timeout_seconds=30, model_settings=settings
    )
    assert out is result
    assert agent.calls == [{"model_settings": settings}]


async def test_run_analysis_without_settings_calls_plainly():
    agent = _RecordingAgent([SimpleNamespace(output="ok")])
    await ai._run_transcript_analysis(agent, "prompt", timeout_seconds=30)
    assert agent.calls == [{}]


async def test_run_analysis_degrades_when_route_refuses_the_parameter():
    class UnsupportedParamsError(Exception):
        pass

    agent = _RecordingAgent(
        [UnsupportedParamsError("groq: ['reasoning_effort']"), SimpleNamespace(output="ok")]
    )
    out = await ai._run_transcript_analysis(
        agent,
        "prompt",
        timeout_seconds=30,
        model_settings={"extra_body": {"reasoning_effort": "high"}},
    )
    assert out.output == "ok"
    # First call carried the knob; the retry is a plain call.
    assert agent.calls[0] != {}
    assert agent.calls[1] == {}


async def test_run_analysis_degrades_on_http_400_naming_the_parameter():
    class FakeHTTPError(Exception):
        status_code = 400

    agent = _RecordingAgent(
        [
            FakeHTTPError("Unsupported parameter: reasoning_effort"),
            SimpleNamespace(output="ok"),
        ]
    )
    out = await ai._run_transcript_analysis(
        agent,
        "prompt",
        timeout_seconds=30,
        model_settings={"extra_body": {"reasoning_effort": "high"}},
    )
    assert out.output == "ok"
    assert agent.calls[1] == {}


async def test_run_analysis_does_not_retry_unrelated_errors():
    agent = _RecordingAgent([ValueError("boom")])
    with pytest.raises(ValueError):
        await ai._run_transcript_analysis(
            agent,
            "prompt",
            timeout_seconds=30,
            model_settings={"extra_body": {"reasoning_effort": "high"}},
        )
    assert len(agent.calls) == 1


# --- rerank wiring ----------------------------------------------------------


def _segment(start: str, end: str, text: str) -> TranscriptSegment:
    return TranscriptSegment(
        start_time=start, end_time=end, text=text, relevance_score=0.9,
        reasoning="r", hook_title="A hook title",
    )


async def test_rerank_runs_at_premium_effort_on_gateway_routes(monkeypatch):
    ranking = CandidateRanking(
        ranking=[
            RankedCandidate(candidate_id=1, score=90, reason="stronger"),
            RankedCandidate(candidate_id=0, score=70, reason="weaker"),
        ]
    )
    agent = _RecordingAgent([SimpleNamespace(output=ranking)])
    monkeypatch.setattr(ai, "get_rerank_agent", lambda: agent)
    candidates = [
        _segment("00:00", "00:30", "first candidate text here"),
        _segment("05:00", "05:30", "second candidate text here"),
    ]
    reranked = await ai.rerank_candidates(candidates, model_name="openai:clip-fast")
    assert [s.start_time for s in reranked] == ["05:00", "00:00"]
    assert agent.calls[0]["model_settings"] == {
        "extra_body": {"reasoning_effort": "high"}
    }


async def test_rerank_stays_default_on_local_routes(monkeypatch):
    ranking = CandidateRanking(
        ranking=[
            RankedCandidate(candidate_id=0, score=90, reason="a"),
            RankedCandidate(candidate_id=1, score=80, reason="b"),
        ]
    )
    agent = _RecordingAgent([SimpleNamespace(output=ranking)])
    monkeypatch.setattr(ai, "get_rerank_agent", lambda: agent)
    candidates = [
        _segment("00:00", "00:30", "first candidate text here"),
        _segment("05:00", "05:30", "second candidate text here"),
    ]
    await ai.rerank_candidates(candidates, model_name="ollama:qwen3:8b")
    assert "model_settings" not in agent.calls[0]


# --- prompt content ---------------------------------------------------------


def test_system_prompt_carries_the_gated_rubric():
    prompt = build_transcript_system_prompt()
    # The four hard gates.
    assert "The first sentence needs no context" in prompt
    assert "One complete idea" in prompt
    assert "No intro, outro, sponsor or crosstalk material" in prompt
    assert "No unowned media" in prompt
    # The /100 components and decision bands.
    assert "Hook in the first 3 seconds (25)" in prompt
    assert "Stands alone (20)" in prompt
    assert "Payoff / turn (20)" in prompt
    assert "Never return a segment under 55" in prompt
    # Coherence red flags named verbatim.
    assert "As I mentioned earlier..." in prompt
    assert "Going back to what we discussed..." in prompt
    # Slate spacing + diversity.
    assert "at least 2 minutes apart" in prompt


def test_system_prompt_carries_hook_calibration_and_boosters():
    prompt = build_transcript_system_prompt()
    for archetype in (
        "Bold / Contrarian claim: 80-100",
        "Curiosity Gap: 75-95",
        "Value Promise: 70-90",
        "Pattern Interrupt: 70-90",
        "Payoff Preview: 65-85",
        "Mid-Action opening: 60-80",
        "Hidden Knowledge: 60-80",
        "Weak / Generic opener: 10-40",
    ):
        assert archetype in prompt
    assert "Boosters: add +5-10 to the total" in prompt
    assert "specific number" in prompt and "personal experience" in prompt


def test_system_prompt_carries_gold_few_shot_examples():
    prompt = build_transcript_system_prompt()
    assert "CALIBRATION EXAMPLES" in prompt
    assert "DIFFERENT video" in prompt
    # All five editor-validated Japan windows, by timestamp and score.
    for marker in (
        "14:50-15:10 (20s, scored 92)",
        "28:11-28:38 (27s, scored 90)",
        "22:04-22:29 (25s, scored 88)",
        "40:39-41:09 (30s, scored 86)",
        "42:24-42:52 (28s, scored 84)",
        "700-year-old tree",
        "jazz massages",
        "real gold in the sandwich",
        "four pieces of sushi cost $400",
    ):
        assert marker in prompt


def test_rerank_prompt_carries_the_same_calibration():
    assert "Calibrate like the first pass" in ai.candidate_rerank_system_prompt
    assert "2 minutes apart" in ai.candidate_rerank_system_prompt
    prompt = build_candidate_rerank_prompt(
        [_segment("00:00", "00:30", "some candidate text here")]
    )
    assert "candidate_id: 0" in prompt


def test_category_field_accepts_the_eight_categories_only():
    for category in (
        "hooks", "emotional_peaks", "opinion_bombs", "revelations",
        "conflict", "quotable_lines", "story_peaks", "practical_value",
    ):
        assert ViralityAnalysis(category=category).category == category
    assert ViralityAnalysis().category is None
    with pytest.raises(ValidationError):
        ViralityAnalysis(category="vibes")


def test_source_context_block_renders_and_truncates():
    section = ai._source_context_section(
        "CORE $1 VS $10,000 RESTAURANTS IN JAPAN",
        "MrBeast",
        "word " * 400,
    )
    assert "- Title: CORE $1 VS $10,000 RESTAURANTS IN JAPAN" in section
    assert "- Creator / channel: MrBeast" in section
    assert "must never be quoted as segment text" in section
    description_line = next(
        line for line in section.splitlines() if line.startswith("- Description:")
    )
    assert len(description_line) <= len("- Description: ") + 601
    assert description_line.endswith("…")


def test_source_context_absent_without_metadata():
    assert ai._source_context_section(None, None, None) == ""
    assert ai._source_context_section("  ", "", None) == ""
    prompt = build_transcript_analysis_prompt(
        transcript="[00:12 - 00:21] A strong opening line"
    )
    assert "Source video context" not in prompt


def test_analysis_prompt_includes_context_and_scene_hints():
    section = ai._source_context_section("A Title", "A Channel", None)
    prompt = build_transcript_analysis_prompt(
        transcript="[00:12 - 00:21] A strong opening line",
        source_context=section,
        scene_cut_times=[12.0, 65.0, 400.0],
    )
    assert "- Title: A Title" in prompt
    assert "- Creator / channel: A Channel" in prompt
    assert "Scene boundaries (visual cuts detected in the video) at: 00:12, 01:05, 06:40" in prompt
    assert "not by itself a reason to start or end a clip" in prompt


def test_scene_hints_are_filtered_to_the_window():
    prompt = build_transcript_analysis_prompt(
        transcript="[10:12 - 10:21] A strong opening line",
        window=(600, 900),
        scene_cut_times=[12.0, 650.0, 4000.0],
    )
    assert "at: 10:50" in prompt
    assert "00:12" not in prompt.split("Scene boundaries")[1]
    # No cuts inside the window: no section at all.
    prompt = build_transcript_analysis_prompt(
        transcript="[10:12 - 10:21] A strong opening line",
        window=(600, 900),
        scene_cut_times=[12.0],
    )
    assert "Scene boundaries" not in prompt


def test_over_ask_pool_exceeds_target_by_three():
    # A single call carrying a mid-size target asks target+3 outright.
    assert ai._segment_target_for(5, 1) == "5-8"
    # Spread across calls, the pool still clears target+3 overall.
    low_high = ai._segment_target_for(10, 3)
    assert low_high == "4-7"


# --- end-to-end request bodies against the fake gateway ---------------------


def _make_transcript(total_seconds: int, line_seconds: int = 10) -> str:
    def label(s: int) -> str:
        return f"{s // 60:02d}:{s % 60:02d}"

    lines = []
    for start in range(0, total_seconds, line_seconds):
        n = start // line_seconds
        lines.append(
            f"[{label(start)} - {label(start + line_seconds)}] Speaker A: point "
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
        "ANALYSIS_MIN_INTERVAL_SEC": "0",
        "no_proxy": "localhost,127.0.0.1",
        "NO_PROXY": "localhost,127.0.0.1",
    }
    for key, value in env.items():
        monkeypatch.setenv(key, value)


def _body_text(body: dict) -> str:
    return json.dumps(body, sort_keys=True)


async def test_short_video_premium_body_carries_everything(monkeypatch):
    with FakeLLMServer(
        lambda body, count: (200, {}, success_payload(body))
    ) as server:
        _configure_gateway(monkeypatch, server.base_url)
        result = await ai.get_most_relevant_parts_by_transcript(
            _make_transcript(5 * 60),
            source_title="CORE $1 VS $10,000 RESTAURANTS IN JAPAN",
            creator_name="MrBeast",
            source_description="We compared the cheapest and priciest food in Japan.",
            scene_cut_times=[30.0, 95.0],
        )

    assert result.most_relevant_segments
    bodies = [r["body"] for r in server.requests]
    assert len(bodies) == 1
    body = bodies[0]
    # The single call IS the final pass: premium effort is in the body.
    assert body.get("reasoning_effort") == "high"
    text = _body_text(body)
    # Rubric + few-shot ride in the system prompt.
    assert "HARD GATES FIRST" in text
    assert "700-year-old tree" in text
    # Source context + scene hints ride in the user prompt.
    assert "Source video context" in text
    assert "CORE $1 VS $10,000 RESTAURANTS IN JAPAN" in text
    assert "Scene boundaries" in text and "01:35" in text
    # The output schema now carries the category field.
    assert '"category"' in text


async def test_long_video_volume_bodies_stay_default_rerank_is_premium(monkeypatch):
    with FakeLLMServer(
        lambda body, count: (200, {}, success_payload(body))
    ) as server:
        _configure_gateway(monkeypatch, server.base_url)
        result = await ai.get_most_relevant_parts_by_transcript(
            _make_transcript(40 * 60)
        )

    assert result.most_relevant_segments
    bodies = [r["body"] for r in server.requests]
    rerank_bodies = [
        b for b in bodies if '"ranking"' in json.dumps(b.get("tools") or [])
    ]
    analysis_bodies = [b for b in bodies if b not in rerank_bodies]
    assert rerank_bodies, "the multi-window flow must end in a rerank call"
    assert len(analysis_bodies) >= 2
    # Volume scans: no reasoning_effort anywhere in the body.
    for body in analysis_bodies:
        assert "reasoning_effort" not in _body_text(body)
    # The final rerank — the shipped slate's judge — thinks at high.
    for body in rerank_bodies:
        assert body.get("reasoning_effort") == "high"
