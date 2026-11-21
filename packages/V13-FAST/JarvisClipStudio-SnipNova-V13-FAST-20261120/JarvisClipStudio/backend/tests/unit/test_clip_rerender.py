"""Clip re-render endpoint, worker job, and re-style service behavior."""

import inspect
import json
import logging
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

from src.api.routes import tasks as task_routes
from src.auto_director.edl import load_profile
from src.auto_director.niche import NICHE_VALUES
from src.caption_templates import get_template_names
from src.clip_source_map import save_clip_source_ranges
from src.services.task_service import TaskService
from src.workers import tasks as worker_tasks


@pytest.fixture
def edit_boundary(monkeypatch):
    """Keep service unit tests independent of the PostgreSQL edit boundary."""

    @asynccontextmanager
    async def transaction(_db, _task_id):
        yield

    monkeypatch.setattr(
        "src.repositories.edit_transaction.task_edit_transaction", transaction
    )


class FakeRedis:
    def __init__(self):
        self.store = {}

    async def set(self, key, value, ex=None):
        self.store[key] = value

    async def get(self, key):
        return self.store.get(key)


def _clip_dict(clip_path: Path) -> dict:
    return {
        "id": "clip-1",
        "task_id": "task-1",
        "file_path": str(clip_path),
        "filename": clip_path.name,
        "start_time": "00:10",
        "end_time": "00:20",
        "duration": 10.0,
        "text": "the truth is the data shows 42 percent growth",
        "relevance_score": 0.9,
        "reasoning": "strong claim",
        "clip_order": 1,
        "virality_score": 88,
        "hook_score": 20,
        "engagement_score": 22,
        "value_score": 21,
        "shareability_score": 25,
        "hook_type": "statement",
        "hook_title": "The truth about growth",
        "niche_override": None,
    }


class FakeClipRepo:
    def __init__(self, clip):
        self.clip = clip
        self.updates = []
        self.niche_updates = []

    async def get_clip_by_id(self, _db, clip_id):
        if self.clip is None or self.clip["id"] != clip_id:
            return None
        return dict(self.clip)

    async def update_clip(self, _db, clip_id, filename, file_path, start_time, end_time, duration, text):
        self.updates.append(
            {
                "clip_id": clip_id,
                "filename": filename,
                "file_path": file_path,
                "start_time": start_time,
                "end_time": end_time,
                "duration": duration,
                "text": text,
            }
        )
        self.clip = {
            **self.clip,
            "filename": filename,
            "file_path": file_path,
            "start_time": start_time,
            "end_time": end_time,
            "duration": duration,
            "text": text,
        }

    async def update_clip_niche_override(self, _db, clip_id, niche_override):
        self.niche_updates.append(niche_override)
        self.clip = {**self.clip, "niche_override": niche_override}


class FakeVideoService:
    """Records the render call; transcription/analysis must never run."""

    def __init__(self, source_path: Path):
        self.source_path = source_path
        self.download_calls = []
        self.render_call = None
        self.generate_transcript = AsyncMock(
            side_effect=AssertionError("re-render must not transcribe")
        )
        self.analyze_transcript = AsyncMock(
            side_effect=AssertionError("re-render must not re-analyse")
        )

    async def download_video(self, url, quality=None, task_id=None, ranges=None):
        self.download_calls.append(
            {"url": url, "quality": quality, "task_id": task_id, "ranges": ranges}
        )
        return str(self.source_path)

    async def prepare_source(self, path):
        return path

    async def create_single_clip(
        self,
        video_path,
        segment,
        clip_index,
        output_dir,
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
        broll_session=None,
        edl=None,
        niche_override=None,
        project_niche_override=None,
        classifier_niche=None,
    ):
        self.render_call = {
            "video_path": video_path,
            "segment": segment,
            "clip_index": clip_index,
            "output_dir": output_dir,
            "caption_template": caption_template,
            "output_format": output_format,
            "edl": edl,
            "niche_override": niche_override,
            "project_niche_override": project_niche_override,
            "classifier_niche": classifier_niche,
        }
        output_dir = Path(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)
        rendered = output_dir / "clip_rerendered.mp4"
        rendered.write_bytes(b"rendered")
        return {
            "filename": rendered.name,
            "path": str(rendered),
            "start_time": segment["start_time"],
            "end_time": segment["end_time"],
            "duration": 10.0,
            "text": segment.get("text", ""),
        }


