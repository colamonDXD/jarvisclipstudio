"""What fills the bars when a wide frame is shown whole on the 9:16 canvas.

Root cause #4 of clip1.mp4 (handoff/quality-clip1/ROOT_CAUSE.md): a "fit"
shot puts the 16:9 picture in the middle and fills the top and bottom
thirds with a BLURRED, ENLARGED COPY OF THE SAME PICTURE. On a wide crowd
shot two thirds of every frame were smeared imagery; viewers read it as a
rendering defect ("blurred transitions").

``FIT_PAD``:
- ``blur`` (default until the owner approves the corrected render, per
  Sanji Q15): today's blurred copy, byte-identical filter strings.
- ``ambient``: the frame averaged to ONE colour, darkened and desaturated.
  The bars carry the shot's colour mood but no imagery, so the eye stays
  on the real picture and captions/titles sit on a clean field. (A 3x5
  grid stretched up was tried first: ffmpeg's upscale of so tiny an input
  showed hard-edged blocks in the render, so one colour it is.)
"""
from __future__ import annotations

import os

FIT_PAD_MODES = ("blur", "ambient")

_AMBIENT_TAIL = (
    "scale=1:1:flags=area,scale=1080:1920:flags=neighbor,"
    "eq=brightness=-0.20:saturation=0.55,setsar=1"
)


def fit_pad_mode() -> str:
    value = os.getenv("FIT_PAD", "blur").strip().lower()
    return value if value in FIT_PAD_MODES else "blur"


def edl_backdrop_chain(blur_chain: str) -> str:
    """The EDL compositor's backdrop chain (``blur_chain`` = today's)."""
    if fit_pad_mode() == "ambient":
        return _AMBIENT_TAIL + ",format=yuv420p"
    return blur_chain


def full_frame_background(blur_sigma: int) -> str:
    """The ``[bgsrc]...[bg];`` fragment of reframing.build_full_frame_filter."""
    if fit_pad_mode() == "ambient":
        return f"[bgsrc]{_AMBIENT_TAIL}[bg];"
    return (
        "[bgsrc]scale=540:960:force_original_aspect_ratio=increase,crop=540:960,"
        f"gblur=sigma={blur_sigma}:steps=2,scale=1080:1920:flags=bilinear,setsar=1[bg];"
    )
