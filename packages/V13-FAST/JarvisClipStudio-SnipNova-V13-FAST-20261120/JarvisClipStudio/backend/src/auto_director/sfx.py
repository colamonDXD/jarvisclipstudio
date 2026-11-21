"""Per-clip SFX placements for the clip editor's SFX panel.

A placement is one user decision: play this library sound effect at
this second of the clip, at this gain. Placements are stored on the
clip's edit plan (see ``clip_edits.build_edit_plan``) and consumed by
the re-render seam, which appends them to the EDL's ``sfx_events`` —
the exact list ``media.edl_render`` already mixes into the export
(``{name, at_sec, gain_db}`` per event, resolved against
``assets/sfx``).

Semantics mirror the other editor blocks but with one deliberate
difference: an EMPTY list is meaningful. ``sfx: []`` says "I removed
every placement", while an absent block says "never touched — carry
the stored placements forward". The stored form therefore keeps the
empty list instead of normalising it away.

This module is pure (no I/O): structural validation only. Whether a
name resolves to a real library file is the service layer's check
(it owns the library listing), so a plan written against a file that
was later deleted fails honestly at edit time, not silently here.
"""

from __future__ import annotations

import math
from pathlib import Path
from typing import Any, Optional

# The extensions the renderer's asset resolver understands; a placement
# naming anything else could never be heard, so it is rejected here.
SFX_EXTENSIONS = (".mp3", ".wav", ".m4a", ".aac", ".ogg", ".flac", ".opus", ".webm")

MAX_SFX_PLACEMENTS = 16
GAIN_DB_MIN = -30.0
GAIN_DB_MAX = 12.0
AT_SEC_MAX = 3600.0  # one hour: beyond any clip this app renders

_PLACEMENT_FIELDS = {"name", "at_sec", "gain_db"}


def _placement_name(value: Any) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("sfx placement name must be a non-empty string")
    name = value.strip()
    if name != Path(name).name or "/" in name or "\\" in name:
        raise ValueError("sfx placement name must be a plain filename")
    if Path(name).suffix.lower() not in SFX_EXTENSIONS:
        raise ValueError(
            f"sfx placement name '{name}' is not an audio file "
            f"({'/'.join(SFX_EXTENSIONS)})"
        )
    return name


def _number(value: Any, field: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"sfx placement {field} must be a number")
    number = float(value)
    if not math.isfinite(number):
        raise ValueError(f"sfx placement {field} must be finite")
    return number


def normalize_sfx_placements(raw: Any) -> Optional[list[dict[str, Any]]]:
    """Validate a placements block; None input stays None.

    Returns a list of ``{"name", "at_sec", "gain_db"}`` dicts (possibly
    empty — the explicit clear), each value rounded to render precision.
    """
    if raw is None:
        return None
    if not isinstance(raw, list):
        raise ValueError("sfx must be a list of placements")
    if len(raw) > MAX_SFX_PLACEMENTS:
        raise ValueError(
            f"too many sfx placements (max {MAX_SFX_PLACEMENTS})"
        )
    placements: list[dict[str, Any]] = []
    for index, entry in enumerate(raw):
        if not isinstance(entry, dict):
            raise ValueError(f"sfx placement {index} must be an object")
        unknown = sorted(set(entry) - _PLACEMENT_FIELDS)
        if unknown:
            raise ValueError(
                f"sfx placement {index} has unknown fields: {', '.join(unknown)}"
            )
        name = _placement_name(entry.get("name"))
        at_sec = _number(entry.get("at_sec", 0.0), "at_sec")
        if not 0.0 <= at_sec <= AT_SEC_MAX:
            raise ValueError(
                f"sfx placement at_sec must be between 0 and {AT_SEC_MAX:g}"
            )
        gain_db = _number(entry.get("gain_db", 0.0), "gain_db")
        if not GAIN_DB_MIN <= gain_db <= GAIN_DB_MAX:
            raise ValueError(
                f"sfx placement gain_db must be between {GAIN_DB_MIN:g} "
                f"and {GAIN_DB_MAX:g}"
            )
        placements.append(
            {
                "name": name,
                "at_sec": round(at_sec, 3),
                "gain_db": round(gain_db, 2),
            }
        )
    return placements


def sfx_placements_from_plan(
    plan: Optional[dict[str, Any]],
) -> Optional[list[dict[str, Any]]]:
    """The (validated) placements stored on an edit plan, or None.

    A stored block that no longer validates (older build, hand-edited
    sidecar) is treated as absent rather than breaking the render —
    the same contract as ``look_overrides_from_plan``.
    """
    if not isinstance(plan, dict):
        return None
    raw = plan.get("sfx")
    if raw is None:
        return None
    try:
        return normalize_sfx_placements(raw)
    except ValueError:
        return None
