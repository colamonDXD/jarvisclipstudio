# PROJECT_STATE — SnipNova session-recovery brief

*Refreshed 2026-11-15 (IST) at `next-milestone` @ `5ea71bf` — the
frozen, gate-green release tip. Every status claim below is dated and
sourced; if a claim here and the code disagree, the code wins. Detail
lives in the linked documents — this page only points. Previous version
was accurate to `7959ad7` (2026-11-14); everything material has been
re-derived from the sources named at the bottom.*

## What this is

SnipNova (working name) is a local-first AI clip studio: long video in,
captioned 9:16 shorts out. Fork of SupoClip (AGPL-3.0, see `LICENSE` /
`NOTICE`) — FastAPI backend + Next.js frontend + Docker Compose, running
entirely on the user's Windows laptop (ASUS TUF A16, RTX 4050 6 GB);
frontend on http://localhost:3107. Pipeline: transcribe (faster-whisper,
local) → highlight selection (LLM ladder) → niche classification →
profile → EDL per clip → FFmpeg render with face-tracked reframe.

## Where the code lives

| Tree | Branch | Role |
| --- | --- | --- |
| `~/workspace/jcs-next` (this repo) | `next-milestone` | **Canonical integration branch.** Tip `5ea71bf`, **FROZEN** — no merges until the V8 release closes on Luffy's explicit decision. |
| `~/workspace/jcs-rc` | `release-candidate` | Frozen @ `b4e2a18` — source of the shipped "Final" build. Do not develop on it. |
| User's machine, `D:\EditedTool\JarvisClipStudio` | `auto-director` | The installed app. Changes **only** when the user runs an installer he approved. Never edit it remotely. |
| `~/workspace/jcs-floorfix` | `fix/floor-export-parity` @ `f7f66a1` | UNMERGED — merge queue (see below). |
| `~/workspace/jcs-recipes` | `feat/recipes-foundation` @ `74381f8` | UNMERGED — merge queue. |
| `~/workspace/jcs-learning-be` | `feat/learning-engine-backend` @ `4582a0f` | UNMERGED — merge queue (test-only). |
| `~/workspace/jcs-brollmerge` | `refactor/broll-consolidation` @ `fd6e432` | UNMERGED — merge queue. |
| `~/workspace/jcs-retention` | `feat/retention-editing` @ `d1f35f0` | UNMERGED — merge queue. |
| `~/workspace/jcs-perf` | `perf/rtx4050-render-path` @ `19bd1e7` | UNMERGED — merge queue. |
| `~/workspace/jcs-docs` | `docs/state-refresh` | This refresh. Docs only. |

Sprint record (the running log, read it first):
`~/workspace/research_notes/sprint-7day/SPRINT_LOG.md`.
Requirement tracker (78 rows):
`~/workspace/research_notes/master-tracker/TRACKER.md`.
Full-scope audit (86 rows, wider than the tracker):
`~/workspace/research_notes/sprint-7day/FULL_SCOPE_AUDIT.md`.

## Current state (2026-11-15)

- **Integration tip: `5ea71bf`, GATE GREEN, tree clean, deliberately
  FROZEN.** Full gate (2026-11-14): backend **4,656 passed / 0 failed**,
  frontend **899 / 0**, tsc exactly the 11-error baseline set, eslint
  0/0, production `next build` exit 0, smoke 162 routes / 136 paths,
  bridge 0-line diff, migrations 48. Report:
  `research_notes/sprint-7day/INTEGRATED_GATE_5EA71BF.md`.
- **Protected fallback:** tag `v6-candidate-90f1cb4` →
  `90f1cb41b0837a04e4e772ee006857938a7db2fb` (verify by deref
  `^{commit}`; the bare rev-parse returns the annotated-tag object —
  that is not a move). Intact at every check through 2026-11-15.
