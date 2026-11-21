"""Phase 1 LIVE evidence (run by the owner): real Pixabay/Coverr B-roll on real videos.

What it does, on the same 3 real videos as the offline evidence:
  1. reads ONLY PIXABAY_API_KEY and COVERR_API_KEY from your .env into this
     process (never printed, never written anywhere),
  2. plans B-roll with Smart B-Roll v3 (production planner),
  3. searches + downloads stock through the production resolver
     (Pixabay -> Coverr, SigLIP relevance veto, per-task de-duplication),
  4. renders each clip through the production EDL renderer,
  5. writes a report: every insert's time, the words that triggered it, the
     search phrase, provider, asset id, source page, licence, and a frame
     image of the inserted shot — plus every planned insert that did NOT make
     it and why.

Cost: free API tiers only (Pixabay and Coverr search/download). No AI model
calls. First run downloads the open SigLIP model (~800 MB, Apache-2.0).

Run from D:\\SnipNova-Dev\\claude-code :
  backend\\.venv\\Scripts\\python.exe tools\\phase1\\broll_live_evidence.py --env-file "D:\\EditedTool\\JarvisClipStudio\\.env"

Output: test-footage\\phase1\\live\\  (report.json, report.md, *_live.mp4, frames)
Takes roughly 15-30 minutes (SigLIP on CPU is the slow part).
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "test-footage" / "phase1" / "live"
WORDS = ROOT / "test-footage" / "phase1" / "offline"
KEYS = ("PIXABAY_API_KEY", "COVERR_API_KEY")
LICENCES = {
    "pixabay": "Pixabay Content License (free, no attribution required) - https://pixabay.com/service/license-summary/",
    "coverr": "Coverr License (free; credit Coverr and the creator) - https://coverr.co/license",
}


def load_keys(env_file: str) -> dict[str, str]:
    found: dict[str, str] = {}
    for line in Path(env_file).read_text(encoding="utf-8", errors="ignore").splitlines():
        m = re.match(r"\s*([A-Z0-9_]+)\s*=\s*(.*)\s*$", line)
        if m and m.group(1) in KEYS and not line.lstrip().startswith("#"):
            found[m.group(1)] = m.group(2).strip().strip('"').strip("'")
    return found


def run(cmd: list[str]) -> None:
    subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--env-file", required=True)
    a = ap.parse_args()
    keys = load_keys(a.env_file)
    print("keys:", {k: ("present" if keys.get(k) else "MISSING") for k in KEYS})
    print("SigLIP relevance check: ON (as in the deployed app); first run downloads the open model")
    if not any(keys.values()):
        print("No stock key found; nothing to test.")
        return 2
    OUT.mkdir(parents=True, exist_ok=True)
    os.environ.update({k: v for k, v in keys.items() if v})
    os.environ["TEMP_DIR"] = str(OUT / "temp")          # stock library + cache stay in the dev folder
    os.environ["BROLL_CADENCE"] = "v3"
    # Same as the deployed app: docker-compose sets BROLL_VISION_VETO_ENABLED=true
    # (the SigLIP relevance check). Without it this run would skip the check.
    os.environ.setdefault("BROLL_VISION_VETO_ENABLED", "true")
    sys.path.insert(0, str(ROOT / "backend"))
    sys.path.insert(0, str(ROOT / "tools" / "phase1"))

    from broll_offline_evidence import CLIP_SEC, VIDEOS, duration_of, windows, words_for  # noqa: E402
    from src.auto_director import edl as E  # noqa: E402
    from src.media.edl_render import render_edl_clip_ffmpeg  # noqa: E402
    from src.media.pixabay_broll import resolve_edl_broll  # noqa: E402

    report = {"clip_seconds": CLIP_SEC, "videos": []}
    for vid, path, niche, attribution in VIDEOS:
        if not path.exists():
            print("missing source video:", path)
            continue
        words = json.loads((WORDS / f"{vid}.words.json").read_text(encoding="utf-8")) if (
            WORDS / f"{vid}.words.json").exists() else words_for(vid, path)
        start = windows(words, duration_of(path))[0]
        rel = [dict(w, start=round(w["start"] - start, 2), end=round(w["end"] - start, 2))
               for w in words if start <= w["start"] < start + CLIP_SEC]
        profile = E.load_profile(niche)
        edl = E.build_edl({"start": 0.0, "end": CLIP_SEC, "duration": CLIP_SEC}, rel, profile, energy_peaks=[])
        planned = len(edl.get("broll") or [])
        print(f"{vid}: planned {planned} inserts, searching stock...", flush=True)
        resolve_edl_broll(edl)
        src = OUT / f"{vid}_src.mp4"
        run(["ffmpeg", "-y", "-ss", str(start), "-t", str(CLIP_SEC), "-i", str(path),
             "-vf", "crop=ih*9/16:ih,scale=1080:1920,setsar=1", "-r", "30", "-c:v", "libx264",
             "-preset", "veryfast", "-crf", "22", "-c:a", "aac", "-ar", "48000", str(src)])
        out = OUT / f"{vid}_live.mp4"
        ok = render_edl_clip_ffmpeg(src, out, edl)
        inserts, missing = [], []
        for i, b in enumerate(edl.get("broll") or []):
            veto = b.get("vision_veto") or {}
            item = {"start": b.get("start"), "end": b.get("end"), "spoken": b.get("spoken"), "query": b.get("search_query"),
                    "layout": b.get("layout"), "cue_source": b.get("cue_source"),
                    "veto_checked": bool(veto.get("checked")), "veto_rejected": len(veto.get("vetoed") or [])}
            if b.get("local_path") and Path(str(b["local_path"])).is_file():
                t = (float(b["start"]) + float(b["end"])) / 2
                frame = OUT / f"{vid}_insert{i}_{t:.1f}s.png"
                if ok:
                    run(["ffmpeg", "-y", "-ss", f"{t:.2f}", "-i", str(out), "-frames:v", "1", "-vf", "scale=360:-2", str(frame)])
                provider = str(b.get("provider") or "")
                item.update({"provider": provider, "asset_id": b.get("asset_id"), "source_page": b.get("url") or b.get("page_url"),
                             "licence": LICENCES.get(provider, "see provider"), "frame": frame.name if ok else None})
                inserts.append(item)
            else:
                item["not_inserted"] = b.get("not_inserted")
                missing.append(item)
        probe = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "stream=codec_type,width,height,duration",
                                "-of", "compact", str(out)], capture_output=True, text=True).stdout.strip() if ok else ""
        report["videos"].append({"id": vid, "niche": niche, "source": attribution, "clip_start": start,
                                 "planned": planned, "inserted": len(inserts), "inserts": inserts,
                                 "not_inserted": missing, "placement_drops": edl.get("broll_dropped") or [],
                                 "render_ok": ok, "render": out.name, "ffprobe": probe})
        print(f"{vid}: inserted {len(inserts)} of {planned}; render ok={ok}", flush=True)

    (OUT / "report.json").write_text(json.dumps(report, indent=1, default=str), encoding="utf-8")
    lines = ["# Live B-roll evidence", ""]
    for v in report["videos"]:
        lines += [f"## {v['id']} ({v['niche']}) - inserted {v['inserted']} of {v['planned']}", f"Source: {v['source']}", ""]
        lines += ["| time | speaker said | searched | provider | asset | image check | frame |", "|---|---|---|---|---|---|---|"]
        for i in v["inserts"]:
            check = f"ran, rejected {i['veto_rejected']}" if i["veto_checked"] else "did not run"
            lines.append(f"| {i['start']}-{i['end']}s | {i['spoken']} | {i['query']} | {i['provider']} | {i['asset_id']} | {check} | {i['frame']} |")
        for m in v["not_inserted"]:
            lines.append(f"| {m['start']}-{m['end']}s | {m['spoken']} | {m['query']} | NOT INSERTED | {m.get('not_inserted')} | |")
        lines.append("")
    (OUT / "report.md").write_text("\n".join(lines), encoding="utf-8")
    print("done:", OUT / "report.md")
    return 0


if __name__ == "__main__":
    sys.exit(main())
