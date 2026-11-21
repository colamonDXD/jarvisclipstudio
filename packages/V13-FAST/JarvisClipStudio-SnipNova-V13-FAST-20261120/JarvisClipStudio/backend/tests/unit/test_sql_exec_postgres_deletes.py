"""Postgres execution tier: the raw DELETEs and the two UPDATE
remainders the SQLite suites cannot run.

DELETE statements were never part of the INSERT/UPDATE inventories;
the canonical AST inventory finds **18 DELETE statements** in
``backend/src``. Fifteen execute on SQLite in
``test_sql_exec_deletes.py``; the two below are Postgres-only and are
documented strict xfails there:

- ``clip_export_repository.purge_failures`` --
  ``make_interval(days => :days)`` (the statement that sat outside
  both original inventories, executed nowhere, until this lane).
- ``api/routes/admin.py::update_runtime_settings`` (delete_keys) --
  ``ANY(CAST(:setting_keys AS text[]))``.

This module also closes the two variant remainders the UPDATE lane
documented in ``test_sql_exec_update_methods.py``:

- ``TaskRepository.update_task_status(..., expected_statuses=[...])``
  appends ``status = ANY(:expected_statuses)`` and binds a Python
  list -- executed here through the REAL method on a real
  ``AsyncSession`` (the assembled statement has no fixed text to
  extract).
- ``SocialRepository.update_post`` with a ``hashtags`` key binds a
  Python list to the ``TEXT[]`` column -- likewise driven through the
  real method; the value read back must be the same list, proving the
  array binding round-trips rather than being stored as text.

OPT-IN, same discipline as ``test_sql_exec_postgres_tier.py``: the
whole module skips unless ``SQL_EXEC_PG_URL`` points at a reachable
PostgreSQL, so the default gate stays green with no Postgres. The
fixture/helper block is duplicated from that module on purpose (the
tier files are self-contained). DDL is minimal and derived from the
migrations, with real Postgres types preserved; nothing is
normalised or transpiled.
"""

from __future__ import annotations

import os

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from sql_exec_support import extract_statement

from src.repositories.social_repository import SocialRepository
from src.repositories.task_repository import TaskRepository

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

_APP_SETTINGS_PG_DDL = """
CREATE TABLE app_settings (
    setting_key VARCHAR(100) PRIMARY KEY,
    encrypted_value TEXT NOT NULL,
    prefer_admin_value BOOLEAN NOT NULL DEFAULT false,
    updated_by VARCHAR(36),
    created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP
)
"""

_TASKS_PG_DDL = """
CREATE TABLE tasks (
    id VARCHAR(36) PRIMARY KEY,
    user_id VARCHAR(36),
    status VARCHAR(32),
    progress INTEGER,
    progress_message TEXT,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
)
"""

_SOCIAL_POSTS_PG_DDL = """
CREATE TABLE social_posts (
    id VARCHAR(36) PRIMARY KEY,
    title TEXT,
    hashtags TEXT[] NOT NULL DEFAULT '{}',
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
)
"""


@pytest.fixture()
async def pg_engine():
    """A live test Postgres, or a clean skip (see module docstring)."""
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


async def _rowcount(engine, sql: str, params: dict | None = None) -> int:
    async with engine.begin() as conn:
        result = await conn.execute(text(sql), params or {})
        return result.rowcount


async def _fetchone(engine, sql: str, params: dict | None = None):
    async with engine.connect() as conn:
        result = await conn.execute(text(sql), params or {})
        return result.mappings().first()


# ---------------------------------------------------------------------------
# 1. ClipExportRepository.purge_failures -- DELETE with
#    make_interval(days => :days)
#    (SQLite tier: strict xfail in test_sql_exec_deletes.py)
# ---------------------------------------------------------------------------


async def test_pg_clip_export_purge_failures_executes(pg_engine):
    sql = extract_statement(
        "repositories/clip_export_repository.py",
        "purge_failures",
        "DELETE FROM clip_exports",
    )
    await _setup(pg_engine, "clip_exports", _CLIP_EXPORTS_PG_DDL)
    seed = """
        INSERT INTO clip_exports (
            id, task_id, clip_id, user_id, preset, options_hash,
            source_file_path, status, download_filename, updated_at,
            expires_at
        ) VALUES (
            :id, 'task-1', 'clip-1', 'user-1', 'vertical_hd', 'hash-1',
            '/media/clip1.mp4', :status, 'clip1.mp4', :updated_at,
            NOW() + INTERVAL '1 day'
        )
    """
    from datetime import datetime, timedelta, timezone

    now = datetime.now(timezone.utc)
    async with pg_engine.begin() as conn:
        await conn.execute(
            text(seed),
            {
                "id": "exp-old-failed",
                "status": "failed",
                "updated_at": now - timedelta(days=10),
            },
        )
        await conn.execute(
            text(seed),
            {
                "id": "exp-fresh-failed",
                "status": "failed",
                "updated_at": now,
            },
        )
        await conn.execute(
            text(seed),
            {
                "id": "exp-old-succeeded",
                "status": "succeeded",
                "updated_at": now - timedelta(days=10),
            },
        )
    deleted = await _rowcount(pg_engine, sql, {"days": 7})
    assert deleted == 1
    remaining = await _fetchone(
        pg_engine, "SELECT string_agg(id, ',' ORDER BY id) AS ids FROM clip_exports"
    )
    # The fresh failure (inside the 7-day window) and the old
    # non-failure both survive: only status AND age together purge.
    assert remaining["ids"] == "exp-fresh-failed,exp-old-succeeded"


