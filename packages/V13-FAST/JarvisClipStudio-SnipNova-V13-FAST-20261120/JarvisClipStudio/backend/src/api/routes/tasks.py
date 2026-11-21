"""
Task API routes using refactored architecture.
"""

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.encoders import jsonable_encoder
from fastapi.responses import FileResponse, RedirectResponse, StreamingResponse
from starlette.background import BackgroundTask
from ...media_storage import MediaStorage, STORAGE_ERRORS, is_s3, media_work
from sqlalchemy.ext.asyncio import AsyncSession
from sse_starlette.sse import EventSourceResponse
from pathlib import Path
from contextlib import aclosing
import json
import logging
from typing import Dict, Any, Optional
import inspect
import re
import secrets
import math
import asyncio
import hashlib
import time
from ...utils.async_helpers import run_in_thread
from ... import clip_archive

from ...repositories.generation_allowance_guard import generation_allowance_guard, GenerationAllowanceBusy
from ...repositories.task_run_guard import task_run_guard, TaskRunBusy
from ...repositories.provider_usage_repository import get_task_token_usage
from ...repositories.task_repository import TaskRepository
from ...database import get_db
from ...database import AsyncSessionLocal
from ...services.task_service import (
    MomentSelectionNotWaitingError,
    MomentSelectionSlateLostError,
    MomentSelectionSourceMissingError,
    TaskService,
)
from ...services.video_service import VideoService
from ...media.upload_ingest import foreign_upload
from ...repositories.upload_repository import get_stored_upload
from ...services.billing_service import BillingService, BillingLimitExceeded
from ...auth_headers import resolve_authenticated_user_id
from ...workers.job_queue import (
    JobQueue,
    get_rerender_outcome,
    set_rerender_outcome,
)
from ...caption_templates import get_template_names, get_template
from ...media.edl_render import TRANSITION_MAP
from ...auto_director.clip_edits import normalize_caption_overrides
from ...auto_director.look import normalize_look_overrides
from ...font_registry import get_available_fonts
from ...workers.progress import ProgressTracker
from ...config import get_config
from ...youtube_utils import async_get_youtube_video_info, canonical_youtube_url
from ...youtube_proxy import YouTubeGeoRestrictedError
from ...stream_source import (
    PREFLIGHT_DEADLINE_SECONDS as STREAM_PREFLIGHT_DEADLINE_SECONDS,
    StreamSourceError,
    fetch_stream_info,
)
from ...font_registry import is_font_accessible
from ...auto_director.learning import REASONS as LEARNING_REASONS
from ...auto_director.niche import validate_niche_override
from ...services.learning_service import (
    record_explicit_feedback,
    record_feedback_safely,
)
from ...media.music_library import validate_music_mode, validate_music_override
from ...auto_director.template_mode import validate_template_mode
from ...auto_director.framing_mode import validate_framing_mode
from ...media.gaming_templates import clean_layout_credit, validate_layout_template
from ...auto_director.broll_setting import validate_broll_enabled
from ...media.separation import validate_separation_opt_in
from ...ai import validate_target_clip_count
from ...auto_director.content_requirements import validate_content_brief
from ...repositories.publishing_repository import PublishingRepository
from ...recipes import create_fragment, merge_recipe_fragment
from ...repositories.recipe_repository import RecipeRepository
from ...auto_director.creation_modes import (
    AWAITING_SELECTION_STATUS,
    MANUAL_CUTTER_AI,
    plan_for_mode,
    validate_creation_mode,
    validate_manual_ranges,
)
from ...services.clip_ai_edit_service import (
    AiEditUnavailable,
    ClipAiEditService,
)
from ...clip_duration import normalize_clip_length, validate_clip_length
from ...clip_cleanup import (
    new_task_cleanup_settings,
    normalize_clip_cleanup_settings,
    updated_cleanup_settings,
)
from ...video_utils import VALID_OUTPUT_FORMATS
from ...admin_auth import require_admin_user, user_is_admin
from ...source_submission import SourceAttempt
import redis.asyncio as redis
from ...clip_editor import export_with_preset, EXPORT_PRESETS

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/tasks", tags=["tasks"])


async def _preflight_youtube_source(url: str, duration_limit: int) -> Dict[str, Any]:
    """Check duration before creating a task or consuming a generation."""
    # wait_for cancels the awaiting task when this elapses. A yt-dlp call already
    # running inside asyncio.to_thread can finish in the background.
    timeout_seconds = get_config().youtube_preflight_timeout_seconds
    try:
        metadata = await asyncio.wait_for(
            async_get_youtube_video_info(url, deadline=time.monotonic() + timeout_seconds),
            timeout=timeout_seconds,
        )
        duration = (metadata or {}).get("duration")
        if isinstance(duration, bool) or not isinstance(duration, (int, float)):
            raise ValueError("Missing video duration")
        if not math.isfinite(duration) or duration <= 0:
            raise ValueError("Invalid video duration")
    except YouTubeGeoRestrictedError as exc:
        raise HTTPException(
            status_code=422,
            detail={
                "code": "YOUTUBE_GEO_RESTRICTED",
                "message": f"{exc} No minutes were used.",
            },
        ) from None
    except TimeoutError:
        logger.warning(
            "YouTube metadata preflight timed out after %s seconds",
            timeout_seconds,
        )
        raise HTTPException(
            status_code=503,
            detail={
                "code": "VIDEO_METADATA_TIMEOUT",
                "message": (
                    "Checking this YouTube video's length timed out. Please try again "
                    "shortly or upload the video file. No minutes were used."
                ),
            },
        ) from None
    except Exception:
        raise HTTPException(
            status_code=503,
            detail={
                "code": "VIDEO_METADATA_UNAVAILABLE",
                "message": (
                    "We couldn't verify this video's length. Please try again shortly "
                    "or upload the video file. No minutes were used."
                ),
            },
        ) from None

    if duration > duration_limit:
        raise HTTPException(
            status_code=422,
            detail={
                "code": "VIDEO_TOO_LONG",
                "message": (
                    f"This video is {duration / 60:.1f} minutes long. Your plan allows "
                    f"YouTube videos up to {duration_limit / 60:g} minutes. "
                    "Choose a shorter video. No minutes were used."
                ),
                "duration_seconds": duration,
                "max_duration_seconds": duration_limit,
            },
        )
    return metadata

async def _preflight_stream_source(url: str, source: str, duration_limit: int) -> Dict[str, Any]:
    try:
        # One overall deadline: yt-dlp (proxy, direct) metadata, then the cheap
        # metadata actor only when both fail. The full-video actor never runs
        # here; the worker owns the rest of the fallback chain.
        metadata = await asyncio.wait_for(
            run_in_thread(fetch_stream_info, url, deadline_seconds=STREAM_PREFLIGHT_DEADLINE_SECONDS),
            timeout=STREAM_PREFLIGHT_DEADLINE_SECONDS + 5,
        )
        duration = (metadata or {}).get("duration")
        if isinstance(duration, bool) or not isinstance(duration, (int, float)) or not math.isfinite(duration) or duration <= 0:
            raise ValueError("Invalid video duration")
    except StreamSourceError as exc:
        raise HTTPException(status_code=422, detail={"code": "VIDEO_UNAVAILABLE", "message": str(exc)}) from None
    except Exception:
        raise HTTPException(status_code=503, detail={"code": "VIDEO_METADATA_UNAVAILABLE", "message": "We couldn't fetch this video, try again later. No minutes were used."}) from None
    if duration > duration_limit:
        raise HTTPException(status_code=422, detail={"code": "VIDEO_TOO_LONG", "message": f"This {source.title()} video is {duration / 60:.1f} minutes long. Your plan allows videos up to {duration_limit / 60:g} minutes. Choose a shorter VOD or clip. No minutes were used.", "duration_seconds": duration, "max_duration_seconds": duration_limit})
    return metadata


def _stream_resolution_metadata(metadata: Dict[str, Any] | None) -> Dict[str, Any]:
    """Persist a preflight actor result so the worker does not pay for it again."""
    if not metadata or not metadata.get("media_url") or not str(metadata.get("provider", "")).startswith("apify_"):
        return {}
    return {
        "stream_resolution": {
            key: metadata.get(key)
            for key in ("provider", "id", "title", "duration", "media_url")
        }
        | {"resolved_at": time.time()}
    }


async def _preflight_upload_source(url: str, user_id: str, duration_limit: int, db=None) -> float:
    """Read an uploaded video's length so its minutes are charged up front."""
    reference = None
    if db is not None and isinstance(url, str) and url.startswith("upload://") and not url.startswith("upload://s3/"):
        row = await get_stored_upload(db, url)
        reference = row.storage_reference if row is not None else None
    try:
        if reference:
            duration = await run_in_thread(VideoService.probe_upload_duration, url, user_id, reference)
        else:
            duration = await run_in_thread(VideoService.probe_upload_duration, url, user_id)
        if not math.isfinite(duration) or duration <= 0:
            raise ValueError("Invalid video duration")
    except Exception:
        raise HTTPException(
            status_code=422,
            detail={
                "code": "VIDEO_METADATA_UNAVAILABLE",
                "message": (
                    "We couldn't read this video's length. Check that the file is a "
                    "playable video and upload it again. No minutes were used."
                ),
            },
        ) from None

    if duration > duration_limit:
        raise HTTPException(
            status_code=422,
            detail={
                "code": "VIDEO_TOO_LONG",
                "message": (
                    f"This video is {duration / 60:.1f} minutes long. Uploads can be "
                    f"up to {duration_limit / 60:g} minutes. No minutes were used."
                ),
                "duration_seconds": duration,
                "max_duration_seconds": duration_limit,
            },
        )
    return duration


def _normalize_font_size(value: Any, default: int = 24) -> Optional[int]:
    if value is None or (isinstance(value, str) and not value.strip()):
        return None
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = default
    return max(12, min(72, parsed))


def _normalize_font_color(value: Any, default: str = "#FFFFFF") -> Optional[str]:
    if value is None or (isinstance(value, str) and not value.strip()):
        return None
    if isinstance(value, str) and re.match(r"^#[0-9A-Fa-f]{6}$", value):
        return value.upper()
    return default


def _normalize_font_family(value: Any) -> Optional[str]:
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None


async def _read_json_object(request: Request) -> Dict[str, Any]:
    try:
        payload = await request.json()
    except (ValueError, UnicodeDecodeError):
        raise HTTPException(status_code=400, detail="Invalid JSON body")
    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail="Request body must be an object")
    return payload


def _finite_number(value: Any, name: str) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        raise ValueError(f"{name} must be a finite number")
    if isinstance(value, bool) or not math.isfinite(number):
        raise ValueError(f"{name} must be a finite number")
    return number


async def _get_user_id_from_headers(request: Request, db: AsyncSession) -> str:
    """Resolve the authenticated user ID from an API key or signed frontend headers."""
    config = get_config()
    return await resolve_authenticated_user_id(request, db, config)


async def _load_task_source_metadata(task_id: str, db: AsyncSession | None = None) -> Dict[str, Any]:
    if db is not None:
        metadata = await TaskRepository.load_source_metadata(db, task_id)
        if metadata:
            return metadata
    runtime_config = get_config()
    redis_client = redis.Redis(
        host=runtime_config.redis_host,
        port=runtime_config.redis_port,
        password=runtime_config.redis_password,
        decode_responses=True,
    )
    try:
        payload = await redis_client.get(f"task_source:{task_id}")
    except Exception as exc:
        logger.warning("Unable to load task source metadata for %s: %s", task_id, exc)
        return {}
    finally:
        await redis_client.aclose()

    if not payload:
        return {}

    try:
        return json.loads(payload)
    except json.JSONDecodeError:
        return {}


