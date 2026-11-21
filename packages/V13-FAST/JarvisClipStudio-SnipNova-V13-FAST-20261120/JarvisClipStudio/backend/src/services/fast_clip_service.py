"""
Fast Clip Generation Service - Optimized for long videos (5-10+ hours).

This service implements a "fast clips" mode that:
1. Uses YouTube captions (if available) for instant transcript - no audio download needed
2. Falls back to fast LLM analysis on transcript only
3. Selects viral clips BEFORE downloading any video
4. Only downloads the selected segments (partial download with padding)
5. Renders clips in parallel with higher concurrency
"""

import asyncio
import json
import logging
import os
import uuid
from pathlib import Path
from time import perf_counter
from typing import Any, Callable, Awaitable, Dict, List, Optional, Tuple

from ..config import get_config
from ..media.ffmpeg import ffprobe_duration, ffprobe_has_audio
from ..media.transcription import (
    get_video_transcript,
    load_cached_transcript_data,
    _get_transcript_with_youtube_captions,
)
from ..youtube_utils import get_youtube_video_id, async_get_youtube_video_info
from ..youtube_source import (
    materialize_clip_source,
    merge_ranges,
    ranges_from_segments,
    build_time_map,
    save_time_map,
)
from ..video_utils import (
    create_clips_with_transitions,
    parse_timestamp_to_seconds,
    normalize_source_ranges,
    build_clip_keep_ranges,
    extend_keep_ranges_to_sentence_boundary,
    apply_segment_hook_teaser,
    clamp_keep_ranges_to_max_duration,
    snap_clip_start_to_sentence,
    keep_ranges_transcript_text,
    build_keep_ranges_from_source_ranges,
)
from ..clip_editor import clip_duration
from ..auto_director.creation_modes import plan_for_mode, REVIEW_MOMENT_PICK
from ..ai import (
    get_most_relevant_parts_by_transcript,
    max_final_clips_for_duration,
    sanitize_hook_title,
)
from ..clip_duration import clip_duration as clip_duration_fn
from .. import provider_manager

logger = logging.getLogger(__name__)

# Configuration for fast clips mode
FAST_CLIPS_MAX_CLIPS = int(os.getenv("FAST_CLIPS_MAX_CLIPS", "15"))
FAST_CLIPS_TARGET_CLIPS_PER_HOUR = float(os.getenv("FAST_CLIPS_TARGET_CLIPS_PER_HOUR", "3"))
FAST_CLIPS_MAX_DURATION_MINUTES = int(os.getenv("FAST_CLIPS_MAX_DURATION_MINUTES", "600"))  # 10 hours
FAST_CLIPS_SECTION_PADDING = float(os.getenv("FAST_CLIPS_SECTION_PADDING", "15.0"))  # seconds
FAST_CLIPS_RENDER_CONCURRENCY = int(os.getenv("FAST_CLIPS_RENDER_CONCURRENCY", "5"))
FAST_CLIPS_LLM = os.getenv("FAST_CLIPS_LLM", "groq:llama-3.3-70b-versatile")
FAST_CLIPS_SKIP_STATIC_CHECK = os.getenv("FAST_CLIPS_SKIP_STATIC_CHECK", "true").lower() in ("1", "true", "yes")
FAST_CLIPS_SKIP_OPENING_REFINEMENT = os.getenv("FAST_CLIPS_SKIP_OPENING_REFINEMENT", "true").lower() in ("1", "true", "yes")


