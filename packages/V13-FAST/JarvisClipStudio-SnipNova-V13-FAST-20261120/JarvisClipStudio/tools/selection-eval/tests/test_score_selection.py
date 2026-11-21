"""Scoring-math tests for tools/selection-eval/score_selection.py.

Run serially from the worktree root:
    TMPDIR=<workspace tmp> OMP_NUM_THREADS=2 \
      ~/workspace/jcs-modes/backend/.venv/bin/python -m pytest \
      tools/selection-eval/tests/test_score_selection.py --no-cov
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
MODULE_PATH = HERE.parent / "score_selection.py"

spec = importlib.util.spec_from_file_location("score_selection", MODULE_PATH)
ss = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ss)


def W(start, end, wid="w", score=None):
    return {"id": wid, "start": float(start), "end": float(end), "score": score}


# ---------------------------------------------------------------- parse_ts

def test_parse_ts_numbers_and_strings():
    assert ss.parse_ts(90) == 90.0
    assert ss.parse_ts(90.5) == 90.5
    assert ss.parse_ts("14:50") == 890.0
    assert ss.parse_ts("1:02:03") == 3723.0
    assert ss.parse_ts("0:00") == 0.0


# ---------------------------------------------------------------- overlap

def test_overlap_disjoint_and_touching():
    assert ss.overlap(W(0, 10), W(20, 30)) == 0.0
    assert ss.overlap(W(0, 10), W(10, 20)) == 0.0  # touching is not overlap


def test_overlap_partial_and_containment():
    assert ss.overlap(W(0, 10), W(5, 15)) == 5.0
    assert ss.overlap(W(0, 30), W(10, 20)) == 10.0   # reference contains other
    assert ss.overlap(W(10, 20), W(0, 30)) == 10.0   # symmetric
    assert ss.overlap(W(5, 15), W(5, 15)) == 10.0    # identical


# ---------------------------------------------------------------- hit rule

def test_hit_boundary_exactly_half_of_gold_counts():
    gold = W(100, 130)  # 30 s
    assert ss.hits(W(115, 145), gold) is True   # 15 s overlap = exactly 50%
    assert ss.hits(W(116, 146), gold) is False  # 14 s < 50%


def test_hit_candidate_fully_containing_gold():
    gold = W(100, 130)
    assert ss.hits(W(90, 140), gold) is True
    assert ss.overlap_fraction_of(W(90, 140), gold) == 1.0


def test_hit_fraction_is_of_gold_not_candidate():
    gold = W(100, 200)  # 100 s
    cand = W(100, 140)  # fully inside gold, but only 40% of it
    assert ss.hits(cand, gold) is False


# ---------------------------------------------------------------- clean hit

def test_clean_hit_tolerances():
    gold = W(100, 130)
    assert ss.is_clean_hit(W(100, 130), gold) is True
    assert ss.is_clean_hit(W(103, 130), gold) is True    # start offset at tolerance
    assert ss.is_clean_hit(W(103.1, 130), gold) is False
    assert ss.is_clean_hit(W(100, 127), gold) is True    # end shortfall at tolerance
    assert ss.is_clean_hit(W(100, 126.9), gold) is False
    assert ss.is_clean_hit(W(100, 135), gold) is True    # ending late is not a shortfall
    assert ss.is_clean_hit(W(120, 150), gold) is False   # shifted window that only just hits


# ---------------------------------------------------------------- fixtures

def golds_traps():
    gold_a = W(100, 130, "gA")
    gold_b = W(200, 220, "gB")
    frag = W(118, 130, "tFrag")
    frag.update(parent_gold="gA", trap_type="fragment")
    payoff = W(100, 118, "tPay")
    payoff.update(parent_gold="gA", trap_type="payoff_cut")
    return [gold_a, gold_b], [frag, payoff]


# ---------------------------------------------------------------- scoring

def test_score_empty_candidate_list():
    golds, traps = golds_traps()
    r = ss.score_candidates(golds, traps, [])
    assert r["recall"] == 0.0
    assert r["precision_at_5"] == 0.0
    assert r["mean_overlap"] == 0.0
    assert r["duplicate_picks"] == 0
    assert r["traps_taken"] == []


def test_score_genuine_pick_takes_no_nested_trap():
    golds, traps = golds_traps()
    r = ss.score_candidates(golds, traps, [W(100, 130, "pick")])
    assert r["recall"] == 0.5           # gA hit, gB missed
    assert r["mean_overlap"] == 0.5     # (1.0 + 0.0) / 2
    assert r["traps_taken"] == []      # nested traps exempt for a clean hit


def test_score_fragment_candidate_misses_and_takes_trap():
    golds, traps = golds_traps()
    r = ss.score_candidates(golds, traps, [W(118, 130, "frag-pick")])
    assert r["recall"] == 0.0                       # 12 s is only 40% of gA
    assert [t["trap_id"] for t in r["traps_taken"]] == ["tFrag"]


def test_score_payoff_cut_candidate_hits_but_takes_trap():
    golds, traps = golds_traps()
    r = ss.score_candidates(golds, traps, [W(100, 118, "cut-pick")])
    assert r["recall"] == 0.5                       # 18 s = 60% of gA: a hit
    assert {t["trap_id"] for t in r["traps_taken"]} == {"tPay"}


def test_score_duplicate_picks_counted():
    golds, traps = golds_traps()
    cands = [W(100, 130, "first"), W(104, 134, "second"), W(200, 220, "other")]
    r = ss.score_candidates(golds, traps, cands)
    assert r["recall"] == 1.0
    assert r["duplicate_picks"] == 1
    per = {g["gold_id"]: g for g in r["per_gold"]}
    assert per["gA"]["hit_by"] == ["first", "second"]


def test_score_mean_overlap_uses_best_per_gold():
    golds, traps = golds_traps()
    cands = [W(100, 115, "half"), W(200, 210, "b-half")]
    r = ss.score_candidates(golds, traps, cands)
    # gA best = 15/30 = 0.5, gB best = 10/20 = 0.5; both are exactly-half hits
    assert r["mean_overlap"] == 0.5
    assert r["recall"] == 1.0


def test_precision_at_5_respects_scores_and_top5_cut():
    golds, traps = golds_traps()
    cands = [
        W(300, 320, "junk1", score=99),
        W(100, 130, "hitA", score=90),
        W(400, 420, "junk2", score=80),
        W(200, 220, "hitB", score=70),
        W(500, 520, "junk3", score=60),
        W(100, 130, "hitA2-lowest-score", score=10),  # hit, but ranked 6th
    ]
    r = ss.score_candidates(golds, traps, cands)
    assert r["precision_at_5"] == 0.4  # 2 of the top 5 hit


def test_precision_at_5_unscored_uses_file_order():
    golds, traps = golds_traps()
    cands = [W(300, 320, "junk")] + [W(100, 130, f"h{i}") for i in range(5)]
    r = ss.score_candidates(golds, traps, cands)
    # file order: junk + 4 hits in the top 5
    assert r["precision_at_5"] == 0.8


def test_rank_candidates_mixed_scores_fall_back_to_file_order():
    cands = [W(0, 1, "a", score=5), W(0, 1, "b"), W(0, 1, "c", score=99)]
    assert [c["id"] for c in ss.rank_candidates(cands)] == ["a", "b", "c"]


# ---------------------------------------------------------------- validation

def test_as_window_rejects_bad_windows():
    with pytest.raises(ValueError):
        ss.as_window({"start": 10, "end": 10}, "x")
    with pytest.raises(ValueError):
        ss.as_window({"start": 10, "end": 9}, "x")
    with pytest.raises(ValueError):
        ss.as_window({"end": 9}, "x")
    w = ss.as_window({"start": "14:50", "end": "15:10", "score": 92}, "x")
    assert (w["start"], w["end"], w["score"]) == (890.0, 910.0, 92.0)


# ---------------------------------------------------------------- real data

def test_real_gold_set_shape():
    doc = ss.load_gold(HERE.parent / "gold_set.json")
    golds, traps = ss.windows_for_source(doc, "japan-restaurants-2026-11-09")
    assert len(golds) == 5
    assert len(traps) == 15
    assert {t["trap_type"] for t in traps} == {"fragment", "payoff_cut", "duplicate"}
    with pytest.raises(ValueError):
        ss.grade(doc, "tjr-week-in-the-life", [])  # listed but unlabelled


def test_real_samples_discriminate():
    doc = ss.load_gold(HERE.parent / "gold_set.json")
    src, asrun = ss.load_candidates(HERE.parent / "samples" / "japan_asrun_candidates.json")
    r = ss.grade(doc, src, asrun)
    assert (r["recall"], r["precision_at_5"], r["mean_overlap"]) == (1.0, 1.0, 1.0)
    assert r["traps_taken"] == [] and r["duplicate_picks"] == 0

    src, degraded = ss.load_candidates(HERE.parent / "samples" / "japan_degraded_candidates.json")
    r2 = ss.grade(doc, src, degraded)
    assert r2["recall"] == 0.4
    assert r2["precision_at_5"] == 0.4
    assert r2["mean_overlap"] == 0.28
    assert len({t["trap_id"] for t in r2["traps_taken"]}) >= 3
