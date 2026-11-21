# SYSTEM_AUDIT — what SnipNova already has (V9 tree + Lane A branch)

Phase 0 deliverable, 2026-11-17. Tree audited: `D:\SnipNova-Dev\claude-code` (V9 snapshot `7e14b3f` + Lane A commits up to `e8b5c5d`).

Labels: **WORKING AND VERIFIED** · **IMPLEMENTED BUT UNVERIFIED** · **PARTIAL** · **BROKEN** · **MISSING**.
"Verified" here needs rendered or measured evidence on real footage. Where my only source is the repo's own record
(`TRACKER.md`, `FEATURE_PROGRESS.md`, `BUG_AUDIT.md`, the 86-row feature map F01–F86), the row says "per repo record".

## Summary

| # | System | Label | One-line reason |
|---|---|---|---|
| 1 | Smart B-Roll v2 (placement) | **PARTIAL** | Works as designed, but the design caps inserts at ~1 per 40 s clip; measured below |
| 2 | Pixabay / Coverr integration | IMPLEMENTED BUT UNVERIFIED | Resolver + cache + provider stamps exist; live yield on the owner's keys never measured |
| 3 | Vision veto (SigLIP) | WORKING AND VERIFIED (calibration only) | 100 judged pairs: 1 miss, 6 false vetoes (per repo record); not re-run by me |
| 4 | Niche transitions + 14 profiles | PARTIAL | 58 FFmpeg xfade presets + 2 overlays; no rendered proof reel; profiles mostly hard-cut |
| 5 | Viral-editing pack (Viral Intelligence plans) | IMPLEMENTED BUT UNVERIFIED | Per-clip LLM plan (hook, opening, B-roll concepts, phases); F40 INV |
| 6 | Beat-grid music sync | IMPLEMENTED BUT UNVERIFIED | `beat_grid.py` + beat-snapped selection; no real export review (F05 INV) |
| 7 | Karaoke captions | WORKING AND VERIFIED | Word-synced highlight, 11 templates; F54/F55 VERIFIED per repo record |
| 8 | Hook and title system | WORKING AND VERIFIED | F41 VERIFIED per repo record; Lane A adds reaction hooks (unit-tested) |
| 9 | Niche auto-director | PARTIAL | 14 profiles, classifier (F11 VERIFIED); profile-specific editing never reviewed on real clips per niche |
| 10 | Streamer-reaction intelligence | IMPLEMENTED BUT UNVERIFIED | Lane A branch; synthetic + 9 real Twitch samples, no labelled real set |
| 11 | Gameplay-event detection | IMPLEMENTED BUT UNVERIFIED | Lane A branch; stand-in fixture only |
| 12 | Clip boundaries + dead air | PARTIAL | Silence/filler tightening VERIFIED (F04); boundary quality not measured on real clips |
| 13 | Edit-recommendation system | IMPLEMENTED BUT UNVERIFIED | Lane A data contract (layout/zoom/pacing/SFX); no renderer consumes it yet |
| 14 | Active-speaker + reframing | PARTIAL | Face reframe VERIFIED (F34); active-speaker default OFF, never validated (F35) |
| 15 | Export + render pipeline | PARTIAL | Renders work; 9:16 only; owner's machine renders CPU-bound (RAM 99%, GPU ~0% per repo record) |
| 16 | Audio, SFX, music libraries | PARTIAL | Ducking, loudnorm, Audio Studio exist; music/SFX libraries ship EMPTY by licence policy |

## 1. Smart B-Roll v2 — PARTIAL (the known 1-in-165 s issue)

* **Files:** `backend/src/auto_director/edl.py` `_plan_broll_segments` (L732), `_create_broll_queries`, `_concept_broll_queries`;
  `auto_director/clip_plan.py` `broll_concepts` (LLM concept writer, L347/L502/L913); `media/pixabay_broll.py` (resolver v2);
  `media/coverr_broll.py`; `media/broll_vision.py` (veto); profiles `auto_director/profiles/*.json` (`broll.density_per_min`).
* **Intended:** illustrate what is said with stock cutaways, never over the hook, payoff or closing line.
* **Is the 165 s bug fixed in this copy?** The *silent-loss* half is fixed: V9 contains the retention fix — every dropped
  cue is recorded (`edl["broll_dropped"]`, `not_inserted {stage, reason}`, `BrollUsageLedger`). The *low count* half is
  **not fixed, because it is the design**:
  * Niche density is 1.5/min (podcast, finance, education, true crime, Hindi podcast) or 2.0/min (vlog, travel, Japan);
    gaming, sports, comedy, music, anime, football have B-roll OFF.
  * The planner targets `max(1, int(clip_seconds/60 × density + 0.5))` cues **per clip**: a 30–45 s clip gets **1**,
    a 60 s clip 2. Four ~41 s finance clips = at most ~4 planned for the owner's 165 s run, before any guard or resolver drop.
  * The keyword path scans a fixed list of ~25 nouns plus an extension list, and each noun fires **once per clip** (first
    occurrence). The concept path (LLM plan) places a concept only at its anchor and never falls back.
  * Guards then remove more: 2 s hook guard, no cue over payoff/conclusion phases or opening on the final line, 4 s of face
    between cutaways, 30% coverage cap.
