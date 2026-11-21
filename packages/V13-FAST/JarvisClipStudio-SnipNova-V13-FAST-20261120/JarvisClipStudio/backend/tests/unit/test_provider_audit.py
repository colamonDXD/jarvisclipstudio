"""Provider audit helpers: pure logic only, no network, no keys."""
import importlib.util
from pathlib import Path

_spec = importlib.util.spec_from_file_location(
    "audit_providers", Path(__file__).resolve().parents[3] / "tools" / "provider-audit" / "audit_providers.py")
audit = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(audit)


def test_classify_statuses():
    c = audit.classify
    assert (c(200), c(401), c(402), c(429), c(404), c(503), c(0)) == (
        "OK", "AUTH_FAILED", "OUT_OF_CREDIT", "RATE_LIMITED_OR_QUOTA", "NOT_FOUND", "PROVIDER_ERROR", "UNREACHABLE")


def test_placeholder_keys_count_as_missing():
    env = {"GROQ_API_KEY": "changeme", "NVIDIA_API_KEY": "nvapi-realish", "X": ""}
    assert not audit.present(env, "GROQ_API_KEY") and audit.present(env, "NVIDIA_API_KEY") and not audit.present(env, "X")


def test_gateway_config_parses_ladder_and_static_audit_has_no_failures():
    rows = audit.static_audit()
    assert not [r for r in rows if r["status"] == "FAIL"], rows
    entries = audit.parse_gateway_config((audit.ROOT / "litellm_config.yaml").read_text(encoding="utf-8"))
    names = {e["name"] for e in entries}
    assert {"deepseek-chat", "clip-assured", "local-qwen", "cloudflare-free"} <= names
    assert all(not e["model"].startswith("openrouter/") or e["model"].endswith(":free") for e in entries)


def test_report_never_contains_key_values(tmp_path, monkeypatch):
    secret = "sk-SUPERSECRET-1234567890"
    monkeypatch.setattr(audit, "http", lambda *a, **k: (401, "bad", {}, 0.01))
    rows = audit.live_audit({"GROQ_API_KEY": secret, "DEEPSEEK_API_KEY": secret}, False, False)
    assert secret not in str(rows)
    assert [r for r in rows if r["provider"] == "groq"][0]["verdict"] == "AUTH_FAILED"


def test_nvidia_probe_reports_working_and_missing_models(monkeypatch):
    calls = []

    def fake_http(url, key=None, body=None, **kw):
        if url.endswith("/models"):
            return 200, '{"data":[{"id":"nvidia/nemotron-3-ultra-550b-a55b"}]}', {}, 0.01
        calls.append(body["model"])
        if body["model"] == "nvidia/nemotron-3-ultra-550b-a55b":
            return 200, '{"choices":[{"message":{"content":"ok"}}]}', {}, 0.2
        return 404, "not found", {}, 0.1

    monkeypatch.setattr(audit, "http", fake_http)
    out = audit.probe_nvidia({"NVIDIA_API_KEY": "nvapi-SECRET-123"})
    by = {r["model"]: r["result"] for r in out["probes"]}
    assert by["nvidia/nemotron-3-ultra-550b-a55b"] == "WORKS"
    assert by["deepseek-ai/deepseek-v4.1-flash"] == "NOT_FOUND"
    assert "nvapi-SECRET-123" not in str(out)
    assert audit.probe_nvidia({}) == {"verdict": "KEY_MISSING"}
