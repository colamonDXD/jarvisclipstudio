"""Recovery + cancel hardening.

Covers the worker wrapper (cancelled jobs are not retried/dead-lettered,
genuine failures dead-letter with a sanitised message), the arq abort
helper, the cancel endpoint (marks pending tasks too, aborts the job),
and the pipeline storing sanitised error messages.
"""

import asyncio
import json
from contextlib import nullcontext
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from arq.constants import in_progress_key_prefix, job_key_prefix

from src.api.routes import tasks as task_routes
from src.config import Config
from src.repositories.edit_transaction import TaskCancelled
from src.services import task_service as task_service_module
from src.services.task_service import TaskService
from src.workers import job_queue as job_queue_module
from src.workers import tasks as worker_tasks
from src.workers.job_queue import JobQueue


# --------------------------------------------------------------------------
# Worker wrapper: process_video_task
# --------------------------------------------------------------------------


class FakeRedis:
    def __init__(self):
        self.values = {}
        self.sets = {}
        self.published = []

    async def get(self, key):
        return self.values.get(key)

    async def set(self, key, value):
        self.values[key] = value

    async def setex(self, key, _ttl, value):
        self.values[key] = value

    async def sadd(self, key, member):
        self.sets.setdefault(key, set()).add(member)

    async def publish(self, channel, payload):
        self.published.append((channel, payload))


def _patch_worker_service(monkeypatch, process_task_impl):
    class FakeSession:
        async def __aenter__(self):
            return object()

        async def __aexit__(self, *exc_info):
            return False

    monkeypatch.setattr("src.database.AsyncSessionLocal", lambda: FakeSession())
    monkeypatch.setattr(
        "src.runtime_settings.load_runtime_settings_cache", AsyncMock()
    )

    class StubService:
        def __init__(self, _db):
            pass

        async def process_task(self, **_kwargs):
            return await process_task_impl()

    monkeypatch.setattr("src.services.task_service.TaskService", StubService)


@pytest.mark.asyncio
async def test_cancelled_job_is_not_retried_or_dead_lettered(monkeypatch):
    async def cancelled():
        raise TaskCancelled()

    _patch_worker_service(monkeypatch, cancelled)
    redis = FakeRedis()
    # job_try == max_tries: the old code dead-lettered exactly here.
    result = await worker_tasks.process_video_task(
        {"redis": redis, "job_try": 3}, "task-1", "upload://x", "upload", "user-1"
    )
    assert result == {"task_id": "task-1", "status": "cancelled"}
    assert not any(key.startswith("dead_letter:") for key in redis.values)
    assert "tasks:dead_letter" not in redis.sets
    published = [json.loads(payload) for _channel, payload in redis.published]
    assert all(entry.get("status") != "error" for entry in published)


@pytest.mark.asyncio
async def test_failed_job_dead_letters_with_sanitised_error(monkeypatch):
    async def boom():
        raise RuntimeError(
            "download exploded https://cdn.example.com/v.mp4?token=secret123"
        )

    _patch_worker_service(monkeypatch, boom)
    redis = FakeRedis()
    with pytest.raises(RuntimeError):
        await worker_tasks.process_video_task(
            {"redis": redis, "job_try": 3},
            "task-9",
            "https://youtu.be/x",
            "youtube",
            "user-1",
        )
    payload = json.loads(redis.values["dead_letter:task-9"])
    assert payload["task_id"] == "task-9"
    assert payload["tries"] == 3
    assert "secret123" not in payload["error"]
    assert "http" not in payload["error"]
    assert "download exploded" in payload["error"]
    assert redis.sets["tasks:dead_letter"] == {"task-9"}


@pytest.mark.asyncio
async def test_failed_job_before_final_try_writes_no_dead_letter(monkeypatch):
    async def boom():
        raise RuntimeError("download exploded")

    _patch_worker_service(monkeypatch, boom)
    redis = FakeRedis()
    with pytest.raises(RuntimeError):
        await worker_tasks.process_video_task(
            {"redis": redis, "job_try": 1},
            "task-9",
            "https://youtu.be/x",
            "youtube",
            "user-1",
        )
    assert not any(key.startswith("dead_letter:") for key in redis.values)


