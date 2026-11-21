"""YouTube channel monitoring: baseline-on-add, claim-first dedupe,
defaults stamping, daily cap, skips, bounded retry, orphan recovery,
feed-outage health, and the settings routes.

The repository is an in-memory fake mirroring
``ChannelMonitorRepository`` (the test_watch_folder.py pattern), the
detector returns fixture feed entries, and the task creator is a
recording stub — no Postgres, Redis, worker or live YouTube call is
needed. The production creator's own decisions (duration preflight,
skip reasons, defaults on the created task) are pinned separately
with its collaborators stubbed at their module seams.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from src.api.routes import channel_monitors as routes
from src.services import channel_monitor_service as service_module
from src.services.channel_monitor_service import (
    MAX_ATTEMPTS,
    ChannelMonitorService,
    SkipVideo,
    create_task_from_channel_video,
)
from src.youtube_feed import ChannelFeedError, FeedEntry

CHANNEL_ID = "UC1234567890abcdefghijkl"
NOW = datetime(2026, 11, 11, 12, 0, tzinfo=timezone.utc)


def entry(video_id: str, *, short: bool = False, title: str | None = None) -> FeedEntry:
    return FeedEntry(
        video_id=video_id,
        title=title or f"Video {video_id}",
        url=f"https://www.youtube.com/watch?v={video_id}",
        published_at=NOW,
        is_short_link=short,
    )


class FakeRepository:
    def __init__(self):
        self.settings = {}
        self.monitors = {}  # id -> row
        self.videos = {}  # (user_id, video_id) -> row
        self._seq = 0

    def _next_id(self, prefix):
        self._seq += 1
        return f"{prefix}-{self._seq}"

    # settings ------------------------------------------------------
    async def get_settings(self, db, user_id):
        row = self.settings.get(user_id)
        return dict(row) if row else None

    async def upsert_settings(self, db, user_id, fields):
        row = {"user_id": user_id, "enabled": bool(fields.get("enabled", False))}
        self.settings[user_id] = row
        return dict(row)

    async def list_enabled_settings(self, db):
        return [dict(s) for s in self.settings.values() if s.get("enabled")]

    # monitors --------------------------------------------------------
    async def create_monitor(self, db, record):
        for row in self.monitors.values():
            if row["user_id"] == record["user_id"] and row["channel_id"] == record["channel_id"]:
                return None
        row = {
            "id": self._next_id("mon"),
            "enabled": True,
            "niche_override": None, "music_mode": None, "music_override": None,
            "template_mode": "plain", "creation_mode": "full_auto",
            "target_clip_count": None, "processing_mode": None,
            "include_shorts": False, "max_tasks_per_day": 3,
            "last_polled_at": None, "last_error": None, "consecutive_failures": 0,
            "created_at": NOW, "updated_at": NOW,
            **record,
        }
        self.monitors[row["id"]] = row
        return dict(row)

    async def get_monitor(self, db, user_id, monitor_id):
        row = self.monitors.get(monitor_id)
        return dict(row) if row and row["user_id"] == user_id else None

    async def get_monitor_by_channel(self, db, user_id, channel_id):
        for row in self.monitors.values():
            if row["user_id"] == user_id and row["channel_id"] == channel_id:
                return dict(row)
        return None

    async def list_monitors(self, db, user_id):
        return [dict(r) for r in self.monitors.values() if r["user_id"] == user_id]

    async def list_enabled_monitors(self, db, user_id):
        return [
            dict(r) for r in self.monitors.values()
            if r["user_id"] == user_id and r.get("enabled")
        ]

    async def update_monitor(self, db, monitor_id, fields):
        row = self.monitors.get(monitor_id)
        if row is not None:
            row.update(fields)
            row["updated_at"] = datetime.now(timezone.utc)

    async def delete_monitor(self, db, user_id, monitor_id):
        row = self.monitors.get(monitor_id)
        if row is None or row["user_id"] != user_id:
            return False
        del self.monitors[monitor_id]
        return True

    # videos ----------------------------------------------------------
    async def get_video(self, db, user_id, video_id):
        row = self.videos.get((user_id, video_id))
        return dict(row) if row else None

    def _video_row(self, record, *, state, attempts, skip_reason=None):
        return {
            "id": self._next_id("vid"),
            "state": state, "skip_reason": skip_reason,
            "task_id": None, "attempts": attempts, "last_error": None,
            "created_at": datetime.now(timezone.utc),
            "updated_at": datetime.now(timezone.utc),
            **record,
        }

    async def claim_video(self, db, record):
        key = (record["user_id"], record["video_id"])
        if key in self.videos:
            return None
        row = self._video_row(record, state="processing", attempts=1)
        self.videos[key] = row
        return dict(row)

    async def insert_terminal_video(self, db, record, *, state, skip_reason):
        key = (record["user_id"], record["video_id"])
        if key in self.videos:
            return None
        row = self._video_row(record, state=state, attempts=0, skip_reason=skip_reason)
        self.videos[key] = row
        return dict(row)

    def _find_video(self, video_row_id):
        for row in self.videos.values():
            if row["id"] == video_row_id:
                return row
        return None

    @staticmethod
    def _age_seconds(row):
        return (datetime.now(timezone.utc) - row["updated_at"]).total_seconds()

    async def reclaim_failed_video(self, db, video_row_id, *, max_attempts, backoff_seconds):
        row = self._find_video(video_row_id)
        if row is None or row["state"] != "failed":
            return None
        if row["attempts"] >= max_attempts or self._age_seconds(row) < backoff_seconds:
            return None
        row["state"] = "processing"
        row["attempts"] += 1
        row["updated_at"] = datetime.now(timezone.utc)
        return dict(row)

    async def reclaim_stale_processing_video(self, db, video_row_id, *, stale_seconds):
        row = self._find_video(video_row_id)
        if row is None or row["state"] != "processing" or row.get("task_id") is not None:
            return None
        if self._age_seconds(row) < stale_seconds:
            return None
        row["attempts"] += 1
        row["updated_at"] = datetime.now(timezone.utc)
        return dict(row)

    async def list_retryable_failed_videos(self, db, monitor_id, *, max_attempts, backoff_seconds, limit=15):
        return [
            dict(r) for r in self.videos.values()
            if r["monitor_id"] == monitor_id and r["state"] == "failed"
            and r["attempts"] < max_attempts and self._age_seconds(r) >= backoff_seconds
        ][:limit]

    async def list_stale_processing_videos(self, db, monitor_id, *, stale_seconds, limit=100):
        return [
            dict(r) for r in self.videos.values()
            if r["monitor_id"] == monitor_id and r["state"] == "processing"
            and r.get("task_id") is None and self._age_seconds(r) >= stale_seconds
        ][:limit]

    async def update_video(self, db, video_row_id, fields):
        row = self._find_video(video_row_id)
        if row is not None:
            row.update(fields)
            row["updated_at"] = datetime.now(timezone.utc)

    async def list_videos(self, db, user_id, limit=50):
        rows = [r for r in self.videos.values() if r["user_id"] == user_id]
        rows.sort(key=lambda r: r["created_at"], reverse=True)
        out = []
        for row in rows[:limit]:
            item = dict(row)
            monitor = self.monitors.get(row["monitor_id"])
            item["channel_title"] = monitor["channel_title"] if monitor else None
            item["channel_id"] = monitor["channel_id"] if monitor else None
            out.append(item)
        return out

    async def count_tasks_created_since(self, db, monitor_id, since):
        return sum(
            1 for r in self.videos.values()
            if r["monitor_id"] == monitor_id and r.get("task_id") is not None
            and r["created_at"] >= since
        )

    async def park_videos_for_missing_monitors(self, db, user_id):
        count = 0
        for row in self.videos.values():
            if (
                row["user_id"] == user_id
                and row["state"] in ("processing", "failed")
                and row["monitor_id"] not in self.monitors
            ):
                row["state"] = "parked"
                row["last_error"] = "monitor_removed: the channel is no longer monitored"
                count += 1
        return count


class FakeDetector:
    def __init__(self, feeds=None):
        self.feeds = feeds or {}
        self.error = None

    async def __call__(self, channel_id):
        if self.error is not None:
            raise self.error
        return list(self.feeds.get(channel_id, []))


class RecordingCreator:
    def __init__(self, fail_with: Exception | None = None):
        self.calls = []
        self.fail_with = fail_with

    async def __call__(self, db, monitor, entry):
        self.calls.append((dict(monitor), entry))
        if self.fail_with is not None:
            raise self.fail_with
        return f"task-{len(self.calls)}"


def _config():
    return SimpleNamespace(
        channel_monitor_poll_seconds=900,
        default_processing_mode="balanced",
        youtube_preflight_timeout_seconds=30,
    )


async def _add_monitor(repo: FakeRepository, **overrides) -> dict:
    base = {
        "user_id": "user-1",
        "channel_id": CHANNEL_ID,
        "channel_title": "Speed Clips",
        "source_input": "@speedclips",
        "niche_override": "gaming",
        "music_mode": "off",
        "music_override": None,
        "template_mode": "plain",
        "creation_mode": "full_auto",
        "target_clip_count": 5,
        "processing_mode": None,
        "include_shorts": False,
        "max_tasks_per_day": 3,
    }
    base.update(overrides)
    created = await repo.create_monitor(None, base)
    assert created is not None
    return created


def _service(repo, detector=None, creator=None):
    return ChannelMonitorService(
        repo, detector or FakeDetector(), creator or RecordingCreator(),
        config=_config(),
    )


# -- watcher behaviour -------------------------------------------------


async def test_baseline_add_creates_zero_tasks():
    repo = FakeRepository()
    monitor = await _add_monitor(repo)
    creator = RecordingCreator()
    detector = FakeDetector({CHANNEL_ID: [entry("v3"), entry("v2"), entry("v1")]})
    service = _service(repo, detector, creator)

    chosen = await service.seed_baseline(None, monitor, detector.feeds[CHANNEL_ID])
    assert chosen is None
    assert creator.calls == []
    assert len(repo.videos) == 3
    assert all(r["state"] == "baseline" for r in repo.videos.values())

    # A later poll over the same feed still creates nothing: every
    # entry is already known.
    summary = await service.poll_monitor(None, monitor)
    assert summary["queued"] == 0 and creator.calls == []


async def test_new_video_creates_exactly_one_task_with_defaults():
    repo = FakeRepository()
    monitor = await _add_monitor(repo)
    creator = RecordingCreator()
    detector = FakeDetector({CHANNEL_ID: [entry("newvid00001")]})
    service = _service(repo, detector, creator)

    summary = await service.poll_monitor(None, monitor)
    assert summary["queued"] == 1
    assert len(creator.calls) == 1
    seen_monitor, seen_entry = creator.calls[0]
    assert seen_entry.video_id == "newvid00001"
    assert seen_monitor["template_mode"] == "plain"
    assert seen_monitor["creation_mode"] == "full_auto"
    assert seen_monitor["niche_override"] == "gaming"
    assert seen_monitor["music_mode"] == "off"
    assert seen_monitor["target_clip_count"] == 5

    row = repo.videos[("user-1", "newvid00001")]
    assert row["state"] == "queued" and row["task_id"] == "task-1"

    # Second poll: no duplicate.
    summary = await service.poll_monitor(None, monitor)
    assert summary["queued"] == 0 and len(creator.calls) == 1


async def test_new_videos_are_processed_oldest_first():
    repo = FakeRepository()
    monitor = await _add_monitor(repo)
    creator = RecordingCreator()
    detector = FakeDetector({CHANNEL_ID: [entry("newer"), entry("older")]})
    service = _service(repo, detector, creator)

    await service.poll_monitor(None, monitor)
    assert [call[1].video_id for call in creator.calls] == ["older", "newer"]


async def test_daily_cap_is_enforced_and_visible():
    repo = FakeRepository()
    monitor = await _add_monitor(repo, max_tasks_per_day=1)
    creator = RecordingCreator()
    detector = FakeDetector({CHANNEL_ID: [entry("capb"), entry("capa")]})
    service = _service(repo, detector, creator)

    summary = await service.poll_monitor(None, monitor)
    assert summary["queued"] == 1 and summary["skipped"] == 1
    assert len(creator.calls) == 1
    capped = repo.videos[("user-1", "capb")]
    assert capped["state"] == "skipped" and capped["skip_reason"] == "daily_cap"
    assert capped["task_id"] is None


async def test_daily_cap_null_means_no_cap():
    repo = FakeRepository()
    monitor = await _add_monitor(repo, max_tasks_per_day=None)
    creator = RecordingCreator()
    detector = FakeDetector({CHANNEL_ID: [entry("n3"), entry("n2"), entry("n1")]})
    service = _service(repo, detector, creator)

    summary = await service.poll_monitor(None, monitor)
    assert summary["queued"] == 3 and len(creator.calls) == 3


async def test_over_duration_skip_is_terminal_not_failed():
    repo = FakeRepository()
    monitor = await _add_monitor(repo)
    creator = RecordingCreator(fail_with=SkipVideo("over_duration_cap"))
    detector = FakeDetector({CHANNEL_ID: [entry("longvod0001")]})
    service = _service(repo, detector, creator)

    summary = await service.poll_monitor(None, monitor)
    assert summary["skipped"] == 1 and summary["failed"] == 0
    row = repo.videos[("user-1", "longvod0001")]
    assert row["state"] == "skipped" and row["skip_reason"] == "over_duration_cap"
    assert row["task_id"] is None


async def test_shorts_link_filtered_unless_included():
    repo = FakeRepository()
    monitor = await _add_monitor(repo, include_shorts=False)
    creator = RecordingCreator()
    detector = FakeDetector({CHANNEL_ID: [entry("short000001", short=True)]})
    service = _service(repo, detector, creator)

    summary = await service.poll_monitor(None, monitor)
    assert summary["skipped"] == 1 and creator.calls == []
    row = repo.videos[("user-1", "short000001")]
    assert row["state"] == "skipped" and row["skip_reason"] == "short"

    repo2 = FakeRepository()
    monitor2 = await _add_monitor(repo2, include_shorts=True)
    creator2 = RecordingCreator()
    service2 = _service(repo2, FakeDetector({CHANNEL_ID: [entry("short000001", short=True)]}), creator2)
    summary2 = await service2.poll_monitor(None, monitor2)
    assert summary2["queued"] == 1 and len(creator2.calls) == 1


async def test_failure_retries_with_backoff_then_parks():
    repo = FakeRepository()
    monitor = await _add_monitor(repo)
    creator = RecordingCreator(fail_with=RuntimeError("metadata exploded"))
    detector = FakeDetector({CHANNEL_ID: [entry("failvid0001")]})
    service = _service(repo, detector, creator)

    await service.poll_monitor(None, monitor)
    row = repo.videos[("user-1", "failvid0001")]
    assert row["state"] == "failed" and row["attempts"] == 1

    # Backoff has not elapsed: no retry yet.
    await service.poll_monitor(None, monitor)
    assert len(creator.calls) == 1

    for expected_attempts in (2, 3):
        row["updated_at"] = datetime.now(timezone.utc) - timedelta(hours=1)
        await service.poll_monitor(None, monitor)
        assert row["attempts"] == expected_attempts
    assert row["state"] == "parked"
    assert len(creator.calls) == MAX_ATTEMPTS

    # Parked is terminal: never touched again.
    row["updated_at"] = datetime.now(timezone.utc) - timedelta(hours=1)
    await service.poll_monitor(None, monitor)
    assert len(creator.calls) == MAX_ATTEMPTS


async def test_stale_processing_claim_is_recovered_on_next_sweep():
    repo = FakeRepository()
    monitor = await _add_monitor(repo)
    creator = RecordingCreator()
    service = _service(repo, FakeDetector(), creator)

    # The orphan exactly as a dead watcher left it.
    claim = await repo.claim_video(None, {
        "user_id": "user-1", "monitor_id": monitor["id"],
        "video_id": "orphan00001",
        "video_url": "https://www.youtube.com/watch?v=orphan00001",
        "title": "Orphaned", "published_at": NOW,
    })
    assert claim["state"] == "processing" and claim["task_id"] is None
    row = repo.videos[("user-1", "orphan00001")]
    row["updated_at"] = datetime.now(timezone.utc) - timedelta(hours=2)

    summary = await service.poll_monitor(None, monitor)
    assert summary["queued"] == 1 and len(creator.calls) == 1
    assert row["state"] == "queued" and row["task_id"] == "task-1"


async def test_feed_outage_sets_health_and_writes_no_rows():
    repo = FakeRepository()
    monitor = await _add_monitor(repo)
    creator = RecordingCreator()
    detector = FakeDetector()
    detector.error = ChannelFeedError("YouTube answered the channel feed with HTTP 503.")
    service = _service(repo, detector, creator)

    summary = await service.poll_monitor(None, monitor)
    assert summary["feed_error"] is True
    assert repo.videos == {} and creator.calls == []
    stored = repo.monitors[monitor["id"]]
    assert stored["consecutive_failures"] == 1
    assert "503" in stored["last_error"]

    # The watcher re-reads the row each cycle in production; feed the
    # updated health back in for the second failure.
    summary = await service.poll_monitor(None, stored)
    assert summary["feed_error"] is True
    assert repo.monitors[monitor["id"]]["consecutive_failures"] == 2

    # Recovery resets health.
    detector.error = None
    detector.feeds = {CHANNEL_ID: []}
    summary = await service.poll_monitor(None, repo.monitors[monitor["id"]])
    assert summary["feed_error"] is False
    stored = repo.monitors[monitor["id"]]
    assert stored["consecutive_failures"] == 0 and stored["last_error"] is None


async def test_claims_of_a_removed_monitor_are_parked_by_the_sweep():
    repo = FakeRepository()
    monitor = await _add_monitor(repo)
    service = _service(repo, FakeDetector(), RecordingCreator())
    await repo.upsert_settings(None, "user-1", {"enabled": True})

    claim = await repo.claim_video(None, {
        "user_id": "user-1", "monitor_id": monitor["id"],
        "video_id": "gone0000001",
        "video_url": "https://www.youtube.com/watch?v=gone0000001",
        "title": "Gone", "published_at": NOW,
    })
    await repo.delete_monitor(None, "user-1", monitor["id"])

    summary = await service.poll_user(None, {"user_id": "user-1", "enabled": True})
    assert summary["monitors"] == 0
    row = repo.videos[("user-1", "gone0000001")]
    assert row["state"] == "parked"
    assert "monitor_removed" in row["last_error"]
    assert claim["id"] == row["id"]


async def test_clip_newest_seeding_leaves_exactly_one_entry_unbaselined():
    repo = FakeRepository()
    monitor = await _add_monitor(repo, include_shorts=False)
    service = _service(repo, FakeDetector(), RecordingCreator())
    entries = [entry("newshort001", short=True), entry("newvideo001"), entry("oldvideo001")]

    chosen = await service.seed_baseline(None, monitor, entries, clip_newest=True)
    # The newest entry is a Short and Shorts are off, so the newest
    # *eligible* entry is the regular video.
    assert chosen is not None and chosen.video_id == "newvideo001"
    states = {vid: r["state"] for (_u, vid), r in repo.videos.items()}
    assert states == {"newshort001": "baseline", "oldvideo001": "baseline"}


# -- production creator decisions --------------------------------------


class _CreatorHarness:
    """Stubs the production creator's collaborators at their seams."""

    def __init__(self, monkeypatch, *, duration=3600.0, duration_limit=5400):
        self.created: list[dict] = []
        self.enqueued: list[tuple] = []
        self.saved_metadata: list[dict] = []
        self.billing_calls: list = []
        holder = self

        class FakeBilling:
            def __init__(self, db):
                pass

            async def assert_can_create_task(self, user_id, duration_seconds=None):
                holder.billing_calls.append(duration_seconds)
                return {"max_youtube_duration_seconds": duration_limit}

        class FakeTaskService:
            def __init__(self, db):
                self.task_repo = SimpleNamespace(
                    update_task_status=self._noop_status
                )

            async def _noop_status(self, *args, **kwargs):
                return None

            async def create_task_with_source(self, **kwargs):
                holder.created.append(kwargs)
                return "task-42"

        async def fake_video_info(url, task_id=None, **kwargs):
            return {"duration": duration, "title": "Feed Title"}

        async def fake_save_metadata(db, task_id, payload):
            holder.saved_metadata.append(payload)

        monkeypatch.setattr(
            "src.services.billing_service.BillingService", FakeBilling
        )
        monkeypatch.setattr("src.services.task_service.TaskService", FakeTaskService)
        monkeypatch.setattr(
            "src.youtube_utils.async_get_youtube_video_info", fake_video_info
        )
        monkeypatch.setattr(
            "src.repositories.task_repository.TaskRepository.save_source_metadata",
            fake_save_metadata,
        )
        monkeypatch.setattr(service_module, "get_config", _config)

    async def enqueue_processing_job(self, *args, **kwargs):
        self.enqueued.append((args, kwargs))
        return "job-1"


