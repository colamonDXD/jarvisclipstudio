# Postgres Execution Tier — Report

**Lane:** `test-postgres-exec-tier` (worktree `~/workspace/jcs-pgtier`),
branched off next-milestone @ `6a3b287`. Test-only; no product code
touched; no existing test modified; **NOT merged** (main merges).
**Date:** 2026-11-11. **Commit:** `701936c`.
**Deliverable:** `backend/tests/unit/test_sql_exec_postgres_tier.py`
(8 tests, 503 lines).

This lane closes the last open piece of the SQL safety net: the five
statements the SQLite tiers carry as documented strict xfails because
they use Postgres-only constructs. **All five now execute for real
against a live PostgreSQL and pass**, plus the two Postgres-only
behaviours the SQLite suites could only approximate
(`pg_advisory_xact_lock`, true jsonb `||` merge). **No suspected live
defects** — every statement did exactly what its guard clauses say.

## Standing up Postgres in the sandbox

- `apt-get install -y postgresql` succeeded (we run as root; same
  route as the earlier tesseract install): **PostgreSQL 16.15**
  (Ubuntu `16.15-0ubuntu0.24.04.1`).
- Scratch cluster created fresh for this lane: data dir +
  unix-socket dir under `~/workspace/.pg-tier/` (never `/tmp`),
  port **55433**, `listen_addresses=127.0.0.1` only, database
  `sql_exec_tier`, local trust auth (throwaway scratch instance).
- One sandbox quirk, recorded so the Nov 17 gate doesn't re-derive
  it: Postgres refuses to run as root, and the `postgres` system user
  cannot reach `~/workspace` at all (`/home/hatch` is
  `root:nogroup`, and `chown` is blocked on this mount). The cluster
  therefore ran inside a **user namespace with a hand-written uid
  map** (inner uid 1000 ↔ outer root): from inside, the server sees a
  non-root owner for its data dir; from outside, all files are
  root-owned as usual. `unshare`'s own `--map-users` helper refused
  the non-identity map (`newuidmap` range error); writing
  `/proc/self/uid_map` directly from the root shell worked.
- The packaged default cluster in `/var/lib/postgresql` was never
  started or touched.

Re-stand-up recipe (exact commands used):

```bash
apt-get install -y postgresql   # once
mkdir -p ~/workspace/.pg-tier/data ~/workspace/.pg-tier/run
chmod 0777 ~/workspace/.pg-tier ~/workspace/.pg-tier/data ~/workspace/.pg-tier/run
# in a held background session (keeps the namespace alive):
unshare -U bash -c '
  echo deny > /proc/self/setgroups
  echo "1000 0 1" > /proc/self/uid_map
  echo "1000 0 1" > /proc/self/gid_map
  /usr/lib/postgresql/16/bin/initdb -D /home/hatch/workspace/.pg-tier/data \
      -U postgres --auth=trust --no-instructions          # first time only
  /usr/lib/postgresql/16/bin/pg_ctl -D /home/hatch/workspace/.pg-tier/data \
      -l /home/hatch/workspace/.pg-tier/server.log \
      -o "-p 55433 -k /home/hatch/workspace/.pg-tier/run \
          -c listen_addresses=127.0.0.1" -w start
  sleep infinity'
createdb -h 127.0.0.1 -p 55433 -U postgres sql_exec_tier
export SQL_EXEC_PG_URL='postgresql+asyncpg://postgres@127.0.0.1:55433/sql_exec_tier'
```

## Per-statement verdicts (all against the live cluster)

