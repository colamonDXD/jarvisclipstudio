# Phase 2 evidence (Windows, Luffy's machine; Linux gate is Sanji's)

Branch `lane-a/phase2-auto-editor`. Commits since `48dd1e9`:

| Commit | What |
|---|---|
| `545ce39` | feat(phase2): harness, CUDA-less transcription fallback, loudness and thought-boundary QC |
| `07caa7e` | docs: checkpoint |
| `2b116b4` | fix(reframe): only delegate a reactor clip when its inset is measurable |
| `25ac503` | feat(qc): run loudness and thought-boundary checks in the pipeline |
| `023f5f1` | feat(reframe): find a streamer's facecam window from its border |
| `1b0fcc8` | feat(layout): "face on top, game below" templates |
| `7c5b03b` | feat(reaction): REACTION_API_CONTRACT fields |
| `27f00d0` | test(tools): fixture v2 scorer, layout-gate comparison runner |

## Tests (literal)

Test environment: `NO_PROXY=localhost`, the fcntl stub on PYTHONPATH, `--no-cov`, real ffmpeg N-125365.

**New tests, all PASS:**

| Test | Result |
|---|---|
| test_clip_qc_phase2 | PASS |
| test_clip_qc_wiring | 14 PASS |
| test_reframe_reactor_inset | 10 PASS |
| test_facecam_rect | 11 PASS |
| test_gaming_templates | 14 PASS |
| test_reaction_contract | 5 PASS |
| frontend home-app-gaming-layout | 4 PASS |
| frontend tsc --noEmit | clean |
| frontend home-app suites | 25 PASS (8 files) |

**Existing suites re-run on this branch, PASS:**

| Suite | Result |
|---|---|
| test_reaction_layout | 14 |
| test_reframe_wire | 33 |
| test_framing_mode | 36 |
| test_reframe_plain | 9 |
| test_scene_context | 46 |
| test_clip_qc | 16 |
| test_qc_compliance | 17 |
| test_caption_carry_followups | 5 |
| test_clip_qc_pipeline | 8 |
| test_reaction_moments + test_reaction_hooks_and_recs | 27 |
| test_broll_toggle | 54 |
| test_settings_integrity_matrix | 9 |
| test_task_settings_readback | 6 + 1 skip |
| test_clip_rerender | 19 |
| test_create_task_source_validation | 4 |
| test_hook_variants | 30 |
| test_clip_timeline_regressions | 19 |
| test_content_requirements_routes | 11 |
| test_publishing_readiness | 15 |
| test_migration_files_safety | 1 |

**Pin and fixture changes (explained in each file):**
- test_clip_qc_pipeline turns the new loudness check off. Its fixture clips are
  raw lavfi tones that never went through the render's `loudnorm=I=-14`.
- Four hand-written SQLite mirrors of `tasks` gain `layout_template` and
  `layout_credit`, exactly as they gained `framing_mode`. No assertion changed.

**Pre-existing, identical on the untouched baseline `7e14b3f`:**
- test_editor_reframe: 2 FAIL (Windows file lock on the export log).
- test_niche_override and test_brand_kit: all pass (23 / 46), then the process
  does not exit (the timeout kills it). Baseline: same.
- test_creation_modes_pipeline: hangs after the first test on Windows.
  Baseline: exit 124 at 600 s, same. NOT RUN to completion on either; please
  confirm on Linux.

## Real renders (final, all Phase 2 code, fixed sentence-aligned windows)

`tools/phase2/run_all.py --select fixed`: 8 sources, 16 clips, **16/16 rendered
at 1080x1920**. Frames are in `render-proof/`.

| Source | Clip 1 | Clip 2 | Layout decision |
|---|---|---|---|
| Finance explainer | -14.5 LUFS | -14.0 | fit (on-screen text kept whole) |
| Video-call podcast | -14.4 | -13.8 | split, both clips (clip 2 was a small-faces fit before `2b116b4`) |
| Talking head | -15.5 | -14.9 | single / fit by shot |
| Travel vlog | -15.5 | -14.6 | single / fit / split by shot |
| Stand-up comedy | -14.2 | -14.6 | single / static |
| Lecture, slides + presenter window | -14.8 | -14.6 | reaction layout (presenter top, slides below)* |
| Kai Cenat 360p | -14.7 | -16.1 | reaction layout (facecam top, game below) |
| Kai Cenat 1080p | -14.7 | -15.9 | reaction layout |

\* The masked presenter window leaves a dark block in the slide's corner. Your
`_source_area_changes` check would keep this lecture on the plain fit instead.
That is the one behavioural difference to settle in the gate composition.

Two clips (vlog 1, Kai HD 2) have boundary QC warnings: those are the harness's
58 s fixed windows ending mid-sentence. The new check flagged them correctly;
AI selection picks real boundaries.

Gaming templates on Kai 1080p: `facecam_red` and `facecam_gold`, 2 clips each,
4/4 rendered with seam captions and credit (`render-proof/templates/`).

## Gate comparison (`tools/phase2/gate_compare.py`, 15 real clips)

Your diff applied cleanly on `48dd1e9` (worktree), your tests 53/53 PASS.

| Clip | Expected | Mine (this branch) | Yours |
|---|---|---|---|
| Kai Cenat 1080p, cam mid-left | facecam layout | reaction | fit |
| Kai Cenat 360p, cam mid-left | facecam layout | reaction | fit |
| Kai GTA IV, small top-left cam | facecam layout | reaction | fit |
| Kai GTA IV, looking down at desk | facecam layout | fit (miss) | fit |
| Vicens GTA V x4 | facecam layout | reaction x4 | fit x4 |
| iShowSpeed Omegle split | not facecam | fit | fit |
| iShowSpeed full-frame cam | not facecam | stage | stage |
| Video-call podcast | not facecam | fit (plain path; EDL path splits) | fit |
| Vlog / stand-up / talking head | not facecam | fit / compositor / fit | same |
| Lecture with presenter window | either | reaction | fit |

Result: mine engages on 7/8 real streamer clips, yours on 0/8 (on real streams
the 480 px analysis pass never finds the facecam face, so the corner-face gate
does not fire). Both give 0/6 false positives.

Suggested ONE gate:
- route on (corner face + measurable inset) OR a `facecam_rect` window;
- compose when source faces exist OR your `_source_area_changes` holds.

## Streamer fixture v2: 6/7 checks PASS after `83c2887` (was 2/7)

`tools/phase2/score_fixture.py`:

| Rank | Window | Matches | fused |
|---|---|---|---|
| 1 | 01:40-01:52 | R3 | 55.0 (commentary_reaction) |
| 2 | 00:16-00:28 | R1 | 18.5 |

DECOY is not in the slate. The checks:
- PASS: R3 #1
- PASS: R1 below R3
- PASS: DECOY not top
- PASS: R1, R2 above DECOY
- PASS: R1 contains 16-28 s
- PASS: R3 contains the scream
- **FAIL: R2 not found.** There is no measurable face reaction or game event
  there. The fixture's facecam is handheld vlog footage, so "most animated face"
  does not separate from the rest of the file.

Synthetic VOD matrix (`tools/gameplay-eval/score_streamer.py vod_v1`): the
streamer-reaction selector went from 2/5 positives to 5/5. The one negative
(`action_no_reaction`) ranks last, 6th of 6 (score 14, against 21 for the
weakest positive).
