"""
Publishing workspace API routes.

The workspace PREPARES posts and keeps the user's own records: it never
publishes anything. The user posts by hand in the platform app, then
records the post URL here; performance numbers exist only when the user
types them in. Endpoints:

* ``GET    /publishing/board`` — every finished clip with per-platform
  readiness judged from recorded facts, its post records and campaign.
* ``GET    /publishing/clips/{clip_id}/bundle`` — the clip's export
  bundle (clip, cover, post.txt and records) as one ZIP download.
* Campaigns CRUD under ``/publishing/campaigns``.
* Post records under ``/publishing/posts`` plus user-entered stats at
  ``PUT /publishing/posts/{post_id}/stats``.
* ``GET    /publishing/analytics`` — production aggregates from stored
  pipeline rows, and performance rollups labelled ``entered_by_user``.
"""

import logging
import tempfile
import uuid
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.background import BackgroundTask

from ...auth_headers import resolve_authenticated_user_id
from ...config import get_config
from ...database import get_db
from ...media_storage import MediaStorage
from ...repositories.publishing_repository import PublishingRepository
from ...services import publishing_service as publishing
from ...utils.async_helpers import run_in_thread

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/publishing", tags=["publishing"])

POST_PLATFORMS = (*publishing.PLATFORMS, "other")
_CAMPAIGN_FIELDS = (
    "name", "platform", "campaign_url", "budget", "notes", "brief", "example_url",
)


def _validated_brief(payload: dict) -> Any:
    """Campaign brief payload -> normalised v2 brief (or None to clear)."""
    from ...auto_director.content_requirements import validate_content_brief

    try:
        return validate_content_brief(payload.get("brief"))
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None
_POST_FIELDS = (
    "post_url",
    "posted_at",
    "campaign_id",
    "submission_url",
    "submission_dismissed",
)
_STAT_FIELDS = ("views", "likes", "comments", "earnings")


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


def _optional_text(payload: dict, key: str, max_chars: int = 2000) -> Optional[str]:
    value = payload.get(key)
    if value is None:
        return None
    if not isinstance(value, str):
        raise HTTPException(status_code=400, detail=f"{key} must be text")
    value = value.strip()
    if len(value) > max_chars:
        raise HTTPException(status_code=400, detail=f"{key} is too long")
    return value or None


def _optional_number(payload: dict, key: str, *, integer: bool) -> Any:
    value = payload.get(key)
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise HTTPException(status_code=400, detail=f"{key} must be a number")
    if value < 0:
        raise HTTPException(status_code=400, detail=f"{key} cannot be negative")
    return int(value) if integer else float(value)


def _parse_posted_at(value: Any) -> datetime:
    if value is None:
        return datetime.now(timezone.utc)
    if not isinstance(value, str):
        raise HTTPException(status_code=400, detail="posted_at must be an ISO timestamp")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        raise HTTPException(status_code=400, detail="posted_at must be an ISO timestamp")
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


async def _require_campaign(
    db: AsyncSession, user_id: str, campaign_id: Any
) -> Optional[str]:
    if campaign_id is None:
        return None
    if not isinstance(campaign_id, str) or not campaign_id:
        raise HTTPException(status_code=400, detail="campaign_id must be a campaign id")
    if await PublishingRepository.get_campaign(db, user_id, campaign_id) is None:
        raise HTTPException(status_code=404, detail="Campaign not found")
    return campaign_id


# ---------------------------------------------------------------------------
# Board + bundle
# ---------------------------------------------------------------------------


@router.get("/board")
async def get_board(request: Request, db: AsyncSession = Depends(get_db)):
    user_id = await _get_authenticated_user_id(request, db)
    clips = await PublishingRepository.list_user_clips(db, user_id)
    posts = await PublishingRepository.list_posts(db, user_id)
    campaigns = await PublishingRepository.list_campaigns(db, user_id)
    stats = await PublishingRepository.list_stats(db, user_id)
    return {
        "clips": publishing.build_board(clips, posts, campaigns, stats),
        "campaigns": campaigns,
    }


def _build_bundle_zip(clip_path: Path) -> Path:
    """Zip the deliverable bundle folder, or the lone clip file."""
    bundle_dir = clip_path.with_suffix("")
    fd, tmp_name = tempfile.mkstemp(prefix="bundle-", suffix=".zip")
    tmp_path = Path(tmp_name)
    try:
        with zipfile.ZipFile(tmp_path, "w", zipfile.ZIP_DEFLATED) as archive:
            if bundle_dir.is_dir():
                for entry in sorted(bundle_dir.iterdir()):
                    if entry.is_file():
                        archive.write(entry, arcname=entry.name)
            else:
                archive.write(clip_path, arcname="clip.mp4")
    finally:
        import os

        os.close(fd)
    return tmp_path


