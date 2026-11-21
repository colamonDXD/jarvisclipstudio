"""Statement-execution tests for ``LiveRepository`` (live clipping).

Series B (live clipping) landed nine raw INSERT/UPDATE/DELETE
statements in ``repositories/live_repository.py`` with repository unit
tests that mock the session -- the exact blind spot the SQL inventory
guard exists to close (see ``test_sql_inventory_guard.py``). This
suite executes the real statement texts, extracted from source with
``sql_exec_support.extract_statement`` (never hand-copied), against
in-memory SQLite following the sibling ``test_sql_exec_*`` suites.

Two statements are Postgres-only and are documented strict xfails
here; both are executed for real in the Postgres tier
(``test_sql_exec_postgres_tier.py``):

- ``claim_requested`` -- the claim subquery ends with
  ``FOR UPDATE SKIP LOCKED``, which SQLite cannot parse.
- ``fail_stale`` -- the staleness clause is an ``INTERVAL '120
  seconds'`` literal; it is covered at method level through
  ``ExecSession`` (the statement text is runtime-assembled from an
  f-string, so there is no fixed text to extract).
"""

from __future__ import annotations

import pytest

from sql_exec_support import ExecSession, count_rows, execute, extract_statement, fresh_conn

from src.repositories.live_repository import LiveRepository

_LIVE_SESSIONS_DDL = """
CREATE TABLE live_sessions (
    id TEXT PRIMARY KEY, user_id TEXT NOT NULL, url TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'requested', settings TEXT NOT NULL DEFAULT '{}',
    stop_requested INTEGER NOT NULL DEFAULT 0, worker_id TEXT,
    heartbeat_at TEXT, recording INTEGER NOT NULL DEFAULT 0,
    buffered_seconds REAL NOT NULL DEFAULT 0, restarts INTEGER NOT NULL DEFAULT 0,
    last_error TEXT, progress_message TEXT,
    started_at TEXT, stopped_at TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
)
"""

_LIVE_CLIPS_DDL = """
CREATE TABLE live_clips (
    id TEXT PRIMARY KEY, session_id TEXT NOT NULL, n INTEGER,
    status TEXT NOT NULL, reason TEXT,
    onset_at TEXT, start_at TEXT, end_at TEXT,
    signals TEXT NOT NULL DEFAULT '[]', strength REAL, talk_share REAL,
    preview_path TEXT, clip_path TEXT, duration REAL, render_seconds REAL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
)
"""

def _seed_session(conn, sid="s-1", user_id="user-1", status="requested", **extra):
    columns = ["id", "user_id", "url", "status"] + list(extra)
    values = [sid, user_id, "https://twitch.tv/example", status] + list(extra.values())
    placeholders = ", ".join("?" for _ in values)
    conn.execute(
        f"INSERT INTO live_sessions ({', '.join(columns)}) VALUES ({placeholders})",
        values,
    )


def _fresh():
    return fresh_conn(_LIVE_SESSIONS_DDL, _LIVE_CLIPS_DDL)


# ---------------------------------------------------------------------------
# INSERTs
# ---------------------------------------------------------------------------


def test_live_create_session_executes():
    sql = extract_statement("repositories/live_repository.py", "create_session", "INSERT INTO live_sessions")
    conn = _fresh()
    execute(conn, sql, {
        "id": "s-1", "user_id": "user-1",
        "url": "https://twitch.tv/example", "settings": '{"mode": "live"}',
    })
    row = conn.execute(
        "SELECT user_id, url, status, settings FROM live_sessions WHERE id = 's-1'"
    ).fetchone()
    assert row == ("user-1", "https://twitch.tv/example", "requested", '{"mode": "live"}')


def test_live_record_moment_executes():
    sql = extract_statement("repositories/live_repository.py", "record_moment", "INSERT INTO live_clips")
    conn = _fresh()
    _seed_session(conn)
    execute(conn, sql, {
        "id": "c-1", "sid": "s-1", "n": 1, "status": "queued",
        "reason": "crowd spike", "onset": "2026-11-19 10:00:00",
        "start": "2026-11-19 09:59:40", "end": "2026-11-19 10:00:20",
        "signals": '["audio_spike"]', "strength": 0.87, "talk": 0.42,
        "preview": "/tmp/preview.mp4",
    })
    row = conn.execute(
        "SELECT session_id, n, status, reason, signals, strength, talk_share, "
        "preview_path FROM live_clips WHERE id = 'c-1'"
    ).fetchone()
    assert row == ("s-1", 1, "queued", "crowd spike", '["audio_spike"]', 0.87, 0.42,
                   "/tmp/preview.mp4")


# ---------------------------------------------------------------------------
# UPDATEs
# ---------------------------------------------------------------------------


