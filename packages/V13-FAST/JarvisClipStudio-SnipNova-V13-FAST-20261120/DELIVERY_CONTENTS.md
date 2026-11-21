# Delivery Contents — SnipNova Consolidated Release v12.1 (2026-11-19)

**The v12 tree plus the combined series and the UI overhaul, in one ZIP:**
`JarvisClipStudio-SnipNova-Consolidated-V12.1-20261119.zip`.
The app tree is branch `next-milestone` @ `3900c3da` — the v12
tree (`49cfad22`) plus Claude's series A (asset library, quality
steps, transition library, font pack, live MVP tools) and series
B (live clipping in the app, the VOD fast clipper, the Layout
picker, profanity masking, the 30 s floor, the plain-path music
fix), Sanji's gate fixes (cold-open floor fix, live-SQL coverage,
font eval re-base, LiteLLM gateway timeout) and the UI overhaul
round 2. Gates on this tree: the full backend gate, the full
frontend gate and a one-combo smoke record (the smoke record is **NOT RUN** (the sandbox attempt was terminated ~53 minutes into the analysis passes, before any export; the report states it plainly, with the attempt record)). It
installs **over your current v12 build** — including the
live-clipping changes Claude added directly to your install
afterwards; every one of them is in this tree — the installer
backs up first, and it is a strict superset of every earlier
release. **The v12 ZIP is superseded by this package.**

