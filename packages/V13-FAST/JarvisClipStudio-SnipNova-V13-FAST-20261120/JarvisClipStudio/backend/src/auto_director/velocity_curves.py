"""Velocity curve presets (Trend-Radar candidate D14): CapCut Curve names.

D14 asked whether the renderer's speed ramps can honestly carry the
CapCut Curve preset names creators search for — Montage, Bullet, Hero,
Jump Cut, Flash In, Flash Out. The audit answer is in two parts:

1. The EXISTING ramp machinery (beat_grid.assign_speed_ramps) is a
   two-value, drop-anchored pattern (slow build ~0.72x into the drop,
   rush ~1.35x after it). It matches NONE of the six named shapes
   honestly — it is not renamed.
2. The six shapes themselves are small additive curve definitions,
   implemented here and A/B-verified (see the workstream report's
   "D14 A/B" section): each curve is a set of control points over the
   normalised clip position, realised through the renderer's native
   currency — per-segment constant speeds (setpts + atempo, exactly
   what beat-grid ramps already use). A range is split into
   ``CURVE_STEPS`` sub-segments; each takes the curve's speed at its
   midpoint ("smooth" curves interpolate cosinely between control
   points; "step" curves hold the previous point's speed).

Definitions (pre-registered from the radar's cited guides):
- Flash In:  fast at the start, settling to normal (1.0x) and staying.
- Flash Out: normal at the start, accelerating to fast at the end.
- Hero:      normal-speed edges, a sustained slow-motion centrepiece.
- Bullet:    "fast in, ultra-slow at the peak, fast out"
             (bestforandroid's description of the CapCut preset).
- Montage:   alternating fast/slow pattern across the whole clip
             (the guides' beat-alternation recipe).
- Jump Cut:  a few abrupt constant-speed steps, no easing.

HONEST RANGE NOTE: CapCut curves span 0.1x-10x. This renderer clamps
ramps to [0.4, 2.0] (beat_grid's bounds — beyond that the atempo audio
chain and the frame-sampled retime stop being honest), so these curves
live inside [0.5, 2.0]. The SHAPE is the name's promise; the range is
documented as narrower, here and in the report.

Restraint: a curve is applied only where a caller explicitly asks for
it by name — the beat-grid seam (``beat_grid.consume_beat_grid`` reads
an optional ``beat_grid.velocity_curve`` profile key) or a future
studio/plan seam; nothing here changes any existing profile's
behaviour. Every stamped segment carries
``speed_reason = "velocity curve <label>"`` so the beat-grid restraint
validator's law (no reason, no ramp) is satisfied by construction.
"""

from __future__ import annotations

import math
from typing import Any, Optional

CURVE_STEPS = 20
SPEED_MIN = 0.5
SPEED_MAX = 2.0

# Control points: (normalised position 0..1, speed). "smooth" curves
# cosine-interpolate between points; "step" curves hold.
CURVES: dict[str, dict[str, Any]] = {
    "flash_in": {
        "label": "Flash In",
        "mode": "smooth",
        "points": [(0.0, 2.0), (0.35, 1.0), (1.0, 1.0)],
    },
    "flash_out": {
        "label": "Flash Out",
        "mode": "smooth",
        "points": [(0.0, 1.0), (0.65, 1.0), (1.0, 2.0)],
    },
    "hero": {
        "label": "Hero",
        "mode": "smooth",
        "points": [(0.0, 1.0), (0.3, 0.55), (0.7, 0.55), (1.0, 1.0)],
    },
    "bullet": {
        "label": "Bullet",
        "mode": "smooth",
        "points": [(0.0, 1.8), (0.35, 0.5), (0.65, 0.5), (1.0, 1.8)],
    },
    "montage": {
        "label": "Montage",
        "mode": "smooth",
        "points": [
            (0.0, 1.6), (0.2, 0.7), (0.4, 1.6), (0.6, 0.7),
            (0.8, 1.6), (1.0, 0.9),
        ],
    },
    "jump_cut": {
        "label": "Jump Cut",
        "mode": "step",
        "points": [(0.0, 1.0), (0.3, 2.0), (0.55, 1.0), (0.8, 1.6)],
    },
}

CURVE_NAMES = tuple(CURVES.keys())


def curve_speed_at(name: str, position: float) -> float:
    """The curve's speed at a normalised position (0..1), clamped."""
    curve = CURVES[name]
    points = curve["points"]
    position = max(0.0, min(1.0, float(position)))
    if curve["mode"] == "step":
        value = points[0][1]
        for pos, speed in points:
            if position >= pos:
                value = speed
        return max(SPEED_MIN, min(SPEED_MAX, float(value)))
    if position <= points[0][0]:
        return max(SPEED_MIN, min(SPEED_MAX, float(points[0][1])))
    for (p0, s0), (p1, s1) in zip(points, points[1:]):
        if p0 <= position <= p1:
            if p1 == p0:
                return max(SPEED_MIN, min(SPEED_MAX, float(s1)))
            frac = (position - p0) / (p1 - p0)
            eased = 0.5 - 0.5 * math.cos(math.pi * frac)
            return max(SPEED_MIN, min(SPEED_MAX, s0 + (s1 - s0) * eased))
    return max(SPEED_MIN, min(SPEED_MAX, float(points[-1][1])))


