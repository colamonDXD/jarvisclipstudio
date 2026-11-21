# INTEGRATED GATE — tip 5ea71bf (next-milestone; font-union fix merge on gate-RED 9b24c7b) — VERDICT: GATE GREEN

Date: 2026-11-14. Resume lane (3rd attempt): the previous gate agent was
cancelled mid-Q2 by its parent run ending, NOT by a failure. Q1 preserved
from that run; Q2 re-run from scratch twice (see "Provisioning / restarts").
Snapshot worktree `~/workspace/jcs-gate-5ea71bf`, detached @
`5ea71bf` ("Merge font-union fix (fix-font-union @ 50224e4)").
Baseline for NEW-vs-baseline classification: the 9b24c7b gate
(`INTEGRATED_GATE_9B24C7B.md`), which was RED on exactly one NEW failure
(tsc TS2322 at `inspector-fonts.test.tsx(113,38)`).

## Verdict

**GATE GREEN on 5ea71bf.** The 9b24c7b gate's one NEW failure is gone and
nothing else moved: backend 4,656/0, frontend 899/0, tsc back to exactly the
11-error baseline set (sorted-error-line diff vs the 9b24c7b log shows
exactly one line removed — the font-union line — nothing added, nothing
missing, nothing changed), eslint 0/0, Prisma generate exit 0, production
`next build` exit 0, smoke 162 routes / 136 paths, bridge 0-line diff,
migrations 48 (identical list), all solos at baseline. No NEW failure, no
regression, no test weakened, no production code touched by this gate.
This verdict does not by itself ship anything — the Nov 15 10:00 IST
release gate owns the ship decision, and it must gate THIS tip (5ea71bf).

## Legs

| Leg | Result | vs 9b24c7b baseline |
|---|---|---|
| Backend Q1 (preserved) | **1,208 passed / 0 failed** (43 warnings) in 629.97s | identical |
| Backend Q2 (re-run from scratch) | **1,162 passed / 0 failed** (0 warnings) in 868.51s | identical |
| Backend Q3 | **1,049 passed / 0 failed / 2 skipped** (13 warnings) in 615.09s | identical |
| Backend Q4 | **1,237 passed / 0 failed / 14 skipped / 7 xfailed** (82 warnings) in 269.60s | identical |
| Backend non-unit (Q5) | 12 passed / 220 skipped (environment-bound integration files, no PG wired into that leg — same as baseline) | identical |
| Selection-eval harness (Q6) | **19 passed / 0 failed** | identical |
| Solo: golden request-shape | **11 passed / 0 failed** (10 warnings) | identical |
| Solo: migration safety | **1 passed** | identical |
| Solo: inventory guard | **6 passed** | identical |
| Solo: SQL-exec family (default env) | **123 passed / 12 skipped / 7 xfailed** | identical |
| Solo: SQL-exec family (LIVE PG 16.15) | **135 passed / 7 xfailed / 0 skipped** | identical |
| Solo: editor roundtrip (LIVE pristine PG 16.15) | **2 passed / 0 failed** | identical |
| Solo: skip-leg | 16 passed / 3 skipped (named opt-in: separation hallucination, separation quality, kokoro cache — same three as baseline) | identical |
| Frontend vitest | **899 passed / 0 failed / 14 skipped**; 139 files passed, **4 failed files** (below) | identical |
| Changelog test | **4 passed** | identical |
| tsc (gate condition: NO generated Prisma client) | **11 errors — exactly the baseline set** (see diff below) | **12 → 11, the one NEW error gone** |
| eslint (src + full) | **exit 0 / exit 0** | identical |
| Prisma generate | exit 0 | identical |
| Production `next build` | **exit 0** (74/74 static pages; 14:42:05Z → 14:49:37Z) | identical |
| Smoke (create_app) | **162 routes / 136 distinct paths**, exit 0 | identical |
| Reference-styles bridge | **0-line diff vs 61ad303** (4/4 proxy-route files byte-identical) | identical |
| Migrations | **48 files**, byte-identical list to 9b24c7b | identical |

Backend unit total (Q1–Q4): **4,656 passed / 0 failed / 16 skipped / 7 xfailed**.

## The fix, confirmed by the gate (not just by the lane)

Tip delta `9b24c7b..5ea71bf` = 3 files, +6/−3, exactly as
`FONT_UNION_FIX.md` describes: `document.ts` (union gains
`"Montserrat-Black" | "Montserrat-ExtraBold"`), `inspector.tsx`
(handler parameter annotated `CaptionStyle["font"]`, cast dropped),
`CHANGELOG.md` (one `**Fixed:**` line). No test file touched, no test
assertion changed, no backend change, no migration.

## tsc error-list diff (sorted `error TS` lines, 5ea71bf vs 9b24c7b)

Method: gate condition honoured — `run-frontend.sh` removed
`src/generated` + `tsconfig.tsbuildinfo` before running the exact gate
command `./node_modules/.bin/tsc --noEmit`, so the generated Prisma client
could not silently clear baseline errors. Diff of the sorted error lines:

```diff
 10a11
-> src/components/editor/inspector-fonts.test.tsx(113,38): TS2322: Type '"Montserrat-ExtraBold"' is not assignable to type '"TikTokSans-Regular" | "TikTokSans-Bold" | "Poppins-ExtraBold" | "THEBOLDFONT"'.
```

Exactly one line removed, nothing added, nothing missing, nothing changed.
New count 11, baseline count 12. The 11 remaining errors are byte-identical
to the 9b24c7b baseline set (2× TS2307 missing generated Prisma client,
8× TS7006 implicit-any in `admin/page.tsx` / `status/page.tsx`, 1× TS2322
`{}` vs `ReactNode` in `admin/page.tsx(438,25)`).

