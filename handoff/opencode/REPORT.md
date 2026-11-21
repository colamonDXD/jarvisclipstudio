# Lane B — render basics

**Agent:** OpenCode (DeepSeek V4.1 Flash Free)
**Branch:** `lane-b/render-basics`
**Base:** `48dd1e9`
**HEAD:** `7162804` (`test(tools): add the xfade transition proof reel`)
**Workspace:** `D:\SnipNova-Dev\opencode`
**Date:** 2026-11-17

**Overall status: PASS** for the three requested tasks, with two known test-expectation failures in out-of-scope test files (both documented below with one-line fixes) and the limits listed at the end.

No merge, rebase, or push was done. `frontend/` and every prohibited file were left untouched.

| Task | Status |
| --- | --- |
| 1. NVENC probe on FFmpeg 8-era builds | **PASS** |
| 2. 16:9 and 1:1 export profiles | **PASS** (implementation and real exports); one out-of-scope test expectation **FAIL** |
| 3. Transition proof reel | **PASS** |
| Full repository test suite | **NOT RUN** |
| Docker / CPU-only environment | **NOT RUN** |
| Frontend UI for the new presets | **NOT RUN** (frontend is out of scope) |
| Full-motion human review of every transition | **NOT VERIFIED** |

## Commits and files

| Commit | Subject |
| --- | --- |
| `52a9ee1` | `fix(media): pass the NVENC probe graph inline` |
| `c55d34a` | `feat(export): add 16:9 and 1:1 export profiles` |
| `7162804` | `test(tools): add the xfade transition proof reel` |

`git diff --stat 48dd1e9..HEAD` (9 files, +965/−32):

```text
 CHANGELOG.md                                     |   2 +
 backend/src/clip_editor.py                       |  71 ++-
 backend/src/media/ffmpeg.py                      |  22 +-
 backend/tests/unit/fake_ffmpeg_shim.py           |  68 ++-
 backend/tests/unit/test_export_profiles.py       | 162 ++++++
 backend/tests/unit/test_nvenc_probe_realistic.py |  22 +-
 tools/transitions-proof/.gitignore               |   3 +
 tools/transitions-proof/README.md                |  51 ++
 tools/transitions-proof/make_reel.py             | 596 +++++++++++++++++++++++
```

All touched files are in the allowed list. `backend/src/media/edl_render.py` was read but not edited.

---

## Task 1 — NVENC probe works on FFmpeg 8-era builds

**Status: PASS**

### Commit and files

- `52a9ee1` — `fix(media): pass the NVENC probe graph inline`
- `backend/src/media/ffmpeg.py`
- `backend/tests/unit/fake_ffmpeg_shim.py`
- `backend/tests/unit/test_nvenc_probe_realistic.py`
- `CHANGELOG.md`

### What was built

1. `check_nvenc_realistic()` now passes the probe graph inline as `-filter_complex <graph>`, exactly as production renders already do. The removed `-filter_complex_script <file>` option is no longer used.
2. Fixed a second real bug found while testing: the probe called `_validate_nvenc_probe_output(output_path)` **after** the `TemporaryDirectory` block had deleted the file. With a real `ffprobe`, validation always failed even when the NVENC encode succeeded. Validation now runs inside the `with` block, before cleanup.
3. The fake shim no longer identifies the probe by `-filter_complex_script`. It uses the unique output name `nvenc_probe.mp4` (`is_probe_call()`), and `render_calls()` excludes it. `tool_name()` normalises `.exe` so call matching still works on Windows.
4. On Windows, `CreateProcess` only resolves a bare name with `.exe`; the old shebang shim was never found and tests silently used the real `ffmpeg`. The shim now writes `ffmpeg.exe`/`ffprobe.exe` launchers using pip's vendored `distlib` when it is available, and remains a no-op if it is not. This makes the shim tests actually exercise the shim on Windows.

### Real-PC result (honest)

This PC has an **NVIDIA GeForce RTX 4050 Laptop GPU**, driver **616.92**, and **FFmpeg N-125365** (the 8-era build from the brief).

```text
realistic True
available True
error None
```

