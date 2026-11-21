# Gold-Set Report — moment-selection benchmark (2026-11-11)

**Lane:** moment-selection gold-set harness, the step-back review's #1 ranked gap
(`STEP_BACK_REVIEW_2026-11-11.md` §c.1: tracker row **1.1** stays PARTIAL because
"no labelled real-world benchmark measures precision/recall or compares selected
moments with human picks").

**Where:** worktree `~/workspace/jcs-goldset`, branch `eval-selection-goldset` off
`next-milestone @ c2bce92`. **Not merged** (main merges). **No production code
touched** — harness + data + docs only, all under `tools/selection-eval/`.

**Commits:**
- `8949c22` feat(eval): gold set for moment selection from the Japan validation run
- `8ad0a64` feat(eval): offline selection scoring harness with sample runs
- `30e7bcd` docs(eval): gated /100 selection rubric as harness companion
- `d29c3da` test(eval): scoring-math tests for the selection harness

## What was built

1. **`tools/selection-eval/gold_set.json`** — 20 labelled windows on one source,
   plus two further sources recorded as deliberately unlabelled (below).
2. **`tools/selection-eval/score_selection.py`** — stdlib-only offline scorer.
   Input: a recorded candidate list (`{start, end, score?}`, seconds or `MM:SS`).
   Output: **recall** (a candidate hits a gold at overlap ≥ 50% of the gold's
   length), **precision@5** (top 5 by score when all candidates are scored, else
   file order), **mean overlap** (best overlap fraction per gold, averaged),
   **traps taken** and **duplicate picks**. Trap rule: a candidate matching a trap
   window (overlap ≥ 50% of the trap) is flagged *unless* it is a clean hit on the
   trap's parent gold (start within 3 s, end shortfall ≤ 3 s) — so the genuine
   pick is never blamed for traps nested inside its own gold, while a shifted,
   fragment or payoff-cut window is flagged even when it overlaps the gold.
3. **`tools/selection-eval/RUBRIC.md`** — the gated /100 rubric from the upgrade
   sweep documented as the harness's companion: 4 hard gates (first sentence
   needs no context / one complete idea / no intro-outro-sponsor-crosstalk / no
   unowned material), then Hook-in-3s 25, Stands-alone 20, Payoff 20, Emotion 15,
   Specifics 10, Ending 10; ship ≥ 70, maybe 55–69, drop below; length discipline
   25–45 s default, 60 max, 90 only at 85+. Plus the virality-category framework
   and the trap→rubric mapping. **No production prompt was changed.**
4. **`tools/selection-eval/tests/test_score_selection.py`** — 19 scoring-math
   tests (overlap edges, the exact-50% hit boundary, clean-hit tolerances,
   ranking/top-5 cut, trap exemption, real-data shape). **19 passed / 0 failed**,
   run serially with the lane venv (`TMPDIR` on workspace, `OMP_NUM_THREADS=2`,
   log at `~/workspace/tmp-goldset/test-run.log`).

## Gold-set provenance, per window

Source: **CORE $1 VS $10,000 RESTAURANTS IN JAPAN** (`ANoZNWQXYZ4`, 4,090 s),
Luffy's real validation run on his machine, 2026-11-09, task
`80fc89e3-ebd2-450f-bb0c-2c558daf3ee5`, 10 clips in ~40 min, verdict "significant
improvement". The 5 gold ranges/scores/titles are the permanent fixture in
`tools/quality-regression/fixture.json`. A range-limited faster-whisper transcript
is cached at `~/workspace/qm-source/source.transcript_cache.json` and covers
g1/g3/g4 only.

**Gold windows — `editor_validated`** (machine picks a human reviewed and praised;
the strongest selection evidence that exists in the project):

