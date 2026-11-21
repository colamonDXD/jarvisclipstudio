"""Watch-folder autopilot: turn files dropped in a folder into tasks.

The watcher is a small polling loop inside the backend process. For
each user who enabled it (``watch_folder_settings``), it scans the
configured folder, waits until a file's size and mtime are unchanged
across two scans (a file still being copied is never queued), then
hands the file to the normal task pipeline:

1. the file is ingested as a completed local upload (hardlink when
   possible, copy otherwise) and registered in
   ``media_upload_sessions`` — the user's original is never moved,
   modified or deleted;
2. a task is created through ``TaskService.create_task_with_source``
   with the user's autopilot defaults and enqueued on the same
   ``JobQueue`` the create-task route uses. There is no parallel
   pipeline.

Durability lives in ``watch_folder_files``: a file is claimed with an
atomic insert before any work happens, so restarts and duplicate
watcher processes cannot queue it twice. A claim orphaned in
``processing`` with no task (the watcher died between the claim insert
and task creation) is recovered on a later scan once it is stale: the
file is re-driven through the normal bounded retry path, or parked if
its source changed meanwhile. A failure is recorded with
its reason and retried at most ``MAX_ATTEMPTS`` times with a backoff,
then parked. Parked and queued files are never touched again
automatically.
"""

from __future__ import annotations

import asyncio
import logging
import math
import os
import secrets
import shutil
from pathlib import Path
from typing import Awaitable, Callable
from uuid import uuid4

from sqlalchemy.ext.asyncio import AsyncSession

from ..ai import validate_target_clip_count
from ..auto_director.brand_kit import validate_caption_template
from ..auto_director.broll_setting import validate_broll_enabled
from ..auto_director.creation_modes import plan_for_mode, validate_creation_mode
from ..auto_director.niche import validate_niche_override
from ..auto_director.template_mode import validate_template_mode
from ..clip_cleanup import new_task_cleanup_settings
from ..clip_duration import validate_clip_length
from ..config import Config, get_config
from ..media.music_library import validate_music_mode, validate_music_override
from ..media.separation import validate_separation_opt_in
from ..recipes import (
    create_fragment,
    merge_recipe_fragment,
    validate_recipe_settings,
)
from ..repositories.recipe_repository import RecipeRepository
from ..repositories.watch_folder_repository import WatchFolderRepository
from ..utils.async_helpers import run_in_thread
from ..video_utils import VALID_OUTPUT_FORMATS

logger = logging.getLogger(__name__)

VIDEO_EXTENSIONS = {
    ".mp4", ".mov", ".mkv", ".webm", ".avi", ".m4v",
    ".mpg", ".mpeg", ".ts", ".mts", ".m2ts", ".flv", ".wmv",
}

MAX_ATTEMPTS = 3
RETRY_BACKOFF_SECONDS = 300
# A claim still in 'processing' with no task after this long is orphaned:
# the watcher died between the claim insert and task creation (F-D1).
# Task creation is a local call, so 30 minutes is a wide margin over the
# longest legitimate claim->task window. Env-tunable for deployments
# with unusually slow storage.
STALE_CLAIM_SECONDS = max(
    60, int(os.getenv("WATCH_FOLDER_STALE_CLAIM_SECONDS", "1800"))
)
_ERROR_LIMIT = 500

# (db, settings, source_path, size_bytes) -> task_id
TaskCreator = Callable[[AsyncSession, dict, Path, int], Awaitable[str]]


def music_task_fields(settings: dict) -> dict:
    """Map autopilot music settings onto create-task fields.

    The watch-folder settings carry the same two music controls a
    manual task has: ``music_mode`` (auto/on/off, validated with the
    task field's own ``validate_music_mode`` semantics — ``None``
    means the settings row predates the control and behaves as Auto)
    and ``music_override`` (an optional library track). They compose
    without contradiction because the mode is the user's instruction
    and the track is only a preference inside it: ``off`` silences the
    task even with a track picked, ``on`` plays the pick or the
    profile bed, ``auto`` keeps today's behaviour. Both values pass
    through unchanged — the pipeline already resolves the pair in
    exactly this way for manual tasks.
    """
    from ..media.music_library import validate_music_mode

    return {
        "music_mode": validate_music_mode(settings.get("music_mode")),
        "music_override": settings.get("music_override"),
    }


