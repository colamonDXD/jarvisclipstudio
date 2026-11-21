"""Live clipping API (``LIVE_CLIPPING``, off by default: every route 403s).

- ``GET  /live/status``                   -> {"enabled": bool}; never 403, so clients can hide the feature
- ``POST /live/sessions``                 -> start clipping a live stream (one active session per user)
- ``GET  /live/sessions``                 -> the caller's sessions, newest first
- ``GET  /live/sessions/{id}``            -> one session with its clips (poll every few seconds)
- ``POST /live/sessions/{id}/stop``       -> stop recording (queued clips still finish)
- ``DELETE /live/sessions/{id}``          -> delete a finished session and its files
- ``GET  /live/clips/{id}/file?kind=clip|preview`` -> the MP4
- ``GET  /live/frame?url=...&at=...`` -> one JPEG frame of a stream page (for drawing layout boxes)

Body of ``POST /live/sessions``: ``{"url", "layout_template"?: "facecam_red" | "facecam_gold" | null,
"credit"?, "hook_title"?, "niche"?, "streamer_words_only"?: bool, "max_clips"?: 1..60,
"boxes"?: {"cam": box, "game": box, "arrange"?: "top" | "bottom"}}``.
The URL must be a YouTube or Twitch page that is live right now.
"""
from __future__ import annotations

import logging
import os
import shutil
import subprocess
from pathlib import Path
from typing import Any, Dict

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse, Response
from sqlalchemy.ext.asyncio import AsyncSession

from ...auth_headers import resolve_authenticated_user_id
from ...auto_director.niche import validate_niche_override
from ...config import get_config
from ...database import get_db
from ...live import live_clipping_enabled, yt_dlp_binary
from ...live.finisher import LAYOUTS
from ...live.recorder import _is_page_url
from ...repositories.live_repository import LiveRepository
from ...utils.async_helpers import run_in_thread

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/live", tags=["live clipping"])
repository = LiveRepository()



def _require_enabled() -> None:
    if not live_clipping_enabled():
        raise HTTPException(status_code=403, detail="Live clipping is not enabled on this server")


def live_root() -> Path:
    return Path(os.getenv("TEMP_DIR", "/tmp")) / "live"


def probe_live_status(url: str) -> str:
    """yt-dlp's ``live_status`` for a page: is_live, was_live, not_live, is_upcoming, or error."""
    try:
        out = subprocess.run([yt_dlp_binary(), "-q", "--no-warnings", "--skip-download", "--print", "live_status", url],
                             capture_output=True, text=True, timeout=45)
    except (OSError, subprocess.TimeoutExpired) as exc:
        logger.warning("Live status probe failed: %s", exc)
        return "error"
    lines = [line.strip() for line in out.stdout.splitlines() if line.strip()]
    return lines[0] if lines else "error"


async def _json_body(request: Request) -> Dict[str, Any]:
    try:
        data = await request.json()
    except Exception:
        raise HTTPException(status_code=422, detail="Body must be JSON") from None
    if not isinstance(data, dict):
        raise HTTPException(status_code=422, detail="Body must be a JSON object")
    return data


def _text(data: Dict[str, Any], key: str, limit: int) -> Any:
    value = data.get(key)
    if value is None:
        return None
    if not isinstance(value, str):
        raise HTTPException(status_code=422, detail=f"{key} must be text")
    value = value.strip()
    if len(value) > limit:
        raise HTTPException(status_code=422, detail=f"{key} is longer than {limit} characters")
    return value or None


def _voice_fx(value: Any) -> Any:
    if value in (None, "", "off", "none"):
        return None
    if value not in ("high", "deep"):
        raise HTTPException(status_code=422, detail="voice_fx must be off, high or deep")
    return value


