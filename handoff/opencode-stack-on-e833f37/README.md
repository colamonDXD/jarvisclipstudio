# OpenCode stack replayed onto e833f37 (for V12.1 integration)

**Why:** R2's base `b0965c8` is on OpenCode's line (Phase 1 `48dd1e9` + the NVENC series), not on `996d074` → `e833f37`. Applied with `git am` on `e833f37`, the series fail (CHANGELOG conflicts, plus R2 expects the NVENC series' files).

**What:** the 9 OpenCode commits cherry-picked in order onto `e833f37` (see `commits.txt`):
- NVENC series: 52a9ee1, c55d34a, 7162804, b0965c8;
- R2: 7fa296e, 92da2cc;
- R3: ac2ecbd, e632d3e, 82a586c (THEBOLDFONT drop).

**Conflicts:** only `CHANGELOG.md` (one-line entries at the top on both sides), resolved by keeping both sides' lines. One `## 2026-11-18` heading remains. No code conflict.

**Tests on the stacked tree** (tip shown in `commits.txt`; Windows; test DB localhost:55432; OpenCode's venv):
- burned_captions, transition_library_api, font_manifest, caption_glyph_fallback, nvenc_probe_realistic, export_presets, plus content_cuts, screen_subject, reframe_consistent, asset_library: **75 passed** (`test_result.txt`).
- Note: `test_nvenc_probe_realistic` fails 7/10 under MY venv on OpenCode's own unchanged tree too, and passes 10/10 under OpenCode's venv. That's an environment difference, not the stack.

**If your tree already has the NVENC series:** `git am --skip` patches 0001–0004 when they report as already applied; 0005–0009 (R2 + R3) then apply.

## NVENC dedupe (Sanji's 17:07 ask)

**ALREADY ON YOUR TREE, so skip these** (the OpenCode NVENC series + my addendum, which you integrated in the Phase 2 round):

| Patch | Original commit | Content vs the version you have |
|---|---|---|
| 0001 fix(media): pass the NVENC probe graph inline | 52a9ee1 | Identical code. Only its `CHANGELOG.md` hunk sits at a different spot (the entry is the same one line). |
| 0002 feat(export): add 16:9 and 1:1 export profiles | c55d34a | Identical code; same CHANGELOG note as above. |
| 0003 test(tools): add the xfade transition proof reel | 7162804 | Identical. |
| 0004 test(export): scope two pins to what they were written for | b0965c8 (my addendum) | Identical. |

**NEW, so apply these:** 0005–0009 (R2: 7fa296e, 92da2cc; R3: ac2ecbd, e632d3e, 82a586c). The only rebase change in these is CHANGELOG placement (both sides' lines kept). The code is byte-identical to the originals.
