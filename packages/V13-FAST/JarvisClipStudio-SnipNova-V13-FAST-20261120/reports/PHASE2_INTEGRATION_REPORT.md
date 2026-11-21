# Phase 2 integration — §9 report (draft, gate numbers pending)

Branch: integrate/phase2-auto-editor @ bcb553de, worktree ~/workspace/jcs-lanea,
stacked on 8095214a (Lane A + Phase 1). NOT merged, NOT packaged (per tasking).

## Hunk log (summary; full detail in HUNK_LOG.md)
- 22/22 Phase 2 patches applied via git am --3way, in order.
- Patch 0006 applied FILTERED: backend + migration + caption templates +
  tests + docs + tools. DROPPED frontend hunks (8 files):
  frontend/messages/{de,en,es,id,pt,zh}/home.json,
  frontend/src/components/home-app.tsx,
  frontend/src/components/home-app-gaming-layout.test.tsx
  (behaviours re-implemented in the Studio Console port, step 5).
- CHANGELOG conflicts on 0001/0003/0005/0007/0010/0020 (+ both opencode
  patches): resolved keep-both-sides, PR suffix on every entry; two
  marker accidents repaired in-history (amend a1d04982, fixup 3c6bb87a).
  Final: 0 markers, changelog vitest 4/4.
- Migration 20261117_0001_task_layout_template.sql applied, no clash.
  MIGRATIONS TOTAL: 52.
- OpenCode: 0001 NVENC (ac30c8e4), 0002 export profiles (f12aacf8),
  0003 proof reel (a51bc70c), addendum 0001 (26ce0ed4) — all shown
  PASS + Claude-signed-off in REPORT.md; nothing else included.

## Gate composition (A1)
Routing seam keeps Sanji's find_reactor_corner + Claude's measurable-inset
condition (REFRAME_REACTOR_NEEDS_INSET). Engagement composed in
choose_reaction_layout: legacy source-faces path unchanged; faceless path
gated on _source_area_changes; facecam_rect branch engages on the
detector's own face-inside verification (refinement f0a60773 after
test_gaming_templates pinned Claude's semantics). Lecture presenter
(visible to the analysis pass) is decided by the corner-face branch's
source-change check -> stays on plain fit, as agreed.
Suites: gate batch 93 passed / 0 failed (facecam_rect, gaming_templates,
reaction_layout, reframe_reactor_inset, reframe_wire, reframe_consistent).
gate_compare.py: NOT RUN — clips not present locally; fetching ~15
Twitch/YouTube sections through the sandbox proxy not feasible in-lane.
Claude's 7/8-vs-0/8 table stands as his-side evidence only.

## Fixture re-score (integrated tree, tools/phase2/score_fixture.py)
Default: 6/7 checks PASS. Slate: #1 01:40-01:52 R3 fused 55.0
(commentary_reaction; signals facecam 0.237 / voice 0.739 / transcript
null / gameplay 0.159); #2 00:16-00:28 R1 fused 18.5 (facecam 0.348 /
voice 0.463 / transcript null / gameplay 0.392). DECOY absent. R2 missed
— the accepted, measured borderline (do not tune).
REACTION_FACE_LED=0: slate identical. REACTION_CONTRACT_FIELDS=0: slate
identical, fused/signals ABSENT (not zero-filled).

## Contract fields
test_reaction_contract PASS (in new-suites batch). Verified from actual
picker output both ways (above): signals{facecam_reaction, voice_burst,
transcript_emotion, gameplay_event} / fused_score present when ON;
transcript_emotion null (unmeasured, never 0); fields absent when OFF.

## Frontend port (step 5) — commit 9b238369 (+ changelog 8a1bcf21)
Picker in Campaign & style beside the niche select, Gaming only; Auto /
Face top - red / Face top - gold + credit input (maxLength 40); disabled
with the Framing hint when framing is fit/crop; payload adds
layout_template + trimmed layout_credit (only when non-empty) only when
a template is picked. Tests: home-app-gaming-layout.test.tsx 5/5 PASS
(Claude's 4 behaviours + the framing-disable behaviour).

## Galaxy background (added scope) — commit 4b48c144
Route (b): canvas-2d starfield, no ogl dependency. Details in HUNK_LOG.
Targeted vitest (reactbits + changelog) 8/8 PASS. Full frontend gate:
see below (runs on the final tip, Galaxy included).

## Switch proof (step 6)
NO BLEED. Finance (99.9-141.9s): Plain = transitions [], zooms 0,
broll 0/0, music none, sfx 0, overlays 0. Auto = 5x hard_cut, zooms 1,
broll 3 planned / 0 inserted (no_provider_configured), music corporate
requested. Comedy (149.6-190.6s): Plain = all zero/none. Auto = zooms 1,
music comedy requested. MUSIC BEDS ARE ABSENT until Luffy's 7 MP3s land
in assets/music — the chain requests them by mood and skips with a
logged reason; renders complete. SFX 0 in Auto = harness limit
(energy_peaks=[]), disclosed by Claude, same on his side; SFX files are
in the tree (patch 0016) and cue logic is unit-covered.

## NVENC (step 7)
Probe fix integrated (ac30c8e4 + addendum). Suites in the affected batch
PASS: test_nvenc_probe_realistic, test_nvenc_probe, test_prestage_nvenc,
test_edl_nvenc_escape_hatch, test_export_profiles, test_export_presets.
Sandbox has no GPU: the probe correctly fails over to libx264 (observed
live in the switch-proof renders: NVENC probe error -> render ok=True).

## Gates (step 8)
- Phase 2 new suites (10 files): 165 passed / 1 failed -> the 1 failure
  fixed by the composition refinement; combined gate batch re-run 93/0.
- Affected suites (17 files): 230 passed / 1 skipped / 0 failed
  (incl. test_creation_modes_pipeline).
- FULL backend gate: 5,235 passed / 5 failed / 239 skipped in the chunked run on 4b48c144 — all 5 failures resolved and re-verified on the final tip (see HUNK_LOG.md)
- Frontend full vitest / tsc / next build: 5,235 passed / 5 failed / 239 skipped in the chunked run on 4b48c144 — all 5 failures resolved and re-verified on the final tip (see HUNK_LOG.md)

## Merge readiness: READY (see final report)

## V12 packaging needs
- The 7 music MP3s (still owed by Luffy — upload failed 2026-11-18):
  calm, comedy, corporate, energetic, funk, phonk, upbeat -> assets/music.
- verify.ps1 expectation: V11 = 176 (19 base + 51 migrations-in-tree +
  51 migrations-applied + 55 feature files). V12 minimum = 178
  (migration 20261117_0001 counted twice: +2). Packaging must also
  recount the 55-file curated list for Phase 2 feature files and decide
  asset checks.
- Package manifest: add assets/music (7 MP3) + assets/sfx (6 WAV) as
  required items.
