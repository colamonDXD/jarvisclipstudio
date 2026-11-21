# DOWNLOAD MANIFEST

*What a release package contains vs what downloads later. Package
figures are the measured V5 package (independent audit,
`research_notes/sprint-7day/V5_PACKAGE_AUDIT.md`); the next release is
the same shape plus the deltas noted. Runtime-download sizes are
given only where an in-tree source states them.*

## 1. Inside the release ZIP (V5, measured)

| | |
| --- | --- |
| ZIP size | 34,799,351 B (~34.8 MB) |
| Uncompressed | 48,962,244 B |
| Entries | 1,745, all under one wrapper folder |
| SHA-256 | `ea6417a774d700971f8cc40ba2c9165f1a1279f1e240d4a8d7f84efd334406d5` (V5) |

**Contents:** the full source tree at the release commit (backend —
including `backend/fonts/` and the bundled YuNet model — frontend
source, `mcp/`), `docker-compose.yml` + the `compose.override.yml`
template, `litellm_config.yaml`, `init.sql`, the SQL migration set
(47 files in V5; the tree now carries 48 — the B-roll toggle added
one), `docs/`, `assets/banner.jpg`, a `TRACKER.md` snapshot,
`reports/` (42 evidence files in V5), and the delivery machinery:
`install.ps1` (backup-first; refuses to proceed without
`-ExpectedSha256` matching), `verify.ps1`, rollback support, and the
install guide.

**Deliberately NOT in the package:**

- `.env` or any key — only `.env.example`. The user's `.env` on his
  machine is preserved untouched by the installer.
- `.git`, `node_modules`, `__pycache__` (asserted absent by
  `zip_assertions.py --mode final`, 38 checks).
- **All model weights except YuNet and YOLOX** (YuNet 233 KB, MIT;
  YOLOX 9.1 MB, Apache-2.0; both bundled with their licences) —
  Whisper, SigLIP, Kokoro and Demucs weights download at
  runtime (§2).
- **All audio assets** — no music or SFX files ship (see
  `ASSET_SOURCES.md` §4); the on-machine library is preserved across
  installs.
- The Ollama host app and its models.

Tree deltas since V5 that the next ZIP will add: the 48th migration,
`tools/nim-bakeoff/`, `PROJECT_STATE.md` and `docs/registries/`.

## 2. Downloads at install / build time (on the user's machine)

| What | When | Size |
| --- | --- | --- |
| Docker base images + service image builds | `docker compose build` during install | Not recorded in-tree |
| Python dependencies from PyPI (incl. `torch==2.13.0`, CPU or CUDA wheel per host) | Backend/worker image build | Not recorded in-tree |
| Frontend dependencies from npm (committed pnpm lockfile) | Frontend image build | Not recorded in-tree |
| `tesseract-ocr` + English data (apt) | Backend image build (`backend/Dockerfile`) | Not recorded in-tree |
| Ollama + `qwen3:8b` model | Host-side, user-installed once (the local LLM floor; reached via `host.docker.internal`) | Not recorded in-tree |

## 3. Downloads at runtime (first use, then cached)

| What | Trigger | Lands in | Size (in-tree source) |
| --- | --- | --- | --- |
| faster-whisper weights for `WHISPER_MODEL_SIZE` (deployment default `medium`) | First transcription | Worker container's model cache (persists in the app volumes) | Varies by size; large-family fp16 weights ~1.6 GB class per `docs/transcription-speed.md` |
| SigLIP `google/siglip-base-patch16-224` | First render with the B-roll vision veto enabled (flag default OFF) | Worker model cache | ~810 MB (`media/broll_vision.py`) |
| Kokoro-82M + voice packs | First voiceover-enabled task (flag default OFF) | Worker model cache | ~315 MB; voices ~0.5 MB each (`media/voiceover.py`) |
| Demucs `htdemucs` weights | First separation-enabled task (`SEPARATION_ENABLED` default OFF + per-task opt-in) | `SEPARATION_MODELS_DIR` / worker cache | Not stated in-tree |
| Stock footage (Pixabay/Coverr) | Per B-roll cue at render time | `<temp_dir>/stock_library/` + `manifest.json` provenance (downloaded once, reused) | Per asset; recorded in the manifest |
| Source videos | Task creation from a URL (yt-dlp) | App media storage | The user's own content; bounded by the duration/upload caps in `.env.example` |
| UI fonts (Inter, JetBrains Mono, Space Grotesk) | Frontend build / first render via `next/font/google` | Next.js font cache | Not recorded in-tree |

**Rule of thumb for support questions:** a fresh install is a ~35 MB
download; the *first week of use* is what pulls the gigabytes
(Whisper weights + Docker images + Ollama), all of it cached on the
user's D: drive thereafter, and none of it re-downloaded by later
release ZIPs.