@router.get("/clips/{clip_id}/bundle")
async def download_bundle(
    clip_id: str, request: Request, db: AsyncSession = Depends(get_db)
):
    user_id = await _get_authenticated_user_id(request, db)
    clip = await PublishingRepository.get_owned_clip(db, user_id, clip_id)
    if clip is None:
        raise HTTPException(status_code=404, detail="Clip not found")
    local_path = MediaStorage().local_path(clip["file_path"])
    if not local_path.is_file():
        raise HTTPException(
            status_code=404, detail="The clip file is not available right now"
        )
    zip_path = await run_in_thread(_build_bundle_zip, local_path)
    filename = f"{Path(clip['filename']).stem}-bundle.zip"
    return FileResponse(
        zip_path,
        media_type="application/zip",
        filename=filename,
        background=BackgroundTask(lambda: zip_path.unlink(missing_ok=True)),
    )


# ---------------------------------------------------------------------------
# Campaigns
# ---------------------------------------------------------------------------


@router.get("/campaigns")
async def list_campaigns(request: Request, db: AsyncSession = Depends(get_db)):
    user_id = await _get_authenticated_user_id(request, db)
    campaigns = await PublishingRepository.list_campaigns(db, user_id)
    posts = await PublishingRepository.list_posts(db, user_id)
    stats = await PublishingRepository.list_stats(db, user_id)
    clips = await PublishingRepository.list_user_clips(db, user_id)
    performance = publishing.aggregate_performance(posts, stats, campaigns, clips)
    rollups = {
        (bucket["campaign_id"] or ""): bucket
        for bucket in performance["per_campaign"]
    }
    enriched = []
    for campaign in campaigns:
        bucket = rollups.get(campaign["id"])
        own_posts = [p for p in posts if p.get("campaign_id") == campaign["id"]]
        enriched.append(
            {
                **campaign,
                "post_count": len(own_posts),
                "entered_stats": (
                    {
                        "views": bucket["views"],
                        "likes": bucket["likes"],
                        "comments": bucket["comments"],
                        "earnings": bucket["earnings"],
                    }
                    if bucket
                    else None
                ),
            }
        )
    return {"campaigns": enriched}


@router.post("/campaigns", status_code=201)
async def create_campaign(request: Request, db: AsyncSession = Depends(get_db)):
    user_id = await _get_authenticated_user_id(request, db)
    payload = await _read_json_object(request)
    name = _optional_text(payload, "name", max_chars=200)
    if not name:
        raise HTTPException(status_code=400, detail="Give the campaign a name first")
    campaign = {
        "id": uuid.uuid4().hex,
        "user_id": user_id,
        "name": name,
        "platform": _optional_text(payload, "platform", max_chars=60) or "",
        "campaign_url": _optional_text(payload, "campaign_url"),
        "budget": _optional_number(payload, "budget", integer=False),
        "notes": _optional_text(payload, "notes", max_chars=4000),
        "brief": _validated_brief(payload) if "brief" in payload else None,
        "example_url": _optional_text(payload, "example_url"),
    }
    created = await PublishingRepository.create_campaign(db, campaign)
    return {"campaign": created}


@router.patch("/campaigns/{campaign_id}")
async def update_campaign(
    campaign_id: str, request: Request, db: AsyncSession = Depends(get_db)
):
    user_id = await _get_authenticated_user_id(request, db)
    payload = await _read_json_object(request)
    fields: dict[str, Any] = {}
    for key in _CAMPAIGN_FIELDS:
        if key not in payload:
            continue
        if key == "brief":
            fields[key] = _validated_brief(payload)
        elif key == "budget":
            fields[key] = _optional_number(payload, key, integer=False)
        elif key == "name":
            name = _optional_text(payload, key, max_chars=200)
            if not name:
                raise HTTPException(
                    status_code=400, detail="Give the campaign a name first"
                )
            fields[key] = name
        elif key == "platform":
            fields[key] = _optional_text(payload, key, max_chars=60) or ""
        else:
            fields[key] = _optional_text(
                payload, key, max_chars=4000 if key == "notes" else 2000
            )
    updated = await PublishingRepository.update_campaign(
        db, user_id, campaign_id, fields
    )
    if updated is None:
        raise HTTPException(status_code=404, detail="Campaign not found")
    return {"campaign": updated}


@router.get("/campaigns/{campaign_id}/brief")
async def get_campaign_brief(
    campaign_id: str, request: Request, db: AsyncSession = Depends(get_db)
):
    """The campaign's stored brief, for the Create-page picker."""
    user_id = await _get_authenticated_user_id(request, db)
    campaign = await PublishingRepository.get_campaign(db, user_id, campaign_id)
    if campaign is None:
        raise HTTPException(status_code=404, detail="Campaign not found")
    return {
        "campaign_id": campaign_id,
        "brief": campaign.get("brief"),
        "has_brief": campaign.get("brief") is not None,
        "example_url": campaign.get("example_url"),
    }


