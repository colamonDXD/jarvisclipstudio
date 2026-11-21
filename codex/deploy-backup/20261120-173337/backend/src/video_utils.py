"""Video utils helpers for the video pipeline."""
import os

from .clip_source_map import save_clip_broll, save_clip_caption_settings
from .media.common import (
    Any,
    Dict,
    List,
    Optional,
    Path,
    Tuple,
    VALID_OUTPUT_FORMATS,
    find_font_path,
    get_template,
    logger,
    normalize_source_ranges,
    record_pass_timing,
    save_clip_source_ranges,
    shutil,
    source_range_bounds,
    subprocess,
    tempfile,
    uuid,
)
from .media.ffmpeg import (
    audio_stream_map_args,
    ffprobe_duration,
    ffprobe_has_audio,
    ffprobe_video_size,
    render_source_ranges_ffmpeg,
    round_to_even,
    run_ffmpeg_command,
    select_audio_stream_index,
)
from .media.source_limits import (
    fit_within_1080p,
    probe_video_geometry,
    source_within_output_limits,
)
from .media.transcription import (
    get_video_transcript,
)
from .media.timeline import (
    add_hook_teaser_to_keep_ranges,
    apply_segment_hook_teaser,
    build_clip_keep_ranges,
    build_hook_teaser_range,
    build_keep_ranges_from_source_ranges,
    clamp_keep_ranges_to_max_duration,
    extend_keep_ranges_to_sentence_boundary,
    parse_timestamp_to_seconds,
    keep_ranges_transcript_text,
    snap_clip_start_to_sentence,
    snap_keep_ranges_to_sentence_start,
)
from .media.captions import (
    ass_fonts_dir,
    build_assemblyai_ass_subtitles,
)
from .media.reframing import (
    render_reframed_clip_ffmpeg,
)
from .media.source_captions import (
    band_through_full_height_crop,
    detect_burned_caption_band,
)
from .media.speaker_framing import (
    build_clip_speaker_turns,
)
from .media.ffmpeg import (
    build_audio_output_args,
    build_final_video_encode_args,
    burn_ass_subtitles_ffmpeg,
    clamp_even,
    crossfade_fade_for_ranges,
    ffmpeg_escape_filter_path,
    ffmpeg_escape_filter_value,
    ffprobe_has_audio,
    render_encoder_diagnostics,
    render_ranges_crossfade_ffmpeg,
    rendered_clip_duration,
    subtitles_filter_fragment,
)
from .media.transcription import (
    _assemblyai_speech_models_value,
    _format_words_for_analysis,
    _get_transcript_with_assemblyai,
    _get_transcript_with_faster_whisper,
    _get_transcript_with_whisper,
    _get_transcript_with_youtube_captions,
    _get_whisper_model,
    _join_transcript_tokens,
    _prepare_audio_for_transcription,
    _serialize_transcript_word,
    _submit_and_wait_for_assemblyai_transcript,
    _whisper_result_to_transcript_data,
    cache_transcript_data,
    format_ms_to_timestamp,
    format_transcript_for_analysis,
    load_cached_transcript_data,
    transcribe_with_faster_whisper,
    transcribe_with_whisper,
    transcribe_with_youtube_captions,
)
from .media.timeline import (
    _build_cleanup_phrases,
    _merge_intervals,
    _normalize_cleanup_token,
    build_clip_signal_summary,
    detect_audio_peak_times,
    get_absolute_words_in_range,
    get_transcript_text_in_range,
    get_words_for_keep_ranges,
    get_words_in_range,
    parse_transcript_lines,
    seconds_to_mmss,
    word_ends_sentence,
)
from .media.captions import (
    _balance_title_lines,
    ass_font_name,
    ass_timestamp,
    build_hook_title_ass,
    emoji_rendering_supported,
    escape_ass_text,
    get_safe_vertical_position,
    get_scaled_font_size,
    get_subtitle_max_width,
    hex_to_ass_color,
)
from .media.reframing import (
    _detect_dominant_face,
    _median_filter,
    _open_face_detectors,
    _scene_cuts_from_diffs,
    analyze_vertical_clip,
    build_crop_trajectory,
    build_layout_plan,
    build_pan_expression,
    build_smooth_pan_expression,
    build_speaker_timeline_from_motion,
    build_vertical_compositor_filter,
    build_vertical_filter_plan,
    cluster_two_face_regions,
    compute_vertical_crop_dims,
    count_scene_cuts,
    detect_faces_in_clip,
    detect_optimal_crop_region,
    detect_speaker_reframe_plan,
    filter_face_outliers,
    kenburns_zoom_fragment,
    parse_motion_metadata,
    smooth_values,
    trajectory_has_movement,
)
from .media.common import (
    ANALYSIS_LONG_UTTERANCE_MAX_DURATION_MS,
    ANALYSIS_LONG_UTTERANCE_MAX_WORDS,
    ANALYSIS_SEGMENT_MAX_DURATION_MS,
    ANALYSIS_SEGMENT_MAX_WORDS,
    ANALYSIS_SEGMENT_MIN_WORDS,
    ANALYSIS_UTTERANCE_SPLIT_THRESHOLD_MS,
    ANALYSIS_UTTERANCE_SPLIT_THRESHOLD_WORDS,
    AUDIO_BITRATE,
    CAPTION_TEMPLATES,
    CLIP_END_PADDING_SECONDS,
    CLIP_END_SENTENCE_EXTENSION_SECONDS,
    DEFAULT_FILTERED_WORDS,
    EMOJI_FONT_NAME,
    FACE_PRESENCE_MIN_AREA,
    FACE_PRESENCE_RATE,
    FACE_RATE_WINDOW,
    FINAL_VIDEO_CRF,
    FINAL_VIDEO_PRESET,
    FONTS_DIR,
    HOOK_TITLE_MIN_SECONDS,
    HOOK_TITLE_SECONDS,
    HOOK_TITLE_TOP_MARGIN_FRAC,
    INTERMEDIATE_CRF,
    KENBURNS_MIN_SECONDS,
    KENBURNS_SUPERSAMPLE_H,
    KENBURNS_SUPERSAMPLE_W,
    KENBURNS_ZOOM_DELTA,
    LAYOUT_SNAP_WINDOW,
    LOUDNORM_FILTER,
    MIN_LAYOUT_SECONDS,
    OUTPUT_FPS,
    POWER_WORDS,
    SENTENCE_END_RE,
    TRANSCRIPT_CACHE_SCHEMA_VERSION,
    ThreadPoolExecutor,
    _EMOJI_SUPPORT_CACHE,
    _WHISPER_AVAILABLE,
    _WHISPER_MODEL_CACHE,
    _whisper,
    aai,
    annotate_caption_words,
    clip_cleanup_enabled,
    cv2,
    get_config,
    get_font_family_name,
    httpx,
    json,
    logging,
    normalize_token,
    np,
    re,
    srt,
    time,
    timedelta,
)


