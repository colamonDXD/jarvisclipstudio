"""
Worker tasks - background jobs processed by arq workers.
"""

import logging
from typing import Dict, Any, Optional
import json
import os
from arq import Retry, cron
from arq.worker import func
from .media_cleanup import cleanup_media, sweep_orphaned_media
from .media_replication import check_media_storage, replicate_pending_media
from ..repositories.edit_transaction import TaskCancelled
from ..repositories.task_run_guard import TaskRunBusy

from ..observability import configure_logging, set_trace_id, set_user_id
from ..utils.user_errors import user_facing_error_message

configure_logging()

logger = logging.getLogger(__name__)


async def process_video_task(
    ctx: Dict[str, Any],
    task_id: str,
    url: str,
    source_type: str,
    user_id: str,
    font_family: Optional[str] = None,
    font_size: Optional[int] = None,
    font_color: Optional[str] = None,
    caption_template: str = "default",
    processing_mode: str = "fast",
    output_format: str = "vertical",
    add_subtitles: bool = True,
    cleanup_settings: Dict[str, Any] | None = None,
    clip_length: str = "auto",
) -> Dict[str, Any]:
    """
    Background worker task to process a video.

    Args:
        ctx: arq context (provides Redis connection and other utilities)
        task_id: Task ID to update
        url: Video URL or file path
        source_type: "youtube" or "upload"
        user_id: User ID who created the task
        font_family: Font family for subtitles
        font_size: Font size for subtitles
        font_color: Font color for subtitles

    Returns:
        Dict with processing results
    """
    from ..database import AsyncSessionLocal
    from ..runtime_settings import load_runtime_settings_cache
    from ..services.task_service import TaskService
    from ..workers.progress import ProgressTracker

    set_trace_id(f"task-{task_id}")
    set_user_id(user_id)
    logger.info(f"Worker processing task {task_id}")

    # Register task_id on this thread so run_ffmpeg_command can tag child procs.
    # This lets kill_task_procs() terminate any active ffmpeg immediately on cancel.
    from ..media.ffmpeg import register_task_id, kill_task_procs
    register_task_id(task_id)

    # Create progress tracker
    progress = ProgressTracker(ctx["redis"], task_id)

    async with AsyncSessionLocal() as db:
        await load_runtime_settings_cache(db)
        task_service = TaskService(db)

        try:
            # Progress callback
            async def update_progress(
                percent: int, message: str, status: str = "processing"
            ):
                await progress.update(percent, message, status)
                logger.info(f"Task {task_id}: {percent}% - {message}")

            async def should_cancel() -> bool:
                cancelled = await ctx["redis"].get(f"task_cancel:{task_id}")
                if cancelled:
                    # Kill any in-flight ffmpeg/ffprobe immediately so the
                    # worker is freed within seconds instead of minutes.
                    kill_task_procs(task_id)
                return bool(cancelled)

            async def clip_ready_callback(
                clip_index: int, total_clips: int, clip_data: dict
            ):
                await progress.clip_ready(clip_index, total_clips, clip_data)

            # Process the video
            result = await task_service.process_task(
                task_id=task_id,
                url=url,
                source_type=source_type,
                user_id=user_id,
                font_family=font_family,
                font_size=font_size,
                font_color=font_color,
                caption_template=caption_template,
                processing_mode=processing_mode,
                output_format=output_format,
                add_subtitles=add_subtitles,
                progress_callback=update_progress,
                should_cancel=should_cancel,
                clip_ready_callback=clip_ready_callback,
                cleanup_settings=cleanup_settings,
                clip_length=clip_length,
            )

            logger.info(f"Task {task_id} completed successfully")
            return result

        except TaskCancelled:
            # User cancellation is a successful outcome, not a failure: the
            # task row is already "cancelled" (written by the pipeline or the
            # cancel endpoint). Never retry it and never dead-letter it as a
            # permanent failure.
            logger.info(f"Task {task_id} cancelled by user")
            return {"task_id": task_id, "status": "cancelled"}
        except TaskRunBusy:
            # A resume request may still own the guard while publishing this job.
            # Retry without rewriting task state or creating a dead-letter entry.
            raise Retry(defer=2)
        except Exception as e:
            logger.error(f"Task {task_id} failed: {e}", exc_info=True)
            try:
                job_try = int(ctx.get("job_try", 1))
                max_tries = int(getattr(WorkerSettings, "max_tries", 3))
                if job_try >= max_tries:
                    payload = {
                        "task_id": task_id,
                        # Sanitised: the raw error is in the worker log; the
                        # dead-letter store must not keep signed URLs/keys.
                        "error": user_facing_error_message(e),
                        "tries": job_try,
                    }
                    await ctx["redis"].set(
                        f"dead_letter:{task_id}", json.dumps(payload)
                    )
                    await ctx["redis"].sadd("tasks:dead_letter", task_id)
                    await progress.error("Task failed permanently after retries")
            except Exception:
                logger.exception("Failed to persist dead-letter payload")
            # Error will be caught by arq and task status will be updated
            raise
        finally:
            # On any exit (success, cancel, failure) kill any lingering
            # ffmpeg/ffprobe that the pipeline left registered for this task.
            try:
                kill_task_procs(task_id)
            except Exception:
                pass
            # Task end (success, cancel, or failure): drop the resident
            # faster-whisper model so the next task's separation and
            # classification stages start with the card free instead of
            # inheriting this task's residency (Wave-4 VRAM scoping).
            # Best-effort — it must never mask the job's own outcome.
            try:
                from ..media.transcription import release_faster_whisper_models

                release_faster_whisper_models()
            except Exception:
                logger.debug("Whisper eviction at task end failed", exc_info=True)


