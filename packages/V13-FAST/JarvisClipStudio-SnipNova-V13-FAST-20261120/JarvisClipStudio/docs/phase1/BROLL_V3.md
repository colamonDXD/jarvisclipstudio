# Phase 1 — Smart B-Roll v3 (the "1 insert in 165 s" repair)

Branch `lane-a/phase1-broll` in `D:\SnipNova-Dev\claude-code`. Off switch: `BROLL_CADENCE=legacy` restores v2 exactly.

## Root cause (measured in Phase 0)
The planner capped cutaways at `max(1, round(clip_seconds/60 × density_per_min))` with density 1.5–2.0/min, so a 30–45 s
clip got exactly **one** planned insert; the keyword path scanned ~25 nouns and fired each once per clip. Guards then
dropped more. The "lost insert" half (silent drops) was already fixed in V9 (`not_inserted` stamps); the count half was not.

## What changed
| Change | File | Notes |
|---|---|---|
| Cadence v3 | `auto_director/edl.py` `broll_target_count`, `broll_uses_v3`; 8 profiles `broll.cadence_per_min` | finance 4.0/min, podcast/Hindi podcast/education 3.5, true crime 3.0, vlog 4.0, travel/Japan 4.5. Same number feeds the AI prompt cap, validation and placement |
| Phrase-level cue source | new `auto_director/broll_lexicon.py` | 116 visual concepts, ~840 trigger phrases, each with 2–3 stock search phrases (>= 2 words); sense context for trap words (market, bank, interest, train, pitch…); name guard (capitalised mid-sentence words, "Steve Jobs", "Tim Cook"); niche fit outranks phrase length; generic concepts need topic support; no repeat within 45 s |
| AI plan keeps priority, lexicon tops up | `auto_director/clip_plan.py` `apply_plan_broll_concepts` | When the AI plan writes fewer concepts than the cadence, lexicon cues fill the rest after the plan's own, never within 5 s of a plan anchor |
| Visual protection | `edl.py` `_plan_broll_segments` | No cutaway over a spoken demonstration ("look at this", "this chart", "let's look at") → drop reason `demonstration`; a live reaction (haha, wow, "!") turns the cue into picture-in-picture; tutorial profile defaults to PiP |
| Picture-in-picture render | `media/edl_render.py` | Framed inset (max 692×560, white 8 px frame) with its bottom edge at 70% of the height, above the caption band; cutaway graph unchanged byte-for-byte |
| Decision log | entry fields | `spoken`, `cue_source` (`plan`/`lexicon`), `lexicon_id`, `layout`, `layout_reason` |
| Diagnosability | `media/pixabay_broll.py` | A rejected key / rate limit / outage was stamped `provider_empty` ("nothing relevant"); now `provider_error` |
All guards are unchanged and apply to every v3 cue: 2 s hook guard, no cue over payoff/conclusion phases or opening on the
final line, 4 s of face between cutaways, coverage cap (30%, travel 40%), end guard.

## Evidence (OFFLINE — real videos, production planner + production renderer, placeholder footage)
Script: `tools/phase1/broll_offline_evidence.py`. 3 real videos × 3 clips of 40 s:

| Video | Niche | Inserts per clip v2 → v3 |
|---|---|---|
| "Investing 101" (YouTube `wIxRKlb_1uk`, Creative Commons Attribution) | finance | 1→2, 1→2, 1→1 |
| "Claude Code Can Now Clip ANY Video" (private test use) | podcast/news | 1→1, 1→1, 1→2 |
| "Best AI Clipping Tools in 2026" (private test use) | education (PiP) | 1→1, 1→1, 1→2 |
| **Total (360 s of real speech)** | | **9 → 13** |

