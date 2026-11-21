# Claude's fixes to the installed app, 2026-11-21 ~22:30 IST (not in any git repo yet)

These edits were made directly in D:\EditedTool\JarvisClipStudio\backend\src (bind-mounted).
Backups of the files from before the fixes (which already contained Antigravity's partial edits) are in backup\.
**Codex: port these into feature/live-quality.**

1. media/ffmpeg.py: Antigravity's per-task ffmpeg registry, corrected:
   - It now uses a contextvars.ContextVar instead of threading.local. asyncio.to_thread (run_in_thread) copies the context, so pipeline threads are tagged.
   - TimeoutExpired is raised again on timeout, as subprocess.run does.
   - Proven: with a long ffmpeg run via to_thread, kill_task_procs freed it in 3.2 s, and a timeout still raises.
   - Limit: covers run_ffmpeg_command only. Direct subprocess.run calls elsewhere (yt-dlp, some ffmpeg) are not registered yet.
2. workers/tasks.py: a `_cancel_watch` asyncio task polls `task_cancel:{id}` every 2 s and calls kill_task_procs; it is cancelled in `finally`.
3. services/video_service.py: **reverted** Antigravity's change that sent YouTube (and any audio_first source) into create_fast. That path calls download_stream_sections (the Twitch/Kick downloader) with a YouTube URL, which is untested. Fast mode for YouTube in Create is still TODO: it needs the YouTube section downloader.
4. docker-compose.yml: Antigravity set RENDER_PROFILE default to `speed`. Kept; it applies on the next worker recreate.

All of this takes effect on the next worker restart. It was NOT restarted, because GTA task 9495f237 is rendering.
Still open: slow rendering (about 4.7 min/clip; Antigravity says per-clip AI analysis + the B-roll vision model take 3–4 min of that), and YouTube fast mode.
