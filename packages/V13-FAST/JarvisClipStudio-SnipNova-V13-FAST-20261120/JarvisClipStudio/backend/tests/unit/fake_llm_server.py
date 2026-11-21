"""A programmable fake OpenAI-compatible LLM server (stdlib only).

Test helper for the analysis-resilience suite; not collected as a test.

Stands in for the LiteLLM gateway in tests and reproductions. Speaks just
enough of the OpenAI chat-completions API for pydantic-ai's OpenAI model:
structured output arrives as a forced tool call, so a success response is a
``tool_calls`` message whose arguments are built from the request itself
(real transcript labels for analysis, real candidate ids for rerank).

Programmable behaviours (set ``server.behaviour`` to a callable taking the
recorded request and returning ``(status, headers, payload_dict)``), or use
the built-in mode factories below.
"""

from __future__ import annotations

import json
import re
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Callable, Optional

SPAN_RE = re.compile(r"\[(\d{1,3}:\d{2}(?::\d{2})?) - (\d{1,3}:\d{2}(?::\d{2})?)\]\s*(.*)")


def _label_to_seconds(label: str) -> int:
    parts = [int(p) for p in label.split(":")]
    if len(parts) == 2:
        return parts[0] * 60 + parts[1]
    return parts[0] * 3600 + parts[1] * 60 + parts[2]


def _request_text(body: dict) -> str:
    parts: list[str] = []
    for message in body.get("messages", []):
        content = message.get("content")
        if isinstance(content, str):
            parts.append(content)
        elif isinstance(content, list):
            for item in content:
                if isinstance(item, dict) and isinstance(item.get("text"), str):
                    parts.append(item["text"])
    return "\n".join(parts)


def _tool_schema(body: dict) -> tuple[str, dict]:
    tools = body.get("tools") or []
    if not tools:
        return "", {}
    function = tools[0].get("function") or {}
    return function.get("name", ""), function.get("parameters") or {}


