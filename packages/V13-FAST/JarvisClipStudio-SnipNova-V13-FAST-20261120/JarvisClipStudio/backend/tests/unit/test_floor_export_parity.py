"""Floor-export parity (O-R3): degraded / transcript-less exports are
not lesser exports.

Step-0 finding (code trace + the step-2 acceptance artefacts): captions
already reach floor renders through the standard seam —
``create_optimized_clip``'s ASS builder reads the transcript sidecar's
words for the clip's keep ranges regardless of which path picked the
segment. The acceptance run's "no captions" warnings came from its
SEEDED sidecar, whose words covered only the labelled windows; the one
floor window overlapping a labelled window rendered captions. The real
gap was the hook title (and the segment text it derives from): floor
producers hard-set ``text: ""`` / ``hook_title: None`` and nothing ever
grounded or titled them, while AI segments are grounded in the
transcript and carry an AI-written title.

These tests pin the fix at the pipeline seam: when the floor slate is
built, each segment's text is grounded in the transcript (the same
``_ground_segment_text`` the AI segments get) and its hook title is
derived from its own first sentence through the same sanitizer the AI
path uses. No words -> no title, honestly.
"""

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

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

# Words for two windows of the fake 600 s source, in the sidecar's
# millisecond-stamped shape: the visual window (60–90 s) and the
# fallback window (0–30 s).
_WINDOW_WORDS = [
    (2.0, "The"),
    (2.4, "harbour"),
    (2.9, "lights"),
    (3.4, "come"),
    (3.8, "on"),
    (4.2, "first."),
    (5.0, "Gulls"),
    (5.5, "circle"),
    (6.0, "overhead."),
    (62.0, "The"),
    (62.4, "river"),
    (62.8, "market"),
    (63.3, "opens"),
    (63.7, "at"),
    (64.0, "dawn."),
    (65.0, "Traders"),
    (65.6, "shout"),
    (66.1, "their"),
    (66.6, "prices"),
    (67.2, "loudly."),
]


def _write_sidecar(source, windows=((0.0, 90.0),)):
    words = [
        {
            "text": f" {text}",
            "start": int(start * 1000),
            "end": int((start + 0.4) * 1000),
            "confidence": 0.95,
            "speaker": None,
        }
        for start, text in _WINDOW_WORDS
        if any(lo <= start < hi for lo, hi in windows)
    ]
    payload = {
        "version": 2,
        "words": words,
        "utterances": [],
        "text": " ".join(w["text"].strip() for w in words),
        "language_code": "en",
    }
    source.with_suffix(".transcript_cache.json").write_text(
        json.dumps(payload)
    )


@pytest.fixture()
def pipeline(monkeypatch, tmp_path):
    """process_video_complete with heavy stages faked (mirrors
    test_analysis_failure_degradation's fixture)."""
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
        VideoService,
        "generate_transcript",
        AsyncMock(return_value="a real transcript with words"),
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


async def _run_pipeline(**kwargs):
    return await VideoService.process_video_complete(
        url="upload://local/source.mp4",
        source_type="upload",
        task_id="task-floor-parity",
        checkpoint={},
        checkpoint_callback=AsyncMock(),
        progress_callback=AsyncMock(),
        **kwargs,
    )


def _fail_analysis(monkeypatch):
    monkeypatch.setattr(
        VideoService,
        "analyze_transcript",
        AsyncMock(
            side_effect=RuntimeError("Transcript analysis failed: boom")
        ),
    )


@pytest.mark.asyncio
async def test_visual_floor_segments_are_grounded_and_titled(
    monkeypatch, pipeline
):
    _write_sidecar(pipeline)
    _fail_analysis(monkeypatch)
    monkeypatch.setattr(
        visual_moments_module,
        "select_visual_moments",
        lambda *_a, **_k: [dict(segment) for segment in VISUAL_SEGMENTS],
    )

    result = await _run_pipeline()

    segment = result["segments_to_render"][0]
    # Text grounded in the transcript words inside the window — the
    # same grounding AI segments receive.
    assert segment["text"] == (
        "The river market opens at dawn. Traders shout their prices "
        "loudly."
    )
    # Hook title derived from the segment's own first sentence.
    assert segment["hook_title"] == "The river market opens at dawn"


@pytest.mark.asyncio
async def test_visual_floor_without_transcript_words_stays_untitled(
    monkeypatch, pipeline
):
    # No sidecar at all: a genuinely transcript-less floor. Nothing to
    # ground, nothing to title — the segment stays exactly as the
    # floor produced it.
    _fail_analysis(monkeypatch)
    monkeypatch.setattr(
        visual_moments_module,
        "select_visual_moments",
        lambda *_a, **_k: [dict(segment) for segment in VISUAL_SEGMENTS],
    )

    result = await _run_pipeline()

    segment = result["segments_to_render"][0]
    assert segment["text"] == ""
    assert segment["hook_title"] is None


@pytest.mark.asyncio
async def test_fallback_window_is_grounded_and_titled(
    monkeypatch, pipeline
):
    _write_sidecar(pipeline)
    _fail_analysis(monkeypatch)
    monkeypatch.setattr(
        visual_moments_module,
        "select_visual_moments",
        lambda *_a, **_k: (_ for _ in ()).throw(
            RuntimeError("beat grid unreadable")
        ),
    )

    result = await _run_pipeline()

    segment = result["segments_to_render"][0]
    assert segment["hook_type"] == "fallback"
    assert segment["text"] == "The harbour lights come on first. Gulls circle overhead."
    assert segment["hook_title"] == "The harbour lights come on first"


@pytest.mark.asyncio
async def test_ai_segments_keep_their_own_title(monkeypatch, pipeline):
    # The parity stamp lives in the floor branch only: an AI-picked
    # segment's own title and text flow through untouched.
    _write_sidecar(pipeline)
    monkeypatch.setattr(
        VideoService,
        "analyze_transcript",
        AsyncMock(
            return_value=SimpleNamespace(
                summary="A real analysis.",
                key_topics=[],
                most_relevant_segments=[
                    {
                        "start_time": "01:00",
                        "end_time": "01:30",
                        "text": "analysis supplied text",
                        "relevance_score": 0.9,
                        "reasoning": "AI pick",
                        "virality": {"total_score": 90},
                        "hook_title": "The AI Wrote This Title",
                    }
                ],
            )
        ),
    )
    monkeypatch.setattr(
        visual_moments_module,
        "select_visual_moments",
        lambda *_a, **_k: (_ for _ in ()).throw(
            AssertionError("floor must not run when the AI served")
        ),
    )

    result = await _run_pipeline()

    segment = result["segments_to_render"][0]
    assert segment["hook_title"] == "The AI Wrote This Title"


# ---------------------------------------------------------------------------
# The title derivation itself
# ---------------------------------------------------------------------------


def test_floor_hook_title_takes_the_first_sentence():
    title = VideoService._floor_hook_title(
        "The river market opens at dawn. Traders shout their prices."
    )
    assert title == "The river market opens at dawn"


def test_floor_hook_title_without_words_is_none():
    assert VideoService._floor_hook_title("") is None
    assert VideoService._floor_hook_title("   ") is None
    assert VideoService._floor_hook_title(None) is None


def test_floor_hook_title_is_capped_like_an_ai_title():
    from src.ai import HOOK_TITLE_MAX_CHARS

    long_sentence = " ".join(["word"] * 60) + "."
    title = VideoService._floor_hook_title(long_sentence)
    assert title is not None
    assert len(title) <= HOOK_TITLE_MAX_CHARS
