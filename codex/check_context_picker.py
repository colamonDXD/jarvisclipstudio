import json
import os
import sys
from pathlib import Path
from statistics import mean

root = Path(__file__).resolve().parent
sys.path.insert(0, str(root / "JarvisClipStudio/tools/live"))
import train_selection as t
from src.live.vod import find_candidates

seen = {p.parent.name for p in (root / "train-18").glob("*/features.json")}
initial = set(json.loads((root / "context-picker-results.json").read_text())["groups"]["unseen"]["installed"])
rows = []
for v in t.load(root / "selection-run"):
    if v["id"] in seen:
        continue
    row = {"id": v["id"], "group": "initial" if v["id"] in initial else "confirmation"}
    for mode in ("off", "on"):
        os.environ["LIVE_CONTEXT_PICKER"] = mode
        for heat in (False, True):
            picks = find_candidates(v["series"], v["heatmap"] if heat else [], v["k"])
            row[mode + ("_heat" if heat else "")] = sum(
                mean(t._rank_at(v["buckets"], p["onset"] + dt) for dt in (0, 10, 20)) for p in picks) / v["k"]
    rows.append(row)
out = {"videos": rows}
for group in ("initial", "confirmation"):
    subset = [r for r in rows if r["group"] == group]
    out[group] = {k: mean(r[k] for r in subset) for k in ("off", "on", "off_heat", "on_heat")}
    print(group, len(subset), out[group], flush=True)
(root / "context-picker-confirmation.json").write_text(json.dumps(out, indent=2))