async def _monitor_dict(**overrides):
    base = {
        "id": "mon-1", "user_id": "user-1", "channel_id": CHANNEL_ID,
        "niche_override": "gaming", "music_mode": "off", "music_override": None,
        "template_mode": "plain", "creation_mode": "full_auto",
        "target_clip_count": 5, "processing_mode": None, "include_shorts": False,
    }
    base.update(overrides)
    return base


async def test_production_creator_stamps_channel_defaults(monkeypatch):
    harness = _CreatorHarness(monkeypatch, duration=3600.0)
    task_id = await create_task_from_channel_video(
        None, await _monitor_dict(), entry("prodvid0001"),
        queue_adapter=harness,
    )
    assert task_id == "task-42"
    assert len(harness.created) == 1
    kwargs = harness.created[0]
    assert kwargs["url"] == "https://www.youtube.com/watch?v=prodvid0001"
    assert kwargs["template_mode"] == "plain"
    assert kwargs["creation_mode"] == "full_auto"
    assert kwargs["niche_override"] == "gaming"
    assert kwargs["music_mode"] == "off"
    assert kwargs["target_clip_count"] == 5
    assert kwargs["source_duration_seconds"] == 3600
    assert harness.saved_metadata[0]["channel_monitor_video"] == "prodvid0001"
    assert harness.saved_metadata[0]["source_type"] == "youtube"
    args, kwargs = harness.enqueued[0]
    assert args[0] == "process_video_task" and args[4] == "youtube"


