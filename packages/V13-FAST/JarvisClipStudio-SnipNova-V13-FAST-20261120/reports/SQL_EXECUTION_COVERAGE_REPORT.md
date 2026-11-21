# Raw-SQL Statement-Execution Coverage — Report

**Lane:** `test-raw-sql-execution` (worktree `~/workspace/jcs-sqlcov`), branched off
next-milestone @ `c2bce92`. Test-only; no product code touched; NOT merged.
**Date:** 2026-11-11. **Trigger:** the 6362cfe full gate found 35 of 37 raw-SQL
INSERT statements in `backend/src` had no statement-level execution test —
repository unit tests mock the DB session, which is exactly how the
duplicated-column tasks INSERT (fixed @ `47eda4a`) shipped and broke all task
creation on Luffy's real Postgres.

## Method

- AST inventory of every SQL string literal in `backend/src` (extractor walks
  parsed modules, attributes each literal to its enclosing function, and
  resolves the `RETURNING {_COLUMNS}` f-string idiom from module constants).
- Each new test extracts the **real statement text from source** (never a
  hand-copied paraphrase), normalises only the Postgres spellings SQLite
  cannot parse, and executes it against an in-memory SQLite engine (3.53.1)
  with minimal DDL **derived from the migrations** — including the exact
  UNIQUE/PK constraint behind every `ON CONFLICT` target, so a conflict
  target with no backing constraint fails here exactly as on Postgres.
- Normalisations (all in `tests/unit/sql_exec_support.py`, documented there):
  `NOW()` / `clock_timestamp()` → `CURRENT_TIMESTAMP`; `:param::type` casts
  stripped; `CAST(:x AS jsonb)` → bare `:x` (SQLite would otherwise apply
  NUMERIC affinity and destroy the JSON value; jsonb `||` *object-merge*
  semantics are approximated as text concatenation — flagged again below).
- Upserts are executed **twice** so the `ON CONFLICT DO UPDATE` /
  `DO NOTHING` path runs, not just the happy insert path.

## Result — INSERT coverage

The gate counted 37 INSERT statements; the AST inventory finds **38 INSERT
literals** (the extra one is the migration runner's own bookkeeping insert,
`database.py::init_db` → `schema_migrations`). All 38 now have
statement-level execution coverage:

| Status | Count | Detail |
|---|---|---|
| Executed green, new tests this lane | 36 | see inventory below |
| Documented strict xfail (Postgres-only) | 1 | `clip_exports` — `make_interval(secs => :ttl_seconds)` |
| Covered before this lane | 1 | `tasks` via `test_task_insert_columns.py` (@ 47eda4a); `minute_usage_events` also had a real-engine billing test and is now pinned at statement level too |

**New tests: 37 test functions, 36 passed + 1 xfailed**, in
`backend/tests/unit/test_sql_exec_creation_core.py` (17),
`test_sql_exec_creation_misc.py` (13), `test_sql_exec_creation_social.py` (7),
plus the shared `sql_exec_support.py`. Combined serial run with neighbours
(existing `test_task_insert_columns.py`, `test_channel_monitor.py`,
`test_channel_monitor_stage2.py`, `test_watch_folder.py`):
**93 passed / 1 xfailed / 0 failed**.

Commits: `915f427` (support + core), `b3e733e` (misc), `88ceaa1` (social).
Test-only changes — no CHANGELOG entry, per repo rules (stated in each
commit body). No existing test was modified.

## Suspected live defects

**None.** Every executable INSERT ran clean against migration-derived DDL,
and every `ON CONFLICT` target was verified against a real migration
constraint:

`app_settings(setting_key)` PK · `processing_cache(cache_key)` PK ·
`channel_monitor_settings(user_id)` PK · `channel_monitors(user_id,
channel_id)` UNIQUE · `channel_monitor_videos(user_id, video_id)` UNIQUE ·
`generated_clips(task_id, clip_order)` unique index
`uq_generated_clips_task_order` · `media_deletions(file_path)` PK ·
`minute_usage_events(task_id)` UNIQUE · `provider_cooldowns(alias)` PK ·
`clip_posts(clip_id, platform)` UNIQUE · `clip_post_stats(post_id)` PK ·
`social_accounts(user_id, provider, external_account_id)` unique index ·
`task_processing_checkpoints(task_id)` PK ·
`media_upload_idempotency_keys(user_id, idempotency_key)` PK ·
`watch_folder_settings(user_id)` PK · `watch_folder_files(user_id,
fingerprint)` UNIQUE.

Two findings during development were **test-harness corrections, not
product defects** (recorded so nobody re-derives them):
1. `create_clip`'s `ON CONFLICT DO UPDATE` does not list `id`, so
   `RETURNING id` on the conflict path yields the *original* row's id —
   correct semantics, now pinned by the test.
2. SQLite's `CAST(x AS jsonb)` applies NUMERIC affinity (JSON text becomes
   `0`); the support module's cast normalisation exists for this reason.

## Full INSERT inventory (38 literals)

| # | File :: function | Table | Coverage |
|---|---|---|---|
| 1 | `api/routes/admin.py` :: update_runtime_settings | app_settings | ✅ new |
| 2 | `database.py` :: init_db | schema_migrations | ✅ new |
| 3 | `repositories/api_key_repository.py` :: create_api_key | api_keys | ✅ new |
| 4 | `repositories/brand_kit_repository.py` :: create_kit | brand_kits | ✅ new |
| 5 | `repositories/cache_repository.py` :: upsert_cache | processing_cache | ✅ new |
| 6 | `repositories/channel_monitor_repository.py` :: upsert_settings | channel_monitor_settings | ✅ new |
| 7 | `repositories/channel_monitor_repository.py` :: create_monitor | channel_monitors | ✅ new |
| 8 | `repositories/channel_monitor_repository.py` :: claim_video | channel_monitor_videos | ✅ new |
| 9 | `repositories/channel_monitor_repository.py` :: insert_terminal_video | channel_monitor_videos | ✅ new |
| 10 | `repositories/clip_export_repository.py` :: insert | clip_exports | ⚠️ xfail — Postgres-only `make_interval` |
| 11 | `repositories/clip_repository.py` :: create_clip (primary) | generated_clips | ✅ new |
| 12 | `repositories/clip_repository.py` :: create_clip (42703 fallback) | generated_clips | ✅ new |
| 13 | `repositories/hook_preview_repository.py` :: replace_previews_for_clip | clip_hook_previews | ✅ new |
| 14 | `repositories/learning_repository.py` :: record_event | learning_feedback_events | ✅ new |
| 15 | `repositories/learning_repository.py` :: insert_version | learning_versions | ✅ new |
| 16 | `repositories/media_repository.py` :: queue_delete | media_deletions | ✅ new |
| 17 | `repositories/minute_usage_repository.py` :: record_minute_usage | minute_usage_events | ✅ new (also pre-existing billing real-engine test) |
| 18 | `repositories/provider_usage_repository.py` :: record_token_usage | llm_token_usage | ✅ new |
| 19 | `repositories/provider_usage_repository.py` :: upsert_cooldown | provider_cooldowns | ✅ new |
| 20 | `repositories/publishing_repository.py` :: create_campaign | campaigns | ✅ new |
| 21 | `repositories/publishing_repository.py` :: upsert_post | clip_posts | ✅ new |
| 22 | `repositories/publishing_repository.py` :: upsert_stats | clip_post_stats | ✅ new |
| 23 | `repositories/social_repository.py` :: upsert_account | social_accounts | ✅ new |
| 24 | `repositories/social_repository.py` :: create_oauth_state | social_oauth_states | ✅ new |
| 25 | `repositories/social_repository.py` :: create_post | social_posts | ✅ new |
| 26 | `repositories/social_repository.py` :: insert_metrics | social_post_metrics | ✅ new |
| 27 | `repositories/source_repository.py` :: create_source (with url) | sources | ✅ new |
| 28 | `repositories/source_repository.py` :: create_source (no url) | sources | ✅ new |
| 29 | `repositories/task_checkpoint_repository.py` :: save | task_processing_checkpoints | ✅ new |
| 30 | `repositories/task_repository.py` :: create_task | tasks | ✅ pre-existing (`test_task_insert_columns.py`) |
| 31 | `repositories/upload_repository.py` :: reserve_upload | media_upload_sessions | ✅ new |
| 32 | `repositories/upload_repository.py` :: _remember_idempotency_key | media_upload_idempotency_keys | ✅ new |
| 33 | `repositories/upload_repository.py` :: claim_stored_upload | media_upload_sessions | ✅ new |
| 34 | `repositories/watch_folder_repository.py` :: upsert_settings | watch_folder_settings | ✅ new |
| 35 | `repositories/watch_folder_repository.py` :: claim_file | watch_folder_files | ✅ new |
| 36 | `repositories/watch_folder_repository.py` :: insert_completed_upload_session | media_upload_sessions | ✅ new |
| 37 | `services/ai_edit_allowance_service.py` :: reserve | ai_edit_usage_events | ✅ new |
| 38 | `source_submission.py` :: _persist_rejection | rejected_source_attempts | ✅ new |

