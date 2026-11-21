# V3 Fixes — the overnight wave of 2026-11-10/11 (branch `next-milestone` @ `ae7e50d`)

This build is the Consolidated v2 build (`4eda669`) plus four merged
items, all built, reviewed and merged by the main agent on the night
of 2026-11-10/11, while Luffy was away for his exam. Full gate on the
merged tree: backend **3,695 passed / 0 failed / 3 skipped**,
frontend **832 passed / 0 failed** effective, `tsc --noEmit` 11 errors
byte-identical to baseline, migration safety PASS over the 45-file
set, Reference Styles bridge 4/4 (`reports/NEXT_GATE_AE7E50D.md`).

**Rebuild (2026-11-11):** this ZIP was rebuilt from the same branch
@ `005e88e36973282f8ba426b780eb72ba4ff54535` — the tree above plus ONE
merge, section 5 below (the self-run audit #2 fixes, KNOWN_ISSUES
FIX-22). The first v3 ZIP (built from `ae7e50d`) is superseded by
this rebuild; its SHA-256 no longer applies to the ZIP Sanji sends
you. Nothing else changed: no new migrations, verify.ps1's
expected count is unchanged at 140.

**Second rebuild (2026-11-11):** this ZIP was rebuilt again from
the same branch @ `49a134ead5fe7cb53ee2cbb85b778ea0e5255276` — the
005e88e tree plus THREE merges, sections 6–8 below (VRAM-lite
FIX-23, channel monitoring Stage 1 FIX-24, reference caption-look
Stage 1 FIX-25). The 005e88e-based ZIP is superseded by this
rebuild; its SHA-256 no longer applies to the ZIP Sanji sends you.
One new migration (`20261111_0001_channel_monitoring.sql`);
verify.ps1's expected count is now 147.

## 1. "Plain — just cuts" template mode — merge `1d0d615`

- **Branch:** `feature-plain-mode` (11 commits, tip `3f643b0`), off `4eda669`.
- **What:** A per-task `template_mode` (migration
  `20261110_0002_task_template_mode.sql`; NULL = legacy Auto, so old
  tasks and watch-folder tasks render byte-identical plans) with a
  Template switch on the Create page. **Plain — just cuts is the
  default.** In Plain mode: no B-roll, no added music (exact Music
  Off EDL shape), no auto SFX, no info cards, no transitions (hard
  cuts only), no speed ramps, zooms, LUT or beat grid. Kept: moment
  selection, cuts, silence tightening, captions, hook title. Brand
  Kit is fully off under Plain (kit dressing is the inserted-graphic
  class this mode exists to remove); the kit and the task's
  `brand_kit_id` are untouched and return in full under Auto
  template. Re-render and hook-preview paths rebuild through the
  plainified builder, so a corrective re-render cannot re-add a bed.
  While Plain is picked, the music group, music picker and B-roll
  chip are disabled on the Create page.
- **Reframing:** `build_plain_reframe_plan` — every shot is one
  full-bleed crop with its own window (dominant face ~40% of window
  width; group region ~85% fill when faces fit one region; saliency
  ladder when faceless). Never fit, never split.
- **Proof (real renders, Japan fixture + derived talking head):**
  face width talking head 13.28% → **34.00%**, Japan r3 19.81% →
  **38.83%**; blurred-band metric auto 0.10–0.28 vs plain 1.08–2.22;
  audio correlation **0.9998** vs a source-only control. The r3
  frame pair was visually verified by the main agent (auto = speaker
  strip with the face blurred into the top band; plain = tight
  full-frame). EDL parity: template None vs "auto" EDLs are identical
  dicts. Lane suites: backend full 3,602 passed (1 own billing-
  fixture failure fixed, assertions untouched; 5 `test_edl_av_sync`
  errors proven identical on pristine base — sandbox OOM), frontend
  830/0, tsc 11 = 11. Post-merge on the merged tree: backend 39/0,
  frontend 9/0.
- **Source:** `research_notes/plain-mode/report.md` (frames in
  `research_notes/plain-mode/frames/`).

## 2. B-roll word-sense fix — merge `6b272d0`

