"""Method-level execution tests: runtime-assembled UPDATEs.

Ten repository update functions build their SET clause from a
caller-supplied field dict, so no fixed statement text exists for
the extraction-based suites to run. These tests drive the *real*
methods through ``ExecSession`` -- an async session double that
executes the assembled statement (transpiled by ``to_sqlite``)
against in-memory SQLite -- with a field dict covering several
keys, and assert the right columns changed. Two fragment-assembled
statements (``mark_failed``, ``cancel_unpublished_for_clips``) are
covered the same way; ``fail_stale_running`` is a documented strict
xfail because its always-present staleness clause uses Postgres
``make_interval``.

Known Postgres-tier remainders, documented not hidden:
- ``update_task_status(..., expected_statuses=[...])`` appends
  ``status = ANY(:expected_statuses)`` -- a Postgres array operator
  SQLite cannot express; the base assembled statement is executed
  here, the ANY variant needs the Postgres tier.
- ``SocialRepository.update_post`` with a ``hashtags`` key binds a
  Python list to a Postgres ``TEXT[]`` column; SQLite has no array
  type to bind, so that one key is exercised for shape (allowlist)
  only -- the scalar and jsonb keys are executed for real.
"""

import json

import pytest

from sql_exec_support import ExecSession

from src.repositories.brand_kit_repository import BrandKitRepository
from src.repositories.channel_monitor_repository import ChannelMonitorRepository
from src.repositories.clip_export_repository import ClipExportRepository
from src.repositories.publishing_repository import PublishingRepository
from src.repositories.recipe_repository import RecipeRepository
from src.repositories.social_repository import SocialRepository
from src.repositories.task_repository import TaskRepository
from src.repositories.watch_folder_repository import WatchFolderRepository

_TASKS_DDL = """
CREATE TABLE tasks (
    id TEXT PRIMARY KEY, user_id TEXT, status TEXT, progress INTEGER,
    progress_message TEXT, cache_hit INTEGER, error_code TEXT,
    stage_timings_json TEXT, started_at TEXT, completed_at TEXT,
    updated_at TEXT
)
"""

_MINUTE_USAGE_DDL = """
CREATE TABLE minute_usage_events (
    id TEXT PRIMARY KEY, user_id TEXT, task_id TEXT, minutes INTEGER,
    source_duration_seconds INTEGER, created_at TEXT, refunded_at TEXT
)
"""

_GENERATED_CLIPS_MIN_DDL = """
CREATE TABLE generated_clips (id TEXT PRIMARY KEY, task_id TEXT, file_path TEXT)
"""


def _seed_task(session: ExecSession, **overrides):
    row = {
        "id": "task-1", "user_id": "user-1", "status": "queued",
        "progress": 0, "progress_message": None, "cache_hit": 0,
        "error_code": None, "stage_timings_json": None,
        "started_at": None, "completed_at": None,
        "updated_at": "2026-11-01 00:00:00",
    }
    row.update(overrides)
    cols = ", ".join(row)
    marks = ", ".join(f":{c}" for c in row)
    session.conn.execute(f"INSERT INTO tasks ({cols}) VALUES ({marks})", row)
    return row


def _task_row(session: ExecSession):
    cur = session.conn.execute("SELECT * FROM tasks WHERE id = 'task-1'")
    cols = [d[0] for d in cur.description]
    return dict(zip(cols, cur.fetchone()))


# ---------------------------------------------------------------------------
# Dict-driven updates with a RETURNING + read-back flow
# ---------------------------------------------------------------------------

_BRAND_KITS_DDL = """
CREATE TABLE brand_kits (
    id TEXT, user_id TEXT, name TEXT, primary_color TEXT,
    accent_color TEXT, caption_template TEXT, font_name TEXT,
    title_style TEXT, music_track TEXT, logo_filename TEXT,
    logo_position TEXT, logo_opacity REAL, intro_asset TEXT,
    outro_asset TEXT, created_at TEXT, updated_at TEXT
)
"""


