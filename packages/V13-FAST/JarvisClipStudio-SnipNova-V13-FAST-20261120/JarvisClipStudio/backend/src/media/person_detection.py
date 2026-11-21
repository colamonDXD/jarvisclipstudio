"""YOLOX person detection (OpenCV ``cv2.dnn``) for the reframing pipeline.

The bundled int8 ONNX model (OpenCV Zoo, Apache-2.0 — see
``models/LICENSE-yolox``) is a subject-presence fallback for people the
YuNet face detector cannot see at all: helmets and visors, backs of
heads, night shots. The 2026-11-16 spike (NONFACE_SUBJECT_SPIKE.md)
measured person IoU 0.54-0.92 on exactly those frames, where YuNet
scores 0 — and a total miss on the drone light-portrait, which the
bright-extent signal in ``scene_context`` owns instead.

Cost is the reason this module is gated by its caller: a forward pass
is ~0.7 s on sandbox-class CPU, so it runs only on analysis samples
where no significant face was found. Pre/post-processing mirrors the
zoo's own demo (letterbox 640 with 114 padding, raw pixel values,
grid/stride decode, batched NMS) — the same recipe the spike measured.
"""

from pathlib import Path
from typing import List, Optional, Tuple

import cv2
import numpy as np

from .common import logger

YOLOX_MODEL_PATH = Path(__file__).parent / "models" / "object_detection_yolox_2022nov_int8.onnx"

YOLOX_INPUT_SIZE = 640

# COCO class 0. Every other class is discarded: the spike's observed
# false positives ("car", "umbrella" on a metal structure) were all
# non-person classes.
YOLOX_PERSON_CLASS_ID = 0

# Spike-measured floor: true persons scored 0.48-0.92; restricting to
# the person class at >= 0.5 removed every observed false positive.
YOLOX_SCORE_THRESHOLD = 0.5

# NMS thresholds are the zoo wrapper's own defaults; the person floor
# above is applied after NMS, so these only shape suppression.
YOLOX_NMS_CONF_THRESHOLD = 0.35
YOLOX_NMS_THRESHOLD = 0.5

YOLOX_STRIDES = (8, 16, 32)

# (x0, y0, x1, y1, score), normalized to the frame.
PersonBox = Tuple[float, float, float, float, float]


def _letterbox(frame_bgr: np.ndarray) -> Tuple[np.ndarray, float]:
    """Resize into a 640x640 canvas, padded with 114 (the zoo demo's recipe)."""
    height, width = frame_bgr.shape[:2]
    ratio = min(YOLOX_INPUT_SIZE / height, YOLOX_INPUT_SIZE / width)
    resized = cv2.resize(
        frame_bgr,
        (int(width * ratio), int(height * ratio)),
        interpolation=cv2.INTER_LINEAR,
    ).astype(np.float32)
    padded = np.full(
        (YOLOX_INPUT_SIZE, YOLOX_INPUT_SIZE, 3), 114.0, dtype=np.float32
    )
    padded[: resized.shape[0], : resized.shape[1]] = resized
    return padded, ratio


def _anchor_grids() -> Tuple[np.ndarray, np.ndarray]:
    grids, strides = [], []
    for stride in YOLOX_STRIDES:
        size = YOLOX_INPUT_SIZE // stride
        xv, yv = np.meshgrid(np.arange(size), np.arange(size))
        grid = np.stack((xv, yv), 2).reshape(1, -1, 2)
        grids.append(grid)
        strides.append(np.full((1, size * size, 1), stride))
    return (
        np.concatenate(grids, 1).astype(np.float32),
        np.concatenate(strides, 1).astype(np.float32),
    )


