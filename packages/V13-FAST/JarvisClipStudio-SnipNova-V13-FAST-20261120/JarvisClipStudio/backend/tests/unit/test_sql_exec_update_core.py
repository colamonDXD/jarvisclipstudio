"""Statement-execution tests: UPDATE statements, core tables.

Continuation of the INSERT lane (``test_sql_exec_creation_*``): each
test extracts the *real* UPDATE text from the repository source,
seeds a row in the pre-state, executes the statement against an
in-memory SQLite engine with migration-derived minimal DDL, and
asserts the row changed the way the statement intends -- including
the guard behaviour (a row the WHERE clause excludes stays
untouched). Postgres-only statements are documented strict xfails,
never silently dropped.
"""

import pytest

from sql_exec_support import execute, extract_statement, fresh_conn

_TASKS_DDL = """
CREATE TABLE tasks (
    id TEXT PRIMARY KEY, user_id TEXT, status TEXT,
    brand_kit_id TEXT, generated_clips_ids TEXT, brief_report TEXT,
    classification TEXT, source_metadata TEXT,
    font_family TEXT, font_size REAL, font_color TEXT,
    caption_template TEXT, include_broll INTEGER,
    share_token TEXT, share_enabled INTEGER,
    completion_notification_sent_at TEXT,
    source_id TEXT, source_duration_seconds INTEGER,
    created_at TEXT, updated_at TEXT
)
"""

_GENERATED_CLIPS_DDL = """
CREATE TABLE generated_clips (
    id TEXT PRIMARY KEY, task_id TEXT, filename TEXT, file_path TEXT,
    start_time TEXT, end_time TEXT, duration REAL, text TEXT,
    niche_override TEXT, hook_variant TEXT, hook_variant_batch TEXT,
    qc_status TEXT, qc_report TEXT, compliance_status TEXT,
    compliance_report TEXT, clip_order INTEGER, updated_at TEXT
)
"""

_CLIP_EXPORTS_DDL = """
CREATE TABLE clip_exports (
    id TEXT PRIMARY KEY, task_id TEXT, clip_id TEXT, user_id TEXT,
    status TEXT, progress INTEGER, file_path TEXT, error_message TEXT,
    updated_at TEXT
)
"""

_UPLOAD_SESSIONS_DDL = """
CREATE TABLE media_upload_sessions (
    id TEXT PRIMARY KEY, user_id TEXT, state TEXT, upload_id TEXT,
    video_path TEXT, idempotency_key TEXT, storage_reference TEXT
)
"""


def _seed_task(conn, **overrides):
    row = {
        "id": "task-1", "user_id": "user-1", "status": "processing",
        "brand_kit_id": "kit-1", "generated_clips_ids": "[]",
        "brief_report": None, "classification": None,
        "source_metadata": "{}", "font_family": "Inter",
        "font_size": 42.0, "font_color": "#FFFFFF",
        "caption_template": "default", "include_broll": 1,
        "share_token": None, "share_enabled": 0,
        "completion_notification_sent_at": None,
        "source_id": "src-1", "source_duration_seconds": 100,
        "created_at": "2026-11-01 00:00:00", "updated_at": "2026-11-01 00:00:00",
    }
    row.update(overrides)
    cols = ", ".join(row)
    marks = ", ".join(f":{c}" for c in row)
    conn.execute(f"INSERT INTO tasks ({cols}) VALUES ({marks})", row)
    return row


def _task_field(conn, field, task_id="task-1"):
    return conn.execute(
        f"SELECT {field} FROM tasks WHERE id = ?", (task_id,)
    ).fetchone()[0]


# ---------------------------------------------------------------------------
# Admin settings / API keys / users
# ---------------------------------------------------------------------------


