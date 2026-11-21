"""DEMONSTRATION render of a reaction pick using its edit_recommendations
(stacked facecam-over-game, 540x960). NOT the product renderer (that is Lane B);
it exists so a human can look at what the recommendations describe.
Usage: render_demo.py <video> <picks.json key> <pick index> <out.mp4>"""
import json, subprocess, sys
video, key, idx, out = sys.argv[1], sys.argv[2], int(sys.argv[3]), sys.argv[4]
pk = json.load(open("test-footage/real_picks_all.json"))[key][idx]
sec = lambda s: int(s.split(":")[0]) * 60 + int(s.split(":")[1])
a, b = sec(pk["start_time"]), sec(pk["end_time"])
roi = pk["edit_recommendations"]["framing"]["facecam_roi"]
x, y, w, h = roi
zooms = pk["edit_recommendations"]["zooms"]
# facecam panel: crop ROI, scale to 540x400; game panel: centre crop 4:3-ish to 540x560
fc = f"[0:v]crop=iw*{w}:ih*{h}:iw*{x}:ih*{y},scale=540:400:force_original_aspect_ratio=increase,crop=540:400[face]"
game = "[0:v]crop=ih*0.9:ih:(iw-ih*0.9)/2:0,scale=540:560[game]"
graph = f"{fc};{game};[face][game]vstack[v]"
cmd = ["ffmpeg", "-v", "error", "-y", "-ss", str(a), "-t", str(b - a), "-i", video,
       "-filter_complex", graph, "-map", "[v]", "-map", "0:a", "-c:v", "libx264", "-preset", "veryfast",
       "-crf", "21", "-c:a", "aac", out]
subprocess.run(cmd, check=True)
print("window", pk["start_time"], pk["end_time"], "title:", pk.get("hook_title"), "zooms recommended:", len(zooms), "(not applied by this demo)")
