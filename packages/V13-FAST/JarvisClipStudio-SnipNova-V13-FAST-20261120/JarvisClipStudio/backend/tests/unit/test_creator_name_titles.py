"""Creator-name wiring for hook titles.

The first-person guardrail (title_person) could already attribute a
rewrite to a named creator ("I Try…" + "TJR" -> "TJR Tries…"), but no
caller passed a name: tasks stored no channel/uploader field. This
suite pins the wiring that fixes that:

* capture — ``create_task_with_source`` stamps the cleaned creator
  name into the task's ``source_metadata``;
* read — ``ClipRepository.create_clip`` (the single write path for
  generated titles) resolves the stored name when no caller passes
  one, so titles produced by a task whose source channel is known are
  attributed through the REAL persistence path;
* safety — an unknown creator keeps the neutral rewrite, a lookup
  failure never breaks clip creation, and clean titles stay
  byte-identical with or without a stored name.
"""

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from src.auto_director.title_person import (
    clean_creator_name,
    contains_first_person,
    creator_name_from_metadata,
)

# --- metadata helpers ---------------------------------------------------------


def test_metadata_helper_reads_stored_creator_name():
    assert creator_name_from_metadata({"creator_name": "TJR"}) == "TJR"
    assert creator_name_from_metadata({"uploader": "TJR"}) == "TJR"
    assert creator_name_from_metadata({"channel": "TJR"}) == "TJR"


def test_metadata_helper_accepts_json_strings():
    stored = json.dumps({"creator_name": "TJR"})
    assert creator_name_from_metadata(stored) == "TJR"


def test_metadata_helper_returns_none_when_unknown_or_unusable():
    assert creator_name_from_metadata(None) is None
    assert creator_name_from_metadata({}) is None
    assert creator_name_from_metadata({"creator_name": "  "}) is None
    assert creator_name_from_metadata("not json") is None
    assert creator_name_from_metadata(42) is None
    # A "name" phrased in the first person is not a creator name.
    assert creator_name_from_metadata({"creator_name": "I"}) is None
    assert clean_creator_name("  TJR  ") == "TJR"
    assert clean_creator_name("") is None


# --- capture: create_task_with_source stamps the name -------------------------


def _stubbed_task_service():
    from src.services.task_service import TaskService

    service = TaskService.__new__(TaskService)
    service.db = object()
    service.reused_upload_task = False
    service.reused_upload_job_id = None
    task_repo = SimpleNamespace(
        user_exists=AsyncMock(return_value=True),
        create_task=AsyncMock(return_value="task-1"),
        save_source_metadata=AsyncMock(),
        find_recent_upload_task=AsyncMock(return_value=None),
    )
    service.task_repo = task_repo
    service.brand_kit_repo = SimpleNamespace(get_kit=AsyncMock())
    service.source_repo = SimpleNamespace(
        create_source=AsyncMock(return_value="src-1")
    )
    service.video_service = SimpleNamespace(
        determine_source_type=lambda url: "youtube"
    )
    service.config = SimpleNamespace(monetization_enabled=False)
    return service, task_repo


async def _create_task(service, **overrides):
    kwargs = {
        "user_id": "user-1",
        "url": "https://www.youtube.com/watch?v=PSNZuuqRff8",
        "title": "Week in the Life of a Millionaire in Los Angeles",
        "source_duration_seconds": 2365,
    }
    kwargs.update(overrides)
    return await service.create_task_with_source(**kwargs)


async def test_create_task_stamps_creator_name_on_task_metadata():
    service, task_repo = _stubbed_task_service()
    task_id = await _create_task(service, creator_name="TJR")
    assert task_id == "task-1"
    task_repo.save_source_metadata.assert_awaited_once()
    args = task_repo.save_source_metadata.await_args.args
    assert args[1] == "task-1"
    assert args[2] == {"creator_name": "TJR"}


async def test_create_task_cleans_the_creator_name_before_storing():
    service, task_repo = _stubbed_task_service()
    await _create_task(service, creator_name="  TJR   Clips ")
    args = task_repo.save_source_metadata.await_args.args
    assert args[2] == {"creator_name": "TJR Clips"}


async def test_create_task_without_a_creator_stamps_nothing():
    service, task_repo = _stubbed_task_service()
    await _create_task(service)
    task_repo.save_source_metadata.assert_not_awaited()


async def test_create_task_with_unusable_creator_stamps_nothing():
    service, task_repo = _stubbed_task_service()
    await _create_task(service, creator_name="I")
    task_repo.save_source_metadata.assert_not_awaited()


# --- read: the real create_clip path ------------------------------------------


class _ClipResult:
    def mappings(self):
        return self

    def first(self):
        return None

    def scalar(self):
        return "clip-1"


class _MetaResult:
    def __init__(self, metadata):
        self._metadata = metadata

    def fetchone(self):
        if self._metadata is _NO_TASK:
            return None
        return (self._metadata,)


