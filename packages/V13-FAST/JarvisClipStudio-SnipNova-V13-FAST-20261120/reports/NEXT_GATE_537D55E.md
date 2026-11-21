# Full-Suite Gate — next-milestone @ 537d55e

**Gate date:** 2026-11-11 (run ~16:00–17:05 IST; backend + frontend + tsc +
eslint + migrations + bridge + smoke, all on one settled tip)
**Tree gated:** `~/workspace/jcs-next`, branch `next-milestone`, tip
`537d55e` (`merge: brain upgrade — reasoning high on final passes, gated
rubric, context+scenes+few-shot (feature-brain-upgrade)`). Carries since
the last full gate @ `06ff0d4`: exactly the 9-commit brain upgrade
(`3ec8929`..`17f9efd`); the diff `06ff0d4..537d55e` is 9 files, +797/−41
— product code `backend/src/ai.py` (+314), `video_service.py` (+24),
`litellm_config.yaml` (+4, reasoning_effort allow-list), 1 new test file
(`test_brain_upgrade.py`, 22 tests), 3 re-derived test files, CHANGELOG.
No migration, no frontend, no config beyond the gateway allow-list.
**Method:** EVERYTHING ran in a detached snapshot worktree at exactly
`537d55e` (`~/workspace/jcs-gate537d55e-snap`, created before the first
chunk, removed after the gate) — the live tip never moved during this
run (verified `537d55e` + clean at start, between chunks, and at end),
so all evidence is byte-pure `537d55e` with no attribution argument
needed. No mid-gate merge landed; no per-chunk attribution or
snapshot-confirmed re-run was required.
**Venv:** `~/workspace/jcs-modes/backend/.venv` (lockfile venv; cv2
4.13.0 — no new deps installed). **Env:** `OMP_NUM_THREADS=2`, serial
chunks, `TMPDIR` pointed at a workspace scratch dir
(`~/workspace/gate-tmp-537d55e`, removed after — never /tmp). The
bracketed-IPv6 `no_proxy` entries were stripped (known httpx InvalidURL
artefact). Every chunk's output went to a log file under
`gate-logs-537d55e/` and was read from disk; pytest processes that
lingered after printing their summary (the known interpreter-exit hang)
were killed by exact PID only after the summary was on disk (Q1 PID
8591, Q2 PID 20871, Q3 PID 32574, Q4 PID 56937).
**Collection:** **4,156 tests** in `backend/tests/unit` (260 files) =
06ff0d4's 4,134 **+22**, reconciled exactly in §2.

## 0. Verdict: GATE PASS — zero failures

**Backend unit: 4,134 passed / 0 failed / 15 skipped / 7 xfailed**
(= 4,156 collected). The re-pinned short-video request-shape golden
passes in-chunk and solo (§3). The 15 skips and 7 xfails are all
expected, named, and identical to 06ff0d4 (§4, §5).
**Frontend: 846 passed / 14 skipped / 0 failed tests** (4 failed files =
the baseline PGlite `beforeAll` hook timeouts, zero failed tests) —
identical outcome to 06ff0d4, 39c7437 and 6362cfe.
**TSC: 11 errors, error set byte-identical to the baseline.**
**ESLint: exit 0, zero problems** (`src` scope and whole-directory
scope both). **Migrations: 47 files, zero semicolon-in-comment
violations, set identical to 06ff0d4. Bridge 61ad303: 4/4
byte-identical. Smoke: 159 routes / 133 distinct paths, output
byte-identical to 06ff0d4.** SQL-exec family (Nov 17 gate item):
**123 passed / 12 skipped / 7 xfailed** — exactly the
DELETE_INVENTORY_REPORT expectation. Selection-eval harness tests:
**19/19 passed**. Non-unit backend scope: 12 passed / 220
environment-gated skipped / 0 failed — identical to 06ff0d4.
Brain-upgrade specific (§7): the re-pinned golden passes in-chunk and
solo; `reasoning_effort: high` rides only the final premium pass
(spot-checked in the passing tests, no live LLM call).

