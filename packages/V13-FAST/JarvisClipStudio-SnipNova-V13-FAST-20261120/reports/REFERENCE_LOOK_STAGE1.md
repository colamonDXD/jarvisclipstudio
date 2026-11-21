# Wave 4 — Reference Style caption-look reading, Stage 1 report

**Branch:** `feature-reference-look` off next-milestone @ `58b5a19` (worktree `~/workspace/jcs-reflook`). NOT merged, NOT packaged, NOT uploaded.
**Commits:** `8b7a8ac` feat(reference-style): the look pass · `20ee5c8` feat(captions): render merge + EDL stamp + Plain strip · `649c01f` test(reference-style): planted-fixture suite · `3052672` docs(changelog).

## What was built

A sparse hi-res `_caption_look_pass` in `backend/src/auto_director/reference_style.py`, called from `measure_reference` after `_visual_pass`, never raising: on any failure/absence `measurements["captions"]["look"] = None` and the analysis is byte-identical to before (proven by test). Results carry per-field instance counts `n`.

- **Sampling.** Primary: the visual pass's per-sample caption votes (extended additively as `caption_votes`), restricted to the 5–95% duration window, preferring speech-like moments (audio levels ≥ 75th percentile − 18 dB, the module's existing convention); up to 10 instances + paired second frames (+0.4 s) on 3. Fallback: an even 10-point grid over the same window when the votes cannot support ≥3 candidates (see deviation (b)).
- **Per frame** (native res capped 720 wide): caption region from edge rows/columns with Canny-style hysteresis thresholding; colours from deterministic k-means (k=5) over region pixels, each cluster scored by edge adjacency; the brightest text cluster is the font colour (reported as the cluster's pixel **median**, which resists anti-aliasing fringe). Highlight = a saturated cluster with glyph interiors (own-mask erosion survival ≥ 0.30), not on the RGB segment between the font colour and any other cluster (blend-fringe rejection), in a plausible share band. Outline = a dark (luminance ≤ 110), near-achromatic (channel spread ≤ 40) cluster that hugs the fill (≥ 30% of pixels within 3 px), is not ≈ the scene colour, is not ≈ a detected background box, and surrounds the glyphs — a dark shell whose pixels sit almost entirely below/right of the nearest fill pixel is classified as a **drop shadow** instead (per-pixel nearest-fill direction test). Background box = a uniform surround (dominant-colour share ≥ 0.55) differing from the scene at the band corners; a black box gets its opacity re-estimated from box/scene channel ratios.
- **OCR** via the tesseract subprocess pattern from `visual_suitability.py` (`shutil.which` degrade guard, PNG via stdin, `--psm 6 tsv`, `OMP_THREAD_LIMIT=1`). The crop is Otsu-binarized and polarity-normalized first — tesseract's own handling drops mid-luminance colours (a pure-green karaoke word inside a black outline was invisible to it); binarized, every planted word reads. Words-per-line = OCR word count ÷ the frame's own edge-row line count (the TSV line grouping proved flaky under karaoke pop). Casing from pooled letter counts (≥ 20 letters; True ≥ 0.90 upper share, False ≤ 0.60). Near-zero confident words on an edge-confirmed region → `script: "unknown_non_latin"`, lexical fields omitted, pixel fields still measured (Devanagari OCR is out — the shipped image has the eng pack only).
- **Apply seam (no migration — profile JSON).** `build_profile` stamps `profile["captions"]["look_override"]` (partial template in the brand-override shape) + `inferred["caption_look"]` + a `reference_style.caption_look` provenance summary; `build_edl` copies it to `edl["reference_look_override"]`; the render path merges through one new helper `video_utils.caption_template_override` at the same site the brand merge used — **precedence: task > Brand Kit > reference look > stock preset**. A measured-*absent* outline/box stamps its off state (stroke_color None / background False); shadow is presence-only. `plainify_edl` strips the look key exactly like the brand blocks.
- **Named gap (a) — closed by proof, not assumption:** the merged template's float `position_y` IS read by `build_assemblyai_ass_subtitles` when no per-clip `position_y_override` exists, and the per-clip override still wins when present. Test `test_renderer_position_y_reaches_layout_from_override` drives the production merge helper into the real builder: stock layout y ≈ 0.78 (0.80 clamped), look-merged template → 0.62, explicit override 0.70 → 0.70.
- `ANALYSER_VERSION` 1 → 2 (measurements gained the look block; nothing pinned the old value).

## Measured vs planted (final planted-fixture run; the pytest suite asserts the same with tolerances)

Fixtures rendered with the app's own caption renderer on a flat slate + voice-burst audio, 720×1280. Colours tolerance ±12/channel (observed deltas ≤ 5), position vs the ASS `\pos` fraction ±0.03, words-per-line = the rendered ASS's chunk maximum, casing exact, outline presence exact, font size within one template class.

| Fixture | Field | Planted | Measured | Verdict |
|---|---|---|---|---|
| harmozi (pill off) | font_color | #FFFFFF | #FFFFFD | ✓ |
| | highlight_color | #00FF66 | #01FC67 | ✓ (Δ ≤ 3) |
| | position_y | 0.716 (ASS) | 0.715 | ✓ |
| | words_per_line | 3 | 3 | ✓ |
| | uppercase | True | True | ✓ |
| | stroke | present #000000 w4 | present #000000, bucket 3–5 across runs | ✓ presence/colour; bucket straddles the 3/5 boundary at planted width 4 |
| | background | none | absent | ✓ |
| | font_size | 32 | 30 | one class low (see (g)) |
| classic_box | font_color | #FFE000 | #FADC04 | ✓ (Δ ≤ 5) |
| | highlight_color | none | omitted | ✓ |
| | position_y | 0.80 (ASS) | 0.802 | ✓ |
| | words_per_line | template max 5, rendered chunks 4 | 4 | ✓ = what was on screen (see (f)) |
| | uppercase | False | False | ✓ |
| | stroke | none | absent | ✓ |
| | background | #00000099 | #0000009B | ✓ (alpha re-estimated, Δ 2) |
| | font_size | 26 | 26 | ✓ exact |
| no_highlight | font_color | #FFFFFF | #FFFFFF | ✓ exact |
| | highlight_color | none | omitted | ✓ |
| | words_per_line | 4 | 4 | ✓ |
| | uppercase | True | True | ✓ |
| | stroke | present #000000 w3 | present #000000 bucket 3 | ✓ exact |
| | font_size | 32 | 32 | ✓ exact |
| shadow | font_color | #FFFFFF | #FDFEFF | ✓ |
| | stroke | none | absent | ✓ (the shadow is not read as an outline) |
| | shadow | True | True | ✓ |
| | words_per_line / uppercase | 4 / False | 4 / False | ✓ |
| | font_size | 32 | 30 | one class low (see (g)) |
| no_caption | look | — | None | ✓ + profile byte-identical to a look-less build |

## Test evidence

- `tests/unit/test_reference_style_look.py` — **18 passed** (12 pure unit: mapping incl. absence stamping, k-means, black-box alpha, size buckets, build_profile stamping + byte-identity, EDL stamp, merge precedence, Plain strip; 1 renderer position_y proof; 5 planted fixtures end-to-end through `analyse_reference`).
- Neighbours, unmodified, serial: `test_reference_style.py` + `test_reference_styles_routes.py` — **48 passed**; `test_brand_kit.py` + `test_brand_kit_gaps.py` + `test_brand_kit_render.py` + `test_auto_director_edl.py` + `test_edl_pipeline_wiring.py` + `test_template_mode.py` + `test_user_profiles.py` — **132 passed**.
- `frontend/src/lib/changelog.test.ts` — **4 passed** (run immediately after the CHANGELOG edit, via the standard borrowed-node_modules symlink, removed after).
- OCR note: this sandbox initially had no tesseract; `tesseract-ocr` + `-eng` (5.3.4) were apt-installed during the lane, so the OCR-dependent assertions ran for real here. The degrade path is the same `shutil.which` guard production uses; without the binary those assertions skip and the pixel assertions still run (verified by code path — the look pass records `ocr: "unavailable"` and omits only the lexical fields).

## Five states

- **IMPLEMENTED:** yes — pass, mapping, EDL stamp, render merge, Plain strip.
- **INDIVIDUALLY TESTED:** yes — planted fixtures at the brief's standard + unit suite (above).
- **INTEGRATED:** yes — wired into `measure_reference` → `build_profile` → `build_edl` → the single render merge site used by `create_optimized_clip`; neighbour suites green.
- **CROSS-FEATURE VERIFIED:** partial — precedence vs Brand Kit and the Plain strip are proven at merge/builder level and the position_y float is proven into the real caption layout; a full service-level task render (reference profile → rendered clip file) was not run in this sandbox (no Docker/Postgres); the equivalent brand path is covered by `test_brand_kit_render`.
- **USER-MACHINE VALIDATION PENDING:** yes — as with every lane; live check belongs in the return pack (build a Reference Style from a captioned reference, render a clip, compare caption appearance).

## Deviations and boundaries (named, with reasons)

- (a) Branch is `feature-reference-look` per the dispatching task, not the scoping brief's `feature-reference-caption-look`.
- (b) Sampling deviates from SCOPING §4's letter: the vote-gated plan is primary, but an even grid fallback was added. Proven necessary: the 320 px visual pass's absolute edge floor (≥ 0.02) measures `frame_fraction = 0.0` on genuinely captioned serif and shadow-only references (band densities 0.010–0.014), so a votes-only pass would return no look for exactly the styles worth copying. The grid tries the visual pass's band first, then the centre band, and still requires ≥ 3 readable instances.
- (c) `background_color` stamps the effective on-screen colour, or black + an estimated alpha when the box reads as black — the original alpha over an unknown scene is not exactly recoverable (the scoping says the same).
- (d) Pre-existing, out of scope: `build_profile`'s preset choice still keys off the visual pass's `frame_fraction`, so a thin-serif reference can map to the "minimal" preset while carrying a measured `look_override` that then overrides it at render. The detector-envelope issue predates this lane.
- (e) Hook previews and cover images do not consume the look (their Brand Kit handling is separate); the clip-editor path consults neither brand nor look by design (it holds final values).
- (f) Words-per-line measures what the reference's renderer actually put on screen. The classic_box planting showed why this matters: the template allows 5 words, but the app's own width model chunked the planted serif text 4-wide — the recovered 4 is the truth of the reference, and the test asserts against the ASS, not the template setting.
- (g) Font size is the weakest field (FAIR grade, as scoped): the edge-extent estimator trims a few low-density rows at curved glyph tops/bottoms, calibrated to a 0.70 extent/font ratio across the planted faces. Result: exact on classic_box and no_highlight; one class low (30 vs 32) on harmozi and shadow. Reported as the nearest class; the override stamps that bucket.
- (h) One accent slot: when a style uses both a karaoke highlight colour and a separate emphasis colour, the look records the dominant qualifying accent as `highlight_color` — the schema (mirroring the brief) has a single accent field. The planted harmozi fixture deliberately avoids the builder's POWER_WORDS so the karaoke colour is the only accent present.
- (i) Font-family identification and animation feel remain Stage 2, untouched, as scoped.
