"""
Recipe repository — the user's own saved creation-settings bundles.

Stores ONLY what the user picked: a name and the settings payload
that ``src.recipes`` validated against the task create path's own
validators. Nothing is inferred or filled in. Tasks that applied a
recipe record only its id in their source metadata, so deleting one
touches no task's stored choices.
"""

import json
from typing import Any, Dict, List, Optional

from sqlalchemy import text as sa_text
from sqlalchemy.ext.asyncio import AsyncSession

_RECIPE_COLUMNS = "id, user_id, name, settings, created_at, updated_at"


def _iso(value: Any) -> Optional[str]:
    return value.isoformat() if value is not None else None


def _settings(value: Any) -> Dict[str, Any]:
    if isinstance(value, dict):
        return value
    if isinstance(value, str) and value:
        return json.loads(value)
    return {}


def _recipe_row(row: Any) -> Dict[str, Any]:
    return {
        "id": row.id,
        "user_id": row.user_id,
        "name": row.name,
        "settings": _settings(row.settings),
        "created_at": _iso(row.created_at),
        "updated_at": _iso(row.updated_at),
    }


class RecipeRepository:
    """Raw-SQL repository for the recipes table."""

    @staticmethod
    async def list_recipes(
        db: AsyncSession, user_id: str
    ) -> List[Dict[str, Any]]:
        result = await db.execute(
            sa_text(
                f"""
                SELECT {_RECIPE_COLUMNS}
                FROM recipes
                WHERE user_id = :user_id
                ORDER BY created_at DESC
                """
            ),
            {"user_id": user_id},
        )
        return [_recipe_row(row) for row in result.fetchall()]

    @staticmethod
    async def get_recipe(
        db: AsyncSession, user_id: str, recipe_id: str
    ) -> Optional[Dict[str, Any]]:
        result = await db.execute(
            sa_text(
                f"""
                SELECT {_RECIPE_COLUMNS}
                FROM recipes
                WHERE id = :recipe_id AND user_id = :user_id
                """
            ),
            {"recipe_id": recipe_id, "user_id": user_id},
        )
        row = result.fetchone()
        return _recipe_row(row) if row is not None else None

    @staticmethod
    async def create_recipe(
        db: AsyncSession, recipe: Dict[str, Any]
    ) -> Dict[str, Any]:
        await db.execute(
            sa_text(
                """
                INSERT INTO recipes (id, user_id, name, settings)
                VALUES (:id, :user_id, :name, CAST(:settings AS jsonb))
                """
            ),
            {
                "id": recipe["id"],
                "user_id": recipe["user_id"],
                "name": recipe["name"],
                "settings": json.dumps(recipe.get("settings") or {}),
            },
        )
        await db.commit()
        return recipe

    @staticmethod
    async def update_recipe(
        db: AsyncSession, user_id: str, recipe_id: str, fields: Dict[str, Any]
    ) -> Optional[Dict[str, Any]]:
        if not fields:
            return await RecipeRepository.get_recipe(db, user_id, recipe_id)
        assignments = []
        params: Dict[str, Any] = {"recipe_id": recipe_id, "user_id": user_id}
        if "name" in fields:
            assignments.append("name = :name")
            params["name"] = fields["name"]
        if "settings" in fields:
            assignments.append("settings = CAST(:settings AS jsonb)")
            params["settings"] = json.dumps(fields["settings"] or {})
        result = await db.execute(
            sa_text(
                f"""
                UPDATE recipes
                SET {", ".join(assignments)}, updated_at = CURRENT_TIMESTAMP
                WHERE id = :recipe_id AND user_id = :user_id
                RETURNING id
                """
            ),
            params,
        )
        if result.scalar() is None:
            return None
        await db.commit()
        return await RecipeRepository.get_recipe(db, user_id, recipe_id)

    @staticmethod
    async def delete_recipe(
        db: AsyncSession, user_id: str, recipe_id: str
    ) -> bool:
        result = await db.execute(
            sa_text(
                """
                DELETE FROM recipes
                WHERE id = :recipe_id AND user_id = :user_id
                RETURNING id
                """
            ),
            {"recipe_id": recipe_id, "user_id": user_id},
        )
        deleted = result.scalar() is not None
        await db.commit()
        return deleted