class VideoProcessor:
    """Handles video processing operations with optimized settings."""

    def __init__(
        self,
        font_family: str = "THEBOLDFONT",
        font_size: int = 24,
        font_color: str = "#FFFFFF",
    ):
        self.font_family = font_family
        self.font_size = font_size
        self.font_color = font_color
        resolved_font = find_font_path(font_family, allow_all_user_fonts=True)
        if not resolved_font:
            resolved_font = find_font_path("TikTokSans-Regular")
        if not resolved_font:
            resolved_font = find_font_path("THEBOLDFONT")
        self.font_path = str(resolved_font) if resolved_font else ""

    def get_optimal_encoding_settings(
        self, target_quality: str = "high"
    ) -> Dict[str, Any]:
        """Get optimal encoding settings for different quality levels."""
        settings = {
            "high": {
                "codec": "libx264",
                "audio_codec": "aac",
                "audio_bitrate": "256k",
                "preset": "slow",
                "ffmpeg_params": [
                    "-crf",
                    "18",
                    "-pix_fmt",
                    "yuv420p",
                    "-profile:v",
                    "high",
                    "-movflags",
                    "+faststart",
                    "-sws_flags",
                    "lanczos",
                ],
            },
            "medium": {
                "codec": "libx264",
                "audio_codec": "aac",
                "bitrate": "4000k",
                "audio_bitrate": "192k",
                "preset": "fast",
                "ffmpeg_params": ["-crf", "23", "-pix_fmt", "yuv420p"],
            },
        }
        return settings.get(target_quality, settings["high"])


def brand_template_override(
    edl: Optional[Dict[str, Any]], caption_template: str
) -> Optional[Dict[str, Any]]:
    """Merge an EDL's Brand Kit colour block over its named caption template.

    Returns None when the EDL carries no brand block, so the caption
    builder uses the plain named template exactly as before. Only the
    fields the kit set are overridden; the rest of the template
    (animation, pill, glow) is untouched.
    """
    block = (edl or {}).get("brand_template_override")
    if not isinstance(block, dict) or not block:
        return None
    merged = dict(get_template(caption_template))
    merged.update(block)
    return merged


def _layout_caption_override(
    override: Optional[Dict[str, Any]],
    caption_template: str,
    layout_template: Optional[str],
) -> Optional[Dict[str, Any]]:
    """A layout template's seam captions keep their own words-per-line:
    the profile's authored grouping must not stretch "[BEEP]" into four
    words across the seam."""
    if not layout_template or override is None:
        return override
    override = dict(override)
    override["max_words_per_line"] = get_template(caption_template).get(
        "max_words_per_line", override.get("max_words_per_line")
    )
    return override


def caption_template_override(
    edl: Optional[Dict[str, Any]], caption_template: str
) -> Optional[Dict[str, Any]]:
    """The full caption-template override stack for an EDL, or None.

    One merge, one site: the stock template at the bottom, the
    Reference Style caption look over it, the Brand Kit over that -
    precedence task choice (the builder's explicit font parameters,
    which outrank the whole dict) > Brand Kit > reference look > stock
    preset. The EDL captions block's own ``words_per_line`` (the
    profile's authored grouping, unread until the D1 fix) fills the
    merged template's ``max_words_per_line`` unless the look or the
    kit set one explicitly: a measured or deliberate value beats the
    profile default, the profile default beats the stock preset.
    Returns None when the EDL carries none of the three blocks, so the
    caption builder uses the plain named template exactly as before.
    """
    look = (edl or {}).get("reference_look_override")
    brand = (edl or {}).get("brand_template_override")
    look = look if isinstance(look, dict) and look else None
    brand = brand if isinstance(brand, dict) and brand else None
    captions_block = (edl or {}).get("captions")
    words_per_line = (
        captions_block.get("words_per_line")
        if isinstance(captions_block, dict)
        else None
    )
    if isinstance(words_per_line, bool):
        words_per_line = None
    elif words_per_line is not None:
        # An unusable value is no value: it must neither manufacture
        # an override dict nor touch the grouping.
        try:
            words_per_line = int(words_per_line)
        except (TypeError, ValueError):
            words_per_line = None
        if words_per_line is not None and words_per_line < 1:
            words_per_line = None
    if look is None and brand is None and words_per_line is None:
        return None
    merged = dict(get_template(caption_template))
    if look is not None:
        merged.update(look)
    if brand is not None:
        merged.update(brand)
    if words_per_line is not None:
        look_or_brand = {**(look or {}), **(brand or {})}
        if "max_words_per_line" not in look_or_brand:
            merged["max_words_per_line"] = words_per_line
    return merged


def edl_caption_position_request(
    edl: Optional[Dict[str, Any]],
) -> Tuple[Optional[float], bool]:
    """The caption height an EDL's captions block asks for, if any.

    Returns ``(position_y, explicit)``. The block ``build_edl`` stamps
    from the profile (and the re-render seam stamps from the Caption
    Studio / AI editor) carried ``position`` / ``position_y`` for a
    long time without any render reading them (editor audit D1/D2);
    this is the reader. A numeric ``position_y`` inside the validated
    0.1-0.85 band is a deliberate per-clip choice and reports
    ``explicit=True``; a named ``position`` resolves through the
    caption overlay path's long-standing presets
    (``POSITION_Y_BY_NAME`` — "center" is the EDL spelling of the
    middle preset) and reports ``explicit=False``: a profile default,
    not a per-clip decision. Between the two, a Reference Style
    look's measured ``position_y`` (F-A) refines the named band it
    was measured from and reports ``explicit=False``: a style-level
    measurement, not a per-clip decision, so a per-clip explicit
    value and the face-aware layout plan still outrank it, exactly
    as they outrank the named band. Anything unusable (no block,
    unknown name, out-of-band or non-numeric value) resolves to
    ``(None, False)``: a stale EDL must never break a render or move
    captions it did not mean to touch.
    """
    import math

    captions = (edl or {}).get("captions")
    if not isinstance(captions, dict):
        return None, False
    from .auto_director.clip_edits import (
        POSITION_Y_BY_NAME,
        POSITION_Y_MAX,
        POSITION_Y_MIN,
    )

    raw_y = captions.get("position_y")
    if isinstance(raw_y, (int, float)) and not isinstance(raw_y, bool):
        value = float(raw_y)
        if math.isfinite(value) and POSITION_Y_MIN <= value <= POSITION_Y_MAX:
            return value, True
    # F-A: the Reference Style look's measured caption height. The
    # look is stamped on the EDL as reference_look_override and its
    # position_y is merged into the caption template — but the
    # template's position_y is only read when no position_y_override
    # is passed, and the override is never None while the EDL carries
    # a named position, so the measured value never reached a render.
    # It enters the request here, at the profile/reference tier: it
    # refines the named band it was measured from (the band is a
    # coarsening of this same measurement), so it beats the named
    # position — but it is a style-level measurement, not a deliberate
    # per-clip choice, so it reports explicit=False and a per-clip
    # explicit position_y above, or the face-aware layout plan below,
    # still outrank it, exactly as they outrank the named band. The
    # same fail-safe band as the captions block applies: an unusable
    # look value falls through to the named position, never raises.
    # Plain is untouched: plainify_edl strips reference_look_override
    # before the render, so a Plain EDL never reaches this branch.
    look = (edl or {}).get("reference_look_override")
    if isinstance(look, dict):
        look_y = look.get("position_y")
        if isinstance(look_y, (int, float)) and not isinstance(look_y, bool):
            value = float(look_y)
            if math.isfinite(value) and POSITION_Y_MIN <= value <= POSITION_Y_MAX:
                return value, False
    name = captions.get("position")
    if isinstance(name, str):
        resolved = POSITION_Y_BY_NAME.get(name.strip().lower())
        if resolved is not None:
            return resolved, False
    return None, False


