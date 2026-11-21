"""Editor edit-plan operations: mutation correctness + validation failures."""

import pytest

from src.auto_director.clip_edits import (
    apply_segment_operations,
    attach_source_shares,
    build_edit_plan,
    caption_overrides_from_plan,
    kept_source_ranges,
    normalize_caption_overrides,
    normalize_segments,
    plan_has_source_mapping,
    plan_segments,
    total_duration,
)
from src.clip_source_map import (
    load_clip_editor_settings,
    load_clip_source_ranges,
    save_clip_editor_settings,
    save_clip_source_ranges,
)


def _segments(*durations):
    segments = []
    cursor = 0.0
    for index, duration in enumerate(durations):
        segments.append(
            {
                "index": index,
                "start": cursor,
                "end": cursor + duration,
                "duration": duration,
                "transition_in": "none" if index == 0 else "fade",
                "transition_duration": 0.0 if index == 0 else 0.4,
            }
        )
        cursor += duration
    return segments


def _windows(segments):
    return [(s["start"], s["end"]) for s in segments]


# --- normalize_segments ----------------------------------------------------


def test_normalize_segments_reindexes_and_forces_first_join():
    raw = [
        {"start": 0, "end": 2, "transition_in": "fade", "transition_duration": 0.4},
        {"start": 2, "end": 5},
    ]
    segments = normalize_segments(raw)
    assert _windows(segments) == [(0.0, 2.0), (2.0, 5.0)]
    assert segments[0]["transition_in"] == "none"
    assert segments[0]["transition_duration"] == 0.0
    assert segments[1]["transition_in"] == "cut"
    assert [s["index"] for s in segments] == [0, 1]
    assert total_duration(segments) == 5.0


@pytest.mark.parametrize(
    "raw",
    [
        [],
        "not-a-list",
        [{"start": 0.5, "end": 3.0}],  # does not start at 0
        [{"start": 0, "end": 2}, {"start": 2.5, "end": 4}],  # gap
        [{"start": 0, "end": 2}, {"start": 1.5, "end": 4}],  # overlap
        [{"start": 0, "end": 0.3}],  # below the minimum segment length
        [{"start": 0, "end": "x"}],
        [{"start": True, "end": 2}],
        [{"start": 0, "end": 2, "transition_in": 3}],
    ],
)
def test_normalize_segments_rejects_invalid_layouts(raw):
    with pytest.raises(ValueError):
        normalize_segments(raw)


# --- trim -------------------------------------------------------------------


def test_trim_shrinks_segment_and_shifts_following():
    segments = _segments(4.0, 4.0, 4.0)
    result = apply_segment_operations(
        segments, [{"type": "trim", "index": 0, "start": 1.0, "end": 3.0}]
    )
    assert _windows(result) == [(1.0, 3.0), (3.0, 7.0), (7.0, 11.0)]
    assert total_duration(result) == 10.0
    # The caller's list is never mutated.
    assert _windows(segments) == [(0.0, 4.0), (4.0, 8.0), (8.0, 12.0)]


def test_trim_middle_segment_keeps_head_and_shifts_tail():
    result = apply_segment_operations(
        _segments(4.0, 4.0, 4.0), [{"type": "trim", "index": 1, "end": 6.0}]
    )
    assert _windows(result) == [(0.0, 4.0), (4.0, 6.0), (6.0, 10.0)]


@pytest.mark.parametrize(
    "operation",
    [
        {"type": "trim", "index": 3, "end": 5.0},  # out of range
        {"type": "trim", "index": 0, "start": -1.0},  # before the window
        {"type": "trim", "index": 0, "end": 9.0},  # past the window
        {"type": "trim", "index": 0, "start": 3.8, "end": 4.0},  # too short
        {"type": "trim", "index": 0, "start": "soon"},
        {"type": "trim", "index": True, "end": 2.0},
    ],
)
def test_trim_rejects_impossible_edits(operation):
    with pytest.raises(ValueError):
        apply_segment_operations(_segments(4.0, 4.0), [operation])


# --- delete -----------------------------------------------------------------


def test_delete_removes_segment_and_inherits_join():
    result = apply_segment_operations(
        _segments(4.0, 4.0, 4.0), [{"type": "delete", "index": 1}]
    )
    assert _windows(result) == [(0.0, 4.0), (4.0, 8.0)]
    # The survivor keeps the deleted segment's join into it.
    assert result[1]["transition_in"] == "fade"
    assert result[1]["transition_duration"] == 0.4


def test_delete_first_segment_makes_survivor_the_opener():
    result = apply_segment_operations(
        _segments(4.0, 4.0), [{"type": "delete", "index": 0}]
    )
    assert _windows(result) == [(0.0, 4.0)]
    assert result[0]["transition_in"] == "none"
    assert result[0]["transition_duration"] == 0.0


