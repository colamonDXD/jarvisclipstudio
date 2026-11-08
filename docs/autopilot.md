# Jarvis Autopilot — Watch Folder

Autopilot turns JarvisClipStudio into a hands-off clipping queue. You drop
videos (or lists of video links) into one folder; Autopilot feeds them to the
app through its public REST API — the exact same endpoints the web UI uses —
watches each task until it finishes, and files the source away with a result
or error report. No browser, no clicking, no paid services.

```
D:\Jarvis_Autopilot\
    INBOX\        <- drop videos / .txt link lists here
    PROCESSING\   items the app is working on right now
    COMPLETED\    finished sources, each with a <name>.result.json
    FAILED\       failed sources, each with a <name>.error.json
    ARCHIVE\      spare folder for your own long-term storage
    state.json    Autopilot's memory (managed automatically)
    autopilot.log every decision Autopilot made, with timestamps
```

## Setup (one time, ~3 minutes)

1. **Start the app** as usual (`docker compose up -d`) and open it in the
   browser once.
2. **Create an API key**: in the app, go to **Settings → API Keys** and create
   a key. It starts with `sk_`. This key is how Autopilot proves to the app
   that it acts for your account — the same mechanism the project's MCP server
   uses. Treat it like a password; you can revoke it in the same screen.
3. **Configure Autopilot**: in `tools\autopilot\` (inside the project folder),
   copy `settings.example.json` to `settings.json` and paste your key:
   `"api_key": "sk_..."`. (Or set the `SUPOCLIP_API_KEY` environment variable
   instead — it wins over the file.)
4. **Start Autopilot**: double-click `tools\autopilot\start-autopilot.bat`,
   or run it yourself:

   ```powershell
   cd D:\EditedTool\JarvisClipStudio\tools\autopilot
   python autopilot.py "D:\Jarvis_Autopilot"
   ```

   Autopilot uses only the Python 3 standard library — nothing to install.
   It creates `D:\Jarvis_Autopilot` and its subfolders on first run.

## Daily use

- Drop `.mp4` / `.mov` / `.webm` / `.mkv` files into **INBOX**. Autopilot
  uploads each one and creates a task with your saved settings.
- Or drop a **`.txt` file with one video URL per line** (YouTube etc.). One
  task is created per URL; the list is finished when every task is finished.
  Lines starting with `#` are comments.
- Items are picked up **alphabetically**, one at a time by default
  (`max_parallel`, 1–3).
- When a task completes, the source moves to **COMPLETED** with a
  `<name>.result.json` beside it: the task id, how many clips were made, and
  every clip exactly as the API reports it (filename, server path, start/end,
  duration) plus a ready-to-open file URL per clip.
- When a task fails, the source moves to **FAILED** with a
  `<name>.error.json` containing the app's own error message.
- Press **Ctrl+C** to stop. Restarting later resumes exactly where things
  left off (see crash recovery below).

## Settings reference (`tools/autopilot/settings.json`)

| Key | Default | Meaning |
| --- | --- | --- |
| `api_url` | `http://127.0.0.1:8000` | Backend API base URL (the backend port from docker-compose). |
| `api_key` | — | Your app API key (`sk_...`). Required unless `user_id` is used. |
| `user_id` | — | Fallback: sent as `x-supoclip-user-id`. Only works if the backend allows unsigned auth. Prefer `api_key`. |
| `poll_interval_seconds` | 15 | How often INBOX is scanned. |
| `task_poll_interval_seconds` | 20 | Task status polling cadence (the loop wakes at the smaller of the two intervals). |
| `max_parallel` | 1 | Items processed at once (1–3). Start with 1 on a 6 GB GPU. |

The `task` object holds the defaults used for every created task. The field
names and allowed values are the app's own (validated against the backend):

