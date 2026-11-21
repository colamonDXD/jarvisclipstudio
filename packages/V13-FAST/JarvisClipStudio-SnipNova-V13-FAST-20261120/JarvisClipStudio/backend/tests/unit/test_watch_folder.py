"""Watch-folder autopilot: scanner stability, dedupe, bounded retry,
the settings routes and the upload placement helper.

The repository is an in-memory fake mirroring
``WatchFolderRepository`` (the test_publishing_routes.py pattern) and
the task creator is a recording stub, so no Postgres, Redis or worker
is needed. Production wiring is covered by construction: the service
only ever calls the injected creator, and the production creator is
the one that wraps TaskService + JobQueue.
"""

from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from src.api.routes import watch_folder as routes
from src.services import watch_folder_service as service_module
from src.services.watch_folder_service import (
    MAX_ATTEMPTS,
    WatchFolderService,
    fingerprint_for,
)


class FakeRepository:
    def __init__(self):
        self.settings = {}
        self.files = {}  # (user_id, fingerprint) -> row dict
        self.uploads = []

    async def get_settings(self, db, user_id):
        row = self.settings.get(user_id)
        return dict(row) if row else None

    async def upsert_settings(self, db, user_id, fields):
        row = {"user_id": user_id, **fields}
        self.settings[user_id] = row
        return dict(row)

    async def list_enabled_settings(self, db):
        return [dict(s) for s in self.settings.values() if s.get("enabled")]

    async def get_file_by_fingerprint(self, db, user_id, fingerprint):
        row = self.files.get((user_id, fingerprint))
        return dict(row) if row else None

    async def claim_file(self, db, record):
        key = (record["user_id"], record["fingerprint"])
        if key in self.files:
            return None
        row = {
            "id": f"file-{len(self.files) + 1}",
            "state": "processing",
            "task_id": None,
            "upload_path": None,
            "attempts": 1,
            "last_error": None,
            "created_at": datetime.now(timezone.utc),
            "updated_at": datetime.now(timezone.utc),
            **record,
        }
        self.files[key] = row
        return dict(row)

    async def reclaim_failed_file(self, db, file_id, *, max_attempts, backoff_seconds):
        for row in self.files.values():
            if row["id"] != file_id or row["state"] != "failed":
                continue
            if row["attempts"] >= max_attempts:
                return None
            age = (datetime.now(timezone.utc) - row["updated_at"]).total_seconds()
            if age < backoff_seconds:
                return None
            row["state"] = "processing"
            row["attempts"] += 1
            row["updated_at"] = datetime.now(timezone.utc)
            return dict(row)
        return None

    async def reclaim_stale_processing_file(self, db, file_id, *, stale_seconds):
        for row in self.files.values():
            if row["id"] != file_id or row["state"] != "processing":
                continue
            if row.get("task_id") is not None:
                return None
            age = (datetime.now(timezone.utc) - row["updated_at"]).total_seconds()
            if age < stale_seconds:
                return None
            row["attempts"] += 1
            row["updated_at"] = datetime.now(timezone.utc)
            return dict(row)
        return None

    async def list_stale_processing_files(self, db, user_id, *, stale_seconds, limit=100):
        from datetime import datetime as _dt, timezone as _tz

        now = _dt.now(_tz.utc)
        rows = [
            r for r in self.files.values()
            if r["user_id"] == user_id
            and r["state"] == "processing"
            and r.get("task_id") is None
            and (now - r["updated_at"]).total_seconds() >= stale_seconds
        ]
        return [dict(r) for r in rows[:limit]]

    async def update_file(self, db, file_id, fields):
        for row in self.files.values():
            if row["id"] == file_id:
                row.update(fields)
                row["updated_at"] = datetime.now(timezone.utc)

    async def list_files(self, db, user_id, limit=50):
        rows = [r for r in self.files.values() if r["user_id"] == user_id]
        return [dict(r) for r in rows[:limit]]

    async def insert_completed_upload_session(self, db, *, user_id, video_path, size):
        self.uploads.append(
            {"user_id": user_id, "video_path": video_path, "size": size}
        )
        return "session-1"


class RecordingCreator:
    def __init__(self, fail_with: Exception | None = None):
        self.calls = []
        self.fail_with = fail_with

    async def __call__(self, db, settings, source_path, size_bytes):
        self.calls.append((settings["user_id"], Path(source_path), size_bytes))
        if self.fail_with is not None:
            raise self.fail_with
        return f"task-{len(self.calls)}"


