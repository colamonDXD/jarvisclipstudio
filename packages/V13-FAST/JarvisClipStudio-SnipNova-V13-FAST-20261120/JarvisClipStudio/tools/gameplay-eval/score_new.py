"""Run the frame-evidence detector and score it. Usage: score_new.py <video> [clip_s] [count] [--dump]"""
import sys, json, time
sys.path.insert(0, "backend"); sys.path.insert(0, "tools/gameplay-eval")
from pathlib import Path
from src.auto_director import gameplay_moments as gm
from src.auto_director.beat_grid import analyse_audio_beats
from truth import score_moments
video = Path(sys.argv[1]); clip = float(sys.argv[2]) if len(sys.argv) > 2 else 30
count = int(sys.argv[3]) if len(sys.argv) > 3 else 5
t0 = time.time(); ev = gm.extract_frame_evidence(video); t1 = time.time()
grid = analyse_audio_beats(video)
if "--dump" in sys.argv:
    az = gm.audio_support_series(grid, len(ev["motion"])); ev_s, zs = gm.fuse_evidence(ev, az)
    for i in range(len(ev_s)):
        if ev_s[i] > 0 or az[i] > 3:
            print(i, f"ev={ev_s[i]:.1f}", " ".join(f"{f[:3]}={zs[f][i]:.1f}" for f in gm.FEATURES), f"aud={az[i]:.1f}")
ms = gm.select_gameplay_moments(video, float(ev["duration"][0]), target_count=count, clip_seconds=clip, beat_grid=grid, evidence=ev)
print(f"extract {t1-t0:.1f}s")
for m in ms: print(m["start_time"], m["end_time"], m["virality_score"], m["hook_type"], m["reasoning"][:150])
print(json.dumps(score_moments(ms)))