def test_live_request_stop_executes():
    sql = extract_statement("repositories/live_repository.py", "request_stop", "UPDATE live_sessions SET stop_requested")
    conn = _fresh()
    _seed_session(conn, sid="s-1", status="requested")
    _seed_session(conn, sid="s-2", status="recording")
    # A session nobody claimed yet stops at once.
    cursor = execute(conn, sql, {"id": "s-1", "user_id": "user-1"})
    assert cursor.rowcount == 1
    row = conn.execute(
        "SELECT status, stop_requested, stopped_at IS NOT NULL FROM live_sessions "
        "WHERE id = 's-1'"
    ).fetchone()
    assert row == ("stopped", 1, 1)
    # A recording session is only flagged; the worker ends it.
    execute(conn, sql, {"id": "s-2", "user_id": "user-1"})
    row = conn.execute(
        "SELECT status, stop_requested FROM live_sessions WHERE id = 's-2'"
    ).fetchone()
    assert row == ("recording", 1)
    # Another owner's session is untouched.
    cursor = execute(conn, sql, {"id": "s-2", "user_id": "user-2"})
    assert cursor.rowcount == 0


def test_live_heartbeat_executes():
    sql = extract_statement("repositories/live_repository.py", "heartbeat", "UPDATE live_sessions SET heartbeat_at")
    conn = _fresh()
    _seed_session(conn, sid="s-1", status="starting")
    row = execute(conn, sql, {
        "id": "s-1", "recording": True, "buffered": 95.5, "restarts": 1,
        "err": None, "progress": "recording",
    }).fetchone()
    assert row[0] == 0  # stop_requested comes back so the worker can stop
    row = conn.execute(
        "SELECT status, recording, buffered_seconds, restarts, progress_message "
        "FROM live_sessions WHERE id = 's-1'"
    ).fetchone()
    assert row == ("recording", 1, 95.5, 1, "recording")


def test_live_finish_session_executes():
    sql = extract_statement("repositories/live_repository.py", "finish_session", "UPDATE live_sessions SET status = :status")
    conn = _fresh()
    _seed_session(conn, sid="s-1", status="recording", recording=1)
    execute(conn, sql, {"id": "s-1", "status": "stopped", "err": None})
    row = conn.execute(
        "SELECT status, recording, stopped_at IS NOT NULL, last_error "
        "FROM live_sessions WHERE id = 's-1'"
    ).fetchone()
    assert row == ("stopped", 0, 1, None)


def test_live_update_clip_executes():
    sql = extract_statement("repositories/live_repository.py", "update_clip", "UPDATE live_clips SET status")
    conn = _fresh()
    _seed_session(conn)
    conn.execute(
        "INSERT INTO live_clips (id, session_id, n, status) "
        "VALUES ('c-1', 's-1', 1, 'queued')"
    )
    execute(conn, sql, {
        "sid": "s-1", "n": 1, "status": "ready", "path": "/clips/c-1.mp4",
        "duration": 32.5, "render_s": 41.0, "error": None,
    })
    row = conn.execute(
        "SELECT status, clip_path, duration, render_seconds FROM live_clips "
        "WHERE id = 'c-1'"
    ).fetchone()
    assert row == ("ready", "/clips/c-1.mp4", 32.5, 41.0)


@pytest.mark.xfail(
    strict=True,
    reason="Postgres-only: the claim subquery ends with FOR UPDATE SKIP "
    "LOCKED, which SQLite cannot parse. Executed for real in the "
    "Postgres tier (test_sql_exec_postgres_tier.py).",
)
def test_live_claim_requested_executes():
    sql = extract_statement("repositories/live_repository.py", "claim_requested", "UPDATE live_sessions SET status = 'starting'")
    conn = _fresh()
    _seed_session(conn, sid="s-1", status="requested")
    row = execute(conn, sql, {"worker": "worker-1"}).fetchone()
    assert row[0] == "s-1"


@pytest.mark.xfail(
    strict=True,
    reason="Postgres-only: the staleness clause is heartbeat_at < "
    "CURRENT_TIMESTAMP - INTERVAL '120 seconds', which SQLite cannot "
    "parse. Executed for real in the Postgres tier "
    "(test_sql_exec_postgres_tier.py).",
)
async def test_live_fail_stale_method_executes():
    session = ExecSession(_LIVE_SESSIONS_DDL, _LIVE_CLIPS_DDL)
    _seed_session(
        session.conn, sid="s-1", status="recording",
        heartbeat_at="2000-01-01 00:00:00",
    )
    repo = LiveRepository()
    count = await repo.fail_stale(session)
    assert count == 1
    assert session.conn.execute(
        "SELECT status FROM live_sessions WHERE id = 's-1'"
    ).fetchone()[0] == "error"


# ---------------------------------------------------------------------------
# DELETE
# ---------------------------------------------------------------------------


def test_live_delete_session_executes():
    sql = extract_statement("repositories/live_repository.py", "delete_session", "DELETE FROM live_sessions")
    conn = _fresh()
    _seed_session(conn, sid="s-1", status="stopped")
    _seed_session(conn, sid="s-2", status="recording")
    # Active sessions refuse deletion.
    cursor = execute(conn, sql, {"id": "s-2", "user_id": "user-1"})
    assert cursor.rowcount == 0
    assert count_rows(conn, "live_sessions") == 2
    # A finished session deletes; another owner's does not.
    cursor = execute(conn, sql, {"id": "s-1", "user_id": "user-2"})
    assert cursor.rowcount == 0
    cursor = execute(conn, sql, {"id": "s-1", "user_id": "user-1"})
    assert cursor.rowcount == 1
    assert count_rows(conn, "live_sessions") == 1
