"""
Imported-LUT library API routes (list, upload, delete).

The library is folder-driven — see ``media.lut_library``. Uploads are
fully parsed and validated by ``auto_director.lut`` before anything is
stored: a malformed .cube gets a 400 with the parser's message and no
file is written, so the library can never contain a LUT the renderer
would choke on.
"""

import logging

from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile
from sqlalchemy.ext.asyncio import AsyncSession

from ...auth_headers import resolve_authenticated_user_id
from ...config import get_config
from ...database import get_db
from ...media.lut_library import delete_lut, list_luts, save_lut
from ...utils.async_helpers import run_in_thread

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/luts", tags=["luts"])


async def _get_authenticated_user_id(request: Request, db: AsyncSession) -> str:
    config = get_config()
    return await resolve_authenticated_user_id(request, db, config)


@router.get("/library")
async def get_lut_library(
    request: Request, db: AsyncSession = Depends(get_db)
) -> dict:
    """List the user's imported .cube LUTs."""
    await _get_authenticated_user_id(request, db)
    luts = await run_in_thread(list_luts)
    return {"luts": luts}


@router.post("/upload")
async def upload_lut(
    request: Request,
    file: UploadFile = File(...),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Validate and store one uploaded .cube file."""
    await _get_authenticated_user_id(request, db)
    data = await file.read()
    try:
        entry = await run_in_thread(save_lut, file.filename or "lut.cube", data)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return entry


@router.delete("/{filename}")
async def remove_lut(
    filename: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Delete one imported LUT by its stored filename."""
    await _get_authenticated_user_id(request, db)
    removed = await run_in_thread(delete_lut, filename)
    if not removed:
        raise HTTPException(status_code=404, detail="LUT not found")
    return {"deleted": filename}
