"""
Video service - handles video processing business logic.
"""

import asyncio
import os
from pathlib import Path
from types import SimpleNamespace
from typing import List, Dict, Any, Optional, Callable, Awaitable, Tuple
import logging
import json
import subprocess
from time import perf_counter
import uuid

from ..utils.async_helpers import run_in_thread
from ..download_progress import (
    DownloadRecovery,
    ProgressCallback,
    report_download_recovery,
    run_download_in_thread,
)
from ..repositories.minute_usage_repository import charged_duration
from ..youtube_utils import (
    async_get_youtube_video_info,
    async_get_youtube_video_title,
    cleanup_downloaded_files,
    download_youtube_video,
    get_youtube_video_id,
)
from ..youtube_proxy import (
    ByteBudget,
    DownloadBudgetExceeded,
    PartialDownloadUnavailable,
    YouTubeGeoRestrictedError,
    proxy_configured,
    public_download_error,
)
from ..youtube_source import (
    cached_full_media,
    download_analysis_source,
    materialize_clip_source,
    resolve_full_youtube_download,
    copy_transcript_sidecar,
    load_time_map,
    local_ranges_for,
    persist_cache_sidecar,
    preserve_geo_restriction,
)
from ..video_utils import (
    get_video_transcript,
    create_clips_with_transitions,
    create_optimized_clip,
    load_cached_transcript_data,
    parse_timestamp_to_seconds,
    build_clip_keep_ranges,
    build_keep_ranges_from_source_ranges,
    build_clip_signal_summary,
    apply_segment_hook_teaser,
    clamp_keep_ranges_to_max_duration,
    extend_keep_ranges_to_sentence_boundary,
    keep_ranges_transcript_text,
    snap_clip_start_to_sentence,
    seconds_to_mmss,
    get_transcript_text_in_range,
)
from ..auto_director.creation_modes import (
    REVIEW_MOMENT_PICK,
    SELECTION_USER_RANGES,
    build_manual_segments,
    cached_analysis_is_user_selected,
    plan_for_mode,
)
from ..broll import BRollSession, broll_overlays_for, plan_clip_broll, stock_broll_configured
from ..clip_source_map import (
    normalize_source_ranges,
    save_clip_broll,
    save_clip_source_ranges,
    source_range_bounds,
)
from .. import provider_manager
from ..ai import get_most_relevant_parts_by_transcript, max_final_clips_for_duration
from ..clip_openings import OPENING_HOOK_PENALTY, opening_text_issue, review_opening
from ..clip_duration import clip_duration
from ..config import get_config
from ..media.source_limits import normalize_source_media
from ..media.upload_ingest import (
    materialize_stored_upload,
    persist_local_upload,
    read_upload_reference,
    upload_needs_remote_copy,
)
from ..media_storage import MediaStorage
from ..media.ffmpeg import (
    cap_video_short_side,
    ffprobe_duration,
    ffprobe_video_size,
    probe_video_codec,
    rendered_clip_duration,
    rendered_ranges_duration,
)
from ..media.timeline import get_words_for_keep_ranges
from ..media.visual_suitability import (
    is_visually_static,
    segment_static_fraction,
    segment_shows_interruption_notice,
    still_picture_shows_face,
)
from ..stream_source import (
    PROCESSING_OPERATION,
    StreamSourceError,
    detect_stream_source,
    download_stream_audio,
    download_stream_sections,
    download_stream_video,
)

logger = logging.getLogger(__name__)
UPLOAD_URL_PREFIX = "upload://"
OPENING_CONTEXT_SECONDS = 10.0


async def resolve_upload_for_edit(video_service, url: str) -> Path:
    """Open an uploaded source for an existing clip.

    A real video service falls back to the session storage reference. Fakes
    that only implement the local resolver stay on that path.
    """
    opener = getattr(video_service, "resolve_stored_upload", None)
    if opener is None:
        return await run_in_thread(video_service.resolve_local_video_path, url)
    return await opener(url)