def _stamp_render_timings(
    layout_report: Optional[Dict[str, Any]], render_timings: Dict[str, float]
) -> None:
    """Publish a clip's per-pass timings on its layout report (F-P3).

    The layout report is the render's existing report-back seam (the
    caller reads broll_applied / caption facts from it); the timings
    ride it as ``render_timings``. Observation only: never raises.
    """
    if layout_report is None or not render_timings:
        return
    try:
        published = dict(render_timings)
        # The encoder stamp rides the timings: which encoder rendered
        # this clip, and the NVENC probe's verdict when the GPU was
        # unavailable — visible in the task's persisted stage timings
        # instead of only in the worker log.
        published.update(render_encoder_diagnostics())
        layout_report["render_timings"] = published
    except Exception:
        pass


def min_clip_seconds() -> float:
    """CLIP_MIN_SECONDS (default 30): no rendered clip is shorter (owner:
    17 s on 2026-11-18 after a 3 s clip shipped, then "minimum clip should
    be 30 sec strict rule" on 2026-11-19)."""
    try:
        return max(0.0, float(os.getenv("CLIP_MIN_SECONDS", "30")))
    except ValueError:
        return 30.0


def enforce_min_clip_duration(
    video_path: Path, ranges: List[Tuple[float, float]], minimum: Optional[float] = None
) -> List[Tuple[float, float]]:
    """Lengthen a clip that would render shorter than the minimum.

    The last range is extended forward (the moment keeps playing); when the
    source ends first, the last range grows backward into its own lead-in,
    never past the end of the range that precedes it in the source. The
    first range's start NEVER moves: a clip may open with a transplanted
    segment (a cold-open hook moved to the front), and starting that range
    earlier would put the source's original opening back in front of it.
    When the source is exhausted the clip stays short — the floor yields
    to the footage that exists.
    """
    minimum = min_clip_seconds() if minimum is None else minimum
    ranges = [(float(a), float(b)) for a, b in ranges]
    total = sum(b - a for a, b in ranges)
    if not ranges or minimum <= 0 or total >= minimum:
        return ranges
    try:
        source_len = float(ffprobe_duration(video_path) or 0.0)
    except Exception:
        source_len = 0.0
    if source_len <= 0:
        return ranges
    deficit = minimum - total
    a, b = ranges[-1]
    new_b = min(source_len, b + deficit)
    ranges[-1] = (a, new_b)
    deficit -= new_b - b
    if deficit > 0.01:
        # Grow the last range backward, but not into footage another range
        # already uses: the floor is the latest source end at or before
        # the last range's start (0.0 for a single-range clip).
        floor_start = 0.0
        for other_a, other_b in ranges[:-1]:
            if other_b <= a:
                floor_start = max(floor_start, other_b)
        new_a = max(floor_start, a - deficit)
        ranges[-1] = (new_a, new_b)
        deficit -= a - new_a
    logger.info("Clip lengthened to the %.0fs minimum: %.1fs -> %.1fs", minimum, total,
                sum(y - x for x, y in ranges))
    return ranges


