# CLAUDE_CHECKPOINT — SnipNova transformation (written 2026-11-17, evening IST)

Resume point for any new Claude session. Read this first, then `docs/phase0/IMPLEMENTATION_PLAN.md`.

## 0. Roles and hard rules (unchanged)
* **Claude = main driver** on Luffy's (owner's) Windows laptop: builds, runs, renders, tests, proves everything here.
* **Sanji (Muse)** is remote: only receives the handoff folders Luffy uploads, verifies on his side (Linux gates), merges,
  wires frontend, ships packages (V10 shipped; it does NOT contain Claude's work). Never assign on-machine steps to him.
* **OpenCode** (DeepSeek model) = the only other coding agent; only tasks Claude delegates; Claude reviews before handoff.
* Work ONLY in `D:\SnipNova-Dev\claude-code` (git). NEVER touch `D:\EditedTool\JarvisClipStudio` (installed app), its
  `.env`, database, Docker volumes, user assets. ₹0 spend. No Gemini. Never print/commit keys. New behaviour behind an
  off switch. Existing tests must not break (compare any failure with the untouched baseline before calling it ours).
* Commits end with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.

## 1. Repository state
| Item | Value |
|---|---|
| Working tree | `D:\SnipNova-Dev\claude-code` |
| Current branch | `lane-a/phase2-auto-editor` @ `545ce39` (+ this checkpoint commit) |
| Uncommitted changes | none at checkpoint time except this file (committed with it) |
| Baseline | branch `baseline-v9` @ `7e14b3f` = snapshot of installed V9 (NOT Sanji's `20f903b`); worktree `D:\SnipNova-Dev\baseline-wt` (detached, used for baseline test comparisons) |
| Lane A (reaction/gameplay detection, provider audit) | `lane-a/ga1-gameplay-moments` @ `e8b5c5d` |
| Phase 0 (study/audit/plan) | `lane-a/phase0-audit` @ `0b74b74` |
| Phase 1 (B-roll v3) | `lane-a/phase1-broll` @ `48dd1e9` (commits `4d4487b`, `6df5b15`, `48dd1e9`) |
| Phase 2 (WIP) | `lane-a/phase2-auto-editor` @ `545ce39` (branched from `48dd1e9`) |
| OpenCode | worktree `D:\SnipNova-Dev\opencode`, branch `lane-b/render-basics` @ `7162804` (NOT yet reviewed by Claude) |
Each branch builds on the previous one (linear): baseline → Lane A → phase0 → phase1 → phase2.

## 2. Handoff folders (`D:\SnipNova-Dev\handoff`) — Luffy uploads these to Sanji
* root: Lane A — `lane-a-reaction-intelligence.patch` (7e14b3f..e8b5c5d), `series\0001..0013`, `HANDOFF.md`,
  `EVIDENCE.md`, `MESSAGE_FOR_MUSE.md`.
* `phase0\`: `COMPETITOR_STUDY.md`, `SYSTEM_AUDIT.md`, `IMPLEMENTATION_PLAN.md`.
* `phase1\`: `lane-a-phase0-phase1.patch` (e8b5c5d..48dd1e9), `series\0001..0004`, `BROLL_V3.md`.
* `opencode\`: `OPENCODE_PROMPT.md` (Claude's brief) + OpenCode's own outputs (evidence, series, pytest logs) — REVIEW PENDING.
* `phase2\`: NOT created yet (Phase 2 unfinished).
Unknown: whether Luffy has uploaded root/phase0/phase1 to Sanji (Sanji at 5:27 PM still asked for root).

## 3. What is done and verified
### Lane A (done)
Streamer-reaction detection (voice/facecam/game-event correlation, coincidence rule), gameplay frame evidence, GA2
hooks/boundaries/caption plan, edit recommendations (layout `stacked_reactor|pip_preserved|full`), keyless provider audit
(`tools/provider-audit/audit_providers.py`). Evidence: `tools/gameplay-eval/EVIDENCE.md`.
### Live provider status (owner-run audit + NVIDIA probe, 2026-11-17)
Working: Groq, NVIDIA (nemotron-3-ultra, nemotron-3.5-super-vl, lightning, nano-omni, bolt, mistral-large-4), OpenRouter
free, Cloudflare, Hugging Face, local Ollama qwen3:8b. Failing: DeepSeek (no credit; owner will NOT fund). NIM
`deepseek-v4.1-flash` HTTP 410 twice → rung `nim-deepseek` moved to `nvidia/nemotron-3.5-super-vl-120b-a12b` (commit `3b6b313`).
### Phase 0 (done) — docs in `docs/phase0/`.
### Phase 1 B-roll v3 (done, proven)
Cadence `broll.cadence_per_min` (finance 4.0, podcast/Hindi/education 3.5, true crime 3.0, vlog 4.0, travel/Japan 4.5),
lexicon `auto_director/broll_lexicon.py` (116 concepts / ~845 triggers), AI-plan priority + lexicon top-up, demonstration
guard, reaction→PiP, PiP render in `media/edl_render.py`, `provider_error` stamping, off switch `BROLL_CADENCE=legacy`.
Evidence: `docs/phase1/BROLL_V3.md`; offline 9×40 s real clips inserts 9→13; owner live run 2 (SigLIP on): **4/4
relevant real Pixabay inserts** (`test-footage/phase1/live/report.md`, frames). Tests: `test_broll_v3_cadence.py`; 19
v2-pinned tests under `v2_cadence` fixture (all pass under legacy).

## 4. Phase 2 (one-click auto-editor across niches) — IN PROGRESS
### Implemented (committed `545ce39`)
* `tools/phase2/auto_edit.py` — real stages: `get_video_transcript` (faster-whisper) → selection (`--select ai`:
  `get_most_relevant_parts_by_transcript`; `--select fixed`: labelled sentence-aligned windows, NOT AI) → `build_edl`
  → `attach_reframe_plan(edl, source, [(start,end)], False, framing="auto")` (added after a harness gap, see 6) →
  `resolve_edl_broll` (only with `--env-file`) → `create_optimized_clip` → QC (ffprobe, LUFS, blackdetect, boundary)
  → `report.json` / `report.md` + frames in `test-footage/phase2/<video-stem>/`.
  `--llm auto` = `groq:openai/gpt-oss-120b` when GROQ_API_KEY is in `--env-file`, else `ollama:qwen3:8b`.
* `tools/phase2/run_all.py` — 7 niches: Finance `finance_business` (phase1/wIxRKlb_1uk.mp4, CC BY); Podcast
  `podcast_news` (phase2_src/1oXjtK5Nsjw.mp4, first 12 min, CC BY); Talking head `podcast_news` (TkHlhgTutFs, uploader-
  marked CC BY); Vlog `vlog_lifestyle` (D3cAyFmhi_M, CC BY); Comedy `meme_comedy` (dwZK6iQlyEw, CC BY); Education
  `education_tutorial` (-m-gudHhLxc, CC BY); Gaming `gaming` (real_more/kai_sa_20000.mp4, Twitch, private test).
  Writes `test-footage/phase2/INDEX.md`. All media is under `test-footage/` (git-ignored, never redistribute).
* Product fixes: `media/transcription.py` (CUDA libs missing → CPU int8, remembered; `WHISPER_DEVICE`), `media/clip_qc.py`
  (`check_loudness`, `check_thought_boundaries`, opt-in via `ClipQcContext.loudness_target_lufs` / `transcript_words`).
### Verified so far (fixed-window mode, no AI, no stock keys)
* Finance (run BEFORE the reframe stage was added): 2 clips, 1080×1920 + audio, −14.5 / −14.0 LUFS, 0 s black,
  boundaries clean, B-roll planned 3 and 2 (not inserted: no keys). Frames: captions `hormozi` render; centre-crop cut an
  on-screen text box (harness gap — must re-run with the reframe stage).
* Podcast (re-run WITH reframe stage): clip1 plan `split` (reason `interaction_exceeds_crop`), clip2 `delegate: plain`;
  −14.4 / −13.8 LUFS; boundaries clean. **Frames NOT yet reviewed.**
* Talking head, vlog, comedy, education, gaming: NOT RUN yet (batch stopped to add the reframe stage).
### Not working / blocked
* Local `ollama:qwen3:8b` selection exceeds the app's 600 s per-attempt timeout on this laptop (qwen3 "thinking" mode);
  AI-selection runs need the owner's Groq free-tier key via `--env-file` (owner-run).

## 5. Tests — commands and recorded results
Windows needs: `NO_PROXY=localhost` and a stub `fcntl.py` on `PYTHONPATH` outside the repo (contents:
`LOCK_EX=2 LOCK_SH=1 LOCK_UN=8 LOCK_NB=4`, `def flock(*a,**k): return None`, `def lockf(*a,**k): return None`).
Use one PYTHONPATH entry (Windows separator is `;`, not `:`). Always `< /dev/null` for pytest in loops.
```
cd D:\SnipNova-Dev\claude-code\backend
NO_PROXY=localhost PYTHONPATH=<shim-dir> .venv/Scripts/python.exe -m pytest tests/unit/<file>.py -q -p no:cacheprovider --no-cov
```
Recorded results on this branch (evidence = tool output in the session):
| File | Result |
|---|---|
| test_broll_v3_cadence | 28 passed |
| test_clip_qc_phase2 | 5 passed |
| test_clip_qc_pipeline | 8 passed |
| test_separation_transcription | 8 passed |
| B-roll/EDL set (Phase 1 final run): broll 43, concepts 32, audit_fixes 15, word_sense 25, slice5 12, consolidation 8, toggle 54, resolver_v2 19, vision_veto 39, pixabay 14, coverr 14, auto_director_edl 18, retention 25, retention_editing 18, layout_report 9, beat_edl 5, pipeline_wiring 14, speaker_turns 3, transitions_sfx 91, rerender 1 | all passed |
| Pre-existing on untouched baseline too (Windows) | test_edl_render 3 failed; test_editor_broll 9 failed; test_editor_broll_review 2 failed; test_edl_av_sync 1 failed; test_billing_allowance 1; test_content_requirements_brief_image 1; test_recipe_create_wiring 1; test_video_utils_effects 1; hangs: test_video_service, test_analysis_failure_degradation, test_clip_qc, test_faster_whisper_transcription, and others listed in `test-footage/affected_results.txt` |
| NOT RUN | full suite on Linux (plan: build `snipnova-devtest` image from `backend/Dockerfile` with `BACKEND_CPU_ONLY=true`, run pytest; never touch the app's containers/volumes) |

## 6. Known bugs / findings
1. **NVENC probe** uses `-filter_complex_script` (removed in FFmpeg 8-era builds, e.g. this PC's N-125365) → probe fails →
   CPU encode silently. Delegated to OpenCode (Task 1). Review pending.
2. **Harness gap (fixed in `545ce39`)**: auto_edit originally skipped `attach_reframe_plan`, so EDL renders centre-cropped.
   Finance results above predate the fix.
3. Animated/faceless explainer: centre crop can cut on-screen text — re-check after the reframe stage.
4. PiP B-roll inset position is fixed (lower-middle); can cover a low face.
5. `HF_TOKEN` not passed through docker-compose (packaging) — not fixed.
6. Music/SFX libraries ship empty by licence policy → "EDL expects music … no file" warnings; no music in renders.
7. Real-world recall of streamer reactions is low (~15–21% of exclamation moments) — needs labelled real clips.

## 7. Precise next steps (in order)
1. Review podcast frames (`test-footage/phase2/1oXjtK5Nsjw/clip*_*.png`); confirm split layout shows both faces.
2. Re-run all 7 niches with the reframe stage: `backend\.venv\Scripts\python.exe tools\phase2\run_all.py --select fixed`
   (from `D:\SnipNova-Dev\claude-code`, with NO_PROXY + shim). ~5–10 min per niche. Review every niche's frames;
   fix pattern bugs (keep each fix behind a switch, add tests).
3. Give Luffy the owner-run command for real AI selection + stock B-roll:
   `& "D:\SnipNova-Dev\claude-code\backend\.venv\Scripts\python.exe" "D:\SnipNova-Dev\claude-code\tools\phase2\run_all.py" --select ai --env-file "D:\EditedTool\JarvisClipStudio\.env"`
   (reads only GROQ_API_KEY, PIXABAY_API_KEY, COVERR_API_KEY; Groq free tier). Then review the AI-selected clips.
4. Wire the new QC checks into the real pipeline call site (`ClipQcContext` construction in services) — hot file
   `services/*`: keep behind a switch, minimal diff.
5. Review OpenCode's `lane-b/render-basics` (`7162804`) against `handoff\opencode\OPENCODE_PROMPT.md`: allowed files only,
   re-run its tests, check its renders/proof reel; then add to handoff.
6. Linux test gate via Docker image `snipnova-devtest` (see §5).
7. Write `docs/phase2/AUTO_EDITOR.md` (evidence per niche), commit, create `D:\SnipNova-Dev\handoff\phase2\` (patch
   `48dd1e9..HEAD`, series, docs) without overwriting earlier folders.
8. Then: transitions (brief Phase 2: honest count, new families with distinctness metric — build on OpenCode's reel),
   music/sound design (brief Phase 3, needs owner decision on licensed audio), remaining A1–A8 (brief Phase 5).

## 8. Owner-facing status
V10 ZIP SHA-256 verified (`e5d219eb…72533e`, 41,230,939 bytes) — owner installs it himself; Claude never installs.
Memory files: `C:\Users\Prateek T H\.claude\projects\D--EditedTool-JarvisClipStudio\memory\` (`claude-is-main-driver.md`,
`snipnova-phase-status.md`).

## Resume point (2026-11-17, after usage limit)
Branch lane-a/phase2-auto-editor. Committed: 2b116b4 (reactor inset fix, podcast clip 2 now split), QC wiring commit (HEAD).
OpenCode: b0965c8 on lane-b/render-basics fixes its 2 test pins; Round 2 prompt at D:\SnipNova-Dev\handoff\opencode\PROMPT_ROUND2.md.
UNCOMMITTED facecam work: backend/src/media/facecam_rect.py (new), reaction_layout.py (inset_bottom generalisation + facecam fallback in choose_reaction_layout), reframe_plan.py (facecam delegation + _inset_probe_times -> probe_times), tests/unit/test_facecam_rect.py (3 FAIL: synthetic noise video yields a spurious right-side rect; fix the fixture or the detector, and _render(cam=None) crashes), tools/phase2/run_all.py (Gaming HD entry; INDEX.md is overwritten by --only runs, make it merge).
Real-video eval of facecam_rect: 14/16 streamer facecams found, lecture presenter window 2/2, 0 false positives on podcast/vlog/comedy/talking head/Speed. Gaming re-render was running (log test-footage/phase2_gaming_rerun.log); check reaction layout in test-footage/phase2/kai_sa_20000*/ frames.
Next: fix the 3 tests, run reframe suites, commit facecam; docs/phase2/AUTO_EDITOR.md; handoff\phase2; give owner the --select ai command.

## Resume point (2026-11-17, evening)
Session now rooted in D:\SnipNova-Dev\claude-code (NEVER the install folder: its shell cwd blocked Luffy's installer).
Phase 2 commits: 545ce39, 07caa7e, 2b116b4, 25ac503, 023f5f1 (facecam_rect), 1b0fcc8 (gaming templates facecam_red/gold + layout_template/layout_credit + frontend picker), 7c5b03b (REACTION_API_CONTRACT fields), 27f00d0 (score_fixture.py, gate_compare.py).
Handoff in progress: D:\SnipNova-Dev\handoff\phase2 (HANDOFF.md, EVIDENCE.md drafted; series + patch + final render table + gate-compare table still to add).
Sanji coordination via Luffy: replies in handoff\REPLY_TO_SANJI_2026-11-17*.md. Freeze Nov 20 10:00 IST.
Fixture v2 at test-footage\fixtures\streamer_fixture_v2.mp4: reaction picker FAILS (decoy #1; no episodes). Next Lane A fix: mic channel / speech-relative voice bursts, YuNet-landmark face activity in the facecam_rect window.
Sanji's gate diff applied in worktree D:\SnipNova-Dev\sanji-gate-wt (base 48dd1e9); compare results in test-footage\gate_compare\*.jsonl.
Owner still to run: tools\phase2\run_all.py --select ai --env-file <his .env> (after his V11 install finishes).

## Resume point (2026-11-18, GTA 6 eve)
Luffy/Sanji: complete website tonight; STOP building from scratch (news-card template paused, not in series).
Phase 2 series = 21 patches, tip 70961ee, in D:\SnipNova-Dev\handoff\phase2 (+ music\ with 7 FreePD MP3s not in git).
Plain/Auto switch proven with music+SFX present: comedy Plain audio bit-identical to voice-only (-240 dB diff), Auto bed -35.8 dB; Plain/Auto EDL decisions in test-footage\phase2\*_sw_plain|auto\report.json.
Replies to Sanji: handoff\REPLY_TO_SANJI_2026-11-18.md, _b.md, _c.md (Q9-Q13: assets mount, defaults, E2E on installed app, Luffy's empty install folder/.env, GPU in Docker).
Try-it page: tools\phase2\try_it.py (Layout picker: auto one-layout / crop / fit).
