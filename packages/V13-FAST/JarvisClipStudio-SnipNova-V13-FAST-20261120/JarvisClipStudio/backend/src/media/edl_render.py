"""FFmpeg execution of auto-director edit decision lists."""

import hashlib
import os
import subprocess
import tempfile
import textwrap
from pathlib import Path
from time import perf_counter
from typing import Any, Optional

from .common import LOUDNORM_FILTER, OUTPUT_FPS, UPSCALE_SHARPEN_FILTER, get_render_profile, logger, record_pass_timing
from .ffmpeg import build_audio_output_args, build_final_video_encode_args, build_nvenc_edl_encode_args, check_nvenc_available, crossfade_fade_for_ranges, disable_nvdec, disable_nvenc, eco_render_command, ffmpeg_escape_filter_path, ffprobe_has_audio, ffprobe_video_size, force_cpu_encode, nvdec_input_args, render_encoder_diagnostics, render_source_ranges_ffmpeg, run_ffmpeg_command, subtitles_filter_fragment

# The full FFmpeg xfade catalog: 58 named transitions (verified against the
# xfade filter documentation). Each name maps to itself, so any of them can
# be used directly as an EDL/profile transition value. The legacy profile
# names keep their long-standing targets below. A transition name missing
# from TRANSITION_MAP (including xfade's expression-only "custom") resolves
# to None and degrades safely to a hard cut in the join loop.
# Picture-in-picture B-roll inset (Smart B-Roll v3): the largest box the stock
# shot may fill inside the 1080x1920 frame, the white frame width, and where
# the inset's bottom edge sits as a fraction of the height (captions start
# around 0.75, so the inset ends above them).
BROLL_PIP_MAX_W = 692
BROLL_PIP_MAX_H = 560
BROLL_PIP_BORDER = 8
BROLL_PIP_BOTTOM_FRAC = 0.70

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
AUDIO_FILE_EXTENSIONS = (".mp3", ".wav", ".m4a", ".aac", ".ogg", ".flac", ".opus", ".webm")
# Every stream entering the audio-finishing mix is normalised to this format
# so amix/sidechaincompress always see matching sample rate and layout.
_AUDIO_NORMALIZE = "aresample=48000,aformat=sample_fmts=fltp:sample_rates=48000:channel_layouts=stereo"


# The NVENC probe lives in .ffmpeg (check_nvenc_available, imported above):
# a single source for every encoder decision in the process. This module
# used to carry a second copy; two cached verdicts evaluated at different
# moments could disagree, locking one decision to CPU and another to NVENC
# inside the same task.

# CPU fallback for the EDL final encode. The shared defaults behind
# build_final_video_encode_args (libx264 preset medium, CRF 19) are a
# quality-first setting that costs roughly 2x the wall time of veryfast/CRF 20
# per clip — and several clips render concurrently, dividing the same cores.
# When NVENC is unavailable the EDL path therefore encodes veryfast/CRF 20;
# the NVENC arguments above are unchanged, and non-EDL final encodes keep
# the shared defaults.
CPU_FALLBACK_PRESET = "veryfast"
CPU_FALLBACK_CRF = 20


def get_video_encoder(use_nvenc: bool = True) -> tuple[str, list[str]]:
    # FORCE_CPU_ENCODE pins the EDL final encode to libx264 as well: the
    # shared kill-switch in ffmpeg.force_cpu_encode previously only covered
    # the plain render path, so there was no way to take NVENC out of an
    # EDL render while debugging a failing clip.
    if use_nvenc and not force_cpu_encode() and check_nvenc_available():
        return "h264_nvenc", build_nvenc_edl_encode_args()
    return "libx264", build_final_video_encode_args(crf=CPU_FALLBACK_CRF, preset=CPU_FALLBACK_PRESET)


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


INFO_CARD_LINE_WIDTH = 34
INFO_CARD_MAX_LINES = 4


def short_info_card_text(text: str) -> str:
    """Cap info-card text so its drawtext box stays on the frame.

    Cards render at fontsize 48 centred on a 1080-wide frame; an
    unbounded verbatim quote ran the box past both edges. Wrapping is
    word-preserving — the card still shows the quote exactly, only
    broken into lines — except for pathological unbroken input, which
    is hard-capped at MAX_LINES lines so the card can never cover the
    frame.
    """
    normalized = " ".join(str(text).split())
    if not normalized:
        return ""
    lines = textwrap.wrap(
        normalized,
        width=INFO_CARD_LINE_WIDTH,
        break_long_words=True,
        break_on_hyphens=False,
    )
    return "\n".join(lines[:INFO_CARD_MAX_LINES])


def build_drawtext_filter(
    text: str,
    start_time: float,
    end_time: float,
    input_label: str,
    output_label: str,
    *,
    sidecar_dir: Optional[Path] = None,
    card_index: Optional[int] = None,
) -> str:
    """Drawtext filter drawing ``text`` via a ``textfile=`` sidecar.

    Card/title text is user-derived (verbatim transcript quotes, hook
    titles) and routinely contains apostrophes. An inline ``text='…'``
    value cannot carry one: the filtergraph parser treats a quote as
    structural no matter how it is backslash-escaped (verified against
    ffmpeg 8.1 — an escaped apostrophe breaks the whole graph parse),
    and the older single-level escaping additionally let a comma end
    the value early. drawtext's textfile bypasses graph parsing and
    option splitting entirely; the one transform still applied to the
    file content is escaping ``%`` → ``\\%``, because drawtext runs its
    expansion pass over textfile content as well and a bare % aborts
    the draw (the card silently never appears). (The handoff invariant
    "escaping order \\ → ' → : → %" describes the inline scheme this
    replaces; the textfile makes that class moot — see
    research_notes/sprint-7day/OPENCODE_AUDIT.md.)

    With ``sidecar_dir`` the file lands there under a deterministic
    name — ``card_<index>_<digest>.txt``, the index being the card's
    position within its render when the caller supplies one and the
    digest a hash of the exact file content — and the returned graph
    references it with a fully escaped absolute path, so the caller
    owns cleanup. Deterministic names mean an identical EDL builds a
    byte-identical graph on every call (the previous count-the-files
    naming made each rebuild point at a different card_N.txt), and
    renders sharing a sidecar dir can only ever collide on identical
    content, which is benign. Without one, a fresh temp file is
    created (best-effort unlinked once the render process has
    exited).
    """
    # drawtext applies its expansion pass to textfile content too, and a
    # bare % aborts the draw ("Stray %") leaving an invisible card — and
    # doubling does NOT save it inside a textfile (verified on ffmpeg
    # 8.1: %% still errors). The one escape that works there is \%,
    # which renders as a single literal %. Applied uniformly: text that
    # never contained % is unaffected, and "%{n}"-style input in a
    # verbatim quote stays text, not an expansion request.
    file_text = text.replace("%", r"\%")
    if sidecar_dir is not None:
        sidecar_dir = Path(sidecar_dir)
        sidecar_dir.mkdir(parents=True, exist_ok=True)
        digest = hashlib.sha256(file_text.encode("utf-8")).hexdigest()[:16]
        if card_index is not None:
            filename = f"card_{card_index}_{digest}.txt"
        else:
            filename = f"card_{digest}.txt"
        text_path = sidecar_dir / filename
        text_path.write_text(file_text, encoding="utf-8")
    else:
        fd, temp_name = tempfile.mkstemp(prefix="drawtext_", suffix=".txt")
        text_path = Path(temp_name)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(file_text)

        def _cleanup() -> None:
            # The render command is a child process; it has exited by the
            # time any later graph is built in this worker. Deleting on a
            # timer keeps the file alive for its own render without
            # leaking temp files forever.
            try:
                text_path.unlink(missing_ok=True)
            except OSError:
                pass

        import threading

        threading.Timer(3600.0, _cleanup).start()
    escaped_path = ffmpeg_escape_filter_path(text_path)
    enable = f"between(t\\,{start_time:.3f}\\,{end_time:.3f})"
    return f"{input_label}drawtext=textfile='{escaped_path}':fontsize=48:fontcolor=white:box=1:boxcolor=0x000000AA:boxborderw=10:x=(w-text_w)/2:y=h*0.85:enable='{enable}'{output_label}"


# Blurred full-frame backdrop under reframe fit/split layers, mirroring the
# background half of reframing.build_full_frame_filter (sigma 14). The
# trailing format keeps every segment stream yuv420p like the legacy chain.
_REFRAME_BG_CHAIN = (
    "scale=540:960:force_original_aspect_ratio=increase,crop=540:960,"
    "gblur=sigma=14:steps=2,scale=1080:1920:flags=bilinear,setsar=1,format=yuv420p"
)


def _reframe_intervals(raw: Any) -> list[tuple[float, float]]:
    intervals: list[tuple[float, float]] = []
    for entry in raw or []:
        try:
            start, end = float(entry[0]), float(entry[1])
        except (TypeError, ValueError, IndexError, KeyError):
            continue
        if end > start:
            intervals.append((start, end))
    return intervals


def _reframe_enable_expr(intervals: list[tuple[float, float]]) -> str:
    """Overlay enable expression over segment-local intervals."""
    return "+".join(f"between(t\\,{a:.3f}\\,{b:.3f})" for a, b in intervals) or "0"


