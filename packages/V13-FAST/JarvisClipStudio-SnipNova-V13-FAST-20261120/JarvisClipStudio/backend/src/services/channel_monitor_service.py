"""YouTube channel monitoring: turn new uploads into tasks.

The sibling of the watch-folder autopilot, with the same discipline
and a different arrival signal. For each user who enabled it
(``channel_monitor_settings``) and each channel they watch
(``channel_monitors``), a polling loop reads the channel's RSS feed
and hands every genuinely new upload to the normal task pipeline
through :func:`create_task_from_channel_video` — the manual
YouTube-URL flow, with the channel's stored defaults. There is no
parallel pipeline.

Durability lives in ``channel_monitor_videos``: a video is claimed
with an atomic insert before any work happens, so restarts and
duplicate watcher processes cannot queue it twice. A claim orphaned
in ``processing`` with no task (the watcher died between the claim
insert and task creation) is recovered on a later cycle once it is
stale. A failure is recorded with its reason and retried at most
``MAX_ATTEMPTS`` times with a backoff, then parked. Skips that no
retry could change — over the duration cap, a filtered Short, the
channel's daily cap — are terminal rows with a plain ``skip_reason``,
visible in the activity list instead of silently swallowed.

A feed that fails to fetch is a *health* event on the monitor row
(``last_error``, ``consecutive_failures``), never "no new videos":
no video rows are written for a failed fetch, and the channel is
never auto-disabled — the feed has documented multi-hour outages
and simply recovers on a later poll. While the feed is failing (or
answering empty, the other documented outage shape), one metered
yt-dlp fallback pass per channel per hour can recover the newest
uploads; the feed stays the primary detector and a healthy feed
never triggers the fallback.

A video whose preflight says it is not downloadable yet — an
upcoming premiere, or a stream still live whose VOD does not exist
— is not a failure either. Its claim moves to the ``waiting`` state
and is re-checked on the normal poll cadence, without spending any
of its bounded retry attempts, until the video publishes or the
48-hour waiting limit passes and it parks with the reason on the
row. A row the user re-drives by hand (a parked or skipped video in
the activity list) re-enters through the same claim path as a fresh
episode.
"""

from __future__ import annotations

import asyncio
import logging
import math
import os
import secrets
import time
from datetime import datetime, timezone
from typing import Awaitable, Callable, Optional

from sqlalchemy.ext.asyncio import AsyncSession

from ..config import Config, get_config
from ..repositories.channel_monitor_repository import ChannelMonitorRepository
from ..youtube_feed import (
    FeedEntry,
    fetch_channel_entries_via_ytdlp,
    fetch_channel_feed,
)

logger = logging.getLogger(__name__)

MAX_ATTEMPTS = 3
RETRY_BACKOFF_SECONDS = 300
# A claim still in 'processing' with no task after this long is
# orphaned: the watcher died between the claim insert and task
# creation. Task creation is a local call plus one metadata fetch,
# so 30 minutes is a wide margin. Env-tunable like the folder one.
STALE_CLAIM_SECONDS = max(
    60, int(os.getenv("CHANNEL_MONITOR_STALE_CLAIM_SECONDS", "1800"))
)
# Belt-and-braces Shorts backstop: the feed link form is the primary
# signal, but a <=61s duration at preflight with Shorts excluded is
# also a Short for this feature's purposes.
SHORT_DURATION_SECONDS = 61
# A premiere or live stream can surface in the feed hours before any
# downloadable VOD exists. The claim waits and is re-checked each
# poll; after this long it parks instead of waiting forever.
WAITING_LIMIT_SECONDS = max(
    300, int(os.getenv("CHANNEL_MONITOR_WAITING_LIMIT_SECONDS", str(48 * 3600)))
)
# yt-dlp live_status values that mean "no downloadable video yet".
NOT_READY_LIVE_STATUSES = {"is_upcoming", "is_live"}
# The yt-dlp fallback is a heavyweight page extraction and the route
# most likely to draw rate limiting, so while the RSS feed is failing
# it runs at most once per channel per this interval. The stamp is
# durable (channel_monitors.last_fallback_at), so restarts honour it.
FALLBACK_MIN_INTERVAL_SECONDS = 3600
_ERROR_LIMIT = 500