def test_admin_update_runtime_settings_executes():
    sql = extract_statement(
        "api/routes/admin.py", "update_runtime_settings", "UPDATE app_settings"
    )
    conn = fresh_conn(
        """
        CREATE TABLE app_settings (
            setting_key TEXT PRIMARY KEY, encrypted_value TEXT,
            prefer_admin_value INTEGER, updated_by TEXT, updated_at TEXT
        )
        """
    )
    conn.execute(
        "INSERT INTO app_settings VALUES ('llm_provider', 'enc:x', 0, 'seed', '2026-11-01')"
    )
    cur = execute(
        conn, sql,
        {"prefer_admin_value": 1, "updated_by": "admin-9", "setting_key": "llm_provider"},
    )
    assert cur.rowcount == 1
    row = conn.execute(
        "SELECT prefer_admin_value, updated_by FROM app_settings"
    ).fetchone()
    assert row == (1, "admin-9")


def test_api_key_resolve_user_id_touches_last_used():
    sql = extract_statement(
        "repositories/api_key_repository.py", "resolve_user_id", "UPDATE api_keys"
    )
    conn = fresh_conn(
        "CREATE TABLE api_keys (id TEXT, user_id TEXT, key_hash TEXT,"
        " last_used_at TEXT, revoked_at TEXT)"
    )
    conn.execute("INSERT INTO api_keys VALUES ('k1', 'user-1', 'hash-1', NULL, NULL)")
    cur = execute(conn, sql, {"key_hash": "hash-1"})
    assert cur.rowcount == 1
    assert conn.execute("SELECT last_used_at FROM api_keys").fetchone()[0] is not None
    # Unknown hash touches nothing.
    assert execute(conn, sql, {"key_hash": "nope"}).rowcount == 0


def test_api_key_revoke_executes_and_guard_blocks_second_revoke():
    sql = extract_statement(
        "repositories/api_key_repository.py", "revoke_api_key", "UPDATE api_keys"
    )
    conn = fresh_conn(
        "CREATE TABLE api_keys (id TEXT, user_id TEXT, key_hash TEXT,"
        " last_used_at TEXT, revoked_at TEXT)"
    )
    conn.execute("INSERT INTO api_keys VALUES ('k1', 'user-1', 'hash-1', NULL, NULL)")
    row = execute(conn, sql, {"id": "k1", "user_id": "user-1"}).fetchone()
    assert row[0] == "k1"  # RETURNING id
    # Already revoked -> the IS NULL guard blocks a second revoke.
    assert execute(conn, sql, {"id": "k1", "user_id": "user-1"}).fetchone() is None
    # Another user's key is out of reach.
    assert execute(conn, sql, {"id": "k1", "user_id": "user-2"}).fetchone() is None


def test_email_unsubscribe_executes_once():
    sql = extract_statement(
        "repositories/email_preference_repository.py",
        "unsubscribe_from_product_updates",
        "UPDATE users",
    )
    conn = fresh_conn(
        "CREATE TABLE users (id TEXT, email TEXT,"
        " product_updates_unsubscribed_at TEXT)"
    )
    conn.execute("INSERT INTO users VALUES ('user-1', 'a@example.com', NULL)")
    execute(conn, sql, {"user_id": "user-1", "email": "a@example.com"})
    first = conn.execute(
        "SELECT product_updates_unsubscribed_at FROM users"
    ).fetchone()[0]
    assert first is not None
    # COALESCE keeps the original timestamp on a repeat call.
    execute(conn, sql, {"user_id": "user-1", "email": "a@example.com"})
    assert conn.execute(
        "SELECT product_updates_unsubscribed_at FROM users"
    ).fetchone()[0] == first
    # Wrong email touches nothing.
    conn.execute("INSERT INTO users VALUES ('user-2', 'b@example.com', NULL)")
    execute(conn, sql, {"user_id": "user-2", "email": "wrong@example.com"})
    assert conn.execute(
        "SELECT product_updates_unsubscribed_at FROM users WHERE id = 'user-2'"
    ).fetchone()[0] is None


# ---------------------------------------------------------------------------
# TaskRepository fixed-text UPDATEs
# ---------------------------------------------------------------------------


def test_task_update_brief_report_executes():
    sql = extract_statement(
        "repositories/task_repository.py", "update_brief_report", "UPDATE tasks"
    )
    conn = fresh_conn(_TASKS_DDL)
    _seed_task(conn)
    execute(conn, sql, {"brief_report": '{"ok": true}', "task_id": "task-1"})
    assert _task_field(conn, "brief_report") == '{"ok": true}'


