import asyncio
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile

root = Path("/proof")
out = root / "editorial"
out.mkdir(exist_ok=True)
from src.live.finisher import render_live_clip
from src.services.video_service import VideoService

if sys.argv[-1] == "boxes":
    boxes = json.loads((root / "title-off/drawn-boxes.json").read_text())
    with tempfile.TemporaryDirectory() as folder:
        r = asyncio.run(VideoService.create_single_clip(
            root / "window_002.mp4", {"start_time":"00:00:10","end_time":"00:00:40","text":"gaming"},
            0, Path(folder), add_subtitles=False, layout_template="facecam_red", boxes=boxes, ai_editing=False))
        assert r, "normal clip render failed"
        import shutil
        shutil.copyfile(r["path"],root / "title-off/uploaded-gameplay.mp4")
    sys.exit()

i = int(sys.argv[-1])
source = root / ("window_002.mp4" if i == 2 else "window_003_cold.mp4")
target = out / f"clip_{i:03d}.mp4"
commands = []
connections = []
def audit(event, args):
    if event == "socket.connect":
        connections.append(str(args[1]))
    if event == "subprocess.Popen":
        cmd = args[1]
        if isinstance(cmd,(list,tuple)) and Path(str(cmd[0])).name == "ffmpeg":
            for opt in ("-filter_complex","-vf","-af"):
                if opt in cmd: commands.append(str(cmd[cmd.index(opt)+1]))
sys.addaudithook(audit)
r = render_live_clip(source,target,minimum_duration=30,credit="@KaiCenat")
assert r["ok"], r.get("error")
flags = "EDL_CONTENT_CUTS SCREEN_SUBJECT SELECTION_EVENTS FIT_PAD REFRAME_HIRES_PROBE CAPTION_LOOP_FILTER CAPTION_PHRASE_SPLIT ASSET_VARIANTS BROLL_OPENVERSE TRANSITION_LIBRARY_API".split()
r.update(runtime_flag_overrides={k:os.getenv(k) for k in flags},network_connections=connections,
         ffmpeg_filters=commands, ffprobe=json.loads(subprocess.check_output(["ffprobe","-v","error","-show_streams","-show_format","-of","json",str(target)])))
target.with_suffix(".json").write_text(json.dumps(r,indent=2,default=str))
print("Shots:",r["applied_game_shots"],"Encoder used hero:",any("gamehero" in c for c in commands),flush=True)
