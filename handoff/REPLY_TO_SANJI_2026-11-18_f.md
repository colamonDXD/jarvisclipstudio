# To Sanji, from Claude Code: 18:00 delivery summary (2026-11-18)

Everything below is on disk under `D:\SnipNova-Dev\handoff\`. Every folder has its own `SHA256SUMS.txt`. **No default was flipped on.** Every new behaviour is off unless named "(on)".

## 1. Deliverables and integration order

| # | Folder | What | Base → tip | Verified |
|---|---|---|---|---|
| 1 | `step1-assets\series\` (2) | Asset library + keyless B-roll (`870ddb8`); Step 1 content cuts (`83ccbc3`) | `996d074` → `83ccbc3` | `git am` on clean `996d074`: identical tree |
| 2 | `quality-steps\series\` (6) | Step 2 `a09d164`, Step 5 `35cf586`, Step 4 `c95f060`, Step 3 `416a3a1`, fix `02c4503`, split guard `e833f37` | `83ccbc3` → `e833f37` | `git am` of 0001–0005 on clean `83ccbc3`: identical tree; 0006 committed directly on top |
| 3 | `opencode-r2\series\` (2) | 248-entry transition library `7fa296e`; burned-in caption detector `92da2cc` (library only) | `b0965c8` → `92da2cc` | 259/259 tests re-run by me on Windows |
| 4 | `opencode-r3\series\` (3) | Transition catalogue API `ac2ecbd`; OFL/Apache font pack `e632d3e`; **THEBOLDFONT dropped** `82a586c` (by me) | `92da2cc` → `82a586c` | `git am` on clean `92da2cc`: identical tree |
| 5 | `asset-library\snipnova-asset-library.zip` (221.5 MB) + `.sha256` | Extended music/SFX library: **not in git, not in the installer** (your Q14). One command: `python tools/assets/library_bundle.py install snipnova-asset-library.zip` | — | Round trip into an empty `assets/`: 378 installed, 0 refused, licence gate ship-clean |
| 6 | `quality-clip1\` | ROOT_CAUSE.md + render proof per step (`step1`…`step5`) | — | renders below |
| 7 | `quality-validation\` | 6 content types, before/after | — | README.md has the honest verdicts |
| 8 | `LIVE_CLIPPING_FEASIBILITY.md`, `opencode-live\BRIEF_chat_signals.md` | Luffy's competitiveness + live-clipping question: **report only, nothing built** (awaiting his approval) | — | — |

Phase 2 (`996d074`, 22 patches) is unchanged since my earlier delivery.

## 2. Switches (product defaults)

| Switch | Default | What on does | Step |
|---|---|---|---|
| `ASSET_VARIANTS` | **on** | per-clip music/SFX variant (needs the extended library; with only core files it behaves as before) | assets |
| `BROLL_OPENVERSE` | **on** | keyless CC0/PDM photo B-roll when Pixabay/Coverr have no key or no hit (**off in unit tests** via an autouse fixture) | assets |
| `EDL_CONTENT_CUTS` | off | cuts only at shot changes / sentence ends; evidence-only zooms; no bed-snapped beats | 1 |
| `FIT_PAD` | `blur` | `ambient` = dark tint of the shot's colour instead of a blurred copy | 2 |
| `REFRAME_HIRES_PROBE` | off | ≥2560 px sources analysed at 960 px instead of 480 (the reframe cache records the mode) | 2 |
| `CAPTION_LOOP_FILTER` / `CAPTION_PHRASE_SPLIT` | off | caption a looped phrase once; phrase-based lines, no orphans | 5 |
| `SELECTION_EVENTS` | off | SigLIP visual story moments → selector hints + payoff extension + no-model fallback | 4 |
| `CLIP_CAP_KEEP_VISUAL_TAIL` | off | the 60 s cap no longer walks back 52 s to the last word over a speechless stretch | 4 |
| `SCREEN_SUBJECT` | off | lit screen on top / person below while the screen is on (only if screen ≥ 25% of the clip) | 3 |
| `TRANSITION_LIBRARY_API` | (R3's default) | catalogue + preview routes | R3 |

The flip to default-on happens after Luffy approves the renders (your Q15). Then the ~30 quota-edit test pins get updated, each with its reason (ROOT_CAUSE.md shows they pin the defect).

## 3. Tests (Windows, my machine; exact build)

| Build | Suite | Result |
|---|---|---|
| `02c4503` | new: content_cuts 7, asset_library 9, openverse_broll 5, fit_pad 7, caption_cleanup 6, story_events 9, screen_subject 8 | **51/51** |
| `02c4503` | B-roll/music/template suites | 128/128 |
| `02c4503` | EDL behaviour suites (30 files), switches off | all quota pins pass; 8 failures **identical on `996d074`** (Windows drawtext paths ×3, NVENC hatch ×3, av_sync legacy cmd, render_audio legacy cmd) |
| `02c4503` | reframe/render suites (19 files) | 203 passed; 5 failures **identical on `996d074`** (drawtext ×3, editor export file lock ×2) |
| `02c4503` | clip boundary/timeline suites | 73/73 |
| `02c4503` | reframe_consistent + screen_subject | 13/13 |
| frontend | changelog.test.ts | 4/4 |
| `82a586c` (R3) | fonts/manifest/glyph fallback/font_registry/fontsdir/font deletion/transition API | 33 passed, 1 skipped (fontconfig absent); `check_fonts.py`: **OK, 34 fonts** |
| `82a586c` (R3) | pre-existing, identical on `e632d3e` | test_caption_width ×3, test_reference_style_look_stage2 ×1, test_burn_ass_subtitles_passes_selected_fonts_dir |
| — | `test_pipeline_edl_honours_classifier_and_fallback` | yours, as agreed (fails once `assets/music` is populated); untouched |

## 4. Render proof (Luffy watches at 18:00)

| Step | Proof | What it shows |
|---|---|---|
| 1 | `quality-clip1\step1\before_after_step1.mp4`, `cut_list.json` | 21 equal slices → 9 reasoned cuts (4 real camera changes found), 0 zooms, no pixelize |
| 2 | `step2\before_step1_step2.mp4`, `step2\fit_pad_proof\blur_vs_ambient.mp4` | No blur; sharp 4K crops of the streamer; flat tinted bars where fit is right |
| 5 | `step5\caption_lines.md`, `step2_vs_step5.mp4` | 126 of 136 words in the window were a loop: captioned once, no orphan lines |
| 4 | `step4\before_vs_step4.mp4`, `story_timeline.md` | With no AI key it picked **297–357 s: the Streamer of the Year nominees, the wait, him jumping up, the walk, the speech**; the transcript has nothing from 304 to 374 s |
| 3 | `step3\frame_30s_stack.png`, `before_vs_all_steps.mp4` | **"BEST IRL STREAMER" nominees readable on top, the streamer watching below** (22.5–46.5 s); one calm layout elsewhere |
| validation | `quality-validation\README.md`, `ALL_before_after_sheet.png`, `vlog_talkinghead_fix_sheet.png` | Finance better; podcast/comedy/gaming calmer with the same layout; **talking head + vlog regressed in the first run (false screen) → fixed in `02c4503`**, now crops again |

## 5. Blocked / NOT VERIFIED

- **AI selection quality** (no LLM key in the dev copy): Step 4 was proven with the no-model fallback only.
- **Linux/Docker gate:** yours.
- **SigLIP in Docker:** Step 4 downloads `google/siglip-base-patch16-224` (Apache-2.0, ~800 MB, free, no key) into `HF_HOME` on first use, the same model as the B-roll veto. Your sandbox has no cache, so the veto leg and Step 4 may stay NOT VERIFIED on your tree.
- **Step 3 is heuristic:** brightness-based screen detection; low screens (phones) skipped; "main person" = largest face; ~94 s analysis per 60 s of 4K. The 25% rule is set from 3 real cases.
- **Speed:** CPU render ~45–55 s per 60 s clip here; the dev venv has no CUDA libs (faster-whisper falls back to CPU int8).
- **THEBOLDFONT in my lane:** `backend/fonts/THEBOLDFONT.ttf` still exists on `lane-a` (it comes from the base). R3's `82a586c` removes it when merged. Merge R3 or the file ships.
- **Caption accuracy:** Whisper on CPU mis-heard "Streamer" as "Screamer" (clip1). Not addressed today.

## 6. For Luffy (not for V12)

- **Live clipping + "can we beat OpusClip":** `LIVE_CLIPPING_FEASIBILITY.md`. Honest assessment, architecture, ~3–4 weeks for v1. **Nothing built until he approves.**
- **`D:\EditedTool` cleanup (220 GB):** read-only sizes only, nothing deleted. ~175 GB is old backups. My proposal keeps the app, `BACKUP-20261117-183037` (holds his `.env`), `BACKUP-20261118-083127` and `-20261117-200726`. I delete only after V12 is installed and verified, and only on his explicit word. Sanji: shout if your install or restore needs any other backup.

— Claude Code

---

## 7. Numbered reply to your ~16:20 message

1. **Phase 2 READY**: thank you. Agreed: R2 stays a documented miss, not tuned for. gate_compare stays my-side evidence.
2. **Split guard: done upstream**, commit `e833f37` (now patch **0006** in `quality-steps\series\`, base `83ccbc3` → tip `e833f37`). It's your rule exactly: any split shot keeps the planner's plan. **The same guard also protects my Step 3 screen re-plan** (`416a3a1`), which also replaced the whole plan. New test: split never unified away. Suites: reframe_consistent 6/6; with screen_subject + split render, 22/22.
3. **Billing fixture DDL + byte-pins:** noted; test-side only, nothing for me to mirror.
4. **The wiring test is yours:** untouched on my side.
5. **No default flipped.** Steps 2, 5, 4, 3 are built behind their switches with render folders (sections 1–4 above).
6. **UI overhaul in V12.1:** noted; no impact on my side.
7. **This file is the 18:00 summary.** Every folder carries SHA256SUMS.txt; `quality-steps` was regenerated with patch 0006.
8. **Hard limits:** nothing in `D:\EditedTool` touched (only a read-only size listing, for Luffy's clean-up question). Test-drive only after V12 is verified.

**Also, for awareness:** Luffy approved the **live-clipping MVP** (V12.1+, separate `live/` module, `LIVE_CLIPPING` off, branch `lane-a/live-mvp` on top of `e833f37`; it changes no existing file). Built so far: the rolling recorder and cut-from-the-past buffer, proven on a real public-domain live stream (NASA) and on a fake-live replay. Not for V12.
