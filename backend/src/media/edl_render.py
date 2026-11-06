"""EDL (Edit Decision List) execution for auto-director.

Builds FFmpeg filter graphs to render clips according to EDL specifications:
- Segment transitions via xfade (profile default; "cut" = no xfade)
- zoompan for punch-in / ken-burns keyframes at EDL timestamps
- info-card drawtext overlays with verbatim text and times
- reference-image overlay PiP bottom-right (max 3s) when EDL gives local image path
"""

from pathlib import Path
from typing import Any, Optional
from .ffmpeg import (
    build_audio_output_args,
    build_final_video_encode_args,
    ffprobe_duration,
    ffprobe_has_audio,
    ffprobe_video_size,
    run_ffmpeg_command,
    subtitles_filter_fragment,
)
from .common import OUTPUT_FPS, logger


# Transition type mapping from profile to FFmpeg xfade transition names
TRANSITION_MAP = {
    "hard_cut": None,  # No xfade, just concat
    "cut": None,
    "cross_dissolve": "fade",
    "fade": "fade",
    "slide": "slideleft",
    "wipe": "wipeleft",
}


def check_nvenc_available() -> bool:
    """Check if h264_nvenc encoder is available in FFmpeg."""
    result = run_ffmpeg_command(["ffmpeg", "-encoders"])
    return "h264_nvenc" in result.stdout


def get_video_encoder(use_nvenc: bool = True) -> tuple[str, list[str]]:
    """Get the video encoder and its args.

    Returns:
        Tuple of (encoder_name, encoder_args)
    """
    if use_nvenc and check_nvenc_available():
        return "h264_nvenc", [
            "-c:v", "h264_nvenc",
            "-preset", "p4",
            "-rc", "vbr",
            "-cq", "22",
            "-pix_fmt", "yuv420p",
            "-profile:v", "high",
        ]
    return "libx264", build_final_video_encode_args()


def build_transition_filter(
    transition_type: str,
    duration: float,
    offset: float,
    in_label: str,
    out_label: str,
    next_label: str,
) -> tuple[str, str]:
    """Build xfade transition filter.

    Returns:
        Tuple of (filter_string, output_label)
    """
    ffmpeg_transition = TRANSITION_MAP.get(transition_type.lower())
    if ffmpeg_transition is None or duration <= 0:
        # Hard cut - just concat
        return f"{in_label}{out_label}concat=n=2:v=1:a=0[{next_label}]", f"[{next_label}]"

    return (
        f"{in_label}{out_label}xfade=transition={ffmpeg_transition}:duration={duration:.6f}:offset={offset:.6f}[{next_label}]",
        f"[{next_label}]"
    )


def build_kenburns_filter(
    start_time: float,
    end_time: float,
    scale_start: float,
    scale_end: float,
    input_label: str,
    output_label: str,
) -> str:
    """Build zoompan filter for Ken Burns effect."""
    duration = end_time - start_time
    if duration <= 0:
        return ""

    # Linear zoom expression
    z_expr = f"if(isnan(it),{scale_start},{scale_start}+({scale_end}-{scale_start})*min(it/{duration:.3f},1))"

    return (
        f"{input_label}zoompan=z='{z_expr}':"
        f"x='(iw-iw/zoom)/2':y='(ih-ih/zoom)*0.5'"
        f":d=1:s=1080x1920:fps={OUTPUT_FPS}[{output_label}]"
    )


def build_punch_in_filter(
    time: float,
    scale: float,
    duration: float,
    input_label: str,
    output_label: str,
) -> str:
    """Build zoompan filter for punch-in effect at a specific time."""
    # Punch-in: zoom in quickly, hold, then zoom out
    # Using a piecewise expression for smooth punch in/out
    hold_end = time + duration
    z_expr = (
        f"if(lt(t,{time:.3f}),1,"
        f"if(lt(t,{hold_end:.3f}),{scale},"
        f"1))"
    )

    return (
        f"{input_label}zoompan=z='{z_expr}':"
        f"x='(iw-iw/zoom)/2':y='(ih-ih/zoom)*0.5'"
        f":d=1:s=1080x1920:fps={OUTPUT_FPS}[{output_label}]"
    )


def build_drawtext_filter(
    text: str,
    start_time: float,
    end_time: float,
    input_label: str,
    output_label: str,
    font_size: int = 48,
    font_color: str = "white",
    box_color: str = "0x000000AA",
    x_pos: str = "(w-text_w)/2",
    y_pos: str = "h*0.85",
) -> str:
    """Build drawtext filter for info card overlay.

    Text is drawn verbatim from transcript as required by EDL spec.
    """
    # Escape text for drawtext filter
    escaped_text = text.replace(":", "\\:").replace("'", "\\'").replace("%", "\\%")

    enable_expr = f"between(t\\,{start_time:.3f}\\,{end_time:.3f})"

    return (
        f"{input_label}drawtext="
        f"text='{escaped_text}':"
        f"fontsize={font_size}:"
        f"fontcolor={font_color}:"
        f"box=1:boxcolor={box_color}:"
        f"boxborderw=10:"
        f"x={x_pos}:y={y_pos}:"
        f"enable='{enable_expr}'"
        f"[{output_label}]"
    )


