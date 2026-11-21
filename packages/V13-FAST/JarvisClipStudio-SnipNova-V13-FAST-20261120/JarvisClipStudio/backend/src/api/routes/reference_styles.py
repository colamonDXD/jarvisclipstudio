"""
Reference style API routes (Lane H2).

Turn a finished reference video — an uploaded file or a URL — into a
user niche profile via the Lane H analyser, then list and delete those
profiles. Storage and naming live in ``auto_director.user_profiles``:
files land in the user profiles directory, never in the stock tree, and
public names carry the ``user-`` prefix so they can be picked anywhere a
stock niche can (task creation validates them like any other override).

The analyser is not rewritten here: creation calls
``reference_style.analyse_reference`` and then proves the stored profile
loads through the real ``load_profile`` before reporting success.
"""

import logging
import shutil
import uuid
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import aiofiles
from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from sqlalchemy.ext.asyncio import AsyncSession

from ...auth_headers import resolve_authenticated_user_id
from ...auto_director.user_profiles import (
    USER_PROFILE_PREFIX,
    delete_user_profile,
    ensure_user_profiles_dir,
    is_user_profile_name,
    list_user_profiles,
    save_user_profile,
    slugify_style_name,
)
from ...config import get_config
from ...database import get_db
from ...media_storage import VIDEO_SUFFIXES
from ...utils.async_helpers import run_in_thread

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/reference-styles", tags=["reference-styles"])

_MAX_NAME_CHARS = 120


async def _get_authenticated_user_id(request: Request, db: AsyncSession) -> str:
    config = get_config()
    return await resolve_authenticated_user_id(request, db, config)


def _require_display_name(raw: str | None) -> str:
    display_name = (raw or "").strip()
    if not display_name:
        raise HTTPException(status_code=400, detail="Give the style a name first")
    if len(display_name) > _MAX_NAME_CHARS:
        raise HTTPException(
            status_code=400,
            detail=f"Style names are limited to {_MAX_NAME_CHARS} characters",
        )
    return display_name


def _analyse_and_store(
    video_path: Path, display_name: str, source_filename: str
) -> dict[str, Any]:
    """Run the Lane H analyser on a local file and store the profile.

    Raises whatever the analyser raises (unreadable file, no video
    stream) — the routes translate that into an honest 400.
    """
    from ...auto_director.edl import load_profile
    from ...auto_director.reference_style import analyse_reference

    measurements, profile, report = analyse_reference(video_path, name=display_name)
    if float(measurements["source"]["duration_sec"]) <= 0:
        raise ValueError("the file has no readable video duration")

    # One style per name: re-creating a style under a name the user
    # already used replaces that style (same slug, same public name,
    # files overwritten atomically by save_user_profile). Minting a
    # suffixed copy instead (user-funky, user-funky-2, ...) left the
    # list and the picker showing several indistinguishable entries
    # with the same display name — the duplicate "funky" entries from
    # the Nov 9 live report. Saving over your own named style is the
    # behaviour the name field promises.
    slug = slugify_style_name(display_name)
    public_name = f"{USER_PROFILE_PREFIX}{slug}"
    profile = dict(profile)
    profile["niche"] = public_name
    reference = dict(profile.get("reference_style") or {})
    # The analyser saw a temp file; record the name the user actually gave.
    reference["source_file"] = source_filename
    profile["reference_style"] = reference

    summary = save_user_profile(
        slug, profile, {"measurements": measurements, "mapping": report}
    )
    try:
        # Prove the stored profile loads through the real loader before
        # calling this a success; a profile nothing can load is deleted.
        load_profile(public_name)
    except Exception:
        delete_user_profile(public_name)
        raise
    summary["inferred"] = report.get("inferred", {})
    summary["not_inferred"] = report.get("not_inferred", [])
    return summary


def _download_reference_video(url: str, dest_dir: Path) -> Path:
    """Download one reference video with yt-dlp (single file, no merging).

    Raises RuntimeError carrying yt-dlp's own message on failure — the
    route turns that into a specific 400 rather than pretending the
    style was created.
    """
    import yt_dlp

    options = {
        "quiet": True,
        "no_warnings": True,
        "noplaylist": True,
        "format": "best[ext=mp4]/best",
        "outtmpl": str(dest_dir / "reference.%(ext)s"),
        "max_filesize": get_config().max_video_upload_bytes,
        "socket_timeout": 30,
        "retries": 3,
    }
    try:
        with yt_dlp.YoutubeDL(options) as ydl:
            ydl.download([url])
    except Exception as exc:  # yt-dlp raises DownloadError and friends
        raise RuntimeError(str(exc)[:300]) from exc
    files = [
        entry
        for entry in dest_dir.iterdir()
        if entry.is_file() and entry.suffix.lower() in VIDEO_SUFFIXES
    ]
    if not files:
        raise RuntimeError("the download finished without producing a video file")
    return files[0]


