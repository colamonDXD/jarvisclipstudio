# ONE combined series against the shipped V12 base

- **Base:** `JarvisClipStudio-SnipNova-Consolidated-V12-20261118.zip` (SHA-256 `19706d5b…a11b6`, verified), with its `JarvisClipStudio/` tree committed as-is (`next-milestone @ 49cfad22`).
- **Tip:** `1e8d405`, 18 patches in `series/`, numbered in apply order.
- **Proof:** `git am` of all 18 onto a fresh copy of the V12 base gives a tree **byte-identical** to my combined tip.

| # | Patch | From |
|---|---|---|
| 0001 | assets: licence-gated music/SFX variant library + keyless Openverse B-roll | step1-assets 1 |
| 0002 | edl: content cuts (Step 1) | step1-assets 2 |
| 0003 | reframe: no blurred-copy fill, 4K probe (Step 2) | quality 1 |
| 0004 | captions: loop filter + phrase split (Step 5) | quality 2 |
| 0005 | selection: visual story moments + payoff (Step 4) | quality 3 |
| 0006 | reframe: screen-as-subject stack (Step 3) | quality 4 |
| 0007 | reframe: screen framing only when ≥ 25% of the clip | quality 5 |
| 0008 | reframe: never screen-replan away split shots | quality 6, **reduced**: V12 already has your unify guard (e4872fc3); only the screen-path guard + test remain |
| 0009 | media: 248-entry transition library | OpenCode R2 |
| 0010 | media: burned-in caption band detector | OpenCode R2 |
| 0011 | media: transition catalogue API + previews | OpenCode R3 |
| 0012 | fonts: OFL/Apache caption font pack + FONT_MANIFEST + check_fonts | OpenCode R3 |
| 0013 | fonts: THEBOLDFONT dropped, aliased to Montserrat-Black | R3 (by me) |
| 0014 | live: rolling recorder + cut-from-the-past buffer | live MVP |
| 0015 | live: audio excitement, reaction scan, fuser, quick preview | live MVP |
| 0016 | live: fast voice pass + picker confirmation | live MVP |
| 0017 | live: record_live / finish_live runners (tools) | live MVP |
| 0018 | tools: `--streamer-words-only` (no game dialogue in captions) | campaign fix (tools only) |

**NVENC dedupe:** OpenCode's NVENC series (52a9ee1, c55d34a, 7162804, b0965c8) is **already in V12**, verified by reverse-apply. It's therefore **not** in this series.

**CHANGELOG:** each patch carries its own lines under its date (V12's head differs from my old lineage). One `## 2026-11-18` heading; every entry ends with the `([#TBD](…))` suffix (checked).

**Switch defaults are unchanged.** Everything new is OFF: `EDL_CONTENT_CUTS`, `FIT_PAD=blur`, `REFRAME_HIRES_PROBE`, `CAPTION_LOOP_FILTER`, `CAPTION_PHRASE_SPLIT`, `SELECTION_EVENTS`, `CLIP_CAP_KEEP_VISUAL_TAIL`, `SCREEN_SUBJECT`, `LIVE_CLIPPING`. Exceptions, as before: `ASSET_VARIANTS` (on; inert without the extended library) and `BROLL_OPENVERSE` (on; off in unit tests).

## Tests on the combined tree (`test_log_combined_tip.txt`)

- **Run:** OpenCode's clean venv, Windows, test DB on localhost:55432, tip `17b1ef7` (= patches 0001–0017).
- **Result: 344 passed, 0 failed** across 27 files:
  - all new suites (content_cuts, asset_library, openverse_broll, fit_pad, caption_cleanup, story_events, screen_subject, live ×3, transition API, burned captions, fonts, glyph fallback);
  - NVENC probe, export presets;
  - plus B-roll/music/template/reframe/reaction/retention/clip-boundary regressions.
- **Patch 0018** touches only `tools/phase2/auto_edit.py` (the harness, no product code), so it does not change those results.
