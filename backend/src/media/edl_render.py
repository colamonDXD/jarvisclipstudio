"""FFmpeg execution of auto-director edit decision lists."""

from functools import lru_cache
from pathlib import Path
from typing import Any, Optional

from .common import OUTPUT_FPS, logger
from .ffmpeg import build_audio_output_args, build_final_video_encode_args, run_ffmpeg_command, subtitles_filter_fragment

TRANSITION_MAP = {"hard_cut": None, "cut": None, "none": None, "cross_dissolve": "fade", "fade": "fade", "slide": "slideleft", "wipe": "wipeleft"}


@lru_cache(maxsize=1)
def check_nvenc_available() -> bool:
    """Return whether this FFmpeg build exposes NVIDIA's H.264 encoder."""
    try:
        result = run_ffmpeg_command(["ffmpeg", "-hide_banner", "-encoders"], timeout=30)
    except OSError:
        return False
    if result.returncode != 0 or "h264_nvenc" not in result.stdout:
        return False
    # Some FFmpeg builds advertise NVENC even when the host has no usable GPU.
    # A one-frame encode avoids choosing that encoder only to fail the clip.
    probe = run_ffmpeg_command(
        ["ffmpeg", "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i", "color=size=64x64:rate=1", "-frames:v", "1", "-c:v", "h264_nvenc", "-f", "null", "-"],
        timeout=30,
    )
    return probe.returncode == 0


def get_video_encoder(use_nvenc: bool = True) -> tuple[str, list[str]]:
    if use_nvenc and check_nvenc_available():
        return "h264_nvenc", ["-c:v", "h264_nvenc", "-preset", "p4", "-rc", "vbr", "-cq", "22", "-pix_fmt", "yuv420p", "-profile:v", "high"]
    return "libx264", build_final_video_encode_args()


def _label(name: str) -> str:
    return f"[{name}]"


def _zoom_expression(start: float, end: float, scale_start: float, scale_end: float) -> str:
    """Build a zoompan expression driven by its documented output-frame counter."""
    start_frame = max(0, round(start * OUTPUT_FPS))
    end_frame = max(start_frame + 1, round(end * OUTPUT_FPS))
    span = end_frame - start_frame
    return f"if(lt(on,{start_frame}),1,if(lt(on,{end_frame}),{scale_start:.6f}+({scale_end:.6f}-{scale_start:.6f})*(on-{start_frame})/{span},{scale_end:.6f}))"


def build_kenburns_filter(start_time: float, end_time: float, scale_start: float, scale_end: float, input_label: str, output_label: str) -> str:
    if end_time <= start_time:
        return ""
    return f"{input_label}zoompan=z='{_zoom_expression(start_time, end_time, scale_start, scale_end)}':x='(iw-iw/zoom)/2':y='(ih-ih/zoom)/2':d=1:s=1080x1920:fps={OUTPUT_FPS}{output_label}"


def build_punch_in_filter(time: float, scale: float, duration: float, input_label: str, output_label: str) -> str:
    if duration <= 0:
        return ""
    start_frame = max(0, round(time * OUTPUT_FPS))
    end_frame = max(start_frame + 1, round((time + duration) * OUTPUT_FPS))
    expression = f"if(lt(on,{start_frame}),1,if(lt(on,{end_frame}),{scale:.6f},1))"
    return f"{input_label}zoompan=z='{expression}':x='(iw-iw/zoom)/2':y='(ih-ih/zoom)/2':d=1:s=1080x1920:fps={OUTPUT_FPS}{output_label}"


def build_drawtext_filter(text: str, start_time: float, end_time: float, input_label: str, output_label: str) -> str:
    escaped = text.replace("\\", r"\\").replace("'", r"\'").replace(":", r"\:").replace("%", r"\%")
    enable = f"between(t\\,{start_time:.3f}\\,{end_time:.3f})"
    return f"{input_label}drawtext=text='{escaped}':fontsize=48:fontcolor=white:box=1:boxcolor=0x000000AA:boxborderw=10:x=(w-text_w)/2:y=h*0.85:enable='{enable}'{output_label}"


