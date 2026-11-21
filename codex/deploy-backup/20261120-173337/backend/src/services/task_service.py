"""
Task service - orchestrates task creation and processing workflow.
"""

from sqlalchemy.ext.asyncio import AsyncSession
from typing import Dict, Any, List, Optional, Callable
import asyncio
import logging
import os
import secrets
from datetime import datetime, timedelta, timezone
from pathlib import Path
import json
import hashlib
from time import perf_counter

import redis.asyncio as redis


from arq.constants import job_key_prefix, in_progress_key_prefix, expires_extra_ms
from ..broll import BRollSession
from ..workers.job_queue import JobQueue
from ..repositories.task_repository import TaskRepository
from .. import provider_manager
from ..repositories.minute_usage_repository import charged_duration
from ..repositories.source_repository import SourceRepository
from ..repositories.clip_repository import ClipRepository
from ..repositories.hook_preview_repository import HookPreviewRepository
from ..repositories.cache_repository import CacheRepository
from ..repositories.task_checkpoint_repository import TaskCheckpointRepository, checkpoint_input_key
from ..auto_director.creation_modes import (
    AWAITING_SELECTION_STATUS,
    MAX_PICKED_MOMENTS,
    REVIEW_MOMENT_PICK,
    SELECTION_USER_RANGES,
    auto_pick_note,
    cached_analysis_is_user_selected,
    plan_for_mode,
    selection_autopick_timeout_minutes,
    validate_creation_mode,
    validate_manual_ranges,
    validate_picked_indexes,
)
from .video_service import VideoService
from .clip_service import ClipEditingMixin
from ..repositories.edit_transaction import serialized_task_edit, task_edit_transaction, TaskCancelled
from ..repositories.task_run_guard import TaskRunBusy, exclusive_task_run, task_run_guard
from ..repositories.generation_allowance_guard import generation_allowance_guard
from .billing_service import BillingService, reduced_media_limits
from .task_completion_email_service import (
    TaskCompletionEmailService,
    TaskCompletionRecipient,
)
from ..config import Config, get_config
from ..youtube_utils import cleanup_downloaded_files, extract_video_id
from ..youtube_proxy import YouTubeGeoRestrictedError
from ..youtube_source import load_time_map, localize_segment, restore_clip_timeline, transcript_source_for
from ..clip_cleanup import normalize_clip_cleanup_settings
from ..clip_duration import clip_duration, normalize_clip_length, validate_clip_length
from ..media.common import VALID_OUTPUT_FORMATS
from ..media.music_library import validate_music_mode
from ..ai import TRANSCRIPT_ANALYSIS_CACHE_VERSION, validate_target_clip_count
from ..auto_director.niche import (
    is_known_profile,
    resolve_effective_profile,
    validate_niche_override,
)
from ..auto_director.template_mode import validate_template_mode
from ..auto_director.framing_mode import validate_framing_mode
from ..media.gaming_templates import clean_layout_credit, validate_layout_template
from ..clip_source_map import (
    load_clip_caption_settings,
    load_clip_reframe,
    load_clip_render_facts,
)
from ..auto_director.edl import load_profile
from ..media.clip_qc import ClipQcContext, run_clip_qc
from ..media.ffmpeg import check_nvenc_available, force_cpu_encode, render_eco_mode
from ..media.timeline import parse_timestamp_to_seconds
from ..media_storage import MediaStorage, media_work
from ..utils.async_helpers import run_in_thread
from ..utils.user_errors import user_facing_error_message
from ..repositories.media_repository import queue_delete, guard_source_creation
from ..stream_source import cleanup_stream_path, cleanup_stream_task, seed_stream_resolution

logger = logging.getLogger(__name__)
PROCESSING_CACHE_VERSION = "20260319_grounded_segments_v1"


class NoCompliantMomentsError(Exception):
    """A brief task found zero compliant moments (design §5.5).

    Carries its own user-facing message: what the brief asked for and
    how many moments were checked. Mapped to error_code
    ``no_compliant_moments`` by process_task's terminal handling.
    """


class MomentSelectionNotWaitingError(Exception):
    """A moment pick was applied to a task not parked for one.

    Raised when the task left ``awaiting_selection`` first — most
    importantly when a user's own pick beat the auto-pick sweep to it,
    in which case the user's pick stands and the sweep backs off.
    """


class MomentSelectionSlateLostError(Exception):
    """The parked task's candidate slate is no longer available."""


class MomentSelectionSourceMissingError(Exception):
    """The parked task has no source URL/type to re-enqueue with."""


def _redis_client() -> "redis.Redis":
    """A redis client with the runtime config's connection settings.

    The same settings the routes build their clients with; kwargs are
    assembled as a dict so the credential is passed by name lookup.
    """
    runtime_config = get_config()
    client_kwargs: Dict[str, Any] = {
        "host": runtime_config.redis_host,
        "port": runtime_config.redis_port,
        "decode_responses": True,
    }
    client_kwargs["password"] = runtime_config.redis_password
    return redis.Redis(**client_kwargs)


async def _clear_task_cancel_flag(task_id: str) -> None:
    """Drop a stale cancel flag before a (re)queued run.

    Mirrors the routes' discipline: a flag left over from a previous
    run must not cancel the run this pick is about to queue.
    """
    redis_client = _redis_client()
    try:
        await redis_client.delete(f"task_cancel:{task_id}")
    finally:
        await redis_client.aclose()


async def _mirror_task_source_metadata(
    task_id: str, metadata: Dict[str, Any]
) -> None:
    """Best-effort refresh of the redis copy of a task's source metadata.

    The database row is authoritative; the redis key is the read cache
    the routes fall back to, kept in step exactly as the routes' own
    save helper keeps it. A redis failure never fails the pick.
    """
    redis_client = _redis_client()
    try:
        await redis_client.set(
            f"task_source:{task_id}",
            json.dumps(metadata),
            ex=60 * 60 * 24 * 7,
        )
    except Exception as exc:
        logger.warning(
            "Unable to save task source metadata for %s: %s", task_id, exc
        )
    finally:
        await redis_client.aclose()


def _as_utc(value: Any) -> Optional[datetime]:
    """A stored timestamp as an aware UTC datetime (naive means UTC)."""
    if not isinstance(value, datetime):
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _parse_stored_timestamp(value: Any) -> Optional[datetime]:
    """Parse an ISO timestamp stored in task metadata, if present."""
    if isinstance(value, datetime):
        return _as_utc(value)
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    return _as_utc(parsed)


def _candidate_rank_score(candidate: Any) -> float:
    """The ranking score a candidate carries, 0 when it carries none.

    AI candidates rank by their virality total (the order the slate
    itself was built in); visual-moment candidates may carry flat
    score fields instead. Anything unrecognised scores 0, so an
    unscored slate auto-picks in slate order.
    """
    if not isinstance(candidate, dict):
        return 0.0
    virality = candidate.get("virality")
    if isinstance(virality, dict):
        total = virality.get("total_score")
        if isinstance(total, (int, float)) and not isinstance(total, bool):
            return float(total)
    for key in ("total_score", "virality_score", "relevance_score"):
        value = candidate.get(key)
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            return float(value)
    return 0.0


def _selection_target_count(
    task: Dict[str, Any], checkpoint: Dict[str, Any], slate_size: int
) -> int:
    """How many moments an auto-pick continues with.

    The pipeline stored the exact target next to the slate when it
    parked; tasks parked before that was recorded fall back to their
    explicit clip-count target, and Auto tasks to half the slate
    (the reserve slate is built at twice the render target).
    """
    stored = checkpoint.get("selection_target_count")
    if isinstance(stored, int) and not isinstance(stored, bool) and stored >= 1:
        target = stored
    elif isinstance(task.get("target_clip_count"), int) and (
        task["target_clip_count"] or 0
    ) >= 1:
        target = task["target_clip_count"]
    else:
        target = (slate_size + 1) // 2
    return max(1, min(target, slate_size, MAX_PICKED_MOMENTS))

DEFAULT_CLIP_RENDER_CONCURRENCY = 3

# When the effective encoder is the CPU the final encodes are libx264:
# N concurrent encodes divide the same physical cores, and each profiled
# (EDL) render peaks at several GB of RAM on top of that. On a
# memory-tight machine a wide fan-out does not slow renders down -- the
# OS kills the excess ones outright (SIGKILL, mid-clip, with no error in
# the log). One render at a time is the safe default for CPU encoding;
# an explicit CLIP_RENDER_CONCURRENCY always wins.
CPU_DEFAULT_RENDER_CONCURRENCY = 1


def _effective_cpu_encoder() -> bool:
    """Whether final encodes will run on the CPU.

    Mirrors the encoder choice itself (media.ffmpeg's
    build_final_video_encode_args, media.edl_render's get_video_encoder):
    NVENC only when FORCE_CPU_ENCODE does not pin the CPU and the probe
    finds a working GPU encoder.
    """
    return force_cpu_encode() or not check_nvenc_available()


def clip_render_concurrency() -> int:
    """How many clips may render at once (CLIP_RENDER_CONCURRENCY, default 3).

    Rendering is thread-pool ffmpeg work, so clips overlap safely; the bound
    keeps a many-clip task from saturating the CPU/GPU and the temp disk.
    An explicit, valid CLIP_RENDER_CONCURRENCY is honoured as-is. Without
    one, the default is ``DEFAULT_CLIP_RENDER_CONCURRENCY`` when encodes
    will run on the GPU and ``CPU_DEFAULT_RENDER_CONCURRENCY`` when the
    effective encoder is the CPU — see the constant's comment. Unset,
    invalid or non-positive values count as "no explicit setting".

    Eco mode (RENDER_ECO_MODE) outranks every other input, including an
    explicit CLIP_RENDER_CONCURRENCY: one clip at a time is the point of
    the mode (see media.ffmpeg's eco section).
    """
    if render_eco_mode():
        return 1
    raw = os.environ.get("CLIP_RENDER_CONCURRENCY", "").strip()
    try:
        value = int(raw)
    except ValueError:
        value = 0
    if value > 0:
        return value
    if _effective_cpu_encoder():
        return CPU_DEFAULT_RENDER_CONCURRENCY
    return DEFAULT_CLIP_RENDER_CONCURRENCY


def _partial_failure_details(
    clip_ids: List[str],
    failed_positions: List[int],
    render_failures: Dict[int, Optional[BaseException]],
) -> Optional[Dict[str, Any]]:
    """Honest partial-success record for a task that kept usable clips.

    Returns None when nothing failed, so clean runs keep their existing
    stored shape. The per-clip reasons go through the same scrubber as
    every other user-facing error: raw render/provider errors can embed
    signed URLs and keys and must never be stored on the task.
    """
    if not clip_ids or not failed_positions:
        return None
    failures = []
    for position in sorted(failed_positions):
        exc = render_failures.get(position)
        failures.append(
            {
                "position": position,
                "reason": user_facing_error_message(
                    exc, "task_error"
                )
                if exc is not None
                else "This clip could not be rendered.",
            }
        )
    return {
        "clips_completed": len(clip_ids),
        "clips_failed": len(failures),
        "failed_clips": failures,
    }


def _collect_clip_render_timings(
    stage_timings: Dict[str, Any],
    position: int,
    clip_info: Optional[Dict[str, Any]],
) -> None:
    """Fold one clip's measured render passes into the task timings (F-P3).

    Entries land under ``stage_timings["clips"]`` — a list of
    ``{"position", "filename", <pass>_seconds...}`` dicts — inside the
    SAME ``stage_timings_json`` the task already persists at completion,
    so no new storage is invented. Publishing analytics sums only
    numeric stage values, so the list is invisible to it. Observation
    only: clips without timings add nothing, and this never raises.
    """
    try:
        timings = (clip_info or {}).get("render_timings")
        if not isinstance(timings, dict) or not timings:
            return
        entry: Dict[str, Any] = {
            "position": position,
            "filename": (clip_info or {}).get("filename"),
        }
        diagnostic_keys = ("encoder", "nvenc_probe_error")
        for key, value in timings.items():
            if key in diagnostic_keys:
                continue
            if isinstance(value, (int, float)) and value >= 0:
                entry[key] = round(float(value), 3)
        # The encoder stamp is textual, not a duration: carry the two
        # diagnostic keys alongside the numeric passes so a task whose
        # clips rendered on the CPU can say why (NVENC probe verdict).
        for key in diagnostic_keys:
            value = timings.get(key)
            if isinstance(value, str) and value:
                entry[key] = value
        if len(entry) > 2:
            stage_timings.setdefault("clips", []).append(entry)
    except Exception:
        pass


# The stand-in the pipeline writes when every analysis provider
# fails (video_service.process_video_complete), and the reasoning
# prefix the deterministic visual floor stamps on its segments
# (auto_director.visual_moments, reason_context "AI analysis
# unavailable"). Both literals live in those modules; these copies
# are the cache layer's detector, so a wording change there must
# update them here. The transcript-less floor ("no usable
# transcript") is deliberately NOT matched: its clips are the
# designed selection for sources with no transcript, and its
# summary is a real AI product.
DEGRADED_ANALYSIS_SUMMARY = (
    "AI analysis was unavailable, so these clips were picked by the "
    "deterministic audio/visual fallback instead of the AI."
)
DEGRADED_SEGMENT_REASONING_PREFIX = (
    "Visual selection (AI analysis unavailable)"
)


def cached_analysis_is_degraded(payload: object) -> bool:
    """Whether a cached analysis payload is the deterministic floor
    stand-in written when AI analysis failed (F-R2).

    A stand-in is identified two ways: its summary, or any segment
    carrying the floor's reasoning prefix. Either leg alone marks
    the payload: a run whose visual selection itself fell over to
    the blind fallback window still carries the stand-in summary.
    An unparseable payload is not degraded — the caller's normal
    parse path already discards it, so this only ever refuses a
    payload, never a run.
    """
    if not isinstance(payload, str) or not payload:
        return False
    try:
        analysis = json.loads(payload)
    except (TypeError, ValueError):
        return False
    if not isinstance(analysis, dict):
        return False
    if analysis.get("summary") == DEGRADED_ANALYSIS_SUMMARY:
        return True
    segments = analysis.get("most_relevant_segments") or []
    if not isinstance(segments, list):
        return False
    return any(
        isinstance(segment, dict)
        and isinstance(segment.get("reasoning"), str)
        and segment["reasoning"].startswith(
            DEGRADED_SEGMENT_REASONING_PREFIX
        )
        for segment in segments
    )


