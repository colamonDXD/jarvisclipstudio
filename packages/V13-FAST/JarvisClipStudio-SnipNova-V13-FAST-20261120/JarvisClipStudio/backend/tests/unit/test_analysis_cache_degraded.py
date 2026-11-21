"""F-R2: the degraded stand-in is persisted to the shared processing_cache.

REAL_EXPORT_ACCEPTANCE_RERUN.md §5: when AI analysis fails, the task
correctly degrades to the deterministic visual floor and the task
checkpoint correctly contains NO ``analysis`` key — but the degraded
stand-in IS written to the shared ``processing_cache`` analysis_json
(summary: "AI analysis was unavailable, so these clips were picked by
the deterministic audio/visual fallback..."). On a real install, the
NEXT run of the same video with providers healthy would be served the
floor slate as its "analysis" and never call the AI. Cache readers
consume the cache as analysis.

The fix has two layers, both covered here, following the F-1
precedent (ANALYSIS_CACHE_MODES_FIX.md):

1. Write-path suppression: a degraded run never writes its stand-in
   into any ``processing_cache`` analysis row — the transcript and
   video path still cache, the analysis write is skipped and logged.
2. Read-path guard: a cached analysis that IS the degraded stand-in
   (rows poisoned before the fix) is discarded as a miss, so the
   pipeline runs a fresh AI analysis instead of serving the floor
   slate. The poison heals when that healthy run writes its real
   analysis over the row.

These tests are the fail-first repro: the task-service tests FAIL on
the base tip (the stand-in is served/written) and PASS after the fix.
"""

import json
from contextlib import nullcontext
from unittest.mock import AsyncMock

import pytest

from src.auto_director.creation_modes import SELECTION_AI_RANKED
from src.config import Config
from src.services import task_service as task_service_module
from src.services.task_service import TaskService


URL = "https://www.youtube.com/watch?v=demo"

# The exact stand-in summary the pipeline writes when every analysis
# provider fails (video_service.process_video_complete), and the exact
# floor-slate segments from the acceptance re-run (Japan source,
# deterministic): 02:29-02:59, 14:56-15:26, ...
DEGRADED_SUMMARY = (
    "AI analysis was unavailable, so these clips were picked by the "
    "deterministic audio/visual fallback instead of the AI."
)

FLOOR_SEGMENTS = [
    {
        "start_time": "02:29",
        "end_time": "02:59",
        "text": "",
        "relevance_score": 0.95,
        "reasoning": (
            "Visual selection (AI analysis unavailable): audio energy "
            "peak at 02:57 (-1.1 dB, standing 12.2 dB above its "
            "surroundings); window 02:29-02:59."
        ),
    },
    {
        "start_time": "14:56",
        "end_time": "15:26",
        "text": "",
        "relevance_score": 0.9,
        "reasoning": (
            "Visual selection (AI analysis unavailable): audio energy "
            "peak at 15:11 (-3.4 dB, standing 9.8 dB above its "
            "surroundings); window 14:56-15:26."
        ),
    },
]

AI_SEGMENT = {
    "start_time": "00:10",
    "end_time": "00:40",
    "text": "The AI picked this moment.",
    "relevance_score": 0.9,
    "reasoning": "A strong standalone insight.",
}


def degraded_analysis_json() -> str:
    """The exact payload a degraded run returns — and, before the fix,
    writes into the shared ``processing_cache`` row."""
    return json.dumps(
        {
            "summary": DEGRADED_SUMMARY,
            "key_topics": [],
            "selection_strategy": SELECTION_AI_RANKED,
            "most_relevant_segments": [dict(s) for s in FLOOR_SEGMENTS],
        }
    )


def ai_analysis_json() -> str:
    return json.dumps(
        {
            "summary": "AI summary",
            "key_topics": ["topic"],
            "selection_strategy": SELECTION_AI_RANKED,
            "most_relevant_segments": [dict(AI_SEGMENT)],
        }
    )


# ---------------------------------------------------------------------------
# Layer 1: the detector
# ---------------------------------------------------------------------------


