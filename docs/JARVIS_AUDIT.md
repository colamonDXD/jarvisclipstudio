# JARVIS Desk — Audit Report

**Generated:** 2026-11-06  
**Project Root:** `D:/AI/JARVIS-Desk`  
**Related Path:** `D:/AI/MultiModel` (LiteLLM gateway)  
**Ollama Models:** `D:/AI/OllamaModels`

---

## 1. File Tree (Excluding `.venv`, `node_modules`, `__pycache__`, `models`, `.git`, `release`)

```
D:/AI/JARVIS-Desk/
├── .cursorignore
├── .gitignore
├── README.md
├── electron.log
├── index.html
├── install-and-run.ps1
├── package.json
├── package-lock.json
├── vite.config.js
├── build/
│   ├── icon.png
│   └── tray.png
├── docs/
│   ├── CONTINUATION-REPORT.md
│   └── JARVIS_AUDIT.md (this file)
├── electron/
│   ├── main.cjs                    # Main process entry point
│   ├── preload.cjs                 # Context-isolated IPC bridge
│   ├── ipc.cjs                     # All IPC handlers (41+ channels)
│   ├── overlay-preload.cjs
│   ├── overlay.html
│   ├── native/
│   │   └── play-wav.py             # Windows audio playback helper
│   └── lib/
│       ├── attachments.cjs         # File/attachment handling (PDF, images, text)
│       ├── browser.cjs             # Isolated browser automation (Playwright)
│       ├── computer.cjs            # Desktop actions (apps, folders, URLs, files)
│       ├── config.cjs              # Central config: MODELS, LIMITS, routing categories
│       ├── gateway.cjs             # LiteLLM gateway client (streaming + non-streaming)
│       ├── health.cjs              # Per-model health tracking + circuit breaker
│       ├── logger.cjs              # Structured logging with redaction
│       ├── memory.cjs              # Conversation + project + memory persistence
│       ├── nativePath.cjs          # Packaged vs dev resource resolution
│       ├── orchestrator.cjs        # AUTO routing, FAST + DEEP WORK modes
│       ├── router.cjs              # Deterministic classification + routing
│       ├── screen.cjs              # Screenshot + OCR (Windows Runtime)
│       ├── screenShare.cjs         # Continuous screen sharing
│       ├── secrets.cjs             # .env parsing (LITELLM_MASTER_KEY, provider keys)
│       ├── settings.cjs            # Settings schema + persistence + quotas
│       ├── sse.cjs                 # SSE parser for streaming responses
│       ├── store.cjs               # JSON file read/write helpers
│       └── voice.cjs               # Electron-side voice manager (Python sidecar)
├── scripts/
│   ├── launch-electron.cjs         # Electron launcher with env fixes
│   ├── diagnose-gemini.cjs
│   ├── verify-gateway.cjs
│   ├── probe-models.cjs
│   ├── restart-gateway.cjs
│   ├── smoke-test.cjs
│   ├── engine-probe.py             # Voice engine diagnostics
│   ├── engine-file-probe.py
│   ├── mic-level-probe.py
│   ├── sidecar-restart-probe.py
│   ├── sidecar-stdout-probe.py
│   ├── wake-load-timing.py
│   ├── install-voice.ps1           # Voice engine installer
│   ├── set-nvidia-key.ps1
│   └── *.cjs (diagnostic/test utilities)
├── src/
│   ├── main.jsx                    # React entry point
│   ├── App.jsx                     # Main app shell (62KB)
│   ├── style.css                   # Desktop UI styling (67KB)
│   ├── lib/
│   │   └── ui.js
│   └── components/
│       ├── Sidebar.jsx
│       ├── TopBar.jsx
│       ├── Composer.jsx
│       ├── ConversationView.jsx
│       ├── SettingsPanel.jsx
│       ├── MemoryPanel.jsx
│       ├── ProjectsPanel.jsx
│       ├── ActivityPanel.jsx
│       ├── ConsentModal.jsx
│       ├── ErrorBoundary.jsx
│       └── *.jsx (other UI components)
├── tests/
│   ├── router.test.cjs
│   ├── settings.test.cjs
│   ├── attachments.test.cjs
│   ├── memory.test.cjs
│   ├── gateway.test.cjs
│   └── health.test.cjs
├── voice/                          # VOICE ENGINE (SEPARATE)
│   ├── jarvis_voice.py             # Main voice engine (38KB)
│   ├── make_cues.py                # Audio cue generator
│   ├── requirements.txt            # Python dependencies
│   ├── assets/
│   │   └── cues/                   # 7 WAV files (wake, ack, completed, error, etc.)
│   └── .venv/                      # EXCLUDED (Python virtual env)
└── MultiModel/ (D:/AI/MultiModel)
    ├── .env                        # HARDCODED KEYS — see Section 3
    ├── config/
    │   ├── config.yaml             # LiteLLM model list
    │   └── Modelfile-qwen35-4k
    ├── .venv/                      # EXCLUDED
    ├── logs/
    └── assistant/
```

