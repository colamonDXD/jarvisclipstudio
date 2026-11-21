# OpenCode Round 3 brief (from Claude Code, lead)

Workspace: `D:\SnipNova-Dev\opencode`. Branch: create `lane-b/round3` from `lane-b/round2` (tip `92da2cc`).
Report to `D:\SnipNova-Dev\handoff\opencode-r3\` exactly like Round 2:
- `REPORT.md` with literal PASS / FAIL / NOT RUN / NOT VERIFIED;
- `series\` from `git format-patch`;
- `evidence\`.

## Hard rules (unchanged)
- No merge, rebase or push. Never touch `.env` files, `D:\EditedTool\JarvisClipStudio`, or any folder outside `D:\SnipNova-Dev\opencode` and the handoff folder.
- **Do not edit:**
  - `backend/src/media/edl_render.py`, `video_utils.py`, `reframe_plan.py`, `reaction_layout.py`, `pixabay_broll.py`;
  - anything under `backend/src/services/`;
  - `backend/src/media/asset_library.py` (the lead is working in these).
- No paid APIs, no sign-ups, no API keys. CPU only.
- Never delete, skip, xfail or weaken an existing test. Every new behaviour gets an off switch.
- Commit messages: `type(scope): summary`. CHANGELOG lines are one line each and end with ` ([#TBD](https://github.com/FujiwaraChoki/supoclip/pulls))`.

## Task A: transition catalogue API + previews

Your `transition_library.py` (248 entries) is not reachable by users yet. Make it browsable. Do not wire it into rendering; that's the lead's job.

1. `GET /transitions/library` (new route in `backend/src/api/routes/media.py`, next to `GET /transitions`):
   - returns `[{name, family, description, duration_default, preview_url}]`, sorted by family, then name;
   - supports optional `?family=`;
   - is public, like `GET /transitions`.
2. `GET /transitions/library/{name}/preview.mp4`:
   - a 1.2 s, 360x640 preview of the transition between two built-in colour/gradient cards (no external assets);
   - rendered on first request into `{TEMP_DIR}/transition_previews/<name>.mp4`, then served from that cache;
   - unknown name → 404; names validated against the registry (no path tricks).
3. Off switch: `TRANSITION_LIBRARY_API=off` makes both routes return 404.
4. Tests:
   - list shape, family filter and 404s;
   - a preview renders and is cached (the second call does not re-run ffmpeg; monkeypatch to count calls);
   - a path-traversal name gives 404.
   Use the existing test client fixtures in `backend/tests/conftest.py`.

## Task B: caption font pack (OFL / Apache only) with licence manifest

`backend/fonts/` has 25 fonts. Add these display fonts popular for short-form captions, **only from the official `google/fonts` GitHub repo**, each under **OFL-1.1 or Apache-2.0**:
- Luckiest Guy, Lilita One, Titan One, Bungee, Permanent Marker, Paytone One, Passion One, Rubik Mono One, Chewy, Fredoka (Bold).

1. A fetch script `tools/assets/fetch_fonts.py`:
   - downloads each TTF plus its licence file from `raw.githubusercontent.com/google/fonts/main/...`;
   - verifies the licence file is OFL/Apache;
   - writes `backend/fonts/FONT_MANIFEST.json`: file, sha256, family, licence, source URL.
2. Add manifest entries for the 25 existing fonts too. Where the licence is not provable from `google/fonts`, record `"license": "UNVERIFIED"` and list it in REPORT.md. `THEBOLDFONT.ttf` especially: do not guess.
3. `tools/assets/check_fonts.py`: exit 1 if any font in `backend/fonts/` has no entry, a sha mismatch, or a licence outside {OFL-1.1, Apache-2.0}. Print every problem.
4. Tests:
   - `GET /fonts` lists the new fonts;
   - the checker passes on a clean temp folder and fails on a missing entry, a wrong licence and an altered file.
5. Render evidence:
   - a 1080x1920 PNG contact sheet showing the word "SNIPNOVA" in each new font (ffmpeg drawtext);
   - saved as `evidence/fonts_sheet.png`.

## Done means
- Both tasks committed (two commits) on `lane-b/round3`, with the focused tests passing.
- A REPORT.md with exact test counts and timings, the list of UNVERIFIED fonts, and the patch series.
