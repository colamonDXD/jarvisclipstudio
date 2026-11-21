# MERGE BATCH + FULL RE-GATE REPORT — 2026-11-16

**Tree:** `~/workspace/jcs-next`, branch `next-milestone`
**Base tip:** `5ea71bf` (gate-green ship candidate) → **Final tip: `b05e6da`**
**Tag `v6-candidate-90f1cb4`:** verified at batch start AND batch end → `90f1cb41b0837a04e4e772ee006857938a7db2fb` (unchanged)
**Verdict: GATE GREEN on `b05e6da`** (one transient failure, attributed with solo re-run evidence — see Q2). Nothing pushed. V9 packaging NOT built (separate step). `~/workspace/jcs-trailerbreak` (D17 lane) untouched.

Logs: per-merge families in `~/workspace/merge-batch-logs/m*.log`; gate legs in `~/workspace/merge-batch-logs/gate/*.log`.

---

## 1. The 13 merges (in brief order)

| # | Branch @ tip | Merge commit | Conflicts + resolution | Family result |
|---|---|---|---|---|
| 1 | fix/floor-export-parity @ f7f66a1 | `5f1cb34` | none (clean) | 61 passed / 0 failed (floor parity + broll toggle) |
| 2 | feat/learning-engine-backend @ 4582a0f | `61e8582` | none (clean; 1 new test file) | 37 / 0 (learning family, 4 files) |
| 3 | perf/rtx4050-render-path @ 480abe0 | `c549b7d` | CHANGELOG union (3 entries under 2026-11-15); changelog vitest 4 ✓ | 161 / 0 (13 files incl. re-derived EDL pins) |
| 4 | refactor/broll-consolidation @ f3ed6b5 | `3e25d1b` | CHANGELOG union (4 entries); video_service.py auto-merged; vitest 4 ✓ | 437 / 0 (19 files incl. classify battery) |
| 5 | feat/retention-editing @ d1f35f0 | `930158f` + chore `d0a5768` | 3 conflicts, all as rehearsed: pixabay_broll.py union (consolidation's `ensure_pixabay_hit` + retention's `not_inserted` stamping kept whole), video_service.py docstring combined, CHANGELOG union (8 entries); vitest 4 ✓. **`proof/` removed in follow-up chore `d0a5768`** per brief — artefacts preserved in `~/workspace/jcs-retention/proof/` and in branch history; removal recorded in the commit body | 283 / 0 (13 files) |
| 6 | feat/recipes-foundation @ c91458b | `ebc9555` | CHANGELOG union (11 entries); task_service.py auto-merged; vitest 4 ✓. Migrations now 50 | 191 passed / 1 xfailed / 0 failed (recipes ×3, watch-folder ×2, SQL-exec creation ×3 — the rehearsal-#1 fixture break passes; the fix was on the branch) |
| 7 | chore/litellm-healthcheck-template @ 18db75e | `ae32e58` | CHANGELOG union (12 entries); vitest 4 ✓ | 26 passed / 1 failed → **attributed pre-existing environmental** (see §3a) |
| 8 | docs/state-refresh @ 5c9bdd3 | `87795f1` | none (clean; exactly the 5 state docs, 1,021+/1,125−) | docs only, no families |
| 9 | fix/multisubject-framing @ f875004 | `41d2e0a` | CHANGELOG union (14 entries); all product code auto-merged; vitest 4 ✓ | 297 / 0 (24 reframe/scene/caption/layout files) |
| 10 | feat/task-text-bands @ 7291572 | `ba5f3ed` | none (clean — its 2026-11-16 CHANGELOG section appended without conflict); vitest 4 ✓ | 235 / 0 (13 files) |
| 11 | verify/campaign-brief-enforcement @ 259f9ea | `2fe390f` | MORE than rehearsed (item 10, absent from both rehearsals, threaded `task_text_bands` kwargs through the same functions): CHANGELOG + video_service.py (2 hunks) + video_utils.py (4) + clip_service.py (2). Every hunk examined — all pure additive adjacency, none semantic: signature/call-site kwarg unions; the two conditional-kwarg hunks resolved by completing BOTH `**({...} if cond else {})` blocks in full. CHANGELOG trap: campaign's side carried its own `## 2026-11-15` header → duplicate header broke the changelog parser; fixed by deleting the duplicate (15 entries under the single header; the 6 whole-file duplicate entry lines are pre-existing at base — identical count there). Vitest 4 ✓ | 267 / 0 (21 files — arbitrates the hand-built unions green) |
| 12 | feat/mistake-fix-hook @ e4e4141 | `3852996` | CHANGELOG only; profiles auto-merged and verified (`available_title_styles = ["problem", "mistake_fix"]`, default stays `problem`). Mistake-fix entry relocated under the existing `## 2026-11-16` header (2 entries there). Vitest 4 ✓ | 71 / 0 (5 files) |
| 13 | feat/list-fill-finance @ 25bdfea | `36a4973` | 4 files. **hook_title_styles.py (10 hunks): marker-drop union is WRONG for 4 hunks** (interleaved comment blocks, shared `),` closers, two availability functions interleaved through shared body lines, two regex compiles sharing a tail) — resolved by rebuilding the file from the mistake-fix version + the list-fill branch's additive blocks at verified anchors, then **behaviourally verified against BOTH branch originals: 15/15 checks** (style-set union, per-style guidance identical to each branch, strategies, availability on sample profiles, regex patterns byte-equal, title matching, defaults untouched). finance_business.json → `["problem", "mistake_fix", "list_fill"]` (default `problem`, templates `["list_fill_live"]`). Test pin union (both entries kept). CHANGELOG: list-fill entry relocated under `## 2026-11-16` (3 entries there). Vitest 4 ✓ | 85 / 0 (list-fill + styles + render + mistake-fix + classify battery + hook variants) |

All CHANGELOG resolutions were union (every entry kept, one header per date, no duplicates); `frontend/src/lib/changelog.test.ts` was run after every CHANGELOG resolution — 4 passed every time.

## 2. Two completion commits after the merges (gate findings, both test-only)

- **`0b20716` test(video): re-pin the fallback segment's hook title.** Q4 failed `test_video_service.py::test_process_video_complete_uses_fallback_when_ai_selects_no_segments`: the fallback segment now carries `hook_title: "[00:00 - 00:01] hello"` where the test expected `None`. Proven stale pin, not a regression: merge 1's headline commit (f7f66a1) deliberately made floor segments derive a title from their own words via `ai.sanitize_hook_title`, rendered-proof-backed, with its own suite pinning both directions. The old expectation was never updated. Assertion shape unchanged.
- **`b05e6da` test(sql-exec): execute the recipes repository statements; bump canonical counts.** The inventory guard failed on the merged tree: 39 INSERT literals vs canonical 38, and all three `recipe_repository.py` statements (create INSERT, runtime-assembled update UPDATE, delete DELETE) had no statement-execution coverage — a recipes-branch obligation the rehearsals missed because neither ran the guard. Per the guard's own prescription: added the three execution tests (extracted-text INSERT/DELETE against migration-derived DDL with owner-guard rows; the assembled UPDATE driven through ExecSession at method level) and bumped canonical counts to **39 / 69 / 18**, with the counts cited in sibling docstrings updated to match. Nothing weakened — coverage was added.

The fix commits touch ONLY six test files, all in Q4's file list (verified by diff) — Q1/Q3 gate legs therefore stand for the final tip unchanged.

## 3. Attribution record (every non-green observation)

**(a) Merge-7 family, 1 failure — pre-existing environmental.** `test_litellm_gateway_config.py::TestCloudflareRoute::test_no_app_or_ui_surface_knows_the_route_exists` walks `frontend/src` asserting no file contains "cloudflare"; it trips on `frontend/src/generated/prisma/runtime/react-native.js` (Prisma's own "cloudflare-workers" strings; the file is git-ignored generated output). A/B: the identical test FAILS on pristine `5ea71bf` under the same condition, and the family is 27/0 on the merged tree with `frontend/src/generated` moved aside. Consequence applied to the gate: backend legs ran with the generated dir moved aside (the certified gate's fresh-snapshot condition), restored before the frontend legs.

**(b) First gate attempt Q1, 15 failures — harness-environment artifact (mine).** All 15 (ai_prompt ×2, assemblyai ×8, broll ×3, broll_consolidation ×2) shared one traceback: httpx `Invalid port: ':1]'` during client construction. Full 2×2 A/B: gate-env (my CIDR `no_proxy` override) FAILS on both merged and pristine trees; ambient env PASSES on both; proxy-free env PASSES on both. Root mechanism: the tree's own `tests/conftest.py` scrubs bracketed `no_proxy` entries at import; my override defeated that path. The certified recipe predates the runtime's current injected proxy variables. Q1 was re-run under the corrected harness: **1,293 / 0**. Related fragility noted, tree untouched: unsetting ALL proxy vars trips a latent conftest `NameError` (`del _kept`) when both `no_proxy` vars are absent — it killed one driver attempt's backend legs (rc=4) before the harness was corrected; frontend legs from that attempt were unaffected and are the gate's frontend results.

**(c) Q2 first run, 5 errors — OOM.** All in `test_edl_av_sync.py`: the tests' ffmpeg renders were SIGKILLed (the log's own message: "a SIGKILL almost always means the machine ran out of memory") while the quarter ran under memory contention. Solo re-run of the file: **11 / 11 passed**. Q2 was later re-run in full on the final tip (below).

