# Wave 4 — Reference Style caption-look, Stage 2: STAGE 2 REPORT

**Branch:** `feature-reference-look-2` off next-milestone @ `974feaa` — worktree `~/workspace/jcs-reflook2`. **Not merged, not packaged, not uploaded.**
**Commits:** `a07f1d3` (animation feel) · `162e730` (font matcher + gates) · `0a4a219` (margin gate correction + test suite) · `1c25246` (static-verdict floor) · `a1202fb` (CHANGELOG)
**Scope built:** (1) a margin-gated font SUGGESTION for the caption look, (2) a coarse animation-feel class stored as information only. Per the brief and SCOPING.md §5, the honesty contract governed every design call below.

---

## HEADLINE — the font evaluation (the gate)

Planted fixtures: 12 of the app's 25 bundled fonts, each rendered by the app's own caption renderer (pangram text, size class 34, flat slate, CRF 23) and analysed end-to-end by `analyse_reference`. Shipped configuration: up to 6 matcher instances, score = glyph-mask IoU − contour-chamfer term. Rankings reproduced identically across two full runs (the pipeline is deterministic on fixed fixtures).

**Raw matcher top-1: 9/12 (75%).** Heavy sans group **3/5**, display **3/3**, text sans **3/3**, serif **0/1**.
**Class-level correctness: 12/12** — every winner is the planted face, its runner-up, or a member of its confusion group.
**Shipped gates emit 4 suggestions; all 4 are exactly right** (precision 4/4 on the fixtures; recall 4/12 — deliberately low).

| Planted | Group | Raw top-1 | Score | Margin | Family margin | Raw | Gated (shipped) |
|---|---|---|---|---|---|---|---|
| Montserrat-Black | heavy | Montserrat-ExtraBold | 0.637 | 0.063 | 0.062 | MISS — weight swap | silent |
| Montserrat-ExtraBold | heavy | Montserrat-Black | 0.605 | 0.081 | 0.080 | MISS — weight swap | silent |
| Poppins-ExtraBold | heavy | Poppins-ExtraBold | 0.694 | 0.190 | — | HIT | **emitted ✓** |
| THEBOLDFONT | heavy | THEBOLDFONT | 0.822 | 0.373 | — | HIT | **emitted ✓** |
| ArchivoBlack-Regular | heavy | ArchivoBlack-Regular | 0.643 | 0.136 | — | HIT | **emitted ✓** |
| BebasNeue-Regular | display | BebasNeue-Regular | 0.661 | 0.004 | 0.004 | HIT | silent (tie with Anton) |
| Anton-Regular | display | Anton-Regular | 0.736 | 0.122 | 0.123 | HIT | **emitted ✓** |
| Bangers-Regular | display | Bangers-Regular | 0.537 | 0.071 | — | HIT | silent (score floor) |
| NotoSerif-Regular | serif | TikTokSans-Bold | 0.566 | 0.033 | — | MISS (planted face is runner-up) | silent |
| Inter | text | Inter | 0.582 | 0.038 | 0.070 | HIT | silent |
| Roboto | text | Roboto | 0.469 | 0.009 | 0.009 | HIT | silent (score floor) |
| OpenSans | text | OpenSans | 0.528 | 0.058 | 0.058 | HIT | silent |

### What the numbers forced (the honesty contract in action)

