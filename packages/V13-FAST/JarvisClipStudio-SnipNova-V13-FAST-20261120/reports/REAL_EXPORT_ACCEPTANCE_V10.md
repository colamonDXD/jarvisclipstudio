# Real-Export Acceptance — RUN ON THE EXACT V10 PACKAGED TIP

**Tree under test:** branch `next-milestone` @ **`901ae252`**
(`901ae2528fb50363ed2b15d3ae290d28cc852ccc`) — the V9 tip
`20f903b` plus today's merges: the face-evidence stack, the
streamer-reaction journey milestone 1 (`4a6b54ce`), the
reaction-layout gate fix (`c7c5b3ca`), the test-only gate fixes
(`dad9e31b`), and the model-config corrections (DeepSeek slug
`deepseek-v4-pro`, HF_TOKEN compose pass-through).
Snapshot worktree: `~/workspace/jcs-accept-v10` (git-archive
extraction of the tip; fidelity spot-proven by file hash).
Tag `v6-candidate-90f1cb4` untouched by this lane.

**Method:** identical to the V9 acceptance
(`REAL_EXPORT_ACCEPTANCE_V9.md`) — the same driver/persist/
measure harness, the same Japan fixture
(`~/workspace/qm-source/source.mp4`, 1,331,157,973 bytes), the
real product pipeline against a real scratch PostgreSQL 16.15
(51 migrations applied on a fresh database), and a
provider-boundary test double at `analyze_transcript` ONLY.
Every provider key was unset, so the AI-dependent modes
exercise the designed degraded path (D-1 floor, honestly
stamped) — this run proves pipeline, settings and rendering on
the exact build. It is **not** AI-quality proof; that remains
user-machine validation, exactly as for the V8/V9 acceptances.

**V10 addition — the streamer journey leg (§5):** the streamer
fixture (1280×720, 150 s, real facecam inset, stereo
gameplay/mic) driven through the real service path in
Find Moments + AI mode with the journey's settings (Auto
template, framing auto, gaming niche, vertical), the
R3-overlapping candidate picked through
`apply_moment_selection`, and the export frame-judged for the
stacked two-pane reaction layout — this package's headline
change.

**Exports:** `exports_v10_manifest.csv` (this folder) — 33 rows
with SHA-256, bytes, duration, dimensions, codecs (32 four-mode
+ 1 streamer). Scratch evidence roots:
`~/workspace/jcs-accept-v10/scratch-v10/` and
`~/workspace/packaging-v10/streamer-accept/`.

## 1. Verdict table

| Creation mode | Plain | Auto |
|---|---|---|
| Manual Cutter + AI Editing | **PASS** (2 clips) | **PASS** (2 clips) |
| Full AutoClip | **PASS — degraded path** (10 clips) | **PASS — degraded path** (10 clips) |
| Find Moments + Manual Pick | **PASS — degraded path** (parked with 10 candidates; pick → 2 clips) | **PASS — degraded path** (2 clips) |
| Find Moments + AI Editing | **PASS — degraded path** (2 clips) | **PASS — degraded path** (2 clips) |
| Streamer journey (Find Moments + AI, gaming) | — | **PASS** (slate §5; pick → 1 stacked-layout clip) |

**33/33 exports, every one 1080×1920, h264 + aac** (manifest
check: 0 exceptions). No mode failed outright; Plain honesty
held in every Plain task (§2). The V9 PASS shape is reproduced
on this tree, and the streamer leg passes on the merged
gate fix.

## 2. Plain honesty (the P0 property)

Source-map reframe shots, counted per combo from
`temp/clips/*.source_map.json`:

| Combo | Shots by mode |
|---|---|
| manual_plain | plain **30/30** |
| fullauto_plain | plain **158/158** |
| momentsmanual_plain | plain **34/34** |
| momentsai_plain | plain **34/34** |
| manual_auto | single 4 / fit 23 / split 3 |
| fullauto_auto | fit 94 / single 45 / pair 2 / split 17 |
| momentsmanual_auto | fit 21 / single 10 / pair 1 / split 2 |
| momentsai_auto | fit 21 / single 10 / pair 1 / split 2 |

Every Plain task rendered **only** `plain` shots (256/256);
every Auto task rendered the designed planner mix with **zero**
plain shots. Settings read back through the real serializers:
Plain tasks `template_mode=plain`, `music_mode=off`; Auto tasks
`template_mode=None`, `music_mode=on`; creation modes exact
(`manual_cutter_ai` / `full_auto` / `moments_manual` /
`moments_ai`) — the F-1 read-back property holds on this tree,
in all four creation modes.

**Audio (lag-0 cross-correlation of export audio against the
source window):** Manual Cutter's gold windows — Plain c1
**0.9993** / c2 **0.9917**, numerically identical to the
`5ea71bf` and V9 acceptances (the export audio is the source
dialogue; no music bed). The manual_auto render of the same
window also reads 0.9993 — as in V9, that task's profile has
no bed and the music library is empty, stated in its driver
log; the ClipPlans still differ by design.

**F-R2 holds:** after all eight combos, `processing_cache`
contains exactly 2 rows (the seeded transcript and the manual
user-ranges analysis) — **zero** stand-in/degraded payloads
were persisted to the shared cache.

## 3. The degraded path

