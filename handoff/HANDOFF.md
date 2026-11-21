# Lane A handoff — streamer-reaction intelligence (2026-11-17)

Working directory: `D:\SnipNova-Dev\claude-code`  ·  branch `lane-a/ga1-gameplay-moments`
Git ancestry: **none inherited**. The delivered copy had no `.git`; baseline commit `7e14b3f` is a snapshot of the installed
tree minus `.env`, made by this agent. It is NOT `20f903b`. Lane A work = `git diff 7e14b3f..HEAD`.
Nobody has integrated this work. Sanji must apply and verify it.

## What is built (all backend, all in `backend/src/auto_director/` unless noted)
| file | purpose |
|---|---|
| `gameplay_moments.py` | seven per-second visual signals, agreement rule, episodes, windows. Game-agnostic. `mask_roi` flattens the facecam so the streamer's own movement is not read as a game event |
| `reaction_moments.py` | voice (vocal/pitch/laugh, harmonic-gated), facecam activity (YuNet cluster finder), game-event correlation, coincidence rule (>=2 of voice/face/context else score capped at 55), selection, `build_reaction_hints` for the LLM ranker |
| `reaction_hooks.py` | grounded hook titles, verbatim quote + profanity mode (keep/mask/drop), word/payoff-aware `refine_window`, `caption_plan`, `apply_ga2` |
| `edit_recommendations.py` | advisory framing/zoom/pacing/SFX data, schema v1 (documented in the module docstring) |
| `visual_moments.py` | `select_visual_moments(..., frame_evidence=False)`: when True tries streamer moments, then gameplay moments, then the legacy picker |
| `services/video_service.py` (HOT FILE) | passes `frame_evidence` (env `FRAME_EVIDENCE_SELECTION`, default on) and appends reaction hints to `clip_signals` (env `REACTION_SIGNALS`, default on). ~25 lines. Needs Sanji's approval |
| `tools/provider-audit/audit_providers.py` | keyless provider audit |
| `tools/gameplay-eval/` | fixture generators, scorers, `render_demo.py`, `EVIDENCE.md` |

## Moment contract (additions to the existing segment dict)
`hook_type` = `reaction_to_event | commentary_reaction | silent_facecam | silent_action` (or `hook_title` filled);
`evidence`: `{detector, moment_type, reaction_kind (laughter|shout|vocal_burst|silent_facecam), modalities[], signals{}, reaction_start_sec,
reaction_end_sec, game_event{start_sec,end_sec,type}|null, lead_in_sec, tail_sec, boundary_notes[], hook_quote?}`;
`caption_plan`: `{profanity_mode, emphasis[{start,end,style}], emphasis_words[], event_cue{at,align_to,not_audio_peak}|null, position{anchor: frame|pane_seam, preset: top|middle|bottom (app's clip_edits.CAPTION_POSITIONS), avoid[], reason}}`;
`edit_recommendations`: see `edit_recommendations.py` docstring. All times in `caption_plan`/`edit_recommendations` are clip-relative seconds.
`silent_action` moments are low-score fallbacks; `visual_moments` drops them.

## Test commands (run from `backend/`)
Windows needs a stub `fcntl` on PYTHONPATH and `NO_PROXY=localhost`; on Linux/Docker neither is needed.
`python -m pytest tests/unit/test_gameplay_moments.py tests/unit/test_reaction_moments.py tests/unit/test_reaction_hooks_and_recs.py tests/unit/test_provider_audit.py tests/unit/test_visual_moments.py -q --no-cov`
Eval tools: `python tools/gameplay-eval/make_streamer_vod.py <dir> <seed> <variant>` then `score_streamer.py <dir> 6 --known-roi`.

## Completed vs incomplete (against the Sanji amendment)
| item | status |
|---|---|
| GA1 premise check | DONE (below) |
| GA1 fusion, coincidence rule, hard-failure reporting | DONE for synthetic + real samples; real precision/recall NOT measured (no labels) |
| fixture v2 scoring | NOT RUN — fixture v2 never landed in `test-footage/` |
| GA2 ranking/reason, boundaries, hooks, caption plan, recommendations | DONE as data + tested; NOT wired into captions/render (Lane B / Sanji) |
| GA3 baseline evaluation on fixture v2 | NOT STARTED (blocked on fixture v2) |
| A1 chapters, A2 trailer, A3 variants, A4 whole-task edit, A5 calibration, A6 clean audio, A7 emoji audit, A8 speaker harness | NOT STARTED |
| provider audit | tool DONE and tested; live results NOT VERIFIED (needs owner keys) |

