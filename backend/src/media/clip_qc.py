"""Per-clip quality control for finished renders (Final Directive Part I).

Every rendered clip is inspected before it is presented as done. The checks
are measurements, not vibes: each returns pass / warn / fail / skip plus the
numbers it measured, and the report is stored on the clip row so a bad render
is visible instead of silently shipped.

The lesson this module exists for: on 2026-11-07 the pipeline shipped clips
with no background music at all and nobody noticed until the user watched
one. The music check below is the one whose absence caused that — when a
profile or an explicit user pick says a clip should have music, QC measures
the audio level inside the speech gaps and warns loudly when those gaps are
digital silence.

Hard failures (the file is not a watchable clip: missing, unprobeable,
wrong duration, wrong resolution, all-black, frozen-and-silent) trigger
exactly one automatic re-render in the pipeline seam; warnings never do.
Caption synchronisation and face-cropping checks are intentionally NOT here
— see docs/qc.md for the honest limits.
"""

from __future__ import annotations

import hashlib
import subprocess
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional, Sequence

import numpy as np

from .common import logger
from .ffmpeg import ffprobe_duration, ffprobe_video_size, select_audio_stream_index
from .gap_activity import _window_levels_db

# --- Thresholds (see docs/qc.md for how each was chosen) -------------------

DURATION_TOLERANCE_SECONDS = 0.5

# Mean luma (0-255, sampled at 64x36 gray) at or below this counts as a
# near-black frame: a true black frame measures 0-2 even after H.264 noise,
# while the darkest legitimate scene content sits well above it.
BLACK_LUMA_MAX = 10.0
BLACK_SAMPLE_FRACTIONS = (0.10, 0.25, 0.50, 0.75, 0.90)

# Largest frame-to-frame mean absolute difference (0-255) across the whole
# sampled clip for the picture to count as frozen. A static slate measures
# ~0; any real camera or screen motion is far above 1.
FROZEN_DIFF_MAX = 1.0
FROZEN_SAMPLE_FPS = 2
FROZEN_MAX_SAMPLE_SECONDS = 300.0

# Overall RMS at or below this (dBFS) is digital silence: loudnorm targets
# -14 LUFS, so real content — even a quiet room tone — sits far above -55.
AUDIO_SILENCE_RMS_DB = -55.0

# Music presence is judged inside speech gaps: stretches where the level
# drops at least MUSIC_SPEECH_DROP_DB under the speech level. Cleanup keeps
# ~0.4s of silence at junctions, so a 0.3s run is a real gap, and a clip
# needs ~0.8s of total gap before the judgement is meaningful.
MUSIC_SPEECH_DROP_DB = 18.0
MUSIC_GAP_RUN_MIN_SECONDS = 0.3
MUSIC_GAP_TOTAL_MIN_SECONDS = 0.8
# Gap RMS at or below this means the gaps carry no music bed: a ducked bed
# under silence plays at the profile volume (typically -25..-40 dBFS RMS),
# while a music-less render leaves the gaps at digital silence.
MUSIC_GAP_SILENCE_DB = -50.0
# Fallback when a clip has no measurable gaps: the 5th-percentile window
# level at or above this is consistent with a music bed under the speech.
MUSIC_FLOOR_DB = -42.0
# Below this speech reference the clip is too quiet overall to judge music.
MUSIC_SPEECH_REFERENCE_MIN_DB = -45.0

# Two clips of one task whose source ranges overlap by at least this IoU
# are the same moment picked twice.
DUPLICATE_IOU_WARN = 0.5

_FRAME_W = 64
_FRAME_H = 36

# Matches gap_activity's 50ms windowing (its levels helper is reused).
_AUDIO_WINDOW_SECONDS = 0.05

_VERTICAL_FORMATS = {"vertical", "vertical_fit", "vertical_pan", "vertical_split"}
_VERTICAL_WIDTH = 1080
_VERTICAL_HEIGHT = 1920


