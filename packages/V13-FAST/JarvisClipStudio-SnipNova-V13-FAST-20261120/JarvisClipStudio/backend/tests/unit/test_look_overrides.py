"""Studio look overrides: validation, precedence, plan storage, catalog."""

import pytest

from src.auto_director import look as look_module
from src.auto_director.clip_edits import build_edit_plan
from src.media.edl_render import TRANSITION_MAP, XFADE_TRANSITIONS


# --- Validation -------------------------------------------------------------

def test_normalize_none_stays_none():
    assert look_module.normalize_look_overrides(None) is None
    assert look_module.normalize_grade(None) is None
    assert look_module.normalize_vfx(None) is None


def test_empty_look_normalises_to_none():
    assert look_module.normalize_look_overrides({}) is None
    assert look_module.normalize_look_overrides({"grade": {}}) is None
    # Turning every effect to 0 is the same as having no VFX block.
    assert look_module.normalize_look_overrides({"vfx": {"grain": 0}}) is None


def test_grade_keeps_only_present_fields_and_keeps_zeros():
    grade = look_module.normalize_grade({"saturation": 40, "contrast": 0})
    assert grade == {"saturation": 40, "contrast": 0}


def test_grade_rejects_unknown_fields_and_bad_values():
    with pytest.raises(ValueError, match="unknown grade fields"):
        look_module.normalize_grade({"vibrance": 10})
    with pytest.raises(ValueError, match="between -100 and 100"):
        look_module.normalize_grade({"exposure": 101})
    with pytest.raises(ValueError, match="must be a number"):
        look_module.normalize_grade({"exposure": "bright"})
    with pytest.raises(ValueError, match="must be a number"):
        look_module.normalize_grade({"exposure": True})


def test_vfx_drops_zeros_and_enforces_per_field_ranges():
    assert look_module.normalize_vfx({"grain": 30, "glow": 0}) == {"grain": 30}
    with pytest.raises(ValueError, match="between 0 and 40"):
        look_module.normalize_vfx({"letterbox": 41})
    with pytest.raises(ValueError, match="unknown vfx fields"):
        look_module.normalize_vfx({"lens_flare": 10})


def test_look_rejects_unknown_top_level_fields():
    with pytest.raises(ValueError, match="unknown look fields"):
        look_module.normalize_look_overrides({"music": "loud"})


def test_transition_is_canonicalised():
    assert look_module.normalize_look_overrides({"transition": "fade"}) == {
        "transition": "fade"
    }
    # Aliases resolve to the catalog entry they stand for.
    assert look_module.normalize_look_overrides({"transition": "cross_dissolve"}) == {
        "transition": "fade"
    }
    assert look_module.normalize_look_overrides({"transition": "cut"}) == {
        "transition": "hard_cut"
    }
    with pytest.raises(ValueError, match="Invalid look.transition"):
        look_module.normalize_look_overrides({"transition": "spin_around"})
    with pytest.raises(ValueError, match="non-empty string"):
        look_module.normalize_look_overrides({"transition": "  "})


# --- Catalog ------------------------------------------------------------------

def test_catalog_covers_every_xfade_name_exactly_once():
    catalog = look_module.transition_catalog()
    names = [entry["name"] for entry in catalog]
    assert names[0] == "hard_cut"
    assert sorted(names[1:]) == sorted(XFADE_TRANSITIONS)
    assert len(names) == len(set(names)) == 1 + len(XFADE_TRANSITIONS)
    for entry in catalog:
        assert TRANSITION_MAP[entry["name"]] == entry["effect"]
        assert entry["group"] in look_module.TRANSITION_GROUPS or entry["name"] == "hard_cut"


def test_canonical_transition_handles_aliases_and_junk():
    assert look_module.canonical_transition("wipe") == "wipeleft"
    assert look_module.canonical_transition("none") == "hard_cut"
    assert look_module.canonical_transition("zoomin") == "zoomin"
    assert look_module.canonical_transition("nope") is None
    assert look_module.canonical_transition(None) is None
    assert look_module.canonical_transition(7) is None


