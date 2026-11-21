# Quality Director

The Quality Director is the layer that checks a clip is not just
technically valid but *edited the way its niche demands* — and fixes
what can be fixed mechanically. It came out of a real quality review
(2026-11-08): clips were flat, hooks did not stop the scroll, Music on
Auto produced no music on a podcast, and the AI picked the source's
own intro trailer as a clip. Five mechanisms, all deterministic (no
new AI calls), all leaving a record the QC report and the clip's plan
make visible.

## 1. Hook enforcement + cold open (`auto_director/hook_enforcement.py`)

After the Viral Intelligence Engine plans a clip, enforcement checks
where the planned hook moment lands:

- **Inside the first ~2 s** → nothing changes (`already_leading`).
- **Later, and safe to move** → the hook's whole *sentence* is moved
  to the front by reordering the clip's keep ranges (a cold open).
  Picture, voice and captions all derive from the keep ranges, so
  they move together; no words are cut, added or reworded. The plan
  is remapped onto the new timeline; its transition choices are
  dropped (they targeted joins the reorder rebuilds), exactly like
  Lane K's cold-open variants.
- **Later, but context-bound** → nothing is moved. The guards, in
  order: the hook sits inside the opening sentence; the sentence is
  too short / spans most of the clip; the sentence opens with a
  pronoun, agreement word or back-reference ("as I said…") — the same
  weak-opener judgement the clip-start snapper uses; the hook is a
  punchline; the plan marks the moment as a payoff whose setup
  (context/build phase) plays earlier. The clip is marked
  `kept_original_context` with the guard's reason. **Context
  preservation beats the 2-second rule whenever they conflict.**

Every outcome is stored on the clip's plan as `hook_application`
(status, reason, hook timestamps before/after, the moved block's
bounds). `HOOK_ENFORCEMENT_ENABLED=false` disables the whole
behaviour: no reordering and no record — the pipeline is byte-for-byte
what it was before.

## 2. Music = Auto bed (`media/music_library.py`)

Music precedence, highest first: the user's explicit pick → the
profile's own bed → **the Auto bed**. When a task's music choice is
Auto and the resolved profile carries no bed (podcast_news: mood
"none"), the EDL is stamped with the library's `calm` mood stem at
volume 0.10 with −15 dB ducking — the quietest existing profile bed's
settings (education_tutorial) — and marked `music_source="auto_bed"`.
Two restraints: `music_artist` is exempt (its source *is* music), and
nothing is stamped when no calm track resolves in the library, so a
bare install never manufactures a phantom expectation.
`music_expectation()` exposes the same precedence for QC so the
render is judged against the decision actually in force.

## 3. Intro/teaser exclusion (`ai.py`)

A produced podcast's opening montage — dense one-liners cut from
later in the episode — scores like a great clip. Before the final
selection slice, candidates *fully contained* in the opening zone
(default 45 s, `ANALYSIS_INTRO_ZONE_SECONDS`; 0 disables) are dropped
when **both** hold: the zone shows the montage signature (≥5
transcript lines inside it, median line ≤7 s), and enough candidates
remain outside the zone to fill the slate. Candidates that start in
the zone and run past it are real content and stay; a short video
entirely inside the zone is unaffected; a continuous monologue
opening has no montage signature and is never excluded.

## 4. Per-niche compliance QC (`media/clip_qc.py`)

QC gained a compliance section, judged from records of what the
render actually did — never assumed:

| Check | Evidence | Verdicts |
| --- | --- | --- |
| `hook_window` | `hook_application` on the clip's plan | pass: final hook ≤2 s · warn: kept for context (reason shown), or no hook found |
| `cut_rate` | EDL segment count (render-facts sidecar) ÷ duration vs the profile's `target_cuts_per_min` band (with tolerance) | pass in band · warn outside |
| `music` | The pre-existing gap-RMS measurement, now driven by the full precedence (pick / profile / Auto bed) | silent gaps = **expectation failure** (warn, `expectation_failed`, fixable) instead of a neutral note |
| `caption_presence` | Caption chunk timings the render burned (render-facts sidecar) | warn when a clip with audible speech has no captions |
| `caption_hold` | Same timings vs the pacing cap the render used | warn over cap (fixable) |

The render facts live in the clip's source-map sidecar
(`render_facts`: segment count, caption chunks, hold cap used),
written by `create_single_clip` on every render — pipeline and
re-renders alike.

**Corrective re-render:** a hard failure *or* a mechanically fixable
compliance failure (music missing → re-render applies the bed;
caption hold → the re-render re-chunks) earns exactly one re-render
through the Lane-5 path and a second QC pass (`attempts=2` on the
report). Unfixable failures stay flagged on the clip. Compliance
checks are warn-level only: they can never hard-fail a clip, and QC
still never fails a task.

## 5. Caption pacing cap (`media/captions.py`)

Display chunks are split at word boundaries so no chunk sits on
screen longer than ~3.5 s — ~2.5 s for fast-pace profiles (pacing
band starting at ≥15 cuts/min, via `caption_hold_cap_for_profile`).
A single word longer than the cap cannot be split and stays whole;
QC's `caption_hold` check flags that case honestly. Karaoke word
highlighting is index-based and survives the split untouched. Editor
renders with per-chunk placements keep their own chunking (their
placements are indexed by it); the timings are still recorded, so QC
sees them as they are.

## What the checks cannot judge

Compliance QC verifies *placement and presence*, not taste: it cannot
tell whether a hook is interesting, whether a ducked bed suits the
topic, whether a cut feels right, or whether a caption's words are
the right words. A `kept_original_context` clip may still open weakly
— the record says why the trade was made, and hook-variant previews
remain the user's lever there. The cut-rate check counts EDL
segments, which approximates — not equals — the perceived cut rate
where transitions hide joins.
