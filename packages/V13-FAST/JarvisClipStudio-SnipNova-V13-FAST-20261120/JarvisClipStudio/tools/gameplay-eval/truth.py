"""Ground truth + scoring shared by baseline and new-detector runs."""
GROUND_TRUTH = {"kill": (20.0, 26.0), "death": (47.0, 52.0), "funny": (75.0, 82.0), "clutch": (110.0, 120.0)}
DECOY = (95.0, 97.5)

def _sec(s):
    m, x = s.split(":"); return int(m) * 60 + int(x)

def score_moments(moments):
    """A moment hits an event if its window contains >=50% of the event span.
    Precision = moments that hit a real event / moments. Decoy fails if any
    moment's window contains the decoy span AND hits no real event, or the
    top-ranked (first by virality) moment is decoy-only."""
    wins = [(_sec(m["start_time"]), _sec(m["end_time"]), m) for m in moments]
    hit, useful = {}, 0
    for name, (a, b) in GROUND_TRUTH.items():
        for s, e, _ in wins:
            ov = max(0, min(e, b) - max(s, a))
            if ov >= 0.5 * (b - a): hit[name] = (s, e); break
    for s, e, _ in wins:
        if any(max(0, min(e, b) - max(s, a)) >= 0.5 * (b - a) for a, b in GROUND_TRUTH.values()): useful += 1
    decoy_only = [(s, e) for s, e, _ in wins
                  if max(0, min(e, DECOY[1]) - max(s, DECOY[0])) > 0
                  and not any(max(0, min(e, b) - max(s, a)) >= 0.5 * (b - a) for a, b in GROUND_TRUTH.values())]
    ranked = sorted(wins, key=lambda w: -w[2].get("virality_score", 0))
    top = ranked[0] if ranked else None
    top_is_decoy = bool(top) and (top[0], top[1]) in decoy_only
    return {"events_hit": sorted(hit), "recall": len(hit) / len(GROUND_TRUTH),
            "precision": (useful / len(wins)) if wins else 0.0, "n_moments": len(wins),
            "decoy_selected": bool(decoy_only), "decoy_is_top_ranked": top_is_decoy,
            "decoy_verdict": "FAIL" if top_is_decoy else ("WARN-selected-lower" if decoy_only else "PASS")}
