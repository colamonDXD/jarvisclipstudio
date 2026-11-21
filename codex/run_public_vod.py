"""Render three fresh picks through the VOD session with public input and offline ASR."""
import json
from pathlib import Path
from src.live.session import LiveSettings
from src.live.vod import VodSession

out = Path("/proof")
def event(data):
    with (out / "events.jsonl").open("a") as log:
        log.write(json.dumps(data) + "\n")
    print(data.get("progress") or data.get("status") or data.get("type"), flush=True)

settings = LiveSettings(url="https://www.youtube.com/watch?v=VZqAPdUvqnQ", credit="@KaiCenat",
                        max_clips=3, niche="gaming", layout_template="facecam_red")
session = VodSession(settings, out, event)
session._run()