Nothing was edited, committed, pushed, uploaded or merged by this
lane; `jcs-rc`, frozen package ZIPs and Luffy's machine untouched. The
snapshot worktree, the `node_modules` symlink and the scratch TMPDIR
were all removed after the run. No `.drawtext-*` test-artifact dirs
were left behind.

## 1. Tip stability

`jcs-next` was verified clean at `537d55e` before the first chunk, again
after Q1, Q2 and Q3, and again after the last check. No merge landed
during this gate. All runs — backend chunks, family runs, frontend,
tsc, eslint, smoke — executed inside the detached snapshot at
`537d55e`.

## 2. Backend chunk results (tests/unit)

| Chunk | Files | Range | Result | Duration | Log |
|---|---|---|---|---|---|
| Q1 | 66 | test_affiliate_email – test_clip_qc | 1089 passed / 0 failed | 502.35 s | `gate-logs-537d55e/chunk-q1.txt` |
| Q2 | 65 | test_clip_qc_pipeline – test_media_font_deletion | 984 passed / 0 failed | 647.71 s | `gate-logs-537d55e/chunk-q2.txt` |
| Q3 | 65 | test_media_replication – test_source_submission | 1020 passed / 2 skipped / 0 failed | 756.77 s | `gate-logs-537d55e/chunk-q3.txt` |
| Q4 | 64 | test_speaker_framing – test_youtube_video_service_cleanup | 1041 passed / 13 skipped / 7 xfailed / 0 failed | 227.82 s | `gate-logs-537d55e/chunk-q4.txt` |
| **Total** | **260** | | **4,134 passed / 0 failed / 15 skipped / 7 xfailed** | **~35.6 min** | |

Chunk totals sum exactly to the 4,156 collected
(1089 + 984 + 1022 + 1061 = 4,156).

**Reconciliation of +22 vs 06ff0d4 (4,134), closed arithmetically:**
exactly one NEW file contributes all 22 — `test_brain_upgrade.py`
(verified by solo `--collect-only`-equivalent count: 22 collected,
22 passed, `solo-brain-upgrade.txt`; and by `diff` of the unit file
lists, which shows only that one addition). The three re-derived test
files (`test_ai_long_video_resilience.py`, `test_ai_rate_limit.py`,
`test_target_clip_count.py`, `test_litellm_gateway_config.py`) change
no test counts. Passed delta: 4,112 + 22 = 4,134 ✓. Skips: 15,
unchanged. Xfails: 7, unchanged. Per-chunk: Q1 1067→1089 (+22, the new
file sits alphabetically in Q1's range); Q2 984→984, Q3 1020/2→1020/2,
Q4 1041/13/7→1041/13/7 — all identical.

**Chunk durations note:** Q1–Q3 ran markedly slower than 06ff0d4
(502 vs 308 s, 648 vs 616 s, 757 vs 615 s; Q4 228 vs 221 s). Cause:
the chat-owned caption/title face-overlap lane was running its own
pytest sweeps in `~/workspace/jcs-facecaption` concurrently on the
shared 1.5-CPU cell (its final sweep 86 passed, its light sweep 81
passed — both observed in its own logs, never touched by this gate),
and its pytest processes lingered in the known exit-hang holding
~550–685 MB each. No OOM, no stall; the gate simply shared the cell.
Not a product signal.

**WATCH item:** `test_range_seek` (Q3) PASSED in-chunk this run —
Q3's totals are identical to 06ff0d4 (1020 passed / 2 skipped /
0 failed) with the same file set, so the known probe-state flake did
not recur; no solo reproduction attempted, per the brief.

## 3. The re-pinned golden — PASSES in-chunk and solo

