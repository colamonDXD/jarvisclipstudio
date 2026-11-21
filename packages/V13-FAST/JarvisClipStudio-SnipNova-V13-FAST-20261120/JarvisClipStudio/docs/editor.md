# Clip editor and Caption Studio

Edit a finished clip's structure and captions, then re-render it for real.
This lane adds a small editor on top of the Lane-5 re-render path — it does
not replace the upstream clip studio at `/tasks/[id]/edit` (whose doc moved
to `docs/clip-studio.md`).

## What you can do

Open a task, open a clip, and the **Clip editor** panel lists the clip's
segments (the EDL's cut layout). You can:

- **Trim** a segment: change its start/end. A trim can only *shrink* a
  segment inside its current window — growing would steal a neighbour's
  footage, so it is rejected. Following segments shift to keep the
  timeline continuous.
- **Split** a segment at a chosen clip-timeline time (the panel splits at
  the midpoint). The new second half starts with a hard cut.
- **Delete** a segment. The following segment inherits the deleted
  segment's join transition, so the join still reads as intended; deleting
  the first segment makes the survivor the opener.
- **Reorder** segments. Join transitions belong to timeline *positions*,
  not to segments, so a moved segment joins its new neighbours the way the
  segment previously there did.
- **Caption Studio**: per-clip template, font family, font size, font
  colour, and caption position (`top` / `middle` / `bottom`, plus a
  fine-grained `position_y` via the API). The values flow through the
  existing caption-building path (template registry, ASS builder) — there
  is no second caption renderer.

Pressing **Apply edits & re-render** queues a background job; the panel
polls it exactly like the existing Re-render control and swaps the
refreshed clip in when it finishes. A failed edit leaves the previous
render untouched.

## How it works

### The EDL is not persisted — the edit plan is

Lane 5 rebuilds a clip's EDL on every re-render from the clip's stored
source ranges + stored text; the EDL itself is never stored. The editor
therefore persists an **edit plan** in the clip's source-map sidecar
(`<clip>.source_map.json`, next to the ranges, under the `"editor"` key):

```json
{
  "version": 1,
  "niche": "gaming",
  "segments": [
    {"index": 0, "start": 0.0, "end": 4.2, "duration": 4.2,
     "transition_in": "none", "transition_duration": 0.0},
    {"index": 1, "start": 4.2, "end": 9.0, "duration": 4.8,
     "transition_in": "fade", "transition_duration": 0.4}
  ],
  "captions": {"template": "hormozi", "font_size": 44,
                "font_color": "#FFE000", "position": "top"}
}
```

Segment times are on the **clip timeline** and always tile
`[0, total duration]`. A clip that predates the editor has no plan: the
first read (or edit) derives one from a fresh EDL build for the clip's
current niche (`persisted: false` in the API response) and the first
successful edit persists it onto the re-rendered file's sidecar.

### Keep-range mapping (the one approximation)

An edit is only real if the *footage* changes, so every segment carries
the slice of the clip's concatenated keep-ranges it shows, stored
relatively (`source_offset` = where its footage begins within the
concatenation, `source_share` = its fraction of the ranges total). The
mapping is stamped when a layout is first bound to a clip — the layout's
timeline windows ARE positions on the concatenated ranges, so the slice
is exact at bind time. Operations then move slices with their segments:

- **delete** drops the segment's slice from the kept ranges;
- **reorder** re-emits the slices in the new order;
- **split** advances the second half's slice past the split point;
- **trim** keeps the head of its slice (the first `duration` seconds) —
  "shrink this part" cuts its tail;
- the kept ranges replace the clip's stored ranges for that render, and
  the render persists them as the new file's source ranges.

The approximation is at bind time: if a clip's segment layout ever
disagreed with its ranges (durations drifted apart), slices follow the
layout's timeline shares, and junctions created by the silence/filler
cleanup can drift by a few frames compared with the original render.
Exact cleanup junctions were never stored, so this is the best mapping
the persisted data supports.

### Rendering seam

Edits call the same `rerender_clip` used by Lane 5 with two extra
optional inputs:

- `segment_plan` — the EDL is built as usual for the niche (zooms, info
  cards, B-roll, SFX, music, colour), then its segments are replaced
  wholesale by the plan's, its duration becomes the plan total, and the
  stored words are re-spread evenly over the plan duration so timed
  placements (cards, B-roll, SFX) track the new layout.
- `caption_overrides` — validated Caption Studio fields. Precedence:
  caption overrides > the re-render's `caption_template` argument > the
  task's stored styling. Template and font size/colour/family go into
  the render arguments; the position name goes onto the EDL caption
  block, which the ASS builder already honours.

After a successful render the plan that rendered is written to the new
file's sidecar, and the clip row's niche override is updated exactly as
Lane 5 does. A plain style re-render (no plan, no overrides) carries the
previous plan's caption overrides forward, so a restyle never silently
drops Caption Studio choices.

### QC interplay

The edit worker runs the same per-clip QC as the pipeline
(`TaskService._qc_edited_clip`): same context construction (duration,
resolution, duplicate detection against siblings, music expectation from
the effective music pick or profile mood), same `qc_status`/`qc_report`
storage, with `"source": "editor"` in the report. One deliberate
difference: a hard failure is **flagged only** — the pipeline's automatic
re-render would discard the edit the user just made, so edited clips
never re-render behind the user's back. QC can never fail the edit job.

## API

All routes live under `/api/tasks` and require task ownership, like the
re-render routes.

| Route | Purpose |
| --- | --- |
| `GET /{task_id}/clips/{clip_id}/edit` | Current edit plan: `{clip_id, task_id, persisted, plan}` |
| `POST /{task_id}/clips/{clip_id}/edit` | Queue an edit job (202): `{clip_id, job_id, status, kind: "edit"}` |
| `GET /{task_id}/clips/{clip_id}/edit/{job_id}` | Job state; edit jobs share the re-render outcome key space (`clip_rerender:`) with `kind: "edit"` |
| `POST /{task_id}/clips/{clip_id}/re-render` | Also accepts an optional `captions` block; when present the request is routed through the edit job so overrides apply |

Edit request body:

```json
{
  "operations": [
    {"type": "trim", "index": 1, "start": 4.2, "end": 8.0},
    {"type": "split", "index": 0, "at": 2.0},
    {"type": "delete", "index": 2},
    {"type": "reorder", "order": [1, 0]}
  ],
  "captions": {
    "template": "hormozi", "font_family": "Montserrat-Bold.ttf",
    "font_size": 44, "font_color": "#FFE000",
    "position": "top", "position_y": 0.18
  },
  "niche_override": "gaming",
  "music_override": "phonk.mp3"
}
```

Operations apply in array order; indexes refer to the segment list after
the preceding operations. At least one of `operations` / `captions` is
required. Validation failures — unknown operation, out-of-range index,
a trim that grows a segment, a segment shorter than 0.5 s, a layout that
does not tile the clip, an unknown template/font, an out-of-range font
size (12–72) or `position_y` (0.1–0.85), a malformed colour — return
**422** before anything is queued or rendered (deep operation validation
runs in the service at job time and lands in the job outcome as a failed
edit with the reason).

The worker job is `edit_clip_task` (one try, one-hour timeout, same
contract as `rerender_clip_task`): failures are recorded in the job
outcome, never retried blindly.

## Limitations

- **No per-word caption re-timing.** The stored transcript for a clip is
  untimed text; on an edited re-render the words are spread evenly over
  the new duration for placement purposes, exactly as Lane-5 re-renders
  already did. Caption *text* is unchanged.
- **`position_y` is finer than the render path can express.** The EDL
  caption block carries a position *name*; `position_y` is validated and
  persisted on the plan, and the name derived from the profile/precedence
  chain is what renders. (The task-settings captions PATCH owns the
  numeric placement for the plain path.)
- **Keep-range drift.** See "Keep-range mapping" above — a few frames at
  cleanup junctions.
- **B-roll is not re-planned** on an edited re-render (same as Lane 5);
  B-roll cues are re-timed proportionally with everything else.
- **No undo history in the panel.** The plan is a single current state;
  the pre-edit render is replaced on success, as with any re-render.
- **Frontend panel scope.** Start/end number inputs, midpoint split,
  up/down reorder, delete, and the five caption fields. There is no
  drag timeline or per-transition picker; the API accepts explicit
  `transition_in` values in plan segments only via future work.
