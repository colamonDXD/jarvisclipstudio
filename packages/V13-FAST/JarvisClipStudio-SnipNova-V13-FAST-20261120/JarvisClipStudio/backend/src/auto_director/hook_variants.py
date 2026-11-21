"""Hook-variant openings for a finished clip (Lane K).

For one clip we derive up to three alternative openings, deterministically
— no AI calls — from what the app already knows: the clip's stored
:class:`~src.auto_director.clip_plan.ClipPlan`, its edit-plan segments and
its niche profile.

* ``current`` — the opening as rendered (the control). Its segments are the
  clip's current layout, untouched.
* ``cold_open`` — the clip reordered so the plan's hook moment leads, with
  the rest of the clip following in its original order. Only offered when
  a planner-produced plan (``source == "llm"``) picked a hook moment late
  enough to be a real reordering, and only while no hook variant has been
  applied yet (the plan's timestamps describe the original timeline, so a
  second cold open on an already-reordered clip would aim at the wrong
  moment).
* ``punch`` — the same start with the opposite opening punch-in strength
  (strong when the current opening is subtle/none, subtle when it is
  strong), produced by the plan/profile punch machinery.

A variant that cannot be produced honestly for a clip is omitted — two
real variants beat three with a fake among them.

Opening strategies (Workstream C) extend the same idea when the caller
supplies the clip's timed transcript words (and, for
``unexpected_visual``, a visual-change signal): ``strongest_statement``,
``genuine_reaction``, ``unexpected_visual``, ``question`` and
``before_the_event``. Each strategy is a re-cut of the SAME clip's own
footage — a whole sentence moved to the front, or the clip's own order
kept — and each passes the same context guard as the cold open (see
``strategy_context_ok``): a sentence that opens on a pronoun /
continuation / back-reference, or that the plan marks as a payoff whose
setup plays earlier, is never moved. A strategy the footage cannot
satisfy honestly is reported by ``strategy_availability`` as
unavailable with the reason, and no preview is derived for it. Without
timed words no strategy is derived at all — the evidence they are
judged on would not exist.

All functions here are pure dict manipulation, mirroring how
``video_service._apply_viral_plan`` consumes a plan into an EDL: plan
transitions and punch are only ever consumed for planner-produced plans,
and plan SFX offsets are mapped onto the rendered timeline before merging.
"""

from __future__ import annotations

import copy
import json
import re
from typing import Any, Optional, Sequence

from .clip_edits import MIN_SEGMENT_SECONDS, normalize_segments
from .clip_plan import (
    ClipPlan,
    HookDecision,
    OpeningTreatment,
    PlannedSfxEvent,
    apply_plan_opening_punch,
    apply_plan_transitions,
    merge_plan_sfx_events,
)
from .edl import _join_plan, _to_rendered_time

VARIANT_CURRENT = "current"
VARIANT_COLD_OPEN = "cold_open"
VARIANT_PUNCH = "punch"
VARIANT_KINDS = (VARIANT_CURRENT, VARIANT_COLD_OPEN, VARIANT_PUNCH)

#: Opening strategies (Workstream C). Each name is also its variant kind
#: when a preview is derived for it.
STRATEGY_STRONGEST_STATEMENT = "strongest_statement"
STRATEGY_GENUINE_REACTION = "genuine_reaction"
STRATEGY_UNEXPECTED_VISUAL = "unexpected_visual"
STRATEGY_QUESTION = "question"
STRATEGY_BEFORE_THE_EVENT = "before_the_event"
STRATEGY_KINDS = (
    STRATEGY_STRONGEST_STATEMENT,
    STRATEGY_GENUINE_REACTION,
    STRATEGY_UNEXPECTED_VISUAL,
    STRATEGY_QUESTION,
    STRATEGY_BEFORE_THE_EVENT,
)
#: A strategy sentence earlier than this already (nearly) opens the clip
#: when it is not sentence 0 — it is still labelled, but not reordered.
STRATEGY_MIN_REORDER_SEC = 1.0

_STRATEGY_LABELS = {
    STRATEGY_STRONGEST_STATEMENT: "Strongest statement first",
    STRATEGY_GENUINE_REACTION: "Genuine reaction first",
    STRATEGY_UNEXPECTED_VISUAL: "Unexpected visual first",
    STRATEGY_QUESTION: "Question first",
    STRATEGY_BEFORE_THE_EVENT: "Just before the event",
}

