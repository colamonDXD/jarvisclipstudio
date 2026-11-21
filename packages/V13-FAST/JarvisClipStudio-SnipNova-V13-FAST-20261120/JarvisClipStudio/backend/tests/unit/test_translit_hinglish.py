"""Devanagari -> Hinglish transliteration (media.translit, from NeonClipping).

The engine expectations are NeonClipping's own documented ones:
dictionary spellings for common words, schwa deletion ("kamal, not
kamala"; "samajhna, not samajahana"), Latin tokens untouched.
"""
from __future__ import annotations

import pytest

from src.media import translit as T


def _word(text, start, end, **extra):
    return {"text": text, "start": start, "end": end, **extra}


# ---------------------------------------------------------------------------
# The engine (NeonClipping translit.py)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "devanagari, hinglish",
    [
        ("मुझे", "mujhe"),
        ("है", "hai"),
        ("नहीं", "nahi"),
        ("क्या", "kya"),
        ("बहुत", "bahut"),
        ("अच्छा", "accha"),
        ("भाई", "bhai"),
        ("वीडियो", "video"),
    ],
)
def test_dictionary_words_use_the_casual_spelling(devanagari, hinglish):
    assert T.word_to_hinglish(devanagari) == hinglish


def test_schwa_deletion_matches_the_upstream_examples():
    assert T.word_to_hinglish("कमल") == "kamal"  # not "kamala"
    assert T.word_to_hinglish("समझना") == "samajhna"  # not "samajahana"
    assert T.word_to_hinglish("लड़का") == "ladka"  # medial schwa drops too


def test_punctuation_is_kept_around_the_converted_core():
    assert T.to_hinglish("बहुत!") == "bahut!"
    assert T.to_hinglish("क्या?") == "kya?"
    assert T.to_hinglish("अच्छा।") == "accha."  # danda reads as a full stop


def test_latin_tokens_pass_through_unchanged():
    assert T.to_hinglish("hello") == "hello"
    assert T.to_hinglish("GTA") == "GTA"
    assert T.to_hinglish("100") == "100"


# ---------------------------------------------------------------------------
# The word-dict layer: detection, timings, byte-identical English
# ---------------------------------------------------------------------------


def test_hindi_sentence_converts_with_timings_preserved():
    texts = ["मुझे", "बहुत", "अच्छा", "लगा", "यह", "वीडियो"]
    words = [_word(t, i * 100, i * 100 + 90) for i, t in enumerate(texts)]
    out = T.transliterate_words(words)
    assert [w["text"] for w in out] == ["mujhe", "bahut", "accha", "laga", "yeh", "video"]
    assert [(w["start"], w["end"]) for w in out] == [(w["start"], w["end"]) for w in words]
    assert [w["text"] for w in words] == texts  # input never mutated


def test_english_word_list_is_returned_byte_identical():
    words = [_word(t, i * 100, i * 100 + 90) for i, t in enumerate(["hello", "world", "this", "is"])]
    out = T.transliterate_words(words)
    assert out is words  # the same list, not a copy
    assert [w["text"] for w in out] == ["hello", "world", "this", "is"]


def test_below_the_share_threshold_nothing_converts():
    # 1 of 4 words Devanagari = 0.25 < TRANSLIT_MIN_DEVANAGARI_SHARE (0.5)
    words = [_word(t, 0, 1) for t in ["hello", "बहुत", "world", "today"]]
    assert T.devanagari_word_share(words) == 0.25
    assert T.transliterate_words(words) is words


def test_at_the_share_threshold_the_list_converts():
    words = [_word(t, i, i + 1) for i, t in enumerate(["मुझे", "बहुत", "hello", "world"])]
    assert T.devanagari_word_share(words) == 0.5
    out = T.transliterate_words(words)
    assert [w["text"] for w in out] == ["mujhe", "bahut", "hello", "world"]


def test_whisper_shaped_dicts_use_the_word_key():
    words = [{"word": t, "start": i, "end": i + 1} for i, t in enumerate(["मुझे", "बहुत", "अच्छा"])]
    out = T.transliterate_words(words)
    assert [w["word"] for w in out] == ["mujhe", "bahut", "accha"]
    assert [(w["start"], w["end"]) for w in out] == [(0, 1), (1, 2), (2, 3)]


def test_empty_list_is_unchanged():
    assert T.transliterate_words([]) == []


# ---------------------------------------------------------------------------
# The switch
# ---------------------------------------------------------------------------


def test_switch_is_off_by_default(monkeypatch):
    monkeypatch.delenv("TRANSLIT_HINGLISH", raising=False)
    assert T.translit_enabled() is False


def test_switch_turns_on(monkeypatch):
    monkeypatch.setenv("TRANSLIT_HINGLISH", "on")
    assert T.translit_enabled() is True
    monkeypatch.setenv("TRANSLIT_HINGLISH", "off")
    assert T.translit_enabled() is False
