# REAL-EXPORT ACCEPTANCE — ON THE EXACT SHIP TIP `5ea71bf` — 2026-11-15

This run closes release-gate failure (3) of `RELEASE_GATE_NOV15.md`: the
previous full acceptance (`REAL_EXPORT_ACCEPTANCE_RERUN.md`) ran on
`9ba6602`, and 92 files changed before the ship tip. This run repeats the
identical method on the exact final build.

- **Code under test:** `next-milestone` tip **`5ea71bf`**
  (`5ea71bfb92201ad09fdd6bd408a87ad659f070d4`, HEAD verified before the
  run), snapshot worktree `~/workspace/jcs-accept-tip` (detached @
  5ea71bf). **Zero production code changes** — snapshot has 0 modified
  tracked files; `~/workspace/jcs-next` untouched (HEAD + clean tree
  re-verified after the run); protected tag `v6-candidate-90f1cb4`
  intact.
- **Path:** the REAL one — `TaskService.create_task_with_source` → real
  PostgreSQL (fresh 16.15 scratch cluster, DB `accept_tip` @
  127.0.0.1:55436, UTF8) → fresh-session re-read →
  `TaskService.process_task` (worker-equivalent). Picks went through the
  endpoint's own `TaskService.apply_moment_selection`. No stage mocked.
- **Fixture:** identical to the precedent — Japan source (YouTube
  `ANoZNWQXYZ4`, 1,331,157,973 B, hardlinked), seeded
  transcript/beat/reframe caches, manual ranges **r3 + r5**.
- **PROOF LEVEL (plainly):** the sandbox holds **no provider or stock
  keys and no Ollama, by design**. Every AI-dependent combo therefore
  exercised the app's designed failure path: ~300 s of provider failover
  → the D-1 deterministic visual selection floor, with every degraded
  segment honestly stamped. **"Degraded completion" is not AI-mode
  proof** — real AI selection quality, real ClipPlans, audible music
  beds and stock B-roll decisions remain **user-machine validation**,
  exactly as in the precedent reports. Provider-boundary behaviour is
  otherwise the app's own: nothing was stubbed inside the pipeline; the
  providers are simply unreachable, which is the condition the floor
  exists for.
- **Clean-cache discipline:** `analysis_json` cleared before every
  combo (precedent finding F-1).

## 1. Verdict table — all four modes × both templates, on `5ea71bf`

| Mode × template | Verdict | Exports |
|---|---|---|
| Manual Cutter × Plain | **PASS** | 2 |
| Manual Cutter × Auto | **PASS** | 2 |
| Full AutoClip × Plain | **PASS (degraded path)** — 10 floor exports, honestly stamped | 10 |
| Full AutoClip × Auto | **PASS (degraded path)** — 10 floor exports; completed via checkpoint resume after a real VM restart (§5) | 10 |
| Find Moments + Manual × Plain | **PASS (degraded path)** — parked with 10 candidates → real pick → 2 exports; **F-R1 fix confirmed (§3)** | 2 |
| Find Moments + Manual × Auto | **PASS (degraded path)** — parked → pick → 2 exports | 2 |
| Find Moments + AI Editing × Plain | **PASS (degraded path)** — parked → pick → 2 exports | 2 |
| Find Moments + AI Editing × Auto | **PASS (degraded path)** — parked → pick → 2 exports | 2 |

**Total: 32 exports — all ffprobe-verified 1080×1920, h264 + AAC**
(manifest: `exports_on_tip_manifest.csv`, alongside this report;
hashes + sizes + durations for every file).

## 2. Plain honesty (rendered level, this tip)

- **Reframe stamps:** every Plain export's source map carries **only**
  `plain` shots — manual_plain 31/31, fullauto_plain 159/159,
  momentsmanual_plain 34/34, momentsai_plain 34/34. No fit, no split,
  no blur band anywhere. Auto combos show the designed mix
  (single/fit/split/pair).
- **No music bed:** export-vs-source audio cross-correlation at lag 0 —
  manual_plain c1 **0.9993**, c2 **0.9917**; fullauto_plain c1
  **0.9992**. The export audio is the source dialogue, re-encoded.
  (Precedent on 9ba6602: 0.9993 / 0.9922 / 0.9992 — reproduced.)
