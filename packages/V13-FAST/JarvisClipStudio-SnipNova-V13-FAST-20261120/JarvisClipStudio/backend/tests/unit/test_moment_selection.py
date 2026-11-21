"""The shared moment-pick path and the parked-task auto-pick sweep.

Two layers, both synthetic (repos, guards, billing, queue and redis
faked in the task_service module namespace):

1. TaskService.apply_moment_selection — the one pick path behind the
   select-moments endpoint AND the sweep: the pick is written as the
   checkpoint's final plan before the job is queued, the status flip
   is guarded on the task still waiting, and an automatic pick is
   recorded as automatic everywhere it leaves a mark.

2. TaskService.auto_pick_stale_selections — the rescue sweep for
   Find-Moments tasks whose pick never came: after the configured
   timeout it continues the task with the top-ranked moments; young
   tasks, other modes and lost races are all left exactly as found.
"""

from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from src.auto_director.creation_modes import (
    AUTOPICK_TIMEOUT_ENV,
    auto_pick_note,
)
from src.config import Config
from src.repositories.task_run_guard import TaskRunBusy
from src.services import task_service as task_service_module
from src.services.billing_service import BillingLimitExceeded
from src.services.task_service import (
    MomentSelectionNotWaitingError,
    MomentSelectionSlateLostError,
    TaskService,
)


def _candidate(text, total=None, relevance=None):
    candidate = {"start_time": "00:10", "end_time": "00:40", "text": text}
    if total is not None:
        candidate["virality"] = {"total_score": total}
    if relevance is not None:
        candidate["relevance_score"] = relevance
    return candidate


CANDIDATES = [
    _candidate("First", total=90, relevance=0.9),
    _candidate("Second", total=80, relevance=0.8),
    _candidate("Third", total=70, relevance=0.7),
]

# Slate order is NOT rank order: the sweep must pick by score.
UNSORTED_CANDIDATES = [
    _candidate("Weak", total=10),
    _candidate("Best", total=95),
    _candidate("Middle", total=40),
]


def _task(**overrides):
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
        "target_clip_count": 2,
        "updated_at": datetime.now(timezone.utc) - timedelta(minutes=30),
    }
    task.update(overrides)
    return task


def build_service(
    monkeypatch,
    *,
    task=None,
    checkpoint=None,
    metadata=None,
    rows=None,
    billing_error=None,
):
    """A TaskService whose every collaborator is faked and recorded."""
    events = []
    task = _task() if task is None else task
    checkpoint = (
        {"candidates": CANDIDATES, "selection_target_count": 2}
        if checkpoint is None
        else checkpoint
    )
    metadata = {"clip_length": "auto"} if metadata is None else metadata

    service = TaskService(db=AsyncMock(), config=Config())

    async def _save_checkpoint(_db, _task_id, _key, patch):
        events.append(("plan", patch))

    async def _enqueue(*_args, **_kwargs):
        events.append(("queue", None))
        return "job-1"

    service.task_repo = SimpleNamespace(
        get_task_by_id=AsyncMock(return_value=task),
        load_source_metadata=AsyncMock(return_value=dict(metadata)),
        save_source_metadata=AsyncMock(),
        update_task_status=AsyncMock(return_value=True),
        list_tasks_by_statuses=AsyncMock(return_value=rows or []),
    )
    service.checkpoint_repo = SimpleNamespace(
        get=AsyncMock(return_value=checkpoint),
        save=AsyncMock(side_effect=_save_checkpoint),
    )

    @asynccontextmanager
    async def run_guard(_db, _task_id):
        yield

    @asynccontextmanager
    async def allowance_guard(_db, _user_id, enabled=True):
        yield

    assert_can_resume = AsyncMock(return_value={})
    if billing_error is not None:
        assert_can_resume = AsyncMock(side_effect=billing_error)

    monkeypatch.setattr(task_service_module, "task_run_guard", run_guard)
    monkeypatch.setattr(
        task_service_module, "generation_allowance_guard", allowance_guard
    )
    monkeypatch.setattr(
        task_service_module,
        "BillingService",
        lambda _db: SimpleNamespace(
            assert_can_resume_task=assert_can_resume
        ),
    )
    monkeypatch.setattr(
        task_service_module,
        "JobQueue",
        SimpleNamespace(enqueue_processing_job=AsyncMock(side_effect=_enqueue)),
    )
    monkeypatch.setattr(
        task_service_module, "_clear_task_cancel_flag", AsyncMock()
    )
    monkeypatch.setattr(
        task_service_module, "_mirror_task_source_metadata", AsyncMock()
    )
    return service, events


