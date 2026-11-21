# Real-Export Smoke Acceptance — RUN ON THE EXACT V11 PACKAGED TIP

**Tree under test:** branch `next-milestone` @ **`5480a9da`**
(`5480a9da898b687dc6086e74844f91513c111063`) — the V10 tip
`901ae252` plus the post-V10 frontend merges ONLY (frontend
completion, React Bits polish, Aurora Flow shell, Studio
Console v2). Snapshot worktree: `~/workspace/jcs-accept-v11`
(git-archive extraction of the tip; backend fidelity proven by
the empty backend name-only diff and a byte-compare of
`task_service.py` against the V10 acceptance tree).

**Scope — one combo, by design.** This is a SMOKE acceptance:
**Full AutoClip + Plain**, end-to-end. The full eight-combo
acceptance was **NOT re-run** for V11, for one stated reason:
the backend code on this tip is **byte-identical** to the V10
tree (`901ae252`) that passed the full acceptance **8/8**
(`reports/REAL_EXPORT_ACCEPTANCE_V10.md`) — the name-only diff
`901ae252..5480a9da` filtered to backend paths is empty, and
migrations are unchanged at 51. Re-running all eight combos
would re-prove an unchanged backend at ~2.5 hours of sandbox
render time; the smoke combo instead proves the packaged
tree's real code path (create → process → render → export)
works end-to-end on this exact tree, and the frontend gate
(`reports/FRONTEND_GATE_5480A9DA.md`) covers the changed
surface. The 8/8 PASS of record for this backend is the V10
acceptance.

**Method:** identical harness to the V10 acceptance — the same
driver, the same Japan fixture
(`~/workspace/qm-source/source.mp4`), the real product pipeline
(`TaskService.create_task_with_source` → fresh-session readback
→ `TaskService.process_task` with worker-identical args)
against a real scratch PostgreSQL 16.15 (51 migrations on a
fresh database), every provider key unset — the designed
degraded (D-1 floor) path, honestly stamped. This proves
pipeline, settings and rendering on the exact build. It is
**not** AI-quality proof; that remains user-machine validation,
exactly as for the V8/V9/V10 acceptances.

## Result

| Item | Verdict |
|---|---|
| Full AutoClip — Plain (create → process → export) | **PASS — degraded path** (10 clips) |
| Export geometry | **10/10 at 1080×1920, h264 + aac** (manifest: `exports_v11_smoke_manifest.csv`, 0 exceptions) |
| Plain honesty (source-map reframe shots) | **PASS** — **158/158 shots `plain`**, numerically identical to the V10 acceptance's fullauto_plain count |
| Settings readback (fresh session, real serializer) | **PASS** — `template_mode=plain`, `music_mode=off`, `creation_mode=full_auto` |
| Frame judgement (extracted frames, read directly) | **PASS** — clip 1 @5s: speaker large and centred in a tight face-tracked 9:16 crop, no overlays; clip 3 @15s: full-bleed close-up crop, no letterbox, no B-roll/template inserts |

- Task `55cea4df-…`, wall **1,629.7 s**, final status
  `completed` (progress 100), no process error.
- The degraded-path stamps are the designed ones (visual
  selection floor; the sandbox holds no provider keys).

## Interruption during this run (environmental)

The sandbox runtime reset once during this lane, wiping the
first scratch cluster (kept in /tmp) mid-render of the first
attempt (task at 50%). The environment was restored from the
workspace-preserved PostgreSQL dist + apt (tesseract 5.3.4,
Redis, PostgreSQL 16.15 — the INTEGRATION_NOV17 F1 recipe), a
fresh database was seeded (51 migrations), and the combo was
re-run from create to completion. **Not a product failure** —
the same class of sandbox interruption the V9 and V10
acceptances recorded.

## What this smoke does NOT prove

The other seven mode/template combos on this tip (covered by
the byte-identical-backend argument above, not by a fresh
run); AI selection/editing quality with live providers (no
keys in the sandbox by design); NVENC behaviour (no GPU in
the sandbox; the encoder probe falls back to CPU, as
designed); the streamer journey on this tip (unchanged
backend path; the V10 streamer leg stands as its record).