def create_optimized_clip(
    video_path: Path,
    start_time: float,
    end_time: float,
    output_path: Path,
    add_subtitles: bool = True,
    font_family: Optional[str] = None,
    font_size: Optional[int] = None,
    font_color: Optional[str] = None,
    caption_template: str = "default",
    output_format: str = "vertical",
    keep_ranges: Optional[List[Tuple[float, float]]] = None,
    hook_title: Optional[str] = None,
    extend_to_sentence: bool = True,
    layout_report: Optional[Dict[str, Any]] = None,
    broll_overlays: Optional[List[Dict[str, Any]]] = None,
    edl: Optional[Dict[str, Any]] = None,
    profile_name: Optional[str] = None,
    max_caption_seconds: Optional[float] = None,
    allowed_ranges: Optional[List[Tuple[float, float]]] = None,
    excluded_ranges: Optional[List[Tuple[float, float]]] = None,
    vocals_stem: Optional[Path] = None,
    task_text_bands: Optional[Dict[str, Any]] = None,
    framing_mode: Optional[str] = None,
    layout_template: Optional[str] = None,
    layout_credit: Optional[str] = None,
) -> bool:
    """Create clip with optional subtitles. output_format: 'vertical' (9:16) or 'original' (keep source size).

    ``layout_template`` (``media.gaming_templates``: ``facecam_red`` /
    ``facecam_gold``) renders a clip with a facecam as "face on top, game
    below" with that template's divider, seam captions and optional
    ``layout_credit`` line; the template's caption look replaces the task
    font and the profile's words-per-line. None keeps the automatic layout.

    ``broll_overlays`` are stock cutaways (path, start, duration on the clip
    timeline) composited under the captions. If that render fails the clip is
    rendered without them; ``layout_report["broll_applied"]`` says which —
    and it also counts the EDL's own resolved ``broll`` entries, which the
    EDL renderer composites in-graph on the EDL path.

    F-P3: the passes that run are timed with real wall-clock and the
    per-clip dict is stamped into ``layout_report["render_timings"]``
    (the EDL renderer adds its own sub-pass entries into the same dict;
    this function's ``total_seconds``/``encode_seconds`` are the outer
    values and win). Observation only — timing never changes the render.
    """
    render_timings: Dict[str, float] = {}
    clip_started = time.perf_counter()
    try:
        if keep_ranges:
            effective_keep_ranges = normalize_source_ranges(keep_ranges)
        else:
            effective_keep_ranges = [
                (max(start_time, start), min(end_time, end))
                for start, end in [(start_time, end_time)]
                if min(end_time, end) - max(start_time, start) > 0.05
            ]
        if extend_to_sentence:
            if not keep_ranges:
                # Only raw bounds are snapped; supplied ranges may carry
                # cleanup cuts or manual trims that must not be undone.
                effective_keep_ranges = snap_keep_ranges_to_sentence_start(
                    video_path, effective_keep_ranges,
                )
            effective_keep_ranges = extend_keep_ranges_to_sentence_boundary(
                video_path, effective_keep_ranges,
            )
            effective_keep_ranges = clamp_keep_ranges_to_max_duration(
                video_path, effective_keep_ranges,
            )
        if allowed_ranges or excluded_ranges:
            # R2 last-line audit (Content Requirements): intersect the
            # FINAL ranges, after this function's own sentence-extension
            # block. R1 upstream should already have caught everything —
            # a change here means a new mutator appeared, so log ERROR.
            from .auto_director.content_requirements import (
                intersect_ranges,
                subtract_ranges,
            )

            audited = effective_keep_ranges
            if allowed_ranges:
                audited = intersect_ranges(audited, list(allowed_ranges))
            if excluded_ranges:
                audited = subtract_ranges(audited, list(excluded_ranges))
            if audited != effective_keep_ranges:
                logger.error(
                    "create_optimized_clip range audit trimmed out-of-range "
                    "footage: %s -> %s (R1 should have caught this)",
                    effective_keep_ranges,
                    audited,
                )
                effective_keep_ranges = audited
        effective_keep_ranges = enforce_min_clip_duration(video_path, effective_keep_ranges)
        duration = sum(end - start for start, end in effective_keep_ranges)
        if duration <= 0:
            logger.error(f"Invalid clip duration: {duration:.1f}s")
            return False

        keep_original = output_format == "original"
        # The seam caption style belongs to the face-on-top stack: when the
        # clip has an EDL whose plan did not choose the stack (no facecam),
        # the task's own caption style stays.
        reframe_section = (edl or {}).get("reframe") or {}
        stack_chosen = not edl or reframe_section.get("delegate") == "plain"
        if layout_template and stack_chosen:
            from .media.gaming_templates import caption_template_for

            seam_template = caption_template_for(layout_template)
            if seam_template:
                caption_template = seam_template
                font_family = font_size = font_color = None
        logger.info(
            f"Creating clip: {start_time:.1f}s - {end_time:.1f}s ({duration:.1f}s) "
            f"subtitles={add_subtitles} template '{caption_template}' format={'original' if keep_original else 'vertical'}"
        )

        # Fast path: no subtitles + original = ffmpeg stream copy (no re-encoding).
        # Oversized or high-frame-rate sources fall through so the render caps them.
        # An EDL always falls through: a stream copy cannot apply its
        # transitions, zooms, music or SFX.
        if (
            not add_subtitles
            and not hook_title
            and not broll_overlays
            and not edl
            and keep_original
            and len(effective_keep_ranges) == 1
            and extend_to_sentence
            and source_within_output_limits(video_path)
        ):
            fast_path_start, fast_path_end = effective_keep_ranges[0]
            result = subprocess.run(
                [
                    "ffmpeg",
                    "-y",
                    "-ss", str(fast_path_start),
                    "-i", str(video_path),
                    *audio_stream_map_args(select_audio_stream_index(video_path)),
                    "-t", str(fast_path_end - fast_path_start),
                    "-c", "copy",
                    "-movflags", "+faststart",
                    str(output_path),
                ],
                capture_output=True,
                text=True,
                timeout=300,
            )
            if result.returncode != 0:
                logger.error(f"ffmpeg stream copy failed: {result.stderr}")
                return False
            logger.info(f"Successfully created clip (stream copy): {output_path}")
            record_pass_timing(render_timings, "total_seconds", clip_started)
            _stamp_render_timings(layout_report, render_timings)
            return True

        with tempfile.TemporaryDirectory(prefix="supoclip_render_") as temp_dir:
            temp_root = Path(temp_dir)
            source_clip_path = temp_root / "source.mp4"
            final_clip_path = temp_root / "final.mp4"
            ass_path = temp_root / "captions.ass"

            reframe_format = (
                output_format if output_format in VALID_OUTPUT_FORMATS else "vertical"
            )

            # Direct-from-source EDL render: the EDL graph trims and joins
            # the keep ranges in-graph (mirroring the intermediate's own
            # crossfade stitch, so the timeline is unchanged), so for a
            # vertical EDL render the CRF-16 intermediate encode is
            # skipped entirely and the final encode reads the original
            # file. The EDL travels with a
            # copy carrying the ranges as ``source_ranges``; the caller's
            # EDL is never mutated. Two things the intermediate used to
            # feed are skipped in this mode (they sample the intermediate's
            # timeline): the vertical layout plan's caption position and
            # the burned-caption band probe — captions fall back to their
            # default placement. Every other input (speaker turns, the ASS
            # itself) was always derived from the original file. Restricted
            # to sources with an audio stream: the in-graph audio pre-stage
            # trims [0:a], which a silent source cannot satisfy.
            # A reframe plan may delegate the clip to the plain path
            # (delegate == "plain", e.g. a reaction-dominant clip whose
            # reactor inset the EDL graph cannot compose): the direct path
            # is skipped so the intermediate renders and the plain layout
            # stack below frames the clip instead.
            direct_edl = (
                bool(edl and edl.get("segments"))
                and (edl.get("reframe") or {}).get("delegate") != "plain"
                and reframe_format == "vertical"
                and ffprobe_has_audio(video_path)
            )
            render_edl = edl
            # Voice/music separation (opt-in tasks): the vocals stem the
            # render should hear instead of the source's raw mix. The
            # explicit argument wins; an EDL stamp (the pipeline's own
            # carrier) is the fallback. On the direct path the EDL
            # renderer consumes the stamp itself; here it only feeds the
            # pre-stage stitch, whose intermediate then carries the
            # stem's audio into every later stage. No stem (or a
            # vanished file) -> None -> the stitch is the historical one.
            stem_candidate = vocals_stem
            if stem_candidate is None and edl:
                raw_stem = edl.get("vocals_stem")
                if isinstance(raw_stem, str) and raw_stem.strip():
                    stem_candidate = Path(raw_stem)
            prestage_audio = (
                Path(stem_candidate)
                if stem_candidate is not None and Path(stem_candidate).is_file()
                else None
            )
            if direct_edl:
                render_edl = {
                    **edl,
                    "source_ranges": [
                        [float(start), float(end)]
                        for start, end in effective_keep_ranges
                    ],
                }
                source_clip_path = video_path
            else:
                prestage_started = time.perf_counter()
                # The audio_path kwarg is passed only when a stem is in
                # play, so the no-separation call keeps its exact
                # historical shape (the Stage-1 conditional-kwarg rule).
                prestage_ok = render_source_ranges_ffmpeg(
                    video_path,
                    effective_keep_ranges,
                    source_clip_path,
                    **(
                        {"audio_path": prestage_audio}
                        if prestage_audio is not None
                        else {}
                    ),
                )
                record_pass_timing(
                    render_timings, "source_prestage_seconds", prestage_started
                )
                if not prestage_ok:
                    raise RuntimeError("ffmpeg source-range render failed")

            # Output dimensions are known ahead of the render: vertical modes are
            # always 1080x1920, "original" keeps the (even) source size. Knowing
            # them lets us build the ASS captions up front and burn them in the
            # SAME pass as reframing — one encode instead of two.
            src_w, src_h = ffprobe_video_size(source_clip_path)
            if reframe_format == "original":
                # Captions are burned after the 1080p/60 scale, so their play
                # resolution has to be the capped display size. A 4K or rotated
                # master would otherwise place them on the unscaled frame.
                geometry = probe_video_geometry(source_clip_path)
                if geometry is not None:
                    target_width, target_height = fit_within_1080p(
                        geometry.display_width, geometry.display_height
                    )
                    target_width, target_height = round_to_even(target_width), round_to_even(target_height)
                else:
                    target_width, target_height = round_to_even(src_w), round_to_even(src_h)
            else:
                target_width, target_height = 1080, 1920

            speaker_turns: List[Dict[str, Any]] = []
            speaker_words: List[Dict[str, Any]] = []
            vertical_plan = None
            caption_y = None
            if reframe_format == "vertical":
                # On the direct-EDL path the turns feed nothing: the
                # layout plan below is already skipped there, and the EDL
                # renderer takes no speaker input — so skip the per-clip
                # decode + face detection entirely. The plain path keeps
                # them: its layout stack consumes them.
                if get_config().speaker_framing and not direct_edl:
                    try:
                        speaker_turns, speaker_words = build_clip_speaker_turns(
                            video_path, effective_keep_ranges
                        )
                    except Exception as exc:
                        logger.warning("Speaker turn extraction failed: %s", exc)
                if not direct_edl:
                    # The layout plan samples the intermediate's timeline;
                    # in direct mode there is no intermediate to sample.
                    # A task framing choice ("fit" / "crop") forces the
                    # plan; None keeps the planner's own decision.
                    try:
                        # View sequencing (media.view_sequencing, off by
                        # default) anchors on the peak the EDL already
                        # carries; without one the layout choice measures
                        # the clip's own loudest second. The niche gate
                        # lives in the layout choice, not here.
                        from .media.view_sequencing import peak_from_edl

                        video_filter, mode, caption_y = build_vertical_filter_plan(
                            source_clip_path, src_w, src_h, speaker_turns, speaker_words,
                            framing=framing_mode,
                            layout_template=layout_template,
                            layout_credit=layout_credit,
                            peak_time=peak_from_edl(render_edl),
                            niche=profile_name,
                        )
                        vertical_plan = (video_filter, mode)
                    except Exception as exc:
                        logger.warning("Vertical layout planning failed: %s", exc)
                        caption_y = None
            if layout_report is not None and caption_y is not None:
                layout_report["caption_y"] = caption_y

            # Detect source captions in both polarities, then inspect their
            # actual composed positions for fit, reaction, stack and split layouts.
            source_caption_band = None
            if add_subtitles and not direct_edl:
                try:
                    source_caption_band = detect_burned_caption_band(source_clip_path)
                    if source_caption_band and reframe_format != "original":
                        from .media.source_captions import detect_rendered_caption_band
                        from .media.reframing import caption_probe_plan
                        probe_filter, probe_mode = caption_probe_plan(source_clip_path, reframe_format, vertical_plan)
                        source_caption_band = detect_rendered_caption_band(source_clip_path, probe_filter, probe_mode, ffprobe_duration(source_clip_path))
                except Exception as exc:
                    logger.warning("Burned-in caption detection failed: %s", exc)
                    source_caption_band = None
            if layout_report is not None and source_caption_band is not None:
                layout_report["source_caption_band"] = source_caption_band

            # When this clip renders from an EDL, its crossfade segment
            # joins compact the rendered timeline (and the voice on it);
            # the caption words must shift through the renderer's join
            # plan to stay on the voice. No EDL or no crossfade join ->
            # no plan -> the caption build is exactly as before.
            edl_join_plan = None
            if render_edl and render_edl.get("segments"):
                from .media.edl_render import segment_join_plan

                plan = segment_join_plan(
                    render_edl["segments"],
                    float(render_edl.get("duration") or 0),
                )
                if any(entry["fade"] > 0 for entry in plan):
                    edl_join_plan = plan

            # Hook title duration from the EDL: build_edl resolves
            # hook.title_seconds to the FULL clip duration unless the
            # profile explicitly opted out, so passing it through keeps
            # the title pinned for the whole clip. No EDL -> None ->
            # the caption builder's persistent default applies.
            edl_title_seconds = None
            if render_edl:
                _edl_hook = render_edl.get("hook") or {}
                if _edl_hook.get("title_seconds") is not None:
                    try:
                        edl_title_seconds = float(_edl_hook["title_seconds"])
                    except (TypeError, ValueError):
                        edl_title_seconds = None

            # Face zones for the caption/title face guard: the reframe
            # plan stamped output-space face boxes on the EDL segments;
            # feed them to the caption builder so text never lands on a
            # face. No EDL (or no stamped boxes) -> None -> the caption
            # build is exactly as before.
            face_zones = None
            fit_strips = None
            if render_edl:
                from .media.reframe_plan import (
                    collect_face_zones,
                    collect_fit_strips,
                )

                face_zones = collect_face_zones(render_edl) or None
                # Fit strips keep captions inside a fitted shot's sharp
                # foreground and out of its blurred pad bands.
                fit_strips = collect_fit_strips(render_edl) or None

            # Caption placement the EDL asks for (editor audit D1/D2:
            # the captions block's position/position_y were stamped by
            # every builder and read by none, so the Studio's Top /
            # Middle / Bottom choice and the AI editor's height never
            # moved a caption). Precedence: a deliberate per-clip
            # choice (an explicit position_y, including the Studio's
            # resolved preset) wins outright; the layout plan's
            # face-aware height keeps the plain path; a named profile
            # position applies wherever the plan computed none —
            # refined, where a Reference Style look measured one, by
            # the look's own height (F-A). The ASS builder's face
            # guard still moves any chunk that would land on a face —
            # the request is a preference, the guard is the arbiter.
            edl_caption_y, edl_caption_explicit = edl_caption_position_request(edl)
            if edl_caption_explicit:
                effective_caption_y = edl_caption_y
            elif caption_y is not None:
                effective_caption_y = caption_y
            else:
                effective_caption_y = edl_caption_y

            # Task-level text bands (media.text_bands), decided once
            # per task and stored on it: offered to the builder, which
            # verifies them against THIS clip's geometry before
            # adopting them. An explicit per-clip caption position is
            # a deliberate choice (editor audit D1/D2) and is never
            # overridden — the task caption centre is withheld for
            # such clips and the withholding is stamped; the title
            # band is independent and still offered.
            task_caption_centre = None
            task_title_bottom = None
            if task_text_bands:
                if not edl_caption_explicit:
                    task_caption_centre = task_text_bands.get("caption_centre")
                task_title_bottom = task_text_bands.get("title_bottom")
                if layout_report is not None:
                    layout_report["task_text_bands"] = {
                        "caption_offered": task_caption_centre is not None,
                        "caption_withheld_explicit": bool(
                            edl_caption_explicit
                            and task_text_bands.get("caption_centre") is not None
                        ),
                        "title_offered": task_title_bottom is not None,
                    }

            burn_ass_path: Optional[Path] = None
            fonts_dir: Optional[Path] = None
            captions_started = (
                time.perf_counter() if (add_subtitles or hook_title) else None
            )
            ass_built = bool(
                (add_subtitles or hook_title)
                and build_assemblyai_ass_subtitles(
                    video_path,
                    start_time,
                    end_time,
                    target_width,
                    target_height,
                    ass_path,
                    font_family,
                    font_size,
                    font_color,
                    caption_template,
                    effective_keep_ranges,
                    hook_title=hook_title,
                    include_captions=add_subtitles,
                    template_override=_layout_caption_override(
                        caption_template_override(edl, caption_template),
                        caption_template,
                        layout_template,
                    ),
                    brand_title=(edl or {}).get("brand_title"),
                    position_y_override=effective_caption_y,
                    avoid_band=source_caption_band,
                    caption_layout_report=layout_report,
                    profile_name=profile_name,
                    max_chunk_seconds=max_caption_seconds,
                    edl_join_plan=edl_join_plan,
                    hook_title_seconds=edl_title_seconds,
                    face_zones=face_zones,
                    fit_strips=fit_strips,
                    task_caption_centre=task_caption_centre,
                    task_title_bottom=task_title_bottom,
                )
            )
            if captions_started is not None:
                record_pass_timing(
                    render_timings, "captions_seconds", captions_started
                )
            if ass_built:
                burn_ass_path = ass_path
                fonts_dir = ass_fonts_dir(
                    font_family or get_template(caption_template)["font_family"]
                )

            def render(overlays):
                return render_reframed_clip_ffmpeg(
                    source_clip_path,
                    final_clip_path,
                    reframe_format,
                    subtitle_ass_path=burn_ass_path,
                    fonts_dir=fonts_dir,
                    speaker_turns=speaker_turns,
                    speaker_words=speaker_words,
                    vertical_plan=vertical_plan,
                    broll_overlays=overlays,
                    edl=render_edl,
                    timings=render_timings,
                )[0]

            encode_started = time.perf_counter()
            framed_ok = render(broll_overlays)
            applied_overlays = broll_overlays if framed_ok else None
            if not framed_ok and broll_overlays:
                # Stock footage must never cost the user the clip itself.
                logger.warning("Render with B-roll failed; rendering the clip without it")
                framed_ok = render(None)
            record_pass_timing(render_timings, "encode_seconds", encode_started)
            # broll_applied must tell the truth about BOTH B-roll paths:
            # the legacy overlays parameter above, and the EDL's own
            # broll entries, which the EDL renderer composites in-graph
            # (audit #2 finding A2-3: the flag used to track only the
            # parameter, so EDL-path renders reported False and callers
            # wiped the clip's recorded B-roll placements). The retry
            # above still passes the EDL, so its entries count whenever
            # the surviving render is the successful one.
            from .media.edl_render import edl_broll_will_composite

            broll_applied = bool(
                framed_ok
                and (applied_overlays or edl_broll_will_composite(render_edl))
            )
            if layout_report is not None:
                layout_report["broll_applied"] = broll_applied
            if not framed_ok:
                raise RuntimeError("ffmpeg reframe render failed")

            # The plain path (face-on-top and other reaction layouts) renders
            # without the EDL graph, so its music bed and SFX were dropped:
            # mix them in now in an audio-only pass (media/audio_finish.py).
            if (
                render_edl
                and not direct_edl
                and ((render_edl.get("reframe") or {}).get("delegate") == "plain")
            ):
                from .media.audio_finish import finish_plain_path_audio

                finish_plain_path_audio(final_clip_path, render_edl)

            # Hook finish (media/hook_finish.py): the punch-in "cut" at ~2.6 s,
            # a whoosh, and a brightness lift for dark footage - what the viral
            # Shorts study found our clips were missing. Plain EDLs are skipped.
            if render_edl and reframe_format == "vertical":
                from .media.hook_finish import finish_hook

                finish_hook(final_clip_path, render_edl, seed=Path(output_path).stem)

            # Beat-density audit + repair (media/beat_density.py): measure
            # the finished clip's longest no-change stretch; an over-long
            # dead stretch in a non-Plain clip gains one gentle punch-in.
            # Off unless BEAT_DENSITY=on; the record rides the layout
            # report like the render timings do. Runs BEFORE the end
            # question below, so the audit measures the hook-finished
            # file exactly as the beats lane proved it, and the card is
            # always burned last and never re-encoded by the repair.
            if render_edl and reframe_format == "vertical":
                from .media.beat_density import audit_and_repair_beat_density

                beat_record = audit_and_repair_beat_density(
                    final_clip_path, render_edl
                )
                if beat_record is not None and layout_report is not None:
                    layout_report["beat_density"] = beat_record

            # End question (media/end_question.py): the clip's comment-bait
            # question burned as a small end card over the final seconds.
            # Runs only when the EDL carries one (COMMENT_BAIT was on when
            # the clip was planned); otherwise nothing runs and the file
            # stays exactly as rendered. Plain EDLs are skipped inside.
            if render_edl and reframe_format == "vertical":
                from .media.end_question import apply_end_question

                apply_end_question(final_clip_path, render_edl)

            shutil.move(str(final_clip_path), str(output_path))
            logger.info(f"Successfully created clip with ffmpeg: {output_path}")
            record_pass_timing(render_timings, "total_seconds", clip_started)
            _stamp_render_timings(layout_report, render_timings)
            return True

    except Exception as e:
        logger.error(f"Failed to create clip: {e}")
        record_pass_timing(render_timings, "total_seconds", clip_started)
        _stamp_render_timings(layout_report, render_timings)
        return False


