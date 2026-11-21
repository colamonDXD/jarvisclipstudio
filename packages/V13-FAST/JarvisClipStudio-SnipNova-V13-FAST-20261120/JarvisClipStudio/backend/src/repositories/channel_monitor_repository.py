"""Persistence for YouTube channel monitoring.

Three tables (migration ``20261111_0001_channel_monitoring``):

- ``channel_monitor_settings``: one row per user with the master
  toggle for the feature.
- ``channel_monitors``: one row per watched channel — the identity
  resolved at add time, the poll health, and the task defaults new
  uploads inherit.
- ``channel_monitor_videos``: the durable claim record for every
  video the watcher has seen. The ``(user_id, video_id)`` unique
  constraint is the duplicate guard: claiming is an
  ``INSERT ... ON CONFLICT DO NOTHING`` and only the caller that
  actually inserted the row may create a task for the video, so a
  restart or a second backend process cannot queue it twice.

The conditional-UPDATE claim idioms are copied from the watch-folder
repository: a failed row is re-claimed only under the attempt cap and
after the backoff, and a claim orphaned in ``processing`` with no
task is only taken over once it is stale. Stage 2 adds the same shape
for two more transitions: a ``waiting`` row (an upcoming premiere or
a stream that has not finished) is reclaimed for its next
availability check without spending an attempt, and a finished row
is reclaimed for a manual re-drive as a fresh episode.
"""

from __future__ import annotations

from datetime import datetime
from uuid import uuid4

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


def _settings_from_row(row) -> dict:
    return {
        "user_id": row.user_id,
        "enabled": bool(row.enabled),
    }


def _monitor_from_row(row) -> dict:
    return {
        "id": row.id,
        "user_id": row.user_id,
        "channel_id": row.channel_id,
        "channel_title": row.channel_title,
        "source_input": row.source_input,
        "enabled": bool(row.enabled),
        "niche_override": row.niche_override,
        "music_mode": row.music_mode,
        "music_override": row.music_override,
        "template_mode": row.template_mode,
        "creation_mode": row.creation_mode,
        "target_clip_count": row.target_clip_count,
        "processing_mode": row.processing_mode,
        "include_shorts": bool(row.include_shorts),
        "max_tasks_per_day": row.max_tasks_per_day,
        "campaign_id": row.campaign_id,
        "brand_kit_id": row.brand_kit_id,
        "last_fallback_at": row.last_fallback_at.isoformat() if row.last_fallback_at else None,
        "last_polled_at": row.last_polled_at.isoformat() if row.last_polled_at else None,
        "last_error": row.last_error,
        "consecutive_failures": row.consecutive_failures,
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
    }


def _video_from_row(row) -> dict:
    return {
        "id": row.id,
        "user_id": row.user_id,
        "monitor_id": row.monitor_id,
        "video_id": row.video_id,
        "video_url": row.video_url,
        "title": row.title,
        "published_at": row.published_at.isoformat() if row.published_at else None,
        "state": row.state,
        "skip_reason": row.skip_reason,
        "task_id": row.task_id,
        "attempts": row.attempts,
        "last_error": row.last_error,
        "waiting_since": row.waiting_since.isoformat() if row.waiting_since else None,
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
    }


def _video_with_channel_from_row(row) -> dict:
    video = _video_from_row(row)
    video["channel_title"] = row.channel_title
    video["channel_id"] = row.channel_id
    return video


_SETTINGS_COLUMNS = "user_id, enabled"

_MONITOR_COLUMNS = (
    "id, user_id, channel_id, channel_title, source_input, enabled,"
    " niche_override, music_mode, music_override, template_mode,"
    " creation_mode, target_clip_count, processing_mode, include_shorts,"
    " max_tasks_per_day, campaign_id, brand_kit_id, last_fallback_at,"
    " last_polled_at, last_error, consecutive_failures,"
    " created_at, updated_at"
)

_VIDEO_COLUMNS = (
    "id, user_id, monitor_id, video_id, video_url, title, published_at,"
    " state, skip_reason, task_id, attempts, last_error, waiting_since,"
    " created_at, updated_at"
)

_MONITOR_WRITABLE = {
    "enabled", "niche_override", "music_mode", "music_override",
    "template_mode", "creation_mode", "target_clip_count",
    "processing_mode", "include_shorts", "max_tasks_per_day",
    "campaign_id", "brand_kit_id", "last_fallback_at",
    "channel_title", "last_polled_at", "last_error", "consecutive_failures",
}