def test_worker_settings_allow_abort_jobs():
    assert worker_tasks.WorkerSettings.allow_abort_jobs is True


# --------------------------------------------------------------------------
# JobQueue.abort_job
# --------------------------------------------------------------------------


class FakeJob:
    instances = []
    behaviour = "ok"

    def __init__(self, job_id, _redis, _queue_name=None):
        self.job_id = job_id
        self.queue_name = _queue_name
        FakeJob.instances.append(self)

    async def abort(self, timeout=None):
        if FakeJob.behaviour == "timeout":
            raise asyncio.TimeoutError()
        if FakeJob.behaviour == "boom":
            raise RuntimeError("redis gone")
        return True


@pytest.fixture
def fake_job(monkeypatch):
    FakeJob.instances = []
    FakeJob.behaviour = "ok"
    monkeypatch.setattr(job_queue_module, "Job", FakeJob)
    monkeypatch.setattr(JobQueue, "get_pool", AsyncMock(return_value=object()))
    return FakeJob


@pytest.mark.asyncio
async def test_abort_job_targets_the_processing_queue(fake_job):
    assert await JobQueue.abort_job("task-1:beef") is True
    assert fake_job.instances[0].job_id == "task-1:beef"
    assert fake_job.instances[0].queue_name == "supoclip_tasks"


@pytest.mark.asyncio
async def test_abort_job_timeout_still_means_stop_requested(fake_job):
    fake_job.behaviour = "timeout"
    assert await JobQueue.abort_job("task-1:beef") is True


@pytest.mark.asyncio
async def test_abort_job_failure_is_reported_not_raised(fake_job):
    fake_job.behaviour = "boom"
    assert await JobQueue.abort_job("task-1:beef") is False


# --------------------------------------------------------------------------
# Cancel endpoint
# --------------------------------------------------------------------------


class FakeCancelRedis:
    instances = []

    def __init__(self, **_kwargs):
        self.flags = {}
        FakeCancelRedis.instances.append(self)

    async def setex(self, key, _ttl, value):
        self.flags[key] = value

    async def aclose(self):
        return None


def _patch_cancel_route(monkeypatch, task):
    FakeCancelRedis.instances = []
    service = SimpleNamespace(
        task_repo=SimpleNamespace(update_task_status=AsyncMock(return_value=True))
    )
    monkeypatch.setattr(task_routes, "TaskService", lambda _db: service)
    monkeypatch.setattr(
        task_routes, "_require_task_owner", AsyncMock(return_value=task)
    )
    monkeypatch.setattr(
        task_routes,
        "_load_task_source_metadata",
        AsyncMock(return_value={"processing_job_id": "task-1:beef"}),
    )
    abort = AsyncMock(return_value=True)
    monkeypatch.setattr(JobQueue, "abort_job", abort)
    monkeypatch.setattr(task_routes.redis, "Redis", FakeCancelRedis)
    return service, abort


@pytest.mark.asyncio
async def test_cancel_marks_task_sets_flag_and_aborts_job(monkeypatch):
    service, abort = _patch_cancel_route(
        monkeypatch, {"id": "task-1", "status": "processing"}
    )
    response = await task_routes.cancel_task(
        "task-1", SimpleNamespace(), AsyncMock()
    )
    assert response == {"message": "Task cancellation requested"}
    call = service.task_repo.update_task_status.await_args
    assert call.args[2] == "cancelled"
    assert call.kwargs["expected_statuses"] == ["queued", "processing", "pending"]
    abort.assert_awaited_once_with("task-1:beef")
    assert FakeCancelRedis.instances[0].flags["task_cancel:task-1"] == "1"


@pytest.mark.asyncio
async def test_cancel_terminal_task_touches_nothing(monkeypatch):
    service, abort = _patch_cancel_route(
        monkeypatch, {"id": "task-1", "status": "completed"}
    )
    response = await task_routes.cancel_task(
        "task-1", SimpleNamespace(), AsyncMock()
    )
    assert "already in terminal state" in response["message"]
    service.task_repo.update_task_status.assert_not_awaited()
    abort.assert_not_awaited()


