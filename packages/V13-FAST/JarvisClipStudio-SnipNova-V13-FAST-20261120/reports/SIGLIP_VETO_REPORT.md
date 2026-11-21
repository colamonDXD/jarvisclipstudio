# SigLIP B-Roll Vision Veto — Lane Report

**Lane:** SigLIP B-roll veto (scout report ADOPT #4) · **Branch:** `feature-broll-vision-veto` (worktree `~/workspace/jcs-siglip`, off next-milestone @ `54c8b8a`) · **Commits:** `657b454` (scorer module) → `3a2a338` (resolver wiring + compose flag) → `eeebdf2` (tests) → `5af7511` (CHANGELOG) · **State:** IMPLEMENTED + INDIVIDUALLY TESTED + INTEGRATED (resolver/render proof) on the sandbox; **NOT MERGED** (main merges); user-machine validation pending. Feature ships **OFF by default** behind `BROLL_VISION_VETO_ENABLED`.

## Step 0 — where B-roll resolution lives today (verified in-tree)

- **Planning is offline/deterministic.** `auto_director/edl.py::build_edl` calls `_plan_broll_segments`, which stamps entries from word-sense-resolved plans: `{query (the planned noun), search_query (e.g. "train station"), context (spoken words in/near the window), start/end, spoken_at}`.
- **Resolution is where network enters.** `media/pixabay_broll.py::resolve_edl_broll` is called (a) at planning time in `video_service.build_pipeline_edl` — AFTER the `plainify_edl` early-return (Plain stamps `edl["template"]="plain"` and strips B-roll first) — and (b) as a render-time mop-up in `media/edl_render.py` (~line 2052) for entries still unresolved. `ensure_pixabay_asset` iterates ≤5 Pixabay hits in relevance order; a hit must pass `broll.footage_mismatch` (the legacy judge) before its download starts; the first passing hit is stamped `{local_path, asset_id, url, provider}`.
- **The legacy judge is tag-text only.** `footage_mismatch` IS wired into the EDL path (shared by the Pixabay resolver; the legacy Pexels `BRollSession` in `src/broll.py` is a separate path, untouched). It reads the hit's tags/description: sensitive themes, word-sense via tags ("train" the verb vs the vehicle), untagged hits allowed with one shared descriptive word. **It never sees a pixel.** A hit whose tags say "train, railway" while the footage is a scenic bridge passes every gate that exists today. That is the hole this lane closes.
- **Veto hook placement:** inside `ensure_pixabay_asset`'s candidate loop, after the tag gate, before the winning download — the one point that sees (sense-resolved subject, candidate renditions) for every hit, in both resolution passes.

## Licence verdict

`google/siglip-base-patch16-224` — **Apache-2.0** (verified 2026-11-11 via the Hugging Face API: `cardData.license` + model tag), **not gated**, 203M params, ~813 MB fp32 safetensors. Weights are **never bundled or committed**: runtime download into the shared models cache (`separation_models_dir()`), mirroring Demucs. No fallback to another model was needed; CLIP ViT-B/32 and smaller SigLIP variants were not pursued.

## Design (veto-only, fail-open, opt-in)

- `backend/src/media/broll_vision.py` — `VisionVetoSession`: lazy load on first use; frames decoded by **FFmpeg to raw RGB24** (the app has no Pillow anywhere — this is also the codebase's existing pattern); 3 frames per candidate (25/50/75% of the clip, or the poster thumbnail when the payload carries one); decision score = **max cosine over frames** vs the sense-resolved subject text (`search_query or query`); veto strictly below the threshold; per-(asset, subject) score cache; 20 s per-placement wall-clock budget (`BROLL_VISION_VETO_BUDGET_SECONDS`); session closes (model released, CUDA cache emptied) at the end of each resolve pass. **Every failure returns "no veto"** — missing model, no network, scoring error, exhausted budget, unavailable device.
- **Device gate mirrors separation:** CUDA only when free VRAM ≥ floor (`BROLL_VISION_VETO_CUDA_MIN_FREE_MB`, default 1500 MB, via `torch.cuda.mem_get_info()`); unknown probe → CUDA by presence; below floor → veto skipped for the pass and logged; no CUDA → CPU. On Luffy's 6 GB card this means the veto yields whenever faster-whisper is holding the card — the safe coexistence behaviour.
- **Tokenizer without SentencePiece:** the reference `AutoTokenizer` build requires SentencePiece, which is **not in the app's lockfile** (verified) — the first calibration run failed exactly there and failed open as designed. The loader falls back to the repo's `tokenizer.json` (the complete fast serialization of the same Unigram model — same normalizer, same `</s>` post-processing) via the `tokenizers` library, already a locked transformers dependency. `AutoTokenizer` is still preferred where it works.
- **Transformers-version surface:** embeddings come from the full forward's `image_embeds`/`text_embeds` (in transformers 5.17, `get_image_features` returns a tower output object, not a tensor — found by measurement, handled with a projection-unwrap fallback).
- **Resolver wiring:** vetoed hits are logged (`Vision veto: Pixabay hit N for 'subject' scored X; falling through…`) and recorded; the EDL entry is stamped `vision_veto: {checked, scores, vetoed, winner_score}` — including all-vetoed entries (the renderer only composites entries whose `local_path` is a real file, so the stamp is inert there). `resolve_edl_broll` never constructs a session for `template == "plain"`; Plain EDLs are structurally B-roll-free anyway. Search normalisation now also emits `preview_url` (smallest rendition) and `thumbnail_url` (opportunistic — the live payload captured in this lane carried neither a `thumbnail` field nor usable posters; the preview-rendition path is the production reality).
- **Compose:** `BROLL_VISION_VETO_ENABLED=${...:-false}` passthrough added for backend + worker, mirroring `SEPARATION_ENABLED`.

## Calibration — measured scores (production frame path, real model, CPU)

Fixture videos were built from stills (3 s loops); frames extracted by the production `candidate_frames`; subjects are exactly what the resolver passes. Fixtures: stock-style images via image search (train at platform, urban traffic, gym barbell squat, stormy ocean, chef in kitchen — visually verified — plus a train on a high trestle bridge, empty desert highway, ocean sunset, forest canopy, billiards table).

| Pair | Subject | Score | Verdict @ 0.048 |
|---|---|---|---|
| match — train at platform | "train station" | 0.1090 | kept |
| match — urban traffic | "car" | 0.0732 | kept |
| match — stormy ocean | "ocean" | 0.0866 | kept |
| match — chef cooking | "kitchen" | 0.0553 | kept |
| match — barbell squat | "gym" | 0.0517 | kept |
| **mismatch — trestle bridge** (named class 1) | "train station" | 0.0389 | **vetoed** |
| **mismatch — desert highway** (named class 2) | "car" | 0.0442 | **vetoed** |
| mismatch — trestle bridge (original incident shape) | "gym" | −0.0380 | **vetoed** |
| mismatch — beach sunset | "kitchen" | −0.0255 | **vetoed** |
| mismatch — forest canopy | "ocean" | 0.0036 | **vetoed** |
| mismatch — billiards table (pool resense) | "pool" | 0.0275 | **vetoed** |

**Chosen threshold: 0.048** (mid-gap: mismatches ≤ 0.0442, matches ≥ 0.0517). **Veto precision on planted mismatches: 6/6. False vetoes on matches: 0/5.** Honest caveat: the gap is thin (0.0075 of cosine) and the set is small (11 pairs, still-image loops, not moving stock). Noun-phrase subjects score low with SigLIP base; richer phrasings moved individual pairs both ways in diagnostics ("cars driving on a city road" widened its pair's gap to ~0.10; "a photo of a car" narrowed its). The production subject stays the sense-resolved search query — the text the tag gate already reasons about — and the threshold is env-overridable (`BROLL_VISION_VETO_THRESHOLD`). Failure modes are soft in both directions (a false veto = next hit or no cutaway, the same outcome the tag gate produces daily; a missed veto = today's behaviour), and the feature is off by default.

## Render-level proof (live, real model)

Scripted-search live run through the real resolver + `edl_render.render_edl_clip_ffmpeg` (only `search_pixabay_videos`/`_download` stubbed to local fixture videos):

- Log: `Vision veto: Pixabay hit 101 for 'ocean' scored 0.0036; falling through to the next hit`
- Winner: asset 102 (true ocean footage); EDL stamp: `{'checked': True, 'scores': {'101': 0.0036, '102': 0.0866}, 'vetoed': [101], 'winner_score': 0.0866}` — scores identical to calibration (deterministic path).
- Render: frame before the window = green source (R0 G130 B0); frame inside the window = ocean footage (R91 G104 B108, blue-dominant) — the vetoed forest hit never reached the frame, and the slot fell through cleanly instead of forcing the bad hit.

## Tests (serial, TMPDIR on workspace, OMP_NUM_THREADS=2, venv python, --no-cov)

- New `tests/unit/test_broll_vision_veto.py` — **30 passed**: flag/threshold parsing, `should_veto` purity, device gate (no-CUDA→CPU, unknown probe→CUDA, below floor→skip), stub-model cosine math + max-over-frames, real-FFmpeg frame decoding, resolver wiring with scripted sessions (fall-through, all-vetoed, fail-open on unjudged, budget exhaustion stops checks), EDL stamping, **Plain never constructs a session**, flag-off build unchanged, and a render-level test (vetoed red stock never appears; blue fall-through composites).
- Neighbours batch 1 (pixabay, broll, word-sense, audit fixes, rerender, editor, layout): **129 passed**.
- Neighbours batch 2 (veto suite + separation + auto-director EDL + pipeline wiring + edl_render + LiteLLM gateway config): **119 passed**.
- `frontend/src/lib/changelog.test.ts`: **4/4** after the CHANGELOG entry.

## VRAM behaviour

Sandbox has no GPU, so on-card behaviour is implemented + unit-pinned, not measured here: lazy per-pass load, `model.to(device)`, `close()` deletes the model and calls `torch.cuda.empty_cache()`; the free-VRAM floor (1.5 GB default) makes the veto step aside whenever faster-whisper or a render holds the 6 GB card, and the skip is logged. Model footprint ~813 MB fp32 — fits the floor with room to spare when the card is otherwise idle.

## User-machine validation remaining

1. Enable the flag on his build (next package after merge) and run one Auto-mode video with known stock cues; confirm the download-once log line, the veto log lines, and the `vision_veto` stamps on clip records.
2. Watch VRAM coexistence on the RTX 4050 during a real render: veto should skip (logged) while whisper/render holds the card, and run on CPU-idle gaps otherwise.
3. Threshold sanity on real Pixabay moving footage (the calibration used still-image loops): if real matches cluster lower, raise/lower `BROLL_VISION_VETO_THRESHOLD` from the stamped scores — the stamps exist precisely so this can be tuned from evidence.

## Hard limits honoured

Auto-mode B-roll path only; word-sense logic, Plain mode, caption/title guard, and selection prompts untouched; ₹0 (open model, Apache-2.0, runtime download); not merged.