A direct NVENC encode also succeeded on this machine: `h264_nvenc`, 1080x1920, 60 frames, 1.90 s output, about 3.33x realtime. So the probe failure was not this GPU: it was the removed option plus the validate-after-cleanup ordering. Both are fixed.

Evidence:

- `D:\SnipNova-Dev\handoff\opencode\evidence\nvenc-probe-result.txt`
- `D:\SnipNova-Dev\handoff\opencode\evidence\nvenc-direct.log`

### Tests

- `tests/unit/test_nvenc_probe_realistic.py`: **10 / 10 PASS**
- `tests/unit/test_nvenc_probe.py` (legacy probe suite, run separately): **3 / 3 PASS**
- The three NVENC-related suites that failed on baseline now pass except for the two current test-expectation failures described below (one of which is in `test_prestage_nvenc.py`).

### Not verified / limits

- **NOT VERIFIED:** NVIDIA GPUs/drivers other than the RTX 4050 Laptop GPU above, and Linux/macOS behaviour.
- **NOT VERIFIED:** a complete product EDL render driven end to end through the worker with NVENC. The probe and a direct `h264_nvenc` encode were exercised; the export profile feature itself uses `libx264` and was exercised separately.
- **NOT RUN:** Docker or a CPU-only container.
- The Windows `.exe` shim launchers need `pip` available inside the venv. Without it the shim falls back to the previous Windows behaviour (the tests may reach the real `ffmpeg`); this did not affect the Linux-style code paths.

---

## Task 2 — 16:9 and 1:1 export profiles

**Status: PASS** for the feature and real exports. One pre-existing test in `test_export_presets.py` now **FAIL**s because it hard-codes three presets; see the failure section.

### Commit and files

- `c55d34a` — `feat(export): add 16:9 and 1:1 export profiles`
- `backend/src/clip_editor.py`
- `backend/tests/unit/test_export_profiles.py` (new)
- `CHANGELOG.md`

### What was built

Two new entries in `EXPORT_PRESETS`:

| Preset | Frame | Base video | High-fps video (>30) | Audio | Background |
| --- | --- | --- | --- | --- | --- |
| `youtube_landscape` | 1920x1080 | 8 Mbps | 12 Mbps | AAC 384 kbps | blurred fill |
| `square` | 1080x1080 | 8 Mbps | 12 Mbps | AAC 384 kbps | blurred fill |

`youtube_landscape` follows YouTube's published 1080p SDR row: 8 Mbps at ≤30 fps, 12 Mbps above, AAC 384 kbps. `square` is a 1080-class frame and uses the same row. The three existing vertical presets keep their geometry, rates and `pad`/black letterboxing path unchanged.

The new profiles use `background="blur"`:

- Foreground: `scale=<W>:<H>:force_original_aspect_ratio=decrease` then centred `overlay` — the whole picture is fitted, never stretched or cropped.
- Background: `scale=...:force_original_aspect_ratio=increase`, `crop=<W>:<H>`, `boxblur=20:2` — a blurred, cropped copy of the same frame fills the bands.
- Captions are part of the source picture and therefore stay inside the fitted frame.
- The existing presets still take the original `-vf ... pad` branch, byte-for-byte in behaviour.

Tests in `test_export_profiles.py` cover: the five-profile table, the YouTube rates at 24/30/60/unknown fps, square rates, unchanged legacy presets, the blurred-fill graph for both new profiles, fps capping at 120 fps, legacy letterboxing, and a real five-profile export with frame sampling.

### Real export evidence

Source: `test-footage\short-9x16.mp4`, 1080x1920, 30 fps, 8.000 s. All five presets exported with the real backend function and probed:

| Preset | Resolution | Duration | Size (bytes) | Video | Audio |
| --- | --- | --- | --- | --- | --- |
| `tiktok` | 1080x1920 | 8.000 s | 1,348,964 | h264 | aac |
| `reels` | 1080x1920 | 8.000 s | 1,348,964 | h264 | aac |
| `shorts` | 1080x1920 | 8.000 s | 1,408,246 | h264 | aac |
| `youtube_landscape` | 1920x1080 | 8.000 s | 1,437,534 | h264 | aac |
| `square` | 1080x1080 | 8.000 s | 1,065,481 | h264 | aac |

