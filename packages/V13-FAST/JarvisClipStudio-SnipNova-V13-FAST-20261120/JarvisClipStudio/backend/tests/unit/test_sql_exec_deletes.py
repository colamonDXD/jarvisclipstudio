"""Statement-execution tests: every raw DELETE in ``backend/src``.

The INSERT and UPDATE lanes inventoried and executed those two
statement kinds (39 INSERT literals, 69 UPDATE statements), but
DELETEs were never in either inventory -- the Postgres-tier lane
found ``clip_export_repository.purge_failures`` (a ``make_interval``
DELETE) sitting outside both, executed nowhere. The canonical AST
inventory (the inventory guard's method: literals in statement
positions, docstrings excluded, deduplicated per function) finds
**18 DELETE statements across 17 (file, function) pairs**, and this
suite plus ``test_sql_exec_postgres_deletes.py`` covers all of them:

- 15 execute here, against in-memory SQLite with migration-derived
  minimal DDL, each seeded with a target row and guard rows (wrong
  owner, other parent, not-yet-due, protected status) that must
  survive -- a DELETE whose WHERE clause silently widened would pass
  a bare "it ran" check and fail these.
- 2 are Postgres-only and are documented strict xfails here; they
  pass for real in the Postgres tier
  (``test_sql_exec_postgres_deletes.py``):
  - ``api/routes/admin.py::update_runtime_settings`` --
    ``ANY(CAST(:setting_keys AS text[]))``; SQLite has no array type
    and no ``ANY`` operator.
  - ``clip_export_repository.purge_failures`` --
    ``make_interval(days => :days)`` has no SQLite equivalent.

Same method as the sibling suites: the real statement text is
extracted from source with ``sql_exec_support.extract_statement``
(never a hand-copied paraphrase) and normalised only by the shared
``to_sqlite`` rules (``NOW()`` -> ``CURRENT_TIMESTAMP``, ``::`` casts
stripped).
"""

import pytest

from sql_exec_support import count_rows, execute, extract_statement, fresh_conn


def _ids(conn, table, column="id"):
    return [
        row[0]
        for row in conn.execute(f"SELECT {column} FROM {table} ORDER BY {column}")
    ]


# ---------------------------------------------------------------------------
# BrandKitRepository.delete_kit -- owner-guarded, RETURNING id
# ---------------------------------------------------------------------------


def test_brand_kit_delete_executes():
    sql = extract_statement(
        "repositories/brand_kit_repository.py", "delete_kit", "DELETE FROM brand_kits"
    )
    conn = fresh_conn(
        """
        CREATE TABLE brand_kits (
            id TEXT, user_id TEXT, name TEXT, primary_color TEXT,
            accent_color TEXT, caption_template TEXT, font_name TEXT,
            title_style TEXT, music_track TEXT, logo_filename TEXT,
            logo_position TEXT, logo_opacity REAL, intro_asset TEXT,
            outro_asset TEXT
        )
        """
    )
    conn.execute("INSERT INTO brand_kits (id, user_id) VALUES ('kit-1', 'user-1')")
    conn.execute("INSERT INTO brand_kits (id, user_id) VALUES ('kit-2', 'user-2')")
    # Wrong owner: nothing is returned, the row survives.
    row = execute(conn, sql, {"kit_id": "kit-1", "user_id": "user-2"}).fetchone()
    assert row is None
    assert count_rows(conn, "brand_kits") == 2
    # Right owner: the id comes back and the row is gone.
    row = execute(conn, sql, {"kit_id": "kit-1", "user_id": "user-1"}).fetchone()
    assert row[0] == "kit-1"
    assert _ids(conn, "brand_kits") == ["kit-2"]


# ---------------------------------------------------------------------------
# ChannelMonitorRepository.delete_monitor -- owner-guarded
# ---------------------------------------------------------------------------


