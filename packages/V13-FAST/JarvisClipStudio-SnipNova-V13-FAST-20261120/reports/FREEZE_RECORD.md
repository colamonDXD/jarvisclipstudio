# FREEZE RECORD — SnipNova release candidate

**Freeze lane executed:** 2026-11-10 (pulled forward from the Nov 20
milestone date — milestones are evidence deadlines, not start dates;
ZERO-IDLE directive). This record freezes the release candidate for
the Nov 15 early-delivery packaging run (Nov 24 fallback intact).

**Freeze commit:** `b4e2a18c684917c1bf3c3b530da871c45243e0ad`
- Branch: `release-candidate`, worktree `~/workspace/jcs-rc`.
- Tree verified clean at the freeze commit before and after the gate.
- The two commits above the combo-gated `6c68e15` are docs-only:
  `9a2340f` (CHANGELOG: brand-stamp music-chain fix entry) and
  `b4e2a18` (`.env.example` duration-cap comment + CHANGELOG entry).
  No code changed after the combo proofs ran.

---

## 1. Freeze gate — run on the EXACT freeze tip `b4e2a18`

Protocol identical to INTEGRATION_NOV12 §6 and combo F-1: backend
`tests/unit` in the four `gate-logs/chunk-q1..q4.txt` file ranges,
serially, `OMP_NUM_THREADS=2`, `--no-cov`, modes venv; frontend
vitest `--testTimeout=120000`; `tsc --noEmit`. Logs:
`research_notes/sprint-7day/freeze-gate/` (`gate-q1..q4.log`,
`fail-q1..q4.txt`, `fail-all.txt`, `frontend-gate.log`,
`frontend-gate-clean.log`, `tsc.log`).

### 1.1 Backend — 3,333 passed / 21 failed / 2 skipped (3,356 collected)

**Identical to combo F-1 — per-chunk and by failure name.**

| Chunk | Freeze (b4e2a18) | Combo F-1 (6c68e15) |
|---|---|---|
| Q1 (53 files) | 871 passed / 11 failed / 0 skipped | 871 / 11 / 0 |
| Q2 (52 files) | 769 passed / 0 failed / 1 skipped | 769 / 0 / 1 |
| Q3 (53 files) | 786 passed / 9 failed / 1 skipped | 786 / 9 / 1 |
| Q4 (53 files) | 907 passed / 1 failed / 0 skipped | 907 / 1 / 0 |

- **Name diff vs F-1 (`combo/F-1/new-all.txt`): ZERO new names, ZERO
  missing names.** The freeze failure set is exactly F-1's 21.
- **Name diff vs the baseline file (`~/workspace/final-failed-list.txt`,
  23 names):** every freeze failure is accounted for — 16 are baseline
  members; 5 are the control-verified pre-existing families below.
  Six baseline names did NOT fail here (the RC is greener than the
  baseline file; §6.1 records them: composition_continuity ×2,
  face_detection ×1, quality_editor_integration ×1,
  source_captions ×1, video_cleanup ×2).

**Every one of the 21 failures, adjudicated:**

