"""Six-case classifier battery for the P0 auto-niche fix (2026-11-10).

The exact proof shape the fix is judged by: six realistic sources,
each stubbed so the FIRST candidate fails in a DIFFERENT way and a
later candidate answers. Since the strongest-first reorder
(2026-11-11) the first candidate is the cloud head of chain (the
configured LLM), not the manager's local pick — the scenarios were
re-based onto the new head; the failure classes and the expected
verdicts are unchanged —

  case            first-candidate failure      expected niche
  --------------  ---------------------------  ----------------
  Japan food      build-time missing-key gate  travel_vlog
  Fortnite        gateway HTTP 400 (live log)  gaming
  Rogan podcast   transport error              podcast_news
  finance talk    JSON-text answer (repair)    finance_business
  football        JSON-text answer (repair)    sports
  music concert   unusable niche, next answers music_artist

plus the guardrail-rescue confirmations: a rescued niche's profile is
the one downstream would stamp — B-roll follows the RESCUED profile
(finance: off), never the podcast profile's stock B-roll.
"""

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

import src.auto_director.classify as classify_module
from src import provider_manager as pm
from src.auto_director.classify import classify_niche
from src.auto_director.edl import load_profile

LOCAL = "openai:local-qwen"
CLOUD = "openai:clip-fast"
ANALYSIS = "openai:clip-assured"
FB = "groq:openai/gpt-oss-120b"
DIRECT = "ollama:qwen3:8b"
ALL = (LOCAL, CLOUD, ANALYSIS, FB, DIRECT)


def _config():
    return SimpleNamespace(
        llm=CLOUD,
        analysis_models=[ANALYSIS],
        analysis_fallback_model=FB,
        analysis_direct_fallback_model=DIRECT,
        provider_mode="auto",
        local_llm=LOCAL,
        provider_manager_enabled=True,
        openai_api_key="test-openai-key",
        openrouter_api_key=None,
        openrouter_fallback_model="anthropic/claude-haiku-4-5",
        google_api_key=None,
        anthropic_api_key=None,
        ollama_base_url="http://localhost:11434",
        ollama_api_key=None,
    )


@pytest.fixture(autouse=True)
def fresh_manager(monkeypatch):
    pm._manager = pm.ProviderManager()
    monkeypatch.setattr(
        pm.ProviderManager, "_schedule_persist", lambda self, coro: coro.close()
    )
    yield
    pm._manager = None


def _ok(niche, confidence=0.9):
    return SimpleNamespace(
        run=AsyncMock(
            return_value=SimpleNamespace(
                output=SimpleNamespace(
                    niche=niche, confidence=confidence, reasons=["stub"]
                )
            )
        )
    )


def _raises(error):
    return SimpleNamespace(run=AsyncMock(side_effect=error))


def _text(payload):
    return SimpleNamespace(
        run=AsyncMock(return_value=SimpleNamespace(output=json.dumps(payload)))
    )


def _chain(monkeypatch, behaviors):
    monkeypatch.setattr(classify_module, "get_config", _config)
    monkeypatch.setattr(
        classify_module,
        "get_niche_classification_agent",
        lambda: (_ for _ in ()).throw(AssertionError("unused")),
    )

    def fake_build(model, *, text_output=False):
        key = (model, text_output)
        assert key in behaviors, f"unexpected build {key}"
        behavior = behaviors[key]
        if isinstance(behavior, BaseException):
            raise behavior
        return behavior

    monkeypatch.setattr(classify_module, "get_agent_for_model", fake_build)
    monkeypatch.setattr(
        classify_module.provider_manager, "manager_enabled", lambda cfg=None: True
    )

    async def _passthrough(agent, *args, **kwargs):
        return await agent.run(*args, **kwargs)

    monkeypatch.setattr(
        classify_module.provider_manager,
        "run_recorded",
        AsyncMock(side_effect=_passthrough),
    )


def _tail(niche, *, skip=()):
    """Harmless successful stubs for the candidates after the point
    of interest. NOTE: build the scenario dict with the point-of-
    interest entries AFTER this spread — later keys win, and the
    scenario entries must override the tail for the same model."""
    return {(model, False): _ok(niche) for model in ALL if model not in skip}