**Top-Level Folder Purposes:**
| Folder | Purpose |
|--------|---------|
| `electron/` | Electron main process + privileged modules (IPC, gateway, voice, computer, screen) |
| `src/` | React renderer (UI, chat, settings, memory, projects) |
| `scripts/` | Build, launch, diagnostic, and test utilities |
| `voice/` | **Offline Python voice engine** (wake-word, STT, TTS, speaker enrollment) — SEPARATE |
| `tests/` | Unit tests (26 passing) |
| `docs/` | Documentation |
| `build/` | App icons |
| `MultiModel/` | External LiteLLM gateway (separate Python env at `D:/AI/MultiModel`) |

---

## 2. Entry Points — How JARVIS Starts

### Primary Entry Points

| Entry Point | File | Description |
|-------------|------|-------------|
| **Electron Main** | `electron/main.cjs` | `package.json:main` → boots logger, settings, memory, health, voice, gateway, creates window, tray, menu |
| **React Renderer** | `src/main.jsx` → `src/App.jsx` | Vite dev server (`npm run dev`) or built `dist/index.html` |
| **Voice Sidecar** | `voice/jarvis_voice.py` | Spawned by `electron/lib/voice.cjs` as Python subprocess (stdio JSON-lines) |
| **LiteLLM Gateway** | `D:/AI/MultiModel/.venv/Scripts/litellm.exe` | Started by `electron/ipc.cjs:launchGateway()` with `config.yaml` |

### Startup Sequence (from `main.cjs:bootstrap()`)

1. `app.whenReady()` → initialize modules: `logger`, `settings`, `memory`, `health`, `attachments`, `screen`, `screenShare`, `computer`, `browser`, `voice`
2. Register IPC handlers (`registerIpc`)
3. Create main `BrowserWindow` with `preload.cjs` (context isolation, sandbox)
4. Create system tray with voice/computer controls
5. **Auto-start LiteLLM gateway** if `settings.providers.enabled.local` (1.5s delay)
6. **Preboot voice engine** (warm wake-model load, 2.5s delay) — no mic opened yet
7. **Auto-start microphone** if `settings.microphone.enabled` and voice installed (3s delay)

### Launch Scripts

| Command | Script | Purpose |
|---------|--------|---------|
| `npm run dev` | `concurrently vite + wait-on + scripts/launch-electron.cjs` | Dev mode with hot reload |
| `npm start` | `vite build && node scripts/launch-electron.cjs` | Production mode |
| `npm run dist:win` | `vite build && electron-builder --win nsis` | Windows installer |

### Launch Electron Wrapper (`scripts/launch-electron.cjs`)

- Unsets `ELECTRON_RUN_AS_NODE` (fixes VS Code extension interference)
- Passes `VITE_DEV_SERVER_URL` for dev mode
- Spawns Electron with proper environment

---

## 3. .env Variable Names Only

### `D:/AI/MultiModel/.env` — **HARDCODED KEYS PRESENT**

