"""
Brand kit repository — the user's own reusable clip branding.

Stores ONLY what the user typed or picked in the Brand Kit settings:
colours, a caption template, a font, a title treatment, a default music
track and a logo. Nothing is scraped, inferred or filled in. Deleting a
kit also clears the opt-in on that user's tasks, so a task never points
at a kit that no longer exists.
"""

from typing import Any, Dict, List, Optional

from sqlalchemy import text as sa_text
from sqlalchemy.ext.asyncio import AsyncSession

_KIT_COLUMNS = (
    "id, user_id, name, primary_color, accent_color, caption_template, "
    "font_name, title_style, music_track, logo_filename, logo_position, "
    "logo_opacity, intro_asset, outro_asset, created_at, updated_at"
)


def _iso(value: Any) -> Optional[str]:
    return value.isoformat() if value is not None else None


def _kit_row(row: Any) -> Dict[str, Any]:
    return {
        "id": row.id,
        "user_id": row.user_id,
        "name": row.name,
        "primary_color": row.primary_color,
        "accent_color": row.accent_color,
        "caption_template": row.caption_template,
        "font_name": row.font_name,
        "title_style": row.title_style,
        "music_track": row.music_track,
        "logo_filename": row.logo_filename,
        "logo_position": row.logo_position or "bottom_right",
        "logo_opacity": float(row.logo_opacity)
        if row.logo_opacity is not None
        else 0.85,
        "intro_asset": row.intro_asset,
        "outro_asset": row.outro_asset,
        "created_at": _iso(row.created_at),
        "updated_at": _iso(row.updated_at),
    }


class BrandKitRepository:
    """Raw-SQL repository for the brand_kits table."""

    @staticmethod
    async def list_kits(
        db: AsyncSession, user_id: str
    ) -> List[Dict[str, Any]]:
        result = await db.execute(
            sa_text(
                f"""
                SELECT {_KIT_COLUMNS}
                FROM brand_kits
                WHERE user_id = :user_id
                ORDER BY created_at DESC
                """
            ),
            {"user_id": user_id},
        )
        return [_kit_row(row) for row in result.fetchall()]

    @staticmethod
    async def get_kit(
        db: AsyncSession, user_id: str, kit_id: str
    ) -> Optional[Dict[str, Any]]:
        result = await db.execute(
            sa_text(
                f"""
                SELECT {_KIT_COLUMNS}
                FROM brand_kits
                WHERE id = :kit_id AND user_id = :user_id
                """
            ),
            {"kit_id": kit_id, "user_id": user_id},
        )
        row = result.fetchone()
        return _kit_row(row) if row is not None else None

    @staticmethod
    async def create_kit(
        db: AsyncSession, kit: Dict[str, Any]
    ) -> Dict[str, Any]:
        await db.execute(
            sa_text(
                """
                INSERT INTO brand_kits
                    (id, user_id, name, primary_color, accent_color,
                     caption_template, font_name, title_style, music_track,
                     logo_filename, logo_position, logo_opacity,
                     intro_asset, outro_asset)
                VALUES
                    (:id, :user_id, :name, :primary_color, :accent_color,
                     :caption_template, :font_name, :title_style,
                     :music_track, :logo_filename, :logo_position,
                     :logo_opacity, :intro_asset, :outro_asset)
                """
            ),
            kit,
        )
        await db.commit()
        return kit

    @staticmethod
    async def update_kit(
        db: AsyncSession, user_id: str, kit_id: str, fields: Dict[str, Any]
    ) -> Optional[Dict[str, Any]]:
        if not fields:
            return await BrandKitRepository.get_kit(db, user_id, kit_id)
        assignments = ", ".join(f"{key} = :{key}" for key in fields)
        result = await db.execute(
            sa_text(
                f"""
                UPDATE brand_kits
                SET {assignments}, updated_at = CURRENT_TIMESTAMP
                WHERE id = :kit_id AND user_id = :user_id
                RETURNING id
                """
            ),
            {**fields, "kit_id": kit_id, "user_id": user_id},
        )
        if result.scalar() is None:
            return None
        await db.commit()
        return await BrandKitRepository.get_kit(db, user_id, kit_id)

    @staticmethod
    async def delete_kit(db: AsyncSession, user_id: str, kit_id: str) -> bool:
        await db.execute(
            sa_text(
                """
                UPDATE tasks SET brand_kit_id = NULL,
                       updated_at = CURRENT_TIMESTAMP
                WHERE brand_kit_id = :kit_id AND user_id = :user_id
                """
            ),
            {"kit_id": kit_id, "user_id": user_id},
        )
        result = await db.execute(
            sa_text(
                """
                DELETE FROM brand_kits
                WHERE id = :kit_id AND user_id = :user_id
                RETURNING id
                """
            ),
            {"kit_id": kit_id, "user_id": user_id},
        )
        deleted = result.scalar() is not None
        await db.commit()
        return deleted