`tiktok` and `reels` are byte-identical here because the CRF encode stays below both bitrate caps; that is pre-existing behaviour and the presets were not changed.

A 3 s captioned 9:16 clip (`test-footage\caption-9x16.mp4`) was also exported to `tiktok`, `youtube_landscape` and `square`. Frames were extracted and inspected: the title text remains inside the fitted picture in both the wide and square outputs, and the side/top bands are a blurred copy of the picture, not black bars.

Evidence:

- `D:\SnipNova-Dev\handoff\opencode\evidence\exports\probe.json`
- `D:\SnipNova-Dev\handoff\opencode\evidence\exports\tiktok-1s.png`
- `D:\SnipNova-Dev\handoff\opencode\evidence\exports\youtube_landscape-1s.png`
- `D:\SnipNova-Dev\handoff\opencode\evidence\exports\square-1s.png`
- `D:\SnipNova-Dev\handoff\opencode\evidence\exports\youtube_landscape-caption-1s.png`
- `D:\SnipNova-Dev\handoff\opencode\evidence\exports\square-caption-1s.png`

### Tests

- `tests/unit/test_export_profiles.py`: **9 / 9 PASS** (8 test functions, 1 parametrized into two).
- `tests/unit/test_export_presets.py`: **7 / 8 PASS**; the failure is the old “exactly three presets” assertion (below).

### Not verified / limits

- **NOT RUN:** the frontend preset picker/UI. The brief forbids `frontend/` edits; the new presets are backend entries only.
- **NOT VERIFIED:** source aspect ratios other than 9:16. The blur/fit graph should be general, but only 9:16 sources were exported.
- **NOT VERIFIED:** Docker/CPU-only export paths and platforms other than Windows with this FFmpeg build.
- **NOT RUN:** a full app task → worker → download flow; exports were called directly through `export_with_preset`.
- The CHANGELOG entries have no PR link because no PR exists; other entries in the section use `#TBD` links.

---

## Task 3 — Transition proof reel

**Status: PASS**

### Commit and files

- `7162804` — `test(tools): add the xfade transition proof reel`
- `tools/transitions-proof/make_reel.py` (new)
- `tools/transitions-proof/README.md` (new)
- `tools/transitions-proof/.gitignore` (new)
- No product code changed; `backend/src/media/edl_render.py` was not edited.

### What was built

`make_reel.py` reads `XFADE_TRANSITIONS` from `edl_render.py` with `ast` (so it needs no app environment), then for every name:

- renders two distinct labelled test cards (blue “A” and orange “B”, 1080x1920, 1.2 s each) joined by a 0.5 s `xfade`, producing a 1.9 s clip;
- probes every output for geometry and duration;
- extracts the midpoint frame (frame 28 at 30 fps);
- computes a 32x32 grey midpoint signature and a three-frame (25/50/75%) 32x32 signature;
- flags near-identical pairs (mean absolute grey difference ≤ 1.0);
- writes one contact sheet per family;
- writes `table.md` with name, family, rendered OK, duration and distinct-from;
- writes `signatures.json` with signatures, thresholds, failures and groups;
- exits 1 if any transition fails to render or validate.

The output directory is git-ignored: `tools/transitions-proof/out/`.

### Results

```text
Rendered OK:        58/58
All outputs:        1080x1920, 1.900 s
Midpoint clusters:  2 near-identical pairs among 58 working variants
Distinct at midpoint: 56 / 58
Three-frame groups: 1 near-identical pair
Distinct across the transition: 57 / 58
```

Groups:

- Midpoint near-identical: `fade` / `fadegrays` (Δ 0.33) and `circlecrop` / `rectcrop` (Δ 0.22).
- Three-frame near-identical: `fade` / `fadegrays` (Δ 0.33) only.

`circlecrop` and `rectcrop` are visually different over the transition but both have a black midpoint frame (confirmed in the extracted frames and contact sheet), so midpoint-only signatures cannot separate them. With the three-frame signature they are distinct.

**Honest count:** 58/58 render and validate; 57/58 are distinct by the three-frame signature. At the midpoint alone, 56/58 are distinct. This is signature-based evidence, not a motion/quality judgement.

Evidence:

- `D:\SnipNova-Dev\handoff\opencode\evidence\transitions\table.md`
- `D:\SnipNova-Dev\handoff\opencode\evidence\transitions\signatures.json`
- `D:\SnipNova-Dev\handoff\opencode\evidence\transitions\run.log`
- `D:\SnipNova-Dev\handoff\opencode\evidence\transitions\` — 12 files named `contact-<family>.png`

The 12 contact sheets are fade-blend, pixel-blur-zoom, wipe-edge, slide, smooth, circle-radial, open-close, diagonal, slice, squeeze, wind and cover-reveal. All were opened and inspected.

### Not verified / limits

- **NOT VERIFIED:** full-motion human review of all 58 transitions. The evidence is one midpoint frame plus three sampled frames per clip, not a frame-by-frame or perceptual quality review.
- **NOT VERIFIED:** the same 58 names inside the real EDL/product renderer. The proof uses a standalone two-shot `xfade` join, intentionally without product code.
- **NOT RUN:** a GPU/CPU matrix; this run used this PC's FFmpeg.
- The distinctness threshold is fixed at 1.0 (mean absolute grey difference); a transition can render correctly and still be visually redundant with another.

---

## Test results

### Environment

- Windows, `backend/.venv`, Python from the worktree.
- `NO_PROXY=localhost`.
- A stub `fcntl.py` outside the repo on `PYTHONPATH`, as required by the brief.
- `--no-cov` for all runs.
- Real FFmpeg N-125365 and `ffprobe` on PATH.

### Focused suite (authoritative comparison)

Command run from `backend`:

```powershell
.venv\Scripts\python.exe -m pytest `
  tests\unit\test_nvenc_probe_realistic.py `
  tests\unit\test_export_profiles.py `
  tests\unit\test_export_presets.py `
  tests\unit\test_prestage_nvenc.py `
  tests\unit\test_edl_nvenc_escape_hatch.py `
  -q --no-cov --color=no
```

| Suite | Current branch | Baseline `48dd1e9` |
| --- | --- | --- |
| `test_nvenc_probe_realistic.py` | **10 / 10 PASS** | 1 / 10 PASS (9 FAIL) |
| `test_export_profiles.py` (new) | **9 / 9 PASS** | file absent |
| `test_export_presets.py` | 7 / 8 PASS (1 FAIL) | **8 / 8 PASS** |
| `test_prestage_nvenc.py` | 10 / 11 PASS (1 FAIL) | 1 / 11 PASS (10 FAIL) |
| `test_edl_nvenc_escape_hatch.py` | **14 / 14 PASS** | 11 / 14 PASS (3 FAIL) |
| **Total** | **50 / 52 PASS, 2 FAIL** | **21 / 43 PASS, 22 FAIL** |

Additionally, `test_nvenc_probe.py` (legacy probe suite, not in the 52 above): **3 / 3 PASS**.

The earlier, wider one-off sweep recorded in `pytest-current.txt` / `pytest-baseline.txt` (a different file set) agrees:

- Current: **78 passed, 2 failed** (80 executed).
- Baseline: **49 passed, 22 failed** (71 executed).
- The same two current failures appear in both runs.

Logs:

- `D:\SnipNova-Dev\handoff\opencode\pytest-focused-current.txt`
- `D:\SnipNova-Dev\handoff\opencode\pytest-focused-baseline.txt`
- `D:\SnipNova-Dev\handoff\opencode\pytest-current.txt`
- `D:\SnipNova-Dev\handoff\opencode\pytest-baseline.txt`

### Baseline failures resolved on this branch

All 22 baseline failures in the focused four-file set pass on this branch:

- 9 in `test_nvenc_probe_realistic.py`
- 10 in `test_prestage_nvenc.py`
- 3 in `test_edl_nvenc_escape_hatch.py`

Cause on baseline: the Windows shim was not actually used (bare-name `ffmpeg` was invisible to `CreateProcess`), and the probe passed `-filter_complex_script`, which FFmpeg N-125365 rejects. Both are fixed.

---

## The two current failures (out of scope)

Neither is a product regression. Both tests encode assumptions that the requested changes invalidate, and both files are outside the allowed edit list.

### 1. `test_export_presets.py::test_the_three_presets_encode_differently_at_30_fps` — FAIL

`backend/tests/unit/test_export_presets.py:36`

```python
rates = {preset_rates(name, 30) for name in EXPORT_PRESETS}
assert len(rates) == len(EXPORT_PRESETS) == 3
```

`EXPORT_PRESETS` now has five entries, and the two new presets intentionally share the 1080-class rate row, so `len(rates)` remains 3 while `len(EXPORT_PRESETS)` is 5. This test passed on baseline (three presets) and cannot pass unchanged after the requested feature.

Suggested one-line fix (keep the test's original intent):

```python
rates = {preset_rates(name, 30) for name in ("tiktok", "reels", "shorts")}
assert len(rates) == 3
```

(Or update the assertion to expect the new five-profile table; do not assert five distinct rates, because the new profiles share rows.)

### 2. `test_prestage_nvenc.py::test_prestage_cpu_leg_renders_real_source` — FAIL

`backend/tests/unit/test_prestage_nvenc.py:332`

```python
assert ffmpeg_module.check_nvenc_available() is False
```

The test was written for a sandbox with no GPU. On this PC the probe now correctly returns `True`, so the test's premise (“no GPU here”) is false. It passed on baseline only because the probe was broken. This is evidence the Task 1 fix works, not a product failure.

Suggested one-line fix:

```python
monkeypatch.setenv("FORCE_CPU_ENCODE", "1")
```

at the start of the test (the autouse fixture clears this variable first, so set it inside the test body), or skip when `check_nvenc_available()` is true.

---

## Patch series

Generated from `48dd1e9..HEAD`:

```powershell
git -C D:\SnipNova-Dev\opencode format-patch 48dd1e9..HEAD `
  -o D:\SnipNova-Dev\handoff\opencode\series
```