def test_cached_analysis_is_degraded():
    # Imported here so the failure on the base tip reads as a missing
    # guard, not a collection error for the whole file.
    from src.services.task_service import cached_analysis_is_degraded

    # The stand-in, exactly as the acceptance re-run found it in the cache.
    assert cached_analysis_is_degraded(degraded_analysis_json()) is True
    # Either leg alone marks it: the summary, or a floor segment's
    # reasoning (a run whose visual selection failed over to the blind
    # fallback window still carries the stand-in summary).
    summary_only = json.dumps(
        {"summary": DEGRADED_SUMMARY, "most_relevant_segments": []}
    )
    assert cached_analysis_is_degraded(summary_only) is True
    reasoning_only = json.dumps(
        {
            "summary": "Some other summary",
            "most_relevant_segments": [dict(FLOOR_SEGMENTS[0])],
        }
    )
    assert cached_analysis_is_degraded(reasoning_only) is True
    # A real AI analysis is not degraded.
    assert cached_analysis_is_degraded(ai_analysis_json()) is False
    # The transcript-less floor is by design, not a degradation: its
    # segments carry a different reason context and its summary is a
    # real AI product, so it must not be mistaken for the stand-in.
    transcriptless_floor = json.dumps(
        {
            "summary": "AI summary",
            "most_relevant_segments": [
                {
                    "start_time": "00:00",
                    "end_time": "00:30",
                    "reasoning": (
                        "Visual selection (no usable transcript): "
                        "audio energy peak at 00:12; window 00:00-00:30."
                    ),
                }
            ],
        }
    )
    assert cached_analysis_is_degraded(transcriptless_floor) is False
    # Manual ranges are a different payload class (F-1's guard owns them).
    manual = json.dumps(
        {
            "summary": None,
            "most_relevant_segments": [
                {
                    "start_time": "22:04",
                    "end_time": "22:29",
                    "reasoning": "User-selected range (Manual Cutter).",
                }
            ],
        }
    )
    assert cached_analysis_is_degraded(manual) is False
    assert cached_analysis_is_degraded("not json") is False
    assert cached_analysis_is_degraded("") is False
    assert cached_analysis_is_degraded(None) is False


# ---------------------------------------------------------------------------
# Task service: the cache read/write boundary
# ---------------------------------------------------------------------------


def _build_task_service():
    config = Config()
    config.app_base_url = "http://localhost:3107"
    config.aws_region = "us-east-1"
    config.aws_access_key_id = "AKIATEST"
    config.aws_secret_access_key = "secret-test"
    config.ses_from_email = "SupoClip <noreply@example.com>"
    service = TaskService(db=AsyncMock(), config=config)
    service._load_performance_context = AsyncMock(return_value=None)
    service._processing_transaction = lambda _task_id: nullcontext()
    service._task_run_guard = lambda _task_id: nullcontext()
    service.cache_repo.get_cache = AsyncMock(return_value=None)
    service.cache_repo.upsert_cache = AsyncMock()
    # A fresh dict per call: the return_value={} form hands every call
    # the SAME dict, so call 1's completed_positions checkpoint would
    # leak into call 2 and make it skip its only segment as "done".
    service.checkpoint_repo.get = AsyncMock(side_effect=lambda *args, **kwargs: {})
    service.checkpoint_repo.save = AsyncMock()
    service.task_repo.update_task_runtime_metadata = AsyncMock()
    service.task_repo.update_task_status = AsyncMock()
    service.task_repo.update_task_clips = AsyncMock()
    service.task_repo.get_task_notification_context = AsyncMock(return_value=None)
    service.clip_repo.delete_clips_by_task = AsyncMock(return_value=0)
    service.clip_repo.get_clips_by_task = AsyncMock(return_value=[])
    service.clip_repo.create_clip = AsyncMock(return_value="clip-1")
    service.video_service.create_single_clip = AsyncMock(
        return_value={
            "filename": "clip-1.mp4",
            "path": "/tmp/clip-1.mp4",
            "start_time": "00:00",
            "end_time": "00:10",
            "duration": 10.0,
            "text": "Hook text",
            "relevance_score": 0.95,
            "reasoning": "Strong hook",
        }
    )
    service.video_service.apply_single_transition = AsyncMock(
        side_effect=lambda _prev_clip_path, clip_info, _index, _clips_output_dir: clip_info
    )
    service.video_service.process_video_complete = AsyncMock(
        return_value={
            "clips": [],
            "segments_to_render": [{"start": 0, "end": 10}],
            "video_path": "/tmp/source.mp4",
            "segments": [],
            "summary": None,
            "key_topics": [],
            "transcript": "Transcript",
            "analysis_json": "{}",
        }
    )
    return service


class _FakeBillingService:
    def __init__(self, *_args, **_kwargs):
        pass

    async def get_usage_summary(self, _user_id):
        return {"plan": "pro", "subscription_status": "active"}


