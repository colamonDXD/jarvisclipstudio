# FEATURE_PROGRESS — SnipNova recovery record

*Refreshed 2026-11-15 (IST), accurate to `next-milestone` @ `5ea71bf`
(the frozen, gate-green release tip) plus the six UNMERGED queue
branches named in §14. Every claim traces to a file on disk —
SPRINT_LOG, a lane report, or the repo. Evidence classes: IMPLEMENTED /
INDIVIDUALLY TESTED / INTEGRATED / CROSS-FEATURE VERIFIED / REMOTE
REAL-VIDEO VERIFIED / USER-MACHINE VALIDATION PENDING. Branch work is
labelled BRANCH — it is not in the tip and not shipped. Recovery-doc
set: this file + `BUG_AUDIT.md` + `TEST_RESULTS.md` +
`NEXT_SESSION_PROMPT.txt` + `PROJECT_STATE.md`. Previous version was
accurate to `7959ad7` (2026-11-14).*

## State at a glance

- **Integration tip:** `next-milestone` @ `5ea71bf`, GATE GREEN
  (backend 4,656/0, frontend 899/0), tree clean, **FROZEN** pending
  Luffy's V8 decision. Protected candidate: tag
  `v6-candidate-90f1cb4` → `90f1cb41b0837a04e4e772ee006857938a7db2fb`.
- **On-tip acceptance (2026-11-15):** four-mode real-export re-run on
  `5ea71bf` itself — 32/32 exports; Manual Cutter PASS outright; AI
  modes PASS on the degraded path (keyless sandbox; NOT AI-quality
  proof). See `REAL_EXPORT_ACCEPTANCE_ON_TIP.md`.
- **V8 package:** built from this tip, verified (manifest 14/14,
  SHA-256 `7807b4576c7d562eeba8d7453609351580ee99c4fa185ca0bfcf609f860145c5`),
  **NOT APPROVED FOR INSTALL** — Luffy's decision pending.
- **Installed on Luffy's machine:** the V7 code tree (app tree
  byte-identical to V8), config restored to shipped state after a
  third-party tool rewrote it; LiteLLM VERIFIED HEALTHY post-restore.
- **Full-scope audit (2026-11-15, 86 rows, `FULL_SCOPE_AUDIT.md`):**
  Class 1 IMPLEMENTED AND VERIFIED **21** · Class 2 IMPLEMENTED BUT
  NOT VERIFIED **31** · Class 3 PARTIALLY IMPLEMENTED **15** · Class 4
  IN DEVELOPMENT **0** (at audit time) · Class 5 NOT STARTED **14** ·
  Class 6 BLOCKED / NEEDS DECISION **5**. The 78-row tracker
  (2026-11-14 re-score: 6 COMPLETE / 21 PARTIAL / 0 MISSING / 51
  NEEDS REAL-WORLD VALIDATION) is the narrower instrument; the audit
  is wider and governs.

## Per-feature status (on the frozen tip, unless marked BRANCH)

### 1. Plain default + P0 settings read-back fix — CROSS-FEATURE VERIFIED; user-machine proof pending

The v3/v4/v5 defect (serializers dropped `template_mode`,
`music_mode`, `separation_opt_in`, `source_duration_seconds`; every
Plain task rendered as Auto) is fixed @ `ef87820` and independently
verified incl. the live-PostgreSQL round-trip
(`SETTINGS_INTEGRITY_AUDIT.md`: 26 settings × 9 stages — 22 proven
end-to-end, 3 absent-by-design, 1 pair proven-with-caveat; F-2/F-4
fixed @ `6793620`). On-tip acceptance adds the rendered level: every
Plain export's source map is 100% `plain` shots; audio
cross-correlation ≈0.999 (no music bed); settings survived a
PostgreSQL restart byte-identically. First *true* Plain run on his
machine (TJR) is still owed — USER-MACHINE VALIDATION PENDING.

