# To Sanji, from Claude Code (2026-11-18, e): Step 1 + asset library delivered

**Series:** `D:\SnipNova-Dev\handoff\step1-assets\series\`
- 2 patches on top of the Phase 2 tip `996d074`; checksums in `SHA256SUMS.txt`.
- Verified with `git am` on a clean `996d074`: the tree is identical to my branch, binary WAVs included.

| Patch | Commit | What | Switch |
|---|---|---|---|
| 0001 | `870ddb8` | licence-gated music/SFX variant library, meme pack, keyless Openverse B-roll | `ASSET_VARIANTS` (on), `BROLL_OPENVERSE` (on; **off in unit tests** via autouse fixture) |
| 0002 | `83ccbc3` | Step 1: cut, zoom and transition only where the footage gives a reason | `EDL_CONTENT_CUTS`: **default OFF** (your Q15), on only in my harness |

**Step 1 render proof:** `D:\SnipNova-Dev\handoff\quality-clip1\step1\`.
- Same source, same 141–199 s window, same Gaming profile.
- Files:
  - `before_after_step1.mp4`: side by side;
  - `clip1_step1.mp4`: the full render;
  - `cut_list.json`: every boundary with its reason, every dropped candidate with why;
  - `step1_every2s.png`, `ffprobe_step1.txt`, `render_log.txt`.

Check it against ROOT_CAUSE.md:
- Before: 21 equal slices, 5 zooms, a pixelize signature.
- After: 9 segments, each starting at one of the 4 real camera changes (12.95, 22.13, 25.93, 38.74 s) or at a sentence end.
- 0 zooms. Signature suppressed (no shot change in its window). The music-bed beat grid is skipped.
- **Honest limit:** Step 1 alone does not make this clip good. The blurred fit bars (Step 2), the caption loop and scraps (Step 5) and the wrong moment (Step 4) are untouched. That is what the render shows.

**Asset library (your Q14 (b), as specified):**
- **In git and the package:**
  - code;
  - `assets/ASSET_MANIFEST.json` (391 entries);
  - the 6 core SFX plus the synthesised meme pack (WAVs);
  - the gate `tools/assets/check_licences.py` (prints `ship-clean`).
  The 7 core MP3s stay as before.
- **Extended library, not in git, not in install:**
  - `D:\SnipNova-Dev\handoff\asset-library\snipnova-asset-library.zip` (221.5 MB, 378 files, plus a `.sha256`);
  - one command, whenever Luffy wants it: `python tools/assets/library_bundle.py install snipnova-asset-library.zip`;
  - every member is checked against the manifest (sha256 + licence + allowed folder) before it is written, then the gate runs;
  - tested round trip into an empty `assets/`: 378 installed, 0 refused, ship-clean.

**Tests (Windows, my machine):**
- New: test_content_cuts 7/7, test_asset_library 9/9, test_openverse_broll 5/5.
- Suites I touched: B-roll/music/template 128/128; frontend `changelog.test.ts` 4/4.
- With `EDL_CONTENT_CUTS` off, the 30 EDL-behaviour pins that the new mode would change all pass unchanged.
- Pre-existing failures, which **fail identically on `996d074`**, checked in a clean worktree:
  - `test_edl_render.py`, 3 info-card tests: Windows drawtext path escaping;
  - `test_edl_nvenc_escape_hatch.py` (3), `test_edl_av_sync::test_no_fade_edl_keeps_legacy_render_commands`, `test_edl_render_audio::test_no_audio_fields_render_command_is_the_legacy_command`: Windows / this ffmpeg build.
- **Heads-up for your gate:** `test_edl_pipeline_wiring::test_pipeline_edl_honours_classifier_and_fallback` fails on `996d074` too **as soon as `assets/music` is populated**.
  - It expects podcast_news's mood "none", but with music present the Auto bed (calm) applies.
  - V12 now ships the music, so it will fail on your tree too.
  - The test should isolate the music library (point `edl_render.ASSETS_DIR` at an empty tmp dir). Your call; I haven't touched it.

**When Luffy approves Step 1's render:**
- I flip `EDL_CONTENT_CUTS` to default on, in one patch.
- The ~30 quota-edit pins get updated, each with its reason. They pin the equal slicing, rehook punches, bed-snapped beats and the signature window that ROOT_CAUSE.md shows are the defect.

**Next:** Step 2 (no blurred-copy fill; detect people properly in 4K footage).

**OpenCode Round 3:**
- OpenCode ran out of messages. Antigravity is finishing it from my prompt (`handoff\opencode-r3\NEMOTRON_PROMPT.md`).
- Task A (transition catalogue API) is committed; Task B (OFL/Apache font pack + `FONT_MANIFEST.json` + `check_fonts.py`) is being committed now.
- `THEBOLDFONT.ttf` will be reported **UNVERIFIED** (no provable licence). Decide with Luffy whether it keeps shipping.

— Claude Code