| Variable | Status | Line |
|----------|--------|------|
| `GEMINI_API_KEY` | **HARDCODED KEY in D:/AI/MultiModel/.env:1** | 1 |
| `GROQ_API_KEY` | **HARDCODED KEY in D:/AI/MultiModel/.env:2** | 2 |
| `LITELLM_MASTER_KEY` | **HARDCODED KEY in D:/AI/MultiModel/.env:3** | 3 |
| `NVIDIA_NIM_API_KEY` | **HARDCODED KEY in D:/AI/MultiModel/.env:4** | 4 |

> **Security Note:** The `.env` file in `D:/AI/MultiModel/` contains actual API key values. These are read by `electron/lib/secrets.cjs:parseEnvFile()` and passed to the LiteLLM child process via environment variables. The keys are **never exposed to the renderer** — they stay in the Electron main process only.

### Expected Environment Variables (from `secrets.cjs:SECRET_NAMES`)

- `LITELLM_MASTER_KEY` — Master key for LiteLLM proxy authentication
- `GEMINI_API_KEY` — Google Gemini API key
- `GROQ_API_KEY` — Groq API key
- `NVIDIA_NIM_API_KEY` — NVIDIA NIM API key

---

## 4. Groq Integration

### Files

| File | Purpose |
|------|---------|
| `electron/lib/gateway.cjs` | Gateway client — `streamChat()`, `chatOnce()`, `probeModel()` |
| `electron/lib/config.cjs` | Model definition: `MODELS.groq` |
| `electron/lib/router.cjs` | Routing: `groq` in FAST mode for `greeting`, `simple`, `quick`, `code` |
| `electron/lib/orchestrator.cjs` | Execution: `runFast()` tries `groq` first for coding tasks |
| `D:/AI/MultiModel/config/config.yaml` | LiteLLM model config: `groq/openai/gpt-oss-120b` |

### Library

- **LiteLLM** (Python proxy) — handles Groq API calls via OpenAI-compatible interface
- **No direct Groq SDK** in this codebase — all traffic goes through LiteLLM gateway

### Model

| Property | Value |
|----------|-------|
| Alias | `groq` |
| Display Name | `GPT OSS 120B` |
| Provider | `Groq` |
| Model ID | `openai/gpt-oss-120b` |
| Location | `cloud` |
| Vision | `false` |
| Context Window | 131,072 |
| Max Output | 4,096 |
| Timeout | 45,000 ms |

### Function Signatures (Gateway)

```javascript
// electron/lib/gateway.cjs
async function streamChat({
  alias,           // 'groq'
  messages,        // ChatMessage[]
  signal,          // AbortSignal
  onDelta,         // (text, {modelId}) => void
  maxOutput,       // number
  temperature,     // default 0.35
  timeoutMs,       // default MODELS[alias].timeoutMs
  maxAttempts,     // default 1
  onAttempt        // (attemptNumber) => void
}): Promise<StreamResult>

async function chatOnce({
  alias, messages, signal, maxOutput, temperature, timeoutMs
}): Promise<ChatResult>

async function probeModel(alias, { signal }): Promise<ProbeResult>
```

### Retry / Rate-Limit Handling

| Mechanism | Location | Behavior |
|-----------|----------|----------|
| **HTTP 429 → `rate_limit`** | `gateway.cjs:classifyStatus()` | `retryable: true`, cooldown 60s |
| **HTTP ≥500 → `overloaded`** | `gateway.cjs:classifyStatus()` | `retryable: true`, cooldown 45s |
| **Per-model circuit breaker** | `health.cjs:recordFailure()` | 2 consecutive failures → cooldown |
| **Sequential fallback** | `orchestrator.cjs:runFast()` | Tries candidates in order: `groq` → `nvidia` → `gemini` → `local` |
| **Daily cloud call budget** | `settings.cjs:quotas.dailyCloudCallBudget` | Default 200 calls/day; disables all cloud models when exceeded |
| **Streaming retry** | `gateway.cjs:streamChat()` | Up to `maxAttempts` (default 1 for chat, higher for probes) |
| **Probe streaming fallback** | `gateway.cjs:probeModel()` | If non-streaming returns empty/reasoning-only, retries via streaming with 512-token budget |

