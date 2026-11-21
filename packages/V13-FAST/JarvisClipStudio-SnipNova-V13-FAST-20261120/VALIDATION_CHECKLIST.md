# When You Are Back — Small Checks Only You Can Do

## V12.1 ADDENDUM — new items 59–61 for this build (do these first)

This checklist now belongs to the **Consolidated v12.1 release**
(branch `next-milestone` @ `3900c3da`). After installing v12.1,
`verify.ps1` should end with **218 passed, 0 failed** (three new
migrations, 12 new feature-file checks and one new container
vs v12's 199 — the list grew; nothing was removed). Items 1–58
below still apply unchanged. The new ones:

## 59. The v12.1 reports are in the folder

- **Do this:** After extracting the ZIP, look in the `reports`
  folder and confirm these are there:
  `V121_PACKAGE_RECORD.md`, `V121_INTEGRATION_REPORT.md`,
  `REAL_EXPORT_ACCEPTANCE_V121_SMOKE.md`.
- **Good result:** All present.

## 60. The v12 ZIP — superseded by this one

- The v12 package (SHA-256 starting `19706d5b`) was the previous
  delivery. This v12.1 package installs OVER your v12 build —
  including the live-clipping changes Claude added directly to
  your install afterwards; every one of them is in this tree,
  so nothing is lost. Your extended music/SFX library
  (123 music / 310 SFX) is preserved by the installer's
  copy-back. Keep the older ZIPs only as archives; do not
  reinstall them over v12.1.

## 61. The re-runs that judge v12.1

- **Live page, finished stream:** paste a finished long stream
  (a marathon VOD) on the Live page. Good result: clips appear
  in minutes (not hours), face on top / game below on gameplay
  clips, captions carry the streamer's words only.
- **Live page, live session:** start a session on a live
  stream, let it find a moment, press Stop. Good result: the
  found clips finish rendering; skipped moments list reasons.
- **Layout picker on a NON-gaming niche:** pick "Face on
  top — red" for a podcast/vlog task, and turn **Title on
  clips** OFF for one task. Good result: the layout applies
  anyway (it is no longer gaming-only), and the title-off
  export has no burned-in title (the clip keeps its library
  name).
- **Profanity masking:** a task whose speech swears. Good
  result: burned-in captions and the hook title mask swear
  words as `F***` (first letter kept).
- **Face-on-top music:** an Auto task with a facecam layout.
  Good result: the export HAS its music bed + SFX (this was
  silently missing before v12.1). A Plain task on the same
  source must still have NO music — cuts + captions only.
- **The Speed test (the standing comparison):** re-run the
  iShowSpeed video with the Streamer Reaction card, as in
  item 58.
- **Step renders still pending:** the quality steps (content
  cuts, story selection, screen-as-subject, fit/pad, caption
  cleanup) stay OFF until you approve their before/after
  renders — approving one flips only that switch in a later
  package.

## V12 ADDENDUM — new items 56–58 for this build (do these first)

This checklist now belongs to the **Consolidated v12 release**
(branch `next-milestone` @ `49cfad22`). After installing v12,
`verify.ps1` should end with **199 passed, 0 failed** (one new
migration and 21 new feature-file checks vs v11's 176 — the
list grew; nothing was removed). Items 1–55 below still apply
unchanged. The new ones:

## 56. The v12 reports are in the folder

- **Do this:** After extracting the ZIP, look in the `reports`
  folder and confirm these are there:
  `V12_PACKAGE_RECORD.md`, `BACKEND_GATE_V12.md`,
  `FRONTEND_GATE_V12.md`, `PHASE2_INTEGRATION_REPORT.md`,
  `REAL_EXPORT_ACCEPTANCE_V12_SMOKE.md`.
- **Good result:** All present.

## 57. The v11 ZIP — superseded by this one

- The v11 package (SHA-256 starting `8ea12923`) was the previous
  delivery. This v12 package installs OVER your v11 build — the
  installer backs up first, and your tasks, clips, settings,
  `.env`, music and SFX carry over. Keep the older ZIPs only
  as archives; do not reinstall them over v12.

## 58. The re-runs that judge v12

- **The Speed test (the standing comparison):** re-run the
  iShowSpeed video v11 clipped badly (the audience-sitting
  clip) with the Streamer Reaction card. Good result: the new
  picker ranks the actual reaction moments, not the flat
  audience segment.
- **One GTA-style streamer VOD:** the Streamer Reaction card
  end-to-end. Good result: stacked face-on-top layout engages,
  captions are speech-synced, the facecam is never cropped out.
- **The new shell + Galaxy:** open the app. Good result: no
  left sidebar; a two-tier top bar (search + New clip on top,
  page tabs under it); the dim Galaxy starfield behind pages.
- **Gaming layout picker:** set niche Gaming on the create
  page, pick "Face on top — red", add a credit line, framing
  Auto. Good result: the export stacks facecam over gameplay
  with the red seam and your credit.
- **Plain vs Auto (the switch):** one Plain task (must have
  NO music, NO B-roll, NO SFX — just cuts + captions) and one
  Auto task (music bed + B-roll on a talking-head video).
- **A YouTube link import:** good result: the downloaded
  source is the best quality the video offers (up to 4K),
  not a 720p rendition.

## V11 ADDENDUM — new items 53–55 for this build (do these first)

This checklist now belongs to the **Consolidated v11 release**
(branch `next-milestone` @ `5480a9da`). After installing v11,
`verify.ps1` should end with **176 passed, 0 failed** (the
same count as v10: no new migrations, no new feature-file
checks — the verify list is unchanged). Items 1–52 below
still apply unchanged. The new ones:

## 53. The v11 reports are in the folder

- **Do this:** After extracting the ZIP, look in the `reports`
  folder and confirm these are there:
  `V11_PACKAGE_RECORD.md`, `FRONTEND_GATE_5480A9DA.md`,
  `REAL_EXPORT_ACCEPTANCE_V11_SMOKE.md`,
  `DELTA_GATE_E015B809.md`.
- **Good result:** All present. (A missing required document is
  what killed the v7 package; this package is checked against
  the manifest before delivery.)

## 54. The v10 ZIP — superseded by this one

- The v10 package (SHA-256 starting `e5d219eb`) was the previous
  delivery. This v11 package installs OVER your current build
  (v10 if its install completed; v8 or v9 otherwise) — the
  installer backs up first, and your tasks, clips, settings,
  `.env`, music and SFX carry over. Keep the older ZIPs only
  as archives; do not reinstall them over v11.

## 55. The re-runs that judge v11

- **The new interface (the v11 change itself):** open the app.
  Good result: no greeting hero; the create page is the
  two-zone Studio Console workspace (icon rail on the left,
  command bar on top, Run Spec panel on the right showing
  Mode / Template / Framing / Niche / Output with Generate
  pinned to it); the Sora typeface and mono section labels
  are visible throughout; the recipes picker appears on the
  create page when you have saved recipes; a finished task
  page shows the run-summary strip (clips, total length,
  template, mode).
- **One real run:** create one clip end-to-end from the new
  create page (any mode you use most). Good result: it
  processes and exports exactly as on v10 — the backend is
  unchanged, so any difference is a frontend bug; report it.
- **The still-owed v10 re-runs** (item 52) still judge the
  carried fixes: the framing dropdown (Plain + Always fit),
  the first real streamer VOD from the Streamer Reaction
  card, the MrBeast task, a fresh Auto finance task, and the
  meme-coin vlog task.

## V10 ADDENDUM — new items 50–52 for this build (do these first)

This checklist now belongs to the **Consolidated v10 release**
(branch `next-milestone` @ `901ae252`). After installing v10,
`verify.ps1` should end with **176 passed, 0 failed** (v9 was
174: no new migrations, +2 feature-file checks — the
person-detection module and the streamer-journey module).
Items 1–49 below still apply unchanged. The new ones:

## 50. The v10 reports are in the folder

- **Do this:** After extracting the ZIP, look in the `reports`
  folder and confirm these are there:
  `REAL_EXPORT_ACCEPTANCE_V10.md`, `FULL_REGATE_327886B3.md`,
  `REACTION_API_CONTRACT.md`, `MODEL_CONFIG_AUDIT.md`,
  `REACTION_GATE_FIX_REPORT.md`.
- **Good result:** All present. (A missing required document is
  what killed the v7 package; this package is checked against
  the manifest before delivery.)

## 51. The v9 ZIP — superseded by this one

- The v9 package (SHA-256 starting `d75e5ff1`) was the previous
  delivery. This v10 package installs OVER your current build
  (v8, or v9 if you installed it) — the installer backs up
  first, and your tasks, clips, settings, `.env`, music and
  SFX carry over. Keep the older ZIPs only as archives; do not
  reinstall them over v10.

## 52. The re-runs that judge v10

- **Framing dropdown (G-V9-10 closure):** on the create page,
  pick Plain + Always fit and run a short video. Good result:
  the export keeps the whole picture (no crop) — the setting
  that was API-only in v9 is now one click.
- **Streamer Reaction journey (first real-VOD proof):** run a
  real streamer VOD (gameplay + facecam + mic) from the
  Streamer Reaction card. Good result: the moments park for
  your pick with hook titles shown; the export is a stacked
  vertical — facecam pane on top, gameplay below, facecam
  never cropped out.
- **The still-owed v9 re-runs** (item 49) still judge the
  carried fixes: the MrBeast task (framing + bands), a fresh
  Auto finance task (retention stamps), the meme-coin vlog
  task (title/caption separation).

## V9 ADDENDUM — new items 47–49 for this build (do these first)

This checklist now belongs to the **Consolidated v9 release**
(the fully merged tree: `next-milestone` @ `20f903b`). After
installing v9, `verify.ps1` should end with **174 passed,
0 failed** (v8 was 167: +3 migrations counted in two passes, +1
feature-file check — the framing-mode module). Items 1–46 below
still apply unchanged. The new ones:

## 47. The v9 reports are in the folder

- **Do this:** After extracting the ZIP, look in the `reports`
  folder and confirm these are there:
  `MERGE_BATCH_AND_GATE_REPORT.md`,
  `MERGE_14_15_AND_REGATE_REPORT.md`,
  `REAL_EXPORT_ACCEPTANCE_V9.md`, `FRAMING_MODE_API.md`.
- **Good result:** All present. (A missing required document is
  what killed the v7 package; this package is checked against
  the manifest before delivery.)

## 48. The v8 ZIP — superseded by this one

- The v8 package (SHA-256 starting `7807b457`) was the previous
  install. This v9 package installs OVER it — the installer
  backs up first, and your tasks, clips, settings, `.env`,
  music and SFX carry over. Keep the v8 ZIP only as an archive;
  do not reinstall it over v9.

## 49. The three re-runs that judge v9

- **MrBeast task (framing + text bands):** re-run the same
  MrBeast video. Good result: title and captions sit in ONE
  consistent position per task, never stacked together, and
  wide/group shots no longer slice faces (the render report
  stamps `amputation` / `caption_band` when it acts).
- **A fresh Auto finance task (retention):** run a new finance
  video on Auto. Good result: B-roll lands at the designed rate;
  if an insert is missing, the task's render data now says why
  (`not_inserted` stamps) instead of silence.
- **The meme-coin vlog task (title/caption separation):**
  re-run the task behind the two clips you sent on 2026-11-16.
  Good result: the caption line no longer touches the title —
  they sit in separate bands.

## V8 ADDENDUM — new items 45–46 for this build (do these first)

This checklist now belongs to the **Consolidated v8 release**
(same tree as the v7 package: `next-milestone` @ `5ea71bf` — no
code changes). After installing v8, `verify.ps1` should end with
**167 passed, 0 failed** (unchanged from the v7 package: same
tree, same check list). Items 1–44 below still apply unchanged.
The new ones:

## 45. The two evidence documents are in the folder

- **Do this:** After extracting the ZIP, look in the top folder
  beside this checklist and confirm two files are there:
  `SETTINGS_INTEGRITY_AUDIT.md` and `AI_MODEL_BAKEOFF.md`.
- **Good result:** Both are present. (Their absence from the v7
  ZIP is one of the two reasons that package was not approved;
  this package carries them.)

## 46. The v7 ZIP — do not install it

- The v7 ZIP (SHA-256 starting `54502a16`) was held at the
  release gate and never approved. It is superseded by this v8
  package — same code, completed contents. If both are in
  `D:\Downloads`, install THIS one; the v7 ZIP can be deleted.

## V7 ADDENDUM — new items 40–44 for this build (do these after 33–39)

This checklist now belongs to the **Consolidated v7 release**
(tree `next-milestone` @ `5ea71bf`). After installing v7,
`verify.ps1` should end with **167 passed, 0 failed** (one more
than v6: the degradation-notice component is now a feature
check). Items 1–39 below still apply unchanged. The new ones:

## 40. Re-render keeps the vocals (FIX-51/52)

- **Do this:** On a task that opted into voice/music separation,
  re-render one clip; then change a setting and regenerate the
  task's clips.
- **Good result:** Both outputs keep the clean vocals — no
  background music creeping back under the voice.
- **If it fails:** Send Sanji the clip name and the task name.

## 41. Words per caption line (FIX-54, N4)

- **Do this:** Open a clip in the quick editor's Caption studio,
  set "Words per caption" to 2, save, and look at the captions;
  then try 6.
- **Good result:** Captions regroup to at most 2 words per line,
  then at most 6. (Proven on Sanji's renders; this is your
  machine's confirmation.)

## 42. Montserrat fonts selectable (FIX-54/56, N6)

- **Do this:** In the Studio editor's caption font list, pick
  Montserrat Black, then Montserrat ExtraBold, then back to the
  previous font.
- **Good result:** Both are offered, and the clip renders in
  the picked font each time.

## 43. B-roll concepts — live calibration (FIX-55)

- **Do this:** Run one Auto task on a finance or podcast video
  with B-roll on, and open the B-roll review panel.
- **Good result:** The written concepts name a subject (a
  person / object in use), not scenery lists; room cutaways
  name the competing room in their avoid list.
- **If it fails:** Screenshot a bad concept and send it to
  Sanji — this is the live measurement the calibration is
  waiting for.

## 44. The stale v6 ZIP — do not install it

- The v6 ZIP downloaded on 2026-11-14 (SHA-256 starting
  `d9c3971c`) is superseded by this v7 package. If both are in
  `D:\Downloads`, install THIS one; the v6 ZIP can be deleted.

## V6 ADDENDUM — new items 33–39 for this build (do these first)

This checklist now belongs to the **Consolidated v6 release**
(tree `next-milestone` @ `7959ad7`, state `6669028`; the v5 and v4
addenda and the header paragraph below are carried forward). After
installing v6, `verify.ps1` should end with **166 passed, 0 failed**.
Items 1–32 below still apply unchanged. The new ones:

## 33. Cloudflare hop — the two override lines + the live check

- **Do this:** In `compose.override.yml`, in the `litellm`
  service's environment list, add these two lines beside the other
  provider keys (both keys are already in your `.env`):
  `- CLOUDFLARE_API_KEY=${CLOUDFLARE_API_KEY:-}` and
  `- CLOUDFLARE_ACCOUNT_ID=${CLOUDFLARE_ACCOUNT_ID:-}`. Then
  restart the stack (`docker compose up -d`). The exact check is
  `AI_MODEL_ROUTING.md` §5 in the app folder: force one gateway
  call with model `cloudflare-free`, and confirm the Cloudflare
  model list shows `@cf/openai/gpt-oss-120b`.
- **Good result:** The call succeeds through the Cloudflare hop,
  and the model list shows the model.
- **If it fails:** Send Sanji the exact error — do not paste the
  keys anywhere.

## 34. Plain mode — your FIRST true Plain run (THE v6 check)

- **Do this:** Make ONE task from the "Train For Diamond Gym" clip
  (or any talking-head video). Leave the Template switch on its
  default — **Plain — just cuts**. Watch one full clip.
- **Good result:** The clip is full-screen and tight — the
  speaker's face large in frame, no blurred bands, no cutaway
  picture, no added music, just cuts with captions and the title.
  (On v4 this same run came out wide with template inserts — that
  was the read-back defect, now fixed. This run is the proof.)
- **If it fails:** If the Plain clip still shows a band, a cutaway
  or music, screenshot the exact second and send it to Sanji.

## 35. Caption position — Top vs Bottom on a real clip

- **Do this:** Open a finished clip in the Studio editor, set the
  caption position to **Top**, export; then set it to **Bottom**
  and export again. Watch both.
- **Good result:** The captions visibly sit at the top in the
  first export and at the bottom in the second.
- **If it fails:** If both look the same, send Sanji both clips —
  that would mean the position is not reaching the render.

## 36. Degraded task — kill the gateway mid-analysis

- **Do this:** Only with Sanji on chat — he will give you the exact
  steps. Roughly: start a task, stop the gateway mid-analysis, let
  the task finish.
- **Good result:** The task still completes with clips, and the
  task page shows the amber notice **"Picked without AI analysis"**
  explaining the stand-in picks. Retrying later runs the full AI
  analysis.
- **If it fails:** If the task dies instead, or completes with no
  notice, screenshot the task page and send it to Sanji.

## 37. Find Moments + Manual — Plain vs Auto picks must differ

- **Do this:** Create a Find Moments + Manual task, pick your
  moments, and let it render with the Template on **Plain**. Then
  run the same moments with the Template on **Auto**.
- **Good result:** The two renders look different — the Plain one
  is the tight full-frame cut, the Auto one carries the template
  treatment (and music, if on).
- **If it fails:** If the two files are identical, send Sanji both
  — that would mean the pick ignored the template again.

## 38. Reference Style measured caption height

- **Do this:** Pick a Reference Style whose reference has
  distinctive caption placement (or create one from such a video),
  then make ONE clip with that style. Watch the captions.
- **Good result:** The captions sit at roughly the reference's
  measured height — not at the stock bottom position.
- **If it fails:** Send Sanji a screenshot of the reference's
  captions and of your clip's captions, side by side.

## 39. Eco vs normal render — the benchmark on your RTX 4050

- **Do this:** Only if Sanji asks. He will give you the exact
  steps: run the same task twice — once with Eco render mode on,
  once off — and note the time and how the laptop felt (CPU/RAM
  in Task Manager).
- **Good result:** Both runs finish with the same picture quality;
  the Eco run is kinder to the laptop.
- **If it fails:** Send Sanji both timings. Turn Eco back off
  afterwards unless Sanji says keep it on.

**Note on item 27:** it is retired as written — the SigLIP vision
veto is ON by default in Auto in this build, so no `.env` line is
needed. The experiment is now the default; judge it by the inserts
it leaves or vetoes in Auto tasks.

---


## V5 ADDENDUM — new items 30–32 for this build (do these first)

This checklist now belongs to the **Consolidated v5 release**
(tree `next-milestone` @ `357628a`; the v4 addendum and the header
paragraph below are carried forward). After installing v5,
`verify.ps1` should end with **157 passed, 0 failed**. Items 1–29
below still apply unchanged. The new ones:

## 30. Brief from a screenshot — the diamonds test

- **Do this:** Open the brief panel and upload the diamonds /
  Botswana brief screenshot (the one you sent Sanji on
  2026-11-12). Let it read and draft the brief — do not confirm
  yet, just look at the draft.
- **Good result:** The draft shows the **5 key timestamps**
  (01:30, 03:18, 17:30, 39:20, 42:51), the never-use list
  contains **0:47–0:53**, and there are **18 flag points**
  (licence plates, third-party branding, copyright music,
  sensitive topics, the 12:10 / 19:09 review points and the
  29:39 alcohol point).
- **If it fails:** Screenshot the draft and send it to Sanji —
  note which timestamps or flags are missing or wrong.

## 31. Brief exclusions are hard — run one task with that brief

- **Do this:** Confirm the brief from item 30 and run a task on
  that video with the brief attached. Let it finish, then look
  at the finished clips.
- **Good result:** **No clip includes any second of
  0:47–0:53.** Clips that touch a flag point carry the
  needs-review stamp instead of a clean pass.
- **If it fails:** If any clip contains footage from the
  never-use range, send Sanji the task and the clip — an
  exclusion leak is a release defect, not a setting.

## 32. The ladder check — fallback order in the log

- **Do this:** With your provider keys in `.env` (Groq at
  minimum; NVIDIA / OpenRouter / DeepSeek wake their hops as
  you add them), use the app normally. If a provider ever hits
  its limit, check the worker / gateway log afterwards.
- **Good result:** The log shows the gateway moving to the
  next brain in the ladder order — Groq → NVIDIA → OpenRouter
  free → DeepSeek → **local Ollama last** — and the task
  completes. The ladder ends at your own machine, never at a
  dead end.
- **If it fails:** If a task dies with a provider-limit error
  instead of falling through, screenshot the log lines and
  send them — the order is config-proven and this is its live
  proof.

---

## V4 ADDENDUM — new items 24–29 for this build (do these first)

This checklist now belongs to the **Consolidated v4 release**
(tree `next-milestone` @ `d3eb750`; the header paragraph below is
the v3 text, carried forward). After installing v4, `verify.ps1`
should end with **154 passed, 0 failed**. Items 1–23 below still
apply unchanged. The new ones:

## 24. The Bert re-run — face guard + border trim (THE v4 check)

- **Do this:** Re-run the same 2 Bears, 1 Cave video that produced
  the clip you sent Sanji on 2026-11-11 (captions across the mouth,
  title across the eyes). Same settings as that run.
- **Good result:** In the new clips, captions never cover a mouth
  or chin, the hook title never crosses anyone's eyes, and the
  split-screen tiles have no white sliver strips at their edges.
  Niche still reads Meme & comedy; titles still third-person.
- **If it fails:** Send Sanji the new clip file itself (not a
  screenshot) plus the clip's title — the frames get measured the
  same way the defect was proven.

## 25. Brain live check — one fresh task, any video

- **Do this:** Generate one fresh task from any video you actually
  want clipped. No special settings.
- **Good result:** The task completes normally (the high-effort
  setting can never fail a task — worst case it silently degrades),
  and the picks feel stricter: fewer padded moments, scores spread
  out instead of flattened.
- **If it fails:** If the task errors, screenshot the error and send
  it — that would be the first sign the gateway rejected the new
  request shape, and Sanji needs the exact text.

## 26. Channel monitoring Stage 2 — the Retry button

- **Do this:** On the Autopilot settings page, find a monitored
  channel with a parked or skipped video (or wait for a premiere
  upload on a channel you follow).
- **Good result:** Parked/skipped rows offer **Retry**; a premiere
  shows as waiting instead of failed; retrying creates the task
  (your explicit press is allowed past the daily cap — the cap
  only brakes automatic creation).
- **If it fails:** Tell Sanji which channel and what the row showed.

## 27. (Optional) SigLIP vision veto — Auto template only

- **Do this:** Only if you want to experiment: add
  `BROLL_VISION_VETO_ENABLED=true` to `.env`, restart
  (`docker compose up -d`), and run one Auto-template task on a
  video whose speech mentions things stock footage gets wrong
  (exercise "train", street "race"). First run downloads ~813 MB
  of weights once.
- **Good result:** The task completes; wrong-picture B-roll is
  absent or replaced by the next candidate.
- **If it fails:** Set it back to `false` and restart — you lose
  nothing; tell Sanji what looked wrong.

## 28. (Optional) Speaking-aware crops

- **Do this:** Add `ACTIVE_SPEAKER_TARGETING_ENABLED=true` to
  `.env`, restart (`docker compose up -d`), and run one
  two-speaker video (a podcast or interview). In this build the
  compose file passes the flag into the app, so the `.env` line
  is all it takes. (The first v4 staging lacked the passthrough —
  fixed; G-V4-1 is closed.)
- **Good result:** Crops favour the person actually speaking.
  With the line removed again, plans are identical to before.
- **If it fails:** Remove the line and restart — you lose
  nothing; tell Sanji what looked wrong.

## 29. No Gemini anywhere — check the worker log once

- **Do this:** After installing, run one fresh task (any video),
  then in PowerShell run: `docker compose logs worker --tail
  200 | Select-String 'gemini'` and look at what comes back.
- **Good result:** Nothing mentions a Gemini model. The app's
  default AI is now your Groq key (`openai/gpt-oss-120b`);
  Gemini was the old fallback and answers 411 for you, so it
  must never be picked.
- **If it fails:** If any line mentions a Gemini model,
  screenshot it and send it to Sanji — do not delete
  anything.

---


**Consolidated v3 version — shipped inside the SnipNova Consolidated
v3 ZIP (2026-11-15).** The build this belongs to: `next-milestone` @
`49a134e` (the SnipNova Consolidated v2 build plus the overnight wave
of 2026-11-10/11 and three more merges; adjudicated full gate: backend 3,800 passed
/ 0 failed, frontend 838 passed / 0 failed effective). After installing,
`verify.ps1` should end with **154 passed, 0 failed** — if your
number differs, send Sanji the full output before doing these
checks. Items 1–11 are carried over from the Consolidated build;
items 12–15 from v2; items 16–19 are new for this build; item 20 came with the first rebuild
of this ZIP from @ `005e88e` (the audit-#2 Hindi fixes); items 21–23 came with the second rebuild from @ `49a134e` (VRAM-lite, channel monitoring, reference caption-look).

These checks need your laptop and your RTX 4050. Nobody else can do
them. None of them can break anything. Do them in this order. Tick
each box when done. If a check fails, do not try to fix it alone —
send Sanji what each item says, and keep using the app.

First, always: open Docker Desktop, wait till it says the engine is
running, then open the app at http://localhost:3107.

---

## 1. Auto-niche check — THE check this build exists for

- **Do this:** Generate ONE fresh task from a video that is clearly
  **not** a podcast — a gaming video, a travel/food video, or a
  finance video. Leave the niche on **Auto-detect**. Let it finish.
- **Good result:** The task and its clips show the video's real niche
  (for example "Gaming" or "Travel vlog") — **not** "Podcast & news".
  There is no warning banner about detection failing.
- **If it fails:** If it still says Podcast & news, look for the
  warning **"Auto niche detection FAILED — default Podcast & news
  profile used"** on the task. Screenshot the task page (with the
  warning, if it shows) and send it to Sanji. That warning existing
  at all is the new honesty working — but Sanji needs to see it to
  chase the remaining cause with your logs.

## 2. Music + B-roll sanity on the same fresh task

- **Do this:** Use the task from item 1, with Music set to **Auto**.
  Watch one full clip.
- **Good result:** (a) There IS background music under the speech
  (quiet, ducked) — Auto no longer renders silent just because no
  track is named "calm". (b) Any B-roll cutaway is actually about
  what is being said at that moment — no random unrelated stock
  (no highway cars in a trading video).
- **If it fails:** Tell Sanji which clip, and the second where the
  music is missing or the wrong B-roll appears.

## 3. Speed test of your graphics card (benchmark pack)

- **Do this:** Open the app folder, go to `tools\benchmark-pack`, and
  run `run-benchmark-pack.ps1` exactly like its README says. It will
  tell you to start one fixed test video in the app. Let it finish
  fully. (This build records per-clip timings, so the report can now
  split pre-stage / encode time per clip — and it is the first real
  A/B for the new NVENC pre-stage.)
- **Good result:** A report file appears in
  `tools\benchmark-pack\benchmark-runs\`. The clips are 1080x1920,
  and the report shows real numbers for GPU use, VRAM, RAM and time
  per step.
- **If it fails:** Send Sanji the whole newest `benchmark-runs`
  folder, or a photo of the error text. Do not run it again and
  again.

## 4. Faster video reading on/off test (NVDEC A/B)

- **Do this:** Only if Sanji asks in his message. He will give you
  the exact one-line change and the same video to run twice — once
  off, once on.
- **Good result:** Both runs finish and make normal clips. You note
  which one was faster.
- **If it fails:** Send Sanji the time each run took and any red
  error text. Then turn it back off, like Sanji's message says.

## 5. Voice speed test (Kokoro on your GPU)

- **Do this:** Only if Sanji asks. He will give you the exact steps
  to turn voiceover on and make one short clip with a voice.
- **Good result:** The clip has a clear voice, and it did not take a
  very long time to make. Note roughly how long it took.
- **If it fails:** Send Sanji how long it ran and any error text.
  Turn voiceover back off after — it stays off by default.

## 6. Lip-sync check (your eyes only)

- **Do this:** Watch 2–3 clips you made earlier (the Japan food
  video clips are best). Watch the mouths closely.
- **Good result:** Mouth movement and voice match. No delay you can
  see.
- **If it fails:** Tell Sanji which clip and at what second the mouth
  and voice go apart (for example "clip 2, around 0:07"). That one
  line is enough.

## 7. Watch folder — drop one real video

- **Do this:** This needs the watch-folder mount switched ON: at
  install time, the installer asks (step 6b) — say yes. (If you said
  no, do this check later with Sanji; the manual lines are in the
  guide.) Then: copy (do not move) one video file into
  `D:\Jarvis_Autopilot`. Wait. The app should find it by itself and
  start making clips. Copying the same file again should NOT start a
  second job.
- **Good result:** One new job appears in the app by itself, clips
  get made, and your original file is still sitting in the folder,
  untouched.
- **If it fails:** Send Sanji a screenshot of the Watch-folder
  settings page and tell him how long you waited. Do not delete
  anything.

## 8. Pick a style, then generate (the old error)

- **Do this:** On the Create page, first create or pick one of your
  saved Reference Styles (like "funky"), then press Generate —
  without touching the Auto-detect dropdown after picking the style.
- **Good result:** The job starts. No red error, and no message like
  `Invalid niche_override`.
- **If it fails:** Screenshot the exact error text and send it to
  Sanji. Workaround that always worked: pick the style from the
  Auto-detect dropdown by hand, then generate.

## 9. Brand Kit bumpers + your own SFX (new in this build)

- **Do this:** (a) In Settings → Brand Kit, set an intro (and outro)
  video on your kit, make one fresh clip with that kit, and watch
  the first and last seconds. (b) In the Studio SFX panel, upload
  one small sound effect of your own, place it in an edit, and
  export.
- **Good result:** (a) The intro/outro actually play at the
  start/end of the exported clip. (b) Your uploaded effect appears
  in the library with a "Your effects" tag and is audible in the
  export at the placed point.
- **If it fails:** Tell Sanji which half failed and send a
  screenshot of any error.

## 10. The old failed task (worker logs)

- **Do this:** Only when Sanji is on chat with you. He will give you
  2–3 exact commands to copy-paste. They only read logs; they change
  nothing. The old failed task starts with `aa2fdc74`.
- **Good result:** You paste the output back to Sanji. That is all —
  he reads it, not you.
- **If it fails:** Send a screenshot of whatever the commands
  printed.

## 11. Title stays for the whole clip

- **Do this:** Make ONE new short clip. Play it and look at the
  start, the middle, and the end.
- **Good result:** The title text is still on screen at all three
  points. (Old clips do not change — only new ones get this.)
- **If it fails:** Send Sanji a screenshot from the middle of the
  clip where the title is missing.

## 12. Find Moments no longer waits forever (new in this build)

- **Do this:** Create a task in one of the Find Moments modes. When
  it reaches "waiting for your pick", do NOT pick anything — just
  leave it. It should continue by itself after about 15 minutes.
  (The Create page cards and the waiting screen now say this
  deadline up front — check that wording is there too.) If Sanji
  gives you the exact two lines to shorten the wait to 1 minute
  (the `TASK_SELECTION_AUTOPICK_MINUTES=1` line for `.env` plus its
  one passthrough line in `compose.override.yml`), you may use
  exactly those lines; otherwise just wait the 15 minutes.
- **Good result:** The task continues on its own with the top
  moments, finishes, and carries the note "Auto-picked the top
  moments — no selection was made within 15 minutes."
- **If it fails:** If it is still waiting after 20 minutes,
  screenshot the task page and send it to Sanji.

## 13. Overlays lane — your image inside an export (new in this build)

- **Do this:** Open a finished clip in the studio editor. In the
  new Overlays lane, upload one image (a logo or any PNG), place it
  for a few seconds in the middle of the clip, and export.
- **Good result:** The exported clip shows your image at the placed
  time — over the video/B-roll, under the captions — and it is not
  there outside those seconds.
- **If it fails:** Tell Sanji which step failed (upload, placing,
  or the export) and screenshot any error.

## 14. Lane hide and lock (new in this build)

- **Do this:** In the studio editor, hide the Overlays lane (or the
  B-roll lane) and export again. Then show the lane again, lock it,
  and try to drag one of its items.
- **Good result:** The hidden lane's content is missing from the new
  export — and nothing was deleted: showing the lane brings it
  back. While the lane is locked, its items do not move.
- **If it fails:** Tell Sanji which lane you used and what happened
  instead.

## 15. Cover picture spot check (new in this build)

- **Do this:** Use the fresh task from item 1 (or make any new one)
  and look at the cover image of a finished clip in its results or
  bundle.
- **Good result:** The cover shows a real moment: the subject's face
  in frame, reasonably sharp — not an empty frame and not mostly
  blurred bands.
- **If it fails:** Send Sanji the cover image and say which clip it
  belongs to.

## 16. Plain mode — your "Train For Diamond Gym" clip (new in this build)

- **Do this:** Make ONE task from the "Train For Diamond Gym" clip
  (or any talking-head video). Leave the Template switch on its
  default — **Plain — just cuts**. Watch one full clip. Then make
  ONE more task from the same video with the Template switched to
  **Auto template**, and watch one clip from that.
- **Good result:** The Plain clip is full-screen and tight — the
  speaker's face large in frame, no blurred bands anywhere, no
  cutaway picture, no added music, just cuts with captions and the
  title. The Auto clip looks like the old treatment (template
  effects present) — that confirms the old path is unchanged.
- **If it fails:** If the Plain clip still shows a band, a cutaway
  or music, screenshot the exact second and send it to Sanji. That
  would mean Plain mode is not doing its one job.

## 17. Voice/music separation opt-in (new in this build)

- **Do this:** Only with Sanji on chat — he will give you the exact
  steps. Roughly: add `SEPARATION_ENABLED=true` to `.env`, restart
  the stack (`docker compose up -d`), then make one task on a
  music-heavy clip with separation opted in. (There is no separation
  switch on the Create page yet — the opt-in is a per-task setting
  Sanji sets with you.) The first use downloads the separation
  model once (~84 MB); that is normal.
- **Good result:** The clip's captions and moment picks read the
  clean voice (fewer wrong words where the music was loud), and —
  if you opted into the render strip — the original music is gone
  from the exported clip while the voice stays.
- **If it fails:** Tell Sanji how long the separation step took and
  send any error text. If it is slow, that is useful data too — the
  timing on your 6 GB card is exactly what this check measures.
  Turn it back off afterwards unless Sanji says keep it on.

## 18. Word-sense check — the gym clip, Auto template (new in this build)

- **Do this:** Use the Auto-template task from item 16 (the same
  "Train For Diamond Gym" clip). Watch the full clip.
- **Good result:** No railway, train or bridge footage appears
  anywhere — the clip stays on the speaker and the gym. (A real
  railway/travel sentence would still be allowed its B-roll; the
  fix is about the word's meaning, not a blanket ban.)
- **If it fails:** Note the second where the wrong footage appears
  and send Sanji a screenshot of that frame.

## 19. Brand Kit watermark + branded hook preview (new in this build)

- **Do this:** In Settings → Brand Kit, use your own logo on a kit
  and make one fresh clip with that kit. Before that, look at one
  hook-variant preview for the same task.
- **Good result:** (a) The watermark in the exported clip is a
  sensible size on screen — the same whether your logo file is big
  or small. (b) The hook preview already shows your brand (title
  band, watermark, kit caption look) — it matches the export.
- **If it fails:** Send Sanji the logo file's pixel size and a
  screenshot of the frame where the watermark looks wrong, or of
  the unbranded preview.

---

## 20. Hindi video — the classification failure path (new in this build)

- **Do this:** Make ONE fresh task from a Hindi video — Devanagari
  or Hinglish speech, ideally a Hindi podcast or Hindi business
  talk. This checks the case where auto-niche cannot use the AI
  at all (the failure path): before this build, a Hindi video in
  that situation always landed on Podcast & news.
- **Good result:** The task's niche is **Hindi podcast**, not
  Podcast & news — and the captions come out in the Hindi
  profile's Roman-Hinglish style.
- **If it fails:** Send Sanji a screenshot of the task's niche
  line and of the first caption. If the task shows a
  classification-failure warning, send that too — it names the
  failure instead of hiding it.

---

## 21. Channel monitoring — add a channel (new in this build)

- **Do this:** In Settings → Autopilot, add ONE YouTube channel you
  follow (paste its URL or @handle). Leave the defaults alone.
  Wait a few minutes, then look at the channel's activity feed.
- **Good result:** The channel appears in the list, and NO task was
  created from its old videos — they were only marked as seen. The
  activity feed says so in plain words.
- **If it fails:** If old videos did become tasks, or the channel
  shows an error, screenshot the activity feed and send it to
  Sanji. (A real new upload becoming a task is the longer check —
  it happens when that channel next posts.)

---

## 22. VRAM scenario — the crowded-card numbers (new in this build)

- **Do this:** Open the app folder, go to `tools\benchmark-pack`,
  and run `vram-scenario.ps1` exactly like its README says. It
  walks the app through the crowded case — speech model loaded,
  then local-AI detection, then separation — and reports the free
  graphics-memory numbers at each step.
- **Good result:** The scenario finishes and prints a number for
  each step. Send Sanji the whole output — the numbers only become
  real on your card, and this is the first time anyone measures
  them.
- **If it fails:** Send Sanji the error text. Do not run it again
  and again.

---

## 23. Reference caption-look — a style that copies the captions (new in this build)

- **Do this:** Pick a reference video whose captions look
  distinctive (big coloured text, ALL CAPS, an outline — the more
  obvious, the better). Create a Reference Style from it, then make
  ONE clip with that style. Watch the clip's captions.
- **Good result:** The clip's captions sit where the reference's
  sit, in roughly the reference's colours, with the same casing
  (ALL CAPS stays ALL CAPS). Your Brand Kit still wins where it
  sets a value — that is by design, not a failure.
- **If it fails:** Send Sanji a screenshot of the reference's
  captions and a screenshot of your clip's captions, side by side.

---

**Rules for all checks:** a failed check is useful information, not
your mistake. Never delete a `JarvisClipStudio-BACKUP-*` folder.
Never edit `.env` or `compose.override.yml` by hand for these
checks — if a check seems to need that, stop and ask Sanji.
