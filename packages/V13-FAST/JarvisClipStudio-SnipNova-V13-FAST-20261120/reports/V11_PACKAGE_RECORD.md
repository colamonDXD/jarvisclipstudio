# V11 Package Record — what this package is and what changed vs the v10 package

**Package:** `JarvisClipStudio-SnipNova-Consolidated-V11-20261117.zip`
**App tree:** branch `next-milestone` @ **`5480a9da`**
(`5480a9da898b687dc6086e74844f91513c111063`) — git-archived from
that commit; the build asserts the full hash. The tree is the
v10 tree (`901ae252`) plus the post-v10 frontend merges (§1).
**Installs over:** the Consolidated **v10** release (if its
install completed) or v8/v9 otherwise — the installer backs up
first; data, clips, settings, `.env`, `compose.override.yml`,
music and SFX are preserved.
**Schema:** 51 migrations (unchanged from v10). **verify.ps1
expectation: 176 passed, 0 failed** (§3 — unchanged from v10).

The package's own SHA-256, byte size and file count are
recorded in the delivery report (delivered with the package
for approval; a file inside the ZIP cannot hash the ZIP).

## 1. The post-v10 merges (tree delta vs v10)

The name-only diff `901ae252..5480a9da` is **20 files**:
`CHANGELOG.md` + 19 frontend files. **Zero backend files, zero
migrations, zero compose changes** — proven by the filtered
name-only diff (empty) in `reports/FRONTEND_GATE_5480A9DA.md`.