### Groq-Specific Notes

- **Reasoning model**: `gpt-oss` produces hidden reasoning; gateway handles with `reasoning_content` separation
- **Probe fix**: `probeModel()` falls back to streaming when non-streaming returns empty content (Groq-specific behavior)
- **No parallel calls**: Orchestrator is strictly sequential — never fans out to multiple providers

---

## 5. Ollama Integration

### Files

| File | Purpose |
|------|---------|
| `electron/lib/gateway.cjs` | `localModelCapabilities()` queries Ollama `/api/show` |
| `electron/lib/config.cjs` | `MODELS.local` definition, `OLLAMA_URL = 'http://127.0.0.1:11434'` |
| `electron/ipc.cjs` | `refreshLocalCapabilities()` calls gateway |
| `D:/AI/MultiModel/config/config.yaml` | LiteLLM model: `ollama_chat/qwen3.5:4b-4k` |

### Model

| Property | Value |
|----------|-------|
| Alias | `local` |
| Display Name | `Qwen 3.5 4B` |
| Provider | `Ollama (local)` |
| Model ID | `qwen3.5:4b-4k` |
| Location | `local` |
| Vision | `false` (verified via Ollama capabilities) |
| Context Window | 4,096 |
| Max Output | 1,536 |
| Timeout | 120,000 ms |

### Host / Port

- **Ollama URL:** `http://127.0.0.1:11434` (hardcoded in `config.cjs:OLLAMA_URL`)
- **LiteLLM proxies to Ollama** via `api_base: "http://127.0.0.1:11434"` in `config.yaml`

### Function Signatures

```javascript
// electron/lib/gateway.cjs
async function localModelCapabilities(): Promise<{
  reachable: boolean,
  vision: boolean,
  capabilities: string[],
  contextLength: number | null
}>

// Queries: POST http://127.0.0.1:11434/api/show
// Body: { model: "qwen3.5:4b-4k" }
// Checks: capabilities.includes('vision')
```

### Capability Detection Flow

1. App startup → `refreshLocalCapabilities()` (ipc.cjs:922)
2. Calls `gateway.localModelCapabilities()` 
3. Queries Ollama `/api/show` endpoint
4. Parses `capabilities` array → sets `localCaps.vision`
5. Used by router: `capabilities.localVision` gates vision routing to local model

### Notes

- **No direct Ollama SDK** — all inference goes through LiteLLM gateway
- **Ollama must be running** on port 11434 for local model to work
- **Model stored in** `D:/AI/OllamaModels/` (blobs, manifests, metadata directories)
- **Vision support** detected dynamically — not hardcoded

---

## 6. GPU / NVIDIA / Torch Device Code

### Summary

**No PyTorch / CUDA / torch.device code exists in this codebase.**

| Search | Result |
|--------|--------|
| `torch` | No matches in any `.py` or `.cjs` files |
| `cuda` | No matches |
| `nvidia` | Only in model config (`NVIDIA_NIM_API_KEY`, `nvidia` model alias) — **cloud API**, not local GPU |
| `CUDA` | No matches |
| `device_map` | No matches |
| `accelerate` | No matches |

### NVIDIA NIM Integration (Cloud Only)

| File | Purpose |
|------|---------|
| `electron/lib/config.cjs` | `MODELS.nvidia` — `nvidia/nemotron-3-ultra-550b-a55b` |
| `D:/AI/MultiModel/config/config.yaml` | `model: nvidia_nim/nvidia/nemotron-3-ultra-550b-a55b`, `api_base: https://integrate.api.nvidia.com/v1` |
| `electron/lib/router.cjs` | `nvidia` preferred for `reasoning`, `code`, `long-context` |
| `electron/lib/orchestrator.cjs` | `nvidia` used in Deep Work analysis/review/synthesis stages |

### Local GPU Usage

- **Ollama** uses local GPU (RTX 4050 per README) for `qwen3.5:4b-4k` inference — managed by Ollama, not this codebase
- **faster-whisper** (STT) runs on CPU (`device="cpu"`, `compute_type="int8"`) — see `jarvis_voice.py:481-486`
- **openwakeword** (wake-word) uses ONNX Runtime — CPU inference
- **No direct CUDA/kernel code** in any file

