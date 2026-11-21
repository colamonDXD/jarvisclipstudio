"""Unit tests for the four creation modes (tracker 1.9).

These cover the mode contract itself: validation, the behaviour-plan
table (including the >=2-axis difference audit against Full AutoClip)
and manual-range segment building. Pipeline-decision tests that drive
the same input through different modes live in
test_creation_modes_pipeline.py.
"""

import pytest

from src.auto_director.creation_modes import (
    AUTOPICK_TIMEOUT_ENV,
    CREATION_MODES,
    FULL_AUTO,
    MANUAL_CUTTER_AI,
    MOMENTS_AI,
    MOMENTS_MANUAL,
    REVIEW_MOMENT_PICK,
    REVIEW_NONE,
    SELECTION_AI_CANDIDATES,
    SELECTION_AI_RANKED,
    SELECTION_USER_RANGES,
    auto_pick_note,
    build_manual_segments,
    plan_for_mode,
    selection_autopick_timeout_minutes,
    validate_creation_mode,
    validate_manual_ranges,
    validate_picked_indexes,
)


# ---------------------------------------------------------------------------
# validate_creation_mode
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("mode", CREATION_MODES)
def test_validate_creation_mode_accepts_the_four_named_modes(mode):
    assert validate_creation_mode(mode) == mode


@pytest.mark.parametrize("value", [None, ""])
def test_validate_creation_mode_treats_absent_as_no_mode(value):
    assert validate_creation_mode(value) is None


@pytest.mark.parametrize("value", ["fast", "balanced", "quality", "auto", 3, True])
def test_validate_creation_mode_rejects_speed_presets_and_junk(value):
    # fast/balanced/quality are processing_mode speed presets, not
    # workflows; accepting them here would recreate the tracker-1.9 gap.
    with pytest.raises(ValueError):
        validate_creation_mode(value)


# ---------------------------------------------------------------------------
# The behaviour-plan table and the difference audit
# ---------------------------------------------------------------------------


def _axes(plan):
    return (plan.selection, plan.ai_editing, plan.review, plan.requires_ranges)


def test_plan_table_matches_the_named_workflows():
    full = plan_for_mode(FULL_AUTO)
    assert full.selection == SELECTION_AI_RANKED
    assert full.ai_editing is True
    assert full.review == REVIEW_NONE
    assert full.requires_ranges is False

    manual = plan_for_mode(MOMENTS_MANUAL)
    assert manual.selection == SELECTION_AI_CANDIDATES
    # F-R1: True, like moments_ai — a picked render honours the
    # task's template/music instead of silently dropping them.
    assert manual.ai_editing is True
    assert manual.review == REVIEW_MOMENT_PICK

    moments_ai = plan_for_mode(MOMENTS_AI)
    assert moments_ai.selection == SELECTION_AI_CANDIDATES
    assert moments_ai.ai_editing is True
    assert moments_ai.review == REVIEW_MOMENT_PICK

    cutter = plan_for_mode(MANUAL_CUTTER_AI)
    assert cutter.selection == SELECTION_USER_RANGES
    assert cutter.ai_editing is True
    assert cutter.review == REVIEW_NONE
    assert cutter.requires_ranges is True


def test_no_mode_is_the_full_auto_plan():
    assert plan_for_mode(None) == plan_for_mode(FULL_AUTO)


@pytest.mark.parametrize("mode", [MOMENTS_MANUAL, MOMENTS_AI, MANUAL_CUTTER_AI])
def test_each_mode_differs_from_full_auto_on_at_least_two_axes(mode):
    baseline = _axes(plan_for_mode(FULL_AUTO))
    axes = _axes(plan_for_mode(mode))
    differences = sum(1 for a, b in zip(axes, baseline) if a != b)
    assert differences >= 2, f"{mode} differs on only {differences} axes"


def test_manual_and_ai_moments_pair_share_treatment():
    # F-R1: this pair used to differ only in treatment ("the human
    # edits vs the AI edits") — but a picked moments_manual render
    # without the pipeline EDL silently dropped the task's own
    # template/music choices (Plain and Auto renders came out
    # byte-identical), so the treatment now matches: both Find
    # Moments modes park for a pick and both render the pick with
    # the task's template/music applied. Recorded so the two modes
    # are never silently re-diverged; if they are ever meant to
    # differ again, that is a product decision, not a quiet revert.
    manual = _axes(plan_for_mode(MOMENTS_MANUAL))
    ai = _axes(plan_for_mode(MOMENTS_AI))
    assert manual == ai


