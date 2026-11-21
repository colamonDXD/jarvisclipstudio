# COMPETITOR_STUDY — SnipNova vs OpusClip, Klap, Vizard, Riverside, Submagic, Captions, Descript

Phase 0 deliverable, 2026-11-17. Author: Claude (Lane A). Working copy `D:\SnipNova-Dev\claude-code`, branch `lane-a/phase0-audit`.

## How to read this

* **Verified** = stated on the vendor's own pricing/product page (fetched 2026-11-17) or measured by me.
  **Marketing claim** = vendor wording with no method behind it (e.g. "99% caption accuracy").
  **Reported** = third-party review; many are affiliate or competitor blogs, so treat as directional.
* I could not use any competitor product hands-on (no accounts; paid tiers needed for most output). Nothing below says
  "competitor X's output is better/worse than SnipNova's" — that needs a same-source A/B test (see IMPLEMENTATION_PLAN §6).
* SnipNova status labels: **HAVE** (implemented and evidenced) · **PARTIAL** · **MISSING** · **UNVERIFIED** (implemented,
  no real-world evidence). Evidence = file/function, test, or the repo's own status records (`TRACKER.md`,
  `FEATURE_PROGRESS.md`, `BUG_AUDIT.md`, the 86-row feature map) — where I only have the repo's word for it, I say so.

## 1. The seven products in one view

| Product | What it is | Moment finding | Free tier (verified) | Paid entry (verified) |
|---|---|---|---|---|
| **OpusClip** | Long→short clipper + AI editor | ClipAnything: per-frame visual, audio events (laughter, cheering, shouting), sentiment, objects, speaker, prompt; "rated on virality potential" (no method published) | Watermark on animated captions, exports expire in 3 days, 1080p | Starter $15/mo; Pro $29/mo (AI video B-roll, transitions, music, SFX, XML export, scheduler) |
| **Klap** | Long→short clipper | "AI analysis of topics"; virality score; AI Reframe 2 layouts (split screen, screencast, **gaming**) | 1 video trial, no download | Basic $29/mo (100 clips), Pro $79, Pro+ $189; sources up to 3 h; API $0.32/short |
| **Vizard** | Clipper + editor + "Agent" | AI clipping, auto B-roll, auto emojis, auto keyword highlight | 60 credits/mo, 720p export, watermark, 3-day expiry | Creator (price not shown on page), 4K, face tracking |
| **Riverside** | Recording studio + editor | Magic Clips: "picks highlights automatically", focus on speaker/topic/keywords | 2 h recording, 720p, watermark | Pro $24–29/mo: AI B-roll, eye contact, silence/filler removal, show notes |
| **Submagic** | Caption-first short editor | Magic Clips (long→short) | "Try for free" (limits not stated) | ₹666–4999/mo: auto zoom, B-roll (Storyblocks on Pro+), SFX, music, transitions, silence removal; template from a reference video |
| **Captions** | Mobile-first AI editor | "Clips" long→short on Max | No AI credits, 1 caption template | Max $24.99/mo: AI Edit, 100+ caption templates, B-roll, music, SFX, AI image/video, lipdub |
| **Descript** | Text-based editor | "Create Clips"; Underlord can "cut to the most engaging parts" | 1 media h, 720p, watermark | Hobbyist $16–24, Creator $24–35: Studio Sound, filler/gap removal, eye contact, dubbing |

Reported weaknesses (directional): OpusClip — generic virality reasoning, weak on slow-build/educational content, credit
mechanics and cancellation complaints (Trustpilot 4.0/5, 22% one-star per Ssemble). Klap — "AI doesn't always pick the most
interesting moments", ~85% clip accuracy per one user. Vizard — one competitor blog claims a 30–40% miss rate. Common to all:
pay-per-minute/credit pricing, watermarks and 3-day expiry on free tiers, cloud upload of the source.

## 2. Feature-by-feature

