"""Route tests for the Find-Moments review endpoints (tracker 1.9).

Synthetic: the service, guards and queue are faked in the routes
module namespace, following test_task_resume_billing.py. What is
proven here is the endpoint contract — the owner check, delegation
to the one pick path (TaskService.apply_moment_selection, shared
with the auto-pick sweep), the HTTP mapping of its outcomes, and
the candidates read including the wait info. The pick path's own
behaviour (plan written before enqueue, guarded flip, auto-pick
marker) is proven in test_moment_selection.py.
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

from src.api.routes import tasks
from src.services.task_service import (
    MomentSelectionNotWaitingError,
    MomentSelectionSlateLostError,
    MomentSelectionSourceMissingError,
)


CANDIDATES = [
    {"start_time": "00:10", "end_time": "00:40", "text": "First"},
    {"start_time": "01:10", "end_time": "01:40", "text": "Second"},
    {"start_time": "02:10", "end_time": "02:40", "text": "Third"},
]

WAIT_INFO = {
    "selection_parked_at": "2026-11-10T10:00:00+00:00",
    "auto_pick_minutes": 15,
    "auto_pick_deadline": "2026-11-10T10:15:00+00:00",
}


@pytest.fixture()
def select_context(monkeypatch):
    task = {
        "id": "task-1",
        "user_id": "user-1",
        "status": "awaiting_selection",
        "creation_mode": "moments_ai",
        "processing_mode": "fast",
        "source_url": "https://youtu.be/abcdefghijk",
        "source_type": "youtube",
        "font_family": None,
        "font_size": None,
        "font_color": None,
        "caption_template": "default",
    }
    service = SimpleNamespace(
        apply_moment_selection=AsyncMock(
            return_value={"job_id": "job-1", "selected_count": 2}
        ),
        load_task_checkpoint=AsyncMock(
            return_value=({"candidates": CANDIDATES}, "input-key")
        ),
        selection_wait_info=lambda _task, _metadata: dict(WAIT_INFO),
    )
    monkeypatch.setattr(tasks, "TaskService", lambda _db: service)
    monkeypatch.setattr(tasks, "_require_task_owner", AsyncMock(return_value=task))
    monkeypatch.setattr(tasks, "_require_task_reader", AsyncMock(return_value=task))
    monkeypatch.setattr(
        tasks,
        "_load_task_source_metadata",
        AsyncMock(return_value={"clip_length": "auto"}),
    )
    monkeypatch.setattr(
        tasks,
        "_read_json_object",
        AsyncMock(return_value={"segment_indexes": [2, 0]}),
    )
    return task, service


@pytest.mark.asyncio
async def test_select_moments_delegates_the_pick_to_the_shared_path(
    select_context,
):
    _task, service = select_context

    result = await tasks.select_moments("task-1", SimpleNamespace(), db=None)

    assert result["selected_count"] == 2
    assert result["job_id"] == "job-1"
    # The endpoint hands the raw requested indexes to the one pick
    # path; validation and slate-order rendering happen there.
    service.apply_moment_selection.assert_awaited_once_with("task-1", [2, 0])


@pytest.mark.asyncio
async def test_select_moments_rejects_a_task_that_is_not_waiting(
    select_context,
):
    _task, service = select_context
    service.apply_moment_selection = AsyncMock(
        side_effect=MomentSelectionNotWaitingError("not waiting")
    )

    with pytest.raises(HTTPException) as error:
        await tasks.select_moments("task-1", SimpleNamespace(), db=None)

    assert error.value.status_code == 400


@pytest.mark.asyncio
async def test_select_moments_rejects_an_out_of_slate_pick(select_context):
    _task, service = select_context
    service.apply_moment_selection = AsyncMock(
        side_effect=ValueError("segment index 9 is outside the candidate slate")
    )

    with pytest.raises(HTTPException) as error:
        await tasks.select_moments("task-1", SimpleNamespace(), db=None)

    assert error.value.status_code == 422


@pytest.mark.asyncio
async def test_select_moments_reports_a_lost_slate(select_context):
    _task, service = select_context
    service.apply_moment_selection = AsyncMock(
        side_effect=MomentSelectionSlateLostError("gone")
    )

    with pytest.raises(HTTPException) as error:
        await tasks.select_moments("task-1", SimpleNamespace(), db=None)

    assert error.value.status_code == 409


@pytest.mark.asyncio
async def test_select_moments_reports_a_missing_source(select_context):
    _task, service = select_context
    service.apply_moment_selection = AsyncMock(
        side_effect=MomentSelectionSourceMissingError("missing")
    )

    with pytest.raises(HTTPException) as error:
        await tasks.select_moments("task-1", SimpleNamespace(), db=None)

    assert error.value.status_code == 400


@pytest.mark.asyncio
async def test_candidates_endpoint_returns_the_checkpoint_slate(
    select_context,
):
    result = await tasks.get_task_candidates(
        "task-1", SimpleNamespace(), db=None
    )

    assert result["status"] == "awaiting_selection"
    assert result["creation_mode"] == "moments_ai"
    assert result["candidates"] == CANDIDATES
    # A parked task's wait is bounded, and the read says so: when it
    # started and when the auto-pick sweep will continue it.
    assert result["selection_parked_at"] == WAIT_INFO["selection_parked_at"]
    assert result["auto_pick_minutes"] == 15
    assert result["auto_pick_deadline"] == WAIT_INFO["auto_pick_deadline"]


@pytest.mark.asyncio
async def test_candidates_endpoint_omits_wait_info_when_not_parked(
    select_context,
):
    task, _service = select_context
    task["status"] = "processing"

    result = await tasks.get_task_candidates(
        "task-1", SimpleNamespace(), db=None
    )

    assert result["candidates"] == CANDIDATES
    assert "auto_pick_deadline" not in result
