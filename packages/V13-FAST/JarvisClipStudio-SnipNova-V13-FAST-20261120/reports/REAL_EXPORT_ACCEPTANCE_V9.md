# Real-Export Acceptance — RE-RUN ON THE EXACT V9 PACKAGED TIP

**Tree under test:** branch `next-milestone` @ **`20f903b`**
(`20f903bd84f6836b346b5941f74f801786cb2e25`) — the 13-branch
merge-batch tip `b05e6da`, the items-14/15 merges (`3dfd817`,
`40f5407`), the merge-gate zoom fix (`f49da36`), and the Step-0
residue-removal chore commit (`20f903b`, no product change).
Snapshot worktree: `~/workspace/jcs-accept-v9` (detached at the
tip). Tag `v6-candidate-90f1cb4` verified intact at
`90f1cb41b0837a04e4e772ee006857938a7db2fb` before and after.

**Method:** identical to Lane A on `5ea71bf`
(`REAL_EXPORT_ACCEPTANCE_ON_TIP.md`) — the same driver/persist/
measure harness, the same Japan fixture
(`~/workspace/qm-source/source.mp4`, 1,331,157,973 bytes), the
real product pipeline against a real scratch PostgreSQL 16.15
(51 migrations applied on a fresh database), and a
provider-boundary test double at `analyze_transcript` ONLY.
Every provider key was unset in the environment, so the
AI-dependent modes exercise the designed degraded path (D-1
floor, honestly stamped) — this run proves pipeline, settings
and rendering on the exact build. It is **not** AI-quality
proof; that remains user-machine validation, exactly as for
the V8 acceptance.

**Exports:** `exports_v9_manifest.csv` (this folder) — 32 rows
with SHA-256, bytes, duration, dimensions, codecs.
Scratch evidence root: `~/workspace/jcs-accept-v9/scratch-v9/`
(`out/<combo>/result.json`, `logs/`, `temp/clips/*.source_map.json`).

## 1. Verdict table

| Creation mode | Plain | Auto |
|---|---|---|
| Manual Cutter + AI Editing | **PASS** (2 clips) | **PASS** (2 clips) |
| Full AutoClip | **PASS — degraded path** (10 clips) | **PASS — degraded path** (10 clips, completed via checkpoint resume after a real runtime restart) |
| Find Moments + Manual Pick | **PASS — degraded path** (parked with 10 candidates; pick → 2 clips) | **PASS — degraded path** (2 clips) |
| Find Moments + AI Editing | **PASS — degraded path** (2 clips) | **PASS — degraded path** (2 clips, completed via checkpoint resume after a second real runtime restart) |

**32/32 exports, every one 1080×1920, h264 + aac** (manifest
check: 0 exceptions). No mode failed outright; Plain honesty
held in every Plain task (§2). The established PASS shape from
the `5ea71bf` acceptance is reproduced on this tree.

## 2. Plain honesty (the P0 property)

Source-map reframe shots, counted per combo from
`temp/clips/*.source_map.json`:

| Combo | Shots by mode |
|---|---|
| manual_plain | plain **30/30** |
| fullauto_plain | plain **159/159** |
| momentsmanual_plain | plain **34/34** |
| momentsai_plain | plain **34/34** |
| manual_auto | single 4 / fit 23 / split 3 |
| fullauto_auto | fit 94 / single 47 / pair 2 / split 16 |
| momentsmanual_auto | fit 20 / single 11 / pair 1 / split 2 |
| momentsai_auto | fit 20 / single 11 / pair 1 / split 2 |

Every Plain task rendered **only** `plain` shots (257/257);
every Auto task rendered the designed planner mix with **zero**
plain shots. Settings read back through the real serializers
after completion: Plain tasks `template_mode=plain`,
`music_mode=off`; Auto tasks `template_mode=None`,
`music_mode=on` — the F-1 read-back property holds on this
tree, in all four creation modes.

**Audio (lag-0 cross-correlation of export audio against the
source window):** the window 149–179 s was rendered by all
eight combos — **every Plain combo: 0.9992** (the export audio
is the source dialogue; no music bed), **every Auto combo:
0.1374** (a ducked music bed is present, as designed).
Manual Cutter's gold windows: Plain c1 0.9993 / c2 0.9917 —
numerically identical to the `5ea71bf` acceptance. (The
manual_auto task rendered without a bed — its driver log
states the profile has no bed and the music library is empty;
the ClipPlans still differ by design: 13 keys on Auto, 0 on
Plain.)

## 3. The degraded path, and what the floor fix changed

