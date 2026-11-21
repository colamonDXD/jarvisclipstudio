# RELEASE GATE — Nov 15, 2026 (Day 3) — VERDICT: DO NOT SHIP

**Gate run:** 2026-11-15 ~10:00 IST, scheduled job `snipnova-early-delivery-nov15`.
**Package under gate:** V7 `JarvisClipStudio-SnipNova-Consolidated-V7-20261115.zip`
— local `~/workspace/packaging-v7/JarvisClipStudio-SnipNova-Consolidated-V7-20261115.zip`,
35,072,430 bytes, SHA-256 `54502a167a88383a2c599a87ac20f555a37a66290b556825ac0b8446a7c36262`
(recomputed by this gate — matches the packaging lane's claim).
**Source tree:** `next-milestone` @ `5ea71bfb92201ad09fdd6bd408a87ad659f070d4`
(verified by this gate: `~/workspace/jcs-next` HEAD is exactly `5ea71bf`, clean).
**Verdict: DO NOT SHIP.** Two of the five ship conditions do not hold on the
packaged tree. No FINAL_DELIVERY.md is written. Nothing touches Luffy's
installed app. **The Nov 24 10:00 IST job (`snipnova-final-delivery-nov25`)
proceeds unchanged as the hard fallback.**

Note on the upload: the packaging lane already uploaded this ZIP this morning
(URL `https://muse.ai/files/1454858041040983/1175763005404722/8rw52et08eo0x2cjaa0ll6eq/JarvisClipStudio-SnipNova-Consolidated-V7-20261115.zip`,
expiry 2026-11-17T04:02:54Z ≈ 09:32 IST Nov 17, per
`packaging-v7/logs/upload-v7-final.log`). An upload is not a delivery and not
an approval: no delivery message was sent, and **this gate does NOT approve
installing this ZIP**. Treat the link as unapproved; do not install from it.

## Per-condition verdicts

### (1) Day-1 outputs final — HOLD ✓
- `SETTINGS_INTEGRITY_AUDIT.md` exists (25,447 B, 295 lines, Nov 12).
  Independent Step-0 verdict HOLDS: pre-fix tree `bc2f230` repro 5 failed /
  1 passed / 1 skipped (stored `'plain'` reads back `None`; round-tripped
  Plain task → full Auto EDL); fix re-proven on tip; live-PostgreSQL 16.15
  round-trip passed; combination matrix proven on the real path; 26 settings
  traced × 9 stages — 22 proven end-to-end, 3 absent-by-design, 1 pair
  proven-with-caveat; zero remaining dropped-field defects with a consumer.
- F-2/F-4 fix commit `6793620` is an ancestor of the ship tip `5ea71bf`
  (verified by this gate via `git merge-base --is-ancestor`).
- Independent read-back verification is recorded in `SPRINT_LOG.md`
  (entries 2026-11-12 ~21:59, ~22:31, 2026-11-13 01:24).

### (2) Day-2 bake-off output — HOLD (with the honest caveat) ✓
- `AI_MODEL_BAKEOFF.md` exists (50,076 B, 809 lines, Nov 14). Offline phase
  proven (harness self-check + 25/25 tests); live phase PENDING — no live
  call made from the sandbox, no winner named anywhere in the document, no
  rung changed, no unverified model upgrade in the tree; the shipped ladder
  is the proven one. The pending live phase is named as gap G-V7-4 in the
  package KNOWN_ISSUES. The condition's intent (no unverified model upgrade
  wired) holds; the live measurement itself remains owed on Luffy's machine.

### (3) Real-export acceptance on the combined tree — DOES NOT HOLD ✗
This is the blocking failure.
- The full four-mode acceptance (same Japan fixture, all four creation
  modes × Plain/Auto, 32 exports, inspected frames/metadata) ran on tip
  **`9ba6602`**, independently verified 32/32 (`EXPORTS_VERIFICATION.md` +
  manifest). It did **not** run on the ship tip `5ea71bf`.
- Between `9ba6602` and `5ea71bf`, **92 files changed (+6,724/−350)**,
  including production render-path code the acceptance would exercise:
  `backend/src/services/task_service.py`, `clip_plan.py`,
  `auto_director/creation_modes.py`, `reference_style.py`, `ai.py`,
  `clip_service.py`, `video_service.py`, `editor_service.py`,
  `frontend/src/lib/editor/export-draft.ts`, and others. The 32-export
  evidence therefore does not transfer to the shipped tree.
- On-tip rendered coverage on `5ea71bf` is real but narrow:
  `RENDERED_PROOFS_BATCH_NOV14.md` 5/5 PASS (F-R1 Plain vs Auto genuinely
  different pipelines on the same Japan fixture/windows; F-B, F-A, F-1,
  F-R2) and `N4_N6_RENDERED_PROOF.md` PASS (words-per-line, Montserrat
  fonts on real renders). That is targeted fix-proof, not the full
  8-combo matrix, and it does not include a settings-survives-restart
  check on this tip.
