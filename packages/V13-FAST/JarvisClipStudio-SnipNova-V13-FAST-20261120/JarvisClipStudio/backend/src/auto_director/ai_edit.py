"""The AI edit assistant's contract: instruction -> editor operations.

The clip editor (``src/auto_director/clip_edits.py``) exposes a small,
fixed set of operations. This module is the ONLY vocabulary the AI edit
assistant may speak: it builds the prompt that describes a clip's current
edit plan, parses the model's reply, and validates it against a strict
schema that allows nothing outside the editor's own operation set:

- segment operations — exactly the four ``apply_segment_operations``
  understands: ``trim``, ``delete``, ``reorder``, ``split``;
- a ``captions`` block — exactly the Caption Studio fields
  ``normalize_caption_overrides`` accepts;
- ``music_override`` — a music-library filename (validated against the
  live library by the service layer, like the manual edit route).

Anything else the model emits — an unknown operation, an extra field,
prose around the JSON — is rejected wholesale by
``parse_ai_edit_response``; the caller then leaves the clip untouched.
Two things people often ask an AI editor for are deliberately NOT in the
contract, because the editor has no operation for them: per-segment
transition changes (join transitions belong to timeline positions in an
edit plan; there is no transition operation) and zoom emphasis (zooms
come from the niche profile when the EDL is built, not from the editor).

The plain-language summary returned to the user is generated HERE from
the operations that were actually applied (``describe_ai_edit``) — never
from free prose the model wrote — so what the UI says happened is what
the dry-run proved will happen.

This module is pure (no I/O, no LLM calls), mirroring clip_edits.py.
"""

from __future__ import annotations

import json
import math
from typing import Any, Optional

from .clip_edits import (
    apply_segment_operations,
    normalize_caption_overrides,
    normalize_segments,
)

# The four segment operations the editor's applier implements.
AI_SEGMENT_OPERATIONS = ("trim", "delete", "reorder", "split")

# Exact field sets per operation (besides "type"). Anything outside these
# is rejected: the model may not invent parameters the applier would
# silently ignore.
_OPERATION_FIELDS: dict[str, tuple[set[str], set[str]]] = {
    # op type -> (required fields, optional fields)
    "trim": ({"index"}, {"start", "end"}),
    "delete": ({"index"}, set()),
    "reorder": ({"order"}, set()),
    "split": ({"index", "at"}, set()),
}

_TOP_LEVEL_FIELDS = {"operations", "captions", "music_override"}

# A rambling instruction is a prompt-injection surface and a bad edit
# waiting to happen; the route caps length, this is the hard ceiling the
# schema layer enforces regardless of caller.
MAX_OPERATIONS = 32


AI_EDIT_SYSTEM_PROMPT = """You are the AI edit assistant inside a clip editor. You translate a user's plain-language editing instruction into a JSON edit plan for ONE existing clip.

You may ONLY use these segment operations (indexes are 0-based positions in the segment list you are given; operations apply in array order, and each operation sees the segment list left by the previous ones):
- {"type": "trim", "index": i, "start": s, "end": e} — shrink segment i inside its current window. "start" and "end" are seconds on the clip timeline; give at least one of them. A trim can only shrink, never grow, and every segment must stay at least 0.5s long.
- {"type": "delete", "index": i} — remove segment i.
- {"type": "reorder", "order": [i, ...]} — reorder all segments; "order" must be a permutation of every current segment index.
- {"type": "split", "index": i, "at": t} — split segment i at clip time t, leaving at least 0.5s on both sides.

You may also include:
- "captions": an object with any of "template" (a caption template name), "font_family", "font_size" (integer 12-72), "font_color" ("#RRGGBB"), "position" ("top", "middle" or "bottom"), "position_y" (0.1-0.85), "words_per_line" (integer 1-8, words shown per caption line). Only include caption fields the user actually asked to change.
- "music_override": a music track filename from the user's library, only when the user names a specific track.

You can NOT change a segment's transition style, add zooms, change the words that are spoken, or invent new footage. If the instruction asks for something outside the operations above, return {}.

OUTPUT REQUIREMENTS:
- Return valid JSON only. No markdown, no code fences, no explanations, no extra text.
- The JSON object may contain only the keys "operations", "captions" and "music_override".
- Omit anything the user did not ask for. If no change is needed, return {}."""


