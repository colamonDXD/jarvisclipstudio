# Raw-SQL Inventory Guard — Report

**Lane:** `test-sql-inventory-guard` (worktree `~/workspace/jcs-invguard`),
branched off next-milestone @ `0b75ba5`. Test-only; no product code
touched; no existing test modified; **NOT merged** (main merges).
**Date:** 2026-11-11. **Commit:** `ad6cff5`.
**Closes:** remaining-work item 2 (second half) of
`UPDATE_EXECUTION_COVERAGE_REPORT.md` — the inventory guard, the last
sandbox-doable piece of the SQL safety net. The Postgres execution
tier was explicitly out of scope (no Postgres in the sandbox).

## Why

The INSERT and UPDATE lanes executed every raw statement in
`backend/src` (38 INSERT literals, 68 UPDATE statements), but coverage
was a snapshot: nothing stopped the *next* raw statement from landing
without an execution test — the exact hole that shipped the
duplicated-column tasks INSERT on 2026-11-11 (repository unit tests
mock the DB session, so raw SQL historically shipped unexecuted).

## Deliverable

`backend/tests/unit/test_sql_inventory_guard.py` — 5 tests:

| Test | Role |
|---|---|
| `test_inventory_counts_match_the_canonical_ast_inventory` | Anti-vacuity pin: the inventory must find exactly 38 INSERT / 68 UPDATE. If a statement is added *with* its execution test, the pin is bumped in the same change; if the inventory method drifts, this fails instead of the guard passing blind. |
| `test_every_raw_insert_or_update_has_execution_coverage` | The guard itself. Fails with every uncovered statement named as `file :: function -- statement text`. |
| `test_guard_reports_a_statement_in_a_brand_new_function` | Bite proof 1: a synthetic INSERT in an appended function of a temp copy of `cache_repository.py` is reported. |
| `test_guard_reports_a_new_statement_inside_a_covered_function` | Bite proof 2: a synthetic INSERT injected inside the already-covered `upsert_cache` is reported (per-statement text check), while the function's real statement stays covered. |
| `test_guard_reports_an_uncovered_runtime_assembled_fragment` | Bite proof 3: a synthetic dict-driven fragment UPDATE in a new function is reported as a runtime-assembled fragment. |

The bite proofs operate on temp copies under `tmp_path`; real sources
are never modified.

## How coverage is determined

**Inventory** (canonical, shared with the lanes): AST-walk
`backend/src`; render string constants and constant-only f-strings
with `sql_exec_support._render` / `_module_constants` (the same
rendering `extract_statement` uses, including the
`RETURNING {_COLUMNS}` idiom); keep literals in statement positions
(call arguments, assignments, returns) whose text starts with
`INSERT INTO` / `UPDATE <table>`; docstrings excluded; identical
texts deduplicated per function; each statement attributed to its
enclosing function. An f-string that interpolates runtime values
(the dict-driven assembled kind) cannot be rendered, so its joined
constant parts are recorded as a **fragment**. Calibration: the
inventory reproduces the lanes' counts and per-function lists exactly
— 38 INSERT (36 functions), 68 UPDATE, 97 (file, function) pairs.

**Covered texts**: the guard parses every `test_sql_exec_*.py` file,
extracts each `extract_statement(rel_path, func_name, needle, …)`
call (all arguments are constants in the suites today), and *replays*
it through the real `extract_statement`, collecting the exact
statement texts the suites execute — 84 (file, function) pairs.
The documented strict xfails (clip_exports INSERT; drain_deletions,
fail_stale_publishing, claim_due_scheduled_posts UPDATEs) are ordinary
tests in those suites, so they are covered by construction, exactly
as the brief requires. A replay that raises is recorded as a problem
and fails the guard loudly — the guard never passes blind.

