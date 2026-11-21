"""Caption-word polish (media.caption_polish).

The tidy pass is PeakCut's ``_fix_caption_words`` contract: exactly one
output entry per input word, timings never touched, ANY anomaly keeps
the original words. The ladder is faked at the boundary
(``fix_caption_words(..., chat=...)`` / ``caption_polish._gateway_chat``)
— no live calls anywhere in this suite.
"""
from __future__ import annotations

import json

import pytest

from src.media import caption_polish as P


def _word(text, start, end, **extra):
    return {"text": text, "start": start, "end": end, **extra}


def _words(texts):
    return [_word(t, i * 100, i * 100 + 90) for i, t in enumerate(texts)]


def _chat_returning(entries):
    def chat(prompt):
        return json.dumps(entries)

    return chat


# ---------------------------------------------------------------------------
# The tidy pass: the happy path (the brief's proof cases)
# ---------------------------------------------------------------------------


def test_planted_asr_errors_are_fixed_and_timings_untouched():
    words = _words(["their", "going", "too", "the", "gym", "at", "eleven"])
    chat = _chat_returning(["They're", "going", "to", "the", "gym", "at", "11"])
    out = P.fix_caption_words(words, chat=chat)
    assert [w["text"] for w in out] == ["They're", "going", "to", "the", "gym", "at", "11"]
    assert [(w["start"], w["end"]) for w in out] == [(w["start"], w["end"]) for w in words]
    assert [w["text"] for w in words][0] == "their"  # input never mutated


def test_kai_style_mumble_line_is_fixed():
    words = _words(["yo", "we", "gonna", "go", "crazy", "wen", "he", "pulls", "up"])
    chat = _chat_returning(["Yo", "we", "going to", "go", "crazy", "when", "he", "pulls", "up"])
    out = P.fix_caption_words(words, chat=chat)
    assert [w["text"] for w in out] == [
        "Yo", "we", "going to", "go", "crazy", "when", "he", "pulls", "up",
    ]
    assert [(w["start"], w["end"]) for w in out] == [(w["start"], w["end"]) for w in words]


def test_merged_number_drops_the_swallowed_word_and_keeps_the_timing():
    words = _words(["it", "costs", "twenty", "five", "dollars", "total"])
    chat = _chat_returning(["It", "costs", "25", "", "dollars", "total"])
    out = P.fix_caption_words(words, chat=chat)
    assert [w["text"] for w in out] == ["It", "costs", "25", "dollars", "total"]
    kept = {w["text"]: (w["start"], w["end"]) for w in out}
    assert kept["25"] == (200, 290)  # "twenty"'s slot, not "five"'s


def test_other_word_fields_survive_a_fix():
    words = _words(["their", "going", "home"])
    words[0]["probability"] = 0.42
    out = P.fix_caption_words(words, chat=_chat_returning(["They're", "going", "home"]))
    assert out[0]["probability"] == 0.42
    assert out[1] is words[1]  # unchanged words are the same dict


def test_answer_wrapped_in_prose_still_parses():
    words = _words(["their", "going", "home"])
    chat = lambda prompt: 'Here you go:\n["They\'re", "going", "home"]\nDone.'
    out = P.fix_caption_words(words, chat=chat)
    assert [w["text"] for w in out] == ["They're", "going", "home"]


# ---------------------------------------------------------------------------
# The anomaly paths: originals kept, silently
# ---------------------------------------------------------------------------


def test_model_failure_keeps_originals():
    words = _words(["their", "going", "home"])
    assert P.fix_caption_words(words, chat=lambda prompt: None) is words


def test_model_exception_keeps_originals():
    def boom(prompt):
        raise RuntimeError("gateway down")

    words = _words(["their", "going", "home"])
    assert P.fix_caption_words(words, chat=boom) is words


def test_wrong_entry_count_keeps_originals():
    words = _words(["their", "going", "home", "now"])
    chat = _chat_returning(["They're", "going", "home"])
    assert P.fix_caption_words(words, chat=chat) is words


def test_unparseable_answer_keeps_originals():
    words = _words(["their", "going", "home"])
    assert P.fix_caption_words(words, chat=lambda prompt: "no array here") is words
    assert P.fix_caption_words(words, chat=lambda prompt: '{"not": "a list"}') is words


def test_everything_dropped_keeps_originals():
    words = _words(["their", "going", "home"])
    assert P.fix_caption_words(words, chat=_chat_returning(["", "", ""])) is words