@dataclass
class ClipQcContext:
    """What a correct render of this clip was supposed to look like."""

    expected_duration: Optional[float] = None
    output_format: str = "vertical"
    # Plan cap applied after rendering (cap_video_short_side); the finished
    # file's short side must match it when set.
    max_short_side: Optional[int] = None
    # Source-timeline range (seconds) the clip was cut from, for duplicate
    # detection against its siblings.
    source_start: Optional[float] = None
    source_end: Optional[float] = None
    # Music expectation: an explicit user pick (music_override) or a profile
    # mood with volume > 0. ``music_label`` names what was expected (track
    # filename or mood) so the warning can say exactly what is missing.
    music_expected: bool = False
    music_label: Optional[str] = None
    # Other clips of the same task: {"start", "end", "sha256", "label"}.
    siblings: Sequence[dict[str, Any]] = field(default_factory=tuple)


def _check(name: str, status: str, detail: str, **evidence: Any) -> dict[str, Any]:
    return {"name": name, "status": status, "detail": detail, "evidence": evidence}


def _decode(command: list[str], timeout: int = 120) -> bytes:
    result = subprocess.run(command, capture_output=True, timeout=timeout)
    if result.returncode != 0:
        raise RuntimeError(result.stderr.decode("utf-8", "replace")[-500:])
    return result.stdout


def _gray_frame(path: Path, at_sec: float) -> Optional[np.ndarray]:
    """One frame at ``at_sec`` as a 64x36 float gray array, or None."""
    try:
        raw = _decode(
            [
                "ffmpeg", "-v", "error", "-ss", f"{max(0.0, at_sec):.3f}",
                "-i", str(path), "-frames:v", "1",
                "-vf", f"scale={_FRAME_W}:{_FRAME_H},format=gray",
                "-f", "rawvideo", "-",
            ],
            timeout=60,
        )
    except Exception:
        return None
    frame = np.frombuffer(raw, dtype=np.uint8)
    if frame.size < _FRAME_W * _FRAME_H:
        return None
    return frame[: _FRAME_W * _FRAME_H].reshape(_FRAME_H, _FRAME_W).astype(np.float32)


def _gray_sequence_max_diff(path: Path, duration: float) -> Optional[float]:
    """Largest frame-to-frame change in the clip, or None if unmeasurable."""
    sample_seconds = min(duration, FROZEN_MAX_SAMPLE_SECONDS)
    raw = _decode(
        [
            "ffmpeg", "-v", "error", "-i", str(path),
            "-t", f"{sample_seconds:.3f}", "-an",
            "-vf", f"fps={FROZEN_SAMPLE_FPS},scale={_FRAME_W}:{_FRAME_H},format=gray",
            "-f", "rawvideo", "-",
        ],
        timeout=180,
    )
    frames = np.frombuffer(raw, dtype=np.uint8)
    count = len(frames) // (_FRAME_W * _FRAME_H)
    if count < 2:
        return None
    frames = frames[: count * _FRAME_W * _FRAME_H].reshape(
        count, _FRAME_H, _FRAME_W
    ).astype(np.float32)
    changes = np.abs(np.diff(frames, axis=0)).mean(axis=(1, 2))
    return float(changes.max()) if len(changes) else None


def _power_mean_db(levels: np.ndarray) -> float:
    if len(levels) == 0:
        return -120.0
    return float(10.0 * np.log10(np.mean(np.power(10.0, levels / 10.0))))


def check_duration(actual: float, expected: Optional[float]) -> dict[str, Any]:
    if expected is None or expected <= 0:
        return _check(
            "duration", "skip", "No expected duration recorded for this clip",
            actual_seconds=round(actual, 3),
        )
    delta = abs(actual - expected)
    status = "pass" if delta <= DURATION_TOLERANCE_SECONDS else "fail"
    return _check(
        "duration", status,
        f"Clip is {actual:.2f}s, expected {expected:.2f}s "
        f"(delta {delta:.2f}s, tolerance {DURATION_TOLERANCE_SECONDS}s)",
        actual_seconds=round(actual, 3), expected_seconds=round(expected, 3),
        delta_seconds=round(delta, 3),
    )