- **Branch:** `fix-broll-word-sense` (5 commits, tip `f7a0426`), off `4eda669`.
- **What:** The "Train For Diamond Gym" clip got a scenic railway-
  bridge stock video because the planner matched the word "train"
  without its meaning. Three links, all fixed: (1) "train" sat in
  the travel-noun trigger vocabulary with word-level matching, so
  the exercise verb matched the travel noun — a `WORD_SENSES`
  table now covers the genuinely ambiguous triggers (train
  railway/exercise, race competition/humanity, pool
  swimming/billiards); (2) the query was the bare noun — placements
  now stamp a context-carrying `search_query` ("train station");
  (3) the Nov 10 relevance gate could not veto a hit whose tags
  contain the trigger word itself — `footage_sense_mismatch` now
  vetoes a hit whose own tags clearly indicate a different sense
  than the spoken context. Occurrences are judged one by one in a
  ±4-word window.
- **Proof:** New suite 16/16 — the gym case produces NO vehicle
  B-roll, the genuine railway case keeps its cue with `q=train
  station` asserted at the actual Pixabay request; neighbours 287
  passed / 0 failed; no existing test was changed. Post-merge
  targeted re-run on the merged tree: 88 passed / 0 failed.
- **Source:** `research_notes/plain-mode/broll-sense-report.md`.

## 3. Voice/music separation, Stages 1 + 2 — merges `6384d81`, `ae7e50d`

- **Branches:** `feature-separation-stage1` (Stage 1, off `4eda669`),
  Stage 2 (off the Stage 1 tip).
- **What:** Demucs `htdemucs` splits a source's voice from its
  original music. Stage 1 feeds the vocals stem to transcription,
  so loud music stops putting wrong words into captions, moment
  picks and classification. Stage 2 lets an opted-in render strip
  the original music out of the clip. Both are **default OFF**:
  `SEPARATION_ENABLED` env gate (off unless `=true` in `.env`) +
  per-task opt-in. The opt-in is a task field on the API — the
  Create page has no separation switch yet. Weights (~84 MB) are
  never bundled or committed; they download once, on the user's
  machine, on first enabled use. Stems are cached by source hash as
  FLAC sidecars. Every failure falls back to the original audio —
  separation can never fail a task.
- **Proof:** On a real speech-over-music source, music attenuation
  in the vocals stem **54.1 dB** while losing no speech anywhere
  (Stage 1 build threshold ≥ 20 dB). Stage 1 and Stage 2 suites
  green; full gate on the merged tree 3,695/0. The decisive
  hallucination comparison (a music-heavy source with labelled
  music-only stretches) is a Stage-3 user-validation item.
- **Licence note (disclosed):** the Demucs weights licence is
  explicitly unclear (the author's README says so; MUSDB data is
  research-only). Mitigations shipped with the feature: weights are
  never bundled or redistributed (runtime download on the user's
  machine), a credits/disclosure note ships with it, and the
  `htdemucs_6s` model (author-flagged likely CC-NC) is excluded.
  MDX-Net is the documented fallback behind the same seam.
- **Source:** `research_notes/wave4-separation/SCOPING.md`,
  `STAGE1_REPORT.md`, `STAGE2_REPORT.md`.

## 4. Brand Kit gaps — merge `abcde5c`

- **Branch:** `feature-brandkit-gaps` (off `1d0d615`).
- **What:** The two remaining Brand Kit gaps from the Nov 9 report:
  (1) the watermark was sized at 16% of the **logo file's** width —
  now 16% of the **frame** width at both render sites (clip graph
  and cover graph), with no schema change; (2) hook-variant
  previews carried no brand — they are now stamped with the same
  brand blocks (title band/accent, watermark, kit caption
  treatment) from the same resolver as the full render and the
  re-render.
- **Proof:** Same clip, same kit, logo stored at 200 px and at 2000
  px wide — the rendered watermark measures **[874, 1776, 1045,
  1861] for BOTH**, 172 px wide (= 0.16 × 1080, within 1 px of the
  172.8 ideal), identical position. No-kit control: 0 logo pixels.
  Hook-preview frame parity proven against the full render.
  Post-merge brand-kit suites green; changelog test 4/4.
- **Source:** `research_notes/brand-kit/gaps-report.md`.

