"""Step 4: visual story moments for selection, payoff extension (clip1 root cause #1c)."""
from __future__ import annotations

import numpy as np

from src.auto_director import story_events as se

N_STORY = len(se.STORY_LABELS)
N_ALL = N_STORY + len(se.BACKGROUND_LABELS)


def _row(story_label: str | None, margin: float = 0.05):
    row = np.zeros(N_ALL)
    row[N_STORY:] = 0.05  # background
    if story_label:
        row[list(se.STORY_LABELS).index(story_label)] = 0.05 + margin
    return row


def _words(spec):
    return [{"text": t, "start": s, "end": e} for t, s, e in spec]


def test_spans_merge_consecutive_story_samples_and_drop_short_ones():
    times = [0, 2, 4, 6, 8, 10, 12, 14, 16, 30]
    rows = [_row(None), _row("stage_speech"), _row("stage_speech"), _row("award"),
            _row("stage_speech"), _row(None), _row(None), _row(None), _row(None), _row("laugh")]
    spans = se.spans_from_scores(times, np.array(rows))
    assert len(spans) == 1  # the lone 'laugh' sample is shorter than MIN_SPAN_SEC
    assert (spans[0].start, spans[0].end, spans[0].label) == (2, 10.0, "stage_speech")


def test_event_window_prefers_a_sustained_quiet_moment_and_starts_on_its_setup_line():
    spans = [se.EventSpan(94, 100, "celebrate", 0.058), se.EventSpan(110, 136, "stage_speech", 0.024),
             se.EventSpan(142, 146, "applause", 0.032), se.EventSpan(332, 378, "award", 0.074)]
    words = _words([("Hello.", 300.0, 300.4), ("The", 301.0, 301.2), ("nominees", 301.3, 301.8),
                    ("are", 302.0, 302.3), ("here.", 302.4, 302.8), ("lots", 120.0, 130.0)])
    win = se.event_window(spans, words, 500.0)
    assert win["events"][0]["label"] == "award"
    assert win["start_time"] == 301.0  # snapped back onto the setup sentence
    assert win["end_time"] <= 301.0 + 60.0


def test_payoff_extends_a_clip_that_ends_inside_a_moment():
    spans = [se.EventSpan(190, 244, "stage_speech", 0.05)]
    new_end, why = se.extend_to_payoff(141.0, 199.0, spans, [], max_len=120)
    assert new_end == 245.0 and why["because"]["label"] == "stage_speech"


def test_payoff_respects_the_cap_and_leaves_unrelated_clips_alone():
    spans = [se.EventSpan(190, 244, "stage_speech", 0.05)]
    assert se.extend_to_payoff(141.0, 199.0, spans, [], max_len=80)[0] == 221.0
    assert se.extend_to_payoff(10.0, 60.0, spans, [])[1] is None


def test_segments_are_extended_in_place_with_a_reason():
    spans = [se.EventSpan(190, 244, "stage_speech", 0.05)]
    segs = [{"start_time": "02:21", "end_time": "03:19", "reasoning": "x."},
            {"start_time": "00:10", "end_time": "00:40", "reasoning": "y."}]
    assert se.extend_segments_to_payoff(segs, spans, []) == 1
    assert segs[0]["end_time"] == "03:21"  # cap: the 60 s render cap from 02:21
    assert "visual moment" in segs[0]["reasoning"]
    assert segs[1]["end_time"] == "00:40"


def test_signal_lines_use_the_selector_hint_format():
    spans = [se.EventSpan(190, 244, "stage_speech", 0.05)]
    text = se.event_signal_lines(spans, [])
    line = text.splitlines()[1]
    import re
    from src.ai import SIGNAL_LINE_PATTERN

    assert SIGNAL_LINE_PATTERN.match(line)
    assert "little or no transcribed speech" in line


def test_switch_default_off(monkeypatch):
    monkeypatch.delenv("SELECTION_EVENTS", raising=False)
    assert not se.story_events_enabled()


def test_no_frames_means_no_events(monkeypatch, tmp_path):
    monkeypatch.setattr(se, "sample_frames", lambda *a, **k: [])
    assert se.detect_story_events(tmp_path / "missing.mp4") == []


def test_cap_keeps_a_visual_tail_instead_of_walking_back_52_seconds(monkeypatch, tmp_path):
    """clip1 Step 4 root cause: a 60 s cap landing in a speechless stretch
    walked back to the last word 52 s earlier and rendered 7.7 s."""
    from src.media import timeline as T

    words = [{"text": "The", "start": 297_500, "end": 297_800},
             {"text": "nominees", "start": 298_000, "end": 299_000},
             {"text": "are...", "start": 304_000, "end": 304_800},
             {"text": "I", "start": 374_200, "end": 374_400}]
    monkeypatch.setattr(T, "load_cached_transcript_data", lambda _p: {"words": words})
    monkeypatch.setattr(T, "ffprobe_duration", lambda _p: 500.0, raising=False)
    monkeypatch.setenv("CLIP_CAP_KEEP_VISUAL_TAIL", "on")
    kept = T.clamp_keep_ranges_to_max_duration(tmp_path / "v.mp4", [(297.19, 379.54)], 60.0)
    assert abs(kept[0][1] - 357.19) < 0.01
    monkeypatch.setenv("CLIP_CAP_KEEP_VISUAL_TAIL", "off")
    old = T.clamp_keep_ranges_to_max_duration(tmp_path / "v.mp4", [(297.19, 379.54)], 60.0)
    assert old[0][1] < 306.0  # today's behaviour, kept when the switch is off
