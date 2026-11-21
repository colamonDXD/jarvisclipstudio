# Known Issues — SnipNova Consolidated Release v12.1 (2026-11-19)

## V12.1 ADDENDUM (2026-11-19) — read this first

This ZIP is the **Consolidated v12.1 release**: branch
`next-milestone` @ `3900c3da` — the v12 tree plus Claude's
combined series (asset library + quality steps, transition
library, font pack, live clipping in the app, the VOD fast
clipper, the Layout picker, profanity masking, the 30 s
floor, the plain-path music fix), Sanji's gate fixes (the
cold-open floor fix, live-SQL execution coverage, the font
eval re-base, the LiteLLM gateway timeout) and the UI
overhaul round 2. It installs **over your current v12
build** — including the live-clipping changes Claude added
directly to your install afterwards; every one of them is
in this tree, so nothing is lost. **Three new database
migrations** (55 total). `verify.ps1` expects **218 passed,
0 failed** — the check list grew by the 3 new migrations,
12 new feature-file checks and the live-worker container;
no check was removed or weakened. Full record:
`reports/V121_PACKAGE_RECORD.md`.

**What v12.1 adds:**

- **Live clipping in the app** (`/live` page): record a
  live stream and get hook-first clips (voice reaction →
  on-camera gate → preview → final render with face-on-top
  layout and streamer-only captions via Groq hosted
  Whisper, local fallback). Stop finishes the clips already
  found. Enabled on your install by the installer
  (`LIVE_CLIPPING=on`); the shipped default is off.
- **Fast VOD clipping:** paste a FINISHED 10–20 h stream on
  the Live page — audio-only scan (heatmap-boosted),
  windowed Groq transcripts, gateway moment picks,
  section-only downloads. Claude's proof on your install:
  an 11.1 h marathon → 9 clips in 26 minutes, 438 MB of
  disk instead of ~25 GB.
- **One Layout picker for every niche** (Auto / Face on
  top · red / Face on top · gold / Full screen / Crop, with
  drawn examples and a credit line) — replaces the Framing
  toggle and the gaming-only row. A chosen layout + a found
  facecam now wins over Fit/Crop framing. **Title on clips**
  switch (off = no burned-in title).
- **Owner rules:** minimum clip length is now strictly
  **30 s**; swear words are masked in burned-in captions
  and hook titles (`F***`, first letter kept, filter on by
  default).
- **Music fix:** face-on-top (plain-path) clips silently
  had NO music bed and NO SFX — they now get the EDL's
  audio plan mixed over the finished clip. Plain "just
  cuts" still has no music, by design.
- **Licence-gated asset library:** `ASSET_MANIFEST.json`
  (391 entries, every shipped audio file licence-checked
  with a matching SHA) + the Openverse B-roll source, the
  248-entry transition library + burned-in caption-band
  detector, and the OFL/Apache caption **font pack**
  (34 fonts). **THEBOLDFONT is removed** (its licence could
  not be verified; Montserrat-Black replaces it).
- **Quality steps, dormant:** Step 1 content-aware cuts,
  Step 4 story-event selection, Step 3 screen-as-subject,
  Step 2 fit/pad + 4K probe, Step 5 caption cleanup — all
  switches OFF until you approve the step renders.
- **LiteLLM gateway timeout:** a route that never answers
  now fails over after 90 s instead of hanging a task at
  "Analyzing content with AI" forever.
- **UI overhaul round 2:** composer-hero create page,
  bento tiles, liquid glass, vivid Galaxy starfield.
- **Installer:** live-worker wiring + `LIVE_CLIPPING=on`
  (above), and the LiteLLM smoke check retries ~60 s
  before it can fail (the v12 exit-1 false alarm is fixed).

**Closed by v12.1:** G-V12-7 (Step 1 content-aware cuts,
the asset-library code and the OpenCode R2/3 libraries are
now IN the tree — the quality steps ship dormant, per the
approval rule). Your extended asset library (123 music /
310 SFX on your current install) is not in this package,
but the installer copies your `assets\music` and
`assets\sfx` contents back over the new tree — it is NOT
wiped by the upgrade.

**New / updated gaps for v12.1 (the honest list):**