def create_clips_from_segments(
    video_path: Path,
    segments: List[Dict[str, Any]],
    output_dir: Path,
    font_family: Optional[str] = None,
    font_size: Optional[int] = None,
    font_color: Optional[str] = None,
    caption_template: str = "default",
    output_format: str = "vertical",
    add_subtitles: bool = True,
    cleanup_settings: Optional[Dict[str, Any]] = None,
    max_clip_seconds: float = 60.0,
    vocals_stem: Optional[Path] = None,
    task_text_bands: Optional[Dict[str, Any]] = None,
    allowed_ranges: Optional[List[Tuple[float, float]]] = None,
    excluded_ranges: Optional[List[Tuple[float, float]]] = None,
    framing_mode: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """Create optimized video clips from segments with template support.

    ``allowed_ranges`` / ``excluded_ranges`` are a campaign brief's hard
    source-range rule, threaded down from regeneration (the batch path's
    only production caller). The sentence-boundary extension below runs
    unconditionally on carried keep_ranges, so without this audit a
    brief-clamped clip could re-export footage inside an excluded range;
    the audit runs after every keep-range mutator, mirroring R1 on the
    single-clip path, and the ranges are also passed to
    ``create_optimized_clip`` whose R2 audit is the last line. Both stay
    None for non-brief callers, which keeps their behaviour identical.
    """
    logger.info(
        f"Creating {len(segments)} clips subtitles={add_subtitles} template '{caption_template}'"
    )

    output_dir.mkdir(parents=True, exist_ok=True)
    clips_info = []

    for i, segment in enumerate(segments):
        try:
            # Debug log the segment data
            logger.info(
                f"Processing segment {i + 1}: start='{segment.get('start_time')}', end='{segment.get('end_time')}'"
            )

            provided_keep_ranges = normalize_source_ranges(segment.get("keep_ranges"))
            provided_source_ranges = normalize_source_ranges(segment.get("source_ranges"))
            provided_bounds = source_range_bounds(
                provided_keep_ranges or provided_source_ranges
            )
            if provided_bounds:
                start_seconds, end_seconds = provided_bounds
            else:
                start_seconds = parse_timestamp_to_seconds(segment["start_time"])
                end_seconds = parse_timestamp_to_seconds(segment["end_time"])

            duration = end_seconds - start_seconds
            logger.info(
                f"Segment {i + 1} duration: {duration:.1f}s (start: {start_seconds}s, end: {end_seconds}s)"
            )

            if duration <= 0:
                logger.warning(
                    f"Skipping clip {i + 1}: invalid duration {duration:.1f}s (start: {start_seconds}s, end: {end_seconds}s)"
                )
                continue

            clip_filename = (
                f"clip_{i + 1}_{segment['start_time'].replace(':', '')}-"
                f"{segment['end_time'].replace(':', '')}_{uuid.uuid4().hex[:12]}.mp4"
            )
            clip_path = output_dir / clip_filename

            if provided_keep_ranges:
                keep_ranges = provided_keep_ranges
            elif provided_source_ranges:
                keep_ranges = build_keep_ranges_from_source_ranges(
                    video_path,
                    provided_source_ranges,
                    cleanup_settings,
                )
            else:
                start_seconds = snap_clip_start_to_sentence(
                    video_path, start_seconds, end_seconds
                )
                keep_ranges = build_clip_keep_ranges(
                    video_path, start_seconds, end_seconds, cleanup_settings
                )
            keep_ranges = extend_keep_ranges_to_sentence_boundary(video_path, keep_ranges)
            clip_text = segment["text"]
            if not provided_keep_ranges and not provided_source_ranges:
                keep_ranges = apply_segment_hook_teaser(video_path, keep_ranges, segment)
            # Rewind and sentence extension happen before this, so the stored
            # ranges — and the render, which must not extend them again — stay
            # inside the selected duration cap.
            keep_ranges = clamp_keep_ranges_to_max_duration(video_path, keep_ranges, max_clip_seconds)
            if not provided_keep_ranges and not provided_source_ranges:
                # Snapping, cleanup, the teaser and the cap change what is
                # spoken; keep the stored text word-for-word with the timeline.
                clip_text = keep_ranges_transcript_text(video_path, keep_ranges) or clip_text

            if allowed_ranges or excluded_ranges:
                # Brief range audit (Content Requirements): intersect the
                # FINAL keep ranges, after every mutator above (sentence
                # extension, hook teaser, duration clamp). The single-clip
                # path's R1 catches this upstream; the batch path has no
                # R1, so a trim here is expected whenever the extension
                # crossed a brief boundary — log it at WARNING, and the
                # R2 audit inside create_optimized_clip (which receives
                # the same ranges below) should then be a no-op.
                from .auto_director.content_requirements import (
                    intersect_ranges,
                    subtract_ranges,
                )

                audited_ranges = keep_ranges
                if allowed_ranges:
                    audited_ranges = intersect_ranges(
                        audited_ranges, list(allowed_ranges)
                    )
                if excluded_ranges:
                    audited_ranges = subtract_ranges(
                        audited_ranges, list(excluded_ranges)
                    )
                if audited_ranges != keep_ranges:
                    logger.warning(
                        "Brief range audit trimmed out-of-range footage "
                        "from a batch-rendered clip: %s -> %s",
                        keep_ranges,
                        audited_ranges,
                    )
                    keep_ranges = audited_ranges
                if sum(end - start for start, end in keep_ranges) < 0.5:
                    logger.error(
                        "Skipping clip %d: no footage remains inside the "
                        "brief's permitted ranges",
                        i + 1,
                    )
                    continue

            layout_report: Dict[str, Any] = {}
            success = create_optimized_clip(
                video_path,
                start_seconds,
                end_seconds,
                clip_path,
                add_subtitles,
                font_family,
                font_size,
                font_color,
                caption_template,
                output_format,
                keep_ranges,
                hook_title=segment.get("hook_title"),
                extend_to_sentence=False,
                layout_report=layout_report,
                broll_overlays=segment.get("broll_overlays"),
                edl=segment.get("edl"),
                # The stem kwarg is passed only when one is in play, so
                # the no-separation call keeps its exact historical shape
                # (the Stage-1 conditional-kwarg rule); with None the leaf
                # falls back to the EDL stamp exactly as before.
                **(
                    {"vocals_stem": vocals_stem}
                    if vocals_stem is not None
                    else {}
                ),
                # Task text bands ride the same conditional-kwarg rule:
                # absent, the leaf call keeps its historical shape.
                **(
                    {"task_text_bands": task_text_bands}
                    if task_text_bands is not None
                    else {}
                ),
                # Same conditional-kwarg rule for the brief ranges: the
                # R2 audit in the leaf runs only for brief renders.
                **(
                    {
                        "allowed_ranges": allowed_ranges,
                        "excluded_ranges": excluded_ranges,
                    }
                    if (allowed_ranges or excluded_ranges)
                    else {}
                ),
                # Same conditional rule for the task's framing choice:
                # only a task that made one passes it down.
                **(
                    {"framing_mode": framing_mode}
                    if framing_mode is not None
                    else {}
                ),
            )

            if success:
                from .media.ffmpeg import output_offsets_for_ranges
                save_clip_source_ranges(clip_path, keep_ranges, output_offsets_for_ranges(keep_ranges))
                # Smart B-Roll v2, Slice 6: on the EDL path the renderer
                # composites the EDL's own resolved entries and ignores
                # the legacy overlays — the sidecar must record the
                # entries that actually rendered, not the legacy plan,
                # or the editor's review surface shows cutaways the
                # viewer never saw. Without resolved EDL entries this
                # is exactly the old behaviour.
                saved_broll = segment.get("broll") if layout_report.get("broll_applied") else []
                segment_edl = segment.get("edl")
                if isinstance(segment_edl, dict) and layout_report.get("broll_applied"):
                    from .broll import edl_broll_entry_to_placement

                    edl_placements = [
                        placement
                        for placement in (
                            edl_broll_entry_to_placement(entry)
                            for entry in (segment_edl.get("broll") or [])
                            if isinstance(entry, dict)
                        )
                        if placement is not None
                    ]
                    if edl_placements:
                        saved_broll = edl_placements
                save_clip_broll(clip_path, saved_broll)
                if layout_report.get("caption_y") is not None:
                    save_clip_caption_settings(
                        clip_path, {"position_y": layout_report["caption_y"], "position_is_center": True}
                    )
                # Crossfades overlap neighbouring ranges, so the file is
                # shorter than the kept source; store what actually plays.
                cleaned_duration = rendered_clip_duration(clip_path, keep_ranges)
                clip_info = {
                    "clip_id": i + 1,
                    "filename": clip_filename,
                    "path": str(clip_path),
                    "start_time": segment["start_time"],
                    "end_time": segment["end_time"],
                    "duration": cleaned_duration,
                    "text": clip_text,
                    "relevance_score": segment["relevance_score"],
                    "reasoning": segment["reasoning"],
                    # Include virality data if available
                    "virality_score": segment.get("virality_score", 0),
                    "hook_score": segment.get("hook_score", 0),
                    "engagement_score": segment.get("engagement_score", 0),
                    "value_score": segment.get("value_score", 0),
                    "shareability_score": segment.get("shareability_score", 0),
                    "hook_type": segment.get("hook_type"),
                    "hook_title": segment.get("hook_title"),
                    "keep_ranges": keep_ranges,
                }
                clips_info.append(clip_info)
                logger.info(f"Created clip {i + 1}: {cleaned_duration:.1f}s")
            else:
                logger.error(f"Failed to create clip {i + 1}")

        except Exception as e:
            logger.error(f"Error processing clip {i + 1}: {e}")

    logger.info(f"Successfully created {len(clips_info)}/{len(segments)} clips")
    return clips_info


def get_available_transitions() -> List[str]:
    """Get list of available transition video files."""
    transitions_dir = Path(__file__).parent.parent / "transitions"
    if not transitions_dir.exists():
        logger.warning("Transitions directory not found")
        return []

    transition_files = []
    for file_path in transitions_dir.glob("*.mp4"):
        transition_files.append(str(file_path))

    logger.info(f"Found {len(transition_files)} transition files")
    return transition_files


def apply_transition_effect(
    clip1_path: Path, clip2_path: Path, transition_path: Path, output_path: Path
) -> bool:
    """Apply transition effect between two clips using a transition video."""
    try:
        clip1_duration = ffprobe_duration(clip1_path)
        clip2_duration = ffprobe_duration(clip2_path)
        transition_duration = min(1.5, clip1_duration, clip2_duration)
        if transition_duration <= 0:
            logger.warning("Transition duration is zero, skipping transition effect")
            return False

        width, height = ffprobe_video_size(clip2_path)
        clip1_tail_start = max(0.0, clip1_duration - transition_duration)
        filter_parts = [
            (
                f"[0:v]trim=start={clip1_tail_start:.3f}:end={clip1_duration:.3f},"
                f"setpts=PTS-STARTPTS,scale={width}:{height}:flags=lanczos[v0]"
            ),
            (
                f"[1:v]trim=start=0:end={transition_duration:.3f},"
                f"setpts=PTS-STARTPTS,scale={width}:{height}:flags=lanczos[v1]"
            ),
            (
                f"[v0][v1]xfade=transition=fade:duration={transition_duration:.3f}:"
                "offset=0[vintro]"
            ),
        ]
        if clip2_duration - transition_duration > 0.05:
            filter_parts.extend(
                [
                    (
                        f"[1:v]trim=start={transition_duration:.3f}:end={clip2_duration:.3f},"
                        "setpts=PTS-STARTPTS[vrem]"
                    ),
                    "[vintro][vrem]concat=n=2:v=1:a=0[v]",
                ]
            )
            video_label = "[v]"
        else:
            video_label = "[vintro]"

        command = [
            "ffmpeg",
            "-y",
            "-i",
            str(clip1_path),
            "-i",
            str(clip2_path),
            "-filter_complex",
            ";".join(filter_parts),
            "-map",
            video_label,
            "-map",
            "1:a?",
            "-c:v",
            "libx264",
            "-preset",
            "fast",
            "-crf",
            "20",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            "-b:a",
            "192k",
            "-movflags",
            "+faststart",
            str(output_path),
        ]
        success = run_ffmpeg_command(command).returncode == 0
        if success:
            logger.info("Applied transition effect: %s", output_path)
        return success

    except Exception as e:
        logger.error(f"Error applying transition effect: {e}")
        return False


def resize_for_916_filter(target_width: int, target_height: int) -> str:
    """Return a scale/crop filter that fills a target portrait frame."""
    return (
        f"scale={target_width}:{target_height}:force_original_aspect_ratio=increase:"
        f"flags=lanczos,crop={target_width}:{target_height},setsar=1"
    )


def create_clips_with_transitions(
    video_path: Path,
    segments: List[Dict[str, Any]],
    output_dir: Path,
    font_family: Optional[str] = None,
    font_size: Optional[int] = None,
    font_color: Optional[str] = None,
    caption_template: str = "default",
    output_format: str = "vertical",
    add_subtitles: bool = True,
    cleanup_settings: Optional[Dict[str, Any]] = None,
    max_clip_seconds: float = 60.0,
    vocals_stem: Optional[Path] = None,
    task_text_bands: Optional[Dict[str, Any]] = None,
    allowed_ranges: Optional[List[Tuple[float, float]]] = None,
    excluded_ranges: Optional[List[Tuple[float, float]]] = None,
    framing_mode: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """Create standalone video clips without inter-clip transitions.

    Kept as a backward-compatible wrapper for older call sites.
    """
    logger.info(
        f"Creating {len(segments)} standalone clips subtitles={add_subtitles} template '{caption_template}'"
    )
    logger.info(
        "Inter-clip transitions are disabled for standalone SupoClip exports"
    )
    return create_clips_from_segments(
        video_path,
        segments,
        output_dir,
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


def get_video_transcript_with_assemblyai(path: Path) -> str:
    """Backward compatibility wrapper."""
    return get_video_transcript(path)


def create_9_16_clip(
    video_path: Path,
    start_time: float,
    end_time: float,
    output_path: Path,
    subtitle_text: str = "",
) -> bool:
    """Backward compatibility wrapper."""
    return create_optimized_clip(
        video_path, start_time, end_time, output_path, add_subtitles=bool(subtitle_text)
    )


def insert_broll_into_clip(
    main_clip_path: Path,
    broll_path: Path,
    insert_time: float,
    broll_duration: float,
    output_path: Path,
    transition_duration: float = 0.3,
) -> bool:
    """
    Insert B-roll footage into a clip at a specified timestamp.

    Args:
        main_clip_path: Path to the main video clip
        broll_path: Path to the B-roll video
        insert_time: When to insert B-roll (seconds from clip start)
        broll_duration: How long to show B-roll (seconds)
        output_path: Where to save the composited clip
        transition_duration: Crossfade duration (seconds)

    Returns:
        True if successful
    """
    try:
        main_duration = ffprobe_duration(main_clip_path)
        source_broll_duration = ffprobe_duration(broll_path)
        target_width, target_height = ffprobe_video_size(main_clip_path)

        insert_time = max(0.0, min(insert_time, max(0.0, main_duration - 0.5)))
        actual_broll_duration = min(
            max(0.0, broll_duration),
            source_broll_duration,
            max(0.0, main_duration - insert_time),
        )
        if actual_broll_duration <= 0.05:
            logger.warning("B-roll duration is too short, skipping insertion")
            return False

        broll_end_time = insert_time + actual_broll_duration
        fade_duration = min(
            max(0.0, transition_duration),
            max(0.0, actual_broll_duration / 3),
        )

        filter_parts: List[str] = []
        concat_labels: List[str] = []
        segment_count = 0
        if insert_time > 0.05:
            filter_parts.append(
                f"[0:v]trim=start=0:end={insert_time:.3f},setpts=PTS-STARTPTS[vpre]"
            )
            concat_labels.append("[vpre]")
            segment_count += 1

        broll_filter = (
            f"[1:v]trim=start=0:end={actual_broll_duration:.3f},setpts=PTS-STARTPTS,"
            f"{resize_for_916_filter(target_width, target_height)}"
        )
        if fade_duration > 0:
            broll_filter += (
                f",fade=t=in:st=0:d={fade_duration:.3f},"
                f"fade=t=out:st={max(0.0, actual_broll_duration - fade_duration):.3f}:"
                f"d={fade_duration:.3f}"
            )
        filter_parts.append(f"{broll_filter}[vbroll]")
        concat_labels.append("[vbroll]")
        segment_count += 1

        if main_duration - broll_end_time > 0.05:
            filter_parts.append(
                f"[0:v]trim=start={broll_end_time:.3f}:end={main_duration:.3f},"
                "setpts=PTS-STARTPTS[vpost]"
            )
            concat_labels.append("[vpost]")
            segment_count += 1

        if segment_count > 1:
            filter_parts.append(
                f"{''.join(concat_labels)}concat=n={segment_count}:v=1:a=0[v]"
            )
            video_label = "[v]"
        else:
            video_label = concat_labels[0]

        command = [
            "ffmpeg",
            "-y",
            "-i",
            str(main_clip_path),
            "-i",
            str(broll_path),
            "-filter_complex",
            ";".join(filter_parts),
            "-map",
            video_label,
            "-map",
            "0:a?",
            "-c:v",
            "libx264",
            "-preset",
            "fast",
            "-crf",
            "20",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            "-b:a",
            "192k",
            "-movflags",
            "+faststart",
            str(output_path),
        ]
        if run_ffmpeg_command(command).returncode != 0:
            return False

        logger.info(
            f"Inserted B-roll at {insert_time:.1f}s ({actual_broll_duration:.1f}s duration): {output_path}"
        )
        return True

    except Exception as e:
        logger.error(f"Error inserting B-roll: {e}")
        return False


def apply_broll_to_clip(
    clip_path: Path, broll_suggestions: List[Dict[str, Any]], output_path: Path
) -> bool:
    """
    Apply multiple B-roll insertions to a clip.

    Args:
        clip_path: Path to the main clip
        broll_suggestions: List of B-roll suggestions with local_path, timestamp, duration
        output_path: Where to save the final clip

    Returns:
        True if successful
    """
    if not broll_suggestions:
        logger.info("No B-roll suggestions to apply")
        return False

    try:
        # Sort suggestions by timestamp (process from end to start to preserve timing)
        sorted_suggestions = sorted(
            broll_suggestions, key=lambda x: x.get("timestamp", 0), reverse=True
        )

        current_clip_path = clip_path
        temp_paths = []

        for i, suggestion in enumerate(sorted_suggestions):
            broll_path = suggestion.get("local_path")
            if not broll_path or not Path(broll_path).exists():
                logger.warning(f"B-roll file not found: {broll_path}")
                continue

            timestamp = suggestion.get("timestamp", 0)
            duration = suggestion.get("duration", 3.0)

            # Create temp output for intermediate clips
            if i < len(sorted_suggestions) - 1:
                temp_output = output_path.parent / f"temp_broll_{i}.mp4"
                temp_paths.append(temp_output)
            else:
                temp_output = output_path

            success = insert_broll_into_clip(
                current_clip_path, Path(broll_path), timestamp, duration, temp_output
            )

            if success:
                current_clip_path = temp_output
            else:
                logger.warning(f"Failed to insert B-roll at {timestamp}s")

        # Cleanup temp files
        for temp_path in temp_paths:
            if temp_path.exists() and temp_path != output_path:
                try:
                    temp_path.unlink()
                except Exception:
                    pass

        return True

    except Exception as e:
        logger.error(f"Error applying B-roll to clip: {e}")
        return False