**(d) Q2 re-run, 1 failure — transient probe.** `test_content_requirements_brief_image.py::test_ocr_unreadable_bytes` expected the tesseract binary to probe as available; it probed unavailable inside that one loaded process (tesseract 5.3.4 verified present at `/usr/bin/tesseract` before and after; the same file passed in the first Q2 run and in merge 11's families). Solo re-run of the file: **19 / 19 passed**.

**(e) `test_media_replication.py`** — the documented 21-failure sandbox set did NOT reproduce in any final gate leg (the file passed inside its quarter). No A/B was needed; stated plainly so the absence is on record.

**(f) Driver kills.** The gate drivers were OOM-killed once (during Q2 attempt 1) and SIGTERM'd once (runtime restart, during Q2 re-run attempt) — in both cases completed leg logs persisted and remaining legs were re-run individually; every number below comes from a completed leg log. The scratch PostgreSQL (16.15, port 55434, fresh `sql_exec_tier` + `roundtrip_gate` DBs) was restarted after each kill.

## 4. Full re-gate results — final tip `b05e6da`

### Backend unit quarters (315 files = 298 base + 17 added by the merges; generated dir aside)

| Leg | Result | Baseline (5ea71bf) | Log |
|---|---|---|---|
| Q1 (80 files) | **1,293 passed / 0 failed** | 1,208 / 0 | gate/q1.log (driver3 run; file set unaffected by the fix commits) |
| Q2 (77 files) | **1,231 passed / 1 failed (transient, §3d) / 2 skipped** | 1,162 / 0 | gate/q2r.log + gate/attr-ocr.log (19/19) |
| Q3 (80 files) | **1,130 passed / 0 failed / 2 skipped** | 1,049 / 0 / 2 sk | gate/q3.log (file set unaffected by the fix commits) |
| Q4 (78 files) | **1,285 passed / 0 failed / 14 skipped / 7 xfailed** | 1,237 / 0 / 14 sk / 7 xf | gate/q4r.log |
| **Total** | **4,939 passed / 1 transient-attributed failure / 18 skipped / 7 xfailed** (+283 tests vs base) | 4,656 / 0 / 16 sk / 7 xf | |

### Solos and live tiers

| Leg | Result | Baseline | Log |
|---|---|---|---|
| Migration safety | 1 passed | 1 | gate/solo-migration.log |
| Golden (ai_long_video_resilience) | 11 passed | 11 | gate/solo-golden.log |
| Skips trio (separation ×2, voiceover) | 16 passed / 3 skipped | 16 / 3 sk | gate/solo-skips.log |
| SQL-exec family, default env | **126 passed / 0 failed / 12 skipped / 7 xfailed** | 123 / 12 sk / 7 xf | gate/solo-sqlexec-default-r.log |
| SQL-exec family, LIVE PG 16.15 | **138 passed / 0 failed / 7 xfailed** | 135 / 7 xf | gate/solo-sqlexec-pg-r.log |
| Editor roundtrip, LIVE PG on a FRESH pristine DB | 2 passed | 2 | gate/solo-roundtrip.log |
| Non-unit Q5 (integration + live + diarization) | 12 passed / 220 skipped | 12 / 220 sk | gate/q5-nonunit.log |
| Selection-eval Q6 | 19 passed | 19 | gate/q6-seleval.log |

### Frontend (generated client present; `frontend/` is byte-identical between 5ea71bf and b05e6da — empty diff)

| Leg | Result | Baseline |
|---|---|---|
| vitest full | **899 passed / 0 failed / 14 skipped**; 4 failed FILES = the baseline env-integration set (affiliates/announcements/service-status/user-acquisition, zero failed tests); VITEST_EXIT=1, identical to baseline | 899 / 0 / 14 sk, exit 1 |
| changelog solo | 4 passed | 4 |
| tsc (tsbuildinfo deleted, client present) | **exit 0 — 0 errors** (the brief's condition for jcs-next) | certified gate's no-client condition: 11-error baseline set |
| eslint `src` / eslint `.` | exit 0 / exit 0 | 0 / 0 |
| prisma generate | exit 0 | 0 |
| next build | exit 0 | 0 |

Logs: gate/fe-*.log.

### Smoke + counts

- **Smoke: 168 routes / 139 distinct paths** — exactly the expected post-recipes count (162/136 baseline + recipes surface). Log: gate/smoke.log.
- **Migrations on merged tree: 50** ✓.
- **Verify expectation re-derived from the merged tree: 171** = 19 base + 50 (migration pass 1) + 50 (migration pass 2) + 52 feature files — all 52 `$featureFiles` paths from the V8 verify.ps1 checked present on the merged tree, 0 missing.
- Reference-Styles bridge: `frontend/` diff 5ea71bf..b05e6da is empty, so the certified gate's 0-line bridge result carries identically.

## 5. Final state

- Final tip: **`b05e6da`** on `next-milestone`; tracked tree clean (untracked `.drawtext-*/` test residue only, documented harmless).
- Tag `v6-candidate-90f1cb4` → `90f1cb41b0837a04e4e772ee006857938a7db2fb` — verified at start and end.
- Nothing pushed anywhere. No other worktree touched.
- **Remains:** V9 packaging from `b05e6da` (verify expectation 171) — a separate later step, deliberately NOT built here. The D17 trailer-breakdown lane continues independently in its own worktree and is merge-queue item 14 for a later batch.