@router.delete("/campaigns/{campaign_id}")
async def delete_campaign(
    campaign_id: str, request: Request, db: AsyncSession = Depends(get_db)
):
    user_id = await _get_authenticated_user_id(request, db)
    if not await PublishingRepository.delete_campaign(db, user_id, campaign_id):
        raise HTTPException(status_code=404, detail="Campaign not found")
    return {"deleted": True}


# ---------------------------------------------------------------------------
# Post records + entered stats
# ---------------------------------------------------------------------------


@router.post("/posts", status_code=201)
async def create_post(request: Request, db: AsyncSession = Depends(get_db)):
    user_id = await _get_authenticated_user_id(request, db)
    payload = await _read_json_object(request)
    clip_id = payload.get("clip_id")
    if not isinstance(clip_id, str) or not clip_id:
        raise HTTPException(status_code=400, detail="clip_id is required")
    platform = payload.get("platform")
    if platform not in POST_PLATFORMS:
        raise HTTPException(
            status_code=400,
            detail=f"platform must be one of: {', '.join(POST_PLATFORMS)}",
        )
    clip = await PublishingRepository.get_owned_clip(db, user_id, clip_id)
    if clip is None:
        raise HTTPException(status_code=404, detail="Clip not found")
    campaign_id = await _require_campaign(db, user_id, payload.get("campaign_id"))
    post = {
        "id": uuid.uuid4().hex,
        "clip_id": clip_id,
        "user_id": user_id,
        "platform": platform,
        "post_url": _optional_text(payload, "post_url") or "",
        "posted_at": _parse_posted_at(payload.get("posted_at")),
        "campaign_id": campaign_id,
        "submission_url": _optional_text(payload, "submission_url"),
        "submission_dismissed": bool(payload.get("submission_dismissed", False)),
    }
    created = await PublishingRepository.upsert_post(db, post)
    return {"post": created}


@router.patch("/posts/{post_id}")
async def update_post(
    post_id: str, request: Request, db: AsyncSession = Depends(get_db)
):
    user_id = await _get_authenticated_user_id(request, db)
    payload = await _read_json_object(request)
    fields: dict[str, Any] = {}
    for key in _POST_FIELDS:
        if key not in payload:
            continue
        if key == "posted_at":
            fields[key] = _parse_posted_at(payload.get(key))
        elif key == "campaign_id":
            fields[key] = await _require_campaign(db, user_id, payload.get(key))
        elif key == "submission_dismissed":
            fields[key] = bool(payload.get(key))
        else:
            fields[key] = _optional_text(payload, key)
    updated = await PublishingRepository.update_post(db, user_id, post_id, fields)
    if updated is None:
        raise HTTPException(status_code=404, detail="Post record not found")
    return {"post": updated}


@router.delete("/posts/{post_id}")
async def delete_post(
    post_id: str, request: Request, db: AsyncSession = Depends(get_db)
):
    user_id = await _get_authenticated_user_id(request, db)
    if not await PublishingRepository.delete_post(db, user_id, post_id):
        raise HTTPException(status_code=404, detail="Post record not found")
    return {"deleted": True}


@router.put("/posts/{post_id}/stats")
async def put_post_stats(
    post_id: str, request: Request, db: AsyncSession = Depends(get_db)
):
    user_id = await _get_authenticated_user_id(request, db)
    if await PublishingRepository.get_post(db, user_id, post_id) is None:
        raise HTTPException(status_code=404, detail="Post record not found")
    payload = await _read_json_object(request)
    stats = {
        key: _optional_number(payload, key, integer=key != "earnings")
        for key in _STAT_FIELDS
    }
    saved = await PublishingRepository.upsert_stats(db, post_id, stats)
    return {"stats": saved, "entered_by_user": True}


# ---------------------------------------------------------------------------
# Analytics
# ---------------------------------------------------------------------------


@router.get("/analytics")
async def get_analytics(request: Request, db: AsyncSession = Depends(get_db)):
    user_id = await _get_authenticated_user_id(request, db)
    tasks = await PublishingRepository.list_user_tasks(db, user_id)
    clips = await PublishingRepository.list_user_clips(db, user_id)
    token_totals = await PublishingRepository.user_token_totals(db, user_id)
    posts = await PublishingRepository.list_posts(db, user_id)
    campaigns = await PublishingRepository.list_campaigns(db, user_id)
    stats = await PublishingRepository.list_stats(db, user_id)
    return {
        "production": publishing.aggregate_production(tasks, clips, token_totals),
        "performance": publishing.aggregate_performance(
            posts, stats, campaigns, clips
        ),
    }
