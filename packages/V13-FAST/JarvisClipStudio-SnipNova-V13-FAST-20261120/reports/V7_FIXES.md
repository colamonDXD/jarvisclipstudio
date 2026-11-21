# V7 Fixes — SnipNova Consolidated Release v7 (2026-11-15)

The v6 build (`6669028`, code tree `7959ad7`) plus **20 commits** on
branch `next-milestone`, tip **`5ea71bf`**. Everything in
`reports/V6_FIXES.md` (FIX-35…FIX-50) stands; this file records only
the v7 delta. Gate on this exact tip: **GATE GREEN**
(`INTEGRATED_GATE_5EA71BF.md`) — backend **4,656 passed / 0 failed**,
frontend **899 passed / 0 failed**, tsc exactly the 11-error baseline
set, eslint 0/0, production `next build` exit 0, smoke 162 routes /
136 paths, migrations 48, **zero new failures**.

## FIX-51 — Re-renders keep the vocals stem (N3)

`6e1576b`, merge `c662413`. The editor audit's independent
verification found that the re-render path (`rerender_clip`, hook
previews) never resolved the vocals stem for separation-opt-in
tasks: the main pipeline used the stem, a re-render silently cut
from the raw mix. A shared resolver now serves both paths;
non-opt-in tasks are byte-identical in behaviour (the stem argument
is only passed when a stem exists). **Rendered proof:** a synthetic
music+voice source through the real pipeline — the music-only clip
measures **−91.0 dB** on the original render, the re-render AND the
regeneration, vs −16.2 dB for the unstripped control (**74.8 dB**
suppression, the unit test's ≥15 dB bar). The re-rendered music clip
is byte-identical to the original (deterministic EDL).

## FIX-52 — Regenerations keep the vocals stem too (N3 regen gap)

`47a66de`, merge `2eedce8`. Found by the FIX-51 lane on the adjacent
seam: `regenerate_all_clips_for_task` re-renders through a legacy
chain that took no stem at all. The stem is now threaded through
with the same conditional-argument pattern; non-opt-in call shapes
are unchanged (pinned by test). Same rendered proof as FIX-51.

## FIX-53 — Reference Style transitions: claim narrowed to the truth (N1)

`2561cce`, merge `0a63942`. The audit's one DRIFT finding: Reference
Style `on_topic_change` / `on_punchline` transition fields were
written and read as if they shaped render joins; in fact nothing
consumed them except the AI director's prompt string
(`on_punchline` only). The fix is honesty, not wiring: both fields
are now documented — in code and in the director prompt — as
director guidance, and tests pin that neither is stamped on joins.
A true per-join mechanism needs a real topic/punchline signal and a
renderer mapping for `zoom_bounce`; neither exists, and inventing
one was out of scope.

## FIX-54 — Editor LOW items N4–N7

Merge `a9c6bc0` (branch commits `35cffcf`, `2f0fab0`, `50d2f60`,
`f015a00`), comment cleanup `d3e7b52`.

- **N4** — `words_per_line` is now settable in the quick editor
  (validated 1–8, no silent fall-through) and understood by the
  per-clip AI edit; it is stamped into the EDL captions block.
- **N5** — the combine hint now discloses hidden-lane drops
  (hidden captions contribute no words, hidden audio contributes
  silence, hidden B-roll/overlays are left out), in all 6 locales.
- **N6** — the caption font select offers all six model fonts
  (Montserrat-Black and Montserrat-ExtraBold added).
- **N7** — the editor audit's headline count is restated honestly:
  90 of 91 inventoried controls confirmed at the audited tip,
  1 partial (the FIX-53 item) — the old "69" figure undercounted
  the audit's own inventory.

**Rendered proof** (`N4_N6_RENDERED_PROOF.md`, on this tip): the
same clip rendered at words-per-line 2 vs 6 groups its captions
exactly so (max 2 vs max 6 words per event); Montserrat-Black and
Montserrat-ExtraBold renders stamp their own font names in the
caption file — no silent fallback.

## FIX-55 — B-roll concept phrasing + room avoid completion

`a0e9e0f`, `18495c5`, `c917016`, merge `9b24c7b`. The veto
calibration expansion (100 judged pairs: 1 miss, 6 false vetoes)
diagnosed 4 of 5 failures as concept *phrasing* — scenery-checklist
concepts ("a sandy bay with turquoise water") vetoed against
correct-sense footage. The planner prompt now requires concepts
built around a defining subject (a person doing the thing, the
object in use; places anchor on their recognising subject), and a
deterministic validator completes room concepts' avoid lists with
competing rooms' signature furniture (studio → bed/sofa/kitchen
counter/stove; apartment → mixing desk/monitor speakers/acoustic
panels/studio microphone). Additive only; non-room concepts are
byte-identical; Plain is untouched. A threshold sweep on the
combined 100-pair base (T from −0.020 to +0.048) found **no
threshold that dominates** the shipped one, so the veto default
stands — measurement only, no change. The live effect (concepts
written under the new guidance, on real provider keys) is
user-machine calibration, checklist item 43.