def validate_settings(data: Dict[str, Any]) -> Dict[str, Any]:
    layout = data.get("layout_template", "facecam_red")
    if layout is not None and layout not in LAYOUTS:
        raise HTTPException(status_code=422, detail=f"layout_template must be one of {list(LAYOUTS)} or null")
    try:
        niche = validate_niche_override(data.get("niche")) or "gaming"
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None
    words_only = data.get("streamer_words_only", True)
    auto_title = data.get("auto_title", True)
    if not isinstance(auto_title, bool):
        raise HTTPException(status_code=422, detail="auto_title must be true or false")
    if not isinstance(words_only, bool):
        raise HTTPException(status_code=422, detail="streamer_words_only must be true or false")
    max_clips = data.get("max_clips", 30)
    if not isinstance(max_clips, int) or isinstance(max_clips, bool) or not 1 <= max_clips <= 60:
        raise HTTPException(status_code=422, detail="max_clips must be a whole number from 1 to 60")
    return {"layout_template": layout, "niche": niche, "streamer_words_only": words_only, "max_clips": max_clips,
            "credit": _text(data, "credit", 40), "hook_title": _text(data, "hook_title", 80), "auto_title": auto_title,
            "request": _text(data, "request", 200), "voice_fx": _voice_fx(data.get("voice_fx")),
            "boxes": _boxes(data.get("boxes"))}


def _boxes(value: Any) -> Any:
    from ...media.gaming_templates import validate_boxes

    try:
        return validate_boxes(value)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None


@router.get("/status")
async def live_status():
    return {"enabled": live_clipping_enabled()}


@router.post("/sessions", status_code=201)
async def start_session(request: Request, db: AsyncSession = Depends(get_db)):
    _require_enabled()
    user_id = await resolve_authenticated_user_id(request, db, get_config())
    data = await _json_body(request)
    url = _text(data, "url", 500)
    if not url or not _is_page_url(url):
        raise HTTPException(status_code=422, detail="Paste a YouTube, Twitch or Kick stream link")
    settings = validate_settings(data)
    if await repository.count_active(db, user_id):
        raise HTTPException(status_code=409, detail="You already have a live session running. Stop it first.")
    status = await run_in_thread(probe_live_status, url)
    if status in ("was_live", "not_live", "post_live"):
        # A finished stream or a long video: the fast path (audio only, the
        # best moments, then only those pieces downloaded) - live/vod.py.
        settings["mode"] = "vod"
        if "max_clips" not in data:
            settings["max_clips"] = 10
    elif status != "is_live":
        detail = {"is_upcoming": "This stream hasn't started yet. Start clipping once it's live.",
                  }.get(status, "We couldn't open this video. Check the link.")
        raise HTTPException(status_code=422, detail={"code": f"LIVE_{status.upper()}", "message": detail})
    session = await repository.create_session(db, user_id, url, settings)
    return {"session": session}


@router.get("/sessions")
async def list_sessions(request: Request, db: AsyncSession = Depends(get_db)):
    _require_enabled()
    user_id = await resolve_authenticated_user_id(request, db, get_config())
    return {"sessions": await repository.list_sessions(db, user_id)}


@router.get("/sessions/{session_id}")
async def get_session(session_id: str, request: Request, db: AsyncSession = Depends(get_db)):
    _require_enabled()
    user_id = await resolve_authenticated_user_id(request, db, get_config())
    session = await repository.get_session(db, session_id, user_id)
    if session is None:
        raise HTTPException(status_code=404, detail="Live session not found")
    return {"session": session, "clips": await repository.list_clips(db, session_id)}


@router.post("/sessions/{session_id}/stop")
async def stop_session(session_id: str, request: Request, db: AsyncSession = Depends(get_db)):
    _require_enabled()
    user_id = await resolve_authenticated_user_id(request, db, get_config())
    if not await repository.request_stop(db, session_id, user_id):
        if await repository.get_session(db, session_id, user_id) is None:
            raise HTTPException(status_code=404, detail="Live session not found")
        raise HTTPException(status_code=409, detail="This live session is not running")
    return {"session": await repository.get_session(db, session_id, user_id)}


