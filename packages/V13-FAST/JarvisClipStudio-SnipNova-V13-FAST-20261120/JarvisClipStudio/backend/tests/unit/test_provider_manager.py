"""Local-first provider manager: classification, cooldowns, selection, accounting."""

import asyncio
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from pydantic_ai.exceptions import ModelHTTPError

from src import ai, provider_manager as pm
from src.provider_manager import (
    FailureClass,
    LocalProviderUnavailable,
    ProviderManager,
)
from src.repositories import provider_usage_repository as repo

FIXED_NOW = datetime(2026, 11, 8, 10, 30, tzinfo=timezone.utc).timestamp()
CLOUD = ["openai:clip-fast", "openai:clip-assured", "openai:local-qwen"]


def make_manager() -> ProviderManager:
    return ProviderManager(now_fn=lambda: FIXED_NOW)


def http_error(status: int, body=None) -> ModelHTTPError:
    return ModelHTTPError(status, "some-model", body=body)


# ---------------------------------------------------------------------------
# Failure classification
# ---------------------------------------------------------------------------


class TestClassifyFailure:
    def test_plain_429_is_rate_limited(self):
        error = http_error(429, {"error": {"message": "Rate limit reached"}})
        assert pm.classify_failure(error) is FailureClass.RATE_LIMITED

    def test_413_is_rate_limited(self):
        assert pm.classify_failure(http_error(413, "too large")) is FailureClass.RATE_LIMITED

    def test_429_with_insufficient_quota_is_quota_exhausted(self):
        error = http_error(
            429,
            {"error": {"code": "insufficient_quota", "message": "You exceeded your current quota"}},
        )
        assert pm.classify_failure(error) is FailureClass.QUOTA_EXHAUSTED

    def test_429_with_tokens_per_day_wording_is_quota_exhausted(self):
        error = http_error(429, "Rate limit reached: tokens per day (TPD) limit hit")
        assert pm.classify_failure(error) is FailureClass.QUOTA_EXHAUSTED

    def test_401_and_403_are_auth(self):
        assert pm.classify_failure(http_error(401, "bad key")) is FailureClass.AUTH
        assert pm.classify_failure(http_error(403, "forbidden")) is FailureClass.AUTH

    def test_5xx_and_404_are_unavailable(self):
        assert pm.classify_failure(http_error(503, "down")) is FailureClass.UNAVAILABLE
        assert pm.classify_failure(http_error(404, "model not found")) is FailureClass.UNAVAILABLE

    def test_transport_errors_are_unavailable(self):
        assert pm.classify_failure(httpx.ConnectError("refused")) is FailureClass.UNAVAILABLE
        assert pm.classify_failure(httpx.ReadTimeout("slow")) is FailureClass.UNAVAILABLE

    def test_wrapped_error_chain_is_walked(self):
        wrapped = RuntimeError("outer")
        wrapped.__cause__ = http_error(429, {"error": {"message": "slow down"}})
        assert pm.classify_failure(wrapped) is FailureClass.RATE_LIMITED

    def test_other_errors_are_unknown(self):
        assert pm.classify_failure(ValueError("nope")) is FailureClass.UNKNOWN


# ---------------------------------------------------------------------------
# Cooldown registry
# ---------------------------------------------------------------------------


class TestCooldowns:
    def test_rate_limit_cooldown_is_two_minutes_then_doubles(self):
        manager = make_manager()
        manager.record_failure("openai:a", failure_class=FailureClass.RATE_LIMITED)
        record = manager.cooldown_record("openai:a")
        assert record.cooldown_until - FIXED_NOW == 120
        manager.record_failure("openai:a", failure_class=FailureClass.RATE_LIMITED)
        record = manager.cooldown_record("openai:a")
        assert record.cooldown_until - FIXED_NOW == 240
        assert record.consecutive_failures == 2

    def test_quota_cooldown_runs_to_next_utc_midnight(self):
        manager = make_manager()
        manager.record_failure("openai:a", failure_class=FailureClass.QUOTA_EXHAUSTED)
        record = manager.cooldown_record("openai:a")
        expected = (datetime(2026, 11, 8, tzinfo=timezone.utc) + timedelta(days=1)).timestamp()
        assert record.cooldown_until == expected

    def test_success_clears_cooldown(self):
        manager = make_manager()
        manager.record_failure("openai:a", failure_class=FailureClass.RATE_LIMITED)
        assert not manager.is_available("openai:a")
        manager.record_success("openai:a")
        assert manager.is_available("openai:a")
        assert manager.cooldown_record("openai:a") is None

    def test_record_failure_classifies_the_error(self):
        manager = make_manager()
        result = manager.record_failure("openai:a", error=http_error(503, "down"))
        assert result is FailureClass.UNAVAILABLE
        assert not manager.is_available("openai:a")


