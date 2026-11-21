"""Per-task B-roll switch (2026-11-12): the create form's own On/Off.

The switch travels on the task row (``tasks.broll_enabled``): NULL is
the template default (Auto plans B-roll, Plain never does), an explicit
value wins in Auto, and Plain resolves Off regardless. These tests pin
the three layers:

- the validator + resolver (``auto_director.broll_setting``),
- the plan-level gate: resolved Off plans ZERO cues and makes ZERO
  stock-provider calls, while On (or NULL in Auto) plans byte-identical
  cues to the pre-switch pipeline,
- the task create/read round-trip through the real repository SQL,
  including the NULL case,
- the migration file: runner-safe (no semicolons in comments) and
  applicable.
"""

import sqlite3
from pathlib import Path

import pytest

from sql_exec_support import ExecSession

from src.auto_director.broll_setting import (
    resolve_broll_enabled,
    validate_broll_enabled,
)
from src.auto_director.edl import build_edl, load_profile
from src.media import edl_render, music_library, pixabay_broll
from src.repositories.task_repository import TaskRepository
from src.services.video_service import VideoService


# ---------------------------------------------------------------------------
# Validator + resolver
# ---------------------------------------------------------------------------


def test_validate_broll_enabled_absent_is_none():
    assert validate_broll_enabled(None) is None


def test_validate_broll_enabled_accepts_booleans_and_spellings():
    assert validate_broll_enabled(True) is True
    assert validate_broll_enabled(False) is False
    assert validate_broll_enabled("false") is False
    assert validate_broll_enabled(" ON ") is True
    assert validate_broll_enabled("0") is False


def test_validate_broll_enabled_rejects_garbage():
    for bad in ("yes please", "", 1, 0, [], {}, 1.5):
        with pytest.raises(ValueError):
            validate_broll_enabled(bad)


@pytest.mark.parametrize(
    "template_mode,broll_enabled,expected",
    [
        ("plain", True, False),   # Plain's identity is unchanged: never on
        ("plain", False, False),
        ("plain", None, False),
        ("auto", True, True),
        ("auto", False, False),   # the explicit Off the user asked for
        ("auto", None, True),     # NULL = template default
        (None, None, True),
        (None, False, False),
        (None, True, True),
    ],
)
def test_resolve_broll_enabled_matrix(template_mode, broll_enabled, expected):
    assert resolve_broll_enabled(template_mode, broll_enabled) is expected


# ---------------------------------------------------------------------------
# Plan-level gate: build_edl
# ---------------------------------------------------------------------------


def _words(*nouns):
    return [
        {"word": noun, "start": float(i), "end": float(i) + 0.5}
        for i, noun in enumerate(nouns)
    ]


_CLIP = {"id": "c1", "start": 0, "end": 60, "duration": 60}
_NOUNS = ("mountain", "beach", "city")


def test_build_edl_broll_off_plans_zero_cues():
    profile = load_profile("travel_vlog")
    baseline = build_edl(_CLIP, _words(*_NOUNS), profile, [])
    assert baseline["broll"], "fixture must plan cues with the switch on"

    off = build_edl(_CLIP, _words(*_NOUNS), profile, [], broll_enabled=False)
    assert off["broll"] == []
    # Everything else about the plan is untouched.
    assert off["segments"] == baseline["segments"]
    assert off["captions"] == baseline["captions"]
    assert off["hook"] == baseline["hook"]


def test_build_edl_broll_on_is_byte_identical_to_default():
    profile = load_profile("travel_vlog")
    default = build_edl(_CLIP, _words(*_NOUNS), profile, [])
    explicit_on = build_edl(
        _CLIP, _words(*_NOUNS), profile, [], broll_enabled=True
    )
    assert explicit_on == default


# ---------------------------------------------------------------------------
# Plan-level gate: build_pipeline_edl (zero stock-provider calls)
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


def test_pipeline_broll_off_zero_cues_and_zero_stock_calls(
    tmp_path, monkeypatch
):
    _library(tmp_path, monkeypatch)
    calls = _spy_resolve(monkeypatch)

    edl = VideoService.build_pipeline_edl(
        _segment(), _pipeline_words(), niche_override="travel_vlog",
        duration=60.0, template_mode="auto", broll_enabled=False,
    )
    assert edl is not None
    assert edl["broll"] == []
    assert calls == [], "no stock provider call may happen when B-roll is off"


