"""Intro/teaser exclusion (Quality Director item 3).

A produced podcast's opening montage — dense one-liners cut from later
in the episode — scores like a great clip, so best-first selection kept
shipping the source's own trailer as a clip (the user's 2026-11-08
run). Candidates fully contained in the opening zone (default 45 s)
are now dropped when the zone shows the montage signature AND enough
real candidates remain outside it. Short videos and monologue openings
are untouched.
"""

import pytest

from src.ai import (
    TranscriptSegment,
    _opening_is_montage,
    exclude_intro_candidates,
)


def _ts(seconds: int) -> str:
    return f"{seconds // 60:02d}:{seconds % 60:02d}"


def _segment(start: int, end: int, text: str = "a moment") -> TranscriptSegment:
    return TranscriptSegment(
        start_time=_ts(start), end_time=_ts(end), text=text,
        relevance_score=0.9,
    )


def _line(start: int, end: int, text: str = "spoken line") -> dict:
    return {
        "start_time": _ts(start),
        "end_time": _ts(end),
        "start_seconds": start,
        "end_seconds": end,
        "text": text,
    }


def _montage_lines() -> list:
    """Eight punchy one-liners inside the first 40 s, then long lines."""
    lines = []
    cursor = 0
    for index in range(8):
        lines.append(_line(cursor, cursor + 4, f"teaser one-liner {index}"))
        cursor += 5
    lines.append(_line(60, 150, "the real conversation starts here"))
    lines.append(_line(150, 260, "and keeps going"))
    return lines


def _monologue_lines() -> list:
    """One continuous opening: two long lines across the first minute."""
    return [
        _line(0, 38, "a long unbroken opening thought that runs on"),
        _line(38, 80, "and the same speaker simply continues"),
        _line(80, 200, "the body of the talk"),
    ]


# --------------------------------------------------------------------------
# Montage signature
# --------------------------------------------------------------------------


def test_montage_signature_detects_dense_one_liners():
    assert _opening_is_montage(_montage_lines(), 45) is True


def test_montage_signature_ignores_a_monologue_opening():
    assert _opening_is_montage(_monologue_lines(), 45) is False


def test_montage_signature_needs_enough_lines():
    lines = [_line(0, 4, "one"), _line(5, 9, "two"), _line(60, 120, "body")]
    assert _opening_is_montage(lines, 45) is False


# --------------------------------------------------------------------------
# The exclusion
# --------------------------------------------------------------------------


def test_produced_intro_is_never_selected_when_real_content_exists():
    intro = _segment(2, 32, "the teaser montage itself")
    real = [_segment(start, start + 40) for start in (300, 700, 1200, 1800, 2400)]
    result = exclude_intro_candidates(
        [intro, *real], _montage_lines(), needed=5, source_seconds=3600
    )
    assert intro not in result
    assert result == real


def test_candidate_running_past_the_zone_is_real_content():
    opener = _segment(30, 90, "starts in the zone, runs well past it")
    real = [_segment(start, start + 40) for start in (300, 700, 1200, 1800)]
    result = exclude_intro_candidates(
        [opener, *real], _montage_lines(), needed=5, source_seconds=3600
    )
    assert result == [opener, *real]


def test_short_video_entirely_inside_the_zone_is_unaffected():
    only = _segment(0, 40, "the whole video")
    result = exclude_intro_candidates(
        [only], _montage_lines(), needed=1, source_seconds=40
    )
    assert result == [only]


def test_not_enough_outside_candidates_keeps_the_intro():
    intro = _segment(2, 32, "the teaser montage itself")
    real = [_segment(300, 340), _segment(700, 740)]
    result = exclude_intro_candidates(
        [intro, *real], _montage_lines(), needed=5, source_seconds=3600
    )
    assert result == [intro, *real]


def test_monologue_opening_is_not_excluded():
    opener = _segment(0, 40, "the presenter's own opening words")
    real = [_segment(start, start + 40) for start in (300, 700, 1200, 1800, 2400)]
    result = exclude_intro_candidates(
        [opener, *real], _monologue_lines(), needed=5, source_seconds=3600
    )
    assert result == [opener, *real]


def test_zone_zero_disables_the_exclusion():
    intro = _segment(2, 32)
    real = [_segment(start, start + 40) for start in (300, 700, 1200, 1800, 2400)]
    result = exclude_intro_candidates(
        [intro, *real], _montage_lines(), needed=5, source_seconds=3600,
        zone_seconds=0,
    )
    assert result == [intro, *real]


def test_custom_zone_is_respected():
    early = _segment(50, 80, "inside a 90 s zone but outside a 45 s one")
    real = [_segment(start, start + 40) for start in (300, 700, 1200, 1800, 2400)]
    lines = [_line(s, s + 4, f"teaser {s}") for s in range(0, 85, 5)]
    assert exclude_intro_candidates(
        [early, *real], lines, needed=5, source_seconds=3600, zone_seconds=45
    ) == [early, *real]
    assert exclude_intro_candidates(
        [early, *real], lines, needed=5, source_seconds=3600, zone_seconds=90
    ) == real


def test_env_zone_override(monkeypatch):
    from src.ai import _intro_zone_seconds

    monkeypatch.delenv("ANALYSIS_INTRO_ZONE_SECONDS", raising=False)
    assert _intro_zone_seconds() == 45
    monkeypatch.setenv("ANALYSIS_INTRO_ZONE_SECONDS", "0")
    assert _intro_zone_seconds() == 0
    monkeypatch.setenv("ANALYSIS_INTRO_ZONE_SECONDS", "90")
    assert _intro_zone_seconds() == 90
    monkeypatch.setenv("ANALYSIS_INTRO_ZONE_SECONDS", "banana")
    assert _intro_zone_seconds() == 45
