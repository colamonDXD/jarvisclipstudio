"""Re-render the three supplied Kai moments from copies of their source windows."""
import json
import os
from pathlib import Path
import shutil
import subprocess

from src.live.finisher import render_live_clip
from src.media.hook_finish import _probe


source = Path("/original/live/2296b3dd-54ae-40a6-80b4-d088fd18beb3")
out = Path("/proof")
titles = {
    7: "Kai calls out to independent women",
    8: "Kai calls on Arnold for help",
    19: "Kai calls security on himself",
}
for number, title in titles.items():
    stem = f"window_{number:03}"
    original = next((p for p in [source / (stem + "_cold.mp4"), source / (stem + ".mp4")] if p.is_file()))
    window = out / original.name
    if not window.exists():
        shutil.copyfile(original, window)
    target = out / f"kai-{number:03}.mp4"
    if target.exists() and _probe(target)["duration"] >= 30:
        continue
    for cache in source.glob(original.stem + ".reframe_cache.json"):
        shutil.copyfile(cache, window.with_suffix(".reframe_cache.json"))
    print("Rendering", window.name, flush=True)
    result = render_live_clip(window, target, niche="gaming", layout_template="facecam_red",
                              credit="@KaiCenat", quote_title={"text": title, "creator": "Kai Cenat"})
    if not result["ok"] or result["duration"] < 30:
        raise RuntimeError(f"Clip {number} failed the render or duration check")
    print("Rendered", number, result["duration"], "seconds,", result["words"], "caption words", flush=True)
    (out / f"kai-{number:03}.json").write_text(json.dumps(result, indent=2))
    subprocess.run(["ffmpeg", "-v", "error", "-ss", "1", "-i", str(target), "-frames:v", "1",
                    "-vf", "scale=270:480", "-y", str(target.with_suffix(".jpg"))], check=True)
