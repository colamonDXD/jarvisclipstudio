"""Tests for the .cube LUT parser and the user LUT library."""

from __future__ import annotations

import pytest

from types import SimpleNamespace

from src.auto_director.lut import parse_cube_header, parse_cube_lut
from src.media import lut_library


def make_cube(size: int = 2, *, title: str | None = None, rows: int | None = None) -> str:
    lines = []
    if title is not None:
        lines.append(f'TITLE "{title}"')
    lines.append(f"LUT_3D_SIZE {size}")
    count = size**3 if rows is None else rows
    lines.extend("0.0 0.5 1.0" for _ in range(count))
    return "\n".join(lines) + "\n"


# --- parser: valid files -------------------------------------------------------


def test_parse_valid_cube():
    info = parse_cube_lut(make_cube(3, title="Teal Orange"))
    assert info.size == 3
    assert info.title == "Teal Orange"
    assert info.data_rows == 27


def test_parse_tolerates_comments_blanks_and_unknown_keywords():
    text = (
        "# exported from somewhere\n\n"
        "LUT_3D_INPUT_RANGE 0.0 1.0\n"
        + make_cube(2)
        + "# trailing comment\n"
    )
    info = parse_cube_lut(text)
    assert info.size == 2
    assert info.data_rows == 8


def test_parse_accepts_explicit_default_domain():
    text = "DOMAIN_MIN 0.0 0.0 0.0\nDOMAIN_MAX 1 1 1\n" + make_cube(2)
    assert parse_cube_lut(text).size == 2


def test_header_parse_stops_at_data():
    # A truncated file still yields header facts for library listings.
    info = parse_cube_header(make_cube(4, rows=3))
    assert info.size == 4


# --- parser: honest rejections ---------------------------------------------------


def test_rejects_non_lut_text():
    with pytest.raises(ValueError, match="no LUT_3D_SIZE"):
        parse_cube_lut("hello world\n1 2 3\n")


def test_rejects_1d_only_with_explanation():
    text = "LUT_1D_SIZE 16\n" + "\n".join("0.0 0.0 0.0" for _ in range(16))
    with pytest.raises(ValueError, match="1D LUT"):
        parse_cube_lut(text)


@pytest.mark.parametrize("size", ["0", "1", "257", "abc", "2.5"])
def test_rejects_bad_sizes(size):
    with pytest.raises(ValueError):
        parse_cube_lut(f"LUT_3D_SIZE {size}\n0 0 0\n")


def test_rejects_truncated_data():
    with pytest.raises(ValueError, match="incomplete"):
        parse_cube_lut(make_cube(2, rows=7))


def test_rejects_extra_data():
    with pytest.raises(ValueError, match="more rows"):
        parse_cube_lut(make_cube(2, rows=9))


def test_rejects_non_numeric_data():
    text = "LUT_3D_SIZE 2\n" + "\n".join(["0 0 0"] * 7 + ["0.0 nope 1.0"]) + "\n"
    with pytest.raises(ValueError, match="non-numeric"):
        parse_cube_lut(text)


def test_rejects_non_finite_data():
    text = "LUT_3D_SIZE 2\n" + "\n".join(["0 0 0"] * 7 + ["nan 0 0"]) + "\n"
    with pytest.raises(ValueError, match="non-finite"):
        parse_cube_lut(text)


def test_rejects_wrong_row_width():
    text = "LUT_3D_SIZE 2\n" + "\n".join(["0 0 0"] * 7 + ["0 0"]) + "\n"
    with pytest.raises(ValueError, match="exactly 3 values"):
        parse_cube_lut(text)


def test_rejects_non_default_domain():
    text = "DOMAIN_MIN 0.0 0.0 0.0\nDOMAIN_MAX 0.5 0.5 0.5\n" + make_cube(2)
    with pytest.raises(ValueError, match="non-default DOMAIN"):
        parse_cube_lut(text)


# --- library storage -------------------------------------------------------------


@pytest.fixture()
def lut_dir(tmp_path, monkeypatch):
    # get_config() builds a fresh Config per call in this codebase, so
    # patch the name lut_library actually calls, not an instance.
    target = tmp_path / "luts"
    monkeypatch.setattr(
        lut_library,
        "get_config",
        lambda: SimpleNamespace(user_luts_dir=str(target)),
    )
    return target


def test_save_list_find_delete_roundtrip(lut_dir):
    saved = lut_library.save_lut("My Look.cube", make_cube(2, title="Warm").encode())
    assert saved["filename"] == "My Look.cube"
    assert saved["size"] == 2
    assert saved["display_name"] == "Warm"

    entries = lut_library.list_luts()
    assert [entry["filename"] for entry in entries] == ["My Look.cube"]
    assert lut_library.find_lut_file("My Look.cube") is not None

    assert lut_library.delete_lut("My Look.cube") is True
    assert lut_library.list_luts() == []
    assert lut_library.delete_lut("My Look.cube") is False


def test_save_dedupes_names(lut_dir):
    first = lut_library.save_lut("look.cube", make_cube(2).encode())
    second = lut_library.save_lut("look.cube", make_cube(2).encode())
    assert first["filename"] == "look.cube"
    assert second["filename"] == "look_2.cube"


def test_save_rejects_malformed_and_stores_nothing(lut_dir):
    with pytest.raises(ValueError, match="no LUT_3D_SIZE"):
        lut_library.save_lut("bad.cube", b"not a lut at all")
    assert not lut_dir.exists() or list(lut_dir.iterdir()) == []


def test_save_rejects_non_utf8(lut_dir):
    with pytest.raises(ValueError, match="UTF-8"):
        lut_library.save_lut("bad.cube", b"\xff\xfe\x00binary")


def test_find_rejects_path_traversal_and_other_extensions(lut_dir):
    lut_library.save_lut("look.cube", make_cube(2).encode())
    assert lut_library.find_lut_file("../look.cube") is None
    assert lut_library.find_lut_file("look.txt") is None
    assert lut_library.find_lut_file("") is None


def test_sanitize_lut_filename():
    assert lut_library.sanitize_lut_filename("../../etc/evil") == "evil.cube"
    assert lut_library.sanitize_lut_filename("my look!.cube") == "my look_.cube"
    assert lut_library.sanitize_lut_filename("") == "lut.cube"