def _plan_patch(events):
    plans = [payload for kind, payload in events if kind == "plan"]
    assert len(plans) == 1
    return plans[0]


def _status_targets(service):
    return [
        call.args[2]
        for call in service.task_repo.update_task_status.await_args_list
    ]


# ---------------------------------------------------------------------------
# apply_moment_selection — the shared pick path
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_user_pick_writes_the_plan_then_queues(monkeypatch):
    service, events = build_service(monkeypatch)

    result = await service.apply_moment_selection("task-1", [2, 0])

    assert result == {"job_id": "job-1", "selected_count": 2}
    # The pick is the final plan, in slate order (indexes were [2, 0]).
    assert _plan_patch(events) == {
        "segments": [CANDIDATES[0], CANDIDATES[2]],
        "selection_source": "user",
    }
    # Plan first, queue second.
    assert [kind for kind, _ in events] == ["plan", "queue"]
    # The flip to queued is guarded on the task still waiting.
    flip = service.task_repo.update_task_status.await_args_list[0]
    assert flip.args[2] == "queued"
    assert flip.kwargs["expected_statuses"] == ["awaiting_selection"]
    assert flip.kwargs["progress_message"] == "Selected moments queued"
    # A user pick leaves no auto-pick marker in the task metadata.
    metadata_patch = (
        service.task_repo.save_source_metadata.await_args.args[2]
    )
    assert "processing_job_id" in metadata_patch
    assert "selection_source" not in metadata_patch


@pytest.mark.asyncio
async def test_auto_pick_records_the_marker_everywhere(monkeypatch):
    service, events = build_service(monkeypatch)

    result = await service.apply_moment_selection(
        "task-1", [0, 1], automatic=True, autopick_timeout_minutes=15
    )

    assert result["selected_count"] == 2
    note = auto_pick_note(15)
    patch = _plan_patch(events)
    assert patch["segments"] == CANDIDATES[:2]
    assert patch["selection_source"] == "auto_timeout"
    assert patch["selection_note"] == note
    # The user sees the honest note as the task's progress message.
    flip = service.task_repo.update_task_status.await_args_list[0]
    assert flip.kwargs["progress_message"] == note
    # And the task's stored metadata carries the marker durably.
    metadata_patch = (
        service.task_repo.save_source_metadata.await_args.args[2]
    )
    assert metadata_patch["selection_source"] == "auto_timeout"
    assert metadata_patch["selection_note"] == note
    assert "selection_auto_picked_at" in metadata_patch


@pytest.mark.asyncio
async def test_pick_rejects_a_task_that_is_not_waiting(monkeypatch):
    service, events = build_service(
        monkeypatch, task=_task(status="completed")
    )

    with pytest.raises(MomentSelectionNotWaitingError):
        await service.apply_moment_selection("task-1", [0])

    assert events == []


@pytest.mark.asyncio
async def test_pick_reports_a_lost_slate(monkeypatch):
    service, events = build_service(monkeypatch, checkpoint={})

    with pytest.raises(MomentSelectionSlateLostError):
        await service.apply_moment_selection("task-1", [0])

    assert events == []


@pytest.mark.asyncio
async def test_pick_validates_indexes_against_the_slate(monkeypatch):
    service, events = build_service(monkeypatch)

    with pytest.raises(ValueError):
        await service.apply_moment_selection("task-1", [9])

    assert events == []


