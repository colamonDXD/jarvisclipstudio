# MODEL CONFIG AUDIT — NVIDIA ladder names, DeepSeek direct name, HF_TOKEN pass-through

*Lane: MODEL-CONFIG AUDIT (research + premise audit only — no code changed, no commits).
Tree audited read-only: `~/workspace/jcs-next` @ `c7c5b3ca`.
Date: 2026-11-17. Trigger: Claude Code Lane A report (via Luffy, 2026-11-17) flagging three config issues.
Method: full-tree grep + file reads for current state; public web sources only for external evidence (no account logins, ₹0).*

---

## Headline verdicts

| # | Claude's claim | Verdict |
|---|---|---|
| 1 | Five NVIDIA names need checking; the ladder's NVIDIA DeepSeek entry name is not in Luffy's NVIDIA list | **Slugs are correct; no NVIDIA slug correction is supported by evidence.** Both *armed* NVIDIA IDs exist in NVIDIA's public catalog. Of the five flagged names, 2 are on current free-endpoint probes, 3 are unarmed bake-off candidates whose hosted/free status is unverified or likely retired — exactly as the tree's own comments already mark them. What Luffy's *account* list contains cannot be proved or disproved from public sources (see §1.4). |
| 2 | DeepSeek account lists `deepseek-flash` + `deepseek-v4-pro`, not `deepseek-chat` | **Confirmed by DeepSeek's own current public docs.** `deepseek-chat` is no longer a documented public model name (deprecation date 2026/07/24 has passed). `deepseek-flash` and `deepseek-v4-pro` are the two current documented names. A correction spec is given in §2.3 — but note the route is also dead for zero credit, and a slug change alone does not revive it; top-up is Luffy's decision only. |
| 3 | `HF_TOKEN` still isn't passed through the compose files | **Factually true — and presently low-impact.** No compose file, no `.env.example`, and no backend code references `HF_TOKEN` at all. But nothing in the tree needs it today: pyannote diarization is explicitly **not wired** (in-tree registry), and every current Hugging Face download is a public model. The pass-through is future-proofing for the pyannote wiring + relief from anonymous HF rate limits. Exact minimal change spec in §3.3. **Not applied — spec only.** |

---

## ITEM 1 — NVIDIA ladder model names

### 1.1 Current state in the tree (every NVIDIA NIM model ID actually used)

All hosted-NVIDIA routes live in one file, `litellm_config.yaml`, all pointing at
`api_base: https://integrate.api.nvidia.com/v1` with `api_key: os.environ/NVIDIA_API_KEY`.
The LiteLLM gateway receives that key via `compose.override.yml:69` (litellm service only).

**Armed (production ladder — something actually calls these):**

| Alias | Model ID (as sent to NVIDIA) | Location |
|---|---|---|
| `clip-fast` | `nvidia/nemotron-3-ultra-550b-a55b` (as `openai/nvidia/nemotron-3-ultra-550b-a55b`) | `litellm_config.yaml:4` |
| `nim-deepseek` | `deepseek-ai/deepseek-v4.1-flash` (as `openai/deepseek-ai/deepseek-v4.1-flash`) | `litellm_config.yaml:13` |

**Unarmed bake-off candidates (pre-registered 2026-11-12; no production chain falls back to them and no app code calls them — `litellm_config.yaml:69-76` comment, pinned by tests):**

| Alias | Model ID | Location | In-tree verification flag |
|---|---|---|---|
| `nim-glm-flash` | `z-ai/glm-5.3-flash` | `litellm_config.yaml:82` | verified |
| `nim-kimi-k2` | `moonshotai/kimi-k2.6` | `litellm_config.yaml:87` | verified (2026-11-12) |
| `nim-mistral-large4` | `mistralai/mistral-large-4-1t-a52b` | `litellm_config.yaml:92` | verified |
| `nim-qwen3-235b` | `qwen/qwen3-235b-a22b` | `litellm_config.yaml:98` | **UNVERIFIED** (comment L97) |
| `nim-llama4-maverick` | `meta/llama-4-maverick-17b-128e-instruct` | `litellm_config.yaml:104` | **UNVERIFIED** (comment L103) |
| `nim-deepseek-pro` | `deepseek-ai/deepseek-v4-pro` | `litellm_config.yaml:110` | **PARTIALLY verified** (comment L107-108) |

