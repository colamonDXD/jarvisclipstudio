# Full Re-Gate — `327886b3` (closes INTEGRATION_NOV17 §8 item 2 / F4)

**Run:** full re-gate lane, 2026-11-17 ~11:18–12:41 IST. **Tree:** `~/workspace/jcs-next`, branch `next-milestone` @ **`327886b3`**, clean at start and at end. Tag `v6-candidate-90f1cb4` → `90f1cb41b0837a04e4e772ee006857938a7db2fb` intact. Migrations **51**; verify expectation **174** (unchanged).
**Harness:** sanctioned venv `~/workspace/jcs-modes/backend/.venv` (pytest **9.0.3**), serial suites, `OMP_NUM_THREADS=2`, `HF_HUB_OFFLINE=1`, batch-gate runner recipe (watchdog kills each suite by exact PID once its summary line is on disk). Logs: `~/workspace/full-regate-logs/`. Frontend full vitest was **not** re-run (already done on this tip by the merge-stack lane: 913 passed / 0 test-failed, 4 PGlite file-level environmental failures A/B-attributed pre-existing).
**Environment:** tesseract 5.3.4 present at `/usr/bin/tesseract` (asserted first, no restore needed). The scratch PG 16.15 cluster had been wiped by a runtime reset (binaries survived at `/usr/lib/postgresql/16`); a fresh cluster was initdb'd and started on 127.0.0.1:55434 with fresh `sql_exec_tier` + `roundtrip_gate` databases (environment only, per INTEGRATION_NOV17 §6 F1's recipe).

**Overall verdict: PASS with 3 attributed failures — zero regressions from the face-stack or frontend merges.** Every failure is A/B-attributed below: two are test-side staleness introduced by the items 14/15 (framing-mode) merge era and reproduce identically on the pre-stack tip `20f903b`; one is an artifact of this run's own harness deviation (generated dir present) and passes under the certified gate condition. No test was weakened, skipped, or deleted. No product code or test was touched.

## 1. Legs run on `327886b3`

