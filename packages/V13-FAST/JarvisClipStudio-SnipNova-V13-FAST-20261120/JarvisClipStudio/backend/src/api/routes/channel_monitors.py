"""YouTube channel monitoring: settings, channels and activity.

``GET /channel-monitors`` returns the caller's master toggle, their
monitored channels (with poll health) and the recent videos the
watcher has seen across those channels. ``POST /channel-monitors``
adds a channel: the pasted URL or @handle is resolved to a channel
ID and validated with a real feed fetch, the channel's current
uploads are recorded as baseline (seen, never tasked), and — only
when the caller explicitly ticks it — the single newest video is run
through the watcher's normal claim path immediately. Per-channel
defaults are validated with the same validators the create-task
route uses, so a saved default can never be a value a manual task
would reject. The manual cutter is refused: it needs hand-picked
ranges, which an unattended watcher cannot have.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.ext.asyncio import AsyncSession

from ...ai import validate_target_clip_count
from ...auth_headers import resolve_authenticated_user_id
from ...auto_director.creation_modes import MANUAL_CUTTER_AI, validate_creation_mode
from ...auto_director.niche import validate_niche_override
from ...auto_director.template_mode import validate_template_mode
from ...config import get_config
from ...database import get_db
from ...media.music_library import validate_music_mode, validate_music_override
from ...repositories.channel_monitor_repository import ChannelMonitorRepository
from ...repositories.publishing_repository import PublishingRepository
from ...services.channel_monitor_service import production_service
from ...youtube_feed import ChannelFeedError, fetch_channel_feed, resolve_channel

logger = logging.getLogger(__name__)

router = APIRouter(tags=["channel monitors"])

repository = ChannelMonitorRepository()

_PROCESSING_MODES = {"fast", "balanced", "quality"}


def _validated_defaults(data: dict, *, partial: bool) -> dict:
    """Validate the per-channel task defaults with the create
    route's own validators. With ``partial=False`` (add), every
    default is resolved to the value that will be stored: template
    mode is explicit (plain unless auto is chosen), creation mode is
    full_auto unless a watcher-runnable mode is chosen."""

    def present(key: str) -> bool:
        return key in data

    out: dict = {}

    if not partial or present("niche_override"):
        try:
            out["niche_override"] = validate_niche_override(data.get("niche_override"))
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from None
    if not partial or present("music_mode"):
        try:
            out["music_mode"] = validate_music_mode(data.get("music_mode"))
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from None
    if not partial or present("music_override"):
        try:
            out["music_override"] = validate_music_override(data.get("music_override"))
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from None
    if not partial or present("template_mode"):
        try:
            template_mode = validate_template_mode(data.get("template_mode"))
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from None
        if template_mode is None and not partial:
            template_mode = "plain"  # stored explicitly, never NULL
        if template_mode is not None:
            out["template_mode"] = template_mode
        elif partial and present("template_mode"):
            raise HTTPException(
                status_code=422,
                detail="template_mode must be plain or auto",
            )
    if not partial or present("creation_mode"):
        try:
            creation_mode = validate_creation_mode(data.get("creation_mode"))
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from None
        if creation_mode == MANUAL_CUTTER_AI:
            raise HTTPException(
                status_code=422,
                detail=(
                    "The manual cutter needs hand-picked ranges, so a "
                    "watched channel cannot use it. Pick Full Auto or "
                    "one of the Find Moments modes."
                ),
            )
        if creation_mode is None and not partial:
            creation_mode = "full_auto"
        if creation_mode is not None:
            out["creation_mode"] = creation_mode
    if not partial or present("target_clip_count"):
        try:
            out["target_clip_count"] = validate_target_clip_count(
                data.get("target_clip_count")
            )
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from None
    if not partial or present("processing_mode"):
        processing_mode = data.get("processing_mode")
        if processing_mode is not None and processing_mode not in _PROCESSING_MODES:
            raise HTTPException(
                status_code=422,
                detail="processing_mode must be fast, balanced, quality or null",
            )
        out["processing_mode"] = processing_mode
    if not partial or present("include_shorts"):
        include_shorts = data.get("include_shorts", False)
        if not isinstance(include_shorts, bool):
            raise HTTPException(
                status_code=422, detail="include_shorts must be true or false"
            )
        out["include_shorts"] = include_shorts
    if not partial or present("max_tasks_per_day"):
        cap = data.get("max_tasks_per_day", 3)
        if cap is not None and (
            isinstance(cap, bool) or not isinstance(cap, int) or not 1 <= cap <= 20
        ):
            raise HTTPException(
                status_code=422,
                detail="max_tasks_per_day must be a whole number from 1 to 20, or null for no cap",
            )
        out["max_tasks_per_day"] = cap
    if not partial or present("campaign_id"):
        # Same shape rule as the create-task route: a campaign id is
        # a non-empty string or absent. Existence is checked by the
        # handlers, which have the caller's id and a session.
        campaign_id = data.get("campaign_id")
        if campaign_id is not None and (
            not isinstance(campaign_id, str) or not campaign_id.strip()
        ):
            raise HTTPException(
                status_code=422, detail="campaign_id must be a campaign id"
            )
        out["campaign_id"] = (
            campaign_id.strip() if isinstance(campaign_id, str) else None
        )
    if not partial or present("brand_kit_id"):
        brand_kit_id = data.get("brand_kit_id")
        if brand_kit_id is not None and (
            not isinstance(brand_kit_id, str) or not brand_kit_id.strip()
        ):
            raise HTTPException(
                status_code=422,
                detail="Invalid brand_kit_id. Use a brand kit id or omit it.",
            )
        out["brand_kit_id"] = (
            brand_kit_id.strip() if isinstance(brand_kit_id, str) else None
        )
    return out


async def _check_campaign_exists(db: AsyncSession, user_id: str, campaign_id) -> None:
    """A channel default that names a campaign must name one of the
    caller's own campaigns — the same lookup the create-task route
    performs, refused here at save time instead of at 3 a.m. in the
    watcher."""
    if not campaign_id:
        return
    campaign = await PublishingRepository.get_campaign(db, user_id, campaign_id)
    if campaign is None:
        raise HTTPException(
            status_code=422,
            detail="That campaign could not be found. Pick one of your campaigns or clear the field.",
        )


async def _json_body(request: Request) -> dict:
    try:
        data = await request.json()
    except Exception:
        data = None
    if not isinstance(data, dict):
        raise HTTPException(status_code=400, detail="A JSON object is required")
    return data


@router.get("/channel-monitors")
async def get_channel_monitors(request: Request, db: AsyncSession = Depends(get_db)):
    user_id = await resolve_authenticated_user_id(request, db, get_config())
    settings = await repository.get_settings(db, user_id)
    monitors = await repository.list_monitors(db, user_id)
    videos = await repository.list_videos(db, user_id, limit=50)
    return {
        "settings": settings or {"user_id": user_id, "enabled": False},
        "monitors": monitors,
        "videos": videos,
    }


@router.put("/channel-monitors/settings")
async def update_channel_monitor_settings(
    request: Request, db: AsyncSession = Depends(get_db)
):
    user_id = await resolve_authenticated_user_id(request, db, get_config())
    data = await _json_body(request)
    enabled = data.get("enabled", False)
    if not isinstance(enabled, bool):
        raise HTTPException(status_code=422, detail="enabled must be true or false")
    settings = await repository.upsert_settings(db, user_id, {"enabled": enabled})
    return {"settings": settings}


@router.post("/channel-monitors", status_code=201)
async def add_channel_monitor(request: Request, db: AsyncSession = Depends(get_db)):
    user_id = await resolve_authenticated_user_id(request, db, get_config())
    data = await _json_body(request)

    source_input = data.get("source_input")
    if not isinstance(source_input, str) or not source_input.strip():
        raise HTTPException(
            status_code=422,
            detail="Paste a YouTube channel URL or @handle first.",
        )
    clip_newest = data.get("clip_newest_now", False)
    if not isinstance(clip_newest, bool):
        raise HTTPException(
            status_code=422, detail="clip_newest_now must be true or false"
        )
    defaults = _validated_defaults(data, partial=False)
    await _check_campaign_exists(db, user_id, defaults.get("campaign_id"))

    try:
        channel_id, channel_title = await resolve_channel(source_input.strip())
    except ChannelFeedError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None

    if await repository.get_monitor_by_channel(db, user_id, channel_id) is not None:
        raise HTTPException(
            status_code=422, detail="That channel is already being monitored."
        )

    monitor = await repository.create_monitor(db, {
        "user_id": user_id,
        "channel_id": channel_id,
        "channel_title": channel_title,
        "source_input": source_input.strip(),
        **defaults,
    })
    if monitor is None:  # a concurrent add won the race
        raise HTTPException(
            status_code=422, detail="That channel is already being monitored."
        )

    # Baseline: everything currently on the channel is marked seen,
    # never tasked. The one explicit exception is the newest video
    # when the caller ticked "clip the newest video now".
    try:
        entries = await fetch_channel_feed(channel_id)
    except ChannelFeedError:
        entries = []
    service = production_service()
    chosen = await service.seed_baseline(
        db, monitor, entries, clip_newest=clip_newest
    )
    if chosen is not None:
        try:
            await service.handle_new_entry(db, monitor, chosen)
        except Exception:
            logger.exception(
                "Channel monitor add: newest-video clip failed for %s", channel_id
            )

    fresh = await repository.get_monitor(db, user_id, monitor["id"])
    return {"monitor": fresh or monitor}


@router.put("/channel-monitors/{monitor_id}")
async def update_channel_monitor(
    monitor_id: str, request: Request, db: AsyncSession = Depends(get_db)
):
    user_id = await resolve_authenticated_user_id(request, db, get_config())
    data = await _json_body(request)
    existing = await repository.get_monitor(db, user_id, monitor_id)
    if existing is None:
        raise HTTPException(status_code=404, detail="Channel monitor not found")

    fields = _validated_defaults(data, partial=True)
    if "campaign_id" in fields:
        await _check_campaign_exists(db, user_id, fields["campaign_id"])
    if "enabled" in data:
        if not isinstance(data["enabled"], bool):
            raise HTTPException(
                status_code=422, detail="enabled must be true or false"
            )
        fields["enabled"] = data["enabled"]
    if fields:
        await repository.update_monitor(db, monitor_id, fields)
    monitor = await repository.get_monitor(db, user_id, monitor_id)
    return {"monitor": monitor}


@router.post("/channel-monitors/videos/{video_row_id}/retry")
async def retry_channel_monitor_video(
    video_row_id: str, request: Request, db: AsyncSession = Depends(get_db)
):
    """Manual re-drive of one activity-list row: a parked, skipped
    or failed video is run through the claim path again, now. A
    video that already has a task is a no-op — the response carries
    the row's fresh state and a plain message either way."""
    user_id = await resolve_authenticated_user_id(request, db, get_config())
    result = await production_service().redrive_video(db, user_id, video_row_id)
    if result["outcome"] == "not_found":
        raise HTTPException(status_code=404, detail=result["message"])
    return result


@router.delete("/channel-monitors/{monitor_id}")
async def delete_channel_monitor(
    monitor_id: str, request: Request, db: AsyncSession = Depends(get_db)
):
    user_id = await resolve_authenticated_user_id(request, db, get_config())
    deleted = await repository.delete_monitor(db, user_id, monitor_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Channel monitor not found")
    return {"deleted": True}
