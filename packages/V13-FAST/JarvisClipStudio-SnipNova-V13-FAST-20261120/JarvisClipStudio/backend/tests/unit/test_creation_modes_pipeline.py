"""Pipeline-decision tests for the four creation modes (tracker 1.9).

Two layers, both synthetic (faked download/transcription/analysis —
labelled as such; no real video is processed):

1. VideoService.process_video_complete: the same analysis result fed
   through different modes must produce *different pipeline decisions*
   — Full Auto renders a slate, Find-Moments modes park with
   candidates, Manual Cutter never calls the analysis at all and
   renders the user's ranges.
2. TaskService.process_task: the mode stored on the task row drives
   the parking behaviour and the ai_editing flag handed to the
   renderer, and a task with no mode behaves exactly as before.
"""

from contextlib import asynccontextmanager, nullcontext
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from src.config import Config
from src.services import task_service as task_service_module
from src.services import video_service as video_service_module
from src.services.task_service import TaskService
from src.services.video_service import VideoService


ANALYSIS_SEGMENTS = [
    {
        "start_time": "00:10",
        "end_time": "00:40",
        "text": "First moment",
        "relevance_score": 0.9,
        "reasoning": "Strong",
        "virality": {"total_score": 90},
    },
    {
        "start_time": "01:10",
        "end_time": "01:40",
        "text": "Second moment",
        "relevance_score": 0.8,
        "reasoning": "Good",
        "virality": {"total_score": 80},
    },
    {
        "start_time": "02:10",
        "end_time": "02:40",
        "text": "Third moment",
        "relevance_score": 0.7,
        "reasoning": "Okay",
        "virality": {"total_score": 70},
    },
]


def _stub_analysis():
    return SimpleNamespace(
        summary="A summary",
        key_topics=["topic"],
        most_relevant_segments=[dict(segment) for segment in ANALYSIS_SEGMENTS],
    )


@pytest.fixture()
def pipeline(monkeypatch, tmp_path):
    """process_video_complete with every heavy stage faked."""
    source = tmp_path / "source.mp4"
    source.write_bytes(b"not a real video")

    monkeypatch.setattr(
        VideoService, "resolve_local_video_path", staticmethod(lambda _url: source)
    )
    monkeypatch.setattr(
        VideoService,
        "prepare_source",
        AsyncMock(side_effect=lambda path: path),
    )
    monkeypatch.setattr(
        VideoService, "_get_file_duration", staticmethod(lambda _path: 600.0)
    )
    monkeypatch.setattr(
        VideoService,
        "generate_transcript",
        AsyncMock(return_value="word " * 200),
    )
    monkeypatch.setattr(
        VideoService,
        "analyze_transcript",
        AsyncMock(side_effect=lambda *a, **k: _stub_analysis()),
    )
    monkeypatch.setattr(
        VideoService,
        "_drop_static_segments",
        staticmethod(lambda _path, segments: segments),
    )
    monkeypatch.setattr(video_service_module, "probe_video_codec", lambda _p: None)
    monkeypatch.setattr(
        video_service_module, "persist_cache_sidecar", lambda _p: None
    )
    monkeypatch.setattr(
        video_service_module, "build_clip_signal_summary", lambda *a: None
    )
    return source


async def _run_pipeline(**kwargs):
    return await VideoService.process_video_complete(
        url="upload://local/source.mp4",
        source_type="upload",
        task_id="task-modes",
        checkpoint={},
        checkpoint_callback=AsyncMock(),
        progress_callback=AsyncMock(),
        **kwargs,
    )


@pytest.mark.asyncio
async def test_full_auto_renders_the_ranked_slate(pipeline):
    result = await _run_pipeline(creation_mode="full_auto")

    assert not result.get("awaiting_selection")
    assert len(result["segments_to_render"]) == len(ANALYSIS_SEGMENTS)
    VideoService.analyze_transcript.assert_awaited_once()


@pytest.mark.asyncio
async def test_no_mode_behaves_like_full_auto(pipeline):
    result = await _run_pipeline()

    assert not result.get("awaiting_selection")
    assert len(result["segments_to_render"]) == len(ANALYSIS_SEGMENTS)


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["moments_manual", "moments_ai"])
async def test_find_moments_modes_park_with_candidates(pipeline, mode):
    result = await _run_pipeline(creation_mode=mode)

    assert result["awaiting_selection"] is True
    assert result["segments_to_render"] == []
    assert len(result["candidates"]) == len(ANALYSIS_SEGMENTS)
    # Selection still ran — finding moments is the AI's job here.
    VideoService.analyze_transcript.assert_awaited_once()