def _analysis_arguments(text: str) -> dict:
    """Build a schema-valid TranscriptAnalysis grounded in the prompt."""
    body_text = text.split("Transcript:", 1)[1] if "Transcript:" in text else text
    lines: list[tuple[int, str, str, str]] = []
    for raw in body_text.splitlines():
        match = SPAN_RE.match(raw.strip())
        if match:
            lines.append(
                (
                    _label_to_seconds(match.group(1)),
                    match.group(1),
                    match.group(2),
                    match.group(3).strip(),
                )
            )
    if not lines:
        return {"most_relevant_segments": [], "summary": "shim", "key_topics": ["shim"]}
    start = lines[0]
    end = lines[-1]
    for candidate in lines:
        if 30 <= candidate[0] - start[0] <= 50:
            end = candidate
            break
    segment_text = " ".join(
        line[3] for line in lines if start[0] <= line[0] <= _label_to_seconds(end[2])
    )
    segment = {
        "start_time": start[1],
        "end_time": end[2],
        "text": segment_text,
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
    return {
        "most_relevant_segments": [segment],
        "summary": "Shim summary.",
        "key_topics": ["shim"],
    }


def _rerank_arguments(text: str) -> dict:
    ids = [int(m) for m in re.findall(r"candidate_id:\s*(\d+)", text)]
    ranking = [
        {"candidate_id": cid, "score": max(10, 90 - 5 * i), "reason": "shim"}
        for i, cid in enumerate(ids)
    ]
    return {"ranking": ranking}


def success_payload(body: dict) -> dict:
    text = _request_text(body)
    tool_name, schema = _tool_schema(body)
    properties = (schema.get("properties") or {}) if isinstance(schema, dict) else {}
    if "ranking" in properties:
        arguments = _rerank_arguments(text)
    else:
        arguments = _analysis_arguments(text)
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
                                "name": tool_name or "final_result",
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


def error_payload(status: int, message: str) -> dict:
    return {
        "error": {
            "message": message,
            "type": "rate_limit_error" if status == 429 else "invalid_request_error",
            "code": "rate_limit_exceeded" if status == 429 else "shim_error",
        }
    }


# --- behaviour factories -------------------------------------------------
Behaviour = Callable[[dict, int], tuple[int, dict, dict]]


def always(status: int, retry_after: Optional[str] = None) -> Behaviour:
    def behave(body: dict, count: int) -> tuple[int, dict, dict]:
        headers = {"Retry-After": retry_after} if retry_after else {}
        return status, headers, error_payload(status, f"shim always-{status}")

    return behave


def first_n_then_success(n: int, status: int = 429) -> Behaviour:
    def behave(body: dict, count: int) -> tuple[int, dict, dict]:
        if count <= n:
            return status, {}, error_payload(status, f"shim {status} #{count}")
        return 200, {}, success_payload(body)

    return behave


def window_then_success(seconds: float, status: int = 429) -> Behaviour:
    """429 every request until ``seconds`` after the first request, then OK."""

    state: dict[str, float] = {}

    def behave(body: dict, count: int) -> tuple[int, dict, dict]:
        now = time.monotonic()
        state.setdefault("first", now)
        if now - state["first"] < seconds:
            return status, {}, error_payload(status, "shim window closed")
        return 200, {}, success_payload(body)

    return behave


def per_model(handlers: dict[str, Behaviour], default: Optional[Behaviour] = None) -> Behaviour:
    fallback = default or (lambda body, count: (200, {}, success_payload(body)))
    counters: dict[str, int] = {}

    def behave(body: dict, count: int) -> tuple[int, dict, dict]:
        model = body.get("model", "")
        counters[model] = counters.get(model, 0) + 1
        handler = handlers.get(model, fallback)
        return handler(body, counters[model])

    return behave


def first_line_label(body: dict) -> str:
    """First transcript line label of an analysis request, else ""."""
    text = _request_text(body)
    if "Transcript:" not in text:
        return ""
    match = SPAN_RE.search(text.split("Transcript:", 1)[1])
    return match.group(1) if match else ""


def hang_first_lines(labels: set[str], seconds: float) -> Behaviour:
    """Sleep ``seconds`` before succeeding for windows starting at ``labels``."""

    def behave(body: dict, count: int) -> tuple[int, dict, dict]:
        if first_line_label(body) in labels:
            time.sleep(seconds)
        return 200, {}, success_payload(body)

    return behave


class FakeLLMServer:
    """Threaded HTTP shim. ``requests`` is the full request log."""

    def __init__(self, behaviour: Behaviour):
        self.behaviour = behaviour
        self.requests: list[dict[str, Any]] = []
        self._lock = threading.Lock()
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):  # silence
                pass

            def do_POST(self):  # noqa: N802 - http.server API
                length = int(self.headers.get("Content-Length") or 0)
                raw = self.rfile.read(length) if length else b"{}"
                try:
                    body = json.loads(raw.decode("utf-8"))
                except Exception:
                    body = {}
                with outer._lock:
                    outer.requests.append(
                        {
                            "t": time.monotonic(),
                            "wall": time.time(),
                            "path": self.path,
                            "model": body.get("model"),
                            "body": body,
                        }
                    )
                    count = len(outer.requests)
                status, headers, payload = outer.behaviour(body, count)
                data = json.dumps(payload).encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                for key, value in headers.items():
                    self.send_header(key, value)
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

        self._server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.port = self._server.server_address[1]
        self.base_url = f"http://127.0.0.1:{self.port}/v1"
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)

    def __enter__(self) -> "FakeLLMServer":
        self._thread.start()
        return self

    def __exit__(self, *exc) -> None:
        self._server.shutdown()
        self._server.server_close()

    # -- log helpers -------------------------------------------------------
    def models_seen(self) -> list[str]:
        return [r["model"] for r in self.requests]

    def analysis_fingerprints(self) -> list[str]:
        """Fingerprints of analysis (not rerank) requests only."""
        out = []
        for record in self.requests:
            if "Transcript:" not in _request_text(record["body"]):
                continue
            out.append(
                f"{record['model']}@{first_line_label(record['body'])}"
            )
        return out

    def prompt_fingerprints(self) -> list[str]:
        """One fingerprint per request: first transcript label + model."""
        out = []
        for record in self.requests:
            text = _request_text(record["body"])
            match = SPAN_RE.search(text.split("Transcript:", 1)[-1])
            out.append(f"{record['model']}@{match.group(1) if match else '????'}")
        return out
