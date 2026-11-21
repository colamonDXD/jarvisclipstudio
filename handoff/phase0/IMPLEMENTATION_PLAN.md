# IMPLEMENTATION_PLAN — from clip finder to complete AI editor

Phase 0 deliverable, 2026-11-17. Inputs: `COMPETITOR_STUDY.md`, `SYSTEM_AUDIT.md`. **Nothing below is started; this plan
waits for the owner's review** (brief §15). Estimates are agent working days in this environment and assume the owner
dependencies in §5 arrive; they are planning numbers, not promises.

## 1. Reuse / repair / extend / build

| Area | Decision | Why |
|---|---|---|
| Moment selection, hooks, karaoke captions, brand kit, silence/filler tightening, SigLIP veto, ducking | **Reuse** | Working; verified per repo record |
| Smart B-Roll v2 cadence and cue generation | **Repair** | Design caps inserts at ~1 per clip; 25-noun keyword list (SYSTEM_AUDIT §1) |
| Viral Intelligence plans, niche auto-director, beat grid, edit recommendations, reaction/gameplay detectors | **Extend** | Exist; need wiring into one end-to-end flow and real-video proof |
| Transitions | **Extend** | 61 candidates, no proof; new families needed for a genuine 200+ |
| Music / SFX | **Extend** | Engine exists; libraries empty; selection is a profile mood, not content-aware |
| Export profiles 16:9, 1:1 | **Build** | Missing (`EXPORT_PRESETS` is 9:16 only) |
| Twitch/YouTube chat-spike signal | **Build** | Cheap strong streamer signal (reference reel); missing |
| Eye contact, bad-take removal, dubbing, AI-generated B-roll, NLE XML | **Defer** | Heavy models or owner-deferred; revisit after the core editor works |

## 2. Roadmap (order follows brief §14)

| Phase | Work | Evidence that closes it | Effort |
|---|---|---|---|
| **1. B-roll repair** | Per-niche cadence (target ~3–4 cutaways/min for talking niches, still off for gaming/sports/comedy unless relevant); multi-occurrence cues; concept writer on the free ladder (Groq → NVIDIA → OpenRouter → Cloudflare → Ollama) with a local noun-phrase fallback; keep every guard + drop stamping; live yield report per stage | 3 real videos: exported MP4s, insert counts + timestamps, provider/licence metadata, inserted frames, human relevance review, false-positive / missed notes | 2–3 days + one owner-run render (§5.1) |
| **2. One-click editor across niches** | Orchestrate existing stages (select → boundaries → tighten → hook → captions → zooms → B-roll → transitions → music/SFX → audio → reframe → QC → export); add 16:9 and 1:1 export profiles; QC additions: boundary completeness, loudness target (-14 LUFS observed in all references), caption overflow, B-roll over face/HUD | One real export per niche (podcast, talking head, gaming/streamer, finance, vlog, comedy, education) with ffprobe, key frames, decision log, checklist | 4–6 days |
| **3a. Transitions** | Proof reel of the existing 61 first (honest working count); then new families (whip/motion blur, glitch/RGB split, light leak/flash, shake, mask/luma/shape wipes, speed-ramp, spin) generated from type × direction × duration × intensity; a distinctness metric (frame-difference signature) rejects near-duplicates | Proof reel per family, ffprobe, sample frames, distinctness table, honest count | 3–5 days |
| **3b. Music + sound design** | Content-aware mood/energy pick (transcript sentiment + speech energy + niche); beat-aligned cut suggestions; ducking proof; fades; SFX at zooms/transitions/punchlines with a restraint budget; loudness QC | Rendered exports with waveform/LUFS measurements and listening notes | 3–4 days (needs licensed audio, §5.2) |
| **4. Remaining product** | A1 chapters/show notes, A3 edit variants, A4 whole-task NL edit, A5 calibration, A6 clean-audio preset, A7 emoji audit, A8 active-speaker validation, trailer breakdown; chat-spike signal; wider gaps from the study | Per-task evidence per brief §12 | 8–12 days total (parallelisable, §4) |
| **5. Integration** | Contracts agreed with Sanji; his frontend exposes real capabilities; full Linux gate | Sanji's integration report | Sanji-led |

