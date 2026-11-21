# Installation Guide — SnipNova Consolidated Release v12.1 (2026-11-19)

**The everything-since-v4 release.** It installs over the build
already on your machine — the **SnipNova Consolidated v12 release**,
including the live-clipping changes Claude added directly to your
install afterwards (every one of them is in this package's tree, so
nothing is lost) — and is a strict superset of it. Your data, clips, settings, `.env`,
`compose.override.yml`, music and SFX are preserved.

Read this fully before starting. The install is the same one script
(`install.ps1`) as every release before: it checks the ZIP, backs up
**everything** first, validates the backup, and only then replaces
anything. If any step's output does not match what this guide says,
**stop and send Sanji a screenshot** — do not improvise.

You will need the SHA-256 checksum from Sanji's delivery message. It
is called `<CHECKSUM>` below — use the real one wherever you see it.

**What v12.1 is:** branch `next-milestone` @ `3900c3da` —
the v12 tree plus Claude's combined series, Sanji's gate
fixes and the UI overhaul round 2: live clipping in the app
(sessions API, live-worker container, /live page, Groq
hosted-Whisper captions) + fast VOD clipping of finished
streams, the licence-gated asset library + Openverse
B-roll, the quality steps (content cuts, story selection,
screen-as-subject, fit/pad, caption cleanup — all switches
OFF, dormant until you approve the renders), the transition
library + burned-in caption detector, the OFL/Apache font
pack (THEBOLDFONT removed), one Layout picker for every
niche with hook-title on/off, profanity masking, the 30 s
minimum clip rule, the plain-path music/SFX finish fix, the
LiteLLM gateway timeout, and the composer-hero UI. It
installs **over your current v12 build**; the installer
backs up first and your data, clips, settings, `.env`,
music and SFX are preserved — including your extended
asset library (the installer copies your `assets\music`
and `assets\sfx` contents back over the new tree).
**Three new database migrations** — the schema rises to
55. The installer also wires live clipping ON for your
install (live-worker override block + `LIVE_CLIPPING=on`;
the shipped default is off) and its LiteLLM smoke check
retries ~60 s before failing. Gates on this exact tree:
the full backend gate, the full frontend gate and a
one-combo smoke record — the smoke record is **NOT RUN** (the sandbox attempt was terminated ~53 minutes into the analysis passes, before any export; the report states it plainly, with the attempt record) (`reports/V121_INTEGRATION_REPORT.md`,
`reports/REAL_EXPORT_ACCEPTANCE_V121_SMOKE.md`). Package
record: `reports/V121_PACKAGE_RECORD.md`.

**RAM note (16 GB machines):** before a big render, close
other heavy apps — local LLM servers especially. A single
resident model server can hold ~4 GB; with renders running,
that is enough to push a render out of memory on a 16 GB
machine. Closing them before you render avoids the failure;
nothing in the install changes because of this note.

**What the tree adds (on top of the v12 build):** live
clipping in the app + the VOD fast clipper, the Layout
picker for every niche + title on/off, the 30 s minimum
clip rule, profanity masking, the plain-path music/SFX
fix, the licence-gated asset library + licence manifest,
the Openverse B-roll source, the transition library +
caption-band detector, the font pack, the dormant quality
steps, the LiteLLM gateway timeout, and the UI overhaul
round 2 (composer hero, bento, liquid glass, vivid
Galaxy). **Three new database migrations** — the schema
rises to 55. Full record: `reports/V7_FIXES.md`.

**What the tree adds (on top of the v8 build):** retention
editing that stamps every undelivered B-roll insert with its
stage and reason; multi-subject framing that stops slicing
faces on wide shots; one title/caption band per task (the
stacking you reported on v8 is fixed); campaign-brief
enforcement on regeneration; Recipes (saved create-settings
bundles, watch-folder autopilot included); the B-roll
consolidation (editor B-roll search works without a Pexels
key); floor-export parity; the RTX 4050 thread budget; two
finance hook styles and the gaming trailer-breakdown style
(available, not defaults); and `framing_mode` — your per-task
framing choice (auto / always fit / always crop), independent
of the template. **Three new database migrations** — the
schema goes from 48 to 51. Full record: `reports/V7_FIXES.md`.

**What this build adds (on top of the v4 build):**

