"""YouTube channel monitoring, Stage 2: the premiere ``waiting``
state, the metered yt-dlp fallback detector, manual re-drive of
finished claims, and per-channel campaign / brand-kit defaults.

Same harness shape as the Stage-1 suite — an in-memory repository
fake (extended with the Stage-2 claim transitions and columns), a
scripted detector pair, and recording task creators. No Postgres,
Redis, worker or live YouTube call. The Stage-1 suite is untouched
and must stay green beside this one.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from src.api.routes import channel_monitors as routes
from src.services import channel_monitor_service as service_module
from src.services.channel_monitor_service import (
    WAITING_LIMIT_SECONDS,
    ChannelMonitorService,
    SkipVideo,
    WaitVideo,
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
    """Stage-1's fake, extended with the Stage-2 columns and the
    waiting / re-drive claim transitions, mirroring the real
    repository's conditional-update semantics."""

    def __init__(self):
        self.settings = {}
        self.monitors = {}
        self.videos = {}
        self._seq = 0

    def _next_id(self, prefix):
        self._seq += 1
        return f"{prefix}-{self._seq}"

    async def get_settings(self, db, user_id):
        row = self.settings.get(user_id)
        return dict(row) if row else None

    async def upsert_settings(self, db, user_id, fields):
        row = {"user_id": user_id, "enabled": bool(fields.get("enabled", False))}
        self.settings[user_id] = row
        return dict(row)

    async def list_enabled_settings(self, db):
        return [dict(s) for s in self.settings.values() if s.get("enabled")]

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
            "campaign_id": None, "brand_kit_id": None, "last_fallback_at": None,
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

    async def get_video(self, db, user_id, video_id):
        row = self.videos.get((user_id, video_id))
        return dict(row) if row else None

    async def get_video_row(self, db, user_id, video_row_id):
        row = self._find_video(video_row_id)
        return dict(row) if row and row["user_id"] == user_id else None

    def _video_row(self, record, *, state, attempts, skip_reason=None):
        return {
            "id": self._next_id("vid"),
            "state": state, "skip_reason": skip_reason,
            "task_id": None, "attempts": attempts, "last_error": None,
            "waiting_since": None,
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

    async def list_waiting_videos(self, db, monitor_id, limit=15):
        rows = [
            dict(r) for r in self.videos.values()
            if r["monitor_id"] == monitor_id and r["state"] == "waiting"
        ]
        rows.sort(key=lambda r: r["waiting_since"] or r["created_at"])
        return rows[:limit]

    async def reclaim_waiting_video(self, db, video_row_id):
        row = self._find_video(video_row_id)
        if row is None or row["state"] != "waiting":
            return None
        row["state"] = "processing"  # attempts deliberately untouched
        row["updated_at"] = datetime.now(timezone.utc)
        return dict(row)

    async def reclaim_for_redrive(self, db, video_row_id):
        row = self._find_video(video_row_id)
        if row is None or row["state"] not in ("parked", "skipped", "failed"):
            return None
        if row.get("task_id") is not None:
            return None
        row["state"] = "processing"
        row["attempts"] = 1
        row["waiting_since"] = None
        row["last_error"] = None
        row["skip_reason"] = None
        row["updated_at"] = datetime.now(timezone.utc)
        return dict(row)

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
                and row["state"] in ("processing", "failed", "waiting")
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
        self.calls = 0

    async def __call__(self, channel_id):
        self.calls += 1
        if self.error is not None:
            raise self.error
        return list(self.feeds.get(channel_id, []))


class FakeFallbackDetector:
    def __init__(self, entries=None):
        self.entries = entries or []
        self.error = None
        self.calls = 0

    async def __call__(self, channel_id):
        self.calls += 1
        if self.error is not None:
            raise self.error
        return list(self.entries)


class ScriptedCreator:
    """Outcomes keyed by video id: a list consumed one per call —
    an Exception instance is raised, anything else is the task id.
    The last outcome repeats."""

    def __init__(self, script=None):
        self.script = script or {}
        self.calls = []

    async def __call__(self, db, monitor, entry):
        self.calls.append((dict(monitor), entry))
        outcomes = self.script.get(entry.video_id) or [f"task-{len(self.calls)}"]
        index = min(
            sum(1 for _m, e in self.calls if e.video_id == entry.video_id) - 1,
            len(outcomes) - 1,
        )
        outcome = outcomes[index]
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


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


def _service(repo, detector=None, creator=None, fallback=None):
    return ChannelMonitorService(
        repo, detector or FakeDetector(), creator or ScriptedCreator(),
        config=_config(), fallback_detector=fallback,
    )


# -- waiting state -------------------------------------------------------


async def test_premiere_waits_then_becomes_exactly_one_task():
    repo = FakeRepository()
    monitor = await _add_monitor(repo)
    creator = ScriptedCreator({
        "premiere001": [WaitVideo("upcoming premiere"), "task-premiere"],
    })
    detector = FakeDetector({CHANNEL_ID: [entry("premiere001")]})
    service = _service(repo, detector, creator)

    summary = await service.poll_monitor(None, monitor)
    assert summary["waiting"] == 1 and summary["queued"] == 0
    row = repo.videos[("user-1", "premiere001")]
    assert row["state"] == "waiting"
    assert row["attempts"] == 1  # the wait spent no retry attempt
    assert row["waiting_since"] is not None
    assert "premiere" in row["last_error"]

    # Next poll: the VOD now exists. The feed still lists the entry,
    # but the claim is settled from the waiting path, exactly once.
    summary = await service.poll_monitor(None, monitor)
    assert summary["queued"] == 1
    assert len(creator.calls) == 2
    assert row["state"] == "queued" and row["task_id"] == "task-premiere"
    assert row["waiting_since"] is None

    # And it never duplicates on later polls.
    summary = await service.poll_monitor(None, monitor)
    assert summary["queued"] == 0 and len(creator.calls) == 2


async def test_waiting_never_consumes_the_retry_budget():
    repo = FakeRepository()
    monitor = await _add_monitor(repo)
    creator = ScriptedCreator({
        "premiere002": [
            WaitVideo("upcoming"), WaitVideo("upcoming"),
            WaitVideo("upcoming"), WaitVideo("upcoming"),
            RuntimeError("metadata exploded"),
            "task-late",
        ],
    })
    detector = FakeDetector({CHANNEL_ID: [entry("premiere002")]})
    service = _service(repo, detector, creator)

    for _ in range(4):  # more waits than MAX_ATTEMPTS
        summary = await service.poll_monitor(None, monitor)
        assert summary["waiting"] == 1
    row = repo.videos[("user-1", "premiere002")]
    assert row["state"] == "waiting" and row["attempts"] == 1

    # A real failure after the wait starts from a full budget:
    # one failure is 'failed', not parked.
    summary = await service.poll_monitor(None, monitor)
    assert summary["failed"] == 1
    assert row["state"] == "failed" and row["attempts"] == 1

    row["updated_at"] = datetime.now(timezone.utc) - timedelta(hours=1)
    summary = await service.poll_monitor(None, monitor)
    assert summary["queued"] == 1 and row["task_id"] == "task-late"


async def test_waiting_past_the_limit_parks_with_the_reason():
    repo = FakeRepository()
    monitor = await _add_monitor(repo)
    creator = ScriptedCreator({"premiere003": [WaitVideo("upcoming")]})
    detector = FakeDetector({CHANNEL_ID: [entry("premiere003")]})
    service = _service(repo, detector, creator)

    await service.poll_monitor(None, monitor)
    row = repo.videos[("user-1", "premiere003")]
    assert row["state"] == "waiting"
    row["waiting_since"] = datetime.now(timezone.utc) - timedelta(
        seconds=WAITING_LIMIT_SECONDS + 60
    )

    summary = await service.poll_monitor(None, monitor)
    assert row["state"] == "parked"
    assert "waiting_expired" in row["last_error"]
    assert row["waiting_since"] is None
    # The creator was never re-run for the expired claim.
    assert len(creator.calls) == 1
    assert summary["queued"] == 0


# -- fallback detector ---------------------------------------------------


async def test_healthy_feed_never_triggers_the_fallback():
    repo = FakeRepository()
    monitor = await _add_monitor(repo)
    creator = ScriptedCreator()
    detector = FakeDetector({CHANNEL_ID: [entry("healthy0001")]})
    fallback = FakeFallbackDetector([entry("fallback001")])
    service = _service(repo, detector, creator, fallback=fallback)

    summary = await service.poll_monitor(None, monitor)
    assert summary["queued"] == 1 and summary["feed_error"] is False
    assert fallback.calls == 0
    assert repo.monitors[monitor["id"]]["last_fallback_at"] is None


async def test_feed_outage_recovers_via_fallback_and_is_metered():
    repo = FakeRepository()
    monitor = await _add_monitor(repo)
    creator = ScriptedCreator()
    detector = FakeDetector()
    detector.error = ChannelFeedError("YouTube answered the channel feed with HTTP 503.")
    fallback = FakeFallbackDetector([entry("recovered01")])
    service = _service(repo, detector, creator, fallback=fallback)

    summary = await service.poll_monitor(None, repo.monitors[monitor["id"]])
    assert summary["feed_error"] is False and summary["queued"] == 1
    assert fallback.calls == 1
    assert len(creator.calls) == 1
    stored = repo.monitors[monitor["id"]]
    assert stored["last_fallback_at"] is not None
    assert "fallback" in stored["last_error"]
    assert stored["consecutive_failures"] == 1  # the RSS failure still counts

    # Still inside the hour: no second yt-dlp pass. With nothing
    # recovered, the cycle is the plain feed-error health path.
    summary = await service.poll_monitor(None, repo.monitors[monitor["id"]])
    assert summary["feed_error"] is True
    assert fallback.calls == 1
    assert repo.monitors[monitor["id"]]["consecutive_failures"] == 2

    # An hour later the fallback may run again — and proves no
    # duplicate: the recovered video is already claimed.
    stored = repo.monitors[monitor["id"]]
    stored["last_fallback_at"] = datetime.now(timezone.utc) - timedelta(hours=2)
    summary = await service.poll_monitor(None, stored)
    assert summary["feed_error"] is False
    assert fallback.calls == 2
    assert len(creator.calls) == 1


async def test_empty_feed_earns_one_fallback_pass():
    repo = FakeRepository()
    monitor = await _add_monitor(repo)
    creator = ScriptedCreator()
    detector = FakeDetector({CHANNEL_ID: []})
    fallback = FakeFallbackDetector([entry("emptyfeed01")])
    service = _service(repo, detector, creator, fallback=fallback)

    summary = await service.poll_monitor(None, monitor)
    assert summary["queued"] == 1 and fallback.calls == 1

    # Both empty is a clean, quiet poll — no error anywhere.
    repo2 = FakeRepository()
    monitor2 = await _add_monitor(repo2)
    fallback2 = FakeFallbackDetector([])
    service2 = _service(
        repo2, FakeDetector({CHANNEL_ID: []}), ScriptedCreator(), fallback=fallback2
    )
    summary2 = await service2.poll_monitor(None, monitor2)
    assert summary2["feed_error"] is False and summary2["queued"] == 0
    assert repo2.videos == {}
    assert repo2.monitors[monitor2["id"]]["last_error"] is None


async def test_fallback_failure_keeps_the_rss_health_path():
    repo = FakeRepository()
    monitor = await _add_monitor(repo)
    detector = FakeDetector()
    detector.error = ChannelFeedError("YouTube answered the channel feed with HTTP 503.")
    fallback = FakeFallbackDetector()
    fallback.error = ChannelFeedError("yt-dlp could not read the channel.")
    service = _service(repo, detector, ScriptedCreator(), fallback=fallback)

    summary = await service.poll_monitor(None, monitor)
    assert summary["feed_error"] is True
    assert repo.videos == {}
    stored = repo.monitors[monitor["id"]]
    assert stored["consecutive_failures"] == 1
    assert "503" in stored["last_error"]
    # The failed attempt still stamps the meter.
    assert stored["last_fallback_at"] is not None


# -- manual re-drive -----------------------------------------------------


async def _parked_row(repo, monitor, video_id="parked00001"):
    creator = ScriptedCreator({video_id: [RuntimeError("boom")] * 3})
    detector = FakeDetector({CHANNEL_ID: [entry(video_id)]})
    service = _service(repo, detector, creator)
    for _ in range(3):
        await service.poll_monitor(None, monitor)
        row = repo.videos[("user-1", video_id)]
        row["updated_at"] = datetime.now(timezone.utc) - timedelta(hours=1)
    await service.poll_monitor(None, monitor)
    row = repo.videos[("user-1", video_id)]
    assert row["state"] == "parked"
    return row


async def test_redrive_parked_creates_the_task_once():
    repo = FakeRepository()
    monitor = await _add_monitor(repo)
    row = await _parked_row(repo, monitor)

    creator = ScriptedCreator({"parked00001": ["task-redriven"]})
    service = _service(repo, FakeDetector(), creator)
    result = await service.redrive_video(None, "user-1", row["id"])
    assert result["outcome"] == "queued"
    assert "Task created" in result["message"]
    assert result["video"]["task_id"] == "task-redriven"
    assert repo.videos[("user-1", "parked00001")]["state"] == "queued"

    # A second press is an honest no-op, not a second task.
    result = await service.redrive_video(None, "user-1", row["id"])
    assert result["outcome"] == "already_tasked"
    assert len(creator.calls) == 1


async def test_redrive_skipped_cap_row_bypasses_the_daily_cap():
    repo = FakeRepository()
    monitor = await _add_monitor(repo, max_tasks_per_day=1)
    creator = ScriptedCreator()
    detector = FakeDetector({CHANNEL_ID: [entry("capb"), entry("capa")]})
    service = _service(repo, detector, creator)
    await service.poll_monitor(None, monitor)
    capped = repo.videos[("user-1", "capb")]
    assert capped["state"] == "skipped" and capped["skip_reason"] == "daily_cap"

    # The cap still reads as reached (one task exists today), yet an
    # explicit re-drive goes through: the cap brakes automatic
    # creation, not a button press.
    result = await service.redrive_video(None, "user-1", capped["id"])
    assert result["outcome"] == "queued"
    assert repo.videos[("user-1", "capb")]["task_id"] is not None


async def test_redrive_baseline_waiting_and_unknown_are_noops():
    repo = FakeRepository()
    monitor = await _add_monitor(repo)
    service = _service(repo, FakeDetector(), ScriptedCreator())
    entries = [entry("base0000001")]
    await service.seed_baseline(None, monitor, entries)
    baseline_row = repo.videos[("user-1", "base0000001")]

    result = await service.redrive_video(None, "user-1", baseline_row["id"])
    assert result["outcome"] == "not_retryable"
    assert baseline_row["state"] == "baseline"

    result = await service.redrive_video(None, "user-1", "no-such-row")
    assert result["outcome"] == "not_found"

    creator = ScriptedCreator({"waiting0001": [WaitVideo("upcoming")]})
    service2 = _service(
        repo, FakeDetector({CHANNEL_ID: [entry("waiting0001")]}), creator
    )
    await service2.poll_monitor(None, monitor)
    waiting_row = repo.videos[("user-1", "waiting0001")]
    assert waiting_row["state"] == "waiting"
    result = await service2.redrive_video(None, "user-1", waiting_row["id"])
    assert result["outcome"] == "not_retryable"
    assert len(creator.calls) == 1  # the press ran nothing


# -- production creator: live status and campaign stamping ----------------


class _CreatorHarness:
    """Stubs the production creator's collaborators at their seams,
    as the Stage-1 harness does, plus live status and campaigns."""

    def __init__(
        self, monkeypatch, *, duration=3600.0, duration_limit=5400,
        live_status=None, campaigns=None,
    ):
        self.created: list[dict] = []
        self.enqueued: list[tuple] = []
        self.saved_metadata: list[dict] = []
        holder = self
        self.campaigns = campaigns or {}

        class FakeBilling:
            def __init__(self, db):
                pass

            async def assert_can_create_task(self, user_id, duration_seconds=None):
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
            info = {"duration": duration, "title": "Feed Title"}
            if live_status is not None:
                info["live_status"] = live_status
            return info

        async def fake_save_metadata(db, task_id, payload):
            holder.saved_metadata.append(payload)

        async def fake_get_campaign(db, user_id, campaign_id):
            return holder.campaigns.get((user_id, campaign_id))

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
        monkeypatch.setattr(
            "src.repositories.publishing_repository.PublishingRepository.get_campaign",
            fake_get_campaign,
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
        "campaign_id": None, "brand_kit_id": None,
    }
    base.update(overrides)
    return base


async def test_production_creator_waits_for_upcoming_and_live(monkeypatch):
    for status in ("is_upcoming", "is_live"):
        harness = _CreatorHarness(monkeypatch, duration=None, live_status=status)
        with pytest.raises(WaitVideo):
            await create_task_from_channel_video(
                None, await _monitor_dict(), entry("premiere009"),
                queue_adapter=harness,
            )
        assert harness.created == [] and harness.enqueued == []

    # A finished upload with the same shape creates normally.
    harness = _CreatorHarness(monkeypatch, duration=3600.0, live_status="was_live")
    task_id = await create_task_from_channel_video(
        None, await _monitor_dict(), entry("premiere009"), queue_adapter=harness,
    )
    assert task_id == "task-42"


_BRIEF = {
    "version": 2,
    "requirements": [{"type": "topic_focus", "text": "Expedia"}],
}


async def test_production_creator_stamps_campaign_brief_and_kit(monkeypatch):
    harness = _CreatorHarness(
        monkeypatch,
        duration=3600.0,
        campaigns={("user-1", "camp-1"): {"id": "camp-1", "brief": _BRIEF}},
    )
    task_id = await create_task_from_channel_video(
        None,
        await _monitor_dict(campaign_id="camp-1", brand_kit_id="kit-9"),
        entry("campvid0001"),
        queue_adapter=harness,
    )
    assert task_id == "task-42"
    kwargs = harness.created[0]
    assert kwargs["campaign_id"] == "camp-1"
    assert kwargs["brand_kit_id"] == "kit-9"
    brief = kwargs["content_brief"]
    assert brief is not None
    assert brief["requirements"][0]["text"] == "Expedia"


async def test_production_creator_without_campaign_stamps_nothing(monkeypatch):
    harness = _CreatorHarness(monkeypatch, duration=3600.0)
    await create_task_from_channel_video(
        None, await _monitor_dict(), entry("campvid0002"), queue_adapter=harness,
    )
    kwargs = harness.created[0]
    assert kwargs["campaign_id"] is None
    assert kwargs["brand_kit_id"] is None
    assert kwargs["content_brief"] is None


async def test_production_creator_deleted_campaign_fails_honestly(monkeypatch):
    harness = _CreatorHarness(monkeypatch, duration=3600.0, campaigns={})
    with pytest.raises(ValueError) as excinfo:
        await create_task_from_channel_video(
            None,
            await _monitor_dict(campaign_id="camp-gone"),
            entry("campvid0003"),
            queue_adapter=harness,
        )
    assert "campaign_not_found" in str(excinfo.value)
    assert harness.created == []


# -- routes: retry + campaign validation ---------------------------------


class _Request:
    def __init__(self, payload=None):
        self._payload = payload or {}

    async def json(self):
        return self._payload


@pytest.fixture
def route_env(monkeypatch):
    repo = FakeRepository()
    creator = ScriptedCreator()
    service = ChannelMonitorService(
        repo, FakeDetector(), creator, config=_config()
    )
    monkeypatch.setattr(routes, "repository", repo)
    monkeypatch.setattr(routes, "production_service", lambda: service)
    monkeypatch.setattr(
        routes.PublishingRepository, "get_campaign",
        staticmethod(lambda db, user_id, campaign_id: _campaign_lookup(campaign_id)),
    )

    async def fake_user_id(request, db, config):
        return "user-1"

    monkeypatch.setattr(routes, "resolve_authenticated_user_id", fake_user_id)
    return repo, creator


async def _campaign_lookup(campaign_id):
    if campaign_id == "camp-1":
        return {"id": "camp-1", "brief": _BRIEF}
    return None


def _stub_resolution(monkeypatch, entries):
    async def fake_resolve(source_input):
        if "junk" in source_input:
            raise ChannelFeedError("That does not look like a YouTube channel.")
        return CHANNEL_ID, "Speed Clips"

    async def fake_fetch(channel_id, **kwargs):
        return list(entries)

    monkeypatch.setattr(routes, "resolve_channel", fake_resolve)
    monkeypatch.setattr(routes, "fetch_channel_feed", fake_fetch)


async def test_retry_route_redrives_and_reports(route_env, monkeypatch):
    repo, creator = route_env
    monitor = await _add_monitor(repo)
    row = await _parked_row(repo, monitor)

    result = await routes.retry_channel_monitor_video(row["id"], _Request(), None)
    assert result["outcome"] == "queued"
    assert result["video"]["task_id"] is not None

    with pytest.raises(HTTPException) as excinfo:
        await routes.retry_channel_monitor_video("nope", _Request(), None)
    assert excinfo.value.status_code == 404


async def test_add_route_accepts_known_campaign_and_kit(route_env, monkeypatch):
    repo, _creator = route_env
    _stub_resolution(monkeypatch, [])
    result = await routes.add_channel_monitor(_Request({
        "source_input": "@speedclips",
        "campaign_id": "camp-1",
        "brand_kit_id": "kit-9",
    }), None)
    monitor = result["monitor"]
    assert monitor["campaign_id"] == "camp-1"
    assert monitor["brand_kit_id"] == "kit-9"


async def test_add_route_refuses_unknown_campaign(route_env, monkeypatch):
    repo, _creator = route_env
    _stub_resolution(monkeypatch, [])
    with pytest.raises(HTTPException) as excinfo:
        await routes.add_channel_monitor(_Request({
            "source_input": "@speedclips",
            "campaign_id": "camp-gone",
        }), None)
    assert excinfo.value.status_code == 422
    assert repo.monitors == {}


async def test_add_route_refuses_blank_campaign_id(route_env, monkeypatch):
    repo, _creator = route_env
    _stub_resolution(monkeypatch, [])
    with pytest.raises(HTTPException) as excinfo:
        await routes.add_channel_monitor(_Request({
            "source_input": "@speedclips",
            "campaign_id": "   ",
        }), None)
    assert excinfo.value.status_code == 422
    assert repo.monitors == {}


async def test_update_route_can_set_and_clear_campaign(route_env, monkeypatch):
    repo, _creator = route_env
    _stub_resolution(monkeypatch, [])
    added = await routes.add_channel_monitor(
        _Request({"source_input": "@speedclips"}), None
    )
    monitor_id = added["monitor"]["id"]

    updated = await routes.update_channel_monitor(
        monitor_id, _Request({"campaign_id": "camp-1"}), None
    )
    assert updated["monitor"]["campaign_id"] == "camp-1"

    cleared = await routes.update_channel_monitor(
        monitor_id, _Request({"campaign_id": None}), None
    )
    assert cleared["monitor"]["campaign_id"] is None

    with pytest.raises(HTTPException) as excinfo:
        await routes.update_channel_monitor(
            monitor_id, _Request({"campaign_id": "camp-gone"}), None
        )
    assert excinfo.value.status_code == 422