def _service(tmp_path, monkeypatch, *, source_type="youtube", clip=True, task_fields=None):
    source_path = tmp_path / "source.mp4"
    source_path.write_bytes(b"source")
    clip_path = tmp_path / "clip.mp4"
    clip_path.write_bytes(b"clip")
    save_clip_source_ranges(clip_path, [(10.0, 20.0)])

    service = TaskService(db=None)
    clip_repo = FakeClipRepo(_clip_dict(clip_path) if clip else None)
    service.clip_repo = clip_repo

    task = {
        "id": "task-1",
        "user_id": "user-1",
        "source_url": "https://www.youtube.com/watch?v=abcdefghijk",
        "source_type": source_type,
        "processing_mode": "fast",
        "caption_template": "default",
        "font_family": None,
        "font_size": None,
        "font_color": None,
        "niche_override": None,
    }
    if task_fields:
        task.update(task_fields)

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
    return service, clip_repo


@pytest.mark.asyncio
async def test_rerender_rebuilds_edl_with_target_profile(
    edit_boundary, monkeypatch, tmp_path
):
    service, clip_repo = _service(tmp_path, monkeypatch)

    refreshed = await service.rerender_clip("task-1", "clip-1", "travel_vlog")

    render = service.video_service.render_call
    assert render is not None
    # The stored source range is fetched and reused — no re-selection.
    assert service.video_service.download_calls[0]["ranges"] == [(10.0, 20.0)]
    assert render["segment"]["keep_ranges"] == [(10.0, 20.0)]
    assert render["segment"]["text"].startswith("the truth is")
    # The EDL is rebuilt from the target profile.
    profile = load_profile("travel_vlog")
    edl = render["edl"]
    assert edl["captions"]["preset_id"] == profile["captions"]["preset_id"]
    assert all(
        segment["transition_in"] == profile["transitions"]["default"]
        for segment in edl["segments"][1:]
    )
    assert render["niche_override"] == "travel_vlog"
    # No transcription or analysis happened on the way.
    service.video_service.generate_transcript.assert_not_called()
    service.video_service.analyze_transcript.assert_not_called()
    # The clip row now points at the new render and records the profile.
    assert clip_repo.updates and clip_repo.updates[0]["filename"] == "clip_rerendered.mp4"
    assert clip_repo.niche_updates == ["travel_vlog"]
    assert refreshed["niche_profile"] == "travel_vlog"
    assert refreshed["niche_override"] == "travel_vlog"


@pytest.mark.asyncio
async def test_rerender_applies_transition_and_caption_overrides(
    edit_boundary, monkeypatch, tmp_path
):
    service, _clip_repo = _service(tmp_path, monkeypatch)
    template = next(name for name in get_template_names() if name != "default")

    await service.rerender_clip(
        "task-1",
        "clip-1",
        "gaming",
        caption_template=template,
        transition="fade",
    )

    render = service.video_service.render_call
    assert render["caption_template"] == template
    assert render["edl"]["captions"]["preset_id"] == template
    assert all(
        segment["transition_in"] == "fade"
        for segment in render["edl"]["segments"][1:]
    )


# ---------------------------------------------------------------------------
# Stored per-clip caption position on direct re-renders (F-B):
# rerender_clip must carry the stored plan's captions forward when the
# caller passes no caption_overrides, exactly as edit_clip does — an
# explicit argument still wins, and the plan persisted next to the new
# file must agree with what actually rendered.
# ---------------------------------------------------------------------------


def _store_caption_plan(clip_path: Path, captions: dict) -> None:
    from src.auto_director.clip_edits import build_edit_plan
    from src.clip_source_map import save_clip_editor_settings

    save_clip_editor_settings(
        clip_path,
        build_edit_plan(
            [{"index": 0, "start": 0.0, "end": 10.0}],
            captions=captions,
        ),
    )


