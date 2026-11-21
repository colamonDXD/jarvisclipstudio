"""Editing recommendations for reaction clips, emitted as DATA for the renderer.

This lane decides WHAT should happen; the render lane (Lane B) decides HOW.
Every recommendation carries a confidence and the evidence it rests on, and the
whole block is advisory: a renderer may ignore any item.

Document contract (``moment["edit_recommendations"]``), times are clip-relative
seconds:

{
  "schema_version": 1,
  "framing": {"mode": "auto|fit|crop", "layout": "stacked_reactor|pip_preserved|full",
              "facecam_roi": [x, y, w, h] | null, "reason": str, "confidence": 0-1},
  "zooms":   [{"start": s, "end": s, "target": "facecam|gameplay", "from_scale": 1.0,
               "to_scale": 1.12, "ease": "out_cubic", "reason": str}],
  "pacing":  {"trim_dead_time": [{"start": s, "end": s, "reason": str}],
              "speed": [{"start": s, "end": s, "rate": 0.6, "reason": str}],
              "open_on": "reaction|trigger", "reason": str},
  "sfx_cues": [{"at": s, "kind": "impact|riser", "requires_library": true, "reason": str}]
}
"""

from __future__ import annotations

from typing import Any, Optional, Sequence

SCHEMA_VERSION = 1
_MAX_ZOOM = 1.15


def _clip_time(value: Optional[float], window_start: float) -> Optional[float]:
    return None if value is None else round(max(0.0, float(value) - window_start), 2)


def recommend_framing(
    facecam_roi: Optional[tuple[float, float, float, float]], mtype: str, kind: str
) -> dict[str, Any]:
    if facecam_roi is None:
        return {"mode": "fit", "layout": "full", "facecam_roi": None, "confidence": 0.5,
                "reason": "no facecam found: keep the full game frame so HUD stays readable"}
    reaction_led = mtype in ("reaction_to_event", "commentary_reaction")
    # Layout names follow the Lane A amendment (stacked reactor / PiP-preserved
    # / full): stacked_reactor = reactor pane on top, gameplay pane below.
    layout = "stacked_reactor" if reaction_led else "pip_preserved"
    return {
        "mode": "fit", "layout": layout, "facecam_roi": [round(v, 3) for v in facecam_roi],
        "confidence": 0.8 if reaction_led else 0.6,
        "reason": ("the reaction is the point of the clip: give the facecam its own panel "
                   "above the game" if reaction_led else
                   "game-led clip: keep the facecam as a small inset"),
    }


def recommend_zooms(
    moment: dict[str, Any], window_start: float, window_len: float,
    facecam_roi: Optional[tuple[float, float, float, float]],
) -> list[dict[str, Any]]:
    ev = moment.get("evidence") or {}
    zooms: list[dict[str, Any]] = []
    r0, r1 = ev.get("reaction_start_sec"), ev.get("reaction_end_sec")
    if r0 is None or r1 is None:
        return zooms
    a = _clip_time(r0, window_start) or 0.0
    b = min(window_len, _clip_time(r1, window_start) or 0.0)
    kind = ev.get("reaction_kind")
    if b - a >= 0.8:
        strong = kind in ("shout", "laughter")
        zooms.append({
            "start": round(a - 0.15 if a >= 0.15 else a, 2), "end": round(min(window_len, b + 0.4), 2),
            "target": "facecam" if facecam_roi else "gameplay",
            "from_scale": 1.0, "to_scale": 1.12 if strong else 1.07, "ease": "out_cubic",
            "reason": "punch in as the reaction starts, hold through it, release after the payoff"})
    game = ev.get("game_event")
    if game:
        g = _clip_time(game["start_sec"], window_start)
        if g is not None and g + 0.6 < (a or window_len):
            zooms.append({
                "start": g, "end": round(min(window_len, g + 0.9), 2), "target": "gameplay",
                "from_scale": 1.0, "to_scale": 1.06, "ease": "out_cubic",
                "reason": "small push on the on-screen trigger so viewers see what the streamer reacts to"})
    for z in zooms:
        z["to_scale"] = min(z["to_scale"], _MAX_ZOOM)
    return zooms


