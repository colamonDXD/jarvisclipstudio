"""Finish the independently selected public VOD moments with the final caption fix."""
import json
import os
from pathlib import Path
import subprocess
import time
from src.live.finisher import render_live_clip

base = Path("/proof")
folder = base / os.getenv("REVIEW_FOLDER", "review")
folder.mkdir(exist_ok=True)
done = set()
deadline = time.monotonic() + 1200
while len(done) < 3 and time.monotonic() < deadline:
    events = [json.loads(s) for s in (base / "events.jsonl").read_text().splitlines() if s.endswith("}")]
    ready = [e["n"] for e in events if e.get("type") == "clip" and e.get("status") == "ready"]
    for n in ready:
        if n in done:
            continue
        moment = next(e for e in events if e.get("type") == "moment" and e["n"] == n and e.get("preview_path"))
        source = base / Path(moment["preview_path"]).name
        output = folder / f"clip_{n:03}.mp4"
        result = render_live_clip(source, output, minimum_duration=30, auto_headline=True,
                                  reaction_signals=moment.get("signals"), credit="@KaiCenat")
        assert result["ok"], result
        output.with_suffix(".json").write_text(json.dumps(result, indent=2))
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-ss", "0.4", "-i", str(output),
                        "-vf", "scale=360:640", "-frames:v", "1", str(output.with_suffix(".jpg"))], check=True)
        print(n, result["duration"], result["words"], flush=True)
        done.add(n)
    if len(done) < 3:
        time.sleep(4)
assert len(done) == 3
