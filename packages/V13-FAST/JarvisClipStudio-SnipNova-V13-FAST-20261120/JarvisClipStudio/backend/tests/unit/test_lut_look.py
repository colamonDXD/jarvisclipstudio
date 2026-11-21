"""Imported-LUT integration: grade validation, resolution, render chain."""

from __future__ import annotations

import pytest

from src.auto_director.look import (
    build_edl_look,
    effective_grade,
    grade_is_neutral,
    normalize_grade,
    normalize_look_overrides,
)
from src.media.edl_render import build_grade_filter_chain, build_look_fragments


# --- validation ------------------------------------------------------------------


def test_grade_accepts_lut_filename():
    assert normalize_grade({"lut": "teal.cube"}) == {"lut": "teal.cube"}


def test_grade_keeps_lut_clear_marker():
    # {"lut": None} alone must survive normalisation: it is the only way
    # to say "remove the LUT I picked earlier" through the partial form.
    assert normalize_grade({"lut": None}) == {"lut": None}


def test_grade_lut_combines_with_sliders():
    grade = normalize_grade({"lut": "teal.cube", "saturation": 10})
    assert grade == {"lut": "teal.cube", "saturation": 10}


@pytest.mark.parametrize(
    "bad", ["../teal.cube", "sub/teal.cube", "teal.txt", "", "  ", 42]
)
def test_grade_rejects_bad_lut_values(bad):
    with pytest.raises(ValueError):
        normalize_grade({"lut": bad})


def test_look_roundtrip_with_lut():
    look = normalize_look_overrides({"grade": {"lut": "teal.cube"}})
    assert look == {"grade": {"lut": "teal.cube"}}


# --- resolution --------------------------------------------------------------------


def test_lut_replaces_named_profile_base():
    grade = effective_grade("punchy", {"lut": "teal.cube"})
    # punchy's base saturation (+22) must NOT leak under the file LUT.
    assert grade["saturation"] == 0
    assert grade["lut"] == "teal.cube"


def test_lut_sliders_apply_on_top():
    grade = effective_grade("punchy", {"lut": "teal.cube", "saturation": 15})
    assert grade["saturation"] == 15
    assert grade["lut"] == "teal.cube"


def test_lut_clear_returns_to_named_base():
    grade = effective_grade("punchy", {"lut": None, "contrast": 5})
    assert "lut" not in grade
    assert grade["saturation"] == 22  # punchy base is back
    assert grade["contrast"] == 5


def test_grade_with_lut_is_not_neutral():
    assert grade_is_neutral({"lut": "teal.cube"}) is False
    assert grade_is_neutral({"lut": None}) is True


def test_build_edl_look_stamps_resolved_lut_file(tmp_path):
    cube = tmp_path / "teal.cube"
    cube.write_text("LUT_3D_SIZE 2\n")
    resolved = build_edl_look(
        {"grade": {"lut": "teal.cube"}}, "punchy", lut_file=str(cube)
    )
    assert resolved is not None
    assert resolved["grade"]["lut"] == "teal.cube"
    assert resolved["grade"]["lut_file"] == str(cube)


def test_build_edl_look_without_resolution_drops_lut():
    resolved = build_edl_look({"grade": {"lut": "teal.cube"}}, "punchy")
    assert resolved is not None
    assert "lut" not in resolved["grade"]
    assert "lut_file" not in resolved["grade"]


# --- renderer chain ------------------------------------------------------------------


def _write_cube(tmp_path):
    cube = tmp_path / "teal.cube"
    rows = "\n".join("0.0 0.0 0.0" for _ in range(8))
    cube.write_text(f"TITLE \"Teal\"\nLUT_3D_SIZE 2\n{rows}\n")
    return cube


def test_chain_starts_with_lut3d(tmp_path):
    cube = _write_cube(tmp_path)
    chain = build_grade_filter_chain(
        {"lut_file": str(cube), "saturation": 20}
    )
    assert chain.startswith(f"lut3d=file='{cube}':interp=tetrahedral")
    assert "eq=" in chain  # slider fine-tuning still composes after


def test_chain_skips_missing_lut_file(tmp_path):
    chain = build_grade_filter_chain(
        {"lut_file": str(tmp_path / "gone.cube"), "saturation": 20}
    )
    assert "lut3d" not in chain
    assert "eq=" in chain


def test_chain_without_lut_is_unchanged():
    # Byte-parity guard: grades that never carried a LUT render exactly
    # as before this feature existed.
    assert build_grade_filter_chain({"saturation": 20}) == (
        "eq=brightness=0.000:contrast=1.000:saturation=1.200"
    )
    assert build_grade_filter_chain({}) == ""
    assert build_grade_filter_chain(None) == ""


def test_look_fragments_apply_lut(tmp_path):
    cube = _write_cube(tmp_path)
    edl = {
        "look": {
            "grade": {"lut_file": str(cube)},
            "vfx": {},
        }
    }
    fragments, label, _ = build_look_fragments(edl, "[vjoined]", 0)
    assert fragments and "lut3d" in fragments[0]
    assert label != "[vjoined]"