# ---------------------------------------------------------------------------
# 2. admin update_runtime_settings delete_keys --
#    DELETE ... WHERE setting_key = ANY(CAST(:setting_keys AS text[]))
#    (SQLite tier: strict xfail in test_sql_exec_deletes.py)
# ---------------------------------------------------------------------------


async def test_pg_admin_delete_keys_executes(pg_engine):
    sql = extract_statement(
        "api/routes/admin.py", "update_runtime_settings", "DELETE FROM app_settings"
    )
    await _setup(pg_engine, "app_settings", _APP_SETTINGS_PG_DDL)
    async with pg_engine.begin() as conn:
        for key in ("llm_provider", "whisper_model", "keep_me"):
            await conn.execute(
                text(
                    "INSERT INTO app_settings (setting_key, encrypted_value)"
                    " VALUES (:key, 'enc:v1:x')"
                ),
                {"key": key},
            )
    # The route binds payload.delete_keys -- a plain Python list --
    # exactly as done here.
    deleted = await _rowcount(
        pg_engine, sql, {"setting_keys": ["llm_provider", "whisper_model"]}
    )
    assert deleted == 2
    row = await _fetchone(pg_engine, "SELECT setting_key FROM app_settings")
    assert row["setting_key"] == "keep_me"


# ---------------------------------------------------------------------------
# 3. TaskRepository.update_task_status with expected_statuses -- the
#    ANY(:expected_statuses) variant, driven through the REAL method.
#    (SQLite methods suite: documented remainder in
#    test_sql_exec_update_methods.py)
# ---------------------------------------------------------------------------


async def test_pg_update_task_status_any_variant_executes(pg_engine):
    await _setup(pg_engine, "tasks", _TASKS_PG_DDL)
    async with pg_engine.begin() as conn:
        await conn.execute(
            text("INSERT INTO tasks (id, status) VALUES ('task-1', 'queued')")
        )
        await conn.execute(
            text("INSERT INTO tasks (id, status) VALUES ('task-2', 'processing')")
        )
    maker = async_sessionmaker(pg_engine, expire_on_commit=False)
    async with maker() as session:
        # Current status is in the expected set: the update lands.
        changed = await TaskRepository.update_task_status(
            session,
            "task-1",
            "processing",
            progress=10,
            expected_statuses=["queued", "pending"],
        )
        assert changed is True
        # Current status NOT in the expected set: no row changes and
        # the method reports False -- the ANY guard is the whole point
        # of the variant (stale-worker protection).
        changed = await TaskRepository.update_task_status(
            session,
            "task-2",
            "completed",
            expected_statuses=["queued"],
        )
        assert changed is False
        # task-1 has moved on to 'processing', so a retry expecting
        # 'queued' must also miss.
        changed = await TaskRepository.update_task_status(
            session,
            "task-1",
            "completed",
            expected_statuses=["queued"],
        )
        assert changed is False
    row = await _fetchone(
        pg_engine, "SELECT status, progress FROM tasks WHERE id = 'task-1'"
    )
    assert row["status"] == "processing"
    assert row["progress"] == 10
    row = await _fetchone(
        pg_engine, "SELECT status FROM tasks WHERE id = 'task-2'"
    )
    assert row["status"] == "processing"


# ---------------------------------------------------------------------------
# 4. SocialRepository.update_post with a hashtags key -- the TEXT[]
#    binding, driven through the REAL method.
#    (SQLite methods suite: documented remainder in
#    test_sql_exec_update_methods.py)
# ---------------------------------------------------------------------------


async def test_pg_social_update_post_hashtags_binding_executes(pg_engine):
    await _setup(pg_engine, "social_posts", _SOCIAL_POSTS_PG_DDL)
    async with pg_engine.begin() as conn:
        await conn.execute(
            text("INSERT INTO social_posts (id, title) VALUES ('sp-1', 'old title')")
        )
    maker = async_sessionmaker(pg_engine, expire_on_commit=False)
    async with maker() as session:
        # The method binds list(value) to the TEXT[] column and
        # commits itself.
        await SocialRepository.update_post(
            session, "sp-1", hashtags=["alpha", "beta"], title="New title"
        )
    row = await _fetchone(
        pg_engine, "SELECT title, hashtags FROM social_posts WHERE id = 'sp-1'"
    )
    assert row["title"] == "New title"
    # Read back as a real array, element for element -- not a
    # stringified list, not a joined string.
    assert list(row["hashtags"]) == ["alpha", "beta"]
    async with maker() as session:
        # None binds the empty list (list(value or [])), clearing the
        # tags rather than writing NULL.
        await SocialRepository.update_post(session, "sp-1", hashtags=None)
    row = await _fetchone(
        pg_engine, "SELECT hashtags FROM social_posts WHERE id = 'sp-1'"
    )
    assert list(row["hashtags"]) == []