class FastClipService:
    """Service for fast clip generation from long videos."""

    def __init__(self):
        self.config = get_config()

    async def process_fast_clips(
        self,
        url: str,
        source_type: str,
        task_id: str,
        font_family: Optional[str] = None,
        font_size: Optional[int] = None,
        font_color: Optional[str] = None,
        caption_template: str = "default",
        output_format: str = "vertical",
        add_subtitles: bool = True,
        clip_length: str = "auto",
        target_clip_count: Optional[int] = None,
        progress_callback: Optional[Callable[[int, str, str], Awaitable[None]]] = None,
        should_cancel: Optional[Callable[[], Awaitable[bool]]] = None,
        performance_context: Optional[str] = None,
        cleanup_settings: Optional[Dict[str, Any]] = None,
        creation_mode: Optional[str] = None,
        manual_ranges: Optional[List[Dict[str, Any]]] = None,
    ) -> Dict[str, Any]:
        """
        Fast clip generation pipeline for long videos.

        Flow:
        1. Get video metadata (title, duration) - fast
        2. Try to get YouTube captions (instant transcript) - fast
        3. If no captions, download audio only and transcribe with fast model
        4. Analyze transcript with fast LLM to select best segments
        5. Download ONLY the selected segments (partial download)
        6. Render clips in parallel
        """
        video_path: Optional[Path] = None
        stage_timings: Dict[str, float] = {}
        pipeline_start = perf_counter()

        async def update_progress(progress: int, message: str, status: str = "processing"):
            if progress_callback:
                await progress_callback(progress, message, status)

        async def check_cancelled():
            if should_cancel and await should_cancel():
                raise Exception("Task cancelled")

        try:
            # Step 1: Get video metadata
            await update_progress(5, "Fetching video info...", "processing")
            await check_cancelled()

            video_id = get_youtube_video_id(url) if source_type == "youtube" else None
            source_duration = None
            source_title = None
            source_description = None

            if source_type == "youtube" and video_id:
                metadata = await async_get_youtube_video_info(url, task_id=task_id)
                if metadata:
                    source_duration = metadata.get("duration")
                    source_title = metadata.get("title")
                    source_description = metadata.get("description")
                    logger.info(f"Video duration: {source_duration}s ({source_duration/3600:.1f} hours)")

            if source_duration and source_duration > FAST_CLIPS_MAX_DURATION_MINUTES * 60:
                logger.warning(f"Video is very long ({source_duration/3600:.1f}h), proceeding with fast clips mode")

            # Step 2: Get transcript - prefer YouTube captions (fastest)
            await update_progress(15, "Getting transcript...", "processing")
            await check_cancelled()

            transcript_started = perf_counter()
            transcript = None
            transcript_data = None

            if source_type == "youtube" and video_id:
                # Try YouTube captions first - instant, no download
                logger.info("Attempting to fetch YouTube captions...")
                try:
                    captions_result = _get_transcript_with_youtube_captions(url)
                    if captions_result and captions_result.get("text"):
                        transcript = captions_result["text"]
                        transcript_data = captions_result
                        logger.info(f"Got transcript from YouTube captions: {len(transcript)} chars")
                except Exception as e:
                    logger.warning(f"YouTube captions unavailable: {e}")

            # If no captions, we'll need to download audio and transcribe
            if not transcript:
                await update_progress(20, "Downloading audio for transcription...", "processing")
                # For fast clips, we download audio only first
                video_path = await self._download_audio_only(url, source_type, task_id, video_id)
                if video_path:
                    transcript = await self._fast_transcribe(video_path, source_type, url)
                    transcript_data = load_cached_transcript_data(video_path)

            stage_timings["transcript_seconds"] = round(perf_counter() - transcript_started, 3)

            if not transcript:
                raise Exception("Failed to get transcript")

            # Step 3: Analyze transcript with fast LLM to select clips
            await update_progress(40, "Analyzing content with AI...", "processing")
            await check_cancelled()

            analysis_started = perf_counter()
            segments = await self._fast_analyze_transcript(
                transcript=transcript,
                transcript_data=transcript_data,
                source_duration=source_duration,
                source_title=source_title,
                source_description=source_description,
                clip_length=clip_length,
                target_clip_count=target_clip_count,
                performance_context=performance_context,
            )
            stage_timings["analysis_seconds"] = round(perf_counter() - analysis_started, 3)

            if not segments:
                raise Exception("No viral segments found")

            logger.info(f"Selected {len(segments)} segments for clipping")

            # Step 4: Download only the selected segments
            await update_progress(60, "Downloading selected segments...", "processing")
            await check_cancelled()

            download_started = perf_counter()
            video_path, time_map = await self._download_segments_only(
                url=url,
                source_type=source_type,
                task_id=task_id,
                video_id=video_id,
                segments=segments,
                source_duration=source_duration,
            )
            stage_timings["download_seconds"] = round(perf_counter() - download_started, 3)

            if not video_path:
                raise Exception("Failed to download segments")

            # Step 5: Render clips in parallel
            await update_progress(80, "Rendering clips...", "processing")
            await check_cancelled()

            render_started = perf_counter()
            clips_info = await self._render_clips_parallel(
                video_path=video_path,
                segments=segments,
                time_map=time_map,
                task_id=task_id,
                font_family=font_family,
                font_size=font_size,
                font_color=font_color,
                caption_template=caption_template,
                output_format=output_format,
                add_subtitles=add_subtitles,
                cleanup_settings=cleanup_settings,
                clip_length=clip_length,
                progress_callback=progress_callback,
            )
            stage_timings["render_seconds"] = round(perf_counter() - render_started, 3)

            stage_timings["total_seconds"] = round(perf_counter() - pipeline_start, 3)

            await update_progress(100, f"Generated {len(clips_info)} clips!", "completed")

            return {
                "video_path": str(video_path),
                "segments": segments,
                "clips": clips_info,
                "transcript": transcript,
                "stage_timings": stage_timings,
                "time_map": time_map,
            }

        except Exception as e:
            logger.error(f"Fast clip generation failed: {e}", exc_info=True)
            raise
        finally:
            # Note: We don't clean up video_path here as it may be cached for reuse
            pass

    async def _download_audio_only(
        self,
        url: str,
        source_type: str,
        task_id: Optional[str],
        video_id: Optional[str],
    ) -> Optional[Path]:
        """Download audio only for transcription."""
        from ..youtube_source import download_analysis_source
        from ..youtube_proxy import ByteBudget
        from ..utils.async_helpers import run_in_thread

        if source_type != "youtube" or not video_id:
            # For non-YouTube, we need full download
            return None

        runtime_config = get_config()
        job_budget = ByteBudget(getattr(runtime_config, "youtube_download_max_bytes", 2_000_000_000))

        def _download_audio():
            return download_analysis_source(
                url,
                video_id,
                task_id=task_id,
                budget=job_budget,
                duration=None,
                download_full=None,
            )

        try:
            video_path, _ = await run_in_thread(_download_audio)
            return video_path
        except Exception as e:
            logger.error(f"Audio download failed: {e}")
            return None

    async def _fast_transcribe(
        self,
        video_path: Path,
        source_type: str,
        source_url: Optional[str],
    ) -> str:
        """Fast transcription using the fast mode model."""
        from ..utils.async_helpers import run_in_thread

        # Use fast mode transcript model
        runtime_config = get_config()
        speech_model = runtime_config.fast_mode_transcript_model

        transcript = await run_in_thread(
            get_video_transcript,
            video_path,
            speech_model,
            source_url if source_type == "youtube" else None,
        )
        return transcript

    async def _fast_analyze_transcript(
        self,
        transcript: str,
        transcript_data: Optional[Dict],
        source_duration: Optional[int],
        source_title: Optional[str],
        source_description: Optional[str],
        clip_length: str,
        target_clip_count: Optional[int],
        performance_context: Optional[str],
    ) -> List[Dict[str, Any]]:
        """Analyze transcript with fast LLM to select best segments."""
        from ..video_utils import build_clip_signal_summary
        from ..auto_director.reaction_moments import build_reaction_hints
        from ..auto_director.visual_moments import detect_scene_cut_times
        from ..auto_director import story_events
        from ..media.timeline import parse_timestamp_to_seconds

        # Calculate target clip count if not specified
        if target_clip_count is None and source_duration:
            hours = source_duration / 3600
            target_clip_count = max(3, min(FAST_CLIPS_MAX_CLIPS, int(hours * FAST_CLIPS_TARGET_CLIPS_PER_HOUR)))
        elif target_clip_count is None:
            target_clip_count = FAST_CLIPS_MAX_CLIPS

        # Build clip signals for the LLM
        clip_signals = None
        # We skip reaction hints and scene cuts for speed in fast mode
        # They can be enabled via env vars if needed

        # Use fast LLM for analysis
        original_llm = os.getenv("LLM")
        os.environ["LLM"] = FAST_CLIPS_LLM

        try:
            # Use a simplified analysis prompt for speed
            relevant_parts = await get_most_relevant_parts_by_transcript(
                transcript,
                clip_signals=clip_signals,
                performance_context=performance_context,
                clip_length=clip_length,
                include_reserve=True,
                target_clip_count=target_clip_count,
                source_title=source_title,
                source_description=source_description,
                # Disable expensive features for speed
                content_brief=None,
            )

            segments = relevant_parts.most_relevant_segments
            # Convert to dict format
            result_segments = []
            for segment in segments:
                if isinstance(segment, dict):
                    result_segments.append(segment)
                else:
                    # Pydantic model
                    virality = segment.virality.model_dump() if segment.virality else {}
                    result_segments.append({
                        "start_time": segment.start_time,
                        "end_time": segment.end_time,
                        "text": segment.text,
                        "relevance_score": segment.relevance_score,
                        "reasoning": segment.reasoning,
                        "virality_score": virality.get("total_score", 0),
                        "hook_score": virality.get("hook_score", 0),
                        "engagement_score": virality.get("engagement_score", 0),
                        "value_score": virality.get("value_score", 0),
                        "shareability_score": virality.get("shareability_score", 0),
                        "hook_type": virality.get("hook_type"),
                        "hook_title": getattr(segment, "hook_title", None),
                    })

            return result_segments[:target_clip_count]
        finally:
            # Restore original LLM
            if original_llm:
                os.environ["LLM"] = original_llm
            else:
                os.environ.pop("LLM", None)

    async def _download_segments_only(
        self,
        url: str,
        source_type: str,
        task_id: Optional[str],
        video_id: Optional[str],
        segments: List[Dict[str, Any]],
        source_duration: Optional[int],
    ) -> Tuple[Optional[Path], Optional[Any]]:
        """Download only the selected segments with padding."""
        from ..youtube_source import materialize_clip_source, merge_ranges, ranges_from_segments, build_time_map, save_time_map
        from ..youtube_proxy import ByteBudget
        from ..utils.async_helpers import run_in_thread

        if source_type != "youtube" or not video_id:
            # For non-YouTube sources, download full video
            logger.warning("Non-YouTube source, downloading full video")
            from ..services.video_service import VideoService
            video_path = await VideoService.download_video(url, task_id=task_id)
            return video_path, None

        runtime_config = get_config()
        job_budget = ByteBudget(getattr(runtime_config, "youtube_download_max_bytes", 2_000_000_000))

        # Extract ranges from segments
        ranges = ranges_from_segments(segments)

        # Add padding to each range
        padded_ranges = []
        for start, end in ranges:
            padded_start = max(0, start - FAST_CLIPS_SECTION_PADDING)
            padded_end = min(source_duration or end, end + FAST_CLIPS_SECTION_PADDING)
            if padded_end > padded_start:
                padded_ranges.append((padded_start, padded_end))

        # Merge overlapping ranges
        merged_ranges = merge_ranges(padded_ranges, padding=0, duration=source_duration)

        logger.info(f"Downloading {len(merged_ranges)} segment ranges: {merged_ranges}")

        def _download_segments():
            seg_dicts = [{"start_time": str(start), "end_time": str(end)} for start, end in merged_ranges]
            return materialize_clip_source(
                url,
                video_id,
                seg_dicts,
                task_id=task_id,
                budget=job_budget,
                quality=None,
            )

        try:
            video_path = await run_in_thread(_download_segments)

            # Build time map for mapping local timestamps back to original
            time_map = build_time_map(merged_ranges)
            save_time_map(video_path, time_map)

            return video_path, time_map
        except Exception as e:
            logger.error(f"Segment download failed: {e}")
            # Fallback to full download
            from ..services.video_service import VideoService
            video_path = await VideoService.download_video(url, task_id=task_id)
            return video_path, None

    async def _render_clips_parallel(
        self,
        video_path: Path,
        segments: List[Dict[str, Any]],
        time_map: Optional[Any],
        task_id: str,
        font_family: Optional[str],
        font_size: Optional[int],
        font_color: Optional[str],
        caption_template: str,
        output_format: str,
        add_subtitles: bool,
        cleanup_settings: Optional[Dict[str, Any]],
        clip_length: str,
        progress_callback: Optional[Callable[[int, str, str], Awaitable[None]]],
    ) -> List[Dict[str, Any]]:
        """Render clips in parallel with higher concurrency."""
        from ..utils.async_helpers import run_in_thread
        from ..video_utils import create_clips_with_transitions
        from ..config import get_config
        from ..media.timeline import localize_segment

        clips_output_dir = Path(get_config().temp_dir) / "clips"
        clips_output_dir.mkdir(parents=True, exist_ok=True)

        # Prepare segments with localized timestamps if time_map exists
        prepared_segments = []
        for segment in segments:
            localized = localize_segment(segment, time_map) if time_map else segment
            prepared_segments.append(localized)

        # Use the existing create_clips_with_transitions but with higher concurrency
        # The function already processes clips, we just need to ensure concurrency is high
        old_concurrency = os.getenv("CLIP_RENDER_CONCURRENCY")
        os.environ["CLIP_RENDER_CONCURRENCY"] = str(FAST_CLIPS_RENDER_CONCURRENCY)

        try:
            clips_info = await run_in_thread(
                create_clips_with_transitions,
                video_path,
                prepared_segments,
                clips_output_dir,
                font_family,
                font_size,
                font_color,
                caption_template,
                output_format,
                add_subtitles,
                cleanup_settings,
                max_clip_seconds=clip_duration(clip_length).maximum,
            )
            return clips_info
        finally:
            # Restore original concurrency
            if old_concurrency:
                os.environ["CLIP_RENDER_CONCURRENCY"] = old_concurrency
            else:
                os.environ.pop("CLIP_RENDER_CONCURRENCY", None)