## GA1 premise check (file evidence, tree at 7e14b3f)
* facecam layout: `media/reaction_layout.py` `find_reactor_face` L33, `measure_reaction_inset` L90, `build_reaction_layout` L135, `choose_reaction_layout` L274; wired at `media/reframe_plan.py` L1241 and `media/reframing.py` L1572/L1595. This is a RENDER layout; it computes no reaction score.
* active speaker: `media/active_speaker.py` `mouth_aperture` L120, `pearson` L137, `windowed_correlations` L148; gated by `ACTIVE_SPEAKER_TARGETING_ENABLED` (L106-113), default OFF; correlates mouth aperture with speech audio, not reaction strength.
* no-transcript selection: `auto_director/visual_moments.py` `select_visual_moments` — audio energy peaks, beat-grid "drop" (fixed score 100), scene cuts. No frame content, no face, no voice character. It ranked a loud decoy first on the stand-in fixture.
* transcript path: `media/timeline.py` `build_clip_signal_summary` L824 — regex triggers, punctuation, speaker turns, top-8 loudest seconds as text hints. No laughter/pitch/face evidence.
* LLM prompt: `ai.py` L433 "Emotion and energy (15)" — the model is told to look for emotion but is given no measured reaction data.
Conclusion: reaction evidence did not exist as a ranking signal. This branch adds it.

## Evidence
* SYNTHETIC matrix (326 s, 8 scenarios): reaction detector found 4/5 (tuning) and 5/5 (held-out) positives, 0 false picks; V9 picker 3/5 with 3 false picks. Facecam position was supplied (YuNet does not recognise the cartoon face).
* REAL samples (Twitch, private, never committed; `test-footage/` is git-ignored): 8 five-minute samples from three streamers (Spanish GTA V, Kai Cenat GTA SA / GTA IV, IShowSpeed). Facecam auto-found in 8/8. 2-6 picks per sample.
  Checked by eye + faster-whisper transcript: Kai SA 04:49 "What the hell? That shit hurt... AHHHH" (reaction_to_event, score 95) = real scream at an event; Kai SA 03:56 "My back chair!" = real shouted outburst; vicens 7200@00:03 and @04:37 = arms-up celebration / face-palm (real physical reactions). Doubtful: Kai GTA4 02:17 (transcript was noise), IShowSpeed 02:02 (face motion, calm speech).
  NOT measured: precision, recall, misses, other games/languages.
* Rendered demo: `test-footage/renders/kai_scream.mp4`, `kai_my_back.mp4` (540x960, h264+aac, 11 s and 12 s, ffprobe-validated) made by `render_demo.py` from the picks' recommendations. It is a demonstration, not the product renderer; recommended zooms are not applied; the facecam panel is a 360p source upscaled.
* Timing on this laptop (CPU path): about 13-21 s per 5 min of video.

## Known defects / limits (be honest with the owner)
1. Game dialogue is voiced speech and can trigger voice signals (seen once). The coincidence rule helps; speaker separation is not built.
2. The laughter feature is weak on synthetic voices; "laughter" is only claimed when voice loudness corroborates it.
3. A first-person language model is not used for hooks; hook titles are template-based and generic by design.
4. `words` (transcript) is accepted by `select_streamer_moments` but the pipeline does not pass it yet.
5. Thresholds were tuned on the synthetic generator and a handful of real clips. Expect re-tuning on real labelled VODs.
6. YouTube sample download needs browser cookies (bot check); not used. Only Twitch VOD samples were fetched.
7. Full backend suite was run on Windows via an `fcntl` stub; see the result line in the final report. CI baseline (Linux) 4,939 passed is NOT re-verified on this branch.

## Remaining tasks for DeepSeek (do not let two agents edit the same files)
Order: 1. integration wiring, 2. A5/A6/A7 (small, independent), 3. A1, 4. A3/A4, 5. A8.
1. **Wire `words` and GA2 into the transcript path.** Files: `backend/src/services/video_service.py` (hot — ask Sanji first), `backend/src/auto_director/reaction_moments.py`. After transcription, call `select_streamer_moments(..., words=word_list)` and merge its `hook_title`/`caption_plan`/`edit_recommendations` onto the LLM's chosen segments by time overlap. Accept: a unit test with fake words shows quote + boundary snap on an LLM segment; REACTION_SIGNALS=0 leaves output byte-identical to today.
2. **A5 calibration** (`auto_director/learning.py`, new `calibration.py`): bounded per-hook-type multipliers incl. the new hook types. Accept: inert below the minimum-posts threshold; clamp and rollback tests.
3. **A6 clean-audio preset**, **A7 emoji audit** (`emoji_captions.py`): per the v2 brief; need render proof.
4. **A1 chapters** (new `services/chapter_service.py`, migration, tasks router): per v2 brief; visual-derived chapters may reuse `gameplay_moments.find_episodes`.
5. **A3 variants / A4 whole-task edit**: reuse ClipPlan + re-render seam; per v2 brief.
6. **A8 speaker harness**: needs the Bert/Japan fixtures; not in this copy.
7. **Fixture v2 scoring (GA1/GA3)** when Sanji's fixture and ground truth land: run `score_streamer.py` equivalents against his ground truth, retune thresholds in `reaction_moments.py` constants only.
Dependencies: none outside the repo (numpy/opencv already installed; faster-whisper optional). Never add paid APIs.