async def test_update_kit_method_executes_with_field_dict():
    session = ExecSession(_BRAND_KITS_DDL)
    session.conn.execute(
        "INSERT INTO brand_kits (id, user_id, name, primary_color, logo_position,"
        " logo_opacity, created_at, updated_at) VALUES"
        " ('kit-1', 'user-1', 'Old name', '#000000', 'bottom_right', 0.5,"
        " '2026-11-01', '2026-11-01')"
    )
    result = await BrandKitRepository.update_kit(
        session, "user-1", "kit-1",
        {"name": "Launch kit", "primary_color": "#7C5CFF", "logo_opacity": 0.9},
    )
    assert result is not None and result["name"] == "Launch kit"
    row = session.conn.execute(
        "SELECT name, primary_color, logo_opacity, logo_position FROM brand_kits"
    ).fetchone()
    assert row == ("Launch kit", "#7C5CFF", 0.9, "bottom_right")
    # Wrong owner: the UPDATE matches nothing and the method says so.
    assert await BrandKitRepository.update_kit(
        session, "user-2", "kit-1", {"name": "Hijack"}
    ) is None
    assert session.conn.execute(
        "SELECT name FROM brand_kits"
    ).fetchone()[0] == "Launch kit"


_CAMPAIGNS_DDL = """
CREATE TABLE campaigns (
    id TEXT, user_id TEXT, name TEXT, platform TEXT,
    campaign_url TEXT, budget REAL, notes TEXT, brief TEXT,
    example_url TEXT, created_at TEXT, updated_at TEXT
)
"""


async def test_update_campaign_method_executes_with_field_dict():
    session = ExecSession(_CAMPAIGNS_DDL)
    session.conn.execute(
        "INSERT INTO campaigns (id, user_id, name, platform, budget, created_at,"
        " updated_at) VALUES ('camp-1', 'user-1', 'Old', 'youtube', 100.0,"
        " '2026-11-01', '2026-11-01')"
    )
    result = await PublishingRepository.update_campaign(
        session, "user-1", "camp-1",
        {"name": "Expedia push", "budget": 8000.0, "notes": "approved footage only"},
    )
    assert result is not None and result["name"] == "Expedia push"
    row = session.conn.execute(
        "SELECT name, budget, notes FROM campaigns"
    ).fetchone()
    assert row == ("Expedia push", 8000.0, "approved footage only")


_CLIP_POSTS_DDL = """
CREATE TABLE clip_posts (
    id TEXT, clip_id TEXT, user_id TEXT, platform TEXT,
    post_url TEXT, posted_at TEXT, campaign_id TEXT,
    submission_url TEXT, submission_dismissed INTEGER,
    created_at TEXT, updated_at TEXT
)
"""


async def test_update_post_publishing_method_executes_with_field_dict():
    session = ExecSession(_CLIP_POSTS_DDL)
    session.conn.execute(
        "INSERT INTO clip_posts (id, clip_id, user_id, platform, campaign_id,"
        " created_at, updated_at) VALUES ('post-1', 'clip-1', 'user-1',"
        " 'youtube', NULL, '2026-11-01', '2026-11-01')"
    )
    result = await PublishingRepository.update_post(
        session, "user-1", "post-1",
        {
            "post_url": "https://youtube.com/shorts/abc",
            "posted_at": "2026-11-11T10:00:00",
            "campaign_id": "camp-1",
            "submission_url": "https://whop.com/sub/1",
        },
    )
    assert result is not None and result["campaign_id"] == "camp-1"
    row = session.conn.execute(
        "SELECT post_url, campaign_id, submission_url FROM clip_posts"
    ).fetchone()
    assert row == (
        "https://youtube.com/shorts/abc", "camp-1", "https://whop.com/sub/1",
    )


# ---------------------------------------------------------------------------
# Dict-driven updates behind a writable-column allowlist
# ---------------------------------------------------------------------------