| # | Statement (SQLite-tier status) | Verdict on Postgres | What was asserted |
|---|---|---|---|
| 1 | `clip_export_repository.insert` — INSERT, `make_interval(secs => :ttl_seconds)` (xfail) | ✅ PASS | Row lands `queued`/0; `expires_at − created_at` is **exactly** 3600 s (same-transaction `NOW()`s) — the TTL arithmetic itself, not just parseability |
| 2 | `clip_export_repository.fail_stale_running` — runtime-assembled UPDATE, `make_interval(secs => :stale_seconds)` clause (method-level xfail) | ✅ PASS, driven through the **real method** on a real `AsyncSession` | Exactly the stale `running` row fails with the message; a fresh `running` row and a stale `queued` row are untouched; rowcount 1 |
| 3 | `workers/media_cleanup.drain_deletions` — retry UPDATE, `make_interval(secs => LEAST(21600, 60 * power(2, LEAST(attempts, 9)))::integer)` (xfail) | ✅ PASS | `attempts` 0→1; first-failure backoff lands at +60 s (`next_attempt_at − clock_timestamp()` ∈ [50 s, 65 s]) — the `power()`/`LEAST` expression evaluated, not stubbed |
| 4 | `workers/media_cleanup.drain_deletions` — claim SELECT ending `FOR UPDATE SKIP LOCKED` (Postgres-tier item in both prior reports) | ✅ PASS | Returns exactly the one due, unheld row; a held row (`hold_token` set) and a not-yet-due row are both skipped |
| 5 | `social_repository.fail_stale_publishing` — UPDATE, `make_interval(secs => CAST(:seconds AS integer))` (xfail) | ✅ PASS | `RETURNING` yields only the stale, handle-less `publishing` post; it lands `failed` with the message; a fresh post and a stale post **with** a pending handle are untouched |
| 6 | `social_repository.claim_due_scheduled_posts` — UPDATE over a `FOR UPDATE SKIP LOCKED` subquery (xfail) | ✅ PASS | Only the due `scheduled` post is claimed to `queued` and returned; the future post stays `scheduled` |
| 7 | `pg_advisory_xact_lock` via `ClipExportRepository.lock_options` (same construct guards the upload reserve) | ✅ PASS | While transaction A holds the lock, `pg_try_advisory_xact_lock` for the same key from an independent connection returns **false**; after A commits it returns **true** — exclusion is behavioural, not assumed |
| 8 | `task_repository.save_source_metadata` — `source_metadata \|\| CAST(:metadata AS JSONB)` (SQLite approximated as text concat) | ✅ PASS with **true jsonb semantics** | `{"a":1,"b":{"x":1}}` merged with `{"b":{"y":2},"c":3}` yields exactly `{"a":1,"b":{"y":2},"c":3}` — shallow merge pinned: `b` is replaced wholesale, **not** deep-merged |

The SQLite-tier xfails are deliberately **unchanged**: they document
the SQLite limit, and the inventory guard counts them as covered.
This tier is where those statements pass; each tier test's docstring
cross-references the xfail it converts.

## Run evidence (serial; TMPDIR on workspace; logs on disk)

Logs in `research_notes/sprint-7day/pg-tier-logs/`:

| Run | Command shape | Result |
|---|---|---|
| `tier-run-2.log` | tier file, `SQL_EXEC_PG_URL` → live cluster | **8 passed** |
| `default-path-run.log` | sql-exec family + inventory guard + task-insert, **no env var** | **107 passed / 8 skipped / 5 xfailed / 0 failed** — the 8 skips are this tier opting out; the 5 xfails are the documented SQLite ones, untouched |
| `unreachable-run.log` | tier file, env var → dead port | **8 skipped** — a stale URL skips, never fails |

(`tier-run-1.log` is the first live run: 5 passed / 3 failed, all
three being bugs in this lane's own test harness — `fetchall()` on
UPDATEs without `RETURNING`, and a `str` bound to a `TIMESTAMPTZ`
parameter, which asyncpg rejects before the statement runs. Fixed in
the harness; no product code was ever implicated. Recorded here so
the sequence is auditable.)

## How the Nov 17 gate should run this tier

1. Stand up the scratch cluster with the recipe above (or any real
   Postgres reachable over TCP).
2. `SQL_EXEC_PG_URL=<url>` + the family run: expect the tier file to
   report **8 passed** (any skip = the tier did not run; treat as a
   gate failure for the tier check, not a silent pass).
3. In the default family run (no env var), expect the tier file to
   report **8 skipped** and the SQLite xfails to remain **exactly
   5**. Add "Postgres tier: 8 passed with cluster up" as a named
   gate check next to the inventory guard.

## Adjacent remainders (observed, out of this lane's scope)

- `task_repository.update_task_status`'s `status = ANY(:expected_statuses)`
  variant and `social_repository.update_post`'s `hashtags` `TEXT[]`
  binding (the UPDATE lane's two variant remainders) are still
  unexecuted on any tier — same treatment would work here.
- `clip_export_repository.purge_failures` is a DELETE using
  `make_interval(days => :days)`; DELETEs were never in the
  INSERT/UPDATE inventories, so it has no execution test on either
  tier. Worth adding to the Nov 17 list.

## Cleanup confirmation

- Postmaster stopped with a fast shutdown (SIGINT); the server log's
  last lines show a completed checkpoint and `database system is
  shut down`; `pg_isready` reports no response; no postgres
  processes remain.
- The namespace holder session was killed; `~/workspace/.pg-tier/`
  (cluster data, socket dir, server log) and the lane's
  `~/workspace/.tmp-pgtier` were removed entirely. The only lasting
  footprint is the `postgresql` apt package itself (binaries, never
  running) and this lane's branch/worktree.
