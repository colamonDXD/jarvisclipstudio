# Viral Scout Pack, item 4 — split-screen layouts: premise + proof report

**Branch:** `feature-split-screen` off next-milestone @ `ab44064` (HEAD verified first), worktree `~/workspace/jcs-split`, tip `ffb75b5`, 1 commit, tree clean. **Not merged, not packaged, not uploaded.**
**Change:** test-only — `backend/tests/unit/test_reframe_split_render.py` (+368 lines, 8 tests). **Zero product code changed.**

## Step-0 premise outcome — REFUTED as a build task; the split already exists end-to-end

A true two-pane split-screen is already implemented in the **Auto production path**, in all three layers:

1. **Planner** — `backend/src/media/reframe_plan.py`, `build_reframe_plan()`. Per-shot decision (entries from the context layout plan, pre-split at complete face turnovers by `_split_entries_at_turnovers`, so a shot is split or not — never per-frame flicker). A shot becomes `mode="split"` (reason `interaction_exceeds_crop`, or `face_blur_avoided` via the fit-blur guard) when **all** of these hold:
   - ≥2 **significant** face tracks: presence share ≥ `TRACK_MIN_PRESENCE_SHARE = 0.35` of the shot AND mean face area ≥ `TRACK_MIN_MEAN_AREA_FRAC = 0.004` of the frame (observed YuNet boxes via greedy-IoU tracks);
   - the pair's combined span is **wider than the 9:16 crop window** (`PAIR_SPAN_MARGIN = 1.0`; a span that fits gets a **pair crop** instead — one window framing both faces, a deliberate choice: a wider margin would bisect a face);
   - shot duration ≥ `SPLIT_MIN_SECONDS = 2.0`.
   Tiles come from `_split_region()` on each track's median box (~2.8× face width, 2.4× face height, face-centred). Fallbacks are explicit: too short → full-frame fit; group of ≥3 → pair-crop if the extremes fit, else fit/dominant-subject (never split); second face transient → single crop on the dominant subject.
2. **Resolution** — `_resolve_segment()` stamps `segment["reframe"]["split_layers"]` (left/right tile dicts + segment-local intervals).
3. **Renderer** — `edl_render._segment_framing_fragments()`: each split layer crops both tiles, scales each to **1080×960**, `vstack`s them (left subject top, right subject bottom) into a full-frame 1080×1920 stack, composited over the blurred backdrop only where intervals don't cover (invisible at full coverage).
4. **Wiring** — `video_service.py` calls `attach_reframe_plan(edl, video_path, keep_ranges, plain)` for every production EDL render lacking a plan; nothing pre-stamps `edl["reframe"]`, so the split is reachable on every Auto clip. **Plain** uses `build_plain_reframe_plan`, whose contract (pinned by `test_reframe_plain.py`) is a single full-bleed crop — never a split.

Pre-existing coverage was planner-level only (`test_reframe_wire.py`, `test_reframe_fit_blur_guard.py`) plus graph-string pins for the legacy/editor vstack paths. **No test rendered a split or measured output frames** — that was the actual gap, and it is what this lane built.

**Adjacent layouts found (not split-screen, for the record):** `reaction_layout.py` (reactor PiP inset; reframe_plan delegates such clips to the plain layout path), `talking_head_layout.py` + `reframing.detect_speaker_reframe_plan` + `speaker_framing.py` stacked overlaps (legacy/editor path only), multitrack Stage-B overlays (user-placed PiP, not auto split).

## Scoped OUT, plainly

- **Speaker + gameplay/screen pane: does not exist and was not built.** The planner's subjects are face tracks only; there is no gameplay/screen-region detector to source a non-face pane from (`scene_context.py` mentions "webcam over gameplay" as classification evidence, not a region). Building one is a new detector, not a wiring job.
- **Portrait/narrow sources:** planner returns None (crop window spans the full width) → legacy chain; no split. **Groups of ≥3:** never split (by design). **Split tiles are static per shot** (median-box crops), not per-frame tracked — by design, and the tiles carry ~2.8× face-size headroom.

## Proof (all through the production path)