def build_edl_filter_graph(edl: dict[str, Any], input_path: Path, subtitle_ass_path: Optional[Path] = None, fonts_dir: Optional[Path] = None) -> tuple[list[str], str, list[str], str]:
    """Build the EDL video graph without changing the non-EDL render path."""
    segments = edl.get("segments") or []
    if not segments:
        raise ValueError("EDL has no segments")
    duration = float(edl.get("duration") or 0)
    graph: list[str] = []
    labels: list[str] = []
    next_id = 0
    for index, segment in enumerate(segments):
        start, end = max(0.0, float(segment.get("start", 0))), float(segment.get("end", duration))
        if end <= start:
            raise ValueError(f"EDL segment {index} has an invalid range")
        name = f"seg{index}"
        graph.append(f"[0:v]trim=start={start:.3f}:end={end:.3f},setpts=PTS-STARTPTS,fps={OUTPUT_FPS},scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,setsar=1,format=yuv420p{_label(name)}")
        label = _label(name)
        for zoom in edl.get("zoom_keyframes") or []:
            zoom_type = zoom.get("type")
            if zoom_type == "punch_in" and start <= float(zoom.get("time", -1)) < end:
                output = _label(f"zoom{next_id}")
                next_id += 1
                graph.append(build_punch_in_filter(float(zoom["time"]) - start, float(zoom.get("scale", 1.25)), float(zoom.get("duration", .5)), label, output))
                label = output
            elif zoom_type == "ken_burns":
                zoom_start, zoom_end = float(zoom.get("start", 0)), float(zoom.get("end", duration))
                overlap_start, overlap_end = max(start, zoom_start), min(end, zoom_end)
                if overlap_end > overlap_start:
                    output = _label(f"zoom{next_id}")
                    next_id += 1
                    graph.append(build_kenburns_filter(overlap_start - start, overlap_end - start, float(zoom.get("scale_start", 1)), float(zoom.get("scale_end", 1.05)), label, output))
                    label = output
        labels.append(label)

    current = labels[0]
    rendered_duration = float(segments[0].get("end", duration)) - float(segments[0].get("start", 0))
    for index, segment in enumerate(segments[1:], start=1):
        transition = str(segment.get("transition_in", "cut")).lower()
        next_duration = float(segment.get("end", duration)) - float(segment.get("start", 0))
        requested = max(0.0, float(segment.get("transition_duration", 0)))
        fade = min(requested, rendered_duration, next_duration) if TRANSITION_MAP.get(transition) else 0.0
        output = _label(f"join{next_id}")
        next_id += 1
        if fade:
            graph.append(f"{current}{labels[index]}xfade=transition={TRANSITION_MAP[transition]}:duration={fade:.6f}:offset={rendered_duration - fade:.6f}{output}")
            rendered_duration += next_duration - fade
        else:
            graph.append(f"{current}{labels[index]}concat=n=2:v=1:a=0{output}")
            rendered_duration += next_duration
        current = output

    extra_inputs: list[str] = []
    image_index = 1
    for overlay in edl.get("overlays") or []:
        start = max(0.0, float(overlay.get("time", 0)))
        end = start + max(0.0, float(overlay.get("duration", 3)))
        output = _label(f"overlay{next_id}")
        next_id += 1
        if overlay.get("type") == "info_card" and overlay.get("text") and end > start:
            graph.append(build_drawtext_filter(str(overlay["text"]), start, end, current, output))
            current = output
        elif overlay.get("type") == "reference_image":
            image_path = Path(str(overlay.get("local_path") or ""))
            if image_path.is_file() and end > start:
                capped_end = min(end, start + 3.0)
                extra_inputs.extend(["-loop", "1", "-i", str(image_path)])
                enable = f"between(t\\,{start:.3f}\\,{capped_end:.3f})"
                graph.append(f"[{image_index}:v]scale=360:-1:flags=lanczos,setsar=1[pip{image_index}];{current}[pip{image_index}]overlay=x=W-w-20:y=H-h-20:enable='{enable}'{output}")
                image_index += 1
                current = output
    if subtitle_ass_path:
        output = _label(f"subtitle{next_id}")
        graph.append(f"{current}{subtitles_filter_fragment(subtitle_ass_path, fonts_dir)}{output}")
        current = output
    _, encoder_args = get_video_encoder()
    return extra_inputs, ";".join(graph), encoder_args, current


def render_edl_clip_ffmpeg(input_path: Path, output_path: Path, edl: dict[str, Any], subtitle_ass_path: Optional[Path] = None, fonts_dir: Optional[Path] = None) -> bool:
    if not edl or not edl.get("segments"):
        return False
    extra_inputs, graph, encoder_args, video_label = build_edl_filter_graph(edl, input_path, subtitle_ass_path, fonts_dir)
    command = ["ffmpeg", "-y", "-i", str(input_path), *extra_inputs, "-filter_complex", graph, "-map", video_label, "-map", "0:a?", *encoder_args, *build_audio_output_args(True), "-shortest", "-movflags", "+faststart", str(output_path)]
    logger.info("Rendering EDL clip: %s", output_path)
    try:
        result = run_ffmpeg_command(command, timeout=3600)
    except OSError as exc:
        logger.error("EDL render could not start: %s", exc)
        return False
    return result.returncode == 0