_CHANNEL_MONITORS_DDL = """
CREATE TABLE channel_monitors (
    id TEXT, user_id TEXT, channel_id TEXT, channel_title TEXT,
    enabled INTEGER, niche_override TEXT, music_mode TEXT,
    music_override TEXT, template_mode TEXT, creation_mode TEXT,
    target_clip_count INTEGER, processing_mode TEXT, include_shorts INTEGER,
    max_tasks_per_day INTEGER, campaign_id TEXT, brand_kit_id TEXT,
    last_fallback_at TEXT, last_polled_at TEXT, last_error TEXT,
    consecutive_failures INTEGER, created_at TEXT, updated_at TEXT
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


async def test_update_monitor_method_executes_and_filters_keys():
    session = ExecSession(_CHANNEL_MONITORS_DDL)
    session.conn.execute(
        "INSERT INTO channel_monitors (id, user_id, channel_id, enabled,"
        " last_error, consecutive_failures) VALUES"
        " ('mon-1', 'user-1', 'UC1', 1, 'feed down', 2)"
    )
    repo = ChannelMonitorRepository()
    await repo.update_monitor(
        session, "mon-1",
        {
            "last_error": None, "consecutive_failures": 0,
            "channel_title": "TJR", "not_a_column": "ignored",
        },
    )
    row = session.conn.execute(
        "SELECT last_error, consecutive_failures, channel_title, enabled"
        " FROM channel_monitors"
    ).fetchone()
    # The non-writable key was dropped by the allowlist (no SQL error),
    # the writable ones landed.
    assert row == (None, 0, "TJR", 1)


async def test_update_video_method_executes_and_filters_keys():
    session = ExecSession(_CHANNEL_VIDEOS_DDL)
    session.conn.execute(
        "INSERT INTO channel_monitor_videos (id, user_id, monitor_id, video_id,"
        " state, attempts) VALUES ('vid-1', 'user-1', 'mon-1', 'yt-1',"
        " 'processing', 1)"
    )
    repo = ChannelMonitorRepository()
    await repo.update_video(
        session, "vid-1",
        {"state": "completed", "task_id": "task-1", "attempts": 2,
         "video_id": "not-writable"},
    )
    row = session.conn.execute(
        "SELECT state, task_id, attempts, video_id FROM channel_monitor_videos"
    ).fetchone()
    assert row == ("completed", "task-1", 2, "yt-1")


_WATCH_FILES_DDL = """
CREATE TABLE watch_folder_files (
    id TEXT, user_id TEXT, file_path TEXT, file_name TEXT,
    fingerprint TEXT, size_bytes INTEGER, mtime_ns INTEGER,
    state TEXT, task_id TEXT, upload_path TEXT, attempts INTEGER,
    last_error TEXT, created_at TEXT, updated_at TEXT
)
"""


async def test_update_file_method_executes_and_filters_keys():
    session = ExecSession(_WATCH_FILES_DDL)
    session.conn.execute(
        "INSERT INTO watch_folder_files (id, user_id, file_path, state, attempts)"
        " VALUES ('file-1', 'user-1', '/watch/a.mp4', 'processing', 1)"
    )
    repo = WatchFolderRepository()
    await repo.update_file(
        session, "file-1",
        {"state": "completed", "task_id": "task-1", "attempts": 2,
         "file_path": "/not/writable.mp4"},
    )
    row = session.conn.execute(
        "SELECT state, task_id, attempts, file_path FROM watch_folder_files"
    ).fetchone()
    assert row == ("completed", "task-1", 2, "/watch/a.mp4")


# ---------------------------------------------------------------------------
# TaskRepository runtime-assembled updates (kwargs-driven)
# ---------------------------------------------------------------------------


async def test_update_task_status_method_executes_base_shape():
    session = ExecSession(_TASKS_DDL)
    _seed_task(session, status="queued")
    changed = await TaskRepository.update_task_status(
        session, "task-1", "processing", progress=35,
        progress_message="Transcribing",
    )
    assert changed is True
    row = _task_row(session)
    assert row["status"] == "processing"
    assert row["progress"] == 35
    assert row["progress_message"] == "Transcribing"


async def test_update_task_status_terminal_triggers_refund_update():
    session = ExecSession(_TASKS_DDL, _MINUTE_USAGE_DDL, _GENERATED_CLIPS_MIN_DDL)
    _seed_task(session, status="processing")
    session.conn.execute(
        "INSERT INTO minute_usage_events VALUES"
        " ('mu-1', 'user-1', 'task-1', 3, 150, '2026-11-01', NULL)"
    )
    changed = await TaskRepository.update_task_status(session, "task-1", "failed")
    assert changed is True
    # The status change and the minute-usage refund are one flow: the
    # refund UPDATE (statement-covered in the core suite) executed too.
    assert session.conn.execute(
        "SELECT refunded_at FROM minute_usage_events"
    ).fetchone()[0] is not None


async def test_update_task_runtime_metadata_method_executes():
    session = ExecSession(_TASKS_DDL)
    _seed_task(session)
    await TaskRepository.update_task_runtime_metadata(
        session, "task-1", cache_hit=True, error_code="E_LLM",
        stage_timings_json='{"transcribe": 12.5}',
    )
    row = _task_row(session)
    assert row["cache_hit"] == 1
    assert row["error_code"] == "E_LLM"
    assert row["stage_timings_json"] == '{"transcribe": 12.5}'


# ---------------------------------------------------------------------------
# SocialRepository.update_post (kwargs, allowlist, jsonb special case)
# ---------------------------------------------------------------------------

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


async def test_social_update_post_method_executes_scalar_and_metrics_keys():
    session = ExecSession(_SOCIAL_POSTS_DDL)
    session.conn.execute(
        "INSERT INTO social_posts (id, user_id, clip_id, status, title)"
        " VALUES ('sp-1', 'user-1', 'clip-1', 'draft', 'old title')"
    )
    await SocialRepository.update_post(
        session, "sp-1", status="scheduled", title="New title",
        metrics={"views": 10, "likes": 2},
    )
    row = session.conn.execute(
        "SELECT status, title, metrics FROM social_posts"
    ).fetchone()
    assert row[0] == "scheduled" and row[1] == "New title"
    # metrics went through the CAST(:metrics AS jsonb) assignment
    # (transpiled to a bare bind of the JSON text).
    assert row[2] == '{"views": 10, "likes": 2}'
    # The allowlist rejects unknown columns before any SQL runs.
    with pytest.raises(ValueError):
        await SocialRepository.update_post(session, "sp-1", bogus_column=1)


# ---------------------------------------------------------------------------
# Fragment-assembled WHERE clauses, driven through the real methods
# ---------------------------------------------------------------------------

_CLIP_EXPORTS_DDL = """
CREATE TABLE clip_exports (
    id TEXT PRIMARY KEY, task_id TEXT, clip_id TEXT, user_id TEXT,
    status TEXT, progress INTEGER, file_path TEXT, error_message TEXT,
    options_hash TEXT, updated_at TEXT
)
"""


async def test_mark_failed_method_executes_with_status_fragment():
    session = ExecSession(_CLIP_EXPORTS_DDL)
    session.conn.execute(
        "INSERT INTO clip_exports (id, status, progress)"
        " VALUES ('exp-1', 'running', 40)"
    )
    session.conn.execute(
        "INSERT INTO clip_exports (id, status, progress)"
        " VALUES ('exp-2', 'succeeded', 100)"
    )
    # Default fragment: status IN ('queued', 'running').
    assert await ClipExportRepository.mark_failed(session, "exp-1", "encode died") is True
    row = session.conn.execute(
        "SELECT status, error_message FROM clip_exports WHERE id = 'exp-1'"
    ).fetchone()
    assert row == ("failed", "encode died")
    # A terminal row is outside the default fragment.
    assert await ClipExportRepository.mark_failed(session, "exp-2", "too late") is False
    assert session.conn.execute(
        "SELECT status FROM clip_exports WHERE id = 'exp-2'"
    ).fetchone()[0] == "succeeded"


async def test_cancel_unpublished_for_clips_both_clause_variants():
    session = ExecSession(_SOCIAL_POSTS_DDL, _GENERATED_CLIPS_MIN_DDL)
    session.conn.execute(
        "INSERT INTO generated_clips VALUES ('clip-1', 'task-1', '/media/a.mp4')"
    )
    session.conn.execute(
        "INSERT INTO generated_clips VALUES ('clip-2', 'task-1', '/media/b.mp4')"
    )
    session.conn.execute(
        "INSERT INTO social_posts (id, user_id, clip_id, status)"
        " VALUES ('sp-1', 'user-1', 'clip-1', 'scheduled')"
    )
    session.conn.execute(
        "INSERT INTO social_posts (id, user_id, clip_id, status)"
        " VALUES ('sp-2', 'user-1', 'clip-2', 'queued')"
    )
    # Variant 1: a single clip id.
    await SocialRepository.cancel_unpublished_for_clips(session, clip_id="clip-1")
    states = dict(session.conn.execute("SELECT id, status FROM social_posts").fetchall())
    assert states == {"sp-1": "cancelled", "sp-2": "queued"}
    # Variant 2: every clip of a task (subquery clause).
    await SocialRepository.cancel_unpublished_for_clips(session, task_id="task-1")
    states = dict(session.conn.execute("SELECT id, status FROM social_posts").fetchall())
    assert states == {"sp-1": "cancelled", "sp-2": "cancelled"}


@pytest.mark.xfail(
    strict=True,
    reason="Postgres-only: the always-present staleness clause is "
    "updated_at < NOW() - make_interval(secs => :stale_seconds) -- "
    "SQLite cannot parse the => named-argument syntax and has no "
    "make_interval. Needs the Postgres execution tier (Nov 17 list).",
)
async def test_fail_stale_running_method_executes():
    session = ExecSession(_CLIP_EXPORTS_DDL)
    session.conn.execute(
        "INSERT INTO clip_exports (id, status, progress, updated_at)"
        " VALUES ('exp-1', 'running', 40, '2000-01-01 00:00:00')"
    )
    count = await ClipExportRepository.fail_stale_running(
        session, message="worker died", stale_seconds=600
    )
    assert count == 1
    assert session.conn.execute(
        "SELECT status FROM clip_exports"
    ).fetchone()[0] == "failed"


_RECIPES_DDL = """
CREATE TABLE recipes (
    id TEXT PRIMARY KEY, user_id TEXT, name TEXT, settings TEXT,
    created_at TEXT, updated_at TEXT
)
"""


async def test_update_recipe_method_executes_with_field_dict():
    session = ExecSession(_RECIPES_DDL)
    session.conn.execute(
        "INSERT INTO recipes (id, user_id, name, settings, created_at, updated_at)"
        " VALUES ('recipe-1', 'user-1', 'Old name', '{}', '2026-11-15', '2026-11-15')"
    )
    result = await RecipeRepository.update_recipe(
        session, "user-1", "recipe-1",
        {"name": "New name", "settings": {"template_mode": "plain"}},
    )
    assert result is not None and result["name"] == "New name"
    row = session.conn.execute("SELECT name, settings FROM recipes").fetchone()
    assert row[0] == "New name"
    assert json.loads(row[1]) == {"template_mode": "plain"}
    # Wrong owner: the UPDATE matches nothing and the method says so.
    result = await RecipeRepository.update_recipe(
        session, "user-2", "recipe-1", {"name": "Hijack"}
    )
    assert result is None
