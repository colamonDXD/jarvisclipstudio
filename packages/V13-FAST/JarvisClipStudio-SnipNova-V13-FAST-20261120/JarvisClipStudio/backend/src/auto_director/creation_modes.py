"""The four explicit creation modes (tracker 1.9).

Named by Luffy's Final Autonomous Development Directive (2026-11-08):
Full AutoClip / Find Moments + Manual / Find Moments + AI Editing /
Manual Cutter + AI Editing. They are four distinct *workflows*, not
speed presets: each mode fixes a moment-selection strategy, an
AI-editing treatment, a user-input contract and a review step, and the
pipeline branches on those behaviours in code.

A task with no stored mode (``creation_mode IS NULL``) is a legacy task
and follows the historical pipeline exactly; ``full_auto`` makes the
same decisions explicitly.
"""

from __future__ import annotations

import json
import math
import os
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

FULL_AUTO = "full_auto"
MOMENTS_MANUAL = "moments_manual"
MOMENTS_AI = "moments_ai"
MANUAL_CUTTER_AI = "manual_cutter_ai"

CREATION_MODES = (FULL_AUTO, MOMENTS_MANUAL, MOMENTS_AI, MANUAL_CUTTER_AI)

# Selection strategies (who decides which moments become clips).
SELECTION_AI_RANKED = "ai_ranked"
SELECTION_AI_CANDIDATES = "ai_candidates_for_review"
SELECTION_USER_RANGES = "user_ranges"

# Review steps (what pauses between selection and rendering).
REVIEW_NONE = "none"
REVIEW_MOMENT_PICK = "moment_pick"

# Task status while a review-mode task waits for the user's pick.
AWAITING_SELECTION_STATUS = "awaiting_selection"

# How long a review-mode task may sit in AWAITING_SELECTION_STATUS before
# the auto-pick sweep continues it with the top-ranked moments. A parked
# task must never wait forever: the user may be away, and a pick that
# never comes would otherwise strand the whole generation.
AUTOPICK_TIMEOUT_ENV = "TASK_SELECTION_AUTOPICK_MINUTES"
AUTOPICK_TIMEOUT_DEFAULT_MINUTES = 15


def selection_autopick_timeout_minutes() -> int:
    """Auto-pick timeout for parked Find-Moments tasks, in minutes.

    Read from the environment on every call so an operator change (and
    a test's monkeypatched env) takes effect without a restart of the
    sweep. Unset, unparsable or non-positive values fall back to the
    default rather than disabling the rescue or firing immediately.
    """
    raw = os.getenv(AUTOPICK_TIMEOUT_ENV)
    if raw is None:
        return AUTOPICK_TIMEOUT_DEFAULT_MINUTES
    try:
        minutes = int(raw)
    except (TypeError, ValueError):
        return AUTOPICK_TIMEOUT_DEFAULT_MINUTES
    return max(1, minutes)


def auto_pick_note(timeout_minutes: int) -> str:
    """The honest marker recorded when the sweep picks in the user's place.

    Stored on the task (checkpoint + source metadata) and shown as the
    task's progress message, so an automatic pick is never dressed up
    as a user pick.
    """
    return (
        "Auto-picked the top moments — no selection was made within "
        f"{timeout_minutes} minutes."
    )

MAX_MANUAL_RANGES = 20
MIN_RANGE_SECONDS = 1.0
MAX_RANGE_SECONDS = 3600.0
MAX_PICKED_MOMENTS = 30


@dataclass(frozen=True)
class ModePlan:
    """The pipeline behaviours one creation mode fixes.

    ``ai_editing`` is the render-treatment switch handed to the
    renderer: True builds the pipeline EDL (reframe plan, music
    chain, profile treatment). True for every mode since the F-R1
    fix — a moments_manual pick renders with the same treatment
    as moments_ai, so a task's template/music choices are never
    silently dropped from a picked render.
    """

    mode: str
    selection: str
    ai_editing: bool
    review: str
    requires_ranges: bool