def test_pipeline_broll_on_plans_and_resolves_as_today(tmp_path, monkeypatch):
    _library(tmp_path, monkeypatch)
    calls = _spy_resolve(monkeypatch)

    edl = VideoService.build_pipeline_edl(
        _segment(), _pipeline_words(), niche_override="travel_vlog",
        duration=60.0, template_mode="auto", broll_enabled=True,
    )
    assert edl is not None
    assert edl["broll"], "travel vlog with the switch on must plan cues"
    assert len(calls) == 1, "plan-time stock resolution runs exactly as today"


def test_pipeline_broll_null_in_auto_behaves_as_on(tmp_path, monkeypatch):
    _library(tmp_path, monkeypatch)
    calls = _spy_resolve(monkeypatch)

    edl = VideoService.build_pipeline_edl(
        _segment(), _pipeline_words(), niche_override="travel_vlog",
        duration=60.0, template_mode="auto", broll_enabled=None,
    )
    assert edl is not None
    assert edl["broll"]
    assert len(calls) == 1


def test_pipeline_broll_on_still_loses_to_plain(tmp_path, monkeypatch):
    _library(tmp_path, monkeypatch)
    calls = _spy_resolve(monkeypatch)

    edl = VideoService.build_pipeline_edl(
        _segment(), _pipeline_words(), niche_override="travel_vlog",
        duration=60.0, template_mode="plain", broll_enabled=True,
    )
    assert edl is not None
    assert edl["broll"] == []
    assert calls == []


# ---------------------------------------------------------------------------
# Re-render builder: the stored value is honoured on re-renders
# ---------------------------------------------------------------------------


def test_rerender_edl_broll_off_plans_zero_cues():
    from src.services.clip_service import ClipEditingMixin

    clip = {"id": "clip-1", "text": "the mountain and the beach and the city"}
    off = ClipEditingMixin._build_rerender_edl(
        clip, 0.0, 60.0, "travel_vlog", None,
        template_mode="auto", broll_enabled=False,
    )
    assert off["broll"] == []

    on = ClipEditingMixin._build_rerender_edl(
        clip, 0.0, 60.0, "travel_vlog", None,
        template_mode="auto", broll_enabled=True,
    )
    assert on["broll"], "re-render with the switch on keeps planning cues"


# ---------------------------------------------------------------------------
# Task create/read round-trip through the real repository SQL
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


async def _round_trip(broll_enabled):
    session = ExecSession(_TASKS_DDL, _SOURCES_DDL)
    session.conn.execute(
        "INSERT INTO sources (id, title, type, url) VALUES "
        "('src-1', 'A video', 'youtube', 'https://example.invalid/v')"
    )
    task_id = await TaskRepository.create_task(
        session,
        user_id="user-1",
        source_id="src-1",
        template_mode="auto",
        broll_enabled=broll_enabled,
        source_duration_seconds=60,
    )
    stored = session.conn.execute(
        "SELECT broll_enabled FROM tasks WHERE id = ?", (task_id,)
    ).fetchone()[0]
    task = await TaskRepository.get_task_by_id(session, task_id)
    assert task is not None
    return stored, task["broll_enabled"]


async def test_task_round_trip_broll_off_persists():
    stored, read_back = await _round_trip(False)
    assert not stored
    assert not read_back


async def test_task_round_trip_broll_on_persists():
    stored, read_back = await _round_trip(True)
    assert stored
    assert read_back


async def test_task_round_trip_broll_null_persists():
    stored, read_back = await _round_trip(None)
    assert stored is None
    assert read_back is None


# ---------------------------------------------------------------------------
# Migration: runner-safe and applicable
# ---------------------------------------------------------------------------

_MIGRATION = (
    Path(__file__).resolve().parents[2]
    / "src" / "migrations" / "sql" / "20261112_0001_task_broll_enabled.sql"
)


def test_broll_migration_is_runner_safe_and_applies():
    sql = _MIGRATION.read_text()
    # The migration runner splits on every semicolon, so comments must
    # never contain one (the discipline test_migration_files_safety.py
    # enforces tree-wide; pinned here for this lane's file by name).
    for line in sql.splitlines():
        if line.strip().startswith("--"):
            assert ";" not in line
    assert "broll_enabled" in sql

    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE tasks (id TEXT)")
    # SQLite spells ADD COLUMN without IF NOT EXISTS; normalise only
    # that clause, the statement itself is the file's own.
    statement = sql.replace("ADD COLUMN IF NOT EXISTS", "ADD COLUMN")
    for fragment in statement.split(";"):
        body = "\n".join(
            line for line in fragment.splitlines()
            if not line.strip().startswith("--")
        ).strip()
        if body:
            conn.execute(body)
    columns = [row[1] for row in conn.execute("PRAGMA table_info(tasks)")]
    assert "broll_enabled" in columns