JAPAN_TRANSCRIPT = (
    "Okay so tell me what you think of this one. Honestly? I love it. "
    "Yeah the flavour is insane. Would you pay for this again? A "
    "hundred percent. The price difference is wild to me."
)
FORTNITE_TRANSCRIPT = (
    "Dropping tilted, got a gold scar off spawn. Rotating zone now, "
    "third party incoming — cracked one, knocked, full squad wipe. "
    "Victory royale, let's go."
)
ROGAN_TRANSCRIPT = (
    "Welcome back to the show. My guest today is a neuroscientist and "
    "we get into consciousness, dreams, and what the science actually "
    "says. Fascinating conversation."
)
FINANCE_TRANSCRIPT = (
    "The market's been open for an hour and the S&P is ripping. I "
    "don't miss a trading day. Let's review the portfolio, the "
    "dividend positions, and this week's earnings."
)
FINANCE_TITLE = "Trading Room — Live Market Open & Portfolio Review"
FOOTBALL_TRANSCRIPT = (
    "The striker holds it up, plays it wide, and the winger cuts "
    "inside — what a goal. The stadium is bouncing, the title race "
    "just blew wide open."
)
CONCERT_TRANSCRIPT = (
    "Thank you, thank you. This next one is a new song. When the "
    "chorus comes in I need everybody singing with me."
)


class TestSixCaseBattery:
    @pytest.mark.asyncio
    async def test_japan_food_travel__gate_failure(self, monkeypatch):
        behaviors = {
            **_tail("travel_vlog"),
            (CLOUD, False): RuntimeError(
                "Selected LLM provider is OpenAI, but OPENAI_API_KEY is not set."
            ),
        }
        behaviors[(ANALYSIS, False)] = _ok("travel_vlog", 0.9)
        _chain(monkeypatch, behaviors)

        result = await classify_niche(
            JAPAN_TRANSCRIPT, [], title="CORE $1 VS $10,000 RESTAURANTS IN JAPAN"
        )

        assert result["niche"] == "travel_vlog"
        assert result["is_fallback"] is False
        assert result["model_used"] == ANALYSIS
        assert result["attempts"][0]["model"] == CLOUD
        assert result["attempts"][0]["stage"] == "build"

    @pytest.mark.asyncio
    async def test_fortnite_gaming__gateway_400(self, monkeypatch):
        gateway_400 = RuntimeError(
            "status_code: 400, model_name: clip-fast, body: "
            "litellm.BadRequestError: OpenAIException - invalid message "
            "content type: <nil>"
        )
        behaviors = {
            **_tail("gaming"),
            (CLOUD, False): _raises(gateway_400),
            (CLOUD, True): _raises(gateway_400),
        }
        behaviors[(ANALYSIS, False)] = _ok("gaming", 0.93)
        _chain(monkeypatch, behaviors)

        result = await classify_niche(
            FORTNITE_TRANSCRIPT, [], title="FORTNITE Ranked — Victory Royale Grind"
        )

        assert result["niche"] == "gaming"
        assert result["is_fallback"] is False
        assert result["model_used"] == ANALYSIS
        assert "400" in result["attempts"][0]["error"]

    @pytest.mark.asyncio
    async def test_rogan_podcast_control__transport_error(self, monkeypatch):
        behaviors = {
            **_tail("podcast_news"),
            (CLOUD, False): _raises(ConnectionError("gateway unreachable")),
            (CLOUD, True): _raises(ConnectionError("gateway unreachable")),
        }
        behaviors[(ANALYSIS, False)] = _ok("podcast_news", 0.95)
        _chain(monkeypatch, behaviors)

        result = await classify_niche(
            ROGAN_TRANSCRIPT, [], title="The Joe Rogan Experience #2140"
        )

        # A REAL podcast answer: not a fallback, not a rescue.
        assert result["niche"] == "podcast_news"
        assert result["is_fallback"] is False
        assert result.get("guardrail_rescued") is not True
        assert result["confidence"] == 0.95

    @pytest.mark.asyncio
    async def test_finance_talk__json_text_repair(self, monkeypatch):
        payload = {
            "niche": "finance_business",
            "confidence": 0.89,
            "reasons": ["Trading floor discussion"],
        }
        behaviors = {
            **_tail("finance_business"),
            (CLOUD, False): _raises(RuntimeError("no final_result tool call")),
            (CLOUD, True): _text(payload),
        }
        _chain(monkeypatch, behaviors)

        result = await classify_niche(FINANCE_TRANSCRIPT, [], title=FINANCE_TITLE)

        assert result["niche"] == "finance_business"
        assert result["is_fallback"] is False
        assert result["model_used"] == CLOUD
        assert result["attempts"][-1]["output_mode"] == "text"

    @pytest.mark.asyncio
    async def test_football__json_text_repair(self, monkeypatch):
        payload = {
            "niche": "sports",
            "confidence": 0.9,
            "reasons": ["Match commentary"],
        }
        behaviors = {
            **_tail("sports"),
            (CLOUD, False): _raises(RuntimeError("no final_result tool call")),
            (CLOUD, True): _text(payload),
        }
        _chain(monkeypatch, behaviors)

        result = await classify_niche(
            FOOTBALL_TRANSCRIPT, [], title="Matchday — Derby Highlights"
        )

        assert result["niche"] == "sports"
        assert result["model_used"] == CLOUD

    @pytest.mark.asyncio
    async def test_music_concert__unusable_niche_then_next(self, monkeypatch):
        behaviors = {
            **_tail("music_artist"),
            (CLOUD, False): _ok("invalid_niche_xyz"),
        }
        behaviors[(ANALYSIS, False)] = _ok("music_artist", 0.91)
        _chain(monkeypatch, behaviors)

        result = await classify_niche(
            CONCERT_TRANSCRIPT, [], title="Live In Concert — Full Set"
        )

        assert result["niche"] == "music_artist"
        assert result["is_fallback"] is False
        assert result["model_used"] == ANALYSIS