RERENDER_CLIP_JOB_TIMEOUT_SECONDS = 3600
EDIT_CLIP_JOB_TIMEOUT_SECONDS = 3600
HOOK_PREVIEW_JOB_TIMEOUT_SECONDS = 3600


async def rerender_clip_task(
    ctx: Dict[str, Any],
    task_id: str,
    clip_id: str,
    niche_override: str,
    caption_template: Optional[str] = None,
    transition: Optional[str] = None,
) -> Dict[str, Any]:
    """Re-render one clip with a different niche profile (a re-style).

    Failures are recorded in the job outcome instead of retried blindly: the
    clip keeps its previous render, and the API reports the recorded state.
    """
    from ..database import AsyncSessionLocal
    from ..runtime_settings import load_runtime_settings_cache
    from ..services.task_service import TaskService
    from ..workers.job_queue import set_rerender_outcome

    job_id = ctx.get("job_id")
    redis = ctx["redis"]
    set_trace_id(f"rerender-{clip_id}")
    logger.info(
        "Worker re-rendering clip %s of task %s as %s",
        clip_id,
        task_id,
        niche_override,
    )

    async def record(outcome: Dict[str, Any]) -> None:
        if job_id:
            try:
                await set_rerender_outcome(redis, job_id, outcome)
            except Exception:
                logger.exception("Could not record re-render outcome for %s", job_id)

    await record(
        {"status": "processing", "clip_id": clip_id, "niche_override": niche_override}
    )
    try:
        async with AsyncSessionLocal() as db:
            await load_runtime_settings_cache(db)
            service = TaskService(db)
            clip = await service.rerender_clip(
                task_id,
                clip_id,
                niche_override,
                caption_template=caption_template,
                transition=transition,
            )
        outcome = {
            "status": "completed",
            "clip_id": clip_id,
            "niche_override": niche_override,
            "clip": clip,
        }
    except Exception as exc:
        logger.error(
            "Re-render of clip %s failed: %s", clip_id, exc, exc_info=True
        )
        outcome = {
            "status": "failed",
            "clip_id": clip_id,
            "niche_override": niche_override,
            "error": str(exc),
        }
    await record(outcome)
    return outcome


