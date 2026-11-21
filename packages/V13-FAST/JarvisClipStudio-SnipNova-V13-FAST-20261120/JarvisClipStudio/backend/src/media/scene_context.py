"""Local visual evidence for deciding whether a vertical crop loses context.

This is deliberately conservative: a face is evidence of a possible subject,
not proof that everything outside that face is disposable. All measurements
are normalized, and motion is measured after compensating for camera movement.
No network/model downloads or additional runtime dependencies are required.
"""

import logging
from dataclasses import dataclass
from collections import Counter
from typing import Optional, Sequence, Tuple

import cv2
import numpy as np

logger = logging.getLogger(__name__)

# Lines of text a face crop may slice through before the shot keeps the full
# frame. One line is often a stray texture; a sign or lower third has two.
TEXT_CUT_MIN_LINES = 2

# Composition hysteresis: a run of one layout shorter than this inherits its
# neighbours, so a clip does not flip between a full-height crop and a fitted
# strip within a couple of seconds.
LAYOUT_HOLD_SECONDS = 2.5

# A fit run this short is a one-sample flash: it cannot be read anyway.
LAYOUT_FLASH_SECONDS = 0.5

# A small but steady single face (a wide shot of the same speaker) may inherit
# a neighbouring face crop; an empty or crowded shot never does.
SOFT_SINGLE_FACE_RATE = 0.6

# --- Bright-subject extent (non-face subject presence) -----------------------
#
# A face made of drone lights is invisible to the face detector, and the
# 1-D detail span carries no subject information on real content ([0.002,
# 0.998] on every scored frame of the 2026-11-16 spike,
# NONFACE_SUBJECT_SPIKE.md). The one cheap signal that sees such a
# subject is the largest bright component of a DARK scene: on the spike's
# frames its box scores IoU 0.755 / 0.46 against the drone portrait
# where every detector scores 0.
#
# The gate is derived from those measured frames, not chosen freely:
# mean luminance of the dark scenes is at most 78 (the portrait frames
# measure 32 / 40); the sunlit forest — where bright coverage is 0.113
# and the extent degenerates — measures 107. 90 sits in the measured
# gap. Bright extent is trusted ONLY below it.
BRIGHT_DARK_SCENE_MAX_MEAN = 90.0
# Total bright coverage (significant components) separates subject
# present (0.12-0.20 on the portrait frames) from subject gone (0.037).
BRIGHT_SUBJECT_MIN_COVERAGE = 0.05
# Components smaller than this share of the frame are specks, not subjects.
BRIGHT_COMPONENT_MIN_AREA_FRAC = 0.001

# --- YOLOX person pass (gated non-face subject presence) ---------------------
#
# People whose faces the face detector cannot see at all — helmets and
# visors, backs of heads, night shots — are the second measured gap of
# the 2026-11-16 spike: the YOLOX person detector (media/person_detection.py)
# scores person IoU 0.54-0.92 on exactly those frames. It costs ~0.7 s
# per frame, so it runs ONLY on samples where the face pass found no
# significant face, and ONLY when the bright signal did not already
# claim the sample: bright is ~free and is the one signal that sees
# the drone light-portrait, which YOLOX provably cannot. The detector
# itself enforces the spike's person-class / >= 0.5 rules.