class VideoService:
    """Service for video processing operations."""

    async def measure_source_duration(self, url: str, user_id: str) -> int:
        """Resolve the full source length for older/unmetered task entry paths."""
        source_type = self.determine_source_type(url)
        if source_type == "youtube":
            metadata = await asyncio.wait_for(
                async_get_youtube_video_info(url),
                timeout=get_config().youtube_preflight_timeout_seconds,
            )
            duration = (metadata or {}).get("duration")
        elif source_type in {"twitch", "kick"}:
            from ..stream_source import fetch_stream_info, PREFLIGHT_DEADLINE_SECONDS

            metadata = await asyncio.wait_for(
                run_in_thread(fetch_stream_info, url, deadline_seconds=PREFLIGHT_DEADLINE_SECONDS),
                timeout=PREFLIGHT_DEADLINE_SECONDS + 5,
            )
            duration = (metadata or {}).get("duration")
        else:
            duration = await run_in_thread(self.probe_upload_duration, url, user_id)
        return charged_duration(duration)

    @staticmethod
    def _ground_segment_text(
        segment_payload: Dict[str, Any],
        transcript_data: Optional[Dict[str, Any]],
    ) -> str:
        if not transcript_data:
            return str(segment_payload.get("text") or "").strip()

        start_time = str(segment_payload.get("start_time") or "").strip()
        end_time = str(segment_payload.get("end_time") or "").strip()
        if not start_time or not end_time:
            return str(segment_payload.get("text") or "").strip()

        start_seconds = parse_timestamp_to_seconds(start_time)
        end_seconds = parse_timestamp_to_seconds(end_time)
        if end_seconds <= start_seconds:
            return str(segment_payload.get("text") or "").strip()

        grounded_text = get_transcript_text_in_range(
            transcript_data,
            start_seconds,
            end_seconds,
        ).strip()
        return grounded_text or str(segment_payload.get("text") or "").strip()

    @staticmethod
    def _segment_words(
        transcript_data: Optional[Dict[str, Any]], start: float, end: float
    ) -> List[Dict[str, Any]]:
        """Transcript words in source seconds from ``start`` to ``end``.

        Zero-length words are kept: the opening review needs every token.
        """
        words = []
        for word in (transcript_data or {}).get("words") or []:
            word_start = float(word.get("start", 0)) / 1000.0
            if start <= word_start < end:
                words.append(
                    {
                        "text": str(word.get("text", "")),
                        "start": word_start,
                        "end": max(word_start, float(word.get("end", 0)) / 1000.0),
                    }
                )
        return sorted(words, key=lambda word: word["start"])

    @staticmethod
    def _refine_segment_openings(
        segments: List[Dict[str, Any]],
        transcript_data: Optional[Dict[str, Any]],
        clip_length: str = "auto",
    ) -> List[Dict[str, Any]]:
        """Trim lead-in banter or fragments off each opening, or demote the clip.

        A start that only moves within limits keeps the clip's point; an
        opening that still leans on missing context loses hook points, and the
        list is re-sorted so the demoted clip no longer leads.
        """
        min_duration = clip_duration(clip_length).minimum
        penalised = False
        for segment in segments:
            # Cached analyses were reviewed when first stored; a second pass
            # would apply the penalty again.
            if segment.get("opening_reviewed"):
                continue
            segment["opening_reviewed"] = True
            try:
                start = parse_timestamp_to_seconds(str(segment.get("start_time") or ""))
                end = parse_timestamp_to_seconds(str(segment.get("end_time") or ""))
            except Exception:
                continue
            if end <= start:
                continue
            # Earlier words show where the opening sentence really starts.
            words = VideoService._segment_words(transcript_data, start - OPENING_CONTEXT_SECONDS, end)
            if words:
                review = review_opening(words, start, end, min_duration)
                issue, should_penalise = review.issue, review.penalise
                if review.start is not None and review.start <= start:
                    # The trim target is not after the stored start, so the
                    # opening stays as it is; rank it like an untrimmable one.
                    should_penalise = True
            else:
                issue = opening_text_issue(str(segment.get("text") or ""))
                review = None
                should_penalise = issue is not None
            if review is not None and review.start is not None and review.start > start:
                new_start = seconds_to_mmss(review.start)
                logger.info(
                    "Opening of %s-%s is %s (%r); starting at %s instead",
                    segment.get("start_time"),
                    segment.get("end_time"),
                    issue,
                    review.skipped_text,
                    new_start,
                )
                segment["start_time"] = new_start
                kept_words = VideoService._segment_words(
                    transcript_data, review.first_word_start or review.start, end
                )
                segment["text"] = " ".join(word["text"] for word in kept_words) or segment.get("text", "")
                hook_start = segment.get("hook_start_time")
                try:
                    if hook_start and parse_timestamp_to_seconds(str(hook_start)) < review.start:
                        segment["hook_start_time"] = None
                        segment["hook_end_time"] = None
                except Exception:
                    segment["hook_start_time"] = None
                    segment["hook_end_time"] = None
            if should_penalise:
                hook_score = int(segment.get("hook_score") or 0)
                reduced = max(0, hook_score - OPENING_HOOK_PENALTY)
                segment["hook_score"] = reduced
                segment["virality_score"] = max(
                    0, int(segment.get("virality_score") or 0) - (hook_score - reduced)
                )
                penalised = True
                logger.info(
                    "Opening of %s-%s still depends on missing context (%s); hook score %d -> %d",
                    segment.get("start_time"),
                    segment.get("end_time"),
                    issue,
                    hook_score,
                    reduced,
                )
        if penalised:
            # Analysis output is already best-first; a stable sort only moves
            # the demoted clips.
            segments = sorted(
                segments, key=lambda segment: int(segment.get("virality_score") or 0), reverse=True
            )
        return segments

    @staticmethod
    def _apply_retention_analysis(
        segments: List[Dict[str, Any]],
        transcript_data: Optional[Dict[str, Any]],
        keep_at_least: int = 0,
    ) -> List[Dict[str, Any]]:
        """Attach a retention-structure record to each segment and demote.

        Workstream B: every candidate is analysed for opening structure
        (first second, curiosity, context, build, payoff, ending, and —
        as a hard defect — a hook the footage does not deliver). The
        influence is bounded and demote-only (see
        auto_director.retention.selection_for): at most
        MAX_RETENTION_PENALTY points, never a promotion, and outright
        rejection only for an overpromising, context-missing candidate
        while at least ``keep_at_least`` segments remain. The record is
        stored on the segment dict and later embedded in the clip's
        stored plan JSON. Gated by RETENTION_ANALYSIS_ENABLED (default
        on); a failure anywhere keeps the segments untouched.
        """
        from ..auto_director.retention import (
            analyse_retention,
            apply_retention_to_segments,
            retention_enabled,
        )

        if not retention_enabled():
            return segments
        analyses = []
        for segment in segments:
            try:
                start = parse_timestamp_to_seconds(str(segment.get("start_time") or ""))
                end = parse_timestamp_to_seconds(str(segment.get("end_time") or ""))
            except Exception:
                start, end = 0.0, 0.0
            words = (
                VideoService._segment_words(transcript_data, start, end)
                if end > start
                else []
            )
            analyses.append(
                analyse_retention(
                    text=str(segment.get("text") or ""),
                    words=words,
                    duration=max(0.0, end - start),
                    segment_start=start,
                    hook_title=segment.get("hook_title"),
                )
            )
        return apply_retention_to_segments(
            segments, analyses, keep_at_least=keep_at_least
        )

    @staticmethod
    def _drop_slate_segments(
        video_path: Path, segments: List[Dict[str, Any]]
    ) -> List[Dict[str, Any]]:
        """Pre-filter segments sitting on ad breaks / BRB slates.

        One ffmpeg pass builds a whole-video luma-motion map
        (``media.slate_exclusion``, PeakCut take); a segment whose
        length is mostly inside a static stretch is excluded before the
        per-segment still-picture check runs. Exclusion only — order
        and scores are untouched. If every segment would go (a podcast
        over cover art), all are kept.
        """
        from ..media.slate_exclusion import (
            filter_segments_by_slate,
            slate_exclusion_enabled,
            static_ranges,
        )

        if not slate_exclusion_enabled() or not segments:
            return segments
        ranges = static_ranges(video_path)
        if not ranges:
            return segments
        kept, excluded = filter_segments_by_slate(
            segments,
            ranges,
            bounds_of=lambda s: (
                parse_timestamp_to_seconds(str(s.get("start_time") or "")),
                parse_timestamp_to_seconds(str(s.get("end_time") or "")),
            )
            if s.get("start_time") and s.get("end_time")
            else None,
        )
        for segment in excluded:
            logger.warning(
                "Excluding segment %s-%s: %.1fs of it sits on a static "
                "slate (ad break / be-right-back card)",
                segment.get("start_time"),
                segment.get("end_time"),
                segment.get("slate_overlap_seconds", 0.0),
            )
        return kept

    @staticmethod
    def _drop_static_segments(
        video_path: Path, segments: List[Dict[str, Any]]
    ) -> List[Dict[str, Any]]:
        """Leave out segments whose picture is one still frame (slate, card, black).

        Selection only reads the transcript, so a strong passage spoken over a
        "footage unavailable" card would otherwise lead the results. When every
        segment is static (an audio podcast with cover art), all are kept.
        """
        notices = [False] * len(segments)
        fractions: List[Optional[float]] = []
        static: List[bool] = []
        for segment, notice in zip(segments, notices):
            fraction: Optional[float] = None
            is_static = False
            try:
                start = parse_timestamp_to_seconds(str(segment.get("start_time") or ""))
                end = parse_timestamp_to_seconds(str(segment.get("end_time") or ""))
                ranges = local_ranges_for(video_path, [(start, end)])
                fraction = segment_static_fraction(video_path, ranges)
                notice = segment_shows_interruption_notice(video_path, ranges)
                notices[len(static)] = notice
                is_static = is_visually_static(fraction) or notice
                # A presenter in front of a fixed backdrop barely moves either;
                # only a held picture without a face is a card or placeholder.
                if is_static and still_picture_shows_face(video_path, ranges):
                    logger.info(
                        "Segment %s-%s holds one picture for %.0f%% but shows a face; keeping it",
                        segment.get("start_time"),
                        segment.get("end_time"),
                        (fraction or 0.0) * 100,
                    )
                    is_static = False
            except Exception as exc:
                logger.warning(
                    "Visual check unavailable for segment %s-%s: %s",
                    segment.get("start_time"),
                    segment.get("end_time"),
                    exc,
                )
                is_static = False
            fractions.append(fraction)
            static.append(is_static)

        if not any(static):
            return segments
        if all(static) and not any(notices):
            logger.warning(
                "Every selected segment shows a still picture; keeping them all "
                "because the whole source looks static"
            )
            return segments
        kept = []
        for segment, fraction, is_static in zip(segments, fractions, static):
            if is_static:
                logger.warning(
                    "Dropping segment %s-%s: one still picture covers %.0f%% of it "
                    "(interruption card, placeholder or black)",
                    segment.get("start_time"),
                    segment.get("end_time"),
                    (fraction or 0.0) * 100,
                )
                continue
            kept.append(segment)
        return kept

    @staticmethod
    def _get_file_duration(path: Path) -> Optional[float]:
        """Return video duration in seconds via ffprobe, or None on failure."""
        try:
            result = subprocess.run(
                [
                    "ffprobe", "-v", "error",
                    "-show_entries", "format=duration",
                    "-of", "csv=p=0",
                    str(path),
                ],
                capture_output=True, text=True, check=True,
            )
            return float(result.stdout.strip())
        except Exception:
            return None

    @staticmethod
    def _build_fallback_segment(
        video_duration: Optional[float],
        transcript: str,
        target_duration: int,
        analysis_failed: bool = False,
    ) -> Dict[str, Any]:
        """Create a bounded starter clip when AI analysis selects no segments.

        When ``analysis_failed`` is true the AI never ran, so the
        reasoning is stamped with the "(AI analysis unavailable)" marker
        the task page's DegradedAnalysisNotice keys on — a degraded run
        must never look like a normal AI-picked run. The caller passes
        true only for the outage context (not when the transcript is
        unusable: that path wins by design and stays unmarked). An
        AI-success-but-zero-segments fallback keeps the plain wording:
        the AI served, it simply found no strong segment.
        """
        fallback_duration = max(1.0, float(target_duration or 30))
        if video_duration and video_duration > 0:
            fallback_duration = min(fallback_duration, max(1.0, video_duration))

        transcript_preview = " ".join((transcript or "").split())
        if len(transcript_preview) > 240:
            transcript_preview = f"{transcript_preview[:237]}..."

        reasoning = (
            "AI analysis did not identify a strong standalone segment, "
            "so SupoClip generated the first available portion of the video."
        )
        if analysis_failed:
            reasoning = f"(AI analysis unavailable) {reasoning}"

        return {
            "start_time": "00:00",
            "end_time": seconds_to_mmss(fallback_duration),
            "text": transcript_preview,
            "relevance_score": 0.25,
            "reasoning": reasoning,
            "virality_score": 0,
            "hook_score": 0,
            "engagement_score": 0,
            "value_score": 0,
            "shareability_score": 0,
            "hook_type": "fallback",
            "hook_title": None,
        }

    @staticmethod
    def _floor_hook_title(text: str) -> Optional[str]:
        """Derive a floor segment's hook title from its own spoken words.

        On the standard path the AI analysis writes each segment's hook
        title (sanitized by ``ai.sanitize_hook_title``); the
        deterministic floor has no AI, so its segments historically
        shipped with ``hook_title: None`` and rendered with no title at
        all (observation O-R3). The floor's title is the segment's own
        first sentence, passed through the SAME sanitizer the AI path
        uses — same length caps, same third-person guardrail — so the
        title reaches the renderer through the same segment field and
        is drawn exactly like an AI-picked one. A segment with no words
        (a genuinely transcript-less source) honestly keeps None.
        """
        cleaned = " ".join((text or "").split())
        if not cleaned:
            return None
        sentence = cleaned
        for index, char in enumerate(cleaned):
            if char in ".!?" and cleaned[index + 1 : index + 2] in ("", " "):
                sentence = cleaned[: index + 1]
                break
        try:
            from ..ai import sanitize_hook_title

            return sanitize_hook_title(sentence, fallback_text=cleaned)
        except Exception:
            logger.warning(
                "Floor hook title derivation failed", exc_info=True
            )
            return None

    @staticmethod
    def _apply_brief_gate(
        segments_json: List[Dict[str, Any]],
        content_brief: Dict[str, Any],
        transcript: str,
        ai_brief_report: Optional[Dict[str, Any]] = None,
    ) -> tuple:
        """S2 selection gate (Content Requirements, design §2.5).

        Re-asserts range compliance on the final slate (paranoia against
        opening refinement moving a start), attaches matcher verdicts,
        and drops Tier-C segments (no mandatory requirement satisfied)
        whenever any Tier A/B candidate exists — the reserve order
        backfills the slate. If only Tier C exists, the slate empties:
        the outcome is no-compliant-moments, never Tier-C clips.
        Returns (gated_segments, brief_report).
        """
        from ..ai import _parse_transcript_lines
        from ..auto_director.content_requirements import (
            allowed_range_tuples,
            build_brief_report,
            clamp_segment_outside_excluded,
            clamp_segment_to_allowed,
            evaluate_candidate_requirements,
            excluded_range_tuples,
            lines_in_window,
        )

        allowed = allowed_range_tuples(content_brief)
        excluded = excluded_range_tuples(content_brief)
        transcript_lines = _parse_transcript_lines(transcript or "")
        total_before_gate = len(segments_json)
        dropped_range = 0
        gated: List[Dict[str, Any]] = []
        for segment in segments_json:
            try:
                seg_start = parse_timestamp_to_seconds(str(segment.get("start_time") or ""))
                seg_end = parse_timestamp_to_seconds(str(segment.get("end_time") or ""))
            except Exception:
                dropped_range += 1
                continue
            if allowed:
                pieces = clamp_segment_to_allowed(seg_start, seg_end, allowed)
                if not pieces:
                    dropped_range += 1
                    continue
                piece_start, piece_end = max(pieces, key=lambda p: p[1] - p[0])
                if piece_start > seg_start or piece_end < seg_end:
                    segment = {
                        **segment,
                        "start_time": seconds_to_mmss(piece_start),
                        "end_time": seconds_to_mmss(piece_end),
                    }
                    seg_start, seg_end = piece_start, piece_end
            if excluded:
                pieces = clamp_segment_outside_excluded(seg_start, seg_end, excluded)
                if not pieces:
                    dropped_range += 1
                    continue
                piece_start, piece_end = max(pieces, key=lambda p: p[1] - p[0])
                if piece_start > seg_start or piece_end < seg_end:
                    segment = {
                        **segment,
                        "start_time": seconds_to_mmss(piece_start),
                        "end_time": seconds_to_mmss(piece_end),
                    }
                    seg_start, seg_end = piece_start, piece_end
            verdict = evaluate_candidate_requirements(
                transcript_lines,
                float(seg_start),
                float(seg_end),
                content_brief,
                llm_available=True,
            )
            gated.append(
                {**segment, "brief_verdict": verdict, "brief_tier": verdict["tier"]}
            )
        tiers = [s["brief_tier"] for s in gated]
        if gated and all(t == "C" for t in tiers):
            gated = []
        elif gated:
            gated = [s for s in gated if s["brief_tier"] != "C"] or gated
        tier_a = sum(1 for s in gated if s["brief_tier"] == "A")
        if isinstance(ai_brief_report, dict):
            brief_report = {
                **ai_brief_report,
                "candidates_dropped_range": (
                    ai_brief_report.get("candidates_dropped_range", 0) + dropped_range
                ),
                "outcome": "clips_selected" if gated else "no_compliant_moments",
            }
        else:
            brief_report = build_brief_report(
                content_brief,
                candidates_total=total_before_gate,
                candidates_in_range=total_before_gate - dropped_range,
                candidates_compliant=tier_a,
                candidates_dropped_range=dropped_range,
                candidates_dropped_mandatory=total_before_gate - dropped_range - tier_a,
                outcome="clips_selected" if gated else "no_compliant_moments",
                llm_pass="not_run",
            )
        # Campaign rules (Chopify take): hard enforcement at cut time,
        # after the tier gate. A segment that breaks the brief's
        # must-mention / banned / length / aspect rules is rejected with
        # its reasons recorded in the report — never silently shipped.
        from ..auto_director.campaign_rules import (
            campaign_rules_enabled,
            campaign_rules_of,
            enforce_campaign_rules,
        )

        rules = campaign_rules_of(content_brief) if campaign_rules_enabled() else None
        if rules and gated:
            # Segments normally carry their transcript text; fill any
            # that don't from the transcript lines inside their window
            # so must-mention checks judge real speech, not an empty
            # string.
            filled: List[Dict[str, Any]] = []
            for segment in gated:
                if str(segment.get("text") or "").strip():
                    filled.append(segment)
                    continue
                try:
                    seg_start = parse_timestamp_to_seconds(
                        str(segment.get("start_time") or "")
                    )
                    seg_end = parse_timestamp_to_seconds(
                        str(segment.get("end_time") or "")
                    )
                    window_text = " ".join(
                        str(line.get("text", ""))
                        for line in lines_in_window(
                            transcript_lines, float(seg_start), float(seg_end)
                        )
                    )
                    filled.append({**segment, "text": window_text})
                except Exception:
                    filled.append(segment)
            gated = filled
            kept, rejected = enforce_campaign_rules(gated, rules)
            if rejected:
                logger.warning(
                    "Campaign rules rejected %d of %d gated segments: %s",
                    len(rejected),
                    len(gated),
                    [
                        {
                            "start_time": r.get("start_time"),
                            "end_time": r.get("end_time"),
                            "reasons": r.get("campaign_rejection"),
                        }
                        for r in rejected
                    ],
                )
                gated = kept
                brief_report = {
                    **brief_report,
                    "campaign_rejections": [
                        {
                            "start_time": r.get("start_time"),
                            "end_time": r.get("end_time"),
                            "reasons": r.get("campaign_rejection") or [],
                        }
                        for r in rejected
                    ],
                    "candidates_dropped_campaign": len(rejected),
                    "outcome": (
                        "clips_selected" if gated else "no_campaign_compliant_moments"
                    ),
                }
        return gated, brief_report

    @staticmethod
    def resolve_local_video_path(url: str) -> Path:
        """Resolve uploaded-video references without exposing server filesystem paths."""
        if url.startswith(UPLOAD_URL_PREFIX):
            if url.startswith("upload://s3/"):
                storage = MediaStorage()
                return storage.local_path(storage.source_reference(url))
            filename = Path(url.removeprefix(UPLOAD_URL_PREFIX)).name
            path = Path(get_config().temp_dir) / "uploads" / filename
            if path.is_file():
                return path
            reference = read_upload_reference(path)
            if reference:
                try:
                    return MediaStorage().local_path(reference)
                except (ValueError, OSError):
                    return path
            return path
        raise ValueError("Only upload:// references are allowed for local video sources")

    async def resolve_stored_upload(self, url: str) -> Path:
        """Resolve an upload for editing, including its durable object when the local file is gone."""
        local = await run_in_thread(self.resolve_local_video_path, url)
        if local.is_file() or str(url).startswith("upload://s3/"):
            return local
        materialized = await materialize_stored_upload(url)
        if materialized is not None and materialized.is_file():
            return materialized
        return local

    @staticmethod
    def probe_upload_duration(url: str, user_id: str, storage_reference: str | None = None) -> float:
        """Read an uploaded video's length without downloading it."""
        if url.startswith("upload://s3/"):
            storage = MediaStorage()
            reference = storage.validate_source(url, user_id)
            # ffprobe reads only the container headers through ranged requests.
            return ffprobe_duration(storage.signed_url(reference, "source.mp4", expires_in=300))
        path = VideoService.resolve_local_video_path(url)
        if path.is_file():
            return ffprobe_duration(path)
        # Local file and sidecar are both gone. The upload session still names
        # the durable object; ffprobe reads its headers through a signed URL.
        if storage_reference:
            storage = MediaStorage()
            if storage.enabled:
                return ffprobe_duration(storage.signed_url(storage_reference, "source.mp4", expires_in=300))
        return ffprobe_duration(path)

    @staticmethod
    async def download_video(
        url: str,
        task_id: Optional[str] = None,
        quality: Optional[str] = None,
        ranges: Optional[list] = None,
        budget: Optional[ByteBudget] = None,
        skip_proxy: bool = False,
        errors: Optional[list] = None,
        progress_callback: Optional[ProgressCallback] = None,
        download_progress: int = 10,
    ) -> Optional[Path]:
        """
        Download YouTube media for rendering.

        With a proxy configured, ``ranges`` downloads only those padded
        sections (reusing the cache). Otherwise this returns a full file,
        stored so later edits do not fetch it again. ``errors`` collects the
        redacted reason of each failed attempt.
        """
        logger.info(f"Starting video download: {url}")
        video_id = get_youtube_video_id(url)

        def _resolve() -> Optional[Path]:
            if not video_id:
                return None
            job_budget = budget or ByteBudget(get_config().youtube_download_max_bytes)
            bypass_proxy = skip_proxy
            section_error = None
            if ranges:
                try:
                    segments = [
                        {"start_time": start, "end_time": end}
                        for start, end in ranges
                    ]
                    return materialize_clip_source(
                        url,
                        video_id,
                        segments,
                        task_id=task_id,
                        budget=job_budget,
                        quality=quality,
                    )
                except DownloadBudgetExceeded:
                    raise
                except Exception as exc:
                    section_error = exc
                    # Only unsupported slicing warrants a full proxy attempt.
                    bypass_proxy = skip_proxy or not isinstance(exc, PartialDownloadUnavailable)
                    if errors is not None and not isinstance(exc, PartialDownloadUnavailable):
                        errors.append(public_download_error(exc))
                    logger.warning(
                        "Section download unavailable for %s, using a full file: %s",
                        video_id,
                        exc,
                    )
                    report_download_recovery(DownloadRecovery.FULL_VIDEO)
            path = resolve_full_youtube_download(
                url,
                video_id,
                lambda **kwargs: download_youtube_video(
                    url, 3, task_id, quality, errors=errors, **kwargs
                ),
                task_id=task_id,
                budget=job_budget,
                skip_proxy=bypass_proxy,
                quality=quality,
            )
            return preserve_geo_restriction(path, section_error)

        try:
            video_path = await run_download_in_thread(
                _resolve, progress_callback=progress_callback, progress=download_progress,
            )
        except DownloadBudgetExceeded:
            logger.error("YouTube download exceeded the per-job byte limit: %s", url)
            raise

        if not video_path:
            logger.error(f"Failed to download video: {url}")
            return None

        logger.info(f"Video downloaded successfully: {video_path}")
        if video_id:
            try:
                await run_in_thread(MediaStorage().restore_youtube_metadata, video_id, Path(video_path))
            except BaseException:
                await run_in_thread(cleanup_downloaded_files, video_id, source_path=Path(video_path))
                raise
        return video_path

    @staticmethod
    async def prepare_source(path: Path) -> Path:
        """Scale a local source down to at most 1080p and 60 fps.

        Plan-specific output (for example 720p) is applied later, when clips
        are rendered. This only enforces the absolute ceiling. An uploaded
        original is copied to object storage first, so the capped file can
        be stored beside that original.
        """
        from ..media.upload_ingest import ensure_local_upload_durable

        await ensure_local_upload_durable(Path(path))
        prepared = await run_in_thread(normalize_source_media, path)
        return prepared if isinstance(prepared, Path) else path

    @staticmethod
    async def get_video_title(url: str) -> str:
        """
        Get video title asynchronously.
        Returns a default title if retrieval fails.
        """
        try:
            title = await async_get_youtube_video_title(url)
            return title or "YouTube Video"
        except Exception as e:
            logger.warning(f"Failed to get video title: {e}")
            return "YouTube Video"

    @staticmethod
    async def generate_transcript(
        video_path: Path,
        processing_mode: str = "balanced",
        source_url: Optional[str] = None,
        separation_opt_in: bool = False,
    ) -> str:
        """
        Generate transcript from video using the configured provider
        (assemblyai, whisper, or youtube_captions).
        Runs in thread pool to avoid blocking.
        """
        logger.info(f"Generating transcript for: {video_path}")
        speech_model = "best"
        runtime_config = get_config()
        # The fast-mode model selector is only meaningful for AssemblyAI; the
        # other providers ignore it and would otherwise silently change behaviour.
        if processing_mode == "fast" and runtime_config.transcription_provider == "assemblyai":
            speech_model = runtime_config.fast_mode_transcript_model

        transcript = await run_in_thread(
            get_video_transcript,
            video_path,
            speech_model,
            source_url,
            **({"separation_opt_in": True} if separation_opt_in else {}),
        )
        logger.info(f"Transcript generated: {len(transcript)} characters")
        return transcript

    @staticmethod
    async def _learned_weights_for_auto_niche(
        learning_document: Dict[str, Any],
        transcript: str,
        *,
        title: Optional[str] = None,
    ) -> Optional[Dict[str, float]]:
        """Learned selection weights for an AUTO (unpinned) task.

        Classifies the transcript's niche and returns the learning
        document's weights for it. Returns None — base selection
        behaviour — when classification falls back (no confident niche),
        when the niche has no qualifying adjustment, or on any error:
        learning must never break or distort analysis.
        """
        try:
            from ..auto_director.classify import (
                classify_niche,
                guardrail_evidence_sample,
            )
            from ..auto_director.learning import score_weights_for

            sample = (transcript or "").strip()[:2000]
            if not sample:
                return None
            classification = await classify_niche(
                sample,
                [],
                title=title,
                guardrail_sample=guardrail_evidence_sample(transcript),
            )
            if not classification:
                return None
            if classification.get("is_fallback"):
                return None
            if float(classification.get("confidence") or 0) <= 0:
                return None
            if "fallback" in (classification.get("reasons") or []):
                return None
            return score_weights_for(
                learning_document, classification.get("niche")
            )
        except Exception:
            logger.warning(
                "Auto-niche learning resolution failed; using base weights",
                exc_info=True,
            )
            return None

    @staticmethod
    async def _classify_source_niche(
        *,
        video_path: Path,
        transcript: str,
        segments: List[Dict[str, Any]],
        title: Optional[str] = None,
        description: Optional[str] = None,
        duration: Optional[float] = None,
    ) -> Optional[Dict[str, Any]]:
        """Classify the source's niche from title + visual + audio evidence.

        This is the producer the render path always lacked: downstream
        (task_service, VideoService.create_single_clip) already reads a
        per-segment ``classifier_niche``, but nothing ever set it, so
        every auto task silently resolved to the fallback profile. The
        evidence is reused, not recomputed from scratch: sampled-frame
        face/scene analysis comes from the reframe planner's cached
        ``get_source_analysis`` over the selected segments' own ranges,
        and the audio summary from the beat-grid analyser's cached grid.
        Speech share comes from the transcript word timings on disk.

        Defensive by design: every evidence source degrades to None
        independently, classification failure yields the classifier's
        marked fallback, and any unexpected error returns None — a
        classification problem must never cost the user their clips.
        The returned dict is the classifier's full result (niche,
        confidence, reasons, is_fallback, evidence, evidence_status).
        """
        try:
            from ..auto_director.classify import (
                classify_niche,
                guardrail_evidence_sample,
                summarize_audio_beats,
                summarize_visual_analysis,
            )

            sample = (transcript or "").strip()[:2000]
            # The deterministic guardrails score a widened sample
            # (head + quartile windows over the full transcript), so
            # a topic that starts after the opening minutes is
            # visible to them; the model prompt keeps the head
            # sample alone.
            guardrail_sample = guardrail_evidence_sample(transcript)

            spans: List[tuple] = []
            for segment in segments[:8]:
                try:
                    start = parse_timestamp_to_seconds(
                        str(segment.get("start_time") or "")
                    )
                    end = parse_timestamp_to_seconds(
                        str(segment.get("end_time") or "")
                    )
                except Exception:
                    continue
                if end > start:
                    spans.append((float(start), float(end)))
            if not spans and duration and duration > 0:
                spans = [(0.0, min(float(duration), 120.0))]

            visual_summary = None
            if spans:
                try:
                    from ..media.reframe_plan import get_source_analysis

                    analysis = await run_in_thread(
                        get_source_analysis, video_path, spans
                    )
                    visual_summary = summarize_visual_analysis(
                        analysis, duration=duration
                    )
                except Exception:
                    logger.warning(
                        "Visual evidence unavailable for classification",
                        exc_info=True,
                    )
                    visual_summary = None

            audio_summary = None
            try:
                from ..auto_director.beat_grid import analyse_audio_beats

                beat_grid = await run_in_thread(analyse_audio_beats, video_path)
                speech_fraction = None
                try:
                    transcript_data = load_cached_transcript_data(video_path)
                    words = (transcript_data or {}).get("words") or []
                    if words and duration and duration > 0:
                        covered = sum(
                            max(
                                0.0,
                                (float(w.get("end", 0)) - float(w.get("start", 0)))
                                / 1000.0,
                            )
                            for w in words
                        )
                        speech_fraction = min(1.0, covered / float(duration))
                except Exception:
                    speech_fraction = None
                audio_summary = summarize_audio_beats(
                    beat_grid, speech_fraction=speech_fraction
                )
            except Exception:
                logger.warning(
                    "Audio evidence unavailable for classification",
                    exc_info=True,
                )
                audio_summary = None

            if not sample and visual_summary is None and not title:
                # Nothing at all to classify with (no words, no picture
                # evidence, no title): stay unclassified rather than
                # spending a model call on an empty prompt.
                return None
            return await classify_niche(
                sample,
                [],
                title=title,
                description=description,
                visual_evidence=visual_summary,
                audio_evidence=audio_summary,
                guardrail_sample=guardrail_sample,
            )
        except Exception:
            logger.warning(
                "Source niche classification failed; clips will use the "
                "default profile",
                exc_info=True,
            )
            return None

    @staticmethod
    async def analyze_transcript(
        transcript: str,
        clip_signals: Optional[str] = None,
        performance_context: Optional[str] = None,
        clip_length: str = "auto",
        score_weights: Optional[Dict[str, float]] = None,
        window_checkpoint: Optional[Dict[str, Any]] = None,
        on_window_complete: Optional[Any] = None,
        learning_document: Optional[Dict[str, Any]] = None,
        target_clip_count: Optional[int] = None,
        source_title: Optional[str] = None,
        source_description: Optional[str] = None,
        scene_cut_times: Optional[List[float]] = None,
        content_brief: Optional[Dict[str, Any]] = None,
        creator_name: Optional[str] = None,
    ) -> Any:
        """
        Analyze transcript with AI to find relevant segments.
        This is already async, no need to wrap.

        ``score_weights`` carries learned per-niche virality sub-score
        weights (auto_director.learning); None = base selection behaviour.
        When weights were not precomputed (an auto-niche task) but a
        ``learning_document`` is supplied, the niche is classified from
        the transcript here — the first point where a resolved niche
        honestly exists — and that niche's learned weights apply.
        Precomputed (pinned-niche) weights always win.
        """
        if score_weights is None and learning_document:
            score_weights = await VideoService._learned_weights_for_auto_niche(
                learning_document, transcript, title=source_title
            )
        logger.info("Starting AI analysis of transcript")
        relevant_parts = await get_most_relevant_parts_by_transcript(
            transcript,
            clip_signals=clip_signals,
            performance_context=performance_context,
            clip_length=clip_length,
            include_reserve=True,
            score_weights=score_weights,
            window_checkpoint=window_checkpoint,
            on_window_complete=on_window_complete,
            target_clip_count=target_clip_count,
            content_brief=content_brief,
            creator_name=creator_name,
            source_title=source_title,
            source_description=source_description,
            scene_cut_times=scene_cut_times,
        )
        logger.info(
            f"AI analysis complete: {len(relevant_parts.most_relevant_segments)} segments found"
        )
        return relevant_parts

    @staticmethod
    async def plan_broll(
        video_path: Path,
        keep_ranges: List[tuple],
        output_format: str = "vertical",
        max_short_side: Optional[int] = None,
        session: Optional[BRollSession] = None,
    ) -> List[Dict[str, Any]]:
        """Plan and fetch stock cutaways for a clip rendered from ``keep_ranges``."""
        if not stock_broll_configured() or not keep_ranges:
            return []
        try:
            transcript = await run_in_thread(load_cached_transcript_data, video_path)
            words = get_words_for_keep_ranges(transcript, keep_ranges) if transcript else []
            # The renderer crossfades between kept ranges, which shortens the clip.
            clip_seconds = rendered_ranges_duration(keep_ranges)
            orientation = "portrait"
            target_short_side = 1080
            if output_format == "original":
                width, height = await run_in_thread(ffprobe_video_size, video_path)
                if width > height:
                    orientation = "landscape"
                target_short_side = min(target_short_side, min(width, height) or target_short_side)
            if max_short_side:
                target_short_side = min(target_short_side, max_short_side)
            return await plan_clip_broll(
                words, clip_seconds, orientation, target_short_side, session
            )
        except Exception as exc:
            logger.warning("B-roll unavailable for this clip: %s", exc)
            return []

    @staticmethod
    async def create_video_clips(
        video_path: Path,
        segments: List[Dict[str, Any]],
        font_family: Optional[str] = None,
        font_size: Optional[int] = None,
        font_color: Optional[str] = None,
        caption_template: str = "default",
        output_format: str = "vertical",
        add_subtitles: bool = True,
        cleanup_settings: Optional[Dict[str, Any]] = None,
        max_short_side: Optional[int] = None,
        max_clip_seconds: float = 60.0,
        include_broll: bool = False,
        vocals_stem: Optional[Path] = None,
        task_text_bands: Optional[Dict[str, Any]] = None,
        allowed_ranges: Optional[List[Tuple[float, float]]] = None,
        excluded_ranges: Optional[List[Tuple[float, float]]] = None,
        framing_mode: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """
        Create standalone video clips from segments with optional subtitles.
        ``max_short_side`` downscales the finished clips for plans below 1080p.
        With ``include_broll``, a segment's stored ``broll`` placements are
        reused; segments without them get a fresh plan.
        ``vocals_stem`` is the separated vocals track a separation-opt-in
        task's clips should draw their audio from instead of the raw mix;
        None (every historical caller) renders exactly as before.
        ``task_text_bands`` is the task's stored text-band decision
        (media.text_bands), reused verbatim by regeneration; None
        renders with per-clip bands exactly as before.
        ``framing_mode`` is the task's framing choice ("fit" / "crop";
        see ``auto_director.framing_mode``), forwarded to the batch
        renderer so regenerated clips keep the framing the task was
        created with; None renders exactly as before.
        Runs in thread pool as video processing is CPU-intensive.
        output_format: 'vertical' (9:16) or 'original' (keep source size, faster).
        add_subtitles: False skips subtitles; with original format uses ffmpeg stream copy (no re-encode).
        """
        logger.info(f"Creating {len(segments)} video clips subtitles={add_subtitles}")
        clips_output_dir = Path(get_config().temp_dir) / "clips"
        clips_output_dir.mkdir(parents=True, exist_ok=True)

        if include_broll:
            session = BRollSession()
            segments = [dict(segment) for segment in segments]
            for segment in segments:
                placements = segment.get("broll") or []
                if not placements:
                    keep_ranges = normalize_source_ranges(segment.get("keep_ranges"))
                    if not keep_ranges and segment.get("source_ranges"):
                        keep_ranges = await run_in_thread(
                            build_keep_ranges_from_source_ranges,
                            video_path,
                            normalize_source_ranges(segment.get("source_ranges")),
                            cleanup_settings,
                        )
                    placements = await VideoService.plan_broll(
                        video_path, keep_ranges, output_format, max_short_side, session
                    )
                segment["broll"] = placements
                segment["broll_overlays"] = await broll_overlays_for(placements)
        else:
            segments = [{**segment, "broll": [], "broll_overlays": []} for segment in segments]

        clips_info = await run_in_thread(
            create_clips_with_transitions,
            video_path,
            segments,
            clips_output_dir,
            font_family,
            font_size,
            font_color,
            caption_template,
            output_format,
            add_subtitles,
            cleanup_settings,
            max_clip_seconds=max_clip_seconds,
            vocals_stem=vocals_stem,
            task_text_bands=task_text_bands,
            allowed_ranges=allowed_ranges,
            excluded_ranges=excluded_ranges,
            framing_mode=framing_mode,
        )

        if max_short_side:
            for clip_info in clips_info:
                try:
                    await run_in_thread(cap_video_short_side, Path(clip_info["path"]), max_short_side)
                    # The re-encode can shift the length by a frame.
                    clip_info["duration"] = await run_in_thread(
                        rendered_clip_duration,
                        Path(clip_info["path"]),
                        clip_info.get("keep_ranges") or [],
                    )
                except Exception as e:
                    logger.warning(f"Keeping full-size clip {clip_info.get('filename')}: {e}")

        logger.info(f"Successfully created {len(clips_info)} clips")
        return clips_info

    @staticmethod
    def build_pipeline_edl(
        segment: Dict[str, Any],
        transcript_words: List[Dict[str, Any]],
        niche_override: Optional[str] = None,
        project_niche_override: Optional[str] = None,
        classifier_niche: Optional[str] = None,
        duration: Optional[float] = None,
        music_override: Optional[str] = None,
        music_mode: Optional[str] = None,
        template_mode: Optional[str] = None,
        broll_enabled: Optional[bool] = None,
        brand_application: Optional[Dict[str, Any]] = None,
        profile_adjustments: Optional[Dict[str, Any]] = None,
        profile_adjustments_niche: Optional[str] = None,
        video_path: Optional[Path] = None,
        broll_ledger: Optional[Any] = None,
    ) -> Optional[Dict[str, Any]]:
        """Build the auto-director EDL for a main-pipeline clip, or None.

        Mirrors the re-render path (``ClipService._build_rerender_edl``): the
        effective profile is resolved with the same precedence the caption
        builder and the deliverable bundle use, loaded, and handed to
        ``build_edl`` with the clip's transcript words. ``duration`` is the
        rendered (post-cleanup) clip length when the caller knows it, so the
        EDL tiles the timeline that actually renders. Energy peaks are not
        persisted for pipeline clips, so ``[]`` is passed — build_edl falls
        back to the profile's default zoom/SFX placement, exactly as at
        re-render time.

        Defensive by design: any failure (unknown profile, malformed
        segment, build_edl raising) logs a warning and returns None, and the
        caller renders through the long-standing plain path. An EDL problem
        must never cost the user a clip.
        """
        try:
            from ..auto_director.edl import build_edl, load_profile
            from ..auto_director.niche import resolve_effective_profile

            profile_name = resolve_effective_profile(
                niche_override, project_niche_override, classifier_niche
            )
            if not profile_name:
                logger.warning(
                    "No niche profile resolved; rendering clip without an EDL"
                )
                return None
            profile = load_profile(profile_name)
            music_silenced_by_learning = False
            if profile_adjustments and profile_adjustments_niche == profile_name:
                # Learned adjustments (auto_director.learning) apply to a
                # copy of the freshly loaded profile — the shipped profile
                # files are never mutated. The niche guard means an
                # adjustment only ever lands on the profile it was learned
                # from, even if resolution picked a different one.
                from ..auto_director.learning import apply_profile_adjustments

                profile = apply_profile_adjustments(profile, profile_adjustments)
                try:
                    music_silenced_by_learning = (
                        float(profile_adjustments.get("music_volume_multiplier"))
                        == 0.0
                    )
                except (TypeError, ValueError):
                    music_silenced_by_learning = False

            start_seconds = parse_timestamp_to_seconds(segment["start_time"])
            end_seconds = parse_timestamp_to_seconds(segment["end_time"])
            clip_duration = (
                duration if duration and duration > 0 else end_seconds - start_seconds
            )
            if clip_duration <= 0:
                logger.warning(
                    "Clip has no positive duration; rendering without an EDL"
                )
                return None
            clip = {
                "id": str(segment.get("id") or segment.get("clip_id") or "clip"),
                "start": start_seconds,
                "end": end_seconds,
                "duration": clip_duration,
            }
            words: List[Dict[str, Any]] = []
            for word in transcript_words or []:
                text = str(word.get("word") or word.get("text") or "").strip()
                if not text:
                    continue
                try:
                    word_start = float(word.get("start", 0.0) or 0.0)
                    word_end = float(word.get("end", word_start) or word_start)
                except (TypeError, ValueError):
                    word_start, word_end = 0.0, 0.0
                entry: Dict[str, Any] = {
                    "word": text, "start": word_start, "end": word_end
                }
                # Diarization labels ride along when the transcript has
                # them (AssemblyAI): the EDL's speaker-change punch-ins
                # for dialogue profiles read exactly this field, and
                # dropping it here silently disabled them on the main
                # pipeline. Unlabelled transcripts stay unlabelled.
                if word.get("speaker") is not None:
                    entry["speaker"] = word.get("speaker")
                words.append(entry)
            # Beat grid for the fast-pace profiles (football / anime /
            # music / gaming / sports): analyse the track that will be
            # mixed — or the source itself for music-led content — and
            # project it onto this clip's timeline. Best-effort like
            # everything in this seam: no grid, no beat consumption,
            # the EDL is exactly what it was.
            beat_grid = None
            try:
                from ..auto_director.beat_grid import (
                    profile_beat_config,
                    resolve_clip_grid,
                )

                if template_mode == "plain":
                    # Plain — just cuts: no beat consumption (its ramps
                    # and beat-snapped joins are template treatment), so
                    # the grid is never even resolved.
                    beat_grid = None
                elif profile_beat_config(profile) is not None:
                    beat_grid = resolve_clip_grid(
                        profile,
                        music_override=music_override,
                        source_path=video_path,
                        clip_start=start_seconds,
                        clip_duration=clip_duration,
                    )
            except Exception:
                logger.warning(
                    "Beat grid resolution failed; building EDL without it",
                    exc_info=True,
                )
                beat_grid = None
            # Audio-reactive flashes for opted-in profiles without a
            # beat grid (meme_comedy): project the source's energy
            # levels onto the clip so build_edl can plan the pulses.
            # Beat profiles flash off their beat grid instead — it
            # already describes the audio that will be heard. Plain
            # never resolves either grid. Best-effort, like the beat
            # grid above: any failure builds the EDL without flashes.
            flash_grid = None
            if template_mode != "plain" and beat_grid is None:
                try:
                    from ..auto_director.audio_flashes import (
                        profile_flash_config,
                        resolve_flash_grid,
                    )

                    if profile_flash_config(profile) is not None:
                        flash_grid = resolve_flash_grid(
                            profile,
                            source_path=video_path,
                            clip_start=start_seconds,
                            clip_duration=clip_duration,
                        )
                except Exception:
                    logger.warning(
                        "Flash grid resolution failed; building EDL without it",
                        exc_info=True,
                    )
                    flash_grid = None
            # The task's B-roll switch, resolved against its template:
            # Off means build_edl plans zero cues, so the stock
            # resolution below never runs for this task either.
            from ..auto_director.broll_setting import resolve_broll_enabled
            from ..auto_director.content_cuts import (
                content_cuts_enabled,
                detect_clip_scene_cuts,
            )

            if content_cuts_enabled() and video_path is not None and "scene_cuts" not in clip:
                # Real source shot changes: the only visual reason the
                # content-cut planner accepts for a cut (sentence ends
                # are the other). A failure just means sentence cuts.
                try:
                    clip = {
                        **clip,
                        "scene_cuts": detect_clip_scene_cuts(
                            video_path, start_seconds, clip_duration,
                        ),
                    }
                except Exception:
                    logger.warning("Scene-cut detection failed; cutting on sentences only")
            edl = build_edl(
                clip,
                words,
                profile,
                [],
                beat_grid=beat_grid,
                flash_grid=flash_grid,
                broll_enabled=resolve_broll_enabled(template_mode, broll_enabled),
            )
            if template_mode == "plain":
                # Plain — just cuts: strip every template-driven element
                # and return. No Brand Kit render blocks, no music chain
                # (the plainified EDL already carries the Music Off
                # silence), no B-roll resolution.
                from ..auto_director.template_mode import plainify_edl

                return plainify_edl(edl)
            if brand_application:
                # Brand Kit (task opted in): stamp the kit's render
                # inputs onto the EDL. The caption builder reads the
                # template override, the EDL renderer composites the
                # watermark, the hook block records the kit's title
                # wording style, and the deliverable bundle reuses the
                # same blocks for the cover. Absent kit = no blocks =
                # the EDL is exactly what it was. This runs BEFORE the
                # music chain on purpose: the kit's own music choice
                # arrives as this call's music_override/mode inputs and
                # is applied by the chain below, exactly as on the
                # brand-kit branch where the stamp was a standalone if.
                if brand_application.get("brand_template_override"):
                    edl["brand_template_override"] = brand_application[
                        "brand_template_override"
                    ]
                if brand_application.get("brand_title"):
                    edl["brand_title"] = brand_application["brand_title"]
                if brand_application.get("brand_watermark"):
                    edl["brand_watermark"] = brand_application["brand_watermark"]
                if brand_application.get("brand_bumpers"):
                    edl["brand_bumpers"] = brand_application["brand_bumpers"]
                if brand_application.get("title_style"):
                    edl.setdefault("hook", {})["title_style"] = brand_application[
                        "title_style"
                    ]
            if music_mode == "off":
                # Music mode Off is the user's explicit instruction for
                # the whole task: no bed at all, and it wins over a picked
                # track and over the profile's own bed.
                from ..media.music_library import apply_music_off

                apply_music_off(edl)
            elif music_override:
                # A track the user picked in the create bar beats the
                # profile's mood; silent profiles get audible defaults so
                # the choice actually plays (see music_library).
                from ..media.music_library import apply_music_override

                apply_music_override(edl, music_override)
            elif music_mode == "on":
                # Music mode On without a pick: the profile's own bed
                # already sits on the EDL from build_edl, and that is
                # the bed that plays. When the profile carries no bed,
                # On falls back to the same Auto-bed selection Auto
                # makes — the user explicitly asked for music, so
                # silence would contradict the instruction (self-run
                # audit issue #3). apply_auto_music_bed is a no-op
                # when the profile already has a bed.
                from ..media.music_library import apply_auto_music_bed

                apply_auto_music_bed(edl, profile_name)
            else:
                # Music = Auto: when the resolved profile carries no bed
                # of its own, Auto still owes the clip a subtle ducked
                # library bed (user directive 2026-11-08 — a podcast
                # rendered in silence because its profile mood is "none").
                # One exception: a learned adjustment that explicitly
                # zeroed this niche's music is a deliberate silence, not
                # a missing bed — the Auto bed must not override it, or
                # the learning loop could never turn a niche's music off.
                # (An explicit user music choice above still wins: the
                # off/override/on branches are untouched.)
                from ..media.music_library import apply_auto_music_bed

                if music_silenced_by_learning:
                    logger.info(
                        "Music is Auto but a learned adjustment silenced "
                        "music for niche %s; keeping the clip bed-free",
                        profile_name,
                    )
                else:
                    apply_auto_music_bed(edl, profile_name)
            if edl.get("broll"):
                # Resolve stock footage while the EDL is being planned, not
                # inside the render: the Pixabay search/download then never
                # sits on the render path, and the renderer's own resolve
                # call is a no-op for the stamped entries. Best-effort by
                # design — a resolution failure leaves entries unresolved
                # and the renderer skips them, exactly as before. With a
                # task ledger the resolution excludes assets already
                # committed to the task's earlier clips, so sibling
                # clips never repeat footage.
                try:
                    from ..media.pixabay_broll import resolve_edl_broll

                    if broll_ledger is not None:
                        broll_ledger.resolve(edl)
                    else:
                        resolve_edl_broll(edl)
                except Exception:
                    logger.warning(
                        "B-roll resolution failed during EDL planning",
                        exc_info=True,
                    )
            return edl
        except Exception:
            logger.warning(
                "Could not build EDL for clip; rendering without it", exc_info=True
            )
            return None

    @staticmethod
    async def _generate_viral_plan(
        *,
        video_path: Path,
        segment: Dict[str, Any],
        text: str,
        words: List[Dict[str, Any]],
        profile_name: Optional[str],
        source_start: float,
        source_end: float,
        duration: float,
    ) -> Optional[tuple]:
        """Generate the per-clip viral plan; None when disabled/failed.

        Returns ``(plan, profile)``. Split from consumption so the
        Quality Director's hook enforcement can inspect (and, for a cold
        open, remap) the plan before it is folded into an EDL.
        """
        try:
            from ..auto_director import plan_service

            if not plan_service.viral_plan_enabled():
                return None
            from ..auto_director.edl import load_profile

            try:
                profile = load_profile(profile_name) if profile_name else {}
            except Exception:
                profile = {}
            niche = str(profile.get("niche") or profile_name or "podcast_news")
            audio_features = await run_in_thread(
                plan_service.compute_audio_features,
                video_path,
                source_start,
                source_end,
            )
            plan = await plan_service.generate_clip_plan(
                niche=niche,
                profile=profile,
                duration=duration,
                text=text,
                words=words,
                audio_features=audio_features,
                selection_scores={
                    key: segment.get(key)
                    for key in (
                        "virality_score",
                        "hook_score",
                        "engagement_score",
                        "value_score",
                        "shareability_score",
                    )
                },
            )
            return plan, profile
        except Exception:
            logger.warning(
                "Viral plan generation failed; rendering unplanned",
                exc_info=True,
            )
            return None

    @staticmethod
    def _consume_viral_plan(
        edl: Dict[str, Any],
        plan: Any,
        profile: Dict[str, Any],
        transcript_words: Optional[List[Dict[str, Any]]] = None,
    ) -> None:
        """Fold a generated plan into the EDL (see _apply_viral_plan).

        ``transcript_words`` gates exactly one consumer: the plan's
        B-roll concepts (Smart B-Roll v2) anchor on spoken words, so
        they are placed only by callers holding the clip's word
        timings. The re-render path (clip_service) calls without them
        on purpose — it carries the first render's saved, resolved
        B-roll entries over the fresh EDL, and re-placing concept cues
        there would silently swap the footage the user already saw.
        """
        from ..auto_director.clip_plan import (
            apply_plan_broll_concepts,
            apply_plan_opening_punch,
            apply_plan_transitions,
            merge_plan_sfx_events,
        )

        # Consumption: a planner-produced plan changes the render —
        # its transition choices land on the EDL's internal joins,
        # its opening punch zoom joins the EDL's zoom keyframes, and
        # its caption style is recorded on the EDL's captions block
        # (rendered caption styling is applied through the caption
        # template seam; see docs/viral-intelligence.md). The
        # deterministic fallback plan is record-only: it describes
        # today's behaviour and must never alter it.
        plan_events = plan.sfx_events
        if plan.source == "llm":
            joins_before = [
                (s.get("transition_in"), s.get("transition_duration"))
                for s in (edl.get("segments") or [])
            ]
            transitions_applied = apply_plan_transitions(edl, plan, profile)
            # Transition discipline (retention lane): the plan's
            # choices land on synthetic joins, so a choice whose join
            # sits inside continuous speech — and is not on a phase
            # boundary the planner itself detected — is downgraded to
            # a hard cut, and no decorative choice repeats back-to-back.
            # Only the joins the plan actually changed are judged: the
            # profile's own grammar is not a per-join decision. The
            # downgrades join the EDL's record next to any the builder
            # already stamped.
            from ..auto_director.edl import enforce_transition_discipline

            changed_joins = {
                index
                for index, segment in enumerate(edl.get("segments") or [])
                if index < len(joins_before)
                and (
                    segment.get("transition_in"),
                    segment.get("transition_duration"),
                )
                != joins_before[index]
            }
            discipline_notes = enforce_transition_discipline(
                edl.get("segments") or [],
                transcript_words or [],
                boundary_times=tuple(
                    float(p.start_offset) for p in plan.phases
                ),
                only_indices=changed_joins,
            )
            if discipline_notes:
                edl["transition_discipline"] = (
                    edl.get("transition_discipline") or []
                ) + discipline_notes
            punch_applied = apply_plan_opening_punch(edl, plan, profile)
            if plan.caption_style is not None:
                edl.setdefault("captions", {})["preset_id"] = (
                    plan.caption_style.name
                )
            if transitions_applied or punch_applied or plan.caption_style:
                logger.info(
                    "Viral plan consumed for clip: %d transition join(s), "
                    "opening punch %s, caption style %s",
                    transitions_applied,
                    "applied" if punch_applied else "unchanged",
                    plan.caption_style.name if plan.caption_style else "profile",
                )
            # B-roll concepts (Smart B-Roll v2): after the punch, so
            # concept cues respect the plan's own punch keyframe like
            # every other cue. None means "not applicable" (no
            # concepts, or no word timings at this call site) and the
            # EDL's keyword-planned cues stand untouched.
            concept_placements = apply_plan_broll_concepts(
                edl, plan, profile, transcript_words
            )
            if concept_placements is not None:
                logger.info(
                    "Viral plan B-roll concepts consumed for clip: "
                    "%d concept cue(s) placed",
                    concept_placements,
                )
            # Plan SFX offsets are clip-timeline, but the EDL's
            # native events live on the rendered timeline (a
            # crossfade pulls everything after it earlier). Map the
            # plan's events through the final join plan so an accent
            # lands on the moment the planner named. The stored plan
            # keeps the planner's clip-timeline offsets.
            from ..auto_director.clip_plan import PlannedSfxEvent
            from ..auto_director.edl import _join_plan, _to_rendered_time

            joins = _join_plan(edl.get("segments") or [])
            if any(join["fade"] > 0 for join in joins):
                plan_events = [
                    PlannedSfxEvent(
                        at_offset=_to_rendered_time(event.at_offset, joins),
                        name=event.name,
                        reason=event.reason,
                    )
                    for event in plan.sfx_events
                ]
        edl["sfx_events"] = merge_plan_sfx_events(
            edl.get("sfx_events") or [], plan_events
        )

    @staticmethod
    async def _apply_viral_plan(
        edl: Dict[str, Any],
        *,
        video_path: Path,
        segment: Dict[str, Any],
        text: str,
        words: List[Dict[str, Any]],
        profile_name: Optional[str],
        source_start: float,
        source_end: float,
    ) -> Optional[Dict[str, Any]]:
        """Generate the per-clip viral plan and consume it into the EDL.

        The Viral Intelligence Engine's seam: it runs where the pipeline
        finalises a segment's EDL, before rendering, so the plan's
        decisions are part of the render — its SFX accents merge into the
        EDL's events, its transition choices land on the EDL's internal
        joins, its opening punch zoom joins the zoom keyframes — and the
        plan itself can be stored on the clip record. Returns the plan
        dict to persist, or None when planning is disabled
        (``VIRAL_PLAN_ENABLED=false`` — the EDL is then exactly what
        ``build_pipeline_edl`` produced).

        Defensive by design, like the EDL builder it follows: ANY failure
        logs a warning and returns None with the EDL untouched. A plan
        problem must never cost the user a clip.
        """
        try:
            generated = await VideoService._generate_viral_plan(
                video_path=video_path,
                segment=segment,
                text=text,
                words=words,
                profile_name=profile_name,
                source_start=source_start,
                source_end=source_end,
                duration=float(edl.get("duration") or 0.0)
                or max(0.0, source_end - source_start),
            )
            if generated is None:
                return None
            plan, profile = generated
            VideoService._consume_viral_plan(edl, plan, profile, words)
            return plan.to_dict()
        except Exception:
            logger.warning(
                "Viral plan wiring failed; rendering with the unplanned EDL",
                exc_info=True,
            )
            return None

    @staticmethod
    async def create_single_clip(
        video_path: Path,
        segment: Dict[str, Any],
        clip_index: int,
        output_dir: Path,
        font_family: Optional[str] = None,
        font_size: Optional[int] = None,
        font_color: Optional[str] = None,
        caption_template: str = "default",
        output_format: str = "vertical",
        add_subtitles: bool = True,
        cleanup_settings: Optional[Dict[str, Any]] = None,
        max_short_side: Optional[int] = None,
        max_clip_seconds: float = 60.0,
        include_broll: bool = False,
        broll_session: Optional[BRollSession] = None,
        broll_ledger: Optional[Any] = None,
        edl: Optional[Dict[str, Any]] = None,
        music_override: Optional[str] = None,
        music_mode: Optional[str] = None,
        template_mode: Optional[str] = None,
        framing_mode: Optional[str] = None,
        layout_template: Optional[str] = None,
        layout_credit: Optional[str] = None,
        broll_enabled: Optional[bool] = None,
        hook_title_enabled: bool = True,
        vocals_stem: Optional[Path] = None,
        brand_application: Optional[Dict[str, Any]] = None,
        niche_override: Optional[str] = None,
        project_niche_override: Optional[str] = None,
        classifier_niche: Optional[str] = None,
        profile_adjustments: Optional[Dict[str, Any]] = None,
        profile_adjustments_niche: Optional[str] = None,
        allowed_ranges: Optional[List] = None,
        excluded_ranges: Optional[List] = None,
        ai_editing: bool = True,
        task_text_bands: Optional[Dict[str, Any]] = None,
    ) -> Optional[Dict[str, Any]]:
        """Render a single clip in the thread pool and return clip_info dict, or None on failure.

        ``max_short_side`` downscales the finished clip for plans below 1080p.
        ``include_broll`` is the LEGACY B-roll flag: it is honoured only
        when the task's per-task switch (``broll_enabled``) was never
        set — an explicit switch value is authoritative over it (the
        U3 contract in ``auto_director.broll_setting.resolve_broll_gate``).
        ``broll_session`` keeps one task's clips from reusing the same
        footage on the legacy path, and ``broll_ledger`` does the same
        for the EDL path's stock resolution
        (media.pixabay_broll.BrollUsageLedger).
        ``ai_editing`` is the creation-mode treatment switch: when False
        (Find Moments + Manual), the auto-director treatment — pipeline
        EDL, Viral Intelligence plan, hook teaser, beat-grid tightening
        and the profile caption-pacing cap — is skipped and the clip
        renders as a plain cut with the caller's caption settings.
        ``template_mode="plain"`` (or a pre-built EDL stamped by
        ``plainify_edl``) is the Plain — just cuts contract: the pipeline
        EDL is still built (the niche still informs captions) but
        plainified, the Viral Intelligence plan, hook teaser, beat-grid
        tightening, legacy B-roll and the Brand Kit application are all
        skipped, and the reframe plan is the always-full-frame tight
        crop. Silence tightening from the cleanup settings still runs —
        no silence is exactly what Plain asks for.
        ``framing_mode`` is the task's framing choice (see
        ``auto_director.framing_mode``): when given it — not the
        template — decides the reframe plan ("fit" always letterboxes,
        "crop" always full-bleed crops, "auto" lets the layout planner
        decide), without touching anything else the template controls.
        None (every caller that does not know the task) leaves the
        historic template-driven framing exactly as it was.
        """
        try:
            from ..auto_director.template_mode import is_plain

            plain = is_plain(template_mode) or (
                isinstance(edl, dict) and edl.get("template") == "plain"
            )
            if plain:
                # No kit dressing on a Plain render: no watermark, no
                # bumpers, no caption-template/title override, no kit
                # font colour. The kit itself is untouched and returns
                # in full for Auto tasks.
                brand_application = None
                if isinstance(edl, dict) and edl.get("template") != "plain":
                    # A caller-supplied EDL (a stored segment plan) gets
                    # the same strip: Plain is a property of the task,
                    # not of which seam built the EDL.
                    from ..auto_director.template_mode import plainify_edl

                    plainify_edl(edl)
            provided_keep_ranges = normalize_source_ranges(segment.get("keep_ranges"))
            source_ranges = normalize_source_ranges(segment.get("source_ranges"))
            provided_bounds = source_range_bounds(provided_keep_ranges or source_ranges)
            if provided_bounds:
                start_seconds, end_seconds = provided_bounds
            else:
                start_seconds = parse_timestamp_to_seconds(segment["start_time"])
                end_seconds = parse_timestamp_to_seconds(segment["end_time"])
            duration = end_seconds - start_seconds

            if duration <= 0:
                logger.warning(
                    f"Skipping clip {clip_index + 1}: invalid duration {duration:.1f}s"
                )
                return None

            unique_suffix = uuid.uuid4().hex[:12]
            clip_filename = (
                f"clip_{clip_index + 1}_"
                f"{segment['start_time'].replace(':', '')}-"
                f"{segment['end_time'].replace(':', '')}_"
                f"{unique_suffix}.mp4"
            )
            clip_path = output_dir / clip_filename
            if provided_keep_ranges:
                keep_ranges = provided_keep_ranges
            elif source_ranges:
                keep_ranges = build_keep_ranges_from_source_ranges(
                    video_path,
                    source_ranges,
                    cleanup_settings,
                )
            else:
                start_seconds = snap_clip_start_to_sentence(
                    video_path, start_seconds, end_seconds
                )
                keep_ranges = build_clip_keep_ranges(
                    video_path,
                    start_seconds,
                    end_seconds,
                    cleanup_settings,
                )
            # Beat-grid silence tightening for the fast-pace profiles
            # (opt-in per profile): silences longer than ~0.6 s inside
            # the clip are cut down to a breath using transcript word
            # timings, so a cut never lands mid-word. Runs before the
            # sentence-boundary extension so complete thoughts stay
            # complete. Explicit keep ranges (editor / hook-variant
            # selections) are the user's own cuts — never re-cut here.
            if ai_editing and not plain and keep_ranges and not provided_keep_ranges:
                try:
                    from ..auto_director.beat_grid import (
                        profile_beat_config,
                        tighten_keep_ranges,
                    )
                    from ..auto_director.edl import load_profile
                    from ..auto_director.niche import resolve_effective_profile

                    effective_niche = resolve_effective_profile(
                        niche_override,
                        project_niche_override,
                        classifier_niche,
                    )
                    tighten_cfg = (
                        profile_beat_config(load_profile(effective_niche))
                        if effective_niche
                        else None
                    )
                    if tighten_cfg is not None and tighten_cfg.get("silence_tighten"):
                        transcript_data = load_cached_transcript_data(video_path) or {}
                        if transcript_data.get("words"):
                            # Cached words are millisecond-stamped; the
                            # helper returns second-based words in source
                            # time, which is the space keep ranges live in.
                            from ..media.timeline import get_absolute_words_in_range

                            timed_words: list[dict] = []
                            for range_start, range_end in keep_ranges:
                                timed_words.extend(
                                    get_absolute_words_in_range(
                                        transcript_data, range_start, range_end
                                    )
                                )
                            if timed_words:
                                keep_ranges = tighten_keep_ranges(
                                    keep_ranges, timed_words
                                )
                except Exception:
                    logger.warning(
                        "Beat-grid silence tightening failed; keeping ranges",
                        exc_info=True,
                    )
            keep_ranges = extend_keep_ranges_to_sentence_boundary(video_path, keep_ranges)
            clip_text = segment.get("text", "")
            if ai_editing and not plain and not provided_keep_ranges and not source_ranges:
                keep_ranges = apply_segment_hook_teaser(video_path, keep_ranges, segment)
            keep_ranges = clamp_keep_ranges_to_max_duration(video_path, keep_ranges, max_clip_seconds)
            if not provided_keep_ranges and not source_ranges:
                # Snapping, cleanup, the teaser and the cap change what is
                # spoken; keep the stored text word-for-word with the timeline.
                clip_text = keep_ranges_transcript_text(video_path, keep_ranges) or clip_text

            broll_placements: List[Dict[str, Any]] = []
            # The per-task B-roll switch gates the legacy cutaway plan
            # too: resolved Off means no placements and no stock calls,
            # exactly as Plain already guarantees. The resolution is
            # resolve_broll_gate, never an AND of the two stored flags:
            # an explicit broll_enabled is authoritative, and the
            # legacy include_broll is honoured only when the switch was
            # never set on the task (U3 contract, see broll_setting).
            from ..auto_director.broll_setting import resolve_broll_gate

            if not plain and resolve_broll_gate(
                template_mode, broll_enabled, include_broll
            ):
                broll_placements = await VideoService.plan_broll(
                    video_path, keep_ranges, output_format, max_short_side, broll_session
                )
            broll_overlays = await broll_overlays_for(broll_placements)
            render_report: Dict[str, Any] = {}

            # The caption builder needs the effective profile up front (the
            # hindi_podcast profile romanizes Devanagari caption words); the
            # deliverable bundle below resolves the same value for itself.
            try:
                from ..auto_director.niche import resolve_effective_profile

                caption_profile_name: Optional[str] = resolve_effective_profile(
                    niche_override,
                    project_niche_override,
                    classifier_niche,
                )
            except Exception:
                caption_profile_name = None

            # Caption pacing cap from the same profile: fast-pace profiles
            # hold a caption for at most ~2.5 s, the rest ~3.5 s, so a slow
            # line can never sit on screen for many seconds. Unknown profile
            # -> None -> the caption builder's default cap applies.
            caption_hold_cap: Optional[float] = None
            if caption_profile_name and ai_editing:
                try:
                    from ..auto_director.edl import load_profile
                    from ..media.captions import caption_hold_cap_for_profile

                    caption_hold_cap = caption_hold_cap_for_profile(
                        load_profile(caption_profile_name)
                    )
                except Exception:
                    caption_hold_cap = None

            clip_plan_dict: Optional[Dict[str, Any]] = None
            if edl is None and ai_editing:
                # Main-pipeline callers never supply an EDL: build one from
                # the effective profile so the render below applies the
                # profile's transitions, zooms, music and SFX instead of the
                # plain path. Callers that pass their own EDL (the clip
                # re-render flow) keep full control of it.
                try:
                    transcript_data = await run_in_thread(
                        load_cached_transcript_data, video_path
                    )
                    timed_words = (
                        get_words_for_keep_ranges(transcript_data, keep_ranges)
                        if transcript_data
                        else []
                    )
                except Exception:
                    timed_words = []
                if not timed_words:
                    # Same degraded word source the re-render path uses: the
                    # clip's own text, split into untimed words.
                    timed_words = [
                        {"word": word, "start": 0.0, "end": 0.0}
                        for word in clip_text.split()
                    ]
                edl = VideoService.build_pipeline_edl(
                    segment,
                    timed_words,
                    niche_override,
                    project_niche_override,
                    classifier_niche,
                    duration=rendered_ranges_duration(keep_ranges),
                    music_override=music_override,
                    music_mode=music_mode,
                    template_mode=template_mode,
                    broll_enabled=broll_enabled,
                    brand_application=brand_application,
                    profile_adjustments=profile_adjustments,
                    profile_adjustments_niche=profile_adjustments_niche,
                    video_path=video_path,
                    broll_ledger=broll_ledger,
                )
                if edl is not None and not plain:
                    # Viral Intelligence Engine: plan this clip's edit and
                    # merge the plan's SFX accents into the EDL before it
                    # renders. Never fails the clip (see _apply_viral_plan).
                    generated = await VideoService._generate_viral_plan(
                        video_path=video_path,
                        segment=segment,
                        text=clip_text,
                        words=timed_words,
                        profile_name=caption_profile_name,
                        source_start=start_seconds,
                        source_end=end_seconds,
                        duration=float(edl.get("duration") or 0.0)
                        or max(0.0, end_seconds - start_seconds),
                    )
                    if generated is not None:
                        plan, plan_profile = generated
                        # Quality Director hook enforcement: when the
                        # planned hook lands late, its whole sentence is
                        # moved to the front (cold open) by reordering
                        # the keep ranges — unless a context guard says
                        # the move would orphan the moment. The decision
                        # and its reason are recorded on the plan.
                        hook_outcome: Optional[Dict[str, Any]] = None
                        try:
                            from ..auto_director.hook_enforcement import (
                                evaluate_hook,
                            )

                            decision = evaluate_hook(
                                plan,
                                words=timed_words,
                                keep_ranges=keep_ranges,
                                duration=rendered_ranges_duration(keep_ranges),
                            )
                            hook_outcome = decision.outcome
                            if (
                                decision.keep_ranges is None
                                and (hook_outcome or {}).get("status") == "no_hook"
                            ):
                                # Trend-Radar D7 fallback: when the
                                # planner named no hook, travel profiles
                                # carrying the bold-claim opener rule
                                # nominate a claim/cost sentence instead.
                                # Profile-gated — every other profile
                                # gets an empty decision and today's
                                # behaviour exactly.
                                from ..auto_director.hook_enforcement import (
                                    evaluate_bold_claim_opener,
                                )

                                claim_decision = evaluate_bold_claim_opener(
                                    plan,
                                    words=timed_words,
                                    keep_ranges=keep_ranges,
                                    duration=rendered_ranges_duration(
                                        keep_ranges
                                    ),
                                    profile=plan_profile,
                                )
                                if claim_decision.outcome is not None:
                                    hook_outcome = claim_decision.outcome
                                if claim_decision.keep_ranges is not None:
                                    decision = claim_decision
                            if decision.keep_ranges is not None:
                                rebuilt_words = (
                                    get_words_for_keep_ranges(
                                        transcript_data, decision.keep_ranges
                                    )
                                    if transcript_data
                                    else timed_words
                                )
                                rebuilt_edl = VideoService.build_pipeline_edl(
                                    segment,
                                    rebuilt_words,
                                    niche_override,
                                    project_niche_override,
                                    classifier_niche,
                                    duration=rendered_ranges_duration(
                                        decision.keep_ranges
                                    ),
                                    music_override=music_override,
                                    music_mode=music_mode,
                                    template_mode=template_mode,
                                    broll_enabled=broll_enabled,
                                    brand_application=brand_application,
                                    profile_adjustments=profile_adjustments,
                                    profile_adjustments_niche=(
                                        profile_adjustments_niche
                                    ),
                                    video_path=video_path,
                                    broll_ledger=broll_ledger,
                                )
                                if rebuilt_edl is not None:
                                    # Commit the reorder only with an EDL
                                    # built for the new timeline.
                                    keep_ranges = decision.keep_ranges
                                    plan = decision.plan or plan
                                    edl = rebuilt_edl
                                    timed_words = rebuilt_words
                                    clip_text = (
                                        keep_ranges_transcript_text(
                                            video_path, keep_ranges
                                        )
                                        or clip_text
                                    )
                                    # Legacy B-roll overlays were placed
                                    # on the pre-reorder timeline; the
                                    # rebuilt EDL plans its own B-roll.
                                    broll_placements = []
                                    broll_overlays = []
                                else:
                                    hook_outcome = {
                                        **(hook_outcome or {}),
                                        "status": "kept_original_context",
                                        "reason": (
                                            "the reordered edit could not be "
                                            "rebuilt, so the original order "
                                            "is kept"
                                        ),
                                        # The clip renders in the original
                                        # order: the hook lands where it
                                        # always did, and the record must
                                        # say so.
                                        "hook_timestamp_after": (
                                            (hook_outcome or {}).get(
                                                "hook_timestamp_before"
                                            )
                                        ),
                                    }
                        except Exception:
                            logger.warning(
                                "Hook enforcement failed; rendering the "
                                "clip in its original order",
                                exc_info=True,
                            )
                            hook_outcome = None
                        try:
                            VideoService._consume_viral_plan(
                                edl, plan, plan_profile, timed_words
                            )
                            clip_plan_dict = plan.to_dict()
                            if hook_outcome:
                                clip_plan_dict["hook_application"] = hook_outcome
                            # Retention record (Workstream B): additive
                            # key inside the existing plan JSON — the
                            # same storage as hook_application, no new
                            # column. Only present when the analysis
                            # stage attached one to this segment.
                            retention_record = segment.get("retention")
                            if isinstance(retention_record, dict):
                                clip_plan_dict["retention"] = retention_record
                        except Exception:
                            logger.warning(
                                "Viral plan wiring failed; rendering with "
                                "the unplanned EDL",
                                exc_info=True,
                            )
                            clip_plan_dict = None

            if edl is not None and "reframe" not in edl:
                # Subject-aware reframe plan for the EDL renderer: without
                # it the EDL graph centre-crops every segment and anyone
                # outside the middle of the frame is lost. Computed from
                # the per-source cached analysis over the exact keep ranges
                # this clip renders (covers caller-supplied EDLs from the
                # re-render flow too). attach_reframe_plan never raises and
                # leaves the EDL untouched on failure.
                try:
                    from ..media.reframe_plan import attach_reframe_plan

                    await run_in_thread(
                        attach_reframe_plan,
                        edl,
                        video_path,
                        keep_ranges,
                        plain,
                        framing=framing_mode,
                        layout_template=layout_template,
                    )
                except Exception as exc:
                    logger.warning("Reframe planning skipped: %s", exc)

            if allowed_ranges or excluded_ranges:
                # R1 render clamp (Content Requirements): after the LAST
                # keep-range mutator (sentence-boundary extension, hook
                # teaser, hook-enforcement reorder, duration clamp), so
                # no expansion can re-add out-of-range footage. An empty
                # intersection renders nothing rather than leaking.
                from ..auto_director.content_requirements import (
                    intersect_ranges,
                    subtract_ranges,
                )

                clamped_ranges = keep_ranges
                if allowed_ranges:
                    clamped_ranges = intersect_ranges(
                        clamped_ranges, list(allowed_ranges)
                    )
                if excluded_ranges:
                    clamped_ranges = subtract_ranges(
                        clamped_ranges, list(excluded_ranges)
                    )
                if not clamped_ranges or sum(
                    e - s for s, e in clamped_ranges
                ) < 0.5:
                    logger.warning(
                        "Clip %s has no in-range footage under the brief; "
                        "rendering nothing",
                        clip_index + 1,
                    )
                    return None
                if clamped_ranges != keep_ranges:
                    keep_ranges = clamped_ranges
                    clip_text = (
                        keep_ranges_transcript_text(video_path, keep_ranges)
                        or clip_text
                    )

            if broll_ledger is not None and edl is not None and not plain:
                # Task-level B-roll de-duplication, final pass: the
                # viral plan may have replaced the EDL's cues with
                # concept placements after build_pipeline_edl resolved
                # the keyword ones, so resolve whatever is still
                # unstamped through the task ledger — assets committed
                # to earlier clips of this task are excluded at both
                # providers, and this clip's winners join the ledger
                # before the render starts. Best-effort like every
                # B-roll seam: a failure leaves the renderer's own
                # resolve to mop up, exactly as before.
                try:
                    broll_ledger.resolve(edl)
                except Exception:
                    logger.warning(
                        "Task B-roll ledger resolution failed",
                        exc_info=True,
                    )

            # Voice/music separation (opt-in tasks): the caller resolved
            # the source's vocals stem once for the whole task. Stamp it
            # onto this clip's EDL — the renderer's own carrier for file
            # inputs — so the export's voice chain is cut from clean
            # speech, and hand it down for the no-EDL pre-stage too. The
            # stamp lands after the last EDL mutator above, so a rebuilt
            # or caller-supplied EDL is covered alike. No stem -> no
            # stamp and no kwarg: the render stays the historical one.
            render_vocals_stem = (
                Path(vocals_stem)
                if vocals_stem is not None and Path(vocals_stem).is_file()
                else None
            )
            if render_vocals_stem is not None and edl is not None:
                edl["vocals_stem"] = str(render_vocals_stem)

            # Comment bait (auto_director/comment_bait.py): with
            # COMMENT_BAIT on, the finished clip gets one pinned-comment
            # question generated from its own transcript. The question
            # rides the EDL into the render (media/end_question.py
            # burns it as the end card, and the deliverable bundle
            # prints it into post.txt) and joins the stored clip-plan
            # JSON beside hook_application/retention, so the clip
            # record — and with it SocialService.create_post — carries
            # it. The stamp lands with the vocals-stem stamp, after
            # the last EDL mutator, so a rebuilt or caller-supplied
            # EDL is covered alike. Off (the default) or on any
            # failure: no key anywhere, the render is the historical
            # one. Never fails the clip.
            comment_question: Optional[str] = None
            try:
                from ..auto_director import comment_bait

                if comment_bait.comment_bait_enabled():
                    from ..auto_director.niche import resolve_effective_profile

                    comment_question = await comment_bait.generate_comment_question(
                        niche=resolve_effective_profile(
                            niche_override,
                            project_niche_override,
                            classifier_niche,
                        )
                        or caption_profile_name,
                        hook_title=segment.get("hook_title"),
                        transcript_text=clip_text,
                    )
                    if comment_question and edl is not None:
                        edl["comment_question"] = comment_question
                    if comment_question and clip_plan_dict is not None:
                        clip_plan_dict["comment_question"] = comment_question
            except Exception:
                logger.warning(
                    "Comment bait generation failed; rendering without it",
                    exc_info=True,
                )
                comment_question = None

            success = await run_in_thread(
                create_optimized_clip,
                video_path,
                start_seconds,
                end_seconds,
                clip_path,
                add_subtitles,
                font_family,
                font_size,
                # Brand Kit: the kit's primary colour is the caption
                # colour when the task picked none (explicit task colour
                # already won inside resolve_brand_application).
                (brand_application or {}).get("font_color") or font_color,
                caption_template,
                output_format,
                keep_ranges,
                # Title on/off (create form): off burns no title; the clip
                # keeps its hook_title as its name in the library.
                segment.get("hook_title") if hook_title_enabled else None,
                extend_to_sentence=False,
                layout_report=render_report,
                broll_overlays=broll_overlays,
                edl=edl,
                profile_name=caption_profile_name,
                max_caption_seconds=caption_hold_cap,
                allowed_ranges=list(allowed_ranges) if allowed_ranges else None,
                excluded_ranges=list(excluded_ranges) if excluded_ranges else None,
                **(
                    {"vocals_stem": render_vocals_stem}
                    if render_vocals_stem is not None
                    else {}
                ),
                # Task text bands ride the same conditional-kwarg rule:
                # absent, the leaf call keeps its historical shape.
                **(
                    {"task_text_bands": task_text_bands}
                    if task_text_bands is not None
                    else {}
                ),
                # A streamer layout template ("face on top, game below")
                # rides the same rule: absent, the call shape is unchanged.
                **(
                    {"layout_template": layout_template, "layout_credit": layout_credit}
                    if layout_template
                    else {}
                ),
            )

            if not success:
                logger.error(f"Failed to create clip {clip_index + 1}")
                return None
            if not render_report.get("broll_applied"):
                broll_placements = []
            elif edl is not None:
                # Smart B-Roll v2, Slice 6: on the EDL path the renderer
                # composites the EDL's own resolved entries and ignores
                # the legacy overlays — so the sidecar must record the
                # entries that actually rendered, not the legacy plan.
                # The editor opens the sidecar, so this is what makes
                # the review surface show the cutaways the viewer saw,
                # with their concepts. Unresolved entries convert to
                # nothing; a render with no EDL entries keeps the legacy
                # placements exactly as before.
                from ..broll import edl_broll_entry_to_placement

                edl_placements = [
                    placement
                    for placement in (
                        edl_broll_entry_to_placement(entry)
                        for entry in (edl.get("broll") or [])
                        if isinstance(entry, dict)
                    )
                    if placement is not None
                ]
                if edl_placements:
                    broll_placements = edl_placements

            if max_short_side:
                try:
                    await run_in_thread(cap_video_short_side, clip_path, max_short_side)
                except Exception as e:
                    # A higher-resolution clip beats losing the clip entirely.
                    logger.warning(f"Keeping full-size clip {clip_index + 1}: {e}")

            # Deliverables are an optional convenience layer. A completed render
            # remains usable even if an image encoder or filesystem is unavailable.
            try:
                from ..auto_director.niche import resolve_effective_profile
                from ..deliverable_bundle import write_deliverable_bundle

                effective_profile = resolve_effective_profile(
                    niche_override,
                    project_niche_override,
                    classifier_niche,
                )
                await run_in_thread(
                    write_deliverable_bundle,
                    clip_path,
                    edl,
                    segment,
                    segment.get("hook_title"),
                    profile_name=effective_profile,
                )
            except Exception as exc:
                logger.warning("Could not create deliverable bundle for clip %s: %s", clip_index + 1, exc)

            from ..media.ffmpeg import output_offsets_for_ranges
            save_clip_source_ranges(clip_path, keep_ranges, output_offsets_for_ranges(keep_ranges))
            save_clip_broll(clip_path, broll_placements)
            if edl and isinstance(edl.get("reframe"), dict) and edl["reframe"].get("shots"):
                # The QC subject-in-frame check reads the planned shot
                # modes from the clip's source-map sidecar.
                from ..clip_source_map import save_clip_reframe

                save_clip_reframe(clip_path, edl["reframe"]["shots"])
            # Quality Director compliance facts: what this render
            # actually did — the EDL segment count it joined and the
            # caption chunk timings it burned — so QC can check the
            # cut-rate band and caption pacing on the finished clip.
            try:
                from ..clip_source_map import save_clip_render_facts

                render_facts: Dict[str, Any] = {}
                if edl and edl.get("segments"):
                    render_facts["segment_count"] = len(edl["segments"])
                if render_report.get("caption_chunks") is not None:
                    render_facts["caption_chunks"] = render_report[
                        "caption_chunks"
                    ]
                if render_report.get("caption_hold_cap_seconds") is not None:
                    render_facts["caption_hold_cap_seconds"] = render_report[
                        "caption_hold_cap_seconds"
                    ]
                # The beat-density audit (+ repair) of the finished clip,
                # when BEAT_DENSITY ran it — QC surfaces it as a check.
                if render_report.get("beat_density") is not None:
                    render_facts["beat_density"] = render_report["beat_density"]
                # Words around the clip's start and end, for QC's
                # thought-boundary check (CLIP_QC_THOUGHT_BOUNDARIES).
                from ..media.clip_qc import (
                    qc_thought_boundaries_enabled,
                    transcript_boundary_words,
                )

                if qc_thought_boundaries_enabled():
                    boundary_words = transcript_boundary_words(
                        video_path, float(start_seconds), float(end_seconds)
                    )
                    if boundary_words:
                        render_facts["boundary_words"] = boundary_words
                if render_facts:
                    save_clip_render_facts(clip_path, render_facts)
            except Exception as exc:
                logger.warning(
                    "Could not store render facts for clip %s: %s",
                    clip_index + 1,
                    exc,
                )
            if render_report.get("caption_y") is not None:
                from ..clip_source_map import save_clip_caption_settings
                save_clip_caption_settings(clip_path, {"position_y": render_report["caption_y"], "position_is_center": True})
            # Store what plays, after crossfades and the resolution cap.
            cleaned_duration = await run_in_thread(
                rendered_clip_duration, clip_path, keep_ranges
            )
            logger.info(
                f"Created clip {clip_index + 1}: {cleaned_duration:.1f}s"
                + (f" with {len(broll_placements)} B-roll cutaways" if broll_placements else "")
            )
            return {
                "clip_id": clip_index + 1,
                "filename": clip_filename,
                "path": str(clip_path),
                "start_time": segment["start_time"],
                "end_time": segment["end_time"],
                "duration": cleaned_duration,
                "text": clip_text,
                "relevance_score": segment.get("relevance_score", 0.0),
                "reasoning": segment.get("reasoning", ""),
                "virality_score": segment.get("virality_score", 0),
                "hook_score": segment.get("hook_score", 0),
                "engagement_score": segment.get("engagement_score", 0),
                "value_score": segment.get("value_score", 0),
                "shareability_score": segment.get("shareability_score", 0),
                "hook_type": segment.get("hook_type"),
                "hook_title": segment.get("hook_title"),
                "keep_ranges": keep_ranges,
                "clip_plan": clip_plan_dict,
                # Comment bait: present only when a question was
                # generated for this clip (COMMENT_BAIT on); the key
                # itself is absent otherwise, exactly as before.
                **(
                    {"comment_question": comment_question}
                    if comment_question
                    else {}
                ),
                "retention": segment.get("retention"),
                # F-P3: per-pass render timings measured by
                # create_optimized_clip (None when the render reported
                # none); task_service folds them into stage_timings_json.
                "render_timings": render_report.get("render_timings"),
            }
        except Exception as e:
            logger.error(f"Error creating clip {clip_index + 1}: {e}")
            return None

    @staticmethod
    async def apply_single_transition(
        prev_clip_path: Path,
        current_clip_info: Dict[str, Any],
        clip_index: int,
        output_dir: Path,
    ) -> Dict[str, Any]:
        """Return the original clip info.

        Standalone exports intentionally do not depend on adjacent clips.
        """
        logger.info(
            "Skipping inter-clip transition for clip %s to preserve standalone exports",
            clip_index + 1,
        )
        return current_clip_info

    @staticmethod
    def determine_source_type(url: str) -> str:
        """Determine and validate the supported source type."""
        video_id = get_youtube_video_id(url)
        if video_id:
            return "youtube"
        if url.startswith(UPLOAD_URL_PREFIX):
            return "video_url"
        if not url.startswith(("http://", "https://")):
            raise ValueError("Only YouTube URLs or upload:// references are supported")
        source, _kind, _source_id = detect_stream_source(url)
        return source

    @staticmethod
    async def process_video_complete(
        url: str,
        source_type: str,
        task_id: Optional[str] = None,
        font_family: Optional[str] = None,
        font_size: Optional[int] = None,
        font_color: Optional[str] = None,
        caption_template: str = "default",
        processing_mode: str = "fast",
        output_format: str = "vertical",
        add_subtitles: bool = True,
        max_video_duration: Optional[int] = None,
        download_quality: Optional[str] = None,
        cached_transcript: Optional[str] = None,
        cached_analysis_json: Optional[str] = None,
        progress_callback: Optional[Callable[[int, str, str], Awaitable[None]]] = None,
        should_cancel: Optional[Callable[[], Awaitable[bool]]] = None,
        performance_context: Optional[str] = None,
        clip_length: str = "auto",
        target_clip_count: Optional[int] = None,
        creation_mode: Optional[str] = None,
        manual_ranges: Optional[List[Dict[str, Any]]] = None,
        score_weights: Optional[Dict[str, float]] = None,
        learning_document: Optional[Dict[str, Any]] = None,
        separation_opt_in: bool = False,
        checkpoint: Optional[Dict[str, Any]] = None,
        checkpoint_callback: Optional[Callable[[Dict[str, Any]], Awaitable[None]]] = None,
        source_title: Optional[str] = None,
        source_description: Optional[str] = None,
        project_niche_override: Optional[str] = None,
        content_brief: Optional[Dict[str, Any]] = None,
        creator_name: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Complete video processing pipeline.
        Returns dict with segments and clips info.

        progress_callback: Optional function to call with progress updates
                          Signature: async def callback(progress: int, message: str, status: str)
        """
        video_path: Optional[Path] = None
        audio_first = False
        checkpoint = dict(checkpoint or {})
        # Creation mode (tracker 1.9): the workflow this task runs.
        # A legacy task (no mode) gets the Full AutoClip plan, which is
        # the historical pipeline behaviour-for-behaviour.
        mode_plan = plan_for_mode(creation_mode)
        manual_selection = mode_plan.selection == SELECTION_USER_RANGES
        # Per-stage wall times, returned to the caller and persisted on the
        # task (stage_timings_json) so a slow run can be decomposed from the
        # database and the worker log instead of guessed at. Keys mirror the
        # render/pipeline keys the task service already records.
        stage_timings: Dict[str, float] = {}

        async def save_checkpoint(patch: Dict[str, Any]) -> None:
            if checkpoint_callback:
                await checkpoint_callback(patch)
            checkpoint.update(patch)

        try:
            runtime_config = get_config()
            duration_limit = (
                runtime_config.max_video_duration
                if max_video_duration is None
                else max_video_duration
            )
            # Step 1: Get video path (download or use existing)
            download_started = perf_counter()
            if should_cancel and await should_cancel():
                raise Exception("Task cancelled")

            if progress_callback:
                await progress_callback(10, "Downloading video...", "processing")

            if source_type == "youtube":
                job_budget = ByteBudget(
                    getattr(runtime_config, "youtube_download_max_bytes", 2_000_000_000)
                )
                metadata_error = None
                try:
                    video_info = await async_get_youtube_video_info(
                        url, task_id=task_id, budget=job_budget,
                    )
                except YouTubeGeoRestrictedError as exc:
                    # A retry already passed admission. Metadata failure must
                    # not prevent reuse of saved media or independent providers.
                    metadata_error = exc
                    video_info = None
                    logger.warning("YouTube metadata unavailable; trying saved media and download providers: %s", exc)
                except TimeoutError:
                    video_info = None
                    logger.warning("YouTube metadata timed out; trying saved media and download providers")
                source_duration = None
                if video_info:
                    # Title/description are classification evidence: the
                    # title often names the activity (a restaurant tour)
                    # that a chatty transcript alone reads as a podcast.
                    if not source_title:
                        source_title = video_info.get("title") or None
                    if not source_description:
                        source_description = video_info.get("description") or None
                    duration = video_info.get("duration", 0)
                    source_duration = duration or None
                    if duration and duration > duration_limit:
                        mins = duration_limit // 60
                        raise Exception(
                            f"Video is too long ({duration // 60} min). "
                            f"Maximum allowed duration is {mins} minutes."
                        )

                video_id = get_youtube_video_id(url)
                cached_full = (
                    await run_in_thread(cached_full_media, video_id, download_quality)
                    if video_id else None
                )
                # Audio first only when a proxy is configured and we do not
                # already have the full source cached. Uploads never reach here.
                if proxy_configured(runtime_config) and video_id and not cached_full:
                    def _resolve_analysis():
                        def _download_full():
                            return resolve_full_youtube_download(
                                url,
                                video_id,
                                lambda **kwargs: download_youtube_video(
                                    url, 3, task_id, download_quality, **kwargs
                                ),
                                task_id=task_id,
                                budget=job_budget,
                                skip_proxy=True,
                                quality=download_quality,
                            )

                        return download_analysis_source(
                            url,
                            video_id,
                            task_id=task_id,
                            budget=job_budget,
                            duration=float(source_duration) if source_duration else None,
                            download_full=_download_full,
                        )

                    try:
                        video_path, audio_first = await run_download_in_thread(
                            _resolve_analysis, progress_callback=progress_callback,
                        )
                    except DownloadBudgetExceeded as exc:
                        raise Exception(str(exc)) from exc
                else:
                    audio_first = False
                    video_path = cached_full or await VideoService.download_video(
                        url, task_id=task_id, quality=download_quality, budget=job_budget,
                        progress_callback=progress_callback,
                    )
                video_path = preserve_geo_restriction(video_path, metadata_error)
                if not video_path:
                    raise Exception("Failed to download video")
            elif source_type in {"twitch", "kick"}:
                # The API preflight already measured the source (and the task
                # seeds that length), so no second metadata request is made here.
                # Each provider checks the plan cap before downloading media.
                try:
                    video_path = await run_in_thread(
                        download_stream_audio, url, None, task_id,
                        max_duration=duration_limit, operation=PROCESSING_OPERATION,
                    )
                    audio_first = True
                except StreamSourceError:
                    # Too long, private, or every provider already failed: a
                    # full-video retry would only rerun the same chain.
                    raise
                except Exception as exc:
                    logger.warning("Stream audio download unavailable; using full video: %s", exc)
                    video_path = await run_in_thread(
                        download_stream_video, url, None, task_id,
                        max_duration=duration_limit, operation=PROCESSING_OPERATION,
                    )
            else:
                if url.startswith("upload://s3/"):
                    video_path = await run_in_thread(VideoService.resolve_local_video_path, url)
                else:
                    video_path = await materialize_stored_upload(url)
                    if video_path is None or not video_path.exists():
                        video_path = VideoService.resolve_local_video_path(url)
                if video_path is None or not video_path.exists():
                    raise Exception("Video file not found")

            # Post-download duration guard (catches cases where preflight info was unavailable)
            file_duration = VideoService._get_file_duration(video_path)
            if file_duration and file_duration > duration_limit:
                mins = duration_limit // 60
                raise Exception(
                    f"Video is too long ({int(file_duration) // 60} min). "
                    f"Maximum allowed duration is {mins} minutes."
                )

            if should_cancel and await should_cancel():
                raise Exception("Task cancelled")
            # Uploads only. YouTube and streams already downloaded above.
            if source_type not in {"youtube", "twitch", "kick"} and await upload_needs_remote_copy(url):
                if progress_callback:
                    await progress_callback(12, "Storing upload...", "processing")
                await persist_local_upload(url)
            if progress_callback:
                await progress_callback(15, "Preparing video...", "processing")
            video_path = await VideoService.prepare_source(video_path)
            stage_timings["download_seconds"] = round(
                perf_counter() - download_started, 3
            )
            # One metadata probe per task: which codec every later stage
            # has to decode (the download format string does not choose).
            codec_summary = probe_video_codec(video_path)
            if codec_summary:
                logger.info(f"Source video codec: {codec_summary} ({video_path})")

            # Step 2: Generate transcript
            transcription_started = perf_counter()
            if should_cancel and await should_cancel():
                raise Exception("Task cancelled")

            if progress_callback:
                await progress_callback(30, "Generating transcript...", "processing")

            transcript = checkpoint.get("transcript") or cached_transcript
            transcript_data = checkpoint.get("transcript_data")
            if transcript and isinstance(transcript_data, dict) and transcript_data.get("words"):
                # The source cache may have expired or moved to another worker.
                # Restore the original-source timings, not a stitched timeline.
                sidecar = video_path.with_suffix(".transcript_cache.json")
                temporary = sidecar.with_name(f".{sidecar.name}.{uuid.uuid4().hex}.tmp")
                try:
                    await run_in_thread(temporary.write_text, json.dumps(transcript_data))
                    await run_in_thread(temporary.replace, sidecar)
                finally:
                    temporary.unlink(missing_ok=True)
            # Database text alone cannot recreate timed captions or cleanup.
            # Repair older caches whose word-timing sidecar was removed.
            needs_word_timings = (
                bool(transcript)
                and getattr(runtime_config, "transcription_provider", "assemblyai") != "youtube_captions"
                and not (load_cached_transcript_data(video_path) or {}).get("words")
            )
            if not transcript or needs_word_timings:
                # The historical call shape is kept unless the task
                # opted in — the no-separation path stays untouched.
                transcript = await VideoService.generate_transcript(
                    video_path,
                    processing_mode=processing_mode,
                    source_url=url if source_type == "youtube" else None,
                    **({"separation_opt_in": True} if separation_opt_in else {}),
                )
                if checkpoint.get("analysis"):
                    # Never combine new word timings with an old clip plan.
                    await save_checkpoint({"analysis": None, "candidates": None, "segments": None, "analysis_windows": None})
                    cached_analysis_json = None

            # Commit paid work before any subsequent storage, analysis or download
            # can fail. The DB copy also repairs an absent word-timing sidecar.
            await save_checkpoint({
                "transcript": transcript,
                "transcript_data": await run_in_thread(load_cached_transcript_data, video_path),
            })
            stage_timings["transcription_seconds"] = round(
                perf_counter() - transcription_started, 3
            )

            # Preserve paid word timings before any analysis/cancellation path.
            try:
                await run_in_thread(MediaStorage().save_source_metadata, video_path)
            except Exception:
                logger.warning("Source word timings retained locally for upload retry")
            await run_in_thread(persist_cache_sidecar, video_path)

            # Step 3: AI analysis
            analysis_started = perf_counter()
            if should_cancel and await should_cancel():
                raise Exception("Task cancelled")

            if progress_callback:
                await progress_callback(
                    50, "Analyzing content with AI...", "processing"
                )

            relevant_parts = None
            analysis_failed = False
            if checkpoint.get("analysis"):
                cached_analysis_json = json.dumps(checkpoint["analysis"])
            if (
                cached_analysis_json
                and not manual_selection
                and cached_analysis_is_user_selected(cached_analysis_json)
            ):
                # F-1: a Manual Cutter analysis cached for this source
                # (pre-fix rows share the AI row) must never serve as
                # an AI selection. Drop it and run a fresh analysis
                # instead of silently presenting user ranges as an AI
                # pick. The task layer guards the same read, but the
                # pipeline is the last line of defence for any caller.
                logger.warning(
                    "Ignoring cached transcript analysis written by "
                    "Manual Cutter for a %s run",
                    mode_plan.mode,
                )
                cached_analysis_json = None
            if cached_analysis_json:
                try:
                    cached_analysis = json.loads(cached_analysis_json)
                    segments = cached_analysis.get("most_relevant_segments", [])
                    if not segments and not (checkpoint.get("candidates") or checkpoint.get("segments")):
                        logger.info(
                            "Ignoring cached transcript analysis with no clip segments"
                        )
                    else:

                        class _SimpleResult:
                            def __init__(self, payload: Dict[str, Any]):
                                self.summary = payload.get("summary")
                                self.key_topics = payload.get("key_topics")
                                self.most_relevant_segments = payload.get(
                                    "most_relevant_segments", []
                                )

                        relevant_parts = _SimpleResult(
                            {
                                "summary": cached_analysis.get("summary"),
                                "key_topics": cached_analysis.get("key_topics", []),
                                "most_relevant_segments": segments,
                            }
                        )
                        # A reused selection is never presented as a
                        # fresh AI pick: its provenance rides the
                        # payload stamp (or its absence, for pre-fix
                        # rows) and is logged here.
                        logger.info(
                            "Reusing cached transcript analysis "
                            "(selection_strategy=%s, %d segments)",
                            cached_analysis.get("selection_strategy"),
                            len(segments),
                        )
                except Exception:
                    relevant_parts = None

            if manual_selection:
                # Manual Cutter: the user's ranges ARE the selection, so
                # the AI highlight analysis is never run — not even when
                # a cached analysis exists for this source. Transcription
                # above still ran: captions and text grounding need it.
                relevant_parts = SimpleNamespace(
                    summary=None, key_topics=[], most_relevant_segments=[]
                )

            story_spans: List[Any] = []
            if relevant_parts is None:
                try:
                    clip_signals = await run_in_thread(
                        build_clip_signal_summary,
                        video_path,
                        transcript,
                    )
                except Exception as exc:
                    logger.warning("Clip signal extraction failed: %s", exc)
                    clip_signals = None
                # Measured streamer reactions (voice, facecam, game events)
                # join the ranker's hints; never-fail, opt out with
                # REACTION_SIGNALS=0.
                if file_duration and os.getenv("REACTION_SIGNALS", "1").lower() not in ("0", "false", "off"):
                    try:
                        from ..auto_director.reaction_moments import (
                            build_reaction_hints,
                        )

                        reaction_hints = await run_in_thread(
                            build_reaction_hints, video_path, float(file_duration)
                        )
                        if reaction_hints:
                            clip_signals = (
                                clip_signals + "\n" + reaction_hints
                                if clip_signals
                                else reaction_hints
                            )
                    except Exception as exc:
                        logger.warning("Reaction signal extraction failed: %s", exc)
                # Visual story moments (SELECTION_EVENTS, default off):
                # the picture's events join the hints so moments with no
                # transcribed speech (an award walk-up, a speech with
                # burned-in subtitles) can be selected at all.
                from ..auto_director import story_events as _story

                if _story.story_events_enabled():
                    try:
                        story_spans = await run_in_thread(
                            _story.detect_story_events, video_path
                        )
                        event_lines = _story.event_signal_lines(
                            story_spans,
                            _story.words_from_transcript_data(
                                load_cached_transcript_data(video_path)
                            ),
                        )
                        if event_lines:
                            clip_signals = (
                                clip_signals + "\n" + event_lines
                                if clip_signals
                                else event_lines
                            )
                    except Exception as exc:
                        logger.warning("Story event extraction failed: %s", exc)
                        story_spans = []
                # Scene-cut times already computed elsewhere for
                # reframing now also hint the selector (never-fail:
                # detection returns [] and any error here just means
                # no hints this run).
                scene_cut_times: Optional[List[float]] = None
                try:
                    from ..auto_director.visual_moments import (
                        detect_scene_cut_times,
                    )

                    detected_cuts = await run_in_thread(
                        detect_scene_cut_times, video_path
                    )
                    scene_cut_times = detected_cuts or None
                except Exception as exc:
                    logger.warning("Scene-cut detection failed: %s", exc)
                    scene_cut_times = None
                # Per-window analysis checkpoints: a task that dies mid-
                # analysis (rate limits, a dead gateway) resumes with the
                # windows it already paid for instead of redoing them.
                stored_windows = (checkpoint or {}).get("analysis_windows")
                window_blob: Dict[str, Any] = (
                    dict(stored_windows) if isinstance(stored_windows, dict) else {}
                )

                async def _save_analysis_window(key, index, payload):
                    windows_map = dict(window_blob.get("windows") or {})
                    windows_map[str(index)] = payload
                    window_blob.update({"key": key, "windows": windows_map})
                    await save_checkpoint({"analysis_windows": dict(window_blob)})

                try:
                    relevant_parts = await VideoService.analyze_transcript(
                        transcript,
                        clip_signals=clip_signals,
                        performance_context=performance_context,
                        clip_length=clip_length,
                        score_weights=score_weights,
                        window_checkpoint=window_blob or None,
                        on_window_complete=_save_analysis_window,
                        learning_document=learning_document,
                        target_clip_count=target_clip_count,
                        source_title=source_title,
                        source_description=source_description,
                        scene_cut_times=scene_cut_times,
                        content_brief=content_brief,
                        creator_name=creator_name,
                    )
                except provider_manager.LocalProviderUnavailable:
                    # A deliberate plain-words configuration state
                    # (local-only mode with the local model down): the
                    # user must hear it, not get silent blind clips.
                    raise
                except Exception:
                    # Every provider in the ladder failed (or the
                    # analysis broke trying): the task must not die
                    # here. It continues with the deterministic
                    # audio/visual floor below — loudly logged, and
                    # the fallback segments say in their own reasoning
                    # that the AI never picked them.
                    logger.error(
                        "AI analysis failed; continuing with the "
                        "deterministic visual selection floor",
                        exc_info=True,
                    )
                    analysis_failed = True
                    relevant_parts = SimpleNamespace(
                        summary=(
                            "AI analysis was unavailable, so these clips "
                            "were picked by the deterministic audio/visual "
                            "fallback instead of the AI."
                        ),
                        key_topics=[],
                        most_relevant_segments=[],
                    )

            if not analysis_failed:
                # A degraded run's stand-in result is NOT persisted as
                # the analysis checkpoint: a later retry must
                # re-attempt the real analysis. The floor slate this
                # run picked is still frozen via the candidates
                # checkpoint below, like any other plan.
                await save_checkpoint({"analysis": {
                    "summary": relevant_parts.summary,
                    "key_topics": relevant_parts.key_topics,
                    # Provenance: who selected these segments, so a
                    # later run never mistakes user ranges for an AI
                    # pick (F-1) or vice versa.
                    "selection_strategy": mode_plan.selection,
                    "most_relevant_segments": [
                        segment if isinstance(segment, dict) else segment.model_dump()
                        for segment in relevant_parts.most_relevant_segments
                    ],
                }, "analysis_windows": None})
            stage_timings["analysis_seconds"] = round(
                perf_counter() - analysis_started, 3
            )
            logger.info(f"Pipeline stage timings so far: {stage_timings}")

            if story_spans:
                # A selected clip that ends inside (or just before) a
                # visual story moment runs on until the moment finishes:
                # never cut away before the payoff.
                from ..auto_director import story_events as _story

                _story.extend_segments_to_payoff(
                    relevant_parts.most_relevant_segments,
                    story_spans,
                    _story.words_from_transcript_data(
                        load_cached_transcript_data(video_path)
                    ),
                )

            # Step 4: Create clips
            if should_cancel and await should_cancel():
                raise Exception("Task cancelled")

            raw_segments = relevant_parts.most_relevant_segments
            prepared_segments = checkpoint.get("segments") or checkpoint.get("candidates")
            final_plan = bool(checkpoint.get("segments"))
            segments_json: List[Dict[str, Any]] = list(prepared_segments or [])
            transcript_data = load_cached_transcript_data(video_path)
            for segment in ([] if prepared_segments else raw_segments):
                if isinstance(segment, dict):
                    virality = segment.get("virality") or {}
                    if hasattr(virality, "model_dump"):
                        virality = virality.model_dump()
                    segment_payload = {
                        "start_time": segment.get("start_time"),
                        "end_time": segment.get("end_time"),
                        "text": segment.get("text", ""),
                        "relevance_score": segment.get("relevance_score", 0.0),
                        "reasoning": segment.get("reasoning", ""),
                        "virality_score": virality.get("total_score", 0),
                        "hook_score": virality.get("hook_score", 0),
                        "engagement_score": virality.get("engagement_score", 0),
                        "value_score": virality.get("value_score", 0),
                        "shareability_score": virality.get("shareability_score", 0),
                        "hook_type": virality.get("hook_type"),
                        "hook_title": segment.get("hook_title"),
                        "hook_start_time": segment.get("hook_start_time"),
                        "hook_end_time": segment.get("hook_end_time"),
                    }
                    if segment.get("opening_reviewed"):
                        segment_payload["opening_reviewed"] = True
                    if segment.get("brief_verdict"):
                        segment_payload["brief_verdict"] = segment["brief_verdict"]
                else:
                    virality = segment.virality.model_dump() if segment.virality else {}
                    segment_payload = {
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
                        "hook_start_time": getattr(segment, "hook_start_time", None),
                        "hook_end_time": getattr(segment, "hook_end_time", None),
                    }
                    if getattr(segment, "brief_verdict", None):
                        segment_payload["brief_verdict"] = segment.brief_verdict

                segment_payload["text"] = VideoService._ground_segment_text(
                    segment_payload,
                    transcript_data,
                )
                segments_json.append(segment_payload)

            if manual_selection:
                # The user's ranges replace whatever the (skipped)
                # analysis produced. They are a final plan: no opening
                # refinement, reserve trimming or static-frame dropping
                # below may second-guess an explicit user cut.
                segments_json = build_manual_segments(
                    manual_ranges or [], duration_seconds=file_duration
                )
                for payload in segments_json:
                    payload["text"] = VideoService._ground_segment_text(
                        payload, transcript_data
                    )
                final_plan = True
                if not segments_json:
                    raise ValueError(
                        "None of the supplied ranges fall inside the "
                        "source video."
                    )

            try:
                if not prepared_segments and not manual_selection:
                    segments_json = VideoService._refine_segment_openings(
                        segments_json, transcript_data, clip_length
                    )
            except Exception:
                # A heuristic must never fail the task; keep the AI's openings.
                logger.exception("Opening review failed; keeping the selected openings")

            clip_limit = max_final_clips_for_duration(
                file_duration,
                runtime_config.max_clips,
                target_clip_count=target_clip_count,
            )
            # An explicit target is the user's own choice for this task, so
            # the fast-mode cap (a default-posture guard) must not silently
            # shrink it; Auto tasks keep the cap exactly as before.
            if processing_mode == "fast" and target_clip_count is None:
                clip_limit = min(clip_limit, runtime_config.fast_mode_max_clips)
            try:
                if not prepared_segments:
                    segments_json = VideoService._apply_retention_analysis(
                        segments_json, transcript_data, keep_at_least=clip_limit
                    )
            except Exception:
                # Structure analysis must never fail the task.
                logger.exception(
                    "Retention analysis failed; keeping the selected segments"
                )
            # Retain a bounded reserve until picture checks finish, so a rejected
            # slate can be replaced by the next already-ranked candidate. The
            # historic 24-segment reserve cap predates explicit targets and
            # would silently break a 25/30-clip request, so it applies to
            # Auto tasks only.
            if not prepared_segments and not manual_selection:
                reserve_limit = max(1, clip_limit) * 2
                if target_clip_count is None:
                    reserve_limit = min(24, reserve_limit)
                segments_json = segments_json[:reserve_limit]

            if not segments_json and content_brief:
                # Brief active: the fallback window and visual-moment
                # branches are DISABLED. Both bypass brief selection by
                # construction, and firing either would violate the
                # no-padding rule — empty stays empty and the task ends
                # with the explicit no-compliant-moments outcome.
                logger.info(
                    "Brief active and no segments selected; fallback and "
                    "visual-moment branches skipped (no padding under a brief)"
                )
            if not segments_json and not content_brief:
                # Sources with no usable transcript (music-only, anime,
                # montage) get visual moment selection — scene cuts +
                # audio energy peaks on the beat grid — instead of one
                # blind window. The same floor is the degradation path
                # when the AI analysis itself failed: a task whose
                # every provider is down still produces sensible
                # clips instead of dying at the analysis step. The
                # transcript path is untouched: when the AI serves,
                # neither condition holds and this branch's selection
                # never runs.
                visual_segments: List[Dict[str, Any]] = []
                transcript_unusable = not (transcript_data or {}).get(
                    "words"
                ) and not (transcript or "").strip()
                if transcript_unusable or analysis_failed:
                    selection_context = (
                        "no usable transcript"
                        if transcript_unusable
                        else "AI analysis unavailable"
                    )
                    try:
                        from ..auto_director.beat_grid import analyse_audio_beats
                        from ..auto_director.visual_moments import (
                            select_visual_moments,
                        )

                        target_seconds = (
                            runtime_config.clip_duration
                            if clip_length == "auto"
                            else clip_duration(clip_length).ideal_minimum
                        )
                        source_grid = await run_in_thread(
                            analyse_audio_beats, video_path
                        )
                        visual_segments = await run_in_thread(
                            select_visual_moments,
                            video_path,
                            file_duration,
                            target_count=max(1, clip_limit),
                            clip_seconds=float(target_seconds),
                            beat_grid=source_grid,
                            reason_context=selection_context,
                            frame_evidence=os.getenv(
                                "FRAME_EVIDENCE_SELECTION", "1"
                            ).lower() not in ("0", "false", "off"),
                        )
                    except Exception:
                        logger.warning(
                            "Visual moment selection failed; using fallback window",
                            exc_info=True,
                        )
                        visual_segments = []
                if visual_segments:
                    logger.info(
                        "Visual moment selection picked %d moments (%s)",
                        len(visual_segments),
                        selection_context,
                    )
                    segments_json = visual_segments
                else:
                    logger.warning(
                        "AI analysis selected no segments; using fallback clip window"
                    )
                    # S-1: stamp the fallback only when the outage is
                    # the operative context. The no-usable-transcript
                    # path wins by design when both hold (§8.3), and
                    # an AI-success-but-zero-segments fallback is not
                    # an outage, so neither gets the marker.
                    segments_json = [
                        VideoService._build_fallback_segment(
                            file_duration,
                            transcript,
                            (runtime_config.clip_duration if clip_length == "auto"
                             else clip_duration(clip_length).ideal_minimum),
                            analysis_failed=analysis_failed
                            and not transcript_unusable,
                        )
                    ]

                # O-R3 parity: a floor slate is not a lesser export.
                # Ground each segment's text in the transcript — the
                # same grounding AI segments receive above — and derive
                # its hook title from its own words, so a floor render
                # carries the captions and hook title the standard path
                # renders (captions burn from these same transcript
                # words at render time). A transcript-less source has
                # no words to ground: its text stays empty and its
                # title stays None, honestly.
                for segment in segments_json:
                    segment["text"] = VideoService._ground_segment_text(
                        segment, transcript_data
                    )
                    if not segment.get("hook_title"):
                        segment["hook_title"] = (
                            VideoService._floor_hook_title(segment["text"])
                        )

            if not final_plan:
                # The target count rides with the slate: if a parked
                # Find-Moments task is later auto-picked by the rescue
                # sweep, it continues with exactly this many top moments.
                await save_checkpoint(
                    {
                        "candidates": segments_json,
                        "selection_target_count": max(1, clip_limit),
                    }
                )

            if mode_plan.review == REVIEW_MOMENT_PICK and not final_plan:
                # Find-Moments workflows stop here. The candidate slate
                # is checkpointed above; rendering waits for the user's
                # pick, which the select-moments endpoint writes back
                # as the checkpoint's final plan before re-enqueueing.
                logger.info(
                    "Creation mode %s: parking %d candidate moments "
                    "for user selection",
                    mode_plan.mode,
                    len(segments_json),
                )
                return {
                    "awaiting_selection": True,
                    "candidates": segments_json,
                    "segments": [],
                    "segments_to_render": [],
                    "stage_timings": stage_timings,
                    "video_path": str(video_path),
                    "clips": [],
                    "summary": relevant_parts.summary
                    if relevant_parts
                    else None,
                    "key_topics": relevant_parts.key_topics
                    if relevant_parts
                    else None,
                    "transcript": transcript,
                    "analysis_json": None,
                }

            if audio_first:
                audio_path = video_path
                if progress_callback:
                    await progress_callback(
                        65, "Downloading selected segments...", "processing"
                    )
                section_error = None
                try:
                    if source_type in {"twitch", "kick"}:
                        video_path = await run_in_thread(
                            download_stream_sections, url, segments_json, file_duration, task_id,
                            max_duration=duration_limit, operation=PROCESSING_OPERATION,
                        )
                        # Shift word timings onto the stitched sections' timeline.
                        await run_in_thread(
                            copy_transcript_sidecar, audio_path, video_path,
                            await run_in_thread(load_time_map, video_path),
                        )
                    else:
                        video_path = await run_download_in_thread(
                            materialize_clip_source, url, video_id, segments_json,
                            task_id=task_id, budget=job_budget, duration=file_duration,
                            audio_path=audio_path, quality=download_quality,
                            progress_callback=progress_callback, progress=65,
                        )
                except DownloadBudgetExceeded as exc:
                    raise Exception(str(exc)) from exc
                except Exception as exc:
                    if source_type in {"twitch", "kick"} and isinstance(exc, StreamSourceError):
                        raise
                    section_error = exc

                if section_error is not None:
                    if progress_callback:
                        await progress_callback(65, DownloadRecovery.FULL_VIDEO.value, "processing")
                    logger.warning(
                        "Section download failed, falling back to a full download: %s",
                        section_error,
                    )
                    if source_type in {"twitch", "kick"}:
                        video_path = await run_in_thread(
                            download_stream_video, url, file_duration, task_id,
                            max_duration=duration_limit, operation=PROCESSING_OPERATION,
                        )
                    else:
                        video_path = await VideoService.download_video(
                            url, task_id=task_id, quality=download_quality, budget=job_budget,
                            skip_proxy=not isinstance(section_error, PartialDownloadUnavailable),
                            progress_callback=progress_callback, download_progress=65,
                        )
                        video_path = preserve_geo_restriction(video_path, section_error)
                    if video_path and audio_path:
                        await run_in_thread(copy_transcript_sidecar, audio_path, video_path)
                        await run_in_thread(persist_cache_sidecar, video_path)
                if not video_path:
                    raise Exception("Failed to download selected segments")
                # Transcription used the audio download. The picture is only
                # available now, so the 1080p/60 cap has to run again or a
                # section download stays at the source resolution.
                if should_cancel and await should_cancel():
                    raise Exception("Task cancelled")
                video_path = await VideoService.prepare_source(video_path)

            # The picture is only available now when analysis ran on audio.
            if segments_json and not final_plan:
                try:
                    slate_filtered = await run_in_thread(
                        VideoService._drop_slate_segments, video_path, segments_json
                    )
                    # A thread helper that cannot run the filter (or a
                    # stubbed one in tests) must never erase the slate:
                    # only a real list result replaces the segments.
                    if slate_filtered is not None:
                        segments_json = slate_filtered
                except Exception:
                    logger.exception("Slate exclusion failed; keeping every segment")
            if segments_json and not final_plan:
                try:
                    segments_json = await run_in_thread(
                        VideoService._drop_static_segments, video_path, segments_json
                    )
                except Exception:
                    logger.exception("Visual suitability check failed; keeping every segment")

            # Content Requirements S2 (selection gate) — see
            # _apply_brief_gate. Brief drops are a correct outcome, not
            # a render failure.
            brief_report: Optional[Dict[str, Any]] = None
            if content_brief:
                segments_json, brief_report = VideoService._apply_brief_gate(
                    segments_json,
                    content_brief,
                    transcript or "",
                    ai_brief_report=getattr(relevant_parts, "brief_report", None),
                )

            if not final_plan:
                segments_json = segments_json[:max(1, clip_limit)]
            if not segments_json:
                if content_brief:
                    # Honest no-match: return an empty slate + the search
                    # report; TaskService turns this into the explicit
                    # no_compliant_moments terminal state. No fallback,
                    # no padding, zero clips.
                    await save_checkpoint({"segments": []})
                    return {
                        "segments": [],
                        "segments_to_render": [],
                        "stage_timings": stage_timings,
                        "video_path": str(video_path),
                        "clips": [],
                        "summary": relevant_parts.summary if relevant_parts else None,
                        "key_topics": relevant_parts.key_topics if relevant_parts else None,
                        "transcript": transcript,
                        "analysis_json": None,
                        "brief_report": brief_report,
                    }
                raise ValueError("No usable footage remains after excluding interruption notices.")

            # Niche classification for the render path: stamp each
            # segment with the classifier's niche so task_service /
            # create_single_clip resolve the right editing profile
            # instead of silently landing on the fallback profile. Only
            # a confident, non-fallback result is stamped; a fallback
            # leaves the segments unstamped (today's default-profile
            # behaviour) but is still returned on the result as
            # ``classification`` so the task layer can surface that the
            # niche was NOT really classified. A pinned project override
            # wins over the classifier everywhere downstream, so
            # classification is skipped entirely for pinned tasks.
            classification_result: Optional[Dict[str, Any]] = (
                checkpoint.get("classification")
                if isinstance(checkpoint.get("classification"), dict)
                else None
            )
            if not final_plan and not project_niche_override:
                if classification_result is None:
                    classification_result = (
                        await VideoService._classify_source_niche(
                            video_path=video_path,
                            transcript=transcript,
                            segments=segments_json,
                            title=source_title,
                            description=source_description,
                            duration=file_duration,
                        )
                    )
                    if classification_result is not None:
                        await save_checkpoint(
                            {"classification": classification_result}
                        )
                if classification_result and not classification_result.get(
                    "is_fallback"
                ):
                    classified_niche = classification_result.get("niche")
                    classified_confidence = classification_result.get(
                        "confidence"
                    )
                    if classified_niche:
                        for segment in segments_json:
                            segment["classifier_niche"] = classified_niche
                            segment["classifier_confidence"] = (
                                classified_confidence
                            )
                        logger.info(
                            "Classified source niche as %s (confidence %s, "
                            "evidence %s)",
                            classified_niche,
                            classified_confidence,
                            classification_result.get("evidence_status"),
                        )
                elif classification_result:
                    logger.warning(
                        "Source niche classification fell back; clips will "
                        "use the default profile (evidence %s)",
                        classification_result.get("evidence_status"),
                    )

            await save_checkpoint({"segments": segments_json})

            logger.info(f"Pipeline stage timings: {stage_timings}")
            return {
                "segments": segments_json,
                "segments_to_render": segments_json,
                "classification": classification_result,
                "stage_timings": stage_timings,
                "video_path": str(video_path),
                "clips": [],
                "summary": relevant_parts.summary if relevant_parts else None,
                "key_topics": relevant_parts.key_topics if relevant_parts else None,
                "transcript": transcript,
                "brief_report": brief_report,
                "analysis_json": json.dumps(
                    {
                        "summary": relevant_parts.summary if relevant_parts else None,
                        "key_topics": relevant_parts.key_topics
                        if relevant_parts
                        else [],
                        # Provenance: who selected these segments (F-1).
                        "selection_strategy": mode_plan.selection,
                        "most_relevant_segments": segments_json,
                    }
                ),
            }

        except (Exception, asyncio.CancelledError) as e:
            if source_type == "youtube" and video_path is not None:
                video_id = get_youtube_video_id(url)
                if video_id:
                    try:
                        cleanup_downloaded_files(video_id, source_path=video_path)
                    except Exception:
                        logger.exception("Could not clean up failed YouTube download")
            logger.error(f"Error in video processing pipeline: {e}")
            raise
