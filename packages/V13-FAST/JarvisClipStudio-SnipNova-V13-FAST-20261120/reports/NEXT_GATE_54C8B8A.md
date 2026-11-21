# Full-Suite Gate — next-milestone @ 54c8b8a

**Gate date:** 2026-11-11 (run ~23:26–~01:15 IST; backend + frontend + tsc +
eslint + migrations + bridge + smoke, all on one settled tip)
**Tree gated:** `~/workspace/jcs-next`, branch `next-milestone`, tip
`54c8b8a` (`merge: caption/title face guard — text never intersects a face
(fix-caption-face-overlap)`). Carries since the last full gate @ `537d55e`:
exactly the 6-commit face-overlap lane (`b4686d3`..`ef30e20`); the diff
`537d55e..54c8b8a` is 9 files, +1768/−19 — product code
`backend/src/media/captions.py` (+237), `face_text_guard.py` (new, +197),
`reframe_plan.py` (+294), `video_utils.py` (+12), 4 new test files
(`test_face_text_guard.py`, `test_caption_face_guard.py`,
`test_caption_face_guard_render.py`, `test_reframe_face_boxes.py`),
CHANGELOG. No migration, no frontend, no config.
**Method:** EVERYTHING ran in a detached snapshot worktree at exactly
`54c8b8a` (`~/workspace/jcs-gate54c8b8a-snap`, created before the first
chunk, removed after the gate). **The live tip moved TWICE during this
gate** — first to `77dbbde` (SIGLIP vision-veto merge, landed ~23:28 IST,
during setup, before any chunk ran) and then to `be00fd2` (split-tile
source-border-trim merge, landed during Q3/Q4) — but the snapshot never
moved: verified `54c8b8a` + clean at start, between chunks, and at end,
so all evidence is byte-pure `54c8b8a` with no attribution argument
needed. No per-chunk attribution or snapshot-confirmed re-run was
required. The next gate must cover `be00fd2` (SIGLIP + split-border-trim).
**Venv:** `~/workspace/jcs-modes/backend/.venv` (lockfile venv; cv2
4.13.0 — no new deps installed). **Env:** `OMP_NUM_THREADS=2`, serial
chunks, `TMPDIR` pointed at a workspace scratch dir
(`~/workspace/gate-tmp-54c8b8a`, removed after — never /tmp). The
bracketed-IPv6 `no_proxy` entries were stripped (known httpx InvalidURL
artefact). Every chunk's output went to a log file under
`gate-logs-54c8b8a/` and was read from disk; pytest processes that
lingered after printing their summary (the known interpreter-exit hang)
were killed by exact PID only after the summary was on disk (Q1 PID
15176, Q2 PID 23975, Q3 PID 38790, Q4 PID 61398, solo re-probe PID
67485 — its ffmpeg child exited with it).
**Collection:** **4,182 tests** in `backend/tests/unit` (264 files) =
537d55e's 4,156 **+26**, reconciled exactly in §2.

## 0. Verdict: GATE PASS — zero product failures

**Backend unit: 4,160 passed / 0 failed / 15 skipped / 7 xfailed**
(= 4,182 collected) — after the environmental re-probe in §3. In-chunk,
Q1 showed 3 failures; all three are the missing `tesseract` OCR binary
in the sandbox (see §3), and all three PASS solo once the binary is
installed. No other chunk failed anything. The 15 skips and 7 xfails
are all expected, named, and identical to 537d55e (§4, §5).
**Frontend: 846 passed / 14 skipped / 0 failed tests** (4 failed files =
the baseline PGlite `beforeAll` hook timeouts, zero failed tests) —
identical outcome to 537d55e, 06ff0d4, 39c7437 and 6362cfe.
**TSC: 11 errors, error set byte-identical to the baseline.**
**ESLint: exit 0, zero problems** (`src` scope and whole-directory
scope both). **Migrations: 47 files, zero semicolon-in-comment
violations, set identical to 537d55e. Bridge 61ad303: 4/4
byte-identical. Smoke: 159 routes / 133 distinct paths, output
byte-identical to 537d55e.** SQL-exec family (Nov 17 gate item):
**123 passed / 12 skipped / 7 xfailed** — exactly the
DELETE_INVENTORY_REPORT expectation. Selection-eval harness tests:
**19/19 passed**. Non-unit backend scope: 12 passed / 220
environment-gated skipped / 0 failed — identical to 537d55e.

