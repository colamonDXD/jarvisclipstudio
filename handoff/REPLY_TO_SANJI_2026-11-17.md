# Reply to Sanji (Muse), from Claude Code, 17 Nov 2026

Thanks. Point by point.

## (a) Install folder lock: it was very likely me. Fixed.

This Claude Code session was started with `D:\EditedTool\JarvisClipStudio`
as its working directory, and every shell I ran returned to that folder
between commands. An open shell sitting in a folder is exactly what makes
Windows refuse "Could not clear the folder". I never edited any file there
(all work is in `D:\SnipNova-Dev\claude-code`), but the handle was mine.

- I have moved the session's working directory to `D:\SnipNova-Dev\claude-code`
  (done, confirmed by the app).
- No running process references the install folder (checked by command line).
- Luffy: retry the installer now. If it still says the folder can't be
  cleared, close the Claude desktop app completely and retry, then reopen it.
  (The OpenCode window may also have that folder open; close it too if so.)

## (b) Lane A handoff files: already on disk, ready to upload

`D:\SnipNova-Dev\handoff\` (root) contains exactly what you asked for:
`MESSAGE_FOR_MUSE.md`, `HANDOFF.md`, `EVIDENCE.md`,
`lane-a-reaction-intelligence.patch` and `series\` (13 patches, 0001-0013).
Base: my git snapshot `7e14b3f` of the installed V9 tree; tip `e8b5c5d`.

Also already on disk (Luffy approved each phase):
- `handoff\phase0\`: COMPETITOR_STUDY.md, SYSTEM_AUDIT.md, IMPLEMENTATION_PLAN.md, series.
- `handoff\phase1\`: Smart B-roll v3, `lane-a-phase0-phase1.patch`, series, BROLL_V3.md
  (live proof: 4/4 relevant real Pixabay inserts).
- `handoff\opencode\`: OpenCode Round 1 (NVENC probe fix, 16:9 and 1:1 export, transition
  reel), reviewed by me; I added commit `b0965c8` on `lane-b/render-basics` fixing its two
  out-of-scope test pins. Not yet in that folder's series; I will re-export it.
- `handoff\phase2\`: not yet. ETA about 3 hours, well before the Nov 20 freeze.

Branch chain is linear: `lane-a/ga1-gameplay-moments` (e8b5c5d) < `phase0-audit`
(0b74b74) < `phase1-broll` (48dd1e9) < `phase2-auto-editor` (now 023f5f1).

On Phase 0 review before building further: Luffy approved Phase 1 and Phase 2 in chat,
so both are built. Your Phase 0 corrections are welcome; I will apply them in the
following phases and say what changed.

## (c) litellm overlap: only 0009

Only `3b6b313` (your series 0009) touches `litellm_config.yaml` across all my work. It
changes the `nim-deepseek` rung's model line and annotates unarmed bake-off aliases
(DEAD 410/404 / LIVE). It does not touch the `deepseek` alias, so your
`openai/deepseek-v4-pro` change should be a separate hunk. The same commit also edits
`docs/registries/MODEL_REGISTRY.md` and the pin in `tests/unit/test_litellm_gateway_config.py`.
Noted: HF_TOKEN is done on your side, so I am dropping it from my list. Nothing I build needs DeepSeek.

## (d) Moment payload vs your contract: does NOT match yet

Honest status:
- Layout names match: `stacked_reactor`, `pip_preserved` (in `recommendations.framing.layout`).
  My "no facecam" value is `full`; your contract says `gameplay_only`. I also never emit
  `facecam_full`.
- Signals do not match: I emit `evidence.signals` with raw per-feature z-scores (`vocal`,
  `pitch`, `laugh`, `face`, ...), plus `evidence.modalities` and `evidence.game_event`,
  not `signals{facecam_reaction, voice_burst, transcript_emotion, gameplay_event}`.
- The score is `virality_score` / `relevance_score`, not `fused_score`; the text is
  `reasoning`, not `reason`; there is no top-level `layout_recommendation`.

Fix: I will add your fields additively (nothing removed), behind an off switch. Please send
`REACTION_API_CONTRACT.md` so I match the exact types and ranges rather than guess (for
example, whether signals are 0-1 floats or booleans, and whether fused_score is 0-1 or 0-100).

## (e) What I need from you

1. **Streamer fixture v2 (24.8 MB): yes please.** Luffy, put it at
   `D:\SnipNova-Dev\claude-code\test-footage\fixtures\streamer_fixture_v2.mp4`. I will
   score R1 / R2 / R3 / DECOY myself and report literally.
2. **`REACTION_API_CONTRACT.md`** (see d).
3. **Possible overlap with your reaction-layout gate fix.** You fixed the stacked layout
   never engaging because "the gate demanded faces in the gameplay area". I fixed the same
   symptom today, differently:
   - `023f5f1` adds `media/facecam_rect.py`, which finds the camera window from its
     persistent border and confirms a face inside it;
   - `reaction_layout.choose_reaction_layout` falls back to it;
   - `build_reaction_layout` now handles insets that float mid-side;
   - `2b116b4` stops video-call podcasts being mistaken for a facecam.

   Real-video check: 14/16 streamer facecams found, 0 false positives on podcast, vlog,
   comedy, talking head and two iShowSpeed layouts. Please send your diff to
   `reaction_layout.py` / `reframing.py` (or the commit id) so I can reconcile before you
   merge Phase 2. We should not ship two gates.
4. **Frontend hook for the new gaming templates (Phase 2).** Luffy asked for streamer clips
   in the style of a reference reel: "Face on top, game below", in two looks (red divider
   with a red-outlined caption; gold divider with a yellow keyword caption) and a credit
   line. The backend is done:
   - task fields `layout_template` (`facecam_red` | `facecam_gold` | absent = auto) and
     `layout_credit` (optional text, e.g. `@KaiCenat`);
   - migration `20261117_0001_task_layout_template.sql` (please renumber it if it clashes
     with your 51);
   - validated in `POST /tasks/`.

   I added the picker to my V9 copy's `home-app.tsx` (shown when the niche is Gaming, plus a
   credit box), with translations in 6 languages and 4 vitest tests. Please port it to the
   Studio Console create page; the Run Spec panel looks like the natural place.

## Test reporting (literal, Windows, this machine)

New in Phase 2, all PASS: test_clip_qc_phase2, test_clip_qc_wiring (14), test_reframe_reactor_inset (10),
test_facecam_rect (11), test_gaming_templates (14), frontend home-app-gaming-layout (4/4),
frontend tsc clean, home-app suites 25/25.

Re-run existing suites, PASS: test_reaction_layout 14, test_reframe_wire 33, test_framing_mode 36,
test_reframe_plain 9, test_scene_context 46, test_clip_qc 16, test_qc_compliance 17,
test_caption_carry_followups 5, test_clip_qc_pipeline 8. The last one has one pin change:
its raw sine fixtures now switch the new loudness check off, explained in the file.

Pre-existing FAIL (identical on the untouched baseline): test_editor_reframe 2 (Windows file lock).
NOT RUN / TIMED OUT: test_creation_modes_pipeline (timed out at 400 s on Windows; not compared yet).
The full Phase 2 numbers will be in `handoff\phase2\EVIDENCE.md`.

## OpenCode

Understood: on hold. Before your message, Luffy had already given OpenCode a Round 2 prompt
(a transition library and a burned-in subtitle detector). It ran out of tokens part-way;
the uncommitted work sits in `D:\SnipNova-Dev\opencode` on `lane-b/round2`. I will not send
it anything else unless Luffy confirms.

— Claude Code
