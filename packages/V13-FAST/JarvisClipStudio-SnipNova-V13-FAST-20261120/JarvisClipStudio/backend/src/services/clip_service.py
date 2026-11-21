"""Clip editing and regeneration operations shared by TaskService.

The mixin uses TaskService's repositories, configuration, and cache key contract.
"""

from ..repositories.edit_transaction import serialized_task_edit
from typing import Dict, Any, List, Optional, Tuple
import logging
from pathlib import Path
import json
import tempfile
from uuid import uuid4

import redis.asyncio as redis

from ..clip_editor import (
    trim_clip_file,
    split_clip_file,
    merge_clip_files,
    overlay_custom_captions,
)
from ..video_utils import VALID_OUTPUT_FORMATS, parse_timestamp_to_seconds, create_optimized_clip
from ..utils.async_helpers import run_in_thread
from ..clip_duration import clip_duration, normalize_clip_length
from ..media.common import MAX_RENDERED_CLIP_SECONDS
from ..clip_cleanup import normalize_clip_cleanup_settings
from ..broll import broll_overlays_for, shift_broll_placements
from ..clip_source_map import (
    load_clip_broll,
    load_clip_editor_settings,
    load_clip_source_ranges,
    save_clip_broll,
    save_clip_editor_settings,
    save_clip_source_ranges,
    load_clip_output_offsets,
    slice_source_timeline,
    save_clip_caption_settings,
    load_clip_caption_settings,
    source_range_bounds,
    split_source_ranges,
    trim_source_ranges,
)

from ..media_storage import MediaStorage
from ..auto_director.edl import build_edl, load_profile
from ..auto_director.broll_setting import (
    resolve_broll_enabled,
    resolve_broll_gate,
)
from ..auto_director.niche import validate_niche_override
from ..auto_director.framing_mode import validate_framing_mode
from ..media.gaming_templates import clean_layout_credit, validate_layout_template
from ..auto_director.template_mode import validate_template_mode
from ..media.music_library import validate_music_mode
from ..caption_templates import get_template_names
from ..youtube_source import (
    load_time_map,
    local_ranges_for,
    localize_segment,
    ranges_from_segments,
    restore_clip_timeline,
)
from ..media.ffmpeg import cap_video_short_side, ffprobe_duration, rendered_clip_duration
from ..media.lut_library import list_luts
from ..stream_source import download_stream_sections
from .video_service import resolve_upload_for_edit


def _stored_layout(task: Dict[str, Any]) -> Tuple[Optional[str], Optional[str]]:
    """The task's streamer layout template and credit, or (None, None).

    Re-renders keep the "face on top, game below" template the clip was
    made with; an unreadable stored value falls back to the automatic layout.
    """
    try:
        template = validate_layout_template(task.get("layout_template"))
    except ValueError:
        logger.warning(
            "Ignoring invalid stored layout_template %r", task.get("layout_template")
        )
        return None, None
    if not template:
        return None, None
    return template, clean_layout_credit(task.get("layout_credit"))

logger = logging.getLogger(__name__)