def test_a_rewrite_is_rejected_per_word_while_real_fixes_land():
    words = _words(["hello", "their", "friend"])
    chat = _chat_returning(["zebra", "They're", "friend"])
    out = P.fix_caption_words(words, chat=chat)
    assert [w["text"] for w in out] == ["hello", "They're", "friend"]


def test_a_wildly_long_entry_is_rejected():
    words = _words(["ok", "their", "friend"])
    chat = _chat_returning(["ok", "they are going to be there for sure", "friend"])
    out = P.fix_caption_words(words, chat=chat)
    assert out[1]["text"] == "their"


def test_devanagari_words_are_never_translated_or_transliterated_by_the_fixer():
    words = _words(["यह", "बहुत", "crazy", "है"])
    chat = _chat_returning(["this", "very", "crazy", "is"])
    out = P.fix_caption_words(words, chat=chat)
    assert [w["text"] for w in out] == ["यह", "बहुत", "crazy", "है"]


def test_fewer_than_three_words_never_calls_the_model():
    calls = []

    def chat(prompt):
        calls.append(prompt)
        return "[]"

    words = _words(["their", "going"])
    assert P.fix_caption_words(words, chat=chat) is words
    assert calls == []


# ---------------------------------------------------------------------------
# The combined polish entry: switches, order, off = identical
# ---------------------------------------------------------------------------


def test_switch_is_off_by_default(monkeypatch):
    monkeypatch.delenv("CAPTION_WORD_FIX", raising=False)
    assert P.caption_word_fix_enabled() is False
    assert P.polish_needed() is False


def test_both_switches_off_returns_the_same_list_and_never_calls(monkeypatch):
    monkeypatch.delenv("CAPTION_WORD_FIX", raising=False)
    monkeypatch.delenv("TRANSLIT_HINGLISH", raising=False)

    def boom(prompt):
        raise AssertionError("the ladder must not be called with both switches off")

    monkeypatch.setattr(P, "_gateway_chat", boom)
    words = _words(["their", "going", "home"])
    assert P.polish_caption_words(words) is words


def test_fix_only_polish_fixes_words(monkeypatch):
    monkeypatch.setenv("CAPTION_WORD_FIX", "on")
    monkeypatch.delenv("TRANSLIT_HINGLISH", raising=False)
    words = _words(["their", "going", "home"])
    chat = _chat_returning(["They're", "going", "home"])
    out = P.polish_caption_words(words, chat=chat)
    assert [w["text"] for w in out] == ["They're", "going", "home"]


def test_translit_only_polish_converts_hindi(monkeypatch):
    monkeypatch.setenv("TRANSLIT_HINGLISH", "on")
    monkeypatch.delenv("CAPTION_WORD_FIX", raising=False)
    words = _words(["मुझे", "बहुत", "अच्छा", "लगा"])
    out = P.polish_caption_words(words)
    assert [w["text"] for w in out] == ["mujhe", "bahut", "accha", "laga"]
    assert [(w["start"], w["end"]) for w in out] == [(w["start"], w["end"]) for w in words]


def test_translit_runs_before_the_fixer(monkeypatch):
    monkeypatch.setenv("TRANSLIT_HINGLISH", "on")
    monkeypatch.setenv("CAPTION_WORD_FIX", "on")
    seen = {}

    def chat(prompt):
        seen["prompt"] = prompt
        return json.dumps(["mujhe", "bahut", "accha", "lagaa", "their", "best"])

    words = _words(["मुझे", "बहुत", "अच्छा", "लगा", "their", "best"])
    out = P.polish_caption_words(words, chat=chat)
    # The fixer was shown the transliterated words, not the Devanagari.
    assert "bahut" in seen["prompt"]
    assert "बहुत" not in seen["prompt"]
    assert [w["text"] for w in out] == ["mujhe", "bahut", "accha", "lagaa", "their", "best"]


def test_polish_on_an_english_list_with_translit_on_is_byte_identical(monkeypatch):
    monkeypatch.setenv("TRANSLIT_HINGLISH", "on")
    monkeypatch.delenv("CAPTION_WORD_FIX", raising=False)
    words = _words(["hello", "world", "this", "is", "fine"])
    out = P.polish_caption_words(words)
    assert out is words
    assert [w["text"] for w in out] == ["hello", "world", "this", "is", "fine"]
