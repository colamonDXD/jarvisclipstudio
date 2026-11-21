# Full-Suite Gate — next-milestone @ ae7e50d

**Gate date:** 2026-11-11 (run 21:59–22:55 IST approx; backend chunks + frontend + tsc)
**Tree gated:** `~/workspace/jcs-next`, branch `next-milestone`, tip `ae7e50d`
(`merge(separation): Stage 2 — render-side strip of original music via the vocals stem`,
on top of Stage 1 @ `6384d81`, Plain mode @ `1d0d615`, brand-kit gaps @ `abcde5c`).
Tree verified clean at start and unchanged at end; tip confirmed identical
after every chunk.
**Venv:** `~/workspace/jcs-modes/backend/.venv` (no new deps installed).
**Env:** `OMP_NUM_THREADS=2`, serial, proxy env stripped
(`NO_PROXY=localhost,127.0.0.1`); `TMPDIR` pointed at a workspace scratch dir
(`gate-logs-next/tmp-ae7e50d`, removed after) per the 4eda669 adjudication
lesson, so the 512 MB `/tmp` tmpfs could not trip the media-replication
disk guard. `/tmp` stayed at 300K used throughout.
**Collection:** 3,698 tests in `backend/tests/unit` (233 files + conftest).

## Verdict: PASS

Backend: **3,695 passed / 3 skipped / 0 failed** (= 3,698 collected).
Frontend: **830 passed / 2 failed / 12 skipped** in the full run — both
failures adjudicated environmental with solo proof (see §2); effective
**832 passed / 0 failed / 12 skipped**.
tsc: **11 errors, byte-identical to the known baseline.**
Migration safety: PASS (45-file set, zero semicolon-in-comment violations,
migration-safety test green).
Bridge: PASS (all 4 files byte-identical to 61ad303).
Smoke: `create_app()` OK, 153 routes / 129 distinct paths, all surfaces
registered.

No code, tests, or branches were edited by this lane; no commits; nothing
uploaded; the frozen RC / package / Luffy's machine untouched.

## 1. Backend chunk results

| Chunk | Files | Result | Duration | Log |
|---|---|---|---|---|
| Q1 | test_affiliate_email – test_clip_editor_captions (58) | 903 passed / 0 failed | 238.02 s | `gate-logs-next/ae7e50d-q1.txt` |
| Q2 | test_clip_edits – test_intro_exclusion (58) | 934 passed / 0 failed | 634.07 s | `gate-logs-next/ae7e50d-q2.txt` |
| Q3 | test_learning_api – test_session_lock_connection (58) | 867 passed / 2 skipped / 0 failed | 503.95 s | `gate-logs-next/ae7e50d-q3.txt` |
| Q4 | test_sfx – test_youtube_video_service_cleanup (59) | 991 passed / 1 skipped / 0 failed | 354.58 s | `gate-logs-next/ae7e50d-q4.txt` |
| **Total** | **233 files** | **3,695 passed / 3 skipped / 0 failed** | **~29 min** | |

Chunk totals sum exactly to the 3,698 collected. All four pytest processes
printed their complete summary and then hung at interpreter exit (idle,
~750 MB RSS) — the known sandbox exit stall; each was killed only after its
summary was on disk. No test result was lost.

Growth vs the 4eda669 gate (3,585 effective): +113 collected tests from the
merges since (Plain mode, brand-kit gaps, B-roll word-sense fix, separation
Stages 1+2, incl. new suites test_separation*.py, test_template_mode.py,
test_brand_kit_gaps.py, test_broll_word_sense.py, test_reframe_plain.py).

### Overlap note (no contamination)

The parent's Stage-2 post-merge targeted run (4 files:
test_separation_render / test_separation / test_template_mode /
test_brand_kit_gaps, started 21:47 IST, `TMPDIR=~/workspace/.tmp-main`)
was alive through the entire gate and was still alive at gate end. It is
not this lane's work and was left alone. No contamination manifested:
every backend chunk ran 0-failed while it overlapped, and the render-heavy
`test_edl_av_sync.py` (Q2) is green. A `jcs-fix-rps` vitest worker was also
observed during the frontend run; same conclusion.

## 2. Frontend

Full suite (`vitest run --testTimeout=120000`): **830 passed / 2 failed /
12 skipped** (844 tests, 130 files; 125 passed, 5 failed). Log:
`gate-logs-next/ae7e50d-frontend.txt`. Duration 381.79 s.
`node_modules` was a symlink to `jcs-quality`'s copy (package.json +
pnpm-lock.yaml verified byte-identical first); symlink **removed after**.

### 2a. The 2 failed tests — ENVIRONMENTAL, solo-proven PASS

