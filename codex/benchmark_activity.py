"""Repeat the four-recording replay comparison, withholding heat from all finders."""
import json
import os
import sys
from pathlib import Path
from statistics import mean

root = Path(__file__).resolve().parent
sys.path.insert(0, str(root / "JarvisClipStudio/tools/live"))
import train_selection as train
from src.live.activity_picker import extract
from src.live.vod import find_candidates
from src.media.yt_captions import fetch_youtube_captions

os.environ["LIVE_CONTEXT_PICKER"] = "off"
folder = root / "matched-sources"
videos = {v["id"]: v for v in train.load(root / "selection-run")}
results = []
for ref in json.loads((folder / "all-three-finders.json").read_text()):
    key = ref["id"]
    v = videos[key]
    path = folder / (key + ".mkv")
    transcript = fetch_youtube_captions("https://www.youtube.com/watch?v=" + key)
    activity = extract(path, path, transcript)
    row = {"id": key, "lexicon_input": bool(transcript), "count": v["k"]}
    for name, features in [("default", None), ("hybrid", activity)]:
        picks = find_candidates(v["series"], [], v["k"], activity=features)
        starts = [max(0., p["onset"] - 6) for p in picks]
        row[name] = sum(mean(train._rank_at(v["buckets"], s + d) for d in (0,10,20)) for s in starts) / v["k"]
        row[name + "_starts"] = starts
    row["neon"] = ref["neon"]
    results.append(row)
    (folder / "activity-comparison.json").write_text(json.dumps(results, indent=2))
    print(key, row["default"], row["hybrid"], row["neon"], row["lexicon_input"], flush=True)