- **Plain — just cuts now really means Plain.** This was the big
  defect in v4/v5: task settings saved at creation (template mode,
  music mode, separation, duration) were dropped when the task was
  read back, so every "Plain" task silently rendered the full Auto
  template — music, effects, B-roll and all. That is fixed and
  independently verified: a Plain task renders Plain, Music Off
  means off, and stored settings are re-validated before rendering
  with a warning instead of a silent fallback. (Checklist item 34
  is the live proof — your first true Plain run.)
- **Smart B-Roll v2 is complete** (Auto template only; Plain never
  gets B-roll). The brain now writes a filmable concept before
  anything searches, so "market" in a trading video finds trading
  footage, not a street market; a second stock source (Coverr) and
  a local stock library with a licence record back up Pixabay; a
  vision check vetoes wrong-picture inserts (calibrated on real
  pairs, ON by default in Auto); finance videos now get B-roll at
  1.5 inserts a minute; and the editor shows each insert's concept
  and thumbnail with Remove / Next candidate. There is also a
  per-task B-roll ON/OFF switch on the Create page.
- **The brains now work as one ladder, ending at your own machine.**
  DeepSeek first, then Groq, NVIDIA NIM, OpenRouter free, then a
  NEW free hop — Cloudflare Workers AI — then Groq-20b, then local
  Ollama last. If one provider hits its limit, the next takes over
  by itself; a task can no longer die because a free pool ran dry.
  Gemini is in no chain, and the paid Luna model is deliberately
  not in the ladder. **The ladder order becomes live with this
  install** — the routes wake as you add your keys (see the keys
  note below). (Checklist items 32–33 are the live proof.)
- **Briefs from a screenshot.** Campaign briefs often arrive as a
  picture — now the brief panel takes the picture. Upload a
  screenshot of a brief and the app reads it **on your machine**
  (OCR — no picture leaves your computer) and drafts the brief:
  the **key timestamps** the brief names, its **never-use ranges**,
  and its **flag points** (licence plates to blur, third-party
  branding, copyright music, sensitive topics…). Nothing is
  applied until you review the draft and confirm it — same rule as
  pasted briefs. It was tested on the diamonds screenshot you sent:
  all 25 timecodes read, the 5 key moments found, the 0:47–0:53
  avoid range captured, 18 flags with their categories.
  (Checklist items 30–31.)
- **Never-use means never.** A brief's excluded ranges are now
  hard at every stage: candidate moments are cut outside them when
  picked, the final slate is checked again, and the render itself
  subtracts them — a finished clip cannot contain a second of
  never-use footage. A clip that touches a flag point is stamped
  **needs-review** instead of quietly passing. (Before this build,
  "avoid" ranges in a pasted brief could even be read backwards,
  as allowed ranges — that inversion is fixed and pinned by a
  test.)
- **Captions go where you put them.** Caption Studio's Top /
  Middle / Bottom and words-per-line were saved but never read —
  every position rendered the same. They now reach the render
  (proven on real exports), and a Reference Style's measured
  caption height is honoured too. (Checklist items 35 and 38.)
- **The editor now exports what it shows.** On-device exports draw
  overlays (they used to silently drop them); TikTok, Shorts and
  Reels presets really differ (Shorts follows YouTube's bitrate
  table); combining clips keeps each clip's own framing, grade,
  volume and overlays; hidden lanes stay hidden in exports; and a
  device export no longer dies outright when a stock cutaway fails
  to download or a caption font is missing — it skips / falls back
  and says so.
- **If every AI provider is down, the app still finishes the job.**
  A failed analysis now degrades to an improved sound-and-scene
  selection instead of killing the task, and the task page says so
  plainly with an amber notice ("Picked without AI analysis").
  (Checklist item 36.)
- **Eco render mode** (new switch): one clip at a time with bounded
  encoder threads, for when rendering used to pin your CPU/RAM at
  100%. Same picture quality — only the scheduling changes.
  (Checklist item 39.)
- **Everything else is v4, unchanged** — the picker brain, the
  face-safe captions and titles, the border trim, channel
  monitoring, the Groq-first default, all of it.



- **Groq** — already in your `.env`; the ladder's first brain.
- **NVIDIA NIM and OpenRouter** — their passthrough lines are
  already in your live `compose.override.yml` (added 2026-11-11),
  so adding `NVIDIA_API_KEY` / `OPENROUTER_API_KEY` to `.env`
  wakes those hops with this build.
- **DeepSeek** — one extra step: your preserved
  `compose.override.yml` does not yet pass `DEEPSEEK_API_KEY` to
  the gateway. Add the key to `.env` AND the line
  `- DEEPSEEK_API_KEY=${DEEPSEEK_API_KEY:-}` to the `litellm`
  service's environment list in `compose.override.yml`, then
  `docker compose up -d`. Without the line, the key never reaches
  the gateway and just that one hop stays asleep — the rest of the
  ladder works regardless. Sanji will walk you through it at key
  time if you prefer.