def test_channel_monitor_delete_executes():
    sql = extract_statement(
        "repositories/channel_monitor_repository.py",
        "delete_monitor",
        "DELETE FROM channel_monitors",
    )
    conn = fresh_conn(
        """
        CREATE TABLE channel_monitors (
            id TEXT, user_id TEXT, channel_id TEXT, channel_title TEXT,
            source_input TEXT, enabled INTEGER, niche_override TEXT,
            music_mode TEXT, music_override TEXT, template_mode TEXT,
            creation_mode TEXT, target_clip_count INTEGER, processing_mode TEXT,
            include_shorts INTEGER, max_tasks_per_day INTEGER, campaign_id TEXT,
            brand_kit_id TEXT, last_fallback_at TEXT, last_polled_at TEXT,
            last_error TEXT, consecutive_failures INTEGER,
            created_at TEXT, updated_at TEXT,
            UNIQUE (user_id, channel_id)
        )
        """
    )
    conn.execute(
        "INSERT INTO channel_monitors (id, user_id, channel_id)"
        " VALUES ('mon-1', 'user-1', 'UC1')"
    )
    conn.execute(
        "INSERT INTO channel_monitors (id, user_id, channel_id)"
        " VALUES ('mon-2', 'user-2', 'UC1')"
    )
    cursor = execute(conn, sql, {"user_id": "user-2", "id": "mon-1"})
    assert cursor.rowcount == 0
    cursor = execute(conn, sql, {"user_id": "user-1", "id": "mon-1"})
    assert cursor.rowcount == 1
    assert _ids(conn, "channel_monitors") == ["mon-2"]


# ---------------------------------------------------------------------------
# ClipExportRepository.delete -- by id
# ---------------------------------------------------------------------------

_CLIP_EXPORTS_DDL = """
CREATE TABLE clip_exports (
    id TEXT, task_id TEXT, clip_id TEXT, user_id TEXT,
    preset TEXT, options_hash TEXT, source_file_path TEXT,
    status TEXT, progress INTEGER, download_filename TEXT,
    created_at TEXT, updated_at TEXT, expires_at TEXT
)
"""


def test_clip_export_delete_executes():
    sql = extract_statement(
        "repositories/clip_export_repository.py", "delete", "DELETE FROM clip_exports"
    )
    conn = fresh_conn(_CLIP_EXPORTS_DDL)
    conn.execute("INSERT INTO clip_exports (id) VALUES ('exp-1')")
    conn.execute("INSERT INTO clip_exports (id) VALUES ('exp-2')")
    cursor = execute(conn, sql, {"id": "exp-1"})
    assert cursor.rowcount == 1
    assert _ids(conn, "clip_exports") == ["exp-2"]


# ---------------------------------------------------------------------------
# ClipExportRepository.purge_failures -- make_interval(days => :days):
# Postgres-only, strict xfail here; passes in the Postgres tier
# (test_sql_exec_postgres_deletes.py).
# ---------------------------------------------------------------------------


@pytest.mark.xfail(
    strict=True,
    reason=(
        "Postgres-only: the staleness window is computed with "
        "make_interval(days => :days), which has no SQLite equivalent; "
        "the statement cannot execute on the in-memory engine. Covered "
        "for real by the Postgres execution tier "
        "(test_sql_exec_postgres_deletes.py) -- not a product defect."
    ),
)
def test_clip_export_purge_failures_executes():
    sql = extract_statement(
        "repositories/clip_export_repository.py",
        "purge_failures",
        "DELETE FROM clip_exports",
    )
    conn = fresh_conn(_CLIP_EXPORTS_DDL)
    conn.execute(
        "INSERT INTO clip_exports (id, status, updated_at)"
        " VALUES ('exp-old', 'failed', '2000-01-01 00:00:00')"
    )
    execute(conn, sql, {"days": 7})
    assert count_rows(conn, "clip_exports") == 0


# ---------------------------------------------------------------------------
# ClipRepository -- delete_clips_by_task / delete_clip
# ---------------------------------------------------------------------------

_GENERATED_CLIPS_DDL = """
CREATE TABLE generated_clips (
    id TEXT, task_id TEXT, filename TEXT, file_path TEXT,
    start_time TEXT, end_time TEXT, duration REAL, text TEXT,
    relevance_score REAL, reasoning TEXT, clip_order INTEGER,
    virality_score INTEGER, hook_score INTEGER, engagement_score INTEGER,
    value_score INTEGER, shareability_score INTEGER, hook_type TEXT,
    hook_title TEXT, niche_override TEXT, clip_plan TEXT,
    created_at TEXT, updated_at TEXT,
    UNIQUE (task_id, clip_order)
)
"""


