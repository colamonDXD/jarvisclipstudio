# ANTIGRAVITY: URGENT. Finish ONE stuck GTA clip job. Do ONLY this.

From Claude, for Luffy (owner), 2026-11-21 ~21:45 IST. Codex was fixing this job and ran out of usage halfway. Luffy has waited 5 hours. **Your only goal: get the 25 GTA clips rendered and visible in the app.** No new features, no refactors, no UI changes, no "while I'm here" edits.

## The app
- Installed app: `D:\EditedTool\JarvisClipStudio`, site http://localhost:3107. It runs in Docker:
  - containers `supoclip-worker`, `supoclip-backend`, `supoclip-postgres`, `supoclip-redis`, `supoclip-frontend`, `supoclip-live-worker`;
  - `backend/src` is bind-mounted into `/app/src`, so code edits apply on a container restart.
- Docker compose, ALWAYS from a neutral folder (never `cd` into the install folder):
  `docker compose -p jarvisclipstudio --project-directory "D:\EditedTool\JarvisClipStudio" -f "D:\EditedTool\JarvisClipStudio\docker-compose.yml" -f "D:\EditedTool\JarvisClipStudio\compose.override.yml" <cmd>`
- Database: `docker exec supoclip-postgres psql -U supoclip -d supoclip`.

## The stuck job (facts checked at 21:40 IST)
- Task `9495f237-e7db-401c-9b91-5b51a5114cb5`: a 5 h 29 min Twitch VOD, `https://www.twitch.tv/videos/2934325173` ("GTA Gaming & Gooning Marathon"), 25 clips requested.
- DB: `status=processing`, `progress=60`, `progress_message='Fetching footage for 25 selected clips...'`, last updated 20:54 IST. It hasn't moved since.
- The worker is **idle** (about 0% CPU), but Redis still holds `arq:in-progress:9495f237-…`. **The job is hung.**
- Already done by Codex, and saved on disk inside the worker (`$TEMP_DIR/...`):
  - the scan, the transcription of 31 candidate windows, and **all 25 clip selections, saved in the DB** for this task;
  - all 25 footage sections downloaded: `stream-work/9495f237-…/job/**/section-*.mp4`;
  - **a verified joined file:** `fast-recovery/9495f237-e7db-401c-9b91-5b51a5114cb5/joined.mkv` (1.07 GB) plus `joined.timeline.json`, which maps each section's source time to its local time in the joined file. Codex checked frames and audio against the source.
- Codex's code changes for the fast Create path are already deployed in `backend/src`:
  - `services/video_service.py`, `live/create_fast.py` (copies in `D:\SnipNova-Dev\codex\long-create-repair\`);
  - helper scripts there: `check_section_join.py` (how the join was built and checked), `check_gta_renders.py`, `youtube_source.py`.
- **Codex's unfinished last step:** "switch the same task to that verified joined file so it can start rendering without repeating the downloads or selection".

## What to do
1. Read `D:\SnipNova-Dev\codex\long-create-repair\check_section_join.py`, `check_gta_renders.py`, and the fast-path code in `backend/src/live/create_fast.py` + `services/video_service.py`. Understand how the selections and the joined file / timeline are meant to feed rendering.
2. Make the worker render the 25 saved selections **from `joined.mkv` using `joined.timeline.json`**:
   - do not download again; do not re-transcribe the whole stream; do not re-pick;
   - keep the task's own settings (clip length 30 s minimum, layout, drawn boxes if any, captions, title off).
   - Prefer the code path Codex built. If it needs a small fix, make the smallest one, in `backend/src`.
3. Getting the job moving:
   - Luffy approves stopping this hung job.
   - Clear the hung state: restart `supoclip-worker` (nothing else is processing), then re-run this task so it resumes from the saved selections and the joined file.
   - If the app has a resume path (`POST /tasks/{id}/resume`), use it. Otherwise re-enqueue properly via the app's own job queue (`backend/src/workers/job_queue.py`, queue `supoclip_tasks`).
   - **Do not delete** the task, `joined.mkv`, the sections, or anything in the Docker volumes.
4. Show progress honestly: the task page should go to "Rendering clips" with real counts ("clip 3/25").
5. Prove it:
   - the task becomes `completed` with 25 clips in the DB;
   - clips play at http://localhost:3107/tasks/9495f237-e7db-401c-9b91-5b51a5114cb5 and in the Clips library;
   - ffprobe one clip: 1080×1920, H.264 + AAC, at least 30 s.
   - Tell Luffy the time each clip took to render.

## Hard rules
- **Never** print, paste, log or commit API keys. Don't touch `.env`, `compose.override.yml`, Docker volumes or backups (except READING files inside the worker's temp dir).
- No new features, no UI redesign, no refactors, no dependency changes, no edits outside what this job needs. Tell Luffy exactly which files you changed and why (diff).
- Don't restart `backend`, `postgres`, `redis` or `frontend`. Only `supoclip-worker`, and only for this job.
- Don't claim it works until the clips exist and play. If you get stuck, stop and report what you found. Don't improvise risky fixes.
- Luffy's clip rules: captions white with each word turning yellow as spoken, about 52 px, every word shown; **no titles**; no emoji; `***` only for real swears; 30 s minimum.