- **GPT-6 Luna is deliberately not in the ladder** — it is free
  only inside the ChatGPT app; its API is paid and has no free
  route, so no key can power it for free.

**Feature switches in this build (unchanged from v4):**

- The v4 feature switches all work from `.env`: the compose file
  passes `ACTIVE_SPEAKER_TARGETING_ENABLED`,
  `BROLL_VISION_VETO_ENABLED` and `BORDER_TRIM_ENABLED` into the
  backend and worker containers (border trim is ON by default;
  `BORDER_TRIM_ENABLED=false` is its kill switch). All optional —
  normal use needs none of them.

**Honest limits are in `KNOWN_ISSUES.md` inside this delivery**, and
`reports/V7_FIXES.md` carries the change record for the v8 tree; the merge and gate reports (`reports/MERGE_BATCH_AND_GATE_REPORT.md`, `reports/MERGE_14_15_AND_REGATE_REPORT.md`, `reports/FULL_REGATE_327886B3.md`, `reports/MERGE_STACK_AND_FRONTEND_REPORT.md`, `reports/FRONTEND_GATE_5480A9DA.md`, `reports/PHASE2_INTEGRATION_REPORT.md`, `reports/BACKEND_GATE_V12.md`, `reports/FRONTEND_GATE_V12.md`) record what merged on top of it, `reports/V121_INTEGRATION_REPORT.md` records the v12.1 integration and its gates, and `reports/V121_PACKAGE_RECORD.md` records this package.
`TRACKER.md` inside the app folder carries the 78-requirement score
— **this package carries the current 2026-11-14 re-score**
(COMPLETE 6, PARTIAL 21, NEEDS REAL-WORLD VALIDATION 51; rows move
only on evidence). Note: earlier packages carried the previous
re-score snapshot; this one is refreshed.

You need **20 GB free on D:** (the script checks — the backup alone
duplicates your project and volumes). Your earlier backup folders can
stay where they are; this install makes a **new** backup folder. Do
not delete any backup until acceptance passes.

---

## 0. Prerequisites

1. **Docker Desktop must be running.** Open it and wait until it says
   the engine is running. Do not start while Docker is still starting.
2. **No task should be running** in the app. If a video is processing,
   let it finish first.
3. Close the app browser tabs (localhost pages).
4. Open **Windows PowerShell** (not Command Prompt).

## 1. Check the downloaded ZIP (do not skip)

Download the ZIP into `D:\Downloads`. In PowerShell:

```powershell
cd D:\Downloads
Get-FileHash .\JarvisClipStudio-SnipNova-Consolidated-V12.1-20261119.zip -Algorithm SHA256
```

Compare the long hash it prints with the `<CHECKSUM>` from Sanji's
delivery message — they must be **identical, character for character**.
If they differ, the download is corrupted or wrong: delete the ZIP,
download it again, and re-check. Do not install from a ZIP whose hash
does not match.

## 2. Extract the ZIP (Expand-Archive)

Still in PowerShell, in `D:\Downloads`, extract the ZIP with this exact
command:

```powershell
Expand-Archive -Path 'D:\Downloads\JarvisClipStudio-SnipNova-Consolidated-V12.1-20261119.zip' -DestinationPath 'D:\Downloads\JarvisClipStudio-SnipNova-Consolidated-V12.1-20261119' -Force
```

(If a folder of that name already exists there from a previous attempt,
delete that folder first and extract again, so the extraction is clean.
Do NOT extract over your live project folder in `D:\EditedTool`.)

**Warning — the double-nested folder:** the delivery wraps everything
in one folder of the same name, so after extracting, the scripts live
in the **inner** folder:

```
D:\Downloads\JarvisClipStudio-SnipNova-Consolidated-V12.1-20261119\JarvisClipStudio-SnipNova-Consolidated-V12.1-20261119\
```

You should see `install.ps1`, `rollback.ps1`, `verify.ps1`,
`INSTALLATION_GUIDE.md` (this file), `DELIVERY_CONTENTS.md`,
`KNOWN_ISSUES.md`, `VALIDATION_CHECKLIST.md`, the `JarvisClipStudio`
folder (the app itself) and the `reports` folder. If you only see one
folder inside the extracted folder, open it — you are one level too
shallow. (A previous install attempt failed only because this
extraction step was skipped — the scripts were never actually on
disk.)

