# REACTION-GATE FIX — lane report (§9 format)

**Branch:** `fix/reaction-gate-gameplay` in `~/workspace/jcs-reactorgate`, base `next-milestone` @ `4a6b54ce`. 4 commits: `dd7be86d` (source-area evidence fix + seam routing + CHANGELOG), `c669843e` (gate tests), `aef4f863` (stability-bar fix), `3b5fc717` (wander test). Diff stat vs base: 4 files, +207/−13 (`reaction_layout.py` +114/−13 region, `reframe_plan.py` 10 lines, `test_reaction_layout.py` +95, `CHANGELOG.md` +1). `jcs-next` untouched @ `4a6b54ce`, tree clean; tag `v6-candidate-90f1cb4` intact @ `90f1cb41`.

## 1. Premise findings

- `find_reactor_face` (reaction_layout.py) gates: ≥8 samples; per corner (right, then left) a face with centre x >76%/<24% of width, centre y <25% of height, area 0.001–0.025 of frame; exactly 1 corner face per counted sample; corner support ≥75% of samples AND `other_count` (samples with any non-corner face centred below 30% of height) ≥30%; then stability (centre std x ≤1.5% width, y ≤2.5% height, width std ≤25% of median).
- **What `other_count` protects against:** a lone stable corner face with nothing else happening — a static overlay/logo face, a PiP signer, or a single talking head near a corner being misread as a reactor inset and hijacking the layout from the podcast/call/content layouts (module docstring: "Otherwise leave the existing podcast, call, and content layouts in charge").
- **Call sites:** `reframe_plan.py` seam (~1257) works from a CACHED analysis dict (samples/context/cuts — no path, no pixels) and delegates the clip to the plain path; `reframing.py` (~1625) calls `find_reactor_face` for an extra detail pass and `choose_reaction_layout(path, …)` for the decision — it has the pixels.
- **Signal calibration (measured, logs in `~/workspace/reactorgate-logs/measure_*.log`):** whole-frame `SceneEvidence.motion` does NOT discriminate — a frozen-source control (live inset over frame-0 gameplay) still reads median motion 0.032 because the reactor's own inset motion inflates it. The discriminating signal is pixel change OUTSIDE the inset: streamer fixture median 1.655 grey levels (blurred, 0.5 s pairs), frozen-source control median 0.000 / max 0.965. It needs pixels, so it can only be checked where the path exists (`choose_reaction_layout`), never at the cached-analysis seam.

## 2. The gate change (exact)

`backend/src/media/reaction_layout.py`:
- The corner loop is factored into `_corner_candidates(width, height, samples) -> [(box, source_faces_evidence)]` — corner geometry, support and stability conditions unchanged except the one bar in §3; the evidence flag is the original `other_count ≥ 30%` condition, now reported instead of used as a hard reject.
- `find_reactor_face(...)` — public, EXACT legacy semantics (first candidate with source-face evidence). All existing callers/tests unaffected; `reframing.py` needed NO change (its detail-pass trigger keeps the strict gate deliberately).
- `find_reactor_corner(...)` — new public routing-level finder: first corner candidate regardless of evidence.
- `_source_area_changes(path, width, height, duration, inset) -> bool` — new: 6 sampled half-second frame pairs, grayscale + GaussianBlur(7,7), mean absdiff over pixels OUTSIDE the measured inset; PASS iff ≥4 valid pairs and median pair-diff ≥ 0.5 (calibration: fixture 1.655, frozen control 0.000 — ≥3× margin under the fixture, unbounded over the control).
- `choose_reaction_layout(...)` — candidates empty → None. First candidate WITH source-face evidence → legacy path, byte-identical behaviour. Otherwise: measure inset (None → None), then `_source_area_changes` (False → None), then build. A corner face over a static faceless source still returns None.

`backend/src/media/reframe_plan.py` (one call + comment): the seam now routes on `find_reactor_corner`. Delegation is only a routing hint to the plain path; `choose_reaction_layout` there makes the final pixel-verified decision, and if it refuses, the plain path's existing layouts apply. The stacked layout can never engage without faces evidence or pixel evidence. Podcast/Bert path safe: corner geometry unchanged, pinned by existing tests.

`CHANGELOG.md`: one **Fixed** entry under 2026-11-17 (root changelog, per repo rule) + changelog vitest 4/4 PASS.

## 3. Second condition found on the real path (separate commit `aef4f863` — independently revertible)

