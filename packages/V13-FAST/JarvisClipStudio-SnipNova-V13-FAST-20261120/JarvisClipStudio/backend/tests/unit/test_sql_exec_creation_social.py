"""Statement-execution tests: publishing and social creation paths.

Same method as ``test_sql_exec_creation_core``: the real statement
text, extracted from source, executed against in-memory SQLite with
migration-derived minimal DDL. Covers campaigns, clip posts and their
stats, social accounts (including the ``metadata`` jsonb merge in the
upsert), OAuth states, social posts and post metrics.
"""

from sql_exec_support import count_rows, execute, extract_statement, fresh_conn


def test_create_campaign_insert_executes():
    sql = extract_statement(
        "repositories/publishing_repository.py",
        "create_campaign",
        "INSERT INTO campaigns",
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
    params = {
        "id": "camp-1",
        "user_id": "user-1",
        "name": "Expedia push",
        "platform": "youtube",
        "campaign_url": "https://whop.com/campaign",
        "budget": 8000.0,
        "notes": None,
        "brief": "{}",
        "example_url": None,
    }
    execute(conn, sql, params)
    assert count_rows(conn, "campaigns") == 1


def test_upsert_post_insert_executes_and_updates():
    sql = extract_statement(
        "repositories/publishing_repository.py",
        "upsert_post",
        "INSERT INTO clip_posts",
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
    params = {
        "id": "post-1",
        "clip_id": "clip-1",
        "user_id": "user-1",
        "platform": "tiktok",
        "post_url": "https://www.tiktok.com/@u/video/1",
        "posted_at": "2026-11-10T10:00:00Z",
        "campaign_id": "camp-1",
        "submission_url": None,
        "submission_dismissed": 0,
    }
    assert execute(conn, sql, params).fetchone()[0] == "post-1"
    execute(conn, sql, {**params, "id": "post-2", "submission_url": "https://whop.com/s/1"})
    assert count_rows(conn, "clip_posts") == 1
    url = conn.execute("SELECT submission_url FROM clip_posts").fetchone()[0]
    assert url == "https://whop.com/s/1"


def test_upsert_stats_insert_executes_and_updates():
    sql = extract_statement(
        "repositories/publishing_repository.py",
        "upsert_stats",
        "INSERT INTO clip_post_stats",
    )
    conn = fresh_conn(
        """
        CREATE TABLE clip_post_stats (
            post_id TEXT PRIMARY KEY, views INTEGER, likes INTEGER,
            comments INTEGER, earnings REAL, updated_at TEXT
        )
        """
    )
    params = {"post_id": "post-1", "views": 100, "likes": 9, "comments": 2, "earnings": 1.5}
    execute(conn, sql, params)
    execute(conn, sql, {**params, "views": 250})
    assert count_rows(conn, "clip_post_stats") == 1
    views = conn.execute("SELECT views FROM clip_post_stats").fetchone()[0]
    assert views == 250


def test_social_upsert_account_insert_executes_and_merges():
    sql = extract_statement(
        "repositories/social_repository.py",
        "upsert_account",
        "INSERT INTO social_accounts",
    )
    conn = fresh_conn(
        """
        CREATE TABLE social_accounts (
            id TEXT, user_id TEXT, provider TEXT, external_account_id TEXT,
            display_name TEXT, username TEXT, avatar_url TEXT,
            access_token_encrypted TEXT, refresh_token_encrypted TEXT,
            token_expires_at TEXT, scopes TEXT, metadata TEXT,
            status TEXT, last_error TEXT, created_at TEXT,
            updated_at TEXT, revoked_at TEXT,
            UNIQUE (user_id, provider, external_account_id)
        )
        """
    )
    params = {
        "id": "acct-1",
        "user_id": "user-1",
        "provider": "youtube",
        "external_account_id": "UC123",
        "display_name": "Channel",
        "username": "@channel",
        "avatar_url": None,
        "access_token_encrypted": "enc:a",
        "refresh_token_encrypted": "enc:r",
        "token_expires_at": "2026-12-01T00:00:00Z",
        "scopes": "upload",
        "metadata": '{"a": 1}',
    }
    row = execute(conn, sql, params).fetchone()
    assert row is not None
    # Conflict path: refresh token kept via COALESCE when the new one
    # is NULL; metadata merged with || (text concat under SQLite; on
    # Postgres jsonb || is an object merge -- see sql_exec_support).
    execute(conn, sql, {**params, "id": "acct-2", "refresh_token_encrypted": None, "metadata": '{"b": 2}'})
    assert count_rows(conn, "social_accounts") == 1
    refresh, metadata = conn.execute(
        "SELECT refresh_token_encrypted, metadata FROM social_accounts"
    ).fetchone()
    assert refresh == "enc:r"
    assert '{"a": 1}' in metadata and '{"b": 2}' in metadata


def test_social_create_oauth_state_insert_executes():
    sql = extract_statement(
        "repositories/social_repository.py",
        "create_oauth_state",
        "INSERT INTO social_oauth_states",
    )
    conn = fresh_conn(
        """
        CREATE TABLE social_oauth_states (
            state TEXT, user_id TEXT, provider TEXT, code_verifier TEXT,
            redirect_uri TEXT, created_at TEXT, expires_at TEXT
        )
        """
    )
    params = {
        "state": "state-1",
        "user_id": "user-1",
        "provider": "youtube",
        "code_verifier": "verifier",
        "redirect_uri": "http://localhost:3107/callback",
        "expires_at": "2026-11-11T12:10:00Z",
    }
    execute(conn, sql, params)
    assert count_rows(conn, "social_oauth_states") == 1


def test_social_create_post_insert_executes():
    sql = extract_statement(
        "repositories/social_repository.py",
        "create_post",
        "INSERT INTO social_posts",
    )
    conn = fresh_conn(
        """
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
    )
    params = {
        "id": "spost-1",
        "user_id": "user-1",
        "task_id": "task-1",
        "clip_id": "clip-1",
        "social_account_id": "acct-1",
        "provider": "youtube",
        "status": "draft",
        "scheduled_for": None,
        "title": "A title",
        "caption": "A caption",
        "hashtags": "#shorts",
        "privacy_level": "public",
        "media_token": "tok-1",
        "source_file_path": "/media/clip1.mp4",
        "hook_type": "question",
        "hook_title": "TJR Tries a $400 Sandwich",
        "virality_score": 88,
        "hook_score": 90,
        "engagement_score": 70,
        "value_score": 60,
        "shareability_score": 75,
        "clip_duration": 30.0,
    }
    execute(conn, sql, params)
    assert count_rows(conn, "social_posts") == 1


def test_social_insert_metrics_executes():
    sql = extract_statement(
        "repositories/social_repository.py",
        "insert_metrics",
        "INSERT INTO social_post_metrics",
    )
    conn = fresh_conn(
        """
        CREATE TABLE social_post_metrics (
            id TEXT, post_id TEXT, captured_at TEXT, views INTEGER,
            likes INTEGER, comments INTEGER, shares INTEGER,
            saves INTEGER, raw TEXT
        )
        """
    )
    params = {
        "id": "metric-1",
        "post_id": "spost-1",
        "views": 1000,
        "likes": 90,
        "comments": 12,
        "shares": 30,
        "saves": 44,
        "raw": '{"views": 1000}',
    }
    execute(conn, sql, params)
    assert count_rows(conn, "social_post_metrics") == 1
    raw = conn.execute("SELECT raw FROM social_post_metrics").fetchone()[0]
    assert raw == '{"views": 1000}'