# ---------------------------------------------------------------------------
# The legacy-flag gate (U3): resolve_broll_gate
#
# Task rows carry BOTH the per-task switch and the legacy include_broll
# flag. The old seams ANDed them, so toggle On + legacy Off silently
# rendered no B-roll, and toggle Off + legacy On silently rendered it.
# The gate makes the explicit switch authoritative; the legacy flag is
# honoured ONLY when the switch was never set.
# ---------------------------------------------------------------------------

from src.auto_director.broll_setting import resolve_broll_gate  # noqa: E402


@pytest.mark.parametrize(
    "template_mode,broll_enabled,include_broll,expected",
    [
        # Plain: Off no matter what either flag says.
        ("plain", True, True, False),
        ("plain", True, False, False),
        ("plain", True, None, False),
        ("plain", False, True, False),
        ("plain", False, False, False),
        ("plain", False, None, False),
        ("plain", None, True, False),
        ("plain", None, False, False),
        ("plain", None, None, False),
        # Auto, explicit switch: authoritative, legacy ignored — the
        # first row is the U3 defect case (On + chip Off used to
        # render NO B-roll).
        ("auto", True, True, True),
        ("auto", True, False, True),
        ("auto", True, None, True),
        ("auto", False, True, False),
        ("auto", False, False, False),
        ("auto", False, None, False),
        # Auto, switch never set: the legacy flag is honoured; absent
        # legacy falls back to the template default (On).
        ("auto", None, True, True),
        ("auto", None, False, False),
        ("auto", None, None, True),
        # No template stored: same as Auto.
        (None, True, True, True),
        (None, True, False, True),
        (None, True, None, True),
        (None, False, True, False),
        (None, False, False, False),
        (None, False, None, False),
        (None, None, True, True),
        (None, None, False, False),
        (None, None, None, True),
    ],
)
def test_resolve_broll_gate_matrix(
    template_mode, broll_enabled, include_broll, expected
):
    assert (
        resolve_broll_gate(template_mode, broll_enabled, include_broll)
        is expected
    )


# ---------------------------------------------------------------------------
# Regeneration seam: the gate decides whether stored cutaways carry
# (mirrors test_regenerate_vocals_stem's harness; the video fake records
# the batch render call instead of rendering).
# ---------------------------------------------------------------------------

from types import SimpleNamespace  # noqa: E402
from unittest.mock import AsyncMock  # noqa: E402

from src.clip_source_map import save_clip_source_ranges  # noqa: E402
from src.services.task_service import TaskService  # noqa: E402
from test_clip_rerender import FakeClipRepo, _clip_dict, edit_boundary  # noqa: E402, F401

_STORED_BROLL = [
    {"path": "/stock/cutaway.mp4", "start": 1.0, "duration": 3.0}
]


class _GateRecordingVideoService:
    def __init__(self, source_path: Path):
        self.source_path = source_path
        self.create_video_clips_calls = []

    async def download_video(self, url, quality=None, task_id=None, ranges=None):
        return str(self.source_path)

    async def prepare_source(self, path):
        return path

    async def create_video_clips(
        self,
        video_path,
        segments,
        font_family=None,
        font_size=None,
        font_color=None,
        caption_template="default",
        output_format="vertical",
        add_subtitles=True,
        cleanup_settings=None,
        max_short_side=None,
        max_clip_seconds=60.0,
        include_broll=False,
        **kwargs,
    ):
        self.create_video_clips_calls.append(
            {"segments": segments, "include_broll": include_broll}
        )
        return [
            {
                "clip_id": 1,
                "filename": "clip_regenerated.mp4",
                "path": str(video_path.parent / "clip_regenerated.mp4"),
                "start_time": "00:10",
                "end_time": "00:20",
                "duration": 10.0,
                "text": "the truth is the data shows 42 percent growth",
                "relevance_score": 0.9,
                "reasoning": "Regenerated with updated settings",
            }
        ]


class _GateFakeTaskRepo:
    def __init__(self, task):
        self.task = task

    async def get_task_by_id(self, _db, _task_id):
        return self.task

    async def update_task_clips(self, _db, task_id, clip_ids):
        pass


