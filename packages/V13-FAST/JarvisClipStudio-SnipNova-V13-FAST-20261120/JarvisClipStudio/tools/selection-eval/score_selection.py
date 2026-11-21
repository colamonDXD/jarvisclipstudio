#!/usr/bin/env python3
"""Offline scorer for SnipNova moment selection, against the gold set.

Takes a candidate list (windows a selector picked for one source) and
scores it against the labelled windows in gold_set.json:

  recall          fraction of gold windows hit by at least one candidate.
                  A candidate HITS a gold when their overlap is at least
                  50% of the GOLD window's length.
  precision@5     of the top 5 candidates (by score, descending, when
                  every candidate carries a numeric score; otherwise in
                  file order), the fraction that hit at least one gold.
  mean_overlap    per gold, the best overlap fraction (overlap / gold
                  length) any candidate achieved; averaged over golds.
                  1.0 means every gold was covered exactly or better.
  traps_taken     candidates that match a trap window (overlap >= 50%
                  of the TRAP's length) without being a clean hit on the
                  trap's parent gold. A clean hit starts within 3 s of
                  the gold's start and ends within 3 s of its end, so a
                  genuine pick is never blamed for the traps nested
                  inside its own gold - but a shifted, fragment or
                  payoff-cut window is flagged even when it overlaps.
  duplicate_picks extra candidates hitting a gold that another candidate
                  already hit (two picks spent on one moment).

Everything is offline and stdlib-only: no model is called. Feed it a
recorded candidate list (e.g. the clips of a finished task, exported by
hand) and it grades selection only - not framing, captions or renders.

Usage:
    python3 score_selection.py --candidates picks.json
    python3 score_selection.py --candidates picks.json --json
    python3 score_selection.py --self-test

Candidate file: {"source": "<gold-set source id>", "candidates": [...]}
or a bare list. Each candidate: {"start": ..., "end": ..., "score": ...}
with times as seconds (number) or "MM:SS" / "HH:MM:SS" strings; score is
optional. The gold file defaults to gold_set.json next to this script;
--source picks which source inside it to grade against (defaults to the
candidate file's "source", else the first labelled source).
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

HIT_FRACTION_OF_GOLD = 0.5
TRAP_FRACTION_OF_TRAP = 0.5
CLEAN_START_TOLERANCE_SEC = 3.0
CLEAN_END_TOLERANCE_SEC = 3.0

HERE = Path(__file__).resolve().parent


def parse_ts(value) -> float:
    """Seconds from a number or an 'MM:SS' / 'HH:MM:SS' string."""
    if isinstance(value, (int, float)):
        return float(value)
    parts = [float(p) for p in str(value).split(":")]
    total = 0.0
    for p in parts:
        total = total * 60 + p
    return total


def as_window(raw: dict, label: str) -> dict:
    try:
        start = parse_ts(raw["start"])
        end = parse_ts(raw["end"])
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError(f"{label}: bad start/end in {raw!r}") from exc
    if end <= start:
        raise ValueError(f"{label}: end must be after start in {raw!r}")
    score = raw.get("score")
    return {
        "id": raw.get("id") or label,
        "start": start,
        "end": end,
        "score": float(score) if isinstance(score, (int, float)) else None,
    }


def overlap(a: dict, b: dict) -> float:
    return max(0.0, min(a["end"], b["end"]) - max(a["start"], b["start"]))


def length(w: dict) -> float:
    return w["end"] - w["start"]


def overlap_fraction_of(candidate: dict, reference: dict) -> float:
    """Overlap as a fraction of the REFERENCE window's length."""
    return overlap(candidate, reference) / length(reference)


def hits(candidate: dict, gold: dict) -> bool:
    return overlap_fraction_of(candidate, gold) >= HIT_FRACTION_OF_GOLD