async def edit_clip_task(
    ctx: Dict[str, Any],
    task_id: str,
    clip_id: str,
    operations: Optional[list] = None,
    captions: Optional[Dict[str, Any]] = None,
    niche_override: Optional[str] = None,
    music_override: Optional[str] = None,
    look: Optional[Dict[str, Any]] = None,
    audio: Optional[Dict[str, Any]] = None,
    voiceover: Optional[Dict[str, Any]] = None,
    sfx: Optional[list] = None,
) -> Dict[str, Any]:
    """Apply editor operations / caption overrides to one clip and re-render.

    Same failure contract as ``rerender_clip_task``: failures are recorded
    in the job outcome (shared ``clip_rerender:`` key space — it IS a
    re-render), never retried blindly, and the clip keeps its previous
    render on failure.
    """
    from ..database import AsyncSessionLocal
    from ..runtime_settings import load_runtime_settings_cache
    from ..services.task_service import TaskService
    from ..workers.job_queue import set_rerender_outcome

    job_id = ctx.get("job_id")
    redis = ctx["redis"]
    set_trace_id(f"edit-{clip_id}")
    logger.info("Worker editing clip %s of task %s", clip_id, task_id)

    async def record(outcome: Dict[str, Any]) -> None:
        if job_id:
            try:
                await set_rerender_outcome(redis, job_id, outcome)
            except Exception:
                logger.exception("Could not record edit outcome for %s", job_id)

    await record({"status": "processing", "clip_id": clip_id, "kind": "edit"})
    try:
        async with AsyncSessionLocal() as db:
            await load_runtime_settings_cache(db)
            service = TaskService(db)
            clip = await service.edit_clip(
                task_id,
                clip_id,
                operations=operations,
                captions=captions,
                niche_override=niche_override,
                music_override=music_override,
                look=look,
                audio=audio,
                voiceover=voiceover,
                sfx=sfx,
            )
            # Same QC verdict trail as pipeline clips, but flag-only: the
            # pipeline's auto re-render would discard the user's edit.
            # _qc_edited_clip never raises; this block must not fail a
            # completed edit either, so it is fully guarded.
            try:
                task_row = await service.task_repo.get_task_by_id(db, task_id)
                effective_niche = (
                    niche_override
                    or clip.get("niche_profile")
                    or (task_row or {}).get("niche_override")
                )
                await service._qc_edited_clip(
                    task_id=task_id,
                    clip_id=clip_id,
                    niche=effective_niche,
                    music_override=music_override,
                    output_format="vertical",
                    output_short_side=None,
                )
                refreshed = await service.clip_repo.get_clip_by_id(db, clip_id)
                if refreshed:
                    clip = {**clip, **refreshed}
            except Exception:
                logger.exception(
                    "Edited-clip QC step failed for clip %s; the edit "
                    "itself already succeeded",
                    clip_id,
                )
        outcome = {
            "status": "completed",
            "clip_id": clip_id,
            "kind": "edit",
            "clip": clip,
        }
        if isinstance(clip, dict) and clip.get("edit_plan") is not None:
            outcome["edit_plan"] = clip["edit_plan"]
    except Exception as exc:
        logger.error(
            "Edit of clip %s failed: %s", clip_id, exc, exc_info=True
        )
        outcome = {
            "status": "failed",
            "clip_id": clip_id,
            "kind": "edit",
            "error": str(exc),
        }
    await record(outcome)
    return outcome


