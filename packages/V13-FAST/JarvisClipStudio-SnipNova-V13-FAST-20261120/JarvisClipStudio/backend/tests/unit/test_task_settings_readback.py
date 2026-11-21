"""Regression: settings written at task creation must survive the read path.

``TaskRepository.create_task`` has always written ``template_mode``,
``music_mode``, ``separation_opt_in`` and ``source_duration_seconds``
to the tasks row, and the pipeline reads all four back off the task
dict: the render loop resolves ``project_template_mode`` /
``project_music_mode`` from ``get_task_by_id`` (task_service), the
separation opt-in is read from the same dict before transcription,
and the stream-download paths read ``source_duration_seconds`` from
it. But the hand-built serialiser dicts in ``get_task_by_id`` and
``get_user_tasks`` never carried those four keys, so every read
resolved them to ``None``: a task created as Plain — just cuts was
planned and rendered with the Auto template (profile music, template
transitions, and — for rows whose ``broll_enabled`` is NULL — stock
B-roll), a stored Music Off was ignored, and a separation opt-in
never separated. Repository unit tests mock the DB session or assert
only the field their own lane added, so no test ever executed the
real create -> read round-trip for these fields before shipping --
the same blind spot as the 2026-11-11 duplicate-column INSERT (see
``test_task_insert_columns.py``).

These tests drive the REAL repository methods: ``create_task`` then
``get_task_by_id`` / ``get_user_tasks`` through ``ExecSession`` (the
statement-execution harness from ``sql_exec_support``), plus a
pipeline-level test that feeds the round-tripped dict into
``VideoService.build_pipeline_edl`` exactly the way the render loop
does, and a PostgreSQL round-trip that runs when ``SQL_EXEC_PG_URL``
points at a live database (the ``test_sql_exec_postgres_tier``
pattern).
"""

import os
from pathlib import Path

import pytest

from sql_exec_support import ExecSession

from src.auto_director.broll_setting import resolve_broll_enabled
from src.media import edl_render, music_library, pixabay_broll
from src.repositories.task_repository import TaskRepository
from src.services.video_service import VideoService


# ---------------------------------------------------------------------------
# Round-trip through the real repository SQL (ExecSession)
# ---------------------------------------------------------------------------

_TASKS_DDL = """
CREATE TABLE tasks (
    id TEXT, user_id TEXT, source_id TEXT, status TEXT,
    font_family TEXT, font_size INTEGER, font_color TEXT,
    caption_template TEXT, include_broll INTEGER, processing_mode TEXT,
    niche_override TEXT, music_override TEXT, music_mode TEXT,
    template_mode TEXT, framing_mode TEXT, layout_template TEXT, layout_credit TEXT, hook_title_enabled INTEGER, broll_enabled INTEGER, separation_opt_in INTEGER,
    target_clip_count INTEGER, source_duration_seconds INTEGER,
    content_brief TEXT, campaign_id TEXT, brand_kit_id TEXT,
    intro_asset TEXT, outro_asset TEXT, creation_mode TEXT,
    manual_ranges_json TEXT, generated_clips_ids TEXT,
    progress INTEGER, progress_message TEXT, brief_report TEXT,
    classification TEXT, cache_hit INTEGER, error_code TEXT,
    stage_timings_json TEXT, started_at TEXT, completed_at TEXT,
    completion_notification_sent_at TEXT,
    created_at TEXT, updated_at TEXT
)
"""

_SOURCES_DDL = """
CREATE TABLE sources (id TEXT, title TEXT, type TEXT, url TEXT)
"""

_CLIPS_DDL = """
CREATE TABLE generated_clips (
    id TEXT, task_id TEXT, virality_score REAL, clip_order INTEGER
)
"""


def _session() -> ExecSession:
    session = ExecSession(_TASKS_DDL, _SOURCES_DDL, _CLIPS_DDL)
    session.conn.execute(
        "INSERT INTO sources (id, title, type, url) VALUES "
        "('src-1', 'A video', 'youtube', 'https://example.invalid/v')"
    )
    return session