@pytest.mark.asyncio
async def test_cancel_survives_abort_failure(monkeypatch):
    service, abort = _patch_cancel_route(
        monkeypatch, {"id": "task-1", "status": "queued"}
    )
    abort.side_effect = RuntimeError("redis gone")
    response = await task_routes.cancel_task(
        "task-1", SimpleNamespace(), AsyncMock()
    )
    assert response == {"message": "Task cancellation requested"}
    service.task_repo.update_task_status.assert_awaited_once()


# --------------------------------------------------------------------------
# Pipeline error marking (TaskService.process_task)
# --------------------------------------------------------------------------


def _failing_service(exc) -> TaskService:
    service = TaskService(db=AsyncMock(), config=Config())
    service._load_performance_context = AsyncMock(return_value=None)
    service._processing_transaction = lambda _task_id: nullcontext()
    service._task_run_guard = lambda _task_id: nullcontext()
    service.task_repo.get_task_by_id = AsyncMock(return_value={"status": "queued"})
    service.cache_repo.get_cache = AsyncMock(return_value=None)
    service.cache_repo.upsert_cache = AsyncMock()
    service.checkpoint_repo.get = AsyncMock(return_value={})
    service.checkpoint_repo.save = AsyncMock()
    service.task_repo.update_task_runtime_metadata = AsyncMock()
    service.task_repo.update_task_status = AsyncMock(return_value=True)
    service.task_repo.update_task_clips = AsyncMock()
    service.video_service.process_video_complete = AsyncMock(side_effect=exc)
    return service


@pytest.mark.asyncio
async def test_process_task_stores_sanitised_error_message():
    service = _failing_service(
        RuntimeError(
            "download failed for https://cdn.example.com/v.mp4?token=secret123 "
            "into /tmp/supoclip/downloads/v.mp4"
        )
    )
    progress = AsyncMock()
    with pytest.raises(RuntimeError):
        await service.process_task(
            task_id="task-1",
            url="https://www.youtube.com/watch?v=demo",
            source_type="youtube",
            progress_callback=progress,
        )
    call = service.task_repo.update_task_status.await_args
    stored = call.kwargs["progress_message"]
    assert call.args[2] == "error"
    assert "secret123" not in stored
    assert "http" not in stored
    assert "/tmp/supoclip" not in stored
    assert "download failed" in stored
    assert (
        service.task_repo.update_task_runtime_metadata.await_args.kwargs[
            "error_code"
        ]
        == "download_error"
    )
    assert progress.await_args.args == (0, stored, "error")


# --------------------------------------------------------------------------
# Startup / sweep recovery (TaskService.recover_stale_tasks)
# --------------------------------------------------------------------------


class FakeTaskRepo:
    """Stateful stand-in for TaskRepository's lifecycle surface."""

    def __init__(self, tasks, job_ids=None):
        self.tasks = {task["id"]: dict(task) for task in tasks}
        self.job_ids = job_ids or {}
        self.updates = []

    async def list_tasks_by_statuses(self, _db, statuses, limit=500):
        return [
            dict(task)
            for task in self.tasks.values()
            if task["status"] in statuses
        ][:limit]

    async def load_source_metadata(self, _db, task_id):
        job_id = self.job_ids.get(task_id)
        return {"processing_job_id": job_id} if job_id else {}

    async def get_task_by_id(self, _db, task_id):
        task = self.tasks.get(task_id)
        return dict(task) if task else None

    async def update_task_status(
        self,
        _db,
        task_id,
        status,
        progress=None,
        progress_message=None,
        expected_statuses=None,
    ):
        current = self.tasks[task_id]
        if expected_statuses is not None and current["status"] not in expected_statuses:
            return False
        current["status"] = status
        current["progress"] = progress
        current["progress_message"] = progress_message
        self.updates.append((task_id, status, progress_message))
        return True


class FakeArqPool:
    """exists() over arq job keys, given the set of live job ids."""

    def __init__(self, live_job_ids=()):
        self.live = set(live_job_ids)

    async def exists(self, *keys):
        found = 0
        for key in keys:
            for prefix in (job_key_prefix, in_progress_key_prefix):
                if key.startswith(prefix) and key[len(prefix):] in self.live:
                    found += 1
        return found