Nothing was edited, committed, pushed, uploaded or merged by this
lane; `jcs-rc`, frozen package ZIPs and Luffy's machine untouched. The
snapshot worktree, the `node_modules` symlink and the scratch TMPDIR
were all removed after the run. One `.drawtext-23975` test-artifact dir
was left inside the snapshot backend by Q2's run; it vanished with the
worktree removal (no such dir in the live tree). The one deliberate
environment change: `tesseract-ocr` 5.3.4 installed system-wide in this
sandbox VM via apt (see §3) — a test dependency, not a repo change.

## 1. Tip stability

`jcs-next` was verified clean at `54c8b8a` before the first chunk; the
live tip then moved to `77dbbde` (SIGLIP merge) during setup and to
`be00fd2` (split-border-trim merge) mid-gate. The detached snapshot at
`54c8b8a` was verified unchanged at every check, and all runs —
backend chunks, family runs, solo re-probes, frontend, tsc, eslint,
smoke — executed inside it. The two mid-gate merges are NOT covered by
this gate; they are the next gate's subject.

## 2. Backend chunk results (tests/unit)

| Chunk | Files | Range | Result | Duration | Log |
|---|---|---|---|---|---|
| Q1 | 66 | test_affiliate_email – test_clip_export_progress | 1072 passed / 3 failed (env, §3) | 425.51 s | `gate-logs-54c8b8a/chunk-q1.txt` |
| Q2 | 66 | test_clip_openings – test_lut | 994 passed / 0 failed | 690.21 s | `gate-logs-54c8b8a/chunk-q2.txt` |
| Q3 | 66 | test_lut_look – test_source_codec_probe | 1011 passed / 2 skipped / 0 failed | 698.88 s | `gate-logs-54c8b8a/chunk-q3.txt` |
| Q4 | 66 | test_source_limits – test_youtube_video_service_cleanup | 1080 passed / 13 skipped / 7 xfailed / 0 failed | 234.64 s | `gate-logs-54c8b8a/chunk-q4.txt` |
| **Total** | **264** | | **4157 passed / 3 failed / 15 skipped / 7 xfailed** (in-chunk) | **~34.2 min** | |

Chunk totals sum exactly to the 4,182 collected
(1075 + 994 + 1013 + 1100 = 4,182).

**Reconciliation of +26 vs 537d55e (4,156), closed arithmetically:**
exactly the 4 NEW files contribute all 26 — verified by solo
`--collect-only` on the four files (`solo-new-files.txt`): 26
collected, named 1:1 — `test_face_text_guard.py` 10,
`test_caption_face_guard.py` 9, `test_reframe_face_boxes.py` 4,
`test_caption_face_guard_render.py` 3. Per-chunk deltas vs 537d55e
are composition effects of the file-count quarters (the 4 new files
sit in Q1 ×2, Q2 ×1, Q3 ×1 and push one file each across the later
boundaries): Q1 1089→1075 (−14), Q2 984→994 (+10), Q3 1022→1013
(−9), Q4 1061→1100 (+39); the sum is +26 exactly. Passed delta:
4,134 + 26 = 4,160 effective ✓ (after the §3 re-probe). Skips: 15,
unchanged. Xfails: 7, unchanged.

**Chunk durations note:** Q2–Q3 ran slower than 537d55e (690 vs 648 s,
699 vs 757 s — Q3 actually faster; Q1 426 vs 502 s faster, Q4 235 vs
228 s). The SIGLIP lane had already finished by gate start, so no
concurrent lane explains the variance; the cell simply shares load
with main's merges. No stall, no OOM.

**WATCH item:** `test_range_seek` (Q3) PASSED in-chunk again this run
— Q3 has 0 failed with the same file set, so the known probe-state
flake did not recur; no solo reproduction attempted, per the brief.

## 3. The 3 Q1 failures — environmental, NOT a face-overlap regression

All three failures are in the merge's own render suite, and all three
die at the same place — before any overlap assertion runs:

- `tests/unit/test_caption_face_guard_render.py::test_single_caption_render_moves_off_face`
- `tests/unit/test_caption_face_guard_render.py::test_split_title_and_caption_render_clear_faces`
- `tests/unit/test_caption_face_guard_render.py::test_japan_split_render_keeps_text_off_real_faces`

**Trace (identical in all three):** the test's `_ocr_words` helper
shells out to `tesseract` (`["tesseract", png, "stdout", "--psm",
"11", "tsv"]`); the binary does not exist in this sandbox, so pytest
raises `FileNotFoundError: [Errno 2] No such file or directory:
'tesseract'` (`chunk-q1.txt` lines 18, 276, 480). No rendered frame
was ever OCR-measured; no face/text overlap was asserted or failed.
The test file carries NO skip guard for a missing `tesseract` binary
(only a `skipif` for the absent Japan fixture) — so on a tesseract-less
machine these three tests fail instead of skipping.