### 2. Smart B-Roll v2 — CROSS-FEATURE VERIFIED in sandbox; USER-MACHINE evidence NEGATIVE (see §14 retention)

All six slices merged (sense table, concept-writing brain, resolver
v2 + Pixabay/Coverr, SigLIP veto ON in Auto — calibration 0/44 missed,
2/44 false vetoes; expansion to 100 pairs: 1 miss, 6 false vetoes, no
better threshold), finance density 1.5/min, editor review surface.
Concept-phrasing fix merged @ `9b24c7b` (115/0). **Live calibration of
the concept fix and real-pool behaviour remain user-machine.** His
first real Auto run (2026-11-15) delivered 1 insert in 165 s — the
retention branch (§14) addresses the silent-loss mechanism; which
stage caused his run is retroactively unanswerable.

### 3. Model ladder v2 — INTEGRATED; live verification pending

Shipped ladder: deepseek-chat → clip-assured (Groq gpt-oss-120b) →
clip-fast (NIM Nemotron Ultra) → nim-deepseek → openrouter-free →
cloudflare-free → groq-20b → local-qwen (Ollama) LAST. The paid-Luna
default trap is fixed + pin-tested @ `5395c0a`; the Cloudflare hop is
in all 15 chains (failure drill 6/6). Provider-death and interruption
drills prove failover never interrupts a generating clip. Live walk
on his real keys: USER-MACHINE VALIDATION PENDING (his restored live
ladder is verified healthy at the gateway level, 2026-11-15).

### 4. NIM / full-ladder bake-off harness — INDIVIDUALLY TESTED; live phase NOT RUN

Harness @ `025ffe9` drives the app's real prompt builders/schemas +
gold scorer; 35/35 after fixtures A/B/C merged @ `eafbbe5`. Six NIM
candidates pre-registered UNARMED. Protocol: `NIM_BAKEOFF.md` +
`AI_MODEL_BAKEOFF.md`. **The live run has never executed** — it needs
a real NVIDIA key inside his worker container; no winner is named or
wired, and none may be armed on benchmark claims alone (validity 8/8
gate; win must exceed the 0.1 noise floor; rubric regression >10
vetoes).

### 5. Eco render mode — INTEGRATED; user-machine benchmark pending

@ `9e6e750`: `RENDER_ECO_MODE` default OFF; scheduling only (effective
concurrency 1 + bounded ffmpeg threads + nice); quality constants
byte-untouched and pinned; flag-off behaviour byte-identical. NVENC
behaviour on his RTX 4050 is code-verified only — the benchmark kit
(`RTX4050_BENCHMARK_KIT.md`) is the owed proof. See also §14 (perf
branch: thread budget for non-Eco renders + encoder diagnostics).

### 6. Hook title out of the blur band — CROSS-FEATURE VERIFIED; user-machine judgement pending

@ `07ba446`: the title builder receives strip-derived band boxes
(faces outrank bands). Rendered BEFORE/AFTER in the lane's frames.
Bert re-run on his machine is the remaining judgement.

### 7. Caption position wiring (D1/D2) — REMOTE REAL-VIDEO VERIFIED

Fix @ `820171d` (explicit position is a preference the face-avoidance
guard may override; no-position tasks render byte-identically —
pinned). Rendered evidence delivered (`CAPTION_POSITION_EVIDENCE.md`):
Top/Middle/Bottom centres 0.1549 / 0.4951 / 0.7549 in actual renders;
14/14 profiles honoured; Reference Style measured position reaches
the renderer (0.4901 applied vs 0.7760 control).

### 8. Editor export honesty (D3–D7) — REMOTE REAL-VIDEO VERIFIED

Fixes @ `da910b6` (honesty set) + the D-series commits. Evidence pack
(`EXPORT_HONESTY_EVIDENCE.md`) proves D3/D4/D5 with re-hashed
artefacts. Editor audit re-run: **69 WORKS / 0 PARTIAL / 0 UI-ONLY /
0 inverse**; independent verification re-traced all 91 inventoried
rows — 90 CONFIRMED, 1 drift (N1, claim narrowed @ `0a63942`).

