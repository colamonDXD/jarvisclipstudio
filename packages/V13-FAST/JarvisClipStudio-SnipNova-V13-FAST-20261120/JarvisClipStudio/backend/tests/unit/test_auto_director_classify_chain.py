"""Candidate-chain tests for the P0 auto-niche fix (2026-11-10).

The shipped chain made exactly one classification attempt: the provider
manager's single pick. A build-time missing-key gate, a transport error,
or an output-contract slip on that one model silently turned EVERY auto
task into the podcast fallback. These tests pin the replacement chain:

- classification tries an ordered, de-duplicated candidate list
  (manager/config assembly, re-ordered strongest-first: cloud
  routes in assembled order, local routes at the tail),
- a failure on one candidate — build-time gate, transport, output
  contract — moves to the NEXT candidate,
- a valid JSON-text answer is accepted (the system prompt literally
  asks for one),
- the validated niche is still checked against AUTO_NICHE_VALUES.

The agent seam is stubbed per model: ``get_agent_for_model(model, text)``
is replaced with agents whose ``run`` behaves per the scenario. No live
provider is ever contacted.
"""

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

import src.auto_director.classify as classify_module
from src import provider_manager as pm
from src.auto_director.classify import classify_niche

MANAGER_PICK = "openai:local-qwen"
CONFIG_LLM = "openai:clip-fast"
ANALYSIS_MODEL = "openai:clip-assured"
FALLBACK_MODEL = "groq:openai/gpt-oss-120b"
DIRECT_FALLBACK_MODEL = "ollama:qwen3:8b"


def _config(**overrides):
    config = SimpleNamespace(
        llm=CONFIG_LLM,
        analysis_models=[ANALYSIS_MODEL],
        analysis_fallback_model=FALLBACK_MODEL,
        analysis_direct_fallback_model=DIRECT_FALLBACK_MODEL,
        provider_mode="auto",
        local_llm=MANAGER_PICK,
        provider_manager_enabled=True,
        openai_api_key="test-openai-key",
        openrouter_api_key=None,
        openrouter_fallback_model="anthropic/claude-haiku-4-5",
        google_api_key=None,
        anthropic_api_key=None,
        ollama_base_url="http://localhost:11434",
        ollama_api_key=None,
    )
    for key, value in overrides.items():
        setattr(config, key, value)
    return config


@pytest.fixture(autouse=True)
def fresh_manager(monkeypatch):
    # A bare singleton: persistence disabled (no session factory ->
    # _schedule_persist creates no tasks; a recording loop would race
    # pytest's per-test event loops).
    pm._manager = pm.ProviderManager()
    monkeypatch.setattr(
        pm.ProviderManager, "_schedule_persist", lambda self, coro: coro.close()
    )
    yield
    pm._manager = None


def _agent(behavior):
    """A stub agent: behavior is an exception to raise or a run result."""
    if isinstance(behavior, BaseException):
        run = AsyncMock(side_effect=behavior)
    else:
        run = AsyncMock(return_value=behavior)
    return SimpleNamespace(run=run)


def _output(niche, confidence=0.9, reasons=None):
    return SimpleNamespace(
        output=SimpleNamespace(
            niche=niche, confidence=confidence, reasons=reasons or ["stub reason"]
        )
    )