| Feature | Competitor capabilities | SnipNova today | Status | Evidence / gap |
|---|---|---|---|---|
| Long→short moment selection (speech) | All seven | LLM transcript analysis, 12-min windows for long videos, rerank, 4 sub-scores | UNVERIFIED | `ai.py`, `services/video_service.analyze_transcript`; repo map F01 "live measurement owed" |
| Multimodal moments (no/low speech) | OpusClip ClipAnything (visual, sound events, emotion, objects) | V9: audio peaks + scene cuts (`visual_moments.py`). Lane A branch adds frame evidence (`gameplay_moments.py`) and streamer reactions (`reaction_moments.py`) | PARTIAL | Branch evidence: synthetic 4/5–5/5; 9 real Twitch samples ~75% precision proxy, ~15–21% recall proxy (`tools/gameplay-eval/EVIDENCE.md`) |
| Prompt-to-clip ("find every time…") | OpusClip Pro, Riverside (topic/keyword focus) | Typed campaign briefs with focus terms (`content_requirements.py`) | PARTIAL | Brief-driven, not free natural-language search over visuals |
| Virality score + reason | OpusClip, Klap, Vizard (score shown to user) | `virality_score` 0–100 from hook/engagement/value/shareability + `reasoning` | HAVE | `ai.py` sub-scores; performance calibration (F43) not built |
| Hook titles | OpusClip, Submagic (Magic Clips hook), Riverside | `hook_title`, styles, variants, enforcement | HAVE | F41 VERIFIED in repo map; `hook_title_styles.py`, `hook_variants.py` |
| Animated captions, word highlight | All; Captions 100+ templates, Submagic dozens | 11 caption templates, word-synced karaoke highlight | HAVE (fewer styles) | `caption_templates.py` (11 ids), `media/captions.py` `KARAOKE_GAP_BRIDGE_SECONDS`; F54/F55 VERIFIED |
| Caption languages / translation | Klap 99+, Submagic 118, Opus/Captions/Descript translate | Whisper multilingual transcription, Hindi/Roman captions | PARTIAL | F56 INV; translation/dubbing F57 not started |
| Face-aware reframe 9:16 | All | YuNet face tracking + reframe planner | HAVE | F34 VERIFIED; `media/reframing.py`, `reframe_plan.py` |
| Active-speaker switching | Klap, Vizard (Creator), Riverside | Mouth-aperture↔audio correlation, default OFF | UNVERIFIED | `media/active_speaker.py` flag `ACTIVE_SPEAKER_TARGETING_ENABLED`; validation harness (A8) not built |
| Gaming / facecam layouts | Klap AI Reframe 2 "Gaming"; Opus genre reframing | Two-pane reactor layout (`reaction_layout.py`) | PARTIAL | Gate fix + rendered proof exist on Sanji's tree (`c7c5b3ca`), not in V9 |
| Auto zoom / punch-ins | Submagic, Opus, Claude-skill reference | Punch-in keyframes, universal opening push-in, Ken Burns on static crops | UNVERIFIED | `zoom_keyframes` in `edl.py`; no real-video quality review on record |
| Stock B-roll auto-insert | Opus (stock; Pro unlimited), Submagic, Vizard, Riverside, Captions | Smart B-Roll v2: concept planner, Pixabay→Coverr, SigLIP veto | PARTIAL | **Too few inserts by design** — see SYSTEM_AUDIT §1 (1 per ~40 s clip; real run 1 insert in 165 s) |
| AI-generated B-roll (image/video) | Opus Pro, Captions, Riverside | None | MISSING | No licence-clean model that fits 6 GB VRAM identified (F37 note) |
| Transitions | Opus Pro, Submagic, Captions, Vizard (adjustable) | 58 FFmpeg xfade presets + 2 MP4 overlays + zoom bounce; per-niche "signature" joins | PARTIAL | `media/edl_render.py` `XFADE_TRANSITIONS` (58); no rendered proof reel; target 200+ distinct |
| Background music, auto pick | Submagic (mood tracks), Opus Pro, Captions | Folder-driven library, auto bed by mood stem; **library ships empty** | PARTIAL | `media/music_library.py` `select_auto_bed`; no licensed tracks bundled (by policy) |
| Beat-synced cuts | Marketing mentions only | `beat_grid.py`, beat-snapped selection, velocity curves | UNVERIFIED | F05 INV |
| Music ducking under speech | Common | `sidechaincompress` in the EDL render | HAVE | `media/edl_render.py:1536` |
| SFX auto-placement | Submagic, Opus (AI SFX), Captions, Claude-skill reference (whoosh on zoom) | `sfx.py`, transition SFX mapping; **library ships empty** | PARTIAL | `signature_transitions.transition_sfx`, `media/sfx_library.py` |
| Silence / filler removal | Descript, Riverside, Submagic, Opus | Word-gap tightening, filler removal | HAVE | F04 VERIFIED; `media/timeline.py` tightening |
| Audio clean-up ("Studio Sound") | Descript, Riverside, Opus, Submagic | Audio Studio chain (afftdn, clarity, loudnorm) | PARTIAL | F47 INV; one-click preset (A6) not built |
| Eye-contact correction | Descript, Riverside, Opus Pro | None | MISSING | — |
| Bad-take / retake removal | Opus Pro, Submagic Pro | None | MISSING | F80 not started (blocked on bake-off) |
| Text-based editing | Descript, Riverside, Vizard, Klap | Clip studio with word-level document and AI `remove_words` op | PARTIAL | `editor_document.py`; UI is Sanji's |
| Chat/agent editing ("make it under 30 s, blue captions") | Underlord, Vizard Agent, Captions AI Edit, Claude-skill reference | Per-clip AI edit (typed ops), whole-task edit not built | PARTIAL | `services/ai_edit_service.py`; A4 not started |
| Multiple variants per moment | Opus/others: several clips per video, not edit variants | Hook/opening variants only | PARTIAL | F45 (full edit variants) not started |
| Brand kit / templates | Opus Pro (4), Vizard Business, Submagic Business | Brand Kit | HAVE | F36 VERIFIED |
| Style from a reference video | Submagic ("upload a reference video") | Reference Style analyser; styles drive renders | UNVERIFIED | F38 INV, F39 VERIFIED |
| Export 9:16 | All | TikTok/Reels/Shorts presets 1080×1920 | HAVE | `clip_editor.EXPORT_PRESETS` |
| Export 16:9 and 1:1 | Opus Pro, Riverside, Vizard, Descript | No preset | MISSING | `EXPORT_PRESETS` has only 9:16 entries |
| 4K export | Vizard Creator, Descript Creator, Opus Business | 1080p max (720p on Pro plan) | MISSING | F81 never requested |
| Scheduling / publishing | Opus Pro, Klap, Vizard, Riverside | YouTube/TikTok/Instagram publish + schedule, gated rollout | UNVERIFIED | F63/F64 INV; platform approvals pending |
| API / MCP | Opus Pro, Klap ($0.32/short), Vizard, Submagic | API keys + MCP server | UNVERIFIED | F76 INV |
| NLE export (Premiere/DaVinci XML) | Opus Pro | None | MISSING | — |
| Chapters / show notes | Riverside, Descript | None | MISSING | A1 (F44) not started |
| Dubbing / lipdub | Opus Pro, Captions, Descript | None | MISSING | F57 deferred by owner |
| AI voice-over | Opus, Captions, Descript | Kokoro TTS | UNVERIFIED | F51 INV |
| Auto emojis | Vizard, Submagic | Emoji captions | UNVERIFIED | F53 INV (A7 audit not done) |
| Post analytics | Klap, Opus "coming soon" | Social metrics + performance context in prompt | PARTIAL | F65 PARTIAL |
| Chat-activity highlight signal (Twitch) | Not in any of the seven; shown in reference reel Dfw7418i7r5 | None | MISSING | See REFERENCE_TECHNIQUES |
| Long sources | Klap 3 h; Vizard 10 h upload | 90 min default cap (`MAX_VIDEO_DURATION=5400`) | PARTIAL | `config.py:205` |
| Local / ₹0 processing | None of the seven (all cloud, credits) | Local faster-whisper, free model ladder, local render | HAVE | Real advantage; live ladder verified today (Groq, NVIDIA, OpenRouter, Cloudflare, Ollama up; DeepSeek no credit) |

