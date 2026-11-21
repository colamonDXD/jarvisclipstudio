# Merge — Face-Evidence Stack + Frontend Branches, with Targeted Re-Gate

**Run:** merge coordinator lane, 2026-11-17, after the Nov 17 integration verification (`INTEGRATION_NOV17.md`, VERIFIED @ `20f903b`).
**Tree:** `~/workspace/jcs-next`, branch `next-milestone`.
**Before:** `20f903b`, clean. **After:** **`327886b3`**, clean.
**Verdict: MERGED + RE-GATED (targeted).** Every leg run passed with zero test failures, except the frontend full-vitest file-level result, which carries 4 PGlite integration-suite hook timeouts **proven pre-existing/environmental by A/B** (identical failure on pre-merge code; each file passes solo). No test was weakened, skipped, or deleted. No package, no ZIP, no upload, no push.

## 1. Pre-state (re-asserted by this run, not quoted)

| Check | Result |
|---|---|
| Tip / branch | `next-milestone` @ `20f903b`, working tree clean — matches the brief |
| Tag `v6-candidate-90f1cb4` | `^{commit}` = `90f1cb41b0837a04e4e772ee006857938a7db2fb` — intact |
| Ancestor check | `20f903b` IS an ancestor of `04975362` (clean fast-forward, 22 commits, linear stack `1e061c4 → 2c76085 → 48c6341 → ca1afee → 74bb15fa → 04975362`) |
| Frontend branches | `feat/frontend-api-proxies` @ `e4be46a9`, `feat/frontend-framing-picker` @ `9e8257ec` — both as briefed |
| Migrations | 51 files in `backend/src/migrations/sql/` |
| Tesseract | `/usr/bin/tesseract`, 5.3.4 — present (no restore needed this run) |

## 2. Merge 1 — face-evidence stack (fast-forward)

`git merge --ff-only 04975362`: `20f903b → 04975362`, 22 commits, 19 files changed (+2,387 / −32). New: YOLOX person model + licence, `person_detection.py`, reframe/scene-context changes, 6 new test files. **No migrations → migration count still 51 → verify expectation stays 174.** Tag intact after the merge; tree clean.

## 3. Targeted re-gate on the post-stack tip `04975362`

Harness: sanctioned venv `~/workspace/jcs-modes/backend/.venv` (pytest 9.0.3), serial, `OMP_NUM_THREADS=2`, `HF_HUB_OFFLINE=1`. Runner + logs in `~/workspace/merge-stack-logs/`.

| Leg | Result on `04975362` | Reference | Verdict |
|---|---|---|---|
| (a) Stack's new test files (6 files: bright, YOLOX, Haar gate, min-area floor, depicted grid, legacy-chain gate) | **71 passed / 0 failed** (`leg-a-stack-new.log`) | 71 contract tests (legacy-fix record) | **PASS**, exact match |
| (b) Framing families — 24 files re-derived per the integration report's description (framing suite + reframe ×10 + scene ×3 + caption ×3 + editor ×2 + speaker framing + broll toggle + settings matrix + insert columns + readback) | **412 passed / 1 skipped / 0 failed** (`leg-b-framing-families.log`) | 399/1sk (integration run, its own re-derived set) | **PASS** — +13 is file-set composition (the stack modified no existing test file; the integration run's exact list was not recorded — same delta class as its finding F3); 0 failed, same skip count |
| (c) Smoke (create_app route census) | **168 routes / 139 distinct paths** (`smoke.log`) | 168/139 | **PASS**, exact match |
| (d) Changelog vitest | **4 passed / 0 failed** (`fe-changelog-poststack.log`) | 4/4 | **PASS**, exact match |

## 4. Merges 2–3 — frontend branches

Both branches were cut from `20f903b`; the only file either shares with the stack is `CHANGELOG.md`. Both merges conflicted on `CHANGELOG.md` only; all code files merged cleanly. Conflicts resolved by **UNION** (all entries kept, single `## 2026-11-17` heading), changelog vitest re-run after each resolution (4/4 both times — `fe-changelog-merge1.log`, `fe-changelog-merge2.log`).

| Merge | Commit | Content | CHANGELOG union result |
|---|---|---|---|
| `feat/frontend-api-proxies` (`e4be46a9`) | **`a839ce8b`** | `/api/learning/[...path]` + `/api/recipes` root/`[...path]` browser proxies + tests (7 files, +457) | 2026-11-17 section: 4 stack entries + 1 proxies entry = 5 |
| `feat/frontend-framing-picker` (`9e8257ec`) | **`327886b3`** | Create-page framing control (Auto / Fit / Crop) + `framingMode` strings in 6 locale files + tests (9 files, +185) | 2026-11-17 section: 5 + 1 picker entry = 6 |

`jcs-next/frontend/node_modules` is a real directory (the worktrees symlink into it), so no symlink treatment was needed in `jcs-next` itself; `src/generated/prisma` present for tsc/build.

## 5. Frontend legs on the merged tip `327886b3`