async def test_production_creator_over_duration_skips_without_a_task(monkeypatch):
    harness = _CreatorHarness(monkeypatch, duration=99999.0, duration_limit=5400)
    with pytest.raises(SkipVideo) as excinfo:
        await create_task_from_channel_video(
            None, await _monitor_dict(), entry("longvod0002"),
            queue_adapter=harness,
        )
    assert excinfo.value.reason == "over_duration_cap"
    assert harness.created == [] and harness.enqueued == []


async def test_production_creator_short_backstop(monkeypatch):
    harness = _CreatorHarness(monkeypatch, duration=45.0)
    with pytest.raises(SkipVideo) as excinfo:
        await create_task_from_channel_video(
            None, await _monitor_dict(include_shorts=False), entry("short4500001"),
            queue_adapter=harness,
        )
    assert excinfo.value.reason == "short"
    assert harness.created == []

    # The same video is fine on a channel that includes Shorts.
    harness2 = _CreatorHarness(monkeypatch, duration=45.0)
    task_id = await create_task_from_channel_video(
        None, await _monitor_dict(include_shorts=True), entry("short4500001"),
        queue_adapter=harness2,
    )
    assert task_id == "task-42"


# -- routes -------------------------------------------------------------


class _Request:
    def __init__(self, payload=None):
        self._payload = payload or {}

    async def json(self):
        return self._payload


