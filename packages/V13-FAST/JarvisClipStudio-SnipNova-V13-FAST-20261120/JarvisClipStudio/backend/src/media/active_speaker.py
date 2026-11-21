"""Active-speaker evidence for reframe target selection.

In a two-person shot the reframe planner picks its crop subject by
presence x face area. That welds the crop to the bigger or closer
face even when the *other* person does all the talking (measured on a
real two-host podcast clip: the area/presence subject logic centred
the non-speaker in 66% of labelled speech samples).

This module produces the missing evidence: per-face *speaking scores*
from a zero-dependency landmark signal. YuNet already localises the
nose tip and mouth corners; the vertical nose->mouth distance (mouth
aperture, normalised by face height) rises and falls with syllables.
A face whose aperture series *correlates with the audio envelope* is
the face producing the audible speech; a face that merely moves —
laughing, nodding along — does not correlate. On the same podcast
clip, windowed aperture/envelope correlation picked the actual
speaker in 71% of labelled samples at this module's cost-capped
grid (480 px wide, 15 fps), against 34% for area/presence.

Everything here is advisory and failure-open:

* gated behind ``ACTIVE_SPEAKER_TARGETING_ENABLED`` (default OFF);
  with the flag off nothing runs and plans are byte-identical;
* the analysis is a separate, span-scoped dense pass (15 fps at
  480 px — sampled frames only, never a full-rate decode), cached in
  a ``<source>.asd_cache.json`` sidecar like the reframe cache;
* any failure returns None and the planner falls back to today's
  area/presence rules exactly.

Model-based active-speaker detection (Light-ASD / TalkNet) was
evaluated and rejected for this app: it needs a parallel 25 fps
S3FD detection pipeline, extra unmaintained dependencies
(python_speech_features, scenedetect, scikit-learn) and hardcodes
CUDA in every entry point — a second analysis stack, not a signal.
See ACTIVE_SPEAKER_REPORT.md.
"""

from __future__ import annotations

import json
import os
import subprocess
import threading
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

import numpy as np

from .common import logger
from .face_detection import open_yunet_detector
from .ffmpeg import ffprobe_duration, ffprobe_video_size, round_to_even

# --- Tunables (calibrated on the Bert two-host fixture; see report) --------

ASD_CACHE_SCHEMA_VERSION = 1

# Dense-pass grid. The frame rate is set by aliasing, not accuracy
# headroom: at 10 fps the sampling Nyquist (5 Hz) sits inside the
# syllabic jaw-motion band, so the correlation swings 52-72% with
# the sampling phase; at 15 fps (Nyquist 7.5 Hz) both phases measure
# ~71% on the fixture. 15 fps — 5x the frames of the 3 fps box pass,
# over the keep ranges only — is the cheapest phase-stable grid.
ASD_SAMPLE_FPS = 15.0
ASD_PROC_WIDTH = 480

# Speaking-score windows: 2 s of aperture/envelope correlation,
# hopping 1 s. On the fixture at 15 fps, averaged over both sampling
# phases: 1.5 s -> 59%, 2 s -> 71%, 3 s -> 68% — and 2 s is also the
# most phase-consistent (0.4 pt spread vs 12-13 pt).
ASD_WINDOW_SECONDS = 2.0
ASD_WINDOW_HOP_SECONDS = 1.0

# A window only biases target selection when the winner's correlation
# clears this bar; below it the window says "no evidence" and the
# planner's own rules decide, untouched.
ASD_MIN_CORRELATION = 0.25

# Winner weight = 1 + ASD_BIAS_STRENGTH * margin (the winner's
# correlation minus the runner-up's), so a clearly identified
# speaker's presence counts up to 3x in the subject series' suffix
# scores. Bounded on purpose: speaking evidence reorders preference
# among present faces, it never conjures a subject the face tracks
# did not ground.
ASD_BIAS_STRENGTH = 2.0

# A track needs aperture readings in at least this share of a
# window's samples (and at least MIN_WINDOW_PAIRS of them) before
# its correlation is trusted.
ASD_MIN_WINDOW_COVERAGE = 0.6
ASD_MIN_WINDOW_PAIRS = 8

# Faces narrower than this (source px at analysis scale) are wall
# posters and picture frames, not speakers.
ASD_MIN_FACE_WIDTH_FRAC = 0.10