async def _create(session, **overrides):
    kwargs = dict(
        user_id="user-1",
        source_id="src-1",
        source_duration_seconds=2365,
    )
    kwargs.update(overrides)
    return await TaskRepository.create_task(session, **kwargs)


def _stored_row(session, task_id):
    cursor = session.conn.execute(
        "SELECT template_mode, music_mode, broll_enabled, "
        "separation_opt_in, source_duration_seconds "
        "FROM tasks WHERE id = ?",
        (task_id,),
    )
    return cursor.fetchone()


async def test_plain_task_settings_survive_get_task_by_id():
    session = _session()
    task_id = await _create(
        session,
        template_mode="plain",
        music_mode="off",
        broll_enabled=False,
        separation_opt_in=True,
    )
    # The write side really stored the settings (guards against a
    # vacuous pass if create ever stops writing them).
    stored = _stored_row(session, task_id)
    assert stored[0] == "plain"
    assert stored[1] == "off"
    assert not stored[2]
    assert stored[3]
    assert stored[4] == 2365

    task = await TaskRepository.get_task_by_id(session, task_id)
    assert task is not None
    # Read back exactly the way the pipeline reads them (.get).
    assert task.get("template_mode") == "plain"
    assert task.get("music_mode") == "off"
    assert not task.get("broll_enabled")
    assert bool(task.get("separation_opt_in")) is True
    assert task.get("source_duration_seconds") == 2365


async def test_auto_task_settings_survive_get_task_by_id():
    session = _session()
    task_id = await _create(
        session,
        template_mode="auto",
        music_mode="on",
        broll_enabled=True,
        separation_opt_in=False,
    )
    task = await TaskRepository.get_task_by_id(session, task_id)
    assert task is not None
    assert task.get("template_mode") == "auto"
    assert task.get("music_mode") == "on"
    assert task.get("broll_enabled")
    assert bool(task.get("separation_opt_in")) is False
    assert task.get("source_duration_seconds") == 2365


async def test_plain_task_settings_survive_get_user_tasks():
    session = _session()
    task_id = await _create(
        session,
        template_mode="plain",
        music_mode="off",
        broll_enabled=False,
        separation_opt_in=True,
    )
    tasks = await TaskRepository.get_user_tasks(session, "user-1")
    mine = [t for t in tasks if t["id"] == task_id]
    assert len(mine) == 1
    task = mine[0]
    assert task.get("template_mode") == "plain"
    assert task.get("music_mode") == "off"
    assert not task.get("broll_enabled")
    assert bool(task.get("separation_opt_in")) is True
    assert task.get("source_duration_seconds") == 2365


async def test_legacy_null_settings_read_back_as_none():
    """Rows that predate the controls store NULL and must keep
    reading as None (the legacy behaves-as-Auto rule), not crash."""
    session = _session()
    task_id = await _create(session)
    task = await TaskRepository.get_task_by_id(session, task_id)
    assert task is not None
    assert task.get("template_mode") is None
    assert task.get("music_mode") is None
    assert task.get("broll_enabled") is None
    # separation_opt_in is a NOT NULL flag (False for pre-feature rows):
    # it must read back falsy, which process_task coerces to off.
    assert not task.get("separation_opt_in")


# ---------------------------------------------------------------------------
# Pipeline level: the render loop's own reads, fed by the round-trip
# ---------------------------------------------------------------------------


def _library(tmp_path: Path, monkeypatch) -> Path:
    assets = tmp_path / "assets"
    (assets / "music").mkdir(parents=True)
    (assets / "sfx").mkdir(parents=True)
    (assets / "music" / "calm.wav").touch()
    user_dir = tmp_path / "user_music"
    user_dir.mkdir()
    monkeypatch.setattr(edl_render, "ASSETS_DIR", assets)
    monkeypatch.setattr(music_library, "user_music_dir", lambda: user_dir)
    return assets