`tests/unit/test_ai_long_video_resilience.py::test_short_video_request_shape_is_unchanged`
— the golden re-pinned by the brain-upgrade lane (`6790e76`) to the new
digest `9e4756151b9bc48322ec253dc97448323cb99525e93b66eed37348a8af466105`
(BRAIN_UPGRADE_REPORT §"Golden re-derivation", every body difference
attributed to the four brain-upgrade commits, no remainder).

- **In-chunk:** Q1 ran 0 failed across its 66 files, which include
  `test_ai_long_video_resilience.py` and the new `test_brain_upgrade.py`.
- **Solo:** `10 passed in 31.26s` (`gate-logs-537d55e/solo-golden.txt`),
  the golden test named in the `-v` output. Deterministic, no flake.

**No new failure names:** there are zero failures in this gate, so
there is nothing to compare against the 06ff0d4 gate (which also had
zero failures). No failure was reproduced, traced, or left open —
there is none to trace.

## 4. The 15 skips — all named, all expected

12 are the Postgres-tier opt-in skips (no `SQL_EXEC_PG_URL` set in
this gate, per the brief — the tier is env-gated, never failed):
8 in `test_sql_exec_postgres_tier.py`, 4 in
`test_sql_exec_postgres_deletes.py`. The remaining 3 are the baseline
set, unchanged in count and chunk distribution since 6362cfe (Q3 2,
Q4 1), confirmed by name in a solo `-rs` run
(`gate-logs-537d55e/solo-skips.txt`: 16 passed, 3 skipped):

| Test | Reason |
|---|---|
| `test_separation_hallucination.py:137` | real-model hallucination probe is opt-in (`SEPARATION_HALLUCINATION_TEST=1`, demucs + faster-whisper required) |
| `test_separation_quality.py:104` | real-model separation proof is opt-in (`SEPARATION_QUALITY_TEST=1`) |
| `test_voiceover.py:185` `test_synthesis_writes_a_cached_wav` | Kokoro package/model not present in the production model cache |

## 5. SQL-exec family — Nov 17 gate item (IN-CHUNK + named check)

The family files ran inside the backend chunks (creation suites in Q4,
UPDATE/deletes/guard in Q4) and additionally as one named check
(`gate-logs-537d55e/sql-exec-family.txt`): **123 passed / 12 skipped /
7 xfailed in 8.29s** — exactly the report's family-default
expectation (123/12/7), identical to 06ff0d4. The 7 xfails are the
same set as 06ff0d4, each with its documented Postgres-only reason
(marker text read from the test files; the `-rX` summary section did
not print in this pytest invocation, so the names below are the
`@pytest.mark.xfail(strict=True)` markers, verified 1:1 against the
06ff0d4 `sql-exec-family-xfails.txt` list):

| File | Collected | Passed | Skipped | Xfailed |
|---|---|---|---|---|
| test_sql_exec_creation_core.py | 17 | 16 | 0 | 1 — clip_exports INSERT, `make_interval(secs => :ttl_seconds)` |
| test_sql_exec_creation_misc.py | 13 | 13 | 0 | 0 |
| test_sql_exec_creation_social.py | 7 | 7 | 0 | 0 |
| test_sql_exec_update_core.py | 37 | 36 | 0 | 1 — drain_deletions, `make_interval` |
| test_sql_exec_update_monitors.py | 18 | 16 | 0 | 2 — fail_stale_publishing (`make_interval`), claim_due_scheduled_posts (`FOR UPDATE SKIP LOCKED`) |
| test_sql_exec_update_methods.py | 13 | 12 | 0 | 1 — fail_stale_running, `make_interval` |
| test_sql_exec_deletes.py | 17 | 15 | 0 | 2 — purge_failures (`make_interval(days =>)`), admin delete_keys (`ANY(text[])`) |
| test_sql_exec_postgres_tier.py | 8 | 0 | 8 (env-gated) | 0 |
| test_sql_exec_postgres_deletes.py | 4 | 0 | 4 (env-gated) | 0 |
| test_task_insert_columns.py | 2 | 2 | 0 | 0 |
| test_sql_inventory_guard.py | 6 | 6 | 0 | 0 |
| **Total** | **142** | **123** | **12** | **7** |

