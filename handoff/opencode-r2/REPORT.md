# Lane B Round 2 - transition library and burned-in captions

**Agent:** OpenCode (DeepSeek V4.1 Flash Free)
**Branch:** `lane-b/round2`
**Base:** `b0965c8` (`test(export): scope two pins to what they were written for`)
**HEAD:** `92da2cc` (`feat(media): detect changing burned-in caption bands`)
**Workspace:** `D:\SnipNova-Dev\opencode`
**Date:** 2026-11-18

**Overall status: PASS** for both requested tasks. The 200+ distinct target is
met by the full transition set (`255 of 306`) and by the new library alone
(`203 of 248`). Two modules and their proof tools/tests are committed; the
renderer was not touched and no merge, rebase or push was done.

| Task | Status |
| --- | --- |
| Task A - extended CPU-only transition library | **PASS** |
| Task A - 200+ genuinely distinct transitions | **PASS** (255/306 total; 203/248 library-only) |
| Task A - 251-test contract matrix | **PASS** (251 passed in 1278.18 s) |
| Task B - burned-in caption detector | **PASS** |
| Task B - synthetic tests | **PASS** (8 passed in 35.90 s) |
| Task B - real-footage evidence, four sources | **PASS** (4/4 match expectation) |
| Focused five-file suite (unchanged since `b0965c8`) | **PASS** (52 passed in 147.72 s) |
| Full repository test suite | **NOT RUN** |
| Docker / CPU-only container | **NOT RUN** |
| Renderer wiring / end-to-end EDL render | **NOT VERIFIED** (out of scope; the lead wires the modules) |
| Human visual review of every transition | **NOT VERIFIED** (contact sheets supplied) |
| Detection on other languages, fonts and colours | **NOT VERIFIED** |

No existing test was deleted, skipped, xfailed or weakened. `frontend/`,
`edl_render.py`, `video_utils.py`, `reframe_plan.py`, `reaction_layout.py`,
`clip_editor.py`, everything under `services/` and the `.env` files were left
untouched. `CHANGELOG.md` was not modified; both modules are libraries that no
render path references yet (the brief says the lead wires them), so there is no
user-visible change to announce. See "CHANGELOG decision" below.

## Commits and files

| Commit | Subject |
| --- | --- |
| `7fa296e` | `feat(media): add an extended CPU-only transition library` |
| `92da2cc` | `feat(media): detect changing burned-in caption bands` |

`git diff --stat b0965c8..HEAD` (8 files, +3024/-0):

```text
 backend/src/media/burned_captions.py          |  483 +++++++++
 backend/src/media/transition_library.py       | 1338 +++++++++++++++++++++++++
 backend/tests/unit/test_burned_captions.py    |  213 ++++
 backend/tests/unit/test_transition_library.py |  232 +++++
 tools/burned-captions-proof/.gitignore        |    2 +
 tools/burned-captions-proof/run_proof.py      |  200 ++++
 tools/transitions-proof/.gitignore            |    1 +
 tools/transitions-proof/make_library_reel.py  |  555 ++++++++++
 8 files changed, 3024 insertions(+)
```

All touched files are in the allowed list.

---

## Task A - extended transition library

**Status: PASS**

### What was built

`backend/src/media/transition_library.py` is a registry of **248 transition
builders in 33 families**. Every entry is a `TransitionSpec(name, family,
description, builder)`. A builder receives `duration`, `offset`, `width`,
`height` and `fps`, consumes two equal-size labelled clips `[a]`/`[b]`, and
returns one FFmpeg filter-graph fragment that ends in `[out]`. The module
imports only `math`, `dataclasses` and `typing`: no app imports, no external
assets, no GPU, no network.

Two implementation styles are used:

- `xfade=transition=custom` with a per-pixel expression for the geometric
  reveals (soft iris, diamond, checkerboard, venetian blinds, zig-zag, wave,
  spiral, clock, random blocks, barn doors, frame, star, multi-iris, shutter,
  corner, pinwheel, polygon, gradient, grid, mosaic, concentric ripples,
  diagonal blinds).