| Path | What it is |
| --- | --- |
| `JarvisClipStudio/` | The complete project tree (above): everything in the v6 build, plus the **vocals-stem threading fixes** (re-renders, regenerations and hook previews of separation-opt-in tasks keep the clean vocals), the **Reference Style transition honesty fix** (director guidance, documented as such), the **editor LOW items** (words-per-caption-line control in the quick editor, both Montserrat caption fonts selectable, combine hint disclosing hidden-lane drops), the **Montserrat font type fix**, and the **B-roll concept-phrasing improvement** (subject-led concepts, competing-room avoid lists). Inside it: `CHANGELOG.md`, `TRACKER.md` (the **2026-11-14 re-scored** 78-requirement copy — refreshed for this package), `AI_MODEL_ROUTING.md`, and the recovery-doc set (`PROJECT_STATE.md`, `FEATURE_PROGRESS.md`, `BUG_AUDIT.md`, `TEST_RESULTS.md`, `NEXT_SESSION_PROMPT.txt`). |
| `install.ps1` | The same proven backup-first installer flow (checksum → disk check → staging → **backup** → validation → install with your files copied back → build/start → smoke checks), including the optional **step 6b** watch-folder mount for `D:\Jarvis_Autopilot` (asked, default NO, backend + worker only, guarded skip-with-warning). It carries the v6 step-5 logic fix (on backup-validation failure the old stack restarts from your untouched project folder). Diffs vs the v12 package's script are listed below — naming/header text **plus three deliberate logic changes** (the live-worker override wiring in step 6, `LIVE_CLIPPING=on` for this install, and the ~60 s LiteLLM smoke retry in step 8), each itemised in the differences section. |
| `rollback.ps1` | Restores the backed-up project folder and database. The failed new tree is renamed and kept, never deleted. `-RestoreVolumes` is the documented nuclear option (default off). |
| `verify.ps1` | Read-only post-install checks. This build's list: **all 55 migration files** present + applied (**three new migrations** in this release — `20261118_0001_live_clipping.sql`, `20261118_0002_task_hook_title_enabled.sql`, `20261119_0001_live_session_progress.sql`; the schema rises to 55), 88 feature-file checks (**+12 vs v12**: the live VOD engine, the live API, the live repository, the live worker, profanity masking, the plain-path audio finish, the asset library, the asset licence manifest, the transition library, the font-pack manifest, the Live page, the Layout picker), containers (**8** — live-worker is now counted), GPU, NVENC, pacing passthrough, voiceover state, gateway, health endpoints, Ollama. **Expected summary: 218 passed, 0 failed** (recounted for this build — arithmetic below; the list grew, and no check was removed or weakened). |
| `INSTALLATION_GUIDE.md` | Step-by-step install: checksum, Expand-Archive extraction (explicit, with the double-nested-folder warning), PowerShell pre-flight parse check (direct in-session commands), per-step expectations including step 6 and 6b, the API-keys note (the DeepSeek AND Cloudflare passthrough lines on your preserved override), the 90-minute duration-cap note, failure guide, rollback. |
| `DELIVERY_CONTENTS.md` | This file. |
| `KNOWN_ISSUES.md` | The v12 record carried forward (v12 addendum: what v12 added + gaps G-V12-1…G-V12-8; earlier addenda below) **plus a v12.1 addendum at the top**: what v12.1 adds (live clipping + VOD fast clipper, the Layout picker for every niche + title on/off, the 30 s floor, profanity masking, the plain-path music fix, the licence-gated asset library, the transition library, the font pack with THEBOLDFONT removed, the dormant quality steps, the LiteLLM gateway timeout, the UI overhaul round 2, the installer wiring) and the new gaps **G-V121-1…G-V121-4** (smoke verdict as recorded, live Groq captions + GPU encode not yet proven on a real stream, the extended library as a post-install fetch for fresh installs, the open 30 s-floor/re-render question), each stated plainly in the addendum. G-V12-7 is CLOSED by this package (Step 1 + asset library + R2/3 libraries are in the tree, dormant). |
| `VALIDATION_CHECKLIST.md` | The v12 checklist (items 1–58) carried forward, **plus a v12.1 addendum with items 59–61**: confirm the v12.1 reports are in the folder, the superseded-v12 note (with the installed-app changes + extended-library preservation), and the re-runs that judge v12.1 (the Live page on a finished stream and on a live session, the Layout picker on a non-gaming niche + title off, profanity masking, face-on-top music present + Plain still silent, the Speed comparison, the pending step approvals). Expected verify line updated: **218 passed, 0 failed**. |
| `SETTINGS_INTEGRITY_AUDIT.md` | **New in this package** — its absence from the v7 ZIP was release-gate failure #2a. The Day-1 settings-integrity audit: every user-selectable setting traced across 9 stages on the real create/read path; the Plain read-back defect (F-1), its fix, and the independent verification (26 settings — 22 proven end-to-end, F-2/F-4 fixed). Byte-identical to the audited original (SHA-256 `298fed5a…`). |
| `AI_MODEL_BAKEOFF.md` | **New in this package** — release-gate failure #2b. The Day-2 model bake-off record: offline phase proven; **live phase PENDING** — no live call has been made and no winner is named anywhere in it; the shipped ladder is the proven one. Byte-identical to the original (SHA-256 `d5acafa8…`). |
| `reports/V121_PACKAGE_RECORD.md` | This package's record: what the v12.1 build changed vs the v12 package (series A + B, Sanji's gate fixes, the UI overhaul merge, the grown verify list, the installer changes), with the gate and acceptance pointers. |
| `reports/V121_INTEGRATION_REPORT.md` | **New in this package:** the integration lane's record — the series application (with the byte-identical overlap proof vs Sanji's earlier stack), the three real defects the gate found and their fixes (the cold-open floor bug, the uncovered live SQL, the stale font eval), the database-backed suites, the Postgres tier, the licence gate, and the frontend merge adjudications. |
| `reports/REAL_EXPORT_ACCEPTANCE_V121_SMOKE.md` + `reports/exports_v121_smoke_manifest.csv` | **New in this package:** the smoke record for this exact tree — the smoke record is **NOT RUN** (the sandbox attempt was terminated ~53 minutes into the analysis passes, before any export; the report states it plainly, with the attempt record). |
| `reports/V12_PACKAGE_RECORD.md` | Carried from the v12 package: what the v12 build changed vs the v11 package (the integration stack, the download-quality and shell merges, the grown verify list, the refreshes), with the gate and acceptance pointers. |
| `reports/FRONTEND_GATE_5480A9DA.md` + `reports/DELTA_GATE_E015B809.md` | **New in this package:** the gate records for this tree — the full frontend gate on the exact packaged tip `5480a9da` (vitest 939 passed / 0 failed outside the known PGlite environment family, every failed file proven PASS solo; tsc 0; next build exit 0, 75/75 pages; changelog 4/4; smoke census 168 routes / 139 paths identical to baseline; backend config suites 29/0), and the earlier delta gate on the frontend-completion + polish combination. |
| `reports/REAL_EXPORT_ACCEPTANCE_V11_SMOKE.md` + `reports/exports_v11_smoke_manifest.csv` | **New in this package:** the smoke acceptance on this exact tree — one Full AutoClip + Plain render end-to-end through the real service path (create → process → export at 1080×1920, frames judged). The full eight-combo acceptance was NOT re-run for v11: the backend is byte-identical to the v10 tree that passed 8/8; the report states that reasoning plainly. |
| `reports/V10_PACKAGE_RECORD.md` | Carried from the v10 package: what the v10 build changed vs the v9 package (the post-v9 merges, the verify-list decision, the refreshes), with the gate and acceptance pointers. |
| `reports/REAL_EXPORT_ACCEPTANCE_V10.md` + `reports/exports_v10_manifest.csv` | Carried from the v10 package: the four-mode real-export acceptance re-run on the v10 tree (`901ae252`) PLUS the streamer journey path — 33/33 exports at 1080×1920; the AI-dependent combos PASS on the degraded path. **This is the acceptance of record for the backend this package ships** (byte-identical). |
| `reports/FULL_REGATE_327886B3.md` + `reports/MERGE_STACK_AND_FRONTEND_REPORT.md` | Carried from the v10 package: the gate records for the v10 tree — the face-stack + frontend merges with their targeted re-gate, and the full re-gate on the merged tree (backend 5,070 passed / 3 failed, all three attributed and later closed by test-only fixes). |
| `reports/REACTION_API_CONTRACT.md` + `reports/REACTION_GATE_FIX_REPORT.md` | Carried from the v10 package: the additive reaction-moment API contract (PROPOSAL — the fields the backend lanes implement next) and the reaction-gate fix record (why the stacked layout never engaged on gameplay streams, the measured fix, and its rendered proof). |
| `reports/MODEL_CONFIG_AUDIT.md` + `reports/INTEGRATION_NOV17.md` | Carried from the v10 package: the model-config audit behind the DeepSeek slug correction and the HF_TOKEN pass-through (with the NVIDIA-names verdict), and the Nov 17 cross-feature integration record for the merged tree. |
| `reports/V9_PACKAGE_RECORD.md` | Carried from the v9 package: what the v9 build changed vs the v8 package (the merged tree, the verify-list decision, the refreshes), with the gate and acceptance pointers. |
| `reports/MERGE_BATCH_AND_GATE_REPORT.md` + `reports/MERGE_14_15_AND_REGATE_REPORT.md` | Carried from the v9 package: the merge records for the v9 tree — the 13-branch batch with its full re-gate (PASS, backend 4,939/0), then items 14–15 with the targeted re-gate (PASS), including the zoom defect found, attributed and fixed mid-merge (`f49da36`). |
| `reports/REAL_EXPORT_ACCEPTANCE_V9.md` + `reports/exports_v9_manifest.csv` | Carried from the v9 package: the four-mode real-export acceptance re-run on the v9 tree (`20f903b`) — 32/32 exports; Manual Cutter PASS outright; the AI-dependent combos PASS on the degraded path; settings persistence across a database restart PASS. |
| `reports/FRAMING_MODE_API.md` | Carried from the v9 package: the framing-mode contract — the `framing_mode` field (auto / fit / crop), its default, read-back, 422 behaviour, and plain-words meanings. The backend shipped in v9; the create-page dropdown shipped in v10. |
| `reports/RETENTION_LANE_REPORT.md`, `reports/TASK_TEXT_BANDS_LANE_REPORT.md`, `reports/LEARNING_ENGINE_API_CONTRACT.md`, `reports/API_CONTRACT_PACK.md`, `reports/RTX4050_BENCHMARK_KIT.md`, `reports/NONFACE_SUBJECT_SPIKE.md` | Carried from the v9 package: the lane records behind the v9 features — retention forensics and the three silent-loss stages, per-task text bands, the Learning Engine contract (152 endpoints verified in the pack), the RTX 4050 benchmark kit for your machine, and the non-face subject spike that scoped the face-evidence stack this build ships. |
| `reports/V8_PACKAGE_RECORD.md` | Carried from the v8 package: the v8 repackage record (what it changed vs the v7 package — contents + evidence only, no code) and the two gate-failure closures. |
| `reports/REAL_EXPORT_ACCEPTANCE_ON_TIP.md` + `reports/exports_on_tip_manifest.csv` | Carried from the v8 package: the four-mode real-export acceptance re-run on the v8 tree (`5ea71bf`) — 32/32 exports; Manual Cutter PASS outright; the AI-dependent combos PASS on the degraded path; F-R1/F-R2 confirmed fixed at rendered level; settings persistence across a database restart PASS. |
| `reports/RELEASE_GATE_NOV15.md` | Carried from the v8 package: the 2026-11-15 release-gate record (verdict DO NOT SHIP on the v7 package) that the v8 package answered. |
| `reports/V7_FIXES.md` | The v7 change record: the 20-commit delta over v6 with merge points, the gate numbers on the exact shipped tip, the rendered-proof closures, and what is deliberately not in this build (no new migration; no bake-off winner wired — the live bake-off never ran; GPT-6 Luna; denoise; Mistral-direct). |
| `reports/INTEGRATED_GATE_5EA71BF.md`, `reports/RENDERED_PROOFS_BATCH_NOV14.md`, `reports/N4_N6_RENDERED_PROOF.md` | Carried from the v7 package: the full gate report on the shipped tip (GREEN, zero new failures), the 5/5 rendered-proofs batch that closes the v6 proof gap, and the words-per-line / Montserrat rendered proof. |
| `reports/V6_FIXES.md` and the rest of the v6 report set | The v6 change record and all its lane/gate records — everything in this build unchanged. |

