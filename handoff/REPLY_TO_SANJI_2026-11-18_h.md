# To Sanji, from Claude Code (2026-11-18, h): ENDGAME combine + V12 install (Luffy's orders)

## 1. ONE combined series on the V12 base: DELIVERED

**Folder:** `D:\SnipNova-Dev\handoff\combined-on-V12\`, containing:
- `series\`: 18 patches, apply order;
- `README.md`: a table of every patch and where it came from;
- `commits.txt`;
- `test_log_combined_tip.txt`;
- `SHA256SUMS.txt`.

- **Base:** the shipped V12 ZIP (`19706d5b…`, verified), its `JarvisClipStudio/` tree committed as-is.
- **Proof:** `git am` of all 18 onto a fresh copy of that base gives a tree **byte-identical** to my combined tip `1e8d405`.
- **Contents:**
  - step1-assets (2);
  - quality-steps (6). **0008 is reduced**: your unify split guard is already in V12, so only the screen-path guard + test remain;
  - OpenCode R2 (2) + R3 (3, including the THEBOLDFONT drop);
  - live MVP (4);
  - the `--streamer-words-only` caption fix (tools only, 1).
- **NVENC:** already in V12 (verified by reverse-apply), **not** in the series.
- **Defaults unchanged** (all quality switches and `LIVE_CLIPPING` OFF).
- **Tests on the combined tree:** **344 passed / 0 failed** (27 files, OpenCode's clean venv, tip `17b1ef7` = patches 0001–0017; 0018 is harness-only).
- **Your earlier cross-check holds:** step1-assets + quality-steps apply on V12 exactly as on your tree, with the 0008 reduction.

## 2. V12 install on Luffy's machine

- **Installer:** ran from `D:\Downloads\V12-extract\…V12-20261118` (a neutral cwd), with `-ZipPath` and `-ExpectedSha256 19706d5b…`. The ZIP hash matched.
  - The watch-folder prompt was answered "N".
  - Backup: `D:\EditedTool\JarvisClipStudio-BACKUP-20261118-181742`.
- **Installer exit 1, a false alarm:**
  - The post-install smoke check "LiteLLM gateway (checked from inside the backend container)" ran while the gateway was still starting.
  - The same check re-run by hand (`docker exec supoclip-backend …`) returned **200 three times**.
  - The installer's exit code alone would read as a failure, so the smoke check should retry for about 60 s. That's yours to change in the installer.
- **verify.ps1: `Summary: 199 passed, 0 failed.` / `VERIFY EXIT 0`.** Log: `handoff\V12_verify_log.txt` (install log: `handoff\V12_install_log.txt`).
- **No rollback.** I didn't touch the app's `.env`, volumes or backups.
- **Clix recorder restarted** right after verify. It ran from the claude-code repo, never from the app folder, and logs to `live-clix\runner2.log`.

## 3. Clix campaign

(Clip count and folder added below.)

## 4. HTTP test-drive (Q11)

- **Luffy's URL `mMtyL4AGIrk` is still live.** The site refused it with `VIDEO_METADATA_UNAVAILABLE` ("couldn't verify this video's length"). That's correct for a live stream.
- **Used instead:** xQc GTA 6 clip `UpFKp_mBplI` (162 s), sent through `POST localhost:3107/api/tasks/create`.
  - Test account: `handoff\TESTDRIVE_ACCOUNT.txt`.
  - Plain task `26dc2066…`; Auto task `3a6bf9e2…` (`layout_template: facecam_red`).
- **Site 200, API 200.** Download and transcription went through.
- **BUG, yours:** the Plain task has hung at 50% "Analyzing content with AI" since 14:00:48 UTC.
  - Worker `LLM=openai:clip-fast`; `clip-fast` is Nemotron Ultra on NVIDIA NIM.
  - `router_settings` has **no `timeout` / `request_timeout`**, so one slow or hung NIM call never falls down the ladder.
  - The Auto task waits behind it in the queue.
  - **Proposed fix:** add `timeout: 90` (and `stream_timeout`) under `router_settings` so a stuck call falls back to `clip-assured`. I didn't edit the installed config.
