"""
Publishing repository - campaigns, clip post records, entered stats.

Everything here stores ONLY what the user typed into the publishing
workspace: campaigns they created, posts they recorded after publishing
by hand, and performance numbers they entered themselves. No scraping,
no platform calls, no estimates.
"""

from typing import Any, Dict, List, Optional

from sqlalchemy import text as sa_text
from sqlalchemy.ext.asyncio import AsyncSession


def _iso(value: Any) -> Optional[str]:
    return value.isoformat() if value is not None else None


def _brief_to_text(value: Any) -> Optional[str]:
    if value is None or isinstance(value, str):
        return value
    import json

    return json.dumps(value)


def _campaign_row(row: Any) -> Dict[str, Any]:
    brief_raw = getattr(row, "brief", None)
    brief = None
    if brief_raw:
        try:
            from ..auto_director.content_requirements import parse_stored_brief

            brief = parse_stored_brief(brief_raw)
        except Exception:
            brief = None
    return {
        "id": row.id,
        "user_id": row.user_id,
        "name": row.name,
        "platform": row.platform or "",
        "campaign_url": row.campaign_url,
        "budget": float(row.budget) if row.budget is not None else None,
        "notes": row.notes,
        "brief": brief,
        "has_brief": brief is not None,
        "example_url": getattr(row, "example_url", None),
        "created_at": _iso(row.created_at),
        "updated_at": _iso(row.updated_at),
    }


def _post_row(row: Any) -> Dict[str, Any]:
    return {
        "id": row.id,
        "clip_id": row.clip_id,
        "user_id": row.user_id,
        "platform": row.platform,
        "post_url": row.post_url or "",
        "posted_at": _iso(row.posted_at),
        "campaign_id": row.campaign_id,
        "submission_url": row.submission_url,
        "submission_dismissed": bool(row.submission_dismissed),
        "created_at": _iso(row.created_at),
        "updated_at": _iso(row.updated_at),
    }


def _stats_row(row: Any) -> Dict[str, Any]:
    return {
        "post_id": row.post_id,
        "views": int(row.views) if row.views is not None else None,
        "likes": int(row.likes) if row.likes is not None else None,
        "comments": int(row.comments) if row.comments is not None else None,
        "earnings": float(row.earnings) if row.earnings is not None else None,
        "updated_at": _iso(row.updated_at),
    }