@pytest.mark.asyncio
async def test_manual_cutter_never_runs_analysis(pipeline):
    result = await _run_pipeline(
        creation_mode="manual_cutter_ai",
        manual_ranges=[{"start": 83.0, "end": 112.0}, {"start": 200.0, "end": 260.0}],
    )

    VideoService.analyze_transcript.assert_not_awaited()
    # Transcription still ran: captions need the words.
    VideoService.generate_transcript.assert_awaited()
    assert not result.get("awaiting_selection")
    segments = result["segments_to_render"]
    assert [(s["start_time"], s["end_time"]) for s in segments] == [
        ("01:23", "01:52"),
        ("03:20", "04:20"),
    ]
    assert all(s["user_selected"] for s in segments)


@pytest.mark.asyncio
async def test_same_input_three_modes_three_decisions(pipeline):
    """The cross-mode proof: identical source + analysis, different outcomes."""
    full = await _run_pipeline(creation_mode="full_auto")
    parked = await _run_pipeline(creation_mode="moments_ai")
    cutter = await _run_pipeline(
        creation_mode="manual_cutter_ai",
        manual_ranges=[{"start": 83.0, "end": 112.0}],
    )

    assert len(full["segments_to_render"]) == 3 and not full.get("awaiting_selection")
    assert parked["segments_to_render"] == [] and parked["awaiting_selection"]
    assert [s["start_time"] for s in cutter["segments_to_render"]] == ["01:23"]


# ---------------------------------------------------------------------------
# TaskService layer: stored mode drives parking + the treatment switch
# ---------------------------------------------------------------------------


def build_clip_result() -> dict:
    return {
        "filename": "clip-1.mp4",
        "path": "/tmp/clip-1.mp4",
        "start_time": "00:00",
        "end_time": "00:10",
        "duration": 10.0,
        "text": "Hook text",
        "relevance_score": 0.95,
        "reasoning": "Strong hook",
    }


def build_mode_task_service(task_row: dict) -> TaskService:
    config = Config()
    config.app_base_url = "http://localhost:3107"
    config.aws_region = "us-east-1"
    config.aws_access_key_id = "AKIATEST"
    config.aws_secret_access_key = "secret-test"
    config.ses_from_email = "SupoClip <noreply@example.com>"
    db = AsyncMock()
    # A real AsyncSession's ``info`` is a plain dict; a bare AsyncMock
    # turns ``db.info.get(...)`` in the repositories (edit_transaction)
    # into a coroutine that production code correctly never awaits.
    db.info = {}
    service = TaskService(db=db, config=config)
    service._load_performance_context = AsyncMock(return_value=None)
    service._processing_transaction = lambda _task_id: nullcontext()
    service._task_run_guard = lambda _task_id: nullcontext()
    service.task_repo.get_task_by_id = AsyncMock(
        return_value={"status": "queued", **task_row}
    )
    # The real load_source_metadata reads its SELECT result
    # synchronously; run against a mock session that spawns a mock
    # coroutine production never awaits (RuntimeWarning at the call
    # site). Stub it like every other repository read in this builder.
    service.task_repo.load_source_metadata = AsyncMock(return_value={})
    service.cache_repo.get_cache = AsyncMock(return_value=None)
    service.cache_repo.upsert_cache = AsyncMock()
    service.checkpoint_repo.get = AsyncMock(return_value={})
    service.checkpoint_repo.save = AsyncMock()
    service.task_repo.update_task_runtime_metadata = AsyncMock()
    service.task_repo.update_task_status = AsyncMock(return_value=True)
    service.task_repo.update_task_clips = AsyncMock()
    service.task_repo.get_task_notification_context = AsyncMock(return_value=None)
    service.clip_repo.get_clips_by_task = AsyncMock(return_value=[])
    service.clip_repo.create_clip = AsyncMock(return_value="clip-1")
    service._qc_persisted_clip = AsyncMock()
    service.video_service.create_single_clip = AsyncMock(
        return_value=build_clip_result()
    )
    service.video_service.process_video_complete = AsyncMock(
        return_value={
            "clips": [],
            "segments_to_render": [{"start_time": "00:00", "end_time": "00:10"}],
            "video_path": "/tmp/source.mp4",
            "segments": [],
            "summary": None,
            "key_topics": [],
            "transcript": "Transcript",
            "analysis_json": "{}",
            "stage_timings": {},
        }
    )
    return service


async def _run_task(service: TaskService):
    return await service.process_task(
        task_id="task-1",
        url="https://www.youtube.com/watch?v=demo",
        source_type="youtube",
    )


@pytest.mark.asyncio
async def test_task_without_mode_renders_with_ai_editing():
    service = build_mode_task_service({})

    await _run_task(service)

    kwargs = service.video_service.process_video_complete.await_args.kwargs
    assert kwargs["creation_mode"] is None
    clip_kwargs = service.video_service.create_single_clip.await_args.kwargs
    assert clip_kwargs["ai_editing"] is True


