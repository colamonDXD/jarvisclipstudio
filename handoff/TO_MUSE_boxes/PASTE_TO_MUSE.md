# To Muse AI (Sanji): finish "draw your own face + gameplay boxes" and borrow from 2 MIT repos

From Claude (engine lead), 2026-11-19. I'm stopping because my weekly limit is nearly used up.

## Rules (same as always)
- Work tree: `D:\SnipNova-Dev\v12-base` (git). Installed app: `D:\EditedTool\JarvisClipStudio`.
- Never touch the installed app's `.env`, Docker volumes or backups. Run `docker compose` from a neutral cwd.
- No paid APIs, no sign-ups, no Gemini, no keys in chat or commits. Every new behaviour goes behind a switch.
- Never delete, skip or weaken a test. Don't claim a feature works until a test or a real render proves it.
- Commits: `type(scope): summary`.

## 1. What I already built (commit `8d41886`, patch in this folder)
It compiles but is **not tested and not deployed**.

- `backend/src/media/gaming_templates.py`:
  - `MANUAL_BOXES` is a contextvar.
  - `validate_boxes()` cleans `{"cam":{x,y,w,h}, "game":{x,y,w,h}, "arrange":"top"|"bottom"}`, where each box is a fraction (0..1) of the source frame.
  - `build_manual_stack_layout()` makes the face/game stack straight from the drawn boxes. There is no detection: each box gets a cover crop at its panel's aspect, then the existing divider, credit and seam captions are added. `arrange: "bottom"` puts the game on top and the face below.
- `media/reframing.py`: when boxes are set and a layout template is chosen, the manual layout is returned before any face analysis.
- `media/reframe_plan.py`: when boxes are set, the clip is sent to the plain path (`_attach…` and `_layout_facecam_found`).
- `live/moments.py`: `creator_on_camera(..., cam_box=None)`. A drawn cam box replaces `find_facecam_rect`, but the face-visible check still runs inside that box.
- `live/finisher.py`: `render_live_clip(..., boxes=None)` sets and resets `MANUAL_BOXES` around the render.
- `live/session.py`, `live/vod.py`, `workers/live_worker.py`: the `boxes` setting is passed through, stored in `live_sessions.settings` (JSONB, so no migration).
- `api/routes/live.py`: `POST /live/sessions` accepts `"boxes"`; bad boxes return 422.

## 2. What you need to do
1. **Tests** in `backend/tests/test_live_boxes.py`:
   - `validate_boxes`: good input; a box missing a key; a box outside the frame; a bad `arrange`.
   - `build_manual_stack_layout`: the crops stay inside the frame, have even sizes and the right aspect; "bottom" swaps the vstack order.
   - The API returns 422 on bad boxes.
   - A real ffmpeg render of a 1280x720 test video with boxes gives a 1080x1920 output.
2. **Frame endpoint:** `GET /live/frame?url=…&at=…`. It runs `yt-dlp -g -f "best[height<=720]/bv*[height<=720]"` (use `yt_dlp_binary()` from `src/live/__init__.py`), then `ffmpeg -ss at -i <url> -frames:v 1` to a JPEG, and returns `image/jpeg`. Run the blocking work with `run_in_thread`. Use auth like the other `/live` routes. Use at=1200 s for a finished stream and 0 for a live one.
3. **Live page UI** (`frontend/src/app/live/page.tsx`): a "Draw boxes" step.
   - Show the frame on a canvas. Two modes, "Face box" (pink) and "Gameplay box" (cyan); drag to draw. Store the boxes as fractions.
   - Show a live 9:16 preview of the result, plus a Top/Bottom toggle.
   - Save and Load in localStorage, keyed by channel, so the same streamer's layout comes back. Send `boxes` with the session.
   - Reference: NeonClipping `app.js`, lines 37–45 (pointer drawing) and line 100 (canvas preview).
4. **Deploy** to the installed app:
   - The backend is bind-mounted, so a restart picks it up: `restart backend worker live-worker`.
   - The frontend needs `docker compose build frontend` and then `up -d --no-build --no-deps frontend`.
   - Prove it with one real clip, using drawn boxes, on a Kai Cenat VOD.

## 3. Worth taking from the two repos (both MIT; credit them in NOTICE)
Clone them into `D:\SnipNova-Dev\ext\` (NeonClipping is already there).

**NeonClipping** (https://github.com/iamajmeena/neonclipping)
- **YouTube captions as the transcript** (`captions_yt.py`, ~45 lines). This reads YouTube's json3 auto-captions: word-timed, about 1 s for a 3-hour video, with no speech model. Use it in `live/vod.py` before Groq or Whisper, and fall back to those when a video has no captions. This is the biggest speed win for 10-hour streams.
- **Star ratings → re-weighted picking** (`learn.py`). This is ridge regression shrunk toward the defaults, starting after 8 ratings. Add a 1–5 star control on Live clips, store the clip's features, and refit `selection_params` weights from the ratings.
- **Shout signal** (`highlights.py` `_audio`): the 2–4 kHz energy share × loudness. It's a cheap extra feature for `voice_scan`.
- **Exclamation lexicon** (`excite.py`): "oh my god", "let's go", Hinglish "arre", "bhaago" and so on, as a per-second signal from the transcript. Add it as a feature in `selection.rank_moments`.
- **Snap to a sentence end** (`smart.py` `snap`): a clip starts where speech starts and ends on a `.?!` inside min..max.
- **More layouts** (`layouts.py`): picture-in-picture in 4 corners, circle cam, side by side, blurred fill, gameplay only. Add them as `arrange` values in `build_manual_stack_layout`.

**PeakCut** (https://github.com/haziqahmedqasmi/PeakCut, 1.3k stars)
- **Local Parakeet ASR** via sherpa-onnx. It's fast on CPU and may beat faster-whisper small for the live worker. Benchmark it on one 30 s window.
- **Kick and Twitch links** for VOD and live, not just YouTube.
- **An AI usage page** (requests and tokens per video) and a **storage page** (free space, move the data folder).
- **Auto-post rules**: spacing and a daily cap. We already have social publishing; add the rules.

Our current edge, so don't break it: the YouTube "most replayed" heatmap signal and trained selection, music/SFX/B-roll, karaoke captions, quote titles, swear masking and the 30 s minimum. Neither repo has transitions, SFX or zooms.

## 4. Still open on my side (after my limit resets Monday)
Real editing variety: punch-in on the punchline, varied transitions, reaction-matched SFX, word pop-ups.
