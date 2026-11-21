"""
Clip repository - handles all database operations for generated clips.
"""

from ..auto_director.clip_plan import comment_question_for, plan_hook_report
from ..editorial_scoring import build_editorial_quality
from ..auto_director.content_requirements import compliance_summary_for
from .clip_export_repository import ClipExportRepository
from .edit_transaction import commit_unless_editing
from .media_repository import persist_clip, queue_clip_delete
from .social_repository import SocialRepository
from sqlalchemy.exc import DBAPIError


from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import text as sa_text
from typing import List, Dict, Any, Optional
import logging
from uuid import uuid4

logger = logging.getLogger(__name__)


async def _stored_creator_name(db: AsyncSession, task_id: str) -> Optional[str]:
    """Creator name captured on the task at creation, if any.

    Read from the task's ``source_metadata`` JSON (stamped by
    ``TaskService.create_task_with_source`` from the source's
    channel/uploader metadata). Best-effort by design: any failure
    returns None and title rewriting falls back to the neutral form —
    a lookup problem must never break clip creation.
    """
    try:
        result = await db.execute(
            sa_text("SELECT source_metadata FROM tasks WHERE id = :task_id"),
            {"task_id": task_id},
        )
        row = result.fetchone()
        if row is None:
            return None
        from ..auto_director.title_person import creator_name_from_metadata

        return creator_name_from_metadata(row[0])
    except Exception:
        logger.debug(
            "Creator-name lookup failed for task %s", task_id, exc_info=True
        )
        return None


