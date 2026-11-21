"""Statement-execution tests: UPDATE statements, channel monitor and
watch-folder reclaim paths plus the publishing/social fixed-text set.

Same method as the core file: real statement text extracted from
source, seeded rows, execute, assert the intended change and the
guard behaviour. The reclaim statements' staleness comparison
(``CURRENT_TIMESTAMP - (:p * INTERVAL '1 second')``) runs through the
one documented interval normalisation in ``sql_exec_support``.
"""

import pytest

from sql_exec_support import execute, extract_statement, fresh_conn

_CHANNEL_MONITORS_DDL = """
CREATE TABLE channel_monitors (
    id TEXT, user_id TEXT, channel_id TEXT, channel_title TEXT,
    source_input TEXT, enabled INTEGER, niche_override TEXT,
    music_mode TEXT, music_override TEXT, template_mode TEXT,
    creation_mode TEXT, target_clip_count INTEGER, processing_mode TEXT,
    include_shorts INTEGER, max_tasks_per_day INTEGER, campaign_id TEXT,
    brand_kit_id TEXT, last_fallback_at TEXT, last_polled_at TEXT,
    last_error TEXT, consecutive_failures INTEGER,
    created_at TEXT, updated_at TEXT
)
"""

_CHANNEL_VIDEOS_DDL = """
CREATE TABLE channel_monitor_videos (
    id TEXT, user_id TEXT, monitor_id TEXT, video_id TEXT,
    video_url TEXT, title TEXT, published_at TEXT, state TEXT,
    skip_reason TEXT, task_id TEXT, attempts INTEGER, last_error TEXT,
    waiting_since TEXT, created_at TEXT, updated_at TEXT
)
"""

_WATCH_FILES_DDL = """
CREATE TABLE watch_folder_files (
    id TEXT, user_id TEXT, file_path TEXT, file_name TEXT,
    fingerprint TEXT, size_bytes INTEGER, mtime_ns INTEGER,
    state TEXT, task_id TEXT, upload_path TEXT, attempts INTEGER,
    last_error TEXT, created_at TEXT, updated_at TEXT
)
"""

OLD = "2000-01-01 00:00:00"  # always stale, whatever the backoff window
FRESH = "2999-01-01 00:00:00"  # never stale


def _seed_video(conn, **overrides):
    row = {
        "id": "vid-1", "user_id": "user-1", "monitor_id": "mon-1",
        "video_id": "yt-1", "video_url": "https://youtu.be/yt-1",
        "title": "A video", "published_at": "2026-11-01", "state": "failed",
        "skip_reason": None, "task_id": None, "attempts": 1,
        "last_error": "boom", "waiting_since": None,
        "created_at": OLD, "updated_at": OLD,
    }
    row.update(overrides)
    cols = ", ".join(row)
    marks = ", ".join(f":{c}" for c in row)
    conn.execute(f"INSERT INTO channel_monitor_videos ({cols}) VALUES ({marks})", row)
    return row


def _seed_watch_file(conn, **overrides):
    row = {
        "id": "file-1", "user_id": "user-1", "file_path": "/watch/a.mp4",
        "file_name": "a.mp4", "fingerprint": "fp-1", "size_bytes": 10,
        "mtime_ns": 1, "state": "failed", "task_id": None,
        "upload_path": None, "attempts": 1, "last_error": "boom",
        "created_at": OLD, "updated_at": OLD,
    }
    row.update(overrides)
    cols = ", ".join(row)
    marks = ", ".join(f":{c}" for c in row)
    conn.execute(f"INSERT INTO watch_folder_files ({cols}) VALUES ({marks})", row)
    return row


# ---------------------------------------------------------------------------
# ChannelMonitorRepository reclaims
# ---------------------------------------------------------------------------


def test_reclaim_failed_video_executes_and_bumps_attempts():
    sql = extract_statement(
        "repositories/channel_monitor_repository.py",
        "reclaim_failed_video",
        "UPDATE channel_monitor_videos",
    )
    conn = fresh_conn(_CHANNEL_VIDEOS_DDL)
    _seed_video(conn, state="failed", attempts=1, updated_at=OLD)
    row = execute(
        conn, sql, {"id": "vid-1", "max_attempts": 3, "backoff_seconds": 300}
    ).fetchone()
    assert row is not None
    state = conn.execute(
        "SELECT state, attempts FROM channel_monitor_videos"
    ).fetchone()
    assert state == ("processing", 2)


