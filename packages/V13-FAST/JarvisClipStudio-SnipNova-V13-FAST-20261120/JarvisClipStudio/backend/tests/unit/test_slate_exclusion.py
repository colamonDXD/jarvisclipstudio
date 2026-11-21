"""Slate exclusion (PeakCut take): motion map ranges + candidate pre-filter."""

from src.media.slate_exclusion import (
    filter_segments_by_slate,
    ranges_from_motion,
    static_overlap,
)


def _motion(static_spans, total=40.0, bucket=2.0):
    pairs = []
    t = 0.0
    while t < total:
        v = 0.1 if any(a <= t < b for a, b in static_spans) else 2.0
        pairs.append((t, v))
        t += bucket
    return pairs


def test_ranges_from_motion_finds_static_stretch():
    ranges = ranges_from_motion(_motion([(10.0, 26.0)]), threshold=0.4, min_seconds=6)
    assert ranges == [(10.0, 26.0)]


def test_ranges_from_motion_ignores_short_dip():
    ranges = ranges_from_motion(_motion([(10.0, 14.0)]), threshold=0.4, min_seconds=6)
    assert ranges == []


def test_static_overlap():
    ranges = [(10.0, 20.0)]
    assert static_overlap(0.0, 15.0, ranges) == 5.0
    assert static_overlap(30.0, 40.0, ranges) == 0.0
    assert static_overlap(10.0, 20.0, ranges) == 10.0


def test_filter_excludes_mostly_slate_segment():
    ranges = [(10.0, 26.0)]
    segments = [
        {"start_time": 0.0, "end_time": 8.0, "text": "real moment"},
        {"start_time": 12.0, "end_time": 24.0, "text": "spoken over the BRB card"},
        {"start_time": 20.0, "end_time": 40.0, "text": "half slate, half gameplay"},
    ]
    kept, excluded = filter_segments_by_slate(segments, ranges)
    assert [s["text"] for s in kept] == ["real moment", "half slate, half gameplay"]
    assert len(excluded) == 1
    assert excluded[0]["slate_overlap_seconds"] == 12.0


def test_filter_all_static_keeps_everything():
    ranges = [(0.0, 100.0)]
    segments = [
        {"start_time": 0.0, "end_time": 30.0, "text": "podcast part one"},
        {"start_time": 30.0, "end_time": 60.0, "text": "podcast part two"},
    ]
    kept, excluded = filter_segments_by_slate(segments, ranges)
    assert kept == segments and excluded == []


def test_filter_accepts_mmss_bounds_and_bad_bounds():
    ranges = [(10.0, 26.0)]
    segments = [
        {"start_time": "00:12", "end_time": "00:24", "text": "on the card"},
        {"start_time": "bad", "end_time": "worse", "text": "unparseable, kept"},
        {"start_time": "00:30", "end_time": "00:40", "text": "gameplay"},
    ]
    kept, excluded = filter_segments_by_slate(segments, ranges)
    assert [s["text"] for s in kept] == ["unparseable, kept", "gameplay"]
    assert len(excluded) == 1


def test_filter_no_ranges_is_noop():
    segments = [{"start_time": 0.0, "end_time": 10.0, "text": "x"}]
    kept, excluded = filter_segments_by_slate(segments, [])
    assert kept == segments and excluded == []