**Deliberately NOT in the ZIP:** any `.env` or secrets (your `.env` is
preserved from your own install by the installer), `.git`,
`node_modules`, `__pycache__`/`*.pyc`, build output, downloaded model
caches (the Demucs separation weights and the SigLIP veto weights are
never bundled — they download once, on your machine, on first enabled
use). `.env.example` files are included, as the repo ships them. The two evidence documents the v7 ZIP lacked — `SETTINGS_INTEGRITY_AUDIT.md` and `AI_MODEL_BAKEOFF.md` — **are** in this ZIP, at the top level beside this file. The
ZIP's contents were machine-checked before delivery
(`zip_assertions.py --mode final`, exit 0 — the full output is in the
delivery report).

## Why there is no `.git` folder in the tree

Same reason as before: dropping this tree's `.git` over your project
folder would clobber **your** repository. The change travels as the
tree itself plus `reports/`.

## verify.ps1 expected count — the arithmetic (recomputed for this build)

Counted from the finished script's own check list, on the all-pass
path (the two early-exit guards — project folder missing, docker
missing — print their FAIL and stop, exactly as in the v6 build's
script). The migration list was also machine-checked against the
tree: `$allMigrations` (55 entries) and the tree's 55 `.sql` files
are set-equal, and all 88 feature-file paths exist in the tree.

