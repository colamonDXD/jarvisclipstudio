"""Keyless-safe provider audit for SnipNova's AI ladder.

Never prints, logs or writes a key value. Only reports present / missing,
status codes and a verdict. Free checks only: model-list and key-validity
endpoints cost nothing. A real inference call is OFF unless you pass --infer,
and the metered DeepSeek route additionally needs --allow-metered.

  python audit_providers.py --static                # repo/config checks, no network
  python audit_providers.py --live                  # key + model checks (free endpoints)
  python audit_providers.py --live --infer          # + one 1-token request per FREE provider
  python audit_providers.py --live --infer --allow-metered   # + DeepSeek (spends a fraction of a cent)
  python audit_providers.py --env-file C:\\path\\.env  # read keys from a file instead of the environment

Run it on the machine that holds the real keys (the installed app's .env).
Exit code 0 = no FAIL. Output: a table plus audit_report.json (no secrets).
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

# name -> (env vars, model-list URL builder, free?, notes)
PROVIDERS = {
    "groq": dict(env=["GROQ_API_KEY"], models="https://api.groq.com/openai/v1/models",
                 chat="https://api.groq.com/openai/v1/chat/completions", cost="free-tier",
                 site="https://console.groq.com", card=False,
                 limits="free tier has per-minute and per-day request/token limits that change; check console.groq.com/settings/limits"),
    "nvidia": dict(env=["NVIDIA_API_KEY"], models="https://integrate.api.nvidia.com/v1/models",
                   chat="https://integrate.api.nvidia.com/v1/chat/completions", cost="free-credits",
                   site="https://build.nvidia.com", card=False,
                   limits="NIM trial access is rate-limited (requests per minute) and may need NVIDIA Developer Program signup"),
    "openrouter": dict(env=["OPENROUTER_API_KEY"], models="https://openrouter.ai/api/v1/models",
                       chat="https://openrouter.ai/api/v1/chat/completions", cost="free-only-with-:free-slugs",
                       site="https://openrouter.ai", card=False,
                       limits="free (:free) models: low daily request cap unless credits were ever bought; verify at openrouter.ai/settings/credits"),
    "deepseek": dict(env=["DEEPSEEK_API_KEY"], models="https://api.deepseek.com/models",
                     chat="https://api.deepseek.com/chat/completions", cost="METERED",
                     site="https://platform.deepseek.com", card=False,
                     limits="pay-as-you-go balance; requests fail with 402 when the balance is 0"),
    "cloudflare": dict(env=["CLOUDFLARE_API_KEY", "CLOUDFLARE_ACCOUNT_ID"], models=None, chat=None,
                       cost="free-allocation", site="https://dash.cloudflare.com", card=False,
                       limits="Workers AI free allocation is 10,000 Neurons/day on the Workers Free plan"),
    "huggingface": dict(env=["HF_TOKEN"], models=None, chat=None, cost="free", site="https://huggingface.co",
                        card=False, limits="needed only for gated models (e.g. pyannote terms click)"),
    "ollama": dict(env=[], models="http://localhost:11434/api/tags", chat=None, cost="local-free",
                   site="https://ollama.com", card=False, limits="runs on your GPU/CPU; qwen3:8b needs ~5-6 GB"),
}


def load_env(path: str | None) -> dict[str, str]:
    env = dict(os.environ)
    if path:
        for line in Path(path).read_text(encoding="utf-8", errors="ignore").splitlines():
            m = re.match(r"\s*([A-Z0-9_]+)\s*=\s*(.*)\s*$", line)
            if m and not line.lstrip().startswith("#"):
                env[m.group(1)] = m.group(2).strip().strip('"').strip("'")
    return env


def present(env: dict[str, str], name: str) -> bool:
    v = env.get(name, "")
    return bool(v) and v.lower() not in ("changeme", "your_key_here", "xxx")


def http(url: str, key: str | None = None, body: dict | None = None, timeout: int = 20, headers: dict | None = None):
    req = urllib.request.Request(url, data=None if body is None else json.dumps(body).encode(),
                                 method="POST" if body is not None else "GET")
    req.add_header("User-Agent", "snipnova-provider-audit")
    if body is not None:
        req.add_header("Content-Type", "application/json")
    if key:
        req.add_header("Authorization", f"Bearer {key}")
    for k, v in (headers or {}).items():
        req.add_header(k, v)
    t0 = time.time()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, r.read().decode("utf-8", "ignore"), dict(r.headers), time.time() - t0
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "ignore")[:400], dict(e.headers or {}), time.time() - t0
    except Exception as e:  # network down, DNS, timeout
        return 0, f"{type(e).__name__}", {}, time.time() - t0


def classify(status: int, body: str = "") -> str:
    """Plain verdict for an HTTP status. Pure, unit-tested."""
    if status == 0:
        return "UNREACHABLE"
    if 200 <= status < 300:
        return "OK"
    if status in (401, 403):
        return "AUTH_FAILED"
    if status == 402:
        return "OUT_OF_CREDIT"
    if status == 429:
        return "RATE_LIMITED_OR_QUOTA"
    if status == 404:
        return "NOT_FOUND"
    if status >= 500:
        return "PROVIDER_ERROR"
    return f"HTTP_{status}"


# ---- static audit ---------------------------------------------------------

def parse_gateway_config(text: str) -> list[dict]:
    """Minimal parser for litellm_config.yaml's model_list (no PyYAML needed)."""
    entries, cur = [], None
    for raw in text.splitlines():
        line = raw.split("#", 1)[0].rstrip()
        m = re.match(r"\s*-\s*model_name:\s*(\S+)", line)
        if m:
            cur = {"name": m.group(1), "model": "", "key_env": None, "api_base": None}
            entries.append(cur)
            continue
        if cur is None:
            continue
        for field, rx in (("model", r"\s*model:\s*(\S+)"), ("api_base", r"\s*api_base:\s*(\S+)"),
                          ("key_env", r"\s*api_key:\s*os\.environ/(\S+)")):
            mm = re.match(rx, line)
            if mm and not cur.get(field):
                cur[field] = mm.group(1)
    return entries