1. **Frontend completion** (merged @ `15c7720a`): the legacy
   `include_broll` chip retired from the create page (the
   authoritative `broll_enabled` path untouched; a test proves
   the payload carries no `include_broll`); the **Recipes
   picker** on the create page (sends `recipe_id` only when
   picked); **F59 pre-processing summary** ("Before you
   generate:" strip from existing state only); **F61 run
   summary** (task-page strip: clips · total length ·
   template · mode — only fields the API returns).
2. **React Bits UI polish** (merged @ `e015b809`): vendored
   CSS adaptations of React Bits primitives (MIT + Commons
   Clause, with attribution) — aurora background, gradient /
   shiny text, spotlight card, button sheen. Zero new
   dependencies. The darker-theme direction (no bright white)
   was applied per the user's order.
3. **Aurora Flow shell** (merged @ `ee3a9882`): top-header
   shell, glass panels, create page reorganised as a flow.
   Superseded as the end-state look by item 4 the same day.
4. **Studio Console v2** (merged @ `5480a9da`): the full
   frontend rebuild the user approved after rejecting Aurora
   Flow v1 as still recognisably SupoClip — **Sora** becomes
   the app typeface, JetBrains Mono the label voice; the
   **greeting hero is removed**; a slim **icon rail + command
   bar** replace the top pills; the create page is a
   **two-zone studio workspace** (flat console sections left,
   sticky glass **Run Spec** panel right with the F59 summary
   as spec rows and Generate pinned to it). Full create-page
   control parity with v10 — identical behaviour and
   payloads; one test file updated for the spec-label
   renames only (Style→Niche, Format→Output; values
   unchanged).

## 2. Gates and acceptance on this tree

- **Full frontend gate on the exact tip `5480a9da`**
  (`reports/FRONTEND_GATE_5480A9DA.md`): vitest **939 passed /
  1 failed / 14 skipped** — the single failed test is the
  known PGlite `social-publishing` 30s timeout; all 5 failed
  files are the PGlite `src/server/*.integration.test.ts`
  family and every one passes solo (18/18). tsc **0 errors**;
  `next build` **exit 0, 75/75 pages**; changelog vitest
  **4/4**; smoke census **168 routes / 139 paths**, identical
  to baseline. Backend litellm + compose config suites
  **29 passed / 0 failed** (sanity check, run with
  `frontend/src/generated` set aside per the certified
  condition).
- **Delta gate on the intermediate combination `e015b809`**
  (`reports/DELTA_GATE_E015B809.md`): vitest 940/0/14, tsc 0,
  build exit 0, smoke identical.
- **Smoke acceptance on the exact tip**
  (`reports/REAL_EXPORT_ACCEPTANCE_V11_SMOKE.md`): one
  Full AutoClip + Plain render end-to-end through the real
  service path on a fresh scratch PostgreSQL (51 migrations),
  keyless degraded path by design — create → process →
  export at 1080×1920, frames judged.
- **Backend full suite: NOT RUN on this tip — labelled
  STALE.** The backend is byte-identical to the v10 tree;
  the v10 verdicts (full re-gate 5,070 passed / 3 attributed
  on `327886b3`; 8/8 four-mode acceptance + streamer journey
  on `901ae252`) are the record for this backend and are
  **not** re-claimed as fresh passes for `5480a9da`.
- **Full 8-combo acceptance: NOT re-run for v11** — the
  reasoning (backend identical to the tree that passed 8/8)
  is stated in the smoke report itself.

## 3. verify.ps1 — the check list is UNCHANGED (a packaging decision)

`$allMigrations` is **unchanged** (51 — this tree adds no
migration). `$featureFiles` is **unchanged** (55 — this
package adds no entries: the new frontend modules —
`create-summary.ts`, `run-summary.ts`, the React Bits
components, the Studio Console shell — are covered by the
frontend gate's test suite, and adding install-time
file-presence checks for them would inflate the count
without adding install safety; the v10 list already covers
every user-visible capability area). Expected total:
19 + 51 + 51 + 55 = **176** (v10: 176). The build's recompute
asserts the migration list is set-equal to the tree's `.sql`
files and every feature path exists in the tree. No check
was removed or weakened.

## 4. Wrapper changes vs the v10 package

- **install.ps1 / rollback.ps1** — naming and header text
  only (release name, ZIP auto-find pattern, backup-manifest
  line, step-6b comment, header notes). **No logic change** —
  the same diff class as v9→v10, grep-verifiable.
- **verify.ps1** — naming/notes only; the check list is
  byte-identical to v10's (§3).
- **KNOWN_ISSUES.md** — v11 addendum: what v11 adds
  (frontend only), closures (none of the G-V10 gaps — the
  delta does not touch their areas), gaps renumbered
  **G-V11-1…G-V11-11** mapped 1:1 to G-V10-1…11.
- **VALIDATION_CHECKLIST.md** — items **53–55** (v11 reports
  present; v10 superseded; the re-runs that judge v11 — the
  new interface itself, one real end-to-end run, the
  still-owed v10 re-runs).
- **DELIVERY_CONTENTS.md / INSTALLATION_GUIDE.md** —
  refreshed for this build; the verify arithmetic updated
  (delta +0/+0 → 176).
- **reports/** — 77 files (v10: 72): the 5 additions are
  this record, the frontend gate on the exact tip, the
  `e015b809` delta gate, and the smoke acceptance report +
  its exports manifest.
- The two evidence documents at the top level
  (`SETTINGS_INTEGRITY_AUDIT.md`, `AI_MODEL_BAKEOFF.md`) are
  **byte-identical** to the v10 package's (SHA-256 `298fed5a…` /
  `d5acafa8…`); `JarvisClipStudio/TRACKER.md` likewise
  (`e4ef37e8…`).

## 5. Build discipline

Deterministic build: the ZIP is built twice and the SHA-256
must be identical; `zip_assertions.py --mode final` must pass;
the manifest verification runs against the **built** ZIP (the
V7 lesson — two missing documents — is checked by name,
including `SETTINGS_INTEGRITY_AUDIT.md`, `AI_MODEL_BAKEOFF.md`,
`REACTION_API_CONTRACT.md`, the gate reports and this record;
zero `.drawtext-*` residue entries; exactly 51 tree
migrations). The shipped scripts are parse-checked under
PowerShell 7.4.6 where available and the install/verify/rollback
rehearsal (incl. fail-SHA, truncated ZIP, backup-failure) is
run against them; verdicts are in the delivery report. No
`.git`, no `node_modules`, no `.env` (only `.env.example`), no
model weights beyond the tree's own committed YOLOX int8 file.