def test_delete_clips_by_task_executes():
    sql = extract_statement(
        "repositories/clip_repository.py",
        "delete_clips_by_task",
        "DELETE FROM generated_clips",
    )
    conn = fresh_conn(_GENERATED_CLIPS_DDL)
    conn.execute(
        "INSERT INTO generated_clips (id, task_id, clip_order)"
        " VALUES ('clip-1', 'task-1', 1)"
    )
    conn.execute(
        "INSERT INTO generated_clips (id, task_id, clip_order)"
        " VALUES ('clip-2', 'task-1', 2)"
    )
    conn.execute(
        "INSERT INTO generated_clips (id, task_id, clip_order)"
        " VALUES ('clip-3', 'task-2', 1)"
    )
    cursor = execute(conn, sql, {"task_id": "task-1"})
    assert cursor.rowcount == 2
    # The other task's clip is the guard row: it must survive.
    assert _ids(conn, "generated_clips") == ["clip-3"]


def test_delete_clip_executes():
    sql = extract_statement(
        "repositories/clip_repository.py", "delete_clip", "DELETE FROM generated_clips"
    )
    conn = fresh_conn(_GENERATED_CLIPS_DDL)
    conn.execute(
        "INSERT INTO generated_clips (id, task_id, clip_order)"
        " VALUES ('clip-1', 'task-1', 1)"
    )
    conn.execute(
        "INSERT INTO generated_clips (id, task_id, clip_order)"
        " VALUES ('clip-2', 'task-1', 2)"
    )
    cursor = execute(conn, sql, {"clip_id": "clip-1"})
    assert cursor.rowcount == 1
    assert _ids(conn, "generated_clips") == ["clip-2"]


# ---------------------------------------------------------------------------
# HookPreviewRepository.replace_previews_for_clip -- delete half of the
# replace (the INSERT half is covered in test_sql_exec_creation_core).
# ---------------------------------------------------------------------------


def test_hook_preview_delete_executes():
    sql = extract_statement(
        "repositories/hook_preview_repository.py",
        "replace_previews_for_clip",
        "DELETE FROM clip_hook_previews",
    )
    conn = fresh_conn(
        """
        CREATE TABLE clip_hook_previews (
            id TEXT, task_id TEXT, clip_id TEXT, batch_id TEXT,
            variant TEXT, label TEXT, description TEXT, params TEXT,
            file_name TEXT, file_path TEXT, duration REAL
        )
        """
    )
    conn.execute(
        "INSERT INTO clip_hook_previews (id, clip_id) VALUES ('hp-1', 'clip-1')"
    )
    conn.execute(
        "INSERT INTO clip_hook_previews (id, clip_id) VALUES ('hp-2', 'clip-1')"
    )
    conn.execute(
        "INSERT INTO clip_hook_previews (id, clip_id) VALUES ('hp-3', 'clip-2')"
    )
    cursor = execute(conn, sql, {"clip_id": "clip-1"})
    assert cursor.rowcount == 2
    assert _ids(conn, "clip_hook_previews") == ["hp-3"]


# ---------------------------------------------------------------------------
# PublishingRepository.delete_campaign -- owner-guarded, RETURNING id
# ---------------------------------------------------------------------------


def test_delete_campaign_executes():
    sql = extract_statement(
        "repositories/publishing_repository.py",
        "delete_campaign",
        "DELETE FROM campaigns",
    )
    conn = fresh_conn(
        """
        CREATE TABLE campaigns (
            id TEXT, user_id TEXT, name TEXT, platform TEXT,
            campaign_url TEXT, budget REAL, notes TEXT, brief TEXT,
            example_url TEXT
        )
        """
    )
    conn.execute("INSERT INTO campaigns (id, user_id) VALUES ('camp-1', 'user-1')")
    conn.execute("INSERT INTO campaigns (id, user_id) VALUES ('camp-2', 'user-2')")
    row = execute(
        conn, sql, {"campaign_id": "camp-1", "user_id": "user-2"}
    ).fetchone()
    assert row is None
    assert count_rows(conn, "campaigns") == 2
    row = execute(
        conn, sql, {"campaign_id": "camp-1", "user_id": "user-1"}
    ).fetchone()
    assert row[0] == "camp-1"
    assert _ids(conn, "campaigns") == ["camp-2"]