The first E2E re-run on this branch still fell back (peripheral_presenter, no seam line). Debugging against the pipeline's own reframe cache (`~/workspace/reactorgate-e2e/temp/uploads/streamer_fixture.reframe_cache.json`, clip samples 85.59–112.08 s): corner support 79/80 ✓, cy std 12.1 ✓ (bar 18.0), width std 14.2 ✓ (bar 24.4), but **centre-x std 21.1 px vs bar 19.2 px (1.5% of 1280)**. Robust MAD is 20.5 px — it is genuine face wander inside a pixel-fixed inset (cx percentiles 1088→1155, p10–p90), not detector outliers; at full-res the same face measures 18.4 and passes, so the failure is specific to pipeline-scale (480p) detection. The bar conflated the face's wander with the inset's stillness; the inset's stillness is proven separately by `measure_reaction_inset`'s edge measurement (unchanged, still required). **Change: centre-x std bar 1.5% → 2.0% of width** (cy and width bars untouched). Margins: fixture 1.65% (passes), drifting-face control ~4%+ and the existing moving-corner-face test (std ≈26 px at 640 w vs new bar 12.8) still reject with ~2× margin. Flagged for Sanji's merge decision: if he prefers the old bar, reverting `aef4f863`+`3b5fc717` leaves the source-area fix intact but the layout will still not engage on pipeline-scale samples of this fixture.

## 4. Tests — before / after (sanctioned venv, serial, OMP_NUM_THREADS=2; logs in `~/workspace/reactorgate-logs/`)

| Suite | Before (base `4a6b54ce`) | After (branch tip) |
|---|---|---|
| `test_reaction_layout.py` | 14 passed | **20 passed** (14 existing unweakened + 6 new) |
| Family set, 27 files (the merge lane's 24-file framing/reframe set + `test_editor_reframe`, `test_talking_head_layout`, `test_overlap_framing`; exact list in `family-set.txt`) | 459 passed, 1 skipped | **459 passed, 1 skipped — identical** |
| `test_reframe_split_render.py` (inside the family set) | pass | pass (two-face path untouched) |
| `frontend/src/lib/changelog.test.ts` | — | 4 passed |

New tests pin: (a) corner face + moving faceless source → `choose_reaction_layout` composes a `reaction` layout (and `_source_area_changes` is True); (b) corner face + static faceless source → None (and `_source_area_changes` False); (c) source faces over a STATIC source still compose (original path unchanged, no motion needed); strict `find_reactor_face` on corner-only samples → None while `find_reactor_corner` returns the box; the reframe-plan seam delegates a gameplay corner face to plain; a corner face wandering inside its inset (std ~1.7% of width) is still a reactor.

## 5. Rendered proof (E2E driver pattern, branch code, streamer fixture; scratch `~/workspace/reactorgate-e2e/`)

Same journey settings as the original run (moments_ai / Auto / framing auto / gaming / music+B-roll on), fresh DB `streamer_e2e_rg`, R3 candidate picked (candidate 2, window 80–110, score 90 — slate identical to the pre-fix run).

- **Seam log line** (`logs/driver-pick.log`, 08:10:52): `Reframe plan: persistent reactor inset detected; delegating this clip to the plain layout path` — absent in both pre-fix runs.
- **Layout line** (08:11:13): `Reaction layout: inset (955.0, 0.0, 325.0, 246.0), 1 source sections` — measured inset ≈ the fixture's true inset (960, 0, 320, 240).
- **Export:** `~/workspace/reactorgate-e2e/out/clip_1_0120-0150_8a2a5b3131e0.mp4` — ffprobe **1080×1920, h264 + aac, 26.6 s**, 18,016,440 B, SHA-256 `c9cd67e2f47b93c56adeb5feca87b60a7a61d58305c2e436b3307671249fb121`. Source range 85.59–112.08 s (same trim as pre-fix); task completed, error None. (Pre-fix export hash `780697ad…` had `reframe_shots` stamped peripheral_presenter; the new source_map has no EDL shots — the plain path rendered it.)
- **Frames judged from the export** (`~/workspace/reactorgate-e2e/frames/`): t=22.5 (source ≈108.1, the R3 scream peak) — reactor pane on top with the streamer's face large and fully inside its pane, mid-reaction; gameplay below with kill feed ("Player eliminated Mako"), crosshair, HP and AMMO HUD all visible; karaoke caption at the pane seam, hook title at the bottom. t=3 and t=14 confirm the composition holds throughout; the masked inset region is visible in the source pane (no ghost facecam copy) — by design.
- **Honest composition note:** the source pane runs in the layout's 'content' mode — the full source is scaled to the pane width over a blurred plate, so gameplay sits in a centred window with dark blurred margins inside the pane rather than filling it edge-to-edge. Nothing is cropped and the HUD is fully readable; whether gameplay should fill the pane is a composition preference for Sanji/Luffy, not a gate defect, and was not tuned in this lane.

## 6. What is NOT proven / remains

- Real (non-synthetic) streamer VOD: not run — the fixture is a synthetic composite; the gate margins (activity 1.655 vs bar 0.5; wander 1.65% vs bar 2.0%) are fixture-measured.
- The 2% stability bar (§3) is this lane's judgement call, isolated in its own commit for Sanji's merge decision.
- Full backend gate not run in this lane (family set + reaction suite only, per brief).
