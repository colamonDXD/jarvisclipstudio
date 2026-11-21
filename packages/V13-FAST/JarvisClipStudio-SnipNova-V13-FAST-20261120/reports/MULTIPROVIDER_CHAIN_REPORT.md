# Multi-Provider Chain — Report (2026-11-12)

Lane: `feature-multiprovider-chain` (worktree `~/workspace/jcs-multiprovider`), off next-milestone @ `d3eb750`. **Not merged.**
Commits: `86d08a5` feat(gateway): rebuild fallback chains as one multi-provider ladder · `215e78c` docs(changelog): entry.
Scope honoured: gateway config + its tests + this key sheet. No prompt changes, no pipeline code changes. ₹0.

Luffy's ask (2026-11-12 ~14:57 IST): wire ALL the free/cheap brains together — Groq (in), NVIDIA NIM, OpenRouter-free, DeepSeek — work divided sensibly, Ollama as the FINAL fallback when any provider hits its limit. He inserts the keys himself when told exactly where.

---

## Step 0(a) — Gateway config as shipped at d3eb750 (verified in-tree)

Model list (`litellm_config.yaml`, unchanged by this lane):

| Alias | Model | Key |
|---|---|---|
| `clip-fast` | `openai/nvidia/nemotron-3-ultra-550b-a55b` via NIM | NVIDIA_API_KEY |
| `or-ultra-free` | `openrouter/nvidia/nemotron-3-ultra-550b-a55b:free` | OPENROUTER_API_KEY |
| `nim-deepseek` | `openai/deepseek-ai/deepseek-v4.1-flash` via NIM | NVIDIA_API_KEY |
| `clip-assured` | `groq/openai/gpt-oss-120b` | GROQ_API_KEY (+ `allowed_openai_params: reasoning_effort`) |
| `groq-20b` | `groq/openai/gpt-oss-20b` | GROQ_API_KEY (+ reasoning_effort allow-list) |
| `local-qwen` | `openai/qwen3:8b` via host Ollama :11434 | literal `ollama` |
| `deepseek-chat` | `openai/deepseek-chat` via api.deepseek.com | DEEPSEEK_API_KEY |
| `openrouter-free` | `openrouter/nvidia/nemotron-3-ultra-550b-a55b:free` | OPENROUTER_API_KEY |

Fallback chains BEFORE this lane (router walks in order):

| Alias called | Chain as shipped | Problem |
|---|---|---|
| `clip-fast` | or-ultra-free → nim-deepseek → clip-assured → **local-qwen (4th)** → deepseek-chat → openrouter-free | Local sits mid-chain; DeepSeek AFTER local; chain **ends on a remote** (openrouter-free); first hop duplicates openrouter-free exactly |
| `clip-assured` | groq-20b → **local-qwen (2nd)** → deepseek-chat → openrouter-free | **The premium route has NO NIM hop at all**; DeepSeek after local; **ends on a remote** |
| `groq-20b` | *(no chain defined)* | **Dead-ends on itself** after `num_retries: 1` — a code-called alias with no fallback at all |
| `local-qwen` | clip-assured → deepseek-chat → openrouter-free | No NIM, no groq-20b; ends on a remote |
| `or-ultra-free`, `nim-deepseek`, `deepseek-chat`, `openrouter-free` | *(no chains)* | Fallback-only targets; fine as terminal hops in flat lists, but nothing catches them if reached and failed under recursive walking |

Dead-end verdict: **yes, chains could dead-end without ever settling on local** — clip-assured's and clip-fast's walks terminate at openrouter-free (a rate-limited free pool — exactly the route most likely to be exhausted when reached), and groq-20b had no chain whatsoever.

## Step 0(b) — Compose passthroughs

- The base `docker-compose.yml` defines **no litellm service at all** (zero matches). The gateway service exists only in `compose.override.yml`.
- In-tree `compose.override.yml` → `litellm.environment` passes **all five**: `GROQ_API_KEY`, `NVIDIA_API_KEY`, `OPENROUTER_API_KEY`, `DEEPSEEK_API_KEY`, `LITELLM_MASTER_KEY` (pinned by `test_compose_passes_all_gateway_keys`).
- backend/worker/ai-worker receive only `GROQ_API_KEY` + `ANALYSIS_MODELS` (+ OPENAI_API_KEY/BASE_URL) — correct: the provider keys are gateway-side only.
- **His LIVE wrinkle:** the installer preserves his live `compose.override.yml`, so the in-tree file does not replace it. His live file already carries the GROQ + NVIDIA + OPENROUTER litellm passthroughs (Nov 11 wiring); the **DEEPSEEK_API_KEY passthrough line is the known missing one** he must add by hand (exact lines in the Key Sheet below). No other service needs any of the three keys.

## Step 0(c) — Which aliases the code actually calls