The same eight IDs are mirrored in `tools/nim-bakeoff/bakeoff.py:1061` (the `BAKEOFF_MODELS` list),
`tools/nim-bakeoff/offline_evidence.json`, and `backend/tests/unit/test_litellm_gateway_config.py:83-218`
(pinned expectations). Claude's five flagged names map to: DeepSeek V4.1 Flash → `nim-deepseek` (armed);
Kimi K2.6, Qwen3 235B, Llama 4 Maverick, DeepSeek V4 Pro → four of the six unarmed candidates.

**Not NVIDIA-hosted (do not confuse):** `nvidia/nemotron-3-ultra-550b-a55b:free` at
`litellm_config.yaml:9,40`, `backend/src/config.py:19`, `docker-compose.yml:194,389`, `.env.example:59`
is **OpenRouter's** ID for the same weights (provider `openrouter/…`, key `OPENROUTER_API_KEY`), not an
NVIDIA endpoint ID.

**Adjacent gap found:** `.env.example` (and `backend/.env.example`) contains **no `NVIDIA_API_KEY` entry
at all** — the key the whole NIM ladder depends on is documented nowhere in the template; it only appears
in `compose.override.yml:69` and `litellm_config.yaml`. Documentation-only fix suggested in §1.3.

### 1.2 External evidence (public catalog, checked 2026-11-17)

Two independent, recently-probed free-endpoint lists were used as the "callable today" yardstick, plus
NVIDIA's own model/API-reference pages for catalog existence:

- **mvalentsev/awesome-free-ai-coding — NVIDIA NIM page**, "live — last verified by a probe on
  **2026-11-16**": callable free IDs include `nvidia/nemotron-3-ultra-550b-a55b`,
  `deepseek-ai/deepseek-v4.1-flash`, `z-ai/glm-5.3-flash` (registry regenerated 2026-11-17).
  URL: https://mvalentsev.github.io/awesome-free-ai-coding/providers/nvidia-nim/
  Its own caveat: "`/v1/models` also lists older IDs without a free mark, so catalog membership alone
  does not establish free access."
- **llmperks.com/nvidia** (crawled 2026-11-17, "18 found"): free list includes
  `nvidia/nemotron-3-ultra-550b-a55b`, `z-ai/glm-5.3-flash`, `mistralai/mistral-large-4-1t-a52b`.
  It does **not** list `deepseek-ai/deepseek-v4.1-flash` — while llmperks' own DeepSeek V4.1 Flash page
  (refreshed 2026-11-12) lists NVIDIA Build `deepseek-ai/deepseek-v4.1-flash` as a free route.
  The two llmperks pages disagree with each other; the mvalentsev live probe (2026-11-16) includes it.
  URL: https://llmperks.com/nvidia and https://llmperks.com/blog/deepseek-v4-1-flash-free-api

Per-ID findings:

| Model ID | Public catalog exists? | On a current (Nov 16-17) free-endpoint probe? | Evidence |
|---|---|---|---|
| `nvidia/nemotron-3-ultra-550b-a55b` | **Yes** | **Yes** (both probes) | https://build.nvidia.com/nvidia/nemotron-3-ultra-550b-a55b/modelcard ; llmperks list #3; mvalentsev callable list |
| `deepseek-ai/deepseek-v4.1-flash` | **Yes** | **Yes** (mvalentsev 2026-11-16; llmperks blog page) / No (llmperks 18-list) — probes disagree, see above | https://build.nvidia.com/deepseek-ai/deepseek-v4.1-flash/modelcard ; a 2026-11-13 NVIDIA forum thread reports the hosted endpoint *stalling* (health, not existence): https://forums.developer.nvidia.com/t/deepseek-v4-1-flash-stalls-in-playground-and-api-kimi-k2-6-returns-404/388998 |
| `z-ai/glm-5.3-flash` | **Yes** | **Yes** (both probes; free since Sep 2026) | https://build.nvidia.com/z-ai/glm-5-3-flash/modelcard (page slug hyphenates the dots; the API ID keeps them) |
| `moonshotai/kimi-k2.6` | **Yes** (API reference + Build release 2026-04-29) | **No** — absent from both Nov probes; forum reports it appearing in `/v1/models` but chat completions returning **404 function-not-found** (2026-11-13) | https://docs.api.nvidia.com/nim/reference/moonshotai-kimi-k2-6 ; forum thread above |
| `mistralai/mistral-large-4-1t-a52b` | **Yes** (Build 2026-10-29) | **Yes** (llmperks 18-list #14; agentdeals: "Free Endpoint on build.nvidia.com as of 2026-10-29") | https://docs.api.nvidia.com/nim/reference/mistralai-mistral-large-4-1t-a52b ; https://agentdeals.dev/vendor/nvidia-nim . Note case: the NIM API ID is lowercase as in the tree; the *vendor checkpoint* name `mistralai/Mistral-Large-4-1T-A52B` is a different string for a different purpose — the tree's lowercase form is correct for `integrate.api.nvidia.com`. |
| `qwen/qwen3-235b-a22b` | **ID is real in the NIM ecosystem** (listed as a known-good build.nvidia.com ID by third-party NIM clients, e.g. https://osp.net/docs and PyPI `nim-claude-proxy`) | **No** — absent from both Nov probes, matching the tree's own 2026-11-12 finding | No NVIDIA-hosted free-endpoint evidence found. The `-instruct-2507` / `-thinking-2507` variants are *different models*; no evidence supports renaming to them. |
| `meta/llama-4-maverick-17b-128e-instruct` | **Yes as a NIM model** — NVIDIA NIM-for-VLMs docs list "Llama 4 Maverick 17B 128E Instruct" and use the exact ID in API examples | **No** — absent from both Nov probes | https://docs.nvidia.com/nim/vision-language-models/1.4.1/support-matrix.html ; https://docs.nvidia.com/nim/vision-language-models/1.4.1/examples/llama-4-maverick-17b-128e-instruct/api.html . This proves the *self-hosted NIM container* ID; it does not prove a hosted free endpoint at `integrate.api.nvidia.com`. |
| `deepseek-ai/deepseek-v4-pro` | **Was** in the catalog (API reference exists) | **No** — absent from both Nov probes; retirement reported | https://docs.api.nvidia.com/nim/reference/deepseek-ai-deepseek-v4-pro ; agentdeals (verified 2026-11-02) lists `deepseek-ai/deepseek-v4-pro` among IDs NVIDIA retired in 2026 (2026-08-07) and `deepseek-ai/deepseek-v4-pro-0813` on 2026-09-14; an Oct-2026 check reported "the V4 Pro endpoint is gone from the catalogue: the only DeepSeek models on Build are V4.1 Flash and an old Coder build" (https://www.orcarouter.ai/blog/deepseek-v4-pro-free). A Sep-2026 source had verified a free endpoint *before* those dates — the timeline is consistent with a retirement, not with the slug never existing. |

### 1.3 Verdict + correction spec

- **Armed routes (`clip-fast`, `nim-deepseek`): no correction supported by evidence.** Both slugs are
  exact current public catalog IDs; `nim-deepseek`'s slug is additionally on a 2026-11-16 live free probe.
  Claude's "that model name isn't in your NVIDIA list" is **not** a slug error that public evidence can
  support — if his account list omits it, that is an account/entitlement listing difference (see §1.4),
  not a wrong name in the tree. **Do not rename `deepseek-ai/deepseek-v4.1-flash`.**
- **Candidates:** no slug changes. The tree already carries the correct epistemic labels
  (`litellm_config.yaml:97,103,107` + `docs/registries/MODEL_REGISTRY.md:56-58`): qwen/llama = UNVERIFIED,
  deepseek-pro = PARTIAL. Today's public evidence *strengthens* two of those labels:
  - `nim-deepseek-pro` (`litellm_config.yaml:110`): hosted endpoint **likely retired** — keep unarmed;
    the bake-off probe should expect a 404/410 and drop it. Its successor on NVIDIA is
    `deepseek-ai/deepseek-v4.1-flash`, which already holds the `nim-deepseek` rung — no replacement slug needed.
  - `nim-kimi-k2` (`litellm_config.yaml:87`): catalog-real but currently **404ing on the hosted API** per
    Nov-2026 forum reports and absent from current free probes — keep unarmed, probe-and-skip stands.
    No closer current ID exists (the newer `moonshotai/kimi-k3` was itself retired 2026-10-15 per the
    mvalentsev changelog).
- **Documentation-only correction (optional, not a slug change):** add the missing key to the template —
  `.env.example`, after L62 (`DEEPSEEK_API_KEY=` block): add `NVIDIA_API_KEY=` with a comment that it
  powers the gateway's NVIDIA NIM routes (`clip-fast`, `nim-deepseek`) via `compose.override.yml`'s
  litellm service. Old value: *(absent)* → New value: `NVIDIA_API_KEY=`.

### 1.4 What public evidence can and cannot prove about Luffy's account

Public sources prove: a slug exists in NVIDIA's public catalog, and whether third-party probes found it
carrying the public "Free Endpoint" mark on a given date. They **cannot** prove: what GET
`https://integrate.api.nvidia.com/v1/models` returns for *his* key, his org's entitlements, or his
account's model list — NVIDIA forum evidence shows newer models can be listed yet return
404 "function not found" per-account (the "Public API Endpoints" entitlement dispute,
https://forums.developer.nvidia.com/t/newer-nim-models-kimi-k2-6-deepseek-v4-pro-hang-indefinitely-or-404-possible-missing-public-api-endpoints-permission/377777).
The only decisive check is a live probe with his own key — which is exactly the bake-off protocol's
step 1 (`tools/nim-bakeoff/bakeoff.py:1044` already prints that curl). That check is his machine, his key;
this lane did not and cannot run it.

---

## ITEM 2 — DeepSeek direct model name (`deepseek-chat`)

### 2.1 Current state in the tree

| Surface | Value | Location |
|---|---|---|
| LiteLLM alias `deepseek-chat` → model | `openai/deepseek-chat` @ `https://api.deepseek.com`, key `os.environ/DEEPSEEK_API_KEY` | `litellm_config.yaml:33-37` (model at L35) |
| Template | `DEEPSEEK_API_KEY=` + comment "powers the gateway's last-resort deepseek-chat fallback route" | `.env.example:61-62` |
| Registry doc | alias `deepseek-chat`, slug `deepseek-chat`, "Strongest brain; first hop of every chain" | `docs/registries/MODEL_REGISTRY.md:21` |
| Routing doc | DeepSeek direct, model `deepseek-chat` | `AI_MODEL_ROUTING.md:14` |
| Pinned test expectation | `deepseek-chat` → `openai/deepseek-chat` | `backend/tests/unit/test_litellm_gateway_config.py:134-138` |

This alias leads every fallback chain (`litellm_config.yaml:138-155`) — it is the ladder's first hop.

### 2.2 External evidence (DeepSeek's public API docs)

- **Official docs, "Your First API Call"** (platform.deepseek.com, page crawled **2026-11-17**):
  the `model` row lists exactly two names — **`deepseek-flash`** and **`deepseek-v4-pro`**.
  Footnote: "`deepseek-flash` … is now served by DeepSeek-V4.1-Flash-1017 … The legacy names
  `deepseek-v4-flash` and `deepseek-v4-flash-vision-exp` are still accepted, but the corresponding
  models have been retired; their requests are served by DeepSeek-V4.1-Flash-1017 and billed at the
  Flash price." `deepseek-chat` appears **nowhere** on the current page.
  URL: https://platform.deepseek.com/api-docs/function_calling
- **The same official page's earlier cached version** (search-index copy) listed
  "`deepseek-v4-flash` `deepseek-v4-pro` `deepseek-chat` (to be deprecated on 2026/07/24)
  `deepseek-reasoner` (to be deprecated on 2026/07/24)" and explained the two legacy names
  "correspond to the non-thinking mode and thinking mode of `deepseek-v4-flash`, respectively."
  That deprecation date is now in the past, and the current page has dropped both names.
- **Independent guides checked against the official docs** agree: deepseek.ai's API guide
  (last verified **2026-11-03**): "There are two current model names … `deepseek-flash` ·
  `deepseek-v4-pro`"; "`deepseek-chat` · `deepseek-reasoner` — Scheduled for discontinuation on
  24 July 2026 and no longer in the official model list. Use instead: `deepseek-flash` (thinking off
  for the old chat behaviour, on for reasoner)". URL: https://deepseek.ai/deepseek-api
  tokensave.app (checked 2026-10-12): "DeepSeek retired `deepseek-chat` and `deepseek-reasoner` in July".

**Verdict per name:**

| Name | Verdict |
|---|---|
| `deepseek-chat` | **Not documented** (current public docs). Formerly a documented alias (non-thinking mode of the Flash line) with a published deprecation date of 2026/07/24, since removed from the official model list. Claude's report matches the public record. Whether a specific account's `/v1/models` might still return it is **account-specific-unknown**, but no one should build on a name the vendor has delisted. |
| `deepseek-flash` | **Documented** — current primary name; served by DeepSeek-V4.1-Flash-1017; 1M context; supports the thinking toggle + `reasoning_effort` (official docs' own curl example uses both). |
| `deepseek-v4-pro` | **Documented** — current flagship name; served by DeepSeek-V4-Pro-0813. (One Sep-2026 announcement caused secondary-source confusion about Pro requests being temporarily served by Flash; DeepSeek's current pricing/quick-start pages still list `deepseek-v4-pro` as a live, separately-priced model.) |

### 2.3 Correction spec (for Sanji to apply later — NOT applied here)

Precondition honesty: this route is currently dead for **two independent reasons** — (a) the delisted
model name, and (b) the account's **zero credit** (Claude's report; the test request returned
"out of credit"). Fixing (a) without (b) changes nothing live. Top-up is Luffy's decision only
(free-only rule stands); the app runs ₹0 today with Groq `gpt-oss-120b` effectively primary and the
ladder failing over as designed. The spec below makes the config *correct-but-dormant* so the route
revives correctly if he ever tops up.

The alias name `deepseek-chat` is only a LiteLLM-internal label — **keep the alias, change only the
model slug**, so no fallback chain (`litellm_config.yaml:138-155`) or chain-order test changes:

| # | File : line | Old value | New value |
|---|---|---|---|
| 1 (required) | `litellm_config.yaml:35` | `model: openai/deepseek-chat` | **Option A (role-preserving):** `model: openai/deepseek-v4-pro` — keeps the slot's documented role ("strongest brain, first hop of every chain", `MODEL_REGISTRY.md:21`) at Pro prices. **Option B (cost-first):** `model: openai/deepseek-flash` — cheapest direct route, but it duplicates the `nim-deepseek` rung's weights (same V4.1 Flash family, direct instead of via NVIDIA) and demotes the first hop from flagship to Flash. **Recommendation: Option A** if the slot is ever revived, because every chain's ordering was designed around this hop being the strongest brain. |
| 2 (required with #1) | `backend/tests/unit/test_litellm_gateway_config.py:136` | `"model": "openai/deepseek-chat"` | Match #1's chosen slug. (The pinned expectation must move with the config, per repo test discipline — update, never weaken.) |
| 3 (docs, same change) | `docs/registries/MODEL_REGISTRY.md:21` | slug cell `deepseek-chat` | Chosen slug from #1; keep the alias cell unchanged. |
| 4 (docs, same change) | `AI_MODEL_ROUTING.md:14` | model cell `deepseek-chat` | Chosen slug from #1. |
| 5 (docs, same change) | `.env.example:61` | comment "…last-resort deepseek-chat fallback route" | Name the chosen slug instead of the delisted one (comment wording only; `DEEPSEEK_API_KEY=` at L62 unchanged). |

**NOT VERIFIED — must be checked live after any swap:** the premium passes send `reasoning_effort`;
the official docs show `deepseek-flash` accepting `thinking` + `reasoning_effort`, but no public doc
in this audit proves the *gateway's* parameter pass-through behaves identically for
`deepseek-v4-pro`. First live call after a swap is the test. Also unproven publicly: whether
Luffy's account `/v1/models` returns exactly the two documented names — Claude reported it does;
that is consistent with, but not proved by, the public docs.

---

## ITEM 3 — HF_TOKEN compose pass-through

### 3.1 Current state — the chain, link by link

| Link in the chain | State | Evidence (file:line) |
|---|---|---|
| Backend code reads `HF_TOKEN` / `HUGGING_FACE_HUB_TOKEN` explicitly | **Absent — zero references in the whole tree** (grep for `HF_TOKEN`, `HUGGING_FACE_HUB_TOKEN`, `HUGGINGFACE_HUB_TOKEN`: no hits anywhere) | — |
| Pyannote diarization (the gated-model use case a token exists for) | **Not wired.** "Speaker diarization via pyannote is **not wired**: no pyannote dependency or code exists in this tree. An HF token is banked in the user's `.env` for the future wiring (post-release queue)." No `pyannote` in `backend/pyproject.toml`. | `docs/registries/MODEL_REGISTRY.md:118-120`; `AI_MODEL_ROUTING.md:20` ("a token is banked for the future pyannote diarization wiring") |
| Code that *would* implicitly use a token if one were in the process env | `huggingface-hub` 1.13.0 is installed (transitive via faster-whisper / demucs / kokoro / transformers — `backend/uv.lock`), and the library itself reads `HF_TOKEN`/`HUGGING_FACE_HUB_TOKEN` from the environment when downloads are made without an explicit token argument. In-tree download call sites, all token-less, all **public** models today: SigLIP veto `AutoModel.from_pretrained(MODEL_ID)` / `AutoTokenizer.from_pretrained` / `hf_hub_download(MODEL_ID, "tokenizer.json")` with `MODEL_ID = "google/siglip-base-patch16-224"`; faster-whisper `WhisperModel(...)` (Systran public repos); Kokoro pipeline; Demucs subprocess. | `backend/src/media/broll_vision.py:63,404,498,500-503`; `backend/src/media/transcription.py:392`; `backend/src/media/voiceover.py:198-200` (sets `HF_HOME` only); `backend/src/media/separation.py:347-352` (subprocess `env = dict(os.environ)` + `HF_HOME` — i.e. it *would* forward `HF_TOKEN` to Demucs if the worker process had it) |
| What the code sets itself | Only `HF_HOME` (the cache dir), in three places — never a token. | `backend/src/media/broll_vision.py:393`; `backend/src/media/voiceover.py:200`; `backend/src/media/separation.py:352` |
| `.env.example` lists `HF_TOKEN` | **No** — neither the root template nor `backend/.env.example` contains it (grep: no hits). The token exists only in Luffy's own `.env` on his machine. | `.env.example` (whole file); `backend/.env.example` (whole file) |
| `docker-compose.yml` passes it to any service | **No** — no occurrence in the file. Backend env block L123-269; worker env anchor `&worker-environment` L318ff; ai-worker inherits it via `*worker-environment` at L483. | `docker-compose.yml:123,318,483` |
| `compose.override.yml` passes it | **No** — no occurrence. (This is where the other optional provider keys are passed: backend L4-9, worker L21-26, ai-worker L47-52, litellm L68-71.) | `compose.override.yml:4-9,21-26,47-52,68-71` |

So Claude's statement is literally correct — the token in Luffy's `.env` reaches **no container** —
but the consequence today is nil: no gated model is downloaded by any current code path, and public
downloads work anonymously (subject to HF's anonymous rate limits). The token's real consumers are
(1) the future pyannote wiring — pyannote's models are gated on Hugging Face, needing **both** the
token pass-through **and** Luffy's terms-accept click (already owed, non-urgent) — and
(2) authenticated (higher) HF rate limits for the existing first-download paths.

### 3.2 Which services would need it

- **worker** — runs transcription (faster-whisper), separation (Demucs), B-roll veto (SigLIP),
  voiceover (Kokoro), and would run any future pyannote diarization. **Needs it.**
- **backend** — same source tree mounted; in-process HF loads can occur on API-side paths. **Needs it**
  (and matches the existing pattern: PIXABAY/COVERR are passed to backend + worker + ai-worker alike).
- **ai-worker** — shares the worker environment; included for parity with the other optional keys.
- **litellm / frontend / mcp / postgres / redis — do NOT need it** (no HF downloads in the gateway or UI).

### 3.3 Exact minimal correction spec (NOT applied — spec only)

Two files, three insertions in the base compose (the worker anchor covers ai-worker automatically),
plus the template entry, plus the override entries that the shipped override pattern requires:

| # | File : anchor | Insert |
|---|---|---|
| 1 | `.env.example`, after L62 (`DEEPSEEK_API_KEY=`) | New block: `# Hugging Face (optional): banked for the future pyannote diarization wiring; also authenticates the public model downloads (Whisper / SigLIP / Kokoro / Demucs) so they are not anonymous-rate-limited. Needs the compose pass-through below to reach the containers.` + `HF_TOKEN=` |
| 2 | `docker-compose.yml`, backend `environment:`, after L153 (`- ASSEMBLY_AI_API_KEY=${ASSEMBLY_AI_API_KEY}`) | `- HF_TOKEN=${HF_TOKEN:-}` |
| 3 | `docker-compose.yml`, worker `environment: &worker-environment`, after L348 (`- ASSEMBLY_AI_API_KEY=${ASSEMBLY_AI_API_KEY}`) | `- HF_TOKEN=${HF_TOKEN:-}` — ai-worker inherits this via `*worker-environment` (L483); no third compose edit needed. |
| 4 | `compose.override.yml` (the file that actually carries optional keys on Luffy's install pattern), after each `COVERR_API_KEY` line — backend L9, worker L26, ai-worker L52 | `- HF_TOKEN=${HF_TOKEN}` (three one-line insertions, mirroring the PIXABAY/COVERR pattern that `.env.example:104-117` documents). |

Do **not** add it to the litellm service (L66-74 of the override) — the gateway downloads nothing
from Hugging Face. No backend code change is required for the pass-through to take effect on the
existing download paths (the `huggingface-hub` library reads the env var itself); a code change is
only owed later, as part of the pyannote wiring itself.

---

## Appendix — sources

NVIDIA / catalog:
- https://build.nvidia.com/nvidia/nemotron-3-ultra-550b-a55b/modelcard
- https://build.nvidia.com/deepseek-ai/deepseek-v4.1-flash/modelcard
- https://build.nvidia.com/z-ai/glm-5-3-flash/modelcard
- https://docs.api.nvidia.com/nim/reference/moonshotai-kimi-k2-6
- https://docs.api.nvidia.com/nim/reference/deepseek-ai-deepseek-v4-pro
- https://docs.api.nvidia.com/nim/reference/mistralai-mistral-large-4-1t-a52b
- https://docs.nvidia.com/nim/vision-language-models/1.4.1/support-matrix.html
- https://mvalentsev.github.io/awesome-free-ai-coding/providers/nvidia-nim/ (live probe 2026-11-16)
- https://llmperks.com/nvidia (crawled 2026-11-17) and https://llmperks.com/blog/deepseek-v4-1-flash-free-api
- https://agentdeals.dev/vendor/nvidia-nim (verified 2026-11-02; retirement list)
- https://forums.developer.nvidia.com/t/deepseek-v4-1-flash-stalls-in-playground-and-api-kimi-k2-6-returns-404/388998

DeepSeek:
- https://platform.deepseek.com/api-docs/function_calling ("Your First API Call", crawled 2026-11-17)
- https://deepseek.ai/deepseek-api (independent guide, verified against official docs 2026-11-03)
- https://tokensave.app/blog/deepseek-api-pricing (checked against official docs 2026-10-12)

*All external pages were read on 2026-11-17. Catalog and free-endpoint membership change frequently
(NVIDIA retired at least a dozen hosted IDs during 2026); every "free today" statement above is a
statement about the cited probe dates, not a guarantee.*
