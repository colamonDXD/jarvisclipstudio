"""Scored cover-frame selection for deliverable bundles.

Historically a clip's cover image was grabbed at one fixed timestamp:
the EDL's peak-energy marker, or the very first frame when the EDL
carried none. That frame is often a transition, a blur, a black flash
or an empty shot. This module samples candidate frames across the
clip and scores them with machinery the codebase already has — YuNet
face detection (Haar fallback, the reframing stack's order), a
Laplacian-variance sharpness measure and an exposure sanity check —
and returns a better timestamp only when a candidate is *clearly*
better than the historical choice.

The fallback contract is hard: any error, an undecodable file, no
candidates, or no clear winner returns the historical timestamp
unchanged, so a bundle build can never fail or stall because of cover
selection. Sampling is capped (``MAX_CANDIDATES`` grid frames plus the
historical timestamp) to keep the added cost bounded.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, List, Optional, Sequence, Tuple

import cv2
import numpy as np

logger = logging.getLogger(__name__)

# Candidate grid: roughly one frame per second, capped so a long clip
# cannot turn cover selection into a second analysis pass.
MAX_CANDIDATES = 24
MIN_SAMPLE_STEP_SECONDS = 1.0

# Frames are downscaled to this width for scoring: every candidate is
# measured on the same scale, and face detection keeps working (the
# YuNet wrapper downscales to 960 wide internally anyway).
ANALYSIS_WIDTH = 480

# Exposure sanity: mean luma outside this band is a near-black or
# near-white frame (fades, flashes, letterbox-only shots).
NEAR_BLACK_LUMA = 10.0
NEAR_WHITE_LUMA = 245.0

# Within the same face class, a challenger must beat the historical
# frame's sharpness by this factor to count as *clearly* better.
SHARPNESS_MARGIN = 1.25

FaceBox = Tuple[float, float, float, float]
DetectFaces = Callable[[np.ndarray], Sequence[FaceBox]]


@dataclass(frozen=True)
class CoverCandidate:
    """One sampled frame and its cover-worthiness measurements."""

    timestamp: float
    has_face: bool
    face_area_frac: float
    sharpness: float
    mean_luma: float
    exposure_ok: bool

    @property
    def rank(self) -> tuple:
        """Ordering key: a face beats no face, then sharper wins."""
        return (1 if self.has_face else 0, self.sharpness, self.face_area_frac)


def frame_candidate(
    timestamp: float,
    frame_bgr: np.ndarray,
    detect_faces: Optional[DetectFaces] = None,
) -> CoverCandidate:
    """Measure one decoded frame. Detector failures mean 'no face'."""
    analysis = frame_bgr
    height, width = frame_bgr.shape[:2]
    if width > ANALYSIS_WIDTH:
        scale = ANALYSIS_WIDTH / float(width)
        analysis = cv2.resize(
            frame_bgr,
            (ANALYSIS_WIDTH, max(1, int(round(height * scale)))),
            interpolation=cv2.INTER_AREA,
        )
    gray = (
        cv2.cvtColor(analysis, cv2.COLOR_BGR2GRAY)
        if analysis.ndim == 3
        else analysis
    )
    mean_luma = float(gray.mean())
    sharpness = float(cv2.Laplacian(gray, cv2.CV_64F).var())

    boxes: Sequence[FaceBox] = []
    if detect_faces is not None:
        try:
            boxes = detect_faces(analysis) or []
        except Exception:
            boxes = []
    frame_area = float(analysis.shape[0] * analysis.shape[1]) or 1.0
    face_area = sum(float(w) * float(h) for (_x, _y, w, h) in boxes) / frame_area
    return CoverCandidate(
        timestamp=float(timestamp),
        has_face=bool(boxes),
        face_area_frac=face_area,
        sharpness=sharpness,
        mean_luma=mean_luma,
        exposure_ok=NEAR_BLACK_LUMA <= mean_luma <= NEAR_WHITE_LUMA,
    )


def pick_cover(
    candidates: Sequence[CoverCandidate], default_timestamp: float
) -> Optional[CoverCandidate]:
    """Return the winning candidate, or None to keep the default.

    A candidate wins only when it is clearly better than the frame at
    ``default_timestamp``: it has a face when the default has none (or
    vice versa it never wins), the default is exposure-rejected, or —
    same face situation — it is sharper by ``SHARPNESS_MARGIN``.
    Exposure-rejected candidates can never win.
    """
    if not candidates:
        return None
    default = min(candidates, key=lambda c: abs(c.timestamp - default_timestamp))
    if abs(default.timestamp - default_timestamp) > 0.6:
        # The historical frame was not actually scored (the sampler
        # always includes it, so this is defensive): only a faced
        # candidate is an unambiguous improvement over an unknown one.
        usable = [c for c in candidates if c.exposure_ok]
        if not usable:
            return None
        best = max(usable, key=lambda c: c.rank)
        return best if best.has_face else None

    usable = [c for c in candidates if c.exposure_ok]
    if not usable:
        return None
    best = max(usable, key=lambda c: c.rank)
    if best.timestamp == default.timestamp:
        return None
    if not default.exposure_ok:
        return best
    if best.has_face != default.has_face:
        return best if best.has_face else None
    if default.sharpness <= 1e-6:
        return best if best.sharpness > 1e-6 else None
    if best.sharpness >= default.sharpness * SHARPNESS_MARGIN:
        return best
    return None


def _sample_times(duration: float, default_timestamp: float) -> List[float]:
    """Grid timestamps across the clip plus the historical choice."""
    if duration <= 0:
        return []
    step = max(MIN_SAMPLE_STEP_SECONDS, duration / MAX_CANDIDATES)
    times: List[float] = []
    t = step / 2.0
    while t < duration - 0.05 and len(times) < MAX_CANDIDATES:
        times.append(round(t, 3))
        t += step
    if not times:
        times = [round(duration / 2.0, 3)]
    default = min(max(float(default_timestamp), 0.0), max(0.0, duration - 0.05))
    if all(abs(default - existing) > 0.05 for existing in times):
        times.append(round(default, 3))
    return sorted(times)


def _open_face_detector():
    """(kind, detector) in the reframing stack's YuNet -> Haar order.

    Returns None when neither detector can be built; scoring then
    relies on sharpness and exposure alone.
    """
    try:
        from .face_detection import open_haar_cascade, open_yunet_detector
    except Exception:
        return None
    try:
        yunet = open_yunet_detector()
    except Exception:
        yunet = None
    if yunet is not None:
        return ("yunet", yunet)
    try:
        cascade = open_haar_cascade()
        if cascade is not None and not cascade.empty():
            return ("haar", cascade)
    except Exception:
        pass
    return None


def _detector_detect(detector, frame_bgr: np.ndarray) -> List[FaceBox]:
    kind, handle = detector
    if kind == "yunet":
        return [tuple(map(float, box[:4])) for box in handle.detect(frame_bgr)]
    gray = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2GRAY)
    boxes = handle.detectMultiScale(
        gray, scaleFactor=1.1, minNeighbors=5, minSize=(20, 20)
    )
    return [tuple(map(float, box)) for box in boxes]


def sample_candidates(
    video_path: Path, default_timestamp: float
) -> List[CoverCandidate]:
    """Decode and score the candidate grid for one rendered clip."""
    capture = cv2.VideoCapture(str(video_path))
    if not capture.isOpened():
        return []
    try:
        fps = float(capture.get(cv2.CAP_PROP_FPS) or 0.0)
        frames = float(capture.get(cv2.CAP_PROP_FRAME_COUNT) or 0.0)
        duration = frames / fps if fps > 0 and frames > 0 else 0.0
        if duration <= 0:
            return []
        detector = _open_face_detector()

        def detect(frame: np.ndarray) -> List[FaceBox]:
            if detector is None:
                return []
            return _detector_detect(detector, frame)

        candidates: List[CoverCandidate] = []
        for timestamp in _sample_times(duration, default_timestamp):
            capture.set(cv2.CAP_PROP_POS_MSEC, timestamp * 1000.0)
            ok, frame = capture.read()
            if not ok or frame is None:
                continue
            candidates.append(frame_candidate(timestamp, frame, detect))
        return candidates
    finally:
        capture.release()


def choose_cover_timestamp(
    video_path: Path,
    default_timestamp: float,
    *,
    sampler: Optional[Callable[[Path, float], List[CoverCandidate]]] = None,
) -> float:
    """Return the cover timestamp to use for a bundle. Never raises.

    Falls back to ``default_timestamp`` — exactly the historical
    behaviour — on any failure or when no candidate is clearly better.
    """
    try:
        candidates = (sampler or sample_candidates)(video_path, default_timestamp)
        if not candidates:
            return float(default_timestamp)
        winner = pick_cover(candidates, default_timestamp)
        if winner is None:
            return float(default_timestamp)
        default = min(
            candidates, key=lambda c: abs(c.timestamp - default_timestamp)
        )
        logger.info(
            "Cover frame: chose %.2fs (face=%s, sharpness=%.1f, luma=%.1f) "
            "over the fixed %.2fs (face=%s, sharpness=%.1f, luma=%.1f) "
            "from %d candidates",
            winner.timestamp,
            winner.has_face,
            winner.sharpness,
            winner.mean_luma,
            default.timestamp,
            default.has_face,
            default.sharpness,
            default.mean_luma,
            len(candidates),
        )
        return float(winner.timestamp)
    except Exception as exc:
        logger.warning(
            "Cover selection failed (%s); using the fixed cover timestamp %.2f",
            exc,
            default_timestamp,
        )
        return float(default_timestamp)
