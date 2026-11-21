"""Live MVP: the fast voice pass flags reactions with the picker's rules, causally."""
from __future__ import annotations

import random

from src.live.voice_scan import VoiceTrack


def _track(spikes):
    rng = random.Random(0)
    tr = VoiceTrack(baseline_sec=120.0, exclude_recent_sec=5.0)
    for sec in range(200):
        tr.times.append(1000.0 + sec)
        vocal, f0, laugh = -30 + rng.uniform(-1, 1), 180 + rng.uniform(-10, 10), 0.05 + rng.uniform(-0.02, 0.02)
        for at, kind in spikes:
            if at <= sec < at + 2:
                if kind == "scream":
                    vocal, f0 = -24.0, 420.0
                elif kind == "shout_laugh":
                    vocal, laugh = -18.0, 0.6
                elif kind == "loud_only":
                    vocal = -10.0
        tr.feats["vocal"].append(vocal)
        tr.feats["f0"].append(f0)
        tr.feats["laugh"].append(laugh)
    return tr


def test_scream_and_laugh_are_flagged_with_their_signals():
    flags = _track([(80, "scream"), (150, "shout_laugh")]).onsets()
    assert [round(f["onset"]) for f in flags] == [1080, 1150]
    assert "scream" in flags[0]["signals"]
    assert {"vocal", "laugh"} <= set(flags[1]["signals"])


def test_one_loud_signal_alone_is_not_a_reaction():
    assert _track([(90, "loud_only")]).onsets() == []


def test_nothing_is_flagged_before_a_baseline_exists():
    assert _track([(10, "scream")]).onsets() == []
