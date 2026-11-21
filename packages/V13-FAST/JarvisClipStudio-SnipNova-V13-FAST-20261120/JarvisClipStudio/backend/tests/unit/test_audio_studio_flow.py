"""Audio Studio flow: edit service -> re-render seam -> routes -> worker."""

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
)
from src.workers import tasks as worker_tasks

from test_clip_edit_endpoint import _service, edit_boundary  # noqa: F401
from test_clip_rerender import FakeRedis


def _seed_plan(clip_path: Path, audio=None, voiceover=None):
    plan = {
        "version": 1,
        "niche": "gaming",
        "segments": [
            {"start": 0.0, "end": 5.0, "transition_in": "none", "transition_duration": 0.0},
            {"start": 5.0, "end": 10.0, "transition_in": "fade", "transition_duration": 0.4},
        ],
    }
    if audio is not None:
        plan["audio"] = audio
    if voiceover is not None:
        plan["voiceover"] = voiceover
    save_clip_editor_settings(clip_path, plan)


# --- Service: the audio reaches the render ------------------------------------


@pytest.mark.asyncio
async def test_edit_with_audio_stamps_edl_and_persists_plan(
    edit_boundary, monkeypatch, tmp_path
):
    service, _clip_repo, _clip_path = _service(tmp_path, monkeypatch)
    audio = {
        "music_volume": 35,
        "ducking_db": 18,
        "noise_reduction": 40,
        "loudness": "-16",
        "fade_out": 1.0,
    }

    refreshed = await service.edit_clip("task-1", "clip-1", audio=audio)

    render = service.video_service.render_call
    assert render is not None
    assert render["edl"]["audio_studio"] == {
        "music_volume": 0.35,
        "ducking_db": 18.0,
        "noise_reduction": 40,
        "loudness": "-16",
        "fade_out": 1.0,
    }
    stored = load_clip_editor_settings(Path(refreshed["file_path"]))
    assert stored["audio"] == audio
    assert refreshed["edit_plan"]["audio"] == audio


@pytest.mark.asyncio
async def test_rerender_carries_the_stored_audio_forward(
    edit_boundary, monkeypatch, tmp_path
):
    service, _clip_repo, clip_path = _service(tmp_path, monkeypatch)
    _seed_plan(clip_path, audio={"voice_clarity": 55, "fade_in": 0.5})

    refreshed = await service.rerender_clip("task-1", "clip-1", "gaming")

    render = service.video_service.render_call
    assert render is not None
    assert render["edl"]["audio_studio"] == {
        "voice_clarity": 55,
        "fade_in": 0.5,
    }
    stored = load_clip_editor_settings(Path(refreshed["file_path"]))
    assert stored["audio"] == {"voice_clarity": 55, "fade_in": 0.5}


@pytest.mark.asyncio
async def test_default_audio_stamps_nothing(
    edit_boundary, monkeypatch, tmp_path
):
    service, _clip_repo, _clip_path = _service(tmp_path, monkeypatch)

    await service.edit_clip(
        "task-1", "clip-1", audio={"loudness": "auto", "noise_reduction": 0}
    )

    render = service.video_service.render_call
    assert render is not None
    assert "audio_studio" not in render["edl"]


@pytest.mark.asyncio
async def test_music_track_none_silences_the_bed(
    edit_boundary, monkeypatch, tmp_path
):
    service, _clip_repo, _clip_path = _service(tmp_path, monkeypatch)

    await service.edit_clip(
        "task-1", "clip-1", audio={"music_track": "none"}
    )

    render = service.video_service.render_call
    assert render is not None
    audio_block = render["edl"]["audio"]
    assert audio_block["music_mood"] == "none"
    assert audio_block["music_volume"] == 0.0
    assert audio_block["music_source"] == "user_none"


@pytest.mark.asyncio
async def test_edit_rejects_invalid_audio_before_rendering(
    edit_boundary, monkeypatch, tmp_path
):
    service, _clip_repo, _clip_path = _service(tmp_path, monkeypatch)

    with pytest.raises(ValueError, match="between 0 and 100"):
        await service.edit_clip(
            "task-1", "clip-1", audio={"music_volume": 140}
        )
    assert service.video_service.render_call is None


# --- Service: voiceover ---------------------------------------------------------


@pytest.mark.asyncio
async def test_voiceover_spec_synthesizes_and_stamps_the_edl(
    edit_boundary, monkeypatch, tmp_path
):
    monkeypatch.setenv("VOICEOVER_ENABLED", "true")
    service, _clip_repo, _clip_path = _service(tmp_path, monkeypatch)
    monkeypatch.setattr(
        "src.media.voiceover.voiceover_edl_block",
        lambda spec: {"path": "/tmp/vo_fake.wav", "mode": spec["mode"]},
    )

    refreshed = await service.edit_clip(
        "task-1",
        "clip-1",
        voiceover={"script": "You will not believe this.", "voice": "am_michael", "mode": "hook"},
    )

    render = service.video_service.render_call
    assert render is not None
    assert render["edl"]["voiceover"] == {
        "path": "/tmp/vo_fake.wav",
        "mode": "hook",
    }
    stored = load_clip_editor_settings(Path(refreshed["file_path"]))
    assert stored["voiceover"]["script"] == "You will not believe this."
    assert stored["voiceover"]["voice"] == "am_michael"
    assert stored["voiceover"]["file"].startswith("vo_")


@pytest.mark.asyncio
async def test_voiceover_rejected_while_disabled(
    edit_boundary, monkeypatch, tmp_path
):
    monkeypatch.delenv("VOICEOVER_ENABLED", raising=False)
    service, _clip_repo, _clip_path = _service(tmp_path, monkeypatch)

    with pytest.raises(ValueError, match="disabled"):
        await service.edit_clip(
            "task-1", "clip-1", voiceover={"script": "Hello"}
        )
    assert service.video_service.render_call is None


