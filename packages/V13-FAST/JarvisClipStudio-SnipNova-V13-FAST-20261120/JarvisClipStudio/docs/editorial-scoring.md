# Editorial quality scoring

Each finished clip carries an `editorial_quality` breakdown alongside
its existing selection scores. It is an editorial-quality assessment
of the clip as made, built only from evidence the pipeline already
recorded — quality-control measurements on the finished render, the
clip's edit-plan record, transcript facts, and the stored
transcript-analysis sub-scores. It is not a forecast of audience
response, and nothing in its labels, fields or reasons claims one.

## Shape

```json
{
  "label": "Editorial quality",
  "note": "Editorial quality assessment only, based on the evidence cited for each dimension.",
  "aggregate": 83,
  "scored_dimensions": 9,
  "legacy_selection_total": 75,
  "dimensions": {
    "hook_strength": {"label": "Hook strength", "score": 86, "reason": "...", "source": "derived"},
    "visual_quality": {"label": "Visual quality", "score": null, "reason": "not measured", "source": null}
  }
}
```

Every dimension returns `{label, score, reason, source}`:

- `score` is 0–100, or `null` when the dimension was not measured.
  There are no default or guessed scores: a dimension with no
  evidence says `not measured`.
- `reason` is one line citing the evidence the score came from.
- `source` is `measured` (render/QC facts), `llm` (a stored
  transcript-analysis sub-score reused as-is), `derived` (computed
  from transcript/plan facts, or a blend), or `null` when not
  measured.

`campaign_compliance` is an optional extra dimension: it appears
only when the clip carries a campaign brief verdict (a stored
compliance record or a QC campaign check). Without one, the key is
absent — not null.

## Dimensions and evidence

| Dimension | Evidence |
| --- | --- |
| Hook strength | Stored hook sub-score (×4), blended with the QC hook-window outcome when recorded |
| Story coherence | Transcript sentence completeness and word count; edit-plan phase coverage (hook / context / build / payoff) |
| Context preservation | QC "kept in place for context" hook outcome; share of the clip covered by the plan's context phase; transcript length |
| Visual quality | QC checks: resolution, black frames, frozen frame, subject-in-frame sampling |
| Audio quality | QC checks: audio level (overall RMS) and music-bed presence when music was expected |
| Caption accuracy | QC checks: caption presence (chunks burned) and longest caption hold vs the pacing cap. Only presence/pacing are measured — caption wording is not compared to the transcript, and the reason says so |
| Pacing | QC cut rate vs the profile band, caption hold outcome, and speech rate (transcript words per minute) |
| Emotional interest | Stored engagement sub-score (×4), reused as-is |
| Payoff | Stored value sub-score (×4), blended with the plan's payoff phase position when recorded |
| Niche relevance | Stored segment relevance score (×100), with the rendered niche named in the reason |
| Campaign compliance | Stored compliance record or QC campaign check, only when present |

Known limits, honestly: caption accuracy measures presence and
pacing only; story coherence and context preservation are computed
from transcript completeness and plan phases, not a semantic read of
the story; the transcript-analysis sub-scores are one model's
editorial judgement from selection time, reused and labelled `llm`.

## Aggregate

The aggregate is a weighted mean of the dimensions that have a
score, with weights renormalised over those dimensions:

hook strength 14, story coherence 10, context preservation 10,
visual quality 12, audio quality 10, caption accuracy 9, pacing 10,
emotional interest 8, payoff 9, niche relevance 8 — plus campaign
compliance 10 when that dimension is present.

## Compatibility

No schema change and no migration: the breakdown is computed at
serialization time in `ClipRepository.get_clips_by_task` /
`get_clip_by_id` from the clip's stored `qc_report` and `clip_plan`
JSON, its transcript text and its stored sub-scores, by the pure
builder in `backend/src/editorial_scoring.py`. The legacy selection
score fields are untouched and remain the inputs to segment ranking
and the learning engine; the breakdown is display/API only and has
no influence on ranking.