def static_audit() -> list[dict]:
    out = []
    cfg = ROOT / "litellm_config.yaml"
    if not cfg.exists():
        return [dict(check="gateway config", status="FAIL", detail="litellm_config.yaml not found")]
    entries = parse_gateway_config(cfg.read_text(encoding="utf-8"))
    out.append(dict(check="gateway routes parsed", status="OK", detail=f"{len(entries)} routes: " + ", ".join(e["name"] for e in entries)))
    for e in entries:
        m = e["model"].lower()
        if "gemini" in m or "gemini" in e["name"].lower():
            out.append(dict(check=f"{e['name']}: no Gemini", status="FAIL", detail="Gemini route present (banned)"))
        if m.startswith("openrouter/") and not m.endswith(":free"):
            out.append(dict(check=f"{e['name']}: OpenRouter free only", status="FAIL", detail=f"paid slug {e['model']}"))
        if e["key_env"] and e["key_env"] not in sum((p["env"] for p in PROVIDERS.values()), []):
            out.append(dict(check=f"{e['name']}: key variable known", status="WARN", detail=f"reads {e['key_env']} (not in audit table)"))
    names = [e["name"] for e in entries]
    expect = ["deepseek-chat", "clip-assured", "clip-fast", "nim-deepseek", "openrouter-free", "cloudflare-free", "groq-20b", "local-qwen"]
    missing = [n for n in expect if n not in names]
    out.append(dict(check="ladder routes present", status="OK" if not missing else "FAIL",
                    detail="all 8 ladder routes defined" if not missing else f"missing: {missing}"))
    # where do app services read the keys?
    readers = {}
    for p in (ROOT / "backend" / "src").rglob("*.py"):
        t = p.read_text(encoding="utf-8", errors="ignore")
        for var in ("GROQ_API_KEY", "DEEPSEEK_API_KEY", "NVIDIA_API_KEY", "OPENROUTER_API_KEY", "CLOUDFLARE_API_KEY", "CLOUDFLARE_ACCOUNT_ID", "HF_TOKEN"):
            if var in t:
                readers.setdefault(var, []).append(p.name)
    compose = (ROOT / "docker-compose.yml").read_text(encoding="utf-8", errors="ignore") + (ROOT / "compose.override.yml").read_text(encoding="utf-8", errors="ignore")
    for var in ("GROQ_API_KEY", "DEEPSEEK_API_KEY", "NVIDIA_API_KEY", "OPENROUTER_API_KEY", "CLOUDFLARE_API_KEY", "CLOUDFLARE_ACCOUNT_ID", "HF_TOKEN"):
        in_compose = var in compose
        in_cfg = any(e["key_env"] == var for e in entries)
        used = in_cfg or var in readers
        out.append(dict(check=f"{var} wired", status="OK" if (in_compose and used) else "WARN",
                        detail=f"gateway-config={in_cfg} app-code={'yes' if var in readers else 'no'} compose-passes-it={in_compose}"))
    return out


# ---- live audit -----------------------------------------------------------

