"""Studio look flow: edit service -> re-render seam -> routes -> worker."""

import copy
import inspect
import json
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

from src.api.routes import tasks as task_routes
from src.auto_director.edl import load_profile as _real_load_profile
from src.clip_source_map import (
    load_clip_editor_settings,
    save_clip_editor_settings,
)
from src.services import clip_service as clip_service_module
from src.workers import tasks as worker_tasks

from test_clip_edit_endpoint import _service, edit_boundary  # noqa: F401
from test_clip_rerender import FakeRedis


def _seed_plan(clip_path: Path, look=None):
    plan = {
        "version": 1,
        "niche": "gaming",
        "segments": [
            {"start": 0.0, "end": 5.0, "transition_in": "none", "transition_duration": 0.0},
            {"start": 5.0, "end": 10.0, "transition_in": "fade", "transition_duration": 0.4},
        ],
    }
    if look is not None:
        plan["look"] = look
    save_clip_editor_settings(clip_path, plan)


# --- Service: the look reaches the render ------------------------------------


@pytest.mark.asyncio
async def test_edit_with_look_stamps_resolved_edl_look_and_persists_plan(
    edit_boundary, monkeypatch, tmp_path
):
    service, _clip_repo, _clip_path = _service(tmp_path, monkeypatch)
    look = {"transition": "fade", "grade": {"saturation": 45}, "vfx": {"grain": 30}}

    refreshed = await service.edit_clip("task-1", "clip-1", look=look)

    render = service.video_service.render_call
    assert render is not None
    edl_look = render["edl"]["look"]
    # User field on top of the gaming profile's punchy base grade.
    assert edl_look["grade"]["saturation"] == 45
    assert edl_look["grade"]["contrast"] == 14
    assert edl_look["vfx"]["grain"] == 30
    assert edl_look["vfx"]["glow"] == 0
    # The transition pick drives the EDL joins like the re-render param.
    joins = render["edl"]["segments"][1:]
    assert joins and all(s["transition_in"] == "fade" for s in joins)
    # The plan persisted on the new file (and returned) carries the look.
    stored = load_clip_editor_settings(Path(refreshed["file_path"]))
    assert stored["look"] == look
    assert refreshed["edit_plan"]["look"] == look


@pytest.mark.asyncio
async def test_rerender_carries_the_stored_look_forward(
    edit_boundary, monkeypatch, tmp_path
):
    service, _clip_repo, clip_path = _service(tmp_path, monkeypatch)
    look = {"transition": "wipeleft", "grade": {"temperature": 30}}
    _seed_plan(clip_path, look=look)

    refreshed = await service.rerender_clip("task-1", "clip-1", "gaming")

    render = service.video_service.render_call
    assert render is not None
    assert render["edl"]["look"]["grade"]["temperature"] == 30
    # Profile base still applies underneath the carried user fields.
    assert render["edl"]["look"]["grade"]["saturation"] == 22
    joins = render["edl"]["segments"][1:]
    assert joins and all(s["transition_in"] == "wipeleft" for s in joins)
    stored = load_clip_editor_settings(Path(refreshed["file_path"]))
    assert stored["look"] == look


@pytest.mark.asyncio
async def test_neutral_look_on_a_neutral_profile_stamps_nothing(
    edit_boundary, monkeypatch, tmp_path
):
    service, _clip_repo, _clip_path = _service(
        tmp_path, monkeypatch, niche="podcast_news"
    )

    await service.edit_clip(
        "task-1", "clip-1", look={"grade": {"exposure": 0, "saturation": 0}}
    )

    render = service.video_service.render_call
    assert render is not None
    assert "look" not in render["edl"]


@pytest.mark.asyncio
async def test_edit_rejects_an_invalid_look_before_rendering(
    edit_boundary, monkeypatch, tmp_path
):
    service, _clip_repo, _clip_path = _service(tmp_path, monkeypatch)

    with pytest.raises(ValueError, match="between -100 and 100"):
        await service.edit_clip(
            "task-1", "clip-1", look={"grade": {"exposure": 500}}
        )
    assert service.video_service.render_call is None


# --- Service: edit-plan display state -----------------------------------------