- Ordinary filter graphs (`overlay`, `zoompan`, `rotate`, `rgbashift`,
  `noise`, `gblur`, `blend`, `eq`, `pixelize`, `hue`, `colorchannelmixer`)
  for motion, glitch, light, blend, strobe, pixel, mirror, rotate, hue,
  channel and grade effects.

Family counts:

```text
barn 4, blend 26, blinds 10, blocks 6, channel 4, checker 6, clock 10,
corner 6, diagonal-blinds 6, diamond 6, frame 4, glitch 10, grade 11,
gradient 10, grid 6, hue 6, iris 8, light 13, mirror 6, mosaic 6,
motion 10, multi-iris 6, pinwheel 4, pixel 8, polygon 6, ripple 4,
rotate 6, shutter 4, spiral 6, star 4, strobe 8, wave 12, zigzag 6
```

### Proof method

`tools/transitions-proof/make_library_reel.py` renders every registry entry
**and all 58 built-in `xfade` names** the same way Round 1 did:

- 1080x1920, 0.5 s transition, two labelled cards (blue A, orange B),
- a real `libx264` encode, then `ffprobe` for duration/geometry,
- a 32x32 grey midpoint signature and a three-frame (25/50/75%) signature,
- mean absolute signature distance to every other clip; a clip is distinct
  only when that distance is **> 1.0** (0..255 grey) against every other clip.

`--reuse` reuses signatures, frames and clips from an earlier run, so the
final incremental pass only re-rendered the new entries.

### Proof result (honest)

```text
Rendered OK:   306/306
Distinct:      255 of 306 (delta > 1.00 vs every other three-frame signature)
Duplicates:    38 clip(s) in 13 near-identical group(s)
  builtin  rendered  58  distinct  52
  library  rendered 248  distinct 203
```

- **Full set:** 255 of 306 distinct (58 built-ins + 248 library entries), so
  the 200+ target is met by 55 clips.
- **Library alone:** 203 of 248 distinct, so the new library also clears 200
  on its own.
- The 38 non-distinct clips are reported, not hidden.

Near-identical groups:

```text
- blend_hardlight, blend_pinlight
- block_glitch, shake_cut_soft, slice_offset_fine, slice_offset_h, slice_offset_v
- blur_dissolve_soft, fade, fadegrays, grade_mono, hue_135, hue_180, hue_270,
  hue_330, hue_45, hue_90, pixel_10, pixel_14, pixel_24, pixel_36, pixel_48,
  pixel_6
- coverdown, whip_down
- coverleft, whip_left
- coverright, whip_right
- coverup, whip_up
- iris_soft_bl, iris_soft_br, iris_soft_center, iris_soft_low, iris_soft_tall,
  iris_soft_tl, iris_soft_tr, iris_soft_wide
- noise_burst_hard, noise_burst_soft
- rgb_split_lr, rgb_split_soft, rgb_split_tb
- strobe_17, strobe_29, strobe_47
- strobe_23, strobe_6
- strobe_37, strobe_9
```

Why they are near-identical under this measure, and why they are still in the
registry:

- `hue_*`, `pixel_*`, `grade_mono`, `fade`, `fadegrays` and
  `blur_dissolve_soft` collapse into one group because the signature is
  **luminance-only** on two flat colour cards. Hue rotation changes colour, not
  much luma; pixelating a flat card changes nothing. They are real colour
  options for real footage, but this proof does not count them as distinct.
- The eight `iris_soft_*` variants differ only in the soft window's position,
  and on flat cards the wide feathered edge moves too little luma to clear the
  threshold.
- `cover*`/`whip_*` pairs, `slice_offset_*`/`shake_cut_soft`/`block_glitch`,
  `rgb_split_*`, `noise_burst_*` and the strobe aliases are motion/glitch
  effects whose sampled 25/50/75% frames land close together on flat cards.
- `blend_hardlight` and the added `blend_pinlight` produce the same luma on
  this blue/orange pair.