## 3. Pre-flight: prove the scripts parse (30 seconds)

Before running anything, let PowerShell itself check that the three
scripts are readable. Run these lines **directly in your PowerShell
window**, one block at a time — do not wrap them in any other command.
From the **inner** folder (see step 2):

```powershell
cd 'D:\Downloads\JarvisClipStudio-SnipNova-Consolidated-V12.1-20261119\JarvisClipStudio-SnipNova-Consolidated-V12.1-20261119'
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
$parseTokens = $null
$parseErrors = $null
[void][System.Management.Automation.Language.Parser]::ParseFile((Resolve-Path '.\install.ps1').Path, [ref]$parseTokens, [ref]$parseErrors)
if ($parseErrors) { $parseErrors | ForEach-Object { $_.Message } } else { 'PARSE OK' }
$parseErrors = $null
[void][System.Management.Automation.Language.Parser]::ParseFile((Resolve-Path '.\verify.ps1').Path, [ref]$parseTokens, [ref]$parseErrors)
if ($parseErrors) { $parseErrors | ForEach-Object { $_.Message } } else { 'PARSE OK' }
$parseErrors = $null
[void][System.Management.Automation.Language.Parser]::ParseFile((Resolve-Path '.\rollback.ps1').Path, [ref]$parseTokens, [ref]$parseErrors)
if ($parseErrors) { $parseErrors | ForEach-Object { $_.Message } } else { 'PARSE OK' }
```

Each script must print **PARSE OK**. (The scripts were parse-checked with real PowerShell 7.4.6 in
Sanji's sandbox — PARSE OK on all three — but this 30-second
check on your machine is the real proof, and it changes nothing.) If any script prints an error instead, stop and send Sanji
the text.

## 4. Run the installer

From the same inner folder, run the installer — pass the ZIP path
explicitly, exactly as downloaded:

```powershell
.\install.ps1 -ZipPath 'D:\Downloads\JarvisClipStudio-SnipNova-Consolidated-V12.1-20261119.zip' -ExpectedSha256 '<CHECKSUM>'
```

The installer works through these steps, printing what it is doing:

1. Verifies the ZIP checksum again (stops on mismatch).
2. Checks at least 20 GB free on D:.
3. Extracts the ZIP to a staging folder (it finds the project tree
   inside the wrapper folder by itself).
4. **Backup** — database dump (taken while your stack is still up), a
   tar archive of every Docker volume, and your whole current project
   folder, all inside
   `D:\EditedTool\JarvisClipStudio-BACKUP-<date-time>`, with a
   `manifest.txt` describing it.
5. **Validates the backup** — your `.env`, your
   `compose.override.yml`, a non-trivial database dump, and every
   volume archive readable. If validation fails, your old stack is
   restarted from your (untouched) project folder and the install stops. Nothing has
   been replaced at that point.
6. Installs the new tree and copies back **your** files unchanged:
   `.env`, `compose.override.yml`, and everything inside your
   `assets\music` and `assets\sfx` folders. It then checks the feature
   environment passthroughs in `compose.override.yml`. **This build
   adds no new feature variables for the installer to add** (the
   ladder's keys travel through the gateway service's own environment
   in your override — see the keys note above — and the brief feature
   needs none), so the
   expected step-6 output for THIS build is,
   for each of backend, worker and ai-worker:
   `compose.override.yml: service '<name>' environment list already has all the feature variables.`
   If a service is instead reported as skipped (no environment list,
   or mapping-style), the app still works — every variable has a safe
   default in code — but send Sanji the output. The installer also
   makes sure `.env` carries `VOICEOVER_ENABLED=false` and
   `ENABLE_NVDEC=false` defaults if they are missing (both features
   stay OFF unless you choose otherwise).