# (channel_id) -> entries, newest first
Detector = Callable[[str], Awaitable[list[FeedEntry]]]
# (db, monitor, entry) -> task_id
TaskCreator = Callable[[AsyncSession, dict, FeedEntry], Awaitable[str]]


class SkipVideo(Exception):
    """The video must not become a task and no retry could change
    that (over the duration cap, or a Short under the duration
    backstop). Carries the ``skip_reason`` stored on the claim row."""

    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


class WaitVideo(Exception):
    """The video is not downloadable yet — an upcoming premiere or
    a stream that has not finished — so the claim waits and is
    re-checked on the poll cadence. Unlike a failure this never
    spends a retry attempt. Carries a plain note for the row."""

    def __init__(self, note: str):
        super().__init__(note)
        self.note = note


def _parse_timestamp(value) -> Optional[datetime]:
    """Repository rows carry timestamps as ISO strings; in-process
    rows may carry datetimes. Both (and None) are accepted."""
    if value is None:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)
    if isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value)
        except ValueError:
            return None
        return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=timezone.utc)
    return None


def _entry_from_row(row: dict) -> FeedEntry:
    """Rebuild a feed entry from a stored claim row, so a failed or
    orphaned claim can be re-driven even after the video has fallen
    out of the feed's 15-entry window."""
    published = row.get("published_at")
    if isinstance(published, str):
        try:
            published = datetime.fromisoformat(published)
        except ValueError:
            published = None
    return FeedEntry(
        video_id=row["video_id"],
        title=row.get("title") or "",
        url=row["video_url"],
        published_at=published,
        is_short_link=False,
    )


def _claim_record(monitor: dict, entry: FeedEntry) -> dict:
    return {
        "user_id": monitor["user_id"],
        "monitor_id": monitor["id"],
        "video_id": entry.video_id,
        "video_url": entry.url,
        "title": entry.title,
        "published_at": entry.published_at,
    }


def _local_midnight() -> datetime:
    return datetime.now().astimezone().replace(
        hour=0, minute=0, second=0, microsecond=0
    )