def apply_velocity_curve(
    segments: list[dict],
    name: str,
    *,
    steps: int = CURVE_STEPS,
) -> list[dict]:
    """Realise a named curve over a segment list's total span.

    The input segments tile [start, end]; the output re-tiles the same
    span into ``steps`` equal sub-segments, each stamped with the
    curve's speed at its midpoint and its reason. Other segment fields
    are inherited from the input segment each sub-segment's start falls
    in (the beat-grid re-tiling convention). Returns the input unchanged
    for an unknown name or a degenerate span — a curve must never cost
    the clip.
    """
    if name not in CURVES or not segments or steps < 2:
        return segments
    span_start = float(segments[0].get("start", 0))
    span_end = float(segments[-1].get("end", 0))
    total = span_end - span_start
    if total <= 0:
        return segments
    out: list[dict] = []
    step = total / steps
    for i in range(steps):
        start = span_start + i * step
        end = span_start + (i + 1) * step
        template = segments[-1]
        for seg in segments:
            if float(seg.get("start", 0)) <= start < float(seg.get("end", 0)):
                template = seg
                break
        midpoint = (i + 0.5) / steps
        entry = dict(template)
        entry.update({
            "index": len(out),
            "start": round(start, 3),
            "end": round(end, 3),
            "duration": round(end - start, 3),
            "speed": round(curve_speed_at(name, midpoint), 3),
            "speed_reason": f"velocity curve {CURVES[name]['label']}",
            "speed_at": round(start, 3),
        })
        if entry["speed"] == 1.0:
            # A 1.0x step is not a ramp; keep the segment plain so the
            # renderer's ramp detection (and its audio retime) only
            # engages where speed actually changes.
            entry.pop("speed", None)
            entry.pop("speed_reason", None)
            entry.pop("speed_at", None)
        out.append(entry)
    return out


def stamp_velocity_curve(
    segments: list[dict],
    name: str,
) -> list[dict]:
    """Stamp a named curve's speeds onto EXISTING segments (beat-grid seam).

    Unlike :func:`apply_velocity_curve` (which re-tiles the span into
    ``CURVE_STEPS`` sub-segments), this keeps the caller's segmentation
    — beat-snapped boundaries and drop-quantised lengths survive — and
    stamps each segment with the curve's speed at its midpoint,
    normalised over the tiled span. Any pre-existing speed stamps are
    replaced: a profile that selects a curve chooses the curve as its
    speed treatment, not build/rush on top of it. Segments whose curve
    speed is exactly 1.0 stay unstamped, as in ``apply_velocity_curve``.
    Returns a new list; the input is untouched. Unknown name or a
    degenerate span returns the input unchanged — a curve must never
    cost the clip.
    """
    if name not in CURVES or not segments:
        return segments
    span_start = float(segments[0].get("start", 0))
    span_end = float(segments[-1].get("end", 0))
    total = span_end - span_start
    if total <= 0:
        return segments
    out: list[dict] = []
    for seg in segments:
        entry = dict(seg)
        for key in ("speed", "speed_reason", "speed_at"):
            entry.pop(key, None)
        midpoint = (
            (float(seg.get("start", 0)) + float(seg.get("end", 0))) / 2.0
            - span_start
        ) / total
        speed = curve_speed_at(name, midpoint)
        if speed != 1.0:
            entry["speed"] = round(speed, 3)
            entry["speed_reason"] = f"velocity curve {CURVES[name]['label']}"
            entry["speed_at"] = round(float(seg.get("start", 0)), 3)
        out.append(entry)
    return out


def realised_profile(
    segments: list[dict], samples: int = 10
) -> list[float]:
    """Speed at ``samples`` evenly spaced normalised positions.

    Reads the stamped segment speeds (unstamped = 1.0) — the profile
    the renderer will actually realise, from the same segment list the
    EDL carries.
    """
    if not segments:
        return []
    span_start = float(segments[0].get("start", 0))
    span_end = float(segments[-1].get("end", 0))
    total = span_end - span_start
    if total <= 0:
        return []
    profile: list[float] = []
    for i in range(samples):
        at = span_start + (i + 0.5) / samples * total
        value: Optional[float] = None
        for seg in segments:
            if float(seg.get("start", 0)) <= at < float(seg.get("end", 0)):
                speed = seg.get("speed")
                value = float(speed) if isinstance(speed, (int, float)) else 1.0
                break
        profile.append(round(value if value is not None else 1.0, 3))
    return profile


def predicted_duration(segments: list[dict]) -> float:
    """The rendered duration a segment list's speeds produce."""
    total = 0.0
    for seg in segments:
        length = float(seg.get("end", 0)) - float(seg.get("start", 0))
        speed = seg.get("speed")
        if isinstance(speed, (int, float)) and speed > 0 and speed != 1.0:
            total += length / float(speed)
        else:
            total += length
    return round(total, 3)
