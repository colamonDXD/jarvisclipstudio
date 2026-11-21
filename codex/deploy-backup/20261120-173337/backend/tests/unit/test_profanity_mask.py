"""Swear words are masked in burned-in captions and titles (owner rule)."""
from __future__ import annotations

import pytest

from src.media.captions import escape_ass_text
from src.media.profanity import mask_profanity


@pytest.mark.parametrize("raw,masked", [
    ("FUCK YOU!", "*** YOU!"),
    ("what the fuck", "what the ***"),
    ("Fucking hell bro", "*** hell bro"),
    ("motherfucker", "***"),
    ("this is shit, bitches", "this is ***, ***"),
    ("you dumbass", "you ***"),
    ("kick his ass", "kick his ***"),
    ("damn", "***"),
])
def test_swear_words_become_full_stars(raw, masked):
    assert mask_profanity(raw) == masked


def test_full_stars_for_every_word_length_in_one_sentence():
    # Lengths 3 (ass), 4 (shit/fuck/damn), 7 (asshole) and 12 (motherfucker)
    # all collapse to exactly "***"; every other word and every punctuation
    # mark around them is byte-identical.
    raw = "Well, shit! That motherfucker said fuck — damn, what an asshole move, ass."
    assert mask_profanity(raw) == "Well, ***! That *** said *** — ***, what an *** move, ***."


@pytest.mark.parametrize("clean", ["class assignment", "pass the assassin", "hello world", "shiitake", "cocktail"])
def test_ordinary_words_are_untouched(clean):
    assert mask_profanity(clean) == clean


def test_every_burned_in_caption_goes_through_the_mask():
    assert escape_ass_text("FUCK YOU!") == "*** YOU!"
    assert escape_ass_text("{x}") == r"\{x\}"


def test_switch_and_extra_words(monkeypatch):
    monkeypatch.setenv("CAPTION_PROFANITY_FILTER", "off")
    assert mask_profanity("FUCK") == "FUCK"
    monkeypatch.setenv("CAPTION_PROFANITY_FILTER", "on")
    monkeypatch.setenv("CAPTION_PROFANITY_EXTRA", "bruh")
    assert mask_profanity("bruh moment") == "*** moment"