- **Four-mode acceptance re-run ON THIS TIP: PASS (2026-11-15).**
  32/32 exports, all 1080×1920 h264+AAC. Manual Cutter PASS outright
  (Plain + Auto); the three AI modes PASS on the degraded path —
  proof level: real pipeline + real PostgreSQL + real renders with
  providers unreachable by sandbox design, so AI combos exercised the
  designed failover to the deterministic floor, honestly stamped.
  That is pipeline/mode/honesty proof, **not** AI-quality proof.
  Plain honesty proven on the exports (100% `plain` source-map shots;
  audio cross-correlation ≈0.999 = no music bed). Settings persisted
  byte-identically across a PostgreSQL restart; one run resumed
  through a real mid-run VM crash to 10/10 clips. New defects: NONE.
  Report: `research_notes/sprint-7day/REAL_EXPORT_ACCEPTANCE_ON_TIP.md`.
- **V8 package: BUILT + VERIFIED, NOT APPROVED FOR INSTALL.**
  `~/workspace/packaging-v8/JarvisClipStudio-SnipNova-Consolidated-V8-20261115.zip`
  — 35,075,996 bytes, 1,585 files, SHA-256
  `7807b4576c7d562eeba8d7453609351580ee99c4fa185ca0bfcf609f860145c5`
  (deterministic build; built twice, identical hash; parent-recomputed).
  Manifest verification **14/14 PASS** against the built ZIP (the two
  documents whose absence killed V7 are present); zip assertions
  38/0/0; verify.ps1 expectation re-derived: **167 passed / 0 failed**.
  The pre-approval pack was delivered to Luffy 2026-11-15; **his
  explicit approval alone decides installation.** V7 is superseded
  and remains NOT APPROVED. Reports:
  `research_notes/sprint-7day/V8_PACKAGE_REPORT.md` +
  `PRE_APPROVAL_PACK.md`.
- **Installed on the user's machine: the V7 code tree** (installer
  completed 2026-11-15; stack 9/9; backend + frontend smoke PASS).
  The V8 app tree is **byte-identical** to it — approving V8 ratifies
  a clean baseline; it changes no app code. After a third-party tool
  rewrote five config files on his live tree, a Sanji-prepared restore
  pack returned them to the shipped state (applied mechanically by
  OpenCode at his direction, verified line-by-line): **LiteLLM is now
  VERIFIED HEALTHY** on his install (healthcheck healthy, failing
  streak 0, backend→gateway HTTP 200, live model list = the shipped
  ladder exactly). His backup:
  `D:\EditedTool\JarvisClipStudio-BACKUP-20261115-094546` — the
  rollback decision is his alone.
- **First real-world run on the installed build (2026-11-15):** a
  14:46 finance video produced 4 clips in ~57 minutes. Measured from
  the uploaded exports: exactly **one** B-roll insert across 165 s of
  Auto output vs ≈10 designed (finance density 1.5/min). Which of the
  three silent B-roll loss stages caused it is **retroactively
  unanswerable** — his task's records predate reason-stamping. The
  retention branch (below) closes that gap for every future run.
  Render speed on his machine is the second live friction (CPU-bound
  renders, RAM 99%, GPU ~0% — see the perf branch below).
- **Defect list (frozen tip): P0 / P1 / P2 — none open.** LOW = G-2
  (phantom `schema_migrations` row) + N2 (stale wording in 3 backend
  comments, note only). INFO = F-3, F-5. See `BUG_AUDIT.md`.
- **Scope audit (2026-11-15, 86 rows):** Class 1 IMPLEMENTED AND
  VERIFIED 21 · Class 2 IMPLEMENTED BUT NOT VERIFIED 31 · Class 3
  PARTIALLY IMPLEMENTED 15 · Class 4 IN DEVELOPMENT 0 (at audit time)
  · Class 5 NOT STARTED 14 · Class 6 BLOCKED / NEEDS DECISION 5.
  The 78-row tracker's "MISSING 0" is a classification against written
  requirements only — the audit is the wider instrument.
- **All Nov 22 must-list code items are DONE at branch level**
  (below). Everything remaining is: his V8 decision → the merge batch
  → a full re-gate on the merged tree → his-machine validation.
- All API keys the app can use were added by the user to his local
  `.env` (2026-11-12: Groq, DeepSeek, NVIDIA, OpenRouter, Pixabay,
  Coverr, HF, Cloudflare). Keys exist only there — never in this repo,
  never in any package, never in chat.

