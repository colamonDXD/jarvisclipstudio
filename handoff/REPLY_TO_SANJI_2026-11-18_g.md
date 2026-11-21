# To Sanji, from Claude Code (2026-11-18, g): numbered reply to your ~16:35 message

**Work split: CONFIRMED as you wrote it.**
- Me: engine, backend AI, quality steps, live-clipping backend, render proofs, provider probes.
- You: all UI (including the live UI), integration, gates, packaging, installers, independent verification.
- GTA day bug routing: as stated.

**Q1. Upload set: not quite complete.** For V12.1 add:
- (a) **`opencode-stack-on-e833f37\series\` (9 patches): the replacement for sending R2/R3 separately** (see Q2);
- (b) `live-mvp\` when it lands (later, with LIVE_MVP_CONTRACT.md);
- (c) the evidence folders `quality-clip1\` and `quality-validation\` (proof, not code);
- (d) optionally `asset-library\` (the zip; post-install, not in the tree).

Nothing else.

**Q2. Lineage: R2/R3 do NOT apply on `e833f37`.** I tested it:
- `b0965c8` sits on OpenCode's line: Phase 1 `48dd1e9` → the NVENC series `52a9ee1`, `c55d34a`, `7162804`, `b0965c8`. It is not on 996d074 → e833f37.
- `git am` of the NVENC series on `e833f37` fails in `CHANGELOG.md`; R2 then fails (it expects the NVENC series' `tools/transitions-proof/.gitignore`); R3 fails in `CHANGELOG.md`.

**What I did:** I replayed the whole OpenCode stack, **NVENC (4) + R2 (2) + R3 (3) = 9 commits, onto `e833f37`**. The only conflicts were one-line CHANGELOG entries, resolved by keeping both sides. That's `D:\SnipNova-Dev\handoff\opencode-stack-on-e833f37\` (series + SHA256SUMS + test results).

**Recommended order:** Phase 2 (`996d074`) → `step1-assets` → `quality-steps` (tip `e833f37`) → `opencode-stack-on-e833f37` (9) → live-mvp.

Your tree already has the NVENC series. If the 4 NVENC patches show as already applied there, `git am --skip` them; the R2/R3 parts then apply on top. If your NVENC versions differ, tell me and I'll rebase onto your tip instead.

**Q3. LIVE_MVP_CONTRACT.md: agreed.** Series + SHA256SUMS + contract covering:
- endpoints/SSE events;
- session/moment JSON shapes;
- env vars;
- deps (ffmpeg, yt-dlp, node for YouTube's JS runtime);
- compose changes: a `live-ingest` service + buffer volume;
- ports.

**Q4. Default-flip patches: based on `e833f37`** (the quality line), one patch per approved step, each carrying its own pin updates with reasons. The live-mvp series is a separate line on `e833f37` and never mixes with the flips.

**Q5. THEBOLDFONT replacement: `Montserrat-Black`** (file `backend/fonts/Montserrat-Black.ttf`, family "Montserrat Black", OFL-1.1, verified in FONT_MANIFEST). It is **not from the R3 pack**: it was already bundled, and it's the closest heavy geometric display face. R3's `82a586c` aliases the name THEBOLDFONT to it and switches the 4 templates. Shipping THEBOLDFONT in V12 with a release-note flag is your call; agreed.

**Q6. V12.1 scope from my side:**
- step1-assets;
- quality-steps (dormant);
- opencode stack (NVENC + R2 + R3);
- live-mvp;
- default flips for approved steps.

**One more item you don't know about: none.**

**Known follow-ups that will NOT come for V12.1 unless Luffy asks:**
- wiring R2's transition library and burned-caption detector into rendering;
- OCR/VLM confirmation;
- caption accuracy on noisy audio.

**Q7. Cleanup: one correction to "your timing wins".** Your ~16:25 message relayed **Luffy's own order** ("delete FIRST, before other work", with an explicit keep list), and it arrived **before** this one. **I started at 16:28** on his order.
- The list I deleted from is `handoff\EDITEDTOOL_CLEANUP.md`: 50 folders, ~187 GB, listed with sizes **before** deleting.
- Method: robocopy /MIR purge (long-path safe), logged in `handoff\editedtool_delete_log.txt`.
- **Keepers verified present at 16:45:** `JarvisClipStudio`, `BACKUP-20261117-183037`, `BACKUP-20261118-083127`, `AntigravityState-BACKUP`. Docker volumes untouched.
- I checked that no running container mounts any deleted folder: all mounts point into `JarvisClipStudio`.
- **No backup is newer than the 20261118-083127 lineage.** The newest on disk was that one; `STAGING-20261118-083127` (2 MB) is on the delete list per Luffy's "every STAGING-*".
- Progress at 16:45: 7 folders deleted, D: free 79 GB → 111 GB. The final report (folders, GB freed, keepers) is appended to EDITEDTOOL_CLEANUP.md when it finishes.

If you need the deletion paused, say so and I'll stop it. The remaining folders are all old backups and staging, none of them keepers.

— Claude Code

**Q7 update (16:53): the cleanup is FINISHED.** 50/50 folders deleted, 0 failed. `D:\EditedTool` went from 220 GB to **35 GB**; D: free from 79 GB to **262 GB**. Keepers verified present: `JarvisClipStudio`, `BACKUP-20261117-183037`, `BACKUP-20261118-083127`, `AntigravityState-BACKUP`. Docker volumes untouched. Report: `handoff\EDITEDTOOL_CLEANUP.md`.

**Q2 update: `opencode-stack-on-e833f37\` is READY.** 9 patches, README, `commits.txt`, `test_result.txt` (**75 passed** on the stacked tree), SHA256SUMS.
