"""Phase 1 OFFLINE evidence: B-roll placement v2 vs v3 on real videos + a
production render of the planned layout with LABELLED PLACEHOLDER footage.

No stock-provider keys are used or needed: this proves placement counts,
timestamps, concepts, search phrases, guards and the cutaway / picture-in-
picture composite. Whether real Pixabay/Coverr footage is RELEVANT is a
separate, owner-run step (tools/phase1/broll_live_evidence.py).

Run from the repo root:
  backend/.venv/Scripts/python.exe tools/phase1/broll_offline_evidence.py
Outputs: test-footage/phase1/offline/ (git-ignored).
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))

from src.auto_director import edl as E  # noqa: E402
from src.media.edl_render import render_edl_clip_ffmpeg  # noqa: E402

OUT = ROOT / "test-footage" / "phase1" / "offline"
VIDEOS = [
    # (id, local path, profile, attribution)
    ("investing101", ROOT / "test-footage/phase1/wIxRKlb_1uk.mp4", "finance_business",
     "\"Investing 101\" (YouTube wIxRKlb_1uk), Creative Commons Attribution, channel 'Best Of Creative Commons'"),
    ("claude_clipper", ROOT / "test-footage/references/IY08-3TS2o8.mp4", "podcast_news",
     "\"Claude Code Can Now Clip ANY Video\" (YouTube IY08-3TS2o8), private test use only"),
    ("clipping_review", ROOT / "test-footage/references/BvzLYTIE3Hw.mp4", "education_tutorial",
     "\"Best AI Clipping Tools in 2026\" (YouTube BvzLYTIE3Hw), private test use only"),
]
CLIP_SEC = 40.0
FONT = "C\\:/Windows/Fonts/arial.ttf"


def run(cmd: list[str]) -> None:
    subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def duration_of(path: Path) -> float:
    p = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)],
                       capture_output=True, text=True)
    return float(p.stdout.strip())


def words_for(vid: str, path: Path) -> list[dict]:
    cache = OUT / f"{vid}.words.json"
    if cache.exists():
        return json.loads(cache.read_text(encoding="utf-8"))
    from faster_whisper import WhisperModel
    model = WhisperModel("base", device="cpu", compute_type="int8")
    segs, _info = model.transcribe(str(path), word_timestamps=True)
    words = [{"word": w.word.strip(), "start": round(w.start, 2), "end": round(w.end, 2)}
             for s in segs for w in (s.words or [])]
    cache.write_text(json.dumps(words), encoding="utf-8")
    return words


def windows(words: list[dict], total: float) -> list[float]:
    """Three clip starts at ~20/45/70% of the video, snapped to a pause."""
    starts = []
    for frac in (0.2, 0.45, 0.7):
        target = total * frac
        best = target
        for prev, cur in zip(words, words[1:]):
            if cur["start"] >= target and cur["start"] - prev["end"] >= 0.3:
                best = cur["start"]
                break
        starts.append(round(min(best, total - CLIP_SEC - 1), 2))
    return starts


def plan(profile: dict, words: list[dict], start: float, mode: str) -> dict:
    os.environ["BROLL_CADENCE"] = mode
    rel = [dict(w, start=round(w["start"] - start, 2), end=round(w["end"] - start, 2))
           for w in words if start <= w["start"] < start + CLIP_SEC]
    clip = {"start": 0.0, "end": CLIP_SEC, "duration": CLIP_SEC}
    return E.build_edl(clip, rel, profile, energy_peaks=[], broll_enabled=True)


def placeholder(phrase: str, layout: str, path: Path) -> None:
    text = phrase.replace(":", " ").replace("'", "")
    color = "0x7c3aed" if layout == "pip" else "0x1d4ed8"
    run(["ffmpeg", "-y", "-f", "lavfi", "-i", f"color=c={color}:s=1280x720:d=6:r=30",
         "-vf", f"drawtext=fontfile='{FONT}':text='B-ROLL PLACEHOLDER':fontcolor=white:fontsize=56:x=(w-tw)/2:y=h/2-90,"
                f"drawtext=fontfile='{FONT}':text='{text}':fontcolor=yellow:fontsize=52:x=(w-tw)/2:y=h/2+10",
         "-c:v", "libx264", "-preset", "veryfast", "-pix_fmt", "yuv420p", str(path)])


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    report = {"clip_seconds": CLIP_SEC, "videos": []}
    for vid, path, niche, attribution in VIDEOS:
        if not path.exists():
            print("missing", path)
            continue
        total = duration_of(path)
        words = words_for(vid, path)
        profile = E.load_profile(niche)
        entry = {"id": vid, "niche": niche, "attribution": attribution, "duration": round(total, 1), "clips": []}
        for k, start in enumerate(windows(words, total)):
            v2 = plan(profile, words, start, "legacy")
            v3 = plan(profile, words, start, "v3")
            entry["clips"].append({
                "source_start": start,
                "v2_count": len(v2.get("broll") or []),
                "v3_count": len(v3.get("broll") or []),
                "v3_target": E.broll_target_count(profile, CLIP_SEC),
                "v3": [{"start": b["start"], "end": b["end"], "query": b["search_query"], "concept": b.get("concept"),
                        "layout": b.get("layout"), "cue_source": b.get("cue_source"), "sense": b.get("sense"),
                        "spoken": b.get("spoken")}
                       for b in v3.get("broll") or []],
                "v3_dropped": v3.get("broll_dropped") or [],
                "v2": [{"start": b["start"], "query": b["search_query"]} for b in v2.get("broll") or []],
            })
            if k == 0:  # render the first clip of each video with placeholders
                os.environ["BROLL_CADENCE"] = "v3"
                src = OUT / f"{vid}_src.mp4"
                run(["ffmpeg", "-y", "-ss", str(start), "-t", str(CLIP_SEC), "-i", str(path),
                     "-vf", "crop=ih*9/16:ih,scale=1080:1920,setsar=1", "-r", "30", "-c:v", "libx264",
                     "-preset", "veryfast", "-crf", "22", "-c:a", "aac", "-ar", "48000", str(src)])
                for i, b in enumerate(v3.get("broll") or []):
                    ph = OUT / f"{vid}_ph{i}.mp4"
                    placeholder(b["search_query"], b.get("layout") or "cutaway", ph)
                    b["local_path"] = str(ph)
                rendered = OUT / f"{vid}_v3_render.mp4"
                ok = render_edl_clip_ffmpeg(src, rendered, v3)
                probe = subprocess.run(["ffprobe", "-v", "error", "-show_entries",
                                        "stream=codec_type,codec_name,width,height,duration", "-of", "compact",
                                        str(rendered)], capture_output=True, text=True).stdout.strip()
                frames = []
                for i, b in enumerate(v3.get("broll") or []):
                    t = (b["start"] + b["end"]) / 2
                    f = OUT / f"{vid}_insert{i}_{t:.1f}s.png"
                    run(["ffmpeg", "-y", "-ss", f"{t:.2f}", "-i", str(rendered), "-frames:v", "1",
                         "-vf", "scale=360:-2", str(f)])
                    frames.append(f.name)
                entry["render"] = {"ok": ok, "file": rendered.name, "ffprobe": probe, "frames": frames}
        report["videos"].append(entry)
        print(vid, [(c["v2_count"], c["v3_count"]) for c in entry["clips"]], flush=True)
    (OUT / "report.json").write_text(json.dumps(report, indent=1), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