class _GateFakeClipRepo(FakeClipRepo):
    def __init__(self, clip):
        super().__init__(clip)
        self.created = []

    async def get_clips_by_task(self, _db, task_id):
        return [dict(self.clip)] if self.clip is not None else []

    async def delete_clips_by_task(self, _db, task_id):
        pass

    async def create_clip(self, _db, **kwargs):
        self.created.append(kwargs)
        return "clip-new-1"


def _gate_service(tmp_path, monkeypatch, *, task_fields):
    source_path = tmp_path / "source.mp4"
    source_path.write_bytes(b"source")
    clip_path = tmp_path / "clip.mp4"
    clip_path.write_bytes(b"clip")
    save_clip_source_ranges(clip_path, [(10.0, 20.0)])

    service = TaskService(db=None)
    service.clip_repo = _GateFakeClipRepo(_clip_dict(clip_path))
    task = {
        "id": "task-1",
        "user_id": "user-1",
        "source_url": "https://www.youtube.com/watch?v=abcdefghijk",
        "source_type": "youtube",
        "processing_mode": "fast",
        "caption_template": "default",
        "font_family": None,
        "font_size": None,
        "font_color": None,
        "niche_override": None,
        "template_mode": "auto",
        "include_broll": False,
        "broll_enabled": None,
    }
    task.update(task_fields)
    service.task_repo = _GateFakeTaskRepo(task)
    service.cache_repo = SimpleNamespace(get_cache=AsyncMock(return_value=None))
    service.video_service = _GateRecordingVideoService(source_path)
    service._media_limits = AsyncMock(return_value=(None, None))
    service._cleanup_source_video = lambda *args: None
    service._resolve_render_vocals_stem = AsyncMock(return_value=None)

    async def fake_settings(_task_id):
        return {
            "output_format": "vertical",
            "add_subtitles": True,
            "clip_length": "auto",
            "cut_long_pauses": False,
            "pause_threshold_ms": 1200,
            "remove_filler_words": False,
            "filtered_words": [],
        }

    monkeypatch.setattr(service, "_load_task_source_settings", fake_settings)
    monkeypatch.setattr(
        TaskService,
        "_get_clip_broll",
        staticmethod(lambda _clip: [dict(_STORED_BROLL[0])]),
    )
    return service


@pytest.mark.asyncio
async def test_regenerate_toggle_on_beats_legacy_off(
    edit_boundary, monkeypatch, tmp_path
):
    """U3 at the regeneration seam: switch On + legacy Off must carry
    the stored cutaways (the old AND dropped them silently)."""
    service = _gate_service(
        tmp_path,
        monkeypatch,
        task_fields={"broll_enabled": True, "include_broll": False},
    )
    await service.regenerate_all_clips_for_task(
        "task-1", None, None, None, "default"
    )
    calls = service.video_service.create_video_clips_calls
    assert len(calls) == 1
    assert calls[0]["include_broll"] is True
    assert calls[0]["segments"][0]["broll"] == _STORED_BROLL


@pytest.mark.asyncio
async def test_regenerate_toggle_off_beats_legacy_on(
    edit_boundary, monkeypatch, tmp_path
):
    """Switch Off + legacy On: the explicit Off wins the same way."""
    service = _gate_service(
        tmp_path,
        monkeypatch,
        task_fields={"broll_enabled": False, "include_broll": True},
    )
    await service.regenerate_all_clips_for_task(
        "task-1", None, None, None, "default"
    )
    calls = service.video_service.create_video_clips_calls
    assert len(calls) == 1
    assert calls[0]["include_broll"] is False
    assert calls[0]["segments"][0]["broll"] == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "include_broll,expected",
    [(True, True), (False, False)],
)
async def test_regenerate_unset_switch_honours_legacy(
    edit_boundary, monkeypatch, tmp_path, include_broll, expected
):
    """Switch never set (pre-switch task): the legacy flag decides,
    exactly the backward compatibility the gate promises."""
    service = _gate_service(
        tmp_path,
        monkeypatch,
        task_fields={"broll_enabled": None, "include_broll": include_broll},
    )
    await service.regenerate_all_clips_for_task(
        "task-1", None, None, None, "default"
    )
    calls = service.video_service.create_video_clips_calls
    assert len(calls) == 1
    assert calls[0]["include_broll"] is expected
    assert (calls[0]["segments"][0]["broll"] == _STORED_BROLL) is expected
