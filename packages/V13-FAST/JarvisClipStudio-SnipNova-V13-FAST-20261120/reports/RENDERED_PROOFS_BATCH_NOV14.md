# Rendered-Proofs Batch — 2026-11-14 (night lane)

**Verdict: 5 / 5 proofs PASS.** F-R1, F-B, F-A, F-1, F-R2 all reproduced —
at rendered-video level — the behaviour their fixes were merged for, on the
frozen release tip. No production code was changed by this lane and no proof
failure was found.

## Environment and ground rules

- Snapshot worktree `~/workspace/jcs-renderproofs`, detached at
  `5ea71bfb92201ad09fdd6bd408a87ad659f070d4` ("Merge font-union fix").
  End-of-lane check: **0 tracked files modified** (only the untracked
  `.proofs/` scratch tree); `~/workspace/jcs-next` still at `5ea71bf`, clean;
  tag `v6-candidate-90f1cb4` still →
  `90f1cb41b0837a04e4e772ee006857938a7db2fb`. Luffy's machine untouched.
- Real pipeline throughout: `TaskService.process_task` /
  `apply_moment_selection` / `update_clip_captions` / `rerender_clip`
  against scratch PostgreSQL 16 databases (`rp_main`, `rp_f1`, `rp_fr2`,
  port 55451) and a scratch Redis on 6379 (the pick path requires it —
  `apply_moment_selection` clears the cancel flag and enqueues through
  Redis; without it the pick correctly refuses to queue).
- Fixture: the Japan source (`qm-source/source.mp4`, YouTube `ANoZNWQXYZ4`,
  4,090 s) hardlinked into the scratch uploads dir with its transcript /
  beat / reframe sidecar caches. Transcription was a cache hit in every run.
- Provider boundary (stated plainly): the sandbox holds **no provider
  keys**, so `VideoService.analyze_transcript` was replaced at the driver
  level by a test double with two modes — `raise` (forced analysis
  failure → the production D-1 deterministic visual floor) and `healthy`
  (returns two fixed segments, 10:00–10:12 and 30:00–30:12, with marker
  reasonings "PROOF DOUBLE healthy analysis pick one/two"; calls counted
  live to `.proofs/double_calls.json`). Everything upstream (intake,
  transcription cache, cache-key logic) and downstream (Step-3 cache
  reads/writes, selection, plans, rendering, persistence) is unmodified
  production code. This is the same precedent as the step-2 acceptance
  re-run (`REAL_EXPORT_ACCEPTANCE_RERUN.md`). F-1/F-R2 are therefore
  **harness-level at the provider seam, real-pipeline everywhere else**;
  F-R1's selection came from the deterministic floor the keyless step-2
  re-run also produced (the mode-treatment difference under test is
  downstream of selection); F-B and F-A involve no provider at all.
- Render artefacts, source maps, ASS captures, cache dumps and logs live
  under `~/workspace/jcs-renderproofs/.proofs/` (`out/`, `ass/`, `fa/`,
  `logs/`).

## Proof 1 — F-R1: Find Moments pick, Plain vs Auto — **PASS**

Fix under test: `0e69c07` via merge `1c1baba` (`ModePlan.ai_editing`
False→True for MOMENTS_MANUAL + the pick-render call site).

Method: two `moments_manual` tasks on the same source, identical except
`template_mode` (`plain` vs `auto`). Analysis was forced to fail (double
mode `raise`), so both parked with the same deterministic floor slate of
10 candidates. The same candidates [0, 1] were picked through the
production `apply_moment_selection` in both tasks and rendered by the
resumed production pipeline.