def test_delete_only_segment_is_rejected():
    with pytest.raises(ValueError, match="only segment"):
        apply_segment_operations(_segments(4.0), [{"type": "delete", "index": 0}])


# --- reorder ----------------------------------------------------------------


def test_reorder_moves_windows_and_keeps_position_joins():
    result = apply_segment_operations(
        _segments(2.0, 3.0, 5.0), [{"type": "reorder", "order": [2, 0, 1]}]
    )
    assert _windows(result) == [(0.0, 5.0), (5.0, 7.0), (7.0, 10.0)]
    # Joins belong to positions: position 0 is always the opener.
    assert result[0]["transition_in"] == "none"
    assert result[1]["transition_in"] == "fade"
    assert total_duration(result) == 10.0


@pytest.mark.parametrize(
    "order",
    [[0, 1], [0, 1, 1], [0, 1, 3], [0, 1, "2"], "210"],
)
def test_reorder_rejects_non_permutations(order):
    with pytest.raises(ValueError):
        apply_segment_operations(
            _segments(2.0, 3.0, 5.0), [{"type": "reorder", "order": order}]
        )


# --- split ------------------------------------------------------------------


def test_split_divides_segment_with_a_hard_cut_join():
    result = apply_segment_operations(
        _segments(6.0, 4.0), [{"type": "split", "index": 0, "at": 2.5}]
    )
    assert _windows(result) == [(0.0, 2.5), (2.5, 6.0), (6.0, 10.0)]
    assert result[1]["transition_in"] == "cut"
    assert result[1]["transition_duration"] == 0.0
    assert [s["index"] for s in result] == [0, 1, 2]


@pytest.mark.parametrize("at", [0.2, 5.9, 6.5, -1.0])
def test_split_rejects_points_too_close_to_an_edge(at):
    with pytest.raises(ValueError):
        apply_segment_operations(
            _segments(6.0), [{"type": "split", "index": 0, "at": at}]
        )


def test_operations_compose_in_order():
    result = apply_segment_operations(
        _segments(4.0, 4.0, 4.0),
        [
            {"type": "split", "index": 1, "at": 6.0},
            {"type": "delete", "index": 0},
            {"type": "trim", "index": 2, "end": 7.0},
        ],
    )
    assert _windows(result) == [(0.0, 2.0), (2.0, 4.0), (4.0, 7.0)]


def test_unknown_and_malformed_operations_are_rejected():
    with pytest.raises(ValueError, match="unknown operation"):
        apply_segment_operations(_segments(4.0), [{"type": "explode"}])
    with pytest.raises(ValueError):
        apply_segment_operations(_segments(4.0), [])
    with pytest.raises(ValueError):
        apply_segment_operations(_segments(4.0), ["trim"])


# --- caption overrides ------------------------------------------------------


def test_normalize_caption_overrides_accepts_the_studio_fields():
    overrides = normalize_caption_overrides(
        {
            "template": "hormozi",
            "font_family": "Archivo Black",
            "font_size": 40,
            "font_color": "#ffe000",
            "position": "center",
            "position_y": 0.5,
        }
    )
    assert overrides == {
        "template": "hormozi",
        "font_family": "Archivo Black",
        "font_size": 40,
        "font_color": "#FFE000",
        "position": "center",
        "position_y": 0.5,
    }
    assert normalize_caption_overrides(None) is None
    assert normalize_caption_overrides({}) is None


def test_normalize_caption_overrides_accepts_words_per_line():
    # N4: the quick editor (and the AI edit) can set the caption
    # grouping. Validation reuses the standalone editor's
    # _valid_words_per_line: clamp into 1-8, never raise for range.
    assert normalize_caption_overrides({"words_per_line": 3}) == {
        "words_per_line": 3
    }
    assert normalize_caption_overrides({"words_per_line": 12}) == {
        "words_per_line": 8
    }
    assert normalize_caption_overrides({"words_per_line": 0}) == {
        "words_per_line": 1
    }
    assert normalize_caption_overrides({"words_per_line": 3.5}) == {
        "words_per_line": 3
    }


def test_caption_overrides_from_plan_carries_words_per_line():
    # A stored grouping survives the read-back, so a later re-render
    # keeps rendering what the user set.
    plan = {"captions": {"words_per_line": 2, "position": "top"}}
    assert caption_overrides_from_plan(plan) == {
        "words_per_line": 2,
        "position": "top",
    }


@pytest.mark.parametrize(
    "raw",
    [
        {"template": ""},
        {"font_family": "  "},
        {"font_size": 8},
        {"font_size": 100},
        {"font_size": 40.5},
        {"font_color": "yellow"},
        {"font_color": "#FFF"},
        {"position": "somewhere"},
        {"position_y": 0.05},
        {"position_y": 0.95},
        {"words_per_line": True},
        {"words_per_line": "abc"},
        {"words_per_line": None},
        {"surprise": True},
        "captions",
    ],
)
def test_normalize_caption_overrides_rejects_bad_values(raw):
    with pytest.raises(ValueError):
        normalize_caption_overrides(raw)


