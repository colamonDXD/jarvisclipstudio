"""Roman Hinglish transliteration for the hindi_podcast profile.

Whisper returns Hindi speech in Devanagari; viral Hindi podcast captions
are burned in Roman Hinglish. These tests pin the word mappings, the
word-by-word/timing-preserving contract, the Devanagari-dominance gate,
and the caption-builder integration (only hindi_podcast converts).
"""

import re
from pathlib import Path

import pytest

from src.media.captions import build_assemblyai_ass_subtitles
from src.media.hinglish import (
    devanagari_share,
    is_devanagari_dominant,
    romanize_caption_words,
    romanize_text,
    romanize_token,
)


# The required viral spellings from the research notes.
REQUIRED_MAPPINGS = {
    "आप": "aap",
    "कैसे": "kaise",
    "हैं": "hain",
    "क्या": "kya",
    "है": "hai",
    "मैं": "main",
    "नहीं": "nahin",
    "बहुत": "bahut",
    "अच्छा": "achha",
    "ठीक": "theek",
    "पैसा": "paisa",
    "और": "aur",
}

# Spellings produced by the general ITRANS normalization rules (no
# dictionary entry): schwa deletion, long-vowel handling, digraphs.
RULE_MAPPINGS = {
    "मुझे": "mujhe",
    "क्यों": "kyon",
    "काम": "kaam",
    "बात": "baat",
    "रहा": "raha",
    "कुछ": "kuchh",
    "अंदर": "andar",
    "दूध": "doodh",
    "ज्ञान": "gyaan",
    "पानी": "paani",
    "जिंदगी": "jindagi",
    "करोड़": "karod",
    "आज": "aaj",
    "संगीत": "sangeet",
    "कोई": "koi",
    "सीता": "seeta",
    "हिंदी": "hindi",
    "घर": "ghar",
    "दिन": "din",
    "किताब": "kitaab",
    "दोस्त": "dost",
    "समय": "samay",
    "झूठ": "jhooth",
    "छोटा": "chhota",
    "स्कूल": "skool",
    "मेरा": "mera",
    "उसका": "usaka",  # medial schwa is kept (conservative, documented)
    "लड़की": "ladaki",  # medial schwa is kept (conservative, documented)
}


@pytest.mark.parametrize("devanagari,hinglish", REQUIRED_MAPPINGS.items())
def test_required_word_mappings(devanagari, hinglish):
    assert romanize_token(devanagari) == hinglish


@pytest.mark.parametrize("devanagari,hinglish", RULE_MAPPINGS.items())
def test_rule_word_mappings(devanagari, hinglish):
    assert romanize_token(devanagari) == hinglish


def test_sentence_from_the_brief():
    words = [romanize_token(word) for word in "आप कैसे हैं".split()]
    assert " ".join(words) == "aap kaise hain"


def test_english_tokens_pass_through_untouched():
    for token in ("podcast", "interesting", "money", "100", "don't"):
        assert romanize_token(token) == token


def test_punctuation_around_a_word_is_preserved():
    assert romanize_token("नहीं,") == "nahin,"
    assert romanize_token("क्या?") == "kya?"
    assert romanize_token("बहुत।") == "bahut."


def test_devanagari_digits_become_ascii():
    assert romanize_token("१००") == "100"


def test_dominance_measurement():
    assert devanagari_share(["hello world"]) == 0.0
    assert devanagari_share(["आप कैसे"]) == 1.0
    assert is_devanagari_dominant(["आप कैसे हैं बहुत अच्छा"])
    assert not is_devanagari_dominant(["this podcast is mostly english आप"])
    assert not is_devanagari_dominant([""])


def _timed_words():
    return [
        {"text": "आप", "start": 0.0, "end": 0.4, "confidence": 0.98},
        {"text": "कैसे", "start": 0.4, "end": 0.9, "confidence": 0.97},
        {"text": "हैं", "start": 0.9, "end": 1.3, "confidence": 0.99},
        {"text": "podcast", "start": 1.5, "end": 1.9, "confidence": 0.95},
        {"text": "बहुत", "start": 1.9, "end": 2.4, "confidence": 0.98},
        {"text": "अच्छा", "start": 2.4, "end": 2.8, "confidence": 0.96},
    ]


def test_caption_words_convert_word_by_word_with_timings_untouched():
    words = _timed_words()
    snapshot = [dict(word) for word in words]

    converted = romanize_caption_words(words)

    assert [word["text"] for word in converted] == [
        "aap",
        "kaise",
        "hain",
        "podcast",  # English inside a Hindi transcript stays English
        "bahut",
        "achha",
    ]
    # Karaoke timing and every other field survive exactly.
    for before, after in zip(snapshot, converted):
        assert after["start"] == before["start"]
        assert after["end"] == before["end"]
        assert after["confidence"] == before["confidence"]
    # The caller's dicts are never mutated (they may be shared cache data).
    assert words == snapshot


def test_caption_words_latin_transcript_is_returned_unchanged():
    words = [
        {"text": "hello", "start": 0.0, "end": 0.4},
        {"text": "world", "start": 0.4, "end": 0.9},
    ]
    assert romanize_caption_words(words) is words