async def process_fast_clips_task(
    url: str,
    source_type: str,
    task_id: str,
    user_id: str,
    font_family: Optional[str] = None,
    font_size: Optional[int] = None,
    font_color: Optional[str] = None,
    caption_template: str = "default",
    processing_mode: str = "fast_clips",
    output_format: str = "vertical",
    add_subtitles: bool = True,
    cleanup_settings: Optional[Dict[str, Any]] = None,
    clip_length: str = "auto",
    progress_callback: Optional[Callable[[int, str, str], Awaitable[None]]] = None,
    should_cancel: Optional[Callable[[], Awaitable[bool]]] = None,
    clip_ready_callback: Optional[Callable[[int, int, Dict], Awaitable[None]]] = None,
    performance_context: Optional[str] = None,
    target_clip_count: Optional[int] = None,
    creation_mode: Optional[str] = None,
    manual_ranges: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    """
    Entry point for fast clip generation task.
    Can be called from the worker or directly.
    """
    service = FastClipService()
    return await service.process_fast_clips(
        url=url,
        source_type=source_type,
        task_id=task_id,
        font_family=font_family,
        font_size=font_size,
        font_color=font_color,
        caption_template=caption_template,
        output_format=output_format,
        add_subtitles=add_subtitles,
        clip_length=clip_length,
        target_clip_count=target_clip_count,
        progress_callback=progress_callback,
        should_cancel=should_cancel,
        performance_context=performance_context,
        cleanup_settings=cleanup_settings,
        creation_mode=creation_mode,
        manual_ranges=manual_ranges,
    )