---

## 7. Voice / STT / TTS Files (SEPARATE)

> **Marked SEPARATE as requested** — the voice engine is a standalone Python sidecar with its own dependencies, process lifecycle, and protocol.

### Core Voice Engine

| File | Lines | Purpose |
|------|-------|---------|
| `voice/jarvis_voice.py` | ~918 | **Main engine** — wake-word, VAD, STT, speaker embedding, TTS coordination |
| `voice/make_cues.py` | ~50 | Generates WAV cue files from system TTS |
| `voice/requirements.txt` | 6 | Python dependencies |

### Audio Assets

| File | Purpose |
|------|---------|
| `voice/assets/cues/chime_wake.wav` | Wake-word detected |
| `voice/assets/cues/acknowledged.wav` | Command acknowledged |
| `voice/assets/cues/right_away.wav` | Quick acknowledgment |
| `voice/assets/cues/on_it.wav` | Task started |
| `voice/assets/cues/completed.wav` | Task completed |
| `voice/assets/cues/error.wav` | Error cue |
| `voice/assets/cues/didnt_catch_that.wav` | Failed to understand |

### Electron Voice Manager

| File | Purpose |
|------|---------|
| `electron/lib/voice.cjs` | Manages Python sidecar lifecycle, config, profile, TTS via PowerShell |
| `electron/native/play-wav.py` | Windows audio playback helper (sounddevice) |
| `electron/native/tts.ps1` | PowerShell SAPI TTS (generates WAV) |
| `electron/native/play-audio.ps1` | PowerShell audio playback for cues |

### STT (Speech-to-Text)

| Component | Details |
|-----------|---------|
| **Library** | `faster-whisper>=1.1.1` (CTranslate2 backend) |
| **Model** | `base.en` (configurable via `settings.stt.model`) |
| **Device** | CPU (`device="cpu"`, `compute_type="int8"`) |
| **Download** | Auto-downloads to `voice/models/` on first run (~75 MB) |
| **Language** | Configurable (`settings.stt.language`, default `en`) |
| **VAD** | Custom RMS-based (not Whisper VAD) — `jarvis_voice.py:_speech_threshold()` |

### TTS (Text-to-Speech)

| Component | Details |
|-----------|---------|
| **Engine** | Windows SAPI via PowerShell (`tts.ps1`) |
| **Voices** | System-installed SAPI voices (enumerated via `screen.ttsVoices()`) |
| **Output** | Generates WAV to temp file → plays via `play-wav.py` (sounddevice) |
| **Rate/Volume** | Configurable (`settings.tts.rate`: -5..5, `settings.tts.volume`: 0..100) |
| **Device Selection** | Specific output device supported (`settings.tts.outputDeviceName/Id`) |

### Wake-Word Detection

| Component | Details |
|-----------|---------|
| **Library** | `openwakeword==0.6.0` |
| **Model** | `hey_jarvis` ONNX model (downloaded by `install-voice.ps1`) |
| **Framework** | ONNX Runtime (`onnxruntime>=1.20`) |
| **Sensitivity** | Configurable (`settings.wakeWord.sensitivity`, default 0.55) |
| **Phrase** | "Hey Jarvis" (hardcoded in model) |

### Speaker Enrollment / Verification

| Component | Details |
|-----------|---------|
| **Embedding** | Custom MFCC-based (80-dim: mean/std + delta mean/std, L2-normalized) |
| **Storage** | Encrypted via Electron `safeStorage` → `%APPDATA%\jarvis-desk\voice-profile.bin` |
| **Threshold** | Configurable (`settings.voiceEnrollment.threshold`, default 0.78) |
| **Samples** | 4-8 enrollment samples (configurable) |

### Protocol (JSON-lines over stdio)

**Commands (stdin → Python):**
```
start, stop, suspend, resume, sleep, config, status, shutdown,
enroll_start, enroll_capture, enroll_finish, enroll_cancel,
set_profile, clear_profile
```