- **No B-roll inserts:** source maps contain only source ranges; the
  template-match scan behaves exactly as in the precedent (low tail on
  deep-zoom two-shot frames, resolved there by direct visual
  comparison as source frames, not inserts).
- **Auto contrast:** manual_auto c1 audio xcorr **0.8008** (precedent:
  0.8008 — the music-decision path decorrelates exactly as before;
  the bed itself is absent in the keyless sandbox, as in the
  precedent), and its clips carry full 13-key ClipPlans vs 0 keys on
  the Plain manual clips.

## 3. F-R1 — Find Moments + Manual picks ignored template/music — **FIXED on this tip**

The precedent defect (pre-fix: Plain and Auto pick renders
byte-identical, zero reframe shots, wide 16:9 strip with blurred
bands) does **not** reproduce on `5ea71bf`:

- momentsmanual_plain picks: `b3bf5194…` / `62f80d1a…` — the Plain
  renders (byte-identical to Full AutoClip × Plain clips 1–2: same
  slate + same template ⇒ same file).
- momentsmanual_auto picks: `ed1aaec0…` / `02dbf7f5…` — the Auto
  renders. **Plain ≠ Auto**, for the first time on this path.
- Source maps for the manual pick renders now carry a full reframe
  plan: 34 shots (all `plain` for Plain; fit/single/pair/split for
  Auto) vs **zero** pre-fix.

## 4. F-R2 — degraded stand-in persisted to the shared cache — **FIXED on this tip**

After all eight combos (six of them degraded), `processing_cache`
contains **zero** rows matching the stand-in markers ("AI analysis was
unavailable" / "Visual selection"). The only non-null `analysis_json`
is the Manual Cutter's own honestly-labelled record
(`selection_strategy: "user_ranges"`). Task checkpoints of degraded
tasks likewise contain **no `analysis` key** (verified for
fullauto_plain and fullauto_auto: keys are `candidates,
classification, completed_positions, segments,
selection_target_count, transcript, transcript_data`), and every
degraded segment reasoning is stamped *"Visual selection (AI analysis
unavailable): …"*.

## 5. Restart evidence (two independent legs)

1. **Unplanned, real:** the sandbox VM restarted mid-`fullauto_auto`
   (~72%, during renders; PostgreSQL/Redis binaries were dropped by
   the restart and restored by the runtime's package replay; the
   cluster data dir was intact). The task was finished through the
   app's own checkpoint resume (`process_task` re-invocation):
   10/10 clips, `completed_positions [1..10]`. Third independent
   resume proof in this acceptance series.
2. **Deliberate persistence leg:** a Manual Cutter task created with
   non-default settings (template `plain`, music `off`,
   `broll_enabled=false`) → **PostgreSQL server restarted** → a fresh
   process re-read the task through the real repository:
   `plain / off / false`, unchanged → rendered to completion → re-read
   again: still `plain / off / false`, status `completed`. Its exports
   are **byte-identical** to the manual_plain combo's
   (`fbf5af89…`, `08991f8b…`) — the exported clips' settings provably
   match the task's stored settings across a restart.
   (Evidence: `scratch-tip/out/persist-result.json`.)

## 6. Determinism cross-check (independent runs, same tip)

The floor slate and renders are deterministic: this run's hash sets
match the independent rendered-proofs batch run on the same tip
(`RENDERED_PROOFS_BATCH_NOV14.md`) — fullauto_plain clips include
`b3bf5194…` and `62f80d1a…`; fullauto_auto includes `ed1aaec0…` and
`02dbf7f5…`. Mode distinctness holds at the pipeline level: Manual
uses the user's ranges with no analysis; Full Auto never parks and
renders the full slate; both Find Moments modes park at
`awaiting_selection` with 10 candidates and render only the picks;
the pick renders are template-correct per mode (§3).

## 7. Per-combo run record