## 5. Hindi guardrail scorer + broll_applied wiring — merge `005e88e` (KNOWN_ISSUES FIX-22)

- **Branch:** `fix-hindi-guardrail` (commits `1736134` classify,
  `9d4d70c` render), off `ae7e50d`; merged by main as `005e88e`.
- **What (A2-1):** Self-run audit #2 found the classifier's
  failure-path guardrails had NO `hindi_podcast` scorer — when the
  LLM path failed, Hindi content always fell back to podcast_news
  (gaming 14–0, music 13–1 and finance 18–1 rescued fine). The fix
  adds a language scorer inside the existing
  `_evidence_guardrail_rescue`: Devanagari letter share ≥ 0.30 —
  the threshold imported from the caption romanizer
  (`media.hinglish.DEVANAGARI_SHARE_THRESHOLD`, never re-typed) —
  scores a decisive 6 and may pass the podcast lockout on the
  language signal alone, because a Hindi podcast's correct profile
  IS hindi_podcast. Romanized-Hinglish markers (29 precision-chosen
  function words) are word-boundary matched — 'hai' inside
  'Thai'/'Shanghai', 'aur' inside 'restaurant'/'dinosaur' can never
  score — compete as ordinary keyword evidence and NEVER
  override the lockout.
- **What (A2-3):** `layout_report.broll_applied` was blind on the
  EDL path, so EDL-path clips' saved B-roll placements could be
  silently dropped on re-render. New predicate
  `edl_broll_will_composite` in `media/edl_render.py` mirrors the
  renderer's exact participation rule (dict entry, existing
  `local_path`, positive window); `create_optimized_clip` now
  reports the flag truthfully.
- **Proof:** 12 new Hindi tests (both audit fixtures rescued —
  shares 0.90 and 1.00; romanized rescue when unlocked; English
  podcast control unchanged; markers under the lockout do NOT
  rescue, by design) + 9 A2-3 tests incl. real-FFmpeg compositing
  (>80% of frame pixels inside the window, <5% outside). The
  audit's own 7-case driver passes 7/7 against the new code. Lane
  families: classify 91/0, B-roll 103/0, EDL 157/0, A/V sync 11/0.
  Post-merge verification by main on `005e88e`: classify family
  119/0, changelog 4/4.
- **Deliberate boundary:** a romanized (Latin-script) Hindi
  podcast that also trips the podcast lockout stays podcast_news —
  precision over reach, per the audit's own design.
- **Source:** `research_notes/selfrun-audit-2/hindi-fix-report.md`
  (shipped in this ZIP as `reports/HINDI_FIX.md`).

## 6. VRAM-lite Stage 0 + build-lite fixes — merge `4327791` (KNOWN_ISSUES FIX-23)

- **Branch:** `feature-vram-lite` (7 commits, tip `c3f0a4f`), off `005e88e`.
- **What:** Stage 0 telemetry (`media/vram.py`): free VRAM is logged
  at every pipeline stage boundary (separation, transcription,
  classification) into the worker log. Build-lite fixes on top of
  it: the resident faster-whisper model is evicted at stage seams
  (including before local classification, where qwen3:8b needs
  ~5.5 GB on the 6 GB card); the Ollama `keep_alive:0` unload
  handshake is asked at seams (before transcription, after a
  detection it served — a polite request to a separate program, and
  nothing breaks if it refuses); Demucs checks free VRAM first and
  goes straight to the CPU when the card is nearly full, with one
  CPU retry if a card attempt still fails; a transcription CUDA-OOM
  retries on the CPU instead of failing the generation. On a roomy
  card, nothing changes.
- **Proof:** 34 new tests (`test_vram_stage1.py`: telemetry format,
  eviction counts, unload handshakes, the Demucs device matrix, the
  CUDA-OOM retry paths); neighbours 413 passed / 1 skipped / 0
  failed; changelog 4/4. All VRAM figures in the sandbox were
  stubbed by design — the numbers only become real on the 4050
  (checklist item 22). The benchmark pack gained a VRAM scenario
  script (`tools/benchmark-pack/vram-scenario.ps1`) that walks the
  crowded case and reports the numbers.