def _config(tmp_path: Path):
    return SimpleNamespace(
        watch_folder_path=str(tmp_path / "autopilot"),
        watch_folder_poll_seconds=15,
        default_processing_mode="balanced",
    )


def _settings(folder: Path, **overrides):
    base = {
        "user_id": "user-1",
        "enabled": True,
        "folder_path": str(folder),
        "niche_override": None,
        "music_override": None,
        "target_clip_count": None,
        "processing_mode": None,
    }
    base.update(overrides)
    return base


def _make_service(tmp_path, creator=None, repo=None):
    repo = repo or FakeRepository()
    creator = creator or RecordingCreator()
    service = WatchFolderService(repo, creator, config=_config(tmp_path))
    return service, repo, creator


async def test_stable_file_is_queued_exactly_once(tmp_path):
    folder = tmp_path / "inbox"
    folder.mkdir()
    video = folder / "stream.mp4"
    video.write_bytes(b"x" * 2048)
    service, repo, creator = _make_service(tmp_path)
    settings = _settings(folder)

    first = await service.scan_once(None, settings)
    assert first["queued"] == 0 and first["skipped_unstable"] == 1

    second = await service.scan_once(None, settings)
    assert second["queued"] == 1
    assert len(creator.calls) == 1

    third = await service.scan_once(None, settings)
    assert third["queued"] == 0 and third["skipped_known"] == 1
    assert len(creator.calls) == 1

    rows = list(repo.files.values())
    assert len(rows) == 1
    assert rows[0]["state"] == "queued" and rows[0]["task_id"] == "task-1"


async def test_growing_file_waits_until_stable(tmp_path):
    folder = tmp_path / "inbox"
    folder.mkdir()
    video = folder / "download.mkv"
    video.write_bytes(b"a" * 100)
    service, _repo, creator = _make_service(tmp_path)
    settings = _settings(folder)

    await service.scan_once(None, settings)
    with open(video, "ab") as handle:
        handle.write(b"b" * 100)  # still arriving
    summary = await service.scan_once(None, settings)
    assert summary["queued"] == 0 and creator.calls == []

    summary = await service.scan_once(None, settings)
    assert summary["queued"] == 1 and len(creator.calls) == 1


async def test_stale_processing_orphan_is_recovered_on_scan(tmp_path):
    """F-D1: a claim left 'processing' with no task (watcher died between
    the claim insert and task creation) must be re-driven once it is
    stale — never duplicated, never stuck forever."""
    folder = tmp_path / "inbox"
    folder.mkdir()
    video = folder / "orphan.mp4"
    video.write_bytes(b"o" * 1024)
    service, repo, creator = _make_service(tmp_path)
    settings = _settings(folder)

    await service.scan_once(None, settings)  # stability sighting only

    # Seed the orphan exactly as the dead watcher left it.
    stat = video.stat()
    fingerprint = fingerprint_for(str(video), stat.st_size, stat.st_mtime_ns)
    orphan = await repo.claim_file(None, {
        "user_id": "user-1",
        "file_path": str(video),
        "file_name": video.name,
        "fingerprint": fingerprint,
        "size_bytes": stat.st_size,
        "mtime_ns": stat.st_mtime_ns,
    })
    assert orphan["state"] == "processing" and orphan["task_id"] is None
    # Age the claim well past the stale threshold.
    row = repo.files[("user-1", fingerprint)]
    row["updated_at"] = datetime.now(timezone.utc) - timedelta(hours=2)

    summary = await service.scan_once(None, settings)
    assert summary["queued"] == 1 and len(creator.calls) == 1
    assert row["state"] == "queued" and row["task_id"] == "task-1"
    assert row["attempts"] == 2

    # Idempotent: further scans create nothing extra.
    again = await service.scan_once(None, settings)
    assert again["queued"] == 0 and len(creator.calls) == 1


async def test_fresh_processing_claim_is_not_recovered_early(tmp_path):
    """A claim still inside the claim->task window belongs to a live
    watcher (or a slow creator) — the sweep must not touch it."""
    folder = tmp_path / "inbox"
    folder.mkdir()
    video = folder / "fresh.mp4"
    video.write_bytes(b"f" * 1024)
    service, repo, creator = _make_service(tmp_path)
    settings = _settings(folder)

    await service.scan_once(None, settings)  # stability sighting only
    stat = video.stat()
    fingerprint = fingerprint_for(str(video), stat.st_size, stat.st_mtime_ns)
    await repo.claim_file(None, {
        "user_id": "user-1",
        "file_path": str(video),
        "file_name": video.name,
        "fingerprint": fingerprint,
        "size_bytes": stat.st_size,
        "mtime_ns": stat.st_mtime_ns,
    })

    summary = await service.scan_once(None, settings)
    assert summary["queued"] == 0 and summary["skipped_known"] == 1
    assert creator.calls == []