| # | Test | Class |
|---|---|---|
| 1 | `test_ai_prompt.py::test_groq_llm_builds_agent_without_unsupported_provider_error` | Baseline; sandbox/provider artefact (no live keys) |
| 2 | `test_ai_prompt.py::test_ollama_llm_builds_native_ollama_model` | Baseline; sandbox/provider artefact |
| 3 | `test_assemblyai_polling.py::test_deployment_model_override_submits_only_universal2_with_speaker_labels[best]` | Baseline; provider artefact |
| 4 | `test_assemblyai_polling.py::test_deployment_model_override_submits_only_universal2_with_speaker_labels[universal-3-5-pro]` | Baseline; provider artefact |
| 5 | `test_assemblyai_polling.py::test_deployment_model_override_submits_only_universal2_with_speaker_labels[universal]` | Baseline; provider artefact |
| 6 | `test_assemblyai_polling.py::test_job_client_has_bounded_timeout_without_mutating_global_settings` | Baseline; provider artefact |
| 7 | `test_assemblyai_polling.py::test_language_is_detected_unless_configured[-None-True]` | Baseline; provider artefact |
| 8 | `test_assemblyai_polling.py::test_language_is_detected_unless_configured[es-es-None]` | Baseline; provider artefact |
| 9 | `test_assemblyai_polling.py::test_real_sdk_retries_gateway_upload_and_submits_once` | Baseline; provider artefact |
| 10 | `test_assemblyai_polling.py::test_transcription_timeout_does_not_resubmit_paid_job` | Baseline; provider artefact |
| 11 | `test_billing_allowance.py::test_task_and_charge_commit_atomically` | Baseline; sandbox artefact |
| 12 | `test_openrouter_analysis.py::test_agent_cache_refreshes_after_key_or_fallback_change` | Baseline; provider artefact |
| 13 | `test_provider_manager_fake_server.py::TestFakeServerProof::test_429_cools_down_and_next_selection_uses_other_alias` | Pre-existing, control-verified identical on unmodified milestone-2 (§6.1; not in baseline file) |
| 14 | `test_provider_manager_fake_server.py::TestFakeServerProof::test_accounting_matches_shim_usage_exactly` | Pre-existing, control-verified (§6.1) |
| 15 | `test_provider_manager_fake_server.py::TestFakeServerProof::test_local_first_routes_to_local_endpoint` | Pre-existing, control-verified (§6.1) |
| 16 | `test_provider_manager_fake_server.py::TestFakeServerProof::test_quota_failure_cools_until_day_boundary` | Pre-existing, control-verified (§6.1) |
| 17 | `test_rerank_nim.py::test_rerank_agent_is_constructed_with_the_nim_base_url` | Baseline; provider artefact |
| 18 | `test_social_caption_service.py::test_caption_uses_haiku_and_grounded_clip_data` | Baseline; provider artefact |
| 19 | `test_social_caption_service.py::test_rejects_invalid_caption[ ]` | Baseline; provider artefact |
| 20 | `test_social_caption_service.py::test_rejects_invalid_caption[xxxx…]` (long-x parameter variant of the baseline name) | Baseline family — same test, longer parameter id than the baseline file records (§6.1) |
| 21 | `test_voiceover.py::test_synthesis_writes_a_cached_wav` | Sandbox artefact — attempts the ~315 MB Kokoro model download over HTTP; no such network path in the sandbox (§6.1) |

**Zero unadjudicated failures. Zero new failures.** The docs-only
delta since F-1 produced exactly the expected result: no code failure
appeared, disappeared, or changed.

### 1.2 Frontend — 779 passed / 1 failed / 12 skipped (792 tests, 126 files)

Identical to combo F-1 (and to §6 modulo skip-count sandbox noise).
- The 1 failed test is the known baseline:
  `src/i18n/messages.test.ts` — "keeps keys and placeholders aligned
  with English". No other test failed.
- The failed FILES are the `src/server/*.integration.test.ts` set
  (affiliates, announcements, user-acquisition — real-Postgres
  integration files; setup fails in the sandbox, zero assertions run
  against product code). Environment-bound, not regressions.

### 1.3 TypeScript — identical 11, set byte-identical

`tsc --noEmit` on the freeze tip: **11 errors; `diff` against combo
F-1's `tsc.log` is EMPTY** — the error set is byte-identical to the
baseline set (prisma-generated-module absence ×2, admin/status-page
implicit-any ×8, admin ReactNode ×1). Zero new type errors.

### 1.4 Migration safety — PASS

`tests/unit/test_migration_files_safety.py` ran inside chunk Q1 (not
among its failures) and standalone: **44 passed**. The tree's sprint
migration set is exactly the 7 files: `20261109_0001_publishing`,
`20261109_0002_content_requirements`, `20261109_0002_task_music_mode`,
`20261109_0003_watch_folder`, `20261109_0004_brand_kit`,
`20261109_0005_creation_modes`,
`20261109_0006_watch_folder_music_mode` (0006 = the residuals variant,
single ALTER; both 0002s coexist as distinct versions — the runner
keys `schema_migrations` by full filename). Migration rehearsal on
scratch SQLite (hardening lanes): full 42-file sequence applies
cleanly in order, 42/42 rows, second pass 0 applied (idempotent);
`UNIQUE(user_id, fingerprint)` present and behaviourally enforced.
Postgres execution itself: no Postgres on this VM — NOT claimed;
user-machine pending.

