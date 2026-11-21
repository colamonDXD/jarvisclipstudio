"""Clip editor: edit service flow, edit routes, and the edit worker job."""

import inspect
import json
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

from src.api.routes import tasks as task_routes
from src.clip_source_map import (
    load_clip_editor_settings,
    save_clip_editor_settings,
    save_clip_source_ranges,
)
from src.services.task_service import TaskService
from src.workers import tasks as worker_tasks

from test_clip_rerender import FakeClipRepo, FakeRedis, FakeVideoService, _clip_dict


@pytest.fixture
def edit_boundary(monkeypatch):
    """Keep service unit tests independent of the PostgreSQL edit boundary."""

    @asynccontextmanager
    async def transaction(_db, _task_id):
        yield

    monkeypatch.setattr(
        "src.repositories.edit_transaction.task_edit_transaction", transaction
    )


def _service(tmp_path, monkeypatch, *, niche="gaming"):
    source_path = tmp_path / "source.mp4"
    source_path.write_bytes(b"source")
    clip_path = tmp_path / "clip.mp4"
    clip_path.write_bytes(b"clip")
    save_clip_source_ranges(clip_path, [(10.0, 20.0)])

    service = TaskService(db=None)
    clip_repo = FakeClipRepo(_clip_dict(clip_path))
    service.clip_repo = clip_repo

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
        "niche_override": niche,
        "music_override": None,
    }

    class FakeTaskRepo:
        async def get_task_by_id(self, _db, _task_id):
            return task

    service.task_repo = FakeTaskRepo()
    service.cache_repo = SimpleNamespace(get_cache=AsyncMock(return_value=None))
    service.video_service = FakeVideoService(source_path)
    service._media_limits = AsyncMock(return_value=(None, None))
    service._cleanup_source_video = lambda *args: None

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
    return service, clip_repo, clip_path


# --- Service: edit_clip -----------------------------------------------------


@pytest.mark.asyncio
async def test_edit_applies_operations_to_the_rendered_edl(
    edit_boundary, monkeypatch, tmp_path
):
    service, _clip_repo, clip_path = _service(tmp_path, monkeypatch)
    save_clip_editor_settings(
        clip_path,
        {
            "version": 1,
            "niche": "gaming",
            "segments": [
                {"start": 0.0, "end": 5.0, "transition_in": "none", "transition_duration": 0.0},
                {"start": 5.0, "end": 10.0, "transition_in": "fade", "transition_duration": 0.4},
            ],
        },
    )

    refreshed = await service.edit_clip(
        "task-1",
        "clip-1",
        operations=[
            {"type": "split", "index": 0, "at": 2.0},
            {"type": "delete", "index": 2},
        ],
    )

    render = service.video_service.render_call
    assert render is not None
    windows = [(s["start"], s["end"]) for s in render["edl"]["segments"]]
    assert windows == [(0.0, 2.0), (2.0, 5.0)]
    assert render["edl"]["duration"] == 5.0
    # The refreshed clip carries the plan that rendered, and the new file's
    # sidecar holds it for the next edit.
    assert [
        (s["start"], s["end"]) for s in refreshed["edit_plan"]["segments"]
    ] == windows
    stored = load_clip_editor_settings(Path(refreshed["file_path"]))
    assert [(s["start"], s["end"]) for s in stored["segments"]] == windows


@pytest.mark.asyncio
async def test_edit_passes_the_edited_footage_to_the_render(
    edit_boundary, monkeypatch, tmp_path
):
    """Deleting segment 0 must change the keep-ranges the render uses."""
    service, _clip_repo, clip_path = _service(tmp_path, monkeypatch)
    save_clip_source_ranges(clip_path, [(10.0, 15.0), (15.0, 20.0)])
    save_clip_editor_settings(
        clip_path,
        {
            "version": 1,
            "niche": "gaming",
            "segments": [
                {
                    "start": 0.0, "end": 5.0,
                    "transition_in": "none", "transition_duration": 0.0,
                    "source_offset": 0.0, "source_share": 0.5,
                },
                {
                    "start": 5.0, "end": 10.0,
                    "transition_in": "fade", "transition_duration": 0.4,
                    "source_offset": 5.0, "source_share": 0.5,
                },
            ],
        },
    )

    await service.edit_clip(
        "task-1", "clip-1", operations=[{"type": "delete", "index": 0}]
    )

    render = service.video_service.render_call
    assert render is not None
    # Only the second segment's footage (source 15-20) reaches the render.
    assert render["segment"]["keep_ranges"] == [(15.0, 20.0)]


@pytest.mark.asyncio
async def test_edit_seeds_a_plan_for_clips_that_predate_the_editor(
    edit_boundary, monkeypatch, tmp_path
):
    service, _clip_repo, _clip_path = _service(tmp_path, monkeypatch)

    refreshed = await service.edit_clip(
        "task-1", "clip-1", operations=[{"type": "delete", "index": 0}]
    )

    render = service.video_service.render_call
    assert render is not None
    # Seeded from a fresh gaming EDL build, one segment shorter afterwards.
    assert len(render["edl"]["segments"]) >= 1
    assert refreshed["edit_plan"]["niche"] == "gaming"