* **Measured today (offline, real speech, production planner, 40 s clips, keyword path):**

| Source (real speech) | Density | Clips | Placed | Per clip | Placement drops |
|---|---|---|---|---|---|
| "Claude Code Can Now Clip ANY Video" (499 s) | profile 1.5/min | 12 | 12 | 1.0 | none (density cap binds) |
| same | 4.0/min | 12 | 20 | 1.7 | overlap 4, face_return 2, cue_too_short 1 |
| "Best AI Clipping Tools 2026" (381 s) | profile 1.5/min | 9 | 5 | 0.6 | none (no trigger noun in 4 clips) |
| same | 4.0/min | 9 | 6 | 0.7 | face_return 1, overlap 1 |

  Script: `tools/phase0/broll_placement_sim.py` (reads `test-footage/references/transcripts.json` from `tools/phase0/transcribe_references.py`) (word timings approximated from Whisper segments). This measures the
  **placement stage only**. Resolution (stock search hit rate, veto, download) needs the owner's Pixabay/Coverr keys.
* **Conclusion:** reaching ~10 relevant inserts in 165 s (~3.6/min) needs (a) per-niche density raised to ~3–4/min for
  talking niches, (b) more and better cue sources (concept writer on the free model ladder, multiple occurrences, noun
  phrases, not a 25-word list), (c) a measured resolver yield on the owner's keys, with the per-stage drop reasons that
  already exist. Raising density alone gets 1.7/clip at best on real speech.
* **Tests:** 15 test files named for B-roll. **Rendered evidence:** sandbox cross-feature proof per repo record; the only
  real-machine evidence is the negative 1-in-165 s run.
* **Recommendation:** REPAIR + EXTEND (Phase 1). Keep the guards and the stamping; change cadence and cue generation.

## 2. Pixabay / Coverr — IMPLEMENTED BUT UNVERIFIED
* **Files:** `media/pixabay_broll.py` (resolver v2: Pixabay → Coverr, provider-stamped, per-task ledger), `media/coverr_broll.py`.
  Legacy `broll.py` (Pexels, editor path) consolidated to the v2 route per repo record (`refactor/broll-consolidation`).
* **Works:** code paths, caching, attribution metadata fields (per repo record). **Unproven:** hit rate and relevance on the
  owner's keys; Coverr credit display. **Recommendation:** REUSE; add a live yield report (queries, hits, vetoes, inserts).

## 3. Vision veto (SigLIP) — WORKING AND VERIFIED (calibration)
* **Files:** `media/broll_vision.py`. ON in Auto, fail-open, Plain exempt.
* **Evidence (repo record):** 100 judged pairs, 1 missed-wrong (1.0%), 6 false vetoes (6.0%), no wrong-domain.
* **Recommendation:** REUSE as the relevance gate for the new B-roll cadence; re-measure false-veto rate at higher density.

## 4. Transitions + 14 niche profiles — PARTIAL
* **Files:** `media/edl_render.py` `XFADE_TRANSITIONS` (58 FFmpeg xfade built-ins), `TRANSITION_MAP`;
  `auto_director/signature_transitions.py` (per-niche signature join, 0.15–0.8 s, transition→SFX mapping);
  `backend/transitions/*.mp4` (2 overlay transitions); `zoom_bounce`; join discipline (hard cuts inside continuous speech).
* **Works:** xfade joins render (tests: 3 files named for transitions). **Unproven:** no rendered proof reel per family,
  no measured visual distinctness. **Honest count today: 58 xfade presets + 2 overlays + 1 zoom bounce = 61 candidates,
  0 with rendered per-variant proof.**
* **Recommendation:** EXTEND (Phase 3): proof reel first, then new families (whip/motion-blur, glitch/RGB split, light leak,
  shake, mask/luma wipes, speed ramp) with a distinctness metric.

## 5. Viral-editing pack (Viral Intelligence Engine) — IMPLEMENTED BUT UNVERIFIED
* **Files:** `auto_director/plan_service.py`, `clip_plan.py` (ClipPlan: hook, opening, phases, B-roll concepts),
  `velocity_curves.py`, `retention.py`; doc `docs/viral-intelligence.md`. F40 INV.
* **Recommendation:** REUSE as the per-clip decision layer of the one-click editor; it is where B-roll concepts come from.

## 6. Beat-grid music sync — IMPLEMENTED BUT UNVERIFIED
* **Files:** `auto_director/beat_grid.py` (`analyse_audio_beats`, energy curve, drop), beat-snapped windows in
  `visual_moments.py`. Measured today: found a 190 BPM grid on the one music-led reference reel; no beat on speech reels.
* **Recommendation:** REUSE; needs licensed music in the library before any audible proof is possible.

## 7. Karaoke captions — WORKING AND VERIFIED
* **Files:** `media/captions.py` (word-synced highlight, `KARAOKE_GAP_BRIDGE_SECONDS`), `caption_templates.py` (11 templates),
  `media/hinglish.py`. F54/F55 VERIFIED per repo record.
