"""Per-task framing mode: how the source sits inside the 9:16 output.

Named by Luffy's product question (2026-11-16): shown two finished
clips — one with the whole 16:9 picture fitted inside the vertical
frame between blurred bands, one zoomed full-bleed with the sides cut
away — he asked which template gives the first one, and the honest
answer was that no control could promise it: the layout planner
decides shot by shot, and Plain always crops. Framing mode is that
missing control, kept deliberately separate from ``template_mode``:
the template decides WHAT is done to the clip (B-roll, music, SFX,
cards, effects); framing decides only how the picture is placed.

Three values travel on the task row (``tasks.framing_mode``):

- ``"auto"`` — the layout planner decides per shot, exactly as it
  always has (``media.scene_context.build_context_layout_plan``:
  fit for wide / stable-context shots, a face-tracked crop otherwise).
- ``"fit"`` — ALWAYS FIT: the whole source frame inside the output,
  blurred bands filling the top and bottom (the plain letterbox fit,
  ``media.reframing.FIT_CHAIN``). Nothing is ever cropped away.
- ``"crop"`` — ALWAYS CROP: a full-bleed face-tracked crop for every
  shot — the framing Plain uses — but as a framing choice only. It
  drags none of Plain's other restrictions with it: an Auto-template
  task framed ``"crop"`` keeps its B-roll, music, SFX and effects.

NULL on the row means the task predates the control; the template
default then decides, exactly as before (Plain tasks crop, Auto tasks
let the planner decide). An explicitly stored value always wins over
that template default — including ``"auto"``, which on a Plain task
means the planner decides the framing while the rest of the Plain
contract (no B-roll, music, SFX, cards or effects) still applies.
"""

from __future__ import annotations

from typing import Optional

from .template_mode import is_plain

FRAMING_AUTO = "auto"
FRAMING_FIT = "fit"
FRAMING_CROP = "crop"

FRAMING_MODES = (FRAMING_AUTO, FRAMING_FIT, FRAMING_CROP)


def validate_framing_mode(value: object) -> Optional[str]:
    """Normalise a create-form framing choice, or None when absent.

    Mirrors ``validate_template_mode``: a missing value is not an error
    (older clients and non-form task creators simply predate the
    control), but a present value must be one of ``FRAMING_MODES``.
    The API layer turns the raised error into the 422 it already
    returns for the sibling validators.
    """
    if value is None:
        return None
    if not isinstance(value, str) or value.strip().lower() not in FRAMING_MODES:
        raise ValueError(
            "Invalid framing_mode: expected one of "
            + ", ".join(FRAMING_MODES)
        )
    return value.strip().lower()


def resolve_framing_mode(
    framing_mode: Optional[str],
    template_mode: Optional[str],
) -> str:
    """The framing a render must use, from the two stored choices.

    An explicit framing choice always wins. When the task never made
    one (NULL), the template default applies — Plain crops, anything
    else lets the planner decide — which is precisely the behaviour
    every task had before this control existed.
    """
    if framing_mode in FRAMING_MODES:
        return framing_mode
    return FRAMING_CROP if is_plain(template_mode) else FRAMING_AUTO
