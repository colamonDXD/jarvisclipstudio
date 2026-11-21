"""Live worker loop: claim, events into the database, stop, stream end, switch off."""
from __future__ import annotations

import time

import pytest
from sqlalchemy import text

from src.database import AsyncSessionLocal
from src.repositories.live_repository import LiveRepository
from src.workers import live_worker as W

pytestmark = pytest.mark.asyncio


class FakeRecorder:
    def __init__(self):
        self.running = True
        self.last_error = None


class FakeSession:
    instances: list = []

    def __init__(self, settings, session_dir, on_event):
        self.settings, self.on_event = settings, on_event
        self.recorder = FakeRecorder()
        self.stopped = False
        self.ended = False
        FakeSession.instances.append(self)

    def start(self):
        now = time.time()
        self.on_event({"type": "status", "recording": True, "buffered_seconds": 40.0, "restarts": 0})
        self.on_event({"type": "moment", "n": 1, "status": "queued", "onset_wall": now, "start_wall": now - 3,
                       "end_wall": now + 18, "signals": ["laugh"], "strength": 6.0, "talk_share": 0.9})
        self.on_event({"type": "clip", "n": 1, "status": "ready", "clip_path": "/nowhere/clip_001.mp4",
                       "duration": 21.0, "render_s": 50.0})
        return self

    def stop_recording(self):
        # The fake has no renders in flight: it ends as soon as recording stops.
        self.recording_stopped = True
        self.ended = True

    def stop(self):
        self.stopped = True


@pytest.fixture()
async def worker_env(monkeypatch, tmp_path, initialized_database):
    monkeypatch.setenv("LIVE_CLIPPING", "on")
    monkeypatch.setenv("TEMP_DIR", str(tmp_path))
    monkeypatch.setattr(W, "LiveSession", FakeSession)
    FakeSession.instances = []

    async def wipe():
        async with AsyncSessionLocal() as db:
            await db.execute(text("DELETE FROM live_sessions WHERE user_id LIKE 'live-worker-test%'"))
            await db.execute(text("UPDATE live_sessions SET status = 'stopped' WHERE status = 'requested'"))
            await db.commit()

    await wipe()
    yield
    await wipe()


async def _session(user="live-worker-test"):
    async with AsyncSessionLocal() as db:
        return await LiveRepository().create_session(db, user, "https://www.twitch.tv/x",
                                                     {"layout_template": "facecam_gold", "credit": "@x"})


async def _get(sid):
    async with AsyncSessionLocal() as db:
        repo = LiveRepository()
        return await repo.get_session(db, sid), await repo.list_clips(db, sid)


async def test_claim_record_and_stop(worker_env):
    created = await _session()
    running: dict = {}
    await W.tick(LiveRepository(), "w1", running, 100.0)  # claims and starts
    assert list(running) == [created["id"]]
    assert FakeSession.instances[0].settings.layout_template == "facecam_gold"
    await W.tick(LiveRepository(), "w1", running, 101.0)  # drains events + heartbeat
    session, clips = await _get(created["id"])
    assert session["status"] == "recording" and session["buffered_seconds"] == 40.0
    assert [(c["n"], c["status"], c["duration"]) for c in clips] == [(1, "ready", 21.0)]

    async with AsyncSessionLocal() as db:
        assert await LiveRepository().request_stop(db, created["id"], "live-worker-test")
    await W.tick(LiveRepository(), "w1", running, 200.0)
    assert running == {} and FakeSession.instances[0].recording_stopped and FakeSession.instances[0].stopped
    session, _ = await _get(created["id"])
    assert session["status"] == "stopped" and session["stopped_at"]


async def test_a_finished_stream_ends_the_session(worker_env):
    created = await _session()
    running: dict = {}
    await W.tick(LiveRepository(), "w1", running, 100.0)
    fake = FakeSession.instances[0]
    fake.ended = True
    fake.recorder.last_error = "stream offline"
    for i in range(3):
        await W.tick(LiveRepository(), "w1", running, 101.0 + i)
    assert running == {}
    session, _ = await _get(created["id"])
    assert session["status"] == "ended" and session["last_error"] == "stream offline"


async def test_only_max_sessions_run_at_once(worker_env, monkeypatch):
    monkeypatch.setenv("LIVE_MAX_SESSIONS", "1")
    await _session("live-worker-test-a")
    second = await _session("live-worker-test-b")
    running: dict = {}
    await W.tick(LiveRepository(), "w1", running, 100.0)
    assert len(running) == 1
    session, _ = await _get(second["id"])
    assert session["status"] == "requested"  # waits its turn


async def test_switch_off_claims_nothing(worker_env, monkeypatch):
    monkeypatch.setenv("LIVE_CLIPPING", "off")
    created = await _session()
    running: dict = {}
    await W.tick(LiveRepository(), "w1", running, 100.0)
    assert running == {}
    session, _ = await _get(created["id"])
    assert session["status"] == "requested"
