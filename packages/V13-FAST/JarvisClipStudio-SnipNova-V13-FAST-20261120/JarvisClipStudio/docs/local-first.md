# Local-first provider management

JarvisClipStudio is built to keep producing clips on your own computer with
**no per-video API charge**. Cloud LLM providers are an accelerant, never a
dependency: transcription (faster-whisper), reframing, rendering and QC
already run locally; the only cloud touch-point is LLM text analysis, and the
provider manager (`backend/src/provider_manager.py`) makes even that
local-first.

## How it works

Every LLM call the app makes for analysis and niche classification is routed
through the provider manager, a state layer above the existing call sites:

1. **Failure classification.** A failed call is classified as `rate_limited`
   (HTTP 413/429), `quota_exhausted` (429 whose body/message carries quota
   wording such as `insufficient_quota` or "tokens per day"), `unavailable`
   (5xx, 404 model errors, transport failures) or `auth` (401/403 — never
   retried, surfaced immediately when it is the only route).
2. **Cooldowns.** The failing alias cools down: minutes for rate limits
   (exponential per consecutive failure, 2 min → 30 min cap), until the next
   UTC day boundary for quota exhaustion, briefly for outages, 6 hours for
   auth. Selection skips cooling aliases. Cooldowns are kept in-process and
   written through to the `provider_cooldowns` table when persistence is
   wired at startup, so they survive restarts.
3. **Selection.** `select_model` logic orders candidates by purpose and
   complexity. The caller's heuristic: a single-window (short) analysis is
   *routine*, a multi-window (long) analysis is *hard*; classification is
   always routine. Routine work prefers a healthy local model; hard work
   prefers the configured cloud order (`ANALYSIS_MODELS`), with the local
   model appended as the final fallback. If every alias is cooling down, the
   configured order is returned unchanged so the existing retry machinery
   still applies — selection never strands a run.
4. **Local health.** The local route (Ollama) is probed at its `/models`
   endpoint (60 s cache) before an analysis is routed at it; a failed probe
   or a failed local call marks it unavailable briefly instead of hanging.
5. **Token accounting.** Each completed run records the provider's own
   `usage` numbers (prompt/completion tokens) per task, per alias, per
   purpose — see `GET /api/tasks/{task_id}/token-usage`. Calls whose
   response carried no usage are counted as calls with unknown usage; no
   numbers are ever invented. This is what makes "we analysed this video
   once, not five times" auditable, alongside the existing transcript and
   analysis caching in the task pipeline (the manager adds no second cache).

### Provider modes (`PROVIDER_MODE`)

| Mode | Behaviour |
| --- | --- |
| `auto` (default) | Configured cloud order for hard work, local preferred for routine work, local as final fallback, cooldowns skipped. |
| `local-first` | Local model for everything while it is healthy; cloud only when local is down or cooling. |
| `local-only` | Never call a cloud provider. If the local model is unreachable, the stage fails with a plain-words message saying so instead of hanging or silently using the cloud. |

`LOCAL_LLM` names the local route: `openai-chat:local-qwen` (the LiteLLM gateway
alias, default) or `ollama:<model>` for a direct Ollama connection.
`PROVIDER_MANAGER_ENABLED=false` turns the whole layer off: selection returns
today's static configuration and agents run unwrapped, byte-identical to
before.

Fallback is always legitimate: configured providers in order, then local.
Quotas are never evaded, accounts are never rotated to bypass limits, and an
exhausted provider is reported as exhausted — it just stops being selected
until its cooldown ends.

### Quality over speed

Full Quality is the default posture: the manager never drops to a weaker
model mid-run to save time. It changes *which provider* serves a stage only
on failure or by the mode rules above, and speed work (NVENC, input seeking,
parallel renders) is untouched by this layer.

## Model catalog (this deployment)

| Alias (app name) | Route | Role |
| --- | --- | --- |
| `openai-chat:clip-fast` | LiteLLM → NVIDIA NIM Nemotron 3 Ultra | Primary analysis (large context) |
| `openai-chat:clip-assured` | LiteLLM → Groq `openai/gpt-oss-120b` | Second analysis model (8k TPM free bucket — paced) |
| `openai-chat:local-qwen` | LiteLLM → Ollama `qwen3:8b` (Q4_K_M, ~5.2 GB) on the host | Local fallback + routine work; fits the RTX 4050's 6 GB VRAM |
| `ollama:<model>` | Direct Ollama | Alternative local route when not using the gateway |

