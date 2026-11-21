"""Segment edit operations and caption overrides for the clip editor.

The editor works on a clip's *edit plan*: the EDL segment layout the clip
was (or will be) rendered with, persisted next to the clip file in its
source-map sidecar (see ``src/clip_source_map.py``). A plan's segments are
windows on the clip timeline — the same timeline the EDL renderer trims
out of the clip's source keep-ranges — so trimming, deleting, reordering
or splitting a segment changes what the next render actually contains.

Each segment also carries the source footage it shows: the slice of the
clip's concatenated keep-ranges proportional to its duration share of the
ORIGINAL layout (``source_share``/``source_offset``). Operations move the
slices with their segments, and ``kept_source_ranges`` turns an edited
plan back into the keep-ranges the render must use — without that mapping
an edit would still render the original footage (the plan would only
re-time the trim windows over unedited ranges). Trims are the exception:
a trim keeps the head of its segment's slice (it cuts the tail), which is
what "shrink this part" means in the panel.

This module is deliberately pure (no I/O, no ffmpeg): it validates and
mutates plan dicts, and every impossible edit raises ``ValueError`` with a
message the API layer can surface as a 422.
"""

from __future__ import annotations

import math
import re
from typing import Any, Optional

# A segment shorter than this cannot hold a meaningful cut; the renderer's
# own crossfade decision also ignores ranges under ~0.45s.
MIN_SEGMENT_SECONDS = 0.5

# Segments must tile the clip timeline; float dust from JSON round-trips
# and mm:ss storage is tolerated, real gaps/overlaps are not.
_TILE_TOLERANCE_SECONDS = 0.01

CAPTION_POSITIONS = ("top", "middle", "center", "bottom")
# Matches the position presets the caption overlay path has always used
# ("center" is the EDL spelling of the middle preset some profiles use).
POSITION_Y_BY_NAME = {"top": 0.18, "middle": 0.52, "center": 0.52, "bottom": 0.78}
POSITION_Y_MIN = 0.1
POSITION_Y_MAX = 0.85
FONT_SIZE_MIN = 12
FONT_SIZE_MAX = 72

_CAPTION_FIELDS = {
    "template",
    "font_family",
    "font_size",
    "font_color",
    "position",
    "position_y",
    "words_per_line",
}
_FONT_COLOR_RE = re.compile(r"^#[0-9A-Fa-f]{6}$")