1. **The heavy-sans coin flip is real and systematic.** The two Montserrat weights swap places in both directions. At 3 instances the swap margins were 0.057/0.072; at 6 instances 0.063/0.081; at 10 instances 0.072/0.063 — more evidence does *not* fix it (the core-mask pipeline normalises the two weights toward the same blob). This matches SCOPING §5's prediction almost exactly.
2. **A margin gate at 0.08 shipped a wrong font.** The first shipped-config evaluation emitted "Montserrat-Black" for a Montserrat-ExtraBold reference — the swap cleared score, margin (0.081) and family margin (0.080) by ~0.001. The evaluation suite caught it before anything shipped. The gate now sits at **0.10**, between the loudest wrong margin measured anywhere (0.081) and the quietest correct decisive win (Anton, 0.122). Re-run: 4 emissions, 4 correct.
3. **Instance count matters for faces that *can* separate.** Raw top-1 was 6/12 at 3 instances → 9/12 at 6 (Poppins's margin 0.060 → 0.190 at 10). Shipped: 6 instances as the cost/benefit point.
4. **Serif is this pipeline's blind spot.** NotoSerif's thin strokes erode in the core mask; it lost to a bold sans (its own face was runner-up). One fixture only — but no serif suggestion can be trusted from this matcher, and none is emitted (margin 0.033, gated out).

### The shipped gate

`font_suggestion {family, score, margin, runner_up, n[, family_margin]}` is stored in the look **only** when all three bars clear:

- score ≥ **0.55** (absolute floor),
- margin ≥ **0.10** over the runner-up,
- margin ≥ **0.10** over the winner's own confusion group (Montserrat weights; Bebas/Anton/Oswald/Barlow condensed grotesques; the humanist text-sans set), when the winner has one.

Below any bar the look carries **no suggestion at all**. When it fires, the apply path stamps `font_family` in the look's existing precedence slot (task > Brand Kit > reference look > stock preset — unchanged), and the provenance summary labels it verbatim: `"<family> (closest bundled font — a guess)"`. The words "identified"/"detected" appear nowhere.

## Animation feel

Method: Stage 1's paired frames (+0.4 s) now get OCR'd too, plus a new burst frame (+0.15 s) per pair instance. Per pair, three gated signals — highlight-centroid movement ≥ 0.03 of frame width with the same text (karaoke progression), strict-prefix word growth (reveal-style karaoke), and a ≥ 1.10 line-height scale ratio (pop/bounce entrance; the renderer's karaoke line entrance tops out at 1.087 and cannot fake it). Classification: karaoke ≥ 1 progression signal → `karaoke`; else ≥ 1 clean scale event → `pop`; else 3 quiet pairs → `static`; else `unknown`. Stored as `look["animation"] {value, n, evidence}` and surfaced in the summary. **No render application in this stage** — nothing consumes the field; that is stated in the code, the summary, and the CHANGELOG.

Planted outcomes (app's own renderer):

| Planted | Read | Evidence | Verdict |
|---|---|---|---|
| static (`animation: none`) | **static** | 3 pairs, 0 signals | ✓ |
| karaoke (hormozi, 0.5 s/word) | **karaoke** | 2 highlight moves, 1 scale change (word-pop entrance, correctly outvoted) | ✓ |
| fade | **static** | 3 pairs, 0 signals | as designed — fade ramps sit at chunk edges; disclosed in the classifier docstring |
| pop (`large_centre_pop`) | **unknown** | only 2 readable pairs, 0 signals | honest: at a 2-pair "static" floor this read *static* — the floor was raised to 3 pairs because the fixture DOES scale; two quiet pairs cannot rule a ~250 ms entrance out |
| bounce | **static** | 3 pairs, 0 signals | **a disclosed miss** — the ~360 ms arc was never sampled. Sub-400 ms entrances are simply not reliably visible at 3-pair sampling; the classifier says so rather than pretending |

The logic itself (evidence gates, precedence, thresholds incl. the karaoke-entrance guard) is pinned deterministically by unit tests on fabricated records; the planted fixtures pin the end-to-end behaviour and the restraint properties (pop never reads as karaoke; fade never reads as karaoke/pop).

## Implementation notes

- `_look_frame_record` now also retains the glyph-interior mask (font + highlight clusters, outline excluded — outlines are near-uniform across faces) and the highlight cluster's frame-relative centroid/area. Stage-1 fields and aggregation are byte-identical; the Stage-1 suite passes **unmodified**.
- The region OCR was extracted into `_ocr_region_words` (identical crop/binarize behaviour) and now also runs on paired frames; pairs previously contributed colour evidence only.
- The matcher renders all 25 candidates in ONE libass strip render per instance (one event per font, `\fn`-tagged, Bold=1 like the production style), core-thresholded at >200 to mirror the reference's k-means core mask. Scores are averaged over up to 6 instances (largest masks with OCR text, dominant line only).
- `ANALYSER_VERSION` 2 → 3 (the look schema grows: `animation`, `font_suggestion`).
- Cost: a 10 s reference analyses in ~11 s in this sandbox end-to-end; the matcher is a few of those seconds (≤ 6 strip renders + scoring).

## Proof

- **New suite** `tests/unit/test_reference_style_look_stage2.py`: **26 tests** — classifier/evidence units, gate units, matcher units (25-font candidate coverage, self-consistency), 4 planted animation fixtures, 4 evaluation tests (raw-rate pins ≥ 7/12 overall and ≥ 3/5 heavy vs measured 9/12 and 3/5; class-level == 12/12; gate precision + Montserrat silence; end-to-end `font_family` stamping + guess label).
- **Final verification batch (serial, final tip `a1202fb`): 204 passed / 0 failed** — Stage-2 (26) + Stage-1 look (18, unmodified) + reference_style (39) + reference_styles_routes (9) + brand_kit (46) + brand_kit_gaps (11) + template_mode (14) + user_profiles (23) + auto_director_edl (18).
- **CHANGELOG** entry added under 2026-11-11; `changelog.test.ts` run immediately after: **4 passed** (node_modules borrowed from `jcs-rc` via temporary symlink, removed after).
- Sandbox note: the VM had been replaced since Stage 1 — tesseract was missing and was re-installed via apt (5.3.4, same as Stage 1) before any OCR-dependent work.

## Honest limits & recommendations

1. **The suggestion is a sometimes-feature by design.** It fires only on decisive silhouettes (4/12 planted). For the most common caption faces in the wild (Montserrat-class heavy sans), it will usually stay silent — correctly. If that recall is considered too low to be useful, the honest alternative is to drop surfacing entirely; what must NOT happen is lowering the margin gate below 0.10 — the evaluation measured a wrong emission at 0.081.
2. **Pop/bounce detection needs denser sampling than this stage's budget** (more pairs/bursts per instance) if it is ever wanted as more than a best-effort note. Karaoke and static discriminate reliably at current sampling; entrances mostly do not.
3. **Serif and thin faces** are structurally hard for a core-mask matcher. Per-glyph segmentation might improve both serif and the text-sans cluster, at significant complexity; not attempted, not recommended now.
4. Stage 3 items from the scoping are untouched, as briefed: Devanagari OCR pack, style-card UI display of the look block.

## States (§9)

- Font suggestion (matcher + gates + stamping): **IMPLEMENTED + INDIVIDUALLY TESTED + INTEGRATED** (end-to-end through `analyse_reference` → profile → EDL override seam, same as Stage 1) — **USER-MACHINE VALIDATION PENDING**.
- Animation feel: **IMPLEMENTED + INDIVIDUALLY TESTED + INTEGRATED** as a stored, summarised measurement — information only, no render application (by design this stage) — **USER-MACHINE VALIDATION PENDING**.
- Cross-feature: Stage-1 look behaviour preserved byte-for-byte on no-suggestion looks (204-test batch incl. the unmodified Stage-1 suite) — **CROSS-FEATURE VERIFIED** in-sandbox.
