"""Per-task template mode: Auto template vs Plain — just cuts.

Named by Luffy's product decision (2026-11-10): the auto template kept
inserting things he never asked for — stock B-roll cutaways matched on a
single transcript word, a music bed, SFX accents, info cards, profile
transitions — and framed speakers in blurred-pad strips. Plain mode is
the contract he asked for instead: the complete clip with cuts, silence
tightening, captions and the hook title, full-frame — and nothing else.
He edits everything else himself.

Two values travel on the task row (``tasks.template_mode``):

- ``"auto"`` — the historical pipeline: the niche profile's full
  template treatment (B-roll, music, SFX, cards, transitions, effects).
- ``"plain"`` — cuts only: :func:`plainify_edl` strips every
  template-driven element from the EDL before it renders, the music
  chain treats the task as Music Off regardless of the music control,
  and the reframe planner produces an always-full-frame tight crop
  (``media.reframe_plan.build_plain_reframe_plan``) — never a
  blurred-pad fit, never a split screen.

NULL on the row means the task predates the control and behaves as
``"auto"`` — the same legacy rule ``music_mode`` uses. The create form's
default for NEW tasks is Plain; it always sends the field, so a stored
NULL only ever means a legacy or non-form task.
"""

from __future__ import annotations

from typing import Any, Dict, Optional

TEMPLATE_AUTO = "auto"
TEMPLATE_PLAIN = "plain"

TEMPLATE_MODES = (TEMPLATE_AUTO, TEMPLATE_PLAIN)


def validate_template_mode(value: object) -> Optional[str]:
    """Normalise a create-form template choice, or None when absent.

    Mirrors ``validate_music_mode``: a missing value is not an error
    (older clients and non-form task creators simply predate the
    control), but a present value must be one of ``TEMPLATE_MODES``.
    The API layer turns the raised error into the 422 it already returns
    for the sibling validators.
    """
    if value is None:
        return None
    if not isinstance(value, str) or value.strip().lower() not in TEMPLATE_MODES:
        raise ValueError(
            "Invalid template_mode: expected one of "
            + ", ".join(TEMPLATE_MODES)
        )
    return value.strip().lower()


def is_plain(template_mode: Optional[str]) -> bool:
    """True when a stored template choice asks for the Plain pipeline."""
    return template_mode == TEMPLATE_PLAIN


def plainify_edl(edl: Dict[str, Any]) -> Dict[str, Any]:
    """Strip every template-driven element from a built EDL, in place.

    The EDL keeps exactly what Plain promises — its segments (as hard
    cuts), its captions block and its hook title block — and loses:

    - B-roll placements and the legacy SFX markers/events,
    - overlay events (info cards, lower thirds),
    - zoom keyframes (punch-ins and every other planned effect),
    - profile transitions and speed ramps (joins become plain concats),
    - the music bed (the exact silence ``apply_music_off`` stamps, so
      the renderer and QC see the one proven "no music" shape),
    - the colour LUT, beat-grid and scene-rule blocks,
    - any Brand Kit render blocks (bumpers, watermark, template/title
      overrides) and the signature-transition and audio-flash stamps.
      A kit is data the user saved, and it stays fully in force for
      Auto tasks, but Plain's contract is that nothing is composited
      over or stitched onto his footage.

    Also stamps ``edl["template"] = "plain"`` so every downstream
    consumer that receives the EDL (reframe attachment, re-renders, the
    deliverable bundle) can honour the same contract without re-reading
    the task row.
    """
    for segment in edl.get("segments") or []:
        if not isinstance(segment, dict):
            continue
        segment["transition_in"] = "none"
        segment["transition_duration"] = 0
        for key in ("speed", "speed_reason", "speed_at"):
            segment.pop(key, None)
    edl["zoom_keyframes"] = []
    edl["overlays"] = []
    edl["broll"] = []
    edl["sfx"] = []
    edl["sfx_events"] = []
    audio = edl.setdefault("audio", {})
    audio["music_track"] = ""
    audio["music_mood"] = "none"
    audio["music_volume"] = 0.0
    if "music_volume_0_to_1" in audio:
        audio["music_volume_0_to_1"] = 0.0
    audio["ducking_db"] = 0.0
    audio["music_source"] = "user_none"
    edl["color"] = {"lut": "none"}
    edl.pop("beat_grid", None)
    edl.pop("scene_rules", None)
    for key in (
        "brand_template_override",
        "brand_title",
        "brand_watermark",
        "brand_bumpers",
        "reference_look_override",
        "signature_transition",
        "audio_flashes",
    ):
        edl.pop(key, None)
    edl["template"] = TEMPLATE_PLAIN
    return edl