### 9. Brief-from-screenshot — INTEGRATED; user-machine proof pending

OCR (tesseract, in the shipped image) → paste-a-brief extraction;
brief schema v2 carries `excluded_ranges` (hard blocks), `flag_points`
(clips stamped `needs_review`; flags do NOT trigger automatic plate
blurring), `key_timestamps`. On his diamonds-brief screenshot: OCR
25/25 timecodes, flags 18/18. Draft-only — the user clicks "Use this
draft". Live upload on his machine: pending.

### 10. Four creation modes — CROSS-FEATURE VERIFIED on the tip (degraded path); live AI proof pending

Full AutoClip (default, never parks) / Find Moments + Manual / Find
Moments + AI Editing / Manual Cutter + AI Editing. Parkfix: the sweep
auto-picks top moments after 15 min with an honest note. On-tip
acceptance (2026-11-15): Manual Cutter PASS outright × Plain/Auto;
all AI combos reach DEGRADED COMPLETION honestly stamped in the
keyless sandbox (D-1 floor); F-R1 (picks ignored template) and F-R2
(stand-in persisted to cache) confirmed FIXED at rendered level;
checkpoint resume proven through a real VM restart. Real AI selection
quality with providers live: USER-MACHINE VALIDATION PENDING.

### 11. Selection floor + degradation (D-1) — IMPROVED + MERGED @ `9ba6602`; safety net, honestly bounded

Floor fuses global level with local prominence + decay-gated anchors;
identical-harness measurement: R1 recall 0.00→0.20, R3 recall
0.00→0.40; R4 slate-5 still 0.00 (explained, not chased — forcing it
would be fixture-fitting). D-1 CLOSED: analysis failure degrades to
the floor with stamped segments + a task-page degradation notice
(@ `51c7ff7`, `1c3df9e`, `9c546cd`) instead of killing the task. The
floor is a safety net, not a selection strategy; live-provider scores
on the same harness are still owed.

### 12. Learning Engine — backend WIRED on the tip; end-to-end proof on BRANCH (test-only); UI unwired (frontend owner's)

The audit's "forgotten connection" was the UI, not the backend.
Traced in code at `5ea71bf` and proven on `feat/learning-engine-backend`
(§14): explicit verdict endpoint (422/403/404 discipline) + passive
signals → append-only storage → **consumption** (selection ranking
under learned weights; EDL profile adjustments to SFX density, music
volume, caption preset) + status/rollback API. Contract:
`LEARNING_ENGINE_API_CONTRACT.md`. The accept/reject buttons have a
client helper (`sendClipFeedback`) with **no .tsx caller** — wiring
them is Luffy's frontend work, as is the `/api/learning/[...path]`
proxy route (contract-pack discrepancy D1).

### 13. Other tracked systems (short status)

- **Niche classification — COMPLETE (tracker row 1.7).** Live-proven
  on his 2026-11-11 Bert run (10/10 Meme & comedy); finance dialect +
  evidence-window fixes merged (`3024cea`, `6942209`); the real $10M
  Trader transcript classifies `finance_business` on both paths.
- **Campaign requirements / briefs — NEEDS REAL-WORLD VALIDATION.**
  Typed briefs, allowed ranges, keyword focus, compliance reports
  merged; sandbox-proven on the real fixture (range enforcement 100%
  in-window, honest `no_compliant_moments`). No real campaign has run
  on his machine yet.
- **Installer / rollback / DB recovery — rehearsed PASS; V8 package
  verified.** Rehearsals (real PG 16.15, product `init_db()`): fresh
  48/48, resume 30→48 exact, dropped ledger self-heals, wrong-SHA
  refusal before side effects, rollback byte-for-byte. V8 adds:
  zip assertions 38/0/0, manifest 14/14, fail-backup rehearsal PASS
  on the shipped scripts. The install itself awaits his approval.