def _segment_framing_fragments(
    segment_input: str,
    segment: dict[str, Any],
    start: float,
    end: float,
    name: str,
    next_id: int,
) -> tuple[list[str], str, int]:
    """Framing fragments for one EDL segment -> (fragments, label, next_id).

    A segment without a resolved ``reframe`` entry (attached at EDL build
    time by media.reframe_plan) emits the legacy fixed centre-crop chain
    verbatim, so EDLs without a reframe plan render byte-identically to
    before. With one, the segment is composed in source space from the
    plan's layers: a tracked/pair/static crop chain, full-frame fit layers
    and split-stack tiles over a blurred backdrop, each layer gated to its
    segment-local intervals; zoom keyframes and the fps/settb
    normalisation downstream are untouched.
    """
    legacy = (
        f"{segment_input}trim=start={start:.3f}:end={end:.3f},setpts=PTS-STARTPTS,"
        f"fps={OUTPUT_FPS},scale=1080:1920:force_original_aspect_ratio=increase,"
        f"crop=1080:1920,setsar=1,format=yuv420p{_label(name)}"
    )
    reframe = segment.get("reframe")
    if not isinstance(reframe, dict) or not reframe:
        return [legacy], _label(name), next_id
    crop = reframe.get("crop") if isinstance(reframe.get("crop"), dict) else None
    if crop is not None and not all(
        key in crop for key in ("w", "h", "x_expr", "y")
    ):
        crop = None
    fit_layers = [
        layer
        for layer in reframe.get("fit_layers") or []
        if isinstance(layer, dict) and str(layer.get("chain") or "")
    ]
    tile_keys = ("tile_w", "tile_h", "tile_x", "tile_y")
    split_layers = [
        layer
        for layer in reframe.get("split_layers") or []
        if isinstance(layer, dict)
        and isinstance(layer.get("left"), dict)
        and isinstance(layer.get("right"), dict)
        and all(key in layer["left"] and key in layer["right"] for key in tile_keys)
    ]
    if crop is None and not fit_layers and not split_layers:
        return [legacy], _label(name), next_id
    duration = end - start
    trim = (
        f"{segment_input}trim=start={start:.3f}:end={end:.3f},"
        f"setpts=PTS-STARTPTS,fps={OUTPUT_FPS},"
    )

    def covers(intervals: list[tuple[float, float]]) -> bool:
        return (
            len(intervals) == 1
            and intervals[0][0] <= 0.001
            and intervals[0][1] >= duration - 0.001
        )

    crop_chain = None
    crop_intervals: list[tuple[float, float]] = []
    if crop is not None:
        crop_intervals = _reframe_intervals(crop.get("intervals"))
        crop_chain = (
            f"crop={int(crop['w'])}:{int(crop['h'])}:x='{crop['x_expr']}':"
            f"y={int(crop['y'])},scale=1080:1920:flags=lanczos,setsar=1,format=yuv420p"
        )
    if (
        crop_chain is not None
        and not fit_layers
        and not split_layers
        and covers(crop_intervals)
    ):
        return [trim + crop_chain + _label(name)], _label(name), next_id

    # Compositor: one trimmed copy of the segment per layer branch over a
    # blurred full-frame backdrop (the backdrop also shows through any gap
    # the resolved layers leave, so the segment is never black).
    branch_count = 1 + (1 if crop_chain else 0) + len(fit_layers) + len(split_layers)
    inputs = [_label(f"{name}in{i}") for i in range(branch_count)]
    fragments = [trim + f"split={branch_count}" + "".join(inputs)]
    from .fit_pad import edl_backdrop_chain

    fragments.append(
        f"{inputs[0]}{edl_backdrop_chain(_REFRAME_BG_CHAIN)}{_label(f'{name}bg')}"
    )
    current = _label(f"{name}bg")
    branch = 1
    overlay_total = branch_count - 1
    overlay_done = 0

    def overlay(
        layer_label: str,
        intervals: list[tuple[float, float]],
        position: str = "0:0",
    ) -> None:
        nonlocal current, next_id, overlay_done
        overlay_done += 1
        output = (
            _label(name)
            if overlay_done == overlay_total
            else _label(f"rfx{next_id}")
        )
        if overlay_done != overlay_total:
            next_id += 1
        if covers(intervals):
            fragments.append(f"{current}{layer_label}overlay={position}{output}")
        else:
            enable = _reframe_enable_expr(intervals)
            fragments.append(
                f"{current}{layer_label}overlay={position}:enable='{enable}'{output}"
            )
        current = output

    if crop_chain is not None:
        fragments.append(f"{inputs[branch]}{crop_chain}{_label(f'{name}cr')}")
        overlay(_label(f"{name}cr"), crop_intervals)
        branch += 1
    for position, layer in enumerate(fit_layers):
        chain = str(layer.get("chain") or "")
        layer_label = _label(f"{name}ft{position}")
        fragments.append(f"{inputs[branch]}{chain},format=yuv420p{layer_label}")
        # Fit layers are letterboxed, not full-frame: centre them over the
        # backdrop exactly like the plain path's fit overlays.
        overlay(
            layer_label,
            _reframe_intervals(layer.get("intervals")),
            position="(W-w)/2:(H-h)/2",
        )
        branch += 1
    for position, layer in enumerate(split_layers):
        left, right = layer.get("left") or {}, layer.get("right") or {}
        top_label = _label(f"{name}sp{position}a")
        bottom_label = _label(f"{name}sp{position}b")
        fragments.append(f"{inputs[branch]}split=2{top_label}{bottom_label}")
        branch += 1
        stack_label = _label(f"{name}sp{position}")
        fragments.append(
            f"{top_label}crop={int(left['tile_w'])}:{int(left['tile_h'])}:"
            f"{int(left['tile_x'])}:{int(left['tile_y'])},"
            f"scale=1080:960:flags=lanczos,{UPSCALE_SHARPEN_FILTER},setsar=1"
            f"{_label(f'{name}sp{position}t')}"
        )
        fragments.append(
            f"{bottom_label}crop={int(right['tile_w'])}:{int(right['tile_h'])}:"
            f"{int(right['tile_x'])}:{int(right['tile_y'])},"
            f"scale=1080:960:flags=lanczos,{UPSCALE_SHARPEN_FILTER},setsar=1"
            f"{_label(f'{name}sp{position}u')}"
        )
        fragments.append(
            f"{_label(f'{name}sp{position}t')}{_label(f'{name}sp{position}u')}"
            f"vstack,setsar=1,format=yuv420p{stack_label}"
        )
        overlay(stack_label, _reframe_intervals(layer.get("intervals")))
    return fragments, _label(name), next_id


def _source_ranges(edl: dict[str, Any]) -> list[tuple[float, float]]:
    """Validated (start, end) source-timeline ranges carried by an EDL.

    An EDL's segments tile the clip timeline (the concatenation of the
    keep ranges). When the EDL also carries ``source_ranges`` the renderer
    can build that concatenation in-graph from the original source file
    instead of requiring a pre-rendered intermediate: the trim/concat
    pre-stage reproduces the intermediate's exact timeline.
    """
    ranges: list[tuple[float, float]] = []
    for entry in edl.get("source_ranges") or []:
        try:
            start, end = float(entry[0]), float(entry[1])
        except (TypeError, ValueError, IndexError, KeyError):
            continue
        if end > start >= 0:
            ranges.append((start, end))
    return ranges


def _source_prestage_fragments(
    ranges: list[tuple[float, float]], with_audio: bool
) -> tuple[list[str], str, Optional[str]]:
    """Trim+join pre-stage reproducing the intermediate clip in-graph.

    Returns (fragments, video_label, audio_label). The video label replaces
    ``[0:v]`` as the segment source; the audio label (when requested)
    replaces ``[0:a]`` for the audio-finishing graph.

    The join mirrors render_source_ranges_ffmpeg exactly — same per-range
    normalisation, same crossfade decision (crossfade_fade_for_ranges),
    same fade and offsets — because every timeline consumer (captions,
    the EDL's own segment tiling, stored durations) reads the
    crossfade-compacted timeline the intermediate produces. A plain hard
    concat here would lengthen multi-range clips and desync all of them.

    Note: render_edl_clip_ffmpeg materialises crossfaded multi-range
    EDLs through render_source_ranges_ffmpeg before building the main
    graph (measured: inside the full segment graph this replica's
    xfade placed the following range a full fade late — see
    research_notes/sync-diagnosis/fix-report.md), so in production
    this in-graph form now serves single-range and hard-concat EDLs,
    plus as the fallback when materialisation itself fails.
    """
    fade = crossfade_fade_for_ranges(list(ranges))
    fragments: list[str] = []
    for index, (start, end) in enumerate(ranges):
        fragments.append(
            f"[0:v]trim=start={start:.3f}:end={end:.3f},setpts=PTS-STARTPTS,"
            f"fps={OUTPUT_FPS},format=yuv420p,setsar=1[vpre{index}]"
        )
    if len(ranges) == 1:
        fragments.append("[vpre0]null[srcv]")
    else:
        current = "[vpre0]"
        cumulative = ranges[0][1] - ranges[0][0]
        for index in range(1, len(ranges)):
            output = "[srcv]" if index == len(ranges) - 1 else f"[vjoin{index}]"
            if fade > 0:
                fragments.append(
                    f"{current}[vpre{index}]xfade=transition=fade:"
                    f"duration={fade:.6f}:offset={cumulative - fade:.6f}{output}"
                )
            else:
                fragments.append(f"{current}[vpre{index}]concat=n=2:v=1:a=0{output}")
            cumulative += (ranges[index][1] - ranges[index][0]) - fade
            current = output
    audio_label: Optional[str] = None
    if with_audio:
        audio_fragments, audio_label = _source_audio_prestage_fragments(ranges)
        fragments.extend(audio_fragments)
    return fragments, "[srcv]", audio_label


def _edl_vocals_stem(edl: dict[str, Any]) -> Optional[Path]:
    """The separated vocals stem an opted-in task stamped onto its EDL.

    The stamp is just a path (the same carrier pattern as the voiceover
    file and the Brand Kit watermark): the renderer honours it only
    while the file actually exists, so a stem that was cleaned up — or
    a stamp copied onto an EDL whose source was never separated —
    quietly renders the historical way instead of failing the clip.
    """
    raw = edl.get("vocals_stem")
    if not isinstance(raw, str) or not raw.strip():
        return None
    candidate = Path(raw)
    return candidate if candidate.is_file() else None


def _source_audio_prestage_fragments(
    ranges: list[tuple[float, float]],
    audio_input_label: str = "[0:a]",
) -> tuple[list[str], str]:
    """Audio half of the source pre-stage: join the ranges' audio.

    Mirrors the intermediate's audio stitch (acrossfade when the video
    crossfades, hard concat otherwise) so the compacted timelines agree.
    Kept separate from the video pre-stage because build_edl_filter_graph
    already emits the video half; the render entry point adds this half
    when it assembles the full graph for the audio paths.

    ``audio_input_label`` is the stream the ranges are cut from: the
    source's own audio (``[0:a]``, every historical render) or the
    separated vocals stem riding as an extra input (voice/music
    separation, opt-in tasks). The stem is timeline-identical to the
    source and windowed with the same seek, so the same ranges produce
    the same compacted timeline from either input.
    """
    fade = crossfade_fade_for_ranges(list(ranges))
    fragments: list[str] = []
    for index, (start, end) in enumerate(ranges):
        fragments.append(
            f"{audio_input_label}atrim=start={start:.3f}:end={end:.3f},asetpts=PTS-STARTPTS[apre{index}]"
        )
    if len(ranges) == 1:
        fragments.append("[apre0]anull[srca]")
    else:
        current = "[apre0]"
        for index in range(1, len(ranges)):
            output = "[srca]" if index == len(ranges) - 1 else f"[ajoin{index}]"
            if fade > 0:
                fragments.append(f"{current}[apre{index}]acrossfade=d={fade:.6f}{output}")
            else:
                fragments.append(f"{current}[apre{index}]concat=n=2:v=0:a=1{output}")
            current = output
    return fragments, "[srca]"


def _source_window(
    edl: dict[str, Any],
) -> tuple[list[str], dict[str, Any], list[tuple[float, float]]]:
    """Seek the input to the first kept moment before decoding.

    The EDL's source ranges live on the original timeline, so a clip taken
    from minute 10 used to open the file at t=0 and decode the whole prefix
    through every trim filter. ``-ss`` before ``-i`` starts the input at the
    first kept moment and resets timestamps to zero, so the caller must shift
    every range by the returned origin. Mirrors
    ``ffmpeg._input_window_for_ranges`` (the non-EDL path already does this).
    """
    ranges = _source_ranges(edl)
    if not ranges:
        return [], edl, ranges
    origin = min(start for start, _ in ranges)
    end = max(stop for _, stop in ranges)
    if origin <= 0.05:
        return [], edl, ranges
    shifted = {
        **edl,
        "source_ranges": [
            [start - origin, stop - origin] for start, stop in ranges
        ],
    }
    seek_args = ["-ss", f"{origin:.3f}", "-t", f"{max(0.2, end - origin + 1.0):.3f}"]
    return seek_args, shifted, ranges


def _look_number(block: Any, field: str) -> float:
    """A look value as a float; anything unusable reads as neutral (0)."""
    if not isinstance(block, dict):
        return 0.0
    value = block.get(field)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return 0.0
    return float(value)


def _lut3d_fragment(lut_file: Any) -> Optional[str]:
    """The ``lut3d`` fragment for a resolved grade's imported LUT file.

    The path is single-quoted for the filtergraph (backslashes and
    quotes escaped). A path that no longer exists is skipped with a
    warning — an export must never fail because a LUT was deleted after
    the clip was styled; the rest of the grade still applies.
    """
    if not isinstance(lut_file, str) or not lut_file:
        return None
    if not Path(lut_file).is_file():
        logger.warning("Imported LUT file missing at render, skipping: %s", lut_file)
        return None
    escaped = lut_file.replace("\\", "\\\\").replace("'", "\\'")
    return f"lut3d=file='{escaped}':interp=tetrahedral"


def build_grade_filter_chain(grade: Any) -> str:
    """The colour-grade chain for a resolved grade block ("" when neutral).

    Mapping (slider space -100..100, see auto_director.look):
    exposure/contrast/saturation -> one ``eq``; highlights/shadows -> one
    ``curves`` over all channels (shadow lift/crush moves the low points,
    highlight rolloff moves the high points); temperature/tint -> one
    ``colorbalance`` on the midtones (warm = +red/-blue, tint = magenta
    when positive). The three filters compose in that order into a single
    comma chain.

    An imported LUT (``grade["lut_file"]``, resolved by the seam from the
    grade's stored ``lut`` filename) goes FIRST: the .cube is the base
    colour transform and the slider filters fine-tune on top of it.
    """
    parts: list[str] = []
    if isinstance(grade, dict):
        lut_fragment = _lut3d_fragment(grade.get("lut_file"))
        if lut_fragment:
            parts.append(lut_fragment)
    exposure = _look_number(grade, "exposure")
    contrast = _look_number(grade, "contrast")
    saturation = _look_number(grade, "saturation")
    if exposure or contrast or saturation:
        parts.append(
            "eq=brightness={:.3f}:contrast={:.3f}:saturation={:.3f}".format(
                exposure * 0.003,
                1.0 + contrast * 0.006,
                max(0.0, 1.0 + saturation * 0.01),
            )
        )
    highlights = _look_number(grade, "highlights") / 100.0
    shadows = _look_number(grade, "shadows") / 100.0
    if highlights or shadows:
        low = min(0.40, max(0.0, shadows * 0.15))
        low_mid = min(0.60, max(0.02, 0.25 + shadows * 0.07))
        high_mid = min(0.98, max(0.40, 0.75 + highlights * 0.07))
        top = min(1.0, max(0.55, 1.0 + highlights * 0.12))
        parts.append(
            "curves=all='0/{:.3f} 0.25/{:.3f} 0.75/{:.3f} 1/{:.3f}'".format(
                low, low_mid, high_mid, top
            )
        )
    temperature = _look_number(grade, "temperature") / 100.0
    tint = _look_number(grade, "tint") / 100.0
    if temperature or tint:
        red = max(-0.6, min(0.6, 0.20 * temperature + 0.06 * tint))
        green = max(-0.6, min(0.6, 0.04 * temperature - 0.20 * tint))
        blue = max(-0.6, min(0.6, -0.28 * temperature + 0.06 * tint))
        parts.append(
            "colorbalance=rm={:.3f}:gm={:.3f}:bm={:.3f}".format(red, green, blue)
        )
    return ",".join(parts)