**Events (stdout → Electron):**
```
ready, state, wake, level, transcript, enroll_sample, enroll_progress,
enroll_done, error, log, stopped, model, speaker, config-ack, profile-set
```

### Python Dependencies (`voice/requirements.txt`)

```
numpy>=2.1
sounddevice>=0.5.1
openwakeword==0.6.0
onnxruntime>=1.20
faster-whisper>=1.1.1
```

---

## 8. ffmpeg / yt-dlp / whisper / opencv Code

### Search Results

| Library | Found In | Usage |
|---------|----------|-------|
| **ffmpeg** | **NO** — not referenced in any file | — |
| **yt-dlp** | **NO** — not referenced in any file | — |
| **whisper** | `voice/jarvis_voice.py` | `faster-whisper` (CTranslate2), NOT `openai-whisper` |
| **opencv** | **NO** — not referenced in any file | — |

### Details

- **faster-whisper** is used (not the original `whisper` package) — CPU-optimized CTranslate2 implementation
- **No video processing** — screenshots are captured via Electron `desktopCapturer`, OCR via Windows Runtime API
- **No audio conversion** — audio stays at 16kHz mono int16 throughout the pipeline
- **No media downloading** — no yt-dlp, no URL audio/video extraction

---

## 9. Requirements / Python Version / Compile Check

### Python Version