| Task | Pick | Window | SHA-256 | Bytes | Reframe shots | Shot modes |
|---|---|---|---|---|---|---|
| Plain `c655f602…` | clip 1 | 02:29–02:59 | `b3bf5194d23a8928dc47363b7c6a26d97e268ff72efd69d1a6708bae745fe770` | 22,170,471 | 23 | all `plain` |
| Plain `c655f602…` | clip 2 | 14:56–15:26 | `62f80d1a6cb599d6196f09ba4c3fa5c8fb5e8bff5e1b35a37b8c5b5fd7bc47f6` | 16,467,642 | 11 | all `plain` |
| Auto `35b8666b…` | clip 1 | 02:29–02:59 | `ed1aaec05f51c2c0d1e918917586cee0000b10b5b0f23e07d43f58f22d8e9f76` | 23,662,271 | 23 | `fit`, `pair`, `single`, `split` |
| Auto `35b8666b…` | clip 2 | 14:56–15:26 | `02dbf7f5413ba02f852584aeec7708bf96db50d8e80beccc98d7b8c6e4e950b7` | 16,375,082 | 11 | `fit`, `single`, `split` |

Pass bar met on both clauses: the pairs' hashes **differ**, and the
measurable difference is exactly the fix's intent — the Auto render
carries the AI-editing template's reframe shots while every Plain shot
stays mode `plain`. Pre-fix (step-2 evidence) the same pair was
byte-identical with zero reframe shots.

Cross-check: the F-R2 degraded Full AutoClip run (Proof 5) rendered the
same two windows in Auto mode from a different task and produced
**byte-identical files** (`ed1aaec0…`, `02dbf7f5…`) — rendering is
deterministic across tasks, so the F-R1 difference is attributable to
the mode alone.

Proof level: rendered video, real pipeline + real DB; selection slate
from the deterministic floor (provider double at the analysis seam).

## Proof 2 — F-B: stored caption Top survives `rerender_clip` — **PASS**