_PROCESSING_MODES = ("fast", "balanced", "quality")


async def load_autopilot_recipe(
    db: AsyncSession, settings: dict
) -> tuple[str | None, dict]:
    """Load the recipe the autopilot settings name, or ``(None, {})``.

    The recipe is read user-scoped, exactly as the create path reads
    it, and its stored settings are re-checked by the recipe
    validator: a row the save-time validator would never have
    accepted (stale, hand-edited, or deleted since the settings were
    saved) raises ``ValueError`` here, before any file is ingested,
    so the file handler never creates a task from it. The caller logs
    the refusal loudly and the claim follows the normal failed path.
    """
    recipe_id = settings.get("recipe_id")
    if recipe_id is None:
        return None, {}
    if not isinstance(recipe_id, str) or not recipe_id.strip():
        raise ValueError("Invalid recipe_id. Use a recipe id or omit it.")
    recipe_id = recipe_id.strip()
    recipe = await RecipeRepository.get_recipe(db, settings["user_id"], recipe_id)
    if recipe is None:
        raise ValueError("Recipe not found")
    return recipe_id, validate_recipe_settings(create_fragment(recipe))


def _validate_merged_autopilot_payload(merged: dict) -> dict:
    """Run the merged payload through the create path's own validators.

    Mirrors the checks ``_create_task`` runs after its merge: each key
    the create path rejects is rejected here the same way, and the
    normalised value is kept. Keys the create path treats leniently
    keep that treatment downstream instead: an unknown
    ``processing_mode`` falls back to the default when the arguments
    are resolved, and font sub-keys come only from the recipe, whose
    stored settings were already re-checked strictly on load. The
    manual-cutter contract is enforced too: that workflow needs
    ranges the autopilot never has. Raises ``ValueError`` on the first
    bad value.
    """
    checked = dict(merged)
    if checked.get("template_mode") is not None:
        checked["template_mode"] = validate_template_mode(
            checked["template_mode"]
        )
    if checked.get("niche_override") is not None:
        checked["niche_override"] = validate_niche_override(
            checked["niche_override"]
        )
    if checked.get("music_override") is not None:
        checked["music_override"] = validate_music_override(
            checked["music_override"]
        )
    if checked.get("broll_enabled") is not None:
        checked["broll_enabled"] = validate_broll_enabled(
            checked["broll_enabled"]
        )
    if checked.get("separation_opt_in") is not None:
        checked["separation_opt_in"] = validate_separation_opt_in(
            checked["separation_opt_in"]
        )
    if checked.get("creation_mode") is not None:
        checked["creation_mode"] = validate_creation_mode(
            checked["creation_mode"]
        )
    if checked.get("clip_length") is not None:
        checked["clip_length"] = validate_clip_length(checked["clip_length"])
    if checked.get("target_clip_count") is not None:
        checked["target_clip_count"] = validate_target_clip_count(
            checked["target_clip_count"]
        )
    if checked.get("caption_template") is not None:
        checked["caption_template"] = validate_caption_template(
            checked["caption_template"]
        )
    if checked.get("output_format") is not None:
        if checked["output_format"] not in VALID_OUTPUT_FORMATS:
            raise ValueError(
                "Invalid output_format. Valid values: "
                + ", ".join(sorted(VALID_OUTPUT_FORMATS))
                + ", or omit it for vertical."
            )
    if checked.get("add_subtitles") is not None and not isinstance(
        checked["add_subtitles"], bool
    ):
        raise ValueError("Invalid add_subtitles. Use true or false, or omit it.")
    if checked.get("include_broll") is not None and not isinstance(
        checked["include_broll"], bool
    ):
        raise ValueError("include_broll must be true or false")
    if checked.get("font_options") is not None and not isinstance(
        checked["font_options"], dict
    ):
        raise ValueError("font_options must be an object")
    if checked.get("creation_mode") is not None and plan_for_mode(
        checked["creation_mode"]
    ).requires_ranges:
        raise ValueError(
            "manual_ranges is required for the manual_cutter_ai creation mode"
        )
    return checked