def gateway_models() -> dict[str, list[str]]:
    cfg = ROOT / "litellm_config.yaml"
    by = {"groq": [], "nvidia": [], "openrouter": [], "deepseek": [], "cloudflare": []}
    for e in parse_gateway_config(cfg.read_text(encoding="utf-8")):
        m = e["model"]
        if m.startswith("groq/"):
            by["groq"].append(m[len("groq/"):])
        elif m.startswith("openrouter/"):
            by["openrouter"].append(m[len("openrouter/"):])
        elif (e.get("api_base") or "").startswith("https://integrate.api.nvidia.com"):
            by["nvidia"].append(m[len("openai/"):])
        elif (e.get("api_base") or "").startswith("https://api.deepseek.com"):
            by["deepseek"].append(m[len("openai/"):])
        elif "cloudflare" in m:
            by["cloudflare"].append(m)
    return by


def live_audit(env: dict[str, str], infer: bool, allow_metered: bool) -> list[dict]:
    rows = []
    wanted = gateway_models()
    for name, p in PROVIDERS.items():
        row = dict(provider=name, cost=p["cost"], site=p["site"], card_required=p["card"], limits=p["limits"],
                   env={v: ("present" if present(env, v) else "MISSING") for v in p["env"]})
        if p["env"] and not all(present(env, v) for v in p["env"]):
            row.update(verdict="KEY_MISSING", detail=f"set {', '.join(p['env'])} in the installed app's .env")
            rows.append(row)
            continue
        key = env.get(p["env"][0]) if p["env"] else None
        if name == "cloudflare":
            acct = env["CLOUDFLARE_ACCOUNT_ID"]
            # Account-owned tokens verify at the account endpoint; user-owned
            # tokens at /user. Try both so a valid token is never called bad.
            st, body, _h, dt = http(f"https://api.cloudflare.com/client/v4/accounts/{acct}/tokens/verify", key)
            row["verified_via"] = "account"
            if st != 200:
                st_u, body_u, _h_u, dt_u = http("https://api.cloudflare.com/client/v4/user/tokens/verify", key)
                if st_u == 200:
                    st, body, dt, row["verified_via"] = st_u, body_u, dt_u, "user"
            row.update(key_check=classify(st), latency=round(dt, 2))
            if st != 200:
                row["detail"] = ("neither account nor user token verification accepted this token; if it is the old "
                                 "'Global API Key' it will not work: create an API TOKEN with Workers AI permission")
            row["verdict"] = "OK" if st == 200 else classify(st)
            if st == 200 and infer:
                model = "@cf/openai/gpt-oss-120b"
                st2, body2, _h2, dt2 = http(f"https://api.cloudflare.com/client/v4/accounts/{acct}/ai/v1/chat/completions", key,
                                            {"model": model, "messages": [{"role": "user", "content": "Reply with: ok"}], "max_tokens": 5})
                row.update(inference=classify(st2), inference_latency=round(dt2, 2))
        elif name == "huggingface":
            st, body, _h, dt = http("https://huggingface.co/api/whoami-v2", key)
            row.update(key_check=classify(st), latency=round(dt, 2), verdict="OK" if st == 200 else classify(st))
        elif name == "ollama":
            st, body, _h, dt = http(p["models"])
            tags = []
            try:
                tags = [m["name"] for m in json.loads(body).get("models", [])]
            except Exception:
                pass
            has = any(t.startswith("qwen3:8b") for t in tags)
            row.update(server=classify(st), models_found=len(tags), qwen3_8b=has,
                       verdict="OK" if has else ("MODEL_MISSING" if st == 200 else "UNREACHABLE"),
                       detail="" if has else "run: ollama pull qwen3:8b (about 5 GB)")
        else:
            st, body, hdr, dt = http(p["models"], key)
            row.update(key_check=classify(st, body), latency=round(dt, 2))
            ids = []
            try:
                ids = [m["id"] for m in json.loads(body).get("data", [])]
            except Exception:
                pass
            missing = [m for m in wanted.get(name, []) if ids and m not in ids]
            row["models_configured"] = wanted.get(name, [])
            row["models_missing_from_provider"] = missing
            if missing and ids:
                # Public model names only (never secrets): the closest ids the provider does list.
                row["closest_listed_models"] = {
                    m: [i for i in ids if m.split("/")[-1].split("-")[0].lower() in i.lower()][:8] for m in missing}
                if name == "deepseek":
                    row["provider_lists"] = ids[:30]
            row["verdict"] = "OK" if st == 200 and not missing else ("MODEL_NOT_LISTED" if st == 200 else classify(st))
            if name == "openrouter":
                for m in wanted["openrouter"]:
                    if not m.endswith(":free"):
                        row["verdict"] = "FAIL_PAID_SLUG"
            if infer and st == 200 and wanted.get(name) and (name != "deepseek" or allow_metered):
                m = wanted[name][0]
                st2, body2, hdr2, dt2 = http(p["chat"], key, {"model": m, "messages": [{"role": "user", "content": "Reply with: ok"}], "max_tokens": 8})
                ok = False
                try:
                    ok = bool(json.loads(body2)["choices"][0])
                except Exception:
                    pass
                row.update(inference=classify(st2) if not (st2 == 200 and not ok) else "EMPTY_RESPONSE",
                           inference_latency=round(dt2, 2), inference_model=m)
                rl = {k: v for k, v in hdr2.items() if "ratelimit" in k.lower() or "retry-after" in k.lower()}
                if rl:
                    row["rate_limit_headers"] = rl
            elif infer and name == "deepseek" and not allow_metered:
                row["inference"] = "SKIPPED (metered; pass --allow-metered)"
        rows.append(row)
    return rows


