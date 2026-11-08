"""FFmpeg execution of auto-director edit decision lists."""

from functools import lru_cache
from pathlib import Path
from typing import Any, Optional

from .common import LOUDNORM_FILTER, OUTPUT_FPS, logger
from .ffmpeg import build_audio_output_args, build_final_video_encode_args, run_ffmpeg_command, subtitles_filter_fragment

# The full FFmpeg xfade catalog: 58 named transitions (verified against the
# xfade filter documentation). Each name maps to itself, so any of them can
# be used directly as an EDL/profile transition value. The legacy profile
# names keep their long-standing targets below. A transition name missing
# from TRANSITION_MAP (including xfade's expression-only "custom") resolves
# to None and degrades safely to a hard cut in the join loop.
XFADE_TRANSITIONS = (
    "fade", "wipeleft", "wiperight", "wipeup", "wipedown",
    "slideleft", "slideright", "slideup", "slidedown",
    "circlecrop", "rectcrop", "distance", "fadeblack", "fadewhite",
    "radial", "smoothleft", "smoothright", "smoothup", "smoothdown",
    "circleopen", "circleclose", "vertopen", "vertclose",
    "horzopen", "horzclose", "dissolve", "pixelize",
    "diagtl", "diagtr", "diagbl", "diagbr",
    "hlslice", "hrslice", "vuslice", "vdslice", "hblur", "fadegrays",
    "wipetl", "wipetr", "wipebl", "wipebr", "squeezeh", "squeezev",
    "zoomin", "fadefast", "fadeslow",
    "hlwind", "hrwind", "vuwind", "vdwind",
    "coverleft", "coverright", "coverup", "coverdown",
    "revealleft", "revealright", "revealup", "revealdown",
)

TRANSITION_MAP = {
    "hard_cut": None,
    "cut": None,
    "none": None,
    "cross_dissolve": "fade",
    "slide": "slideleft",
    "wipe": "wipeleft",
    **{name: name for name in XFADE_TRANSITIONS},
}

# User asset library (repo-root assets/music and assets/sfx). Files are
# resolved by the exact name the EDL/profile asks for, or by file stem with
# any common audio extension. Anything missing is skipped silently.
ASSETS_DIR = Path(__file__).resolve().parents[3] / "assets"
AUDIO_FILE_EXTENSIONS = (".mp3", ".wav", ".m4a", ".aac", ".ogg", ".flac", ".opus")
# Every stream entering the audio-finishing mix is normalised to this format
# so amix/sidechaincompress always see matching sample rate and layout.
_AUDIO_NORMALIZE = "aresample=48000,aformat=sample_fmts=fltp:sample_rates=48000:channel_layouts=stereo"


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

    # B-roll cutaways: full-frame stock windows composited over the joined
    # segments, under the overlays and captions. Only entries that resolved
    # to a real file participate (see pixabay_broll.resolve_edl_broll);
    # unresolved entries are skipped and the graph is unchanged. The stock
    # input's own audio is never mapped, so the clip's audio (voice, music,
    # SFX) runs uninterrupted underneath.
    for entry in edl.get("broll") or []:
        if not isinstance(entry, dict):
            continue
        broll_path = Path(str(entry.get("local_path") or ""))
        try:
            win_start = float(entry.get("start", 0))
            win_end = float(entry.get("end", 0))
            src_start = max(0.0, float(entry.get("source_start", 0) or 0))
        except (TypeError, ValueError):
            continue
        if not broll_path.is_file() or win_end <= win_start:
            continue
        input_index = 1 + extra_inputs.count("-i")
        extra_inputs.extend(["-stream_loop", "-1", "-i", str(broll_path)])
        stock_label = _label(f"broll{next_id}")
        output = _label(f"brolljoin{next_id}")
        next_id += 1
        graph.append(f"[{input_index}:v]trim=start={src_start:.3f}:end={src_start + (win_end - win_start):.3f},setpts=PTS-STARTPTS+{win_start:.3f}/TB,fps={OUTPUT_FPS},scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,setsar=1,format=yuv420p{stock_label}")
        enable = f"between(t\\,{win_start:.3f}\\,{win_end:.3f})"
        graph.append(f"{current}{stock_label}overlay=x=0:y=0:enable='{enable}'{output}")
        current = output

    image_index = 1 + extra_inputs.count("-i")
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


