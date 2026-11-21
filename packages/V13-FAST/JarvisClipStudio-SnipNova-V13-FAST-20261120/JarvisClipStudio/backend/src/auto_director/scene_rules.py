"""Scene-aware editing rules: the per-niche ``scene_rules`` profile section.

Until now every niche profile was consumed by the same uniform machinery:
punch-in zooms landed on every energy peak whatever the profile's
``punch_in_trigger`` label said, plan validation applied one global set of
restraint budgets to comedy and finance alike, and nothing in the edit
knew what a *scene* was — who just started speaking, whether the opening
seconds are an establishing shot, whether a pause is the joke.

This module owns the additive ``scene_rules`` section a profile may carry
(existing fields keep their exact meaning; a profile without the section
behaves exactly as before). Every rule is a pair:

- a TRIGGER — the concrete evidence that fires it (a diarized speaker
  change, a measured pause, the clip's opening window), and
- a RESTRAINT — when it must NOT fire (no speaker labels, a turn too
  short to be a real change, a clip too short to spare an establishing
  shot, a punchline that has earned its strong zoom).

The consumers are the seams that already shape the render:

- ``build_edl`` (edl.py): speaker-change punch-ins and the travel
  establishing-shot guard.
- ``validate_plan_dict`` (clip_plan.py): per-niche plan caps (SFX,
  transitions, punch strength) and comedy pause protection.
- ``plan_service``: the same rules, stated in the planner prompt so the
  LLM plans inside them instead of being corrected after the fact.

Pure and import-light like ``clip_plan``: no LLM, no media stack, no I/O.
Bad or missing rule values degrade to "rule off" — a malformed section
must never cost a render.
"""

from __future__ import annotations

import logging
from typing import Any, Optional

logger = logging.getLogger(__name__)

# --- Speaker-change punch-ins (dialogue niches) ---------------------------
#
# Mirrors media.speaker_framing's discipline (SPEAKER_MIN_TURN_SECONDS /
# the 0.75 s stacking gap in edl.py) without importing the cv2 stack:
# a "yeah" / "right" interjection is not a speaker change worth a zoom.
SPEAKER_MIN_TURN_SEC = 1.2
SPEAKER_MIN_GAP_SEC = 3.0
SPEAKER_MAX_PER_CLIP = 6
SPEAKER_HOOK_GUARD_SEC = 1.0   # never punch inside the opening second
SPEAKER_END_GUARD_SEC = 0.6    # ...or on the clip's last breath
SPEAKER_STACK_GAP_SEC = 0.75   # never stack on an existing punch-in

# --- Comedy pause protection ----------------------------------------------
#
# The pause around a punchline IS the timing. A planned accent that lands
# inside a measured pause (plus this small lead-in) steps on the joke.
PAUSE_PROTECTION_DEFAULT_SEC = 0.8