def _task(task_id, status, age_seconds):
    stamp = datetime.now(timezone.utc) - timedelta(seconds=age_seconds)
    return {
        "id": task_id,
        "user_id": "user-1",
        "status": status,
        "created_at": stamp,
        "updated_at": stamp,
    }


def _recovery_service(monkeypatch, tasks, job_ids=None, live_job_ids=()):
    config = Config()
    config.queued_task_timeout_seconds = 100
    config.processing_task_timeout_seconds = 1000
    service = TaskService(db=AsyncMock(), config=config)
    service.task_repo = FakeTaskRepo(tasks, job_ids)
    monkeypatch.setattr(
        task_service_module.JobQueue,
        "get_pool",
        AsyncMock(return_value=FakeArqPool(live_job_ids)),
    )
    return service


@pytest.mark.asyncio
async def test_startup_recovers_processing_task_whose_job_is_gone(monkeypatch):
    service = _recovery_service(
        monkeypatch,
        [_task("task-1", "processing", age_seconds=10)],
        job_ids={"task-1": "task-1:beef"},
        live_job_ids=(),
    )
    counts = await service.recover_stale_tasks(worker_startup=True)
    assert counts == {"checked": 1, "failed": 1}
    task = service.task_repo.tasks["task-1"]
    assert task["status"] == "error"
    assert task["progress_message"] == "Worker restarted while this task was running."


@pytest.mark.asyncio
async def test_startup_leaves_processing_task_with_live_job_alone(monkeypatch):
    service = _recovery_service(
        monkeypatch,
        [_task("task-1", "processing", age_seconds=10)],
        job_ids={"task-1": "task-1:beef"},
        live_job_ids={"task-1:beef"},
    )
    counts = await service.recover_stale_tasks(worker_startup=True)
    assert counts == {"checked": 1, "failed": 0}
    assert service.task_repo.tasks["task-1"]["status"] == "processing"


@pytest.mark.asyncio
async def test_fresh_processing_task_survives_non_startup_sweep(monkeypatch):
    service = _recovery_service(
        monkeypatch,
        [_task("task-1", "processing", age_seconds=10)],
        job_ids={"task-1": "task-1:beef"},
        live_job_ids=(),
    )
    counts = await service.recover_stale_tasks(worker_startup=False)
    assert counts == {"checked": 1, "failed": 0}
    assert service.task_repo.tasks["task-1"]["status"] == "processing"


@pytest.mark.asyncio
async def test_stale_processing_task_fails_with_stalled_message(monkeypatch):
    service = _recovery_service(
        monkeypatch,
        [_task("task-1", "processing", age_seconds=5000)],
        job_ids={"task-1": "task-1:beef"},
        live_job_ids={"task-1:beef"},
    )
    counts = await service.recover_stale_tasks(worker_startup=False)
    assert counts == {"checked": 1, "failed": 1}
    task = service.task_repo.tasks["task-1"]
    assert task["status"] == "error"
    assert "stalled during processing" in task["progress_message"]


@pytest.mark.asyncio
async def test_legacy_task_without_job_id_keeps_age_rule_at_startup(monkeypatch):
    service = _recovery_service(
        monkeypatch,
        [
            _task("fresh", "processing", age_seconds=10),
            _task("stale", "processing", age_seconds=5000),
        ],
        job_ids={},
        live_job_ids=(),
    )
    counts = await service.recover_stale_tasks(worker_startup=True)
    assert counts == {"checked": 2, "failed": 1}
    assert service.task_repo.tasks["fresh"]["status"] == "processing"
    stale = service.task_repo.tasks["stale"]
    assert stale["status"] == "error"
    assert "stalled during processing" in stale["progress_message"]


