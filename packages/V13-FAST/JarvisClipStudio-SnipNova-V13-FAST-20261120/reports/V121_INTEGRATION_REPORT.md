# V12.1 Final Integration — Final Report (§9 format)

**Branch:** `integrate/v121-final` (worktree `~/workspace/jcs-v121final`)
**Base:** shipped V12 tree, `next-milestone` @ `49cfad22`
**Final tip:** `3900c3da` — 50 commits over the base. NOT packaged, NOT merged into `next-milestone` (per brief).

---

## 1. Applied

### Series A (18 patches, combined delivery, `git am` via context-apply helper)
`git am` aborted at A-0006 (Claude's CHANGELOG blob lineage differs from the V12 tree's); all 18 were applied with a git-apply + mailinfo helper (context matching only, no fuzz). A tip: `c8a78d76`.

- 0001 licence-gated asset library + Openverse B-roll · 0002 content cuts (Step 1) · 0003 fit/pad + 4K probe (Step 2) · 0004 caption loop filter + phrase split (Step 5) · 0005 story events (Step 4) · 0006 screen-as-subject (Step 3) · 0007 screen ≥25% guard · 0008 split-shot screen guard (reduced — V12 already carries Sanji's unify guard) · 0009–0011 transition library (248 entries) + burned-caption detector + catalogue API · 0012 OFL/Apache font pack · 0013 THEBOLDFONT dropped → Montserrat-Black alias · 0014–0017 live MVP tools · 0018 `--streamer-words-only` tool.
- **NVENC: verified ABSENT from the whole series** — and re-verified on the final tree: `git diff 49cfad22..HEAD` contains **0 added NVENC lines**. NVENC stays exactly as shipped in V12.
- Switch defaults unchanged: all quality switches OFF; `ASSET_VARIANTS` + `BROLL_OPENVERSE` on.

### Series B — three deliveries, final = combined3 (13 patches)
- Delivery #1 (7 patches) and #2 (11 patches) were integrated and then **superseded**; their gate results are STALE and were discarded.
- **B-diff settlement (delivery #2 → #3):** patches 0001–0011 have identical sizes but different bytes. I diffed them with headers/index lines stripped: **all 11 are hunk-identical**. Path taken: reset to the B-0011 tip, `git am` only **0012 + 0013**, then re-cherry-picked my two Sanji commits. Evidence: per-patch "HUNKS IDENTICAL" for all 11 (normalised diff, 0 changed hunk lines each).
- B contents (final): 0001 gaming-layout delegation fix (the earlier wrong forced-render fix `1cdd8c6` is **reverted by 0007** — verified: the `and not layout_template` line is absent; the planner delegates to the plain path when a layout template is chosen AND `find_facecam_rect` finds a facecam) · 0002 live clipping in the app + `live-worker` service · 0003 Live page + nav entry · 0004 yt-dlp venv fix · 0005 fast speech model for live · 0006 Groq hosted-Whisper captions · 0007 17 s floor (now 30 s, see 0012) + white seam captions + title on/off + one Layout picker · 0008 `tasks.hook_title_enabled` added to 7 hand-built test tables (test-only, nothing weakened) · 0009 VOD fast clipping of finished streams (`live/vod.py`) · 0010/0011 live perf fixes · **0012 profanity masking in burned-in captions + hook titles (`media/profanity.py`, `CAPTION_PROFANITY_FILTER` default ON, whole-word-only for ambiguous words) + owner rule `CLIP_MIN_SECONDS` default 17 → 30** · **0013 `media/audio_finish.py`: the plain (face-on-top) path now mixes the EDL audio plan (music bed + SFX) over the finished clip; Plain "just cuts" EDLs carry no music and stay untouched; `PLAIN_PATH_MUSIC` default on**.
- Migrations on the final tree: **55** (52 + `20261118_0001_live_clipping` + `20261118_0002_task_hook_title_enabled` + `20261119_0001_live_session_progress`). New migration comments contain **0 semicolons** (checked).

### Sanji commits on top (in stack order)
1. `0e7a3d55` fix(gateway): LiteLLM `timeout: 90` + `stream_timeout: 90` under `router_settings` (a task had hung 20+ min on one unreturned NIM call). The config test pins only num_retries/cooldown — stays green.
2. `5b478a84` test(captions): retarget two suites at series A/B product changes (font name Montserrat-Black for the dropped THEBOLDFONT; the caption roundtrip pins `CLIP_MIN_SECONDS=0` because the floor legitimately lengthens its 1.5 s clip — assertions untouched).
3. `6c50e0a1` **fix(render): clip floor no longer rewrites a cold-open opening** — see §4.1. Includes a CHANGELOG entry (2026-11-19) and 2 regression tests.
4. `001d4d5d` test(sql): LiveRepository statement-execution coverage + inventory re-pin + Postgres tier — see §4.2.
5. `f48b2355` test(fonts): stage-2 font eval re-based on the 34-font bundle — see §4.3.
6. `0853deec` merge: UI overhaul round 2 (see §5).
7. `98dd74fe` test(frontend): ResizeObserver stub in the jsdom setup — see §5.
8. `536a4924` test(frontend): retarget the create-page suites at the Layout picker.
9. `c8491064` fix(changelog): the Bold Font entry's category `Changed` → `Fixed` (the changelog parser only knows New/Improved/Fixed/Under the hood; series A's entry failed `changelog.test.ts`).
10. Music + brief test timeout commits (`3900c3da` is the last) — see §5.

### Overlap cross-check vs `integrate/v121-quality` @ `f1f49b03`
All 26 backend files of A-0001–0008 are **byte-identical** at the A tip to f1f49b03's versions — **zero semantic divergence**. (Final-tree differences in `reframe_plan.py` / `reframing.py` / `video_service.py` are exactly B-0007's intended changes.)

