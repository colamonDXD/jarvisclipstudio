# AI_MODEL_ROUTING — how SnipNova's brains are orchestrated

*Written 2026-11-13 at `next-milestone` @ `11fd688` + the
`cloudflare-ladder-hop` branch. Grounded in `litellm_config.yaml`,
`backend/src/config.py`, `compose.override.yml` and `.env.example`.
If this page and the config disagree, the config wins. The gateway
pins live in `backend/tests/unit/test_litellm_gateway_config.py`;
the model inventory lives in `docs/registries/MODEL_REGISTRY.md`.*

## 1. Provider roles (the owner's Final Directive)

| Provider | Role in the system | Where it serves |
| --- | --- | --- |
| **DeepSeek** (direct API) | Primary reasoning / editing director — strongest general brain, first hop of every chain: moment selection judgement, ranking, hooks, editing plans | `deepseek-v4-pro` |
| **Groq** | Fast inference — the premium selection pass and light structured passes at free-tier speed | `clip-assured` (gpt-oss-120b), `groq-20b` |
| **NVIDIA NIM** | Specialised / bulk execution — the flagship Nemotron route for bulk and long-form passes, the DeepSeek Flash route as the second NIM rung; also the bake-off pool | `clip-fast`, `nim-deepseek` (+ 6 unarmed bake-off candidates) |
| **OpenRouter** | Backup access via verified free models only — the same Nemotron Ultra through OpenRouter's shared, daily-capped free pool | `openrouter-free` (and its legacy twin `or-ultra-free`, in no chain) |
| **Cloudflare Workers AI** | Eligible fallback on independent infrastructure — gpt-oss-120b (clip-assured's weights) on a provider whose quota nothing else drains, inside its free daily Neuron allocation | `cloudflare-free` |
| **Ollama** (user's PC) | Local emergency floor — last in every chain; cannot run out of anyone's allowance | `local-qwen` (qwen3:8b) |
| **Hugging Face** | Models and resources, not a chat route: SigLIP veto weights, Whisper-family downloads; a token is banked for the future pyannote diarization wiring | — |

Local non-LLM intelligence (faster-whisper transcription, YuNet face
tracking, the SigLIP B-roll veto) runs on the user's machine and is
not routed through the gateway at all.

## 2. The ladder — one ranking, every chain

There is deliberately **one** strength ranking; every chain is that
ranking with the chain's own route removed, so a failed route always
falls to the next strongest brain:

**deepseek-chat → clip-assured → clip-fast → nim-deepseek →
openrouter-free → cloudflare-free → groq-20b → local-qwen**

Task families differ only in where they *enter* the ladder — the
walk from there is the same:

| Task family | Enters at | Then walks |
| --- | --- | --- |
| Premium moment selection (reasoning_effort=high) | `clip-assured` | deepseek-chat → clip-fast → nim-deepseek → openrouter-free → cloudflare-free → groq-20b → local-qwen |
| Bulk / fast analysis, long-form chunked analysis | `clip-fast` | deepseek-chat → clip-assured → nim-deepseek → openrouter-free → cloudflare-free → groq-20b → local-qwen |
| Light structured passes | `groq-20b` | deepseek-chat → clip-assured → clip-fast → nim-deepseek → openrouter-free → cloudflare-free → local-qwen |
| Local-first passes (content requirements permits) | `local-qwen` | the cloud ladder strongest-first: deepseek-chat → clip-assured → clip-fast → nim-deepseek → openrouter-free → cloudflare-free → groq-20b |

Fallback-only routes (`deepseek-chat`, `nim-deepseek`,
`openrouter-free`, `cloudflare-free`) are never called by app code
directly — test-pinned. The six NIM bake-off candidates are
pre-registered but **unarmed**: no production chain falls back to
them; if called directly they walk the full ladder.

## 3. Failure behaviour

- **Detection.** The LiteLLM router treats a rate limit (429), quota
  exhaustion, a provider 5xx/outage, a connection failure, a timeout,
  and an unparseable response body as a failed hop and walks to the
  next route *inside the same request* — the caller just receives an
  answer from a lower hop. (Drill-proven for every failure shape,
  including a hanging hop and an HTTP-200 garbage body: the garbage
  is never accepted as an answer.)
- **Retries and cooldowns.** `num_retries: 1`, `cooldown_time: 60` —
  identical for every hop, Cloudflare included; nothing in the
  config gives any route special retry behaviour. A 429-ing hop gets
  at most 1 + num_retries attempts per call (drill-observed: 2),
  then the walk moves on; there is no retry loop.
- **Non-interruption.** Failover happens inside a single gateway
  request. Rendering is ffmpeg work downstream of a *frozen* plan;
  a provider dying mid-selection cannot touch a clip that is
  generating, and no fallback path deletes or restarts completed
  work.
- **Checkpoint resume.** Each pipeline stage's output is checkpointed
  per task (transcript, analysis windows, candidates, classification,
  the frozen plan, per-clip completed positions). A retry resumes
  from the checkpoints — transcription and finished clips are never
  redone.
- **Code-level nets below the gateway.** The chunked analysis walker
  has its own failover (configured models → `openai-chat:local-qwen` via
  the gateway → direct `ollama:qwen3:8b` if the gateway itself is
  down); the provider manager tracks failures/cooldowns and re-picks
  models for analysis and classification. Structured outputs are
  validated by the app layer (schema-checked, with output retries
  that repair shape) — a hop returning malformed output fails the
  stage honestly rather than passing silently.
- **Missing keys.** A route whose key is unset fails authentication
  if reached and the walk moves on; the gateway boots and the
  primary routes are unaffected. Nothing in the app or UI reports such a
  route as configured or healthy — Cloudflare's name appears in no
  backend or frontend source at all (test-pinned).

## 4. The never-paid and never-rotate guarantees

- **Free slugs are pinned by tests.** `openrouter-free` must stay a
  `:free` model; the app's `OPENROUTER_FALLBACK_MODEL` default must
  stay the free Nemotron slug (the paid Luna default was a live
  spend-risk defect, removed 2026-11-12); `cloudflare-free` must
  stay `@cf/openai/gpt-oss-120b`, the free-allocation model, and its
  slug may never contain a paid-plan-only fragment (GLM-5.x, Kimi,
  DeepSeek V4).
- **Cloudflare is structurally free-only.** The route names exactly
  one deployment with one fixed model slug. There is no model list,
  no fallback-model parameter and no server-side model switching on
  that route, so there is no mechanism by which it could select a
  paid Workers AI model. Its budget is the Workers Free plan's
  10,000 Neurons/day (this model costs 31,818 neurons/M input tokens
  per Cloudflare's own pricing table — roughly ten selection-size
  passes a day, which matches the hop's depth: it is only reached
  when five routes above it are down or drained).
- **One account, one key per provider.** Keys live only in the
  user's `.env`; no code rotates accounts or keys, and no route is
  duplicated under a second key to multiply a quota.
- **The one metered route is a deliberate, documented choice.**
  DeepSeek direct is pay-per-token (paise-level; a one-time
  new-account grant was reported). The owner ordered strength-first
  routing knowing this; with no `DEEPSEEK_API_KEY` set the hop is
  skipped by authentication failure and the walk continues.
- **Gemini is nowhere** — no route, no chain, no default (its API
  answers 411 here). Test-pinned.

## 5. What is UNVERIFIED-LIVE, and the check that settles it

Everything above about *routing behaviour* is verified in tests and
in a real-Router drill (see §6). What the build sandbox cannot prove
— it holds no keys — is each provider's live endpoint on the user's
account. For Cloudflare specifically, the validation-window check on
the user's machine is:

1. In `D:\EditedTool\JarvisClipStudio\.env`, confirm
   `CLOUDFLARE_API_KEY` and `CLOUDFLARE_ACCOUNT_ID` are set (both —
   the token authenticates, the account ID builds the endpoint).
2. In `compose.override.yml`, the `litellm` service's environment
   list must carry both passthrough lines —
   `- CLOUDFLARE_API_KEY=${CLOUDFLARE_API_KEY}` and
   `- CLOUDFLARE_ACCOUNT_ID=${CLOUDFLARE_ACCOUNT_ID}` — alongside the
   other provider keys. **The user's live override may not have
   these two lines yet** (his keys were added before this route
   existed); the shipped template now carries them, and the
   consolidated install instructions for the validation window
   include adding them to his live file.
3. Restart the stack, then one forced call through the gateway with
   model `cloudflare-free` must return a real completion, and
   `GET https://api.cloudflare.com/client/v4/accounts/<ACCOUNT_ID>/ai/v1/models`
   with his token must list `@cf/openai/gpt-oss-120b`. Until that
   runs, the Cloudflare endpoint and slug-on-his-account are
   **UNVERIFIED-LIVE** — the route is wired, pinned and
   drill-proven, but has never served a real request.

Also still UNVERIFIED-LIVE (unchanged from ladder v2): the full
ladder walk on real keys, and the NIM bake-off live run that decides
whether any candidate ever replaces a rung.

## 6. Evidence for this wiring

- Gateway pins: `backend/tests/unit/test_litellm_gateway_config.py`
  — exact model list and chains, the Cloudflare position pins, the
  free-slug pins, the key-plumbing pins, the no-app-surface pin.
- Router drill: `research_notes/sprint-7day/cloudflare-drill/`
  (the shipped config loaded verbatim into a real LiteLLM Router,
  endpoints rewired to dead ports and local mocks): Cloudflare
  serves in position; a failing (500), hanging (timeout) and
  garbage-output Cloudflare hop all fall through correctly; a 429
  hop gets bounded attempts; the local floor still ends every walk.
- Ladder v2 drill and audit: `research_notes/sprint-7day/LADDER_V2_REPORT.md`.
