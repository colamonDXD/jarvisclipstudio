# Merge queue items 14+15 + targeted re-gate — report (2026-11-16)

Repo `~/workspace/jcs-next`, branch `next-milestone`.
Base tip: `b05e6da` (13-branch batch + full gate PASS).
**Final tip: `40f5407`.** Nothing pushed. Tag `v6-candidate-90f1cb4` verified at
`90f1cb41b0837a04e4e772ee006857938a7db2fb` before and after. Reference-Styles bridge
files: 0-line diff vs `61ad303` before and after.

## 1. Merges

| Item | Branch | Merge commit | Conflicts and resolution |
|---|---|---|---|
| 14 | `feat/trailer-breakdown` @ `c3dc561` | `3dfd817` | `hook_title_styles.py` (9 hunks), `test_hook_title_styles.py` (exact-set pin), `CHANGELOG.md` — all resolved by UNION. The styles file was verified line-complete against BOTH parents: the only lines absent from either side are the two superseded registry comments, replaced by the merged "D15/D16/D17 candidates" line. The shared-body `*_available` functions were rebuilt whole (mistake_fix + list_fill from HEAD, trailer_breakdown from the branch) rather than interleaved. |
| 15 | `feat/framing-mode` @ `e2ac8f1` | `40f5407` | `clip_service.py` (3 hunks), `video_service.py` (3), `video_utils.py` (5), `CHANGELOG.md` — additive adjacency throughout: `framing_mode` now sits alongside `task_text_bands` / `allowed_ranges` / `excluded_ranges` in every signature, docstring and call site; the two partial conditional-kwarg blocks were each completed (`else {}` + `),`) before stacking the next. ONE deliberate exception, recorded in the merge commit: in `clip_service`'s re-render read, the framing branch's older two-argument `include_broll` statement was superseded by the floor lane's `resolve_broll_gate` form (merge item 1); the framing validation block itself was kept in full. |

Migrations: **51** (`20261116_0001_task_framing_mode.sql` added by item 15).

CHANGELOG vitest (`frontend/src/lib/changelog.test.ts`): **4 passed** after merge 14,
after merge 15, and again on the final tip.

Per-merge family runs (pre-commit, on the merged working trees):
- Item 14 (after the §2 fix): trailer + freeze + styles + mistake_fix + list_fill +
  eco families — **112 passed / 0 failed**.
- Item 15: framing suite + all reframe/scene families + broll-toggle + settings
  integrity matrix + task insert columns + settings readback —
  **305 passed / 1 skipped / 0 failed**. No fixture-column fallout occurred (the
  framing branch had already updated its fixture DDLs).

## 2. NEW DEFECT FOUND AND FIXED — explicit encoder `-threads` silently kills zoompan zooms

**Commit `f49da36`** (`fix(render): …`), between the two merges.

### Symptom
On the item-14 merged tree, the D17 lane's rendered proof
(`test_trailer_breakdown_render.py`) FAILED: the freeze punch-zoom was absent from
the exported frames (frame-diff mean 0.034 vs the asserted > 0.3), while the same
test PASSED on the D17 branch tip alone (A/B, same harness, same sandbox).

### Attribution (bisect + isolation, all logs in `~/workspace/merge-1415-logs/`)
- D17 merged onto successive batch tips: item-1 tip PASS, item-3 tip FAIL,
  item-5 FAIL, item-9 FAIL → the change enters with items 2–3; item 2 is tests-only,
  item 3 is `perf/rtx4050-render-path`.
- On the item-3 tree, disabling the perf lane's thread bound restored PASS.
  Flag isolation: `-filter_threads 4` alone → PASS; output `-threads 4` alone →
  FAIL. Manual single-command A/B on the recorded render argv (identical input,
  identical filter graph): zoom present without `-threads`, absent with it.
- Value sweep: `-threads 1`, `-threads 2`, `-threads 4` and
  `-x264-params …:threads=4` ALL break it identically (mean 0.0178, over8 588);
  no explicit encoder thread setting → zoom renders (mean 0.3669, over8 45,693).
  Frame scan confirms the zoomed window (frames 90–119) is simply never zoomed
  under any explicit encoder thread count on this ffmpeg (8.1.2).

