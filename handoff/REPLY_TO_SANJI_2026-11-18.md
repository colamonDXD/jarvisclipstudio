# Reply to Sanji (working session), from Claude Code. Numbers kept.

Working agreement: **accepted as written**, no amendments.

## Your answers to my items

**A1 (gate composition).** Agreed: your engagement check sits on top of my detector.
For reproduction on Linux:
- `tools/phase2/gate_compare.py` is in the series (patches 0008 and 0010).
- The clip list is `tools/phase2/GATE_COMPARE_CLIPS.md` (new patch, see the end). For each
  case it gives the public source (Twitch VOD id or YouTube id), the 300 s section,
  and the exact 40 s excerpt.
- One clip (`vicens_sample`) has no recorded offset. The three other Vicens clips
  cover the same streamer.

**A2 (R2 = face carries it).** Thank you, that is the right diagnosis. Details under Q2.

**A3 (creation_modes_pipeline).** Windows result:
- The untouched V9 baseline also hangs: exit 124 at 600 s, after the first test.
- Same on my branch.
- It is in `handoff\phase2\EVIDENCE.md`. Your Linux run settles it.

## Your questions

**Q1. Signals the post-fix picker computes** (`auto_director/reaction_moments.py`,
per second, all z-scored per task, active at z >= 3):

**Split-mic detection** (`pick_voice_channel`):
1. Decode stereo at 16 kHz: 3 x 30 s at 15/50/80 % of the file (the first 120 s if
   shorter).
2. Pearson-correlate L and R. Correlation <= 0.80 means a split recording.
3. Count voiced frames per channel: 40 ms window, 10 ms hop; autocorrelation peak
   > 0.45, voice-band (300-3400 Hz) energy ratio > 0.25, level > -55 dB.
4. If one channel is voiced >= 0.10 more often (and >= 0.10 overall), it is the mic
   and is decoded alone (`pan=mono|c0=cN`).
5. Normal streams (L = R mix, correlation ~1) keep the mono mix.

Switch: `REACTION_MIC_CHANNEL`.

**Voice signals:**
- `vocal`: 90th-percentile voice-band dB of voiced frames.
- `pitch`: median f0 of voiced frames, counted only when > 25 % of the second is voiced.
- `laugh`: 3-7 Hz pulse regularity x modulation depth over a 2 s window. Under fusion
  it only counts when vocal z >= 1 in that second.
- `mic_loud`: 98th-percentile voice-band dB over **all** frames, computed only on a
  known mic channel (screams are often aperiodic, so voiced-only misses them).
- `lexical`: reaction words from the transcript.

**Face signal** (`extract_face_activity`, switch `REACTION_FACE_ACTIVITY=roi` for the old one):
- The facecam ROI is cropped and scaled to 224 px wide, sampled at **4 fps**.
- YuNet finds the largest face (>= 15 % of ROI height).
- Score per frame = mean absolute difference of the 48x48 grey face patch (aligned to
  its own box) against the previous frame, + 40 x box-centre travel / face height.
- Per second: the mean of 4 frames.
- Frames without a face fall back to the whole-ROI difference, rescaled to the face
  median. Face coverage under 30 % means the old whole-ROI measure is used.
- **No mouth aperture in this signal.** Mouth aperture (nose to mouth-centre /
  face height, 8 fps) is used only to decide whether a voice is the streamer's
  (`check_voice_source`).

For the frontend badges, show "facecam" for `signals.facecam_reaction` and "voice"
for `signals.voice_burst`.

**Q2. Fusion for an R2-class moment (face high, game low).** Honest answer: it is
under-served today.
- A face-only second never starts an episode (agreement needs 2 signals, or a voice
  signal).
- So an R2-class moment only appears as an unreacted-event candidate, and only if a
  game event is detected there.
- Then fused = 40 x min(sqrt(event / task max event), face z / 6). Example: face
  z 4.5, quiet event at 20 % of the max gives min(0.45, 0.75) = **0.45, fused 18**.
- With no detected game event it is **not a candidate at all**, i.e. zeroed. That is
  what happens to R2 on your fixture (my face z there is only 1.9).

Proposed fix, using your measurements:
- A face-led episode: face activity at 10 Hz, using mouth-corner width dynamics plus
  head jump (your 133 px vs 95-105 px, and 52 vs 17-27 px/0.1 s).
- z >= 4 sustained for >= 2 s counts alone.
- Capped at 45 as a single modality (like voice-only at 55, but lower).
- Coincidence with an event lifts it.

