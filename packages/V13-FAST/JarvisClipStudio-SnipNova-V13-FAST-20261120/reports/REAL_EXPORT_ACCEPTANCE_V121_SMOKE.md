# Real-Export Acceptance — SnipNova Consolidated V12.1 (smoke, sandbox)

**Tree under test:** the exact packaged tree — `next-milestone`
@ `3900c3da` (`3900c3dab9b9522eb30840ce4fa028dc2532121b`),
extracted from a `git archive` of the tip into a clean
snapshot, with the 7 bundled music beds added to
`assets/music` exactly as the package assembly adds them
(checksum-verified copies; the repo gitignores the binaries
by design) and the 22 SFX WAVs from the archive.

**Scope (per the V12.1 lane brief):** ONE combination end to
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
for this tree (`exports_v121_smoke_manifest.csv` is
header-only: zero exports were produced). The attempt
record:

- Schema init against the fresh scratch database: **exit 0**
  (all 55 migrations applied by the app's own init path).
- The combination run was launched with a 7,200 s budget.
  The harness session was **terminated externally (SIGTERM)
  ~53 minutes in**, during the source-analysis passes —
  before the render stage — and the buffered driver output
  was lost with the session. No `result.json` was written;
  no export exists.
- This is the same sandbox limit that kept v12's three
  attempts from reaching render: this tree runs multiple
  full-source analysis passes over a 68-minute AV1 file on
  a 1.5-CPU VM. It is an environment limit, not a product
  error — no product exception was observed at any point
  (schema init clean; the run was in analysis when killed).

What DOES stand on this exact tree (see
`reports/V121_INTEGRATION_REPORT.md`): the full backend
gate (5,634 passed / 0 failed), the database-backed suites
(108 passed incl. live + VOD), the Postgres tier (10
passed), the full frontend gate (959 tests, tsc + build
clean), the streamer-fixture re-score (6/7, the accepted
honest score) and the Plain/Auto no-bleed proof — all run
on this code before packaging. The end-to-end render proof
on Luffy's machine is checklist item 61 plus the standing
Speed re-run (KNOWN_ISSUES G-V121-1).
