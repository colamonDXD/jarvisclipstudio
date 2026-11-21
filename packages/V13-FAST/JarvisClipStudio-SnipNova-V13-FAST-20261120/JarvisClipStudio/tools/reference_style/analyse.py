#!/usr/bin/env python3
"""Reference Style Analyser - turn a reference short into a niche profile.

Point it at a finished short-form video (a Reel, a Short, a TikTok - any
file on disk) and it measures how that video was edited, then writes two
files next to the output directory of your choice:

    <name>.measurements.json   what was measured (cuts/min, caption usage,
                               colour, music bed, tempo, hook shape)
    <name>.profile.json        a JarvisClipStudio niche profile in the
                               existing profiles/*.json schema, built by a
                               deterministic mapping from the measurements

To use the profile, copy the profile JSON into
``backend/src/auto_director/profiles/`` under the file name of the stock
niche it was derived from (the ``niche`` field and the mapping summary
printed by this tool name it) - it then loads through the app's existing
profile loader exactly like a stock profile. Keep the original file if
you want the stock style back.

This is a measurement tool, not an oracle: caption detection is a
band-energy heuristic, the music-bed verdict reuses the QC lane's
speech-gap logic, and tempo is only meaningful on beat-driven music.
The method and honesty grade of every measurement are documented in
``backend/src/auto_director/reference_style.py``.

Usage:
    python tools/reference_style/analyse.py REFERENCE.mp4 [--name my-style]
                                              [--out-dir DIR]

Requires the backend's Python environment (numpy + opencv) - run it with
the same interpreter the backend tests use.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any, Optional

# The analyser lives in the backend package; make it importable when this
# script is run straight from the repository root.
_REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_REPO_ROOT / "backend"))


def _slug(text: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return slug or "reference-style"


def analyse_file(
    video: Path, name: Optional[str] = None, out_dir: Optional[Path] = None
) -> dict[str, Path]:
    """Analyse one file and write the measurements + profile JSON pair."""
    from src.auto_director.reference_style import analyse_reference

    measurements, profile, report = analyse_reference(video, name=name)
    label = _slug(name or video.stem)
    out = Path(out_dir) if out_dir else Path.cwd()
    out.mkdir(parents=True, exist_ok=True)
    measurements_path = out / f"{label}.measurements.json"
    profile_path = out / f"{label}.profile.json"
    measurements_path.write_text(
        json.dumps({"measurements": measurements, "mapping": report}, indent=2),
        encoding="utf-8",
    )
    profile_path.write_text(json.dumps(profile, indent=2), encoding="utf-8")
    return {"measurements": measurements_path, "profile": profile_path}


def _summary(video: Path, written: dict[str, Path]) -> str:
    data = json.loads(written["measurements"].read_text(encoding="utf-8"))
    m = data["measurements"]
    audio = m.get("audio") or {}
    lines = [
        f"Reference: {video} ({m['source']['duration_sec']}s, "
        f"{m['source']['width']}x{m['source']['height']})",
        f"  cuts/min: {m['cuts']['cuts_per_min']} "
        f"(median shot {m['cuts']['shot_sec']['median']}s, method {m['cuts']['method']})",
        f"  captions: {m['captions']['frame_fraction']:.0%} of frames, "
        f"position {m['captions']['position']}",
        f"  music bed: {audio.get('music_bed_present')} "
        f"(method {audio.get('music_bed_method')}, tempo {audio.get('tempo_bpm')} BPM)",
        f"  hook: face close-up {m['hook']['opens_on_face_closeup']}, "
        f"loud open {m['hook']['loud_open']}, caption from start "
        f"{m['hook']['caption_from_start']}",
        f"  base niche: {data['mapping']['base_niche']}",
        f"Wrote: {written['measurements']}",
        f"Wrote: {written['profile']}",
    ]
    return "\n".join(lines)


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Measure a reference short's editing style and emit a "
        "JarvisClipStudio niche profile in the existing schema."
    )
    parser.add_argument("video", type=Path, help="Reference video file")
    parser.add_argument(
        "--name", default=None,
        help="Style name recorded in the profile (default: video file name)",
    )
    parser.add_argument(
        "--out-dir", type=Path, default=None,
        help="Where to write the JSON pair (default: current directory)",
    )
    args = parser.parse_args(argv)

    if not args.video.is_file():
        print(f"error: no such file: {args.video}", file=sys.stderr)
        return 2
    written = analyse_file(args.video, name=args.name, out_dir=args.out_dir)
    print(_summary(args.video, written))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