async def generate_hook_previews_task(
    ctx: Dict[str, Any],
    task_id: str,
    clip_id: str,
) -> Dict[str, Any]:
    """Render the ~6s hook-variant previews for one clip (Lane K).

    Same outcome contract as ``rerender_clip_task``: the outcome —
    including failures — is recorded under the job id in the shared
    ``clip_rerender:`` key space, never retried blindly, and the clip
    itself is never modified by preview generation.
    """
    from ..database import AsyncSessionLocal
    from ..runtime_settings import load_runtime_settings_cache
    from ..services.task_service import TaskService
    from ..workers.job_queue import set_rerender_outcome

    job_id = ctx.get("job_id")
    redis = ctx["redis"]
    set_trace_id(f"hook-previews-{clip_id}")
    logger.info("Worker generating hook previews for clip %s of task %s", clip_id, task_id)

    async def record(outcome: Dict[str, Any]) -> None:
        if job_id:
            try:
                await set_rerender_outcome(redis, job_id, outcome)
            except Exception:
                logger.exception("Could not record hook preview outcome for %s", job_id)

    await record(
        {"status": "processing", "clip_id": clip_id, "kind": "hook_previews"}
    )
    try:
        async with AsyncSessionLocal() as db:
            await load_runtime_settings_cache(db)
            service = TaskService(db)
            result = await service.generate_hook_previews(task_id, clip_id)
        outcome = {
            "status": "completed",
            "clip_id": clip_id,
            "kind": "hook_previews",
            "batch_id": result["batch_id"],
            "previews": result["previews"],
        }
    except Exception as exc:
        logger.error(
            "Hook preview generation for clip %s failed: %s",
            clip_id,
            exc,
            exc_info=True,
        )
        outcome = {
            "status": "failed",
            "clip_id": clip_id,
            "kind": "hook_previews",
            "error": str(exc),
        }
    await record(outcome)
    return outcome


async def apply_hook_variant_task(
    ctx: Dict[str, Any],
    task_id: str,
    clip_id: str,
    preview_id: str,
) -> Dict[str, Any]:
    """Apply a picked hook variant to the full clip (Lane K).

    This IS a re-render: the job id lives in the ``clip-rerender:``
    space, the outcome carries the refreshed clip, and on failure the
    clip keeps its previous render and its previously recorded pick.
    """
    from ..database import AsyncSessionLocal
    from ..runtime_settings import load_runtime_settings_cache
    from ..services.task_service import TaskService
    from ..workers.job_queue import set_rerender_outcome

    job_id = ctx.get("job_id")
    redis = ctx["redis"]
    set_trace_id(f"hook-variant-{clip_id}")
    logger.info(
        "Worker applying hook preview %s to clip %s of task %s",
        preview_id,
        clip_id,
        task_id,
    )

    async def record(outcome: Dict[str, Any]) -> None:
        if job_id:
            try:
                await set_rerender_outcome(redis, job_id, outcome)
            except Exception:
                logger.exception("Could not record hook variant outcome for %s", job_id)

    await record(
        {"status": "processing", "clip_id": clip_id, "kind": "hook_variant"}
    )
    try:
        async with AsyncSessionLocal() as db:
            await load_runtime_settings_cache(db)
            service = TaskService(db)
            clip = await service.apply_hook_variant(task_id, clip_id, preview_id)
        outcome = {
            "status": "completed",
            "clip_id": clip_id,
            "kind": "hook_variant",
            "hook_variant": clip.get("hook_variant"),
            "clip": clip,
        }
    except Exception as exc:
        logger.error(
            "Applying hook preview %s to clip %s failed: %s",
            preview_id,
            clip_id,
            exc,
            exc_info=True,
        )
        outcome = {
            "status": "failed",
            "clip_id": clip_id,
            "kind": "hook_variant",
            "error": str(exc),
        }
    await record(outcome)
    return outcome


async def _social_service(db):
    from ..runtime_settings import load_runtime_settings_cache
    from ..services.social_service import SocialService

    await load_runtime_settings_cache(db)
    return SocialService(db)