def test_task_update_classification_executes():
    sql = extract_statement(
        "repositories/task_repository.py", "update_classification", "UPDATE tasks"
    )
    conn = fresh_conn(_TASKS_DDL)
    _seed_task(conn)
    execute(
        conn, sql,
        {"classification": '{"niche": "finance_business"}', "task_id": "task-1"},
    )
    assert _task_field(conn, "classification") == '{"niche": "finance_business"}'


def test_task_save_source_metadata_executes():
    sql = extract_statement(
        "repositories/task_repository.py", "save_source_metadata", "UPDATE tasks"
    )
    conn = fresh_conn(_TASKS_DDL)
    _seed_task(conn, source_metadata='{"creator_name": "TJR"}')
    execute(conn, sql, {"metadata": '{"duration": 2365}', "task_id": "task-1"})
    merged = _task_field(conn, "source_metadata")
    # On Postgres this is a jsonb || merge; the SQLite run approximates
    # it as text concatenation (see sql_exec_support docstring) -- what
    # is pinned here is that the statement executes and writes.
    assert merged.startswith('{"creator_name": "TJR"}')
    assert '{"duration": 2365}' in merged


def test_task_update_settings_full_variant_executes():
    sql = extract_statement(
        "repositories/task_repository.py",
        "update_task_settings",
        "UPDATE tasks",
        must_contain=("include_broll",),
    )
    conn = fresh_conn(_TASKS_DDL)
    _seed_task(conn)
    execute(
        conn, sql,
        {
            "font_family": "Poppins", "font_size": 64.0, "font_color": "#FFD500",
            "caption_template": "karaoke", "include_broll": 0, "task_id": "task-1",
        },
    )
    row = conn.execute(
        "SELECT font_family, font_size, font_color, caption_template,"
        " include_broll FROM tasks"
    ).fetchone()
    assert row == ("Poppins", 64.0, "#FFD500", "karaoke", 0)


def test_task_update_settings_font_only_variant_executes():
    sql = extract_statement(
        "repositories/task_repository.py",
        "update_task_settings",
        "UPDATE tasks",
        must_not_contain=("include_broll", "caption_template"),
    )
    conn = fresh_conn(_TASKS_DDL)
    _seed_task(conn)
    execute(
        conn, sql,
        {
            "font_family": "Anton", "font_size": 72.0, "font_color": "#38E1FF",
            "task_id": "task-1",
        },
    )
    row = conn.execute(
        "SELECT font_family, font_size, font_color, caption_template FROM tasks"
    ).fetchone()
    assert row == ("Anton", 72.0, "#38E1FF", "default")  # template untouched


def test_task_update_task_clips_executes():
    sql = extract_statement(
        "repositories/task_repository.py", "update_task_clips", "UPDATE tasks"
    )
    conn = fresh_conn(_TASKS_DDL)
    _seed_task(conn)
    # Production binds the list for the jsonb column; SQLite stores the
    # serialised form -- the statement shape is what is under test.
    execute(conn, sql, {"clip_ids": '["clip-1", "clip-2"]', "task_id": "task-1"})
    assert _task_field(conn, "generated_clips_ids") == '["clip-1", "clip-2"]'


def test_task_enable_and_disable_sharing_execute():
    enable_sql = extract_statement(
        "repositories/task_repository.py", "enable_sharing", "UPDATE tasks"
    )
    disable_sql = extract_statement(
        "repositories/task_repository.py", "disable_sharing", "UPDATE tasks"
    )
    conn = fresh_conn(_TASKS_DDL)
    _seed_task(conn)
    execute(conn, enable_sql, {"share_token": "tok-abc", "task_id": "task-1"})
    row = conn.execute("SELECT share_token, share_enabled FROM tasks").fetchone()
    assert row == ("tok-abc", 1)
    returned = execute(conn, disable_sql, {"task_id": "task-1"}).fetchone()
    assert returned[0] == "task-1"
    assert _task_field(conn, "share_enabled") == 0


