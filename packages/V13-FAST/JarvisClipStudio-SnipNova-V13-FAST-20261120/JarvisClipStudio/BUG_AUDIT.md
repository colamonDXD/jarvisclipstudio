# BUG_AUDIT — SnipNova defect register

*Refreshed 2026-11-15 (IST), accurate to `next-milestone` @ `5ea71bf`
(the frozen, gate-green release tip) plus the UNMERGED queue branches
(§2 — fixed on branches, NOT in the tip, NOT shipped). Every claim
cites its report file. Severity: P0 = ship-blocker / data loss; P1 =
real user-facing breakage; P2 = real defect, bounded impact; LOW /
INFO = recorded, not blocking. Previous version was accurate to
`7f04289` (2026-11-13).*

**Headline: on the frozen tip, P0 / P1 / P2 — NONE OPEN.** The
register below is what remains: two LOWs, two INFOs, one report-only
follow-up, the V8 package's carried gaps, and four defect groups that
are FIXED ON BRANCHES awaiting the post-release merge.

## 1. Open on the frozen tip

### G-2 — LOW — Phantom `schema_migrations` row silently suppresses a migration — REPORT-ONLY

- **Symptom:** a row claiming a migration was applied (without the
  runner having written it) causes the runner to skip that migration;
  `feature_flags` table absent while init reports OK. Ledger is
  runner-written only, so reachability is low.
- **Proposed fix (not done):** a future probe fix — verify the table
  actually exists before trusting a ledger row. Carried in the V8
  package as gap G-V8-2.
- **Source:** `research_notes/sprint-7day/REHEARSALS_REPORT.md` (R4,
  G-2); SPRINT_LOG 2026-11-13 ~18:27 IST.

### N2 — LOW (note, not a defect) — Stale "recorded only" wording in backend comments

- `grep "recorded only"` still hits 3 backend comments
  (`backend/src/api/routes/tasks.py:2610`,
  `backend/src/auto_director/reference_style.py:2595`,
  `backend/src/provider_manager.py:18`) — all unrelated comments,
  none of them Brand Kit copy (the D6 copy defect itself is closed;
  frontend grep = 0 matches). No action needed; recorded so a future
  grep isn't misread as D6 regressing.
- **Source:** `research_notes/sprint-7day/EDITOR_AUDIT_VERIFICATION.md`
  (LOW note N2).

### INFO F-3 — List-view projection omits styling/brand fields — documented, no fix

- `get_user_tasks` projection omits styling/brand fields; proven
  consumer-free and pinned by a test. Documented, not a defect.
- **Source:** `research_notes/sprint-7day/SETTINGS_INTEGRITY_AUDIT.md`
  §4 (finding F-3).

### INFO F-5 — Create form hardcodes `processing_mode "fast"` — product decision, not a defect

- The main create form hardcodes `"fast"` (`home-app.tsx`); the mode
  is honoured end-to-end when supplied elsewhere (API / autopilot /
  monitor). Awaiting Luffy's product decision (also audit Class 6);
  the exposure question is his, not a bug.
- **Source:** `research_notes/sprint-7day/SETTINGS_INTEGRITY_AUDIT.md`
  §4 (finding F-5).

### Pydantic-ai family-3 follow-up — REPORT-ONLY, decide before the v2 upgrade

- Families 1–3 code fixes are merged (`235cf5c`, `fd3f3e3` — see
  Closed). What remains is a DECISION, not a defect: normalise-or-reject
  for **user-supplied** `openai:` model strings before any pydantic-ai
  v2 upgrade (v2 resolves them to the Responses API). Shipped defaults
  already use `openai-chat:`.
- **Source:** SPRINT_LOG 2026-11-14 (warnings hygiene / deprecations).

### Carried observations from the on-tip acceptance (not new defects)

- **O-R3 (= package gap G-V8-4):** degraded floor exports carry no
  hook title, and carry captions only where transcript words exist.
  The hook-title half is FIXED ON BRANCH (§2,
  `fix/floor-export-parity`); the captions half was REFUTED as a
  product defect by that lane's Step-0 (captions burn in from the
  transcript sidecar on every path — the acceptance observation came
  from floor windows whose seeded sidecar held no words). On the tip
  the gap stands as recorded.
- **O-R4:** degraded picks carry no ClipPlan (expected with no AI
  planner). Re-check on the user's machine with providers live.
