"""Caption quality audit (quality milestone, workstream E).

Measured against synthetic fixtures with known ground-truth word timings:
timing accuracy, no overlapping cues, no highlight lingering across long
pauses, sentence/pause-aware segmentation, ASS timestamp validity,
platform bottom safe zone, emphasis density, and a no-fabrication guard
(caption text must be exactly the spoken words, punctuation included).
"""

import re

import pytest

from src.caption_templates import CAPTION_TEMPLATES, get_template
from src.emoji_captions import annotate_caption_words
from src.media import captions
from src.media.captions import (
    ass_timestamp,
    build_assemblyai_ass_subtitles,
    get_safe_vertical_position,
    get_scaled_font_size,
)


def _words(*items):
    return [
        {"text": text, "start": start, "end": end, "confidence": 0.99}
        for text, start, end in items
    ]


def _build(tmp_path, monkeypatch, words, template="default", **kwargs):
    monkeypatch.setattr(captions, "emoji_rendering_supported", lambda: False)
    output = tmp_path / "captions.ass"
    report = {}
    assert build_assemblyai_ass_subtitles(
        tmp_path / "missing.mp4", 0.0, 60.0, 1080, 1920, output,
        caption_template=template, caption_words=[dict(w) for w in words],
        caption_layout_report=report, **kwargs,
    )
    return output.read_text(encoding="utf-8"), report


def _ass_seconds(stamp):
    hours, minutes, rest = stamp.split(":")
    return int(hours) * 3600 + int(minutes) * 60 + float(rest)


def _events(content):
    events = []
    for line in content.splitlines():
        match = re.match(r"Dialogue: \d,([^,]+),([^,]+),Default,,0,0,0,,(.*)", line)
        if match:
            text = re.sub(r"\{[^}]*\}", "", match.group(3))
            events.append((_ass_seconds(match.group(1)), _ass_seconds(match.group(2)), text))
    return events


# ---------------------------------------------------------------------------
# ASS timestamps: centisecond rounding must never emit "60.00" seconds.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "seconds,expected",
    [
        (0.0, "0:00:00.00"),
        (59.994, "0:00:59.99"),
        (59.999, "0:01:00.00"),
        (3599.999, "1:00:00.00"),
        (61.005, "0:01:01.00"),
        (61.006, "0:01:01.01"),
    ],
)
def test_ass_timestamp_never_overflows_the_seconds_field(seconds, expected):
    assert ass_timestamp(seconds) == expected


# ---------------------------------------------------------------------------
# Timing accuracy: karaoke event starts equal word starts (ASS is
# centisecond-quantised, so allow one centisecond), and events never
# overlap even when the ASR word timings themselves overlap.
# ---------------------------------------------------------------------------


def test_karaoke_event_starts_match_word_starts(tmp_path, monkeypatch):
    words = _words(*[(f"w{i}", i * 0.4, i * 0.4 + 0.4) for i in range(8)])
    content, _report = _build(tmp_path, monkeypatch, words)
    events = _events(content)
    assert len(events) == len(words)
    for (start, _end, _text), word in zip(events, words):
        assert abs(start - word["start"]) <= 0.01 + 1e-9


def test_overlapping_asr_words_do_not_produce_overlapping_cues(tmp_path, monkeypatch):
    # ASR overlap: each word ends after the next one starts.
    words = _words(
        ("alpha", 0.0, 1.2), ("bravo", 1.0, 2.0),
        ("charlie", 1.9, 3.0), ("delta", 2.8, 4.0),
    )
    for template in ("default", "minimal"):
        content, _report = _build(tmp_path, monkeypatch, words, template=template)
        events = _events(content)
        for previous, current in zip(events, events[1:]):
            assert current[0] >= previous[1] - 1e-9, (template, events)


def test_karaoke_highlight_does_not_linger_across_a_pause(tmp_path, monkeypatch):
    # A 0.6 s in-chunk gap: too short to split the chunk, too long to
    # keep the previous word lit. Before the fix the "hello" event ran
    # 0.00-1.60 (the highlight lingered for the whole gap).
    words = _words(
        ("hello", 0.0, 1.0), ("world", 1.6, 2.0),
        ("again", 2.0, 2.5), ("now", 2.5, 3.0),
    )
    content, _report = _build(tmp_path, monkeypatch, words)
    events = _events(content)
    hello_event = next(event for event in events if event[2].startswith("hello"))
    assert hello_event[1] == pytest.approx(
        1.0 + captions.KARAOKE_GAP_BRIDGE_SECONDS, abs=0.011
    )


def test_karaoke_micro_gap_is_bridged_without_flicker(tmp_path, monkeypatch):
    words = _words(
        ("hello", 0.0, 1.0), ("world", 1.2, 2.0),
        ("again", 2.0, 2.5), ("now", 2.5, 3.0),
    )
    content, _report = _build(tmp_path, monkeypatch, words)
    events = _events(content)
    hello_event = events[0]
    assert hello_event[1] == pytest.approx(1.2, abs=0.011)


# ---------------------------------------------------------------------------
# Segmentation: a chunk must not straddle a sentence end, and a long
# pause is a natural break even when the word count would allow more.
# ---------------------------------------------------------------------------


