"""GA2 for reaction clips: grounded hooks, payoff-aware boundaries, caption sync.

Everything here is derived from MEASURED evidence on a moment (the
``evidence`` block written by ``reaction_moments``) and, when available, the
transcript's word timings. Nothing names a game, a weapon or an event the
pictures and sound did not show; a hook never claims more than the evidence
supports ("a sudden on-screen blast", not "a car explodes").

Pure functions, no I/O: the pipeline and the tests call them directly.
"""

from __future__ import annotations

import re
import zlib
from typing import Any, Optional, Sequence

_TRIGGER_PHRASE = {
    "screen_flash_banner": "a sudden on-screen blast",
    "hud_event_streak": "a burst of on-screen action",
    "erratic_motion": "everything going wildly wrong",
    "sustained_escalation": "an intense on-screen sequence",
    "visual_change": "something unexpected on screen",
}
_EVENT_HOOKS = {
    "laughter": ("Streamer loses it laughing at {t}", "Streamer can't stop laughing at {t}",
                 "Streamer cracks up over {t}"),
    "shout": ("Streamer screams at {t}", "Streamer erupts after {t}", "Streamer panics at {t}"),
    "vocal_burst": ("Streamer reacts loudly to {t}", "Streamer can't believe {t}",
                    "Streamer is stunned by {t}"),
}
_COMMENTARY_HOOKS = {
    "laughter": ("Streamer bursts out laughing mid-stream", "Streamer completely loses it laughing",
                 "Streamer cracks up out of nowhere"),
    "shout": ("Streamer suddenly erupts mid-commentary", "Streamer's wild outburst out of nowhere",
              "Streamer goes off unexpectedly"),
    "vocal_burst": ("Streamer's unexpected outburst", "Streamer blurts out something unexpected",
                    "Streamer reacts out of nowhere"),
}
_SILENT_HOOKS = ("Streamer's silent reaction says it all", "Streamer goes quiet at the worst moment")

_PROFANITY = re.compile(r"\b(fuck\w*|shit\w*|bitch\w*|asshole|damn|crap|dick)\b", re.I)


def mask_profanity(text: str, mode: str = "mask") -> str:
    """User-configurable profanity handling: keep | mask (full ***) | drop."""
    if mode == "keep":
        return text
    if mode == "drop":
        return re.sub(r"\s{2,}", " ", _PROFANITY.sub("", text)).strip()
    return _PROFANITY.sub("***", text)


def _pick(options: Sequence[str], seed: str) -> str:
    return options[zlib.crc32(seed.encode()) % len(options)]


def reaction_hook_title(moment: dict[str, Any]) -> Optional[str]:
    """A 3–9 word third-person hook grounded in the moment's evidence, or None
    when the moment is a plain gameplay pick (those keep their own hooks)."""
    ev = moment.get("evidence") or {}
    mtype = ev.get("moment_type")
    if mtype is None:
        return None
    kind = ev.get("reaction_kind") or "vocal_burst"
    seed = f"{moment.get('start_time')}{mtype}{kind}"
    if mtype == "reaction_to_event":
        game = ev.get("game_event") or {}
        trigger = _TRIGGER_PHRASE.get(game.get("type"), _TRIGGER_PHRASE["visual_change"])
        title = _pick(_EVENT_HOOKS.get(kind, _EVENT_HOOKS["vocal_burst"]), seed).format(t=trigger)
    elif mtype == "commentary_reaction":
        title = _pick(_COMMENTARY_HOOKS.get(kind, _COMMENTARY_HOOKS["vocal_burst"]), seed)
    elif mtype == "silent_facecam":
        title = _pick(_SILENT_HOOKS, seed)
    else:
        return None
    return title if 3 <= len(title.split()) <= 9 else " ".join(title.split()[:9])


