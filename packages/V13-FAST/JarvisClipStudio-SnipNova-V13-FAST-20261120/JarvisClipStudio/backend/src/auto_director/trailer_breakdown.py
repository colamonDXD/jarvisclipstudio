"""Trailer-breakdown selection template (Trend-Radar D17).

The format (HookMafia, crawled 15–16 Nov 2026 — practitioner
evidence only, NO view counts): 2–3 s of trailer play, then a
freeze frame, a fast punch-zoom, a red circle on a background detail
the viewer missed, and 2–3 s of face-cam reaction; repeated 4–6x in
a 30–60 s clip. Hook: "You missed this in the first 3 seconds…".
The clip ends on a cliffhanger question / Part 2 bait. The window
it rides on — the final Avengers: Doomsday trailer, expected
16–22 Nov 2026, most likely ~19 Nov — is a rumour (multiple insider
tracks converging on 16–19 Nov; Marvel has announced nothing as of
16 Nov), not a confirmed date. Nothing in this module claims an
audience outcome. What it owns is the editorial shape,
deterministically:

* DETAIL GROUNDING — the "detail" the red circle marks must be a
  real, detectable element in the frozen frame: a legible on-screen
  text line, a detected face (a background figure), or a bright
  region in a dark frame. The evidence comes from the frame's own
  measurements (face boxes, OCR text lines, bright-region boxes) —
  never from a claim about what "must" be in the shot. No evidence
  in the frame means no detail, and no detail means the template
  DECLINES for that beat rather than circling nothing.

* STRUCTURE — a beat is trailer play (2–3 s) + freeze (1.0–2.0 s,
  the picture held, the audio silent — a deliberate, recorded
  choice: the reaction beat that follows carries the voice) +
  reaction (2–3 s of face-cam). Beats are laid out in source order,
  trailer window followed by the next reaction window after it.
  The clip needs at least 2 beats; 4–6 is the format's target for a
  30–60 s clip (recorded in the plan, not a hard gate — a shorter
  clip with 2–3 honest beats is still the format).

* ELIGIBILITY — a moment is eligible only when it has trailer
  windows (each >= 2 s of play), reaction windows (each >= 2 s with
  a face on camera), AND a grounded detail spot at the freeze point
  of at least 2 beats. A source with no trailer segment, no
  face-cam reaction, or no detectable detail declines — to the
  profile's own default style, with the reason recorded — rather
  than faking a breakdown.

* RENDER SEAM — the plan translates to EDL segments through
  ``plan_to_edl_segments``: trailer and reaction segments are
  ordinary source windows; a freeze is a one-frame segment
  (``freeze: True``) the EDL renderer holds for ``freeze_duration``
  (see media.edl_render's freeze branch — a ramp-model sibling:
  effective duration, per-segment silence, caption shift all ride
  the same accounting). The punch-zoom is an EDL ``zoom_keyframes``
  punch-in at each freeze start with trigger ``detail_reveal``.
  The red circle is an ASS ``\\p1`` ellipse ring appended to the
  caption ASS file (see ``append_trailer_breakdown_overlay``),
  mirroring the list_fill overlay seam.

The hook title is the format's formula, not a claim about a
specific detail — but it is only USED when the moment is eligible.
The cliffhanger names the last beat's detail in the detail's own
words ("Did you catch the figure in the background?").

What this module does NOT do (integration remainder, recorded
honestly): it does not DETECT which windows of a source are
trailer footage and which are face-cam reaction — the caller
supplies ``trailer_windows`` / ``reaction_windows`` (the planner
integration must classify them, e.g. from face-track presence per
segment). It does not reframe-map the detail box: the circle is
positioned against the frame the evidence was measured on; a
reframed freeze frame shifts the box, and the reframe mapping is
a follow-up, not something this lane pretends to have solved.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional, Sequence

#: The format's hook formula — a fixed string, used only when the
#: moment is eligible. It names no specific detail, so it cannot
#: mislead about one; the detail claim lives in the circle.
HOOK_FORMULA = "You missed this in the first 3 seconds…"

#: Punch-in trigger value the template's zoom keyframes carry.
#: The gaming profile's own default trigger stays "action" — this
#: value names the template's detail-reveal punches only.
DETAIL_REVEAL_TRIGGER = "detail_reveal"

#: The selection template name, as declared in profiles.
TRAILER_BREAKDOWN_TEMPLATE = "trailer_breakdown"

#: D17 candidate assignment: niches whose profiles OFFER the
#: template (listed under ``selection_templates`` in the shipped
#: profiles). The template is an OPTION, never a default.
TRAILER_BREAKDOWN_PROFILES = ("gaming",)

#: Beat geometry (seconds). Trailer play and reaction are the
#: format's 2–3 s windows; the freeze is the held beat between
#: them. The freeze is silent by design (see the module docstring).
TRAILER_MIN_PLAY_SEC = 2.0
TRAILER_MAX_PLAY_SEC = 3.0
REACTION_MIN_SEC = 2.0
REACTION_MAX_SEC = 3.0
FREEZE_DURATION_SEC = 1.5
FREEZE_MIN_SEC = 1.0
FREEZE_MAX_SEC = 2.0

#: Minimum beats for a breakdown clip; the format's target for a
#: full 30–60 s clip (recorded, not gated).
MIN_BEATS = 2
TARGET_BEATS = (4, 6)

#: A detail must cover at least this fraction of the frame (a
#: 2%-wide sliver is not something a circle can honestly mark) and
#: at most this much (a box covering most of the frame is the
#: scene, not a detail).
DETAIL_MIN_AREA = 0.0004
DETAIL_MAX_AREA = 0.50

#: The freeze frame is one video frame: the segment's source bounds
#: are a single frame at the output frame rate.
FRAME_SEC = 1.0 / 30.0

#: Cliffhanger tail: the last seconds of the clip carry the
#: question. The "Part 2" bait rides with it.
CLIFFHANGER_SEC = 2.5

MAX_TITLE_WORDS = 10
MAX_TITLE_CHARS = 64
MAX_LABEL_CHARS = 24


# --- Detail spots ------------------------------------------------------------


@dataclass
class DetailSpot:
    """A real, detectable element in a frozen frame, worth circling."""

    kind: str  # "text" | "face" | "bright"
    box: tuple[float, float, float, float]  # normalized xyxy
    label: str  # words for the cliffhanger, from the evidence itself
    score: float


def _valid_box(box: object) -> Optional[tuple[float, float, float, float]]:
    """A normalized xyxy box, or None when it is not one."""
    try:
        values = [float(v) for v in (box or [])]
    except (TypeError, ValueError):
        return None
    if len(values) != 4:
        return None
    x0, y0, x1, y1 = values
    if not (0.0 <= x0 < x1 <= 1.0 and 0.0 <= y0 < y1 <= 1.0):
        return None
    area = (x1 - x0) * (y1 - y0)
    if area < DETAIL_MIN_AREA or area > DETAIL_MAX_AREA:
        return None
    return (x0, y0, x1, y1)


def _norm(text: str) -> str:
    return " ".join(str(text or "").split())


def find_detail_spots(frame_evidence: Optional[dict]) -> list[DetailSpot]:
    """The circled details a frozen frame honestly offers, best first.

    ``frame_evidence`` is the frame's own measurements — the same
    shapes the scene analysis already produces: ``faces`` (boxes
    with scores), ``text_lines`` (OCR text + boxes), and
    ``bright_regions`` (bright-component boxes with coverage).
    Priority is text, then face, then bright: a legible on-screen
    text is the classic circled detail; a background face is a
    figure worth circling; a bright region in a dark frame is the
    weakest claim and only counts when nothing better exists.
    Labels come from the evidence itself — the text, or a fixed
    phrase for a faceless/wordless region — never from a guess
    about what the shot "must" contain. An empty list means the
    frame offers no honest detail: the beat declines.
    """
    evidence = frame_evidence or {}
    spots: list[DetailSpot] = []

    for line in evidence.get("text_lines") or []:
        if not isinstance(line, dict):
            continue
        box = _valid_box(line.get("box"))
        text = _norm(line.get("text") or "")
        if box is None or len(text) < 2:
            continue
        spots.append(
            DetailSpot(
                kind="text",
                box=box,
                label=text[:MAX_LABEL_CHARS].strip(),
                score=0.9,
            )
        )
    if spots:
        return sorted(spots, key=lambda s: -s.score)

    for face in evidence.get("faces") or []:
        if not isinstance(face, dict):
            continue
        box = _valid_box(face.get("box"))
        if box is None:
            continue
        try:
            score = float(face.get("score") or 0.8)
        except (TypeError, ValueError):
            score = 0.8
        spots.append(
            DetailSpot(
                kind="face",
                box=box,
                label="figure in the background",
                score=min(0.85, max(0.5, score)),
            )
        )
    if spots:
        return sorted(spots, key=lambda s: -s.score)

    for region in evidence.get("bright_regions") or []:
        if not isinstance(region, dict):
            continue
        box = _valid_box(region.get("box"))
        if box is None:
            continue
        try:
            coverage = float(region.get("coverage") or 0.0)
        except (TypeError, ValueError):
            coverage = 0.0
        if coverage < 0.05:
            continue
        spots.append(
            DetailSpot(
                kind="bright",
                box=box,
                label="bright detail in the background",
                score=0.6,
            )
        )
    return sorted(spots, key=lambda s: -s.score)


# --- Beats and plan ----------------------------------------------------------


@dataclass
class TrailerBeat:
    """One trailer-play → freeze → reaction beat, source + clip times."""

    trailer_start: float
    trailer_end: float
    freeze_at: float  # source time of the frozen frame (trailer end)
    freeze_duration: float
    detail: DetailSpot
    reaction_start: float
    reaction_end: float
    #: Clip-relative layout, filled by the plan builder.
    clip_trailer_start: float = 0.0
    clip_freeze_start: float = 0.0
    clip_reaction_start: float = 0.0
    clip_end: float = 0.0

    @property
    def trailer_play_sec(self) -> float:
        return self.trailer_end - self.trailer_start

    @property
    def reaction_sec(self) -> float:
        return self.reaction_end - self.reaction_start

    @property
    def total_sec(self) -> float:
        return self.trailer_play_sec + self.freeze_duration + self.reaction_sec


@dataclass
class BreakdownPlan:
    """A trailer-breakdown cut: beats, hook, cliffhanger, duration."""

    hook_text: str
    beats: list[TrailerBeat]
    cliffhanger: str
    clip_duration: float  # rendered clip length (sum of beat times)
    moment_duration: float  # source moment the beats were cut from

    @property
    def beats_target(self) -> tuple[int, int]:
        return TARGET_BEATS


@dataclass
class Eligibility:
    """Whether a moment may be cut as a trailer breakdown."""

    eligible: bool
    reasons: list[str] = field(default_factory=list)
    plan: Optional[BreakdownPlan] = None


def _window(entry: object) -> Optional[tuple[float, float]]:
    if not isinstance(entry, dict):
        return None
    try:
        start, end = float(entry.get("start") or 0.0), float(entry.get("end") or 0.0)
    except (TypeError, ValueError):
        return None
    if end <= start:
        return None
    return (start, end)


def _spots_at(
    spots_by_freeze: Optional[dict], freeze_at: float
) -> list[DetailSpot]:
    """Spots recorded for a freeze point (within 50 ms — float keys)."""
    if not spots_by_freeze:
        return []
    for key, value in spots_by_freeze.items():
        try:
            at = float(key)
        except (TypeError, ValueError):
            continue
        if abs(at - freeze_at) <= 0.05 and isinstance(value, list):
            return [s for s in value if isinstance(s, DetailSpot)]
    return []


def build_cliffhanger(detail: DetailSpot) -> str:
    """The end question, naming the last beat's detail in its own words.

    The label is the evidence's own text (for a text spot) or the
    fixed phrase for a faceless region — the question never names a
    detail the frame did not offer.
    """
    label = _norm(detail.label)
    if detail.kind == "text":
        question = f"Did you catch {label} in the background?"
    else:
        question = f"Did you catch the {label}?"
    return f"{question} Part 2"


def trailer_breakdown_eligibility(
    trailer_windows: Sequence[dict],
    reaction_windows: Sequence[dict],
    detail_spots_by_freeze: Optional[dict],
    clip_duration: float,
) -> Eligibility:
    """Whether a moment may be cut as a trailer breakdown.

    Eligible only with at least two beats, each an honest chain:
    a trailer window of >= 2 s of play, followed in source order
    by a reaction window of >= 2 s with a face on camera, frozen
    at the trailer's end on a frame that offers a grounded detail
    spot. Every failure is a recorded reason, never a silent
    fallback — a moment rejected here stays eligible for every
    other style.
    """
    duration = float(clip_duration or 0.0)
    reasons: list[str] = []

    trailers: list[tuple[float, float]] = []
    for entry in trailer_windows or []:
        window = _window(entry)
        if window is None:
            continue
        if window[1] - window[0] < TRAILER_MIN_PLAY_SEC:
            reasons.append(
                f"trailer window {window[0]:.1f}–{window[1]:.1f}s is "
                f"shorter than the {TRAILER_MIN_PLAY_SEC:.0f}s of play a "
                "breakdown beat needs"
            )
            continue
        trailers.append(window)
    if not trailers:
        reasons.append("no trailer window of at least 2s of play")
        return Eligibility(eligible=False, reasons=reasons)

    reactions: list[tuple[float, float]] = []
    for entry in reaction_windows or []:
        window = _window(entry)
        if window is None:
            continue
        if not bool((entry or {}).get("face_present")):
            reasons.append(
                f"reaction window {window[0]:.1f}–{window[1]:.1f}s has "
                "no face on camera — a reaction beat needs the "
                "creator's face"
            )
            continue
        if window[1] - window[0] < REACTION_MIN_SEC:
            reasons.append(
                f"reaction window {window[0]:.1f}–{window[1]:.1f}s is "
                f"shorter than the {REACTION_MIN_SEC:.0f}s a reaction "
                "beat needs"
            )
            continue
        reactions.append(window)
    if not reactions:
        reasons.append("no reaction window of at least 2s with a face on camera")
        return Eligibility(eligible=False, reasons=reasons)

    beats: list[TrailerBeat] = []
    used_reactions: set[int] = set()
    for trailer_start, trailer_end in sorted(trailers):
        play = min(trailer_end - trailer_start, TRAILER_MAX_PLAY_SEC)
        play_start = trailer_end - play
        freeze_at = trailer_end
        spots = _spots_at(detail_spots_by_freeze, freeze_at)
        if not spots:
            reasons.append(
                f"freeze at {freeze_at:.1f}s offers no grounded detail "
                "spot — nothing in that frame can honestly be circled"
            )
            continue
        reaction = None
        reaction_index = None
        for index, (react_start, react_end) in enumerate(sorted(reactions)):
            if index in used_reactions:
                continue
            if react_start < trailer_end:
                continue
            reaction = (react_start, react_end)
            reaction_index = index
            break
        if reaction is None:
            reasons.append(
                f"no reaction window follows the trailer ending at "
                f"{trailer_end:.1f}s"
            )
            continue
        used_reactions.add(reaction_index)
        react_play = min(
            reaction[1] - reaction[0], REACTION_MAX_SEC
        )
        beats.append(
            TrailerBeat(
                trailer_start=play_start,
                trailer_end=trailer_end,
                freeze_at=freeze_at,
                freeze_duration=FREEZE_DURATION_SEC,
                detail=spots[0],
                reaction_start=reaction[0],
                reaction_end=reaction[0] + react_play,
            )
        )

    if len(beats) < MIN_BEATS:
        reasons.append(
            f"only {len(beats)} breakdown beat(s) can be built; the "
            f"format needs at least {MIN_BEATS}"
        )
        return Eligibility(eligible=False, reasons=reasons)

    total = sum(beat.total_sec for beat in beats)
    if duration > 0 and total > duration + 0.01:
        return Eligibility(
            eligible=False,
            reasons=[
                f"the {len(beats)} beats need {total:.1f}s but the "
                f"moment is only {duration:.1f}s long"
            ],
        )

    cursor = 0.0
    for beat in beats:
        beat.clip_trailer_start = cursor
        beat.clip_freeze_start = cursor + beat.trailer_play_sec
        beat.clip_reaction_start = beat.clip_freeze_start + beat.freeze_duration
        beat.clip_end = beat.clip_reaction_start + beat.reaction_sec
        cursor = beat.clip_end
    target = (
        f" (format target is {TARGET_BEATS[0]}–{TARGET_BEATS[1]} beats "
        "for a 30–60s clip)"
        if len(beats) < TARGET_BEATS[0]
        else ""
    )
    plan = BreakdownPlan(
        hook_text=HOOK_FORMULA,
        beats=beats,
        cliffhanger=build_cliffhanger(beats[-1].detail),
        clip_duration=total,
        moment_duration=duration,
    )
    return Eligibility(
        eligible=True,
        reasons=[
            f"{len(beats)} breakdown beat(s), {total:.1f}s of "
            f"{duration:.1f}s{target}"
        ],
        plan=plan,
    )


def build_breakdown_plan(
    trailer_windows: Sequence[dict],
    reaction_windows: Sequence[dict],
    detail_spots_by_freeze: Optional[dict],
    clip_duration: float,
) -> Optional[BreakdownPlan]:
    """The timed breakdown plan for a moment, or None (decline)."""
    verdict = trailer_breakdown_eligibility(
        trailer_windows, reaction_windows, detail_spots_by_freeze, clip_duration
    )
    return verdict.plan if verdict.eligible else None


def resolve_trailer_breakdown_style(
    requested_style: Optional[str],
    profile: Optional[dict],
    trailer_windows: Sequence[dict],
    reaction_windows: Sequence[dict],
    detail_spots_by_freeze: Optional[dict],
    clip_duration: float,
) -> tuple[str, Optional[str]]:
    """Resolve a requested title style for one moment.

    Returns ``(style_to_use, decline_reason)``. Any style other than
    ``trailer_breakdown`` passes through untouched — a moment
    rejected for this template stays eligible for the rest.
    ``trailer_breakdown`` itself is granted only when the profile
    offers it AND the moment is eligible; otherwise the profile's
    own default style is returned with the reason it declined, so
    the fallback is a decision on record, never a silent switch.
    """
    requested = str(requested_style or "")
    if requested != "trailer_breakdown":
        return requested, None
    from .hook_title_styles import trailer_breakdown_available

    hook = (profile or {}).get("hook") or {}
    default = str(hook.get("title_style") or "statement")
    if default == "trailer_breakdown":
        default = "statement"
    if not trailer_breakdown_available(profile):
        return default, "trailer_breakdown is not offered by this profile"
    verdict = trailer_breakdown_eligibility(
        trailer_windows, reaction_windows, detail_spots_by_freeze, clip_duration
    )
    if not verdict.eligible:
        return default, "; ".join(verdict.reasons)
    return "trailer_breakdown", None


def trailer_breakdown_template_available(profile: Optional[dict]) -> bool:
    """Whether a profile declares the trailer_breakdown template (D17).

    The shipped profiles record their selection templates under the
    top-level ``selection_templates`` list; that list governs when
    present. A profile without the list falls back to the recorded
    D17 assignment by niche name. A profile whose list exists but
    omits the template does NOT offer it.
    """
    if not profile:
        return False
    templates = profile.get("selection_templates")
    if isinstance(templates, (list, tuple)):
        return TRAILER_BREAKDOWN_TEMPLATE in templates
    return str(profile.get("niche") or "") in TRAILER_BREAKDOWN_PROFILES


# --- EDL translation ---------------------------------------------------------


def _prestage_time(
    source_time: float, source_ranges: Sequence[Sequence[float]]
) -> Optional[float]:
    """Map a source-timeline time onto the prestage (compacted) timeline.

    Ranges tile the prestage in order (see
    media.ffmpeg.output_offsets_for_ranges — crossfade junctions
    pull later ranges earlier). A time outside every range maps to
    None: the plan cannot place a freeze there.
    """
    try:
        from ..media.ffmpeg import (
            normalize_source_ranges,
            output_offsets_for_ranges,
        )

        ranges = normalize_source_ranges(
            [(float(a), float(b)) for a, b in source_ranges]
        )
        offsets = output_offsets_for_ranges(ranges)
    except Exception:
        return None
    for (start, end), offset in zip(ranges, offsets):
        if start <= source_time <= end:
            return offset + (source_time - start)
    return None


def plan_to_edl_segments(
    plan: BreakdownPlan,
    source_ranges: Sequence[Sequence[float]],
    punch_scale: float = 1.2,
    punch_duration: float = 0.5,
) -> dict:
    """The EDL pieces for a breakdown plan: segments + zoom keyframes.

    Trailer and reaction beats are ordinary source windows. A freeze
    is a one-frame segment (``freeze: True``) the renderer holds
    for ``freeze_duration`` — its source bounds are the single
    frame at the trailer's end, its effective duration is the hold.
    The punch-zoom is an EDL ``zoom_keyframes`` punch-in at each
    freeze's clip start, trigger ``detail_reveal``. ``source_ranges``
    is the prestage the renderer concatenates; with a single range
    covering the beats the prestage IS the source timeline and the
    mapping is the identity.
    """
    ranges = [(float(a), float(b)) for a, b in source_ranges]
    segments: list[dict] = []
    zooms: list[dict] = []
    for beat in plan.beats:
        # Every bound is mapped source -> prestage: segments tile
        # the prestage timeline, not the source timeline. With a
        # single range covering the beats the mapping is the
        # identity; with several ranges it is the compacted
        # offsets (see _prestage_time).
        mapped: dict[str, float] = {}
        for key, source_time in (
            ("trailer_start", beat.trailer_start),
            ("trailer_end", beat.trailer_end),
            ("freeze_at", beat.freeze_at),
            ("reaction_start", beat.reaction_start),
            ("reaction_end", beat.reaction_end),
        ):
            prestage = _prestage_time(source_time, ranges)
            if prestage is None:
                raise ValueError(
                    f"breakdown time {source_time:.2f}s ({key}) falls "
                    "outside the EDL source ranges — the plan cannot "
                    "place it"
                )
            mapped[key] = prestage
        segments.append(
            {
                "start": mapped["trailer_start"],
                "end": mapped["trailer_end"],
                "transition_in": "cut",
            }
        )
        segments.append(
            {
                "start": mapped["freeze_at"],
                "end": mapped["freeze_at"] + FRAME_SEC,
                "freeze": True,
                "freeze_at": mapped["freeze_at"],
                "freeze_duration": beat.freeze_duration,
                "transition_in": "cut",
            }
        )
        segments.append(
            {
                "start": mapped["reaction_start"],
                "end": mapped["reaction_end"],
                "transition_in": "cut",
            }
        )
        zooms.append(
            {
                "type": "punch_in",
                "time": beat.clip_freeze_start,
                "scale": float(punch_scale),
                "duration": float(punch_duration),
                "trigger": DETAIL_REVEAL_TRIGGER,
            }
        )
    return {
        "segments": segments,
        "zoom_keyframes": zooms,
        "source_ranges": [list(r) for r in ranges],
        "duration": plan.clip_duration,
        "breakdown": {
            "template": TRAILER_BREAKDOWN_TEMPLATE,
            "beats": len(plan.beats),
            "hook": plan.hook_text,
            "cliffhanger": plan.cliffhanger,
            "freeze_audio": "silent",
        },
    }


# --- Render: the red-circle ASS overlay --------------------------------------

#: The circle is drawn in ASS vector mode (\\p1): a ring, not a
#: filled disc — fill fully transparent, red outline, 4 px stroke.
#: The ring sits on the detail box with a small margin so it frames
#: the detail instead of sitting on top of it. The cliffhanger
#: rides the last seconds of the clip, centred, above the caption
#: band.
_CIRCLE_MARGIN_FRAC = 0.03
_CIRCLE_BORDER_PX = 4
_CLIFFHANGER_Y_FRAC = 0.78


def _ass_time(seconds: float) -> str:
    total = max(0.0, float(seconds))
    hours = int(total // 3600)
    minutes = int((total % 3600) // 60)
    secs = total % 60
    return f"{hours}:{minutes:02d}:{secs:05.2f}"


def _escape_ass_text(text: str) -> str:
    return (
        str(text or "")
        .replace("\\", "\\\\")
        .replace("{", "\\{")
        .replace("}", "\\}")
        .replace("\n", "\\N")
    )


def _ellipse_path(cx: float, cy: float, rx: float, ry: float) -> str:
    """An ASS \\p1 ellipse path (four bezier curves, 0.5523 constant).

    The coordinates are PATH coordinates, not frame coordinates: this
    pipeline's libass (verified against ffmpeg 8.1.2 / libass 0.17.1,
    the version the render tests run under) renders \\p1 drawings
    TRANSPOSED — a path point (x, y) lands at frame (y, x) — and
    ``l`` (line) and ``s`` (spline) commands render nothing at all,
    only closed ``b`` curves draw. The caller therefore builds the
    path in transposed, origin-relative coordinates and pins it with
    ``\\an7\\pos`` (see ``trailer_breakdown_ass_events``); the render
    test pins the landed ring against the intended box, so a libass
    that stops transposing fails loudly instead of silently
    misplacing it.

    KNOWN LIMITATION, measured not assumed: the landed outline is
    a closed red curve around the detail box, correctly placed and
    sized (within ~4 px, the border width), but it is NOT a
    geometric ellipse — this libass shears the curve into a
    leaf/eye shape (one side bowed, the opposite edge nearly
    straight). The ring still reads as a highlight around the
    detail; a geometrically exact ellipse wants a PNG overlay
    instead of the ASS seam, which is a follow-up, not this lane.
    """
    k = 0.5522847498

    def p(value: float) -> str:
        return str(int(round(value)))

    return (
        f"m {p(cx - rx)},{p(cy)} "
        f"b {p(cx - rx)},{p(cy - ry * k)} {p(cx - rx * k)},{p(cy - ry)} {p(cx)},{p(cy - ry)} "
        f"b {p(cx + rx * k)},{p(cy - ry)} {p(cx + rx)},{p(cy - ry * k)} {p(cx + rx)},{p(cy)} "
        f"b {p(cx + rx)},{p(cy + ry * k)} {p(cx + rx * k)},{p(cy + ry)} {p(cx)},{p(cy + ry)} "
        f"b {p(cx - rx * k)},{p(cy + ry)} {p(cx - rx)},{p(cy + ry * k)} {p(cx - rx)},{p(cy)}"
    )


def trailer_breakdown_ass_events(
    plan: BreakdownPlan, video_width: int, video_height: int, font_name: str = "Arial"
) -> tuple[str, list[str]]:
    """The ASS style line + Dialogue lines for a breakdown overlay.

    One red ring per beat, drawn around that beat's detail box for
    exactly the freeze window (clip-relative times); the
    cliffhanger question rides the last seconds. Positions are
    computed from the frame size so the layout scales. The caller
    appends these to the caption ASS file the pipeline already
    builds (see ``append_trailer_breakdown_overlay``).
    """
    width = int(video_width or 540)
    height = int(video_height or 960)
    font_px = max(28, round(38 * width / 540))
    margin_x = round(_CIRCLE_MARGIN_FRAC * width)
    margin_y = round(_CIRCLE_MARGIN_FRAC * height)
    cliff_y = round(_CLIFFHANGER_Y_FRAC * height)

    style_line = (
        f"Style: TrailerBreak,{font_name},{font_px},&H00FFFFFF,&H000000FF,"
        f"&H00000000,&H00000000,1,0,0,0,100,100,0,0,1,2,0,8,60,60,60,1"
    )
    events: list[str] = []
    for beat in plan.beats:
        x0, y0, x1, y1 = beat.detail.box
        px0 = max(0, int(x0 * width) - margin_x)
        py0 = max(0, int(y0 * height) - margin_y)
        px1 = min(width, int(x1 * width) + margin_x)
        py1 = min(height, int(y1 * height) + margin_y)
        cx = (px0 + px1) / 2.0
        cy = (py0 + py1) / 2.0
        rx = max(4.0, (px1 - px0) / 2.0)
        ry = max(4.0, (py1 - py0) / 2.0)
        # The ring is placed with \\an7\\pos at the box's top-left and
        # a path built TRANSPOSED and origin-relative: this pipeline's
        # libass renders a path point (x, y) at frame (y, x), so a
        # path ellipse centred at (cy - py0, cx - px0) with radii
        # (ry, rx) lands centred at (cx, cy) with radii (rx, ry) —
        # exactly the detail box. \\an7 makes \\pos refer to the
        # top-left corner, so the ring's top-left lands at (px0, py0)
        # (the \\bord outline extends ~4 px past it). Calibrated
        # empirically (bezier squares at known positions, measured
        # pixel positions of the rendered outline); see _ellipse_path.
        path = _ellipse_path(cy - py0, cx - px0, ry, rx)
        events.append(
            "Dialogue: 0,"
            f"{_ass_time(beat.clip_freeze_start)},"
            f"{_ass_time(beat.clip_freeze_start + beat.freeze_duration)},"
            "TrailerBreak,,0,0,0,,"
            "{\\an7\\pos("
            f"{px0},{py0}"
            ")\\p1\\1a&HFF&\\3c&H000000FF&\\bord"
            f"{_CIRCLE_BORDER_PX}}}"
            f"{path}"
        )
    cliff_start = max(0.0, plan.clip_duration - CLIFFHANGER_SEC)
    events.append(
        "Dialogue: 0,"
        f"{_ass_time(cliff_start)},{_ass_time(plan.clip_duration)},"
        "TrailerBreak,,0,0,0,,"
        f"{{\\pos({width // 2},{cliff_y})}}"
        f"{_escape_ass_text(plan.cliffhanger)}"
    )
    return style_line, events


def append_trailer_breakdown_overlay(
    ass_path: str | Path,
    plan: BreakdownPlan,
    video_width: int,
    video_height: int,
) -> bool:
    """Append a trailer-breakdown overlay to an existing caption ASS file.

    Inserts the ``TrailerBreak`` style into the [V4+ Styles] section
    (font taken from the file's own Hook style, else its Default
    style) and appends the overlay Dialogue lines to [Events].
    Returns True when appended; False when the file already carries
    a TrailerBreak style (never append twice) or has no [Events]
    section to append to.
    """
    path = Path(ass_path)
    text = path.read_text(encoding="utf-8")
    if "TrailerBreak" in text:
        return False
    if "[Events]" not in text:
        return False
    font_name = "Arial"
    for prefix in ("Style: Hook,", "Style: Default,"):
        for line in text.splitlines():
            if line.startswith(prefix):
                parts = line.split(",")
                if len(parts) > 1 and parts[1].strip():
                    font_name = parts[1].strip()
                break
        if font_name != "Arial":
            break
    style_line, events = trailer_breakdown_ass_events(
        plan, video_width, video_height, font_name
    )
    lines = text.splitlines()
    out: list[str] = []
    inserted = False
    for line in lines:
        out.append(line)
        if not inserted and line.startswith("Format: Name, Fontname,"):
            out.append(style_line)
            inserted = True
    if not inserted:
        return False
    out.extend(events)
    path.write_text("\n".join(out) + "\n", encoding="utf-8")
    return True