Files:

- `series\0001-fix-media-pass-the-NVENC-probe-graph-inline.patch`
- `series\0002-feat-export-add-16-9-and-1-1-export-profiles.patch`
- `series\0003-test-tools-add-the-xfade-transition-proof-reel.patch`

Verification: all three patches were applied in order to a clean detached worktree at `48dd1e9`; every `git apply --check` and `git apply` succeeded. The temporary worktree was removed afterwards. No commits exist after `7162804`, so the series matches `HEAD`.

The overall diff is 9 files, +965/−32 (including the new tool and tests).

### Housekeeping note

The worktree's existing git identity authored all three commits as `Claude Lane A <lane-a@local.invalid>`; each commit message ends with the OpenCode attribution line required by the brief. The lead may want to re-author when integrating.

---

## What is not verified overall

- **NOT RUN:** the full repository test suite, integration tests requiring Postgres/Redis, Docker Compose, or the worker/API stack.
- **NOT RUN:** any frontend build, lint, or UI check; `frontend/` was not touched. The new presets have no UI control yet.
- **NOT VERIFIED:** Linux/macOS or a CPU-only machine for Tasks 1 and 2.
- **NOT VERIFIED:** GPUs other than the RTX 4050 Laptop GPU / driver 616.92.
- **NOT VERIFIED:** a complete product render through the NVENC path end to end. The probe and a direct NVENC encode passed; the real export evidence used the `libx264` export function.
- **NOT VERIFIED:** source aspect ratios other than 9:16 for the new frame fitting, and clips other than the 8 s and 3 s samples.
- **NOT VERIFIED:** full-motion visual review of all 58 transitions; the reel's distinctness count is signature-based.
- **NOT VERIFIED:** the new presets' bitrate/quality on YouTube/Instagram in the real world.
- `CHANGELOG.md` entries were added at the top of `## 2026-11-17` without PR links, because no PR exists.

## Final status

- Task 1: **PASS** — probe and direct NVENC encode verified on this PC; real bug fixed; tests pass.
- Task 2: **PASS** — both profiles built, all five real exports probed, frames inspected, existing presets unchanged; one old test expectation **FAIL**s and is documented with a one-line fix.
- Task 3: **PASS** — 58/58 rendered, probed and signature-checked; 57/58 distinct by the three-frame signature, with 12 contact sheets and a table.
- Deliverable: **PASS** for the requested scope, with the two out-of-scope test failures above and the listed limits.