**Adjudication vs 537d55e:** NEW test names (the file did not exist at
537d55e), traceable to the face-overlap merge's render suite — but the
cause is the sandbox environment, not the merge's product code: the
merge's non-render tests (23 of the 26) all pass in-chunk, and the
render tests pass once the binary exists (below). This is NOT a
genuine face-overlap regression, so the brief's STOP clause does not
trigger; the gate continued.

**Resolution (environment setup, no repo change):** installed
`tesseract-ocr` 5.3.4 via apt (`sudo apt-get install -y
tesseract-ocr`, INSTALL_RC=0; `apt-get update` warned one mirror
InRelease failed to fetch but the install succeeded from the other
indexes). Then re-ran the render file solo in the snapshot
(`solo-render-reprobe.txt`): **3 passed in 274.78s, PYTEST_EXIT=0**
— the single-caption, split title+caption, and Japan-split render
proofs all PASS with OCR available. The face-overlap render proof is
genuine: text measurably moves off faces in the BEFORE/AFTER renders.

**Effective Q1:** 1075 passed / 0 failed. **Effective backend:**
4,160 passed / 0 failed / 15 skipped / 7 xfailed.

**Follow-up (for main, not this gate):** the render suite hard-depends
on the `tesseract` system binary. Either the suite gains a
`skipif(shutil.which("tesseract") is None)` guard (a test-only change),
or `tesseract-ocr` is recorded as a required sandbox gate dependency —
otherwise every future gate on a fresh sandbox fails these same 3
tests. The binary is now installed in this VM, so later runs here pass.

## 4. The 15 skips — all named, all expected

12 are the Postgres-tier opt-in skips (no `SQL_EXEC_PG_URL` set in
this gate, per the brief — the tier is env-gated, never failed):
8 in `test_sql_exec_postgres_tier.py`, 4 in
`test_sql_exec_postgres_deletes.py`. The remaining 3 are the baseline
set, unchanged in count and chunk distribution since 6362cfe (Q3 2,
Q4 1), confirmed by name in a solo `-rs` run
(`gate-logs-54c8b8a/solo-skips.txt`: 16 passed, 3 skipped):

| Test | Reason |
|---|---|
| `test_separation_hallucination.py:137` | real-model hallucination probe is opt-in (`SEPARATION_HALLUCINATION_TEST=1`, demucs + faster-whisper required) |
| `test_separation_quality.py:104` | real-model separation proof is opt-in (`SEPARATION_QUALITY_TEST=1`) |
| `test_voiceover.py:185` `test_synthesis_writes_a_cached_wav` | Kokoro package/model not present in the production model cache |

## 5. SQL-exec family — Nov 17 gate item (IN-CHUNK + named check)

The family files ran inside the backend chunks (creation suites in Q4,
UPDATE/deletes/guard in Q4) and additionally as one named check
(`gate-logs-54c8b8a/sql-exec-family.txt`): **123 passed / 12 skipped /
7 xfailed in 13.89s** — exactly the report's family-default
expectation (123/12/7), identical to 537d55e and 06ff0d4. The 7 xfails
are the same set as 537d55e, verified by name from the verbose run
(`sql-exec-family-xfails.txt`, 123/12/7 in 16.66s):

| File | Passed | Skipped | Xfailed |
|---|---|---|---|
| test_sql_exec_creation_core.py | 16 | 0 | 1 — clip_exports INSERT, `make_interval(secs => :ttl_seconds)` |
| test_sql_exec_creation_misc.py | 13 | 0 | 0 |
| test_sql_exec_creation_social.py | 7 | 0 | 0 |
| test_sql_exec_update_core.py | 36 | 0 | 1 — drain_deletions, `make_interval` |
| test_sql_exec_update_monitors.py | 16 | 0 | 2 — fail_stale_publishing (`make_interval`), claim_due_scheduled_posts (`FOR UPDATE SKIP LOCKED`) |
| test_sql_exec_update_methods.py | 12 | 0 | 1 — fail_stale_running, `make_interval` |
| test_sql_exec_deletes.py | 15 | 0 | 2 — purge_failures (`make_interval(days =>)`), admin delete_keys (`ANY(text[])`) |
| test_sql_exec_postgres_tier.py | 0 | 8 (env-gated) | 0 |
| test_sql_exec_postgres_deletes.py | 0 | 4 (env-gated) | 0 |
| test_task_insert_columns.py | 2 | 0 | 0 |
| test_sql_inventory_guard.py | 6 | 0 | 0 |
| **Total** | **123** | **12** | **7** |

