# OpenCode brief — SnipNova Lane B "render basics" (from Claude Code, technical lead, 2026-11-17)

You are OpenCode, the second coding agent on SnipNova (an open-source, local-first AI video clipper: long video in,
captioned 9:16 shorts out; FastAPI backend + Next.js frontend + Docker). Claude Code is the technical lead and integrates
your work. Muse/Sanji owns ALL frontend: do not edit anything under `frontend/`.

## Workspace (absolute rules)
* Work ONLY in `D:\SnipNova-Dev\opencode` (a git worktree, branch `lane-b/render-basics`, already created from commit `48dd1e9`).
* NEVER touch `D:\EditedTool\JarvisClipStudio` (the owner's installed app), its `.env`, databases, Docker volumes or user files.
* NEVER touch `D:\SnipNova-Dev\claude-code` (Claude's working tree) or `D:\SnipNova-Dev\baseline-wt`.
* No API keys exist in your worktree and you must not add any. No paid API calls. No new paid services.
* Commit on `lane-b/render-basics` only (format `type(scope): summary`, one logical change per commit, end each commit
  message with your own attribution line). Never merge, rebase onto other branches, or push.
* Python env: `cd D:\SnipNova-Dev\opencode\backend` then `uv sync` (creates your own `.venv`). Tests on Windows need two
  workarounds: `$env:NO_PROXY="localhost"` and a stub `fcntl.py` on `PYTHONPATH` (a file containing
  `LOCK_EX=2; LOCK_SH=1; LOCK_UN=8; LOCK_NB=4` and `def flock(*a,**k): return None` / `def lockf(*a,**k): return None`),
  kept OUTSIDE the repo. Run single files: `.venv\Scripts\python.exe -m pytest tests\unit\<file>.py -q --no-cov`.
  Several suites hang or fail on Windows on the untouched baseline too; compare against baseline before calling a failure yours.

## Allowed files (only these)
* `backend/src/media/ffmpeg.py` (NVENC probe only)
* `backend/tests/unit/fake_ffmpeg_shim.py`, `backend/tests/unit/test_nvenc_probe_realistic.py`
* `backend/src/clip_editor.py` (`EXPORT_PRESETS` and the export function it feeds)
* new tests `backend/tests/unit/test_export_profiles.py`
* new folder `tools/transitions-proof/`
* `CHANGELOG.md` (add your entries at the top of the `## 2026-11-17` section; do not edit others' lines)
## Prohibited (Claude owns or they are shared/hot)
`backend/src/auto_director/**`, `backend/src/media/edl_render.py`, `backend/src/media/pixabay_broll.py`,
`backend/src/services/**`, `backend/src/config.py`, `litellm_config.yaml`, `docker-compose*.yml`, `frontend/**`,
any profile JSON. If a task seems to need one of these, STOP and write the reason in your report instead.

## Task 1 — NVENC probe works on new FFmpeg (bug, highest priority)
`check_nvenc_realistic()` in `backend/src/media/ffmpeg.py` passes `-filter_complex_script <file>`. FFmpeg 8-era builds
(the one on this PC: `ffmpeg -version` shows N-125365) removed that option, so the probe fails ("Unrecognized option
'filter_complex_script'") and every render silently falls back to the CPU encoder. Production renders already pass the
graph inline with `-filter_complex <graph>`. Fix: pass the probe graph inline the same way (it is short). Update
`fake_ffmpeg_shim.py` and `test_nvenc_probe_realistic.py`, which currently identify the probe by the old option, so they
identify it another way (e.g. the `nvenc_probe.mp4` output name) — keep every behaviour they assert.
Evidence: tests pass; on this PC run `check_nvenc_available()` (or the probe) and report the result honestly — if this
laptop's RTX 4050 encodes, record it; if it fails for another reason, record that reason.

## Task 2 — 16:9 and 1:1 export profiles (missing feature)
`clip_editor.EXPORT_PRESETS` has only 9:16 (`tiktok`, `reels`, `shorts`, 1080x1920). Add `youtube_landscape`
(1920x1080) and `square` (1080x1080), following YouTube's published bitrates for 1080p (8 Mbps SDR at <=30 fps, 12 Mbps
above; AAC 384 kbps) and a sensible equivalent for square. The export of a 9:16 clip into a 16:9 or 1:1 frame must keep
the whole picture (fit inside, blurred or plain background fill), never stretch, and keep captions inside the frame.
Do not change the behaviour of the existing three presets. Tests in `test_export_profiles.py`.
Evidence: export one real clip (render any short 9:16 test clip yourself with ffmpeg, e.g. from
`D:\SnipNova-Dev\claude-code\test-footage\phase1\offline\investing101_v3_render.mp4` — READ ONLY, copy it into your
worktree's `test-footage\`, which is git-ignored) to all five presets; ffprobe each (resolution, duration, audio);
extract one frame per output and look at it.

## Task 3 — Transition proof reel (evidence for the existing catalogue; no product code change)
`backend/src/media/edl_render.py` defines `XFADE_TRANSITIONS` (58 FFmpeg xfade names); `backend/transitions/` holds 2 MP4
overlays. Build `tools/transitions-proof/make_reel.py` that renders, for every name, a 2-shot join (two distinct coloured,
labelled test cards, 1080x1920, 0.5 s transition) with FFmpeg's `xfade`, then: ffprobe each output; extract the frame at
the transition midpoint; compute a distinctness signature (e.g. downscaled grey midpoint frame) and flag pairs whose
signatures are near-identical. Output a contact sheet per family and a table: name, rendered OK, duration, distinct-from.
Report the HONEST count of working, visually distinct variants. Do not edit `edl_render.py`.

## Report (write to `D:\SnipNova-Dev\handoff\opencode\REPORT.md` and stop)
Per task: commits (hashes), files changed, what was built, exact test counts (and which failures also occur on the
baseline commit `48dd1e9`), evidence paths and measurements, what is NOT verified, known limits. Also export
`git format-patch 48dd1e9..HEAD -o D:\SnipNova-Dev\handoff\opencode\series`. Use the words PASS / FAIL / NOT RUN /
NOT VERIFIED literally. Never claim something works without having run it.