Both are PGlite/real-Postgres integration tests with a **hard 30 s
per-test timeout** written in the test (`}, 30_000);` — the CLI
`--testTimeout=120000` does not override a per-test timeout. Each runs
two full PGlite migration sequences, sitting near its 30 s budget even
solo.

| Test | Full-run failure | Solo re-run |
|---|---|---|
| `src/server/clip-duration.integration.test.ts` — "preserves clip length defaults in either migration order…" | `Test timed out in 30000ms` | PASS |
| `src/server/social-publishing.integration.test.ts` — "creates identical social tables from either migration runner, in either order" | `Test timed out in 30000ms` | PASS |

Solo re-run of both files together: **2 files passed, 5 tests passed**
(`gate-logs-next/ae7e50d-frontend-solo.txt`, 29.57 s). No assertion
failed anywhere — pure timeout under full-suite + concurrent load.
Neither test touches the ae7e50d delta (they run the 20261025 clip-length
and 20260901 social migrations, both pre-dating the new migrations).

### 2b. The 3 failed suites — ENVIRONMENT-BOUND, same as two prior gates

All three fail in `beforeAll` on `Hook timed out in 10000ms` (PGlite
instantiation + `init.sql` exec exceeds the 10 s hook budget in this
sandbox; `hookTimeout` is not covered by `--testTimeout`):

- `src/server/affiliates.integration.test.ts`
- `src/server/announcements.integration.test.ts`
- `src/server/user-acquisition.integration.test.ts`

Solo re-run of all three (`gate-logs-next/ae7e50d-frontend-solo2.txt`):
same 3 hook timeouts, 12 tests skipped. This is the **identical
name set and signature as the 4eda669 gate** (4 failed files incl. these
3) **and the 1291851 gate** (3 failed files, same 3). `git diff 4eda669
ae7e50d` shows **zero changes** to `init.sql` or any of the three test
files, so nothing in the delta can have caused it. The 12 skipped tests
in the full run are exactly the 12 tests inside these 3 suites
(2 + 3 + 7).

**Effective frontend: 832 passed / 0 failed / 12 skipped** (830 + the 2
solo-proven; the 12 skipped are the environment-blocked PGlite suites).

## 3. tsc

`tsc --noEmit`: exit 2, **11 errors, byte-identical to the known baseline**
(`diff` vs `gate-logs/tsc-quality-baseline.log` empty). Log:
`gate-logs-next/ae7e50d-tsc.txt`.

## 4. Migration safety

`backend/src/migrations/sql/` holds **45 files** = RC's 42 +
`20261110_0001_task_classification.sql` + dual
`20261110_0002_task_template_mode.sql` /
`20261110_0002_task_separation_opt_in.sql`. Sprint set (all `202611*.sql`,
21 files): **zero semicolon-in-comment violations** (static scan).
`test_migration_files_safety.py` (replays the runner's `;`-split over
every migration file) ran inside Q3 and in the explicit confirmation run
— green in both. PASS.

## 5. Bridge check (next-milestone tree)

All 4 reference-styles bridge files present and **byte-identical to
61ad303** (content check via `git show 61ad303:<path>` + `cmp`, not
ancestry):

- `frontend/src/app/api/reference-styles/route.ts`
- `frontend/src/app/api/reference-styles/route.test.ts`
- `frontend/src/app/api/reference-styles/[...path]/route.ts`
- `frontend/src/app/api/reference-styles/[...path]/route.test.ts`

PASS.

## 6. Smoke: create_app()

`create_app()` registers all surfaces on ae7e50d: **153 routes / 129
distinct paths** (RC gate: 127 distinct paths; +2 from the merges since).
Spot counts: watch-folder 2, brand-kit 7, brief 2, campaign 5,
reference-style 4, sfx 3, moments 1, candidates 1, template 1,
separation 0 (opt-in is a task field, not a route — expected).

## 7. Non-pass adjudication — test_range_seek (standalone only)

**Not a gate failure.** In the gate itself the test passed (Q3, 867/0).
Run standalone afterwards (explicit confirmation run
`gate-logs-next/ae7e50d-confirm.txt`), the test fails:

- `tests/unit/test_range_seek.py::test_multi_range_render_seeks_to_the_first_kept_moment`
  — `ValueError: '-ss' is not in list` at `test_range_seek.py:24`;
  captured log shows the NVENC probe failing
  ("h264_nvenc is not in the ffmpeg encoder list").

This is the brief's WATCH item, pre-adjudicated: **probe-state
sensitivity, pre-existing, not merge-caused**. Mechanism (per
`wave4-separation/STAGE2_REPORT.md`): when the NVENC probe has not yet
run in the process, its probe call lands in the test's captured command
list as `commands[0]`, which carries no `-ss`; in the full-suite order
an earlier test has already triggered (and cached) the probe, so the
test passes in-chunk. The Stage 2 lane reproduced it **identically on a
pristine worktree of base `6384d81`**; my standalone failure matches
that reproduction exactly (same test, same `ValueError` at the same line,
same NVENC-probe log lines). The sibling test
`test_crossfade_render_seeks_before_reading` passes standalone.
**Do not count as a regression; no action.**

## 8. What this gate does NOT cover

- No renders were run by this lane (verification-only gate; render
  evidence for the merged features lives in the lanes' own reports).
- Live-LLM classification leg, Postgres atomicity, benchmark numbers,
  and aa2fdc74's inner cause remain user-machine pending (unchanged).
- The parent's sep2 post-merge run was still alive at gate end; its
  own result is the parent's to record.

## Appendix — evidence map

- Backend chunks: `research_notes/sprint-7day/gate-logs-next/ae7e50d-q1.txt`
  (903/0), `-q2.txt` (934/0), `-q3.txt` (867/2 skipped), `-q4.txt`
  (991/1 skipped); collection `ae7e50d-collect.txt` (3,698 tests, 233 files);
  chunk file lists `ae7e50d-q1..q4-files.txt`
- Frontend: `gate-logs-next/ae7e50d-frontend.txt` (830/2/12),
  solo re-runs `ae7e50d-frontend-solo.txt` (5/5 PASS) and
  `ae7e50d-frontend-solo2.txt` (3 hook timeouts, 12 skipped)
- tsc: `gate-logs-next/ae7e50d-tsc.txt` (11 errors, baseline-identical)
- Confirmation: `gate-logs-next/ae7e50d-confirm.txt`
  (migration-safety PASS; range_seek 1 pre-existing standalone failure)
- Baselines: 4eda669 gate adjudicated GREEN (effective 3,585/0 backend,
  827/0 frontend, tsc 11); 1291851 gate (3,458/3/5 errors, frontend
  802/0/12, tsc 11); retired list
  `~/workspace/final-failed-list.RETIRED-2026-11.txt`