## The merge queue (all UNMERGED, parked on the V8 decision)

Merging moves the tip and invalidates tip-bound evidence — the exact
V7 failure mode — so **nothing merges until the release closes**.
Recorded queue order (SPRINT_LOG); a merge-rehearsal lane is
discovering conflicts/totals in a scratch tree — read its report
before starting the real batch.

1. **`fix/floor-export-parity`** (`f7f66a1`) — B-roll gate contract
   (explicit switch authoritative; legacy chip honoured only when the
   switch was never set; Plain always OFF) + floor/degraded exports
   carry a hook title derived from their own words. Rendered proof on
   disk (`~/workspace/floorfix-proof/`).
2. **`feat/recipes-foundation`** (`74381f8`, 9 commits) — Recipes:
   storage + API, `recipe_id` on task creation (explicit fields win,
   unknown/cross-user → 422, nothing created), watch-folder autopilot
   `recipe_id`. Carries **two migrations** (49th + 50th:
   `20261115_0001_recipes.sql`, `20261115_0002_watch_folder_recipe_id.sql`)
   → the merged tree's verify expectation must be **re-derived**
   (migrations become 50); never carry 167 across a merge.
3. **`feat/learning-engine-backend`** (`4582a0f`) — test-only: the
   end-to-end proof that the Learning Engine loop (verdict → storage
   → selection/EDL consumption) is wired on the shipped backend.
4. **`refactor/broll-consolidation`** (`fd6e432`) — one B-roll
   resolution path (v2 resolver everywhere); the legacy Pexels route
   retired to stored-footage compatibility; editor search/suggest,
   AI-edit AddBRoll and `/broll/status` un-gated from the Pexels key.
5. **`feat/retention-editing`** (`d1f35f0`) — every undelivered B-roll
   insert stamped with stage + reason; per-task asset de-duplication
   ledger; hard cuts inside continuous speech; opening push-in on
   Auto clips (Plain untouched).
6. **`perf/rtx4050-render-path`** (`19bd1e7`) — ffmpeg thread budget
   (his machine → 8; Eco → 4; quality constants byte-untouched) +
   encoder/probe diagnostics stamped into persisted task timings
   (the silent NVENC→CPU fallback is no longer silent).
7. **Python3 healthcheck probe** — adopt into the repo's
   `compose.override.yml` template the urllib probe his live override
   already runs (the shipped curl probe can never pass: the LiteLLM
   image ships no curl). No branch yet — small commit at merge time.
8. **`docs/state-refresh`** — this refresh (docs only).

## Programme state

- The 3-day quality programme (2026-11-12 directive) is **complete**:
  Day 1 correctness (settings integrity, independently verified),
  Day 2 AI quality (offline phase done; live bake-off still blocked —
  his keys, by design), Day 3 integration + release safety (gate +
  on-tip acceptance + V8 package, above).
- **Nov 22, 2026 is the HARD DEADLINE** (Luffy's 2026-11-15 ruling;
  Nov 24 is **not** an alternate or fallback). Nov 17 is the
  release-integrity deadline, not a start date.
- **Scope split (2026-11-15): the frontend is Luffy's** (his Claude
  Code); **Sanji is backend-only** — backend, APIs, contracts, docs.
  No other AI tool touches the backend.
- Deferred past Nov 22 (disclosed to Luffy; his explicit approval of
  the deferral list is still pending — silence is not approval):
  whole-task natural-language AI edit (full version), full Trend
  Radar system, retake detection, full motion-graphics suite,
  caption translation/dubbing at production quality, adaptive VRAM
  manager on-GPU validation.

## Pending user-machine validation (after the release closes)

