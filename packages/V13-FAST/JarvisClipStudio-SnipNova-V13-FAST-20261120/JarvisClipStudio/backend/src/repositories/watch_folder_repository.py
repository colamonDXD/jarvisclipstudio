"""Persistence for the watch-folder autopilot.

Two tables (migration ``20261109_0003_watch_folder``):

- ``watch_folder_settings``: one row per user with the autopilot
  toggle, the folder override and the task defaults for dropped files.
- ``watch_folder_files``: the durable claim record for every file the
  watcher has picked up. The ``(user_id, fingerprint)`` unique
  constraint is the duplicate guard: claiming is an
  ``INSERT ... ON CONFLICT DO NOTHING`` and only the caller that
  actually inserted the row may create a task for the file, so a
  restart or a second backend process cannot queue it twice.

The upload-session insert lives here too: a watched file becomes a
normal completed local upload (a ``media_upload_sessions`` row plus
the bytes at ``local_upload_path``), which is what lets it flow
through the exact same task pipeline as a browser upload.
"""

from __future__ import annotations

from uuid import uuid4

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


def _settings_from_row(row) -> dict:
    return {
        "user_id": row.user_id,
        "enabled": bool(row.enabled),
        "folder_path": row.folder_path,
        "niche_override": row.niche_override,
        "music_override": row.music_override,
        "music_mode": row.music_mode,
        "target_clip_count": row.target_clip_count,
        "processing_mode": row.processing_mode,
        "recipe_id": row.recipe_id,
    }


def _file_from_row(row) -> dict:
    return {
        "id": row.id,
        "user_id": row.user_id,
        "file_path": row.file_path,
        "file_name": row.file_name,
        "fingerprint": row.fingerprint,
        "size_bytes": row.size_bytes,
        "mtime_ns": row.mtime_ns,
        "state": row.state,
        "task_id": row.task_id,
        "upload_path": row.upload_path,
        "attempts": row.attempts,
        "last_error": row.last_error,
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
    }


_SETTINGS_COLUMNS = (
    "user_id, enabled, folder_path, niche_override, music_override,"
    " music_mode, target_clip_count, processing_mode, recipe_id"
)

_FILE_COLUMNS = (
    "id, user_id, file_path, file_name, fingerprint, size_bytes,"
    " mtime_ns, state, task_id, upload_path, attempts, last_error,"
    " created_at, updated_at"
)


