"""RERANK_LLM can target an OpenAI-compatible NVIDIA NIM endpoint by config."""

from src import ai
from src.config import Config, set_config_override

NIM_BASE_URL = "https://integrate.api.nvidia.com/v1"
NIM_MODEL = "openai:meta/llama-3.1-8b-instruct"


def test_rerank_agent_is_constructed_with_the_nim_base_url(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "nim-placeholder-key")
    monkeypatch.setenv("OPENAI_BASE_URL", NIM_BASE_URL)
    monkeypatch.setenv("RERANK_LLM", NIM_MODEL)

    config = Config()
    config.llm = NIM_MODEL
    config.rerank_llm = NIM_MODEL
    config.openai_api_key = "nim-placeholder-key"
    set_config_override(config)

    ai._rerank_agent = None
    ai._rerank_agent_signature = None
    try:
        agent = ai.get_rerank_agent()

        # Construction only: no request is sent. The OpenAI client holds the
        # base URL that the rerank agent will use for real calls.
        assert str(agent.model.client.base_url).rstrip("/") == NIM_BASE_URL
    finally:
        set_config_override(None)
        ai._rerank_agent = None
        ai._rerank_agent_signature = None