### 1.5 Bridge assertion — PASS (4/4 files present in the freeze tree)

- `frontend/src/app/api/reference-styles/route.ts`
- `frontend/src/app/api/reference-styles/route.test.ts`
- `frontend/src/app/api/reference-styles/[...path]/route.ts`
- `frontend/src/app/api/reference-styles/[...path]/route.test.ts`

Content traces to the Update-1 bridge commit (`61ad303`, RC-local
cherry-pick `0901808`). The Nov 15 packaging run must assert these
same 4 files inside the shipped ZIP (`zip_assertions.py` covers it).

### 1.6 Gate verdict

**FREEZE GATE: PASS.** The freeze candidate reproduces the
combo-verified gate exactly — backend 3,333/21/2 with every failure
adjudicated, frontend 779/1/12 with the single baseline failure, tsc
set identical, migration safety green, bridge present. Nothing was
waived, skipped, or re-baselined to reach this verdict.

---

## 2. Feature states at freeze (four/five-state table)

Source: INTEGRATION_NOV12 §8 as finalized by §10 (all ten combo
proofs executed 2026-11-10: **Q-A PASS** — composition, classification
live-LLM leg NOT RUN in the sandbox; **R-B PASS; W-C PASS** — service
level; **BR-D PASS** — STOP condition not triggered; **M-E PASS;
E-1 PASS; E-2 PASS; X-1 PASS; P-1 PASS; N-1 PASS; F-1 PASS**) plus
the hardening results (§3 below). UVP = USER-MACHINE VALIDATION
PENDING. "Finalizes" = the §10 rows the combo inventory closed:
QM1 · QM4 · 1.7 · 1.9 · 2.2 · 2.4 · 3.11 · 5.1 (partially) · 6.2 ·
7.10 — finalized as INTEGRATED + UVP, never as user-validated.

### Quality gates

| ID | State at freeze |
|---|---|
| QM1 Subject-aware composition | INTEGRATED + UVP — combo Q-A PASS on the combined tree (unions 17.67/17.02/6.82/11.60/23.78, all ≥ BEFORE 6.68/6.22/3.74/4.36/12.49; r4 below lane-AFTER with named explanation in the verdict) |
| QM2 Retention structure engine | REMOTELY VERIFIED (structure/honesty only) + UVP — retention *effect* is unprovable without audience data |
| QM3 Opening strategies | REMOTELY VERIFIED + UVP |
| QM4 Scene-aware per-niche editing | INTEGRATED + UVP — combo Q-A |
| QM5 Caption quality | REMOTELY VERIFIED + UVP — ramp caption-drift limit stays open (Known Issues Q5) |
| QM6 Dimension sub-scores | REMOTELY VERIFIED + UVP (light) — publishing board doesn't receive the field (Q8) |

### Priority 1 — Clip quality

| ID | State at freeze |
|---|---|
| 1.1 Moment discovery accuracy | IMPLEMENTED + UVP |
| 1.2 Story coherence / context | IMPLEMENTED + UVP |
| 1.3 Retention pacing / beat / ramps | REMOTELY VERIFIED + UVP (beat proof synthetic-source) |
| 1.4 Audio clarity / A/V sync | REMOTELY VERIFIED + UVP — A/V offset 0 ms on all fixture exports; his lip-sync verdict still owed |
| 1.5 Render quality / output integrity | REMOTELY VERIFIED + UVP |
| 1.6 Clip count without padding | COMPLETE-candidate — the only row already validated live on his machine (M2 era) |
| 1.7 Niche classification | INTEGRATED + UVP — combo Q-A: evidence + guardrail legs verified on the real file (guardrail corrects podcast_news → travel_vlog @0.70 on real evidence); live LLM verdict NOT RUN (no sandbox provider key) |
| 1.8 Automatic QC + corrective re-render | IMPLEMENTED + UVP |
| 1.9 Four creation modes | INTEGRATED + UVP — combo M-E PASS (decision + render legs differ as contracted on the combined tree) |
| 1.10 Visual moment selection | IMPLEMENTED + UVP |