@pytest.mark.asyncio
async def test_edit_plan_reports_effective_look_and_catalogs(
    edit_boundary, monkeypatch, tmp_path
):
    service, _clip_repo, clip_path = _service(tmp_path, monkeypatch)
    _seed_plan(clip_path, look={"transition": "fade", "grade": {"saturation": 30}})

    result = await service.get_clip_edit_plan("task-1", "clip-1")

    look = result["look"]
    assert look["stored"] is True
    assert look["transition"] == "fade"
    assert look["profile_lut"] == "punchy"
    assert look["grade"]["saturation"] == 30  # user field
    assert look["grade"]["contrast"] == 14  # punchy base underneath
    assert look["vfx"] == {
        "grain": 0, "vignette": 0, "glow": 0,
        "sharpen": 0, "letterbox": 0, "shake": 0,
    }
    assert len(result["transitions"]) == 59
    assert result["transitions"][0]["name"] == "hard_cut"
    assert [p["name"] for p in result["grade_presets"]] == [
        "neutral", "cinematic", "sports_punch",
        "podcast_clean", "warm_travel", "documentary",
    ]


@pytest.mark.asyncio
async def test_edit_plan_without_a_stored_look_reports_profile_defaults(
    edit_boundary, monkeypatch, tmp_path
):
    service, _clip_repo, _clip_path = _service(tmp_path, monkeypatch)

    result = await service.get_clip_edit_plan("task-1", "clip-1")

    look = result["look"]
    assert look["stored"] is False
    # Gaming's signature transition is pixelize: merge 6767093 (viral
    # scout 1+2) declares one signature per niche profile and build_edl
    # stamps it onto the first suitable join, which _look_display
    # reports when no look is stored. The grade is still the punchy base.
    assert look["transition"] == "pixelize"
    assert look["grade"]["saturation"] == 22


@pytest.mark.asyncio
async def test_edit_plan_without_a_declared_signature_reports_hard_cut(
    edit_boundary, monkeypatch, tmp_path
):
    # The hard-cut default survives at the display layer: with no
    # signature declared, build_edl stamps nothing and the first join
    # stays a hard cut. Every shipped profile declares a signature
    # (test_signature_transitions pins that), so pin the default by
    # stripping the declaration from the loaded profile, as the
    # velocity-curve pin does in test_beat_edl.py (692df19).
    def _unsigned_profile(niche):
        profile = copy.deepcopy(_real_load_profile(niche))
        (profile.get("transitions") or {}).pop("signature", None)
        return profile

    monkeypatch.setattr(
        clip_service_module, "load_profile", _unsigned_profile
    )
    service, _clip_repo, _clip_path = _service(tmp_path, monkeypatch)

    result = await service.get_clip_edit_plan("task-1", "clip-1")

    look = result["look"]
    assert look["stored"] is False
    assert look["transition"] == "hard_cut"
    assert look["grade"]["saturation"] == 22


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
async def test_edit_route_queues_a_look_only_edit(monkeypatch, fake_queue):
    _stub_route_service(monkeypatch, {"id": "clip-1", "task_id": "task-1"})
    queue, _redis = fake_queue

    result = await task_routes.edit_clip(
        "task-1",
        "clip-1",
        _request(
            {"look": {"transition": "cross_dissolve", "grade": {"saturation": 10}}},
            queue,
        ),
        db=None,
    )

    assert result["status"] == "queued"
    args, _kwargs = queue.enqueue_job.call_args
    assert args[0] == "edit_clip_task"
    # The look is canonicalised on the way in (cross_dissolve -> fade).
    assert args[7] == {"transition": "fade", "grade": {"saturation": 10}}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "body",
    [
        {"look": {"grade": {"exposure": 999}}},
        {"look": {"transition": "spin_around"}},
        {"look": {"vfx": {"lens_flare": 5}}},
        {"look": "everything"},
    ],
)
async def test_edit_route_rejects_invalid_looks_with_422(
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
async def test_rerender_route_with_look_uses_the_edit_job(
    monkeypatch, fake_queue
):
    _stub_route_service(monkeypatch, {"id": "clip-1", "task_id": "task-1"})
    queue, _redis = fake_queue

    result = await task_routes.rerender_clip(
        "task-1",
        "clip-1",
        _request(
            {"niche_override": "gaming", "look": {"vfx": {"grain": 25}}},
            queue,
        ),
        db=None,
    )

    assert result["status"] == "queued"
    args, _kwargs = queue.enqueue_job.call_args
    assert args[0] == "edit_clip_task"
    assert args[7] == {"vfx": {"grain": 25}}


# --- Worker -----------------------------------------------------------------------


@pytest.mark.asyncio
async def test_worker_forwards_the_look_to_the_service(monkeypatch):
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
    look = {"grade": {"contrast": 5}}
    outcome = await worker_tasks.edit_clip_task(
        ctx, "task-1", "clip-1", None, None, None, None, look
    )

    assert outcome["status"] == "completed"
    assert captured["kwargs"]["look"] == look
    stored = json.loads(redis.store["clip_rerender:clip-edit:clip-1:abc123"])
    assert stored["status"] == "completed"
    assert inspect.iscoroutinefunction(worker_tasks.edit_clip_task)