def _segment():
    return {"start_time": "00:00", "end_time": "01:00",
            "text": "we saw the mountain then the beach and the city"}


def _pipeline_words():
    return [
        {"word": word, "start": float(i), "end": float(i) + 0.5}
        for i, word in enumerate(
            ["we", "saw", "the", "mountain", "then", "the", "beach",
             "and", "the", "city"]
        )
    ]


def _spy_resolve(monkeypatch):
    calls = []

    def _spy(edl):
        calls.append(edl)
        return edl

    monkeypatch.setattr(pixabay_broll, "resolve_edl_broll", _spy)
    return calls


def _render_loop_edl(task_row, monkeypatch, tmp_path):
    """Reproduce task_service's render-loop resolution verbatim:
    the project_* values come from the task dict, and the same
    kwargs go into build_pipeline_edl (via create_single_clip)."""
    _library(tmp_path, monkeypatch)
    calls = _spy_resolve(monkeypatch)
    project_music_mode = task_row.get("music_mode")
    project_template_mode = task_row.get("template_mode")
    project_broll_enabled = task_row.get("broll_enabled")
    edl = VideoService.build_pipeline_edl(
        _segment(), _pipeline_words(), niche_override="travel_vlog",
        duration=60.0,
        music_override=task_row.get("music_override"),
        music_mode=project_music_mode,
        template_mode=project_template_mode,
        broll_enabled=resolve_broll_enabled(
            project_template_mode, project_broll_enabled
        ),
    )
    return edl, calls


async def test_db_round_tripped_plain_task_builds_plain_edl(
    tmp_path, monkeypatch
):
    """A Plain task whose broll_enabled is NULL (channel-monitor and
    other non-form creators store it that way) must still plan as
    Plain end to end: stamped EDL, no cues, no stock calls, no bed."""
    session = _session()
    task_id = await _create(
        session, template_mode="plain", music_mode="off",
        broll_enabled=None, separation_opt_in=False,
    )
    task_row = await TaskRepository.get_task_by_id(session, task_id)

    edl, calls = _render_loop_edl(task_row, monkeypatch, tmp_path)
    assert edl is not None
    assert edl.get("template") == "plain"
    assert edl["broll"] == []
    assert calls == [], "a Plain task must never resolve stock footage"
    # The proven no-music shape (plainify/apply_music_off stamp it).
    assert edl["audio"].get("music_track") == ""
    assert edl["audio"].get("music_source") == "user_none"
    assert edl["audio"].get("music_mood") == "none"
    assert all(
        segment.get("transition_in") == "none"
        for segment in edl["segments"]
    )


async def test_db_round_tripped_music_off_auto_task_builds_silent_edl(
    tmp_path, monkeypatch
):
    """Music Off stored on an Auto task must reach the music chain:
    the built EDL carries the proven no-music shape."""
    session = _session()
    task_id = await _create(
        session, template_mode="auto", music_mode="off",
        broll_enabled=True, separation_opt_in=False,
    )
    task_row = await TaskRepository.get_task_by_id(session, task_id)

    edl, _calls = _render_loop_edl(task_row, monkeypatch, tmp_path)
    assert edl is not None
    assert edl.get("template") != "plain"
    assert edl["audio"].get("music_track") == ""
    assert edl["audio"].get("music_source") == "user_none"
    assert edl["audio"].get("music_mood") == "none"
    assert edl["audio"].get("music_volume") == 0.0


# ---------------------------------------------------------------------------
# PostgreSQL round-trip (opt-in: SQL_EXEC_PG_URL, tier pattern)
# ---------------------------------------------------------------------------

PG_URL = os.getenv("SQL_EXEC_PG_URL")