class ChannelMonitorService:
    """One user's poll cycle, with the detector and task creator
    injected so tests can substitute fakes and no pipeline code is
    duplicated."""

    def __init__(
        self,
        repository: ChannelMonitorRepository,
        detector: Detector,
        task_creator: TaskCreator,
        config: Config | None = None,
        fallback_detector: Detector | None = None,
    ):
        self.repository = repository
        self.detector = detector
        self.task_creator = task_creator
        self.config = config or get_config()
        self.fallback_detector = fallback_detector

    async def poll_user(self, db: AsyncSession, settings: dict) -> dict:
        summary = {"monitors": 0, "queued": 0, "skipped": 0, "failed": 0, "waiting": 0, "feed_errors": 0}
        if not settings.get("enabled"):
            return summary
        user_id = settings["user_id"]
        monitors = await self.repository.list_enabled_monitors(db, user_id)
        for monitor in monitors:
            summary["monitors"] += 1
            try:
                outcome = await self.poll_monitor(db, monitor)
            except Exception:
                logger.exception(
                    "Channel monitor poll failed for channel %s",
                    monitor.get("channel_id"),
                )
                outcome = {"queued": 0, "skipped": 0, "failed": 1, "waiting": 0, "feed_error": False}
            summary["queued"] += outcome["queued"]
            summary["skipped"] += outcome["skipped"]
            summary["failed"] += outcome["failed"]
            summary["waiting"] += outcome["waiting"]
            summary["feed_errors"] += 1 if outcome["feed_error"] else 0
        try:
            await self.repository.park_videos_for_missing_monitors(db, user_id)
        except Exception:
            logger.exception("Channel monitor removed-monitor sweep failed")
        return summary

    def _fallback_allowed(self, monitor: dict, now: datetime) -> bool:
        """The fallback is metered: at most one yt-dlp pass per
        channel per hour while the feed is failing, stamped durably
        on the monitor row so restarts honour the limit."""
        if self.fallback_detector is None:
            return False
        last = _parse_timestamp(monitor.get("last_fallback_at"))
        if last is None:
            return True
        return (now - last).total_seconds() >= FALLBACK_MIN_INTERVAL_SECONDS

    async def _run_fallback(
        self, db: AsyncSession, monitor: dict, now: datetime
    ) -> Optional[list[FeedEntry]]:
        """One fallback detection pass. Every attempt is stamped —
        the attempt itself is what the hourly meter counts — and a
        fallback failure returns None, never raises: the RSS failure
        remains the health story for the cycle."""
        assert self.fallback_detector is not None  # gated by _fallback_allowed
        try:
            entries = await self.fallback_detector(monitor["channel_id"])
        except Exception as exc:
            logger.warning(
                "Channel fallback detection failed for %s: %s",
                monitor.get("channel_id"), exc,
            )
            entries = None
        try:
            await self.repository.update_monitor(db, monitor["id"], {
                "last_fallback_at": now,
            })
        except Exception:
            logger.exception("Channel monitor fallback stamp failed")
        return entries

    async def poll_monitor(self, db: AsyncSession, monitor: dict) -> dict:
        summary = {"queued": 0, "skipped": 0, "failed": 0, "waiting": 0, "feed_error": False}
        now = datetime.now(timezone.utc)
        entries: Optional[list[FeedEntry]] = None
        rss_error: Optional[Exception] = None
        try:
            entries = await self.detector(monitor["channel_id"])
        except Exception as exc:
            rss_error = exc

        # RSS is primary. Only a feed that errored or answered empty
        # — both documented outage shapes — earns one metered yt-dlp
        # fallback pass; a healthy feed never triggers it, so the
        # normal path is byte-for-byte the Stage-1 behaviour.
        used_fallback = False
        if (entries is None or len(entries) == 0) and self._fallback_allowed(monitor, now):
            fallback_entries = await self._run_fallback(db, monitor, now)
            if fallback_entries is not None:
                entries = fallback_entries
                # The health note is owed only when the fallback
                # changed the outcome: it recovered videos, or it
                # answered at all while the feed was erroring. An
                # empty feed confirmed empty is a clean poll.
                used_fallback = rss_error is not None or len(fallback_entries) > 0

        if entries is None:
            assert rss_error is not None  # detection only fails via the feed
            await self.repository.update_monitor(db, monitor["id"], {
                "last_polled_at": now,
                "last_error": str(rss_error)[:_ERROR_LIMIT] or rss_error.__class__.__name__,
                "consecutive_failures": int(monitor.get("consecutive_failures") or 0) + 1,
            })
            logger.warning(
                "Channel feed failed for %s: %s", monitor.get("channel_id"), rss_error
            )
            summary["feed_error"] = True
            return summary

        if used_fallback:
            detail = (
                str(rss_error)[:200] if rss_error is not None else "empty response"
            )
            await self.repository.update_monitor(db, monitor["id"], {
                "last_polled_at": now,
                "last_error": (
                    f"RSS feed unavailable ({detail}); latest videos "
                    "recovered with the yt-dlp fallback"
                )[:_ERROR_LIMIT],
                "consecutive_failures": int(monitor.get("consecutive_failures") or 0)
                + (1 if rss_error is not None else 0),
            })
        else:
            await self.repository.update_monitor(db, monitor["id"], {
                "last_polled_at": now,
                "last_error": None,
                "consecutive_failures": 0,
            })

        # Waiting claims first: a premiere that has now published is
        # the oldest outstanding work on the channel. Each is either
        # re-checked (its wait costs no retry attempt) or, past the
        # waiting limit, parked with the reason on the row. The
        # waiting seam is read through getattr so a repository double
        # that predates Stage 2 simply has no waiting rows.
        list_waiting = getattr(self.repository, "list_waiting_videos", None)
        waiting = await list_waiting(db, monitor["id"]) if list_waiting else []
        for row in waiting:
            outcome = await self._settle_waiting(db, monitor, row)
            if outcome is not None:
                summary[outcome] += 1

        # Re-drive bounded retries and orphaned claims next: both
        # are settled from their stored rows, because the video may
        # already have fallen out of the feed's 15-entry window.
        retryable = await self.repository.list_retryable_failed_videos(
            db, monitor["id"],
            max_attempts=MAX_ATTEMPTS, backoff_seconds=RETRY_BACKOFF_SECONDS,
        )
        for row in retryable:
            claimed = await self.repository.reclaim_failed_video(
                db, row["id"],
                max_attempts=MAX_ATTEMPTS, backoff_seconds=RETRY_BACKOFF_SECONDS,
            )
            if claimed is None:
                continue
            outcome = await self._create_for_claim(db, monitor, claimed)
            summary[outcome] += 1

        stale = await self.repository.list_stale_processing_videos(
            db, monitor["id"], stale_seconds=STALE_CLAIM_SECONDS
        )
        for row in stale:
            claimed = await self.repository.reclaim_stale_processing_video(
                db, row["id"], stale_seconds=STALE_CLAIM_SECONDS
            )
            if claimed is None:
                continue
            logger.info(
                "Channel monitor recovering orphaned claim %s for video %s",
                claimed["id"], claimed["video_id"],
            )
            outcome = await self._create_for_claim(db, monitor, claimed)
            summary[outcome] += 1

        # New arrivals, oldest first, so tasks queue in publish order.
        for entry in reversed(entries):
            existing = await self.repository.get_video(
                db, monitor["user_id"], entry.video_id
            )
            if existing is not None:
                continue
            outcome = await self.handle_new_entry(db, monitor, entry)
            summary[outcome] += 1
        return summary

    async def handle_new_entry(self, db: AsyncSession, monitor: dict, entry: FeedEntry) -> str:
        """Run one never-seen video through the filter → cap → claim
        → create path. Returns the summary bucket: queued, skipped,
        failed or known. Also used by the add-channel route for its
        one explicit "clip the newest video now" entry, so the
        explicit action takes exactly the watcher's path."""
        if entry.is_short_link and not monitor.get("include_shorts"):
            await self.repository.insert_terminal_video(
                db, _claim_record(monitor, entry),
                state="skipped", skip_reason="short",
            )
            return "skipped"

        cap = monitor.get("max_tasks_per_day")
        if cap is not None:
            created_today = await self.repository.count_tasks_created_since(
                db, monitor["id"], _local_midnight()
            )
            if created_today >= int(cap):
                await self.repository.insert_terminal_video(
                    db, _claim_record(monitor, entry),
                    state="skipped", skip_reason="daily_cap",
                )
                return "skipped"

        claimed = await self.repository.claim_video(db, _claim_record(monitor, entry))
        if claimed is None:
            return "known"  # another watcher claimed it first
        return await self._create_for_claim(db, monitor, claimed)

    async def redrive_video(
        self, db: AsyncSession, user_id: str, video_row_id: str
    ) -> dict:
        """The manual re-drive behind the activity list's retry
        button: one finished claim (parked, skipped or failed) is
        run through the claim path again, now, as a fresh episode.
        Everything else is an honest no-op with the reason in the
        message — a video that already has a task is never tasked
        twice, a baseline sighting stays a sighting, and a waiting
        video is already being re-checked every poll. The daily cap
        is a brake on *automatic* creation, so an explicit press is
        not re-checked against it; the creator's own decisions
        (duration cap, Shorts backstop, premiere wait) still apply,
        so a retry can land right back at skipped or waiting, with
        the fresh reason on the row."""
        row = await self.repository.get_video_row(db, user_id, video_row_id)
        if row is None:
            return {"outcome": "not_found", "message": "That video is no longer in the activity list.", "video": None}
        if row.get("task_id") or row["state"] == "queued":
            return {
                "outcome": "already_tasked",
                "message": "This video already has a task — it will not be created twice.",
                "video": row,
            }
        if row["state"] == "baseline":
            return {
                "outcome": "not_retryable",
                "message": "This video was already on the channel when monitoring started, so it was only marked as seen.",
                "video": row,
            }
        if row["state"] == "waiting":
            return {
                "outcome": "not_retryable",
                "message": "This video is not downloadable yet — the watcher is already re-checking it every poll.",
                "video": row,
            }
        if row["state"] == "processing":
            return {
                "outcome": "not_retryable",
                "message": "This video is already being worked on.",
                "video": row,
            }
        monitor = await self.repository.get_monitor(db, user_id, row["monitor_id"])
        if monitor is None:
            return {
                "outcome": "monitor_removed",
                "message": "The channel for this video is no longer monitored, so there are no channel defaults to create the task with.",
                "video": row,
            }
        claimed = await self.repository.reclaim_for_redrive(db, row["id"])
        if claimed is None:
            # The row changed under us — most likely the watcher
            # just settled it. Report its fresh state.
            fresh = await self.repository.get_video_row(db, user_id, video_row_id)
            return {
                "outcome": "already_tasked" if fresh and fresh.get("task_id") else "not_retryable",
                "message": "This video was just picked up — refresh the list to see its new state.",
                "video": fresh or row,
            }
        outcome = await self._create_for_claim(db, monitor, claimed)
        fresh = await self.repository.get_video_row(db, user_id, video_row_id)
        message = {
            "queued": "Task created — it is in the queue now.",
            "skipped": "Still skipped: "
            + ((fresh or {}).get("skip_reason") or "the channel's filters still exclude it")
            + ".",
            "waiting": "Still not downloadable — the watcher will keep re-checking it every poll.",
            "failed": "Creation failed again: "
            + ((fresh or {}).get("last_error") or "unknown error"),
        }.get(outcome, outcome)
        return {"outcome": outcome, "message": message, "video": fresh or claimed}

    async def _settle_waiting(self, db: AsyncSession, monitor: dict, row: dict) -> Optional[str]:
        """One waiting claim on this poll: park it if the wait has
        run past the limit, otherwise re-check availability through
        the normal claim path. Returns the summary bucket, or None
        when another watcher settled the row first."""
        since = _parse_timestamp(row.get("waiting_since")) or _parse_timestamp(
            row.get("created_at")
        )
        if since is not None and (
            datetime.now(timezone.utc) - since
        ).total_seconds() > WAITING_LIMIT_SECONDS:
            await self.repository.update_video(db, row["id"], {
                "state": "parked",
                "waiting_since": None,
                "last_error": (
                    "waiting_expired: still not downloadable "
                    f"{WAITING_LIMIT_SECONDS // 3600} hours after it was "
                    "first seen — re-drive it from the activity list "
                    "if it has since been published"
                ),
            })
            logger.info(
                "Channel monitor parked video %s: waiting limit passed",
                row["video_id"],
            )
            return "failed"
        reclaim_waiting = getattr(self.repository, "reclaim_waiting_video", None)
        if reclaim_waiting is None:
            return None
        claimed = await reclaim_waiting(db, row["id"])
        if claimed is None:
            return None
        return await self._create_for_claim(db, monitor, claimed)

    async def _create_for_claim(self, db: AsyncSession, monitor: dict, claim: dict) -> str:
        entry = _entry_from_row(claim)
        try:
            task_id = await self.task_creator(db, monitor, entry)
        except WaitVideo as wait:
            fields: dict = {
                "state": "waiting",
                "last_error": wait.note,
            }
            if not claim.get("waiting_since"):
                fields["waiting_since"] = datetime.now(timezone.utc)
            await self.repository.update_video(db, claim["id"], fields)
            logger.info(
                "Channel monitor waiting on video %s: %s",
                entry.video_id, wait.note,
            )
            return "waiting"
        except SkipVideo as skip:
            await self.repository.update_video(db, claim["id"], {
                "state": "skipped",
                "skip_reason": skip.reason,
                "waiting_since": None,
                "last_error": None,
            })
            logger.info(
                "Channel monitor skipped video %s: %s", entry.video_id, skip.reason
            )
            return "skipped"
        except Exception as exc:
            attempts = int(claim["attempts"])
            await self.repository.update_video(db, claim["id"], {
                "state": "parked" if attempts >= MAX_ATTEMPTS else "failed",
                "waiting_since": None,
                "last_error": str(exc)[:_ERROR_LIMIT] or exc.__class__.__name__,
            })
            logger.warning(
                "Channel monitor video %s failed attempt %s: %s",
                entry.video_id, attempts, exc,
            )
            return "failed"
        await self.repository.update_video(db, claim["id"], {
            "state": "queued",
            "task_id": task_id,
            "waiting_since": None,
            "last_error": None,
        })
        logger.info(
            "Channel monitor queued video %s as task %s", entry.video_id, task_id
        )
        return "queued"

    async def seed_baseline(
        self,
        db: AsyncSession,
        monitor: dict,
        entries: list[FeedEntry],
        *,
        clip_newest: bool = False,
    ) -> Optional[FeedEntry]:
        """First-run behaviour for a newly added channel: every
        current feed entry is written as a ``baseline`` row — seen,
        never tasked — so enabling a monitor cannot flood the queue
        with the channel's back catalogue. With ``clip_newest``, the
        single newest eligible entry (a Short only when the channel
        includes Shorts) is left un-baselined and returned, for the
        caller to run through :meth:`handle_new_entry`."""
        chosen: Optional[FeedEntry] = None
        if clip_newest:
            for entry in entries:  # feed order is newest first
                if entry.is_short_link and not monitor.get("include_shorts"):
                    continue
                chosen = entry
                break
        for entry in entries:
            if chosen is not None and entry.video_id == chosen.video_id:
                continue
            await self.repository.insert_terminal_video(
                db, _claim_record(monitor, entry),
                state="baseline", skip_reason="baseline",
            )
        return chosen