# --- Depicted-face grid screen (companion to the reframing floor) -----------
#
# Some "faces" the detector reports are pictures of faces: printed
# portrait walls (the MrBeast TRIBUTES walls), where a dozen uniform
# portraits sit in manufactured rows. They are not people a crop must
# protect, but taken at face value they inflate face_count / face_span,
# join the protect spans and close the bright / YOLOX gates.
# DEPICTED_FACES_SPIKE.md verdicted all 158 sub-threshold boxes of its
# corpus and tested three candidate signals: temporal stillness and
# in-box texture separate nothing usable (a motionless real person —
# a guard at attention, someone posing in front of the wall — is
# exactly as still as a print, and a stillness screen taxes precisely
# the wide-shot faces the floor fix exists to recover). One signal is
# exact on that corpus: grid membership. A box in an aligned row of
# >= 3 similar-size boxes is depicted in 39/69 depicted cases and
# 0/63 real cases.
#
# The screen is scoped to the band the reframing floor change newly
# admits — area fraction below the OLD floor, 0.002. That scope is
# also the adversarial guard: a real row of people at speaking scale
# (a team lineup, a class photo being taken) sits above the band and
# can never be demoted by this rule. The constant below is the
# historical floor value on purpose: it is NOT the speaker-path
# FACE_PRESENCE_MIN_AREA and must not be "unified" with it — the band
# is where the spike measured, whatever either floor does next.
# Demote, never delete: demoted boxes leave the significant set (and
# with it the protect spans and the non-face gates) and are counted
# on SceneEvidence as depicted_count, so the demotion stays auditable.
DEPICTED_GRID_BAND_MAX_AREA_FRAC = 0.002
# Row partners, as measured (chosen from the mechanism — printed walls
# are manufactured uniform — then counted, not swept for accuracy):
# centre-y within this share of the candidate's height, width within
# +/- this share of the candidate's width, at least this many partners.
DEPICTED_GRID_ROW_TOLERANCE = 0.4
DEPICTED_GRID_WIDTH_TOLERANCE = 0.3
DEPICTED_GRID_MIN_PARTNERS = 2


@dataclass(frozen=True)
class SceneEvidence:
    time: float
    face_area: float = 0.0
    face_span: float = 0.0
    face_count: int = 0
    face_center: float = 0.5
    motion: float = 0.0
    outside_motion: float = 0.0
    motion_spread: float = 0.0
    camera_motion: float = 0.0
    text_rows: int = 0
    cut: bool = False
    difference: float = 0.0
    # Horizontal span (0-1) of visible detail (edges, text, UI). Defaults to
    # the whole width, so unmeasured evidence never permits a zoomed fit.
    detail_left: float = 0.0
    detail_right: float = 1.0
    # Span of the regions a crop must not cut: faces plus text lines.
    protect_left: float = 0.0
    protect_right: float = 1.0
    # Text lines (placards, lower thirds, slides) cut by the face crop's edges.
    cut_text_lines: int = 0
    # Horizontal centroid (0-1) of the camera-compensated motion mass:
    # where this frame's movement actually is. The no-face crop fallback
    # anchors on it instead of the detail span's midpoint (a wide
    # detailed scene's span midpoint sits between its subjects). 0.5 —
    # the frame centre — when nothing moved or nothing was measured.
    motion_center: float = 0.5
    # Non-face subject extent (normalized xyxy). Two sources, in
    # precedence order: "bright" — the largest bright component's box
    # on a dark scene, the drone light-portrait class of subject no
    # detector can see; "yolox" — the union of YOLOX person boxes on a
    # sample with no significant face (helmets, backs, night shots).
    # subject_cov is the share of the frame the subject evidence
    # covers (bright coverage, resp. the union box's area). None /
    # 0.0 / None when neither signal engages, so absent evidence is
    # never mistaken for a centred subject.
    subject_box: Optional[Tuple[float, float, float, float]] = None
    subject_cov: float = 0.0
    subject_source: Optional[str] = None
    # Detector boxes demoted by the depicted grid screen (printed
    # portrait rows; see the constants block above): excluded from
    # face_count / face_span, the protect spans and the non-face
    # gates, and counted here so the demotion stays auditable.
    depicted_count: int = 0