class PublishingRepository:
    """Raw-SQL repository for the publishing workspace tables."""

    # -- Board inputs ----------------------------------------------------

    @staticmethod
    async def list_user_clips(
        db: AsyncSession, user_id: str, limit: int = 300
    ) -> List[Dict[str, Any]]:
        """Every finished clip of the user, newest first, with task context."""
        result = await db.execute(
            sa_text(
                """
                SELECT c.id, c.task_id, c.filename, c.file_path, c.duration,
                       c.text, c.clip_order, c.created_at, c.hook_title,
                       c.niche_override, c.qc_status, c.qc_report, c.clip_plan,
                       c.compliance_status, c.compliance_report,
                       t.niche_override AS task_niche_override,
                       t.content_brief AS task_content_brief,
                       t.stage_timings_json AS task_stage_timings_json,
                       s.title AS source_title
                FROM generated_clips c
                JOIN tasks t ON t.id = c.task_id
                LEFT JOIN sources s ON s.id = t.source_id
                WHERE t.user_id = :user_id
                ORDER BY c.created_at DESC
                LIMIT :limit
                """
            ),
            {"user_id": user_id, "limit": limit},
        )
        clips = []
        for row in result.fetchall():
            clips.append(
                {
                    "id": row.id,
                    "task_id": row.task_id,
                    "filename": row.filename,
                    "file_path": row.file_path,
                    "duration": row.duration,
                    "text": row.text,
                    "clip_order": row.clip_order,
                    "created_at": _iso(row.created_at),
                    "hook_title": getattr(row, "hook_title", None),
                    "niche_override": getattr(row, "niche_override", None),
                    "task_niche_override": getattr(
                        row, "task_niche_override", None
                    ),
                    "qc_status": getattr(row, "qc_status", None),
                    "qc_report": getattr(row, "qc_report", None),
                    "clip_plan": getattr(row, "clip_plan", None),
                    "compliance_status": getattr(row, "compliance_status", None),
                    "compliance_report": getattr(row, "compliance_report", None),
                    "task_content_brief": getattr(row, "task_content_brief", None),
                    "source_title": getattr(row, "source_title", None),
                    "video_url": f"/tasks/{row.task_id}/clips/{row.id}/file",
                }
            )
        return clips

    @staticmethod
    async def get_owned_clip(
        db: AsyncSession, user_id: str, clip_id: str
    ) -> Optional[Dict[str, Any]]:
        result = await db.execute(
            sa_text(
                """
                SELECT c.id, c.task_id, c.filename, c.file_path
                FROM generated_clips c
                JOIN tasks t ON t.id = c.task_id
                WHERE c.id = :clip_id AND t.user_id = :user_id
                """
            ),
            {"clip_id": clip_id, "user_id": user_id},
        )
        row = result.fetchone()
        if row is None:
            return None
        return {
            "id": row.id,
            "task_id": row.task_id,
            "filename": row.filename,
            "file_path": row.file_path,
        }

    @staticmethod
    async def list_user_tasks(
        db: AsyncSession, user_id: str
    ) -> List[Dict[str, Any]]:
        result = await db.execute(
            sa_text(
                """
                SELECT id, status, created_at, completed_at, stage_timings_json
                FROM tasks
                WHERE user_id = :user_id
                """
            ),
            {"user_id": user_id},
        )
        return [
            {
                "id": row.id,
                "status": row.status,
                "created_at": _iso(row.created_at),
                "completed_at": _iso(row.completed_at),
                "stage_timings_json": row.stage_timings_json,
            }
            for row in result.fetchall()
        ]

    @staticmethod
    async def user_token_totals(
        db: AsyncSession, user_id: str
    ) -> Dict[str, Any]:
        """Token totals over the user's own tasks (provider-reported only)."""
        result = await db.execute(
            sa_text(
                """
                SELECT COUNT(*) AS calls,
                       COALESCE(SUM(u.prompt_tokens), 0) AS prompt_tokens,
                       COALESCE(SUM(u.completion_tokens), 0) AS completion_tokens,
                       COALESCE(SUM(u.total_tokens), 0) AS total_tokens,
                       COALESCE(SUM(CASE WHEN u.usage_known THEN 0 ELSE 1 END), 0)
                           AS unknown_usage_calls
                FROM llm_token_usage u
                JOIN tasks t ON t.id = u.task_id
                WHERE t.user_id = :user_id
                """
            ),
            {"user_id": user_id},
        )
        row = result.fetchone()
        if row is None:
            return {
                "calls": 0,
                "prompt_tokens": 0,
                "completion_tokens": 0,
                "total_tokens": 0,
                "unknown_usage_calls": 0,
            }
        return {
            "calls": int(row.calls or 0),
            "prompt_tokens": int(row.prompt_tokens or 0),
            "completion_tokens": int(row.completion_tokens or 0),
            "total_tokens": int(row.total_tokens or 0),
            "unknown_usage_calls": int(row.unknown_usage_calls or 0),
        }

    # -- Campaigns ---------------------------------------------------------

    @staticmethod
    async def list_campaigns(
        db: AsyncSession, user_id: str
    ) -> List[Dict[str, Any]]:
        result = await db.execute(
            sa_text(
                """
                SELECT id, user_id, name, platform, campaign_url, budget,
                       notes, brief, example_url, created_at, updated_at
                FROM campaigns
                WHERE user_id = :user_id
                ORDER BY created_at DESC
                """
            ),
            {"user_id": user_id},
        )
        return [_campaign_row(row) for row in result.fetchall()]

    @staticmethod
    async def get_campaign(
        db: AsyncSession, user_id: str, campaign_id: str
    ) -> Optional[Dict[str, Any]]:
        result = await db.execute(
            sa_text(
                """
                SELECT id, user_id, name, platform, campaign_url, budget,
                       notes, brief, example_url, created_at, updated_at
                FROM campaigns
                WHERE id = :campaign_id AND user_id = :user_id
                """
            ),
            {"campaign_id": campaign_id, "user_id": user_id},
        )
        row = result.fetchone()
        return _campaign_row(row) if row else None

    @staticmethod
    async def create_campaign(
        db: AsyncSession, campaign: Dict[str, Any]
    ) -> Dict[str, Any]:
        await db.execute(
            sa_text(
                """
                INSERT INTO campaigns
                    (id, user_id, name, platform, campaign_url, budget, notes,
                     brief, example_url)
                VALUES
                    (:id, :user_id, :name, :platform, :campaign_url, :budget,
                     :notes, :brief, :example_url)
                """
            ),
            {
                **campaign,
                "brief": _brief_to_text(campaign.get("brief")),
                "example_url": campaign.get("example_url"),
            },
        )
        await db.commit()
        return campaign

    @staticmethod
    async def update_campaign(
        db: AsyncSession, user_id: str, campaign_id: str, fields: Dict[str, Any]
    ) -> Optional[Dict[str, Any]]:
        if not fields:
            return await PublishingRepository.get_campaign(db, user_id, campaign_id)
        fields = {
            key: (_brief_to_text(value) if key == "brief" else value)
            for key, value in fields.items()
        }
        assignments = ", ".join(f"{key} = :{key}" for key in fields)
        result = await db.execute(
            sa_text(
                f"""
                UPDATE campaigns
                SET {assignments}, updated_at = CURRENT_TIMESTAMP
                WHERE id = :campaign_id AND user_id = :user_id
                RETURNING id
                """
            ),
            {**fields, "campaign_id": campaign_id, "user_id": user_id},
        )
        if result.scalar() is None:
            return None
        await db.commit()
        return await PublishingRepository.get_campaign(db, user_id, campaign_id)

    @staticmethod
    async def delete_campaign(
        db: AsyncSession, user_id: str, campaign_id: str
    ) -> bool:
        await db.execute(
            sa_text(
                """
                UPDATE clip_posts SET campaign_id = NULL,
                       updated_at = CURRENT_TIMESTAMP
                WHERE campaign_id = :campaign_id AND user_id = :user_id
                """
            ),
            {"campaign_id": campaign_id, "user_id": user_id},
        )
        result = await db.execute(
            sa_text(
                """
                DELETE FROM campaigns
                WHERE id = :campaign_id AND user_id = :user_id
                RETURNING id
                """
            ),
            {"campaign_id": campaign_id, "user_id": user_id},
        )
        deleted = result.scalar() is not None
        await db.commit()
        return deleted

    # -- Posts -------------------------------------------------------------

    @staticmethod
    async def list_posts(
        db: AsyncSession, user_id: str
    ) -> List[Dict[str, Any]]:
        result = await db.execute(
            sa_text(
                """
                SELECT id, clip_id, user_id, platform, post_url, posted_at,
                       campaign_id, submission_url, submission_dismissed,
                       created_at, updated_at
                FROM clip_posts
                WHERE user_id = :user_id
                ORDER BY posted_at DESC
                """
            ),
            {"user_id": user_id},
        )
        return [_post_row(row) for row in result.fetchall()]

    @staticmethod
    async def get_post(
        db: AsyncSession, user_id: str, post_id: str
    ) -> Optional[Dict[str, Any]]:
        result = await db.execute(
            sa_text(
                """
                SELECT id, clip_id, user_id, platform, post_url, posted_at,
                       campaign_id, submission_url, submission_dismissed,
                       created_at, updated_at
                FROM clip_posts
                WHERE id = :post_id AND user_id = :user_id
                """
            ),
            {"post_id": post_id, "user_id": user_id},
        )
        row = result.fetchone()
        return _post_row(row) if row else None

    @staticmethod
    async def upsert_post(
        db: AsyncSession, post: Dict[str, Any]
    ) -> Dict[str, Any]:
        """One post record per (clip, platform): re-marking updates it."""
        await db.execute(
            sa_text(
                """
                INSERT INTO clip_posts
                    (id, clip_id, user_id, platform, post_url, posted_at,
                     campaign_id, submission_url, submission_dismissed)
                VALUES
                    (:id, :clip_id, :user_id, :platform, :post_url, :posted_at,
                     :campaign_id, :submission_url, :submission_dismissed)
                ON CONFLICT (clip_id, platform) DO UPDATE SET
                    post_url = EXCLUDED.post_url,
                    posted_at = EXCLUDED.posted_at,
                    campaign_id = EXCLUDED.campaign_id,
                    submission_url = EXCLUDED.submission_url,
                    submission_dismissed = EXCLUDED.submission_dismissed,
                    updated_at = CURRENT_TIMESTAMP
                RETURNING id
                """
            ),
            post,
        )
        await db.commit()
        result = await db.execute(
            sa_text(
                """
                SELECT id, clip_id, user_id, platform, post_url, posted_at,
                       campaign_id, submission_url, submission_dismissed,
                       created_at, updated_at
                FROM clip_posts
                WHERE clip_id = :clip_id AND platform = :platform
                """
            ),
            {"clip_id": post["clip_id"], "platform": post["platform"]},
        )
        return _post_row(result.fetchone())

    @staticmethod
    async def update_post(
        db: AsyncSession, user_id: str, post_id: str, fields: Dict[str, Any]
    ) -> Optional[Dict[str, Any]]:
        if not fields:
            return await PublishingRepository.get_post(db, user_id, post_id)
        assignments = ", ".join(f"{key} = :{key}" for key in fields)
        result = await db.execute(
            sa_text(
                f"""
                UPDATE clip_posts
                SET {assignments}, updated_at = CURRENT_TIMESTAMP
                WHERE id = :post_id AND user_id = :user_id
                RETURNING id
                """
            ),
            {**fields, "post_id": post_id, "user_id": user_id},
        )
        if result.scalar() is None:
            return None
        await db.commit()
        return await PublishingRepository.get_post(db, user_id, post_id)

    @staticmethod
    async def delete_post(
        db: AsyncSession, user_id: str, post_id: str
    ) -> bool:
        await db.execute(
            sa_text("DELETE FROM clip_post_stats WHERE post_id = :post_id"),
            {"post_id": post_id},
        )
        result = await db.execute(
            sa_text(
                """
                DELETE FROM clip_posts
                WHERE id = :post_id AND user_id = :user_id
                RETURNING id
                """
            ),
            {"post_id": post_id, "user_id": user_id},
        )
        deleted = result.scalar() is not None
        await db.commit()
        return deleted

    # -- Entered stats -------------------------------------------------------

    @staticmethod
    async def list_stats(db: AsyncSession, user_id: str) -> List[Dict[str, Any]]:
        result = await db.execute(
            sa_text(
                """
                SELECT s.post_id, s.views, s.likes, s.comments, s.earnings,
                       s.updated_at
                FROM clip_post_stats s
                JOIN clip_posts p ON p.id = s.post_id
                WHERE p.user_id = :user_id
                """
            ),
            {"user_id": user_id},
        )
        return [_stats_row(row) for row in result.fetchall()]

    @staticmethod
    async def upsert_stats(
        db: AsyncSession, post_id: str, stats: Dict[str, Any]
    ) -> Dict[str, Any]:
        await db.execute(
            sa_text(
                """
                INSERT INTO clip_post_stats
                    (post_id, views, likes, comments, earnings)
                VALUES (:post_id, :views, :likes, :comments, :earnings)
                ON CONFLICT (post_id) DO UPDATE SET
                    views = EXCLUDED.views,
                    likes = EXCLUDED.likes,
                    comments = EXCLUDED.comments,
                    earnings = EXCLUDED.earnings,
                    updated_at = CURRENT_TIMESTAMP
                """
            ),
            {"post_id": post_id, **stats},
        )
        await db.commit()
        result = await db.execute(
            sa_text(
                """
                SELECT post_id, views, likes, comments, earnings, updated_at
                FROM clip_post_stats
                WHERE post_id = :post_id
                """
            ),
            {"post_id": post_id},
        )
        return _stats_row(result.fetchone())