async def _preflight_duration(url: str, config: Config) -> tuple[dict, float]:
    """The manual route's duration preflight, watcher-shaped:
    metadata problems raise (a normal failed attempt), an over-limit
    or backstop-Short outcome is the creator's skip decision, and a
    video that is not downloadable yet — an upcoming premiere or a
    stream still live — raises :class:`WaitVideo` so its claim waits
    instead of failing."""
    from ..youtube_utils import async_get_youtube_video_info

    timeout_seconds = config.youtube_preflight_timeout_seconds
    metadata = await asyncio.wait_for(
        async_get_youtube_video_info(
            url, deadline=time.monotonic() + timeout_seconds
        ),
        timeout=timeout_seconds,
    )
    live_status = (metadata or {}).get("live_status")
    if live_status in NOT_READY_LIVE_STATUSES:
        note = (
            "This video is an upcoming premiere and cannot be "
            "downloaded yet."
            if live_status == "is_upcoming"
            else "This video is a live stream that has not finished, "
            "so there is no recording to download yet."
        )
        raise WaitVideo(note)
    duration = (metadata or {}).get("duration")
    if isinstance(duration, bool) or not isinstance(duration, (int, float)):
        raise ValueError("Missing video duration")
    if not math.isfinite(duration) or duration <= 0:
        raise ValueError("Invalid video duration")
    return metadata or {}, float(duration)