class ContextAnalyzer:
    """Bounded-memory analysis of the frames already decoded for face tracking."""

    def __init__(self, person_detector=None):
        self.previous: Optional[np.ndarray] = None
        self.histogram: Optional[np.ndarray] = None
        # Optional gated person pass (media/person_detection.py): any
        # object with detect_persons(frame) -> [(x0, y0, x1, y1, score)]
        # in normalized coordinates. None disables the pass entirely —
        # the analysis entry point passes a lazily-opening YOLOX
        # detector; the pass is expensive (~0.7 s/frame), so it runs
        # only where measure()'s gates say a subject is otherwise
        # invisible.
        self._person_detector = person_detector

    @staticmethod
    def _text_components(gray: np.ndarray) -> list[tuple[float, float, int, int, int]]:
        # Text produces repeated small components aligned in rows, rather than
        # treating all high-frequency texture (grass/crowds) as text.
        h, _ = gray.shape
        binary = cv2.adaptiveThreshold(gray, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
                                       cv2.THRESH_BINARY_INV, 15, 9)
        count, _, stats, centers = cv2.connectedComponentsWithStats(binary)
        components = []
        for i in range(1, count):
            x, y, cw, ch, area = stats[i]
            if not (3 <= ch <= h * 0.075 and 1 <= cw <= ch * 1.5):
                continue
            if not (0.15 <= area / max(1, cw * ch) <= 0.9):
                continue
            cx, cy = centers[i]
            components.append((float(cx), float(cy), int(x), int(cw), int(ch)))
        return components

    @staticmethod
    def _text_rows(components, width: int, crop_left: int, crop_right: int) -> int:
        # Rows containing several text components outside the proposed crop.
        rows: dict[int, list[float]] = {}
        for cx, cy, *_ in components:
            if crop_left <= cx <= crop_right:
                continue
            rows.setdefault(int(cy // 8), []).append(cx)
        return sum(len(xs) >= 8 and max(xs) - min(xs) > width * 0.2 for xs in rows.values())

    @staticmethod
    def _text_lines(gray: np.ndarray) -> list[tuple[int, int, int, int]]:
        """Boxes (x0, y0, x1, y1) of text lines: signs, lower thirds, slides.

        Letter-sized components of either polarity (dark-on-light placards,
        light-on-dark lower thirds) are chained when they share a baseline,
        a similar height, and sit closer than a character height or so apart.
        At analysis resolution neighbouring letters often touch, so a
        component may be a few letters wide.
        """
        h, _ = gray.shape
        letters = []
        for image in (gray, 255 - gray):
            binary = cv2.adaptiveThreshold(image, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
                                           cv2.THRESH_BINARY_INV, 15, 9)
            count, _, stats, _ = cv2.connectedComponentsWithStats(binary)
            for i in range(1, count):
                x, y, cw, ch, area = stats[i]
                if 4 <= ch <= h * 0.075 and cw <= ch * 3 and 0.15 <= area / (cw * ch) <= 0.9:
                    letters.append((int(x), int(y), int(x + cw), int(y + ch)))
        letters.sort()
        parent = list(range(len(letters)))

        def root(i: int) -> int:
            while parent[i] != i:
                parent[i] = parent[parent[i]]
                i = parent[i]
            return i

        for i, (x0, y0, x1, y1) in enumerate(letters):
            ch = y1 - y0
            for j in range(i + 1, len(letters)):
                u0, v0, u1, v1 = letters[j]
                if u0 - x1 > ch * 1.2:
                    break
                other = v1 - v0
                if 0.6 * ch <= other <= ch / 0.6 and abs((v0 + v1) - (y0 + y1)) <= ch * 0.6:
                    parent[root(j)] = root(i)
        groups: dict[int, list[tuple[int, int, int, int]]] = {}
        for i, box in enumerate(letters):
            groups.setdefault(root(i), []).append(box)
        lines = []
        for boxes in groups.values():
            x0 = min(b[0] for b in boxes)
            x1 = max(b[2] for b in boxes)
            heights = sorted(b[3] - b[1] for b in boxes)
            if len(boxes) >= 4 and x1 - x0 >= heights[len(heights) // 2] * 4:
                lines.append((x0, min(b[1] for b in boxes), x1, max(b[3] for b in boxes)))
        return lines

    @staticmethod
    def _detail_span(gray: np.ndarray) -> tuple[float, float]:
        """Leftmost/rightmost columns containing visible detail, as 0-1 fractions.

        Flat letterbox margins and empty slide backgrounds have no edges; any
        column with a couple of edge pixels (text, UI, objects) counts as content.
        """
        h, w = gray.shape
        edges = cv2.Canny(gray, 50, 150)
        edges[:, :2] = 0
        edges[:, w - 2:] = 0
        columns = np.convolve((edges > 0).sum(axis=0).astype(np.float32), np.ones(3) / 3, "same")
        active = np.flatnonzero(columns >= 2)
        if not len(active):
            return 0.5, 0.5
        return float(active[0]) / w, float(active[-1] + 1) / w

    @staticmethod
    def _bright_subject(
        gray: np.ndarray,
    ) -> Tuple[Optional[Tuple[float, float, float, float]], float]:
        """Largest bright component's box (normalized xyxy) + coverage.

        The mask recipe is the spike's measured one: threshold at
        max(Otsu, 97th percentile), a 15x15 close (x2) to bridge the
        gaps of a dot-matrix subject, components of at least 0.1% of
        the frame. Returns (None, 0.0) unless the scene is dark AND
        the total bright coverage reaches the presence threshold —
        on a lit scene the same mask degenerates to sky/foliage and
        must say nothing at all.
        """
        if float(gray.mean()) > BRIGHT_DARK_SCENE_MAX_MEAN:
            return None, 0.0
        otsu, _ = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
        threshold = max(float(otsu), float(np.percentile(gray, 97)))
        mask = (gray > threshold).astype(np.uint8)
        mask = cv2.morphologyEx(
            mask, cv2.MORPH_CLOSE,
            cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (15, 15)),
            iterations=2,
        )
        count, _, stats, _ = cv2.connectedComponentsWithStats(mask, 8)
        h, w = gray.shape
        min_area = BRIGHT_COMPONENT_MIN_AREA_FRAC * h * w
        kept = [
            i for i in range(1, count)
            if stats[i, cv2.CC_STAT_AREA] >= min_area
        ]
        if not kept:
            return None, 0.0
        coverage = sum(
            int(stats[i, cv2.CC_STAT_AREA]) for i in kept
        ) / (h * w)
        if coverage < BRIGHT_SUBJECT_MIN_COVERAGE:
            return None, 0.0
        largest = max(kept, key=lambda i: stats[i, cv2.CC_STAT_AREA])
        x = int(stats[largest, cv2.CC_STAT_LEFT])
        y = int(stats[largest, cv2.CC_STAT_TOP])
        bw = int(stats[largest, cv2.CC_STAT_WIDTH])
        bh = int(stats[largest, cv2.CC_STAT_HEIGHT])
        return (x / w, y / h, (x + bw) / w, (y + bh) / h), float(coverage)

    def _person_subject(
        self, frame: np.ndarray
    ) -> Tuple[Optional[Tuple[float, float, float, float]], float]:
        """Union of the person detector's boxes (normalized xyxy) + coverage.

        The detector has already applied the person-class / >= 0.5
        rules. A group of people is one subject extent, the same way
        the face span covers every significant face; coverage is the
        union box's share of the frame.
        """
        persons = self._person_detector.detect_persons(frame)
        if not persons:
            return None, 0.0
        x0 = min(p[0] for p in persons)
        y0 = min(p[1] for p in persons)
        x1 = max(p[2] for p in persons)
        y1 = max(p[3] for p in persons)
        return (x0, y0, x1, y1), float((x1 - x0) * (y1 - y0))

    @staticmethod
    def _demote_grid_depicted(
        boxes: Sequence[Sequence[float]], width: int, height: int
    ) -> Tuple[Sequence[Sequence[float]], int]:
        """Split printed-portrait-row boxes off a sample's candidates.

        The measured grid rule (DEPICTED_FACES_SPIKE.md): within the
        sub-0.002 band, a box with >= DEPICTED_GRID_MIN_PARTNERS row
        partners — centre-y within DEPICTED_GRID_ROW_TOLERANCE x its
        height, width within +/- DEPICTED_GRID_WIDTH_TOLERANCE of its
        width — is depicted. Candidates and partners both come from
        the band only, so an above-band neighbour can neither condemn
        nor rescue a box. Returns (kept boxes, demoted count).
        """
        frame_area = float(max(1, width * height))
        band = [
            i
            for i, b in enumerate(boxes)
            if b[2] * b[3] / frame_area < DEPICTED_GRID_BAND_MAX_AREA_FRAC
        ]
        if len(band) <= DEPICTED_GRID_MIN_PARTNERS:
            return boxes, 0
        demoted = set()
        for i in band:
            _, by, bw, bh = boxes[i]
            centre_y = by + bh / 2
            partners = 0
            for j in band:
                if j == i:
                    continue
                other = boxes[j]
                if (
                    abs(other[1] + other[3] / 2 - centre_y)
                    <= DEPICTED_GRID_ROW_TOLERANCE * bh
                    and abs(other[2] - bw) <= DEPICTED_GRID_WIDTH_TOLERANCE * bw
                ):
                    partners += 1
                    if partners >= DEPICTED_GRID_MIN_PARTNERS:
                        break
            if partners >= DEPICTED_GRID_MIN_PARTNERS:
                demoted.add(i)
        if not demoted:
            return boxes, 0
        return [b for i, b in enumerate(boxes) if i not in demoted], len(demoted)

    def measure(self, frame: np.ndarray, boxes: Sequence[Sequence[float]], time: float) -> SceneEvidence:
        h, w = frame.shape[:2]
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        crop_width = min(w, h * 9 / 16)
        # Depicted grid screen: printed portrait rows leave the
        # candidate list before the significant set (and its >= 30%
        # trim) are built, so they never inflate the face evidence,
        # join the protect spans or close the non-face gates below.
        boxes, depicted_count = self._demote_grid_depicted(boxes, w, h)
        significant = sorted(boxes, key=lambda b: b[2] * b[3], reverse=True)
        area, center, span = 0.0, 0.5, 0.0
        if significant:
            x, y, fw, fh = significant[0]
            area = fw * fh / (w * h)
            center = (x + fw / 2) / w
            # Ignore tiny audience faces next to a clearly dominant presenter.
            significant = [b for b in significant if b[2] * b[3] >= fw * fh * 0.3]
            span = (max(b[0] + b[2] for b in significant) - min(b[0] for b in significant)) / w
        left = int(np.clip(center * w - crop_width / 2, 0, w - crop_width))
        right = int(left + crop_width)
        hist = cv2.calcHist([frame], [0, 1, 2], None, [8, 8, 8], [0, 256] * 3)
        cv2.normalize(hist, hist)
        motion = outside = spread = camera = 0.0
        motion_center = 0.5
        delta = 0.0
        cut = False
        if self.previous is not None:
            prev = self.previous
            delta = float(np.mean(cv2.absdiff(prev, gray)))
            hist_distance = cv2.compareHist(self.histogram, hist, cv2.HISTCMP_BHATTACHARYYA)
            cut = delta > 24 and hist_distance > 0.55
            points = cv2.goodFeaturesToTrack(prev, maxCorners=160, qualityLevel=0.02, minDistance=10)
            transform = None
            if not cut and points is not None and len(points) >= 12:
                moved, status, errors = cv2.calcOpticalFlowPyrLK(prev, gray, points, None)
                if moved is not None and status is not None:
                    back, back_status, _ = cv2.calcOpticalFlowPyrLK(gray, prev, moved, None)
                    valid = status.ravel() == 1
                    if back is not None and back_status is not None:
                        valid &= back_status.ravel() == 1
                        valid &= np.linalg.norm((back - points).reshape(-1, 2), axis=1) < 1.5
                    else:
                        valid[:] = False
                    if errors is not None:
                        valid &= errors.ravel() < 20
                    # Reverse-angle edits often share the same colors. A large
                    # image change plus collapsed correspondence detects these
                    # without confusing an ordinary, trackable camera pan.
                    if delta > 24 and (hist_distance > 0.12 or delta > 40) and np.mean(valid) < 0.2:
                        cut = True
                    source, target = points[valid], moved[valid]
                    if not cut and len(source) >= 12:
                        candidate, inliers = cv2.estimateAffinePartial2D(
                            source, target, method=cv2.RANSAC, ransacReprojThreshold=2.5)
                        if candidate is not None and inliers is not None and np.mean(inliers) >= 0.5:
                            scale = float(np.linalg.norm(candidate[:, 0]))
                            support = source[inliers.ravel() == 1].reshape(-1, 2)
                            # A team of players moving together is not camera
                            # motion. Demand support across the image height
                            # and width before compensating for the transform.
                            coverage = np.ptp(support, axis=0)
                            if 0.85 < scale < 1.18 and coverage[0] > w * 0.55 and coverage[1] > h * 0.55:
                                transform = candidate
                                camera = float(np.linalg.norm(candidate[:, 2]) / w)
            if not cut:
                valid_pixels = np.ones_like(gray, dtype=np.uint8)
                if transform is not None:
                    prev = cv2.warpAffine(prev, transform, (w, h))
                    valid_pixels = cv2.warpAffine(valid_pixels, transform, (w, h))
                # Low-frequency differences suppress codec noise and fine texture.
                diff = cv2.absdiff(cv2.GaussianBlur(prev, (7, 7), 0),
                                   cv2.GaussianBlur(gray, (7, 7), 0))
                active = ((diff > 22) & (valid_pixels > 0)).astype(np.float32)
                active[:int(h * 0.08)] = 0  # scoreboards / broadcast overlays
                active[int(h * 0.92):] = 0
                motion = float(active.mean())
                total = float(active.sum())
                outside = float((active[:, :left].sum() + active[:, right:].sum()) / max(1, total))
                columns = np.array_split(active, 6, axis=1)
                spread = sum(float(column.mean()) > 0.035 for column in columns) / 6
                column_mass = active.sum(axis=0)
                mass_total = float(column_mass.sum())
                if mass_total > 0:
                    motion_center = float(
                        (column_mass * np.arange(w, dtype=np.float32)).sum()
                        / mass_total
                    ) / w
        self.previous, self.histogram = gray, hist
        components = self._text_components(gray)
        # Text-line and detail extents only refine the layout. If they fail,
        # fall back to "no cut text, full-width detail" (the previous behaviour)
        # instead of discarding the whole clip analysis.
        cut_lines, protect_left, protect_right = 0, 0.0, 1.0
        detail_left, detail_right = 0.0, 1.0
        try:
            lines = self._text_lines(gray)
            margin = max(2, int(w * 0.005))
            cut_lines = sum(
                any(x0 < edge - margin and x1 > edge + margin for edge in (left, right) if 0 < edge < w)
                for x0, _, x1, _ in lines
            )
            protected = [(x0, x1) for x0, _, x1, _ in lines]
            protected += [(b[0], b[0] + b[2]) for b in significant]
            protect_left = min((a for a, _ in protected), default=w / 2) / w
            protect_right = max((b for _, b in protected), default=w / 2) / w
            detail_left, detail_right = self._detail_span(gray)
        except Exception as exc:  # pragma: no cover - defensive
            logger.warning("Text/detail measurement failed at %.2fs (%s); ignoring it", time, exc)
            cut_lines, protect_left, protect_right = 0, 0.0, 1.0
            detail_left, detail_right = 0.0, 1.0
        # Bright-subject extent: independent of the text/detail block —
        # its failure must not discard either measurement.
        subject_box, subject_cov = None, 0.0
        subject_source = None
        try:
            subject_box, subject_cov = self._bright_subject(gray)
            if subject_box is not None:
                subject_source = "bright"
        except Exception as exc:  # pragma: no cover - defensive
            logger.warning("Bright-subject measurement failed at %.2fs (%s); ignoring it", time, exc)
        # Gated YOLOX person pass: only when no significant face was
        # found AND bright did not already claim this sample. The gate
        # is the cost control — an ungated pass would add ~0.7 s to
        # every sample of every analysis.
        if (
            subject_box is None
            and not significant
            and self._person_detector is not None
        ):
            try:
                subject_box, subject_cov = self._person_subject(frame)
                if subject_box is not None:
                    subject_source = "yolox"
            except Exception as exc:  # pragma: no cover - defensive
                logger.warning("Person-subject measurement failed at %.2fs (%s); ignoring it", time, exc)
        return SceneEvidence(
            time, area, span, len(significant), center, motion, outside, spread, camera,
            self._text_rows(components, w, left, right), cut, delta,
            detail_left=detail_left, detail_right=detail_right,
            protect_left=max(0.0, protect_left), protect_right=min(1.0, protect_right),
            cut_text_lines=cut_lines,
            motion_center=motion_center,
            subject_box=subject_box,
            subject_cov=subject_cov,
            subject_source=subject_source,
            depicted_count=depicted_count,
        )


def context_scene_cuts(samples: Sequence[SceneEvidence]) -> list[float]:
    """Reject sustained rapid movement: an edit must be an isolated change spike.

    Optical-flow failure alone also happens during fast sports camera pans.
    Looking at both neighbors prevents those pans from becoming dozens of cuts.
    """
    cuts = []
    for i in range(1, len(samples)):
        sample = samples[i]
        neighbors = [samples[i - 1].difference]
        if i + 1 < len(samples):
            neighbors.append(samples[i + 1].difference)
        if sample.cut and sample.difference > max(24, 1.6 * max(neighbors)):
            cuts.append(sample.time)
    return cuts


def context_reason(sample: SceneEvidence, crop_fraction: float) -> Optional[str]:
    """Return the evidence requiring a full frame, or None for a safe face crop."""
    if sample.face_area < 0.008:
        return "no_reliable_close_subject"
    if sample.face_count == 1 and sample.face_span > crop_fraction * 0.95:
        return "subject_exceeds_crop"
    if sample.face_count >= 3 and sample.face_span > crop_fraction:
        return "subjects_spread_across_frame"
    # Two people wider than the 9:16 window cannot share a dominant-face crop,
    # even when the larger face is calm and bigger than 2.5% of the frame.
    # Speaker framing may still cut between them; otherwise the shot stays full frame.
    if sample.face_count >= 2 and sample.face_span > crop_fraction * 1.05:
        return "interaction_exceeds_crop"
    # A placard or lower third running past the crop edge would lose letters.
    if sample.cut_text_lines >= TEXT_CUT_MIN_LINES:
        return "text_cut_by_crop"
    peripheral = sample.face_center < 0.18 or sample.face_center > 0.82
    # A large corner webcam does not make the slide underneath disposable.
    if sample.text_rows >= 4 and (sample.face_area < 0.045 or peripheral):
        return "screen_content_outside_crop"
    if sample.face_area < 0.045 and peripheral:
        return "peripheral_presenter"
    if sample.motion > 0.065 and sample.outside_motion > 0.45 and sample.motion_spread >= 0.5:
        return "distributed_action"
    return None


def _soft_fit(shot: Sequence[SceneEvidence], reason: str) -> bool:
    """A fit chosen only because one steady speaker looks small (a wide shot)."""
    if reason != "no_reliable_close_subject" or not shot:
        return False
    single = sum(s.face_count == 1 and s.face_area > 0 for s in shot)
    return single / len(shot) >= SOFT_SINGLE_FACE_RATE


def stabilize_layout_plan(plan: list[dict]) -> list[dict]:
    """Give each composition a minimum hold so neighbouring shots stay coherent.

    A face crop shorter than LAYOUT_HOLD_SECONDS next to a fitted shot becomes
    a fit as well: widening never loses content. A short fitted shot between
    face crops becomes a crop only when it is soft (the same lone speaker in a
    wider shot) or a sub-second flash. Content, crowds and cut text stay fit.
    """
    plan = [dict(p) for p in plan]

    def groups():
        out, start = [], 0
        for i in range(1, len(plan) + 1):
            if i == len(plan) or plan[i]["kind"] != plan[start]["kind"]:
                out.append((start, i))
                start = i
        return out

    while True:
        runs = groups()
        candidates = []
        for index, (a, b) in enumerate(runs):
            length = plan[b - 1]["end"] - plan[a]["start"]
            if length >= LAYOUT_HOLD_SECONDS or len(runs) == 1:
                continue
            neighbours = [runs[j] for j in (index - 1, index + 1) if 0 <= j < len(runs)]
            if plan[a]["kind"] == "face":
                flip, rank = True, 1
            else:
                soft = all(p.get("soft") for p in plan[a:b])
                flip = soft or length < LAYOUT_FLASH_SECONDS
                # A brief wide shot of the lone speaker between two crops
                # joins them; otherwise fast close/wide cutting would widen
                # every crop and letterbox the whole clip.
                rank = 0 if soft and len(neighbours) == 2 else 2
            if flip:
                donor = max(neighbours, key=lambda r: plan[r[1] - 1]["end"] - plan[r[0]]["start"])
                candidates.append((rank, length, a, b, donor))
        if not candidates:
            break
        # Soft wide shots between crops become crops first; then short crops
        # widen; other short fits only inherit a crop once the crops around
        # them are known to hold.
        *_, a, b, donor = min(candidates)
        # Continue the composition of the shot right next to the flipped run.
        source = plan[donor[1] - 1] if donor[1] <= a else plan[donor[0]]
        for entry in plan[a:b]:
            entry["kind"] = source["kind"]
            entry["reason"] = source["reason"]
            entry["soft"] = source.get("soft", False)
        merged = [plan[0]]
        for entry in plan[1:]:
            last = merged[-1]
            if entry["kind"] == last["kind"] and entry["reason"] == last["reason"]:
                last["end"] = entry["end"]
                last["soft"] = last.get("soft", False) and entry.get("soft", False)
            else:
                merged.append(entry)
        plan = merged
    runs = groups()
    hard_fit = any(p["kind"] == "fit" and not p.get("soft")
                   and p["end"] - p["start"] >= LAYOUT_FLASH_SECONDS for p in plan)
    fit_duration = sum(p["end"] - p["start"] for p in plan if p["kind"] == "fit")
    duration = plan[-1]["end"] - plan[0]["start"] if plan else 0
    # Repeated crop/fit changes or a predominantly wide demonstration use one
    # full-context composition. First resolve soft/brief shots above, so a lone
    # speaker's wide angle does not needlessly shrink the whole clip.
    if hard_fit and (len(runs) >= 3 or (len(runs) > 1 and fit_duration > duration * .6)):
        return [{"start": plan[0]["start"], "end": plan[-1]["end"], "kind": "fit", "reason": "stable_context", "soft": False}]
    return plan


def build_context_layout_plan(samples: Sequence[SceneEvidence], scene_cuts: Sequence[float],
                              duration: float, crop_fraction: float) -> list[dict]:
    """Choose one stable layout per shot; uncertain shots preserve the full image.

    A brief missed face in an otherwise confident close-up does not change the
    layout. Sustained context risk wins over face tracking, including a small
    webcam over gameplay. Short shots then inherit their neighbours'
    composition (see ``stabilize_layout_plan``).
    """
    if duration <= 0 or not samples:
        return [{"start": 0.0, "end": max(0.0, duration), "kind": "fit", "reason": "analysis_unavailable"}]
    cuts = sorted({float(c) for c in scene_cuts if 0 < c < duration})
    boundaries = [0.0, *cuts, duration]
    plan = []
    for start, end in zip(boundaries, boundaries[1:]):
        shot = [s for s in samples if start <= s.time < end]
        reasons = [context_reason(s, crop_fraction) for s in shot]
        risks = [reason for reason in reasons if reason is not None]
        # Require recurrent evidence, but preserve a short shot with only one
        # available sample. This is a fraction of samples, not a speed threshold.
        fit = not shot or len(risks) / len(shot) >= 0.3
        reason = Counter(risks).most_common(1)[0][0] if risks and fit else "confident_subject"
        if not shot:
            reason = "analysis_unavailable"
        kind = "fit" if fit else "face"
        soft = kind == "fit" and _soft_fit(shot, reason)
        if plan and plan[-1]["kind"] == kind and plan[-1]["reason"] == reason:
            plan[-1]["end"] = end
            plan[-1]["soft"] = plan[-1]["soft"] and soft
        else:
            plan.append({"start": start, "end": end, "kind": kind, "reason": reason, "soft": soft})
    try:
        return stabilize_layout_plan(plan)
    except Exception as exc:  # pragma: no cover - defensive
        logger.warning("Layout hysteresis failed (%s); keeping per-shot layout", exc)
        return plan