def build_look_fragments(
    edl: dict[str, Any], input_label: str, next_id: int
) -> tuple[list[str], str, int]:
    """Filter fragments realising the EDL's resolved ``look`` block.

    The look (``auto_director.look.build_edl_look``) is applied to the
    joined, B-roll-composited picture BEFORE overlays and burned captions,
    so grading and effects treat footage and cutaways alike while text
    stays crisp. Order inside the look: grade chain, then VFX in
    shake -> sharpen -> glow -> grain -> vignette -> letterbox order.

    A missing look, or one whose every value is neutral/off, produces NO
    fragments and returns the input label untouched — the graph is then
    byte-identical to the pre-look renderer.
    """
    look = edl.get("look")
    if not isinstance(look, dict):
        return [], input_label, next_id
    grade = look.get("grade")
    vfx = look.get("vfx")
    fragments: list[str] = []
    current = input_label

    chain = build_grade_filter_chain(grade)
    if chain:
        output = _label(f"lk{next_id}")
        next_id += 1
        fragments.append(f"{current}{chain}{output}")
        current = output

    shake = _look_number(vfx, "shake")
    if shake > 0:
        amplitude = 2 + round(shake / 100.0 * 8)
        pad = amplitude * 2
        scaled = _label(f"lk{next_id}s")
        output = _label(f"lk{next_id}")
        next_id += 1
        fragments.append(f"{current}scale=iw+{pad}:ih+{pad}{scaled}")
        fragments.append(
            f"{scaled}crop=w='iw-{pad}':h='ih-{pad}':"
            f"x='{amplitude}+{amplitude}*sin(2*PI*t*1.31)':"
            f"y='{amplitude}+{amplitude}*cos(2*PI*t*1.73)'{output}"
        )
        current = output

    sharpen = _look_number(vfx, "sharpen")
    if sharpen > 0:
        output = _label(f"lk{next_id}")
        next_id += 1
        fragments.append(
            f"{current}unsharp=5:5:{sharpen / 100.0 * 1.2:.2f}:5:5:0.0{output}"
        )
        current = output

    glow = _look_number(vfx, "glow")
    if glow > 0:
        main = _label(f"lk{next_id}m")
        branch = _label(f"lk{next_id}b")
        blurred = _label(f"lk{next_id}g")
        output = _label(f"lk{next_id}")
        next_id += 1
        sigma = 8.0 + glow / 100.0 * 24.0
        opacity = 0.15 + glow / 100.0 * 0.5
        fragments.append(f"{current}split{main}{branch}")
        fragments.append(f"{branch}gblur=sigma={sigma:.1f}{blurred}")
        fragments.append(
            f"{main}{blurred}blend=all_mode=screen:all_opacity={opacity:.2f}{output}"
        )
        current = output

    grain = _look_number(vfx, "grain")
    if grain > 0:
        output = _label(f"lk{next_id}")
        next_id += 1
        strength = max(1, round(grain / 100.0 * 24))
        fragments.append(f"{current}noise=alls={strength}:allf=t{output}")
        current = output

    vignette = _look_number(vfx, "vignette")
    if vignette > 0:
        output = _label(f"lk{next_id}")
        next_id += 1
        angle = 1.25 - vignette / 100.0 * 0.8
        fragments.append(f"{current}vignette=angle={angle:.3f}{output}")
        current = output

    letterbox = _look_number(vfx, "letterbox")
    if letterbox > 0:
        output = _label(f"lk{next_id}")
        next_id += 1
        bar = letterbox / 100.0 / 2.0
        fragments.append(
            f"{current}drawbox=x=0:y=0:w=iw:h='ih*{bar:.3f}':color=black:t=fill,"
            f"drawbox=x=0:y='ih-ih*{bar:.3f}':w=iw:h='ih*{bar:.3f}':color=black:t=fill"
            f"{output}"
        )
        current = output

    return fragments, current, next_id


def edl_broll_will_composite(edl: Optional[dict]) -> bool:
    """True when at least one of the EDL's broll entries will actually
    composite into the render.

    This mirrors the participation rule build_edl_filter_graph applies
    to the same entries: a dict whose ``local_path`` is an existing
    file and whose window is positive (``end`` after ``start``).
    Unresolved entries — no ``local_path``, e.g. when no stock provider
    key was available at planning time — do NOT count: they are skipped
    by the graph, so any report built on this predicate (the layout
    report's ``broll_applied``) must skip them too.
    """
    for entry in (edl or {}).get("broll") or []:
        if not isinstance(entry, dict):
            continue
        try:
            win_start = float(entry.get("start", 0))
            win_end = float(entry.get("end", 0))
        except (TypeError, ValueError):
            continue
        if win_end <= win_start:
            continue
        if Path(str(entry.get("local_path") or "")).is_file():
            return True
    return False


def build_flash_fragments(
    edl: dict[str, Any], input_label: str, next_id: int
) -> tuple[list[str], str, int]:
    """Filter fragments realising the EDL's ``audio_flashes`` stamp.

    One ``eq`` pulse per planned flash, chained on the joined picture
    right after the studio look (flashes treat footage and cutaways
    alike; overlays and burned captions stay un-flashed, like the
    grade). Each pulse raises brightness/gamma only inside its
    ``between(t, ...)`` window on the rendered timeline — the stamp's
    ``rendered_at_sec`` — and passes every other frame through
    unchanged. A missing stamp, an empty point list or a non-positive
    brightness produces NO fragments and returns the input label
    untouched, so the graph stays byte-identical for every EDL that
    predates or skips the feature.
    """
    flashes = edl.get("audio_flashes")
    if not isinstance(flashes, dict):
        return [], input_label, next_id
    points = flashes.get("points")
    if not isinstance(points, list) or not points:
        return [], input_label, next_id
    try:
        brightness = float(flashes.get("brightness", 0) or 0)
        gamma = float(flashes.get("gamma", 1) or 1)
        frames = int(flashes.get("duration_frames", 3) or 3)
    except (TypeError, ValueError):
        return [], input_label, next_id
    if brightness <= 0 or frames <= 0:
        return [], input_label, next_id
    half = frames / OUTPUT_FPS / 2.0
    fragments: list[str] = []
    current = input_label
    for point in points:
        if not isinstance(point, dict):
            continue
        try:
            at = float(point.get("rendered_at_sec", point.get("at_sec")))
        except (TypeError, ValueError):
            continue
        start = max(0.0, at - half)
        end = at + half
        output = _label(f"fl{next_id}")
        next_id += 1
        fragments.append(
            f"{current}eq=brightness={brightness:.3f}:gamma={gamma:.3f}"
            f":enable='between(t\\,{start:.3f}\\,{end:.3f})'{output}"
        )
        current = output
    return fragments, current, next_id


