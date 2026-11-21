"""Same-source comparison of both first-stage finders against public replay labels."""
import json
import os
import sys
from pathlib import Path
from statistics import mean

root = Path(__file__).resolve().parent
sys.path.insert(0, str(root / "JarvisClipStudio/tools/live"))
import train_selection as t
sys.path.insert(0, str(root.parent / "ext/neonclipping"))
import highlights
from src.live.vod import find_candidates

data = {v["id"]: v for v in t.load(root / "selection-run")}
out = root / "matched-sources"
out.mkdir(exist_ok=True)
results = []
for key in ("r39UkWMV-2k", "Wb4Z_4eMPfo", "UgYMkjW9JX8", "6YESOZ_tGWo"):
    v = data[key]
    path = out / (key + ".mkv")
    if not path.is_file():
        import subprocess
        cmd = [t._ytdlp(), "--no-playlist", "--no-warnings", "--js-runtimes", "node", "-q",
               "-f", "bv[height<=360]+ba/b[height<=480]", "--merge-output-format", "mkv", "--remux-video", "mkv",
               "-o", str(out / (key + ".%(ext)s")), "https://www.youtube.com/watch?v=" + key]
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode:
            print(key, "download failed", result.stderr[-180:], flush=True)
            if "bot" in result.stderr.lower():
                break
            continue
    print("Measuring", key, flush=True)
    count = v["k"]
    neon = highlights.rank(path, v["duration"], count=count, length=30)
    os.environ["LIVE_CONTEXT_PICKER"] = "on"
    ours = find_candidates(v["series"], [], count)
    def evaluate(starts):
        return sum(mean(t._rank_at(v["buckets"], s + d) for d in (0, 10, 20)) for s in starts) / count
    a = [max(0, p["onset"] - 6) for p in ours]
    b = [p["start"] for p in neon]
    row = {"id": key, "count": count, "ours": evaluate(a), "neon": evaluate(b), "our_starts": a, "neon_starts": b}
    results.append(row)
    print(key, row["ours"], row["neon"], flush=True)
    (out / "replay-comparison.json").write_text(json.dumps(results, indent=2))