def autopilot_task_arguments(
    settings: dict, recipe_id: str | None, fragment: dict, config: Config
) -> dict:
    """Merge the autopilot defaults with a recipe fragment into the
    exact arguments ``create_task_from_watch_file`` passes on.

    The autopilot's own settings are the explicit payload and win over
    the recipe, exactly as ``POST /tasks`` merges (see
    ``merge_recipe_fragment``): a setting left unset (``None``) is no
    choice and the recipe fills it. When a recipe is applied the merged
    payload is checked by the create path's own validators first, so a
    stored value the create path would reject refuses the file instead
    of creating a wrong-mode task. With no recipe the fragment is empty
    and the result is byte-identical to the arguments the autopilot
    passed before recipes: caption ``"default"``, clip length
    ``"auto"``, vertical output with subtitles, and the cleanup
    defaults.

    Returns ``create_kwargs`` (the keyword arguments for
    ``TaskService.create_task_with_source``, minus the per-file
    ``user_id``/``url``/``title``/``source_duration_seconds``),
    ``clip_length``, ``output_format``, ``add_subtitles`` and
    ``cleanup_settings`` (the render-affecting choices the worker job
    and the task metadata carry), plus the applied ``recipe_id``.
    """
    explicit = {
        "processing_mode": settings.get("processing_mode"),
        "niche_override": settings.get("niche_override"),
        "music_mode": settings.get("music_mode"),
        "music_override": settings.get("music_override"),
        "target_clip_count": settings.get("target_clip_count"),
    }
    merged = merge_recipe_fragment(explicit, fragment)
    # The music mode is normalised exactly as the autopilot always
    # normalised it (see music_task_fields): a stored " OFF " is
    # "off", an unset mode stays None and behaves as Auto.
    merged["music_mode"] = validate_music_mode(merged.get("music_mode"))
    if recipe_id is not None:
        merged = _validate_merged_autopilot_payload(merged)

    processing_mode = merged.get("processing_mode") or config.default_processing_mode
    if processing_mode not in _PROCESSING_MODES:
        processing_mode = config.default_processing_mode
    font_options = merged.get("font_options") or {}
    return {
        "create_kwargs": {
            "caption_template": merged.get("caption_template") or "default",
            "processing_mode": processing_mode,
            "niche_override": merged.get("niche_override"),
            "music_mode": merged.get("music_mode"),
            "music_override": merged.get("music_override"),
            "target_clip_count": merged.get("target_clip_count"),
            "font_family": font_options.get("font_family"),
            "font_size": font_options.get("font_size"),
            "font_color": font_options.get("font_color"),
            "include_broll": merged.get("include_broll") is True,
            "template_mode": merged.get("template_mode"),
            "broll_enabled": merged.get("broll_enabled"),
            "separation_opt_in": merged.get("separation_opt_in") or False,
            "creation_mode": merged.get("creation_mode"),
            "brand_kit_id": merged.get("brand_kit_id"),
            "recipe_id": recipe_id,
        },
        "clip_length": merged.get("clip_length") or "auto",
        "output_format": merged.get("output_format") or "vertical",
        "add_subtitles": merged.get("add_subtitles")
        if isinstance(merged.get("add_subtitles"), bool)
        else True,
        "cleanup_settings": new_task_cleanup_settings(merged),
        "recipe_id": recipe_id,
    }


def fingerprint_for(file_path: str, size_bytes: int, mtime_ns: int) -> str:
    """Identity of one version of one file: same path rewritten with
    different bytes is a different fingerprint and may be queued again,
    the identical file never is."""
    return f"{file_path}:{size_bytes}:{mtime_ns}"


def _is_candidate(path: Path) -> bool:
    return (
        path.is_file()
        and not path.name.startswith(".")
        and path.suffix.lower() in VIDEO_EXTENSIONS
    )


