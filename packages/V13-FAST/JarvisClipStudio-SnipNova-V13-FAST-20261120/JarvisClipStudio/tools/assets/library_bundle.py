"""Pack / install the extended asset library (music + SFX variants).

  python tools/assets/library_bundle.py pack    [--out snipnova-asset-library.zip]
  python tools/assets/library_bundle.py install  snipnova-asset-library.zip [--assets assets]

The extended library (assets/music/library, assets/sfx/library/**/*.mp3) is
not in git and not in the installer's critical path (Sanji, Q14: install
must stay offline-safe and fast). It ships as one zip, installed with one
command whenever the owner wants it.

``install`` never trusts the zip: every member must be listed in
assets/ASSET_MANIFEST.json with the same sha256 and an allowed licence, and
must land under assets/music/library or assets/sfx/library. Anything else
is refused and reported, nothing outside those folders is written, and the
licence gate (check_licences.py) is run at the end.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
import zipfile
from pathlib import Path, PurePosixPath

ROOT = Path(__file__).resolve().parents[2]
ALLOWED_PREFIXES = ("music/library/", "sfx/library/")
ALLOWED_LICENSES = {"CC0-1.0", "PDM-1.0", "original"}


def _manifest(assets: Path) -> dict:
    return json.loads((assets / "ASSET_MANIFEST.json").read_text(encoding="utf-8")).get("files", {})


def pack(assets: Path, out: Path) -> int:
    files = _manifest(assets)
    count = 0
    with zipfile.ZipFile(out, "w", compression=zipfile.ZIP_STORED) as zf:
        for key in sorted(files):
            if not key.startswith(ALLOWED_PREFIXES):
                continue
            path = assets / key
            if path.is_file():
                zf.write(path, key)
                count += 1
    print(f"packed {count} files -> {out} ({out.stat().st_size / 1e6:.1f} MB)")
    return 0


def install(bundle: Path, assets: Path) -> int:
    files = _manifest(assets)
    written = refused = 0
    with zipfile.ZipFile(bundle) as zf:
        for info in zf.infolist():
            if info.is_dir():
                continue
            key = info.filename.replace("\\", "/")
            parts = PurePosixPath(key).parts
            entry = files.get(key)
            reason = None
            if ".." in parts or key.startswith("/") or not key.startswith(ALLOWED_PREFIXES):
                reason = "outside the library folders"
            elif entry is None:
                reason = "not in ASSET_MANIFEST.json"
            elif entry.get("license") not in ALLOWED_LICENSES:
                reason = f"licence {entry.get('license')!r} not allowed"
            data = b"" if reason else zf.read(info)
            if not reason and hashlib.sha256(data).hexdigest() != entry.get("sha256"):
                reason = "sha256 does not match the manifest"
            if reason:
                print(f"REFUSED {key}: {reason}")
                refused += 1
                continue
            target = assets / key
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
            written += 1
    print(f"installed {written} files, refused {refused}")
    sys.path.insert(0, str(ROOT / "backend"))
    from src.media.asset_library import check_manifest  # noqa: E402

    problems = check_manifest(assets)
    print("licence gate:", "ship-clean" if not problems else f"{len(problems)} problem(s)")
    for problem in problems:
        print("  BLOCK", problem)
    return 1 if (refused or problems) else 0


def main() -> int:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("pack")
    p.add_argument("--assets", default=str(ROOT / "assets"))
    p.add_argument("--out", default="snipnova-asset-library.zip")
    i = sub.add_parser("install")
    i.add_argument("bundle")
    i.add_argument("--assets", default=str(ROOT / "assets"))
    a = ap.parse_args()
    if a.cmd == "pack":
        return pack(Path(a.assets), Path(a.out))
    return install(Path(a.bundle), Path(a.assets))


if __name__ == "__main__":
    sys.exit(main())