def build_edl_filter_graph(edl: dict[str, Any], input_path: Path, subtitle_ass_path: Optional[Path] = None, fonts_dir: Optional[Path] = None, sidecar_dir: Optional[Path] = None) -> tuple[list[str], str, list[str], str]:
    """Build the EDL video graph without changing the non-EDL render path."""
    # Callers pass plain strings often enough (tests, ad-hoc drivers) that
    # the path parameters are coerced once, here, instead of every consumer
    # having to care — input_path.parent below used to crash on a str.
    input_path = Path(input_path)
    if subtitle_ass_path is not None:
        subtitle_ass_path = Path(subtitle_ass_path)
    if fonts_dir is not None:
        fonts_dir = Path(fonts_dir)
    if sidecar_dir is not None:
        sidecar_dir = Path(sidecar_dir)
    segments = edl.get("segments") or []
    if not segments:
        raise ValueError("EDL has no segments")
    duration = float(edl.get("duration") or 0)
    graph: list[str] = []
    labels: list[str] = []
    next_id = 0
    if sidecar_dir is None:
        # drawtext textfiles live beside the render's input by default so
        # the caller's temp workspace owns their lifetime.
        sidecar_dir = input_path.parent / f".drawtext-{os.getpid()}"
    segment_input = "[0:v]"
    ranges = _source_ranges(edl)
    if ranges:
        prestage, segment_input, _ = _source_prestage_fragments(ranges, with_audio=False)
        graph.extend(prestage)
    # Zoom keyframes are timed on the CLIP timeline, but a segment's
    # start/end are its SOURCE bounds — identical numbers for every
    # historical EDL, different ones the moment a segment retimes
    # (a ramp, or a freeze hold). For retimed EDLs the matching
    # below therefore runs on a clip cursor (effective durations
    # minus join fades); for every other EDL the cursor IS the
    # segment start, so the matching is byte-identical to before.
    retimed = any(_is_ramp(s) or _is_freeze(s) for s in segments)
    join_fades = _join_fades(segments, duration)
    clip_cursor = float(segments[0].get("start", 0)) if segments else 0.0
    for index, segment in enumerate(segments):
        start, end = max(0.0, float(segment.get("start", 0))), float(segment.get("end", duration))
        if end <= start:
            raise ValueError(f"EDL segment {index} has an invalid range")
        name = f"seg{index}"
        seg_duration = _effective_segment_duration(segment, duration)
        if _is_freeze(segment):
            # Freeze-frame segment (trailer breakdown): hold the
            # single frame at freeze_at for the beat. The loop
            # filter repeats the first frame of a short trim window
            # (0.1 s always captures a frame at any sane source
            # rate); the trim after it caps the hold at the beat
            # length. Framing then runs on the held stream, so the
            # segment's own reframe/crop still applies.
            freeze_at = float(segment.get("freeze_at", start))
            freeze_dur = _freeze_duration(segment)
            hold = _label(f"frz{next_id}")
            next_id += 1
            graph.append(
                f"{segment_input}trim=start={freeze_at:.3f}:end={freeze_at + 0.1:.3f},"
                f"setpts=PTS-STARTPTS,loop=loop=-1:size=1:start=0,"
                f"trim=start=0:end={freeze_dur:.3f},setpts=PTS-STARTPTS,"
                f"fps={OUTPUT_FPS},format=yuv420p,setsar=1{hold}"
            )
            framing, label, next_id = _segment_framing_fragments(
                hold, segment, 0.0, freeze_dur, name, next_id
            )
        else:
            framing, label, next_id = _segment_framing_fragments(
                segment_input, segment, start, end, name, next_id
            )
        graph.extend(framing)
        zoom_start, zoom_end = (
            (clip_cursor, clip_cursor + seg_duration) if retimed else (start, end)
        )
        for zoom in edl.get("zoom_keyframes") or []:
            zoom_type = zoom.get("type")
            if zoom_type == "punch_in" and zoom_start <= float(zoom.get("time", -1)) < zoom_end:
                output = _label(f"zoom{next_id}")
                next_id += 1
                graph.append(build_punch_in_filter(float(zoom["time"]) - zoom_start, float(zoom.get("scale", 1.25)), float(zoom.get("duration", .5)), label, output))
                label = output
            elif zoom_type == "ken_burns":
                zoom_s, zoom_e = float(zoom.get("start", 0)), float(zoom.get("end", duration))
                overlap_start, overlap_end = max(zoom_start, zoom_s), min(zoom_end, zoom_e)
                if overlap_end > overlap_start:
                    output = _label(f"zoom{next_id}")
                    next_id += 1
                    graph.append(build_kenburns_filter(overlap_start - zoom_start, overlap_end - zoom_start, float(zoom.get("scale_start", 1)), float(zoom.get("scale_end", 1.05)), label, output))
                    label = output
        clip_cursor += seg_duration - (
            join_fades[index] if index < len(join_fades) else 0.0
        )
        # Speed ramp: retime the segment's frames (the audio finisher
        # applies the matching atempo over the same window). setpts
        # runs before the fps normalisation below, which then drops or
        # duplicates frames to hold the constant output rate — a real
        # speed change, frame-sampled (not optical-flow) by design.
        if _is_ramp(segment):
            sped = _label(f"spd{next_id}")
            next_id += 1
            graph.append(f"{label}setpts=PTS/{float(segment['speed']):.6f}{sped}")
            label = sped
        # xfade refuses inputs whose timebases differ ("timebase (1/30) do
        # not match"): a plain segment keeps the source timebase while a
        # zoompan segment leaves at the frame-rate timebase, so a mixed
        # concat+xfade EDL hard-failed. Normalise every segment stream to
        # the same timebase and frame rate before any join sees it. Order
        # matters: fps first, then settb LAST — with settb first, the fps
        # filter re-derives a 1/fps timebase on some branches (zoompan)
        # and the mismatch survives (reproduced on FFmpeg 8.1.2).
        normalised = _label(f"norm{next_id}")
        next_id += 1
        graph.append(f"{label}fps={OUTPUT_FPS},settb=AVTB{normalised}")
        labels.append(normalised)

    current = labels[0]
    rendered_duration = _effective_segment_duration(segments[0], duration)
    for index, (segment, fade) in enumerate(zip(segments[1:], join_fades), start=1):
        transition = str(segment.get("transition_in", "cut")).lower()
        next_duration = _effective_segment_duration(segment, duration)
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
            if entry.get("local_path"):
                # A resolved entry whose file vanished before the
                # composite (cache eviction, a moved library) used to
                # disappear without a trace; the render still succeeds
                # without the cutaway, but the loss is on the record.
                logger.warning(
                    "B-roll entry for query %r will not composite: %s is not a file",
                    entry.get("query"), broll_path,
                )
            continue
        input_index = 1 + extra_inputs.count("-i")
        extra_inputs.extend(["-stream_loop", "-1", "-i", str(broll_path)])
        stock_label = _label(f"broll{next_id}")
        output = _label(f"brolljoin{next_id}")
        next_id += 1
        enable = f"between(t\\,{win_start:.3f}\\,{win_end:.3f})"
        if str(entry.get("layout") or "cutaway") == "pip":
            # Picture-in-picture (Smart B-Roll v3): the stock shot sits in a
            # framed inset in the lower-middle band, so the speaker's face,
            # the top hook title and the caption band (~75% down) stay
            # visible. Chosen for live reactions and for profiles whose
            # source picture is the content (tutorial screens).
            graph.append(f"[{input_index}:v]trim=start={src_start:.3f}:end={src_start + (win_end - win_start):.3f},setpts=PTS-STARTPTS+{win_start:.3f}/TB,fps={OUTPUT_FPS},scale=w={BROLL_PIP_MAX_W}:h={BROLL_PIP_MAX_H}:force_original_aspect_ratio=decrease,scale=trunc(iw/2)*2:trunc(ih/2)*2,pad=iw+{2 * BROLL_PIP_BORDER}:ih+{2 * BROLL_PIP_BORDER}:{BROLL_PIP_BORDER}:{BROLL_PIP_BORDER}:color=white,setsar=1,format=yuv420p{stock_label}")
            graph.append(f"{current}{stock_label}overlay=x=(W-w)/2:y=H*{BROLL_PIP_BOTTOM_FRAC}-h:enable='{enable}'{output}")
        else:
            graph.append(f"[{input_index}:v]trim=start={src_start:.3f}:end={src_start + (win_end - win_start):.3f},setpts=PTS-STARTPTS+{win_start:.3f}/TB,fps={OUTPUT_FPS},scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,setsar=1,format=yuv420p{stock_label}")
            graph.append(f"{current}{stock_label}overlay=x=0:y=0:enable='{enable}'{output}")
        current = output

    # Studio look (grade + VFX): applied to the composited picture before
    # overlays and burned captions so text is never graded or grained. No
    # look (or a fully neutral one) adds no fragments — the graph stays
    # byte-identical to the pre-look renderer.
    look_fragments, current, next_id = build_look_fragments(edl, current, next_id)
    graph.extend(look_fragments)

    # Audio-reactive flashes: timed brightness pulses on the finished
    # picture, after the look and before overlays/captions (the same
    # layering law — text is never flashed). No stamp, no fragments.
    flash_fragments, current, next_id = build_flash_fragments(edl, current, next_id)
    graph.extend(flash_fragments)

    image_index = 1 + extra_inputs.count("-i")
    card_index = 0
    for overlay in edl.get("overlays") or []:
        start = max(0.0, float(overlay.get("time", 0)))
        end = start + max(0.0, float(overlay.get("duration", 3)))
        output = _label(f"overlay{next_id}")
        next_id += 1
        if overlay.get("type") == "info_card" and overlay.get("text") and end > start:
            graph.append(build_drawtext_filter(short_info_card_text(str(overlay["text"])), start, end, current, output, sidecar_dir=sidecar_dir, card_index=card_index))
            card_index += 1
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
    # Brand Kit logo watermark: the kit's stored logo composited over
    # the finished picture (after the look, before captions so text is
    # never covered), present for the whole clip at the kit's corner and
    # opacity. The size is frame-relative — ``width_frac`` of the frame
    # width, never of the logo file's own pixels — so the same kit
    # renders the same on-screen size whatever resolution the uploaded
    # logo has. No brand block — or a logo file that is gone — leaves
    # the graph exactly as it was.
    watermark = edl.get("brand_watermark")
    if isinstance(watermark, dict):
        logo_file = Path(str(watermark.get("path") or ""))
        if logo_file.is_file():
            try:
                opacity = min(1.0, max(0.05, float(watermark.get("opacity", 0.85))))
                width_frac = min(0.5, max(0.05, float(watermark.get("width_frac", 0.16))))
                margin_frac = min(0.2, max(0.0, float(watermark.get("margin_frac", 0.03))))
            except (TypeError, ValueError):
                opacity, width_frac, margin_frac = 0.85, 0.16, 0.03
            position = str(watermark.get("position") or "bottom_right")
            margin_x = f"W*{margin_frac:.3f}"
            margin_y = f"H*{margin_frac:.3f}"
            positions = {
                "bottom_right": f"x=W-w-{margin_x}:y=H-h-{margin_y}",
                "bottom_left": f"x={margin_x}:y=H-h-{margin_y}",
                "top_right": f"x=W-w-{margin_x}:y={margin_y}",
                "top_left": f"x={margin_x}:y={margin_y}",
            }
            logo_index = 1 + extra_inputs.count("-i")
            extra_inputs.extend(["-loop", "1", "-i", str(logo_file)])
            logo_label = _label(f"brandlogo{next_id}")
            base_label = _label(f"brandbase{next_id}")
            ref_label = _label(f"brandref{next_id}")
            output = _label(f"brandwm{next_id}")
            next_id += 1
            # The picture is split: one copy feeds the overlay, the
            # other is the scale filter's reference input, so ``rw`` in
            # the scale expression is the frame width at this point of
            # the graph and the logo lands at width_frac of the frame —
            # identical output for a 200 px and a 2000 px logo file.
            graph.append(f"{current}split{base_label}{ref_label}")
            graph.append(
                f"[{logo_index}:v]{ref_label}scale=w='rw*{width_frac:.3f}':h=-1:flags=lanczos,"
                f"format=rgba,colorchannelmixer=aa={opacity:.3f},setsar=1{logo_label}"
            )
            graph.append(
                f"{base_label}{logo_label}overlay={positions.get(position, positions['bottom_right'])}{output}"
            )
            current = output
    if subtitle_ass_path:
        output = _label(f"subtitle{next_id}")
        graph.append(f"{current}{subtitles_filter_fragment(subtitle_ass_path, fonts_dir)}{output}")
        current = output
    encoder_name, encoder_args = get_video_encoder()
    # Name the encoder in the log on every render: when a render dies,
    # "which encoder was running" is the first question, and the answer
    # used to be discarded here before it could be recorded anywhere.
    logger.info("EDL encoder: %s (profile=%s)", encoder_name, get_render_profile())
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
    # A mood/cue that exists only as a variant folder (assets/<kind>/
    # library/<name>/) still resolves; ASSET_VARIANTS=off skips this.
    from .asset_library import first_library_file

    return first_library_file(folder, name)


def _resolve_user_music_file(name: str) -> Optional[Path]:
    """Resolve a music name against the user-uploaded music folder.

    The bundled library (``assets/music``) always wins; this is the
    second stop for profiles and per-task overrides that name a track
    the user uploaded through the music library API.
    """
    try:
        from .music_library import user_music_dir

        folder = user_music_dir()
    except Exception:
        return None
    return _resolve_asset_file(folder, name)


def _resolve_user_sfx_file(name: str) -> Optional[Path]:
    """Resolve an SFX name against the user-uploaded SFX folder.

    The bundled library (``assets/sfx``) always wins; this is the
    second stop for placements that name an effect the user uploaded
    through the SFX library API.
    """
    try:
        from .sfx_library import user_sfx_dir

        folder = user_sfx_dir()
    except Exception:
        return None
    return _resolve_asset_file(folder, name)


def _effective_segment_duration(segment: dict[str, Any], edl_duration: float) -> float:
    """A segment's duration on the rendered timeline.

    A speed-ramped segment (EDL ``speed`` != 1.0, assigned by the
    beat-grid lane with a reason) occupies (end - start) / speed
    seconds of output. A freeze-frame segment (EDL ``freeze: True``,
    assigned by the trailer-breakdown template) occupies
    ``freeze_duration`` seconds of output — its source bounds are a
    single frame, held for the beat. Everything that mirrors the
    render — duration accounting, join offsets, the audio finisher —
    must use this or audio and picture drift apart by exactly the
    freeze delta.
    """
    if _is_freeze(segment):
        return _freeze_duration(segment)
    duration = float(segment.get("end", edl_duration)) - float(segment.get("start", 0))
    speed = segment.get("speed")
    if isinstance(speed, (int, float)) and speed > 0 and speed != 1.0:
        return duration / float(speed)
    return duration


def _is_freeze(segment: dict[str, Any]) -> bool:
    """Whether an EDL segment is a freeze-frame hold (D17).

    A freeze segment's source bounds are a single frame (one frame
    at the output rate); the renderer holds that frame for
    ``freeze_duration`` seconds. Anything that is not a dict, or
    lacks a truthy ``freeze`` flag, is not a freeze.
    """
    return isinstance(segment, dict) and bool(segment.get("freeze"))


def _freeze_duration(segment: dict[str, Any]) -> float:
    """A freeze segment's hold length in seconds (default 1.5 s)."""
    try:
        duration = float(segment.get("freeze_duration") or 0.0)
    except (TypeError, ValueError):
        duration = 0.0
    return duration if duration > 0 else 1.5


def _is_ramp(segment: dict[str, Any]) -> bool:
    speed = segment.get("speed")
    return isinstance(speed, (int, float)) and speed > 0 and speed != 1.0


def _atempo_chain(speed: float) -> str:
    """An atempo chain realising any factor (atempo itself caps at 2x
    per instance on the FFmpeg builds this targets, so out-of-range
    factors compose multiplicatively)."""
    factors: list[float] = []
    remaining = float(speed)
    while remaining > 2.0:
        factors.append(2.0)
        remaining /= 2.0
    while remaining < 0.5:
        factors.append(0.5)
        remaining /= 0.5
    factors.append(remaining)
    return ",".join(f"atempo={factor:.6f}" for factor in factors)


def _join_fades(segments: list[dict[str, Any]], edl_duration: float) -> list[float]:
    """Per-join crossfade durations, mirroring build_edl_filter_graph."""
    fades: list[float] = []
    if not segments:
        return fades
    rendered = _effective_segment_duration(segments[0], edl_duration)
    for segment in segments[1:]:
        seg_duration = _effective_segment_duration(segment, edl_duration)
        transition = str(segment.get("transition_in", "cut")).lower()
        requested = max(0.0, float(segment.get("transition_duration", 0) or 0))
        fade = min(requested, rendered, seg_duration) if TRANSITION_MAP.get(transition) else 0.0
        fades.append(fade)
        rendered += seg_duration - fade
    return fades


