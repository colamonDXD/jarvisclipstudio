# Frontend Gate — SnipNova Consolidated V12

**Packaged tip:** branch `next-milestone` @ `49cfad22`
(`49cfad2284c9a2bdb6fec9c7a4d836cac377c57a`) — includes the
Studio Console + the gaming layout picker port (Phase 2),
the Galaxy starfield background and the top-navigation
shell.

**Method:** full vitest run; `tsc --noEmit`; `next build`;
the changelog test (`src/lib/changelog.test.ts`) run
standalone as the repo requires after CHANGELOG edits. The
full run executed on the merged tree `02ddf98f`; the final
tip's only frontend-visible delta is one CHANGELOG line
(the music fix's release note), and the changelog test was
re-run on the final tip: **4/4**. Logs:
`~/workspace/packaging-v12/logs/`.

## Results

- **vitest (full run): raw 944 passed / 3 failed / 12
  skipped** (155 files, 959 counted tests). The 3 failed
  tests (`home-app-framing`, `home-app-music`,
  `home-app-recipes` — each at the ~5 s sandbox-CPU timeout
  line) and the 3 suite-level failures (the PGlite
  "real Postgres" integration files: affiliates,
  service-status, user-acquisition) ALL pass on serial
  solo re-runs (24/24 tests). **Effective: 959 passed /
  0 failed / 12 skipped (971 tests incl. the solo-only
  integration files).**
- **tsc --noEmit: 0 errors.**
- **next build: exit 0** (compiled successfully; the
  BetterAuth default-secret lines are the known
  sandbox-env build warnings, present in prior lanes).
- **changelog test: 4/4** — on the merged tree and again
  on the final tip after the music-fix entry.

## Notes

- The timeout family is the sandbox's known CPU-contention
  pattern (the Phase 2 lane: 948/1 with the 1 passing solo;
  the shell lane: 13 raw failures, 38/38 solo). No test was
  skipped, loosened or edited in this gate; the only test
  edit in the whole build is the sanctioned backend
  wiring-test isolation (`02ddf98f`).
- The gaming-picker tests (`home-app-gaming-layout.test.tsx`,
  5 tests incl. the framing-disable behaviour) and the
  Galaxy background tests are inside the passing totals.