# --- plans + persistence ----------------------------------------------------


def test_build_edit_plan_round_trips_through_the_sidecar(tmp_path):
    clip_path = tmp_path / "clip.mp4"
    clip_path.write_bytes(b"clip")
    save_clip_source_ranges(clip_path, [(10.0, 20.0), (30.0, 35.0)])
    plan = build_edit_plan(
        _segments(5.0, 5.0),
        niche="gaming",
        captions={"position": "top"},
    )
    save_clip_editor_settings(clip_path, plan)

    loaded = load_clip_editor_settings(clip_path)
    assert loaded["niche"] == "gaming"
    assert plan_segments(loaded)[1]["start"] == 5.0
    assert caption_overrides_from_plan(loaded) == {"position": "top"}
    # Ranges survive alongside the plan, and vice versa.
    assert load_clip_source_ranges(clip_path) == [(10.0, 20.0), (30.0, 35.0)]
    save_clip_source_ranges(clip_path, [(10.0, 18.0), (30.0, 35.0)])
    assert load_clip_editor_settings(clip_path)["niche"] == "gaming"


def test_caption_overrides_from_plan_ignores_foreign_keys():
    plan = {"captions": {"position": "top", "internal": "x"}, "segments": []}
    assert caption_overrides_from_plan(plan) == {"position": "top"}
    assert caption_overrides_from_plan(None) is None
    assert caption_overrides_from_plan({}) is None


# --- footage mapping (source shares) -----------------------------------------
#
# Ranges (10-20) + (30-35) concatenate to a 15 s clip timeline. Segments
# 5/5/5 map to slices [0,5), [5,10), [10,15) of that concatenation.

_RANGES = [(10.0, 20.0), (30.0, 35.0)]


def _mapped_segments(*durations):
    return attach_source_shares(_segments(*durations), _RANGES)


def test_attach_source_shares_stamps_relative_slices():
    segments = _mapped_segments(5.0, 5.0, 5.0)
    assert [s["source_offset"] for s in segments] == [0.0, 5.0, 10.0]
    assert all(abs(s["source_share"] - 1 / 3) < 1e-5 for s in segments)
    assert plan_has_source_mapping({"segments": segments})
    assert not plan_has_source_mapping({"segments": _segments(5.0)})
    # An unedited mapped plan reproduces the original footage (ranges that
    # abut on the concatenated timeline come back split at the joins).
    assert kept_source_ranges(segments, _RANGES) == [
        (10.0, 15.0),
        (15.0, 20.0),
        (30.0, 35.0),
    ]


def test_attach_source_shares_rejects_a_mismatched_layout():
    with pytest.raises(ValueError, match="does not match"):
        attach_source_shares(_segments(4.0, 4.0), _RANGES)


def test_kept_ranges_follow_a_delete():
    edited = apply_segment_operations(
        _mapped_segments(5.0, 5.0, 5.0), [{"type": "delete", "index": 0}]
    )
    # Footage of segments 2+3 only: source 15-20 then 30-35.
    assert kept_source_ranges(edited, _RANGES) == [(15.0, 20.0), (30.0, 35.0)]


def test_kept_ranges_follow_a_reorder():
    edited = apply_segment_operations(
        _mapped_segments(5.0, 5.0, 5.0),
        [{"type": "reorder", "order": [2, 0, 1]}],
    )
    assert kept_source_ranges(edited, _RANGES) == [
        (30.0, 35.0),
        (10.0, 15.0),
        (15.0, 20.0),
    ]


def test_kept_ranges_trim_cuts_the_slice_tail():
    edited = apply_segment_operations(
        _mapped_segments(5.0, 5.0, 5.0),
        [{"type": "trim", "index": 1, "end": 8.0}],
    )
    # Segment 2 keeps its first 3 s (source 15-18); segment 1 and 3 whole.
    assert kept_source_ranges(edited, _RANGES) == [
        (10.0, 15.0),
        (15.0, 18.0),
        (30.0, 35.0),
    ]


def test_kept_ranges_follow_a_split_then_delete():
    edited = apply_segment_operations(
        _mapped_segments(5.0, 5.0, 5.0),
        [
            {"type": "split", "index": 0, "at": 2.0},
            {"type": "delete", "index": 0},
        ],
    )
    # Only the second half of segment 1's footage survives from it.
    assert kept_source_ranges(edited, _RANGES) == [
        (12.0, 15.0),
        (15.0, 20.0),
        (30.0, 35.0),
    ]


def test_kept_ranges_without_mapping_degrade_to_the_originals():
    assert kept_source_ranges(_segments(5.0, 5.0, 5.0), _RANGES) == _RANGES
    assert kept_source_ranges(_segments(5.0), []) == []
