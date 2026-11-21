# Selection gold set + offline scoring harness

Why this exists: tracker row **1.1** (moment-selection accuracy) stays
PARTIAL because, until now, nothing measured the selector against human
judgement - there was no labelled benchmark. This directory is that
benchmark: a small gold set of labelled windows plus a stdlib-only
scorer that grades any recorded candidate list against it, fully
offline (no model is reachable from the dev sandbox, and none is
needed - grading is arithmetic).

## Files

- `gold_set.json` - the labelled windows and their provenance.
- `score_selection.py` - the scorer CLI (Python 3, stdlib only).
- `samples/japan_asrun_candidates.json` - the Japan run's actual top-5
  picks (sanity input: they define the gold windows).
- `samples/japan_degraded_candidates.json` - a deliberately degraded
  list (shifted duplicate, fragment, payoff-cut, two non-moments) that
  a working harness must score clearly worse.
- `RUBRIC.md` - the gated /100 rubric the gold reasons were written
  against; the companion document for later selection-prompt work.
- `tests/test_score_selection.py` - scoring-math tests.

## The gold set (v1)

One labelled source: **CORE $1 VS $10,000 RESTAURANTS IN JAPAN**
(`japan-restaurants-2026-11-09`, ~68 min), from Luffy's real validation
run of 2026-11-09 - 20 labelled windows:

- **5 gold windows** (`validation: editor_validated`) - the run's top-5
  picks, with as-run scores 92/90/88/86/84, which he praised. Each
  carries a *why it works* reason and virality categories from the
  framework mined in the 2026-11-11 upgrade sweep (hooks, emotional
  peaks, opinion bombs, revelations, conflict, quotable lines, story
  peaks, practical value). Reasons for g1/g3/g4 are grounded in the
  cached range transcript; g2/g5 rest on the fixture hook titles and
  are marked `transcript_grounded: false`.
- **15 trap windows** (`validation: trap_derived`) - three per gold: a
  **duplicate** (the gold shifted +8 s), a **fragment** (the gold's tail
  with its setup cut - fails the "first sentence needs no context"
  gate), and a **payoff_cut** (the gold ending just before its payoff).
  These were derived by the gold-set lane from transcript/fixture
  evidence; no editor rejected these exact windows, and the data says
  so. They exist so a selector that returns *almost* the right moment
  is measurably distinguished from one that returns the moment.

Two further sources (TJR's LA vlog, the JRE bake-off video) are listed
in the gold set as **unlabelled**, with the reason: no transcript or
accepted pick list for either exists in-tree, and inventing labels
would poison the benchmark. They become labelable after a live run on
Luffy's machine whose picks he accepts (his accepted clips + the task's
stored candidates are exactly the two inputs needed).

## Usage

```bash
cd tools/selection-eval
python3 score_selection.py --candidates samples/japan_asrun_candidates.json
python3 score_selection.py --candidates samples/japan_degraded_candidates.json
python3 score_selection.py --self-test        # both samples + expectations
python3 score_selection.py --candidates my_picks.json --json
```

Candidate file: `{"source": "<source id>", "candidates": [...]}` or a
bare list; each candidate is `{"start": ..., "end": ..., "score": ...}`
with times in seconds or `"MM:SS"` strings; `score` optional, `label`
optional (shown in trap reports).

To grade a real run: take the clips of a finished task (start/end of
each exported clip, in source time), write them as a candidate file,
and run the scorer. That grades **selection only** - framing, captions
and render quality are measured by `tools/quality-regression/`, not
here.

## Metric definitions (exact)

- **Hit**: overlap(candidate, gold) ≥ 50% of the **gold** window's
  length.
- **Recall**: gold windows hit by ≥1 candidate / all gold windows.
- **Precision@5**: among the top 5 candidates - by score descending
  when *every* candidate has a numeric score, otherwise file order -
  the fraction hitting ≥1 gold.
- **Mean overlap**: per gold, the best overlap fraction any candidate
  achieved; averaged over golds.
- **Traps taken**: a candidate matches a trap when overlap ≥ 50% of the
  **trap's** length, *unless* the candidate is a clean hit on the
  trap's parent gold (starts within 3 s of the gold's start and ends
  within 3 s of its end). So the genuine pick is never blamed for traps
  nested inside its own gold, while a shifted/fragment/payoff-cut
  window is flagged even when it overlaps the gold.
- **Duplicate picks**: candidates beyond the first that hit an
  already-hit gold.

## Honesty notes

- The gold windows are *human-validated machine picks*, not an
  independent editor's from-scratch picks - no stronger selection
  evidence exists in the project yet. The set measures agreement with
  the one run Luffy approved, plus rejection of known-bad shapes.
- 20 windows on one source is a smoke benchmark, not a statistic.
  Treat score differences under ~0.1 as noise until the set grows
  (TJR/JRE labelling, more sources).
- The scorer never calls a model and never renders; a live selection
  run needs the real gateway on Luffy's machine. What this harness
  adds today: the moment such a run is recorded, it can be graded in
  seconds, offline, against fixed labels.
