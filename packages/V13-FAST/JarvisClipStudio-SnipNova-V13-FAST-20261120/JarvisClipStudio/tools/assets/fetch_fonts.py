#!/usr/bin/env python3
"""Fetch the short-form caption font pack from google/fonts and build a manifest.

Only official ``google/fonts`` artefacts are downloaded. Every new file is
paired with the family licence from the same repository, the licence text is
checked for an OFL-1.1 or Apache-2.0 marker, and only then is the TTF written
into ``backend/fonts``. Existing bundled fonts keep their bytes; their licence
is verified against the matching google/fonts family licence. ``THEBOLDFONT``
has no google/fonts provenance and is recorded as ``UNVERIFIED`` rather than
guessed.

Usage:
    python tools/assets/fetch_fonts.py
    python tools/assets/fetch_fonts.py --fonts-dir path/to/fonts
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import tempfile
import time
from pathlib import Path
from urllib.parse import quote

import requests

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_FONTS_DIR = REPO_ROOT / "backend" / "fonts"
RAW_ROOT = "https://raw.githubusercontent.com/google/fonts/main"
FAMILY_ROOT = "https://github.com/google/fonts/tree/main"
USER_AGENT = "supoclip-font-fetch/1.0 (+https://github.com/FujiwaraChoki/supoclip)"
ALLOWED_LICENSES = ("OFL-1.1", "Apache-2.0")
SESSION = requests.Session()

# Official google/fonts paths for the new display pack.
NEW_FONTS = (
    {
        "file": "LuckiestGuy-Regular.ttf",
        "family": "Luckiest Guy",
        "path": "apache/luckiestguy",
        "remote": "LuckiestGuy-Regular.ttf",
        "license_file": "LICENSE.txt",
    },
    {
        "file": "LilitaOne-Regular.ttf",
        "family": "Lilita One",
        "path": "ofl/lilitaone",
        "remote": "LilitaOne-Regular.ttf",
        "license_file": "OFL.txt",
    },
    {
        "file": "TitanOne-Regular.ttf",
        "family": "Titan One",
        "path": "ofl/titanone",
        "remote": "TitanOne-Regular.ttf",
        "license_file": "OFL.txt",
    },
    {
        "file": "Bungee-Regular.ttf",
        "family": "Bungee",
        "path": "ofl/bungee",
        "remote": "Bungee-Regular.ttf",
        "license_file": "OFL.txt",
    },
    {
        "file": "PermanentMarker-Regular.ttf",
        "family": "Permanent Marker",
        "path": "apache/permanentmarker",
        "remote": "PermanentMarker-Regular.ttf",
        "license_file": "LICENSE.txt",
    },
    {
        "file": "PaytoneOne-Regular.ttf",
        "family": "Paytone One",
        "path": "ofl/paytoneone",
        "remote": "PaytoneOne-Regular.ttf",
        "license_file": "OFL.txt",
    },
    {
        "file": "PassionOne-Regular.ttf",
        "family": "Passion One",
        "path": "ofl/passionone",
        "remote": "PassionOne-Regular.ttf",
        "license_file": "OFL.txt",
    },
    {
        "file": "RubikMonoOne-Regular.ttf",
        "family": "Rubik Mono One",
        "path": "ofl/rubikmonoone",
        "remote": "RubikMonoOne-Regular.ttf",
        "license_file": "OFL.txt",
    },
    {
        "file": "Chewy-Regular.ttf",
        "family": "Chewy",
        "path": "apache/chewy",
        "remote": "Chewy-Regular.ttf",
        "license_file": "LICENSE.txt",
    },
    {
        # Fredoka ships as one variable font (wght 300-700); this is the
        # official Bold-capable artefact, not a generated static instance.
        "file": "Fredoka-Variable.ttf",
        "family": "Fredoka",
        "path": "ofl/fredoka",
        "remote": "Fredoka[wdth,wght].ttf",
        "license_file": "OFL.txt",
    },
)

# Existing bundled files. The path is the google/fonts family whose licence
# covers the file; None means provenance is not provable and must stay
# UNVERIFIED.
EXISTING_FONTS = {
    "Anton-Regular.ttf": ("Anton", "ofl/anton"),
    "ArchivoBlack-Regular.ttf": ("Archivo Black", "ofl/archivoblack"),
    "Bangers-Regular.ttf": ("Bangers", "ofl/bangers"),
    "BarlowCondensed-Bold.ttf": ("Barlow Condensed", "ofl/barlowcondensed"),
    "BebasNeue-Regular.ttf": ("Bebas Neue", "ofl/bebasneue"),
    "DMSans.ttf": ("DM Sans", "ofl/dmsans"),
    "Inter.ttf": ("Inter", "ofl/inter"),
    "LeagueSpartan.ttf": ("League Spartan", "ofl/leaguespartan"),
    "Montserrat-Black.ttf": ("Montserrat", "ofl/montserrat"),
    "Montserrat-ExtraBold.ttf": ("Montserrat", "ofl/montserrat"),
    "Montserrat-Variable-wght.ttf": ("Montserrat", "ofl/montserrat"),
    "NotoSerif-Regular.ttf": ("Noto Serif", "ofl/notoserif"),
    "NunitoSans.ttf": ("Nunito Sans", "ofl/nunitosans"),
    "OpenSans.ttf": ("Open Sans", "ofl/opensans"),
    "Oswald-Variable-wght.ttf": ("Oswald", "ofl/oswald"),
    "Poppins-ExtraBold.ttf": ("Poppins", "ofl/poppins"),
    "Raleway-Variable-wght.ttf": ("Raleway", "ofl/raleway"),
    "Roboto.ttf": ("Roboto", "ofl/roboto"),
    "Rubik.ttf": ("Rubik", "ofl/rubik"),
    "Sora.ttf": ("Sora", "ofl/sora"),
    "TikTokSans-Bold.ttf": ("TikTok Sans", "ofl/tiktoksans"),
    "TikTokSans-Regular.ttf": ("TikTok Sans", "ofl/tiktoksans"),
    "Urbanist.ttf": ("Urbanist", "ofl/urbanist"),
    "WorkSans.ttf": ("Work Sans", "ofl/worksans"),
}


def _url(path: str) -> str:
    quoted = "/".join(quote(part) for part in path.split("/"))
    return f"{RAW_ROOT}/{quoted}"


def _download(url: str) -> bytes:
    last_error: Exception | None = None
    for attempt in range(3):
        try:
            response = SESSION.get(
                url, headers={"User-Agent": USER_AGENT}, timeout=60
            )
            response.raise_for_status()
            return response.content
        except requests.RequestException as error:
            last_error = error
            time.sleep(1 + attempt)
    raise SystemExit(f"download failed: {url}: {last_error}")


def classify_license(text: str) -> str:
    upper = text.upper()
    if "SIL OPEN FONT LICENSE" in upper:
        return "OFL-1.1"
    if "APACHE LICENSE" in upper and "VERSION 2.0" in upper:
        return "Apache-2.0"
    return "UNVERIFIED"


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _write_atomic(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        dir=path.parent, prefix=f".{path.name}.", delete=False
    ) as handle:
        handle.write(data)
        temporary = Path(handle.name)
    temporary.replace(path)


def fetch_new_fonts(fonts_dir: Path) -> None:
    for spec in NEW_FONTS:
        license_url = _url(f"{spec['path']}/{spec['license_file']}")
        license_text = _download(license_url).decode("utf-8", "replace")
        license_name = classify_license(license_text)
        if license_name not in ALLOWED_LICENSES:
            raise SystemExit(
                f"refusing {spec['file']}: licence is {license_name}, "
                f"source {license_url}"
            )

        font_url = _url(f"{spec['path']}/{spec['remote']}")
        font_bytes = _download(font_url)
        if not font_bytes:
            raise SystemExit(f"empty font download: {font_url}")
        _write_atomic(fonts_dir / spec["file"], font_bytes)
        print(f"downloaded {spec['file']} ({len(font_bytes)} bytes, {license_name})")


def build_manifest(fonts_dir: Path) -> dict:
    entries = []
    license_cache: dict[str, tuple[str, str]] = {}

    for spec in NEW_FONTS:
        path = fonts_dir / spec["file"]
        if not path.is_file():
            raise SystemExit(f"missing downloaded font: {path}")
        license_url = _url(f"{spec['path']}/{spec['license_file']}")
        license_text = _download(license_url).decode("utf-8", "replace")
        license_name = classify_license(license_text)
        if license_name not in ALLOWED_LICENSES:
            raise SystemExit(
                f"refusing manifest entry for {spec['file']}: {license_name}"
            )
        entries.append(
            {
                "file": spec["file"],
                "sha256": sha256(path),
                "family": spec["family"],
                "license": license_name,
                "source_url": _url(f"{spec['path']}/{spec['remote']}"),
                "license_url": license_url,
            }
        )

    for file_name, (family, source_path) in EXISTING_FONTS.items():
        path = fonts_dir / file_name
        if not path.is_file():
            raise SystemExit(f"missing bundled font: {path}")
        if source_path is None:
            license_name = "UNVERIFIED"
            source_url = ""
            license_url = ""
        else:
            if source_path not in license_cache:
                candidate_url = _url(f"{source_path}/OFL.txt")
                try:
                    license_text = _download(candidate_url).decode(
                        "utf-8", "replace"
                    )
                except SystemExit as error:
                    print(
                        f"warning: licence lookup failed for {file_name}: {error}",
                        file=sys.stderr,
                    )
                    license_text = ""
                license_cache[source_path] = (
                    classify_license(license_text),
                    candidate_url,
                )
            license_name, license_url = license_cache[source_path]
            source_url = f"{FAMILY_ROOT}/{source_path}"
        entries.append(
            {
                "file": file_name,
                "sha256": sha256(path),
                "family": family,
                "license": license_name,
                "source_url": source_url,
                "license_url": license_url,
            }
        )

    entries.sort(key=lambda entry: entry["file"].lower())
    return {
        "source": "https://github.com/google/fonts",
        "fonts": entries,
    }


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

    fonts_dir = args.fonts_dir
    manifest_path = args.manifest or fonts_dir / "FONT_MANIFEST.json"
    fonts_dir.mkdir(parents=True, exist_ok=True)

    fetch_new_fonts(fonts_dir)
    manifest = build_manifest(fonts_dir)
    _write_atomic(
        manifest_path,
        (json.dumps(manifest, indent=2) + "\n").encode("utf-8"),
    )

    unverified = [
        entry["file"]
        for entry in manifest["fonts"]
        if entry["license"] == "UNVERIFIED"
    ]
    print(f"wrote {manifest_path} with {len(manifest['fonts'])} entries")
    if unverified:
        print("UNVERIFIED: " + ", ".join(unverified))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