Every v3 insert with what was said (human review, Claude):
| Speaker said | Searched | Verdict |
|---|---|---|
| "market" / "stock market" / "stocks" | stock market chart / trading screen | relevant |
| "interest rate" | central bank building exterior | relevant |
| "investment" | investment portfolio on laptop | relevant |
| "claude" (twice) | artificial intelligence digital brain | relevant (topic is AI) |
| "camera" (camera cuts) | content creator recording camera | acceptable |
| "branding" | marketing team planning | weak (since fixed: needs marketing context) |
| "credit cards" (Apple privacy) | credit card payment terminal | acceptable |
| "computer(s)" ×2 | person typing on laptop keyboard | acceptable |
| "university" (Wozniak's degree) | students in classroom | acceptable |
False positives found during review and fixed before this table: "Tim Cook" → chef, "interview footage" → job interview,
"Sony in Japan" → Tokyo street, "Steve Jobs" → office, "skill file" → learning, "write down" → notebook.

Renders: all three 1080×1920, 40.0 s, H.264 + AAC (ffprobe). Frames checked by eye: full-frame cutaway at the planned
time; PiP inset with the face visible.

## NOT verified yet (needs the owner's run — `tools/phase1/broll_live_evidence.py`)
* Whether real Pixabay/Coverr footage for these phrases is relevant (stock hit rate, SigLIP veto rate, download success).
* Counts with the AI concept writer in the loop (the gateway runs inside Docker; this local run is lexicon-only).
* On the owner's 165 s finance video specifically: the planner would target up to 12; offline evidence suggests ~6–7 land
  in keyless mode, more with AI concepts. Only a real run can say.

## Known limits
* PiP position is fixed (lower-middle); in sources where the face sits low (a screen-recorded phone video) it can cover
  the chin. Face-aware placement needs the reframe stage's face boxes (future).
* Protection is transcript-based; the reframe stage's per-shot face/text evidence is not yet available to the planner.
* The keyless cue source is a curated lexicon; topics outside it get no B-roll (by design: no insert beats a wrong one).

## Contract additions for Sanji (all optional fields)
* `edl.broll[]`: `layout` (`cutaway`|`pip`), `layout_reason`, `spoken`, `cue_source` (`plan`|`lexicon`), `lexicon_id`.
* `edl.broll_dropped[].reason`: new value `demonstration`. `not_inserted.reason`: `provider_error` now also covers rejected keys.
* Profile: `broll.cadence_per_min`, `broll.layout`. Env: `BROLL_CADENCE=legacy`.
* Suggested review UI copy: "B-roll at 0:20 — speaker said 'stock market' → stock market trading screen (Pixabay #…)".

## Tests (Windows, fcntl stub; Linux gate is Sanji's)
* New: `backend/tests/unit/test_broll_v3_cadence.py` — cadence math and legacy switch, prompt/validation cap, lexicon
  well-formedness, the real TJR clip, 9 sense/name traps (5 found on real speech), guards on the v3 path, payoff phases,
  demonstration drop, reaction PiP, tutorial PiP default, AI-plan priority + lexicon top-up, PiP render graph, cutaway graph
  unchanged, rejected key → `provider_error`.
* Pin changes (Sanji re-checks): 19 tests that assert v2's exact cues/counts on real profiles now run under the
  `v2_cadence` fixture (`BROLL_CADENCE=legacy`), with no assertion changed — in `test_broll_concepts.py` (7),
  `test_broll_audit_fixes.py` (2), `test_broll_word_sense.py` (2), `test_broll_slice5_density.py` (7),
  `test_broll_resolver_v2.py` (1). Proof they encode v2 only: all 19 pass unchanged with `BROLL_CADENCE=legacy`; under v3
  they fail only on cue identity/count (e.g. `'business growth chart rising' == 'market'`, `[8.0, 51.0, 32.0] == [9.5, 17.0]`).
* Pre-existing on the untouched baseline too: `test_edl_render.py` 3 info-card drawtext failures (Windows fonts).

## Found, not fixed (belongs to packaging/render owner)
The NVENC probe (`media/ffmpeg.py` `check_nvenc_realistic`) passes `-filter_complex_script`, which FFmpeg 8-era builds
removed. On such a build the probe fails and every render silently falls back to the CPU encoder. Production renders pass
the graph inline and are unaffected. Fix: pass the probe graph inline; `tests/unit/fake_ffmpeg_shim.py` and
`test_nvenc_probe_realistic.py` key on the option and need updating with it. Check which FFmpeg the Docker image ships.

## LIVE evidence (owner-run, real Pixabay/Coverr, 2026-11-17)
Run 1 (`live_run1_noveto`): SigLIP check OFF by mistake (script did not set `BROLL_VISION_VETO_ENABLED`, which Docker sets).
4/4 cues filled; 3 relevant, 1 wrong ("credit card payment terminal" -> airport walkway tagged "terminal").
Run 2 (`live`, after 6df5b15): SigLIP check ON, as deployed. 4/4 cues filled, check ran on all 4.
| Speaker said | Searched | Pixabay asset | Shot (checked by eye) | Verdict |
|---|---|---|---|---|
| "market" | stock market chart screen | 166289 | 3D red bar chart | relevant |
| "stock market" | stock market trading screen | 259819 | phone with candlestick chart, UP/DOWN | relevant |
| "claude" (AI talk) | artificial intelligence digital brain | 176794 | neon circuit brain | relevant |
| "credit cards" | paying with credit card | 304598 | rows of chip cards, one glowing (PiP, face visible) | relevant |
Result: 4/4 relevant on real footage. Renders 1080x1920, 40 s, H.264 + AAC. Licence: Pixabay Content License.
Limits: small sample (4 cues, 3 videos); keyless planning (no AI concepts); the check rejected 0 candidates, so its
rejection path was not exercised in this run (it was calibrated separately: 100 pairs, 1 miss, 6 false vetoes).
