"""Signature transitions + the transition->SFX map (Viral Scout Pack 1+2).

Two data-driven rules from the viral-editing scout (2026-11-09):

1. **One signature transition per clip, early.** Viral shorts do not
   sprinkle random transitions everywhere; they plant ONE recognisable
   transition in the first seconds — the first one lands at ~3-5s —
   and let the rest of the edit cut plainly. A profile declares its
   signature in ``transitions.signature`` (``{"name", "duration_sec"}``);
   :func:`apply_signature_transition` stamps it onto the first suitable
   join inside the window and downgrades any decorative join before it,
   so the signature is the first decorative moment of the clip. With no
   suitable join (a one-segment clip, or every join far from the
   window) there is no signature at all — it is never forced mid-shot.

2. **Every transition type has a characteristic sound.** The EDL's
   auto-SFX emitter used to play the same generic whoosh on every
   crossfade. :data:`TRANSITION_SFX_MAP` pairs each transition name
   with the bundled SFX it should sound like and the gain it plays at.
   Only canonical names from ``edl.SFX_EVENT_NAMES`` are ever emitted
   (they resolve by file stem in the user's SFX library and are skipped
   silently when the file is absent). A transition with no pairing
   stays silent — no sound is invented for it.

This module is pure (no I/O): the caller (``auto_director.edl``) owns
segments, profiles and emission. Plain mode needs no branch here —
``plainify_edl`` already forces every join to a hard cut and empties
the SFX events, and it pops the signature stamp this module's caller
records, so a plain EDL never carries either rule's output.
"""

from __future__ import annotations

from typing import Any, Optional

# The signature window (seconds, clip-relative) and how far outside it
# the nearest-join fallback may reach.
SIGNATURE_WINDOW_SEC = (3.0, 5.0)
SIGNATURE_FALLBACK_SEC = 2.0
SIGNATURE_DURATION_MIN_SEC = 0.15
SIGNATURE_DURATION_MAX_SEC = 0.8
SIGNATURE_DURATION_DEFAULT_SEC = 0.3

_CUT_NAMES = {"cut", "hard_cut", "none", ""}

# The full xfade catalog the renderer resolves (media.edl_render's
# XFADE_TRANSITIONS) plus the three profile-facing aliases its
# TRANSITION_MAP adds. Kept as data here so this module stays free of
# renderer imports; a unit test pins this set against the renderer's
# own catalog so the two can never drift apart silently.
_MOVEMENT_NAMES = (
    "wipeleft", "wiperight", "wipeup", "wipedown",
    "wipetl", "wipetr", "wipebl", "wipebr",
    "slideleft", "slideright", "slideup", "slidedown",
    "smoothleft", "smoothright", "smoothup", "smoothdown",
    "coverleft", "coverright", "coverup", "coverdown",
    "revealleft", "revealright", "revealup", "revealdown",
    "hlwind", "hrwind", "vuwind", "vdwind",
    "hlslice", "hrslice", "vuslice", "vdslice",
    "diagtl", "diagtr", "diagbl", "diagbr",
    "circleopen", "circleclose", "vertopen", "vertclose",
    "horzopen", "horzclose",
    "radial", "distance", "squeezeh", "squeezev",
    "circlecrop", "rectcrop", "zoomin",
)
_FADE_NAMES = (
    "fade", "dissolve", "fadeblack", "fadewhite",
    "fadegrays", "fadefast", "fadeslow", "hblur",
)
_ALIAS_NAMES = ("wipe", "slide", "cross_dissolve")

#: Every transition name the signature rule may place and the SFX map
#: covers: the 58-name xfade catalog + the profile aliases.
KNOWN_TRANSITION_NAMES = frozenset(
    _MOVEMENT_NAMES + _FADE_NAMES + ("pixelize",) + _ALIAS_NAMES
)

#: Transition name -> (SFX event name, gain dB), or None for silence.
#: Movement transitions whoosh at the level crossfades have always
#: played at; fades swell more softly; the pixelize glitch pops.
TRANSITION_SFX_MAP: dict[str, Optional[tuple[str, float]]] = {
    **{name: ("whoosh", -6.0) for name in _MOVEMENT_NAMES},
    **{name: ("whoosh", -10.0) for name in _FADE_NAMES},
    "pixelize": ("pop", -3.0),
    "wipe": ("whoosh", -6.0),
    "slide": ("whoosh", -6.0),
    "cross_dissolve": ("whoosh", -10.0),
}


