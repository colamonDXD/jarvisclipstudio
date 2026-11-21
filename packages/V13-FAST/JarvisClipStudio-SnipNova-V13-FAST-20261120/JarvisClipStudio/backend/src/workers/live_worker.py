"""Live clipping worker: runs the users' live sessions (own container).

  python -m src.workers.live_worker

A live session runs for hours, so it is not an ARQ job. This process polls
``live_sessions`` every few seconds, claims ``requested`` rows (at most
``LIVE_MAX_SESSIONS`` at once, default 1: the CPU budget of one machine),
runs each as a ``live.session.LiveSession`` and writes everything the
session reports into ``live_sessions`` / ``live_clips``. The session threads
never touch the database: their events go through a queue that this loop
drains. A user's stop is seen on the next heartbeat. With ``LIVE_CLIPPING``
off the process stays idle and claims nothing.
"""
from __future__ import annotations

import os

# Before observability reads it: logs and errors from here are the live worker's.
os.environ["SUPOCLIP_SERVICE"] = "live-worker"
# Live clips must be ready in minutes: the shared worker's speech model
# (openai-whisper medium on CPU took 8 min just to load) is far too slow.
# faster-whisper "small" transcribes a 30 s window in seconds. Override with
# LIVE_TRANSCRIPTION_PROVIDER / LIVE_WHISPER_MODEL.
os.environ["TRANSCRIPTION_PROVIDER"] = os.getenv("LIVE_TRANSCRIPTION_PROVIDER", "faster_whisper")
os.environ["WHISPER_MODEL"] = os.getenv("LIVE_WHISPER_MODEL", "small")

import asyncio  # noqa: E402
import logging  # noqa: E402
import queue  # noqa: E402
import socket  # noqa: E402
from pathlib import Path  # noqa: E402
from typing import Any, Dict  # noqa: E402

from ..database import AsyncSessionLocal  # noqa: E402
from ..live import live_clipping_enabled  # noqa: E402
from ..live.session import LiveSession, LiveSettings  # noqa: E402
from ..repositories.live_repository import LiveRepository  # noqa: E402

logger = logging.getLogger(__name__)

POLL_SECONDS = 5.0
HEARTBEAT_SECONDS = 15.0


def live_root() -> Path:
    return Path(os.getenv("TEMP_DIR", "/tmp")) / "live"


def max_sessions() -> int:
    try:
        return max(1, int(os.getenv("LIVE_MAX_SESSIONS", "1")))
    except ValueError:
        return 1


def settings_from_row(row: Dict[str, Any]) -> LiveSettings:
    s = row.get("settings") or {}
    return LiveSettings(
        url=row["url"], niche=s.get("niche") or "gaming",
        layout_template=s.get("layout_template"), credit=s.get("credit"), hook_title=s.get("hook_title"),
        request=s.get("request"), voice_fx=s.get("voice_fx"), boxes=s.get("boxes"),
        streamer_words_only=bool(s.get("streamer_words_only", True)),
        max_clips=int(s.get("max_clips") or (10 if s.get("mode") == "vod" else 30)),
    )


class Running:
    def __init__(self, row: Dict[str, Any], session: LiveSession, events: "queue.Queue"):
        self.row, self.session, self.events = row, session, events
        self.last_status: Dict[str, Any] = {}
        self.last_beat = 0.0


async def drain(repo: LiveRepository, run: Running) -> None:
    sid = run.row["id"]
    while True:
        try:
            event = run.events.get_nowait()
        except queue.Empty:
            return
        async with AsyncSessionLocal() as db:
            if event["type"] == "status":
                run.last_status = event
            elif event["type"] == "moment":
                await repo.record_moment(db, sid, event)
            elif event["type"] == "clip":
                await repo.update_clip(db, sid, event)


async def tick(repo: LiveRepository, worker_id: str, running: Dict[str, Running], now: float) -> None:
    # 1. events and heartbeats of the sessions this worker runs
    for sid, run in list(running.items()):
        await drain(repo, run)
        if now - run.last_beat >= HEARTBEAT_SECONDS or not run.last_beat:
            run.last_beat = now
            async with AsyncSessionLocal() as db:
                stop = await repo.heartbeat(db, sid, run.last_status or {"recording": run.session.recorder.running})
            if stop and not getattr(run, "stopping", False):
                # Stop recording and detecting now; clips already found keep
                # rendering and the session closes once they are done.
                logger.info("Live session %s: stop requested", sid)
                run.stopping = True
                await asyncio.to_thread(run.session.stop_recording)
        if getattr(run, "stopping", False) and run.session.ended:
            await asyncio.to_thread(run.session.stop)
            await drain(repo, run)
            async with AsyncSessionLocal() as db:
                await repo.finish_session(db, sid, "stopped")
            running.pop(sid, None)
            continue
        if run.session.ended and not getattr(run, "stopping", False):
            # Give the analyst one more pass over the last footage first.
            run.ended_ticks = getattr(run, "ended_ticks", 0) + 1
            if run.ended_ticks >= 3:
                error = run.session.recorder.last_error
                logger.info("Live session %s: stream ended (%s)", sid, error)
                await asyncio.to_thread(run.session.stop)
                await drain(repo, run)
                async with AsyncSessionLocal() as db:
                    await repo.finish_session(db, sid, "ended", (error or "")[:300] or None)
                running.pop(sid, None)
    # 2. sessions whose worker died
    async with AsyncSessionLocal() as db:
        await repo.fail_stale(db)
    # 3. new sessions
    if not live_clipping_enabled():
        return
    while len(running) < max_sessions():
        async with AsyncSessionLocal() as db:
            row = await repo.claim_requested(db, worker_id)
        if row is None:
            return
        events: "queue.Queue" = queue.Queue()
        try:
            settings = settings_from_row(row)
            if (row.get("settings") or {}).get("mode") == "vod":
                from ..live.vod import VodSession

                session = VodSession(settings, live_root() / row["id"], events.put).start()
            else:
                session = LiveSession(settings, live_root() / row["id"], events.put).start()
        except Exception as exc:
            logger.exception("Live session %s failed to start", row["id"])
            async with AsyncSessionLocal() as db:
                await repo.finish_session(db, row["id"], "error", f"could not start: {exc}"[:300])
            continue
        logger.info("Live session %s started for %s", row["id"], row["url"])
        running[row["id"]] = Running(row, session, events)


async def main() -> None:
    from ..observability import configure_logging

    configure_logging()
    repo = LiveRepository()
    worker_id = f"{socket.gethostname()}:{os.getpid()}"
    running: Dict[str, Running] = {}
    logger.info("Live worker %s up (LIVE_CLIPPING %s)", worker_id, "on" if live_clipping_enabled() else "off")
    loop = asyncio.get_running_loop()
    while True:
        try:
            await tick(repo, worker_id, running, loop.time())
        except Exception:
            # Tables missing until the backend ran its migrations, DB restart...
            logger.exception("Live worker tick failed")
        await asyncio.sleep(POLL_SECONDS)


if __name__ == "__main__":
    asyncio.run(main())
