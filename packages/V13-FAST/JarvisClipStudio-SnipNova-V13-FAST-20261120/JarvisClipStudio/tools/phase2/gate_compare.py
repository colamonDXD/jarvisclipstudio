"""Which vertical layout does each real clip get under a given code tree?

  python tools/phase2/gate_compare.py --repo <tree> --out <results.jsonl> [--clips <dir>]

Cuts one 40 s excerpt (60 % into each source) from the real test videos
(streamer facecams, a video-call podcast, vlog, stand-up, talking head, a
lecture with a presenter window) and runs the plain path's own decision,
``media.reframing.describe_vertical_layout``, from ``<tree>/backend/src``.
Run it once per tree (e.g. this branch, and a worktree with another gate
applied) and compare the ``kind`` column: ``reaction`` means the facecam
layout engaged. Excerpts are cached in ``--clips`` and shared between runs.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CASES = [
    # (file under test-footage, what it is, should the facecam layout engage?)
    ("real_hd/kai_sa_20000_hd.mp4", "Kai Cenat 1080p, cam mid-left", True),
    ("real_more/kai_sa_20000.mp4", "Kai Cenat 360p, cam mid-left", True),
    ("real_more/kai_gta4_30000.mp4", "Kai Cenat GTA IV, small top-left cam", True),
    ("real_more/kai_gta4_9000.mp4", "Kai Cenat GTA IV, looking down at desk", True),
    ("real_twitch/vicens_1800.mp4", "Vicens GTA V, cam left", True),
    ("real_twitch/vicens_4200.mp4", "Vicens GTA V, cam left", True),
    ("real_twitch/vicens_7200.mp4", "Vicens GTA V, cam left", True),
    ("real_twitch/vicens_sample.mp4", "Vicens GTA V, cam left", True),
    ("real_more/speed_tw_2400.mp4", "iShowSpeed Omegle split (two people)", False),
    ("real_more/speed_tw_7000.mp4", "iShowSpeed full-frame cam", False),
    ("phase2_src/1oXjtK5Nsjw.mp4", "Video-call podcast, two windows", False),
    ("phase2_src/D3cAyFmhi_M.mp4", "Travel vlog", False),
    ("phase2_src/dwZK6iQlyEw.mp4", "Stand-up comedy", False),
    ("phase2_src/TkHlhgTutFs.mp4", "Talking head speech", False),
    ("phase2_src/-m-gudHhLxc.mp4", "Lecture: slides + presenter window", None),
]


def _probe(path: Path) -> tuple[int, int, float]:
    out = json.loads(subprocess.check_output(
        ["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
         "stream=width,height:format=duration", "-of", "json", str(path)]))
    return out["streams"][0]["width"], out["streams"][0]["height"], float(out["format"]["duration"])


def _excerpt(source: Path, clips: Path) -> Path:
    clips.mkdir(parents=True, exist_ok=True)
    target = clips / f"{source.stem}_x40.mp4"
    if not target.exists():
        _w, _h, duration = _probe(source)
        start = max(0.0, duration * 0.6)
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-ss", f"{start:.2f}", "-i", str(source),
                        "-t", "40", "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
                        "-c:a", "aac", str(target)], check=True)
    return target


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--clips", default=str(ROOT / "test-footage" / "gate_compare_clips"))
    a = ap.parse_args()
    out_path = Path(a.out).resolve()
    clips_dir = Path(a.clips).resolve()
    backend = Path(a.repo).resolve() / "backend"
    sys.path.insert(0, str(backend))
    os.chdir(backend)
    from src.media.reframing import describe_vertical_layout  # noqa: E402

    rows = []
    for rel, label, expected in CASES:
        source = ROOT / "test-footage" / rel
        if not source.exists():
            continue
        clip = _excerpt(source, clips_dir)
        width, height, _ = _probe(clip)
        t = time.time()
        try:
            kind = describe_vertical_layout(clip, width, height).get("kind")
        except Exception as exc:  # report, never hide
            kind = f"error: {exc}"
        row = {"case": rel, "label": label, "expected_reaction": expected, "kind": kind,
               "seconds": round(time.time() - t, 1)}
        rows.append(row)
        print(json.dumps(row), flush=True)
    out_path.write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