@pytest.mark.asyncio
async def test_enqueue_failure_restores_the_park(monkeypatch):
    service, events = build_service(monkeypatch)

    async def _boom(*_args, **_kwargs):
        raise RuntimeError("queue down")

    monkeypatch.setattr(
        task_service_module.JobQueue,
        "enqueue_processing_job",
        AsyncMock(side_effect=_boom),
    )

    with pytest.raises(RuntimeError):
        await service.apply_moment_selection("task-1", [0])

    # Parked again, with the honest retry message — never half-queued.
    assert _status_targets(service) == ["queued", "awaiting_selection"]
    restore = service.task_repo.update_task_status.await_args_list[-1]
    assert restore.kwargs["expected_statuses"] == [
        "queued",
        "awaiting_selection",
    ]


@pytest.mark.asyncio
async def test_a_lost_flip_race_changes_nothing(monkeypatch):
    service, events = build_service(monkeypatch)
    service.task_repo.update_task_status = AsyncMock(
        side_effect=[False, True]
    )

    with pytest.raises(MomentSelectionNotWaitingError):
        await service.apply_moment_selection("task-1", [0])

    # The plan was written, but nothing was ever queued.
    assert [kind for kind, _ in events] == ["plan"]


# ---------------------------------------------------------------------------
# auto_pick_stale_selections — the rescue sweep
# ---------------------------------------------------------------------------


def _row(task):
    return {
        "id": task["id"],
        "user_id": task["user_id"],
        "status": task["status"],
        "created_at": task["updated_at"],
        "updated_at": task["updated_at"],
    }


def _stale_metadata(minutes_ago=30):
    parked_at = datetime.now(timezone.utc) - timedelta(minutes=minutes_ago)
    return {
        "clip_length": "auto",
        "selection_parked_at": parked_at.isoformat(),
    }


@pytest.mark.asyncio
async def test_sweep_auto_picks_the_top_ranked_after_the_timeout(
    monkeypatch,
):
    task = _task()
    service, events = build_service(
        monkeypatch,
        task=task,
        checkpoint={
            "candidates": UNSORTED_CANDIDATES,
            "selection_target_count": 2,
        },
        metadata=_stale_metadata(30),
        rows=[_row(task)],
    )

    counts = await service.auto_pick_stale_selections()

    assert counts == {"checked": 1, "auto_picked": 1, "failed": 0}
    # By score, not slate order: Best (95) and Middle (40), never Weak.
    patch = _plan_patch(events)
    assert patch["segments"] == [
        UNSORTED_CANDIDATES[1],
        UNSORTED_CANDIDATES[2],
    ]
    assert patch["selection_source"] == "auto_timeout"
    assert [kind for kind, _ in events] == ["plan", "queue"]


@pytest.mark.asyncio
async def test_sweep_leaves_a_young_parked_task_alone(monkeypatch):
    task = _task()
    service, events = build_service(
        monkeypatch,
        task=task,
        metadata=_stale_metadata(5),
        rows=[_row(task)],
    )

    counts = await service.auto_pick_stale_selections()

    assert counts == {"checked": 1, "auto_picked": 0, "failed": 0}
    assert events == []


@pytest.mark.asyncio
async def test_sweep_falls_back_to_updated_at_without_a_stamp(
    monkeypatch,
):
    """Tasks parked before the park stamp existed still get rescued."""
    task = _task()  # updated_at is already 30 minutes ago.
    service, events = build_service(
        monkeypatch, task=task, metadata={"clip_length": "auto"}, rows=[_row(task)]
    )

    counts = await service.auto_pick_stale_selections()

    assert counts["auto_picked"] == 1
    assert [kind for kind, _ in events] == ["plan", "queue"]


@pytest.mark.asyncio
async def test_sweep_respects_the_configured_timeout(monkeypatch):
    monkeypatch.setenv(AUTOPICK_TIMEOUT_ENV, "30")
    task = _task()
    service, events = build_service(
        monkeypatch,
        task=task,
        metadata=_stale_metadata(20),
        rows=[_row(task)],
    )

    counts = await service.auto_pick_stale_selections()

    assert counts["auto_picked"] == 0
    assert events == []


