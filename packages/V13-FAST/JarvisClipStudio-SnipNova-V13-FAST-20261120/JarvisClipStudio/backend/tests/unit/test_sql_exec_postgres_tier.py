"""Postgres execution tier for the raw-SQL statement tests.

The ``test_sql_exec_*`` SQLite suites execute every raw INSERT/UPDATE
in ``backend/src`` except seven statements that use constructs SQLite
cannot even parse; those seven are documented strict xfails there:

- ``clip_exports`` INSERT -- ``make_interval(secs => :ttl_seconds)``
  (``test_sql_exec_creation_core.py``)
- ``workers/media_cleanup.drain_deletions`` retry UPDATE --
  ``make_interval(secs => ...)`` over ``power()``/``LEAST``
  (``test_sql_exec_update_core.py``)
- ``social_repository.fail_stale_publishing`` --
  ``make_interval(secs => CAST(:seconds AS integer))``
  (``test_sql_exec_update_monitors.py``)
- ``social_repository.claim_due_scheduled_posts`` --
  ``FOR UPDATE SKIP LOCKED`` (``test_sql_exec_update_monitors.py``)
- ``clip_export_repository.fail_stale_running`` (method-level) --
  always-present ``make_interval(secs => :stale_seconds)`` clause
  (``test_sql_exec_update_methods.py``)
- ``live_repository.claim_requested`` -- ``FOR UPDATE SKIP LOCKED``
  (``test_sql_exec_live.py``)
- ``live_repository.fail_stale`` (method-level) -- ``INTERVAL '120
  seconds'`` staleness literal (``test_sql_exec_live.py``)

This module executes those seven statements FOR REAL against a live
PostgreSQL, plus two further Postgres-only behaviours the SQLite
suites could only approximate:

- ``pg_advisory_xact_lock`` (``ClipExportRepository.lock_options``;
  the same construct guards the upload reserve in
  ``upload_repository``) -- verified to actually exclude a second
  transaction while held.
- the ``source_metadata || CAST(:metadata AS JSONB)`` merge in
  ``TaskRepository.save_source_metadata`` -- verified with TRUE jsonb
  object-merge semantics (shallow: a repeated key is replaced
  wholesale), which the SQLite run approximates as text
  concatenation.

The SQLite-tier xfails stay exactly as they are -- they document the
SQLite limit; this tier is where the statements pass.

OPT-IN: the whole module skips unless ``SQL_EXEC_PG_URL`` points at a
reachable PostgreSQL (an async SQLAlchemy URL, e.g.
``postgresql+asyncpg://postgres@127.0.0.1:55433/sql_exec_tier``). The
default gate therefore stays green on machines with no Postgres. The
DDL below is minimal and derived from the migrations, with real
Postgres types (``TIMESTAMPTZ``, ``JSONB``, ``BOOLEAN``) preserved --
unlike the SQLite suites, nothing is normalised or transpiled: the
extracted statement text runs verbatim.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from sql_exec_support import extract_statement

from src.repositories.clip_export_repository import ClipExportRepository
from src.repositories.live_repository import LiveRepository

PG_URL = os.getenv("SQL_EXEC_PG_URL")

# ---------------------------------------------------------------------------
# Minimal DDL, derived from the migrations (real Postgres types).
# ---------------------------------------------------------------------------

_CLIP_EXPORTS_PG_DDL = """
CREATE TABLE clip_exports (
    id VARCHAR(36) PRIMARY KEY,
    task_id VARCHAR(36) NOT NULL,
    clip_id VARCHAR(36) NOT NULL,
    user_id VARCHAR(36) NOT NULL,
    preset VARCHAR(32) NOT NULL,
    options_hash VARCHAR(64) NOT NULL,
    source_file_path TEXT NOT NULL,
    status VARCHAR(16) NOT NULL,
    progress INTEGER NOT NULL DEFAULT 0,
    error_message TEXT,
    file_path TEXT,
    download_filename VARCHAR(255) NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    expires_at TIMESTAMPTZ NOT NULL
)
"""

_MEDIA_DELETIONS_PG_DDL = """
CREATE TABLE media_deletions (
    file_path TEXT PRIMARY KEY,
    is_prefix BOOLEAN NOT NULL DEFAULT FALSE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    attempts INTEGER NOT NULL DEFAULT 0,
    next_attempt_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    hold_token TEXT
)
"""

_SOCIAL_POSTS_PG_DDL = """
CREATE TABLE social_posts (
    id VARCHAR(36) PRIMARY KEY,
    user_id VARCHAR(36) NOT NULL,
    status VARCHAR(20) NOT NULL DEFAULT 'queued',
    scheduled_for TIMESTAMPTZ,
    pending_handle VARCHAR(255),
    claimed_at TIMESTAMPTZ,
    error_message TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
)
"""

_TASKS_PG_DDL = """
CREATE TABLE tasks (
    id VARCHAR(36) PRIMARY KEY,
    source_metadata JSONB NOT NULL DEFAULT '{}'::jsonb
)
"""


@pytest.fixture()
async def pg_engine():
    """A live test Postgres, or a clean skip.

    The tier is opt-in twice over: no ``SQL_EXEC_PG_URL`` -> skip;
    URL set but nothing reachable there -> skip (never fail), so a
    stale env var cannot break the default gate.
    """
    if not PG_URL:
        pytest.skip(
            "SQL_EXEC_PG_URL not set -- Postgres execution tier is opt-in "
            "(see this module's docstring)"
        )
    engine = create_async_engine(
        PG_URL, poolclass=NullPool, connect_args={"timeout": 5}
    )
    try:
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
    except Exception as exc:  # noqa: BLE001 -- any connect failure = skip
        await engine.dispose()
        pytest.skip(f"test Postgres not reachable via SQL_EXEC_PG_URL: {exc}")
    try:
        yield engine
    finally:
        await engine.dispose()


async def _setup(engine, table: str, ddl: str) -> None:
    async with engine.begin() as conn:
        await conn.execute(text(f"DROP TABLE IF EXISTS {table}"))
        await conn.execute(text(ddl))


async def _execute(engine, sql: str, params: dict | None = None):
    async with engine.begin() as conn:
        result = await conn.execute(text(sql), params or {})
        # UPDATEs without RETURNING return no rows; fetchall() on them
        # raises ResourceClosedError.
        return result.fetchall() if result.returns_rows else []


async def _fetchone(engine, sql: str, params: dict | None = None):
    async with engine.connect() as conn:
        result = await conn.execute(text(sql), params or {})
        return result.mappings().first()


# ---------------------------------------------------------------------------
# 1. ClipExportRepository.insert -- make_interval(secs => :ttl_seconds)
#    (SQLite tier: strict xfail in test_sql_exec_creation_core.py)
# ---------------------------------------------------------------------------


async def test_pg_clip_export_insert_executes(pg_engine):
    sql = extract_statement(
        "repositories/clip_export_repository.py",
        "insert",
        "INSERT INTO clip_exports",
    )
    await _setup(pg_engine, "clip_exports", _CLIP_EXPORTS_PG_DDL)
    params = {
        "id": "exp-1",
        "task_id": "task-1",
        "clip_id": "clip-1",
        "user_id": "user-1",
        "preset": "vertical_hd",
        "options_hash": "hash-1",
        "source_file_path": "/media/clip1.mp4",
        "download_filename": "clip1.mp4",
        "ttl_seconds": 3600,
    }
    rows = await _execute(pg_engine, sql, params)
    assert len(rows) == 1
    row = await _fetchone(
        pg_engine, "SELECT * FROM clip_exports WHERE id = 'exp-1'"
    )
    assert row["status"] == "queued"
    assert row["progress"] == 0
    # expires_at is NOW() + make_interval(secs => 3600); both NOW()s in
    # one statement share the transaction timestamp, so the TTL lands
    # exactly -- this is the assertion SQLite could never run.
    assert row["expires_at"] - row["created_at"] == timedelta(seconds=3600)


# ---------------------------------------------------------------------------
# 2. ClipExportRepository.fail_stale_running -- runtime-assembled UPDATE
#    whose always-present clause uses make_interval(secs => ...).
#    Driven through the REAL method, as in the SQLite methods suite
#    (strict xfail in test_sql_exec_update_methods.py).
# ---------------------------------------------------------------------------


async def test_pg_clip_export_fail_stale_running_executes(pg_engine):
    await _setup(pg_engine, "clip_exports", _CLIP_EXPORTS_PG_DDL)
    seed = """
        INSERT INTO clip_exports (
            id, task_id, clip_id, user_id, preset, options_hash,
            source_file_path, status, progress, download_filename,
            created_at, updated_at, expires_at
        ) VALUES (
            :id, 'task-1', 'clip-1', 'user-1', 'vertical_hd', 'hash-1',
            '/media/clip1.mp4', :status, 40, 'clip1.mp4',
            NOW() - INTERVAL '2 hours', :updated_at, NOW() + INTERVAL '1 day'
        )
    """
    # asyncpg binds TIMESTAMPTZ params strictly: real datetimes, not
    # strings (a str raises DataError before the statement ever runs).
    long_ago = datetime(2000, 1, 1, tzinfo=timezone.utc)
    far_future = datetime(2999, 1, 1, tzinfo=timezone.utc)
    async with pg_engine.begin() as conn:
        await conn.execute(
            text(seed),
            {"id": "exp-stale", "status": "running", "updated_at": long_ago},
        )
        await conn.execute(
            text(seed),
            {"id": "exp-fresh", "status": "running", "updated_at": far_future},
        )
        await conn.execute(
            text(seed),
            {
                "id": "exp-queued-stale",
                "status": "queued",
                "updated_at": long_ago,
            },
        )
    maker = async_sessionmaker(pg_engine, expire_on_commit=False)
    async with maker() as session:
        count = await ClipExportRepository.fail_stale_running(
            session, message="worker died", stale_seconds=600
        )
        await session.commit()
    assert count == 1
    stale = await _fetchone(
        pg_engine, "SELECT * FROM clip_exports WHERE id = 'exp-stale'"
    )
    assert stale["status"] == "failed"
    assert stale["error_message"] == "worker died"
    fresh = await _fetchone(
        pg_engine, "SELECT status FROM clip_exports WHERE id = 'exp-fresh'"
    )
    assert fresh["status"] == "running"
    queued = await _fetchone(
        pg_engine, "SELECT status FROM clip_exports WHERE id = 'exp-queued-stale'"
    )
    assert queued["status"] == "queued"


# ---------------------------------------------------------------------------
# 3. workers/media_cleanup.drain_deletions -- the retry UPDATE uses
#    make_interval(secs => LEAST(21600, 60 * power(2, ...))) and the
#    claim SELECT ends FOR UPDATE SKIP LOCKED.
#    (SQLite tier: strict xfail in test_sql_exec_update_core.py)
# ---------------------------------------------------------------------------


async def test_pg_media_cleanup_drain_deletions_update_executes(pg_engine):
    sql = extract_statement(
        "workers/media_cleanup.py", "drain_deletions", "UPDATE media_deletions"
    )
    await _setup(pg_engine, "media_deletions", _MEDIA_DELETIONS_PG_DDL)
    await _execute(
        pg_engine,
        "INSERT INTO media_deletions (file_path) VALUES ('/media/old.mp4')",
    )
    result = await _execute(pg_engine, sql, {"path": "/media/old.mp4"})
    assert result == []
    row = await _fetchone(
        pg_engine,
        "SELECT attempts, next_attempt_at FROM media_deletions "
        "WHERE file_path = '/media/old.mp4'",
    )
    assert row["attempts"] == 1
    # First failure backoff: LEAST(21600, 60 * 2 ** LEAST(0, 9)) = 60s
    # from clock_timestamp() at execution time.
    delta = (
        await _fetchone(
            pg_engine,
            "SELECT next_attempt_at - clock_timestamp() AS delta "
            "FROM media_deletions WHERE file_path = '/media/old.mp4'",
        )
    )["delta"]
    assert timedelta(seconds=50) <= delta <= timedelta(seconds=65)


async def test_pg_media_cleanup_drain_deletions_claim_select_executes(pg_engine):
    sql = extract_statement(
        "workers/media_cleanup.py",
        "drain_deletions",
        "SELECT file_path, is_prefix",
    )
    await _setup(pg_engine, "media_deletions", _MEDIA_DELETIONS_PG_DDL)
    async with pg_engine.begin() as conn:
        await conn.execute(
            text(
                "INSERT INTO media_deletions (file_path, next_attempt_at) "
                "VALUES ('/media/due.mp4', NOW() - INTERVAL '1 minute')"
            )
        )
        await conn.execute(
            text(
                "INSERT INTO media_deletions (file_path, next_attempt_at, hold_token) "
                "VALUES ('/media/held.mp4', NOW() - INTERVAL '1 minute', 'tok-1')"
            )
        )
        await conn.execute(
            text(
                "INSERT INTO media_deletions (file_path, next_attempt_at) "
                "VALUES ('/media/future.mp4', NOW() + INTERVAL '1 hour')"
            )
        )
    # FOR UPDATE requires (and locks within) a transaction.
    async with pg_engine.begin() as conn:
        rows = (await conn.execute(text(sql))).fetchall()
    assert [row.file_path for row in rows] == ["/media/due.mp4"]
    assert rows[0].is_prefix is False


# ---------------------------------------------------------------------------
# 4. SocialRepository.fail_stale_publishing --
#    make_interval(secs => CAST(:seconds AS integer))
#    (SQLite tier: strict xfail in test_sql_exec_update_monitors.py)
# ---------------------------------------------------------------------------


async def test_pg_social_fail_stale_publishing_executes(pg_engine):
    sql = extract_statement(
        "repositories/social_repository.py",
        "fail_stale_publishing",
        "UPDATE social_posts",
    )
    await _setup(pg_engine, "social_posts", _SOCIAL_POSTS_PG_DDL)
    async with pg_engine.begin() as conn:
        await conn.execute(
            text(
                "INSERT INTO social_posts (id, user_id, status, claimed_at, updated_at) "
                "VALUES ('sp-1', 'user-1', 'publishing', "
                "'2000-01-01 00:00:00+00', '2000-01-01 00:00:00+00')"
            )
        )
        await conn.execute(
            text(
                "INSERT INTO social_posts (id, user_id, status, claimed_at) "
                "VALUES ('sp-2', 'user-1', 'publishing', NOW())"
            )
        )
        await conn.execute(
            text(
                "INSERT INTO social_posts (id, user_id, status, claimed_at, pending_handle) "
                "VALUES ('sp-3', 'user-1', 'publishing', "
                "'2000-01-01 00:00:00+00', 'handle-1')"
            )
        )
    rows = await _execute(
        pg_engine, sql, {"message": "worker died", "seconds": 600}
    )
    assert [row.id for row in rows] == ["sp-1"]
    failed = await _fetchone(
        pg_engine, "SELECT * FROM social_posts WHERE id = 'sp-1'"
    )
    assert failed["status"] == "failed"
    assert failed["error_message"] == "worker died"
    for post_id in ("sp-2", "sp-3"):
        other = await _fetchone(
            pg_engine,
            "SELECT status FROM social_posts WHERE id = :id",
            {"id": post_id},
        )
        assert other["status"] == "publishing"


# ---------------------------------------------------------------------------
# 5. SocialRepository.claim_due_scheduled_posts -- FOR UPDATE SKIP LOCKED
#    (SQLite tier: strict xfail in test_sql_exec_update_monitors.py)
# ---------------------------------------------------------------------------


async def test_pg_social_claim_due_scheduled_posts_executes(pg_engine):
    sql = extract_statement(
        "repositories/social_repository.py",
        "claim_due_scheduled_posts",
        "UPDATE social_posts",
    )
    await _setup(pg_engine, "social_posts", _SOCIAL_POSTS_PG_DDL)
    async with pg_engine.begin() as conn:
        await conn.execute(
            text(
                "INSERT INTO social_posts (id, user_id, status, scheduled_for) "
                "VALUES ('sp-1', 'user-1', 'scheduled', NOW() - INTERVAL '1 minute')"
            )
        )
        await conn.execute(
            text(
                "INSERT INTO social_posts (id, user_id, status, scheduled_for) "
                "VALUES ('sp-2', 'user-1', 'scheduled', NOW() + INTERVAL '1 hour')"
            )
        )
        await conn.execute(
            text(
                "INSERT INTO social_posts (id, user_id, status, scheduled_for) "
                "VALUES ('sp-3', 'user-1', 'queued', NOW() - INTERVAL '1 minute')"
            )
        )
    rows = await _execute(pg_engine, sql, {"limit": 10})
    assert [row.id for row in rows] == ["sp-1"]
    claimed = await _fetchone(
        pg_engine, "SELECT status FROM social_posts WHERE id = 'sp-1'"
    )
    assert claimed["status"] == "queued"
    future = await _fetchone(
        pg_engine, "SELECT status FROM social_posts WHERE id = 'sp-2'"
    )
    assert future["status"] == "scheduled"


# ---------------------------------------------------------------------------
# 6. pg_advisory_xact_lock -- ClipExportRepository.lock_options executes
#    SELECT pg_advisory_xact_lock(hashtextextended(:key, 902110)).
#    The same construct guards the upload reserve (upload_repository).
#    Verified behaviourally: a second transaction cannot take the same
#    lock until the first transaction ends.
# ---------------------------------------------------------------------------


async def test_pg_advisory_xact_lock_executes_and_excludes(pg_engine):
    maker = async_sessionmaker(pg_engine, expire_on_commit=False)
    key = "clip-1:hash-1"
    try_lock = text(
        "SELECT pg_try_advisory_xact_lock(hashtextextended(:key, 902110))"
    )
    async with maker() as session:
        async with session.begin():
            await ClipExportRepository.lock_options(session, "clip-1", "hash-1")
            # While the first transaction holds the lock, a try-lock
            # from an independent connection must be refused.
            async with pg_engine.connect() as other:
                async with other.begin():
                    got = (
                        await other.execute(try_lock, {"key": key})
                    ).scalar_one()
            assert got is False
    # After the holding transaction commits, the lock is free again.
    async with pg_engine.connect() as other:
        async with other.begin():
            got = (
                await other.execute(try_lock, {"key": key})
            ).scalar_one()
    assert got is True


# ---------------------------------------------------------------------------
# 7. True jsonb || merge -- TaskRepository.save_source_metadata.
#    The SQLite suite executes this statement with merge approximated
#    as text concatenation; here the real jsonb semantics are pinned:
#    shallow object merge (existing keys kept, repeated keys replaced
#    wholesale, new keys added).
# ---------------------------------------------------------------------------


async def test_pg_jsonb_merge_source_metadata_executes(pg_engine):
    sql = extract_statement(
        "repositories/task_repository.py", "save_source_metadata", "UPDATE tasks"
    )
    await _setup(pg_engine, "tasks", _TASKS_PG_DDL)
    await _execute(
        pg_engine,
        "INSERT INTO tasks (id, source_metadata) "
        "VALUES ('task-1', '{\"a\": 1, \"b\": {\"x\": 1}}'::jsonb)",
    )
    await _execute(
        pg_engine,
        sql,
        {"metadata": '{"b": {"y": 2}, "c": 3}', "task_id": "task-1"},
    )
    row = await _fetchone(
        pg_engine,
        "SELECT source_metadata::text AS merged FROM tasks WHERE id = 'task-1'",
    )
    # NOT {"b": {"x": 1, "y": 2}} -- jsonb || replaces the value of a
    # repeated top-level key wholesale.
    assert json.loads(row["merged"]) == {"a": 1, "b": {"y": 2}, "c": 3}


_LIVE_SESSIONS_PG_DDL = """
CREATE TABLE live_sessions (
    id VARCHAR(36) PRIMARY KEY,
    user_id VARCHAR(255) NOT NULL,
    url TEXT NOT NULL,
    status VARCHAR(20) NOT NULL DEFAULT 'requested',
    settings JSONB NOT NULL DEFAULT '{}'::jsonb,
    stop_requested BOOLEAN NOT NULL DEFAULT FALSE,
    worker_id VARCHAR(120),
    heartbeat_at TIMESTAMPTZ,
    recording BOOLEAN NOT NULL DEFAULT FALSE,
    buffered_seconds DOUBLE PRECISION NOT NULL DEFAULT 0,
    restarts INTEGER NOT NULL DEFAULT 0,
    last_error TEXT,
    progress_message TEXT,
    started_at TIMESTAMPTZ,
    stopped_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
)
"""


# ---------------------------------------------------------------------------
# 8. LiveRepository.claim_requested -- FOR UPDATE SKIP LOCKED
#    (SQLite tier: strict xfail in test_sql_exec_live.py)
# ---------------------------------------------------------------------------


async def test_pg_live_claim_requested_executes(pg_engine):
    sql = extract_statement(
        "repositories/live_repository.py",
        "claim_requested",
        "UPDATE live_sessions",
    )
    await _setup(pg_engine, "live_sessions", _LIVE_SESSIONS_PG_DDL)
    async with pg_engine.begin() as conn:
        await conn.execute(
            text(
                "INSERT INTO live_sessions (id, user_id, url, status, created_at) "
                "VALUES ('ls-1', 'user-1', 'https://twitch.tv/a', 'requested', "
                "NOW() - INTERVAL '2 minutes')"
            )
        )
        await conn.execute(
            text(
                "INSERT INTO live_sessions (id, user_id, url, status, created_at) "
                "VALUES ('ls-2', 'user-1', 'https://twitch.tv/b', 'requested', NOW())"
            )
        )
        await conn.execute(
            text(
                "INSERT INTO live_sessions (id, user_id, url, status, stop_requested) "
                "VALUES ('ls-3', 'user-1', 'https://twitch.tv/c', 'requested', TRUE)"
            )
        )
    # The oldest unclaimed, unstopped session is claimed first.
    rows = await _execute(pg_engine, sql, {"worker": "worker-1"})
    assert [row.id for row in rows] == ["ls-1"]
    claimed = await _fetchone(
        pg_engine, "SELECT status, worker_id FROM live_sessions WHERE id = 'ls-1'"
    )
    assert claimed["status"] == "starting" and claimed["worker_id"] == "worker-1"
    rows = await _execute(pg_engine, sql, {"worker": "worker-1"})
    assert [row.id for row in rows] == ["ls-2"]
    # The stop-requested session is never claimed.
    rows = await _execute(pg_engine, sql, {"worker": "worker-1"})
    assert rows == []


# ---------------------------------------------------------------------------
# 9. LiveRepository.fail_stale (method level) -- INTERVAL '120 seconds'
#    (SQLite tier: strict xfail in test_sql_exec_live.py)
# ---------------------------------------------------------------------------


async def test_pg_live_fail_stale_executes(pg_engine):
    await _setup(pg_engine, "live_sessions", _LIVE_SESSIONS_PG_DDL)
    async with pg_engine.begin() as conn:
        await conn.execute(
            text(
                "INSERT INTO live_sessions (id, user_id, url, status, heartbeat_at) "
                "VALUES ('ls-stale', 'user-1', 'https://twitch.tv/a', 'recording', "
                "NOW() - INTERVAL '10 minutes')"
            )
        )
        await conn.execute(
            text(
                "INSERT INTO live_sessions (id, user_id, url, status, heartbeat_at) "
                "VALUES ('ls-fresh', 'user-1', 'https://twitch.tv/b', 'recording', NOW())"
            )
        )
    session_factory = async_sessionmaker(pg_engine, expire_on_commit=False)
    async with session_factory() as session:
        count = await LiveRepository().fail_stale(session)
    assert count == 1
    stale = await _fetchone(
        pg_engine, "SELECT status, last_error FROM live_sessions WHERE id = 'ls-stale'"
    )
    assert stale["status"] == "error"
    assert "live worker stopped" in stale["last_error"]
    fresh = await _fetchone(
        pg_engine, "SELECT status FROM live_sessions WHERE id = 'ls-fresh'"
    )
    assert fresh["status"] == "recording"
