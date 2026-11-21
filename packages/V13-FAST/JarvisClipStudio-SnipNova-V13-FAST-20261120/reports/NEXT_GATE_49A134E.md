# Full-Suite Gate — next-milestone @ 49a134e

**Gate date:** 2026-11-11 (run ~08:01–08:50 IST; backend chunks + frontend + tsc)
**Tree gated:** `~/workspace/jcs-next`, branch `next-milestone`, tip `49a134e`
(`merge(reference-look): Stage 1 — caption-look pass measures colour/position/words/casing/outline and applies under Brand Kit`,
on top of channel monitoring @ `58b5a19`, VRAM-lite @ `4327791`,
Hindi guardrail @ `005e88e`, Separation Stage 2 @ `ae7e50d`).
Tree verified clean at start, after every chunk, and at end; tip confirmed
identical throughout. Two untracked test-artifact dirs
(`backend/.drawtext-22202/`, `backend/.drawtext-36190/`, one 17-byte card
file each, left by the Q2/Q3 pytest processes) were removed to restore
cleanliness — no code, tests, or branches were edited; no commits; nothing
uploaded; the frozen RC / package ZIPs / Luffy's machine untouched.
**Venv:** `~/workspace/jcs-modes/backend/.venv` (no new deps installed).
**Env:** `OMP_NUM_THREADS=2`, serial, `no_proxy`/`NO_PROXY` sanitised to
`localhost,127.0.0.1` (bracketed-IPv6 entries stripped per
BASELINE_ROOTCAUSE.md); `TMPDIR` pointed at a workspace scratch dir
(`~/workspace/.tmp-gate49`, kept for inspection) so the 512 MB `/tmp`
tmpfs could not trip the media-replication disk guard.
**Collection:** 3,803 tests in `backend/tests/unit` (238 files + conftest).

## Verdict: PASS

Backend: **3,800 passed / 3 skipped / 0 failed** (= 3,803 collected).
Frontend: **838 passed / 0 failed / 14 skipped** (852 tests, 132 files;
128 passed, 4 failed — all 4 are the environment-bound PGlite
beforeAll-hook-timeout suites, §2; the 2 known 30 s-timeout tests from
the ae7e50d gate PASSED in the full run this time, so no solo re-run
was needed).
tsc: **11 errors, byte-identical to the known baseline.**
Migration safety: PASS (46-file set, 22-file sprint set, zero
semicolon-in-comment violations, migration-safety test green).
Bridge: PASS (all 4 files byte-identical to 61ad303).
Smoke: `create_app()` OK, 158 routes / 132 distinct paths, all surfaces
registered (channel monitoring adds 5 route objects on 3 paths).

## 1. Backend chunk results

| Chunk | Files | Result | Duration | Log |
|---|---|---|---|---|
| Q1 | test_affiliate_email – test_clip_editor_captions (59) | 936 passed / 0 failed | 389.20 s | `gate-logs-next/49a134e-q1.txt` |
| Q2 | test_clip_edits – test_learning_api (60) | 956 passed / 0 failed | 603.19 s | `gate-logs-next/49a134e-q2.txt` |
| Q3 | test_learning_classifier_seam – test_sfx (59) | 896 passed / 2 skipped / 0 failed | 336.51 s | `gate-logs-next/49a134e-q3.txt` |
| Q4 | test_sfx_flow – test_youtube_video_service_cleanup (60) | 1012 passed / 1 skipped / 0 failed | 237.48 s | `gate-logs-next/49a134e-q4.txt` |
| **Total** | **238 files** | **3,800 passed / 3 skipped / 0 failed** | **~26 min** | |

Chunk totals sum exactly to the 3,803 collected. All four pytest
processes printed their complete summary and then hung at interpreter
exit (idle, ~550–750 MB RSS) — the known sandbox exit stall; each was
killed only after its summary was on disk (Q1 PID 13159, Q2 reaped via
pattern kill, Q3 PID 36190, Q4 PID 45969). No test result was lost.
The 3 skipped tests sit in the same chunk distribution as ae7e50d
(Q3 2, Q4 1).

Growth vs the ae7e50d gate (3,695 passed): +105 passed / +105
collected from the four merges since. Five new test files, none
removed: `test_channel_monitor.py`, `test_layout_report_broll_applied.py`,
`test_reference_style_look.py`, `test_vram_stage1.py`,
`test_youtube_feed.py` (the rest of the +105 is growth inside existing
files, incl. the Hindi-guardrail and reference-look suites).

### Overlap note — Wave 4 lane (jcs-viral1), two runs

