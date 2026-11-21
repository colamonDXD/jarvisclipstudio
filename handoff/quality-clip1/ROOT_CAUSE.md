# clip1.mp4: root cause, responsible components, plan

## What produced this file

`D:\Downloads\clip1.mp4` is byte-identical (md5 `f6f1b633…`) to
`test-footage\phase2\tryit_1cf9a50cd3\clip1.mp4`. My local test page
(`tools/phase2/try_it.py`) made it on 18 Nov at 00:07. The page drives the same
auto-editor code the product uses: `build_edl`, the reframe planner and
`render_edl`.

**Source.** An 8 min 24 s, 4K (3840x2160) IRL vlog filmed at an awards show.
It has no facecam and no screen recording. The "content being reacted to" is
the venue's **stage screen**, which shows the "BEST IRL STREAMER" nominees at
about 165–195 s. Then come the winner announcement, the walk to the stage and
the acceptance speech at about 196–240 s. See `source_160-260s_every4s.png`.

**Settings used.**
- Niche: Gaming.
- AI selection: none. The page had no Groq key, and local qwen3:8b times out,
  so it fell back to a fixed, sentence-aligned window. The run report says so
  ("Pipeline check only … not an AI selection").
- Window: 141.1–199.1 s.

## Each failure, its cause, its component

| # | What you saw | Exact cause | Component |
|---|---|---|---|
| 1 | The thing he reacts to isn't shown | **(a)** The window was a fixed fallback, not a story choice. It **ends at 199 s, just as the winner is announced**: the payoff is cut off. **(b)** The stimulus (stage screen) appears only in wide shots. The framing step has no idea a screen is the subject: it either letterboxes the whole frame small (screen unreadable) or face-crops (screen cut away). **(c)** Selection reads only the transcript. The award moment (165–237 s) has music and applause but no transcribed speech, so a transcript-based selector, AI or not, can't see it. | `tools/phase2/auto_edit.py` fallback window; `ai.py` selection (transcript-only); `media/reframe_plan.py` (no screen-as-subject) |
| 2 | Cuts without a reason | `auto_director/edl.py::build_edl` divides the clip into **equal intervals**: `num_segments = duration/60 × mean(target_cuts_per_min)`. Gaming asks for 15–30 cuts a minute, so 58 s became **21 segments, one every 2.8 s**, whatever was on screen. Each interval can also get its own framing, which made the camera jump. | `auto_director/edl.py` (build_edl segmentation); `profiles/gaming.json` pacing |
| 3 | Zooms with no emphasis | The 5 punch-ins are placed by the profile's zoom density at energy peaks of the **audio bed + crowd noise**, not at a spoken or visual emphasis. | `auto_director/edl.py` zoom keyframes; `profiles/gaming.json` zooms |
| 4 | Blurred frames at 0–4 s, 14–18 s, 24–28 s… | Two things. **(a) Fit layout:** the 16:9 picture sits in the middle of the 9:16 frame, with a **blurred, enlarged copy of itself filling the top and bottom thirds**. Most shots were "fit" because the people are small in the 4K wide shots and the face detector reported "no_subject". About two-thirds of every fit frame is blur (see `clip1_every2s.png`). **(b)** The gaming profile's signature transition is **pixelize**, which looks like a smear. | `media/reframe_plan.py` + `reframing.py` (blur-pad fit, low-resolution face probe); `profiles/gaming.json` transitions.signature |
| 5 | Irrelevant audience / stage wide shots | The window itself is mostly wide crowd footage (141–199 s). Nothing in the pipeline judges whether a shot advances the story: segments are kept by time, not content. | Selection plus build_edl (no shot-level relevance) |
| 6 | Caption fragments ("just", "have a…") | **(a)** Transcription error: the transcript repeats "I'm like, I just want to have a war" over and over from 123 to 167 s. That is a speech-to-text **repetition loop** over loud crowd audio (local Whisper, int8 on CPU), not what was said. **(b)** The caption style breaks every 2–3 words with no regard for phrase boundaries, which produces orphan words. | Transcription fallback (`faster-whisper` path); caption chunking (`caption_templates` / subtitle builder) |
| 7 | No story structure | Follows from 1 + 2 + 5: no stimulus→reaction→payoff timeline exists anywhere in the pipeline. The Phase 2 reaction picker builds one only for **facecam streams** (mic, face and game signals), and this source has no facecam. | Missing component (described below) |
| 8 | No restraint | Effect density comes from **profile quotas** (cuts per minute, zooms per minute, a signature transition), not from evidence in the footage. | Profiles plus build_edl |

**Already fixed since this export, but not enough on its own:**
- `REFRAME_CONSISTENT` (18 Nov) now keeps one layout for the whole clip.
- On this source that layout would be **fit with blur bars** for every shot. That
  fixes the jumping, but the blur and the unreadable screen remain.

**What would be different with a Groq key:** AI selection would pick a
better-spoken window. Failures 2, 3, 4, 6b and 8 would still happen, because
they come from `build_edl`, the profiles and the reframe planner, not from
selection.