# ---------------------------------------------------------------------------
# Selection
# ---------------------------------------------------------------------------


class TestSelection:
    def test_auto_hard_preserves_configured_order(self):
        manager = make_manager()
        assert manager.select_models(
            CLOUD, complexity="hard", mode="auto", local_healthy=True
        ) == CLOUD

    def test_auto_routine_prefers_local_when_healthy(self):
        manager = make_manager()
        selected = manager.select_models(
            CLOUD, complexity="routine", mode="auto", local_healthy=True
        )
        assert selected[0] == "openai:local-qwen"

    def test_auto_routine_skips_local_when_unhealthy(self):
        manager = make_manager()
        selected = manager.select_models(
            CLOUD, complexity="routine", mode="auto", local_healthy=False
        )
        assert "openai:local-qwen" not in selected
        assert selected == ["openai:clip-fast", "openai:clip-assured"]

    def test_cooling_alias_is_skipped(self):
        manager = make_manager()
        manager.record_failure("openai:clip-fast", failure_class=FailureClass.RATE_LIMITED)
        selected = manager.select_models(
            CLOUD, complexity="hard", mode="auto", local_healthy=True
        )
        assert selected == ["openai:clip-assured", "openai:local-qwen"]

    def test_all_cooling_returns_configured_order_not_empty(self):
        manager = make_manager()
        for alias in CLOUD:
            manager.record_failure(alias, failure_class=FailureClass.RATE_LIMITED)
        selected = manager.select_models(
            CLOUD, complexity="hard", mode="auto", local_healthy=True
        )
        assert selected == CLOUD

    def test_local_first_puts_local_first_even_for_hard_work(self):
        manager = make_manager()
        selected = manager.select_models(
            CLOUD, complexity="hard", mode="local-first", local_healthy=True
        )
        assert selected[0] == "openai:local-qwen"

    def test_local_only_returns_only_local(self):
        manager = make_manager()
        selected = manager.select_models(
            CLOUD, complexity="hard", mode="local-only", local_healthy=True
        )
        assert selected == ["openai:local-qwen"]

    def test_local_only_with_local_down_raises_plain_words(self):
        manager = make_manager()
        with pytest.raises(LocalProviderUnavailable) as excinfo:
            manager.select_models(
                CLOUD, complexity="routine", mode="local-only", local_healthy=False
            )
        message = str(excinfo.value)
        assert "local-only" in message and "Ollama" in message

    def test_dedupes_and_drops_empties(self):
        manager = make_manager()
        selected = manager.select_models(
            ["openai:a", "", "openai:a", "openai:b"],
            complexity="hard",
            mode="auto",
        )
        assert selected == ["openai:a", "openai:b"]

    def test_direct_ollama_names_are_local(self):
        assert pm.is_local_alias("ollama:qwen3:8b")
        assert pm.is_local_alias("openai:local-qwen")
        assert not pm.is_local_alias("openai:clip-fast")
        assert not pm.is_local_alias("groq:openai/gpt-oss-120b")


# ---------------------------------------------------------------------------
# Config-driven helpers
# ---------------------------------------------------------------------------


def fake_config(**overrides):
    config = SimpleNamespace(
        llm="openai:clip-fast",
        analysis_models=["openai:clip-fast", "openai:clip-assured"],
        provider_mode="auto",
        local_llm="openai:local-qwen",
        provider_manager_enabled=True,
    )
    for key, value in overrides.items():
        setattr(config, key, value)
    return config


@pytest.fixture(autouse=True)
def fresh_manager():
    pm.reset_manager_for_tests()
    yield
    pm.reset_manager_for_tests()