_REACTION_PHRASES = (
    "no way", "oh my god", "oh my goodness", "are you serious",
    "i can't believe", "i cannot believe", "did you see that",
    "look at that", "what just happened",
)
_REACTION_FIRST_WORDS = frozenset({"wow", "whoa", "woah", "oh", "ooh", "hah"})
_STRONG_WORDS = frozenset(
    {
        "never", "nobody", "everyone", "always", "only", "secret",
        "truth", "wrong", "mistake", "biggest", "best", "worst",
        "largest", "smallest", "fastest", "craziest", "greatest",
    }
)

#: Hook previews are short opening renders, not full clips.
PREVIEW_SECONDS = 6.0
#: A hook earlier than this is already (nearly) the opening — calling it a
#: cold open would be a lie, so no cold-open variant is offered.
COLD_OPEN_MIN_HOOK_SEC = 1.0
#: The hook block that leads a cold open must be long enough to land.
COLD_OPEN_MIN_TAIL_SEC = 1.5
#: Punch keyframes at or before ``at_offset + window`` count as "the
#: opening punch" when one variant replaces it — the same order of window
#: ``apply_plan_opening_punch`` uses when re-scaling an existing keyframe.
_OPENING_PUNCH_WINDOW_SEC = 0.35

_PUNCH_LEVELS = ("none", "subtle", "strong")


def _as_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _mmss(seconds: float) -> str:
    total = max(0, int(round(float(seconds))))
    return f"{total // 60}:{total % 60:02d}"


def plan_from_clip(clip: Optional[dict]) -> Optional[ClipPlan]:
    """Parse the clip record's stored ClipPlan JSON, or None."""
    raw = (clip or {}).get("clip_plan")
    if not raw:
        return None
    try:
        data = json.loads(raw) if isinstance(raw, str) else raw
        if not isinstance(data, dict) or not data:
            return None
        return ClipPlan.from_dict(data)
    except Exception:
        return None


def effective_punch_level(
    plan: Optional[ClipPlan], profile: Optional[dict]
) -> str:
    """The opening punch level the clip was actually rendered with.

    A planner-produced plan's opening treatment is what the pipeline
    applied; anything else rendered with the profile's own punch default
    (the same rule ``plan_service.minimal_plan`` records).
    """
    if plan is not None and plan.source == "llm":
        level = str(plan.opening_treatment.punch_zoom or "none")
        return level if level in _PUNCH_LEVELS else "none"
    zooms = (profile or {}).get("zooms", {})
    return "subtle" if _as_float(zooms.get("punch_in_scale"), 1.0) > 1.0 else "none"


def _windows(segments: list[dict]) -> list[dict]:
    """Segment dicts as timeline windows with an explicit source offset."""
    windows = []
    for segment in segments:
        start = _as_float(segment.get("start"))
        end = _as_float(segment.get("end"))
        if end - start <= 0:
            continue
        window = dict(segment)
        window["start"] = start
        window["end"] = end
        window["source_offset"] = _as_float(
            segment.get("source_offset"), start
        )
        windows.append(window)
    return windows


def _split_at(
    windows: list[dict], at: float
) -> tuple[list[dict], list[dict]]:
    """Split windows into those before ``at`` and those from ``at`` on."""
    head: list[dict] = []
    tail: list[dict] = []
    for window in windows:
        if window["end"] <= at + 1e-6:
            head.append(window)
        elif window["start"] >= at - 1e-6:
            tail.append(window)
        else:
            first = dict(window)
            first["end"] = at
            second = dict(window)
            second["start"] = at
            second["source_offset"] = window["source_offset"] + (
                at - window["start"]
            )
            head.append(first)
            tail.append(second)
    return head, tail


def _retile(windows: list[dict]) -> list[dict]:
    """Re-stamp start/end so the windows tile [0, total] in this order."""
    out = []
    cursor = 0.0
    for window in windows:
        segment = dict(window)
        duration = _as_float(window["end"]) - _as_float(window["start"])
        segment["start"] = round(cursor, 3)
        segment["end"] = round(cursor + duration, 3)
        out.append(segment)
        cursor += duration
    return out