6b. **Watch-folder mount — OPTIONAL, and the installer asks.** This
    is the step that lets Watch-Folder Autopilot see a folder on your
    computer. First create the folder (the installer also creates it
    for you if you say yes):

    ```powershell
    New-Item -ItemType Directory -Path 'D:\Jarvis_Autopilot' -Force
    ```

    The installer then asks:
    `Enable the D:\Jarvis_Autopilot watch-folder mount now? [y/N]`
    - **You already said yes at the v3 install,** so the expected
      outcome is that the step prints that the mount is already
      there, service by service, and changes nothing.
    - **If you answer yes:** it adds a read-only mount
      `D:\Jarvis_Autopilot:/autopilot:ro` and the line
      `- WATCH_FOLDER_PATH=${WATCH_FOLDER_PATH:-}` to the **backend**
      and **worker** services only (the two services that read
      `WATCH_FOLDER_PATH`; the ai-worker does not use the folder), and
      sets `WATCH_FOLDER_PATH=/autopilot` in your `.env`. Success
      prints:
      `Watch-folder mount APPLIED: backend + worker now mount D:\Jarvis_Autopilot read-only at /autopilot, and .env sets WATCH_FOLDER_PATH=/autopilot.`
      Your originals in that folder are **never moved or deleted** —
      the mount is read-only and the watcher only reads.
    - **If the automatic edit cannot be made safely** (a section is
      missing or in the wrong shape), the step skips with a printed
      warning — `Watch-folder mount NOT applied automatically. Your files were left untouched.` —
      plus the exact lines to add by hand (also listed below). Your
      files stay untouched and the install continues.
    - **If you answer no (the default):** nothing changes.
      `compose.override.yml` and `.env` stay byte-identical, and
      Watch-Folder keeps watching its in-container default folder —
      which means files dropped into `D:\Jarvis_Autopilot` **do
      nothing**. That is your choice, not a silent failure.
    - Either way, both files were already backed up in steps 4–5, so
      **rollback restores them wholesale** — the mount leaves no
      residue after a rollback.
    - **Manual fallback lines** (only if the step skipped and you want
      the mount): in `compose.override.yml`, under `volumes:` of BOTH
      backend and worker add `- D:\Jarvis_Autopilot:/autopilot:ro`,
      under `environment:` of BOTH add
      `- WATCH_FOLDER_PATH=${WATCH_FOLDER_PATH:-}`, and add
      `WATCH_FOLDER_PATH=/autopilot` to `.env`. Then restart:
      `docker compose up -d`.
7. Builds and starts the new stack. Backend, worker and frontend
   source changed in this release — expect a real build, comparable
   to the v4 install.
8. Smoke checks: backend health, the frontend page, and the LiteLLM
   gateway model list (checked from inside the backend container,
   because its port is not published to the host). **Note:** a FAIL on
   the LiteLLM line in this step is a known boot-timing false alarm —
   the gateway can still be starting (it printed FAIL at your Final,
   Consolidated, v2 and v3 installs and the gateway was fine minutes later). Do NOT
   roll back for that line alone; run `verify.ps1` (step 6 below)
   once the stack has settled — it retries properly.

## 5. What success looks like

The installer ends with:

```
== INSTALL COMPLETE
```

plus the backup location, and the reminder that the backup is never
deleted by any script.

**Send back to Sanji:**
- a screenshot (or copy-paste) of the full installer output,
  especially the step-6 passthrough lines, **what step 6b printed**
  (applied / already there / skipped / declined), the final PASS/FAIL
  lines and the backup folder name;
- the output of `verify.ps1` (next section);
- then work through `VALIDATION_CHECKLIST.md` — items 33–39 (the
  Cloudflare check, the first true Plain run, caption position,
  the degraded-task notice, the F-R1 / F-A proofs and the Eco
  benchmark) are the headline checks for this build.

**One thing to know about video length:** sources longer than **90
minutes** are rejected unless your `.env` sets a higher
`MAX_VIDEO_DURATION` (a 2-hour video needs **14400**). **Your existing
`.env` already carries 14400**, and the installer copies your `.env`
back unchanged, so this does **not** change on your machine — but if
you ever reset `.env`, long videos stop working until you raise that
value again.

**If a step fails — quick guide:**
- *Checksum mismatch* → the download is bad. Re-download, re-check
  (step 1). Never force it.
- *Not enough disk space* → free space on D: (old staging folders
  `JarvisClipStudio-STAGING-*` are safe to delete yourself). Do NOT
  delete any `JarvisClipStudio-BACKUP-*` folder before acceptance.
- *Backup validation failed* → your old install was restarted from
  your untouched project folder automatically. Send Sanji the red lines.
- *Build failed / stack did not start / a smoke check failed* → the
  installer prints the exact `rollback.ps1` command with your backup
  path. You can run it, or message Sanji first. Nothing is deleted
  either way. (Exception: a lone LiteLLM smoke FAIL at install time
  is the known timing false alarm — verify first, see step 8.)
- *"Could not clear the folder"* → an editor or terminal is holding
  the project folder open. Close it and re-run the installer.

## 6. Run the verification script (read-only)

From the same extracted inner folder:

```powershell
.\verify.ps1
```

It changes nothing; it only checks: the project tree and **all 55
migration files** present in the tree **and applied** in the database
— **three more than v12** (`20261118_0001_live_clipping.sql`,
`20261118_0002_task_hook_title_enabled.sql`,
`20261119_0001_live_session_progress.sql`); the feature files for
this build (everything the v12 script checked, **plus twelve new
ones: the live VOD engine, the live API, the live repository, the
live worker, profanity masking, the plain-path audio finish, the
asset library, the asset licence manifest, the transition library,
the font-pack manifest, the Live page and the Layout picker**);
your `.env` and `compose.override.yml` in place; containers running
(**eight** now — live-worker joined the list); GPU visible; NVENC
working; the long-form pacing value actually reaching the worker
(`ANALYSIS_LONGFORM_TPM` = 120000); the voiceover toggle state;
backend/frontend answering; the LiteLLM gateway (from inside the
backend container); and Ollama reachable.

**Expected result for this build: `218 passed, 0 failed`.** (Counted
from this build's own verify.ps1 check list: 1 project folder + 55
migration files + 88 feature files + 1 `.env` + 1 override + 8
containers + 1 GPU + 1 NVENC + 1 pacing + 1 voiceover + 55 migrations
applied + 1 gateway + 2 health + 1 frontend + 1 Ollama. Do not
compare with the v12 build's 199 — this release adds three migrations
(counted in both passes), twelve feature-file checks and one
container check.) Send Sanji the
full output.

## 7. What you do NOT need to do

- **You do not need to edit `compose.override.yml` or `.env` by hand
  for the install** — the only optional edits are the watch-folder
  mount (step 6b offers to do it) and, **only at key time**, the one
  DeepSeek passthrough line in the keys note above. The installer
  also wires the **live-worker** block for you (Groq + gateway keys
  and the GPU reservation) and sets `LIVE_CLIPPING=on` in `.env`.
  ONLY if the installer prints a NOTE that the live-worker wiring
  could not be completed automatically, add this block to
  `compose.override.yml` under `services:` by hand, then restart
  the stack (`docker compose up -d`):

  ```yaml
    live-worker:
      environment:
        - GROQ_API_KEY=${GROQ_API_KEY:-}
        - OPENAI_API_KEY=${OPENAI_API_KEY:-}
        - OPENAI_BASE_URL=${OPENAI_BASE_URL:-}
      deploy:
        resources:
          reservations:
            devices:
              - driver: nvidia
                count: 1
                capabilities: [gpu]
  ```
- **You do not need to touch your music/SFX.** The installer carries
  over everything inside `assets\music` and `assets\sfx`, same as
  before — including your extended library (123 music / 310 SFX);
  the upgrade does NOT wipe it. (Sound effects you upload inside the app are separate —
  they live in the app's own storage, not in `assets\sfx`.)
- **NVDEC stays off, voiceover stays off** — unchanged defaults.
- **You do not need to re-apply any patch** — there are none; every
  fix travels as regular commits in the tree.

## 8. Rollback (if anything is wrong)

From the extracted delivery folder:

```powershell
.\rollback.ps1
```

That automatically uses the newest `JarvisClipStudio-BACKUP-*` folder
next to your project. To name the exact backup this install printed,
use:

```powershell
.\rollback.ps1 -BackupRoot 'D:\EditedTool\JarvisClipStudio-BACKUP-<the date-time the installer printed>'
```

What rollback does: stops the stack, renames the new project folder
to `JarvisClipStudio-FAILED-<date-time>` (**kept, never deleted**),
puts your previous project folder back (including the pre-install
`.env` and `compose.override.yml`, so the watch-folder mount from
step 6b is reverted too), restores the database from the pre-install
dump, starts everything, and runs the same smoke checks. Note: a
backup can be rolled back from **once** — its `project\` folder is
moved out by the restore, so a second rollback from the same backup
stops at its own guard and tells you. That is by design.

There is also a nuclear option, `-RestoreVolumes`, which additionally
overwrites every Docker volume with the backup archives. Do **not**
use it unless Sanji asks — the normal rollback already restores the
project and the database, and your volumes were never changed by the
install itself.

**Backup rule: every `JarvisClipStudio-BACKUP-*` folder (including
the Milestone 1, Milestone 2, Update 1, Hotfix, Final, Consolidated, v2, v3 and v4 ones) is
deleted only by you, manually, and only after acceptance testing
has passed.** No script ever deletes a backup.