def segment_join_plan(
    segments: list[dict[str, Any]], edl_duration: float
) -> list[dict[str, float]]:
    """The renderer's per-join plan: one entry per join after the first
    segment, ``{"source_boundary", "fade"}``.

    THE single source of join truth inside the renderer: the video graph
    (build_edl_filter_graph), the voice chain
    (_segment_voice_join_fragments), the duration math
    (_rendered_duration) and — passed in by the caller in video_utils —
    the caption word shift all consume the fades computed by
    _join_fades, so the streams can never disagree about where a join
    sits or how much it compacts the timeline.
    """
    fades = _join_fades(segments, edl_duration)
    return [
        {
            "source_boundary": float(segment.get("start", 0)),
            "fade": fade,
        }
        for segment, fade in zip(segments[1:], fades)
    ]


def _rendered_duration(edl: dict[str, Any]) -> float:
    """Total output duration of an EDL, mirroring build_edl_filter_graph's joins."""
    segments = edl.get("segments") or []
    if not segments:
        return max(0.0, float(edl.get("duration") or 0))
    duration = float(edl.get("duration") or 0)
    rendered = _effective_segment_duration(segments[0], duration)
    for segment, fade in zip(segments[1:], _join_fades(segments, duration)):
        rendered += _effective_segment_duration(segment, duration) - fade
    return max(0.0, rendered)


def _ramped_voice_fragments(
    edl: dict[str, Any], audio_source_label: str
) -> tuple[list[str], str]:
    """Per-segment voice chain for a speed-ramped or freeze-held EDL.

    The video graph retimes each segment with setpts (ramps) or
    holds one frame (freezes); the voice must be retimed by the
    same factor over the same windows — or held silent for the
    same hold — or speech slides off the picture. Each segment's
    audio is cut from the (compacted) source timeline, run through
    an atempo chain at the segment's speed (ramps) or replaced by
    silence for the hold (freezes, by design), and rejoined with
    the video's own join fades (acrossfade where the video
    crossfades, hard concat where it cuts) — so the joined audio
    is sample-aligned with the joined video by construction.
    """
    segments = edl.get("segments") or []
    duration = float(edl.get("duration") or 0)
    fragments: list[str] = []
    # Only the segments that cut audio from the source need a split
    # branch: a freeze draws its silence from anullsrc and never
    # consumes one, so splitting for it would leave an unconnected
    # asplit output and ffmpeg refuses the graph.
    cut = [i for i, segment in enumerate(segments) if not _is_freeze(segment)]
    if len(cut) > 1:
        branch_labels = {i: f"[araw{i}]" for i in cut}
        fragments.append(
            f"{audio_source_label}asplit={len(cut)}"
            f"{''.join(branch_labels[i] for i in cut)}"
        )
    else:
        branch_labels = {i: audio_source_label for i in cut}
    labels: list[str] = []
    for index, segment in enumerate(segments):
        start = max(0.0, float(segment.get("start", 0)))
        end = float(segment.get("end", duration))
        if _is_freeze(segment):
            # A freeze holds one frame for freeze_duration seconds
            # and renders SILENT by design (the trailer-breakdown
            # template stamps freeze_audio: silent): the reaction
            # beat that follows carries the voice. Emitting the
            # segment's one frame of audio stretched to the hold
            # would smear it into noise, and cutting it short
            # would starve the join and desync everything after.
            label = f"[aramp{index}]"
            fragments.append(
                f"anullsrc=r=48000:cl=stereo,"
                f"atrim=0:{_freeze_duration(segment):.3f},"
                f"asetpts=PTS-STARTPTS{label}"
            )
            labels.append(label)
            continue
        chain = _atempo_chain(float(segment["speed"])) if _is_ramp(segment) else "anull"
        label = f"[aramp{index}]"
        fragments.append(
            f"{branch_labels[index]}atrim=start={start:.3f}:end={end:.3f},"
            f"asetpts=PTS-STARTPTS,{chain}{label}"
        )
        labels.append(label)
    if len(labels) == 1:
        return fragments, labels[0]
    fades = _join_fades(segments, duration)
    current = labels[0]
    for index, fade in enumerate(fades, start=1):
        output = "[a_ramped]" if index == len(fades) else f"[ajoinr{index}]"
        if fade > 0:
            fragments.append(f"{current}{labels[index]}acrossfade=d={fade:.6f}{output}")
        else:
            fragments.append(f"{current}{labels[index]}concat=n=2:v=0:a=1{output}")
        current = output
    return fragments, "[a_ramped]"


def _segment_voice_join_fragments(
    edl: dict[str, Any], audio_source_label: str
) -> tuple[list[str], Optional[str]]:
    """Per-segment voice chain mirroring the video's segment joins.

    The video graph compacts the timeline at every crossfade join; the
    voice must compact identically or it trails the picture by the fade
    duration per join, cumulatively (the sync diagnosis measured
    +400 ms per 0.4 s join, +1.6 s by the fifth marker). Each segment's
    audio is cut from the (range-compacted) source timeline with the
    same bounds the video trims use, then rejoined with acrossfade
    where the video crossfades and hard concat where it cuts —
    acrossfade compacts by exactly the xfade amount, so the joined
    voice is sample-aligned with the joined video by construction.

    Returns (fragments, label), or ([], None) when no join crossfades:
    the caller then keeps the legacy single-stream audio path, whose
    commands stay byte-identical.
    """
    segments = edl.get("segments") or []
    duration = float(edl.get("duration") or 0)
    fades = _join_fades(segments, duration)
    if len(segments) < 2 or not any(fade > 0 for fade in fades):
        return [], None
    fragments: list[str] = []
    branch_labels = [f"[avraw{index}]" for index in range(len(segments))]
    fragments.append(
        f"{audio_source_label}asplit={len(segments)}{''.join(branch_labels)}"
    )
    labels: list[str] = []
    for index, segment in enumerate(segments):
        start = max(0.0, float(segment.get("start", 0)))
        end = float(segment.get("end", duration))
        label = f"[avseg{index}]"
        fragments.append(
            f"{branch_labels[index]}atrim=start={start:.3f}:end={end:.3f},"
            f"asetpts=PTS-STARTPTS{label}"
        )
        labels.append(label)
    current = labels[0]
    for index, fade in enumerate(fades, start=1):
        output = "[a_segjoin]" if index == len(fades) else f"[avjoin{index}]"
        if fade > 0:
            fragments.append(f"{current}{labels[index]}acrossfade=d={fade:.6f}{output}")
        else:
            fragments.append(f"{current}{labels[index]}concat=n=2:v=0:a=1{output}")
        current = output
    return fragments, "[a_segjoin]"


def _resolve_sfx_path(assets: Path, name: str, seed_key: str, pack: Optional[str]) -> Optional[Path]:
    """Resolve one SFX name to a library file, in the renderer's order:
    the seeded variant pool, the bundled library file, the user library."""
    from .asset_library import pick_variant

    resolved = pick_variant(assets / "sfx", name, seed_key, pack=pack)
    if resolved is None:
        resolved = _resolve_asset_file(assets / "sfx", name)
    if resolved is None:
        resolved = _resolve_user_sfx_file(name)
    return resolved