class ClipRepository:
    """Repository for clip-related database operations."""

    @staticmethod
    async def create_clip(
        db: AsyncSession,
        task_id: str,
        filename: str,
        file_path: str,
        start_time: str,
        end_time: str,
        duration: float,
        text: str,
        relevance_score: float,
        reasoning: str,
        clip_order: int,
        virality_score: int = 0,
        hook_score: int = 0,
        engagement_score: int = 0,
        value_score: int = 0,
        shareability_score: int = 0,
        hook_type: Optional[str] = None,
        hook_title: Optional[str] = None,
        niche_override: Optional[str] = None,
        clip_plan: Optional[Dict[str, Any]] = None,
        clip_id: Optional[str] = None,
        creator_name: Optional[str] = None,
    ) -> str:
        """Create a new clip record and return its ID."""
        import json

        # Third-person guardrail (title_person): every generated title is
        # persisted through here, whichever path produced it, and the clip
        # is posted by a clipper sharing another creator's moment — the
        # speaker's first person ("I bought…") must never reach storage.
        # Clean titles pass through byte-identical, so carry-over paths
        # (split, re-render) are unaffected. When no caller passes a
        # creator name, the name captured on the task at creation is
        # resolved from its stored metadata so rewrites attribute to the
        # creator ("TJR Tries…") instead of only neutralising.
        if hook_title:
            from ..auto_director.title_person import ensure_third_person_title

            if creator_name is None:
                creator_name = await _stored_creator_name(db, task_id)
            hook_title = ensure_third_person_title(
                hook_title, creator_name=creator_name, fallback_text=text
            )
        previous = await db.execute(sa_text(
            "SELECT id, task_id, filename, file_path FROM generated_clips "
            "WHERE task_id = :task_id AND clip_order = :clip_order"
        ), {"task_id": task_id, "clip_order": clip_order})
        old_clip = previous.mappings().first()
        file_path = await persist_clip(db, file_path, task_id)
        if old_clip and old_clip["file_path"] != file_path:
            await queue_clip_delete(db, old_clip)
        base_params = {
            "id": clip_id or str(uuid4()),
            "task_id": task_id,
            "filename": filename,
            "file_path": file_path,
            "start_time": start_time,
            "end_time": end_time,
            "duration": duration,
            "text": text,
            "relevance_score": relevance_score,
            "reasoning": reasoning,
            "clip_order": clip_order,
        }
        try:
            async with db.begin_nested():
                result = await db.execute(
                    sa_text("""
                        INSERT INTO generated_clips
                        (id, task_id, filename, file_path, start_time, end_time, duration,
                         text, relevance_score, reasoning, clip_order,
                         virality_score, hook_score, engagement_score, value_score, shareability_score, hook_type,
                         hook_title, niche_override, clip_plan, created_at)
                        VALUES
                        (:id, :task_id, :filename, :file_path, :start_time, :end_time, :duration,
                         :text, :relevance_score, :reasoning, :clip_order,
                         :virality_score, :hook_score, :engagement_score, :value_score, :shareability_score, :hook_type,
                         :hook_title, :niche_override, :clip_plan, NOW())
                        ON CONFLICT (task_id, clip_order) DO UPDATE SET
                            filename = EXCLUDED.filename,
                            file_path = EXCLUDED.file_path,
                            start_time = EXCLUDED.start_time,
                            end_time = EXCLUDED.end_time,
                            duration = EXCLUDED.duration,
                            text = EXCLUDED.text,
                            relevance_score = EXCLUDED.relevance_score,
                            reasoning = EXCLUDED.reasoning,
                            virality_score = EXCLUDED.virality_score,
                            hook_score = EXCLUDED.hook_score,
                            engagement_score = EXCLUDED.engagement_score,
                            value_score = EXCLUDED.value_score,
                            shareability_score = EXCLUDED.shareability_score,
                            hook_type = EXCLUDED.hook_type,
                            hook_title = EXCLUDED.hook_title,
                            niche_override = COALESCE(EXCLUDED.niche_override, generated_clips.niche_override),
                            clip_plan = EXCLUDED.clip_plan,
                            updated_at = NOW()
                        RETURNING id
                    """),
                    {
                        **base_params,
                        "virality_score": virality_score,
                        "hook_score": hook_score,
                        "engagement_score": engagement_score,
                        "value_score": value_score,
                        "shareability_score": shareability_score,
                        "hook_type": hook_type,
                        "hook_title": hook_title,
                        "niche_override": niche_override,
                        "clip_plan": json.dumps(clip_plan) if clip_plan is not None else None,
                    },
                )
        except DBAPIError as exc:
            if getattr(exc.orig, "sqlstate", None) != "42703":
                raise
            result = await db.execute(
                sa_text("""
                    INSERT INTO generated_clips
                    (id, task_id, filename, file_path, start_time, end_time, duration,
                     text, relevance_score, reasoning, clip_order, created_at)
                    VALUES
                    (:id, :task_id, :filename, :file_path, :start_time, :end_time, :duration,
                     :text, :relevance_score, :reasoning, :clip_order, NOW())
                    ON CONFLICT (task_id, clip_order) DO UPDATE SET
                        filename = EXCLUDED.filename,
                        file_path = EXCLUDED.file_path,
                        start_time = EXCLUDED.start_time,
                        end_time = EXCLUDED.end_time,
                        duration = EXCLUDED.duration,
                        text = EXCLUDED.text,
                        relevance_score = EXCLUDED.relevance_score,
                        reasoning = EXCLUDED.reasoning,
                        updated_at = NOW()
                    RETURNING id
                """),
                base_params,
            )
        clip_id = result.scalar()
        if not clip_id:
            raise RuntimeError("Failed to create clip: no ID returned")
        logger.debug(f"Created clip {clip_id} for task {task_id}")
        return str(clip_id)

    @staticmethod
    async def get_clips_by_task(db: AsyncSession, task_id: str) -> List[Dict[str, Any]]:
        """Get all clips for a specific task, ordered by clip_order."""
        try:
            async with db.begin_nested():
                result = await db.execute(
                    sa_text("""
                        SELECT id, filename, file_path, start_time, end_time, duration,
                               text, relevance_score, reasoning, clip_order, created_at,
                               virality_score, hook_score, engagement_score, value_score, shareability_score, hook_type,
                               hook_title, niche_override, qc_status, qc_report, clip_plan, hook_variant, hook_variant_batch, compliance_status, compliance_report
                        FROM generated_clips
                        WHERE task_id = :task_id
                        ORDER BY clip_order ASC
                    """),
                    {"task_id": task_id},
                )
        except DBAPIError as exc:
            if getattr(exc.orig, "sqlstate", None) != "42703":
                raise
            result = await db.execute(
                sa_text("""
                    SELECT id, filename, file_path, start_time, end_time, duration,
                           text, relevance_score, reasoning, clip_order, created_at
                    FROM generated_clips
                    WHERE task_id = :task_id
                    ORDER BY clip_order ASC
                """),
                {"task_id": task_id},
            )

        clips = []
        for row in result.fetchall():
            clips.append(
                {
                    "id": row.id,
                    "filename": row.filename,
                    "file_path": row.file_path,
                    "start_time": row.start_time,
                    "end_time": row.end_time,
                    "duration": row.duration,
                    "text": row.text,
                    "relevance_score": row.relevance_score,
                    "reasoning": row.reasoning,
                    "clip_order": row.clip_order,
                    "created_at": row.created_at.isoformat(),
                    "video_url": f"/tasks/{task_id}/clips/{row.id}/file",
                    "virality_score": getattr(row, "virality_score", 0) or 0,
                    "hook_score": getattr(row, "hook_score", 0) or 0,
                    "engagement_score": getattr(row, "engagement_score", 0) or 0,
                    "value_score": getattr(row, "value_score", 0) or 0,
                    "shareability_score": getattr(row, "shareability_score", 0) or 0,
                    "hook_type": getattr(row, "hook_type", None),
                    "hook_title": getattr(row, "hook_title", None),
                    "niche_override": getattr(row, "niche_override", None),
                    "qc_status": getattr(row, "qc_status", None),
                    "qc_report": getattr(row, "qc_report", None),
                    "clip_plan": getattr(row, "clip_plan", None),
                    "hook_report": plan_hook_report(getattr(row, "clip_plan", None)),
                    "comment_question": comment_question_for(
                        getattr(row, "clip_plan", None)
                    ),
                    "hook_variant": getattr(row, "hook_variant", None),
                    "hook_variant_batch": getattr(row, "hook_variant_batch", None),
                    "compliance_status": getattr(row, "compliance_status", None),
                    "compliance_report": getattr(row, "compliance_report", None),
                    "compliance_summary": compliance_summary_for(
                        getattr(row, "compliance_report", None)
                    ),
                }
            )

        for clip in clips:
            # Editorial quality v2: computed at serialization from the
            # stored QC report, clip plan and selection sub-scores —
            # no schema change, legacy score fields above are untouched.
            clip["editorial_quality"] = build_editorial_quality(clip)
        return clips

    @staticmethod
    async def get_clips_count(db: AsyncSession, task_id: str) -> int:
        """Get the count of clips for a task."""
        result = await db.execute(
            sa_text(
                "SELECT COUNT(*) as count FROM generated_clips WHERE task_id = :task_id"
            ),
            {"task_id": task_id},
        )
        return result.scalar()

    @staticmethod
    async def delete_clips_by_task(db: AsyncSession, task_id: str) -> int:
        """Delete all clips for a task. Returns count of deleted clips."""
        rows = await db.execute(sa_text(
            "SELECT id, task_id, filename, file_path FROM generated_clips WHERE task_id = :task_id"
        ), {"task_id": task_id})
        for clip in rows.mappings():
            await queue_clip_delete(db, clip)
        await ClipExportRepository.queue_file_deletes(db, task_id=task_id)
        await SocialRepository.cancel_unpublished_for_clips(db, task_id=task_id)
        result = await db.execute(
            sa_text("DELETE FROM generated_clips WHERE task_id = :task_id"),
            {"task_id": task_id},
        )
        await commit_unless_editing(db)
        deleted_count = result.rowcount
        logger.info(f"Deleted {deleted_count} clips for task {task_id}")
        return deleted_count

    @staticmethod
    async def delete_clip(db: AsyncSession, clip_id: str) -> None:
        """Delete a single clip by ID."""
        rows = await db.execute(sa_text(
            "SELECT id, task_id, filename, file_path FROM generated_clips WHERE id = :clip_id"
        ), {"clip_id": clip_id})
        clip = rows.mappings().first()
        if clip:
            await queue_clip_delete(db, clip)
            await ClipExportRepository.queue_file_deletes(db, clip_id=clip_id)
            await SocialRepository.cancel_unpublished_for_clips(db, clip_id=clip_id)
        await db.execute(
            sa_text("DELETE FROM generated_clips WHERE id = :clip_id"),
            {"clip_id": clip_id},
        )
        await commit_unless_editing(db)
        logger.info(f"Deleted clip {clip_id}")

    @staticmethod
    async def get_clip_by_id(
        db: AsyncSession, clip_id: str
    ) -> Optional[Dict[str, Any]]:
        """Get one clip by ID."""
        try:
            async with db.begin_nested():
                result = await db.execute(
                    sa_text(
                        """
                        SELECT id, task_id, filename, file_path, start_time, end_time, duration,
                               text, relevance_score, reasoning, clip_order,
                               virality_score, hook_score, engagement_score, value_score, shareability_score, hook_type,
                               hook_title, niche_override, qc_status, qc_report, clip_plan, hook_variant, hook_variant_batch, compliance_status, compliance_report, created_at
                        FROM generated_clips
                        WHERE id = :clip_id
                        """
                    ),
                    {"clip_id": clip_id},
                )
        except DBAPIError as exc:
            if getattr(exc.orig, "sqlstate", None) != "42703":
                raise
            result = await db.execute(
                sa_text(
                    """
                    SELECT id, task_id, filename, file_path, start_time, end_time, duration,
                           text, relevance_score, reasoning, clip_order, created_at
                    FROM generated_clips
                    WHERE id = :clip_id
                    """
                ),
                {"clip_id": clip_id},
            )
        row = result.fetchone()
        if not row:
            return None

        clip = {
            "id": row.id,
            "task_id": row.task_id,
            "filename": row.filename,
            "file_path": row.file_path,
            "start_time": row.start_time,
            "end_time": row.end_time,
            "duration": row.duration,
            "text": row.text,
            "relevance_score": row.relevance_score,
            "reasoning": row.reasoning,
            "clip_order": row.clip_order,
            "virality_score": getattr(row, "virality_score", 0) or 0,
            "hook_score": getattr(row, "hook_score", 0) or 0,
            "engagement_score": getattr(row, "engagement_score", 0) or 0,
            "value_score": getattr(row, "value_score", 0) or 0,
            "shareability_score": getattr(row, "shareability_score", 0) or 0,
            "hook_type": getattr(row, "hook_type", None),
            "hook_title": getattr(row, "hook_title", None),
            "niche_override": getattr(row, "niche_override", None),
            "qc_status": getattr(row, "qc_status", None),
            "qc_report": getattr(row, "qc_report", None),
            "clip_plan": getattr(row, "clip_plan", None),
            "hook_report": plan_hook_report(getattr(row, "clip_plan", None)),
            "comment_question": comment_question_for(
                getattr(row, "clip_plan", None)
            ),
            "hook_variant": getattr(row, "hook_variant", None),
            "hook_variant_batch": getattr(row, "hook_variant_batch", None),
            "compliance_status": getattr(row, "compliance_status", None),
            "compliance_report": getattr(row, "compliance_report", None),
            "compliance_summary": compliance_summary_for(
                getattr(row, "compliance_report", None)
            ),
            "created_at": row.created_at.isoformat(),
            "video_url": f"/tasks/{row.task_id}/clips/{row.id}/file",
        }
        clip["editorial_quality"] = build_editorial_quality(clip)
        return clip

    @staticmethod
    async def update_clip(
        db: AsyncSession,
        clip_id: str,
        filename: str,
        file_path: str,
        start_time: str,
        end_time: str,
        duration: float,
        text: str,
    ) -> None:
        """Update core clip metadata and file path."""
        rows = await db.execute(sa_text(
            "SELECT id, task_id, filename, file_path FROM generated_clips WHERE id = :clip_id"
        ), {"clip_id": clip_id})
        previous = rows.mappings().first()
        if not previous:
            raise ValueError("Clip not found")
        file_path = await persist_clip(db, file_path, previous["task_id"])
        if previous["file_path"] != file_path:
            await queue_clip_delete(db, previous)
        await db.execute(
            sa_text(
                """
                UPDATE generated_clips
                SET filename = :filename,
                    file_path = :file_path,
                    start_time = :start_time,
                    end_time = :end_time,
                    duration = :duration,
                    text = :text,
                    updated_at = NOW()
                WHERE id = :clip_id
                """
            ),
            {
                "clip_id": clip_id,
                "filename": filename,
                "file_path": file_path,
                "start_time": start_time,
                "end_time": end_time,
                "duration": duration,
                "text": text,
            },
        )
        await commit_unless_editing(db)

    @staticmethod
    async def update_clip_niche_override(
        db: AsyncSession, clip_id: str, niche_override: Optional[str]
    ) -> None:
        """Record the niche profile a clip was last rendered with."""
        await db.execute(
            sa_text(
                """
                UPDATE generated_clips
                SET niche_override = :niche_override,
                    updated_at = NOW()
                WHERE id = :clip_id
                """
            ),
            {"clip_id": clip_id, "niche_override": niche_override},
        )
        await commit_unless_editing(db)

    @staticmethod
    async def update_clip_hook_variant(
        db: AsyncSession,
        clip_id: str,
        hook_variant: Optional[str],
        hook_variant_batch: Optional[str],
    ) -> None:
        """Record which hook variant opening the clip currently uses.

        ``hook_variant`` is the variant kind ("cold_open" / "punch"), or
        None when the clip is back on its original opening;
        ``hook_variant_batch`` names the preview batch it was picked
        from, so the UI can mark the picked preview.
        """
        await db.execute(
            sa_text(
                """
                UPDATE generated_clips
                SET hook_variant = :hook_variant,
                    hook_variant_batch = :hook_variant_batch,
                    updated_at = NOW()
                WHERE id = :clip_id
                """
            ),
            {
                "clip_id": clip_id,
                "hook_variant": hook_variant,
                "hook_variant_batch": hook_variant_batch,
            },
        )
        await commit_unless_editing(db)

    @staticmethod
    async def update_clip_qc(
        db: AsyncSession,
        clip_id: str,
        qc_status: Optional[str],
        qc_report: Optional[Dict[str, Any]],
    ) -> None:
        """Store the AI quality-control verdict and evidence report on a clip."""
        import json

        await db.execute(
            sa_text(
                """
                UPDATE generated_clips
                SET qc_status = :qc_status,
                    qc_report = :qc_report,
                    updated_at = NOW()
                WHERE id = :clip_id
                """
            ),
            {
                "clip_id": clip_id,
                "qc_status": qc_status,
                "qc_report": json.dumps(qc_report) if qc_report is not None else None,
            },
        )
        await commit_unless_editing(db)

    @staticmethod
    async def update_clip_compliance(
        db: AsyncSession,
        clip_id: str,
        compliance_status: Optional[str],
        compliance_report: Optional[Dict[str, Any]],
    ) -> None:
        """Store the campaign-brief compliance verdict + evidence report."""
        import json

        await db.execute(
            sa_text(
                """
                UPDATE generated_clips
                SET compliance_status = :compliance_status,
                    compliance_report = :compliance_report,
                    updated_at = NOW()
                WHERE id = :clip_id
                """
            ),
            {
                "clip_id": clip_id,
                "compliance_status": compliance_status,
                "compliance_report": (
                    json.dumps(compliance_report)
                    if compliance_report is not None
                    else None
                ),
            },
        )
        await commit_unless_editing(db)

    @staticmethod
    async def make_room_for_clip(db: AsyncSession, task_id: str, clip_order: int) -> None:
        """Shift following clips without colliding with the unique order key."""
        result = await db.execute(
            sa_text("SELECT id FROM generated_clips WHERE task_id = :task_id "
                    "AND clip_order >= :clip_order ORDER BY clip_order DESC FOR UPDATE"),
            {"task_id": task_id, "clip_order": clip_order},
        )
        for row in result.fetchall():
            await db.execute(
                sa_text("UPDATE generated_clips SET clip_order = clip_order + 1 WHERE id = :id"),
                {"id": row.id},
            )


    @staticmethod
    async def reorder_task_clips(db: AsyncSession, task_id: str) -> None:
        """Normalize clip_order sequence after edits."""
        result = await db.execute(
            sa_text(
                "SELECT id FROM generated_clips WHERE task_id = :task_id ORDER BY clip_order ASC, created_at ASC"
            ),
            {"task_id": task_id},
        )
        clip_ids = [row.id for row in result.fetchall()]
        for idx, cid in enumerate(clip_ids, start=1):
            await db.execute(
                sa_text(
                    "UPDATE generated_clips SET clip_order = :clip_order, updated_at = NOW() WHERE id = :clip_id"
                ),
                {"clip_order": idx, "clip_id": cid},
            )
        await db.execute(
            sa_text("UPDATE tasks SET generated_clips_ids = :ids, updated_at = NOW() WHERE id = :task_id"),
            {"ids": clip_ids, "task_id": task_id},
        )
        await commit_unless_editing(db)