- **Source:** `research_notes/sprint-7day/REAL_EXPORT_ACCEPTANCE_ON_TIP.md` §8.

### V8 package carried gaps (in the package's KNOWN_ISSUES, G-V8-1…7)

G-V8-1 Cloudflare passthrough lines in his preserved override
(unverified live) · G-V8-2 = G-2 above · G-V8-3 live provider
validation (bake-off + ladder walk — user-machine) · G-V8-4 = O-R3
above · G-V8-5 = F-5 above · G-V8-6 slow-render DB-timeout sandbox
observation (a >300 s in-transaction render on the 1.5-CPU VM can hit
`idle_in_transaction_session_timeout`; seconds-long renders on real
hardware unaffected) · G-V8-7 legal operator name for Terms/Privacy
(his to supply). G-V7-3 (pre-tip acceptance = STALE) is **CLOSED** by
the on-tip re-run. Source: `V8_PACKAGE_REPORT.md`.

## 2. Fixed on branches — NOT in the tip, NOT shipped (UNMERGED)

These defects are LIVE on the frozen tip (and therefore on his
installed build) until the merge batch lands after the release
closes. Each entry: defect → fix → branch status.

### B-1 — Silent B-roll loss at three stages (his 1-insert-in-165 s run) — FIXED ON BRANCH

- **Defect (traced at `5ea71bf`):** a planned B-roll insert could
  silently become zero at (1) **placement** — every guard in
  `_plan_broll_segments` returned a bare `False` (coverage cap,
  blocked windows, payoff/final-line guards, spacing, density cap),
  leaving no trace; (2) **resolution** — no provider configured was a
  silent no-op returning 0, and provider-empty / gate rejection /
  veto exhaustion / download failure were log-only at best, the entry
  staying bare for the renderer to skip; (3) **render** — a resolved
  entry whose cached file had vanished was silently skipped. The EDL
  path also had **no task-level memory**, so sibling clips could (and
  did) receive the same stock asset. Which stage caused his real run
  is retroactively unanswerable — the records predate stamping.
- **Fix:** every undelivered entry stamped `not_inserted {stage,
  reason, candidate tallies}`; placement drops recorded in
  `edl["broll_dropped"]`; render-stage vanish WARNING-logged;
  per-task `BrollUsageLedger` excludes committed assets at both
  providers. Plus transition discipline (decision joins → hard cuts
  inside continuous speech) and a universal opening push-in.
- **Branch:** `feat/retention-editing` @ `d1f35f0` — 741 PASS / 0 FAIL
  after the re-pin sweep; rendered proof PASS. **UNMERGED.**
- **Source:** `research_notes/sprint-7day/RETENTION_LANE_REPORT.md`.

### B-2 — Editor B-roll surfaces gated on the wrong provider key — FIXED ON BRANCH

- **Defect (traced at `5ea71bf`):** the legacy Pexels path was still
  reachable in Auto mode, and on Luffy's actual deployment (Pixabay +
  Coverr keys, NO Pexels key) the editor search/suggest routes
  returned 503, the AI-edit AddBRoll op reported "not set up", and
  `/broll/status` read unconfigured — all despite v2 being fully
  configured. With a Pexels key it was worse: legacy overlays were
  computed (LLM + searches + downloads) and then silently dropped by
  the EDL renderer. Caption-edit re-renders also silently dropped
  stored v2 cutaways (0 overlays rendered from a stored placement).
- **Fix:** one resolution path — every cue sources through the v2
  resolver (Pixabay→Coverr, provider-stamped, stock library); gates
  moved to `stock_broll_configured()`; editor search library-ensures
  hits before offering them; Pexels code remains stored-footage
  compatibility only (search/choose layer has zero production
  callers, marked RETIRED in-module).
- **Branch:** `refactor/broll-consolidation` @ `fd6e432` — 846 PASS /
  1 skipped; rendered before/after proof PASS. **UNMERGED.**
- **Source:** SPRINT_LOG 2026-11-15 ~14:08 IST (lane §9).

### B-3 — NVENC→CPU fallback silent from the user's seat; ffmpeg threads unbounded — FIXED ON BRANCH