@pytest.mark.asyncio
async def test_queued_sweep_only_fails_stale_tasks_with_missing_jobs(monkeypatch):
    service = _recovery_service(
        monkeypatch,
        [
            _task("lost", "queued", age_seconds=500),
            _task("waiting", "queued", age_seconds=500),
            _task("fresh", "queued", age_seconds=10),
        ],
        job_ids={"lost": "lost:beef", "waiting": "waiting:beef", "fresh": "fresh:beef"},
        live_job_ids={"waiting:beef"},
    )
    counts = await service.recover_stale_tasks(worker_startup=True)
    assert counts == {"checked": 3, "failed": 1}
    lost = service.task_repo.tasks["lost"]
    assert lost["status"] == "error"
    assert "queued job is no longer available" in lost["progress_message"]
    assert service.task_repo.tasks["waiting"]["status"] == "queued"
    assert service.task_repo.tasks["fresh"]["status"] == "queued"


@pytest.mark.asyncio
async def test_recovery_is_idempotent_and_ignores_terminal_tasks(monkeypatch):
    service = _recovery_service(
        monkeypatch,
        [
            _task("task-1", "processing", age_seconds=10),
            _task("done", "completed", age_seconds=99999),
            _task("bad", "error", age_seconds=99999),
        ],
        job_ids={"task-1": "task-1:beef"},
        live_job_ids=(),
    )
    first = await service.recover_stale_tasks(worker_startup=True)
    assert first == {"checked": 1, "failed": 1}
    updates_after_first = list(service.task_repo.updates)
    second = await service.recover_stale_tasks(worker_startup=True)
    assert second == {"checked": 0, "failed": 0}
    assert service.task_repo.updates == updates_after_first
    assert service.task_repo.tasks["done"]["status"] == "completed"
    assert service.task_repo.tasks["bad"]["status"] == "error"


@pytest.mark.asyncio
async def test_lazy_sweep_on_task_read_still_fails_stale_processing(monkeypatch):
    service = _recovery_service(
        monkeypatch,
        [_task("task-1", "processing", age_seconds=5000)],
        job_ids={"task-1": "task-1:beef"},
        live_job_ids={"task-1:beef"},
    )
    service.clip_repo.get_clips_by_task = AsyncMock(return_value=[])
    service._load_task_source_settings = AsyncMock(return_value={})
    task = await service.get_task_with_clips("task-1")
    assert task["status"] == "error"
    assert "stalled during processing" in task["progress_message"]


@pytest.mark.asyncio
async def test_lazy_sweep_leaves_fresh_processing_task_alone(monkeypatch):
    service = _recovery_service(
        monkeypatch,
        [_task("task-1", "processing", age_seconds=10)],
        job_ids={"task-1": "task-1:beef"},
        live_job_ids=(),
    )
    service.clip_repo.get_clips_by_task = AsyncMock(return_value=[])
    service._load_task_source_settings = AsyncMock(return_value={})
    task = await service.get_task_with_clips("task-1")
    assert task["status"] == "processing"


# --------------------------------------------------------------------------
# Worker startup hook
# --------------------------------------------------------------------------


def _patch_startup(monkeypatch, service_class):
    class FakeSession:
        async def __aenter__(self):
            return object()

        async def __aexit__(self, *exc_info):
            return False

    monkeypatch.setattr("src.database.AsyncSessionLocal", lambda: FakeSession())
    monkeypatch.setattr("src.services.task_service.TaskService", service_class)


@pytest.mark.asyncio
async def test_worker_startup_runs_recovery(monkeypatch):
    calls = {}

    class StubService:
        def __init__(self, _db):
            pass

        async def recover_stale_tasks(self, *, worker_startup):
            calls["worker_startup"] = worker_startup
            return {"checked": 2, "failed": 1}

    _patch_startup(monkeypatch, StubService)
    await worker_tasks._recover_stale_tasks_on_startup({})
    assert calls["worker_startup"] is True


@pytest.mark.asyncio
async def test_worker_startup_recovery_failure_is_not_fatal(monkeypatch):
    class StubService:
        def __init__(self, _db):
            pass

        async def recover_stale_tasks(self, **_kwargs):
            raise RuntimeError("db down")

    _patch_startup(monkeypatch, StubService)
    # Must not raise: the worker still starts and the lazy sweep applies.
    await worker_tasks._recover_stale_tasks_on_startup({})


def test_worker_startup_hook_is_composed():
    assert worker_tasks.WorkerSettings.on_startup is worker_tasks._worker_startup
