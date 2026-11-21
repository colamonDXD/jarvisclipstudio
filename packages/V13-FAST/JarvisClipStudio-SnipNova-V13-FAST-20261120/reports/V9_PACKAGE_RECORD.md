# V9 Package Record — what this package is and what changed vs the v8 package

**Package:** `JarvisClipStudio-SnipNova-Consolidated-V9-20261116.zip`
**App tree:** branch `next-milestone` @ **`20f903b`**
(`20f903bd84f6836b346b5941f74f801786cb2e25`) — git-archived from
that commit; the build asserts the full hash. The tree is the
v8 tree (`5ea71bf`) plus the 15 merge-queue items (§1), the
zoom fix found during the items-14/15 merge (`f49da36`), and
one chore commit (`20f903b`: removal of a tracked test-residue
file, `backend/.drawtext-7329/card_0_….txt`, accidentally
committed during the merge batch — no product or test change).
**Installs over:** the Consolidated **v8** release (the
installer backs up first; data, clips, settings, `.env`,
`compose.override.yml`, music and SFX are preserved).
**Schema:** 51 migrations (v8: 48). **verify.ps1 expectation:
174 passed, 0 failed** (§3).

The package's own SHA-256, byte size and file count are
recorded in `V9_PACKAGE_REPORT.md` (delivered with the package
for approval; a file inside the ZIP cannot hash the ZIP).

## 1. The fifteen merged work items (tree delta vs v8)

1. **Floor-export parity** (`fix/floor-export-parity`) — the
   per-task B-roll toggle is authoritative over the legacy
   create-form chip (`resolve_broll_gate`); degraded no-AI
   exports get grounded hook titles instead of none.
2. **Learning Engine verification** (`feat/learning-engine-backend`,
   tests + contract) — the feedback loop proven wired end to
   end; contract in `reports/LEARNING_ENGINE_API_CONTRACT.md`.
3. **RTX 4050 render path** (`perf/rtx4050-render-path`) — a
   bounded ffmpeg thread budget on every render (previously
   Eco-only) and the encoder actually used recorded per task;
   the silent NVENC→CPU fallback is now visible in the task's
   stage timings.
4. **B-roll consolidation** (`refactor/broll-consolidation`) —
   every B-roll cue sources through the v2 resolver; the
   editor's search/suggest buttons, dead on a keyless-Pexels
   setup, work again.
5. **Retention editing** (`feat/retention-editing`) — the three
   silent B-roll loss stages found by forensics on real exports
   are closed: undelivered inserts are stamped `not_inserted
   {stage, reason}`; a per-task usage ledger stops sibling
   clips sharing an asset; transition discipline (no slides
   mid-speech); a universal opening push-in in Auto.
6. **Recipes foundation** (`feat/recipes-foundation`) — saved,
   reusable create-settings bundles: migrations
   `20261115_0001_recipes.sql` +
   `20261115_0002_watch_folder_recipe_id.sql`, CRUD + fragment
   routes, `recipe_id` on task create (unknown/cross-user/
   malformed → 422 before anything is created), watch-folder
   autopilot support. Contract: `API_CONTRACT_PACK.md` §6.7.
7. **LiteLLM healthcheck template** (`chore/litellm-healthcheck-template`)
   — the python3 liveliness probe (proven on the user's machine
   during the 2026-11-15 restore) adopted into the shipped
   `compose.override.yml` template.
8. **State-docs refresh** (`docs/state-refresh`) — PROJECT_STATE /
   FEATURE_PROGRESS / TEST_RESULTS / BUG_AUDIT /
   NEXT_SESSION_PROMPT.txt rewritten to the shipped reality.
9. **Multi-subject framing** (`fix/multisubject-framing`) —
   motion-centroid scene evidence (reframe cache schema v2);
   crops re-anchor or fit instead of slicing faces entering a
   held shot; what cannot be saved is stamped; one caption band
   per clip with hard title↔caption separation.
10. **Per-task text bands** (`feat/task-text-bands`) — title and
    caption bands decided once per task; the title and captions
    can no longer stack into one block (the defect reported on
    the v8 build, 2026-11-16).
11. **Campaign-brief enforcement** (`verify/campaign-brief-enforcement`)
    — regeneration now audits excluded ranges and review flags;
    two proven defects fixed (a dropped brief flag; a
    sentence-extension leak across an excluded boundary).
12. **Mistake-fix hook style** (`feat/mistake-fix-hook`) —
    finance/education, available not default; the fix must land
    in the first third (structural).
13. **List-fill hook style** (`feat/list-fill-finance`) —
    finance, available not default; the on-screen list fills as
    the speaker names each item.