Fix under test: `d8ca686`/`4832715` (`rerender_clip` resolves
`effective_captions` from the clip's stored plan) + follow-up `5bca336`
(the caption PATCH records the position into the edit plan).

Method: Manual Cutter task (user range 22:04–22:12, Plain, travel_vlog)
→ clip `8b2305a0…`. Position set to Top through the real editor path
(`TaskService.update_clip_captions(..., "top", ...)`, ASS captured by
tee) → plan dumped from the clip's source map → re-render through the
worker-identical call `rerender_clip(task_id, clip_id, "travel_vlog")`
with **no** `caption_overrides` (the `workers/tasks.py` regeneration
call), ASS captured again.

Measurements:

- Stored plan after the PATCH (source-map `editor` block):
  `captions: {"position": "top", "position_y": 0.18}` — the follow-up
  persistence fix works.
- Top PATCH render: ASS `\pos` median Y = 299 / 1920 = **0.1557**
  (36 events) — captions at the top band.
- `rerender_clip` output: ASS `\pos` median Y = 299 / 1920 = **0.1557**
  (47 events) — **stayed at Top**, not the 0.7557 bottom fallback the
  pre-fix code produced (step-3 R2 evidence: stored Top dropped, render
  byte-identical to profile-default bottom).
- Re-rendered file: `clip_1_2204-2215_0a65beb17683.mp4`, SHA-256
  `d6e9ae091d4e39ce46b3aa0229c556ec8ca6964f65b01cdb349f99302e48f59d`,
  9,253,934 B (exported to `.proofs/out/fb/`). Its own source map still
  carries the plan (`{"position": "top", "position_y": 0.18}`), so a
  second regeneration would keep Top too.

Environment note (not a proof failure): the first PATCH attempt on a
25-s clip was killed mid-flow by the app's own
`idle_in_transaction_session_timeout = 300000` connection setting
(`backend/src/database.py:47`) — this sandbox's CPU render of that clip
inside `update_clip_captions` exceeded 300 s of transaction-idle time,
so the (completed) render's plan write rolled back with the connection.
The render itself measured 0.1557 in its captured ASS. The proof was
re-run end-to-end on an 8-s clip, whose PATCH render fits the window.
On normal hardware a 25-s caption re-render takes seconds; flagged here
as an INFO observation only: on a sufficiently slow machine, a caption
PATCH render longer than 300 s in one transaction would hit the same
guard.

Proof level: rendered video, real pipeline + real DB, no doubles.

## Proof 3 — F-A: Reference Style measured `position_y` renders — **PASS**

Fix under test: merge `75fc951` (`edl_caption_position_request` reads
`reference_look_override.position_y` at the profile/reference tier,
`explicit=False`, band 0.1–0.85).

Method (the N4/N6 seam pattern): production `_build_rerender_edl` for
the Japan r3 window 1324–1338 s (travel_vlog profile captions
`{position: "bottom"}`), three variants rendered serially through
`attach_reframe_plan` → `create_optimized_clip` with the ASS tee:

1. `fa_nolook` — no look (control).
2. `fa_look` — EDL + `reference_look_override {"position_y": 0.66}`.
   The stamp shape was verified against production `build_edl` itself
   (a look-carrying profile stamps exactly `{"position_y": 0.66}`),
   not assumed.
3. `fa_explicit` — caption override `position_y: 0.66` (known-good
   control; step-3 measured 0.6359).

Reader checks (all exact): nolook → `(0.78, False)`; look →
`(0.66, False)`; explicit → `(0.66, True)`.

| Render | layout `caption_y` | ASS `\pos` median | SHA-256 | Bytes |
|---|---|---|---|---|
| fa_nolook | 0.7557 | 0.7557 (52 events) | `04d9354c75d2f21f…` | 10,913,782 |
| fa_look | **0.6359** | 0.6438 (52 events) | `566399acb4e25376…` | 10,906,563 |
| fa_explicit | **0.6359** | 0.6438 (52 events) | `566399acb4e25376…` | 10,906,563 |

Pass bar met: the no-look render sits at the named-band bottom default
(0.7557); the look render moved to the measured height and is
**byte-identical to the explicit control**. Pre-fix (step-3 R1) the
look render was byte-identical to the no-look render instead.

Proof level: rendered video through the production EDL→render seam;
no DB or provider involved (none is on this path in production either).

## Proof 4 — F-1: Manual cache cannot poison Full AutoClip — **PASS**

Fix under test: `615b618` (analysis-cache key separation:
`selection:user_ranges` rows, read guard
`cached_analysis_is_user_selected` in both services, `selection_strategy`
stamps).

Method: fresh DB `rp_f1`, healthy double throughout. Leg 1: Manual
Cutter task (r3). Leg 2: Full AutoClip task on the same source. Leg 3
(poison): both cache rows overwritten by the driver with a pre-fix-style
manual payload built by the production `build_manual_segments` (r3+r5,
**no** `selection_strategy` stamp), then a third Full AutoClip run.

Measurements:

- Leg 1: double call count **0** (Manual never calls analysis). Cache
  dump: the manual payload exists **only** in the `selection:user_ranges`
  row (`selection_strategy: "user_ranges"`, window 22:04–22:29); the
  shared row's `analysis_json` is **NULL**.
- Leg 2: double called (**n=1**) — Full Auto ran its own analysis. The
  rendered clip is the double's 30:00–30:12 window with reasoning
  "PROOF DOUBLE healthy analysis pick two" (SHA `208e8c79…`). The Manual
  r3 window appears nowhere. (The double's first clip, 10:00–10:12, was
  lost to a sandbox OOM kill of its ffmpeg — see Incidents; the pipeline
  reported honestly: "Complete — 1 of 2 clips created. Retry generation
  to create the missing clips.")
- Leg 3: with the poison in place, the Full Auto run **still called the
  double (n=1)** — the read guard discarded the unstamped user-selected
  payload — and rendered **both** double windows with the double's
  reasonings: 10:00–10:12 `121f35fef29b99b20d09bab96193572852c28b4e6f4581c3c9a59e8ce462fb0f`
  (7,342,950 B) and 30:00–30:12 `208e8c79090033607b8752cc1a137e8387c44796b20f73c331a3b4f89443b64f`
  (5,406,516 B). Final cache state is the designed end-state: shared row
  stamped `selection_strategy: "ai_ranked"` holding the healthy payload;
  the user-ranges row left as the (driver-poisoned) manual payload under
  its own key, where no Full Auto read can consume it.

Proof level: real pipeline + real DB + rendered clips; provider-boundary
double for the analysis content (harness-level at that seam only).

## Proof 5 — F-R2: degraded floor slate is never cached — **PASS**

Fix under test: `19069e9` (`cached_analysis_is_degraded` detector +
read guard + write-path suppression of degraded stand-ins).

Method: fresh DB `rp_fr2`. Run 1: Full AutoClip with the double in
`raise` mode (analysis forced to fail). Cache dump. Run 2: same source,
healthy double.

Measurements:

- Run 1 completed with **10 floor clips**, every clip's reasoning
  stamped "Visual selection (AI analysis unavailable): audio energy
  peak …" (windows = the deterministic slate 02:29–02:59 … 55:51–56:21;
  hashes in `.proofs/out/fr2-degraded/result.json`).
- Cache dump after run 1: the source's only cache row has
  **`analysis_json` = NULL** — the degraded stand-in was never persisted
  (write-path suppression). The task checkpoint likewise carried no
  `analysis` key.
- Run 2: double **called (n=1)** — fresh analysis, not a cached slate.
  Clips: 10:00–10:12 "PROOF DOUBLE healthy analysis pick one"
  (`121f35fe…`) and 30:00–30:12 "pick two" (`208e8c79…`) — byte-identical
  to the same windows rendered in Proof 4. Final cache row holds the
  healthy payload stamped `selection_strategy: "ai_ranked"`.

Proof level: real pipeline + real DB + rendered clips (10 degraded +
2 healthy); provider-boundary double for failure/health at the analysis
seam (harness-level at that seam only).

## Incidents and environment notes (all harness-side; none is a product defect)

1. **First scratch cluster was created with SQL_ASCII encoding**; the
   moments checkpoint save failed on non-ASCII payload text
   (`UntranslatableCharacterError`) and the task errored. Cluster
   recreated with `--encoding=UTF8 --locale=C.UTF-8`; every later run
   was clean. (The production compose Postgres is UTF-8.)
2. **Scene-cut detection times out at its internal 300 s** on this
   1.5-CPU sandbox for the 68-min AV1 source (`analysis_seconds ≈ 300.1`
   in every full run); the pipeline proceeds without cut hints through
   its designed never-fail path. On target hardware this pass completes
   in seconds–minutes.
3. **Sandbox OOM kills**: finished driver processes linger after printing
   results (asyncio pools), holding ~0.5 GB each; with five accumulated,
   two ffmpeg renders were SIGKILLed (F-1 leg 2 clip 1; the first F-1
   poison attempt lost both renders and was re-run clean after the
   zombies were killed by exact PID). The pipeline reported partial
   completion honestly in both cases.
4. **The VM's system layer was recycled once (~18:01 UTC)**: apt
   binaries (PostgreSQL) vanished and the scratch cluster received a
   smart shutdown. `postgresql-16` was reinstalled from the Ubuntu repo
   and the cluster restarted from its intact data directory; no proof
   state was lost (rp_f1's cache rows verified present after restart).
5. F-B's 300 s idle-in-transaction observation is recorded in Proof 2.

## What this batch means for the Nov 15 gate

The five fixes that were unit/integration-proven now also carry
rendered-video proof on the frozen tip `5ea71bf`. Nothing in this batch
blocks the release gate; no defect report is owed from it. The F-B
300 s observation is INFO-level and hardware-dependent — worth a line
in KNOWN_ISSUES at most, not a fix lane.
