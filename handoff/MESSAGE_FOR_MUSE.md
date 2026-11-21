# From Claude (Lane A, backend AI) to Muse / Sanji — 2026-11-17

## 1. The patch you are waiting for (the one you saw at 9555a37 is STALE)
* Latest: branch `lane-a/ga1-gameplay-moments` @ **e8b5c5d** in `D:\SnipNova-Dev\claude-code` (13 commits on top of my snapshot `7e14b3f`).
* `7e14b3f` is NOT `20f903b`: the delivered copy had no `.git`, so I snapshotted the installed V9 tree (minus `.env`). File contents should match V9, so 3-way apply works.
* Files in this folder:
  * `series\0001…0013-*.patch` — one patch per commit, with messages. Recommended: `git am -3 series/*.patch` on your tip, so you can skip or reorder any commit.
  * `lane-a-reaction-intelligence.patch` — the same changes as one diff (`git apply -3`).
  * `HANDOFF.md` (contract, test commands, DeepSeek task list), `EVIDENCE.md` (synthetic vs real results, labelled).
* Expected conflicts on your tip: `CHANGELOG.md` (both of us added entries at the top — keep both), and possibly `litellm_config.yaml` / `test_litellm_gateway_config.py` / `docs/registries/MODEL_REGISTRY.md` if you already touched the NVIDIA routes (see 2).
* Hot file: `backend/src/services/video_service.py`, +24 lines, one block. It passes `frame_evidence` to the no-transcript selector (env `FRAME_EVIDENCE_SELECTION`, default on) and appends reaction hints to `clip_signals` (env `REACTION_SIGNALS`, default on). Both env flags set to `0` restore today's behaviour exactly. Needs your approval.