def is_clean_hit(candidate: dict, gold: dict) -> bool:
    """Hit with both boundaries close to the gold's own boundaries."""
    return (
        hits(candidate, gold)
        and abs(candidate["start"] - gold["start"]) <= CLEAN_START_TOLERANCE_SEC
        and (gold["end"] - candidate["end"]) <= CLEAN_END_TOLERANCE_SEC
    )


def rank_candidates(candidates: list[dict]) -> list[dict]:
    """By score descending when every candidate is scored, else file order."""
    if candidates and all(c["score"] is not None for c in candidates):
        return sorted(candidates, key=lambda c: -c["score"])
    return list(candidates)


def score_candidates(golds: list[dict], traps: list[dict], candidates: list[dict]) -> dict:
    gold_by_id = {g["id"]: g for g in golds}

    per_gold = []
    for gold in golds:
        fracs = [overlap_fraction_of(c, gold) for c in candidates]
        best = max(fracs) if fracs else 0.0
        hitters = [c["id"] for c in candidates if hits(c, gold)]
        per_gold.append({
            "gold_id": gold["id"],
            "best_overlap_fraction": round(best, 4),
            "hit": bool(hitters),
            "hit_by": hitters,
            "duplicate_picks": max(0, len(hitters) - 1),
        })

    n_gold = len(golds)
    n_hit = sum(1 for g in per_gold if g["hit"])
    recall = n_hit / n_gold if n_gold else 0.0
    mean_overlap = (
        sum(g["best_overlap_fraction"] for g in per_gold) / n_gold if n_gold else 0.0
    )

    top5 = rank_candidates(candidates)[:5]
    top5_hits = sum(1 for c in top5 if any(hits(c, g) for g in golds))
    precision_at_5 = top5_hits / len(top5) if top5 else 0.0

    traps_taken = []
    for cand in candidates:
        for trap in traps:
            if overlap_fraction_of(cand, trap) < TRAP_FRACTION_OF_TRAP:
                continue
            parent = gold_by_id.get(trap.get("parent_gold"))
            if parent is not None and is_clean_hit(cand, parent):
                continue
            traps_taken.append({"candidate_id": cand["id"], "trap_id": trap["id"],
                                "trap_type": trap.get("trap_type")})

    return {
        "n_gold": n_gold,
        "n_candidates": len(candidates),
        "recall": round(recall, 4),
        "precision_at_5": round(precision_at_5, 4),
        "mean_overlap": round(mean_overlap, 4),
        "duplicate_picks": sum(g["duplicate_picks"] for g in per_gold),
        "traps_taken": traps_taken,
        "per_gold": per_gold,
    }