def _finite_number(value: Any, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{name} must be a number")
    number = float(value)
    if not math.isfinite(number):
        raise ValueError(f"{name} must be a finite number")
    return number


def _segment_index(value: Any, count: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError("operation index must be an integer")
    if not 0 <= value < count:
        raise ValueError(
            f"operation index {value} is out of range for {count} segment(s)"
        )
    return value


def normalize_segments(raw: Any) -> list[dict[str, Any]]:
    """Validate and copy a plan's segment list.

    Segments must tile the clip timeline from 0 with no gaps or overlaps,
    and each must be at least ``MIN_SEGMENT_SECONDS`` long. The returned
    segments carry the fields the EDL renderer consumes (``start``, ``end``,
    ``transition_in``, ``transition_duration``) plus ``index``/``duration``
    and, when the input carried them, the ``source_share``/``source_offset``
    footage mapping (see the module docstring).
    """
    if not isinstance(raw, list) or not raw:
        raise ValueError("segments must be a non-empty list")
    segments: list[dict[str, Any]] = []
    cursor = 0.0
    for position, entry in enumerate(raw):
        if not isinstance(entry, dict):
            raise ValueError(f"segment {position} must be an object")
        start = round(_finite_number(entry.get("start"), f"segment {position} start"), 3)
        end = round(_finite_number(entry.get("end"), f"segment {position} end"), 3)
        if start < -_TILE_TOLERANCE_SECONDS:
            raise ValueError(f"segment {position} start must be >= 0")
        if abs(start - cursor) > _TILE_TOLERANCE_SECONDS:
            raise ValueError(
                "segments must tile the clip timeline without gaps or overlaps "
                f"(segment {position} starts at {start}, expected {round(cursor, 3)})"
            )
        if end - start < MIN_SEGMENT_SECONDS - 1e-9:
            raise ValueError(
                f"segment {position} is shorter than {MIN_SEGMENT_SECONDS}s"
            )
        transition_in = entry.get("transition_in", "cut")
        if not isinstance(transition_in, str) or not transition_in:
            raise ValueError(f"segment {position} transition_in must be a string")
        transition_duration = _finite_number(
            entry.get("transition_duration", 0), f"segment {position} transition_duration"
        )
        if transition_duration < 0:
            raise ValueError(f"segment {position} transition_duration must be >= 0")
        segment = {
            "index": position,
            "start": start,
            "end": end,
            "duration": round(end - start, 3),
            "transition_in": "none" if position == 0 else transition_in,
            "transition_duration": 0.0 if position == 0 else transition_duration,
        }
        if entry.get("source_share") is not None:
            share = _finite_number(
                entry.get("source_share"), f"segment {position} source_share"
            )
            if share <= 0:
                raise ValueError(f"segment {position} source_share must be > 0")
            offset = _finite_number(
                entry.get("source_offset", 0.0),
                f"segment {position} source_offset",
            )
            if offset < -1e-9:
                raise ValueError(f"segment {position} source_offset must be >= 0")
            segment["source_share"] = round(share, 6)
            segment["source_offset"] = round(max(0.0, offset), 3)
        segments.append(segment)
        cursor = end
    return segments


def attach_source_shares(
    segments: Any, ranges: Any
) -> list[dict[str, Any]]:
    """Stamp each segment with the slice of ``ranges`` it shows.

    The clip's stored keep-ranges concatenate to the clip timeline; a
    segment covering timeline window [start, end] shows exactly the
    ranges' overlap with that window. The mapping is stored RELATIVE to
    the ranges list (``source_offset`` = timeline position within the
    concatenated ranges where the segment's footage begins,
    ``source_share`` = the fraction of the ranges total the segment
    consumes), so the plan stays valid whatever the ranges' absolute
    source times are.
    """
    normalized = normalize_segments(segments)
    total = 0.0
    for entry in ranges or []:
        try:
            start, end = float(entry[0]), float(entry[1])
        except (TypeError, ValueError, IndexError):
            continue
        if end > start:
            total += end - start
    if total <= 0:
        raise ValueError("source ranges are empty")
    clip_total = total_duration(normalized)
    if abs(clip_total - total) > max(1.0, 0.05 * total):
        raise ValueError(
            "segment layout does not match the clip's source ranges "
            f"({clip_total}s of segments vs {round(total, 3)}s of ranges)"
        )
    for segment in normalized:
        segment["source_offset"] = segment["start"]
        segment["source_share"] = round(
            (segment["end"] - segment["start"]) / total, 6
        )
    return normalized


def kept_source_ranges(
    segments: Any, ranges: Any
) -> list[tuple[float, float]]:
    """The keep-ranges an edited plan renders, in play order.

    Each segment contributes the first ``duration`` seconds of its stored
    slice (a trim cuts the slice's tail), mapped back onto the source
    timeline through the concatenated ``ranges``. Plans without a footage
    mapping degrade to the unedited ranges — the caller's signal that the
    edit cannot change footage and must not be treated as applied.
    """
    from ..clip_source_map import normalize_source_ranges, slice_source_ranges

    normalized_ranges = normalize_source_ranges(ranges)
    if not normalized_ranges:
        return []
    total = sum(end - start for start, end in normalized_ranges)
    plan = normalize_segments(segments)
    if any("source_share" not in segment for segment in plan):
        return [tuple(entry) for entry in normalized_ranges]
    kept: list[tuple[float, float]] = []
    for segment in plan:
        window_start = segment.get("source_offset", 0.0)
        window_end = window_start + segment["duration"]
        kept.extend(
            slice_source_ranges(normalized_ranges, window_start, window_end)
        )
    return kept


def plan_has_source_mapping(plan: Optional[dict[str, Any]]) -> bool:
    """True when every segment of ``plan`` carries a footage mapping."""
    if not isinstance(plan, dict):
        return False
    segments = plan.get("segments")
    if not isinstance(segments, list) or not segments:
        return False
    return all(
        isinstance(entry, dict) and entry.get("source_share") is not None
        for entry in segments
    )


def total_duration(segments: list[dict[str, Any]]) -> float:
    return round(sum(float(s["end"]) - float(s["start"]) for s in segments), 3)


def build_edit_plan(
    segments: Any,
    *,
    niche: Optional[str] = None,
    captions: Optional[dict[str, Any]] = None,
    look: Optional[dict[str, Any]] = None,
    audio: Optional[dict[str, Any]] = None,
    voiceover: Optional[dict[str, Any]] = None,
    sfx: Optional[list[dict[str, Any]]] = None,
) -> dict[str, Any]:
    """Assemble a validated edit plan from raw segments (+ optional extras).

    ``look`` is the studio panels' override block (transition / grade /
    VFX, see ``auto_director.look``), stored verbatim — it was validated
    by ``normalize_look_overrides`` before it got here. ``audio`` and
    ``voiceover`` are the Audio Studio's blocks (see
    ``auto_director.audio_studio`` and ``media.voiceover``), stored the
    same way. ``sfx`` is the SFX panel's placement list (see
    ``auto_director.sfx``): unlike the other blocks an EMPTY list is
    stored too — it is the user's explicit "no placements", distinct
    from the block being absent.
    """
    plan: dict[str, Any] = {"version": 1, "segments": normalize_segments(segments)}
    if niche:
        plan["niche"] = niche
    if captions:
        plan["captions"] = captions
    if look:
        plan["look"] = look
    if audio:
        plan["audio"] = audio
    if voiceover:
        plan["voiceover"] = voiceover
    if sfx is not None:
        plan["sfx"] = sfx
    return plan


def plan_segments(plan: Optional[dict[str, Any]]) -> list[dict[str, Any]]:
    """The validated segment list of a stored plan (raises when unusable)."""
    if not isinstance(plan, dict):
        raise ValueError("clip has no stored edit plan")
    return normalize_segments(plan.get("segments"))


def caption_overrides_from_plan(
    plan: Optional[dict[str, Any]],
) -> Optional[dict[str, Any]]:
    """The caption override block of a stored plan, or None."""
    if not isinstance(plan, dict):
        return None
    captions = plan.get("captions")
    if not isinstance(captions, dict):
        return None
    overrides = {key: captions[key] for key in _CAPTION_FIELDS if key in captions}
    return overrides or None


def normalize_caption_overrides(raw: Any) -> Optional[dict[str, Any]]:
    """Validate a caption override block; None input stays None.

    The fields mirror what the ASS caption builder already accepts on the
    render path: template name, font family, font size (12-72, the UI
    range), font colour (#RRGGBB), position (a named preset and/or an
    explicit ``position_y`` fraction, 0.1-0.85 like the caption PATCH)
    and ``words_per_line`` (the caption grouping, 1-8 like the standalone
    editor's control). A ``words_per_line`` the user sets is validated
    with the standalone editor's own helper
    (``services.editor_service._valid_words_per_line``): missing,
    non-numeric and boolean values are not a usable grouping, and an
    in-range integer is clamped into 1-8 rather than rejected.
    """
    if raw is None:
        return None
    if not isinstance(raw, dict):
        raise ValueError("captions must be an object")
    unknown = sorted(set(raw) - _CAPTION_FIELDS)
    if unknown:
        raise ValueError(f"unknown caption fields: {', '.join(unknown)}")
    overrides: dict[str, Any] = {}
    if "template" in raw:
        template = raw["template"]
        if not isinstance(template, str) or not template.strip():
            raise ValueError("captions.template must be a non-empty string")
        overrides["template"] = template.strip()
    if "font_family" in raw:
        family = raw["font_family"]
        if not isinstance(family, str) or not family.strip():
            raise ValueError("captions.font_family must be a non-empty string")
        overrides["font_family"] = family.strip()
    if "font_size" in raw:
        size = raw["font_size"]
        if isinstance(size, bool) or not isinstance(size, int):
            raise ValueError("captions.font_size must be an integer")
        if not FONT_SIZE_MIN <= size <= FONT_SIZE_MAX:
            raise ValueError(
                f"captions.font_size must be between {FONT_SIZE_MIN} and {FONT_SIZE_MAX}"
            )
        overrides["font_size"] = size
    if "font_color" in raw:
        color = raw["font_color"]
        if not isinstance(color, str) or not _FONT_COLOR_RE.match(color):
            raise ValueError("captions.font_color must be a #RRGGBB hex colour")
        overrides["font_color"] = color.upper()
    if "position" in raw:
        position = raw["position"]
        if position not in CAPTION_POSITIONS:
            raise ValueError(
                f"captions.position must be one of: {', '.join(CAPTION_POSITIONS)}"
            )
        overrides["position"] = position
    if "position_y" in raw:
        position_y = _finite_number(raw["position_y"], "captions.position_y")
        if not POSITION_Y_MIN <= position_y <= POSITION_Y_MAX:
            raise ValueError(
                f"captions.position_y must be between {POSITION_Y_MIN} and {POSITION_Y_MAX}"
            )
        overrides["position_y"] = position_y
    if "words_per_line" in raw:
        # The grouping validator lives with the standalone editor that
        # owns it (services.editor_service) — imported here, not at the
        # top, because this module is deliberately pure and the services
        # layer is the heavyweight side of the dependency. A present
        # value the helper cannot use (boolean, non-numeric, None) is
        # an invalid override here, not a silent fall-through: the
        # caller asked for a grouping and must be told it did not take.
        from ..services.editor_service import _valid_words_per_line

        words_per_line = _valid_words_per_line(raw["words_per_line"])
        if words_per_line is None:
            raise ValueError(
                "captions.words_per_line must be an integer between 1 and 8"
            )
        overrides["words_per_line"] = words_per_line
    return overrides or None


def apply_segment_operations(
    segments: Any, operations: Any
) -> list[dict[str, Any]]:
    """Apply editor operations to a plan's segments, in order.

    Supported operations (times are seconds on the clip timeline):

    - ``{"type": "trim", "index": i, "start": s, "end": e}`` — shrink one
      segment inside its current window; ``start``/``end`` are optional
      and default to the current values. Following segments shift by the
      duration change. (Growing a segment would steal a neighbour's
      footage and is rejected.)
    - ``{"type": "delete", "index": i}`` — remove a segment; the segment
      that followed it inherits its join transition so the cut into it
      looks the same as the cut into the deleted segment did.
    - ``{"type": "reorder", "order": [i, ...]}`` — a permutation of the
      current indices. Transitions belong to timeline *positions*, not to
      segments, so each position keeps its join style.
    - ``{"type": "split", "index": i, "at": t}`` — split one segment at
      clip time ``t``; the new second half joins with a hard cut.

    Every step is validated; the first impossible edit raises ValueError
    and nothing is applied (the caller's list is never mutated).
    """
    if not isinstance(operations, list) or not operations:
        raise ValueError("operations must be a non-empty list")
    result = normalize_segments(segments)

    for step, operation in enumerate(operations):
        if not isinstance(operation, dict):
            raise ValueError(f"operation {step} must be an object")
        op_type = operation.get("type")
        if op_type == "trim":
            index = _segment_index(operation.get("index"), len(result))
            segment = result[index]
            new_start = round(
                _finite_number(operation.get("start", segment["start"]), "trim start"), 3
            )
            new_end = round(
                _finite_number(operation.get("end", segment["end"]), "trim end"), 3
            )
            if new_start < segment["start"] - 1e-9 or new_end > segment["end"] + 1e-9:
                raise ValueError(
                    "trim can only shrink a segment within its current window; "
                    "neighbouring footage belongs to other segments"
                )
            if new_end - new_start < MIN_SEGMENT_SECONDS - 1e-9:
                raise ValueError(
                    f"trimmed segment would be shorter than {MIN_SEGMENT_SECONDS}s"
                )
            shift = new_end - segment["end"]
            segment["start"] = new_start
            segment["end"] = new_end
            segment["duration"] = round(new_end - new_start, 3)
            # The footage slice is untouched: kept_source_ranges takes the
            # first `duration` seconds of it, so the trim cuts its tail.
            for following in result[index + 1:]:
                following["start"] = round(following["start"] + shift, 3)
                following["end"] = round(following["end"] + shift, 3)
        elif op_type == "delete":
            index = _segment_index(operation.get("index"), len(result))
            if len(result) == 1:
                raise ValueError("cannot delete the clip's only segment")
            removed = result.pop(index)
            shift = -(removed["end"] - removed["start"])
            for following in result[index:]:
                following["start"] = round(following["start"] + shift, 3)
                following["end"] = round(following["end"] + shift, 3)
            if index < len(result):
                if index == 0:
                    result[0]["transition_in"] = "none"
                    result[0]["transition_duration"] = 0.0
                else:
                    result[index]["transition_in"] = removed["transition_in"]
                    result[index]["transition_duration"] = removed[
                        "transition_duration"
                    ]
        elif op_type == "reorder":
            order = operation.get("order")
            if (
                not isinstance(order, list)
                or len(order) != len(result)
                or any(isinstance(i, bool) or not isinstance(i, int) for i in order)
                or sorted(order) != list(range(len(result)))
            ):
                raise ValueError(
                    "reorder order must be a permutation of the segment indices"
                )
            joins = [
                (s["transition_in"], s["transition_duration"]) for s in result
            ]
            cursor = 0.0
            reordered: list[dict[str, Any]] = []
            for position, source_index in enumerate(order):
                segment = dict(result[source_index])
                length = segment["end"] - segment["start"]
                segment["start"] = round(cursor, 3)
                segment["end"] = round(cursor + length, 3)
                segment["duration"] = round(length, 3)
                segment["transition_in"], segment["transition_duration"] = joins[
                    position
                ]
                reordered.append(segment)
                cursor += length
            result = reordered
        elif op_type == "split":
            index = _segment_index(operation.get("index"), len(result))
            segment = result[index]
            at = round(_finite_number(operation.get("at"), "split at"), 3)
            if (
                at - segment["start"] < MIN_SEGMENT_SECONDS - 1e-9
                or segment["end"] - at < MIN_SEGMENT_SECONDS - 1e-9
            ):
                raise ValueError(
                    f"split point must leave at least {MIN_SEGMENT_SECONDS}s on both sides"
                )
            left = {
                **segment,
                "end": at,
                "duration": round(at - segment["start"], 3),
            }
            right = {
                **segment,
                "start": at,
                "duration": round(segment["end"] - at, 3),
                "transition_in": "cut",
                "transition_duration": 0.0,
            }
            if "source_share" in segment:
                # The right half shows the footage after the split point:
                # advance its slice into the segment's original slice.
                left["source_share"] = round(
                    left["duration"] / segment["duration"] * segment["source_share"], 6
                )
                right["source_share"] = round(
                    segment["source_share"] - left["source_share"], 6
                )
                right["source_offset"] = round(
                    segment.get("source_offset", 0.0) + left["duration"], 3
                )
            result[index:index + 1] = [left, right]
        else:
            raise ValueError(f"unknown operation type: {op_type!r}")

    for position, segment in enumerate(result):
        segment["index"] = position
    return result
