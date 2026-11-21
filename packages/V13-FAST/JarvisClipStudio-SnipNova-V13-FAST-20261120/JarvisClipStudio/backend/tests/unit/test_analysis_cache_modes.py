"""F-1: the analysis cache is shared across creation modes.

REAL_EXPORT_ACCEPTANCE.md §3a: a Manual Cutter run wrote its
user-selected ranges into the shared ``processing_cache`` row for a
source, and a later Full AutoClip run on the same source finished
Step 3 in 0.2s by silently reusing those manual ranges as its "AI
selection" — no note, no fresh AI pick.

The fix has two layers, both covered here:

1. Key separation: a user-ranges selection derives its own analysis
   cache row, so a manual analysis can never be *written* into the
   row an AI mode reads. AI selections keep the historic row, so
   same-mode/same-source/same-settings reuse still hits.
2. Read-path guard: a cached analysis whose segments are
   user-selected is never *consumed* by an AI run — this also covers
   rows poisoned before the fix, which key separation alone cannot
   heal (a run that reuses the poison writes it straight back).

These tests are the fail-first repro: the video-service and
task-service tests FAIL on the base tip (the manual ranges are
served as the AI pick) and PASS after the fix.
"""

import hashlib
import json
from contextlib import nullcontext
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from src.auto_director.creation_modes import (
    SELECTION_AI_CANDIDATES,
    SELECTION_AI_RANKED,
    SELECTION_USER_RANGES,
    build_manual_segments,
)
from src.config import Config
from src.services import task_service as task_service_module
from src.services import video_service as video_service_module
from src.services.task_service import TaskService
from src.services.video_service import VideoService


@pytest.fixture(autouse=True)
def isolated_upload_lookup(monkeypatch):
    # Same as test_video_service.py: the stored-upload lookup must
    # never reach the network/DB in a unit test.
    monkeypatch.setattr(
        video_service_module,
        "materialize_stored_upload",
        AsyncMock(return_value=None),
    )
    monkeypatch.setattr(
        video_service_module,
        "upload_needs_remote_copy",
        AsyncMock(return_value=False),
    )


# The F-1 fixture ranges (REAL_EXPORT_ACCEPTANCE.md §3a): Japan source
# ranges r3 22:04-22:29 and r5 42:24-42:52, user-selected in Manual
# Cutter and then silently served as the Full AutoClip pick.
MANUAL_RANGES = [{"start": 1324.0, "end": 1349.0}, {"start": 2544.0, "end": 2572.0}]
URL = "https://www.youtube.com/watch?v=demo"

AI_SEGMENT = {
    "start_time": "00:10",
    "end_time": "00:40",
    "text": "The AI picked this moment.",
    "relevance_score": 0.9,
    "reasoning": "A strong standalone insight.",
}


