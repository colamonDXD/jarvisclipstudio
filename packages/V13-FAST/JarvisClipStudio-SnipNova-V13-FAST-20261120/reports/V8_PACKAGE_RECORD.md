# V8 Package Record — SnipNova Consolidated Release v8 (2026-11-15)

## What this package is

The Consolidated v8 package carries **exactly the same app tree**
as the v7 package: branch `next-milestone` @
`5ea71bfb92201ad09fdd6bd408a87ad659f070d4`. **Zero code changes vs
the v7 package** — no commits were added, removed or altered; the
tree in this ZIP is the v7 tree, byte for byte. The full code
record for the tree remains `reports/V7_FIXES.md` (the 20-commit
delta over v6, FIX-51…FIX-56) plus the gate and proof reports
carried in `reports/`.

This package exists because the 2026-11-15 release gate returned
**DO NOT SHIP** on the v7 package (`reports/RELEASE_GATE_NOV15.md`).
Two of the five ship conditions failed. Both are package-level
failures, not code failures, and this repackage closes both:

## Closure 1 — the acceptance now ran on THIS tree (gate failure 3)

The gate found the full four-mode real-export acceptance had run
on tip `9ba6602`, not on the ship tip `5ea71bf` (92 files changed
in between, including render-path code). The acceptance has now
been re-run on `5ea71bf` itself, with the identical method and
fixture (Japan source, real pipeline, real PostgreSQL):
`reports/REAL_EXPORT_ACCEPTANCE_ON_TIP.md` +
`reports/exports_on_tip_manifest.csv`.

- **32/32 exports**, all ffprobe-verified 1080×1920, h264 + AAC.
- **Manual Cutter × Plain and × Auto: PASS outright.**
- **Full AutoClip and both Find Moments modes, × Plain and
  × Auto: PASS on the degraded path.** Proof level, stated
  plainly: the build sandbox holds no provider keys by design, so
  the AI-dependent combos exercised the app's designed failure
  path — provider failover into the deterministic selection
  floor, every degraded segment honestly stamped. This proves the
  pipeline, the modes, the templates and the honesty machinery on
  this exact tree; it is **not** AI-selection-quality proof. Real
  AI quality with live providers remains user-machine validation
  (KNOWN_ISSUES gap G-V8-3).
- **Plain honesty on this tip:** every Plain export's source map
  is 100% `plain` shots; audio cross-correlation vs source
  0.9993 / 0.9917 / 0.9992 (no music bed); Auto contrast 0.8008
  with full 13-key ClipPlans on Auto clips vs none on Plain.
- **F-R1 confirmed FIXED at rendered level:** Find Moments +
  Manual Plain picks now render differently from Auto picks
  (pre-fix they were byte-identical with zero reframe shots; the
  pick renders now carry a full 34-shot reframe plan).
- **F-R2 confirmed FIXED:** after six degraded combos, zero
  stand-in payloads in the shared analysis cache; degraded
  checkpoints carry no `analysis` key.
- **Settings persistence across restart: PASS** — a task created
  Plain / music-off / B-roll-off kept those exact settings across
  a PostgreSQL restart and a fresh-process re-read, and its
  exports are byte-identical to the same combo's exports.
- **Checkpoint resume:** one combo finished through the app's own
  checkpoint resume after a real, unplanned VM restart mid-run
  (10/10 clips).
- **New product defects found by the re-run: NONE.** Two carried
  observations (degraded floor exports lack captions/hook title —
  gap G-V8-4; degraded picks carry no ClipPlan — re-check with
  live providers) are recorded in the report itself.

KNOWN_ISSUES gap **G-V7-3 is therefore CLOSED**; the v8 addendum
in `KNOWN_ISSUES.md` records the closure and renumbers the
carried gaps (G-V8-1…G-V8-7, with the mapping stated there).

## Closure 2 — the package now contains its own evidence (gate failure 2)

The v7 ZIP did not contain `SETTINGS_INTEGRITY_AUDIT.md` or
`AI_MODEL_BAKEOFF.md` anywhere in its 1,579 entries, and its
`DELIVERY_CONTENTS.md` never disclosed the absence. This package
carries both documents at the **wrapper level**, beside
`KNOWN_ISSUES.md`:

- `SETTINGS_INTEGRITY_AUDIT.md` — SHA-256
  `298fed5a10cea6881274412531ff95ad58a66e5b3c05ea91fd8cea26a068cdb4`,
  byte-identical to the audited Day-1 original.
- `AI_MODEL_BAKEOFF.md` — SHA-256
  `d5acafa8e010e15e9e4a185c415236e6ab71c89f396ef17e44ce1592463b519e`,
  byte-identical to the Day-2 original. Its honesty statements
  stand unchanged: the live phase is PENDING, no winner is named,
  no unverified model was wired; the shipped ladder is the proven
  one.

`DELIVERY_CONTENTS.md` now carries explicit rows for both
documents, and the validation checklist gained an item (45) that
has you confirm both files are in the extracted folder.

## Everything else that changed vs the v7 package (complete list)

- `reports/` gains this record, `RELEASE_GATE_NOV15.md`,
  `REAL_EXPORT_ACCEPTANCE_ON_TIP.md` and
  `exports_on_tip_manifest.csv`. All 47 v7 reports are carried
  unchanged.
- `KNOWN_ISSUES.md` — v8 addendum at the top (this closure;
  G-V7-3 closed; carried gaps renumbered G-V8-1…G-V8-7). All
  earlier text carried forward unchanged.
- `VALIDATION_CHECKLIST.md` — v8 addendum with items 45–46
  (confirm the two evidence docs are present; the v7 ZIP is not
  approved — install this one). Expected verify line unchanged:
  **167 passed, 0 failed**.
- `DELIVERY_CONTENTS.md` — v8 intro, rows for the two evidence
  documents and the new reports, updated script-difference list.
- `INSTALLATION_GUIDE.md` — v8 title/names and a "what v8 is"
  block. The install procedure itself is unchanged.
- `install.ps1` / `verify.ps1` / `rollback.ps1` — **naming and
  header text only** (release name, ZIP auto-find pattern,
  backup-manifest line, step-6b comment, header notes). No logic
  change: the executable logic is identical to the v7 package's
  scripts, which carry the proven backup-first flow and the
  step-5 restart-from-your-project-folder fix. The verify
  expectation is unchanged at **167** (same tree, same check
  list — re-derived from this package's tree for this build).

## Status

This package **supersedes the v7 ZIP** (SHA-256 `54502a16…`),
which was never approved for install and must not be installed.
Like every package before it, this one is **not approved for
install until Luffy explicitly approves it** — the delivery
message carries the build identity, the SHA-256, the manifest
verification result, and the backup/rollback instructions.