class _FakeNested:
    async def __aenter__(self):
        return None

    async def __aexit__(self, *exc):
        return False


_NO_TASK = object()


class _ClipDB:
    """Answers create_clip's statements; the tasks lookup returns the
    configured source_metadata, like a task row stamped at creation."""

    def __init__(self, metadata):
        self._metadata = metadata
        self.insert_params = []

    async def execute(self, stmt, params=None):
        if "source_metadata" in str(stmt):
            return _MetaResult(self._metadata)
        if params and "hook_title" in params:
            self.insert_params.append(params)
        return _ClipResult()

    def begin_nested(self):
        return _FakeNested()


class _FailingMetaDB(_ClipDB):
    async def execute(self, stmt, params=None):
        if "source_metadata" in str(stmt):
            raise RuntimeError("metadata store unavailable")
        return await super().execute(stmt, params)


async def _create_with_title(monkeypatch, db, hook_title, **kwargs):
    from src.repositories import clip_repository

    monkeypatch.setattr(
        clip_repository, "persist_clip", AsyncMock(side_effect=lambda db, p, t: p)
    )
    clip_id = await clip_repository.ClipRepository.create_clip(
        db,
        task_id="task-1",
        filename="clip.mp4",
        file_path="/tmp/clip.mp4",
        start_time="00:00",
        end_time="00:30",
        duration=30,
        text="he tried the spiciest ramen in the city",
        relevance_score=0.9,
        reasoning="test",
        clip_order=1,
        hook_title=hook_title,
        **kwargs,
    )
    assert clip_id == "clip-1"
    assert db.insert_params, "create_clip never inserted"
    return db.insert_params[0]["hook_title"]


async def test_create_clip_attributes_titles_to_the_stored_creator(monkeypatch):
    db = _ClipDB({"creator_name": "TJR"})
    stored = await _create_with_title(
        monkeypatch, db, "I Try the World's Spiciest Ramen"
    )
    assert stored == "TJR Tries the World's Spiciest Ramen"

    db = _ClipDB({"creator_name": "TJR"})
    stored = await _create_with_title(monkeypatch, db, "My Morning Routine")
    assert stored == "TJR's Morning Routine"


async def test_create_clip_reads_json_string_metadata(monkeypatch):
    db = _ClipDB(json.dumps({"creator_name": "TJR"}))
    stored = await _create_with_title(
        monkeypatch, db, "I Met a Millionaire in LA"
    )
    assert stored == "TJR Met a Millionaire in LA"


async def test_create_clip_unknown_creator_keeps_neutral_rewrite(monkeypatch):
    for metadata in ({}, None, _NO_TASK):
        db = _ClipDB(metadata)
        stored = await _create_with_title(
            monkeypatch, db, "I Bought the Most Expensive Sandwich"
        )
        assert stored == "Bought the Most Expensive Sandwich"
        assert not contains_first_person(stored)


async def test_create_clip_clean_titles_byte_identical_with_creator(monkeypatch):
    db = _ClipDB({"creator_name": "TJR"})
    stored = await _create_with_title(
        monkeypatch, db, "$400 Wagyu Sandwich With Real Gold"
    )
    assert stored == "$400 Wagyu Sandwich With Real Gold"

    db = _ClipDB({"creator_name": "TJR"})
    stored = await _create_with_title(
        monkeypatch, db, "Walked 25 Mins for a $20k Gym Tour"
    )
    assert stored == "Walked 25 Mins for a $20k Gym Tour"


async def test_create_clip_explicit_creator_wins_over_stored(monkeypatch):
    db = _ClipDB({"creator_name": "TJR"})
    stored = await _create_with_title(
        monkeypatch,
        db,
        "I Spent 24 Hours in a Haunted House",
        creator_name="MrBeast",
    )
    assert stored == "MrBeast Spent 24 Hours in a Haunted House"


async def test_create_clip_metadata_failure_falls_back_to_neutral(monkeypatch):
    db = _FailingMetaDB({"creator_name": "TJR"})
    stored = await _create_with_title(
        monkeypatch, db, "I Bought the Most Expensive Sandwich"
    )
    assert stored == "Bought the Most Expensive Sandwich"


# --- analysis seam: sanitize_hook_title with a creator ------------------------


def test_sanitize_attributes_first_person_titles():
    from src.ai import sanitize_hook_title

    assert (
        sanitize_hook_title(
            "I Try the World's Spiciest Ramen",
            creator_name="TJR",
            fallback_text="he tried the spiciest ramen",
        )
        == "TJR Tries the World's Spiciest Ramen"
    )
    assert (
        sanitize_hook_title(
            "$400 Wagyu Sandwich With Real Gold",
            creator_name="TJR",
            fallback_text="wagyu sandwich with real gold",
        )
        == "$400 Wagyu Sandwich With Real Gold"
    )