- **Defect (audited at `5ea71bf`):** the NVENC probe's diagnostics
  (`last_nvenc_probe_error()` / `last_nvdec_probe_error()`) had **zero
  consumers** outside `ffmpeg.py` — a failed probe meant silent CPU
  rendering with nothing on any task record, API field, or screen.
  Separately, outside Eco mode **no render ffmpeg command carried a
  thread bound** (matches his 2026-11-12 measurement: CPU 93%,
  RAM 99%, GPU ~0%). Also recorded: NVDEC is opt-in OFF by default
  and the shipped compose files do not pass `ENABLE_NVDEC` into the
  worker — a wiring gap, so a `.env` entry alone cannot enable it.
- **Fix:** `render_thread_budget()` — `ceil(cpus / resolved
  concurrency)` clamped [2, 8] (his machine → 8; Eco stays 4), applied
  to every render command; quality constants byte-untouched (pins
  pass). `render_encoder_diagnostics()` (encoder + probe error)
  stamped onto render timings and persisted into `stage_timings_json`.
  The CPU fallback itself is kept — a CPU render beats a failed one.
- **Branch:** `perf/rtx4050-render-path` @ `19bd1e7` — 170 targeted
  PASS. **UNMERGED.** Whether his probe passes at all is
  user-machine only (`RTX4050_BENCHMARK_KIT.md`).
- **Source:** SPRINT_LOG 2026-11-15 (perf lane §9);
  `research_notes/sprint-7day/RTX4050_BENCHMARK_KIT.md`.

### B-4 — Floor/degraded exports carried no hook title; B-roll gate AND-trap — FIXED ON BRANCH

- **Defect 1 (confirmed):** floor producers hard-set
  `hook_title: None`; the only title source was AI analysis, so
  degraded/floor exports (the honest fallback path) shipped without
  the hook title Auto clips get. Floor segment `text` was never
  grounded either.
- **Defect 2 (confirmed):** the create form sends both the per-task
  `broll_enabled` switch and a legacy `include_broll` chip, and the
  backend ANDed them (`clip_service.py`) — toggle ON + chip OFF = no
  B-roll with no visible reason.
- **Fix:** floor segments' text grounded via the same
  `_ground_segment_text` the AI path uses; hook title derived from
  the segment's own first sentence through the AI path's
  `sanitize_hook_title` (same caps, same guardrails); no words → text
  stays empty, title stays None, honestly. New `resolve_broll_gate`:
  explicit switch authoritative (legacy ignored), legacy honoured
  only when the switch was never set, Plain resolves OFF over both.
  Retiring the chip itself is the frontend owner's action; the
  backend contract now makes that safe.
- **Branch:** `fix/floor-export-parity` @ `f7f66a1` — 61 + 137 + 112
  PASS; rendered before/after proof (title event appears; captions
  unchanged). **UNMERGED.**
- **Source:** SPRINT_LOG 2026-11-15 ~11:41 IST (lane B2 §9).

## 3. Observations (not defects — recorded so nobody re-opens them as such)

- **O-1 — No bundled music/SFX files in the tree/ZIP.** The shipped
  libraries contain ZERO files in repo/ZIP; working libraries exist
  only on Luffy's machine, provenance UNVERIFIED (also audit Class 6).
  Auto×MusicOn renders without a bed wherever the library is absent —
  expected behaviour given an empty library, not a rendering defect.
- **O-2 — Manual Cutter exports carry no hook title.** Manual mode
  runs no analysis, so there is no title to place. Whether manual
  ranges should get titles is a product question, not a defect.
- **O-3 — Deterministic plan stamps the fallback niche label
  `podcast_news` in Manual × Auto** (classification is bypassed by
  design for final-plan tasks). The Auto template still applied; the
  stamp must not be mistaken for a classification result.

## 4. Closed (with closing evidence)

