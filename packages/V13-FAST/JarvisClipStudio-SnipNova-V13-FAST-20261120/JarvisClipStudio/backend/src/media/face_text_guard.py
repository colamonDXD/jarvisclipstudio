"""Face guard for burned-in text: captions and the pinned hook title.

Hard rule (user-proven defect, 2026-11-11): a rendered caption block or
the pinned title block must NEVER intersect a face box in the OUTPUT
frame. Template positions, reference-look overrides and brand positions
are preferences; this guard outranks them all and moves the block to the
nearest face-free position instead.

The guard is pure geometry over boxes in output pixels. Its inputs are
the per-shot face boxes the reframe planner already computes (transformed
to output coordinates and stamped on the EDL by
``media.reframe_plan``), expanded by a small safety margin. Placement
policy when the preferred position collides:

- Consider only faces whose x-range overlaps the text block's x-range
  (a face beside the text does not constrain it).
- The free vertical bands are what remains of the safe area after the
  (expanded) face y-ranges are subtracted.
- If the preferred centre already clears every face, it is returned
  unchanged — no collision, no movement, byte-identical placement.
- Otherwise the block moves to the NEAREST face-free position: among
  the free bands that fit it, the one whose placement is closest to the
  preferred centre (size breaks ties), centred as close to the
  preferred centre as the band allows. In practice that is the band
  below a single speaker's chin, or the inter-tile gap band of a split
  shot for the title. Within a shot the face boxes are constant, so
  every caption chunk of the shot lands at the same centre — no jitter
  between lines.
- If no band fits the block, it is centred in the largest band anyway
  (least possible overlap, kept on-frame), and the caller may retry
  with a smaller block (the hook title shrinks its font before
  accepting overlap).
"""

from typing import List, Optional, Sequence, Tuple

Box = Tuple[float, float, float, float]  # (x, y, w, h) in output pixels

FACE_TEXT_MARGIN_PX = 12


def expand_box(box: Box, margin: float, width: int, height: int) -> Box:
    x, y, w, h = box
    x0 = max(0.0, x - margin)
    y0 = max(0.0, y - margin)
    x1 = min(float(width), x + w + margin)
    y1 = min(float(height), y + h + margin)
    return (x0, y0, max(0.0, x1 - x0), max(0.0, y1 - y0))


# Sub-pixel tolerance: a block whose edge lands exactly on a face
# boundary (the normal outcome of band placement) must count as
# touching, not intersecting — float noise at the boundary is ~1e-13,
# and rendered text rasterizes to whole pixels anyway.
_EPS = 0.5


def boxes_intersect(a: Box, b: Box) -> bool:
    return (
        a[0] + _EPS < b[0] + b[2]
        and b[0] + _EPS < a[0] + a[2]
        and a[1] + _EPS < b[1] + b[3]
        and b[1] + _EPS < a[1] + a[3]
    )


def intersection_area(a: Box, b: Box) -> float:
    if not boxes_intersect(a, b):
        return 0.0
    x_overlap = min(a[0] + a[2], b[0] + b[2]) - max(a[0], b[0])
    y_overlap = min(a[1] + a[3], b[1] + b[3]) - max(a[1], b[1])
    return max(0.0, x_overlap) * max(0.0, y_overlap)


def _x_overlapping_faces(
    block_x: float, block_w: float, faces: Sequence[Box]
) -> List[Box]:
    return [
        face
        for face in faces
        if face[0] < block_x + block_w and block_x < face[0] + face[2]
    ]


def free_bands(
    faces: Sequence[Box],
    block_x: float,
    block_w: float,
    top_limit: float,
    bottom_limit: float,
) -> List[Tuple[float, float]]:
    """Free vertical intervals inside [top_limit, bottom_limit].

    Only faces overlapping the block horizontally constrain the bands.
    ``faces`` must already be margin-expanded by the caller.
    """
    spans = []
    for face in _x_overlapping_faces(block_x, block_w, faces):
        spans.append((face[1], face[1] + face[3]))
    spans.sort()
    bands: List[Tuple[float, float]] = []
    cursor = float(top_limit)
    for span_top, span_bottom in spans:
        if span_top > cursor:
            bands.append((cursor, min(span_top, float(bottom_limit))))
        cursor = max(cursor, span_bottom)
        if cursor >= bottom_limit:
            break
    if cursor < bottom_limit:
        bands.append((cursor, float(bottom_limit)))
    return [(top, bottom) for top, bottom in bands if bottom - top > 0]


def guarded_centre_y(
    preferred_centre: float,
    block_x: float,
    block_w: float,
    block_h: float,
    faces: Sequence[Box],
    width: int,
    height: int,
    *,
    margin: float = FACE_TEXT_MARGIN_PX,
    top_limit: float = 0.0,
    bottom_limit: Optional[float] = None,
) -> float:
    """Centre y for a text block that never intersects a face box.

    Returns ``preferred_centre`` unchanged when the block placed there
    already clears every (margin-expanded) face. ``top_limit`` /
    ``bottom_limit`` bound the vertical safe area the block's CENTRE may
    use when it has to move (the block is clamped inside the chosen band,
    so its edges stay within the band, which itself sits inside the
    limits expanded by half the block height on each side — callers pass
    centre limits, and band edges are derived from them).
    """
    if bottom_limit is None:
        bottom_limit = float(height)
    if not faces or block_h <= 0:
        return preferred_centre
    expanded = [expand_box(face, margin, width, height) for face in faces]
    half = block_h / 2.0
    preferred_box = (block_x, preferred_centre - half, block_w, block_h)
    if not any(boxes_intersect(preferred_box, face) for face in expanded):
        return preferred_centre

    # Bands are computed over the span the block's EDGES may occupy: the
    # centre limits widened by half the block on each side, capped at
    # the frame itself.
    edge_top = max(0.0, top_limit - half)
    edge_bottom = min(float(height), bottom_limit + half)
    bands = free_bands(expanded, block_x, block_w, edge_top, edge_bottom)
    if not bands:
        return preferred_centre
    fitting = [band for band in bands if band[1] - band[0] >= block_h]

    def placement(band: Tuple[float, float]) -> float:
        return min(max(preferred_centre, band[0] + half), band[1] - half)

    if fitting:
        # Nearest face-free placement wins; the larger band breaks ties.
        best = min(
            fitting,
            key=lambda band: (
                abs(placement(band) - preferred_centre),
                -(band[1] - band[0]),
            ),
        )
        return placement(best)
    # No band fits: centre in the largest band (least overlap possible),
    # clamped so the block stays on the frame.
    best = max(bands, key=lambda band: band[1] - band[0])
    centre = (best[0] + best[1]) / 2.0
    if block_h < height:
        centre = min(max(centre, half), height - half)
    return centre


def block_fits_face_free(
    centre_y: float,
    block_x: float,
    block_w: float,
    block_h: float,
    faces: Sequence[Box],
    width: int,
    height: int,
    *,
    margin: float = FACE_TEXT_MARGIN_PX,
) -> bool:
    """Whether a block centred at ``centre_y`` clears every face."""
    if not faces:
        return True
    box = (block_x, centre_y - block_h / 2.0, block_w, block_h)
    return not any(
        boxes_intersect(box, expand_box(face, margin, width, height))
        for face in faces
    )
