"""Provider-manager proof against fake OpenAI-compatible HTTP servers.

A real shim (stdlib http.server) serves two cloud aliases and a fake local
Ollama endpoint. Real pydantic-ai agents talk to it over HTTP through the
manager's proxy, proving: a 429 cools the alias down and the next selection
uses the other alias without another failed call; a quota failure cools down
until the UTC day boundary; local-first routes routine work to the local
endpoint; local-only with local down fails in plain words with zero cloud
calls; and token accounting matches the shim's reported usage exactly.
"""

import json
import socket
import threading
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
from openai import AsyncOpenAI
from pydantic_ai import Agent
from pydantic_ai.exceptions import ModelHTTPError
from pydantic_ai.models.openai import OpenAIChatModel
from pydantic_ai.providers.openai import OpenAIProvider

from src import provider_manager as pm
from src.provider_manager import FailureClass, ProviderManager

FIXED_NOW = datetime(2026, 11, 8, 10, 30, tzinfo=timezone.utc).timestamp()

USAGE_A = {"prompt_tokens": 111, "completion_tokens": 22, "total_tokens": 133}
USAGE_LOCAL = {"prompt_tokens": 50, "completion_tokens": 10, "total_tokens": 60}


def completion_payload(model: str, usage: dict) -> dict:
    return {
        "id": "chatcmpl-test",
        "object": "chat.completion",
        "created": 0,
        "model": model,
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": "ok"},
                "finish_reason": "stop",
            }
        ],
        "usage": usage,
    }


class Shim:
    """One fake OpenAI-compatible server; behaviour maps model -> (status, payload)."""

    def __init__(self, behaviour):
        self.behaviour = behaviour
        self.requests: list[dict] = []
        shim = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def _send(self, status, payload):
                body = json.dumps(payload).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self):
                shim.requests.append({"method": "GET", "path": self.path, "model": None})
                if self.path.endswith("/models"):
                    self._send(200, {"data": [{"id": "qwen3:8b"}]})
                else:
                    self._send(404, {"error": {"message": "not found"}})

            def do_POST(self):
                length = int(self.headers.get("Content-Length", 0))
                raw = self.rfile.read(length) if length else b"{}"
                try:
                    body = json.loads(raw)
                except json.JSONDecodeError:
                    body = {}
                model = body.get("model")
                shim.requests.append({"method": "POST", "path": self.path, "model": model})
                if self.path.endswith("/chat/completions"):
                    status, payload = shim.behaviour(model)
                    self._send(status, payload)
                else:
                    self._send(404, {"error": {"message": "not found"}})

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base_url = f"http://127.0.0.1:{self.server.server_address[1]}/v1"

    def close(self):
        self.server.shutdown()
        self.server.server_close()

    def count(self, model: str) -> int:
        return sum(1 for request in self.requests if request["model"] == model)


@pytest.fixture
def cloud_shim():
    def behaviour(model):
        if model == "alias-a":
            return 429, {"error": {"message": "Rate limit reached, try again later"}}
        if model == "alias-q":
            return 429, {
                "error": {
                    "code": "insufficient_quota",
                    "message": "You exceeded your current quota",
                }
            }
        return 200, completion_payload(model, USAGE_A)

    shim = Shim(behaviour)
    yield shim
    shim.close()


@pytest.fixture
def local_shim():
    shim = Shim(lambda model: (200, completion_payload(model, USAGE_LOCAL)))
    yield shim
    shim.close()


def build_agent(base_url: str, model_name: str) -> Agent:
    client = AsyncOpenAI(base_url=base_url, api_key="test-key", max_retries=0)
    model = OpenAIChatModel(model_name, provider=OpenAIProvider(openai_client=client))
    return Agent(model, output_type=str)


def dead_port() -> int:
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    return port