@pytest.fixture
def route_env(monkeypatch):
    repo = FakeRepository()
    creator = RecordingCreator()
    service = ChannelMonitorService(
        repo, FakeDetector(), creator, config=_config()
    )
    monkeypatch.setattr(routes, "repository", repo)
    monkeypatch.setattr(routes, "production_service", lambda: service)

    async def fake_user_id(request, db, config):
        return "user-1"

    monkeypatch.setattr(routes, "resolve_authenticated_user_id", fake_user_id)
    return repo, creator


def _stub_resolution(monkeypatch, entries):
    async def fake_resolve(source_input):
        if "junk" in source_input:
            raise ChannelFeedError("That does not look like a YouTube channel.")
        return CHANNEL_ID, "Speed Clips"

    async def fake_fetch(channel_id, **kwargs):
        return list(entries)

    monkeypatch.setattr(routes, "resolve_channel", fake_resolve)
    monkeypatch.setattr(routes, "fetch_channel_feed", fake_fetch)


async def test_add_route_baselines_existing_uploads(route_env, monkeypatch):
    repo, creator = route_env
    _stub_resolution(monkeypatch, [entry("b2"), entry("b1")])

    result = await routes.add_channel_monitor(_Request({
        "source_input": "@speedclips",
    }), None)
    monitor = result["monitor"]
    assert monitor["template_mode"] == "plain"  # stored explicitly
    assert monitor["creation_mode"] == "full_auto"
    assert monitor["max_tasks_per_day"] == 3
    assert monitor["include_shorts"] is False
    assert creator.calls == []
    assert len(repo.videos) == 2
    assert all(r["state"] == "baseline" for r in repo.videos.values())


