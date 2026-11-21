"""HookFinish v2: the first three seconds composed as one segment.

Viral Shorts stack four opening elements (viral-comparison queue #1):
cold-open mid-sentence, a frame-1 text hook, an opening settle-zoom and
a sound hit inside the first second. The pipeline already shipped the
first two plus B-0023's step punch-in; this module adds the missing two
behind one switch, ``HOOK_FINISH_V2`` (default OFF — with it off,
renders are byte-identical to the pre-v2 pipeline):

- **Settle-zoom** — the frame opens at ``SETTLE_START_ZOOM`` (1.3x),
  anchored on the face/subject, and eases back to 1.0 over
  ``SETTLE_SECONDS`` (1.7 s) on an ease-out cubic. Applied by
  ``media.hook_finish`` in the same pass as B-0023's hook cut.
- **First-second SFX guarantee** — the EDL audio plan
  (``media.edl_render.build_edl_audio_graph``, which also serves the
  plain layout path via ``media.audio_finish``) adds one library hit
  at ``FIRST_SECOND_SFX_AT`` when no planned SFX already lands inside
  the first second. The hit is sidechain-ducked under the voice, so
  it never masks the first words.

Composition with the existing opening treatments (both interactions
exist only while the switch is on):

- B-0023's punch-in (first step at the profile's ``first_cut_s``,
  2.6 s by default): the settle window normally ends at 1.7 s, before
  the punch. If a trained profile moves the first punch earlier, the
  settle clamps to end ``SETTLE_PUNCH_GAP`` before it, and is skipped
  entirely when even the clamped window would be too short to read
  (< ``SETTLE_MIN_SECONDS``) — the two zooms never stack.
- The EDL ken-burns opening ramp (a 1.0 -> 1.12 push-in over the
  first 2 s, ``auto_director.edl``): suppressed while v2 is on — the
  settle owns the opening; two opening zooms must not compound.
"""
from __future__ import annotations

import os
from typing import Any, Optional, Tuple

# --- the switch -----------------------------------------------------------

_ON_VALUES = ("on", "1", "true", "yes")


def hook_finish_v2_enabled() -> bool:
    """True only when HOOK_FINISH_V2 is explicitly switched on."""
    return os.getenv("HOOK_FINISH_V2", "off").strip().lower() in _ON_VALUES


# --- settle-zoom ----------------------------------------------------------

SETTLE_SECONDS = 1.7          # the viral settle duration (Zella: ~1.7 s)
SETTLE_START_ZOOM = 1.3       # frame 1 is punched in 1.3x on the face
SETTLE_MIN_SECONDS = 0.6      # a shorter settle would read as a glitch
SETTLE_PUNCH_GAP = 0.15       # the settle always ends this far before a punch

# Where the subject sits in a finished vertical frame with no facecam
# stack: the reframe crop centres the face-tracked subject, slightly
# above middle. (Fractions of width / height.)
DEFAULT_ANCHOR = (0.5, 0.45)


def settle_zoom_at(t: float, seconds: float = SETTLE_SECONDS,
                   start_zoom: float = SETTLE_START_ZOOM) -> float:
    """The settle curve: ease-out cubic from ``start_zoom`` to 1.0.

    Mirrors the expression embedded by :func:`settle_zoom_filter`, so
    tests can pin the curve without parsing ffmpeg syntax.
    """
    if t <= 0:
        return start_zoom
    if t >= seconds:
        return 1.0
    u = 1.0 - t / seconds
    return 1.0 + (start_zoom - 1.0) * u * u * u


def settle_seconds_for(first_punch_at: Optional[float]) -> Optional[float]:
    """Settle window length, composed with B-0023's first punch-in.

    ``None`` when the punch comes so early that no readable settle
    fits before it — the punch then owns the opening alone.
    """
    if first_punch_at is None:
        return SETTLE_SECONDS
    clamped = min(SETTLE_SECONDS, float(first_punch_at) - SETTLE_PUNCH_GAP)
    return clamped if clamped >= SETTLE_MIN_SECONDS else None