class WatchFolderService:
    """One user's scan cycle, with the task creator injected so tests
    can substitute a fake and no pipeline code is duplicated."""

    def __init__(
        self,
        repository: WatchFolderRepository,
        task_creator: TaskCreator,
        config: Config | None = None,
    ):
        self.repository = repository
        self.task_creator = task_creator
        self.config = config or get_config()
        # path -> (size_bytes, mtime_ns) as of the previous scan. A
        # file is only "arrived" once two consecutive scans agree.
        self._seen: dict[str, tuple[int, int]] = {}

    def folder_for(self, settings: dict) -> Path:
        return Path(settings.get("folder_path") or self.config.watch_folder_path)

    async def scan_once(self, db: AsyncSession, settings: dict) -> dict:
        summary = {
            "folder": None,
            "folder_missing": False,
            "seen": 0,
            "queued": 0,
            "failed": 0,
            "skipped_unstable": 0,
            "skipped_known": 0,
        }
        if not settings.get("enabled"):
            return summary
        folder = self.folder_for(settings)
        summary["folder"] = str(folder)
        if not folder.is_dir():
            summary["folder_missing"] = True
            return summary

        user_id = settings["user_id"]
        candidates = [p for p in sorted(folder.iterdir()) if _is_candidate(p)]
        current: dict[str, tuple[int, int]] = {}
        for path in candidates:
            try:
                stat = path.stat()
            except OSError:
                continue  # vanished mid-scan; next cycle will settle it
            key = str(path)
            current[key] = (stat.st_size, stat.st_mtime_ns)
            summary["seen"] += 1
            if self._seen.get(key) != current[key]:
                summary["skipped_unstable"] += 1
                continue
            fingerprint = fingerprint_for(key, stat.st_size, stat.st_mtime_ns)
            try:
                outcome = await self._handle_stable_file(
                    db, settings, path, stat.st_size, stat.st_mtime_ns, fingerprint
                )
            except Exception:
                logger.exception(
                    "Watch-folder handling failed for %s", key
                )
                outcome = "failed"
            summary[outcome] += 1
        # F-D1 companion sweep: an orphaned 'processing' claim whose
        # source changed (or vanished) after the claim is never seen
        # under its old fingerprint again, so the per-file path above
        # can never settle it. Park those rows here, once per cycle.
        try:
            await self._park_stale_claims_with_changed_source(
                db, settings, current
            )
        except Exception:
            logger.exception("Watch-folder stale-claim sweep failed")
        self._seen = current
        return summary

    async def _handle_stable_file(
        self,
        db: AsyncSession,
        settings: dict,
        path: Path,
        size_bytes: int,
        mtime_ns: int,
        fingerprint: str,
    ) -> str:
        """Queue one stable file, or explain why not. Returns the
        summary bucket: queued, failed or skipped_known."""
        user_id = settings["user_id"]
        existing = await self.repository.get_file_by_fingerprint(
            db, user_id, fingerprint
        )
        if existing is not None:
            if existing["state"] == "failed":
                claimed = await self.repository.reclaim_failed_file(
                    db,
                    existing["id"],
                    max_attempts=MAX_ATTEMPTS,
                    backoff_seconds=RETRY_BACKOFF_SECONDS,
                )
                if claimed is None:
                    return "skipped_known"
                return await self._create_for_claim(db, settings, claimed, path)
            if (
                existing["state"] == "processing"
                and existing.get("task_id") is None
            ):
                return await self._recover_stale_claim(
                    db, settings, existing, path, size_bytes, mtime_ns
                )
            return "skipped_known"

        claimed = await self.repository.claim_file(db, {
            "user_id": user_id,
            "file_path": str(path),
            "file_name": path.name,
            "fingerprint": fingerprint,
            "size_bytes": size_bytes,
            "mtime_ns": mtime_ns,
        })
        if claimed is None:
            return "skipped_known"  # another watcher claimed it first
        return await self._create_for_claim(db, settings, claimed, path)

    async def _park_stale_claims_with_changed_source(
        self,
        db: AsyncSession,
        settings: dict,
        current: dict[str, tuple[int, int]],
    ) -> None:
        """Park this user's orphaned 'processing' claims whose source
        no longer matches the claim (F-D1 companion — see scan_once).
        A claim is settled here only when the repository's conditional
        stale-takeover succeeds, so a live watcher's fresh claim and a
        claim another watcher already recovered are never touched."""
        list_stale = getattr(
            self.repository, "list_stale_processing_files", None
        )
        if list_stale is None:
            return
        user_id = settings["user_id"]
        stale = await list_stale(
            db, user_id, stale_seconds=STALE_CLAIM_SECONDS
        )
        for row in stale:
            seen = current.get(row["file_path"])
            if seen is not None and (
                int(row["size_bytes"]) == seen[0]
                and int(row["mtime_ns"]) == seen[1]
            ):
                continue  # unchanged source: the per-file path owns it
            claimed = await self.repository.reclaim_stale_processing_file(
                db, row["id"], stale_seconds=STALE_CLAIM_SECONDS
            )
            if claimed is None:
                continue
            await self.repository.update_file(db, claimed["id"], {
                "state": "parked",
                "last_error": (
                    "source_changed_or_missing: file changed after an "
                    "orphaned claim; re-copy it to retry"
                ),
            })
            logger.warning(
                "Watch-folder parked orphaned claim %s: source changed",
                claimed["id"],
            )

    async def _recover_stale_claim(
        self,
        db: AsyncSession,
        settings: dict,
        existing: dict,
        path: Path,
        size_bytes: int,
        mtime_ns: int,
    ) -> str:
        """Re-drive a claim orphaned in 'processing' with no task
        (F-D1: the watcher died between the claim insert and task
        creation). The conditional repository update is the claim, so
        two watchers can never recover the same orphan twice."""
        if (
            int(existing["size_bytes"]) != size_bytes
            or int(existing["mtime_ns"]) != mtime_ns
            or existing.get("file_path") != str(path)
        ):
            # The source changed (or vanished and another file took
            # the name) after the orphaned claim. Park the row with a
            # reason; a changed file re-triggers on its own via its
            # new fingerprint.
            await self.repository.update_file(db, existing["id"], {
                "state": "parked",
                "last_error": (
                    "source_changed_or_missing: file changed after an "
                    "orphaned claim; re-copy it to retry"
                ),
            })
            logger.warning(
                "Watch-folder parked orphaned claim %s: source changed",
                existing["id"],
            )
            return "skipped_known"
        claimed = await self.repository.reclaim_stale_processing_file(
            db, existing["id"], stale_seconds=STALE_CLAIM_SECONDS
        )
        if claimed is None:
            return "skipped_known"  # fresh claim, or another watcher won
        logger.info(
            "Watch-folder recovering orphaned claim %s for %s (attempt %s)",
            claimed["id"], path, claimed["attempts"],
        )
        return await self._create_for_claim(db, settings, claimed, path)

    async def _create_for_claim(
        self, db: AsyncSession, settings: dict, claim: dict, path: Path
    ) -> str:
        try:
            task_id = await self.task_creator(
                db, settings, path, int(claim["size_bytes"])
            )
        except Exception as exc:
            attempts = int(claim["attempts"])
            await self.repository.update_file(db, claim["id"], {
                "state": "parked" if attempts >= MAX_ATTEMPTS else "failed",
                "last_error": str(exc)[:_ERROR_LIMIT] or exc.__class__.__name__,
            })
            logger.warning(
                "Watch-folder file %s failed attempt %s: %s",
                path, attempts, exc,
            )
            return "failed"
        await self.repository.update_file(db, claim["id"], {
            "state": "queued",
            "task_id": task_id,
            "last_error": None,
        })
        logger.info("Watch-folder queued %s as task %s", path, task_id)
        return "queued"