Named xfails (7): `test_clip_export_insert_executes`,
`test_media_cleanup_drain_deletions_update_executes`,
`test_social_fail_stale_publishing_executes`,
`test_social_claim_due_scheduled_posts_executes`,
`test_fail_stale_running_method_executes`,
`test_clip_export_purge_failures_executes`,
`test_admin_delete_keys_executes` — all Postgres-only, all strict.

**`test_task_insert_columns.py` passes in-chunk** (Q4, 0 failed) and
in the named family run (2/2, visible as `..` in the per-file verbose
run `sql-exec-family-xfails.txt`). **Inventory guard 6/6** (visible as
`......` in the same run). Canonical AST counts, pinned in the guard
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
(232 collected): **12 passed / 220 skipped / 0 failed** (1.68 s) —
`gate-logs-537d55e/chunk-q5-nonunit.txt`. Identical to 06ff0d4.
Selection-eval harness (`tools/selection-eval/tests`, 19 collected):
**19 passed / 0 failed** —
`gate-logs-537d55e/chunk-q6-selection-eval.txt`.

## 7. Brain-upgrade specific (gate item §7)

- **Re-pinned golden:** PASSES in-chunk (Q1) and solo (§3).
  The new pin is the lane's re-derived digest; the body diff is fully
  attributed in BRAIN_UPGRADE_REPORT (reasoning_effort field, rubric/
  few-shot system prompt, `category` key + schema property).
- **`reasoning_effort: high` only on the final premium pass
  (spot-check, no live LLM call — the sandbox has no provider route;
  the live call stays user-machine pending):** the two body tests
  passed in-chunk (Q1) and solo (22/22, `solo-brain-upgrade.txt`).
  Read from the test file: `test_short_video_premium_body_carries_everything`
  asserts the short-video request body carries
  `reasoning_effort == "high"` (line 421); `test_long_video_volume_bodies_stay_default_rerank_is_premium`
  asserts `reasoning_effort` is absent from every volume-scan body
  (line 453) and present with value `"high"` on the rerank body
  (line 456). The degrade paths (both refusal shapes) and the
  no-retry-on-unrelated-errors test also pass.
- **Neighbouring re-derivations green:** `test_ai_rate_limit` (chunk-
  rotation counts re-derived for the +12-char prompt growth),
  `test_target_clip_count` (+3 over-ask), `test_litellm_gateway_config`
  (reasoning_effort allow-list pins) — all inside Q1/Q4 with 0 failed.

## 8. Frontend: full vitest (snapshot worktree @ 537d55e)

**846 passed / 14 skipped / 0 failed tests** (860 total; 134 files:
130 passed, 4 failed). The 4 failed files are exactly the standing
baseline: `src/server/affiliates.integration.test.ts`,
`src/server/announcements.integration.test.ts`,
`src/server/service-status.integration.test.ts`,
`src/server/user-acquisition.integration.test.ts` — all PGlite
`beforeAll` hook timeouts ("Hook timed out in 10000ms", environment-
bound, zero failed tests inside them). Outcome identical to the
06ff0d4, 39c7437 and 6362cfe gates. Run via a temporary symlink of
`~/workspace/jcs-rc/frontend/node_modules` after confirming
`package.json` + `pnpm-lock.yaml` are byte-identical between the
snapshot and `jcs-rc` (re-verified immediately before linking);
symlink removed after. Run: `vitest run --testTimeout=30000`, 254.16 s.
Log: `gate-logs-537d55e/frontend-vitest.txt`. (Vitest exit code 1 is
solely the 4 environment-bound failed files, as in every prior gate.)

## 9. TSC: `--noEmit` (snapshot worktree @ 537d55e)

