"""
Recipe API routes.

A recipe is the user's own reusable bundle of creation settings —
template mode, niche, music, B-roll, captions, brand kit and the
other create-form controls — saved under a name so the same choices
can be applied to future tasks. These routes are the storage surface:
recipe CRUD plus the apply fragment (the exact task-create payload
the bundle contributes). Validation lives in ``src.recipes`` and
reuses the create path's own validators; nothing here renders. Task
creation reads recipes through ``POST /tasks`` with ``recipe_id``:
the fragment is merged under the request's explicit choices in
``api.routes.tasks._create_task`` and the applied recipe is recorded
on the task's source metadata.
"""

import logging
from typing import Any, Dict

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.ext.asyncio import AsyncSession

from ... import recipes as recipe_domain
from ...auth_headers import resolve_authenticated_user_id
from ...config import get_config
from ...database import get_db
from ...repositories.recipe_repository import RecipeRepository

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/recipes", tags=["recipes"])


async def _get_authenticated_user_id(request: Request, db: AsyncSession) -> str:
    config = get_config()
    return await resolve_authenticated_user_id(request, db, config)


async def _read_json_object(request: Request) -> dict:
    try:
        payload = await request.json()
    except (ValueError, UnicodeDecodeError):
        raise HTTPException(status_code=400, detail="Invalid JSON body")
    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail="Request body must be an object")
    return payload


def _validated_fields(payload: dict, *, partial: bool) -> Dict[str, Any]:
    try:
        return recipe_domain.validate_recipe_payload(payload, partial=partial)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None


@router.get("")
async def list_recipes(request: Request, db: AsyncSession = Depends(get_db)):
    """Every recipe the user saved, newest first."""
    user_id = await _get_authenticated_user_id(request, db)
    recipes = await RecipeRepository.list_recipes(db, user_id)
    return {"recipes": recipes}


@router.post("", status_code=201)
async def create_recipe(request: Request, db: AsyncSession = Depends(get_db)):
    """Create a recipe from validated fields; invalid input persists nothing."""
    user_id = await _get_authenticated_user_id(request, db)
    payload = await _read_json_object(request)
    fields = _validated_fields(payload, partial=False)
    recipe = {
        "id": recipe_domain.new_recipe_id(),
        "user_id": user_id,
        "name": fields["name"],
        "settings": fields["settings"],
    }
    await RecipeRepository.create_recipe(db, recipe)
    stored = await RecipeRepository.get_recipe(db, user_id, recipe["id"])
    return stored or {**recipe, "created_at": None, "updated_at": None}


@router.get("/{recipe_id}")
async def get_recipe(
    recipe_id: str, request: Request, db: AsyncSession = Depends(get_db)
):
    user_id = await _get_authenticated_user_id(request, db)
    recipe = await RecipeRepository.get_recipe(db, user_id, recipe_id)
    if recipe is None:
        raise HTTPException(status_code=404, detail="Recipe not found")
    return recipe


@router.patch("/{recipe_id}")
async def update_recipe(
    recipe_id: str, request: Request, db: AsyncSession = Depends(get_db)
):
    """Update the fields present in the body; the rest stay as saved.

    A present ``settings`` replaces the whole bundle — it is validated
    as a complete settings object, never merged sight-unseen.
    """
    user_id = await _get_authenticated_user_id(request, db)
    payload = await _read_json_object(request)
    fields = _validated_fields(payload, partial=True)
    updated = await RecipeRepository.update_recipe(db, user_id, recipe_id, fields)
    if updated is None:
        raise HTTPException(status_code=404, detail="Recipe not found")
    return updated


@router.delete("/{recipe_id}")
async def delete_recipe(
    recipe_id: str, request: Request, db: AsyncSession = Depends(get_db)
):
    """Delete a recipe. Tasks that applied it keep only the recorded
    recipe id in their source metadata — the id then names a bundle
    that no longer exists, while the task's own stored choices are
    unaffected — so deletion touches no task."""
    user_id = await _get_authenticated_user_id(request, db)
    recipe = await RecipeRepository.get_recipe(db, user_id, recipe_id)
    if recipe is None:
        raise HTTPException(status_code=404, detail="Recipe not found")
    await RecipeRepository.delete_recipe(db, user_id, recipe_id)
    return {"deleted": True, "id": recipe_id}


@router.get("/{recipe_id}/fragment")
async def get_recipe_fragment(
    recipe_id: str, request: Request, db: AsyncSession = Depends(get_db)
):
    """The apply-path: the exact task-create payload fragment this
    recipe contributes — its stored settings, nothing added."""
    user_id = await _get_authenticated_user_id(request, db)
    recipe = await RecipeRepository.get_recipe(db, user_id, recipe_id)
    if recipe is None:
        raise HTTPException(status_code=404, detail="Recipe not found")
    return {
        "recipe_id": recipe_id,
        "fragment": recipe_domain.create_fragment(recipe),
    }