* **Gap:** template range (competitors: dozens to 100+); keyword highlight is per template, not semantic ("billionaire").
* **Recommendation:** EXTEND (semantic keyword emphasis; more templates) — UI selection is Sanji's.

## 8. Hook and title system — WORKING AND VERIFIED
* **Files:** `ai.py` (`hook_title`, `sanitize_hook_title`), `auto_director/hook_title_styles.py`, `hook_variants.py`,
  `hook_enforcement.py`, `video_utils.build_hook_title_ass`; Lane A `reaction_hooks.py`.
* **Recommendation:** REUSE.

## 9. Niche auto-director — PARTIAL
* **Files:** `auto_director/niche.py` (14 profiles), `classify.py`, `plan_service.py`, `scene_rules.py`, `template_mode.py`.
  F11 niche classification VERIFIED per repo record.
* **Gap:** no rendered real-video export per niche on record (the brief's evidence standard).
* **Recommendation:** EXTEND into the one-click editor (Phase 2) with one real export per niche.

## 10. Streamer-reaction intelligence — IMPLEMENTED BUT UNVERIFIED
* **Files (Lane A branch):** `auto_director/reaction_moments.py`, `reaction_hooks.py`, `edit_recommendations.py`; render side
  `media/reaction_layout.py` (Sanji's gate fix on his tree).
* **Evidence:** synthetic matrix 4/5 and 5/5, 0 false picks; 9 real Twitch samples: facecam found 9/9, ~75% of picks contain
  exclamations, ~15–21% of exclamation moments caught (weak labels). **Recommendation:** KEEP; add Twitch chat-spike signal.

## 11. Gameplay-event detection — IMPLEMENTED BUT UNVERIFIED
* **Files:** `auto_director/gameplay_moments.py` (Lane A), `visual_moments.py`. Stand-in fixture 4/4 + decoy rejected.
* **Recommendation:** KEEP; validate on Sanji's fixture v2 (his integration task).

## 12. Clip boundaries + dead air — PARTIAL
* **Files:** `media/timeline.py` (tightening, fillers count as silence), `clip_openings.py`, `clip_duration.py`,
  `auto_director/mistake_fix.py`; Lane A `reaction_hooks.refine_window`. F04 VERIFIED per repo record.
* **Gap:** "does the clip contain the beginning and end of the thought" is not measured on real clips.
* **Recommendation:** EXTEND with a boundary-completeness check in QC.

## 13. Edit-recommendation system — IMPLEMENTED BUT UNVERIFIED
* **Files:** Lane A `auto_director/edit_recommendations.py` (schema v1: layout stacked_reactor/pip_preserved/full, zooms,
  pacing, SFX cues); existing typed edit ops `auto_director/clip_edits.py`, `ai_edit.py`.
* **Recommendation:** EXTEND into the one-click editor's decision record; renderer consumption is Lane B/Sanji.

## 14. Active-speaker detection + reframing — PARTIAL
* **Files:** `media/active_speaker.py` (`mouth_aperture`, `windowed_correlations`, flag default OFF), `media/reframing.py`,
  `reframe_plan.py`, `speaker_framing.py`, `media/face_detection.py` (YuNet), `talking_head_layout.py`.
* **Recommendation:** REPAIR/VALIDATE (A8 harness) before enabling; re-centre on every camera cut (reference technique 8).

## 15. Export + render pipeline — PARTIAL
* **Files:** `media/edl_render.py` (`render_edl_clip_ffmpeg`, `get_video_encoder`), `media/ffmpeg.py`
  (`check_nvenc_available`, `build_final_video_encode_args`), `media/common.py` (NVENC p4), `clip_editor.EXPORT_PRESETS`,
  `media/clip_qc.py` (QC + corrective re-render, F09 VERIFIED).
* **Gaps:** presets are 9:16 only (no 16:9, no 1:1, no 4K); on the owner's machine renders were CPU-bound with RAM at 99%
  and GPU ~0% (per repo record, 2026-11-15) — NVENC availability on his machine is unmeasured.
* **Recommendation:** EXTEND (export profiles) + MEASURE (NVENC/NVDEC on the RTX 4050).

## 16. Audio, SFX and music libraries — PARTIAL
* **Files:** `media/music_library.py` (folder-driven, `select_auto_bed` by mood stem), `media/sfx_library.py`,
  `auto_director/sfx.py`, `auto_director/audio_studio.py` (afftdn/clarity/loudnorm), `media/edl_render.py` ducking
  (`sidechaincompress`), Demucs separation (double-gated OFF), Kokoro voice-over.
* **Gap:** music and SFX libraries ship **empty** (policy: no unverified audio). Auto selection is a mood stem from the
  niche profile, not content analysis. No loudness target check against platforms (-14 LUFS observed in all references).
* **Recommendation:** EXTEND: content-aware mood/energy selection, licensed-source intake with provenance (owner action),
  loudness QC.

## Test baseline caveat
On Windows (fcntl stub) several suites hang or fail identically on the untouched baseline (e.g. `test_video_service`,
`test_billing_allowance`); the full gate must run on Linux (Sanji). Lane A's own suites pass.
