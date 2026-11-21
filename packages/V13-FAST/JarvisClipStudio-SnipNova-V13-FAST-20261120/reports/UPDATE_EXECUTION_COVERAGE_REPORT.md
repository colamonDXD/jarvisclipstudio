# Raw-SQL UPDATE Execution Coverage — Report

**Lane:** `test-raw-sql-update-execution` (worktree `~/workspace/jcs-updcov`),
branched off next-milestone @ `fca2f7e`. Test-only; no product code touched;
no existing test modified; **NOT merged** (main merges).
**Date:** 2026-11-11. **Continuation of:** the INSERT lane
(`SQL_EXECUTION_COVERAGE_REPORT.md`, merged @ `39c7437`), whose remaining-work
list items 1–2 this lane closes.

## Result — full UPDATE inventory accounted for

The AST inventory (same counting method as the INSERT lane: SQL literals in
`text()`/`sa_text()` calls, assignments and returns, deduplicated, docstrings
and log strings excluded) finds **68 UPDATE statements** in `backend/src` —
exactly the INSERT lane's literal count. All 68 are now accounted for:

| Status | Count | Where |
|---|---|---|
| Executed green, statement-level (extracted text, seeded row, effect + guard asserted) | 53 | `test_sql_exec_update_core.py`, `test_sql_exec_update_monitors.py` |
| Executed green, method-level (real method driven through the executing session double) | 11 | `test_sql_exec_update_methods.py` |
| Documented strict xfail (Postgres-only construct) | 4 | listed below |
| **Total** | **68** | |

By inventory class: fixed-text 50 (47 green + 3 xfail), constant-interpolated
6 (all green — the channel-monitor / watch-folder reclaim family),
runtime-assembled 12 (11 green at method level + 1 xfail).