def _install_chain(monkeypatch, config, behaviors, *, manager_order=None):
    """Stub the classify seams: config, manager ordering, agent builds.

    ``behaviors`` maps (model, text_mode) to the stub agent behaviour:
    an exception instance means the BUILD fails with it (the
    missing-key gate shape) when text_mode is False, or the text agent
    build fails with it when True; anything else is the agent itself.
    A missing key means that build must never be attempted.
    ``manager_order`` replaces the manager's ordering when given.

    The module's default agent factory is pinned to a raising stub so
    classify_niche's patch-seam detection never mistakes the real
    factory for a substituted one.
    """
    monkeypatch.setattr(classify_module, "get_config", lambda: config)

    def _no_default_agent():
        raise AssertionError("default agent factory must not be used here")

    monkeypatch.setattr(
        classify_module, "get_niche_classification_agent", _no_default_agent
    )
    monkeypatch.setattr(
        classify_module.provider_manager, "manager_enabled", lambda cfg=None: True
    )
    if manager_order is not None:
        monkeypatch.setattr(
            classify_module.provider_manager,
            "select_classify_candidates",
            lambda cfg, local_healthy=None: list(manager_order),
        )

    def fake_build(model, *, text_output=False):
        key = (model, text_output)
        assert key in behaviors, f"unexpected agent build for {key}"
        behavior = behaviors[key]
        if isinstance(behavior, BaseException):
            raise behavior
        return behavior

    monkeypatch.setattr(classify_module, "get_agent_for_model", fake_build)
    monkeypatch.setattr(
        classify_module.provider_manager,
        "run_recorded",
        AsyncMock(side_effect=_passthrough_run),
    )
    return behaviors


async def _passthrough_run(agent, *args, **kwargs):
    return await agent.run(*args, **kwargs)


class TestCandidateOrdering:
    """The chain is re-ordered strongest-first (2026-11-11): cloud
    routes in their assembled relative order, local routes at the
    tail — classification is one small call and must not be locked
    by the weakest (local) model's first answer."""

    def test_full_ordered_list(self, monkeypatch):
        config = _config()
        candidates = classify_module._classify_candidates(config)
        assert candidates == [
            CONFIG_LLM,
            ANALYSIS_MODEL,
            FALLBACK_MODEL,
            MANAGER_PICK,
            DIRECT_FALLBACK_MODEL,
        ]

    def test_local_first_mode_still_puts_cloud_first(self, monkeypatch):
        """Luffy's deployment shape: PROVIDER_MODE=local-first made
        the manager return the local model first; the classify chain
        must nevertheless ask the cloud routes first and keep the
        local routes as the offline tail."""
        config = _config(provider_mode="local-first")
        candidates = classify_module._classify_candidates(config)
        assert candidates == [
            CONFIG_LLM,
            ANALYSIS_MODEL,
            FALLBACK_MODEL,
            MANAGER_PICK,
            DIRECT_FALLBACK_MODEL,
        ]
        assert candidates.index(FALLBACK_MODEL) < candidates.index(MANAGER_PICK)

    def test_cloud_relative_order_follows_the_manager(self, monkeypatch):
        """Multiple cloud candidates keep the manager's relative
        order among themselves (stable partition, no re-ranking)."""
        monkeypatch.setattr(
            classify_module.provider_manager,
            "select_classify_candidates",
            lambda cfg, local_healthy=None: [
                FALLBACK_MODEL,
                MANAGER_PICK,
                ANALYSIS_MODEL,
                CONFIG_LLM,
            ],
        )
        candidates = classify_module._classify_candidates(_config())
        assert candidates == [
            FALLBACK_MODEL,
            ANALYSIS_MODEL,
            CONFIG_LLM,
            MANAGER_PICK,
            DIRECT_FALLBACK_MODEL,
        ]

    def test_local_only_chain_is_unchanged(self, monkeypatch):
        """No cloud candidate anywhere in the assembly: the
        strongest-first partition is the identity — a local-only
        deployment's chain is byte-identical to before."""
        monkeypatch.setattr(
            classify_module.provider_manager,
            "select_classify_candidates",
            lambda cfg, local_healthy=None: [MANAGER_PICK],
        )
        config = _config(
            llm=MANAGER_PICK,
            analysis_models=[],
            analysis_fallback_model=MANAGER_PICK,
            analysis_direct_fallback_model=DIRECT_FALLBACK_MODEL,
        )
        assert classify_module._classify_candidates(config) == [
            MANAGER_PICK,
            DIRECT_FALLBACK_MODEL,
        ]

    def test_local_only_manager_disabled_is_unchanged(self, monkeypatch):
        monkeypatch.setattr(
            classify_module.provider_manager,
            "manager_enabled",
            lambda cfg=None: False,
        )
        config = _config(
            provider_manager_enabled=False,
            llm=MANAGER_PICK,
            analysis_models=[DIRECT_FALLBACK_MODEL],
            analysis_fallback_model=MANAGER_PICK,
            analysis_direct_fallback_model="",
        )
        assert classify_module._classify_candidates(config) == [
            MANAGER_PICK,
            DIRECT_FALLBACK_MODEL,
        ]

    def test_dedupes_and_drops_empty(self):
        config = _config(
            analysis_models=[CONFIG_LLM, ""],
            analysis_fallback_model="",
            analysis_direct_fallback_model=CONFIG_LLM,
        )
        assert classify_module._classify_candidates(config) == [
            CONFIG_LLM,
            MANAGER_PICK,
        ]

    def test_manager_disabled_uses_config_order(self, monkeypatch):
        config = _config(provider_manager_enabled=False)
        monkeypatch.setattr(
            classify_module.provider_manager,
            "manager_enabled",
            lambda cfg=None: False,
        )
        # Config order here is already cloud-first with the local
        # direct fallback at the tail, so the partition changes
        # nothing — the expectation is deliberately unchanged.
        assert classify_module._classify_candidates(config) == [
            CONFIG_LLM,
            ANALYSIS_MODEL,
            FALLBACK_MODEL,
            DIRECT_FALLBACK_MODEL,
        ]


