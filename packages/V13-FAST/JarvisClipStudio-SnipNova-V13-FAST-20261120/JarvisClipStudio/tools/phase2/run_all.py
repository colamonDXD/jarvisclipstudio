"""Run the Phase 2 auto-editor on every niche test video, one after another.

  backend\\.venv\\Scripts\\python.exe tools\\phase2\\run_all.py [--env-file "<.env>"] [--select ai|fixed]

--env-file reads only GROQ_API_KEY (free-tier AI selection) and the Pixabay/Coverr
keys (stock B-roll) into the child processes; nothing is printed or saved.
Writes test-footage\\phase2\\INDEX.md linking every per-video report.
"""
from __future__ import annotations

import argparse
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PY = ROOT / "backend" / ".venv" / "Scripts" / "python.exe"
NICHES = [
    # (niche label, profile, video, source / licence)
    ("Finance", "finance_business", "test-footage/phase1/wIxRKlb_1uk.mp4", "Investing 101, YouTube wIxRKlb_1uk, CC BY"),
    ("Podcast", "podcast_news", "test-footage/phase2_src/1oXjtK5Nsjw.mp4", "The Entrepreneur Podcast (first 12 min), YouTube 1oXjtK5Nsjw, CC BY"),
    ("Talking head", "podcast_news", "test-footage/phase2_src/TkHlhgTutFs.mp4", "Speech for future doctors, YouTube TkHlhgTutFs, marked CC BY by uploader"),
    ("Vlog", "vlog_lifestyle", "test-footage/phase2_src/D3cAyFmhi_M.mp4", "First time in Mexico City, YouTube D3cAyFmhi_M, CC BY"),
    ("Comedy", "meme_comedy", "test-footage/phase2_src/dwZK6iQlyEw.mp4", "Problems of an only child (stand-up), YouTube dwZK6iQlyEw, CC BY"),
    ("Education", "education_tutorial", "test-footage/phase2_src/-m-gudHhLxc.mp4", "PHO103 Speech Anatomy lecture, YouTube -m-gudHhLxc, CC BY"),
    ("Gaming / streamer", "gaming", "test-footage/real_more/kai_sa_20000.mp4", "Kai Cenat GTA San Andreas stream sample (Twitch), private test use only"),
    ("Gaming HD", "gaming", "test-footage/real_hd/kai_sa_20000_hd.mp4", "Same Kai Cenat section at 1080p60 (Twitch VOD 2922436506, 20000-20300 s), private test use only"),
]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--env-file")
    ap.add_argument("--select", choices=("ai", "fixed"), default="ai")
    ap.add_argument("--clips", type=int, default=2)
    ap.add_argument("--only", help="run one niche label only")
    a = ap.parse_args()
    if a.env_file and not Path(a.env_file).is_file():
        print(f"--env-file not found: {a.env_file}\n"
              "Point it at the .env that holds GROQ_API_KEY (and optionally PIXABAY_API_KEY / COVERR_API_KEY).\n"
              "Nothing was run.", file=sys.stderr)
        return 2
    rows = []
    for label, profile, video, source in NICHES:
        if a.only and a.only.lower() != label.lower():
            continue
        path = ROOT / video
        if not path.exists():
            rows.append((label, profile, "missing video", ""))
            continue
        cmd = [str(PY), str(ROOT / "tools" / "phase2" / "auto_edit.py"), "--video", str(path), "--niche", profile,
               "--clips", str(a.clips), "--select", a.select]
        if a.env_file:
            cmd += ["--env-file", a.env_file]
        print(f"== {label} ({profile})", flush=True)
        t = time.time()
        r = subprocess.run(cmd, cwd=str(ROOT))
        status = "ok" if r.returncode == 0 else f"FAILED (exit {r.returncode})"
        rows.append((label, profile, f"{status} in {time.time() - t:.0f}s", f"{path.stem}/report.md"))
        print(f"   {status}", flush=True)
    out = ROOT / "test-footage" / "phase2" / "INDEX.md"
    out.parent.mkdir(parents=True, exist_ok=True)
    if a.only and out.exists():
        # A single-niche re-run updates its own row and keeps the others.
        previous = {}
        for line in out.read_text(encoding="utf-8").splitlines():
            cells = [c.strip() for c in line.strip().strip("|").split("|")]
            if len(cells) == 4 and cells[0] not in ("Niche", "---"):
                previous[cells[0]] = (cells[0], cells[1].strip("`"), cells[2], cells[3])
        for row in rows:
            previous[row[0]] = row
        order = [label for label, *_ in NICHES]
        rows = sorted(previous.values(), key=lambda r: order.index(r[0]) if r[0] in order else len(order))
    index = ["# Phase 2 auto-edit runs", "", f"Selection: {a.select}", "",
             "| Niche | Profile | Result | Report |", "|---|---|---|---|"]
    index += [f"| {l} | `{p}` | {s} | {r} |" for l, p, s, r in rows]
    index += ["", "Sources: " + "; ".join(f"{l}: {src}" for l, _p, _v, src in NICHES)]
    out.write_text("\n".join(index), encoding="utf-8")
    print("done:", out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