Render time (1080x1920, 0.5 s join): the 224 earlier library entries took
2.1-286.9 s each (12,633 s total, which is why the incremental pass reused
them); the 24 new effect entries took 1.9-20.9 s each (190 s total).

### Tests

`backend/tests/unit/test_transition_library.py`:

- registry names are unique and every entry has a family, description and
  callable builder,
- the requested families are present,
- every builder is deterministic,
- every entry is rendered for real at 108x192 / 10 fps through `ffmpeg`:
  output duration is `2*card - duration` (1.5 s), the frame count is 15, the
  first frame's centre pixel is the A card and the last is the B card.

Result: **251 passed in 1278.18 s (21:18)**. This is 3 contract tests plus 248
per-entry renders.

### Task A limits

- The distinct count is measured on two flat colour cards with the brief's
  three-frame 32x32 grey signature. Real footage may distinguish some clips
  this signature calls near-identical (hue, pixel, colour grades), and may make
  some motion clips look more alike. The number reported is the measured one.
- No human watched all 306 clips; the proof writes 45 per-family contact sheets
  for review.
- The library is not referenced by the renderer. No EDL render consumes it yet,
  so its output inside a real timeline is **NOT VERIFIED**.

---

## Task B - burned-in caption detector

**Status: PASS**

### What was built

`backend/src/media/burned_captions.py` exposes:

```python
detect_burned_caption_band(video_path, times=None, width=None, height=None,
                           sample_count=12) -> Optional[CaptionBand]
```

`CaptionBand` carries `y`, `h` (source pixels), `confidence`,
`frames_with_text` and `frames_checked`. OpenCV + NumPy only; no OCR model, no
downloads, no GPU.

Per sampled frame:

1. The frame is resized to 480 px wide (grey/edges/stroke masks are all
   computed there).
2. A sliding horizontal strip (heights 6, 8, 10, 12, 14, 17, 20, 24, 28, 33,
   40, 48 px) is scored. A strip is kept only when it:
   - spans 10-96% of the frame width,
   - has edge density 8-45%,
   - has at least 1.4x the edge density of equal-size strips just above and
     below it,
   - contains at least 3 glyph-sized components spread over at least 25% of
     its width,
   - has at least 1.8% of its pixels in the outlined-bright-stroke mask
     (white glyph core with a dark edge/shadow).
3. Overlapping strips on one frame are reduced to the three best
   non-overlapping candidates, so one frame cannot outvote the others.
4. Candidates are clustered by vertical centre (tolerance 5.5% of frame
   height). The largest cluster must appear in at least 35% of the decoded
   samples (minimum 3).
5. For the winning cluster, the band is the union of the frame-level strips.
   One fixed rectangle per frame is cropped from the stroke mask, resized to
   256x32, and every pair is compared by Jaccard distance of the masks. At
   least 15% of the pairs must differ by 0.30 or more; otherwise the text is
   static and the detector returns `None`.
6. Confidence is `0.55 * frame_share + 0.45 * change_score`, where
   `change_score = min(1, p75_distance / (2 * 0.30))`.

This is deliberately a *changing* band detector. A watermark, channel name,
"follow us" panel or logo that never changes fails at step 5 even when it looks
exactly like a subtitle per frame.

### Synthetic tests

`backend/tests/unit/test_burned_captions.py` builds real H.264 fixtures with
ffmpeg `drawtext` (font: `backend/fonts/Anton-Regular.ttf`) and samples 12
times over 6 s:

| Fixture | Content | Expected | Result |
| --- | --- | --- | --- |
| `bottom` | 12 changing words in a bottom band | band in the lower quarter | PASS: `CaptionBand(y=189, h=31, confidence=1.0, 12/12)` |
| `top` | the same words in a top band | band at the top edge | PASS: `CaptionBand(y=7, h=33, confidence=1.0, 12/12)` |
| `static_both` | static top and bottom channel text | `None` | PASS |
| `static_bottom` | static "FOLLOW US" only | `None` | PASS |
| `no_text` | moving `testsrc2`, no text | `None` | PASS |
| `texture` | `testsrc2` plus `noise=alls=45:allf=t` | `None` | PASS |
| `moving_static` | moving video plus a static tag | `None` | PASS |
| blank frame | black still frame | no candidates | PASS |

