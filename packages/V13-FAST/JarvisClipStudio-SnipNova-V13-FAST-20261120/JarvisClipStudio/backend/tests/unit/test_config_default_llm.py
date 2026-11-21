"""Default LLM inference: Groq first, local Ollama last, never Gemini.

``Config._infer_default_llm`` used to resolve to
``google-gla:gemini-3-flash-preview`` whenever a Google key was present,
and to the same dead model when no key was present at all — while never
consulting GROQ_API_KEY, the working provider on this deployment. The
resolved string feeds the main pipeline directly (ai.py transcript
analysis, classify.py, plan/retention passes, AI edit) through the
pydantic-ai direct clients in ``ai._build_transcript_model``, so the
default must be a string that consumer accepts.
"""

import pytest

from src import config as config_module
from src.ai import (
    GroqModel,
    _build_transcript_model,
    _get_missing_llm_key_error,
)
from src.config import Config

KEY_VARS = (
    "GROQ_API_KEY",
    "OPENAI_API_KEY",
    "ANTHROPIC_API_KEY",
    "OPENROUTER_API_KEY",
    "GOOGLE_API_KEY",
    "LLM",
)


def _config_with(monkeypatch, **keys):
    """Config with exactly the given key env vars set (no admin settings)."""
    for name in KEY_VARS:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(config_module, "get_cached_setting", lambda name: None)
    for name, value in keys.items():
        monkeypatch.setenv(name, value)
    return Config()


@pytest.mark.parametrize(
    "keys,expected",
    [
        ({"GROQ_API_KEY": "groq-key"}, "groq:openai/gpt-oss-120b"),
        # Groq wins even when a Google key is present (the old first branch).
        (
            {"GROQ_API_KEY": "groq-key", "GOOGLE_API_KEY": "google-key"},
            "groq:openai/gpt-oss-120b",
        ),
        ({"GROQ_API_KEY": "groq-key", "OPENAI_API_KEY": "openai-key"},
         "groq:openai/gpt-oss-120b"),
        ({"OPENAI_API_KEY": "openai-key"}, "openai-chat:gpt-5.2"),
        ({"ANTHROPIC_API_KEY": "anthropic-key"}, "anthropic:claude-4-sonnet"),
        (
            {"OPENROUTER_API_KEY": "openrouter-key"},
            "openrouter:anthropic/claude-sonnet-5.5",
        ),
        # Upstream order among the remaining providers is preserved.
        (
            {"OPENAI_API_KEY": "openai-key", "OPENROUTER_API_KEY": "openrouter-key"},
            "openai-chat:gpt-5.2",
        ),
        # A Google key alone no longer resolves to Gemini.
        ({"GOOGLE_API_KEY": "google-key"}, "ollama:qwen3:8b"),
        # No keys at all: local Ollama, never the old Gemini final fallback.
        ({}, "ollama:qwen3:8b"),
    ],
)
def test_infer_default_llm(monkeypatch, keys, expected):
    config = _config_with(monkeypatch, **keys)

    assert config.llm == expected
    assert "gemini" not in config.llm.lower()
    assert "google" not in config.llm.lower()


def test_groq_api_key_is_a_config_attribute(monkeypatch):
    config = _config_with(monkeypatch, GROQ_API_KEY="groq-key")

    assert config.groq_api_key == "groq-key"

    config_without = _config_with(monkeypatch)
    assert config_without.groq_api_key is None


def test_explicit_llm_setting_beats_inference(monkeypatch):
    config = _config_with(
        monkeypatch, LLM="ollama:custom-model", GROQ_API_KEY="groq-key"
    )

    assert config.llm == "ollama:custom-model"


def test_groq_default_is_accepted_by_the_pipeline_consumer(monkeypatch):
    """The inferred Groq string must pass the key check and build a
    GroqModel (construction only — no live call)."""
    config = _config_with(monkeypatch, GROQ_API_KEY="groq-key")

    assert _get_missing_llm_key_error(config.llm, config) is None
    model = _build_transcript_model(config, config.llm)
    assert isinstance(model, GroqModel)
    assert model.model_name == "openai/gpt-oss-120b"


def test_ollama_fallback_is_accepted_by_the_pipeline_consumer(monkeypatch):
    config = _config_with(monkeypatch)

    assert _get_missing_llm_key_error(config.llm, config) is None
    model = _build_transcript_model(config, config.llm)
    assert type(model).__name__ == "OllamaModel"


def test_openai_chat_default_is_accepted_by_the_pipeline_consumer(monkeypatch):
    """The inferred OpenAI string must pass the key check and reach the
    pipeline consumer unchanged (construction only — no live call)."""
    import warnings

    from pydantic_ai import Agent

    config = _config_with(monkeypatch, OPENAI_API_KEY="openai-key")

    assert config.llm == "openai-chat:gpt-5.2"
    assert _get_missing_llm_key_error(config.llm, config) is None
    # _build_transcript_model returns non-special providers as-is; the
    # string itself is what pydantic-ai infers (openai-chat: resolves to
    # OpenAIChatModel with no deprecation warning, unlike openai:).
    assert _build_transcript_model(config, config.llm) == "openai-chat:gpt-5.2"
    # Construction is the point the deprecation warning fires: the shipped
    # default must build an agent with no PydanticAIDeprecationWarning.
    # (Matched by category name: the class lives in pydantic_ai._warnings,
    # with no public import path.)
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        Agent(model=_build_transcript_model(config, config.llm))
    assert not [
        w
        for w in caught
        if w.category.__name__ == "PydanticAIDeprecationWarning"
    ]


def test_openai_chat_key_check_requires_key(monkeypatch):
    """The canonical prefix still requires OPENAI_API_KEY, and the
    deprecated openai: spelling keeps working (same provider, same key)."""
    config = _config_with(monkeypatch, OPENAI_API_KEY="openai-key")

    assert _get_missing_llm_key_error("openai-chat:gpt-5.2", config) is None
    assert _get_missing_llm_key_error("openai:gpt-5.2", config) is None

    config_without = _config_with(monkeypatch)
    error = _get_missing_llm_key_error("openai-chat:gpt-5.2", config_without)
    assert error is not None
    assert "OPENAI_API_KEY" in error
