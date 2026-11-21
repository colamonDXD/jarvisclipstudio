"""Statement-execution tests: core creation paths.

Each test executes the *real* SQL text of a creation statement
(extracted from the repository source by ``sql_exec_support``) against
an in-memory SQLite engine with minimal DDL derived from the
migrations -- including the exact UNIQUE constraints the migrations
define for every ``ON CONFLICT`` target, so a conflict target with no
backing constraint fails here exactly as it would fail on Postgres.

Covered here: clip creation (both variants), sources, hook previews,
task checkpoints, upload sessions, watch-folder claims/settings,
channel monitors, and the clip-export insert (xfail: Postgres-only
``make_interval``).
"""

import pytest

from sql_exec_support import count_rows, execute, extract_statement, fresh_conn

# ---------------------------------------------------------------------------
# ClipRepository.create_clip -- primary variant (full column set)
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

_CLIP_BASE_PARAMS = {
    "id": "clip-1",
    "task_id": "task-1",
    "filename": "clip1.mp4",
    "file_path": "/media/clip1.mp4",
    "start_time": "00:00:10",
    "end_time": "00:00:40",
    "duration": 30.0,
    "text": "transcript text",
    "relevance_score": 0.91,
    "reasoning": "strong hook",
    "clip_order": 1,
}


def test_create_clip_primary_insert_executes_and_upserts():
    sql = extract_statement(
        "repositories/clip_repository.py",
        "create_clip",
        "INSERT INTO generated_clips",
        must_contain=("virality_score",),
    )
    conn = fresh_conn(_GENERATED_CLIPS_DDL)
    params = {
        **_CLIP_BASE_PARAMS,
        "virality_score": 88,
        "hook_score": 90,
        "engagement_score": 70,
        "value_score": 60,
        "shareability_score": 75,
        "hook_type": "question",
        "hook_title": "TJR Tries a $400 Sandwich",
        "niche_override": None,
        "clip_plan": '{"segments": []}',
    }
    row_id = execute(conn, sql, params).fetchone()[0]
    assert row_id == "clip-1"
    # Second call with the same (task_id, clip_order) takes the
    # ON CONFLICT DO UPDATE path -- it must execute too, and update.
    # Note: id is not in the DO UPDATE SET list, so the surviving row
    # keeps the original id and RETURNING yields it (clip-1).
    params2 = {**params, "id": "clip-2", "filename": "clip1b.mp4"}
    row_id2 = execute(conn, sql, params2).fetchone()[0]
    assert row_id2 == "clip-1"
    assert count_rows(conn, "generated_clips") == 1
    filename = conn.execute("SELECT filename FROM generated_clips").fetchone()[0]
    assert filename == "clip1b.mp4"


def test_create_clip_fallback_insert_executes_and_upserts():
    """The legacy fallback variant (42703 handler) uses the short
    column list; it must execute against the same table shape."""
    sql = extract_statement(
        "repositories/clip_repository.py",
        "create_clip",
        "INSERT INTO generated_clips",
        must_not_contain=("virality_score",),
    )
    conn = fresh_conn(_GENERATED_CLIPS_DDL)
    row_id = execute(conn, sql, dict(_CLIP_BASE_PARAMS)).fetchone()[0]
    assert row_id == "clip-1"
    params2 = {**_CLIP_BASE_PARAMS, "id": "clip-2", "filename": "clip1b.mp4"}
    execute(conn, sql, params2)
    assert count_rows(conn, "generated_clips") == 1


# ---------------------------------------------------------------------------
# SourceRepository.create_source -- both variants
# ---------------------------------------------------------------------------

_SOURCES_DDL = """
CREATE TABLE sources (
    id TEXT, type TEXT, title TEXT, url TEXT,
    created_at TEXT, updated_at TEXT
)
"""


def test_create_source_with_url_executes():
    sql = extract_statement(
        "repositories/source_repository.py",
        "create_source",
        "INSERT INTO sources",
        must_contain=(":url",),
    )
    conn = fresh_conn(_SOURCES_DDL)
    params = {
        "source_id": "src-1",
        "source_type": "youtube",
        "title": "A video",
        "url": "https://www.youtube.com/watch?v=abc",
    }
    assert execute(conn, sql, params).fetchone()[0] == "src-1"
    assert count_rows(conn, "sources") == 1


def test_create_source_without_url_executes():
    sql = extract_statement(
        "repositories/source_repository.py",
        "create_source",
        "INSERT INTO sources",
        must_not_contain=(":url",),
    )
    conn = fresh_conn(_SOURCES_DDL)
    params = {"source_id": "src-2", "source_type": "upload", "title": "Local file"}
    assert execute(conn, sql, params).fetchone()[0] == "src-2"
    assert count_rows(conn, "sources") == 1