The 13 CRITICAL rows of
`research_notes/sprint-7day/V7_INSTALL_VALIDATION_PLAN.md`, plus:
live AI-selection quality + ClipPlans with providers live; the live
model bake-off (harness + protocol ready; runs inside his worker
container so keys never leave his machine); the RTX 4050 benchmark
kit (`research_notes/sprint-7day/RTX4050_BENCHMARK_KIT.md`, ~10 min,
also reads the 57-minute task's stage split); Bert re-run (caption/
title face fix); TJR Plain (first true Plain render) + TJR Auto
re-runs; Cloudflare hop live check; Eco vs normal benchmark; a fresh
Auto run whose EDLs carry the new B-roll stamps. Small user actions
still owed (non-urgent): pyannote terms-accept on Hugging Face;
DeepSeek balance check (his account); optional key rotation (his
call, after a third-party tool processed his `.env`).

## Standing rules — never violate

1. **Free-only.** The user pays ₹0 (hard cap ₹300 total; do not
   re-raise paid options).
2. **Keys** are typed by the user into his local `.env` only — never
   pasted to any chat/agent, never committed, never in a package.
3. **Gemini is never in any model chain** (its API answers 411 here);
   DeepSeek is the primary director, Groq `openai/gpt-oss-120b` is the
   primary runtime LLM, NVIDIA NIM / OpenRouter-free / Cloudflare are
   fallbacks, Ollama is the local floor. Retired
   `llama-3.3-70b-versatile` is never recommended.
4. **Plain — just cuts is the create-page DEFAULT** (no B-roll / music
   / template inserts; tight full-screen crop). Auto is opt-in.
5. **Verify before merge; test before handover.** A lane's verdict is
   a claim, not evidence — independently verify decisive claims.
   Judge renders from extracted frames, never thumbnails.
6. **No changes to the user's installed app, backups or local config
   without his explicit approval.** His stop instructions are
   absolute.
7. One complete verified ZIP per delivery; every command pasted in
   full in the current message; downloads to `D:\Downloads`.
8. The 9 release-discipline rules in `~/AGENTS.md` govern every
   release claim (exact-build evidence; PASS / FAIL / NOT RUN /
   STALE / NOT VERIFIED used literally; manifest verified against the
   built ZIP; no premature completion claims).

## How to run the gate

The sprint gate (serial, on the settled tip): backend pytest full
suite (Q1–Q4) → live-PostgreSQL tier incl. the roundtrip suite on a
fresh pristine DB → frontend vitest + `tsc` + full eslint (exit 0) +
production `next build` → migration-set check → bridge-file check
(0-line diff on the 4 reference-styles proxy files vs `61ad303`) →
smoke (162 routes / 136 paths) → changelog test. Sanctioned venv:
`~/workspace/jcs-modes/backend/.venv/bin/python` (pytest 9.0.3),
`OMP_NUM_THREADS=2`, `--no-cov`; pytest output to a LOG FILE (it hangs
at interpreter exit — kill by exact PID after the summary lands).
Last full gate: tip `5ea71bf`, backend 4,656/0, frontend 899/0,
`next build` exit 0 (`INTEGRATED_GATE_5EA71BF.md`). After the merge
batch, the gate re-runs on the merged tree before anything ships.

## Registries

- `docs/registries/MODEL_REGISTRY.md` — every callable model + chains.
- `docs/registries/ASSET_SOURCES.md` — stock / music / SFX / fonts.
- `docs/registries/THIRD_PARTY_LICENSES.md` — licence picture.
- `docs/registries/DOWNLOAD_MANIFEST.md` — package vs runtime downloads.
- API contract for the frontend: `research_notes/sprint-7day/API_CONTRACT_PACK.md`
  (152 endpoints verified against handlers; discrepancies D1–D5 —
  the frontend needs `/api/learning/[...path]` and, post-merge,
  `/api/recipes/[...path]` proxy routes).

*Sources for this refresh: SPRINT_LOG.md (through 2026-11-15 ~17:53
IST), FULL_SCOPE_AUDIT.md, INTEGRATED_GATE_5EA71BF.md,
REAL_EXPORT_ACCEPTANCE_ON_TIP.md, V8_PACKAGE_REPORT.md,
PRE_APPROVAL_PACK.md, RETENTION_LANE_REPORT.md,
LEARNING_ENGINE_API_CONTRACT.md, API_CONTRACT_PACK.md, and the branch
inventory verified with git on 2026-11-15.*
