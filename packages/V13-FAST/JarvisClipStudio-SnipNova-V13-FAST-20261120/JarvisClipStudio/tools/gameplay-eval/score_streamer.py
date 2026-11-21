"""Score selectors on the SYNTHETIC streamer-VOD matrix.
Usage: score_streamer.py <vod_dir> [count] [--dump]
Reports, per selector, which scenario each pick landed in and the verdicts."""
import sys, json, time
sys.path.insert(0, "backend"); sys.path.insert(0, "tools/gameplay-eval")
from pathlib import Path
from src.auto_director import reaction_moments as rm, gameplay_moments as gm, visual_moments as vm
from src.auto_director.beat_grid import analyse_audio_beats

d = Path(sys.argv[1]); count = int(sys.argv[2]) if len(sys.argv) > 2 and sys.argv[2].isdigit() else 6
truth = json.loads((d / "truth.json").read_text()); plan = truth["plan"]; dur = float(truth["total"]); vod = d / "vod.mp4"
def sec(s): m, x = s.split(":"); return int(m) * 60 + int(x)

def where(m):
    s, e = sec(m["start_time"]), sec(m["end_time"])
    best = max(plan, key=lambda p: max(0, min(e, p["end"]) - max(s, p["start"])))
    ov = max(0, min(e, best["end"]) - max(s, best["start"]))
    return best, ov

def report(label, ms, t):
    print(f"\n== {label}  ({t:.1f}s)")
    pos_hit, fp = set(), []
    for m in sorted(ms, key=lambda m: -m["virality_score"]):
        p, ov = where(m); good = p["role"].startswith("pos") and ov >= 0.5 * (p["end"] - p["start"])
        if good: pos_hit.add(p["name"])
        elif p["role"] in ("neg", "weak") or not good: fp.append(p["name"])
        print(f"  {m['start_time']}-{m['end_time']} score={m['virality_score']:>3} -> {p['name']:<20} role={p['role']:<10} {'HIT' if good else 'MISS/FP'}  [{m.get('hook_type','')}]")
    pos = [p["name"] for p in plan if p["role"].startswith("pos")]
    print(f"  positives found {len(pos_hit)}/{len(pos)}: {sorted(pos_hit)}; missed {sorted(set(pos)-pos_hit)}; false-positive picks: {fp}")
    return {"found": len(pos_hit), "of": len(pos), "fp": len(fp), "n": len(ms)}

res = {}
t = time.time(); grid = analyse_audio_beats(vod)
ms = vm.select_visual_moments(vod, dur, target_count=count, clip_seconds=30, beat_grid=grid)
res["baseline(audio peaks)"] = report("BASELINE: audio peaks + cuts (V9)", ms, time.time() - t)
t = time.time(); ms = gm.select_gameplay_moments(vod, dur, target_count=count, clip_seconds=30, beat_grid=grid, evidence=gm.extract_frame_evidence(vod))
res["GA1 gameplay-only"] = report("GA1: gameplay frame-evidence only (no streamer model)", ms, time.time() - t)
t = time.time(); voice = rm.extract_voice_evidence(vod); tv = time.time() - t
t = time.time(); roi = (470/640, 226/360, 160/640, 120/360) if '--known-roi' in sys.argv else rm.find_facecam(vod, dur); face = rm.extract_facecam_activity(vod, roi) if roi else None; tf = time.time() - t
print(f"\nvoice extract {tv:.1f}s, facecam {tf:.1f}s, roi={None if roi is None else [round(x,2) for x in roi]}")
if "--dump" in sys.argv:
    st, zs, vp = rm.reaction_series(voice, face)
    for i in range(len(st)):
        if st[i] > 0: print(i, f"str={st[i]:.1f}", " ".join(f"{k[:3]}={zs[k][i]:.1f}" for k in zs))
t = time.time(); ms = rm.select_streamer_moments(vod, dur, target_count=count, clip_seconds=30, voice=voice, face=face, facecam_roi=roi)
res["streamer reaction"] = report("NEW: streamer-reaction + event correlation", ms, time.time() - t)
print(json.dumps(res))