Result: **8 passed in 35.90 s**.

### Real-footage evidence

`tools/burned-captions-proof/run_proof.py` samples 12 frames evenly across each
read-only source, times the detector, and saves one debug frame with every
candidate box (green) and the accepted band (red).

| Source | Expected | Detected | Band (source px) | Confidence | Frames | Seconds |
| --- | --- | --- | --- | --- | --- | --- |
| `TkHlhgTutFs.mp4` (854x480) | has burned subtitles | **YES** | `y=217, h=36` (0.452-0.527 H) | 0.908 | 10/12 | 13.54 |
| `dwZK6iQlyEw.mp4` (854x480) | has burned subtitles | **YES** | `y=439, h=23` (0.915-0.963 H) | 0.908 | 10/12 | 10.27 |
| `1oXjtK5Nsjw.mp4` (640x360) | static panels only | **NO** | - | - | - | 1.70 |
| `D3cAyFmhi_M.mp4` (854x480) | vlog, no subtitles | **NO** | - | - | - | 10.34 |

All four match expectation.

Notes from the evidence:

- The first source's subtitles are **mid-frame** (about 45-53% of the height),
  not at the bottom edge, even though the brief called them bottom. The
  detector finds them and reports the real position.
- The second source's text sits at the bottom edge (91-96% of the height); the
  strip scan does not reject bands that touch the frame border, because real
  burned subtitles often do.
- Both true sources cover 10 of 12 samples. One sample (about 126.5 s in the
  first source) has no subtitle on screen at all; another is below the cluster
  threshold. The 35% rule tolerates the gap.
- On the second source's debug frame the strip scan also proposes green boxes
  on the speaker's striped shirt. They are per-frame false candidates that do
  not win the cluster and do not change the band; the detector is not
  per-frame perfect, only stable across samples.

### Task B limits

- The detector is tuned for white/outlined subtitles, the common look in these
  sources. Other colours, thin fonts, fully transparent backgrounds and
  non-Latin scripts are **NOT VERIFIED**.
- It uses no OCR, so it cannot prove the changing text is a subtitle rather
  than another changing on-screen label; the contract is "stable band + changing
  glyphs".
- Synthetic fixtures are 320x240; real evidence is 854x480 and 640x360.
- The module is not wired into the renderer; how far it moves our captions in a
  real 9:16 crop is **NOT VERIFIED**.

---

## Test results against base `b0965c8`

### Environment

- Windows, `backend/.venv`, Python 3.11.17.
- `NO_PROXY=localhost`.
- Stub `fcntl.py` outside the repo on `PYTHONPATH`, as required.
- `--no-cov` for every run.
- FFmpeg/FFprobe `N-125365-g9a01c1cb6a-20260630`.

### Base commit

`b0965c8` is the parent of this branch's two commits. At that commit the two
new test files do not exist and none of the touched files are imported by the
existing focused suite. The same focused five-file suite was run at HEAD
(identical code in those files) to confirm the base is still green:

| Check | Result |
| --- | --- |
| Focused five-file suite (`test_nvenc_probe_realistic`, `test_export_profiles`, `test_export_presets`, `test_prestage_nvenc`, `test_edl_nvenc_escape_hatch`) | **52 passed in 147.72 s (0:02:27)** |
| Failures / errors / skips | 0 / 0 / 0 |

The branch adds only new files and a `.gitignore` line, so no code imported by
these five files differs between `b0965c8` and `HEAD`; the run above is the
same test code against the same implementation as the base.