### Priority 2 — Campaign requirements

| ID | State at freeze |
|---|---|
| 2.1 Typed requirements | INTEGRATED + UVP — frontend present in RC; vitest suites pass (brief panel 11/11, home-app-brief 3/3, picker 4/4) |
| 2.2 Hard time-range enforcement | REMOTELY VERIFIED (mechanism) + UVP — combo R-B PASS; live Expedia 2:56:29 case still UVP |
| 2.3 Major-focus vs passing mention | REMOTELY VERIFIED (mechanism) + UVP |
| 2.4 Brief + range + honest no-match | REMOTELY VERIFIED (mechanism) + UVP — combo R-B PASS (in-window render + honest `no_compliant_moments`) |
| 2.5 Paste-a-brief + brief library | INTEGRATED + UVP |
| 2.6 Pre-gen summary + compliance report | INTEGRATED + UVP |
| 2.7 Caption/hashtag/CTA compliance | INTEGRATED + UVP — Tier B reading (render-and-flag vs hard-drop) remains Luffy's open product decision |

### Priority 3 — Professional editing

| ID | State at freeze |
|---|---|
| 3.1 Multitrack editor | IMPLEMENTED + UVP (professional depth DEFERRED — §4) |
| 3.2 Trim/split/reorder/undo | IMPLEMENTED + UVP |
| 3.3 AI Editing Assistant | IMPLEMENTED + UVP |
| 3.4 Re-render parity | REMOTELY VERIFIED + UVP — combo E-1 PASS |
| 3.5 Advanced transitions | IMPLEMENTED + UVP — 58 in catalog; only a sample export-proven |
| 3.6 VFX | IMPLEMENTED + UVP |
| 3.7 Colour grading | IMPLEMENTED + UVP |
| 3.8 LUT .cube import | INTEGRATED + UVP — combo E-2 PASS; lane's "COMPLETE" wording stays downgraded under the evidence rule |
| 3.9 Text layers / motion graphics | IMPLEMENTED + UVP |
| 3.10 Multi-speaker / split-screen | IMPLEMENTED + UVP |
| 3.11 Brand Kit | INTEGRATED + UVP — combo BR-D PASS on all four surfaces; intro/outro stored but NOT rendered (Known Issues B1) — partial forever in this release |
| 3.12 AI thumbnails / covers | IMPLEMENTED + UVP — branding ≠ thumbnail intelligence |

### Priority 4 — Reference Style & Viral Intelligence

| ID | State at freeze |
|---|---|
| 4.1 Reference Style Analyser | IMPLEMENTED + UVP — donor-family label imprecision recorded (Known Issues R1) |
| 4.2 Reference Styles end-to-end | INTEGRATED + UVP — bridge in RC (§1.5); final-build create-by-link check still owed |
| 4.3 Proxy repair | INTEGRATED — bridge asserted at freeze (§1.5) and again in the shipped ZIP at packaging |
| 4.4 Reusable styles driving renders | INTEGRATED + UVP — includes the style-niche 422 fix (both layers + regression test) |
| 4.5 Viral Intelligence / Hook Reports | IMPLEMENTED + UVP |
| 4.6 Learning Engine (feedback) | IMPLEMENTED + UVP |
| 4.7 Performance Learning Engine | IMPLEMENTED + UVP |
| 4.8 Reading reference fonts / caption text | DEFERRED-FUTURE (§4) |

### Priority 5 — Audio, B-roll, captions, effects