def test_task_mark_completion_notification_sent_executes_once():
    sql = extract_statement(
        "repositories/task_repository.py",
        "mark_completion_notification_sent",
        "UPDATE tasks",
    )
    conn = fresh_conn(_TASKS_DDL)
    _seed_task(conn)
    row = execute(conn, sql, {"task_id": "task-1"}).fetchone()
    assert row is not None and row[0] is not None  # RETURNING the stamp
    # The IS NULL guard makes a second call a no-op.
    assert execute(conn, sql, {"task_id": "task-1"}).fetchone() is None


def test_brand_kit_delete_detaches_tasks():
    sql = extract_statement(
        "repositories/brand_kit_repository.py", "delete_kit", "UPDATE tasks"
    )
    conn = fresh_conn(_TASKS_DDL)
    _seed_task(conn)
    _seed_task(conn, id="task-2", user_id="user-2")
    execute(conn, sql, {"kit_id": "kit-1", "user_id": "user-1"})
    assert _task_field(conn, "brand_kit_id") is None
    # Another user's task with the same kit id is untouched (no such
    # state in production, but the user guard must hold).
    assert _task_field(conn, "brand_kit_id", "task-2") == "kit-1"


def test_billing_assert_can_resume_task_executes():
    sql = extract_statement(
        "services/billing_service.py", "assert_can_resume_task", "UPDATE tasks"
    )
    conn = fresh_conn(_TASKS_DDL)
    _seed_task(conn, status="processing", source_duration_seconds=100)
    cur = execute(
        conn, sql,
        {"seconds": 240, "task_id": "task-1", "user_id": "user-1",
         "explicit_resume": 0},
    )
    assert cur.rowcount == 1
    assert _task_field(conn, "source_duration_seconds") == 240
    # A completed task cannot be resumed without the explicit flag...
    _seed_task(conn, id="task-2", status="completed", source_duration_seconds=100)
    cur = execute(
        conn, sql,
        {"seconds": 240, "task_id": "task-2", "user_id": "user-1",
         "explicit_resume": 0},
    )
    assert cur.rowcount == 0
    # ...but the explicit resume flag lets it through.
    cur = execute(
        conn, sql,
        {"seconds": 300, "task_id": "task-2", "user_id": "user-1",
         "explicit_resume": 1},
    )
    assert cur.rowcount == 1


# ---------------------------------------------------------------------------
# ClipRepository UPDATEs on generated_clips
# ---------------------------------------------------------------------------


def _seed_clip(conn, **overrides):
    row = {
        "id": "clip-1", "task_id": "task-1", "filename": "a.mp4",
        "file_path": "/media/a.mp4", "start_time": "00:00:01",
        "end_time": "00:00:31", "duration": 30.0, "text": "hello",
        "niche_override": None, "hook_variant": None,
        "hook_variant_batch": None, "qc_status": None, "qc_report": None,
        "compliance_status": None, "compliance_report": None,
        "clip_order": 1, "updated_at": "2026-11-01 00:00:00",
    }
    row.update(overrides)
    cols = ", ".join(row)
    marks = ", ".join(f":{c}" for c in row)
    conn.execute(f"INSERT INTO generated_clips ({cols}) VALUES ({marks})", row)
    return row


def test_clip_update_clip_executes():
    sql = extract_statement(
        "repositories/clip_repository.py", "update_clip", "UPDATE generated_clips"
    )
    conn = fresh_conn(_GENERATED_CLIPS_DDL)
    _seed_clip(conn)
    execute(
        conn, sql,
        {
            "filename": "b.mp4", "file_path": "/media/b.mp4",
            "start_time": "00:00:05", "end_time": "00:00:45",
            "duration": 40.0, "text": "updated transcript", "clip_id": "clip-1",
        },
    )
    row = conn.execute(
        "SELECT filename, duration, text FROM generated_clips"
    ).fetchone()
    assert row == ("b.mp4", 40.0, "updated transcript")


def test_clip_update_niche_override_executes():
    sql = extract_statement(
        "repositories/clip_repository.py",
        "update_clip_niche_override",
        "UPDATE generated_clips",
    )
    conn = fresh_conn(_GENERATED_CLIPS_DDL)
    _seed_clip(conn)
    execute(conn, sql, {"niche_override": "gaming", "clip_id": "clip-1"})
    assert conn.execute(
        "SELECT niche_override FROM generated_clips"
    ).fetchone()[0] == "gaming"