### INSTALLED_APP_CHANGES.md cross-check (final tree)
Every repo change it lists is present: `backend/src/live/` (incl. `vod.py`), `api/routes/live.py`, `repositories/live_repository.py`, `workers/live_worker.py`, migration `20261118_0001`, the live router wired in `main.py`, `frontend/src/app/live/` (page + layout), `frontend/src/app/api/live/[...path]/route.ts`, the app-shell Live entry, home-app + `layout-picker.tsx`, `media/profanity.py`, `media/audio_finish.py`, the `live-worker` service in the repo `docker-compose.yml` (container `supoclip-live-worker`, `LIVE_CLIPPING=${LIVE_CLIPPING:-off}` — repo default OFF). **Install-only items are absent by design** and are listed in §7: installed compose defaults `LIVE_CLIPPING` on, the live-worker `image: jarvisclipstudio-worker:latest` reuse line, and the compose.override.yml block. The doc's database-rows items (test account, session ownership, failed-clip marking) are install-side data, not repo content — nothing to land.

### Licence gate (Claude §10 hand-off)
**Ship-clean, no manifest additions needed on this tree.** A-0001's `assets/ASSET_MANIFEST.json` (391 entries) already carries V12's base files with licences and SHAs that match the shipped bytes:
- Gate over the V12 package's own assets (7 music + 6 SFX): **0 problems**.
- Gate over the full V12.1 ship set (repo assets incl. the 15-file SFX library + the 7 base MP3s staged as packaging stages them; 29 audio files): **0 problems**.
- Music entries: `CC0-1.0`, source `freepd`, per-track author/title from `docs/assets/MUSIC_PROVENANCE.md`. SFX entries: licence `original`, source `synthesised: tools/assets/make_sfx.py`. **No licence was invented; no file's licence was undeterminable in the ship set.**
- Why Claude saw 55 problems: his lineage's copies of the base files differ byte-wise from the manifest his A-0001 built (his install's `assets/music` holds 34 files vs the 7 shipped; his fetched copies differ). Against the actual V12-shipped bytes the manifest is exact. For the report only: Claude installed the extended library on Luffy's install (music 34→123, SFX 21→310); the V12.1 package still ships the base bundle, the extended library remains a post-install fetch per the Q14(b) settlement.

---

## 2. Tested — backend (exact counts)