| Leg | Result | Verdict |
|---|---|---|
| Branches' new tests (4 files) | **14 passed / 0 failed** — 10 proxies + 4 framing picker (`fe-new-tests.log`) | **PASS**, exact match with both branch records |
| Full vitest | **913 passed / 14 skipped / 0 tests failed; 4 test FILES failed** (`fe-full-vitest.log`) | **FAIL (file level) — attributed, see §6.** Test arithmetic is exact: 899 @ `b05e6da` + 14 new = 913 |
| tsc (`tsconfig.tsbuildinfo` deleted first) | **exit 0 — 0 errors** (`fe-tsc.log`, empty) | **PASS** |
| Eslint on changed files (8 src files, `--max-warnings 0`) | **exit 0 — clean** (`fe-eslint-changed.log`, empty) | **PASS** |
| Production `next build` (`prisma generate` + `next build`; `pnpm` is not on PATH in this shell, so the script's two steps were run from `node_modules/.bin`) | **exit 0** — full route table incl. `/api/learning/[...path]`, `/api/recipes`, `/api/recipes/[...path]` (`fe-next-build.log`) | **PASS** |

## 6. Finding F1 — 4 PGlite integration files fail on hook timeout: PRE-EXISTING, ENVIRONMENTAL (A/B proven)

**What:** `src/server/{affiliates, announcements, service-status, user-acquisition}.integration.test.ts` fail in `beforeAll` with "Hook timed out in 10000ms" while initializing PGlite (WASM Postgres) and executing `init.sql`/migrations. In the full run all 4 failed; **zero tests failed** anywhere (913 passed).

**Attribution evidence:**
1. `git diff 20f903b..HEAD -- frontend/src/server/` is EMPTY — neither the stack nor the frontend merges touched these files or anything they import.
2. A/B re-run of the same 4 files in the pre-merge worktree `~/workspace/jcs-frontend1` (@ `e4be46a9`; `src/server` verified byte-identical via `diff -r`): **identical result** — 3 failed / 1 passed, 2 passed / 12 skipped (`fe-pglite-ab-premerge.log` vs `fe-pglite-ab-standalone.log`).
3. Solo run of `affiliates.integration.test.ts` on the merged tip: **3/3 PASS in 4.6 s** (`fe-pglite-affiliates-solo.log`) — the suite passes when PGlite initializes without concurrency.
4. Mechanism: 4 PGlite instances initializing concurrently on this VM (1.5 shared CPUs) exceed the 10 s hook timeout; solo init takes ~4.6 s. At the `b05e6da` gate (899/0) the same files passed — machine headroom differed, code did not.

**Disposition:** not a merge regression; no test weakened, skipped, or deleted; no code changed. The full-vitest leg is recorded **FAIL (file level, environmental, pre-existing)** rather than passed over. A future full re-gate on a quieter machine, or a serial PGlite run, is expected to clear it; if it ever fails solo, it becomes a real finding.

## 7. Final state

| Check | Result |
|---|---|
| Tip | `next-milestone` @ **`327886b3`**, working tree clean |
| Tag | `v6-candidate-90f1cb4` → `90f1cb41b0837a04e4e772ee006857938a7db2fb` — intact |
| Migrations | **51** (unchanged) |
| Smoke at final tip | **168 routes / 139 distinct paths** (`smoke-final.log`) — PASS |
| Verify expectation | **174** (unchanged: no migrations added; frontend merges touch no verify-list file) |
| History | `20f903b` → FF `04975362` → merge `a839ce8b` (proxies) → merge `327886b3` (framing picker) |

## 8. NOT RUN / still owed (explicitly)

- **Full backend re-gate on the new tip** — Q1–Q4 quarters + Q5 + Q6: **NOT RUN** in this lane (separate later step, per the lane brief). The backend legs of the Nov 17 integration run remain STALE for `327886b3` beyond the targeted families re-run here (the stack's delta is confined to `backend/src/media/` + its 6 test files; the frontend merges touch no backend code).
- SQL-exec / roundtrip / migration-pristine legs: not re-run (no migration or SQL change in either merge; last verified on `20f903b` by the integration run).
- Campaign-brief render re-run (R-B) on the merged tree: still owed (integration report §8 item 3).
- V9 install verify (174) on Luffy's machine: still awaited; V9 does NOT contain the stack or the frontend merges (built from `20f903b`).

## 9. Logs

All under `~/workspace/merge-stack-logs/`: `leg-a-stack-new.log`, `leg-b-framing-families.log`, `smoke.log`, `smoke-final.log`, `fe-changelog-poststack.log`, `fe-changelog-merge1.log`, `fe-changelog-merge2.log`, `fe-new-tests.log`, `fe-full-vitest.log`, `fe-pglite-ab-standalone.log`, `fe-pglite-ab-premerge.log`, `fe-pglite-affiliates-solo.log`, `fe-tsc.log`, `fe-eslint-changed.log`, `fe-next-build.log` (+ `grun.sh`, `smoke.py`).