def test_clip_update_hook_variant_executes():
    sql = extract_statement(
        "repositories/clip_repository.py",
        "update_clip_hook_variant",
        "UPDATE generated_clips",
    )
    conn = fresh_conn(_GENERATED_CLIPS_DDL)
    _seed_clip(conn)
    execute(
        conn, sql,
        {"hook_variant": "question", "hook_variant_batch": "batch-7",
         "clip_id": "clip-1"},
    )
    row = conn.execute(
        "SELECT hook_variant, hook_variant_batch FROM generated_clips"
    ).fetchone()
    assert row == ("question", "batch-7")


def test_clip_update_qc_executes():
    sql = extract_statement(
        "repositories/clip_repository.py", "update_clip_qc", "UPDATE generated_clips"
    )
    conn = fresh_conn(_GENERATED_CLIPS_DDL)
    _seed_clip(conn)
    execute(
        conn, sql,
        {"qc_status": "passed", "qc_report": '{"score": 91}', "clip_id": "clip-1"},
    )
    row = conn.execute("SELECT qc_status, qc_report FROM generated_clips").fetchone()
    assert row == ("passed", '{"score": 91}')


def test_clip_update_compliance_executes():
    sql = extract_statement(
        "repositories/clip_repository.py",
        "update_clip_compliance",
        "UPDATE generated_clips",
    )
    conn = fresh_conn(_GENERATED_CLIPS_DDL)
    _seed_clip(conn)
    execute(
        conn, sql,
        {"compliance_status": "flagged", "compliance_report": '{"rule": "brand"}',
         "clip_id": "clip-1"},
    )
    row = conn.execute(
        "SELECT compliance_status, compliance_report FROM generated_clips"
    ).fetchone()
    assert row == ("flagged", '{"rule": "brand"}')


def test_clip_make_room_for_clip_executes():
    sql = extract_statement(
        "repositories/clip_repository.py",
        "make_room_for_clip",
        "UPDATE generated_clips",
    )
    conn = fresh_conn(_GENERATED_CLIPS_DDL)
    _seed_clip(conn, clip_order=3)
    execute(conn, sql, {"id": "clip-1"})
    assert conn.execute("SELECT clip_order FROM generated_clips").fetchone()[0] == 4


def test_clip_reorder_task_clips_both_statements_execute():
    tasks_sql = extract_statement(
        "repositories/clip_repository.py",
        "reorder_task_clips",
        "UPDATE tasks",
    )
    clips_sql = extract_statement(
        "repositories/clip_repository.py",
        "reorder_task_clips",
        "UPDATE generated_clips",
    )
    conn = fresh_conn(_TASKS_DDL, _GENERATED_CLIPS_DDL)
    _seed_task(conn)
    _seed_clip(conn, clip_order=1)
    execute(conn, tasks_sql, {"ids": '["clip-1"]', "task_id": "task-1"})
    assert _task_field(conn, "generated_clips_ids") == '["clip-1"]'
    execute(conn, clips_sql, {"clip_order": 5, "clip_id": "clip-1"})
    assert conn.execute("SELECT clip_order FROM generated_clips").fetchone()[0] == 5


def test_source_update_title_executes():
    sql = extract_statement(
        "repositories/source_repository.py", "update_source_title", "UPDATE sources"
    )
    conn = fresh_conn(
        "CREATE TABLE sources (id TEXT, type TEXT, title TEXT, url TEXT,"
        " created_at TEXT, updated_at TEXT)"
    )
    conn.execute(
        "INSERT INTO sources VALUES ('src-1', 'youtube', 'old', 'https://x', NULL, NULL)"
    )
    execute(conn, sql, {"title": "Week in the Life", "source_id": "src-1"})
    assert conn.execute("SELECT title FROM sources").fetchone()[0] == "Week in the Life"


# ---------------------------------------------------------------------------
# UploadRepository UPDATEs
# ---------------------------------------------------------------------------


