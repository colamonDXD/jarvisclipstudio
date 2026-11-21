# SnipNova: can it beat OpusClip, and can it clip live streams? (engineering assessment)

From Claude Code, 18 Nov 2026. Based on the repository as it stands (branch
`lane-a/phase2-auto-editor`, tip `416a3a1`) and today's real renders. Live
clipping is **not started**: this report is for your review first.

---

## PART 1: Honest competitiveness

### Short answers

| # | Question | Honest answer |
|---|---|---|
| 1 | Competitive with OpusClip / Klap / Vizard after current work? | **On editing restraint and framing: close, and on some content better. On picking moments at scale: not yet.** Today's work fixed the worst editing defects (random cuts, zooms, blur, caption loops), and the screen-plus-reactor layout is something OpusClip does not do well. But their moment selection is trained on huge engagement data. Ours is a free LLM reading a transcript plus our own signals. |
| 2 | Better viral-moment selection than OpusClip? | **Possible in specific niches, unproven in general.** Our concrete advantages: (a) streamer signals OpusClip doesn't use: mic on its own channel, facecam reaction, game events, and from today **visual story moments** (it found the award win with no transcript); (b) **live chat** (Part 3) is a signal they don't have on uploaded VODs; (c) **per-user learning** from the clips' own published metrics (`social_post_metrics` → `build_performance_context`, already wired). Our weakness: no large trained virality model. |
| 3 | Understand full context (what the streamer reacts to, the meme, punchline, audience interest)? | **Partly.** It sees: transcript, speaker, mic/face/laugh/scream, scene cuts, a lit screen in frame (Step 3), and zero-shot visual events (Step 4). It does **not** read the meme's text (no OCR in the selector yet), does not watch the meme as a video (no video-language model), and cannot judge audience interest beyond these proxies. |
| 4 | Avoid needless cuts, random zooms, excess transitions, irrelevant B-roll, lost context? | **Yes, now, behind switches**: Steps 1–5 (proven on the clip1 source). The B-roll relevance gate + vision veto existed (Phase 1, 4/4 relevant live). Needs your approval of the renders, then the switches go default-on. |
| 5 | Each content type independently? | **Profiles exist for 14 niches; validation renders on 6 types are running now** (podcast, talking head, gaming, finance, vlog, comedy). Livestream/reaction is the strongest; interviews/education are profile-tuned but less tested. |
| 6 | Professional without over-editing? | **That is exactly what Steps 1–5 change:** effects need evidence (a shot change, a sentence end, speech under a zoom); Plain mode = cuts + captions only. Proof: before/after renders in `handoff\quality-clip1\`. |
| 7 | Still weaker than OpusClip after this work | Listed in the comparison table below (marked ❌ / 🔨). Biggest gaps: selection model quality, caption animation polish, speed (CPU render on your laptop; CUDA libs missing in the dev venv), no OCR of on-screen text, no VLM scene understanding, transcription accuracy on noisy audio (it heard "Screamer" for "Streamer"). |

### How "beating OpusClip" gets measured (no claim without this)

1. **Blind pairwise test.** Same 10 source videos (2 per type) through OpusClip (free tier, you run it; I won't sign up) and SnipNova. For each pair: 5+ viewers who don't know which is which pick "which would you keep watching". Win rate with a confidence interval, at least 30 pairs. We win only if >55% with the interval above 50%.
2. **Rubric per clip** (scored by you): story complete (hook→content→reaction→payoff), stimulus visible, captions readable/accurate, no needless effects, framing correct. It's the 7-point checklist we already use.
3. **Real metrics.** Post matched clips and compare 3-day views, average view duration and retention from `social_post_metrics`. This is the only measure that settles "viral".

### Feature-by-feature

Legend: ✅ verified on real footage · 🟡 implemented, not verified on real footage · 🔨 needs development · ❌ not realistic at ₹0 with this architecture

| Area | SnipNova state | OpusClip (public behaviour) |
|---|---|---|
| Transcript-based moment selection (LLM) | ✅ (Groq free tier / local qwen3; quality depends on the model) | ✅ trained, data-rich |
| Streamer reaction signals (mic channel, face activity, scream/laugh, game events) | ✅ fixture 6/7 | ❓ not advertised |
| Visual story moments (no-transcript events) | ✅ found the award win on clip1 (switch off) | ✅ (multimodal) |
| Screen-being-reacted-to layout (stack) | ✅ on clip1 (switch off); 🟡 other reaction sources | partial (layouts, not stimulus-aware) |
| Restraint: cuts at real reasons, evidence zooms | ✅ (switch off) | ✅ |
| Face-tracked 9:16 reframe, one layout per clip | ✅ | ✅ (very polished) |
| No-blur fill / 4K subject probe | ✅ (switch off) | ✅ |
| Animated captions, templates, emojis | ✅ templates; 🔨 auto-emoji, kinetic polish | ✅ strong |
| Caption accuracy on noisy audio | 🟡 faster-whisper CPU int8; loop filter ✅ | ✅ |
| Burned-in subtitle detection (avoid double captions) | 🟡 OpenCode R2 library, not wired | ✅ |
| B-roll | ✅ keyed (Pixabay/Coverr) + vision veto; 🟡 keyless Openverse | ✅ |
| Music/SFX, licence-clean | ✅ 89 tracks / 289 SFX, gate ship-clean | ✅ |
| Transitions library | 🟡 248 (OpenCode R2), not wired | ✅ |
| Social publishing + performance learning loop | ✅ code (gated rollout); 🟡 learning on real metrics | ✅ |
| OCR of on-screen text (memes, chat overlay) | 🔨 (tesseract/PaddleOCR CPU, ₹0) | ✅ |
| Video-language understanding ("what happens") | 🔨 small local VLM (e.g. Qwen2-VL-2B) feasible on 6 GB VRAM, slow; ❌ big VLM | ✅ |
| Trained virality model | ❌ no data at that scale; 🔨 per-user learning only | ✅ |
| Speed | 🟡 ~45–55 s per 60 s clip on CPU (4K source); GPU encode pending NVENC fix | ✅ cloud GPUs |
| **Live clipping during the stream** | 🔨 (Part 2) | ❌ OpusClip clips after upload/VOD |
| **Live chat signals** | 🔨 (Part 3) | ❌ |

---

## PART 2–6: Live AI clipping (feasibility; not started)

### What exists that we reuse

| Need | Existing module |
|---|---|
| Source URL parsing, Twitch/YouTube detection | `source_submission.py` (twitch.tv recognised), `youtube_utils.py` (`live_status`) |
| Auto-create tasks from a channel | `services/channel_monitor_service.py`. Today it **waits for the VOD**; live clipping is its natural sibling |
| Reaction / excitement signals on audio + facecam | `auto_director/reaction_moments.py` (mic channel, scream, laugh, face activity, game episodes, `correlate`) |
| Visual events | `auto_director/story_events.py` (Step 4), `visual_moments.py` (scene cuts) |
| Stimulus framing | `media/screen_subject.py` (Step 3), facecam layouts, gaming templates |
| Clip from given ranges (no AI re-selection) | Manual Cutter path in `video_service` (`manual_selection`) |
| Edit + render | `build_edl` + `render_edl` + captions (Steps 1–5) |
| Progress to the browser | Redis pub/sub → SSE (`workers/progress.py`) |
| Publishing + metrics | `social/`, `social_service.py` |
| Workers | ARQ worker + AI worker containers |

### What must be built

1. **Live ingest service** (new container `live-ingest`). It records the stream as HLS segments: `streamlink` or `yt-dlp` → `ffmpeg -c copy -f segment`, 2–6 s `.ts` segments plus an index (segment → wall-clock from `EXT-X-PROGRAM-DATE-TIME`). There's no re-encoding and no re-downloading.
2. **Rolling buffer manager.** A ring of segments (default: keep 30 min, configurable), and a **"cut from buffer"** that concatenates segments `[event − preroll, event + postroll]` with stream copy into a working file. Pre-roll is possible because the footage is **already on disk** before the event is detected.
3. **Chat ingest.** Twitch IRC read-only and YouTube Live Chat (below) → timestamped JSONL.
4. **Chat signal detector.** Message-rate z-score vs a rolling baseline, emote bursts, "LMAO/WHAT/NO WAY" lexicon, **unique-chatter ratio** (spam/bot filter), raid/host notices excluded, copy-pasta detection.
5. **Live moment fuser.** Candidates come from chat spikes, audio excitement (live RMS/pitch per second), and reaction signals over the last N minutes. Each must be **confirmed by audio/visual** (chat alone never makes a clip). It is scored and de-duplicated by time window.
6. **Two-stage render.** Quick preview: stream copy + simple crop, a few seconds. Then the polished clip through the normal pipeline (Manual Cutter ranges on the buffer cut).
7. **Live dashboard.** A frontend page listing moments (timestamp, score, signals, preview player, "make clip" / "discard"), fed by SSE.
8. **Notifications.** In-app via SSE; push or email later.

### Data flow

```
Twitch/YouTube live ──HLS──▶ live-ingest (ffmpeg -c copy segments, 2–6 s) ──▶ buffer/  (+ index: seg → wall-clock)
        chat ──IRC/API──▶ chat-ingest ──▶ chat.jsonl (wall-clock)
                                   │
   every 5–10 s: signals over the last minutes (chat rate/emotes, audio RMS/pitch, face, visual events)
                                   ▼
                     live moment fuser ──▶ candidate {t_event, score, signals}
                                   ▼  (confirm with audio/visual; dedupe; priority queue by score)
           cut buffer [t−preroll, t+postroll] (stream copy, seconds) ──▶ preview MP4 ──▶ dashboard (SSE)
                                   ▼
           normal pipeline (Manual Cutter range: captions, framing Steps 1–5) ──▶ final clip ──▶ notify