class TestConfigHelpers:
    def test_analysis_selection_appends_local_as_final_fallback(self):
        selected = pm.select_analysis_models(
            fake_config(), window_count=3, local_healthy=True
        )
        assert selected == ["openai:clip-fast", "openai:clip-assured", "openai:local-qwen"]

    def test_analysis_selection_none_when_nothing_to_reroute(self):
        config = fake_config(analysis_models=[])
        assert pm.select_analysis_models(config, window_count=4, local_healthy=False) is None

    def test_analysis_selection_skips_cooled_alias(self):
        pm.get_manager().record_failure(
            "openai:clip-fast", failure_class=FailureClass.QUOTA_EXHAUSTED
        )
        selected = pm.select_analysis_models(
            fake_config(), window_count=3, local_healthy=True
        )
        assert selected[0] == "openai:clip-assured"

    def test_classify_prefers_local_for_routine_call(self):
        assert pm.select_classify_model(fake_config(), local_healthy=True) == "openai:local-qwen"

    def test_classify_uses_configured_llm_when_local_down(self):
        assert pm.select_classify_model(fake_config(), local_healthy=False) == "openai:clip-fast"

    def test_classify_local_only_down_raises(self):
        config = fake_config(provider_mode="local-only")
        pm.get_manager().note_local_health("openai:local-qwen", False)
        with pytest.raises(LocalProviderUnavailable):
            pm.select_classify_model(config)


# ---------------------------------------------------------------------------
# Local health probe
# ---------------------------------------------------------------------------


class FakeProbeClient:
    def __init__(self, status_code=200, error=None):
        self.status_code = status_code
        self.error = error
        self.calls = 0

    async def get(self, url):
        self.calls += 1
        if self.error is not None:
            raise self.error
        return SimpleNamespace(status_code=self.status_code)


class TestLocalHealthProbe:
    async def test_probe_success_and_cache(self):
        manager = make_manager()
        client = FakeProbeClient(200)
        assert await manager.probe_local_health("openai:local-qwen", "http://x/v1", client=client)
        assert await manager.probe_local_health("openai:local-qwen", "http://x/v1", client=client)
        assert client.calls == 1  # second call served from the 60s cache

    async def test_probe_failure_marks_unhealthy(self):
        manager = make_manager()
        client = FakeProbeClient(error=httpx.ConnectError("refused"))
        assert not await manager.probe_local_health("openai:local-qwen", "http://x/v1", client=client)
        assert manager.local_health("openai:local-qwen") is False


# ---------------------------------------------------------------------------
# Token accounting at the agent-run seam
# ---------------------------------------------------------------------------


def result_with_usage(prompt_tokens, completion_tokens):
    usage = SimpleNamespace(input_tokens=prompt_tokens, output_tokens=completion_tokens)
    return SimpleNamespace(output="done", usage=lambda: usage)


class TestRunRecorded:
    async def test_usage_is_recorded_exactly(self):
        manager = make_manager()
        agent = SimpleNamespace(run=AsyncMock(return_value=result_with_usage(120, 30)))
        with pm.task_usage_scope("task-1"):
            result = await pm.run_recorded(
                agent, "prompt", alias="openai:a", purpose="analysis", manager=manager
            )
        assert result.output == "done"
        view = manager.usage_for_task("task-1")
        totals = view["by_alias"]["openai:a"]
        assert totals.calls == 1
        assert totals.prompt_tokens == 120
        assert totals.completion_tokens == 30
        assert totals.total_tokens == 150
        assert totals.unknown_usage_calls == 0

    async def test_missing_usage_counts_call_without_inventing_tokens(self):
        manager = make_manager()
        agent = SimpleNamespace(run=AsyncMock(return_value=SimpleNamespace(output="done")))
        await pm.run_recorded(agent, "p", alias="openai:a", purpose="analysis", manager=manager)
        totals = manager.usage_for_task(None)["by_alias"]["openai:a"]
        assert totals.calls == 1
        assert totals.total_tokens == 0
        assert totals.unknown_usage_calls == 1

    async def test_failure_is_classified_and_cools_the_alias(self):
        manager = make_manager()
        agent = SimpleNamespace(
            run=AsyncMock(side_effect=http_error(429, {"error": {"message": "slow down"}}))
        )
        with pytest.raises(ModelHTTPError):
            await pm.run_recorded(
                agent, "p", alias="openai:a", purpose="analysis", manager=manager
            )
        record = manager.cooldown_record("openai:a")
        assert record is not None
        assert record.failure_class is FailureClass.RATE_LIMITED

    async def test_proxy_delegates_attributes_and_records(self):
        manager = make_manager()
        inner = SimpleNamespace(
            run=AsyncMock(return_value=result_with_usage(5, 7)), model="inner-model"
        )
        proxy = pm.ManagedAgentProxy(
            inner, alias="openai:a", purpose="classify", manager=manager
        )
        assert proxy.model == "inner-model"
        await proxy.run("p")
        totals = manager.usage_for_task(None)["by_alias"]["openai:a"]
        assert totals.total_tokens == 12