### Scope
The mechanism is zoompan's `on`-counter expressions — the same mechanism behind
`build_punch_in_filter` (profile punch-in zooms) and `build_kenburns_filter`
(the retention lane's opening push-in). On any tree containing the perf merge,
**every planned zoom silently rendered flat** while all stages reported success.
The perf lane's premise "thread counts are scheduling only" is therefore false
for encoder threads on this ffmpeg; its filter-thread half is unaffected.

### Fix
`eco_render_command` (`backend/src/media/ffmpeg.py`) no longer inserts the output
`-threads` option. `-filter_threads` bounding (the perf lane's CPU bound for the
filter graph) and Eco mode's tighter bound + nice prefix are unchanged; encoder
threads stay at the encoder default. The evidence is recorded in a comment block
in `ffmpeg.py`. Command-shape pins re-pinned to the deliberate new shape in
`test_eco_render_mode.py` (11 assertions), `test_edl_av_sync.py` (2) and
`test_edl_render_audio.py` (1) — no assertion weakened; each now asserts
`"-threads" not in …` or the exact new command tail. A CHANGELOG "Fixed" entry
was added (landed in the item-14 merge commit; the fix commit body records this).
The D17 rendered proof is the standing regression test.

### Also re-pinned (union rule)
`test_trailer_breakdown.py::test_style_is_registered_additively` — the lane's own
exact-set pin predated the D15/D16 registrations; unioned to include
`mistake_fix` + `list_fill` (proof: the deliberate D15/D16 commits on the merged
tree). Included in the item-14 merge commit.

## 3. Targeted re-gate — final tip `40f5407`

Environment asserted first: `tesseract 5.3.4` present at `/usr/bin/tesseract`;
sanctioned venv pytest 9.0.3; suites serial; logs in
`~/workspace/merge-1415-logs/gate/`. Scratch PostgreSQL 16.15 on port 55434 with
FRESH databases (`sql_exec_tier`, `roundtrip_gate` dropped and recreated).

| Leg | Result | Baseline (`b05e6da`) | Log |
|---|---|---|---|
| Trailer suites (breakdown, render, freeze, hook styles) | **30 passed / 0 failed** | — (new) | gate-trailer.log |
| Framing suite + reframe/scene families (+ broll toggle, settings matrix, insert columns, readback) | **305 passed / 1 skipped / 0 failed** | — (framing new) | m15-framing-families.log |
| Creation modes + settings/readback families | **116 passed / 1 skipped / 0 failed** | — | gate-creation-settings.log |
| Migration safety + pristine schema solos | **3 passed / 0 failed** | 1 + (pristine in Q4) | gate-solo-migration-pristine.log |
| SQL-exec family, default env | **126 passed / 0 failed / 12 skipped / 7 xfailed** | 126 / 12 sk / 7 xf — IDENTICAL | gate-sqlexec-default.log |
| SQL-exec family, LIVE PG | **138 passed / 0 failed / 7 xfailed** | 138 / 7 xf — IDENTICAL | gate-sqlexec-pg.log |
| Editor roundtrip, LIVE PG on a FRESH pristine DB | **2 passed** | 2 | gate-roundtrip.log |
| Re-pinned command shapes (edl_render_audio, edl_av_sync, eco) | **63 passed / 0 failed** | — | gate-repinned-shapes.log |
| Changelog vitest | **4 passed** | 4 | gate-changelog-vitest-final.log |
| Smoke (create_app route census) | **168 routes / 139 distinct paths** | 168 / 139 — IDENTICAL | gate/smoke.log |

Direct evidence on the fresh roundtrip DB: `tasks.framing_mode character
varying(10)` present; `schema_migrations` holds exactly **51** rows — the framing
lane's owed live-PG tier is discharged.

SQL-exec canonical counts did NOT shift with the framing column (the branch had
already carried the fixture columns); both legs are count-identical to the
13-branch gate.

### Verify expectation recount
Re-derived from the tree with the V8 `verify.ps1`'s own `$featureFiles` list:
**173 = 19 base + 51 migrations + 51 (second pass) + 52 feature files**; all 52
listed paths present on the merged tree, 0 missing. The feature-file count stays
52 because neither new module (`trailer_breakdown.py`, `framing_mode.py`) is in
verify.ps1's curated list — adding one is a packaging decision (it would make
the expectation 174), NOT taken here.

## 4. Standing checks and housekeeping
- Tag `v6-candidate-90f1cb4` → `90f1cb41b0837a04e4e772ee006857938a7db2fb` ✓ (start and end).
- Bridge files vs `61ad303`: 0-line diff ×4 ✓.
- Nothing pushed ✓. Tracked tree clean at `40f5407` ✓.
- Untracked `backend/.drawtext-*` test residue removed. Note: `backend/.drawtext-7329/`
  contains one TRACKED file (committed accidentally by the item-5 retention merge in
  the previous batch); it was left exactly as found — removing it is a separate
  decision for the parent.
- Four scratch bisect worktrees (`jcs-bisect1/3/5/9`) removed.

## 5. Verdict
**Targeted re-gate: PASS on `40f5407`** — every leg green on the final merged tip,
with one real defect found, attributed, fixed and regression-pinned along the way.
No package was built (packaging is a separate step). Verify expectation for a
package from this tip: **173**.