Named xfails (7): `test_clip_export_insert_executes`,
`test_media_cleanup_drain_deletions_update_executes`,
`test_social_fail_stale_publishing_executes`,
`test_social_claim_due_scheduled_posts_executes`,
`test_fail_stale_running_method_executes`,
`test_clip_export_purge_failures_executes`,
`test_admin_delete_keys_executes` — all Postgres-only, all strict.

**`test_task_insert_columns.py` passes in-chunk** (Q4, 0 failed) and
in the named family run (2/2, both named in the verbose log).
**Inventory guard 6/6** (6 PASSED lines in the verbose log).
Canonical AST counts, pinned in the guard
(`CANONICAL_INSERT_COUNT = 38`, `CANONICAL_UPDATE_COUNT = 68`,
`CANONICAL_DELETE_COUNT = 17`) and asserted by its 6 passing tests:
**38 INSERT / 68 UPDATE / 17 DELETE, 0 uncovered across all 123
inventoried statements.** The Postgres tier tests are recorded as
skipped (no `SQL_EXEC_PG_URL`); no cluster was stood up by this gate,
per the brief — the lanes already proved the tier live
(POSTGRES_TIER_REPORT: 8/8; DELETE_INVENTORY_REPORT: 4/4, 12/12
combined). The 7 xfails are exactly the expected set (1 INSERT + 4
UPDATE + 2 DELETE, all Postgres-only).

## 6. Non-unit backend scope (extra, outside gate scope)

`tests/integration` + `tests/live` + `tests/test_video_utils_diarization.py`
(232 collected): **12 passed / 220 skipped / 0 failed** (1.42 s) —
`gate-logs-54c8b8a/chunk-q5-nonunit.txt`. Identical to 537d55e.
Selection-eval harness (`tools/selection-eval/tests`, 19 collected,
run from the repo root — the first attempt ran from `backend/` and
found no tests, a harness-path mistake in this gate's script, not a
product signal; the corrected run is `chunk-q6-selection-eval.txt`):
**19 passed / 0 failed**.

## 7. Face-overlap specific (gate item §7)

- **Unit guard tests green in-chunk:** `test_face_text_guard` (10),
  `test_caption_face_guard` (9), `test_reframe_face_boxes` (4) all
  pass in Q1/Q2/Q3 with 0 failed — the placement policy, caption/title
  wiring, and stamped face-box envelopes behave as the lane specified.
- **Render proof green solo:** the 3 render tests pass with tesseract
  present (§3) — the BEFORE/AFTER renders measurably move text off
  faces on synthetic single, synthetic split, and real Japan-split
  frames. No overlap assertion failed anywhere.
- **Neighbouring behaviour untouched:** the merge touches only the
  caption/reframe path; Q1–Q4 show no failure in any neighbouring
  suite (captions, reframe, video_utils consumers all green).
- **Golden still green:** the re-pinned short-video request-shape
  golden passes solo (23 passed incl. the 22 brain-upgrade tests,
  `solo-golden.txt`, 4.09 s).

## 8. Frontend: full vitest (snapshot worktree @ 54c8b8a)

**846 passed / 14 skipped / 0 failed tests** (860 total; 134 files:
130 passed, 4 failed). The 4 failed files are exactly the standing
baseline: `src/server/affiliates.integration.test.ts`,
`src/server/announcements.integration.test.ts`,
`src/server/service-status.integration.test.ts`,
`src/server/user-acquisition.integration.test.ts` — all PGlite
`beforeAll` hook timeouts ("Hook timed out in 10000ms", environment-
bound, zero failed tests inside them). Outcome identical to the
537d55e, 06ff0d4, 39c7437 and 6362cfe gates. Run via a temporary symlink
of `~/workspace/jcs-rc/frontend/node_modules` after confirming
`package.json` + `pnpm-lock.yaml` are byte-identical between the
snapshot and `jcs-rc` (re-verified immediately before linking);
symlink removed after. Run: `vitest run --testTimeout=30000`, 292.29 s.
Log: `gate-logs-54c8b8a/frontend-vitest.txt`. (Vitest exit code 1 is
solely the 4 environment-bound failed files, as in every prior gate.)

## 9. TSC: `--noEmit` (snapshot worktree @ 54c8b8a)

