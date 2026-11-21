"""Parsing and validation for user-imported ``.cube`` LUT files.

The Studio Colour panel lets a user import a ``.cube`` colour look-up
table and apply it to a clip.  The file is parsed here — strictly —
before it is stored, and the renderer later applies it with ffmpeg's
``lut3d`` filter (see ``media.edl_render.build_grade_filter_chain``).

Honesty rules, matching the rest of the auto-director package:

* Only 3D LUTs are supported.  A file that declares ``LUT_1D_SIZE``
  without a ``LUT_3D_SIZE`` is rejected with a message that says so —
  ffmpeg's ``lut3d`` cannot apply it and pretending otherwise would
  silently render the wrong colours.
* ``DOMAIN_MIN`` / ``DOMAIN_MAX`` must be the cube default
  (0 0 0 / 1 1 1) when declared.  ``lut3d`` has no domain remap, so a
  non-default domain is rejected instead of being silently misapplied.
* The data section must contain exactly ``size ** 3`` rows of three
  finite numbers.  Truncated, oversized or non-numeric data is a hard
  error, never a partial apply.

This module is pure (no I/O): callers pass text in and get either a
:class:`CubeLutInfo` or a ``ValueError`` whose message the API layer
can surface as a 4xx, the same contract as ``auto_director.look``.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from typing import Optional

# practical bounds: size 2 is the smallest meaningful cube; 256^3 rows
# is ~50M lines, far beyond any real grading LUT (33/65 are standard).
LUT_SIZE_MIN = 2
LUT_SIZE_MAX = 256
# Uploads are capped well below the theoretical maximum: a 65^3 cube is
# ~3 MB of text, so 48 MB leaves generous headroom while keeping a
# hostile or accidental giant file out of memory.
MAX_LUT_BYTES = 48 * 1024 * 1024

_DEFAULT_DOMAIN_MIN = (0.0, 0.0, 0.0)
_DEFAULT_DOMAIN_MAX = (1.0, 1.0, 1.0)

_TITLE_RE = re.compile(r'^"([^"]*)"\s*$')


@dataclass(frozen=True)
class CubeLutInfo:
    """Validated facts about one ``.cube`` file."""

    size: int
    title: Optional[str]
    data_rows: int


def _parse_float_row(tokens: list[str], what: str) -> tuple[float, float, float]:
    if len(tokens) != 3:
        raise ValueError(f"{what} must have exactly 3 values, got {len(tokens)}")
    try:
        values = tuple(float(token) for token in tokens)
    except ValueError:
        raise ValueError(f"{what} contains a non-numeric value") from None
    if not all(math.isfinite(value) for value in values):
        raise ValueError(f"{what} contains a non-finite value")
    return values  # type: ignore[return-value]


def parse_cube_header(text: str) -> CubeLutInfo:
    """Validate the keyword section and return its facts.

    Stops at the first data row: cheap enough for library listings that
    only need size/title.  Use :func:`parse_cube_lut` to validate the
    full data section before accepting an upload.
    """
    size: Optional[int] = None
    title: Optional[str] = None
    saw_1d = False
    domain_min = _DEFAULT_DOMAIN_MIN
    domain_max = _DEFAULT_DOMAIN_MAX

    for raw_line in text.lstrip("\ufeff").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        tokens = line.split()
        keyword = tokens[0].upper()
        if keyword == "LUT_3D_SIZE":
            if len(tokens) != 2:
                raise ValueError("LUT_3D_SIZE must have exactly one value")
            try:
                size = int(tokens[1])
            except ValueError:
                raise ValueError("LUT_3D_SIZE is not an integer") from None
            if not LUT_SIZE_MIN <= size <= LUT_SIZE_MAX:
                raise ValueError(
                    f"LUT_3D_SIZE {size} is outside the supported range "
                    f"{LUT_SIZE_MIN}-{LUT_SIZE_MAX}"
                )
        elif keyword == "LUT_1D_SIZE":
            saw_1d = True
        elif keyword == "TITLE":
            match = _TITLE_RE.match(line[len(tokens[0]):].strip())
            if match:
                title = match.group(1)
        elif keyword == "DOMAIN_MIN":
            domain_min = _parse_float_row(tokens[1:], "DOMAIN_MIN")
        elif keyword == "DOMAIN_MAX":
            domain_max = _parse_float_row(tokens[1:], "DOMAIN_MAX")
        elif keyword[0].isalpha() or keyword[0] == "_":
            # An unrecognised keyword line (LUT_3D_INPUT_RANGE and
            # friends): real-world cubes carry a few; keywords never
            # appear in the data section, so skipping is safe.
            continue
        else:
            break  # first data row: the header is complete

    if size is None:
        if saw_1d:
            raise ValueError(
                "this file is a 1D LUT (LUT_1D_SIZE) — only 3D .cube "
                "LUTs are supported"
            )
        raise ValueError("not a .cube LUT: no LUT_3D_SIZE declaration found")
    if domain_min != _DEFAULT_DOMAIN_MIN or domain_max != _DEFAULT_DOMAIN_MAX:
        raise ValueError(
            "this .cube declares a non-default DOMAIN_MIN/DOMAIN_MAX, "
            "which the lut3d renderer cannot remap — export it with the "
            "standard 0..1 domain to use it here"
        )
    return CubeLutInfo(size=size, title=title, data_rows=0)


def parse_cube_lut(text: str) -> CubeLutInfo:
    """Fully validate a ``.cube`` file's text (header + every data row)."""
    info = parse_cube_header(text)
    expected = info.size ** 3
    rows = 0
    seen_header_end = False
    for raw_line in text.lstrip("\ufeff").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        first = line.split(None, 1)[0]
        if not seen_header_end:
            if first[0].isalpha() or first[0] == "_":
                continue  # still in the keyword section
            seen_header_end = True
        _parse_float_row(line.split(), f"LUT data row {rows + 1}")
        rows += 1
        if rows > expected:
            raise ValueError(
                f"LUT data has more rows than LUT_3D_SIZE {info.size} "
                f"allows ({expected})"
            )
    if rows != expected:
        raise ValueError(
            f"LUT data is incomplete: LUT_3D_SIZE {info.size} needs "
            f"{expected} rows, found {rows}"
        )
    return CubeLutInfo(size=info.size, title=info.title, data_rows=rows)
