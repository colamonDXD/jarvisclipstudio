"""Analysis-failure degradation: when every AI provider is down, a
Full AutoClip task must still complete with deterministic-floor
clips instead of dying at the analysis step (acceptance defect D-1's
pipeline half). The deliberate LocalProviderUnavailable configuration
error still propagates, and a brief still forbids padding.
"""

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from src import provider_manager
from src.auto_director import beat_grid as beat_grid_module
from src.auto_director import visual_moments as visual_moments_module
from src.services import video_service as video_service_module
from src.services.video_service import VideoService

VISUAL_SEGMENTS = [
    {
        "start_time": "01:00",
        "end_time": "01:30",
        "text": "",
        "relevance_score": 0.85,
        "reasoning": (
            "Visual selection (AI analysis unavailable): audio energy "
            "peak at 01:24 (standing above its surroundings); "
            "window 01:00–01:30."
        ),
        "virality_score": 85,
        "hook_score": 76,
        "engagement_score": 59,
        "value_score": 0,
        "shareability_score": 51,
        "hook_type": "visual_energy",
        "hook_title": None,
    }
]


@pytest.fixture()
def pipeline(monkeypatch, tmp_path):
    """process_video_complete with every heavy stage faked (mirrors
    test_creation_modes_pipeline's fixture)."""
    source = tmp_path / "source.mp4"
    source.write_bytes(b"not a real video")

    monkeypatch.setattr(
        VideoService, "resolve_local_video_path", staticmethod(lambda _url: source)
    )
    monkeypatch.setattr(
        VideoService, "prepare_source", AsyncMock(side_effect=lambda path: path)
    )
    monkeypatch.setattr(
        VideoService, "_get_file_duration", staticmethod(lambda _path: 600.0)
    )
    monkeypatch.setattr(
        VideoService, "generate_transcript", AsyncMock(return_value="word " * 200)
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
    monkeypatch.setattr(beat_grid_module, "analyse_audio_beats", lambda _p: None)
    return source


async def _run_pipeline(checkpoint_callback=None, **kwargs):
    return await VideoService.process_video_complete(
        url="upload://local/source.mp4",
        source_type="upload",
        task_id="task-degraded",
        checkpoint={},
        checkpoint_callback=checkpoint_callback or AsyncMock(),
        progress_callback=AsyncMock(),
        **kwargs,
    )


@pytest.mark.asyncio
async def test_analysis_failure_completes_with_visual_floor(monkeypatch, pipeline):
    monkeypatch.setattr(
        VideoService,
        "analyze_transcript",
        AsyncMock(
            side_effect=RuntimeError(
                "Transcript analysis failed: Invalid port: ':1]'"
            )
        ),
    )
    select_calls: list[dict] = []

    def fake_select(_path, _duration, **kwargs):
        select_calls.append(kwargs)
        return [dict(segment) for segment in VISUAL_SEGMENTS]

    monkeypatch.setattr(
        visual_moments_module, "select_visual_moments", fake_select
    )
    checkpoints = AsyncMock()

    result = await _run_pipeline(checkpoint_callback=checkpoints)

    # The task completed with the floor's segments instead of erroring.
    assert not result.get("awaiting_selection")
    assert [s["start_time"] for s in result["segments_to_render"]] == ["01:00"]
    assert result["segments_to_render"][0]["hook_type"] == "visual_energy"
    # The floor was told WHY it is selecting, and its segments carry
    # that reason — the degradation is stamped, not silent.
    assert select_calls and select_calls[0]["reason_context"] == (
        "AI analysis unavailable"
    )
    saved = [
        call.args[0]
        for call in checkpoints.await_args_list
        if call.args and isinstance(call.args[0], dict)
    ]
    # The degraded stand-in is never persisted as the analysis
    # checkpoint (a retry must re-attempt the real analysis)…
    assert not [s["analysis"] for s in saved if s.get("analysis")]
    # …but the run's own summary says plainly what happened.
    assert "AI analysis was unavailable" in (result.get("summary") or "")


@pytest.mark.asyncio
async def test_local_provider_unavailable_still_fails_the_task(
    monkeypatch, pipeline
):
    monkeypatch.setattr(
        VideoService,
        "analyze_transcript",
        AsyncMock(
            side_effect=provider_manager.LocalProviderUnavailable(
                "Local model is not reachable"
            )
        ),
    )
    with pytest.raises(provider_manager.LocalProviderUnavailable):
        await _run_pipeline()


@pytest.mark.asyncio
async def test_analysis_failure_under_brief_never_pads(monkeypatch, pipeline):
    monkeypatch.setattr(
        VideoService,
        "analyze_transcript",
        AsyncMock(side_effect=RuntimeError("Transcript analysis failed: boom")),
    )
    select = AsyncMock(return_value=list(VISUAL_SEGMENTS))
    monkeypatch.setattr(visual_moments_module, "select_visual_moments", select)

    result = await _run_pipeline(
        content_brief={
            "version": 2,
            "allowed_ranges": [],
            "excluded_ranges": [],
            "flag_points": [],
            "key_timestamps": [],
            "mandatory": [],
            "preferred": [],
        }
    )

    # The brief's explicit no-compliant-moments outcome, zero clips:
    # the floor must not pad a brief task even when analysis failed.
    assert result["segments_to_render"] == []
    select.assert_not_called()


# S-1 (SETTINGS_RENDERER_VERIFICATION.md §8.2): the fallback window
# runs when the visual floor itself fails on top of an AI outage.
# That starter clip must carry the "(AI analysis unavailable)" marker
# in its reasoning, or DegradedAnalysisNotice (which renders only on
# the marker) returns null and the degraded task looks like a normal
# AI-picked run. The AI-success-but-zero-segments case falls through
# to the same window and must stay UNMARKED: that is not an outage.
# The no-usable-transcript case keeps its precedence (§8.3): when the
# transcript is unusable, that context wins even if the analysis also
# failed, so its fallback stays unmarked too.
MARKER = "(AI analysis unavailable)"
UNMARKED_FALLBACK_REASONING = (
    "AI analysis did not identify a strong standalone segment, "
    "so SupoClip generated the first available portion of the video."
)


@pytest.mark.asyncio
async def test_floor_failure_during_outage_stamps_fallback_reasoning(
    monkeypatch, pipeline
):
    monkeypatch.setattr(
        VideoService,
        "analyze_transcript",
        AsyncMock(side_effect=RuntimeError("Transcript analysis failed: boom")),
    )
    monkeypatch.setattr(
        visual_moments_module,
        "select_visual_moments",
        lambda *_a, **_k: (_ for _ in ()).throw(
            RuntimeError("beat grid unreadable")
        ),
    )

    result = await _run_pipeline()

    assert [s["start_time"] for s in result["segments_to_render"]] == ["00:00"]
    fallback = result["segments_to_render"][0]
    assert fallback["hook_type"] == "fallback"
    assert MARKER in fallback["reasoning"]


@pytest.mark.asyncio
async def test_ai_success_zero_segments_fallback_stays_unmarked(
    monkeypatch, pipeline
):
    monkeypatch.setattr(
        VideoService,
        "analyze_transcript",
        AsyncMock(
            return_value=SimpleNamespace(
                summary="Nothing stood out.",
                key_topics=[],
                most_relevant_segments=[],
            )
        ),
    )
    select = monkeypatch.setattr(
        visual_moments_module,
        "select_visual_moments",
        lambda *_a, **_k: (_ for _ in ()).throw(
            AssertionError("floor must not run when the AI served")
        ),
    )

    result = await _run_pipeline()

    assert [s["start_time"] for s in result["segments_to_render"]] == ["00:00"]
    fallback = result["segments_to_render"][0]
    assert fallback["hook_type"] == "fallback"
    assert fallback["reasoning"] == UNMARKED_FALLBACK_REASONING
    assert MARKER not in fallback["reasoning"]


@pytest.mark.asyncio
async def test_no_transcript_precedence_keeps_fallback_unmarked(
    monkeypatch, pipeline
):
    monkeypatch.setattr(
        VideoService, "generate_transcript", AsyncMock(return_value="")
    )
    monkeypatch.setattr(
        VideoService,
        "analyze_transcript",
        AsyncMock(side_effect=RuntimeError("Transcript analysis failed: boom")),
    )
    monkeypatch.setattr(
        visual_moments_module,
        "select_visual_moments",
        lambda *_a, **_k: (_ for _ in ()).throw(
            RuntimeError("beat grid unreadable")
        ),
    )

    result = await _run_pipeline()

    assert [s["start_time"] for s in result["segments_to_render"]] == ["00:00"]
    fallback = result["segments_to_render"][0]
    assert fallback["hook_type"] == "fallback"
    assert fallback["reasoning"] == UNMARKED_FALLBACK_REASONING
    assert MARKER not in fallback["reasoning"]