_ENVELOPE_HZ = 100

_ASD_LOCK = threading.Lock()

Box = Tuple[float, float, float, float]
# One dense sample: (source_time, audio RMS at that time,
# [(box, mouth aperture), ...]) with boxes in source pixels.
SpeakingSample = Tuple[float, float, List[Tuple[Box, float]]]


def active_speaker_enabled() -> bool:
    """ACTIVE_SPEAKER_TARGETING_ENABLED=true (or 1/on/yes) opts in.

    Default OFF — an opt-in like separation's: the extra dense pass
    only exists where the deployment asked for it.
    """
    return os.environ.get(
        "ACTIVE_SPEAKER_TARGETING_ENABLED", ""
    ).strip().lower() in {"1", "true", "on", "yes"}


# --- Signal math (pure) ------------------------------------------------------


def mouth_aperture(box: Box, landmarks: Sequence[Tuple[float, float]]) -> Optional[float]:
    """Nose->mouth distance normalised by face height.

    ``landmarks`` are YuNet's five points (right eye, left eye, nose
    tip, right mouth corner, left mouth corner). The ratio is
    scale-free, so it is comparable across faces and frame sizes.
    """
    if len(landmarks) < 5 or box[3] <= 0:
        return None
    nose = landmarks[2]
    mouth_mid = (
        (landmarks[3][0] + landmarks[4][0]) / 2.0,
        (landmarks[3][1] + landmarks[4][1]) / 2.0,
    )
    return float(np.hypot(nose[0] - mouth_mid[0], nose[1] - mouth_mid[1]) / box[3])


def pearson(xs: Sequence[float], ys: Sequence[float]) -> Optional[float]:
    """Pearson correlation, or None when either series is flat/short."""
    if len(xs) != len(ys) or len(xs) < 3:
        return None
    x = np.asarray(xs, dtype=np.float64)
    y = np.asarray(ys, dtype=np.float64)
    if float(np.std(x)) < 1e-9 or float(np.std(y)) < 1e-9:
        return None
    return float(np.corrcoef(x, y)[0, 1])


def windowed_correlations(
    times: Sequence[float],
    apertures: Dict[Any, Sequence[Optional[float]]],
    envelope: Sequence[float],
    *,
    window: float = ASD_WINDOW_SECONDS,
    hop: float = ASD_WINDOW_HOP_SECONDS,
) -> List[Tuple[float, float, Dict[Any, float]]]:
    """Per-window aperture/envelope correlation per track key.

    ``times``/``envelope`` are the dense sample grid; ``apertures``
    maps a track key to its aperture (or None) per sample. Returns
    [(t0, t1, {key: corr})] for windows where at least two tracks
    produced a trustworthy correlation.
    """
    out: List[Tuple[float, float, Dict[Any, float]]] = []
    if not times:
        return out
    start = float(times[0])
    last = float(times[-1])
    t0 = start
    while t0 <= last:
        t1 = t0 + window
        idx = [i for i, t in enumerate(times) if t0 <= t < t1]
        scores: Dict[Any, float] = {}
        if len(idx) >= ASD_MIN_WINDOW_PAIRS:
            env_vals = [envelope[i] for i in idx]
            env_varies = float(np.std(env_vals)) >= 1e-9
            for key, series in apertures.items():
                pairs = [
                    (series[i], envelope[i])
                    for i in idx
                    if series[i] is not None
                ]
                if len(pairs) < ASD_MIN_WINDOW_PAIRS:
                    continue
                if len(pairs) < ASD_MIN_WINDOW_COVERAGE * len(idx):
                    continue
                corr = pearson([p[0] for p in pairs], [p[1] for p in pairs])
                if corr is None and env_varies:
                    # A face whose aperture never moves while speech
                    # is audible is evidence too: it is NOT the source.
                    # Score it 0 so the speaker's correlation has a
                    # runner-up to margin against.
                    ap_vals = [p[0] for p in pairs]
                    if float(np.std(ap_vals)) < 1e-9:
                        corr = 0.0
                if corr is not None:
                    scores[key] = corr
        if len(scores) >= 2:
            out.append((t0, min(t1, last), scores))
        t0 += hop
    return out


# --- Audio envelope ------------------------------------------------------------