def _place_upload(source_path: Path, dest: Path) -> None:
    """Put the watched bytes at the upload location: a hardlink when
    the filesystems allow it (zero extra disk), a real copy otherwise.
    The source file is only ever read."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    try:
        os.link(source_path, dest)
        return
    except OSError:
        pass
    tmp = dest.with_name(f".{dest.name}.partial-{secrets.token_hex(4)}")
    try:
        with open(source_path, "rb") as src, open(tmp, "wb") as out:
            shutil.copyfileobj(src, out, length=1024 * 1024)
        os.replace(tmp, dest)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise


async def create_task_from_watch_file(
    db: AsyncSession,
    settings: dict,
    source_path: Path,
    size_bytes: int,
    *,
    repository: WatchFolderRepository | None = None,
    queue_adapter=None,
) -> str:
    """The production task creator: ingest ``source_path`` as a
    completed local upload, then create and enqueue a task through the
    same service calls the create-task route makes."""
    from ..media.upload_ingest import local_upload_path
    from ..repositories.task_repository import TaskRepository
    from ..services.billing_service import BillingService
    from ..services.task_service import TaskService
    from ..services.video_service import VideoService
    from ..workers.job_queue import JobQueue

    repo = repository or WatchFolderRepository()
    queue = queue_adapter or JobQueue
    config = get_config()
    user_id = settings["user_id"]

    await BillingService(db).assert_can_create_task(user_id)

    # Recipes: a recipe set on the autopilot fills the choices the
    # settings left unmade, exactly as POST /tasks merges it. The load
    # happens before any file is ingested: an unknown, malformed or
    # deleted recipe refuses the file loudly and creates no task.
    try:
        recipe_id, fragment = await load_autopilot_recipe(db, settings)
    except ValueError as exc:
        logger.error(
            "Watch-folder autopilot recipe for user %s refused: %s",
            user_id, exc,
        )
        raise
    arguments = autopilot_task_arguments(settings, recipe_id, fragment, config)

    name = f"autopilot-{uuid4().hex}{source_path.suffix.lower()}"
    video_path = f"upload://{name}"
    dest = local_upload_path(video_path)
    await run_in_thread(_place_upload, source_path, dest)
    await repo.insert_completed_upload_session(
        db, user_id=user_id, video_path=video_path, size=size_bytes
    )

    duration = await run_in_thread(
        VideoService.probe_upload_duration, video_path, user_id
    )

    create_kwargs = arguments["create_kwargs"]
    processing_mode = create_kwargs["processing_mode"]
    cleanup_settings = arguments["cleanup_settings"]

    task_service = TaskService(db)
    task_id = await task_service.create_task_with_source(
        user_id=user_id,
        url=video_path,
        title=source_path.stem or "Uploaded Video",
        source_duration_seconds=math.ceil(duration),
        **create_kwargs,
    )

    processing_job_id = f"{task_id}:{secrets.token_hex(16)}"
    try:
        await TaskRepository.save_source_metadata(db, task_id, {
            "processing_job_id": processing_job_id,
            "clip_length": arguments["clip_length"],
            "url": video_path,
            "source_type": "video_url",
            "output_format": arguments["output_format"],
            "add_subtitles": arguments["add_subtitles"],
            "watch_folder_file": source_path.name,
            **cleanup_settings,
        })
        await queue.enqueue_processing_job(
            "process_video_task",
            processing_mode,
            task_id,
            video_path,
            "video_url",
            user_id,
            create_kwargs["font_family"],
            create_kwargs["font_size"],
            create_kwargs["font_color"],
            create_kwargs["caption_template"],
            processing_mode,
            arguments["output_format"],
            arguments["add_subtitles"],
            cleanup_settings,
            arguments["clip_length"],
            _job_id=processing_job_id,
        )
    except Exception:
        await task_service.task_repo.update_task_status(
            db, task_id, "error", progress=0,
            progress_message="Unable to queue processing. Please retry this generation.",
            expected_statuses=["queued"],
        )
        raise
    return task_id


async def _production_task_creator(
    db: AsyncSession, settings: dict, source_path: Path, size_bytes: int
) -> str:
    return await create_task_from_watch_file(db, settings, source_path, size_bytes)


async def run_watch_folder_loop(stop_event: asyncio.Event) -> None:
    """Poll every enabled user's folder until ``stop_event`` is set.

    Started from the FastAPI lifespan. Every cycle opens a fresh
    session and re-reads the settings table, so toggling the autopilot
    in the UI takes effect on the next cycle and a restart simply
    resumes from the durable claim records.
    """
    from ..database import AsyncSessionLocal

    repository = WatchFolderRepository()
    service = WatchFolderService(repository, _production_task_creator)
    poll = service.config.watch_folder_poll_seconds
    logger.info("Watch-folder autopilot loop started (poll %ss)", poll)
    while not stop_event.is_set():
        try:
            async with AsyncSessionLocal() as db:
                for settings in await repository.list_enabled_settings(db):
                    try:
                        await service.scan_once(db, settings)
                    except Exception:
                        logger.exception(
                            "Watch-folder scan failed for user %s",
                            settings.get("user_id"),
                        )
        except Exception:
            logger.exception("Watch-folder cycle failed")
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=poll)
        except asyncio.TimeoutError:
            pass
    logger.info("Watch-folder autopilot loop stopped")


def start_watch_folder_watcher() -> tuple[asyncio.Task, asyncio.Event]:
    """Start the loop as a background task. Returns (task, stop_event);
    the caller sets the event and awaits the task on shutdown."""
    stop_event = asyncio.Event()
    task = asyncio.create_task(run_watch_folder_loop(stop_event))
    return task, stop_event