@pytest.mark.asyncio
async def test_rerender_carries_stored_caption_position_when_none_given(
    edit_boundary, monkeypatch, tmp_path
):
    """F-B: a stored Top survives a direct re-render with no overrides.

    podcast_news defaults to bottom; the stored plan says top. The EDL
    must carry top (resolved to position_y 0.18), and the plan persisted
    next to the new file must still say top — the editor and the file
    must agree.
    """
    from src.clip_source_map import load_clip_editor_settings

    service, clip_repo = _service(tmp_path, monkeypatch)
    _store_caption_plan(tmp_path / "clip.mp4", {"position": "top"})

    await service.rerender_clip("task-1", "clip-1", "podcast_news")

    render = service.video_service.render_call
    assert render is not None
    assert render["edl"]["captions"]["position"] == "top"
    assert render["edl"]["captions"]["position_y"] == 0.18
    new_path = Path(clip_repo.updates[0]["file_path"])
    stored = load_clip_editor_settings(new_path)
    assert stored is not None
    assert stored.get("captions", {}).get("position") == "top"


@pytest.mark.asyncio
async def test_rerender_explicit_caption_overrides_win_over_stored_plan(
    edit_boundary, monkeypatch, tmp_path
):
    """An explicit caption_overrides argument still beats the stored plan."""
    from src.clip_source_map import load_clip_editor_settings

    service, clip_repo = _service(tmp_path, monkeypatch)
    _store_caption_plan(tmp_path / "clip.mp4", {"position": "top"})

    await service.rerender_clip(
        "task-1",
        "clip-1",
        "podcast_news",
        caption_overrides={"position": "bottom"},
    )

    render = service.video_service.render_call
    assert render is not None
    assert render["edl"]["captions"]["position"] == "bottom"
    assert render["edl"]["captions"]["position_y"] == 0.78
    new_path = Path(clip_repo.updates[0]["file_path"])
    stored = load_clip_editor_settings(new_path)
    assert stored is not None
    assert stored.get("captions", {}).get("position") == "bottom"


@pytest.mark.asyncio
async def test_rerender_editor_plan_keeps_stored_caption_position(
    edit_boundary, monkeypatch, tmp_path
):
    """F-B persist half: with an editor segment plan the stored Top must
    reach both the EDL and the plan persisted next to the new file.

    (The persist step used to reload the stored captions only for plain
    re-styles, so an editor render silently dropped them from the new
    plan even when the EDL had carried them.)
    """
    from src.clip_source_map import load_clip_editor_settings

    service, clip_repo = _service(tmp_path, monkeypatch)
    _store_caption_plan(tmp_path / "clip.mp4", {"position": "top"})
    segment_plan = [
        {
            "index": 0,
            "start": 0.0,
            "end": 10.0,
            "source_offset": 10.0,
            "source_share": 1.0,
        },
    ]

    await service.rerender_clip(
        "task-1", "clip-1", "podcast_news", segment_plan=segment_plan
    )

    render = service.video_service.render_call
    assert render is not None
    assert render["edl"]["captions"]["position"] == "top"
    assert render["edl"]["captions"]["position_y"] == 0.18
    new_path = Path(clip_repo.updates[0]["file_path"])
    stored = load_clip_editor_settings(new_path)
    assert stored is not None
    assert stored.get("captions", {}).get("position") == "top"