- **PostgreSQL tier — PROVEN LIVE at the tip gate.** SQL-exec family
  135 passed / 7 xfailed / 0 skipped on live PG 16.15; editor
  roundtrip 2/0 on a pristine DB (the `7959ad7` roundtrip fix holds).
- **Quality Director / scoring v2 / viral intelligence — NEEDS
  REAL-WORLD VALIDATION.** Scored dimensions with reasons, hook
  strategies, retention guardrails — sandbox-proven; no audience data
  exists to prove a retention effect, by design.
- **Roundtrip schema fix — MERGED @ `7959ad7`.** `init_db()` ran
  `create_all` before migrations; `separation_opt_in` ended NOT NULL
  without a default on pristine DBs. One-line `server_default` fix;
  blast radius proven = that one column (all 60 migration ADD COLUMNs
  cross-checked); pin `test_pristine_schema_defaults.py`.
- **Pydantic-ai deprecations — families 1–3 fixed + merged**
  (`235cf5c`, `fd3f3e3`). Remaining follow-up (not a defect): decide
  normalise-or-reject for user-supplied `openai:` strings before any
  pydantic-ai v2 upgrade.

### 14. Branch work — IMPLEMENTED + INDIVIDUALLY TESTED on branches; NOT INTEGRATED, NOT SHIPPED

All six branches sit on `5ea71bf`, parent-verified, merge queue parked
on the V8 decision (see PROJECT_STATE.md for the queue order). Full
gate on any of them: NOT RUN (the gate belongs to the merged tree).

