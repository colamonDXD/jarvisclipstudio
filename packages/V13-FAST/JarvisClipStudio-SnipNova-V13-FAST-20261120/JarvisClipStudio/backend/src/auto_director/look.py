"""Per-clip "look" overrides for the studio panels (Transitions / Colour / VFX).

A clip's look is the user's restyle of one finished clip: a transition pick,
a colour grade and a set of visual effects. It is stored on the clip's edit
plan next to the caption overrides (see ``clip_edits.build_edit_plan``) and
consumed by the Lane-5 re-render seam, which stamps a fully-resolved copy
onto the EDL (``edl["look"]``) for the renderer
(``media.edl_render.build_look_fragments``).

The stored form is deliberately PARTIAL: the grade block holds only the
fields the user actually set, so the effective grade is always the niche
profile's own named look (``color.lut`` — ``punchy`` / ``warm_soft`` /
``none``) with the user's fields laid over it. Restyling the clip into a
different niche therefore re-bases the grade onto the new profile's look
instead of freezing the old one. VFX fields have no profile base, so a
stored ``0`` and an absent field mean the same thing and zeroes are
dropped at validation time.

One grade field is not a slider: ``lut`` names an imported ``.cube`` LUT
(see ``auto_director.lut`` / ``media.lut_library``). When set, the file
LUT replaces the named look as the grade's base — the seam resolves the
filename to a path and the renderer applies it with ``lut3d`` before the
slider filters. ``lut: None`` is the explicit clear marker.

This module is pure (no I/O, no ffmpeg): it validates and merges plain
dicts, and every impossible value raises ``ValueError`` with a message the
API layer can surface as a 422 — the same contract as
``clip_edits.normalize_caption_overrides``.
"""

from __future__ import annotations

import math
from pathlib import Path
from typing import Any, Optional

# --- Grade -----------------------------------------------------------------
# Slider space: every grade field is an integer in [-100, 100], 0 = neutral.
GRADE_FIELDS = (
    "exposure",
    "contrast",
    "saturation",
    "temperature",
    "tint",
    "highlights",
    "shadows",
)
GRADE_MIN = -100
GRADE_MAX = 100
NEUTRAL_GRADE: dict[str, int] = {field: 0 for field in GRADE_FIELDS}

# The base grade each named profile look (``color.lut``) stands for. Until
# now the renderer never consumed the name; the studio look is where it
# becomes real, as the base the user's grade overrides are laid over.
PROFILE_LOOK_GRADES: dict[str, dict[str, int]] = {
    "none": dict(NEUTRAL_GRADE),
    "punchy": {
        **NEUTRAL_GRADE,
        "contrast": 14,
        "saturation": 22,
        "temperature": 4,
        "highlights": -12,
        "shadows": -10,
    },
    "warm_soft": {
        **NEUTRAL_GRADE,
        "exposure": 2,
        "contrast": -6,
        "saturation": 8,
        "temperature": 20,
        "tint": 2,
        "highlights": -4,
        "shadows": 6,
    },
}

# One-click looks offered by the Colour panel. Full seven-field value sets:
# picking one replaces the panel's draft grade wholesale.
GRADE_PRESETS: dict[str, dict[str, int]] = {
    "neutral": dict(NEUTRAL_GRADE),
    "cinematic": {
        **NEUTRAL_GRADE,
        "exposure": -4,
        "contrast": 16,
        "saturation": -12,
        "temperature": -6,
        "highlights": -18,
        "shadows": -14,
    },
    "sports_punch": {
        **NEUTRAL_GRADE,
        "exposure": 2,
        "contrast": 18,
        "saturation": 26,
        "temperature": 2,
        "highlights": -8,
        "shadows": -8,
    },
    "podcast_clean": {
        **NEUTRAL_GRADE,
        "exposure": 3,
        "contrast": 6,
        "saturation": 4,
        "temperature": 4,
        "highlights": -2,
        "shadows": 4,
    },
    "warm_travel": {
        **NEUTRAL_GRADE,
        "exposure": 4,
        "contrast": 2,
        "saturation": 14,
        "temperature": 24,
        "tint": 2,
        "highlights": -6,
        "shadows": 8,
    },
    "documentary": {
        **NEUTRAL_GRADE,
        "contrast": 8,
        "saturation": -6,
        "temperature": -2,
        "highlights": -10,
        "shadows": -2,
    },
}