def _finite_number(value: Any, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{name} must be a number")
    number = float(value)
    if not math.isfinite(number):
        raise ValueError(f"{name} must be a finite number")
    return number


def _segment_index(value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError("operation index must be an integer")
    if value < 0:
        raise ValueError("operation index must be >= 0")
    return value


def validate_ai_operation(raw: Any, step: int) -> dict[str, Any]:
    """Validate one AI-proposed segment operation against the schema.

    This checks shape only (types, exact field sets); whether the
    operation actually fits the clip is the applier's job, dry-run by the
    service before anything is queued.
    """
    if not isinstance(raw, dict):
        raise ValueError(f"operation {step} must be an object")
    op_type = raw.get("type")
    if op_type not in _OPERATION_FIELDS:
        raise ValueError(
            f"operation {step} has unsupported type {op_type!r}; "
            f"allowed: {', '.join(AI_SEGMENT_OPERATIONS)}"
        )
    required, optional = _OPERATION_FIELDS[op_type]
    fields = set(raw) - {"type"}
    unknown = sorted(fields - required - optional)
    if unknown:
        raise ValueError(
            f"operation {step} ({op_type}) has unsupported fields: "
            f"{', '.join(unknown)}"
        )
    missing = sorted(required - fields)
    if missing:
        raise ValueError(
            f"operation {step} ({op_type}) is missing fields: "
            f"{', '.join(missing)}"
        )
    operation: dict[str, Any] = {"type": op_type}
    if "index" in required or "index" in fields:
        operation["index"] = _segment_index(raw.get("index"))
    if op_type == "trim":
        if "start" not in fields and "end" not in fields:
            raise ValueError(
                f"operation {step} (trim) needs a start and/or end"
            )
        for bound in ("start", "end"):
            if bound in fields:
                operation[bound] = round(
                    _finite_number(raw[bound], f"trim {bound}"), 3
                )
    elif op_type == "split":
        operation["at"] = round(_finite_number(raw.get("at"), "split at"), 3)
    elif op_type == "reorder":
        order = raw.get("order")
        if not isinstance(order, list) or not order:
            raise ValueError(f"operation {step} (reorder) order must be a list")
        operation["order"] = [_segment_index(entry) for entry in order]
    return operation


def validate_ai_edit_payload(payload: Any) -> dict[str, Any]:
    """Validate a decoded AI edit reply against the strict schema.

    Returns the normalized edit ``{operations?, captions?,
    music_override?}`` the edit endpoint understands. Raises ValueError
    on ANY deviation — unknown keys, unknown operations, wrong types, or
    an edit that changes nothing — so the caller can reject the whole
    reply and leave the clip untouched.
    """
    if not isinstance(payload, dict):
        raise ValueError("the AI reply must be a JSON object")
    unknown = sorted(set(payload) - _TOP_LEVEL_FIELDS)
    if unknown:
        raise ValueError(f"unsupported fields in AI reply: {', '.join(unknown)}")

    edit: dict[str, Any] = {}
    raw_operations = payload.get("operations")
    if raw_operations is not None:
        if not isinstance(raw_operations, list):
            raise ValueError("operations must be an array")
        if len(raw_operations) > MAX_OPERATIONS:
            raise ValueError(
                f"too many operations (max {MAX_OPERATIONS})"
            )
        if raw_operations:
            edit["operations"] = [
                validate_ai_operation(entry, step)
                for step, entry in enumerate(raw_operations)
            ]

    captions = normalize_caption_overrides(payload.get("captions"))
    if captions is not None:
        edit["captions"] = captions

    music = payload.get("music_override")
    if music is not None:
        if not isinstance(music, str) or not music.strip():
            raise ValueError("music_override must be a track filename")
        edit["music_override"] = music.strip()

    if not edit:
        raise ValueError(
            "the instruction cannot be carried out with the editor's "
            "operations (segment trim/delete/reorder/split, caption "
            "changes, or a named music track)"
        )
    return edit


def parse_ai_edit_response(raw: Any) -> dict[str, Any]:
    """Parse a raw model reply into a validated edit (see above).

    The prompt demands bare JSON; a single surrounding markdown fence is
    tolerated because smaller local models add one despite instructions.
    Anything else unparseable is rejected wholesale.
    """
    if not isinstance(raw, str):
        raise ValueError("the AI reply was not text")
    text = raw.strip()
    if text.startswith("```"):
        lines = text.splitlines()
        if len(lines) >= 2 and lines[-1].strip() == "```":
            text = "\n".join(lines[1:-1]).strip()
    try:
        payload = json.loads(text)
    except (json.JSONDecodeError, ValueError):
        raise ValueError("the AI reply was not valid JSON") from None
    return validate_ai_edit_payload(payload)


def build_ai_edit_prompt(plan: dict[str, Any], instruction: str) -> str:
    """The user prompt: the instruction plus the clip's current plan."""
    segments = normalize_segments(plan.get("segments"))
    lines = [
        f"User's editing instruction: {instruction.strip()}",
        "",
        "The clip's segments (0-based indexes; times in seconds on the "
        "clip timeline):",
    ]
    for segment in segments:
        join = segment["transition_in"]
        if segment["index"] > 0 and segment["transition_duration"]:
            join = f"{join} ({segment['transition_duration']}s)"
        lines.append(
            f"- segment {segment['index']}: "
            f"{segment['start']:.1f}s to {segment['end']:.1f}s "
            f"({segment['duration']:.1f}s), joins with {join}"
        )
    total = segments[-1]["end"] if segments else 0.0
    lines.append(f"Total clip duration: {total:.1f}s")
    captions = plan.get("captions")
    if isinstance(captions, dict) and captions:
        described = ", ".join(
            f"{key}={value}" for key, value in sorted(captions.items())
        )
        lines.append(f"Current caption overrides: {described}")
    else:
        lines.append("Current caption overrides: none (profile defaults)")
    if plan.get("niche"):
        lines.append(f"The clip was rendered with the '{plan['niche']}' style.")
    lines.append("")
    lines.append("Return the JSON edit plan now.")
    return "\n".join(lines)


def _fmt_seconds(value: float) -> str:
    return f"{value:.1f}s"


def describe_ai_edit(
    before_segments: Any,
    operations: Optional[list] = None,
    captions: Optional[dict[str, Any]] = None,
    music_override: Optional[str] = None,
) -> str:
    """A plain-language summary of an edit, from the operations applied.

    The operations are replayed one at a time over ``before_segments``
    (they must already have passed a full dry-run — this function assumes
    they apply cleanly) so each sentence can name the segment state the
    operation actually saw. The summary is generated from the applied
    operations, never from model prose.
    """
    fragments: list[str] = []
    state = normalize_segments(before_segments) if operations else []
    before_total = state[-1]["end"] if state else 0.0
    for operation in operations or []:
        op_type = operation["type"]
        index = operation.get("index")
        if op_type == "trim":
            before = state[index]
            state = apply_segment_operations(state, [operation])
            after = state[index]
            fragments.append(
                f"Trimmed segment {index + 1} from "
                f"{_fmt_seconds(before['end'] - before['start'])} to "
                f"{_fmt_seconds(after['end'] - after['start'])} "
                f"(now {_fmt_seconds(after['start'])}–"
                f"{_fmt_seconds(after['end'])})"
            )
        elif op_type == "delete":
            before = state[index]
            state = apply_segment_operations(state, [operation])
            fragments.append(
                f"Removed segment {index + 1} "
                f"({_fmt_seconds(before['end'] - before['start'])})"
            )
        elif op_type == "split":
            state = apply_segment_operations(state, [operation])
            fragments.append(
                f"Split segment {index + 1} at "
                f"{_fmt_seconds(operation['at'])}"
            )
        elif op_type == "reorder":
            state = apply_segment_operations(state, [operation])
            fragments.append("Reordered the segments")
    if captions:
        changes = []
        if captions.get("template"):
            changes.append(f"template set to {captions['template']}")
        if captions.get("font_family"):
            changes.append(f"font set to {captions['font_family']}")
        if captions.get("font_size"):
            changes.append(f"size set to {captions['font_size']}")
        if captions.get("font_color"):
            changes.append(f"colour set to {captions['font_color']}")
        if captions.get("position"):
            changes.append(f"moved to the {captions['position']}")
        if captions.get("position_y") is not None:
            changes.append(f"height set to {captions['position_y']}")
        if changes:
            fragments.append("Captions: " + ", ".join(changes))
    if music_override:
        fragments.append(f"Music set to {music_override}")
    if operations and state:
        after_total = state[-1]["end"]
        if abs(after_total - before_total) > 0.05:
            fragments.append(
                f"Clip is now {_fmt_seconds(after_total)} "
                f"(was {_fmt_seconds(before_total)})"
            )
    return ". ".join(fragments) + ("." if fragments else "")
