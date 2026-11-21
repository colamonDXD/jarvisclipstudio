import json
import sys
from pathlib import Path
from statistics import mean

root = Path(__file__).resolve().parent
sys.path.insert(0, str(root / "JarvisClipStudio/tools/live"))
import train_selection as trainer
from src.live.selection import DEFAULT_PARAMS, load_params, zscores

seen = {f.parent.name for f in (root / "train-18").glob("*/features.json")}
report = json.loads((root / "train-18/training_report.json").read_text())
configs = {"default": DEFAULT_PARAMS, "installed": load_params(), "candidate": report["params"]}
scores = []
for video in trainer.load(root / "selection-run"):
    if video["id"] in seen:
        continue
    cache = {(video["id"], b): zscores(video["series"], dict(DEFAULT_PARAMS, baseline_sec=b))
             for b in {int(p["baseline_sec"]) for p in configs.values()}}
    scores.append({"id": video["id"], **{k: trainer.score_video(video, p, cache) for k, p in configs.items()}})
result = {"scores": scores, "means": {k: mean(row[k] for row in scores) for k in configs}}
(root / "independent-replay-scores.json").write_text(json.dumps(result, indent=2))
print(len(scores), result["means"])