async def publish_social_post(ctx: Dict[str, Any], post_id: str) -> Dict[str, Any]:
    """Upload one queued social post to its platform."""
    from ..database import AsyncSessionLocal

    set_trace_id(f"social-{post_id}")
    logger.info("Worker publishing social post %s", post_id)
    async with AsyncSessionLocal() as db:
        service = await _social_service(db)
        try:
            post = await service.publish_post(post_id)
        except Exception as exc:
            # Failures are recorded on the post row; never let arq retry blindly.
            # Cancellation (job timeout, shutdown) propagates; the stale
            # publishing sweep fails that post. Logged without a traceback
            # because provider errors can carry request URLs.
            from ..social.base import redact

            logger.error(
                "Publishing social post %s crashed (%s): %s",
                post_id,
                type(exc).__name__,
                redact(exc),
            )
            return {"post_id": post_id, "status": "error"}
    return {"post_id": post_id, "status": post.get("status")}


async def dispatch_scheduled_social_posts(ctx: Dict[str, Any]) -> Dict[str, Any]:
    """Cron: move due scheduled posts onto the publish queue."""
    from ..database import AsyncSessionLocal

    async with AsyncSessionLocal() as db:
        service = await _social_service(db)
        dispatched = await service.dispatch_scheduled_posts()
        resolved = await service.resolve_pending_posts()
        # Covers workers that crashed or were cancelled mid-upload.
        swept = await service.sweep_stale_publishing()
    return {
        "dispatched": len(dispatched),
        "pending_checked": resolved,
        "stale_failed": len(swept),
    }


async def refresh_social_metrics(ctx: Dict[str, Any]) -> Dict[str, Any]:
    """Cron: pull fresh audience metrics for recently published posts."""
    from ..database import AsyncSessionLocal
    from ..repositories.social_repository import SocialRepository

    async with AsyncSessionLocal() as db:
        service = await _social_service(db)
        refreshed = await service.refresh_due_metrics()
        purged = await SocialRepository.purge_expired_oauth_states(db)
    return {"refreshed": refreshed, "purged_oauth_states": purged}


async def _provider_manager_startup(ctx: Dict[str, Any]) -> None:
    """Wire provider-manager persistence for this worker process."""
    try:
        from .. import provider_manager

        provider_manager.configure_persistence()
        await provider_manager.get_manager().warm_up()
    except Exception as error:  # never block worker startup on accounting
        logger.warning("Provider manager startup wiring failed: %s", error)


async def _recover_stale_tasks_on_startup(ctx: Dict[str, Any]) -> None:
    """Fail tasks orphaned by the previous worker's death.

    A fresh worker owns no jobs, so any task still "processing" whose arq
    job is gone belonged to the worker that died. Never fatal: a recovery
    failure must not keep the worker from starting — the lazy sweep on
    task reads (TaskService.get_task_with_clips) still applies.
    """
    try:
        from ..database import AsyncSessionLocal
        from ..services.task_service import TaskService

        async with AsyncSessionLocal() as db:
            counts = await TaskService(db).recover_stale_tasks(worker_startup=True)
        if counts["failed"]:
            logger.warning(
                "Startup recovery failed %s orphaned task(s)", counts["failed"]
            )
    except Exception:
        logger.exception("Stale task recovery on startup failed")


async def auto_pick_stale_selections(ctx: Dict[str, Any]) -> None:
    """Continue Find-Moments tasks whose pick never came.

    A task parked in awaiting_selection waits for the user's moment
    pick; past the selection timeout (TASK_SELECTION_AUTOPICK_MINUTES,
    default 15) the sweep continues it with the top-ranked moments
    through the same pick path the endpoint uses. Never fatal: a
    sweep failure must not take the worker down, and a task whose
    auto-pick fails simply stays parked for the next run.
    """
    try:
        from ..database import AsyncSessionLocal
        from ..services.task_service import TaskService

        async with AsyncSessionLocal() as db:
            counts = await TaskService(db).auto_pick_stale_selections()
        if counts["auto_picked"] or counts["failed"]:
            logger.info(
                "Selection auto-pick sweep: checked=%s auto_picked=%s "
                "failed=%s",
                counts["checked"],
                counts["auto_picked"],
                counts["failed"],
            )
    except Exception:
        logger.exception("Selection auto-pick sweep failed")