async def _save_task_source_metadata(
    task_id: str, payload: Dict[str, Any], db: AsyncSession | None = None
) -> None:
    if db is not None:
        await TaskRepository.save_source_metadata(db, task_id, payload)
    runtime_config = get_config()
    redis_client = redis.Redis(
        host=runtime_config.redis_host,
        port=runtime_config.redis_port,
        password=runtime_config.redis_password,
        decode_responses=True,
    )
    try:
        await redis_client.set(
            f"task_source:{task_id}",
            json.dumps(payload),
            ex=60 * 60 * 24 * 7,
        )
    except Exception as exc:
        logger.warning("Unable to save task source metadata for %s: %s", task_id, exc)
    finally:
        await redis_client.aclose()


def _merge_task_source_metadata(
    existing: Dict[str, Any] | None,
    *,
    source_url: Any = None,
    source_type: Any = None,
    output_format: Any = None,
    add_subtitles: Any = None,
    cleanup_settings: Dict[str, Any] | None = None,
) -> Dict[str, Any]:
    merged = dict(existing or {})

    if isinstance(source_url, str) and source_url:
        merged["url"] = source_url
    if isinstance(source_type, str) and source_type:
        merged["source_type"] = source_type
    if output_format in VALID_OUTPUT_FORMATS:
        merged["output_format"] = output_format
    if isinstance(add_subtitles, bool):
        merged["add_subtitles"] = add_subtitles
    if cleanup_settings:
        merged.update(cleanup_settings)

    return merged


# Matches the admin dashboard's live queue: queued, processing, and the
# pre-queue pending default. Finished tasks stay visible only to their owner.
ADMIN_READABLE_TASK_STATUSES = {"queued", "processing", "pending"}