def _decode_persons(
    raw: np.ndarray,
    ratio: float,
    width: int,
    height: int,
    grids: np.ndarray,
    expanded_strides: np.ndarray,
    score_threshold: float = YOLOX_SCORE_THRESHOLD,
) -> List[PersonBox]:
    """Decode one raw YOLOX output (8400 x 85) into normalized person boxes.

    Mirrors the zoo wrapper's postprocess: grid/stride box decode,
    objectness x class scores, batched (per-class) NMS — then the
    person-only, >= threshold filter the spike validated.
    """
    dets = np.array(raw, dtype=np.float64)
    if dets.ndim != 2 or dets.shape[1] < 6:
        return []
    dets[:, :2] = (dets[:, :2] + grids) * expanded_strides
    dets[:, 2:4] = np.exp(dets[:, 2:4]) * expanded_strides
    boxes_xywh = np.empty_like(dets[:, :4])
    boxes_xywh[:, 0] = dets[:, 0] - dets[:, 2] / 2.0
    boxes_xywh[:, 1] = dets[:, 1] - dets[:, 3] / 2.0
    boxes_xywh[:, 2] = dets[:, 2]
    boxes_xywh[:, 3] = dets[:, 3]
    scores = dets[:, 4:5] * dets[:, 5:]
    max_scores = np.amax(scores, axis=1)
    max_classes = np.argmax(scores, axis=1)
    keep = cv2.dnn.NMSBoxesBatched(
        boxes_xywh.tolist(),
        max_scores.tolist(),
        max_classes.tolist(),
        YOLOX_NMS_CONF_THRESHOLD,
        YOLOX_NMS_THRESHOLD,
    )
    persons: List[PersonBox] = []
    for index in np.array(keep).flatten():
        if int(max_classes[index]) != YOLOX_PERSON_CLASS_ID:
            continue
        score = float(max_scores[index])
        if score < score_threshold:
            continue
        x, y, w, h = (float(v) / ratio for v in boxes_xywh[index])
        x0 = min(max(x / width, 0.0), 1.0)
        y0 = min(max(y / height, 0.0), 1.0)
        x1 = min(max((x + w) / width, 0.0), 1.0)
        y1 = min(max((y + h) / height, 0.0), 1.0)
        if x1 - x0 < 0.005 or y1 - y0 < 0.005:
            continue
        persons.append((x0, y0, x1, y1, score))
    persons.sort(key=lambda box: box[4], reverse=True)
    return persons


class YoloxPersonDetector:
    """Stateful YOLOX wrapper: one loaded net, reused across samples."""

    def __init__(
        self,
        model_path: Path = YOLOX_MODEL_PATH,
        score_threshold: float = YOLOX_SCORE_THRESHOLD,
    ):
        if not Path(model_path).is_file():
            raise FileNotFoundError(f"YOLOX model not found: {model_path}")
        self._net = cv2.dnn.readNetFromONNX(str(model_path))
        self._net.setPreferableBackend(cv2.dnn.DNN_BACKEND_OPENCV)
        self._net.setPreferableTarget(cv2.dnn.DNN_TARGET_CPU)
        self._score_threshold = float(score_threshold)
        self._grids, self._expanded_strides = _anchor_grids()

    def detect_persons(self, frame_bgr: np.ndarray) -> List[PersonBox]:
        """Return person boxes (normalized xyxy + score), best first."""
        height, width = frame_bgr.shape[:2]
        if width <= 0 or height <= 0:
            return []
        image = frame_bgr
        if image.ndim == 2:
            image = cv2.cvtColor(image, cv2.COLOR_GRAY2BGR)
        padded, ratio = _letterbox(image)
        blob = np.transpose(padded, (2, 0, 1))[np.newaxis, :, :, :]
        self._net.setInput(blob)
        outputs = self._net.forward(self._net.getUnconnectedOutLayersNames())
        if not len(outputs):
            return []
        return _decode_persons(
            outputs[0][0],
            ratio,
            width,
            height,
            self._grids,
            self._expanded_strides,
            self._score_threshold,
        )


def open_yolox_detector(
    model_path: Path = YOLOX_MODEL_PATH, **kwargs
) -> Optional[YoloxPersonDetector]:
    """Load YOLOX, or return None (callers treat that as "no person pass")."""
    try:
        return YoloxPersonDetector(model_path, **kwargs)
    except Exception as exc:
        logger.warning("YOLOX person detector unavailable (%s); pass disabled", exc)
        return None


class _LazyYoloxPersonDetector:
    """Opens the real net on the first gated sample, never before.

    Analysis passes where every sample has a face never pay the load;
    a load failure disables the pass for the wrapper's lifetime
    instead of retrying (and re-logging) on every no-face sample.
    """

    def __init__(self):
        self._detector: Optional[YoloxPersonDetector] = None
        self._tried = False

    def detect_persons(self, frame_bgr: np.ndarray) -> List[PersonBox]:
        if not self._tried:
            self._tried = True
            self._detector = open_yolox_detector()
        if self._detector is None:
            return []
        return self._detector.detect_persons(frame_bgr)


def lazy_yolox_detector() -> _LazyYoloxPersonDetector:
    """A person detector that costs nothing until its first real use."""
    return _LazyYoloxPersonDetector()
