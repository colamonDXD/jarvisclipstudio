#!/usr/bin/env python3
"""Run the burned-in caption detector over real footage.

The four sources under ``claude-code/test-footage/phase2_src`` are
read-only inputs; this tool never writes to them. For each source it:

* samples 12 frames spread evenly across the file,
* records what the detector found (band, confidence, coverage),
* times the detection,
* saves one outlined debug frame showing every candidate box and the
  accepted band.

Results go to ``out/results.json`` and ``out/<source>.png``.

Usage (from the repository root, with the backend venv's python):

    python tools/burned-captions-proof/run_proof.py
    python tools/burned-captions-proof/run_proof.py --samples 24
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

import cv2

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "backend" / "src"))

from media.burned_captions import (  # noqa: E402
    candidates_in_frame,
    detect_burned_caption_band,
)

DEFAULT_FOOTAGE = Path(
    r"D:\SnipNova-Dev\claude-code\test-footage\phase2_src"
)
SOURCES = (
    ("TkHlhgTutFs.mp4", "bottom burned subtitles", True),
    ("dwZK6iQlyEw.mp4", "bottom burned subtitles", True),
    ("1oXjtK5Nsjw.mp4", "static panels", False),
    ("D3cAyFmhi_M.mp4", "vlog", False),
)


def _video_info(path: Path) -> dict:
    capture = cv2.VideoCapture(str(path))
    try:
        fps = float(capture.get(cv2.CAP_PROP_FPS) or 0.0)
        frames = float(capture.get(cv2.CAP_PROP_FRAME_COUNT) or 0.0)
        width = int(capture.get(cv2.CAP_PROP_FRAME_WIDTH) or 0)
        height = int(capture.get(cv2.CAP_PROP_FRAME_HEIGHT) or 0)
    finally:
        capture.release()
    return {
        "fps": fps,
        "frames": int(frames),
        "duration": (frames / fps) if fps > 0 else 0.0,
        "width": width,
        "height": height,
    }


def _sample_times(duration: float, count: int) -> list[float]:
    return [duration * (index + 0.5) / count for index in range(count)]


def _debug_frame(path: Path, when: float, band, output: Path) -> dict:
    capture = cv2.VideoCapture(str(path))
    try:
        capture.set(cv2.CAP_PROP_POS_MSEC, when * 1000.0)
        ok, frame = capture.read()
    finally:
        capture.release()
    if not ok or frame is None:
        return {"saved": False, "when": when}
    height, width = frame.shape[:2]
    candidates = candidates_in_frame(frame)
    for candidate in candidates:
        x0 = int(round(candidate.left * width))
        y0 = int(round(candidate.top * height))
        x1 = int(round(candidate.right * width))
        y1 = int(round(candidate.bottom * height))
        cv2.rectangle(frame, (x0, y0), (x1, y1), (0, 220, 0), 2)
    if band is not None:
        cv2.rectangle(
            frame, (0, band.y), (width - 1, band.y + band.h), (0, 0, 255), 3
        )
        label = f"band y={band.y} h={band.h} conf={band.confidence:.2f}"
    else:
        label = "no changing caption band"
    cv2.rectangle(frame, (0, 0), (width, 28), (0, 0, 0), -1)
    cv2.putText(
        frame, label, (8, 20), cv2.FONT_HERSHEY_SIMPLEX, 0.55,
        (255, 255, 255), 1, cv2.LINE_AA,
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    saved = bool(cv2.imwrite(str(output), frame))
    return {
        "saved": saved,
        "when": when,
        "candidates": len(candidates),
        "path": str(output),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--footage", type=Path, default=DEFAULT_FOOTAGE,
        help=f"directory holding the four sources (default {DEFAULT_FOOTAGE})",
    )
    parser.add_argument(
        "--out", type=Path,
        default=Path(__file__).resolve().parent / "out",
        help="output directory",
    )
    parser.add_argument(
        "--samples", type=int, default=12,
        help="frames sampled per source (default 12)",
    )
    args = parser.parse_args(argv)

    out_dir = args.out.resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    results = []
    missing = []
    for filename, label, expected in SOURCES:
        path = args.footage / filename
        if not path.is_file():
            missing.append(str(path))
            print(f"MISSING {path}")
            continue
        info = _video_info(path)
        times = _sample_times(info["duration"], args.samples)
        started = time.perf_counter()
        band = detect_burned_caption_band(
            path,
            times,
            info["width"],
            info["height"],
        )
        seconds = time.perf_counter() - started
        debug = _debug_frame(
            path,
            times[len(times) // 2] if times else 0.0,
            band,
            out_dir / f"{path.stem}.png",
        )
        entry = {
            "source": filename,
            "label": label,
            "expected_detected": expected,
            "detected": band is not None,
            "matches_expectation": (band is not None) == expected,
            "band": (
                {
                    "y": band.y,
                    "h": band.h,
                    "confidence": band.confidence,
                    "frames_with_text": band.frames_with_text,
                    "frames_checked": band.frames_checked,
                }
                if band is not None
                else None
            ),
            "seconds": round(seconds, 3),
            "samples": args.samples,
            "times": [round(value, 3) for value in times],
            "video": info,
            "debug": debug,
        }
        results.append(entry)
        print(
            f"{filename:22s} {info['width']}x{info['height']} "
            f"{info['duration']:8.1f}s  "
            f"{'DETECTED' if band is not None else 'none':8s} "
            f"{'' if band is None else f'y={band.y} h={band.h} '}"
            f"{seconds:6.2f}s"
        )

    payload = {
        "footage": str(args.footage),
        "samples_per_source": args.samples,
        "sources": results,
        "missing": missing,
    }
    (out_dir / "results.json").write_text(
        json.dumps(payload, indent=2), encoding="utf-8"
    )
    print(f"Results: {out_dir / 'results.json'}")
    return 1 if missing else 0


if __name__ == "__main__":
    sys.exit(main())