async def test_stale_processing_orphan_with_changed_source_is_parked(tmp_path):
    """If the source changed or vanished after the orphaned claim, the
    row is parked with a reason — a changed file re-triggers on its own
    via its new fingerprint."""
    folder = tmp_path / "inbox"
    folder.mkdir()
    video = folder / "changed.mp4"
    video.write_bytes(b"c" * 1024)
    service, repo, creator = _make_service(tmp_path)
    settings = _settings(folder)

    await service.scan_once(None, settings)  # stability sighting only
    stat = video.stat()
    fingerprint = fingerprint_for(str(video), stat.st_size, stat.st_mtime_ns)
    await repo.claim_file(None, {
        "user_id": "user-1",
        "file_path": str(video),
        "file_name": video.name,
        "fingerprint": fingerprint,
        "size_bytes": stat.st_size,
        "mtime_ns": stat.st_mtime_ns,
    })
    row = repo.files[("user-1", fingerprint)]
    row["updated_at"] = datetime.now(timezone.utc) - timedelta(hours=2)

    with open(video, "ab") as handle:  # source changed since the claim
        handle.write(b"more bytes")

    summary = await service.scan_once(None, settings)
    assert summary["queued"] == 0 and creator.calls == []
    assert row["state"] == "parked"
    assert "source_changed_or_missing" in row["last_error"]


async def test_duplicate_not_requeued_after_restart(tmp_path):
    folder = tmp_path / "inbox"
    folder.mkdir()
    (folder / "clip.webm").write_bytes(b"v" * 500)
    repo = FakeRepository()
    creator = RecordingCreator()
    settings = _settings(folder)

    first_service = WatchFolderService(repo, creator, config=_config(tmp_path))
    await first_service.scan_once(None, settings)
    await first_service.scan_once(None, settings)
    assert len(creator.calls) == 1

    # A restarted watcher has an empty stability memory but the same DB.
    restarted = WatchFolderService(repo, creator, config=_config(tmp_path))
    await restarted.scan_once(None, settings)
    summary = await restarted.scan_once(None, settings)
    assert summary["queued"] == 0 and summary["skipped_known"] == 1
    assert len(creator.calls) == 1


async def test_failed_file_retries_with_backoff_then_parks(tmp_path):
    folder = tmp_path / "inbox"
    folder.mkdir()
    (folder / "broken.mp4").write_bytes(b"v" * 500)
    repo = FakeRepository()
    creator = RecordingCreator(fail_with=RuntimeError("probe exploded"))
    service = WatchFolderService(repo, creator, config=_config(tmp_path))
    settings = _settings(folder)

    await service.scan_once(None, settings)
    summary = await service.scan_once(None, settings)
    assert summary["failed"] == 1 and len(creator.calls) == 1
    row = next(iter(repo.files.values()))
    assert row["state"] == "failed" and row["attempts"] == 1
    assert "probe exploded" in row["last_error"]

    # Inside the backoff window: no retry.
    summary = await service.scan_once(None, settings)
    assert summary["skipped_known"] == 1 and len(creator.calls) == 1

    for expected_attempts in (2, MAX_ATTEMPTS):
        row["updated_at"] = datetime.now(timezone.utc) - timedelta(
            seconds=service_module.RETRY_BACKOFF_SECONDS + 1
        )
        summary = await service.scan_once(None, settings)
        assert summary["failed"] == 1
        assert row["attempts"] == expected_attempts
        assert len(creator.calls) == expected_attempts

    assert row["state"] == "parked"
    row["updated_at"] = datetime.now(timezone.utc) - timedelta(days=1)
    summary = await service.scan_once(None, settings)
    assert summary["skipped_known"] == 1
    assert len(creator.calls) == MAX_ATTEMPTS  # never attempted again


async def test_disabled_toggle_queues_nothing(tmp_path):
    folder = tmp_path / "inbox"
    folder.mkdir()
    (folder / "clip.mp4").write_bytes(b"v" * 500)
    service, _repo, creator = _make_service(tmp_path)
    settings = _settings(folder, enabled=False)

    for _ in range(2):
        summary = await service.scan_once(None, settings)
        assert summary["queued"] == 0 and summary["seen"] == 0
    assert creator.calls == []


