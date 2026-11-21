"""Caption pacing cap (Quality Director item 5).

Word-count chunking alone let one caption sit on screen for ~10 s on a
real clip. Chunks are now also split by display duration at word
boundaries: ~3.5 s by default, ~2.5 s for fast-pace profiles. Karaoke
word highlighting must survive the split (it is driven by global word
indices), and the timings that actually render are recorded into the
layout report so QC can verify them on the finished clip.
"""

import re

import pytest

from src.auto_director.edl import load_profile
from src.media import captions
from src.media.captions import (
    CAPTION_HOLD_DEFAULT_SECONDS,
    CAPTION_HOLD_FAST_SECONDS,
    caption_hold_cap_for_profile,
    split_chunk_spans_by_duration,
)


def _words(*items):
    return [
        {"text": text, "start": start, "end": end, "confidence": 0.99}
        for text, start, end in items
    ]


# --------------------------------------------------------------------------
# Pure span splitting
# --------------------------------------------------------------------------


def test_slow_chunk_splits_at_word_boundaries_under_cap():
    # 1.5 s words: a pair displays for 3.0 s (fits the 3.5 s cap), a
    # triple would display for 4.5 s (does not).
    words = _words(
        ("one", 0.0, 1.5), ("two", 1.5, 3.0), ("three", 3.0, 4.5),
        ("four", 4.5, 6.0), ("five", 6.0, 7.5),
    )
    spans = split_chunk_spans_by_duration(words, [(0, 5, 1.0)], 3.5)
    assert spans == [(0, 2, 1.0), (2, 4, 1.0), (4, 5, 1.0)]
    for start, stop, _scale in spans:
        assert words[stop - 1]["end"] - words[start]["start"] <= 3.5


def test_chunk_under_cap_is_untouched():
    words = _words(("a", 0.0, 1.0), ("b", 1.0, 2.0))
    assert split_chunk_spans_by_duration(words, [(0, 2, 0.9)], 3.5) == [(0, 2, 0.9)]


def test_single_word_longer_than_cap_stays_whole():
    words = _words(("held", 0.0, 9.0), ("next", 9.0, 9.5))
    spans = split_chunk_spans_by_duration(words, [(0, 2, 1.0)], 3.5)
    assert spans == [(0, 1, 1.0), (1, 2, 1.0)]


def test_zero_duration_words_never_split():
    words = _words(("a", 0.0, 0.0), ("b", 0.0, 0.0), ("c", 0.0, 0.0))
    assert split_chunk_spans_by_duration(words, [(0, 3, 1.0)], 3.5) == [(0, 3, 1.0)]


def test_non_positive_cap_disables_splitting():
    words = _words(("a", 0.0, 5.0), ("b", 5.0, 10.0))
    assert split_chunk_spans_by_duration(words, [(0, 2, 1.0)], 0) == [(0, 2, 1.0)]


def test_scale_is_carried_into_split_spans():
    words = _words(("a", 0.0, 3.0), ("b", 3.0, 6.0))
    assert split_chunk_spans_by_duration(words, [(0, 2, 0.75)], 3.5) == [
        (0, 1, 0.75),
        (1, 2, 0.75),
    ]


# --------------------------------------------------------------------------
# Profile -> cap
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "profile_name,expected",
    [
        ("podcast_news", CAPTION_HOLD_DEFAULT_SECONDS),   # 10-15 cuts/min
        ("education_tutorial", CAPTION_HOLD_DEFAULT_SECONDS),  # 12-18
        ("gaming", CAPTION_HOLD_FAST_SECONDS),            # 15-30
        ("football_edit", CAPTION_HOLD_FAST_SECONDS),     # 25-40
        ("anime_funk_edit", CAPTION_HOLD_FAST_SECONDS),   # 60-120
    ],
)
def test_cap_comes_from_the_real_profiles(profile_name, expected):
    assert caption_hold_cap_for_profile(load_profile(profile_name)) == expected


def test_cap_defaults_for_missing_or_malformed_profiles():
    assert caption_hold_cap_for_profile(None) == CAPTION_HOLD_DEFAULT_SECONDS
    assert caption_hold_cap_for_profile({}) == CAPTION_HOLD_DEFAULT_SECONDS
    assert (
        caption_hold_cap_for_profile({"pacing": {"target_cuts_per_min": "fast"}})
        == CAPTION_HOLD_DEFAULT_SECONDS
    )