## 3. REFERENCE_TECHNIQUES — the owner's supplied references

Method: downloaded each public item without login (yt-dlp), measured cuts (FFmpeg scene score 0.3), loudness (EBU R128),
beat grid, transcribed with faster-whisper `base`, and looked at a 12-frame contact sheet of each. Files stay local
(`test-footage/references/`, git-ignored, not redistributed).

| Link | Accessible? | What it is | Measured |
|---|---|---|---|
| youtu.be/IY08-3TS2o8 | Yes | "Claude Code Can Now Clip ANY Video (Free Skill)", 8:19 tutorial | 8.5 cuts/min, -14.1 LUFS |
| youtu.be/BvzLYTIE3Hw | Yes | "Best AI Clipping Tools in 2026 — I Tried 5", 6:21 review | 9.0 cuts/min, -26.8 LUFS |
| instagram.com/reel/DfsFEgXijzh | Yes | skillwars.ai, animated explainer, 67 s | 11.6 cuts/min, median shot 5.3 s, -14.1 LUFS |
| instagram.com/reel/Dfha6XzOS6l | Yes | ai.praful, 8 s listicle over music | 190 BPM beat found, -14.1 LUFS |
| instagram.com/reel/DftmSFNNqFM | Yes | classified.ai, Hinglish talking head + graphics, 35 s | 13.5 cuts/min, 2 cuts in first 3 s, -14.1 LUFS |
| instagram.com/p/DfzFZAmGoY2 | **Partly** | Image carousel (no video): "4 Open-Source AI Clipping Tools" — OpenShorts, SupoClip, Cutawan, Clips Kitty. Carousel images could not be downloaded; caption read from the page | — |
| instagram.com/reel/Dfw7418i7r5 | Yes | thedigitalpillar.ai, typographic explainer, 55 s | 9.8 cuts/min, -14.2 LUFS |
| instagram.com/reel/DfwH_9ROwSt | Yes | tech.happens, illustrated "clipper showdown", 39 s | 1 cut (continuous animation), -14.1 LUFS |
| yizerge.vercel.app/beta-access | Yes (page) | Offline Windows vertical editor (beta): dynamic framing, karaoke subtitles, blur backgrounds, GPU export | Not installed (unsigned installer) |

