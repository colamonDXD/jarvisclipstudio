"""Statement-execution tests: settings, keys, usage and ledger creation.

Same method as ``test_sql_exec_creation_core``: the real statement
text, extracted from source, executed against in-memory SQLite with
migration-derived minimal DDL. Covers the admin settings upsert, the
migration runner's own bookkeeping insert, API keys, brand kits, the
processing cache, learning events/versions, the media-deletion queue,
the minute-usage ledger, provider token usage/cooldowns, the AI-edit
allowance ledger and rejected-source attempts.
"""

from sql_exec_support import count_rows, execute, extract_statement, fresh_conn


def test_admin_app_settings_upsert_executes():
    sql = extract_statement(
        "api/routes/admin.py",
        "update_runtime_settings",
        "INSERT INTO app_settings",
    )
    conn = fresh_conn(
        """
        CREATE TABLE app_settings (
            setting_key TEXT PRIMARY KEY, encrypted_value TEXT,
            updated_by TEXT, updated_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    params = {
        "setting_key": "llm_provider",
        "encrypted_value": "enc:v1:abc",
        "updated_by": "admin-1",
    }
    execute(conn, sql, params)
    execute(conn, sql, {**params, "encrypted_value": "enc:v1:def"})
    assert count_rows(conn, "app_settings") == 1
    value = conn.execute("SELECT encrypted_value FROM app_settings").fetchone()[0]
    assert value == "enc:v1:def"


def test_schema_migrations_insert_executes():
    sql = extract_statement("database.py", "init_db", "INSERT INTO schema_migrations")
    conn = fresh_conn("CREATE TABLE schema_migrations (version TEXT PRIMARY KEY)")
    execute(conn, sql, {"version": "20261111_0001_channel_monitoring.sql"})
    assert count_rows(conn, "schema_migrations") == 1


def test_create_api_key_insert_executes():
    sql = extract_statement(
        "repositories/api_key_repository.py",
        "create_api_key",
        "INSERT INTO api_keys",
    )
    conn = fresh_conn(
        """
        CREATE TABLE api_keys (
            id TEXT, user_id TEXT, name TEXT, key_hash TEXT,
            key_prefix TEXT, created_at TEXT, last_used_at TEXT,
            revoked_at TEXT
        )
        """
    )
    params = {
        "id": "key-1",
        "user_id": "user-1",
        "name": "CLI key",
        "key_hash": "hash",
        "key_prefix": "sk_live_1234",
    }
    row = execute(conn, sql, params).fetchone()
    assert row[0] == "key-1" and row[2] == "sk_live_1234"
    assert count_rows(conn, "api_keys") == 1


def test_create_brand_kit_insert_executes():
    sql = extract_statement(
        "repositories/brand_kit_repository.py",
        "create_kit",
        "INSERT INTO brand_kits",
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
    params = {
        "id": "kit-1",
        "user_id": "user-1",
        "name": "Default kit",
        "primary_color": "#7C5CFF",
        "accent_color": "#38E1FF",
        "caption_template": "bold",
        "font_name": "Inter",
        "title_style": "uppercase",
        "music_track": None,
        "logo_filename": None,
        "logo_position": "bottom_right",
        "logo_opacity": 0.8,
        "intro_asset": None,
        "outro_asset": None,
    }
    execute(conn, sql, params)
    assert count_rows(conn, "brand_kits") == 1


def test_processing_cache_upsert_executes():
    sql = extract_statement(
        "repositories/cache_repository.py",
        "upsert_cache",
        "INSERT INTO processing_cache",
    )
    conn = fresh_conn(
        """
        CREATE TABLE processing_cache (
            cache_key TEXT PRIMARY KEY, source_url TEXT, source_type TEXT,
            video_path TEXT, transcript_text TEXT, analysis_json TEXT,
            created_at TEXT, updated_at TEXT
        )
        """
    )
    params = {
        "cache_key": "cache-1",
        "source_url": "https://www.youtube.com/watch?v=abc",
        "source_type": "youtube",
        "video_path": "/media/v.mp4",
        "transcript_text": "hello",
        "analysis_json": "{}",
    }
    execute(conn, sql, params)
    # Upsert path: COALESCE keeps the stored video_path when the new
    # value is NULL, and replaces the transcript.
    execute(conn, sql, {**params, "video_path": None, "transcript_text": "hello v2"})
    assert count_rows(conn, "processing_cache") == 1
    row = conn.execute(
        "SELECT video_path, transcript_text FROM processing_cache"
    ).fetchone()
    assert row == ("/media/v.mp4", "hello v2")


def test_learning_record_event_insert_executes():
    sql = extract_statement(
        "repositories/learning_repository.py",
        "record_event",
        "INSERT INTO learning_feedback_events",
    )
    conn = fresh_conn(
        """
        CREATE TABLE learning_feedback_events (
            id TEXT, user_id TEXT, task_id TEXT, clip_id TEXT,
            niche TEXT, verdict TEXT, reason TEXT, source TEXT,
            weight REAL, features TEXT
        )
        """
    )
    params = {
        "id": "evt-1",
        "user_id": "user-1",
        "task_id": "task-1",
        "clip_id": "clip-1",
        "niche": "finance_business",
        "verdict": "kept",
        "reason": None,
        "source": "user",
        "weight": 1.0,
        "features": "{}",
    }
    execute(conn, sql, params)
    assert count_rows(conn, "learning_feedback_events") == 1


def test_learning_insert_version_executes():
    sql = extract_statement(
        "repositories/learning_repository.py",
        "insert_version",
        "INSERT INTO learning_versions",
    )
    conn = fresh_conn(
        """
        CREATE TABLE learning_versions (
            id TEXT, user_id TEXT, version INTEGER, adjustments TEXT,
            event_count INTEGER, summary TEXT
        )
        """
    )
    params = {
        "id": "ver-1",
        "user_id": "user-1",
        "version": 3,
        "adjustments": "{}",
        "event_count": 12,
        "summary": "tightened hooks",
    }
    execute(conn, sql, params)
    assert count_rows(conn, "learning_versions") == 1


def test_media_queue_delete_insert_executes_and_merges():
    sql = extract_statement(
        "repositories/media_repository.py",
        "queue_delete",
        "INSERT INTO media_deletions",
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
    params = {"path": "/media/old.mp4", "prefix": 0, "hold": None}
    execute(conn, sql, params)
    # Conflict path: attempts reset, hold token filled in via COALESCE.
    execute(conn, sql, {**params, "prefix": 1, "hold": "hold-1"})
    assert count_rows(conn, "media_deletions") == 1
    row = conn.execute(
        "SELECT is_prefix, hold_token FROM media_deletions"
    ).fetchone()
    assert row == (1, "hold-1")


def test_minute_usage_insert_executes():
    sql = extract_statement(
        "repositories/minute_usage_repository.py",
        "record_minute_usage",
        "INSERT INTO minute_usage_events",
    )
    # DDL mirrors the migration, including its UNIQUE(task_id) and the
    # rounding CHECK (minutes must equal ceil(seconds/60)).
    conn = fresh_conn(
        """
        CREATE TABLE minute_usage_events (
            id TEXT PRIMARY KEY, user_id TEXT, task_id TEXT UNIQUE,
            minutes INTEGER, source_duration_seconds INTEGER,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            CHECK (minutes = (source_duration_seconds + 59) / 60)
        )
        """
    )
    params = {
        "id": "mu-1",
        "user_id": "user-1",
        "task_id": "task-1",
        "minutes": 2,
        "seconds": 120,
    }
    execute(conn, sql, params)
    execute(conn, sql, params)  # ON CONFLICT (task_id) DO NOTHING
    assert count_rows(conn, "minute_usage_events") == 1


def test_provider_token_usage_insert_executes():
    sql = extract_statement(
        "repositories/provider_usage_repository.py",
        "record_token_usage",
        "INSERT INTO llm_token_usage",
    )
    conn = fresh_conn(
        """
        CREATE TABLE llm_token_usage (
            id TEXT, task_id TEXT, alias TEXT, purpose TEXT,
            prompt_tokens INTEGER, completion_tokens INTEGER,
            total_tokens INTEGER, usage_known INTEGER
        )
        """
    )
    params = {
        "id": "usage-1",
        "task_id": "task-1",
        "alias": "clip-assured",
        "purpose": "classification",
        "prompt_tokens": 1200,
        "completion_tokens": 300,
        "total_tokens": 1500,
        "usage_known": 1,
    }
    execute(conn, sql, params)
    assert count_rows(conn, "llm_token_usage") == 1


def test_provider_cooldown_upsert_executes():
    sql = extract_statement(
        "repositories/provider_usage_repository.py",
        "upsert_cooldown",
        "INSERT INTO provider_cooldowns",
    )
    conn = fresh_conn(
        """
        CREATE TABLE provider_cooldowns (
            alias TEXT PRIMARY KEY, failure_class TEXT,
            cooldown_until TEXT, consecutive_failures INTEGER,
            last_error TEXT, updated_at TEXT
        )
        """
    )
    params = {
        "alias": "clip-fast",
        "failure_class": "rate_limit",
        "cooldown_until": "2026-11-11T12:00:00Z",
        "consecutive_failures": 1,
        "last_error": "HTTP 429",
    }
    execute(conn, sql, params)
    execute(conn, sql, {**params, "consecutive_failures": 2})
    assert count_rows(conn, "provider_cooldowns") == 1
    failures = conn.execute(
        "SELECT consecutive_failures FROM provider_cooldowns"
    ).fetchone()[0]
    assert failures == 2


def test_ai_edit_reserve_insert_executes():
    sql = extract_statement(
        "services/ai_edit_allowance_service.py",
        "reserve",
        "INSERT INTO ai_edit_usage_events",
    )
    conn = fresh_conn(
        """
        CREATE TABLE ai_edit_usage_events (
            job_id TEXT, user_id TEXT, created_at TEXT
        )
        """
    )
    execute(conn, sql, {"job_id": "job-1", "user_id": "user-1"})
    assert count_rows(conn, "ai_edit_usage_events") == 1


def test_rejected_source_attempt_insert_executes():
    sql = extract_statement(
        "source_submission.py",
        "_persist_rejection",
        "INSERT INTO rejected_source_attempts",
    )
    conn = fresh_conn(
        """
        CREATE TABLE rejected_source_attempts (
            id TEXT, user_id TEXT, domain TEXT, sanitized_url TEXT,
            reason TEXT, client TEXT, created_at TEXT
        )
        """
    )
    params = {
        "id": "rej-1",
        "user_id": "user-1",
        "domain": "example.com",
        "sanitized_url": "https://example.com/v",
        "reason": "unsupported_domain",
        "client": "web",
    }
    execute(conn, sql, params)
    assert count_rows(conn, "rejected_source_attempts") == 1


# ---------------------------------------------------------------------------
# RecipeRepository.create_recipe -- recipes insert (jsonb settings payload)
# ---------------------------------------------------------------------------


def test_recipe_insert_executes():
    sql = extract_statement(
        "repositories/recipe_repository.py",
        "create_recipe",
        "INSERT INTO recipes",
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
    params = {
        "id": "recipe-1",
        "user_id": "user-1",
        "name": "Finance plain",
        "settings": '{"template_mode": "plain"}',
    }
    execute(conn, sql, params)
    assert count_rows(conn, "recipes") == 1
    row = conn.execute("SELECT user_id, name, settings FROM recipes").fetchone()
    assert row == ("user-1", "Finance plain", '{"template_mode": "plain"}')
