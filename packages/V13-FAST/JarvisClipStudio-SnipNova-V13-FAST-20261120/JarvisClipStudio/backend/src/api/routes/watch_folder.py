"""Watch-folder autopilot settings and activity.

``GET /watch-folder`` returns the caller's settings (with the
effective folder the watcher will scan) plus the recent files the
watcher has claimed. ``PUT /watch-folder/settings`` replaces the
settings: the enable toggle, an optional folder override, and the
task defaults applied to dropped files. Overrides are validated with
the same validators the create-task route uses, so a saved autopilot
default can never be a value a manual task would reject.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.ext.asyncio import AsyncSession

from ...ai import validate_target_clip_count
from ...auth_headers import resolve_authenticated_user_id
from ...auto_director.niche import validate_niche_override
from ...config import get_config
from ...database import get_db
from ...media.music_library import validate_music_mode, validate_music_override
from ...repositories.recipe_repository import RecipeRepository
from ...repositories.watch_folder_repository import WatchFolderRepository

logger = logging.getLogger(__name__)

router = APIRouter(tags=["watch folder"])

repository = WatchFolderRepository()

_PROCESSING_MODES = {"fast", "balanced", "quality"}


def _public_settings(settings: dict | None, user_id: str) -> dict:
    config = get_config()
    base = settings or {
        "user_id": user_id,
        "enabled": False,
        "folder_path": None,
        "niche_override": None,
        "music_override": None,
        "music_mode": None,
        "target_clip_count": None,
        "processing_mode": None,
        "recipe_id": None,
    }
    return {
        **base,
        "effective_folder": base.get("folder_path") or config.watch_folder_path,
        "default_folder": config.watch_folder_path,
    }


@router.get("/watch-folder")
async def get_watch_folder(request: Request, db: AsyncSession = Depends(get_db)):
    user_id = await resolve_authenticated_user_id(request, db, get_config())
    settings = await repository.get_settings(db, user_id)
    files = await repository.list_files(db, user_id, limit=50)
    return {
        "settings": _public_settings(settings, user_id),
        "files": files,
    }


@router.put("/watch-folder/settings")
async def update_watch_folder_settings(
    request: Request, db: AsyncSession = Depends(get_db)
):
    user_id = await resolve_authenticated_user_id(request, db, get_config())
    try:
        data = await request.json()
    except Exception:
        data = None
    if not isinstance(data, dict):
        raise HTTPException(status_code=400, detail="A settings object is required")

    enabled = data.get("enabled", False)
    if not isinstance(enabled, bool):
        raise HTTPException(status_code=422, detail="enabled must be true or false")

    folder_path = data.get("folder_path")
    if folder_path is not None:
        if not isinstance(folder_path, str) or not folder_path.strip():
            raise HTTPException(
                status_code=422,
                detail="folder_path must be a non-empty path or null",
            )
        folder_path = folder_path.strip()

    try:
        niche_override = validate_niche_override(data.get("niche_override"))
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None
    try:
        music_override = validate_music_override(data.get("music_override"))
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None
    try:
        music_mode = validate_music_mode(data.get("music_mode"))
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None
    try:
        target_clip_count = validate_target_clip_count(data.get("target_clip_count"))
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None

    processing_mode = data.get("processing_mode")
    if processing_mode is not None and processing_mode not in _PROCESSING_MODES:
        raise HTTPException(
            status_code=422,
            detail="processing_mode must be fast, balanced, quality or null",
        )

    # Recipes: the same user-scoped check the create path makes, at
    # save time so a bad id can never sit in the settings and silently
    # shape (or fail) a dropped file later. A recipe deleted after
    # saving is refused again by the file handler itself.
    recipe_id = data.get("recipe_id")
    if recipe_id is not None:
        if not isinstance(recipe_id, str) or not recipe_id.strip():
            raise HTTPException(
                status_code=422,
                detail="Invalid recipe_id. Use a recipe id or omit it.",
            )
        recipe_id = recipe_id.strip()
        recipe = await RecipeRepository.get_recipe(db, user_id, recipe_id)
        if recipe is None:
            raise HTTPException(status_code=422, detail="Recipe not found")

    settings = await repository.upsert_settings(db, user_id, {
        "enabled": enabled,
        "folder_path": folder_path,
        "niche_override": niche_override,
        "music_override": music_override,
        "music_mode": music_mode,
        "target_clip_count": target_clip_count,
        "processing_mode": processing_mode,
        "recipe_id": recipe_id,
    })
    return {"settings": _public_settings(settings, user_id)}