## Baseline-known / environment items (not failures)

- The 4 failed vitest FILES are the same baseline-known set, verified by
  diff of the failed-file lists against the 9b24c7b log (identical):
  `src/server/affiliates.integration.test.ts`,
  `src/server/announcements.integration.test.ts`,
  `src/server/service-status.integration.test.ts`,
  `src/server/user-acquisition.integration.test.ts` — file-level env
  failures (real-Postgres integration files), **zero failed tests**.
  `VITEST_EXIT=1` is the same exit the baseline gate recorded.
- Bridge note: commit 61ad303 is NOT an ancestor of 5ea71bf (same as the
  9b24c7b gate recorded for its tip) — the bridge check is the 0-line diff
  on the 4 proxy-route files, which holds. A `git diff 61ad303 HEAD` on the
  wider `*reference-style*` glob DOES show a diff, but that is
  `frontend/src/lib/reference-styles.ts` gaining the close-call fields
  (`base_niche_margin` / `base_niche_runner_up` /
  `base_niche_close_call`) from a later lane — a different file, not part
  of the bridge's 4 proxy-route files, and already present in the 9b24c7b
  baseline tip.
- Protected tag `v6-candidate-90f1cb4` verified still →
  `90f1cb41b0837a04e4e772ee006857938a7db2fb` (deref, unchanged).

## Provisioning / restarts log

- Resume-lane start 14:01:18Z: snapshot verified @ 5ea71bf (clean except
  pre-existing untracked `backend/.drawtext-5027/` from the Q1 run);
  `~/workspace/jcs-next` verified @ 5ea71bf, clean, untouched throughout.
  tesseract 5.3.4 present at `/usr/bin/tesseract`; scratch PG 16 accepting
  on 127.0.0.1:55433 (data dir `/tmp/.pg-gate-5ea71bf/data`); sanctioned
  venv `~/workspace/jcs-modes/backend/.venv` pytest 9.0.3; no stray
  pytest/vitest processes. No provisioning was needed in this lane — no
  reinstall, no PG restart, no re-initdb.
- Q2 history: first attempt 13:38:22Z (interrupted by runtime restart
  ~13:43Z, no summary — discarded); second attempt 13:45:05Z (interrupted
  by parent cancellation ~13:55Z at ~80% dots, no summary — preserved as
  `chunk-q2-partial-discarded-1355Z.txt`, discarded); third attempt
  14:01:24Z, this lane, completed 14:16:34Z: 1,162/0. Every restart and its
  reason is recorded in `driver-progress.txt`.
- Driver `run-resume.sh` ran Q2 → Q3 → Q4 → solos → frontend → build →
  smoke serially, exit 0, `GATE_ALL_DONE 2026-11-14T14:50:09Z`. No chunk
  failed environmentally; no re-run of any completed chunk was needed.
- Untracked residue in the snapshot (harmless test/build output, left in
  place, does not pollute the verdict): `backend/.drawtext-5027/` (Q1 run),
  `backend/.drawtext-20969/` (Q2, 14:11Z), `backend/.drawtext-37585/`
  (Q3, 14:17Z), `frontend/jcs-fix-rps/frontend/node_modules/.pnpm/`
  (empty, created 14:49:36Z during the `next build` leg; no source file
  references that path).

## Rendered proof — N4/N6 (ATTEMPTED ITEM: OWED, not attempted)

Not attempted in this lane, by design, not by oversight: the gate verdict
is the deliverable and the run window was ending (verdict secured
~14:50Z / 20:20 IST, past the 20:00 IST handover); the probe needs a
fresh serial render slot and the 0a63942 A/B harness to be re-read and
rebuilt on this tip, which would have delayed the verdict the Nov 15
release gate is waiting on. The brief explicitly says not to delay the
verdict for it. **OWED to the Nov 15 release gate or a follow-up lane.**
Probe design (unchanged from `INTEGRATED_GATE_9B24C7B.md`):

- N4: two renders of the same captioned clip with `words_per_line` 2 vs 6;
  pass bar = different ASS span counts in the rendered caption files.
- N6: render a clip with caption font `Montserrat-Black` and one with
  `Montserrat-ExtraBold`; pass bar = the font name present in the ASS +
  successful export (the inspector runtime test already proves the select
  shows them; the render proves the renderers load them by name).

## Guardrails

- No production code edited by this gate; no test weakened; no error
  suppressed. The gate changed nothing in the snapshot except untracked
  test/build residue listed above.
- `~/workspace/jcs-next` untouched (still 5ea71bf, clean). Luffy's
  installed app / backups / config / `.env` untouched. Nothing pushed or
  uploaded. ₹0; no Gemini anywhere.
- Logs: `~/workspace/research_notes/sprint-7day/gate-logs-5ea71bf/`
  (`chunk-q1..q4.txt`, `chunk-q5-nonunit.txt`,
  `chunk-q6-selection-eval.txt`, `solo-*.txt`, `sql-exec-family*.txt`,
  `frontend-vitest.txt`, `changelog-test.txt`, `tsc.txt`,
  `eslint.txt`, `eslint-full.txt`, `prisma-generate.txt`,
  `next-build.txt`, `smoke.txt`, `driver-progress.txt`,
  `chunk-q2-partial-discarded-1355Z.txt`).

## Next

Nov 15 10:00 IST release gate owns the ship decision, on THIS tip
(5ea71bf). N4/N6 rendered proof is owed to that gate or a follow-up lane.
Open known non-blocking items carried forward: LOW = G-2 + N2 (note only);
INFO F-3/F-5; F-P4/F-P5 (missing B-roll footage / missing caption font
edge cases) remain queued per the SPRINT_LOG.