@pytest.mark.asyncio
async def test_sweep_target_falls_back_to_half_the_slate(monkeypatch):
    candidates = [_candidate(f"C{i}", total=100 - i) for i in range(5)]
    task = _task(target_clip_count=None)
    service, events = build_service(
        monkeypatch,
        task=task,
        checkpoint={"candidates": candidates},
        metadata=_stale_metadata(30),
        rows=[_row(task)],
    )

    counts = await service.auto_pick_stale_selections()

    assert counts["auto_picked"] == 1
    # No stored target, no explicit count: half the reserve slate,
    # rounded up — the reserve is built at twice the render target.
    assert len(_plan_patch(events)["segments"]) == 3


@pytest.mark.asyncio
async def test_a_user_pick_racing_the_sweep_wins(monkeypatch):
    task = _task()
    service, events = build_service(
        monkeypatch,
        task=task,
        metadata=_stale_metadata(30),
        rows=[_row(task)],
    )
    # The sweep sees the parked task; by the time the pick path
    # re-reads it inside the run guard, the user's pick has landed.
    service.task_repo.get_task_by_id = AsyncMock(
        side_effect=[task, _task(status="queued")]
    )

    counts = await service.auto_pick_stale_selections()

    assert counts == {"checked": 1, "auto_picked": 0, "failed": 0}
    assert events == []


@pytest.mark.asyncio
async def test_a_pick_in_flight_makes_the_sweep_back_off(monkeypatch):
    task = _task()
    service, events = build_service(
        monkeypatch,
        task=task,
        metadata=_stale_metadata(30),
        rows=[_row(task)],
    )

    @asynccontextmanager
    async def busy_guard(_db, _task_id):
        raise TaskRunBusy("pick in flight")
        yield

    monkeypatch.setattr(task_service_module, "task_run_guard", busy_guard)

    counts = await service.auto_pick_stale_selections()

    assert counts == {"checked": 1, "auto_picked": 0, "failed": 0}
    assert events == []


@pytest.mark.asyncio
async def test_a_second_sweep_run_is_a_no_op(monkeypatch):
    task = _task()
    service, events = build_service(
        monkeypatch,
        task=task,
        metadata=_stale_metadata(30),
        rows=[_row(task)],
    )

    first = await service.auto_pick_stale_selections()
    assert first["auto_picked"] == 1

    # The continued task left the swept status, so the next run's
    # listing no longer contains it; a stale listing row is likewise
    # ignored because the fresh read no longer shows it parked.
    service.task_repo.list_tasks_by_statuses = AsyncMock(return_value=[])
    second = await service.auto_pick_stale_selections()
    assert second == {"checked": 0, "auto_picked": 0, "failed": 0}

    service.task_repo.list_tasks_by_statuses = AsyncMock(
        return_value=[_row(task)]
    )
    service.task_repo.get_task_by_id = AsyncMock(
        return_value=_task(status="queued")
    )
    third = await service.auto_pick_stale_selections()
    assert third["auto_picked"] == 0
    # Still exactly one plan + one queue across all three runs.
    assert [kind for kind, _ in events] == ["plan", "queue"]


@pytest.mark.parametrize("mode", ["full_auto", "manual_cutter_ai"])
@pytest.mark.asyncio
async def test_sweep_never_touches_non_review_modes(monkeypatch, mode):
    task = _task(creation_mode=mode)
    service, events = build_service(
        monkeypatch,
        task=task,
        metadata=_stale_metadata(30),
        rows=[_row(task)],
    )

    counts = await service.auto_pick_stale_selections()

    assert counts["auto_picked"] == 0
    assert events == []


@pytest.mark.asyncio
async def test_a_failed_auto_pick_leaves_the_task_parked(monkeypatch):
    task = _task()
    service, events = build_service(
        monkeypatch,
        task=task,
        metadata=_stale_metadata(30),
        rows=[_row(task)],
        billing_error=BillingLimitExceeded({"reason": "Plan usage limit reached"}),
    )

    counts = await service.auto_pick_stale_selections()

    assert counts == {"checked": 1, "auto_picked": 0, "failed": 1}
    # Nothing was planned, flipped or queued — the task stays parked
    # for the user's own pick or a later sweep.
    assert events == []
    assert _status_targets(service) == []
