import json
from pathlib import Path
import shutil
import subprocess
from src.media.hook_finish import finish_hook

out = Path("/proof/effects")
out.mkdir(exist_ok=True)
raw = out / "source.mp4"
subprocess.run(["ffmpeg", "-v", "error", "-y", "-ss", "12", "-i", "/proof/window_008_cold.mp4", "-t", "8",
                "-vf", "scale=640:-2", "-c:v", "h264_nvenc", "-c:a", "aac", str(raw)], check=True)
for name, signals in [("laugh", ["laugh"]), ("shout", ["scream"]), ("neutral", ["vocal"])]:
    target = out / (name + ".mp4")
    shutil.copyfile(raw, target)
    assert finish_hook(target, {"peak_energy_timestamp": 3, "reaction_signals": signals,
                                "quote_title": {"text": "Kai calls on Arnold", "creator": "Kai"}}, seed="effect-check")
    result = subprocess.check_output(["ffprobe", "-v", "error", "-show_entries", "stream=codec_type,duration",
                                      "-of", "json", str(target)])
    streams = json.loads(result)["streams"]
    assert {s["codec_type"] for s in streams} == {"video", "audio"}
    assert all(abs(float(s["duration"]) - 8) < 0.25 for s in streams)
    print(name, [(s["codec_type"], s["duration"]) for s in streams], flush=True)