class TestRescuedProfileBroll:
    """Addendum (B): the trading-room clip Luffy uploaded wore the
    podcast profile, whose stock B-roll (pexels, enabled) spliced an
    unrelated highway clip into the export. Once the niche is right —
    by detection or by guardrail rescue — B-roll follows THAT profile."""

    def test_podcast_profile_has_stock_broll_on(self):
        assert load_profile("podcast_news")["broll"]["enabled"] is True

    def test_finance_profile_has_broll_on_and_comedy_profile_has_broll_off(
        self,
    ):
        # Slice 5 (d8cb6a6, SMART_BROLL_V2_DESIGN.md §5.1) deliberately
        # turned finance B-roll ON at 1.5/min: the TJR "market" damage
        # is now prevented by the sense table (Slice 1), concept
        # writing (Slice 2), the concept resolver + avoid gate
        # (Slice 3) and the vision veto ON by default (Slice 4) — not
        # by keeping finance B-roll off. The old "finance off" pin is
        # gone by design; the table is pinned exactly instead.
        finance = load_profile("finance_business")["broll"]
        assert finance["enabled"] is True
        assert finance["density_per_min"] == 1.5
        assert finance["source"] == "stock"
        assert load_profile("meme_comedy")["broll"]["enabled"] is False

    @pytest.mark.asyncio
    async def test_guardrail_rescued_finance_uses_finance_profile(
        self, monkeypatch
    ):
        """All candidates fail on the trading-room video; the
        guardrail rescue stamps finance_business — and the render
        follows the finance profile (B-roll ON at 1.5/min since
        Slice 5, d8cb6a6), never the podcast profile. The
        highway-clip damage is now prevented by the sense /
        concept / veto layers, not by finance B-roll being off."""
        behaviors = {
            (model, False): _raises(ConnectionError("down"))
            for model in ALL
        }
        for model in ALL:
            behaviors[(model, True)] = RuntimeError("no text build")
        _chain(monkeypatch, behaviors)

        result = await classify_niche(FINANCE_TRANSCRIPT, [], title=FINANCE_TITLE)

        assert result["niche"] == "finance_business"
        assert result["is_fallback"] is True
        assert result["guardrail_rescued"] is True
        profile = load_profile(result["niche"])
        assert profile["broll"]["enabled"] is True
        assert profile["broll"]["density_per_min"] == 1.5