New suite `test_reframe_split_render.py` — **8 passed** (~26 s). Method: fabricated detector evidence at the established seam (`get_source_analysis` monkeypatched, the same seam `test_reframe_wire` uses); everything downstream is real production code (`attach_reframe_plan` → `render_edl_clip_ffmpeg`). The synthetic 1920×1080 source paints each subject a distinct colour (left red, right blue) with a **white marker square exactly at each fabricated face centre**, so marker centroids in output frames are the face-in-pane measurements; expectations are computed from the resolved tile/crop geometry in the EDL, not hard-coded.

| Check | Result |
|---|---|
| (a) Two-face render splits | Plan `mode="split"`, one `split_layers` entry, intervals [[0, 6]]. Output frame t=3s: top pane red share **>0.90**, bottom pane blue share **>0.90**, cross-bleed **<0.01** each way. Markers: 2 clusters at **(539.5, 402.5)** and **(539.5, 1362.5)** vs tile-predicted (540, 403.4) / (540, 1363.4) — both faces centred in their panes (test tolerance ±40 px; observed error ≤1 px). |
| Graph pin | Graph contains both `crop=556:476:… ,scale=1080:960` tile fragments and `vstack`. |
| (b) One-face render unchanged | Plan `mode="single"`, segment has `crop` only, **no `split_layers`**, graph has no `vstack`. Marker at **(516.5, 728.5)** vs crop-predicted (517.8, 728.9). Control: the same EDL rendered **without** attach (legacy centre crop x 657–1263) contains **zero** markers — the face is framed only because the plan's single crop runs, i.e. today's behaviour exactly. |
| (c) Plain on the two-face source | `edl["reframe"]["plain"] is True`, shot mode `plain`, no `split_layers`. Render: blue share <0.01, red >0.5 in both halves (one continuous crop), **exactly 1 marker** at (542, 729) vs predicted (542.4, 729.6) — the dominant face, full-bleed. |
| Trigger: presence share | Second face at 5/18 samples (0.28) → `single`; at 7/18 (0.39) → `split`. Threshold 0.35 pinned from both sides. |
| Trigger: shot length | 1.5 s two-shot → `fit`, no split shot (SPLIT_MIN_SECONDS = 2.0). |

**Real-detector corroboration (evidence, not a test — fixture lives outside the repo):** `get_source_analysis` + `build_reframe_plan` on the real Japan source (`~/workspace/qm-source/source.mp4`, real YuNet): window 1310–1360s → 2 split shots (201/390 samples with 2 faces); 1690–1760s → 4 split shots; 880–920s → 4 split shots. The trigger fires on real content through real detection.

**Neighbours, unmodified, serial:** `test_reframe_wire` + `test_reframe_fit_blur_guard` + `test_reframe_plain` — **61 passed**; `test_edl_render` + `test_template_mode` + `test_edl_pipeline_wiring` — **42 passed**. No pinned test changed; nothing needed re-deriving. `changelog.test.ts` — **4 passed**.

**CHANGELOG:** no entry — test-only change with no product behaviour change (AGENTS.md exempts exactly this; the reason is stated in the commit body). The validator was still run and passes.

## Five states

- **IMPLEMENTED:** the split itself pre-existed this lane (planner/resolver/renderer/wiring, unchanged). This lane implemented the missing render-level proof + trigger boundary pins.
- **INDIVIDUALLY TESTED:** yes — new suite 8/8 incl. 4 real renders; neighbours 103/103.
- **INTEGRATED:** yes — the proof drives the exact production seam (`video_service`'s attach call + the production renderer); no integration change was needed or made.
- **CROSS-FEATURE VERIFIED:** partial — split × Plain exclusion proven at render level; split × Brand Kit / separation / viral-plan in one render not exercised (the split is a framing layer upstream of those, and the flashes/signature lanes' Plain proofs cover the shared strip path).
- **USER-MACHINE VALIDATION PENDING:** a fresh Auto (not Plain) two-person podcast/gaming clip on Luffy's install should show the stacked split on its wide two-shots; note Plain — his default — never splits by design, so validation must use an Auto-template task.

## Notes for the merge decision

- Nothing to merge but the test file; product trees are untouched, so the v3 package identity is unaffected either way.
- If a future package note describes split-screen, the honest wording is: "wide two-person shots stack the two speakers (Auto template only; close pairs share one crop; Plain never splits)" — and speaker+gameplay split remains an unbuilt detector project, not a toggle.