- **Deliberate boundary:** the full adaptive VRAM manager (the app
  changing its own concurrency/settings from measured memory
  pressure) is still NOT built — tracker 7.7 stays DEFERRED-FUTURE.
- **Source:** `research_notes/wave4-vram/STAGE1_REPORT.md`
  (shipped in this ZIP as `reports/VRAM_STAGE1.md`).

## 7. YouTube channel monitoring Stage 1 — merge `58b5a19` (KNOWN_ISSUES FIX-24)

- **Branch:** `feature-channel-monitoring` (off `4327791`).
- **What:** RSS channel watcher end to end: add a channel by URL or
  @handle (resolution mirrors the metadata path's proxy-tolerant
  yt-dlp options); baseline-on-add (existing videos marked seen,
  never clipped, unless `Also clip the newest video now` is ticked);
  per-channel defaults (style, Plain/Auto template, creation mode,
  clips per video, music, daily task limit); Shorts left out unless
  included; over-duration videos listed as skipped with the reason;
  claim-first dedupe so each new video becomes exactly one task;
  failure backoff then park after 3 attempts; stale claims recovered
  on the next sweep; activity feed with plain reasons; ~15-minute
  poll cadence (YouTube's feed refresh rate); Settings UI section
  with BFF proxies. New migration
  `20261111_0001_channel_monitoring.sql` (settings, monitors, video
  claims). Detection uses public RSS feeds — no account sign-in is
  needed for this stage.
- **Proof:** Backend 32 new tests (`test_youtube_feed.py` 11 +
  `test_channel_monitor.py` 21: baseline creates zero tasks, a new
  video makes exactly one task, second poll makes no duplicate,
  caps enforced, over-duration skipped with no task, Shorts
  filtered, failure retried then parked) + frontend 8 new tests
  (BFF proxies + section component); neighbours backend 82 /
  frontend 17 green; migration replays cleanly under the runner's
  semicolon split; changelog 4/4; tsc 11 baseline-identical.
  **Live leg from the sandbox:** `resolve_channel("@MrBeast")`
  resolved to `UCX6OQ3DkcsbYNE6H8uQQuVA` and a live RSS fetch
  returned exactly 15 entries, with the Shorts link-form flag
  correct on real data.
- **Deliberate boundary:** the account-authorised workflow from the
  original requirement is still future work; `include_shorts_feed`
  is a documented no-op in Stage 1 (the UULF/UUSH playlist feeds
  are a Stage-2 item).
- **Source:** `research_notes/wave4-channel-monitoring/STAGE1_REPORT.md`
  (shipped in this ZIP as `reports/CHANNEL_MONITORING_STAGE1.md`).

## 8. Reference Style caption-look Stage 1 — merge `49a134e` (KNOWN_ISSUES FIX-25)

- **Branch:** `feature-reference-look` (commits `8b7a8ac` look pass,
  `20ee5c8` render merge, `649c01f` planted-fixture suite, `3052672`
  changelog), off `58b5a19`; merged by main as `49a134e`.
- **What:** The analyser's sparse hi-res pass measures the
  reference's caption look — position, main text colour, highlight
  colour, words per line, ALL-CAPS casing, outline presence and
  rough thickness, drop shadow, dark box, rough text size — and the
  measured look is applied to clips made with that style under the
  Brand Kit at render (Brand Kit wins where it sets a value; the
  measured look wins over the stock caption preset). A reference
  with no readable captions produces a style exactly as before;
  non-Latin-script captions still give position, colours, outline,
  box and size — only words per line and casing cannot be read yet.
- **Proof:** Planted-fixture suite 18/18 end to end through
  `analyse_reference` (fixtures rendered with the app's own caption
  renderer, measured back against what was planted: colours within
  tolerance, position within 0.03, casing exact); neighbours
  `test_reference_style.py` + routes 48 green, brand-kit/EDL/
  template/user-profiles batch 132 green; changelog 4/4.
- **Deliberate boundary:** reading the FONT NAME is still not built
  — tracker 4.8 stays DEFERRED-FUTURE.
- **Source:** `research_notes/wave4-reference-look/STAGE1_REPORT.md`
  (shipped in this ZIP as `reports/REFERENCE_LOOK_STAGE1.md`).

## Gate this build ships on

`next-milestone` @ `ae7e50d` — `reports/NEXT_GATE_AE7E50D.md`:
backend **3,695 passed / 0 failed / 3 skipped** (3,698 collected,
4 serial chunks); frontend **832 passed / 0 failed** effective (830
in the full run + 2 PGlite integration tests solo-proven PASS; 12
skipped are the same environment-bound PGlite suites as the last two
gates); `tsc --noEmit` 11 errors byte-identical to baseline;
migration safety PASS (45-file set, zero semicolon-in-comment
violations); bridge 4/4 byte-identical to `61ad303`; smoke
`create_app()` OK, 153 routes / 129 distinct paths. One test-only
note: `test_range_seek` fails only when run standalone
(probe-state sensitivity, pre-existing, reproduced identically on a
pristine pre-separation base) — KNOWN_ISSUES T1; not a regression.

**Rebuild gate note:** the shipped ZIP is the merge `005e88e` on top
of this gated tree. The merge changed 6 files (classifier, EDL
renderer, video utils, CHANGELOG, 2 test files — no migrations, no
new feature paths). Post-merge verification by main: classify
family 119 passed / 0 failed, changelog 4/4.

**Second-rebuild gate note:** the shipped ZIP is the three merges
`4327791` + `58b5a19` + `49a134e` on top of the 005e88e tree. The
full gate re-ran on that exact tip (`reports/NEXT_GATE_49A134E.md`):
backend 3,800 passed / 0 failed / 3 skipped, frontend 838 passed /
0 failed effective, tsc 11 baseline-identical, migration safety PASS
over the 46-file set, bridge 4/4 byte-identical to `61ad303`, smoke
`create_app()` OK, 158 routes / 132 distinct paths.

## Deliberately NOT in this build

- **Self-run audit #2 findings** (Nov 11, on `ae7e50d`): the two
  buildable findings — the missing `hindi_podcast` guardrail
  scorer (A2-1) and the blind `broll_applied` flag (A2-3) — are
  FIXED in this rebuild: see section 5. The remaining finding,
  A2-2 (profile caption position unwired), is an open design
  call, not a defect fix.
- **Free-API radar research** (5 videos Luffy shared Nov 11 ~00:53
  IST): research only, no code in this build.
- **F-S1 kill-switch** for the inherited scheduled-publishing path:
  needs Luffy's decision before anyone builds it.
- **Auto-niche live proof**: still user-machine pending (checklist
  item 1) — row 1.7 of TRACKER.md is untouched in this re-score.
- **No Create-page separation switch yet** (API task field only);
  the live separation check (item 17) is done with Sanji on chat.
- **The adaptive VRAM manager is still NOT built** — only Stage 0
  telemetry + build-lite fixes shipped (FIX-23); measurement-driven
  concurrency changes are future work (tracker 7.7). Channel
  monitoring Stage 1 uses public RSS feeds — no account
  authorisation is needed for detection; the account-authorised
  workflow from the original requirement is still future work.

## Five states

- **IMPLEMENTED:** yes — package REBUILT from the merged tree @
  `49a134e`, all four overnight merges plus the audit-#2 merge,
  VRAM-lite, channel monitoring Stage 1 and reference-look Stage 1
  inside, docs/scripts/reports complete.
- **INDIVIDUALLY TESTED:** yes — assertion gate 38/0/0 exit 0;
  SHA-256 double-computed and identical; tree-vs-archive byte diff
  clean; verify arithmetic recounted from the script itself; guide
  grep-checked (zero nested `powershell -c "`, install command
  carries `-ZipPath` + `-ExpectedSha256`).
- **INTEGRATED:** yes — the tree is the gate-adjudicated
  `49a134e` tree (backend 3,800/0, frontend 838/0 effective,
  migrations 46, bridge 4/4, smoke 158 routes).
- **CROSS-FEATURE VERIFIED:** inherits the merged tree's gate +
  adjudication; the package itself adds no code.
- **USER-MACHINE VALIDATION PENDING:** install + verify (expected
  **147 passed, 0 failed**) and checklist items 1, 16–23 on Luffy's
  machine, after his upload approval.