def _resolve_asset_file(folder: Path, name: str) -> Optional[Path]:
    """Resolve an asset by exact filename, or by stem with an audio extension."""
    if not name or not folder.is_dir():
        return None
    candidate = folder / name
    if candidate.is_file():
        return candidate
    if Path(name).suffix:
        return None
    for extension in AUDIO_FILE_EXTENSIONS:
        candidate = folder / f"{name}{extension}"
        if candidate.is_file():
            return candidate
    for entry in sorted(folder.iterdir()):
        if entry.is_file() and entry.stem == name:
            return entry
    return None


def _rendered_duration(edl: dict[str, Any]) -> float:
    """Total output duration of an EDL, mirroring build_edl_filter_graph's joins."""
    segments = edl.get("segments") or []
    if not segments:
        return max(0.0, float(edl.get("duration") or 0))
    duration = float(edl.get("duration") or 0)
    rendered = float(segments[0].get("end", duration)) - float(segments[0].get("start", 0))
    for segment in segments[1:]:
        next_duration = float(segment.get("end", duration)) - float(segment.get("start", 0))
        transition = str(segment.get("transition_in", "cut")).lower()
        requested = max(0.0, float(segment.get("transition_duration", 0)))
        fade = min(requested, rendered, next_duration) if TRANSITION_MAP.get(transition) else 0.0
        rendered += next_duration - fade
    return max(0.0, rendered)


def build_edl_audio_graph(edl: dict[str, Any], first_input_index: int, assets_dir: Optional[Path] = None) -> Optional[tuple[list[str], str, str]]:
    """Plan the audio-finishing graph (music ducking + SFX events) for an EDL.

    Returns (extra ffmpeg inputs, filter-graph fragment, output audio label),
    or None when the EDL carries no music track or SFX event that resolves to
    a file in the asset library — the caller then keeps the legacy audio path
    untouched. The fragment ends with the -14 LUFS loudnorm as its last step:
    FFmpeg refuses a -af chain on a stream fed from a complex filtergraph, so
    the finished path carries loudnorm inside the graph and the caller drops
    it from the output args (the legacy path keeps it via -af).
    """
    assets = Path(assets_dir) if assets_dir is not None else ASSETS_DIR
    duration = _rendered_duration(edl)
    if duration <= 0:
        return None
    audio = edl.get("audio") or {}
    try:
        volume = float(audio.get("music_volume_0_to_1", audio.get("music_volume", 0.0)) or 0.0)
        ducking_db = float(audio.get("ducking_db", 0.0) or 0.0)
    except (TypeError, ValueError):
        return None
    track_name = str(audio.get("music_track") or audio.get("music_mood") or "").strip()
    music_path = None
    if track_name and track_name.lower() != "none" and volume > 0:
        music_path = _resolve_asset_file(assets / "music", track_name)

    sfx_events: list[tuple[Path, float, float]] = []
    for event in edl.get("sfx_events") or []:
        if not isinstance(event, dict):
            continue
        name = str(event.get("name") or "").strip()
        if not name:
            continue
        sfx_path = _resolve_asset_file(assets / "sfx", name)
        if sfx_path is None:
            continue
        try:
            at_sec = max(0.0, float(event.get("at_sec", 0) or 0))
            gain_db = float(event.get("gain_db", 0) or 0)
        except (TypeError, ValueError):
            continue
        sfx_events.append((sfx_path, at_sec, gain_db))

    if music_path is None and not sfx_events:
        return None

    ducking = music_path is not None and ducking_db > 0
    extra_inputs: list[str] = []
    graph: list[str] = []
    if ducking:
        graph.append(f"[0:a]atrim=0:{duration:.3f},asetpts=PTS-STARTPTS,{_AUDIO_NORMALIZE},asplit=2[a_voice][a_voice_sc]")
    else:
        graph.append(f"[0:a]atrim=0:{duration:.3f},asetpts=PTS-STARTPTS,{_AUDIO_NORMALIZE}[a_voice]")
    mix_labels = ["[a_voice]"]
    next_index = first_input_index
    if music_path is not None:
        extra_inputs.extend(["-stream_loop", "-1", "-i", str(music_path)])
        graph.append(f"[{next_index}:a]atrim=0:{duration:.3f},asetpts=PTS-STARTPTS,{_AUDIO_NORMALIZE},volume={volume:.6f}[a_music]")
        next_index += 1
        if ducking:
            # ducking_db drives the compressor ratio: more requested ducking,
            # harder the music is pushed under the voice.
            ratio = min(20.0, 2.0 + ducking_db)
            graph.append(f"[a_music][a_voice_sc]sidechaincompress=threshold=0.02:ratio={ratio:.2f}:attack=20:release=300[a_music_ducked]")
            mix_labels.append("[a_music_ducked]")
        else:
            mix_labels.append("[a_music]")
    for position, (sfx_path, at_sec, gain_db) in enumerate(sfx_events):
        extra_inputs.extend(["-i", str(sfx_path)])
        gain = 10 ** (gain_db / 20.0)
        delay_ms = int(round(at_sec * 1000))
        graph.append(f"[{next_index}:a]{_AUDIO_NORMALIZE},adelay={delay_ms}:all=1,volume={gain:.6f}[a_sfx{position}]")
        next_index += 1
        mix_labels.append(f"[a_sfx{position}]")
    graph.append(f"{''.join(mix_labels)}amix=inputs={len(mix_labels)}:duration=first:dropout_transition=0:normalize=0[a_mix]")
    graph.append(f"[a_mix]{LOUDNORM_FILTER}[a_out]")
    return extra_inputs, ";".join(graph), "[a_out]"


