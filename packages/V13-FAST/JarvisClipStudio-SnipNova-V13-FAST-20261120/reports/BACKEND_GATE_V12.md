# Backend Gate — SnipNova Consolidated V12

**Packaged tip:** branch `next-milestone` @ `49cfad22`
(`49cfad2284c9a2bdb6fec9c7a4d836cac377c57a`).

**How the gate maps to the tip.** The full suite ran on the
merged tree `02ddf98f` (the V11 tip + the integration stack
+ both merges + the bundled music + the sanctioned
test-isolation fix). Every failure it produced was
attributed and closed (below). The only delta from
`02ddf98f` to the packaged tip is ONE product bug fix found
by that gate (`49cfad22`: a learned music-off was overridden
by the Auto bed when the music library is installed) plus
its CHANGELOG line. On the final tip, the affected areas
were re-verified: the full chunk containing both previously
failing files passes **478/478**, and the learning + music
+ wiring suites pass **100/100** — all with the music
library populated, the shipped condition.

**Method:** the Phase 1/2 chunked method — `tests/unit`
split into 30-file chunks, plus
`tests/test_video_utils_diarization.py`, `tests/integration`
and `tests/live` as separate runs; sanctioned venv (pytest
9.0.3); `TMPDIR` redirected to a workspace dir;
`OMP_NUM_THREADS=2`; `--no-cov` (the coverage threshold is
not evaluated on chunked runs, as in every prior gate); the
certified litellm condition held throughout
(`frontend/src/generated` set aside OUTSIDE the scanned
tree for the whole gate and restored after). The tree's
`assets/music` was POPULATED (the 7 bundled beds) for the
entire gate. A sandbox runtime restart interrupted the run
after chunk_ae; the remaining chunks resumed from the
on-disk DONE markers with identical conditions. Logs:
`~/workspace/packaging-v12/logs/gate_chunks/`.

## Totals (on `02ddf98f`)

| Run | Passed | Failed | Skipped | Xfailed |
|---|---|---|---|---|
| unit chunk_aa | 410 | 0 | 0 | 0 |
| unit chunk_ab | 615 | 0 | 0 | 0 |
| unit chunk_ac | 443 | 3 | 0 | 0 |
| unit chunk_ad | 384 | 1 | 2 | 0 |
| unit chunk_ae | 527 | 0 | 0 | 0 |
| unit chunk_af | 476 | 2 | 0 | 0 |
| unit chunk_ag | 350 | 0 | 0 | 0 |
| unit chunk_ah | 439 | 0 | 5 | 0 |
| unit chunk_ai | 440 | 0 | 2 | 0 |
| unit chunk_aj | 473 | 0 | 13 | 7 |
| unit chunk_ak | 459 | 0 | 1 | 0 |
| unit chunk_al | 204 | 0 | 0 | 0 |
| diarization (root) | 10 | 0 | 0 | 0 |
| integration | 2 | 0 | 218 | 0 |
| live | 0 | 0 | 2 | 0 |
| **Total** | **5,232** | **6** | **243** | **7** |

## The 6 failures — attribution and closure

1. **`test_caption_face_guard_render` (3 tests, chunk_ac)**
   and 2. **`test_content_requirements_brief_image::test_ocr_unreadable_bytes`
   (chunk_ad)** — ENVIRONMENT. Both shell out to the
   `tesseract` binary; the sandbox runtime had been reset
   and the binary was absent (the documented F1 condition).
   After restoring tesseract 5.3.4 via apt, the four tests
   pass: **4/4** (`logs/attribution-ocr-rerun.log`).
3. **`test_litellm_gateway_config::TestCloudflareRoute::test_no_app_or_ui_surface_knows_the_route_exists`
   (chunk_af)** — GATE-HARNESS ARTIFACT. The test scans
   `frontend/src` for the string "cloudflare"; the gate's
   first pass parked the set-aside `src/generated` INSIDE
   `src/`, where the scan found it in the Prisma runtime.
   With the set-aside moved outside the scanned tree (the
   certified condition), the test passes — confirmed in the
   final-tip chunk_af re-run (478/478).
4. **`test_learning_engine::test_pipeline_edl_applies_music_adjustment_only_to_its_niche`
   (chunk_af)** — REAL PRODUCT BUG, FIXED. With the music
   library installed, `apply_auto_music_bed` treated a
   profile whose music the learning engine had deliberately
   zeroed (multiplier 0.0 for its niche) as a bed-less
   profile and stamped a library bed at volume 0.10 — the
   learning loop could never silence a niche's music on a
   real install. Proven both ways before the fix (populated
   → fail `0.1 != 0.0`; library aside → pass). Fix in
   `49cfad22` (product code only; the test was not
   touched): on the Auto path, an applied adjustment with
   `music_volume_multiplier == 0.0` keeps the clip
   bed-free; explicit user music choices (off / override /
   on) are unchanged, and genuinely bed-less profiles still
   receive the Auto bed per the 2026-11-08 directive.
   Verified on the final tip with the library populated:
   the test passes; learning + music + wiring suites
   **100/100**; full chunk_af **478/478**.

## Notes

- The music-populated wiring test
  (`test_edl_pipeline_wiring::test_pipeline_edl_honours_classifier_and_fallback`)
  passes in both library conditions after the sanctioned
  test-isolation fix (`02ddf98f`): the test points
  `edl_render.ASSETS_DIR` at an empty tmp dir; its
  assertion is byte-identical. Before the fix it failed
  with `assert 'calm' == 'none'` (the Auto bed correctly
  applying once the library exists — intended behaviour,
  flagged by Claude, accepted by Sanji).
- Chunk exit codes of 143 are the watchdog killing pytest's
  known hang-at-exit AFTER the chunk summary printed; the
  summary line is the result of record for those chunks.
- The 218 integration skips and 2 live skips are the
  suites' own environment gates (no live services in the
  sandbox), identical in shape to prior gates.