def settle_zoom_filter(width: int, height: int, anchor_x: float, anchor_y: float,
                       seconds: float, start_zoom: float = SETTLE_START_ZOOM) -> str:
    """Scale+crop chain easing the frame from ``start_zoom`` back to 1.0.

    The crop offset is written in terms of the crop filter's own input
    size (``iw``/``ih``), so the anchor point stays exactly fixed at
    every zoom step: offset = anchor * (z - 1), and since the anchor
    is inside the frame the offset can never leave the valid range.
    Outside the window the scale is the native size, mirroring
    ``hook_finish.punch_filter``'s conditional form.
    """
    extra = start_zoom - 1.0
    curve = (f"(1+{extra:.4f}*(1-t/{seconds:.3f})*(1-t/{seconds:.3f})"
             f"*(1-t/{seconds:.3f}))")
    w_expr = (f"if(between(t,0,{seconds:.3f}),"
              f"trunc({width}*{curve}/2)*2,{width})")
    h_expr = (f"if(between(t,0,{seconds:.3f}),"
              f"trunc({height}*{curve}/2)*2,{height})")
    return (f"scale=w='{w_expr}':h='{h_expr}':eval=frame,"
            f"crop={width}:{height}:"
            f"x='{anchor_x:.1f}*(iw/{width}-1)':y='{anchor_y:.1f}*(ih/{height}-1)'")


def _face_panel_frac() -> float:
    """Face-panel share of the vertical frame (gaming stack geometry)."""
    try:
        from .gaming_templates import FACE_PANEL_H, OUT_H

        return FACE_PANEL_H / OUT_H
    except Exception:  # pragma: no cover - constants exist in-tree
        return 0.45


def settle_anchor(edl: Optional[dict], width: int, height: int) -> Tuple[float, float]:
    """Anchor point (px) the settle-zoom holds fixed, in output coords.

    A facecam stack (Layout picker template, or hand-drawn boxes still
    in their render context) puts the face in the 45% panel — top by
    default, bottom when the drawn boxes say so — so the anchor is
    that panel's centre. Any other vertical clip was framed by the
    face-tracked reframe crop, which centres the subject: the default
    anchor is that centre.
    """
    panel = _face_panel_frac()
    boxes = None
    try:
        from .gaming_templates import MANUAL_BOXES

        boxes = MANUAL_BOXES.get()
    except Exception:  # pragma: no cover - module exists in-tree
        boxes = None
    if isinstance(boxes, dict):
        bottom = boxes.get("arrange") == "bottom"
        return (width * 0.5, height * (1.0 - panel / 2.0) if bottom else height * (panel / 2.0))
    reframe = (edl or {}).get("reframe") or {}
    if reframe.get("layout_template") in ("facecam_red", "facecam_gold"):
        return (width * 0.5, height * (panel / 2.0))
    return (width * DEFAULT_ANCHOR[0], height * DEFAULT_ANCHOR[1])


# --- first-second SFX guarantee ------------------------------------------

FIRST_SECOND_WINDOW = 1.0     # the hit must land inside the first second
FIRST_SECOND_SFX_AT = 0.05    # ...and it lands here, on the first frames
FIRST_SECOND_SFX_GAIN_DB = -9.0
# Mirrors hook_finish's own floor: clips shorter than this get no hook
# finish at all, so the guarantee does not apply to them either.
MIN_HOOK_CLIP_SECONDS = 6.0


def audio_guarantee_applies(edl: Optional[dict], duration: float) -> bool:
    """Should the audio plan guarantee a first-second SFX hit for this EDL?

    The population mirrors ``media.hook_finish``'s: non-Plain clips of
    at least ``MIN_HOOK_CLIP_SECONDS``. The EDL must also carry its
    audio block — the retimed voice-only fallback in ``edl_render``
    deliberately strips ``audio``/``sfx_events`` to ``{}``/``[]`` and
    must not acquire new sounds on its way through the same builder.
    """
    if not hook_finish_v2_enabled():
        return False
    if not isinstance(edl, dict):
        return False
    if str(edl.get("template") or "") == "plain":
        return False
    if duration < MIN_HOOK_CLIP_SECONDS:
        return False
    audio = edl.get("audio")
    return isinstance(audio, dict) and bool(audio)


def hook_hit_categories(edl: Optional[dict]) -> list:
    """Library SFX categories for the guaranteed hit, best first.

    A facecam-stack (reaction) moment takes an impact — the sound the
    reaction lands with. Everything else takes a whoosh, the sound of
    the settle-zoom's own motion. The remaining names are fallbacks in
    case a trimmed library lacks the first choice.
    """
    reframe = (edl or {}).get("reframe") or {}
    if reframe.get("layout_template") in ("facecam_red", "facecam_gold"):
        return ["impact", "whoosh", "riser", "pop"]
    return ["whoosh", "impact", "riser", "pop"]