def test_reclaim_failed_video_blocked_when_fresh_or_exhausted():
    sql = extract_statement(
        "repositories/channel_monitor_repository.py",
        "reclaim_failed_video",
        "UPDATE channel_monitor_videos",
    )
    conn = fresh_conn(_CHANNEL_VIDEOS_DDL)
    _seed_video(conn, id="vid-fresh", state="failed", attempts=1, updated_at=FRESH)
    _seed_video(conn, id="vid-maxed", state="failed", attempts=3, updated_at=OLD)
    # Backoff window has not elapsed for the fresh row.
    assert execute(
        conn, sql, {"id": "vid-fresh", "max_attempts": 3, "backoff_seconds": 300}
    ).fetchone() is None
    # Attempt ceiling reached for the maxed row.
    assert execute(
        conn, sql, {"id": "vid-maxed", "max_attempts": 3, "backoff_seconds": 300}
    ).fetchone() is None


def test_reclaim_stale_processing_video_executes():
    sql = extract_statement(
        "repositories/channel_monitor_repository.py",
        "reclaim_stale_processing_video",
        "UPDATE channel_monitor_videos",
    )
    conn = fresh_conn(_CHANNEL_VIDEOS_DDL)
    _seed_video(conn, state="processing", task_id=None, attempts=2, updated_at=OLD)
    row = execute(conn, sql, {"id": "vid-1", "stale_seconds": 900}).fetchone()
    assert row is not None
    assert conn.execute(
        "SELECT attempts FROM channel_monitor_videos"
    ).fetchone()[0] == 3
    # A processing row that already has a task is not an orphan.
    _seed_video(conn, id="vid-2", state="processing", task_id="task-9", updated_at=OLD)
    assert execute(conn, sql, {"id": "vid-2", "stale_seconds": 900}).fetchone() is None


def test_reclaim_waiting_video_executes():
    sql = extract_statement(
        "repositories/channel_monitor_repository.py",
        "reclaim_waiting_video",
        "UPDATE channel_monitor_videos",
    )
    conn = fresh_conn(_CHANNEL_VIDEOS_DDL)
    _seed_video(conn, state="waiting", waiting_since=OLD)
    row = execute(conn, sql, {"id": "vid-1"}).fetchone()
    assert row is not None
    assert conn.execute(
        "SELECT state FROM channel_monitor_videos"
    ).fetchone()[0] == "processing"


def test_reclaim_for_redrive_executes_and_clears_error_state():
    sql = extract_statement(
        "repositories/channel_monitor_repository.py",
        "reclaim_for_redrive",
        "UPDATE channel_monitor_videos",
    )
    conn = fresh_conn(_CHANNEL_VIDEOS_DDL)
    _seed_video(
        conn, state="parked", attempts=3, last_error="gave up",
        skip_reason="over_duration_cap", waiting_since=OLD,
    )
    row = execute(conn, sql, {"id": "vid-1"}).fetchone()
    assert row is not None
    state = conn.execute(
        "SELECT state, attempts, waiting_since, last_error, skip_reason"
        " FROM channel_monitor_videos"
    ).fetchone()
    assert state == ("processing", 1, None, None, None)
    # A parked row that already produced a task cannot be re-driven.
    _seed_video(conn, id="vid-2", state="parked", task_id="task-9")
    assert execute(conn, sql, {"id": "vid-2"}).fetchone() is None


def test_park_videos_for_missing_monitors_executes():
    sql = extract_statement(
        "repositories/channel_monitor_repository.py",
        "park_videos_for_missing_monitors",
        "UPDATE channel_monitor_videos",
    )
    conn = fresh_conn(_CHANNEL_MONITORS_DDL, _CHANNEL_VIDEOS_DDL)
    conn.execute(
        "INSERT INTO channel_monitors (id, user_id, channel_id)"
        " VALUES ('mon-1', 'user-1', 'UC1')"
    )
    _seed_video(conn, id="vid-orphan", monitor_id="mon-gone", state="processing")
    _seed_video(conn, id="vid-kept", monitor_id="mon-1", state="processing")
    _seed_video(conn, id="vid-done", monitor_id="mon-gone", state="completed")
    execute(conn, sql, {"user_id": "user-1"})
    states = dict(
        conn.execute("SELECT id, state FROM channel_monitor_videos").fetchall()
    )
    assert states == {
        "vid-orphan": "parked",
        "vid-kept": "processing",
        "vid-done": "completed",
    }
    error = conn.execute(
        "SELECT last_error FROM channel_monitor_videos WHERE id = 'vid-orphan'"
    ).fetchone()[0]
    assert error.startswith("monitor_removed")