| Branch (tip) | What it proves / changes | Lane evidence |
| --- | --- | --- |
| `fix/floor-export-parity` (`f7f66a1`) | B-roll gate contract — explicit `broll_enabled` authoritative, legacy `include_broll` honoured only when the switch was never set, Plain always OFF (kills the silent toggle-ON + chip-OFF trap) — plus floor segments' hook titles derived from their own grounded text via the AI path's sanitizer. Step-0 REFUTED the "floor exports lack captions" claim (captions burn from the transcript sidecar on every path; the observation was a word-less seeded sidecar). | 61 PASS (gate matrix + seams) + neighbours 137 PASS / 1 skipped + creation modes 112 PASS; rendered before/after: same 11 caption events + a new Hook event; frames confirm title appears |
| `feat/recipes-foundation` (`74381f8`, 9 commits, **2 migrations**) | Recipes end-to-end at the backend: storage + CRUD + fragment API; `recipe_id` on `POST /tasks` (fragment merges UNDER the request; explicit wins; unknown/cross-user/malformed → 422, nothing created; provenance stamped in `source_metadata`); watch-folder autopilot `recipe_id` (validated at save; deleted recipe refused loudly). Contract §6.7 in `API_CONTRACT_PACK.md`. UI + proxy route: frontend owner's (D2). | Foundation 55 + wiring 26 + autopilot 36 PASS; neighbours green; live-PG tier NOT RUN |
| `feat/learning-engine-backend` (`4582a0f`) | Test-only: the end-to-end loop proof for §12 (feedback flips the next selection's pick order through the real service + real repository SQL; rollback restores it; cross-user isolation; below-threshold no-op). No production code changed. | Family 50 PASS / 0 FAIL |
| `refactor/broll-consolidation` (`fd6e432`) | One B-roll resolution path: every cue sources through the v2 resolver (Pixabay→Coverr, provider-stamped); profile `source: "pexels"` → `"stock"` (8 profiles, densities untouched); editor search/suggest, AI-edit AddBRoll, `/broll/status` un-gated from the Pexels key (they 503'd on his deployment); caption-edit re-renders no longer drop stored v2 cutaways. Pexels remains stored-footage compatibility only — zero new placements. | 846 PASS / 1 skipped; rendered proof: stored placement 0 overlays BEFORE → insert present AFTER; live-provider behaviour NOT VERIFIED (no sandbox keys) |
| `feat/retention-editing` (`d1f35f0`) | Closes the silent B-roll loss: every undelivered insert stamped `not_inserted {stage, reason}` (placement drops recorded too); per-task `BrollUsageLedger` (siblings never share an asset); transition discipline (decision joins → hard cuts inside continuous speech; no back-to-back identical transitions); universal opening push-in 1.0→1.12 on Auto EDLs (Plain strips it). | 741 PASS / 0 FAIL after the re-pin sweep (3 stale pins, each proven stale); rendered proof PASS: siblings got assets 101 vs 102 (before: 101 again), mid-speech slide → cut with no xfade, push-in measured 1.231× vs 1.080× natural |
| `perf/rtx4050-render-path` (`19bd1e7`) | Render-path audit + fixes: ffmpeg thread budget `ceil(cpus/concurrency)` clamped [2,8] (Eco stays 4; quality constants byte-untouched, pins pass); encoder diagnostics (`encoder`, `nvenc_probe_error`) stamped into persisted `stage_timings_json` — the NVENC→CPU fallback is no longer silent from the user's seat. Audit verdicts: NVDEC opt-in OFF and not passed by shipped compose (wiring gap); face tracking + filter graph CPU by design; transcription GPU by design. No single regression commit — per-clip workload grew + the Nov-12 serialisation. | 170 targeted PASS; full suite NOT RUN; on-GPU proof = his machine via `RTX4050_BENCHMARK_KIT.md` |

Also queued (no branch): adopt the python3 urllib healthcheck probe
into the repo's `compose.override.yml` template (his live override
already runs it; the shipped curl probe can never pass — the LiteLLM
image has no curl).

## What is NOT done (do not misread this file)

- **Nothing in §14 is merged or shipped.** The merge batch + a full
  re-gate on the merged tree happen only after Luffy approves V8 and
  the release closes. Any merge moves the tip and invalidates
  tip-bound evidence until re-gated.
- **V8 is NOT APPROVED FOR INSTALL.** His decision is pending; V7 is
  superseded and also NOT APPROVED.
- **Live bake-off: NOT RUN** (blocked on his keys by design). No model
  winner is named or wired.
- **AI-quality validation: user-machine only.** The sandbox is keyless
  by design; every AI-mode proof here is pipeline/honesty proof on
  the degraded path, never selection-quality proof.
- **Audit Class 5 (NOT STARTED, 14 at audit time):** Recipes is now
  branch-built (§14); the rest remain unstarted — pre-processing
  summary panel, creation-workspace redesign, task-page run summary
  (the three UI items are the frontend owner's), animated Viral Hook
  templates, motion graphics, AI-generated thumbnails, caption
  translation/dubbing, whole-task AI edit, chapters/show-notes, Trend
  Radar *system*, adaptive GPU/VRAM manager, retake detection, 4K
  export. Several are on the disclosed post-Nov-22 deferral list
  (his explicit approval of that list is still pending).
- **Audit Class 6 (BLOCKED / NEEDS DECISION, 5):** F-S1 publishing
  kill-switch (his decision), F-5 `processing_mode` (his decision),
  music/SFX library provenance (libraries exist only on his machine;
  zero files in repo/ZIP), pyannote terms-accept (his click; blocks
  diarization), live bake-off (his machine/keys).
- **Forgotten connections, current state:** Learning Engine UI
  (§12 — frontend owner's), Demucs separation has no frontend surface
  (API-only opt-in), Trend Radar system never built (three hand-merged
  candidates exist under its name), state docs — refreshed by this
  very update (2026-11-15).
- Everything marked USER-MACHINE VALIDATION PENDING needs Luffy's
  laptop (his keys, his library, his GPU) and cannot be closed from
  this VM.