| ID | State at freeze |
|---|---|
| 5.1 Music Auto/library/custom + ducking | INTEGRATED + UVP — per-task Auto/On/Off + autopilot threading; combo W-C music legs PASS. In-app upload confirmation still outstanding |
| 5.2 Audio Studio | IMPLEMENTED + UVP |
| 5.3 SFX browsing + placement | INTEGRATED + UVP — combo E-2 PASS; lane's "COMPLETE" wording stays downgraded |
| 5.4 Voice/music separation | DEFERRED-FUTURE (§4) |
| 5.5 Kokoro voiceover | IMPLEMENTED + UVP — finalizes only on his machine (GPU speed + first download) |
| 5.6 B-roll matching | IMPLEMENTED + UVP — re-render can lose B-roll (Known Issues BR1) |
| 5.7 Multi-language / Roman captions | IMPLEMENTED + UVP |
| 5.8 Caption animations / text controls | IMPLEMENTED + UVP |
| 5.9 Viral scout pack | PARTIAL by design: velocity slice REMOTELY VERIFIED + one profile opts in (football_edit → Bullet); rest DEFERRED-FUTURE (§4) |
| 5.10 Trend Radar loop | REMOTELY VERIFIED (first cycle) + ongoing — process, not an install feature |

### Priority 6 — Automation, integrations, publishing

| ID | State at freeze |
|---|---|
| 6.1 One-upload full automation | IMPLEMENTED + UVP |
| 6.2 Watch-Folder Autopilot | INTEGRATED + UVP — combo W-C PASS (service level); migration rehearsal enforces the dedupe UNIQUE; real dropped-file run only on his machine; mount decision in §5 of this record |
| 6.3 YouTube source integration | IMPLEMENTED + UVP |
| 6.4 YouTube channel monitoring | DEFERRED-FUTURE (§4) |
| 6.5 Social account integrations | IMPLEMENTED + UVP — finalizes only with his accounts |
| 6.6 Publishing workspace / board | IMPLEMENTED + UVP — never auto-posts, by design |
| 6.7 Scheduling / direct posting | IMPLEMENTED + UVP |
| 6.8 Analytics | IMPLEMENTED + UVP |
| 6.9 Campaign manager | INTEGRATED + UVP |
| 6.10 Per-clip deliverable bundle | INTEGRATED + UVP — combo BR-D cover evidence |

### Priority 7 — Performance, reliability, integration

| ID | State at freeze |
|---|---|
| 7.1 Long-video processing | IMPLEMENTED + UVP — synthetic resilience suite 10/10 (hardening); real 2 h end-to-end UVP; shipped-default duration cap 5400 s (90 min) — his carried-over `.env` sets 14400 (packaging-readiness §5) |
| 7.2 Checkpoints and resume | IMPLEMENTED + UVP — forced-kill/resume proof only on his machine |
| 7.3 Recovery and cancellation | INTEGRATED + UVP — combo X-1 PASS (honest partial completion on the combined tree); aa2fdc74's inner per-clip cause still needs his worker logs |
| 7.4 Queue priorities / pause | IMPLEMENTED + UVP — worker max_jobs=1 by design (RAM/VRAM) |
| 7.5 RTX 4050 utilisation | IMPLEMENTED + UVP — all numbers only from his machine |
| 7.6 NVDEC | IMPLEMENTED + UVP — flag default OFF; A/B only on his machine |
| 7.7 Adaptive VRAM manager | DEFERRED-FUTURE (§4) |
| 7.8 Benchmark baseline | INTEGRATED (pack in tree) + UVP for numbers — combo P-1 PASS (self-test 28/28; no numbers produced, by design) |
| 7.9 Regression / integration gate | INTEGRATED — §6 gate + combo F-1 + **this freeze gate (§1)**, all adjudicated |
| 7.10 Combined next-update integration | INTEGRATED — this RC; combo proofs are its cross-feature evidence |
| 7.11 SnipNova rebrand | INTEGRATED + UVP (light) — combo N-1 PASS; legal-name leftovers (stock "Shiori LLC") await Luffy's decision |
| 7.12 Installer + one-ZIP delivery | PENDING — finalizes only at packaging (Nov 15 run); rehearsals in §3; PowerShell/Docker execution cannot be rehearsed on this VM |
| 7.13 Local-first zero-cost + fallbacks | IMPLEMENTED + UVP — ₹0 rule held throughout the sprint |
| 7.14 Autonomous testing / self-repair | IMPLEMENTED + UVP — "self-repair" = bounded recovery, not an autonomous repair system |
| 7.15 Delivery documentation set | Finalizes at packaging (Nov 15 run) |