## UPDATE inventory (not covered by this lane — creation first, per brief)

68 UPDATE literals were inventoried. They fall into three classes:

- **Fixed-text UPDATEs** (the majority; e.g. all `clip_repository.update_clip_*`,
  `clip_export_repository.mark_*`, `social_repository.claim_post`,
  `task_repository.enable/disable_sharing`, channel/watch reclaim bases) —
  executable with the same helper; **not yet covered**.
- **Constant-interpolated f-string UPDATEs** — the channel-monitor and
  watch-folder `reclaim_*` methods and `clip_export_repository.fail_stale_running`
  interpolate module constants/fragments only, so the existing
  `extract_statement` resolver can already reconstruct them; coverable in
  the same style.
- **Runtime-assembled UPDATEs (no fixed statement text)** — the SET clause
  is built from caller-supplied field dicts in:
  `brand_kit_repository.update_kit`, `channel_monitor_repository.update_monitor`,
  `channel_monitor_repository.update_video`, `publishing_repository.update_campaign`,
  `publishing_repository.update_post`, `social_repository.update_post`,
  `task_repository.update_task_runtime_metadata`,
  `task_repository.update_task_status`, `watch_folder_repository.update_file`.
  Statement extraction cannot cover these by construction.

## Remaining work — Nov 17 gate formalisation list

1. **UPDATE execution coverage** using the same helper, repositories first
   (fixed-text + constant-interpolated classes above).
2. **Method-level tests for the 9 runtime-assembled UPDATEs** — drive each
   repository method against a real (SQLite) async engine with a
   representative field dict, or property-test the assignment builder;
   extraction-based tests structurally cannot reach them.
3. **Postgres-backed execution tier** for the statements SQLite cannot
   model: `clip_exports` INSERT (`make_interval`), the
   `SELECT … FOR UPDATE SKIP LOCKED` claim sites
   (`clip_export_repository.lock_expired`,
   `social_repository.claim_due_scheduled_posts`,
   `workers/media_cleanup.drain_deletions`), `pg_advisory_xact_lock`
   (upload reserve), and true jsonb `||` merge semantics
   (checkpoints, social accounts). Needs a scratch Postgres in the gate
   environment (the sandbox has none).
4. **Gate item proposal:** add the `test_sql_exec_*` suites as a named
   gate check alongside `test_task_insert_columns.py`, and add an
   inventory guard test that fails when a new raw INSERT literal appears
   in `backend/src` without a corresponding execution test — so the
   coverage cannot silently regress again.
5. **Canonical counting:** the gate counted 37 INSERTs, the AST inventory
   finds 38 literals (difference = the `schema_migrations` bookkeeping
   insert). Future gates should quote the AST extractor's count.