async def _campaign_brief(
    db: AsyncSession, monitor: dict, duration: float
):
    """The manual create route's campaign handling, watcher-shaped:
    the campaign's stored brief is snapshotted for this task (later
    campaign edits never mutate a task) and validated against this
    video's duration. No campaign on the channel → no brief. A
    campaign deleted since the channel was saved fails the attempt
    with the reason on the row — the watcher's form of the manual
    route's refusal.

    Deliberately not mirrored: the manual route also feeds a
    campaign's example link through Reference Style when the task
    named no style. That downloads and analyses the example per
    task; per upload it would re-analyse the same example forever.
    The channel's own niche default is the watcher's equivalent."""
    campaign_id = monitor.get("campaign_id")
    if not campaign_id:
        return None
    from ..auto_director.content_requirements import validate_content_brief
    from ..repositories.publishing_repository import PublishingRepository

    campaign = await PublishingRepository.get_campaign(
        db, monitor["user_id"], campaign_id
    )
    if campaign is None:
        raise ValueError(
            "campaign_not_found: the campaign saved on this channel "
            "no longer exists"
        )
    brief = campaign.get("brief")
    if not brief:
        return None
    return validate_content_brief(brief, source_duration=duration)


async def create_task_from_channel_video(
    db: AsyncSession,
    monitor: dict,
    entry: FeedEntry,
    *,
    queue_adapter=None,
) -> str:
    """The production task creator: mirror the manual YouTube-URL
    flow (billing check, duration preflight, create, metadata,
    enqueue) with the channel's stored defaults."""
    from ..clip_cleanup import new_task_cleanup_settings
    from ..repositories.task_repository import TaskRepository
    from ..services.billing_service import BillingService
    from ..services.task_service import TaskService
    from ..workers.job_queue import JobQueue

    queue = queue_adapter or JobQueue
    config = get_config()
    user_id = monitor["user_id"]

    billing_service = BillingService(db)
    billing = await billing_service.assert_can_create_task(user_id)

    metadata, duration = await _preflight_duration(entry.url, config)
    if duration > float(billing["max_youtube_duration_seconds"]):
        raise SkipVideo("over_duration_cap")
    if duration <= SHORT_DURATION_SECONDS and not monitor.get("include_shorts"):
        raise SkipVideo("short")
    await billing_service.assert_can_create_task(user_id, duration)

    effective_brief = await _campaign_brief(db, monitor, duration)

    processing_mode = monitor.get("processing_mode") or config.default_processing_mode
    if processing_mode not in {"fast", "balanced", "quality"}:
        processing_mode = config.default_processing_mode
    cleanup_settings = new_task_cleanup_settings({})

    task_service = TaskService(db)
    task_id = await task_service.create_task_with_source(
        user_id=user_id,
        url=entry.url,
        title=entry.title or metadata.get("title") or "YouTube Video",
        caption_template="default",
        processing_mode=processing_mode,
        niche_override=monitor.get("niche_override"),
        music_override=monitor.get("music_override"),
        music_mode=monitor.get("music_mode"),
        template_mode=monitor.get("template_mode"),
        creation_mode=monitor.get("creation_mode"),
        target_clip_count=monitor.get("target_clip_count"),
        source_duration_seconds=math.ceil(duration),
        content_brief=effective_brief,
        campaign_id=monitor.get("campaign_id"),
        brand_kit_id=monitor.get("brand_kit_id"),
        creator_name=metadata.get("uploader") or metadata.get("channel"),
    )

    processing_job_id = f"{task_id}:{secrets.token_hex(16)}"
    try:
        await TaskRepository.save_source_metadata(db, task_id, {
            "processing_job_id": processing_job_id,
            "clip_length": "auto",
            "url": entry.url,
            "source_type": "youtube",
            "output_format": "vertical",
            "add_subtitles": True,
            "channel_monitor_video": entry.video_id,
            **cleanup_settings,
        })
        await queue.enqueue_processing_job(
            "process_video_task",
            processing_mode,
            task_id,
            entry.url,
            "youtube",
            user_id,
            None,
            None,
            None,
            "default",
            processing_mode,
            "vertical",
            True,
            cleanup_settings,
            "auto",
            _job_id=processing_job_id,
        )
    except Exception:
        await task_service.task_repo.update_task_status(
            db, task_id, "error", progress=0,
            progress_message="Unable to queue processing. Please retry this generation.",
            expected_statuses=["queued"],
        )
        raise
    return task_id