async def test_add_route_clip_newest_now_runs_the_claim_path(route_env, monkeypatch):
    repo, creator = route_env
    _stub_resolution(monkeypatch, [entry("n2"), entry("n1")])

    result = await routes.add_channel_monitor(_Request({
        "source_input": "@speedclips",
        "clip_newest_now": True,
        "template_mode": "auto",
    }), None)
    assert result["monitor"]["template_mode"] == "auto"
    assert len(creator.calls) == 1
    assert creator.calls[0][1].video_id == "n2"
    states = {vid: r["state"] for (_u, vid), r in repo.videos.items()}
    assert states == {"n2": "queued", "n1": "baseline"}


async def test_add_route_refuses_manual_cutter(route_env, monkeypatch):
    repo, _creator = route_env
    _stub_resolution(monkeypatch, [])
    with pytest.raises(HTTPException) as excinfo:
        await routes.add_channel_monitor(_Request({
            "source_input": "@speedclips",
            "creation_mode": "manual_cutter_ai",
        }), None)
    assert excinfo.value.status_code == 422
    assert repo.monitors == {}


async def test_add_route_bad_input_creates_no_row(route_env, monkeypatch):
    repo, _creator = route_env
    _stub_resolution(monkeypatch, [])
    with pytest.raises(HTTPException) as excinfo:
        await routes.add_channel_monitor(_Request({
            "source_input": "junk input here",
        }), None)
    assert excinfo.value.status_code == 422
    assert repo.monitors == {}