# ---------------------------------------------------------------------------
# Persistence: repository shaping + manager write-through / warm-up
# ---------------------------------------------------------------------------


class FakeMappings:
    def __init__(self, rows):
        self._rows = rows

    def all(self):
        return self._rows


class FakeResult:
    def __init__(self, rows):
        self._rows = rows

    def mappings(self):
        return FakeMappings(self._rows)


class FakeSession:
    def __init__(self, rows=None):
        self.rows = rows or []
        self.calls = []

    async def execute(self, statement, params=None):
        self.calls.append((str(statement), params))
        return FakeResult(self.rows)

    async def commit(self):
        pass


class TestRepository:
    async def test_record_usage_computes_total_and_marks_known(self):
        session = FakeSession()
        await repo.record_token_usage(
            session,
            alias="openai:a",
            purpose="analysis",
            task_id="t1",
            prompt_tokens=10,
            completion_tokens=4,
            usage_known=True,
        )
        _sql, params = session.calls[0]
        assert params["total_tokens"] == 14
        assert params["usage_known"] is True

    async def test_record_usage_unknown_keeps_tokens_null(self):
        session = FakeSession()
        await repo.record_token_usage(
            session,
            alias="openai:a",
            purpose="analysis",
            task_id="t1",
            prompt_tokens=None,
            completion_tokens=None,
            usage_known=False,
        )
        _sql, params = session.calls[0]
        assert params["total_tokens"] is None
        assert params["prompt_tokens"] is None

    async def test_task_usage_aggregation_shape(self):
        rows = [
            {
                "alias": "openai:a", "purpose": "analysis", "calls": 3,
                "prompt_tokens": 30, "completion_tokens": 12, "total_tokens": 42,
                "unknown_usage_calls": 1,
            },
            {
                "alias": "openai:a", "purpose": "classify", "calls": 1,
                "prompt_tokens": 5, "completion_tokens": 2, "total_tokens": 7,
                "unknown_usage_calls": 0,
            },
        ]
        session = FakeSession(rows=rows)
        usage = await repo.get_task_token_usage(session, "t1")
        assert usage["totals"] == {
            "calls": 4,
            "prompt_tokens": 35,
            "completion_tokens": 14,
            "total_tokens": 49,
            "unknown_usage_calls": 1,
        }
        assert usage["by_alias"][0]["alias"] == "openai:a"
        assert len(usage["by_alias"][0]["purposes"]) == 2

    async def test_load_cooldowns_parses_iso_and_datetime(self):
        rows = [
            {
                "alias": "openai:a", "failure_class": "rate_limited",
                "cooldown_until": "2026-11-08T12:00:00+00:00",
                "consecutive_failures": 2, "last_error": "ModelHTTPError",
            },
            {
                "alias": "openai:b", "failure_class": "unavailable",
                "cooldown_until": datetime(2026, 11, 8, 13, 0, tzinfo=timezone.utc),
                "consecutive_failures": 1, "last_error": "ModelHTTPError",
            },
        ]
        session = FakeSession(rows=rows)
        loaded = await repo.load_cooldowns(session)
        assert loaded["openai:a"]["cooldown_until"] == datetime(
            2026, 11, 8, 12, 0, tzinfo=timezone.utc
        ).timestamp()
        assert loaded["openai:b"]["consecutive_failures"] == 1

    def test_migration_sql_creates_both_tables(self):
        sql_path = (
            Path(__file__).resolve().parents[2]
            / "src" / "migrations" / "sql" / "20261108_0004_provider_manager.sql"
        )
        connection = sqlite3.connect(":memory:")
        connection.executescript(sql_path.read_text())
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        assert "llm_token_usage" in tables
        assert "provider_cooldowns" in tables