| Check group | Count |
| --- | --- |
| Project folder exists | 1 |
| Migration files present in tree (`$allMigrations` loop) | 55 |
| Feature files present (`$featureFiles` loop) | 88 |
| `.env` in place | 1 |
| `compose.override.yml` in place | 1 |
| Containers running (postgres, redis, backend, worker, ai-worker, live-worker, frontend, litellm) | 8 |
| GPU visible to the AI worker | 1 |
| NVENC test encode in the worker | 1 |
| Long-form pacing passthrough (`ANALYSIS_LONGFORM_TPM`) | 1 |
| Voiceover toggle state | 1 |
| Migrations applied in the database (`$allMigrations` loop) | 55 |
| LiteLLM gateway model list (from inside the backend) | 1 |
| Backend health + backend DB health | 2 |
| Frontend page | 1 |
| Ollama reachable on the host | 1 |
| **Total** | **218** |

For comparison, the v12 build's 199 was: 1 + 52 + 76 + 1 + 1
+ 7 + 1 + 1 + 1 + 1 + 52 + 1 + 2 + 1 + 1. This build's delta:
**+3** migrations (counted twice — file present + applied:
+6), **+12** feature-file checks (the live VOD engine, the
live API, the live repository, the live worker, profanity
masking, the plain-path audio finish, the asset library +
licence manifest, the transition library, the font-pack
manifest, the Live page, the Layout picker) and **+1**
container (live-worker).
199 + 6 + 12 + 1 = **218**.

