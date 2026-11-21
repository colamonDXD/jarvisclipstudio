# Caption/Title Face-Overlap — Root Causes, Fix, Measured Proof

**Lane:** `fix-caption-face-overlap` off next-milestone @ `537d55e`, worktree `~/workspace/jcs-facecaption`. **Not merged** (main verifies + merges). All work ₹0, layout/composition code + tests only; no LLM/prompt changes.

**Trigger:** Luffy's own Bert Kreischer clip (25.8 s, 1080×1920, rendered by his installed v3+patch build): karaoke caption across the speaker's mouth/chin in a single-speaker tight crop; pinned hook title across the top speaker's eyes in a split-screen shot; thin slivers of other source content at the split tiles' edges. His verdict: text in front of the face is unacceptable.

## The defect, measured on his actual frames (YuNet faces + tesseract OCR)

Frames in `caption-face-frames/bert_before_f2.png` / `bert_before_f7.png`:

- **f2 (single-speaker):** face box (297, 496, 499×581). Caption words intersecting the face: "BLINDERS" 21,386 px², "OUTFIT" 22,774 px², "TO" 5,386 px² — **≈ 49,500 px² of caption on the face**, across the mouth/chin line.
- **f7 (split):** top-tile face box (401, 217, 339×414). Every title word intersects it: "ASSISTANT" 7,401 + "BUYS" 11,886 + "PEAKY" 4,305 + "BLINDERS" 1,505 + "OUTFIT" 18,270 + "FOR" 9,107 = **≈ 52,474 px² of title on the top speaker's face/eyes**. Same pattern in f15 (≈ 34,900 px²) and f20 (≈ 54,200 px²).

## Step 0 — root causes (code + pixels)

1. **The EDL caption path had no face awareness at all.** `build_assemblyai_ass_subtitles` takes `pos_y` from the template (or an override); `get_safe_vertical_position` only clamps to the platform safe area (bottom 18%). The one face-adjacent mechanism, `avoid_band`, handles burned-in *source* captions, not faces. On the EDL path, `create_optimized_clip` skips the heuristic layout planner entirely ("the EDL renderer takes no speaker input"), so the template position governed the whole clip. The meme/comedy preset **pins captions at position_y = 0.50 — dead centre**, which is exactly where a tight face-tracked crop puts the face. Bert's profile was Meme & comedy.
2. **The pinned hook title had zero face input.** `build_hook_title_ass` places the block at Alignment 8 with MarginV = 12% of height (y ≈ 230–460 for a 2-line title). Nothing ever checked it against a face.
3. **Split shots bypassed even the heuristic layouts**, and the heuristics were never guards: the non-EDL talking-head layout uses only the dominant face's median bottom; the speaker-"complex" path hardcodes caption_y = 0.5. Worse, split tiles centre the top face at ~40% of the tile, putting the eyes at ~0.19–0.22 of frame height — inside the title band by construction.
4. **The planner computed the needed face boxes but never exported them.** `build_reframe_plan` knows every shot's face tracks; the resolved segment `reframe` carried only crop/fit/split geometry. Caption layout and reframing were two systems that never met.
5. **Split-tile edge slivers are NOT a composition bug.** Every split composition scales tiles to exactly 1080×960 and vstacks them over the backdrop; the backdrop is blurred (σ=14) and cannot produce hard pure-white lines or legible art. Pixel measurement of f7: pure-white (255,255,255) vertical bars at x≈9–16 and x≈1073–1079 **in the top tile only**, with landscape/forest art outside them — that is the *source frame's own* YMH border furniture (white frame lines + decorative border + the feed's burned-in "1 CAVE" logo) captured **inside** Tom's tile crop window: `_split_region` makes tiles 2.8× face width × 2.4× face height, and Tom's smaller face lets his window outgrow his camera feed into the border; Bert's bigger face keeps his tile inside his feed (hence top-tile-only). Proven by pixels + composition exactness (edge scans below). A generic "detect and exclude a source's decorative border/inset" feature is a separate build — recorded as a residual, not faked as a composition fix.

## The fix (5 commits)