Techniques observed (each tied to what I saw or measured, not assumed):

1. **Loudness standard**: every reel is mastered at about **-14 LUFS integrated**. SnipNova should check exports against a
   platform loudness target (measurable today with `ebur128`).
2. **Captions 2–3 words at a time, one keyword highlighted** (red box: DfsFEgXijzh, DfwH_9ROwSt; yellow word: DftmSFNNqFM;
   the Claude-skill video says it highlights "billionaire", "million"). Placement lower-middle, never over the face.
3. **Section labels**: a small header names each story beat ("THE PLAN", "ROUND 2 · PRIVACY", "the trick") — a retention
   device SnipNova does not have.
4. **Split layout**: graphics/B-roll on top half, presenter on bottom half for the whole reel (DftmSFNNqFM).
5. **Cut rhythm**: talking/explainer reels change scene every ~4–6 s (median shot 3.8–5.8 s; 10–13.5 cuts/min).
   SnipNova's B-roll cadence (1 cutaway per ~40 s) is far below this.
6. **Hook in the first second**: big number or stakes text on frame 1 ("5 DAYS", "GTA 6", "Start Clipping Before … Drops").
7. **Visual proof cards**: inset screenshots/mock UI for each claim (IShowSpeed stream still, "$999" card, scored highlight
   list "Insane bridge jump 94 / Police chase gone wrong 59").
