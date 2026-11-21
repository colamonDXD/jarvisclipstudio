"""
Learning repository - feedback events and versioned adjustment documents.

Raw SQL in the style of the other repositories. Two tables (migration
20261108_0003_learning_engine.sql):

- ``learning_feedback_events``: append-only feedback log. ``features`` is a
  JSON snapshot of the clip's virality sub-scores, hook type, caption
  preset, music/SFX settings at signal time.
- ``learning_versions``: one immutable row per recomputation; the current
  document for a user is the highest version. Rollback inserts a new row
  copying an older version's adjustments — history is never rewritten.
"""

import json
import logging
from typing import Any, Dict, List, Optional
from uuid import uuid4

from sqlalchemy import text as sa_text
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)


def _parse_json(value: Any) -> Dict[str, Any]:
    if isinstance(value, dict):
        return value
    if not value:
        return {}
    try:
        parsed = json.loads(value)
    except (TypeError, ValueError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


class LearningRepository:
    """Persistence for the learning engine (see auto_director/learning.py)."""

    @staticmethod
    async def record_event(
        db: AsyncSession,
        *,
        user_id: str,
        task_id: str,
        clip_id: str,
        niche: Optional[str],
        verdict: str,
        reason: Optional[str],
        source: str,
        weight: float,
        features: Optional[Dict[str, Any]],
    ) -> str:
        """Append one feedback event and return its id."""
        event_id = str(uuid4())
        await db.execute(
            sa_text(
                "INSERT INTO learning_feedback_events "
                "(id, user_id, task_id, clip_id, niche, verdict, reason, "
                "source, weight, features) VALUES "
                "(:id, :user_id, :task_id, :clip_id, :niche, :verdict, "
                ":reason, :source, :weight, :features)"
            ),
            {
                "id": event_id,
                "user_id": user_id,
                "task_id": task_id,
                "clip_id": clip_id,
                "niche": niche,
                "verdict": verdict,
                "reason": reason,
                "source": source,
                "weight": float(weight),
                "features": json.dumps(features or {}),
            },
        )
        await db.commit()
        return event_id

    @staticmethod
    async def list_events(
        db: AsyncSession, user_id: str
    ) -> List[Dict[str, Any]]:
        """All feedback events for a user, oldest first (engine input)."""
        result = await db.execute(
            sa_text(
                "SELECT niche, verdict, reason, source, weight, features "
                "FROM learning_feedback_events WHERE user_id = :user_id "
                "ORDER BY created_at ASC, id ASC"
            ),
            {"user_id": user_id},
        )
        events: List[Dict[str, Any]] = []
        for row in result.mappings().all():
            events.append(
                {
                    "niche": row["niche"],
                    "verdict": row["verdict"],
                    "reason": row["reason"],
                    "source": row["source"],
                    "weight": row["weight"],
                    "features": _parse_json(row["features"]),
                }
            )
        return events

    @staticmethod
    async def count_events(db: AsyncSession, user_id: str) -> int:
        result = await db.execute(
            sa_text(
                "SELECT COUNT(*) AS n FROM learning_feedback_events "
                "WHERE user_id = :user_id"
            ),
            {"user_id": user_id},
        )
        row = result.mappings().first()
        return int(row["n"]) if row else 0

    @staticmethod
    async def list_versions(
        db: AsyncSession, user_id: str
    ) -> List[Dict[str, Any]]:
        """All version documents for a user, oldest first."""
        result = await db.execute(
            sa_text(
                "SELECT version, adjustments, event_count, summary, "
                "created_at FROM learning_versions "
                "WHERE user_id = :user_id ORDER BY version ASC"
            ),
            {"user_id": user_id},
        )
        versions: List[Dict[str, Any]] = []
        for row in result.mappings().all():
            adjustments = _parse_json(row["adjustments"])
            summary_raw = row["summary"] or ""
            versions.append(
                {
                    "version": int(row["version"]),
                    "event_count": int(row["event_count"]),
                    "created_at": str(row["created_at"]),
                    "summary": summary_raw.split("\n") if summary_raw else [],
                    "niches": adjustments,
                }
            )
        return versions

    @staticmethod
    async def current_version(
        db: AsyncSession, user_id: str
    ) -> Optional[Dict[str, Any]]:
        versions = await LearningRepository.list_versions(db, user_id)
        return versions[-1] if versions else None

    @staticmethod
    async def insert_version(
        db: AsyncSession, user_id: str, document: Dict[str, Any]
    ) -> None:
        """Persist one version document (append-only)."""
        await db.execute(
            sa_text(
                "INSERT INTO learning_versions "
                "(id, user_id, version, adjustments, event_count, summary) "
                "VALUES (:id, :user_id, :version, :adjustments, "
                ":event_count, :summary)"
            ),
            {
                "id": str(uuid4()),
                "user_id": user_id,
                "version": int(document["version"]),
                "adjustments": json.dumps(document.get("niches") or {}),
                "event_count": int(document.get("event_count") or 0),
                "summary": "\n".join(document.get("summary") or []),
            },
        )
        await db.commit()