def _as_float(value: Any, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _as_int(value: Any, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def scene_rules(profile: Optional[dict]) -> dict[str, Any]:
    """The profile's validated ``scene_rules`` section ({} when absent).

    Only the keys this module defines are returned, each coerced to its
    documented type/range; anything unusable is dropped, which switches
    that rule off rather than failing.
    """
    raw = (profile or {}).get("scene_rules")
    if not isinstance(raw, dict):
        return {}

    rules: dict[str, Any] = {}

    speaker = raw.get("speaker_punch_in")
    if isinstance(speaker, dict) and speaker.get("enabled"):
        rules["speaker_punch_in"] = {
            "enabled": True,
            "min_turn_sec": max(0.5, _as_float(speaker.get("min_turn_sec"), SPEAKER_MIN_TURN_SEC)),
            "min_gap_sec": max(1.0, _as_float(speaker.get("min_gap_sec"), SPEAKER_MIN_GAP_SEC)),
            "max_per_clip": max(1, _as_int(speaker.get("max_per_clip"), SPEAKER_MAX_PER_CLIP)),
        }

    establishing = _as_float(raw.get("establishing_shot_sec"), 0.0)
    if establishing > 0:
        rules["establishing_shot_sec"] = min(establishing, 10.0)

    sfx_cap = raw.get("plan_sfx_cap")
    if sfx_cap is not None:
        rules["plan_sfx_cap"] = max(0, _as_int(sfx_cap, 0))

    transition_cap = raw.get("plan_transition_cap")
    if transition_cap is not None:
        rules["plan_transition_cap"] = max(0, _as_int(transition_cap, 0))

    punch_cap = raw.get("plan_punch_zoom_cap")
    if punch_cap in ("none", "subtle", "strong"):
        rules["plan_punch_zoom_cap"] = punch_cap

    strong_hooks = raw.get("strong_punch_allowed_hook_types")
    if isinstance(strong_hooks, list):
        hooks = [h for h in strong_hooks if isinstance(h, str) and h]
        if hooks:
            rules["strong_punch_allowed_hook_types"] = hooks

    pause_protection = raw.get("pause_protection_sec")
    if pause_protection is not None:
        value = _as_float(pause_protection, 0.0)
        if value > 0:
            rules["pause_protection_sec"] = min(value, 3.0)

    bold_claim = raw.get("bold_claim_opener")
    if isinstance(bold_claim, dict) and bold_claim.get("enabled"):
        rules["bold_claim_opener"] = {
            "enabled": True,
            "min_score": max(1, _as_int(bold_claim.get("min_score"), BOLD_CLAIM_MIN_SCORE)),
            "window_sec": max(5.0, _as_float(bold_claim.get("window_sec"), BOLD_CLAIM_WINDOW_SEC)),
        }

    return rules


def speaker_change_times(
    transcript_words: Optional[list[dict]],
    *,
    min_turn_sec: float = SPEAKER_MIN_TURN_SEC,
) -> list[float]:
    """Clip-timeline times where the diarized speaker genuinely changes.

    TRIGGER evidence: consecutive timed words carrying ``speaker`` labels
    (AssemblyAI diarization is the only transcription path that supplies
    them; faster-whisper words carry ``speaker=None``). Runs of one
    speaker shorter than ``min_turn_sec`` are absorbed into the previous
    run first — an interjection is not a change. The change time is the
    incoming speaker's first word start.

    RESTRAINT: returns [] when fewer than two speakers speak, when words
    are untimed (the degraded re-render word shape sits at 0.0), or when
    no labels exist at all. Same inputs -> same times, always.
    """
    runs: list[dict[str, Any]] = []
    for word in transcript_words or []:
        if not isinstance(word, dict):
            continue
        speaker = word.get("speaker")
        if speaker is None:
            continue
        try:
            start = float(word.get("start", 0.0) or 0.0)
            end = float(word.get("end", start) or start)
        except (TypeError, ValueError):
            continue
        if end <= start:
            continue
        if runs and runs[-1]["speaker"] == str(speaker):
            runs[-1]["end"] = max(runs[-1]["end"], end)
            continue
        runs.append({"speaker": str(speaker), "start": start, "end": end})

    # Absorb short runs into the previous speaker (or the next one when
    # the short run opens the clip), exactly like speaker_framing does,
    # so back-channels never register as changes.
    def _coalesce(items: list[dict]) -> list[dict]:
        merged: list[dict] = []
        for item in items:
            if merged and merged[-1]["speaker"] == item["speaker"]:
                merged[-1]["end"] = max(merged[-1]["end"], item["end"])
            else:
                merged.append(dict(item))
        return merged

    runs = _coalesce(runs)
    while len(runs) > 1:
        shortest = min(
            range(len(runs)), key=lambda i: runs[i]["end"] - runs[i]["start"]
        )
        if runs[shortest]["end"] - runs[shortest]["start"] >= min_turn_sec:
            break
        neighbour = shortest - 1 if shortest > 0 else 1
        lo, hi = sorted((shortest, neighbour))
        runs[lo] = {
            "speaker": runs[neighbour]["speaker"],
            "start": runs[lo]["start"],
            "end": max(runs[lo]["end"], runs[hi]["end"]),
        }
        del runs[hi]
        runs = _coalesce(runs)

    if len({run["speaker"] for run in runs}) < 2:
        return []
    return [round(float(run["start"]), 3) for run in runs[1:]]


def establishing_guard_sec(profile: Optional[dict], clip_duration: float) -> float:
    """Seconds of protected establishing shot at the clip's open (0 = off).

    TRIGGER: the profile declares ``establishing_shot_sec`` (travel
    niches) — the opening wide/context shot must play clean: no punch-in
    zooms, no SFX accents, no B-roll cutaway lands inside it. The Ken
    Burns drift is exempt: it IS the establishing move.

    RESTRAINT: a clip no longer than twice the guard cannot spare it —
    stripping every effect from a 5 s clip to protect a 3 s opening
    would leave nothing — so the guard switches off entirely.
    """
    rules = scene_rules(profile)
    guard = float(rules.get("establishing_shot_sec") or 0.0)
    if guard <= 0 or clip_duration <= guard * 2:
        return 0.0
    return guard


def plan_sfx_cap(profile: Optional[dict], default_cap: int) -> int:
    """The plan SFX cap for this profile (its scene rule or the global)."""
    cap = scene_rules(profile).get("plan_sfx_cap")
    return default_cap if cap is None else min(default_cap, int(cap))


def plan_transition_cap(profile: Optional[dict], default_cap: int) -> int:
    """The non-default-transition cap for this profile."""
    cap = scene_rules(profile).get("plan_transition_cap")
    return default_cap if cap is None else min(default_cap, int(cap))


def capped_punch_zoom(
    requested: str, *, cap: Optional[str], hook_type: str, allowed_hook_types: Optional[list]
) -> str:
    """Apply a profile's punch-strength ceiling to a planned punch zoom.

    TRIGGER for keeping ``strong`` despite a ``subtle`` cap: the clip's
    hook is one of the profile's ``strong_punch_allowed_hook_types``
    (comedy: a real punchline has earned the hard punch). RESTRAINT:
    every other strong request is downgraded one step, not dropped —
    the moment still earned emphasis, just not the biggest one.
    """
    if requested != "strong" or cap is None or cap == "strong":
        return requested
    if allowed_hook_types and hook_type in allowed_hook_types:
        return requested
    return cap


def inside_protected_pause(
    at_offset: float, pauses: Optional[list[dict]], protection_sec: float
) -> bool:
    """True when a planned accent lands on a protected comedy pause.

    A pause window is [start - protection lead-in, end]: an accent in
    the breath right before the silence, or inside it, breaks the
    timing the pause is holding.
    """
    if protection_sec <= 0:
        return False
    for pause in pauses or []:
        if not isinstance(pause, dict):
            continue
        try:
            start = float(pause.get("start", 0.0) or 0.0)
            end = float(pause.get("end", start) or start)
        except (TypeError, ValueError):
            continue
        if start - protection_sec <= at_offset <= end:
            return True
    return False


# --- Bold-claim opener (travel niches, Trend-Radar candidate D7) ----------
#
# DETERMINISTIC marker definition, fixed before any measurement (the
# A/B report quotes this list verbatim). A sentence is claim-bearing
# when its score reaches BOLD_CLAIM_MIN_SCORE: one STRONG marker (an
# explicit money figure, a "wish I knew"-type confession hook) or two
# MEDIUM markers (superlatives / absolutes / value judgements).
#
# STRONG (2 points each):
#   - money figure: $1,000 / $400 / 400 dollars / 10000 yen / ₹500
#   - phrases: "wish i knew", "nobody tells you", "no one tells you",
#     "don't waste your", "stop wasting"
# MEDIUM (1 point each):
#   - superlatives/absolutes: most expensive, cheapest, best, worst,
#     biggest, oldest, first, only, never, always
#   - value/shock words: expensive, incredible, insane, unbelievable,
#     amazing, shocking, secret, hidden, mistake, avoid, worth it,
#     must try, must visit, overpriced, rip-off, ripoff, bargain,
#     priceless, luxury
import re as _re

_STRONG_PHRASES = (
    "wish i knew",
    "nobody tells you",
    "no one tells you",
    "don't waste your",
    "dont waste your",
    "stop wasting",
)
_MONEY_RE = _re.compile(
    r"(?:\$\s?\d[\d,]*(?:\.\d+)?|\d[\d,]*(?:\.\d+)?\s?(?:dollars?|yen|usd|bucks)\b|₹\s?\d[\d,]*)",
    _re.IGNORECASE,
)
_MEDIUM_MARKERS = (
    "most expensive", "cheapest", "best", "worst", "biggest", "oldest",
    "first", "only", "never", "always", "expensive", "incredible",
    "insane", "unbelievable", "amazing", "shocking", "secret", "hidden",
    "mistake", "avoid", "worth it", "must try", "must visit",
    "overpriced", "rip-off", "ripoff", "bargain", "priceless", "luxury",
)
BOLD_CLAIM_MIN_SCORE = 2
BOLD_CLAIM_WINDOW_SEC = 25.0


def claim_marker_score(sentence_text: str) -> int:
    """Deterministic claim/cost marker score for one sentence (D7).

    STRONG phrase = 2, money figure = 2, each distinct MEDIUM marker
    word/phrase present = 1. No ML, no LLM: same text, same score.
    """
    # Punctuation-insensitive token text: "the BEST sandwich!" and
    # "the best sandwich" score identically.
    normalised = _re.sub(r"[^a-z0-9'$ ]+", " ", sentence_text.lower())
    text = f" {_re.sub(r' +', ' ', normalised).strip()} "
    score = 0
    if _MONEY_RE.search(sentence_text):
        score += 2
    for phrase in _STRONG_PHRASES:
        if f" {phrase} " in text:
            score += 2
    for marker in _MEDIUM_MARKERS:
        if f" {marker} " in text:
            score += 1
    return score


def split_sentences(words: Optional[list[dict]]) -> list[dict]:
    """Segment clip-timeline words into sentences.

    Uses the timeline module's own sentence rules (terminal punctuation
    or a clear pause) — the same boundaries hook_enforcement's
    sentence_block trusts. Each entry: {start, end, text, first_idx,
    last_idx} on the words' own timeline.
    """
    timed = [
        w for w in (words or [])
        if isinstance(w, dict)
        and isinstance(w.get("start"), (int, float))
        and isinstance(w.get("end"), (int, float))
        and float(w.get("end", 0)) > 0
    ]
    if not timed:
        return []
    try:
        from ..media.timeline import _word_starts_sentence, word_ends_sentence
        from ..media.common import SENTENCE_PAUSE_BOUNDARY_SECONDS
    except Exception:
        return []

    def _text(w: dict) -> str:
        return str(w.get("text") or w.get("word") or "")

    sentences: list[dict] = []
    first = 0
    for idx in range(1, len(timed) + 1):
        boundary = idx == len(timed)
        if not boundary:
            prev, nxt = timed[idx - 1], timed[idx]
            boundary = (
                _word_starts_sentence(timed, idx)
                or word_ends_sentence(_text(prev))
                or float(nxt.get("start", 0)) - float(prev.get("end", 0))
                >= SENTENCE_PAUSE_BOUNDARY_SECONDS
            )
        if boundary:
            chunk = timed[first:idx]
            sentences.append({
                "start": round(float(chunk[0].get("start", 0)), 3),
                "end": round(float(chunk[-1].get("end", 0)), 3),
                "text": " ".join(_text(w) for w in chunk).strip(),
                "first_idx": first,
                "last_idx": idx - 1,
            })
            first = idx
    return [s for s in sentences if s["text"]]


def pick_bold_claim_sentence(
    words: Optional[list[dict]],
    *,
    min_score: int = BOLD_CLAIM_MIN_SCORE,
    window_sec: float = BOLD_CLAIM_WINDOW_SEC,
) -> dict[str, Any]:
    """Pick the bold-claim opener candidate for a clip (D7).

    Returns a dict with ``status``:
    - ``already_claim``: the opening sentence already scores >=
      min_score — nothing to fix, no move proposed.
    - ``candidate``: the highest-scoring later sentence starting within
      ``window_sec`` of the clip start (ties: earliest). Carries its
      sentence dict + score.
    - ``no_claim``: no sentence in the window reaches min_score.

    RESTRAINT: this picker only *nominates*; hook_enforcement still
    applies its context guards (weak opener, block shape) before any
    reorder happens, and a payoff/punchline plan hook is never
    overridden by this rule in the pipeline wiring.
    """
    sentences = split_sentences(words)
    if not sentences:
        return {"status": "no_claim", "sentences": []}
    scored = [
        {**s, "score": claim_marker_score(s["text"])} for s in sentences
    ]
    opener = scored[0]
    if opener["score"] >= min_score:
        return {"status": "already_claim", "opener": opener, "sentences": scored}
    origin = float(scored[0]["start"])
    candidates = [
        s for s in scored[1:]
        if s["start"] - origin <= window_sec and s["score"] >= min_score
    ]
    if not candidates:
        return {"status": "no_claim", "opener": opener, "sentences": scored}
    best = max(candidates, key=lambda s: (s["score"], -s["start"]))
    return {
        "status": "candidate",
        "opener": opener,
        "candidate": best,
        "sentences": scored,
    }


def scene_rule_summary_lines(profile: Optional[dict]) -> list[str]:
    """Plain-language rule lines for the planner prompt (plan_service).

    The LLM plans inside the same rules the validators enforce; a rule
    that only exists as a post-hoc correction invites the planner to
    keep proposing effects the niche forbids.
    """
    rules = scene_rules(profile)
    if not rules:
        return []
    lines = []
    if rules.get("speaker_punch_in"):
        lines.append(
            "Scene rule — dialogue: punch-in zooms belong on speaker changes "
            "and genuine reactions, not on arbitrary lines; clean dialogue "
            "with no zoom is the default between changes."
        )
    if rules.get("establishing_shot_sec"):
        lines.append(
            f"Scene rule — establishing shot: the first "
            f"{rules['establishing_shot_sec']:.0f}s are a clean context shot. "
            "No punch zoom, SFX accent or non-default transition may land "
            "inside that window."
        )
    if rules.get("bold_claim_opener"):
        lines.append(
            "Scene rule — travel opener: open on a bold claim, a cost or a "
            "'wish I knew' line when the clip has one — not on a slow "
            "descriptive sentence. Only claims the clip itself delivers "
            "count; never invent a claim the footage does not back up."
        )
    if rules.get("pause_protection_sec"):
        lines.append(
            "Scene rule — comedy timing: pauses are protected. Never place "
            "an SFX accent or punch zoom on or just before a pause; the "
            "silence around a punchline is the joke's timing."
        )
    caps = []
    if "plan_sfx_cap" in rules:
        caps.append(f"at most {rules['plan_sfx_cap']} SFX accents")
    if "plan_transition_cap" in rules:
        caps.append(f"at most {rules['plan_transition_cap']} non-default transition(s)")
    if rules.get("plan_punch_zoom_cap"):
        allowed = rules.get("strong_punch_allowed_hook_types") or []
        caps.append(
            f"punch zoom capped at {rules['plan_punch_zoom_cap']}"
            + (f" (strong only for: {', '.join(allowed)})" if allowed else "")
        )
    if caps:
        lines.append("Scene rule — restraint caps for this niche: " + "; ".join(caps) + ".")
    return lines
