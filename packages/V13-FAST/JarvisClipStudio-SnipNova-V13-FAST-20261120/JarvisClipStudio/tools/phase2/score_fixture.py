"""Score the streamer-reaction picker against Sanji's streamer fixture v2.

  python tools/phase2/score_fixture.py [--video test-footage/fixtures/streamer_fixture_v2.mp4]

Ground truth (Sanji, 2026-11-17): 150 s, 1280x720, stereo L=gameplay R=mic,
facecam top-right. Setup 16-20 s; R1 20-28 (kill + animated face + loud
speech); R2 50-58 (funny event, most animated face); DECOY 80-86 (loudest
gameplay explosion, still face, silent mic: must NOT rank top); R3 105-115
(small event, mic scream 107.5-110.2 = loudest moment: must rank #1).
Contract: R3 > R1, R2 > DECOY; clips include their context and never cut a
burst. Prints the slate literally, then each check as PASS / FAIL.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TRUTH = {"R1": (20.0, 28.0), "R2": (50.0, 58.0), "DECOY": (80.0, 86.0), "R3": (105.0, 115.0)}


def _sec(label: str) -> float:
    parts = [float(p) for p in str(label).split(":")]
    total = 0.0
    for p in parts:
        total = total * 60 + p
    return total


def _overlap(a: tuple[float, float], b: tuple[float, float]) -> float:
    return max(0.0, min(a[1], b[1]) - max(a[0], b[0]))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--video", default=str(ROOT / "test-footage" / "fixtures" / "streamer_fixture_v2.mp4"))
    ap.add_argument("--clips", type=int, default=5)
    ap.add_argument("--clip-seconds", type=float, default=30.0)
    a = ap.parse_args()
    sys.path.insert(0, str(ROOT / "backend"))
    from src.auto_director.reaction_moments import select_streamer_moments

    moments = select_streamer_moments(a.video, 150.0, target_count=a.clips, clip_seconds=a.clip_seconds)
    ranked = sorted(moments, key=lambda m: -float(m.get("fused_score", m.get("virality_score", 0))))
    print("rank | window | label | fused | virality | type | signals")
    labelled = []
    for rank, m in enumerate(ranked, 1):
        win = (_sec(m["start_time"]), _sec(m["end_time"]))
        label = max(TRUTH, key=lambda k: _overlap(win, TRUTH[k]))
        label = label if _overlap(win, TRUTH[label]) > 0 else "-"
        labelled.append((rank, win, label, m))
        print(f"{rank} | {m['start_time']}-{m['end_time']} | {label} | {m.get('fused_score')} | "
              f"{m.get('virality_score')} | {m.get('hook_type')} | {json.dumps(m.get('signals'))}")

    def rank_of(name: str):
        hits = [r for r, _w, lab, _m in labelled if lab == name]
        return min(hits) if hits else None

    r1, r2, r3, decoy = rank_of("R1"), rank_of("R2"), rank_of("R3"), rank_of("DECOY")
    checks = []
    checks.append(("R3 ranks #1", r3 == 1))
    checks.append(("R1 found and below R3", r1 is not None and r3 is not None and r1 > r3))
    checks.append(("R2 found and below R3", r2 is not None and r3 is not None and r2 > r3))
    checks.append(("DECOY not top", decoy != 1))
    checks.append(("R1 and R2 above DECOY (or DECOY absent)",
                   decoy is None or (r1 is not None and r2 is not None and r1 < decoy and r2 < decoy)))
    for name, need in (("R1", (16.0, 28.0)), ("R3", (107.5, 110.2))):
        wins = [w for _r, w, lab, _m in labelled if lab == name]
        checks.append((f"{name} clip contains {need[0]}-{need[1]} s",
                       bool(wins) and any(w[0] <= need[0] and w[1] >= need[1] for w in wins)))
    print()
    for name, ok in checks:
        print(f"{'PASS' if ok else 'FAIL'}  {name}")
    return 0 if all(ok for _n, ok in checks) else 1


if __name__ == "__main__":
    sys.exit(main())