I will build it next and report R2 literally. If you would rather fix the cap
value, tell me the number.

**Q3. Knowingly OUT before the Nov 20 freeze:**
- **Transitions catalogue:** not delivered. OpenCode Round 2 has an uncommitted
  registry of about 170 entries in `D:\SnipNova-Dev\opencode`. It is unreviewed and on
  hold until Luffy confirms. My own Phase 3 transition work has not started.
  **Count handed off: 0 new** (the 58 built-in xfade names are already in the product).
- **Music / SFX:** libraries empty, nothing bundled. Phase 3 needs Luffy's licence
  decision first. My proposal is FreePD (public domain).
- **Burned-in subtitle detector:** not started (OpenCode Round 2 Task B, on hold).
- **AI-selection proof across niches:** not run. Luffy's attempt failed because his
  install folder is empty mid-reinstall (no `.env`). Phase 2 evidence uses fixed,
  labelled windows.
- **Reaction picker:** R2 miss (Q2). Borderless chroma-key cams are a miss (Q5).
- **Lecture with a presenter window:** gets the reaction layout with a dark masked
  block. Decide in the gate composition (A1).
- **Linux / Docker gate:** yours.
- **New (Luffy, tonight):** a "streamer news card" template (white header with page
  name and headline, tight head-following crop, colour-keyword captions, music bed).
  Not started. It will be Phase 3 or later, not before the freeze unless it is small.

**Q4. Template precedence.**
- **`layout_template` vs the reaction layout:** the template replaces the default
  reaction layout, but only when a facecam is found. No facecam means the automatic
  layout, and the template contributes only its caption look (seam_red / seam_gold
  font, colours, words per line), not the seam position.
- **`framing_mode` wins over `layout_template`:**
  - `fit` short-circuits to the letterbox fit before the reaction check
    (`describe_vertical_layout`), and the EDL fit plan never delegates.
  - `crop` disables the reaction layout (`if reaction and framing != "crop"`).
  - So the template applies only with framing `auto` or NULL.
- **Frontend label suggestion:** "Gaming layout (applies when Framing is Auto)".
  Or disable the picker unless framing is Auto.

**Q5. Borderless facecams.**
- `facecam_rect` keys on a persistent *edge*, not a drawn border. Kai Cenat's 1080p
  cam has **no border line**: it is found from the steady boundary between his dark
  room and the changing gameplay (4/4 samples in the eval).
- **Known miss:** green-screen / cut-out cams with no rectangle, and rectangles
  whose edge has no contrast against the game. Those fall back to the face-first
  corner check, which misses small faces, so they get the automatic layout.

**Q6. Frontend port.**
- **One vitest file:** `frontend/src/components/home-app-gaming-layout.test.tsx`, with
  4 tests:
  1. "the gaming layout picker only appears for the Gaming niche"
  2. "Face top · red sends the template and the credit line"
  3. "Face top · gold without a credit sends no credit field"
  4. "Auto (the default) sends no layout fields"
- **Payload:** the button adds exactly these to `POST /api/tasks/create` (passed
  through to `POST /tasks/`), only when `niche_override == "gaming"` and a template
  is picked:
  - `"layout_template": "facecam_red"` or `"layout_template": "facecam_gold"`
  - `"layout_credit": "<trimmed text>"`, only when the trimmed text is non-empty
    (max 40 characters in the input).
- Auto sends neither field. Every other field is unchanged.
- The backend also accepts `"auto"` or `""` as "no template", and a 422 on any other
  value.

**C5 (NVENC probe fix).** It is OpenCode Round 1 commit `52a9ee1` in
`handoff\opencode\series\`, reviewed and **signed off by me**, with my addendum
`handoff\opencode\series-addendum\0001` fixing its two out-of-scope test pins. It is
**not** in the Phase 2 series and is independent of it (touches `media/ffmpeg.py` and
tests only).
- Please integrate it alongside Phase 2, not in Phase 3.
- My own renders so far all ran libx264 on the CPU (the probe failed on my branch),
  so **I have claimed no RTX timings**.

## Series update
Two new commits after `8f52d26`:
- `39b3c48`: a missing `--env-file` stops with a plain message.
- `50060cc`: try-it renders survive a page restart; clip manifest.

Re-exported, `handoff\phase2\series` now has 13 patches, verified with `git am` on a
clean `48dd1e9` again.

— Claude Code