For context, Round 1's pre-fix run at `48dd1e9` recorded **50/52 PASS, 2 FAIL**
in the same focused files (the two test-expectation pins). The lead fixed both
pins in `b0965c8`, which is why this run is expected to be 52/52. Round 1's
wider one-off sweep (`handoff/opencode/pytest-current.txt`) was 78 passed / 2
failed before those fixes.

### This branch

| Suite | Result |
| --- | --- |
| `tests/unit/test_transition_library.py` (new) | **251 passed** (1278.18 s) |
| `tests/unit/test_burned_captions.py` (new) | **8 passed** (35.90 s) |
| Focused five-file suite | **52 passed** (147.72 s) |
| Full repository suite | **NOT RUN** |

The 21-minute Task A matrix is real `ffmpeg` rendering of all 248 entries at
108x192; it is not a stubbed unit test.

Logs:

- `evidence/transitions/pytest.txt`
- `evidence/burned-captions/pytest.txt`
- `evidence/pytest-focused.txt`

---

## CHANGELOG decision

`CHANGELOG.md` was not changed. The brief allows it but the ground rules ask for
an entry only for user-visible behaviour, and explicitly exempt internal-only
changes. Neither module is referenced by `edl_render.py`, the worker or any
service: the lead wires them in a later step. Adding a release note now would
announce transitions and caption handling that a user cannot get yet. When the
renderer starts using them, the user-visible entry should describe the new
transitions and the caption-band avoidance.

---

## Reproduction

From `D:\SnipNova-Dev\opencode`:

```powershell
# Task A proof (306 clips; reuses prior signatures where possible)
backend\.venv\Scripts\python.exe tools\transitions-proof\make_library_reel.py `
  --out tools\transitions-proof\library-out-r2b `
  --reuse tools\transitions-proof\library-out-r2b-reuse\library.json

# Task A tests (about 21 minutes)
cd backend
$env:NO_PROXY="localhost"
$env:PYTHONPATH="$env:TEMP\opencode\win_stubs"
.venv\Scripts\python.exe -m pytest tests\unit\test_transition_library.py -q --no-cov

# Task B tests (about 36 seconds)
.venv\Scripts\python.exe -m pytest tests\unit\test_burned_captions.py -q --no-cov

# Task B real-footage proof
cd ..
backend\.venv\Scripts\python.exe tools\burned-captions-proof\run_proof.py

# Focused five-file suite (about 2.5 minutes)
cd backend
.venv\Scripts\python.exe -m pytest tests\unit\test_nvenc_probe_realistic.py `
  tests\unit\test_export_profiles.py tests\unit\test_export_presets.py `
  tests\unit\test_prestage_nvenc.py tests\unit\test_edl_nvenc_escape_hatch.py `
  -q --no-cov
```

The `library-out-r2b-reuse` folder is a hardlink merge of the two earlier proof
runs so the final incremental pass would not re-render the slow custom
expressions; it is ignored by `tools/transitions-proof/.gitignore`
(`library-out*/`), as are all proof outputs.

## Evidence index

`D:\SnipNova-Dev\handoff\opencode-r2\`

- `REPORT.md` - this file.
- `series\` - `git format-patch b0965c8..HEAD` (2 patches).
- `evidence\environment.txt` - toolchain and commit.
- `evidence\pytest-focused.txt` - focused suite log.
- `evidence\transitions\summary.txt` - proof summary.
- `evidence\transitions\table.md` - every clip, render time, near-identical
  groups.
- `evidence\transitions\library.json` - signatures, distances, groups.
- `evidence\transitions\contact-*.png` - 45 per-family contact sheets.
- `evidence\transitions\render.log` - full render log.
- `evidence\transitions\pytest.txt` - 251-test matrix log.
- `evidence\burned-captions\results.json` - band, confidence, timing per
  source.
- `evidence\burned-captions\TkHlhgTutFs.png`,
  `dwZK6iQlyEw.png`, `1oXjtK5Nsjw.png`, `D3cAyFmhi_M.png` - outlined debug
  frames.
- `evidence\burned-captions\run_proof.log` - proof output.
- `evidence\burned-captions\pytest.txt` - 8-test log.
