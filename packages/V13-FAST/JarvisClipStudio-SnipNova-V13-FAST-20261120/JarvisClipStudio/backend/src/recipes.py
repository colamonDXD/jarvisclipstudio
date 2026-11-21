"""Recipes: saved, named bundles of task creation settings.

A recipe is the reusable half of the create form. It stores ONLY keys
the task create path (``api.routes.tasks._create_task``) already
accepts, each validated by the same validator the create path uses, so
a stored recipe can never name a setting the create path would reject
— and applying one later is exactly equivalent to picking the same
controls by hand.

What a recipe may carry (the reusable creation settings):

- ``template_mode``, ``niche_override``, ``music_mode``,
  ``music_override``, ``broll_enabled``, ``separation_opt_in``,
  ``creation_mode``, ``clip_length``, ``target_clip_count``,
  ``caption_template`` and ``brand_kit_id`` — each checked by the
  create path's own validator.
- ``output_format`` and ``add_subtitles`` — checked with the create
  route's own rules (a known format, a real boolean).
- ``font_options`` — the create form's font dict; its sub-keys are the
  three the create path reads (``font_family``, ``font_size``,
  ``font_color``).
- ``cut_long_pauses``, ``pause_threshold_ms``,
  ``remove_filler_words``, ``filtered_words`` — the cleanup toggles
  the create path reads.
- ``include_broll`` and ``processing_mode`` — see the strictness note
  below.

What a recipe may NOT carry, by design:

- ``source`` and ``manual_ranges`` — they describe one specific video,
  not a reusable bundle.
- ``content_brief`` and ``campaign_id`` — they are one campaign's
  content and references, not creation settings.

Strictness note: where the create path *rejects* a bad value (the
``validate_*`` functions, the output_format / add_subtitles checks),
this module reuses that exact behaviour. Where the create path
*silently replaces* a bad value (an unknown ``processing_mode`` falls
back to the default, a malformed font colour becomes white), a saved
recipe rejects it instead — a bundle that is stored and reused must
mean exactly what it says, never something the create path quietly
swapped in. ``caption_template`` follows the saved-bundle rule the
Brand Kit validator already established: it must name a real template.

A settings key sent with a ``None`` value means "no choice" for every
key above, so it is dropped rather than stored: the stored payload is
exactly the set of choices the user actually made.
"""

from __future__ import annotations

import copy
import re
import uuid
from typing import Any, Dict, Optional

from .ai import validate_target_clip_count
from .auto_director.brand_kit import validate_caption_template
from .auto_director.broll_setting import validate_broll_enabled
from .auto_director.creation_modes import validate_creation_mode
from .auto_director.niche import validate_niche_override
from .auto_director.template_mode import validate_template_mode
from .clip_duration import validate_clip_length
from .media.music_library import validate_music_mode, validate_music_override
from .media.separation import validate_separation_opt_in
from .video_utils import VALID_OUTPUT_FORMATS

RECIPE_FIELDS = ("name", "settings")

#: Every settings key a recipe may store. Each one is a top-level key
#: the task create path reads; anything else is rejected, so a recipe
#: can never invent a setting.
RECIPE_SETTING_KEYS = (
    "template_mode",
    "niche_override",
    "music_mode",
    "music_override",
    "broll_enabled",
    "include_broll",
    "separation_opt_in",
    "creation_mode",
    "clip_length",
    "target_clip_count",
    "output_format",
    "add_subtitles",
    "processing_mode",
    "caption_template",
    "font_options",
    "brand_kit_id",
    "cut_long_pauses",
    "pause_threshold_ms",
    "remove_filler_words",
    "filtered_words",
)

_FONT_OPTION_KEYS = ("font_family", "font_size", "font_color")
_FONT_COLOR_RE = re.compile(r"^#[0-9A-Fa-f]{6}$")
_PROCESSING_MODES = ("fast", "balanced", "quality")


def new_recipe_id() -> str:
    return uuid.uuid4().hex


def validate_recipe_name(value: Any) -> str:
    """A recipe name: the Brand Kit name rule (present, ≤120 chars)."""
    if not isinstance(value, str) or not value.strip():
        raise ValueError("Give the recipe a name first")
    name = value.strip()
    if len(name) > 120:
        raise ValueError("Recipe names are limited to 120 characters")
    return name


