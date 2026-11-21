# V12.1 PACKAGE RECORD — SnipNova Consolidated V12.1 (2026-11-19)

**Package:** `JarvisClipStudio-SnipNova-Consolidated-V12.1-20261119.zip`
**Source tree:** `jcs-next`, branch `next-milestone` @ `3900c3da`
(`3900c3dab9b9522eb30840ce4fa028dc2532121b`) — git-archived from
that commit; the build asserts the full hash. (The ZIP's own
SHA-256 / size / file count live in the delivery note that
accompanies the package — a file inside the ZIP cannot carry
the ZIP's own hash.)

## 1. What the V12.1 build is

The V12 tree (`49cfad22`) plus **50 commits** — Claude's
combined series on the shipped V12 base, Sanji's fixes found
by the integration gate, and the approved UI overhaul —
merged in this order:

1. **Series A (18 patches, Claude, on the V12 tree):** the
   licence-gated asset library (`assets/ASSET_MANIFEST.json`,
   391 entries) + Openverse B-roll source; the quality steps
   — Step 1 content-aware cuts (`EDL_CONTENT_CUTS`), Step 2
   fit/pad + 4K probe, Step 5 caption loop filter + phrase
   split, Step 4 story-event selection, Step 3
   screen-as-subject (with the ≥25% guard and the split-shot
   screen guard) — **all quality switches default OFF**,
   pending Luffy's render approvals; the transition library
   (248 entries) + burned-in caption-band detector +
   catalogue API; the OFL/Apache caption font pack
   (34 fonts) with **THEBOLDFONT dropped** (licence
   unverifiable; Montserrat-Black is the alias target); the
   live-clipping MVP tools. NVENC is **not** in the series —
   it stays exactly as shipped in V12 (verified: 0 added
   NVENC lines in the whole merge).
2. **Series B (13 patches, Claude, on A):** the
   gaming-layout delegation fix (a layout template + a found
   facecam now wins over Fit/Crop framing); **live clipping
   in the app** — sessions API, the `live-worker` container,
   the `/live` page + proxy + nav entry, yt-dlp venv fix,
   fast speech model, Groq hosted-Whisper captions;
   the owner fixes (white seam captions, hook-title on/off
   via `tasks.hook_title_enabled`, **one Layout picker for
   every niche** replacing the Framing toggle + gaming-only
   row); fast VOD clipping of finished 10–20 h streams
   (`live/vod.py`); live perf fixes; **profanity masking**
   in burned-in captions + hook titles
   (`CAPTION_PROFANITY_FILTER` default ON) and the owner
   rule **`CLIP_MIN_SECONDS` default 30**; and
   `media/audio_finish.py` — face-on-top (plain-path) clips
   now receive their music bed + SFX (they silently had
   none). Three new migrations — **55 total**
   (`20261118_0001_live_clipping`,
   `20261118_0002_task_hook_title_enabled`,
   `20261119_0001_live_session_progress`).
3. **Sanji's commits on top:** the LiteLLM gateway timeout
   (`timeout: 90` + `stream_timeout: 90` — a task had hung
   20+ minutes on one unreturned NIM call); caption-suite
   retargets for the font/floor changes; **a real product
   fix — the 30 s clip floor no longer rewrites a cold-open
   hook** (the floor's backward extension of the first range
   was undoing the transplanted hook sentence); SQL
   execution coverage for the live repository's 9 raw
   statements (the exact hole that broke task creation on
   2026-11-11) with the inventory guard re-pinned to the
   true counts; the stage-2 font eval re-based on the
   34-font bundle (measured rates identical to the original
   calibration — no pin lowered); the **UI overhaul round 2
   merge** (composer-hero create page, bento tiles, liquid
   glass, vivid Galaxy) reconciled with the Layout picker —
   one picker instance, B's payload intact, the Live nav
   item spliced into the top bar; the jsdom ResizeObserver
   stub the merged page's Switch primitive requires; the
   create-page test retargets to the picker; a changelog
   category fix.
4. **Carry-over proof:** all 26 backend files of the
   step/asset patches are byte-identical to Sanji's earlier
   independently-integrated stack (`f1f49b03`) — zero
   semantic divergence between the two integration paths.
   Every change Claude listed in `INSTALLED_APP_CHANGES.md`
   for Luffy's installed app is present in this tree, so
   installing V12.1 does **not** wipe the live clipping he
   built directly into the installed app.

## 2. Gates on the exact packaged lineage

- **Backend full gate** (fresh, chunked, on tip `f48b2355`
  — the last backend-touching commit; the 10 files changed
  after it up to `3900c3da` are frontend/test files only,
  0 backend): **5,634 passed / 0 failed / 262 skipped /
  9 xfailed / 1 error**. The 1 error is `test_live_api`
  setup without a database (OSError) — the certified no-DB
  pattern, adjudicated by the DB-backed run on the same
  tip: step-1 DB suites + live + VOD = **108 passed,
  1 skipped** (incl. `test_task_insert_columns`,
  `test_task_settings_readback`,
  `test_settings_integrity_matrix`, `test_framing_mode`,
  `test_migration_files_safety` at 55 migrations; live API
  13, live worker 4, Groq 5, VOD 6). Postgres execution
  tier: **10 passed**. Coverage reconciled exactly:
  5,906 collected = chunk outcomes + the skip-only
  `tests/live` files. The gate found and this branch fixed
  three real defects (the cold-open floor bug, the
  uncovered live SQL, the stale font eval — §1.3); the
  earlier gate's 4 OCR failures were the sandbox's wiped
  `tesseract` binary (environmental; solo re-run 22 passed;
  clean in the final gate).
- **Frontend gate** (final tip `3900c3da`): full vitest
  serial run over the 144 non-integration files: **930
  tests passed / 0 failed**; the 11 integration files run
  separately bring the total to **959 tests, 0 assertion
  failures** (every red mark in any full run was a sandbox
  CPU timeout that passes solo — the same adjudication as
  every prior gate on this machine). `changelog.test.ts`
  PASS; **tsc exit 0**; **next build exit 0, 76/76 static
  pages** (+1 vs V12: the `/live` page).
- **Licence gate:** ship-clean over the whole ship set —
  `assets/ASSET_MANIFEST.json` (391 entries) covers every
  shipped audio file (7 music + 22 SFX = 29) with a licence
  and a matching SHA-256; music is CC0-1.0 (FreePD,
  per-track provenance in `docs/assets/MUSIC_PROVENANCE.md`),
  SFX are original synthesised files. No licence was
  invented. `THEBOLDFONT.ttf` is absent from the tree.
- **Smoke acceptance** (Full AutoClip + Plain, real service
  path, this exact tree): see
  `reports/REAL_EXPORT_ACCEPTANCE_V121_SMOKE.md` — the
  verdict is stated there literally and is **not** upgraded
  here.

## 3. The verify list — why 218

V12's derivation: 19 base checks + 52 migration files + 52
applied-migration checks + 76 feature files + 7 containers
inside the base sum = **199**. For V12.1 the list GREW,
recomputed from the finished script
(`recompute_verify_v121.py` against the assembled tree):
**+3 migrations** (`20261118_0001_live_clipping.sql`,
`20261118_0002_task_hook_title_enabled.sql`,
`20261119_0001_live_session_progress.sql` — counted twice,
file + applied: +6), **+12 feature files** (the live module
`live/vod.py`, the live API route, the live repository, the
live worker, `profanity.py`, `audio_finish.py`,
`asset_library.py`, `assets/ASSET_MANIFEST.json`, the
transition library, the font-pack manifest, the `/live`
page, the Layout picker) and **+1 container**
(`live-worker`). **199 + 6 + 12 + 1 = 218.** No check was
removed or weakened. The install instructions carry 218.

## 4. Installer changes in this package

- The stack now includes the **`live-worker`** service
  (already in the repo `docker-compose.yml`); verify.ps1
  counts `supoclip-live-worker` as a running container.
- Step 6 ensures the **`live-worker` block in
  `compose.override.yml`**: created with the GPU reservation
  if absent, or completed in place — its environment list
  carries `GROQ_API_KEY`, `OPENAI_API_KEY` and
  `OPENAI_BASE_URL` (the LLM gateway the live worker picks
  moments through). Any edit it cannot make safely is
  skipped with printed manual lines; the install continues.
- Step 6 sets **`LIVE_CLIPPING=on`** in `.env` for this
  install (the repo default stays off).
- Step 8's **LiteLLM smoke check retries for ~60 s** before
  it can fail — the V12 installer's exit 1 was this
  boot-timing false alarm (gateway re-checked 200×3 by
  hand). It is still never a rollback reason on its own;
  verify.ps1 is the judge.
- **Your extended asset library is preserved:** step 6
  copies your installed `assets\music` and `assets\sfx`
  contents back over the new tree (the same merge-copy as
  every prior release), so the 123 music / 310 SFX files on
  your current install survive the upgrade. The package
  itself ships the base bundle (7 music + 22 SFX).

## 5. Deliberately NOT in this package

- The **extended asset library** (123 music / 310 SFX on
  Luffy's current install; ~221 MB as a bundle) — a
  post-install, checksum-verified fetch per the Q14(b)
  decision; never inside the installer. On upgrade it is
  preserved in place (§4), so no re-fetch is needed.
- **Antigravity's "Job F" frontend tests** (picker / Title
  switch / Live page tests written against Claude's tree)
  — they arrive in a future series and will be reconciled
  then; the shipped create-page tests are Sanji's
  retargets, faithful to `layout-picker.tsx`.
- The **quality-step switches stay OFF**
  (`EDL_CONTENT_CUTS`, `SELECTION_EVENTS`,
  `SCREEN_SUBJECT`, caption cleanup, fit/pad probe):
  Step 1–5 render approvals are still Luffy's to give; the
  code ships dormant exactly as gated.

## 6. Known limitations (carried honestly)

- Phase 1 B-roll density on finance speech is PARTIAL
  (1.67 inserts/min on the TJR windows vs the 3/min bar —
  cue supply, all cues on-topic).
- The reaction picker has a measured borderline miss class
  (quiet reactions at ~1 z face signal — the fixture's R2;
  the picker is deliberately not tuned to force it).
- The real-clip layout-gate comparison (Claude's 7/8 vs
  0/8) is NOT RUN on this tree — his-side evidence only.
- The live B-roll resolver and SigLIP veto legs are NOT
  RUN on Sanji's tree (no provider keys / model cache in
  the sandbox).
- Live clipping's **Groq captions and GPU encode are not
  yet proven on a real stream** (Claude's disclosure: both
  were deployed after his real-stream test, which produced
  one finished 30.1 s clip via the local path).
- Several music beds sit quiet under constant speech
  (compressor-ducking limitation; only meme/comedy is
  tuned clearly audible).
- Green-screen / cut-out facecams (no rectangle edge) can
  be missed by the facecam-rect detector.
- After download, the working file is normalised into a
  1920×1080 box (portrait 1080×1920 passes unchanged; 4K
  landscape sources are capped there).
- The DeepSeek route remains dormant (zero credit); the
  ladder fails over to Groq as designed. Top-up is the
  user's decision only.
- Open owner question: whether the 30 s clip floor should
  apply to editor caption re-renders (the floor itself is
  pinned by tests; the re-render path currently pins it
  off in its roundtrip test).