def reaction_quote(words: Sequence[dict[str, Any]], start: float, end: float,
                   profanity: str = "mask", max_words: int = 6) -> Optional[str]:
    """The streamer's own words at the reaction (verbatim, never rewritten).
    Returns None when nobody spoke intelligibly there."""
    spoken = [w for w in words if start - 0.2 <= float(w["start"]) <= end + 0.2]
    if len(spoken) < 2:
        return None  # a lone word ("Come", "Get") is a fragment, not a quote
    # Centre the quote on the most exclamatory word (a '!' or a known
    # reaction word); otherwise start at the reaction's first word.
    from .reaction_moments import _EXCLAIM

    def strength(w: dict[str, Any]) -> int:
        raw = str(w["text"])
        core = re.sub(r"[^\w']", "", raw).lower()
        return 2 * ("!" in raw) + (1 if core and _EXCLAIM.match(core) else 0)

    best = max(range(len(spoken)), key=lambda i: (strength(spoken[i]), -i))
    lo = max(0, min(best - 2, len(spoken) - max_words))
    chosen = spoken[lo:lo + max_words]
    text = " ".join(str(w["text"]) for w in chosen).strip()
    if len(text.replace(" ", "")) < 5:
        return None
    return mask_profanity(text, profanity) or None


# --------------------------------------------------------------------------
# Boundaries
# --------------------------------------------------------------------------

def refine_window(
    start: float,
    end: float,
    reaction_start: float,
    reaction_end: float,
    duration: float,
    words: Optional[Sequence[dict[str, Any]]] = None,
    strength: Optional[Sequence[float]] = None,
) -> tuple[float, float, list[str]]:
    """Snap a window to speech boundaries and finish on the payoff.

    * never starts or ends inside a spoken word;
    * starts on a pause (>= 0.3 s gap) found within 2.5 s before ``start``;
    * ends after the reaction has really died down: it extends while the
      reaction-strength series is still active (up to 4 s) and then past the
      last word that begins within a second of that point;
    * always keeps >= 2 s after the reaction ends.
    Returns (start, end, notes) — notes name every adjustment made.
    """
    notes: list[str] = []
    s, e = start, end
    if strength is not None and len(strength):
        t = int(reaction_end)
        extra = 0
        while extra < 4 and t + extra < len(strength) and strength[t + extra] > 0:
            extra += 1
        if extra and reaction_end + extra + 1.5 > e:
            e = reaction_end + extra + 1.5
            notes.append(f"extended end {extra}s: reaction still active")
    if e < reaction_end + 2.0:
        e = reaction_end + 2.0
        notes.append("kept 2s of payoff after the reaction")
    if words:
        ws = [(float(w["start"]), float(w["end"])) for w in words]
        for ws_, we_ in ws:
            if ws_ < s < we_:
                s = ws_
                notes.append("moved start to the start of the word it cut")
                break
        else:
            gaps = [ws[i + 1][0] for i in range(len(ws) - 1)
                    if ws[i + 1][0] - ws[i][1] >= 0.3 and s - 2.5 <= ws[i + 1][0] <= s]
            if gaps:
                s = max(gaps)
                notes.append("started on a pause between sentences")
        for ws_, we_ in ws:
            if ws_ < e < we_:
                e = we_ + 0.25
                notes.append("moved end past the word it cut")
                break
    return max(0.0, s), min(float(duration), e), notes


# --------------------------------------------------------------------------
# Caption synchronisation
# --------------------------------------------------------------------------