def transition_sfx(name: Any) -> Optional[tuple[str, float]]:
    """The (SFX name, gain dB) paired with a transition, or None.

    None means the transition plays silently: cut names, unknown
    names, and any catalog name a future edit deliberately unpairs.
    """
    if not isinstance(name, str):
        return None
    return TRANSITION_SFX_MAP.get(name.strip().lower())


def signature_for_profile(
    profile: Optional[dict[str, Any]],
) -> Optional[tuple[str, float]]:
    """The profile's declared signature as (name, duration_sec).

    Reads ``transitions.signature`` — a dict with a ``name`` that must
    resolve to a real crossfade (a cut name or an unknown name is not
    a signature) and an optional ``duration_sec`` clamped to a visible
    but brief range. Anything undeclared or invalid returns None and
    the clip simply gets no signature.
    """
    if not isinstance(profile, dict):
        return None
    transitions = profile.get("transitions")
    if not isinstance(transitions, dict):
        return None
    raw = transitions.get("signature")
    if not isinstance(raw, dict):
        return None
    name = raw.get("name")
    if not isinstance(name, str):
        return None
    name = name.strip().lower()
    if name not in KNOWN_TRANSITION_NAMES:
        return None
    try:
        duration = float(raw.get("duration_sec", SIGNATURE_DURATION_DEFAULT_SEC))
    except (TypeError, ValueError):
        duration = SIGNATURE_DURATION_DEFAULT_SEC
    duration = min(
        SIGNATURE_DURATION_MAX_SEC,
        max(SIGNATURE_DURATION_MIN_SEC, duration),
    )
    return name, round(duration, 3)


def _is_decorative(segment: dict[str, Any]) -> bool:
    """A join that would render as something other than a plain cut."""
    name = str(segment.get("transition_in", "cut")).strip().lower()
    try:
        duration = float(segment.get("transition_duration", 0) or 0)
    except (TypeError, ValueError):
        duration = 0.0
    return duration > 0 and name not in _CUT_NAMES


def choose_signature_join(
    segments: list[dict[str, Any]],
    window: tuple[float, float] = SIGNATURE_WINDOW_SEC,
    fallback_sec: float = SIGNATURE_FALLBACK_SEC,
) -> Optional[int]:
    """Index of the segment whose join carries the signature, or None.

    The first join inside the window wins. Otherwise the join nearest
    the window is used when it sits within ``fallback_sec`` of it;
    anything farther (or a clip with no joins at all) gets no
    signature — the moment is never forced mid-shot.
    """
    joins = [
        (index, float(segments[index].get("start", 0) or 0))
        for index in range(1, len(segments))
    ]
    if not joins:
        return None
    low, high = window
    for index, start in joins:
        if low <= start <= high:
            return index

    def _distance(start: float) -> float:
        return low - start if start < low else start - high

    index, start = min(joins, key=lambda item: (_distance(item[1]), item[0]))
    if _distance(start) <= fallback_sec:
        return index
    return None


def apply_signature_transition(
    segments: list[dict[str, Any]],
    profile: Optional[dict[str, Any]],
    clip_duration: float,
) -> Optional[dict[str, Any]]:
    """Stamp the profile's signature transition onto one join, in place.

    Decorative joins before the chosen one are downgraded to hard
    cuts (the signature must read as THE opening transition); joins
    after it keep the profile's normal grammar. Returns the stamp for
    the EDL's ``signature_transition`` block (without the rendered
    time, which only the caller's join plan knows), or None when the
    profile declares no signature or no join is suitable.
    """
    signature = signature_for_profile(profile)
    if signature is None or clip_duration <= 0:
        return None
    chosen = choose_signature_join(segments)
    if chosen is None:
        return None
    name, duration = signature
    suppressed: list[int] = []
    for index in range(1, chosen):
        segment = segments[index]
        if _is_decorative(segment):
            segment["transition_in"] = "hard_cut"
            segment["transition_duration"] = 0
            suppressed.append(index)
    segments[chosen]["transition_in"] = name
    segments[chosen]["transition_duration"] = duration
    return {
        "name": name,
        "duration_sec": duration,
        "segment_index": chosen,
        "at_sec": round(float(segments[chosen].get("start", 0) or 0), 3),
        "suppressed_segments": suppressed,
    }
