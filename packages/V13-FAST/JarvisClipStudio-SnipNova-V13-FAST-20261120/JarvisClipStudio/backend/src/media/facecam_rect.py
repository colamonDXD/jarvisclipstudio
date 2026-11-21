"""Find a streamer's facecam rectangle from its persistent border.

The face-first reactor check (``reaction_layout.find_reactor_face``) needs a
face detected in most sampled frames, in an upper corner, with another face
lower in the frame. Real streams break all three: the facecam face is small
in the analysis pass (often ~16 px after downscaling), the camera can float
half-way down a side, and gameplay rarely has faces. This module looks for
the camera window itself instead:

1. Persistent edges: the per-pixel median Sobel response over frames spread
   across the clip. Gameplay edges move; an overlay border does not.
2. A side-anchored rectangle: two long horizontal edge runs with matching
   extents and an inner vertical run joining them (an outer vertical run, or
   the frame edge, on the other side). Top/bottom may be the frame edge.
3. A face inside: YuNet on the full-resolution crop, upscaled when small,
   must find a face in at least ``FACE_FRACTION`` of the frames.

Switch: ``FACECAM_RECT_DETECT`` (default on; ``0``/``off`` disables).
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, List, Optional, Sequence, Tuple

import cv2
import numpy as np

from .common import logger

Box = Tuple[float, float, float, float]

WORK_WIDTH = 960
EDGE_THRESHOLD = 36.0
MIN_W_FRAC, MAX_W_FRAC = 0.10, 0.40
MIN_H_FRAC, MAX_H_FRAC = 0.15, 0.55
MIN_ASPECT, MAX_ASPECT = 0.9, 2.2
MIN_FACE_HEIGHT_FRAC = 0.22
SIDE_FRAC = 0.15
LINE_COVERAGE = 0.70
FACE_FRACTION = 0.40
MIN_FRAMES = 6
SECOND_CAMERA_PRESENCE = 0.9
SECOND_CAMERA_SIZE = (0.6, 1.6)


@dataclass(frozen=True)
class FacecamRect:
    rect: Box          # (x, y, w, h) in source pixels
    face: Box          # median face box in source pixels
    side: str          # "left" or "right"
    face_frames: int
    frames: int


def facecam_rect_enabled() -> bool:
    return os.getenv("FACECAM_RECT_DETECT", "1").strip().lower() not in {
        "0", "false", "no", "off",
    }


def _read_frames(path: Path, times: Sequence[float]) -> List[np.ndarray]:
    cap = cv2.VideoCapture(str(path))
    frames: List[np.ndarray] = []
    try:
        for t in times:
            cap.set(cv2.CAP_PROP_POS_MSEC, float(t) * 1000.0)
            ok, frame = cap.read()
            if ok and frame is not None:
                frames.append(frame)
    finally:
        cap.release()
    return frames


def persistent_edges(gray_frames: Sequence[np.ndarray]) -> Tuple[np.ndarray, np.ndarray]:
    """(horizontal-edge map, vertical-edge map): per-pixel medians over frames."""
    horizontal, vertical = [], []
    for gray in gray_frames:
        g = gray.astype(np.float32)
        horizontal.append(np.abs(cv2.Sobel(g, cv2.CV_32F, 0, 1, ksize=3)))
        vertical.append(np.abs(cv2.Sobel(g, cv2.CV_32F, 1, 0, ksize=3)))
    return np.median(np.stack(horizontal), axis=0), np.median(np.stack(vertical), axis=0)


def _coverage(mask_line: np.ndarray) -> float:
    return float(mask_line.mean()) if mask_line.size else 0.0


def _line_strength(edge: np.ndarray, axis_index: int, start: int, end: int, horizontal: bool) -> float:
    """Fraction of a straight line (allowing 1 px jitter) above the threshold."""
    lo, hi = sorted((start, end))
    if hi - lo < 4:
        return 0.0
    if horizontal:
        rows = edge[max(0, axis_index - 1): axis_index + 2, lo:hi]
        return _coverage((rows > EDGE_THRESHOLD).any(axis=0))
    cols = edge[lo:hi, max(0, axis_index - 1): axis_index + 2]
    return _coverage((cols > EDGE_THRESHOLD).any(axis=1))


def _peaks(profile: np.ndarray, min_value: float, min_gap: int) -> List[int]:
    order = np.argsort(profile)[::-1]
    picked: List[int] = []
    for index in order:
        if profile[index] < min_value:
            break
        if all(abs(int(index) - p) >= min_gap for p in picked):
            picked.append(int(index))
        if len(picked) >= 12:
            break
    return picked


def find_rect_candidates(
    horizontal: np.ndarray, vertical: np.ndarray
) -> List[Tuple[float, Box, str]]:
    """Side-anchored rectangles in work pixels, best first: (score, box, side)."""
    height, width = horizontal.shape
    candidates: List[Tuple[float, Box, str]] = []
    min_w, max_w = int(width * MIN_W_FRAC), int(width * MAX_W_FRAC)
    min_h, max_h = int(height * MIN_H_FRAC), int(height * MAX_H_FRAC)
    h_mask = horizontal > EDGE_THRESHOLD
    v_mask = vertical > EDGE_THRESHOLD
    for side in ("left", "right"):
        band = slice(0, int(width * (SIDE_FRAC + MAX_W_FRAC))) if side == "left" else slice(
            int(width * (1 - SIDE_FRAC - MAX_W_FRAC)), width
        )
        offset = band.start or 0
        # Inner vertical edge candidates: columns with long persistent runs.
        col_profile = v_mask[:, band].mean(axis=0)
        row_profile = h_mask[:, band].mean(axis=1)
        inner_xs = [x + offset for x in _peaks(col_profile, min_h / height * LINE_COVERAGE, 6)]
        rows = _peaks(row_profile, min_w / (band.stop - offset) * LINE_COVERAGE, 6)
        rows = sorted(set(rows + [0, height - 1]))
        for inner in inner_xs:
            # Outer edge: the frame edge, or a persistent vertical line.
            outer_options = [0] if side == "left" else [width - 1]
            for x in inner_xs:
                if x != inner and (x < inner if side == "left" else x > inner):
                    outer_options.append(x)
            for outer in outer_options:
                x0, x1 = sorted((outer, inner))
                w = x1 - x0
                if not (min_w <= w <= max_w):
                    continue
                if side == "left" and x0 > width * SIDE_FRAC:
                    continue
                if side == "right" and x1 < width * (1 - SIDE_FRAC):
                    continue
                for i, top in enumerate(rows):
                    for bottom in rows[i + 1:]:
                        h = bottom - top
                        if not (min_h <= h <= max_h) or not (MIN_ASPECT <= w / h <= MAX_ASPECT):
                            continue
                        top_ok = top <= 1 or _line_strength(horizontal, top, x0, x1, True) >= LINE_COVERAGE
                        bottom_ok = bottom >= height - 2 or _line_strength(horizontal, bottom, x0, x1, True) >= LINE_COVERAGE
                        inner_cov = _line_strength(vertical, inner, top, bottom, False)
                        outer_ok = outer in (0, width - 1) or _line_strength(vertical, outer, top, bottom, False) >= LINE_COVERAGE
                        if not (top_ok and bottom_ok and outer_ok and inner_cov >= LINE_COVERAGE):
                            continue
                        # Prefer the outermost valid window: a static room
                        # behind the streamer draws persistent lines INSIDE
                        # the camera too, and those must not become its edge.
                        score = inner_cov + 4.0 * (w * h) / (width * height)
                        candidates.append((score, (float(x0), float(top), float(w), float(h)), side))
    candidates.sort(key=lambda item: item[0], reverse=True)
    # Near-duplicates (one edge a few pixels off) would crowd the short
    # list the face check reads; keep only the best of each overlapping set.
    distinct: List[Tuple[float, Box, str]] = []
    for candidate in candidates:
        if all(_iou(candidate[1], kept[1]) < 0.5 for kept in distinct):
            distinct.append(candidate)
    return distinct


def _iou(a: Box, b: Box) -> float:
    ax, ay, aw, ah = a
    bx, by, bw, bh = b
    ix = max(0.0, min(ax + aw, bx + bw) - max(ax, bx))
    iy = max(0.0, min(ay + ah, by + bh) - max(ay, by))
    inter = ix * iy
    union = aw * ah + bw * bh - inter
    return inter / union if union > 0 else 0.0


def _default_detector() -> Optional[Callable[[np.ndarray], List[Box]]]:
    try:
        from .face_detection import open_yunet_detector

        detector = open_yunet_detector()
    except Exception:
        return None
    if detector is None:
        return None
    return lambda frame: [tuple(map(float, box[:4])) for box in detector.detect(frame)]


def _faces_in_crop(
    frames: Sequence[np.ndarray], rect: Box, detect: Callable[[np.ndarray], List[Box]]
) -> List[Box]:
    x, y, w, h = (int(round(v)) for v in rect)
    found: List[Box] = []
    for frame in frames:
        crop = frame[y:y + h, x:x + w]
        if crop.size == 0:
            continue
        scale = max(1.0, 480.0 / max(1, crop.shape[1]))
        if scale > 1.0:
            crop = cv2.resize(crop, None, fx=scale, fy=scale, interpolation=cv2.INTER_CUBIC)
        faces = [
            f for f in detect(crop)
            if f[3] >= crop.shape[0] * MIN_FACE_HEIGHT_FRAC
            and 0 <= f[0] + f[2] / 2 <= crop.shape[1]
            and 0 <= f[1] + f[3] / 2 <= crop.shape[0]
        ]
        if not faces:
            continue
        fx, fy, fw, fh = max(faces, key=lambda f: f[2] * f[3])
        found.append((x + fx / scale, y + fy / scale, fw / scale, fh / scale))
    return found


def _second_camera(
    frames: Sequence[np.ndarray], rect: Box, cam_face: Box,
    detect: Callable[[np.ndarray], List[Box]],
) -> bool:
    """True when another camera-sized face sits in one place OUTSIDE the
    window in nearly every frame.

    That is a video call or an Omegle-style split (each person in a fixed
    window of similar size), not a facecam over changing content. Measured
    on real streams: call partners appear in 10/10 samples at 0.9-1.2x the
    cam face; GTA cutscene faces in 7-8/10 at 1.5-3.5x, so they do not count.
    """
    height, width = frames[0].shape[:2]
    x, y, w, h = rect
    radius = width * 0.12
    # Each cluster: [centre_x, centre_y, frame indices, face heights].
    clusters: List[List[Any]] = []
    for index, frame in enumerate(frames):
        for f in detect(frame):
            cx, cy = f[0] + f[2] / 2, f[1] + f[3] / 2
            if x <= cx <= x + w and y <= cy <= y + h:
                continue
            for cluster in clusters:
                if abs(cluster[0] - cx) <= radius and abs(cluster[1] - cy) <= radius:
                    cluster[2].add(index)
                    cluster[3].append(f[3])
                    break
            else:
                clusters.append([cx, cy, {index}, [f[3]]])
    for _cx, _cy, seen, heights in clusters:
        ratio = float(np.median(heights)) / max(1.0, cam_face[3])
        if len(seen) >= len(frames) * SECOND_CAMERA_PRESENCE and (
            SECOND_CAMERA_SIZE[0] <= ratio <= SECOND_CAMERA_SIZE[1]
        ):
            return True
    return False


def find_facecam_rect(
    path: Path,
    width: int,
    height: int,
    times: Sequence[float],
    detect: Optional[Callable[[np.ndarray], List[Box]]] = None,
) -> Optional[FacecamRect]:
    """The facecam window of a stream, or None when there is no clear one."""
    if not facecam_rect_enabled() or width <= 0 or height <= 0:
        return None
    frames = [f for f in _read_frames(path, times) if f.shape[:2] == (height, width)]
    if len(frames) < MIN_FRAMES:
        return None
    scale = min(1.0, WORK_WIDTH / float(width))
    work = [
        cv2.cvtColor(
            cv2.resize(f, (int(round(width * scale)), int(round(height * scale))), interpolation=cv2.INTER_AREA)
            if scale < 1.0 else f,
            cv2.COLOR_BGR2GRAY,
        )
        for f in frames
    ]
    horizontal, vertical = persistent_edges(work)
    candidates = find_rect_candidates(horizontal, vertical)
    if not candidates:
        return None
    detect = detect or _default_detector()
    if detect is None:
        return None
    for _score, box, side in candidates[:6]:
        rect = tuple(v / scale for v in box)
        faces = _faces_in_crop(frames, rect, detect)
        if len(faces) >= max(2, int(np.ceil(len(frames) * FACE_FRACTION))):
            face = tuple(float(v) for v in np.median(np.asarray(faces), axis=0))
            if _second_camera(frames, rect, face, detect):
                logger.info(
                    "Camera window at %s is one of several fixed camera "
                    "windows (a call layout), not a facecam",
                    tuple(round(v) for v in rect),
                )
                return None
            logger.info(
                "Facecam window found: %s side, rect %s, face in %d/%d frames",
                side, tuple(round(v) for v in rect), len(faces), len(frames),
            )
            return FacecamRect(rect, face, side, len(faces), len(frames))
    return None


def probe_times(ranges: Sequence[Tuple[float, float]], count: int = 10) -> List[float]:
    """``count`` source times spread evenly over the kept ranges."""
    total = sum(max(0.0, end - start) for start, end in ranges)
    times: List[float] = []
    if total <= 0:
        return times
    for index in range(count):
        position = (index + 0.5) / count * total
        for start, end in ranges:
            span = end - start
            if position <= span:
                times.append(start + position)
                break
            position -= span
    return times