## Package differences vs the v12 package (complete list)

- **App tree: the combined-series tree.** The
  `JarvisClipStudio/` tree is `next-milestone` @ `3900c3da`
  — the v12 tree plus 50 commits: series A (18) + series B
  (13), Sanji's gate fixes (LiteLLM timeout, caption
  retargets, the cold-open floor fix, live-SQL coverage,
  the font eval re-base), the UI overhaul round 2 merge and
  its test retargets. **Three new migrations** (55 total).
  Gate evidence + the smoke record in `reports/` as
  itemised above.
- **Wrapper additions:** `reports/` gains
  `V121_PACKAGE_RECORD.md`, `V121_INTEGRATION_REPORT.md`,
  `REAL_EXPORT_ACCEPTANCE_V121_SMOKE.md` and
  `exports_v121_smoke_manifest.csv`. The two evidence
  documents at the top level are unchanged (byte-identical
  to the audited originals).
- **install.ps1** — naming/header text **plus three
  deliberate logic changes** vs the v12 package's script:
  (1) step 6 wires the **live-worker** block in
  `compose.override.yml` (created with the GPU reservation
  if absent; completed in place if present — Groq +
  `OPENAI_API_KEY` / `OPENAI_BASE_URL` gateway keys);
  (2) step 6 sets **`LIVE_CLIPPING=on`** in `.env` for this
  install (the shipped default stays off); (3) step 8's
  **LiteLLM smoke check retries for ~60 s** before it can
  fail (the v12 install exit-1 false alarm). The step-6
  asset copy-back is unchanged — your `assets\music` and
  `assets\sfx` contents (including the extended library)
  are preserved.
- **verify.ps1** — naming/notes plus REAL check-list
  growth: `$allMigrations` 52 → 55, `$featureFiles` 76 →
  88, containers 7 → 8 (live-worker) — the expectation
  rises **199 → 218**. No check was removed or weakened.
  (One stale banner string dating from v5 was corrected to
  name this release; text only.)
- **rollback.ps1** — naming and header text only vs the
  v12 package's script. **No logic change.**
- **KNOWN_ISSUES.md / VALIDATION_CHECKLIST.md /
  DELIVERY_CONTENTS.md / INSTALLATION_GUIDE.md** — v12.1
  addenda and refreshes as itemised in
  `reports/V121_PACKAGE_RECORD.md`; all earlier text
  carried forward unchanged, except the guide's verify
  paragraph, which had been stale since v5 (48 migrations
  / 174) and is recounted for this build (55 / 218), and
  its "installs over" line, which now names the v12 build
  and the installed-app live changes this package carries.

## Script differences vs the SnipNova Consolidated v6 package scripts (complete list)

- **App tree: none.** The `JarvisClipStudio/` tree is byte-identical
  to the v7 package's (same tip `5ea71bf`, same archive).
- **Wrapper additions:** `SETTINGS_INTEGRITY_AUDIT.md` and
  `AI_MODEL_BAKEOFF.md` at the top level (byte-identical to the
  audited originals); `reports/` gains `V8_PACKAGE_RECORD.md`,
  `RELEASE_GATE_NOV15.md`, `REAL_EXPORT_ACCEPTANCE_ON_TIP.md` and
  `exports_on_tip_manifest.csv` (51 reports, up from 47).
- **install.ps1 / verify.ps1 / rollback.ps1** — naming and header
  text only vs the v7 package's scripts (release name, ZIP
  auto-find pattern, backup-manifest line, step-6b comment, header
  notes). **No logic change** — the executable logic is identical
  to the v7 package's scripts (machine-diffed for this build; see
  the honest note below). The verify expectation is unchanged at
  **167** — same tree, same check list, re-derived for this build.
