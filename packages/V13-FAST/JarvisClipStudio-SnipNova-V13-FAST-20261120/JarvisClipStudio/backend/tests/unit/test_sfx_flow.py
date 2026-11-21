"""SFX placement flow: edit service -> re-render seam (EDL sfx_events)."""

import json
from pathlib import Path

import pytest

from src.clip_source_map import (
    load_clip_editor_settings,
    save_clip_editor_settings,
)

from test_clip_edit_endpoint import _service, edit_boundary  # noqa: F401

KNOWN_SFX = {"pop.wav", "whoosh.mp3"}


@pytest.fixture
def sfx_library(monkeypatch):
    """Pretend the deployment's assets/sfx holds two files."""
    monkeypatch.setattr(
        "src.media.sfx_library.find_sfx_file",
        lambda name: Path("/assets/sfx") / name if name in KNOWN_SFX else None,
    )


def _seed_plan(clip_path: Path, sfx=None):
    plan = {
        "version": 1,
        "niche": "gaming",
        "segments": [
            {"start": 0.0, "end": 5.0, "transition_in": "none", "transition_duration": 0.0},
            {"start": 5.0, "end": 10.0, "transition_in": "fade", "transition_duration": 0.4},
        ],
    }
    if sfx is not None:
        plan["sfx"] = sfx
    save_clip_editor_settings(clip_path, plan)


@pytest.mark.asyncio
async def test_edit_with_sfx_stamps_edl_events_and_persists_plan(
    edit_boundary, sfx_library, monkeypatch, tmp_path
):
    service, _clip_repo, _clip_path = _service(tmp_path, monkeypatch)
    placements = [{"name": "pop.wav", "at_sec": 1.5, "gain_db": -3.0}]

    refreshed = await service.edit_clip("task-1", "clip-1", sfx=placements)

    render = service.video_service.render_call
    assert render is not None
    events = render["edl"]["sfx_events"]
    assert events[-1] == {"name": "pop.wav", "at_sec": 1.5, "gain_db": -3.0}
    stored = load_clip_editor_settings(Path(refreshed["file_path"]))
    assert stored["sfx"] == placements
    assert refreshed["edit_plan"]["sfx"] == placements


@pytest.mark.asyncio
async def test_rerender_carries_stored_placements_forward(
    edit_boundary, sfx_library, monkeypatch, tmp_path
):
    service, _clip_repo, clip_path = _service(tmp_path, monkeypatch)
    placements = [{"name": "whoosh.mp3", "at_sec": 3.0, "gain_db": 0.0}]
    _seed_plan(clip_path, sfx=placements)

    refreshed = await service.rerender_clip("task-1", "clip-1", "gaming")

    render = service.video_service.render_call
    assert render is not None
    assert {"name": "whoosh.mp3", "at_sec": 3.0, "gain_db": 0.0} in render["edl"][
        "sfx_events"
    ]
    stored = load_clip_editor_settings(Path(refreshed["file_path"]))
    assert stored["sfx"] == placements


@pytest.mark.asyncio
async def test_edit_with_empty_sfx_clears_placements(
    edit_boundary, sfx_library, monkeypatch, tmp_path
):
    service, _clip_repo, clip_path = _service(tmp_path, monkeypatch)
    _seed_plan(
        clip_path, sfx=[{"name": "pop.wav", "at_sec": 1.0, "gain_db": 0.0}]
    )

    refreshed = await service.edit_clip("task-1", "clip-1", sfx=[])

    render = service.video_service.render_call
    assert render is not None
    user_events = [
        event
        for event in render["edl"].get("sfx_events") or []
        if event.get("name") == "pop.wav"
    ]
    assert user_events == []
    stored = load_clip_editor_settings(Path(refreshed["file_path"]))
    assert stored["sfx"] == []


@pytest.mark.asyncio
async def test_edit_rejects_an_unknown_sfx_file_before_rendering(
    edit_boundary, sfx_library, monkeypatch, tmp_path
):
    service, _clip_repo, _clip_path = _service(tmp_path, monkeypatch)

    with pytest.raises(ValueError, match="not in the SFX library"):
        await service.edit_clip(
            "task-1",
            "clip-1",
            sfx=[{"name": "airhorn.wav", "at_sec": 0.5, "gain_db": 0.0}],
        )
    assert service.video_service.render_call is None


@pytest.mark.asyncio
async def test_edit_plan_reports_placements_and_library(
    edit_boundary, monkeypatch, tmp_path
):
    service, _clip_repo, clip_path = _service(tmp_path, monkeypatch)
    placements = [{"name": "pop.wav", "at_sec": 2.0, "gain_db": -6.0}]
    _seed_plan(clip_path, sfx=placements)
    monkeypatch.setattr(
        "src.media.sfx_library.list_sfx_tracks",
        lambda probe_durations=True: [
            {"filename": "pop.wav", "display_name": "pop", "duration": 0.4}
        ],
    )

    response = await service.get_clip_edit_plan("task-1", "clip-1")

    assert response["sfx"]["placements"] == placements
    assert response["sfx"]["library"][0]["filename"] == "pop.wav"
    # The LUT library rides the same response for the Colour panel.
    assert isinstance(response["luts"], list)


def test_worker_signature_carries_sfx():
    import inspect

    from src.workers import tasks as worker_tasks

    params = inspect.signature(worker_tasks.edit_clip_task).parameters
    assert "sfx" in params
    assert params["sfx"].default is None


def test_route_validator_accepts_and_rejects(sfx_library):
    from src.api.routes.tasks import _validate_sfx_placements

    assert _validate_sfx_placements({}) is None
    assert _validate_sfx_placements(
        {"sfx": [{"name": "pop.wav", "at_sec": 1, "gain_db": 0}]}
    ) == [{"name": "pop.wav", "at_sec": 1.0, "gain_db": 0.0}]

    from fastapi import HTTPException

    with pytest.raises(HTTPException) as excinfo:
        _validate_sfx_placements(
            {"sfx": [{"name": "airhorn.wav", "at_sec": 1, "gain_db": 0}]}
        )
    assert excinfo.value.status_code == 422
    with pytest.raises(HTTPException):
        _validate_sfx_placements({"sfx": [{"name": "pop.wav", "at_sec": -1}]})


def test_edit_plan_json_roundtrip_keeps_sfx():
    plan = {"sfx": [{"name": "pop.wav", "at_sec": 1.0, "gain_db": 0.0}]}
    assert json.loads(json.dumps(plan)) == plan
