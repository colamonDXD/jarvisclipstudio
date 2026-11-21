"""Fetch the official SenseVoiceSmall weights for the reaction-feature pass.

  python tools/live/fetch_sensevoice.py [--dir PATH]

The weights are NOT in git (~893 MiB). This downloads them from the
official FunAudioLLM/SenseVoiceSmall repository (Hugging Face), pinned to
one commit, and verifies every file's SHA-256 before keeping it — a file
that does not match its pin is deleted and reported, never used.

SenseVoiceSmall is by Alibaba Group / FunAudioLLM, distributed under the
FunASR Model Open Source License Agreement v1.1 (attribution: NOTICE and
docs/registries/THIRD_PARTY_LICENSES.md; licence text:
docs/registries/FunASR_MODEL_LICENSE_v1.1.txt). Only the official weights
are fetched — third-party conversions carry their own terms and are out.

Inference also needs the optional runtime packages (not installed by
this script):  pip install funasr kaldi-native-fbank
The pass itself is gated behind SENSEVOICE_FEATURES (default off); see
backend/src/live/sensevoice_scan.py.
"""
from __future__ import annotations

import argparse
import hashlib
import os
import sys
import urllib.request
from pathlib import Path

REPO = "FunAudioLLM/SenseVoiceSmall"
COMMIT = "3847d57b6bdf2dd8875cb1508d2af43d80a16bf7"
BASE = f"https://huggingface.co/{REPO}/resolve/{COMMIT}"

# filename -> (size bytes, sha256). Pins verified against the official
# repository's LFS records on 2026-11-20.
FILES = {
    "model.pt": (936_291_369, "833ca2dcfdf8ec91bd4f31cfac36d6124e0c459074d5e909aec9cabe6204a3ea"),
    "chn_jpn_yue_eng_ko_spectok.bpe.model": (377_341, "aa87f86064c3730d799ddf7af3c04659151102cba548bce325cf06ba4da4e6a8"),
    "config.yaml": (1_855, "f71e239ba36705564b5bf2d2ffd07eece07b8e3f2bbf6d2c99d8df856339ac19"),
    "am.mvn": (11_203, "29b3c740a2c0cfc6b308126d31d7f265fa2be74f3bb095cd2f143ea970896ae5"),
    "configuration.json": (396, "02810a7f8e9e8aee10370a265f7e799728ce25b4c00cdbf4602b303ee395a38e"),
}


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _default_dir() -> Path:
    env = os.getenv("SENSEVOICE_MODEL_DIR")
    if env:
        return Path(env)
    return Path.home() / ".cache" / "snipnova" / "SenseVoiceSmall"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dir", type=Path, default=None,
                    help="target directory (default: SENSEVOICE_MODEL_DIR or ~/.cache/snipnova/SenseVoiceSmall)")
    args = ap.parse_args()
    target = args.dir or _default_dir()
    target.mkdir(parents=True, exist_ok=True)
    total = sum(size for size, _ in FILES.values())
    print(f"SenseVoiceSmall (official, {REPO} @ {COMMIT[:12]}) -> {target}")
    print(f"Total download: {total / 1e6:.0f} MB. Licence: FunASR Model Licence v1.1 (Alibaba Group / FunAudioLLM).")
    failed = []
    for name, (size, sha) in FILES.items():
        dest = target / name
        if dest.is_file() and dest.stat().st_size == size and _sha256(dest) == sha:
            print(f"  ok (already present, sha256 verified): {name}")
            continue
        tmp = dest.with_suffix(dest.suffix + ".part")
        print(f"  downloading {name} ({size / 1e6:.1f} MB)...", flush=True)
        urllib.request.urlretrieve(f"{BASE}/{name}", tmp)
        if tmp.stat().st_size != size or _sha256(tmp) != sha:
            tmp.unlink(missing_ok=True)
            failed.append(name)
            print(f"  FAILED verification (size/sha256 mismatch), discarded: {name}")
            continue
        tmp.replace(dest)
        print(f"  ok (sha256 verified): {name}")
    if failed:
        print(f"FAILED: {', '.join(failed)} — weights NOT installed; the SenseVoice pass stays unavailable.")
        return 1
    print("Done. Enable with SENSEVOICE_FEATURES=on (ranking weights for the new "
          "features stay inert until trained/approved params arm them).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
