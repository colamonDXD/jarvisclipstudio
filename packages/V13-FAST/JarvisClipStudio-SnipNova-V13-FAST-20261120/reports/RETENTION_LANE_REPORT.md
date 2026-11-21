# §9 — RETENTION EDITING LANE: **COMPLETE WITH ONE FAILED TEST** (finisher-verified)

Branch `feat/retention-editing` @ `f938d81` (worktree `~/workspace/jcs-retention`), base `5ea71bf`, **UNMERGED**.
Finisher audit 2026-11-15: all 5 commits read, tests re-run under the sanctioned venv, rendered proof judged from the EDL JSONs and extracted frames. `jcs-next` still at `5ea71bf`, tree clean; tag `v6-candidate-90f1cb4` → `90f1cb41…` intact; retention tracked tree clean (only untracked `proof/out/`).

## Step-0 answers (traced in the base code at `5ea71bf` + the lane's diffs)

**(a) Where planned inserts silently became zero — three stages, all silent at base:**
1. **Placement** (`auto_director/edl.py::_plan_broll_segments`, also used by `clip_plan.apply_plan_broll_concepts`): every guard returned a bare `False` with no record — hook/first-cue guard and end guard (`cue_too_short`), the 0.30 coverage cap, blocked windows, payoff/blocked phases, the final-line guard, overlap, the ≥4 s face-return spacing, the density cap (`target_count` from the profile's 1.5/min finance rate), and concept cues' anchor-or-nothing rule. On a ~40 s clip these guards legitimately compress the count — but at base a refused cue left **no trace anywhere**, so "few by design" and "lost" were indistinguishable.
2. **Resolution** (`media/pixabay_broll.py::resolve_edl_broll` + `coverr_broll.py`): with no provider configured the function was a **silent no-op returning 0**; provider search returning nothing, tag-gate rejection of every hit, SigLIP veto exhaustion, and download failure were log-only at best. The entry stayed bare on the EDL and the renderer skipped it without a word.
3. **Render** (`media/edl_render.py::build_edl_filter_graph`): a resolved entry whose file had vanished from the cache was silently skipped.

Which of these explains Luffy's actual 1-insert-in-165 s run **cannot be settled from the sandbox** — his task's records predate any stamping, and his provider reachability (Pixabay/Coverr from his network/VPN) is not reproducible here. That unanswerability is precisely the defect the stamps close: from this branch onward, the reason is recorded on the EDL itself.

**(b) Cross-clip repetition:** the auto-director EDL path — the path that produced his clips — had **no task-level memory**: `build_pipeline_edl` resolved each clip's EDL independently, and the on-disk stock cache made the library's top hit the instant winner for every sibling asking the same question. (The legacy session path had a `BRollSession` for reuse avoidance; the EDL path had no counterpart.) Confirmed as charged.

**(c) Transitions:** EDL segments are **synthetic equal divisions** of the clip window, not detected scene boundaries. A join's transition came from (1) the profile default, (2) the profile's signature transition planted at one join (`apply_signature_transition`), or (3) the AI plan's `transition_choices` (`apply_plan_transitions`). Nothing measured the speech at the join — so a signature/plan **slide could and did land mid-sentence** (his c4, ~32 s). No variety rule existed either.

**(d) The first ~2 seconds:** **nothing universal.** Punch-ins fired only at energy peaks (the pipeline passes none), speaker-turn punches are dialogue-profile-only, and a plan opening punch existed only when an LLM plan happened to choose one. A typical Auto clip's opening was a static frame.

## Completed (5 commits, all read by the finisher)

- `9988c8a` — **Stamps + ledger** (`pixabay_broll.py`, `coverr_broll.py`, `edl_render.py`): resolution now tallies each cue's candidate pool (hits / excluded / gate_rejected / vetoed / download_failed) and stamps every undelivered entry `not_inserted = {stage: "resolve", reason, candidates}` with reasons `no_provider_configured`, `provider_empty`, `provider_error`, `download_failed`, `veto_exhausted`, `gate_rejected`, `duplicate_in_task`; a resolved entry clears the stamp. New `BrollUsageLedger`: one per task, holds committed asset ids under a lock, resolves each clip's EDL with them excluded at both providers; winners within a single resolve join the exclusion set too (no repeat inside one clip either). Render-stage file-vanish is now a WARNING log (log only — the EDL is not mutated at render time).
- `9527aec` — **Discipline + push-in** (`auto_director/edl.py`, `clip_plan.py`): placement drops are recorded into `edl["broll_dropped"]` (`{query, concept, stage: "placement", reason}`) for both the keyword and concept paths. `enforce_transition_discipline` downgrades a *decision* join (profile signature, or a join the AI plan changed) to a hard cut when it sits in continuous speech (word gap < 0.5 s and no planner phase boundary within 0.75 s) or repeats the previous join's decorative transition back-to-back; every downgrade is noted in `edl["transition_discipline"]`. Profile-default grammar joins are deliberately **not** judged. Every Auto EDL gains an opening push-in: ken-burns ramp 1.0 → 1.12 over 0–2.0 s via the existing machinery, skipped for clips < 4 s or when the opening is already treated; a plan opening punch re-scales this ramp instead of stacking. Plain strips it with all zoom keyframes (`plainify_edl`), unit-pinned.
- `09679fe` — **Threading** (`task_service.py`, `video_service.py`): `TaskService.process_task` creates one `BrollUsageLedger` per task and threads it through every `create_single_clip`; resolution runs through the ledger at EDL build **and** once more after the viral plan is consumed (plan-added concepts dedupe against siblings too). Plain tasks skip ledger resolution entirely. Single-clip re-renders (`clip_service`) intentionally get no ledger — their saved entries carry over; that is the boundary of the guarantee.
- `fc2003f` — 18 new tests (`test_retention_editing.py`) + the rendered proof harness (`proof/run_proof.py`, `proof/README.md`).
- `f938d81` — CHANGELOG entry (4 bullets, plain language, per repo rules).

## Tested (finisher's own runs, sanctioned venv pytest 9.0.3, `OMP_NUM_THREADS=2`, `--no-cov`, from `jcs-retention/backend/`)

- **Suite 1 — 345 passed / 0 failed** (134 s): `test_retention_editing.py` (the lane's 18) + `test_auto_director_edl.py` (incl. the lane's re-pin) + B-roll family (`test_broll`, `test_broll_resolver_v2`, `test_broll_concepts`, `test_pixabay_broll`, `test_coverr_broll`, `test_broll_toggle`, `test_broll_slice5_density`, `test_broll_vision_veto`) + transition family (`test_signature_transitions`, `test_n1_e1_transitions`, `test_edl_transitions_sfx`).
- **Suite 2 — 221 passed / 1 FAILED** (155 s): `test_viral_plan`, `test_viral_plan_pipeline`, `test_render_parity`, `test_edl_render`, `test_edl_pipeline_wiring`, `test_editor_broll`, `test_editor_broll_review`, `test_broll_audit_fixes`, `test_broll_rerender_render`, `test_broll_word_sense`, `test_retention`, `test_layout_report_broll_applied`, `test_beat_edl`.
- **Changelog vitest — 4 passed** (`frontend/src/lib/changelog.test.ts`, temporary node_modules symlink, removed after).
- **Total: 566 passed / 1 failed.** No test weakened by the finisher; no test fixed by the finisher.

## The one failure — FAILED (lane-caused stale pin, not a product defect)

`test_viral_plan_pipeline.py::TestSeam::test_plan_transitions_punch_and_caption_style_are_consumed` fails on the branch at line 154: a precondition asserting a freshly built EDL has `zoom_keyframes == []`. The lane's universal opening ramp makes that false by design. **Proven lane-caused**: the finisher ran the identical test at base `5ea71bf` in a scratch worktree — **1 passed**. It is the same stale-pin class the lane itself correctly re-pinned in `test_auto_director_edl.py` (podcast test); this second pin was missed. Per the finisher's charge it is **reported, not fixed**. Consequence, stated plainly: **the branch is NOT full-gate green** — a one-line re-pin (assert the only pre-plan keyframe is the `opening_punch` ramp, mirroring the podcast re-pin) plus a re-run of this file is owed before merge. The full backend suite was **NOT RUN** (targeted families only; the full gate belongs to the post-merge release process), so further stale pins of the same class elsewhere are possible and must be swept at merge time.

## Rendered proof — judged by the finisher from the artefacts: **PASS** (proof level: real pipeline + real renderer; provider pool stubbed via MockTransport with 2 assets — the established provider-boundary pattern)

- **De-duplication:** `edl_afterA.json` carries asset **101**, `edl_afterB.json` asset **102** for the identical concept/query; the no-ledger "before" resolution took 101 again. Cue-window frames confirm visually: afterA's insert is the red stock, afterB's the blue stock (mean RGB (127.9, 47.7, 47.0) vs (61.9, 89.2, 138.1)); before = red again.
- **Transition discipline:** the plan's slide on the mid-speech join (6.0 s, continuous synthetic word stream) is `transition_in: "cut"` in both after EDLs with `transition_discipline` notes (`from: slide, reason: continuous_speech`), and the after render graph contains **no xfade**; the before EDL keeps the slide (0.4 s) and its graph contains the xfade.
- **Opening push-in:** afterA YuNet face width 309.2 px @0.3 s → 380.5 px @2.5 s (**ratio 1.231**) vs before 305.6 → 330.0 (**1.080**, the speaker's natural lean on the identical source window) — the growth beyond natural motion matches the EDL's 1.0→1.12 ramp; the finisher visually confirmed the tighter framing in `afterA_late.jpg` vs `afterA_open.jpg`.
- **Honest construction note:** "before" is the pre-fix *decisions* reconstructed on the same plan (ramp stripped, slide re-instated, resolution re-run without the ledger), not a run of the pre-fix binary — the three claims are EDL-decision + render-graph claims, and the reconstruction reinstates exactly those decisions. The proof fixture delivers both its cues, so the `not_inserted` stamps are proven by the unit tests, **not** by the rendered proof.

## Observations (not verdict blockers)

1. `transition_discipline` can hold two identical notes for one join when both the signature pass (in `build_edl`) and the plan pass (in `video_service`) downgrade it at different stages — each note is a real downgrade event, but the record is noisy.
2. The render-stage loss (resolved file vanished) is WARNING-log only, not an EDL stamp — the weakest of the three stages' records.
3. Discipline judges only decision joins by design; a profile whose *default grammar* transition is decorative keeps it on every join — a product-design question, not a discipline bug.

## Remains / NOT VERIFIED

- The one stale-pin re-pin + `test_viral_plan_pipeline.py` re-run (above), then the full gate at merge time.
- **User-machine only:** which stamp reason explains Luffy's real zero-insert run (provider reachability vs provider-empty vs veto/gate exhaustion) — settleable only by a fresh run on his machine with this branch merged, where the EDL will now carry the answer. Real-pool dedup depth (his actual Pixabay/Coverr candidate counts vs the proof's 2-asset pool) and the push-in's look on his other footage are likewise NOT VERIFIED here.
- Live-PostgreSQL tier: NOT RUN (not touched by this lane's seams; remains the gate's family).

## Next

Re-pin the one stale test (mirroring the podcast re-pin in `9527aec`), re-run `test_viral_plan_pipeline.py`, and sweep for other `zoom_keyframes == []`-class pins before this branch joins the post-release merge queue alongside the B2 fixes, Recipes wiring, Learning tests and the RTX 4050 branch. After merge, Luffy's next real Auto run is the validation: its EDLs should show different assets per sibling clip, hard cuts mid-speech, the opening ramp — and, if any insert is missing, its stamped reason.