def recommend_pacing(
    moment: dict[str, Any], window_start: float, window_len: float,
    strength: Optional[Sequence[float]] = None, words: Optional[Sequence[dict[str, Any]]] = None,
) -> dict[str, Any]:
    ev = moment.get("evidence") or {}
    game = ev.get("game_event")
    r0 = ev.get("reaction_start_sec")
    pacing: dict[str, Any] = {"trim_dead_time": [], "speed": [], "open_on": "trigger",
                              "reason": "show the trigger first so the reaction has a cause"}
    if not game:
        pacing["open_on"] = "reaction"
        pacing["reason"] = "no visible trigger: open directly on the reaction"
    elif r0 is not None and r0 - game["end_sec"] > 4.0:
        pacing["open_on"] = "reaction"
        pacing["reason"] = "long gap between trigger and reaction: cold-open on the reaction"
    # Dead time: stretches of >= 2.5 s with no speech and no reaction/event.
    if strength is not None and window_len > 6:
        quiet_start: Optional[int] = None
        lo, hi = int(window_start), int(window_start + window_len)
        for t in range(lo, hi + 1):
            quiet = (t >= len(strength) or strength[t] <= 0)
            if words:
                quiet = quiet and not any(float(w["start"]) <= t + 1 and float(w["end"]) >= t for w in words)
            if game and game["start_sec"] - 1 <= t <= game["end_sec"] + 1:
                quiet = False
            if quiet and quiet_start is None:
                quiet_start = t
            if (not quiet or t == hi) and quiet_start is not None:
                if t - quiet_start >= 3:
                    pacing["trim_dead_time"].append({
                        "start": round(quiet_start - window_start + 0.4, 2),
                        "end": round(t - window_start - 0.4, 2),
                        "reason": "silence with nothing happening on screen"})
                quiet_start = None
    if game and ev.get("game_event", {}).get("type") in ("screen_flash_banner", "erratic_motion"):
        g = _clip_time(game["start_sec"], window_start)
        if g is not None and r0 is not None and r0 - game["start_sec"] >= 0.8:
            pacing["speed"].append({
                "start": g, "end": round(g + 0.8, 2), "rate": 0.6,
                "reason": "brief slow-down on the trigger before the reaction lands"})
    return pacing


def recommend_sfx(moment: dict[str, Any], window_start: float) -> list[dict[str, Any]]:
    """Cues only; the app's SFX library ships empty, so every cue says so."""
    ev = moment.get("evidence") or {}
    game = ev.get("game_event")
    if not game:
        return []
    return [{"at": _clip_time(game["start_sec"], window_start), "kind": "impact",
             "requires_library": True, "reason": "accent the on-screen trigger"}]


def recommend_edits(
    moment: dict[str, Any], window_start: float, window_end: float, *,
    facecam_roi: Optional[tuple[float, float, float, float]] = None,
    strength: Optional[Sequence[float]] = None,
    words: Optional[Sequence[dict[str, Any]]] = None,
) -> dict[str, Any]:
    ev = moment.get("evidence") or {}
    mtype = ev.get("moment_type", "")
    window_len = max(0.0, window_end - window_start)
    return {
        "schema_version": SCHEMA_VERSION,
        "framing": recommend_framing(facecam_roi, mtype, ev.get("reaction_kind", "")),
        "zooms": recommend_zooms(moment, window_start, window_len, facecam_roi),
        "pacing": recommend_pacing(moment, window_start, window_len, strength, words),
        "sfx_cues": recommend_sfx(moment, window_start),
    }


def attach_recommendations(
    moments: list[dict[str, Any]], *,
    facecam_roi: Optional[tuple[float, float, float, float]] = None,
    strength: Optional[Sequence[float]] = None,
    words: Optional[Sequence[dict[str, Any]]] = None,
) -> list[dict[str, Any]]:
    def sec(label: str) -> float:
        m, x = label.split(":")
        return int(m) * 60 + int(x)

    for m in moments:
        if "moment_type" not in (m.get("evidence") or {}):
            continue
        m["edit_recommendations"] = recommend_edits(
            m, sec(m["start_time"]), sec(m["end_time"]),
            facecam_roi=facecam_roi, strength=strength, words=words)
    return moments