# --------------------------------------------------------------------------
# Builder level: the ASS that renders
# --------------------------------------------------------------------------


def _build(tmp_path, monkeypatch, words, **kwargs):
    monkeypatch.setattr(captions, "emoji_rendering_supported", lambda: False)
    output = tmp_path / "captions.ass"
    report = {}
    assert captions.build_assemblyai_ass_subtitles(
        tmp_path / "missing.mp4", 0.0, 30.0, 1080, 1920, output,
        caption_words=words, caption_layout_report=report, **kwargs,
    )
    return output.read_text(encoding="utf-8"), report


def _ass_seconds(stamp):
    hours, minutes, rest = stamp.split(":")
    return int(hours) * 3600 + int(minutes) * 60 + float(rest)


def _dialogue_events(content):
    events = []
    for line in content.splitlines():
        match = re.match(r"Dialogue: \d,([^,]+),([^,]+),", line)
        if match:
            events.append((_ass_seconds(match.group(1)), _ass_seconds(match.group(2))))
    return events


# Four slow words, one word-count chunk (the "minimal"/fade template shows
# one event per chunk): 12 s of speech that used to be ONE caption.
SLOW_WORDS = _words(
    ("alpha", 0.0, 3.0), ("bravo", 3.0, 6.0),
    ("charlie", 6.0, 9.0), ("delta", 9.0, 12.0),
)


def test_fade_template_events_never_exceed_default_cap(tmp_path, monkeypatch):
    content, report = _build(
        tmp_path, monkeypatch, SLOW_WORDS, caption_template="minimal"
    )
    events = _dialogue_events(content)
    assert len(events) == 4  # one chunk per word at 3 s apiece under a 3.5 s cap
    assert all(end - start <= 3.5 + 1e-6 for start, end in events)
    chunks = report["caption_chunks"]
    assert len(chunks) == 4
    assert all(c["end"] - c["start"] <= 3.5 + 1e-6 for c in chunks)
    assert report["caption_hold_cap_seconds"] == CAPTION_HOLD_DEFAULT_SECONDS


def test_explicit_fast_cap_splits_harder(tmp_path, monkeypatch):
    # 1.3 s words: a pair displays 2.6 s — fine under the 3.5 s default,
    # over the 2.5 s fast cap, so the fast cap forces single-word chunks.
    words = _words(
        ("alpha", 0.0, 1.3), ("bravo", 1.3, 2.6),
        ("charlie", 2.6, 3.9), ("delta", 3.9, 5.2),
    )
    content, report = _build(
        tmp_path, monkeypatch, words,
        caption_template="minimal", max_chunk_seconds=2.5,
    )
    events = _dialogue_events(content)
    assert len(events) == 4
    assert all(end - start <= 2.5 + 1e-6 for start, end in events)
    assert report["caption_hold_cap_seconds"] == 2.5
    assert len(report["caption_chunks"]) == 4


def test_karaoke_chunks_split_and_stay_word_synced(tmp_path, monkeypatch):
    content, report = _build(
        tmp_path, monkeypatch, SLOW_WORDS, caption_template="default"
    )
    # Karaoke still emits one event per word (highlight intact) ...
    events = _dialogue_events(content)
    assert len(events) == len(SLOW_WORDS)
    # ... but the recorded display chunks respect the cap.
    chunks = report["caption_chunks"]
    assert len(chunks) == 4
    assert all(c["end"] - c["start"] <= 3.5 + 1e-6 for c in chunks)
    # Every word's text still appears in the ASS.
    for word in SLOW_WORDS:
        assert word["text"] in content


def test_editor_placements_skip_the_duration_split(tmp_path, monkeypatch):
    # caption_placements are indexed by the pre-split chunking; with them
    # present the builder must keep the original chunks (the recorded
    # timings then honestly show the longer hold for QC to flag).
    _content, report = _build(
        tmp_path, monkeypatch, SLOW_WORDS,
        caption_template="minimal",
        caption_placements=[{"x": 540, "y": 1500, "font_px": 60}],
    )
    assert len(report["caption_chunks"]) == 1
    assert report["caption_chunks"][0]["end"] == 12.0
