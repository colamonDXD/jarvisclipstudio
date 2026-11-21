"""Range algebra helpers (design §2.5) + time_map localisation."""

from __future__ import annotations

import math

from src.auto_director.content_requirements import (
    allowed_range_tuples,
    clamp_segment_to_allowed,
    intersect_ranges,
    localize_allowed_ranges,
    ranges_within,
)
from src.youtube_source import build_time_map

ALLOWED = [(120.0, 480.0)]


def test_intersect_contained_and_straddling():
    assert intersect_ranges([(200.0, 300.0)], ALLOWED) == [(200.0, 300.0)]
    assert intersect_ranges([(100.0, 200.0)], ALLOWED) == [(120.0, 200.0)]
    assert intersect_ranges([(400.0, 900.0)], ALLOWED) == [(400.0, 480.0)]
    assert intersect_ranges([(0.0, 100.0)], ALLOWED) == []


def test_intersect_spanning_a_gap_multi_range():
    allowed = [(0.0, 100.0), (200.0, 300.0)]
    assert intersect_ranges([(50.0, 250.0)], allowed) == [
        (50.0, 100.0),
        (200.0, 250.0),
    ]


def test_intersect_non_monotonic_teaser_input():
    # A hook teaser plays before the body: order is not sorted.
    assert intersect_ranges([(300.0, 320.0), (100.0, 300.0)], ALLOWED) == [
        (120.0, 320.0)
    ]


def test_intersect_drops_tiny_fragments():
    assert intersect_ranges([(119.8, 120.2)], ALLOWED) == []


def test_ranges_within_audit():
    ok, violations = ranges_within([(200.0, 300.0)], ALLOWED)
    assert ok and violations == []
    ok, violations = ranges_within([(100.0, 300.0)], ALLOWED)
    assert not ok and violations == [[100.0, 300.0]]


def test_clamp_longest_piece():
    pieces = clamp_segment_to_allowed(50.0, 250.0, [(0.0, 100.0), (200.0, 300.0)])
    assert pieces == [(50.0, 100.0), (200.0, 250.0)]
    assert clamp_segment_to_allowed(500.0, 600.0, ALLOWED) == []


def test_allowed_tuples_open_end_is_infinite():
    brief = {
        "allowed_ranges": [
            {"start_seconds": 10589.0, "end_seconds": None,
             "start_label": "2:56:29", "end_label": None}
        ]
    }
    tuples = allowed_range_tuples(brief)
    assert tuples[0][0] == 10589.0 and math.isinf(tuples[0][1])
    assert intersect_ranges([(20000.0, 20100.0)], tuples) == [(20000.0, 20100.0)]


def test_localize_allowed_ranges_across_sections():
    # Sections 100-200 and 300-400 stitched back to back: local timeline
    # is 0-200, and the gap (200-300) must never become allowed locally.
    time_map = build_time_map([(100.0, 200.0), (300.0, 400.0)])
    local = localize_allowed_ranges([(150.0, 350.0)], time_map)
    # The two local pieces touch on the stitched timeline and merge.
    assert local == [(50.0, 150.0)]
    # The whole allowed span maps to exactly the downloaded sections.
    local = localize_allowed_ranges([(0.0, 1000.0)], time_map)
    assert local == [(0.0, 200.0)]
