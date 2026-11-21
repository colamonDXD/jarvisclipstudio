"""
Brand Kit API routes.

A brand kit is the user's own reusable branding — caption colours and
template, a caption font, a hook-title treatment, a default music
track and a logo watermark — applied to a task's clips only when the
task opts in (``brand_kit_id`` on task creation). These routes are the
settings surface: kit CRUD plus the logo upload. Application lives in
``auto_director.brand_kit`` and the render pipeline; nothing here
renders or guesses — every stored value is one the user picked.
"""

import logging
import os
import uuid
from pathlib import Path
from typing import Any

import aiofiles
from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile
from sqlalchemy.ext.asyncio import AsyncSession

from ...auth_headers import resolve_authenticated_user_id
from ...auto_director import brand_kit as bk
from ...config import get_config
from ...database import get_db
from ...repositories.brand_kit_repository import BrandKitRepository
from ...utils.async_helpers import run_in_thread

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/brand-kits", tags=["brand-kits"])


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


def _public_kit(kit: dict, user_id: str) -> dict:
    """The stored kit plus whether its logo file is really on disk."""
    public = dict(kit)
    public["logo_present"] = bk.logo_path(user_id, kit.get("logo_filename")) is not None
    return public


async def _validated_fields(payload: dict, *, partial: bool) -> dict:
    """Run the payload validators (font check touches Fontconfig → thread)."""
    try:
        return await run_in_thread(bk.validate_kit_payload, payload, partial=partial)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None


@router.get("")
async def list_brand_kits(request: Request, db: AsyncSession = Depends(get_db)):
    """Every brand kit the user saved, newest first."""
    user_id = await _get_authenticated_user_id(request, db)
    kits = await BrandKitRepository.list_kits(db, user_id)
    return {"kits": [_public_kit(kit, user_id) for kit in kits]}


@router.post("", status_code=201)
async def create_brand_kit(request: Request, db: AsyncSession = Depends(get_db)):
    """Create a kit from validated fields; invalid input persists nothing."""
    user_id = await _get_authenticated_user_id(request, db)
    payload = await _read_json_object(request)
    fields = await _validated_fields(payload, partial=False)
    kit = {
        "id": bk.new_kit_id(),
        "user_id": user_id,
        "name": fields["name"],
        "primary_color": fields.get("primary_color"),
        "accent_color": fields.get("accent_color"),
        "caption_template": fields.get("caption_template"),
        "font_name": fields.get("font_name"),
        "title_style": fields.get("title_style"),
        "music_track": fields.get("music_track"),
        "logo_filename": None,
        "logo_position": fields.get("logo_position", "bottom_right"),
        "logo_opacity": fields.get("logo_opacity", 0.85),
        "intro_asset": fields.get("intro_asset"),
        "outro_asset": fields.get("outro_asset"),
    }
    stored = await BrandKitRepository.create_kit(db, kit)
    return _public_kit({**kit, **stored, "created_at": None, "updated_at": None}, user_id)


@router.get("/{kit_id}")
async def get_brand_kit(
    kit_id: str, request: Request, db: AsyncSession = Depends(get_db)
):
    user_id = await _get_authenticated_user_id(request, db)
    kit = await BrandKitRepository.get_kit(db, user_id, kit_id)
    if kit is None:
        raise HTTPException(status_code=404, detail="Brand kit not found")
    return _public_kit(kit, user_id)


@router.patch("/{kit_id}")
async def update_brand_kit(
    kit_id: str, request: Request, db: AsyncSession = Depends(get_db)
):
    """Update the fields present in the body; the rest stay as saved."""
    user_id = await _get_authenticated_user_id(request, db)
    payload = await _read_json_object(request)
    fields = await _validated_fields(payload, partial=True)
    updated = await BrandKitRepository.update_kit(db, user_id, kit_id, fields)
    if updated is None:
        raise HTTPException(status_code=404, detail="Brand kit not found")
    return _public_kit(updated, user_id)


@router.delete("/{kit_id}")
async def delete_brand_kit(
    kit_id: str, request: Request, db: AsyncSession = Depends(get_db)
):
    """Delete a kit, its logo file, and the opt-in on tasks that used it."""
    user_id = await _get_authenticated_user_id(request, db)
    kit = await BrandKitRepository.get_kit(db, user_id, kit_id)
    if kit is None:
        raise HTTPException(status_code=404, detail="Brand kit not found")
    await BrandKitRepository.delete_kit(db, user_id, kit_id)
    await run_in_thread(bk.delete_logo_file, user_id, kit.get("logo_filename"))
    return {"deleted": True, "id": kit_id}


@router.post("/{kit_id}/logo", status_code=201)
async def upload_brand_logo(
    kit_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
    file: UploadFile = File(...),
):
    """Store a logo image for the kit through the brand asset folder.

    The file lands in the same shared tree the render workers read
    (next to user music and user profiles), so a logo uploaded here is
    the exact file the EDL renderer composites into clips.
    """
    user_id = await _get_authenticated_user_id(request, db)
    kit = await BrandKitRepository.get_kit(db, user_id, kit_id)
    if kit is None:
        raise HTTPException(status_code=404, detail="Brand kit not found")
    raw_name = file.filename or ""
    extension = Path(raw_name).suffix.lower()
    if extension not in bk.LOGO_EXTENSIONS:
        raise HTTPException(
            status_code=400,
            detail=(
                "Unsupported logo file type. Use one of: "
                + ", ".join(sorted(bk.LOGO_EXTENSIONS))
            ),
        )
    folder = bk.user_brand_dir(user_id)
    folder.mkdir(parents=True, exist_ok=True)
    temporary = folder / f".logo-{uuid.uuid4().hex[:8]}.part"
    written = 0
    try:
        async with aiofiles.open(temporary, "wb") as out:
            while chunk := await file.read(1024 * 1024):
                written += len(chunk)
                if written > bk.MAX_LOGO_BYTES:
                    raise HTTPException(
                        status_code=413, detail="Logo upload exceeds the 10 MB limit"
                    )
                await out.write(chunk)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise
    filename = await run_in_thread(bk.store_logo, user_id, temporary, raw_name)
    temporary.unlink(missing_ok=True)
    old_filename = kit.get("logo_filename")
    updated = await BrandKitRepository.update_kit(
        db, user_id, kit_id, {"logo_filename": filename}
    )
    if old_filename and old_filename != filename:
        await run_in_thread(bk.delete_logo_file, user_id, old_filename)
    logger.info("Brand logo stored for kit %s: %s (%d bytes)", kit_id, filename, written)
    return _public_kit(updated or kit, user_id)


@router.delete("/{kit_id}/logo")
async def delete_brand_logo(
    kit_id: str, request: Request, db: AsyncSession = Depends(get_db)
):
    user_id = await _get_authenticated_user_id(request, db)
    kit = await BrandKitRepository.get_kit(db, user_id, kit_id)
    if kit is None:
        raise HTTPException(status_code=404, detail="Brand kit not found")
    await BrandKitRepository.update_kit(db, user_id, kit_id, {"logo_filename": None})
    await run_in_thread(bk.delete_logo_file, user_id, kit.get("logo_filename"))
    updated = await BrandKitRepository.get_kit(db, user_id, kit_id)
    return _public_kit(updated or kit, user_id)