def check_resolution(
    width: int, height: int, context: ClipQcContext
) -> dict[str, Any]:
    evidence = {"width": width, "height": height, "output_format": context.output_format}
    if context.output_format == "original":
        return _check(
            "resolution", "pass",
            f"Original format keeps the source size ({width}x{height})",
            **evidence,
        )
    if context.output_format not in _VERTICAL_FORMATS:
        return _check(
            "resolution", "skip",
            f"No fixed canvas known for format '{context.output_format}'",
            **evidence,
        )
    expected_short = _VERTICAL_WIDTH
    if context.max_short_side:
        expected_short = min(expected_short, int(context.max_short_side))
    expected_long = round(_VERTICAL_HEIGHT * expected_short / _VERTICAL_WIDTH)
    short_side, long_side = min(width, height), max(width, height)
    aspect = long_side / short_side if short_side else 0.0
    expected_aspect = _VERTICAL_HEIGHT / _VERTICAL_WIDTH
    aspect_ok = abs(aspect - expected_aspect) / expected_aspect <= 0.02
    size_ok = abs(short_side - expected_short) <= 4
    evidence.update(
        expected_width=expected_short, expected_height=expected_long,
        aspect=round(aspect, 4),
    )
    if aspect_ok and size_ok:
        return _check(
            "resolution", "pass",
            f"Clip is {width}x{height}, expected {expected_short}x{expected_long}",
            **evidence,
        )
    return _check(
        "resolution", "fail",
        f"Clip is {width}x{height}, expected {expected_short}x{expected_long} "
        f"(9:16 canvas)",
        **evidence,
    )


def check_black_frames(path: Path, duration: float) -> dict[str, Any]:
    lumas: list[Optional[float]] = []
    for fraction in BLACK_SAMPLE_FRACTIONS:
        frame = _gray_frame(path, duration * fraction)
        lumas.append(None if frame is None else float(frame.mean()))
    measured = [value for value in lumas if value is not None]
    evidence = {
        "sample_luma": [None if v is None else round(v, 2) for v in lumas],
        "black_threshold": BLACK_LUMA_MAX,
    }
    if not measured:
        return _check("black_frames", "skip", "Could not extract sample frames", **evidence)
    black_count = sum(1 for value in measured if value <= BLACK_LUMA_MAX)
    if black_count == len(measured):
        return _check(
            "black_frames", "fail",
            f"All {len(measured)} sampled frames are near-black "
            f"(mean luma <= {BLACK_LUMA_MAX})",
            **evidence,
        )
    if black_count:
        return _check(
            "black_frames", "warn",
            f"{black_count} of {len(measured)} sampled frames are near-black",
            **evidence,
        )
    return _check(
        "black_frames", "pass",
        f"No near-black frames in {len(measured)} samples",
        **evidence,
    )


def check_audio(
    levels: Optional[np.ndarray], has_audio_stream: bool
) -> tuple[dict[str, Any], bool]:
    """Audio presence + level. Returns (check, is_silent)."""
    if not has_audio_stream:
        return _check(
            "audio", "warn", "Clip has no audio stream",
            has_audio_stream=False,
        ), True
    if levels is None or len(levels) == 0:
        return _check(
            "audio", "warn", "Audio stream exists but could not be measured",
            has_audio_stream=True,
        ), True
    rms_db = _power_mean_db(levels)
    silent = rms_db <= AUDIO_SILENCE_RMS_DB
    if silent:
        return _check(
            "audio", "warn",
            f"Audio is digital silence (overall RMS {rms_db:.1f} dBFS)",
            has_audio_stream=True, rms_db=round(rms_db, 2),
        ), True
    return _check(
        "audio", "pass",
        f"Audio present (overall RMS {rms_db:.1f} dBFS)",
        has_audio_stream=True, rms_db=round(rms_db, 2),
    ), False


