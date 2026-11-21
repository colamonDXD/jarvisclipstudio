#!/usr/bin/env python3
"""Verify every bundled font against FONT_MANIFEST.json.

Checks, in this order:

* every ``.ttf``/``.otf`` file in the font directory has a manifest entry;
* every entry points at a real font file;
* the file's sha256 matches the manifest;
* the licence is one of the allowed pack licences (OFL-1.1, Apache-2.0).

Any problem is printed and the process exits 1. A clean folder exits 0.
``UNVERIFIED`` is deliberately not on the allowed list: an unresolved font
must be fixed or removed before the check can pass.

Usage:
    python tools/assets/check_fonts.py
    python tools/assets/check_fonts.py --fonts-dir path/to/fonts
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_FONTS_DIR = REPO_ROOT / "backend" / "fonts"
ALLOWED_LICENSES = ("OFL-1.1", "Apache-2.0")
FONT_EXTENSIONS = (".ttf", ".otf")


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _load_entries(manifest_path: Path, problems: list[str]) -> list[dict[str, Any]]:
    try:
        payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    except OSError as error:
        problems.append(f"cannot read manifest {manifest_path}: {error}")
        return []
    except json.JSONDecodeError as error:
        problems.append(f"manifest is not valid JSON ({manifest_path}): {error}")
        return []

    entries = payload.get("fonts") if isinstance(payload, dict) else payload
    if not isinstance(entries, list):
        problems.append(
            f"manifest must be a list or have a 'fonts' list: {manifest_path}"
        )
        return []

    valid: list[dict[str, Any]] = []
    for index, entry in enumerate(entries):
        if not isinstance(entry, dict):
            problems.append(f"manifest entry {index} is not an object")
            continue
        missing = [
            field
            for field in ("file", "sha256", "family", "license")
            if not entry.get(field)
        ]
        if missing:
            problems.append(
                f"manifest entry {index} is missing {', '.join(missing)}"
            )
            continue
        valid.append(entry)
    return valid


def check_fonts(
    fonts_dir: str | Path,
    manifest_path: str | Path | None = None,
) -> list[str]:
    """Return one human-readable problem per violation; empty means clean."""
    fonts_dir = Path(fonts_dir)
    manifest_path = (
        Path(manifest_path)
        if manifest_path is not None
        else fonts_dir / "FONT_MANIFEST.json"
    )
    problems: list[str] = []

    entries = _load_entries(manifest_path, problems)
    by_file: dict[str, dict[str, Any]] = {}
    for entry in entries:
        file_name = str(entry["file"])
        if file_name in by_file:
            problems.append(f"duplicate manifest entry: {file_name}")
            continue
        by_file[file_name] = entry

    font_files = sorted(
        (
            path
            for path in fonts_dir.iterdir()
            if path.is_file() and path.suffix.lower() in FONT_EXTENSIONS
        ),
        key=lambda path: path.name.lower(),
    )

    seen = set()
    for path in font_files:
        seen.add(path.name)
        entry = by_file.get(path.name)
        if entry is None:
            problems.append(f"missing manifest entry: {path.name}")
            continue

        license_name = str(entry["license"])
        if license_name not in ALLOWED_LICENSES:
            problems.append(
                f"licence not allowed: {path.name} is {license_name} "
                f"(allowed: {', '.join(ALLOWED_LICENSES)})"
            )

        expected = str(entry["sha256"]).lower()
        actual = sha256(path)
        if actual != expected:
            problems.append(
                f"sha256 mismatch: {path.name} "
                f"(manifest {expected[:12]}..., file {actual[:12]}...)"
            )

    for file_name in sorted(by_file, key=str.lower):
        if file_name not in seen:
            problems.append(f"manifest entry has no font file: {file_name}")

    return problems


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fonts-dir", type=Path, default=DEFAULT_FONTS_DIR)
    parser.add_argument(
        "--manifest",
        type=Path,
        default=None,
        help="defaults to FONT_MANIFEST.json inside --fonts-dir",
    )
    args = parser.parse_args()

    problems = check_fonts(args.fonts_dir, args.manifest)
    if problems:
        for problem in problems:
            print(f"PROBLEM: {problem}")
        print(f"FAIL: {len(problems)} problem(s)")
        return 1

    count = sum(
        1
        for path in args.fonts_dir.iterdir()
        if path.is_file() and path.suffix.lower() in FONT_EXTENSIONS
    )
    print(f"OK: {count} font file(s) match the manifest and allowed licences")
    return 0


if __name__ == "__main__":
    sys.exit(main())