def cold_open_segments(
    segments: list[dict], hook_timestamp: float
) -> Optional[list[dict]]:
    """The clip's segments reordered so the hook moment leads.

    Returns normalized segments (source mapping carried through the
    split), or None when the reordering is not a valid tiling.
    """
    windows = _windows(segments)
    if not windows:
        return None
    head, tail = _split_at(windows, float(hook_timestamp))
    if not head or not tail:
        return None
    try:
        return normalize_segments(_retile(tail + head))
    except ValueError:
        return None


def strategy_for_title_style(style: Optional[str]) -> Optional[str]:
    """The opening strategy that carries a hook title style (D1 mapping).

    ``problem`` titles are carried by the strongest-statement opening;
    ``curiosity_gap`` titles by the question opening. This is a mapping
    only — ``strategy_availability`` still decides, per clip, whether
    that strategy can be produced honestly from the footage.
    """
    from .hook_title_styles import opening_strategy_for_title_style

    return opening_strategy_for_title_style(style)


# --- Opening strategies -------------------------------------------------------
#
# Sentence-level, deterministic, and honest-or-unavailable. Words are
# clip-relative timed dicts ({text|word, start, end}); sentences come
# from clip_openings.split_sentences, the same boundary rules the rest
# of the opening machinery uses.


def _strategy_sentences(words: Sequence[dict]) -> list[dict]:
    from ..clip_openings import split_sentences

    norm = []
    for word in words or []:
        token = str(word.get("text") or word.get("word") or "").strip()
        if not token:
            continue
        try:
            start = float(word.get("start") or 0.0)
            end = float(word.get("end") or start)
        except (TypeError, ValueError):
            start, end = 0.0, 0.0
        norm.append({"text": token, "start": start, "end": end})
    if not norm or not any(w["end"] > 0 for w in norm):
        return []
    sentences = []
    for group in split_sentences([w["text"] for w in norm]):
        toks = [norm[i] for i in group]
        sentences.append(
            {
                "text": " ".join(w["text"] for w in toks),
                "start": float(toks[0]["start"]),
                "end": float(toks[-1]["end"]),
                "first_word_index": group[0],
                "words": norm,
            }
        )
    return sentences


def strategy_context_ok(sentence: dict, plan: Optional[ClipPlan]) -> Optional[str]:
    """None when the sentence may lead, else why it must not move.

    The context guard, per strategy: the sentence must stand on its own
    (the timeline module's weak-opener judgement — pronouns, agreement
    openers, back-references — plus the dangling-conjunction check), and
    the plan must not mark its moment as a payoff whose setup plays
    earlier (the hook-enforcement guard, reused).
    """
    from ..clip_openings import is_dangling, normalize_token

    words = sentence.get("words") or []
    index = int(sentence.get("first_word_index") or 0)
    try:
        from ..media.timeline import _opening_is_weak

        if words and _opening_is_weak(words, index):
            opener = normalize_token(words[index]["text"]) if words else ""
            return (
                f"the sentence opens with '{opener}', which points back "
                "at context the viewer would not have seen"
            )
    except Exception:
        pass
    tokens = [normalize_token(tok) for tok in sentence["text"].split()[:6]]
    if is_dangling([tok for tok in tokens if tok]):
        return "the sentence opens by continuing an earlier thought"
    if plan is not None:
        from .hook_enforcement import _payoff_needs_setup

        reason = _payoff_needs_setup(plan, float(sentence["start"]))
        if reason:
            return reason
    return None


def _sentence_strength(sentence: dict) -> int:
    text = sentence["text"]
    lower = text.lower()
    tokens = [tok.strip(".,!?\"'") for tok in lower.split()]
    score = 0
    if re.search(r"\d", text):
        score += 3
    score += 2 * sum(1 for tok in tokens if tok in _STRONG_WORDS)
    if text.rstrip().endswith("!"):
        score += 1
    return score


