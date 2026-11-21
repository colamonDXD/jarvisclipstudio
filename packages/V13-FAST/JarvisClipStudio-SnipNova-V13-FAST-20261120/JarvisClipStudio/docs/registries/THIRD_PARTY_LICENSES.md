# THIRD-PARTY LICENCES

*The maintainer-level picture at `next-milestone` @ `6793620`
(2026-11-12). A licence is marked **verified** only where an in-tree
file states it; anything else is **UNVERIFIED in-tree** — meaning the
repo carries no licence text or statement for it, not that the
upstream licence is unknown to the world. Check the upstream project
before redistributing anything marked UNVERIFIED.*

## 1. This product

- **SnipNova itself: GNU AGPL-3.0** (`LICENSE` at repo root).
- Built on the open-source **SupoClip** project
  (https://github.com/FujiwaraChoki/supoclip); `NOTICE` carries the
  required attribution. AGPL obligations that matter to a maintainer:
  source must be offered to anyone the software is distributed to,
  **and** to users who interact with it over a network (§13) —
  modified versions included. Branding added by SnipNova does not
  change the licence of the combined work.

## 2. Models

| Component | Role | Licence | In-tree source | Status |
| --- | --- | --- | --- | --- |
| YuNet face detector (`face_detection_yunet_2023mar.onnx`) | Face detection | MIT, © 2020 Shiqi Yu | `backend/src/media/models/LICENSE-yunet` (bundled with the model) + source/SHA in `models/README.md` | **Verified** |
| YOLOX person detector (`object_detection_yolox_2022nov_int8.onnx`) | Person detection on no-face analysis samples (subject-presence fallback) | Apache-2.0 (OpenCV Zoo model folder; YOLOX upstream © Megvii) | `backend/src/media/models/LICENSE-yolox` (bundled with the model) + source/SHA in `models/README.md` | **Verified** (licence text re-read first-hand from the zoo repo, 2026-11-16) |
| SigLIP `google/siglip-base-patch16-224` | B-roll vision veto | Apache-2.0 | `media/broll_vision.py` header ("verified") | **Verified** (in-tree claim) |
| Kokoro-82M weights | Voiceover TTS | Apache-2.0 | `media/voiceover.py` header | **Verified** (in-tree claim) |
| `kokoro` pip package | Voiceover runtime | Not stated in-tree | `backend/pyproject.toml` (dependency only) | UNVERIFIED in-tree |
| Demucs code | Separation engine | MIT | `media/separation.py` header | **Verified** (in-tree claim) |
| Demucs `htdemucs` **weights** | Separation model | **Explicitly unclear** — the author's README states the training data includes research-only material | `media/separation.py` header | **Unclear by record** — weights are NEVER bundled or redistributed by this app; they download once, at first enabled use, on the user's own machine. Feature is double-gated OFF by default. |
| SenseVoiceSmall weights (FunAudioLLM) | Optional reaction-feature pass: laughter / applause / cheering / emotion tags for moment ranking (`live/sensevoice_scan.py`, `SENSEVOICE_FEATURES`, OFF by default) | **FunASR Model Open Source License Agreement v1.1** (Alibaba Group) — §2.2 attribution + model-name retention complied with here and in `NOTICE` | Licence text shipped at `docs/registries/FunASR_MODEL_LICENSE_v1.1.txt`; weights are NEVER in git — official weights only, downloaded pinned by SHA-256 by `tools/live/fetch_sensevoice.py` (repo `FunAudioLLM/SenseVoiceSmall` @ `3847d57b`, model.pt SHA-256 `833ca2dc…a3ea`) | **Verified** (licence text + pins read first-hand from the official sources, 2026-11-20) |
| FunASR toolkit (`funasr` pip package) | SenseVoiceSmall inference runtime (optional; the pass degrades to shout-only without it) | MIT | `live/sensevoice_scan.py` header; installed by the operator, not a core dependency | **Verified** (upstream LICENSE) |
| faster-whisper / CTranslate2 | Transcription engine | Not stated in-tree | `backend/pyproject.toml` | UNVERIFIED in-tree |
| Whisper model weights (via faster-whisper / openai-whisper) | Transcription models | Not stated in-tree | `backend/pyproject.toml`, `.env.example` | UNVERIFIED in-tree |
| Ollama `qwen3:8b` | Local LLM floor | Not part of this repo (runs on the host's Ollama install) | `litellm_config.yaml` | UNVERIFIED in-tree (out of scope of the package) |
| Hosted LLM APIs (Groq, DeepSeek, NVIDIA NIM, OpenRouter) | Cloud brains | Governed by each provider's API terms, accepted by the user when he created his keys | `litellm_config.yaml` | Provider terms, not code licences |

## 3. Libraries and platform pieces

| Component | Role | Licence in-tree |
| --- | --- | --- |
| NeonClipping (`iamajmeena/neonclipping`) | Adapted code: the "shout" signal (2-4 kHz energy share x loudness, local z) in `live/sensevoice_scan.py` | MIT (upstream LICENSE; attribution in `NOTICE`) |
| PyTorch (`torch==2.13.0`, cpu/cuda extras) | ML runtime for Kokoro/Demucs/SigLIP paths | UNVERIFIED in-tree (pinned in `backend/pyproject.toml`) |
| LiteLLM | Model gateway container | UNVERIFIED in-tree (`litellm_config.yaml`, compose) |
| Tesseract OCR (`tesseract-ocr`, `-eng`) | Brief-from-screenshot OCR | UNVERIFIED in-tree (apt install in `backend/Dockerfile`) |
| FFmpeg | All media rendering | UNVERIFIED in-tree (system dependency of the images) |
| Next.js / React and the pnpm dependency set | Frontend | UNVERIFIED in-tree as a set — lockfile is committed (`frontend/pnpm-lock.yaml`); per-package licences are not inventoried in the repo |
| Python dependency set (FastAPI, pydantic-ai, etc.) | Backend | UNVERIFIED in-tree as a set — `backend/pyproject.toml` + lockfile; no per-package inventory |
| PostgreSQL / Redis | Data services (compose images) | UNVERIFIED in-tree |
| NeonClipping (upstream project) | Design reference only: YouTube timed captions as the transcript source, implemented originally in `backend/src/media/yt_captions.py`; no upstream code in-tree | MIT (upstream); credited in `NOTICE` and the module header |
| wordcaps (upstream project, commit ff520f4) | Adapted code in-tree: word-edge alignment onto the loudness envelope + caption-coverage QA (`backend/src/media/word_align.py`) | MIT (upstream LICENSE read first-hand in the Harvest-2 audit, 2026-11-19); credited in `NOTICE` and the module header |
| PeakCut (`haziqahmedqasmi/PeakCut`) | Adapted code in-tree: the LLM caption-word tidy pass — exactly one output entry per input word, timings untouchable, originals kept on any anomaly (`backend/src/media/caption_polish.py`, `CAPTION_WORD_FIX`, OFF by default) | MIT (upstream LICENSE read first-hand in the Harvest audit, 2026-11-19); credited in `NOTICE` and the module header |
| NeonClipping (`iamajmeena/neonclipping`) | Adapted code in-tree: Devanagari -> Hinglish transliteration — common-word dictionary + letter rules with schwa deletion, per-word timings preserved (`backend/src/media/translit.py`, `TRANSLIT_HINGLISH`, OFF by default) | MIT (upstream LICENSE read first-hand in the Harvest audit, 2026-11-19); credited in `NOTICE` and the module header |

## 4. Media assets and fonts

Stock footage, music/SFX and fonts carry their own terms — see
`ASSET_SOURCES.md`. Headlines: Pixabay and Coverr licence wordings are
recorded per-asset in the stock library manifest; the on-machine
music/SFX library's provenance is **UNVERIFIED** (nothing ships in the
repo); caption fonts ship **without licence files** in
`backend/fonts/` and are UNVERIFIED in-tree per font.

## 5. Maintainer rules

1. Never bundle model weights whose licence is unclear — the Demucs
   precedent (download-at-first-use on the user's machine, feature
   OFF by default) is the pattern.
2. Never add an asset (track, font, clip) to the repo without adding
   its licence to `ASSET_SOURCES.md` in the same change.
3. Keep `LICENSE` and `NOTICE` intact in every package — AGPL
   distribution without them is a licence breach, not a style issue.
