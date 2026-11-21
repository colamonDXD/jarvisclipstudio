"""Evidence-based niche classification (visual + audio + title).

Regression suite for the 2026-11-09 defect: a ~1h video of two hosts
eating their way through restaurants in Japan ("CORE $1 VS $10,000
RESTAURANTS IN JAPAN") was classified podcast_news from its chatty
transcript alone, so every clip got the podcast editing profile.

Covered here:
(a) chatty transcript + title/visual evidence of destination food/travel
    scenes -> NOT podcast_news (the deterministic guardrail corrects a
    model that still picks the podcast attractor);
(b) genuine studio podcast evidence -> podcast_news still wins, even
    when the title mentions food (a chef interview in a studio);
(c) visual evidence unavailable -> graceful degradation, result marked
    with what was missing;
(d) the fallback path is honest: marked is_fallback, zero confidence,
    and never the shared constant object.

No live LLM calls: the classification agent is faked, and the visual /
audio analysers are faked at their seams (no cv2 in this environment).
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

import src.auto_director.classify as classify_module
from src.auto_director.classify import (
    FALLBACK_RESULT,
    NICHE_CLASSIFICATION_SYSTEM_PROMPT,
    _build_niche_classification_prompt,
    classify_niche,
    summarize_audio_beats,
    summarize_visual_analysis,
)

CHATTY_TRANSCRIPT = (
    "Okay so tell me what you think of this one. Honestly? I love it. "
    "Yeah I was going to say the same thing, like, the flavour is insane. "
    "Right? And the texture — wait, try this one next. Oh wow. Okay so "
    "would you pay for this again? A hundred percent. Let's talk about "
    "the price difference because that is wild to me."
)

JAPAN_TITLE = "CORE $1 VS $10,000 RESTAURANTS IN JAPAN"


def _mock_agent(monkeypatch, *, niche, confidence=0.9, reasons=None, capture=None):
    mock_output = SimpleNamespace(
        niche=niche,
        confidence=confidence,
        reasons=reasons or ["reason"],
    )

    async def _run(prompt):
        if capture is not None:
            capture.append(prompt)
        return SimpleNamespace(output=mock_output)

    mock_agent = SimpleNamespace(run=AsyncMock(side_effect=_run))
    monkeypatch.setattr(
        "src.auto_director.classify.get_niche_classification_agent",
        lambda: mock_agent,
    )
    return mock_agent


def _travel_visual_summary():
    """Visual summary of location food footage: faces in some frames,
    many scene changes, action spread across the frame."""
    return {
        "sample_count": 200,
        "face_presence_rate": 0.6,
        "multi_face_rate": 0.45,
        "mean_face_count": 1.2,
        "mean_largest_face_area_frac": 0.05,
        "cut_count": 180,
        "cuts_per_minute": 3.0,
        "span_seconds": 3600.0,
        "mean_motion_spread": 0.52,
        "static_talking_head_likely": False,
        "scene_variety_high": True,
    }


def _studio_visual_summary():
    """Visual summary of a studio podcast: two faces in nearly every
    frame, almost no scene changes, concentrated motion."""
    return {
        "sample_count": 200,
        "face_presence_rate": 0.97,
        "multi_face_rate": 0.9,
        "mean_face_count": 1.9,
        "mean_largest_face_area_frac": 0.12,
        "cut_count": 4,
        "cuts_per_minute": 0.4,
        "span_seconds": 3600.0,
        "mean_motion_spread": 0.12,
        "static_talking_head_likely": True,
        "scene_variety_high": False,
    }


def _audio_summary():
    return {
        "tempo_bpm": 92.0,
        "tempo_strength": 0.9,
        "beat_count": 12,
        "has_drop": False,
        "duration_seconds": 3600.0,
        "mean_level_db": -22.0,
        "p95_level_db": -9.0,
        "dynamic_range_db": 8.0,
        "music_likely": False,
        "speech_fraction": 0.72,
        "speech_dominant": True,
    }


class TestGuardrailFoodTravel:
    @pytest.mark.asyncio
    async def test_chatty_transcript_with_food_travel_evidence_is_not_podcast(
        self, monkeypatch
    ):
        """(a) The Japan restaurants case: the model picks the podcast
        attractor from the chatty transcript, but title + visual
        evidence must override it to travel_vlog."""
        capture = []
        _mock_agent(
            monkeypatch,
            niche="podcast_news",
            confidence=0.88,
            reasons=["Two people discussing opinions"],
            capture=capture,
        )

        result = await classify_niche(
            CHATTY_TRANSCRIPT,
            [],
            title=JAPAN_TITLE,
            visual_evidence=_travel_visual_summary(),
            audio_evidence=_audio_summary(),
        )

        assert result["niche"] == "travel_vlog"
        assert result["niche"] != "podcast_news"
        assert result["is_fallback"] is False
        assert 0.55 <= result["confidence"] <= 0.7
        assert (
            result["evidence"]["guardrail"]
            == "podcast_news_overridden_by_title_and_visual_evidence"
        )
        assert result["evidence"]["title"] == JAPAN_TITLE
        assert result["evidence"]["visual"]["cut_count"] == 180
        assert result["evidence_status"] == "full"
        # The model actually saw the new evidence, not just the transcript.
        assert JAPAN_TITLE in capture[0]
        assert "Visual evidence" in capture[0]
        assert "Audio evidence" in capture[0]

    @pytest.mark.asyncio
    async def test_raw_source_analysis_is_summarized_and_corrects(
        self, monkeypatch
    ):
        """(a, raw seam) A raw get_source_analysis-shaped dict (face
        boxes + cuts + SceneEvidence-like context) is summarized by the
        classifier itself and drives the same correction."""
        _mock_agent(monkeypatch, niche="podcast_news", confidence=0.8)

        samples = []
        for i in range(100):
            t = i * 36.0
            boxes = (
                [(100.0, 100.0, 300.0, 300.0), (900.0, 120.0, 280.0, 280.0)]
                if i % 5 < 3
                else []
            )
            samples.append((t, boxes))
        context = [
            SimpleNamespace(
                time=i * 36.0,
                face_count=2,
                face_area=0.04,
                motion=0.03,
                motion_spread=0.52,
                outside_motion=0.4,
                text_rows=1,
                cut_text_lines=0,
            )
            for i in range(100)
        ]
        raw_analysis = {
            "width": 1920,
            "height": 1080,
            "samples": samples,
            "cuts": [float(i * 20) for i in range(180)],
            "context": context,
        }

        result = await classify_niche(
            CHATTY_TRANSCRIPT,
            [],
            title=JAPAN_TITLE,
            visual_evidence=raw_analysis,
        )

        assert result["niche"] == "travel_vlog"
        visual = result["evidence"]["visual"]
        assert visual["face_presence_rate"] == 0.6
        assert visual["cut_count"] == 180
        assert visual["static_talking_head_likely"] is False

    @pytest.mark.asyncio
    async def test_model_travel_answer_is_kept_and_carries_evidence(
        self, monkeypatch
    ):
        """When the model itself reads the evidence correctly, its
        answer and confidence pass through untouched."""
        _mock_agent(
            monkeypatch,
            niche="travel_vlog",
            confidence=0.91,
            reasons=["Restaurant tour in Japan"],
        )
        result = await classify_niche(
            CHATTY_TRANSCRIPT,
            [],
            title=JAPAN_TITLE,
            visual_evidence=_travel_visual_summary(),
        )
        assert result["niche"] == "travel_vlog"
        assert result["confidence"] == 0.91
        assert "guardrail" not in result["evidence"]


class TestStudioPodcastStillWins:
    @pytest.mark.asyncio
    async def test_studio_podcast_evidence_keeps_podcast_news(self, monkeypatch):
        """(b) A genuine studio podcast: static two-shot visual evidence
        and a podcast title — podcast_news must still win."""
        _mock_agent(
            monkeypatch,
            niche="podcast_news",
            confidence=0.93,
            reasons=["Studio interview discussion"],
        )
        result = await classify_niche(
            "Welcome back to the show. Today we're talking about startups, "
            "fundraising, and what founders get wrong in year one.",
            ["podcast"],
            title="The Daily Grind Podcast #142 — Talking Startups",
            visual_evidence=_studio_visual_summary(),
            audio_evidence=_audio_summary(),
        )
        assert result["niche"] == "podcast_news"
        assert result["confidence"] == 0.93
        assert "guardrail" not in result["evidence"]

    @pytest.mark.asyncio
    async def test_food_word_in_title_does_not_override_a_studio_setup(
        self, monkeypatch
    ):
        """(b) A chef interviewed in a studio is still a podcast: the
        static talking-head evidence blocks the guardrail even though
        the title names a restaurant."""
        _mock_agent(monkeypatch, niche="podcast_news", confidence=0.9)
        result = await classify_niche(
            "So how did you end up opening your first restaurant? "
            "It started in my grandmother's kitchen, honestly.",
            [],
            title="Podcast: How I Built My Restaurant Empire",
            visual_evidence=_studio_visual_summary(),
        )
        assert result["niche"] == "podcast_news"
        assert "guardrail" not in result["evidence"]


class TestGracefulDegradation:
    @pytest.mark.asyncio
    async def test_visual_unavailable_is_marked_and_still_classifies(
        self, monkeypatch
    ):
        """(c) No visual evidence (analysis failed / no cv2 / audio-only
        source): classification still runs on title + audio, and the
        result says the visual evidence was missing."""
        _mock_agent(
            monkeypatch,
            niche="travel_vlog",
            confidence=0.84,
            reasons=["Title names a Japan restaurant tour"],
        )
        result = await classify_niche(
            CHATTY_TRANSCRIPT,
            [],
            title=JAPAN_TITLE,
            visual_evidence=None,
            audio_evidence=_audio_summary(),
        )
        assert result["niche"] == "travel_vlog"
        assert result["is_fallback"] is False
        assert result["evidence"]["visual"] is None
        assert "visual" in result["evidence"]["missing"]
        assert result["evidence_status"] == "partial"

    @pytest.mark.asyncio
    async def test_transcript_only_is_marked(self, monkeypatch):
        """(c) Nothing but the transcript: evidence_status says so."""
        _mock_agent(monkeypatch, niche="gaming", confidence=0.77)
        result = await classify_niche("ranked grind tonight, new season drops", [])
        assert result["niche"] == "gaming"
        assert result["evidence_status"] == "transcript_only"
        assert set(result["evidence"]["missing"]) == {"visual", "audio", "title"}


class TestFallbackHonesty:
    @pytest.mark.asyncio
    async def test_llm_failure_returns_marked_fallback(self, monkeypatch):
        """(d) A provider failure returns the fallback clearly marked:
        podcast_news in name only, zero confidence, is_fallback True —
        and a fresh dict, never the shared constant."""
        mock_agent = SimpleNamespace(
            run=AsyncMock(side_effect=ValueError("LLM error"))
        )
        monkeypatch.setattr(
            "src.auto_director.classify.get_niche_classification_agent",
            lambda: mock_agent,
        )
        result = await classify_niche(
            CHATTY_TRANSCRIPT, [], title=JAPAN_TITLE,
            visual_evidence=_travel_visual_summary(),
        )
        assert result["niche"] == "podcast_news"
        assert result["confidence"] == 0.0
        assert result["reasons"] == ["fallback"]
        assert result["is_fallback"] is True
        assert result["evidence"]["title"] == JAPAN_TITLE
        assert result is not FALLBACK_RESULT

    def test_fallback_constant_unchanged(self):
        """(d) The shared constant keeps its historical shape; callers
        that compare against it still see exactly the marked values."""
        assert FALLBACK_RESULT == {
            "niche": "podcast_news",
            "confidence": 0.0,
            "reasons": ["fallback"],
        }


class TestSummarizers:
    def test_visual_summary_from_raw_analysis(self):
        samples = [
            (0.0, [(0.0, 0.0, 480.0, 270.0)]),
            (10.0, [(0.0, 0.0, 480.0, 270.0), (500.0, 0.0, 480.0, 270.0)]),
            (20.0, []),
            (30.0, [(0.0, 0.0, 480.0, 270.0)]),
        ]
        summary = summarize_visual_analysis(
            {"width": 1920, "height": 1080, "samples": samples, "cuts": [10.0, 20.0], "context": []},
            duration=60.0,
        )
        assert summary["face_presence_rate"] == 0.75
        assert summary["multi_face_rate"] == 0.25
        assert summary["cut_count"] == 2
        assert summary["cuts_per_minute"] == 2.0
        assert summary["static_talking_head_likely"] is False

    def test_visual_summary_empty_analysis_is_none(self):
        assert summarize_visual_analysis(None) is None
        assert summarize_visual_analysis({}) is None
        assert (
            summarize_visual_analysis(
                {"width": 0, "height": 0, "samples": [], "cuts": [], "context": []}
            )
            is None
        )

    def test_audio_summary_from_beat_grid(self):
        grid = {
            "tempo_bpm": 120.0,
            "tempo_strength": 2.4,
            "beat_times": [float(i) for i in range(40)],
            "drop_time": 30.0,
            "duration": 60.0,
            "energy_curve": {"hop_sec": 0.05, "levels_db": [-30.0, -20.0, -10.0, -12.0]},
        }
        summary = summarize_audio_beats(grid, speech_fraction=0.2)
        assert summary["music_likely"] is True
        assert summary["has_drop"] is True
        assert summary["beat_count"] == 40
        assert summary["speech_fraction"] == 0.2
        assert summary["speech_dominant"] is False
        assert summarize_audio_beats(None) is None
        assert summarize_audio_beats({}) is None


class TestPromptContract:
    def test_prompt_tightens_podcast_and_broadens_travel(self):
        prompt = NICHE_CLASSIFICATION_SYSTEM_PROMPT
        assert "recorded primarily as conversation" in prompt
        assert "destination food experiences" in prompt
        assert "restaurant tours" in prompt

    def test_prompt_builder_includes_all_evidence_sections(self):
        prompt = _build_niche_classification_prompt(
            "hello transcript",
            ["Tokyo"],
            title=JAPAN_TITLE,
            description="Eating through Japan on two budgets.",
            visual_evidence=_travel_visual_summary(),
            audio_evidence=_audio_summary(),
        )
        assert JAPAN_TITLE in prompt
        assert "Eating through Japan" in prompt
        assert "Visual evidence" in prompt
        assert "Audio evidence" in prompt
        assert "hello transcript" in prompt

    def test_prompt_builder_without_evidence_matches_legacy_shape(self):
        prompt = _build_niche_classification_prompt("hello", [])
        assert "Visual evidence" not in prompt
        assert "Audio evidence" not in prompt
        assert "Video title" not in prompt


class TestPipelineSeam:
    """VideoService._classify_source_niche: the producer that stamps
    segments for the render path, with analysers faked at their seams."""

    @pytest.mark.asyncio
    async def test_evidence_gathered_and_result_returned(self, monkeypatch):
        import src.services.video_service as video_module
        from src.services.video_service import VideoService

        seen = {}

        async def fake_classify(sample, entities, **kwargs):
            seen.update(kwargs)
            seen["sample"] = sample
            return {
                "niche": "travel_vlog",
                "confidence": 0.8,
                "reasons": ["evidence"],
                "is_fallback": False,
                "evidence": {},
                "evidence_status": "full",
            }

        monkeypatch.setattr(classify_module, "classify_niche", fake_classify)
        monkeypatch.setattr(
            "src.media.reframe_plan.get_source_analysis",
            lambda path, spans: {
                "width": 1920,
                "height": 1080,
                "samples": [(0.0, [(0.0, 0.0, 300.0, 300.0)]), (30.0, [])],
                "cuts": [10.0, 20.0],
                "context": [],
            },
        )
        monkeypatch.setattr(
            "src.auto_director.beat_grid.analyse_audio_beats",
            lambda path: {
                "tempo_bpm": 90.0,
                "tempo_strength": 0.5,
                "beat_times": [1.0, 2.0],
                "drop_time": None,
                "duration": 60.0,
                "energy_curve": {"hop_sec": 0.05, "levels_db": [-20.0, -10.0]},
            },
        )
        monkeypatch.setattr(
            video_module,
            "load_cached_transcript_data",
            lambda path: {
                "words": [{"text": "hi", "start": 0, "end": 30000}]
            },
        )

        from pathlib import Path

        result = await VideoService._classify_source_niche(
            video_path=Path("/tmp/whatever.mp4"),
            transcript="chatty eating talk",
            segments=[{"start_time": "00:00", "end_time": "01:00"}],
            title=JAPAN_TITLE,
            duration=60.0,
        )
        assert result["niche"] == "travel_vlog"
        assert seen["title"] == JAPAN_TITLE
        assert seen["visual_evidence"]["cut_count"] == 2
        assert seen["audio_evidence"]["speech_fraction"] == 0.5

    @pytest.mark.asyncio
    async def test_nothing_to_classify_returns_none_without_llm(
        self, monkeypatch
    ):
        import src.services.video_service as video_module
        from src.services.video_service import VideoService

        calls = []

        async def fake_classify(sample, entities, **kwargs):
            calls.append(sample)
            return {}

        monkeypatch.setattr(classify_module, "classify_niche", fake_classify)
        monkeypatch.setattr(
            "src.media.reframe_plan.get_source_analysis",
            lambda path, spans: None,
        )
        monkeypatch.setattr(
            "src.auto_director.beat_grid.analyse_audio_beats",
            lambda path: None,
        )
        monkeypatch.setattr(
            video_module, "load_cached_transcript_data", lambda path: None
        )

        from pathlib import Path

        result = await VideoService._classify_source_niche(
            video_path=Path("/tmp/whatever.mp4"),
            transcript="",
            segments=[],
            title=None,
            duration=60.0,
        )
        assert result is None
        assert calls == []