async def test_non_video_and_hidden_files_are_ignored(tmp_path):
    folder = tmp_path / "inbox"
    folder.mkdir()
    (folder / "notes.txt").write_bytes(b"hello")
    (folder / ".partial.mp4").write_bytes(b"v" * 100)
    subdir = folder / "nested"
    subdir.mkdir()
    (subdir / "inner.mp4").write_bytes(b"v" * 100)
    service, _repo, creator = _make_service(tmp_path)
    settings = _settings(folder)

    await service.scan_once(None, settings)
    summary = await service.scan_once(None, settings)
    assert summary["seen"] == 0 and summary["queued"] == 0
    assert creator.calls == []


async def test_missing_folder_is_reported_not_fatal(tmp_path):
    service, _repo, creator = _make_service(tmp_path)
    summary = await service.scan_once(None, _settings(tmp_path / "nope"))
    assert summary["folder_missing"] is True and summary["queued"] == 0
    assert creator.calls == []


def test_fingerprint_changes_with_content():
    base = fingerprint_for("/inbox/a.mp4", 100, 5)
    assert base != fingerprint_for("/inbox/a.mp4", 101, 5)
    assert base != fingerprint_for("/inbox/a.mp4", 100, 6)
    assert base == fingerprint_for("/inbox/a.mp4", 100, 5)


def test_place_upload_links_or_copies_and_keeps_source(tmp_path):
    source = tmp_path / "watch" / "movie.mp4"
    source.parent.mkdir()
    source.write_bytes(b"video-bytes")
    dest = tmp_path / "uploads" / "autopilot-abc.mp4"
    service_module._place_upload(source, dest)
    assert dest.read_bytes() == b"video-bytes"
    assert source.read_bytes() == b"video-bytes"  # original untouched


def test_place_upload_falls_back_to_copy(tmp_path, monkeypatch):
    source = tmp_path / "watch" / "movie.mp4"
    source.parent.mkdir()
    source.write_bytes(b"video-bytes")
    dest = tmp_path / "uploads" / "autopilot-abc.mp4"

    def no_link(src, dst):
        raise OSError("cross-device link")

    monkeypatch.setattr(os, "link", no_link)
    service_module._place_upload(source, dest)
    assert dest.read_bytes() == b"video-bytes"
    assert not list(dest.parent.glob(".partial-*"))


class _Request:
    def __init__(self, payload=None):
        self._payload = payload

    async def json(self):
        if isinstance(self._payload, Exception):
            raise self._payload
        return self._payload


@pytest.fixture
def route_env(tmp_path, monkeypatch):
    repo = FakeRepository()
    monkeypatch.setattr(routes, "repository", repo)
    monkeypatch.setattr(routes, "get_config", lambda: _config(tmp_path))

    async def fake_user_id(request, db, config):
        return "user-1"

    monkeypatch.setattr(routes, "resolve_authenticated_user_id", fake_user_id)
    return repo


async def test_settings_route_defaults_then_roundtrip(route_env, tmp_path):
    result = await routes.get_watch_folder(_Request(), None)
    assert result["settings"]["enabled"] is False
    assert result["settings"]["effective_folder"] == str(tmp_path / "autopilot")
    assert result["files"] == []

    saved = await routes.update_watch_folder_settings(
        _Request({
            "enabled": True,
            "folder_path": "  /data/inbox  ",
            "niche_override": None,
            "music_override": None,
            "target_clip_count": 5,
            "processing_mode": "quality",
        }),
        None,
    )
    assert saved["settings"]["enabled"] is True
    assert saved["settings"]["folder_path"] == "/data/inbox"
    assert saved["settings"]["target_clip_count"] == 5
    assert route_env.settings["user-1"]["processing_mode"] == "quality"

    result = await routes.get_watch_folder(_Request(), None)
    assert result["settings"]["effective_folder"] == "/data/inbox"


async def test_settings_route_rejects_bad_values(route_env):
    with pytest.raises(HTTPException) as excinfo:
        await routes.update_watch_folder_settings(
            _Request({"enabled": True, "niche_override": "not-a-niche"}), None
        )
    assert excinfo.value.status_code == 422

    with pytest.raises(HTTPException) as excinfo:
        await routes.update_watch_folder_settings(
            _Request({"enabled": "yes"}), None
        )
    assert excinfo.value.status_code == 422

    with pytest.raises(HTTPException) as excinfo:
        await routes.update_watch_folder_settings(
            _Request({"enabled": True, "processing_mode": "ludicrous"}), None
        )
    assert excinfo.value.status_code == 422
    assert route_env.settings == {}  # nothing was persisted