@pytest.mark.asyncio
async def test_edit_rejects_impossible_operation_before_rendering(
    edit_boundary, monkeypatch, tmp_path
):
    service, _clip_repo, _clip_path = _service(tmp_path, monkeypatch)

    with pytest.raises(ValueError, match="trim can only shrink"):
        await service.edit_clip(
            "task-1",
            "clip-1",
            operations=[{"type": "trim", "index": 0, "start": 9.9, "end": 10.0}],
        )
    assert service.video_service.render_call is None


@pytest.mark.asyncio
async def test_edit_applies_caption_overrides_to_the_render(
    edit_boundary, monkeypatch, tmp_path
):
    service, _clip_repo, clip_path = _service(tmp_path, monkeypatch)

    refreshed = await service.edit_clip(
        "task-1",
        "clip-1",
        captions={
            "template": "hormozi",
            "font_size": 44,
            "font_color": "#ffe000",
            "position": "top",
        },
    )

    render = service.video_service.render_call
    assert render["caption_template"] == "hormozi"
    # Caption position is carried on the EDL itself (direct-EDL mode has no
    # separate placement argument — the ASS builder reads edl["captions"]).
    assert render["edl"]["captions"]["position"] == "top"
    assert refreshed["edit_plan"]["captions"]["position"] == "top"
    stored = load_clip_editor_settings(Path(refreshed["file_path"]))
    assert stored["captions"]["font_size"] == 44
    assert stored["captions"]["font_color"] == "#FFE000"
    _ = clip_path


@pytest.mark.asyncio
async def test_edit_rejects_unknown_caption_font_and_template(
    edit_boundary, monkeypatch, tmp_path
):
    service, _clip_repo, _clip_path = _service(tmp_path, monkeypatch)

    with pytest.raises(ValueError, match="font_family"):
        await service.edit_clip(
            "task-1", "clip-1", captions={"font_family": "Definitely Not A Font"}
        )
    with pytest.raises(ValueError, match="template"):
        await service.edit_clip(
            "task-1", "clip-1", captions={"template": "no-such-template"}
        )
    assert service.video_service.render_call is None


@pytest.mark.asyncio
async def test_get_clip_edit_plan_derives_then_reads_persisted(
    edit_boundary, monkeypatch, tmp_path
):
    service, _clip_repo, clip_path = _service(tmp_path, monkeypatch)

    derived = await service.get_clip_edit_plan("task-1", "clip-1")
    assert derived["persisted"] is False
    assert derived["plan"]["segments"]

    save_clip_editor_settings(
        clip_path,
        {
            "version": 1,
            "niche": "gaming",
            "segments": [
                {"start": 0.0, "end": 10.0, "transition_in": "none", "transition_duration": 0.0}
            ],
        },
    )
    persisted = await service.get_clip_edit_plan("task-1", "clip-1")
    assert persisted["persisted"] is True
    assert len(persisted["plan"]["segments"]) == 1


# --- Routes -----------------------------------------------------------------


def _request(body, queue_adapter):
    return SimpleNamespace(
        json=AsyncMock(return_value=body),
        headers={},
        app=SimpleNamespace(state=SimpleNamespace(queue_adapter=queue_adapter)),
    )


def _stub_route_service(monkeypatch, clip):
    class StubClipRepo:
        async def get_clip_by_id(self, _db, _clip_id):
            return clip

    class StubTaskService:
        def __init__(self, _db):
            self.clip_repo = StubClipRepo()

    monkeypatch.setattr(task_routes, "TaskService", StubTaskService)
    monkeypatch.setattr(
        task_routes, "_require_task_owner", AsyncMock(return_value={})
    )


@pytest.fixture
def fake_queue(monkeypatch):
    redis = FakeRedis()
    queue = SimpleNamespace(
        enqueue_job=AsyncMock(return_value="job"),
        get_pool=AsyncMock(return_value=redis),
        get_job_status=AsyncMock(return_value="queued"),
    )
    monkeypatch.setattr(task_routes, "JobQueue", queue)
    return queue, redis


@pytest.mark.asyncio
async def test_edit_route_queues_worker_job(monkeypatch, fake_queue):
    _stub_route_service(monkeypatch, {"id": "clip-1", "task_id": "task-1"})
    queue, redis = fake_queue
    operations = [{"type": "delete", "index": 0}]

    result = await task_routes.edit_clip(
        "task-1",
        "clip-1",
        _request({"operations": operations}, queue),
        db=None,
    )

    assert result["status"] == "queued"
    assert result["job_id"].startswith("clip-edit:clip-1:")
    args, kwargs = queue.enqueue_job.call_args
    assert args[0] == "edit_clip_task"
    assert args[1:4] == ("task-1", "clip-1", operations)
    assert kwargs["_job_id"] == result["job_id"]
    stored = json.loads(redis.store[f"clip_rerender:{result['job_id']}"])
    assert stored["status"] == "queued"
    assert stored["kind"] == "edit"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "body",
    [
        {},  # nothing to edit
        {"operations": "trim"},  # operations must be an array
        {"operations": []},  # empty edit
        {"captions": {"font_size": 500}},  # range-checked
        {"captions": {"template": "no-such-template"}},
        {"captions": {"font_family": "Definitely Not A Font"}},
        {"niche_override": "cooking", "captions": {"position": "top"}},
        {"music_override": "no-such-track.mp3", "captions": {"position": "top"}},
    ],
)
async def test_edit_route_rejects_invalid_payloads_with_422(
    monkeypatch, fake_queue, body
):
    _stub_route_service(monkeypatch, {"id": "clip-1", "task_id": "task-1"})
    queue, _redis = fake_queue

    with pytest.raises(HTTPException) as error:
        await task_routes.edit_clip(
            "task-1", "clip-1", _request(body, queue), db=None
        )

    assert error.value.status_code == 422
    queue.enqueue_job.assert_not_called()