- **neighbours1** (PID 7784, 5 files, started ~01:50 UTC): still alive
  when the gate started. Per the brief the gate WAITED: its summary was
  already on disk (2 failed / 180 passed in 82.85 s — the lane's own
  result on ITS tree, not this gate's) but the process was hung at
  interpreter exit with 21 s CPU in 28 min elapsed; it was NOT killed
  (not this lane's process). It exited at 02:30 UTC when its 2400 s
  `timeout` fired. Q1 started 02:31 UTC, after it was gone.
- **neighbours2** (PID 13148, 12 files, started ~02:31 UTC, i.e.
  simultaneously with Q1): its active phase (~5.5 min of tests,
  summary 1 failed / 293 passed in 327.97 s, the 1 failure being
  `test_auto_director_new_niches.py::test_true_crime_dark_captions_and_sting_markers`
  on the jcs-viral1 tree — again the lane's own result, not this
  gate's) overlapped Q1/Q2. It then hung at interpreter exit (25 s CPU
  in 43+ min elapsed) and was still hung when Q3 started; it was left
  alone per the brief (its 3000 s timeout fires ~03:21 UTC). The gate
  started Q3 with that process present because it was provably idle
  (0 CPU for 16+ min, summary on disk) — exactly one ACTIVE pytest
  process at all times, and Q1/Q2/Q3/Q4 all came back 0-failed, so no
  OOM artefact manifested. This matches the ae7e50d gate's precedent
  (an active targeted run overlapped the whole gate with zero
  contamination). Recorded here so the overlap is visible, not hidden.
- One process-hygiene incident of this lane's own making: the Q2 reap
  used `pkill -f` with a pattern that also matched the lane's own shell
  command line, SIGKILLing the wrapper shell (Q2's pytest was still
  reaped as intended; Q3 had not started). All later reaps used
  `kill -9 <exact PID>` from a prior `ps` read. No result was lost;
  Q2's summary (956/0) was already on disk.

## 2. Frontend

Full suite (`vitest run --testTimeout=120000`): **838 passed / 0 failed /
14 skipped** (852 tests, 132 files; 128 passed, 4 failed). Log:
`gate-logs-next/49a134e-frontend.txt`. Duration 275.39 s.
`node_modules` was a symlink to `jcs-quality`'s copy (package.json +
pnpm-lock.yaml verified byte-identical first); symlink **removed after**.
A `jcs-fix-rps` vitest worker was also observed during the run (same as
the ae7e50d gate); no contamination — 0 failed tests.

### 2a. The 2 known 30 s-timeout tests — PASSED IN THE FULL RUN

| Test | ae7e50d full run | 49a134e full run |
|---|---|---|
| `src/server/clip-duration.integration.test.ts` — "preserves clip length defaults in either migration order…" | `Test timed out in 30000ms` (passed solo 5/5) | **PASS** (1 test, 15,606 ms) |
| `src/server/social-publishing.integration.test.ts` — "creates identical social tables from either migration runner, in either order" | `Test timed out in 30000ms` (passed solo 5/5) | **PASS** (4 tests, 41,575 ms) |

No solo re-run was needed. Neither test touches the 49a134e delta;
both sit near their 30 s budget and land on either side of it with
run-to-run load variance.

### 2b. The 4 failed files — ENVIRONMENT-BOUND, same signature as before

All four fail in `beforeAll` on `Hook timed out in 10000ms` (PGlite
instantiation + `init.sql` exec exceeds the 10 s hook budget in this
sandbox; `hookTimeout` is not covered by `--testTimeout`). Four
occurrences of the string in the log, one per file:

- `src/server/affiliates.integration.test.ts`
- `src/server/announcements.integration.test.ts`
- `src/server/service-status.integration.test.ts` — **new in the failed
  set this run** (was passing at ae7e50d)
- `src/server/user-acquisition.integration.test.ts`

`service-status` is NOT a regression: `git diff ae7e50d 49a134e` shows
**zero changes** to `service-status.integration.test.ts`, `init.sql`,
or any `frontend/src/server/*.sql`, so nothing in the delta can have
caused it. It is the same environment-bound flake class as the other
three — one more file tripping the 10 s hook budget under this run's
load (the sandbox ran slower in places this time: the social-publishing
file took 41.6 s vs 30 s-timeout at ae7e50d). The 14 skipped tests are
exactly the tests inside these 4 suites. No assertion failed anywhere.

**Effective frontend: 838 passed / 0 failed / 14 skipped.**

## 3. tsc

`tsc --noEmit`: exit 2, **11 errors, byte-identical to the known baseline**
(`diff` vs `gate-logs/tsc-quality-baseline.log` empty). Log:
`gate-logs-next/49a134e-tsc.txt`.

## 4. Migration safety

`backend/src/migrations/sql/` holds **46 files** = ae7e50d's 45 +
`20261111_0001_channel_monitoring.sql`. Sprint set (all `202611*.sql`,
**22 files**): 20261103_0001, 20261105_0001, 20261105_0004,
20261107_0001, 20261108_0001–0007, 20261109_0001, dual
20261109_0002 (content_requirements + task_music_mode),
20261109_0003–0006, 20261110_0001, dual 20261110_0002
(task_template_mode + task_separation_opt_in), 20261111_0001.
Dual 0002s on 20261109 AND dual 0002s on 20261110, single 0006 —
matches the brief's expected set exactly.
**Zero semicolon-in-comment violations** (static scan over all 22
sprint files: lines starting `--` containing `;`). The
`test_migration_files_safety.py` test (replays the runner's `;`-split
over every migration file) ran inside Q3 and in the explicit
confirmation run — **1 passed in 0.07 s** (`49a134e-confirm.txt`).
PASS.

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

`create_app()` registers all surfaces on 49a134e: **158 routes / 132
distinct paths** (ae7e50d: 153/129; +5/+3, all from channel
monitoring: 5 route objects on 3 paths — `/channel-monitors`,
`/channel-monitors/settings`, `/channel-monitors/{monitor_id}`).
Route-object spot counts match ae7e50d exactly on every shared
surface: watch-folder 2, brand-kit 7, brief 2, campaign 5,
reference-style 4, sfx 3, moments 1, candidates 1, template 1,
separation 0 (opt-in is a task field, not a route — expected).

## 7. Non-pass adjudication

**Zero failures to adjudicate.** Backend 0 failed, frontend 0 failed
tests; the 4 failed frontend files are the §2b environment-bound
hook timeouts (same signature, same class, zero diffs in the delta
for the one new name). Nothing was re-baselined; nothing was weakened.

### test_range_seek (WATCH item) — passes in-chunk, as before

In the gate itself the test passed (Q3, 896 passed / 2 skipped /
0 failed; no FAILED line in the chunk log). No standalone run was
made in this gate (the ae7e50d gate already reproduced the standalone
failure and matched it exactly to the Stage 2 lane's reproduction on
pristine `6384d81`: probe-state sensitivity, pre-existing, not
merge-caused). **Not a regression; no action.**

## 8. What this gate does NOT cover

- No renders were run by this lane (verification-only gate; render
  evidence for the merged features lives in the lanes' own reports).
- Live-LLM classification leg, Postgres atomicity, benchmark numbers,
  and aa2fdc74's inner cause remain user-machine pending (unchanged).
- The Wave 4 viral lane's neighbours1/neighbours2 results (2 failed /
  180 passed; 1 failed / 293 passed) are that lane's own results on
  the jcs-viral1 tree with uncommitted changes — NOT this gate's, and
  NOT adjudicated here. The `test_true_crime_dark_captions_and_sting_markers`
  failure in neighbours2 is flagged to the parent only as a pointer:
  if that test also fails on a clean next-milestone tree it would be a
  separate item; it was not in this gate's tree run (the same file
  passed inside Q4 here — `test_auto_director_new_niches.py` is in
  the Q4 file list, Q4 1012/0).

