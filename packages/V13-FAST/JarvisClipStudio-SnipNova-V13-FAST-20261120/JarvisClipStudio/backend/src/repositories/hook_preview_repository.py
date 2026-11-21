"""
Hook preview repository - durable records for hook-variant previews.

One row per rendered preview file. ``params`` snapshots the variant's
full segment layout and opening treatment so a later pick re-renders the
full clip from exactly what the user previewed, even after a newer
preview batch replaced the visible list.
"""

import json
from typing import Any, Dict, List, Optional

from sqlalchemy import text as sa_text
from sqlalchemy.ext.asyncio import AsyncSession

from .edit_transaction import commit_unless_editing


def _row_to_preview(row: Any) -> Dict[str, Any]:
    try:
        params = json.loads(row.params) if row.params else {}
    except (TypeError, ValueError):
        params = {}
    if not isinstance(params, dict):
        params = {}
    return {
        "id": row.id,
        "task_id": row.task_id,
        "clip_id": row.clip_id,
        "batch_id": row.batch_id,
        "variant": row.variant,
        "label": row.label,
        "description": row.description or "",
        "params": params,
        "file_name": row.file_name,
        "file_path": row.file_path,
        "duration": row.duration or 0,
        "created_at": row.created_at.isoformat() if row.created_at else None,
    }


class HookPreviewRepository:
    """Repository for clip hook-variant preview records."""

    @staticmethod
    async def replace_previews_for_clip(
        db: AsyncSession, clip_id: str, previews: List[Dict[str, Any]]
    ) -> List[Dict[str, Any]]:
        """Replace a clip's preview rows with a fresh batch.

        Returns the rows that were removed (so the caller can delete
        their files) — the new batch is inserted in the same call.
        """
        result = await db.execute(
            sa_text(
                """
                SELECT id, task_id, clip_id, batch_id, variant, label,
                       description, params, file_name, file_path, duration,
                       created_at
                FROM clip_hook_previews
                WHERE clip_id = :clip_id
                """
            ),
            {"clip_id": clip_id},
        )
        removed = [_row_to_preview(row) for row in result.fetchall()]
        await db.execute(
            sa_text(
                "DELETE FROM clip_hook_previews WHERE clip_id = :clip_id"
            ),
            {"clip_id": clip_id},
        )
        for preview in previews:
            await db.execute(
                sa_text(
                    """
                    INSERT INTO clip_hook_previews (
                        id, task_id, clip_id, batch_id, variant, label,
                        description, params, file_name, file_path, duration
                    ) VALUES (
                        :id, :task_id, :clip_id, :batch_id, :variant,
                        :label, :description, :params, :file_name,
                        :file_path, :duration
                    )
                    """
                ),
                {
                    "id": preview["id"],
                    "task_id": preview["task_id"],
                    "clip_id": preview["clip_id"],
                    "batch_id": preview["batch_id"],
                    "variant": preview["variant"],
                    "label": preview["label"],
                    "description": preview.get("description") or "",
                    "params": json.dumps(preview.get("params") or {}),
                    "file_name": preview["file_name"],
                    "file_path": preview["file_path"],
                    "duration": preview.get("duration") or 0,
                },
            )
        await commit_unless_editing(db)
        return removed

    @staticmethod
    async def list_previews_for_clip(
        db: AsyncSession, clip_id: str
    ) -> List[Dict[str, Any]]:
        """The clip's latest preview batch, oldest variant first."""
        result = await db.execute(
            sa_text(
                """
                SELECT id, task_id, clip_id, batch_id, variant, label,
                       description, params, file_name, file_path, duration,
                       created_at
                FROM clip_hook_previews
                WHERE clip_id = :clip_id
                  AND batch_id = (
                      SELECT batch_id FROM clip_hook_previews
                      WHERE clip_id = :clip_id
                      ORDER BY created_at DESC
                      LIMIT 1
                  )
                ORDER BY CASE variant
                             WHEN 'current' THEN 0
                             WHEN 'cold_open' THEN 1
                             WHEN 'punch' THEN 2
                             ELSE 3
                         END, created_at ASC
                """
            ),
            {"clip_id": clip_id},
        )
        return [_row_to_preview(row) for row in result.fetchall()]

    @staticmethod
    async def get_preview(
        db: AsyncSession, preview_id: str
    ) -> Optional[Dict[str, Any]]:
        """One preview record by id, or None."""
        result = await db.execute(
            sa_text(
                """
                SELECT id, task_id, clip_id, batch_id, variant, label,
                       description, params, file_name, file_path, duration,
                       created_at
                FROM clip_hook_previews
                WHERE id = :preview_id
                """
            ),
            {"preview_id": preview_id},
        )
        row = result.fetchone()
        return _row_to_preview(row) if row else None