### Addendum — live incidents

| Item | State at freeze |
|---|---|
| Preview slide-down bug | INTEGRATED + UVP |
| Manual niche override ignored | INTEGRATED + UVP |
| Music Auto silent on Comedy | INTEGRATED + UVP |
| Style-picker `Invalid niche_override ""` 422 | INTEGRATED + UVP |
| Partial failure shown as total Failed (aa2fdc74 shape) | INTEGRATED + UVP (that task's inner cause still needs his worker logs) |
| Pinned hook title | REMOTELY VERIFIED + UVP (new renders only) |

---

## 3. Hardening results carried into the freeze

- **Migration rehearsal** (release hardening lane): full 42-file
  sequence on scratch SQLite 3.45.1 — 42/42 applied in filename
  order, 42 `schema_migrations` rows incl. both 0002s as distinct
  versions, second pass 0 applied; `UNIQUE(user_id, fingerprint)`
  present on `watch_folder_files` AND behaviourally enforced
  (duplicate insert rejected) — discharges combo W-C's carried leg
  as far as this sandbox allows. Postgres `ON CONFLICT` atomicity
  itself stays by-design + UVP (no Postgres on this VM — NOT
  claimed).
- **Rollback rehearsal** (hardening lane): 10/12 — guards + folder
  mechanics PASS; the 2 misses are the expected docker/robocopy
  absences on this VM. Negative control (missing `db/dump.sql`)
  caught; second rollback from the same backup fails its `project/`
  guard (single-use, by design).
- **Long-video reliability:** synthetic
  `test_ai_long_video_resilience.py` 10/10 on the RC tree; resume /
  checkpoint wiring verified in code (completed_positions re-saved
  after every clip; per-window analysis checkpoints; >1800 s
  transcripts split into overlapping 1200 s windows). Real 2 h
  end-to-end stays UVP.
- **Trial package** (`git archive` of the RC tree):
  `zip_assertions.py --mode final` = 33 passed / 5 failed — all 5
  are packaging-run items (re-scored TRACKER.md, guide
  Expand-Archive / Jarvis_Autopilot / WATCH_FOLDER_PATH content,
  verify.ps1 migration list), closed by the Nov 15 run per
  packaging-readiness §2. The RC-tree side of the final contract is
  fully green.

---

## 4. Deferred features — unchanged from TRACKER (scope freeze)

Kept with their original requirements + future priority in
`~/workspace/research_notes/master-tracker/TRACKER.md`; NOT in this
release:

- **4.8** Reading reference fonts / caption text (OCR / font matching)
- **5.4** Voice/music source separation (VRAM-safe)
- **5.9 remainder** Viral scout pack beyond the adopted velocity slice
  (signature transition, Flash In/Out, transition→SFX map,
  audio-reactive flashes, split-screen, zoom cutouts)
- **6.4** YouTube channel monitoring
- **7.7** Adaptive GPU/VRAM manager
- **3.1 depth** Professional multitrack-editor depth (design note only)

---

## 5. Watch-folder mount — DECIDED

**Decision: installer step (opt-in), guide documents it.** The Nov 15
packaging run implements the watch-folder mount as an explicit step
in `install.ps1` — NOT as a guide-only manual edit, and NOT hardcoded
into the shipped `compose.override.yml` (the RC's shipped override
stays byte-identical to the Hotfix's; no user path is baked into the
release tree).

Exact mechanism (bind mount + env passthrough):
- Services: **backend** and **worker** (the two services that read
  `WATCH_FOLDER_PATH` — backend scans, worker renders; `config.py:244-250`
  in the RC tree). `ai-worker` does not consume the folder and is not
  mounted.
- Volume (both services): `D:\Jarvis_Autopilot:/autopilot:ro`
  (read-only; originals are never moved or deleted by design).
- Environment (both services): `- WATCH_FOLDER_PATH=${WATCH_FOLDER_PATH:-}`
  added via the installer's existing `Add-EnvPassthrough` mechanism,
  plus `WATCH_FOLDER_PATH=/autopilot` set in `.env` by the same step
  (the `.env` is backed up with everything else in step 5). The `:-`
  empty default is deliberate: compose resolves the value from `.env`
  only, so rollback — which restores the backed-up `.env` and
  `compose.override.yml` unchanged — reverts the whole change with
  no residue.
- **Opt-in:** the step asks at install time (default NO). Declining
  leaves `compose.override.yml` and `.env` byte-identical to the
  Hotfix behaviour; the guide states plainly that with the step
  declined, Watch-Folder watches its in-container default folder and
  `D:\Jarvis_Autopilot` drops do nothing (the feature is inert for
  host drops, never silently broken).
- **Guarded:** if either service's `volumes:`/`environment:` section
  is absent or in mapping form, or any edit throws, the step skips
  with a printed warning, reports "watch-folder mount NOT applied",
  prints the exact lines for manual adding, and leaves the files
  untouched — the `Add-EnvPassthrough` discipline (never insert into
  any other list; a failed automatic edit never blocks the install).
- **Backed up:** install.ps1 step 5 backs up and validates
  `compose.override.yml` + `.env` BEFORE this step runs (unchanged
  Hotfix machinery), so the mount edit is covered by the same backup
  as every other installer change; rollback.ps1 restores both files
  wholesale.
- **Guide:** INSTALLATION_GUIDE.md carries the step as a numbered
  item: create `D:\Jarvis_Autopilot` first, what the opt-in prompt
  asks, what success prints verbatim, what a skip prints, and the
  manual-lines fallback.
- The packaging checklist records this as DECIDED
  (packaging-readiness.md §4 patched by this lane); the Nov 15 run
  implements it and states the printed outcome in FINAL_DELIVERY.md.

---

## 6. Known issues

Pointer: `~/workspace/research_notes/release-machinery/KNOWN_ISSUES_DRAFT.md`
— the draft the Nov 15 run finalizes into the shipped
`KNOWN_ISSUES.md` (brand-kit B1–B5, requirements C1–C2, watch-folder
W1–W2, quality Q1–Q9, modes M1–M3, editor E1, B-roll BR1, failure
F1/X1, performance P1–P3, rebrand N1–N2, reference R1–R2, test notes
T1–T2; T2 — the title-pin test blemish — is CLOSED by this gate, §6.1
of INTEGRATION_NOV12 + §1.1 here). Nothing in that draft was found
contradicted by the freeze gate.

---

## 7. The freeze rule — from this commit forward

From freeze commit `b4e2a18c684917c1bf3c3b530da871c45243e0ad`, **only
release-blocking fixes may land before delivery.** A fix qualifies
as release-blocking ONLY if it addresses one of:

1. a **crash** in the core application,
2. **data corruption** (including silent wrong-output corruption of
   exported clips),
3. an **install-verify failure** (the delivery ZIP failing its own
   install.ps1 / verify.ps1 contract), or
4. the **core create → clips → export path being broken**.

Every other change — features, polish, refactors, test-expectation
updates, docs beyond the delivery set — waits for the next milestone.
Each release-blocking fix that lands must be listed here (commit,
what it fixes, which of the four classes it falls under) and the
affected gate section re-run before packaging proceeds.

**Release-blocking fixes landed after the freeze: NONE** (as of this
record).

---

## 8. What remains after this record

Only two scheduled acts: (1) the Nov 12 report finalization, and
(2) the Nov 15 early-delivery packaging run, which executes
`packaging-readiness.md` (now with §4 DECIDED) verbatim — closing the
5 trial-package assertion FAILs, applying the final
install.ps1/verify.ps1 deltas incl. §5's mount step, running
`zip_assertions.py --mode final` to exit 0, and recording SHA-256 +
size + upload URL/expiry in FINAL_DELIVERY.md. Nov 24 remains the
fallback if — and only if — the Nov 15 run hits a genuine gate
failure. Luffy's installed application was not touched by this lane.
