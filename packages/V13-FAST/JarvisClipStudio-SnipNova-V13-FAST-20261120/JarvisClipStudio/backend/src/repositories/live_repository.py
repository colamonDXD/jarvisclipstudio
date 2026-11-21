"""Persistence for live clipping (migration ``20261118_0001_live_clipping``).

``live_sessions``: one row per session, claimed by the live worker with a
conditional UPDATE (only one worker can move a row out of ``requested``).
``live_clips``: one row per detected moment, keyed by ``(session_id, n)``
for moments that got a window; rejected flags without a window have no n.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from uuid import uuid4

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

ACTIVE_STATUSES = ("requested", "starting", "recording")
STALE_HEARTBEAT_SECONDS = 120


def _ts(value: Optional[float]) -> Optional[datetime]:
    return None if value is None else datetime.fromtimestamp(float(value), tz=timezone.utc)


def _iso(value) -> Optional[str]:
    return value.isoformat() if value is not None else None


def _json(value, default):
    if value is None:
        return default
    if isinstance(value, (dict, list)):
        return value
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return default


def session_from_row(row) -> Dict[str, Any]:
    return {
        "id": row.id, "user_id": row.user_id, "url": row.url, "status": row.status,
        "settings": _json(row.settings, {}), "stop_requested": bool(row.stop_requested),
        "recording": bool(row.recording), "buffered_seconds": float(row.buffered_seconds or 0),
        "restarts": int(row.restarts or 0), "last_error": row.last_error,
        "progress_message": getattr(row, "progress_message", None),
        "mode": (_json(row.settings, {}) or {}).get("mode") or "live",
        "started_at": _iso(row.started_at), "stopped_at": _iso(row.stopped_at),
        "heartbeat_at": _iso(row.heartbeat_at), "created_at": _iso(row.created_at),
    }


def clip_from_row(row) -> Dict[str, Any]:
    return {
        "id": row.id, "session_id": row.session_id, "n": row.n, "status": row.status, "reason": row.reason,
        "onset_at": _iso(row.onset_at), "start_at": _iso(row.start_at), "end_at": _iso(row.end_at),
        "signals": _json(row.signals, []), "strength": row.strength, "talk_share": row.talk_share,
        "has_preview": bool(row.preview_path), "has_clip": bool(row.clip_path),
        "duration": row.duration, "render_seconds": row.render_seconds, "created_at": _iso(row.created_at),
    }


class LiveRepository:
    # -- sessions (API) ----------------------------------------------------
    async def create_session(self, db: AsyncSession, user_id: str, url: str, settings: Dict[str, Any]) -> Dict[str, Any]:
        sid = str(uuid4())
        await db.execute(text(
            "INSERT INTO live_sessions (id, user_id, url, status, settings) "
            "VALUES (:id, :user_id, :url, 'requested', CAST(:settings AS JSONB))"),
            {"id": sid, "user_id": user_id, "url": url, "settings": json.dumps(settings)})
        await db.commit()
        return await self.get_session(db, sid)

    async def get_session(self, db: AsyncSession, session_id: str, user_id: Optional[str] = None) -> Optional[Dict[str, Any]]:
        sql = "SELECT * FROM live_sessions WHERE id = :id"
        params: Dict[str, Any] = {"id": session_id}
        if user_id is not None:
            sql += " AND user_id = :user_id"
            params["user_id"] = user_id
        row = (await db.execute(text(sql), params)).first()
        return session_from_row(row) if row else None

    async def list_sessions(self, db: AsyncSession, user_id: str, limit: int = 20) -> List[Dict[str, Any]]:
        rows = (await db.execute(text(
            "SELECT * FROM live_sessions WHERE user_id = :user_id ORDER BY created_at DESC LIMIT :limit"),
            {"user_id": user_id, "limit": limit})).fetchall()
        return [session_from_row(r) for r in rows]

    async def count_active(self, db: AsyncSession, user_id: Optional[str] = None) -> int:
        sql = "SELECT COUNT(*) FROM live_sessions WHERE status IN ('requested', 'starting', 'recording')"
        params: Dict[str, Any] = {}
        if user_id is not None:
            sql += " AND user_id = :user_id"
            params["user_id"] = user_id
        return int((await db.execute(text(sql), params)).scalar() or 0)

    async def request_stop(self, db: AsyncSession, session_id: str, user_id: str) -> bool:
        """Ask the worker to stop; a session nobody claimed yet stops at once."""
        result = await db.execute(text(
            "UPDATE live_sessions SET stop_requested = TRUE, "
            "status = CASE WHEN status = 'requested' THEN 'stopped' ELSE status END, "
            "stopped_at = CASE WHEN status = 'requested' THEN CURRENT_TIMESTAMP ELSE stopped_at END, "
            "updated_at = CURRENT_TIMESTAMP "
            "WHERE id = :id AND user_id = :user_id AND status IN ('requested', 'starting', 'recording')"),
            {"id": session_id, "user_id": user_id})
        await db.commit()
        return (result.rowcount or 0) > 0

    async def delete_session(self, db: AsyncSession, session_id: str, user_id: str) -> bool:
        result = await db.execute(text(
            "DELETE FROM live_sessions WHERE id = :id AND user_id = :user_id "
            "AND status NOT IN ('requested', 'starting', 'recording')"),
            {"id": session_id, "user_id": user_id})
        await db.commit()
        return (result.rowcount or 0) > 0

    # -- clips (API) -------------------------------------------------------
    async def list_clips(self, db: AsyncSession, session_id: str) -> List[Dict[str, Any]]:
        rows = (await db.execute(text(
            "SELECT * FROM live_clips WHERE session_id = :sid ORDER BY created_at"), {"sid": session_id})).fetchall()
        return [clip_from_row(r) for r in rows]

    async def get_clip_file(self, db: AsyncSession, clip_id: str, user_id: str, kind: str) -> Optional[str]:
        column = "clip_path" if kind == "clip" else "preview_path"
        row = (await db.execute(text(
            f"SELECT c.{column} AS path FROM live_clips c JOIN live_sessions s ON s.id = c.session_id "
            "WHERE c.id = :id AND s.user_id = :user_id"), {"id": clip_id, "user_id": user_id})).first()
        return row.path if row else None

    # -- worker ------------------------------------------------------------
    async def claim_requested(self, db: AsyncSession, worker_id: str) -> Optional[Dict[str, Any]]:
        row = (await db.execute(text(
            "UPDATE live_sessions SET status = 'starting', worker_id = :worker, heartbeat_at = CURRENT_TIMESTAMP, "
            "started_at = CURRENT_TIMESTAMP, updated_at = CURRENT_TIMESTAMP "
            "WHERE id = (SELECT id FROM live_sessions WHERE status = 'requested' AND NOT stop_requested "
            "ORDER BY created_at LIMIT 1 FOR UPDATE SKIP LOCKED) RETURNING *"), {"worker": worker_id})).first()
        await db.commit()
        return session_from_row(row) if row else None

    async def fail_stale(self, db: AsyncSession) -> int:
        """Sessions whose worker stopped sending heartbeats (crash, restart)."""
        result = await db.execute(text(
            "UPDATE live_sessions SET status = 'error', recording = FALSE, stopped_at = CURRENT_TIMESTAMP, "
            "last_error = COALESCE(last_error, 'the live worker stopped (restart or crash)'), "
            "updated_at = CURRENT_TIMESTAMP "
            "WHERE status IN ('starting', 'recording') "
            f"AND heartbeat_at < CURRENT_TIMESTAMP - INTERVAL '{STALE_HEARTBEAT_SECONDS} seconds'"))
        await db.commit()
        return result.rowcount or 0

    async def heartbeat(self, db: AsyncSession, session_id: str, status: Dict[str, Any]) -> bool:
        """Store recorder health; returns True when the user asked to stop."""
        row = (await db.execute(text(
            "UPDATE live_sessions SET heartbeat_at = CURRENT_TIMESTAMP, updated_at = CURRENT_TIMESTAMP, "
            "status = CASE WHEN status = 'starting' AND :recording THEN 'recording' ELSE status END, "
            "recording = :recording, buffered_seconds = :buffered, restarts = :restarts, last_error = :err, "
            "progress_message = COALESCE(:progress, progress_message) "
            "WHERE id = :id RETURNING stop_requested"),
            {"id": session_id, "recording": bool(status.get("recording")),
             "buffered": float(status.get("buffered_seconds") or 0), "restarts": int(status.get("restarts") or 0),
             "err": status.get("last_error"), "progress": status.get("progress")})).first()
        await db.commit()
        return bool(row.stop_requested) if row else True

    async def is_stop_requested(self, db: AsyncSession, session_id: str) -> bool:
        row = (await db.execute(text("SELECT stop_requested FROM live_sessions WHERE id = :id"),
                                {"id": session_id})).first()
        return bool(row.stop_requested) if row else True

    async def finish_session(self, db: AsyncSession, session_id: str, status: str, error: Optional[str] = None) -> None:
        await db.execute(text(
            "UPDATE live_sessions SET status = :status, recording = FALSE, stopped_at = CURRENT_TIMESTAMP, "
            "last_error = COALESCE(:err, last_error), updated_at = CURRENT_TIMESTAMP WHERE id = :id"),
            {"id": session_id, "status": status, "err": error})
        await db.commit()

    async def record_moment(self, db: AsyncSession, session_id: str, event: Dict[str, Any]) -> None:
        params = {
            "id": str(uuid4()), "sid": session_id, "n": event.get("n"), "status": event["status"],
            "reason": event.get("reason") or event.get("camera"), "onset": _ts(event.get("onset_wall")),
            "start": _ts(event.get("start_wall")), "end": _ts(event.get("end_wall")),
            "signals": json.dumps(event.get("signals") or []), "strength": event.get("strength"),
            "talk": event.get("talk_share"), "preview": event.get("preview_path"),
        }
        await db.execute(text(
            "INSERT INTO live_clips (id, session_id, n, status, reason, onset_at, start_at, end_at, signals, "
            "strength, talk_share, preview_path) VALUES (:id, :sid, :n, :status, :reason, :onset, :start, :end, "
            "CAST(:signals AS JSONB), :strength, :talk, :preview)"), params)
        await db.commit()

    async def update_clip(self, db: AsyncSession, session_id: str, event: Dict[str, Any]) -> None:
        await db.execute(text(
            "UPDATE live_clips SET status = :status, clip_path = COALESCE(:path, clip_path), "
            "duration = COALESCE(:duration, duration), render_seconds = COALESCE(:render_s, render_seconds), "
            "reason = COALESCE(:error, reason), updated_at = CURRENT_TIMESTAMP "
            "WHERE session_id = :sid AND n = :n"),
            {"sid": session_id, "n": event["n"], "status": event["status"], "path": event.get("clip_path"),
             "duration": event.get("duration"), "render_s": event.get("render_s"), "error": event.get("error")})
        await db.commit()