_VIDEO_WRITABLE = {
    "state", "skip_reason", "task_id", "attempts", "last_error",
    "waiting_since",
}


class ChannelMonitorRepository:
    """SQL access for channel-monitor settings, monitors and claims."""

    # -- settings ----------------------------------------------------

    async def get_settings(self, db: AsyncSession, user_id: str) -> dict | None:
        row = (await db.execute(text(f"""
            SELECT {_SETTINGS_COLUMNS} FROM channel_monitor_settings
            WHERE user_id = :user_id
        """), {"user_id": user_id})).first()
        return _settings_from_row(row) if row is not None else None

    async def upsert_settings(self, db: AsyncSession, user_id: str, fields: dict) -> dict:
        await db.execute(text("""
            INSERT INTO channel_monitor_settings (user_id, enabled)
            VALUES (:user_id, :enabled)
            ON CONFLICT (user_id) DO UPDATE SET
                enabled = EXCLUDED.enabled,
                updated_at = CURRENT_TIMESTAMP
        """), {
            "user_id": user_id,
            "enabled": bool(fields.get("enabled", False)),
        })
        await db.commit()
        settings = await self.get_settings(db, user_id)
        assert settings is not None  # the upsert above just wrote it
        return settings

    async def list_enabled_settings(self, db: AsyncSession) -> list[dict]:
        rows = (await db.execute(text(f"""
            SELECT {_SETTINGS_COLUMNS} FROM channel_monitor_settings
            WHERE enabled = true ORDER BY user_id
        """))).all()
        return [_settings_from_row(row) for row in rows]

    # -- monitors ------------------------------------------------------

    async def create_monitor(self, db: AsyncSession, record: dict) -> dict | None:
        """Insert one monitor row, or return None when this user
        already monitors that channel."""
        row = (await db.execute(text(f"""
            INSERT INTO channel_monitors
                (id, user_id, channel_id, channel_title, source_input,
                 enabled, niche_override, music_mode, music_override,
                 template_mode, creation_mode, target_clip_count,
                 processing_mode, include_shorts, max_tasks_per_day,
                 campaign_id, brand_kit_id)
            VALUES (:id, :user_id, :channel_id, :channel_title, :source_input,
                    :enabled, :niche_override, :music_mode, :music_override,
                    :template_mode, :creation_mode, :target_clip_count,
                    :processing_mode, :include_shorts, :max_tasks_per_day,
                    :campaign_id, :brand_kit_id)
            ON CONFLICT (user_id, channel_id) DO NOTHING
            RETURNING {_MONITOR_COLUMNS}
        """), {
            "id": str(uuid4()),
            "user_id": record["user_id"],
            "channel_id": record["channel_id"],
            "channel_title": record.get("channel_title"),
            "source_input": record["source_input"],
            "enabled": bool(record.get("enabled", True)),
            "niche_override": record.get("niche_override"),
            "music_mode": record.get("music_mode"),
            "music_override": record.get("music_override"),
            "template_mode": record.get("template_mode") or "plain",
            "creation_mode": record.get("creation_mode") or "full_auto",
            "target_clip_count": record.get("target_clip_count"),
            "processing_mode": record.get("processing_mode"),
            "include_shorts": bool(record.get("include_shorts", False)),
            "max_tasks_per_day": record.get("max_tasks_per_day", 3),
            "campaign_id": record.get("campaign_id"),
            "brand_kit_id": record.get("brand_kit_id"),
        })).first()
        await db.commit()
        return _monitor_from_row(row) if row is not None else None

    async def get_monitor(self, db: AsyncSession, user_id: str, monitor_id: str) -> dict | None:
        row = (await db.execute(text(f"""
            SELECT {_MONITOR_COLUMNS} FROM channel_monitors
            WHERE user_id = :user_id AND id = :id
        """), {"user_id": user_id, "id": monitor_id})).first()
        return _monitor_from_row(row) if row is not None else None

    async def get_monitor_by_channel(
        self, db: AsyncSession, user_id: str, channel_id: str
    ) -> dict | None:
        row = (await db.execute(text(f"""
            SELECT {_MONITOR_COLUMNS} FROM channel_monitors
            WHERE user_id = :user_id AND channel_id = :channel_id
        """), {"user_id": user_id, "channel_id": channel_id})).first()
        return _monitor_from_row(row) if row is not None else None

    async def list_monitors(self, db: AsyncSession, user_id: str) -> list[dict]:
        rows = (await db.execute(text(f"""
            SELECT {_MONITOR_COLUMNS} FROM channel_monitors
            WHERE user_id = :user_id ORDER BY created_at ASC
        """), {"user_id": user_id})).all()
        return [_monitor_from_row(row) for row in rows]

    async def list_enabled_monitors(self, db: AsyncSession, user_id: str) -> list[dict]:
        rows = (await db.execute(text(f"""
            SELECT {_MONITOR_COLUMNS} FROM channel_monitors
            WHERE user_id = :user_id AND enabled = true ORDER BY created_at ASC
        """), {"user_id": user_id})).all()
        return [_monitor_from_row(row) for row in rows]

    async def update_monitor(self, db: AsyncSession, monitor_id: str, fields: dict) -> None:
        updates = {k: v for k, v in fields.items() if k in _MONITOR_WRITABLE}
        if not updates:
            return
        assignments = ", ".join(f"{key} = :{key}" for key in updates)
        await db.execute(text(f"""
            UPDATE channel_monitors
            SET {assignments}, updated_at = CURRENT_TIMESTAMP
            WHERE id = :id
        """), {**updates, "id": monitor_id})
        await db.commit()

    async def delete_monitor(self, db: AsyncSession, user_id: str, monitor_id: str) -> bool:
        result = await db.execute(text("""
            DELETE FROM channel_monitors
            WHERE user_id = :user_id AND id = :id
        """), {"user_id": user_id, "id": monitor_id})
        await db.commit()
        return bool(result.rowcount)

    # -- video claims --------------------------------------------------

    async def get_video(self, db: AsyncSession, user_id: str, video_id: str) -> dict | None:
        row = (await db.execute(text(f"""
            SELECT {_VIDEO_COLUMNS} FROM channel_monitor_videos
            WHERE user_id = :user_id AND video_id = :video_id
        """), {"user_id": user_id, "video_id": video_id})).first()
        return _video_from_row(row) if row is not None else None

    async def claim_video(self, db: AsyncSession, record: dict) -> dict | None:
        """Insert a ``processing`` claim row, or return None when the
        video is already known to this user (seen before, claimed by
        another watcher, or baselined)."""
        row = (await db.execute(text(f"""
            INSERT INTO channel_monitor_videos
                (id, user_id, monitor_id, video_id, video_url, title,
                 published_at, state, attempts)
            VALUES (:id, :user_id, :monitor_id, :video_id, :video_url,
                    :title, :published_at, 'processing', 1)
            ON CONFLICT (user_id, video_id) DO NOTHING
            RETURNING {_VIDEO_COLUMNS}
        """), {
            "id": str(uuid4()),
            "user_id": record["user_id"],
            "monitor_id": record["monitor_id"],
            "video_id": record["video_id"],
            "video_url": record["video_url"],
            "title": record.get("title"),
            "published_at": record.get("published_at"),
        })).first()
        await db.commit()
        return _video_from_row(row) if row is not None else None

    async def insert_terminal_video(
        self, db: AsyncSession, record: dict, *, state: str, skip_reason: str | None
    ) -> dict | None:
        """Insert a row that is finished on arrival — a baseline
        sighting or a skip decided before any task work (Shorts
        filter, daily cap). Same conflict guard as a claim."""
        row = (await db.execute(text(f"""
            INSERT INTO channel_monitor_videos
                (id, user_id, monitor_id, video_id, video_url, title,
                 published_at, state, skip_reason, attempts)
            VALUES (:id, :user_id, :monitor_id, :video_id, :video_url,
                    :title, :published_at, :state, :skip_reason, 0)
            ON CONFLICT (user_id, video_id) DO NOTHING
            RETURNING {_VIDEO_COLUMNS}
        """), {
            "id": str(uuid4()),
            "user_id": record["user_id"],
            "monitor_id": record["monitor_id"],
            "video_id": record["video_id"],
            "video_url": record["video_url"],
            "title": record.get("title"),
            "published_at": record.get("published_at"),
            "state": state,
            "skip_reason": skip_reason,
        })).first()
        await db.commit()
        return _video_from_row(row) if row is not None else None

    async def reclaim_failed_video(
        self,
        db: AsyncSession,
        video_row_id: str,
        *,
        max_attempts: int,
        backoff_seconds: int,
    ) -> dict | None:
        """Move a ``failed`` row back to ``processing`` for one more
        attempt, but only under the attempt cap and past the backoff.
        The conditional UPDATE is the claim."""
        row = (await db.execute(text(f"""
            UPDATE channel_monitor_videos
            SET state = 'processing', attempts = attempts + 1,
                updated_at = CURRENT_TIMESTAMP
            WHERE id = :id AND state = 'failed'
                AND attempts < :max_attempts
                AND updated_at <= CURRENT_TIMESTAMP
                    - (:backoff_seconds * INTERVAL '1 second')
            RETURNING {_VIDEO_COLUMNS}
        """), {
            "id": video_row_id,
            "max_attempts": max_attempts,
            "backoff_seconds": backoff_seconds,
        })).first()
        await db.commit()
        return _video_from_row(row) if row is not None else None

    async def reclaim_stale_processing_video(
        self,
        db: AsyncSession,
        video_row_id: str,
        *,
        stale_seconds: int,
    ) -> dict | None:
        """Take over a claim orphaned in ``processing`` with no task
        (the watcher died between the claim insert and task
        creation). Only rows untouched for longer than
        ``stale_seconds`` qualify."""
        row = (await db.execute(text(f"""
            UPDATE channel_monitor_videos
            SET attempts = attempts + 1,
                updated_at = CURRENT_TIMESTAMP
            WHERE id = :id AND state = 'processing'
                AND task_id IS NULL
                AND updated_at <= CURRENT_TIMESTAMP
                    - (:stale_seconds * INTERVAL '1 second')
            RETURNING {_VIDEO_COLUMNS}
        """), {
            "id": video_row_id,
            "stale_seconds": stale_seconds,
        })).first()
        await db.commit()
        return _video_from_row(row) if row is not None else None

    async def list_retryable_failed_videos(
        self,
        db: AsyncSession,
        monitor_id: str,
        *,
        max_attempts: int,
        backoff_seconds: int,
        limit: int = 15,
    ) -> list[dict]:
        rows = (await db.execute(text(f"""
            SELECT {_VIDEO_COLUMNS} FROM channel_monitor_videos
            WHERE monitor_id = :monitor_id AND state = 'failed'
                AND attempts < :max_attempts
                AND updated_at <= CURRENT_TIMESTAMP
                    - (:backoff_seconds * INTERVAL '1 second')
            ORDER BY updated_at ASC LIMIT :limit
        """), {
            "monitor_id": monitor_id,
            "max_attempts": max_attempts,
            "backoff_seconds": backoff_seconds,
            "limit": limit,
        })).all()
        return [_video_from_row(row) for row in rows]

    async def list_stale_processing_videos(
        self,
        db: AsyncSession,
        monitor_id: str,
        *,
        stale_seconds: int,
        limit: int = 100,
    ) -> list[dict]:
        """This monitor's claims stuck in ``processing`` with no
        task, untouched for longer than ``stale_seconds`` (orphan
        sweep input). Read-only: the conditional UPDATE in
        :meth:`reclaim_stale_processing_video` settles a row."""
        rows = (await db.execute(text(f"""
            SELECT {_VIDEO_COLUMNS} FROM channel_monitor_videos
            WHERE monitor_id = :monitor_id AND state = 'processing'
                AND task_id IS NULL
                AND updated_at <= CURRENT_TIMESTAMP
                    - (:stale_seconds * INTERVAL '1 second')
            ORDER BY updated_at ASC LIMIT :limit
        """), {
            "monitor_id": monitor_id,
            "stale_seconds": stale_seconds,
            "limit": limit,
        })).all()
        return [_video_from_row(row) for row in rows]

    async def get_video_row(
        self, db: AsyncSession, user_id: str, video_row_id: str
    ) -> dict | None:
        """One claim row by its own id, scoped to its owner — the
        manual re-drive looks rows up this way, where
        :meth:`get_video` keys on the YouTube video id."""
        row = (await db.execute(text(f"""
            SELECT {_VIDEO_COLUMNS} FROM channel_monitor_videos
            WHERE user_id = :user_id AND id = :id
        """), {"user_id": user_id, "id": video_row_id})).first()
        return _video_from_row(row) if row is not None else None

    async def list_waiting_videos(
        self,
        db: AsyncSession,
        monitor_id: str,
        *,
        limit: int = 15,
    ) -> list[dict]:
        """This monitor's claims in the ``waiting`` state — videos
        whose preflight said upcoming or still live. They are
        re-checked on the poll cadence, oldest wait first."""
        rows = (await db.execute(text(f"""
            SELECT {_VIDEO_COLUMNS} FROM channel_monitor_videos
            WHERE monitor_id = :monitor_id AND state = 'waiting'
            ORDER BY waiting_since ASC NULLS FIRST, created_at ASC
            LIMIT :limit
        """), {
            "monitor_id": monitor_id,
            "limit": limit,
        })).all()
        return [_video_from_row(row) for row in rows]

    async def reclaim_waiting_video(
        self,
        db: AsyncSession,
        video_row_id: str,
    ) -> dict | None:
        """Move a ``waiting`` claim back to ``processing`` for one
        more availability check. Deliberately does NOT touch
        ``attempts``: waiting for a premiere is not a failure, so a
        wait never spends any of the bounded retry budget."""
        row = (await db.execute(text(f"""
            UPDATE channel_monitor_videos
            SET state = 'processing',
                updated_at = CURRENT_TIMESTAMP
            WHERE id = :id AND state = 'waiting'
            RETURNING {_VIDEO_COLUMNS}
        """), {
            "id": video_row_id,
        })).first()
        await db.commit()
        return _video_from_row(row) if row is not None else None

    async def reclaim_for_redrive(
        self,
        db: AsyncSession,
        video_row_id: str,
    ) -> dict | None:
        """Take over a finished claim for a manual re-drive: a
        ``parked``, ``skipped`` or ``failed`` row with no task goes
        back to ``processing`` as a fresh episode (attempts restart
        at 1, any wait stamp clears). The conditional UPDATE is the
        claim — a row the watcher just queued, or one already
        tasked, does not match and the re-drive is a no-op."""
        row = (await db.execute(text(f"""
            UPDATE channel_monitor_videos
            SET state = 'processing', attempts = 1,
                waiting_since = NULL, last_error = NULL,
                skip_reason = NULL,
                updated_at = CURRENT_TIMESTAMP
            WHERE id = :id AND state IN ('parked', 'skipped', 'failed')
                AND task_id IS NULL
            RETURNING {_VIDEO_COLUMNS}
        """), {
            "id": video_row_id,
        })).first()
        await db.commit()
        return _video_from_row(row) if row is not None else None

    async def update_video(self, db: AsyncSession, video_row_id: str, fields: dict) -> None:
        updates = {k: v for k, v in fields.items() if k in _VIDEO_WRITABLE}
        if not updates:
            return
        assignments = ", ".join(f"{key} = :{key}" for key in updates)
        await db.execute(text(f"""
            UPDATE channel_monitor_videos
            SET {assignments}, updated_at = CURRENT_TIMESTAMP
            WHERE id = :id
        """), {**updates, "id": video_row_id})
        await db.commit()

    async def list_videos(self, db: AsyncSession, user_id: str, limit: int = 50) -> list[dict]:
        """Recent activity across the user's channels, newest first,
        with the channel's title attached (NULL once its monitor has
        been removed)."""
        rows = (await db.execute(text(f"""
            SELECT {", ".join("v." + c.strip() for c in _VIDEO_COLUMNS.split(","))},
                   m.channel_title AS channel_title,
                   m.channel_id AS channel_id
            FROM channel_monitor_videos v
            LEFT JOIN channel_monitors m ON m.id = v.monitor_id
            WHERE v.user_id = :user_id
            ORDER BY v.created_at DESC LIMIT :limit
        """), {"user_id": user_id, "limit": limit})).all()
        return [_video_with_channel_from_row(row) for row in rows]

    async def count_tasks_created_since(
        self, db: AsyncSession, monitor_id: str, since: datetime
    ) -> int:
        """Tasks this monitor has actually created since ``since``
        (the daily-cap counter: claim rows that carry a task)."""
        value = (await db.execute(text("""
            SELECT COUNT(*) FROM channel_monitor_videos
            WHERE monitor_id = :monitor_id AND task_id IS NOT NULL
                AND created_at >= :since
        """), {"monitor_id": monitor_id, "since": since})).scalar()
        return int(value or 0)

    async def park_videos_for_missing_monitors(self, db: AsyncSession, user_id: str) -> int:
        """Settle claims whose monitor was deleted: a claim can never
        be re-driven without its channel's defaults, so it parks
        with the reason on the row instead of spinning forever."""
        result = await db.execute(text("""
            UPDATE channel_monitor_videos
            SET state = 'parked',
                last_error = 'monitor_removed: the channel is no longer monitored',
                updated_at = CURRENT_TIMESTAMP
            WHERE user_id = :user_id
                AND state IN ('processing', 'failed', 'waiting')
                AND monitor_id NOT IN (
                    SELECT id FROM channel_monitors WHERE user_id = :user_id
                )
        """), {"user_id": user_id})
        await db.commit()
        return int(result.rowcount or 0)
