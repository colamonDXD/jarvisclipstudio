# Real-Export Acceptance — SnipNova Consolidated V12 (smoke, sandbox)

**Tree under test:** the exact packaged tree — `next-milestone`
@ `49cfad22` (`49cfad2284c9a2bdb6fec9c7a4d836cac377c57a`),
extracted from a `git archive` of the tip into a clean
snapshot, with the 7 bundled music beds added to
`assets/music` exactly as the package assembly adds them
(checksum-verified copies; the repo gitignores the binaries
by design) and the 6 SFX WAVs from the archive.

**Scope (per the V12 lane brief):** ONE combination end to
end — **Full AutoClip + Plain template** — driven through
the REAL service path (`TaskService.create_task_with_source`
→ fresh-session settings readback →
`TaskService.process_task` with the worker's own arguments),
on the Japan vlog fixture (4,090 s, 1920×1080 AV1, 1.33 GB)
with its cached transcript/beat/reframe sidecars. Scratch
PostgreSQL 16 + Redis; ALL provider keys unset, so the run
exercises the designed no-key floor path (`SELF_HOST=true`).

## Verdict: NOT RUN — the render stage was never reached in this sandbox

This is **not a pass**. The combination did not complete,
so there are no exports to probe and no Plain-honesty count
for this tree (`exports_v12_smoke_manifest.csv` is
header-only: zero exports were produced). What the attempts
established, with logs (`~/workspace/packaging-v12/logs/smoke-*`,
`~/workspace/jcs-accept-v12/scratch-v12/logs/`):

- Schema init, task creation through the real service path
  and processing entry all worked on the exact packaged
  tree, repeatedly, with **no product errors** in any log.
- The V12 pipeline runs **at least four full-source
  analysis passes before the first render** on this
  fixture: (1) face-region stream fps=4 @ 224 px,
  (2) thumbnail stream fps=8 @ 160×90, (3) face-region
  stream fps=5 @ 384 px, (4) scene-select
  (`select='gt(scene,0.4)',showinfo`). On a 68-minute AV1
  source in this 1.5-CPU sandbox the first three passes
  alone consumed ~97 minutes (measured read rates
  ~0.5–1.0 MB/s against the 1.33 GB file).
- Attempts: a 2,400 s budget run (stopped by the lane once
  the pass structure was clear), a 7,200 s run killed by a
  sandbox runtime restart, and a final 7,200 s run that
  completed passes 1–3 and was inside pass 4 when the lane
  stopped it at 11:40 UTC on the wrap-up order — the
  remaining ~20 min could not contain pass 4 plus ten
  1080×1920 renders. The V11 smoke (same fixture, same
  procedure, wall 1,629.7 s, PASS) ran on a tree whose
  pipeline did not include the added passes.
- The shipped app on the user's RTX 4050 machine runs
  these stages on GPU-accelerated paths with far more CPU;
  the sandbox number is a sandbox property. The honest
  record for THIS tree in THIS environment is NOT RUN.

**Related verdict the lane was asked to close here:** the
three `test_caption_face_guard_render` tests that failed
in backend-gate chunk_ac are **PASS** — they failed only
because the sandbox's `tesseract` binary had been wiped by
a runtime reset; with tesseract 5.3.4 restored they pass
4/4 together with the brief-image OCR test (full account
in `reports/BACKEND_GATE_V12.md`).

## Notes

- Frame judgement: not applicable — no exports exist to
  extract frames from.
- A future sandbox smoke on this tree should either use a
  much larger budget (≥4 h for this fixture) or a shorter
  fixture; the procedure itself is unchanged from V11.