### Step-1 DB suites FIRST (final A+B13 tip `5b478a84`, scratch Postgres, real `AsyncSessionLocal`)
- test_task_insert_columns 2 passed · test_task_settings_readback 6 passed + 1 skipped · test_settings_integrity_matrix 9 passed · test_framing_mode 36 passed · test_migration_files_safety 1 passed (55 migrations).
- Live + VOD with DB: **54 passed** (live_api 13, live_worker 4, buffer/fuser/moments/voice 25, Groq 5, VOD 6 — batch total 54 as reported by pytest).
- Re-run on the gate-4 tip `f48b2355` (step-1 + live + VOD combined): **108 passed, 1 skipped**.

### Named + delta suites (tip `5b478a84` unless noted)
- Profanity + audio_finish + edl_render_audio: **28 passed** (profanity 15 ✓ = Claude's count, audio_finish 4 ✓).
- 48-group remainder (asset_library, openverse_broll, gaming_templates, clip_min_and_layout): **35 passed**. Gaming seam-stroke pin is `#000000` (Claude's deliberate owner-directed change from `#E5262B`) — present and passing.
- Delta batches over every backend file A+B touch: A2 (17 files) 447 passed / 7 skipped · B (reaction/reframe/render + pristine_schema + compose_flag + speaker_turns + render_stage_timings) 62 passed · C (facecam/reaction/reframe, 15 files) 173 passed.
- LiteLLM config suite: 27 passed (with `frontend/src/generated` absent in this worktree — the certified condition).
- Postgres execution tier (real scratch Postgres): **10 passed**, incl. the 2 new live statements (§4.2).

### FULL backend gate — fresh on tip `f48b2355` (gate 4, chunked, TMPDIR on workspace, no DATABASE_URL)
**5,634 passed / 0 failed / 262 skipped / 9 xfailed / 1 error.**
- The 1 error is `test_live_api` setup without a database (OSError) — the certified no-DB pattern, adjudicated by the DB-backed run on the same tip (108 passed above).
- Coverage reconciled exactly: chunk outcomes 5,904 + `tests/live` 2 (skipped by design; the driver's summary grep misses skip-only lines — cosmetic) = **5,906 = `pytest --collect-only` total**. No file was missed despite the driver's unstable chunk naming (one group ran twice with identical results, 590/590).
- The same gate on the superseded tip `5b478a84` (gate 3) found the 12 failures dissected in §4; gate 4 is the verdict that counts.

### Frontend (final tip `3900c3da`; post-gate-4 commits touch frontend files + CHANGELOG only — `git diff f48b2355..HEAD` = 10 files, 0 backend)
- Targeted retarget run: framing 4/4, gaming-layout 5/5, template 3/3, broll 5/5 (see §5 for the ResizeObserver precondition).
- **Full vitest, serial (`--maxWorkers=1`), 144 non-integration files on the final tip `3900c3da`: 144 files passed, 930 tests passed, 0 failed** (single clean run, `logs/vitest_serial_final2.log`). The earlier serial run on the pre-timeout-fix lineage was 929/1 (the brief timeout, §5); the final run supersedes it.
- Integration files (11, run separately): the 7 non-server files 14 passed / 1 timeout in one serial group; the timeout (social-publishing parity, §5) passes solo 4/4. The 4 PGlite server suites pass solo: affiliates 3, announcements 2, service-status 2, user-acquisition (passed in group run). Total frontend across all files: **959 tests** (930 + 15 integration-non-server + 14 across the 4 PGlite suites' counted tests) — matching the previous effective 959 baseline with the retargeted picker suites included.
- `changelog.test.ts`: PASS (after the §1.9 category fix).
- tsc: **exit 0** (tsconfig.tsbuildinfo deleted first).
- next build: **exit 0, 76/76 static pages** (V12-era builds: 75/75; **+1 is the `/live` page**; `/api/live/[...path]` route present). BetterAuth default-secret warnings in the log are the known sandbox-env noise, present in prior builds.

---

## 3. Failed / NOT RUN

- **FAIL (real, found by gate 3, fixed and re-gated):** the three clusters in §4. Nothing from gate 3 remains failing in gate 4.
- **NOT RUN:** live-resolver / SigLIP veto legs that need Pixabay/Coverr keys or the cached veto model — the sandbox has neither (same limitation as every prior gate on this machine; they run on Luffy's install where the keys live).
- **NOT RUN:** smoke acceptance of the packaged app (packaging is a separate lane; V12's was also NOT RUN at build time).
- Frontend full-suite parallel runs on this 1.5-CPU sandbox produce rotating 5 s/10 s **timeout** failures (never assertion failures) — every affected test was adjudicated solo and passes; see §5. Treat any single parallel full-run number as NOT RELIABLE on this machine; the serial runs are the evidence.

---

## 4. Divergences and gate-found defects (all fixed on this branch)

### 4.1 The 30 s floor broke cold-open hooks — REAL product bug (fixed, `6c50e0a1`)
Gate 3 failed `test_hook_enforcement.py::test_pipeline_cold_open_moves_hook_sentence_to_front`: the plan correctly moved the hook sentence to the front, but the exported file opened with the source's original sentence (300 Hz obtained, 900 Hz expected). Bisected by environment: with `CLIP_MIN_SECONDS=0` the file passes 15/15; at the default floor it fails. **Mechanism:** `enforce_min_clip_duration` (B-0007, stricter in B-0012) extended the last range forward, then extended **the first range backward** — after a cold open the first range IS the transplanted hook, so the export re-opened with the original opening. **Fix:** the first range's start never moves; the last range grows forward, then backward into its own lead-in, clamped at the latest source end of any earlier range (no overlap, no duplicated footage); when the source is exhausted the floor yields. Single-range behaviour (the documented "starts earlier" case) is preserved and still pinned. 2 regression tests added; hook suite **15 passed at the default 30 s floor**; gate 4 group green (413 passed).
**Open owner question (unchanged from before):** whether the clip floor should apply at all to editor caption re-renders — my earlier caption-roundtrip pin (`CLIP_MIN_SECONDS=0` in that one test) stands; the floor itself is covered by test_clip_min_and_layout. Owner decision, not silently changed.

### 4.2 Series B shipped 9 raw SQL statements with no execution coverage (fixed, `001d4d5d`)
Gate 3 failed `test_sql_inventory_guard` twice: canonical INSERT count drifted 39 → 41, and 9 `live_repository.py` statements (create_session, record_moment, request_stop, claim_requested, fail_stale, heartbeat, finish_session, update_clip, delete_session) had no statement-execution test — the exact hole that broke task creation on 2026-11-11. **Fix, following the guard's own prescription:** new `tests/unit/test_sql_exec_live.py` executes 7 statements for real on SQLite; the 2 Postgres-only ones (`claim_requested` — FOR UPDATE SKIP LOCKED; `fail_stale` — INTERVAL literal, method-level via ExecSession) are documented strict xfails in the SQLite tier **and executed for real in the Postgres tier** (2 tests added; tier run: 10 passed against the scratch Postgres). Canonical counts re-pinned to the true inventory **41 / 75 / 19** (deltas are exactly the live module: +2 INSERT, +6 UPDATE, +1 DELETE). Guard: **13 passed, 2 xfailed**. No test was weakened — coverage was added.

### 4.3 Stage-2 font eval still planted the dropped THEBOLDFONT (fixed, `f48b2355`)
Gate 3 failed 5 tests in `test_reference_style_look_stage2.py`: the eval planted THEBOLDFONT (dropped by A-0013) and pinned the bundle at 25 fonts (now 34 after A-0012). **Fix, by the suite's own methodology (pins sit below measured rates):** I measured the current bundle with the fixture's own pipeline over 14 planted fonts. BarlowCondensed-Bold (from A-0012's pack) is the only candidate that fills THEBOLDFONT's decisive role (raw top-1 hit **and** gate emission); TikTokSans-Bold hits but stays silent; LeagueSpartan lands in the wrong class. The eval list swaps THEBOLDFONT → BarlowCondensed-Bold; measured rates on the 34-font bundle came out at **exactly 9/12 overall and 3/5 heavy** — identical to the original calibration — so **no pin was lowered** (≥7, ≥3, class-correctness, gate precision, Montserrat coin-flip silence all unchanged in strength). Bundle count pin updated 25 → 34 with the reason recorded in a comment. Suite: **26 passed**; gate 4 group green (383 passed).

### 4.4 Environmental failures in gate 3 — adjudicated, not product
4 OCR failures (`test_caption_face_guard_render` ×3, `test_content_requirements_brief_image` ×1): the chunk's subprocess environment could not find `tesseract` (`FileNotFoundError`; the binary exists and works from a normal shell). Solo re-run of both files: **22 passed**. In gate 4 the same group ran clean (321 passed).

### 4.5 Series divergence notes
- Claude's §7 claim for combined2 (246 passed / 0 failed on his side incl. DB suites) is consistent with my independent DB runs (54 + 54 on the step-1/live lists) — but his full-gate-equivalent evidently did not include the SQL inventory guard or the font-eval suite, which is where series B/A actually broke (§4.2, §4.3). His note that 6 caption-render tests fail on Windows with and without his change: on this Linux run the caption-render suites pass (the 4 OCR-adjacent failures were the tesseract-environment issue in §4.4, since resolved).
- B-0008 (Claude's own hook_title test-table fix) made my delivery-#1 test-table patches unnecessary; they were not carried forward.

---

## 5. Frontend merge (UI overhaul round 2)

- Merged `integrate/ui-overhaul` @ `a2722732` on top of A+B13 (`0853deec`). Only **CHANGELOG.md** conflicted (both sides added 2026-11-18 entries) — resolved by keeping **all** entries; home-app.tsx and app-shell.tsx auto-merged and were then **verified semantically**: single state declarations, one LayoutPicker instance, B's payload intact (`hook_title_enabled` always sent; `layout_template` + trimmed credit for any niche), composer hero present, no leftover Framing ToggleGroup or gaming-only row; app-shell keeps the overhaul's two-tier top bar and splices the **Live** item at position 2 only when `/api/live/status` reports enabled (B-0003's rule). One stale payload comment and one stale CHANGELOG line (the old gaming-only picker description) were corrected to the merged behaviour. The `/live` page survives and is reachable (build route present, §2).
- **ResizeObserver:** the merged page renders B's "Title on clips" Radix Switch, whose primitive constructs a ResizeObserver that jsdom lacks — every home-app test died at render (`ReferenceError`). The overhaul tree alone never rendered a Switch there, which is why it never hit this. Fix: a no-op ResizeObserver stub in `frontend/src/test/setup.ts` (standard jsdom shim; layout measurement is not asserted anywhere). All home-app suites then run.
- **Retargets (minimal, faithful to `layout-picker.tsx`):** accessible names are the picker's aria-labels — "Auto · Picks per clip", "Face on top · Red divider", "Face on top · Gold divider", "Full screen · Whole picture", "Crop · Zoom on the face".
  - `home-app-framing.test.tsx`: pure label renames (Fit/Crop → Full screen/Crop picker labels); every assertion kept. The steer's open uncertainty — the "Plain — just cuts" template ToggleGroup item exposing role="radio" — **confirmed working** (test passes as written).
  - `home-app-gaming-layout.test.tsx`: test 1 now asserts the Layout picker renders for **any** niche; tests 2–3 use the new radio names (credit field query unchanged — B kept `aria-label="Credit, e.g. @iShowSpeed"`); test 5 replaced with "picking a face layout after Full screen resets framing to Auto" (asserts `framing_mode: "auto"` + `layout_template: "facecam_red"`). No assertion was deleted to make anything pass.
  - `home-app-template.test.tsx` / `home-app-broll.test.tsx`: untouched, per instruction (both pass).
- **Timeout flakes:** three render-heavy tests straddle the 5 s default on this sandbox (music ×2 — timed out at 5.4–6.0 s in full runs, pass in small runs; brief ×1 at 5.18 s). They received the repo's own precedent timeout (30 s, as already used throughout `home-app-gaming-layout.test.tsx`); assertions untouched; each file re-verified green. The PGlite integration suites (10 s hook) and the social-publishing parity test (34.1 s vs its own 30 s limit under load; 4/4 solo in 20 s) are the same phenomenon at suite level — **all adjudicated by solo passes**, listed in §2/§3.
- **Overlap note (coordination):** Claude has an Antigravity agent ("Job F") also updating the Create-page tests to the Layout picker against his tree (no UI overhaul), plus picker/Title-switch/Live-page tests, arriving in a future series. My retarget here is deliberately minimal and strictly faithful to `layout-picker.tsx` so that reconciliation is a diff, not a rewrite. Expect his Job F versions of `home-app-framing.test.tsx` / `home-app-gaming-layout.test.tsx` to supersede or merge with these.

---

## 6. Final tip

`3900c3da` on `integrate/v121-final` — 50 commits over V12 `49cfad22`:
V12 → A×18 → B×13 → LiteLLM timeout → caption retargets → floor fix → SQL coverage → font-eval re-base → UI-overhaul merge → ResizeObserver stub → picker retargets → changelog category fix → music timeouts → brief timeout.
Working tree clean except untracked render-test sidecars (`backend/.drawtext-*/`) and the local `frontend/node_modules` + `src/generated/prisma` (build outputs, gitignored).

---

## 7. Installer notes for the packaging lane (do NOT skip)

1. **live-worker service:** B-0002 already adds it to the repo `docker-compose.yml` (container `supoclip-live-worker`, command `src.workers.live_worker`, `LIVE_CLIPPING`/`LIVE_MAX_SESSIONS` passthroughs defaulting **off**). The installer must ship it and **verify.ps1 must count `supoclip-live-worker` as running**.
2. **compose.override.yml block for Luffy's install** (install-only, mirrors backend/worker): `live-worker` gets `GROQ_API_KEY` **and `OPENAI_API_KEY` / `OPENAI_BASE_URL`** (the LLM gateway — added by delivery #2 §9) plus the GPU block (`NVIDIA_VISIBLE_DEVICES=all`, `NVIDIA_DRIVER_CAPABILITIES=compute,video,utility`, nvidia device reservation count 1).
3. **`LIVE_CLIPPING=on` for Luffy's install** (his installed compose defaults `${LIVE_CLIPPING:-on}` and live-worker reuses `image: jarvisclipstudio-worker:latest`); the **repo default stays off**.
4. **Verify expectation math:** migrations **55** (was 54 in the original brief; +1 = `20261119_0001_live_session_progress`). New feature files to expect in the package: `backend/src/live/` (incl. `vod.py`), `backend/src/api/routes/live.py`, `backend/src/repositories/live_repository.py`, `backend/src/workers/live_worker.py`, `backend/src/media/profanity.py`, `backend/src/media/audio_finish.py`, `frontend/src/app/live/`, `frontend/src/app/api/live/`, `frontend/src/components/layout-picker.tsx`. Static pages **76** (was 75; +/live).
5. **Assets:** the package ships the base bundle (7 music + 6 SFX + the SFX library files tracked in the repo). `assets/ASSET_MANIFEST.json` already covers every shipped audio file with a licence and a matching SHA — the licence gate prints ship-clean over the ship set (§1). The extended library (123 music / 310 SFX on Luffy's current install) is **not** in the package; it stays a post-install fetch per Q14(b). Do not let the installer wipe the installed extended library — the install replaces the app tree, so packaging must flag that the extended assets on Luffy's machine need preserving or re-fetching.
6. **LiteLLM smoke check:** per Claude's §2 note and the V12 install experience, the installer's LiteLLM smoke check should **retry for ~60 s** before failing — the V12 installer's exit 1 was this known boot-timing false alarm (gateway re-checked 200×3). Never roll back for it.
7. **THEBOLDFONT.ttf stays OUT** (dropped in A-0013; V12 shipped it with an unverified licence — V12.1 must not re-include it). Montserrat-Black is the alias target.
8. `CLIP_MIN_SECONDS` default is now **30** (owner rule, B-0012) — release notes and any verify script that assumed 17 must be updated.

---

## 8. Verdict

Backend: **PASS** on the exact gate-4 tip (0 failed, coverage-reconciled), DB suites PASS, Postgres tier PASS, licence gate ship-clean.
Frontend: **PASS** with the documented sandbox-timeout adjudications (zero assertion failures anywhere; every red mark in every full run was a timeout that passes solo).
The branch is ready for Sanji's review and the packaging lane. It has **not** been packaged and **not** merged into `next-milestone`.