**Function-level coverage**: statements assembled at runtime have no
fixed text to extract, so their coverage is the method being driven.
The guard recognises, in the suites, calls of the form
`RepositoryClass.method(...)` and `repo.method(...)` (instance
variables tracked **per test-function scope** — the name `repo` is
reused for different repository classes across tests, which a
file-global map gets wrong), resolving the class to its module via
the suites' `src.…` imports; plus `inspect.getsource(Class.method)`
pins (the `test_task_insert_columns.py` mechanism). This yields
exactly the 13 expected pairs: the 9 dict-driven UPDATE functions +
`mark_failed` + `cancel_unpublished_for_clips` + `fail_stale_running`
(method-level) + `create_task` (getsource). Fragment statements are
covered **only** at this level; full-text statements must match an
extracted text exactly (whitespace-collapsed).

**Result on the current tree: 0 uncovered.** 84 extraction pairs +
13 function-level pairs = the 97 inventory pairs.

Two calibration bugs were found and fixed while building the guard,
recorded so nobody re-derives them:

1. A renderable f-string's first constant part also matches the
   statement pattern on its own — the inventory must not descend into
   a string node's parts, or every interpolated statement double-counts
   (early prototype read 43 INSERT / 85 UPDATE before this fix).
2. Scope walking must never descend into top-level `def`s of the
   scope being walked, or calls are attributed to the wrong test
   function's variable map (this produced a phantom
   `channel_monitor_repository :: update_file` pair before the fix).

Known boundary, shared with the lanes' canonical inventory: only
literals inside functions are inventoried (`extract_statement` has
the same reach). A raw statement parked in a module-level constant
would need extraction support first.

## Results

Serial runs (TMPDIR on workspace, `OMP_NUM_THREADS=2`, lane venv,
logs on disk):

- Guard alone: **5 passed / 0 failed**.
- Batch 1 — guard + `test_task_insert_columns.py` + creation suites:
  **43 passed / 1 xfailed / 0 failed**.
- Batch 2 — UPDATE suites (core + monitors + methods):
  **64 passed / 4 xfailed / 0 failed**.
- Whole sql-exec family + guard combined: **107 passed / 5 xfailed /
  0 failed** (the 102/5 family baseline + the guard's 5).

No CHANGELOG — test-only, stated in the commit body per repo rules.

## What the Nov 17 gate checklist should say (verbatim)

> **Raw-SQL execution coverage (gate item).** Run the full sql-exec
> family as one named check:
> `pytest tests/unit/test_sql_exec_creation_core.py tests/unit/test_sql_exec_creation_misc.py tests/unit/test_sql_exec_creation_social.py tests/unit/test_sql_exec_update_core.py tests/unit/test_sql_exec_update_monitors.py tests/unit/test_sql_exec_update_methods.py tests/unit/test_task_insert_columns.py tests/unit/test_sql_inventory_guard.py --no-cov`
> Expected: all pass, with exactly 5 strict xfails (clip_exports
> INSERT `make_interval`; UPDATEs drain_deletions,
> fail_stale_publishing, fail_stale_running — `make_interval`;
> claim_due_scheduled_posts — `FOR UPDATE SKIP LOCKED`), which belong
> to the Postgres tier. The inventory guard must pass: it fails when
> any raw INSERT/UPDATE in `backend/src` lacks an execution test,
> naming the statement's file and function. Quote the canonical AST
> counts in the gate report — 38 INSERT literals / 68 UPDATE
> statements (or the guard's bumped pins if statements were added
> with coverage in the same change) — never grep counts. A red guard
> is release-blocking: either the new statement gains an execution
> test in the same change, or it does not ship.

## What remains for Nov 17

Only the **Postgres-backed execution tier** (explicitly out of scope
here): the 5 `make_interval` statements, the `FOR UPDATE SKIP LOCKED`
claim sites, `pg_advisory_xact_lock` (upload reserve), true jsonb
`||` merge semantics, the `update_task_status`
`ANY(:expected_statuses)` variant, and the social `hashtags` `TEXT[]`
binding. Everything else on the formalisation list — INSERT coverage,
UPDATE coverage, method-level coverage, the inventory guard,
canonical counting — is now built.