The six AI-dependent combos completed via the D-1 floor with
honest stamps (segment reasonings read "Visual selection (AI
analysis unavailable): audio energy peak …"). The seeded
transcript sidecar covers only 890–2469 s of the 5,621 s
source (237 words — a fixture property, identical in every
acceptance); caption/hook-title presence tracks that coverage
uniformly, and the renderer warns ("No words or hook title
available") instead of inventing text where no words exist —
the G-V9-4 limitation, unchanged on this tree (carried as
G-V10-4).

## 4. Persistence across a database restart

Deliberate leg (persist.py): a Manual/Plain task (plain /
music off / broll_enabled false) was created, the scratch
PostgreSQL was restarted, and a fresh process re-read the task
through the real repository and rendered it.
**Re-read after restart: `plain / off / false` — unchanged.**
Post-restart status: completed. The two exports are
**byte-identical** to the manual_plain combo's exports
(SHA-256 `8a8cc3b4…` and `08991f8b…` in both places — the same
hashes the V9 acceptance recorded; the manual path is
deterministic and untouched by the V9→V10 delta).

## 5. Streamer journey leg (new in V10)

Fixture: `streamer_fixture.mp4` (24,804,617 bytes, SHA-256
`1fe488e8…`) — ground truth R1 20–28 s (kill + reaction),
R2 50–58 s (funny), DECOY 80–86 s (loudest gameplay explosion,
face still, mic silent at −84 dB), R3 105–115 s (small event,
biggest reaction, mic peak −0.5 dB at t≈107.8 s).

- **Create + process:** task created with the journey's exact
  settings (Find Moments + AI, Auto template, framing auto,
  gaming niche, vertical) and parked at `awaiting_selection`
  with **4 candidates** via the visual floor (the analysis
  gateway is unreachable in the keyless sandbox, by design).
- **Slate vs ground truth:** candidate **[80–110 s], score 90
  — the slate's #1 — is anchored on the audio-energy peak at
  108 s, the R3 scream**; its window overlaps R3 by 5 s. The
  decoy was **not** picked as an anchor (the window merely
  starts inside the decoy's span; the driving peak is the mic
  scream, not the silent-face explosion). Candidates [0–30]
  and [42–72] overlap R1/R2 but are anchored on ambient speech
  energy — the floor has no reaction signal; reaction-aware
  ranking is Lane A's contract work, not in this tree.
- **Pick + render:** the R3-overlapping candidate was selected
  through `apply_moment_selection` (selected_count 1) and
  rendered to completion with no process error.
- **Export:** 26.6 s, **1080×1920, h264 + aac**,
  18,016,440 bytes.
- **Frame judgement (extracted frames at 3 s / 13 s / 24 s,
  read directly):** the **stacked two-pane reaction layout
  ENGAGED** — top pane the reactor facecam at full width (face
  large, fully inside its pane), bottom pane the gameplay
  (city scene, crosshair, HP/AMMO HUD; an elimination event
  banner visible at 24 s). The facecam inset is **masked out**
  of the gameplay pane (no duplicate inset). Word captions sit
  at the seam with the active word highlighted; the hook title
  ("Bro, you know, you know") renders at the bottom. The
  gameplay pane uses the layout's 'content' mode — the full
  source width over a dark plate, nothing cropped — the honest
  limitation carried as G-V10-10. On the V9 tip this same path
  fell back to a single crop (the layout gate demanded faces
  in the gameplay area); the merged gate fix (gameplay-motion
  evidence) is what engages the layout here.

## 6. Interruption during this run (environmental)

The sandbox runtime restarted once mid-acceptance (~11:19
UTC), killing the first chain during the `momentsmanual_auto`
pick and wiping the scratch PostgreSQL data (kept in /tmp).
The five completed combos' evidence (exports + result records)
lives on the persistent workspace and was unaffected.
PostgreSQL was restored from the banked dist, Redis/tesseract
were restored by the platform's package replay, a fresh
database was seeded (51 migrations), and the three remaining
combos were run in a continuation chain — `momentsmanual_auto`
re-run from create (its parked task had died with the old
database), then both `momentsai` combos. All completed with
the same shapes as the uninterrupted combos. **Not a product
failure** — the same class of sandbox interruption the V9
acceptance recorded twice; here the database loss (a scratch
placement choice, /tmp) forced one combo re-run instead of a
checkpoint resume.

## 7. Run record

| Combo | Wall | Result |
|---|---|---|
| manual_plain | 213.6 s | completed, 2 clips |
| manual_auto | 257.9 s | completed, 2 clips |
| fullauto_plain | 1,868.8 s | completed, 10 clips |
| fullauto_auto | 2,011.8 s | completed, 10 clips |
| momentsmanual_plain | ~489 s + pick ~513 s | parked (10 candidates) → completed, 2 clips |
| momentsmanual_auto | ~544 s + pick ~573 s (continuation chain) | parked (10 candidates) → completed, 2 clips |
| momentsai_plain | ~494 s + pick ~519 s | parked (10 candidates) → completed, 2 clips |
| momentsai_auto | ~544 s + pick ~570 s | parked (10 candidates) → completed, 2 clips |
| streamer journey | run ~71 s + pick ~178 s | parked (4 candidates) → completed, 1 clip |

## 8. What this acceptance does NOT prove

AI selection/editing quality with live providers (no keys in
the sandbox by design) — user-machine validation, unchanged
from every prior acceptance. NVENC behaviour (the sandbox has
no GPU; the encoder probe falls back to CPU, as designed).
Reaction-aware **ranking** (the fused reaction×gameplay
scoring is Lane A's contract work; the floor's slate here is
energy-anchored, honestly reported in §5). The streamer leg
ran on the synthetic fixture — a real streamer VOD remains
user-machine validation (G-V10-10).