**11 errors, error set byte-identical to the baseline** (`diff` of the
sorted error list vs `gate-logs-39c7437/tsc.txt`: empty; also identical
to `gate-logs-06ff0d4/tsc.txt`): 2× `TS2307` missing
`../generated/prisma` (`e2e/global-setup.ts`, `src/lib/prisma.ts`),
8× `TS7006` implicit-`any` parameters (`admin/page.tsx` ×7,
`status/page.tsx` ×1), 1× `TS2322` `ReactNode` (`admin/page.tsx:438`).
Zero new errors. Log: `gate-logs-537d55e/tsc.txt`.

## 10. ESLint (gate item since the v3 install failure)

**Exit 0, zero problems** — run twice: `eslint src --max-warnings 0`
(the package lint scope, the one the production build exercises) and
`eslint . --max-warnings 0` (whole frontend directory). Both logs
contain only the exit line. The `b555eca` lesson holds: no
`react/no-unescaped-entities` recurrence and no other lint offender
anywhere in the frontend.
Logs: `gate-logs-537d55e/eslint.txt`, `gate-logs-537d55e/eslint-full.txt`.

## 11. Migrations

`backend/src/migrations/sql/` at `537d55e` holds **47 files** — the same
set as 06ff0d4 (list: `gate-logs-537d55e/migrations-537d55e.txt`;
`diff` vs the 06ff0d4 list: empty): dual `0002`s on 20261109 AND
20261110, single `0006`, `20261111_0001` + `20261111_0002`.
**Zero semicolon-in-comment violations** under both scans (line
comments `-- … ;` and block comments `/* … ; … */`). The
`test_migration_files_safety.py` test passed inside Q3 and solo
(1 passed, `gate-logs-537d55e/solo-migration-safety.txt`). PASS.

## 12. Bridge check (61ad303)

All 4 reference-styles proxy files byte-identical (sha256) to commit
`61ad303`:
`frontend/src/app/api/reference-styles/route.ts`,
`frontend/src/app/api/reference-styles/route.test.ts`,
`frontend/src/app/api/reference-styles/[...path]/route.ts`,
`frontend/src/app/api/reference-styles/[...path]/route.test.ts`. PASS.

## 13. Smoke: create_app() (snapshot worktree @ 537d55e)

**159 routes / 133 distinct paths** — the full output (methods census +
every surface spot count + channel-monitor path list) is byte-identical
to the 06ff0d4 gate's `smoke.txt` (the only diff line is this run's
`SMOKE_EXIT=0` trailer). Methods: GET 76, POST 55, DELETE 16,
PATCH 8, PUT 4, HEAD 4. Script: `gate-logs-537d55e/smoke.py` (copied
unchanged from the 06ff0d4 gate); output:
`gate-logs-537d55e/smoke.txt`. PASS.

## 14. Follow-ups this gate hands forward

1. **Nov 17 gate:** the 7 xfails + 12 PG skips are the known
   Postgres-tier boundary, proven live by the lanes. A Nov 17 gate run
   with a scratch cluster (POSTGRES_TIER_REPORT recipe) and
   `SQL_EXEC_PG_URL` set should turn the 12 skips into passes; the 7
   SQLite xfails remain by design (they document the SQLite limit).
   Not required for this verdict.
2. `test_range_seek` remains a WATCH item (passes in-chunk, fails
   standalone on probe-state sensitivity — pre-existing, not a
   regression). It passed in-chunk here; no action.
3. **Brain upgrade live leg stays user-machine pending:** one real
   premium call through Luffy's shipped gateway build proving
   `reasoning_effort` is forwarded, not rejected (the degrade path
   makes a rejection harmless, but the benefit needs the live log).
   Selection quality on a real run is likewise pending his TJR proof
   run.
4. The tip is package-clean on every gate item (backend, frontend,
   tsc, eslint, migrations, bridge, smoke, SQL-exec family,
   brain-upgrade goldens). **537d55e is gate-green for the Nov 17
   formalisation.** The next packaging / V4 decision is main's, now
   with this verdict in hand.