## FIX-56 — The Montserrat font type gap

`701546b`, `360304c`, `50224e4`, merge `5ea71bf`. FIX-54's N6 added
the two Montserrat fonts to the editor's font select, but the
`CaptionStyle` font union in the editor document model still listed
only four — the full gate on the first v7 candidate (`9b24c7b`)
went **RED on exactly this one new failure** (tsc TS2322). The
union now includes both Montserrat fonts, the unsafe cast is gone,
and the re-gate on `5ea71bf` is GREEN with the tsc error set
byte-identical to the 11-error baseline (the TS2322 line removed,
nothing added). Recorded here because it is the gate working as
designed: a real defect, caught before shipping, fixed at the root.

## Evidence added with this build

- **Rendered-proofs batch, 5/5 PASS** (`RENDERED_PROOFS_BATCH_NOV14.md`,
  on this tip): F-R1 (Find Moments + Manual pick pair — Plain vs
  Auto now differ exactly as designed; pre-fix byte-identical),
  F-B (stored caption Top survives a direct re-render, ASS median
  0.1557), F-A (Reference Style measured caption height renders at
  0.6359, byte-identical to the explicit control), F-1 (Manual
  Cutter cache cannot poison a Full AutoClip run — 3 legs incl. the
  poison leg), F-R2 (a degraded floor slate is never cached as an
  analysis). **This closes G-V6-3.**
- **N4/N6 rendered proof** (`N4_N6_RENDERED_PROOF.md`) — see FIX-54.
- **Editor audit independent verification** — 90 of 91 inventoried
  controls confirmed at tip `6669028`, the 1 drift being FIX-53.

## Deliberately NOT in this build

- **No new database migration** — the schema stays at 48.
- **No model winner wired** — the live bake-off never ran (the
  build sandbox holds no provider keys by design); the shipped
  provider ladder is the proven FIX-39 ladder. See
  `AI_MODEL_BAKEOFF.md` in the tree (live phase PENDING, no winner
  named).
- GPT-6 Luna (API paid, no free route), denoise (measured and
  rejected), Mistral-direct free API (revoked/disputed) — as in v6.

## Known limitations (summary — full text in KNOWN_ISSUES.md)

G-V7-1 Cloudflare override lines still owed on the preserved
override; G-V7-2 phantom migration-ledger row (LOW); G-V7-3 the
32-export acceptance set predates this tip (fresh re-run owed);
G-V7-4 live provider validation owed on the user's machine;
G-V7-5 degraded picks carry no captions/hook title; G-V7-6 the
create form hardcodes `processing_mode: "fast"` (product decision
open); G-V7-7 the slow-render database-timeout sandbox observation;
G-V7-8 legal pages still show the placeholder company name.