| Combo | Task id | Wall (run leg) | Final |
|---|---|---|---|
| manual_plain | `b45b617f-8338-49b9-8ce1-7c48285ee9d9` | 189.8 s | completed, 2 clips |
| manual_auto | `02df2f2e-796a-4a07-9bcb-b01cb7e9b653` | 257.8 s | completed, 2 clips |
| fullauto_plain | `ea4cbc06-7211-4214-b187-1e1e702b6412` | 1,853.1 s | completed, 10 clips (degraded) |
| fullauto_auto | `6ab4e5d2-79ec-4a35-91a2-c59627dad494` | 1,494.3 s + resume leg | completed, 10 clips (degraded, resumed) |
| momentsmanual_plain | `6cfccadc-bf4b-4456-a0a1-45f49f00a7cb` | 476.3 s → park; pick leg ~500 s | completed, 2 clips (degraded) |
| momentsmanual_auto | `67ed2bdf-db80-4f56-8e1f-2410b02830c7` | 535.1 s → park; pick leg ~562 s | completed, 2 clips (degraded) |
| momentsai_plain | `8b9ea5c1-2d49-41ba-83d6-e7da7e4b86be` | 473.5 s → park; pick leg ~498 s | completed, 2 clips (degraded) |
| momentsai_auto | `bc3011d1-0055-47ea-b654-1c328140e7f3` | 533.4 s → park; pick leg ~556 s | completed, 2 clips (degraded) |

## 8. Defects and observations

- **New product defects found: NONE.** No combo failed; no shipped
  claim was contradicted. F-R1 and F-R2 — the two MEDIUM defects the
  9ba6602 rerun filed — are both confirmed fixed at rendered level on
  this tip (§3, §4).
- **O-R3 (carried, known):** degraded floor exports carry no captions
  and no hook title (render log warns `No words or hook title
  available for ASS subtitles`, 9× in fullauto_plain) — already
  recorded as gap **G-V7-5** in the V7 validation plan. Not a new
  finding.
- **O-R4 (carried):** degraded pick renders carry no ClipPlan
  (expected with no AI planner; re-check on the user's machine with
  providers live).
- **Harness incidents (not product defects):** (a) the VM restart in
  §5.1; (b) the bare driver needs `src.models` imported before
  `init_db` (production imports models transitively via the app
  entry) and a forced exit after results are on disk (non-daemon pool
  threads hang interpreter exit — the first manual_plain driver
  lingered 10 min post-completion and was killed by exact PID; its
  results were already recorded). Both were fixed in the harness
  copy only.

## 9. Evidence index & class

Root: `~/workspace/jcs-accept-tip/scratch-tip/`

- `out/<combo>/result.json` (+ `pick-result.json`) — task ids, walls,
  fresh-session re-reads, checkpoint summaries (**integration** class).
- `out/<combo>/*.mp4` — the 32 exports (**rendered-video** class);
  `out/manifest.csv` = `exports_on_tip_manifest.csv` (hash/size/
  duration/streams for all 32).
- `frames/<combo>/<clip>/measure.json` + frame PNGs — YuNet/audio
  measurements for manual_plain c1/c2, manual_auto c1,
  fullauto_plain c1 (**rendered-video, measured**).
- `out/persist-result.json` — the persistence leg (§5.2).
- `logs/driver-*.log` (per-invocation copies preserved), `logs/
  chain.log` — pipeline + degradation lines (**integration**).
- Database `accept_tip` @ 127.0.0.1:55436 — task rows, checkpoints,
  `processing_cache` (§4 read directly from SQL).
- Scratch cluster: `~/workspace/.pg-accept-tip/` (PostgreSQL 16.15).

## 10. What still requires the user's machine (unchanged)

Real AI selection quality and ClipPlans (Full Auto / moments_ai with
providers live), audible Auto music beds, stock B-roll decisions,
the F-R2 live form (outage run → healthy re-run must call the AI —
the sandbox half is now proven: the stand-in never enters the
cache), Cloudflare live check, Eco-vs-normal benchmark, Bert/TJR
re-runs. These are the V7 validation plan's CRITICAL/HIGH tiers —
this report closes the sandbox-side acceptance gap (gate failure
(3)) and nothing more.
