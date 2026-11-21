# MODEL REGISTRY

*Grounded in `litellm_config.yaml`, `backend/src/config.py` and
`.env.example` at `next-milestone` @ `6793620` (2026-11-12); §1
updated 2026-11-13 for the Cloudflare hop (`cloudflare-ladder-hop`
branch). If this page and the config disagree, the config wins.
Routing behaviour is documented in `AI_MODEL_ROUTING.md` (repo root).*

## 1. The LiteLLM gateway ladder (ladder v2, 2026-11-12)

The gateway (`litellm_config.yaml`) exposes aliases; every production
chain is ranked **strongest first**, and `local-qwen` (Ollama) is LAST
in every chain — the local model is the emergency floor that cannot
run out of allowance, not a peer. Canonical ranking:

**deepseek-chat → clip-assured → clip-fast → nim-deepseek →
openrouter-free → cloudflare-free → groq-20b → local-qwen**

| Alias | Provider / endpoint | Model slug | Role | Cost class |
| --- | --- | --- | --- | --- |
| `deepseek-chat` | DeepSeek direct, `api.deepseek.com` | `deepseek-v4-pro` | Strongest brain; first hop of every chain | Paid metered API (user's key)¹ |
| `clip-assured` | Groq | `openai/gpt-oss-120b` | Premium selection pass (sends `reasoning_effort=high`) | Groq free tier² |
| `clip-fast` | NVIDIA NIM, `integrate.api.nvidia.com/v1` | `nvidia/nemotron-3-ultra-550b-a55b` | Bulk/fast passes; also the long-form analysis route (~1M context per `.env.example`) | Free NIM endpoint (user's NVIDIA key) |
| `nim-deepseek` | NVIDIA NIM | `nvidia/nemotron-3.5-super-vl-120b-a12b` | Second NIM rung. Route name kept; was `deepseek-ai/deepseek-v4.1-flash` until the 2026-11-17 live probe returned HTTP 410 (retired). Verified WORKS on the owner's key that day | Free NIM endpoint |
| `openrouter-free` | OpenRouter | `nvidia/nemotron-3-ultra-550b-a55b:free` | Same Nemotron Ultra via OpenRouter's shared free pool; the `:free` suffix is pinned by test — a non-free fallback slug here once created a live paid-spend risk and must never regress | Free (rate-limited shared pool) |
| `or-ultra-free` | OpenRouter | `nvidia/nemotron-3-ultra-550b-a55b:free` | Legacy alias of the same route, kept with its own chain | Free |
| `cloudflare-free` | Cloudflare Workers AI, OpenAI-compatible endpoint built from `CLOUDFLARE_ACCOUNT_ID` at call time | `@cf/openai/gpt-oss-120b` | Eligible fallback on independent infrastructure (2026-11-13, owner's Final Directive), at his specified slot: after `openrouter-free`, before `groq-20b`. Slug pinned by test to the free-allocation model — the frontier Workers AI rows (GLM-5.x, Kimi K2.6+, DeepSeek V4) are paid-plan only and are banned from this slug by a pin. Endpoint **UNVERIFIED-LIVE** until the check in `AI_MODEL_ROUTING.md` §5 runs on the user's machine | Free allocation (10,000 Neurons/day, Workers Free plan) |
| `groq-20b` | Groq | `openai/gpt-oss-20b` | Light late-chain passes | Groq free tier² |
| `local-qwen` | Ollama on the user's PC, `host.docker.internal:11434/v1` | `qwen3:8b` | Final fallback in every chain; its own chain walks the cloud routes strongest-first | Local, free |

¹ No free tier for the DeepSeek direct API is documented in-tree; a
one-time new-account token grant was reported by the user (project
record, not in-tree evidence).
² `.env.example` describes the Groq route as "default, free tier" and
sizes pacing to "Groq's free tier" (`ANALYSIS_TPM=8000`).

Routes whose key is unset fail authentication if reached and are
cooled down by the router; the gateway boots regardless
(`litellm_config.yaml` router comment). **Gemini appears nowhere in
any chain** — standing rule, test-pinned.

## 2. Bake-off candidates — pre-registered, UNARMED

Six NIM deployments were appended to `litellm_config.yaml` on
2026-11-12 for the model bake-off (`tools/nim-bakeoff/`, report:
`research_notes/sprint-7day/NIM_BAKEOFF.md`). **No production chain
falls back to any candidate and no app code calls these aliases**
(pinned by gateway-config tests). Arming one later is a one-line slug
change on the `clip-fast` / `nim-deepseek` entry.

| Alias | Slug | Slug verification (offline, 2026-11-12) |
| --- | --- | --- |
| `nim-glm-flash` | `z-ai/glm-5.3-flash` | Verified on free-callable listings |
| `nim-kimi-k2` | `moonshotai/kimi-k2.6` | Verified |
| `nim-mistral-large4` | `mistralai/mistral-large-4-1t-a52b` | Verified |
| `nim-deepseek-pro` | `deepseek-ai/deepseek-v4-pro` | PARTIAL — catalogue page exists; absent from free-callable listings |
| `nim-qwen3-235b` | `qwen/qwen3-235b-a22b` | **UNVERIFIED** — probe `GET /v1/models` first, drop on 404 |
| `nim-llama4-maverick` | `meta/llama-4-maverick-17b-128e-instruct` | **UNVERIFIED** — probe first, drop on 404 |

**Decision rule** (NIM_BAKEOFF.md §5, binding): (1) a challenger must
be reliable — schema validity 8/8 on a run (≥23/24 across 3 repeats);
(2) it must WIN the workload, not the benchmark — Japan gold-set mean
overlap or recall better than the incumbent by more than the 0.1
noise floor, with no regression in traps taken, guardrail-corrected
niche accuracy, title violations, or plan rubric (rubric regression
> 10 points vetoes); (3) latency is a tie-breaker only, and only for
the `clip-fast` rung; (4) if no challenger clears (1)+(2) the
incumbent stays — **published benchmarks alone never arm a swap**.
The live run needs a real NVIDIA key in machine env and is scheduled
inside the user's worker container; the release ships with the proven
§1 ladder unless that measurement says otherwise.

## 3. The direct route (no gateway)

`backend/src/config.py` also resolves a direct `LLM`
(`provider:model`) used by the pydantic-ai passes in `src/ai.py`. If
`LLM` is unset, `_infer_default_llm` picks by whichever key exists,
in this order: `groq:openai/gpt-oss-120b` → `openai-chat:gpt-5.2` →
`anthropic:claude-4-sonnet` → `openrouter:anthropic/claude-sonnet-5.5`
→ `ollama:qwen3:8b`. Gemini is never a default (config docstring:
its API answers HTTP 411 here). Related settings (`.env.example`):
`ANALYSIS_MODELS` rotates analysis chunks round-robin;
`ANALYSIS_LONGFORM_MODEL` for long sources; the last-resort analysis
fallback is `openai-chat:local-qwen` (via the gateway), and if the gateway
itself is unreachable, `ollama:qwen3:8b` directly
(`ANALYSIS_DIRECT_FALLBACK_MODEL`).

*Footnote — template vs code:* `.env.example` still shows the upstream
example line `LLM=openrouter:anthropic/claude-sonnet-5.5`. That is the
template's example value, not the app default: with `LLM` unset the
code default above (Groq first) applies. The user's live `.env` is
not in this repo.

## 4. Transcription providers

Selected by `TRANSCRIPTION_PROVIDER` (`backend/src/config.py`;
unrecognised/unset normalises to `assemblyai` in code — the user's
deployment sets `faster-whisper` in his local `.env`):

| Provider | Engine | Notes | Cost |
| --- | --- | --- | --- |
| `faster-whisper` | Local CTranslate2 Whisper (`backend/src/media/transcription.py`) | This deployment's provider. Sizes: tiny, base, small, medium, large, large-v3, large-v3-turbo, distil-large-v3 (`WHISPER_MODEL_SIZE`, template default `medium`). Weights download on first use. Word timestamps + VAD. | Local, free |
| `whisper` | Local OpenAI Whisper (pip `openai-whisper`) | Same size setting. | Local, free |
| `assemblyai` | AssemblyAI cloud API | Needs `ASSEMBLY_AI_API_KEY`; word timing + speaker diarization; code default if the setting is unset. | Paid cloud API |
| `youtube_captions` | YouTube caption track extraction | YouTube-sourced videos only. | Free |

## 5. Local models (no API key; weights are runtime downloads)

| Model | Used for | Licence (in-tree source) | Download |
| --- | --- | --- | --- |
| YuNet `face_detection_yunet_2023mar.onnx` | Face detection / tracking for reframe (`media/face_detection.py`) | **MIT** — `backend/src/media/models/LICENSE-yunet`, © Shiqi Yu | **Bundled** in the repo (232,589 B; SHA-256 in `models/README.md`) — the one model that ships |
| YOLOX-s int8 `object_detection_yolox_2022nov_int8.onnx` | Person detection for reframe subject presence on samples with no significant face (`media/person_detection.py`, via `cv2.dnn`) | **Apache-2.0** — `backend/src/media/models/LICENSE-yolox` (OpenCV Zoo; YOLOX upstream © Megvii) | **Bundled** in the repo (9,079,452 B; SHA-256 in `models/README.md`) |
| SigLIP `google/siglip-base-patch16-224` | B-roll vision veto (`media/broll_vision.py`) — opt-in, `BROLL_VISION_VETO_ENABLED` default OFF, Auto mode only | Apache-2.0 (module header, "verified") | ~810 MB on first veto-enabled use |
| Kokoro-82M | AI voiceover (`media/voiceover.py`) — opt-in, `VOICEOVER_ENABLED` default OFF | Apache-2.0 weights (module header) | ~315 MB on first use; voice packs ~0.5 MB each |
| Demucs `htdemucs` | Voice/music separation (`media/separation.py`) — double-gated: `SEPARATION_ENABLED` default OFF **and** per-task opt-in | Code MIT; **weights licence explicitly unclear** (module header) — weights are therefore NEVER bundled | On first enabled use, by the user's own machine |
| Ollama `qwen3:8b` | Local LLM floor (see §1/§3) | Model runs on the user's host Ollama install, outside this repo | Host-side `ollama pull`, not part of any package |

Speaker diarization via pyannote is **not wired**: no pyannote
dependency or code exists in this tree. An HF token is banked in the
user's `.env` for the future wiring (post-release queue).