| id | window | as-run score | basis for the reason |
|---|---|---|---|
| g1 | 14:50–15:10 (890–910) | 92, TOP PICK | transcript + fixture title — taste reaction → "700-year-old tree" reveal inside the window |
| g2 | 28:11–28:38 (1691–1718) | 90 | fixture title only ("Wagyu Cows Get Jazz Massages For 30 Years"); `transcript_grounded: false` |
| g3 | 22:04–22:29 (1324–1349) | 88 | transcript + title — opens on "$400 per sandwich?" and ends on "Oh, I didn't mention there's gold. Real gold." |
| g4 | 40:39–41:09 (2439–2469) | 86 | transcript + title — Kobe sandwich presentation → escalating reaction ("It says eat, bro.") |
| g5 | 42:24–42:52 (2544–2572) | 84 | fixture title only ("Four Pieces of Sushi Cost $400"); `transcript_grounded: false` |

Each gold carries virality categories from the sweep's framework (hooks,
emotional_peaks, opinion_bombs, revelations, conflict, quotable_lines,
story_peaks, practical_value) and a "why it works" reason.

**Trap windows — `trap_derived`** (derived by this lane from transcript/fixture
evidence; **no editor ever rejected these exact windows** — they encode the
failure shapes the rubric rejects): three per gold — `duplicate` (gold shifted
+8 s: t1a/t2a/t3a/t4a/t5a), `fragment` (gold's tail, setup cut: t1b "700 years
old. No" with no subject; t3b opens on "So 400 for like this right here?"; t4b
opens mid-reaction; t2b/t5b structural, no transcript), `payoff_cut` (gold ending
just before its payoff: t1c ends ~1 s before the reveal; t3c ends 1 s before
"there's gold"; t4c ends mid-sentence; t2c/t5c structural).

**Recorded but NOT labelled:** TJR "Week in the Life of a Millionaire in Los
Angeles" (`PSNZuuqRff8`) and the JRE bake-off video (`z6S9DyxVwLg`). Neither has a
transcript or an accepted pick list in-tree (TJR appears only as task metadata in
a titles unit test; Luffy's TJR run was rejected for title voice, not validated
as moments). Labelling them would fabricate editor judgement, so the gold set
lists them with `labelled: false` and the reason.

## Sample-run numbers (offline, this sandbox)

`python3 score_selection.py --self-test` (also reproducible per-file):

| candidate list | recall | precision@5 | mean overlap | traps taken | duplicate picks |
|---|---|---|---|---|---|
| Japan as-run top-5 (sanity: these define the golds) | **1.00** | **1.00** | **1.00** | none | 0 |
| Deliberately degraded 5 (shifted copy, fragment, payoff-cut, 2 non-moments) | **0.40** | **0.40** | **0.28** | t1a, t1b, t3b, t3c (5 candidate flags) | 0 |

The degraded list still "hits" g1 at 0.60 and g3 at 0.80 — which is the point of
the trap layer: overlap alone calls those picks fine; the trap report shows one
was a shifted copy and the other had its payoff cut off.

## What a live run on Luffy's machine would add

Everything above grades **recorded** lists. The sandbox has no live provider
route, so no current-build selection has been graded yet. One accepted run on
his machine unlocks, in order:

1. **A real score for the current selector** — export the finished task's clip
   windows as a candidate file, run this scorer, and tracker 1.1 gets its first
   measured number instead of a verdict by vibes.
2. **TJR labelling** — if he accepts a TJR run's clips, those windows become a
   second labelled source (editor-validated the same way), and the set stops
   being a single-source smoke benchmark.
3. **Before/after proof for prompt work** — adopting the gated /100 rubric into
   the selection prompt (a later lane) can be graded on identical sources:
   record candidates before, record after, compare recall/precision/traps.
4. **Regression value** — any future selection change (model route, rerank,
   dedupe) can be checked against fixed labels in seconds, offline.

## Honest limits

- Gold = human-validated machine picks from ONE run of ONE source. 20 windows
  is a smoke benchmark; differences under ~0.1 are noise until the set grows.
- The harness grades selection only — not framing, captions, titles or renders
  (those have their own harnesses: quality-regression, plain-mode proof, titles
  suites).
- Trap windows are lane-derived, labelled as such in the data; if Luffy or an
  editor later disagrees with a specific trap, the fix is editing one JSON file.