14. **Trailer-breakdown style** (`feat/trailer-breakdown`) —
    gaming, available not default: freeze → punch-zoom → face-cam
    reaction → "Part 2" cliffhanger, rendered-proven on a
    stand-in fixture. Known limits in KNOWN_ISSUES G-V9-8.
15. **Framing mode** (`feat/framing-mode`) — migration
    `20261116_0001_task_framing_mode.sql`; `framing_mode`
    (`auto` / `fit` / `crop`) on every task, orthogonal to the
    template (Plain + always-fit is now possible). Contract:
    `reports/FRAMING_MODE_API.md`.

**Plus, found by the items-14/15 merge gate and fixed before
packaging (`f49da36`):** an explicit encoder `-threads` flag
silently disabled every zoompan zoom on the merged tree
(punch-ins and the opening push-in rendered flat). The
renderer no longer sets an output thread count; the
trailer-breakdown rendered proof is the standing regression
test. Record: `reports/MERGE_14_15_AND_REGATE_REPORT.md`.

## 2. Gates and acceptance on this tree

- 13-branch merge gate (`b05e6da`): backend **4,939 passed /
  0 failed**, frontend **899 / 0**, SQL-exec live-PG 138/0 —
  `reports/MERGE_BATCH_AND_GATE_REPORT.md`.
- Items-14/15 targeted re-gate (`40f5407`): PASS, incl. a fresh
  pristine-DB roundtrip proving all 51 migrations —
  `reports/MERGE_14_15_AND_REGATE_REPORT.md`.
- Four-mode real-export acceptance re-run on the packaged tip
  (`20f903b`): **32/32 exports**, Manual Cutter PASS outright,
  AI-dependent combos PASS on the degraded path, Plain honesty
  257/257 shots, persistence-across-restart PASS —
  `reports/REAL_EXPORT_ACCEPTANCE_V9.md` +
  `reports/exports_v9_manifest.csv`.

## 3. verify.ps1 — the check-list delta (a packaging decision)

`$allMigrations` gains the 3 new migration files (**51**);
`$featureFiles` gains **one** entry —
`backend/src/auto_director/framing_mode.py` (the framing-mode
setting module), taking the curated list to **53**. Expected
total: 19 + 51 + 51 + 53 = **174** (v8: 167). The framing-mode
module is the user-visible feature of item 15; leaving it
unlisted would repeat the v6→v7 "gate-verified but unlisted"
gap. No check was removed or weakened. The build's recompute
asserts the migration list is set-equal to the tree's `.sql`
files and every feature path exists in the tree.

## 4. Wrapper changes vs the v8 package

- **install.ps1 / rollback.ps1** — naming and header text only
  (release name, ZIP auto-find pattern, backup-manifest line,
  step-6b comment, header notes). **No logic change** — the
  same diff class as v7→v8, grep-verifiable.
- **verify.ps1** — naming/notes plus the §3 check-list delta.
- **KNOWN_ISSUES.md** — v9 addendum: closures (title/caption
  stacking, retention stamps, the zoom fix, G-V8-4 partially
  closed) and gaps renumbered **G-V9-1…G-V9-10** (three new:
  trailer-breakdown limits, non-face subjects, the framing-mode
  frontend dropdown).
- **VALIDATION_CHECKLIST.md** — items **47–49** (v9 reports
  present; v8 superseded; the three re-runs that judge v9).
- **DELIVERY_CONTENTS.md / INSTALLATION_GUIDE.md** — refreshed
  for this build; the verify arithmetic updated to 174.
- **reports/** — 63 files (v8: 51): the 12 additions are the
  two merge reports, the V9 acceptance report + manifest, this
  record, the framing-mode contract, and six lane records.
- The two evidence documents at the top level
  (`SETTINGS_INTEGRITY_AUDIT.md`, `AI_MODEL_BAKEOFF.md`) are
  **byte-identical** to the v8 package's (SHA-256 `298fed5a…` /
  `d5acafa8…`); `JarvisClipStudio/TRACKER.md` likewise
  (`e4ef37e8…`).

## 5. Build discipline

Deterministic build: the ZIP is built twice and the SHA-256
must be identical; `zip_assertions.py --mode final` must pass;
the manifest verification runs against the **built** ZIP (the
V7 lesson — two missing documents — is checked by name,
including `SETTINGS_INTEGRITY_AUDIT.md`, `AI_MODEL_BAKEOFF.md`,
`FRAMING_MODE_API.md` and the merge reports; zero `.drawtext-*`
residue entries; exactly 51 tree migrations). The shipped
scripts are parse-checked under PowerShell 7.4.6 and the
install/verify/rollback rehearsal (incl. fail-SHA, truncated
ZIP, backup-failure) is run against them; verdicts are in
`V9_PACKAGE_REPORT.md`. No `.git`, no `node_modules`, no `.env`
(only `.env.example`), no model weights.
