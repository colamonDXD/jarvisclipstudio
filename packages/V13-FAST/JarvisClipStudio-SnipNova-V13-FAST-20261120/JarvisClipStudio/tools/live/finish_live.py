"""Turn every flagged live moment into a finished vertical clip, as they arrive.

  python tools/live/finish_live.py --live-dir D:/SnipNova-Dev/live-clix --credit @Clix --title "CLIX PLAYS GTA 6"

Watches ``moments.jsonl`` written by record_live.py; each new
``window_NNN.mp4`` (20 s before -> 15 s after the reaction, cut from the
buffer) is edited by the normal engine with the gaming "face on top, game
below" template (red), the credit line and the title, captions on the
seam. Finished clips land in ``<live-dir>/final/clip_NNN.mp4``.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PY = ROOT / "backend" / ".venv" / "Scripts" / "python.exe"
sys.path.insert(0, str(ROOT / "backend"))
sys.path.insert(0, str(ROOT / "tools" / "phase2" / "_winshim"))


def creator_on_camera(window: Path, onset_in_window: float = 20.0):
    """Is the streamer's face visible in the facecam around the reaction?

    On Twitch the game and the mic share one audio track, so a voice flag can
    be game characters talking (a cutscene with the facecam hidden). The
    campaign needs the creator as the focus: keep a moment only when the
    facecam is found and a face is detected in it in >= 2 of 3 frames at
    the reaction (onset-1, onset+1, onset+3 s).
    """
    import numpy as np
    from src.media.face_detection import open_yunet_detector
    from src.media.facecam_rect import find_facecam_rect, probe_times

    probe = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
                            "stream=width,height", "-of", "csv=p=0", str(window)], capture_output=True, text=True)
    w, h = [int(x) for x in probe.stdout.strip().split(",")[:2]]
    found = find_facecam_rect(str(window), w, h, probe_times([(0.0, 35.0)]))
    if found is None:
        return False, "no facecam found in the window"
    x, y, cw, ch = [int(v) for v in found.rect]
    detector = open_yunet_detector()
    hits = 0
    for t in (onset_in_window - 1, onset_in_window + 1, onset_in_window + 3):
        raw = subprocess.run(["ffmpeg", "-v", "error", "-ss", f"{t:.2f}", "-i", str(window), "-frames:v", "1",
                              "-vf", f"crop={cw}:{ch}:{x}:{y}", "-f", "rawvideo", "-pix_fmt", "bgr24", "pipe:1"],
                             capture_output=True).stdout
        if len(raw) >= cw * ch * 3:
            frame = np.frombuffer(raw[: cw * ch * 3], dtype=np.uint8).reshape(ch, cw, 3)
            if detector is not None and detector.detect(frame):
                hits += 1
    if hits < 2:
        return False, f"face visible in only {hits}/3 facecam frames (cutscene or game voices)"
    # Visible is not enough: during a cutscene the streamer sits still while
    # game characters shout. The face must move (expression + head) clearly
    # more at the reaction than in the rest of the window.
    from src.auto_director.reaction_moments import extract_face_activity

    roi = (x / w, y / h, cw / w, ch / h)
    activity = extract_face_activity(str(window), roi)
    if len(activity) < 10:
        return True, f"face visible {hits}/3 (activity not measurable)"
    lo, hi = int(max(0, onset_in_window - 2)), int(min(len(activity), onset_in_window + 5))
    at_moment = float(np.max(activity[lo:hi]))
    rest = np.concatenate([activity[:lo], activity[hi:]])
    base = float(np.median(rest)) if len(rest) else 0.0
    ratio = at_moment / max(base, 1e-6)
    if ratio >= 1.8:
        return True, f"face visible {hits}/3 and active at the moment ({ratio:.1f}x its usual movement)"
    return False, f"face visible but still at the moment ({ratio:.1f}x usual): voices were not his reaction"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--live-dir", required=True)
    ap.add_argument("--credit", default="")
    ap.add_argument("--title", default="")
    ap.add_argument("--layout", default="facecam_red")
    a = ap.parse_args()
    live = Path(a.live_dir)
    final = live / "final"
    final.mkdir(parents=True, exist_ok=True)
    env = {**os.environ, "NO_PROXY": "localhost", "PYTHONPATH": str(ROOT / "tools" / "phase2" / "_winshim")}
    done = {p.stem.split("_")[-1] for p in final.glob("clip_*.mp4")}
    while True:
        moments = []
        if (live / "moments.jsonl").exists():
            moments = [json.loads(l) for l in (live / "moments.jsonl").read_text(encoding="utf-8").splitlines() if l.strip()]
        for m in moments:
            tag = f"{m['n']:03d}"
            window = live / f"window_{tag}.mp4"
            if tag in done or not window.exists():
                continue
            t = time.time()
            ok, why = creator_on_camera(window)
            if not ok:
                with (final / "rejected.jsonl").open("a", encoding="utf-8") as fh:
                    fh.write(json.dumps({"window": window.name, "clock": m["clock"], "reason": why}) + "\n")
                print(json.dumps({"skipped": window.name, "reason": why}), flush=True)
                done.add(tag)
                continue
            cmd = [str(PY), str(ROOT / "tools" / "phase2" / "auto_edit.py"), "--video", str(window),
                   "--niche", "gaming", "--clips", "1", "--select", "whole", "--template", "auto",
                   "--layout-template", a.layout, "--out-suffix", "_live"]
            if a.credit:
                cmd += ["--credit", a.credit]
            if a.title:
                cmd += ["--hook-title", a.title]
            subprocess.run(cmd, cwd=str(ROOT), env=env, capture_output=True)
            produced = ROOT / "test-footage" / "phase2" / f"window_{tag}_live" / "clip1.mp4"
            if produced.exists():
                shutil.copy2(produced, final / f"clip_{tag}.mp4")
                print(json.dumps({"clip": f"clip_{tag}.mp4", "moment_clock": m["clock"], "signals": m["signals"],
                                  "strength": m["strength"], "render_s": round(time.time() - t, 1)}), flush=True)
            else:
                print(json.dumps({"clip": f"clip_{tag}.mp4", "error": "render failed"}), flush=True)
            done.add(tag)
        time.sleep(15)


if __name__ == "__main__":
    sys.exit(main())