def _span_envelope(
    source_path: Path, start: float, duration: float
) -> Optional[np.ndarray]:
    """100 Hz RMS envelope of one span (16 kHz mono decode)."""
    if duration <= 0:
        return None
    command = ["ffmpeg", "-v", "error"]
    if start > 0:
        command += ["-ss", f"{start:.3f}"]
    command += [
        "-i", str(source_path), "-t", f"{duration:.3f}",
        "-vn", "-ac", "1", "-ar", "16000", "-f", "s16le", "-",
    ]
    try:
        proc = subprocess.run(command, capture_output=True, timeout=max(60.0, duration * 4))
    except Exception as exc:
        logger.warning("Active-speaker envelope decode failed: %s", exc)
        return None
    if proc.returncode != 0 or not proc.stdout:
        return None
    pcm = np.frombuffer(proc.stdout, dtype=np.int16).astype(np.float32) / 32768.0
    hop = 16000 // _ENVELOPE_HZ
    n = len(pcm) // hop
    if n == 0:
        return None
    return np.sqrt((pcm[: n * hop].reshape(n, hop) ** 2).mean(axis=1))


# --- Dense landmark pass -------------------------------------------------------


def _span_samples(
    source_path: Path, start: float, duration: float, width: int, height: int
) -> List[SpeakingSample]:
    """One span of the dense pass: faces + apertures + envelope."""
    envelope = _span_envelope(source_path, start, duration)
    if envelope is None:
        return []
    proc_w = round_to_even(min(ASD_PROC_WIDTH, width))
    proc_h = round_to_even(max(2, int(round(proc_w * height / width))))
    frame_bytes = proc_w * proc_h * 3
    command = ["ffmpeg", "-v", "error"]
    if start > 0:
        command += ["-ss", f"{start:.3f}"]
    command += ["-an", "-sn", "-i", str(source_path)]
    command += [
        "-t", f"{duration:.3f}",
        "-vf", f"fps={ASD_SAMPLE_FPS:.3f},scale={proc_w}:{proc_h}",
        "-pix_fmt", "bgr24", "-f", "rawvideo", "-threads", "0", "-",
    ]
    detector = open_yunet_detector(max_input_width=proc_w)
    if detector is None:
        return []
    scale_x = width / proc_w
    scale_y = height / proc_h
    min_face_w = ASD_MIN_FACE_WIDTH_FRAC * proc_w
    samples: List[SpeakingSample] = []
    try:
        proc = subprocess.Popen(
            command, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL
        )
    except Exception as exc:
        logger.warning("Active-speaker decode spawn failed: %s", exc)
        return []
    idx = 0
    try:
        while True:
            raw = proc.stdout.read(frame_bytes) if proc.stdout else b""
            if not raw or len(raw) < frame_bytes:
                break
            frame = np.frombuffer(raw, dtype=np.uint8).reshape(proc_h, proc_w, 3)
            t = start + idx / ASD_SAMPLE_FPS
            env_i = int(round((idx / ASD_SAMPLE_FPS) * _ENVELOPE_HZ))
            env_v = float(envelope[env_i]) if env_i < len(envelope) else 0.0
            faces: List[Tuple[Box, float]] = []
            for box, landmarks in detector.detect_with_landmarks(frame):
                if box[2] < min_face_w:
                    continue
                aperture = mouth_aperture(box, landmarks)
                if aperture is None:
                    continue
                faces.append(
                    (
                        (
                            box[0] * scale_x,
                            box[1] * scale_y,
                            box[2] * scale_x,
                            box[3] * scale_y,
                        ),
                        aperture,
                    )
                )
            samples.append((t, env_v, faces))
            idx += 1
    finally:
        try:
            if proc.stdout:
                proc.stdout.close()
        except Exception:
            pass
        proc.wait()
        detector.close()
    if proc.returncode != 0:
        logger.warning("Active-speaker decoder failed; span evidence discarded")
        return []
    return samples


# --- Sidecar cache -------------------------------------------------------------


def _cache_path(source_path: Path) -> Path:
    return source_path.with_suffix(source_path.suffix + ".asd_cache.json")


def _source_stamp(source_path: Path) -> Optional[Tuple[int, int]]:
    try:
        st = os.stat(source_path)
    except OSError:
        return None
    return (st.st_size, st.st_mtime_ns)


def _merge_spans(
    spans: Sequence[Tuple[float, float]]
) -> List[Tuple[float, float]]:
    merged: List[Tuple[float, float]] = []
    for start, end in sorted(spans):
        if merged and start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))
    return merged