def _seed_session(conn, **overrides):
    row = {
        "id": "sess-1", "user_id": "user-1", "state": "reserved",
        "upload_id": None, "video_path": None, "idempotency_key": None,
        "storage_reference": None,
    }
    row.update(overrides)
    cols = ", ".join(row)
    marks = ", ".join(f":{c}" for c in row)
    conn.execute(f"INSERT INTO media_upload_sessions ({cols}) VALUES ({marks})", row)
    return row


def test_upload_bind_upload_executes():
    sql = extract_statement(
        "repositories/upload_repository.py", "bind_upload", "UPDATE media_upload_sessions"
    )
    conn = fresh_conn(_UPLOAD_SESSIONS_DDL)
    _seed_session(conn)
    cur = execute(conn, sql, {"upload": "up-1", "path": "/tmp/up.mp4", "id": "sess-1"})
    assert cur.rowcount == 1
    row = conn.execute(
        "SELECT state, upload_id FROM media_upload_sessions"
    ).fetchone()
    assert row == ("uploading", "up-1")
    # State guard: a second bind against the moved session does nothing.
    assert execute(
        conn, sql, {"upload": "up-2", "path": "/tmp/x", "id": "sess-1"}
    ).rowcount == 0


def test_upload_finish_upload_executes():
    sql = extract_statement(
        "repositories/upload_repository.py",
        "finish_upload",
        "UPDATE media_upload_sessions",
    )
    conn = fresh_conn(_UPLOAD_SESSIONS_DDL)
    _seed_session(conn, state="uploading")
    execute(conn, sql, {"state": "completed", "id": "sess-1"})
    assert conn.execute(
        "SELECT state FROM media_upload_sessions"
    ).fetchone()[0] == "completed"


def test_upload_remember_idempotency_key_update_executes():
    sql = extract_statement(
        "repositories/upload_repository.py",
        "_remember_idempotency_key",
        "UPDATE media_upload_sessions",
    )
    conn = fresh_conn(_UPLOAD_SESSIONS_DDL)
    _seed_session(conn, state="completed")
    execute(conn, sql, {"key": "idem-1", "id": "sess-1"})
    assert conn.execute(
        "SELECT idempotency_key FROM media_upload_sessions"
    ).fetchone()[0] == "idem-1"
    # IS NULL guard: the first key wins.
    execute(conn, sql, {"key": "idem-2", "id": "sess-1"})
    assert conn.execute(
        "SELECT idempotency_key FROM media_upload_sessions"
    ).fetchone()[0] == "idem-1"


def test_upload_save_storage_reference_both_statements_execute():
    session_sql = extract_statement(
        "repositories/upload_repository.py",
        "save_storage_reference",
        "UPDATE media_upload_sessions",
    )
    tasks_sql = extract_statement(
        "repositories/upload_repository.py",
        "save_storage_reference",
        "UPDATE tasks",
    )
    conn = fresh_conn(
        _UPLOAD_SESSIONS_DDL,
        _TASKS_DDL,
        "CREATE TABLE sources (id TEXT, type TEXT, title TEXT, url TEXT,"
        " created_at TEXT, updated_at TEXT)",
    )
    _seed_session(conn, state="completed")
    execute(conn, session_sql, {"reference": "s3://bucket/k", "id": "sess-1"})
    assert conn.execute(
        "SELECT storage_reference FROM media_upload_sessions"
    ).fetchone()[0] == "s3://bucket/k"
    conn.execute(
        "INSERT INTO sources VALUES ('src-1', 'youtube', 't', 'https://youtu.be/x', NULL, NULL)"
    )
    _seed_task(conn, source_id="src-1", source_metadata='{"a": 1}')
    execute(conn, tasks_sql, {"meta": '{"storage": "s3"}', "url": "https://youtu.be/x"})
    assert '{"storage": "s3"}' in _task_field(conn, "source_metadata")


def test_media_cleanup_abort_expired_multipart_executes():
    sql = extract_statement(
        "workers/media_cleanup.py",
        "_abort_expired_multipart_uploads",
        "UPDATE media_upload_sessions",
    )
    conn = fresh_conn(_UPLOAD_SESSIONS_DDL)
    _seed_session(conn, state="uploading")
    execute(conn, sql, {"id": "sess-1"})
    assert conn.execute(
        "SELECT state FROM media_upload_sessions"
    ).fetchone()[0] == "aborted"


