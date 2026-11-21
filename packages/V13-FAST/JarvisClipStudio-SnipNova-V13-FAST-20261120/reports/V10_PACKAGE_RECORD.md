# V10 Package Record — what this package is and what changed vs the v9 package

**Package:** `JarvisClipStudio-SnipNova-Consolidated-V10-20261117.zip`
**App tree:** branch `next-milestone` @ **`901ae252`**
(`901ae2528fb50363ed2b15d3ae290d28cc852ccc`) — git-archived from
that commit; the build asserts the full hash. The tree is the
v9 tree (`20f903b`) plus the post-v9 merges (§1).
**Installs over:** the Consolidated **v8** release (or v9, if
that was installed) — the installer backs up first; data,
clips, settings, `.env`, `compose.override.yml`, music and
SFX are preserved.
**Schema:** 51 migrations (unchanged from v9). **verify.ps1
expectation: 176 passed, 0 failed** (§3).

The package's own SHA-256, byte size and file count are
recorded in the delivery report (delivered with the package
for approval; a file inside the ZIP cannot hash the ZIP).

## 1. The post-v9 merges (tree delta vs v9)

1. **Face-evidence stack** (six branches, 22 commits, merged
   @ `327886b3` via `04975362`): bright-extent subject
   evidence in `ContextAnalyzer.measure` (`1e061c4`); a gated
   YOLOX person pass (`2c76085` — `backend/src/media/person_detection.py`
   + the OpenCV Zoo YOLOX int8 model, Apache-2.0); a Haar
   depiction gate (`48c6341`); a split min-area floor
   (`ca1afee`); a depicted-grid screen (`74bb15fa`); the
   legacy-chain Haar gate (`04975362`). Wide, group and
   action shots now keep people in frame instead of
   protecting faces only. Gaps G-V9-9 partially closed.
2. **Frontend API proxies** (`feat/frontend-api-proxies`,
   merged @ `a839ce8b`): `/api/learning/[...path]` and
   `/api/recipes` + `/api/recipes/[...path]` route handlers —
   the Learning Engine and Recipes surfaces now reach the
   backend (contract-pack gaps D1/D2).
3. **Framing-mode picker** (`feat/frontend-framing-picker`,
   merged @ `327886b3`): the create-page framing control
   (Auto / Fit (whole picture) / Crop (full screen)) per
   `FRAMING_MODE_API.md` — default Auto, the value always
   sent including explicit `"auto"`, stays enabled in Plain;
   locale strings in all 6 locales. **This closes G-V9-10**
   (framing mode was backend+API only in v9).
4. **Streamer Reaction journey, milestone 1**
   (`feat/frontend-streamer-journey`, merged @ `4a6b54ce`):
   the create-page **Streamer Reaction card** (Find Moments
   + AI, Auto template, framing auto, gaming niche, vertical
   — existing payload fields only, with the plain-language
   parking warning); failed task screens show the backend's
   **real failure reason** (`frontend/src/lib/task-failure.ts`);
   the moment picker shows each candidate's **hook title**
   where the API returns one (nothing invented when it does
   not). Plus `reports/REACTION_API_CONTRACT.md` — the
   additive, optional-fields contract for the reaction
   signals (PROPOSAL; no backend lane implements it yet).
5. **Test-only re-gate fixes** (`fix/regate-test-staleness`,
   merged @ `dad9e31b`): the two stale framing-era tests
   (billing fixture DDL gains the `framing_mode` column; the
   recipe byte-identical pin gains `framing_mode: None`).
   Test files only — zero product code. These close the
   full re-gate's F1/F2 findings; F3 was a gate-harness slip,
   proven environmental.
6. **Reaction-layout gate fix** (`fix/reaction-gate-gameplay`,
   merged @ `c7c5b3ca`): `find_reactor_face` no longer demands
   faces inside the gameplay area — a changing faceless
   source (gameplay motion, measured outside the inset) now
   proves the reaction inset, so the **stacked
   facecam-over-gameplay layout engages on gameplay
   streams**; a static faceless source still returns None.
   The corner-face stability bar was calibrated 1.5% → 2.0%
   of width (genuine face wander inside a fixed inset
   measured 1.65% at pipeline scale; the drifting-face
   control still rejects at ~4%+). Record:
   `reports/REACTION_GATE_FIX_REPORT.md`.
7. **Model-config corrections** (`fix/model-config-corrections`,
   merged @ `901ae252`): the DeepSeek gateway route's slug
   `openai/deepseek-chat` → `openai/deepseek-v4-pro`
   (DeepSeek retired `deepseek-chat` on 2026/07/24; the
   account also has zero credit, so the route is
   correct-but-dormant and Groq gpt-oss-120b remains
   effectively primary); `HF_TOKEN` now passes through both
   compose files to backend, worker and ai-worker;
   `.env.example` gains the `HF_TOKEN` block and the
   previously missing `NVIDIA_API_KEY` entry. Audit:
   `reports/MODEL_CONFIG_AUDIT.md` (the NVIDIA ladder names
   needed no correction — the armed IDs are exact current
   public catalog IDs).