## What can be reused

- **Reaction timeline machinery** (`auto_director/reaction_moments.py`):
  - per-second robust z-scores and the agreement rule for episodes;
  - laugh, scream and crowd-burst detection;
  - the face-dynamics measure;
  - `correlate()` (stimulus ↔ reaction).
  It needs one new signal source for non-facecam footage: crowd/applause bursts
  and on-screen change.
- **Layouts:**
  - `media/gaming_templates.build_facecam_stack_layout` (top/bottom stack) and
    `facecam_rect` (finds a persistent bordered rectangle; a stage screen is one);
  - the reframe planner's split/stack builders.
- **Plain template contract** (`template_mode.plainify_edl`) and the
  one-layout rule (`unify_layout_plan`).
- **QC** (`clip_qc`: thought boundaries, loudness), extended to judge content.
- **OpenCode's `burned_captions.py`:** it detects text bands, a cheap
  "readable text on screen" signal.

## What has to change (in order)

1. **Cuts by meaning, not by quota** (`auto_director/edl.py`; switch
   `EDL_CONTENT_CUTS`).
   - Segment boundaries only at real source shot changes or sentence and
     thought boundaries.
   - The profile's cuts per minute becomes a **ceiling**, not a target.
   - No transition on a plain cut. The signature transition only at a real
     scene change, and never pixelize or blur on non-montage profiles.
   - Punch-ins only on evidence: a spoken emphasis word, a laugh or scream
     onset, or a face-reaction peak. At most one per 8 s, and never on a
     wide/fit shot.
2. **No blur filler** (`media/reframing.py`, `reframe_plan.py`).
   - Probe faces and people at a usable resolution on 4K sources; this is why
     these shots came out "no_subject".
   - Prefer a **subject-following crop** (person, not only face) when one
     person carries the shot.
   - When fit is the right answer, pad with a dark, soft gradient sampled from
     the frame edge, **not a blurred copy of the picture**. Switch
     `FIT_BLUR_PAD=0`.
3. **Screen as subject** (`reaction_layout`, `facecam_rect`).
   - Detect a bright, text-bearing rectangle (stage screen, monitor, browser,
     meme window) with the persistent-border detector, plus text-band evidence.
   - When it carries the stimulus, use the **stack layout**: screen on top,
     readable; reactor below. It is used only while the screen shows content;
     otherwise one stable framing.
   - Never cropped away. Never replaced by B-roll.
   - If no source of the stimulus exists in the video, say so in the clip's
     report (`stimulus: "not in source"`); never invent it.
4. **Story-aware selection that sees non-speech moments** (`ai.py` input plus
   the reaction timeline).
   - Give the selector an event track as well as the transcript: crowd and
     applause bursts, screen-content changes, laugh/scream onsets.
   - Build stimulus → reaction → payoff spans, and **extend the clip end until
     the payoff completes** (speech finished, applause decays).
   - The no-key fallback uses the same timeline instead of a fixed window.
5. **Captions that read** (transcription plus caption chunking).
   - Drop repetition loops: the same 4+ word phrase three or more times in a
     row is a transcription artefact.
   - Chunk captions by phrase: break at punctuation or pauses, with no one-word
     lines except deliberate emphasis.
   - Mark low-confidence stretches (crowd or music over speech) as no-caption,
     rather than inventing text.

## Validation (on the same source, then six other content types)

- Corrected render of this exact source: the award story, roughly 160–240 s.
  - Nominees on screen, then the announcement, the reaction, the walk to the
    stage and the start of the speech.
- **Before/after:** the old and new clips side by side in one MP4.
- **Timeline file:** stimulus, reaction and payoff timestamps, with the
  evidence for each.
- **Frame samples:** every 2 s, showing the stage screen readable while it
  carries the story.
- **Checks:** ffprobe output, the render log, and the EDL decisions (every cut,
  zoom and layout with its reason).
- **Human-review checklist:** story, context, framing, captions, pacing, audio
  and visual quality, filled in by me after watching. You watch it before
  anything is called fixed.
- **Separately:** podcast, talking head, gaming facecam, finance, vlog and
  comedy, from the test footage already on disk. Same artefacts for each,
  checked for regressions (a podcast must not suddenly get stacked layouts).

## Time estimate (honest)

| Step | Estimate |
|---|---|
| 1. Cuts by meaning + zoom evidence + transition restraint | 4–5 h |
| 2. No blur filler + 4K subject probe | 4–6 h |
| 3. Screen-as-subject stack layout | 6–8 h |
| 4. Event-aware selection + payoff end | 6–8 h |
| 5. Caption loop filter + phrase chunking | 2–3 h |
| Validation renders + other content types | 4–6 h |
| **Total** | **about 3 working days** |

With the freeze on 20 Nov at 10:00 IST, I propose:
- Steps 1, 2 and 5 plus the corrected render of this source by tomorrow (19 Nov).
- Steps 3 and 4 on 19–20 Nov.
- Any of them that misses the freeze goes in as an early V12.1 patch, off by
  default until you have watched the result.