# ---------------------------------------------------------------------------
# PublishingRepository.delete_post -- two DELETEs in one method: the
# stats sweep (unguarded by owner) and the post itself (owner-guarded,
# RETURNING id).
# ---------------------------------------------------------------------------


def test_delete_post_stats_executes():
    sql = extract_statement(
        "repositories/publishing_repository.py",
        "delete_post",
        "DELETE FROM clip_post_stats",
    )
    conn = fresh_conn(
        """
        CREATE TABLE clip_post_stats (
            post_id TEXT PRIMARY KEY, views INTEGER, likes INTEGER,
            comments INTEGER, earnings REAL, updated_at TEXT
        )
        """
    )
    conn.execute("INSERT INTO clip_post_stats (post_id) VALUES ('post-1')")
    conn.execute("INSERT INTO clip_post_stats (post_id) VALUES ('post-2')")
    cursor = execute(conn, sql, {"post_id": "post-1"})
    assert cursor.rowcount == 1
    assert _ids(conn, "clip_post_stats", "post_id") == ["post-2"]


def test_delete_post_row_executes():
    sql = extract_statement(
        "repositories/publishing_repository.py", "delete_post", "DELETE FROM clip_posts"
    )
    conn = fresh_conn(
        """
        CREATE TABLE clip_posts (
            id TEXT, clip_id TEXT, user_id TEXT, platform TEXT,
            post_url TEXT, posted_at TEXT, campaign_id TEXT,
            submission_url TEXT, submission_dismissed INTEGER,
            updated_at TEXT,
            UNIQUE (clip_id, platform)
        )
        """
    )
    conn.execute("INSERT INTO clip_posts (id, user_id) VALUES ('post-1', 'user-1')")
    conn.execute("INSERT INTO clip_posts (id, user_id) VALUES ('post-2', 'user-2')")
    row = execute(conn, sql, {"post_id": "post-1", "user_id": "user-2"}).fetchone()
    assert row is None
    assert count_rows(conn, "clip_posts") == 2
    row = execute(conn, sql, {"post_id": "post-1", "user_id": "user-1"}).fetchone()
    assert row[0] == "post-1"
    assert _ids(conn, "clip_posts") == ["post-2"]


# ---------------------------------------------------------------------------
# SocialRepository -- consume_oauth_state (DELETE ... RETURNING the row),
# purge_expired_oauth_states (due-date guard), delete_post (owner +
# status guards, RETURNING id).
# ---------------------------------------------------------------------------

_OAUTH_STATES_DDL = """
CREATE TABLE social_oauth_states (
    state TEXT, user_id TEXT, provider TEXT, code_verifier TEXT,
    redirect_uri TEXT, created_at TEXT, expires_at TEXT
)
"""


def test_consume_oauth_state_delete_executes():
    sql = extract_statement(
        "repositories/social_repository.py",
        "consume_oauth_state",
        "DELETE FROM social_oauth_states",
    )
    conn = fresh_conn(_OAUTH_STATES_DDL)
    conn.execute(
        "INSERT INTO social_oauth_states"
        " (state, user_id, provider, code_verifier, redirect_uri, expires_at)"
        " VALUES ('state-1', 'user-1', 'youtube', 'verifier',"
        " 'http://localhost:3107/callback', '2999-01-01 00:00:00')"
    )
    conn.execute(
        "INSERT INTO social_oauth_states (state, user_id) VALUES ('state-2', 'user-2')"
    )
    row = execute(conn, sql, {"state": "state-1"}).fetchone()
    assert row[0] == "state-1"
    assert row[1] == "user-1"
    assert row[2] == "youtube"
    # Consumed means gone; the other state is untouched.
    assert _ids(conn, "social_oauth_states", "state") == ["state-2"]
    # A second consume of the same state returns nothing.
    assert execute(conn, sql, {"state": "state-1"}).fetchone() is None


