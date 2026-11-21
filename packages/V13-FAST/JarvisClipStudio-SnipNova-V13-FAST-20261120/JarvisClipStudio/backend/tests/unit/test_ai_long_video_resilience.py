"""Long-video analysis resilience, proven against a fake LLM server.

The fake server (``fake_llm_server.py``) stands in for the LiteLLM gateway
and speaks the OpenAI chat API, so these tests exercise the real HTTP path:
chunking, pacing, backoff, failover and per-window checkpointing.

Covered failure shapes, all seen on a real 40-minute task:
- every cloud alias answers 429 until the local model takes over;
- a burst of 429s clears after paced retries (no failover needed);
- the next configured model takes over a rate-limited chunk first;
- a hard 400 fails fast, exactly as before (no pacing, no failover);
- the gateway is dead (connection refused): chunks fail over to the
  direct local route, and if that is dead too the surfaced error says
  "gateway unreachable" in plain words, quickly;
- a run killed mid-analysis resumes from checkpointed windows;
- a short video issues exactly the request it issued before this work
  (canonical hash captured from the pre-fix code).
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import time
from types import SimpleNamespace

import pytest

from fake_llm_server import (
    FakeLLMServer,
    always,
    error_payload,
    first_line_label,
    hang_first_lines,
    per_model,
    success_payload,
)
from src import ai, provider_manager

# Canonical request hash of the pre-fix short-video path, captured with
# this same fake server against commit 0c23fb4 (one request, model
# clip-fast). Any change to the single-chunk request shape breaks it.
# Re-captured on the qm-retention branch: the only request-body change
# is the Trend-Radar D1 hook-title guidance (problem / curiosity_gap
# bullets in the HOOK TITLES prompt section) — the shape (one request,
# same fields) is unchanged.
# Re-captured 2026-11-11 on fix-request-shape-golden: the titles fix
# (3b34313) added THIRD PERSON ONLY hook-title instructions to the
# analysis prompt, the JSON-only prompt and the hook_title field
# description. The full request body was reconstructed at both pins
# and diffed; every differing byte is one of those additions (proof:
# research_notes/sprint-7day/REPIN_REPORT.md). The shape (one request,
# same fields) is unchanged.
# Re-captured 2026-11-11 on feature-brain-upgrade: the brain lane's
# changes, each reconstructed and diffed byte-for-byte at both pins
# (proof: research_notes/sprint-7day/BRAIN_UPGRADE_REPORT.md) — a new
# top-level "reasoning_effort": "high" (the single-window call is the
# premium pass), the gated /100 rubric + hook calibration + gold
# few-shot replacing the old VIRALITY SCORING section, and the new
# virality "category" field in the output contract, the JSON-only key
# list and the tool schema. The shape is still one request; the
# source-context and scene-boundary sections are absent here because
# this scenario passes no source metadata.
GOLDEN_SHORT_VIDEO_SHA256 = (
    "9e4756151b9bc48322ec253dc97448323cb99525e93b66eed37348a8af466105"
)


def make_transcript(total_seconds: int, line_seconds: int = 10) -> str:
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
    # Agents are cached per model name and the cache key does not include
    # OPENAI_BASE_URL; a fresh worker process would rebuild them.
    ai._transcript_agent = None
    ai._transcript_agent_signature = None
    ai._transcript_agents_by_model.clear()
    ai._rerank_agent = None
    ai._rerank_agent_signature = None


@pytest.fixture(autouse=True)
def _reset_agent_caches():
    _clear_agent_caches()
    # The provider manager (local-first lane) keeps a process-global
    # cooldown registry by design; these scenarios assume a cold registry,
    # so reset it the same way the manager's own tests do.
    provider_manager.reset_manager_for_tests()
    yield
    _clear_agent_caches()
    provider_manager.reset_manager_for_tests()


def configure_gateway(monkeypatch, base_url: str, **overrides: str) -> None:
    env = {
        "LLM": "openai:clip-fast",
        "OPENAI_API_KEY": "shim-key",
        "OPENAI_BASE_URL": base_url,
        "ANALYSIS_MODELS": "openai:clip-fast,openai:clip-assured",
        "ANALYSIS_FALLBACK_MODEL": "openai:local-qwen",
        "ANALYSIS_DIRECT_FALLBACK_MODEL": "openai:local-qwen",
        # The sandbox's ambient no_proxy lists bracketed IPv6 hosts that
        # this httpx build cannot parse; pin it to plain loopback names.
        "no_proxy": "localhost,127.0.0.1",
        "NO_PROXY": "localhost,127.0.0.1",
    }
    env.update(overrides)
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    # Unset means auto-pacing; tests opt into or out of pacing via env.
    if "ANALYSIS_MIN_INTERVAL_SEC" not in overrides:
        monkeypatch.delenv("ANALYSIS_MIN_INTERVAL_SEC", raising=False)


def fast_backoff(monkeypatch) -> None:
    """Shrink the rate-limit backoff base so exhaustion takes milliseconds."""
    monkeypatch.setattr(ai, "ANALYSIS_RATE_LIMIT_INITIAL_DELAY_SECONDS", 0.05)


async def test_always_429_fails_over_to_the_local_model(monkeypatch):
    fast_backoff(monkeypatch)
    # Retry-After: 0.1 keeps both the SDK's internal retries and the app's
    # header-honouring backoff fast; the retry SHAPE is unchanged.
    behaviour = per_model(
        {
            "clip-fast": always(429, retry_after="0.1"),
            "clip-assured": always(429, retry_after="0.1"),
            "local-qwen": lambda body, count: (200, {}, success_payload(body)),
        }
    )
    with FakeLLMServer(behaviour) as server:
        # Pacing is proven separately; here it would only slow exhaustion.
        configure_gateway(
            monkeypatch, server.base_url, ANALYSIS_MIN_INTERVAL_SEC="0"
        )
        result = await ai.get_most_relevant_parts_by_transcript(
            make_transcript(40 * 60)
        )

    assert result.most_relevant_segments
    fingerprints = server.analysis_fingerprints()
    # Both long-form windows were rescued by the local model.
    assert "local-qwen@00:00" in fingerprints
    assert "local-qwen@18:00" in fingerprints


async def test_next_configured_model_takes_over_before_the_local_one(monkeypatch):
    fast_backoff(monkeypatch)
    behaviour = per_model(
        {
            "clip-fast": always(429, retry_after="0.1"),
            "clip-assured": lambda body, count: (200, {}, success_payload(body)),
            "local-qwen": lambda body, count: (200, {}, success_payload(body)),
        }
    )
    with FakeLLMServer(behaviour) as server:
        configure_gateway(
            monkeypatch, server.base_url, ANALYSIS_MIN_INTERVAL_SEC="0"
        )
        # 20 minutes: standard route, windows rotate clip-fast/clip-assured.
        result = await ai.get_most_relevant_parts_by_transcript(
            make_transcript(20 * 60)
        )

    assert result.most_relevant_segments
    fingerprints = server.analysis_fingerprints()
    # Window 1 started on clip-fast, exhausted it, and clip-assured (the
    # next configured model) took the chunk over.
    assert "clip-assured@00:00" in fingerprints
    # The local last resort was never needed.
    assert not any(fp.startswith("local-qwen@") for fp in fingerprints)


async def test_hanging_primary_times_out_and_fails_over(monkeypatch):
    # Real case, 2026-11-08: a 9-minute video's analysis died after one
    # 600 s stall on the primary model because the timeout surfaced as a
    # bare RuntimeError, which the failover layer did not treat as a
    # provider failure — no other model was ever tried. A provider that
    # accepts the request and then goes silent must hand the chunk over.
    monkeypatch.setattr(ai, "TRANSCRIPT_ANALYSIS_TIMEOUT_SECONDS", 1)
    behaviour = per_model(
        {
            "clip-fast": hang_first_lines({"00:00"}, 30),
            "clip-assured": lambda body, count: (200, {}, success_payload(body)),
            "local-qwen": lambda body, count: (200, {}, success_payload(body)),
        }
    )
    with FakeLLMServer(behaviour) as server:
        configure_gateway(
            monkeypatch, server.base_url, ANALYSIS_MIN_INTERVAL_SEC="0"
        )
        result = await ai.get_most_relevant_parts_by_transcript(
            make_transcript(9 * 60)
        )

    assert result.most_relevant_segments
    fingerprints = server.analysis_fingerprints()
    # The next configured model rescued the chunk after the timeout.
    assert "clip-assured@00:00" in fingerprints
    assert not any(fp.startswith("local-qwen@") for fp in fingerprints)


async def test_windowed_429_recovers_with_pacing_and_no_failover(monkeypatch):
    fast_backoff(monkeypatch)

    def behaviour(body, count):
        if count <= 3:
            return 429, {"Retry-After": "0.1"}, error_payload(429, "shim burst")
        return 200, {}, success_payload(body)
    with FakeLLMServer(behaviour) as server:
        configure_gateway(
            monkeypatch,
            server.base_url,
            # Explicit pacing floor: this test measures pacing itself.
            # (Auto-engagement is covered by the pacer decision tests.)
            ANALYSIS_MIN_INTERVAL_SEC="0.4",
            # Huge bucket: the floor alone governs the spacing, so the test
            # measures pacing, not token arithmetic.
            ANALYSIS_LONGFORM_TPM="100000000",
        )
        result = await ai.get_most_relevant_parts_by_transcript(
            make_transcript(40 * 60)
        )

    assert result.most_relevant_segments
    # Pacing + backoff absorbed the burst on the primary route: the local
    # fallback never saw a request.
    assert "local-qwen" not in server.models_seen()
    # The two windows' first attempts on the same model start at least the
    # pacing floor (0.4s) apart. (SDK-internal retries share a window's
    # fingerprint, so first occurrences mark attempt starts.)
    firsts: dict[str, float] = {}
    for record in server.requests:
        if record["model"] != "clip-fast":
            continue
        key = first_line_label(record["body"])
        if key and key not in firsts:
            firsts[key] = record["t"]
    assert "00:00" in firsts and "18:00" in firsts
    assert abs(firsts["18:00"] - firsts["00:00"]) >= 0.35


async def test_hard_error_fails_fast_without_failover_or_delays(monkeypatch):
    behaviour = per_model({"clip-fast": always(400)})
    with FakeLLMServer(behaviour) as server:
        # Pacing off: this test isolates hard-error behaviour.
        configure_gateway(
            monkeypatch, server.base_url, ANALYSIS_MIN_INTERVAL_SEC="0"
        )
        started = time.monotonic()
        with pytest.raises(RuntimeError) as caught:
            await ai.get_most_relevant_parts_by_transcript(make_transcript(40 * 60))
        elapsed = time.monotonic() - started

    assert "Transcript analysis failed" in str(caught.value)
    # One attempt per window, no other route was tried, no pacing/backoff
    # delay was slept through. (The elapsed bound is generous because the
    # suite shares its machine; the discriminating asserts are above.)
    assert set(server.models_seen()) == {"clip-fast"}
    assert len(server.requests) <= 6
    assert elapsed < 20


def test_failover_groups_openai_and_openai_chat_spellings():
    """A dead gateway on one OpenAI spelling must mark the other unusable
    too: the deprecated openai: prefix and the canonical openai-chat:
    prefix name the same provider (the LiteLLM gateway)."""
    failover = ai._AnalysisFailover(
        ["openai:clip-fast", "openai-chat:clip-assured"],
        "openai-chat:local-qwen",
        "ollama:qwen3:8b",
    )

    assert ai._AnalysisFailover._provider("openai:clip-fast") == "openai"
    assert ai._AnalysisFailover._provider("openai-chat:clip-fast") == "openai"
    assert ai._AnalysisFailover._provider("groq:openai/gpt-oss-120b") == "groq"

    failover._note_failure(
        "openai-chat:local-qwen", ai.AnalysisGatewayUnreachableError("down")
    )
    assert not failover._usable("openai:clip-fast")
    assert not failover._usable("openai-chat:clip-assured")
    assert failover._usable("ollama:qwen3:8b")


async def test_dead_gateway_fails_over_to_the_direct_local_route(monkeypatch):
    monkeypatch.setattr(ai, "ANALYSIS_CONNECTION_RETRY_DELAY_SECONDS", 0.2)
    with FakeLLMServer(
        lambda body, count: (200, {}, success_payload(body))
    ) as server:
        configure_gateway(
            monkeypatch,
            # Nothing listens on the gateway port: connection refused.
            "http://127.0.0.1:9/v1",
            ANALYSIS_MIN_INTERVAL_SEC="0",
            ANALYSIS_DIRECT_FALLBACK_MODEL="ollama:qwen3:8b",
            OLLAMA_BASE_URL=server.base_url,
        )
        started = time.monotonic()
        result = await ai.get_most_relevant_parts_by_transcript(
            make_transcript(40 * 60)
        )
        elapsed = time.monotonic() - started

    assert result.most_relevant_segments
    # Every rescued request went to the direct local route, which does not
    # traverse the dead gateway.
    assert set(server.models_seen()) == {"qwen3:8b"}
    assert elapsed < 90


async def test_dead_gateway_and_dead_local_surface_a_plain_words_error(
    monkeypatch,
):
    monkeypatch.setattr(ai, "ANALYSIS_CONNECTION_RETRY_DELAY_SECONDS", 0.2)
    configure_gateway(
        monkeypatch,
        "http://127.0.0.1:9/v1",
        ANALYSIS_MIN_INTERVAL_SEC="0",
        ANALYSIS_DIRECT_FALLBACK_MODEL="ollama:qwen3:8b",
        OLLAMA_BASE_URL="http://127.0.0.1:9/v1",
    )
    started = time.monotonic()
    with pytest.raises(ai.AnalysisGatewayUnreachableError) as caught:
        await ai.get_most_relevant_parts_by_transcript(make_transcript(40 * 60))
    elapsed = time.monotonic() - started

    # The error names the real problem (not a rate limit, not a generic
    # failure) and detection is fast: no retry ladder burned on a corpse.
    assert "could not reach the AI gateway" in str(caught.value)
    assert "rate-limited" not in str(caught.value)
    assert elapsed < 60


async def test_resume_reuses_checkpointed_windows(monkeypatch):
    transcript = make_transcript(40 * 60)
    blob: dict = {}

    async def collect(key, index, payload):
        windows = dict(blob.get("windows") or {})
        windows[str(index)] = payload
        blob.update({"key": key, "windows": windows})

    # Run A: window 2 hangs; the run is killed after window 1 checkpointed.
    with FakeLLMServer(hang_first_lines({"18:00"}, 30)) as server_a:
        configure_gateway(monkeypatch, server_a.base_url)
        task = asyncio.create_task(
            ai.get_most_relevant_parts_by_transcript(
                transcript, on_window_complete=collect
            )
        )
        deadline = time.monotonic() + 20
        while "0" not in (blob.get("windows") or {}):
            assert time.monotonic() < deadline, "window 1 never checkpointed"
            await asyncio.sleep(0.05)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    assert blob.get("key")
    assert set((blob.get("windows") or {}).keys()) == {"0"}

    # Run B: the retried task must not re-request the checkpointed window.
    # A retry runs in a fresh worker invocation, so rebuild the agents
    # against the new gateway address (the agent cache key has no base URL).
    _clear_agent_caches()
    with FakeLLMServer(
        lambda body, count: (200, {}, success_payload(body))
    ) as server_b:
        configure_gateway(monkeypatch, server_b.base_url)
        result = await ai.get_most_relevant_parts_by_transcript(
            transcript, window_checkpoint=blob
        )

    assert result.most_relevant_segments
    fingerprints = server_b.analysis_fingerprints()
    assert not any(fp.endswith("@00:00") for fp in fingerprints)
    assert any(fp.endswith("@18:00") for fp in fingerprints)


def _pacer_for(explicit, *, chunks: int, tokens: int, tpm: int = 8000, floor=2.0):
    config = SimpleNamespace(
        analysis_min_interval_sec=explicit, analysis_pacing_floor_sec=floor
    )
    return ai._build_analysis_pacer(
        config, chunk_count=chunks, total_tokens=tokens, tpm=tpm
    )


def test_pacer_auto_engages_only_when_a_burst_could_overflow():
    # Multi-chunk, combined prompts bigger than the per-minute bucket: on.
    assert _pacer_for(None, chunks=4, tokens=12000).min_interval_seconds == 2.0
    # A single chunk is never paced, however large.
    assert _pacer_for(None, chunks=1, tokens=120000).min_interval_seconds == 0
    # Multi-chunk but comfortably inside the bucket: off.
    assert _pacer_for(None, chunks=3, tokens=5000).min_interval_seconds == 0
    # An explicit value always wins; an explicit 0 disables pacing.
    assert _pacer_for(0.25, chunks=1, tokens=10).min_interval_seconds == 0.25
    assert _pacer_for(0, chunks=9, tokens=999999).min_interval_seconds == 0


async def test_short_video_request_shape_is_unchanged(monkeypatch):
    with FakeLLMServer(
        lambda body, count: (200, {}, success_payload(body))
    ) as server:
        configure_gateway(monkeypatch, server.base_url)
        result = await ai.get_most_relevant_parts_by_transcript(
            make_transcript(5 * 60)
        )

    assert result.most_relevant_segments
    bodies = [r["body"] for r in server.requests]
    assert len(bodies) == 1
    digest = hashlib.sha256(
        json.dumps(bodies, sort_keys=True).encode()
    ).hexdigest()
    assert digest == GOLDEN_SHORT_VIDEO_SHA256