- `b4686d3` — **new pure policy module** `backend/src/media/face_text_guard.py`: given a text block and output-space face boxes (+12 px safety margin), returns the block centre unchanged if it already clears every face (**byte-identical placement when no collision**); otherwise the nearest face-free position among the free vertical bands that fit the block (band size breaks ties); if no band fits, centred in the largest band, clamped on-frame. Sub-pixel epsilon so a block edge landing exactly on a face boundary counts as touching, not intersecting.
- `dccfdae` — **planner stamps**: each decided shot records `shot["face_src"]`; segment resolution transforms the boxes through the renderer's own geometry (crop-window sweep at the shot's static y, split tile rects, letterbox fit) and stamps `segment["reframe"]["face_boxes"]` (segment-local entries). New `collect_face_zones(edl)` helper returns clip-time zones. Render behaviour unchanged — this only exports evidence the planner already had.
- `525cf56` — **caption + title wiring**: `create_optimized_clip` passes `collect_face_zones(render_edl)` into `build_assemblyai_ass_subtitles` (covers Auto + Plain renders and hook previews, which share this path). Zones shift through the EDL join plan with the same math as the caption words. Each caption chunk overlapping a zone gets its centre guarded *after* the existing source-caption band adjustment — the guard wins last. The hook title gets the same guard with shrink-to-fit (sizes 1.0→0.6) when no band fits at full size. **Bypasses:** explicit editor placements and manual centre overrides (the user's own choices win), editor-styled titles. Guard decisions recorded in the caption layout report (`face_guard_captions.moves`, `face_guard_title`). No caption text/timing/styling/colours changed; `title_person.py` untouched.
- `4e4c9b0` — **envelopes, not medians** (found by the Japan proof below): `face_src` stamps each track's **union envelope** — the full range the face occupied during the shot — because a face placed clear of the *median* box is still hit when the subject leans. The fit-blur guard keeps using medians exactly as before.
- `2b5048f` — CHANGELOG entry (changelog.test.ts 4/4).

**Placement policy (as specified):** single-speaker → below the chin into the lower third (respecting the bottom safe area) or above if freer; split → the inter-tile gap band, below the top face inside its tile, or above the bottom face — measured per shot from both tiles' actual boxes. Zones are constant within a shot, so every caption chunk of a shot lands at the same centre — no jitter between lines.

## Measured proof (render-level, mandatory)

Harness: `backend/tests/unit/test_caption_face_guard_render.py` — production pair (`build_assemblyai_ass_subtitles` + `render_edl_clip_ffmpeg`) on real `attach_reframe_plan` EDLs; BEFORE = same pipeline with face boxes withheld (pre-fix behaviour), AFTER = zones passed. Text boxes from tesseract OCR on rendered frames; face boxes are the plan's stamped boxes, validated against white face-centre markers painted into the synthetic sources (marker centroid within 40 px of the stamped box centre — observed ≤ a few px). Frames in `caption-face-frames/`.

| Scenario | Before | After |
|---|---|---|
| Synthetic single-face tight crop, centre-pinned captions (the Bert f2 geometry) | caption ∩ face **> 0** (defect reproduced) | **0 px²** at t = 1.5 / 2.5 / 5.0 |
| Synthetic two-face split (title + captions) | title/caption ∩ face **> 0** (defect reproduced) | **0 px²** at t = 1.5 / 2.5 |
| Split edge scan, x = 0 and x = 1079, both variants | longest near-white run **< 20 px** (no underlay bleed; composition is exact) | same |
| **Real Japan split shot** (source 1321.5–1324 s, real YuNet on output frames) | title ∩ top face **42,619 px²** (t = 6.25) | **0 px²** at all sampled times (5.6 / 6.25 / 6.9) |

The Japan run also caught the median-box flaw mid-lane: with median boxes the AFTER render still showed 16,680 px² at t = 6.9 (the subject's head rose ~50 source px above the median box); the envelope fix (`4e4c9b0`) took it to 0.

Unit/ASS coverage: `test_face_text_guard.py` (10, policy: collision→moved, no collision→unchanged, split gap geometry, no-fit fallback, x-beside-face ignored), `test_caption_face_guard.py` (9: caption move, byte-identical when clear, time-scoped zones, editor bypass, title→split gap, title→above-face, title shrink-to-fit, join-plan zone shift), `test_reframe_face_boxes.py` (4: stamped geometry for split/single/plain + `collect_face_zones`). Neighbour suites green: reframe split/plain/wire/fit-blur (73 with the new file), captions batch (148: pacing, width, word timing, hook title styles, hook variants, source captions, editor captions, title person, creator titles).

## Residuals / honest limits

- **Source-border furniture in split tiles** (the Bert edge slivers): root-caused to the source's own decorative border captured inside oversized tile windows — not a composition defect, and the composition is proven exact by the edge scans. Excluding it needs a source-border/inset detection feature (banked earlier as an adopt-later item alongside `reaction_layout.measure_reaction_inset`); not built here.
- The guard covers faces the **planner observed** (3 fps sampling; envelopes over each shot). A face that appears mid-shot without a track, or moves beyond its shot envelope between samples, is outside the guarantee — same evidence limit as the reframer itself.
- Legacy/editor stacked layout (`speaker_framing.build_stacked_overlap_filter`) does not stamp EDL face zones; the editor path is placement-explicit by design and bypasses the guard intentionally.
- Transcription word errors in the Bert clip are out of scope (noted only), per the lane brief.

## Remaining user-machine validation

Ships in v4 (with the brain upgrade). On Luffy's machine: re-run the same Bert video (2 Bears, 1 Cave Ep. 347) on the v4 build and confirm (a) no caption crosses Bert's mouth in the single-speaker sections, (b) the pinned title never covers the top speaker's eyes in split sections (expect it in the split gap or above the top face), (c) the niche still classifies Meme & comedy and titles stay third-person. Until that run, state = **CROSS-FEATURE VERIFIED (sandbox renders), USER-MACHINE VALIDATION PENDING**.