def caption_plan(
    moment: dict[str, Any],
    window_start: float,
    words: Optional[Sequence[dict[str, Any]]] = None,
    facecam_roi: Optional[tuple[float, float, float, float]] = None,
    profanity: str = "mask",
) -> dict[str, Any]:
    """Caption timing/placement data for the renderer (times are clip-relative).

    * emphasis spans sit on the REACTION (measured voice onset), not on the
      loudest game sound;
    * an on-screen-event cue is aligned to when the event appears on screen;
    * placement keeps captions off the facecam rectangle.
    Words are never altered except for the user's profanity setting.
    """
    ev = moment.get("evidence") or {}
    r0 = ev.get("reaction_start_sec")
    r1 = ev.get("reaction_end_sec")
    plan: dict[str, Any] = {"profanity_mode": profanity, "emphasis": [], "event_cue": None,
                            "words_modified": profanity != "keep"}
    if r0 is not None and r1 is not None:
        plan["emphasis"] = [{
            "start": round(max(0.0, r0 - window_start), 2),
            "end": round(max(0.0, r1 - window_start), 2),
            "style": "laugh" if ev.get("reaction_kind") == "laughter" else "shout",
            "reason": "measured reaction span",
        }]
        if words:
            plan["emphasis_words"] = [
                {"text": mask_profanity(str(w["text"]), profanity),
                 "start": round(float(w["start"]) - window_start, 2),
                 "end": round(float(w["end"]) - window_start, 2)}
                for w in words if r0 - 0.2 <= float(w["start"]) <= r1 + 0.2]
    game = ev.get("game_event")
    if game:
        plan["event_cue"] = {"at": round(max(0.0, game["start_sec"] - window_start), 2),
                             "align_to": "event_start", "not_audio_peak": True}
    from .edit_recommendations import recommend_framing

    layout = recommend_framing(facecam_roi, ev.get("moment_type", ""), ev.get("reaction_kind", ""))["layout"]
    plan["position"] = caption_position(facecam_roi, layout)
    return plan


def caption_position(roi: Optional[tuple[float, float, float, float]],
                     layout: Optional[str] = None) -> dict[str, Any]:
    """Where captions should sit, in the app's own caption presets
    (``clip_edits.CAPTION_POSITIONS``: top / middle / bottom).

    * stacked_reactor: on the seam between the reactor pane and the gameplay
      pane (the renderer knows where its seam is; ``middle`` is the fallback);
    * pip_preserved: the half of the frame away from the facecam inset;
    * full / no facecam: the app's default bottom band.
    """
    if roi is None or layout == "full":
        return {"anchor": "frame", "preset": "bottom", "avoid": ["hud"],
                "reason": "no facecam pane: the app's default caption band"}
    x, y, w, h = roi
    roi_out = [round(v, 3) for v in roi]
    if layout == "stacked_reactor":
        return {"anchor": "pane_seam", "preset": "middle", "avoid": ["facecam", "hud"],
                "facecam_roi": roi_out,
                "reason": "two-pane layout: captions sit on the seam between reactor and gameplay"}
    centre_y = y + h / 2
    return {"anchor": "frame", "preset": "top" if centre_y > 0.5 else "bottom",
            "avoid": ["facecam"], "facecam_roi": roi_out,
            "reason": "keep captions on the half of the frame away from the facecam inset"}


def apply_ga2(
    moments: list[dict[str, Any]],
    duration: float,
    *,
    words: Optional[Sequence[dict[str, Any]]] = None,
    strength: Optional[Sequence[float]] = None,
    facecam_roi: Optional[tuple[float, float, float, float]] = None,
    profanity: str = "mask",
) -> list[dict[str, Any]]:
    """Fill hook, refined boundaries and caption plan on reaction moments."""
    from .gameplay_moments import _mmss

    def sec(label: str) -> float:
        m, x = label.split(":")
        return int(m) * 60 + int(x)

    for m in moments:
        ev = m.get("evidence") or {}
        if "moment_type" not in ev:
            continue
        s, e, notes = refine_window(
            sec(m["start_time"]), sec(m["end_time"]),
            ev["reaction_start_sec"], ev["reaction_end_sec"], duration, words, strength)
        m["start_time"], m["end_time"] = _mmss(s), _mmss(e)
        ev["boundary_notes"] = notes
        title = reaction_hook_title(m)
        if title and not m.get("hook_title"):
            m["hook_title"] = title
        if words:
            q = reaction_quote(words, ev["reaction_start_sec"], ev["reaction_end_sec"], profanity)
            if q:
                ev["hook_quote"] = q
        m["caption_plan"] = caption_plan(m, s, words, facecam_roi, profanity)
    return moments
