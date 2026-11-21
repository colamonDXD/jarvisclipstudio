import glob, json, re, subprocess, sys, statistics
sys.path.insert(0, "backend")
from pathlib import Path
from src.auto_director.visual_moments import detect_scene_cut_times
from src.auto_director.beat_grid import analyse_audio_beats
out = {}
for v in sorted(glob.glob("test-footage/references/*.mp4")):
    p = subprocess.run(["ffprobe","-v","error","-show_entries","stream=codec_type,width,height,r_frame_rate:format=duration","-of","json",v],capture_output=True,text=True)
    j = json.loads(p.stdout); d = float(j["format"]["duration"])
    vs = [s for s in j["streams"] if s["codec_type"]=="video"][0]
    cuts = detect_scene_cut_times(v, threshold=0.3)
    shots = [b-a for a,b in zip([0.0]+cuts, cuts+[d])]
    grid = analyse_audio_beats(Path(v)) or {}
    lufs = subprocess.run(["ffmpeg","-v","info","-i",v,"-af","ebur128=framelog=quiet","-f","null","-"],capture_output=True,text=True).stderr
    m = re.findall(r"I:\s+(-?[\d.]+) LUFS", lufs)
    first3 = [c for c in cuts if c <= 3.0]
    out[Path(v).stem] = dict(duration=round(d,1), res=f"{vs['width']}x{vs['height']}", fps=vs["r_frame_rate"],
        cuts=len(cuts), cuts_per_min=round(len(cuts)/d*60,1), median_shot=round(statistics.median(shots),2) if shots else None,
        cuts_in_first_3s=len(first3), tempo=grid.get("tempo_bpm") or grid.get("tempo"), beats=len(grid.get("beat_times") or []),
        loudness_lufs=float(m[-1]) if m else None)
    print(Path(v).stem, out[Path(v).stem], flush=True)
json.dump(out, open("test-footage/references/metrics.json","w"), indent=1)