_PG_TASKS_DDL = """
CREATE TABLE tasks (
    id VARCHAR(36) PRIMARY KEY,
    user_id VARCHAR(36), source_id VARCHAR(36),
    generated_clips_ids JSONB, status VARCHAR(20),
    progress INTEGER, progress_message TEXT,
    font_family VARCHAR(100), font_size INTEGER, font_color VARCHAR(20),
    caption_template VARCHAR(50), include_broll BOOLEAN,
    processing_mode VARCHAR(20), niche_override VARCHAR(40),
    music_override VARCHAR(255), music_mode VARCHAR(10),
    template_mode VARCHAR(10), framing_mode VARCHAR(10), broll_enabled BOOLEAN,
    separation_opt_in BOOLEAN, brand_kit_id VARCHAR(36),
    intro_asset TEXT, outro_asset TEXT, target_clip_count INTEGER,
    content_brief TEXT, brief_report TEXT, classification TEXT,
    campaign_id VARCHAR(36), creation_mode VARCHAR(20),
    manual_ranges_json TEXT, started_at TIMESTAMPTZ,
    completed_at TIMESTAMPTZ, cache_hit BOOLEAN, error_code VARCHAR(50),
    stage_timings_json TEXT, source_metadata JSONB,
    source_duration_seconds INTEGER,
    completion_notification_sent_at TIMESTAMPTZ,
    share_token VARCHAR(64), share_enabled BOOLEAN,
    created_at TIMESTAMPTZ, updated_at TIMESTAMPTZ
)
"""

_PG_SOURCES_DDL = """
CREATE TABLE sources (
    id VARCHAR(36) PRIMARY KEY, type VARCHAR(20), title TEXT, url TEXT,
    created_at TIMESTAMPTZ, updated_at TIMESTAMPTZ
)
"""

_PG_CLIPS_DDL = """
CREATE TABLE generated_clips (
    id VARCHAR(36) PRIMARY KEY, task_id VARCHAR(36),
    virality_score DOUBLE PRECISION, clip_order INTEGER
)
"""


@pytest.mark.skipif(not PG_URL, reason="SQL_EXEC_PG_URL not set")
async def test_postgres_round_trip_task_settings():
    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
    from sqlalchemy.pool import NullPool

    engine = create_async_engine(PG_URL, poolclass=NullPool)
    try:
        async with engine.begin() as conn:
            await conn.execute(text("DROP TABLE IF EXISTS generated_clips"))
            await conn.execute(text("DROP TABLE IF EXISTS tasks"))
            await conn.execute(text("DROP TABLE IF EXISTS sources"))
            await conn.execute(text(_PG_TASKS_DDL))
            await conn.execute(text(_PG_SOURCES_DDL))
            await conn.execute(text(_PG_CLIPS_DDL))
        maker = async_sessionmaker(engine, expire_on_commit=False)
        async with maker() as session:
            await session.execute(
                text(
                    "INSERT INTO sources (id, type, title, url) VALUES "
                    "('src-1', 'youtube', 'A video', "
                    "'https://example.invalid/v')"
                )
            )
            await session.commit()
            task_id = await TaskRepository.create_task(
                session,
                user_id="user-1",
                source_id="src-1",
                template_mode="plain",
                music_mode="off",
                broll_enabled=False,
                separation_opt_in=True,
                source_duration_seconds=2365,
            )
            task = await TaskRepository.get_task_by_id(session, task_id)
            assert task is not None
            assert task.get("template_mode") == "plain"
            assert task.get("music_mode") == "off"
            assert task.get("broll_enabled") is False
            assert task.get("separation_opt_in") is True
            assert task.get("source_duration_seconds") == 2365

            tasks = await TaskRepository.get_user_tasks(session, "user-1")
            mine = [t for t in tasks if t["id"] == task_id]
            assert len(mine) == 1
            assert mine[0].get("template_mode") == "plain"
            assert mine[0].get("music_mode") == "off"
        async with engine.begin() as conn:
            await conn.execute(text("DROP TABLE IF EXISTS generated_clips"))
            await conn.execute(text("DROP TABLE IF EXISTS tasks"))
            await conn.execute(text("DROP TABLE IF EXISTS sources"))
    finally:
        await engine.dispose()
