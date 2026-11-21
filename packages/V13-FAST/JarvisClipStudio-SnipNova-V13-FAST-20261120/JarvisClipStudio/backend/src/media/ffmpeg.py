"""Ffmpeg helpers for the video pipeline."""

from typing import Callable
from typing import List
from typing import Optional
from pathlib import Path
from typing import Tuple
from ..clip_source_map import normalize_source_ranges
from ..config import get_config
from functools import lru_cache
import json
import os
import re
import shutil
import signal
import subprocess
import tempfile
from ..font_registry import FONTS_DIR
from .common import (
    AUDIO_BITRATE,
    CPU_SPEED_CRF,
    CPU_SPEED_PRESET,
    CROSSFADE_MAX_SECONDS,
    FINAL_VIDEO_CRF,
    FINAL_VIDEO_NVENC_CQ_OFFSET,
    FINAL_VIDEO_NVENC_PRESET,
    FINAL_VIDEO_PRESET,
    INTERMEDIATE_CRF,
    LOUDNORM_FILTER,
    NVENC_BALANCED_CQ,
    NVENC_SPEED_CQ,
    NVENC_SPEED_PRESET,
    OUTPUT_FPS,
    PRESTAGE_NVENC_CQ,
    PRESTAGE_NVENC_PRESET,
    get_render_profile,
    logger,
)


def round_to_even(value: int) -> int:
    """Round integer to nearest even number for H.264 compatibility."""
    return value - (value % 2)


def clamp_even(value: int, minimum: int, maximum: int) -> int:
    """Clamp an integer to an even value within inclusive bounds."""
    if maximum < minimum:
        return round_to_even(minimum)
    return round_to_even(max(minimum, min(value, maximum)))


def describe_abnormal_exit(returncode: int) -> Optional[str]:
    """Plain-words explanation for a process that was killed, else None.

    An ordinary non-zero exit needs no gloss: the process printed its own
    error. A killed process leaves nothing behind -- its stderr simply
    stops mid-stream -- so without this the failure is indistinguishable
    from a corrupt input or a bad filter graph, which is exactly how
    out-of-memory render deaths presented (stderr ending at the subtitle
    engine's startup lines, no error anywhere).
    """
    if returncode < 0:
        signum = -returncode
        try:
            signame = signal.Signals(signum).name
        except ValueError:
            signame = f"signal {signum}"
        description = (
            f"the process was killed by {signame} (signal {signum}) "
            "before it could finish"
        )
        if signum == signal.SIGKILL:
            description += (
                "; a SIGKILL almost always means the machine ran out of "
                "memory and the OS killed the process"
            )
        return description
    if returncode == 137:
        # 128 + SIGKILL, the status observed when the process dies by
        # SIGKILL underneath a shell wrapper that converts the death
        # into an ordinary exit.
        return (
            "the process was killed by SIGKILL (signal 9, reported as "
            "exit status 137) before it could finish; a SIGKILL almost "
            "always means the machine ran out of memory and the OS "
            "killed the process"
        )
    return None


