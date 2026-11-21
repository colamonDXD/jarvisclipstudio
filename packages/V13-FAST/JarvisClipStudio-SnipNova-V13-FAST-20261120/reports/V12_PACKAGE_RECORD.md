# V12 PACKAGE RECORD — SnipNova Consolidated V12 (2026-11-18)

**Package:** `JarvisClipStudio-SnipNova-Consolidated-V12-20261118.zip`
**Source tree:** `jcs-next`, branch `next-milestone` @ `49cfad22`
(`49cfad2284c9a2bdb6fec9c7a4d836cac377c57a`) — git-archived from
that commit; the build asserts the full hash. (The ZIP's own
SHA-256 / size / file count live in the delivery note that
accompanies the package — a file inside the ZIP cannot carry
the ZIP's own hash.)

## 1. What the V12 build is

The V11 tree (`5480a9da`) plus, merged in this order on
2026-11-18 (136 files changed vs the V11 tip, +10,462/−336):

1. **The integration stack** (`integrate/phase2-auto-editor`
   @ `bcb553de`, merge `c55ee990`): Lane A reaction
   intelligence (reaction/gameplay moment fusion, contract
   fields, hooks, edit recommendations), Phase 1 Smart
   B-Roll v3, the Phase 2 auto-editor (22 patches: facecam-rect
   layouts, red/gold gaming templates + the layout-template
   migration, clip QC, one-layout-per-clip, CPU transcription
   fallback), the OpenCode set (NVENC probe fix, 16:9/1:1
   export profiles, transition proof reel + addendum), the
   Studio Console gaming-layout picker port, and the Galaxy
   canvas starfield background. The stack's own evidence:
   streamer fixture 6/7 (reaction #1 fused 55.0, decoy
   excluded, R2 the measured borderline miss), gate suites
   93/0, full backend gate on the stack with all failures
   attributed and re-run green — full record in
   `reports/PHASE2_INTEGRATION_REPORT.md`.
2. **Maximum download quality** (`feat/max-download-quality`
   @ `62080b8a`, merge `32a519fe`): `YOUTUBE_MAX_HEIGHT`
   default 1080 → 2160 and the `capped_height` ceiling
   raised to match — link imports fetch the best quality
   the source offers (545 tests passed on its branch).
   CHANGELOG conflict resolved keep-both-sides.
3. **Top-navigation shell** (`feat/topnav-shell` @
   `881b2578`, merge `0680f2f6`): the two-tier top bar
   replaces the icon rail (app-shell.tsx + feedback-button
   only; vitest effectively 940/0, tsc 0, build 75/75 on its
   branch). CHANGELOG conflict resolved keep-both-sides.
4. **The bundled music beds** (packaging-added): the 7
   checksum-verified FreePD CC0 MP3s (calm, comedy,
   corporate, energetic, funk, phonk, upbeat) copied into
   `assets/music` at assembly — the repo gitignores music
   binaries by design, so they are not in the git archive;
   every file's SHA-256 was asserted against Claude's
   `SHA256SUMS.txt` during assembly and again against the
   built ZIP. The 6 synthesised SFX WAVs are tracked in git
   (Phase 2 patch 0016) and arrive via the archive; their
   checksums were verified the same way.
5. **One test-isolation fix** (`02ddf98f`): the EDL wiring
   test now points `edl_render.ASSETS_DIR` at an empty tmp
   dir — with the library populated it otherwise fails on
   intended behaviour (the Auto bed applying). Assertion
   unchanged; verified passing with the library populated
   (14/14 file) and empty (1/1). The ONLY test edit in the
   build, sanctioned in advance (flagged by Claude,
   accepted by Sanji).
6. **One product bug fix** (`49cfad22`, found by the V12
   gate): with the music library installed, the Auto bed
   re-added a track at 0.10 over a profile whose music the
   learning engine had deliberately zeroed for its niche —
   the learning loop could never silence a niche's music.
   A learned music-off now wins on the Auto path; explicit
   user music choices are unchanged. The failing test
   passes with the library populated, and the learning +
   music + wiring suites pass 100/100 on the final tip.

## 2. Gates on the exact packaged tip (`49cfad22`)

- **Backend full suite** (chunked, on the merged tree
  `02ddf98f`, music library populated): **5,232 passed /
  6 failed / 243 skipped / 7 xfailed**. All 6 failures
  attributed and closed: 4× tesseract binary wiped by a
  sandbox runtime reset (re-run **4/4 PASS** with tesseract
  5.3.4 restored — this includes the 3
  `test_caption_face_guard_render` tests); 1× gateway
  route-scan vs the gate's own set-aside location (re-run
  PASS under the certified condition); 1× the learned
  music-off bug (fixed in product code at `49cfad22`).
  On the final tip: the affected chunk re-run **478/478**,
  learning + music + wiring suites **100/100**. Full
  record: `reports/BACKEND_GATE_V12.md`.