- The gap is honestly named as **G-V7-3** in the package KNOWN_ISSUES
  ("a fresh four-mode acceptance re-run on `5ea71bf` itself has not been
  run; it is owed to the Nov 17 integration pass and to your machine") —
  it was not waived silently. But the body's rule for unrun work is
  exclusion + naming, and the four creation modes are the product core:
  they cannot be excluded from the ZIP. Naming the gap does not make the
  condition ("acceptance has run on the combined tree") true. No
  known-broken feature was found — this is unproven-on-this-tree
  acceptance, not a known defect — but the ship standard is "ALL of these
  hold", and this one does not.

### (4) Full gate, zero new failures vs baselines — HOLD ✓
- `INTEGRATED_GATE_5EA71BF.md`: backend **4,656 passed / 0 failed**
  (Q1 1,208 + Q2 1,162 + Q3 1,049 + Q4 1,237 — each total spot-checked by
  this gate against the raw chunk logs in `gate-logs-5ea71bf/`), frontend
  vitest **899 passed / 0 failed / 14 skipped** (raw log confirms; the
  4 failed files are the baseline-known env-bound PGlite set with zero
  failed tests inside), tsc exactly the **11-error baseline set** (raw
  count 11; the one NEW error from the 9b24c7b gate is gone, nothing
  added), eslint **exit 0 / exit 0** (both legs confirmed from raw logs),
  production `next build` **exit 0** (raw log `NEXT_BUILD_EXIT=0`).
  Zero new failures vs the 9b24c7b baseline; no test weakened.
- Baseline-file note: `~/workspace/final-failed-list.txt` does not exist
  as a file — it was retired 2026-11-10
  (`final-failed-list.RETIRED-2026-11.txt`) because the suite reached zero
  failures, with an explicit "do not use as a gate baseline". The gate's
  use of the immediately preceding full gate (9b24c7b) as the baseline is
  the correct successor and is documented in the gate report. Not a
  blocker; recorded here so the body's reference is reconciled.

### (5) zip_assertions --mode final — HOLD ✓ (independently re-verified)
- This gate re-ran `research_notes/release-machinery/zip_assertions.py
  --mode final` on the built ZIP itself: **38 passed / 0 failed /
  0 warnings, exit 0**. SHA-256 and byte size recomputed by this gate
  match the packaging lane exactly.

## Second, independent failure: package contents

The body requires the ZIP to contain `SETTINGS_INTEGRITY_AUDIT.md` and
`AI_MODEL_BAKEOFF.md`. **Neither file is in the ZIP** (verified by this
gate by full name listing of all 1,579 entries; `DELIVERY_CONTENTS.md`
does not mention them either). The docs exist in the workspace
(`research_notes/sprint-7day/`) and are cited by the reports, but the
delivered package does not contain its own Day-1/Day-2 evidence.
Everything else in the required contents list is present: complete tree
(no `.env`, no `.git`, no `node_modules` — verified, zero forbidden
entries; `.env.example` present), `CHANGELOG.md`, re-scored `TRACKER.md`
(fourth re-score 2026-11-14), `KNOWN_ISSUES.md`, `INSTALLATION_GUIDE.md`,
benchmark pack (`tools/benchmark-pack/`), validation checklist, 47 reports.

## What remains (in order)

1. **Fresh four-mode real-export acceptance on the packaged tip** — same
   Japan fixture, all four creation modes × Plain/Auto, exports inspected
   (frames/metadata), settings surviving an app restart. Owner: the Nov 17
   integration pass (as G-V7-3 already states); result must land before
   any ship decision on that tree.
2. **Package contents fix** — include `SETTINGS_INTEGRITY_AUDIT.md` and
   `AI_MODEL_BAKEOFF.md` in the ZIP (rebuilt package, fresh
   `zip_assertions --mode final` run, new SHA-256, re-upload).
3. Live-provider validation stays owed on Luffy's machine (G-V7-4):
   live bake-off, Cloudflare hop check, Eco vs normal benchmark on his
   RTX 4050, real Demucs quality.
4. **Nov 24 10:00 IST job (`snipnova-final-delivery-nov25`) proceeds
   unchanged** as the hard fallback. No install from the V7 link.

## Evidence this gate relied on

- `~/workspace/research_notes/sprint-7day/SETTINGS_INTEGRITY_AUDIT.md`
- `~/workspace/research_notes/sprint-7day/AI_MODEL_BAKEOFF.md`
- `~/workspace/research_notes/sprint-7day/INTEGRATED_GATE_5EA71BF.md` +
  `gate-logs-5ea71bf/` (raw chunk, vitest, tsc, eslint, next-build logs)
- `~/workspace/research_notes/sprint-7day/REAL_EXPORT_ACCEPTANCE_RERUN.md`
  (tip 9ba6602) + `EXPORTS_VERIFICATION.md` (32/32) +
  `exports_verification_manifest.csv`
- `~/workspace/research_notes/sprint-7day/RENDERED_PROOFS_BATCH_NOV14.md`
  (5/5 on 5ea71bf) + `N4_N6_RENDERED_PROOF.md` (on 5ea71bf)
- `~/workspace/research_notes/sprint-7day/V7_PACKAGE_REPORT.md`
- `~/workspace/packaging-v7/JarvisClipStudio-SnipNova-Consolidated-V7-20261115.zip`
  + `packaging-v7/logs/` (assertions, sha256, upload logs)
- `~/workspace/research_notes/sprint-7day/SPRINT_LOG.md` (read-back
  verification entries)
- This gate's own re-runs: `zip_assertions.py --mode final` exit 0
  (38/0/0); SHA-256 recomputation; `git merge-base` ancestry checks;
  full ZIP name listing; `git diff --stat 9ba6602..5ea71bf`.

## Short factual message (for the main agent to deliver)

Nov 15 release gate: DO NOT SHIP. The V7 package (tip 5ea71bf) passes the
full gate green (backend 4,656/0, frontend 899/0, eslint 0, build 0,
assertions 38/0/0) and the settings audit + bake-off docs are final — but
the full four-mode export acceptance was never re-run on this tip (the
32-export set is from 9ba6602; render code changed since), and the ZIP is
missing the settings-audit and bake-off docs it must contain. The uploaded
V7 link is NOT approved for install. Nov 24 delivery proceeds as the
fallback; the Nov 17 pass owns the fresh acceptance re-run.