class TestFallthrough:
    @pytest.mark.asyncio
    async def test_build_gate_failure_moves_to_next_candidate(self, monkeypatch):
        """The P0 headline: the head of the chain dies at agent BUILD
        time (missing key) and classification still succeeds on the
        next candidate instead of falling back. (The head is the
        configured cloud LLM since the strongest-first reorder; the
        manager's local pick heads nothing anymore.)"""
        behaviors = {
            (CONFIG_LLM, False): RuntimeError(
                "Selected LLM provider is OpenAI, but OPENAI_API_KEY is not set."
            ),
            (ANALYSIS_MODEL, False): _agent(_output("gaming", 0.91)),
            (FALLBACK_MODEL, False): _agent(_output("gaming", 0.5)),
            (MANAGER_PICK, False): _agent(_output("gaming", 0.5)),
            (DIRECT_FALLBACK_MODEL, False): _agent(_output("gaming", 0.5)),
        }
        _install_chain(monkeypatch, _config(), behaviors)

        result = await classify_niche(
            "ranked grind, new season drops tonight, let's gooo", []
        )

        assert result["niche"] == "gaming"
        assert result["is_fallback"] is False
        assert result["model_used"] == ANALYSIS_MODEL
        assert result["attempts"][0]["model"] == CONFIG_LLM
        assert result["attempts"][0]["stage"] == "build"
        # The build failure is recorded against the alias, so selection
        # can cool it down instead of repeating it forever.
        record = pm.get_manager().cooldown_record(CONFIG_LLM)
        assert record is not None

    @pytest.mark.asyncio
    async def test_transport_failure_moves_to_next_candidate(self, monkeypatch):
        behaviors = {
            (CONFIG_LLM, False): _agent(ConnectionError("gateway unreachable")),
            (CONFIG_LLM, True): _agent(ConnectionError("gateway unreachable")),
            (ANALYSIS_MODEL, False): _agent(_output("sports", 0.87)),
            (FALLBACK_MODEL, False): _agent(_output("sports", 0.5)),
            (MANAGER_PICK, False): _agent(_output("sports", 0.5)),
            (DIRECT_FALLBACK_MODEL, False): _agent(_output("sports", 0.5)),
        }
        _install_chain(monkeypatch, _config(), behaviors)

        result = await classify_niche("matchday vlog at the stadium", [])

        assert result["niche"] == "sports"
        assert result["model_used"] == ANALYSIS_MODEL
        assert result["attempts"][0]["stage"] == "run"

    @pytest.mark.asyncio
    async def test_gateway_400_on_first_candidate_moves_to_next(self, monkeypatch):
        """The live failure class from Luffy's worker log (task
        e4617280, 2026-11-10): a route dies with a gateway HTTP 400
        (litellm.BadRequestError — request shape rejected before the
        model answers). Then it was the local head of chain; since
        the strongest-first reorder the head is a cloud route — the
        chain must still try the next candidate, not fall back."""
        gateway_400 = RuntimeError(
            "status_code: 400, model_name: clip-fast, body: "
            "litellm.BadRequestError: OpenAIException - invalid message "
            "content type: <nil>"
        )
        behaviors = {
            (CONFIG_LLM, False): _agent(gateway_400),
            (CONFIG_LLM, True): _agent(gateway_400),
            (ANALYSIS_MODEL, False): _agent(_output("travel_vlog", 0.9)),
            (FALLBACK_MODEL, False): _agent(_output("travel_vlog", 0.5)),
            (MANAGER_PICK, False): _agent(_output("travel_vlog", 0.5)),
            (DIRECT_FALLBACK_MODEL, False): _agent(_output("travel_vlog", 0.5)),
        }
        _install_chain(monkeypatch, _config(), behaviors)

        result = await classify_niche(
            "eating our way through Tokyo's best restaurants", [],
            title="CORE $1 VS $10,000 RESTAURANTS IN JAPAN",
        )

        assert result["niche"] == "travel_vlog"
        assert result["is_fallback"] is False
        assert result["model_used"] == ANALYSIS_MODEL
        assert "400" in result["attempts"][0]["error"]

    @pytest.mark.asyncio
    async def test_chain_reaches_the_analysis_fallback_models(self, monkeypatch):
        behaviors = {
            (CONFIG_LLM, False): _agent(ConnectionError("down")),
            (ANALYSIS_MODEL, False): RuntimeError("gate"),
            (FALLBACK_MODEL, False): _agent(_output("finance_business", 0.83)),
            (MANAGER_PICK, False): _agent(_output("finance_business", 0.5)),
            (DIRECT_FALLBACK_MODEL, False): _agent(_output("finance_business", 0.5)),
        }
        _install_chain(monkeypatch, _config(), behaviors)

        result = await classify_niche("index funds and dividend talk", [])

        assert result["niche"] == "finance_business"
        assert result["model_used"] == FALLBACK_MODEL
        # One recorded failure per earlier candidate: transport,
        # transport + text-build probe for the transport failure,
        # and the build gate.
        assert [a["model"] for a in result["attempts"] if a["stage"] != "ok"] == [
            CONFIG_LLM,
            CONFIG_LLM,
            ANALYSIS_MODEL,
        ]

    @pytest.mark.asyncio
    async def test_all_candidates_fail_returns_marked_fallback(self, monkeypatch):
        all_models = (
            MANAGER_PICK,
            CONFIG_LLM,
            ANALYSIS_MODEL,
            FALLBACK_MODEL,
            DIRECT_FALLBACK_MODEL,
        )
        behaviors = {}
        for model in all_models:
            behaviors[(model, False)] = _agent(ConnectionError("down"))
            behaviors[(model, True)] = RuntimeError("text build unavailable")
        _install_chain(monkeypatch, _config(), behaviors)

        result = await classify_niche("some transcript", [])

        assert result["is_fallback"] is True
        assert result["niche"] == "podcast_news"
        assert result["confidence"] == 0.0
        assert result["model_used"] is None
        # Every candidate was genuinely tried before giving up, and
        # the chain record carries each candidate's errors.
        assert {a["model"] for a in result["attempts"]} == set(all_models)
        assert any("down" in (a["error"] or "") for a in result["attempts"])
        assert result["last_error"]