- **Frontend gate**: vitest effectively **959 passed /
  0 failed / 12 skipped** (raw 944/3/12 — every failure a
  sandbox-CPU timeout, all passing on solo re-runs);
  tsc **0 errors**; `next build` **exit 0**; changelog
  **4/4** (re-run on the final tip). Full record:
  `reports/FRONTEND_GATE_V12.md`.
- **Smoke acceptance** (Full AutoClip + Plain, real
  service path, this exact tree): **NOT RUN in the sandbox** — the run was driven through the real service path on the exact tree (schema init, task creation, processing entry all clean, no product errors), but the render stage was never reached: this tree's pipeline runs four full-source analysis passes over the 68-minute AV1 fixture and the sandbox harness budget (7,200 s, three attempts incl. one killed by a runtime restart) ran out first. No exports exist for this tree; the V11 smoke (same fixture + procedure) passed on the V11 tree, whose pipeline did not include the added passes. This is recorded as NOT RUN, not as a pass.
  Full record: `reports/REAL_EXPORT_ACCEPTANCE_V12_SMOKE.md`.
- **Migrations on the merged tree: 52** (51 + the
  layout-template migration).

## 3. The verify list — why 199

V11's derivation: 19 base checks + 51 migration files + 51
applied-migration checks + 55 feature files = **176**. For
V12 the list GREW, by design and recomputed from the finished
script (`recompute_verify_v12.py` against the assembled
tree): **+1 migration** (`20261117_0001_task_layout_template.sql`
— counted twice, file + applied: +2) and **+21 feature
files** (8 stack code files: reaction_moments,
gameplay_moments, reaction_hooks, edit_recommendations,
facecam_rect, gaming_templates, the create-page picker
file, the Galaxy AppBackground; plus the 13 bundled asset
files — 7 music beds + 6 SFX — whose absence would silently
disable the music/SFX features). **19 + 52 + 52 + 76 =
199.** No check was removed or weakened. The install
instructions carry 199.

## 4. Deliberately NOT in this package

- Claude's **step1-assets series** (asset-library code,
  `ASSET_MANIFEST.json`, the meme pack, Step 1
  content-aware cuts behind `EDL_CONTENT_CUTS`) — the
  patches had not been received when this package was
  built; they land in **V12.1**. Step 1 stays dormant
  until Luffy approves its before/after render.
- The **extended asset library** (221 MB) — a post-install,
  one-command, checksum-verified fetch, per the Q14
  decision; never inside the installer.
- **OpenCode Round 2/3 libraries** (transition catalogue,
  caption detector, font pack) — library-only, not wired
  into rendering; not in V12.

## 5. Known limitations (carried honestly)

- Phase 1 B-roll density on finance speech is PARTIAL
  (1.67 inserts/min on the TJR windows vs the 3/min bar —
  cue supply, all cues on-topic).
- The reaction picker has a measured borderline miss class
  (quiet reactions at ~1 z face signal — the fixture's R2;
  the picker is deliberately not tuned to force it).
- The real-clip layout-gate comparison (Claude's 7/8 vs
  0/8) is NOT RUN on this tree — his-side evidence only.
- The live B-roll resolver and SigLIP veto legs are NOT
  RUN on this tree (no provider keys / model cache in the
  sandbox).
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
