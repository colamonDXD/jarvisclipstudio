"""Fit a supervised acoustic reranker using disjoint source-video groups."""
import json
import sys
from pathlib import Path

import numpy as np

root = Path(__file__).resolve().parent
sys.path.insert(0, str(root / "JarvisClipStudio/tools/live"))
import train_selection as t
from src.live.selection import load_params, rank_moments, spaced


from src.live.context_picker import context_features


def prepare(v, params):
    moments = rank_moments(v["series"], params)
    y = np.array([np.mean([t._rank_at(v["buckets"], m["onset"] + d) for d in (0, 10, 20)]) for m in moments])
    x = context_features(v["series"], moments)
    return dict(video=v, moments=moments, y=y, x=x)


def metric(group, weights, mix, gap):
    result = {}
    for item in group:
        x, y = item["x"], item["y"]
        def scale(a):
            return (a - a.mean()) / max(0.001, a.std())
        predicted = (1 - mix) * scale(x[:, 0]) + mix * scale(x @ weights)
        ranked = [dict(item["moments"][i], label=y[i]) for i in np.argsort(-predicted, kind="stable")]
        v = item["video"]
        picks = spaced(ranked, v["k"], gap)
        result[v["id"]] = sum(p["label"] for p in picks) / v["k"]
    return result


params = load_params()
split = json.loads((root / "train-18/training_report.json").read_text())
items = {v["id"]: prepare(v, params) for v in t.load(root / "selection-run")}
train = [items[k] for k in split["train_videos"]]
valid = [items[k] for k in split["heldout_videos"]]
test = [v for k, v in items.items() if k not in split["train_videos"] + split["heldout_videos"]]
# Center each stream's targets and give each stream equal training weight.
x = np.concatenate([(i["x"] - i["x"].mean(axis=0)) / np.sqrt(len(i["x"])) for i in train])
y = np.concatenate([(i["y"] - i["y"].mean()) / np.sqrt(len(i["y"])) for i in train])
gap = params["min_gap"]
options = []
for ridge in (0.03, 0.3, 3, 30):
    w = np.linalg.solve(x.T @ x + ridge * np.eye(x.shape[1]), x.T @ y)
    for mix in (0.25, 0.5, 0.75, 1):
        values = metric(valid, w, mix, gap)
        options.append((np.mean(list(values.values())), ridge, mix, w))
_, ridge, mix, w = max(options, key=lambda row: row[0])
out = {"ridge": ridge, "mix": mix, "weights": w.tolist(), "groups": {}}
for name, group in (("train", train), ("validation", valid), ("unseen", test)):
    old, new = metric(group, w, 0, gap), metric(group, w, mix, gap)
    out["groups"][name] = {"installed": old, "candidate": new}
    print(name, len(old), round(np.mean(list(old.values())), 4), round(np.mean(list(new.values())), 4))
(root / "context-picker-results.json").write_text(json.dumps(out, indent=2))
