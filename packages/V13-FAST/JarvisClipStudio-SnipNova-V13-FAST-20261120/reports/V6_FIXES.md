# V6 Fixes — SnipNova Consolidated Release v6 (2026-11-15)

**Tree:** branch `next-milestone` @ `7959ad7` (state commit
`6669028`). **Delta over v5:** 151 commits (v5 tip `357628a` →
`7959ad7`), one new migration (`20261112_0001_task_broll_enabled.sql`;
schema 47 → 48). **Gate on the exact shipped tip** (report in
Sanji's research notes, `INTEGRATED_GATE_7959AD7.md`): backend
**4,618 passed / 0 failed**, frontend **892 passed / 0 failed**,
eslint 0, production `next build` exit 0, migrations 48, bridge
4/4, smoke 162 routes / 136 paths. Zero new failures vs the
9ba6602 gate. The protected fallback tag `v6-candidate-90f1cb4`
(`90f1cb41b083`) is untouched by this package.

The v5 record (`reports/V5_FIXES.md`: FIX-33 gateway ladder,
FIX-34 brief-from-screenshot) and everything before it still
stand. This file records only what v6 adds. Numbering continues
from FIX-34.

## FIX-35 — Task settings now actually reach the pipeline (merge `ef87820`)

The read-back defect. `template_mode`, `music_mode`,
`separation_opt_in` and `source_duration_seconds` were written at
creation but dropped by both task serializers, so every "Plain —
just cuts" task on v3/v4/v5 silently rendered the full Auto
template (profile music bed, template transitions/effects, stock
B-roll), Music Off was ignored, and separation never ran. This is
the root cause of the 2026-11-10 complaint that "Plain" renders
came out wide/letterboxed with template inserts. Fix:
serializer-only, +14 lines in `task_repository.py` (both
`get_task_by_id` and `get_user_tasks`). Independently verified on
a live PostgreSQL round-trip; 26 settings traced across 9 stages
(22 proven end-to-end, 3 absent-by-design, 1 proven-with-caveat).
Old tasks heal on re-render / regenerate. Full record:
`SETTINGS_INTEGRITY_AUDIT.md` in Sanji's research notes.

## FIX-36 — Smart B-Roll v2 complete (Slices 1–6)

- S1 word-sense table (`dc48e5f`): "market" and the creator trap
  set get sense entries; the cue-context window runs to cue end.
- S2 concept-writing (`c2362d4`): the planner writes a filmable
  concept + sense + avoid list before anything searches.
- S3 resolver v2 (`c2753b7`): concept phrases in order, tag gate
  v2, Pixabay photos + Ken Burns stills, Coverr same phrases.
- S4 vision veto calibration (`c9c9bf9`): concept subject +
  contrastive avoid; **ON by default in Auto** (Plain exempt).
  44-pair set: 0 missed wrong-sense, 2 false vetoes. Expanded
  later to 100 pairs: 1 miss, 6 false vetoes — zero TJR-class
  wrong-domain misses in 100 pairs.
- S5 densities + profiles (`90ee61e`): per-profile densities;
  **finance B-roll ON at 1.5/min**; placement guards.
- S6 editor review surface (`d0d8990`): concept + thumbnail per
  insert, Remove / Next candidate.
- Coverr second stock source + persistent stock library with
  licence manifest (`a0c6901`); per-task B-roll ON/OFF switch
  (`bc2f230`, migration `20261112_0001`).

## FIX-37 — Niche classification fixes

- Vlog formats rescued out of the podcast attractor (`dc6c766`):
  a "Week in the Life" transcript with zero trading vocabulary
  now classifies vlog_lifestyle.
- Finance dialect + evidence rule (`3024cea`): day-trading
  monologues classify finance_business; a genuine finance
  podcast stays podcast.
- Evidence window widened (`6942209`): guardrails sample the head
  plus one window at each quartile instead of the opening minutes
  only.
- Hindi guardrail (`005e88e`): Devanagari-share ≥ 0.30 rescues to
  hindi_podcast.

## FIX-38 — Framing fix + hook-title blur band

- Framing (`5159b1d`): face tracks beat the fit-blur exemptions;
  one layout per clip (no crop/fit mixing); captions strip-aware.
  Measured on the Japan fixture: face-in-strip 15.1% → 42.9%
  full-bleed, caption out of the blur band.
- Hook title (`07ba446`): the title builder is wired with
  strip-derived band boxes, so titles stay out of blurred bands.

## FIX-39 — Provider ladder v2 + Cloudflare hop

- Ladder v2 (`5395c0a`): one strength ranking for every chain —
  deepseek-chat → Groq gpt-oss-120b → NIM Ultra → NIM DeepSeek
  Flash → OpenRouter free → Cloudflare free → Groq 20B → Ollama
  last. The old paid Luna default is gone (free Nemotron slug).
- Cloudflare hop (`7f04289`): `cloudflare-free` →
  `cloudflare/@cf/openai/gpt-oss-120b`, inserted into all 15
  chains after openrouter-free. Free-only by construction (single
  fixed slug; paid-plan fragments banned by tests). Failure drill
  6/6 on the real LiteLLM Router. Full doc:
  `AI_MODEL_ROUTING.md` in the tree. Live endpoint check is
  checklist item 33 (your override needs the two CLOUDFLARE
  passthrough lines — G-V6-1).

## FIX-40 — Eco render mode (`9e6e750`)

`RENDER_ECO_MODE`, off by default: effective concurrency 1,
bounded ffmpeg threads, lowered priority. Scheduling only — the
picture-quality constants are byte-untouched and pinned by test.
For when rendering used to pin CPU/RAM at ~100%.

## FIX-41 — Caption position wiring (`820171d`)

Caption Studio Top / Middle / Bottom and words-per-line were
validated, stored and stamped into the EDL — and nothing read
them. They now reach the render (proven on real exports: Top
0.155, Middle 0.496, Bottom 0.755; no-request renders
byte-identical to before). Closes audit item A2-2.

## FIX-42 — Editor export honesty, D3–D7 (`da910b6`)

The 2026-11-13 editor dead-UI audit found 7 defects; all fixed
with exported-file proof and the audit re-run at 69/69 working:
device exports draw overlays (D3); TikTok / Shorts / Reels
presets really differ — Shorts follows YouTube's bitrate table
(D4); combine keeps each clip's framing, grade, volume and
overlays (D5); Brand Kit copy corrected — bumpers do stitch (D6);
hidden captions/audio lanes honoured in exports (D7).

## FIX-43 — Selection floor + honest degradation (`9ba6602`, notice `9c546cd`)

- The deterministic no-AI selection floor now ranks peaks by
  local prominence with decay-gated anchors: gold-set recall
  0.00 → 0.20 (full slate), 0.00 → 0.40 (cuts-only).
- A failed AI analysis degrades to that floor instead of killing
  the task (D-1), with every stand-in segment honestly stamped
  "AI analysis unavailable" and never saved as an analysis.
- The task page shows an amber "Picked without AI analysis"
  notice for degraded tasks (also for the starter-clip fallback,
  S-1, `cfc2d29`).

## FIX-44 — Analysis-cache isolation (F-1 `615b618`, F-R2 `19069e9`)

Manual Cutter ranges can no longer be served as an AI analysis
(key separation + service-level isolation), and a degraded
stand-in is never cached as an analysis (write-path suppression
+ read guard; a healthy earlier analysis is preserved).

## FIX-45 — Caption-carry fixes

- F-B (`230d8d0`): stored caption position survives direct
  re-renders.
- F-A (`75fc951`): a Reference Style's measured caption height
  now reaches the render (it used to fall back to the coarse
  named band).
- Caption-carry follow-ups (`5bca336`): the caption PATCH route
  writes the edit plan; hook previews carry the stored caption
  position.
- F-C (`68bec02`): the Studio editor seeds caption grouping from
  the clip's niche profile, not the create-page template.

## FIX-46 — Device-export parity

- D-P1 (`a41d614`): device exports use each preset's own audio
  rate (Shorts 384 kbps, TikTok/Reels 192 kbps).
- F-P2/F-P3 (`da441ba`): device exports pinned to 30 fps (a 60
  fps source used to export at ~2× the preset video target).
- F-P4/F-P5 (`eef0332`): device export survives a missing B-roll
  download and a missing caption font (skip / bundled-font
  fallback, with a warning) instead of failing outright.
- D-C1 (`0ba8ef1`): combine respects each clip's hidden lanes.

## FIX-47 — Find Moments + Manual honours template/music (`1c1baba`, F-R1)

A manual pick used to render through the plain-cut path whatever
template/music was chosen — a Plain pick and an Auto pick
produced byte-identical files. Picks now render with the same
pipeline treatment as Find Moments + AI Editing (Plain still
plainified to the tight full-bleed crop).

## FIX-48 — pydantic-ai deprecation fixes (`235cf5c`, `fd3f3e3`)

`output_retries=` → `retries={'output': 2}` (8 sites),
`AgentRunResult.usage` read as a property, `openai:` model prefix
→ `openai-chat:`. Behaviour-preserving on the locked
pydantic-ai 1.107.10. One follow-up recorded, not done: before any
pydantic-ai v2 upgrade, decide normalise-or-reject for
user-supplied `openai:` strings.

## FIX-49 — Pristine-database schema fix (`7959ad7`)

`init_db()` built ORM tables before running the SQL migrations,
so on a fresh database the `tasks.separation_opt_in` column had
no server default while upgraded installs had one — any raw
INSERT omitting it failed. One-line fix (`server_default` on the
ORM column); installed databases are unchanged. Proven the only
such column by cross-checking all 60 migration `ADD COLUMN`s.
This gate ran the editor roundtrip suite on a fresh pristine DB
(2/0) as a new leg.

## FIX-50 — Fail-safe validation (`6793620`, findings F-2/F-4)

Stored template/music modes are re-validated with a warning +
documented default before rendering (a corrupt stored value can
no longer silently switch modes); invalid `output_format` /
non-bool `add_subtitles` on create now return 422 instead of
silently coercing to "vertical" / True.

## Also in this build

- NIM bake-off harness + fixtures (`025ffe9`, `eafbbe5`):
  candidates pre-registered **UNARMED** in the gateway config;
  no behaviour change, no winner claimed. The live bake-off runs
  on your machine with your keys.
- Recovery-doc set (`108153f`): FEATURE_PROGRESS.md, BUG_AUDIT.md,
  TEST_RESULTS.md, NEXT_SESSION_PROMPT.txt at the repo root.
- Model / asset / licence / download registries (`a939750`).
- `.env.example` LLM default points at the Groq free model
  (`e67d764`).
- Installer step-5 restart-path fix (script-level, found by the
  2026-11-13 rehearsals): on backup-validation failure the
  installer used to restart the old stack from the backup copy
  (`...\JarvisClipStudio-BACKUP-<stamp>\project`) — but the
  compose project name comes from the folder name, so that would
  have started a different, EMPTY project while claiming the old
  install was running again. It now restarts from `$ProjectDir`.

## Deliberately NOT in this build

- **GPT-6 Luna** — free only inside the ChatGPT app; its API is
  paid with no free route, so no key can power it for free.
- **Denoise** — measured and rejected (DeepFilterNet worsened
  Whisper word-error rate).
- **Mistral-direct free API** — free API access effectively gone
  (pricing page dropped the Free plan's API credits Oct 2026);
  Mistral models remain reachable via NIM / OpenRouter free.
- **Pexels new keys** — issuance paused since ~Sep 2026; the app
  uses Pixabay + Coverr.
- **Live bake-off winners** — the harness is ready; winners are
  decided only by measured runs on your machine.

## Known limitations (summary — full text in KNOWN_ISSUES.md)

G-V6-1 Cloudflare passthrough lines owed on your override;
G-V6-2 phantom migration-ledger row (LOW); G-V6-3 rendered-video
proof still owed for F-1 / F-R2 / F-R1 / F-A / F-B; G-V6-4 veto
follow-ups queued; G-V6-5 degraded picks carry no captions / hook
title yet; G-V6-6 the new degradation-notice component is not in
verify.ps1's 51-file list (expected total stays 166).