Transcription is local by configuration (`TRANSCRIPTION_PROVIDER=
faster-whisper`, Whisper `medium` by default; `large-v3-turbo` is opt-in and
measurably worse for Hindi — see docs/transcription-speed.md).

## Open-source research (verified 2026-11-08)

How comparable projects achieve local, no-per-video-charge clipping:

| Project | Licence | What runs locally | How the LLM step is handled | Worth adopting |
| --- | --- | --- | --- | --- |
| [artbyjazi/autoclip](https://github.com/artbyjazi/autoclip) | MIT | faster-whisper (CTranslate2) + MediaPipe speaker reframe + FFmpeg; fully offline mode | Ollama (suggests `llama3.1:8b`) *or* BYO API key; only transcript text ever leaves the machine | Stage-artifact resume (retry restarts at the failed stage); highlight detection returns word indices, not timestamps (models copy numbers reliably, do arithmetic badly). Their honest caveat: a 7B model returns valid JSON full of mediocre picks vs a frontier model |
| [osbytes/reelsaw](https://github.com/osbytes/reelsaw) | AGPL-3.0 | Whole pipeline keyless: Ollama LLM, Speaches (faster-whisper server) STT, 1.2 MB ONNX face model on CPU | `provider:model` strings per stage (LLM / transcription / alignment) via the Vercel AI SDK; local default Ollama `qwen2.5:14b`, `qwen2.5:7b` for low RAM | Long-video strategy for small local models: chunk at long pauses with overlap, collect candidate moments per chunk, score on a rubric — no single call sees the whole video (validates this app's windowed analysis); installer probes machine RAM/GPU and picks model sizes |
| [zhouxiaoka/autoclip](https://github.com/zhouxiaoka/autoclip) | MIT | Cutting, framing and rendering on-device | Analysis model selectable: cloud APIs *or* Ollama / LM Studio locally; transcription via creator captions, local Whisper / SenseVoice, or cloud | Proof that a selectable local analysis model is a shippable default, not an experiment |
| [JeremySNR/cutawan](https://github.com/JeremySNR/cutawan) | MIT | faster-whisper transcription local; bundled LR-ASD ONNX active-speaker model | Clip-finding is **not** local: OpenAI-compatible API, OpenRouter, or ChatGPT/Codex sign-in. A "local captions only" mode needs no AI at all | The no-AI floor mode (caption the whole video) as a last-resort degradation; sentence-based clip boundaries measured by an eval script |
| [mutonby/openshorts](https://github.com/mutonby/openshorts) | MIT | Whisper transcription, YOLO/MediaPipe tracking, FFmpeg | Gemini cloud only — maintainers explicitly declined local-model support (issue #17, closed not planned) because analysis must reason over full long transcripts | The counterpoint: full-transcript reasoning is exactly what windowed analysis + rerank solves, which this app already does |
| [Farkoal2128/AutoClip-MVP](https://github.com/Farkoal2128/AutoClip-MVP) | MIT | Whisper transcription on Windows | Ollama local highlight selection, or a cloud provider | Confirms the Ollama-for-highlights pattern at small scale |

Note: "AutoClip" is a shared name — the GitHub projects above are unrelated
to each other and to the paid "AutoClip" tools advertised on social media.

## Limitations (honest)

- Local analysis quality on long transcripts was **not** benchmarked in this
  change (no GPU/Ollama on the build machine). The routing, cooldown and
  accounting behaviour is proven against fake OpenAI-compatible servers; the
  quality of `qwen3:8b` picks versus the cloud aliases must be judged on the
  target laptop. The windowed pipeline plus structured-output retries are
  the mitigations, and `auto` mode keeps cloud first for hard analyses.
- Cooldown state is per-process in memory (authoritative) with best-effort
  database write-through; two processes can briefly disagree after a failure
  until the next warm-up.
- Token *quota remaining* is only visible where a provider exposes it; the
  manager tracks usage totals and failure-driven cooldowns instead of
  pretending to know a provider's remaining balance.