@pytest.mark.asyncio
async def test_task_full_auto_passes_mode_and_ai_editing():
    service = build_mode_task_service({"creation_mode": "full_auto"})

    await _run_task(service)

    kwargs = service.video_service.process_video_complete.await_args.kwargs
    assert kwargs["creation_mode"] == "full_auto"
    clip_kwargs = service.video_service.create_single_clip.await_args.kwargs
    assert clip_kwargs["ai_editing"] is True


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["moments_manual", "moments_ai"])
async def test_find_moments_task_parks_and_renders_nothing(mode):
    service = build_mode_task_service({"creation_mode": mode})
    service.video_service.process_video_complete = AsyncMock(
        return_value={
            "awaiting_selection": True,
            "candidates": [dict(s) for s in ANALYSIS_SEGMENTS],
            "segments": [],
            "segments_to_render": [],
            "video_path": "/tmp/source.mp4",
            "summary": "s",
            "key_topics": [],
            "transcript": "Transcript",
            "analysis_json": None,
            "stage_timings": {},
        }
    )

    result = await _run_task(service)

    assert result["status"] == "awaiting_selection"
    assert result["candidates_count"] == len(ANALYSIS_SEGMENTS)
    service.video_service.create_single_clip.assert_not_awaited()
    service.clip_repo.create_clip.assert_not_awaited()
    status_calls = [
        call.kwargs.get("progress_message", "")
        for call in service.task_repo.update_task_status.await_args_list
    ]
    assert any("pick the ones to render" in message for message in status_calls)


@pytest.mark.asyncio
async def test_moments_manual_resumed_run_renders_with_ai_editing():
    """Second run after the pick: segments render with treatment ON.

    F-R1: the pick is a final plan and renders with the same
    pipeline treatment as moments_ai, so the task's template/music
    choices reach the render (the EDL build produces the reframe
    plan and the music chain). Plain vs Auto is covered by the
    pick_renders tests above.
    """
    service = build_mode_task_service({"creation_mode": "moments_manual"})

    await _run_task(service)

    clip_kwargs = service.video_service.create_single_clip.await_args.kwargs
    assert clip_kwargs["ai_editing"] is True


@pytest.mark.asyncio
async def test_moments_manual_pick_renders_plain_template(monkeypatch):
    """F-R1: a picked moments_manual render honours Plain + Music Off.

    The pick goes through the shared pick path
    (``apply_moment_selection``), then the resumed run renders the
    pick. The render job must carry the Plain template
    (``template_mode="plain"``, ``music_mode="off"``) AND
    ``ai_editing=True``: that flag is the gate for the pipeline EDL
    build that produces the reframe plan (the full-bleed tight crop
    for Plain) and the music chain. On the pre-fix code the template
    values already arrive but ``ai_editing`` is False, so no EDL is
    built, no reframe plan exists and the export renders the wide
    16:9 strip with blurred bands — the F-R1 defect.
    """
    service, _enqueued = _pick_and_render_service(
        monkeypatch,
        {
            "id": "task-1",
            "creation_mode": "moments_manual",
            "template_mode": "plain",
            "music_mode": "off",
            "status": "awaiting_selection",
            "user_id": "user-1",
            "source_url": "https://www.youtube.com/watch?v=demo",
            "source_type": "youtube",
        },
    )

    pick = await service.apply_moment_selection("task-1", [0])

    assert pick == {"job_id": "job-1", "selected_count": 1}
    await _run_task(service)

    clip_kwargs = service.video_service.create_single_clip.await_args.kwargs
    assert clip_kwargs["ai_editing"] is True
    assert clip_kwargs["template_mode"] == "plain"
    assert clip_kwargs["music_mode"] == "off"


@pytest.mark.asyncio
async def test_moments_manual_pick_renders_auto_template(monkeypatch):
    """F-R1 twin: the same pick with Auto settings renders Auto.

    The Plain and Auto twins must differ in the render job: same
    mode, same pick, opposite template/music choices. (On the
    pre-fix code both twins rendered the identical neutral file.)
    """
    service, _enqueued = _pick_and_render_service(
        monkeypatch,
        {
            "id": "task-1",
            "creation_mode": "moments_manual",
            "template_mode": "auto",
            "music_mode": "on",
            "status": "awaiting_selection",
            "user_id": "user-1",
            "source_url": "https://www.youtube.com/watch?v=demo",
            "source_type": "youtube",
        },
    )

    pick = await service.apply_moment_selection("task-1", [0])

    assert pick == {"job_id": "job-1", "selected_count": 1}
    await _run_task(service)

    clip_kwargs = service.video_service.create_single_clip.await_args.kwargs
    assert clip_kwargs["ai_editing"] is True
    assert clip_kwargs["template_mode"] == "auto"
    assert clip_kwargs["music_mode"] == "on"


