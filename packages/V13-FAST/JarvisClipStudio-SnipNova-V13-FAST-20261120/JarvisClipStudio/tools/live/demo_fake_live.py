"""End-to-end live-clipping demo on a recorded stream played as if live.

  python tools/live/demo_fake_live.py VIDEO --seconds 150 [--out DIR]

Plays VIDEO in real time into the live recorder (the same code that pulls a
real Twitch/YouTube stream), and every few seconds, WHILE IT IS "LIVE":
measures new audio, runs the fuser, and for each new confirmed candidate
whose window is already in the buffer, cuts it from the buffer and renders a
quick preview. Logs, per candidate, how long after the moment it was
detected and how long until its preview existed. No chat here (the chat
module is built separately; its path is covered by tests), so candidates
are audio-confirmed only and this is stated in the report.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))

from src.live.fuser import FuserConfig, fuse  # noqa: E402
from src.live.preview import render_preview  # noqa: E402
from src.live.recorder import LiveRecorder  # noqa: E402
from src.live.voice_scan import VoiceTrack, confirm_with_picker  # noqa: E402
from src.live.signals import AudioTrack  # noqa: E402
import threading  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("video")
    ap.add_argument("--seconds", type=float, default=150.0)
    ap.add_argument("--grace", type=float, default=150.0, help="keep processing after the source ends")
    ap.add_argument("--out", default=str(ROOT / "test-footage" / "live-demo"))
    ap.add_argument("--tick", type=float, default=5.0)
    a = ap.parse_args()
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    rec = LiveRecorder(a.video, out / "session", segment_sec=2.0, realtime_file=True).start()
    started = time.time()
    track = AudioTrack(baseline_sec=120.0)
    cfg = FuserConfig(preroll=15.0, postroll=5.0, max_len=45.0)
    done: dict = {}
    log = []
    reactions: list = []
    scans: list = []

    voice = VoiceTrack()
    flags_log: list = []

    def scanner():
        # Fast pass every few seconds: voice evidence on the NEW audio only;
        # each flag is confirmed by the full picker on +/-15 s around it once
        # those 15 s after it are recorded.
        handled = set()
        while time.time() - started < a.seconds + a.grace:
            time.sleep(3.0)
            t = time.time()
            voice.update(rec.buffer, out)
            span = rec.buffer.span()
            for flag in voice.onsets():
                key = round(flag["onset"])
                if key in handled or not span or span[1] < flag["onset"] + 15.0:
                    continue
                handled.add(key)
                c0 = time.time()
                confirmed = confirm_with_picker(rec.buffer, out, flag["onset"])
                flags_log.append({"stream_time": round(flag["onset"] - span[0], 1), "signals": flag["signals"],
                                  "flag_seen_after_onset_s": round(c0 - flag["onset"], 1),
                                  "confirm_took_s": round(time.time() - c0, 1),
                                  "confirmed": bool(confirmed)})
                if confirmed and all(abs(confirmed["onset"] - x["onset"]) > 20 for x in reactions):
                    reactions.append(confirmed)
            scans.append({"at": round(t - started, 1), "took_s": round(time.time() - t, 1)})

    threading.Thread(target=scanner, daemon=True).start()
    try:
        while time.time() - started < a.seconds + a.grace:
            time.sleep(a.tick)
            track.update(rec.buffer)
            span = rec.buffer.span()
            if not span:
                continue
            for cand in fuse([], track.zscores(), cfg, reactions=list(reactions)):
                key = round(cand.onset)
                if key in done or cand.end > span[1]:
                    continue  # already handled, or the payoff is not recorded yet
                detected = time.time()
                window = rec.buffer.cut_window(cand.start, cand.end, out / f"window_{key}.mp4")
                preview = render_preview(window, out / f"preview_{key}.mp4") if window else None
                ready = time.time()
                entry = {**cand.as_dict(),
                         "stream_time_of_onset": round(cand.onset - span[0], 1),
                         "detected_after_onset_s": round(detected - cand.onset, 1),
                         "preview_ready_after_onset_s": round(ready - cand.onset, 1),
                         "preview_render_s": round(ready - detected, 1),
                         "preview": str(preview) if preview else None,
                         "stream_still_live": rec.running}
                done[key] = entry
                log.append(entry)
                print(json.dumps(entry), flush=True)
    finally:
        rec.stop()
    (out / "live_demo_report.json").write_text(json.dumps(
        {"video": a.video, "seconds": a.seconds, "restarts": rec.restarts, "gaps": rec.buffer.gaps(),
         "voice_flags": flags_log, "candidates": log}, indent=1), encoding="utf-8")
    print(f"candidates: {len(log)}; report: {out / 'live_demo_report.json'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