NVIDIA_CANDIDATES = [  # names seen on build.nvidia.com; probed, never assumed
    "nvidia/nemotron-3-ultra-550b-a55b",
    "nvidia/nemotron-3.5-super-vl-120b-a12b",
    "nvidia/nemotron-3.5-lightning-30b-a3b",
    "deepseek-ai/deepseek-v4.1-flash",
]


def probe_nvidia(env: dict[str, str]) -> dict:
    """Send one 8-token request to every NIM slug in the gateway config plus the
    known candidates, and report which ones THIS account can actually call.
    Free-tier traffic only; key never printed."""
    key = env.get("NVIDIA_API_KEY", "")
    if not key:
        return {"verdict": "KEY_MISSING"}
    cfg_ids = gateway_models()["nvidia"]
    st, body, _h, _dt = http("https://integrate.api.nvidia.com/v1/models", key)
    listed = []
    try:
        listed = [m["id"] for m in json.loads(body).get("data", [])]
    except Exception:
        pass
    family = [i for i in listed if any(w in i.lower() for w in ("nemotron", "deepseek", "kimi", "glm", "qwen3", "mistral-large", "maverick"))
              and "embed" not in i.lower() and "guard" not in i.lower() and "retriever" not in i.lower()]
    todo = list(dict.fromkeys(cfg_ids + NVIDIA_CANDIDATES + family[:10]))
    rows = []
    for m in todo:
        st2, body2, hdr2, dt2 = http("https://integrate.api.nvidia.com/v1/chat/completions", key,
                                    {"model": m, "messages": [{"role": "user", "content": "Reply with: ok"}], "max_tokens": 8}, timeout=45)
        ok = False
        try:
            ok = st2 == 200 and bool(json.loads(body2)["choices"])
        except Exception:
            pass
        rows.append({"model": m, "in_config": m in cfg_ids, "listed_by_/v1/models": m in listed,
                     "result": "WORKS" if ok else classify(st2, body2), "http": st2, "seconds": round(dt2, 1)})
    return {"models_listed_total": len(listed), "family_listed": family[:25], "probes": rows}



def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--static", action="store_true")
    ap.add_argument("--live", action="store_true")
    ap.add_argument("--infer", action="store_true")
    ap.add_argument("--allow-metered", action="store_true")
    ap.add_argument("--probe-nvidia", action="store_true", help="test every NIM slug with a real 8-token call")
    ap.add_argument("--env-file")
    ap.add_argument("--out", default="audit_report.json")
    a = ap.parse_args()
    if not (a.static or a.live):
        a.static = True
    report = {}
    fails = 0
    if a.static:
        report["static"] = static_audit()
        print("== STATIC (no network, no keys read)")
        for r in report["static"]:
            print(f"  [{r['status']:<4}] {r['check']}: {r['detail']}")
            fails += r["status"] == "FAIL"
    if a.live:
        env = load_env(a.env_file)
        report["live"] = live_audit(env, a.infer, a.allow_metered)
        print("== LIVE (key values are never printed)")
        for r in report["live"]:
            print(f"  {r['provider']:<12} {r['verdict']:<16} env={r['env']} cost={r['cost']}")
            for k in ("key_check", "verified_via", "inference", "models_missing_from_provider",
                      "closest_listed_models", "provider_lists", "detail"):
                if r.get(k):
                    print(f"      {k}: {r[k]}")
            fails += r["verdict"].startswith(("AUTH", "FAIL", "OUT_OF"))
    if a.probe_nvidia:
        report["nvidia_probe"] = probe_nvidia(load_env(a.env_file))
        pr = report["nvidia_probe"]
        print("== NVIDIA PROBE (real 8-token call per model; keys never printed)")
        if "probes" in pr:
            print(f"  models listed by your key: {pr['models_listed_total']}")
            for r in pr["probes"]:
                print(f"  {r['result']:<22} {r['model']}  (in_config={r['in_config']}, listed={r['listed_by_/v1/models']}, {r['seconds']}s)")
        else:
            print("  ", pr)
    Path(a.out).write_text(json.dumps(report, indent=1))
    print(f"\nreport written to {a.out} (contains no secrets)")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