## 2. Gates and acceptance on this tree

- Full re-gate on the merged tip `327886b3`:
  **backend 5,070 passed / 3 failed / 16 skipped / 7 xfailed**
  (5,101 passed including Q5/Q6) — all 3 failures attributed
  (F1/F2 stale framing-era tests, F3 gate-harness slip) and
  closed by item 5 above — `reports/FULL_REGATE_327886B3.md`.
- Merge + delta gates: the stack/frontend merges with their
  targeted re-gate (stack tests 71/0, framing families
  412/1sk/0) — `reports/MERGE_STACK_AND_FRONTEND_REPORT.md`;
  the delta gate on the final combination `c7c5b3ca`
  (frontend vitest 921 passed with only the known PGlite
  environmental file family failing; tsc 0; `next build`
  exit 0; backend combo 87/0; API census 168 routes / 139
  paths, exact match with the re-gate).
- Campaign-brief render proof (R-B) re-run on `c7c5b3ca`:
  PASS, zero regression — third identical reproduction
  (gate selected exactly the compliant candidate; render
  sidecar [1695.06, 1715.0], 100% in-window; no-match leg
  honestly returned `no_compliant_moments`).
- Four-mode real-export acceptance **plus the streamer
  journey path** re-run on the packaged tip (`901ae252`):
  `reports/REAL_EXPORT_ACCEPTANCE_V10.md` +
  `reports/exports_v10_manifest.csv`.

## 3. verify.ps1 — the check-list delta (a packaging decision)

`$allMigrations` is **unchanged** (51 — this tree adds no
migration). `$featureFiles` gains **two** entries — the
YOLOX person-detection module
(`backend/src/media/person_detection.py`), the face-evidence
stack's user-visible capability, and the streamer-journey
preset module (`frontend/src/lib/streamer-journey.ts`), this
package's headline feature — taking the curated list to
**55**. Expected total: 19 + 51 + 51 + 55 = **176** (v9: 174).
Leaving either unlisted would repeat the v6→v7
"gate-verified but unlisted" gap. No check was removed or
weakened. The build's recompute asserts the migration list
is set-equal to the tree's `.sql` files and every feature
path exists in the tree.

## 4. Wrapper changes vs the v9 package

- **install.ps1 / rollback.ps1** — naming and header text
  only (release name, ZIP auto-find pattern, backup-manifest
  line, step-6b comment, header notes). **No logic change** —
  the same diff class as v8→v9, grep-verifiable.
- **verify.ps1** — naming/notes plus the §3 check-list delta.
- **KNOWN_ISSUES.md** — v10 addendum: closures (G-V9-10
  framing dropdown shipped; G-V9-9 partially closed by the
  face-evidence stack; Recipes/Learning proxies live) and
  gaps renumbered **G-V10-1…G-V10-11** (three new: the
  dormant DeepSeek route, streamer real-VOD validation +
  source-pane content-mode margins, HF_TOKEN pass-through
  with pyannote still unwired).
- **VALIDATION_CHECKLIST.md** — items **50–52** (v10 reports
  present; v9 superseded; the re-runs that judge v10).
- **DELIVERY_CONTENTS.md / INSTALLATION_GUIDE.md** —
  refreshed for this build; the verify arithmetic updated
  to 176.
- **reports/** — 72 files (v9: 63): the 9 additions are the
  V10 acceptance report + manifest, the full re-gate, the
  merge-stack/frontend report, the reaction API contract,
  the reaction-gate fix report, the model-config audit, the
  Nov 17 integration record, and this record.
- The two evidence documents at the top level
  (`SETTINGS_INTEGRITY_AUDIT.md`, `AI_MODEL_BAKEOFF.md`) are
  **byte-identical** to the v9 package's (SHA-256 `298fed5a…` /
  `d5acafa8…`); `JarvisClipStudio/TRACKER.md` likewise
  (`e4ef37e8…`).

## 5. Build discipline

Deterministic build: the ZIP is built twice and the SHA-256
must be identical; `zip_assertions.py --mode final` must pass;
the manifest verification runs against the **built** ZIP (the
V7 lesson — two missing documents — is checked by name,
including `SETTINGS_INTEGRITY_AUDIT.md`, `AI_MODEL_BAKEOFF.md`,
`REACTION_API_CONTRACT.md` and the gate reports; zero
`.drawtext-*` residue entries; exactly 51 tree migrations).
The shipped scripts are parse-checked under PowerShell 7.4.6
and the install/verify/rollback rehearsal (incl. fail-SHA,
truncated ZIP, backup-failure) is run against them; verdicts
are in the delivery report. No `.git`, no `node_modules`, no
`.env` (only `.env.example`), no model weights beyond the
tree's own committed YOLOX int8 file (part of the product
tree since the face-evidence merge).
