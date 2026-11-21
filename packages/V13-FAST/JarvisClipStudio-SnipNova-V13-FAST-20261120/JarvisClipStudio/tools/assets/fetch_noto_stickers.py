#!/usr/bin/env python3
"""Download Noto Emoji PNGs from Google's GitHub and write a manifest.

Usage (run from repo root):
    python tools/assets/fetch_noto_stickers.py

Output:
    assets/stickers/emoji/<name>.png  — downloaded PNGs
    assets/stickers/STICKERS_MANIFEST.json
"""
from __future__ import annotations

import hashlib
import json
import sys
import urllib.request
from pathlib import Path

# ---------------------------------------------------------------------------
# Target emojis: (emoji_char, codepoint_hex_string)
# Multi-codepoint sequences use underscore-joined lowercase hex.
# ---------------------------------------------------------------------------
TARGETS: list[tuple[str, str]] = [
    ("😂", "1f602"),
    ("🤣", "1f923"),
    ("💀", "1f480"),
    ("🔥", "1f525"),
    ("😱", "1f631"),
    ("😭", "1f62d"),
    ("😡", "1f621"),
    ("🤯", "1f92f"),
    ("👀", "1f440"),
    ("💯", "1f4af"),
    ("🙏", "1f64f"),
    ("😳", "1f633"),
    ("🥶", "1f976"),
    ("🤡", "1f921"),
    ("👑", "1f451"),
    ("💸", "1f4b8"),
    ("🚨", "1f6a8"),
    ("❗", "2757"),
    ("❓", "2753"),
    ("✅", "2705"),
    ("❌", "274c"),
    ("🎯", "1f3af"),
    ("🏆", "1f3c6"),
    ("💥", "1f4a5"),
    ("⚡", "26a1"),
    ("🫡", "1fae1"),
    ("😤", "1f624"),
    ("😎", "1f60e"),
    ("🤔", "1f914"),
    ("🙄", "1f644"),
    ("😬", "1f62c"),
    ("🥲", "1f972"),
    ("😈", "1f608"),
    ("👻", "1f47b"),
    ("🧠", "1f9e0"),
    ("🫠", "1fae0"),
    ("🗿", "1f5ff"),
]

BASE_URL = "https://raw.githubusercontent.com/googlefonts/noto-emoji/main/2D/png/512/emoji_u{cp}.png"
REPO_ROOT = Path(__file__).resolve().parent.parent.parent
OUT_DIR = REPO_ROOT / "assets" / "stickers" / "emoji"
MANIFEST_PATH = REPO_ROOT / "assets" / "stickers" / "STICKERS_MANIFEST.json"


def sha256_of(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def fetch(url: str) -> bytes | None:
    try:
        with urllib.request.urlopen(url, timeout=30) as resp:
            if resp.status == 200:
                return resp.read()
    except Exception as exc:
        print(f"  SKIP {url}: {exc}", file=sys.stderr)
    return None


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    manifest: list[dict] = []

    for emoji_char, cp in TARGETS:
        filename = f"emoji_u{cp}.png"
        dest = OUT_DIR / filename
        url = BASE_URL.format(cp=cp)
        print(f"Fetching {emoji_char} → {filename} …")

        if dest.exists():
            data = dest.read_bytes()
            print(f"  cached ({len(data)} bytes)")
        else:
            data = fetch(url)
            if data is None:
                print(f"  404 / error – skipped")
                continue
            dest.write_bytes(data)
            print(f"  saved ({len(data)} bytes)")

        manifest.append(
            {
                "file": f"assets/stickers/emoji/{filename}",
                "emoji": emoji_char,
                "source_url": url,
                "license": "Apache-2.0",
                "sha256": sha256_of(data),
            }
        )

    MANIFEST_PATH.write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"\nManifest written: {MANIFEST_PATH} ({len(manifest)} entries)")


if __name__ == "__main__":
    main()