def test_purge_expired_oauth_states_delete_executes():
    sql = extract_statement(
        "repositories/social_repository.py",
        "purge_expired_oauth_states",
        "DELETE FROM social_oauth_states",
    )
    conn = fresh_conn(_OAUTH_STATES_DDL)
    conn.execute(
        "INSERT INTO social_oauth_states (state, expires_at)"
        " VALUES ('state-old', '2000-01-01 00:00:00')"
    )
    conn.execute(
        "INSERT INTO social_oauth_states (state, expires_at)"
        " VALUES ('state-fresh', '2999-01-01 00:00:00')"
    )
    rows = execute(conn, sql, {}).fetchall()
    # Only the expired state is purged (and returned); the fresh one
    # is the not-yet-due guard row.
    assert [row[0] for row in rows] == ["state-old"]
    assert _ids(conn, "social_oauth_states", "state") == ["state-fresh"]


_SOCIAL_POSTS_DDL = """
CREATE TABLE social_posts (
    id TEXT, user_id TEXT, task_id TEXT, clip_id TEXT,
    social_account_id TEXT, provider TEXT, status TEXT,
    scheduled_for TEXT, title TEXT, caption TEXT, hashtags TEXT,
    privacy_level TEXT, media_token TEXT, source_file_path TEXT,
    hook_type TEXT, hook_title TEXT, virality_score INTEGER,
    hook_score INTEGER, engagement_score INTEGER,
    value_score INTEGER, shareability_score INTEGER,
    clip_duration REAL, created_at TEXT, updated_at TEXT
)
"""


def test_social_delete_post_executes():
    sql = extract_statement(
        "repositories/social_repository.py", "delete_post", "DELETE FROM social_posts"
    )
    conn = fresh_conn(_SOCIAL_POSTS_DDL)
    conn.execute(
        "INSERT INTO social_posts (id, user_id, status)"
        " VALUES ('sp-1', 'user-1', 'posted')"
    )
    conn.execute(
        "INSERT INTO social_posts (id, user_id, status)"
        " VALUES ('sp-2', 'user-1', 'queued')"
    )
    conn.execute(
        "INSERT INTO social_posts (id, user_id, status)"
        " VALUES ('sp-3', 'user-2', 'posted')"
    )
    # Status guard: a queued post is the worker's, not deletable here.
    row = execute(conn, sql, {"id": "sp-2", "user_id": "user-1"}).fetchone()
    assert row is None
    # Owner guard: someone else's posted post survives.
    row = execute(conn, sql, {"id": "sp-3", "user_id": "user-1"}).fetchone()
    assert row is None
    assert count_rows(conn, "social_posts") == 3
    # The deletable row goes, RETURNING its id.
    row = execute(conn, sql, {"id": "sp-1", "user_id": "user-1"}).fetchone()
    assert row[0] == "sp-1"
    assert _ids(conn, "social_posts") == ["sp-2", "sp-3"]


# ---------------------------------------------------------------------------
# TaskRepository.delete_task -- by id
# ---------------------------------------------------------------------------


def test_delete_task_executes():
    sql = extract_statement(
        "repositories/task_repository.py", "delete_task", "DELETE FROM tasks"
    )
    conn = fresh_conn("CREATE TABLE tasks (id TEXT PRIMARY KEY)")
    conn.execute("INSERT INTO tasks (id) VALUES ('task-1')")
    conn.execute("INSERT INTO tasks (id) VALUES ('task-2')")
    cursor = execute(conn, sql, {"task_id": "task-1"})
    assert cursor.rowcount == 1
    assert _ids(conn, "tasks") == ["task-2"]


# ---------------------------------------------------------------------------
# workers/media_cleanup -- the drain's success-path DELETE and the
# upload-session expiry DELETE.
# ---------------------------------------------------------------------------