@pytest.mark.asyncio
async def test_process_task_discards_cached_degraded_analysis(monkeypatch):
    """F-R2 repro (read): a shared row poisoned by a pre-fix degraded
    run must not reach the pipeline as cached_analysis_json — the
    next healthy run must call the AI, not serve the floor slate."""
    service = _build_task_service()
    service.task_repo.get_task_by_id = AsyncMock(
        return_value={"status": "queued", "creation_mode": "full_auto"}
    )
    # The shared source row still holds the degraded stand-in, exactly
    # as the acceptance re-run found it.
    service.cache_repo.get_cache = AsyncMock(
        return_value={"transcript_text": "Transcript", "analysis_json": degraded_analysis_json()}
    )
    monkeypatch.setattr(task_service_module, "BillingService", _FakeBillingService)

    await service.process_task(
        task_id="task-1",
        url=URL,
        source_type="youtube",
        user_id="user-1",
    )

    assert (
        service.video_service.process_video_complete.await_args.kwargs[
            "cached_analysis_json"
        ]
        is None
    )


@pytest.mark.asyncio
async def test_process_task_never_writes_degraded_standin_to_cache(monkeypatch):
    """F-R2 repro (write): a degraded run's stand-in must never land in
    any ``processing_cache`` analysis row — neither the shared source
    row nor a preset-derived analysis row."""
    service = _build_task_service()
    service.task_repo.get_task_by_id = AsyncMock(
        return_value={"status": "queued", "creation_mode": "full_auto"}
    )
    service.video_service.process_video_complete = AsyncMock(
        return_value={
            "clips": [],
            "segments_to_render": [{"start": 0, "end": 10}],
            "video_path": "/tmp/source.mp4",
            "segments": [],
            "summary": DEGRADED_SUMMARY,
            "key_topics": [],
            "transcript": "Transcript",
            "analysis_json": degraded_analysis_json(),
        }
    )
    monkeypatch.setattr(task_service_module, "BillingService", _FakeBillingService)

    # Auto length: the analysis shares the source row.
    await service.process_task(
        task_id="task-1",
        url=URL,
        source_type="youtube",
        user_id="user-1",
    )
    # Short length: the analysis goes to a preset-derived row.
    await service.process_task(
        task_id="task-2",
        url=URL,
        source_type="youtube",
        user_id="user-1",
        clip_length="short",
    )

    writes = [
        call.kwargs
        for call in service.cache_repo.upsert_cache.await_args_list
        if call.kwargs.get("analysis_json")
    ]
    assert not any(
        w["analysis_json"] == degraded_analysis_json() for w in writes
    ), "the degraded stand-in must never be written as a cached analysis"
    # The transcript still caches on the source row — only the
    # analysis write is suppressed, and it is suppressed as None.
    source_key = TaskService._build_cache_key(URL, "youtube", "fast")
    source_writes = [
        call.kwargs
        for call in service.cache_repo.upsert_cache.await_args_list
        if call.kwargs.get("cache_key") == source_key
    ]
    assert source_writes, "the source row must still be cached"
    assert all(w.get("analysis_json") is None for w in source_writes)
    assert any(w.get("transcript_text") == "Transcript" for w in source_writes)


@pytest.mark.asyncio
async def test_process_task_still_caches_healthy_analysis(monkeypatch):
    """Legitimate caching is preserved: a healthy AI analysis is still
    written to the shared row, and a preset-derived row when set."""
    service = _build_task_service()
    service.task_repo.get_task_by_id = AsyncMock(
        return_value={"status": "queued", "creation_mode": "full_auto"}
    )
    service.video_service.process_video_complete = AsyncMock(
        return_value={
            "clips": [],
            "segments_to_render": [{"start": 0, "end": 10}],
            "video_path": "/tmp/source.mp4",
            "segments": [],
            "summary": "AI summary",
            "key_topics": ["topic"],
            "transcript": "Transcript",
            "analysis_json": ai_analysis_json(),
        }
    )
    monkeypatch.setattr(task_service_module, "BillingService", _FakeBillingService)

    await service.process_task(
        task_id="task-1",
        url=URL,
        source_type="youtube",
        user_id="user-1",
    )
    await service.process_task(
        task_id="task-2",
        url=URL,
        source_type="youtube",
        user_id="user-1",
        clip_length="short",
    )

    source_key = TaskService._build_cache_key(URL, "youtube", "fast")
    analysis_key = TaskService._build_analysis_cache_key(source_key, "short")
    writes = [
        (call.kwargs["cache_key"], call.kwargs.get("analysis_json"))
        for call in service.cache_repo.upsert_cache.await_args_list
    ]
    assert (source_key, ai_analysis_json()) in writes
    assert (analysis_key, ai_analysis_json()) in writes