def _find_strategy_sentence(
    strategy: str,
    sentences: list[dict],
    plan: Optional[ClipPlan],
    visual_change_seconds: Optional[Sequence[float]],
) -> tuple[Optional[dict], str]:
    """(sentence, unavailable-reason) for one strategy."""
    if strategy == STRATEGY_STRONGEST_STATEMENT:
        ranked = sorted(
            sentences, key=lambda s: (-_sentence_strength(s), s["start"])
        )
        for sentence in ranked:
            if _sentence_strength(sentence) > 0:
                return sentence, ""
        return None, "no sentence makes a specific strong statement (a number, an absolute, or a superlative)"
    if strategy == STRATEGY_GENUINE_REACTION:
        for sentence in sentences:
            lower = sentence["text"].lower()
            first = lower.split()[0].strip(".,!?\"'") if lower.split() else ""
            if first in _REACTION_FIRST_WORDS or any(
                phrase in lower for phrase in _REACTION_PHRASES
            ):
                return sentence, ""
        return None, "no sentence is a genuine reaction in the clip's own words"
    if strategy == STRATEGY_QUESTION:
        for sentence in sentences:
            if sentence["text"].rstrip().endswith("?"):
                return sentence, ""
        return None, "no sentence in the clip is a question"
    if strategy == STRATEGY_UNEXPECTED_VISUAL:
        if not visual_change_seconds:
            return None, "no visual-change signal was supplied, so an unexpected visual cannot be claimed"
        change = float(visual_change_seconds[0])
        for sentence in sentences:
            if sentence["start"] <= change < sentence["end"]:
                return sentence, ""
        return None, "the visual change does not fall inside any spoken sentence"
    if strategy == STRATEGY_BEFORE_THE_EVENT:
        event_sentence: Optional[dict] = None
        phases = list(getattr(plan, "phases", None) or []) if plan is not None else []
        payoff = next((p for p in phases if p.intent == "payoff"), None)
        if payoff is not None:
            event_sentence = next(
                (s for s in sentences if s["start"] <= payoff.start_offset < s["end"]),
                None,
            )
        if event_sentence is None:
            # Deterministic fallback: the strongest later sentence is
            # the event; the sentence right before it is the opening.
            later = [s for s in sentences[1:]]
            if later:
                event_sentence = max(later, key=_sentence_strength)
                if _sentence_strength(event_sentence) <= 0:
                    event_sentence = None
        if event_sentence is None:
            return None, "no event moment (planned payoff or strong later sentence) exists to open just before"
        index = sentences.index(event_sentence)
        if index == 0:
            return None, "the event is already the opening sentence; there is no 'just before' in this clip"
        return sentences[index - 1], ""
    return None, f"unknown strategy {strategy!r}"


def strategy_availability(
    words: Optional[Sequence[dict]],
    plan: Optional[ClipPlan] = None,
    *,
    visual_change_seconds: Optional[Sequence[float]] = None,
    duration: float = 0.0,
) -> list[dict]:
    """Per-strategy availability for one clip, with honest reasons.

    Each entry: {strategy, available, reason, sentence_start,
    sentence_text}. Unavailable is a first-class outcome — a strategy
    that cannot be satisfied from the footage is reported, not faked.
    """
    sentences = _strategy_sentences(words or [])
    out: list[dict] = []
    for strategy in STRATEGY_KINDS:
        if not sentences:
            out.append(
                {
                    "strategy": strategy,
                    "available": False,
                    "reason": "no timed transcript words are available for this clip",
                    "sentence_start": None,
                    "sentence_text": None,
                }
            )
            continue
        sentence, reason = _find_strategy_sentence(
            strategy, sentences, plan, visual_change_seconds
        )
        if sentence is None:
            out.append(
                {
                    "strategy": strategy,
                    "available": False,
                    "reason": reason,
                    "sentence_start": None,
                    "sentence_text": None,
                }
            )
            continue
        guard = strategy_context_ok(sentence, plan)
        if guard:
            out.append(
                {
                    "strategy": strategy,
                    "available": False,
                    "reason": f"context guard: {guard}",
                    "sentence_start": round(float(sentence["start"]), 3),
                    "sentence_text": sentence["text"],
                }
            )
            continue
        if duration and duration - float(sentence["start"]) < COLD_OPEN_MIN_TAIL_SEC:
            out.append(
                {
                    "strategy": strategy,
                    "available": False,
                    "reason": "the strategy sentence is too close to the end to open the clip",
                    "sentence_start": round(float(sentence["start"]), 3),
                    "sentence_text": sentence["text"],
                }
            )
            continue
        out.append(
            {
                "strategy": strategy,
                "available": True,
                "reason": (
                    "the sentence just before the event, in the clip's own order"
                    if strategy == STRATEGY_BEFORE_THE_EVENT
                    else "found in the clip's own footage and passes the context guard"
                ),
                "sentence_start": round(float(sentence["start"]), 3),
                "sentence_text": sentence["text"],
            }
        )
    return out


