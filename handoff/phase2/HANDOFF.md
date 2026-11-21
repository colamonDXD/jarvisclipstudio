# Phase 2 handoff: one-click auto-editor, facecam layouts, gaming templates

From Claude Code (Luffy's machine) to Sanji. Branch `lane-a/phase2-auto-editor`,
base `48dd1e9` (Phase 1 tip), linear on top of Lane A, Phase 0 and Phase 1.
Patch series and full patch: `series/` and `phase2.patch` in this folder.
Evidence: `EVIDENCE.md`. Design notes: `docs/phase2/AUTO_EDITOR.md` (in the series).

Tip: **`996d074`, 22 patches** (0022: checkpoint doc) (0021: Plain/Auto harness switch + music-On test now uses a bed-less profile). Verified to apply cleanly in order on a fresh
`48dd1e9` checkout (`git am`), binary SFX included.

- 0012-0013: tool fixes (a missing `--env-file` message, try-it restart safety,
  `GATE_COMPARE_CLIPS.md`).
- 0014-0020 were added on 2026-11-18 (see below).

| Patch | Commit | What | Switch |
|---|---|---|---|
| 0014 | `0be0da5` | one layout per clip (fit / face-crop / split family by screen time) | `REFRAME_CONSISTENT` |
| 0015 | `59f040c` | FreePD CC0 music beds: manifest + fetch/verify script, comedy bed for meme_comedy | (music_mode off) |
| 0016 | `41d27b3` | six original synthesised SFX in `assets/sfx` | — |
| 0017 | `1cbb630` | changelog | — |
| 0018 | `c90853a` | meme comedy bed unducked at 0.15 (17.8 dB under voice) | — |
| 0019 | `bc0dd8a` | face-led reaction candidates (your Q2/Q7) | **`REACTION_FACE_LED`** |
| 0020 | `808cd7b` | changelog PR-link suffix on every entry (your P1) | — |

**Music binaries are NOT in the series** (45 MB). They are in `music/` in this
folder. Copy them to `assets/music/`, or run `tools/assets/fetch_freepd_music.py`,
which re-downloads them from the Internet Archive and verifies each MD5.
Provenance: `docs/assets/MUSIC_PROVENANCE.md`.

| Patch | Commit | What |
|---|---|---|
| 0001 | `545ce39` | harness, CUDA-less transcription fallback, loudness and thought-boundary QC |
| 0002 | `07caa7e` | checkpoint doc |
| 0003 | `2b116b4` | reactor delegation needs a measurable inset (video-call podcast fix) |
| 0004 | `25ac503` | loudness and boundary QC wired into the pipeline |
| 0005 | `023f5f1` | `facecam_rect`: facecam window from its persistent border |
| 0006 | `1b0fcc8` | "face on top, game below" templates, API, migration, frontend picker |
| 0007 | `7c5b03b` | REACTION_API_CONTRACT fields (your §2, as specified) |
| 0008 | `27f00d0` | fixture v2 scorer and gate-comparison runner |
| 0009 | `9a8336c` | checkpoint doc |
| 0010 | `83c2887` | **Lane A fix**: reaction picker passes your decoy (6/7 checks) |
| 0011 | `8f52d26` | local try-it page for Luffy (`tools/phase2/try_it.py`, loopback only) |

0010 changes Lane A code (`auto_director/reaction_moments.py`). Integrate it
after Lane A. Its switches are `REACTION_FUSION`, `REACTION_MIC_CHANNEL` and
`REACTION_FACE_ACTIVITY=roi`.

## What it does for a user

1. **Streamer clips get a real facecam layout.** The app now finds the camera
   window itself: its persistent border, a face inside, and no second fixed
   camera. Small faces, cameras half-way down a side and faceless gameplay no
   longer defeat it. Video-call podcasts are no longer mistaken for a facecam
   (they get both people large, one above the other).
2. **"Face on top, game below" templates** (Luffy's reference reel), red or
   gold, picked per task under "Gaming layout" when the niche is Gaming, with an
   optional "credit @creator" line.
3. **QC checks loudness (-14 LUFS) and complete-thought boundaries** on every
   clip. Both are warn-only and never trigger a re-render.
4. **Transcription survives a GPU without CUDA libraries** (CPU int8 fallback,
   remembered for the process).

## Contract / schema additions (all optional, all additive)

- `tasks.layout_template VARCHAR(20)` and `tasks.layout_credit VARCHAR(48)`:
  migration `20261117_0001_task_layout_template.sql`. Renumber if it clashes
  with your 51.
- `POST /tasks/` accepts `layout_template` (`facecam_red` | `facecam_gold` |
  `auto`/absent) and `layout_credit` (string; kept only with a template). An
  invalid template returns 422, like `framing_mode`.
- Caption templates `seam_red` and `seam_gold` (`GET /caption-templates` lists
  them).
- `create_optimized_clip(..., layout_template=None, layout_credit=None)`, passed
  through `build_vertical_filter_plan` / `describe_vertical_layout` /
  `choose_reaction_layout` as keyword-only additions. Callers that omit them are
  unchanged.

## Off switches

| Switch | Default | Off restores |
|---|---|---|
| `FACECAM_RECT_DETECT` | on | face-first reactor check only |
| `REFRAME_REACTOR_NEEDS_INSET` | on | delegate on a still corner face alone |
| `CLIP_QC_LOUDNESS_LUFS` | -14 | `off` = no loudness check |
| `CLIP_QC_THOUGHT_BOUNDARIES` | on | no boundary words / check |
| `WHISPER_DEVICE` | auto | `cpu` forces CPU |
| `layout_template` | NULL | automatic layout |

## Frontend (please port)

My copy's frontend is the V9 `home-app.tsx`. The picker is written there: a
ToggleGroup (Auto / Face top · red / Face top · gold), shown only for the
Gaming niche, and a credit text box once a template is picked. It has
translations in 6 locales (`gamingLayout.*` in `messages/*/home.json`) and
`home-app-gaming-layout.test.tsx` (4 tests). The payload sends
`layout_template` and `layout_credit` only when a template is picked. Please
port it to the Studio Console create page (Run Spec panel).

## Known overlap to reconcile

Your reaction-layout gate fix (the stacked layout never engaged because the gate
wanted faces in the gameplay) touches the same area as `023f5f1`
(`media/facecam_rect.py`, the `choose_reaction_layout` fallback and
`build_reaction_layout` for insets that float mid-side). Please send me your
diff before merging, so we keep one gate rather than two.

## Not verified

- Linux / Docker gate (yours).
- AI selection across the niches: needs Luffy's one command (free Groq tier).
  Phase 2 evidence uses fixed, sentence-aligned windows, clearly labelled.
- The picker inside the real V10/V11 frontend (written against V9).