- **Target:** Python 3.10+ (implied by `faster-whisper`, `onnxruntime`, `openwakeword` requirements)
- **Actual:** Not explicitly pinned in `pyproject.toml` (doesn't exist) or `package.json`
- **Virtual Env:** `voice/.venv/` (created by `scripts/install-voice.ps1`)
- **MultiModel Env:** `D:/AI/MultiModel/.venv/` (for LiteLLM)

### Python Dependencies

#### Voice Engine (`voice/requirements.txt`)

| Package | Version | Purpose |
|---------|---------|---------|
| `numpy` | `>=2.1` | Audio processing, MFCC, embeddings |
| `sounddevice` | `>=0.5.1` | Microphone I/O (PortAudio) |
| `openwakeword` | `==0.6.0` | Wake-word detection (ONNX) |
| `onnxruntime` | `>=1.20` | ONNX model inference |
| `faster-whisper` | `>=1.1.1` | STT (CTranslate2) |

#### MultiModel / LiteLLM (`D:/AI/MultiModel/.venv/`)

| Package | Inferred Purpose |
|---------|------------------|
| `litellm` | LLM proxy / gateway |
| `ollama` | Local model client (for LiteLLM) |
| `google-generativeai` | Gemini provider |
| `groq` | Groq provider |
| `openai` | NVIDIA NIM (OpenAI-compatible) |
| `pydantic`, `fastapi`, `uvicorn` | LiteLLM server deps |

> **Note:** No `pyproject.toml`, `setup.py`, or `requirements.txt` in `MultiModel/` — dependencies managed in its `.venv/`.

### Compile Check Results

```bash
cd D:/AI/JARVIS-Desk
python -m py_compile voice/jarvis_voice.py voice/make_cues.py \
  scripts/engine-file-probe.py scripts/engine-probe.py \
  scripts/mic-level-probe.py scripts/sidecar-restart-probe.py \
  scripts/sidecar-stdout-probe.py scripts/wake-load-timing.py \
  electron/native/play-wav.py
```

**Result: ✅ NO ERRORS** — All Python files compile successfully.

---

## 10. Verdict Table — For Clipper App (Shares Only Groq/Ollama/GPU/Config)

| Module | Reuse / Adapt / Ignore | Reason |
|--------|------------------------|--------|
| **Groq Integration** | **REUSE** | Clean abstraction via LiteLLM gateway; `gateway.cjs` handles streaming, retries, rate-limits. Clipper can call same `streamChat({alias:'groq',...})` or replicate the LiteLLM config. |
| **Ollama Integration** | **REUSE** | Local model via LiteLLM + Ollama. `gateway.cjs:localModelCapabilities()` queries Ollama directly. Clipper can use same endpoint (`http://127.0.0.1:11434/api/show`) for capability detection. |
| **GPU / Torch Code** | **IGNORE** | No torch/CUDA code in this repo. Local GPU used only by Ollama (external) and faster-whisper (CPU). Clipper manages its own GPU inference. |
| **Config System** | **ADAPT** | `config.cjs` defines `MODELS`, `LIMITS`, routing categories — good reference for model registry. Clipper should extract model metadata into shared config (JSON/YAML) rather than JS module. |
| **Settings Schema** | **ADAPT** | `settings.cjs` has comprehensive schema with validation. Clipper can adopt similar structure (providers, quotas, privacy, routing) but implement in its own stack. |
| **Health / Circuit Breaker** | **REUSE** | `health.cjs` is a standalone, well-designed module (per-model status, cooldowns, persistence). Directly portable to any TypeScript/Python service. |
| **Router / Classifier** | **ADAPT** | `router.cjs` has deterministic `classify()` + `planRoutes()` — pure functions, easily portable. Clipper can reuse logic for request categorization. |
| **Orchestrator (FAST/DEEP)** | **ADAPT** | `orchestrator.cjs` implements sequential multi-model pipeline (analysis→review→synthesis). Good pattern for Clipper's "Deep Work" equivalent. |
| **Gateway Client** | **REUSE** | `gateway.cjs` is the core LLM client — streaming, non-streaming, retries, error classification. Clipper can wrap/adapt for its own provider abstraction. |
| **Secrets Management** | **ADAPT** | `secrets.cjs` reads `.env` with presence-only reporting. Clipper should use proper secret manager (OS keychain, Vault) instead of `.env`. |
| **Voice Engine** | **IGNORE** | Entirely separate Python sidecar (STT/TTS/wake-word/speaker ID). Clipper has its own voice stack or uses cloud APIs. |
| **Screen Capture / OCR** | **IGNORE** | Windows-specific (Electron `desktopCapturer` + PowerShell OCR). Not portable. |
| **Computer Control** | **IGNORE** | Windows-specific desktop automation (app launch, folders, browser). Clipper is a different product. |
| **Memory / Conversations** | **ADAPT** | `memory.cjs` file-based persistence with projects/memories. Good reference for local-first storage schema. |
| **Attachments (PDF/images)** | **ADAPT** | `attachments.cjs` handles multi-format reading with size limits. Reusable logic for file preprocessing. |
| **SSE Parser** | **REUSE** | `sse.cjs` is a clean streaming parser — directly portable. |
| **Logger (with redaction)** | **REUSE** | `logger.cjs` has secret redaction built-in — good pattern for any service logging. |
| **IPC / Preload Bridge** | **IGNORE** | Electron-specific (context isolation, preload). Not applicable to non-Electron apps. |

---

## Summary

**JARVIS Desk** is a well-architected, local-first Windows AI assistant built on Electron + React with a Python voice sidecar. Key architectural strengths:

1. **Security-first**: Secrets never leave main process; renderer is sandboxed; context isolation enforced
2. **Provider abstraction**: LiteLLM gateway centralizes all LLM calls (Groq, Gemini, NVIDIA NIM, local Ollama)
3. **Deterministic routing**: Pure-function classifier + sequential fallback — no fan-out, predictable costs
4. **Health-aware**: Per-model circuit breakers with cooldowns, persisted across restarts
5. **Voice as separate process**: Complete offline STT/TTS/wake-word in Python — no browser microphone access
6. **Comprehensive test coverage**: 26 unit tests passing, smoke tests, build verification

**For a clipper app sharing Groq/Ollama/GPU/config:**
- **Reuse directly**: Gateway client, health tracker, SSE parser, logger (with redaction)
- **Adapt**: Config schema, settings structure, router logic, orchestrator pattern, memory schema
- **Ignore**: Electron-specific code, voice engine, screen capture, computer control, Windows-specific integrations

The hardcoded API keys in `D:/AI/MultiModel/.env` should be migrated to a secure secret store before any production deployment.