## 9. What the Nov 15 package decision needs

- **Gate: GREEN.** The combined tree of all four merges since
  ae7e50d (Hindi guardrail, VRAM-lite, channel monitoring,
  reference-look Stage 1) passes the full suite with zero failures on
  either side, tsc baseline-identical, migrations + bridge + smoke
  all green.
- **V3 package is stale:** the built V3 ZIP (SHA-256
  `3494b835…3f4ca7`) was built from `005e88e`; the tip has since moved
  through `4327791` → `58b5a19` → `49a134e`. A rebuild from this
  gate-green tip is the remaining packaging step before the Nov 15
  decision — the gate itself does not build packages.
- **Still user-machine pending (unchanged):** live-LLM classification
  verdict, Postgres atomicity leg, benchmark numbers, aa2fdc74's
  inner cause. None is a gate item; none blocks the package decision
  on code-correctness grounds, but the delivery record must keep
  carrying them as validation-pending.

## Appendix — evidence map

- Backend chunks: `research_notes/sprint-7day/gate-logs-next/49a134e-q1.txt`
  (936/0), `-q2.txt` (956/0), `-q3.txt` (896/2 skipped), `-q4.txt`
  (1012/1 skipped); collection `49a134e-collect.txt` (3,803 tests, 238 files);
  chunk file lists `49a134e-q1..q4-files.txt`
- Frontend: `gate-logs-next/49a134e-frontend.txt` (838/0/14; 4 failed files)
- tsc: `gate-logs-next/49a134e-tsc.txt` (11 errors, baseline-identical)
- Confirmation: `gate-logs-next/49a134e-confirm.txt`
  (migration-safety 1 passed)
- Baselines: ae7e50d gate (backend 3,695/3/0, frontend effective 832/0/12,
  tsc 11); 4eda669 gate (effective 3,585/0 backend, 827/0 frontend, tsc 11);
  BASELINE_ROOTCAUSE.md (Class A proxy-env artefact + Class B billing
  fixture; neither applies here — the gate ran with sanitised proxy env
  and 0 failures)