async def _production_detector(channel_id: str) -> list[FeedEntry]:
    return await fetch_channel_feed(channel_id)


async def _production_fallback_detector(channel_id: str) -> list[FeedEntry]:
    return await fetch_channel_entries_via_ytdlp(channel_id)


async def _production_task_creator(
    db: AsyncSession, monitor: dict, entry: FeedEntry
) -> str:
    return await create_task_from_channel_video(db, monitor, entry)


def production_service() -> ChannelMonitorService:
    """The watcher as the loop and the add-channel route run it."""
    return ChannelMonitorService(
        ChannelMonitorRepository(), _production_detector, _production_task_creator,
        fallback_detector=_production_fallback_detector,
    )


async def run_channel_monitor_loop(stop_event: asyncio.Event) -> None:
    """Poll every enabled user's channels until ``stop_event`` is
    set. Started from the FastAPI lifespan beside the watch-folder
    loop. Every cycle opens a fresh session and re-reads the
    settings tables, so toggling monitoring in the UI takes effect
    on the next cycle and a restart simply resumes from the durable
    claim records."""
    from ..database import AsyncSessionLocal

    repository = ChannelMonitorRepository()
    service = ChannelMonitorService(
        repository, _production_detector, _production_task_creator,
        fallback_detector=_production_fallback_detector,
    )
    poll = service.config.channel_monitor_poll_seconds
    logger.info("Channel monitor loop started (poll %ss)", poll)
    while not stop_event.is_set():
        try:
            async with AsyncSessionLocal() as db:
                for settings in await repository.list_enabled_settings(db):
                    try:
                        await service.poll_user(db, settings)
                    except Exception:
                        logger.exception(
                            "Channel monitor cycle failed for user %s",
                            settings.get("user_id"),
                        )
        except Exception:
            logger.exception("Channel monitor cycle failed")
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=poll)
        except asyncio.TimeoutError:
            pass
    logger.info("Channel monitor loop stopped")


def start_channel_monitor_watcher() -> tuple[asyncio.Task, asyncio.Event]:
    """Start the loop as a background task. Returns (task, stop_event);
    the caller sets the event and awaits the task on shutdown."""
    stop_event = asyncio.Event()
    task = asyncio.create_task(run_channel_monitor_loop(stop_event))
    return task, stop_event
