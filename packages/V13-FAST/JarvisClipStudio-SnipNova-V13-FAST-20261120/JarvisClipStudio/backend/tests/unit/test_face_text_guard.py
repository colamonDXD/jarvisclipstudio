"""Face guard placement policy: text blocks never intersect face boxes.

Synthetic geometry only — the render-level proof lives in
test_caption_face_guard_render.py. The policy contract:

- No collision at the preferred position -> the position is returned
  byte-identically (existing renders must not shift by a pixel).
- Collision -> the block moves to the largest face-free band that fits
  it (ties: nearest the preferred centre), still inside the safe-area
  centre limits.
- Split-screen geometry: both tiles' faces constrain the bands; the
  inter-tile gap band is chosen when it is the largest fitting band.
- No faces -> unchanged.
"""

from src.media.face_text_guard import (
    FACE_TEXT_MARGIN_PX,
    block_fits_face_free,
    boxes_intersect,
    expand_box,
    free_bands,
    guarded_centre_y,
)

W, H = 1080, 1920
MARGIN = FACE_TEXT_MARGIN_PX


def _hits(centre, block_w, block_h, faces, block_x=None):
    if block_x is None:
        block_x = (W - block_w) / 2
    box = (block_x, centre - block_h / 2, block_w, block_h)
    return any(
        boxes_intersect(box, expand_box(face, MARGIN, W, H)) for face in faces
    )


def test_no_faces_returns_preferred_unchanged():
    assert guarded_centre_y(960.0, 90, 900, 100, [], W, H) == 960.0


def test_no_collision_returns_preferred_byte_identical():
    # Face high in the frame; the caption's preferred centre (0.80) is
    # far below it. Nothing may move.
    faces = [(300.0, 300.0, 480.0, 420.0)]
    centre = guarded_centre_y(
        1536.0, 90, 900, 100, faces, W, H, top_limit=96, bottom_limit=1560
    )
    assert centre == 1536.0


def test_face_beside_the_block_does_not_constrain_it():
    # A narrow caption block centred in the frame; the face is entirely
    # to its left at the same height. No x-overlap -> no move.
    faces = [(0.0, 800.0, 200.0, 400.0)]
    centre = guarded_centre_y(
        960.0, 340, 400, 100, faces, W, H, top_limit=96, bottom_limit=1560
    )
    assert centre == 960.0


def test_centre_template_face_collision_moves_below_the_face():
    # The Bert defect: meme template centre 0.50, a tight-crop face
    # spanning y 560..1060 (mouth/chin inside the caption's band).
    faces = [(270.0, 560.0, 540.0, 500.0)]
    preferred = 960.0
    assert _hits(preferred, 900, 110, faces)
    centre = guarded_centre_y(
        preferred, 90, 900, 110, faces, W, H, top_limit=96, bottom_limit=1560
    )
    assert centre != preferred
    assert not _hits(centre, 900, 110, faces)
    # Below the face (chin -> lower third), not above it: the band below
    # (1072..1650 of edge space) is larger than the band above.
    assert centre > 1060


def test_margin_is_respected():
    # Face bottom at 1000; a block whose edge sits exactly at the face
    # edge still collides once the 12px margin is applied, and the guard
    # must push it the extra margin clear.
    faces = [(270.0, 500.0, 540.0, 500.0)]
    centre = 1000 + MARGIN + 40  # block top exactly margin-clear
    assert not _hits(centre + 40, 900, 80, faces)  # helper uses centre of block
    moved = guarded_centre_y(
        1000.0 + 40, 90, 900, 80, faces, W, H, top_limit=96, bottom_limit=1560
    )
    top = moved - 40
    assert top >= 1000 + MARGIN - 1e-9


def test_split_geometry_title_moves_into_the_gap_band():
    # Split screen: top face y 190..700, bottom face y 1010..1560. The
    # pinned title (block 230 tall) prefers the top band (centre ~345)
    # and collides with the top face's eyes. The gap band between the
    # faces is the largest fitting band -> the title lands there.
    faces = [
        (250.0, 190.0, 580.0, 510.0),   # top tile face
        (240.0, 1010.0, 600.0, 550.0),  # bottom tile face
    ]
    preferred = 230 + 115  # margin_v + half of a 230px block
    assert _hits(preferred, 900, 230, faces)
    centre = guarded_centre_y(
        preferred, 90, 900, 230, faces, W, H,
        top_limit=preferred, bottom_limit=1750,
    )
    assert not _hits(centre, 900, 230, faces)
    top = centre - 115
    bottom = centre + 115
    assert top >= 700 + MARGIN - 1e-9
    assert bottom <= 1010 - MARGIN + 1e-9


def test_caption_in_split_prefers_the_gap_when_it_fits():
    faces = [
        (250.0, 190.0, 580.0, 510.0),
        (240.0, 1010.0, 600.0, 550.0),
    ]
    # The gap band after margins is 712..998; a 110px block centred at
    # 880 (825..935) is genuinely free there and must not move.
    centre = guarded_centre_y(
        880.0, 90, 900, 110, faces, W, H, top_limit=96, bottom_limit=1560
    )
    assert centre == 880.0
    # Centred exactly at the seam (960) the same block's bottom edge
    # (1015) crosses the bottom face's margin (998): it slides up just
    # enough to sit margin-clear in the gap.
    centre = guarded_centre_y(
        960.0, 90, 900, 110, faces, W, H, top_limit=96, bottom_limit=1560
    )
    assert centre == 943.0
    assert not _hits(centre, 900, 110, faces)


def test_block_taller_than_any_band_centres_in_the_largest():
    faces = [(100.0, 400.0, 880.0, 900.0)]
    centre = guarded_centre_y(
        960.0, 90, 900, 700, faces, W, H, top_limit=96, bottom_limit=1700
    )
    # Free edge space: 0..388 above the (expanded) face, 1312..1920
    # below. Neither fits a 700px block; the larger band is below, and
    # the block centres there clamped on-frame (1920 - 350).
    assert centre == 1570.0
    assert centre - 350 >= 0 and centre + 350 <= H


def test_free_bands_subtracts_only_x_overlapping_faces():
    faces = [
        (0.0, 500.0, 200.0, 200.0),    # left of the block: ignored
        (300.0, 900.0, 400.0, 200.0),  # overlaps: splits the space
    ]
    bands = free_bands(faces, 340, 400, 0.0, 1920.0)
    assert bands == [(0.0, 900.0), (1100.0, 1920.0)]


def test_block_fits_face_free_helper():
    faces = [(270.0, 560.0, 540.0, 500.0)]
    assert not block_fits_face_free(960.0, 90, 900, 110, faces, W, H)
    assert block_fits_face_free(1400.0, 90, 900, 110, faces, W, H)
    assert block_fits_face_free(960.0, 90, 900, 110, [], W, H)
