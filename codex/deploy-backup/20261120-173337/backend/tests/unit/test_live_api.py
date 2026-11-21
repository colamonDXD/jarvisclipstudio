"""Live clipping API and repository (LIVE_CLIPPING gate, validation, worker claims)."""
from __future__ import annotations

import time
from pathlib import Path

import pytest
from sqlalchemy import text

from src.api.routes import live as routes
from src.database import AsyncSessionLocal
from src.repositories.live_repository import LiveRepository

pytestmark = pytest.mark.asyncio


@pytest.fixture()
async def clean_live():
    async def wipe():
        async with AsyncSessionLocal() as db:
            await db.execute(text("DELETE FROM live_sessions WHERE user_id LIKE 'user-%' OR user_id LIKE 'live-test-%'"))
            await db.commit()

    await wipe()
    yield
    await wipe()


@pytest.fixture()
def live_on(monkeypatch, tmp_path):
    monkeypatch.setenv("LIVE_CLIPPING", "on")
    monkeypatch.setenv("TEMP_DIR", str(tmp_path))
    monkeypatch.setattr(routes, "probe_live_status", lambda url: "is_live")


async def test_every_route_is_closed_while_the_switch_is_off(client, auth_headers, monkeypatch):
    monkeypatch.delenv("LIVE_CLIPPING", raising=False)
    assert (await client.get("/live/status")).json() == {"enabled": False}
    for method, path in (("post", "/live/sessions"), ("get", "/live/sessions"), ("get", "/live/sessions/x"),
                         ("post", "/live/sessions/x/stop"), ("delete", "/live/sessions/x"),
                         ("get", "/live/clips/x/file")):
        response = await getattr(client, method)(path, headers=auth_headers)
        assert response.status_code == 403, path


async def test_start_list_get_stop(client, auth_headers, live_on, clean_live):
    response = await client.post("/live/sessions", headers=auth_headers, json={
        "url": "https://www.youtube.com/watch?v=abc", "credit": "@Someone", "hook_title": "GTA 6 LIVE"})
    assert response.status_code == 201, response.text
    session = response.json()["session"]
    assert session["status"] == "requested"
    assert session["settings"]["layout_template"] == "facecam_red"
    assert session["settings"]["credit"] == "@Someone"

    again = await client.post("/live/sessions", headers=auth_headers, json={"url": "https://www.twitch.tv/x"})
    assert again.status_code == 409  # one active session per user

    listed = (await client.get("/live/sessions", headers=auth_headers)).json()["sessions"]
    assert [s["id"] for s in listed] == [session["id"]]
    one = (await client.get(f"/live/sessions/{session['id']}", headers=auth_headers)).json()
    assert one["clips"] == []

    stopped = await client.post(f"/live/sessions/{session['id']}/stop", headers=auth_headers)
    assert stopped.status_code == 200
    assert stopped.json()["session"]["status"] == "stopped"  # never claimed: stops at once
    assert (await client.post(f"/live/sessions/{session['id']}/stop", headers=auth_headers)).status_code == 409
    assert (await client.delete(f"/live/sessions/{session['id']}", headers=auth_headers)).json() == {"deleted": True}
    assert (await client.get(f"/live/sessions/{session['id']}", headers=auth_headers)).status_code == 404


@pytest.mark.parametrize("body,fragment", [
    ({"url": "https://example.com/stream"}, "YouTube or Twitch"),
    ({"url": "https://www.twitch.tv/x", "layout_template": "neon"}, "layout_template"),
    ({"url": "https://www.twitch.tv/x", "max_clips": 0}, "max_clips"),
    ({"url": "https://www.twitch.tv/x", "streamer_words_only": "yes"}, "streamer_words_only"),
    ({"url": "https://www.twitch.tv/x", "credit": "x" * 41}, "credit"),
])
async def test_bad_bodies_are_422(client, auth_headers, live_on, clean_live, body, fragment):
    response = await client.post("/live/sessions", headers=auth_headers, json=body)
    assert response.status_code == 422
    assert fragment in response.text


async def test_a_finished_stream_starts_the_fast_vod_path(client, auth_headers, live_on, clean_live, monkeypatch):
    # Owner change 2026-11-19: finished streams (10-20 h VODs) are clipped by
    # the fast path (live/vod.py) instead of being refused.
    monkeypatch.setattr(routes, "probe_live_status", lambda url: "was_live")
    response = await client.post("/live/sessions", headers=auth_headers, json={"url": "https://www.youtube.com/watch?v=old"})
    assert response.status_code == 201, response.text
    session = response.json()["session"]
    assert session["mode"] == "vod" and session["settings"]["max_clips"] == 10


async def test_an_upcoming_stream_is_refused_with_a_reason(client, auth_headers, live_on, clean_live, monkeypatch):
    monkeypatch.setattr(routes, "probe_live_status", lambda url: "is_upcoming")
    response = await client.post("/live/sessions", headers=auth_headers, json={"url": "https://www.youtube.com/watch?v=soon"})
    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "LIVE_IS_UPCOMING"