## 2. Your queued "check the five NVIDIA names" — already done, don't redo
The owner ran my probe (`tools/provider-audit/audit_providers.py --probe-nvidia`) on his real key, one 8-token call per model:
* WORKS: `nvidia/nemotron-3-ultra-550b-a55b`, `nvidia/nemotron-3.5-super-vl-120b-a12b`, `nvidia/nemotron-3.5-lightning-30b-a3b`, `nvidia/nemotron-3-nano-omni-30b-a3b-reasoning`, `nvidia/nemotron-3.5-bolt-e4b`, `mistralai/mistral-large-4-1t-a52b`
* HTTP 410 (retired): `deepseek-ai/deepseek-v4.1-flash`, `moonshotai/kimi-k2.6`, `meta/llama-4-maverick-17b-128e-instruct`, `deepseek-ai/deepseek-v4-pro` · 404: `qwen/qwen3-235b-a22b` · timeout: `z-ai/glm-5.3-flash`
* Commit `3b6b313`: ladder rung `nim-deepseek` → `openai/nvidia/nemotron-3.5-super-vl-120b-a12b`. Route name and ladder order unchanged; `clip-fast` stays Nemotron 3 Ultra (owner's instruction). Test pin + registry row updated in the same commit. Dead unarmed aliases annotated, not removed.
* Full provider status today: working = Groq, NVIDIA (above), OpenRouter free, Cloudflare Workers AI, Hugging Face, local Ollama qwen3:8b. Failing = DeepSeek (no credit; owner will not fund). Still yours: `HF_TOKEN` is not passed through compose.

## 3. What I aligned to YOUR vocabulary (commit e8b5c5d)
From your amendment and your merged two-pane composition:
* `edit_recommendations.framing.layout` = `stacked_reactor` | `pip_preserved` | `full`
* zoom `target` = `facecam` | `gameplay`
* `caption_plan.position` = `{anchor: "pane_seam" | "frame", preset: top|middle|bottom}` — presets are the app's own `clip_edits.CAPTION_POSITIONS`; `pane_seam` means "captions at the seam", like your render.

## 4. Field map for your REACTION_API_CONTRACT.md (I have not seen it)
| your contract term | my field (per moment) |
|---|---|
| fused score | `virality_score` (0-100; capped at 55 unless >= 2 of voice / facecam / context agree) |
| reason | `reasoning` (names the signals that fired) |
| per-signal evidence | `evidence.signals` (z-scores), `evidence.modalities`, `evidence.reaction_kind`, `evidence.voice_source` |
| context / trigger | `evidence.game_event {start_sec, end_sec, type}` or null |
| layout recommendation | `edit_recommendations.framing.layout` (+ `facecam_roi`) |
| boundaries | `start_time` / `end_time`, plus `evidence.lead_in_sec`, `tail_sec`, `boundary_notes` |
| hook | `hook_title` (grounded template), `evidence.hook_quote` (verbatim words, profanity mode applied) |
Everything is additive and optional; `silent_action` moments are low-score fallbacks that the selector drops.

## 5. What I need from you (please answer, I will build to it)
1. **Fixture v2 + its ground-truth file**: have the owner copy both into `D:\SnipNova-Dev\claude-code\test-footage\`. The amendment says GA1/GA3 are scored against exactly your ground truth; those legs are NOT RUN until then.
2. **REACTION_API_CONTRACT.md**: a copy in `D:\SnipNova-Dev\claude-code\docs\`, so I rename fields to your final names instead of you mapping them.
3. **Facecam source of truth**: your `reaction_layout` gate (now with the outside-the-inset motion check) vs my `reaction_moments.find_facecam` (YuNet, most stable face across 14 sampled frames). Proposal: your renderer keeps yours for layout; mine stays selection-only and reports `evidence.facecam_roi` as advisory. Or should selection reuse your gate output?
4. **Per-task switch**: my detectors are global env flags, default on. Should the reaction hints only run for tasks created through your Streamer Reaction card (or niche = gaming)? If yes, tell me the task field name and I will gate on it.
5. **Frontend display rule**: safe to show as measured facts: `moment_type`, `reaction_kind`, `modalities`, `voice_source`, `game_event`. Advisory only: `edit_recommendations`, `caption_plan`. To keep your "no fake reaction badges" rule, show a reaction badge only when `modalities` has two or more entries.

## 6. Test status of my branch (Windows, fcntl stub; you re-run on Linux)
* My own suites (reaction moments, hooks/recommendations, gameplay moments, visual moments, provider audit, gateway config, NIM bake-off): **all pass** (46 + 30 + 35 across runs; last full run of the lane files after e8b5c5d: 46/46).
* Every non-passing file in the affected set was re-run on the UNTOUCHED baseline (`7e14b3f`): identical results so far — the failures and Windows hangs are pre-existing, not from my branch:
  * fail on both: `test_billing_allowance` (1), `test_content_requirements_brief_image` (1), `test_recipe_create_wiring` (1), `test_video_utils_effects` (1, `test_burn_ass_subtitles_passes_selected_fonts_dir`)
  * hang on both (Windows): `test_analysis_failure_degradation`, `test_analysis_cache_modes`, `test_beat_pipeline`, `test_brand_kit_gaps`, `test_broll`, `test_clip_duration`, `test_video_service` (hangs at its first test with my features OFF too, and on baseline)
  * `test_task_service` 40 passed on both trees
* Not compared to baseline: `test_upload_ingest` (2 failed on my branch — upload spool/hard-link tests; my branch does not touch upload code; the baseline run was interrupted), and about 13 slow files of the comparison list.
* None of this replaces your Linux gate. Please run the full suite there after applying.

## 7. Honest limits
* Real-world quality is NOT verified: no labelled real VOD exists. On 9 public Twitch samples (Kai Cenat, IShowSpeed, a Spanish GTA streamer; local only, never shared), about 75% of picks contained the streamer exclaiming, but only about 15-21% of exclamation moments were caught. Precision is reasonable; recall is low.
* The game-dialogue mouth check demoted nothing on the real samples (no real cutscene false positive to prove it on).
* Hook titles are grounded templates, not model-written.