def test_caption_words_mostly_english_transcript_is_unchanged():
    words = [
        {"text": text, "start": float(i), "end": float(i) + 0.5}
        for i, text in enumerate(
            ["this", "podcast", "is", "mostly", "english", "आप"]
        )
    ]
    assert romanize_caption_words(words) is words


def test_missing_transliteration_library_falls_back_without_crashing(monkeypatch):
    monkeypatch.setattr("src.media.hinglish._load_sanscript", lambda: None)
    words = _timed_words()
    assert romanize_caption_words(words) is words
    assert romanize_text("आप कैसे हैं") == "आप कैसे हैं"


def test_romanize_text_only_when_devanagari_dominant():
    assert romanize_text("आप कैसे हैं") == "aap kaise hain"
    assert romanize_text("My Hindi Podcast Story") == "My Hindi Podcast Story"
    assert romanize_text("") == ""


# --- Caption-builder integration -------------------------------------------

HINDI_WORDS = [
    {"text": "आप", "start": 0.0, "end": 0.4, "confidence": 0.98},
    {"text": "कैसे", "start": 0.4, "end": 0.9, "confidence": 0.98},
    {"text": "हैं", "start": 0.9, "end": 1.3, "confidence": 0.98},
    {"text": "बहुत", "start": 1.5, "end": 1.9, "confidence": 0.98},
    {"text": "अच्छा", "start": 1.9, "end": 2.4, "confidence": 0.98},
    {"text": "है", "start": 2.4, "end": 2.8, "confidence": 0.98},
]

LATIN_WORDS = [
    {"text": "hello", "start": 0.0, "end": 0.4, "confidence": 0.98},
    {"text": "world", "start": 0.4, "end": 0.9, "confidence": 0.98},
]


@pytest.fixture(autouse=True)
def _no_emoji_probe(monkeypatch):
    monkeypatch.setattr(
        "src.media.captions.emoji_rendering_supported", lambda: False
    )


def _build_ass(tmp_path: Path, name: str, **kwargs) -> str:
    ass_path = tmp_path / f"{name}.ass"
    ok = build_assemblyai_ass_subtitles(
        tmp_path / "video.mp4",
        0.0,
        3.0,
        1080,
        1920,
        ass_path,
        caption_words=[dict(word) for word in kwargs.pop("words")],
        **kwargs,
    )
    assert ok
    return ass_path.read_text(encoding="utf-8")


def _dialogue_times(ass_text: str):
    times = []
    for line in ass_text.splitlines():
        if line.startswith("Dialogue:"):
            fields = line.split(",", 3)
            times.append((fields[1], fields[2]))
    return times


def _has_devanagari(text: str) -> bool:
    return any("\u0900" <= char <= "\u097f" for char in text)


def test_hindi_profile_burns_roman_hinglish_captions(tmp_path):
    ass = _build_ass(
        tmp_path, "hindi", words=HINDI_WORDS, profile_name="hindi_podcast"
    )
    assert "aap" in ass and "kaise" in ass and "bahut" in ass
    assert not _has_devanagari(ass)


def test_other_profiles_keep_devanagari_captions(tmp_path):
    ass = _build_ass(
        tmp_path, "podcast", words=HINDI_WORDS, profile_name="podcast_news"
    )
    assert "आप" in ass


def test_default_profile_keeps_devanagari_captions(tmp_path):
    ass = _build_ass(tmp_path, "default", words=HINDI_WORDS)
    assert "आप" in ass


def test_hindi_profile_with_latin_transcript_is_unchanged(tmp_path):
    ass = _build_ass(
        tmp_path, "latin", words=LATIN_WORDS, profile_name="hindi_podcast"
    )
    assert "hello" in ass and "world" in ass


def test_romanization_preserves_karaoke_event_times(tmp_path):
    hindi = _build_ass(
        tmp_path, "hindi_times", words=HINDI_WORDS, profile_name="hindi_podcast"
    )
    plain = _build_ass(
        tmp_path, "plain_times", words=HINDI_WORDS, profile_name="podcast_news"
    )
    assert _dialogue_times(hindi) == _dialogue_times(plain)
    assert _dialogue_times(hindi)  # there are events to compare


def test_hindi_profile_romanizes_devanagari_hook_title(tmp_path):
    ass = _build_ass(
        tmp_path,
        "hook_hindi",
        words=HINDI_WORDS,
        profile_name="hindi_podcast",
        hook_title="बहुत पैसा",
    )
    assert "bahut" in ass and "paisa" in ass
    assert not _has_devanagari(ass)


def test_hook_title_in_latin_is_left_alone(tmp_path):
    ass = _build_ass(
        tmp_path,
        "hook_latin",
        words=HINDI_WORDS,
        profile_name="hindi_podcast",
        hook_title="Money Secrets",
    )
    # The title renderer wraps each word in its own colour span, so the
    # words are present but never as one contiguous string.
    assert "Money" in ass and "Secrets" in ass


def test_ass_dialogue_regex_sanity(tmp_path):
    # Guard the timing-comparison helper itself: ASS times look like 0:00:00.40.
    ass = _build_ass(
        tmp_path, "sanity", words=HINDI_WORDS, profile_name="hindi_podcast"
    )
    for start, end in _dialogue_times(ass):
        assert re.fullmatch(r"\d+:\d{2}:\d{2}\.\d{2}", start)
        assert re.fullmatch(r"\d+:\d{2}:\d{2}\.\d{2}", end)
