"""Brief extraction (Addendum A.4/A.7): deterministic fallback + no persistence."""

from __future__ import annotations

import pytest

from src.api.routes import briefs as briefs_route
from src.auto_director.content_requirements import extract_brief_deterministically
from src.auto_director.content_requirements import validate_content_brief

EXPEDIA_TEXT = (
    "Clips can only be taken from after 2:56:29 in the source video. "
    "Expedia must be a major focus of the content."
)
CREATOR_TEXT = (
    'Use code "CREATOR" in-game and mention the code in every post. '
    "Use hashtag #Minerscave in the caption. "
    "Payouts start at 10,000 views and pay $1 per 1,000 views."
)


def test_expedia_fixture_range_survives():
    draft = extract_brief_deterministically(EXPEDIA_TEXT)
    assert draft["allowed_ranges"] == [
        {"start_seconds": 10589.0, "end_seconds": None,
         "start_label": "2:56:29", "end_label": None}
    ]
    # The draft is already a valid v2 brief with the focus requirement intact.
    brief = validate_content_brief(draft)
    assert brief["allowed_ranges"][0]["start_seconds"] == 10589.0
    assert brief["allowed_ranges"][0]["end_seconds"] is None
    assert any(r["type"] == "topic_focus" and r["requires_major"] for r in brief["requirements"])


def test_creator_fixture_must_mention_and_info_survive():
    draft = extract_brief_deterministically(CREATOR_TEXT)
    types = [r["type"] for r in draft["requirements"]]
    assert "must_mention" in types
    assert "caption_rule" in types
    assert "info_only" in types
    mention = next(r for r in draft["requirements"] if r["type"] == "must_mention")
    assert mention["phrase"] == "CREATOR"
    caption = next(r for r in draft["requirements"] if r["type"] == "caption_rule")
    assert caption["hashtags"] == ["#Minerscave"]


async def test_extract_endpoint_persists_nothing(monkeypatch):
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    monkeypatch.setattr(briefs_route, "extract_brief_with_llm", _no_llm)
    monkeypatch.setattr(
        briefs_route, "resolve_authenticated_user_id",
        AsyncMock(return_value="user-1"),
    )
    called = {"touched": False}

    class ExplodingRepository:
        def __init__(self, *args, **kwargs):
            called["touched"] = True
            raise AssertionError("extract endpoint must not persist anything")

    monkeypatch.setattr(briefs_route, "TaskRepository", ExplodingRepository, raising=False)
    monkeypatch.setattr(briefs_route, "PublishingRepository", ExplodingRepository, raising=False)
    request = SimpleNamespace(
        json=AsyncMock(return_value={"brief_text": CREATOR_TEXT}), headers={}
    )
    result = await briefs_route.extract_brief(request, db=None)
    assert called["touched"] is False
    assert result["extraction"]["source"] == "deterministic"
    assert result["content_brief"]["version"] == 2
    phrases = [
        r.get("phrase") for r in result["content_brief"]["requirements"]
        if r["type"] == "must_mention"
    ]
    assert "CREATOR" in phrases


async def _no_llm(text):
    return None
