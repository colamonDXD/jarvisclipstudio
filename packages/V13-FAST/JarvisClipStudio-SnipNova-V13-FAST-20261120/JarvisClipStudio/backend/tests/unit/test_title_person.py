"""Third-person guardrail for generated hook titles (Luffy's order, 2026-11-11).

The app clips OTHER creators' videos; the poster is a clipper, not the
speaker. A generated title must never carry the speaker's first person
("I met…", "I bought…", "my routine"). These tests pin the pure
guardrail, its wiring into ``sanitize_hook_title`` (analysis path), the
AI edit service's hook_title setting, and the ``create_clip``
persistence choke point.
"""

from unittest.mock import AsyncMock

import pytest

from src.ai import sanitize_hook_title
from src.auto_director.title_person import (
    contains_first_person,
    ensure_third_person_title,
)


# --- detection --------------------------------------------------------------


@pytest.mark.parametrize(
    "title",
    [
        "I Am Here",
        "I'm Trying the Spiciest Ramen",
        "I've Seen Enough",
        "My Morning Routine",
        "We Won Big",
        "Watch Us Cook",
        "Join Me at the Gym",
        "This Changed My Life",
        "Why I Quit My Job",
    ],
)
def test_detection_flags_first_person(title):
    assert contains_first_person(title) is True


@pytest.mark.parametrize(
    "title",
    [
        "Incredible $20k Gym Tour",
        "Island Hopping in Greece",
        "US Open Final Highlights",
        "Back in the US Again",
        "The $40k Mistake",
        "Your Move, Their Rules",
        "Flour Power in the Kitchen",
        "Do This Before Your Next Interview",
        "$400 Wagyu Sandwich With Real Gold",
        None,
        "",
    ],
)
def test_detection_ignores_clean_and_lookalike_titles(title):
    assert contains_first_person(title) is False


# --- clean titles pass through byte-identical --------------------------------


@pytest.mark.parametrize(
    "title",
    [
        "$400 Wagyu Sandwich With Real Gold",
        "Walked 25 Mins for a $20k Gym Tour",
        "Incredible $20k Gym Tour",
        "Island Hopping in Greece",
        "US Open Final Highlights",
        "Why Nobody Tells You This About VC",
        "Do This Before Your Next Interview",
        "He Said WHAT to the CEO?",
    ],
)
def test_clean_titles_pass_through_unchanged(title):
    assert ensure_third_person_title(title) == title
    assert ensure_third_person_title(title, creator_name="TJR") == title


# --- rewrites with a known creator -------------------------------------------


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("I Met a Millionaire in LA", "TJR Met a Millionaire in LA"),
        (
            "I Bought the Most Expensive Sandwich",
            "TJR Bought the Most Expensive Sandwich",
        ),
        ("I Try the World's Spiciest Ramen", "TJR Tries the World's Spiciest Ramen"),
        ("I'm Trying the Spiciest Ramen", "TJR Is Trying the Spiciest Ramen"),
        ("I Spend $10,000 in One Day", "TJR Spends $10,000 in One Day"),
        ("My Morning Routine", "TJR's Morning Routine"),
        ("Watch Me Cook", "Watch TJR Cook"),
        ("The $40k Mistake I Keep Seeing", "The $40k Mistake TJR Keeps Seeing"),
    ],
)
def test_rewrite_with_creator_attributes_to_creator(raw, expected):
    result = ensure_third_person_title(raw, creator_name="TJR")
    assert result == expected
    assert not contains_first_person(result)


# --- rewrites without a creator: neutral headline fragments -------------------


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("I Met a Millionaire in LA", "Met a Millionaire in LA"),
        ("I Bought the Most Expensive Sandwich", "Bought the Most Expensive Sandwich"),
        ("I'm Trying the Spiciest Ramen", "Trying the Spiciest Ramen"),
        ("My Morning Routine", "The Morning Routine"),
        ("We Tried the Most Expensive Hotel", "Tried the Most Expensive Hotel"),
        ("I Tried My Luck in Vegas", "Tried the Luck in Vegas"),
    ],
)
def test_rewrite_without_creator_is_neutral(raw, expected):
    result = ensure_third_person_title(raw)
    assert result == expected
    assert not contains_first_person(result)


# --- fallbacks: never first-person, never empty --------------------------------


def test_i_did_it_falls_back_to_the_moments_own_words():
    result = ensure_third_person_title(
        "I Did It",
        fallback_text="I finally beat the final boss after a hundred tries",
    )
    assert result
    assert result != "I Did It"
    assert not contains_first_person(result)
    assert "Boss" in result


