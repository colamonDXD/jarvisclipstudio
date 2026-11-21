# Per-Task Text Band Unification — Lane Report

Branch `feat/task-text-bands` (worktree `~/workspace/jcs-taskbands`), stacked on
`fix/multisubject-framing` @ f875004. Nothing merged; jcs-next stays at 5ea71bf.
Date: 2026-11-16.

## Step 0 — design answers

**(1) Pre-pass point.** `TaskService.process_task` already resolves every
task-level render input once and threads it into the per-clip renders
(`brand_application`, `broll_session`, `render_vocals_stem`). The band pre-pass
joins that exact seam — after `segments_to_render`, `time_map`, `video_path`,
the task's template mode and `existing_clips` are known, immediately before the
render tasks are created. Clips render concurrently under a semaphore, so the
decision must complete before any render starts; at this seam it does. Per
segment, geometry is produced with the PRODUCTION code: a one-segment mini-EDL
over the segment's localized source ranges → `attach_reframe_plan(...)` (the
source analysis is cached per source, so the render's own attach reuses it) →
`collect_face_zones` / `collect_fit_strips`. The only approximation is
segmentation (raw window vs the render's final keep-range tiles); safety does
not depend on it because engagement is re-verified exactly at render time.

**(2) Aggregation rule — worst-case union guard.** The task candidate is the
preference position guarded against the UNION of every clip's dominant
obstacles (coverage ≥ 0.8 of the clip's span — the framing lane's dominance
rule, lifted to task level). It engages only if the representative block
(widest/tallest any clip can draw) fits face-free against the union of HARD
obstacles (all clips' faces + the task title obstacle). If the full obstacle
set leaves no band, retry against the hard set alone (the framing lane's
priority pass: strips yield). If it still fails, that band does not engage and
the task's clips keep the per-clip system. Titles are decided first (captions
need the title obstacle); title blocks are centre+bottom anchored, so a
representative (max w × max h) box clearing the union guarantees every clip's
own guard finds the task bottom clear at natural size. Per clip at render, the
ASS builder re-verifies the offered centre against THAT clip's real hard
obstacles and real block dims: fits → adopted (stamped `source: "task"`);
doesn't fit → today's per-clip computation runs unchanged and the deviation is
stamped (`task_deviated`). The title bottom is a preference the clip's own
title guard arbitrates the same way. An explicit per-clip caption position
(EDL `position_y` — the Studio/editor contract, editor audit D1/D2) is never
overridden: `create_optimized_clip` withholds the task centre for such clips
and stamps the withholding.

**(3) Persistence — NO migration.** `tasks.source_metadata` (JSONB) via the
existing `TaskRepository.save_source_metadata` / `load_source_metadata` — the
same store Recipes uses for provenance. Key `text_bands`:
`{version, width, height, caption_centre, title_bottom, clips_considered}`
(height fractions, 4dp; null = that band did not engage). Written once, before
the render loop; a stored decision is loaded and NEVER re-decided (a resume
rendering a subset cannot shift bands under already-rendered clips).
Re-renders/regenerations only read.

**(4) Old tasks.** A decision is taken only for a task with NO existing clips
and NO stored decision (a fresh render slate). Legacy tasks, resumed tasks
with clips, and every re-render seam without stored bands behave
byte-identically to before: every new kwarg is optional and conditional
(the vocals_stem / allowed_ranges pattern), so untouched callers keep the
historical call shape.

## Built

- `backend/src/media/captions.py` (d65c29f): builder gains
  `task_caption_centre` / `task_title_bottom` + stamps; `natural_hook_title_block`
  mirror helper for the decision.
- `backend/src/media/text_bands.py` (8a857ff, new): geometry collection +
  pure decision `decide_bands_from_geometry` + `decide_task_text_bands`
  (never raises; failure → None → per-clip behaviour).
- `backend/src/video_utils.py`, `backend/src/services/video_service.py`
  (a5f5532): threading through create_optimized_clip / create_single_clip /
  create_video_clips / create_clips_from_segments / create_clips_with_transitions,
  with the explicit-caption-position arbitration at the create_optimized_clip seam.
- `backend/src/services/task_service.py`, `backend/src/services/clip_service.py`
  (4921fb9): the pre-pass + persistence + fresh-slate rule; re-render and
  regeneration reuse via `ClipEditingMixin._load_task_text_bands`.
- Tests (0be1bca): `backend/tests/unit/test_task_text_bands.py`, 13 tests.
- CHANGELOG (7291572): 2026-11-16 entry; changelog vitest 4/4 PASS.

## Rendered proof (3-clip synthetic task, ASS-level measurement)

Geometries: clip 1 face low (y 1250–1610), clip 2 face high (y 300–660),
clip 3 face mid (y 1000–1300), 1080×1920. Task decision:
`caption_centre 0.482, title_bottom 0.15`.

| clip | caption centre BEFORE | AFTER | title bottom BEFORE | AFTER |
|---|---|---|---|---|
| 1 (low face) | 1176 | **925** | 330 | **288** |
| 2 (high face) | 1490 | **925** | 288 (guard-moved) | **288** |
| 3 (mid face) | 1490 | **925** | 330 | **288** |

BEFORE: captions diverge (1176 vs 1490), titles diverge (330 vs 288).
AFTER: identical everywhere; every clip stamped `source: "task"`,
`task_deviated: False`, chunk deviations 0. The task title bottom (288)
is exactly the binding constraint clip 2's own guard had chosen alone.
Artefacts: `~/workspace/taskbands-proof/` (script + generated ASS files).

## Tested

- New tests: 13 passed / 0 failed.
- Caption families (band consistency, face guard, fit strips, title fit
  bands, hook title styles, caption position wiring): 56 passed / 0 failed.
- Reframe + scene families (11 files): 171 passed / 0 failed.
- Neighbours (creation modes ×3, clip rerender, broll rerender, vocals-stem
  regen/rerender, caption carry/glyph/pacing/quality/roundtrip/width/timing,
  clip editor captions, creator titles, title person, face guard render,
  hook enforcement, EDL pipeline wiring): 289 passed / 3 failed — the 3
  failures are all `test_caption_face_guard_render.py`, all
  `FileNotFoundError: 'tesseract'` (the sandbox lost the apt binary in a
  runtime restart; `apt-get update` was lock-held by a system process).
  A/B: the pristine framing tip f875004 fails the identical 3 tests.
  NOT lane-caused.
- Full backend unit suite on the branch tip 7291572: **4,659 passed /
  21 failed / 21 skipped / 7 xfailed** (2,262.6s). All 21 failures are
  `tests/unit/test_media_replication.py` — the pre-existing
  sandbox-environment set the framing lane documented count-identical
  (21×) on the untouched base; this lane touches nothing in media
  replication. Zero errors. The 13 new tests are inside the 4,659.
- Postscript on the tesseract failures: the sandbox's own apt refresh
  completed during the full-suite run and restored /usr/bin/tesseract;
  `test_caption_face_guard_render.py` then passed inside the full run
  and again standalone on the branch: **3 passed**.

## Honest remains

- The decision's geometry is computed on the raw segment window, not the
  render's final keep-range tiles (sentence snapping / hook teaser / brief
  clamps are unknowable pre-render without duplicating create_single_clip's
  planning). Render-time verification absorbs the difference; the cost of a
  miss is a stamped deviation, never a face collision.
- The title representative block uses natural template sizing; a Brand Kit
  "band" title can render slightly taller — absorbed by the per-clip title
  guard the same way (deviation stamped).
- Hook-preview renders (generate_hook_previews) do not take the task bands —
  previews are comparison renders, not deliverable clips; out of the brief's
  scope (single-clip re-renders/regenerations are covered).
- Persistence-failure + crash + resume in that exact order can leave a task
  whose first clips used in-memory bands and whose remaining clips render
  per-clip (no stored decision, existing clips → no re-decision by design).
- Live-PostgreSQL tier NOT RUN (unit tests mock the repository seam, per
  repo norm); the post-merge gate owes it, as with every queued branch.
- User-machine validation pending (post-merge): re-run the MrBeast task and
  compare title/caption band stamps across its clips.
