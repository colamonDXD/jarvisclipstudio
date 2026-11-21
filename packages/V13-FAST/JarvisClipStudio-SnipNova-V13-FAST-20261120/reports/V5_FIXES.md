# V5 Changes — SnipNova Consolidated Release v5 (2026-11-15)

**The v4 build plus everything merged since, in one ZIP.** This tree
is branch `next-milestone` @ `357628ae1c062ea768d984b98d6402daa0bcc9db`
— the v4 tree (`d3eb750`) plus **9 commits** (132 commits over the v3
tree `49a134e`). Your installed build at install time is the
Consolidated v4 release; this build is a strict superset of it, and
it equally installs over v3 plus its three hand-applied patches.
**No new database migration** — the schema stays at 47 migrations.

## What is new since v4

1. **The gateway fallback chains are one multi-provider ladder**
   (`86d08a5`, merged @ `3e1ee41`; CHANGELOG entry `215e78c`).
   Before, the chains could dead-end: the premium chain
   (`clip-assured`) ended on a rate-limited free remote, the local
   model sat mid-chain, DeepSeek sat *after* local, and `groq-20b`
   had no fallback chain at all. Now every code-called chain walks
   the providers in a fixed order and **ends at the local model on
   your own machine**:
   - `clip-assured` (premium picks): NIM ultra → NIM DeepSeek →
     OpenRouter free → DeepSeek → **local Ollama (final)**
   - `clip-fast` (volume picks): NIM DeepSeek → Groq 120B → Groq
     20B → OpenRouter free → DeepSeek → **local Ollama (final)**
   - `groq-20b`: Groq 120B → NIM ultra → NIM DeepSeek → OpenRouter
     free → DeepSeek → **local Ollama (final)**
   - `local-qwen`'s own chain is the documented exception (it
     cannot contain itself; it is consulted only when local itself
     failed).
   DeepSeek sits after the free routes and before local, exactly
   as ordered. The gateway **model list is byte-identical**, so
   the brain upgrade's `reasoning_effort` allow-list on the Groq
   routes is untouched. **No Gemini in any chain.** The order is
   pinned by the gateway config suite (14 tests). Routes wake as
   you add keys — Groq is already in; NVIDIA and OpenRouter
   passthroughs are already in your live `compose.override.yml`;
   the DeepSeek hop additionally needs one passthrough line in
   that file (KNOWN_ISSUES G-V5-1). Report:
   `MULTIPROVIDER_CHAIN_REPORT.md`.
2. **Briefs from a screenshot, with hard never-use enforcement**
   (schema `cb237b1`, extraction schema `d21ce25`, screenshot
   ingestion + enforcement `9b91a7e`, test suites `5763b5a`,
   brief UI `ec9733e`; merged @ `357628a`). Brief schema v2 gains
   three field families, all optional and additive (a brief
   without them keeps its exact previous shape):
   - `excluded_ranges` — footage that must never be used;
   - `flag_points` — single timestamps with a review category
     (licence plate, branding, copyright music, sensitive topic,
     alcohol, review, other);
   - `key_timestamps` — the brief's own named moments, which now
     generate candidate clips of their own.
   A new `POST /briefs/extract-image` reads an uploaded screenshot
   with on-machine OCR (tesseract, already inside the app image —
   `backend/src/auto_director/brief_image.py`) and returns a
   **draft**: nothing is stored and nothing applies until you
   review and confirm it in the panel, exactly like pasted briefs.
   The extractor is section-aware: a range printed under a
   "Content to avoid" heading is extracted as an exclusion, never
   as an allowed range (before this lane, it could be read exactly
   backwards). Exclusions are **hard at every layer**: the
   candidate pool is clamped when moments are picked, the final
   slate is re-clamped at the brief gate, keep-ranges are
   subtracted again at render and at re-render, and the
   post-render audit applies the same subtraction. A finished clip
   overlapping an excluded range is a hard violation; a clip
   containing a flag point is stamped **needs-review** and can
   never be silently "compliant". Measured on your diamonds /
   Botswana screenshot: OCR recovered **25/25 timecodes**;
   deterministic extraction scored key timestamps **5/5**, the
   excluded range **1/1** (0:47–0:53, with `allowed_ranges`
   correctly empty) and flag points **18/18 with categories**.
   19 new tests plus the neighbour suites. Report:
   `BRIEF_IMAGE_REPORT.md`.

## Deliberately NOT in this build

- **GPT-6 Luna**: checked again live for this release — free only
  inside the ChatGPT app; its API is paid ($0.10 in / $0.50 out per
  1M) and no `:free` route exists for it, so it cannot be powered
  by a free key and is in no chain. (See the ladder lane's report.)
- **Denoise** (DeepFilterNet/FFmpeg-native pre-stage): measured
  and rejected earlier (it worsened transcription); still not
  merged, still not in this ZIP.
- The **Postgres execution tier** runs only when a test Postgres is
  provided; it is a test-tier, not a runtime feature.
- No new niche profiles; additive-only rule unchanged.