def _uncovered(
    wanted: Sequence[Tuple[float, float]], covered: Sequence[Tuple[float, float]]
) -> List[Tuple[float, float]]:
    missing: List[Tuple[float, float]] = []
    for start, end in wanted:
        cursor = start
        for c_start, c_end in covered:
            if c_end <= cursor:
                continue
            if c_start > cursor:
                missing.append((cursor, min(c_start, end)))
            cursor = max(cursor, c_end)
            if cursor >= end:
                break
        if cursor < end:
            missing.append((cursor, end))
    return [(s, e) for s, e in missing if e - s > 0.05]


def _load_cache(source_path: Path) -> Optional[Dict[str, Any]]:
    path = _cache_path(source_path)
    stamp = _source_stamp(source_path)
    if stamp is None or not path.is_file():
        return None
    try:
        payload = json.loads(path.read_text())
    except Exception:
        return None
    if payload.get("schema") != ASD_CACHE_SCHEMA_VERSION:
        return None
    if tuple(payload.get("stamp") or ()) != stamp:
        return None
    if float(payload.get("sample_fps") or 0) != ASD_SAMPLE_FPS:
        return None
    return payload


def _save_cache(source_path: Path, payload: Dict[str, Any]) -> None:
    try:
        _cache_path(source_path).write_text(json.dumps(payload))
    except Exception as exc:
        logger.debug("Active-speaker cache save failed: %s", exc)


def get_speaking_analysis(
    source_path: Path, spans: Sequence[Tuple[float, float]]
) -> Optional[Dict[str, Any]]:
    """Dense speaking evidence for ``spans`` of the source, cached.

    Returns {"samples": [(t, env_rms, [(box, aperture), ...]), ...]}
    in source pixels / source time, or None when nothing could be
    analysed. Callers treat None (and any gap) as "no evidence" and
    keep today's targeting rules.
    """
    source_path = Path(source_path)
    with _ASD_LOCK:
        try:
            duration = ffprobe_duration(source_path)
        except Exception:
            duration = 0.0
        if duration <= 0:
            return None
        width, height = ffprobe_video_size(source_path)
        if width <= 0 or height <= 0:
            return None
        wanted = _merge_spans(
            [(max(0.0, s), min(duration, e)) for s, e in spans if e > s]
        )
        if not wanted:
            return None
        cache = _load_cache(source_path)
        by_time: Dict[float, SpeakingSample] = {}
        covered: List[Tuple[float, float]] = []
        if cache is not None:
            covered = [tuple(map(float, s)) for s in cache.get("covered_spans") or []]
            for entry in cache.get("samples") or []:
                try:
                    t = round(float(entry["t"]), 3)
                    faces = [
                        (tuple(float(v) for v in f[:4]), float(f[4]))
                        for f in entry.get("faces") or []
                    ]
                    by_time[t] = (t, float(entry.get("env") or 0.0), faces)
                except (TypeError, ValueError, IndexError):
                    continue
        for start, end in _uncovered(wanted, covered):
            logger.info(
                "Analyzing source span %.1f-%.1fs of %s for active speakers",
                start, end, source_path,
            )
            try:
                fresh = _span_samples(source_path, start, end - start, width, height)
            except Exception as exc:
                logger.warning("Active-speaker analysis failed for %s: %s", source_path, exc)
                continue
            if not fresh:
                continue
            for t, env_v, faces in fresh:
                by_time[round(float(t), 3)] = (float(t), env_v, faces)
            covered = _merge_spans(covered + [(start, end)])
        if not by_time:
            return None
        samples = [by_time[t] for t in sorted(by_time)]
        stamp = _source_stamp(source_path)
        _save_cache(
            source_path,
            {
                "schema": ASD_CACHE_SCHEMA_VERSION,
                "stamp": list(stamp) if stamp else None,
                "sample_fps": ASD_SAMPLE_FPS,
                "covered_spans": [list(s) for s in covered],
                "samples": [
                    {
                        "t": round(t, 3),
                        "env": round(env_v, 5),
                        "faces": [
                            [round(v, 1) for v in box] + [round(ap, 4)]
                            for box, ap in faces
                        ],
                    }
                    for t, env_v, faces in samples
                ],
            },
        )
        return {"samples": samples}