@pytest.mark.asyncio
async def test_edit_route_missing_clip_is_404(monkeypatch, fake_queue):
    _stub_route_service(monkeypatch, None)
    queue, _redis = fake_queue

    with pytest.raises(HTTPException) as error:
        await task_routes.edit_clip(
            "task-1",
            "clip-1",
            _request({"operations": [{"type": "delete", "index": 0}]}, queue),
            db=None,
        )

    assert error.value.status_code == 404
    queue.enqueue_job.assert_not_called()


@pytest.mark.asyncio
async def test_edit_status_route_returns_recorded_outcome(
    monkeypatch, fake_queue
):
    from src.workers.job_queue import set_rerender_outcome

    _stub_route_service(monkeypatch, {"id": "clip-1", "task_id": "task-1"})
    queue, redis = fake_queue
    job_id = "clip-edit:clip-1:abc123"
    await set_rerender_outcome(
        redis,
        job_id,
        {"status": "completed", "clip_id": "clip-1", "kind": "edit"},
    )

    result = await task_routes.get_clip_edit_status(
        "task-1", "clip-1", job_id, _request({}, queue), db=None
    )
    assert result["status"] == "completed"
    assert result["kind"] == "edit"

    with pytest.raises(HTTPException) as error:
        await task_routes.get_clip_edit_status(
            "task-1", "clip-1", "clip-edit:other:abc123", _request({}, queue),
            db=None,
        )
    assert error.value.status_code == 404


@pytest.mark.asyncio
async def test_rerender_route_with_captions_uses_the_edit_job(
    monkeypatch, fake_queue
):
    _stub_route_service(monkeypatch, {"id": "clip-1", "task_id": "task-1"})
    queue, _redis = fake_queue

    result = await task_routes.rerender_clip(
        "task-1",
        "clip-1",
        _request(
            {
                "niche_override": "gaming",
                "captions": {"template": "hormozi", "position": "top"},
            },
            queue,
        ),
        db=None,
    )

    assert result["status"] == "queued"
    args, _kwargs = queue.enqueue_job.call_args
    assert args[0] == "edit_clip_task"
    assert args[4] == {"template": "hormozi", "position": "top"}


# --- Worker job ---------------------------------------------------------------


def _stub_worker(monkeypatch, edit):
    class StubTaskService:
        def __init__(self, _db):
            pass

        async def edit_clip(self, *args, **kwargs):
            result = edit(*args, **kwargs)
            if inspect.isawaitable(result):
                result = await result
            return result

    @asynccontextmanager
    async def fake_session():
        yield object()

    monkeypatch.setattr("src.database.AsyncSessionLocal", fake_session)
    monkeypatch.setattr(
        "src.runtime_settings.load_runtime_settings_cache", AsyncMock()
    )
    monkeypatch.setattr("src.services.task_service.TaskService", StubTaskService)


@pytest.mark.asyncio
async def test_worker_edit_records_completed_outcome(monkeypatch):
    clip = {"id": "clip-1", "edit_plan": {"version": 1, "segments": []}}
    _stub_worker(monkeypatch, AsyncMock(return_value=clip))
    redis = FakeRedis()
    ctx = {"redis": redis, "job_id": "clip-edit:clip-1:abc123"}

    outcome = await worker_tasks.edit_clip_task(
        ctx, "task-1", "clip-1", [{"type": "delete", "index": 0}]
    )

    assert outcome["status"] == "completed"
    assert outcome["kind"] == "edit"
    assert outcome["edit_plan"] == {"version": 1, "segments": []}
    stored = json.loads(redis.store["clip_rerender:clip-edit:clip-1:abc123"])
    assert stored["status"] == "completed"


@pytest.mark.asyncio
async def test_worker_edit_records_failure_without_raising(monkeypatch):
    _stub_worker(
        monkeypatch, AsyncMock(side_effect=ValueError("trim can only shrink"))
    )
    redis = FakeRedis()
    ctx = {"redis": redis, "job_id": "clip-edit:clip-1:abc123"}

    outcome = await worker_tasks.edit_clip_task(
        ctx, "task-1", "clip-1", [{"type": "trim", "index": 0, "end": 1.0}]
    )

    assert outcome["status"] == "failed"
    assert "trim can only shrink" in outcome["error"]