def build_edl_audio_graph(edl: dict[str, Any], first_input_index: int, assets_dir: Optional[Path] = None, audio_source_label: str = "[0:a]") -> Optional[tuple[list[str], str, str]]:
    """Plan the audio-finishing graph (music ducking + SFX events) for an EDL.

    Returns (extra ffmpeg inputs, filter-graph fragment, output audio label),
    or None when the EDL carries no music track or SFX event that resolves to
    a file in the asset library — the caller then keeps the legacy audio path
    untouched. The fragment ends with the -14 LUFS loudnorm as its last step:
    FFmpeg refuses a -af chain on a stream fed from a complex filtergraph, so
    the finished path carries loudnorm inside the graph and the caller drops
    it from the output args (the legacy path keeps it via -af).

    Two editor-only EDL blocks also force a plan (see
    ``auto_director.audio_studio``): ``edl["audio_studio"]`` retunes the
    music values, cleans the voice chain, picks the loudness target and
    adds edge fades; ``edl["voiceover"]`` mixes a synthesized narration
    file over (``hook``) or instead of (``replace``) the original speech.
    With neither block present the graph is byte-identical to the
    pre-studio one.
    """
    assets = Path(assets_dir) if assets_dir is not None else ASSETS_DIR
    duration = _rendered_duration(edl)
    if duration <= 0:
        return None
    # A speed-ramped EDL always gets an audio plan — even with no music
    # or SFX to finish — because the plain path cannot retime the voice
    # and the clip would drift out of sync by the ramp delta. A
    # freeze-held EDL is the same: the plain path cannot hold the voice
    # silent for the hold, so it gets a plan for the same reason.
    has_ramps = any(_is_ramp(segment) for segment in edl.get("segments") or [])
    has_freezes = any(_is_freeze(segment) for segment in edl.get("segments") or [])
    has_retimed = has_ramps or has_freezes
    audio = edl.get("audio") or {}
    try:
        volume = float(audio.get("music_volume_0_to_1", audio.get("music_volume", 0.0)) or 0.0)
        ducking_db = float(audio.get("ducking_db", 0.0) or 0.0)
    except (TypeError, ValueError):
        return None
    # Audio Studio overrides (editor-only; stamped onto the EDL by the
    # re-render seam — see auto_director.audio_studio). They retune the
    # music values above and may force a finishing plan below; malformed
    # fields are ignored one by one, never costing the clip its audio.
    studio = edl.get("audio_studio")
    if not isinstance(studio, dict):
        studio = {}
    try:
        if studio.get("music_volume") is not None:
            volume = float(studio["music_volume"])
        if studio.get("ducking_db") is not None:
            ducking_db = float(studio["ducking_db"])
    except (TypeError, ValueError):
        pass
    from ..auto_director.audio_studio import (
        fade_filters,
        loudnorm_filter,
        studio_needs_finishing,
        voice_cleanup_chain,
    )

    # Voiceover (also editor-only, also opt-in per clip): a synthesized
    # narration file the finisher mixes over — or instead of — the
    # original speech. A missing file degrades to no narration with a
    # warning; it must never cost the clip.
    vo_path: Optional[Path] = None
    vo_mode = "hook"
    voiceover = edl.get("voiceover")
    if isinstance(voiceover, dict):
        candidate = str(voiceover.get("path") or "").strip()
        if candidate:
            if Path(candidate).is_file():
                vo_path = Path(candidate)
                mode = str(voiceover.get("mode") or "hook").strip().lower()
                vo_mode = mode if mode in ("hook", "replace") else "hook"
            else:
                logger.warning(
                    "EDL voiceover file %r does not exist; rendering without narration",
                    candidate,
                )
    track_name = str(audio.get("music_track") or audio.get("music_mood") or "").strip()
    music_path = None
    variant_seed = str(edl.get("clip_id") or "clip")
    if track_name and track_name.lower() != "none" and volume > 0:
        if not str(audio.get("music_track") or "").strip() and not edl.get("beat_grid"):
            # A mood (not an explicit track pick) plays one member of its
            # variant pool, stable per clip. Beat-grid clips keep the stem
            # file: their grid was analysed on it.
            from .asset_library import pick_variant

            music_path = pick_variant(assets / "music", track_name, f"{variant_seed}|music")
        if music_path is None:
            music_path = _resolve_asset_file(assets / "music", track_name)
        if music_path is None:
            music_path = _resolve_user_music_file(track_name)
        if music_path is None:
            # The profile asked for music and none resolved: say so loudly,
            # naming the track and both library locations, or a
            # missing/renamed library file is indistinguishable from a
            # profile that never wanted music.
            try:
                from .music_library import user_music_dir

                user_music = str(user_music_dir())
            except Exception:
                user_music = "<user music folder>"
            logger.warning(
                "EDL expects music %r but no audio plan was built — no file "
                "matching it was found in %s or in the user music folder %s; "
                "check assets/music and the uploaded-music library",
                track_name,
                assets / "music",
                user_music,
            )

    sfx_events: list[tuple[Path, float, float]] = []
    sfx_pack = str(audio.get("sfx_pack") or "").strip() or None
    for index, event in enumerate(edl.get("sfx_events") or []):
        if not isinstance(event, dict):
            continue
        name = str(event.get("name") or "").strip()
        if not name:
            continue
        sfx_path = _resolve_sfx_path(assets, name, f"{variant_seed}|sfx|{index}", sfx_pack)
        if sfx_path is None:
            continue
        try:
            at_sec = max(0.0, float(event.get("at_sec", 0) or 0))
            gain_db = float(event.get("gain_db", 0) or 0)
        except (TypeError, ValueError):
            continue
        sfx_events.append((sfx_path, at_sec, gain_db))

    # HookFinish v2 (media.hook_v2, HOOK_FINISH_V2=on): the plan
    # guarantees one SFX hit inside the first second of a hooked clip.
    # When a planned event already lands there, the plan is untouched;
    # when none does, one library hit — picked by moment type — is
    # resolved here and ducked under the voice in the graph below. With
    # the switch off this block never runs and the plan is byte-for-byte
    # the pre-v2 one.
    hook_hit: Optional[tuple[Path, float, float]] = None
    from . import hook_v2

    if hook_v2.audio_guarantee_applies(edl, duration) and not any(
        at_sec <= hook_v2.FIRST_SECOND_WINDOW for _, at_sec, _ in sfx_events
    ):
        for category in hook_v2.hook_hit_categories(edl):
            resolved = _resolve_sfx_path(assets, category, f"{variant_seed}|hook-hit", sfx_pack)
            if resolved is not None:
                hook_hit = (resolved, hook_v2.FIRST_SECOND_SFX_AT, hook_v2.FIRST_SECOND_SFX_GAIN_DB)
                break
        if hook_hit is None:
            logger.warning(
                "HookFinish v2: no library SFX resolved for the first-second "
                "hook hit (tried %s); the clip keeps its planned audio",
                ", ".join(hook_v2.hook_hit_categories(edl)),
            )

    if (
        music_path is None
        and not sfx_events
        and hook_hit is None
        and not has_retimed
        and not studio_needs_finishing(studio)
        and vo_path is None
    ):
        return None

    # Profiles store ducking_db as a (negative) reduction — -12 means "lower
    # the music by 12 dB under the voice". The amount is what drives the
    # compressor, so a stored -12 and a stored +12 request the same ducking.
    ducking_amount = abs(ducking_db)
    ducking = music_path is not None and ducking_amount > 0
    extra_inputs: list[str] = []
    graph: list[str] = []
    if has_retimed:
        ramp_fragments, voice_label = _ramped_voice_fragments(edl, audio_source_label)
        graph.extend(ramp_fragments)
        voice_in, voice_prefix = voice_label, ""
    else:
        voice_in = audio_source_label
        voice_prefix = f"atrim=0:{duration:.3f},asetpts=PTS-STARTPTS,"
    # Audio Studio voice cleanup rides the voice chain (so the ducking
    # key hears the cleaned voice too); "" when nothing was requested,
    # keeping the graph byte-identical to the pre-studio one. A
    # "replace" voiceover mutes the original speech in place — same
    # timing, same labels consumed, no original words survive.
    cleanup = voice_cleanup_chain(studio)
    mute = ",volume=0" if vo_path is not None and vo_mode == "replace" else ""
    if ducking:
        # The hook hit (when guaranteed) sidechain-ducks under the
        # voice, so the voice feeds a third split as its key.
        key_split = ",asplit=3[a_voice][a_voice_sc][a_voice_key]" if hook_hit is not None else ",asplit=2[a_voice][a_voice_sc]"
        graph.append(f"{voice_in}{voice_prefix}{_AUDIO_NORMALIZE}{mute}{cleanup}{key_split}")
    elif hook_hit is not None:
        graph.append(f"{voice_in}{voice_prefix}{_AUDIO_NORMALIZE}{mute}{cleanup},asplit=2[a_voice][a_voice_key]")
    else:
        graph.append(f"{voice_in}{voice_prefix}{_AUDIO_NORMALIZE}{mute}{cleanup}[a_voice]")
    mix_labels = ["[a_voice]"]
    next_index = first_input_index
    if music_path is not None:
        extra_inputs.extend(["-stream_loop", "-1", "-i", str(music_path)])
        graph.append(f"[{next_index}:a]atrim=0:{duration:.3f},asetpts=PTS-STARTPTS,{_AUDIO_NORMALIZE},volume={volume:.6f}[a_music]")
        next_index += 1
        if ducking:
            # The ducking amount drives the compressor ratio: more requested
            # ducking, harder the music is pushed under the voice.
            ratio = min(20.0, 2.0 + ducking_amount)
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
    if hook_hit is not None:
        # The guaranteed first-second hit, ducked under the voice by
        # the sidechain key split out of the voice chain above: when
        # the first words land, the hit is already under them.
        sfx_path, at_sec, gain_db = hook_hit
        extra_inputs.extend(["-i", str(sfx_path)])
        gain = 10 ** (gain_db / 20.0)
        delay_ms = int(round(at_sec * 1000))
        graph.append(f"[{next_index}:a]{_AUDIO_NORMALIZE},adelay={delay_ms}:all=1,volume={gain:.6f}[a_hook_raw]")
        graph.append("[a_hook_raw][a_voice_key]sidechaincompress=threshold=0.02:ratio=8.00:attack=5:release=250[a_hook_hit]")
        next_index += 1
        mix_labels.append("[a_hook_hit]")
    graph.append(f"{''.join(mix_labels)}amix=inputs={len(mix_labels)}:duration=first:dropout_transition=0:normalize=0[a_mix]")
    mix_label = "[a_mix]"
    if vo_path is not None:
        # Narration stage: the voiceover starts at the clip's first
        # frame, trimmed to the clip. "hook" mode ducks the finished
        # mix hard under the narration while it speaks (the narration
        # IS the hook); "replace" needs no ducking — the original
        # speech is already muted in the voice chain above.
        extra_inputs.extend(["-i", str(vo_path)])
        graph.append(f"[{next_index}:a]{_AUDIO_NORMALIZE},atrim=0:{duration:.3f},asetpts=PTS-STARTPTS[a_vo_raw]")
        next_index += 1
        if vo_mode == "hook":
            graph.append("[a_vo_raw]asplit=2[a_vo][a_vo_key_raw]")
            # sidechaincompress ends when its key input ends, so the key
            # is padded with silence to the full clip duration: after
            # the narration finishes the mix plays on unducked and the
            # clip keeps its full length.
            graph.append(f"[a_vo_key_raw]apad,atrim=0:{duration:.3f}[a_vo_key]")
            graph.append(f"{mix_label}[a_vo_key]sidechaincompress=threshold=0.02:ratio=12.00:attack=10:release=250[a_mix_vo_ducked]")
            graph.append("[a_mix_vo_ducked][a_vo]amix=inputs=2:duration=first:dropout_transition=0:normalize=0[a_mix_vo]")
        else:
            graph.append(f"{mix_label}[a_vo_raw]amix=inputs=2:duration=first:dropout_transition=0:normalize=0[a_mix_vo]")
        mix_label = "[a_mix_vo]"
    # Finishing tail: loudnorm first (the studio block may pick the
    # -16 target or none at all), then the edge fades last so their
    # endpoints are true silence whatever the loudnorm did. With no
    # studio block this is exactly the legacy "[a_mix]<loudnorm>[a_out]".
    tail: list[str] = []
    loudnorm = loudnorm_filter(studio)
    if loudnorm:
        tail.append(loudnorm)
    tail.extend(fade_filters(studio, duration))
    if tail:
        graph.append(f"{mix_label}{','.join(tail)}[a_out]")
    else:
        graph.append(f"{mix_label}anull[a_out]")
    return extra_inputs, ";".join(graph), "[a_out]"


DEFAULT_RENDER_TIMEOUT_SEC = 900


def render_timeout_sec() -> int:
    """Per-attempt EDL render timeout (env RENDER_TIMEOUT_SEC, default 900).

    A wedged encode must surface as a failed clip in minutes, not stall a
    task for the hour the previous 3600 s allowance permitted. Unset,
    invalid or non-positive values fall back to the default.
    """
    try:
        value = int(os.environ.get("RENDER_TIMEOUT_SEC", "").strip())
    except ValueError:
        return DEFAULT_RENDER_TIMEOUT_SEC
    return value if value > 0 else DEFAULT_RENDER_TIMEOUT_SEC


# stderr fragments that blame the audio-finishing graph (its filters, its
# stream labels, or the source audio pad it reads). Only failures carrying
# one of these can plausibly be cured by retrying without the audio graph;
# anything else (video graph, encoder, muxer) would fail identically on the
# plain path, so the clip fails fast with the original error instead of
# paying a second full encode.
_AUDIO_GRAPH_ERROR_MARKERS = (
    "sidechaincompress",
    "loudnorm",
    "amix",
    "adelay",
    "acrossfade",
    "asplit",
    "afftdn",
    "afade",
    "equalizer",
    "highpass",
    "[a_mix]",
    "[a_out]",
    "[a_voice]",
    "[a_music]",
    "[a_sfx",
    "[a_vo",
    "[a_segjoin]",
    "[avseg",
    "[avraw",
    "[avjoin",
    "[0:a]",
)


def _failure_implicates_audio(stderr: Optional[str]) -> bool:
    """Whether an ffmpeg failure plausibly came from the audio graph."""
    if not stderr:
        return False
    text = stderr.lower()
    return any(marker in text for marker in _AUDIO_GRAPH_ERROR_MARKERS)


# stderr fragments that blame the GPU encoder itself -- its session setup
# or its driver libraries -- rather than the filter graph or the input.
# Only a failure carrying one of these can plausibly be cured by
# re-encoding on libx264; anything else would fail identically, so the
# clip keeps its fail-fast behaviour. Matched case-insensitively, and
# only consulted when the failed encode actually selected NVENC (on a
# libx264 encode these strings cannot appear at all). The list is kept
# deliberately narrow: every entry is specific to the GPU encode path.
_NVENC_FAILURE_MARKERS = (
    "h264_nvenc",
    "nvenc",
    "cuda",
    "openencodesessionex",
    "libnvidia",
    "error while opening encoder",
)


def _nvenc_failure_marker(stderr: Optional[str]) -> Optional[str]:
    """The NVENC marker a failure's stderr carries, or None."""
    if not stderr:
        return None
    text = stderr.lower()
    for marker in _NVENC_FAILURE_MARKERS:
        if marker in text:
            return marker
    return None


_NVDEC_FAILURE_MARKERS = (
    "hwaccel",
    "cuvid",
    "nvdec",
    "no decoder for",
    "failed to open decoder",
    "invalid setup for format",
    "cuda_error",
)


def _nvdec_failure_marker(stderr: Optional[str]) -> Optional[str]:
    """The NVDEC marker a failure's stderr carries, or None."""
    if not stderr:
        return None
    text = stderr.lower()
    for marker in _NVDEC_FAILURE_MARKERS:
        if marker in text:
            return marker
    return None


def _run_render_command(command: list[str]) -> tuple[bool, str]:
    try:
        result = run_ffmpeg_command(eco_render_command(command), timeout=render_timeout_sec())
    except subprocess.TimeoutExpired:
        logger.error(
            "EDL render timed out after %ss: %s",
            render_timeout_sec(),
            " ".join(command),
        )
        return False, ""
    except OSError as exc:
        logger.error("EDL render could not start: %s", exc)
        return False, ""
    return result.returncode == 0, result.stderr or ""


