# What I changed inside Luffy's installed app (`D:\EditedTool\JarvisClipStudio`)

**Why:** Luffy, 2026-11-18: "u only build it add to website now fastttt i need to clip live stream". Every change below is in series B (except the two install-only compose tweaks, marked). Originals are backed up in `D:\SnipNova-Dev\handoff\live-install-backup\`.

**Not touched:** `.env`, the database volumes, backups.

## Files copied from my V12 tree

**Backend** (bind-mounted, applied by restarting `backend`, `worker`, `live-worker`):

- New:
  - `backend/src/live/` (the whole module, incl. `moments.py`, `finisher.py`, `session.py`, `groq_transcribe.py`);
  - `backend/src/api/routes/live.py`;
  - `backend/src/repositories/live_repository.py`;
  - `backend/src/workers/live_worker.py`;
  - `backend/src/migrations/sql/20261118_0001_live_clipping.sql`.
- Modified:
  - `backend/src/main.py` (the router).
- `backend/src/video_utils.py`: briefly carried my wrong `1cdd8c6` fix (the 3 s clips). It was **restored to the original V12 file** and stays original until the series-B fixes are deployed.

**Frontend** (needs `docker compose build frontend`):
- `frontend/src/app/live/` (page + layout);
- `frontend/src/app/api/live/[...path]/route.ts`;
- `frontend/src/components/app/app-shell.tsx` (the Live nav entry);
- `frontend/src/components/home-app.tsx` and `layout-picker.tsx` (the Layout picker and Title switch).

**`docker-compose.yml`:**
- `LIVE_CLIPPING` / `LIVE_MAX_SESSIONS` passthroughs on `backend` and the worker anchor;
- the new `live-worker` service.
- **Install-only:**
  - `LIVE_CLIPPING` defaults to **on** (`${LIVE_CLIPPING:-on}`); the repo says `off`.
  - `live-worker` has `image: jarvisclipstudio-worker:latest` so it reuses the worker image instead of building an 11 GB copy.

**`compose.override.yml`** (install-only, same pattern as `backend` and `worker`):

```yaml
  live-worker:
    environment:
      - GROQ_API_KEY=${GROQ_API_KEY}
      - NVIDIA_VISIBLE_DEVICES=all
      - NVIDIA_DRIVER_CAPABILITIES=compute,video,utility
    deploy: {resources: {reservations: {devices: [{driver: nvidia, count: 1, capabilities: [gpu]}]}}}
```

## Database rows

- **Test account:** `testdrive-v12@snipnova.test`, created through the sign-up API. Its password is in `handoff\TESTDRIVE_ACCOUNT.txt`.
- **Live sessions:** the two Speed sessions were moved to Luffy's own user so he sees them.
- **Unfinished clips:** those of the stopped sessions were marked `failed` ("stopped before it finished").

## Containers restarted by me

- `backend`
- `worker`: this stopped Luffy's task `c8dfde54` at clip 5/15, deliberately, because it was producing the 3 s clips.
- `frontend`: rebuilt.
- `live-worker`: new.