@pytest.mark.asyncio
async def test_voiceover_clear_removes_the_stored_spec(
    edit_boundary, monkeypatch, tmp_path
):
    monkeypatch.setenv("VOICEOVER_ENABLED", "true")
    service, _clip_repo, clip_path = _service(tmp_path, monkeypatch)
    _seed_plan(
        clip_path,
        voiceover={
            "script": "Old line",
            "voice": "af_heart",
            "mode": "hook",
            "file": "vo_old.wav",
        },
    )

    refreshed = await service.edit_clip(
        "task-1", "clip-1", voiceover={"clear": True}
    )

    render = service.video_service.render_call
    assert render is not None
    assert "voiceover" not in render["edl"]
    stored = load_clip_editor_settings(Path(refreshed["file_path"]))
    assert "voiceover" not in stored


# --- Service: edit-plan display state -------------------------------------------


@pytest.mark.asyncio
async def test_edit_plan_reports_audio_state(
    edit_boundary, monkeypatch, tmp_path
):
    monkeypatch.delenv("VOICEOVER_ENABLED", raising=False)
    service, _clip_repo, clip_path = _service(tmp_path, monkeypatch)
    _seed_plan(clip_path, audio={"noise_reduction": 30, "loudness": "-16"})

    result = await service.get_clip_edit_plan("task-1", "clip-1")

    audio = result["audio"]
    assert audio["stored"] is True
    assert audio["noise_reduction"] == 30
    assert audio["loudness"] == "-16"
    assert audio["music_track"] == "auto"
    assert isinstance(audio["tracks"], list)
    # The feature is off: no voiceover surface at all.
    assert audio["voiceover_enabled"] is False
    assert audio["voiceover"] is None
    assert audio["voices"] == []


# --- Routes ---------------------------------------------------------------------


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
async def test_edit_route_queues_an_audio_only_edit(monkeypatch, fake_queue):
    _stub_route_service(monkeypatch, {"id": "clip-1", "task_id": "task-1"})
    queue, _redis = fake_queue

    result = await task_routes.edit_clip(
        "task-1",
        "clip-1",
        _request({"audio": {"noise_reduction": 25, "fade_in": 0.5}}, queue),
        db=None,
    )

    assert result["status"] == "queued"
    args, _kwargs = queue.enqueue_job.call_args
    assert args[0] == "edit_clip_task"
    assert args[8] == {"noise_reduction": 25, "fade_in": 0.5}
    assert args[9] is None


@pytest.mark.asyncio
async def test_edit_route_rejects_a_voiceover_spec_while_disabled(
    monkeypatch, fake_queue
):
    monkeypatch.delenv("VOICEOVER_ENABLED", raising=False)
    _stub_route_service(monkeypatch, {"id": "clip-1", "task_id": "task-1"})
    queue, _redis = fake_queue

    with pytest.raises(HTTPException) as error:
        await task_routes.edit_clip(
            "task-1",
            "clip-1",
            _request({"voiceover": {"script": "Hello"}}, queue),
            db=None,
        )

    assert error.value.status_code == 422
    queue.enqueue_job.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "body",
    [
        {"audio": {"music_volume": 140}},
        {"audio": {"loudness": "-12"}},
        {"audio": {"echo": 3}},
        {"audio": {"music_track": "not-in-the-library.wav"}},
    ],
)
async def test_edit_route_rejects_invalid_audio_with_422(
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
async def test_rerender_route_with_audio_uses_the_edit_job(
    monkeypatch, fake_queue
):
    _stub_route_service(monkeypatch, {"id": "clip-1", "task_id": "task-1"})
    queue, _redis = fake_queue

    result = await task_routes.rerender_clip(
        "task-1",
        "clip-1",
        _request(
            {"niche_override": "gaming", "audio": {"voice_clarity": 20}},
            queue,
        ),
        db=None,
    )

    assert result["status"] == "queued"
    args, _kwargs = queue.enqueue_job.call_args
    assert args[0] == "edit_clip_task"
    assert args[8] == {"voice_clarity": 20}


# --- Worker -----------------------------------------------------------------------


@pytest.mark.asyncio
async def test_worker_forwards_audio_and_voiceover(monkeypatch):
    captured = {}

    class StubTaskService:
        def __init__(self, _db):
            pass

        async def edit_clip(self, *args, **kwargs):
            captured["args"] = args
            captured["kwargs"] = kwargs
            return {"id": "clip-1"}

    @asynccontextmanager
    async def fake_session():
        yield object()

    monkeypatch.setattr("src.database.AsyncSessionLocal", fake_session)
    monkeypatch.setattr(
        "src.runtime_settings.load_runtime_settings_cache", AsyncMock()
    )
    monkeypatch.setattr("src.services.task_service.TaskService", StubTaskService)

    redis = FakeRedis()
    ctx = {"redis": redis, "job_id": "clip-edit:clip-1:abc123"}
    audio = {"noise_reduction": 15}
    voiceover = {"clear": True}
    outcome = await worker_tasks.edit_clip_task(
        ctx, "task-1", "clip-1", None, None, None, None, None, audio, voiceover
    )

    assert outcome["status"] == "completed"
    assert captured["kwargs"]["audio"] == audio
    assert captured["kwargs"]["voiceover"] == voiceover
    stored = json.loads(redis.store["clip_rerender:clip-edit:clip-1:abc123"])
    assert stored["status"] == "completed"
    assert inspect.iscoroutinefunction(worker_tasks.edit_clip_task)