# ---------------------------------------------------------------------------
# ClipExportRepository lifecycle UPDATEs
# ---------------------------------------------------------------------------


def _seed_export(conn, **overrides):
    row = {
        "id": "exp-1", "task_id": "task-1", "clip_id": "clip-1",
        "user_id": "user-1", "status": "queued", "progress": 0,
        "file_path": None, "error_message": None, "updated_at": "2026-11-01 00:00:00",
    }
    row.update(overrides)
    cols = ", ".join(row)
    marks = ", ".join(f":{c}" for c in row)
    conn.execute(f"INSERT INTO clip_exports ({cols}) VALUES ({marks})", row)
    return row


def test_export_mark_running_executes():
    sql = extract_statement(
        "repositories/clip_export_repository.py", "mark_running", "UPDATE clip_exports"
    )
    conn = fresh_conn(_CLIP_EXPORTS_DDL)
    _seed_export(conn, status="queued", progress=0)
    row = execute(conn, sql, {"id": "exp-1"}).fetchone()
    assert row[0] == "exp-1"
    state = conn.execute("SELECT status, progress FROM clip_exports").fetchone()
    assert state == ("running", 1)  # GREATEST(progress, 1)


def test_export_update_progress_executes_with_guards():
    sql = extract_statement(
        "repositories/clip_export_repository.py",
        "update_progress",
        "UPDATE clip_exports",
    )
    conn = fresh_conn(_CLIP_EXPORTS_DDL)
    _seed_export(conn, status="running", progress=10)
    assert execute(conn, sql, {"progress": 50, "id": "exp-1"}).rowcount == 1
    # Progress never moves backwards, and 100 is reserved for success.
    assert execute(conn, sql, {"progress": 40, "id": "exp-1"}).rowcount == 0
    assert execute(conn, sql, {"progress": 100, "id": "exp-1"}).rowcount == 0
    assert conn.execute("SELECT progress FROM clip_exports").fetchone()[0] == 50


def test_export_touch_executes():
    sql = extract_statement(
        "repositories/clip_export_repository.py", "touch", "UPDATE clip_exports"
    )
    conn = fresh_conn(_CLIP_EXPORTS_DDL)
    _seed_export(conn, status="running")
    assert execute(conn, sql, {"id": "exp-1"}).rowcount == 1
    _seed_export(conn, id="exp-2", status="queued")
    assert execute(conn, sql, {"id": "exp-2"}).rowcount == 0


def test_export_mark_succeeded_executes():
    sql = extract_statement(
        "repositories/clip_export_repository.py",
        "mark_succeeded",
        "UPDATE clip_exports",
    )
    conn = fresh_conn(_CLIP_EXPORTS_DDL)
    _seed_export(conn, status="running", progress=80, error_message="stale")
    row = execute(
        conn, sql, {"file_path": "/media/out.mp4", "id": "exp-1"}
    ).fetchone()
    assert row[0] == "exp-1"
    state = conn.execute(
        "SELECT status, progress, file_path, error_message FROM clip_exports"
    ).fetchone()
    assert state == ("succeeded", 100, "/media/out.mp4", None)


# ---------------------------------------------------------------------------
# Minute usage + AI edit refunds
# ---------------------------------------------------------------------------

_MINUTE_USAGE_DDL = """
CREATE TABLE minute_usage_events (
    id TEXT PRIMARY KEY, user_id TEXT, task_id TEXT, minutes INTEGER,
    source_duration_seconds INTEGER, created_at TEXT, refunded_at TEXT
)
"""


def test_minute_usage_refund_executes_for_failed_task_without_clips():
    sql = extract_statement(
        "repositories/minute_usage_repository.py",
        "refund_failed_minute_usage",
        "UPDATE minute_usage_events",
    )
    conn = fresh_conn(_MINUTE_USAGE_DDL, _TASKS_DDL, _GENERATED_CLIPS_DDL)
    _seed_task(conn, status="failed")
    conn.execute(
        "INSERT INTO minute_usage_events VALUES"
        " ('mu-1', 'user-1', 'task-1', 3, 150, '2026-11-01', NULL)"
    )
    assert execute(conn, sql, {"task_id": "task-1"}).rowcount == 1
    assert conn.execute(
        "SELECT refunded_at FROM minute_usage_events"
    ).fetchone()[0] is not None
    # Second call: already refunded -> no-op.
    assert execute(conn, sql, {"task_id": "task-1"}).rowcount == 0


