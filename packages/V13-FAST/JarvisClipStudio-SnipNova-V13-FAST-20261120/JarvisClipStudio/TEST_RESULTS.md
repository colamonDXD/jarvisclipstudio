# TEST_RESULTS — SnipNova test-evidence ledger

*Refreshed 2026-11-15 (IST), accurate to `next-milestone` @ `5ea71bf`
plus the UNMERGED queue branches (lane-level results, clearly marked
— a lane suite passing is not a gate). Every number traces to a gate
report, a lane report, or a run recorded in SPRINT_LOG. Previous
version was accurate to `7959ad7` (2026-11-14).*

## Current gate state

| Tip | Verdict | Detail |
| --- | --- | --- |
| `5ea71bf` (current, FROZEN) | **GATE GREEN** | Backend Q1–Q4 **4,656 passed / 0 failed / 16 skipped / 7 xfailed** (Q1 1,208; Q2 1,162; Q3 1,049/2sk; Q4 1,237/14sk/7xf). Frontend vitest **899 passed / 0 failed / 14 skipped** (the 4 "failed files" are the baseline-known real-PG integration files — zero failed tests). tsc **11 errors = exactly the baseline set** (the 9b24c7b gate's one NEW error removed, nothing else moved — sorted-line diff). eslint 0/0; Prisma generate exit 0; production `next build` exit 0; smoke 162 routes / 136 paths; bridge 0-line diff (4/4 files); migrations 48; changelog 4/4. NEW failures: NONE. Source: `research_notes/sprint-7day/INTEGRATED_GATE_5EA71BF.md`; logs `gate-logs-5ea71bf/`. |
| `9b24c7b` | **GATE RED** (one failure) | Exactly one NEW failure vs the `0a63942` baseline: tsc TS2322 at `inspector-fonts.test.tsx(113,38)` (Montserrat-ExtraBold missing from the `CaptionStyle["font"]` union; caused by the N6 LOW merge). Fixed by the font-union merge @ `5ea71bf` (3 files, +6/−3, no test touched). Source: `INTEGRATED_GATE_9B24C7B.md`. |

## Gate history (this sprint)

| Tip | Backend | Frontend | Verdict / note | Report |
| --- | --- | --- | --- | --- |
| `5ea71bf` | 4,656/0/16/7 | 899/0/14 | **GATE GREEN** | `INTEGRATED_GATE_5EA71BF.md` |
| `9b24c7b` | — | — | **GATE RED** — one NEW tsc failure (font union) | `INTEGRATED_GATE_9B24C7B.md` |
| `0a63942` | 4,642/0/16/7 | 892/0/14 | **GATE GREEN** (NEW failures: NONE) | `INTEGRATED_GATE_0A63942.md` |
| `6669028` (code `7959ad7`) | 4,618/0/16/7 | 892/0/14 | **GATE GREEN** (run-1 Q1's 3 face-guard failures proven environmental — tesseract absent after a runtime restart; restored 5.3.4, solo re-run 3 passed) | `INTEGRATED_GATE_7959AD7.md` |
| `9ba6602` | 4,574/0/16/7 | 868/0/14 | **GATE GREEN** (next build exit 0; smoke 162/136) | `INTEGRATED_GATE_9BA6602.md` |
| `90f1cb4` | Q1 1,215/0; Q2 1,096/0/0 | — | **GATE GREEN** — Day-1 certified; tag `v6-candidate-90f1cb4` protects this tip | `NEXT_GATE_D0D8990.md` §17/§17b |
| `d0d8990` | 4,522/4/16/7 | 861/0/14 | FAIL (narrow) — 4 intended-behaviour test-expectation misses, zero product regressions; fixed test-only @ `90f1cb4` | `NEXT_GATE_D0D8990.md` |
| `2479942` (V5) | 4,262/0 | 854/0 | GATE GREEN | `NEXT_GATE_357628A.md` |
| `4537b84` (V4 source) | 4,227/0 | 846/0 | GATE GREEN | `NEXT_GATE_4537B84.md` |
| `49a134e` (V3 source) | 3,800/0 | 838/0 | GATE GREEN | `NEXT_GATE_49A134E.md` |

Gate discipline notes: (1) the gate's "11-error tsc baseline" is a
PRISTINE-snapshot artifact (tsc runs BEFORE prisma generate;
`frontend/src/generated/prisma` is git-ignored) — with the generated
client present, tsc on `5ea71bf` reports 0 errors; both are the same
clean verdict. (2) A runtime restart drops apt binaries — assert
`which tesseract` before a gate's Q1 (5.3.4 was present in the
`5ea71bf` gate logs). (3) Repository unit tests MOCK the DB session —
the live-PG SQL-execution tier below is the safety net that closes
that gap. (4) Any lane changing an LLM prompt or a fallback chain
must re-derive/re-pin request-shape goldens in the same lane.

## PostgreSQL tier (live, real PG 16.15 — from the `5ea71bf` gate)

- SQL-exec family, LIVE PG: **135 passed / 7 xfailed / 0 skipped**
  (default-env run: 123 passed / 12 skipped / 7 xfailed).
- Editor roundtrip on a fresh pristine DB: **2 passed / 0 failed**
  (the `7959ad7` roundtrip fix holds; the gate instruction stands:
  the roundtrip suite runs WITH a live DB, on a fresh pristine DB,
  in every final gate).
- Selection-eval harness (Q6): **19 passed / 0 failed**. Golden
  request-shape solo: 11/0. Migration safety solo: 1 passed.
  Inventory guard solo: 6 passed. Non-unit Q5: 12 passed / 220
  skipped (environment-bound integration files, no PG wired into
  that leg — same as baseline).

## Acceptance + rendered evidence on the tip (not suites)

- **Four-mode acceptance re-run on `5ea71bf` (2026-11-15):** 32/32
  exports, all ffprobe-verified 1080×1920 h264+AAC (manifest:
  `exports_on_tip_manifest.csv`). Manual Cutter × Plain/Auto PASS
  outright; AI combos PASS on the degraded path (keyless sandbox —
  pipeline/honesty proof, NOT AI-quality proof). Plain source maps
  100% `plain` (31/31, 159/159, 34/34, 34/34); audio xcorr 0.9993 /
  0.9917 / 0.9992 (no music bed); Auto contrast 0.8008 + 13-key
  ClipPlans. F-R1 + F-R2 confirmed FIXED at rendered level.
  Persistence-across-restart leg PASS (byte-identical exports after
  a PG restart); checkpoint resume through a real VM restart: 10/10
  clips. New defects: NONE. Source: `REAL_EXPORT_ACCEPTANCE_ON_TIP.md`.
- **Rendered-proofs batch on the tip (2026-11-15 ~00:31 IST): 5/5
  PASS** — F-R1 pick-pair distinctness, F-B stored caption Top
  survives re-renders (ASS median 0.1557), F-A Reference Style look
  renders at its measured height (0.6359, byte-identical to the
  explicit control), F-1 cache legs incl. the poison leg, F-R2
  degraded-slate hygiene. Source: `RENDERED_PROOFS_BATCH_NOV14.md`.
- **N4/N6 rendered proof: PASS** — words-per-line 2 vs 6 changes the
  rendered ASS event word counts and nothing else in the EDL;
  Montserrat-Black / ExtraBold renders stamp the real font names (no
  silent fallback). Source: `N4_N6_RENDERED_PROOF.md`.
- **N3/regen A/B proof: PASS** — vocals-stem suppression 74.8 dB on
  original render, re-render AND regeneration; voice clips preserved
  (0.3 dB). Caveat: proves stem THREADING with a ground-truth stem,
  NOT real Demucs separation quality (user-machine).
- **Caption-position evidence:** Top/Middle/Bottom rendered centres
  0.1549 / 0.4951 / 0.7549; no-request renders byte-identical pre/post
  fix. **Export-honesty evidence:** D3/D4/D5 proven with re-hashed
  artefacts. Editor audit re-run: 69 WORKS / 0 / 0 / 0; independent
  verification 90/91 rows CONFIRMED (1 drift = N1, claim narrowed).
- **V8 package verification (on the BUILT ZIP):** zip_assertions
  final mode **38 passed / 0 failed / 0 warnings**, exit 0; manifest
  **14/14 PASS** item-by-item against the ZIP's own listing;
  PowerShell 7.4.6 parse OK ×3 scripts; fail-backup rehearsal PASS;
  verify expectation re-derived = **167** (19 + 48 + 48 + 52);
  determinism: built twice → identical SHA-256
  `7807b4576c7d562eeba8d7453609351580ee99c4fa185ca0bfcf609f860145c5`.
  Source: `V8_PACKAGE_REPORT.md`.

## Lane-level proof on the UNMERGED branches (not a gate)

All runs: sanctioned venv (pytest 9.0.3), `OMP_NUM_THREADS=2`,
`--no-cov`, from the branch worktree. Full suite on any branch:
**NOT RUN** — the full gate belongs to the merged tree.

| Branch (tip) | Result | Notes |
| --- | --- | --- |
| `fix/floor-export-parity` (`f7f66a1`) | New suite 7 + extended B-roll toggle (27-case gate matrix + 4 seams) = **61 PASS / 0 FAIL** (run twice incl. final tree); neighbours **137 PASS / 1 skipped**; creation modes + duration + clamp **112 PASS**; changelog vitest 4 PASS | Rendered before/after through `create_single_clip`: BEFORE ASS = 11 caption events, no title; AFTER = same 11 + 1 Hook event from the segment's grounded first sentence. One creation-modes attempt was killed mid-run by a runtime restart with no summary — not counted, re-run from scratch. |
| `feat/recipes-foundation` (`74381f8`) | Foundation **55 PASS**; wiring **26 PASS** (parent re-run of both on the committed tip: 81/0); autopilot **36 PASS** (parent re-run 36/0); neighbours 168/1sk + 121/1xf + brand-kit 46 + broll-toggle 39 (lane runs) | Migration applied on a pristine DB through the runner's split rule twice (idempotent) in the foundation suite. Live-PG tier NOT RUN for wiring/autopilot. Changelog vitest 4 PASS (wiring lane). |
| `feat/learning-engine-backend` (`4582a0f`) | New end-to-end suite **5 PASS**; family total **50 PASS / 0 FAIL** | Real service + real repository SQL (SQLite via the sql_exec harness). Live-PG tier NOT RUN (established pattern). Test-only branch — no production code changed. |
| `refactor/broll-consolidation` (`fd6e432`) | **846 PASS / 1 skipped / 0 FAIL** (baseline on pristine `5ea71bf`: 373 PASS in the same families); changelog vitest 4 PASS; app smoke 162 routes / 136 paths | Rendered proof pair: stored v2 placement 0 overlays BEFORE → 1 overlay (the actual stock frame) AFTER. One baseline batch was killed by a runtime restart at ~67% with no summary — discarded, re-run from scratch. Live-provider behaviour NOT VERIFIED (no keys). |
| `feat/retention-editing` (`d1f35f0`) | Finisher runs: 566 PASS / 1 FAILED (the one failure = lane-caused stale pin, proven by running the identical test at base `5ea71bf` — 1 passed there). Re-pin micro-lane: **174 + 345 + 222 = 741 PASS / 0 FAIL** (3 stale pins total, each proven stale before re-pinning; no test weakened) | Rendered proof PASS (provider pool stubbed via MockTransport, 2 assets — the established provider-boundary pattern): dedup 101 vs 102, slide→cut with no xfade in the render graph, push-in face-width ratio 1.231 vs 1.080 natural. The `not_inserted` stamps are proven by unit tests, not by the rendered proof (its fixture delivers both cues). |
| `perf/rtx4050-render-path` (`19bd1e7`) | **170 PASS / 0 FAIL** across five targeted batches (59 + 27 + 24 + 19 + 41); changelog vitest 4 PASS | Quality-constant and encoder-args pins unchanged and passing (the change is scheduling/threads + diagnostics only). One initial failure in the lane's own new tests — fixed properly, full batch re-ran PASS. On-GPU effect NOT VERIFIED (sandbox has no GPU). |

## What is NOT proven (say it plainly)

- **AI-selection quality with providers live: NOT PROVEN anywhere.**
  The sandbox holds no provider keys by design; every AI-mode result
  above is the degraded path, honestly stamped. Live proof is
  user-machine validation (his keys, his install).
- **Live model bake-off: NOT RUN.** Harness + fixtures + protocol
  only (35/35 harness tests). No winner named or wired.
- **Full suite on the merged (post-queue) tree: NOT RUN** — it cannot
  exist until Luffy's V8 decision closes the release and the merge
  batch lands. The merged tree then needs its own full gate +
  verify-expectation re-derivation (Recipes adds 2 migrations → 50).
- **On-GPU behaviour** (NVENC probe pass/fail on his RTX 4050, Eco vs
  normal timings, NVDEC benefit): NOT VERIFIED — code + unit proof
  only; the benchmark kit is the owed instrument.
- **Real Demucs separation quality:** NOT PROVEN (stem threading is).
- **Live-provider B-roll behaviour** (real Pixabay/Coverr pools,
  veto calibration of the concept-phrasing fix): NOT VERIFIED in the
  sandbox.
- **His real zero-insert run's root cause:** retroactively
  UNANSWERABLE (records predate stamping) — the retention branch
  makes every future run self-explaining.
- Package rehearsals were run on the shipped V8 scripts in the
  sandbox (parse + fail-backup); a real install has NOT run — V8 is
  NOT APPROVED and nothing has been installed from it.