## Integration instructions for Sanji
1. Fresh tree at the intended base: `git apply --check <patch>` then `git apply <patch>` (patch = `git diff 7e14b3f..HEAD` excluding `.gitignore` noise is fine). Paths are repo-relative.
2. Review the 25-line `video_service.py` change (hot file). Disable with `FRAME_EVIDENCE_SELECTION=0` / `REACTION_SIGNALS=0`.
3. Run the five test files above, then the full suite on Linux; update request-shape goldens only if prompt hints change (the LLM prompt text itself is unchanged; only the `clip_signals` argument grows).
4. Renderer consumers read `moment["edit_recommendations"]`/`["caption_plan"]` as optional advisory data.

## Provider audit — LIVE result, run by the owner on 2026-11-17 (`audit_providers.py --live --infer --allow-metered`, keys never printed)
| provider | key | 1-token inference | finding |
|---|---|---|---|
| Groq | OK | OK | working |
| OpenRouter | OK | OK (free slug) | working |
| Cloudflare Workers AI | OK (account token) | OK | working. An earlier AUTH_FAILED was an audit-tool bug (only user-token endpoint tried); fixed in 529a936 |
| Hugging Face | OK | n/a | working |
| Ollama (local) | n/a | n/a | up, qwen3:8b present |
| NVIDIA NIM | OK | OK (first configured model) | 5 configured slugs are NOT in this key's model list: `deepseek-ai/deepseek-v4.1-flash` (route `nim-deepseek`, in the ladder), `moonshotai/kimi-k2.6`, `qwen/qwen3-235b-a22b`, `meta/llama-4-maverick-17b-128e-instruct`, `deepseek-ai/deepseek-v4-pro`. No deepseek/kimi/qwen3 ids listed at all. Those routes will fail over; verify names on build.nvidia.com before relying on them |
| DeepSeek | OK | **OUT_OF_CREDIT (402)** | balance is zero, so the metered primary `deepseek-chat` cannot answer. Its model list shows `deepseek-flash` and `deepseek-v4-pro`; `deepseek-chat` is NOT listed (may be a retired alias). Not verifiable until credit exists |
Effective ladder today (verified): deepseek-chat DOWN (no credit) -> Groq 120B UP -> NIM Nemotron Ultra UP -> nim-deepseek DOWN (slug) -> OpenRouter free UP -> Cloudflare free UP -> Groq 20B (same key as Groq, not separately tested) -> local qwen3:8b UP.
Net: the app runs at zero cost; Groq 120B is the real primary. No new keys are required. Open items (owner/Sanji decisions, not mine): whether to buy DeepSeek credit and rename the route to `deepseek-flash`; replacing the unlisted NIM slugs. `HF_TOKEN` is still not passed through compose.

## Afternoon additions (2026-11-17)
* `3b6b313` gateway: rung `nim-deepseek` now `nvidia/nemotron-3.5-super-vl-120b-a12b` (DeepSeek V4.1 Flash returned HTTP 410 on the owner's
  live probe). Ladder order unchanged; `clip-fast` stays Nemotron 3 Ultra. Dead unarmed aliases annotated. Test pin + MODEL_REGISTRY updated.
* `e4e336f` reaction intelligence: transcript reaction-word signal (`lexical`) and facecam mouth check for game dialogue.
  `build_reaction_hints` loads the cached transcript words itself — `video_service.py` did not change for this.
* NVIDIA probe (owner-run): WORKS = nemotron-3-ultra-550b-a55b, nemotron-3.5-super-vl-120b-a12b, nemotron-3.5-lightning-30b-a3b,
  nemotron-3-nano-omni-30b-a3b-reasoning, nemotron-3.5-bolt-e4b, mistralai/mistral-large-4-1t-a52b. GONE (410): deepseek-v4.1-flash,
  kimi-k2.6, llama-4-maverick, deepseek-v4-pro. NOT_FOUND: qwen3-235b-a22b. TIMEOUT: z-ai/glm-5.3-flash.
* Working providers right now: Groq, NVIDIA (models above), OpenRouter free, Cloudflare Workers AI, Hugging Face, local Ollama qwen3:8b.
  Failing: DeepSeek (no credit; owner will NOT fund it).
* New DeepSeek-lane task: recall on real streamer VODs is low (see EVIDENCE.md round 2). Needs a labelled real set
  (start with the owner's own clips) before any threshold change. Do not loosen the two-signal rule without one.

## Vocabulary alignment (2026-11-17, commit after 07f8d4d)
`edit_recommendations.framing.layout` now uses the amendment's names: `stacked_reactor` (reactor pane on top, gameplay below — Sanji's merged composition), `pip_preserved`, `full`. Zoom `target` is `facecam` or `gameplay`. Caption position uses the app's presets and `anchor: pane_seam` for the stacked layout. Old names (`facecam_split`, `game_with_facecam_inset`, `game_only`, `center`) are gone; nothing had integrated them.