Order inside Phase 1–2 is deliberate: B-roll is the measured failure on the owner's own video; the one-click flow is the
core promise; transitions and sound are only worth polishing once the flow renders end to end.

## 3. Contracts to agree with Sanji before frontend-facing changes (PROPOSALS)

1. **B-roll placements on each clip** — `broll: [{start, end, query, concept, provider, asset_id, licence, attribution,
   preview_url}]` plus `broll_dropped: [{query, stage, reason}]` (already stamped in V9); edit ops remove / replace /
   move reuse the existing editor B-roll items (`broll-panel.tsx` is his).
2. **Transition catalogue** — extend `GET /transitions` with `{id, family, direction, duration_range, preview_url}`; the
   per-clip choice stays an EDL field.
3. **Export profiles** — add `youtube_landscape` (1920×1080) and `square` (1080×1080) to the export preset names; same
   endpoint (`GET /tasks/{id}/clips/{clip_id}/export?preset=`).
4. **One-click settings** — reuse create-page fields (template, niche, framing, B-roll, music); add only
   `auto_edit_level: off | light | full` if Sanji agrees.
5. **Decision log per clip** — read-only list of what the editor decided and why (zoom at 4.0 s "point lands", B-roll
   "concept: stock market" …) so the review UI shows real reasons, never decorative ones.
A full `API_CONTRACT.md` follows once he confirms names (his `REACTION_API_CONTRACT.md` is the template).

## 4. Delegation (no two agents on the same files)

| Task | Owner | Files it may touch |
|---|---|---|
| Phases 1, 2, 3a, 3b | Claude | `auto_director/edl.py` B-roll section, `clip_plan.py`, new `auto_director/auto_editor.py`, `clip_editor.EXPORT_PRESETS`, transition modules, `media/music_library.py`, `media/sfx_library.py` |
| A6 clean-audio preset, A7 emoji audit, A1 chapters | DeepSeek (only if its access is confirmed working and free) | `auto_director/audio_studio.py`, `emoji_captions.py`, new `services/chapter_service.py` + migration |
| All UI, contracts, merge, Linux gate | Sanji | frontend, integration docs |
Hot shared files (`services/video_service.py`, `services/edl.py`, `config.py`) change only after Sanji agrees.

## 5. What the owner needs to do (only these)

1. **B-roll evidence run (keys stay yours):** I write one script that reads Pixabay/Coverr keys from your `.env` at run time
   (like the provider audit — never prints them) and renders the three test videos. You run one command.
2. **Licensed music/SFX:** decide the source. Options: your own tracks; CC0 libraries with per-track licence recorded;
   or keep music off. I will not bundle unverified audio.
3. **Test videos:** for each niche, either point me at your own footage, or approve Creative-Commons (CC-BY) videos I find,
   used privately for testing only.
4. **Upload the Lane A handoff** (`D:\SnipNova-Dev\handoff`) to Sanji (already requested by him).
5. Optional: run 2 of the same test videos through OpusClip's free tier, so "better than OpusClip" can be tested on the same
   source instead of claimed.

## 6. How "better than OpusClip/Klap" will be measured (no claim before this)
Same source videos, both tools; blind human ranking of clip pairs; boundary completeness ("starts and ends the thought");
caption word error rate against a hand transcript; B-roll relevance rate; render time on the RTX 4050.

## 7. Hardware plan (RTX 4050, 6 GB VRAM, 16 GB RAM)
Measure before optimising: NVENC/NVDEC probe and encode speed on the owner's machine, peak RAM/VRAM per stage
(faster-whisper, SigLIP, YuNet), one long-video run. Rule: one GPU model resident at a time; SigLIP and Whisper never
co-resident; no parallel renders above what RAM allows (the 2026-11-15 run hit 99% RAM).

## 8. Risks
* Stock libraries may not contain relevant footage for niche topics (GTA, specific companies) — fallback must be "no
  B-roll", never a wrong one.
* More B-roll raises the false-veto/irrelevance rate — the veto must be re-measured at the new density.
* Licensed music availability is an owner decision; without it, music proofs cannot be produced.
* Windows test environment hangs on several suites; Linux gate stays authoritative.