_PLANS: Dict[str, ModePlan] = {
    FULL_AUTO: ModePlan(
        mode=FULL_AUTO,
        selection=SELECTION_AI_RANKED,
        ai_editing=True,
        review=REVIEW_NONE,
        requires_ranges=False,
    ),
    MOMENTS_MANUAL: ModePlan(
        mode=MOMENTS_MANUAL,
        selection=SELECTION_AI_CANDIDATES,
        # F-R1: True, like moments_ai. False used to mean "plain cuts
        # the user edits themselves" — but a picked render without
        # the pipeline EDL has no reframe plan and no music chain,
        # so the task's own template/music choices were silently
        # dropped (Plain and Auto renders came out byte-identical).
        # A chosen setting must never silently switch modes: the
        # pick renders with the same pipeline treatment as
        # moments_ai, and Plain still means Plain (the EDL is
        # plainified to the full-bleed tight crop, no B-roll/music).
        ai_editing=True,
        review=REVIEW_MOMENT_PICK,
        requires_ranges=False,
    ),
    MOMENTS_AI: ModePlan(
        mode=MOMENTS_AI,
        selection=SELECTION_AI_CANDIDATES,
        ai_editing=True,
        review=REVIEW_MOMENT_PICK,
        requires_ranges=False,
    ),
    MANUAL_CUTTER_AI: ModePlan(
        mode=MANUAL_CUTTER_AI,
        selection=SELECTION_USER_RANGES,
        ai_editing=True,
        review=REVIEW_NONE,
        requires_ranges=True,
    ),
}


def plan_for_mode(mode: Optional[str]) -> ModePlan:
    """Behaviour plan for a stored mode; ``None`` is the legacy default."""
    if mode is None:
        return _PLANS[FULL_AUTO]
    return _PLANS[mode]


# The reasoning stamped on every Manual Cutter segment, so nothing
# downstream can mistake a user range for an AI-ranked moment.
MANUAL_RANGE_REASONING = "User-selected range (Manual Cutter)."


def cached_analysis_is_user_selected(payload: object) -> bool:
    """Whether a cached analysis payload holds user-selected ranges.

    A Manual Cutter analysis is identified three ways, newest first:
    the ``selection_strategy`` stamp the pipeline writes on the
    payload, the ``user_selected`` flag on its segments, or the Manual
    Cutter reasoning string. Any one of them marks the payload:
    pre-fix rows carry no stamp, but their segments still carry the
    flag and the reasoning. An unparseable payload is not
    user-selected — the caller's normal parse path already discards
    it, so this only ever refuses a payload, never a run.
    """
    if not isinstance(payload, str) or not payload:
        return False
    try:
        analysis = json.loads(payload)
    except (TypeError, ValueError):
        return False
    if not isinstance(analysis, dict):
        return False
    if analysis.get("selection_strategy") == SELECTION_USER_RANGES:
        return True
    segments = analysis.get("most_relevant_segments") or []
    if not isinstance(segments, list):
        return False
    return any(
        isinstance(segment, dict)
        and (
            segment.get("user_selected")
            or segment.get("reasoning") == MANUAL_RANGE_REASONING
        )
        for segment in segments
    )


def validate_creation_mode(value: object) -> Optional[str]:
    """Validate an explicit per-task creation mode.

    ``None`` (and an empty string, which form encoders produce for an
    untouched optional field) means no mode was chosen: the task keeps
    the legacy pipeline behaviour. Anything else must be one of the
    four named workflows.
    """
    if value is None or value == "":
        return None
    if not isinstance(value, str) or value not in CREATION_MODES:
        raise ValueError(
            f"Invalid creation_mode {value!r}. Valid values: "
            f"{sorted(CREATION_MODES)}, or null."
        )
    return value


def _finite_seconds(value: object, field: str, index: int) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(
            f"manual_ranges[{index}].{field} must be a number of seconds"
        )
    seconds = float(value)
    if not math.isfinite(seconds):
        raise ValueError(f"manual_ranges[{index}].{field} must be finite")
    return seconds