# --- VFX -------------------------------------------------------------------
# Slider space: 0 = off. Letterbox is a percentage of the frame height
# given over to black bars in total (split evenly top/bottom), so its
# useful range is smaller than the others'.
VFX_FIELDS = ("grain", "vignette", "glow", "sharpen", "letterbox", "shake")
VFX_RANGES: dict[str, tuple[int, int]] = {
    "grain": (0, 100),
    "vignette": (0, 100),
    "glow": (0, 100),
    "sharpen": (0, 100),
    "letterbox": (0, 40),
    "shake": (0, 100),
}
NEUTRAL_VFX: dict[str, int] = {field: 0 for field in VFX_FIELDS}

_LOOK_FIELDS = {"transition", "grade", "vfx"}

# --- Transition catalog ------------------------------------------------------
# The picker offers the hard cut plus every named xfade transition in
# TRANSITION_MAP, grouped for the UI. Aliases the map also accepts
# ("cut"/"none"/"cross_dissolve"/"slide"/"wipe") are input spellings, not
# separate choices: the catalog resolves them to their canonical entry.
TRANSITION_GROUPS: dict[str, tuple[str, ...]] = {
    "fades": (
        "fade", "fadeblack", "fadewhite", "fadegrays",
        "fadefast", "fadeslow", "dissolve",
    ),
    "slides": (
        "slideleft", "slideright", "slideup", "slidedown",
        "smoothleft", "smoothright", "smoothup", "smoothdown",
        "coverleft", "coverright", "coverup", "coverdown",
        "revealleft", "revealright", "revealup", "revealdown",
    ),
    "wipes": (
        "wipeleft", "wiperight", "wipeup", "wipedown",
        "wipetl", "wipetr", "wipebl", "wipebr",
        "hlslice", "hrslice", "vuslice", "vdslice",
        "hlwind", "hrwind", "vuwind", "vdwind",
    ),
    "shapes": (
        "circlecrop", "rectcrop", "circleopen", "circleclose",
        "vertopen", "vertclose", "horzopen", "horzclose",
        "radial", "diagtl", "diagtr", "diagbl", "diagbr",
    ),
    "zoom_blur": (
        "zoomin", "hblur", "distance", "pixelize", "squeezeh", "squeezev",
    ),
}


def transition_catalog() -> list[dict[str, Any]]:
    """Every pickable transition as ``{name, effect, group}`` entries.

    ``effect`` is the xfade transition the renderer will use (None for the
    hard cut). Derived from TRANSITION_MAP so the picker can never offer a
    name the renderer rejects.
    """
    from ..media.edl_render import TRANSITION_MAP, XFADE_TRANSITIONS

    catalog: list[dict[str, Any]] = [
        {"name": "hard_cut", "effect": None, "group": "fades"}
    ]
    grouped = {name for names in TRANSITION_GROUPS.values() for name in names}
    if grouped != set(XFADE_TRANSITIONS):
        raise ValueError(
            "transition groups do not cover the xfade catalog exactly: "
            f"missing={sorted(set(XFADE_TRANSITIONS) - grouped)}, "
            f"extra={sorted(grouped - set(XFADE_TRANSITIONS))}"
        )
    for group, names in TRANSITION_GROUPS.items():
        for name in names:
            catalog.append(
                {"name": name, "effect": TRANSITION_MAP[name], "group": group}
            )
    return catalog


def canonical_transition(name: Any) -> Optional[str]:
    """Resolve any accepted transition spelling to its catalog entry name.

    Aliases resolve through TRANSITION_MAP to the xfade they stand for
    (``cross_dissolve`` -> ``fade``, ``cut`` -> ``hard_cut``). Unknown or
    non-string input returns None.
    """
    if not isinstance(name, str) or not name:
        return None
    from ..media.edl_render import TRANSITION_MAP

    if name not in TRANSITION_MAP:
        return None
    effect = TRANSITION_MAP[name]
    if effect is None:
        return "hard_cut"
    return effect


# --- Validation ---------------------------------------------------------------

