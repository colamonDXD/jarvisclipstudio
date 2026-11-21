"""Packaging gate: every shipped audio asset must be licence-clean.

  python tools/assets/check_licences.py [--assets assets]

Exit 0 and "ship-clean" when every audio file under assets/music and
assets/sfx has an ASSET_MANIFEST.json entry with a matching sha256, a
recorded source and a licence on the allowlist (CC0-1.0, PDM-1.0,
original). Otherwise lists each problem and exits 1: do not package.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))
from src.media.asset_library import check_manifest, shipped_audio  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--assets", default=str(ROOT / "assets"))
    assets = Path(ap.parse_args().assets)
    problems = check_manifest(assets)
    total = len(shipped_audio(assets))
    for problem in problems:
        print(f"BLOCK  {problem}")
    if problems:
        print(f"{len(problems)} problem(s) across {total} files: NOT ship-clean")
        return 1
    print(f"ship-clean: {total} files, every one licence-checked")
    return 0


if __name__ == "__main__":
    sys.exit(main())