def derive_strategy_variants(
    segments: list[dict],
    plan: Optional[ClipPlan],
    *,
    words: Optional[Sequence[dict]] = None,
    visual_change_seconds: Optional[Sequence[float]] = None,
    hook_variant_applied: Optional[str] = None,
) -> list[dict]:
    """Preview specs for the strategies the footage honestly supports.

    A reordering strategy moves its WHOLE sentence to the front (the
    cold-open tiling) and is skipped once any hook variant has been
    applied (stored timestamps describe the original timeline).
    ``before_the_event`` never reorders: its layout is the clip's own.
    """
    base = normalize_segments(copy.deepcopy(segments))
    duration = _as_float(base[-1]["end"]) if base else 0.0
    if not base or hook_variant_applied:
        return []
    specs: list[dict] = []
    for entry in strategy_availability(
        words, plan, visual_change_seconds=visual_change_seconds, duration=duration
    ):
        if not entry["available"]:
            continue
        strategy = entry["strategy"]
        start = float(entry["sentence_start"] or 0.0)
        reorders = (
            strategy != STRATEGY_BEFORE_THE_EVENT
            and start >= STRATEGY_MIN_REORDER_SEC
        )
        trims_head = (
            strategy == STRATEGY_BEFORE_THE_EVENT
            and start >= STRATEGY_MIN_REORDER_SEC
        )
        layout = copy.deepcopy(base)
        if reorders:
            reordered = cold_open_segments(base, start)
            if reordered is None:
                continue
            layout = reordered
        elif trims_head:
            # Start the clip at the pre-event sentence: the footage
            # before it is dropped (never reordered), the rest plays
            # in its own order.
            _head, tail = _split_at(_windows(base), start)
            if not tail:
                continue
            try:
                layout = normalize_segments(_retile(tail))
            except ValueError:
                continue
        quote = str(entry["sentence_text"] or "")
        if len(quote) > 90:
            quote = quote[:87].rstrip() + "..."
        specs.append(
            {
                "variant": strategy,
                "strategy": strategy,
                "label": f"Opening strategy — {_STRATEGY_LABELS[strategy]}",
                "description": (
                    f"Starts just before the event, on \"{quote}\" — "
                    "earlier lead-in is trimmed, nothing is reordered."
                    if trims_head
                    else f"Opens just before the event, on \"{quote}\" "
                    "— already at the start of the clip."
                    if strategy == STRATEGY_BEFORE_THE_EVENT
                    else f"Opens on \"{quote}\" at {_mmss(start)}, then "
                    "plays the rest of the clip in its original order."
                    if reorders
                    else f"Opens on \"{quote}\" — already at the start "
                    "of the clip, kept in its own order."
                ),
                "segments": layout,
                "punch_zoom": None,
                "punch_at_offset": 0.0,
                "from_punch_zoom": None,
                "hook_timestamp": round(start, 3),
                "reorders": reorders,
                "trims_head": trims_head,
                "strategy_sentence": entry["sentence_text"],
            }
        )
    return specs


