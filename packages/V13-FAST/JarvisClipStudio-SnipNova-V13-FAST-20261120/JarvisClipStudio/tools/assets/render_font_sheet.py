#!/usr/bin/env python3
"""Render a 1080x1920 contact sheet of the new caption display fonts.

Each row is a 192 px band with the word ``SNIPNOVA`` set in one of the
downloaded fonts, plus a small family label in Inter. Everything is drawn by
ffmpeg's ``drawtext`` filter; the output is a single PNG.

Usage:
    python tools/assets/render_font_sheet.py
    python tools/assets/render_font_sheet.py --out D:/path/fonts_sheet.png
"""

from __future__ import annotations

import argparse
import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
FONTS_DIR = REPO_ROOT / "backend" / "fonts"
WIDTH = 1080
HEIGHT = 1920
ROWS = (
    ("Luckiest Guy", "LuckiestGuy-Regular.ttf"),
    ("Lilita One", "LilitaOne-Regular.ttf"),
    ("Titan One", "TitanOne-Regular.ttf"),
    ("Bungee", "Bungee-Regular.ttf"),
    ("Permanent Marker", "PermanentMarker-Regular.ttf"),
    ("Paytone One", "PaytoneOne-Regular.ttf"),
    ("Passion One", "PassionOne-Regular.ttf"),
    ("Rubik Mono One", "RubikMonoOne-Regular.ttf"),
    ("Chewy", "Chewy-Regular.ttf"),
    ("Fredoka (variable wght 300-700)", "Fredoka-Variable.ttf"),
)
ROW_HEIGHT = HEIGHT // len(ROWS)


def _filter_path(path: Path) -> str:
    return str(path).replace("\\", "/").replace(":", "\\:")


def build_filter() -> str:
    filters = []
    for index in range(1, len(ROWS)):
        y = index * ROW_HEIGHT
        filters.append(
            f"drawbox=x=0:y={y}:w={WIDTH}:h=2:color=0x333333:t=fill"
        )

    label_font = _filter_path(FONTS_DIR / "Inter.ttf")
    for index, (family, file_name) in enumerate(ROWS):
        top = index * ROW_HEIGHT
        font = _filter_path(FONTS_DIR / file_name)
        filters.append(
            f"drawtext=fontfile='{font}':text='SNIPNOVA':fontcolor=white:"
            f"fontsize=96:x=(w-text_w)/2:y={top}+({ROW_HEIGHT}-text_h)/2"
            ":borderw=2:bordercolor=black"
        )
        filters.append(
            f"drawtext=fontfile='{label_font}':text='{family}':"
            f"fontcolor=0x9CA3AF:fontsize=24:x=28:y={top + ROW_HEIGHT - 38}"
        )
    return ",".join(filters)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--out",
        type=Path,
        default=REPO_ROOT / "tools" / "assets" / "out" / "fonts_sheet.png",
    )
    args = parser.parse_args()

    missing = [
        file_name
        for _, file_name in ROWS
        if not (FONTS_DIR / file_name).is_file()
    ]
    if missing:
        raise SystemExit("missing font(s): " + ", ".join(missing))

    args.out.parent.mkdir(parents=True, exist_ok=True)
    command = [
        "ffmpeg",
        "-y",
        "-hide_banner",
        "-loglevel",
        "error",
        "-f",
        "lavfi",
        "-i",
        f"color=c=0x111111:s={WIDTH}x{HEIGHT}:r=1:d=1",
        "-vf",
        build_filter(),
        "-frames:v",
        "1",
        str(args.out),
    ]
    result = subprocess.run(command, capture_output=True, text=True)
    if result.returncode != 0:
        raise SystemExit(result.stderr.strip() or "ffmpeg failed")
    print(f"wrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
