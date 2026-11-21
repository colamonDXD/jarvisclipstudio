# DELETE Inventory + Postgres Residuals — Report

**Lane:** `test-sql-delete-inventory` (worktree `~/workspace/jcs-delinv`),
branched off next-milestone @ `1130cfb`. Test-only; no product code
touched; the only existing test modified is the inventory guard (it
had to grow — stated in its commit body); **NOT merged** (main
merges). **Date:** 2026-11-11.
**Commits:** `bd0d6d8` (SQLite DELETE suite), `9ee88ac` (Postgres
tier: PG-only DELETEs + residuals), `376d5f6` (guard extension).

This lane closes the SQL safety net. DELETE statements were in
neither the INSERT inventory (38) nor the UPDATE inventory (68); the
Postgres-tier lane flagged one of them (`purge_failures`) sitting
outside both, executed nowhere, plus two UPDATE variant remainders.
All three residuals are now executed for real, every DELETE has
execution coverage, and the inventory guard watches DELETEs too.
**No suspected live defects — every statement did exactly what its
guard clauses say.**

## 1. Canonical DELETE inventory: 17 statements, 16 pairs

Method: the guard's canonical AST inventory (literals in statement
positions — call arguments, assignments, returns — docstrings
excluded, identical texts deduplicated per function, rendered with
`sql_exec_support._render` / `_module_constants`). No fragments: all
17 have fixed text.

| # | (file, function) | Statement (WHERE clause) | Tier |
|---|---|---|---|
| 1 | `api/routes/admin.py :: update_runtime_settings` | `app_settings … setting_key = ANY(CAST(:setting_keys AS text[]))` | **PG** (xfail SQLite) |
| 2 | `repositories/brand_kit_repository.py :: delete_kit` | `brand_kits … id AND user_id … RETURNING id` | SQLite |
| 3 | `repositories/channel_monitor_repository.py :: delete_monitor` | `channel_monitors … user_id AND id` | SQLite |
| 4 | `repositories/clip_export_repository.py :: delete` | `clip_exports … id` | SQLite |
| 5 | `repositories/clip_export_repository.py :: purge_failures` | `clip_exports … status='failed' AND updated_at < NOW() - make_interval(days => :days)` | **PG** (xfail SQLite) |
| 6 | `repositories/clip_repository.py :: delete_clips_by_task` | `generated_clips … task_id` | SQLite |
| 7 | `repositories/clip_repository.py :: delete_clip` | `generated_clips … id` | SQLite |
| 8 | `repositories/hook_preview_repository.py :: replace_previews_for_clip` | `clip_hook_previews … clip_id` | SQLite |
| 9 | `repositories/publishing_repository.py :: delete_campaign` | `campaigns … id AND user_id … RETURNING id` | SQLite |
| 10 | `repositories/publishing_repository.py :: delete_post` | `clip_post_stats … post_id` | SQLite |
| 11 | `repositories/publishing_repository.py :: delete_post` | `clip_posts … id AND user_id … RETURNING id` | SQLite |
| 12 | `repositories/social_repository.py :: consume_oauth_state` | `social_oauth_states … state … RETURNING state, user_id, provider, code_verifier, redirect_uri, expires_at` | SQLite |
| 13 | `repositories/social_repository.py :: purge_expired_oauth_states` | `social_oauth_states … expires_at < NOW() RETURNING state` | SQLite |
| 14 | `repositories/social_repository.py :: delete_post` | `social_posts … id AND user_id AND status NOT IN ('queued','publishing') … RETURNING id` | SQLite |
| 15 | `repositories/task_repository.py :: delete_task` | `tasks … id` | SQLite |
| 16 | `workers/media_cleanup.py :: drain_deletions` | `media_deletions … file_path` | SQLite |
| 17 | `workers/media_cleanup.py :: _expire_one_upload_session` | `media_upload_sessions … id` | SQLite |

## 2. SQLite tier — `tests/unit/test_sql_exec_deletes.py` (17 tests)

