"""Selection enforcement: S1 clamp (ai.py), S2 gate (video_service), cache key."""

from __future__ import annotations

import pytest

from src import ai
from src.auto_director.content_requirements import validate_content_brief
from src.services.task_service import TaskService
from src.services.video_service import VideoService

BRIEF = validate_content_brief(
    {
        "requirements": [
            {"text": "Expedia must be a major focus of the content.", "kind": "mandatory"}
        ],
        "allowed_ranges": [{"start": "2:00", "end": "8:00"}],
    }
)


def _segment(start_label, end_label, text="some grounded words here"):
    return ai.TranscriptSegment(
        start_time=start_label, end_time=end_label, text=text
    )


def _lines():
    lines = []
    for start in range(0, 600, 10):
        lines.append(
            {
                "start_time": f"{start // 60:02d}:{start % 60:02d}",
                "end_time": f"{(start + 10) // 60:02d}:{(start + 10) % 60:02d}",
                "start_seconds": start,
                "end_seconds": start + 10,
                "text": "generic words for this line",
            }
        )
    return lines


def test_s1_clamp_drops_out_of_range_candidate():
    seg = _segment("00:30", "01:20")
    assert ai._clamp_segment_to_allowed_ranges(seg, [(120.0, 480.0)], _lines(), 15) is None


def test_s1_clamp_keeps_longest_in_range_piece_and_regrounds():
    lines = _lines()
    seg = _segment("01:40", "03:20")  # 100-200, allowed opens at 120
    kept = ai._clamp_segment_to_allowed_ranges(seg, [(120.0, 480.0)], lines, 15)
    assert kept is not None
    assert kept.start_time == "02:00" and kept.end_time == "03:20"
    assert "generic words" in kept.text


def test_s1_clamp_drops_piece_below_minimum():
    seg = _segment("01:55", "02:05")  # only 5 in-range seconds
    assert ai._clamp_segment_to_allowed_ranges(seg, [(120.0, 480.0)], _lines(), 15) is None


def _transcript_text():
    rows = []
    for start in range(0, 600, 10):
        text = "generic chatter about lunch and the drive"
        if 180 <= start < 230:
            text = "we booked on Expedia and Expedia planned the destination trip"
        if start == 300:
            text = "thanks to Expedia for sponsoring this"
        rows.append(f"[{start // 60:02d}:{start % 60:02d} - {(start + 10) // 60:02d}:{(start + 10) % 60:02d}] Speaker A: {text}")
    return "\n".join(rows)


def _seg_dict(start, end, virality=50):
    return {
        "start_time": f"{start // 60:02d}:{start % 60:02d}",
        "end_time": f"{end // 60:02d}:{end % 60:02d}",
        "text": "x",
        "virality_score": virality,
    }


def test_s2_gate_drops_out_of_range_and_tier_c_backfills_with_tier_a():
    segments = [
        _seg_dict(30, 80, virality=99),     # out of range entirely
        _seg_dict(300, 330, virality=95),   # in range, passing only (Tier C)
        _seg_dict(180, 230, virality=40),   # in range, major focus (Tier A)
    ]
    gated, report = VideoService._apply_brief_gate(segments, BRIEF, _transcript_text())
    assert [(s["start_time"], s["end_time"]) for s in gated] == [("03:00", "03:50")]
    assert gated[0]["brief_tier"] == "A"
    assert report["outcome"] == "clips_selected"
    assert report["candidates_dropped_range"] == 1


def test_s2_gate_only_tier_c_empties_the_slate():
    segments = [_seg_dict(300, 330, virality=95)]
    gated, report = VideoService._apply_brief_gate(segments, BRIEF, _transcript_text())
    assert gated == []
    assert report["outcome"] == "no_compliant_moments"


def test_analysis_cache_key_brief_fingerprint():
    base = TaskService._build_analysis_cache_key("cachekey", "auto", None)
    assert base == "cachekey"  # no brief, no target: the historic key
    briefed = TaskService._build_analysis_cache_key(
        "cachekey", "auto", None, brief_fingerprint="abc123"
    )
    assert briefed != base
    other = TaskService._build_analysis_cache_key(
        "cachekey", "auto", None, brief_fingerprint="def456"
    )
    assert other != briefed
    same = TaskService._build_analysis_cache_key(
        "cachekey", "auto", None, brief_fingerprint="abc123"
    )
    assert same == briefed