def _slider_value(value: Any, name: str, minimum: int, maximum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{name} must be a number")
    number = float(value)
    if not math.isfinite(number):
        raise ValueError(f"{name} must be a finite number")
    rounded = int(round(number))
    if not minimum <= rounded <= maximum:
        raise ValueError(f"{name} must be between {minimum} and {maximum}")
    return rounded


def _lut_value(value: Any) -> Optional[str]:
    """Validate the grade's ``lut`` field: a stored .cube basename or None.

    A string names an imported LUT (see ``media.lut_library``); an
    explicit None is the clear marker — the user removed a previously
    picked LUT and the profile's named look becomes the base again.
    Only basenames are stored, never paths, so a plan can never point
    the renderer outside the LUT library.
    """
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise ValueError("look.grade.lut must be a .cube filename or null")
    name = value.strip()
    if name != Path(name).name or "/" in name or "\\" in name:
        raise ValueError("look.grade.lut must be a plain filename, not a path")
    if not name.lower().endswith(".cube"):
        raise ValueError("look.grade.lut must name a .cube file")
    return name


def normalize_grade(raw: Any) -> Optional[dict[str, Any]]:
    """Validate a grade block; None input stays None.

    The result holds ONLY the fields present in the input (zeros kept — an
    explicit 0 is how the user overrides a profile-look field back to
    neutral), each an integer in [-100, 100]. The one non-slider field is
    ``lut``: the filename of an imported .cube LUT, or None as the
    explicit clear marker — it is kept even when it is the only field,
    because it carries meaning (replace / clear the grade's base look).
    """
    if raw is None:
        return None
    if not isinstance(raw, dict):
        raise ValueError("look.grade must be an object")
    unknown = sorted(set(raw) - set(GRADE_FIELDS) - {"lut"})
    if unknown:
        raise ValueError(f"unknown grade fields: {', '.join(unknown)}")
    grade: dict[str, Any] = {
        field: _slider_value(raw[field], f"look.grade.{field}", GRADE_MIN, GRADE_MAX)
        for field in GRADE_FIELDS
        if field in raw
    }
    if "lut" in raw:
        grade["lut"] = _lut_value(raw["lut"])
    return grade or None


def normalize_vfx(raw: Any) -> Optional[dict[str, int]]:
    """Validate a VFX block; None input stays None.

    Zero values are dropped (absence already means "off" for effects, so
    turning an effect back to 0 simply removes it from the stored look).
    """
    if raw is None:
        return None
    if not isinstance(raw, dict):
        raise ValueError("look.vfx must be an object")
    unknown = sorted(set(raw) - set(VFX_FIELDS))
    if unknown:
        raise ValueError(f"unknown vfx fields: {', '.join(unknown)}")
    vfx: dict[str, int] = {}
    for field in VFX_FIELDS:
        if field not in raw:
            continue
        minimum, maximum = VFX_RANGES[field]
        value = _slider_value(raw[field], f"look.vfx.{field}", minimum, maximum)
        if value:
            vfx[field] = value
    return vfx or None


def normalize_look_overrides(raw: Any) -> Optional[dict[str, Any]]:
    """Validate a look override block; None input stays None.

    Returns ``{"transition": str, "grade": {...}, "vfx": {...}}`` with only
    the sub-blocks the input actually set; a block that sets nothing (or
    whose every value is neutral/off for vfx) normalises to None so
    callers can treat "no look" uniformly.
    """
    if raw is None:
        return None
    if not isinstance(raw, dict):
        raise ValueError("look must be an object")
    unknown = sorted(set(raw) - _LOOK_FIELDS)
    if unknown:
        raise ValueError(f"unknown look fields: {', '.join(unknown)}")
    look: dict[str, Any] = {}
    if raw.get("transition") is not None:
        transition = raw["transition"]
        if not isinstance(transition, str) or not transition.strip():
            raise ValueError("look.transition must be a non-empty string")
        canonical = canonical_transition(transition.strip())
        if canonical is None:
            from ..media.edl_render import TRANSITION_MAP

            raise ValueError(
                f"Invalid look.transition '{transition}'. "
                f"Valid values: {sorted(TRANSITION_MAP)}."
            )
        look["transition"] = canonical
    grade = normalize_grade(raw.get("grade"))
    if grade is not None:
        look["grade"] = grade
    vfx = normalize_vfx(raw.get("vfx"))
    if vfx is not None:
        look["vfx"] = vfx
    return look or None


def look_overrides_from_plan(
    plan: Optional[dict[str, Any]],
) -> Optional[dict[str, Any]]:
    """The (validated) look override block of a stored edit plan, or None."""
    if not isinstance(plan, dict):
        return None
    raw = plan.get("look")
    if not isinstance(raw, dict):
        return None
    try:
        return normalize_look_overrides(raw)
    except ValueError:
        # Plans are machine-written through normalize_look_overrides; a
        # block that no longer validates (older build, hand-edited sidecar)
        # must not break the render — it is simply not a look.
        return None


# --- Resolution ---------------------------------------------------------------

def profile_look_grade(lut_name: Any) -> dict[str, int]:
    """The base grade a profile's named look (``color.lut``) stands for."""
    if isinstance(lut_name, str) and lut_name in PROFILE_LOOK_GRADES:
        return dict(PROFILE_LOOK_GRADES[lut_name])
    return dict(NEUTRAL_GRADE)


def effective_grade(
    lut_name: Any, user_grade: Optional[dict[str, Any]]
) -> dict[str, Any]:
    """Profile look first, user fields on top — the render's grade.

    An imported LUT (``user_grade["lut"]`` a filename) REPLACES the
    profile's named look as the base: a .cube already encodes a full
    colour transform, so layering it over the named look's slider base
    would double-apply a look the user never asked for. The user's
    slider fields still apply on top of the LUT. An explicit
    ``lut: None`` (or no lut field) keeps the named-look base.
    """
    lut_pick = user_grade.get("lut") if user_grade else None
    if isinstance(lut_pick, str) and lut_pick:
        grade: dict[str, Any] = dict(NEUTRAL_GRADE)
        grade["lut"] = lut_pick
    else:
        grade = profile_look_grade(lut_name)
    if user_grade:
        for field in GRADE_FIELDS:
            if field in user_grade:
                grade[field] = user_grade[field]
    return grade


def effective_vfx(user_vfx: Optional[dict[str, int]]) -> dict[str, int]:
    """The render's VFX values: stored fields over an all-off base."""
    vfx = dict(NEUTRAL_VFX)
    if user_vfx:
        for field in VFX_FIELDS:
            if field in user_vfx:
                vfx[field] = user_vfx[field]
    return vfx


def grade_is_neutral(grade: Optional[dict[str, Any]]) -> bool:
    if not isinstance(grade, dict):
        return True
    if isinstance(grade.get("lut"), str) and grade["lut"]:
        return False  # an applied LUT is a real colour transform
    return all(not grade.get(field) for field in GRADE_FIELDS)


def vfx_is_neutral(vfx: Optional[dict[str, Any]]) -> bool:
    if not isinstance(vfx, dict):
        return True
    return all(not vfx.get(field) for field in VFX_FIELDS)


def build_edl_look(
    look: Optional[dict[str, Any]],
    lut_name: Any,
    lut_file: Optional[str] = None,
) -> Optional[dict[str, Any]]:
    """Resolve a stored look into the fully-populated EDL ``look`` block.

    The renderer consumes only this resolved form (all seven grade fields,
    all six VFX fields) so it never re-derives precedence. Returns None
    when the look resolves to a fully neutral grade and no effects — the
    renderer then emits exactly the graph it emits today.

    ``lut_file`` is the seam-resolved absolute path of the grade's
    imported LUT (the service resolves the stored filename against the
    LUT library): the resolved grade carries it as ``lut_file`` for the
    renderer's ``lut3d`` filter. Without a resolved path the LUT is not
    applied — the renderer must never guess at file locations.
    """
    if not look:
        return None
    grade = effective_grade(lut_name, look.get("grade"))
    vfx = effective_vfx(look.get("vfx"))
    if grade_is_neutral(grade) and vfx_is_neutral(vfx):
        return None
    if isinstance(grade.get("lut"), str) and grade["lut"]:
        if lut_file:
            grade["lut_file"] = lut_file
        else:
            del grade["lut"]
    return {"grade": grade, "vfx": vfx}


def grade_presets() -> list[dict[str, Any]]:
    """The Colour panel's one-click looks: ``[{name, grade}]``."""
    return [
        {"name": name, "grade": dict(values)}
        for name, values in GRADE_PRESETS.items()
    ]