@pytest.mark.asyncio
async def test_rerender_carries_saved_broll_placements(
    edit_boundary, monkeypatch, tmp_path
):
    """A clip's saved stock cutaways survive a re-style.

    The saved placements (clip seconds, from the clip's source-map
    sidecar) ride the re-render's EDL with their resolved footage, so
    the re-render composites the same stock windows instead of
    re-planning the profile's queries from the degraded transcript.
    """
    from src.clip_source_map import save_clip_broll

    service, _clip_repo = _service(tmp_path, monkeypatch)
    clip_path = tmp_path / "clip.mp4"
    stock = tmp_path / "stock.mp4"
    stock.write_bytes(b"stock")
    save_clip_broll(
        clip_path,
        [
            {
                "start": 3.0,
                "duration": 2.5,
                "footage_offset": 0.0,
                "provider": "pixabay",
                "id": 4242,
                "local_path": str(stock),
                "query": "city",
            }
        ],
    )

    await service.rerender_clip("task-1", "clip-1", "travel_vlog")

    render = service.video_service.render_call
    assert render is not None
    entries = render["edl"]["broll"]
    assert len(entries) == 1
    entry = entries[0]
    assert entry["start"] == 3.0
    assert entry["end"] == 5.5
    assert entry["local_path"] == str(stock)
    assert entry["provider"] == "pixabay"


@pytest.mark.asyncio
async def test_rerender_without_saved_broll_keeps_profile_queries(
    edit_boundary, monkeypatch, tmp_path
):
    """No saved placements: the profile's own B-roll queries stay."""
    service, _clip_repo = _service(tmp_path, monkeypatch)

    await service.rerender_clip("task-1", "clip-1", "travel_vlog")

    render = service.video_service.render_call
    assert render is not None
    # travel_vlog enables B-roll; the fresh EDL's entries are queries
    # (no resolved file yet) — exactly the pre-carry behaviour.
    assert render["edl"]["broll"]
    assert all("local_path" not in e or not e["local_path"] for e in render["edl"]["broll"])


@pytest.mark.asyncio
async def test_rerender_editor_plan_rebases_saved_broll(
    edit_boundary, monkeypatch, tmp_path
):
    """An editor segment plan that drops the clip head rebases B-roll.

    The plan totals 6 s against the stored 10 s. Its segments carry
    the persisted source mapping (``source_offset``/``source_share``
    over the stored 10 s keep range): the first segment shows the
    clip's 4–7 s, the second its 7–10 s. A saved cutaway at 5–7 s
    lands at 1–3 s on the edited timeline, and one inside the dropped
    head disappears.
    """
    from src.clip_source_map import save_clip_broll

    service, _clip_repo = _service(tmp_path, monkeypatch)
    clip_path = tmp_path / "clip.mp4"
    stock = tmp_path / "stock.mp4"
    stock.write_bytes(b"stock")
    save_clip_broll(
        clip_path,
        [
            {"start": 1.0, "duration": 2.0, "provider": "pixabay", "local_path": str(stock)},
            {"start": 5.0, "duration": 2.0, "provider": "pixabay", "local_path": str(stock)},
        ],
    )
    segment_plan = [
        {"index": 0, "start": 0.0, "end": 3.0, "source_offset": 4.0, "source_share": 0.3},
        {"index": 1, "start": 3.0, "end": 6.0, "source_offset": 7.0, "source_share": 0.3},
    ]

    await service.rerender_clip(
        "task-1", "clip-1", "travel_vlog", segment_plan=segment_plan
    )

    render = service.video_service.render_call
    assert render is not None
    entries = render["edl"]["broll"]
    assert len(entries) == 1
    assert entries[0]["start"] == 1.0
    assert entries[0]["end"] == 3.0


@pytest.mark.asyncio
async def test_rerender_rejects_invalid_inputs_before_rendering(
    edit_boundary, monkeypatch, tmp_path
):
    service, _clip_repo = _service(tmp_path, monkeypatch)

    with pytest.raises(ValueError):
        await service.rerender_clip("task-1", "clip-1", "cooking")
    with pytest.raises(ValueError):
        await service.rerender_clip(
            "task-1", "clip-1", "gaming", caption_template="no-such-template"
        )
    with pytest.raises(ValueError):
        await service.rerender_clip(
            "task-1", "clip-1", "gaming", transition="spin-zoom-9000"
        )
    assert service.video_service.render_call is None


@pytest.mark.asyncio
async def test_rerender_missing_clip_raises_not_found(
    edit_boundary, monkeypatch, tmp_path
):
    service, _clip_repo = _service(tmp_path, monkeypatch, clip=False)

    with pytest.raises(ValueError, match="Clip not found"):
        await service.rerender_clip("task-1", "clip-1", "gaming")