@router.get("")
async def get_reference_styles(request: Request, db: AsyncSession = Depends(get_db)):
    """List the user's saved reference styles."""
    await _get_authenticated_user_id(request, db)
    return {"profiles": await run_in_thread(list_user_profiles)}


@router.post("/upload", status_code=201)
async def create_reference_style_from_upload(
    request: Request,
    db: AsyncSession = Depends(get_db),
    file: UploadFile = File(...),
    name: str = Form(""),
):
    """Analyse an uploaded reference video and save it as a style."""
    await _get_authenticated_user_id(request, db)
    display_name = _require_display_name(name)
    raw_name = file.filename or ""
    extension = Path(raw_name).suffix.lower()
    if extension not in VIDEO_SUFFIXES:
        raise HTTPException(
            status_code=400,
            detail=(
                "Unsupported video file type. Use one of: "
                + ", ".join(sorted(VIDEO_SUFFIXES))
            ),
        )
    max_bytes = get_config().max_video_upload_bytes
    folder = await run_in_thread(ensure_user_profiles_dir)
    temporary = folder / f".incoming-{uuid.uuid4().hex[:12]}{extension}"
    written = 0
    try:
        async with aiofiles.open(temporary, "wb") as out:
            while chunk := await file.read(1024 * 1024):
                written += len(chunk)
                if written > max_bytes:
                    raise HTTPException(
                        status_code=413,
                        detail="Reference video exceeds the upload size limit",
                    )
                await out.write(chunk)
        try:
            return await run_in_thread(
                _analyse_and_store, temporary, display_name, raw_name
            )
        except HTTPException:
            raise
        except Exception as exc:
            logger.warning("Reference style analysis failed for %s: %s", raw_name, exc)
            raise HTTPException(
                status_code=400,
                detail=f"Could not analyse that video: {str(exc)[:300]}",
            ) from exc
    finally:
        temporary.unlink(missing_ok=True)


@router.post("/from-url", status_code=201)
async def create_reference_style_from_url(
    request: Request, db: AsyncSession = Depends(get_db)
):
    """Download a reference video from a URL and save it as a style."""
    await _get_authenticated_user_id(request, db)
    try:
        payload = await request.json()
    except Exception:
        raise HTTPException(
            status_code=400, detail="Expected a JSON body with 'url' and 'name'"
        )
    display_name = _require_display_name(str(payload.get("name") or ""))
    url = str(payload.get("url") or "").strip()
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise HTTPException(
            status_code=400, detail="That does not look like a valid http(s) URL"
        )
    folder = await run_in_thread(ensure_user_profiles_dir)
    incoming = folder / f".incoming-{uuid.uuid4().hex[:12]}"
    incoming.mkdir(parents=True, exist_ok=True)
    try:
        try:
            video_path = await run_in_thread(_download_reference_video, url, incoming)
        except Exception as exc:
            logger.warning("Reference style download failed for %s: %s", url, exc)
            raise HTTPException(
                status_code=400,
                detail=f"Could not download that URL: {str(exc)[:300]}",
            ) from exc
        try:
            return await run_in_thread(
                _analyse_and_store, video_path, display_name, url
            )
        except Exception as exc:
            logger.warning("Reference style analysis failed for %s: %s", url, exc)
            raise HTTPException(
                status_code=400,
                detail=f"Could not analyse that video: {str(exc)[:300]}",
            ) from exc
    finally:
        shutil.rmtree(incoming, ignore_errors=True)


@router.delete("/{name}")
async def delete_reference_style(
    name: str, request: Request, db: AsyncSession = Depends(get_db)
):
    """Delete one of the user's reference styles (stock styles excluded)."""
    await _get_authenticated_user_id(request, db)
    if not is_user_profile_name(name):
        raise HTTPException(
            status_code=400,
            detail="Only reference styles (user-...) can be deleted",
        )
    if not await run_in_thread(delete_user_profile, name):
        raise HTTPException(status_code=404, detail="Reference style not found")
    return {"deleted": name}
