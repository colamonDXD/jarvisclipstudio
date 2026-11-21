import json
import shutil
import subprocess
from pathlib import Path
from src.live.finisher import render_live_clip
from src.media.transcription import cache_transcript_data

root = Path("/proof")
out = root / "revised"
out.mkdir(exist_ok=True)
for number, title in [(8, "Kai calls on Arnold for help"), (19, "Kai calls security on himself"),
                      (7, "Kai calls out to independent women")]:
    original = next(root.glob(f"window_{number:03}*.mp4"))
    source = out / original.name
    shutil.copyfile(original, source)
    shutil.copyfile(original.with_suffix(".reframe_cache.json"), source.with_suffix(".reframe_cache.json"))
    if number == 8:
        shutil.copyfile(original.with_suffix(".transcript_cache.full.json"), source.with_suffix(".transcript_cache.full.json"))
    else:
        cache_transcript_data(source, json.loads(original.with_suffix(".local-asr.json").read_text()))
        shutil.copyfile(source.with_suffix(".transcript_cache.json"), source.with_suffix(".transcript_cache.full.json"))
    target = out / f"kai-{number:03}.mp4"
    result = render_live_clip(source, target, credit="@KaiCenat", quote_title={"text": title, "creator": "Kai Cenat"})
    if not result["ok"] or result["duration"] < 30:
        raise RuntimeError(f"Invalid export {number}")
    target.with_suffix(".json").write_text(json.dumps(result, indent=2))
    subprocess.run(["ffmpeg", "-v", "error", "-ss", "1", "-i", str(target), "-frames:v", "1",
                    "-vf", "scale=270:480", "-y", str(target.with_suffix(".jpg"))], check=True)
    print(number, result["words"], result["duration"], flush=True)