class WatchFolderRepository:
    """SQL access for watch-folder settings and claimed files."""

    async def get_settings(self, db: AsyncSession, user_id: str) -> dict | None:
        row = (await db.execute(text(f"""
            SELECT {_SETTINGS_COLUMNS} FROM watch_folder_settings
            WHERE user_id = :user_id
        """), {"user_id": user_id})).first()
        return _settings_from_row(row) if row is not None else None

    async def upsert_settings(self, db: AsyncSession, user_id: str, fields: dict) -> dict:
        await db.execute(text("""
            INSERT INTO watch_folder_settings
                (user_id, enabled, folder_path, niche_override,
                 music_override, music_mode, target_clip_count, processing_mode,
                 recipe_id)
            VALUES (:user_id, :enabled, :folder_path, :niche_override,
                    :music_override, :music_mode, :target_clip_count, :processing_mode,
                    :recipe_id)
            ON CONFLICT (user_id) DO UPDATE SET
                enabled = EXCLUDED.enabled,
                folder_path = EXCLUDED.folder_path,
                niche_override = EXCLUDED.niche_override,
                music_override = EXCLUDED.music_override,
                music_mode = EXCLUDED.music_mode,
                target_clip_count = EXCLUDED.target_clip_count,
                processing_mode = EXCLUDED.processing_mode,
                recipe_id = EXCLUDED.recipe_id,
                updated_at = CURRENT_TIMESTAMP
        """), {
            "user_id": user_id,
            "enabled": bool(fields.get("enabled", False)),
            "folder_path": fields.get("folder_path"),
            "niche_override": fields.get("niche_override"),
            "music_override": fields.get("music_override"),
            "music_mode": fields.get("music_mode"),
            "target_clip_count": fields.get("target_clip_count"),
            "processing_mode": fields.get("processing_mode"),
            "recipe_id": fields.get("recipe_id"),
        })
        await db.commit()
        settings = await self.get_settings(db, user_id)
        assert settings is not None  # the upsert above just wrote it
        return settings

    async def list_enabled_settings(self, db: AsyncSession) -> list[dict]:
        rows = (await db.execute(text(f"""
            SELECT {_SETTINGS_COLUMNS} FROM watch_folder_settings
            WHERE enabled = true ORDER BY user_id
        """))).all()
        return [_settings_from_row(row) for row in rows]

    async def get_file_by_fingerprint(
        self, db: AsyncSession, user_id: str, fingerprint: str
    ) -> dict | None:
        row = (await db.execute(text(f"""
            SELECT {_FILE_COLUMNS} FROM watch_folder_files
            WHERE user_id = :user_id AND fingerprint = :fingerprint
        """), {"user_id": user_id, "fingerprint": fingerprint})).first()
        return _file_from_row(row) if row is not None else None

    async def claim_file(self, db: AsyncSession, record: dict) -> dict | None:
        """Insert a ``processing`` claim row, or return None when the
        fingerprint is already claimed (duplicate, restart, or a second
        watcher process won the race)."""
        row = (await db.execute(text(f"""
            INSERT INTO watch_folder_files
                (id, user_id, file_path, file_name, fingerprint,
                 size_bytes, mtime_ns, state, attempts)
            VALUES (:id, :user_id, :file_path, :file_name, :fingerprint,
                    :size_bytes, :mtime_ns, 'processing', 1)
            ON CONFLICT (user_id, fingerprint) DO NOTHING
            RETURNING {_FILE_COLUMNS}
        """), {
            "id": str(uuid4()),
            "user_id": record["user_id"],
            "file_path": record["file_path"],
            "file_name": record["file_name"],
            "fingerprint": record["fingerprint"],
            "size_bytes": record["size_bytes"],
            "mtime_ns": record["mtime_ns"],
        })).first()
        await db.commit()
        return _file_from_row(row) if row is not None else None

    async def reclaim_failed_file(
        self,
        db: AsyncSession,
        file_id: str,
        *,
        max_attempts: int,
        backoff_seconds: int,
    ) -> dict | None:
        """Move a ``failed`` row back to ``processing`` for one more
        attempt, but only when it is under the attempt cap and its last
        attempt is older than the backoff. The conditional UPDATE is
        the claim: when it returns no row, another watcher already took
        it or it is no longer retryable."""
        row = (await db.execute(text(f"""
            UPDATE watch_folder_files
            SET state = 'processing', attempts = attempts + 1,
                updated_at = CURRENT_TIMESTAMP
            WHERE id = :id AND state = 'failed'
                AND attempts < :max_attempts
                AND updated_at <= CURRENT_TIMESTAMP
                    - (:backoff_seconds * INTERVAL '1 second')
            RETURNING {_FILE_COLUMNS}
        """), {
            "id": file_id,
            "max_attempts": max_attempts,
            "backoff_seconds": backoff_seconds,
        })).first()
        await db.commit()
        return _file_from_row(row) if row is not None else None

    async def reclaim_stale_processing_file(
        self,
        db: AsyncSession,
        file_id: str,
        *,
        stale_seconds: int,
    ) -> dict | None:
        """Take over a claim orphaned in ``processing`` with no task
        (the watcher died between the claim insert and task creation —
        F-D1). Only rows untouched for longer than ``stale_seconds``
        qualify, so a live watcher's in-flight claim is never stolen.
        The conditional UPDATE is the claim: when it returns no row,
        another watcher recovered it first or it is not stale."""
        row = (await db.execute(text(f"""
            UPDATE watch_folder_files
            SET attempts = attempts + 1,
                updated_at = CURRENT_TIMESTAMP
            WHERE id = :id AND state = 'processing'
                AND task_id IS NULL
                AND updated_at <= CURRENT_TIMESTAMP
                    - (:stale_seconds * INTERVAL '1 second')
            RETURNING {_FILE_COLUMNS}
        """), {
            "id": file_id,
            "stale_seconds": stale_seconds,
        })).first()
        await db.commit()
        return _file_from_row(row) if row is not None else None

    async def list_stale_processing_files(
        self,
        db: AsyncSession,
        user_id: str,
        *,
        stale_seconds: int,
        limit: int = 100,
    ) -> list[dict]:
        """This user's claims stuck in ``processing`` with no task,
        untouched for longer than ``stale_seconds`` (F-D1 sweep input).
        Read-only: the conditional UPDATE in
        :meth:`reclaim_stale_processing_file` is what settles a row."""
        rows = (await db.execute(text(f"""
            SELECT {_FILE_COLUMNS} FROM watch_folder_files
            WHERE user_id = :user_id AND state = 'processing'
                AND task_id IS NULL
                AND updated_at <= CURRENT_TIMESTAMP
                    - (:stale_seconds * INTERVAL '1 second')
            ORDER BY updated_at ASC LIMIT :limit
        """), {
            "user_id": user_id,
            "stale_seconds": stale_seconds,
            "limit": limit,
        })).all()
        return [_file_from_row(row) for row in rows]

    async def update_file(self, db: AsyncSession, file_id: str, fields: dict) -> None:
        allowed = {"state", "task_id", "upload_path", "attempts", "last_error"}
        updates = {key: value for key, value in fields.items() if key in allowed}
        if not updates:
            return
        assignments = ", ".join(f"{key} = :{key}" for key in updates)
        await db.execute(text(f"""
            UPDATE watch_folder_files
            SET {assignments}, updated_at = CURRENT_TIMESTAMP
            WHERE id = :id
        """), {**updates, "id": file_id})
        await db.commit()

    async def list_files(self, db: AsyncSession, user_id: str, limit: int = 50) -> list[dict]:
        rows = (await db.execute(text(f"""
            SELECT {_FILE_COLUMNS} FROM watch_folder_files
            WHERE user_id = :user_id
            ORDER BY created_at DESC LIMIT :limit
        """), {"user_id": user_id, "limit": limit})).all()
        return [_file_from_row(row) for row in rows]

    async def insert_completed_upload_session(
        self, db: AsyncSession, *, user_id: str, video_path: str, size: int
    ) -> str:
        """Register an ingested watch file as a completed local upload
        so ``get_stored_upload`` resolves it like a browser upload."""
        session_id = str(uuid4())
        await db.execute(text("""
            INSERT INTO media_upload_sessions
                (id, user_id, upload_id, video_path, size, state)
            VALUES (:id, :user_id, :upload_id, :video_path, :size, 'completed')
        """), {
            "id": session_id,
            "user_id": user_id,
            "upload_id": str(uuid4()),
            "video_path": video_path,
            "size": size,
        })
        await db.commit()
        return session_id