| Key | Values | Meaning |
| --- | --- | --- |
| `niche_override` | `null` or one of `podcast_news`, `travel_vlog`, `meme_comedy`, `gaming`, `sports`, `education_tutorial`, `finance_business`, `vlog_lifestyle`, `true_crime`, `hindi_podcast`, `music_artist`, `football_edit`, `japan_travel_cinematic`, `anime_funk_edit` | `null` = the app auto-detects the niche. |
| `music_override` | `null` or a track name from the Music library | `null` = profile mood decides. |
| `clip_length` | `auto` (15–60 s), `short` (15–30 s), `medium` (30–60 s), `long` (60–120 s) | Clip duration preset. |
| `output_format` | `vertical`, `vertical_fit`, `vertical_pan`, `vertical_split`, `original` | Aspect/reframe (default `vertical` = 9:16). |
| `processing_mode` | `fast`, `balanced`, `quality` | Pipeline mode. |
| `add_subtitles` | true/false | Burn captions into clips. |
| `caption_template` | e.g. `default` | Caption preset name from the app. |
| `include_broll` | true/false | Stock B-roll cutaways (needs the app's Pixabay key for footage). |
| `cut_long_pauses` / `remove_filler_words` | true/false | Cleanup toggles (same defaults as the UI). |
| `pause_threshold_ms` | 250–3000 (default 1200) | Pause length treated as "long". |
| `filtered_words` | list of words | Extra filler words to cut. |

> **There is no clip-count setting** — the app's create-task API does not
> accept one (checked in the backend route, the web UI payload, and the MCP
> client). The pipeline decides how many worthwhile clips a video yields;
> `result.json` reports the real count.

## Crash recovery — exactly what happens

Autopilot saves every decision to `state.json` **before** acting on it, using
atomic writes (write-temp-then-replace), so a crash can never leave a
half-written state file. On startup it reconciles state with the disk:

- **PROCESSING file with recorded task ids** → polling resumes. Nothing is
  re-uploaded and no duplicate task is created. (For file uploads Autopilot
  also sends a content-derived `Idempotency-Key`, so even a re-submission of
  the same file reuses the stored upload and queued task server-side.)
- **PROCESSING file with no recorded task** (crashed mid-submission, or a
  file you copied there by hand) → moved back to **INBOX** for a safe retry.
- **A URL list that crashed half-submitted** → the remaining URLs are
  submitted on the next cycle; already-created tasks are not duplicated.
- **State says PROCESSING but the file already sits in COMPLETED/FAILED**
  (crash between the move and the save) → the record is corrected to match.
- **Recorded file missing from disk entirely** → marked `missing` in
  state.json and logged; nothing is invented or retried.
- **Corrupt state.json** → backed up to `state.json.bak-<timestamp>` and
  Autopilot starts fresh, logging the fact loudly.

## Honest limitations

- **The app must be running.** Autopilot only talks HTTP to
  `http://127.0.0.1:8000`; if Docker/the backend is down, polls fail with a
  warning and are retried next cycle — items are never failed just because
  the app was briefly unreachable.
- **File uploads go through `POST /media/upload`** — the same backend
  endpoint the web UI's own proxy upload path uses. (The UI additionally
  offers a direct-to-S3 path when object storage is configured; Autopilot
  always uploads through the backend, which stores the file and hands it
  onward exactly as it does for UI uploads.)
- **A mixed URL list is all-or-nothing for filing**: if any URL's task
  fails, the list lands in FAILED — but only after every other task has
  finished, and `error.json` lists each task's individual outcome. The
  successful clips still exist in the app.
- **Autopilot never guesses success.** Only the app's exact `completed`
  status finishes an item; `error`/`cancelled` fail it; any unknown status
  (or a task that stops answering) means "keep waiting". A task the app
  itself abandons is eventually marked `error` by the app's own stale-task
  watchdog, and Autopilot files it then.
- **Billing rules still apply**: if your app account cannot create tasks
  (for example a plan limit returns HTTP 402), that error lands in FAILED
  with the app's message — Autopilot cannot and does not bypass it.
- **Your API key sits in `settings.json` in plain text** (like most local
  tool configs). Keep the project folder private, and revoke the key in the
  app if it ever leaks.
- Autopilot does not download finished clips for you — `result.json` gives
  you the in-app file URL for every clip, and the app's own ZIP download
  remains the bulk option.

## API used (for developers)

Verified against this repository at the time of writing:

| Call | Source of truth |
| --- | --- |
| `POST /media/upload` (multipart, field `video`) → `{"video_path": "upload://…"}` | `backend/src/api/routes/media.py` (`upload_video`) |
| `POST /tasks/` → `{"task_id", "job_id", "message"}` | `backend/src/api/routes/tasks.py` (`create_task`); payload mirrors `frontend/src/components/home-app.tsx` |
| `GET /tasks/{id}` → status/progress/`progress_message`/clips | same file (`get_task`) |
| `GET /tasks/{id}/clips` → `{"clips": [...], "total_clips": int}` | same file (`get_task_clips`) |
| Auth `Authorization: Bearer sk_…` | `backend/src/auth_headers.py`; precedent: `mcp/src/supoclip_mcp/client.py` |
| Terminal statuses `completed` / `error` / `cancelled` | `mcp/src/supoclip_mcp/server.py` (`TERMINAL_STATES`), SSE route in `tasks.py` |

Tests: `tools/autopilot/tests/test_autopilot.py` runs the full state machine
against a fake in-process HTTP server implementing these shapes
(`python -m pytest tools/autopilot/tests`).