| Leg | Result on `327886b3` | Reference | Verdict |
|---|---|---|---|
| Q1 (batch chunk split, 80 files) | **1 failed, 1292 passed** (727 s) | 1293/0 @ `b05e6da` | **FAIL** — 1 failure, attributed (F1) |
| Q2 (batch chunk split, 77 files) | **1 failed, 1233 passed** (1213 s) | Q2r: 1 failed (OCR transient), 1231 passed, 2 skipped @ `b05e6da` | **FAIL** — 1 failure, attributed (F2); the reference transient now passes |
| Q3 (batch chunk split, 80 files) | **1 failed, 1129 passed, 2 skipped** (1816 s) | 1130 passed, 2 skipped @ `b05e6da` | **FAIL** — 1 failure, attributed (F3, this run's harness condition) |
| Q4 (batch chunk split, 78 files) | **1285 passed, 14 skipped, 7 xfailed** (524 s) | 1285/14sk/7xf @ `b05e6da` | **PASS**, exact match |
| Q-new (10 unit files added after the chunk split froze: 6 face-stack + `test_framing_mode` + `test_trailer_breakdown` + `test_trailer_breakdown_render` + `test_edl_freeze`) | **131 passed / 0 failed** (156 s) | stack 6 files = 71 (merge lane, on `04975362`); 14/15 files covered by integration legs on `20f903b` | **PASS** |
| Migration-safety + pristine-schema solo (`test_migration_files_safety.py` + `test_pristine_schema_defaults.py`) | **3 passed / 0 failed** | 3/0 (integration run) | **PASS**, exact match |
| Golden pins solo (`test_ai_long_video_resilience.py`; also inside Q1's chunk — run explicitly as a named leg, as the batch gate did) | **11 passed / 0 failed** | 11/0 | **PASS**, exact match |
| SQL-exec family, default env (batch 11-file set incl. `test_task_insert_columns.py`) | **126 passed, 12 skipped, 7 xfailed / 0 failed** | 126/12sk/7xf (batch re-run set); integration's 10-file set gave 124/12sk/7xf | **PASS**, exact match on the 11-file set |
| SQL-exec family, LIVE PG 16.15 | **138 passed, 7 xfailed / 0 failed** | 138/7xf (batch re-run set); integration 10-file set: 136/7xf | **PASS**, exact match on the 11-file set |
| Editor roundtrip, LIVE PG on a FRESH pristine DB (`roundtrip_gate` dropped + recreated immediately before the leg) | **2 passed**; psql proof: `schema_migrations` = **51** rows; `tasks.framing_mode` = `character varying(10)` (`roundtrip-psql-proof.txt`) | 2/0, same proof @ `20f903b` | **PASS**, exact match |
| Q5 non-unit (`tests/integration tests/live tests/test_video_utils_diarization.py`) | **12 passed, 220 skipped / 0 failed** | 12/220sk @ `b05e6da` | **PASS**, exact match |
| Q6 selection-eval (`../tools/selection-eval/tests`) | **19 passed / 0 failed** | 19 @ `b05e6da` | **PASS**, exact match |

**Backend unit total on this tip (Q1–Q4 + Q-new): 5070 passed / 3 failed / 16 skipped / 7 xfailed** (5096 collected). With Q5 + Q6: **5101 passed / 3 failed**.

## 2. Count-delta reconciliation vs the `b05e6da` quarters (4,939 passed)

Collection is identical for the chunk split (4965 tests both times); every delta is explained per quarter:

- **Q1 (1293 collected, both):** 1293p/0f → 1292p/1f. The billing test moved pass→fail (F1).
- **Q2 (1234 collected, both):** Q2r 1231p/1f/2sk → 1233p/1f/0sk. The reference failure (`test_ocr_unreadable_bytes`, a tesseract-absence transient at batch time) now **passes** with tesseract 5.3.4 present (+1); the 2 tests skipped in Q2r now run and pass (+2, by arithmetic: collection identical, all other movements accounted); the recipe byte-identical pin moved pass→fail (F2, −1). Net +2 passed.
- **Q3 (1132 collected, both):** 1130p/0f/2sk → 1129p/1f/2sk. The cloudflare generated-dir test moved pass→fail under this run's harness condition (F3).
- **Q4:** exact match, no delta.
- **Q-new (+131 collected):** files that did not exist when the chunk split froze — the face stack's 6 files (71, matching the merge lane's count on `04975362`) + the 4 items-14/15 files (60).

Quarters passed total is coincidentally also 4,939 (4939 = 1292+1233+1129+1285); the composition differs as itemised above, and Q-new adds 131 on top.

## 3. Findings — all three failures A/B-attributed

**F1 — `tests/unit/test_billing_allowance.py::test_task_and_charge_commit_atomically` FAILS: framing-era test-fixture staleness, pre-existing since items 14/15, NOT a stack/frontend regression, NOT a product bug.**
Error: `sqlite3.OperationalError: table tasks has no column named framing_mode` on the product task INSERT. The test file carries its own literal `CREATE TABLE tasks` fixture DDL (line 43) which history shows being updated per added column (template_mode, broll_enabled commits); the framing-mode merge added `framing_mode` to the model/INSERT but did not update this fixture. A/B (solo file runs, `ab-*.log`): @ `327886b3` 1 failed / 40 passed; @ `20f903b` (pre-stack, pre-frontend) **identical** 1 failed / 40 passed; @ `b05e6da` (pre-framing) **41 passed / 0 failed**. Product behaviour against the real schema is proven correct by the roundtrip leg (fresh DB: `framing_mode varchar(10)` present, insert path green). Fix owed (test-only, one column in the fixture DDL) — not applied by this lane per its brief.

**F2 — `tests/unit/test_recipe_create_wiring.py::test_create_without_recipe_is_byte_identical` FAILS: framing-era stale pin, same attribution class as F1.**
Assertion: the created-task dict now contains one more item than the pinned expected dict: `{'framing_mode': None}`. The pin predates the framing-mode field. A/B: @ `327886b3` 1 failed / 25 passed; @ `20f903b` **identical** 1 failed / 25 passed; @ `b05e6da` **26 passed / 0 failed**. The product change is the intended framing feature (field present, defaulting to None = auto); the test's expected dict was not extended. Fix owed (test-only) — not applied by this lane.

**F3 — `tests/unit/test_litellm_gateway_config.py::TestCloudflareRoute::test_no_app_or_ui_surface_knows_the_route_exists` FAILS under this run's harness condition only: this run's deviation, not a tree finding.**
The test walks UI surfaces and asserts the string `cloudflare` appears nowhere; with `frontend/src/generated` present it matches the Prisma-generated runtime (`.../generated/prisma/runtime/react-native.js`, which legitimately contains "cloudflare workers"). This is the batch gate's known generated-dir sensitivity (its §3a attribution), which is why the certified gate moves `frontend/src/generated` aside for the backend phase. **This run's driver attempted the same move but it failed**: the target parent `~/workspace/.tmp-regate/` did not exist when the driver started (grun.sh creates it only at the first leg), so all backend legs ran with generated present. A/B on this tip: generated present → **1 failed** (5.0 s); generated aside → **1 passed** (0.36 s) (`ab-tip-cloudflare-*.log`). Q1/Q2/Q4 are demonstrably insensitive (they passed/failed identically for unrelated reasons with generated present). Next gate: `mkdir -p` the TMPDIR before the Phase-0 move. The generated dir was restored and the tree verified clean after the A/B.

## 4. What this run did NOT do

- No merges, no package, no ZIP, no upload, no push. No product code or test edited — F1/F2 fixes are reported, not applied.
- Frontend full vitest not re-run (done on this tip by the merge-stack lane; see header).
- Campaign-brief render re-run (R-B) on the merged tree remains owed (integration report §8 item 3) — outside this lane's legs.

## 5. Final state

| Check | Result |
|---|---|
| Tip | `next-milestone` @ `327886b3`, working tree clean (gate drawtext residue removed; A/B worktrees removed; `frontend/src/generated` restored) |
| Tag | `v6-candidate-90f1cb4` → `90f1cb41b0837a04e4e772ee006857938a7db2fb` — intact |
| Migrations | 51 |
| Verify expectation | 174 (unchanged) |
| STALE legs remaining for this tip | None of the legs assigned to this lane. (Frontend vitest: done by the merge-stack lane, file-level environmental FAIL attributed there.) |

## 6. Sources

- Logs: `~/workspace/full-regate-logs/` — `q1.log`…`q4.log`, `q-new.log`, `solo-migration-pristine.log`, `solo-golden.log`, `sqlexec-default.log`, `sqlexec-pg.log`, `roundtrip.log` + `roundtrip-psql-proof.txt`, `q5-nonunit.log`, `q6-seleval.log`, A/B: `ab-tip-billing.log`, `ab-tip-recipe.log`, `ab-tip-cloudflare-present.log`, `ab-tip-cloudflare-aside.log`, `ab-20f-billing.log`, `ab-20f-recipe.log`, `ab-b05-billing.log`, `ab-b05-recipe.log`, `progress.txt`, `ab-progress.txt`, `driver.sh`, `grun.sh`, `ab.sh`, `pg-initdb.log`, `pg-server.log`.
- References: `~/workspace/merge-batch-logs/gate/` (chunk split + Q logs @ `b05e6da`), `INTEGRATION_NOV17.md` (§4, §6 F1/F2, §8 item 2), `MERGE_STACK_AND_FRONTEND_REPORT.md`.