```

### Part 4: the 12 technical questions

| # | Question | Answer |
|---|---|---|
| 1 | Ingest continuously without re-downloading? | Yes. One persistent HLS pull per stream (`streamlink`/`yt-dlp` → `ffmpeg -c copy`), appending segments. |
| 2 | Rolling buffer with segments? | Yes: `ffmpeg -f segment -segment_time 4 -reset_timestamps 1` plus a ring delete. Standard and cheap (no encoding). |
| 3 | Footage from 30 s **before** detection? | Yes. It's already in the buffer when the spike is detected (detection lags 5–15 s); pre/post-roll are configurable per event type. |
| 4 | Keep recording while rendering? | Yes. Ingest is a separate process doing stream copy (~1–3% CPU); rendering runs in the worker. |
| 5 | No gaps between segments? | Contiguous media sequence numbers from the HLS playlist; a gap is detected by a sequence jump and logged as a hole (a clip spanning a hole is refused or marked). |
| 6 | Sync video, audio and chat? | Video and audio share the segment timeline. Chat ↔ video via wall-clock: Twitch IRC `tmi-sent-ts` / YouTube `publishedAt` vs HLS `EXT-X-PROGRAM-DATE-TIME`. The **viewer delay** (2–15 s, chat reacts *after* seeing the moment) is measured per stream and subtracted. |
| 7 | Interruptions, reconnects, expired URLs, no VOD? | Watchdog restarts the pull with fresh playlist URLs (they expire); the buffer keeps what it has; the stream-ended state is detected; we **never depend on a VOD**. |
| 8 | 3 / 6 / 10-hour streams? | Yes with a ring buffer (disk bounded); optionally keep everything (~2.5–3.5 GB/h at 1080p60). |
| 9 | CPU / RAM / GPU / storage? | Ingest: ~1–3% CPU, ~100 MB RAM. Signals: audio features trivial; SigLIP every 2 s ≈ 1 frame/s on CPU, fast on GPU. Live transcription of the buffer (faster-whisper small/base) ≈ 0.3–0.6× real time on a 4050 **when CUDA libs are present** (they're missing in the dev venv today: "cublas64_12.dll not found"). Storage: 30-min ring ≈ 1.5–2 GB; whole 10 h ≈ 25–35 GB. Each final render: 45–60 s CPU now, faster with NVENC. |
| 10 | RTX 4050 6 GB + 16 GB RAM? | **One live stream: yes**, with Docker + ingest + signals + one render at a time. RAM is the constraint (we hit out-of-memory today with other apps open). Two concurrent streams: not recommended. |
| 11 | What runs locally for ₹0? | Everything: ingest (ffmpeg/streamlink), Twitch chat (read-only), signals, SigLIP, Whisper, rendering. The YouTube Live Chat API is free within quota but needs a Google Cloud project + API key (**your sign-up; I'll ask**). The LLM step stays on the free Groq tier or local Ollama. |
| 12 | Platform restrictions / ToS / legal? | See below. **This is the biggest constraint.** |

### Platform access: what is legitimate

| Platform | Video ingest | Chat | Notes |
|---|---|---|---|
| **Twitch** | HLS playback via `streamlink`/`yt-dlp`. It's the public player stream, not an official recording API. | **IRC read-only** (`irc.chat.twitch.tv`, anonymous `justinfan` login works for public channels) or EventSub chat (needs a registered app + token). | Twitch's terms: you may not redistribute others' content without permission. **Safe scope: the user's own channel, or streamers who authorise it.** The official alternative for clips is the Twitch API **Create Clip** (user token, `clips:edit`), which captures from Twitch's own buffer. Worth offering for own-channel users. |
| **YouTube Live** | `yt-dlp` on a live URL (`--live-from-start` within the DVR window). Not an official API. | **YouTube Data API v3 `liveChatMessages`** (polling, quota-limited on the free 10,000 units/day; a 3 h stream polled every ~5 s can exhaust it, so polling interval and stream count must be budgeted; verify current quota costs at build time). Needs an API key. | Same ownership rule: the user's own streams or authorised ones. Unofficial chat scrapers (e.g. pytchat) are **not** used: against ToS, and they break. |
| Kick, others | Out of scope for v1. | — | — |

**Must-haves:** an ownership/authorisation checkbox per stream, no downloading of DRM or members-only streams, buffer auto-deleted after the session unless kept, rate-limit-respecting polling.

### Part 5: clip selection logic for live

A candidate is created when any trigger fires: a chat spike, audio excitement, a facecam reaction burst, or a game/visual event. It is **kept** only when:
- at least one audio/visual signal confirms it (chat alone never counts);
- the unique-chatter ratio is normal (a bot spam or raid burst has few unique chatters, or a raid notice nearby);
- the moment is not a repeat of a clip made in the last N minutes.

The **window is chosen by content, not a fixed 30 s**:
- **Start:** walk back from the reaction to the stimulus. That's the last scene change or sentence start before it, the screen-run start (Step 3), or the game event, bounded by the pre-roll range (default 15–45 s).
- **End:** the payoff finishes (Step 4's extension: speech ends, the applause decays), bounded by the post-roll.
- **Ranking:** a fused score (chat surge × confirmation strength × story completeness).

The existing reaction picker's `correlate` / coincidence rule is the template for this.

### Part 6: speed

| Stage | Time after the event (1 stream, your laptop) |
|---|---|
| Chat/audio spike detected | 5–15 s (viewer delay + analysis window) |
| Candidate confirmed (audio/visual) | +5–10 s |
| Quick preview (stream copy cut, centre/face crop, no captions) | +5–15 s → **≈ 30–40 s after the moment** |
| Final clip (transcribe 60 s, captions, framing, render) | +60–120 s on CPU → **≈ 2–3 min after the moment** |

Trade-offs:
- The preview is fast but rough. The final is polished but takes minutes.
- Processing only candidate windows (not the whole stream) is what makes it fast. The cost: moments without any trigger signal can be missed, which the after-stream full analysis (existing pipeline) still catches.
- Duplicates are suppressed by a time window (default 90 s) and by merging overlapping candidates.

### Part 7: decision as engineering lead

| # | Item | Answer |
|---|---|---|
| 1 | Feasible? | **Yes, for the user's own (or authorised) Twitch/YouTube streams, one stream at a time on your laptop.** Not for clipping arbitrary third-party streams at scale (ToS + resources). |
| 2 | Reuse | Table above: reaction/visual signals, Steps 1–5 pipeline, Manual Cutter ranges, SSE, workers, channel monitor pattern, publishing. |
| 3 | New modules | `live/ingest` (segmenter + watchdog), `live/buffer` (ring + cut), `live/chat_twitch`, `live/chat_youtube`, `live/chat_signals`, `live/fuser`, `live` API routes + dashboard page, migration (live_sessions, live_moments). |
| 4 | APIs / auth | Twitch: anonymous IRC (read) or app + token for EventSub / Create Clip. YouTube: Data API key (free, your Google Cloud project). No paid services. |
| 5 | ₹0 options | All local. YouTube chat needs a free API key (asked at build time). |
| 6 | Architecture | Data flow above. **A separate `live-ingest` service + `live` module that feeds the existing pipeline**: it reuses the editing engine, but its lifecycle (hours-long session, buffer, watchdog) is too different to sit inside task processing. |
| 7 | Estimate | **MVP (own Twitch channel: ingest + buffer + Twitch chat + chat/audio fuser + preview + clip via existing pipeline + simple dashboard list): 8–12 working days.** + YouTube Live chat: 2–3 days. + Polished dashboard, notifications, Create-Clip integration, VLM confirmation: 1–2 weeks more. Total for a solid v1: **~3–4 weeks**. |
| 8 | Testing | (a) Recorded fixtures: chat logs + VODs replayed as fake live (deterministic, in CI). (b) Your own test stream (OBS → Twitch, unlisted/low-traffic) with scripted events (a meme on screen, scream, chat spam test from 2 accounts). (c) One authorised real stream. Evidence: moment list with timestamps vs ground truth, preview + final clips, latency per stage. |
| 9 | Risks | Platform changes breaking HLS pulls; YouTube chat quota; RAM on 16 GB; legal scope (own/authorised only); chat-delay calibration; false positives from raids/spam; CUDA not available in the venv (slower). |
| 10 | Separate or integrated? | **Separate live module + service, integrated at the clip step** (it calls the existing pipeline with explicit ranges). No duplicate editing engine. |

### Consequences for current work

The 18:00 delivery to Sanji (Steps 1–5, validation, OpenCode R2/R3 packaging) continues **unchanged**. Live clipping starts only after you approve this report, and it doesn't touch Phase 1/2 code paths, only new modules plus a call into the existing pipeline.

### OpenCode: proposed isolated task (start ONLY after your approval)

Brief: `D:\SnipNova-Dev\handoff\opencode-live\BRIEF_chat_signals.md`. It covers a pure, testable chat-signal library + Twitch read-only chat recorder. No video, no pipeline edits, no overlap with my files.

---

## FINAL: can our architecture deliver both products?

**A. Normal clipping: yes, it's the current product.** After today's Steps 1–5 (once you approve the renders), editing quality is close to OpusClip's on restraint and framing, and better on streamer/reaction stimulus framing. To *beat* them on selection we need the measurement loop above, OCR + a small local VLM confirmation step (₹0, slower), and real performance data from published clips.

**B. Live clipping: yes, as a new module on the same engine**, for own/authorised streams, one stream at a time on your laptop, preview ≈ 30–40 s and final ≈ 2–3 min after the moment. That speed is a genuine advantage OpusClip doesn't offer today. It's ~3–4 weeks to a solid v1, and **chat-plus-confirmation** is the differentiator.

What it does **not** become: a system that legally clips anyone's stream, or one that matches OpusClip's trained selection on day one. That part is earned with data.
