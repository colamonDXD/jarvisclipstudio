# DELTA GATE — `e015b809` (frontend-only delta over V10 tree `901ae252`)

- **Date:** 2026-11-17, ~19:30–20:00 IST
- **Lane:** DELTA-GATE (verification only — no code changes, no commits, no pushes)
- **Tree:** `~/workspace/jcs-next`, branch `next-milestone`
- **HEAD before:** `e015b809f42706b0636abfe9b66323e3f4f2c14e` — verified
- **HEAD after:** `e015b809f42706b0636abfe9b66323e3f4f2c14e` — verified, tree clean
- **Logs:** `~/workspace/deltagate-logs-e015b809/` (vitest-full, 4 solo re-runs, 3-at-once re-run, tsc, next-build, changelog-vitest, smoke)

## 1. Diff gate (own check, before any test ran)

`git diff --name-only 901ae252..e015b809` = **20 files**: `CHANGELOG.md` + 19 frontend files
(`frontend/src/app/globals.css`, `layout.tsx`, `tasks/[id]/page.tsx`,
`components/app/app-shell.tsx`, `components/home-app*.tsx` + 3 test files,
`components/reactbits/` ×6 incl. `LICENSE_NOTE.md` + `reactbits.test.tsx`,
`components/ui/button.tsx`, `lib/create-summary.{ts,test.ts}`, `lib/run-summary.{ts,test.ts}`).

**No backend file, no migration, no compose file in the diff — PASS.** The two merged
branches are frontend-only as briefed (`feat/frontend-completion` 15c7720a:
recipe picker, pre-processing summary, run summary, legacy `include_broll` chip
retired; `feat/frontend-ui-polish` e015b809: vendored React Bits CSS adaptations,
darker theme).

## 2. Full vitest — `npx vitest run` (frontend, OMP_NUM_THREADS=2)

| | Count |
|---|---|
| Tests passed | **940** |
| Tests failed | **0** |
| Tests skipped | **14** (954 total) |
| Test files passed | **150** |
| Test files failed | **4** (154 total) |
| Exit code | 1 (driven by the 4 failed files, not by any failed test) |

**Failed files — all one family, all the same signature:**

1. `src/server/affiliates.integration.test.ts` — `affiliates table (real Postgres)` —
   `Error: Hook timed out in 10000ms` in `beforeAll` (`new PGlite()`)
2. `src/server/announcements.integration.test.ts` — same hook timeout
3. `src/server/service-status.integration.test.ts` — same hook timeout
4. `src/server/user-acquisition.integration.test.ts` — same hook timeout

Zero failed *tests* anywhere in the run. This is exactly the known environmental
class: `src/server/*.integration.test.ts` PGlite files pass solo and time out on
`beforeAll` hook init under full-run concurrency.

**Attribution evidence (own solo re-runs, each exit 0):**

| File | Solo result |
|---|---|
| affiliates.integration.test.ts | 3/3 PASS |
| announcements.integration.test.ts | 2/2 PASS |
| service-status.integration.test.ts | 2/2 PASS |
| user-acquisition.integration.test.ts | 7/7 PASS |

A 3-at-once re-run of the last three still failed with the identical hook-timeout
signature — concurrency, not code. `social-publishing.integration.test.ts`, the
5th PGlite file that failed at the `c7c5b3ca` gate (32 s timeout), **passed** in
this full run.

**Comparison vs `c7c5b3ca` delta gate:** 921 passed / 1 failed test / 14 skipped,
5 failed files → now 940 passed / 0 failed / 14 skipped, 4 failed files.
Net: +19 passed tests, −1 failed test, −1 failed file. The delta's 6 changed test
files (22 tests, independently re-run 22/22 by the mainline run) are inside the
940. No test was weakened; nothing was skipped over silently.

**Verdict: PASS with environmental attribution (evidence above).**

## 3. `tsc --noEmit` — PASS

Exit 0, zero errors, empty log.

## 4. `npx next build` — PASS

Exit 0. `✓ Compiled successfully in 36.3s`, `✓ Generating static pages (75/75)` —
identical page count to the `c7c5b3ca` gate (75/75). The 6 `Error` lines in the
build log are all the same single build-time warning, repeated:
`[Error [BetterAuthError]: You are using the default secret…]` — the known
default-secret env warning also recorded at the `c7c5b3ca` gate, not a build failure.

## 5. Changelog vitest — PASS

`npx vitest run src/lib/changelog.test.ts`: **4/4 passed**, 1 file, exit 0.

## 6. Smoke — route census — PASS

Reused the `c7c5b3ca` lane's exact script (`~/workspace/deltagate-logs/smoke.py`,
run from `backend/` with the sanctioned venv `~/workspace/jcs-modes/backend/.venv`,
Python 3.11.18):

- **168 routes / 139 distinct paths** — identical to the `c7c5b3ca` gate, the
  13-branch gate (`b05e6da`), the items 14/15 gate (`40f5407`), and the `327886b3` re-gate.
- Methods identical: DELETE 17, GET 80, HEAD 4, PATCH 9, POST 58, PUT 4.
- Per-surface counts identical (tasks 64/54, brand-kit 7/3, channel-monitor 6/4, …).

Expected: no change — the delta touches no backend route code. Confirmed.

## 7. NOT RUN — and why

- **Backend pytest suite: NOT RUN.** Zero backend files changed in this delta
  (diff evidence §1), so there is no backend leg to re-gate. The backend code on
  this tip is byte-identical to the V10 tree (`901ae252`); the V10 backend verdict
  is therefore not re-proven here and is **STALE as a fresh PASS for this tip** —
  it is not claimed. No backend coverage is claimed by this gate.
- **Renders: NOT RUN** (not in this gate's brief).
- **ESLint: NOT RUN** (not in this gate's brief; the `c7c5b3ca` lane recorded it
  clean there, no eslint-relevant config changed in this delta).

## 8. Verdict

**DELTA GATE PASS on `e015b809`.** All five legs ran on the exact tip:
vitest 940 passed / 0 failed tests (4 PGlite files failed on hook timeout,
all 4 proven PASS solo) / 14 skipped; tsc 0 errors; next build exit 0, 75/75
pages; changelog vitest 4/4; smoke 168/139 identical to baseline. Backend pytest
explicitly NOT RUN (no backend change). No code changed, no commits, no pushes
by this lane. Tree verified clean at `e015b809` before and after.