def check_music(levels: Optional[np.ndarray], context: ClipQcContext) -> dict[str, Any]:
    """Judge an expected music bed by the level inside speech gaps.

    Only called when the context says music was intended. A clip whose gaps
    are digital silence has no audible bed no matter what the EDL asked
    for — that is the 2026-11-07 failure this check exists to catch.
    """
    label = context.music_label or "music"
    base = {"expected_track": label}
    if levels is None or len(levels) == 0:
        return _check(
            "music", "skip",
            f"Music expected ({label}) but the audio could not be measured",
            **base,
        )
    speech_db = float(np.percentile(levels, 75))
    if speech_db < MUSIC_SPEECH_REFERENCE_MIN_DB:
        return _check(
            "music", "skip",
            f"Music expected ({label}) but the clip is too quiet overall "
            f"(speech reference {speech_db:.1f} dB) to judge the gaps",
            speech_reference_db=round(speech_db, 2), **base,
        )
    gap_threshold = speech_db - MUSIC_SPEECH_DROP_DB
    is_gap = levels < gap_threshold
    # Collect the windows belonging to qualifying gap runs.
    gap_indices: list[int] = []
    run_start: Optional[int] = None
    min_run = max(1, int(MUSIC_GAP_RUN_MIN_SECONDS / _AUDIO_WINDOW_SECONDS))
    for index, flag in enumerate(is_gap):
        if flag and run_start is None:
            run_start = index
        elif not flag and run_start is not None:
            if index - run_start >= min_run:
                gap_indices.extend(range(run_start, index))
            run_start = None
    if run_start is not None and len(is_gap) - run_start >= min_run:
        gap_indices.extend(range(run_start, len(is_gap)))
    gap_seconds = len(gap_indices) * _AUDIO_WINDOW_SECONDS
    if gap_seconds >= MUSIC_GAP_TOTAL_MIN_SECONDS:
        gap_rms = _power_mean_db(levels[np.array(gap_indices)])
        if gap_rms <= MUSIC_GAP_SILENCE_DB:
            return _check(
                "music", "warn",
                f"MUSIC MISSING: expected {label}, but the speech gaps are "
                f"silent (gap RMS {gap_rms:.1f} dB across {gap_seconds:.1f}s "
                f"of gaps) — the render has no audible music bed",
                gap_rms_db=round(gap_rms, 2), gap_seconds=round(gap_seconds, 2),
                speech_reference_db=round(speech_db, 2), **base,
            )
        return _check(
            "music", "pass",
            f"Music bed audible in speech gaps (gap RMS {gap_rms:.1f} dB "
            f"across {gap_seconds:.1f}s of gaps)",
            gap_rms_db=round(gap_rms, 2), gap_seconds=round(gap_seconds, 2),
            speech_reference_db=round(speech_db, 2), **base,
        )
    floor_db = float(np.percentile(levels, 5))
    if floor_db >= MUSIC_FLOOR_DB:
        return _check(
            "music", "pass",
            f"No silent gaps to judge; the audio floor ({floor_db:.1f} dB) "
            f"is consistent with a music bed under the speech",
            floor_db=round(floor_db, 2), gap_seconds=round(gap_seconds, 2),
            speech_reference_db=round(speech_db, 2), **base,
        )
    return _check(
        "music", "skip",
        f"Music expected ({label}) but the clip has only "
        f"{gap_seconds:.1f}s of speech gaps — not enough to judge whether "
        f"a bed is playing",
        floor_db=round(floor_db, 2), gap_seconds=round(gap_seconds, 2),
        speech_reference_db=round(speech_db, 2), **base,
    )


def check_frozen(
    path: Path, duration: float, audio_silent: bool
) -> dict[str, Any]:
    try:
        max_diff = _gray_sequence_max_diff(path, duration)
    except Exception as exc:
        return _check("frozen_frame", "skip", f"Could not sample frames: {exc}")
    if max_diff is None:
        return _check(
            "frozen_frame", "skip", "Too few frames to judge motion",
        )
    evidence = {"max_frame_diff": round(max_diff, 3), "frozen_threshold": FROZEN_DIFF_MAX}
    if max_diff > FROZEN_DIFF_MAX:
        return _check(
            "frozen_frame", "pass",
            f"Picture moves (max frame-to-frame change {max_diff:.2f})",
            **evidence,
        )
    if audio_silent:
        return _check(
            "frozen_frame", "fail",
            f"Picture never changes (max frame-to-frame change "
            f"{max_diff:.2f}) and the audio is silent — this is a frozen, "
            f"silent render, not a watchable clip",
            **evidence,
        )
    return _check(
        "frozen_frame", "warn",
        f"Picture never changes (max frame-to-frame change {max_diff:.2f}); "
        f"static content can be legitimate, so this is a warning only",
        **evidence,
    )