class TestJsonTextOutputContract:
    @pytest.mark.asyncio
    async def test_json_text_answer_classifies_successfully(self, monkeypatch):
        """RC-1: the prompt says 'Return valid JSON only'; a model that
        obeys literally and answers with JSON TEXT (no tool call) raises
        on the tool-contract attempt, then succeeds on the text retry."""
        payload = {
            "niche": "gaming",
            "confidence": 0.88,
            "reasons": ["Gameplay footage and gaming slang"],
        }
        behaviors = {
            (CONFIG_LLM, False): _agent(
                RuntimeError("model did not call the final_result tool")
            ),
            (CONFIG_LLM, True): _agent(
                SimpleNamespace(output=json.dumps(payload))
            ),
        }
        _install_chain(monkeypatch, _config(), behaviors)

        result = await classify_niche("let's play ranked, new map just dropped", [])

        assert result["niche"] == "gaming"
        assert result["confidence"] == 0.88
        assert result["is_fallback"] is False
        assert result["model_used"] == CONFIG_LLM
        assert result["attempts"][-1]["output_mode"] == "text"

    @pytest.mark.asyncio
    async def test_json_text_with_fences_and_prose_is_repaired(self, monkeypatch):
        payload = {
            "niche": "music_artist",
            "confidence": 0.8,
            "reasons": ["Live performance"],
        }
        behaviours_text = (
            "Here is the classification:\n```json\n"
            + json.dumps(payload)
            + "\n```\nHope that helps!"
        )
        behaviors = {
            (CONFIG_LLM, False): _agent(RuntimeError("no tool call")),
            (CONFIG_LLM, True): _agent(SimpleNamespace(output=behaviours_text)),
        }
        _install_chain(monkeypatch, _config(), behaviors)

        result = await classify_niche("live set from last night", [])

        assert result["niche"] == "music_artist"
        assert result["is_fallback"] is False

    @pytest.mark.asyncio
    async def test_json_text_with_manual_only_niche_is_rejected(self, monkeypatch):
        """The AUTO_NICHE_VALUES check applies to text answers exactly
        as to tool answers: a manual-only niche never classifies."""
        payload = {"niche": "football_edit", "confidence": 0.9, "reasons": ["x"]}
        behaviors = {
            (CONFIG_LLM, False): _agent(RuntimeError("no tool call")),
            (CONFIG_LLM, True): _agent(
                SimpleNamespace(output=json.dumps(payload))
            ),
            (ANALYSIS_MODEL, False): _agent(_output("sports", 0.8)),
            (FALLBACK_MODEL, False): _agent(_output("sports", 0.5)),
            (MANAGER_PICK, False): _agent(_output("sports", 0.5)),
            (DIRECT_FALLBACK_MODEL, False): _agent(_output("sports", 0.5)),
        }
        _install_chain(monkeypatch, _config(), behaviors)

        result = await classify_niche("football edits", [])

        assert result["niche"] == "sports"
        assert result["model_used"] == ANALYSIS_MODEL

    @pytest.mark.asyncio
    async def test_unparseable_text_moves_to_next_candidate(self, monkeypatch):
        behaviors = {
            (CONFIG_LLM, False): _agent(RuntimeError("no tool call")),
            (CONFIG_LLM, True): _agent(
                SimpleNamespace(output="I think this is gaming, probably")
            ),
            (ANALYSIS_MODEL, False): _agent(_output("gaming", 0.7)),
            (FALLBACK_MODEL, False): _agent(_output("gaming", 0.5)),
            (MANAGER_PICK, False): _agent(_output("gaming", 0.5)),
            (DIRECT_FALLBACK_MODEL, False): _agent(_output("gaming", 0.5)),
        }
        _install_chain(monkeypatch, _config(), behaviors)

        result = await classify_niche("gameplay", [])

        assert result["niche"] == "gaming"
        assert result["model_used"] == ANALYSIS_MODEL
