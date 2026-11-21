"""The trainable ranker matches the shipped voice rules under default params."""
from __future__ import annotations

import json
import random

from src.live.selection import DEFAULT_PARAMS, load_params, rank_moments, spaced
from src.live.voice_scan import VoiceTrack


def _series(n, spikes, seed=3):
    rng = random.Random(seed)
    s = {"vocal": [], "f0": [], "laugh": [], "voiced_frac": []}
    for i in range(n):
        s["vocal"].append(-40 + rng.random() * 4)
        s["f0"].append(150 + rng.random() * 30)
        s["laugh"].append(rng.random() * 0.05)
        s["voiced_frac"].append(0.6)
    for t in spikes:
        for d in range(3):
            s["vocal"][t + d], s["f0"][t + d], s["laugh"][t + d] = -8.0, 430.0, 0.9
    return s


def test_defaults_reproduce_the_live_voice_rules():
    series = _series(3000, [500, 900, 1700, 1704, 2600])
    track = VoiceTrack(baseline_sec=240.0)
    for sec in range(3000):
        track.times.append(float(sec))
        for k in ("vocal", "f0", "laugh"):
            track.feats[k].append(series[k][sec])
    old = sorted((f["onset"], round(f["peak"], 3)) for f in track.onsets())
    new = sorted((m["onset"], round(m["peak"], 3)) for m in rank_moments(series, DEFAULT_PARAMS))
    assert old == new and len(new) == 4  # 1700 and 1704 merge (gap <= 3 s)


def test_spacing_and_trained_params_file(tmp_path, monkeypatch):
    series = _series(3000, [500, 560, 1700])
    moments = rank_moments(series)
    assert [round(m["onset"]) for m in spaced(moments, 5, 90.0)] in ([500, 1700], [1700, 500], [560, 1700], [1700, 560])
    f = tmp_path / "p.json"
    f.write_text(json.dumps({"params": {"active_z": 2.2, "bogus": 1}}), encoding="utf-8")
    monkeypatch.setenv("LIVE_SELECTION_PARAMS", str(f))
    p = load_params()
    assert p["active_z"] == 2.2 and "bogus" not in p and p["min_gap"] == DEFAULT_PARAMS["min_gap"]