def build_overlay_filter(
    overlay_path: Path,
    start_time: float,
    end_time: float,
    input_label: str,
    output_label: str,
    x_pos: str = "W-w-20",
    y_pos: str = "H-h-20",
    max_duration: float = 3.0,
) -> tuple[list[str], str]:
    """Build overlay filter for reference image PiP.

    Returns:
        Tuple of (input_args, filter_string)
    """
    actual_duration = min(end_time - start_time, max_duration)
    enable_expr = f"between(t\\,{start_time:.3f}\\,{start_time + actual_duration:.3f})"

    input_args = ["-i", str(overlay_path)]
    filter_str = (
        f"[1:v]scale=360:-1:flags=lanczos,setsar=1[ovr];"
        f"{input_label}[ovr]overlay=x={x_pos}:y={y_pos}:enable='{enable_expr}'[{output_label}]"
    )
    return input_args, filter_str


def build_edl_filter_graph(
    edl: dict[str, Any],
    input_path: Path,
    subtitle_ass_path: Optional[Path] = None,
    fonts_dir: Optional[Path] = None,
) -> tuple[list[str], str, list[str]]:
    """Build complete FFmpeg filter graph for EDL execution.

    Args:
        edl: The EDL dict from build_edl()
        input_path: Path to source video
        subtitle_ass_path: Optional ASS subtitle file to burn
        fonts_dir: Optional fonts directory for subtitles

    Returns:
        Tuple of (extra_input_args, filter_complex_string, encoder_args)
    """
    segments = edl.get("segments", [])
    zoom_keyframes = edl.get("zoom_keyframes", [])
    overlays = edl.get("overlays", [])
    broll = edl.get("broll", [])  # Not used in this module - handled by video_utils
    duration = edl.get("duration", 0)

    if not segments:
        raise ValueError("EDL has no segments")

    # Check if we have transitions (more than 1 segment with non-cut transitions)
    has_transitions = len(segments) > 1 and any(
        s.get("transition_in") not in ("none", "cut", "hard_cut") and s.get("transition_duration", 0) > 0
        for s in segments[1:]
    )

    # Build segment filter chain
    filter_parts = []
    input_args = []

    # Split input for each segment
    for i, seg in enumerate(segments):
        seg_start = seg.get("start", 0)
        seg_end = seg.get("end", duration)
        seg_duration = seg_end - seg_start

        filter_parts.append(
            f"[0:v]trim=start={seg_start:.3f}:end={seg_end:.3f},"
            f"setpts=PTS-STARTPTS,"
            f"fps={OUTPUT_FPS},format=yuv420p,setsar=1[v{i}]"
        )

    # Apply zoom keyframes to segments
    # For simplicity, we apply zooms per-segment based on keyframe timing
    current_label = "[v0]"
    label_counter = len(segments)

    # Process zoom keyframes - group by segment
    for i, seg in enumerate(segments):
        seg_start = seg.get("start", 0)
        seg_end = seg.get("end", duration)

        # Find zoom keyframes that apply to this segment
        seg_zooms = [
            z for z in zoom_keyframes
            if seg_start <= z.get("time", 0) < seg_end
            or (z.get("type") == "ken_burns" and z.get("start", 0) <= seg_end and z.get("end", duration) >= seg_start)
        ]

        input_label = f"[v{i}]"
        output_label = f"[vz{label_counter}]"
        label_counter += 1

        for zk in seg_zooms:
            zk_type = zk.get("type")
            if zk_type == "ken_burns":
                filter_parts.append(build_kenburns_filter(
                    max(zk.get("start", 0), seg_start) - seg_start,
                    min(zk.get("end", duration), seg_end) - seg_start,
                    zk.get("scale_start", 1.0),
                    zk.get("scale_end", 1.05),
                    input_label,
                    output_label,
                ))
                input_label = output_label
                output_label = f"[vz{label_counter}]"
                label_counter += 1
            elif zk_type == "punch_in":
                filter_parts.append(build_punch_in_filter(
                    zk.get("time", 0) - seg_start,
                    zk.get("scale", 1.25),
                    zk.get("duration", 0.5),
                    input_label,
                    output_label,
                ))
                input_label = output_label
                output_label = f"[vz{label_counter}]"
                label_counter += 1

        # Update segment label to the final zoom output
        filter_parts.append(f"{input_label}copy[{output_label}]" if input_label != f"[v{i}]" else "")
        # Actually we need to track the final label for each segment
        segments[i]["_final_label"] = input_label if seg_zooms else f"[v{i}]"

    # Now build transition chain
    if len(segments) == 1:
        # Single segment, no transitions needed
        final_video_label = segments[0]["_final_label"]
    else:
        # Chain segments with transitions
        cur_label = segments[0]["_final_label"]
        cumulative_duration = segments[0]["duration"]

        for i in range(1, len(segments)):
            seg = segments[i]
            trans_in = seg.get("transition_in", "cut")
            trans_dur = seg.get("transition_duration", 0)
            next_label = seg["_final_label"]
            out_label = f"[vtrans{label_counter}]"
            label_counter += 1

            filter_str, cur_label = build_transition_filter(
                trans_in, trans_dur, cumulative_duration,
                cur_label, next_label, f"vtrans{label_counter-1}"
            )
            filter_parts.append(filter_str)
            cumulative_duration += seg["duration"] - trans_dur

        final_video_label = cur_label

    # Apply info card overlays (drawtext)
    for ov in overlays:
        if ov.get("type") == "info_card":
            ov_start = ov.get("time", 0)
            ov_end = ov_start + ov.get("duration", 3)
            ov_text = ov.get("text", "")

            if ov_text and ov_end > ov_start:
                next_label = f"[vtxt{label_counter}]"
                label_counter += 1
                filter_parts.append(build_drawtext_filter(
                    ov_text, ov_start, ov_end,
                    final_video_label, next_label
                ))
                final_video_label = next_label

    # Apply reference image overlays (PiP)
    extra_overlay_inputs = []
    for ov in overlays:
        if ov.get("type") == "reference_image":
            local_path = ov.get("local_path", "")
            if local_path and Path(local_path).exists():
                ov_start = ov.get("time", 0)
                ov_end = ov_start + ov.get("duration", 3)

                next_label = f"[vovr{label_counter}]"
                label_counter += 1

                input_args_ov, filter_str = build_overlay_filter(
                    Path(local_path), ov_start, ov_end,
                    final_video_label, next_label
                )
                extra_overlay_inputs.extend(input_args_ov)
                filter_parts.append(filter_str)
                final_video_label = next_label

    # Add subtitles if provided
    map_label = final_video_label
    if subtitle_ass_path:
        subs_fragment = subtitles_filter_fragment(subtitle_ass_path, fonts_dir)
        next_label = f"[vsub{label_counter}]"
        label_counter += 1
        filter_parts.append(f"{final_video_label}{subs_fragment}[{next_label}]")
        map_label = f"[{next_label}]"

    # Build final filter complex
    filter_complex = ";".join(p for p in filter_parts if p)

    # Get encoder args
    _, encoder_args = get_video_encoder(use_nvenc=True)

    return extra_overlay_inputs, filter_complex, encoder_args, map_label