def validate_manual_ranges(value: object) -> Optional[List[Dict[str, float]]]:
    """Validate and normalise user-supplied cutter ranges.

    Ranges are ``{"start": seconds, "end": seconds}`` objects. The
    normalised result is sorted by start with float seconds, so the
    rest of the pipeline never re-checks the contract: 1-20 ranges,
    each 1 s to 60 min long, starting at or after 0, never overlapping
    (a cutter range is a distinct piece of the source).
    """
    if value is None:
        return None
    if not isinstance(value, list) or not value:
        raise ValueError("manual_ranges must be a non-empty list of ranges")
    if len(value) > MAX_MANUAL_RANGES:
        raise ValueError(
            f"manual_ranges is limited to {MAX_MANUAL_RANGES} ranges"
        )
    ranges: List[Dict[str, float]] = []
    for index, item in enumerate(value):
        if not isinstance(item, dict):
            raise ValueError(f"manual_ranges[{index}] must be an object")
        start = _finite_seconds(item.get("start"), "start", index)
        end = _finite_seconds(item.get("end"), "end", index)
        if start < 0:
            raise ValueError(f"manual_ranges[{index}].start must be >= 0")
        if end <= start:
            raise ValueError(
                f"manual_ranges[{index}].end must be after its start"
            )
        if end - start < MIN_RANGE_SECONDS:
            raise ValueError(
                f"manual_ranges[{index}] must be at least "
                f"{MIN_RANGE_SECONDS:.0f} second long"
            )
        if end - start > MAX_RANGE_SECONDS:
            raise ValueError(
                f"manual_ranges[{index}] must be at most "
                f"{MAX_RANGE_SECONDS:.0f} seconds long"
            )
        ranges.append({"start": start, "end": end})
    ranges.sort(key=lambda item: item["start"])
    for previous, current in zip(ranges, ranges[1:]):
        if current["start"] < previous["end"]:
            raise ValueError("manual_ranges must not overlap")
    return ranges


def validate_picked_indexes(
    value: object, candidate_count: int
) -> List[int]:
    """Validate a moment-pick selection against the candidate slate.

    Returns the picked indexes sorted ascending (render order follows
    the slate order, not the click order). At least one pick is
    required; duplicates, out-of-range indexes and oversized picks are
    rejected rather than silently repaired.
    """
    if not isinstance(value, list) or not value:
        raise ValueError("segment_indexes must be a non-empty list")
    if len(value) > MAX_PICKED_MOMENTS:
        raise ValueError(
            f"At most {MAX_PICKED_MOMENTS} moments can be picked at once"
        )
    picked: List[int] = []
    for item in value:
        if isinstance(item, bool) or not isinstance(item, int):
            raise ValueError("segment_indexes must contain whole numbers")
        if item < 0 or item >= candidate_count:
            raise ValueError(
                f"segment index {item} is outside the candidate slate"
            )
        if item in picked:
            raise ValueError(f"segment index {item} was picked twice")
        picked.append(item)
    return sorted(picked)


def build_manual_segments(
    ranges: List[Dict[str, float]],
    duration_seconds: Optional[float] = None,
) -> List[Dict[str, Any]]:
    """Turn validated cutter ranges into pipeline segment payloads.

    The payload shape mirrors what transcript analysis produces
    (``MM:SS`` timestamps plus score fields) so the render path treats
    user ranges and AI segments identically downstream. Scores are
    zero with an explicit user-selection reasoning: no AI ever ranked
    these moments, and nothing downstream may pretend otherwise. Text
    is filled in by the pipeline's transcript grounding, like any
    other segment. Ranges are clamped to the real source duration;
    a range starting beyond it is dropped.
    """
    from ..media.timeline import seconds_to_mmss

    segments: List[Dict[str, Any]] = []
    for item in ranges:
        start = float(item["start"])
        end = float(item["end"])
        if duration_seconds is not None and duration_seconds > 0:
            if start >= duration_seconds:
                continue
            end = min(end, float(duration_seconds))
        if end - start < MIN_RANGE_SECONDS:
            continue
        segments.append(
            {
                "start_time": seconds_to_mmss(start),
                "end_time": seconds_to_mmss(end),
                "text": "",
                "relevance_score": 0.0,
                "reasoning": MANUAL_RANGE_REASONING,
                "virality_score": 0,
                "hook_score": 0,
                "engagement_score": 0,
                "value_score": 0,
                "shareability_score": 0,
                "hook_type": None,
                "hook_title": None,
                "hook_start_time": None,
                "hook_end_time": None,
                "user_selected": True,
            }
        )
    return segments