**New tests: 68 test functions — 64 passed, 4 xfailed, 0 failed.**
Combined serial run of the whole sql-exec family plus
`test_task_insert_columns.py`: **102 passed / 5 xfailed / 0 failed** (the 5th
xfail is the INSERT lane's `clip_exports` `make_interval`). Neighbour suites
serial: channel monitor ×2 + watch folder **55/0**; brand kit + publishing
routes + social providers **83/0** (that batch's pytest process hit the known
exit-hang after printing its summary; killed only after the summary was on
disk, per protocol).

Commits: `099a7a2` (support extension + core), `6f15f92` (monitors/social),
`4d184f9` (methods). No CHANGELOG — test-only, stated in each commit body
per repo rules.

## Suspected live defects

**None.** Every executable UPDATE ran clean against migration-derived DDL,
and the guard behaviour held everywhere it was probed: already-applied
states are no-ops (revoke, completion stamp, idempotency key), wrong-owner
rows are unreachable (API keys, brand-kit detach, campaign detach),
progress never moves backwards, refunds fire only for terminal tasks with
no saved clips, and reclaim statements respect their backoff windows and
attempt ceilings (fresh rows and exhausted rows are both refused).

## Method notes

- Statement tests reuse the INSERT lane's discipline: the *real* statement
  text is extracted from source, a row is seeded in the pre-state, the
  statement executes against in-memory SQLite, and the test asserts the
  intended change (not merely the absence of an error).
- `sql_exec_support` gained three documented extensions:
  1. an interval normalisation —
     `CURRENT_TIMESTAMP - (:p * INTERVAL '1 second')` →
     `datetime(CURRENT_TIMESTAMP, '-' || :p || ' seconds')` — for the six
     reclaim statements;
  2. `GREATEST`/`LEAST` registered as SQLite scalar functions with
     Postgres NULL semantics (statement text untouched) — needed by
     `clip_exports.mark_running`;
  3. `ExecSession`, an async session double that executes the real
     (transpiled) statement on a sync SQLite connection and returns
     attribute-access rows with asyncpg-style timestamp decoding.
     aiosqlite is **not installed** in the sandbox venv, so a real
     SQLAlchemy async engine over SQLite is unavailable; the double is
     the in-sandbox route, and per-function it executes exactly the SQL
     the method assembles.
- The nine dict-driven functions were exercised with field dicts covering
  several keys each. The allowlist functions (channel monitor ×2, social
  posts, watch files) were additionally fed a non-writable key and proven
  to drop it silently, as designed; `update_task_status` was also run to
  a terminal status, which executed the minute-usage refund UPDATE
  through the same double (cross-coverage of that statement in its real
  call chain).
- The `source_metadata || CAST(:x AS JSONB)` merge statements
  (`task_repository.save_source_metadata`,
  `upload_repository.save_storage_reference`) execute here with the
  established text-concatenation approximation; true jsonb merge
  semantics remain a Postgres-tier item, as flagged by the INSERT lane.

## The four strict xfails (Postgres tier)

Each xfail is a test that executes the real statement/method and asserts
the intended effect; each was verified with `--runxfail` to fail on
SQLite for exactly the documented reason:

| Statement | Construct | Verified SQLite failure |
|---|---|---|
| `workers/media_cleanup.py :: drain_deletions` | `make_interval(secs => …)` over `power()`/`LEAST` backoff | `OperationalError: near ">"` (the `=>` call syntax is unparseable) |
| `social_repository.py :: fail_stale_publishing` | `make_interval(secs => CAST(:seconds AS integer))` | `OperationalError: near ">"` |
| `social_repository.py :: claim_due_scheduled_posts` | selection subquery ends `FOR UPDATE SKIP LOCKED` | `OperationalError: near "FOR"` |
| `clip_export_repository.py :: fail_stale_running` (method-level) | always-present clause `updated_at < NOW() - make_interval(secs => :stale_seconds)` | `OperationalError: near ">"`, raised through `ExecSession` — i.e. the assembled statement itself |

## Variant remainders (statement shape executed; one variant needs Postgres)

- `task_repository.update_task_status` with `expected_statuses=[...]`
  appends `AND status = ANY(:expected_statuses)` — a Postgres array
  operator SQLite cannot express. The base assembled statement (status +
  progress + message, plus the terminal-refund chain) is executed at
  method level; the ANY variant belongs to the Postgres tier.
- `social_repository.update_post` with a `hashtags` key binds a Python
  list to a Postgres `TEXT[]` column (`20260901_0001_social_publishing.sql`);
  SQLite has no array type to bind. The scalar keys and the
  `CAST(:metrics AS jsonb)` key are executed for real.

## Full UPDATE inventory (68 statements)

Executed green = ✅ · strict xfail = ⚠️ · `(m)` = method-level.

**api/routes/admin.py** — update_runtime_settings ✅
**repositories/api_key_repository.py** — resolve_user_id ✅ · revoke_api_key ✅
**repositories/brand_kit_repository.py** — update_kit ✅(m) · delete_kit ✅
**repositories/channel_monitor_repository.py** — update_monitor ✅(m) ·
update_video ✅(m) · reclaim_failed_video ✅ · reclaim_stale_processing_video ✅ ·
reclaim_waiting_video ✅ · reclaim_for_redrive ✅ ·
park_videos_for_missing_monitors ✅
**repositories/clip_export_repository.py** — mark_running ✅ ·
update_progress ✅ · touch ✅ · mark_succeeded ✅ · mark_failed ✅(m) ·
fail_stale_running ⚠️(m)
**repositories/clip_repository.py** — update_clip ✅ ·
update_clip_niche_override ✅ · update_clip_hook_variant ✅ ·
update_clip_qc ✅ · update_clip_compliance ✅ · make_room_for_clip ✅ ·
reorder_task_clips (tasks) ✅ · reorder_task_clips (generated_clips) ✅
**repositories/email_preference_repository.py** —
unsubscribe_from_product_updates ✅
**repositories/minute_usage_repository.py** — refund_failed_minute_usage ✅ ·
reactivate_minute_usage ✅
**repositories/publishing_repository.py** — update_campaign ✅(m) ·
update_post ✅(m) · delete_campaign ✅
**repositories/social_repository.py** — update_account_tokens ✅ ·
set_account_status ✅ · revoke_account (accounts) ✅ ·
revoke_account (posts) ✅ · claim_post ✅ · cancel_post ✅ ·
requeue_post ✅ · insert_metrics ✅ · update_post ✅(m) ·
cancel_unpublished_for_clips ✅(m) · fail_stale_publishing ⚠️ ·
claim_due_scheduled_posts ⚠️
**repositories/source_repository.py** — update_source_title ✅
**repositories/task_repository.py** — update_brief_report ✅ ·
update_classification ✅ · save_source_metadata ✅ ·
update_task_settings (full) ✅ · update_task_settings (font-only) ✅ ·
update_task_clips ✅ · enable_sharing ✅ · disable_sharing ✅ ·
mark_completion_notification_sent ✅ ·
update_task_runtime_metadata ✅(m) · update_task_status ✅(m, base shape)
**repositories/upload_repository.py** — bind_upload ✅ · finish_upload ✅ ·
_remember_idempotency_key (UPDATE) ✅ · save_storage_reference (sessions) ✅ ·
save_storage_reference (tasks) ✅
**repositories/watch_folder_repository.py** — reclaim_failed_file ✅ ·
reclaim_stale_processing_file ✅ · update_file ✅(m)
**services/ai_edit_allowance_service.py** — refund_ai_edit_usage ✅
**services/billing_service.py** — assert_can_resume_task ✅
**workers/media_cleanup.py** — _abort_expired_multipart_uploads ✅ ·
drain_deletions ⚠️

## What remains for the Nov 17 gate formalisation

1. **Postgres-backed execution tier** — the only uncovered ground left,
   all of it enumerated: the 4 UPDATE xfails above + the INSERT lane's
   `clip_exports` `make_interval` insert; the `FOR UPDATE SKIP LOCKED`
   claim sites (clip-export expiry lock, `claim_due_scheduled_posts`,
   `workers/media_cleanup.drain_deletions`'s select side);
   `pg_advisory_xact_lock` (upload reserve); true jsonb `||` merge
   semantics (task checkpoints, source metadata, social accounts); the
   `update_task_status` `ANY(:expected_statuses)` variant; the social
   `hashtags` `TEXT[]` binding. Needs a scratch Postgres in the gate
   environment (the sandbox has none).
2. **Gate items:** name the `test_sql_exec_*` suites (INSERT + UPDATE +
   methods) as a gate check alongside `test_task_insert_columns.py`,
   and add the **inventory-guard** test that fails when a new raw
   INSERT or UPDATE literal appears in `backend/src` without a
   corresponding execution test — so this coverage cannot silently
   regress again.
3. **Canonical counting:** quote the AST extractor's counts (38 INSERT
   literals / 68 UPDATE statements) in future gates, not grep counts.