class TestPersistenceWiring:
    async def test_write_through_and_warm_up_round_trip(self, monkeypatch):
        persisted = {}

        async def fake_upsert(db, **kwargs):
            persisted.update(kwargs)

        async def fake_load(db):
            return {
                "openai:cold": {
                    "failure_class": "rate_limited",
                    "cooldown_until": FIXED_NOW + 600,
                    "consecutive_failures": 1,
                    "last_error": "ModelHTTPError",
                }
            }

        monkeypatch.setattr(repo, "upsert_cooldown", fake_upsert)
        monkeypatch.setattr(repo, "load_cooldowns", fake_load)

        class FakeCtx:
            async def __aenter__(self):
                return FakeSession()

            async def __aexit__(self, *exc):
                return False

        manager = ProviderManager(
            now_fn=lambda: FIXED_NOW, session_factory=lambda: FakeCtx()
        )
        manager.record_failure("openai:a", failure_class=FailureClass.RATE_LIMITED)
        # Let the scheduled write-through run.
        for _ in range(5):
            await asyncio.sleep(0)
        assert persisted["alias"] == "openai:a"
        assert persisted["failure_class"] == "rate_limited"

        await manager.warm_up()
        assert not manager.is_available("openai:cold")


# ---------------------------------------------------------------------------
# Kill-switch guard: disabled manager = today's behaviour, exactly
# ---------------------------------------------------------------------------


def ollama_config(**overrides):
    config = SimpleNamespace(
        llm="ollama:qwen3:8b",
        analysis_models=[],
        provider_mode="auto",
        local_llm="openai:local-qwen",
        provider_manager_enabled=True,
        openai_api_key=None,
        openrouter_api_key=None,
        openrouter_fallback_model="openai/gpt-6-luna",
        google_api_key=None,
        anthropic_api_key=None,
        ollama_base_url=None,
        ollama_api_key=None,
        as_runtime_settings=lambda: {},
        resolve_ollama_base_url=lambda: "http://localhost:11434/v1",
        max_clips=10,
        transcript_analysis_max_tokens=7000,
        analysis_min_interval_sec=0.0,
        analysis_tpm=8000,
    )
    for key, value in overrides.items():
        setattr(config, key, value)
    return config


class TestDisabledGuard:
    """The kill-switch guard: disabled = the exact pre-manager behaviour.

    Agent construction is stubbed (building a real SDK client is covered by
    other suites); what is guarded here is the wrap/no-wrap decision and the
    agent cache identity in both modes.
    """

    def _stub_agent_build(self, monkeypatch, config):
        marker = SimpleNamespace(run=AsyncMock())
        monkeypatch.setattr(ai, "get_config", lambda: config)
        monkeypatch.setattr(ai, "_transcript_agent", None)
        monkeypatch.setattr(ai, "_transcript_agent_signature", None)
        monkeypatch.setattr(ai, "_create_transcript_agent", lambda *a, **k: marker)
        return marker

    def test_disabled_manager_returns_the_raw_cached_agent(self, monkeypatch):
        config = ollama_config(provider_manager_enabled=False)
        marker = self._stub_agent_build(monkeypatch, config)
        first = ai.get_transcript_agent()
        assert first is marker
        assert not isinstance(first, pm.ManagedAgentProxy)
        assert ai.get_transcript_agent() is first

    def test_enabled_manager_wraps_once_and_keeps_identity(self, monkeypatch):
        config = ollama_config(provider_manager_enabled=True)
        marker = self._stub_agent_build(monkeypatch, config)
        first = ai.get_transcript_agent()
        assert isinstance(first, pm.ManagedAgentProxy)
        assert first._agent is marker
        assert ai.get_transcript_agent() is first