# ---------------------------------------------------------------------------
# WatchFolderRepository reclaims
# ---------------------------------------------------------------------------


def test_reclaim_failed_file_executes():
    sql = extract_statement(
        "repositories/watch_folder_repository.py",
        "reclaim_failed_file",
        "UPDATE watch_folder_files",
    )
    conn = fresh_conn(_WATCH_FILES_DDL)
    _seed_watch_file(conn, state="failed", attempts=1, updated_at=OLD)
    row = execute(
        conn, sql, {"id": "file-1", "max_attempts": 3, "backoff_seconds": 300}
    ).fetchone()
    assert row is not None
    state = conn.execute(
        "SELECT state, attempts FROM watch_folder_files"
    ).fetchone()
    assert state == ("processing", 2)
    _seed_watch_file(conn, id="file-2", state="failed", attempts=1, updated_at=FRESH)
    assert execute(
        conn, sql, {"id": "file-2", "max_attempts": 3, "backoff_seconds": 300}
    ).fetchone() is None


def test_reclaim_stale_processing_file_executes():
    sql = extract_statement(
        "repositories/watch_folder_repository.py",
        "reclaim_stale_processing_file",
        "UPDATE watch_folder_files",
    )
    conn = fresh_conn(_WATCH_FILES_DDL)
    _seed_watch_file(conn, state="processing", task_id=None, attempts=2, updated_at=OLD)
    row = execute(conn, sql, {"id": "file-1", "stale_seconds": 900}).fetchone()
    assert row is not None
    assert conn.execute(
        "SELECT attempts FROM watch_folder_files"
    ).fetchone()[0] == 3


# ---------------------------------------------------------------------------
# Publishing / social fixed-text UPDATEs
# ---------------------------------------------------------------------------

_CLIP_POSTS_DDL = """
CREATE TABLE clip_posts (
    id TEXT, clip_id TEXT, user_id TEXT, platform TEXT,
    post_url TEXT, posted_at TEXT, campaign_id TEXT,
    submission_url TEXT, submission_dismissed INTEGER, updated_at TEXT
)
"""

_SOCIAL_ACCOUNTS_DDL = """
CREATE TABLE social_accounts (
    id TEXT, user_id TEXT, provider TEXT, external_account_id TEXT,
    display_name TEXT, username TEXT, avatar_url TEXT,
    access_token_encrypted TEXT, refresh_token_encrypted TEXT,
    token_expires_at TEXT, scopes TEXT, metadata TEXT,
    status TEXT, last_error TEXT, created_at TEXT,
    updated_at TEXT, revoked_at TEXT
)
"""

_SOCIAL_POSTS_DDL = """
CREATE TABLE social_posts (
    id TEXT, user_id TEXT, task_id TEXT, clip_id TEXT,
    social_account_id TEXT, provider TEXT, status TEXT,
    scheduled_for TEXT, title TEXT, caption TEXT, hashtags TEXT,
    privacy_level TEXT, media_token TEXT, media_token_expires_at TEXT,
    source_file_path TEXT, pending_handle TEXT, error_message TEXT,
    attempts INTEGER, claimed_at TEXT, metrics TEXT, last_metrics_at TEXT,
    created_at TEXT, updated_at TEXT
)
"""


def test_publishing_delete_campaign_detaches_posts():
    sql = extract_statement(
        "repositories/publishing_repository.py",
        "delete_campaign",
        "UPDATE clip_posts",
    )
    conn = fresh_conn(_CLIP_POSTS_DDL)
    conn.execute(
        "INSERT INTO clip_posts (id, clip_id, user_id, platform, campaign_id)"
        " VALUES ('post-1', 'clip-1', 'user-1', 'youtube', 'camp-1')"
    )
    conn.execute(
        "INSERT INTO clip_posts (id, clip_id, user_id, platform, campaign_id)"
        " VALUES ('post-2', 'clip-2', 'user-2', 'youtube', 'camp-1')"
    )
    execute(conn, sql, {"campaign_id": "camp-1", "user_id": "user-1"})
    rows = dict(conn.execute("SELECT id, campaign_id FROM clip_posts").fetchall())
    assert rows == {"post-1": None, "post-2": "camp-1"}


