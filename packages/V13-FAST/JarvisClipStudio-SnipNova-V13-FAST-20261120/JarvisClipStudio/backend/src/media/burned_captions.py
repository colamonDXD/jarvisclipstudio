"""Find burned-in subtitles that change while staying in one band.

Some sources already carry captions. Our 9:16 crop can slice them and our
own captions then sit on top, so the render needs to know where the
source captions live. This detector is deliberately about *subtitles*,
not about text in general: a channel name, a logo or a static "follow
us" panel must not move our captions, because it never changes.

The detector works per sampled frame:

1. Canny edges are scanned with a sliding horizontal strip. A strip is
   kept only when it is wide, glyph-rich, edged much more densely than
   the equal-size strips just above and below it, and holds outlined
   bright strokes (the usual subtitle look). This finds a subtitle even
   when it touches other scene edges, which a connected-box approach
   does not.
2. Overlapping strips on one frame are reduced to the best few, so one
   frame cannot outvote the rest.
3. The outlined-stroke mask of the band's fixed rectangle is the frame's
   signature.

Across frames the candidates are clustered by vertical centre. A cluster
is a caption band only when it appears in a good share of the sampled
frames *and* its stroke masks differ between frames: static text keeps
the same mask, changing subtitles do not. Busy textures fail at step 1
because their edges are spread evenly, not concentrated in a wide, short
band.

OpenCV + NumPy only. No OCR model, no downloads, no GPU.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional, Sequence, Tuple

import cv2
import numpy as np

ANALYSIS_WIDTH = 480
SAMPLE_COUNT = 12

# A band has to show up in at least this share of decoded samples.
MIN_FRAME_SHARE = 0.35
MIN_FRAME_COUNT = 3
# Vertical centre tolerance, as a fraction of the frame height.
BAND_TOLERANCE = 0.055

# Strip heights tried by the sliding scan, in analysis-frame pixels.
STRIP_HEIGHTS = (6, 8, 10, 12, 14, 17, 20, 24, 28, 33, 40, 48)
# A strip must be at least this wide, as a fraction of the frame.
MIN_BAND_WIDTH = 0.10
MAX_BAND_WIDTH = 0.96
# The column projection must carry an edge in at least this share of
# the strip's rows to be treated as text-bearing.
MIN_COLUMN_SHARE = 0.20

# Edge-density limits inside a candidate strip.
MIN_EDGE_DENSITY = 0.080
MAX_EDGE_DENSITY = 0.45
# The strip must hold more edges than the equal-size strips above and
# below it; parallel neighbours make a busy texture fail this.
MIN_EDGE_RATIO = 1.40
# Neighbour strips are sampled this many band heights away, so a
# descender or an outline cannot leak into the comparison.
NEIGHBOUR_GAP = 0.50

MIN_GLYPHS = 3
MIN_GLYPH_SPREAD = 0.25
# Share of the band's pixels that must be outlined bright strokes. A
# subtitle line clears this easily; a noisy texture and a moving test
# pattern sit far below it.
MIN_STROKE_SHARE = 0.018
MAX_CANDIDATES_PER_FRAME = 3
BAND_SIGNATURE = (256, 32)

# Jaccard distance between two binary stroke masks (0..1).
CHANGE_THRESHOLD = 0.30
# Share of frame pairs that must differ for the band to be "changing".
MIN_CHANGING_SHARE = 0.15


@dataclass(frozen=True)
class CaptionBand:
    """A burned-in caption band, in source pixels."""

    y: int
    h: int
    confidence: float
    frames_with_text: int
    frames_checked: int


@dataclass
class _Candidate:
    top: float
    bottom: float
    left: float
    right: float
    strokes: np.ndarray
    score: float

    @property
    def centre(self) -> float:
        return (self.top + self.bottom) / 2.0


def _analysis_frame(frame: np.ndarray) -> np.ndarray:
    height, width = frame.shape[:2]
    scale = ANALYSIS_WIDTH / float(width)
    if scale == 1.0:
        return frame
    return cv2.resize(
        frame,
        (ANALYSIS_WIDTH, max(1, int(round(height * scale)))),
        interpolation=cv2.INTER_AREA,
    )


def _stroke_mask(gray: np.ndarray) -> np.ndarray:
    """Outlined bright strokes: white glyphs with a dark edge/shadow."""
    bright = (gray >= 185).astype(np.uint8)
    dark = cv2.dilate((gray <= 85).astype(np.uint8), np.ones((3, 3), np.uint8))
    return (bright & dark).astype(np.uint8)


def _edge_density(edges: np.ndarray) -> float:
    if edges.size == 0:
        return 0.0
    return float(np.count_nonzero(edges)) / float(edges.size)


def _glyph_count(
    edges: np.ndarray, box_width: int, box_height: int
) -> Tuple[int, float]:
    count, _, stats, centres = cv2.connectedComponentsWithStats(edges)
    glyphs = 0
    xs: List[float] = []
    for index in range(1, count):
        _x, _y, cw, ch, area = stats[index]
        if (
            box_height * 0.15 <= ch <= box_height * 1.10
            and cw <= box_height * 1.20
            and area >= 4
        ):
            glyphs += 1
            xs.append(float(centres[index][0]))
    spread = (max(xs) - min(xs)) if len(xs) >= 2 else 0.0
    return glyphs, spread


def _strip_candidate(
    edges: np.ndarray,
    strokes: np.ndarray,
    y0: int,
    strip: int,
    width: int,
    height: int,
) -> Optional[_Candidate]:
    """Score one horizontal strip, or return ``None`` when it is not text."""
    y1 = y0 + strip
    inside = edges[y0:y1]
    required = max(2, int(round(strip * MIN_COLUMN_SHARE)))
    columns = np.where(inside.sum(axis=0) >= required)[0]
    if columns.size < 4:
        return None
    x0, x1 = int(columns[0]), int(columns[-1]) + 1
    span = x1 - x0
    if span < width * MIN_BAND_WIDTH or span > width * MAX_BAND_WIDTH:
        return None

    box = inside[:, x0:x1]
    density = _edge_density(box)
    if not (MIN_EDGE_DENSITY <= density <= MAX_EDGE_DENSITY):
        return None

    stroke_box = strokes[y0:y1, x0:x1]
    stroke_share = _edge_density(stroke_box)
    if stroke_share < MIN_STROKE_SHARE:
        return None

    # Equal-size strips just above and below: a caption line spikes
    # against them, a texture does not.
    gap = max(2, int(round(strip * NEIGHBOUR_GAP)))
    above = edges[max(0, y0 - gap - strip):max(0, y0 - gap), x0:x1]
    below = edges[min(height, y1 + gap):min(height, y1 + gap + strip), x0:x1]
    blocks = [block for block in (above, below) if block.size]
    if not blocks:
        return None
    outside = sum(int(np.count_nonzero(block)) for block in blocks) / float(
        sum(block.size for block in blocks)
    )
    ratio = density / max(outside, 0.004)
    if ratio < MIN_EDGE_RATIO:
        return None

    glyphs, spread = _glyph_count(box, span, strip)
    if glyphs < MIN_GLYPHS or spread < span * MIN_GLYPH_SPREAD:
        return None

    return _Candidate(
        top=y0 / height,
        bottom=y1 / height,
        left=x0 / width,
        right=x1 / width,
        strokes=strokes,
        score=float(density * min(ratio, 6.0)),
    )


def _suppress(candidates: List[_Candidate]) -> List[_Candidate]:
    """Keep the best non-overlapping strips of one frame.

    Every band yields several overlapping strips (one per height), and
    counting them all would let a single frame outvote the rest during
    clustering.
    """
    ordered = sorted(candidates, key=lambda item: item.score, reverse=True)
    kept: List[_Candidate] = []
    for candidate in ordered:
        candidate_height = candidate.bottom - candidate.top
        overlap = False
        for other in kept:
            shared = min(candidate.bottom, other.bottom) - max(
                candidate.top, other.top
            )
            other_height = other.bottom - other.top
            if shared > 0.5 * min(candidate_height, other_height):
                overlap = True
                break
        if overlap:
            continue
        kept.append(candidate)
        if len(kept) >= MAX_CANDIDATES_PER_FRAME:
            break
    return kept


def candidates_in_frame(frame: np.ndarray) -> List[_Candidate]:
    """Return the subtitle-like bands of one BGR frame."""
    if frame is None or frame.size == 0:
        return []
    frame = _analysis_frame(frame)
    height, width = frame.shape[:2]
    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    edges = cv2.Canny(cv2.GaussianBlur(gray, (3, 3), 0), 70, 180)
    strokes = _stroke_mask(gray)

    found: List[_Candidate] = []
    for strip in STRIP_HEIGHTS:
        if strip >= height:
            continue
        for y0 in range(0, height - strip + 1):
            candidate = _strip_candidate(
                edges, strokes, y0, strip, width, height
            )
            if candidate is not None:
                found.append(candidate)
    return _suppress(found)


def _cluster(
    candidates: List[Tuple[int, _Candidate]]
) -> List[List[Tuple[int, _Candidate]]]:
    """Group ``(frame index, candidate)`` pairs by vertical centre."""
    ordered = sorted(candidates, key=lambda item: item[1].centre)
    clusters: List[List[Tuple[int, _Candidate]]] = []
    for item in ordered:
        if not clusters:
            clusters.append([item])
            continue
        current = clusters[-1]
        centre = float(np.median([entry[1].centre for entry in current]))
        if abs(item[1].centre - centre) <= BAND_TOLERANCE:
            current.append(item)
        else:
            clusters.append([item])
    return clusters


def _mask_change(first: np.ndarray, second: np.ndarray) -> float:
    """Jaccard distance of two stroke masks: XOR over union."""
    left = first > 0.5
    right = second > 0.5
    union = int(np.count_nonzero(left | right))
    if not union:
        return 0.0
    xor = int(np.count_nonzero(left ^ right))
    return xor / float(union)


def _band_strip(
    strokes: np.ndarray,
    top: float,
    bottom: float,
    left: float,
    right: float,
) -> np.ndarray:
    """Crop one fixed band from a full-frame stroke mask.

    The crop is anchored to the band median, not to this frame's
    bounding box, so a one-pixel box wobble cannot change the mask. A
    small vertical pad keeps ascenders and descenders in view.
    """
    height, width = strokes.shape[:2]
    band = max(1.0 / height, bottom - top)
    y0 = int(round((top - band * 0.30) * height))
    y1 = int(round((bottom + band * 0.30) * height))
    y0 = max(0, min(height - 1, y0))
    y1 = max(y0 + 1, min(height, y1))

    span = max(1.0 / width, right - left)
    x0 = int(round((left - span * 0.15) * width))
    x1 = int(round((right + span * 0.15) * width))
    x0 = max(0, min(width - 1, x0))
    x1 = max(x0 + 1, min(width, x1))

    strip = strokes[y0:y1, x0:x1]
    return cv2.resize(
        strip, BAND_SIGNATURE, interpolation=cv2.INTER_NEAREST
    )


def _aggregate(
    frames: List[List[_Candidate]],
    frames_checked: int,
    height: int,
) -> Optional[CaptionBand]:
    usable = [
        (index, candidate)
        for index, frame in enumerate(frames)
        for candidate in frame
    ]
    if not usable or frames_checked <= 0:
        return None
    clusters = _cluster(usable)
    if not clusters:
        return None

    def frames_of(cluster: List[Tuple[int, _Candidate]]) -> set:
        return {index for index, _ in cluster}

    def rank(cluster: List[Tuple[int, _Candidate]]):
        return (
            len(frames_of(cluster)),
            float(np.median([item.score for _, item in cluster])),
        )

    best = max(clusters, key=rank)
    frames_with_text = len(frames_of(best))
    required = max(
        MIN_FRAME_COUNT, int(np.ceil(frames_checked * MIN_FRAME_SHARE))
    )
    if frames_with_text < required:
        return None

    # Compare one strip per frame: several overlapping strips of the
    # same frame must not masquerade as temporal change.
    by_frame: dict[int, _Candidate] = {}
    for index, item in best:
        current = by_frame.get(index)
        if current is None or item.score > current.score:
            by_frame[index] = item

    # The band is the union of the frame-level strips: taking a median
    # would clip whichever rows the densest strip missed.
    top = min(item.top for item in by_frame.values())
    bottom = max(item.bottom for item in by_frame.values())
    left = min(item.left for item in by_frame.values())
    right = max(item.right for item in by_frame.values())
    top = max(0.0, min(1.0, top))
    bottom = max(top, min(1.0, bottom))
    left = max(0.0, min(1.0, left))
    right = max(left, min(1.0, right))

    strips = [
        _band_strip(item.strokes, top, bottom, left, right)
        for item in by_frame.values()
    ]
    if len(strips) >= 2:
        changes = [
            _mask_change(strips[i], strips[j])
            for i in range(len(strips))
            for j in range(i + 1, len(strips))
        ]
        changing_share = float(
            np.mean([change >= CHANGE_THRESHOLD for change in changes])
        )
        p75 = float(np.percentile(changes, 75))
    else:
        changing_share = 0.0
        p75 = 0.0
    if changing_share < MIN_CHANGING_SHARE:
        return None

    frame_share = frames_with_text / float(frames_checked)
    change_score = min(1.0, p75 / (2.0 * CHANGE_THRESHOLD))
    confidence = float(
        max(0.0, min(1.0, 0.55 * frame_share + 0.45 * change_score))
    )

    y = int(round(top * height))
    band_height = max(1, int(round((bottom - top) * height)))
    if y + band_height > height:
        band_height = max(1, height - y)
    return CaptionBand(
        y=y,
        h=band_height,
        confidence=round(confidence, 3),
        frames_with_text=frames_with_text,
        frames_checked=frames_checked,
    )


def _even_times(video_path: Path, count: int) -> List[float]:
    capture = cv2.VideoCapture(str(video_path))
    try:
        fps = float(capture.get(cv2.CAP_PROP_FPS) or 0.0)
        frames = float(capture.get(cv2.CAP_PROP_FRAME_COUNT) or 0.0)
    finally:
        capture.release()
    if fps <= 0 or frames <= 0:
        return []
    duration = frames / fps
    return [duration * (index + 0.5) / count for index in range(count)]


def detect_burned_caption_band(
    video_path,
    times: Optional[Sequence[float]] = None,
    width: Optional[int] = None,
    height: Optional[int] = None,
    sample_count: int = SAMPLE_COUNT,
) -> Optional[CaptionBand]:
    """Detect a changing burned-in caption band in ``video_path``.

    ``times`` are source seconds to sample. When omitted, ``sample_count``
    times are spread evenly across the video. ``width``/``height`` are the
    source dimensions the result must be expressed in; when omitted the
    decoded frame size is used.
    """
    path = Path(video_path)
    if not path.is_file():
        return None
    if times is None:
        times = _even_times(path, sample_count)
    times = list(times or [])
    if not times:
        return None

    capture = cv2.VideoCapture(str(path))
    per_frame: List[List[_Candidate]] = []
    frames_checked = 0
    try:
        if not capture.isOpened():
            return None
        fps = float(capture.get(cv2.CAP_PROP_FPS) or 0.0)
        source_width = int(capture.get(cv2.CAP_PROP_FRAME_WIDTH) or 0)
        source_height = int(capture.get(cv2.CAP_PROP_FRAME_HEIGHT) or 0)
        for when in times:
            if fps > 0:
                index = max(0, int(round(float(when) * fps)))
                ok = capture.set(cv2.CAP_PROP_POS_FRAMES, index)
                if not ok:
                    capture.set(cv2.CAP_PROP_POS_MSEC, float(when) * 1000.0)
            else:
                capture.set(cv2.CAP_PROP_POS_MSEC, float(when) * 1000.0)
            ok, frame = capture.read()
            if not ok or frame is None:
                continue
            frames_checked += 1
            per_frame.append(candidates_in_frame(frame))
    finally:
        capture.release()

    if frames_checked == 0:
        return None
    out_width = int(width or source_width or 0)
    out_height = int(height or source_height or 0)
    if out_width <= 0 or out_height <= 0:
        return None
    return _aggregate(per_frame, frames_checked, out_height)
