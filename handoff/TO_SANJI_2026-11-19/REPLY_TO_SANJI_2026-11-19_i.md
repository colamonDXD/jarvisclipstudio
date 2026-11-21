# To Sanji, from Claude Code (2026-11-19, i): everything you're waiting for, in one folder

This supersedes `REPLY_TO_SANJI_2026-11-18_h.md`. Everything you need for V12.1 is in this folder (`D:\SnipNova-Dev\handoff\TO_SANJI_2026-11-19\`):

| Path | What |
|---|---|
| `A-combined-on-V12\series\` | **Series A**, 18 patches on the shipped V12 tree (unchanged since `_h`, README inside) |
| `B-live-and-fixes\series\` | **Series B**, the patches after A, applied on top of A's tip `1e8d405` |
| `INSTALLED_APP_CHANGES.md` | Exactly what I changed **inside Luffy's installed app** (Luffy's order: "u only build it add to website now"). V12.1 must carry all of it, or the next install wipes it |
| `SHA256SUMS.txt` | Hashes of every file here |

**Apply order:** V12 tree → `git am A-combined-on-V12/series/*.patch` → `git am B-live-and-fixes/series/*.patch`.

My tip after both series: `078c732` (13 patches in series B, listed in `B-live-and-fixes/commits.txt`).

## 1. Series A: combined on V12 (unchanged)

- 18 patches, byte-identical to my tip `1e8d405`.
- Tests: 344 passed / 0 failed.
- NVENC is already in V12, so it's not in the series.
- Defaults are unchanged.

Details are in `A-combined-on-V12/README.md`.

## 2. V12 install on Luffy's machine: done, verify 199/0

- **Installer exit 1 was a false alarm.** The LiteLLM smoke check ran during gateway startup. Re-checked by hand: 200 ×3. **Yours:** retry that check for about 60 s.
- **verify.ps1:** 199 passed, 0 failed.
- **Logs:** `handoff\V12_install_log.txt`, `handoff\V12_verify_log.txt`.
- **No rollback.**

## 3. Clix campaign: cancelled by Luffy

Luffy: "nah i don't want clix, delete that". The recorder is stopped and all Clix data deleted (about 11.5 GB). The Clix tools stay in the repo (they work for any streamer).

## 4. HTTP test-drive: two real bugs found

**4a. LiteLLM has no request timeout (yours).**
- A task hung 20+ minutes at "Analyzing content with AI": one NIM call never returned, so the fallback ladder never ran.
- **Fix:** `timeout: 90` (plus `stream_timeout`) under `router_settings`.
- I didn't edit the installed config.

**4b. Gaming layout on Create tasks.**
- An Auto task with `layout_template: facecam_red` rendered as a plain crop or fit with the facecam floating over the game.
- **Cause:** the face-on-top stack is only composed on the plain path. The reframe planner sends a clip there only when framing is Auto and it finds the facecam itself. With framing **Fit** (Luffy's task), it never looked, and `build_vertical_filter_plan` also short-circuits Fit before the reaction layout.
- **My first fix (`1cdd8c6`, forcing the non-direct path) was wrong:** the EDL branch of `render_reframed_clip_ffmpeg` then rendered source-time segments on the trimmed intermediate. Clips came out **3–8 s instead of 14–21 s** (Luffy's task `c8dfde54`).
- **Reverted** on his install and in series B.
- **The real fix (series B, last commit):**
  - With a layout template chosen and a facecam found (`find_facecam_rect`), the planner delegates to the plain path whatever the framing is.
  - `build_vertical_filter_plan` drops the Fit/Crop override when a layout template is set.
  - Without a facecam, the framing choice stands.
  - **Proof (real render):** xQc source, clip starting at 60 s, framing **fit**, `facecam_red` → delegated, face on top / game below, **17.07 s** (the floor below did that).

## 5. Live clipping in the website (Luffy: "i want that live clipping in website… u only build it")

Built end to end and **live on Luffy's install now** (localhost:3107/live).

**Backend:**
- `POST/GET /live/sessions`, `GET /live/sessions/{id}`, `POST …/stop`, `DELETE`, `GET /live/clips/{id}/file`, `GET /live/status`.
- Migration `20261118_0001_live_clipping` adds `live_sessions` and `live_clips`.
- A new **`live-worker`** container (`python -m src.workers.live_worker`; not ARQ, since a session lasts hours).
- `LIVE_CLIPPING` (repo default **off**: every route 403s except `/live/status`) and `LIVE_MAX_SESSIONS` (default 1).

**How a clip is made:**
1. The voice pass flags a reaction.
2. A **hook-first window** is cut: it opens ≤ 6 s before the reaction and runs while the streamer talks, **22–30 s** (`LIVE_CLIP_MIN_SEC` / `LIVE_CLIP_MAX_SEC`).
3. The on-camera gate checks the facecam layouts: face visible and moving ≥ 1.8× its usual movement.
4. A quick preview is made.
5. The final clip renders through the normal engine with the face-on-top layout and **streamer-only captions** (game subtitle pixels removed).

**Captions:** sent to **Groq's hosted Whisper** (`whisper-large-v3-turbo`, Luffy's free key) when `GROQ_API_KEY` reaches the live worker. Otherwise faster-whisper `small` runs locally (the shared worker's openai-whisper medium took 8 min just to load). Any Groq failure falls back to local.

**Stop:** stops recording and detecting at once, and finishes the clips already found.

**Frontend (yours to restyle as you like):**
- `/live` page: URL, layout, credit, title (optional), REC status, a clip grid that polls every 5 s, a "Skipped moments (n)" list with reasons, a session list.
- `/api/live/[...path]` proxy (Range forwarded).
- A sidebar "Live" entry, shown only when `/live/status` says enabled.

**Proof:**
- **Simulated live** (a real file played at 1×): the preview was ready 63 s after the moment, the final clip 124 s later.
- **Real stream (IShowSpeed, YouTube live) on Luffy's install:**
  - It recorded and flagged moments; the gate passed real reactions (2.4–3.2× movement) and rejected game noise (1.3–1.7×).
  - Session 1 lost its clips: the worker was still on the slow speech model when Luffy needed his CPU back, and an old Stop discarded the queue (both fixed since).
  - Session 2 produced **one 30.1 s finished clip**.
- **Not yet proven on a real stream:** the Groq captions and the GPU encode. Both were deployed after the stream test.

**Tests:** 23 live tests plus 5 Groq tests.

## 6. Fixes Luffy asked for on 2026-11-18/19 (series B, last commit)

| Ask (Luffy) | Change |
|---|---|
| "WTF 3 SEC CLIP… clip should be 17 seconds minimum" | `CLIP_MIN_SECONDS` (default **17**) in `create_optimized_clip`: a shorter clip is lengthened forward (or earlier at the source end). Never past the source |
| "why tf captions r red" | `seam_red` is now white with a **black** outline; red is only the divider. The seam style only applies when the stack is actually used; otherwise the task's caption style stays. **Pin updated:** `test_gaming_templates` red `stroke_color` `#E5262B` → `#000000`, a deliberate owner change |
| "sometimes there is no need of titles, give option title on or off" | New nullable `tasks.hook_title_enabled` (NULL = on, as before), migration `20261118_0002`. API `hook_title_enabled` (bool). Off = no burned-in title; the clip keeps its name in the library |
| "make a clear option with photo reference" for the layout | Create page: one **Layout** picker with drawn example pictures (Auto, Face on top · red, Face on top · gold, Full screen, Crop). It replaces the Framing toggle and the Gaming-only layout row, and works for any niche. Credit field under the face-on-top cards. **Title on clips** switch |

## 7. Tests

**Backend: 246 passed, 0 failed** on the final tip (`7f5d0e8`, OpenCode's clean venv, Windows, fresh Postgres 15 test DB).
- **Settings and schema** (174 passed, 1 skipped): `test_task_insert_columns`, `test_task_settings_readback`, `test_settings_integrity_matrix`, `test_framing_mode`, `test_broll_toggle`, `test_billing_allowance`, `test_recipe_create_wiring`.
- **Render, layout and live** (72 passed): `test_pristine_schema_defaults`, `test_migration_files_safety`, `test_gaming_templates`, `test_clip_min_and_layout`, `test_reframe_consistent`, `test_speaker_turns_edl_skip`, `test_render_stage_timings`, `test_compose_flag_passthroughs`, live API/worker/moments, Groq.
- **Two of my own mistakes, both fixed and in series B:**
  - A semicolon in my migration comment broke your `;`-splitting runner.
  - Seven test files build their own `tasks` table (in SQLite) or pin the insert parameters. Patch 0008 adds `hook_title_enabled` there, exactly as `layout_credit` was added. Nothing weakened.
- **Real render proof** of the layout + 17 s floor: section 4b.

**Frontend: not run by me** (no node_modules here; the Docker production build passed, which type-checks). These vitest files test the controls I replaced and **need updating to the new picker**. They're yours, and I didn't touch or skip them:
- `home-app-framing.test.tsx`
- `home-app-gaming-layout.test.tsx`
- probably `home-app-template.test.tsx` and `home-app-broll.test.tsx` where they click the Framing toggle

## 8. Things only you can do

1. **Package V12.1 from V12 + A + B** and ship it with the installer.
2. In the installer:
   - add the `live-worker` service;
   - add its `compose.override.yml` block (GROQ key + GPU);
   - add `LIVE_CLIPPING=on` for Luffy's install (the repo default stays off).
3. Make verify.ps1 count `supoclip-live-worker` as running.
4. Add the LiteLLM timeout (4a).
5. Update the frontend tests (section 7).

## 9. NEW: fast clipping of finished 10-20 h streams (series B 0009-0011)

**Luffy:** "there is a 10 hours video... make something that can clip faster and clips better clips that can go viral".

**How it works:**
- A finished stream pasted on the Live page now runs `live/vod.py`. The API turns `was_live`/`not_live` into `settings.mode = "vod"`, `max_clips` 10; upcoming streams still get 422.
- **Steps:**
  1. Audio only.
  2. Parallel voice scan (30-min chunks, 4 processes), boosted by YouTube's "most replayed" `heatmap`.
  3. Groq transcripts of the best 3x candidates, 5 windows per request (the free tier is 20 requests/min).
  4. The LLM gateway picks the most viral moments with start/end/title, falling back to the signals.
  5. `--download-sections` (1080p, exact cuts) of only those pieces.
  6. `render_live_clip` (face on top, streamer-only captions, NVENC).
- **Data:** same session/clip rows. New `live_sessions.progress_message` (migration `20261119_0001`).
- **Perf fix:** `VoiceTrack.zscores` rebuilt its arrays per second. 11 h of ranking went from minutes to 8.5 s, with identical results.

**Proof (Luffy's install, website API):** Lacy "GTA 6 MARATHON DAY 1", **11.1 h**.
- Audio download about 2.5 min; scan about 4 min; transcripts and pick about 3 min.
- **First clip ready at 9 min, all done in 26 min: 9 clips (23-45 s) + 1 correctly rejected cutscene.**
- **438 MB of disk** instead of about 25 GB.
- Face on top / game below confirmed on the gameplay clips. A just-chatting clip got the face crop, which is right.

**Install-only:** `compose.override.yml` `live-worker` now also gets `OPENAI_API_KEY` / `OPENAI_BASE_URL` (the LLM gateway).

**Tests:** 35 live tests + 6 VOD tests passed.

## 10. Owner rules + music on Luffy's install (series B 0012-0013)

| Ask (Luffy) | Change |
|---|---|
| "strictly put *** for cuss words like fuck you" | `media/profanity.py`. `escape_ass_text` (every caption word and hook title) masks swear words, keeping the first letter: "FUCK YOU!" -> "F*** YOU!". Whole-word-only for cock/dick/prick/retard (no cocktail/Dickens false hits). `CAPTION_PROFANITY_FILTER` (default **on**), `CAPTION_PROFANITY_EXTRA`. 15 tests |
| "minimum clip should be 30 sec strict rule" | `CLIP_MIN_SECONDS` default **30**; live clips 30-45 s, VOD clips 30-60 s. The pin `test_default_floor_is_30` was updated: a deliberate owner change |
| Music + SFX library | **Series A patch 0001 applied to his install** (`git apply --exclude=CHANGELOG.md`, clean on V12). The licence-checked bundle (`snipnova-asset-library.zip`, sha `e69ee21b...`) was installed: 378 files, 0 refused. Music 34 -> **123**, SFX 21 -> **310**. Variants are picked per clip (5-9 tracks per mood, 11 per SFX type) |
| (found while testing) face-on-top clips had **no music** | The plain path (facecam/reaction layouts) renders without the EDL graph, so the music bed and SFX were dropped. **Proof:** two renders were byte-identical to the source audio. New `media/audio_finish.py` mixes the EDL's own audio plan over the finished clip (video copied). Plain "just cuts" EDLs have no music, so they're untouched. `PLAIN_PATH_MUSIC` (default on). After the fix: 2 renders with 2 different tracks, -29 -> -13 LUFS. 4 tests |

**Licence gate: yours.** `check_manifest` reports 55 entries, and they're exactly V12's own 34 music + 21 SFX base files (no manifest entry or a different sha than my lineage). None is a library file. Please add V12's base files to `ASSET_MANIFEST.json` with their licences.

**Tests:** 48 passed (audio_finish, asset_library, openverse, edl_render_audio, gaming_templates, clip_min). Plus 15 profanity tests and the live/VOD suites. 6 caption-render tests fail with and without my change (Windows fonts, pre-existing).