async def _load_task_for_user(
    request: Request, task_service: TaskService, db: AsyncSession, task_id: str
):
    user_id = await _get_user_id_from_headers(request, db)
    task = await task_service.task_repo.get_task_by_id(db, task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    return user_id, task


async def _require_task_owner(
    request: Request, task_service: TaskService, db: AsyncSession, task_id: str
):
    """Ensure authenticated user owns the task."""
    user_id, task = await _load_task_for_user(request, task_service, db, task_id)

    if task.get("user_id") != user_id:
        raise HTTPException(status_code=403, detail="Not authorized for this task")

    return task


async def _authorize_task_reader(
    request: Request, task_service: TaskService, db: AsyncSession, task_id: str
):
    """Owner, or an admin reading a task that is still active. Returns (user_id, task)."""
    user_id, task = await _load_task_for_user(request, task_service, db, task_id)

    if task.get("user_id") == user_id:
        return user_id, task
    if task.get("status") in ADMIN_READABLE_TASK_STATUSES and await user_is_admin(
        db, user_id
    ):
        return user_id, task

    raise HTTPException(status_code=403, detail="Not authorized for this task")


async def _require_task_reader(
    request: Request, task_service: TaskService, db: AsyncSession, task_id: str
):
    """Owner, or an admin reading a task that is still active."""
    _user_id, task = await _authorize_task_reader(request, task_service, db, task_id)
    return task


async def _record_passive_feedback(
    task: Dict[str, Any],
    clip: Dict[str, Any],
    verdict: str,
    source: str,
    niche_override: Optional[str] = None,
) -> None:
    """Best-effort learning capture for actions the user took anyway.

    Runs on its own short-lived session so it can never entangle the
    request's transaction, and swallows everything: a learning hiccup
    must never fail a download, export, delete, or re-render.
    ``record_feedback_safely`` already never raises; this wrapper also
    guards session creation.
    """
    try:
        async with AsyncSessionLocal() as learning_db:
            await record_feedback_safely(
                learning_db,
                user_id=str(task.get("user_id") or ""),
                task=task,
                clip=clip,
                verdict=verdict,
                reason=None,
                source=source,
                niche_override=niche_override,
            )
    except Exception:
        logger.warning(
            "Passive learning capture failed for clip %s",
            clip.get("id"),
            exc_info=True,
        )


PUBLIC_TASK_FIELDS = {
    "source_title",
    "source_type",
    "status",
    "clips_count",
    "created_at",
    "updated_at",
}
PUBLIC_CLIP_FIELDS = {
    "id",
    "filename",
    "start_time",
    "end_time",
    "duration",
    "text",
    "relevance_score",
    "reasoning",
    "clip_order",
    "created_at",
    "virality_score",
    "hook_score",
    "engagement_score",
    "value_score",
    "shareability_score",
    "hook_type",
    "hook_title",
    "editorial_quality",
}


def _build_public_task(task: Dict[str, Any], share_token: str) -> Dict[str, Any]:
    """Return only fields intended for anyone holding the share URL.

    Campaign briefs are the owner's business data: the share view never
    carries content_brief/brief_report or compliance internals — only
    each clip's compliance status badge summary.
    """
    public_task = {key: task.get(key) for key in PUBLIC_TASK_FIELDS}
    public_task["clips"] = []
    for clip in task.get("clips", []):
        public_clip = {key: clip.get(key) for key in PUBLIC_CLIP_FIELDS}
        summary = clip.get("compliance_summary")
        if isinstance(summary, dict) and summary.get("status"):
            public_clip["compliance_summary"] = {"status": summary["status"]}
        public_clip["video_url"] = (
            f"/tasks/shared/{share_token}/clips/{clip['id']}/file"
        )
        public_task["clips"].append(public_clip)
    return public_task


async def _reference_style_from_campaign_example(
    campaign: Dict[str, Any],
) -> Optional[str]:
    """Feed a campaign's example link through the Reference Style seam.

    Exactly the user-pasted-link flow (download + analyse + store a user
    profile) from the reference-styles routes; returns the profile name
    to use as the task's niche/style input, or None on any failure —
    an example link must never fail task creation.
    """
    url = campaign.get("example_url")
    if not url:
        return None
    try:
        from .reference_styles import _analyse_and_store, _download_reference_video
        from ...auto_director.user_profiles import ensure_user_profiles_dir
        import shutil
        import uuid as _uuid

        folder = await run_in_thread(ensure_user_profiles_dir)
        incoming = folder / f".incoming-{_uuid.uuid4().hex[:12]}"
        incoming.mkdir(parents=True, exist_ok=True)
        try:
            video_path = await run_in_thread(_download_reference_video, url, incoming)
            display_name = f"Campaign example - {campaign.get('name') or 'campaign'}"
            summary = await run_in_thread(
                _analyse_and_store, video_path, display_name, url
            )
            return summary.get("name")
        finally:
            shutil.rmtree(incoming, ignore_errors=True)
    except Exception:
        logger.warning(
            "Campaign example link could not be turned into a reference "
            "style; creating the task without it",
            exc_info=True,
        )
        return None


@router.get("/")
async def list_tasks(
    request: Request, db: AsyncSession = Depends(get_db), limit: int = 50
):
    """
    Get all tasks for the authenticated user.
    """
    user_id = await _get_user_id_from_headers(request, db)

    try:
        task_service = TaskService(db)
        tasks = await task_service.get_user_tasks(user_id, limit)

        return {"tasks": tasks, "total": len(tasks)}

    except Exception as e:
        logger.error(f"Error retrieving user tasks: {e}")
        raise HTTPException(status_code=500, detail=f"Error retrieving tasks: {str(e)}")


def _request_idempotency_key(request: Request) -> str:
    headers = getattr(request, "headers", None)
    getter = getattr(headers, "get", None)
    if getter is None:
        return ""
    raw = getter("idempotency-key")
    if raw is None or not str(raw).strip():
        return ""
    key = str(raw).strip()
    if not re.fullmatch(r"[A-Za-z0-9._:-]{8,200}", key):
        raise HTTPException(status_code=400, detail="Invalid idempotency key")
    return key


def _upload_request_key(request: Request, url: str, options: Dict[str, Any]) -> str | None:
    """Stable id for one upload plus the options that change the job.

    A missing header still matches an identical retry. YouTube and other
    links are not deduped here.
    """
    header = _request_idempotency_key(request)
    if not isinstance(url, str) or not url.startswith("upload://"):
        return None
    payload = json.dumps(options, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(f"{header}\n{payload}".encode()).hexdigest()


def _queued_upload_task(task_id: str, job_id: str | None) -> Dict[str, Any]:
    return {
        "task_id": task_id,
        "job_id": job_id or task_id,
        "message": "Task already queued",
    }


@router.post("/")
async def create_task(request: Request, db: AsyncSession = Depends(get_db)):
    """
    Create a new task and enqueue it for processing.
    Returns task_id immediately.
    """
    attempt = SourceAttempt(request, db)
    try:
        response = await _create_task(request, db, attempt)
    except HTTPException as exc:
        await attempt.rejected(exc.status_code, exc.detail)
        raise
    await attempt.accepted()
    return response


async def _create_task(request: Request, db: AsyncSession, attempt: SourceAttempt):
    data = await _read_json_object(request)

    raw_source = data.get("source")
    if isinstance(raw_source, dict):
        attempt.note_url(raw_source.get("url"))
    if not isinstance(raw_source, dict) or not isinstance(raw_source.get("url"), str) or not raw_source["url"].strip():
        raise HTTPException(status_code=400, detail="Source URL is required")
    user_id = await _get_user_id_from_headers(request, db)
    attempt.note_user(user_id)

    # Recipes: a saved bundle fills the choices the request left
    # unmade. Explicit per-choice fields win over the recipe; the
    # merged payload then goes through the same validation below as a
    # hand-built payload, so a stored value the create path would
    # reject is rejected here too. Unknown ids are refused before
    # billing, preflight, or any task creation.
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
        data = merge_recipe_fragment(data, create_fragment(recipe))

    # Get font options
    font_options = data.get("font_options", {})
    if not isinstance(font_options, dict):
        raise HTTPException(status_code=400, detail="font_options must be an object")
    font_family = _normalize_font_family(font_options.get("font_family"))
    font_size = _normalize_font_size(font_options.get("font_size"))
    font_color = _normalize_font_color(font_options.get("font_color"))
    caption_template = data.get("caption_template", "default")
    include_broll = data.get("include_broll") is True
    runtime_config = get_config()
    processing_mode = data.get(
        "processing_mode", runtime_config.default_processing_mode
    )
    if processing_mode not in {"fast", "balanced", "quality", "fast_clips"}:
        processing_mode = runtime_config.default_processing_mode
    output_format = data.get("output_format", "vertical")
    if output_format not in VALID_OUTPUT_FORMATS:
        raise HTTPException(
            status_code=422,
            detail=(
                "Invalid output_format. Valid values: "
                + ", ".join(sorted(VALID_OUTPUT_FORMATS))
                + ", or omit it for vertical."
            ),
        )
    add_subtitles = data.get("add_subtitles", True)
    if not isinstance(add_subtitles, bool):
        raise HTTPException(
            status_code=422,
            detail="Invalid add_subtitles. Use true or false, or omit it.",
        )
    try:
        clip_length = validate_clip_length(data.get("clip_length", "auto"))
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None
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
        template_mode = validate_template_mode(data.get("template_mode"))
        framing_mode = validate_framing_mode(data.get("framing_mode"))
        layout_template = validate_layout_template(data.get("layout_template"))
        layout_credit = (
            clean_layout_credit(data.get("layout_credit")) if layout_template else None
        )
        hook_title_enabled = data.get("hook_title_enabled")
        if hook_title_enabled is not None and not isinstance(hook_title_enabled, bool):
            raise ValueError("hook_title_enabled must be true or false")
        broll_enabled = validate_broll_enabled(data.get("broll_enabled"))
        separation_opt_in = validate_separation_opt_in(
            data.get("separation_opt_in")
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None
    try:
        target_clip_count = validate_target_clip_count(
            data.get("target_clip_count")
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None
    # Campaign brief: validated here without the source duration (for the
    # upload idempotency key) and again after preflight with it, where
    # range-vs-duration checks and end clamping apply.
    try:
        content_brief = validate_content_brief(data.get("content_brief"))
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None
    campaign_id = data.get("campaign_id")
    if campaign_id is not None and (
        not isinstance(campaign_id, str) or not campaign_id.strip()
    ):
        raise HTTPException(status_code=400, detail="campaign_id must be a campaign id")
    if isinstance(campaign_id, str):
        campaign_id = campaign_id.strip()
    brand_kit_id = data.get("brand_kit_id")
    if brand_kit_id is not None and (
        not isinstance(brand_kit_id, str) or not brand_kit_id.strip()
    ):
        raise HTTPException(
            status_code=422,
            detail="Invalid brand_kit_id. Use a brand kit id or omit it.",
        )
    if isinstance(brand_kit_id, str):
        brand_kit_id = brand_kit_id.strip()
    # Creation mode contract (tracker 1.9): the mode names a workflow,
    # and Manual Cutter ranges are required for — and exclusive to —
    # the manual_cutter_ai workflow. Rejecting a mismatched payload
    # here keeps a stored task from ever claiming a workflow its
    # inputs cannot run.
    try:
        creation_mode = validate_creation_mode(data.get("creation_mode"))
        manual_ranges = validate_manual_ranges(data.get("manual_ranges"))
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None
    if plan_for_mode(creation_mode).requires_ranges and not manual_ranges:
        raise HTTPException(
            status_code=422,
            detail="manual_ranges is required for the manual_cutter_ai "
            "creation mode",
        )
    if manual_ranges and creation_mode != MANUAL_CUTTER_AI:
        raise HTTPException(
            status_code=422,
            detail="manual_ranges is only accepted for the "
            "manual_cutter_ai creation mode",
        )
    cleanup_settings = new_task_cleanup_settings(data)
    if not raw_source or not raw_source.get("url"):
        raise HTTPException(status_code=400, detail="Source URL is required")

    upload_request_key = _upload_request_key(
        request,
        raw_source["url"],
        {
            "url": raw_source["url"],
            "font_family": font_family,
            "font_size": font_size,
            "font_color": font_color,
            "caption_template": caption_template,
            "include_broll": include_broll,
            "processing_mode": processing_mode,
            "niche_override": niche_override,
            "music_override": music_override,
            "music_mode": music_mode,
            "template_mode": template_mode,
            "framing_mode": framing_mode,
            "layout_template": layout_template,
            "layout_credit": layout_credit,
            "hook_title_enabled": hook_title_enabled,
            "broll_enabled": broll_enabled,
            "separation_opt_in": separation_opt_in,
            "target_clip_count": target_clip_count,
            "content_brief": content_brief,
            "campaign_id": campaign_id,
            "brand_kit_id": brand_kit_id,
            "creation_mode": creation_mode,
            "manual_ranges": manual_ranges,
            "output_format": output_format,
            "add_subtitles": add_subtitles,
            "clip_length": clip_length,
            "cleanup_settings": cleanup_settings,
        },
    )
    if upload_request_key and db is not None:
        existing = await TaskRepository.find_recent_upload_task(db, user_id, upload_request_key)
        if existing is not None:
            return _queued_upload_task(existing.id, existing.job_id)

    try:
        billing_service = BillingService(db)
        billing = await billing_service.assert_can_create_task(user_id)

        task_service = TaskService(db)

        try:
            source_type = task_service.video_service.determine_source_type(raw_source["url"])
        except ValueError as exc:
            # Only rejected http(s) stream URLs are a 422 validation error.
            # Local paths and other non-URL sources keep the historic 404.
            if not str(raw_source["url"]).strip().lower().startswith(("http://", "https://")):
                raise
            raise HTTPException(status_code=422, detail=str(exc)) from None
        if source_type == "youtube":
            # Shorts, live, share and embed links all become watch?v=ID, so the
            # stored source, downloads and caches see one form per video.
            raw_source["url"] = canonical_youtube_url(raw_source["url"]) or raw_source["url"]
        source_title = raw_source.get("title")
        # Creator/channel name from the same preflight metadata, for
        # hook-title attribution (title_person). Uploads have none.
        creator_name = None
        stream_metadata: Dict[str, Any] | None = None
        if source_type == "youtube":
            metadata = await _preflight_youtube_source(
                raw_source["url"], billing["max_youtube_duration_seconds"]
            )
            source_title = source_title or metadata.get("title") or "YouTube Video"
            creator_name = metadata.get("uploader") or metadata.get("channel")
            duration_seconds = float(metadata["duration"])
        elif source_type in {"twitch", "kick"}:
            stream_metadata = await _preflight_stream_source(raw_source["url"], source_type, billing["max_youtube_duration_seconds"])
            source_title = source_title or stream_metadata.get("title") or f"{source_type.title()} Video"
            creator_name = stream_metadata.get("uploader") or stream_metadata.get("channel")
            duration_seconds = float(stream_metadata["duration"])
        else:
            if await foreign_upload(db, raw_source["url"], user_id):
                raise HTTPException(status_code=404, detail="Video file not found")
            duration_seconds = await _preflight_upload_source(
                raw_source["url"], user_id, billing["max_upload_duration_seconds"], db
            )

        # Campaign brief library: a campaign_id snapshots the campaign's
        # stored brief as this task's content_brief. An explicit payload
        # brief wins over the snapshot; later campaign edits never mutate
        # the task. The campaign's example link feeds the Reference Style
        # seam only when the payload named no style of its own.
        effective_brief = content_brief
        if campaign_id:
            campaign = await PublishingRepository.get_campaign(
                db, user_id, campaign_id
            )
            if campaign is None:
                raise HTTPException(status_code=404, detail="Campaign not found")
            if effective_brief is None and campaign.get("brief"):
                effective_brief = campaign["brief"]
            if campaign.get("example_url") and niche_override is None:
                style_name = await _reference_style_from_campaign_example(campaign)
                if style_name:
                    niche_override = style_name
        try:
            effective_brief = validate_content_brief(
                effective_brief, source_duration=duration_seconds
            )
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from None
        if manual_ranges and max(r["end"] for r in manual_ranges) > (
            duration_seconds + 1.0
        ):
            raise HTTPException(
                status_code=422,
                detail="manual_ranges extend beyond the source duration",
            )

        async with generation_allowance_guard(
            db, user_id, enabled=runtime_config.monetization_enabled
        ):
            await billing_service.assert_can_create_task(user_id, duration_seconds)
            # Create task
            task_id = await task_service.create_task_with_source(
                user_id=user_id,
                url=raw_source["url"],
                title=source_title,
                font_family=font_family,
                font_size=font_size,
                font_color=font_color,
                caption_template=caption_template,
                include_broll=include_broll,
                processing_mode=processing_mode,
                niche_override=niche_override,
                music_override=music_override,
                music_mode=music_mode,
                template_mode=template_mode,
                framing_mode=framing_mode,
                layout_template=layout_template,
                layout_credit=layout_credit,
                hook_title_enabled=hook_title_enabled,
                broll_enabled=broll_enabled,
                separation_opt_in=separation_opt_in,
                target_clip_count=target_clip_count,
                creation_mode=creation_mode,
                manual_ranges=manual_ranges,
                source_duration_seconds=math.ceil(duration_seconds),
                upload_request_key=upload_request_key,
                content_brief=effective_brief,
                campaign_id=campaign_id,
                brand_kit_id=brand_kit_id,
                creator_name=creator_name,
                recipe_id=recipe_id,
            )
            if getattr(task_service, "reused_upload_task", False):
                return _queued_upload_task(
                    task_id, getattr(task_service, "reused_upload_job_id", None)
                )

            try:
                # Persist retry options before submitting the job; Redis remains a legacy cache.
                processing_job_id = f"{task_id}:{secrets.token_hex(16)}"
                await _save_task_source_metadata(
                    task_id,
                    _merge_task_source_metadata(
                        {
                            "processing_job_id": processing_job_id,
                            "clip_length": clip_length,
                            **_stream_resolution_metadata(stream_metadata),
                        },
                        source_url=raw_source["url"],
                        source_type=source_type,
                        output_format=output_format,
                        add_subtitles=add_subtitles,
                        cleanup_settings=cleanup_settings,
                    ),
                    db=db,
                )

                # Enqueue job for worker
                queue_adapter = getattr(request.app.state, "queue_adapter", JobQueue)
                job_id = await queue_adapter.enqueue_processing_job(
                    "process_video_task",
                    processing_mode,
                    task_id,
                    raw_source["url"],
                    source_type,
                    user_id,
                    font_family,
                    font_size,
                    font_color,
                    caption_template,
                    processing_mode,
                    output_format,
                    add_subtitles,
                    cleanup_settings,
                    clip_length,
                    _job_id=processing_job_id,
                )

            except Exception:
                await task_service.task_repo.update_task_status(
                    db, task_id, "error", progress=0,
                    progress_message="Unable to queue processing. Please retry this generation.",
                    expected_statuses=["queued"],
                )
                raise

            logger.info(f"Task {task_id} created and job {job_id} enqueued")

            return {
                "task_id": task_id,
                "job_id": job_id,
                "message": "Task created and queued for processing",
            }

    except GenerationAllowanceBusy as e:
        raise HTTPException(status_code=409, detail=str(e), headers={"Retry-After": "1"})
    except HTTPException:
        raise
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except BillingLimitExceeded as e:
        raise HTTPException(
            status_code=402,
            detail={
                "code": "SUBSCRIPTION_REQUIRED",
                "message": e.summary.get("reason") or "Choose a paid plan to process videos.",
                "billing": jsonable_encoder(e.summary),
            },
        )
    except Exception as e:
        logger.error(f"Error creating task: {e}")
        raise HTTPException(status_code=500, detail=f"Error creating task: {str(e)}")


@router.get("/billing/summary")
async def get_billing_summary(request: Request, db: AsyncSession = Depends(get_db)):
    """Get monetization status and current usage for authenticated user."""
    user_id = await _get_user_id_from_headers(request, db)

    try:
        billing_service = BillingService(db)
        summary = await billing_service.get_usage_summary(user_id)
        return summary
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        logger.error(f"Error retrieving billing summary: {e}")
        raise HTTPException(
            status_code=500,
            detail=f"Error retrieving billing summary: {str(e)}",
        )


@router.get("/shared/{share_token}")
async def get_shared_task(share_token: str, db: AsyncSession = Depends(get_db)):
    """Get the read-only generation result associated with an opaque share token."""
    task_service = TaskService(db)
    task_id = await task_service.task_repo.get_shared_task_id(db, share_token)
    if not task_id:
        raise HTTPException(status_code=404, detail="Shared result not found")

    task = await task_service.get_task_with_clips(task_id)
    if not task or task.get("status") != "completed":
        raise HTTPException(status_code=404, detail="Shared result not found")

    return _build_public_task(task, share_token)


@router.get("/shared/{share_token}/clips/{clip_id}/file")
async def get_shared_clip_file(
    share_token: str,
    clip_id: str,
    download: bool = False,
    db: AsyncSession = Depends(get_db, scope="function"),
):
    """Serve one shared clip only when the bearer share token is enabled."""
    task_service = TaskService(db)
    task_id = await task_service.task_repo.get_shared_task_id(db, share_token)
    if not task_id:
        raise HTTPException(status_code=404, detail="Shared result not found")

    clip = await task_service.clip_repo.get_clip_by_id(db, clip_id)
    if not clip or clip.get("task_id") != task_id:
        raise HTTPException(status_code=404, detail="Clip not found")

    if is_s3(clip["file_path"]):
        await db.close()
        url = await run_in_thread(MediaStorage().signed_url, clip["file_path"], clip["filename"], attachment=download)
        return RedirectResponse(url, status_code=307, headers={"Cache-Control": "private, no-store"})
    clip_path = Path(clip["file_path"])
    if not clip_path.exists():
        raise HTTPException(status_code=404, detail="Clip file not found")

    return FileResponse(
        path=str(clip_path),
        media_type="video/mp4",
        filename=clip["filename"],
        content_disposition_type="attachment" if download else "inline",
        headers={"Cache-Control": "private, no-store"},
    )


@router.get("/{task_id}")
async def get_task(
    task_id: str, request: Request, db: AsyncSession = Depends(get_db)
):
    """Get task details."""
    try:
        task_service = TaskService(db)
        await _require_task_reader(request, task_service, db, task_id)
        task = await task_service.get_task_with_clips(task_id)

        if not task:
            raise HTTPException(status_code=404, detail="Task not found")

        return task

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error retrieving task: {e}")
        raise HTTPException(status_code=500, detail=f"Error retrieving task: {str(e)}")


@router.get("/{task_id}/clips")
async def get_task_clips(
    task_id: str, request: Request, db: AsyncSession = Depends(get_db)
):
    """Get all clips for a task."""
    try:
        task_service = TaskService(db)
        await _require_task_reader(request, task_service, db, task_id)
        task = await task_service.get_task_with_clips(task_id)

        if not task:
            raise HTTPException(status_code=404, detail="Task not found")

        return {
            "task_id": task_id,
            "clips": task.get("clips", []),
            "total_clips": len(task.get("clips", [])),
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error retrieving clips: {e}")
        raise HTTPException(status_code=500, detail=f"Error retrieving clips: {str(e)}")


@router.get("/{task_id}/token-usage")
async def get_task_token_usage_route(
    task_id: str, request: Request, db: AsyncSession = Depends(get_db)
):
    """LLM token usage recorded for a task by the provider manager.

    Totals come from the providers' own usage reports; calls whose response
    carried no usage are counted under ``unknown_usage_calls`` with no
    invented token numbers.
    """
    try:
        task_service = TaskService(db)
        await _require_task_reader(request, task_service, db, task_id)
        task = await task_service.get_task_with_clips(task_id)

        if not task:
            raise HTTPException(status_code=404, detail="Task not found")

        usage = await get_task_token_usage(db, task_id)
        return {"task_id": task_id, **usage}

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error retrieving token usage: {e}")
        raise HTTPException(
            status_code=500, detail=f"Error retrieving token usage: {str(e)}"
        )


@router.post("/{task_id}/share")
async def share_task(
    task_id: str, request: Request, db: AsyncSession = Depends(get_db)
):
    """Create or re-enable a stable, read-only share URL for a completed task."""
    task_service = TaskService(db)
    task = await _require_task_owner(request, task_service, db, task_id)
    if task.get("status") != "completed":
        raise HTTPException(
            status_code=409, detail="Only completed generations can be shared"
        )

    share_token = await task_service.task_repo.enable_sharing(
        db, task_id, secrets.token_urlsafe(32)
    )
    if not share_token:
        raise HTTPException(status_code=404, detail="Task not found")

    return {
        "share_token": share_token,
        "share_path": f"/share/{share_token}",
    }


@router.delete("/{task_id}/share")
async def unshare_task(
    task_id: str, request: Request, db: AsyncSession = Depends(get_db)
):
    """Disable the public URL while preserving the private generation result."""
    task_service = TaskService(db)
    await _require_task_owner(request, task_service, db, task_id)
    await task_service.task_repo.disable_sharing(db, task_id)
    return {"message": "Share link disabled"}


@router.get("/{task_id}/progress")
async def get_task_progress_sse(task_id: str, request: Request):
    """
    SSE endpoint for real-time progress updates.
    Streams progress updates as Server-Sent Events.
    """

    async with AsyncSessionLocal() as local_db:
        task_service = TaskService(local_db)
        task = await _require_task_reader(request, task_service, local_db, task_id)

    async def event_generator():
        """Generate SSE events for task progress."""
        # Send initial task status
        yield {
            "event": "status",
            "data": json.dumps(
                {
                    "task_id": task_id,
                    "status": task.get("status"),
                    "progress": task.get("progress", 0),
                    "message": task.get("progress_message", ""),
                }
            ),
        }

        # If task is already completed or error, close connection
        if task.get("status") in ["completed", "error", "cancelled"]:
            yield {"event": "close", "data": json.dumps({"status": task.get("status")})}
            return

        # Connect to Redis for real-time updates
        runtime_config = get_config()
        redis_client = redis.Redis(
            host=runtime_config.redis_host,
            port=runtime_config.redis_port,
            password=runtime_config.redis_password,
            decode_responses=True,
        )

        try:
            # Subscribe to progress updates
            async with aclosing(ProgressTracker.subscribe_to_progress(redis_client, task_id)) as updates:
                async for progress_data in updates:
                    event_type = progress_data.get("event_type", "progress")
                    yield {"event": event_type, "data": json.dumps(progress_data)}

                    # Close connection if task is done
                    if progress_data.get("status") in ["completed", "error", "cancelled"]:
                        yield {
                            "event": "close",
                            "data": json.dumps({"status": progress_data.get("status")}),
                        }
                        break
        finally:
            await redis_client.aclose()

    return EventSourceResponse(event_generator())


@router.patch("/{task_id}")
async def update_task(
    task_id: str, request: Request, db: AsyncSession = Depends(get_db)
):
    """Update task details (title)."""
    try:
        data = await _read_json_object(request)
        title = data.get("title")

        if not isinstance(title, str) or not title.strip():
            raise HTTPException(status_code=400, detail="Title is required")

        task_service = TaskService(db)

        task = await _require_task_owner(request, task_service, db, task_id)

        # Update source title
        await task_service.source_repo.update_source_title(db, task["source_id"], title)

        return {"message": "Task updated successfully", "task_id": task_id}

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error updating task: {e}")
        raise HTTPException(status_code=500, detail=f"Error updating task: {str(e)}")


@router.delete("/{task_id}")
async def delete_task(
    task_id: str, request: Request, db: AsyncSession = Depends(get_db)
):
    """Delete a task and all its associated clips."""
    try:
        user_id = await _get_user_id_from_headers(request, db)
        task_service = TaskService(db)

        # Get task to verify ownership
        task = await task_service.task_repo.get_task_by_id(db, task_id)
        if not task:
            raise HTTPException(status_code=404, detail="Task not found")

        if task["user_id"] != user_id:
            raise HTTPException(
                status_code=403, detail="Not authorized to delete this task"
            )

        # Delete clips and task
        await task_service.delete_task(task_id)

        return {"message": "Task deleted successfully"}

    except HTTPException:
        raise
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"Error deleting task: {e}")
        raise HTTPException(status_code=500, detail=f"Error deleting task: {str(e)}")


@router.post("/{task_id}/clips/{clip_id}/feedback")
async def record_clip_feedback(
    task_id: str, clip_id: str, request: Request, db: AsyncSession = Depends(get_db)
):
    """Record an explicit accept/reject verdict for one clip.

    Feeds the learning engine (auto_director.learning): the verdict, an
    optional reason, and a snapshot of the clip's virality sub-scores and
    styling are stored as one feedback event, and the user's adjustment
    document is refreshed. Recording never fails the request — a storage
    problem degrades to ``recorded: false``.
    """
    try:
        payload = await _read_json_object(request)
        verdict = payload.get("verdict")
        if verdict not in ("accept", "reject"):
            raise HTTPException(
                status_code=422,
                detail="Invalid verdict. Valid values: ['accept', 'reject'].",
            )
        reason = payload.get("reason")
        if reason is not None and reason not in LEARNING_REASONS:
            raise HTTPException(
                status_code=422,
                detail=(
                    f"Invalid reason. Valid values: {sorted(LEARNING_REASONS)} "
                    f"or null."
                ),
            )

        user_id = await _get_user_id_from_headers(request, db)
        task_service = TaskService(db)
        task = await task_service.task_repo.get_task_by_id(db, task_id)
        if not task:
            raise HTTPException(status_code=404, detail="Task not found")
        if task["user_id"] != user_id:
            raise HTTPException(
                status_code=403, detail="Not authorized for this task"
            )
        clip = await task_service.clip_repo.get_clip_by_id(db, clip_id)
        if not clip or clip["task_id"] != task_id:
            raise HTTPException(status_code=404, detail="Clip not found")

        return await record_explicit_feedback(
            db,
            user_id=user_id,
            task=task,
            clip=clip,
            verdict=verdict,
            reason=reason,
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error recording clip feedback: {e}")
        raise HTTPException(
            status_code=500, detail=f"Error recording clip feedback: {str(e)}"
        )


@router.delete("/{task_id}/clips/{clip_id}")
async def delete_clip(
    task_id: str, clip_id: str, request: Request, db: AsyncSession = Depends(get_db)
):
    """Delete a specific clip."""
    try:
        user_id = await _get_user_id_from_headers(request, db)
        task_service = TaskService(db)

        # Verify task ownership
        task = await task_service.task_repo.get_task_by_id(db, task_id)
        if not task:
            raise HTTPException(status_code=404, detail="Task not found")

        if task["user_id"] != user_id:
            raise HTTPException(
                status_code=403, detail="Not authorized to delete this clip"
            )

        clip = await task_service.clip_repo.get_clip_by_id(db, clip_id)
        if not clip or clip["task_id"] != task_id:
            raise HTTPException(status_code=404, detail="Clip not found")

        await task_service.delete_clip(task_id, clip_id)

        # Deleting a clip is a reject-leaning signal for the learning
        # engine (half-strength, passive). Snapshot uses the rows loaded
        # above, so it still works now that the clip is gone.
        await _record_passive_feedback(task, clip, "reject", "passive_delete")

        return {"message": "Clip deleted successfully"}

    except HTTPException:
        raise
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"Error deleting clip: {e}")
        raise HTTPException(status_code=500, detail=f"Error deleting clip: {str(e)}")


@router.get("/{task_id}/clips/{clip_id}/file")
async def get_clip_file(
    task_id: str,
    clip_id: str,
    request: Request,
    download: bool = False,
    db: AsyncSession = Depends(get_db, scope="function"),
):
    """Serve a clip file after verifying task ownership."""
    try:
        task_service = TaskService(db)
        task = await _require_task_reader(request, task_service, db, task_id)
        clip = await task_service.clip_repo.get_clip_by_id(db, clip_id)
        if not clip or clip.get("task_id") != task_id:
            raise HTTPException(status_code=404, detail="Clip not found")

        if download:
            # An explicit download (not inline playback) is an
            # accept-leaning signal for the learning engine.
            await _record_passive_feedback(
                task, clip, "accept", "passive_download"
            )

        if is_s3(clip["file_path"]):
            await db.close()
            url = await run_in_thread(MediaStorage().signed_url, clip["file_path"], clip["filename"], attachment=download)
            return RedirectResponse(url, status_code=307, headers={"Cache-Control": "private, no-store"})
        clip_path = Path(clip["file_path"])
        if not clip_path.exists():
            raise HTTPException(status_code=404, detail="Clip file not found")

        # Release the DB connection before streaming: FastAPI keeps a
        # `Depends(get_db)` session open for the full response lifetime, and a
        # FileResponse can take many seconds (large file, slow client, range
        # scrubbing). Holding it leaves the read-only transaction above as
        # "idle in transaction" in Postgres for the whole transfer, which
        # exhausts the connection pool under concurrent playback.
        await db.close()

        return FileResponse(
            path=str(clip_path),
            media_type="video/mp4",
            filename=clip["filename"],
            content_disposition_type="attachment" if download else "inline",
            headers={"Cache-Control": "private, no-store"},
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error serving clip file: {e}")
        raise HTTPException(status_code=500, detail=f"Error serving clip file: {str(e)}")


async def _prepare_clip_archive(request: Request, db: AsyncSession, task_id: str):
    """Authorize like a single-clip download and list the clip files that exist."""
    task_service = TaskService(db)
    user_id, task = await _authorize_task_reader(request, task_service, db, task_id)
    clips = await task_service.clip_repo.get_clips_by_task(db, task_id)
    # Release the DB connection before storage lookups and the long stream;
    # see get_clip_file.
    await db.close()
    try:
        entries = await run_in_thread(clip_archive.resolve_entries, clips, MediaStorage())
    except STORAGE_ERRORS:
        logger.exception("Could not list clip files for task %s", task_id)
        raise HTTPException(
            status_code=503,
            detail="Clip storage is unavailable right now. Please try again shortly.",
        ) from None
    if not entries:
        raise HTTPException(
            status_code=404,
            detail="This generation has no clip files to download.",
        )
    return user_id, task, entries


@router.get("/{task_id}/clips/zip/preflight")
async def preflight_clip_archive(
    task_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db, scope="function"),
):
    """Check a ZIP download would start, so a browser can surface errors first."""
    user_id, task, entries = await _prepare_clip_archive(request, db, task_id)
    if await clip_archive.archive_in_progress(user_id):
        raise HTTPException(
            status_code=429,
            detail="A ZIP download of your clips is already in progress. Wait for it to finish, then try again.",
        )
    return {
        "filename": clip_archive.archive_filename(task.get("source_title")),
        "clip_count": len(entries),
        "total_bytes": sum(entry.size for entry in entries),
    }


@router.get("/{task_id}/clips/zip")
async def download_clip_archive(
    task_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db, scope="function"),
):
    """Stream every existing clip of the task as one ZIP, built on the fly."""
    user_id, task, entries = await _prepare_clip_archive(request, db, task_id)
    try:
        slot = await clip_archive.ArchiveSlot(user_id).acquire()
    except clip_archive.ClipArchiveBusy as exc:
        raise HTTPException(status_code=429, detail=str(exc)) from None
    filename = clip_archive.archive_filename(task.get("source_title"))
    return StreamingResponse(
        clip_archive.stream_with_slot(entries, MediaStorage(), slot),
        media_type="application/zip",
        headers={
            "Content-Disposition": clip_archive.content_disposition(filename),
            "Cache-Control": "private, no-store",
            # Let nginx pass chunks through instead of buffering the archive.
            "X-Accel-Buffering": "no",
        },
    )


@router.patch("/{task_id}/clips/{clip_id}")
async def trim_clip(
    task_id: str, clip_id: str, request: Request, db: AsyncSession = Depends(get_db)
):
    """Trim clip boundaries and regenerate clip file."""
    try:
        payload = await _read_json_object(request)
        start_offset = _finite_number(payload.get("start_offset", 0), "start_offset")
        end_offset = _finite_number(payload.get("end_offset", 0), "end_offset")

        if start_offset < 0 or end_offset < 0:
            raise HTTPException(status_code=400, detail="Offsets must be non-negative")

        task_service = TaskService(db)
        await _require_task_owner(request, task_service, db, task_id)
        updated_clip = await task_service.trim_clip(
            task_id, clip_id, start_offset, end_offset
        )
        return {"clip": updated_clip}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error trimming clip: {e}")
        raise HTTPException(status_code=500, detail=f"Error trimming clip: {str(e)}")


@router.post("/{task_id}/clips/{clip_id}/split")
async def split_clip(
    task_id: str, clip_id: str, request: Request, db: AsyncSession = Depends(get_db)
):
    """Split a clip into two clips."""
    try:
        payload = await _read_json_object(request)
        split_time = _finite_number(payload.get("split_time", 0), "split_time")
        if split_time <= 0:
            raise HTTPException(
                status_code=400, detail="split_time must be greater than zero"
            )

        task_service = TaskService(db)
        await _require_task_owner(request, task_service, db, task_id)
        result = await task_service.split_clip(task_id, clip_id, split_time)
        return result
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error splitting clip: {e}")
        raise HTTPException(status_code=500, detail=f"Error splitting clip: {str(e)}")


@router.post("/{task_id}/clips/merge")
async def merge_clips(
    task_id: str, request: Request, db: AsyncSession = Depends(get_db)
):
    """Merge multiple clips into one clip."""
    try:
        payload = await _read_json_object(request)
        clip_ids = payload.get("clip_ids") or []
        if not isinstance(clip_ids, list) or any(not isinstance(cid, str) or not cid for cid in clip_ids):
            raise HTTPException(status_code=400, detail="clip_ids must be an array")

        task_service = TaskService(db)
        await _require_task_owner(request, task_service, db, task_id)
        result = await task_service.merge_clips(task_id, clip_ids)
        return result
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error merging clips: {e}")
        raise HTTPException(status_code=500, detail=f"Error merging clips: {str(e)}")


@router.patch("/{task_id}/clips/{clip_id}/captions")
async def update_clip_captions(
    task_id: str, clip_id: str, request: Request, db: AsyncSession = Depends(get_db)
):
    """Update clip caption text, timing style and highlighted words."""
    try:
        payload = await _read_json_object(request)
        caption_text = str(payload.get("caption_text", "")).strip()
        position = str(payload.get("position", "bottom"))
        highlight_words = payload.get("highlight_words") or []
        if not isinstance(highlight_words, list):
            raise HTTPException(
                status_code=400, detail="highlight_words must be an array"
            )

        font_size = payload.get("font_size")
        if font_size is not None:
            font_size = _finite_number(font_size, "font_size")
            if not math.isfinite(font_size) or not 12 <= font_size <= 72:
                raise ValueError("Font size must be between 12 and 72")
            font_size = int(font_size)
        position_y = payload.get("position_y")
        if position_y is not None:
            position_y = _finite_number(position_y, "position_y")
            if not math.isfinite(position_y) or not 0.1 <= position_y <= 0.85:
                raise ValueError("Caption position must be between 0.1 and 0.85")
        if position not in {"top", "middle", "bottom"}:
            raise ValueError("Invalid caption position")

        task_service = TaskService(db)
        await _require_task_owner(request, task_service, db, task_id)
        updated_clip = await task_service.update_clip_captions(
            task_id,
            clip_id,
            caption_text,
            position,
            [str(word) for word in highlight_words],
            font_size=font_size,
            position_y=position_y,
        )
        return {"clip": updated_clip}
    except YouTubeGeoRestrictedError as exc:
        raise HTTPException(
            status_code=422,
            detail={"code": "YOUTUBE_GEO_RESTRICTED", "message": str(exc)},
        ) from None
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error updating captions: {e}")
        raise HTTPException(
            status_code=500, detail=f"Error updating captions: {str(e)}"
        )


@router.post("/{task_id}/clips/{clip_id}/regenerate")
async def regenerate_clip(
    task_id: str, clip_id: str, request: Request, db: AsyncSession = Depends(get_db)
):
    """Regenerate a single clip after editing timing values."""
    try:
        payload = await _read_json_object(request)
        start_offset = _finite_number(payload.get("start_offset", 0), "start_offset")
        end_offset = _finite_number(payload.get("end_offset", 0), "end_offset")

        task_service = TaskService(db)
        await _require_task_owner(request, task_service, db, task_id)
        updated_clip = await task_service.trim_clip(
            task_id, clip_id, start_offset, end_offset
        )
        return {"clip": updated_clip, "message": "Clip regenerated successfully"}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error regenerating clip: {e}")
        raise HTTPException(
            status_code=500, detail=f"Error regenerating clip: {str(e)}"
        )


@router.post("/{task_id}/clips/{clip_id}/re-render", status_code=202)
async def rerender_clip(
    task_id: str, clip_id: str, request: Request, db: AsyncSession = Depends(get_db)
):
    """Queue a re-render of one clip with a different niche profile.

    The clip's footage and transcript are reused; only its auto-director
    styling (pacing, transitions, captions, overlays, music) is rebuilt.
    Poll the companion status route for the refreshed clip.
    """
    try:
        payload = await _read_json_object(request)
        try:
            niche_override = validate_niche_override(payload.get("niche_override"))
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from None
        if niche_override is None:
            raise HTTPException(
                status_code=422, detail="niche_override is required to re-render a clip"
            )
        caption_template = payload.get("caption_template")
        if caption_template is not None and (
            not isinstance(caption_template, str)
            or caption_template not in get_template_names()
        ):
            raise HTTPException(
                status_code=422,
                detail=(
                    f"Invalid caption_template. Valid values: "
                    f"{sorted(get_template_names())}."
                ),
            )
        transition = payload.get("transition")
        if transition is not None and (
            not isinstance(transition, str) or transition not in TRANSITION_MAP
        ):
            raise HTTPException(
                status_code=422,
                detail=(
                    f"Invalid transition. Valid values: {sorted(TRANSITION_MAP)}."
                ),
            )
        # Caption Studio overrides on a plain re-render: same fields, same
        # validation as the edit endpoint; they are forwarded to the worker
        # only when present so older clients see no behaviour change.
        captions = _validate_caption_overrides(payload)
        # Studio look overrides travel the same way (via the edit job).
        look = _validate_look_overrides(payload)
        # Audio Studio / voiceover overrides travel the same way too.
        audio = _validate_audio_overrides(payload)
        voiceover = _validate_voiceover_request(payload)
        sfx = _validate_sfx_placements(payload)

        task_service = TaskService(db)
        task = await _require_task_owner(request, task_service, db, task_id)
        clip = await task_service.clip_repo.get_clip_by_id(db, clip_id)
        if not clip or clip["task_id"] != task_id:
            raise HTTPException(status_code=404, detail="Clip not found")

        job_id = f"clip-rerender:{clip_id}:{secrets.token_hex(6)}"
        queue_adapter = getattr(request.app.state, "queue_adapter", JobQueue)
        if (
            captions is not None
            or look is not None
            or audio is not None
            or voiceover is not None
            or sfx is not None
        ):
            # Route caption/look/audio-override re-renders through the edit
            # job, which forwards the overrides into the same Lane-5 seam.
            await queue_adapter.enqueue_job(
                "edit_clip_task",
                task_id,
                clip_id,
                None,
                captions,
                niche_override,
                None,
                look,
                audio,
                voiceover,
                sfx,
                _job_id=job_id,
            )
        else:
            await queue_adapter.enqueue_job(
                "rerender_clip_task",
                task_id,
                clip_id,
                niche_override,
                caption_template,
                transition,
                _job_id=job_id,
            )
        try:
            pool = await JobQueue.get_pool()
            await set_rerender_outcome(
                pool,
                job_id,
                {
                    "status": "queued",
                    "clip_id": clip_id,
                    "niche_override": niche_override,
                },
            )
        except Exception:
            logger.warning(
                "Could not record queued re-render state for %s", job_id, exc_info=True
            )
        # Asking for a re-render is a dissatisfaction signal for the
        # learning engine, attributed to the niche being switched TO.
        await _record_passive_feedback(
            task,
            clip,
            "reject",
            "passive_rerender",
            niche_override=niche_override,
        )
        return {
            "clip_id": clip_id,
            "job_id": job_id,
            "status": "queued",
            "niche_override": niche_override,
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error queueing clip re-render: {e}")
        raise HTTPException(
            status_code=500, detail=f"Error queueing clip re-render: {str(e)}"
        )


@router.get("/{task_id}/clips/{clip_id}/re-render/{job_id}")
async def get_clip_rerender_status(
    task_id: str,
    clip_id: str,
    job_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Report a queued re-render's state; carries the refreshed clip when done."""
    try:
        task_service = TaskService(db)
        await _require_task_owner(request, task_service, db, task_id)
        clip = await task_service.clip_repo.get_clip_by_id(db, clip_id)
        if not clip or clip["task_id"] != task_id:
            raise HTTPException(status_code=404, detail="Clip not found")
        if not job_id.startswith(f"clip-rerender:{clip_id}:"):
            raise HTTPException(status_code=404, detail="Re-render job not found")

        outcome = None
        try:
            pool = await JobQueue.get_pool()
            outcome = await get_rerender_outcome(pool, job_id)
        except Exception:
            logger.warning(
                "Could not read re-render state for %s", job_id, exc_info=True
            )
        if outcome:
            return {"job_id": job_id, "clip_id": clip_id, **outcome}

        status = await JobQueue.get_job_status(job_id)
        arq_status = getattr(status, "value", status)
        mapped = {
            "deferred": "queued",
            "queued": "queued",
            "in_progress": "processing",
            "complete": "completed",
        }.get(str(arq_status), "unknown")
        return {"job_id": job_id, "clip_id": clip_id, "status": mapped}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error reading clip re-render status: {e}")
        raise HTTPException(
            status_code=500, detail=f"Error reading clip re-render status: {str(e)}"
        )



@router.post("/{task_id}/clips/{clip_id}/hook-variants", status_code=202)
async def generate_clip_hook_variants(
    task_id: str, clip_id: str, request: Request, db: AsyncSession = Depends(get_db)
):
    """Queue hook-variant preview renders for one clip (Lane K).

    The variants are derived deterministically from the clip and its
    stored plan — no new AI calls — and rendered short (~6s) through the
    real EDL path. Poll the companion status route for the previews.
    """
    try:
        task_service = TaskService(db)
        await _require_task_owner(request, task_service, db, task_id)
        clip = await task_service.clip_repo.get_clip_by_id(db, clip_id)
        if not clip or clip["task_id"] != task_id:
            raise HTTPException(status_code=404, detail="Clip not found")

        job_id = f"hook-previews:{clip_id}:{secrets.token_hex(6)}"
        queue_adapter = getattr(request.app.state, "queue_adapter", JobQueue)
        await queue_adapter.enqueue_job(
            "generate_hook_previews_task",
            task_id,
            clip_id,
            _job_id=job_id,
        )
        try:
            pool = await JobQueue.get_pool()
            await set_rerender_outcome(
                pool,
                job_id,
                {
                    "status": "queued",
                    "clip_id": clip_id,
                    "kind": "hook_previews",
                },
            )
        except Exception:
            logger.warning(
                "Could not record queued hook preview state for %s",
                job_id,
                exc_info=True,
            )
        return {"clip_id": clip_id, "job_id": job_id, "status": "queued"}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error queueing hook preview generation: {e}")
        raise HTTPException(
            status_code=500,
            detail=f"Error queueing hook preview generation: {str(e)}",
        )


@router.get("/{task_id}/clips/{clip_id}/hook-variants/{job_id}")
async def get_clip_hook_variants_status(
    task_id: str,
    clip_id: str,
    job_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Report a hook-preview generation job; carries the previews when done."""
    try:
        task_service = TaskService(db)
        await _require_task_owner(request, task_service, db, task_id)
        clip = await task_service.clip_repo.get_clip_by_id(db, clip_id)
        if not clip or clip["task_id"] != task_id:
            raise HTTPException(status_code=404, detail="Clip not found")
        if not job_id.startswith(f"hook-previews:{clip_id}:"):
            raise HTTPException(
                status_code=404, detail="Hook preview job not found"
            )

        outcome = None
        try:
            pool = await JobQueue.get_pool()
            outcome = await get_rerender_outcome(pool, job_id)
        except Exception:
            logger.warning(
                "Could not read hook preview state for %s",
                job_id,
                exc_info=True,
            )
        if outcome:
            return {"job_id": job_id, "clip_id": clip_id, **outcome}

        status = await JobQueue.get_job_status(job_id)
        arq_status = getattr(status, "value", status)
        mapped = {
            "deferred": "queued",
            "queued": "queued",
            "in_progress": "processing",
            "complete": "completed",
        }.get(str(arq_status), "unknown")
        return {"job_id": job_id, "clip_id": clip_id, "status": mapped}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error reading hook preview status: {e}")
        raise HTTPException(
            status_code=500,
            detail=f"Error reading hook preview status: {str(e)}",
        )


@router.get("/{task_id}/clips/{clip_id}/hook-variants")
async def list_clip_hook_variants(
    task_id: str, clip_id: str, request: Request, db: AsyncSession = Depends(get_db)
):
    """The clip's latest hook-preview batch, with the applied one marked."""
    try:
        task_service = TaskService(db)
        await _require_task_owner(request, task_service, db, task_id)
        return await task_service.list_hook_previews(task_id, clip_id)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error listing hook previews: {e}")
        raise HTTPException(
            status_code=500, detail=f"Error listing hook previews: {str(e)}"
        )


@router.post(
    "/{task_id}/clips/{clip_id}/hook-variants/{preview_id}/select",
    status_code=202,
)
async def select_clip_hook_variant(
    task_id: str,
    clip_id: str,
    preview_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Queue applying a picked hook preview to the full clip.

    The apply is a re-render through the Lane-5 seam, so the returned
    job id polls on the re-render status route and the outcome carries
    the refreshed clip (with the recorded pick on it).
    """
    try:
        task_service = TaskService(db)
        await _require_task_owner(request, task_service, db, task_id)
        clip = await task_service.clip_repo.get_clip_by_id(db, clip_id)
        if not clip or clip["task_id"] != task_id:
            raise HTTPException(status_code=404, detail="Clip not found")
        preview = await task_service.hook_preview_repo.get_preview(db, preview_id)
        if (
            not preview
            or preview["clip_id"] != clip_id
            or preview["task_id"] != task_id
        ):
            raise HTTPException(status_code=404, detail="Hook preview not found")

        job_id = f"clip-rerender:{clip_id}:{secrets.token_hex(6)}"
        queue_adapter = getattr(request.app.state, "queue_adapter", JobQueue)
        await queue_adapter.enqueue_job(
            "apply_hook_variant_task",
            task_id,
            clip_id,
            preview_id,
            _job_id=job_id,
        )
        try:
            pool = await JobQueue.get_pool()
            await set_rerender_outcome(
                pool,
                job_id,
                {
                    "status": "queued",
                    "clip_id": clip_id,
                    "kind": "hook_variant",
                    "hook_variant": preview["variant"],
                },
            )
        except Exception:
            logger.warning(
                "Could not record queued hook variant state for %s",
                job_id,
                exc_info=True,
            )
        return {
            "clip_id": clip_id,
            "job_id": job_id,
            "status": "queued",
            "hook_variant": preview["variant"],
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error queueing hook variant selection: {e}")
        raise HTTPException(
            status_code=500,
            detail=f"Error queueing hook variant selection: {str(e)}",
        )


@router.get("/{task_id}/clips/{clip_id}/hook-variants/{preview_id}/file")
async def get_clip_hook_variant_file(
    task_id: str,
    clip_id: str,
    preview_id: str,
    request: Request,
    download: bool = False,
    db: AsyncSession = Depends(get_db),
):
    """Serve one rendered hook preview file for inline playback."""
    task_service = TaskService(db)
    await _require_task_reader(request, task_service, db, task_id)
    preview = await task_service.hook_preview_repo.get_preview(db, preview_id)
    if (
        not preview
        or preview["clip_id"] != clip_id
        or preview["task_id"] != task_id
    ):
        raise HTTPException(status_code=404, detail="Hook preview not found")

    if is_s3(preview["file_path"]):
        await db.close()
        url = await run_in_thread(
            MediaStorage().signed_url,
            preview["file_path"],
            preview["file_name"],
            attachment=download,
        )
        return RedirectResponse(
            url, status_code=307, headers={"Cache-Control": "private, no-store"}
        )
    preview_path = Path(
        MediaStorage().local_path(preview["file_path"], metadata_only=True)
    )
    if not preview_path.exists():
        raise HTTPException(status_code=404, detail="Hook preview file not found")

    return FileResponse(
        path=str(preview_path),
        media_type="video/mp4",
        filename=preview["file_name"],
        content_disposition_type="attachment" if download else "inline",
        headers={"Cache-Control": "private, no-store"},
    )

def _validate_caption_overrides(payload: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Validate the Caption Studio block of an edit / re-render request.

    Structure and ranges are checked by ``normalize_caption_overrides``;
    the template name and font family are checked here against the live
    registries, mirroring the re-render route's own validation style.
    """
    try:
        overrides = normalize_caption_overrides(payload.get("captions"))
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None
    if overrides is None:
        return None
    template = overrides.get("template")
    if template is not None and template not in get_template_names():
        raise HTTPException(
            status_code=422,
            detail=(
                f"Invalid captions.template. Valid values: "
                f"{sorted(get_template_names())}."
            ),
        )
    font_family = overrides.get("font_family")
    if font_family is not None:
        known = any(
            entry.get("filename") == font_family
            for entry in get_available_fonts()
        )
        if not known:
            for name in get_template_names():
                if get_template(name).get("font_family") == font_family:
                    known = True
                    break
        if not known:
            raise HTTPException(
                status_code=422,
                detail=f"Unknown captions.font_family '{font_family}'.",
            )
    return overrides


def _validate_look_overrides(payload: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Validate the studio look block of an edit / re-render request.

    ``normalize_look_overrides`` checks the shape, the slider ranges and
    the transition name against the renderer's catalog, mirroring the
    caption block's split of responsibilities.
    """
    try:
        return normalize_look_overrides(payload.get("look"))
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None


def _validate_audio_overrides(payload: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Validate the Audio Studio block of an edit / re-render request.

    ``normalize_audio_overrides`` checks the shape and ranges; a named
    music track is checked here against the live library, mirroring the
    caption block's split of responsibilities.
    """
    from ...auto_director.audio_studio import normalize_audio_overrides

    try:
        overrides = normalize_audio_overrides(payload.get("audio"))
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None
    if overrides is None:
        return None
    track = overrides.get("music_track")
    if track and track not in ("auto", "none"):
        try:
            validate_music_override(track)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from None
    return overrides


def _validate_voiceover_request(payload: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Validate the voiceover block of an edit / re-render request.

    ``normalize_voiceover_request`` enforces the deployment gate
    (VOICEOVER_ENABLED, default off) as well as the spec shape, so a
    disabled feature rejects the block here instead of storing it.
    """
    from ...media.voiceover import normalize_voiceover_request

    try:
        return normalize_voiceover_request(payload.get("voiceover"))
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None


def _validate_sfx_placements(payload: Dict[str, Any]) -> Optional[list]:
    """Validate the SFX placements block of an edit / re-render request.

    ``normalize_sfx_placements`` checks the shape and ranges; each named
    file is checked here against the live SFX library (the exact folder
    the renderer resolves against), so an unplaceable name is a 422 at
    request time instead of a silent no-op at render time.
    """
    from ...auto_director.sfx import normalize_sfx_placements
    from ...media.sfx_library import find_sfx_file

    try:
        placements = normalize_sfx_placements(payload.get("sfx"))
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None
    if placements is None:
        return None
    for placement in placements:
        if find_sfx_file(placement["name"]) is None:
            raise HTTPException(
                status_code=422,
                detail=(
                    f"SFX file '{placement['name']}' is not in the SFX "
                    "library"
                ),
            )
    return placements


@router.get("/{task_id}/clips/{clip_id}/edit")
async def get_clip_edit_plan(
    task_id: str, clip_id: str, request: Request, db: AsyncSession = Depends(get_db)
):
    """The clip's current edit plan (segments + caption overrides)."""
    try:
        task_service = TaskService(db)
        await _require_task_owner(request, task_service, db, task_id)
        return await task_service.get_clip_edit_plan(task_id, clip_id)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error reading clip edit plan: {e}")
        raise HTTPException(
            status_code=500, detail=f"Error reading clip edit plan: {str(e)}"
        )


@router.get("/{task_id}/clips/{clip_id}/compliance")
async def get_clip_compliance(
    task_id: str, clip_id: str, request: Request, db: AsyncSession = Depends(get_db)
):
    """One clip's campaign-brief compliance report (owner only)."""
    try:
        task_service = TaskService(db)
        await _require_task_owner(request, task_service, db, task_id)
        clip = await task_service.clip_repo.get_clip_by_id(db, clip_id)
        if not clip or clip.get("task_id") != task_id:
            raise HTTPException(status_code=404, detail="Clip not found")
        raw_report = clip.get("compliance_report")
        if not raw_report:
            raise HTTPException(
                status_code=404, detail="No compliance report for this clip"
            )
        report = raw_report
        if isinstance(raw_report, str):
            try:
                report = json.loads(raw_report)
            except ValueError:
                report = raw_report
        return {
            "task_id": task_id,
            "clip_id": clip_id,
            "compliance_status": clip.get("compliance_status"),
            "compliance_report": report,
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error reading clip compliance: {e}")
        raise HTTPException(
            status_code=500, detail=f"Error reading clip compliance: {str(e)}"
        )


@router.post("/{task_id}/clips/{clip_id}/edit", status_code=202)
async def edit_clip(
    task_id: str, clip_id: str, request: Request, db: AsyncSession = Depends(get_db)
):
    """Queue an editor edit (segment operations and/or caption overrides).

    The operations mutate the clip's EDL segment layout and the worker
    re-renders through the Lane-5 path, so an accepted edit always changes
    the rendered file. Poll the re-render status route with the returned
    job id — edit jobs share its outcome key space.
    """
    try:
        payload = await _read_json_object(request)
        operations = payload.get("operations")
        if operations is not None and not isinstance(operations, list):
            raise HTTPException(
                status_code=422, detail="operations must be an array"
            )
        captions = _validate_caption_overrides(payload)
        niche_override = payload.get("niche_override")
        if niche_override is not None:
            try:
                niche_override = validate_niche_override(niche_override)
            except ValueError as exc:
                raise HTTPException(status_code=422, detail=str(exc)) from None
        music_override = payload.get("music_override")
        if music_override is not None:
            try:
                music_override = validate_music_override(music_override)
            except ValueError as exc:
                raise HTTPException(status_code=422, detail=str(exc)) from None
        look = _validate_look_overrides(payload)
        audio = _validate_audio_overrides(payload)
        voiceover = _validate_voiceover_request(payload)
        sfx = _validate_sfx_placements(payload)
        if (
            not operations
            and captions is None
            and look is None
            and audio is None
            and voiceover is None
            and sfx is None
        ):
            raise HTTPException(
                status_code=422,
                detail=(
                    "Nothing to edit: provide operations, captions, look, "
                    "audio, voiceover and/or sfx"
                ),
            )

        task_service = TaskService(db)
        task = await _require_task_owner(request, task_service, db, task_id)
        clip = await task_service.clip_repo.get_clip_by_id(db, clip_id)
        if not clip or clip["task_id"] != task_id:
            raise HTTPException(status_code=404, detail="Clip not found")

        job_id = f"clip-edit:{clip_id}:{secrets.token_hex(6)}"
        queue_adapter = getattr(request.app.state, "queue_adapter", JobQueue)
        await queue_adapter.enqueue_job(
            "edit_clip_task",
            task_id,
            clip_id,
            operations,
            captions,
            niche_override,
            music_override,
            look,
            audio,
            voiceover,
            sfx,
            _job_id=job_id,
        )
        try:
            pool = await JobQueue.get_pool()
            await set_rerender_outcome(
                pool,
                job_id,
                {"status": "queued", "clip_id": clip_id, "kind": "edit"},
            )
        except Exception:
            logger.warning(
                "Could not record queued edit state for %s", job_id, exc_info=True
            )
        return {
            "clip_id": clip_id,
            "job_id": job_id,
            "status": "queued",
            "kind": "edit",
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error queueing clip edit: {e}")
        raise HTTPException(
            status_code=500, detail=f"Error queueing clip edit: {str(e)}"
        )


@router.get("/{task_id}/clips/{clip_id}/edit/{job_id}")
async def get_clip_edit_status(
    task_id: str,
    clip_id: str,
    job_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Report a queued edit job's state (shares the re-render outcomes)."""
    try:
        task_service = TaskService(db)
        await _require_task_owner(request, task_service, db, task_id)
        clip = await task_service.clip_repo.get_clip_by_id(db, clip_id)
        if not clip or clip["task_id"] != task_id:
            raise HTTPException(status_code=404, detail="Clip not found")
        if not job_id.startswith(f"clip-edit:{clip_id}:"):
            raise HTTPException(status_code=404, detail="Edit job not found")

        outcome = None
        try:
            pool = await JobQueue.get_pool()
            outcome = await get_rerender_outcome(pool, job_id)
        except Exception:
            logger.warning(
                "Could not read edit state for %s", job_id, exc_info=True
            )
        if outcome:
            return {"job_id": job_id, "clip_id": clip_id, **outcome}

        status = await JobQueue.get_job_status(job_id)
        arq_status = getattr(status, "value", status)
        mapped = {
            "deferred": "queued",
            "queued": "queued",
            "in_progress": "processing",
            "complete": "completed",
        }.get(str(arq_status), "unknown")
        return {"job_id": job_id, "clip_id": clip_id, "status": mapped}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error reading clip edit status: {e}")
        raise HTTPException(
            status_code=500, detail=f"Error reading clip edit status: {str(e)}"
        )


@router.post("/{task_id}/clips/{clip_id}/ai-edit", status_code=202)
async def ai_edit_clip(
    task_id: str, clip_id: str, request: Request, db: AsyncSession = Depends(get_db)
):
    """Translate a plain-language instruction into a queued editor edit.

    The AI assistant (``ClipAiEditService``) may only speak the editor's
    own operations; its reply is schema-validated and dry-run against
    the clip's current plan before anything is queued, so a bad reply is
    a 422 here and the clip stays untouched. A good one queues the SAME
    ``edit_clip_task`` job as a manual edit — poll the edit status route
    with the returned job id. The response carries the plain-language
    ``summary`` generated from the operations that will be applied.
    """
    try:
        payload = await _read_json_object(request)
        instruction = payload.get("instruction")
        if not isinstance(instruction, str) or not instruction.strip():
            raise HTTPException(
                status_code=422,
                detail="instruction must be a non-empty string",
            )

        task_service = TaskService(db)
        await _require_task_owner(request, task_service, db, task_id)
        clip = await task_service.clip_repo.get_clip_by_id(db, clip_id)
        if not clip or clip["task_id"] != task_id:
            raise HTTPException(status_code=404, detail="Clip not found")

        ai_service = ClipAiEditService(task_service)
        try:
            edit = await ai_service.build_edit(task_id, clip_id, instruction)
        except AiEditUnavailable as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

        job_id = f"clip-edit:{clip_id}:{secrets.token_hex(6)}"
        queue_adapter = getattr(request.app.state, "queue_adapter", JobQueue)
        await queue_adapter.enqueue_job(
            "edit_clip_task",
            task_id,
            clip_id,
            edit.get("operations"),
            edit.get("captions"),
            None,
            edit.get("music_override"),
            _job_id=job_id,
        )
        try:
            pool = await JobQueue.get_pool()
            await set_rerender_outcome(
                pool,
                job_id,
                {
                    "status": "queued",
                    "clip_id": clip_id,
                    "kind": "edit",
                    "source": "ai-edit",
                    "summary": edit["summary"],
                },
            )
        except Exception:
            logger.warning(
                "Could not record queued AI edit state for %s",
                job_id,
                exc_info=True,
            )
        return {
            "clip_id": clip_id,
            "job_id": job_id,
            "status": "queued",
            "kind": "edit",
            "summary": edit["summary"],
            "operations": edit.get("operations"),
            "captions": edit.get("captions"),
            "music_override": edit.get("music_override"),
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error queueing AI clip edit: {e}")
        raise HTTPException(
            status_code=500, detail=f"Error queueing AI clip edit: {str(e)}"
        )


@router.post("/{task_id}/settings")
async def apply_task_settings(
    task_id: str, request: Request, db: AsyncSession = Depends(get_db)
):
    """Update task-level styling settings and optionally apply to all existing clips."""
    try:
        payload = await _read_json_object(request)
        font_family = _normalize_font_family(payload.get("font_family"))
        font_size = _normalize_font_size(payload.get("font_size"))
        font_color = _normalize_font_color(payload.get("font_color"))
        caption_template = payload.get("caption_template", "default")
        apply_to_existing = bool(payload.get("apply_to_existing", False))

        task_service = TaskService(db)
        await _require_task_owner(request, task_service, db, task_id)
        task_record = await task_service.task_repo.get_task_by_id(db, task_id)
        if not task_record:
            raise HTTPException(status_code=404, detail="Task not found")
        # Like cleanup below: an omitted flag keeps the task's B-roll setting,
        # so a styling-only update doesn't strip B-roll from every clip.
        include_broll = (
            payload["include_broll"] is True
            if "include_broll" in payload
            else bool(task_record.get("include_broll"))
        )

        # Cleanup fields the request omits keep the task's stored values, so a
        # styling-only update can't silently switch pause/filler cleanup off.
        metadata = await _load_task_source_metadata(task_id, db=db)
        cleanup_settings = updated_cleanup_settings(payload, metadata)
        if font_family is not None and not is_font_accessible(
            font_family, task_record["user_id"]
        ):
            raise HTTPException(
                status_code=400, detail="Selected font is not available"
            )
        task = await task_service.update_task_settings(
            task_id,
            font_family,
            font_size,
            font_color,
            caption_template,
            include_broll,
            apply_to_existing,
            cleanup_settings,
        )
        # Settings edits must not write the job ID captured before a lengthy
        # regeneration: a concurrent resume may have reserved a newer attempt.
        metadata.pop("processing_job_id", None)
        await _save_task_source_metadata(
            task_id,
            _merge_task_source_metadata(
                metadata,
                source_url=metadata.get("url") or task_record.get("source_url"),
                source_type=metadata.get("source_type") or task_record.get("source_type"),
                output_format=metadata.get("output_format") or task.get("output_format"),
                add_subtitles=(
                    metadata["add_subtitles"]
                    if isinstance(metadata.get("add_subtitles"), bool)
                    else task.get("add_subtitles")
                ),
                cleanup_settings=cleanup_settings,
            ),
            db=db,
        )
        return {"task": task, "message": "Task settings updated"}
    except YouTubeGeoRestrictedError as exc:
        raise HTTPException(
            status_code=422,
            detail={"code": "YOUTUBE_GEO_RESTRICTED", "message": str(exc)},
        ) from None
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error updating task settings: {e}")
        raise HTTPException(
            status_code=500, detail=f"Error updating task settings: {str(e)}"
        )


@router.get("/{task_id}/clips/{clip_id}/export")
@media_work
async def export_clip(
    task_id: str,
    clip_id: str,
    request: Request,
    preset: str = "tiktok",
    db: AsyncSession = Depends(get_db, scope="function"),
):
    """Export clip with a social platform preset."""
    try:
        preset_name = preset.lower().strip()
        if preset_name not in EXPORT_PRESETS:
            raise HTTPException(
                status_code=400,
                detail=f"Invalid preset. Use one of: {', '.join(EXPORT_PRESETS.keys())}",
            )

        task_service = TaskService(db)
        task = await _require_task_reader(request, task_service, db, task_id)
        clip = await task_service.clip_repo.get_clip_by_id(db, clip_id)
        if not clip or clip.get("task_id") != task_id:
            raise HTTPException(status_code=404, detail="Clip not found")

        from pathlib import Path

        # Release the DB connection before the (potentially multi-minute) ffmpeg
        # re-encode and file streaming below — see get_clip_file for why holding
        # it here turns into "idle in transaction" connection-pool exhaustion.
        await db.close()

        runtime_config = get_config()
        output_path = await run_in_thread(
            export_with_preset,
            await run_in_thread(MediaStorage().local_path, clip["file_path"]),
            Path(runtime_config.temp_dir) / "exports",
            preset_name,
        )

        # Exporting a clip for posting is an accept-leaning signal for
        # the learning engine (recorded only once the export succeeded).
        await _record_passive_feedback(task, clip, "accept", "passive_export")

        download_name = f"{Path(clip['filename']).stem}_{preset_name}.mp4"
        return FileResponse(
            path=str(output_path), media_type="video/mp4", filename=download_name,
            background=BackgroundTask(output_path.unlink, missing_ok=True),
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error exporting clip: {e}")
        raise HTTPException(status_code=500, detail=f"Error exporting clip: {str(e)}")


@router.post("/{task_id}/cancel")
async def cancel_task(
    task_id: str, request: Request, db: AsyncSession = Depends(get_db)
):
    """Cancel an active queued or processing task."""
    try:
        task_service = TaskService(db)
        task = await _require_task_owner(request, task_service, db, task_id)

        if task.get("status") in ["completed", "error", "cancelled"]:
            return {"message": f"Task already in terminal state: {task.get('status')}"}

        runtime_config = get_config()
        redis_client = redis.Redis(
            host=runtime_config.redis_host,
            port=runtime_config.redis_port,
            password=runtime_config.redis_password,
            decode_responses=True,
        )
        try:
            await redis_client.setex(f"task_cancel:{task_id}", 3600, "1")
        finally:
            await redis_client.aclose()

        await task_service.task_repo.update_task_status(
            db,
            task_id,
            "cancelled",
            progress=0,
            progress_message="Cancelled by user",
            expected_statuses=["queued", "processing", "pending"],
        )

        # Best effort: abort the arq job as well, so a queued job never
        # starts and a running job is interrupted between pipeline
        # checkpoints. The Redis flag and the DB status above are
        # authoritative — a failed abort never fails the cancel request.
        try:
            metadata = await _load_task_source_metadata(task_id, db=db)
            processing_job_id = metadata.get("processing_job_id")
            if processing_job_id:
                await JobQueue.abort_job(processing_job_id)
        except Exception:
            logger.warning(
                "Could not abort processing job for task %s", task_id, exc_info=True
            )

        return {"message": "Task cancellation requested"}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error cancelling task: {e}")
        raise HTTPException(status_code=500, detail=f"Error cancelling task: {str(e)}")


@router.get("/metrics/performance")
async def get_performance_metrics(
    request: Request, db: AsyncSession = Depends(get_db)
):
    """Get aggregate processing performance metrics by mode."""
    try:
        await require_admin_user(request, db, get_config())
        task_service = TaskService(db)
        return await task_service.get_performance_metrics()
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error loading performance metrics: {e}")
        raise HTTPException(status_code=500, detail=f"Error loading metrics: {str(e)}")


@router.get("/{task_id}/candidates")
async def get_task_candidates(
    task_id: str, request: Request, db: AsyncSession = Depends(get_db)
):
    """Candidate moments waiting for the user's pick (Find-Moments modes)."""
    task_service = TaskService(db)
    task = await _require_task_reader(request, task_service, db, task_id)
    metadata = await _load_task_source_metadata(task_id, db=db)
    checkpoint, _key = await task_service.load_task_checkpoint(
        task, metadata
    )
    candidates = checkpoint.get("candidates") or []
    result: Dict[str, Any] = {
        "task_id": task_id,
        "status": task.get("status"),
        "creation_mode": task.get("creation_mode"),
        "candidates": candidates,
    }
    if task.get("status") == AWAITING_SELECTION_STATUS:
        # The wait is bounded: say when it started and when the
        # auto-pick sweep will continue with the top picks, so the
        # picker never reads as a wait with no end.
        result.update(task_service.selection_wait_info(task, metadata))
    return result


@router.post("/{task_id}/select-moments")
async def select_moments(
    task_id: str, request: Request, db: AsyncSession = Depends(get_db)
):
    """Pick moments for an awaiting-selection task and queue rendering.

    The pick itself — the picked slate written into the processing
    checkpoint as the final plan, the guarded status flip, billing
    and the enqueue — lives in TaskService.apply_moment_selection,
    the same path the auto-pick sweep uses when a parked task's wait
    times out, so a user's pick and a timed-out pick can never drift
    apart. This endpoint adds the owner check and maps the outcomes
    to HTTP status codes.
    """
    try:
        data = await _read_json_object(request)
        task_service = TaskService(db)
        await _require_task_owner(request, task_service, db, task_id)

        try:
            result = await task_service.apply_moment_selection(
                task_id, data.get("segment_indexes")
            )
        except MomentSelectionNotWaitingError:
            raise HTTPException(
                status_code=400,
                detail="Task is not waiting for a moment selection",
            )
        except MomentSelectionSlateLostError:
            raise HTTPException(
                status_code=409,
                detail="The candidate moments for this task are no "
                "longer available",
            )
        except MomentSelectionSourceMissingError:
            raise HTTPException(
                status_code=400, detail="Task source URL is missing"
            )
        except ValueError as exc:
            raise HTTPException(
                status_code=422, detail=str(exc)
            ) from None

        return {
            "message": "Selected moments queued for rendering",
            "job_id": result["job_id"],
            "selected_count": result["selected_count"],
        }
    except GenerationAllowanceBusy as e:
        raise HTTPException(status_code=409, detail=str(e), headers={"Retry-After": "1"})
    except BillingLimitExceeded as e:
        raise HTTPException(
            status_code=402,
            detail={
                "code": "SUBSCRIPTION_REQUIRED",
                "message": e.summary.get("reason") or "Plan usage limit reached",
                "billing": jsonable_encoder(e.summary),
            },
        )
    except TaskRunBusy as e:
        raise HTTPException(status_code=409, detail=str(e))
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error selecting moments: {e}")
        raise HTTPException(status_code=500, detail=f"Error selecting moments: {str(e)}")


@router.post("/{task_id}/resume")
async def resume_task(
    task_id: str, request: Request, db: AsyncSession = Depends(get_db)
):
    """Retry a cancelled or errored task while preserving completed clips."""
    try:
        task_service = TaskService(db)
        task = await _require_task_owner(request, task_service, db, task_id)

        async with task_run_guard(db, task_id):
            task = await task_service.task_repo.get_task_by_id(db, task_id)
            if task.get("status") == "queued":
                return {"message": "Task already queued"}
            if task.get("status") not in ["cancelled", "error"]:
                raise HTTPException(
                    status_code=400,
                    detail="Only cancelled/error/queued tasks can be retried",
                )

            source_url = task.get("source_url")
            source_type = task.get("source_type")
            output_format = "vertical"
            add_subtitles = True

            metadata = await _load_task_source_metadata(task_id, db=db)
            if not source_url:
                source_url = metadata.get("url")
            if not source_type:
                source_type = metadata.get("source_type")
            of = metadata.get("output_format", output_format)
            if of in VALID_OUTPUT_FORMATS:
                output_format = of
            asub = metadata.get("add_subtitles", add_subtitles)
            if isinstance(asub, bool):
                add_subtitles = asub
            cleanup_settings = normalize_clip_cleanup_settings(
                metadata.get("cut_long_pauses"),
                metadata.get("pause_threshold_ms"),
                metadata.get("remove_filler_words"),
                metadata.get("filtered_words"),
            )

            if not source_url or not source_type:
                raise HTTPException(status_code=400, detail="Task source URL is missing")

            async with generation_allowance_guard(
                db, task["user_id"], enabled=get_config().monetization_enabled
            ):
                await BillingService(db).assert_can_resume_task(task["user_id"], task_id)

                try:
                    runtime_config = get_config()
                    redis_client = redis.Redis(
                        host=runtime_config.redis_host,
                        port=runtime_config.redis_port,
                        password=runtime_config.redis_password,
                        decode_responses=True,
                    )
                    try:
                        await redis_client.delete(f"task_cancel:{task_id}")
                    finally:
                        await redis_client.aclose()

                    await task_service.task_repo.update_task_status(
                        db,
                        task_id,
                        "queued",
                        progress=0,
                        progress_message="Re-queued by user",
                    )

                    processing_mode = (
                        task.get("processing_mode") or runtime_config.default_processing_mode
                    )

                    processing_job_id = f"{task_id}:{secrets.token_hex(16)}"
                    metadata["processing_job_id"] = processing_job_id
                    await _save_task_source_metadata(task_id, metadata, db=db)
                    job_id = await JobQueue.enqueue_processing_job(
                        "process_video_task",
                        processing_mode,
                        task_id,
                        source_url,
                        source_type,
                        task["user_id"],
                        task.get("font_family"),
                        task.get("font_size"),
                        task.get("font_color"),
                        task.get("caption_template") or "default",
                        processing_mode,
                        output_format,
                        add_subtitles,
                        cleanup_settings,
                        normalize_clip_length(metadata.get("clip_length")),
                        _job_id=processing_job_id,
                    )
                except Exception:
                    await task_service.task_repo.update_task_status(
                        db, task_id, task["status"],
                        progress_message="Could not queue generation. Please try again.",
                        expected_statuses=["queued", task["status"]],
                    )
                    raise

                return {"message": "Generation retry queued", "job_id": job_id}
    except GenerationAllowanceBusy as e:
        raise HTTPException(status_code=409, detail=str(e), headers={"Retry-After": "1"})
    except BillingLimitExceeded as e:
        raise HTTPException(
            status_code=402,
            detail={
                "code": "SUBSCRIPTION_REQUIRED",
                "message": e.summary.get("reason") or "Plan usage limit reached",
                "billing": jsonable_encoder(e.summary),
            },
        )
    except TaskRunBusy as e:
        raise HTTPException(status_code=409, detail=str(e))
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error retrying generation: {e}")
        raise HTTPException(status_code=500, detail=f"Error retrying generation: {str(e)}")


@router.get("/dead-letter/list")
async def list_dead_letter_tasks(request: Request, db: AsyncSession = Depends(get_db)):
    """List tasks that exhausted retries and landed in dead-letter store."""
    runtime_config = get_config()
    await require_admin_user(request, db, runtime_config)
    redis_client = redis.Redis(
        host=runtime_config.redis_host,
        port=runtime_config.redis_port,
        password=runtime_config.redis_password,
        decode_responses=True,
    )
    try:
        ids_result = redis_client.smembers("tasks:dead_letter")
        ids = await ids_result if inspect.isawaitable(ids_result) else ids_result
        items = []
        safe_ids = list(ids or [])
        for task_id in sorted(safe_ids):
            payload = await redis_client.get(f"dead_letter:{task_id}")
            if payload:
                try:
                    items.append(json.loads(payload))
                except json.JSONDecodeError:
                    items.append({"task_id": task_id, "raw": payload})

        return {"total": len(items), "tasks": items}
    finally:
        await redis_client.aclose()