def test_i_did_it_without_any_context_still_never_ships_first_person():
    result = ensure_third_person_title("I Did It")
    assert result
    assert not contains_first_person(result)


@pytest.mark.parametrize(
    "raw",
    [
        "I Met This",
        "I Did This",
        "I Like This",
        "I Can't Believe I Ate This",
        "My Honest Review",
        "We Need to Talk",
        "I'll Never Do This Again",
        "Join Us",
        "Follow Me",
        "I Am the Best",
    ],
)
def test_no_first_person_ever_survives(raw):
    for kwargs in (
        {},
        {"creator_name": "TJR"},
        {"fallback_text": "he opens the box and finds a golden ticket inside"},
    ):
        result = ensure_third_person_title(raw, **kwargs)
        assert result, raw
        assert not contains_first_person(result), (raw, kwargs, result)


def test_none_and_empty_inputs():
    assert ensure_third_person_title(None) is None
    assert ensure_third_person_title("") is None
    assert ensure_third_person_title("   ") is None


# --- wiring: sanitize_hook_title (analysis path) -------------------------------


def test_sanitize_rewrites_first_person_titles():
    assert (
        sanitize_hook_title("I Bought the Most Expensive Sandwich")
        == "Bought the Most Expensive Sandwich"
    )


def test_sanitize_keeps_clean_titles_identical():
    assert (
        sanitize_hook_title("$400 Wagyu Sandwich With Real Gold")
        == "$400 Wagyu Sandwich With Real Gold"
    )


def test_sanitize_fallback_uses_segment_text():
    result = sanitize_hook_title(
        "I Did It", fallback_text="he finally beat the final boss today"
    )
    assert result
    assert not contains_first_person(result)


# --- wiring: AI edit service hook_title setting --------------------------------


def test_ai_edit_setting_rewrites_generated_headline():
    from src.services.ai_edit_service import _apply_setting

    doc = {"captions": {}, "framing": {}, "effects": {}, "hookTitle": ""}
    note = _apply_setting(doc, "hook_title", "I Bought a Sandwich")
    assert note is None
    assert doc["hookTitle"] == "Bought a Sandwich"
    assert not contains_first_person(doc["hookTitle"])


def test_ai_edit_setting_keeps_clean_headline_and_empty_removes():
    from src.services.ai_edit_service import _apply_setting

    doc = {"captions": {}, "framing": {}, "effects": {}, "hookTitle": "Old"}
    _apply_setting(doc, "hook_title", "A $20k Gym Tour")
    assert doc["hookTitle"] == "A $20k Gym Tour"
    _apply_setting(doc, "hook_title", "")
    assert doc["hookTitle"] == ""


# --- wiring: create_clip persistence choke point -------------------------------


class _FakeResult:
    def mappings(self):
        return self

    def first(self):
        return None

    def scalar(self):
        return "clip-1"


class _FakeNested:
    async def __aenter__(self):
        return None

    async def __aexit__(self, *exc):
        return False


class _FakeDB:
    def __init__(self):
        self.insert_params = []

    async def execute(self, _stmt, params=None):
        if params and "hook_title" in params:
            self.insert_params.append(params)
        return _FakeResult()

    def begin_nested(self):
        return _FakeNested()


async def _create_with_title(monkeypatch, hook_title, text="he bought a sandwich"):
    from src.repositories import clip_repository

    monkeypatch.setattr(
        clip_repository, "persist_clip", AsyncMock(side_effect=lambda db, p, t: p)
    )
    db = _FakeDB()
    clip_id = await clip_repository.ClipRepository.create_clip(
        db,
        task_id="task-1",
        filename="clip.mp4",
        file_path="/tmp/clip.mp4",
        start_time="00:00",
        end_time="00:30",
        duration=30,
        text=text,
        relevance_score=0.9,
        reasoning="test",
        clip_order=1,
        hook_title=hook_title,
    )
    assert clip_id == "clip-1"
    assert db.insert_params, "create_clip never inserted"
    return db.insert_params[0]["hook_title"]


async def test_create_clip_never_persists_first_person_titles(monkeypatch):
    stored = await _create_with_title(
        monkeypatch, "I Bought the Most Expensive Sandwich"
    )
    assert stored == "Bought the Most Expensive Sandwich"
    assert not contains_first_person(stored)


async def test_create_clip_keeps_clean_titles_byte_identical(monkeypatch):
    stored = await _create_with_title(
        monkeypatch, "$400 Wagyu Sandwich With Real Gold"
    )
    assert stored == "$400 Wagyu Sandwich With Real Gold"