def _run_render_command(command: list[str]) -> bool:
    try:
        result = run_ffmpeg_command(command, timeout=3600)
    except OSError as exc:
        logger.error("EDL render could not start: %s", exc)
        return False
    return result.returncode == 0


def render_edl_clip_ffmpeg(input_path: Path, output_path: Path, edl: dict[str, Any], subtitle_ass_path: Optional[Path] = None, fonts_dir: Optional[Path] = None, assets_dir: Optional[Path] = None) -> bool:
    if not edl or not edl.get("segments"):
        return False
    # Stock B-roll resolves lazily at render time (Pixabay). Without an
    # API key — or on any fetch failure — this is a no-op and the EDL
    # renders exactly as before; B-roll must never cost the clip.
    try:
        from .pixabay_broll import resolve_edl_broll

        resolve_edl_broll(edl)
    except Exception as exc:
        logger.warning("B-roll resolution skipped: %s", exc)
    extra_inputs, graph, encoder_args, video_label = build_edl_filter_graph(edl, input_path, subtitle_ass_path, fonts_dir)
    plain_command = ["ffmpeg", "-y", "-i", str(input_path), *extra_inputs, "-filter_complex", graph, "-map", video_label, "-map", "0:a?", *encoder_args, *build_audio_output_args(True), "-shortest", "-movflags", "+faststart", str(output_path)]
    audio_plan = build_edl_audio_graph(edl, 1 + extra_inputs.count("-i"), assets_dir)
    logger.info("Rendering EDL clip: %s", output_path)
    if audio_plan is None:
        return _run_render_command(plain_command)
    audio_inputs, audio_graph, audio_label = audio_plan
    command = ["ffmpeg", "-y", "-i", str(input_path), *extra_inputs, *audio_inputs, "-filter_complex", f"{graph};{audio_graph}", "-map", video_label, "-map", audio_label, *encoder_args, *build_audio_output_args(True, loudnorm=False), "-shortest", "-movflags", "+faststart", str(output_path)]
    if _run_render_command(command):
        return True
    # Audio finishing must never cost the clip itself: fall back to the
    # long-standing plain audio path (e.g. a source with no audio stream).
    logger.warning("EDL audio finishing failed for %s; retrying with plain audio path", output_path)
    return _run_render_command(plain_command)
