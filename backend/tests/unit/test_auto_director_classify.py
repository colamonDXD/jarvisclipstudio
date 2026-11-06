"""Tests for auto_director niche classification."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from src.auto_director.classify import classify_niche, FALLBACK_RESULT, NICHE_CLASSIFICATION_SYSTEM_PROMPT


class TestClassifyNiche:
    """Tests for the classify_niche function."""

    @pytest.mark.asyncio
    async def test_travel_text_returns_travel_vlog(self, monkeypatch):
        """Travel-related transcript should be classified as travel_vlog."""
        transcript = "Welcome to my Japan travel vlog! Today we're exploring Tokyo's Shibuya crossing, visiting the Meiji Shrine, and trying amazing street food in Harajuku. The cherry blossoms are in full bloom."
        entities = ["Tokyo", "Shibuya", "Meiji Shrine", "Harajuku", "Japan", "cherry blossoms"]

        # Mock the agent to return travel_vlog
        mock_output = SimpleNamespace(
            niche="travel_vlog",
            confidence=0.92,
            reasons=["Travel destination content", "Cultural exploration", "Location-specific landmarks"],
        )
        mock_agent = SimpleNamespace(run=AsyncMock(return_value=SimpleNamespace(output=mock_output)))
        monkeypatch.setattr("src.auto_director.classify.get_transcript_agent", lambda *_, **__: mock_agent)

        result = await classify_niche(transcript, entities)

        assert result["niche"] == "travel_vlog"
        assert result["confidence"] == 0.92
        assert result["reasons"] == ["Travel destination content", "Cultural exploration", "Location-specific landmarks"]

    @pytest.mark.asyncio
    async def test_meme_text_returns_meme_comedy(self, monkeypatch):
        """Meme/comedy transcript should be classified as meme_comedy."""
        transcript = "POV: When you realize the NPC has better dialogue than the main character. This meme format is taking over TikTok right now. The timing on this reaction is perfect."
        entities = ["TikTok", "NPC", "meme format", "reaction"]

        # Mock the agent to return meme_comedy
        mock_output = SimpleNamespace(
            niche="meme_comedy",
            confidence=0.88,
            reasons=["Meme format reference", "POV comedy style", "Social media trend awareness"],
        )
        mock_agent = SimpleNamespace(run=AsyncMock(return_value=SimpleNamespace(output=mock_output)))
        monkeypatch.setattr("src.auto_director.classify.get_transcript_agent", lambda *_, **__: mock_agent)

        result = await classify_niche(transcript, entities)

        assert result["niche"] == "meme_comedy"
        assert result["confidence"] == 0.88
        assert result["reasons"] == ["Meme format reference", "POV comedy style", "Social media trend awareness"]

    @pytest.mark.asyncio
    async def test_llm_raises_returns_fallback(self, monkeypatch):
        """When LLM raises an exception, should return fallback dict."""
        transcript = "Some content here"
        entities = ["entity1"]

        # Mock the agent to raise an exception
        mock_agent = SimpleNamespace(run=AsyncMock(side_effect=ValueError("LLM error")))
        monkeypatch.setattr("src.auto_director.classify.get_transcript_agent", lambda *_, **__: mock_agent)

        result = await classify_niche(transcript, entities)

        assert result == FALLBACK_RESULT
        assert result["niche"] == "podcast_news"
        assert result["confidence"] == 0.0
        assert result["reasons"] == ["fallback"]

    @pytest.mark.asyncio
    async def test_invalid_niche_returns_fallback(self, monkeypatch):
        """When LLM returns invalid niche, should return fallback dict."""
        transcript = "Some content here"
        entities = ["entity1"]

        # Mock the agent to return invalid niche
        mock_output = SimpleNamespace(
            niche="invalid_niche",
            confidence=0.9,
            reasons=["reason"],
        )
        mock_agent = SimpleNamespace(run=AsyncMock(return_value=SimpleNamespace(output=mock_output)))
        monkeypatch.setattr("src.auto_director.classify.get_transcript_agent", lambda *_, **__: mock_agent)

        result = await classify_niche(transcript, entities)

        assert result == FALLBACK_RESULT

    @pytest.mark.asyncio
    async def test_confidence_clamped_to_valid_range(self, monkeypatch):
        """Confidence should be clamped to 0.0-1.0 range."""
        transcript = "Some content here"
        entities = ["entity1"]

        # Mock the agent to return confidence > 1.0
        mock_output = SimpleNamespace(
            niche="travel_vlog",
            confidence=1.5,
            reasons=["reason"],
        )
        mock_agent = SimpleNamespace(run=AsyncMock(return_value=SimpleNamespace(output=mock_output)))
        monkeypatch.setattr("src.auto_director.classify.get_transcript_agent", lambda *_, **__: mock_agent)

        result = await classify_niche(transcript, entities)

        assert result["confidence"] == 1.0

    @pytest.mark.asyncio
    async def test_confidence_clamped_from_negative(self, monkeypatch):
        """Confidence should be clamped from negative values."""
        transcript = "Some content here"
        entities = ["entity1"]

        # Mock the agent to return negative confidence
        mock_output = SimpleNamespace(
            niche="travel_vlog",
            confidence=-0.5,
            reasons=["reason"],
        )
        mock_agent = SimpleNamespace(run=AsyncMock(return_value=SimpleNamespace(output=mock_output)))
        monkeypatch.setattr("src.auto_director.classify.get_transcript_agent", lambda *_, **__: mock_agent)

        result = await classify_niche(transcript, entities)

        assert result["confidence"] == 0.0

    @pytest.mark.asyncio
    async def test_reasons_limited_to_three(self, monkeypatch):
        """Reasons should be limited to max 3 items."""
        transcript = "Some content here"
        entities = ["entity1"]

        # Mock the agent to return 5 reasons
        mock_output = SimpleNamespace(
            niche="travel_vlog",
            confidence=0.9,
            reasons=["reason1", "reason2", "reason3", "reason4", "reason5"],
        )
        mock_agent = SimpleNamespace(run=AsyncMock(return_value=SimpleNamespace(output=mock_output)))
        monkeypatch.setattr("src.auto_director.classify.get_transcript_agent", lambda *_, **__: mock_agent)

        result = await classify_niche(transcript, entities)

        assert len(result["reasons"]) == 3
        assert result["reasons"] == ["reason1", "reason2", "reason3"]

    @pytest.mark.asyncio
    async def test_empty_reasons_uses_fallback_reason(self, monkeypatch):
        """Empty reasons list should be replaced with fallback reason."""
        transcript = "Some content here"
        entities = ["entity1"]

        # Mock the agent to return empty reasons
        mock_output = SimpleNamespace(
            niche="travel_vlog",
            confidence=0.9,
            reasons=[],
        )
        mock_agent = SimpleNamespace(run=AsyncMock(return_value=SimpleNamespace(output=mock_output)))
        monkeypatch.setattr("src.auto_director.classify.get_transcript_agent", lambda *_, **__: mock_agent)

        result = await classify_niche(transcript, entities)

        assert result["reasons"] == ["fallback"]

    @pytest.mark.asyncio
    async def test_all_niche_categories_accepted(self, monkeypatch):
        """All valid niche categories should be accepted."""
        valid_niches = [
            "podcast_news",
            "travel_vlog",
            "meme_comedy",
            "gaming",
            "sports",
            "education_tutorial",
            "finance_business",
            "vlog_lifestyle",
        ]

        for niche in valid_niches:
            transcript = "Some content here"
            entities = ["entity1"]

            mock_output = SimpleNamespace(
                niche=niche,
                confidence=0.9,
                reasons=["reason"],
            )
            mock_agent = SimpleNamespace(run=AsyncMock(return_value=SimpleNamespace(output=mock_output)))
            monkeypatch.setattr("src.auto_director.classify.get_transcript_agent", lambda *_, **__: mock_agent)

            result = await classify_niche(transcript, entities)

            assert result["niche"] == niche, f"Failed for niche: {niche}"

    @pytest.mark.asyncio
    async def test_parsing_error_returns_fallback(self, monkeypatch):
        """JSON parsing errors should return fallback."""
        transcript = "Some content here"
        entities = ["entity1"]

        # Mock the agent to raise a parsing error
        mock_agent = SimpleNamespace(run=AsyncMock(side_effect=Exception("Failed to parse JSON")))
        monkeypatch.setattr("src.auto_director.classify.get_transcript_agent", lambda *_, **__: mock_agent)

        result = await classify_niche(transcript, entities)

        assert result == FALLBACK_RESULT

    def test_fallback_result_constant(self):
        """FALLBACK_RESULT should have the expected values."""
        assert FALLBACK_RESULT == {
            "niche": "podcast_news",
            "confidence": 0.0,
            "reasons": ["fallback"],
        }

    def test_system_prompt_contains_all_categories(self):
        """System prompt should mention all 8 niche categories."""
        prompt = NICHE_CLASSIFICATION_SYSTEM_PROMPT
        categories = [
            "podcast_news",
            "travel_vlog",
            "meme_comedy",
            "gaming",
            "sports",
            "education_tutorial",
            "finance_business",
            "vlog_lifestyle",
        ]
        for cat in categories:
            assert cat in prompt, f"Category {cat} not found in system prompt"

    def test_system_prompt_requires_json_only(self):
        """System prompt should require JSON-only output."""
        prompt = NICHE_CLASSIFICATION_SYSTEM_PROMPT
        assert "Return valid JSON only" in prompt
        assert "No markdown" in prompt or "no markdown" in prompt or "code fences" in prompt