# --- Precedence: profile look first, user fields on top ------------------------

def test_effective_grade_layers_user_fields_over_profile_look():
    punchy = look_module.effective_grade("punchy", None)
    assert punchy["saturation"] == 22
    assert punchy["contrast"] == 14
    assert punchy["exposure"] == 0

    layered = look_module.effective_grade("punchy", {"saturation": -30, "exposure": 0})
    assert layered["saturation"] == -30  # user wins
    assert layered["contrast"] == 14  # untouched field keeps the profile base
    assert layered["exposure"] == 0  # explicit zero is a real override

    assert look_module.effective_grade("none", None) == look_module.NEUTRAL_GRADE
    assert look_module.effective_grade("unknown-lut", None) == look_module.NEUTRAL_GRADE
    warm = look_module.effective_grade("warm_soft", None)
    assert warm["temperature"] == 20


def test_presets_are_full_valid_grade_sets():
    presets = look_module.grade_presets()
    assert [p["name"] for p in presets] == [
        "neutral",
        "cinematic",
        "sports_punch",
        "podcast_clean",
        "warm_travel",
        "documentary",
    ]
    for preset in presets:
        assert set(preset["grade"]) == set(look_module.GRADE_FIELDS)
        assert look_module.normalize_grade(preset["grade"]) == preset["grade"]
    assert look_module.GRADE_PRESETS["neutral"] == look_module.NEUTRAL_GRADE


def test_build_edl_look_resolves_full_blocks():
    resolved = look_module.build_edl_look({"grade": {"saturation": 50}}, "none")
    assert resolved["grade"]["saturation"] == 50
    assert set(resolved["grade"]) == set(look_module.GRADE_FIELDS)
    assert resolved["vfx"] == look_module.NEUTRAL_VFX

    # A transition-only look on a punchy profile still resolves the
    # profile's grade — profile grade first is the documented order.
    resolved = look_module.build_edl_look({"transition": "fade"}, "punchy")
    assert resolved["grade"]["saturation"] == 22

    # Fully neutral resolution returns None so the renderer stays untouched.
    assert look_module.build_edl_look({"transition": "fade"}, "none") is None
    assert look_module.build_edl_look(None, "punchy") is None
    assert look_module.build_edl_look({"grade": {"exposure": 0}}, "none") is None


def test_effective_vfx_defaults_to_all_off():
    assert look_module.effective_vfx(None) == look_module.NEUTRAL_VFX
    assert look_module.effective_vfx({"grain": 12})["grain"] == 12
    assert look_module.effective_vfx({"grain": 12})["glow"] == 0


# --- Plan storage ---------------------------------------------------------------

_SEGMENTS = [
    {"start": 0, "end": 5, "transition_in": "none", "transition_duration": 0},
    {"start": 5, "end": 10, "transition_in": "cut", "transition_duration": 0},
]


def test_look_round_trips_through_the_edit_plan():
    stored = {"transition": "fade", "grade": {"saturation": 30}, "vfx": {"grain": 20}}
    plan = build_edit_plan(_SEGMENTS, niche="gaming", look=stored)
    assert plan["look"] == stored
    assert look_module.look_overrides_from_plan(plan) == stored


def test_plan_without_look_has_no_look_block():
    plan = build_edit_plan(_SEGMENTS, niche="gaming")
    assert "look" not in plan
    assert look_module.look_overrides_from_plan(plan) is None
    assert look_module.look_overrides_from_plan(None) is None
    assert look_module.look_overrides_from_plan({"look": "junk"}) is None
    # A stale/hand-edited block that no longer validates is ignored, not fatal.
    assert look_module.look_overrides_from_plan({"look": {"grade": {"x": 1}}}) is None