def _range_iou(
    a_start: float, a_end: float, b_start: float, b_end: float
) -> float:
    intersection = max(0.0, min(a_end, b_end) - max(a_start, b_start))
    union = max(a_end, b_end) - min(a_start, b_start)
    return intersection / union if union > 0 else 0.0


def check_duplicate(
    context: ClipQcContext, file_sha256: Optional[str]
) -> dict[str, Any]:
    for sibling in context.siblings:
        label = sibling.get("label") or "a sibling clip"
        sibling_hash = sibling.get("sha256")
        if file_sha256 and sibling_hash and file_sha256 == sibling_hash:
            return _check(
                "duplicate", "warn",
                f"File is byte-identical to {label} (same SHA-256)",
                duplicate_of=label, match="sha256",
            )
        start, end = sibling.get("start"), sibling.get("end")
        if (
            context.source_start is not None and context.source_end is not None
            and start is not None and end is not None
        ):
            iou = _range_iou(
                context.source_start, context.source_end, float(start), float(end)
            )
            if iou >= DUPLICATE_IOU_WARN:
                return _check(
                    "duplicate", "warn",
                    f"Source range overlaps {label} by {iou:.0%} (IoU) — "
                    f"the same moment was likely picked twice",
                    duplicate_of=label, match="source_range",
                    iou=round(iou, 3),
                )
    return _check("duplicate", "pass", "No duplicate sibling clip detected")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def run_clip_qc(path: Path, context: ClipQcContext) -> dict[str, Any]:
    """Run every check over one rendered clip file and build the report.

    Pure with respect to the database and the pipeline: the caller stores
    the returned report. Never raises for a bad file — a bad file is the
    input this function exists to describe.
    """
    path = Path(path)
    checked_at = datetime.now(timezone.utc).isoformat()
    if not path.is_file():
        return {
            "version": 1,
            "status": "fail",
            "hard_fail": True,
            "checked_at": checked_at,
            "file": {"path": str(path)},
            "checks": [
                _check("file", "fail", f"Rendered file is missing: {path}")
            ],
        }

    try:
        duration = ffprobe_duration(path)
    except Exception:
        duration = 0.0
    try:
        width, height = ffprobe_video_size(path)
    except Exception:
        width, height = 0, 0
    if duration <= 0 or width <= 0 or height <= 0:
        return {
            "version": 1,
            "status": "fail",
            "hard_fail": True,
            "checked_at": checked_at,
            "file": {"path": str(path), "duration": duration, "width": width, "height": height},
            "checks": [
                _check(
                    "file", "fail",
                    "File cannot be probed (corrupt or not a video): "
                    f"duration={duration}, size={width}x{height}",
                    duration=duration, width=width, height=height,
                )
            ],
        }

    file_sha256 = _sha256(path)
    audio_stream = select_audio_stream_index(path)
    levels: Optional[np.ndarray] = None
    if audio_stream is not None:
        try:
            levels = _window_levels_db(path, audio_stream, 0.0, duration)
        except Exception as exc:
            logger.warning("QC audio measurement failed for %s: %s", path, exc)
            levels = None

    checks = [
        _check(
            "file", "pass", "File exists and probes as a valid video",
            duration=round(duration, 3), width=width, height=height,
        ),
        check_duration(duration, context.expected_duration),
        check_resolution(width, height, context),
    ]
    audio_check, audio_silent = check_audio(levels, audio_stream is not None)
    checks.append(audio_check)
    if context.music_expected:
        checks.append(check_music(levels, context))
    checks.append(check_black_frames(path, duration))
    checks.append(check_frozen(path, duration, audio_silent))
    checks.append(check_duplicate(context, file_sha256))

    statuses = {entry["status"] for entry in checks}
    overall = "fail" if "fail" in statuses else "warn" if "warn" in statuses else "pass"
    return {
        "version": 1,
        "status": overall,
        "hard_fail": overall == "fail",
        "checked_at": checked_at,
        "file": {
            "path": str(path),
            "sha256": file_sha256,
            "duration": round(duration, 3),
            "width": width,
            "height": height,
        },
        "checks": checks,
    }
