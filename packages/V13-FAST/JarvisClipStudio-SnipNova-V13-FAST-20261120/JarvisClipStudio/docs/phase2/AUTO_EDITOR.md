# Phase 2: one-click auto-editor across niches

Goal (master brief §14): upload a video and get finished, publish-ready
vertical clips without manual editing, for every niche, with the same
pipeline the app runs. Branch `lane-a/phase2-auto-editor` (base `48dd1e9`,
the Phase 1 tip).

## How it is proven

`tools/phase2/auto_edit.py` drives the production pipeline end to end on one
video, using the same functions the worker calls:

1. transcription (`get_video_transcript`, faster-whisper);
2. selection: `--select ai` uses `get_most_relevant_parts_by_transcript`;
   `--select fixed` uses sentence-aligned windows and is labelled as not AI;
3. the EDL (`build_edl`), reframe plan (`attach_reframe_plan`), stock B-roll
   (`resolve_edl_broll`, only with keys) and render (`create_optimized_clip`);
4. QC on the finished file: probe, loudness, black frames, sentence
   boundaries and the reframe decision, saved to `report.json` / `report.md`
   with frames at 8 / 50 / 90 %.

`tools/phase2/run_all.py` runs eight sources (finance, podcast, talking
head, vlog, comedy, education, a 360p streamer and the same stream at
1080p) and writes `test-footage/phase2/INDEX.md`.

## What changed

| Change | Switch | Commit |
|---|---|---|
| Transcription falls back to CPU int8 once when CUDA libraries are missing (it used to crash or hang) | `WHISPER_DEVICE=cpu` forces CPU | `545ce39` |
| QC: loudness against -14 LUFS (±2 LU) and "starts/ends on a complete thought", both warn-only, now run in the pipeline | `CLIP_QC_LOUDNESS_LUFS=off`, `CLIP_QC_THOUGHT_BOUNDARIES=0` | `545ce39`, `25ac503` |
| A still corner face only counts as a streamer facecam when its camera rectangle can be measured (video-call podcasts were shrunk into a small-faces fit) | `REFRAME_REACTOR_NEEDS_INSET=0` | `2b116b4` |
| Facecam found from the camera window itself: persistent border, plausible size, a face inside, no second fixed camera | `FACECAM_RECT_DETECT=0` | `023f5f1` |
| "Face on top, game below" templates for streamer clips (red and gold), chosen per task, with a create-form picker for the Gaming niche | `layout_template` per task; NULL = automatic | (next commit) |

## Evidence

See `test-footage/phase2/INDEX.md` and each folder's `report.md`.
Numbers are filled in by the final run (below).

## Not verified

- AI selection on the eight sources (needs the owner's free Groq key: one
  command, see the handoff).
- Linux / Docker gate (Sanji's side).
- The create-form picker in the real V10 frontend: it is written against
  this copy's (V9) `home-app.tsx`; Sanji ports it.
