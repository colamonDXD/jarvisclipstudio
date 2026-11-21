# BRAIN UPGRADE — the selection brain thinks harder and judges better

**Lane:** `feature-brain-upgrade` in `~/workspace/jcs-brain`, off
`next-milestone` @ `06ff0d4` (the gate-green tip). 9 commits, tip
`17f9efd`. **NOT merged — main merges.**
**Date:** 2026-11-11 (evening IST).
**Evidence base:** scout sweep `research_notes/finds-2026-11-11/REPORT.md`
(ADOPT items 1–3 + the brain answer), `tools/selection-eval/RUBRIC.md`,
`GOLDSET_REPORT.md`, `REPIN_REPORT.md`.
**Scope held:** prompt/request-shape + wiring only. No new services, no
Plain-mode change, no title-guardrail change, ₹0.

## Commits

| Commit | Change |
|---|---|
| `3ec8929` | feat(ai): premium selection passes think at high reasoning effort |
| `f767f96` | feat(ai): gated /100 rubric, hook calibration and gold few-shot in selection prompts |
| `7395882` | feat(ai): source title, channel and description reach the selection prompt |
| `88c4d7b` | feat(ai): scene-cut boundaries reach the selection prompt as hints |
| `5c5519d` | fix(ai): over-ask headroom is target+3 per selection call |
| `4d07915` | test(ai): brain-upgrade proofs (22 tests) + target-ask expectation |
| `6790e76` | test(ai): request-shape golden re-pin, byte-attributed |
| `ef0f4d3` | docs(changelog): user-visible entry |
| `17f9efd` | test(ai): chunk-rotation expectation follows the prompt's new size |

## Step 0 — premise checks (what existed before any change)

**(a) The selection request.** Built in `backend/src/ai.py`:
`transcript_analysis_system_prompt` (via `build_transcript_system_prompt`)
+ `build_transcript_analysis_prompt`. It carried: the timestamped
transcript; optional `clip_signals` (deterministic hints from
`media/timeline.build_clip_signal_summary` — trigger phrases, question/
emphasis marks, audio peaks; **no scene information**); optional
performance history. Scoring guidance was four 0–25 subscore band lists.
**It carried NO source title, NO channel, NO description, NO scene
boundaries.** The metadata existed one floor up —
`process_video_complete` fills `source_title`/`source_description` from
the YouTube preflight (`youtube_utils` returns title/description/
uploader) — but the title fed only classification + learned weights,
the description only classification, and `creator_name` reached only
`_validate_segments` (hook-title sanitising). The selector judged the
transcript blind.

**(b) The rerank.** `rerank_candidates` → `get_rerank_agent` (own
comparative system prompt) → `apply_candidate_ranking` (the `903b461`
fix: strictly-decreasing one-point step-down, no flattening). It runs
**only for multi-window analyses**; a single-window video's analysis
call is its own final pass. `_run_transcript_analysis` called
`agent.run(prompt)` — **no `model_settings` anywhere in the codebase**;
the only reasoning knob was `openrouter_reasoning: {effort: low}` on
the direct-OpenRouter model build. Every Groq selection call therefore
ran at Groq's default `medium` effort (scout-verified).

**(c) Golden pins.** Exactly one request-body golden exists:
`GOLDEN_SHORT_VIDEO_SHA256` in `test_ai_long_video_resilience.py`
(the `SHA256` hit in `test_source_submission.py` is an S3 URL string,
unrelated). It covers the single-window analysis request under
`LLM=openai:clip-fast`.

**(d) Over-ask / duration filter.** Duration hard-filter **already
existed**: `_validate_segments` repairs then drops anything outside
`clip_duration(clip_length)`'s [minimum, maximum]. The over-ask half
was short: `_segment_target_for` asked each call for its share of an
explicit target **+2**; without a target the per-window ask is the
built-in "2-5".