@pytest.mark.asyncio
async def test_rerender_accepts_manual_only_niche_when_available(
    edit_boundary, monkeypatch, tmp_path
):
    if "football_edit" not in NICHE_VALUES:
        pytest.skip("manual-only niches are not in this tree's niche set")
    service, clip_repo = _service(tmp_path, monkeypatch)

    refreshed = await service.rerender_clip("task-1", "clip-1", "football_edit")

    assert clip_repo.niche_updates == ["football_edit"]
    assert refreshed["niche_profile"] == "football_edit"


# --- Route tests -----------------------------------------------------------


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
async def test_rerender_route_rejects_invalid_niche_with_422(
    monkeypatch, fake_queue
):
    _stub_route_service(monkeypatch, {"id": "clip-1", "task_id": "task-1"})
    queue, _redis = fake_queue

    with pytest.raises(HTTPException) as error:
        await task_routes.rerender_clip(
            "task-1",
            "clip-1",
            _request({"niche_override": "cooking"}, queue),
            db=None,
        )

    assert error.value.status_code == 422
    assert "niche_override" in error.value.detail
    queue.enqueue_job.assert_not_called()


@pytest.mark.asyncio
async def test_rerender_route_missing_clip_is_404(monkeypatch, fake_queue):
    _stub_route_service(monkeypatch, None)
    queue, _redis = fake_queue

    with pytest.raises(HTTPException) as error:
        await task_routes.rerender_clip(
            "task-1",
            "clip-1",
            _request({"niche_override": "gaming"}, queue),
            db=None,
        )

    assert error.value.status_code == 404
    queue.enqueue_job.assert_not_called()


@pytest.mark.asyncio
async def test_rerender_route_queues_worker_job(monkeypatch, fake_queue):
    _stub_route_service(monkeypatch, {"id": "clip-1", "task_id": "task-1"})
    queue, redis = fake_queue

    result = await task_routes.rerender_clip(
        "task-1",
        "clip-1",
        _request({"niche_override": "travel_vlog"}, queue),
        db=None,
    )

    assert result["status"] == "queued"
    assert result["niche_override"] == "travel_vlog"
    assert result["job_id"].startswith("clip-rerender:clip-1:")
    args, kwargs = queue.enqueue_job.call_args
    assert args[0] == "rerender_clip_task"
    assert args[1:4] == ("task-1", "clip-1", "travel_vlog")
    assert kwargs["_job_id"] == result["job_id"]
    stored = json.loads(redis.store[f"clip_rerender:{result['job_id']}"])
    assert stored["status"] == "queued"


@pytest.mark.asyncio
async def test_rerender_status_route_returns_recorded_outcome(
    monkeypatch, fake_queue
):
    from src.workers.job_queue import set_rerender_outcome

    _stub_route_service(monkeypatch, {"id": "clip-1", "task_id": "task-1"})
    queue, redis = fake_queue
    job_id = "clip-rerender:clip-1:abc123"
    await set_rerender_outcome(
        redis,
        job_id,
        {"status": "completed", "clip_id": "clip-1", "clip": {"id": "clip-1"}},
    )

    result = await task_routes.get_clip_rerender_status(
        "task-1", "clip-1", job_id, _request({}, queue), db=None
    )

    assert result["status"] == "completed"
    assert result["clip"] == {"id": "clip-1"}

    with pytest.raises(HTTPException) as error:
        await task_routes.get_clip_rerender_status(
            "task-1", "clip-1", "clip-rerender:other:abc123", _request({}, queue),
            db=None,
        )
    assert error.value.status_code == 404


# --- Worker job tests ------------------------------------------------------