class TestFakeServerProof:
    async def test_429_cools_down_and_next_selection_uses_other_alias(self, cloud_shim):
        manager = ProviderManager(now_fn=lambda: FIXED_NOW)
        proxy_a = pm.ManagedAgentProxy(
            build_agent(cloud_shim.base_url, "alias-a"),
            alias="openai:alias-a",
            purpose="analysis",
            manager=manager,
        )
        with pytest.raises(ModelHTTPError):
            await proxy_a.run("find the best moments")
        assert cloud_shim.count("alias-a") == 1
        record = manager.cooldown_record("openai:alias-a")
        assert record is not None
        assert record.failure_class is FailureClass.RATE_LIMITED
        assert not manager.is_available("openai:alias-a")

        # The next selection skips the cooled alias entirely — no failed call.
        selected = manager.select_models(
            ["openai:alias-a", "openai:alias-b"],
            purpose="analysis",
            complexity="hard",
            mode="auto",
        )
        assert selected == ["openai:alias-b"]

        proxy_b = pm.ManagedAgentProxy(
            build_agent(cloud_shim.base_url, "alias-b"),
            alias="openai:alias-b",
            purpose="analysis",
            manager=manager,
        )
        result = await proxy_b.run("find the best moments")
        assert result.output == "ok"
        assert cloud_shim.count("alias-a") == 1  # never retried while cooling
        assert cloud_shim.count("alias-b") == 1

    async def test_accounting_matches_shim_usage_exactly(self, cloud_shim):
        manager = ProviderManager(now_fn=lambda: FIXED_NOW)
        proxy_b = pm.ManagedAgentProxy(
            build_agent(cloud_shim.base_url, "alias-b"),
            alias="openai:alias-b",
            purpose="analysis",
            manager=manager,
        )
        with pm.task_usage_scope("task-42"):
            await proxy_b.run("one")
            await proxy_b.run("two")
        view = manager.usage_for_task("task-42")
        totals = view["by_alias"]["openai:alias-b"]
        assert totals.calls == 2
        assert totals.prompt_tokens == 2 * USAGE_A["prompt_tokens"]
        assert totals.completion_tokens == 2 * USAGE_A["completion_tokens"]
        assert totals.total_tokens == 2 * USAGE_A["total_tokens"]
        assert totals.unknown_usage_calls == 0
        assert view["totals"].total_tokens == 2 * USAGE_A["total_tokens"]

    async def test_quota_failure_cools_until_day_boundary(self, cloud_shim):
        manager = ProviderManager(now_fn=lambda: FIXED_NOW)
        proxy_q = pm.ManagedAgentProxy(
            build_agent(cloud_shim.base_url, "alias-q"),
            alias="openai:alias-q",
            purpose="analysis",
            manager=manager,
        )
        with pytest.raises(ModelHTTPError):
            await proxy_q.run("analyse")
        record = manager.cooldown_record("openai:alias-q")
        assert record is not None
        assert record.failure_class is FailureClass.QUOTA_EXHAUSTED
        expected = (
            datetime(2026, 11, 8, tzinfo=timezone.utc) + timedelta(days=1)
        ).timestamp()
        assert record.cooldown_until == expected

    async def test_local_first_routes_to_local_endpoint(self, cloud_shim, local_shim):
        manager = ProviderManager(now_fn=lambda: FIXED_NOW)
        healthy = await manager.probe_local_health(
            "openai:local-qwen", local_shim.base_url
        )
        assert healthy is True
        selected = manager.select_models(
            ["openai:alias-b", "openai:local-qwen"],
            purpose="analysis",
            complexity="hard",
            mode="local-first",
            local_healthy=healthy,
        )
        assert selected[0] == "openai:local-qwen"

        proxy_local = pm.ManagedAgentProxy(
            build_agent(local_shim.base_url, "local-qwen"),
            alias="openai:local-qwen",
            purpose="analysis",
            manager=manager,
        )
        result = await proxy_local.run("routine classification-sized job")
        assert result.output == "ok"
        assert local_shim.count("local-qwen") == 1
        assert cloud_shim.count("alias-b") == 0
        totals = manager.usage_for_task(None)["by_alias"]["openai:local-qwen"]
        assert totals.total_tokens == USAGE_LOCAL["total_tokens"]

    async def test_local_only_with_local_down_makes_zero_cloud_calls(self, cloud_shim):
        manager = ProviderManager(now_fn=lambda: FIXED_NOW)
        healthy = await manager.probe_local_health(
            "openai:local-qwen", f"http://127.0.0.1:{dead_port()}/v1"
        )
        assert healthy is False
        with pytest.raises(pm.LocalProviderUnavailable) as excinfo:
            manager.select_models(
                ["openai:alias-b", "openai:local-qwen"],
                purpose="analysis",
                complexity="routine",
                mode="local-only",
                local_healthy=healthy,
            )
        assert "local-only" in str(excinfo.value)
        assert cloud_shim.requests == []
