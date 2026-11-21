# Reaction Moment API Contract — PROPOSAL

**Status: PROPOSAL — Sanji approves before any backend lane implements it.**
Written by the frontend streamer-journey lane (Sanji side), 2026-11-17,
for Claude Code's GA (reaction-intelligence) work now and DeepSeek's
backend work later. Nothing in this document is implemented yet; the
frontend at `327886b3` + `feat/frontend-streamer-journey` binds only to
the existing moment shape (see §1).

## 1. The existing moment object (what the frontend already consumes)

`GET /api/tasks/{task_id}/candidates` returns the checkpointed candidate
slate; `MomentPicker` (`frontend/src/components/app/moment-picker.tsx`)
declares and renders exactly these fields:

| Field | Type | Notes |
|---|---|---|
| `start_time` | string (`mm:ss`) | rendered as the moment's range start |
| `end_time` | string (`mm:ss`) | rendered as the moment's range end |
| `text` | string | the moment's spoken text / description |
| `reasoning` | string | why the moment was picked (LLM `virality_reasoning` or the visual path's stamped reason) |
| `virality_score` | number | shown as the score badge when > 0 |
| `relevance_score` | number (0–0.95) | declared; produced by the visual-led path |
| `hook_title` | string \| null | the analysis/floor hook title; rendered by the picker when a non-empty string (added on `feat/frontend-streamer-journey`) |

Selection is `POST /api/tasks/{task_id}/select-moments` with
`{ "segment_indexes": number[] }` — unchanged by this proposal.

## 2. The proposed extension (ADDITIVE, all fields OPTIONAL)

A backend lane implementing reaction fusion (GA1/GA2) MAY add the
following fields to each moment object on the same slate. Every field
is optional; absence is always valid and means "not measured", never
zero. No existing field changes name, type, or meaning.

| Field | Type | Meaning |
|---|---|---|
| `signals` | object (all subfields optional) | The per-signal evidence behind the fused score. Subfields below. |
| `signals.facecam_reaction` | number (0–1) \| null | Strength of the facecam reaction evidence (face present in the facecam region + measurable change: mouth-aperture dynamics, face motion/scale change) around the moment. `null` = no facecam detected in the source. |
| `signals.voice_burst` | number (0–1) \| null | Strength of the voice reaction evidence (energy/pitch/onset burst or laughter-like pattern) at the moment. |
| `signals.transcript_emotion` | number (0–1) \| null | Strength of spoken-content emotion evidence (transcript emotion markers + LLM judgement). `null` = no transcript for this moment. |
| `signals.gameplay_event` | number (0–1) \| null | Strength of the simultaneous gameplay/context event (visual-led burst, scene change, on-screen event). |
| `fused_score` | number (0–100) | The reaction×context fusion score. A top rank requires coincidence of reaction evidence AND context — never one signal alone. Distinct from `virality_score`, which keeps its existing meaning; when both exist, ranking UIs prefer `fused_score` for reaction journeys. |
| `reason` | string | One plain-language sentence naming which signals fired, e.g. "Streamer screams as the clutch lands — face, voice and gameplay peak together." Written for the user, shown verbatim. (`reasoning` keeps its existing producer meaning; `reason` is the fusion layer's user-facing summary. A lane may fill both.) |
| `layout_recommendation` | enum string | The layout the render should use for this moment's clip: `"stacked_reactor"` (facecam pane over gameplay, the existing reaction-layout composition), `"pip_preserved"` (facecam inset kept in place, gameplay cropped around it), `"facecam_full"` (the reaction fills the frame for this stretch), `"gameplay_only"` (no facecam worth keeping for this moment). Absent = planner decides, as today. |

### Example moment (existing fields + full extension)

```json
{
  "start_time": "17:42",
  "end_time": "18:05",
  "text": "No way. NO WAY. He actually did it!",
  "reasoning": "Emotional peak: disbelief at the reveal, strongest voice energy in the VOD.",
  "virality_score": 88,
  "relevance_score": 0.91,
  "hook_title": "He was NOT ready for that ending",
  "signals": {
    "facecam_reaction": 0.93,
    "voice_burst": 0.88,
    "transcript_emotion": 0.76,
    "gameplay_event": 0.81
  },
  "fused_score": 91,
  "reason": "Streamer screams at the reveal — face, voice and gameplay peak at the same second.",
  "layout_recommendation": "stacked_reactor"
}
```

## 3. Compatibility rules (binding on any implementing lane)

1. **Additive only.** New fields appear alongside the existing shape;
   no renames, no type changes, no removed fields. The slate stays a
   list of moment objects on the same endpoints.
2. **Existing consumers ignore unknown fields.** The current frontend
   (picker, task page) reads only the fields it declares; extra fields
   must not break parsing anywhere (the slate is stored as JSON in the
   processing checkpoint and round-tripped — field additions must
   survive that round-trip untouched).
3. **Frontend renders each new field only when present.** No UI may
   display a reaction score, signal badge, or layout label for a
   moment that does not carry the corresponding field — and must never
   synthesise one from `virality_score` or any other existing field.
   Until a backend lane ships these fields, the UI shows none of them.
4. **Null means unmeasured, not zero.** A signal that could not be
   measured (no facecam, no transcript, no audio) is `null` or absent,
   never `0` — a genuine 0 is a measured "nothing there", which is
   itself evidence (e.g. the loud-gameplay/no-reaction decoy case:
   `gameplay_event` high, `facecam_reaction` ≈ 0, `voice_burst` ≈ 0,
   and therefore a LOW `fused_score`).
5. **Scores are comparable within a task.** `fused_score` and the
   signal values are normalised per task, so ranking inside one slate
   is meaningful; cross-task comparison is not promised.

## 4. What the frontend will do once fields land (milestone 2, not built)

- Picker rows gain: the `reason` line, a `fused_score` badge (preferred
  over `virality_score` when present), and per-signal indicators —
  each rendered only for moments carrying the data.
- The clip step can surface `layout_recommendation` as the clip's
  layout label once the renderer stamps which layout it actually used
  (recommendation vs applied must stay distinguishable — a follow-up
  field, not part of this proposal).
