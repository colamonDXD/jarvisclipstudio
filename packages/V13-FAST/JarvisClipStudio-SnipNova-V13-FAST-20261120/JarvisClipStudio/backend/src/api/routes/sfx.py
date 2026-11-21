"""
SFX library API routes (list, preview stream, upload).

The library has two roots — bundled ``assets/sfx`` (read-only) and the
writable user folder — see ``media.sfx_library``. These routes are the
clip editor's window onto it: list the placeable effects, stream one
for the preview player, and accept an upload into the user folder.
"""

import logging
import os
import uuid
from pathlib import Path

import aiofiles
from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse
from sqlalchemy.ext.asyncio import AsyncSession

from ...auth_headers import resolve_authenticated_user_id
from ...config import get_config
from ...database import get_db
from ...media.sfx_library import (
    MAX_UPLOAD_BYTES,
    UPLOAD_EXTENSIONS,
    ensure_user_sfx_dir,
    find_sfx_file,
    list_sfx_tracks,
    sanitize_sfx_filename,
    unique_sfx_filename,
)
from ...utils.async_helpers import run_in_thread

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/sfx", tags=["sfx"])

_MEDIA_TYPES = {
    ".mp3": "audio/mpeg",
    ".wav": "audio/wav",
    ".ogg": "audio/ogg",
    ".m4a": "audio/mp4",
    ".flac": "audio/flac",
    ".aac": "audio/aac",
    ".opus": "audio/opus",
    ".webm": "audio/webm",
}


async def _get_authenticated_user_id(request: Request, db: AsyncSession) -> str:
    config = get_config()
    return await resolve_authenticated_user_id(request, db, config)


@router.get("/library")
async def get_sfx_library(request: Request, db: AsyncSession = Depends(get_db)):
    """List every SFX file the renderer can place."""
    await _get_authenticated_user_id(request, db)
    tracks = await run_in_thread(list_sfx_tracks)
    return {"tracks": tracks}


@router.get("/file/{filename}")
async def get_sfx_file(
    filename: str, request: Request, db: AsyncSession = Depends(get_db)
):
    """Stream one SFX file (the editor panel's preview player source)."""
    await _get_authenticated_user_id(request, db)
    if filename != Path(filename).name or filename in {".", ".."}:
        raise HTTPException(status_code=400, detail="Invalid SFX filename")
    path = find_sfx_file(filename)
    if path is None:
        raise HTTPException(status_code=404, detail="SFX file not found")
    media_type = _MEDIA_TYPES.get(path.suffix.lower(), "application/octet-stream")
    return FileResponse(path, media_type=media_type, filename=filename)


@router.post("/upload", status_code=201)
async def upload_sfx_file(
    request: Request,
    db: AsyncSession = Depends(get_db),
    file: UploadFile = File(...),
):
    """Save an uploaded effect into the user SFX folder and return its entry."""
    await _get_authenticated_user_id(request, db)
    raw_name = file.filename or ""
    extension = Path(raw_name).suffix.lower()
    if extension not in UPLOAD_EXTENSIONS:
        raise HTTPException(
            status_code=400,
            detail=(
                "Unsupported SFX file type. Use one of: "
                + ", ".join(sorted(UPLOAD_EXTENSIONS))
            ),
        )
    filename = unique_sfx_filename(sanitize_sfx_filename(raw_name))
    folder = await run_in_thread(ensure_user_sfx_dir)
    destination = folder / filename
    temporary = folder / f".{filename}.part-{uuid.uuid4().hex[:8]}"
    written = 0
    try:
        async with aiofiles.open(temporary, "wb") as out:
            while chunk := await file.read(1024 * 1024):
                written += len(chunk)
                if written > MAX_UPLOAD_BYTES:
                    raise HTTPException(
                        status_code=413,
                        detail="SFX upload exceeds the 10 MB limit",
                    )
                await out.write(chunk)
        os.replace(temporary, destination)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise
    logger.info("SFX uploaded to library: %s (%d bytes)", filename, written)
    tracks = await run_in_thread(list_sfx_tracks)
    for track in tracks:
        if track["filename"] == filename:
            return track
    # The file is saved even if the listing race lost it; report it plainly.
    return {
        "filename": filename,
        "display_name": Path(filename).stem.replace("_", " ").replace("-", " "),
        "duration": None,
        "source": "user",
    }