def test_media_deletions_drain_delete_executes():
    sql = extract_statement(
        "workers/media_cleanup.py", "drain_deletions", "DELETE FROM media_deletions"
    )
    conn = fresh_conn(
        """
        CREATE TABLE media_deletions (
            file_path TEXT PRIMARY KEY, is_prefix INTEGER,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            next_attempt_at TEXT DEFAULT CURRENT_TIMESTAMP,
            attempts INTEGER DEFAULT 0, hold_token TEXT
        )
        """
    )
    conn.execute("INSERT INTO media_deletions (file_path) VALUES ('/media/a.mp4')")
    conn.execute("INSERT INTO media_deletions (file_path) VALUES ('/media/b.mp4')")
    cursor = execute(conn, sql, {"path": "/media/a.mp4"})
    assert cursor.rowcount == 1
    assert _ids(conn, "media_deletions", "file_path") == ["/media/b.mp4"]


def test_expire_upload_session_delete_executes():
    sql = extract_statement(
        "workers/media_cleanup.py",
        "_expire_one_upload_session",
        "DELETE FROM media_upload_sessions",
    )
    conn = fresh_conn(
        "CREATE TABLE media_upload_sessions (id TEXT, user_id TEXT, size INTEGER)"
    )
    conn.execute("INSERT INTO media_upload_sessions (id) VALUES ('sess-1')")
    conn.execute("INSERT INTO media_upload_sessions (id) VALUES ('sess-2')")
    cursor = execute(conn, sql, {"id": "sess-1"})
    assert cursor.rowcount == 1
    assert _ids(conn, "media_upload_sessions") == ["sess-2"]


# ---------------------------------------------------------------------------
# api/routes/admin.py::update_runtime_settings -- the delete_keys half:
# ANY(CAST(:setting_keys AS text[])) is Postgres-only, strict xfail
# here; passes for real in the Postgres tier
# (test_sql_exec_postgres_deletes.py).
# ---------------------------------------------------------------------------


@pytest.mark.xfail(
    strict=True,
    reason=(
        "Postgres-only: the key set is matched with "
        "ANY(CAST(:setting_keys AS text[])); SQLite has no array type "
        "and no ANY operator, so the statement cannot execute on the "
        "in-memory engine. Covered for real by the Postgres execution "
        "tier (test_sql_exec_postgres_deletes.py) -- not a product "
        "defect."
    ),
)
def test_admin_delete_keys_executes():
    sql = extract_statement(
        "api/routes/admin.py", "update_runtime_settings", "DELETE FROM app_settings"
    )
    conn = fresh_conn(
        """
        CREATE TABLE app_settings (
            setting_key TEXT PRIMARY KEY, encrypted_value TEXT,
            updated_by TEXT, updated_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    conn.execute("INSERT INTO app_settings (setting_key) VALUES ('llm_provider')")
    execute(conn, sql, {"setting_keys": ["llm_provider"]})
    assert count_rows(conn, "app_settings") == 0


# ---------------------------------------------------------------------------
# RecipeRepository.delete_recipe -- owner-guarded, RETURNING id
# ---------------------------------------------------------------------------


def test_recipe_delete_executes():
    sql = extract_statement(
        "repositories/recipe_repository.py", "delete_recipe", "DELETE FROM recipes"
    )
    conn = fresh_conn(
        """
        CREATE TABLE recipes (
            id TEXT PRIMARY KEY, user_id TEXT, name TEXT, settings TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    conn.execute("INSERT INTO recipes (id, user_id) VALUES ('recipe-1', 'user-1')")
    conn.execute("INSERT INTO recipes (id, user_id) VALUES ('recipe-2', 'user-2')")
    # Wrong owner: nothing is returned, the row survives.
    row = execute(conn, sql, {"recipe_id": "recipe-1", "user_id": "user-2"}).fetchone()
    assert row is None
    assert count_rows(conn, "recipes") == 2
    # Right owner: the id comes back and the row is gone.
    row = execute(conn, sql, {"recipe_id": "recipe-1", "user_id": "user-1"}).fetchone()
    assert row[0] == "recipe-1"
    assert _ids(conn, "recipes") == ["recipe-2"]