Model strings are `openai:<alias>` when routed via the gateway (tests mirror the production-shaped config: `ANALYSIS_MODELS=openai:clip-fast,openai:clip-assured`, `ANALYSIS_FALLBACK_MODEL=openai:local-qwen`):

| Alias | Called by |
|---|---|
| `clip-fast` | Primary analysis (volume), long-form route, classification candidate |
| `clip-assured` | Second analysis model; premium/rerank final passes (brain upgrade sends `reasoning_effort=high` to clip-fast + clip-assured) |
| `groq-20b` | `PERMITTED_ALIASES = {"clip-assured", "groq-20b", "local-qwen"}` in `content_requirements_llm.py`; analysis rotation |
| `local-qwen` | `ANALYSIS_FALLBACK_MODEL` default, `LOCAL_LLM` default (provider manager), classification tail |
| `or-ultra-free`, `nim-deepseek`, `deepseek-chat`, `openrouter-free` | **Never code-called** (zero references in `backend/src`) — fallback targets only |

Classification's app-side candidate chain (`classify.py::_classify_candidates`, strongest-first) resolves to these same aliases, so the gateway chains above are the whole story for limit-fallback.

## Step 0(d) — GPT-6 Luna free-API re-check (one live check, 2026-11-12)

**Verdict stands: app-only free, API paid.** Fresh sources (Oct–Nov 2026 crawls) agree: OpenAI lists GPT-6 Luna pay-as-you-go at $0.10 in / $0.50 out per 1M ("No GPT model is priced free" in OpenAI's own table); OpenRouter lists Luna as a paid model ($0.10/$0.50; the 5.6 Luna variant $0.20/$1.20) and its free pool is only `:free`-tagged models — no `luna:free` route exists. Luna is therefore NOT added to any chain; it cannot be powered by a free key.

---

## What changed — the ladder

`litellm_config.yaml` `router_settings.fallbacks` only. **Model list byte-identical** → the `reasoning_effort` allow-list on both Groq routes is untouched, and the brain lane's provider-gating/degrade behaviour is unaffected by construction.

| Alias called | New chain |
|---|---|
| `clip-assured` (premium) | clip-fast (NIM ultra) → nim-deepseek (NIM DeepSeek-V4) → openrouter-free → deepseek-chat → **local-qwen FINAL** |
| `clip-fast` (volume) | nim-deepseek → clip-assured → groq-20b → openrouter-free → deepseek-chat → **local-qwen FINAL** |
| `groq-20b` (volume) | clip-assured → clip-fast → nim-deepseek → openrouter-free → deepseek-chat → **local-qwen FINAL** |
| `local-qwen` | clip-assured → clip-fast → nim-deepseek → groq-20b → openrouter-free → deepseek-chat *(documented exception: consulted only when local itself failed; cannot contain itself)* |
| `or-ultra-free` | clip-assured → clip-fast → nim-deepseek → groq-20b → deepseek-chat → **local-qwen FINAL** |
| `nim-deepseek` | clip-fast → clip-assured → groq-20b → openrouter-free → deepseek-chat → **local-qwen FINAL** |
| `deepseek-chat` | clip-assured → clip-fast → nim-deepseek → groq-20b → openrouter-free → **local-qwen FINAL** |
| `openrouter-free` | clip-assured → clip-fast → nim-deepseek → groq-20b → deepseek-chat → **local-qwen FINAL** |

Design notes:
- Premium order per Luffy's spec: Groq 120b → NIM → OpenRouter-free → DeepSeek direct → Ollama FINAL. A chain never dead-ends on a remote.
- Work division: volume scanning stays on NIM/Groq-20b primaries; the premium final passes stay on clip-assured (Groq 120b) and only leave Groq when Groq itself fails or limits.
- `or-ultra-free` was dropped from all chains: it is the *identical deployment* to `openrouter-free` (same model, same key) — the old clip-fast chain burned its first hop re-trying the same pool. It remains in the model list (pinned, harmless).
- DeepSeek sits after every free route and immediately before local in every chain that contains it (its own chain excepted — a route cannot be its own fallback; it still ends at local).
- Inertness: every remote route's `api_key` is an `os.environ/<KEY>` reference. Key unset → that deployment fails auth only if the walk ever reaches it, the router cools it down (60 s), and the walk continues. The gateway boots and primaries are unaffected — same semantics the Nov 11 routes shipped with, now pinned by test.

## Proof

- `test_litellm_gateway_config.py` — **14 passed** (was 11). Updated: exact chain map (8 chains), docstring. New pins: every alias has a chain + no self-fallback; every chain ends at `local-qwen` (local-qwen's own chain the documented exception); DeepSeek sits after all free routes and immediately before local; **no Gemini model anywhere in the gateway** (model strings + raw file scan). Model-list and key-plumbing tests unchanged and green.
- Neighbours (serial, same session): `test_brain_upgrade.py` + `test_provider_manager.py` + `test_content_requirements_llm.py` + `test_auto_director_classify_chain.py` + `test_config_default_llm.py` — **110 passed / 0 failed**.
- Config-load smoke (structural; litellm is not installed in the sandbox venv — the gateway runs in its own container): YAML parses, every code-called alias's simulated all-remote-failure walk terminates at `local-qwen` with no repeated hop, every hop names an existing deployment, all remote keys are `os.environ/*` references. **SMOKE PASS.**
- `changelog.test.ts` — **4/4** after the CHANGELOG entry (borrowed node_modules symlink, removed after).
- NOT proven here (no keys in sandbox, by design): a live call down any new chain; the litellm container booting this exact config (package-time check); per-provider limit behaviour under real load. All user-machine validation pending.

Feature state: IMPLEMENTED + INDIVIDUALLY TESTED in-branch / NOT MERGED / USER-MACHINE VALIDATION PENDING.

---

## KEY SHEET — for Luffy (plain words)

**What this does:** your clipper's AI now has a full backup ladder. Groq does the main work. If Groq hits its limit, NVIDIA's free service tries next, then OpenRouter's free models, then DeepSeek, and if EVERYTHING online fails, your own PC's local AI (Ollama) finishes the job. No more failed generations just because one service said "limit reached".

**You need 3 free keys. Groq you already have. Get these:**

1. **NVIDIA NIM** — free. Sign up at build.nvidia.com, create an API key. (Free developer tier, rate-limited — that's fine, it's a backup.)
2. **OpenRouter** — free account at openrouter.ai, create an API key. The models we use from it are the `:free` ones: ₹0, about 50 requests/day on a fresh account. We only use it as a deep backup, so that's plenty.
3. **DeepSeek** — sign up at platform.deepseek.com, create an API key. Honest note: DeepSeek is **pay-per-token after its reported one-time starter grant** (new accounts reportedly get a free token grant — not guaranteed). The prices are tiny (about $0.28 per million input tokens), and it's the LAST online backup before your local model, so it would only ever spend paise. The other two routes are fully free. **GPT Luna is NOT in this ladder** — checked again today: it's free only inside the ChatGPT app; its API costs money and there is no free Luna key. Nothing I can wire changes that.

**Step 1 — add these 3 lines to your `.env`** (in `D:\EditedTool\JarvisClipStudio\.env`, with your key pasted after each `=` — type them there yourself, never in chat):

```
NVIDIA_API_KEY=
OPENROUTER_API_KEY=
DEEPSEEK_API_KEY=
```

**Step 2 — one line in your `compose.override.yml`.** Your installed file is kept as-is by installers, so this one is by hand. Open `D:\EditedTool\JarvisClipStudio\compose.override.yml`, find the `litellm:` section, and make sure its `environment:` list has all five of these lines (yours already has most of them — **the DeepSeek line is the one known to be missing**; add any line that isn't there, don't paste a line twice):

```yaml
    environment:
      - GROQ_API_KEY=${GROQ_API_KEY}
      - NVIDIA_API_KEY=${NVIDIA_API_KEY}
      - OPENROUTER_API_KEY=${OPENROUTER_API_KEY}
      - DEEPSEEK_API_KEY=${DEEPSEEK_API_KEY}
      - LITELLM_MASTER_KEY=${LITELLM_MASTER_KEY}
```

**Step 3 — restart the app** (PowerShell, in `D:\EditedTool\JarvisClipStudio`):

```
docker compose up -d
```

**Timing, honestly:** you can do steps 1–3 right now on your current build — the keys already wake up the backup routes your v4 has (DeepSeek + OpenRouter sit at the ends of its chains). The new, better ladder ORDER (NVIDIA in the premium chain, local model always last, no dead ends) arrives in the next package after this branch merges — Sanji will say when it's in a build you can install.

---

## Note for the parent — Luffy's campaign-screenshot ask (14:57 IST)

Not built by this lane (out of scope), but the machinery he is asking for largely EXISTS — answer from this, don't promise new work blindly:

- Screenshots he sends **in chat** are read by Sanji directly (as with today's diamonds/Botswana brief image) — that part needs no feature.
- In-app, the Content Requirements system (merged 2026-11-09, in RC + next-milestone) already does what this brief needs: brief library + `POST /briefs/extract` (paste brief TEXT → structured requirements), hard range enforcement, and a post-render audit. The brief concepts map 1:1 onto today's screenshot: key timestamps/topics to include, **forbidden ranges** (his "0:47–0:53 must not be included in any content"), must-connect-to-message topics (natural diamonds), and avoid/flag lists (licence plates, third-party branding, copyright music, sensitive topics).
- Gap to state honestly: `briefs/extract` takes text, not an image file — today the flow is: he sends the screenshot in chat, Sanji reads it and hands him the brief text to paste (or he copies the brief text from the campaign page). An in-app image-upload-for-briefs would be a NEW feature (needs OCR/vision wiring) — his call, not promised here.
