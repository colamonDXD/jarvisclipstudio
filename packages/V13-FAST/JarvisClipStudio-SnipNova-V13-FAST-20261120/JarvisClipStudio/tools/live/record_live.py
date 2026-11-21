"""Record a live stream and cut reaction moments while it runs (live MVP runner).

  python tools/live/record_live.py URL --out DIR [--keep-hours 4]

Only for streams the user owns or is authorised to clip (e.g. a creator's
official clipping campaign). Writes into DIR:
- session/ : the rolling buffer (stream-copied segments)
- moments.jsonl : one line per voice-flagged reaction (stream time, signals, strength)
- preview_<n>.mp4 : vertical quick preview, 20 s before -> 15 s after each flag
- window_<n>.mp4 : the same span in source quality (for the full edit later)
- status.json : buffer span, gaps, restarts, last error (updated every tick)
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))

from src.live.preview import render_preview  # noqa: E402
from src.live.recorder import LiveRecorder  # noqa: E402
from src.live.voice_scan import VoiceTrack  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("url")
    ap.add_argument("--out", required=True)
    ap.add_argument("--keep-hours", type=float, default=4.0)
    ap.add_argument("--preroll", type=float, default=20.0)
    ap.add_argument("--postroll", type=float, default=15.0)
    ap.add_argument("--tick", type=float, default=10.0)
    a = ap.parse_args()
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    rec = LiveRecorder(a.url, out / "session", segment_sec=4.0, keep_seconds=a.keep_hours * 3600,
                       max_height=1080).start()
    voice = VoiceTrack(baseline_sec=240.0)
    done: set = set()
    n = 0
    t_start = time.time()
    while True:
        time.sleep(a.tick)
        try:
            voice.update(rec.buffer, out)
        except Exception as exc:  # keep recording whatever happens to analysis
            print("voice pass error:", exc, flush=True)
        span = rec.buffer.span()
        status = {"now": time.strftime("%H:%M:%S"), "running": rec.running, "restarts": rec.restarts,
                  "last_error": (rec.last_error or "")[:300], "gaps": rec.buffer.gaps()[-5:],
                  "buffered_min": round((span[1] - span[0]) / 60, 1) if span else 0, "moments": n}
        (out / "status.json").write_text(json.dumps(status, indent=1), encoding="utf-8")
        if not span:
            continue
        for flag in voice.onsets():
            key = round(flag["onset"])
            if key in done or span[1] < flag["onset"] + a.postroll:
                continue
            done.add(key)
            n += 1
            window = rec.buffer.cut_window(flag["onset"] - a.preroll, flag["onset"] + a.postroll,
                                           out / f"window_{n:03d}.mp4")
            preview = render_preview(window, out / f"preview_{n:03d}.mp4") if window else None
            entry = {"n": n, "clock": time.strftime("%H:%M:%S", time.localtime(flag["onset"])),
                     "stream_min": round((flag["onset"] - span[0]) / 60, 2), "signals": flag["signals"],
                     "strength": round(flag["peak"], 1), "preview": str(preview) if preview else None,
                     "ready_after_s": round(time.time() - flag["onset"], 1)}
            with (out / "moments.jsonl").open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(entry) + "\n")
            print(json.dumps(entry), flush=True)


if __name__ == "__main__":
    sys.exit(main())