class TaskService(ClipEditingMixin):
    """Service for task workflow orchestration."""

    def __init__(self, db: AsyncSession, config: Config | None = None):
        self.db = db
        self.task_repo = TaskRepository()
        from ..repositories.brand_kit_repository import BrandKitRepository

        self.brand_kit_repo = BrandKitRepository()
        self.source_repo = SourceRepository()
        self.clip_repo = ClipRepository()
        self.hook_preview_repo = HookPreviewRepository()
        self.cache_repo = CacheRepository()
        self.checkpoint_repo = TaskCheckpointRepository()
        self.video_service = VideoService()
        self.config = config or get_config()
        self.reused_upload_task = False
        self.reused_upload_job_id = None

    @staticmethod
    def _build_cache_key(
        url: str, source_type: str, processing_mode: str, quality: Optional[str] = None
    ) -> str:
        payload = (
            f"{source_type}|{processing_mode}|"
            f"{TRANSCRIPT_ANALYSIS_CACHE_VERSION}|{url.strip()}"
        )
        # Reduced-quality YouTube downloads get their own entry so a 1080p
        # request never reuses a 720p source. Full quality keeps the old key.
        if quality and source_type == "youtube":
            payload += f"|q{quality}"
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    @staticmethod
    def _build_analysis_cache_key(
        cache_key: str,
        clip_length: str = "auto",
        target_clip_count: Optional[int] = None,
        brief_fingerprint: Optional[str] = None,
        selection: Optional[str] = None,
    ) -> str:
        """Row holding the analysis for a preset. Auto keeps the source row.

        The source and transcript do not depend on the preset, so other presets
        store only their analysis under a derived key and share the rest. An
        explicit clip-count target also derives its own row: a targeted
        analysis selects a different number of segments, so it must never be
        served to an Auto task (or the other way round). A campaign brief
        changes selection at least as much, so its fingerprint also derives
        its own row — brief and non-brief tasks on the same source must
        never poison each other's analysis cache. A user-ranges selection
        derives its own row too: Manual Cutter ranges are the user's
        choice, not an AI pick, so they must never be written into the
        row an AI mode reads (F-1). AI selections (ranked or candidates
        for review, legacy included) keep the historic row, so
        same-mode reuse still hits.
        """
        parts = []
        if validate_clip_length(clip_length) != "auto":
            parts.append(f"length:{clip_length}")
        if target_clip_count is not None:
            parts.append(f"clips:{target_clip_count}")
        if brief_fingerprint:
            parts.append(f"brief:{brief_fingerprint}")
        if selection == SELECTION_USER_RANGES:
            parts.append(f"selection:{selection}")
        if not parts:
            return cache_key
        payload = cache_key + "|" + "|".join(parts)
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    async def _media_limits(self, user_id: Optional[str]) -> tuple[Optional[str], Optional[int]]:
        """(download quality, clip cap) for the user's plan; see reduced_media_limits."""
        if not user_id:
            return None, None
        summary = await BillingService(self.db, self.config).get_usage_summary(user_id)
        return reduced_media_limits(summary)

    def _is_stale_queued_task(self, task: Dict[str, Any]) -> bool:
        """Detect queued tasks that have likely stalled due to worker issues."""
        if task.get("status") != "queued":
            return False

        return self._task_age_seconds(task) >= self.config.queued_task_timeout_seconds

    async def _job_is_gone(self, job_id: str) -> bool:
        """True when arq has no queued or in-progress record of the job.

        Redis errors return False: lifecycle recovery must never fail a
        task it could not actually check.
        """
        try:
            pool = await JobQueue.get_pool()
            return not bool(await pool.exists(
                job_key_prefix + job_id, in_progress_key_prefix + job_id
            ))
        except Exception:
            logger.exception("Could not verify job %s", job_id)
            return False

    async def _queued_job_is_missing(self, task: Dict[str, Any]) -> bool:
        """Confirm this reservation has no queued/running ARQ job, not worker health.

        A heartbeat gap cannot invalidate a healthy backlog. Redis outages leave
        reservations intact until job existence can be checked again.
        """
        metadata = await self.task_repo.load_source_metadata(self.db, task["id"])
        job_id = metadata.get("processing_job_id")
        if not job_id:
            # Old tasks predate tracked job IDs. Give their jobs the full ARQ
            # retention window instead of dropping them during a worker restart.
            return self._task_age_seconds(task) >= max(
                self.config.queued_task_timeout_seconds, expires_extra_ms / 1000
            )
        return await self._job_is_gone(job_id)

    async def _processing_job_is_missing(self, task: Dict[str, Any]) -> bool:
        """True when a processing task's tracked arq job is provably gone.

        Only meaningful at worker startup: a live job is either queued or
        in some worker's in-progress set, and both are checked. Tasks
        without a tracked job id (created before job ids were recorded)
        return False so they keep the age-based rule instead of being
        failed on a guess.
        """
        metadata = await self.task_repo.load_source_metadata(self.db, task["id"])
        job_id = metadata.get("processing_job_id")
        if not job_id:
            return False
        return await self._job_is_gone(job_id)

    def _is_stale_processing_task(self, task: Dict[str, Any]) -> bool:
        """Detect processing tasks whose worker likely died mid-run.

        Recovers tasks stuck in "processing" (e.g. arq hard-killed the job past
        its timeout) so they don't stay "processing" forever. The configured
        timeout defaults well above arq's job_timeout, so a legitimately
        long-running job is never falsely swept into "error".
        """
        if task.get("status") != "processing":
            return False

        return (
            self._task_age_seconds(task)
            >= self.config.processing_task_timeout_seconds
        )

    @staticmethod
    def _task_age_seconds(task: Dict[str, Any]) -> float:
        """Seconds since the task's last update (or creation)."""
        created_at = task.get("created_at")
        updated_at = task.get("updated_at") or created_at

        if not created_at or not updated_at:
            return 0.0

        now = (
            datetime.now(updated_at.tzinfo)
            if getattr(updated_at, "tzinfo", None)
            else datetime.utcnow()
        )
        return (now - updated_at).total_seconds()

    # Stale-task messages live here so the lazy sweep on task reads and the
    # worker-startup sweep can never drift apart.
    _QUEUED_JOB_LOST_MESSAGE = (
        "The queued job is no longer available. Please retry this generation."
    )
    _PROCESSING_STALLED_MESSAGE = (
        "Task stalled during processing (worker likely stopped or timed out). "
        "Check the worker logs (docker-compose logs -f worker) and try again."
    )
    _WORKER_RESTARTED_MESSAGE = "Worker restarted while this task was running."

    async def _fail_stale_task(
        self, task: Dict[str, Any], *, worker_startup: bool = False
    ) -> bool:
        """Fail one queued/processing task whose worker is provably gone.

        Returns True when this call transitioned the task to "error". Every
        transition is guarded by the status it was read with, so the sweep
        is safe next to live work and idempotent: a task already in a
        terminal state is never touched twice.
        """
        task_id = task["id"]
        if task.get("status") == "queued":
            if self._is_stale_queued_task(task) and await self._queued_job_is_missing(task):
                logger.warning(
                    "Task %s stuck in queued status for over %ss; marking as error",
                    task_id,
                    self.config.queued_task_timeout_seconds,
                )
                return await self.task_repo.update_task_status(
                    self.db,
                    task_id,
                    "error",
                    progress=0,
                    progress_message=self._QUEUED_JOB_LOST_MESSAGE,
                    expected_statuses=["queued"],
                )
            return False
        if task.get("status") == "processing":
            # At worker startup this process owns no jobs yet, so a
            # processing task whose tracked arq job is in neither the queue
            # nor any worker's in-progress set was orphaned by the previous
            # worker's death — fail it now instead of waiting out the age
            # threshold. A job still in progress elsewhere is left alone,
            # and tasks without a tracked job id keep the age rule below.
            if worker_startup and await self._processing_job_is_missing(task):
                logger.warning(
                    "Task %s was processing when the worker restarted; marking as error",
                    task_id,
                )
                return await self.task_repo.update_task_status(
                    self.db,
                    task_id,
                    "error",
                    progress=0,
                    progress_message=self._WORKER_RESTARTED_MESSAGE,
                    expected_statuses=["processing"],
                )
            if self._is_stale_processing_task(task):
                logger.warning(
                    "Task %s stuck in processing status for over %ss; marking as error",
                    task_id,
                    self.config.processing_task_timeout_seconds,
                )
                return await self.task_repo.update_task_status(
                    self.db,
                    task_id,
                    "error",
                    progress=0,
                    progress_message=self._PROCESSING_STALLED_MESSAGE,
                    expected_statuses=["processing"],
                )
        return False

    async def recover_stale_tasks(
        self, *, worker_startup: bool = False
    ) -> Dict[str, int]:
        """Fail every queued/processing task whose worker is gone.

        Runs on worker startup (``worker_startup=True`` enables the orphan
        check in :meth:`_fail_stale_task`) and is safe to run again at any
        time: only active statuses are selected and each transition is
        status-guarded, so a second run changes nothing.
        """
        counts = {"checked": 0, "failed": 0}
        tasks = await self.task_repo.list_tasks_by_statuses(
            self.db, ["queued", "processing"]
        )
        for task in tasks:
            counts["checked"] += 1
            if await self._fail_stale_task(task, worker_startup=worker_startup):
                counts["failed"] += 1
        if counts["failed"]:
            logger.warning(
                "Recovered %s stale task(s) out of %s active",
                counts["failed"],
                counts["checked"],
            )
        return counts

    async def load_task_checkpoint(
        self, task: Dict[str, Any], metadata: Dict[str, Any]
    ) -> tuple[Dict[str, Any], str]:
        """The processing checkpoint for a task, with its input key.

        The key is recomputed from the same stored inputs the worker
        used (source, processing mode, clip length), so reads and
        writes here address the exact checkpoint the pipeline wrote.
        """
        source_url = task.get("source_url") or metadata.get("url")
        source_type = task.get("source_type") or metadata.get("source_type")
        if not source_url or not source_type:
            return {}, ""
        input_key = checkpoint_input_key(
            source_url,
            source_type,
            task.get("processing_mode") or self.config.default_processing_mode,
            normalize_clip_length(metadata.get("clip_length")),
            self.config,
        )
        checkpoint = await self.checkpoint_repo.get(
            self.db, task["id"], input_key
        )
        return checkpoint, input_key

    async def apply_moment_selection(
        self,
        task_id: str,
        segment_indexes: Any,
        *,
        automatic: bool = False,
        autopick_timeout_minutes: Optional[int] = None,
    ) -> Dict[str, Any]:
        """Write a moment pick as the checkpoint's final plan, then queue.

        The one pick path, shared by the select-moments endpoint (the
        user's own pick) and the auto-pick sweep (``automatic=True``
        after the park timeout). The pick becomes the checkpoint's
        final plan *before* the job is queued, so a worker can never
        start from the unpicked slate; the status flip is guarded on
        the task still waiting, so a pick that loses a race against
        another pick changes nothing.

        An automatic pick is recorded as one — the checkpoint, the
        task's source metadata and the progress message all carry the
        auto-pick note. Nothing pretends the user picked.
        """
        async with task_run_guard(self.db, task_id):
            task = await self.task_repo.get_task_by_id(self.db, task_id)
            if not task or task.get("status") != AWAITING_SELECTION_STATUS:
                raise MomentSelectionNotWaitingError(
                    "Task is not waiting for a moment selection"
                )

            metadata = await self.task_repo.load_source_metadata(
                self.db, task_id
            )
            checkpoint, input_key = await self.load_task_checkpoint(
                task, metadata
            )
            candidates = checkpoint.get("candidates") or []
            if not candidates:
                raise MomentSelectionSlateLostError(
                    "The candidate moments for this task are no longer "
                    "available"
                )
            picked_indexes = validate_picked_indexes(
                segment_indexes, len(candidates)
            )
            picked = [candidates[index] for index in picked_indexes]

            source_url = task.get("source_url") or metadata.get("url")
            source_type = task.get("source_type") or metadata.get(
                "source_type"
            )
            if not source_url or not source_type:
                raise MomentSelectionSourceMissingError(
                    "Task source URL is missing"
                )
            output_format = "vertical"
            of = metadata.get("output_format", output_format)
            if of in VALID_OUTPUT_FORMATS:
                output_format = of
            add_subtitles = metadata.get("add_subtitles", True)
            if not isinstance(add_subtitles, bool):
                add_subtitles = True
            cleanup_settings = normalize_clip_cleanup_settings(
                metadata.get("cut_long_pauses"),
                metadata.get("pause_threshold_ms"),
                metadata.get("remove_filler_words"),
                metadata.get("filtered_words"),
            )

            async with generation_allowance_guard(
                self.db,
                task["user_id"],
                enabled=self.config.monetization_enabled,
            ):
                await BillingService(self.db).assert_can_resume_task(
                    task["user_id"], task_id
                )

                try:
                    await _clear_task_cancel_flag(task_id)

                    if automatic:
                        timeout_minutes = (
                            autopick_timeout_minutes
                            if autopick_timeout_minutes is not None
                            else selection_autopick_timeout_minutes()
                        )
                        note = auto_pick_note(timeout_minutes)
                        plan_patch: Dict[str, Any] = {
                            "segments": picked,
                            "selection_source": "auto_timeout",
                            "selection_note": note,
                        }
                        progress_message = note
                    else:
                        plan_patch = {
                            "segments": picked,
                            "selection_source": "user",
                        }
                        progress_message = "Selected moments queued"

                    # The pick becomes the final plan before the job is
                    # queued, so a worker can never start from the
                    # unpicked slate.
                    await self.checkpoint_repo.save(
                        self.db, task_id, input_key, plan_patch
                    )

                    flipped = await self.task_repo.update_task_status(
                        self.db,
                        task_id,
                        "queued",
                        progress=0,
                        progress_message=progress_message,
                        expected_statuses=[AWAITING_SELECTION_STATUS],
                    )
                    if not flipped:
                        raise MomentSelectionNotWaitingError(
                            "Task is not waiting for a moment selection"
                        )

                    processing_mode = (
                        task.get("processing_mode")
                        or self.config.default_processing_mode
                    )

                    processing_job_id = f"{task_id}:{secrets.token_hex(16)}"
                    metadata_patch: Dict[str, Any] = {
                        "processing_job_id": processing_job_id,
                    }
                    if automatic:
                        metadata_patch["selection_source"] = "auto_timeout"
                        metadata_patch["selection_note"] = note
                        metadata_patch["selection_auto_picked_at"] = (
                            datetime.now(timezone.utc).isoformat()
                        )
                    await self.task_repo.save_source_metadata(
                        self.db, task_id, metadata_patch
                    )
                    await _mirror_task_source_metadata(
                        task_id, {**metadata, **metadata_patch}
                    )

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
                    await self.task_repo.update_task_status(
                        self.db,
                        task_id,
                        AWAITING_SELECTION_STATUS,
                        progress_message="Could not queue rendering. "
                        "Please try again.",
                        expected_statuses=[
                            "queued",
                            AWAITING_SELECTION_STATUS,
                        ],
                    )
                    raise

                return {
                    "job_id": job_id,
                    "selected_count": len(picked),
                }

    @staticmethod
    def _parked_at(
        task: Dict[str, Any], metadata: Dict[str, Any]
    ) -> Optional[datetime]:
        """When a parked task's wait began.

        The park site stamps ``selection_parked_at`` into the task's
        source metadata; tasks parked before the stamp existed fall
        back to the row's ``updated_at``, which the park transition
        itself set.
        """
        return _parse_stored_timestamp(
            metadata.get("selection_parked_at")
        ) or _as_utc(task.get("updated_at"))

    def selection_wait_info(
        self, task: Dict[str, Any], metadata: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Park timing for the selection UI.

        When the wait started, the configured auto-pick timeout, and
        the deadline the sweep continues the task at — so the picker
        can say plainly how long the user has before the top picks
        are chosen for them.
        """
        timeout_minutes = selection_autopick_timeout_minutes()
        parked_at = self._parked_at(task, metadata)
        return {
            "selection_parked_at": (
                parked_at.isoformat() if parked_at else None
            ),
            "auto_pick_minutes": timeout_minutes,
            "auto_pick_deadline": (
                (parked_at + timedelta(minutes=timeout_minutes)).isoformat()
                if parked_at
                else None
            ),
        }

    async def auto_pick_stale_selections(self) -> Dict[str, int]:
        """Continue Find-Moments tasks whose pick never came.

        A review-mode task parks at ``awaiting_selection`` until the
        user picks; a user who is away would otherwise strand the
        generation forever. This sweep — run periodically by the
        worker, in the spirit of the watch-folder stale-claim recovery
        — auto-picks the top-ranked moments for any task parked longer
        than the configured timeout, through the same pick path the
        select-moments endpoint uses. Safe to run at any time: young
        tasks are skipped, a user pick racing the sweep wins, tasks
        that already continued are no longer in the swept status, and
        a failed auto-pick leaves the task parked for a later run.
        """
        counts = {"checked": 0, "auto_picked": 0, "failed": 0}
        timeout_minutes = selection_autopick_timeout_minutes()
        tasks = await self.task_repo.list_tasks_by_statuses(
            self.db, [AWAITING_SELECTION_STATUS]
        )
        for task in tasks:
            counts["checked"] += 1
            try:
                picked = await self._auto_pick_if_stale(
                    task, timeout_minutes
                )
            except Exception:
                counts["failed"] += 1
                logger.warning(
                    "Auto-pick could not continue parked task %s; "
                    "leaving it parked",
                    task.get("id"),
                    exc_info=True,
                )
                continue
            if picked:
                counts["auto_picked"] += 1
        if counts["auto_picked"] or counts["failed"]:
            logger.info(
                "Auto-pick sweep: %s of %s parked task(s) continued, "
                "%s failed",
                counts["auto_picked"],
                counts["checked"],
                counts["failed"],
            )
        return counts

    async def _auto_pick_if_stale(
        self, task_row: Dict[str, Any], timeout_minutes: int
    ) -> bool:
        """Auto-pick one parked task if its wait passed the timeout."""
        task_id = task_row["id"]
        task = await self.task_repo.get_task_by_id(self.db, task_id)
        if not task or task.get("status") != AWAITING_SELECTION_STATUS:
            return False
        # Structural guarantee, not an incidental one: only the two
        # Find-Moments review modes are ever auto-picked. Full Auto and
        # Manual Cutter tasks never park, and this check keeps it that
        # way even if a task row were ever parked by another path.
        if plan_for_mode(task.get("creation_mode")).review != REVIEW_MOMENT_PICK:
            return False

        metadata = await self.task_repo.load_source_metadata(
            self.db, task_id
        )
        parked_at = self._parked_at(task, metadata)
        if parked_at is None:
            return False
        if datetime.now(timezone.utc) - parked_at < timedelta(
            minutes=timeout_minutes
        ):
            return False

        checkpoint, _input_key = await self.load_task_checkpoint(
            task, metadata
        )
        candidates = checkpoint.get("candidates") or []
        if not candidates:
            logger.warning(
                "Parked task %s has no candidate slate to auto-pick; "
                "leaving it parked",
                task_id,
            )
            return False

        target = _selection_target_count(task, checkpoint, len(candidates))
        ranked_indexes = sorted(
            range(len(candidates)),
            key=lambda index: (
                -_candidate_rank_score(candidates[index]),
                index,
            ),
        )
        try:
            await self.apply_moment_selection(
                task_id,
                ranked_indexes[:target],
                automatic=True,
                autopick_timeout_minutes=timeout_minutes,
            )
        except (MomentSelectionNotWaitingError, TaskRunBusy):
            # The user's own pick landed first (or is landing now);
            # it always wins, and the sweep simply backs off.
            return False
        logger.info(
            "Auto-picked the top %d moment(s) for task %s after %d "
            "minutes without a selection",
            target,
            task_id,
            timeout_minutes,
        )
        return True

    @staticmethod
    def _cleanup_source_video(
        video_path: Optional[Path], source_type: str, url: str
    ) -> None:
        """Delete the downloaded source video + audio sidecar after a task.

        Generated clips and the tiny transcript-cache JSON are left in place.
        For YouTube sources, ``cleanup_downloaded_files`` removes downloaded
        media and partials but retains word timings. For uploads we remove the audio sidecar
        generated for transcription — the original upload is owned elsewhere.
        """
        if not video_path:
            return

        if source_type == "youtube":
            try:
                video_id = extract_video_id(url)
                if video_id:
                    try:
                        MediaStorage().save_youtube_metadata(
                            video_id, transcript_source_for(video_id, video_path)
                        )
                    except Exception:
                        logger.exception("Could not back up YouTube word timings")
                    cleanup_downloaded_files(video_id, source_path=video_path)
                    logger.info("Cleaned up YouTube source artifacts for %s", video_id)
                    return
            except Exception as e:
                logger.warning("Failed YouTube source cleanup: %s", e)

        if source_type in {"twitch", "kick"}:
            try:
                # Removes only the operation (processing job or one editor
                # render) that produced this file, never a concurrent edit's.
                if cleanup_stream_path(video_path):
                    logger.info("Cleaned up stream source artifacts")
                    return
            except Exception as e:
                logger.warning("Failed stream source cleanup: %s", e)

        # Fallback / upload path: at least drop the transcription audio sidecar
        # (e.g. "<stem>.transcription.mp3") next to the source file.
        try:
            MediaStorage().save_source_metadata(video_path)
            sidecar = video_path.with_name(f"{video_path.stem}.transcription.mp3")
            if sidecar.exists():
                sidecar.unlink()
                logger.info("Removed transcription audio sidecar: %s", sidecar.name)
        except Exception as e:
            logger.warning("Failed to remove transcription sidecar: %s", e)

    @guard_source_creation
    async def create_task_with_source(
        self,
        user_id: str,
        url: str,
        title: Optional[str] = None,
        font_family: Optional[str] = None,
        font_size: Optional[int] = None,
        font_color: Optional[str] = None,
        caption_template: str = "default",
        include_broll: bool = False,
        processing_mode: str = "fast",
        niche_override: Optional[str] = None,
        music_override: Optional[str] = None,
        music_mode: Optional[str] = None,
        template_mode: Optional[str] = None,
        framing_mode: Optional[str] = None,
        layout_template: Optional[str] = None,
        layout_credit: Optional[str] = None,
        hook_title_enabled: Optional[bool] = None,
        broll_enabled: Optional[bool] = None,
        separation_opt_in: bool = False,
        target_clip_count: Optional[int] = None,
        creation_mode: Optional[str] = None,
        manual_ranges: Optional[List[Dict[str, float]]] = None,
        source_duration_seconds: Optional[int] = None,
        upload_request_key: Optional[str] = None,
        content_brief: Optional[Dict[str, Any]] = None,
        campaign_id: Optional[str] = None,
        brand_kit_id: Optional[str] = None,
        creator_name: Optional[str] = None,
        recipe_id: Optional[str] = None,
    ) -> str:
        """
        Create a new task with associated source.
        Returns the task ID.
        """
        self.reused_upload_task = False
        self.reused_upload_job_id = None
        # Validate user exists
        if not await self.task_repo.user_exists(self.db, user_id):
            raise ValueError(f"User {user_id} not found")

        # Brand Kit opt-in: the kit fills only what the task itself left
        # unchosen (explicit task choice > kit default > profile
        # default). An unknown or foreign kit id is refused, never
        # silently ignored.
        brand_kit = None
        if brand_kit_id:
            brand_kit = await self.brand_kit_repo.get_kit(
                self.db, user_id, brand_kit_id
            )
            if brand_kit is None:
                raise ValueError("Brand kit not found")
            if caption_template == "default" and brand_kit.get("caption_template"):
                caption_template = brand_kit["caption_template"]
            if font_family is None and brand_kit.get("font_name"):
                font_family = brand_kit["font_name"]
            if music_override is None and brand_kit.get("music_track"):
                music_override = brand_kit["music_track"]

        if upload_request_key and url.startswith("upload://"):
            existing = await self.task_repo.find_recent_upload_task(
                self.db, user_id, upload_request_key,
            )
            if existing is not None:
                self.reused_upload_task = True
                self.reused_upload_job_id = existing.job_id or existing.id
                return str(existing.id)

        # Determine source type
        source_type = self.video_service.determine_source_type(url)
        if url.startswith("upload://s3/"):
            await run_in_thread(MediaStorage().validate_source, url, user_id)

        # Routes preflight new sources. Other callers must also persist a verified
        # duration, rather than creating an unmetered task with a NULL default.
        try:
            source_duration_seconds = charged_duration(source_duration_seconds)
        except ValueError:
            source_duration_seconds = await self.video_service.measure_source_duration(url, user_id)

        # Get or generate title
        if not title:
            if source_type == "youtube":
                title = await self.video_service.get_video_title(url)
            else:
                title = "Uploaded Video"

        # Create source
        source_id = await self.source_repo.create_source(
            self.db, source_type=source_type, title=title, url=url
        )

        # Create task
        task_id = await self.task_repo.create_task(
            self.db,
            user_id=user_id,
            source_id=source_id,
            status="queued",  # Changed from "processing" to "queued"
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
            manual_ranges_json=(
                json.dumps(manual_ranges) if manual_ranges else None
            ),
            source_duration_seconds=source_duration_seconds,
            charge_minutes=self.config.monetization_enabled,
            content_brief=(
                json.dumps(content_brief) if content_brief is not None else None
            ),
            campaign_id=campaign_id,
            brand_kit_id=brand_kit_id if brand_kit else None,
            intro_asset=brand_kit.get("intro_asset") if brand_kit else None,
            outro_asset=brand_kit.get("outro_asset") if brand_kit else None,
        )

        if upload_request_key and url.startswith("upload://"):
            metadata: Dict[str, Any] = {"upload_request_key": upload_request_key}
            from ..repositories.upload_repository import get_stored_upload

            stored = await get_stored_upload(self.db, url)
            if stored is not None and stored.storage_reference:
                metadata["storage_reference"] = stored.storage_reference
            await self.task_repo.save_source_metadata(self.db, task_id, metadata)

        # Creator attribution for hook titles (title_person): callers that
        # fetched source metadata (YouTube/stream preflight, channel
        # monitor) pass the channel/uploader name; it is stored on the
        # task's source_metadata so the title guardrail can attribute
        # rewrites ("TJR Tries…") instead of only neutralising them.
        # Sources with no channel (file uploads, watch folder) pass
        # nothing and keep the neutral rewrite. Best-effort: a failed
        # stamp must never fail task creation.
        if creator_name:
            try:
                from ..auto_director.title_person import clean_creator_name

                cleaned_creator = clean_creator_name(creator_name)
                if cleaned_creator:
                    await self.task_repo.save_source_metadata(
                        self.db, task_id, {"creator_name": cleaned_creator}
                    )
            except Exception:
                logger.warning(
                    "Could not store creator name on task %s", task_id, exc_info=True
                )

        # Recipe provenance: the task records which saved bundle its
        # choices came from, on the task's source_metadata alongside
        # the creator stamp (``save_source_metadata`` merges). A
        # task created without a recipe stamps nothing. Best-effort
        # like the creator stamp: a failed write must never fail task
        # creation. Watch-folder autopilot tasks pass their recipe
        # through here too (see
        # ``watch_folder_service.create_task_from_watch_file``).
        if recipe_id:
            try:
                await self.task_repo.save_source_metadata(
                    self.db, task_id, {"recipe_id": recipe_id}
                )
            except Exception:
                logger.warning(
                    "Could not store recipe id on task %s", task_id, exc_info=True
                )

        logger.info(f"Created task {task_id} for user {user_id}")
        return task_id

    async def _load_performance_context(self, user_id: Optional[str]) -> Optional[str]:
        """Build the creator's performance summary for the AI prompt (best effort)."""
        if not user_id:
            return None
        try:
            from ..database import AsyncSessionLocal
            from .social_service import SocialService

            # A short-lived session of its own: a failed lookup (e.g. social
            # tables not migrated yet when the worker deploys first) must not
            # leave the task's transaction aborted.
            async with AsyncSessionLocal() as session:
                return await SocialService(session, self.config).build_performance_context(
                    user_id
                )
        except Exception as exc:  # never block processing
            logger.warning("Skipping performance context for user %s: %s", user_id, exc)
            return None

    def _processing_transaction(self, task_id: str):
        return task_edit_transaction(self.db, task_id, processing=True)

    def _task_run_guard(self, task_id: str):
        return task_run_guard(self.db, task_id)

    async def _qc_persisted_clip(
        self,
        *,
        task_id: str,
        clip_id: str,
        segment: Dict[str, Any],
        clip_niche_override: Optional[str],
        project_niche_override: Optional[str],
        output_format: str,
        output_short_side: Optional[int],
        music_override: Optional[str],
        music_mode: Optional[str] = None,
        template_mode: Optional[str] = None,
    ) -> None:
        """Run AI quality control over one freshly persisted clip.

        Stores the QC report on the clip row. A hard failure (missing /
        corrupt file, wrong duration, wrong resolution, all-black,
        frozen-and-silent) earns exactly one re-render through the Lane-5
        path — same niche, same music pick — and a second QC pass; the
        same single corrective re-render is earned by a mechanically
        fixable compliance failure (expected music missing, a caption
        chunk over the pacing cap). The clip ships either way, flagged
        if it is still bad. QC must never fail the task: unexpected QC
        errors land on the clip as qc_status "error" and processing
        continues. Cancellation still propagates.
        """
        try:
            await self._qc_persisted_clip_inner(
                task_id=task_id,
                clip_id=clip_id,
                segment=segment,
                clip_niche_override=clip_niche_override,
                project_niche_override=project_niche_override,
                output_format=output_format,
                output_short_side=output_short_side,
                music_override=music_override,
                music_mode=music_mode,
                template_mode=template_mode,
            )
        except TaskCancelled:
            raise
        except Exception:
            logger.exception(
                "Clip QC crashed for clip %s of task %s; shipping the clip "
                "with qc_status=error",
                clip_id,
                task_id,
            )
            try:
                await self.clip_repo.update_clip_qc(
                    self.db,
                    clip_id,
                    "error",
                    {
                        "version": 1,
                        "status": "error",
                        "hard_fail": False,
                        "checks": [],
                        "detail": "QC raised an unexpected error; see worker logs",
                    },
                )
            except Exception:
                logger.exception(
                    "Could not store the QC error state for clip %s", clip_id
                )

    async def _compliance_persisted_clip(
        self,
        *,
        task_id: str,
        clip_id: str,
        segment: Dict[str, Any],
        content_brief: Optional[Dict[str, Any]],
        transcript: Optional[str],
    ) -> None:
        """Post-render campaign-brief compliance check for one clip.

        Sibling of clip QC, run after it (so it judges the file that
        actually ships, including after a QC corrective re-render). It
        judges RENDER FACTS: the source ranges from the clip's source-map
        sidecar, the generated caption text from its render facts, the
        recorded hook outcome on its plan, and its title copy. There is
        no corrective re-render for a brief failure — the content is
        what it is — and the check never fails the task: its own errors
        land on the clip as compliance_status "uncertain" with a note.
        """
        if not content_brief:
            return
        try:
            from ..ai import _parse_transcript_lines
            from ..auto_director.content_requirements import (
                evaluate_clip_compliance,
            )
            from ..clip_source_map import load_clip_source_ranges

            clip = await self.clip_repo.get_clip_by_id(self.db, clip_id)
            if not clip:
                return
            local_path = await run_in_thread(
                MediaStorage().local_path, clip["file_path"]
            )
            actual_ranges = await run_in_thread(
                load_clip_source_ranges, local_path
            )
            if actual_ranges is None:
                # No sidecar: fall back to the clip's stored bounds so the
                # check still runs, flagged in the report.
                actual_ranges = [
                    (
                        parse_timestamp_to_seconds(str(clip.get("start_time") or "0")),
                        parse_timestamp_to_seconds(str(clip.get("end_time") or "0")),
                    )
                ]
                sidecar_missing = True
            else:
                sidecar_missing = False
            facts = await run_in_thread(load_clip_render_facts, local_path)
            chunks = facts.get("caption_chunks") if isinstance(facts, dict) else None
            caption_text: Optional[str] = None
            if isinstance(chunks, list) and chunks:
                texts = [
                    str(chunk.get("text") or "")
                    for chunk in chunks
                    if isinstance(chunk, dict)
                ]
                joined = " ".join(t for t in texts if t).strip()
                caption_text = joined or None
            plan: Dict[str, Any] = {}
            raw_plan = clip.get("clip_plan")
            if isinstance(raw_plan, str) and raw_plan.strip():
                try:
                    plan = json.loads(raw_plan)
                except ValueError:
                    plan = {}
            elif isinstance(raw_plan, dict):
                plan = raw_plan
            hook_facts = plan.get("hook_application") if isinstance(plan, dict) else None
            selection = segment.get("brief_verdict") or {}
            selection_judgements = {}
            for entry in selection.get("requirements") or []:
                llm_info = entry.get("llm") if isinstance(entry, dict) else None
                if llm_info:
                    selection_judgements[entry["id"]] = llm_info
            report = evaluate_clip_compliance(
                content_brief,
                [(float(s), float(e)) for s, e in actual_ranges],
                _parse_transcript_lines(transcript or ""),
                caption_text=caption_text,
                title_copy=clip.get("hook_title") or None,
                hook_facts=hook_facts if isinstance(hook_facts, dict) else None,
                selection_judgements=selection_judgements or None,
                llm_available=bool(selection_judgements),
            )
            if sidecar_missing:
                report["source_ranges_source"] = "clip_row_fallback"
            await self.clip_repo.update_clip_compliance(
                self.db, clip_id, report["status"], report
            )
        except Exception:
            logger.exception(
                "Brief compliance check crashed for clip %s of task %s; "
                "marking the clip uncertain",
                clip_id,
                task_id,
            )
            try:
                await self.clip_repo.update_clip_compliance(
                    self.db,
                    clip_id,
                    "uncertain",
                    {
                        "version": 1,
                        "status": "uncertain",
                        "detail": "compliance check raised an unexpected error",
                        "requirements": [],
                    },
                )
            except Exception:
                logger.exception(
                    "Could not store the compliance error state for clip %s", clip_id
                )

    async def _qc_persisted_clip_inner(
        self,
        *,
        task_id: str,
        clip_id: str,
        segment: Dict[str, Any],
        clip_niche_override: Optional[str],
        project_niche_override: Optional[str],
        output_format: str,
        output_short_side: Optional[int],
        music_override: Optional[str],
        music_mode: Optional[str] = None,
        template_mode: Optional[str] = None,
    ) -> None:
        clip = await self.clip_repo.get_clip_by_id(self.db, clip_id)
        if not clip:
            return
        effective_niche = resolve_effective_profile(
            clip_niche_override,
            project_niche_override,
            segment.get("niche")
            or segment.get("niche_profile")
            or segment.get("classifier_niche"),
        )
        # Music expectation, mirroring how the render itself decided —
        # the shared precedence in music_library: an explicit user pick,
        # then the profile's bed, then the Auto bed a silent profile
        # gets on the Auto path.
        profile: Optional[Dict[str, Any]] = None
        try:
            profile = load_profile(effective_niche)
        except Exception:
            logger.warning(
                "QC could not load profile %s for the music expectation",
                effective_niche,
                exc_info=True,
            )
        from ..media.music_library import music_expectation

        # Plain — just cuts renders carry no music by design: judge them
        # against the Music Off expectation so a correct plain clip is
        # never flagged "music missing" (a fixable verdict that would
        # burn a corrective re-render chasing a bed Plain forbids).
        music_expected, music_label, _music_source = music_expectation(
            profile,
            music_override,
            effective_niche,
            music_mode="off" if template_mode == "plain" else music_mode,
        )
        compliance = self._compliance_inputs(clip, profile)

        sibling_rows = await self.clip_repo.get_clips_by_task(self.db, task_id)
        context = self._build_qc_context(
            clip,
            sibling_rows,
            output_format=output_format,
            output_short_side=output_short_side,
            music_expected=music_expected,
            music_label=music_label,
            **compliance,
        )
        local_path = await run_in_thread(
            MediaStorage().local_path, clip["file_path"]
        )
        # The reframe shot plan travels in the clip's source-map sidecar;
        # QC's subject-in-frame check reads the modes from the context.
        context.reframe_shots = await run_in_thread(
            load_clip_reframe, local_path
        )
        await self._attach_render_facts(context, local_path)
        report = await run_in_thread(run_clip_qc, local_path, context)
        report["attempts"] = 1
        await self.clip_repo.update_clip_qc(
            self.db, clip_id, report["status"], report
        )
        self._log_qc_result(task_id, clip, report)
        if not report.get("hard_fail") and not report.get("fixable"):
            return

        logger.warning(
            "Clip %s of task %s failed QC (%s) or carries a fixable "
            "compliance failure (%s); re-rendering once as %s",
            clip_id,
            task_id,
            ", ".join(
                entry["name"]
                for entry in report.get("checks", [])
                if entry.get("status") == "fail"
            ),
            ", ".join(report.get("fixable_reasons") or []),
            effective_niche,
        )
        try:
            # The edit-transaction guard refuses user edits while a task is
            # processing; the pipeline itself owns this task right now, so
            # enter the processing variant and let the Lane-5 re-render run
            # inside it (its own guard nests on the same task).
            async with task_edit_transaction(self.db, task_id, processing=True):
                await self.rerender_clip(
                    task_id,
                    clip_id,
                    effective_niche,
                    music_override=music_override,
                    music_mode=music_mode,
                )
        except TaskCancelled:
            raise
        except Exception:
            logger.exception(
                "QC re-render failed for clip %s of task %s; keeping the "
                "flagged original render",
                clip_id,
                task_id,
            )
            report = {**report, "retry": {"attempted": True, "succeeded": False}}
            await self.clip_repo.update_clip_qc(
                self.db, clip_id, report["status"], report
            )
            return

        refreshed = await self.clip_repo.get_clip_by_id(self.db, clip_id)
        if not refreshed:
            return
        context = self._build_qc_context(
            refreshed,
            sibling_rows,
            output_format=output_format,
            output_short_side=output_short_side,
            music_expected=music_expected,
            music_label=music_label,
            **self._compliance_inputs(refreshed, profile),
        )
        local_path = await run_in_thread(
            MediaStorage().local_path, refreshed["file_path"]
        )
        context.reframe_shots = await run_in_thread(
            load_clip_reframe, local_path
        )
        await self._attach_render_facts(context, local_path)
        report = await run_in_thread(run_clip_qc, local_path, context)
        report["attempts"] = 2
        report["retry"] = {"attempted": True, "succeeded": True}
        await self.clip_repo.update_clip_qc(
            self.db, clip_id, report["status"], report
        )
        self._log_qc_result(task_id, refreshed, report)

    async def _qc_edited_clip(
        self,
        *,
        task_id: str,
        clip_id: str,
        niche: Optional[str],
        music_override: Optional[str],
        output_format: str,
        output_short_side: Optional[int],
        music_mode: Optional[str] = None,
    ) -> None:
        """QC one user-edited clip, WITHOUT the pipeline's auto re-render.

        Same context construction and storage as ``_qc_persisted_clip`` —
        a clip the user just edited gets the same verdict trail — but a
        hard failure is only flagged: re-rendering behind the user's back
        would discard the edit they just made. Never raises; unexpected
        errors land on the clip as qc_status "error", like the pipeline
        wrapper.
        """
        from ..auto_director.edl import load_profile

        try:
            clip = await self.clip_repo.get_clip_by_id(self.db, clip_id)
            if not clip:
                return
            task = await self.task_repo.get_task_by_id(self.db, task_id)
            effective_music = (
                music_override
                if music_override is not None
                else (task or {}).get("music_override")
            )
            # The task's stored mode applies unless the caller states one
            # (the edit path passes its own clip-scoped decision through).
            effective_music_mode = (
                music_mode
                if music_mode is not None
                else (task or {}).get("music_mode")
            )
            profile: Optional[Dict[str, Any]] = None
            try:
                profile = load_profile(niche)
            except Exception:
                logger.warning(
                    "Edited-clip QC could not load profile %s for the "
                    "music expectation",
                    niche,
                    exc_info=True,
                )
            from ..media.music_library import music_expectation

            # A Plain task's edited clips carry no music by design (see
            # _qc_persisted_clip_inner): same Music Off expectation.
            if (task or {}).get("template_mode") == "plain":
                effective_music_mode = "off"
            music_expected, music_label, _music_source = music_expectation(
                profile, effective_music, niche, music_mode=effective_music_mode
            )
            sibling_rows = await self.clip_repo.get_clips_by_task(
                self.db, task_id
            )
            context = self._build_qc_context(
                clip,
                sibling_rows,
                output_format=output_format,
                output_short_side=output_short_side,
                music_expected=music_expected,
                music_label=music_label,
                **self._compliance_inputs(clip, profile),
            )
            local_path = await run_in_thread(
                MediaStorage().local_path, clip["file_path"]
            )
            await self._attach_render_facts(context, local_path)
            report = await run_in_thread(run_clip_qc, local_path, context)
            report["attempts"] = 1
            report["source"] = "editor"
            await self.clip_repo.update_clip_qc(
                self.db, clip_id, report["status"], report
            )
            self._log_qc_result(task_id, clip, report)
            # Brief compliance re-check, flag-only like the QC above. The
            # clip's stored text is the rendered ranges' transcript text
            # (kept in sync by the render), so it serves as the slice.
            task_row = task or {}
            edited_brief = task_row.get("content_brief")
            if edited_brief:
                start_s = parse_timestamp_to_seconds(str(clip.get("start_time") or "0"))
                end_s = parse_timestamp_to_seconds(str(clip.get("end_time") or "0"))
                synthetic = (
                    f"[{int(start_s // 60):02d}:{int(start_s % 60):02d} - "
                    f"{int(end_s // 60):02d}:{int(end_s % 60):02d}] "
                    f"{clip.get('text') or ''}"
                )
                await self._compliance_persisted_clip(
                    task_id=task_id,
                    clip_id=clip_id,
                    segment={},
                    content_brief=edited_brief,
                    transcript=synthetic,
                )
        except Exception:
            logger.exception(
                "Edited-clip QC crashed for clip %s of task %s; flagging "
                "qc_status=error",
                clip_id,
                task_id,
            )
            try:
                await self.clip_repo.update_clip_qc(
                    self.db,
                    clip_id,
                    "error",
                    {
                        "version": 1,
                        "status": "error",
                        "hard_fail": False,
                        "checks": [],
                        "detail": "QC raised an unexpected error; see worker logs",
                        "source": "editor",
                    },
                )
            except Exception:
                logger.exception(
                    "Could not store the QC error state for clip %s", clip_id
                )

    @staticmethod
    def _compliance_inputs(
        clip: Dict[str, Any], profile: Optional[Dict[str, Any]]
    ) -> Dict[str, Any]:
        """Quality Director compliance inputs for one clip's QC context.

        The hook outcome comes from the clip's stored plan (the hook
        enforcement record); the cut-rate band and caption hold cap come
        from the effective profile. Anything missing simply leaves the
        matching QC check skipped.
        """
        inputs: Dict[str, Any] = {
            "hook_outcome": None,
            "cut_rate_band": None,
            "caption_hold_cap_seconds": None,
        }
        raw_plan = clip.get("clip_plan") if clip else None
        if raw_plan:
            try:
                plan = (
                    json.loads(raw_plan)
                    if isinstance(raw_plan, str)
                    else raw_plan
                )
            except (TypeError, ValueError):
                plan = None
            if isinstance(plan, dict):
                outcome = plan.get("hook_application")
                if isinstance(outcome, dict):
                    inputs["hook_outcome"] = outcome
        if profile:
            try:
                band = (profile.get("pacing") or {}).get(
                    "target_cuts_per_min"
                )
                if band and len(band) >= 2:
                    inputs["cut_rate_band"] = (float(band[0]), float(band[1]))
            except (TypeError, ValueError):
                inputs["cut_rate_band"] = None
            try:
                from ..media.captions import caption_hold_cap_for_profile

                inputs["caption_hold_cap_seconds"] = (
                    caption_hold_cap_for_profile(profile)
                )
            except Exception:
                inputs["caption_hold_cap_seconds"] = None
        return inputs

    @staticmethod
    async def _attach_render_facts(
        context: ClipQcContext, local_path: Path
    ) -> None:
        """Load the clip's render facts (segment count, caption timings).

        The cap the render actually used wins over the profile-derived
        one — QC judges the render against its own settings. Also sets
        the loudness target (CLIP_QC_LOUDNESS_LUFS) and the words around
        the clip's boundaries the render stored (CLIP_QC_THOUGHT_BOUNDARIES);
        both checks are warn-only and never trigger a re-render.
        """
        from ..media.clip_qc import qc_loudness_target, qc_thought_boundaries_enabled

        context.loudness_target_lufs = qc_loudness_target()
        try:
            facts = await run_in_thread(load_clip_render_facts, local_path)
        except Exception:
            return
        if not facts:
            return
        words = facts.get("boundary_words")
        if isinstance(words, list) and words and qc_thought_boundaries_enabled():
            context.transcript_words = words
        count = facts.get("segment_count")
        if isinstance(count, int) and count > 0:
            context.rendered_segment_count = count
        chunks = facts.get("caption_chunks")
        if isinstance(chunks, list):
            context.caption_timings = chunks
        cap = facts.get("caption_hold_cap_seconds")
        if isinstance(cap, (int, float)) and cap > 0:
            context.caption_hold_cap_seconds = float(cap)
        density = facts.get("beat_density")
        if isinstance(density, dict) and density:
            context.beat_density = density

    @staticmethod
    def _build_qc_context(
        clip: Dict[str, Any],
        sibling_rows: Any,
        *,
        output_format: str,
        output_short_side: Optional[int],
        music_expected: bool,
        music_label: Optional[str],
        hook_outcome: Optional[Dict[str, Any]] = None,
        cut_rate_band: Optional[tuple] = None,
        caption_hold_cap_seconds: Optional[float] = None,
    ) -> ClipQcContext:
        def _seconds(value: Any) -> Optional[float]:
            if value is None or value == "":
                return None
            return parse_timestamp_to_seconds(str(value))

        siblings = []
        for row in sibling_rows or []:
            if row.get("id") == clip.get("id"):
                continue
            sha256 = None
            raw_report = row.get("qc_report")
            if raw_report:
                try:
                    parsed = (
                        json.loads(raw_report)
                        if isinstance(raw_report, str)
                        else raw_report
                    )
                    sha256 = (parsed.get("file") or {}).get("sha256")
                except (ValueError, AttributeError):
                    sha256 = None
            siblings.append(
                {
                    "start": _seconds(row.get("start_time")),
                    "end": _seconds(row.get("end_time")),
                    "sha256": sha256,
                    "label": f"clip {row.get('clip_order')}",
                }
            )
        return ClipQcContext(
            expected_duration=clip.get("duration"),
            output_format=output_format,
            max_short_side=output_short_side,
            source_start=_seconds(clip.get("start_time")),
            source_end=_seconds(clip.get("end_time")),
            music_expected=music_expected,
            music_label=music_label,
            siblings=tuple(siblings),
            hook_outcome=hook_outcome,
            cut_rate_band=cut_rate_band,
            caption_hold_cap_seconds=caption_hold_cap_seconds,
        )

    @staticmethod
    def _log_qc_result(
        task_id: str, clip: Dict[str, Any], report: Dict[str, Any]
    ) -> None:
        """Leave a per-clip QC trail in the worker log next to the report."""
        flagged = [
            entry
            for entry in report.get("checks", [])
            if entry.get("status") in {"warn", "fail"}
        ]
        if not flagged:
            logger.info(
                "QC pass for clip %s of task %s", clip.get("id"), task_id
            )
            return
        for entry in flagged:
            log = logger.error if entry.get("status") == "fail" else logger.warning
            log(
                "QC %s for clip %s of task %s [%s]: %s",
                entry.get("status"),
                clip.get("id"),
                task_id,
                entry.get("name"),
                entry.get("detail"),
            )

    async def _seed_stream_source(self, task_id: str) -> None:
        """Reuse the preflight's length and any cheap actor result in the worker."""
        try:
            task = await self.task_repo.get_task_by_id(self.db, task_id) or {}
            metadata = await self.task_repo.load_source_metadata(self.db, task_id) or {}
            await run_in_thread(
                seed_stream_resolution,
                task_id,
                metadata.get("stream_resolution"),
                task.get("source_duration_seconds"),
            )
        except Exception as exc:  # the worker can still resolve everything itself
            logger.warning("Could not seed stream source details: %s", exc)

    @exclusive_task_run
    @media_work
    async def process_task(
        self,
        task_id: str,
        url: str,
        source_type: str,
        user_id: Optional[str] = None,
        font_family: Optional[str] = None,
        font_size: Optional[int] = None,
        font_color: Optional[str] = None,
        caption_template: str = "default",
        processing_mode: str = "fast",
        output_format: str = "vertical",
        add_subtitles: bool = True,
        progress_callback: Optional[Callable] = None,
        should_cancel: Optional[Callable] = None,
        clip_ready_callback: Optional[Callable] = None,
        cleanup_settings: Optional[Dict[str, Any]] = None,
        clip_length: str = "auto",
    ) -> Dict[str, Any]:
        """
        Process a task: download video, analyze, create clips.
        Returns processing results.
        """
        # Tracked so the downloaded source video can be cleaned up on both the
        # success and error paths; multi-GB YouTube downloads otherwise pile up
        # in temp/ until the disk fills and later tasks fail.
        video_path: Optional[Path] = None
        pending_clip_path: Optional[Path] = None
        clips_output_dir = Path(self.config.temp_dir) / "clips"
        # Concurrent clip renders in flight, and the files they produced that
        # were committed to the DB. The finally block cancels the former and
        # removes any rendered file that was never persisted.
        pending_render_tasks: Dict[int, asyncio.Task] = {}
        persisted_clip_paths: set = set()

        async def check_cancelled():
            if should_cancel and await should_cancel():
                raise TaskCancelled()

        try:
            await check_cancelled()
            logger.info(f"Starting processing for task {task_id}")
            # New tasks already have an atomic acceptance charge. Recover queued
            # legacy jobs with missing durations before doing paid processing.
            if self.config.monetization_enabled and user_id:
                async with generation_allowance_guard(self.db, user_id, enabled=True):
                    await BillingService(self.db, self.config).assert_can_resume_task(
                        user_id, task_id, require_active_plan=False
                    )
            started_at = datetime.utcnow()
            stage_timings: Dict[str, float] = {}
            billing = (
                await BillingService(self.db, self.config).get_usage_summary(user_id)
                if user_id
                else {}
            )
            # Reduced-resolution plans download at their output quality and get
            # a final downscale; 1080p plans keep the renderer's output.
            download_quality, output_short_side = reduced_media_limits(billing)
            cache_key = self._build_cache_key(
                url, source_type, processing_mode, download_quality
            )
            # The clip-count target lives on the task row (validated at
            # creation). Read it once here: the analysis cache key and the
            # pipeline both depend on it. A malformed stored value falls
            # back to Auto rather than failing the task.
            target_clip_count = None
            settings_row = (
                await self.task_repo.get_task_by_id(self.db, task_id) or {}
            )
            try:
                target_clip_count = validate_target_clip_count(
                    settings_row.get("target_clip_count")
                )
            except ValueError:
                logger.warning(
                    "Ignoring invalid stored target_clip_count %r on task %s",
                    settings_row.get("target_clip_count"),
                    task_id,
                )
            # Separation opt-in lives on the task row too (validated at
            # creation against the deployment flag). Anything malformed
            # reads as off — the original-audio path.
            separation_opt_in = bool(settings_row.get("separation_opt_in"))
            # The campaign brief lives on the task row too (validated at
            # creation, immutable for the task's life — that is what makes
            # checkpoint reuse safe). A malformed stored value is ignored
            # loudly rather than enforced wrongly.
            content_brief = None
            try:
                from ..auto_director.content_requirements import (
                    brief_fingerprint,
                    normalize_brief_v2,
                )

                content_brief = normalize_brief_v2(settings_row.get("content_brief"))
            except (ValueError, TypeError):
                logger.warning(
                    "Ignoring invalid stored content_brief on task %s", task_id
                )
                content_brief = None
            # The creation mode lives on the same row (validated at
            # creation). Malformed stored values fall back to no mode —
            # the legacy pipeline — rather than failing the task.
            creation_mode = None
            manual_ranges = None
            try:
                creation_mode = validate_creation_mode(
                    settings_row.get("creation_mode")
                )
            except ValueError:
                logger.warning(
                    "Ignoring invalid stored creation_mode %r on task %s",
                    settings_row.get("creation_mode"),
                    task_id,
                )
            if creation_mode:
                try:
                    stored_ranges = settings_row.get("manual_ranges_json")
                    manual_ranges = validate_manual_ranges(
                        json.loads(stored_ranges) if stored_ranges else None
                    )
                except (ValueError, json.JSONDecodeError):
                    logger.warning(
                        "Ignoring invalid stored manual ranges on task %s",
                        task_id,
                    )
                    manual_ranges = None
            mode_plan = plan_for_mode(creation_mode)
            analysis_cache_key = self._build_analysis_cache_key(
                cache_key,
                clip_length,
                target_clip_count,
                brief_fingerprint(content_brief) if content_brief else None,
                selection=mode_plan.selection,
            )

            cache_entry = await self.cache_repo.get_cache(self.db, cache_key)
            cached_transcript = (
                cache_entry.get("transcript_text") if cache_entry else None
            )
            analysis_entry = (
                cache_entry
                if analysis_cache_key == cache_key
                else await self.cache_repo.get_cache(self.db, analysis_cache_key)
            )
            cached_analysis_json = (
                analysis_entry.get("analysis_json") if analysis_entry else None
            )
            # F-1: never hand a Manual Cutter analysis to an AI run.
            # Key separation keeps new manual analyses out of the AI
            # row, but rows poisoned before the fix still hold one —
            # and a run that reuses it would write it straight back,
            # so the poison would never heal. Discard it here (the
            # pipeline re-checks at consumption too); discarding here
            # also keeps the task's cache_hit flag honest.
            if (
                cached_analysis_json
                and mode_plan.selection != SELECTION_USER_RANGES
                and cached_analysis_is_user_selected(cached_analysis_json)
            ):
                logger.warning(
                    "Ignoring cached Manual Cutter analysis for a %s task %s",
                    mode_plan.mode,
                    task_id,
                )
                cached_analysis_json = None
            # F-R2: never serve a degraded stand-in as AI analysis.
            # Rows poisoned before the write-path fix still hold one —
            # a run that reused it would write it straight back, so
            # the poison would never heal. Discard it here (the next
            # healthy run overwrites the row with a real analysis);
            # discarding here also keeps the task's cache_hit flag
            # honest. This applies to every mode: no mode legitimately
            # consumes the floor slate as an analysis.
            if cached_analysis_json and cached_analysis_is_degraded(
                cached_analysis_json
            ):
                logger.warning(
                    "Ignoring cached degraded analysis stand-in for a %s task %s",
                    mode_plan.mode,
                    task_id,
                )
                cached_analysis_json = None
            # Performance loop: feed this creator's real audience data back into
            # clip selection. Personalized analyses bypass the shared analysis
            # cache so one creator's history never shapes another's clips.
            performance_context = await self._load_performance_context(user_id)
            if performance_context:
                cached_analysis_json = None
            input_key = checkpoint_input_key(url, source_type, processing_mode, clip_length, self.config)
            checkpoint = await self.checkpoint_repo.get(self.db, task_id, input_key)
            cache_hit = bool(
                (checkpoint.get("transcript") or cached_transcript)
                and (checkpoint.get("analysis") or cached_analysis_json)
            )

            async def save_checkpoint(patch: Dict[str, Any]) -> None:
                await self.checkpoint_repo.save(self.db, task_id, input_key, patch)
                checkpoint.update(patch)

            await self.task_repo.update_task_runtime_metadata(
                self.db,
                task_id,
                started_at=started_at,
                cache_hit=cache_hit,
            )

            # Update status to processing
            started = await self.task_repo.update_task_status(
                self.db, task_id, "processing", progress=0,
                progress_message="Starting...",
                expected_statuses=["queued", "processing"],
            )
            if started is False:
                raise TaskCancelled()

            # Progress callback wrapper
            async def update_progress(
                progress: int, message: str, status: str = "processing"
            ):
                await check_cancelled()
                changed = await self.task_repo.update_task_status(
                    self.db, task_id, status, progress=progress,
                    progress_message=message, expected_statuses=["processing"],
                )
                if changed is False:
                    raise TaskCancelled()
                if progress_callback:
                    await progress_callback(progress, message, status)

            # Process video with progress updates
            max_video_duration = self.config.max_video_duration
            if source_type in {"youtube", "twitch", "kick"} and user_id:
                max_video_duration = self.config.max_youtube_video_duration_for_plan(
                    billing.get("plan"),
                    billing.get("subscription_status"),
                    billing.get("subscription_provider"),
                )

            if source_type in {"twitch", "kick"}:
                await self._seed_stream_source(task_id)

            # Learning engine: resolve the user's current adjustment
            # document once per task. When the task's niche is pinned up
            # front, its selection weights are computed here; on auto
            # tasks the document travels into the pipeline and the
            # weights are resolved at analysis time, once classification
            # has produced a niche. The document is reused below for
            # per-clip EDL adjustments. The resolver never raises and
            # returns None when disabled or empty, and None means base
            # behaviour everywhere downstream.
            learning_document = None
            learning_score_weights = None
            if user_id:
                from ..auto_director.learning import score_weights_for
                from .learning_service import resolve_learning_document

                learning_document = await resolve_learning_document(
                    self.db, user_id
                )
                if learning_document:
                    pinned_row = (
                        await self.task_repo.get_task_by_id(self.db, task_id)
                        or {}
                    )
                    pinned_niche = pinned_row.get("niche_override")
                    if pinned_niche:
                        learning_score_weights = score_weights_for(
                            learning_document, pinned_niche
                        )

            # Creator name for hook-title attribution (title_person):
            # captured on the task's source_metadata at creation from the
            # source's channel/uploader name. Absent (uploads, older
            # tasks) -> titles take the neutral rewrite. Best-effort.
            creator_name = None
            try:
                from ..auto_director.title_person import (
                    creator_name_from_metadata,
                )

                creator_name = creator_name_from_metadata(
                    await self.task_repo.load_source_metadata(self.db, task_id)
                )
            except Exception:
                logger.warning(
                    "Could not load creator name for task %s",
                    task_id,
                    exc_info=True,
                )

            pipeline_start = perf_counter()
            # Attribute this pipeline's LLM usage to the task for the
            # provider manager's per-task token accounting.
            usage_scope = provider_manager.set_task_context(task_id)
            result = await self.video_service.process_video_complete(
                url=url,
                source_type=source_type,
                task_id=task_id,
                font_family=font_family,
                font_size=font_size,
                font_color=font_color,
                caption_template=caption_template,
                processing_mode=processing_mode,
                output_format=output_format,
                add_subtitles=add_subtitles,
                max_video_duration=max_video_duration,
                download_quality=download_quality,
                cached_transcript=cached_transcript,
                cached_analysis_json=cached_analysis_json,
                progress_callback=update_progress,
                should_cancel=should_cancel,
                performance_context=performance_context,
                clip_length=clip_length,
                target_clip_count=target_clip_count,
                creation_mode=creation_mode,
                manual_ranges=manual_ranges,
                score_weights=learning_score_weights,
                learning_document=learning_document,
                separation_opt_in=separation_opt_in,
                checkpoint=checkpoint,
                checkpoint_callback=save_checkpoint,
                source_title=settings_row.get("source_title"),
                project_niche_override=settings_row.get("niche_override"),
                content_brief=content_brief,
                creator_name=creator_name,
            )
            _classification = result.get("classification")
            if _classification:
                # Persist the classification outcome on the task row:
                # niche, confidence, is_fallback, the model that
                # answered, the last error and the per-candidate
                # attempts. A fallback is then visible on the task
                # itself instead of existing only in this log.
                try:
                    await self.task_repo.update_classification(
                        self.db, task_id, _classification
                    )
                except Exception:
                    logger.warning(
                        "Could not persist classification for task %s",
                        task_id,
                        exc_info=True,
                    )
                if _classification.get("is_fallback"):
                    logger.warning(
                        "Auto niche detection FAILED — default Podcast & news profile used "
                        "(task %s, evidence %s, last error: %s)",
                        task_id,
                        _classification.get("evidence_status"),
                        _classification.get("last_error"),
                    )
                else:
                    logger.info(
                        "Task %s: classified niche %s (confidence %s, "
                        "evidence %s)",
                        task_id,
                        _classification.get("niche"),
                        _classification.get("confidence"),
                        _classification.get("evidence_status"),
                    )
            provider_manager.reset_task_context(usage_scope)
            if content_brief and result.get("brief_report"):
                try:
                    await self.task_repo.update_brief_report(
                        self.db, task_id, result["brief_report"]
                    )
                except Exception:
                    logger.warning(
                        "Could not persist brief_report for task %s",
                        task_id,
                        exc_info=True,
                    )
            # The pipeline's own per-stage timings (download, transcription,
            # analysis) join the task-level keys below; all of them persist
            # together in stage_timings_json when the task completes.
            for stage_name, stage_seconds in (
                result.get("stage_timings") or {}
            ).items():
                stage_timings[stage_name] = stage_seconds
            stage_timings["pipeline_seconds"] = round(
                perf_counter() - pipeline_start, 3
            )
            video_path = Path(result["video_path"])

            if result.get("awaiting_selection"):
                # Find-Moments workflows park here by design: moments
                # are found and checkpointed, nothing renders until
                # the user picks (select-moments endpoint). This is a
                # successful stop, not a failure — no error state, no
                # completion email, clips stay at zero.
                candidates = result.get("candidates") or []
                message = (
                    f"Found {len(candidates)} moments — "
                    "pick the ones to render"
                )
                parked = await self.task_repo.update_task_status(
                    self.db,
                    task_id,
                    AWAITING_SELECTION_STATUS,
                    progress=55,
                    progress_message=message,
                    expected_statuses=["processing"],
                )
                if parked is False:
                    raise TaskCancelled()
                await self.task_repo.update_task_runtime_metadata(
                    self.db,
                    task_id,
                    stage_timings_json=json.dumps(stage_timings),
                )
                # Stamp the park moment: the auto-pick sweep measures
                # the wait from here (falling back to the row's
                # updated_at for tasks parked before this stamp
                # existed). Best-effort — parking must never fail
                # because a stamp could not be written.
                try:
                    await self.task_repo.save_source_metadata(
                        self.db,
                        task_id,
                        {
                            "selection_parked_at": datetime.now(
                                timezone.utc
                            ).isoformat()
                        },
                    )
                except Exception:
                    logger.warning(
                        "Could not stamp the park time for task %s",
                        task_id,
                        exc_info=True,
                    )
                if progress_callback:
                    await progress_callback(
                        55, message, AWAITING_SELECTION_STATUS
                    )
                logger.info(
                    "Task %s parked for moment selection with %d "
                    "candidates",
                    task_id,
                    len(candidates),
                )
                return {
                    "task_id": task_id,
                    "status": AWAITING_SELECTION_STATUS,
                    "candidates_count": len(candidates),
                    "segments": [],
                    "summary": result.get("summary"),
                    "key_topics": result.get("key_topics"),
                }

            normalized_cleanup_settings = normalize_clip_cleanup_settings(
                **(cleanup_settings or {})
            )

            # Render clips incrementally: render, save, notify one at a time
            segments_to_render = result.get("segments_to_render", [])
            if not segments_to_render:
                await self.cache_repo.upsert_cache(
                    self.db,
                    cache_key=cache_key,
                    source_url=url,
                    source_type=source_type,
                    video_path=MediaStorage().source_reference(url) if url.startswith("upload://s3/") else result.get("video_path"),
                    transcript_text=result.get("transcript"),
                    analysis_json=None,
                )
                if content_brief:
                    # Explicit no-match outcome: say what was searched,
                    # never pad with unrelated clips.
                    from ..auto_director.content_requirements import (
                        brief_summary_text,
                    )

                    report = result.get("brief_report") or {}
                    checked = report.get("candidates_total", 0)
                    raise NoCompliantMomentsError(
                        "No compliant moments found — no clips were made. "
                        f"The brief asked for {brief_summary_text(content_brief)}. "
                        f"{checked} moments were checked."
                    )
                raise ValueError(
                    "No usable clip segments were selected for this video."
                )

            # F-R2: a degraded run's stand-in is not an AI analysis
            # and must never be cached as one — the next run of the
            # same video with providers healthy would otherwise be
            # served the floor slate and never call the AI. The
            # transcript and video path still cache; only the
            # analysis write is suppressed, on both rows. Passing
            # None keeps whatever the row already holds (the
            # upsert's COALESCE): a healthy analysis cached earlier
            # is never clobbered by a degraded run, and a poisoned
            # row is healed when a later healthy run overwrites it.
            result_analysis_json = result.get("analysis_json")
            result_analysis_is_degraded = cached_analysis_is_degraded(
                result_analysis_json
            )
            if result_analysis_is_degraded:
                logger.warning(
                    "Not caching degraded analysis stand-in for task %s "
                    "(analysis cache key %s)",
                    task_id,
                    analysis_cache_key,
                )
            await self.cache_repo.upsert_cache(
                self.db,
                cache_key=cache_key,
                source_url=url,
                source_type=source_type,
                video_path=MediaStorage().source_reference(url) if url.startswith("upload://s3/") else result.get("video_path"),
                transcript_text=result.get("transcript"),
                analysis_json=(
                    result_analysis_json
                    if not performance_context
                    and analysis_cache_key == cache_key
                    and not result_analysis_is_degraded
                    else None
                ),
            )
            if (
                not performance_context
                and analysis_cache_key != cache_key
                and not result_analysis_is_degraded
            ):
                await self.cache_repo.upsert_cache(
                    self.db,
                    cache_key=analysis_cache_key,
                    source_url=url,
                    source_type=source_type,
                    analysis_json=result_analysis_json,
                )

            video_path = Path(result["video_path"])
            time_map = await run_in_thread(load_time_map, video_path)
            total_clips = len(segments_to_render)
            clips_output_dir = Path(self.config.temp_dir) / "clips"
            clips_output_dir.mkdir(parents=True, exist_ok=True)

            # Completed positions survive retries, including edits, exports and
            # scheduled posts attached to their IDs. Never delete usable output
            # before a later render has even succeeded.
            async with self._processing_transaction(task_id):
                await check_cancelled()
                existing_clips = await self.clip_repo.get_clips_by_task(self.db, task_id)
                clip_ids_by_order = {clip["clip_order"]: clip["id"] for clip in existing_clips}
                # Display order changes when a user splits, merges or reorders
                # clips. Track completed plan positions separately and commit
                # them atomically with each newly created clip.
                completed_positions = set(checkpoint.get("completed_positions", clip_ids_by_order))
                await save_checkpoint({"completed_positions": sorted(completed_positions)})
                await self.task_repo.update_task_clips(
                    self.db, task_id, [clip_ids_by_order[order] for order in sorted(clip_ids_by_order)]
                )

            failed_positions = []
            # Scrubbed later, at completion: why each failed position's
            # render raised, when it raised instead of returning None.
            render_failures: Dict[int, Optional[BaseException]] = {}
            render_start = perf_counter()
            # Read from the task so queued jobs and resumes honour the setting.
            task_row = await self.task_repo.get_task_by_id(self.db, task_id) or {}
            include_broll = bool(task_row.get("include_broll"))
            project_niche_override = task_row.get("niche_override")
            project_music_override = task_row.get("music_override")
            # The stored template/music choices are re-validated with
            # the same try/warn pattern as the target count above: the
            # API only ever stores values its validators accepted, but
            # a stored value that fails validation now (a corrupt row,
            # a future writer) must warn and fall back to the documented
            # default instead of silently steering the render.
            try:
                project_music_mode = validate_music_mode(
                    task_row.get("music_mode")
                )
            except ValueError:
                logger.warning(
                    "Ignoring invalid stored music_mode %r on task %s",
                    task_row.get("music_mode"),
                    task_id,
                )
                project_music_mode = None
            try:
                project_template_mode = validate_template_mode(
                    task_row.get("template_mode")
                )
            except ValueError:
                logger.warning(
                    "Ignoring invalid stored template_mode %r on task %s",
                    task_row.get("template_mode"),
                    task_id,
                )
                project_template_mode = None
            try:
                project_framing_mode = validate_framing_mode(
                    task_row.get("framing_mode")
                )
            except ValueError:
                logger.warning(
                    "Ignoring invalid stored framing_mode %r on task %s",
                    task_row.get("framing_mode"),
                    task_id,
                )
                project_framing_mode = None
            try:
                project_layout_template = validate_layout_template(
                    task_row.get("layout_template")
                )
            except ValueError:
                logger.warning(
                    "Ignoring invalid stored layout_template %r on task %s",
                    task_row.get("layout_template"),
                    task_id,
                )
                project_layout_template = None
            project_layout_credit = (
                clean_layout_credit(task_row.get("layout_credit"))
                if project_layout_template
                else None
            )
            project_broll_enabled = task_row.get("broll_enabled")
            # Title on/off: only an explicit False turns the burned-in title off.
            project_hook_title_enabled = task_row.get("hook_title_enabled") is not False
            # Brand Kit application for this task's renders: resolved
            # once from the opted-in kit, then threaded into every
            # clip's EDL build exactly like the music override. No kit
            # (or a kit deleted since) leaves every input untouched.
            brand_application: Dict[str, Any] = {}
            if task_row.get("brand_kit_id"):
                try:
                    task_kit = await self.brand_kit_repo.get_kit(
                        self.db,
                        task_row.get("user_id") or user_id or "",
                        task_row["brand_kit_id"],
                    )
                except Exception:
                    task_kit = None
                if task_kit:
                    from ..auto_director.brand_kit import resolve_brand_application

                    brand_application = resolve_brand_application(
                        task_kit,
                        user_id=task_row.get("user_id") or user_id,
                        task_font_color=font_color,
                        task_font_family=font_family,
                    )
            broll_session = BRollSession()
            # EDL-path counterpart of the legacy session: one ledger
            # per task, so the auto-director's stock resolution never
            # hands two clips of this task the same footage.
            from ..media.pixabay_broll import BrollUsageLedger

            broll_ledger = BrollUsageLedger()

            # Voice/music separation (Stage 2, render side): an opted-in
            # task's clips draw their audio from the source's vocals
            # stem, so the source's own background music never reaches
            # the export. The stem is resolved once here through the
            # shared resolution in media.separation — transcription
            # already separated this source, so this is normally a cache
            # hit — and the render loop just carries the path down. Any
            # failure (flag off, separator unavailable, stem missing)
            # leaves it None, the kwarg is never passed, and every clip
            # renders with the original audio exactly as before. The
            # re-render seam resolves the same way (see
            # ClipEditingMixin._resolve_render_vocals_stem).
            from ..media.separation import resolve_render_vocals_stem

            render_vocals_stem = await run_in_thread(
                resolve_render_vocals_stem, separation_opt_in, video_path
            )
            if render_vocals_stem is not None:
                logger.info(
                    "Task renders will draw audio from the vocals stem %s",
                    render_vocals_stem,
                )

            # Task text bands (media.text_bands): one title band + one
            # caption band decided ONCE for the whole task, so every
            # clip places its text at the same height — the framing
            # lane unified the bands per clip, and the MrBeast exports
            # still showed four title heights across one task because
            # each clip's guard decided alone. Resolved here, at the
            # same once-per-task seam as the Brand Kit application and
            # the B-roll session, and threaded into every clip.
            #
            # The decision runs only on a fresh render slate (no
            # existing clips, no stored decision): a task resumed with
            # clips already rendered keeps the placement those clips
            # used, and a task created before this feature never gains
            # bands at all. A stored decision is loaded verbatim and
            # NEVER re-decided — single-clip re-renders read the same
            # store and reuse it byte-identically. The fresh decision
            # is persisted BEFORE the render loop starts; a persistence
            # failure keeps the bands for this run in memory only. Any
            # failure anywhere in the pre-pass leaves task_text_bands
            # None and every clip renders with per-clip bands exactly
            # as before.
            task_text_bands: Optional[Dict[str, Any]] = None
            try:
                stored_metadata = (
                    await self.task_repo.load_source_metadata(self.db, task_id)
                    or {}
                )
                stored_bands = stored_metadata.get("text_bands")
                if isinstance(stored_bands, dict) and stored_bands:
                    task_text_bands = stored_bands
                elif not existing_clips and segments_to_render:
                    from ..auto_director.template_mode import is_plain
                    from ..caption_templates import get_template
                    from ..media.text_bands import decide_task_text_bands

                    if output_format == "vertical":
                        band_width, band_height = 1080, 1920
                    else:
                        from ..media.ffmpeg import ffprobe_video_size

                        band_width, band_height = await run_in_thread(
                            ffprobe_video_size, video_path
                        )
                    if band_width > 0 and band_height > 0:
                        # The caption preference the decision guards
                        # around: the task profile's named position
                        # (five profiles ask for centre captions, far
                        # from the template default), resolved once
                        # from the first segment's classification.
                        band_preference_y: Optional[float] = None
                        try:
                            from ..auto_director.clip_edits import (
                                POSITION_Y_BY_NAME,
                            )
                            from ..auto_director.edl import load_profile
                            from ..auto_director.niche import (
                                resolve_effective_profile,
                            )

                            first_segment = segments_to_render[0]
                            band_profile_name = resolve_effective_profile(
                                None,
                                project_niche_override,
                                first_segment.get("niche")
                                or first_segment.get("niche_profile")
                                or first_segment.get("classifier_niche"),
                            )
                            if band_profile_name:
                                band_profile = load_profile(band_profile_name)
                                band_preference_y = POSITION_Y_BY_NAME.get(
                                    (band_profile.get("captions") or {}).get(
                                        "position"
                                    )
                                )
                        except Exception:
                            band_preference_y = None
                        segment_ranges = []
                        for segment in segments_to_render:
                            localized_segment = (
                                localize_segment(segment, time_map)
                                if time_map
                                else segment
                            )
                            seg_start = localized_segment.get(
                                "start", segment.get("start")
                            )
                            seg_end = localized_segment.get(
                                "end", segment.get("end")
                            )
                            if seg_start is None or seg_end is None:
                                continue
                            segment_ranges.append(
                                (
                                    [(float(seg_start), float(seg_end))],
                                    segment.get("hook_title"),
                                )
                            )
                        decided_bands = await run_in_thread(
                            decide_task_text_bands,
                            video_path,
                            segment_ranges,
                            plain=is_plain(project_template_mode),
                            template=get_template(caption_template),
                            font_size=font_size,
                            video_width=band_width,
                            video_height=band_height,
                            preference_y=band_preference_y,
                        )
                        if decided_bands is not None:
                            task_text_bands = decided_bands
                            try:
                                await self.task_repo.save_source_metadata(
                                    self.db,
                                    task_id,
                                    {"text_bands": decided_bands},
                                )
                            except Exception:
                                logger.warning(
                                    "Could not persist text bands for task "
                                    "%s; this run still uses them in memory",
                                    task_id,
                                    exc_info=True,
                                )
            except Exception:
                logger.warning(
                    "Task text-band pre-pass failed for task %s; "
                    "rendering with per-clip bands",
                    task_id,
                    exc_info=True,
                )
                task_text_bands = None

            # Clips render concurrently, bounded by CLIP_RENDER_CONCURRENCY.
            # Rendering is thread-pool ffmpeg work, so positions overlap while
            # the persistence loop below stays sequential and in plan order:
            # clip IDs, ordering, checkpoints and SSE notifications are
            # unchanged. Render outcomes are (clip_info, niche_override) pairs.
            render_semaphore = asyncio.Semaphore(clip_render_concurrency())

            async def render_segment(
                position: int, segment: Dict[str, Any]
            ) -> tuple[Optional[Dict[str, Any]], Optional[str], Optional[BaseException]]:
                async with render_semaphore:
                    # A partial download is a concatenated timeline. Render against
                    # local timestamps, then store the clip against the source.
                    localized = localize_segment(segment, time_map) if time_map else segment
                    try:
                        niche_override = validate_niche_override(
                            segment.get("niche_override")
                        )
                    except ValueError:
                        logger.warning(
                            "Ignoring invalid niche_override on clip %s", position + 1
                        )
                        niche_override = None
                    classifier_niche = (
                        segment.get("niche")
                        or segment.get("niche_profile")
                        or segment.get("classifier_niche")
                    )
                    # Learned EDL adjustments for this clip's niche, when
                    # the niche is known from a real source (clip/project
                    # override or classifier output) — never for the
                    # fallback-resolved profile. build_pipeline_edl
                    # re-checks the niche against the resolved profile.
                    learning_niche = (
                        niche_override
                        or project_niche_override
                        or classifier_niche
                    )
                    segment_adjustments = None
                    if learning_document and learning_niche:
                        from ..auto_director.learning import (
                            profile_adjustment_for,
                        )

                        segment_adjustments = profile_adjustment_for(
                            learning_document, learning_niche
                        )
                    # Brief range enforcement at render (R1): allowed
                    # ranges are source-time; on a partial-download
                    # (time_map) path they are re-based onto the local
                    # timeline the render actually consumes.
                    render_allowed_ranges = None
                    render_excluded_ranges = None
                    if content_brief:
                        from ..auto_director.content_requirements import (
                            allowed_range_tuples,
                            excluded_range_tuples,
                            localize_allowed_ranges,
                        )

                        source_allowed = allowed_range_tuples(content_brief)
                        if source_allowed:
                            render_allowed_ranges = (
                                localize_allowed_ranges(source_allowed, time_map)
                                if time_map
                                else source_allowed
                            )
                        source_excluded = excluded_range_tuples(content_brief)
                        if source_excluded:
                            render_excluded_ranges = (
                                localize_allowed_ranges(source_excluded, time_map)
                                if time_map
                                else source_excluded
                            )
                    try:
                        clip_info = await self.video_service.create_single_clip(
                            video_path,
                            localized,
                            position,
                            clips_output_dir,
                            font_family,
                            font_size,
                            font_color,
                            caption_template,
                            output_format,
                            add_subtitles,
                            normalized_cleanup_settings,
                            max_short_side=output_short_side,
                            max_clip_seconds=clip_duration(clip_length).maximum,
                            include_broll=include_broll,
                            broll_session=broll_session,
                            broll_ledger=broll_ledger,
                            edl=localized.get("edl"),
                            niche_override=niche_override,
                            project_niche_override=project_niche_override,
                            classifier_niche=classifier_niche,
                            music_override=project_music_override,
                            music_mode=project_music_mode,
                            template_mode=project_template_mode,
                            framing_mode=project_framing_mode,
                            **(
                                {
                                    "layout_template": project_layout_template,
                                    "layout_credit": project_layout_credit,
                                }
                                if project_layout_template
                                else {}
                            ),
                            broll_enabled=project_broll_enabled,
                            hook_title_enabled=project_hook_title_enabled,
                            brand_application=brand_application or None,
                            profile_adjustments=segment_adjustments,
                            profile_adjustments_niche=(
                                learning_niche if segment_adjustments else None
                            ),
                            **(
                                {"allowed_ranges": render_allowed_ranges}
                                if render_allowed_ranges
                                else {}
                            ),
                            **(
                                {"excluded_ranges": render_excluded_ranges}
                                if render_excluded_ranges
                                else {}
                            ),
                            **(
                                {"vocals_stem": render_vocals_stem}
                                if render_vocals_stem is not None
                                else {}
                            ),
                            **(
                                {"task_text_bands": task_text_bands}
                                if task_text_bands is not None
                                else {}
                            ),
                            # Creation-mode treatment switch (F-R1):
                            # True for every mode since the fix — a
                            # moments_manual pick renders with the same
                            # pipeline treatment as moments_ai, so the
                            # task's template/music choices are honoured
                            # (the EDL build produces the reframe plan
                            # and the music chain; Plain is plainified
                            # to the full-bleed tight crop inside it).
                            ai_editing=mode_plan.ai_editing,
                        )
                    except Exception as exc:
                        # One clip's render must not take the whole task down.
                        # create_single_clip normally reports failure as None,
                        # so this only catches unexpected crashes. The exception
                        # travels with the result so the task can record a
                        # scrubbed per-clip reason instead of failing whole.
                        logger.exception(
                            "Clip %s crashed during render; marking it failed",
                            position + 1,
                        )
                        clip_info = None
                        render_error: Optional[BaseException] = exc
                    else:
                        render_error = None
                    return clip_info, niche_override, render_error

            render_tasks: Dict[int, asyncio.Task] = {
                i: asyncio.create_task(render_segment(i, segment))
                for i, segment in enumerate(segments_to_render)
                if i + 1 not in completed_positions
            }
            # Shared with the outer finally, which cancels and cleans up any
            # render whose clip was never persisted (cancellation, errors).
            pending_render_tasks.update(render_tasks)

            for i, segment in enumerate(segments_to_render):
                # Check cancellation
                await check_cancelled()
                if i + 1 in completed_positions:
                    continue

                # Update progress: 70-95% spread across clips
                clip_progress = 70 + int(
                    ((i + 1) / total_clips) * 25
                ) if total_clips > 0 else 95
                await update_progress(
                    clip_progress,
                    f"Creating clip {i + 1}/{total_clips}...",
                )

                clip_info, clip_niche_override, render_error = await render_tasks[i]
                pending_clip_path = Path(clip_info["path"]) if clip_info else None
                await check_cancelled()
                if clip_info is None:
                    failed_positions.append(i + 1)
                    render_failures[i + 1] = render_error
                    continue
                _collect_clip_render_timings(stage_timings, i + 1, clip_info)
                if time_map:
                    clip_info = await run_in_thread(
                        restore_clip_timeline, clip_info, time_map, segment
                    )

                async with self._processing_transaction(task_id):
                    await check_cancelled()
                    clip_order = max(clip_ids_by_order, default=0) + 1
                    # Save to DB immediately
                    clip_id = await self.clip_repo.create_clip(
                        self.db,
                        task_id=task_id,
                        filename=clip_info["filename"],
                        file_path=clip_info["path"],
                        start_time=clip_info["start_time"],
                        end_time=clip_info["end_time"],
                        duration=clip_info["duration"],
                        text=clip_info.get("text", ""),
                        relevance_score=clip_info.get("relevance_score", 0.0),
                        reasoning=clip_info.get("reasoning", ""),
                        clip_order=clip_order,
                        virality_score=clip_info.get("virality_score", 0),
                        hook_score=clip_info.get("hook_score", 0),
                        engagement_score=clip_info.get("engagement_score", 0),
                        value_score=clip_info.get("value_score", 0),
                        shareability_score=clip_info.get("shareability_score", 0),
                        hook_type=clip_info.get("hook_type"),
                        hook_title=clip_info.get("hook_title"),
                        niche_override=clip_niche_override,
                        clip_plan=clip_info.get("clip_plan"),
                    )

                    # Update task's clip IDs array
                    next_clip_ids = {**clip_ids_by_order, clip_order: clip_id}
                    await self.task_repo.update_task_clips(
                        self.db, task_id, [next_clip_ids[order] for order in sorted(next_clip_ids)]
                    )
                    await save_checkpoint({"completed_positions": sorted(completed_positions | {i + 1})})

                    await check_cancelled()
                # AI quality control: inspect the rendered file, store the
                # report on the clip, and give a hard failure exactly one
                # re-render through the Lane-5 path before flagging it. QC
                # never fails the task — its own errors land on the clip as
                # qc_status "error" and processing continues.
                await self._qc_persisted_clip(
                    task_id=task_id,
                    clip_id=clip_id,
                    segment=segment,
                    clip_niche_override=clip_niche_override,
                    project_niche_override=project_niche_override,
                    output_format=output_format,
                    output_short_side=output_short_side,
                    music_override=project_music_override,
                    music_mode=project_music_mode,
                    template_mode=project_template_mode,
                )
                # Brief compliance: judged on the rendered file's actual
                # source ranges, after QC (and any corrective re-render).
                await self._compliance_persisted_clip(
                    task_id=task_id,
                    clip_id=clip_id,
                    segment=segment,
                    content_brief=content_brief,
                    transcript=result.get("transcript"),
                )
                pending_clip_path = None
                persisted_clip_paths.add(Path(clip_info["path"]))
                clip_ids_by_order[clip_order] = clip_id
                completed_positions.add(i + 1)

                # Notify frontend via SSE
                if clip_ready_callback:
                    clip_record = await self.clip_repo.get_clip_by_id(
                        self.db, clip_id
                    )
                    if clip_record:
                        await clip_ready_callback(i, total_clips, clip_record)

            stage_timings["render_seconds"] = round(
                perf_counter() - render_start, 3
            )
            logger.info(f"Task {task_id} stage timings: {stage_timings}")

            clip_ids = [clip_ids_by_order[order] for order in sorted(clip_ids_by_order)]
            if not clip_ids:
                raise ValueError("No clips could be rendered. Please try again.")
            # Partial success is still success: the clips that rendered are
            # usable output, so the task completes and reports exactly which
            # positions failed (with scrubbed reasons) instead of flipping
            # to a bare failure that hides the finished clips. Retry
            # generation resumes from the completed positions and only
            # re-renders the missing ones.
            partial_failure = _partial_failure_details(
                clip_ids, failed_positions, render_failures
            )
            completion_message = (
                "Complete!"
                if partial_failure is None
                else (
                    f"Complete — {partial_failure['clips_completed']} of "
                    f"{partial_failure['clips_completed'] + partial_failure['clips_failed']} "
                    "clips created. Retry generation to create the missing clips."
                )
            )

            # A cancellation committed after the final render must win over completion.
            await check_cancelled()
            completed = await self.task_repo.update_task_status(
                self.db,
                task_id,
                "completed",
                progress=100,
                progress_message=completion_message,
                expected_statuses=["processing"],
            )
            if completed is False:
                raise TaskCancelled()

            if progress_callback:
                await progress_callback(100, completion_message, "completed")

            completion_timings = dict(stage_timings)
            if partial_failure is not None:
                completion_timings["partial_failure"] = partial_failure
            await self.task_repo.update_task_runtime_metadata(
                self.db,
                task_id,
                completed_at=datetime.utcnow(),
                stage_timings_json=json.dumps(completion_timings),
                error_code="",
            )
            # The completion email is a non-essential late stage: its
            # failure (mail provider down, template error) must never flip
            # a task whose clips are already persisted and marked complete.
            try:
                await self._send_completion_notification_if_needed(
                    task_id=task_id,
                    clips_count=len(clip_ids),
                )
            except Exception:
                logger.exception(
                    "Completion notification failed for task %s; "
                    "the task itself is complete",
                    task_id,
                )

            if partial_failure is not None:
                logger.warning(
                    "Task %s completed with %d of %d clips; failed positions: %s",
                    task_id,
                    partial_failure["clips_completed"],
                    partial_failure["clips_completed"]
                    + partial_failure["clips_failed"],
                    [entry["position"] for entry in partial_failure["failed_clips"]],
                )
            else:
                logger.info(
                    f"Task {task_id} completed successfully with {len(clip_ids)} clips"
                )

            return {
                "task_id": task_id,
                "clips_count": len(clip_ids),
                "segments": result["segments"],
                "summary": result.get("summary"),
                "key_topics": result.get("key_topics"),
                "partial_failure": partial_failure,
            }

        except Exception as e:
            logger.error(f"Error processing task {task_id}: {e}")
            # Clear failed writes before recording the terminal error state.
            await self.db.rollback()
            if pending_clip_path and pending_clip_path != video_path:
                try:
                    if pending_clip_path.resolve().is_relative_to(clips_output_dir.resolve()):
                        pending_clip_path.unlink(missing_ok=True)
                        pending_clip_path.with_suffix(".source_map.json").unlink(missing_ok=True)
                except OSError:
                    logger.warning("Could not remove unfinished clip %s", pending_clip_path)
            if str(e) == "Task cancelled":
                await self.task_repo.update_task_status(
                    self.db,
                    task_id,
                    "cancelled",
                    progress=0,
                    progress_message="Cancelled by user",
                    expected_statuses=["queued", "processing"],
                )
                raise
            error_code = "task_error"
            message = str(e).lower()
            if isinstance(e, NoCompliantMomentsError):
                error_code = "no_compliant_moments"
            elif isinstance(e, YouTubeGeoRestrictedError):
                error_code = "youtube_geo_restricted"
            elif "download" in message or "youtube" in message:
                error_code = "download_error"
            elif "analysis" in message:
                error_code = "analysis_error"
            elif "transcript" in message:
                error_code = "transcription_error"
            elif "cancelled" in message:
                error_code = "cancelled"

            # The raw exception stays in the logs above; what is stored on
            # the task (and returned by the API) is the sanitised message —
            # raw provider errors can embed signed URLs and keys.
            user_message = user_facing_error_message(e, error_code)
            failed = await self.task_repo.update_task_status(
                self.db, task_id, "error", progress=0,
                progress_message=user_message,
                expected_statuses=["queued", "processing"],
            )
            if failed is False:
                raise

            await self.task_repo.update_task_runtime_metadata(
                self.db,
                task_id,
                completed_at=datetime.utcnow(),
                error_code=error_code,
            )
            if progress_callback:
                try:
                    await progress_callback(0, user_message, "error")
                except Exception:
                    logger.exception("Could not publish task failure for %s", task_id)
            raise
        finally:
            if pending_render_tasks:
                # Cancel renders nobody will persist (cancellation or an error
                # part-way through the plan) and remove their orphaned files.
                # Clips committed to the DB are kept: retries resume from them.
                for render_task in pending_render_tasks.values():
                    if not render_task.done():
                        render_task.cancel()
                outcomes = await asyncio.gather(
                    *pending_render_tasks.values(), return_exceptions=True
                )
                for outcome in outcomes:
                    if not (isinstance(outcome, tuple) and outcome[0]):
                        continue
                    orphan = Path(outcome[0]["path"])
                    if orphan in persisted_clip_paths or orphan == pending_clip_path:
                        continue
                    try:
                        orphan.unlink(missing_ok=True)
                        orphan.with_suffix(".source_map.json").unlink(missing_ok=True)
                    except OSError:
                        logger.warning(
                            "Could not remove unpersisted clip %s", orphan
                        )
            await run_in_thread(self._cleanup_source_video, video_path, source_type, url)
            if source_type in {"twitch", "kick"}:
                # Also handles failures before process_video_complete returns a
                # path (and therefore before the generic path cleanup can run).
                await run_in_thread(cleanup_stream_task, task_id)

    async def _send_completion_notification_if_needed(
        self, *, task_id: str, clips_count: int
    ) -> None:
        context = await self.task_repo.get_task_notification_context(self.db, task_id)
        if not context:
            logger.warning("Task %s missing notification context; skipping email", task_id)
            return

        if not context.get("notify_on_completion"):
            return

        if context.get("completion_notification_sent_at"):
            logger.info(
                "Completion notification already sent for task %s; skipping", task_id
            )
            return

        user_email = context.get("user_email")
        if not user_email:
            logger.warning(
                "Task %s has notify_on_completion enabled but user email is missing",
                task_id,
            )
            return

        email_service = TaskCompletionEmailService(self.config)
        if not email_service.is_configured:
            logger.warning(
                "Skipping completion notification for task %s because Amazon SES is not configured",
                task_id,
            )
            return

        try:
            await email_service.send_task_completed_email(
                recipient=TaskCompletionRecipient(
                    email=user_email,
                    name=context.get("user_name"),
                    first_name=context.get("user_first_name"),
                ),
                task_id=task_id,
                source_title=context.get("source_title"),
                clips_count=clips_count,
            )
            stamped = await self.task_repo.mark_completion_notification_sent(
                self.db, task_id
            )
            if not stamped:
                logger.info(
                    "Completion notification stamp already existed for task %s",
                    task_id,
                )
        except Exception:
            logger.exception(
                "Failed to send completion notification for task %s",
                task_id,
            )

    async def get_task_with_clips(self, task_id: str) -> Optional[Dict[str, Any]]:
        """Get task details with all clips."""
        task = await self.task_repo.get_task_by_id(self.db, task_id)

        if not task:
            return None

        # Lazy sweep: fail the task here if its worker is provably gone, so
        # a dead task does not show "processing" forever on this read. The
        # worker also runs the same sweep for all tasks on startup
        # (recover_stale_tasks) — this read-path check stays as the
        # backstop for workers that die while the API keeps serving.
        if await self._fail_stale_task(task):
            task = await self.task_repo.get_task_by_id(self.db, task_id)
            if not task:
                return None

        # Get clips
        clips = await self.clip_repo.get_clips_by_task(self.db, task_id)
        task["clips"] = [
            {
                **{key: value for key, value in clip.items() if key != "file_path"},
                "caption_settings": await run_in_thread(
                    MediaStorage().caption_settings,
                    clip["file_path"],
                ) if clip.get("file_path") else None,
                "niche_profile": await run_in_thread(
                    self._recorded_clip_profile, clip.get("file_path")
                )
                or resolve_effective_profile(
                    clip.get("niche_override"), task.get("niche_override"), None
                ),
            }
            for clip in clips
        ]
        task["clips_count"] = len(clips)
        # Surface the stored partial-failure record (written into
        # stage_timings_json at completion) as its own field so the UI
        # can show "completed with N clips missing" honestly instead of
        # a bare Completed over a short clip list.
        task["partial_failure"] = None
        raw_timings = task.get("stage_timings_json")
        if raw_timings:
            try:
                parsed_timings = (
                    json.loads(raw_timings)
                    if isinstance(raw_timings, str)
                    else raw_timings
                )
            except (TypeError, ValueError):
                parsed_timings = None
            if isinstance(parsed_timings, dict):
                recorded = parsed_timings.get("partial_failure")
                if isinstance(recorded, dict) and recorded.get("failed_clips"):
                    task["partial_failure"] = recorded
        # Surface the stored classification outcome as its own field:
        # it carries is_fallback, so the UI can say "auto detection
        # failed — default profile used" instead of presenting the
        # fallback niche as a real detection.
        stored_classification = task.get("classification")
        task["classification"] = (
            stored_classification
            if isinstance(stored_classification, dict)
            else None
        )
        task.update(await self._load_task_source_settings(task_id))

        return task

    @staticmethod
    def _recorded_clip_profile(file_path: Optional[str]) -> Optional[str]:
        """Niche a clip was actually rendered with, from its bundle record."""
        if not file_path:
            return None
        try:
            from ..deliverable_bundle import read_recorded_profile

            profile = read_recorded_profile(
                MediaStorage().local_path(file_path, metadata_only=True)
            )
        except Exception:
            return None
        return profile if is_known_profile(profile) else None

    async def get_user_tasks(
        self, user_id: str, limit: int = 50
    ) -> list[Dict[str, Any]]:
        """Get all tasks for a user."""
        return await self.task_repo.get_user_tasks(self.db, user_id, limit)

    @serialized_task_edit
    async def delete_task(self, task_id: str) -> None:
        """Delete a task and all its associated clips."""
        task = await self.task_repo.get_task_by_id(self.db, task_id)
        source_url = task.get("source_url") if task else None
        if source_url and source_url.startswith("upload://"):
            if source_url.startswith("upload://s3/"):
                path = MediaStorage().source_reference(source_url)
            else:
                path = str(self.video_service.resolve_local_video_path(source_url))
            await queue_delete(self.db, path)
        elif source_url and MediaStorage().enabled:
            video_id = extract_video_id(source_url)
            if video_id:
                await queue_delete(self.db, MediaStorage().youtube_metadata_reference(video_id))
        # Delete all clips for this task
        await self.clip_repo.delete_clips_by_task(self.db, task_id)

        # Delete the task
        await self.task_repo.delete_task(self.db, task_id)

        logger.info(f"Deleted task {task_id} and all associated clips")


    async def get_performance_metrics(self) -> Dict[str, Any]:
        """Return aggregate processing performance metrics."""
        return await self.task_repo.get_performance_metrics(self.db)