Each test extracts the real statement text, seeds a target row AND
guard rows, executes, and asserts the target is gone and every guard
row survives: wrong owner (brand kits, campaigns, clip posts, social
posts, channel monitors), other parent (clips of another task,
previews of another clip, stats of another post), not-yet-due
(unexpired OAuth state), protected status (a `queued` social post —
the worker's, not deletable; an old but non-failed export is the PG
tier's guard). `RETURNING` payloads are asserted where the statement
has one, including a second `consume_oauth_state` call returning
nothing (consume-once semantics).

**Result: 15 passed / 2 strict-xfailed.** Both xfails were verified
with `--runxfail` to fail for exactly the documented reason:
`purge_failures` → `sqlite3.OperationalError: near ">": syntax error`
(the `=>` named argument); admin delete → `no such function: ANY`.

## 3. Postgres tier — `tests/unit/test_sql_exec_postgres_deletes.py` (4 tests)

Same opt-in discipline as the first tier file (`SQL_EXEC_PG_URL`;
unset or unreachable → skip, never fail). Scratch cluster stood up
with the POSTGRES_TIER_REPORT recipe (PostgreSQL 16.15, port 55433,
127.0.0.1 only, db `sql_exec_tier`). One deviation from the recipe,
recorded for the gate: in this run the namespace session reported
the `uid_map` writes as not permitted, yet initdb proceeded with the
overflow user and the cluster ran normally — the recipe works
either way; verify with `pg_isready` before running tests.

| Test | Verdict | What was asserted |
|---|---|---|
| `purge_failures` DELETE | ✅ PASS | Exactly 1 row deleted with `days=7`: the 10-day-old `failed` export. Guards survive: a fresh `failed` export (inside the window) and a 10-day-old `succeeded` export — status AND age together purge |
| admin `delete_keys` DELETE | ✅ PASS | The route's plain Python list binds through `ANY(CAST(:setting_keys AS text[]))`: both named keys deleted (rowcount 2), the unnamed key survives |
| `update_task_status` ANY variant (residual) | ✅ PASS, through the **real method** | Expected-status hit lands (`queued` → `processing`, progress written, returns True); misses return False and change nothing — including a retry against a status the task has since left. The `status = ANY(:expected_statuses)` list binding works on the production stack (SQLAlchemy + asyncpg) |
| `update_post` hashtags (residual) | ✅ PASS, through the **real method** | `hashtags=['alpha','beta']` reads back as the exact list (a real `TEXT[]`, not a stringified list); combined with a `title` write in one call; `hashtags=None` binds the empty list and clears the column rather than writing NULL |

**Result: 4 passed live.** Combined with the first tier file
against the same cluster: **12 passed**.

## 4. Inventory guard — DELETEs watched

`tests/unit/test_sql_inventory_guard.py` (the only existing test
modified, necessarily): statement pattern extended with
`DELETE\s+FROM\s`; `CANONICAL_DELETE_COUNT = 17` pinned next to the
38/68 pins; the coverage test's failure message now names
INSERT/UPDATE/DELETE; the module docstring records the DELETE count
and the `purge_failures` history. New bite proof
`test_guard_reports_an_uncovered_delete`: a synthetic uncovered
DELETE in an appended function of a temp copy of
`cache_repository.py` is reported by name and text (temp copies
only; real sources untouched). INSERT/UPDATE behaviour unchanged:
their suites are untouched and the guard's INSERT/UPDATE assertions
are verbatim.

**Result: guard 6 passed** (5 prior + the DELETE bite proof);
0 uncovered across all 123 inventoried statements
(38 INSERT + 68 UPDATE + 17 DELETE).

## 5. Run evidence (serial; TMPDIR on workspace; logs on disk)

Logs in `research_notes/sprint-7day/delete-inventory-logs/`:

| Run | Configuration | Result |
|---|---|---|
| `sqlite-deletes-run-1.log` | new DELETE suite | **15 passed / 2 xfailed** |
| `sqlite-deletes-runxfail.log` | same, `--runxfail` | 2 failed, both for the documented reasons (above) |
| `pg-deletes-run-1.log` | new PG file, live cluster | **4 passed** |
| `pg-combined-run.log` | both PG tier files, live cluster | **12 passed** |
| `pg-deletes-noenv.log` / `pg-deletes-deadurl.log` | no env var / dead URL | **4 skipped** each — never a failure |
| `guard-run-1.log` | extended guard | **6 passed** |
| `family-default-run.log` | full family, **no env var** | **123 passed / 12 skipped / 7 xfailed / 0 failed** |
| `family-pg-run.log` | full family, live cluster | **135 passed / 7 xfailed / 0 failed / 0 skipped** |

The default-path numbers reconcile exactly against the pre-lane
baseline (107 passed / 8 skipped / 5 xfailed): +15 passed +2 xfailed
(the DELETE suite), +1 passed (the guard bite proof), +4 skipped
(the new opt-in PG file). With the cluster up, the 12 skips become
12 passes and only the 7 documented SQLite-limit xfails remain —
every one of the 123 inventoried statements now executes somewhere:
SQLite tier, Postgres tier, or method level.

## 6. Cleanup confirmation

- Postmaster stopped by fast shutdown (SIGINT); the server log ends
  with `database system is shut down`; `pg_isready` reports no
  response; no postgres processes remain.
- The namespace holder session was killed; `~/workspace/.pg-tier/`
  and the lane's `~/workspace/.tmp-delinv` were removed entirely.
  Only footprint left: the `postgresql` apt binaries (never running)
  and this lane's branch/worktree.

## 7. What the Nov 17 gate checklist should say (updated wording)

> **Raw-SQL execution coverage (gate item).** Run the full sql-exec
> family as one named check:
> `pytest tests/unit/test_sql_exec_creation_core.py tests/unit/test_sql_exec_creation_misc.py tests/unit/test_sql_exec_creation_social.py tests/unit/test_sql_exec_update_core.py tests/unit/test_sql_exec_update_monitors.py tests/unit/test_sql_exec_update_methods.py tests/unit/test_sql_exec_deletes.py tests/unit/test_sql_exec_postgres_tier.py tests/unit/test_sql_exec_postgres_deletes.py tests/unit/test_task_insert_columns.py tests/unit/test_sql_inventory_guard.py --no-cov`
> Expected (no Postgres): all pass, with exactly 7 strict xfails
> (clip_exports INSERT `make_interval`; UPDATEs drain_deletions,
> fail_stale_publishing, fail_stale_running — `make_interval`;
> claim_due_scheduled_posts — `FOR UPDATE SKIP LOCKED`; DELETEs
> purge_failures — `make_interval(days =>)`, admin delete_keys —
> `ANY(text[])`), which belong to the Postgres tier, and 12 skips
> (the two opt-in Postgres tier files). The inventory guard must
> pass: it fails when any raw INSERT/UPDATE/DELETE in `backend/src`
> lacks an execution test, naming the statement's file and function.
> Quote the canonical AST counts in the gate report — 38 INSERT
> literals / 68 UPDATE statements / 17 DELETE statements (or the
> guard's bumped pins if statements were added with coverage in the
> same change) — never grep counts. A red guard is
> release-blocking: either the new statement gains an execution test
> in the same change, or it does not ship.
> **Postgres tier check:** stand up a scratch Postgres (recipe in
> POSTGRES_TIER_REPORT.md), set `SQL_EXEC_PG_URL`, re-run the same
> family: expect 0 skips and the 12 tier tests passed (8 + 4); the
> 7 SQLite xfails remain (they document the SQLite limit). Any tier
> skip with the cluster up = the tier did not run; treat as a gate
> failure for the tier check, not a silent pass.

## 8. Safety net — final state

Nothing remains on the formalisation list. INSERT (38), UPDATE (68)
and DELETE (17) statements are all inventoried by the guard,
executed on the SQLite tier where the dialect allows, executed on
the Postgres tier where it does not (including `make_interval` in
all its forms, `FOR UPDATE SKIP LOCKED` claims, `pg_advisory_xact_lock`,
true jsonb `||` merge, `ANY(:list)` bindings in both statement and
assembled form, and the `TEXT[]` array binding), and the guard fails
the gate if a future statement lands without an execution test.
Known boundary, unchanged from the guard's own docstring: only
literals inside functions are inventoried; a raw statement parked in
a module-level constant would need extraction support first.