def load_gold(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def windows_for_source(gold_doc: dict, source_id: str) -> tuple[list[dict], list[dict]]:
    golds, traps = [], []
    for i, w in enumerate(gold_doc.get("windows", [])):
        if w.get("source") != source_id:
            continue
        win = as_window(w, w.get("id") or f"window-{i}")
        if w.get("kind") == "gold":
            golds.append(win)
        elif w.get("kind") == "trap":
            win["parent_gold"] = w.get("parent_gold")
            win["trap_type"] = w.get("trap_type")
            traps.append(win)
    return golds, traps


def load_candidates(path: Path) -> tuple[str | None, list[dict]]:
    doc = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(doc, list):
        source_id, raw_list = None, doc
    else:
        source_id, raw_list = doc.get("source"), doc.get("candidates", [])
    candidates = [as_window(c, f"candidate-{i + 1}") for i, c in enumerate(raw_list)]
    for cand, raw in zip(candidates, raw_list):
        if isinstance(raw, dict) and raw.get("label"):
            cand["id"] = str(raw["label"])
    return source_id, candidates


def labelled_source_ids(gold_doc: dict) -> list[str]:
    return [s["id"] for s in gold_doc.get("sources", []) if s.get("labelled")]


def grade(gold_doc: dict, source_id: str, candidates: list[dict]) -> dict:
    golds, traps = windows_for_source(gold_doc, source_id)
    if not golds:
        raise ValueError(f"source {source_id!r} has no gold windows in the gold set")
    result = score_candidates(golds, traps, candidates)
    result["source"] = source_id
    return result


def fmt_report(result: dict) -> str:
    lines = [
        f"Source:        {result['source']}",
        f"Candidates:    {result['n_candidates']}  (gold windows: {result['n_gold']})",
        f"Recall:        {result['recall']:.2f}",
        f"Precision@5:   {result['precision_at_5']:.2f}",
        f"Mean overlap:  {result['mean_overlap']:.2f}",
        f"Duplicate picks (extra candidates on an already-hit gold): {result['duplicate_picks']}",
    ]
    if result["traps_taken"]:
        lines.append("Traps taken:")
        for t in result["traps_taken"]:
            lines.append(f"  - {t['candidate_id']} took {t['trap_id']} ({t['trap_type']})")
    else:
        lines.append("Traps taken:   none")
    lines.append("Per gold:")
    for g in result["per_gold"]:
        state = "HIT by " + ", ".join(g["hit_by"]) if g["hit"] else "missed"
        lines.append(f"  - {g['gold_id']}: best overlap {g['best_overlap_fraction']:.2f} - {state}")
    return "\n".join(lines)


def self_test() -> int:
    gold_doc = load_gold(HERE / "gold_set.json")
    failures = []

    asrun_src, asrun = load_candidates(HERE / "samples" / "japan_asrun_candidates.json")
    r = grade(gold_doc, asrun_src, asrun)
    print("SELF-TEST 1 - the as-run picks against their own gold set (sanity):")
    print(fmt_report(r))
    if not (r["recall"] == 1.0 and r["precision_at_5"] == 1.0 and r["mean_overlap"] == 1.0):
        failures.append("as-run sanity: expected recall/precision@5/mean-overlap all 1.0")
    if r["traps_taken"] or r["duplicate_picks"]:
        failures.append("as-run sanity: expected no traps taken and no duplicate picks")

    deg_src, degraded = load_candidates(HERE / "samples" / "japan_degraded_candidates.json")
    r2 = grade(gold_doc, deg_src, degraded)
    print("\nSELF-TEST 2 - deliberately degraded picks (must score clearly worse):")
    print(fmt_report(r2))
    if not r2["recall"] <= 0.4:
        failures.append(f"degraded: expected recall <= 0.40, got {r2['recall']}")
    if not r2["mean_overlap"] <= 0.35:
        failures.append(f"degraded: expected mean overlap <= 0.35, got {r2['mean_overlap']}")
    if len({t['trap_id'] for t in r2['traps_taken']}) < 3:
        failures.append("degraded: expected at least 3 distinct traps taken")

    if failures:
        print("\nSELF-TEST FAILED:")
        for f in failures:
            print(f"  - {f}")
        return 1
    print("\nSELF-TEST PASSED: harness reproduces the gold picks perfectly and "
          "penalises the degraded list on every metric.")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--gold", type=Path, default=HERE / "gold_set.json")
    ap.add_argument("--candidates", type=Path, help="candidate list JSON file")
    ap.add_argument("--source", help="gold-set source id (default: from the candidate file)")
    ap.add_argument("--json", action="store_true", help="print the raw result as JSON")
    ap.add_argument("--self-test", action="store_true", help="run the built-in sanity + discrimination check")
    args = ap.parse_args(argv)

    if args.self_test:
        return self_test()
    if not args.candidates:
        ap.error("--candidates is required (or use --self-test)")

    gold_doc = load_gold(args.gold)
    file_source, candidates = load_candidates(args.candidates)
    source_id = args.source or file_source
    if source_id is None:
        labelled = labelled_source_ids(gold_doc)
        if len(labelled) != 1:
            ap.error(f"--source is required; labelled sources: {labelled}")
        source_id = labelled[0]
    try:
        result = grade(gold_doc, source_id, candidates)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(result, indent=2) if args.json else fmt_report(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