async def test_layout_null_is_allowed_for_streams_without_a_facecam(client, auth_headers, live_on, clean_live):
    response = await client.post("/live/sessions", headers=auth_headers,
                                 json={"url": "https://www.twitch.tv/irl", "layout_template": None, "niche": "podcast_news"})
    assert response.status_code == 201, response.text
    assert response.json()["session"]["settings"]["layout_template"] is None


async def test_worker_claim_events_and_files(client, auth_headers, live_on, clean_live, tmp_path):
    repo = LiveRepository()
    async with AsyncSessionLocal() as db:
        created = await repo.create_session(db, "user-1", "https://www.twitch.tv/x", {"layout_template": None})
        claimed = await repo.claim_requested(db, "test-worker")
        assert claimed["id"] == created["id"] and claimed["status"] == "starting"
        assert await repo.claim_requested(db, "other-worker") is None  # only one worker gets it
        stop = await repo.heartbeat(db, created["id"], {"recording": True, "buffered_seconds": 64.0})
        assert stop is False
        now = time.time()
        await repo.record_moment(db, created["id"], {"status": "rejected", "onset_wall": now,
                                                     "reason": "streamer barely talking", "talk_share": 0.1})
        live_dir = tmp_path / "live" / created["id"]
        live_dir.mkdir(parents=True)
        preview = live_dir / "preview_001.mp4"
        preview.write_bytes(b"\x00" * 32)
        await repo.record_moment(db, created["id"], {"n": 1, "status": "queued", "onset_wall": now,
                                                     "start_wall": now - 4, "end_wall": now + 20,
                                                     "signals": ["f0", "laugh"], "strength": 5.2,
                                                     "talk_share": 0.8, "preview_path": str(preview)})
        await repo.update_clip(db, created["id"], {"n": 1, "status": "ready", "clip_path": str(preview),
                                                   "duration": 24.0, "render_s": 61.0})
    body = (await client.get(f"/live/sessions/{created['id']}", headers=auth_headers)).json()
    assert body["session"]["status"] == "recording" and body["session"]["buffered_seconds"] == 64.0
    assert [c["status"] for c in body["clips"]] == ["rejected", "ready"]
    assert body["clips"][0]["reason"] == "streamer barely talking"
    ready = body["clips"][1]
    assert ready["has_preview"] and ready["has_clip"] and ready["duration"] == 24.0
    assert "preview_path" not in ready  # server paths never reach the client
    file = await client.get(f"/live/clips/{ready['id']}/file?kind=preview", headers=auth_headers)
    assert file.status_code == 200 and file.headers["content-type"] == "video/mp4"
    assert (await client.delete(f"/live/sessions/{created['id']}", headers=auth_headers)).status_code == 409

    async with AsyncSessionLocal() as db:
        assert await repo.request_stop(db, created["id"], "user-1")
        assert await repo.heartbeat(db, created["id"], {"recording": True}) is True  # worker sees the stop
        await repo.finish_session(db, created["id"], "stopped")
    assert (await client.delete(f"/live/sessions/{created['id']}", headers=auth_headers)).status_code == 200
    assert not Path(tmp_path / "live" / created["id"]).exists()


async def test_files_outside_the_live_folder_are_never_served(client, auth_headers, live_on, clean_live, tmp_path):
    repo = LiveRepository()
    outside = tmp_path / "secret.mp4"
    outside.write_bytes(b"x")
    async with AsyncSessionLocal() as db:
        created = await repo.create_session(db, "user-1", "https://www.twitch.tv/x", {})
        await repo.record_moment(db, created["id"], {"n": 1, "status": "queued", "preview_path": str(outside)})
    clip_id = (await client.get(f"/live/sessions/{created['id']}", headers=auth_headers)).json()["clips"][0]["id"]
    assert (await client.get(f"/live/clips/{clip_id}/file?kind=preview", headers=auth_headers)).status_code == 404


async def test_another_users_session_is_invisible(client, auth_headers, live_on, clean_live):
    repo = LiveRepository()
    async with AsyncSessionLocal() as db:
        other = await repo.create_session(db, "live-test-other", "https://www.twitch.tv/x", {})
    assert (await client.get(f"/live/sessions/{other['id']}", headers=auth_headers)).status_code == 404
    assert (await client.post(f"/live/sessions/{other['id']}/stop", headers=auth_headers)).status_code == 404


async def test_stale_sessions_are_failed(live_on, clean_live):
    repo = LiveRepository()
    async with AsyncSessionLocal() as db:
        created = await repo.create_session(db, "live-test-stale", "https://www.twitch.tv/x", {})
        await repo.claim_requested(db, "dead-worker")
        await db.execute(text("UPDATE live_sessions SET heartbeat_at = CURRENT_TIMESTAMP - INTERVAL '10 minutes' "
                              "WHERE id = :id"), {"id": created["id"]})
        await db.commit()
        assert await repo.fail_stale(db) >= 1
        row = await repo.get_session(db, created["id"])
    assert row["status"] == "error" and "live worker stopped" in row["last_error"]