- **G-V121-1 — smoke acceptance NOT RUN on this exact tree.** The packaged app's real service path was started in Sanji's sandbox (fresh database, all 55 migrations applied by the app's init path, fixture staged); the run was terminated externally ~53 minutes into the source-analysis passes — before the render stage — and produced no export. Same sandbox limit as v12's attempts (multiple full-source analysis passes over a 68-minute AV1 file on a 1.5-CPU VM); no product error was observed. The tree's gates (backend 5,634 passed, frontend 959, database-backed suites, Postgres tier) all ran on this exact code; the end-to-end render proof on YOUR machine is checklist item 61 + the standing Speed re-run. Full attempt record: `reports/REAL_EXPORT_ACCEPTANCE_V121_SMOKE.md`.
- **G-V121-2 — live Groq captions + GPU encode not yet
  proven on a real stream.** Claude's real-stream test
  (IShowSpeed) produced one finished 30.1 s clip through
  the local path; the Groq hosted-Whisper captions and the
  GPU encode were deployed after that test. Treat the
  first real-stream Groq-captioned clip as the proof run.
- **G-V121-3 — the extended asset library is a
  post-install fetch for fresh installs.** The package
  ships the base bundle (7 music + 22 SFX); the ~221 MB
  extended library stays a one-command verified fetch
  (Q14(b)). On THIS upgrade yours is preserved in place
  (above), so no re-fetch is needed.
- **G-V121-4 — the 30 s floor vs editor caption
  re-renders is an open owner question.** The floor itself
  is pinned by tests and the cold-open interaction is
  fixed; whether the floor should also lengthen editor
  caption re-renders is undecided (the re-render path
  currently pins it off in its roundtrip test).

The v12 gaps G-V12-1…G-V12-8 below carry forward unless
closed above (G-V12-7 is closed — see above): B-roll
density on finance speech still PARTIAL (G-V12-1), quiet
reactions can still be missed (G-V12-2), the real-clip
layout-gate comparison is still Claude-side evidence only
(G-V12-3), the live resolver/veto legs are still NOT RUN
on Sanji's tree (G-V12-4), quiet music beds (G-V12-5),
green-screen facecams (G-V12-6) and the 1920×1080 working
box (G-V12-8) all stand. Everything in the v12 addendum
below is carried forward as history.

---

## V12 ADDENDUM (2026-11-18) — read this first

This ZIP is the **Consolidated v12 release**: branch
`next-milestone` @ `49cfad22` — the v11 tree plus the full
integration stack (Lane A reaction intelligence, Phase 1
Smart B-Roll v3, Phase 2 auto-editor), the OpenCode NVENC
probe fix, maximum-quality link downloads, the gaming
layout picker, the Galaxy starfield background, the
top-navigation shell, and the bundled music/SFX libraries.
It installs **over your current v11 build**. **One new
database migration** (52 total). `verify.ps1` expects
**199 passed, 0 failed** — the check list grew by the new
migration and 21 new feature-file checks (8 stack code
files + the 13 bundled asset files); no check was removed
or weakened. Full record: `reports/V12_PACKAGE_RECORD.md`.

**What v12 adds:**

- **Reaction intelligence (Lane A):** the moment picker now
  fuses facecam reaction, voice bursts, transcript emotion
  and gameplay events into one score, with a plain-language
  reason and per-signal badges on the picker output. On the
  measured streamer fixture the fixed picker scores 6/7:
  the real reaction ranks #1, the loud decoy is excluded.
- **Smart B-Roll v3 (Phase 1):** concept-first B-roll with
  the vision veto in Auto (Plain stays exempt, zero B-roll).
- **Phase 2 auto-editor:** facecam-rectangle layouts (a
  borderless facecam is found by its persistent edge), the
  red/gold "Face on top, game below" gaming templates with
  seam captions and an optional credit line, clip QC
  (loudness −14 LUFS target + complete-thought boundary
  checks, warn-only), and one consistent layout per clip.
- **NVENC probe fix (OpenCode):** GPU encoding is detected
  properly again, so renders can use the RTX GPU instead of
  falling back to CPU.
- **Maximum-quality downloads:** link imports now fetch the
  best quality the source offers, up to 2160 (4K) by height
  (was capped at 1080; portrait videos suffered most).
- **Gaming layout picker** on the create page (Campaign &
  style, Gaming niche, applies when framing is Auto).
- **Galaxy starfield background** and the **top-navigation
  shell** (two-tier top bar; the icon rail is gone).
- **Bundled music + SFX:** 7 CC0 FreePD beds (calm, comedy,
  corporate, energetic, funk, phonk, upbeat) and 6 original
  synthesised SFX now ship inside the package.

**New / updated gaps for v12 (the honest list):**

- **G-V12-1 — B-roll density on finance speech is
  PARTIAL.** On the TJR finance test windows, v3 inserted
  1.67 inserts/minute against the 3/minute bar — every cue
  was on-topic; the shortfall is cue SUPPLY (the lexicon
  matched too few moments), and the drops were legitimate
  protections. Recorded as PARTIAL, not fixed.
- **G-V12-2 — quiet reaction moments can still be
  missed.** A reaction whose face signal is real but
  moderate (measured ~1 z-score, below the burst threshold)
  is a known borderline miss (the fixture's R2 case, 6/7).
  The picker is deliberately NOT tuned to force it.
- **G-V12-3 — the real-clip layout-gate comparison is
  Claude-side evidence only.** His table (his gate engages
  7/8 real streamer clips vs the old gate's 0/8) was NOT
  re-run on this tree (the comparison clips are not in the
  sandbox). The composed gate ships with both sides' test
  suites green; the real-VOD proof is still owed.
- **G-V12-4 — live B-roll resolver + vision-veto legs not
  re-proven on this tree.** The sandbox holds no Pixabay/
  Coverr keys and no cached veto model, so those legs are
  NOT RUN here; Claude's live evidence is owner-run only.
- **G-V12-5 — some music beds sit quiet under constant
  speech.** The profile beds sit ~24–29 dB under a voice
  that never pauses (compressor-ducking limitation); only
  the meme/comedy bed is tuned clearly audible.
- **G-V12-6 — green-screen / cut-out facecams can be
  missed.** The facecam-rectangle detector keys on a
  persistent rectangle edge; chroma-key cams with no
  rectangle (and very low-contrast edges) can be missed.
- **G-V12-7 — NOT in v12 (arrives in V12.1):** Claude's
  Step 1 content-aware cuts (cuts at real shot changes /
  sentence ends instead of equal intervals), the gated
  asset-library code + meme pack, and the OpenCode Round
  2/3 libraries. Step 1 ships dormant until you approve
  its before/after render.
- **G-V12-8 — downloads are normalised after fetching.**
  The working file is scaled into a 1920×1080 box before
  clipping: portrait 1080×1920 passes unchanged, but a 4K
  landscape source is capped at that box. The download
  itself is now source-max.

The v11 gaps G-V11-1…G-V11-11 below carry forward unless
closed above: G-V11-10's reaction-ranking half is CLOSED
by Lane A (fusion scoring shipped + fixture-proven); its
real-streamer-VOD validation half is still open and is
now G-V12-3's companion. Everything in the v11 addendum
below is carried forward as history.

---

## V11 ADDENDUM (2026-11-17) — read this first

This ZIP is the **Consolidated v11 release**: branch
`next-milestone` @ `5480a9da` — the v10 tree plus the
post-v10 frontend merges ONLY (frontend completion, React
Bits polish, Aurora Flow shell, Studio Console rebuild).
It installs **over your current build** (v10 if its install
completed; v8 or v9 otherwise). **No new database
migrations** (51 total, unchanged) and **no backend changes
at all** — the name-only diff `901ae252..5480a9da` touches
zero backend files. `verify.ps1` expects **176 passed,
0 failed** — the same count as v10, because the verify
check list is unchanged for this package. Full record:
`reports/V11_PACKAGE_RECORD.md`.

**What v11 adds (all frontend):**

- **The Studio Console interface** — a full frontend
  rebuild, not a reskin: Sora is now the app typeface with
  JetBrains Mono console labels; the "Good afternoon"
  greeting hero is gone (the create page opens straight
  into the workspace); a slim icon rail plus a command bar
  replace the old sidebar/top pills; the create page is a
  two-zone studio workspace with a sticky **Run Spec**
  panel (your whole run as a spec sheet, Generate pinned
  to it). The dim aurora background is kept. Every create
  control from v10 is present with identical behaviour and
  identical payloads (the gate's parity checklist is in
  `reports/FRONTEND_GATE_5480A9DA.md`).
- **Frontend completion:** the recipes picker on the
  create page (sends `recipe_id` only when you pick one);
  the "Before you generate" pre-processing summary; the
  run-summary strip on the task page (clips, total length,
  template, mode — only fields the API actually returns);
  the legacy `include_broll` chip retired from the create
  page (the authoritative `broll_enabled` path is
  unchanged).

**Closures since v10: none of the G-V10 gaps.** V11 is a
frontend-only delta; it does not touch the backend areas
the gaps live in. The gaps are renumbered for v11 below,
mapped 1:1 to their v10 numbers (the v10 entries further
down remain the detailed record):

- **G-V11-1 — the Cloudflare hop needs two lines in YOUR
  override.** (= G-V10-1.)
- **G-V11-2 — a phantom migration ledger row silently
  suppresses a migration (LOW).** (= G-V10-2.)
- **G-V11-3 — live provider validation is still owed (your
  machine).** (= G-V10-3.) The live model bake-off has
  still never run; the sandbox holds no provider keys by
  design. For v11 the acceptance is a one-combo smoke
  (Full AutoClip + Plain) on this exact tree
  (`reports/REAL_EXPORT_ACCEPTANCE_V11_SMOKE.md`) — the
  full 8/8 acceptance stands on the v10 tree, whose
  backend is byte-identical to this one. Still owed: the
  Cloudflare live check, the Eco vs normal benchmark on
  your RTX 4050, real Demucs separation quality, and the
  re-runs in checklist item 55.
- **G-V11-4 — degraded picks can still lack captions.**
  (= G-V10-4.)
- **G-V11-5 — the create form hardcodes `processing_mode:
  "fast"`** (= G-V10-5; audit INFO F-5). Still a product
  decision; the Studio Console create page sends the same
  payload as before.
- **G-V11-6 — a very slow render can outlive a database
  timeout (sandbox observation).** (= G-V10-6.)
- **G-V11-7 — legal pages still show a placeholder company
  name.** (= G-V10-7.)
- **G-V11-8 — trailer-breakdown limits; non-face remainder.**
  (= G-V10-8.) The trailer ring still renders leaf/eye-shaped;
  drone light-portrait subjects still have no detector.
- **G-V11-9 — the DeepSeek route is dormant (zero credit).**
  (= G-V10-9.) The shipped config names the current model
  (`deepseek-v4-pro`); the slot is correct-but-dormant, the
  ladder fails over as designed and Groq's 120B model is
  effectively primary — the app runs at ₹0. A top-up is
  your decision only.
- **G-V11-10 — the streamer reaction journey is proven on a
  synthetic fixture, not yet on a real streamer VOD.**
  (= G-V10-10.) A real streamer VOD is still the
  outstanding proof (checklist item 55). Inside the stacked
  layout, the gameplay pane uses 'content' mode — dark
  margins inside the pane; nothing is cropped and the HUD
  stays readable.
- **G-V11-11 — HF_TOKEN passes through the compose files;
  pyannote is still not wired.** (= G-V10-11.) Diarization
  still waits on the pyannote terms-accept (your click)
  plus the future wiring.

Everything in the v10 addendum below is carried forward as
history — the renumbered gaps above point back to it.

---

## V10 ADDENDUM (2026-11-17) — read this first

This ZIP is the **Consolidated v10 release**: branch
`next-milestone` @ `901ae252` — the v9 tree plus the post-v9
merges (the six-branch face-evidence stack, the frontend
completion merges, the Streamer Reaction journey, the
reaction-layout gate fix, the test-only re-gate fixes, and the
model-config corrections). It installs **over your current
build** (v8 or v9). **No new database migrations** (51 total,
unchanged). `verify.ps1` expects **176 passed, 0 failed**
(v9's 174, +2 feature-file checks — the person-detection
module and the streamer-journey module joined the curated
list for this package). Full record:
`reports/V10_PACKAGE_RECORD.md`.

**What closed since v9:**

- **G-V9-10 is CLOSED: the framing-mode dropdown is on the
  create page in this build** (Auto / Always fit / Always
  crop), so Plain + always-fit is now reachable from the UI —
  no API call needed.
- **G-V9-9 is PARTIALLY CLOSED: non-face subjects now have
  evidence.** The face-evidence stack shipped: bright-extent
  subject evidence, a gated YOLOX person detector, and
  depiction screens — wide, group and action shots keep
  people in frame instead of protecting faces only. What
  remains: drone light-portrait subjects still have no
  detector (see G-V10-8).
- **The Recipes and Learning Engine surfaces now reach the
  backend.** The frontend API proxies ship in this build, so
  the recipe picker and the learning screens work against the
  existing backend instead of dead-ending.

**Carried gaps, renumbered for v10** (the v9 entries below
remain the detailed record; nothing else about them changed):

- **G-V10-1 — the Cloudflare hop needs two lines in YOUR
  override.** (= G-V9-1.)
- **G-V10-2 — a phantom migration ledger row silently
  suppresses a migration (LOW).** (= G-V9-2.)
- **G-V10-3 — live provider validation is still owed (your
  machine).** (= G-V9-3.) The live model bake-off has still
  never run; the sandbox holds no provider keys by design.
  The four-mode acceptance plus the streamer path were
  re-run on this exact tree
  (`reports/REAL_EXPORT_ACCEPTANCE_V10.md`) — pipeline,
  settings and degraded-path proof, **not** AI-quality
  proof. Still owed with it: the Cloudflare live check, the
  Eco vs normal benchmark on your RTX 4050, real Demucs
  separation quality, and the re-runs in checklist item 52.
- **G-V10-4 — degraded picks can still lack captions.**
  (= G-V9-4.)
- **G-V10-5 — the create form hardcodes `processing_mode:
  "fast"`** (= G-V9-5; audit INFO F-5). Still a product
  decision; the frontend is Sanji's workstream now.
- **G-V10-6 — a very slow render can outlive a database
  timeout (sandbox observation).** (= G-V9-6.)
- **G-V10-7 — legal pages still show a placeholder company
  name.** (= G-V9-7.)
- **G-V10-8 — trailer-breakdown limits; non-face remainder.**
  (= G-V9-8, plus the unclosed half of G-V9-9.) The trailer
  ring still renders leaf/eye-shaped; drone light-portrait
  subjects still have no detector.

**New in v10:**

- **G-V10-9 — the DeepSeek route is dormant (zero credit).**
  The account's key is valid but the balance is zero, so the
  primary-director slot cannot answer. The shipped config
  now names the current model (`deepseek-v4-pro`;
  `deepseek-chat` was retired by DeepSeek on 2026/07/24), so
  the slot is correct-but-dormant: the ladder fails over as
  designed and Groq's 120B model is effectively primary —
  the app runs at ₹0. A top-up is your decision only; if you
  ever top up, the slot revives with no further change.
- **G-V10-10 — the streamer reaction journey is proven on a
  synthetic fixture, not yet on a real streamer VOD.** The
  full path (upload → moments → pick → stacked-layout
  vertical export) was verified end-to-end in the sandbox on
  the synthetic streamer fixture: the reaction-ranked pick
  won, the loud-gameplay decoy was not picked, and the
  stacked layout engaged with the facecam preserved. A real
  streamer VOD is still the outstanding proof (checklist
  item 52). Inside the stacked layout, the gameplay pane
  uses 'content' mode — the game sits in a centred window
  over a blurred plate, so there are dark margins inside
  the pane; nothing is cropped and the HUD stays readable.
- **G-V10-11 — HF_TOKEN now passes through the compose
  files; pyannote is still not wired.** Diarization still
  waits on the pyannote terms-accept (your click, owed
  since v9) plus the future wiring; the token plumbing is
  now in place for it.

Everything in the v9 addendum below is carried forward as
history — the renumbered gaps above point back to it.

---

## V9 ADDENDUM (2026-11-16) — read this first

This ZIP is the **Consolidated v9 release**: the **fully merged
tree** (branch `next-milestone` @ `20f903b`) — the 13-branch
merge batch, the trailer-breakdown and framing-mode merges, one
fix found during those merges, and one residue-removal chore
commit. It installs **over your current v8 build**. **Three new
database migrations** (51 total): Recipes, the watch-folder
recipe link, and the framing-mode column. `verify.ps1` expects
**174 passed, 0 failed** (v8's 167, +3 migrations counted twice,
+1 feature-file check — the framing-mode module joined the
curated list for this package). Full record:
`reports/V9_PACKAGE_RECORD.md`.

**What closed since v8:**

- **The title/caption stacking you reported on v8 is fixed in
  this tree.** Title and captions are now decided once per task
  with a forced minimum separation — they can no longer render
  in the same position as one block. The re-run on your machine
  is the proof (checklist item 49).
- **Retention editing:** a B-roll insert that does not land is
  now stamped with the stage and the reason (`not_inserted`)
  instead of vanishing silently; sibling clips never share a
  B-roll asset; slide transitions no longer land mid-speech; the
  opening push-in is universal in Auto. (The v8 behaviour behind
  your "one B-roll in 165 seconds" report.)
- **A zoom defect found during the merge gate is fixed:** an
  explicit encoder thread count silently killed every zoompan
  zoom on the merged tree — punch-in zooms and the opening
  push-in rendered flat while every stage reported success. The
  renderer no longer sets an encoder thread count; the trailer
  render proof is the standing regression test.
- **G-V8-4 is PARTIALLY CLOSED:** degraded (no-AI) exports now
  receive grounded hook titles (the floor-export parity fix).
  Captions burn from the transcript sidecar; in a fully keyless
  degraded run with no word timings, captions can still be
  absent — see G-V9-4.

**Carried gaps, renumbered for v9** (the v8 entries below remain
the detailed record; nothing else about them changed):

- **G-V9-1 — the Cloudflare hop needs two lines in YOUR
  override.** (= G-V8-1.) Add `CLOUDFLARE_API_KEY` /
  `CLOUDFLARE_ACCOUNT_ID` beside the other provider keys in the
  `litellm` environment list of your preserved
  `compose.override.yml`, then restart. Checklist item 33 is the
  live check. The keys are already in your `.env`.
- **G-V9-2 — a phantom migration ledger row silently suppresses
  a migration (LOW).** (= G-V8-2.) The runner trusts
  `schema_migrations`; reachability is low — the ledger is
  written only by the runner. A post-init sanity probe is a
  future product fix.
- **G-V9-3 — live provider validation is still owed (your
  machine).** (= G-V8-3.) The live model bake-off has never run —
  the build sandbox holds no provider keys by design, so NO model
  winner was wired for this release either; the shipped ladder is
  the proven one. The four-mode acceptance was re-run on this
  exact tree (`reports/REAL_EXPORT_ACCEPTANCE_V9.md`, 32/32
  exports) — pipeline, settings and degraded-path proof, **not**
  AI-quality proof. Still owed with it: the Cloudflare live check
  (item 33), the Eco vs normal benchmark on your RTX 4050 (item
  39), real Demucs separation quality, and the re-runs in
  checklist item 49.
- **G-V9-4 — degraded picks can still lack captions.** (=
  G-V8-4, hook-title half fixed — see the closures above.)
  Stand-in segments carry no word timings; when a cached
  transcript with timings exists (normal use), captions burn
  normally.
- **G-V9-5 — the create form hardcodes `processing_mode:
  "fast"`** (= G-V8-5; audit INFO F-5). The main create form does
  not expose the processing-mode choice; whether it should is a
  product decision that is still open — and the frontend is now
  your workstream.
- **G-V9-6 — a very slow render can outlive a database timeout
  (sandbox observation).** (= G-V8-6.) Recorded for completeness;
  renders on your hardware take seconds.
- **G-V9-7 — legal pages still show a placeholder company
  name.** (= G-V8-7.) The Terms/Privacy pages carry the stock
  placeholder; send Sanji the legal operator name to replace it.

**New in v9:**

- **G-V9-8 — trailer-breakdown limits.** The red ring around the
  circled detail renders as a leaf/eye shape, not a clean
  ellipse, on this renderer (a libass quirk — measured and
  documented in the lane report; a PNG-overlay ring is the
  follow-up). The freeze, punch-zoom, reaction beat and
  cliffhanger are rendered-proven on a stand-in fixture. The
  planner wiring that spots trailer/reaction windows in a real
  video is NOT built yet — the style takes windows as inputs.
  The final Avengers: Doomsday trailer had not been released at
  build time (rumoured ~19 Nov). The style's value is audience
  data — only real posts prove it.
- **G-V9-9 — non-face subjects still have no detection boxes.**
  Drone light-portraits, helmeted riders and backs are invisible
  to the face tracker, so framing protects faces only; a
  near-empty wide shot is flagged (`content_flag`), not fixed.
  The specced builds (a bright-extent presence signal, then a
  gated person detector — `reports/NONFACE_SUBJECT_SPIKE.md`)
  land after v9.
- **G-V9-10 — framing mode is backend + API in this build.** The
  create-page dropdown (Auto-decide / Always fit / Always crop)
  is frontend work on your side; the contract is
  `reports/FRAMING_MODE_API.md`. Until the dropdown exists,
  `framing_mode` can be set through the API; unset tasks behave
  exactly as before (Plain crops, Auto decides per shot).
- **New hook styles are available, not defaults.** `mistake_fix`
  and `list_fill` (finance/education) and `trailer_breakdown`
  (gaming) join the style lists; the defaults are unchanged.
  Whether they perform is audience data — nothing here claims
  they do.

Everything in the v8 addendum below is carried forward as
history — the renumbered gaps above point back to it.

---

## V8 ADDENDUM (2026-11-15) — read this first

This ZIP is the **Consolidated v8 release**: the **same app tree**
as the v7 package (branch `next-milestone` @ `5ea71bf`) — **zero
code changes**. The v7 package was held at the 2026-11-15 release
gate (`reports/RELEASE_GATE_NOV15.md`, verdict DO NOT SHIP) for
two package-level reasons. Both are closed in this package; the
full record is `reports/V8_PACKAGE_RECORD.md`.

**Closure 1 — G-V7-3 is CLOSED.** The four-mode real-export
acceptance has been re-run on `5ea71bf` itself
(`reports/REAL_EXPORT_ACCEPTANCE_ON_TIP.md`): 32/32 exports, all
1080×1920; Manual Cutter PASS outright in both templates; the
AI-dependent combos PASS on the degraded path — the build sandbox
holds no provider keys by design, so those combos exercised the
designed failover into the selection floor with honest stamps.
That is pipeline/mode/template proof on this exact tree, **not**
AI-quality proof: real AI selection quality with live providers
remains your-machine validation (G-V8-3 below). The re-run also
confirmed F-R1 and F-R2 fixed at rendered level and settings
persistence across a database restart (byte-identical exports).

**Closure 2 — the package contains its evidence.** The v7 ZIP
was missing `SETTINGS_INTEGRITY_AUDIT.md` and
`AI_MODEL_BAKEOFF.md`; both are now at the top level of this ZIP,
byte-identical to the audited originals (SHA-256 `298fed5a…` and
`d5acafa8…`). Checklist item 45 has you confirm they are in the
extracted folder.

**Carried gaps, renumbered for v8** (the v7 entries below remain
the detailed record; nothing else about them changed):

- **G-V8-1 — the Cloudflare hop needs two lines in YOUR
  override.** (= G-V7-1.) Add `CLOUDFLARE_API_KEY` /
  `CLOUDFLARE_ACCOUNT_ID` beside the other provider keys in the
  `litellm` environment list of your preserved
  `compose.override.yml`, then restart. Checklist item 33 is the
  live check. The keys are already in your `.env`.
- **G-V8-2 — a phantom migration ledger row silently suppresses
  a migration (LOW).** (= G-V7-2.) The runner trusts
  `schema_migrations`; reachability is low — the ledger is
  written only by the runner. A post-init sanity probe is a
  future product fix.
- **G-V8-3 — live provider validation is still owed (your
  machine).** (= G-V7-4.) The live model bake-off has never run —
  the build sandbox holds no provider keys by design, so NO model
  winner was wired for this release; the shipped ladder is the
  proven one from FIX-39. Still owed with it: the Cloudflare live
  check (item 33), the Eco vs normal benchmark on your RTX 4050
  (item 39), real Demucs separation quality (FIX-51/52 prove the
  stem is USED; how cleanly Demucs itself separates on your GPU
  is your-machine evidence), and the live-provider half of the
  four-mode acceptance (the on-tip re-run proved the pipeline and
  the degraded path; selection quality with real providers is
  yours to measure).
- **G-V8-4 — degraded picks have no captions or hook title
  yet.** (= G-V7-5.) Stand-in segments carry no word timings.
  Observed again in the on-tip acceptance re-run (its O-R3).
  Queued improvement.
- **G-V8-5 — the create form hardcodes `processing_mode:
  "fast"`** (= G-V7-6; audit INFO F-5). The main create form does
  not expose the processing-mode choice; whether it should is a
  product decision that is still open.
- **G-V8-6 — a very slow render can outlive a database timeout
  (sandbox observation).** (= G-V7-7.) In the build sandbox
  (1.5 CPU), one in-transaction render ran past the app's 300 s
  idle-in-transaction timeout and its plan write rolled back.
  Renders on your hardware take seconds, so this is recorded for
  completeness, not as expected behaviour.
- **G-V8-7 — legal pages still show a placeholder company
  name.** (= G-V7-8.) The Terms/Privacy pages carry the stock
  placeholder; send Sanji the legal operator name to replace it.

**The v7 ZIP** (SHA-256 starting `54502a16`) **was never approved
for install** and is superseded by this package — same code,
incomplete contents. Checklist item 46 covers it.

Everything in the v7 addendum below is carried forward as history
— FIX-51…FIX-56 all still stand, and the v7 gap entries remain
the detailed record for the renumbered gaps above.

---

## V7 ADDENDUM (2026-11-15) — read this first

This ZIP is the **Consolidated v7 release**: the v6 build below plus
**20 commits** (tree `next-milestone` @ `5ea71bf`). The full record
is `reports/V7_FIXES.md`. Everything in the v6 addendum below is
carried forward as history — FIX-35…FIX-50 all still stand. What
v7 adds:

- **FIX-51 — re-renders keep the vocals stem** (`6e1576b`, merge
  `c662413`). On v6, re-rendering a clip from a separation-opt-in
  task silently dropped the clean vocals track, so the re-render
  was cut from the raw mix with its background music back in —
  hook previews too. Re-renders now resolve the same stem the
  original render used. Proven on real exports: the music-only
  clip measures −91.0 dB after re-render, same as the original
  (74.8 dB of suppression vs the unstripped control).
- **FIX-52 — regenerations keep the vocals stem too** (`47a66de`,
  merge `2eedce8`). The settings-regeneration path had the same
  hole as FIX-51 through a different chain; it now threads the
  stem end to end, with the same rendered proof.
- **FIX-53 — Reference Style transitions are guidance, honestly
  labelled** (`2561cce`, merge `0a63942`). The audit found the
  `on_topic_change` / `on_punchline` transition fields never
  reached any render join — the only consumer was the AI
  director's prompt. They are now documented exactly as that
  (director guidance), in code and in the prompt. No render
  behaviour change; a true per-join mechanism would need a real
  topic/punchline signal, which does not exist yet.
- **FIX-54 — editor LOW items N4–N7** (merge `a9c6bc0`, cleanup
  `d3e7b52`). The quick editor gains a words-per-caption-line
  control (1–8) that the AI editor also understands; the font
  select offers both Montserrat fonts (Black and ExtraBold);
  the combine hint now discloses that hidden lanes are left out
  of a combination; and the editor audit's count is restated
  honestly (90 of 91 inventoried controls confirmed).
  Words-per-line and both Montserrat fonts are proven on real
  renders.
- **FIX-55 — B-roll concept phrasing** (merge `9b24c7b`). The
  100-pair veto calibration showed most false vetoes came from
  concepts written as scenery checklists. The planner now writes
  concepts around a defining subject (a person doing the thing,
  the object in use), and room concepts automatically get
  competing-room avoid lists (a studio avoids bed/sofa/kitchen
  counter; an apartment avoids mixing desk/monitor speakers).
  Closes the code side of G-V6-4; a threshold sweep on the
  combined 100-pair base found no setting that beats the shipped
  one, so the veto default stands. The live effect measures on
  your machine (G-V7-4).
- **FIX-56 — the Montserrat font type gap** (`701546b`, merge
  `5ea71bf`). FIX-54's font select offered two fonts the saved
  font setting did not officially recognise; the type check on
  the first v7 candidate caught it (that gate went RED on exactly
  this one error, the fix was merged, and the re-gate is GREEN).
  The setting now recognises all six fonts.
- Also in: the full gate on this exact tip — backend **4,656
  passed / 0 failed**, frontend **899 passed / 0 failed**, eslint
  0, production `next build` exit 0, migrations 48
  (`reports/INTEGRATED_GATE_5EA71BF.md`) — and the rendered-proof
  batch that closes G-V6-3 (`reports/RENDERED_PROOFS_BATCH_NOV14.md`,
  `reports/N4_N6_RENDERED_PROOF.md`).

**Closures of v6 gaps:** **G-V6-3 is CLOSED** — F-1, F-R2, F-R1,
F-A and F-B all have rendered-video proof on this tip (5/5 in
the batch). **G-V6-4 is CLOSED in code** (FIX-55); its live
calibration is G-V7-4 below. **G-V6-6 is CLOSED** — the
degradation-notice component is now one of verify.ps1's feature
checks (52, up from 51), so the expected verify total is **167**.

**New known gaps in v7 (honest list):**

- **G-V7-1 — the Cloudflare hop needs two lines in YOUR
  override.** Unchanged from G-V6-1: add `CLOUDFLARE_API_KEY` /
  `CLOUDFLARE_ACCOUNT_ID` beside the other provider keys in the
  `litellm` environment list of your preserved
  `compose.override.yml`, then restart. Checklist item 33 is
  the live check. The keys are already in your `.env`.
- **G-V7-2 — a phantom migration ledger row silently suppresses
  a migration (LOW).** Unchanged from G-V6-2 (the runner trusts
  `schema_migrations`; reachability is low — the ledger is
  written only by the runner). A post-init sanity probe is a
  future product fix.
- **G-V7-3 — the 32-export acceptance set predates this tip.**
  Those exports were rendered and independently verified (32/32)
  on tip `9ba6602`. Every fix merged since has its own rendered
  proof on THIS tip (the batch + N4/N6), but a fresh four-mode
  acceptance re-run on `5ea71bf` itself has not been run; it is
  owed to the Nov 17 integration pass and to your machine.
- **G-V7-4 — live provider validation is still owed (your
  machine).** The live model bake-off has never run — the build
  sandbox holds no provider keys by design, so NO model winner
  was wired for this release; the shipped ladder is the proven
  one from FIX-39. Still owed with it: the Cloudflare live
  check (item 33), the Eco vs normal benchmark on your RTX 4050
  (item 39), and real Demucs separation quality (FIX-51/52 prove
  the stem is USED; how cleanly Demucs itself separates on your
  GPU is your-machine evidence).
- **G-V7-5 — degraded picks have no captions or hook title
  yet.** Unchanged from G-V6-5 (stand-in segments carry no word
  timings). Queued improvement.
- **G-V7-6 — the create form hardcodes `processing_mode:
  "fast"`** (audit INFO F-5). The main create form does not
  expose the processing-mode choice; whether it should is a
  product decision that is still open.
- **G-V7-7 — a very slow render can outlive a database
  timeout (sandbox observation).** In the build sandbox
  (1.5 CPU), one in-transaction render ran past the app's
  300 s idle-in-transaction timeout and its plan write rolled
  back. Renders on your hardware take seconds, so this is
  recorded for completeness, not as expected behaviour.
- **G-V7-8 — legal pages still show a placeholder company
  name.** Unchanged from §N1 below: the Terms/Privacy pages
  carry the stock placeholder; send Sanji the legal operator
  name to replace it.
- Everything in the v6 addendum below that was open stays open
  unless FIX-51…FIX-56 above or the checklist addendum closes
  it — in particular G-V6-1/G-V6-2 (restated as G-V7-1/G-V7-2)
  and G-V5-1 (DeepSeek passthrough).

---

## V6 ADDENDUM (2026-11-15) — read this first

This ZIP is the **Consolidated v6 release**: the v5 build below plus
**151 commits** (tree `next-milestone` @ `7959ad7`, state commit
`6669028`; 86 commits over the 2026-11-13 rehearsal tree `11fd688`).
The full record is `reports/V6_FIXES.md`. Everything in the v5
addendum and the v4/v3 text below is carried forward as history —
FIX-1…FIX-34 all still stand. What v6 adds:

- **FIX-35 — task settings now actually reach the pipeline**
  (merge `ef87820`). The read-back defect: `template_mode`,
  `music_mode`, `separation_opt_in` and `source_duration_seconds`
  were written at creation but dropped by both task serializers,
  so every "Plain — just cuts" task on v3/v4/v5 silently rendered
  the full Auto template. Fixed (+14 lines, serializer-only) and
  independently verified on a live database: Plain renders Plain,
  Music Off means off. Old tasks heal on re-render / regenerate.
- **FIX-36 — Smart B-Roll v2 complete** (Slices 1–6: `dc48e5f`,
  `c2362d4`, `c2753b7`, `c9c9bf9`, `90ee61e`, `d0d8990`; Coverr +
  stock library `a0c6901`; per-task toggle `bc2f230`). Word-sense
  resolution ("market" = trading), concept-writing brain,
  concept-driven resolver (Pixabay photos + Ken Burns stills,
  Coverr second source), SigLIP vision veto calibrated on real
  pairs and ON by default in Auto (Plain exempt), per-niche
  densities (finance B-roll ON at 1.5/min), editor review surface
  (concept + thumbnail, Remove / Next candidate).
- **FIX-37 — niche classification fixes** (vlog `dc6c766`,
  finance `3024cea`, evidence window `6942209`, Hindi `005e88e`).
  Declared vlog formats and day-trading monologues no longer fall
  into Podcast & news; guardrails now sample past the opening
  minutes; Hindi rescues to hindi_podcast.
- **FIX-38 — framing fix** (`5159b1d`) **+ hook-title blur band**
  (`07ba446`). Face tracks beat fit-blur exemptions; one layout
  per clip; captions stay out of fit blur bands; hook titles stay
  out of blurred bands.
- **FIX-39 — provider ladder v2** (`5395c0a`) **+ Cloudflare hop**
  (`7f04289`). One strength ranking for every chain: DeepSeek →
  Groq 120B → NIM Ultra → NIM DeepSeek Flash → OpenRouter free →
  **Cloudflare free** → Groq 20B → Ollama last. Free-only by
  construction (single fixed slug, paid-plan fragments banned by
  tests). Full routing doc: `AI_MODEL_ROUTING.md` in the tree.
- **FIX-40 — Eco render mode** (`9e6e750`). `RENDER_ECO_MODE`,
  off by default: one clip at a time, bounded encoder threads,
  lowered priority. Scheduling only — picture quality constants
  byte-untouched and pinned.
- **FIX-41 — caption position wiring** (`820171d`). Caption
  Studio Top / Middle / Bottom and words-per-line were stored but
  never read; they now reach the render (proven on real exports:
  Top 0.155, Middle 0.496, Bottom 0.755; no-request renders
  byte-identical to before). Closes the old audit item A2-2.
- **FIX-42 — editor export honesty, D3–D7** (`da910b6`). Device
  exports draw overlays; TikTok / Shorts / Reels presets really
  differ (Shorts follows YouTube's bitrate table); combine keeps
  each clip's framing, grade, volume and overlays; hidden lanes
  honoured in exports; Brand Kit copy corrected (bumpers do
  stitch). All proven on real exports.
- **FIX-43 — selection floor + honest degradation** (`9ba6602`,
  notice `9c546cd`). A failed AI analysis degrades to an improved
  sound-and-scene selection (recall 0.00 → 0.20 full slate, 0.40
  cuts-only on the gold set) instead of killing the task, and the
  task page shows an amber "Picked without AI analysis" notice.
- **FIX-44 — analysis-cache isolation** (F-1 `615b618`, F-R2
  `19069e9`). Manual Cutter ranges can no longer be served as an
  AI analysis, and a degraded stand-in is never cached as one.
- **FIX-45 — caption-carry fixes** (F-B `230d8d0`, F-A `75fc951`,
  follow-ups `5bca336`, F-C `68bec02`). Stored caption position
  survives direct re-renders, the caption PATCH route and hook
  previews; Reference Style measured caption heights reach the
  render; the editor seeds caption grouping from the clip's style.
- **FIX-46 — device-export parity** (D-P1 `a41d614`, F-P2/F-P3
  `da441ba`, F-P4/F-P5 `eef0332`, D-C1 `0ba8ef1`). Device exports
  pinned to 30 fps with each preset's own audio rate (Shorts
  384 kbps), survive missing B-roll / missing caption font, and
  combine respects each clip's hidden lanes.
- **FIX-47 — Find Moments + Manual honours template/music**
  (`1c1baba`, F-R1) **+ fallback-window stamp** (`cfc2d29`,
  S-1). A manual pick no longer renders through the plain-cut
  path; the starter-clip fallback is stamped when AI failed.
- **FIX-48 — pydantic-ai deprecation fixes** (`235cf5c`,
  `fd3f3e3`). `output_retries=` → `retries={'output': 2}`,
  `usage` read as a property, `openai:` → `openai-chat:`.
  Behaviour-preserving on the locked pydantic-ai 1.107.10.
- **FIX-49 — pristine-database schema fix** (`7959ad7`).
  `init_db()` built tables before running migrations, so a fresh
  install's `tasks.separation_opt_in` column had no server default
  while upgraded installs did — raw inserts omitting it failed.
  One-line fix; installed databases unchanged.
- **FIX-50 — fail-safe validation** (`6793620`, findings F-2/F-4).
  Stored template/music modes are re-validated before rendering
  (warning + documented default on a corrupt value); invalid
  `output_format` / `add_subtitles` on create now return 422
  instead of silently coercing.
- Also in: the NIM bake-off harness + fixtures (`025ffe9`,
  `eafbbe5` — candidates pre-registered UNARMED, no behaviour
  change), the recovery-doc set (`108153f`: FEATURE_PROGRESS.md,
  BUG_AUDIT.md, TEST_RESULTS.md, NEXT_SESSION_PROMPT.txt), the
  model/asset/licence/download registries (`a939750`), and the
  `.env.example` LLM default pointing at the Groq free model
  (`e67d764`). Gate on this exact tip: backend **4,618 passed /
  0 failed**, frontend **892 passed / 0 failed**, eslint 0,
  production `next build` exit 0, migrations 48.

**New known gaps in v6 (honest list):**

- **G-V6-1 — the Cloudflare hop needs two lines in YOUR override.**
  The shipped template passes `CLOUDFLARE_API_KEY` /
  `CLOUDFLARE_ACCOUNT_ID` to the gateway, but your preserved
  `compose.override.yml` may not (same class as G-V5-1, which
  still stands for DeepSeek). Until you add both lines beside the
  other provider keys in the `litellm` environment list, that hop
  stays asleep. Checklist item 33 is the live check
  (`AI_MODEL_ROUTING.md` §5). The keys themselves are already in
  your `.env`.
- **G-V6-2 — a phantom migration ledger row silently suppresses a
  migration (LOW).** The runner trusts `schema_migrations`
  blindly: a row claiming a migration applied, inserted without
  its SQL, makes startup report success while that migration's
  table is missing. Reachability is low (the ledger is written
  only by the runner; it needs manual DB tampering or a
  hand-spliced partial restore), and the supported recovery path
  (rollback.ps1's dump restore) always restores a consistent
  ledger. A post-init sanity probe is a future product fix.
- **G-V6-3 — rendered-video proof still owed for five fixes.**
  F-1 (cache isolation), F-R2 (cache stand-in), F-R1
  (moments-manual template), F-A (reference-look height) and F-B
  (re-render caption carry) are proven at unit + integration
  level only; their rendered-video proof is checklist items
  35–38 and the Nov 15 release-gate acceptance re-run. Nothing
  about them is claimed as rendered-proven here.
- **G-V6-4 — the vision veto's follow-ups are queued, not done.**
  The 100-pair calibration (1 miss, 6 false vetoes) found the
  failures are mostly concept phrasing, not sense confusion:
  planner concept-phrasing guidance, a T=0.000 re-test on the
  combined base, and room-sense avoid lists are queued
  improvements. The veto ships ON by default in Auto as
  calibrated; G-V4-2's "ships OFF, thin calibration" is closed
  by FIX-36 (0 missed wrong-sense inserts in 100 pairs).
- **G-V6-5 — degraded picks have no captions or hook title yet.**
  When the selection floor stands in for a failed AI analysis,
  its exports carry no captions / hook title (no word timings on
  stand-in segments) and no ClipPlan. The task still completes
  honestly stamped; feeding transcript words into floor renders
  is a queued improvement.
- **G-V6-6 — the new degradation-notice component is not in
  verify.ps1's file list.** `degraded-analysis-notice.tsx` (the
  amber banner) is gate-verified but verify.ps1's 51 feature
  checks are a fixed list — it is not one of them. The expected
  verify total stays **166**.
- Everything in the v5 addendum below that was open stays open
  unless FIX-35…FIX-50 above or the checklist addendum closes
  it — in particular G-V5-1 (DeepSeek passthrough) and G-V5-3
  (the ladder's live walk, now checklist items 32–33).

---

## V5 ADDENDUM (2026-11-15) — read this first

This ZIP is the **Consolidated v5 release**: the v4 build below plus
9 commits (tree `next-milestone` @ `357628a`; 132 commits over the
v3 tree `49a134e`). The full record is `reports/V5_FIXES.md`.
Everything in the v4 addendum and the v3 text below is carried
forward as history — FIX-1…FIX-32 all still stand. What v5 adds:

- **FIX-33 — the gateway fallback chains are one ladder ending at
  local** (`86d08a5`, merged @ `3e1ee41`). Before: the premium
  chain ended on a rate-limited free remote, the local model sat
  mid-chain, DeepSeek sat after local, and `groq-20b` had no chain
  at all — a route could dead-end without ever reaching your own
  machine. Now every code-called chain walks the providers in a
  fixed order and ends at `local-qwen`; DeepSeek sits after the
  free routes, before local. The model list is byte-identical, so
  the brain's reasoning-effort allow-list is untouched. No Gemini
  in any chain.
- **FIX-34 — briefs from a screenshot, with hard exclusions**
  (schema `cb237b1`, extraction `d21ce25`, ingestion + enforcement
  `9b91a7e`, UI `ec9733e`; merged @ `357628a`). Brief schema v2
  learns `excluded_ranges` (never-use footage), `flag_points` and
  `key_timestamps`; a screenshot upload is OCR-read on your
  machine into a reviewable draft — nothing is applied until you
  confirm it, and an "avoid" range can never be misread as an
  allowed range. Excluded footage is cut out when moments are
  picked, when the slate is finalised and again at render; a clip
  touching a flag point is stamped needs-review, never silently
  compliant. On your diamonds screenshot the reader recovered all
  25 timecodes, the 5 key moments, the 0:47–0:53 exclusion and
  all 18 flags.

**New known gaps in v5 (honest list):**

- **G-V5-1 — the DeepSeek hop needs one line in YOUR override.**
  The gateway service in your preserved `compose.override.yml`
  already passes the GROQ, NVIDIA and OPENROUTER keys, but not
  `DEEPSEEK_API_KEY`. Until you add
  `- DEEPSEEK_API_KEY=${DEEPSEEK_API_KEY:-}` to its `litellm`
  environment list by hand, a DeepSeek key in `.env` never
  reaches the gateway and that one hop is silently skipped —
  the rest of the ladder works regardless. (Same class as
  G-V4-4; Sanji walks you through it at key time.)
- **G-V5-2 — OCR drafts can carry small reading blemishes.**
  On the diamonds screenshot, one key-moment description was
  truncated where the screenshot's own text wrapped to the next
  line, and two flag notes carried a stray trailing character.
  Both kinds are visible in the draft you review; nothing is
  applied unreviewed, and the times, the exclusion and the flag
  categories were exact.
- **G-V5-3 — the ladder's live walk is unproven on your keys.**
  The order is pinned by the gateway config suite; that it walks
  live when a provider hits its limit proves on your machine
  (checklist item 32). Until then the chains are config-proven,
  not live-proven.
- Everything in the v4 addendum below that was open stays open
  unless FIX-33/FIX-34 above or the checklist addendum closes
  it.

---

## V4 ADDENDUM (2026-11-15)

This ZIP is the **Consolidated v4 release**: the v3 build below plus
123 commits (tree `next-milestone` @ `d3eb750`). The full record is
`reports/V4_FIXES.md`. Everything in the v3 text below is carried
forward as history — Section 1's FIX-1…FIX-25 all still stand.
What v4 adds, in brief:

- **FIX-26 — the three hand patches are in-tree.** The moment-picker
  apostrophe (`b555eca`, plus an eslint gate item), the task-creation
  INSERT duplicate column (`47eda4a`, with a regression test that
  executes the real statement), and the titles patch
  (third-person guardrail + creator-named titles, `50045d6` /
  `c2bce92`, golden re-pin `fca2f7e` proven titles-only).
- **FIX-27 — classification asks the strongest route first**
  (`eae0595`); the local model is the backstop, not the first answer.
- **FIX-28 — captions/title can no longer sit on a face**
  (`54c8b8a`): measured on your Bert frames, text-on-face
  ≈ 49,500 px² (captions) and up to ≈ 54,200 px² (title) → 0.
- **FIX-29 — split-tile source-border slivers trimmed**
  (`be00fd2`), on by default.
- **FIX-30 — separation stems deleted with their task**
  (`532d401`): the ~640 MB/hour-of-source leak is closed.
- **FIX-31 — picker brain upgrade** (`537d55e`): high reasoning
  effort on final passes, gated /100 rubric, source context + scene
  hints + gold few-shot examples, over-ask +3.
- **FIX-32 — default AI is Groq-first, Gemini nowhere**
  (`5d744f1`, merged @ `f363b6f`; docs cleanup `2678ca1`, merged
  @ `d3eb750`). The app's fallback model choice used to resolve
  to Gemini (411 for you) and never checked your Groq key; it
  now returns `groq:openai/gpt-oss-120b` first, local
  `ollama:qwen3:8b` last. Checklist item 29 is the live proof
  (no Gemini model name in the worker log).
- Also in: viral scout pack (signature transitions, transition SFX,
  audio flashes, split proof), channel monitoring Stage 2 (one new
  migration, schema 46 → 47), reference-look Stage 2 (gated font
  suggestion), the SQL safety net (all 123 raw statements executed
  in tests + inventory guard), the selection gold set, and the
  DeepSeek/OpenRouter free gateway fallbacks (inert without keys).

**New known gaps in v4 (honest list):**

- **G-V4-1 — CLOSED in this build.** The first v4 staging
  (tree `e5705bc`) had no compose passthrough for
  `ACTIVE_SPEAKER_TARGETING_ENABLED` / `BORDER_TRIM_ENABLED`.
  The fix (`5a0bb0d`, merged @ `4537b84`, pinned by the compose
  test suite `eab9a99`) is IN this ZIP: the base compose file
  passes both flags to backend + worker, so both switches work
  from `.env` + `docker compose up -d` — and
  `BORDER_TRIM_ENABLED=false` is now a working kill switch.
  `BROLL_VISION_VETO_ENABLED` was never affected.
- **G-V4-2 — the SigLIP vision veto is calibrated but thin.** On
  the planted set it vetoed 6/6 mismatches with 0/5 false vetoes,
  but the score gap is 0.0075 cosine — narrow. It ships OFF by
  default; treat enabling it as an experiment, and judge it by the
  `vision_veto` stamps it leaves in task EDLs.
- **G-V4-3 — the brain's high-effort setting is unproven live.**
  If your gateway build rejects `reasoning_effort`, the call
  degrades to a normal one (a task can never fail because of it) —
  but then the benefit is also absent. Checklist item 25 is the
  live proof.
- **G-V4-4 — gateway keys need a passthrough on YOUR override.**
  The in-tree template carries the `DEEPSEEK_API_KEY` /
  `OPENROUTER_API_KEY` lines for the gateway service; your preserved
  `compose.override.yml` may not. Only matters if/when you add keys.
- **G-V4-5 — denoise is NOT in this build.** The denoise lane's
  report exists in Sanji's research notes only; no denoise code is
  merged into this tree.
- Everything in the v3 Section 2 below that was open stays open
  unless FIX-26…FIX-31 above or the checklist addendum closes it
  — in particular the user-machine proofs (checklist items 1, 24
  and 25 are the headline ones now).

---


**Shipped inside the SnipNova Consolidated v3 ZIP.** This build is
the SnipNova Consolidated v2 release (branch `next-milestone` @
`4eda669`, already installed on your machine) plus the overnight
wave of 2026-11-10/11 (same branch @ `49a134e`; adjudicated full
gate: backend **3,800 passed / 0 failed**, frontend **838 passed /
0 failed** effective). Section 1 lists what this build FIXES —
FIX-1 to FIX-11 came with the Consolidated build; FIX-12 to FIX-17
with v2; FIX-18 to FIX-21 came with the first v3 build; FIX-22 was
added when this ZIP was rebuilt from @ `005e88e`; FIX-23 to FIX-25
came with the second rebuild from @ `49a134e`. Section 2 is
everything still open, carried forward honestly. Nothing here is
invented; the source is named on each item.

**Rebuild note (2026-11-11):** this ZIP was rebuilt from the same
branch @ `005e88e` — the first v3 build's tree (@ `ae7e50d`) plus one
merge: the self-run audit #2 fixes, FIX-22 below. No new migrations;
verify.ps1's expected count is unchanged (140). The parent tree's
full gate stands (backend 3,695/0, frontend 832/0); the merge's own
post-merge checks: classify family 119/0, changelog 4/4.

**Second rebuild note (2026-11-11):** this ZIP was rebuilt again
from the same branch @ `49a134e` — the 005e88e tree plus three
merges: VRAM-lite Stage 0 + build-lite fixes (FIX-23), YouTube
channel monitoring Stage 1 (FIX-24) and Reference Style caption-look
Stage 1 (FIX-25). One new migration
(`20261111_0001_channel_monitoring.sql`); verify.ps1's expected
count is now 147. The full gate re-ran on the exact shipped tip
(backend 3,800/0, frontend 838/0 effective, tsc 11 baseline-
identical, migrations 46, bridge 4/4, smoke 158 routes).



## 1. FIXED in this build

### FIX-1. Auto-niche detection stamped EVERY video "Podcast & news" — FIXED
- **What was wrong:** On your machine, every auto task came out as
  Podcast & news, whatever the video was. You reported it on
  2026-11-10 and your worker log pinned the exact failure: the
  classifier always tried the local model route first, that route
  died at the gateway (HTTP 400, "invalid message content type:
  <nil>"), the gateway had **no fallback configured for that model
  group**, and the classifier had no second candidate — so every
  failure silently became the podcast fallback, indistinguishable
  from a real podcast detection. (Sanji's sandbox could never see
  this: it has no provider keys, and the one test leg that would have
  caught it was the leg that never ran live. That gap is on Sanji,
  and it is why item Q7 below stays open until YOUR machine proves
  the fix.)
- **The fix (four layers, all in this build):** (1) the classifier
  now walks an ordered candidate list — if a route fails at build,
  transport or validation, the next candidate is tried, and the
  failure is recorded against that candidate; (2) a model that
  answers with plain JSON text instead of a tool call is now
  accepted and validated, instead of failing; (3) the gateway config
  gained the missing fallback (`local-qwen` → the assured route,
  which is backed by Groq); (4) when every route fails, title and
  transcript evidence can still rescue the niche for gaming, sports,
  finance, music, comedy and travel — and a genuine podcast is never
  overridden. On top of that, **a failed detection is now visible**:
  the classification result is saved on the task, and a fallback task
  carries the plain warning "Auto niche detection FAILED — default
  Podcast & news profile used". A silent wrong answer is no longer
  possible.
- **Proof so far:** a six-case battery (travel, gaming via your exact
  live 400 error string, a real podcast control, finance, sports,
  music) passes; the fix branch's full suite ran 3,421 passed / 0
  failed; the merged tree's full gate ran 3,468 passed / 0 failed.
- **What is NOT yet proven:** the live leg on your machine, with your
  real keys and gateway. That is validation checklist item 1 — one
  fresh non-podcast clip. See Q7.
- **Source:** `research_notes/sprint-7day/CLASSIFY_CHAIN_AUDIT.md` +
  `CLASSIFY_FIX_REPORT.md`; merge `1291851`.

### FIX-2. Unrelated full-screen stock B-roll (the highway cars) — FIXED
- **What was wrong:** A trading clip got ~4 seconds of full-screen
  highway traffic because the B-roll planner searched Pixabay for the
  single word "car" and took the first hit with **no relevance check
  of any kind**, placed over unrelated speech. The fallback podcast
  stamp (FIX-1) is also what armed stock B-roll on those videos at
  all.
- **The fix:** placements are anchored to when the word is actually
  spoken; the trigger vocabulary was extended past the old ~20 nouns
  (food/travel words like avocado and sushi can now trigger relevant
  cutaways; "car" no longer fires inside "careful"); and every
  Pixabay hit is gated by the relevance checker (hit tags + query +
  spoken context) — the first *passing* hit wins, and if none pass,
  the cue is skipped entirely.
- **Proof:** render-level frame proof on Sanji's machine — stock
  lands exactly inside its anchored window and nowhere else.
- **Source:** `research_notes/sprint-7day/SELF_RUN_AUDIT.md` (issues
  #1/#2/#7) + `research_notes/selfrun-fixes/report.md`; commit
  `4d3472d`, merge `a43b2e4`.

### FIX-3. Music Auto silently rendered no bed — FIXED
- **What was wrong:** Music Auto looked for a library track under
  one specific mood name ("calm"). If no track resolved under that
  name, the clip rendered with no music at all — the only trace was
  a warning line in the worker log. Music **On** on a profile with
  no bed of its own (podcast, comedy) was equally silent.
- **The fix:** Auto now falls back through other suitable moods and
  then to the library itself; it is silent only when the library is
  genuinely empty. On without a pick takes the same selection.
- **Proof:** measured render pair — bed present at −35.4 dB in speech
  gaps (ducked 23.7 dB under speech) versus a −120 dB silent control.
- **Source:** `SELF_RUN_AUDIT.md` R3 + selfrun-fixes report; commit
  `46f8eb8`, merge `a43b2e4`.

### FIX-4. Clip scores flattened to one number — FIXED
- **What was wrong:** The reranker could compress a whole slate to a
  single identical score (five clips all showing 86 although the
  passes scored them 92/90/88/86/84), which destroyed the TOP PICK
  signal.
- **The fix:** reranked scores stay distinct and strictly ordered
  (a score is never raised, and ties/inversions step down by one).
  A duplicated window-summary text artefact went with it.
- **Source:** `SELF_RUN_AUDIT.md` R5; commits `618a23f` + `d3d2743` +
  `66597ed`, merge `903b461`.

### FIX-5. An apostrophe in an info card killed the whole render — FIXED
- **What was wrong:** Info-card text was escaped for one parsing
  level while the render graph is parsed twice: any card containing
  an apostrophe aborted the entire clip render (FFmpeg exit 234),
  and a long card (138 characters) ran off both edges of the frame.
  Found by the golden-frame render tests after the Final build
  froze — the bug is live in the Final build on your machine until
  this build replaces it.
- **The fix:** card text is never inlined into the filter graph any
  more — it travels in a textfile sidecar (with `%` escaped, the one
  character drawtext still expands), wraps at 34 characters, max 4
  lines, and sidecar names are deterministic. Proven at frame level:
  the apostrophe card is **visible** in its window and the 138-char
  card sits inside the frame with margin. (A later cleanup also
  fixed a rare crash when a render step received a plain-text file
  path instead of a path object.)
- **Source:** OpenCode golden-frame findings (his machine) +
  `OPENCODE_AUDIT.md` + `DRAWTEXT_FIX_REPORT.md`; commits `152c16b`,
  `3c9b2dc`, `c382d16`, `2f3a3c8`; merges `5f6215f`, `8eeeb46`.

### FIX-6. Watch-folder: a dropped file could get stuck forever — FIXED
- **What was wrong (F-D1):** if the watcher died in the narrow window
  between claiming a dropped file and creating its task, the claim
  row stayed `processing` forever — no duplicate, no data loss, but
  that file version was never autopiloted and nothing ever retried
  it. (The Final build's delivery carried a workaround note for
  this: re-copy the file. That workaround is **no longer needed**
  and is not part of this build's instructions.)
- **The fix:** every watcher scan now sweeps stale claims — a claim
  whose source is unchanged is re-driven; one whose source changed
  is parked with a reason. Watch-folder suite 15/15.
- **Source:** `DURABILITY_DRILL.md` (drill D1a, reproduced on the
  frozen tree) + fix commit `94f66d2`.

### FIX-7. Brand Kit intro/outro videos now render — FIXED
- **What was wrong:** the Final build's item B1 — kits saved your
  intro/outro picks but never joined them to clips.
- **The fix:** bumpers are stitched onto exports, normalised to the
  clip's frame size and −14 LUFS loudness; a missing bumper file
  drops only that side; any bumper failure leaves the plain render
  untouched.
- **Proof:** real renders — a 12 s clip + 2 s intro + 2 s outro
  exports at ≈16 s, frames verified at the bumper positions;
  brand-kit suites 52/52.
- **Source:** commit `f342810`. (Still open under Brand Kit: B2, B4,
  B5 below.)

### FIX-8. Re-render caption template — investigated, NOT a defect
- The Final build's item B3 said re-rendered clips lose the Brand
  Kit's caption template. Re-investigation on 2026-11-10 found the
  override is in fact wired into **both** render pipelines and
  already had render-level proof — the gap was stale. No code change
  was made, and B3 is retired. If you ever see a re-render whose
  captions ignore the kit, send Sanji the clip — that would be a new
  bug, not this one.
- **Source:** Wave-2 investigation note, 2026-11-10.

### FIX-9. You can now upload your own sound effects — ADDED
- The Final build let you upload music but not SFX (the bundled SFX
  folder is read-only in Docker and there was no upload path). The
  Studio SFX panel now has an upload button mirroring music: your
  effects are listed with a "Your effects" tag, can be placed like
  bundled ones, and are mixed into exports (proven with a measured
  render: a user-only 900 Hz effect audible exactly in its placed
  window).
- **Source:** commit `1817d50`.

### FIX-10. Three deferred performance items — BUILT
- **F-P1 (pre-stage stitch was CPU-only):** the crossfade pre-stage
  now uses NVENC when the GPU encoder probes healthy, CPU-identical
  otherwise, with a one-time automatic fallback if NVENC errors. The
  speed gain itself can only be measured on your card — see P3.
- **F-P2 (bundle full-copies):** the deliverable bundle now hardlinks
  finished clips instead of copying them (≈170 MB saved per 10-clip
  task), falling back to a copy where hardlinks are impossible.
- **F-P3 (no per-clip timings):** renders now record per-clip,
  per-pass timings (pre-stage, B-roll resolve, encode, bumpers,
  total) into the task's existing stage timings.
- **Source:** `PERF_SANITY.md` + commits `ec5cefc`, `ebabe13`
  (merge), `d1914d2` (merge).

### FIX-11. Non-English UI showed raw key text; one frontend test failed — FIXED
- The Final build's item T1: 302 English labels (298 + 4 SFX) were
  missing from all five non-English catalogues, so those UIs showed
  raw keys like `home.music.auto`. All 302 are backfilled per locale
  (interim English values; real translations are a future backlog
  item). The frontend gate on this build is **802 passed / 0
  failed** — the "known i18n failure" is gone, and the backend
  suite's 21 adjudicated sandbox failures are also retired by the
  test-hygiene pack (this build's full gate: 3,468 passed / 0
  failed). English was always complete; your own usage was never
  affected.
- **Source:** `I18N_ROOTCAUSE.md` + `TEST_HYGIENE_SPEC.md` +
  `BASELINE_ROOTCAUSE.md`; commits `6d57c69`, `789ccd1` (merge).



### FIX-12. A framed shot could put a speaker's face inside the blurred band — FIXED
- **What was wrong:** In the self-run audit, one shot of the Japan
  test video fell back to the blurred-pad layout with the speaker's
  face sitting inside the top blurred band (section 2, item Q10).
- **The fix:** The layout planner now refuses any composition that
  would place a grounded face inside a blurred pad band. The check
  is judged on the face boxes actually observed across the shot —
  an early version judged on the median box, which understated the
  face's true extent; that was caught and fixed before this build.
- **Proof so far:** On the same Japan fixture, the defective shot's
  subject coverage went 17.28% → 18.36%; the other four test ranges
  are unchanged (one moved 0.26 points, inside the documented
  measurement-variance class). The after-frame was inspected by eye:
  split layout, both faces fully sharp, no face in any blur band.
  Post-merge reframe suites: 74 passed / 0 failed.
- **Source:** `research_notes/face-in-blur/report.md`; merge
  `bad2c81`.

### FIX-13. Reference Style label showed a coin-flip as a confident verdict — FIXED
- **What was wrong:** The family name shown for a Reference Style is
  a pacing match, not the app understanding your video — but
  near-ties were displayed as confident verdicts, with no margin
  shown anywhere (section 2, item R1). Measured on real cases: one
  winner led by 0.08 points; another was an exact tie.
- **The fix:** The app now works out the winner's margin over the
  runner-up and shows it. A clear win reads "Built on the … style";
  a close call (margin under 0.25) reads "Closest pacing match: …
  — nearly tied with …". The matching itself is unchanged — the
  label is honest now, nothing more is claimed.
- **Proof so far:** Both audit cases were reproduced before the fix
  (margins 0.0789 and exactly 0.0); post-merge reference-style
  suites: 48 passed / 0 failed.
- **Source:** `research_notes/sprint-7day/REF_LABEL_FIX_REPORT.md`;
  merge `38909ee`.

### FIX-14. Clip covers were grabbed blind — now picked by score — IMPROVED
- **What was wrong:** A finished clip's cover image (the poster in
  its ready-to-post bundle) was one frame grabbed at a fixed moment
  — often a near-empty frame with the speakers tiny in a strip
  between blurred bands, or a shot with nobody in it.
- **The fix:** Covers are now picked by scored frame selection:
  frames are sampled across the clip (about one per second, at most
  24, plus the old fixed frame), and ranked — a frame with a face
  beats one without, sharper beats blurrier, and near-black or
  near-white frames can never win. A sampled frame must be clearly
  better to replace the old one, and if anything at all goes wrong,
  the old fixed frame is used exactly as before, so a cover can
  never fail or hold up a bundle. Brand Kit title colours and logos
  are applied to the chosen frame exactly as before.
- **Proof so far:** On the Japan test clips (identical inputs, old
  code vs new): cover face area 2.2% → 14.2% on one clip and 1.6%
  → 17.2% on another; a cover that had no face at all was replaced
  by a face frame. Post-merge suites: 34 passed / 0 failed.
- **Source:** `research_notes/thumbnail-intelligence/report.md`;
  merge `5e98c9f`.

### FIX-15. Find Moments tasks waited forever for a pick — FIXED
- **What was wrong:** You found this live on 2026-11-10: a Find
  Moments task sat at "waiting for your pick" with 19 candidate
  moments for ~20 minutes — and it would have waited forever. If
  you are away from the computer, the generation simply never
  continued.
- **The fix:** A checker now runs every minute. If no pick is made
  within 15 minutes, the task continues by itself with the
  top-ranked moments, and the task says so plainly: "Auto-picked
  the top moments — no selection was made within 15 minutes."
  A pick you make yourself always wins, even one made at the last
  second. Tasks that were already parked before this build are
  rescued the same way after you install it. The two Find Moments
  cards on the Create page and the waiting screen now state the
  deadline up front. (The wait length is
  `TASK_SELECTION_AUTOPICK_MINUTES`, default 15.) Full AutoClip
  never waited and is unchanged.
- **Proof so far:** Post-merge suites — backend 75 passed / 0
  failed, frontend 20 passed / 0 failed — including races between
  your pick and the timer in both directions. Live proof on your
  machine is validation checklist item 12.
- **Source:** `research_notes/parkfix/report.md`; merge `de4d2a8`.

### FIX-16. Studio editor: a second visual lane (Overlays) — ADDED
- **What it is:** The studio editor had exactly one visual lane
  (B-roll). It now has an Overlays lane above B-roll: upload your
  own image (logo, sticker, screenshot — PNG, JPEG or WebP, up to
  8 MB), place it at any time with its own start and end, position
  and size. Exports composite in the order: base video, then
  B-roll, then overlays, then captions.
- **Safety:** Old projects are untouched — with no overlays placed,
  an export runs through exactly the same render commands as before
  (proven byte-identical against the previous build).
- **Proof so far:** A real test render: the overlay appears above
  the B-roll inside its placed window and is absent outside it
  (pixel-checked at four timestamps; the frame was inspected by
  eye). Post-merge backend editor suites: 102 passed / 0 failed;
  frontend: 22 passed / 0 failed.
- **Source:** `research_notes/multitrack/report-stage-b.md`; merge
  `0bb58f8`.

### FIX-17. Studio editor: hide and lock controls per lane — ADDED
- **What it is:** Every studio timeline lane (video, B-roll,
  overlays, captions, hook, audio) now has hide/show and lock
  controls. Hiding a lane leaves its content out of the export —
  nothing is deleted, and showing the lane brings it all back.
  Locking a lane stops its items being moved or edited until you
  unlock it.
- **Safety:** With every lane visible and unlocked, exports are
  identical to before (proven byte-identical).
- **Proof so far:** Post-merge backend: 105 passed / 0 failed;
  frontend: 19 passed / 0 failed.
- **Source:** `research_notes/multitrack/report-stage-c.md`; merge
  `4eda669`.

### FIX-18. "Plain — just cuts" template mode, now the create-page default — ADDED
- **What it is:** The Create page now has a Template switch, and
  **Plain — just cuts** is picked by default. In Plain mode the app
  makes no B-roll, adds no music, no SFX, no info cards and no
  transitions — just cuts, silence tightening, captions and the hook
  title — and the framing is always a full-frame tight crop, so the
  speaker fills the screen. The old Auto template is still there if
  you pick it; nothing about it changed. Brand Kit is also off
  under Plain (kit dressing is the inserted-graphic class this mode
  exists to remove); your own per-clip editor choices still work.
- **Proof so far:** Measured on real renders, not just tests: face
  width on a talking-head clip 13.28% → **34.00%** of the frame;
  Japan fixture r3 19.81% → **38.83%**; the blurred-band metric
  (auto 0.10–0.28) reads 1.08–2.22 under Plain — the bands are gone
  by construction. Plain audio matches a source-only render at
  0.9998 correlation. Post-merge backend 39 passed / 0 failed,
  frontend 9 passed / 0 failed.
- **What is NOT yet proven:** the live leg on your machine — one
  default (Plain) task on your "Train For Diamond Gym" clip. See
  validation checklist item 16.
- **Source:** `research_notes/plain-mode/report.md`; merge `1d0d615`.

### FIX-19. Voice/music separation, Stages 1 and 2 — ADDED (off by default)
- **What it is:** The app can split a video's voice from its
  original music (Demucs `htdemucs`). Stage 1 feeds the clean
  vocals to transcription, so loud music stops putting wrong words
  into captions and moment picks. Stage 2 lets an opted-in render
  strip the original music out of the clip. Both are **off by
  default** and switched on per task inside the app — your normal
  tasks are untouched. To switch the feature on at all, add
  `SEPARATION_ENABLED=true` to `.env`, and the opt-in itself is a
  per-task setting (there is no separation switch on the Create page
  yet — the live check, checklist item 17, is done with Sanji on
  chat). The separation model weights (~84 MB) are never bundled in
  the ZIP — they download once, on your machine, on first enabled
  use. If separation ever fails, the task falls back to the original
  audio and carries on.
- **Proof so far:** On a real speech-over-music source, the vocals
  stem cut the music by **54.1 dB** while losing no speech anywhere.
  Stage 1 and Stage 2 suites are green; the full gate on the merged
  tree ran 3,695 passed / 0 failed.
- **What is NOT yet proven:** everything about your 6 GB card —
  separation timing and VRAM peak, and the first-use download in
  the real install. See validation checklist item 17.
- **Source:** `research_notes/wave4-separation/SCOPING.md` +
  `STAGE1_REPORT.md` + `STAGE2_REPORT.md`; merges `6384d81`,
  `ae7e50d`.

### FIX-20. The B-roll word-sense failure ("train" = railway bridge) — FIXED
- **What was wrong:** Your "Train For Diamond Gym" clip got a
  full-screen scenic railway-bridge video because the B-roll planner
  matched the word "train" without its meaning: the exercise verb
  sat in the travel-noun trigger list, the search query was the bare
  word, and the relevance gate could not veto a hit whose own tags
  contain "train".
- **The fix:** The planner now judges each occurrence in its
  sentence (±4 words). "Train" next to "gym" plans no vehicle
  B-roll; a genuine railway sentence still gets its cue, and the
  search now carries the context ("train station"). The same
  treatment covers "race" and "pool". Plain mode makes this moot
  anyway (no B-roll at all), but Auto-template users get the fix
  too.
- **Proof so far:** New suite 16 passed / 0 failed (the gym case
  produces no vehicle B-roll; the genuine railway case keeps its
  cue, asserted at the actual Pixabay request); neighbours 287
  passed / 0 failed; no existing test was changed. Post-merge
  targeted re-run 88 passed / 0 failed.
- **What is NOT yet proven:** a live Pixabay leg on your machine —
  the sandbox has no Pixabay key. See validation checklist item 18.
- **Source:** `research_notes/plain-mode/broll-sense-report.md`;
  merge `6b272d0`.

### FIX-21. Brand Kit: frame-relative watermark + branded hook previews — FIXED
- **What was wrong:** (1) The watermark logo was sized at 16% of
  the **logo file's** width — a 2000 px logo rendered at 320 px on
  screen, a 200 px logo at 32 px. (2) Hook-variant previews carried
  no brand at all, so what you previewed was not what you exported.
- **The fix:** The watermark is now 16% of the **frame** width at
  both render sites (clip and cover), and hook previews are stamped
  with the same brand blocks as the full render and the re-render —
  title band/accent, watermark, kit caption treatment — from the
  same resolver. No schema change; no new migration.
- **Proof so far:** Same clip, same kit, logo stored at 200 px and
  at 2000 px wide — the rendered watermark measures **[874, 1776,
  1045, 1861] for BOTH**, 172 px wide (= 0.16 × 1080, within 1 px of
  the 172.8 ideal), identical position. No-kit control: 0 logo
  pixels. Hook-preview frame parity proven against the full render.
- **What is NOT yet proven:** the live leg on your machine — one
  branded task with your own logo. See validation checklist item 19.
- **Source:** `research_notes/brand-kit/gaps-report.md`; merge
  `abcde5c`.



### FIX-22. Hindi videos fell back to "Podcast & news" when classification failed — FIXED
- **What was wrong:** The classifier's failure path had backup
  scorers for gaming, sports, finance, music and comedy — but
  none for Hindi. So when the AI classification call failed on a
  Hindi video, nothing could rescue it and it silently took the
  Podcast & news profile and its English-style treatment, even
  though the app has a dedicated Hindi podcast profile with
  romanized-Hinglish captions. The same audit also found the
  renderer's B-roll record (`broll_applied`) could say False on
  the EDL path when B-roll did render, quietly dropping a clip's
  saved B-roll placements on re-render.
- **The fix:** A language scorer now sits on the same failure-path
  rescue. If at least 30% of the transcript's letters are
  Devanagari script — the same threshold the caption romanizer
  itself uses — Hindi scores decisively and wins, even over the
  podcast lockout (a Hindi podcast's correct profile IS the Hindi
  one). Romanized Hinglish words (kya, kyunki, matlab, doston…)
  also count as evidence, matched as whole words so 'hai' inside
  'Thai' or 'aur' inside 'restaurant' can never score — but they
  can never override the podcast lockout on their own. And
  `broll_applied` is now computed with the renderer's own
  participation rule, so it tells the truth on the EDL path too.
- **Proof so far:** Both audit fixtures (podcast-titled Devanagari
  talk, share 0.90; Devanagari business talk, share 1.00) now
  rescue to hindi_podcast; the English podcast control is
  unchanged; 12 new Hindi tests + 9 B-roll-record tests; the
  audit's own 7-case driver passes 7/7 against the new code;
  classify family after the merge 119 passed / 0 failed.
- **What is NOT yet proven:** the live leg on your machine — a
  Hindi video at a moment when classification fails. See
  validation checklist item 20.
- **Source:** `research_notes/selfrun-audit-2/hindi-fix-report.md`
  (shipped in this ZIP as `reports/HINDI_FIX.md`); commits
  `1736134`, `9d4d70c`, merge `005e88e`.



### FIX-23. The app's own AI models crowded each other out of the 6 GB card — FIXED (build-lite)
- **What was wrong:** On a computer whose graphics card has little
  memory to spare (the app is built for a 6 GB laptop card), a
  generation could fail at the transcription step, or slow to a
  crawl, when the app's own AI models got in each other's way inside
  that memory. The speech-recognition model used to stay loaded in
  graphics memory long after transcribing finished — including
  while the local AI model was trying to load for category detection
  — and the voice/music separation tool would attempt the graphics
  card even when there was plainly no room, wasting minutes before
  giving up.
- **The fix:** The speech model is now released whenever the next
  step needs the room and at the end of every generation; the app
  asks the local AI runtime to unload its model before transcription
  starts and after a detection it served (a polite request to a
  separate program — if it refuses, nothing breaks); separation
  checks the free graphics memory first and goes straight to the
  processor when the card is nearly full, and retries once on the
  processor if a graphics attempt still fails; and if transcription
  itself runs out of graphics memory, it retries on the processor
  instead of failing the generation. On a machine with plenty of
  graphics memory, nothing changes. The app also now writes how much
  graphics memory is free at each step of a generation into the
  worker log, and the benchmark pack gained a VRAM scenario that
  walks a small-memory laptop through exactly the crowded case and
  reports those numbers.
- **Proof so far:** 34 new tests (telemetry format, eviction counts,
  unload handshakes, the separation device matrix, the CUDA-OOM
  retry paths); neighbouring suites 413 passed / 1 skipped / 0
  failed. All VRAM figures in the sandbox were stubbed by design
  — the numbers only become real on your card.
- **What is NOT yet proven:** any of it on your RTX 4050. See
  validation checklist item 22 (the benchmark pack's VRAM scenario).
  The full adaptive VRAM manager — the app changing its own
  concurrency from measured memory pressure — is still NOT built
  (tracker 7.7).
- **Source:** `research_notes/wave4-vram/STAGE1_REPORT.md`
  (shipped in this ZIP as `reports/VRAM_STAGE1.md`); merge `4327791`.



### FIX-24. YouTube channel monitoring — NEW in this build (Stage 1)
- **What it is:** A "watch folder, but for a channel" on the
  Autopilot settings page. Add a channel by its URL or @handle and
  every new upload becomes a task on its own, with per-channel
  defaults you choose — style, Plain or Auto template, creation
  mode, clips per video, music, and a daily task limit. Videos
  already on the channel when you add it are only marked as seen
  and are never clipped, unless you explicitly tick "Also clip the
  newest video now". Shorts are left out unless you include them, a
  video longer than your plan allows is listed as skipped with the
  reason instead of failing, and everything the watcher sees — queued, skipped, or given up on after retries — is listed in
  a channel activity feed with plain reasons. Channels are checked
  about every 15 minutes, which is how often YouTube refreshes its
  feeds. Detection uses YouTube's public feeds, so no account
  sign-in is needed for this stage.
- **Proof so far:** 32 new backend tests (feed parsing 11, monitor
  behaviour 21: baseline creates zero tasks, a new video makes
  exactly one task, a second poll makes no duplicate, caps enforced,
  over-duration skipped with no task, failure retried then parked
  after 3 attempts) + 8 new frontend tests; neighbouring suites
  green; the new migration replays cleanly. From Sanji's sandbox a
  live check resolved `@MrBeast` to its channel and fetched its real
  feed (15 entries, the Shorts flag correct on real data).
- **What is NOT yet proven:** adding a real channel on your machine
  and watching a new upload become a task. See validation checklist
  item 21. The account-authorised workflow from the original
  requirement is still future work.
- **Source:** `research_notes/wave4-channel-monitoring/STAGE1_REPORT.md`
  (shipped in this ZIP as `reports/CHANNEL_MONITORING_STAGE1.md`);
  merge `58b5a19`.



### FIX-25. Reference Styles now read the reference's caption look — NEW in this build (Stage 1)
- **What it is:** Reference Styles used to read only a reference
  video's pacing. They now also read its caption appearance: where
  the captions sit, the main text colour, the highlight colour, how
  many words sit on a line, whether the captions are ALL CAPS,
  whether the letters have an outline (and roughly how thick),
  whether there is a drop shadow, whether the captions sit inside a
  dark box, and roughly how big the text is. Those measured values
  are applied to clips made with that style — your Brand Kit
  still wins where it sets a value, and the measured look wins over
  the stock caption preset the style would otherwise use. A
  reference with no readable captions produces a style exactly as
  before, and captions written in a non-Latin script still give
  their position, colours, outline, box and size — only their
  words per line and casing cannot be read yet.
- **Proof so far:** A planted-fixture suite (18 tests): fixtures
  rendered with the app's own caption renderer, measured back
  against what was planted — colours within tolerance, position
  within 0.03, casing exact; neighbours 48 + 132 green.
- **What is NOT yet proven:** a style-picked generate on your
  machine from a reference with distinctive captions. See
  validation checklist item 23. Reading the FONT NAME itself is
  still not built (tracker 4.8).
- **Source:** `research_notes/wave4-reference-look/STAGE1_REPORT.md`
  (shipped in this ZIP as `reports/REFERENCE_LOOK_STAGE1.md`);
  merge `49a134e`.



## 2. Still open in this build

## Auto-niche — the remaining leg

### Q7. The classifier fix has not yet run live on your machine — USER-MACHINE PENDING
- **What it means:** FIX-1 above is proven by a stubbed battery, the
  branch suite and the merged full gate — but Sanji's sandbox has no
  provider keys, so no run anywhere has yet used your real gateway
  and keys. The deciding check is one fresh clip from a clearly
  non-podcast video on your machine: it must come out with its real
  niche (gaming, travel, finance…), and the task must carry its
  classification record. If it ever falls back again, the task now
  **says so on its face** — screenshot that warning and send it to
  Sanji; it names the failure instead of hiding it.
- **Severity:** annoyance. **Tag: USER-MACHINE PENDING.**
- **Source:** FIX-1 sources; validation checklist item 1.

## Brand Kit (remaining)

### B2. Logo size depends on your logo file, not on the video frame — FIXED in this build (v3), see FIX-21
- **What it means:** The watermark logo was shown at 16% of the logo file's own width. If you uploaded a small logo file, the watermark looked small. If you uploaded a big logo file, it looked big.
- **Severity:** was workaround-exists.
- **Fixed in this build (v3):** The watermark is now 16% of the frame width at both render sites — the same logo at 200 px or 2000 px wide renders at the same 172 px on screen. See FIX-21 in section 1.
- **Source:** `research_notes/brand-kit/report.md` (Honest gaps); `research_notes/brand-kit/gaps-report.md`.

### B4. Hook preview clips do not show your brand — FIXED in this build (v3), see FIX-21
- **What it means:** The short hook-variant previews did not carry the kit's logo, colours or music. Only the final exported clip was branded.
- **Severity:** was annoyance.
- **Fixed in this build (v3):** Hook previews are now stamped with the same brand blocks as the full render — what you preview is what you export. See FIX-21 in section 1.
- **Source:** `research_notes/brand-kit/report.md` (§3 Exports); `research_notes/brand-kit/gaps-report.md`.

### B5. Picking the "Default" caption template plus a kit uses the kit's template
- **What it means:** If you pick the template literally named "Default" **and** also pick a Brand Kit that has a template, the kit's template wins. The app cannot tell "I really want Default" apart from "I did not pick anything".
- **Severity:** annoyance.
- **Workaround:** Pick any non-Default template by name when you want to override the kit.
- **Future fix:** None scheduled; it is written-down behaviour, not a bug.
- **Source:** `research_notes/brand-kit/report.md` (Honest gaps — template fill sentinel).

## Four creation modes

### M1. "Find Moments + Manual" clips have no background music from the app
- **What it means:** In the Manual mode (AI editing off), the render is plain on purpose — so the app's music bed is not added, and even a music track you picked does not apply (music travels through the AI editing plan, which is off in this mode). The mode's own description in the app says this.
- **Severity:** workaround-exists.
- **Workaround:** Add music in the editor afterwards — that is also what this mode is for.
- **Future fix:** A music option for the plain render path is a future-milestone job.
- **Source:** `research_notes/creation-modes/report.md` (Known limitations (a); FUTURE MILESTONE).

### M2. A task waiting for your moment picks cannot be cancelled
- **What it means:** A Find-Moments task parks in "waiting for your pick". The Cancel button does not work for a parked task.
- **Severity:** workaround-exists.
- **Workaround:** Delete the task instead — deleting works.
- **Future fix:** Cancel for parked tasks is a future-milestone job.
- **Update in this build (v2):** A parked task no longer waits
  forever — after 15 minutes it continues by itself with the
  top-ranked picks and says so on the task (FIX-15). The Cancel
  button itself still does not act on a parked task; deleting
  remains the manual way out before the timeout fires.
- **Source:** `research_notes/creation-modes/report.md` (Known limitations (c); FUTURE MILESTONE).

### M3. Modes do not yet have their own default styles or usage stats
- **What it means:** Each mode does not come with its own saved default settings (for example a special caption preset just for Manual Cutter), and the app does not yet count how often each mode is picked. The mode differences themselves (what the AI does, what you pick, what renders) are real and tested.
- **Severity:** feature-partial.
- **Workaround:** Set the options you want on the Create page each time.
- **Future fix:** Per-mode default profiles and mode analytics are future-milestone jobs.
- **Source:** `research_notes/creation-modes/report.md` (FUTURE MILESTONE).

## Speed curves (velocity)

### V1. Football edits use the Bullet speed curve; other types use none yet
- **What it means:** The fast/slow speed-curve system (Bullet, Jump Cut and the others) is built, wired in, and proven on real renders. At integration the first switch-on was DECIDED: **Football edit clips use the Bullet curve** (build, slow into the key moment, snap back out). Every other type still edits exactly as before — no curve is forced on them.
- **Severity:** annoyance (information only).
- **Workaround:** None needed.
- **Future fix:** Switching curves on for more types is a future-milestone choice, made per type with proof.
- **Source:** `research_notes/residuals/report.md` (Piece 1); RC profile `backend/src/auto_director/profiles/football_edit.json` (`velocity_curve: "bullet"`); CHANGELOG.md 2026-11-10 entry.

## Reference Styles

### R1. The style label could name the wrong type of video — FIXED in this build (v2), see FIX-13
- **What it means:** When you create a Reference Style, the app shows which built-in style family it was built from. That name is picked by measuring speed of cuts and look — it is **not** the app understanding what the video is about. The self-run audit measured how fragile it is at slow pace: the top two families can sit ~0.08 apart in score, small bonuses decide the winner, and **no margin or confidence is shown anywhere** — a coin-flip is displayed as a confident genre verdict. A slow, calm gaming/funk reference was labelled "Japan travel cinematic" for exactly this reason. The style itself still copies the real measured pace and look of your reference.
- **Severity:** annoyance.
- **Workaround:** Judge the style by the clips it makes, not by the family name next to it.
- **Fixed in this build (v2):** The margin is now computed and shown, and close calls are labelled as close — see FIX-13 in section 1. (What remains open is the deeper item R2 below: the app still cannot read a reference video's actual words or font.)
- **Source:** `research_notes/fix-style-niche/report.md` (§3); `research_notes/sprint-7day/SELF_RUN_AUDIT.md` R4 (measured margins).

### R2. The app cannot read the words or the font inside a reference video
- **What it means:** The analyser measures cut speed, colours, where captions sit and how fast they move. It does not read the actual caption words in your reference, and it cannot name the font the reference used.
- **Severity:** feature-partial.
- **Workaround:** Pick the font and template yourself on the Create page to match your reference by eye.
- **Future fix:** Deferred under the scope freeze (tracker row 4.8, kept with its original requirement).
- **Source:** `TRACKER.md` row 4.8; `research_notes/fix-style-niche/report.md` (§3, dissolve/fade cuts can also be undercounted).

## Quality engine — small honest limits

### Q1. Scenes with 3 or more faces stay wide
- **What it means:** When 3+ people are in the frame together, the app keeps the whole group in shot instead of zooming on one face. Faces look smaller in those moments. This is a choice, not a failure — zooming would cut people out.
- **Severity:** annoyance.
- **Source:** `research_notes/quality-milestone/report.md` (composition residuals).

### Q2. Food close-ups with no face stay in the wide fit view
- **What it means:** For shots with no person in them (for example a close-up of a dish), the app has no "find the interesting object" detector, so those shots stay in the fit view instead of a zoomed crop.
- **Severity:** feature-partial.
- **Future fix:** An object/saliency detector is future work; not in this release.
- **Source:** `research_notes/quality-milestone/report.md` (composition residuals).

### Q3. Very short fit shots can flicker between views on busy montage parts
- **What it means:** On the most cut-heavy ranges, a very short (half-second to 1.5-second) fit-view insert can sit between two zoomed shots, so the view jumps a little.
- **Severity:** annoyance.
- **Future fix:** A "keep one view for a minimum time" smoothing pass is the named follow-up; not in this release.
- **Source:** `research_notes/quality-milestone/report.md` (composition residuals — minimum-hold smoothing follow-up).

### Q4. Captions can show a full line slightly before its last word is spoken
- **What it means:** Captions are placed line by line. A whole line can appear up to about 1.2 seconds before its last word is actually said.
- **Severity:** annoyance.
- **Source:** `research_notes/quality-milestone/report.md` (captions residuals — line-at-a-time design, documented).

### Q5. Captions inside speed-up/slow-down parts can drift a little
- **What it means:** In clips where the speed is ramped (sped up or slowed down), caption timing can be off by the ramp amount. The sound and picture themselves stay in sync (measured 0 ms on the proof exports) — this is only about caption timing inside ramped parts.
- **Severity:** annoyance.
- **Future fix:** Still open; it is written down as a known limit, not closed.
- **Source:** `research_notes/quality-milestone/report.md` (rows QM5 / 1.4).

### Q6. On a very tight zoom, the title's second line can touch the top of the hair
- **What it means:** In the tightest single-person crop, the pinned title's second line can slightly overlap the top of the speaker's hair. It never covers the face, and the title stays readable.
- **Severity:** annoyance.
- **Source:** `research_notes/quality-milestone/report.md` (pinned-title frame check).

### Q8. Score reasons show on the clip wall, not yet on the Publishing board
- **What it means:** Each clip's quality scores come with plain reasons for every score. Those reasons show on the clip wall. The Publishing board does not receive them yet.
- **Severity:** annoyance.
- **Future fix:** Small follow-up to pass the field to the board.
- **Source:** `research_notes/quality-milestone/report.md` (row QM6 residual).

### Q9. Four caption templates still use a font with a licence note
- **What it means:** FOUR caption templates — "mrbeast", "neon", "large_centre_pop" and "bold_uppercase" — still use THEBOLDFONT because that slanted heavy look is the point of those templates. It is kept on purpose, with a licence risk noted. Hindi/Devanagari text is covered by the bundled Poppins font instead of a bundled Noto font.
- **Severity:** annoyance.
- **Source:** `research_notes/quality-milestone/report.md` (D13 residuals); template count corrected 2026-11-10 by combo proof Q-A against the RC tree's `caption_templates.py`.

### Q10. A framed shot could put a face inside the blurred band — FIXED in this build (v2), see FIX-12
- **What it means:** In the self-run audit, one shot of the Japan fixture fell back to the blurred-pad layout with the speaker's face sitting inside the top blurred band (the split-screen layout handled the neighbouring shots correctly). It is one shot shape, not every clip — but a face should never be inside the blur.
- **Severity:** annoyance.
- **Fixed in this build (v2):** The composition guard now refuses that layout — see FIX-12 in section 1.
- **Source:** `research_notes/sprint-7day/SELF_RUN_AUDIT.md` R1 (issue #5), frame `a1_t10.png`.

## Campaign briefs (Requirements)

### C1. "Show the product/game on screen" can only ever be marked "uncertain"
- **What it means:** Some brief rules are about what is **seen** in the video (for example "the game must be shown"). The app cannot prove a picture the way it can prove a word in the transcript. So those rules are judged by the AI model and can hold a clip at "uncertain — review", never a full pass. The app says this openly and never dresses it up as proof.
- **Severity:** feature-partial (by design — the honest limit of the method).
- **Workaround:** Eyeball the "uncertain" clips yourself before posting.
- **Source:** `research_notes/content-requirements/report.md` (row 2.7); `TRACKER.md` row 2.7.

### C2. A clip that passes only SOME must-have rules still renders — but is flagged and blocked — PENDING DECISION
- **What it means:** If a clip passes one must-have rule of a brief but fails another, the app still makes the clip, marks it "Brief: non-compliant" in red, and blocks it from the ready-to-post board. It is never quietly called compliant. Whether such clips should instead not be made at all is an open product choice (the change is one line, if you decide that way).
- **Severity:** workaround-exists (the flag + board block protect you). **Tag: PENDING DECISION (yours).**
- **Source:** `research_notes/content-requirements/report.md` (§Tier B + §7 open question).

## Watch-Folder Autopilot (remaining)

### W1. One setup step on your machine before the watch folder can work
- **What it means:** The app runs inside Docker, so Docker must be told where your `D:\Jarvis_Autopilot` folder is. The installer offers this as an optional step (step 6b): say yes and it adds the read-only folder link for `D:\Jarvis_Autopilot` plus `WATCH_FOLDER_PATH=/autopilot` for you; say no (or skip the manual lines the installer prints if its automatic edit cannot be made) and the watch folder cannot see dropped files. Everything else about the watcher (no duplicates after restart, half-copied files ignored, retries, pause switch, your original files never moved or deleted — and, new in this build, orphan-claim recovery, FIX-6) is built and tested.
- **Severity:** workaround-exists (the guide step). **Tag: USER-MACHINE PENDING.**
- **Source:** `research_notes/watch-folder/report.md` (USER VALIDATION PENDING #1).

### W2. Folder stages are shown in the app, not as real sub-folders
- **What it means:** The watcher does not move your file through INBOX / PROCESSING / COMPLETED folders. Your file stays exactly where you dropped it, always. The stage of each file is tracked inside the app and shown on the Watch-folder page instead. This is deliberate — moving your originals would be a risk.
- **Severity:** annoyance (by design).
- **Source:** `TRACKER.md` row 6.2; `research_notes/watch-folder/report.md`.

## Speed, voice and hardware numbers

### P1. There are no speed numbers for your RTX 4050 yet — USER-MACHINE PENDING
- **What it means:** The benchmark pack is built and ready (it measures GPU use, VRAM, RAM, CPU, time per step, and output quality on one fixed test video — and this build's per-clip timings, FIX-10, give it more to read). But it has never been run on your laptop, so **no speed numbers exist**. Anyone quoting you a number before that run is guessing.
- **Severity:** annoyance. **Tag: USER-MACHINE PENDING.**
- **Workaround:** Run the pack once when you are back — it is on your validation checklist.
- **Source:** `research_notes/benchmark-pack/report.md` (Status).

### P2. Voiceover speed on your graphics card is unmeasured — USER-MACHINE PENDING
- **What it means:** The Kokoro voiceover works, but it was only timed on Sanji's slow server CPU. How fast it is on your RTX 4050 is unknown. Voiceover stays **off** unless you turn it on, and the first time it downloads a model of about 313 MB.
- **Severity:** annoyance. **Tag: USER-MACHINE PENDING.**
- **Source:** `TRACKER.md` row 5.5; `research_notes/benchmark-pack/report.md`.

### P3. GPU speed paths are on but their gains are unproven — USER-MACHINE PENDING
- **What it means:** Two separate things. (a) Fast video-reading (NVDEC) is off by default and its on/off speed test on your card has not been run. (b) NEW in this build: the crossfade pre-stage stitch uses NVENC when your card is healthy (FIX-10) — the correctness gate passed remotely (same join anchors, CPU-identical fallback), but the actual speed A/B on the Japan fixture can only be measured on your machine, via the benchmark pack.
- **Severity:** annoyance. **Tag: USER-MACHINE PENDING.**
- **Source:** `TRACKER.md` row 7.6; `PERF_SANITY.md` F-P1 + fix merge `ebabe13`.

### P4. A fresh install rejects videos longer than 90 minutes — your machine is NOT affected
- **What it means:** The app ships with a source-length limit of 5400 seconds (90 minutes). Sources longer than 90 minutes are rejected unless the `.env` file sets a higher `MAX_VIDEO_DURATION` (a 2-hour video needs 14400). **Your existing `.env` already carries 14400, and the installer copies your `.env` back unchanged — so on your machine nothing changes.** This only bites if you ever reset `.env` to the shipped example: then long videos stop working until the value is raised again.
- **Severity:** workaround-exists (raise `MAX_VIDEO_DURATION` in `.env`). **Tag: tracker row 7.1.**
- **Source:** release hardening check (`backend/src/config.py` default 5400; `.env.example` comment added at freeze commit `b4e2a18`).

### P5. Database proof is SQLite-grade; Postgres atomicity is by design — note
- **What it means:** The migration sequence and the watch-folder dedupe uniqueness were rehearsed on a scratch SQLite database (42/42 applied, idempotent, uniqueness enforced) because Sanji's server has no Postgres. The Postgres-specific atomicity (the dedupe insert's ON CONFLICT behaviour) is correct by construction in the SQL and runs on your machine's Postgres — it has never been exercised under a real Postgres race. Your installs apply the migrations on real Postgres every time (verify.ps1 checks all 43 applied), which is the practical proof for everything except the race itself.
- **Severity:** none observed. **Tag: by-design.**
- **Source:** `migration-rehearsal.md`; classify migration `20261110_0001_task_classification.sql` follows the same runner discipline.

## The old failed task on your machine

### F1. Why 3 clips failed inside that one old task is still unknown — USER-MACHINE PENDING
- **What it means:** Your task from Nov 9 (`aa2fdc74…`) made some clips, then the whole task showed "Failed". The app-side cause of that wrong "Failed" label is found and fixed — now, if at least one clip is made, the task completes and honestly lists what is missing. But **why those specific clips failed to render on your machine** is still unknown; only your worker logs can say. The checklist covers pulling those logs with Sanji on chat.
- **Severity:** workaround-exists (the honest-completion fix ships in this release line).
- **Source:** `research_notes/partial-failure/report.md`; sprint log, Nov 9.

## Editor

### E1. SFX placement is proven through edit plans; the Studio-panel path is narrower
- **What it means:** Browsing the SFX library, placing an effect, setting its volume and hearing it in the export are built and proven through the edit-plan path. New in this build: uploading your OWN effects works in the Studio panel (FIX-9). The separate document-Studio path for SFX placement is still not built.
- **Severity:** feature-partial.
- **Source:** `research_notes/editor-gaps/report.md` (Tracker deltas — 5.3 note); commit `1817d50`.

## Publishing — one honest note

### PB1. Two publishing paths exist; the board never auto-posts
- **What it means:** The publishing **board** in this app is state-only by design — it tracks ready-to-post clips and never posts anything by itself. Separately, the base project (inherited, unchanged since Milestone 2) contains a scheduled-publishing path that fires **only** for posts you yourself created and scheduled, to accounts you connected with your own logins. Both facts are stated so the "never auto-posts" shorthand is never misunderstood. An optional global kill-switch for the scheduled path exists as a future option — it needs your decision before anyone builds it, because it changes behaviour you might use.
- **Severity:** none (information). **Tag: PENDING DECISION (yours), optional.**
- **Source:** `SECURITY_PASS.md` finding F-S1; `TRACKER.md` row 6.7.

## SnipNova name change — two small leftovers

### N1. Legal pages still show the old stock company name
- **What it means:** The Terms and Privacy pages still show the name that came with the original open-source project ("Shiori LLC"). Nobody invented a new legal name, because that is your decision, not ours.
- **Severity:** annoyance. **Tag: PENDING DECISION (yours).**
- **Source:** `research_notes/rebrand/report.md` (Judgement calls #1).

### N2. Emails from the app still say "SupoClip"
- **What it means:** The app itself is fully renamed to SnipNova, but the subject/body text of the app's own emails still uses the old name. They are code, not settings, so they were left for a later pass.
- **Severity:** annoyance.
- **Future fix:** A later rename pass over backend email text.
- **Source:** `research_notes/rebrand/report.md` (§Judgement calls / leftovers).

## B-roll (remaining)

### BR1. Two B-roll systems still coexist
- **What it means:** The app still has two B-roll paths (an older LLM-planned one used by the plain render path, and the EDL stock one). This build fixed the EDL path's relevance, anchoring and re-render loss (FIX-2 + the re-render carry fix), but the two systems were not merged into one. Also, with no Pixabay key set, stock B-roll quietly does nothing.
- **Severity:** feature-partial (consolidation only).
- **Workaround:** Add a free Pixabay key if you want stock B-roll at all.
- **Source:** `TRACKER.md` row 5.6; `SELF_RUN_AUDIT.md` R2.

## Cancelling

### X1. Cancel only stops a task at safe checkpoints
- **What it means:** Pressing cancel does not kill a render mid-frame; the task stops at its next safe checkpoint. A task whose worker died can also keep showing "processing" until the worker next starts and sweeps it.
- **Severity:** annoyance.
- **Source:** `TRACKER.md` row 7.3.

## Not in this release at all (deferred on purpose)

These were **deferred to a future milestone** by the scope freeze and
remain deferred in this build, except where a first stage is now built
(noted below). Each stays in `TRACKER.md` with its original
requirement:

- **Adaptive VRAM manager** (auto-tuning rendering to your 6 GB card) — still deferred (tracker 7.7); its Stage 0 telemetry + build-lite fixes shipped in this rebuild, see FIX-23.
- **Reading fonts/caption words from reference videos** — tracker 4.8 (see R2 above).
- **Rest of the viral scout pack** — one-signature-transition rule, Flash In/Out, transition→SFX map, audio-reactive flashes, zoom cutouts — tracker 5.9 (only the speed curves were built; see V1).
- **Deeper multitrack editor work** — tracker 3.1 (a design was delivered; the build is future work).

Channel monitoring left this list in this rebuild: Stage 1 (RSS watcher + Settings UI) is built — see FIX-24 and tracker 6.4. The account-authorised workflow is still future work.

**Source:** scope freeze, `SPRINT_LOG.md` 2026-11-09 ~20:37 IST; `TRACKER.md` rows as named.

## One test-only note (not a product defect)

### T1. One test fails only when run alone — `test_range_seek.py`
- **What it means:** The test `test_multi_range_render_seeks_to_the_first_kept_moment` passes inside the full suite (it passed in this build's gate) but fails when run **alone** on this VM, with `ValueError: '-ss' is not in list`. The cause is probe-state sensitivity, pre-existing and not merge-caused: when the NVENC probe has not yet run in the process, its probe call lands first in the test's captured command list, and that call carries no `-ss`. In the full-suite order an earlier test has already triggered (and cached) the probe, so the test passes. The Stage 2 lane reproduced the same standalone failure identically on a pristine worktree of the pre-separation base.
- **Severity:** none for the app — it is a test-ordering artefact in the sandbox; it cannot occur in the shipped build and it changes no render.
- **Source:** `research_notes/sprint-7day/NEXT_GATE_AE7E50D.md` §7; `research_notes/wave4-separation/STAGE2_REPORT.md`.