# ---------------------------------------------------------------------------
# validate_manual_ranges
# ---------------------------------------------------------------------------


def test_validate_manual_ranges_normalises_and_sorts():
    result = validate_manual_ranges(
        [{"start": 90, "end": 120.5}, {"start": 10, "end": 25}]
    )
    assert result == [
        {"start": 10.0, "end": 25.0},
        {"start": 90.0, "end": 120.5},
    ]


def test_validate_manual_ranges_absent_is_none():
    assert validate_manual_ranges(None) is None


@pytest.mark.parametrize(
    "value",
    [
        [],
        "10-20",
        [{"start": 10}],
        [{"start": "10", "end": 20}],
        [{"start": True, "end": 20}],
        [{"start": -1, "end": 20}],
        [{"start": 20, "end": 20}],
        [{"start": 20, "end": 10}],
        [{"start": 10, "end": 10.5}],  # under the 1s floor
        [{"start": 0, "end": 3601}],  # over the 60min ceiling
        [{"start": 10, "end": 30}, {"start": 25, "end": 40}],  # overlap
        [{"start": i * 10, "end": i * 10 + 5} for i in range(21)],  # too many
    ],
)
def test_validate_manual_ranges_rejects_bad_contracts(value):
    with pytest.raises(ValueError):
        validate_manual_ranges(value)


def test_validate_manual_ranges_allows_touching_ranges():
    result = validate_manual_ranges(
        [{"start": 0, "end": 10}, {"start": 10, "end": 20}]
    )
    assert len(result) == 2


# ---------------------------------------------------------------------------
# validate_picked_indexes
# ---------------------------------------------------------------------------


def test_validate_picked_indexes_sorts_render_order():
    assert validate_picked_indexes([3, 0, 2], 5) == [0, 2, 3]


@pytest.mark.parametrize(
    "value",
    [[], [10], [-1], [1, 1], ["1"], [True], list(range(31))],
)
def test_validate_picked_indexes_rejects_bad_picks(value):
    with pytest.raises(ValueError):
        validate_picked_indexes(value, 10 if len(value) <= 30 else 40)


# ---------------------------------------------------------------------------
# build_manual_segments
# ---------------------------------------------------------------------------


def test_build_manual_segments_shape_and_honest_scores():
    segments = build_manual_segments(
        [{"start": 83.0, "end": 112.0}, {"start": 0.0, "end": 30.0}]
    )
    assert [s["start_time"] for s in segments] == ["01:23", "00:00"]
    first = segments[0]
    assert first["end_time"] == "01:52"
    assert first["reasoning"] == "User-selected range (Manual Cutter)."
    assert first["user_selected"] is True
    assert first["virality_score"] == 0
    assert first["hook_title"] is None


def test_build_manual_segments_clamps_to_duration_and_drops_beyond():
    segments = build_manual_segments(
        [
            {"start": 50.0, "end": 90.0},
            {"start": 120.0, "end": 150.0},
            {"start": 59.5, "end": 70.0},
        ],
        duration_seconds=60.0,
    )
    # First clamps to 60, second starts beyond the source, third would
    # shrink under the 1s floor after clamping.
    assert len(segments) == 1
    assert segments[0]["end_time"] == "01:00"


# ---------------------------------------------------------------------------
# Auto-pick timeout (parked Find-Moments rescue)
# ---------------------------------------------------------------------------


def test_autopick_timeout_defaults_to_15_minutes(monkeypatch):
    monkeypatch.delenv(AUTOPICK_TIMEOUT_ENV, raising=False)
    assert selection_autopick_timeout_minutes() == 15


def test_autopick_timeout_reads_the_env_override(monkeypatch):
    monkeypatch.setenv(AUTOPICK_TIMEOUT_ENV, "30")
    assert selection_autopick_timeout_minutes() == 30


@pytest.mark.parametrize("raw", ["", "abc", "0", "-5"])
def test_autopick_timeout_never_disables_or_fires_immediately(
    monkeypatch, raw
):
    monkeypatch.setenv(AUTOPICK_TIMEOUT_ENV, raw)
    assert selection_autopick_timeout_minutes() >= 1


def test_auto_pick_note_names_the_timeout():
    note = auto_pick_note(15)
    assert "Auto-picked the top moments" in note
    assert "15 minutes" in note