async def _worker_startup(ctx: Dict[str, Any]) -> None:
    await _provider_manager_startup(ctx)
    await _recover_stale_tasks_on_startup(ctx)


from ..services.editor_service import prepare_editor, export_editor, combine_editor
from ..services.clip_export_service import (
    CLIP_EXPORT_JOB_TIMEOUT_SECONDS,
    export_clip_preset,
)
from ..services.social_service import PUBLISH_JOB_TIMEOUT_SECONDS

# Worker configuration for arq
class WorkerSettings:
    """Configuration for arq worker."""

    from ..config import Config
    from arq.connections import RedisSettings

    config = Config()

    # Functions to run
    functions = [
        process_video_task,
        # One try: the outcome (including failures) is recorded by the job
        # itself, and a retry would start a second encode for the same clip.
        func(
            rerender_clip_task,
            timeout=RERENDER_CLIP_JOB_TIMEOUT_SECONDS,
            max_tries=1,
        ),
        func(
            edit_clip_task,
            timeout=EDIT_CLIP_JOB_TIMEOUT_SECONDS,
            max_tries=1,
        ),
        func(
            generate_hook_previews_task,
            timeout=HOOK_PREVIEW_JOB_TIMEOUT_SECONDS,
            max_tries=1,
        ),
        func(
            apply_hook_variant_task,
            timeout=RERENDER_CLIP_JOB_TIMEOUT_SECONDS,
            max_tries=1,
        ),
        prepare_editor,
        export_editor,
        combine_editor,
        # One try: a lost running export is failed by the heartbeat sweep, and a
        # retry would start a second encode for the same row.
        func(
            export_clip_preset,
            timeout=CLIP_EXPORT_JOB_TIMEOUT_SECONDS,
            max_tries=1,
        ),
        # Results are not kept so a finished job's id ("social:{post}:{attempt}")
        # frees up; a dead job's post is failed by the stale-publishing sweep.
        func(publish_social_post, timeout=PUBLISH_JOB_TIMEOUT_SECONDS, keep_result=0),
    ]
    queue_name = "supoclip_tasks"
    on_startup = _worker_startup
    health_check_interval = 30
    # Honour Job.abort() for in-flight jobs (task cancellation), instead of
    # only noticing the cancel flag at the next pipeline checkpoint.
    allow_abort_jobs = True

    # Redis settings from environment
    redis_settings = RedisSettings(
        host=config.redis_host, port=config.redis_port, password=config.redis_password, database=0
    )

    # Retry settings
    max_tries = 3  # Retry failed jobs up to 3 times
    job_timeout = 10800  # 3 hour timeout for video processing

    # Worker pool settings. Each processing job can fan out into several
    # concurrent ffmpeg renders, so the default is one job at a time to keep
    # a long queue from multiplying memory use on small GPU hosts. Raise
    # WORKER_MAX_JOBS when the host has RAM to spare.
    try:
        max_jobs = max(1, int(os.environ.get("WORKER_MAX_JOBS", "1")))
    except ValueError:
        max_jobs = 1  # Process one job at a time by default
    cron_jobs = [
        cron(check_media_storage, second=20, run_at_startup=True, timeout=120),
        cron(replicate_pending_media, second=40, run_at_startup=True, timeout=1800),
        cron(cleanup_media, minute={0, 5, 10, 15, 20, 25, 30, 35, 40, 45, 50, 55}, run_at_startup=True),
        cron(sweep_orphaned_media, hour={0, 6, 12, 18}, minute=15),
        # Every minute: promote due scheduled posts and poll async publishes.
        cron(dispatch_scheduled_social_posts, second=0, timeout=300),
        # Every minute: continue Find-Moments tasks whose moment pick
        # timed out (they would otherwise wait forever).
        cron(auto_pick_stale_selections, second=30, timeout=300),
        # Hourly: pull audience metrics back for the performance loop.
        cron(refresh_social_metrics, minute=7, second=0, timeout=1800),
    ]