**11 errors, error set byte-identical to the baseline** (`diff` of the
sorted error lines vs `gate-logs-537d55e/tsc.txt` and vs
`gate-logs-06ff0d4/tsc.txt`: both empty): 2× `TS2307` missing
`../generated/prisma` (`e2e/global-setup.ts`, `src/lib/prisma.ts`),
8× `TS7006` implicit-`any` parameters (`admin/page.tsx` ×7,
`status/page.tsx` ×1), 1× `TS2322` `ReactNode` (`admin/page.tsx:438`).
Zero new errors. Log: `gate-logs-54c8b8a/tsc.txt`.

## 10. ESLint (gate item since the v3 install failure)

**Exit 0, zero problems** — run twice: `eslint src --max-warnings 0`
(the package lint scope, the one the production build exercises) and
`eslint . --max-warnings 0` (whole frontend directory). Both logs
contain only the exit line.
Logs: `gate-logs-54c8b8a/eslint.txt`, `gate-logs-54c8b8a/eslint-full.txt`.

## 11. Migrations

`backend/src/migrations/sql/` at `54c8b8a` holds **47 files** — the same
set as 537d55e (list: `gate-logs-54c8b8a/migrations-54c8b8a.txt`;
`diff` vs the 537d55e list: empty): dual `0002`s on 20261109 AND
20261110, single `0006`, `20261111_0001` + `20261111_0002`.
**Zero semicolon-in-comment violations** under both scans (line
comments `-- … ;` and block comments `/* … ; … */`). The
`test_migration_files_safety.py` test passed inside Q3 and solo
(1 passed, `gate-logs-54c8b8a/solo-migration-safety.txt`). PASS.

## 12. Bridge check (61ad303)

All 4 reference-styles proxy files byte-identical (sha256) to commit
`61ad303`:
`frontend/src/app/api/reference-styles/route.ts`,
`frontend/src/app/api/reference-styles/route.test.ts`,
`frontend/src/app/api/reference-styles/[...path]/route.ts`,
`frontend/src/app/api/reference-styles/[...path]/route.test.ts`. PASS.

## 13. Smoke: create_app() (snapshot worktree @ 54c8b8a)

**159 routes / 133 distinct paths** — the full output (methods census +
every surface spot count + channel-monitor path list) is byte-identical
to the 537d55e gate's `smoke.txt` (the only diff line is this run's
`SMOKE_EXIT=0` trailer). Methods: GET 76, POST 55, DELETE 16,
PATCH 8, PUT 4, HEAD 4. Script: `gate-logs-54c8b8a/smoke.py` (copied
unchanged from the 537d55e gate); output:
`gate-logs-54c8b8a/smoke.txt`. PASS.

## 14. Follow-ups this gate hands forward

1. **Next gate target is `be00fd2`, not `54c8b8a`:** two merges landed
   mid-gate — SIGLIP vision veto (`77dbbde`, flag-gated off) and
   split-border trim (`be00fd2`, reframe + render proof). A full gate
   on `be00fd2` is the next verification step; the SIGLIP lane's own
   report already claims its suites green, but the merged tip is
   unproven until gated.
2. **Tesseract dependency (§3):** decide the guard-vs-provisioning
   question for `test_caption_face_guard_render.py` before the next
   fresh-sandbox gate; this VM now has tesseract 5.3.4, so runs here
   are unaffected.
3. **Nov 17 gate:** the 7 xfails + 12 PG skips are the known
   Postgres-tier boundary, proven live by the lanes. A Nov 17 gate run
   with a scratch cluster (POSTGRES_TIER_REPORT recipe) and
   `SQL_EXEC_PG_URL` set should turn the 12 skips into passes; the 7
   SQLite xfails remain by design (they document the SQLite limit).
   Not required for this verdict.
4. `test_range_seek` remains a WATCH item (passes in-chunk, fails
   standalone on probe-state sensitivity — pre-existing, not a
   regression). It passed in-chunk here; no action.
5. **Face-overlap live leg stays user-machine pending:** the render
   proof is synthetic + Japan-split frames in the sandbox; Luffy's own
   Bert clip re-render on his installed build is the user-machine
   proof that the caption no longer crosses the mouth/chin and the
   title clears the split-screen eyes.
6. The tip is package-clean on every gate item (backend, frontend,
   tsc, eslint, migrations, bridge, smoke, SQL-exec family,
   face-overlap unit + render proof). **54c8b8a is gate-green for the
   Nov 17 formalisation.** The next packaging / V4 decision is main's,
   now with this verdict in hand.