def render_edl_clip_ffmpeg(
    input_path: Path,
    output_path: Path,
    edl: dict[str, Any],
    subtitle_ass_path: Optional[Path] = None,
    fonts_dir: Optional[Path] = None,
) -> bool:
    """Render a clip according to EDL specification.

    This is the main entry point for EDL-based rendering.
    Falls back to standard rendering if EDL is None or empty.

    Args:
        input_path: Path to source video
        output_path: Path for output video
        edl: EDL dict from build_edl() or None for standard rendering
        subtitle_ass_path: Optional ASS subtitle file to burn
        fonts_dir: Optional fonts directory for subtitles

    Returns:
        True if render succeeded
    """
    if not edl or not edl.get("segments"):
        # No EDL - should not be called in this case
        logger.warning("render_edl_clip_ffmpeg called with empty EDL")
        return False

    has_audio = ffprobe_has_audio(input_path)
    audio_args = build_audio_output_args(has_audio)

    extra_inputs, filter_complex, encoder_args, map_label = build_edl_filter_graph(
        edl, input_path, subtitle_ass_path, fonts_dir
    )

    command = [
        "ffmpeg", "-y", "-i", str(input_path),
        *extra_inputs,
        "-filter_complex", filter_complex,
        "-map", map_label, "-map", "0:a?",
        *encoder_args,
        *audio_args,
        "-movflags", "+faststart",
        str(output_path),
    ]

    logger.info(f"Rendering EDL clip: {output_path}")
    result = run_ffmpeg_command(command, timeout=3600)

    if result.returncode != 0:
        logger.error(f"EDL render failed: {result.stderr[-4000:]}")
        return False

    return True