def _validate_font_options(value: Any) -> Dict[str, Any]:
    """The create path's font dict, with only the sub-keys it reads.

    The create route normalises leniently (an unparseable size falls
    back, a malformed colour becomes white). A stored recipe is
    stricter for the values that would be silently replaced: the
    family must be a real name and the colour a real #RRGGBB value.
    The size is any whole number — the create path clamps it into its
    own range, identically for a hand-built payload.
    """
    if not isinstance(value, dict):
        raise ValueError("font_options must be an object")
    unknown = set(value) - set(_FONT_OPTION_KEYS)
    if unknown:
        raise ValueError(
            "Unknown font_options keys: " + ", ".join(sorted(unknown))
        )
    cleaned: Dict[str, Any] = {}
    family = value.get("font_family")
    if family is not None:
        if not isinstance(family, str) or not family.strip():
            raise ValueError("font_options.font_family must be a font name")
        cleaned["font_family"] = family.strip()
    size = value.get("font_size")
    if size is not None:
        if isinstance(size, bool) or not isinstance(size, int):
            raise ValueError("font_options.font_size must be a whole number")
        cleaned["font_size"] = size
    color = value.get("font_color")
    if color is not None:
        if not isinstance(color, str) or not _FONT_COLOR_RE.match(color):
            raise ValueError(
                "font_options.font_color must be a #RRGGBB colour"
            )
        cleaned["font_color"] = color.upper()
    return cleaned


def _validate_brand_kit_id(value: Any) -> Optional[str]:
    """The create route's own brand_kit_id rule, verbatim in effect."""
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise ValueError("Invalid brand_kit_id. Use a brand kit id or omit it.")
    return value.strip()


def _require_bool(key: str, value: Any) -> bool:
    if not isinstance(value, bool):
        raise ValueError(f"{key} must be true or false")
    return value


def validate_recipe_settings(value: Any) -> Dict[str, Any]:
    """Validate and normalise one recipe settings payload.

    Returns the normalised settings dict — the exact fragment the
    create path would be handed when the recipe is applied. Unknown
    keys raise ``ValueError``; each known key is checked by the
    validator the create path itself uses (see the module docstring
    for the two deliberate strictness differences). Keys sent as
    ``None`` are dropped: they record no choice.
    """
    if not isinstance(value, dict):
        raise ValueError("Recipe settings must be an object")
    unknown = set(value) - set(RECIPE_SETTING_KEYS)
    if unknown:
        raise ValueError(
            "Unknown recipe settings: " + ", ".join(sorted(unknown))
        )

    cleaned: Dict[str, Any] = {}

    def keep(key: str, normalised: Any) -> None:
        if normalised is not None:
            cleaned[key] = normalised

    if value.get("template_mode") is not None:
        keep("template_mode", validate_template_mode(value["template_mode"]))
    if value.get("niche_override") is not None:
        keep("niche_override", validate_niche_override(value["niche_override"]))
    if value.get("music_mode") is not None:
        keep("music_mode", validate_music_mode(value["music_mode"]))
    if value.get("music_override") is not None:
        keep("music_override", validate_music_override(value["music_override"]))
    if value.get("broll_enabled") is not None:
        keep("broll_enabled", validate_broll_enabled(value["broll_enabled"]))
    if value.get("include_broll") is not None:
        cleaned["include_broll"] = _require_bool(
            "include_broll", value["include_broll"]
        )
    if value.get("separation_opt_in") is not None:
        cleaned["separation_opt_in"] = validate_separation_opt_in(
            value["separation_opt_in"]
        )
    if value.get("creation_mode") is not None:
        keep("creation_mode", validate_creation_mode(value["creation_mode"]))
    if value.get("clip_length") is not None:
        cleaned["clip_length"] = validate_clip_length(value["clip_length"])
    if value.get("target_clip_count") is not None:
        keep(
            "target_clip_count",
            validate_target_clip_count(value["target_clip_count"]),
        )
    if value.get("output_format") is not None:
        output_format = value["output_format"]
        if output_format not in VALID_OUTPUT_FORMATS:
            raise ValueError(
                "Invalid output_format. Valid values: "
                + ", ".join(sorted(VALID_OUTPUT_FORMATS))
                + ", or omit it for vertical."
            )
        cleaned["output_format"] = output_format
    if value.get("add_subtitles") is not None:
        cleaned["add_subtitles"] = _require_bool(
            "add_subtitles", value["add_subtitles"]
        )
    if value.get("processing_mode") is not None:
        processing_mode = value["processing_mode"]
        if processing_mode not in _PROCESSING_MODES:
            raise ValueError(
                "Invalid processing_mode. Valid values: "
                + ", ".join(_PROCESSING_MODES)
                + ", or omit it."
            )
        cleaned["processing_mode"] = processing_mode
    if value.get("caption_template") is not None:
        keep(
            "caption_template",
            validate_caption_template(value["caption_template"]),
        )
    if value.get("font_options") is not None:
        cleaned["font_options"] = _validate_font_options(value["font_options"])
    if value.get("brand_kit_id") is not None:
        keep("brand_kit_id", _validate_brand_kit_id(value["brand_kit_id"]))
    if value.get("cut_long_pauses") is not None:
        cleaned["cut_long_pauses"] = _require_bool(
            "cut_long_pauses", value["cut_long_pauses"]
        )
    if value.get("pause_threshold_ms") is not None:
        threshold = value["pause_threshold_ms"]
        if isinstance(threshold, bool) or not isinstance(
            threshold, (int, float)
        ):
            raise ValueError("pause_threshold_ms must be a number")
        cleaned["pause_threshold_ms"] = threshold
    if value.get("remove_filler_words") is not None:
        cleaned["remove_filler_words"] = _require_bool(
            "remove_filler_words", value["remove_filler_words"]
        )
    if value.get("filtered_words") is not None:
        words = value["filtered_words"]
        if not isinstance(words, list) or not all(
            isinstance(word, str) for word in words
        ):
            raise ValueError("filtered_words must be a list of words")
        cleaned["filtered_words"] = list(words)
    return cleaned