@router.delete("/sessions/{session_id}")
async def delete_session(session_id: str, request: Request, db: AsyncSession = Depends(get_db)):
    _require_enabled()
    user_id = await resolve_authenticated_user_id(request, db, get_config())
    if not await repository.delete_session(db, session_id, user_id):
        if await repository.get_session(db, session_id, user_id) is None:
            raise HTTPException(status_code=404, detail="Live session not found")
        raise HTTPException(status_code=409, detail="Stop the live session before deleting it")
    folder = live_root() / session_id
    if folder.is_dir():
        await run_in_thread(shutil.rmtree, folder, True)
    return {"deleted": True}


@router.post("/clips/{clip_id}/library")
async def save_to_library(clip_id: str, request: Request, db: AsyncSession = Depends(get_db)):
    from ...live.library import save_live_clip

    _require_enabled()
    user_id = await resolve_authenticated_user_id(request, db, get_config())
    saved = await save_live_clip(db, user_id, clip_id)
    if not saved:
        raise HTTPException(status_code=404, detail="Finished clip unavailable")
    return saved


@router.get("/clips/{clip_id}/file")
async def clip_file(clip_id: str, request: Request, kind: str = "clip", db: AsyncSession = Depends(get_db)):
    _require_enabled()
    if kind not in ("clip", "preview"):
        raise HTTPException(status_code=422, detail="kind must be clip or preview")
    user_id = await resolve_authenticated_user_id(request, db, get_config())
    path = await repository.get_clip_file(db, clip_id, user_id, kind)
    if not path:
        raise HTTPException(status_code=404, detail="File not ready")
    file = Path(path).resolve()
    root = live_root().resolve()
    if root not in file.parents or not file.is_file():
        raise HTTPException(status_code=404, detail="File not found")
    return FileResponse(file, media_type="video/mp4", filename=f"live_{kind}_{clip_id[:8]}.mp4")


def grab_frame_jpeg(url: str, at: float) -> bytes:
    """One JPEG frame of a stream page at ``at`` seconds (blocking; run in a thread).

    Resolves a direct media URL with yt-dlp, then input-seeks with ffmpeg.
    Raises HTTPException(502) when the frame cannot be grabbed.
    """
    try:
        resolved = subprocess.run(
            [yt_dlp_binary(), "-g", "-f", "best[height<=720]/bv*[height<=720]", url],
            capture_output=True, text=True, timeout=45)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise HTTPException(status_code=502, detail="Could not open that stream link") from exc
    direct = (resolved.stdout or "").strip().splitlines()
    if resolved.returncode != 0 or not direct:
        raise HTTPException(status_code=502, detail="Could not open that stream link")
    try:
        shot = subprocess.run(
            ["ffmpeg", "-v", "error", "-ss", f"{max(0.0, at):.3f}", "-i", direct[0],
             "-frames:v", "1", "-q:v", "3", "-f", "image2pipe", "-vcodec", "mjpeg", "-"],
            capture_output=True, timeout=60)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise HTTPException(status_code=502, detail="Could not grab a frame from that stream") from exc
    if shot.returncode != 0 or not shot.stdout:
        raise HTTPException(status_code=502, detail="Could not grab a frame from that stream")
    return shot.stdout


@router.get("/frame")
async def stream_frame(request: Request, url: str = "", at: float = 0.0, db: AsyncSession = Depends(get_db)):
    _require_enabled()
    await resolve_authenticated_user_id(request, db, get_config())
    if not url or not _is_page_url(url):
        raise HTTPException(status_code=422, detail="Paste a YouTube, Twitch or Kick stream link")
    if at < 0 or at > 36000:
        raise HTTPException(status_code=422, detail="at must be between 0 and 36000 seconds")
    jpeg = await run_in_thread(grab_frame_jpeg, url, at)
    return Response(content=jpeg, media_type="image/jpeg")