# ---------------------------------------------------------------------------
# HookPreviewRepository.replace_previews_for_clip
# ---------------------------------------------------------------------------


def test_hook_preview_insert_executes():
    sql = extract_statement(
        "repositories/hook_preview_repository.py",
        "replace_previews_for_clip",
        "INSERT INTO clip_hook_previews",
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
    params = {
        "id": "hp-1",
        "task_id": "task-1",
        "clip_id": "clip-1",
        "batch_id": "batch-1",
        "variant": "bold_claim",
        "label": "Bold claim",
        "description": "Opens on the claim",
        "params": "{}",
        "file_name": "hp1.mp4",
        "file_path": "/media/hp1.mp4",
        "duration": 4.5,
    }
    execute(conn, sql, params)
    assert count_rows(conn, "clip_hook_previews") == 1


# ---------------------------------------------------------------------------
# TaskCheckpointRepository.save -- INSERT ... SELECT FROM tasks
# ---------------------------------------------------------------------------


def test_task_checkpoint_save_executes_insert_merge_and_replace():
    sql = extract_statement(
        "repositories/task_checkpoint_repository.py",
        "save",
        "INSERT INTO task_processing_checkpoints",
    )
    conn = fresh_conn(
        "CREATE TABLE tasks (id TEXT PRIMARY KEY)",
        """
        CREATE TABLE task_processing_checkpoints (
            task_id TEXT PRIMARY KEY, input_key TEXT, payload TEXT,
            updated_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
        """,
    )
    conn.execute("INSERT INTO tasks (id) VALUES ('task-1')")
    params = {"task_id": "task-1", "input_key": "key-a", "payload": '{"step": 1}'}
    returned = execute(conn, sql, params).fetchone()[0]
    assert returned == "task-1"
    # Same input_key: the CASE branch merges payloads with ||.
    execute(conn, sql, {**params, "payload": '{"step": 2}'})
    payload = conn.execute(
        "SELECT payload FROM task_processing_checkpoints"
    ).fetchone()[0]
    assert '{"step": 1}' in payload and '{"step": 2}' in payload
    # Different input_key: the CASE branch replaces the payload.
    execute(conn, sql, {**params, "input_key": "key-b", "payload": '{"fresh": true}'})
    payload = conn.execute(
        "SELECT payload FROM task_processing_checkpoints"
    ).fetchone()[0]
    assert payload == '{"fresh": true}'
    assert count_rows(conn, "task_processing_checkpoints") == 1


# ---------------------------------------------------------------------------
# Upload repository -- reserve / idempotency keys / claim stored upload
# ---------------------------------------------------------------------------


def test_reserve_upload_insert_executes():
    sql = extract_statement(
        "repositories/upload_repository.py",
        "reserve_upload",
        "INSERT INTO media_upload_sessions",
    )
    conn = fresh_conn(
        "CREATE TABLE media_upload_sessions (id TEXT, user_id TEXT, size INTEGER)"
    )
    execute(conn, sql, {"id": "sess-1", "owner": "user-1", "size": 12345})
    assert count_rows(conn, "media_upload_sessions") == 1


def test_remember_idempotency_key_insert_executes():
    sql = extract_statement(
        "repositories/upload_repository.py",
        "_remember_idempotency_key",
        "INSERT INTO media_upload_idempotency_keys",
    )
    conn = fresh_conn(
        """
        CREATE TABLE media_upload_idempotency_keys (
            user_id TEXT, idempotency_key TEXT, session_id TEXT,
            PRIMARY KEY (user_id, idempotency_key)
        )
        """
    )
    params = {"owner": "user-1", "key": "key-1", "session": "sess-1"}
    execute(conn, sql, params)
    execute(conn, sql, params)  # ON CONFLICT DO NOTHING path
    assert count_rows(conn, "media_upload_idempotency_keys") == 1


def test_claim_stored_upload_insert_executes():
    sql = extract_statement(
        "repositories/upload_repository.py",
        "claim_stored_upload",
        "INSERT INTO media_upload_sessions",
    )
    conn = fresh_conn(
        """
        CREATE TABLE media_upload_sessions (
            id TEXT, user_id TEXT, size INTEGER, state TEXT,
            video_path TEXT, content_sha256 TEXT, idempotency_key TEXT
        )
        """
    )
    params = {
        "id": "sess-2",
        "owner": "user-1",
        "size": 999,
        "path": "/media/v.mp4",
        "digest": "ab12",
        "key": "key-9",
    }
    execute(conn, sql, params)
    state = conn.execute("SELECT state FROM media_upload_sessions").fetchone()[0]
    assert state == "completed"


# ---------------------------------------------------------------------------
# WatchFolderRepository -- settings upsert, file claim, completed session
# ---------------------------------------------------------------------------


def test_watch_folder_settings_upsert_executes():
    sql = extract_statement(
        "repositories/watch_folder_repository.py",
        "upsert_settings",
        "INSERT INTO watch_folder_settings",
    )
    conn = fresh_conn(
        """
        CREATE TABLE watch_folder_settings (
            user_id TEXT PRIMARY KEY, enabled INTEGER, folder_path TEXT,
            niche_override TEXT, music_override TEXT, music_mode TEXT,
            target_clip_count INTEGER, processing_mode TEXT,
            updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
            recipe_id TEXT
        )
        """
    )
    params = {
        "user_id": "user-1",
        "enabled": 1,
        "folder_path": "/watch",
        "niche_override": None,
        "music_override": None,
        "music_mode": "auto",
        "target_clip_count": 10,
        "processing_mode": "balanced",
        "recipe_id": "recipe-1",
    }
    execute(conn, sql, params)
    execute(conn, sql, {**params, "folder_path": "/watch2", "recipe_id": "recipe-2"})
    assert count_rows(conn, "watch_folder_settings") == 1
    folder = conn.execute(
        "SELECT folder_path FROM watch_folder_settings"
    ).fetchone()[0]
    assert folder == "/watch2"
    recipe_id = conn.execute(
        "SELECT recipe_id FROM watch_folder_settings"
    ).fetchone()[0]
    assert recipe_id == "recipe-2"


def test_watch_folder_claim_file_insert_executes():
    sql = extract_statement(
        "repositories/watch_folder_repository.py",
        "claim_file",
        "INSERT INTO watch_folder_files",
    )
    conn = fresh_conn(
        """
        CREATE TABLE watch_folder_files (
            id TEXT, user_id TEXT, file_path TEXT, file_name TEXT,
            fingerprint TEXT, size_bytes INTEGER, mtime_ns INTEGER,
            state TEXT, task_id TEXT, upload_path TEXT, attempts INTEGER,
            last_error TEXT, created_at TEXT, updated_at TEXT,
            UNIQUE (user_id, fingerprint)
        )
        """
    )
    params = {
        "id": "file-1",
        "user_id": "user-1",
        "file_path": "/watch/a.mp4",
        "file_name": "a.mp4",
        "fingerprint": "fp-1",
        "size_bytes": 2048,
        "mtime_ns": 123456789,
    }
    row = execute(conn, sql, params).fetchone()
    assert row[0] == "file-1"
    # Duplicate claim: ON CONFLICT DO NOTHING -> no row returned.
    assert execute(conn, sql, params).fetchone() is None
    assert count_rows(conn, "watch_folder_files") == 1


def test_watch_folder_insert_completed_upload_session_executes():
    sql = extract_statement(
        "repositories/watch_folder_repository.py",
        "insert_completed_upload_session",
        "INSERT INTO media_upload_sessions",
    )
    conn = fresh_conn(
        """
        CREATE TABLE media_upload_sessions (
            id TEXT, user_id TEXT, upload_id TEXT, video_path TEXT,
            size INTEGER, state TEXT
        )
        """
    )
    params = {
        "id": "sess-3",
        "user_id": "user-1",
        "upload_id": "upl-1",
        "video_path": "/watch/a.mp4",
        "size": 2048,
    }
    execute(conn, sql, params)
    state = conn.execute("SELECT state FROM media_upload_sessions").fetchone()[0]
    assert state == "completed"


# ---------------------------------------------------------------------------
# ChannelMonitorRepository -- settings, monitor, video claims
# ---------------------------------------------------------------------------

_CHANNEL_MONITORS_DDL = """
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

_CHANNEL_VIDEOS_DDL = """
CREATE TABLE channel_monitor_videos (
    id TEXT, user_id TEXT, monitor_id TEXT, video_id TEXT,
    video_url TEXT, title TEXT, published_at TEXT, state TEXT,
    skip_reason TEXT, task_id TEXT, attempts INTEGER, last_error TEXT,
    waiting_since TEXT, created_at TEXT, updated_at TEXT,
    UNIQUE (user_id, video_id)
)
"""


def test_channel_monitor_settings_upsert_executes():
    sql = extract_statement(
        "repositories/channel_monitor_repository.py",
        "upsert_settings",
        "INSERT INTO channel_monitor_settings",
    )
    conn = fresh_conn(
        """
        CREATE TABLE channel_monitor_settings (
            user_id TEXT PRIMARY KEY, enabled INTEGER,
            updated_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    execute(conn, sql, {"user_id": "user-1", "enabled": 1})
    execute(conn, sql, {"user_id": "user-1", "enabled": 0})
    assert count_rows(conn, "channel_monitor_settings") == 1


def test_channel_create_monitor_insert_executes():
    sql = extract_statement(
        "repositories/channel_monitor_repository.py",
        "create_monitor",
        "INSERT INTO channel_monitors",
    )
    conn = fresh_conn(_CHANNEL_MONITORS_DDL)
    params = {
        "id": "mon-1",
        "user_id": "user-1",
        "channel_id": "UC123",
        "channel_title": "Some Channel",
        "source_input": "@somechannel",
        "enabled": 1,
        "niche_override": None,
        "music_mode": "auto",
        "music_override": None,
        "template_mode": "plain",
        "creation_mode": "full_auto",
        "target_clip_count": 10,
        "processing_mode": "balanced",
        "include_shorts": 0,
        "max_tasks_per_day": 3,
        "campaign_id": None,
        "brand_kit_id": None,
    }
    row = execute(conn, sql, params).fetchone()
    assert row[0] == "mon-1"
    # Same (user_id, channel_id): DO NOTHING, no row back.
    assert execute(conn, sql, params).fetchone() is None
    assert count_rows(conn, "channel_monitors") == 1


def test_channel_claim_video_insert_executes():
    sql = extract_statement(
        "repositories/channel_monitor_repository.py",
        "claim_video",
        "INSERT INTO channel_monitor_videos",
    )
    conn = fresh_conn(_CHANNEL_VIDEOS_DDL)
    params = {
        "id": "vid-row-1",
        "user_id": "user-1",
        "monitor_id": "mon-1",
        "video_id": "dQw4w9WgXcQ",
        "video_url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
        "title": "A video",
        "published_at": "2026-11-01T00:00:00Z",
    }
    row = execute(conn, sql, params).fetchone()
    assert row[0] == "vid-row-1"
    state, attempts = conn.execute(
        "SELECT state, attempts FROM channel_monitor_videos"
    ).fetchone()
    assert (state, attempts) == ("processing", 1)
    assert execute(conn, sql, params).fetchone() is None
    assert count_rows(conn, "channel_monitor_videos") == 1


def test_channel_insert_terminal_video_executes():
    sql = extract_statement(
        "repositories/channel_monitor_repository.py",
        "insert_terminal_video",
        "INSERT INTO channel_monitor_videos",
    )
    conn = fresh_conn(_CHANNEL_VIDEOS_DDL)
    params = {
        "id": "vid-row-2",
        "user_id": "user-1",
        "monitor_id": "mon-1",
        "video_id": "abc123",
        "video_url": "https://www.youtube.com/watch?v=abc123",
        "title": "A short",
        "published_at": "2026-11-01T00:00:00Z",
        "state": "skipped",
        "skip_reason": "is_short",
    }
    row = execute(conn, sql, params).fetchone()
    assert row[0] == "vid-row-2"
    assert count_rows(conn, "channel_monitor_videos") == 1


# ---------------------------------------------------------------------------
# ClipExportRepository.insert -- Postgres-only make_interval()
# ---------------------------------------------------------------------------


@pytest.mark.xfail(
    strict=True,
    reason=(
        "Postgres-only: expires_at is computed with "
        "make_interval(secs => :ttl_seconds), which has no SQLite "
        "equivalent; the statement cannot execute on the in-memory "
        "engine. Needs a Postgres-backed execution test (Nov 17 gate "
        "formalisation) -- not a product defect."
    ),
)
def test_clip_export_insert_executes():
    sql = extract_statement(
        "repositories/clip_export_repository.py",
        "insert",
        "INSERT INTO clip_exports",
    )
    conn = fresh_conn(
        """
        CREATE TABLE clip_exports (
            id TEXT, task_id TEXT, clip_id TEXT, user_id TEXT,
            preset TEXT, options_hash TEXT, source_file_path TEXT,
            status TEXT, progress INTEGER, download_filename TEXT,
            created_at TEXT, updated_at TEXT, expires_at TEXT
        )
        """
    )
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
    row = execute(conn, sql, params).fetchone()
    assert row is not None