def _seed_account(conn, **overrides):
    row = {
        "id": "acct-1", "user_id": "user-1", "provider": "youtube",
        "external_account_id": "UC1", "display_name": "Chan",
        "username": "@chan", "avatar_url": None,
        "access_token_encrypted": "enc:a", "refresh_token_encrypted": "enc:r",
        "token_expires_at": "2026-12-01", "scopes": "upload",
        "metadata": "{}", "status": "active", "last_error": None,
        "created_at": "2026-11-01", "updated_at": "2026-11-01",
        "revoked_at": None,
    }
    row.update(overrides)
    cols = ", ".join(row)
    marks = ", ".join(f":{c}" for c in row)
    conn.execute(f"INSERT INTO social_accounts ({cols}) VALUES ({marks})", row)
    return row


def test_social_update_account_tokens_executes():
    sql = extract_statement(
        "repositories/social_repository.py",
        "update_account_tokens",
        "UPDATE social_accounts",
    )
    conn = fresh_conn(_SOCIAL_ACCOUNTS_DDL)
    _seed_account(conn, status="error", last_error="expired")
    row = execute(
        conn, sql,
        {"access_token_encrypted": "enc:new", "refresh_token_encrypted": None,
         "token_expires_at": "2027-01-01", "id": "acct-1"},
    ).fetchone()
    assert row[0] == "acct-1"
    state = conn.execute(
        "SELECT access_token_encrypted, refresh_token_encrypted, status, last_error"
        " FROM social_accounts"
    ).fetchone()
    # A NULL refresh token keeps the stored one (COALESCE).
    assert state == ("enc:new", "enc:r", "active", None)


def test_social_set_account_status_executes():
    sql = extract_statement(
        "repositories/social_repository.py",
        "set_account_status",
        "UPDATE social_accounts",
    )
    conn = fresh_conn(_SOCIAL_ACCOUNTS_DDL)
    _seed_account(conn)
    execute(conn, sql, {"status": "error", "last_error": "rate limited", "id": "acct-1"})
    state = conn.execute(
        "SELECT status, last_error FROM social_accounts"
    ).fetchone()
    assert state == ("error", "rate limited")


def test_social_revoke_account_both_statements_execute():
    accounts_sql = extract_statement(
        "repositories/social_repository.py",
        "revoke_account",
        "UPDATE social_accounts",
    )
    posts_sql = extract_statement(
        "repositories/social_repository.py",
        "revoke_account",
        "UPDATE social_posts",
    )
    conn = fresh_conn(_SOCIAL_ACCOUNTS_DDL, _SOCIAL_POSTS_DDL)
    _seed_account(conn)
    conn.execute(
        "INSERT INTO social_posts (id, user_id, clip_id, social_account_id, status)"
        " VALUES ('sp-1', 'user-1', 'clip-1', 'acct-1', 'scheduled')"
    )
    conn.execute(
        "INSERT INTO social_posts (id, user_id, clip_id, social_account_id, status)"
        " VALUES ('sp-2', 'user-1', 'clip-2', 'acct-1', 'published')"
    )
    row = execute(conn, accounts_sql, {"id": "acct-1", "user_id": "user-1"}).fetchone()
    assert row[0] == "acct-1"
    acct = conn.execute(
        "SELECT status, access_token_encrypted, refresh_token_encrypted, revoked_at"
        " FROM social_accounts"
    ).fetchone()
    assert acct[0] == "revoked" and acct[1] == "" and acct[2] is None
    assert acct[3] is not None
    execute(conn, posts_sql, {"id": "acct-1"})
    states = dict(conn.execute("SELECT id, status FROM social_posts").fetchall())
    assert states == {"sp-1": "cancelled", "sp-2": "published"}


def _seed_social_post(conn, **overrides):
    row = {
        "id": "sp-1", "user_id": "user-1", "task_id": "task-1",
        "clip_id": "clip-1", "social_account_id": "acct-1",
        "provider": "youtube", "status": "queued", "scheduled_for": None,
        "title": "t", "caption": "c", "hashtags": "#x",
        "privacy_level": "public", "media_token": "tok",
        "media_token_expires_at": None, "source_file_path": "/media/old.mp4",
        "pending_handle": None, "error_message": "stale", "attempts": 0,
        "claimed_at": None, "metrics": None, "last_metrics_at": None,
        "created_at": "2026-11-01", "updated_at": "2026-11-01",
    }
    row.update(overrides)
    cols = ", ".join(row)
    marks = ", ".join(f":{c}" for c in row)
    conn.execute(f"INSERT INTO social_posts ({cols}) VALUES ({marks})", row)
    return row