def test_minute_usage_refund_blocked_when_task_has_clips():
    sql = extract_statement(
        "repositories/minute_usage_repository.py",
        "refund_failed_minute_usage",
        "UPDATE minute_usage_events",
    )
    conn = fresh_conn(_MINUTE_USAGE_DDL, _TASKS_DDL, _GENERATED_CLIPS_DDL)
    _seed_task(conn, status="failed")
    _seed_clip(conn)  # a saved clip means the charge stands
    conn.execute(
        "INSERT INTO minute_usage_events VALUES"
        " ('mu-1', 'user-1', 'task-1', 3, 150, '2026-11-01', NULL)"
    )
    assert execute(conn, sql, {"task_id": "task-1"}).rowcount == 0


def test_minute_usage_refund_blocked_for_successful_task():
    sql = extract_statement(
        "repositories/minute_usage_repository.py",
        "refund_failed_minute_usage",
        "UPDATE minute_usage_events",
    )
    conn = fresh_conn(_MINUTE_USAGE_DDL, _TASKS_DDL, _GENERATED_CLIPS_DDL)
    _seed_task(conn, status="completed")
    conn.execute(
        "INSERT INTO minute_usage_events VALUES"
        " ('mu-1', 'user-1', 'task-1', 3, 150, '2026-11-01', NULL)"
    )
    assert execute(conn, sql, {"task_id": "task-1"}).rowcount == 0


def test_minute_usage_reactivate_executes():
    sql = extract_statement(
        "repositories/minute_usage_repository.py",
        "reactivate_minute_usage",
        "UPDATE minute_usage_events",
    )
    conn = fresh_conn(_MINUTE_USAGE_DDL)
    conn.execute(
        "INSERT INTO minute_usage_events VALUES"
        " ('mu-1', 'user-1', 'task-1', 3, 150, '2026-11-01', '2026-11-02')"
    )
    cur = execute(conn, sql, {"task_id": "task-1", "user_id": "user-1"})
    assert cur.rowcount == 1
    assert conn.execute(
        "SELECT refunded_at FROM minute_usage_events"
    ).fetchone()[0] is None


def test_ai_edit_refund_executes_once():
    sql = extract_statement(
        "services/ai_edit_allowance_service.py",
        "refund_ai_edit_usage",
        "UPDATE ai_edit_usage_events",
    )
    conn = fresh_conn(
        "CREATE TABLE ai_edit_usage_events (job_id TEXT, user_id TEXT,"
        " created_at TEXT, refunded_at TEXT)"
    )
    conn.execute(
        "INSERT INTO ai_edit_usage_events VALUES ('job-1', 'user-1', '2026-11-01', NULL)"
    )
    assert execute(conn, sql, {"job_id": "job-1"}).rowcount == 1
    assert execute(conn, sql, {"job_id": "job-1"}).rowcount == 0


# ---------------------------------------------------------------------------
# Postgres-only UPDATEs -- documented strict xfails (Postgres tier)
# ---------------------------------------------------------------------------


@pytest.mark.xfail(
    strict=True,
    reason="Postgres-only: next_attempt_at uses make_interval(secs => ...) "
    "-- SQLite cannot parse the => named-argument call syntax at all "
    "(verified: OperationalError near \">\"), and has no make_interval. "
    "Needs the Postgres execution tier (Nov 17 list).",
)
def test_media_cleanup_drain_deletions_update_executes():
    sql = extract_statement(
        "workers/media_cleanup.py", "drain_deletions", "UPDATE media_deletions"
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
    conn.execute("INSERT INTO media_deletions (file_path) VALUES ('/media/old.mp4')")
    execute(conn, sql, {"path": "/media/old.mp4"})
    assert conn.execute("SELECT attempts FROM media_deletions").fetchone()[0] == 1