def derive_variants(
    segments: list[dict],
    plan: Optional[ClipPlan],
    profile: Optional[dict],
    *,
    hook_variant_applied: Optional[str] = None,
    words: Optional[Sequence[dict]] = None,
    visual_change_seconds: Optional[Sequence[float]] = None,
) -> list[dict]:
    """Derive the honest hook variants for a clip's current segments.

    ``segments`` are the clip's edit-plan segments (with source mapping).
    Each returned spec carries the full-clip segment layout for that
    variant plus the opening-treatment parameters the render path needs.
    """
    base = normalize_segments(copy.deepcopy(segments))
    duration = _as_float(base[-1]["end"]) if base else 0.0

    specs: list[dict] = [
        {
            "variant": VARIANT_CURRENT,
            "label": "Current opening",
            "description": "The opening exactly as this clip starts now.",
            "segments": copy.deepcopy(base),
            "punch_zoom": None,
            "punch_at_offset": 0.0,
            "from_punch_zoom": None,
            "hook_timestamp": None,
        }
    ]

    if (
        plan is not None
        and plan.source == "llm"
        and not hook_variant_applied
    ):
        hook_ts = _as_float(plan.hook.timestamp)
        if (
            hook_ts >= COLD_OPEN_MIN_HOOK_SEC
            and duration - hook_ts >= COLD_OPEN_MIN_TAIL_SEC
            and duration - hook_ts >= MIN_SEGMENT_SECONDS
        ):
            reordered = cold_open_segments(base, hook_ts)
            if reordered is not None:
                reason = str(plan.hook.reason or "").strip()
                if len(reason) > 120:
                    reason = reason[:117].rstrip() + "..."
                description = (
                    f"Jumps straight to the strongest moment at "
                    f"{_mmss(hook_ts)}"
                    + (f" — {reason}" if reason else "")
                    + ", then plays the start."
                )
                specs.append(
                    {
                        "variant": VARIANT_COLD_OPEN,
                        "label": "Cold open — strongest moment first",
                        "description": description,
                        "segments": reordered,
                        "punch_zoom": None,
                        "punch_at_offset": 0.0,
                        "from_punch_zoom": None,
                        "hook_timestamp": round(hook_ts, 3),
                    }
                )

    # Opening strategies: only with timed transcript evidence, and
    # never duplicating the cold open's exact reordering.
    if words:
        cold_spec = next(
            (s for s in specs if s["variant"] == VARIANT_COLD_OPEN), None
        )
        for spec in derive_strategy_variants(
            base,
            plan,
            words=words,
            visual_change_seconds=visual_change_seconds,
            hook_variant_applied=hook_variant_applied,
        ):
            if (
                cold_spec is not None
                and spec.get("reorders")
                and spec.get("hook_timestamp") == cold_spec.get("hook_timestamp")
            ):
                continue
            specs.append(spec)

    current_level = effective_punch_level(plan, profile)
    target_level = "subtle" if current_level == "strong" else "strong"
    if plan is not None and plan.source == "llm":
        at_offset = _as_float(
            plan.opening_treatment.at_offset or plan.hook.timestamp
        )
    else:
        at_offset = 0.0
    if duration > 0:
        at_offset = max(0.0, min(at_offset, max(0.0, duration - 0.6)))
    if target_level == "strong":
        punch_description = (
            "Same start as now, but the first line gets a strong "
            "zoom punch-in."
        )
        punch_label = "Strong punch-in opening"
    else:
        punch_description = (
            "Same start as now, with a gentler zoom punch-in on the "
            "first line."
        )
        punch_label = "Gentler punch-in opening"
    specs.append(
        {
            "variant": VARIANT_PUNCH,
            "label": punch_label,
            "description": punch_description,
            "segments": copy.deepcopy(base),
            "punch_zoom": target_level,
            "punch_at_offset": round(at_offset, 3),
            "from_punch_zoom": current_level,
            "hook_timestamp": None,
        }
    )
    return specs


def apply_opening_punch(
    edl: dict,
    *,
    level: str,
    at_offset: float,
    profile: Optional[dict],
) -> bool:
    """Set the EDL's opening punch-in to ``level`` at ``at_offset``.

    Any existing punch keyframe in the opening window is replaced (not
    stacked), then the plan machinery computes the scale — subtle stays
    at or below 1.25, strong is at least 1.30 — exactly as it does for a
    planner-chosen opening. Used by the preview renders and by the
    re-render seam when a picked variant is applied to the full clip, so
    the preview and the applied render agree.
    """
    if level not in ("subtle", "strong"):
        return False
    keyframes = edl.get("zoom_keyframes") or []
    kept = [
        keyframe
        for keyframe in keyframes
        if not (
            isinstance(keyframe, dict)
            and keyframe.get("type") == "punch_in"
            and _as_float(keyframe.get("time", keyframe.get("start")))
            <= at_offset + _OPENING_PUNCH_WINDOW_SEC
        )
    ]
    if len(kept) != len(keyframes):
        edl["zoom_keyframes"] = kept
    synthetic = ClipPlan(
        niche="",
        source="hook_variant",
        hook=HookDecision(
            type="none_strong",
            timestamp=float(at_offset),
            confidence=0.0,
            reason="",
        ),
        opening_treatment=OpeningTreatment(
            punch_zoom=level,
            at_offset=float(at_offset),
            reason="Hook variant opening chosen by the user.",
        ),
    )
    return apply_plan_opening_punch(edl, synthetic, profile)