**(e) Reason + category in the output.** The per-clip why-it-works
**already existed** (`reasoning` + `virality_reasoning`). A virality
**category did not** — `hook_type` is a hook-archetype enum, not the
SamurAI category set (`gold_set.json`'s `categories_vocabulary`).

**(f) Scene cuts.** `auto_director/visual_moments.detect_scene_cut_times`
exists, never-fails (returns `[]`), and was **not wired into selection**
(it served reframing/visual-moments only).

## What shipped, per item

**1. Reasoning effort — WIRED.** `_premium_reasoning_settings()` returns
`{"extra_body": {"reasoning_effort": "high"}}` for `openai:` (gateway)
and `groq:` routes only; every other provider returns None and keeps
its own configuration (the OpenRouter path's `low` setting is
untouched). It is applied at exactly the two final-pass sites:
`rerank_candidates` (long videos) and the single-window analysis call
(short videos — that call *is* the final pass there: it writes the
slate and the hook titles in one shot). Window scans never receive it.
`_run_transcript_analysis` gained a `model_settings` kwarg threaded
through `_AnalysisFailover.run` (primary + failover candidates).
**Degrade safety:** if a route refuses the parameter —
`UnsupportedParamsError` by type name, or HTTP 400 naming
`reasoning_effort` — the call drops the settings and retries as a
plain call, inside the existing retry loop; a refused knob can never
fail a task, and unrelated errors are never retried by this branch
(proven by tests, incl. the pre-existing hard-400-fails-fast resilience
test staying green). Gateway side: `litellm_config.yaml` now
allow-lists `reasoning_effort` on both Groq routes (`clip-assured`,
`groq-20b`) per the scout's documented workaround.
*Design note:* the single-window premium decision spends high effort
on one call per short video; the alternative (rerank-only) would have
left every short video — the bulk of clipping work — at medium,
including its hook titles.

**2. Rubric + calibration.** The old VIRALITY SCORING section is
replaced by the gated rubric from `tools/selection-eval/RUBRIC.md`:
four hard gates (context-free first sentence — with the claude-shorts
red-flag phrases named verbatim; one complete idea; no
intro/outro/sponsor/crosstalk; no unowned media), then Hook 25 /
Stands-alone 20 / Payoff 20 / Emotion 15 / Specifics 10 / Ending 10,
ship ≥70, 55–69 only when nothing stronger exists, never under 55.
The stored schema keeps its four 0–25 subscores, so the prompt gives
an explicit conversion (hook = Hook; engagement = Emotion + Ending;
value = (Payoff + Specifics) × 25/30; shareability = Stands-alone ×
25/20; total = their sum — and `_validate_segments` already recomputes
the total from the subscores, so the shapes agree). Merged calibration:
the eight hook-archetype bands (Bold/Contrarian 80–100 … Weak/Generic
10–40, which also caps hook_score at 10), the +5–10 boosters (specific
number, named entity, personal-experience claim), and the ≥2-minute
spacing + subtopic-diversity slate rule. A compact form of the same
calibration (gates, archetypes, boosters, spacing) was added to the
**rerank** system prompt, whose comparative scores are the shipped
ones. The hook-title non-duplication rule from claude-shorts was
**already present** ("Do not simply repeat the first spoken words
verbatim; reframe them as a headline") and was left as-is.
**Category:** `ViralityAnalysis` gains an optional `category` field —
a Literal of the eight gold-set category names — named in the output
contract, the JSON-only key list and the prompt's scoring section,
which also requires `virality_reasoning` to name the category and the
concrete moment that earns the score.

**3. Context.** `analyze_transcript` now forwards `source_title` +
`source_description` (it already received both; only the title was
used, for learned weights) alongside the existing `creator_name`;
`process_video_complete` passes its `source_description` through.
`get_most_relevant_parts_by_transcript` builds one identical
`_source_context_section` into every analysis prompt (single-window,
windowed, token-budget-split): Title / Creator-channel / Description
lines for whichever fields exist, the description
whitespace-collapsed and truncated to 600 chars, the block framed as
metadata that must never be quoted as segment text. Metadata-less
sources produce no block and their prompts stay byte-identical in
that section.

**4. Scene boundaries.** `process_video_complete` detects cuts beside
the existing clip-signal summary (`run_in_thread(detect_scene_cut_times)`,
same never-fail discipline — any error means no hints this run) and
threads the times through `analyze_transcript` →
`get_most_relevant_parts_by_transcript` → every prompt build.
`_scene_boundaries_section` renders them compactly (MM:SS list,
deduped, rounded, filtered to the prompt's window, capped at 100 per
prompt) framed strictly as hints: prefer boundaries that coincide
with a sentence break; a mid-sentence cut is not a clip edge.

**5. Few-shot.** The five editor-validated Japan gold windows
(g1–g5: ranges, durations, as-run scores 92/90/88/86/84, one-line
why-it-works each) ride in the **system prompt** as CALIBRATION
EXAMPLES, explicitly framed as a different video's clips ("calibrate
your scoring against their shape, they are not candidates"). System
prompt placement keeps the block byte-identical across calls (the
RUBRIC.md adoption note: repeated-prefix caching then makes it
effectively free).

**6. Over-ask.** `_segment_target_for` headroom +2 → **+3** per call,
so a targeted pool always exceeds the target by at least three before
dedupe/rerank (the per-call cap of 12 stands; a single call carrying
a 30-clip target remains capped, as before). The duration half needed
no work (Step 0d).

**7. Golden.** Re-derived in-lane per the REPIN checklist (below).

## Golden re-derivation (item 7 proof)

Capture harness replicates `test_short_video_request_shape_is_unchanged`
exactly (same `configure_gateway` env, the test module's own
`make_transcript`, `FakeLLMServer`, `sha256(json.dumps(bodies,
sort_keys=True))`):

| Tree | Digest |
|---|---|
| base `06ff0d4` | `0c130b0c0653397d42d5905ee368bd0525b72421b664123ad1e2fff649996ab2` = the old pin — **exact match (faithfulness)** |
| lane tip | `9e4756151b9bc48322ec253dc97448323cb99525e93b66eed37348a8af466105` — **new pin** |

Full body diff (1 request both sides; `model`, `stream`, `tool_choice`
and the transcript payload byte-identical) — **every difference
attributed, no remainder**:

1. New top-level field `"reasoning_effort": "high"` ← commit `3ec8929`.
2. System message (12,266 → 15,868 chars): the VIRALITY SCORING
   section replaced by the gates/rubric/conversion/bands/boosters/
   category/slate-shape/few-shot block, and `"category"` added to the
   output contract's virality key list ← commit `f767f96`.
3. User message (7,958 → 7,970 chars): exactly one line changed —
   `"category"` added to the JSON-only Virality keys list ← `f767f96`.
4. Tool schema: the `category` property (enum of the eight categories,
   default null) added to the virality object ← `f767f96`.

The source-context and scene sections (commits `7395882`, `88c4d7b`)
are correctly **absent** — the golden scenario passes no metadata, and
the sections render only when data exists.

## Self-run (real Japan source, sandbox)

Drove the real selection path against the fake gateway on the cached
Japan transcript (`~/workspace/qm-source/source.transcript_cache.json`,
formatted by the pipeline's own `format_transcript_for_analysis`),
with **real scene cuts** detected on the real `source.mp4` (180 s
excerpt around the g1 window, offset-corrected; dozens of cuts found
in that window alone — restaurant footage cuts often; the per-prompt
cap of 100 and per-window filtering held).

- **Run A** — g1 neighbourhood (14:10–17:10 lines), single window,
  with title/creator/description + cuts: exactly 1 request; body
  carries `reasoning_effort: "high"`, the rubric and few-shot in the
  system prompt, Title + Creator/channel + Description in the source
  context block, the Scene boundaries section, and `category` in the
  tool schema. **All checks true.**
- **Run B** — all cached utterances stitched (multi-window): 3
  requests — 2 volume window calls with **zero** `reasoning_effort`
  anywhere in their bodies, 1 rerank call with
  `reasoning_effort: "high"`. **The two-pass economy holds end-to-end.**

## Test evidence (serial, TMPDIR on workspace, OMP_NUM_THREADS=2, logs read from disk)

New suite `test_brain_upgrade.py`: **22 passed / 0 failed** (settings
routing, both refusal shapes degrading, unrelated errors not retried,
rerank premium on gateway routes / default on local, prompt content
for rubric + calibration + few-shot + context + scenes, category
validation, over-ask values, and the two fake-gateway body tests).

Neighbours, all green: `test_ai_long_video_resilience` **10/10**
(with the re-pinned golden), `test_ai_prompt` 9, `test_ai_windowed_selection`
33, `test_target_clip_count` 32, `test_ai_rate_limit` 4, `test_ai_retry`
20, `test_rerank_nim` 1, `test_ai_token_budget` 2, `test_ai_grounding` 2,
`test_ai_models` 2, `test_title_person` 62, `test_creator_name_titles`
14, `test_litellm_gateway_config` 11, `test_video_service` 9,
`test_creation_modes_pipeline` 14, `test_processing_checkpoints` 11,
`test_learning_classifier_seam` 6. Frontend `changelog.test.ts` **4/4**
(via the standing temp node_modules symlink, removed after).
**Consolidated: 268 checks passed / 0 failed.**

### Existing tests changed — three, all deliberate re-derivations

1. `test_litellm_gateway_config.py` — the exact-match model pins gain
   `allowed_openai_params: ["reasoning_effort"]` on the two Groq
   routes (the config change itself; pins stay exact).
2. `test_target_clip_count.py` — `_segment_target_for` parametrize
   re-derived for +3 ((30,7) "4-7"→"5-8", (10,3) "3-6"→"4-7", (30,6)
   "4-7"→"5-8"; cap/floor cases unchanged) and the e2e literal ask
   text "Choose 4-7" → "Choose 5-8".
3. `test_ai_rate_limit.py` — chunk-rotation counts: the user prompt
   grew +12 chars (the category key line), which moves one
   token-budget chunk boundary in the fixture (verified: the test
   passes with four chunks at the lane base, five at the tip). The
   rotation assertion — exact round-robin order — is unchanged in
   strength; counts re-derived (5 chunks a/b/a/b/a, run counts 3/2).

No assertion was weakened anywhere; each change is commented in place
and stated in its commit body.

## Token-budget note (free pool)

- **Premium effort** spends reasoning tokens (billed as output on
  Groq; free pool 200K TPD for gpt-oss-120b) on **one call per video**
  in each shape: the rerank (long) or the single analysis call
  (short). Volume window scans stay at the provider default — the
  two-pass economy from the sweep, preserved.
- **Prompt growth:** the system prompt grew 12,266 → 15,868 chars
  (~+900 estimated tokens: rubric + calibration + few-shot). It is
  byte-identical across calls, so Groq's prompt caching (cached tokens
  don't count toward the limits, per the sweep) should make the
  repeated block effectively free after the first call of a burst;
  the user prompt grows only when metadata exists (context block
  ≤ ~800 chars incl. the truncated description; scene list ≤ ~600
  chars at the 100-cut cap).
- Net: the pool cost of a long video rises by roughly the rerank's
  extra reasoning output; the scan cost is unchanged in effort and
  near-unchanged in billed input.

## What remains user-machine validation

- **One live gateway call.** The sandbox has no Docker/LiteLLM, so
  the scout's must-do smoke test — a real premium call through
  Luffy's shipped gateway build proving `reasoning_effort` is
  forwarded, not rejected — can only run on his machine. If his
  gateway build does reject it, the degrade path turns it into a
  plain call and nothing fails; the check is whether the *benefit*
  lands, visible as `reasoning_effort` accepted in the gateway log
  for the rerank/single-window call of a fresh task.
- **Selection quality on a real run.** The rubric/few-shot/context
  changes are prompt-side; their proof is a fresh generation judged
  on its clips (the pending TJR proof run serves: Plain framing +
  niche + titles + now also pick quality), and grading a recorded
  live candidate list with `tools/selection-eval/score_selection.py`
  against the gold set (TRACKER 1.1's missing half).
- **Pool burn in practice.** Reasoning-token spend per premium call
  on real videos vs the 200K/day pool — observable from his gateway
  usage after a few generations; the design bound is one premium
  call per video.

## State

IMPLEMENTED / INDIVIDUALLY TESTED / INTEGRATED (all call paths wired:
single-window, windowed, token-budget-split, failover, brief and
non-brief tasks) / CROSS-FEATURE spot-verified in the self-run —
USER-MACHINE VALIDATION PENDING per the section above. Plain mode
untouched (it consumes the same selection); the first-person titles
guardrail (`title_person.py`) untouched.