async def test_add_route_duplicate_channel_is_refused(route_env, monkeypatch):
    repo, _creator = route_env
    _stub_resolution(monkeypatch, [])
    await routes.add_channel_monitor(_Request({"source_input": "@speedclips"}), None)
    with pytest.raises(HTTPException) as excinfo:
        await routes.add_channel_monitor(_Request({"source_input": "@speedclips"}), None)
    assert excinfo.value.status_code == 422
    assert len(repo.monitors) == 1


async def test_settings_toggle_and_monitor_update_delete(route_env, monkeypatch):
    repo, _creator = route_env
    _stub_resolution(monkeypatch, [])

    result = await routes.update_channel_monitor_settings(
        _Request({"enabled": True}), None
    )
    assert result["settings"]["enabled"] is True

    added = await routes.add_channel_monitor(
        _Request({"source_input": "@speedclips"}), None
    )
    monitor_id = added["monitor"]["id"]

    updated = await routes.update_channel_monitor(
        monitor_id, _Request({"template_mode": "auto", "enabled": False}), None
    )
    assert updated["monitor"]["template_mode"] == "auto"
    assert updated["monitor"]["enabled"] is False

    listed = await routes.get_channel_monitors(_Request(), None)
    assert listed["settings"]["enabled"] is True
    assert len(listed["monitors"]) == 1

    deleted = await routes.delete_channel_monitor(monitor_id, _Request(), None)
    assert deleted == {"deleted": True}
    with pytest.raises(HTTPException) as excinfo:
        await routes.delete_channel_monitor(monitor_id, _Request(), None)
    assert excinfo.value.status_code == 404