class ClipEditingMixin:
    @staticmethod
    def _brief_allowed_ranges(task: Optional[Dict[str, Any]], time_map: Any = None):
        """Allowed source ranges for a brief task, localised when needed.

        Re-renders of a brief task must obey the same hard range rule as
        the original render (R1 in create_single_clip). Returns None for
        non-brief tasks, which keeps every re-render byte-identical.
        """
        brief = (task or {}).get("content_brief")
        if not brief:
            return None
        try:
            from ..auto_director.content_requirements import (
                allowed_range_tuples,
                localize_allowed_ranges,
                normalize_brief_v2,
            )

            brief = normalize_brief_v2(brief) or {}
            allowed = allowed_range_tuples(brief)
            if not allowed:
                return None
            if time_map is not None:
                return localize_allowed_ranges(allowed, time_map)
            return allowed
        except Exception:
            return None

    @staticmethod
    def _brief_excluded_ranges(task: Optional[Dict[str, Any]], time_map: Any = None):
        """Excluded source ranges for a brief task, localised when needed.

        Sibling of _brief_allowed_ranges with the opposite sign: footage
        inside these ranges must never survive a re-render either.
        Returns None for non-brief tasks / briefs without exclusions.
        """
        brief = (task or {}).get("content_brief")
        if not brief:
            return None
        try:
            from ..auto_director.content_requirements import (
                excluded_range_tuples,
                localize_allowed_ranges,
                normalize_brief_v2,
            )

            brief = normalize_brief_v2(brief) or {}
            excluded = excluded_range_tuples(brief)
            if not excluded:
                return None
            if time_map is not None:
                return localize_allowed_ranges(excluded, time_map)
            return excluded
        except Exception:
            return None
    @serialized_task_edit
    async def delete_clip(self, task_id: str, clip_id: str) -> None:
        clip = await self.clip_repo.get_clip_by_id(self.db, clip_id)
        if not clip or clip["task_id"] != task_id:
            raise ValueError("Clip not found")
        await self.clip_repo.delete_clip(self.db, clip_id)
        await self.clip_repo.reorder_task_clips(self.db, task_id)

    @serialized_task_edit
    async def update_task_settings(
        self,
        task_id: str,
        font_family: Optional[str],
        font_size: Optional[int],
        font_color: Optional[str],
        caption_template: str,
        include_broll: bool,
        apply_to_existing: bool,
        cleanup_settings: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Update task-level settings and optionally regenerate all clips."""
        await self.task_repo.update_task_settings(
            self.db,
            task_id,
            font_family,
            font_size,
            font_color,
            caption_template,
            include_broll,
        )

        if apply_to_existing:
            await self.regenerate_all_clips_for_task(
                task_id,
                font_family,
                font_size,
                font_color,
                caption_template,
                cleanup_settings=cleanup_settings,
            )

        return await self.get_task_with_clips(task_id) or {}


    @serialized_task_edit
    async def regenerate_all_clips_for_task(
        self,
        task_id: str,
        font_family: Optional[str],
        font_size: Optional[int],
        font_color: Optional[str],
        caption_template: str,
        cleanup_settings: Optional[Dict[str, Any]] = None,
    ) -> None:
        """Regenerate all clips in a task using existing segment boundaries."""
        task = await self.task_repo.get_task_by_id(self.db, task_id)
        if not task:
            raise ValueError("Task not found")

        source_url = task.get("source_url")
        source_type = task.get("source_type")
        metadata = await self._load_task_source_settings(task_id)
        output_format = metadata.get("output_format", "vertical")
        add_subtitles = metadata.get("add_subtitles", True)
        cleanup_payload = cleanup_settings or {
            "cut_long_pauses": metadata.get("cut_long_pauses"),
            "pause_threshold_ms": metadata.get("pause_threshold_ms"),
            "remove_filler_words": metadata.get("remove_filler_words"),
            "filtered_words": metadata.get("filtered_words"),
        }
        normalized_cleanup_settings = normalize_clip_cleanup_settings(
            cleanup_payload.get("cut_long_pauses"),
            cleanup_payload.get("pause_threshold_ms"),
            cleanup_payload.get("remove_filler_words"),
            cleanup_payload.get("filtered_words"),
        )
        existing_cleanup_settings = normalize_clip_cleanup_settings(
            metadata.get("cut_long_pauses"),
            metadata.get("pause_threshold_ms"),
            metadata.get("remove_filler_words"),
            metadata.get("filtered_words"),
        )
        should_recompute_cleanup = (
            cleanup_settings is not None
            and normalized_cleanup_settings != existing_cleanup_settings
        )

        if not source_url or not source_type:
            raise ValueError("Task source URL is missing; cannot regenerate clips")

        clips = await self.clip_repo.get_clips_by_task(self.db, task_id)
        if not clips:
            return

        download_quality, output_short_side = await self._media_limits(task.get("user_id"))
        # The task's B-roll switch gates regeneration too: resolved Off
        # means stored cutaways are not carried and none are re-planned.
        # The gate is resolve_broll_gate, not an AND with the legacy
        # include_broll flag: an explicit switch value is authoritative
        # (U3 — toggle On + legacy Off used to silently drop every
        # stored cutaway here), and the legacy flag is honoured only
        # when the switch was never set on the task.
        # Stored task-row settings are re-validated before use, the
        # same try/warn pattern TaskService.process_task applies: a
        # value that fails validation is logged with the task id and
        # the documented default applies, instead of the raw string
        # silently steering the regeneration.
        try:
            stored_template_mode = validate_template_mode(
                task.get("template_mode")
            )
        except ValueError:
            logger.warning(
                "Ignoring invalid stored template_mode %r on task %s",
                task.get("template_mode"),
                task_id,
            )
            stored_template_mode = None

        try:
            stored_framing_mode = validate_framing_mode(
                task.get("framing_mode")
            )
        except ValueError:
            logger.warning(
                "Ignoring invalid stored framing_mode %r on task %s",
                task.get("framing_mode"),
                task_id,
            )
            stored_framing_mode = None
        stored_layout_template, stored_layout_credit = _stored_layout(task)
        include_broll = resolve_broll_gate(
            stored_template_mode,
            task.get("broll_enabled"),
            task.get("include_broll"),
        )
        segments = []
        for clip in clips:
            source_ranges = await run_in_thread(self._get_clip_source_ranges, clip)
            # Same timeline, same cutaways. Recomputed cleanup moves the words,
            # so those clips get a fresh plan instead.
            broll = (
                await run_in_thread(self._get_clip_broll, clip)
                if include_broll and not should_recompute_cleanup
                else []
            )
            bounds = source_range_bounds(source_ranges)
            if bounds:
                start_time = self._seconds_to_mmss(bounds[0])
                end_time = self._seconds_to_mmss(bounds[1])
            else:
                start_time = clip["start_time"]
                end_time = clip["end_time"]

            segments.append(
                {
                    "start_time": start_time,
                    "end_time": end_time,
                    **(
                        {"source_ranges": source_ranges}
                        if should_recompute_cleanup
                        else {"keep_ranges": source_ranges}
                    ),
                    "text": clip.get("text") or "",
                    "relevance_score": clip.get("relevance_score", 0.5),
                    "reasoning": clip.get("reasoning")
                    or "Regenerated with updated settings",
                    "virality_score": clip.get("virality_score", 0),
                    "hook_score": clip.get("hook_score", 0),
                    "engagement_score": clip.get("engagement_score", 0),
                    "value_score": clip.get("value_score", 0),
                    "shareability_score": clip.get("shareability_score", 0),
                    "hook_type": clip.get("hook_type"),
                    "hook_title": clip.get("hook_title"),
                    "broll": broll,
                }
            )

        video_path: Path
        if source_type == "youtube":
            downloaded = await self.video_service.download_video(
                source_url,
                quality=download_quality,
                task_id=task_id,
                ranges=ranges_from_segments(segments),
            )
            if not downloaded:
                raise ValueError("Failed to download source video for regeneration")
            video_path = Path(downloaded)
        elif source_type in {"twitch", "kick"}:
            # Its own stream-work operation; cleaned below without touching
            # any other edit of this task.
            video_path = await run_in_thread(
                download_stream_sections, source_url, segments,
                task.get("source_duration_seconds"), task_id,
            )
        else:
            video_path = await resolve_upload_for_edit(self.video_service, source_url)
            if not video_path.exists():
                raise ValueError("Source video file no longer exists")

        try:
            # Cap the working file before localizing timestamps. The path does
            # not change, so the timeline sidecar still applies.
            video_path = await self.video_service.prepare_source(video_path)
            # Separation-opt-in tasks regenerate from the same clean
            # vocals stem the originals were cut from (N3 regen gap):
            # resolved once per regeneration, against the prepared
            # source the render consumes. Any failure returns None and
            # the regeneration renders with the original audio.
            render_vocals_stem = await self._resolve_render_vocals_stem(
                task, video_path
            )
            time_map = await run_in_thread(load_time_map, video_path)
            render_segments = [
                localize_segment(segment, time_map) if time_map else segment
                for segment in segments
            ]
            # The task's stored text bands are reused verbatim, so the
            # regenerated clips keep the original render's text heights.
            task_text_bands = await self._load_task_text_bands(task_id)
            # A brief task's hard range rule binds regeneration too:
            # resolved through the same helpers the re-render path uses,
            # localised with the same time_map as the segments.
            render_allowed_ranges = self._brief_allowed_ranges(task, time_map)
            render_excluded_ranges = self._brief_excluded_ranges(task, time_map)
            clips_info = await self.video_service.create_video_clips(
                video_path,
                render_segments,
                font_family,
                font_size,
                font_color,
                caption_template,
                output_format,
                add_subtitles,
                normalized_cleanup_settings,
                max_short_side=output_short_side,
                # Merged and trimmed clips can run past a short preset; only
                # cap them as tightly as they were before presets existed.
                max_clip_seconds=max(
                    clip_duration(normalize_clip_length(metadata.get("clip_length"))).maximum,
                    MAX_RENDERED_CLIP_SECONDS,
                ),
                include_broll=include_broll,
                # The stem kwarg is passed only when one resolved, so a
                # non-opted-in task's regeneration call keeps its exact
                # historical shape (the N3 conditional-kwarg pattern).
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
                # Same conditional-kwarg pattern for the brief ranges:
                # a non-brief task's regeneration call is unchanged.
                **(
                    {"allowed_ranges": render_allowed_ranges}
                    if render_allowed_ranges is not None
                    else {}
                ),
                **(
                    {"excluded_ranges": render_excluded_ranges}
                    if render_excluded_ranges is not None
                    else {}
                ),
                # The task's framing choice rides along the same way, so
                # regenerated clips keep the framing they were made with.
                **(
                    {"framing_mode": stored_framing_mode}
                    if stored_framing_mode is not None
                    else {}
                ),
                **(
                    {
                        "layout_template": stored_layout_template,
                        "layout_credit": stored_layout_credit,
                    }
                    if stored_layout_template
                    else {}
                ),
            )
            if time_map:
                for clip_info in clips_info:
                    index = int(clip_info.get("clip_id") or 0) - 1
                    if 0 <= index < len(segments):
                        await run_in_thread(
                            restore_clip_timeline, clip_info, time_map, segments[index]
                        )
        finally:
            if source_type in {"youtube", "twitch", "kick"}:
                self._cleanup_source_video(video_path, source_type, source_url)

        if not clips_info:
            # Replacing the rows with nothing would delete every clip.
            raise ValueError("Could not regenerate the clips. Your existing clips are unchanged.")

        await self.clip_repo.delete_clips_by_task(self.db, task_id)

        clip_ids = []
        for i, clip_info in enumerate(clips_info):
            clip_id = await self.clip_repo.create_clip(
                self.db,
                task_id=task_id,
                filename=clip_info["filename"],
                file_path=clip_info["path"],
                start_time=clip_info["start_time"],
                end_time=clip_info["end_time"],
                duration=clip_info["duration"],
                text=clip_info.get("text") or "",
                relevance_score=clip_info.get("relevance_score", 0.5),
                reasoning=clip_info.get("reasoning")
                or "Regenerated with updated settings",
                clip_order=i + 1,
                virality_score=clip_info.get("virality_score", 0),
                hook_score=clip_info.get("hook_score", 0),
                engagement_score=clip_info.get("engagement_score", 0),
                value_score=clip_info.get("value_score", 0),
                shareability_score=clip_info.get("shareability_score", 0),
                hook_type=clip_info.get("hook_type"),
                hook_title=clip_info.get("hook_title"),
            )
            clip_ids.append(clip_id)

        await self.task_repo.update_task_clips(self.db, task_id, clip_ids)

        # The fresh rows above are born with no compliance verdict: the
        # brief's flags and verdicts would silently vanish here if the
        # post-render check were not re-run for the regenerated files.
        await self._restamp_regenerated_compliance(
            task=task,
            task_id=task_id,
            video_path=video_path,
            segments=segments,
            clips_info=clips_info,
            clip_ids=clip_ids,
        )

    async def _restamp_regenerated_compliance(
        self,
        *,
        task: Optional[Dict[str, Any]],
        task_id: str,
        video_path: Path,
        segments: List[Dict[str, Any]],
        clips_info: List[Dict[str, Any]],
        clip_ids: List[str],
    ) -> None:
        """Re-run brief compliance for clips a regeneration just created.

        The evaluation is TaskService's ``_compliance_persisted_clip``
        (this mixin's only host class) — the same post-render check the
        create path runs, judging each regenerated file's own source
        map. Regenerated segments carry no selection verdict, so
        LLM-judged requirements re-check as uncertain where they need
        one; range and flag verdicts — the brief's hard guarantees —
        are recomputed in full. Never raises: the helper it delegates
        to stamps its own failures as "uncertain".
        """
        raw_brief = (task or {}).get("content_brief")
        if not raw_brief:
            return
        try:
            from ..auto_director.content_requirements import normalize_brief_v2

            brief = normalize_brief_v2(raw_brief)
        except Exception:
            logger.warning(
                "Ignoring invalid stored content_brief on task %s; "
                "regenerated clips carry no compliance stamp",
                task_id,
            )
            return
        if not brief:
            return
        transcript_text: Optional[str] = None
        try:
            from ..media.transcription import load_cached_transcript_data

            transcript_data = await run_in_thread(
                load_cached_transcript_data, video_path
            )
            if isinstance(transcript_data, dict):
                transcript_text = transcript_data.get("text")
        except Exception:
            transcript_text = None
        for clip_id, clip_info in zip(clip_ids, clips_info):
            index = int(clip_info.get("clip_id") or 0) - 1
            segment = segments[index] if 0 <= index < len(segments) else {}
            await self._compliance_persisted_clip(
                task_id=task_id,
                clip_id=clip_id,
                segment=segment,
                content_brief=brief,
                transcript=transcript_text,
            )


    @staticmethod
    def _build_rerender_edl(
        clip: Dict[str, Any],
        start_seconds: float,
        end_seconds: float,
        target_niche: str,
        transition: Optional[str],
        segment_plan: Optional[list] = None,
        caption_overrides: Optional[Dict[str, Any]] = None,
        music_mode: Optional[str] = None,
        template_mode: Optional[str] = None,
        broll_enabled: Optional[bool] = None,
    ) -> Dict[str, Any]:
        """Build a fresh EDL for a re-style from the clip's stored transcript.

        The stored clip text is the transcript for this exact range, so the
        EDL's verbatim info cards and B-roll queries are rebuilt from it word
        by word — no transcription or re-selection is involved. Energy peaks
        are unknown at edit time, so profile zooms lean on their defaults.

        ``segment_plan`` (the editor's segment layout) replaces the freshly
        built segments wholesale, and the transcript handed to build_edl is
        rescaled to the plan's total duration so its time-based placements
        stay proportional. ``caption_overrides`` carries the Caption
        Studio's position choice into the EDL's caption block — the named
        preset plus the numeric ``position_y`` it resolves to (or the
        explicit ``position_y`` when one was given), so the render seam
        can honour the choice — and a user-set ``words_per_line``
        grouping, which beats the profile default (the other studio
        fields travel as render arguments instead).
        """
        profile = load_profile(target_niche)
        if segment_plan is not None:
            from ..auto_director.clip_edits import normalize_segments

            segment_plan = normalize_segments(segment_plan)
        plan_duration = (
            sum(s["end"] - s["start"] for s in segment_plan)
            if segment_plan is not None
            else end_seconds - start_seconds
        )
        stored_words = [
            {"word": word, "start": 0.0, "end": 0.0}
            for word in (clip.get("text") or "").split()
        ]
        words = stored_words
        if segment_plan is not None and len(stored_words) > 1:
            # build_edl times info cards / B-roll queries from word timings
            # proportionally (its degraded words all sit at 0.0); spread the
            # words evenly over the edited duration so those placements
            # track the new layout instead of collapsing to the clip start.
            words = [
                {
                    "word": entry["word"],
                    "start": round(plan_duration * i / len(stored_words), 3),
                    "end": round(plan_duration * (i + 1) / len(stored_words), 3),
                }
                for i, entry in enumerate(stored_words)
            ]
        edl = build_edl(
            {
                "id": clip.get("id", "clip"),
                "start": start_seconds,
                "end": end_seconds,
                "duration": plan_duration,
            },
            words,
            profile,
            [],
            broll_enabled=resolve_broll_enabled(template_mode, broll_enabled),
        )
        if segment_plan is not None:
            edl["segments"] = segment_plan
            edl["duration"] = plan_duration
        if transition is not None:
            for segment in edl.get("segments", []):
                if segment.get("index", 0) > 0:
                    segment["transition_in"] = transition
            if isinstance(edl.get("transitions"), dict):
                edl["transitions"]["default"] = transition
        if caption_overrides and caption_overrides.get("position"):
            edl.setdefault("captions", {})["position"] = caption_overrides[
                "position"
            ]
        if caption_overrides and caption_overrides.get("position_y") is not None:
            # Editor audit D2: the explicit height used to be dropped
            # here — validated and stored in the plan, but never stamped
            # onto the EDL, so no render ever saw it. It reaches the
            # EDL now, where create_optimized_clip resolves it (see
            # video_utils.edl_caption_position_request).
            edl.setdefault("captions", {})["position_y"] = caption_overrides[
                "position_y"
            ]
        if caption_overrides and caption_overrides.get("words_per_line") is not None:
            # N4: a grouping the user set in the quick editor (or the AI
            # edit) beats the profile default build_edl stamped — the
            # template override fills max_words_per_line from this block
            # (see video_utils.caption_template_override), so the render
            # groups captions the way the user asked. Without the choice
            # the profile value stands, exactly as before.
            edl.setdefault("captions", {})["words_per_line"] = caption_overrides[
                "words_per_line"
            ]
        elif caption_overrides and caption_overrides.get("position"):
            # A named Studio / AI-edit choice is a deliberate per-clip
            # choice too: resolve it to its numeric preset here, at the
            # seam where the choice was made, so the render can tell it
            # apart from the profile's own default position (which
            # stays a bare name on the EDL and yields to the layout
            # plan's face-aware height where a plan exists).
            from ..auto_director.clip_edits import POSITION_Y_BY_NAME

            resolved_y = POSITION_Y_BY_NAME.get(caption_overrides["position"])
            if resolved_y is not None:
                edl.setdefault("captions", {})["position_y"] = resolved_y
        if template_mode == "plain":
            # Plain — just cuts: a plain task's clips re-render plain.
            # Strip everything the fresh build (or the editor's stored
            # segment plan) carries and skip the music chain entirely —
            # without this, a QC corrective re-render would quietly add
            # the Auto bed back onto a clip whose task forbids it. An
            # explicit transition picked for THIS re-style is the user's
            # own edit, not template treatment, so it is restored with
            # the join durations the builder had assigned.
            from ..auto_director.template_mode import plainify_edl

            join_durations = [
                segment.get("transition_duration", 0)
                for segment in edl.get("segments", [])
            ]
            plainify_edl(edl)
            if transition is not None:
                for segment, join_duration in zip(
                    edl.get("segments", []), join_durations
                ):
                    if segment.get("index", 0) > 0:
                        segment["transition_in"] = transition
                        segment["transition_duration"] = join_duration
            return edl
        # Music = Auto on a silent profile still owes the re-render the
        # subtle Auto bed (same rule as the main pipeline). A caller's
        # explicit music_override is applied after this builder returns
        # and wins over the stamped bed. An explicit task music mode is
        # stronger than both: "off" silences the EDL outright, and "on"
        # suppresses the Auto bed (the profile's own bed, already on the
        # EDL, is what plays).
        if music_mode == "off":
            from ..media.music_library import apply_music_off

            apply_music_off(edl)
        elif music_mode != "on":
            from ..media.music_library import apply_auto_music_bed

            apply_auto_music_bed(edl, target_niche)
        return edl

    async def _stamp_brand_blocks(
        self, edl: Dict[str, Any], task: Dict[str, Any]
    ) -> None:
        """Stamp the task's Brand Kit blocks onto a freshly built EDL.

        Shared by the re-render seam and hook-preview generation so a
        preview carries exactly the branding picking the variant will
        produce — both resolve the kit the same way the main pipeline
        does (task > kit > profile; a kit that cannot be loaded renders
        unbranded, never failed). A Plain task is the exception: its
        contract is no kit dressing on any render, first or re-render
        (see plainify_edl).
        """
        if not task.get("brand_kit_id") or task.get("template_mode") == "plain":
            return
        try:
            from ..auto_director.brand_kit import resolve_brand_application
            from ..repositories.brand_kit_repository import (
                BrandKitRepository,
            )

            task_kit = await BrandKitRepository.get_kit(
                self.db,
                task.get("user_id") or "",
                task["brand_kit_id"],
            )
        except Exception:
            task_kit = None
        if not task_kit:
            return
        brand = resolve_brand_application(
            task_kit, user_id=task.get("user_id")
        )
        if brand.get("brand_template_override"):
            edl["brand_template_override"] = brand["brand_template_override"]
        if brand.get("brand_title"):
            edl["brand_title"] = brand["brand_title"]
        if brand.get("brand_watermark"):
            edl["brand_watermark"] = brand["brand_watermark"]
        if brand.get("title_style"):
            edl.setdefault("hook", {})["title_style"] = brand["title_style"]

    async def _acquire_clip_source(
        self,
        task: Dict[str, Any],
        task_id: str,
        source_ranges: list,
        download_quality: Optional[str],
    ) -> tuple[Optional[Path], Optional[Path]]:
        """Acquire a task's source video for a clip-scoped render.

        Processing cache first, then a ranged download, so only the
        clip's footage is fetched. Returns ``(video_path,
        downloaded_source)`` — ``downloaded_source`` is the temp file the
        caller must clean up (None when the cache supplied the video),
        and ``video_path`` is None when the source cannot be obtained.
        Shared by the re-render seam and hook-preview generation.
        """
        source_url = task.get("source_url")
        source_type = task.get("source_type")
        video_path: Optional[Path] = None
        downloaded_source: Optional[Path] = None
        processing_mode = (
            task.get("processing_mode") or self.config.default_processing_mode
        )
        cache_entry = await self.cache_repo.get_cache(
            self.db,
            self._build_cache_key(source_url, source_type, processing_mode, download_quality),
        )
        cached_video_path = cache_entry.get("video_path") if cache_entry else None
        if cached_video_path:
            candidate = await run_in_thread(MediaStorage().local_path, cached_video_path)
            if candidate.exists():
                video_path = candidate
        if video_path is None:
            if source_type == "youtube":
                downloaded = await self.video_service.download_video(
                    source_url,
                    quality=download_quality,
                    task_id=task_id,
                    ranges=source_ranges,
                )
                video_path = Path(downloaded) if downloaded else None
                downloaded_source = video_path
            elif source_type in {"twitch", "kick"}:
                video_path = await run_in_thread(
                    download_stream_sections, source_url,
                    [{"start_time": start, "end_time": end} for start, end in source_ranges],
                    task.get("source_duration_seconds"), task_id,
                )
                downloaded_source = video_path
            else:
                video_path = await resolve_upload_for_edit(self.video_service, source_url)
        return video_path, downloaded_source

    async def _resolve_render_vocals_stem(
        self, task: Dict[str, Any], video_path: Optional[Path]
    ) -> Optional[Path]:
        """Resolve the vocals stem for a re-render / hook preview /
        settings regeneration (N3).

        The same shared resolution the main pipeline uses
        (``media.separation.resolve_render_vocals_stem``): an opted-in
        task's clips draw their audio from the source's vocals stem, so
        a re-rendered clip is cut from the same clean vocals as the
        original instead of silently falling back to the raw mix. The
        flag is read off the task row exactly as the main pipeline
        reads it (``bool(...)`` — anything malformed reads as off), and
        the stem is resolved against the prepared source the render
        consumes, so a cache hit from the first render is reused and a
        ranged partial download gets a stem aligned to itself. Any
        failure returns None and the caller renders with the original
        audio, its call shape unchanged.
        """
        from ..media.separation import resolve_render_vocals_stem

        stem = await run_in_thread(
            resolve_render_vocals_stem,
            bool(task.get("separation_opt_in")),
            video_path,
        )
        if stem is not None:
            logger.info(
                "Re-render will draw audio from the vocals stem %s", stem
            )
        return stem

    @serialized_task_edit
    async def rerender_clip(
        self,
        task_id: str,
        clip_id: str,
        niche_override: str,
        caption_template: Optional[str] = None,
        transition: Optional[str] = None,
        music_override: Optional[str] = None,
        music_mode: Optional[str] = None,
        segment_plan: Optional[list] = None,
        caption_overrides: Optional[Dict[str, Any]] = None,
        font_user_id: Optional[str] = None,
        keep_ranges_override: Optional[list] = None,
        opening_override: Optional[Dict[str, Any]] = None,
        look_overrides: Optional[Dict[str, Any]] = None,
        audio_overrides: Optional[Dict[str, Any]] = None,
        voiceover_request: Optional[Dict[str, Any]] = None,
        sfx_overrides: Optional[list] = None,
    ) -> Dict[str, Any]:
        """Re-render one clip with a different niche profile.

        This is a re-style, not a re-pick: the clip's stored source ranges and
        transcript text are reused exactly, a new EDL is built from the target
        profile, and the render goes through the same ``create_single_clip``
        path (and deliverable bundle writer) as the original render.

        ``segment_plan``/``caption_overrides`` are the editor's additions:
        the plan replaces the EDL's segment layout (see
        ``src/auto_director/clip_edits.py``) and the overrides restyle the
        captions. When ``caption_overrides`` is None the stored plan's
        captions carry forward instead, so a plain re-style or a QC
        corrective re-render never silently drops the caption position
        already set. Either way the clip's persisted edit plan is refreshed
        from the EDL that actually rendered, so later edits build on the
        current layout.
        """
        target_niche = validate_niche_override(niche_override)
        if target_niche is None:
            raise ValueError("niche_override is required to re-render a clip")
        if caption_template is not None and caption_template not in get_template_names():
            raise ValueError(
                f"Invalid caption_template '{caption_template}'. "
                f"Valid values: {sorted(get_template_names())}."
            )
        if caption_overrides:
            override_template = caption_overrides.get("template")
            if (
                override_template is not None
                and override_template not in get_template_names()
            ):
                raise ValueError(
                    f"Invalid caption template '{override_template}'. "
                    f"Valid values: {sorted(get_template_names())}."
                )
            override_font = caption_overrides.get("font_family")
            if override_font is not None:
                # Same registry lookup the ASS builder resolves with:
                # bundled fonts by family name, user fonts by filename.
                from ..caption_templates import get_template as _get_caption_template
                from ..font_registry import find_font_path, get_available_fonts

                known = find_font_path(
                    override_font, allow_all_user_fonts=True
                ) is not None
                if not known:
                    resolved_template = _get_caption_template(
                        override_template
                        or caption_template
                        or "default"
                    )
                    if resolved_template.get("font_family") == override_font:
                        known = True
                if not known and font_user_id:
                    known = any(
                        entry.get("filename") == override_font
                        for entry in get_available_fonts(font_user_id)
                    )
                if not known:
                    raise ValueError(
                        f"Unknown caption font_family '{override_font}'"
                    )
        if transition is not None:
            from ..media.edl_render import TRANSITION_MAP

            if transition not in TRANSITION_MAP:
                raise ValueError(
                    f"Invalid transition '{transition}'. "
                    f"Valid values: {sorted(TRANSITION_MAP)}."
                )
        # Studio look overrides (transition / grade / VFX): validated here
        # so every caller of the seam gets the same rejection behaviour.
        from ..auto_director.look import normalize_look_overrides

        requested_look = normalize_look_overrides(look_overrides)
        # Audio Studio overrides (music / cleanup / loudness / fades):
        # same treatment. The voiceover request arrives pre-validated
        # by media.voiceover.normalize_voiceover_request at the API /
        # edit layer; here it is only interpreted (set / clear / carry).
        from ..auto_director.audio_studio import normalize_audio_overrides

        requested_audio = normalize_audio_overrides(audio_overrides)

        # SFX placements (the editor's SFX panel): same treatment — an
        # explicit list replaces (an empty list clears), no mention
        # carries the stored placements forward.
        from ..auto_director.sfx import normalize_sfx_placements

        requested_sfx = normalize_sfx_placements(sfx_overrides)

        clip = await self.clip_repo.get_clip_by_id(self.db, clip_id)
        if not clip or clip["task_id"] != task_id:
            raise ValueError("Clip not found")

        task = await self.task_repo.get_task_by_id(self.db, task_id)
        if not task:
            raise ValueError("Task not found")
        source_url = task.get("source_url")
        source_type = task.get("source_type")
        if not source_url or not source_type:
            raise ValueError("Task source URL is missing; cannot re-render clips")

        # The STORED template/music values are re-validated with the
        # same try/warn pattern as in regenerate_all_clips_for_task
        # and are used for every read below; the explicit music_mode
        # argument is a per-call choice the route already validated
        # and is used as-is when given.
        try:
            stored_music_mode = validate_music_mode(task.get("music_mode"))
        except ValueError:
            logger.warning(
                "Ignoring invalid stored music_mode %r on task %s",
                task.get("music_mode"),
                task_id,
            )
            stored_music_mode = None
        try:
            stored_template_mode = validate_template_mode(
                task.get("template_mode")
            )
        except ValueError:
            logger.warning(
                "Ignoring invalid stored template_mode %r on task %s",
                task.get("template_mode"),
                task_id,
            )
            stored_template_mode = None

        try:
            stored_framing_mode = validate_framing_mode(
                task.get("framing_mode")
            )
        except ValueError:
            logger.warning(
                "Ignoring invalid stored framing_mode %r on task %s",
                task.get("framing_mode"),
                task_id,
            )
            stored_framing_mode = None
        stored_layout_template, stored_layout_credit = _stored_layout(task)

        settings = await self._load_task_source_settings(task_id)
        # The task's stored text bands (decided once at creation) are
        # reused verbatim: a single-clip re-render must not recompute
        # different bands than its sibling clips rendered with.
        stored_text_bands = await self._load_task_text_bands(task_id)
        # Studio look resolution: an explicit look wins; otherwise the
        # look stored on the clip's edit plan carries forward, so a plain
        # re-style or an unrelated editor render never silently drops the
        # grade/VFX the user already set. A stored transition fills in
        # only when this call did not name one itself.
        effective_look = requested_look
        # The stored plan is also the carry-forward source for the Audio
        # Studio blocks; read it once when anything needs it. Captions
        # join that list: a stored position must survive a re-render
        # whose caller passed no overrides (F-B).
        previous_plan = None
        if (
            effective_look is None
            or requested_audio is None
            or voiceover_request is None
            or requested_sfx is None
            or caption_overrides is None
        ):
            previous_plan = await run_in_thread(
                load_clip_editor_settings,
                MediaStorage().local_path(clip["file_path"], metadata_only=True),
            )
        if effective_look is None and previous_plan:
            from ..auto_director.look import look_overrides_from_plan

            effective_look = look_overrides_from_plan(previous_plan)
        # Audio Studio resolution mirrors the look: explicit wins, the
        # stored block carries forward untouched otherwise.
        effective_audio = requested_audio
        if effective_audio is None and previous_plan:
            from ..auto_director.audio_studio import audio_overrides_from_plan

            effective_audio = audio_overrides_from_plan(previous_plan)
        # SFX resolution mirrors the audio: explicit wins (including the
        # explicit empty list), the stored placements carry forward.
        effective_sfx = requested_sfx
        if effective_sfx is None and previous_plan:
            from ..auto_director.sfx import sfx_placements_from_plan

            effective_sfx = sfx_placements_from_plan(previous_plan)
        # Caption resolution mirrors the look: an explicit override wins
        # (including an explicit template/font/position), and the stored
        # plan's captions carry forward untouched otherwise — exactly as
        # edit_clip resolves them — so a plain re-style, a QC corrective
        # re-render, or any other caller passing no overrides never
        # silently drops the caption position the user already set (F-B).
        effective_captions = caption_overrides
        if effective_captions is None and previous_plan:
            from ..auto_director.clip_edits import caption_overrides_from_plan

            effective_captions = caption_overrides_from_plan(previous_plan)
        # Voiceover resolution: an explicit spec replaces, {"clear": ...}
        # removes, and no mention carries the stored spec forward.
        effective_voiceover: Optional[Dict[str, Any]] = None
        if voiceover_request is not None:
            if not voiceover_request.get("clear"):
                effective_voiceover = {
                    "script": voiceover_request["script"],
                    "voice": voiceover_request["voice"],
                    "mode": voiceover_request["mode"],
                }
        elif previous_plan:
            from ..media.voiceover import voiceover_spec_from_plan

            stored_voiceover = voiceover_spec_from_plan(previous_plan)
            if stored_voiceover:
                effective_voiceover = {
                    "script": stored_voiceover["script"],
                    "voice": stored_voiceover["voice"],
                    "mode": stored_voiceover["mode"],
                }
        if (
            transition is None
            and effective_look
            and effective_look.get("transition")
        ):
            transition = effective_look["transition"]
        output_format = settings.get("output_format", "vertical")
        add_subtitles = settings.get("add_subtitles", True)
        download_quality, output_short_side = await self._media_limits(task.get("user_id"))

        source_ranges = await run_in_thread(self._get_clip_source_ranges, clip)
        bounds = source_range_bounds(source_ranges)
        if not bounds:
            raise ValueError("Clip source timing is unavailable")
        start_seconds, end_seconds = bounds

        # Acquire the source the same way caption edits do: processing cache
        # first, then a ranged download, so only this clip's footage is fetched.
        video_path, downloaded_source = await self._acquire_clip_source(
            task, task_id, source_ranges, download_quality
        )
        if video_path is None or not video_path.exists():
            raise ValueError(
                "The source video is no longer available. Upload it again to re-render this clip."
            )

        try:
            video_path = await self.video_service.prepare_source(video_path)
            # Voice/music separation (N3): a re-render from a
            # separation-opt-in task must cut from the same clean vocals
            # as the original — this seam never resolved the stem, so
            # re-renders silently fell back to the raw mix. Resolved
            # once here, against the prepared source the render
            # consumes; a missing stem leaves the historical call shape.
            render_vocals_stem = await self._resolve_render_vocals_stem(
                task, video_path
            )
            time_map = await run_in_thread(load_time_map, video_path)
            # Editor renders substitute the plan's kept footage for the
            # clip's stored ranges; every other re-render keeps them.
            effective_ranges = (
                [tuple(entry) for entry in keep_ranges_override]
                if keep_ranges_override
                else source_ranges
            )
            segment = {
                "start_time": self._seconds_to_mmss(start_seconds),
                "end_time": self._seconds_to_mmss(end_seconds),
                "keep_ranges": effective_ranges,
                "text": clip.get("text") or "",
                "relevance_score": clip.get("relevance_score", 0.5),
                "reasoning": clip.get("reasoning")
                or "Re-rendered with a new niche profile",
                "virality_score": clip.get("virality_score", 0),
                "hook_score": clip.get("hook_score", 0),
                "engagement_score": clip.get("engagement_score", 0),
                "value_score": clip.get("value_score", 0),
                "shareability_score": clip.get("shareability_score", 0),
                "hook_type": clip.get("hook_type"),
                "hook_title": clip.get("hook_title"),
                "niche_override": target_niche,
            }
            render_segment = (
                localize_segment(segment, time_map) if time_map else segment
            )
            edl = await run_in_thread(
                self._build_rerender_edl,
                clip,
                start_seconds,
                end_seconds,
                target_niche,
                transition,
                segment_plan,
                effective_captions,
                music_mode if music_mode is not None else stored_music_mode,
                stored_template_mode,
                task.get("broll_enabled"),
            )
            # B-roll carry: the clip's SAVED stock cutaways (the exact
            # footage the first render composited, resolved paths and
            # all) survive a re-style. Without this the fresh EDL only
            # re-plans the profile's queries from the degraded
            # transcript, and a re-render silently swaps or drops the
            # cutaways the user already saw. An editor segment plan
            # re-bases the placements onto the edited timeline through
            # the plan's source mapping; placements the edit cut away
            # disappear with their footage. Any failure here keeps the
            # freshly planned queries — B-roll never costs the render.
            try:
                saved_broll = await run_in_thread(self._get_clip_broll, clip)
                # The task's B-roll switch wins over the carry: with it
                # resolved Off, saved cutaways stay out of the re-render
                # and the freshly built (cue-free) EDL stands.
                if saved_broll and resolve_broll_enabled(
                    stored_template_mode, task.get("broll_enabled")
                ):
                    carried = self._carried_broll_entries(
                        saved_broll,
                        edl,
                        segment_plan,
                        source_duration=end_seconds - start_seconds,
                    )
                    if carried is not None:
                        edl["broll"] = carried
            except Exception:
                logger.warning(
                    "Could not carry saved B-roll into the re-render of "
                    "clip %s; keeping the planned queries",
                    clip_id,
                    exc_info=True,
                )
            # Render parity: the first render consumed the clip's
            # stored viral plan into its EDL (plan transitions on the
            # joins, the opening punch zoom, plan SFX merged) — a
            # re-render must consume the SAME plan the SAME way or an
            # untouched clip drifts from its first export. Explicit
            # choices on THIS call win over the plan: a named transition
            # (or an editor segment plan, whose joins the user owns)
            # suppresses the plan's transitions, and a picked hook
            # variant suppresses the plan's punch (the variant's own
            # punch is applied further below).
            stored_raw_plan = clip.get("clip_plan")
            if isinstance(stored_raw_plan, str) and stored_raw_plan:
                from dataclasses import replace as _dc_replace

                from ..auto_director.clip_plan import ClipPlan, OpeningTreatment
                from .video_service import VideoService

                try:
                    parsed_plan = ClipPlan.from_dict(json.loads(stored_raw_plan))
                except (TypeError, ValueError):
                    parsed_plan = None
                if parsed_plan is not None:
                    render_plan = parsed_plan
                    if transition is not None or segment_plan is not None:
                        render_plan = _dc_replace(
                            render_plan, transition_choices=[]
                        )
                    if opening_override is not None:
                        render_plan = _dc_replace(
                            render_plan, opening_treatment=OpeningTreatment()
                        )
                    try:
                        plan_profile = load_profile(target_niche)
                    except Exception:
                        plan_profile = {}
                    VideoService._consume_viral_plan(
                        edl, render_plan, plan_profile
                    )
            if effective_sfx:
                # The user's placements append AFTER any plan merge:
                # the SFX panel owns these events outright, so nothing
                # reshapes them. The renderer resolves the names
                # against assets/sfx and mixes them like any other
                # sfx_events entry.
                edl["sfx_events"] = [
                    *(edl.get("sfx_events") or []),
                    *effective_sfx,
                ]
            # Brand Kit parity with the main pipeline: when the clip's
            # task opted into a kit, the same brand blocks ride this
            # re-render's EDL (the render arguments below — caption
            # template and font colour — already resolve through their
            # own precedence and are not second-guessed here). Hook
            # previews stamp through the same helper, so a preview
            # shows the branding picking the variant will produce.
            await self._stamp_brand_blocks(edl, task)
            if music_override and stored_template_mode != "plain":
                # Mirror the main pipeline (VideoService.build_pipeline_edl):
                # an explicit user track beats the profile's mood, including
                # the audible defaults for otherwise-silent profiles. A
                # Plain task's stored pick is a music control, and Plain
                # treats every music control as Off; per-clip Audio
                # Studio choices on THIS call are separate and still
                # apply below.
                from ..media.music_library import apply_music_override

                apply_music_override(edl, music_override)
            if effective_look is not None:
                # Stamp the resolved look (profile grade first, the
                # user's fields on top) onto the EDL for the renderer.
                # A fully neutral resolution stamps nothing, leaving the
                # render byte-identical to a look-less one.
                from ..auto_director.look import build_edl_look
                from ..media.lut_library import find_lut_file

                target_profile = load_profile(target_niche)
                lut_name = (effective_look.get("grade") or {}).get("lut")
                lut_path = find_lut_file(lut_name) if lut_name else None
                if lut_name and lut_path is None:
                    logger.warning(
                        "Clip %s references missing LUT %s; rendering "
                        "without it",
                        clip_id,
                        lut_name,
                    )
                edl_look = build_edl_look(
                    effective_look,
                    (target_profile.get("color") or {}).get("lut"),
                    lut_file=str(lut_path) if lut_path else None,
                )
                if edl_look is not None:
                    edl["look"] = edl_look
            if effective_audio is not None:
                # The Audio Studio's music-track choice applies after
                # the plain music_override argument: the panel is the
                # newer, per-clip surface, so its pick wins for this
                # render. "none" silences the bed outright (including
                # the Auto bed _build_rerender_edl may have stamped);
                # "auto" leaves whatever the earlier steps decided.
                from ..auto_director.audio_studio import (
                    MUSIC_TRACK_AUTO,
                    MUSIC_TRACK_NONE,
                    build_edl_audio_studio,
                )
                from ..media.music_library import apply_music_override

                audio_track = effective_audio.get("music_track")
                if audio_track == MUSIC_TRACK_NONE:
                    audio_block = edl.setdefault("audio", {})
                    audio_block["music_track"] = ""
                    audio_block["music_mood"] = "none"
                    audio_block["music_volume"] = 0.0
                    if "music_volume_0_to_1" in audio_block:
                        audio_block["music_volume_0_to_1"] = 0.0
                    audio_block["music_source"] = "user_none"
                elif audio_track and audio_track != MUSIC_TRACK_AUTO:
                    apply_music_override(edl, audio_track)
                # The render-ready studio block (volume / ducking /
                # cleanup / loudness / fades). An all-default block
                # stamps nothing, leaving the render byte-identical.
                edl_studio = build_edl_audio_studio(effective_audio)
                if edl_studio is not None:
                    edl["audio_studio"] = edl_studio
            stored_voiceover: Optional[Dict[str, Any]] = None
            if effective_voiceover is not None:
                # Synthesize (or reuse the cached WAV) and stamp the
                # narration onto the EDL. Synthesis failure renders
                # without narration rather than losing the clip, but
                # the spec still persists so a later render can retry.
                from ..media.voiceover import (
                    cache_filename,
                    voiceover_edl_block,
                )

                stored_voiceover = {
                    **effective_voiceover,
                    "file": cache_filename(
                        effective_voiceover["script"],
                        effective_voiceover["voice"],
                    ),
                }
                vo_block = await run_in_thread(
                    voiceover_edl_block, effective_voiceover
                )
                if vo_block is not None:
                    edl["voiceover"] = vo_block
            plan_caption_style: Optional[str] = None
            if caption_template is None:
                # The stored per-clip plan's caption style is the default
                # for a re-style — the engine chose it for THIS clip's
                # content. An explicit user choice always wins, and a
                # stored name the registry no longer knows is ignored.
                from ..auto_director.clip_plan import ClipPlan

                stored_plan = clip.get("clip_plan")
                if isinstance(stored_plan, str) and stored_plan:
                    try:
                        parsed_plan = ClipPlan.from_dict(json.loads(stored_plan))
                    except (TypeError, ValueError):
                        parsed_plan = None
                    if (
                        parsed_plan is not None
                        and parsed_plan.caption_style is not None
                        and parsed_plan.caption_style.name in get_template_names()
                    ):
                        plan_caption_style = parsed_plan.caption_style.name
            # Caption Studio overrides beat the explicit template argument,
            # which beats the plan's stored style, which beats the task's
            # stored styling — the same precedence the rest of the render
            # arguments follow below. ``overrides`` is the resolved
            # caption block: an explicit argument, or the stored plan's
            # captions carried forward above (F-B).
            overrides = effective_captions or {}
            effective_caption_template = (
                overrides.get("template")
                or caption_template
                or plan_caption_style
                or task.get("caption_template")
                or "default"
            )
            if caption_template is not None:
                edl["captions"]["preset_id"] = caption_template
            elif plan_caption_style is not None:
                edl["captions"]["preset_id"] = plan_caption_style
            if overrides.get("template") is not None:
                edl["captions"]["preset_id"] = overrides["template"]
            if opening_override:
                # A picked hook variant's opening treatment (Lane K): the
                # punch level/offset the preview rendered with, applied
                # through the same helper the previews used so the full
                # clip's opening matches what the user picked.
                from ..auto_director.hook_variants import apply_opening_punch

                try:
                    punch_profile = load_profile(target_niche)
                except Exception:
                    punch_profile = {}
                apply_opening_punch(
                    edl,
                    level=str(opening_override.get("punch_zoom") or ""),
                    at_offset=float(
                        opening_override.get("punch_at_offset") or 0.0
                    ),
                    profile=punch_profile,
                )
            # Brief clamp kwargs are only passed when a brief actually
            # constrains this task, so non-brief re-renders keep the exact
            # historic call shape.
            brief_allowed = self._brief_allowed_ranges(task, time_map)
            brief_excluded = self._brief_excluded_ranges(task, time_map)
            clip_info = await self.video_service.create_single_clip(
                video_path,
                render_segment,
                int(clip.get("clip_order", 1)) - 1,
                Path(self.config.temp_dir) / "clips",
                overrides.get("font_family") or task.get("font_family") or None,
                overrides.get("font_size") or task.get("font_size") or None,
                overrides.get("font_color") or task.get("font_color") or None,
                effective_caption_template,
                output_format,
                add_subtitles,
                normalize_clip_cleanup_settings(
                    settings.get("cut_long_pauses"),
                    settings.get("pause_threshold_ms"),
                    settings.get("remove_filler_words"),
                    settings.get("filtered_words"),
                ),
                max_short_side=output_short_side,
                max_clip_seconds=max(
                    clip_duration(normalize_clip_length(settings.get("clip_length"))).maximum,
                    MAX_RENDERED_CLIP_SECONDS,
                ),
                include_broll=False,
                edl=edl,
                niche_override=target_niche,
                project_niche_override=task.get("niche_override"),
                classifier_niche=None,
                **({"allowed_ranges": brief_allowed} if brief_allowed else {}),
                **({"excluded_ranges": brief_excluded} if brief_excluded else {}),
                **(
                    {"vocals_stem": render_vocals_stem}
                    if render_vocals_stem is not None
                    else {}
                ),
                **(
                    {"task_text_bands": stored_text_bands}
                    if stored_text_bands is not None
                    else {}
                ),
                # Re-renders keep the task's framing choice (fit/crop)
                # instead of silently reverting to the template default.
                **(
                    {"framing_mode": stored_framing_mode}
                    if stored_framing_mode is not None
                    else {}
                ),
                **(
                    {
                        "layout_template": stored_layout_template,
                        "layout_credit": stored_layout_credit,
                    }
                    if stored_layout_template
                    else {}
                ),
            )
            if clip_info is None:
                raise ValueError(
                    "Could not re-render the clip. Your existing clip is unchanged."
                )
            if time_map:
                clip_info = await run_in_thread(
                    restore_clip_timeline, clip_info, time_map, segment
                )
        finally:
            if downloaded_source is not None:
                self._cleanup_source_video(downloaded_source, source_type, source_url)

        await self.clip_repo.update_clip(
            self.db,
            clip_id,
            clip_info["filename"],
            clip_info["path"],
            clip_info["start_time"],
            clip_info["end_time"],
            clip_info["duration"],
            clip.get("text") or "",
        )
        await self.clip_repo.update_clip_niche_override(
            self.db, clip_id, target_niche
        )
        # Persist the edit plan that actually rendered (its segment layout
        # plus any caption overrides in force) next to the new file, so the
        # editor always opens on the clip's current truth.
        try:
            from ..auto_director.clip_edits import (
                attach_source_shares,
                build_edit_plan,
            )

            # The captions that actually rendered (explicit argument, or
            # the stored plan's captions carried forward above) are what
            # the new plan records, so the editor and the file never
            # diverge — the old reload-here-only-for-plain-re-styles
            # dropped the carried captions from editor renders (F-B).
            captions = effective_captions
            rendered_segments = edl.get("segments") or []
            if segment_plan is None:
                # A plain re-style renders the stored ranges unchanged, so
                # the plan's footage mapping is (re)derived from them; an
                # editor render's plan already carries its mapping.
                rendered_segments = await run_in_thread(
                    attach_source_shares, rendered_segments, source_ranges
                )
            await run_in_thread(
                save_clip_editor_settings,
                Path(clip_info["path"]),
                build_edit_plan(
                    rendered_segments,
                    niche=target_niche,
                    captions=captions,
                    look=effective_look,
                    audio=effective_audio,
                    voiceover=stored_voiceover,
                    sfx=effective_sfx,
                ),
            )
        except Exception:
            logger.warning(
                "Could not persist the edit plan for clip %s", clip_id,
                exc_info=True,
            )
        refreshed = await self.clip_repo.get_clip_by_id(self.db, clip_id) or {}
        refreshed["niche_profile"] = target_niche
        return refreshed


    # --- Hook-variant previews (Lane K) ------------------------------------
    #
    # Short alternative openings for a finished clip, derived
    # deterministically from the clip + its stored ClipPlan (see
    # src/auto_director/hook_variants.py). Previews render through the
    # same EDL path as a re-render, truncated to the first few seconds;
    # picking one re-renders the full clip through the re-render seam
    # with the variant's segment layout and opening treatment.

    async def _hook_variant_context(
        self, task_id: str, clip_id: str
    ) -> Dict[str, Any]:
        """Clip, task, niche, current segments and source ranges.

        Segments seed exactly like the editor's: the persisted edit plan
        wins, a fresh EDL build covers clips that predate it, and the
        footage mapping is attached when the plan predates it.
        """
        from ..auto_director.clip_edits import (
            attach_source_shares,
            plan_has_source_mapping,
            plan_segments,
        )
        from ..auto_director.niche import resolve_effective_profile

        clip = await self.clip_repo.get_clip_by_id(self.db, clip_id)
        if not clip or clip["task_id"] != task_id:
            raise ValueError("Clip not found")
        task = await self.task_repo.get_task_by_id(self.db, task_id)
        if not task:
            raise ValueError("Task not found")

        niche = resolve_effective_profile(
            clip.get("niche_override"), task.get("niche_override"), None
        )
        source_ranges = await run_in_thread(self._get_clip_source_ranges, clip)
        bounds = source_range_bounds(source_ranges)
        if not bounds:
            raise ValueError("Clip source timing is unavailable")

        plan_record = await run_in_thread(
            load_clip_editor_settings,
            MediaStorage().local_path(clip["file_path"], metadata_only=True),
        )
        if plan_record:
            segments = plan_segments(plan_record)
        else:
            seed = await run_in_thread(
                self._build_rerender_edl,
                clip,
                bounds[0],
                bounds[1],
                niche,
                None,
            )
            segments = seed.get("segments") or []
        if not segments:
            raise ValueError("This clip has no segments to preview")
        if not plan_has_source_mapping({"segments": segments}):
            try:
                segments = await run_in_thread(
                    attach_source_shares, segments, source_ranges
                )
            except ValueError:
                # Unmapped plans keep the seam's proportional mapping —
                # the same fallback an editor re-render would use.
                pass
        return {
            "clip": clip,
            "task": task,
            "niche": niche,
            "segments": segments,
            "source_ranges": source_ranges,
            "bounds": bounds,
            # The stored editor plan, so callers that build an EDL can
            # carry its caption position forward (F-B follow-up 2) instead
            # of previewing the profile default.
            "editor_plan": plan_record,
        }

    @staticmethod
    def _public_hook_preview(
        preview: Dict[str, Any], clip: Optional[Dict[str, Any]]
    ) -> Dict[str, Any]:
        """The API shape of a preview: no file paths or layout params."""
        applied = (clip or {}).get("hook_variant") or "current"
        applied_batch = (clip or {}).get("hook_variant_batch")
        selected = preview["variant"] == applied and (
            applied_batch is None or applied_batch == preview["batch_id"]
        )
        return {
            "id": preview["id"],
            "task_id": preview["task_id"],
            "clip_id": preview["clip_id"],
            "batch_id": preview["batch_id"],
            "variant": preview["variant"],
            "label": preview["label"],
            "description": preview["description"],
            "duration": preview["duration"],
            "video_url": (
                f"/tasks/{preview['task_id']}/clips/{preview['clip_id']}"
                f"/hook-variants/{preview['id']}/file"
            ),
            "created_at": preview.get("created_at"),
            "selected": selected,
        }

    async def list_hook_previews(
        self, task_id: str, clip_id: str
    ) -> Dict[str, Any]:
        """The clip's latest hook-preview batch (possibly empty)."""
        clip = await self.clip_repo.get_clip_by_id(self.db, clip_id)
        if not clip or clip["task_id"] != task_id:
            raise ValueError("Clip not found")
        previews = await self.hook_preview_repo.list_previews_for_clip(
            self.db, clip_id
        )
        return {
            "clip_id": clip_id,
            "hook_variant": clip.get("hook_variant"),
            "previews": [
                self._public_hook_preview(preview, clip)
                for preview in previews
            ],
        }

    async def generate_hook_previews(
        self, task_id: str, clip_id: str
    ) -> Dict[str, Any]:
        """Render the ~6s hook previews for a clip's derived variants.

        Each variant renders through the real re-render EDL path (same
        builder, same plan consumption rules, truncated to the preview
        window) so what the user compares is what picking it produces.
        Records replace the clip's previous batch; replaced preview
        files are deleted.
        """
        from ..auto_director import hook_variants as hv
        from ..auto_director.clip_edits import kept_source_ranges
        from ..clip_source_map import normalize_source_ranges, slice_source_ranges

        context = await self._hook_variant_context(task_id, clip_id)
        clip = context["clip"]
        task = context["task"]
        # The stored plan's caption position is the preview default,
        # exactly like a re-render (F-B): the previews render through
        # the same EDL builder, so a stored Top must not preview as
        # the profile-default Bottom.
        from ..auto_director.clip_edits import caption_overrides_from_plan

        preview_captions = caption_overrides_from_plan(context.get("editor_plan"))
        # Stored template/music values get the same re-validate-and-
        # warn treatment as the other re-render reads (see
        # rerender_clip): warn + documented default on garbage.
        try:
            stored_music_mode = validate_music_mode(task.get("music_mode"))
        except ValueError:
            logger.warning(
                "Ignoring invalid stored music_mode %r on task %s",
                task.get("music_mode"),
                task_id,
            )
            stored_music_mode = None
        try:
            stored_template_mode = validate_template_mode(
                task.get("template_mode")
            )
        except ValueError:
            logger.warning(
                "Ignoring invalid stored template_mode %r on task %s",
                task.get("template_mode"),
                task_id,
            )
            stored_template_mode = None

        try:
            stored_framing_mode = validate_framing_mode(
                task.get("framing_mode")
            )
        except ValueError:
            logger.warning(
                "Ignoring invalid stored framing_mode %r on task %s",
                task.get("framing_mode"),
                task_id,
            )
            stored_framing_mode = None
        stored_layout_template, stored_layout_credit = _stored_layout(task)
        niche = context["niche"]
        source_ranges = context["source_ranges"]
        start_seconds, end_seconds = context["bounds"]

        plan = hv.plan_from_clip(clip)
        try:
            profile = load_profile(niche)
        except Exception:
            profile = {}
        specs = hv.derive_variants(
            context["segments"],
            plan,
            profile,
            hook_variant_applied=clip.get("hook_variant"),
        )

        settings = await self._load_task_source_settings(task_id)
        output_format = settings.get("output_format", "vertical")
        add_subtitles = settings.get("add_subtitles", True)
        download_quality, output_short_side = await self._media_limits(
            task.get("user_id")
        )

        video_path, downloaded_source = await self._acquire_clip_source(
            task, task_id, source_ranges, download_quality
        )
        if video_path is None or not video_path.exists():
            raise ValueError(
                "The source video is no longer available. Upload it again "
                "to build hook previews for this clip."
            )

        # The stored plan's caption style is the preview default, exactly
        # like a re-style (an unknown stored name is ignored).
        effective_caption_template = task.get("caption_template") or "default"
        if (
            plan is not None
            and plan.caption_style is not None
            and plan.caption_style.name in get_template_names()
        ):
            effective_caption_template = plan.caption_style.name

        batch_id = uuid4().hex
        preview_dir = (
            Path(self.config.temp_dir)
            / "clips"
            / "hook_previews"
            / clip_id
            / batch_id
        )
        records: list[Dict[str, Any]] = []
        try:
            video_path = await self.video_service.prepare_source(video_path)
            # Voice/music separation (N3): previews render through this
            # same seam, so they share the re-render gap — a preview of
            # a separation-opt-in clip must sound like the clip picking
            # the variant will produce. Resolved once for the batch.
            preview_vocals_stem = await self._resolve_render_vocals_stem(
                task, video_path
            )
            # Opening strategies (Workstream C) need timed transcript
            # evidence. When the source's cached transcript is available,
            # re-derive the variants with its clip-relative words so the
            # strategy previews join this batch; without it, the batch
            # is exactly the legacy variants and every strategy reports
            # unavailable rather than being faked.
            try:
                from ..media.timeline import get_words_for_keep_ranges
                from ..media.transcription import load_cached_transcript_data

                transcript_data = await run_in_thread(
                    load_cached_transcript_data, video_path
                )
                if transcript_data:
                    strategy_words = await run_in_thread(
                        get_words_for_keep_ranges, transcript_data, source_ranges
                    )
                    if strategy_words:
                        specs = hv.derive_variants(
                            context["segments"],
                            plan,
                            profile,
                            hook_variant_applied=clip.get("hook_variant"),
                            words=strategy_words,
                        )
            except Exception:
                logger.warning(
                    "Opening-strategy derivation failed; keeping the "
                    "standard hook variants",
                    exc_info=True,
                )
            time_map = await run_in_thread(load_time_map, video_path)
            for spec in specs:
                full_ranges = await run_in_thread(
                    kept_source_ranges, spec["segments"], source_ranges
                )
                if not full_ranges:
                    if spec["variant"] == hv.VARIANT_CURRENT:
                        raise ValueError("Clip source timing is unavailable")
                    continue
                preview_ranges = slice_source_ranges(
                    normalize_source_ranges(full_ranges),
                    0.0,
                    hv.PREVIEW_SECONDS,
                )
                edl = await run_in_thread(
                    self._build_rerender_edl,
                    clip,
                    start_seconds,
                    end_seconds,
                    niche,
                    None,
                    spec["segments"],
                    preview_captions,
                    stored_music_mode,
                    stored_template_mode,
                    task.get("broll_enabled"),
                )
                if task.get("music_override") and stored_template_mode != "plain":
                    from ..media.music_library import apply_music_override

                    apply_music_override(edl, task["music_override"])
                # Brand Kit parity: the preview carries the same kit
                # blocks the full render and the re-render stamp, so
                # what the user compares is what picking the variant
                # produces (band, watermark, caption treatment).
                await self._stamp_brand_blocks(edl, task)
                hv.consume_plan_for_variant(edl, spec, plan, profile)
                hv.truncate_edl_for_preview(edl)
                segment = {
                    "start_time": self._seconds_to_mmss(start_seconds),
                    "end_time": self._seconds_to_mmss(end_seconds),
                    "keep_ranges": preview_ranges,
                    "text": clip.get("text") or "",
                    "relevance_score": clip.get("relevance_score", 0.5),
                    "reasoning": clip.get("reasoning")
                    or "Hook variant preview",
                    "virality_score": clip.get("virality_score", 0),
                    "hook_score": clip.get("hook_score", 0),
                    "engagement_score": clip.get("engagement_score", 0),
                    "value_score": clip.get("value_score", 0),
                    "shareability_score": clip.get("shareability_score", 0),
                    "hook_type": clip.get("hook_type"),
                    "hook_title": clip.get("hook_title"),
                    "niche_override": niche,
                }
                render_segment = (
                    localize_segment(segment, time_map) if time_map else segment
                )
                clip_info = await self.video_service.create_single_clip(
                    video_path,
                    render_segment,
                    int(clip.get("clip_order", 1)) - 1,
                    preview_dir,
                    task.get("font_family") or None,
                    task.get("font_size") or None,
                    task.get("font_color") or None,
                    effective_caption_template,
                    output_format,
                    add_subtitles,
                    normalize_clip_cleanup_settings(
                        settings.get("cut_long_pauses"),
                        settings.get("pause_threshold_ms"),
                        settings.get("remove_filler_words"),
                        settings.get("filtered_words"),
                    ),
                    max_short_side=output_short_side,
                    max_clip_seconds=max(
                        clip_duration(
                            normalize_clip_length(settings.get("clip_length"))
                        ).maximum,
                        MAX_RENDERED_CLIP_SECONDS,
                    ),
                    include_broll=False,
                    edl=edl,
                    niche_override=niche,
                    project_niche_override=task.get("niche_override"),
                    classifier_niche=None,
                    **(
                        {"vocals_stem": preview_vocals_stem}
                        if preview_vocals_stem is not None
                        else {}
                    ),
                    # Previews render with the task's framing choice too,
                    # so what the user compares is what picking produces.
                    **(
                        {"framing_mode": stored_framing_mode}
                        if stored_framing_mode is not None
                        else {}
                    ),
                    **(
                        {
                            "layout_template": stored_layout_template,
                            "layout_credit": stored_layout_credit,
                        }
                        if stored_layout_template
                        else {}
                    ),
                )
                if clip_info is None:
                    if spec["variant"] == hv.VARIANT_CURRENT:
                        raise ValueError(
                            "Could not render hook previews. "
                            "Your clip is unchanged."
                        )
                    continue
                records.append(
                    {
                        "id": uuid4().hex,
                        "task_id": task_id,
                        "clip_id": clip_id,
                        "batch_id": batch_id,
                        "variant": spec["variant"],
                        "label": spec["label"],
                        "description": spec["description"],
                        "params": {
                            "segments": spec["segments"],
                            "punch_zoom": spec["punch_zoom"],
                            "punch_at_offset": spec["punch_at_offset"],
                            "hook_timestamp": spec["hook_timestamp"],
                            "niche": niche,
                            "strategy": spec.get("strategy"),
                            "reorders": bool(spec.get("reorders")),
                            "trims_head": bool(spec.get("trims_head")),
                        },
                        "file_name": clip_info["filename"],
                        "file_path": clip_info["path"],
                        "duration": clip_info["duration"],
                    }
                )
        finally:
            if downloaded_source is not None:
                self._cleanup_source_video(
                    downloaded_source,
                    task.get("source_type"),
                    task.get("source_url"),
                )
        if not records:
            raise ValueError(
                "Could not render hook previews. Your clip is unchanged."
            )
        removed = await self.hook_preview_repo.replace_previews_for_clip(
            self.db, clip_id, records
        )
        for old in removed:
            try:
                Path(old["file_path"]).unlink(missing_ok=True)
            except OSError:
                pass
        return {
            "batch_id": batch_id,
            "previews": [
                self._public_hook_preview(record, clip) for record in records
            ],
        }

    @serialized_task_edit
    async def apply_hook_variant(
        self, task_id: str, clip_id: str, preview_id: str
    ) -> Dict[str, Any]:
        """Apply a picked hook preview to the full clip.

        The preview's snapshotted segment layout goes through the
        re-render seam exactly like an editor layout (segment plan +
        kept ranges), with the variant's opening treatment passed as the
        seam's opening override; the pick is then recorded on the clip
        so it stays visible on every later fetch. A failed re-render
        leaves the clip — and the recorded pick — untouched.
        """
        from ..auto_director import hook_variants as hv
        from ..auto_director.clip_edits import (
            caption_overrides_from_plan,
            kept_source_ranges,
            normalize_segments,
        )
        from ..auto_director.niche import resolve_effective_profile

        clip = await self.clip_repo.get_clip_by_id(self.db, clip_id)
        if not clip or clip["task_id"] != task_id:
            raise ValueError("Clip not found")
        task = await self.task_repo.get_task_by_id(self.db, task_id)
        if not task:
            raise ValueError("Task not found")
        preview = await self.hook_preview_repo.get_preview(self.db, preview_id)
        if (
            not preview
            or preview["clip_id"] != clip_id
            or preview["task_id"] != task_id
        ):
            raise ValueError("Hook preview not found")

        params = preview.get("params") or {}
        segments = params.get("segments") or []
        if not segments:
            raise ValueError(
                "This hook preview has no opening layout to apply"
            )
        segments = normalize_segments(segments)
        niche = params.get("niche") or resolve_effective_profile(
            clip.get("niche_override"), task.get("niche_override"), None
        )
        source_ranges = await run_in_thread(self._get_clip_source_ranges, clip)
        kept_ranges = await run_in_thread(
            kept_source_ranges, segments, source_ranges
        )
        if not kept_ranges:
            raise ValueError("Clip source timing is unavailable")

        plan_record = await run_in_thread(
            load_clip_editor_settings,
            MediaStorage().local_path(clip["file_path"], metadata_only=True),
        )
        captions = (
            caption_overrides_from_plan(plan_record) if plan_record else None
        )

        opening_override: Optional[Dict[str, Any]] = None
        variant = preview["variant"]
        if variant == hv.VARIANT_PUNCH and params.get("punch_zoom"):
            opening_override = {
                "punch_zoom": params["punch_zoom"],
                "punch_at_offset": params.get("punch_at_offset") or 0.0,
            }
        elif variant == hv.VARIANT_COLD_OPEN or (
            variant in hv.STRATEGY_KINDS
            and (params.get("reorders") or params.get("trims_head"))
        ):
            # The plan's opening punch belongs on the hook moment, which
            # leads the reordered (or head-trimmed) clip — the same
            # treatment the preview rendered with.
            plan = hv.plan_from_clip(clip)
            if (
                plan is not None
                and plan.source == "llm"
                and plan.opening_treatment.punch_zoom != "none"
            ):
                opening_override = {
                    "punch_zoom": plan.opening_treatment.punch_zoom,
                    "punch_at_offset": 0.0,
                }

        refreshed = await self.rerender_clip(
            task_id,
            clip_id,
            niche,
            music_override=task.get("music_override"),
            segment_plan=segments,
            caption_overrides=captions,
            font_user_id=task.get("user_id"),
            keep_ranges_override=kept_ranges,
            opening_override=opening_override,
        )
        applied_variant = None if variant == hv.VARIANT_CURRENT else variant
        await self.clip_repo.update_clip_hook_variant(
            self.db, clip_id, applied_variant, preview["batch_id"]
        )
        refreshed["hook_variant"] = applied_variant
        refreshed["hook_variant_batch"] = preview["batch_id"]
        return refreshed

    def _look_display(
        self,
        plan: Optional[Dict[str, Any]],
        segments: Optional[list],
        niche: Optional[str],
    ) -> Dict[str, Any]:
        """The studio panels' initial state for one clip.

        ``grade``/``vfx`` are the EFFECTIVE values the next render would
        use (the profile's named look first, the stored user fields on
        top); ``transition`` is the join style the clip currently renders
        with; ``stored`` says whether the user has saved a look at all.
        """
        from ..auto_director.look import (
            canonical_transition,
            effective_grade,
            effective_vfx,
            look_overrides_from_plan,
        )

        stored = look_overrides_from_plan(plan)
        profile: Dict[str, Any] = {}
        if niche:
            try:
                profile = load_profile(niche)
            except Exception:
                profile = {}
        lut = (profile.get("color") or {}).get("lut")
        transition = (stored or {}).get("transition")
        if transition is None:
            joins = [
                entry for entry in (segments or []) if isinstance(entry, dict)
            ][1:]
            if joins:
                transition = canonical_transition(joins[0].get("transition_in"))
            if transition is None:
                transition = canonical_transition(
                    (profile.get("transitions") or {}).get("default")
                )
            if transition is None:
                transition = "hard_cut"
        grade = effective_grade(lut, (stored or {}).get("grade"))
        lut_pick = grade.pop("lut", None)
        return {
            "transition": transition,
            "grade": grade,
            "vfx": effective_vfx((stored or {}).get("vfx")),
            "profile_lut": lut if isinstance(lut, str) else "none",
            # The imported .cube LUT currently acting as the grade's
            # base (a stored filename), or None for the named look.
            "lut": lut_pick if isinstance(lut_pick, str) else None,
            "stored": stored is not None,
        }

    def _sfx_display(
        self,
        plan: Optional[Dict[str, Any]],
    ) -> Dict[str, Any]:
        """The SFX panel's initial state for one clip.

        ``placements`` are the user's stored placements (the exact list
        the next render will append to the EDL's ``sfx_events``);
        ``library`` is every SFX file the renderer can resolve, with
        durations for the picker.
        """
        from ..auto_director.sfx import sfx_placements_from_plan
        from ..media.sfx_library import list_sfx_tracks

        return {
            "placements": sfx_placements_from_plan(plan) or [],
            "library": list_sfx_tracks(),
        }

    def _audio_display(
        self,
        plan: Optional[Dict[str, Any]],
        task_music_override: Optional[str],
        niche: Optional[str],
    ) -> Dict[str, Any]:
        """The Audio Studio panel's initial state for one clip.

        Every field is the EFFECTIVE value the next render would use:
        the stored per-clip overrides where the user set them, the
        profile's own audio values (or the audible pick defaults for a
        silent profile) underneath. ``voiceover`` carries the stored
        spec only when the deployment flag is on — a disabled feature
        shows no surface at all.
        """
        from ..auto_director.audio_studio import audio_overrides_from_plan
        from ..media.music_library import (
            OVERRIDE_DEFAULT_DUCKING_DB,
            OVERRIDE_DEFAULT_VOLUME,
            list_music_tracks,
        )
        from ..media.voiceover import (
            VOICEOVER_VOICES,
            voiceover_enabled,
            voiceover_spec_from_plan,
        )

        stored = audio_overrides_from_plan(plan)
        profile: Dict[str, Any] = {}
        if niche:
            try:
                profile = load_profile(niche)
            except Exception:
                profile = {}
        profile_audio = profile.get("audio") or {}
        try:
            profile_volume = float(
                profile_audio.get(
                    "music_volume_0_to_1",
                    profile_audio.get("music_volume", 0.0),
                )
                or 0.0
            )
        except (TypeError, ValueError):
            profile_volume = 0.0
        try:
            profile_ducking = abs(
                float(profile_audio.get("ducking_db", 0.0) or 0.0)
            )
        except (TypeError, ValueError):
            profile_ducking = 0.0
        # A silent profile's sliders open on the pick defaults — the
        # values apply_music_override would stamp if the user chose a
        # track without touching the sliders.
        if profile_volume <= 0:
            profile_volume = OVERRIDE_DEFAULT_VOLUME
        if profile_ducking <= 0:
            profile_ducking = abs(OVERRIDE_DEFAULT_DUCKING_DB)
        stored = stored or {}
        enabled = voiceover_enabled()
        spec = voiceover_spec_from_plan(plan) if enabled else None
        return {
            "music_track": stored.get("music_track")
            or task_music_override
            or "auto",
            "music_volume": stored.get(
                "music_volume", round(profile_volume * 100)
            ),
            "ducking_db": stored.get(
                "ducking_db", round(profile_ducking)
            ),
            "noise_reduction": stored.get("noise_reduction", 0),
            "voice_clarity": stored.get("voice_clarity", 0),
            "loudness": stored.get("loudness", "auto"),
            "fade_in": stored.get("fade_in", 0),
            "fade_out": stored.get("fade_out", 0),
            "stored": bool(stored),
            "tracks": [
                {
                    "filename": track["filename"],
                    "display_name": track["display_name"],
                    "source": track["source"],
                }
                for track in list_music_tracks(probe_durations=False)
            ],
            "voiceover_enabled": enabled,
            "voiceover": (
                {
                    "script": spec["script"],
                    "voice": spec["voice"],
                    "mode": spec["mode"],
                }
                if spec
                else None
            ),
            "voices": [
                {"name": name, "label": info["label"]}
                for name, info in VOICEOVER_VOICES.items()
            ]
            if enabled
            else [],
        }

    async def get_clip_edit_plan(
        self, task_id: str, clip_id: str
    ) -> Dict[str, Any]:
        """The clip's current edit plan, for the editor panel.

        The persisted plan (written by every re-render) wins; a clip that
        predates the editor gets a plan derived on the spot from a fresh
        EDL build for its current niche — the same layout a re-style
        would render — without persisting anything.
        """
        from ..auto_director.clip_edits import (
            attach_source_shares,
            build_edit_plan,
            plan_has_source_mapping,
        )
        from ..auto_director.look import grade_presets, transition_catalog
        from ..auto_director.niche import resolve_effective_profile

        clip = await self.clip_repo.get_clip_by_id(self.db, clip_id)
        if not clip or clip["task_id"] != task_id:
            raise ValueError("Clip not found")
        task = await self.task_repo.get_task_by_id(self.db, task_id)
        if not task:
            raise ValueError("Task not found")

        plan = await run_in_thread(
            load_clip_editor_settings,
            MediaStorage().local_path(clip["file_path"], metadata_only=True),
        )
        if plan:
            if not plan_has_source_mapping(plan):
                # Plans written before footage mapping existed get their
                # segments bound to the clip's current ranges on read, so
                # the next edit moves real footage.
                try:
                    ranges = await run_in_thread(
                        self._get_clip_source_ranges, clip
                    )
                    plan = {
                        **plan,
                        "segments": await run_in_thread(
                            attach_source_shares,
                            plan.get("segments") or [],
                            ranges,
                        ),
                    }
                except ValueError:
                    pass
            niche = (
                plan.get("niche")
                or clip.get("niche_override")
                or task.get("niche_override")
            )
            return {
                "clip_id": clip_id,
                "plan": plan,
                "persisted": True,
                "niche": niche,
                "look": self._look_display(plan, plan.get("segments"), niche),
                "audio": self._audio_display(
                    plan, task.get("music_override"), niche
                ),
                "transitions": transition_catalog(),
                "grade_presets": grade_presets(),
                "luts": await run_in_thread(list_luts),
                "sfx": await run_in_thread(self._sfx_display, plan),
            }

        niche = resolve_effective_profile(
            clip.get("niche_override"), task.get("niche_override"), None
        )
        source_ranges = await run_in_thread(self._get_clip_source_ranges, clip)
        bounds = source_range_bounds(source_ranges)
        if not bounds:
            raise ValueError("Clip source timing is unavailable")
        edl = await run_in_thread(
            self._build_rerender_edl,
            clip,
            bounds[0],
            bounds[1],
            niche,
            None,
        )
        derived_segments = edl.get("segments") or []
        try:
            derived_segments = await run_in_thread(
                attach_source_shares, derived_segments, source_ranges
            )
        except ValueError:
            pass
        derived = build_edit_plan(derived_segments, niche=niche)
        return {
            "clip_id": clip_id,
            "plan": derived,
            "persisted": False,
            "niche": niche,
            "look": self._look_display(derived, derived_segments, niche),
            "audio": self._audio_display(
                derived, task.get("music_override"), niche
            ),
            "transitions": transition_catalog(),
            "grade_presets": grade_presets(),
            "luts": await run_in_thread(list_luts),
            "sfx": await run_in_thread(self._sfx_display, derived),
        }

    @serialized_task_edit
    async def edit_clip(
        self,
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
        """Apply editor operations / caption overrides and re-render.

        The edit runs through the Lane-5 re-render seam: the operations
        mutate the clip's persisted edit plan (seeded from a fresh EDL
        build when the clip predates the editor), caption overrides are
        validated and stored on the plan, and the re-render persists the
        resulting plan back onto the new file. Music follows the task's
        stored pick unless ``music_override`` says otherwise. The Audio
        Studio's ``audio`` / ``voiceover`` blocks ride the same seam;
        when the audio block names a track choice it — not the task's
        pick — decides this clip's music.
        """
        from ..auto_director.audio_studio import (
            audio_overrides_from_plan,
            normalize_audio_overrides,
        )
        from ..auto_director.clip_edits import (
            apply_segment_operations,
            build_edit_plan,
            caption_overrides_from_plan,
            normalize_caption_overrides,
            plan_has_source_mapping,
            plan_segments,
        )
        from ..auto_director.look import (
            look_overrides_from_plan,
            normalize_look_overrides,
        )
        from ..auto_director.niche import resolve_effective_profile
        from ..media.voiceover import (
            normalize_voiceover_request,
            voiceover_spec_from_plan,
        )

        overrides = normalize_caption_overrides(captions)
        look_overrides = normalize_look_overrides(look)
        audio_overrides = normalize_audio_overrides(audio)
        voiceover_request = normalize_voiceover_request(voiceover)
        from ..auto_director.sfx import normalize_sfx_placements

        sfx_placements = normalize_sfx_placements(sfx)
        if sfx_placements:
            # A placement naming a file the renderer cannot resolve
            # would be silently dropped at render — reject it here,
            # where the user can still fix the pick.
            from ..media.sfx_library import find_sfx_file

            for placement in sfx_placements:
                if find_sfx_file(placement["name"]) is None:
                    raise ValueError(
                        f"SFX file '{placement['name']}' is not in the "
                        "SFX library"
                    )
        clip = await self.clip_repo.get_clip_by_id(self.db, clip_id)
        if not clip or clip["task_id"] != task_id:
            raise ValueError("Clip not found")
        task = await self.task_repo.get_task_by_id(self.db, task_id)
        if not task:
            raise ValueError("Task not found")

        target_niche = niche_override or resolve_effective_profile(
            clip.get("niche_override"), task.get("niche_override"), None
        )

        plan = await run_in_thread(
            load_clip_editor_settings,
            MediaStorage().local_path(clip["file_path"], metadata_only=True),
        )
        source_ranges = await run_in_thread(self._get_clip_source_ranges, clip)
        if operations:
            if plan:
                base_segments = plan_segments(plan)
            else:
                bounds = source_range_bounds(source_ranges)
                if not bounds:
                    raise ValueError("Clip source timing is unavailable")
                seed = await run_in_thread(
                    self._build_rerender_edl,
                    clip,
                    bounds[0],
                    bounds[1],
                    target_niche,
                    None,
                )
                base_segments = seed.get("segments") or []
            if not plan_has_source_mapping(
                {"segments": base_segments} if base_segments else None
            ):
                # First edit of this layout: bind each segment to the
                # footage slice it currently shows, so the operations
                # below move footage, not just trim windows.
                from ..auto_director.clip_edits import attach_source_shares

                base_segments = await run_in_thread(
                    attach_source_shares, base_segments, source_ranges
                )
            new_segments = apply_segment_operations(base_segments, operations)
        else:
            new_segments = plan_segments(plan) if plan else None

        effective_captions = overrides
        if effective_captions is None and plan:
            effective_captions = caption_overrides_from_plan(plan)
        effective_music = (
            music_override
            if music_override is not None
            else task.get("music_override")
        )
        if audio_overrides is not None and "music_track" in audio_overrides:
            # The Audio Studio panel named this clip's music (a track,
            # "auto" or "none"): its choice replaces the task-level
            # pick for this render — the re-render seam applies it.
            effective_music = None

        kept_ranges = None
        if new_segments is not None:
            from ..auto_director.clip_edits import kept_source_ranges

            kept_ranges = await run_in_thread(
                kept_source_ranges, new_segments, source_ranges
            )
            if not kept_ranges:
                raise ValueError("Clip source timing is unavailable")

        refreshed = await self.rerender_clip(
            task_id,
            clip_id,
            target_niche,
            music_override=effective_music,
            segment_plan=new_segments,
            caption_overrides=effective_captions,
            font_user_id=task.get("user_id"),
            keep_ranges_override=kept_ranges,
            look_overrides=look_overrides,
            audio_overrides=audio_overrides,
            voiceover_request=voiceover_request,
            sfx_overrides=sfx_placements,
        )
        if new_segments is not None:
            response_look = look_overrides
            if response_look is None and plan:
                response_look = look_overrides_from_plan(plan)
            response_audio = audio_overrides
            if response_audio is None and plan:
                response_audio = audio_overrides_from_plan(plan)
            response_voiceover = None
            if voiceover_request is not None and not voiceover_request.get(
                "clear"
            ):
                from ..media.voiceover import cache_filename

                response_voiceover = {
                    "script": voiceover_request["script"],
                    "voice": voiceover_request["voice"],
                    "mode": voiceover_request["mode"],
                    "file": cache_filename(
                        voiceover_request["script"],
                        voiceover_request["voice"],
                    ),
                }
            elif voiceover_request is None and plan:
                response_voiceover = voiceover_spec_from_plan(plan)
            from ..auto_director.sfx import sfx_placements_from_plan

            response_sfx = sfx_placements
            if response_sfx is None and plan:
                response_sfx = sfx_placements_from_plan(plan)
            refreshed["edit_plan"] = build_edit_plan(
                new_segments,
                niche=target_niche,
                captions=effective_captions,
                look=response_look,
                audio=response_audio,
                voiceover=response_voiceover,
                sfx=response_sfx,
            )
        else:
            stored = await run_in_thread(
                load_clip_editor_settings,
                MediaStorage().local_path(
                    refreshed["file_path"], metadata_only=True
                ),
            )
            if stored:
                refreshed["edit_plan"] = stored
        return refreshed

    @serialized_task_edit
    async def trim_clip(
        self,
        task_id: str,
        clip_id: str,
        start_offset: float,
        end_offset: float,
    ) -> Dict[str, Any]:
        clip = await self.clip_repo.get_clip_by_id(self.db, clip_id)
        if not clip or clip["task_id"] != task_id:
            raise ValueError("Clip not found")

        input_path = await run_in_thread(MediaStorage().local_path, clip["file_path"])
        if not input_path.exists():
            raise ValueError("Clip file not found")

        output_path = await run_in_thread(
            trim_clip_file, input_path, Path(self.config.temp_dir) / "clips", start_offset, end_offset
        )
        source_ranges = await run_in_thread(self._get_clip_source_ranges, clip)
        offsets = load_clip_output_offsets(input_path, source_ranges)
        total = max(offset + b - a for offset, (a, b) in zip(offsets, source_ranges))
        trimmed_ranges, trimmed_offsets = slice_source_timeline(source_ranges, start_offset, total - end_offset, offsets)
        clip_duration = max(
            0.1, await run_in_thread(rendered_clip_duration, output_path, trimmed_ranges)
        )
        bounds = source_range_bounds(trimmed_ranges)
        if not bounds:
            raise ValueError("Trimmed clip has no remaining source mapping")
        start_seconds, end_seconds = bounds
        save_clip_source_ranges(output_path, trimmed_ranges, trimmed_offsets)
        if settings := load_clip_caption_settings(input_path):
            save_clip_caption_settings(output_path, settings)
        if broll := load_clip_broll(input_path):
            await self._carry_broll(broll, [(output_path, max(0.0, start_offset))])

        new_start = self._seconds_to_mmss(start_seconds)
        new_end = self._seconds_to_mmss(end_seconds)

        await self.clip_repo.update_clip(
            self.db,
            clip_id,
            output_path.name,
            str(output_path),
            new_start,
            new_end,
            clip_duration,
            clip.get("text") or "",
        )
        return (await self.clip_repo.get_clip_by_id(self.db, clip_id)) or {}


    @serialized_task_edit
    async def split_clip(
        self, task_id: str, clip_id: str, split_time: float
    ) -> Dict[str, Any]:
        clip = await self.clip_repo.get_clip_by_id(self.db, clip_id)
        if not clip or clip["task_id"] != task_id:
            raise ValueError("Clip not found")

        input_path = await run_in_thread(MediaStorage().local_path, clip["file_path"])
        if not input_path.exists():
            raise ValueError("Clip file not found")

        first_path, second_path = await run_in_thread(
            split_clip_file, input_path, Path(self.config.temp_dir) / "clips", split_time
        )

        clamped_split = max(0.2, min(split_time, float(clip["duration"]) - 0.2))
        source_ranges = await run_in_thread(self._get_clip_source_ranges, clip)
        offsets = load_clip_output_offsets(input_path, source_ranges)
        total = max(offset + b - a for offset, (a, b) in zip(offsets, source_ranges))
        first_ranges, first_offsets = slice_source_timeline(source_ranges, 0, clamped_split, offsets)
        second_ranges, second_offsets = slice_source_timeline(source_ranges, clamped_split, total, offsets)
        first_bounds = source_range_bounds(first_ranges)
        second_bounds = source_range_bounds(second_ranges)
        if not first_bounds or not second_bounds:
            raise ValueError("Split clip has invalid source mapping")
        save_clip_source_ranges(first_path, first_ranges, first_offsets)
        save_clip_source_ranges(second_path, second_ranges, second_offsets)
        if settings := load_clip_caption_settings(input_path):
            save_clip_caption_settings(first_path, settings)
            save_clip_caption_settings(second_path, settings)
        if broll := load_clip_broll(input_path):
            await self._carry_broll(broll, [(first_path, 0.0), (second_path, clamped_split)])
        first_duration = max(
            0.1, await run_in_thread(rendered_clip_duration, first_path, first_ranges)
        )
        second_duration = max(
            0.1, await run_in_thread(rendered_clip_duration, second_path, second_ranges)
        )

        await self.clip_repo.update_clip(
            self.db,
            clip_id,
            first_path.name,
            str(first_path),
            self._seconds_to_mmss(first_bounds[0]),
            self._seconds_to_mmss(first_bounds[1]),
            first_duration,
            clip.get("text") or "",
        )

        await self.clip_repo.make_room_for_clip(
            self.db, task_id, clip.get("clip_order", 1) + 1
        )
        await self.clip_repo.create_clip(
            self.db,
            task_id=task_id,
            filename=second_path.name,
            file_path=str(second_path),
            start_time=self._seconds_to_mmss(second_bounds[0]),
            end_time=self._seconds_to_mmss(second_bounds[1]),
            duration=second_duration,
            text=clip.get("text") or "",
            relevance_score=clip.get("relevance_score", 0.5),
            reasoning=clip.get("reasoning") or "Split from original clip",
            clip_order=clip.get("clip_order", 1) + 1,
            virality_score=clip.get("virality_score", 0),
            hook_score=clip.get("hook_score", 0),
            engagement_score=clip.get("engagement_score", 0),
            value_score=clip.get("value_score", 0),
            shareability_score=clip.get("shareability_score", 0),
            hook_type=clip.get("hook_type"),
            hook_title=clip.get("hook_title"),
        )

        await self.clip_repo.reorder_task_clips(self.db, task_id)
        return {"message": "Clip split successfully"}


    @serialized_task_edit
    async def merge_clips(self, task_id: str, clip_ids: list[str]) -> Dict[str, Any]:
        if len(clip_ids) < 2 or len(set(clip_ids)) != len(clip_ids):
            raise ValueError("At least two distinct clips are required to merge")

        clips = []
        for clip_id in clip_ids:
            clip = await self.clip_repo.get_clip_by_id(self.db, clip_id)
            if not clip or clip["task_id"] != task_id:
                raise ValueError("One or more clips not found")
            clips.append(clip)

        ordered = sorted(clips, key=lambda c: c.get("clip_order", 0))
        input_paths = [await run_in_thread(MediaStorage().local_path, c["file_path"]) for c in ordered]
        merged_path = await run_in_thread(
            merge_clip_files,
            input_paths,
            Path(self.config.temp_dir) / "clips",
        )

        merged_ranges, merged_offsets, cursor = [], [], 0.0
        for clip, path in zip(ordered, input_paths):
            source_ranges = await run_in_thread(self._get_clip_source_ranges, clip)
            merged_ranges.extend(source_ranges)
            merged_offsets += [cursor + offset for offset in load_clip_output_offsets(path, source_ranges)]
            cursor += float(clip["duration"])
        merged_bounds = source_range_bounds(merged_ranges)
        if merged_bounds:
            start_time = self._seconds_to_mmss(merged_bounds[0])
            end_time = self._seconds_to_mmss(merged_bounds[1])
            duration = await run_in_thread(rendered_clip_duration, merged_path, merged_ranges)
            save_clip_source_ranges(merged_path, merged_ranges, merged_offsets)
        else:
            start_time = ordered[0]["start_time"]
            end_time = ordered[-1]["end_time"]
            duration = sum(float(c.get("duration", 0.0)) for c in ordered)
        clip_broll = [load_clip_broll(path) for path in input_paths]
        if any(clip_broll):
            merged_broll = []
            offset = 0.0
            try:
                for path, placements in zip(input_paths, clip_broll):
                    length = await run_in_thread(ffprobe_duration, path)
                    merged_broll += shift_broll_placements(placements, -offset, offset + length)
                    offset += length
                save_clip_broll(merged_path, merged_broll)
            except Exception:
                # The merged file already has the footage; only later re-renders lose it.
                logger.warning("Could not carry B-roll placements into merged clip", exc_info=True)
        text = " ".join((c.get("text") or "").strip() for c in ordered if c.get("text"))

        first = ordered[0]
        await self.clip_repo.update_clip(
            self.db,
            first["id"],
            merged_path.name,
            str(merged_path),
            start_time,
            end_time,
            duration,
            text,
        )

        for clip in ordered[1:]:
            await self.clip_repo.delete_clip(self.db, clip["id"])

        await self.clip_repo.reorder_task_clips(self.db, task_id)
        return {"message": "Clips merged successfully", "clip_id": first["id"]}


    @staticmethod
    def _record_caption_patch_in_edit_plan(
        input_path: Path,
        output_path: Path,
        caption_settings: Dict[str, Any],
        duration: float,
    ) -> None:
        """Write a caption PATCH's values into the clip's edit plan.

        The PATCH also persists the same values to the ``caption_settings``
        sidecar key, which only the Studio-editor seed reads — the
        re-render EDL never looks there. The edit plan is the copy the
        re-render carry-forward (F-B) actually reads, so a PATCH position
        only survives a later re-render when it is recorded here too:
        merged into the stored plan's captions block (other blocks —
        segments, look, audio — are untouched), or a minimal
        single-segment plan when the clip has none yet. Never raises: a
        plan-write failure must not break a PATCH whose render already
        succeeded.
        """
        try:
            from ..auto_director.clip_edits import (
                build_edit_plan,
                normalize_caption_overrides,
            )

            candidates = {
                key: caption_settings[key]
                for key in ("position", "position_y", "font_size")
                if caption_settings.get(key) is not None
            }
            try:
                overrides = normalize_caption_overrides(candidates)
            except ValueError:
                # A stored task font size outside the valid range must not
                # drop the position the user just set: retry with the
                # route-validated fields only.
                overrides = normalize_caption_overrides(
                    {
                        key: candidates[key]
                        for key in ("position", "position_y")
                        if key in candidates
                    }
                )
            if overrides is None:
                return
            plan = load_clip_editor_settings(input_path)
            if plan:
                merged = dict(plan.get("captions") or {})
                merged.update(overrides)
                plan = {**plan, "captions": merged}
            else:
                if not duration or duration < 0.5:
                    return
                plan = build_edit_plan(
                    [
                        {
                            "index": 0,
                            "start": 0.0,
                            "end": round(float(duration), 3),
                        }
                    ],
                    captions=overrides,
                )
            save_clip_editor_settings(output_path, plan)
        except Exception:
            logger.warning(
                "Could not record the caption PATCH in the edit plan for %s",
                output_path,
                exc_info=True,
            )

    @serialized_task_edit
    async def update_clip_captions(
        self,
        task_id: str,
        clip_id: str,
        caption_text: str,
        position: str,
        highlight_words: list[str],
        *,
        font_size: Optional[int] = None,
        position_y: Optional[float] = None,
    ) -> Dict[str, Any]:
        clip = await self.clip_repo.get_clip_by_id(self.db, clip_id)
        if not clip or clip["task_id"] != task_id:
            raise ValueError("Clip not found")

        input_path = await run_in_thread(MediaStorage().local_path, clip["file_path"])
        if not input_path.exists():
            raise ValueError("Clip file not found")

        task = await self.task_repo.get_task_by_id(self.db, task_id)
        if not task:
            raise ValueError("Task not found")

        transcript_video_path: Optional[Path] = None
        source_url = task.get("source_url")
        source_type = task.get("source_type")
        processing_mode = (
            task.get("processing_mode") or self.config.default_processing_mode
        )
        download_quality, output_short_side = await self._media_limits(task.get("user_id"))
        if source_url and source_type:
            cache_entry = await self.cache_repo.get_cache(
                self.db,
                self._build_cache_key(source_url, source_type, processing_mode, download_quality),
            )
            cached_video_path = cache_entry.get("video_path") if cache_entry else None
            if cached_video_path:
                transcript_video_path = await run_in_thread(MediaStorage().local_path, cached_video_path)
            elif source_type not in {"youtube", "twitch", "kick"}:
                try:
                    transcript_video_path = await resolve_upload_for_edit(
                        self.video_service, source_url
                    )
                except ValueError:
                    transcript_video_path = None

        # Always render from the source; overlaying an already captioned clip
        # stacks old and new captions and compounds quality loss on every save.
        source_ranges = await run_in_thread(self._get_clip_source_ranges, clip)
        downloaded_source: Optional[Path] = None
        if not transcript_video_path or not transcript_video_path.exists():
            if source_type == "youtube" and source_url:
                downloaded = await self.video_service.download_video(
                    source_url,
                    quality=download_quality,
                    task_id=task_id,
                    ranges=source_ranges,
                )
                transcript_video_path = Path(downloaded) if downloaded else None
                downloaded_source = transcript_video_path
            elif source_type in {"twitch", "kick"} and source_url:
                transcript_video_path = await run_in_thread(
                    download_stream_sections, source_url,
                    [{"start_time": start, "end_time": end} for start, end in source_ranges],
                    task.get("source_duration_seconds"), task_id,
                )
                downloaded_source = transcript_video_path
            elif source_url:
                transcript_video_path = await resolve_upload_for_edit(self.video_service, source_url)
        if not transcript_video_path or not transcript_video_path.exists():
            raise ValueError("The source video is no longer available. Upload it again to edit captions.")

        try:
            transcript_video_path = await self.video_service.prepare_source(transcript_video_path)
            settings = await self._load_task_source_settings(task_id)
            local_ranges = await run_in_thread(
                local_ranges_for, transcript_video_path, source_ranges
            )
            bounds = source_range_bounds(local_ranges)
            if not bounds:
                raise ValueError("Clip source timing is unavailable")
            output_dir = Path(self.config.temp_dir) / "clips"
            output_dir.mkdir(parents=True, exist_ok=True)
            # The clip timeline is unchanged, so its cutaways go back on as they were.
            broll = load_clip_broll(input_path)
            broll_overlays = await broll_overlays_for(broll)
            render_report: Dict[str, Any] = {}
            with tempfile.TemporaryDirectory(prefix="caption_edit_", dir=output_dir) as temporary:
                clean_path = Path(temporary) / "clean.mp4"
                rendered = await run_in_thread(
                    create_optimized_clip, transcript_video_path, bounds[0], bounds[1], clean_path,
                    add_subtitles=False,
                    output_format=settings.get("output_format", "vertical"),
                    keep_ranges=local_ranges,
                    hook_title=clip.get("hook_title"),
                    font_family=task.get("font_family") or None,
                    font_size=task.get("font_size") or None,
                    font_color=task.get("font_color") or None,
                    caption_template=task.get("caption_template") or "default",
                    extend_to_sentence=False,
                    layout_report=render_report,
                    broll_overlays=broll_overlays,
                )
                if not rendered:
                    raise ValueError("Could not prepare the clip for caption editing")
                if not render_report.get("broll_applied"):
                    broll = []
                output_path = await run_in_thread(
                    overlay_custom_captions,
                    clean_path, output_dir, caption_text, position, highlight_words,
                    font_family=task.get("font_family") or None,
                    font_size=font_size if font_size is not None else task.get("font_size") or None,
                    font_color=task.get("font_color") or None,
                    caption_template=task.get("caption_template") or "default",
                    transcript_video_path=transcript_video_path,
                    source_ranges=local_ranges,
                    position_y=position_y,
                )
                if output_short_side:
                    try:
                        await run_in_thread(cap_video_short_side, output_path, output_short_side)
                    except Exception as e:
                        logger.warning(f"Keeping full-size caption edit for clip {clip_id}: {e}")
        finally:
            if downloaded_source is not None:
                self._cleanup_source_video(downloaded_source, source_type, source_url)
        save_clip_source_ranges(output_path, source_ranges)
        caption_settings = {
            "font_size": font_size if font_size is not None else task.get("font_size"),
            "position": position,
            "position_y": position_y if position_y is not None else {"top": 0.18, "middle": 0.52, "bottom": 0.78}[position],
            "highlight_words": highlight_words,
        }
        save_clip_caption_settings(output_path, caption_settings)
        save_clip_broll(output_path, broll)
        # Re-rendering also corrects durations stored before they were probed.
        rendered_duration = await run_in_thread(
            rendered_clip_duration, output_path, source_ranges
        )
        # The edit plan is the copy re-renders read (F-B carry-forward);
        # the caption_settings key alone never reaches a re-render, so
        # record the PATCH there too (merged, never replacing the plan).
        self._record_caption_patch_in_edit_plan(
            input_path,
            output_path,
            caption_settings,
            rendered_duration if rendered_duration > 0 else clip["duration"],
        )

        await self.clip_repo.update_clip(
            self.db,
            clip_id,
            output_path.name,
            str(output_path),
            clip["start_time"],
            clip["end_time"],
            rendered_duration if rendered_duration > 0 else clip["duration"],
            caption_text,
        )
        return (await self.clip_repo.get_clip_by_id(self.db, clip_id)) or {}


    @staticmethod
    def _seconds_to_mmss(seconds: float) -> str:
        total = max(0, int(round(seconds)))
        minutes = total // 60
        secs = total % 60
        return f"{minutes:02d}:{secs:02d}"


    @staticmethod
    async def _carry_broll(broll: list[dict], cuts: list[tuple[Path, float]]) -> None:
        """Store B-roll for clips cut from a longer one (path, start offset).

        The cut files already contain the footage; losing the placements only
        means a later re-render from source drops it, so never fail the edit.
        """
        try:
            for path, offset in cuts:
                length = await run_in_thread(ffprobe_duration, path)
                save_clip_broll(path, shift_broll_placements(broll, offset, length))
        except Exception:
            logger.warning("Could not carry B-roll placements into edited clip", exc_info=True)

    @staticmethod
    def _carried_broll_entries(
        saved_broll: list[dict],
        edl: Dict[str, Any],
        segment_plan: Optional[list],
        source_duration: float = 0.0,
    ) -> Optional[list[dict]]:
        """The clip's saved B-roll placements as EDL cutaway entries.

        Saved placements live in clip seconds with ``start`` +
        ``duration``; EDL entries carry ``start`` / ``end`` /
        ``source_start`` and the resolved ``local_path``. With no
        segment plan the timeline is unchanged, so the conversion is
        direct. With a plan, each placement is re-based through the
        plan's persisted source mapping: a segment's
        ``source_offset``/``source_share`` locate its slice of the
        stored clip range (``attach_source_shares`` stamps them; see
        ``clip_edits``), so the part of a window the edit kept lands
        where the edited timeline shows that footage, and a window the
        edit dropped disappears with it. Returns None when the plan
        carries no usable mapping or nothing survives the edit — the
        caller then keeps the fresh EDL's own planned queries.
        """
        if segment_plan is None:
            entries = []
            for placement in saved_broll:
                start = float(placement["start"])
                entry = dict(placement)
                entry["start"] = round(start, 3)
                entry["end"] = round(
                    start + float(placement["duration"]), 3
                )
                entry["source_start"] = float(
                    placement.get("footage_offset") or 0.0
                )
                entries.append(entry)
            return entries
        if source_duration <= 0:
            return None
        spans = []
        for seg in edl.get("segments") or []:
            if not isinstance(seg, dict):
                continue
            share = seg.get("source_share")
            if share is None:
                continue
            try:
                src_start = float(seg.get("source_offset") or 0.0)
                src_end = src_start + float(share) * source_duration
                out_start = float(seg["start"])
            except (TypeError, ValueError):
                continue
            spans.append((src_start, src_end, out_start))
        if not spans:
            return None
        entries = []
        for placement in saved_broll:
            win_start = float(placement["start"])
            win_end = win_start + float(placement["duration"])
            footage_offset = float(placement.get("footage_offset") or 0.0)
            for src_start, src_end, out_start in spans:
                lo = max(win_start, src_start)
                hi = min(win_end, src_end)
                if hi - lo < 0.25:
                    continue
                entry = dict(placement)
                entry["start"] = round(out_start + (lo - src_start), 3)
                entry["end"] = round(out_start + (hi - src_start), 3)
                entry["source_start"] = round(
                    footage_offset + (lo - win_start), 3
                )
                entries.append(entry)
        return entries or None

    @staticmethod
    def _get_clip_broll(clip: Dict[str, Any]) -> list[dict]:
        file_path = clip.get("file_path")
        if not isinstance(file_path, str) or not file_path:
            return []
        return load_clip_broll(MediaStorage().local_path(file_path, metadata_only=True))

    @staticmethod
    def _get_clip_source_ranges(clip: Dict[str, Any]) -> list[tuple[float, float]]:
        file_path = clip.get("file_path")
        if isinstance(file_path, str) and file_path:
            persisted = load_clip_source_ranges(MediaStorage().local_path(file_path, metadata_only=True))
            if persisted:
                return persisted

        start_seconds = parse_timestamp_to_seconds(clip["start_time"])
        end_seconds = parse_timestamp_to_seconds(clip["end_time"])
        return [(start_seconds, end_seconds)]


    async def _load_task_text_bands(
        self, task_id: str
    ) -> Optional[Dict[str, Any]]:
        """The task's stored text-band decision (media.text_bands), or None.

        Re-renders and regenerations only ever READ the stored
        decision — they never re-decide — so a clip re-rendered alone
        places its text exactly where the original task render put
        it. Tasks created before the feature have no stored decision
        and re-render with per-clip bands, exactly as before. (The
        settings loader above returns a whitelist that predates the
        bands, so they are read from source_metadata directly.)
        """
        if self.db is None:
            return None
        try:
            metadata = (
                await self.task_repo.load_source_metadata(self.db, task_id)
                or {}
            )
        except Exception:
            return None
        bands = metadata.get("text_bands")
        return bands if isinstance(bands, dict) and bands else None

    async def _load_task_source_settings(self, task_id: str) -> Dict[str, Any]:
        defaults = {
            "output_format": "vertical",
            "add_subtitles": True,
            "clip_length": "auto",
            **normalize_clip_cleanup_settings(),
        }
        parsed = (
            await self.task_repo.load_source_metadata(self.db, task_id)
            if self.db is not None else {}
        )
        if not parsed:
            redis_client = redis.Redis(
                host=self.config.redis_host,
                port=self.config.redis_port,
                password=self.config.redis_password,
                decode_responses=True,
            )
            try:
                payload = await redis_client.get(f"task_source:{task_id}")
            except Exception as exc:
                logger.warning(
                    "Falling back to default task source settings for task %s: %s",
                    task_id, exc,
                )
                return defaults
            finally:
                try:
                    await redis_client.aclose()
                except Exception:
                    pass

            if not payload:
                return defaults
            try:
                parsed = json.loads(payload)
            except json.JSONDecodeError:
                return defaults
            if not isinstance(parsed, dict):
                return defaults

        output_format = parsed.get("output_format", defaults["output_format"])
        if output_format not in VALID_OUTPUT_FORMATS:
            output_format = defaults["output_format"]

        add_subtitles = parsed.get("add_subtitles", defaults["add_subtitles"])
        if not isinstance(add_subtitles, bool):
            add_subtitles = defaults["add_subtitles"]

        return {
            "output_format": output_format,
            "add_subtitles": add_subtitles,
            "clip_length": normalize_clip_length(parsed.get("clip_length")),
            **normalize_clip_cleanup_settings(
                parsed.get("cut_long_pauses"),
                parsed.get("pause_threshold_ms"),
                parsed.get("remove_filler_words"),
                parsed.get("filtered_words"),
            ),
        }