def manual_analysis_json() -> str:
    """The exact payload a Manual Cutter run leaves in the cache."""
    return json.dumps(
        {
            "summary": None,
            "key_topics": [],
            "most_relevant_segments": build_manual_segments(MANUAL_RANGES),
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
# Layer 1: key separation
# ---------------------------------------------------------------------------


def test_analysis_cache_key_separates_manual_selection():
    base = "cache-key"
    # AI selections keep the historic row exactly.
    assert (
        TaskService._build_analysis_cache_key(base, selection=SELECTION_AI_RANKED)
        == base
    )
    assert (
        TaskService._build_analysis_cache_key(
            base, selection=SELECTION_AI_CANDIDATES
        )
        == base
    )
    assert TaskService._build_analysis_cache_key(base, selection=None) == base
    # A user-ranges selection derives its own row, so a manual
    # analysis can never be written into the row an AI mode reads.
    manual = TaskService._build_analysis_cache_key(
        base, selection=SELECTION_USER_RANGES
    )
    assert manual == hashlib.sha256(
        f"{base}|selection:{SELECTION_USER_RANGES}".encode("utf-8")
    ).hexdigest()
    assert manual != base
    # ... and it still composes with the existing derivations.
    assert TaskService._build_analysis_cache_key(
        base, "short", 30, selection=SELECTION_USER_RANGES
    ) == hashlib.sha256(
        f"{base}|length:short|clips:30|selection:{SELECTION_USER_RANGES}".encode(
            "utf-8"
        )
    ).hexdigest()


def test_cached_analysis_is_user_selected():
    # Imported here so the failure on the base tip reads as a missing
    # guard, not a collection error for the whole file.
    from src.auto_director.creation_modes import cached_analysis_is_user_selected

    assert cached_analysis_is_user_selected(manual_analysis_json()) is True
    stamped = json.dumps(
        {
            "selection_strategy": SELECTION_USER_RANGES,
            "most_relevant_segments": [],
        }
    )
    assert cached_analysis_is_user_selected(stamped) is True
    reasoning_only = json.dumps(
        {
            "most_relevant_segments": [
                {
                    "start_time": "00:00",
                    "end_time": "00:10",
                    "reasoning": "User-selected range (Manual Cutter).",
                }
            ]
        }
    )
    assert cached_analysis_is_user_selected(reasoning_only) is True
    assert cached_analysis_is_user_selected(ai_analysis_json()) is False
    plain = json.dumps(
        {
            "most_relevant_segments": [
                {
                    "start_time": "00:00",
                    "end_time": "00:10",
                    "reasoning": "A strong standalone insight.",
                }
            ]
        }
    )
    assert cached_analysis_is_user_selected(plain) is False
    assert cached_analysis_is_user_selected("not json") is False
    assert cached_analysis_is_user_selected("") is False


# ---------------------------------------------------------------------------
# Layer 2: read-path guard (video service)
# ---------------------------------------------------------------------------


class _AIAnalysis:
    summary = "AI summary"
    key_topics = ["topic"]
    most_relevant_segments = [dict(AI_SEGMENT)]


def _run_pipeline(monkeypatch, tmp_path, **kwargs):
    video_path = tmp_path / "source.mp4"
    video_path.write_bytes(b"placeholder")
    config = SimpleNamespace(
        max_video_duration=5400,
        clip_duration=30,
        max_clips=10,
        fast_mode_max_clips=4,
    )
    monkeypatch.setattr(video_service_module, "get_config", lambda: config)
    monkeypatch.setattr(
        VideoService,
        "resolve_local_video_path",
        staticmethod(lambda _url: video_path),
    )
    monkeypatch.setattr(
        VideoService,
        "_get_file_duration",
        staticmethod(lambda _path: 3000.0),
    )

    async def fake_generate_transcript(
        _video_path, processing_mode="balanced", source_url=None
    ):
        return "[00:00 - 00:01] hello"

    async def fake_run_in_thread(_func, *_args, **_kwargs):
        if _func == VideoService._drop_static_segments:
            return _args[1]
        return None

    monkeypatch.setattr(
        VideoService,
        "generate_transcript",
        staticmethod(fake_generate_transcript),
    )
    monkeypatch.setattr(video_service_module, "run_in_thread", fake_run_in_thread)
    return VideoService.process_video_complete(
        url="upload://source.mp4",
        source_type="video_url",
        processing_mode="fast",
        **kwargs,
    )


@pytest.mark.asyncio
async def test_full_auto_ignores_cached_manual_cutter_analysis(
    monkeypatch, tmp_path
):
    """F-1 repro: a manual analysis in the cache must not become the
    Full AutoClip pick. On the base tip the pipeline serves the manual
    ranges (Step 3 in 0.2s); after the fix it runs a fresh AI pick."""
    analysed = []

    async def fake_analyze_transcript(
        _transcript,
        clip_signals=None,
        performance_context=None,
        clip_length="auto",
        score_weights=None,
        **_kwargs,
    ):
        analysed.append(True)
        return _AIAnalysis()

    monkeypatch.setattr(
        VideoService, "analyze_transcript", staticmethod(fake_analyze_transcript)
    )
    result = await _run_pipeline(
        monkeypatch,
        tmp_path,
        creation_mode="full_auto",
        cached_analysis_json=manual_analysis_json(),
    )

    assert analysed, "a fresh AI analysis must run instead of reusing the manual ranges"
    rendered = result["segments_to_render"]
    assert [(s["start_time"], s["end_time"]) for s in rendered] == [
        (AI_SEGMENT["start_time"], AI_SEGMENT["end_time"])
    ]
    assert "22:04" not in json.dumps(rendered)
    assert "42:24" not in json.dumps(rendered)
    # The fresh analysis is stamped with its own provenance.
    assert json.loads(result["analysis_json"])["selection_strategy"] == (
        SELECTION_AI_RANKED
    )


@pytest.mark.asyncio
async def test_full_auto_still_reuses_cached_ai_analysis(monkeypatch, tmp_path):
    """Legitimate reuse is preserved: same mode + same source + same
    settings still hits the cache, and the AI is not re-run."""

    async def fake_analyze_transcript(*_args, **_kwargs):
        raise AssertionError("cached AI analysis must be reused, not re-run")

    monkeypatch.setattr(
        VideoService, "analyze_transcript", staticmethod(fake_analyze_transcript)
    )
    result = await _run_pipeline(
        monkeypatch,
        tmp_path,
        creation_mode="full_auto",
        cached_analysis_json=ai_analysis_json(),
    )

    rendered = result["segments_to_render"]
    assert [(s["start_time"], s["end_time"]) for s in rendered] == [
        (AI_SEGMENT["start_time"], AI_SEGMENT["end_time"])
    ]


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
    service.checkpoint_repo.get = AsyncMock(return_value={})
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
async def test_process_task_never_hands_manual_analysis_to_full_auto(monkeypatch):
    """A poisoned shared row (manual analysis written before the fix)
    must not reach the Full AutoClip pipeline as cached_analysis_json."""
    service = _build_task_service()
    service.task_repo.get_task_by_id = AsyncMock(
        return_value={"status": "queued", "creation_mode": "full_auto"}
    )
    # The shared source row still holds the manual analysis, exactly
    # as the acceptance lane found it.
    service.cache_repo.get_cache = AsyncMock(
        return_value={"transcript_text": "Transcript", "analysis_json": manual_analysis_json()}
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
async def test_process_task_writes_manual_analysis_to_its_own_row(monkeypatch):
    """A Manual Cutter run must not write its ranges into the shared
    source row an AI mode reads."""
    service = _build_task_service()
    service.task_repo.get_task_by_id = AsyncMock(
        return_value={
            "status": "queued",
            "creation_mode": "manual_cutter_ai",
            "manual_ranges_json": json.dumps(MANUAL_RANGES),
        }
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
            "analysis_json": manual_analysis_json(),
        }
    )
    monkeypatch.setattr(task_service_module, "BillingService", _FakeBillingService)

    await service.process_task(
        task_id="task-1",
        url=URL,
        source_type="youtube",
        user_id="user-1",
    )

    source_key = TaskService._build_cache_key(URL, "youtube", "fast")
    writes = [
        call.kwargs
        for call in service.cache_repo.upsert_cache.await_args_list
        if call.kwargs.get("analysis_json")
    ]
    assert writes, "the manual analysis must be cached somewhere"
    assert all(w["cache_key"] != source_key for w in writes), (
        "manual ranges must not land in the shared source row"
    )
    assert any(w["analysis_json"] == manual_analysis_json() for w in writes)
