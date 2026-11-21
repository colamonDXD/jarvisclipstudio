# Companion rubric - gated /100 moment scoring

**Status: documentation only.** This file records the scoring rubric
mined from open-source clippers in the 2026-11-11 upgrade sweep
(`research_notes/free-api-radar/2026-11-11/UPGRADE_SWEEP.md`, techniques
section) so the gold set's reasons have a stated standard and later
selection-prompt work has a checked source to adopt from. **No
production prompt was changed by the lane that wrote this file.**

Primary source: `social-media-skills/video-to-shorts-skill` (MIT),
via its dev.to write-up of 2026-10-29, as read and summarised in the
sweep. Category framework: `SamurAIGPT/AI-Youtube-Shorts-Generator`
(MIT, ~5.6k stars), same sweep.

## Step 1 - four hard gates (pass/fail; any failure kills the candidate)

| Gate | Rule |
|---|---|
| (a) | The first sentence needs no context. A cold viewer understands what is happening and what is at stake from the opening line alone. |
| (b) | One complete idea per clip. No mid-thought starts or ends; the window contains a whole mini-arc. |
| (c) | No intro/outro/sponsor/crosstalk. Channel furniture, ad reads and two people talking over each other are not moments. |
| (d) | No unowned music/footage. The clip's value cannot rest on material the clipper has no right or claim to. |

## Step 2 - score the survivors /100

| Component | Points | What earns them |
|---|---:|---|
| Hook in first 3 s | 25 | The opening seconds state or dramatise the stakes: a price, a claim, a question, a reaction - something that makes stopping cost the viewer information. |
| Stands alone | 20 | The clip is fully satisfying with zero knowledge of the source video. |
| Payoff / turn | 20 | Something lands inside the window: a reveal, a verdict, a result, a twist. Setup without landing scores ~0 here. |
| Emotion & energy | 15 | A felt peak - surprise, disbelief, laughter, tension - visibly or audibly peaks, not just talks. |
| Specifics | 10 | Concrete numbers, names, prices, durations. "$400", "700 years old", "four pieces" - specifics are what viewers repeat. |
| Ending | 10 | The clip ends ON the payoff or a quotable line, not on trailing chatter after the peak. |

## Step 3 - decision bands

- **≥ 70: ship.**
- **55–69: maybe** - human look before shipping.
- **< 55: drop.**

## Length discipline

- Default clip length **25–45 s**.
- **60 s** maximum in general.
- **90 s** only for a story that scores **85+**.

## Virality categories (name one per pick, with a "why it works")

From the AI-Youtube-Shorts-Generator framework: the selector reads the
transcript against named categories - **hooks, emotional peaks,
opinion bombs, revelations, conflict, quotable lines, story peaks,
practical value** - scores each candidate, collapses overlapping
candidates (dedupe) before picking the top N, and returns for every
pick its score, a hook sentence and a one-line **"why it works"**.
Forcing the justification measurably improves calibration versus bare
timestamps. The gold set in this directory uses exactly these category
names and carries a why-it-works reason per gold window.

## How the gold set's traps map to this rubric

| Trap type | Rubric failure it encodes |
|---|---|
| `fragment` | Gate (a) - the first sentence needs context the window cut away; also bleeds Stands-alone points. |
| `payoff_cut` | Payoff/turn ≈ 0 and Ending ≈ 0 - the window is all setup; typically drags a true 80+ moment under the 55 drop line. |
| `duplicate` | Not a per-clip failure but a slate failure: two picks spend the viewer's slate on one moment. The framework's collapse-overlaps step exists to prevent it; the harness reports it as duplicate picks / trap taken. |

## Weighted-pillars variant (alternative shape, same sweep)

The Cadence clipper (open source) uses published percentage weights
instead of points: **Hook Strength 30% / Storytelling Tension 30% /
Standalone Value 20% / Shareability 20%**, with structured JSON out
(`start_time`, `virality_score`, `headline`, `reasoning`). Either shape
works; the non-negotiables the sweep identified are *published weights
plus a reasoning field*, so weak picks can be audited after the fact.

## Adoption notes for later prompt work (not done here)

- The gate-then-score shape is model-agnostic: it is a free quality
  upgrade on the current Groq `openai/gpt-oss-120b` selector, not a
  reason to buy a bigger model.
- Keep the rubric text byte-identical across calls if it enters a
  system prompt - the sweep notes Groq prompt caching then makes the
  repeated rubric effectively free against the daily cap.
- When a future lane wires this into the selection prompt, grade the
  before/after candidate lists with `score_selection.py` against
  `gold_set.json` and report both numbers; do not ship on vibes.