The six AI-dependent combos completed via the D-1 floor with
honest stamps (segment reasonings read "Visual selection (AI
analysis unavailable): audio energy peak …"). Two deltas vs
the `5ea71bf` acceptance, both measured:

- **Floor hook titles now land where transcript words exist.**
  The seeded transcript sidecar covers only 890–2469 s of the
  5,621 s source (237 words — a fixture property, identical in
  both acceptances). Of the ten fullauto_plain floor windows,
  exactly one (896–926 s) falls inside the covered span; that
  clip's export carries **12 caption chunks and a grounded,
  burned-in hook title** ("avocado tree that was 700 years
  old" — frame-checked at t=1 s, captions burning below it).
  On `5ea71bf` the same-shaped run produced no floor titles at
  all. The other nine windows contain no transcript words, so
  there is nothing to caption or ground a title from — the
  renderer warns ("No words or hook title available") instead
  of inventing one. Caption presence tracks transcript
  coverage uniformly in every mode (manual clip 1, inside the
  span: 37 chunks; manual clip 2, outside: 0). This is the
  G-V8-4 limitation narrowed by the floor lane, recorded as
  G-V9-4 in the package's KNOWN_ISSUES.
- **Reframe cache schema v1 → v2 upgrade path exercised.**
  The seeded reframe cache is schema v1; this tree requires v2
  (the framing lane's motion-centroid evidence), so the cache
  was rejected on size+mtime match and scene evidence was
  recomputed by the pipeline — the designed upgrade path, not
  an error.

**F-R2 holds:** after all eight combos, `processing_cache`
contains exactly 2 rows (the seeded transcript and the manual
user-ranges analysis) — **zero** stand-in/degraded payloads
were persisted to the shared cache.

## 4. Persistence across a database restart

Deliberate leg (persist.py): a Manual/Plain task (plain /
music off / broll_enabled false) was created, the scratch
PostgreSQL was restarted, and a fresh process re-read the task
through the real repository and rendered it.
**Re-read after restart: `plain / off / false` — unchanged.**
Post-restart status: completed. The two exports are
**byte-identical** to the manual_plain combo's exports
(SHA-256 `8a8cc3b4…` and `08991f8b…` in both places).

## 5. Interruptions during this run (all environmental)

This sandbox's runtime restarted twice mid-acceptance; the
record is kept because the recovery is itself evidence:

1. ~17:13 UTC — the first chain and the scratch PostgreSQL
   were killed mid-`fullauto_auto` (0 clips rendered). PG was
   restarted (clean WAL recovery); the task was **resumed from
   its checkpoint and completed** (10 clips) — the Lane A
   resume shape, reproduced on this tree.
2. ~19:05 UTC — a second restart killed the `momentsai_auto`
   pick mid-render **and dropped the apt-installed binaries**
   (PostgreSQL + Redis). PG was restored from the banked dist
   (`~/workspace/.pg-dist`), Redis 7.0.15 reinstalled from the
   local apt cache; the task (selection already applied,
   status `processing`) was **resumed and completed** (2 clips).
3. The `momentsmanual_plain` pick's first attempt failed in
   26 s with `ConnectionError … port 6379` — Redis was down
   (casualty of restart 1, restarted late). The product behaved
   as designed (selection rolled back to `awaiting_selection`
   with "Could not queue rendering. Please try again."); the
   re-pick with Redis up completed normally (selected_count 2,
   task completed). **Not a product failure** — the pick path
   requires Redis, as it did in the `5ea71bf` acceptance.

## 6. Run record

| Combo | Wall | Result |
|---|---|---|
| manual_plain | 200.2 s | completed, 2 clips |
| manual_auto | 245.9 s | completed, 2 clips |
| fullauto_plain | 1,784.5 s | completed, 10 clips |
| fullauto_auto | 1,702.4 s (both legs) | completed, 10 clips (resume) |
| momentsmanual_plain | ~630 s + pick ~570 s | parked → completed, 2 clips |
| momentsmanual_auto | ~615 s + pick ~568 s | parked → completed, 2 clips |
| momentsai_plain | ~629 s + pick ~513 s | parked → completed, 2 clips |
| momentsai_auto | ~629 s + pick + resume | parked → completed, 2 clips |

## 7. What this acceptance does NOT prove

AI selection/editing quality with live providers (no keys in
the sandbox by design) — user-machine validation, unchanged
from every prior acceptance. NVENC behaviour (the sandbox has
no GPU; the probe fails and the encoder diagnostics record the
CPU fallback, as the perf lane designed). The new styles'
audience value (mistake-fix, list-fill, trailer-breakdown) —
only real posts prove that.