def test_social_claim_post_executes_and_refreshes_source_path():
    sql = extract_statement(
        "repositories/social_repository.py", "claim_post", "UPDATE social_posts"
    )
    conn = fresh_conn(
        _SOCIAL_POSTS_DDL,
        "CREATE TABLE generated_clips (id TEXT PRIMARY KEY, task_id TEXT,"
        " file_path TEXT)",
    )
    conn.execute(
        "INSERT INTO generated_clips VALUES ('clip-1', 'task-1', '/media/current.mp4')"
    )
    _seed_social_post(conn, status="queued", source_file_path="/media/old.mp4")
    row = execute(
        conn, sql, {"media_token_expires_at": "2026-11-12", "id": "sp-1"}
    ).fetchone()
    assert row[0] == "sp-1"
    state = conn.execute(
        "SELECT status, attempts, source_file_path, error_message FROM social_posts"
    ).fetchone()
    # The claim re-points the post at the clip's CURRENT render.
    assert state == ("publishing", 1, "/media/current.mp4", None)
    # A second claim is blocked by the status guard.
    assert execute(
        conn, sql, {"media_token_expires_at": "2026-11-12", "id": "sp-1"}
    ).fetchone() is None


def test_social_cancel_post_executes():
    sql = extract_statement(
        "repositories/social_repository.py", "cancel_post", "UPDATE social_posts"
    )
    conn = fresh_conn(_SOCIAL_POSTS_DDL)
    _seed_social_post(conn, status="scheduled")
    row = execute(conn, sql, {"id": "sp-1", "user_id": "user-1"}).fetchone()
    assert row[0] == "sp-1"
    assert conn.execute("SELECT status FROM social_posts").fetchone()[0] == "cancelled"


def test_social_requeue_post_executes():
    sql = extract_statement(
        "repositories/social_repository.py", "requeue_post", "UPDATE social_posts"
    )
    conn = fresh_conn(_SOCIAL_POSTS_DDL)
    _seed_social_post(
        conn, status="failed", attempts=2, media_token=None,
        scheduled_for="2026-11-05",
    )
    attempts = execute(
        conn, sql, {"media_token": "tok-new", "id": "sp-1", "user_id": "user-1"}
    ).fetchone()
    assert attempts[0] == 2  # RETURNING attempts (unchanged by the requeue)
    state = conn.execute(
        "SELECT status, scheduled_for, media_token FROM social_posts"
    ).fetchone()
    assert state == ("queued", None, "tok-new")


def test_social_insert_metrics_update_executes():
    sql = extract_statement(
        "repositories/social_repository.py", "insert_metrics", "UPDATE social_posts"
    )
    conn = fresh_conn(_SOCIAL_POSTS_DDL)
    _seed_social_post(conn, status="published")
    execute(conn, sql, {"metrics": '{"views": 1200}', "id": "sp-1"})
    row = conn.execute(
        "SELECT metrics, last_metrics_at FROM social_posts"
    ).fetchone()
    assert row[0] == '{"views": 1200}' and row[1] is not None


# ---------------------------------------------------------------------------
# Postgres-only UPDATEs -- documented strict xfails (Postgres tier)
# ---------------------------------------------------------------------------


@pytest.mark.xfail(
    strict=True,
    reason="Postgres-only: the staleness comparison uses "
    "make_interval(secs => CAST(:seconds AS integer)) -- SQLite cannot "
    "parse the => named-argument syntax and has no make_interval. "
    "Needs the Postgres execution tier (Nov 17 list).",
)
def test_social_fail_stale_publishing_executes():
    sql = extract_statement(
        "repositories/social_repository.py",
        "fail_stale_publishing",
        "UPDATE social_posts",
    )
    conn = fresh_conn(_SOCIAL_POSTS_DDL)
    _seed_social_post(conn, status="publishing", claimed_at="2000-01-01 00:00:00")
    row = execute(conn, sql, {"message": "worker died", "seconds": 600}).fetchone()
    assert row[0] == "sp-1"


@pytest.mark.xfail(
    strict=True,
    reason="Postgres-only: the due-post selection subquery ends with "
    "FOR UPDATE SKIP LOCKED, which SQLite cannot parse. Needs the "
    "Postgres execution tier (Nov 17 list).",
)
def test_social_claim_due_scheduled_posts_executes():
    sql = extract_statement(
        "repositories/social_repository.py",
        "claim_due_scheduled_posts",
        "UPDATE social_posts",
    )
    conn = fresh_conn(_SOCIAL_POSTS_DDL)
    _seed_social_post(conn, status="scheduled", scheduled_for="2000-01-01 00:00:00")
    row = execute(conn, sql, {"limit": 10}).fetchone()
    assert row[0] == "sp-1"
    assert conn.execute("SELECT status FROM social_posts").fetchone()[0] == "queued"