def test_chunks_break_at_sentence_ends(tmp_path, monkeypatch):
    texts = "Hello world. How are you today? I am fine thanks so much".split()
    words = _words(*[(t, i * 0.4, i * 0.4 + 0.35) for i, t in enumerate(texts)])
    _content, report = _build(tmp_path, monkeypatch, words)
    chunks = report["caption_chunks"]
    # [Hello world.] [How are you today?] [I am fine thanks] [so much]:
    # sentence breaks first, then the 4-word cap splits the long sentence.
    assert len(chunks) == 4
    # No chunk contains a sentence terminator before its final word.
    cursor = 0
    for chunk in chunks:
        count = sum(
            1 for w in words if chunk["start"] - 1e-9 <= w["start"] < chunk["end"]
        )
        chunk_words = words[cursor:cursor + count]
        cursor += count
        for word in chunk_words[:-1]:
            assert not re.search(r"[.!?]", word["text"]), chunk_words


def test_chunks_break_at_long_pauses(tmp_path, monkeypatch):
    words = _words(
        ("before", 0.0, 0.4), ("pause", 0.4, 0.8),
        ("after", 2.3, 2.7), ("gap", 2.7, 3.1),
    )
    _content, report = _build(tmp_path, monkeypatch, words)
    assert len(report["caption_chunks"]) == 2
    assert report["caption_chunks"][0]["end"] == pytest.approx(0.8)


def test_editor_placements_keep_grid_chunking(tmp_path, monkeypatch):
    # Placements are indexed by the word-count grid; segmentation changes
    # must not move them.
    texts = "Hello world. How are you today?".split()
    words = _words(*[(t, i * 0.4, i * 0.4 + 0.35) for i, t in enumerate(texts)])
    _content, report = _build(
        tmp_path, monkeypatch, words,
        caption_placements=[{"x": 540, "y": 1500, "font_px": 60}] * 2,
    )
    assert len(report["caption_chunks"]) == 2  # grid chunks of 4 + 2


# ---------------------------------------------------------------------------
# Safe zone: on a 1080x1920 canvas every stock template's rendered
# caption bottom edge must stay above the platform bottom chrome
# (bottom 18% of the frame: TikTok/Reels/Shorts description + nav).
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("template_name", sorted(CAPTION_TEMPLATES))
def test_template_caption_bottom_edge_clears_platform_chrome(template_name):
    template = get_template(template_name)
    font_px = get_scaled_font_size(int(template["font_size"]), 1080)
    est_height = int(font_px * 1.5)
    centre = get_safe_vertical_position(1920, est_height, float(template["position_y"]))
    bottom_edge = centre + est_height // 2
    assert bottom_edge <= 1920 * (1 - captions.PLATFORM_BOTTOM_SAFE_FRACTION) + 1


def test_low_caption_position_is_clamped_into_the_safe_zone():
    # classic_box asks for 0.86, whose bottom edge used to land at 86%.
    centre = get_safe_vertical_position(1920, 75, 0.86)
    assert centre + 75 // 2 <= 1920 * (1 - captions.PLATFORM_BOTTOM_SAFE_FRACTION) + 1


# ---------------------------------------------------------------------------
# Highlighting: emphasis must stay selective — never two emphasised
# words side by side, and never most of a sentence.
# ---------------------------------------------------------------------------


def test_emphasis_never_highlights_adjacent_words():
    sentence = "this is the best and most important secret you must remember"
    words = _words(*[(t, i * 0.4, i * 0.4 + 0.35) for i, t in enumerate(sentence.split())])
    _emoji, emphasis = annotate_caption_words(words)
    ordered = sorted(emphasis)
    assert all(b - a >= 2 for a, b in zip(ordered, ordered[1:]))
    assert len(emphasis) <= len(words) // 2
    # The genuinely strong keywords survive the thinning.
    texts = {words[i]["text"] for i in emphasis}
    assert "secret" in texts


def test_emphasis_keeps_numbers_and_power_words():
    sentence = "this changed everything for 90% of people"
    words = _words(*[(t, i * 0.5, i * 0.5 + 0.45) for i, t in enumerate(sentence.split())])
    _emoji, emphasis = annotate_caption_words(words)
    texts = {words[i]["text"] for i in emphasis}
    assert "90%" in texts
    assert "of" not in texts


# ---------------------------------------------------------------------------
# No fabrication: the rendered caption text is exactly the spoken
# words, punctuation and casing included (non-uppercase template).
# ---------------------------------------------------------------------------


def test_caption_text_matches_spoken_words_exactly(tmp_path, monkeypatch):
    words = _words(
        ("Hello,", 0.0, 0.4), ("world.", 0.4, 0.8),
        ("It's", 0.8, 1.2), ("$5,000", 1.2, 1.6),
    )
    content, _report = _build(tmp_path, monkeypatch, words, template="tiktok")
    # Karaoke repeats the whole chunk line on every word event; the set of
    # distinct line texts is the caption text a viewer reads.
    line_texts = list(dict.fromkeys(text for _s, _e, text in _events(content)))
    shown = [token for text in line_texts for token in text.split()]
    assert shown == [w["text"] for w in words]
    assert "Hello," in shown and "world." in shown