@pytest.mark.asyncio
async def test_moments_ai_pick_renders_plain_template_unchanged(monkeypatch):
    """F-R1 pin: the moments_ai pick render is unchanged by the fix."""
    service, _enqueued = _pick_and_render_service(
        monkeypatch,
        {
            "id": "task-1",
            "creation_mode": "moments_ai",
            "template_mode": "plain",
            "music_mode": "off",
            "status": "awaiting_selection",
            "user_id": "user-1",
            "source_url": "https://www.youtube.com/watch?v=demo",
            "source_type": "youtube",
        },
    )

    pick = await service.apply_moment_selection("task-1", [0])

    assert pick == {"job_id": "job-1", "selected_count": 1}
    await _run_task(service)

    clip_kwargs = service.video_service.create_single_clip.await_args.kwargs
    assert clip_kwargs["ai_editing"] is True
    assert clip_kwargs["template_mode"] == "plain"
    assert clip_kwargs["music_mode"] == "off"


def _pick_and_render_service(monkeypatch, task_row):
    """A parked Find-Moments task, ready for pick then resumed render.

    Merges the pick-path fakes (repos, guards, billing, queue) with
    the render-leg fakes from :func:`build_mode_task_service`: the
    checkpoint holds the candidate slate the pick reads, the
    pipeline stub returns the picked segment to render, and the
    render call is recorded. Returns ``(service, enqueued_calls)``.
    """
    service = build_mode_task_service(task_row)
    service.checkpoint_repo.get = AsyncMock(
        return_value={"candidates": [dict(s) for s in ANALYSIS_SEGMENTS]}
    )
    service.task_repo.load_source_metadata = AsyncMock(
        return_value={"clip_length": "auto"}
    )
    service.task_repo.save_source_metadata = AsyncMock()

    enqueued = []

    async def _enqueue(*args, **kwargs):
        enqueued.append((args, kwargs))
        return "job-1"

    @asynccontextmanager
    async def run_guard(_db, _task_id):
        yield

    @asynccontextmanager
    async def allowance_guard(_db, _user_id, enabled=True):
        yield

    monkeypatch.setattr(task_service_module, "task_run_guard", run_guard)
    monkeypatch.setattr(
        task_service_module, "generation_allowance_guard", allowance_guard
    )
    monkeypatch.setattr(
        task_service_module,
        "BillingService",
        lambda *args, **kwargs: SimpleNamespace(
            assert_can_resume_task=AsyncMock(return_value={})
        ),
    )
    monkeypatch.setattr(
        task_service_module,
        "JobQueue",
        SimpleNamespace(
            enqueue_processing_job=AsyncMock(side_effect=_enqueue)
        ),
    )
    monkeypatch.setattr(
        task_service_module, "_clear_task_cancel_flag", AsyncMock()
    )
    monkeypatch.setattr(
        task_service_module, "_mirror_task_source_metadata", AsyncMock()
    )
    return service, enqueued


@pytest.mark.asyncio
async def test_moments_ai_resumed_run_renders_with_ai_editing():
    service = build_mode_task_service({"creation_mode": "moments_ai"})

    await _run_task(service)

    clip_kwargs = service.video_service.create_single_clip.await_args.kwargs
    assert clip_kwargs["ai_editing"] is True


@pytest.mark.asyncio
async def test_manual_cutter_task_passes_validated_ranges_to_pipeline():
    service = build_mode_task_service(
        {
            "creation_mode": "manual_cutter_ai",
            "manual_ranges_json": '[{"start": 90, "end": 120}, {"start": 10, "end": 25}]',
        }
    )

    await _run_task(service)

    kwargs = service.video_service.process_video_complete.await_args.kwargs
    assert kwargs["creation_mode"] == "manual_cutter_ai"
    # Stored ranges are re-validated and normalised (sorted) en route.
    assert kwargs["manual_ranges"] == [
        {"start": 10.0, "end": 25.0},
        {"start": 90.0, "end": 120.0},
    ]


@pytest.mark.asyncio
async def test_invalid_stored_mode_falls_back_to_legacy_pipeline():
    service = build_mode_task_service({"creation_mode": "ludicrous_speed"})

    await _run_task(service)

    kwargs = service.video_service.process_video_complete.await_args.kwargs
    assert kwargs["creation_mode"] is None
    clip_kwargs = service.video_service.create_single_clip.await_args.kwargs
    assert clip_kwargs["ai_editing"] is True