def _render_edl_with_encoder(input_path: Path, output_path: Path, edl: dict[str, Any], extra_inputs: list[str], graph: str, video_label: str, encoder_name: str, encoder_args: list[str], assets_dir: Optional[Path], use_nvdec: bool = False) -> tuple[bool, str]:
    """One full render attempt of an EDL with a chosen encoder.

    Returns (ok, stderr of the last ffmpeg run). The finished-audio path
    and its plain-audio retry both live here, so the caller's escape
    hatch re-runs the whole attempt -- same graph, same audio behaviour --
    with only the encoder swapped. The seek window is recomputed from the
    EDL (a pure transform) so the commands below decode only the kept
    ranges, exactly as the caller's graph -- built from the same shifted
    EDL -- expects.

    ``use_nvdec`` adds ``-hwaccel cuda`` to the SOURCE input only (the
    extra inputs are audio/images/short B-roll, where hardware decode
    buys nothing). Frames download to system memory automatically --
    the graph's zoompan/xfade/libass stages are CPU filters -- so the
    graph itself is unchanged. The caller passes it only when
    nvdec_input_args() approved it (flag on + probe passed); the
    default False keeps every command byte-identical.
    """
    seek_args, graph_edl, _ = _source_window(edl)
    decode_args = ["-hwaccel", "cuda"] if use_nvdec else []
    ranges = _source_ranges(graph_edl)
    # The full input list for this attempt: the caller's extra inputs,
    # plus — for an opted-in separation task — the vocals stem as one
    # more input. The stem is appended before any audio-plan index is
    # computed, so the music/SFX input numbering shifts consistently.
    render_inputs = list(extra_inputs)
    audio_source_label = "[0:a]"
    if ranges:
        # Direct-from-source mode: the EDL carries the keep ranges, so the
        # graph's pre-stage already concatenated them from the original
        # file (build_edl_filter_graph). The audio side needs the same
        # pre-stage, and the plain path can no longer map the raw input
        # audio — it takes the concatenated stream through loudnorm inside
        # the graph, like the finished path. The caller only sets
        # source_ranges when the source has an audio stream.
        stem = _edl_vocals_stem(edl)
        if stem is not None:
            # Voice/music separation (opt-in): the voice chain is cut
            # from the separated vocals stem instead of the raw mix, so
            # the source's own background music never reaches the
            # export; the music bed and ducking then work over clean
            # speech exactly as designed. The stem input receives the
            # same seek window as the source, and the shifted ranges
            # apply to it unchanged. No stamp (or a vanished stem) keeps
            # the historical [0:a] pre-stage, byte for byte.
            stem_index = 1 + render_inputs.count("-i")
            render_inputs = [*render_inputs, *seek_args, "-i", str(stem)]
            audio_prestage, audio_source_label = _source_audio_prestage_fragments(
                ranges, f"[{stem_index}:a]"
            )
            logger.info(
                "EDL render drawing clip audio from the separated vocals "
                "stem: %s",
                stem,
            )
        else:
            audio_prestage, audio_source_label = _source_audio_prestage_fragments(ranges)
        graph = ";".join([*audio_prestage, graph])

    segments = edl.get("segments") or []
    has_retimed = any(
        _is_ramp(segment) or _is_freeze(segment) for segment in segments
    )
    # Crossfade segment joins compact the video timeline; the voice gets
    # the same joins (see _segment_voice_join_fragments) in both the
    # plain and the finished path. Retimed EDLs are excluded here: their
    # audio plan already joins per-segment with atempo/silence — and
    # always exists — so the plain path never carries their voice.
    voice_join_fragments: list[str] = []
    voice_join_label: Optional[str] = None
    if not has_retimed:
        voice_join_fragments, voice_join_label = _segment_voice_join_fragments(
            edl, audio_source_label
        )
    graph_without_voice_chain = graph
    if voice_join_fragments:
        graph = ";".join([graph, *voice_join_fragments])
    # The stream the audio finisher treats as "the voice": the joined
    # stream when the chain exists, the raw source stream otherwise
    # (identical to the pre-fix behaviour whenever no join crossfades).
    voice_label = voice_join_label or audio_source_label

    legacy_plain_command: Optional[list[str]] = None
    if ranges:
        plain_graph = f"{graph};{voice_label}{LOUDNORM_FILTER}[a_out]"
        plain_command = ["ffmpeg", "-y", *seek_args, *decode_args, "-i", str(input_path), *render_inputs, "-filter_complex", plain_graph, "-map", video_label, "-map", "[a_out]", *encoder_args, *build_audio_output_args(True, loudnorm=False), "-shortest", "-movflags", "+faststart", str(output_path)]
    elif voice_join_label:
        # Intermediate mode with crossfade joins: the plain path can no
        # longer map the raw input audio (it sits on the un-compacted
        # timeline), so loudnorm moves in-graph behind the joined voice,
        # exactly like the direct-from-source plain path. The legacy
        # command is kept as the last-resort fallback for a source whose
        # audio stream cannot feed the chain (e.g. no audio at all) —
        # it cannot desync a clip that has no voice to desync.
        plain_graph = f"{graph};{voice_join_label}{LOUDNORM_FILTER}[a_out]"
        plain_command = ["ffmpeg", "-y", *seek_args, *decode_args, "-i", str(input_path), *render_inputs, "-filter_complex", plain_graph, "-map", video_label, "-map", "[a_out]", *encoder_args, *build_audio_output_args(True, loudnorm=False), "-shortest", "-movflags", "+faststart", str(output_path)]
        legacy_plain_command = ["ffmpeg", "-y", *seek_args, *decode_args, "-i", str(input_path), *render_inputs, "-filter_complex", graph_without_voice_chain, "-map", video_label, "-map", "0:a?", *encoder_args, *build_audio_output_args(True), "-shortest", "-movflags", "+faststart", str(output_path)]
    else:
        plain_command = ["ffmpeg", "-y", *seek_args, *decode_args, "-i", str(input_path), *render_inputs, "-filter_complex", graph, "-map", video_label, "-map", "0:a?", *encoder_args, *build_audio_output_args(True), "-shortest", "-movflags", "+faststart", str(output_path)]

    def plain_fallback(ok: bool, stderr: str) -> tuple[bool, str]:
        """The plain attempt, plus the legacy command behind it when the
        segment voice chain is in play and its own failure is what
        stopped the plain attempt."""
        if ok or legacy_plain_command is None:
            return ok, stderr
        if not _failure_implicates_audio(stderr):
            return ok, stderr
        logger.warning(
            "EDL segment voice chain failed for %s; retrying with the "
            "legacy plain audio path",
            output_path,
        )
        return _run_render_command(legacy_plain_command)

    audio_plan = build_edl_audio_graph(edl, 1 + render_inputs.count("-i"), assets_dir, audio_source_label=voice_label or "[0:a]")
    used_voice_only = False
    if audio_plan is None and has_retimed:
        # A retimed EDL whose audio fields fail to parse gets no
        # finishing plan; it must still never take the plain path,
        # whose unretimed voice would drift against the retimed
        # picture by the ramp/freeze delta.
        audio_plan = build_edl_audio_graph(
            {**edl, "audio": {}, "sfx_events": []},
            1 + render_inputs.count("-i"),
            assets_dir,
            audio_source_label=audio_source_label,
        )
        used_voice_only = audio_plan is not None
    logger.info("Rendering EDL clip: %s (encoder=%s)", output_path, encoder_name)
    if audio_plan is None:
        return plain_fallback(*_run_render_command(plain_command))
    audio_inputs, audio_graph, audio_label = audio_plan
    command = ["ffmpeg", "-y", *seek_args, *decode_args, "-i", str(input_path), *render_inputs, *audio_inputs, "-filter_complex", f"{graph};{audio_graph}", "-map", video_label, "-map", audio_label, *encoder_args, *build_audio_output_args(True, loudnorm=False), "-shortest", "-movflags", "+faststart", str(output_path)]
    ok, stderr = _run_render_command(command)
    if ok:
        return True, ""
    if not _failure_implicates_audio(stderr):
        # The plain retry re-runs the same video graph and encoder; a
        # failure that does not implicate the audio graph would simply
        # repeat. Fail the clip now with the original error.
        logger.error(
            "EDL render failed for %s with a non-audio error; "
            "not retrying on the plain audio path",
            output_path,
        )
        return False, stderr
    if has_retimed and not used_voice_only:
        # Music/SFX finishing failed on a retimed EDL: retry with the
        # voice-only plan (ramps/freezes preserved) before any plainer
        # path — the plain path would ship the voice unretimed against
        # retimed video, silently out of sync by the ramp/freeze delta.
        voice_only_plan = build_edl_audio_graph(
            {**edl, "audio": {}, "sfx_events": []},
            1 + render_inputs.count("-i"),
            assets_dir,
            audio_source_label=audio_source_label,
        )
        if voice_only_plan is not None:
            v_inputs, v_graph, v_label = voice_only_plan
            v_command = ["ffmpeg", "-y", *seek_args, *decode_args, "-i", str(input_path), *render_inputs, *v_inputs, "-filter_complex", f"{graph};{v_graph}", "-map", video_label, "-map", v_label, *encoder_args, *build_audio_output_args(True, loudnorm=False), "-shortest", "-movflags", "+faststart", str(output_path)]
            logger.warning(
                "EDL audio finishing failed for %s; retrying with the "
                "retimed voice-only plan",
                output_path,
            )
            ok, stderr = _run_render_command(v_command)
            if ok:
                return True, ""
            if not _failure_implicates_audio(stderr):
                return False, stderr
    # Audio finishing must never cost the clip itself: fall back to the
    # plain audio path (e.g. a source with no audio stream). On a
    # crossfade EDL the plain path carries the segment voice chain, so
    # the fallback stays in sync; the legacy command behind it covers
    # sources with no audio stream to chain.
    logger.warning("EDL audio finishing failed for %s; retrying with plain audio path", output_path)
    return plain_fallback(*_run_render_command(plain_command))


def _materialise_range_prestage(
    input_path: Path,
    output_path: Path,
    ranges: list[tuple[float, float]],
    audio_path: Optional[Path] = None,
) -> Optional[Path]:
    """Render crossfaded source ranges into a temp intermediate file.

    Measured (research_notes/sync-diagnosis/fix-report.md): inside the
    full segment graph, the in-graph pre-stage's xfade placed every
    range after the first a full fade late (+150 ms at the stock
    0.15 s range fade) while the acrossfade audio pre-stage compacted
    correctly — the video join's anchoring proved graph-dependent.
    The standalone stitch render (a single-consumer graph, the same
    producer the non-direct path has always used) compacts correctly,
    so crossfaded multi-range EDLs are materialised through it and the
    main graph then runs against the intermediate with the ranges
    stripped — identical inputs to the proven intermediate flow.
    Returns None when the stitch fails; the caller then falls back to
    the in-graph pre-stage rather than losing the clip.
    """
    try:
        fd, temp_name = tempfile.mkstemp(
            prefix=".range-prestage-", suffix=".mp4", dir=str(output_path.parent)
        )
        os.close(fd)
        temp_path = Path(temp_name)
    except OSError as exc:
        logger.warning("Range pre-stage materialisation unavailable: %s", exc)
        return None
    try:
        if render_source_ranges_ffmpeg(
            input_path,
            list(ranges),
            temp_path,
            **({"audio_path": audio_path} if audio_path is not None else {}),
        ):
            return temp_path
        if audio_path is not None:
            # The stem must never cost the clip its intermediate: if the
            # stitch failed with the replacement audio riding along,
            # retry once with the source's own audio before falling back
            # to the in-graph pre-stage like any other stitch failure.
            logger.warning(
                "Range pre-stage stitch failed with the vocals stem; "
                "retrying with the source's own audio",
            )
            if render_source_ranges_ffmpeg(input_path, list(ranges), temp_path):
                return temp_path
        logger.warning(
            "Range pre-stage stitch failed; rendering %s with the in-graph pre-stage",
            output_path,
        )
    except Exception as exc:
        logger.warning("Range pre-stage stitch errored (%s); using in-graph pre-stage", exc)
    temp_path.unlink(missing_ok=True)
    return None


