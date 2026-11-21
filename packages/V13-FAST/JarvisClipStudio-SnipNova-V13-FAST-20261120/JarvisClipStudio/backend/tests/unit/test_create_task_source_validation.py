"""Create-route source-field validation (settings-integrity audit F-4).

Every sibling field validator on the create route rejects garbage with
a 422; output_format and add_subtitles used to silently substitute
"vertical" / True instead, so a malformed client got a different
aspect/subtitle choice than it asked for, with no signal. The shipped
frontend cannot send bad values (its OutputFormat type is exactly the
valid set and the subtitles control is a boolean Switch — see
frontend/src/components/home-app.tsx:70/:124/:130), so the 422 only
ever fires for genuinely malformed clients.

The wiring mirrors test_content_requirements_routes.py.
"""

from __future__ import annotations

import contextlib
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

from src.api.routes import tasks

YOUTUBE_URL = "https://www.youtube.com/watch?v=abcdefghijk"


def _request(body, queue_adapter=None):
    return SimpleNamespace(
        json=AsyncMock(return_value=body),
        headers={},
        app=SimpleNamespace(
            state=SimpleNamespace(queue_adapter=queue_adapter or _QueueAdapter())
        ),
    )


class _QueueAdapter:
    async def enqueue_processing_job(self, *args, **kwargs):
        return "job-1"


class _FakeBillingService:
    def __init__(self, db):
        pass

    async def assert_can_create_task(self, *args, **kwargs):
        return {
            "max_youtube_duration_seconds": 100000,
            "max_upload_duration_seconds": 100000,
        }


class _FakeTaskService:
    captured: dict = {}
    reused_upload_task = False

    def __init__(self, db):
        self.video_service = SimpleNamespace(
            determine_source_type=lambda url: "youtube"
        )
        self.task_repo = SimpleNamespace(update_task_status=AsyncMock())

    async def create_task_with_source(self, **kwargs):
        _FakeTaskService.captured = kwargs
        return "task-1"


@contextlib.asynccontextmanager
async def _no_guard(*args, **kwargs):
    yield


def _wire_common(monkeypatch):
    monkeypatch.setattr(tasks, "_get_user_id_from_headers", AsyncMock(return_value="user-1"))
    monkeypatch.setattr("src.source_submission.record_source_submission", AsyncMock())
    monkeypatch.setattr(tasks, "BillingService", _FakeBillingService)
    monkeypatch.setattr(tasks, "TaskService", _FakeTaskService)
    monkeypatch.setattr(tasks, "generation_allowance_guard", _no_guard)
    monkeypatch.setattr(tasks, "_save_task_source_metadata", AsyncMock())
    monkeypatch.setattr(
        tasks,
        "_preflight_youtube_source",
        AsyncMock(return_value={"duration": 600.0, "title": "Source"}),
    )
    _FakeTaskService.captured = {}


async def test_create_task_rejects_invalid_output_format_with_422(monkeypatch):
    _wire_common(monkeypatch)
    with pytest.raises(HTTPException) as error:
        await tasks.create_task(
            _request(
                {"source": {"url": YOUTUBE_URL}, "output_format": "portrait"}
            ),
            db=None,
        )
    assert error.value.status_code == 422


async def test_create_task_rejects_non_bool_add_subtitles_with_422(monkeypatch):
    _wire_common(monkeypatch)
    with pytest.raises(HTTPException) as error:
        await tasks.create_task(
            _request(
                {"source": {"url": YOUTUBE_URL}, "add_subtitles": "yes"}
            ),
            db=None,
        )
    assert error.value.status_code == 422


async def test_create_task_accepts_valid_source_fields(monkeypatch):
    _wire_common(monkeypatch)
    result = await tasks.create_task(
        _request(
            {
                "source": {"url": YOUTUBE_URL},
                "output_format": "original",
                "add_subtitles": False,
            }
        ),
        db=None,
    )
    assert result["task_id"] == "task-1"


async def test_create_task_accepts_omitted_source_fields(monkeypatch):
    _wire_common(monkeypatch)
    result = await tasks.create_task(
        _request({"source": {"url": YOUTUBE_URL}}),
        db=None,
    )
    assert result["task_id"] == "task-1"