def validate_recipe_payload(payload: Any, *, partial: bool = False) -> Dict[str, Any]:
    """Validate a create (``partial=False``) or update payload.

    Mirrors ``validate_kit_payload``: on create, ``name`` is required
    and ``settings`` defaults to an empty bundle; on update only the
    keys present are validated and returned, and a present ``settings``
    replaces the stored bundle wholesale (it is validated as a
    complete settings object, never merged sight-unseen).
    """
    if not isinstance(payload, dict):
        raise ValueError("Recipe data must be an object")
    unknown = set(payload) - set(RECIPE_FIELDS)
    if unknown:
        raise ValueError(f"Unknown recipe fields: {', '.join(sorted(unknown))}")
    cleaned: Dict[str, Any] = {}
    if not partial or "name" in payload:
        cleaned["name"] = validate_recipe_name(payload.get("name"))
    if "settings" in payload:
        cleaned["settings"] = validate_recipe_settings(payload.get("settings"))
    elif not partial:
        cleaned["settings"] = {}
    return cleaned


def create_fragment(recipe: Dict[str, Any]) -> Dict[str, Any]:
    """The task-create payload fragment one recipe contributes.

    Pure: the fragment is exactly the recipe's stored (already
    normalised) settings, deep-copied so a caller can merge it into a
    create payload without aliasing the stored bundle. The create
    path merges it with :func:`merge_recipe_fragment` and then runs
    the merged payload through its own validators, so applying a
    recipe is exactly equivalent to picking the same controls by hand.
    """
    settings = recipe.get("settings") or {}
    return copy.deepcopy(settings)


def merge_recipe_fragment(
    data: Dict[str, Any], fragment: Dict[str, Any]
) -> Dict[str, Any]:
    """Merge a recipe fragment under an explicit create payload.

    Explicit wins: a key present in ``data`` with a non-None value
    keeps the request's value; the fragment fills every other stored
    setting. A ``None`` in the request means "no choice" — the same
    rule recipes themselves use — so the fragment fills it, and an
    explicit ``False``/``0``/``""`` still wins (a present but invalid
    explicit value must fail the create path's own validation, never
    be quietly replaced by the recipe). Only keys a recipe may store
    are taken from the fragment; anything else in it is ignored, never
    passed on. ``font_options`` merges per sub-key, so one explicit
    font choice does not drop the recipe's other two. Neither input
    is mutated.
    """
    merged = dict(data)
    for key, value in fragment.items():
        if key not in RECIPE_SETTING_KEYS:
            continue
        explicit = data.get(key)
        if explicit is None:
            merged[key] = copy.deepcopy(value)
        elif (
            key == "font_options"
            and isinstance(explicit, dict)
            and isinstance(value, dict)
        ):
            merged[key] = {**copy.deepcopy(value), **copy.deepcopy(explicit)}
    return merged