def _stub_worker(monkeypatch, rerender):
    class StubTaskService:
        def __init__(self, _db):
            pass

        async def rerender_clip(self, *args, **kwargs):
            result = rerender(*args, **kwargs)
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
async def test_worker_rerender_records_completed_outcome(monkeypatch):
    _stub_worker(monkeypatch, AsyncMock(return_value={"id": "clip-1"}))
    redis = FakeRedis()
    ctx = {"redis": redis, "job_id": "clip-rerender:clip-1:abc123"}

    outcome = await worker_tasks.rerender_clip_task(
        ctx, "task-1", "clip-1", "travel_vlog"
    )

    assert outcome["status"] == "completed"
    assert outcome["clip"] == {"id": "clip-1"}
    stored = json.loads(redis.store["clip_rerender:clip-rerender:clip-1:abc123"])
    assert stored["status"] == "completed"


@pytest.mark.asyncio
async def test_worker_rerender_records_failure_without_raising(monkeypatch):
    _stub_worker(
        monkeypatch, AsyncMock(side_effect=RuntimeError("source unavailable"))
    )
    redis = FakeRedis()
    ctx = {"redis": redis, "job_id": "clip-rerender:clip-1:abc123"}

    outcome = await worker_tasks.rerender_clip_task(
        ctx, "task-1", "clip-1", "travel_vlog"
    )

    assert outcome["status"] == "failed"
    assert "source unavailable" in outcome["error"]
    stored = json.loads(redis.store["clip_rerender:clip-rerender:clip-1:abc123"])
    assert stored["status"] == "failed"


# ---------------------------------------------------------------------------
# Stored template/music modes on the re-render seam: re-validate-and-warn
# (settings-integrity audit finding F-2). The stored values feed
# _build_rerender_edl positionally: (clip, start, end, niche, transition,
# segment_plan, caption_overrides, music_mode, template_mode,
# broll_enabled) — indexes 7 and 8 below.
# ---------------------------------------------------------------------------


def _spy_rerender_edl(service, monkeypatch):
    seen = {}
    original = service._build_rerender_edl

    def spy(*args, **kwargs):
        seen["args"] = args
        seen["kwargs"] = kwargs
        return original(*args, **kwargs)

    monkeypatch.setattr(service, "_build_rerender_edl", spy)
    return seen


@pytest.mark.asyncio
async def test_rerender_warns_and_defaults_on_invalid_stored_modes(
    edit_boundary, monkeypatch, tmp_path, caplog
):
    service, _clip_repo = _service(
        tmp_path,
        monkeypatch,
        task_fields={"template_mode": "bogus", "music_mode": "bogus"},
    )
    seen = _spy_rerender_edl(service, monkeypatch)

    with caplog.at_level(logging.WARNING, logger="src.services.clip_service"):
        await service.rerender_clip("task-1", "clip-1", "travel_vlog")

    # The builder received the documented defaults (None = Auto
    # template / Auto music), never the corrupt strings.
    assert seen["args"][8] is None
    assert seen["args"][7] is None
    messages = [record.getMessage() for record in caplog.records]
    assert any(
        "Ignoring invalid stored template_mode 'bogus' on task task-1" in m
        for m in messages
    )
    assert any(
        "Ignoring invalid stored music_mode 'bogus' on task task-1" in m
        for m in messages
    )
    # And the render still happened, with the Auto-shaped EDL.
    render = service.video_service.render_call
    assert render is not None
    assert render["edl"].get("template") != "plain"


@pytest.mark.asyncio
async def test_rerender_forwards_valid_stored_modes_unchanged(
    edit_boundary, monkeypatch, tmp_path, caplog
):
    service, _clip_repo = _service(
        tmp_path,
        monkeypatch,
        task_fields={"template_mode": "plain", "music_mode": "off"},
    )
    seen = _spy_rerender_edl(service, monkeypatch)

    with caplog.at_level(logging.WARNING, logger="src.services.clip_service"):
        await service.rerender_clip("task-1", "clip-1", "travel_vlog")

    assert seen["args"][8] == "plain"
    assert seen["args"][7] == "off"
    assert not any(
        "Ignoring invalid stored" in record.getMessage()
        for record in caplog.records
    )
    render = service.video_service.render_call
    assert render is not None
    assert render["edl"].get("template") == "plain"
    assert render["edl"]["audio"].get("music_source") == "user_none"