- **KNOWN_ISSUES.md / VALIDATION_CHECKLIST.md /
  DELIVERY_CONTENTS.md / INSTALLATION_GUIDE.md** — v8 addenda and
  refreshes as itemised in `reports/V8_PACKAGE_RECORD.md`; all
  earlier text carried forward unchanged.

## Script differences vs the SnipNova Consolidated v6 package scripts (complete list)

- **install.ps1** — header opening rewritten for the Consolidated
  v7 release (describes the delta over v6 and states this build
  adds NO new migration — the schema stays at 48); the
  installs-over line names the v4 release (your actual installed
  build) with v5/v6 as alternatives; ZIP auto-find pattern updated
  to `JarvisClipStudio-SnipNova-Consolidated-V7-*.zip` (2 places);
  backup manifest line renamed to "SnipNova Consolidated v7
  release install, 2026-11-15"; title line renamed; the step-6b
  `.env` comment renamed; two expectation lines reworded (full
  build, comparable to the v6 install). **No logic change** —
  every executable line is identical to the v6 package's script,
  which carries the step-5 restart-path fix (machine-diffed for
  this build; see the honest note below).
- **verify.ps1** — header/title updated; the notes block rewritten
  for the v7 delta; **1 feature-file check added** (the task-page
  degradation-notice component; `$featureFiles` is now 52, verified
  entry-for-entry against the tree). `$allMigrations` unchanged
  (48). Expected count recomputed: **167**.
- **rollback.ps1** — naming only (header + title line); the restore
  logic is byte-identical to the v6 package's script.
- **INSTALLATION_GUIDE.md** — title, ZIP/folder names, the v7-adds
  block, the verify section rewritten for 48 migrations / 52
  feature files / 167, the tracker re-score date corrected to
  2026-11-14, the reports pointer updated to `reports/V7_FIXES.md`.
- **KNOWN_ISSUES.md / VALIDATION_CHECKLIST.md** — v7 addenda at the
  top of each (FIX-51…FIX-56 + gaps G-V7-1…G-V7-8 + closures of
  G-V6-3/G-V6-4/G-V6-6; checklist items 40–44). All earlier text
  carried forward unchanged.
- **reports/** — the v6 report set carried forward unchanged, plus
  `reports/V7_FIXES.md`, `reports/INTEGRATED_GATE_5EA71BF.md`,
  `reports/RENDERED_PROOFS_BATCH_NOV14.md` and
  `reports/N4_N6_RENDERED_PROOF.md`.

**Honest note on script checking:** the three scripts were
parse-checked with real PowerShell 7.4.6 in Sanji's sandbox
(PARSE OK on all three). Their install/rollback logic is
**unchanged from the v6 package's scripts**, whose logic was
rehearsed end-to-end (backup → validation → tree swap →
verify-equivalent 102/102 → rollback byte-for-byte, plus the
wrong-SHA, truncated-ZIP and backup-validation failure modes);
for this build the step-5 backup-validation branch was re-checked
against the shipped script (the restart goes to your project
folder — grep-verified, 0 occurrences of the old backup-copy
form). They were **not** executed on Windows — PowerShell
execution, robocopy mechanics, `docker compose build/up`, the DB
dump inside the installer and the HTTP smoke checks are first-run
on your machine, exactly as in every prior release. The guide
carries a 30-second pre-flight parse check you run before
`install.ps1`, and the backup-first / validate-before-replace
design protects the first real execution.

## What this tree is relative to your installed build

Your installed build at install time is the **SnipNova Consolidated
v4 release**. This tree is that build plus the v5 delta (gateway
ladder, brief-from-screenshot) plus the v6 delta (itemised in
`reports/V6_FIXES.md`) plus the v7 delta (itemised in
`reports/V7_FIXES.md`) — nothing is removed, and there is **no new
database migration** vs v6 (the schema stays at 48 migrations;
`verify.ps1` checks all 48 are applied). Your data, clips,
settings, `.env`, `compose.override.yml`, music and SFX are
untouched (the installer backs everything up first and copies your
files back unchanged; the only optional change is the step-6b
mount, which is yours to accept or decline — you already accepted
it at the v3 install, so the step simply reports it is already
there).