8. **Zoom placed where a point lands, framed around the face**, whoosh SFX on the zoom, re-centre on whoever is on screen
   at every camera cut (all four stated in the Claude-skill video transcript).
9. **Natural-language re-edit** ("only the best clip, under 30 s, no SFX, blue captions") applied to the whole output.
10. **Chat-spike clipping for streams** (Dfw7418i7r5): count chat messages per 10 s, a spike marks a moment, and the clip
    starts ~20 s *before* the spike because chat reacts late. This is a cheap, strong signal for Twitch/YouTube live VODs
    that none of the seven products advertises and SnipNova lacks.
11. **Story structure**: problem → method in 3 parts → proof → caveat ("BUT… fact check") → CTA ("Comment CLIP").
12. **What a reviewer checks** (BvzLYTIE3Hw): titles + transcript + score per clip to scan quickly; "does the clip include
    the beginning and ending of that thought"; readable captions with room for the speaker; free tiers end at 720p/watermark.

Not observed / not claimable: colour grading differences (360–720p sources, no reference grade), music choices beyond
"present/absent" (beat found only in Dfha6XzOS6l).

## 4. Where SnipNova is genuinely ahead / behind

Ahead (evidenced): local processing at ₹0 with a verified free model ladder; transparent per-clip reasons and sub-scores;
reference-style analyser; brand kit; streamer-reaction + gameplay evidence on the Lane A branch (early).

Behind (biggest gaps, ordered by user impact):
1. **B-roll cadence and relevance** — design-capped at ~1 insert per clip, keyword list of ~25 nouns, no AI B-roll.
2. **One-click full edit across niches** — pieces exist, but no end-to-end rendered proof per niche (podcast, talking head,
   finance, vlog, comedy, education, gaming).
3. **Export formats** — 9:16 only; competitors offer 16:9/1:1, some 4K.
4. **Transitions and sound design** — 58 presets with no proof reel; music/SFX libraries empty by licence policy.
5. **Editing depth** — no eye contact, no bad-take removal, no chapters/show notes, no dubbing, no NLE export.
6. **Caption style range** — 11 templates vs dozens–100+.

## Sources

OpusClip [pricing](https://www.opus.pro/pricing), [ClipAnything](https://www.opus.pro/clipanything), [home](https://www.opus.pro/);
Klap [pricing](https://klap.app/pricing), [home](https://klap.app/); Vizard [pricing](https://vizard.ai/pricing), [home](https://vizard.ai/);
Submagic [pricing](https://www.submagic.co/pricing), [home](https://www.submagic.co/); Descript [pricing](https://www.descript.com/pricing),
[Underlord](https://www.descript.com/underlord); Riverside [pricing](https://riverside.com/pricing), [Magic Clips](https://riverside.com/magic-clips);
Captions [pricing](https://www.captions.ai/pricing).
Reviews (directional): [Ssemble OpusClip review](https://ssemble.com/blog/opus-clip-review-2026), [Capterra OpusClip](https://www.capterra.com/p/10006559/Opus-Clip/reviews),
[argil.ai OpusClip](https://argil.ai/blog/opusclip-ai-powered-video-repurposing-1cfae), [toolsforhumans Klap](https://www.toolsforhumans.ai/ai-tools/klap),
[Capterra Klap](https://www.capterra.com/p/10014703/Klap/reviews/), [Zemith Submagic](https://www.zemith.com/en/learn/submagic-review),
[G2 Submagic](https://www.g2.com/products/Submagic/reviews), [Capterra Vizard](https://www.capterra.com/p/10009818/Vizard/reviews/),
[FluxNote Vizard](https://fluxnote.io/guides/vizard-ai-video-review-2026).
Open source: [OpenShorts (MIT)](https://github.com/mutonby/openshorts), [Auto-Cut AI](https://awesome.ecosyste.ms/projects/github.com%2Ftheghostrat%2Fai-video-editor),
[short-video-maker](https://unpkg.com/short-video-maker@1.3.0/README.md), [opensource-clipping](https://github.com/NaufalRizqullah/opensource-clipping).
