# Consolidated Fixes — what this build adds on top of the SnipNova Final release

**Base:** the frozen release candidate `release-candidate` @
`b4e2a18c684917c1bf3c3b530da871c45243e0ad` — the exact tree inside the
SnipNova Final ZIP (installed and verified on Luffy's machine
2026-11-10, verify.ps1 61 passed / 0 failed). Its record ships in
this package as `reports/FREEZE_RECORD.md` and
`reports/INTEGRATION_NOV12.md` and is not repeated here.

**This build:** branch `next-milestone` @
`8eeeb46857a100615130b1dc8df7ec002a8d59e5`. Everything below was
merged into that branch after the freeze, each item with its own
lane evidence, and the merged tree then passed the full gate.

## Full gate on the merged tree (the numbers this build ships on)

- Backend: **3,468 passed / 0 failed / 2 skipped** (clean serial run,
  19 min 17 s; log `full-gate-8eeeb46.log`). Zero failures are
  "expected" anywhere — the old 21-name baseline was retired by the
  hygiene pack below.
- Frontend: **802 passed / 0 failed / 12 skipped** (the old i18n
  baseline failure is gone after the backfill below).
- `tsc --noEmit`: 11 errors, byte-identical to the freeze baseline.
- Migration safety: PASS — the new migration
  `20261110_0001_task_classification.sql` included; zero
  semicolon-in-comment violations across all sprint migration files.
- Reference Styles bridge: 4/4 files present, byte-identical to the
  audited cherry-pick.

## The fixes, in merge order

| # | Fix | Commits / merge | Evidence |
|---|---|---|---|
| 1 | Watch-folder orphan recovery (F-D1): per-scan sweep re-drives or parks stale `processing` claims | `94f66d2` | Watch-folder suite 15/15 (3 new tests watched failing first) |
| 2 | Deliverable bundle hardlink instead of full copy (F-P2) | `ec5cefc` | Bundle suite 5/5 (3 new tests) |
| 3 | Brand Kit intro/outro bumpers render (stitched, normalised, fail-safe) | `f342810` | Brand-kit suites 52/52 incl. real-FFmpeg renders (≈16 s = 12 + 2 + 2; frames at 1.0/8.0/15.0 s) |
| 4 | Test hygiene: conftest proxy scrub, billing fixture columns, Kokoro gate probe | `6d57c69` | Full suite at application: 3,356 passed / 0 failed; baseline failure list retired |
| 5 | B-roll survives re-renders (saved placements carried into the re-render EDL) | `ade76bb` | Re-render suite 14/14 + real-FFmpeg render proof (stock inside its window only); editor suites 59 passed |
| 6 | SFX own-upload Studio path (config dir, two-root library, upload route, render resolver, panel UI) | `1817d50` | Backend 32/32, frontend 6/6, real-FFmpeg mix proof (900 Hz user effect ≥15 dB over quiet window); combined batch 124/124 |
| 7 | NVENC crossfade pre-stage stitch (F-P1), CPU-identical fallback | `ed5a80f` + `52bd9fe`, merge `ebabe13` | Contract tests 11/11; post-merge targeted 25/25. Speed A/B = user-machine pending |
| 8 | OpenCode adoptions: loopback upload proxying, create-route 502 JSON, Enter-submit guard, info-card textfile sidecar + wrap (B5) | `113215e`, `cd5fb26`, `e6e8bff`, `152c16b`, `3c9b2dc`; merge `5f6215f` | Frame-visibility proofs pass outright (apostrophe card visible; 138-char card inside frame); test_edl_render 12/12 post-merge |
| 9 | Per-clip / per-pass stage timings (F-P3) recorded into `stage_timings_json` | branch `feature-fp3-stage-timings` (6 commits), merge `d1914d2` | Lane 267 passed / 0 failed targeted; post-merge 21/21 |
| 10 | Non-English UI backfill (F-I18N): 302 keys × 5 locales, interim English | `dd1d847` + `8aa06d3`, merge `789ccd1` | messages + changelog tests 6/6 post-merge; every locale at 2,882 keys; `en/` untouched |
| 11 | Self-run audit fixes: B-roll relevance + word anchoring; Music Auto bed fallback | `4d3472d`, `46f8eb8`, `c441e0c`; merge `a43b2e4` | Post-merge targeted 74/0; render frame diffs (stock in-window only); music render pair −35.4 dB bed vs −120 dB silent control |
| 12 | Rerank score-flattening fix + duplicate window-summary drop | `618a23f`, `d3d2743`, `66597ed`; merge `903b461` | test_ai_windowed_selection 33/33 post-merge; neighbours 119/0 |
| 13 | **P0 auto-niche classifier chain fix**: candidate fallthrough, JSON-text repair, failure-path guardrails (gaming/sports/finance/music/comedy), `tasks.classification` persistence + visible fallback warning, gateway `local-qwen` → `clip-assured` fallback | `e988450`, `66dec36`, `8310fda`, `354d5db`; merge `1291851` | 6-case battery 6/6 (incl. the user's exact live HTTP-400 string); branch full suite 3,421 passed / 0 failed (one clean serial run); post-merge targeted 166/0. **Live proof on the user's machine still pending — see KNOWN_ISSUES Q7** |
| 14 | Drawtext sidecar regressions from #8: str-path coercion at renderer entry, deterministic sidecar filenames | `c382d16`, `2f3a3c8`, `57116d3`; merge `8eeeb46` | Lane 99/99 incl. original info-card proofs; post-merge 29/29; the 3 gate failures these fix were found by the full gate on the merged tree, not waived |

## Also investigated, no code change

- **Re-render caption-template override** (Final build's KNOWN_ISSUES
  B3): found already wired in both render pipelines with render-level
  proof. The gap was stale; B3 is retired in this build's
  KNOWN_ISSUES (FIX-8).

## Deliberately NOT in this build

- Self-run audit items #4 (Reference Style label confidence/margin
  display — KNOWN_ISSUES R1) and #5 (face-in-blur framing fallback —
  KNOWN_ISSUES Q10): queued behind this package.
- The frozen deferred six (adaptive VRAM manager, YouTube channel
  monitoring, voice/music separation, reference font/caption-text
  reading, viral scout pack remainder, multitrack depth): unchanged.
- The F-S1 scheduled-publishing kill-switch: an option requiring the
  user's decision; not built (KNOWN_ISSUES PB1).

## Packaging notes for this ZIP

- Tree = `git archive` of `8eeeb46`, plus exactly one added file
  (`JarvisClipStudio/TRACKER.md`, the re-scored tracker for this
  build) and the delivery docs/scripts in the wrapper.
- `verify.ps1` expected summary recomputed from scratch for this
  build: **126 passed, 0 failed** (arithmetic in
  `DELIVERY_CONTENTS.md`).
- The shipped Final package (`packaging-final/`) and the frozen
  branch were not modified by this packaging run.