def consume_plan_for_variant(
    edl: dict,
    spec: dict,
    plan: Optional[ClipPlan],
    profile: Optional[dict],
) -> dict:
    """Fold the clip's stored plan into a variant preview's EDL.

    Mirrors ``_apply_viral_plan``'s consumption so a preview sounds and
    moves like the clip it previews: plan transitions and caption style
    for the unchanged-layout variants, the plan's opening punch re-timed
    onto the hook moment for the cold open (the plan's other offsets name
    moments on the original timeline and do not transfer to a reordered
    one), and plan SFX accents merged on the rendered timeline.
    """
    variant = spec["variant"]
    reorders = variant == VARIANT_COLD_OPEN or (
        variant in STRATEGY_KINDS
        and bool(spec.get("reorders") or spec.get("trims_head"))
    )
    if plan is None:
        if variant == VARIANT_PUNCH:
            apply_opening_punch(
                edl,
                level=str(spec.get("punch_zoom") or ""),
                at_offset=_as_float(spec.get("punch_at_offset")),
                profile=profile,
            )
        return edl

    if reorders:
        if (
            plan.source == "llm"
            and plan.opening_treatment.punch_zoom != "none"
        ):
            apply_opening_punch(
                edl,
                level=str(plan.opening_treatment.punch_zoom),
                at_offset=0.0,
                profile=profile,
            )
        return edl

    if plan.source == "llm":
        apply_plan_transitions(edl, plan, profile)
        if variant == VARIANT_PUNCH:
            apply_opening_punch(
                edl,
                level=str(spec.get("punch_zoom") or ""),
                at_offset=_as_float(spec.get("punch_at_offset")),
                profile=profile,
            )
        else:
            apply_plan_opening_punch(edl, plan, profile)
        if plan.caption_style is not None:
            edl.setdefault("captions", {})["preset_id"] = (
                plan.caption_style.name
            )
        plan_events = plan.sfx_events
        joins = _join_plan(edl.get("segments") or [])
        if any(join["fade"] > 0 for join in joins):
            plan_events = [
                PlannedSfxEvent(
                    at_offset=_to_rendered_time(event.at_offset, joins),
                    name=event.name,
                    reason=event.reason,
                )
                for event in plan.sfx_events
            ]
        edl["sfx_events"] = merge_plan_sfx_events(
            edl.get("sfx_events") or [], plan_events
        )
    return edl


def truncate_edl_for_preview(
    edl: dict, seconds: float = PREVIEW_SECONDS
) -> dict:
    """Trim an EDL to its first ``seconds`` for a preview render.

    Segments are clamped to the preview window; zoom keyframes, SFX
    events and overlay events past the window are dropped so nothing in
    the preview references media that is not there.
    """
    segments = edl.get("segments") or []
    kept: list[dict] = []
    for segment in segments:
        start = _as_float(segment.get("start"))
        end = _as_float(segment.get("end"))
        if start >= seconds:
            break
        trimmed = dict(segment)
        trimmed["end"] = round(min(end, seconds), 3)
        trimmed["duration"] = round(trimmed["end"] - start, 3)
        if trimmed["end"] - start > 0.01:
            kept.append(trimmed)
    if kept:
        edl["segments"] = kept
    duration = (
        _as_float(kept[-1]["end"])
        if kept
        else _as_float(edl.get("duration"))
    )
    edl["duration"] = round(duration, 3)
    if edl.get("zoom_keyframes"):
        edl["zoom_keyframes"] = [
            keyframe
            for keyframe in edl["zoom_keyframes"]
            if _as_float(keyframe.get("time", keyframe.get("start")))
            < duration
        ]
    if edl.get("sfx_events"):
        edl["sfx_events"] = [
            event
            for event in edl["sfx_events"]
            if _as_float(event.get("at_sec")) < duration
        ]
    overlays = edl.get("overlay_events")
    if isinstance(overlays, list):
        edl["overlay_events"] = [
            overlay
            for overlay in overlays
            if not isinstance(overlay, dict)
            or _as_float(
                overlay.get("at_sec", overlay.get("start")), 0.0
            )
            < duration
        ]
    return edl