def _stitch_brand_bumpers(output_path: Path, edl: dict[str, Any]) -> None:
    """Stitch the Brand Kit's intro/outro around a finished EDL render.

    Bumpers arrive as bare videos (any size, any loudness, possibly
    silent). Each is normalised to the clip's frame (cover-crop to the
    clip's resolution, clip's frame rate) and audio shape — a silent
    bumper gains a silent track; a loud one is loudness-normalised to
    the same -14 LUFS target the clip itself targets — then everything
    is concatenated in one encode. Any failure leaves the plain render
    untouched: a bumper must never cost the clip.
    """
    bumpers = edl.get("brand_bumpers")
    if not isinstance(bumpers, dict):
        return
    intro = bumpers.get("intro") or {}
    outro = bumpers.get("outro") or {}
    intro_path = Path(str(intro.get("path") or ""))
    outro_path = Path(str(outro.get("path") or ""))
    has_intro = bool(intro.get("path")) and intro_path.is_file()
    has_outro = bool(outro.get("path")) and outro_path.is_file()
    if not has_intro and not has_outro:
        return
    try:
        width, height = ffprobe_video_size(output_path)
        clip_has_audio = ffprobe_has_audio(output_path)
    except Exception as exc:
        logger.warning("Brand bumper stitch skipped (probe failed): %s", exc)
        return

    def bumper_chain(index: int, path: Path, tag: str) -> str:
        video = (
            f"[{index}:v]scale={width}:{height}:force_original_aspect_ratio=increase:flags=lanczos,"
            f"crop={width}:{height},setsar=1,fps={OUTPUT_FPS},format=yuv420p[{tag}v]"
        )
        if clip_has_audio:
            if ffprobe_has_audio(path):
                audio = f"[{index}:a]{LOUDNORM_FILTER},aresample=48000,apad,atrim=duration={{DUR_{tag}}}[{tag}a]"
            else:
                audio = f"anullsrc=channel_layout=stereo:sample_rate=48000,atrim=duration={{DUR_{tag}}}[{tag}a]"
            return f"{video};{audio}"
        return video

    def bumper_duration(path: Path) -> float:
        try:
            from .ffmpeg import ffprobe_duration

            return max(ffprobe_duration(path), 0.1)
        except Exception:
            return 0.1

    parts: list[Path] = []
    if has_intro:
        parts.append(intro_path)
    parts.append(output_path)
    if has_outro:
        parts.append(outro_path)
    tags = ["b0", "main", "b1"] if (has_intro and has_outro) else (
        ["b0", "main"] if has_intro else ["main", "b1"]
    )
    graph_parts: list[str] = []
    concat_inputs: list[str] = []
    for index, (path, tag) in enumerate(zip(parts, tags)):
        if tag == "main":
            graph_parts.append(f"[{index}:v]null[mainv]")
            if clip_has_audio:
                graph_parts.append(f"[{index}:a]anull[maina]")
                concat_inputs.append("[mainv][maina]")
            else:
                concat_inputs.append("[mainv]")
        else:
            chain = bumper_chain(index, path, tag)
            chain = chain.replace("{DUR_" + tag + "}", f"{bumper_duration(path):.3f}")
            graph_parts.append(chain)
            concat_inputs.append(f"[{tag}v][{tag}a]" if clip_has_audio else f"[{tag}v]")
    n = len(parts)
    if clip_has_audio:
        graph_parts.append(f"{''.join(concat_inputs)}concat=n={n}:v=1:a=1[outv][outa]")
        audio_args = ["-map", "[outa]", "-c:a", "aac", "-b:a", "192k", "-ar", "48000"]
    else:
        graph_parts.append(f"{''.join(concat_inputs)}concat=n={n}:v=1:a=0[outv]")
        audio_args = ["-an"]
    stitched = output_path.with_name(f".{output_path.stem}.branded{output_path.suffix}")
    command = ["ffmpeg", "-y"]
    for path in parts:
        command += ["-i", str(path)]
    command += [
        "-filter_complex", ";".join(graph_parts),
        "-map", "[outv]",
        *build_final_video_encode_args(),
        *audio_args,
        "-movflags", "+faststart",
        str(stitched),
    ]
    try:
        result = run_ffmpeg_command(eco_render_command(command), timeout=900)
    except Exception as exc:
        logger.warning("Brand bumper stitch failed to run: %s", exc)
        stitched.unlink(missing_ok=True)
        return
    if result.returncode != 0 or not stitched.is_file():
        logger.warning("Brand bumper stitch failed; keeping the plain render")
        stitched.unlink(missing_ok=True)
        return
    stitched.replace(output_path)
    logger.info("Brand bumpers stitched onto %s (intro=%s, outro=%s)", output_path, has_intro, has_outro)


def _brand_bumpers_configured(edl: dict[str, Any]) -> bool:
    """True when the EDL carries Brand Kit bumper paths to stitch."""
    bumpers = edl.get("brand_bumpers")
    if not isinstance(bumpers, dict):
        return False
    intro = bumpers.get("intro") or {}
    outro = bumpers.get("outro") or {}
    return bool(intro.get("path") or outro.get("path"))


def _timed_stitch_brand_bumpers(
    output_path: Path, edl: dict[str, Any], timings: Optional[dict]
) -> None:
    """_stitch_brand_bumpers, timed into ``timings`` when the pass ran.

    The stitch early-exits when no bumpers are configured; that case is
    not a pass that ran, so no timing entry is recorded for it.
    """
    if timings is None or not _brand_bumpers_configured(edl):
        _stitch_brand_bumpers(output_path, edl)
        return
    started = perf_counter()
    try:
        _stitch_brand_bumpers(output_path, edl)
    finally:
        record_pass_timing(timings, "bumpers_seconds", started, accumulate=True)


def render_edl_clip_ffmpeg(input_path: Path, output_path: Path, edl: dict[str, Any], subtitle_ass_path: Optional[Path] = None, fonts_dir: Optional[Path] = None, assets_dir: Optional[Path] = None, timings: Optional[dict] = None) -> bool:
    """Render one EDL clip; optionally record per-pass timings (F-P3).

    When ``timings`` (a mutable dict) is supplied, it gains REAL measured
    wall-clock entries for the passes that actually ran:
    ``prestage_seconds`` (multi-range crossfade stitch only),
    ``broll_resolve_seconds``, ``encode_seconds`` (accumulated across
    encoder escape-hatch retries), ``bumpers_seconds`` (only when Brand
    Kit bumpers are configured) and ``total_seconds``. It also gains the
    encoder stamp (``encoder``, plus ``nvenc_probe_error`` when the
    NVENC probe failed) from render_encoder_diagnostics, so the task's
    persisted stage timings say which encoder rendered and why the GPU
    was absent. Recording is observation only — see
    common.record_pass_timing; a broken timings dict can never fail the
    render. ``timings=None`` is exactly the pre-F-P3 behaviour.
    """
    # Coerced once at this public entry so the whole chain below — the
    # range pre-stage's output_path.parent, the bumper stitcher's
    # with_name/stem/suffix, the graph builder — can rely on Paths even
    # when a caller handed over plain strings.
    input_path = Path(input_path)
    output_path = Path(output_path)
    started = perf_counter()
    try:
        return _render_edl_clip_ffmpeg_impl(
            input_path, output_path, edl, subtitle_ass_path, fonts_dir,
            assets_dir, timings,
        )
    finally:
        record_pass_timing(timings, "total_seconds", started)
        if timings is not None:
            # Stamp which encoder this process renders with — and why
            # NVENC is absent when it is — onto the same timings dict
            # the task persists (F-P3): a failed GPU probe must be
            # visible in the app's own records, not only in the log.
            try:
                timings.update(render_encoder_diagnostics())
            except Exception:
                pass


def _render_edl_clip_ffmpeg_impl(input_path: Path, output_path: Path, edl: dict[str, Any], subtitle_ass_path: Optional[Path] = None, fonts_dir: Optional[Path] = None, assets_dir: Optional[Path] = None, timings: Optional[dict] = None) -> bool:
    if not edl or not edl.get("segments"):
        return False
    # Crossfaded multi-range EDLs render their range stitch first (see
    # _materialise_range_prestage): the main graph below then consumes
    # the intermediate exactly like the non-direct path does, with the
    # segment tiling, captions and durations all reading the compacted
    # timeline the stitch actually produced.
    ranges = _source_ranges(edl)
    if len(ranges) >= 2 and crossfade_fade_for_ranges(ranges) > 0:
        prestage_started = perf_counter()
        # An opted-in separation task's intermediate carries the vocals
        # stem's audio (see render_source_ranges_ffmpeg): the recursive
        # render below then consumes the intermediate exactly as before,
        # already hearing clean speech. No stamp -> None -> the stitch
        # uses the source's own audio, byte for byte as before.
        prestaged = _materialise_range_prestage(
            input_path, output_path, ranges, audio_path=_edl_vocals_stem(edl)
        )
        record_pass_timing(timings, "prestage_seconds", prestage_started)
        if prestaged is not None:
            stripped_edl = {
                key: value for key, value in edl.items() if key != "source_ranges"
            }
            try:
                return render_edl_clip_ffmpeg(
                    prestaged,
                    output_path,
                    stripped_edl,
                    subtitle_ass_path,
                    fonts_dir,
                    assets_dir,
                    timings=timings,
                )
            finally:
                prestaged.unlink(missing_ok=True)
    # Stock B-roll resolves at EDL planning time (build_pipeline_edl stamps
    # local_path); this call only mops up entries a caller built without
    # planning. Entries already carrying local_path are skipped without any
    # HTTP, and without an API key — or on any fetch failure — this is a
    # no-op; B-roll must never cost the clip.
    broll_started = perf_counter()
    try:
        from .pixabay_broll import resolve_edl_broll

        resolve_edl_broll(edl)
    except Exception as exc:
        logger.warning("B-roll resolution skipped: %s", exc)
    record_pass_timing(timings, "broll_resolve_seconds", broll_started)
    # Seek the input to the first kept moment: without this the graph decodes
    # the whole source from t=0 for every clip (minutes of wasted decode per
    # clip on a long video). Segment/caption timestamps are already relative
    # to the compacted source, so only the source ranges are shifted.
    _, graph_edl, _ = _source_window(edl)
    extra_inputs, graph, encoder_args, video_label = build_edl_filter_graph(graph_edl, input_path, subtitle_ass_path, fonts_dir)
    encoder_name, _ = get_video_encoder()
    # Opt-in hardware decode (ENABLE_NVDEC + realistic probe); [] here
    # means the render below is the software-decode command it has
    # always been, byte for byte.
    use_nvdec = bool(nvdec_input_args())
    encode_started = perf_counter()
    ok, stderr = _render_edl_with_encoder(input_path, output_path, edl, extra_inputs, graph, video_label, encoder_name, encoder_args, assets_dir, use_nvdec=use_nvdec)
    record_pass_timing(timings, "encode_seconds", encode_started, accumulate=True)
    if ok:
        logger.info("EDL render finished: %s (encoder=%s)", output_path, encoder_name)
        _timed_stitch_brand_bumpers(output_path, edl, timings)
        return True
    if use_nvdec:
        nvdec_marker = _nvdec_failure_marker(stderr)
        if nvdec_marker is not None:
            # Escape hatch, mirroring the NVENC one below: the probe said
            # hardware decode works, but a real decode just died with the
            # decoder's own fingerprints on the error. Trust the decode
            # over the probe -- take NVDEC away for the rest of the
            # process and give this clip one more attempt with software
            # decode, instead of letting every later clip repeat it.
            logger.error(
                "EDL render for %s failed with hardware decode (ffmpeg output names %r); "
                "disabling NVDEC for this process and retrying once with software decode",
                output_path,
                nvdec_marker,
            )
            disable_nvdec(f"a production decode failed with an NVDEC error ({nvdec_marker})")
            encode_started = perf_counter()
            ok, stderr = _render_edl_with_encoder(input_path, output_path, edl, extra_inputs, graph, video_label, encoder_name, encoder_args, assets_dir, use_nvdec=False)
            record_pass_timing(timings, "encode_seconds", encode_started, accumulate=True)
            if ok:
                logger.info("EDL render finished: %s (encoder=%s)", output_path, encoder_name)
                _timed_stitch_brand_bumpers(output_path, edl, timings)
                return True
    if encoder_name != "h264_nvenc":
        return False
    marker = _nvenc_failure_marker(stderr)
    if marker is None:
        return False
    # Escape hatch: the probe said NVENC works, but a real encode just
    # died with the encoder's own fingerprints on the error. Trust the
    # encode over the probe -- take NVENC away for the rest of the
    # process and give this clip one more attempt on libx264, instead of
    # letting every later clip in the process repeat the same death.
    logger.error(
        "EDL render for %s failed on h264_nvenc (ffmpeg output names %r); "
        "disabling NVENC for this process and retrying once with libx264",
        output_path,
        marker,
    )
    disable_nvenc(f"a production encode failed with an NVENC error ({marker})")
    cpu_name, cpu_args = get_video_encoder()
    encode_started = perf_counter()
    ok, _ = _render_edl_with_encoder(input_path, output_path, edl, extra_inputs, graph, video_label, cpu_name, cpu_args, assets_dir)
    record_pass_timing(timings, "encode_seconds", encode_started, accumulate=True)
    if ok:
        logger.info("EDL render finished: %s (encoder=%s)", output_path, cpu_name)
        _timed_stitch_brand_bumpers(output_path, edl, timings)
    return ok
