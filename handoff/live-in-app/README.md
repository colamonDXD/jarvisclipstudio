# Live clipping in the website: backend done, UI is yours (Sanji)

Luffy: "i want that live clipping in website". The backend, worker and API are built and tested on the V12 base. What's left is the page, the `/api/live/*` proxies, and the installer.

## Patches (on V12 combined tip `1e8d405`, in order)

| # | Patch | What |
|---|---|---|
| 0001 | `fix(render): gaming layout template is no longer skipped on Auto clips` | **Bug found in the V12 test-drive.** An Auto task with `layout_template: facecam_red` rendered as a plain crop with the facecam floating over the game. The direct EDL render skips the layout plan that builds the stack. Compare `facecam_red_BEFORE_fix_strip.png` and `facecam_red_after_fix_strip.png`. |
| 0002 | `feat(live): live clipping in the app` | Migration, repository, `live-worker` container, `/live/*` routes, tests. |

**Tests (OpenCode's clean venv, test DB):**
- new: 25 passed (`test_live_moments`, `test_live_api`, `test_live_worker`, plus the migration-safety and compose-passthrough pins);
- render, layout and settings regressions touched by 0001: 172 passed (`test_gaming_templates`, `test_framing_mode`, `test_render_stage_timings`, `test_separation_render`, `test_speaker_turns_edl_skip`, `test_speed_pack_3`, `test_settings_integrity_matrix`, `test_broll_toggle`).

**End to end (a real file played into the recorder at 1× speed, as if live):**
- record → voice flag → hook-first window → quick preview ready **63 s** after the moment → finished clip **124 s** later (`sample_live_clip_nogate.mp4`, `sample_live_preview.mp4`);
- with the facecam gate on, the same moment was correctly rejected: the face was still (1.4× usual movement; 1.8× needed), so the scream came from the game.

## Switch

- `LIVE_CLIPPING` is **off** by default.
  - Off: every `/live/*` route returns 403, except `GET /live/status` → `{"enabled": false}` (use it to hide the nav entry).
  - Off: the worker idles.
- `LIVE_MAX_SESSIONS` (default 1) is how many streams the machine records at once.
- Both are passed through in `docker-compose.yml` for `backend` and the worker anchor.

## Installer: new container `supoclip-live-worker`

- `command: .venv/bin/python -m src.workers.live_worker`, same image as the worker.
- Mounts the `uploads` volume: clips live in `/app/uploads/live/<session_id>/`, and the backend serves them from there.
- No healthcheck: it isn't ARQ.
  - A dead worker is detected by heartbeat: sessions with no heartbeat for 120 s become `error`.
- verify.ps1 should count the container as running.

## API (all need the normal signed user headers / API key)

| Method | Path | Notes |
|---|---|---|
| GET | `/live/status` | `{"enabled": bool}`, never 403 |
| POST | `/live/sessions` | body below → 201 `{"session"}`. 409 if the user already has one running. 422 with `detail.code` `LIVE_WAS_LIVE` / `LIVE_NOT_LIVE` / `LIVE_IS_UPCOMING` / `LIVE_ERROR` + `detail.message` (show the message) |
| GET | `/live/sessions` | `{"sessions": [...]}`, newest first |
| GET | `/live/sessions/{id}` | `{"session", "clips": [...]}`. Poll every 5 s while `status` is `requested`/`starting`/`recording` |
| POST | `/live/sessions/{id}/stop` | the worker stops within ~15 s; already-queued clips still render |
| DELETE | `/live/sessions/{id}` | only when not running (409); deletes the files |
| GET | `/live/clips/{id}/file?kind=clip\|preview` | the MP4 (use for `<video>` and Download) |

**POST body:**

```json
{
  "url": "https://www.youtube.com/watch?v=...",
  "layout_template": "facecam_red",
  "credit": "@Streamer",
  "hook_title": "GTA 6 LIVE",
  "niche": "gaming",
  "streamer_words_only": true,
  "max_clips": 30
}
```

- `layout_template` is `"facecam_red"`, `"facecam_gold"` or `null`. Use `null` for streams without a facecam (IRL, podcast); `null` also turns the on-camera gate off.
- `credit`: up to 40 characters; `hook_title`: up to 80.
- `streamer_words_only`: captions skip game dialogue.
- `max_clips`: 1–60.
- Only `url` is required.

**Session `status`:**
- `requested` → `starting` → `recording`;
- then `stopped` (user), `ended` (the stream finished; `last_error` says why), or `error`.

**Session fields:** `recording`, `buffered_seconds`, `restarts`, `last_error`.

**Clip `status`:**
- `queued`: preview ready, `has_preview: true`;
- `rendering`;
- `ready`: `has_clip: true`, `duration`;
- `failed`: `reason`;
- `rejected`: `reason` in plain words, e.g. "streamer barely talking", or "face visible but still at the moment… the voices were not a reaction".

**Clip fields:** `onset_at` (when he reacted, UTC), `signals` (`f0`, `scream`, `laugh`, `vocal`), `strength`, `talk_share`.

## Page (suggestion)

1. **"Go live"**: URL field, layout picker (Red / Gold / No facecam), credit, title.
2. **Session header:** a red REC dot while `recording`, minutes buffered, a Stop button.
3. **Clip grid, newest first:**
   - while `queued` or `rendering`, the preview plays with a "finishing…" badge;
   - `ready` gets Download and "Open in studio" (later);
   - collapse `rejected` items into "Skipped moments (n)" showing the reasons.

## Limits you should know

- **Speed:**
  - The voice pass needs about 4 minutes of stream before it trusts its baseline.
  - A clip is decided about 34 s after the moment (it waits for the footage after it).
  - The preview takes about 1 minute; the final render takes 1–2 minutes on this laptop's CPU.
- **Selection is voice only:** screams, laughs and loud talk. A quiet great moment isn't flagged (chat signals are the next step).
- **Rights:** only streams the user owns or may clip (campaigns). Say so in the form's help text.
