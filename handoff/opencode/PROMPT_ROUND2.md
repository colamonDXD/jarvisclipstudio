# OpenCode — Round 2 (from Claude Code, lead)

Round 1 is reviewed and accepted. I re-ran your focused suite (53 pass, the same 2
fail) and fixed those 2 test pins myself in commit `b0965c8` on
`lane-b/render-basics` — do not redo them.

## Ground rules (unchanged, non-negotiable)

- Work ONLY in `D:\SnipNova-Dev\opencode`. Never open, edit or run anything in
  `D:\EditedTool\JarvisClipStudio`, any `JarvisClipStudio-BACKUP-*` folder, Docker
  volumes or any `.env`. You may READ (never write) source videos under
  `D:\SnipNova-Dev\claude-code\test-footage\`.
- No API keys, no paid APIs, no sign-ups, no model downloads, no Gemini, no Ultralytics.
- Do not merge, rebase or push. Do not touch `frontend/`.
- Never delete, skip, xfail or weaken an existing test.
- Commits: `type(scope): summary`, CHANGELOG entry for anything user-visible, and
  the same attribution line you used in Round 1.
- Create a new branch first:
  `git -C D:\SnipNova-Dev\opencode switch -c lane-b/round2 lane-b/render-basics`
- Windows test setup as before: `NO_PROXY=localhost`, the stub `fcntl.py` on
  `PYTHONPATH`, `--no-cov`.

Allowed files (create/edit only these):
- `backend/src/media/transition_library.py` (new)
- `backend/src/media/burned_captions.py` (new)
- `backend/tests/unit/test_transition_library.py` (new)
- `backend/tests/unit/test_burned_captions.py` (new)
- `tools/transitions-proof/*`
- `tools/burned-captions-proof/*` (new)
- `CHANGELOG.md`

Do NOT edit `edl_render.py`, `video_utils.py`, `reframe_plan.py`, `reaction_layout.py`,
`clip_editor.py` or anything under `services/`. I wire your modules in myself.

---

## Task A — Transition library (new families, honest distinct count)

Goal: grow the transition set well past the 58 built-in `xfade` names with
genuinely different looks, measured by your three-frame signature, not by renaming.

1. `transition_library.py`: a registry `TRANSITIONS: dict[str, TransitionSpec]`
   (name, family, description, builder). Each builder returns an FFmpeg filter-graph
   fragment that takes `[a][b]` (two equal-size clips) and outputs `[out]`, given
   `duration`, `offset`, `width`, `height`, `fps`. CPU only, FFmpeg filters only, no
   external assets.
2. Families to add (each with real variants, not cosmetic duplicates):
   - `xfade=transition=custom:expr=...`: soft-edge iris, diamond, checkerboard,
     venetian blinds (H/V, several slat counts), zig-zag and sine-wave wipes, spiral,
     clock wipes from off-centre points, random-block dissolve, soft barn doors.
   - Motion (non-xfade graphs): whip-pan with directional blur (4 directions), zoom
     punch in/out, spin+zoom, shake cut.
   - Glitch: RGB split (`rgbashift`), slice offset, noise burst.
   - Light: white flash, colour flash, light-leak sweep (generated gradient,
     additive blend), blur dissolve (`gblur` ramp), luma fade.
3. Extend `tools/transitions-proof/make_reel.py` (or add a sibling script) to render
   EVERY registry entry the same way as Round 1 (blue "A" / orange "B" cards,
   1080x1920, 0.5 s), probe them, and compute the three-frame signature against ALL
   others, including the 58 xfade ones. Report: total rendered OK, total distinct
   (Δ > 1.0 vs every other), near-duplicate groups, and render time per transition.
4. Tests: unique names; every builder renders at a tiny size (108x192, 10 fps)
   through real ffmpeg; output duration = 2×card − duration; the first frame is
   A-coloured and the last frame is B-coloured.
5. Honest count only. The brief's target is 200+ distinct; if you reach fewer, say
   the number you actually measured.

## Task B — Burned-in subtitle detector

Problem seen in Phase 2 renders: some sources already have subtitles burned into
the video. Our 9:16 crop slices them mid-word, and our own captions then sit on
top of them.

1. `burned_captions.py`:
   `detect_burned_caption_band(video_path, times, width, height) -> Optional[CaptionBand]`
   with `CaptionBand(y, h, confidence, frames_with_text, frames_checked)` in
   source pixels. OpenCV + NumPy only (no OCR model, no downloads).
   - It must fire on text that sits in a stable horizontal band while its content
     changes between samples (subtitles).
   - It must NOT fire on static text or logos that never change (watermarks,
     "Follow us on" panels, channel names), on frames with no text, or on busy
     textures.
   - Also report a top band if the subtitles are at the top.
2. Tests with synthetic videos made by ffmpeg `drawtext` (use a font file from
   `backend/fonts/`): changing bottom subtitles are detected with the right y range;
   changing top subtitles are detected at the top; a static watermark is not
   detected; no text is not detected; a moving textured background is not detected.
3. Real evidence (`tools/burned-captions-proof/`): run on these sources (read-only)
   and save one debug frame each with the band outlined:
   - `D:\SnipNova-Dev\claude-code\test-footage\phase2_src\TkHlhgTutFs.mp4` — HAS
     burned subtitles (bottom)
   - `D:\SnipNova-Dev\claude-code\test-footage\phase2_src\dwZK6iQlyEw.mp4` — HAS
     burned subtitles (bottom)
   - `D:\SnipNova-Dev\claude-code\test-footage\phase2_src\1oXjtK5Nsjw.mp4` — static
     text panels only, must be NOT detected
   - `D:\SnipNova-Dev\claude-code\test-footage\phase2_src\D3cAyFmhi_M.mp4` — vlog,
     expected NOT detected (say what you find)
   Sample about 12 times spread across each video. Report the band, the confidence,
   and the run time per video.

## Deliverable

- Report: `D:\SnipNova-Dev\handoff\opencode-r2\REPORT.md` (new folder; do not
  overwrite `handoff\opencode`). Same honest PASS / FAIL / NOT VERIFIED style as
  Round 1, with your test results against the base `b0965c8`.
- Evidence under `D:\SnipNova-Dev\handoff\opencode-r2\evidence\`.
- Patch series: `git format-patch b0965c8..HEAD -o D:\SnipNova-Dev\handoff\opencode-r2\series`.
- Do Task A first, commit, then Task B, commit. Stop and report when both are done.