| Item | Severity | Closed by | Evidence |
| --- | --- | --- | --- |
| P0 settings read-back (audit F-1) — serializers dropped 4 fields; every Plain task rendered Auto on v3/v4/v5 | CRITICAL | `ef87820` | Independently verified incl. live-PG round-trip: `SETTINGS_INTEGRITY_AUDIT.md` + on-tip acceptance persistence leg (2026-11-15) |
| D-1 — analysis propagates an LLM client-build failure as a fatal task error | P2 | `51c7ff7` + `1c3df9e` (merged via `9ba6602`); notice `9c546cd` | Analysis degrades to the stamped floor instead of dying; on-tip acceptance: six degraded combos completed honestly, none errored (`REAL_EXPORT_ACCEPTANCE_ON_TIP.md`) |
| F-1 (cache) — analysis cache shared across creation modes (Manual ranges silently reused as "AI selection") | P2 | `615b618` | Key separation + provenance stamp + poisoned-row discard; F-R2 companion fix `19069e9` (stand-in never persisted) confirmed at rendered level on the tip (acceptance §4: zero stand-in rows after six degraded combos) |
| F-R1 — Find Moments + Manual pick renders ignored template/music (Plain ≡ Auto byte-identical) | MEDIUM | `1c1baba` | Confirmed FIXED at rendered level on the tip: Plain picks ≠ Auto picks, 34 reframe shots vs 0 pre-fix (acceptance §3) |
| F-2 — render-loop reads of stored modes lacked re-validate-and-warn | LOW | `6793620` | Validators + updated audit pin; `SETTINGS_INTEGRITY_AUDIT.md` |
| F-4 — API silently coerced invalid `output_format` / non-bool `add_subtitles` | LOW | `6793620` | Now 422 |
| P1(a) — `.env.example` showed a Claude LLM default against the Groq-first comment/code | P1 | `e67d764` | In-tree verified |
| P1(b) — hook title over the top blur band on surviving fits | P1 | `07ba446` | Rendered BEFORE/AFTER (band boxes wired into the production caller) |
| D1/D2 — caption position + words-per-line dead fields (HIGH) | HIGH | `820171d` | Rendered evidence delivered: `CAPTION_POSITION_EVIDENCE.md` (centres 0.1549/0.4951/0.7549; 14/14 profiles; Reference Style reaches renderer) |
| D3–D7 — editor export honesty set | MED–LOW | `da910b6` + D-series | `EXPORT_HONESTY_EVIDENCE.md` (re-hashed artefacts); audit re-run 69/0/0/0; independent verification 90/91 CONFIRMED |
| Step-3 set (F-A, F-B, D-P1, S-1, D-C1, F-C) — settings→renderer defects | LOW–MED | `75fc951`, `230d8d0`, `a41d614`, `cfc2d29`, `0ba8ef1`, `68bec02` | F-A/F-B rendered proof delivered in the 2026-11-15 proofs batch (5/5 PASS); remainder unit+integration per `SETTINGS_RENDERER_VERIFICATION.md` |
| Step-4 set (F-P2/F-P3 device fps, F-P4/F-P5 missing assets) | LOW–MED | `da441ba`, `eef0332` | Rendered proof in `EXPORT_PARITY_MATRIX.md` lane |
| Roundtrip schema defect — pristine DB got `separation_opt_in` NOT NULL with no default | MEDIUM | `7959ad7` | Roundtrip 2/0 on live pristine PG at the `5ea71bf` gate; blast radius = one column, proven |
| Pydantic-ai deprecation families 1–3 (production sites) | LOW | `235cf5c`, `fd3f3e3` | Golden/provider batches green; decision follow-up remains (§1) |
| Gate pins — 4 stale test pins on `d0d8990` | test-only | `90f1cb4` | Re-gate Q1 1,215/0 + Q2 1,096/0/0 |
| install.ps1 step-5 rehearsal defect — old stack restarted from the BACKUP folder | MEDIUM (packaging) | Fixed in rehearsal scripts; carried into V6/V7/V8 builds | V8: grep-verified 0 occurrences of the buggy form; fail-backup rehearsal PASS on shipped V8 scripts |

## 5. Prioritised defect list (current, 2026-11-15)

- **P0:** none open (tip or branches).
- **P1:** none open.
- **P2:** none open on the tip. (B-1/B-2/B-3/B-4 were P2-class on the
  tip; all four are fixed on branches — §2 — and close at merge.)
- **LOW:** G-2 (phantom ledger row), N2 (comment wording, note only).
- **INFO:** F-3, F-5; package gaps G-V8-1…7 (KNOWN_ISSUES); pydantic
  family-3 decision (before any v2 upgrade).
- **Merge-time obligations:** sweep for same-class stale pins when
  the retention branch merges (its finisher flagged the class); the
  full gate on the merged tree is the arbiter — no branch result in
  §2 counts as gate evidence.