def run_ffmpeg_command(command: List[str], timeout: int = 900) -> subprocess.CompletedProcess:
    """Run ffmpeg/ffprobe and log the exit status and stderr on failure."""
    result = subprocess.run(
        command,
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    if result.returncode != 0:
        logger.error(
            "Command failed (exit status %s): %s\n%s",
            result.returncode,
            " ".join(command),
            result.stderr[-4000:],
        )
        explanation = describe_abnormal_exit(result.returncode)
        if explanation:
            logger.error("%s: %s", command[0], explanation)
    return result


def ffprobe_has_audio(video_path: Path) -> bool:
    result = run_ffmpeg_command(
        [
            "ffprobe",
            "-v",
            "error",
            "-select_streams",
            "a:0",
            "-show_entries",
            "stream=codec_type",
            "-of",
            "csv=p=0",
            str(video_path),
        ],
        timeout=60,
    )
    return result.returncode == 0 and "audio" in result.stdout


# ISO 639-1 → ISO 639-2 (B and T forms) for the languages container tags most
# often carry, so an explicit TRANSCRIPTION_LANGUAGE can pick the matching track.
_ISO_639_2_CODES = {
    "ar": {"ara"}, "bg": {"bul"}, "ca": {"cat"}, "cs": {"ces", "cze"},
    "da": {"dan"}, "de": {"deu", "ger"}, "el": {"ell", "gre"}, "en": {"eng"},
    "es": {"spa"}, "fa": {"fas", "per"}, "fi": {"fin"}, "fr": {"fra", "fre"},
    "he": {"heb"}, "hi": {"hin"}, "hr": {"hrv"}, "hu": {"hun"}, "id": {"ind"},
    "it": {"ita"}, "ja": {"jpn"}, "ko": {"kor"}, "ms": {"msa", "may"},
    "nl": {"nld", "dut"}, "no": {"nor", "nob", "nno"}, "pl": {"pol"},
    "pt": {"por"}, "ro": {"ron", "rum"}, "ru": {"rus"}, "sk": {"slk", "slo"},
    "sv": {"swe"}, "th": {"tha"}, "tr": {"tur"}, "uk": {"ukr"}, "ur": {"urd"},
    "vi": {"vie"}, "zh": {"zho", "chi"},
}
# Commentary, audio-description and dub tracks are never the primary dialogue.
_SECONDARY_AUDIO_DISPOSITIONS = ("comment", "visual_impaired", "hearing_impaired", "dub")


def _audio_language_matches(tag: str, preferred_language: str) -> bool:
    tag = tag.strip().lower()
    base = preferred_language.strip().lower().replace("-", "_").split("_", 1)[0]
    return tag == base or tag in _ISO_639_2_CODES.get(base, set())


def select_audio_stream_index(
    video_path: Path, preferred_language: Optional[str] = None
) -> Optional[int]:
    """Pick the dialogue audio track, as an index for ``0:a:N``, or None if silent.

    Transcription and every render of the source must read the same track;
    ffmpeg's implicit choice (most channels) and ``[0:a]`` (first track) can
    differ on multi-track uploads, desyncing captions from the audio.
    ``preferred_language`` defaults to the configured TRANSCRIPTION_LANGUAGE.
    """
    if preferred_language is None:
        preferred_language = get_config().transcription_language
    result = run_ffmpeg_command(
        [
            "ffprobe",
            "-v",
            "error",
            "-select_streams",
            "a",
            "-show_entries",
            "stream=index:stream_tags=language:stream_disposition",
            "-of",
            "json",
            str(video_path),
        ],
        timeout=60,
    )
    if result.returncode != 0:
        return None
    try:
        streams = json.loads(result.stdout or "{}").get("streams") or []
    except ValueError:
        return None
    if not streams:
        return None

    def rank(item: Tuple[int, dict]) -> Tuple[int, int, int, int]:
        position, stream = item
        disposition = stream.get("disposition") or {}
        language = (stream.get("tags") or {}).get("language") or ""
        language_miss = int(
            bool(preferred_language)
            and not _audio_language_matches(language, preferred_language or "")
        )
        secondary = int(any(disposition.get(key) for key in _SECONDARY_AUDIO_DISPOSITIONS))
        not_primary = int(not (disposition.get("default") or disposition.get("original")))
        return language_miss, secondary, not_primary, position

    return min(enumerate(streams), key=rank)[0]


def probe_video_codec(video_path: Path) -> Optional[str]:
    """Short summary of a file's video stream: ``"av1 1920x1080"``.

    None when ffprobe cannot read the file. The download format string is
    codec-agnostic, so the same pipeline can land on AV1, VP9 or H.264
    depending on what the provider serves; until the codec is logged, the
    decode cost behind a slow run is a guess. Metadata-only: no frame is
    decoded, so this costs milliseconds next to the stages it explains.
    """
    try:
        result = run_ffmpeg_command(
            [
                "ffprobe",
                "-v",
                "error",
                "-select_streams",
                "v:0",
                "-show_entries",
                "stream=codec_name,width,height",
                "-of",
                "json",
                str(video_path),
            ],
            timeout=60,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0:
        return None
    try:
        streams = json.loads(result.stdout or "{}").get("streams") or []
    except (ValueError, AttributeError):
        return None
    if not streams or not streams[0].get("codec_name"):
        return None
    stream = streams[0]
    width, height = stream.get("width"), stream.get("height")
    if width and height:
        return f"{stream['codec_name']} {width}x{height}"
    return str(stream["codec_name"])


def audio_stream_map_args(audio_stream: Optional[int]) -> List[str]:
    """Explicit ``-map`` args for the first video track plus the chosen audio."""
    args = ["-map", "0:v:0"]
    if audio_stream is not None:
        args += ["-map", f"0:a:{audio_stream}"]
    return args


def stream_rotation_degrees(stream: dict) -> int:
    """Display rotation in degrees from a stream's rotate tag or display matrix.

    ``tags.rotate`` wins when it parses. Otherwise the first side-data
    ``rotation`` is used (ffprobe's display-matrix angle). ``0`` means the
    coded frame is already the displayed orientation.
    """
    tags = stream.get("tags") or {}
    raw = tags.get("rotate")
    if raw is not None:
        try:
            return int(round(float(raw)))
        except (TypeError, ValueError):
            pass
    for side in stream.get("side_data_list") or []:
        if not isinstance(side, dict) or "rotation" not in side:
            continue
        try:
            return int(round(float(side["rotation"])))
        except (TypeError, ValueError):
            continue
    return 0


def displayed_video_size(width: int, height: int, rotation: int) -> Tuple[int, int]:
    """Pixel size after a 90° or 270° display rotation.

    Matches ``VideoGeometry.display_width`` / ``display_height`` for square
    pixels. Sample aspect ratio is not applied: crop and scale filters address
    stored pixels, and anamorphic sources are squared only by normalization.
    """
    if abs(rotation) % 180 == 90:
        return height, width
    return width, height


def parse_ffprobe_video_size(payload: object) -> Tuple[int, int]:
    """Displayed size of the first video stream in an ffprobe JSON document.

    Extra streams and side-data entries are ignored. A display-matrix rotation
    of 90° or 270° swaps the coded width and height, because ffmpeg autorotates
    before user filters unless ``-noautorotate`` is set.
    """
    if not isinstance(payload, dict):
        raise ValueError("ffprobe size payload is not an object")
    streams = payload.get("streams")
    if not isinstance(streams, list):
        raise ValueError("ffprobe returned no video stream")
    stream = None
    for candidate in streams:
        if not isinstance(candidate, dict):
            continue
        codec_type = candidate.get("codec_type")
        if codec_type not in (None, "video"):
            continue
        if "width" not in candidate or "height" not in candidate:
            continue
        stream = candidate
        break
    if stream is None:
        raise ValueError("ffprobe returned no video stream")
    try:
        width = int(stream["width"])
        height = int(stream["height"])
    except (TypeError, ValueError) as exc:
        raise ValueError("ffprobe returned invalid video dimensions") from exc
    if width <= 0 or height <= 0:
        raise ValueError("ffprobe returned invalid video dimensions")
    return displayed_video_size(width, height, stream_rotation_degrees(stream))


def ffprobe_video_size(video_path: Path) -> Tuple[int, int]:
    """Displayed pixel size of the first video stream.

    The probe is JSON, not ``csv=s=x``. A stream with side data (a display
    matrix, HDR metadata, SEI) makes that csv writer emit a trailing separator,
    so ``1920x1080`` becomes ``1920x1080x`` and ``split("x", 1)`` leaves the
    height as ``1080x``.

    The returned size is the one ffmpeg filters see. Autorotate is on by
    default and this pipeline never passes ``-noautorotate``, so a phone file
    stored as 1920×1080 with rotation -90 is 1080×1920 inside crop, scale, and
    rawvideo analysis.
    """
    result = run_ffmpeg_command(
        [
            "ffprobe",
            "-v",
            "error",
            "-select_streams",
            "v:0",
            "-show_entries",
            "stream=width,height,codec_type:stream_tags=rotate:stream_side_data=rotation",
            "-of",
            "json",
            str(video_path),
        ],
        timeout=60,
    )
    if result.returncode != 0:
        raise RuntimeError(f"Unable to read video size for {video_path}")
    try:
        payload = json.loads(result.stdout or "")
    except ValueError as exc:
        raise RuntimeError(
            f"Unable to read video size for {video_path}: probe output was not JSON"
        ) from exc
    try:
        return parse_ffprobe_video_size(payload)
    except (TypeError, ValueError) as exc:
        raise RuntimeError(f"Unable to read video size for {video_path}: {exc}") from exc


def ffprobe_duration(video_path: Path) -> float:
    result = run_ffmpeg_command(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "default=noprint_wrappers=1:nokey=1",
            str(video_path),
        ],
        timeout=60,
    )
    if result.returncode != 0:
        raise RuntimeError(f"Unable to read duration for {video_path}")
    try:
        return max(0.0, float(result.stdout.strip()))
    except ValueError as exc:
        raise RuntimeError(f"Invalid duration for {video_path}") from exc


def ffmpeg_escape_filter_value(value: str) -> str:
    """Escape a value for use as a filter option inside an ffmpeg filtergraph.

    ffmpeg unescapes filter arguments twice: once when splitting the
    filtergraph (special: backslash, quote, ``[ ] , ;``) and once when
    splitting a filter's ``key=value:...`` options (special: backslash,
    quote, colon). Escaping for both levels keeps Windows drive colons,
    backslashes, apostrophes and brackets in paths intact.
    """
    option_level = re.sub(r"([\\':])", r"\\\1", str(value))
    return re.sub(r"([\\'\[\],;])", r"\\\1", option_level)


def ffmpeg_escape_filter_path(path: Path) -> str:
    """Escape a filesystem path for use as an ffmpeg filter option value."""
    return ffmpeg_escape_filter_value(str(path))


# ---------------------------------------------------------------------------
# NVENC availability probe.
#
# The probe is deliberately production-shaped. The previous check (an
# encoder listing plus a single 256x256 frame into the null muxer) passed
# on machines where every real 1080x1920 graph encode then failed -- and
# when it failed itself, nothing recorded why. The composed check keeps the
# two cheap stages as pre-filters and adds a realistic stage: the
# production graph skeleton (per-segment normalisation, a punch-in zoompan,
# the fps/settb timebase normalisation, an xfade join, a real libass
# subtitles burn, loudnorm) at full output resolution with the encoder
# arguments the EDL path actually uses, into a real faststart MP4 that
# ffprobe then validates. Whichever stage fails, its stderr tail is kept
# for last_nvenc_probe_error() and logged: the probe never fails silently.
# ---------------------------------------------------------------------------

NVENC_PROBE_TIMEOUT_SEC = 120
NVENC_PROBE_WIDTH = 1080
NVENC_PROBE_HEIGHT = 1920
NVENC_PROBE_DURATION_SEC = 5.6
NVENC_PROBE_DURATION_TOLERANCE_SEC = 0.3

_LAST_NVENC_PROBE_ERROR: Optional[str] = None
_NVENC_DISABLED_REASON: Optional[str] = None


def _result_stderr(result: subprocess.CompletedProcess) -> str:
    # CompletedProcess.stderr can be None when the caller captured nothing.
    return getattr(result, "stderr", None) or ""


def _record_nvenc_probe_error(stage: str, detail: str) -> None:
    global _LAST_NVENC_PROBE_ERROR
    tail = (detail or "").strip()[-500:]
    _LAST_NVENC_PROBE_ERROR = f"NVENC probe failed at the {stage} stage"
    if tail:
        _LAST_NVENC_PROBE_ERROR += f": {tail}"
    logger.warning("%s", _LAST_NVENC_PROBE_ERROR)


def last_nvenc_probe_error() -> Optional[str]:
    """Why NVENC is unavailable, in plain words, or None when it probed OK.

    Once disable_nvenc() has taken the encoder away, this returns that
    reason instead: it is the more useful diagnosis.
    """
    if _NVENC_DISABLED_REASON is not None:
        return _NVENC_DISABLED_REASON
    return _LAST_NVENC_PROBE_ERROR


def disable_nvenc(reason: str) -> None:
    """Take NVENC away for the rest of this process.

    A probe that passed can still be wrong for real encodes; when a
    production encode proves it, the render path calls this so every later
    encoder choice in the process goes to libx264 instead of repeating the
    same death. There is deliberately no re-enable: the verdict is cached
    per process, and the next process re-probes from scratch.
    """
    global _NVENC_DISABLED_REASON
    _NVENC_DISABLED_REASON = reason
    check_nvenc_available.cache_clear()
    logger.error("NVENC disabled for this process: %s", reason)


def _reset_nvenc_probe_state() -> None:
    """Clear the probe cache, the recorded error and the disable flag.

    Test support: production code never re-enables NVENC mid-process.
    """
    global _LAST_NVENC_PROBE_ERROR, _NVENC_DISABLED_REASON
    _LAST_NVENC_PROBE_ERROR = None
    _NVENC_DISABLED_REASON = None
    check_nvenc_available.cache_clear()


def _nvenc_encoder_listed() -> bool:
    """Probe stage 1: this FFmpeg build must list h264_nvenc."""
    try:
        result = run_ffmpeg_command(["ffmpeg", "-hide_banner", "-encoders"], timeout=30)
    except (OSError, subprocess.TimeoutExpired) as exc:
        _record_nvenc_probe_error("encoder listing", f"ffmpeg could not run: {exc}")
        return False
    if result.returncode != 0:
        _record_nvenc_probe_error("encoder listing", _result_stderr(result))
        return False
    if "h264_nvenc" not in result.stdout:
        _record_nvenc_probe_error(
            "encoder listing", "h264_nvenc is not in the ffmpeg encoder list"
        )
        return False
    return True


def _nvenc_one_frame_ok() -> bool:
    """Probe stage 2: the original cheap check, one frame to the null muxer.

    Some FFmpeg builds advertise h264_nvenc even when the host has no
    usable GPU, so a listing alone is not enough. The frame must clear
    NVENC's minimum supported dimensions (176x144 on current drivers): a
    smaller frame fails with "Frame Dimension less than the minimum
    supported value" even on a healthy GPU, which silently disabled GPU
    encoding for every render. Passing this stage still proves almost
    nothing about production encodes -- that is what the realistic stage
    is for.
    """
    try:
        probe = run_ffmpeg_command(
            [
                "ffmpeg", "-hide_banner", "-loglevel", "error", "-f", "lavfi",
                "-i", "color=size=256x256:rate=1", "-frames:v", "1",
                "-c:v", "h264_nvenc", "-f", "null", "-",
            ],
            timeout=30,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        _record_nvenc_probe_error("one-frame encode", f"ffmpeg could not run: {exc}")
        return False
    if probe.returncode != 0:
        _record_nvenc_probe_error("one-frame encode", _result_stderr(probe))
        return False
    return True


_NVENC_PROBE_ASS = """[Script Info]
Title: NVENC realistic probe
ScriptType: v4.00+
PlayResX: 1080
PlayResY: 1920
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,Arial,64,&H00FFFFFF,&H0000FFFF,&H00000000,&H64000000,-1,0,0,0,100,100,0,0,1,3,0,2,40,40,90,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
Dialogue: 0,0:00:00.20,0:00:02.80,Default,,0,0,0,,PROBE CAPTION ONE
Dialogue: 0,0:00:03.00,0:00:05.40,Default,,0,0,0,,PROBE CAPTION TWO
"""


def _nvenc_probe_graph(ass_path: Path, fonts_dir: Optional[Path]) -> str:
    """Filter graph for the realistic probe: the production skeleton.

    Mirrors build_edl_filter_graph for a two-segment clip -- per-segment
    trim/fps/scale/crop normalisation, a punch-in zoompan on the first
    segment, the fps/settb timebase normalisation, an xfade join, the
    libass subtitles burn -- plus the finished-audio skeleton (trim,
    48 kHz normalise, loudnorm). 3.2s + 2.8s joined by a 0.4s fade gives
    the expected 5.6s output.
    """
    subtitles = subtitles_filter_fragment(ass_path, fonts_dir)
    fps = OUTPUT_FPS
    return ";".join(
        [
            "[0:v]split=2[src0][src1]",
            f"[src0]trim=start=0:end=3.2,setpts=PTS-STARTPTS,fps={fps},"
            "scale=1080:1920:force_original_aspect_ratio=increase,"
            "crop=1080:1920,setsar=1,format=yuv420p[seg0]",
            f"[src1]trim=start=3.2:end=6,setpts=PTS-STARTPTS,fps={fps},"
            "scale=1080:1920:force_original_aspect_ratio=increase,"
            "crop=1080:1920,setsar=1,format=yuv420p[seg1]",
            "[seg0]zoompan=z='if(lt(on,0),1,if(lt(on,15),1.250000,1))'"
            ":x='(iw-iw/zoom)/2':y='(ih-ih/zoom)/2':d=1:s=1080x1920"
            f":fps={fps}[zoom0]",
            f"[zoom0]fps={fps},settb=AVTB[norm0]",
            f"[seg1]fps={fps},settb=AVTB[norm1]",
            "[norm0][norm1]xfade=transition=fade:duration=0.400000"
            ":offset=2.800000[join0]",
            f"[join0]{subtitles}[finalv]",
            "[1:a]atrim=0:5.6,asetpts=PTS-STARTPTS,aresample=48000,"
            "aformat=sample_fmts=fltp:sample_rates=48000"
            f":channel_layouts=stereo,{LOUDNORM_FILTER}[aout]",
        ]
    )


def _validate_nvenc_probe_output(output_path: Path) -> bool:
    """ffprobe verdict on the realistic probe's MP4 (probe stage 3b)."""
    try:
        result = run_ffmpeg_command(
            [
                "ffprobe", "-v", "error",
                "-show_entries", "stream=codec_type,codec_name,width,height",
                "-show_entries", "format=duration",
                "-of", "json", str(output_path),
            ],
            timeout=30,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        _record_nvenc_probe_error("output validation", f"ffprobe could not run: {exc}")
        return False
    if result.returncode != 0:
        _record_nvenc_probe_error("output validation", _result_stderr(result))
        return False
    try:
        payload = json.loads(result.stdout or "")
        streams = payload.get("streams") or []
        duration = float((payload.get("format") or {}).get("duration"))
    except (ValueError, TypeError, AttributeError):
        _record_nvenc_probe_error(
            "output validation", "ffprobe output was not the expected JSON"
        )
        return False
    video = next((s for s in streams if s.get("codec_type") == "video"), None)
    audio = next((s for s in streams if s.get("codec_type") == "audio"), None)
    if (
        video is None
        or video.get("codec_name") != "h264"
        or int(video.get("width") or 0) != NVENC_PROBE_WIDTH
        or int(video.get("height") or 0) != NVENC_PROBE_HEIGHT
    ):
        _record_nvenc_probe_error(
            "output validation",
            f"expected an h264 {NVENC_PROBE_WIDTH}x{NVENC_PROBE_HEIGHT} "
            f"video stream, got {video!r}",
        )
        return False
    if audio is None or audio.get("codec_name") != "aac":
        _record_nvenc_probe_error(
            "output validation", f"expected an aac audio stream, got {audio!r}"
        )
        return False
    if abs(duration - NVENC_PROBE_DURATION_SEC) > NVENC_PROBE_DURATION_TOLERANCE_SEC:
        _record_nvenc_probe_error(
            "output validation",
            f"expected a {NVENC_PROBE_DURATION_SEC:.1f}s clip, got {duration:.3f}s",
        )
        return False
    return True


def check_nvenc_realistic() -> bool:
    """Probe stage 3: encode a short production-shaped clip with NVENC.

    The graph mirrors the real EDL render at full 1080x1920 output, the
    encode uses build_nvenc_edl_encode_args() -- the exact arguments the
    EDL path renders with -- and the result is a real faststart MP4 that
    _validate_nvenc_probe_output checks. A pass is therefore evidence
    about the encode production will run; the earlier stages cannot say
    that. Unlike check_nvenc_available this measurement is not cached:
    direct callers (diagnostics, tests) get a fresh run each time.
    """
    fonts_dir = FONTS_DIR if FONTS_DIR.is_dir() else None
    try:
        with tempfile.TemporaryDirectory(prefix="nvenc-probe-") as tmp:
            tmp_dir = Path(tmp)
            ass_path = tmp_dir / "probe.ass"
            ass_path.write_text(_NVENC_PROBE_ASS, encoding="utf-8")
            output_path = tmp_dir / "nvenc_probe.mp4"
            # FFmpeg 8-era builds removed -filter_complex_script, so a
            # script-based probe failed with "Unrecognized option" and every
            # render fell back to the CPU encoder. Production already passes
            # the graph inline, and the probe graph is short: do the same.
            command = [
                "ffmpeg", "-y", "-hide_banner", "-loglevel", "info",
                "-f", "lavfi", "-i",
                f"testsrc2=size=1920x1080:rate={OUTPUT_FPS}:duration=6",
                "-f", "lavfi", "-i",
                "sine=frequency=440:sample_rate=48000:duration=6",
                "-filter_complex", _nvenc_probe_graph(ass_path, fonts_dir),
                "-map", "[finalv]", "-map", "[aout]",
                *build_nvenc_edl_encode_args(),
                "-c:a", "aac", "-b:a", AUDIO_BITRATE, "-ar", "48000",
                "-shortest", "-movflags", "+faststart", str(output_path),
            ]
            result = run_ffmpeg_command(command, timeout=NVENC_PROBE_TIMEOUT_SEC)
            if result.returncode != 0:
                _record_nvenc_probe_error("realistic encode", _result_stderr(result))
                return False
            # Validate before the temporary directory is cleaned up. The
            # validation used to run after the ``with`` block had already
            # deleted the MP4, so the probe always failed here on a real
            # ffprobe even when the encode itself succeeded.
            return _validate_nvenc_probe_output(output_path)
    except (OSError, subprocess.TimeoutExpired) as exc:
        _record_nvenc_probe_error("realistic encode", f"ffmpeg could not run: {exc}")
        return False


@lru_cache(maxsize=1)
def check_nvenc_available() -> bool:
    """Return whether this host can actually encode production clips with NVENC.

    Three stages, cheapest first: the encoder must be listed, a one-frame
    encode must succeed, and the realistic production-shaped encode
    (check_nvenc_realistic) must produce a valid clip. The verdict is
    cached for the life of the process; any failure is recorded for
    last_nvenc_probe_error() and logged, never silent. A process whose
    NVENC was taken away by disable_nvenc() reports False until restart.
    """
    global _LAST_NVENC_PROBE_ERROR
    if _NVENC_DISABLED_REASON is not None:
        return False
    if not _nvenc_encoder_listed():
        return False
    if not _nvenc_one_frame_ok():
        return False
    if not check_nvenc_realistic():
        return False
    _LAST_NVENC_PROBE_ERROR = None
    return True


# --- NVDEC hardware decode (opt-in A/B; ENABLE_NVDEC) ---------------------
#
# The decode-side mirror of the NVENC machinery above. NVENC earned its
# place by being on by default once probed; NVDEC has not earned that
# yet -- the perf audit's estimate (10-20% off the render loop) is
# unmeasured on the user's hardware, and NVDEC surfaces share the 6 GB
# card with the NVENC sessions and the resident whisper model. So the
# probe exists, the flag defaults OFF, and the A/B verdict belongs to
# the benchmark protocol, not to this code.

NVDEC_PROBE_TIMEOUT_SEC = 120

_LAST_NVDEC_PROBE_ERROR: Optional[str] = None
_NVDEC_DISABLED_REASON: Optional[str] = None


def _record_nvdec_probe_error(stage: str, detail: str) -> None:
    global _LAST_NVDEC_PROBE_ERROR
    tail = (detail or "").strip()[-500:]
    _LAST_NVDEC_PROBE_ERROR = f"NVDEC probe failed at the {stage} stage"
    if tail:
        _LAST_NVDEC_PROBE_ERROR += f": {tail}"
    logger.warning("%s", _LAST_NVDEC_PROBE_ERROR)


def last_nvdec_probe_error() -> Optional[str]:
    """Why NVDEC is unavailable, in plain words, or None when it probed OK.

    Once disable_nvdec() has taken hardware decode away, this returns
    that reason instead: it is the more useful diagnosis.
    """
    if _NVDEC_DISABLED_REASON is not None:
        return _NVDEC_DISABLED_REASON
    return _LAST_NVDEC_PROBE_ERROR


def disable_nvdec(reason: str) -> None:
    """Take NVDEC away for the rest of this process.

    A probe that passed can still be wrong for real decodes; when a
    production render proves it, the render path calls this so every
    later render in the process decodes in software instead of
    repeating the same death. There is deliberately no re-enable: the
    verdict is cached per process, and the next process re-probes.
    """
    global _NVDEC_DISABLED_REASON
    _NVDEC_DISABLED_REASON = reason
    check_nvdec_available.cache_clear()
    logger.error("NVDEC disabled for this process: %s", reason)


def _reset_nvdec_probe_state() -> None:
    """Clear the probe cache, the recorded error and the disable flag.

    Test support: production code never re-enables NVDEC mid-process.
    """
    global _LAST_NVDEC_PROBE_ERROR, _NVDEC_DISABLED_REASON
    _LAST_NVDEC_PROBE_ERROR = None
    _NVDEC_DISABLED_REASON = None
    check_nvdec_available.cache_clear()


def nvdec_enabled() -> bool:
    """ENABLE_NVDEC=true opts renders into NVDEC hardware decode.

    Default OFF: until the on-hardware A/B says otherwise, the render
    command must stay exactly the software-decode command it has
    always been.
    """
    return os.environ.get("ENABLE_NVDEC", "").strip().lower() in {
        "1", "true", "yes", "on",
    }


def _nvdec_hwaccel_listed() -> bool:
    """Probe stage 1: this FFmpeg build must list the cuda hwaccel."""
    try:
        result = run_ffmpeg_command(
            ["ffmpeg", "-hide_banner", "-hwaccels"], timeout=30
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        _record_nvdec_probe_error("hwaccel listing", f"ffmpeg could not run: {exc}")
        return False
    if result.returncode != 0:
        _record_nvdec_probe_error("hwaccel listing", _result_stderr(result))
        return False
    if "cuda" not in (result.stdout or "").split():
        _record_nvdec_probe_error(
            "hwaccel listing", "cuda is not in the ffmpeg hwaccel list"
        )
        return False
    return True


def check_nvdec_realistic() -> bool:
    """Probe stage 2: hardware-decode one frame of a production-shaped file.

    Mirrors check_nvenc_realistic's philosophy -- a listing is not
    evidence. The probe builds a real 1920x1080 H.264 file (the shape
    renders actually open) and decodes a frame with ``-hwaccel cuda``
    and no output-format pin, so frames download to system memory
    automatically: exactly how the CPU filter graph will consume them.
    Not cached: direct callers get a fresh run each time;
    check_nvdec_available caches the composed verdict.
    """
    try:
        with tempfile.TemporaryDirectory(prefix="nvdec-probe-") as tmp:
            source_path = Path(tmp) / "nvdec_probe_src.mp4"
            build = run_ffmpeg_command(
                [
                    "ffmpeg", "-y", "-hide_banner",
                    "-f", "lavfi", "-i",
                    f"testsrc2=size=1920x1080:rate={OUTPUT_FPS}:duration=1",
                    "-c:v", "libx264", "-pix_fmt", "yuv420p",
                    str(source_path),
                ],
                timeout=NVDEC_PROBE_TIMEOUT_SEC,
            )
            if build.returncode != 0:
                _record_nvdec_probe_error(
                    "probe source", _result_stderr(build)
                )
                return False
            decode = run_ffmpeg_command(
                [
                    "ffmpeg", "-y", "-hide_banner",
                    "-hwaccel", "cuda",
                    "-i", str(source_path),
                    "-frames:v", "1",
                    "-f", "null", "-",
                ],
                timeout=NVDEC_PROBE_TIMEOUT_SEC,
            )
    except (OSError, subprocess.TimeoutExpired) as exc:
        _record_nvdec_probe_error("realistic decode", f"ffmpeg could not run: {exc}")
        return False
    if decode.returncode != 0:
        _record_nvdec_probe_error("realistic decode", _result_stderr(decode))
        return False
    return True


@lru_cache(maxsize=1)
def check_nvdec_available() -> bool:
    """Return whether this host can actually hardware-decode with NVDEC.

    Two stages, cheapest first: the cuda hwaccel must be listed, and a
    realistic decode of a production-shaped file must succeed. The
    verdict is cached for the life of the process; any failure is
    recorded for last_nvdec_probe_error() and logged, never silent. A
    process whose NVDEC was taken away by disable_nvdec() reports
    False until restart.
    """
    global _LAST_NVDEC_PROBE_ERROR
    if _NVDEC_DISABLED_REASON is not None:
        return False
    if not _nvdec_hwaccel_listed():
        return False
    if not check_nvdec_realistic():
        return False
    _LAST_NVDEC_PROBE_ERROR = None
    return True


def nvdec_input_args() -> List[str]:
    """ffmpeg input args that hardware-decode the NEXT input, or [].

    [] unless ENABLE_NVDEC is set AND the realistic probe passed --
    this is the single gate every render path consults, so the
    default render command stays byte-identical to the
    software-decode command. With no ``-hwaccel_output_format`` pin,
    decoded frames download to system memory automatically: exactly
    one round trip, and the CPU filter graph (zoompan, xfade, libass)
    needs no changes.
    """
    if not nvdec_enabled():
        return []
    if not check_nvdec_available():
        return []
    return ["-hwaccel", "cuda"]


def build_nvenc_edl_encode_args() -> List[str]:
    """h264_nvenc args for the EDL final encode (the production graph path).

    These are the exact arguments the realistic probe validates with, so a
    passed probe is evidence about the encode the renderer will run. The
    plain final-encode path uses build_nvenc_final_video_encode_args
    instead (same preset/CQ, plus level and rate caps). Under
    RENDER_PROFILE=speed the preset/CQ are the profile's cheaper pair.
    """
    if get_render_profile() == "speed":
        preset, cq = NVENC_SPEED_PRESET, NVENC_SPEED_CQ
    else:
        preset, cq = FINAL_VIDEO_NVENC_PRESET, NVENC_BALANCED_CQ
    return [
        "-c:v", "h264_nvenc",
        "-preset", preset,
        "-rc", "vbr",
        "-cq", str(cq),
        "-pix_fmt", "yuv420p",
        "-profile:v", "high",
    ]


def force_cpu_encode() -> bool:
    """FORCE_CPU_ENCODE=true pins final encodes to libx264 (debugging, CI)."""
    return os.environ.get("FORCE_CPU_ENCODE", "").strip().lower() in {
        "1", "true", "yes", "on",
    }


# --- Eco render mode (RENDER_ECO_MODE) -------------------------------------
#
# A laptop-friendly scheduling mode for the render path. Rendering is the
# one stage that can saturate a whole machine: clips render concurrently
# (clip_render_concurrency), every ffmpeg spawns threads proportional to
# the core count (x264 defaults to 1.5x cores, the filter graph to the
# core count), and nothing runs at reduced priority — so on a laptop the
# desktop starves for the length of a task (measured on the Japan
# fixture: one EDL clip's ffmpeg peaked at ~2.8 GB RSS and one clip alone
# saturated the sandbox's whole CPU quota). Eco mode changes SCHEDULING
# ONLY — never a quality constant, preset, filter graph or encoder
# choice:
#
#   * clip_render_concurrency() resolves to 1 (task_service);
#   * every render-path ffmpeg command is thread-bounded by
#     eco_render_command() below;
#   * the same commands run at lowered priority (nice) where the
#     platform provides it.
#
# With the flag off, the concurrency resolution is untouched and no
# nice prefix is applied — but the thread bound is NOT eco-only: since
# the RTX 4050 performance lane, eco_render_command bounds every
# render command's FILTER-GRAPH threads at render_thread_budget()
# (below), because ffmpeg's unbounded defaults oversubscribe the
# target laptop even for a single serial render. Eco mode tightens
# that bound and adds the nice prefix on top. Scheduling only, in
# both layers.
#
# The bound is filter-graph only, deliberately: an explicit ENCODER
# thread count (the output ``-threads`` option, at ANY value, and
# equally x264's own ``threads=`` parameter) silently breaks
# zoompan-driven zooms on ffmpeg 8.1.2 — the punch-in / push-in zoom
# simply never appears in the exported frames while every other
# stage reports success. Proven by A/B on the trailer-breakdown
# rendered proof (merge 14/15 re-gate, 2026-11-16): identical
# command and filter graph, zoom present with no encoder thread
# setting, absent with ``-threads 1``, ``2`` or ``4`` and with
# ``x264-params threads=4``; ``-filter_threads`` alone is safe.
# Encoder threads therefore stay at the encoder's own default.

# Thread bound for one render's ffmpeg in eco mode. The target machine
# class is a 12-thread laptop CPU: one clip at a time, at most 4 encoder
# threads plus at most 4 filter-graph threads, leaves the desktop clear
# headroom; a larger bound would hand the saturation back, a smaller one
# stretches the final encode for little extra smoothness.
ECO_FFMPEG_THREADS = 4

# nice level for render processes in eco mode: clearly below interactive
# work, above background daemons.
ECO_NICE_LEVEL = 10


def render_eco_mode() -> bool:
    """RENDER_ECO_MODE=true turns on laptop-friendly render scheduling."""
    return os.environ.get("RENDER_ECO_MODE", "").strip().lower() in {
        "1", "true", "yes", "on",
    }


# Per-process thread bounds for render ffmpeg commands.
#
# ffmpeg's own defaults are sized for a dedicated workstation: x264
# spawns 1.5x the logical core count of encoder threads and the filter
# graph one thread per core, PER PROCESS. On the target laptop (6
# cores / 12 threads, shared with the desktop and a 10 GB WSL memory
# cap) one unbounded encode oversubscribes the machine before any
# clip-level concurrency is even considered — the Nov 2026 lag
# evidence (CPU 93%, RAM 99% during renders) is that oversubscription.
# The budget divides the logical cores by the resolved clip-render
# concurrency and clamps to [RENDER_THREAD_MIN, RENDER_THREAD_MAX]:
# enough threads to keep one 1080x1920 encode fed, never enough to
# thrash. Thread counts are scheduling only — they never touch a
# preset, CRF/CQ value or filter graph.
RENDER_THREAD_MIN = 2
RENDER_THREAD_MAX = 8


def _resolved_render_concurrency() -> int:
    """Clip-render concurrency, mirroring task_service.clip_render_concurrency.

    Kept in step with services.task_service (which cannot be imported
    here without a cycle): eco forces 1, an explicit valid
    CLIP_RENDER_CONCURRENCY wins, otherwise 1 when the effective
    encoder is the CPU and 3 when it is NVENC.
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
    if force_cpu_encode() or not check_nvenc_available():
        return 1
    return 3


def render_thread_budget() -> int:
    """ffmpeg thread bound for ONE render process on this machine.

    Eco mode keeps its own tighter bound (ECO_FFMPEG_THREADS): the
    desktop's headroom is the point of the mode. Otherwise the logical
    core count is divided by the resolved render concurrency and
    clamped to [RENDER_THREAD_MIN, RENDER_THREAD_MAX].
    """
    if render_eco_mode():
        return ECO_FFMPEG_THREADS
    cores = os.cpu_count() or 4
    per_process = -(-cores // _resolved_render_concurrency())
    return max(RENDER_THREAD_MIN, min(RENDER_THREAD_MAX, per_process))


def eco_render_command(command: List[str]) -> List[str]:
    """Apply the render scheduling transform to one render-path ffmpeg call.

    Every render command is filter-thread-bounded at
    render_thread_budget() (``-filter_threads``, a global option
    placed before the inputs). The output ``-threads`` option is
    deliberately NEVER added: an explicit encoder thread count
    silently breaks zoompan-driven zooms on ffmpeg 8.1.2 (see the
    note above render_eco_mode's section header). Eco mode
    additionally prefixes ``nice -n`` when the platform provides nice
    (the Linux worker container does; anywhere else the prefix is
    simply skipped) and tightens the bound via the budget function.
    Only ever called with render commands, whose last argument is the
    output path. Encoder, preset, quality and filter arguments are
    never touched.
    """
    if not command or command[0] != "ffmpeg":
        return command
    wrapped = list(command)
    budget = str(render_thread_budget())
    insert_at = 2 if len(wrapped) > 1 and wrapped[1] == "-y" else 1
    wrapped[insert_at:insert_at] = ["-filter_threads", budget]
    if render_eco_mode():
        nice = shutil.which("nice")
        if nice:
            wrapped = [nice, "-n", str(ECO_NICE_LEVEL), *wrapped]
    return wrapped


def render_encoder_diagnostics() -> dict:
    """Encoder facts about this process, for the render-timings stamp.

    Returns ``{"encoder": "h264_nvenc" | "libx264"}`` naming the
    encoder the final-encode builders select in this process right
    now, plus ``"nvenc_probe_error"`` when the NVENC probe failed or
    a production encode took NVENC away mid-process. Before this
    stamp existed the probe verdict lived only in the worker log: a
    machine whose probe failed rendered every clip on the CPU with
    nothing in the app saying so. Observation only: never raises, and
    the verdict it reads is the cached one every encoder decision in
    the process already used.
    """
    diagnostics: dict = {}
    try:
        if not force_cpu_encode() and check_nvenc_available():
            diagnostics["encoder"] = "h264_nvenc"
        else:
            diagnostics["encoder"] = "libx264"
        probe_error = last_nvenc_probe_error()
        if probe_error:
            diagnostics["nvenc_probe_error"] = probe_error
    except Exception:
        pass
    return diagnostics


def select_prestage_video_encoder() -> Tuple[str, List[str]]:
    """Video encoder args for the intermediate / pre-stage stitch.

    The single encoder-selection point for render_source_ranges_ffmpeg
    and render_ranges_crossfade_ffmpeg: h264_nvenc at CQ 18 / preset p5
    when the probe the main encode already consumes says NVENC is
    healthy (and FORCE_CPU_ENCODE is not set), else the libx264
    veryfast / CRF 16 intermediate the stitch has always used. The
    probe verdict is cached per process, so the pre-stage never pays
    for a probe the main encode has not already paid for -- it reuses
    the same check_nvenc_available() result. The filter graph is
    identical either way (the encoder args sit downstream of it), so
    the join anchors -- and A/V sync -- cannot move with the encoder
    choice. Unlike the final encode, the pre-stage does not follow
    RENDER_PROFILE=speed: it is re-encoded by the main pass, so its
    quality is pinned to the CQ 18 point regardless of profile.
    """
    if not force_cpu_encode() and check_nvenc_available():
        logger.info(
            "Pre-stage stitch encoder: h264_nvenc (preset=%s, cq=%s)",
            PRESTAGE_NVENC_PRESET,
            PRESTAGE_NVENC_CQ,
        )
        return "h264_nvenc", [
            "-c:v", "h264_nvenc",
            "-preset", PRESTAGE_NVENC_PRESET,
            "-rc", "vbr",
            "-cq", str(PRESTAGE_NVENC_CQ),
            "-pix_fmt", "yuv420p",
            "-profile:v", "high",
        ]
    logger.info(
        "Pre-stage stitch encoder: libx264 (preset=veryfast, crf=%s)",
        INTERMEDIATE_CRF,
    )
    return "libx264", [
        "-c:v", "libx264",
        "-preset", "veryfast",
        "-crf", str(INTERMEDIATE_CRF),
        "-pix_fmt", "yuv420p",
    ]


# stderr fragments that blame the GPU encoder itself -- its session setup
# or its driver libraries -- rather than the filter graph or the input.
# Only a failure carrying one of these can plausibly be cured by
# re-encoding on libx264; anything else would fail identically, so the
# stitch keeps its fail-fast behaviour. This mirrors
# edl_render._NVENC_FAILURE_MARKERS; ffmpeg.py cannot import from
# edl_render (that module imports from here), so the narrow list is
# stated here too -- keep the two in step.
_PRESTAGE_NVENC_FAILURE_MARKERS = (
    "h264_nvenc",
    "nvenc",
    "cuda",
    "openencodesessionex",
    "libnvidia",
    "error while opening encoder",
)


def _prestage_nvenc_failure_marker(stderr: Optional[str]) -> Optional[str]:
    """The NVENC marker a failed pre-stage encode's stderr carries, or None."""
    if not stderr:
        return None
    text = stderr.lower()
    for marker in _PRESTAGE_NVENC_FAILURE_MARKERS:
        if marker in text:
            return marker
    return None


def _run_prestage_encode(
    build_command: Callable[[List[str]], List[str]],
    encoder_name: str,
    video_args: List[str],
    timeout: int,
) -> bool:
    """Run one intermediate encode, retrying once on CPU when NVENC fails.

    The retry follows the EDL escape-hatch discipline (edl_render.py):
    it fires only when the failure's stderr implicates the encoder
    itself, and it takes NVENC away for the rest of the process
    (disable_nvenc) so later stitches in the same task do not repeat
    the death. A filter-graph or input failure returns as-is -- the
    CPU retry would fail identically, so it is not attempted.
    """
    result = run_ffmpeg_command(eco_render_command(build_command(video_args)), timeout=timeout)
    if result.returncode == 0:
        return True
    if encoder_name != "h264_nvenc":
        return False
    marker = _prestage_nvenc_failure_marker(result.stderr)
    if marker is None:
        return False
    logger.warning(
        "Pre-stage stitch failed on h264_nvenc (ffmpeg output names %r); "
        "disabling NVENC for this process and retrying once with libx264",
        marker,
    )
    disable_nvenc(f"a pre-stage stitch failed with an NVENC error ({marker})")
    _, cpu_args = select_prestage_video_encoder()
    result = run_ffmpeg_command(eco_render_command(build_command(cpu_args)), timeout=timeout)
    return result.returncode == 0


def build_nvenc_final_video_encode_args(
    crf: int = FINAL_VIDEO_CRF,
    fps: int | str = OUTPUT_FPS,
    level: str = "4.1",
) -> List[str]:
    """h264_nvenc args mirroring the libx264 final pass (CFR, H.264 High).

    NVENC has no CRF; its CQ scale runs a few points above x264's CRF for a
    comparable bitrate, hence the offset. NVENC has no GOP-tuning equivalent
    of the x264 params below, so the defaults stand. Under
    RENDER_PROFILE=speed the preset and CQ are the profile's cheaper pair
    and the CRF offset does not apply.
    """
    if get_render_profile() == "speed":
        preset, cq = NVENC_SPEED_PRESET, NVENC_SPEED_CQ
    else:
        preset = FINAL_VIDEO_NVENC_PRESET
        cq = max(0, min(51, crf + FINAL_VIDEO_NVENC_CQ_OFFSET))
    return [
        "-c:v", "h264_nvenc",
        "-preset", preset,
        "-rc", "vbr",
        "-cq", str(cq),
        "-pix_fmt", "yuv420p",
        "-profile:v", "high",
        "-level", level,
        "-r", str(fps),
    ]


def build_final_video_encode_args(
    crf: int = FINAL_VIDEO_CRF,
    preset: str = FINAL_VIDEO_PRESET,
    fps: int | str = OUTPUT_FPS,
    level: str = "4.1",
) -> List[str]:
    """Args for the quality-determining final pass (CFR, H.264 High).

    This is the single encoder-selection point for every final encode in the
    pipeline: h264_nvenc when the probe finds a working NVIDIA GPU, libx264
    otherwise or when FORCE_CPU_ENCODE is set. ``preset`` only applies to
    the libx264 branch. The choice is logged on every call: a render log
    that cannot name its encoder cannot be diagnosed after the fact.

    Under RENDER_PROFILE=speed the libx264 branch ignores the caller's
    ``preset``/``crf`` and uses the profile's veryfast/CRF 21 (the NVENC
    branch applies the profile inside build_nvenc_final_video_encode_args);
    the balanced profile never touches them.

    Level 4.1 covers 1080p30. Original-format output at 60 fps (or 59.94)
    needs 4.2 or x264 rejects the stream.
    """
    profile = get_render_profile()
    if not force_cpu_encode() and check_nvenc_available():
        logger.info("Final encode encoder: h264_nvenc (profile=%s)", profile)
        return build_nvenc_final_video_encode_args(crf=crf, fps=fps, level=level)
    if profile == "speed":
        preset, crf = CPU_SPEED_PRESET, CPU_SPEED_CRF
    logger.info("Final encode encoder: libx264 (profile=%s)", profile)
    return [
        "-c:v", "libx264",
        "-preset", preset,
        "-crf", str(crf),
        "-pix_fmt", "yuv420p",
        "-profile:v", "high",
        "-level", level,
        "-r", str(fps),
        "-x264-params", "keyint=120:min-keyint=30:scenecut=40",
    ]


def cap_video_short_side(video_path: Path, short_side: int) -> bool:
    """Downscale a rendered clip in place so its shorter side is at most
    ``short_side`` pixels. Returns whether the file changed.

    Rendering always happens at 1080 wide so captions and overlays keep their
    layout; plans with a lower output resolution get this final pass.
    """
    width, height = ffprobe_video_size(video_path)
    if min(width, height) <= short_side:
        return False
    scale = short_side / min(width, height)
    target_width = round_to_even(round(width * scale))
    target_height = round_to_even(round(height * scale))
    capped_path = video_path.with_name(f".{video_path.stem}.capped{video_path.suffix}")
    command = [
        "ffmpeg", "-y", "-v", "error",
        "-i", str(video_path),
        "-map", "0:v:0", "-map", "0:a?",
        "-vf", f"scale={target_width}:{target_height}:flags=lanczos,setsar=1",
        *build_final_video_encode_args(),
        "-c:a", "copy",
        "-movflags", "+faststart",
        str(capped_path),
    ]
    result = run_ffmpeg_command(eco_render_command(command))
    if result.returncode != 0 or not capped_path.exists():
        capped_path.unlink(missing_ok=True)
        raise RuntimeError(f"Unable to downscale {video_path}: {result.stderr[-500:]}")
    capped_path.replace(video_path)
    return True


def build_audio_output_args(has_audio: bool, loudnorm: bool = True) -> List[str]:
    """Audio encode args (with optional loudness normalisation) or `-an`."""
    if not has_audio:
        return ["-an"]
    args: List[str] = []
    if loudnorm:
        args += ["-af", LOUDNORM_FILTER]
    args += ["-c:a", "aac", "-b:a", AUDIO_BITRATE, "-ar", "48000"]
    return args


def subtitles_filter_fragment(
    ass_path: Path, fonts_dir: Optional[Path] = None
) -> str:
    """ffmpeg `subtitles` filter fragment burning an ASS file (with fonts dir)."""
    fragment = f"subtitles=filename={ffmpeg_escape_filter_path(ass_path)}"
    if fonts_dir:
        fragment += f":fontsdir={ffmpeg_escape_filter_path(fonts_dir)}"
    return fragment


def crossfade_fade_for_ranges(keep_ranges: List[Tuple[float, float]]) -> float:
    """Crossfade duration render_source_ranges will use, or 0.0 for hard concat.

    A single source of truth so caption timing (which compacts the same ranges)
    stays perfectly in sync with the crossfade-shortened video timeline.
    """
    ranges = normalize_source_ranges(keep_ranges)
    if len(ranges) < 2 or len(ranges) > 8:
        return 0.0
    # A hook teaser plays ranges out of source order; hard-cut out of the cold
    # open so persisted source maps (which assume concatenation) stay exact.
    if any(ranges[i + 1][0] < ranges[i][0] for i in range(len(ranges) - 1)):
        return 0.0
    durations = [end - start for start, end in ranges]
    if min(durations) < 0.45:
        return 0.0
    fade = min(CROSSFADE_MAX_SECONDS, min(durations) * 0.5)
    return fade if fade >= 0.06 else 0.0


def output_offsets_for_ranges(keep_ranges: List[Tuple[float, float]]) -> List[float]:
    """Where each kept range starts on the rendered clip timeline.

    Each crossfade overlaps the two neighbouring ranges, so every junction
    pulls the following ranges ``fade`` seconds earlier. Captions, headline
    timing and stored durations all read the timeline through this mapping.
    """
    ranges = normalize_source_ranges(keep_ranges)
    fade = crossfade_fade_for_ranges(ranges)
    offsets: List[float] = []
    cursor = 0.0
    for index, (start, end) in enumerate(ranges):
        if index > 0:
            cursor -= fade
        offsets.append(cursor)
        cursor += end - start
    return offsets


def rendered_ranges_duration(keep_ranges: List[Tuple[float, float]]) -> float:
    """Length of the clip render_source_ranges_ffmpeg produces for these ranges."""
    ranges = normalize_source_ranges(keep_ranges)
    if not ranges:
        return 0.0
    offsets = output_offsets_for_ranges(ranges)
    return max(0.0, offsets[-1] + ranges[-1][1] - ranges[-1][0])


def rendered_clip_duration(
    clip_path: Path, keep_ranges: List[Tuple[float, float]]
) -> float:
    """Playback length of a finished clip, probed from the file when possible.

    Teasers, crossfades, frame rounding and resolution caps all change the
    final length, so the file is the authority; the range mapping is only the
    fallback when the file cannot be probed.
    """
    try:
        clip_path = Path(clip_path)
        probed = ffprobe_duration(clip_path) if clip_path.exists() else 0.0
    except Exception:
        probed = 0.0
    return probed if probed > 0 else rendered_ranges_duration(keep_ranges)


def render_ranges_crossfade_ffmpeg(
    video_path: Path,
    keep_ranges: List[Tuple[float, float]],
    output_path: Path,
    audio_stream: Optional[int],
    transition: str = "fade",
    output_offsets: Optional[List[float]] = None,
    audio_path: Optional[Path] = None,
) -> bool:
    """Stitch kept ranges together with short crossfades instead of hard cuts.

    Turns the abrupt jump cuts left by pause/filler removal into quick, smooth
    dissolves (video xfade + audio acrossfade), which read as intentional,
    polished transitions.

    ``audio_path`` (voice/music separation, opt-in tasks only) names a
    replacement audio source that is timeline-identical to ``video_path``
    — the separated vocals stem. The video still comes from
    ``video_path``; only the audio fragments read the second input, which
    receives the same seek window so the relative trims agree. ``None``
    (every historical caller) builds the command exactly as before.
    """
    keep_ranges = normalize_source_ranges(keep_ranges)
    n = len(keep_ranges)
    if n < 2:
        return False
    durations = [end - start for start, end in keep_ranges]
    fade = crossfade_fade_for_ranges(keep_ranges)
    if output_offsets is not None:
        fades = [max(0.0, output_offsets[i-1] + durations[i-1] - output_offsets[i]) for i in range(1, n)]
    else:
        fades = [fade] * (n - 1)
    if not any(fades):
        return False
    has_audio = audio_stream is not None
    # A replacement audio source only exists when the source itself has
    # audio to replace; it becomes input 1, windowed exactly like input 0.
    stem_audio = audio_path if (audio_path is not None and has_audio) else None
    audio_label = f"[0:a:{audio_stream}]" if stem_audio is None else "[1:a]"
    seek_args, origin = _input_window_for_ranges(keep_ranges)

    parts: List[str] = []
    for idx, (start, end) in enumerate(keep_ranges):
        rel_start, rel_end = start - origin, end - origin
        parts.append(
            f"[0:v]trim=start={rel_start:.3f}:end={rel_end:.3f},setpts=PTS-STARTPTS,"
            f"fps={OUTPUT_FPS},format=yuv420p,setsar=1[v{idx}]"
        )
        if has_audio:
            parts.append(
                f"{audio_label}atrim=start={rel_start:.3f}:end={rel_end:.3f},asetpts=PTS-STARTPTS[a{idx}]"
            )

    cur_v = "[v0]"
    cumulative = durations[0]
    for i in range(1, n):
        fade = fades[i-1]
        offset = cumulative - fade
        out = f"[vx{i}]"
        parts.append(f"{cur_v}[v{i}]concat=n=2:v=1:a=0{out}" if fade <= 1e-6 else
            f"{cur_v}[v{i}]xfade=transition={transition}:duration={fade:.6f}:offset={offset:.6f}{out}")
        cumulative = cumulative + durations[i] - fade
        cur_v = out

    map_args = ["-map", cur_v]
    if has_audio:
        cur_a = "[a0]"
        for i in range(1, n):
            out = f"[ax{i}]"
            fade = fades[i-1]
            parts.append(f"{cur_a}[a{i}]concat=n=2:v=0:a=1{out}" if fade <= 1e-6 else f"{cur_a}[a{i}]acrossfade=d={fade:.6f}{out}")
            cur_a = out
        map_args += ["-map", cur_a]

    encoder_name, video_args = select_prestage_video_encoder()

    def _build(video_encode_args: List[str]) -> List[str]:
        command = [
            "ffmpeg", "-y", *seek_args, *nvdec_input_args(), "-i", str(video_path),
            *([*seek_args, "-i", str(stem_audio)] if stem_audio is not None else []),
            "-filter_complex", ";".join(parts),
            *map_args,
            *video_encode_args,
        ]
        if has_audio:
            command += ["-c:a", "aac", "-b:a", "192k"]
        command += ["-movflags", "+faststart", str(output_path)]
        return command

    return _run_prestage_encode(_build, encoder_name, video_args, timeout=1800)


def _input_window_for_ranges(
    keep_ranges: List[Tuple[float, float]],
) -> Tuple[List[str], float]:
    """Seek to the first kept moment and stop just after the last one.

    Multi-range trims used to open the source at t=0, so a 40s clip taken from
    minute 30 decoded the whole prefix. Timestamps after `-ss` (before `-i`)
    start at 0, so callers subtract the returned origin from every trim.
    """
    origin = min(start for start, _ in keep_ranges)
    end = max(end for _, end in keep_ranges)
    # A few dozen milliseconds at the head is not worth a seek. Shifting trims
    # without seeking would start each piece early, so leave those timestamps
    # absolute.
    if origin <= 0.05:
        origin = 0.0
    args: List[str] = []
    if origin > 0:
        args.extend(["-ss", f"{origin:.3f}"])
    args.extend(["-t", f"{max(0.2, end - origin + 1.0):.3f}"])
    return args, origin


def render_source_ranges_ffmpeg(
    video_path: Path,
    keep_ranges: List[Tuple[float, float]],
    output_path: Path,
    output_offsets: Optional[List[float]] = None,
    audio_path: Optional[Path] = None,
) -> bool:
    """Render source ranges into one intermediate clip using ffmpeg only.

    ``audio_path`` (voice/music separation, opt-in tasks only) names a
    replacement audio source that is timeline-identical to ``video_path``
    — the separated vocals stem. The intermediate then carries the stem's
    audio under the source's picture, so every downstream render stage
    hears clean speech. A missing/unreadable stem file is ignored with a
    warning: the stitch must never fail because of separation. ``None``
    (every historical caller) builds the commands exactly as before.
    """
    keep_ranges = normalize_source_ranges(keep_ranges)
    if not keep_ranges:
        return False

    audio_stream = select_audio_stream_index(video_path)
    has_audio = audio_stream is not None

    stem_audio: Optional[Path] = None
    if audio_path is not None and has_audio:
        candidate = Path(audio_path)
        if candidate.is_file():
            stem_audio = candidate
        else:
            logger.warning(
                "Replacement audio source %s is missing; stitching the "
                "source's own audio",
                candidate,
            )

    if len(keep_ranges) == 1:
        start, end = keep_ranges[0]
        encoder_name, video_args = select_prestage_video_encoder()

        def _build_single(video_encode_args: List[str]) -> List[str]:
            if stem_audio is not None:
                return [
                    "ffmpeg",
                    "-y",
                    "-ss",
                    f"{start:.3f}",
                    *nvdec_input_args(),
                    "-i",
                    str(video_path),
                    "-ss",
                    f"{start:.3f}",
                    "-i",
                    str(stem_audio),
                    "-map",
                    "0:v:0",
                    "-map",
                    "1:a:0",
                    "-t",
                    f"{end - start:.3f}",
                    *video_encode_args,
                    "-c:a",
                    "aac",
                    "-b:a",
                    "192k",
                    "-movflags",
                    "+faststart",
                    str(output_path),
                ]
            return [
                "ffmpeg",
                "-y",
                "-ss",
                f"{start:.3f}",
                *nvdec_input_args(),
                "-i",
                str(video_path),
                *audio_stream_map_args(audio_stream),
                "-t",
                f"{end - start:.3f}",
                *video_encode_args,
                "-c:a",
                "aac",
                "-b:a",
                "192k",
                "-movflags",
                "+faststart",
                str(output_path),
            ]

        return _run_prestage_encode(_build_single, encoder_name, video_args, timeout=900)

    # Smooth a handful of substantial internal cuts with crossfades; fall back to
    # a hard concat for many tiny fragments (heavy filler edits) or on failure.
    if output_offsets is not None or crossfade_fade_for_ranges(keep_ranges) > 0:
        if render_ranges_crossfade_ffmpeg(
            video_path, keep_ranges, output_path, audio_stream, output_offsets=output_offsets,
            audio_path=stem_audio,
        ):
            return True
        logger.info("Crossfade stitch failed; falling back to hard concat")

    seek_args, origin = _input_window_for_ranges(keep_ranges)
    audio_label = f"[0:a:{audio_stream}]" if stem_audio is None else "[1:a]"
    filter_parts: List[str] = []
    concat_inputs: List[str] = []
    for idx, (start, end) in enumerate(keep_ranges):
        rel_start, rel_end = start - origin, end - origin
        filter_parts.append(
            f"[0:v]trim=start={rel_start:.3f}:end={rel_end:.3f},setpts=PTS-STARTPTS[v{idx}]"
        )
        concat_inputs.append(f"[v{idx}]")
        if has_audio:
            filter_parts.append(
                f"{audio_label}atrim=start={rel_start:.3f}:end={rel_end:.3f},asetpts=PTS-STARTPTS[a{idx}]"
            )
            concat_inputs.append(f"[a{idx}]")

    if has_audio:
        filter_parts.append(
            f"{''.join(concat_inputs)}concat=n={len(keep_ranges)}:v=1:a=1[v][a]"
        )
        map_args = ["-map", "[v]", "-map", "[a]"]
    else:
        filter_parts.append(
            f"{''.join(concat_inputs)}concat=n={len(keep_ranges)}:v=1:a=0[v]"
        )
        map_args = ["-map", "[v]"]

    encoder_name, video_args = select_prestage_video_encoder()

    def _build_concat(video_encode_args: List[str]) -> List[str]:
        command = [
            "ffmpeg",
            "-y",
            *seek_args,
            *nvdec_input_args(),
            "-i",
            str(video_path),
            *([*seek_args, "-i", str(stem_audio)] if stem_audio is not None else []),
            "-filter_complex",
            ";".join(filter_parts),
            *map_args,
            *video_encode_args,
        ]
        if has_audio:
            command.extend(["-c:a", "aac", "-b:a", "192k"])
        command.extend(["-movflags", "+faststart", str(output_path)])
        return command

    return _run_prestage_encode(_build_concat, encoder_name, video_args, timeout=1800)


def burn_ass_subtitles_ffmpeg(
    input_path: Path,
    ass_path: Path,
    output_path: Path,
    fonts_dir: Optional[Path] = None,
) -> bool:
    video_filter = f"{subtitles_filter_fragment(ass_path, fonts_dir)},setsar=1"

    command = [
        "ffmpeg",
        "-y",
        "-i",
        str(input_path),
        "-vf",
        video_filter,
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
    return run_ffmpeg_command(command).returncode == 0
