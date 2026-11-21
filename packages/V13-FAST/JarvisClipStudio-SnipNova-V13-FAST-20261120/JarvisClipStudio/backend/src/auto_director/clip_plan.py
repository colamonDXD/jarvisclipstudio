"""Per-clip viral plan: the Viral Intelligence Engine's decision record.

Until now the pipeline picked ONE niche profile per video and applied it
uniformly to every clip. A plan is the per-clip layer on top of that: for
one selected segment it records how THIS clip should be edited — what its
hook is and where it lands, how the opening is treated, where sound-effect
accents belong, and whether the profile's music should play — together with
the reason for each call, in plain language.

This module is deliberately import-light (no LLM stack, no media stack) so
the render-completion and API paths can read plans without paying for the
planner. Generation lives in ``plan_service``; this module owns the data
shape, the JSON schema, validation/sanitising of untrusted planner output,
the deterministic fallback plan (today's behaviour), and the consumers
that fold a validated plan into an EDL (SFX merge, per-clip transition
choices, the opening punch zoom, and — Smart B-Roll v2 — the planner's
B-roll concepts replacing the keyword-planned stock cues).

Explainability + restraint contract: every creative decision in a plan
carries a plain-language reason that names the content moment justifying
it. Validation enforces "no reason, no effect" — an effect decision
(a transition that differs from the profile default, a punch zoom, a
caption style that differs from the profile default, any SFX event, or
switching the profile's music off) whose reason is missing, empty or
boilerplate is dropped back to the profile default, and the drop is
logged. Restraint budgets cap how many non-default transitions and zoom
accents one clip may carry.

Fail-safe contract: nothing in here may raise on bad input — validation
repairs or drops what it can and reports the rest as a minimal plan.
"""

from __future__ import annotations

import json
import logging
import math
import re
from dataclasses import dataclass, field
from typing import Any, Optional

from .edl import (
    BROLL_CONCEPT_MAX_CUE_SEC,
    BROLL_CONCEPT_MIN_CUE_SEC,
    BROLL_HOOK_GUARD_SEC,
    SFX_EVENT_NAMES,
    _plan_broll_segments,
    broll_target_count,
    broll_uses_v3,
)
from .scene_rules import (
    capped_punch_zoom,
    establishing_guard_sec,
    inside_protected_pause,
    plan_sfx_cap,
    plan_transition_cap,
    scene_rules,
)

logger = logging.getLogger(__name__)

# --- Taxonomies -----------------------------------------------------------

HOOK_TYPES = (
    "strong_statement",
    "question",
    "punchline",
    "surprising_visual",
    "controversial",
    "emotional_reaction",
    "reveal",
    "sound_led",
    "visual_transition",
    "gameplay_moment",
    "action_moment",
    "before_after",
    "none_strong",
)

PHASE_INTENTS = (
    "hook",
    "context",
    "build",
    "payoff",
    "reaction",
    "conclusion",
    "loop_point",
)

PUNCH_ZOOM_LEVELS = ("none", "subtle", "strong")

# --- Placement / density discipline (mirrors edl.py's SFX emitter) --------

# A planned accent never lands this close to the clip's edges.
SFX_EDGE_GUARD_SEC = 0.35
# Two planned accents closer than this are one accent, not two.
SFX_MIN_GAP_SEC = 0.8
# Plan accents are *additional* punctuation on top of the EDL emitter's own
# events (which already cap at 8 peak hits), so the plan's own cap is
# tighter: the four strongest signals survive.
PLAN_SFX_MAX_EVENTS = 4
# When a planned event sits within this of an EDL-native event, the native
# event wins and the planned one is dropped (no stacked double-hits).
MERGE_DEDUPE_WINDOW_SEC = 0.3

# Gains for planned events once merged into an EDL, mirroring the levels
# _create_sfx_events uses for the same names.
PLAN_SFX_GAIN_DB = {
    "whoosh": -6.0,
    "riser": -6.0,
    "ding": -6.0,
    "pop": -3.0,
    "sting": -3.0,
    "impact": 0.0,
}

_MAX_REASON_CHARS = 240
_MAX_PHASES = 8

# --- B-roll concepts (Smart B-Roll v2, Slice 2) ---------------------------
#
# A concept is the planner's own visual reading of one spoken moment,
# written BEFORE anything searches a stock library: a filmable sentence,
# the search phrases derived from it, the resolved sense when the moment
# turns on an ambiguous word, and the subjects that would be wrong. The
# core rule it exists to enforce: no bare transcript word ever reaches a
# stock provider again — the concept's phrases are what gets searched.
#
# Sense-trap words: spellings that name more than one filmable thing
# (the planner prompt carries the same table with the competing senses).
# A concept whose spoken moment turns on one of these words must carry
# a resolved ``sense`` tag ("market:financial") or validation drops it —
# an untagged trap-word concept is exactly the bare-word failure this
# slice removes. The deterministic WORD_SENSES table in src/broll.py
# covers the subset the keyword planner can judge mechanically
# (train/race/pool/market); the planner can resolve the rest from
# context, which is why this list is the wider one.
BROLL_SENSE_TRAP_WORDS = frozenset(
    {
        "market", "bank", "match", "crane", "spring", "cap", "bull",
        "bear", "bat", "tank", "jam", "pitch", "strike", "bond",
        "interest", "short", "long", "stream", "viral", "train",
        "race", "pool",
    }
)
BROLL_CONCEPT_MEDIA = ("video", "photo", "either")
_MAX_CONCEPT_CHARS = 240
_MAX_CONCEPT_QUERIES = 3
_MAX_CONCEPT_AVOID = 6
_MAX_AVOID_CHARS = 60
# Two concepts anchored closer than this are the same moment said twice.
_CONCEPT_DEDUPE_GAP_SEC = 1.0

# --- Restraint budgets (Acceptance: professionally edited, not overloaded)
#
# At most this many joins in one clip may deviate from the profile's
# default transition. Excess choices are dropped weakest-last: the
# earliest choices (by offset) survive.
MAX_NON_DEFAULT_TRANSITIONS = 2
# At most this many zoom accents per clip. In v1 the plan carries exactly
# one zoom decision (the opening punch), so this budget is enforced
# structurally; the constant is the contract phase-2 zoom accents must
# validate against.
MAX_ZOOM_ACCENTS = 2
# When a plan replaces a hard cut with a real crossfade but the profile
# only defines a cut-length duration, the join gets this duration — the
# same 0.4s the wipe-style profiles use — so the transition is visible.
PLAN_TRANSITION_DEFAULT_DURATION_SEC = 0.4

# Reasons that say nothing about the content. A decision justified only
# by one of these is not justified at all ("no reason, no effect").
BOILERPLATE_REASONS = frozenset(
    {
        "for engagement",
        "to boost engagement",
        "for more engagement",
        "more engaging",
        "to make it viral",
        "to go viral",
        "for virality",
        "for views",
        "for more views",
        "looks good",
        "sounds good",
        "for effect",
        "adds energy",
        "for energy",
        "just because",
    }
)

_CUT_TRANSITION_NAMES = frozenset({"cut", "hard_cut", "none"})


def _reason_ok(reason: Any) -> bool:
    """True when a decision reason actually justifies the decision.

    The reason must be non-empty and must not be boilerplate: a generic
    filler phrase ("for engagement", "to make it viral") — alone or as
    the whole substance of a short reason — fails.
    """
    if not isinstance(reason, str):
        return False
    text = reason.strip()
    if not text:
        return False
    normalized = re.sub(r"[^a-z0-9 ]+", "", text.lower())
    normalized = re.sub(r"\s+", " ", normalized).strip()
    if not normalized:
        return False
    if normalized in BOILERPLATE_REASONS:
        return False
    if len(normalized.split()) <= 4 and any(
        phrase in normalized for phrase in BOILERPLATE_REASONS
    ):
        return False
    return True


# The valid vocabularies for per-clip transition / caption decisions live
# with the renderer and the caption registry. They are imported lazily so
# this module stays import-light and a broken media stack degrades plans
# to profile defaults instead of breaking plan reads.
_transition_names_cache: Optional[frozenset] = None
_caption_names_cache: Optional[frozenset] = None


def _valid_transition_names() -> frozenset:
    global _transition_names_cache
    if _transition_names_cache is None:
        try:
            from ..media.edl_render import TRANSITION_MAP

            _transition_names_cache = frozenset(TRANSITION_MAP)
        except Exception:
            logger.warning(
                "Could not load the renderer transition map; "
                "per-clip transition choices will be dropped",
                exc_info=True,
            )
            return frozenset()
    return _transition_names_cache


def _valid_caption_names() -> frozenset:
    global _caption_names_cache
    if _caption_names_cache is None:
        try:
            from ..caption_templates import get_template_names

            _caption_names_cache = frozenset(get_template_names())
        except Exception:
            logger.warning(
                "Could not load the caption template registry; "
                "per-clip caption styles will be dropped",
                exc_info=True,
            )
            return frozenset()
    return _caption_names_cache


def _profile_default_transition(profile: Optional[dict]) -> str:
    transitions = (profile or {}).get("transitions", {})
    return str(transitions.get("default") or "cut").lower()


def _profile_default_caption(profile: Optional[dict]) -> str:
    captions = (profile or {}).get("captions", {})
    return str(captions.get("preset_id") or "default")


def _profile_transition_duration(profile: Optional[dict]) -> float:
    transitions = (profile or {}).get("transitions", {})
    try:
        return float(transitions.get("duration_sec") or 0.0)
    except (TypeError, ValueError):
        return 0.0

# JSON schema for the stored plan shape. ``validate_plan_dict`` enforces it;
# the planner's prompt and the docs both describe this same structure.
CLIP_PLAN_JSON_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": ["niche", "hook"],
    "properties": {
        "niche": {"type": "string"},
        "hook": {
            "type": "object",
            "required": ["type"],
            "properties": {
                "type": {"type": "string", "enum": list(HOOK_TYPES)},
                "timestamp": {"type": "number", "minimum": 0},
                "confidence": {"type": "number", "minimum": 0, "maximum": 1},
                "reason": {"type": "string"},
            },
        },
        "opening_treatment": {
            "type": "object",
            "properties": {
                "punch_zoom": {"type": "string", "enum": list(PUNCH_ZOOM_LEVELS)},
                "first_caption_emphasis": {"type": "boolean"},
                "sfx_accent": {"type": ["string", "null"], "enum": list(SFX_EVENT_NAMES) + [None]},
                "at_offset": {"type": "number", "minimum": 0},
                "reason": {"type": "string"},
            },
        },
        "transition_choices": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["name"],
                "properties": {
                    "name": {"type": "string"},
                    "at_offset": {"type": "number", "minimum": 0},
                    "reason": {"type": "string"},
                },
            },
        },
        "caption_style": {
            "type": ["object", "null"],
            "properties": {
                "name": {"type": "string"},
                "reason": {"type": "string"},
            },
        },
        "phases": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["start_offset", "end_offset", "intent"],
                "properties": {
                    "start_offset": {"type": "number", "minimum": 0},
                    "end_offset": {"type": "number", "minimum": 0},
                    "intent": {"type": "string", "enum": list(PHASE_INTENTS)},
                },
            },
        },
        "sfx_events": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["at_offset", "name"],
                "properties": {
                    "at_offset": {"type": "number", "minimum": 0},
                    "name": {"type": "string", "enum": list(SFX_EVENT_NAMES)},
                    "reason": {"type": "string"},
                },
            },
        },
        "broll_concepts": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["anchor_sec", "concept", "search_queries"],
                "properties": {
                    "anchor_sec": {"type": "number", "minimum": 0},
                    "spoken": {"type": "string"},
                    "concept": {"type": "string"},
                    "search_queries": {
                        "type": "array",
                        "items": {"type": "string"},
                        "minItems": 1,
                        "maxItems": 3,
                    },
                    "sense": {"type": ["string", "null"]},
                    "media": {"type": "string", "enum": list(BROLL_CONCEPT_MEDIA)},
                    "avoid": {"type": "array", "items": {"type": "string"}},
                    "duration_sec": {
                        "type": ["number", "null"],
                        "minimum": BROLL_CONCEPT_MIN_CUE_SEC,
                        "maximum": BROLL_CONCEPT_MAX_CUE_SEC,
                    },
                },
            },
        },
        "music_decision": {
            "type": "object",
            "properties": {
                "use_profile_music": {"type": "boolean"},
                "reason": {"type": "string"},
            },
        },
        "plan_confidence": {"type": "number", "minimum": 0, "maximum": 1},
        "source": {"type": "string", "enum": ["llm", "fallback"]},
    },
}


# --- Data shape -----------------------------------------------------------


@dataclass
class HookDecision:
    type: str = "none_strong"
    timestamp: float = 0.0  # seconds from the clip's start
    confidence: float = 0.0
    reason: str = ""


@dataclass
class OpeningTreatment:
    punch_zoom: str = "none"
    first_caption_emphasis: bool = False
    sfx_accent: Optional[str] = None
    # Where the treatment lands (seconds from clip start; normally the
    # hook timestamp) and why — a punch zoom or accent without a reason
    # naming the moment is dropped by validation.
    at_offset: float = 0.0
    reason: str = ""


@dataclass
class TransitionChoice:
    """One per-clip transition decision for an internal EDL join.

    ``at_offset`` targets the join nearest to it on the clip timeline;
    ``name`` must resolve in the renderer's transition map.
    """

    name: str = "cut"
    at_offset: float = 0.0
    reason: str = ""


@dataclass
class CaptionStyleChoice:
    """One per-clip caption template decision (registry template name)."""

    name: str = "default"
    reason: str = ""


@dataclass
class Phase:
    start_offset: float = 0.0
    end_offset: float = 0.0
    intent: str = "context"


@dataclass
class PlannedSfxEvent:
    at_offset: float = 0.0
    name: str = "pop"
    reason: str = ""


@dataclass
class MusicDecision:
    use_profile_music: bool = True
    reason: str = ""


@dataclass
class BrollConcept:
    """One planner-written B-roll concept (Smart B-Roll v2, Slice 2).

    ``anchor_sec`` is where the illustrated line is spoken (seconds from
    clip start); ``spoken`` is that clause, kept as provenance;
    ``concept`` is ONE filmable visual sentence — something a camera can
    point at; ``search_queries`` are the 2–3 concrete phrases derived
    from it (never a bare word); ``sense`` is the resolved sense tag
    ("market:financial") when the moment turns on a sense-trap word;
    ``media`` is video | photo | either; ``avoid`` names the wrong-sense
    subjects the footage must not show; ``duration_sec`` is the
    planner's optional cue-length hint (2.5–4.0 s — a slow chart pan
    earns longer than a quick product flash; None means the fixed
    3.0 s default).
    """

    anchor_sec: float = 0.0
    spoken: str = ""
    concept: str = ""
    search_queries: list[str] = field(default_factory=list)
    sense: Optional[str] = None
    media: str = "either"
    avoid: list[str] = field(default_factory=list)
    duration_sec: Optional[float] = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "anchor_sec": round(float(self.anchor_sec), 3),
            "spoken": self.spoken,
            "concept": self.concept,
            "search_queries": list(self.search_queries),
            "sense": self.sense,
            "media": self.media,
            "avoid": list(self.avoid),
            "duration_sec": (
                round(float(self.duration_sec), 3)
                if self.duration_sec is not None
                else None
            ),
        }


@dataclass
class ClipPlan:
    niche: str
    hook: HookDecision = field(default_factory=HookDecision)
    opening_treatment: OpeningTreatment = field(default_factory=OpeningTreatment)
    transition_choices: list[TransitionChoice] = field(default_factory=list)
    caption_style: Optional[CaptionStyleChoice] = None
    phases: list[Phase] = field(default_factory=list)
    sfx_events: list[PlannedSfxEvent] = field(default_factory=list)
    broll_concepts: list[BrollConcept] = field(default_factory=list)
    music_decision: MusicDecision = field(default_factory=MusicDecision)
    plan_confidence: float = 0.0
    # How this plan was produced: "llm" (planner call succeeded and its
    # output validated) or "fallback" (deterministic profile defaults).
    source: str = "fallback"

    def to_dict(self) -> dict[str, Any]:
        return {
            "niche": self.niche,
            "hook": {
                "type": self.hook.type,
                "timestamp": round(float(self.hook.timestamp), 3),
                "confidence": round(float(self.hook.confidence), 3),
                "reason": self.hook.reason,
            },
            "opening_treatment": {
                "punch_zoom": self.opening_treatment.punch_zoom,
                "first_caption_emphasis": bool(self.opening_treatment.first_caption_emphasis),
                "sfx_accent": self.opening_treatment.sfx_accent,
                "at_offset": round(float(self.opening_treatment.at_offset), 3),
                "reason": self.opening_treatment.reason,
            },
            "transition_choices": [
                {
                    "name": t.name,
                    "at_offset": round(float(t.at_offset), 3),
                    "reason": t.reason,
                }
                for t in self.transition_choices
            ],
            "caption_style": (
                {"name": self.caption_style.name, "reason": self.caption_style.reason}
                if self.caption_style is not None
                else None
            ),
            "phases": [
                {
                    "start_offset": round(float(p.start_offset), 3),
                    "end_offset": round(float(p.end_offset), 3),
                    "intent": p.intent,
                }
                for p in self.phases
            ],
            "sfx_events": [
                {
                    "at_offset": round(float(e.at_offset), 3),
                    "name": e.name,
                    "reason": e.reason,
                }
                for e in self.sfx_events
            ],
            "broll_concepts": [c.to_dict() for c in self.broll_concepts],
            "music_decision": {
                "use_profile_music": bool(self.music_decision.use_profile_music),
                "reason": self.music_decision.reason,
            },
            "plan_confidence": round(float(self.plan_confidence), 3),
            "source": self.source,
        }

    @classmethod
    def from_dict(cls, raw: Any) -> "ClipPlan":
        """Rebuild a plan from a stored dict, without profile context.

        Lenient by design (a stored plan must always be readable): unknown
        enum values degrade to the neutral defaults instead of raising.
        Placement guards that need the profile/duration are NOT re-applied
        here — they ran at generation time in ``validate_plan_dict``.
        """
        raw = raw if isinstance(raw, dict) else {}
        hook_raw = raw.get("hook") if isinstance(raw.get("hook"), dict) else {}
        hook_type = hook_raw.get("type")
        opening_raw = (
            raw.get("opening_treatment") if isinstance(raw.get("opening_treatment"), dict) else {}
        )
        accent = opening_raw.get("sfx_accent")
        music_raw = (
            raw.get("music_decision") if isinstance(raw.get("music_decision"), dict) else {}
        )
        phases = []
        for entry in raw.get("phases") or []:
            if not isinstance(entry, dict) or entry.get("intent") not in PHASE_INTENTS:
                continue
            phases.append(
                Phase(
                    start_offset=_as_float(entry.get("start_offset")),
                    end_offset=_as_float(entry.get("end_offset")),
                    intent=entry["intent"],
                )
            )
        events = []
        for entry in raw.get("sfx_events") or []:
            if not isinstance(entry, dict) or entry.get("name") not in SFX_EVENT_NAMES:
                continue
            events.append(
                PlannedSfxEvent(
                    at_offset=_as_float(entry.get("at_offset")),
                    name=entry["name"],
                    reason=_as_reason(entry.get("reason")),
                )
            )
        transition_choices = []
        for entry in raw.get("transition_choices") or []:
            if not isinstance(entry, dict):
                continue
            name = entry.get("name")
            if not isinstance(name, str) or not name.strip():
                continue
            transition_choices.append(
                TransitionChoice(
                    name=name.strip().lower(),
                    at_offset=_as_float(entry.get("at_offset")),
                    reason=_as_reason(entry.get("reason")),
                )
            )
        concepts = []
        for entry in raw.get("broll_concepts") or []:
            if not isinstance(entry, dict):
                continue
            concept_text = entry.get("concept")
            if not isinstance(concept_text, str) or not concept_text.strip():
                continue
            queries = _clean_concept_queries(entry.get("search_queries"))
            if not queries:
                continue
            media = entry.get("media")
            concepts.append(
                BrollConcept(
                    anchor_sec=_as_float(entry.get("anchor_sec")),
                    spoken=_as_reason(entry.get("spoken")),
                    concept=concept_text.strip()[:_MAX_CONCEPT_CHARS],
                    search_queries=queries,
                    sense=_clean_sense_tag(entry.get("sense")),
                    media=media if media in BROLL_CONCEPT_MEDIA else "either",
                    avoid=_clean_concept_avoid(entry.get("avoid")),
                    duration_sec=_clean_concept_duration(entry.get("duration_sec")),
                )
            )
        caption_raw = raw.get("caption_style")
        caption_style = None
        if isinstance(caption_raw, dict):
            cap_name = caption_raw.get("name")
            if isinstance(cap_name, str) and cap_name.strip():
                caption_style = CaptionStyleChoice(
                    name=cap_name.strip(),
                    reason=_as_reason(caption_raw.get("reason")),
                )
        return cls(
            niche=str(raw.get("niche") or "podcast_news"),
            hook=HookDecision(
                type=hook_type if hook_type in HOOK_TYPES else "none_strong",
                timestamp=_as_float(hook_raw.get("timestamp")),
                confidence=_clamp01(hook_raw.get("confidence")),
                reason=_as_reason(hook_raw.get("reason")),
            ),
            opening_treatment=OpeningTreatment(
                punch_zoom=(
                    opening_raw.get("punch_zoom")
                    if opening_raw.get("punch_zoom") in PUNCH_ZOOM_LEVELS
                    else "none"
                ),
                first_caption_emphasis=bool(opening_raw.get("first_caption_emphasis")),
                sfx_accent=accent if accent in SFX_EVENT_NAMES else None,
                at_offset=_as_float(opening_raw.get("at_offset")),
                reason=_as_reason(opening_raw.get("reason")),
            ),
            transition_choices=transition_choices,
            caption_style=caption_style,
            phases=phases,
            sfx_events=events,
            broll_concepts=concepts,
            music_decision=MusicDecision(
                use_profile_music=bool(music_raw.get("use_profile_music", True)),
                reason=_as_reason(music_raw.get("reason")),
            ),
            plan_confidence=_clamp01(raw.get("plan_confidence")),
            source="llm" if raw.get("source") == "llm" else "fallback",
        )

    def hook_report(self) -> dict[str, Any]:
        """The Hook Report the clips API carries for this clip."""
        strategy = _opening_strategy_text(self.opening_treatment)
        for choice in self.transition_choices:
            if choice.name not in _CUT_TRANSITION_NAMES:
                strategy += f" + {choice.name} transition at {choice.at_offset:.0f}s"
                break
        return {
            "type": self.hook.type,
            "timestamp": round(float(self.hook.timestamp), 2),
            "why": self.hook.reason,
            "opening_strategy": strategy,
            "confidence": round(float(self.hook.confidence), 3),
        }


def _opening_strategy_text(treatment: OpeningTreatment) -> str:
    parts: list[str] = []
    if treatment.punch_zoom != "none":
        parts.append(f"{treatment.punch_zoom} punch zoom on the hook")
    if treatment.sfx_accent:
        parts.append(f"{treatment.sfx_accent} accent under the opening")
    if treatment.first_caption_emphasis:
        parts.append("first caption line emphasised")
    if not parts:
        return "Clean open — no added effects"
    return " + ".join(parts)


def plan_hook_report(plan_json: Optional[str]) -> Optional[dict[str, Any]]:
    """Hook Report for a stored plan JSON string, or None when unusable."""
    if not plan_json:
        return None
    try:
        raw = json.loads(plan_json)
    except (TypeError, ValueError):
        return None
    if not isinstance(raw, dict):
        return None
    return ClipPlan.from_dict(raw).hook_report()


def comment_question_for(plan_json: Optional[str]) -> Optional[str]:
    """The clip's comment-bait question from a stored plan JSON string.

    The question rides the plan JSON as an additive sibling key (like
    ``hook_application``/``retention``) when the clip was finished with
    COMMENT_BAIT on; None when the key is absent or unusable, so every
    reader sees exactly today's value for clips without one.
    """
    if not plan_json:
        return None
    try:
        raw = json.loads(plan_json)
    except (TypeError, ValueError):
        return None
    if not isinstance(raw, dict):
        return None
    question = raw.get("comment_question")
    if not isinstance(question, str):
        return None
    return question.strip() or None


# --- Validation / sanitising ----------------------------------------------


def _as_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _clamp01(value: Any) -> float:
    return max(0.0, min(1.0, _as_float(value)))


def _as_reason(value: Any) -> str:
    if not isinstance(value, str):
        return ""
    return value.strip()[:_MAX_REASON_CHARS]


def _profile_allows_sfx(profile: Optional[dict]) -> bool:
    audio = (profile or {}).get("audio", {})
    return bool(audio.get("sfx_from_user_library"))


def _profile_broll_concept_cap(profile: Optional[dict], duration: float) -> int:
    """How many B-roll concepts this profile allows on one clip.

    Mirrors the EDL planner's own arithmetic (``_create_broll_queries``
    gates + ``_plan_broll_segments``' target count) so validation and
    placement agree: B-roll off (disabled, source "off", or zero
    density) means no concepts at all; otherwise the density-derived
    count, at least one for a clip that allows B-roll at all.
    """
    broll = (profile or {}).get("broll", {})
    if not broll.get("enabled"):
        return 0
    if broll.get("source", "off") == "off":
        return 0
    try:
        density = float(broll.get("density_per_min", 0) or 0)
    except (TypeError, ValueError):
        density = 0.0
    if density <= 0 or duration <= 0:
        return 0
    # One cadence for validation, the planner prompt and placement
    # (``edl.broll_target_count``): v3 profiles use ``cadence_per_min``.
    return broll_target_count(profile or {}, duration)


def _clean_concept_queries(value: Any) -> list[str]:
    """Sanitise a concept's search phrases.

    A phrase must be a PHRASE: at least two words. A bare single word is
    the failure this slice exists to remove — a stock library reads a
    bare noun in its most common sense only ("market" → street
    markets) — so single-token entries are dropped here, mechanically,
    whatever the planner wrote. At most ``_MAX_CONCEPT_QUERIES``
    survive, duplicates removed, order kept.
    """
    if not isinstance(value, list):
        return []
    queries: list[str] = []
    seen: set[str] = set()
    for item in value:
        if not isinstance(item, str):
            continue
        query = " ".join(item.split())
        if len(re.findall(r"[a-z0-9]+", query.lower())) < 2:
            continue
        key = query.lower()
        if key in seen:
            continue
        seen.add(key)
        queries.append(query)
        if len(queries) >= _MAX_CONCEPT_QUERIES:
            break
    return queries


_SENSE_TAG_RE = re.compile(r"^([a-z][a-z0-9]*):([a-z][a-z0-9_]*)$")


def _clean_sense_tag(value: Any) -> Optional[str]:
    """A sense tag is "word:sense" ("market:financial"); else None."""
    if not isinstance(value, str):
        return None
    match = _SENSE_TAG_RE.match(value.strip().lower())
    if not match:
        return None
    return f"{match.group(1)}:{match.group(2)}"


def _clean_concept_avoid(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    avoid: list[str] = []
    for item in value:
        if not isinstance(item, str):
            continue
        text = " ".join(item.split())[:_MAX_AVOID_CHARS]
        if text and text not in avoid:
            avoid.append(text)
        if len(avoid) >= _MAX_CONCEPT_AVOID:
            break
    return avoid


# Room-sense avoid completion. Interior concepts are the vision veto's
# hardest pair type: two rooms both read as "a person in a room" until
# their signature furniture is named. The veto calibration expansion
# (100 judged pairs, VETO_CALIBRATION_EXPANSION.md §4) had exactly one
# missed insert — an apartment passing under a recording-studio cue
# whose avoid side barely registered — and its fix list asks for avoid
# lists that name the competing room's signature furniture. The planner
# prompt asks for the same completion; this table is the deterministic
# backstop, so the tag gate and the contrastive veto always have the
# competing room to bite on even when the planner's list is thin.
# Each row: (room markers matched against the concept sentence,
# competing rooms' signature items appended to the avoid list).
# Bare "studio" is deliberately NOT a marker: it names too many
# interiors (photo studio, dance studio, studio apartment) to pick a
# competing room honestly.
_ROOM_AVOID_COMPLETION: tuple[tuple[tuple[str, ...], tuple[str, ...]], ...] = (
    (
        ("recording studio", "podcast studio", "music studio", "home studio", "recording booth"),
        ("bed", "sofa", "kitchen counter", "stove"),
    ),
    (
        ("apartment", "living room", "bedroom"),
        ("mixing desk", "monitor speakers", "acoustic panels", "studio microphone"),
    ),
    (
        ("kitchen",),
        ("bed", "mixing desk", "acoustic panels"),
    ),
    (
        ("office",),
        ("bed", "sofa", "stove", "kitchen counter"),
    ),
)


def _complete_room_avoid(concept_text: str, avoid: list[str]) -> list[str]:
    """Complete a room concept's avoid list with competing-room furniture.

    Matches room markers against the CONCEPT sentence only (never the
    spoken line or the avoid list itself — a concept that avoids
    "office" is not an office concept). The completion only ever ADDS:
    what the planner wrote stays, in its order, duplicates are skipped
    case-insensitively, and the result is capped like any avoid list,
    so a full planner list is returned untouched. A concept that names
    no room comes back byte-identical.
    """
    text = concept_text.lower()
    completed = list(avoid)
    seen = {item.lower() for item in completed}
    for markers, competing in _ROOM_AVOID_COMPLETION:
        if not any(
            re.search(rf"\b{re.escape(marker)}\b", text) for marker in markers
        ):
            continue
        for item in competing:
            if item.lower() in seen:
                continue
            if len(completed) >= _MAX_CONCEPT_AVOID:
                return completed
            completed.append(item)
            seen.add(item.lower())
    return completed


def _clean_concept_duration(value: Any) -> Optional[float]:
    """A concept's cue-length hint, clamped to the 2.5–4.0 s band.

    None (or anything non-numeric) means no hint — the fixed 3.0 s
    default stands. A hint outside the band clamps to it rather than
    dropping the concept: the planner's length preference is advisory,
    never load-bearing.
    """
    if value is None:
        return None
    try:
        seconds = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(seconds) or seconds <= 0:
        return None
    return min(max(seconds, BROLL_CONCEPT_MIN_CUE_SEC), BROLL_CONCEPT_MAX_CUE_SEC)


def _validate_broll_concepts(
    raw: dict,
    *,
    duration: float,
    profile: Optional[dict],
    phases: list[Phase],
) -> list[BrollConcept]:
    """Validate the plan's B-roll concepts, dropping bad ones singly.

    A concept is an insert decision, so it validates like one: the
    profile must allow B-roll at all and caps the count at its density;
    the concept sentence must be present and its search phrases must be
    real phrases (never a bare word); a moment that turns on a
    sense-trap word must carry the resolved sense tag for THAT word;
    and no concept may anchor inside the plan's payoff or conclusion
    phases — the speaker's face owns those moments, so such a concept
    is dropped here rather than placed over the payoff line. Anything
    unrecognised drops the one concept, never the plan. A cue-length
    hint (``duration_sec``) is advisory: it is clamped to the 2.5–4.0 s
    band, and a missing or non-numeric hint leaves the fixed 3.0 s.
    A concept that names a room gets its avoid list completed with the
    competing rooms' signature furniture (``_complete_room_avoid``) —
    interiors are the veto's hardest pair type, and the completion
    only ever adds, never rewrites.
    """
    cap = _profile_broll_concept_cap(profile, duration)
    entries = raw.get("broll_concepts")
    if cap <= 0 or not isinstance(entries, list):
        return []
    blocked = [
        (p.start_offset, p.end_offset)
        for p in phases
        if p.intent in ("payoff", "conclusion")
    ]
    kept: list[BrollConcept] = []
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        concept_raw = entry.get("concept")
        if not isinstance(concept_raw, str) or not concept_raw.strip():
            continue
        concept_text = concept_raw.strip()[:_MAX_CONCEPT_CHARS]
        queries = _clean_concept_queries(entry.get("search_queries"))
        if not queries:
            logger.info(
                "Clip plan: B-roll concept %r dropped — no usable search "
                "phrases (a bare word is not a phrase)",
                concept_text[:60],
            )
            continue
        anchor = max(0.0, min(_as_float(entry.get("anchor_sec")), duration))
        if any(start <= anchor < end for start, end in blocked):
            logger.info(
                "Clip plan: B-roll concept at %.2fs dropped — anchored in "
                "the payoff/conclusion, where the speaker's face stays",
                anchor,
            )
            continue
        sense = _clean_sense_tag(entry.get("sense"))
        spoken = _as_reason(entry.get("spoken"))
        trap_hits = BROLL_SENSE_TRAP_WORDS.intersection(
            re.findall(r"[a-z0-9]+", f"{spoken} {concept_text}".lower())
        )
        if trap_hits and (sense is None or sense.split(":", 1)[0] not in trap_hits):
            logger.info(
                "Clip plan: B-roll concept at %.2fs dropped — its moment "
                "turns on sense-trap word(s) %s but carries no resolved "
                "sense tag",
                anchor,
                sorted(trap_hits),
            )
            continue
        media = entry.get("media")
        kept.append(
            BrollConcept(
                anchor_sec=anchor,
                spoken=spoken,
                concept=concept_text,
                search_queries=queries,
                sense=sense,
                media=media if media in BROLL_CONCEPT_MEDIA else "either",
                avoid=_complete_room_avoid(
                    concept_text, _clean_concept_avoid(entry.get("avoid"))
                ),
                duration_sec=_clean_concept_duration(entry.get("duration_sec")),
            )
        )
    kept.sort(key=lambda c: c.anchor_sec)
    deduped: list[BrollConcept] = []
    for concept in kept:
        if deduped and concept.anchor_sec - deduped[-1].anchor_sec < _CONCEPT_DEDUPE_GAP_SEC:
            continue
        deduped.append(concept)
    if len(deduped) > cap:
        logger.info(
            "Clip plan: %d B-roll concept(s) dropped — the profile's "
            "density allows %d per clip",
            len(deduped) - cap,
            cap,
        )
        deduped = deduped[:cap]
    return deduped


def sanitize_sfx_events(
    events: list[PlannedSfxEvent],
    *,
    duration: float,
    profile: Optional[dict],
    max_events: Optional[int] = None,
) -> list[PlannedSfxEvent]:
    """Apply the profile gate and the placement/density discipline.

    Rules, in order: the profile's ``sfx_from_user_library`` gate decides
    whether planned accents may exist at all; events must sit at least
    ``SFX_EDGE_GUARD_SEC`` inside the clip; events closer than
    ``SFX_MIN_GAP_SEC`` collapse to the one with the strongest (longest,
    most specific) reason; and at most ``max_events`` survive — the
    global ``PLAN_SFX_MAX_EVENTS`` unless the profile's scene rules set
    a tighter per-niche cap (comedy restraint).
    """
    if max_events is None:
        max_events = plan_sfx_cap(profile, PLAN_SFX_MAX_EVENTS)
    if duration <= 0 or not _profile_allows_sfx(profile):
        return []
    usable = [
        e
        for e in events
        if SFX_EDGE_GUARD_SEC <= e.at_offset <= duration - SFX_EDGE_GUARD_SEC
    ]
    usable.sort(key=lambda e: e.at_offset)
    # Cluster events that sit closer than the minimum gap; per cluster the
    # longest reason wins (ties keep the earliest event).
    kept: list[PlannedSfxEvent] = []
    for event in usable:
        if kept and event.at_offset - kept[-1].at_offset < SFX_MIN_GAP_SEC:
            if len(event.reason) > len(kept[-1].reason):
                kept[-1] = event
            continue
        kept.append(event)
    if len(kept) > max_events:
        strongest = sorted(kept, key=lambda e: len(e.reason), reverse=True)[:max_events]
        kept = sorted(strongest, key=lambda e: e.at_offset)
    return kept


def _sanitize_phases(raw_phases: Any, duration: float) -> list[Phase]:
    phases: list[Phase] = []
    if isinstance(raw_phases, list):
        for entry in raw_phases:
            if not isinstance(entry, dict) or entry.get("intent") not in PHASE_INTENTS:
                continue
            start = max(0.0, min(_as_float(entry.get("start_offset")), duration))
            end = max(0.0, min(_as_float(entry.get("end_offset")), duration))
            if end - start < 0.1:
                continue
            phases.append(Phase(start_offset=start, end_offset=end, intent=entry["intent"]))
    phases.sort(key=lambda p: p.start_offset)
    return phases[:_MAX_PHASES]


def validate_plan_dict(
    raw: Any,
    *,
    niche: str,
    duration: float,
    profile: Optional[dict],
    pauses: Optional[list] = None,
) -> ClipPlan:
    """Validate untrusted planner output into a ClipPlan.

    The niche always comes from the caller (the classifier/profile resolver
    owns it), never from the planner output. Anything unrecognised degrades
    to the neutral value for that field instead of failing — a plan with a
    dropped accent is still a plan; a crashed planner must never cost a
    render. Raises ValueError only when ``raw`` is not a dict at all.
    """
    if not isinstance(raw, dict):
        raise ValueError("Plan output must be a JSON object")
    duration = max(0.0, float(duration or 0.0))

    hook_raw = raw.get("hook") if isinstance(raw.get("hook"), dict) else {}
    hook_type = hook_raw.get("type")
    hook = HookDecision(
        type=hook_type if hook_type in HOOK_TYPES else "none_strong",
        timestamp=max(0.0, min(_as_float(hook_raw.get("timestamp")), duration)),
        confidence=_clamp01(hook_raw.get("confidence")),
        reason=_as_reason(hook_raw.get("reason")),
    )

    opening_raw = (
        raw.get("opening_treatment") if isinstance(raw.get("opening_treatment"), dict) else {}
    )
    accent = opening_raw.get("sfx_accent")
    if not _profile_allows_sfx(profile):
        accent = None
    if accent not in SFX_EVENT_NAMES:
        accent = None
    punch = (
        opening_raw.get("punch_zoom")
        if opening_raw.get("punch_zoom") in PUNCH_ZOOM_LEVELS
        else "none"
    )
    opening_reason = _as_reason(opening_raw.get("reason"))
    # "No reason, no effect": a punch zoom or an opening accent is an
    # effect decision — without a reason naming the moment, both drop
    # back to the profile's own behaviour.
    if (punch != "none" or accent is not None) and not _reason_ok(opening_reason):
        logger.info(
            "Clip plan: opening treatment dropped (punch=%s, accent=%s) — "
            "no content reason given",
            punch,
            accent,
        )
        punch = "none"
        accent = None
    # Scene-rule punch ceiling (comedy restraint): a strong punch above
    # the niche's cap is downgraded, not dropped — unless the hook is a
    # type the profile says has earned the hard punch (a punchline).
    rules = scene_rules(profile)
    capped = capped_punch_zoom(
        punch,
        cap=rules.get("plan_punch_zoom_cap"),
        hook_type=hook.type,
        allowed_hook_types=rules.get("strong_punch_allowed_hook_types"),
    )
    if capped != punch:
        logger.info(
            "Clip plan: opening punch %s downgraded to %s — niche punch "
            "ceiling (hook type %s has not earned a strong punch)",
            punch,
            capped,
            hook.type,
        )
        punch = capped
    opening = OpeningTreatment(
        punch_zoom=punch,
        first_caption_emphasis=bool(opening_raw.get("first_caption_emphasis")),
        sfx_accent=accent,
        at_offset=max(
            0.0,
            min(
                _as_float(opening_raw.get("at_offset"), hook.timestamp),
                duration,
            ),
        ),
        reason=opening_reason,
    )

    transition_choices = _validate_transition_choices(raw, duration=duration, profile=profile)
    caption_style = _validate_caption_style(raw, profile=profile)

    # Scene-rule pause protection (comedy timing): an accent that lands
    # on a measured pause — or in the breath just before it — steps on
    # the timing the pause is holding, however good its reason sounds.
    pause_protection = float(rules.get("pause_protection_sec") or 0.0)
    raw_events: list[PlannedSfxEvent] = []
    if isinstance(raw.get("sfx_events"), list):
        for entry in raw["sfx_events"]:
            if not isinstance(entry, dict) or entry.get("name") not in SFX_EVENT_NAMES:
                continue
            reason = _as_reason(entry.get("reason"))
            if not _reason_ok(reason):
                logger.info(
                    "Clip plan: SFX event %s at %.2fs dropped — no content reason given",
                    entry.get("name"),
                    _as_float(entry.get("at_offset")),
                )
                continue
            if pause_protection and inside_protected_pause(
                _as_float(entry.get("at_offset")), pauses, pause_protection
            ):
                logger.info(
                    "Clip plan: SFX event %s at %.2fs dropped — lands on a "
                    "protected pause (niche pause protection)",
                    entry.get("name"),
                    _as_float(entry.get("at_offset")),
                )
                continue
            raw_events.append(
                PlannedSfxEvent(
                    at_offset=_as_float(entry.get("at_offset")),
                    name=entry["name"],
                    reason=reason,
                )
            )
    events = sanitize_sfx_events(raw_events, duration=duration, profile=profile)

    music_raw = raw.get("music_decision") if isinstance(raw.get("music_decision"), dict) else {}
    music_reason = _as_reason(music_raw.get("reason"))
    use_profile_music = bool(music_raw.get("use_profile_music", True))
    if not use_profile_music and not _reason_ok(music_reason):
        logger.info(
            "Clip plan: music-off decision dropped — no content reason given"
        )
        use_profile_music = True
    music = MusicDecision(
        use_profile_music=use_profile_music,
        reason=music_reason,
    )

    phases = _sanitize_phases(raw.get("phases"), duration)
    concepts = _validate_broll_concepts(
        raw, duration=duration, profile=profile, phases=phases
    )

    return ClipPlan(
        niche=niche,
        hook=hook,
        opening_treatment=opening,
        transition_choices=transition_choices,
        caption_style=caption_style,
        phases=phases,
        sfx_events=events,
        broll_concepts=concepts,
        music_decision=music,
        plan_confidence=_clamp01(raw.get("plan_confidence")),
        source="llm",
    )


def _validate_transition_choices(
    raw: dict, *, duration: float, profile: Optional[dict]
) -> list[TransitionChoice]:
    """Validate per-clip transition decisions against the renderer's map.

    A name outside the renderer's transition map is dropped (logged). A
    choice that deviates from the profile default is an effect decision:
    it needs a real reason ("no reason, no effect") and counts against
    the restraint budget — at most ``MAX_NON_DEFAULT_TRANSITIONS``
    survive, earliest first.
    """
    entries: list[Any] = []
    if isinstance(raw.get("transition_choices"), list):
        entries.extend(raw["transition_choices"])
    if isinstance(raw.get("transition_choice"), dict):
        entries.append(raw["transition_choice"])
    valid_names = _valid_transition_names()
    default_name = _profile_default_transition(profile)
    kept: list[TransitionChoice] = []
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        name = entry.get("name")
        if not isinstance(name, str):
            continue
        name = name.strip().lower()
        if name not in valid_names:
            logger.info(
                "Clip plan: transition %r dropped — not in the renderer transition map",
                name,
            )
            continue
        reason = _as_reason(entry.get("reason"))
        if name != default_name and not _reason_ok(reason):
            logger.info(
                "Clip plan: transition %s at %.2fs dropped — no content reason given",
                name,
                _as_float(entry.get("at_offset")),
            )
            continue
        kept.append(
            TransitionChoice(
                name=name,
                at_offset=max(0.0, min(_as_float(entry.get("at_offset")), duration)),
                reason=reason,
            )
        )
    # The restraint budget is the global cap unless the profile's scene
    # rules tighten it for this niche (comedy: one earned transition).
    budget = plan_transition_cap(profile, MAX_NON_DEFAULT_TRANSITIONS)
    non_default = sorted(
        (c for c in kept if c.name != default_name), key=lambda c: c.at_offset
    )
    if len(non_default) > budget:
        dropped = non_default[budget:]
        logger.info(
            "Clip plan: %d transition choice(s) dropped — restraint budget is %d "
            "non-default transitions per clip",
            len(dropped),
            budget,
        )
        non_default = non_default[:budget]
    defaults = [c for c in kept if c.name == default_name]
    return sorted(defaults + non_default, key=lambda c: c.at_offset)


def _validate_caption_style(
    raw: dict, *, profile: Optional[dict]
) -> Optional[CaptionStyleChoice]:
    """Validate the per-clip caption style against the template registry.

    An unknown template name drops to the profile default (logged). A
    style that differs from the profile default is an effect decision
    and needs a real reason.
    """
    caption_raw = raw.get("caption_style")
    if isinstance(caption_raw, str):
        caption_raw = {"name": caption_raw}
    if not isinstance(caption_raw, dict):
        return None
    name = caption_raw.get("name")
    if not isinstance(name, str) or not name.strip():
        return None
    name = name.strip()
    if name not in _valid_caption_names():
        logger.info(
            "Clip plan: caption style %r dropped — not a caption template; "
            "profile default stands",
            name,
        )
        return None
    reason = _as_reason(caption_raw.get("reason"))
    if name != _profile_default_caption(profile) and not _reason_ok(reason):
        logger.info(
            "Clip plan: caption style %s dropped — no content reason given", name
        )
        return None
    return CaptionStyleChoice(name=name, reason=reason)


# --- Deterministic fallback (today's behaviour) ----------------------------


def _default_phases(duration: float) -> list[Phase]:
    if duration <= 0:
        return []
    if duration < 4.0:
        return [Phase(0.0, duration, "hook")]
    hook_end = min(3.0, duration * 0.25)
    return [
        Phase(0.0, round(hook_end, 3), "hook"),
        Phase(round(hook_end, 3), round(duration * 0.6, 3), "context"),
        Phase(round(duration * 0.6, 3), round(duration * 0.85, 3), "payoff"),
        Phase(round(duration * 0.85, 3), round(duration, 3), "conclusion"),
    ]


def minimal_plan(niche: str, profile: Optional[dict], duration: float) -> ClipPlan:
    """The deterministic plan used whenever the planner cannot run.

    It records exactly what the pipeline already does without a plan: the
    profile drives the edit, no planned SFX accents are added (the EDL
    emitter's own events are untouched), and the profile's music policy
    stands. A clip rendered with this plan is a clip rendered as before.
    """
    profile = profile or {}
    zooms = profile.get("zooms", {})
    punch = "subtle" if _as_float(zooms.get("punch_in_scale"), 1.0) > 1.0 else "none"
    return ClipPlan(
        niche=niche,
        hook=HookDecision(
            type="none_strong",
            timestamp=0.0,
            confidence=0.0,
            reason="No per-clip plan was generated; the niche profile's defaults were applied.",
        ),
        opening_treatment=OpeningTreatment(punch_zoom=punch),
        phases=_default_phases(max(0.0, float(duration or 0.0))),
        sfx_events=[],
        music_decision=MusicDecision(
            use_profile_music=True,
            reason="Profile music policy unchanged.",
        ),
        plan_confidence=0.0,
        source="fallback",
    )


# --- EDL merge --------------------------------------------------------------


def merge_plan_sfx_events(
    edl_events: Optional[list[dict]],
    plan_events: Optional[list[PlannedSfxEvent]],
) -> list[dict]:
    """Merge planned SFX accents into an EDL's ``sfx_events``.

    EDL-native events always win: a planned event within
    ``MERGE_DEDUPE_WINDOW_SEC`` of a native event (or of an already-kept
    planned event) is dropped, never stacked. Kept planned events take the
    EDL event shape ``{name, at_sec, gain_db}`` with the emitter's own gain
    for that name. Inputs are not mutated.
    """
    merged: list[dict] = [dict(e) for e in (edl_events or [])]
    occupied = [_as_float(e.get("at_sec")) for e in merged if isinstance(e, dict)]
    for event in sorted(plan_events or [], key=lambda e: e.at_offset):
        if any(abs(event.at_offset - taken) <= MERGE_DEDUPE_WINDOW_SEC for taken in occupied):
            continue
        merged.append(
            {
                "name": event.name,
                "at_sec": round(float(event.at_offset), 3),
                "gain_db": PLAN_SFX_GAIN_DB.get(event.name, -3.0),
            }
        )
        occupied.append(event.at_offset)
    merged.sort(key=lambda e: _as_float(e.get("at_sec")))
    return merged


# --- EDL consumers: transitions + opening punch -----------------------------
#
# These fold a *validated* plan into the EDL the renderer consumes. They
# only ever run for planner-produced plans (source == "llm") — the
# deterministic fallback plan describes today's behaviour and must never
# alter it. Both are pure dict manipulation on the EDL's existing fields
# (segment transition_in / transition_duration, zoom_keyframes), reusing
# the profile/render machinery exactly as build_edl shaped it.


def apply_plan_transitions(
    edl: dict, plan: ClipPlan, profile: Optional[dict]
) -> int:
    """Apply the plan's transition choices to the EDL's internal joins.

    Each choice targets the join (segment boundary) nearest its offset;
    a join takes at most one choice. A crossfade choice on a profile
    whose joins are cut-length gets a visible duration (the profile's
    own transition duration when it is a real crossfade length, else
    ``PLAN_TRANSITION_DEFAULT_DURATION_SEC``); the renderer clamps the
    fade to the neighbouring segment lengths. Returns how many joins
    were changed. A clip with fewer than two segments has no internal
    joins — the choices stay on the plan record, unapplied.
    """
    segments = edl.get("segments") or []
    if len(segments) < 2 or not plan.transition_choices:
        return 0
    boundaries = [
        (index, _as_float(segments[index].get("start")))
        for index in range(1, len(segments))
    ]
    applied = 0
    used: set[int] = set()
    for choice in plan.transition_choices:
        candidates = [
            (abs(boundary - choice.at_offset), index)
            for index, boundary in boundaries
            if index not in used
        ]
        if not candidates:
            break
        _, target = min(candidates)
        used.add(target)
        segment = segments[target]
        segment["transition_in"] = choice.name
        if choice.name in _CUT_TRANSITION_NAMES:
            segment["transition_duration"] = 0.0
        else:
            duration = _profile_transition_duration(profile)
            if duration <= 0.1:
                duration = PLAN_TRANSITION_DEFAULT_DURATION_SEC
            segment["transition_duration"] = duration
        applied += 1
    return applied


def apply_plan_opening_punch(
    edl: dict, plan: ClipPlan, profile: Optional[dict]
) -> bool:
    """Apply the plan's opening punch zoom to the EDL's zoom keyframes.

    Reuses the profile's punch-in machinery: the keyframe shape is the
    one ``_create_zoom_keyframes`` emits ({type punch_in, trigger, time,
    scale, duration}) and the scale is anchored on the profile's own
    punch scale — "subtle" stays at or below 1.25, "strong" is at least
    1.30 — so the plan intensifies the niche's grammar instead of
    inventing a new one. An existing punch keyframe within 0.3s of the
    hook moment is re-scaled rather than duplicated. Returns True when a
    keyframe was inserted or re-scaled.
    """
    treatment = plan.opening_treatment
    if treatment.punch_zoom == "none":
        return False
    duration = _as_float(edl.get("duration"))
    at = treatment.at_offset or plan.hook.timestamp
    at = max(0.0, min(at, max(0.0, duration - 0.6))) if duration > 0 else max(0.0, at)
    zooms = (profile or {}).get("zooms", {})
    base = _as_float(zooms.get("punch_in_scale"), 1.0)
    if treatment.punch_zoom == "subtle":
        scale = min(max(base, 1.15), 1.25)
    else:  # strong
        scale = min(max(base, 1.30), 1.50)
    keyframes = edl.setdefault("zoom_keyframes", [])
    for keyframe in keyframes:
        # The EDL builder's opening ramp (a ken-burns push-in stamped
        # trigger "opening_punch") already treats this moment: the
        # plan's punch re-scales the ramp instead of stacking a step
        # zoom on top of it.
        if (
            keyframe.get("type") == "ken_burns"
            and keyframe.get("trigger") == "opening_punch"
            and abs(_as_float(keyframe.get("start")) - at) <= 0.3
        ):
            keyframe["scale_end"] = round(scale, 3)
            return True
        if keyframe.get("type") != "punch_in":
            continue
        if abs(_as_float(keyframe.get("time")) - at) <= 0.3:
            keyframe["scale"] = round(scale, 3)
            keyframe["duration"] = max(_as_float(keyframe.get("duration"), 0.5), 0.5)
            return True
    keyframes.append(
        {
            "type": "punch_in",
            "trigger": "hook",
            "time": round(at, 3),
            "scale": round(scale, 3),
            "duration": 0.5,
        }
    )
    keyframes.sort(key=lambda k: _as_float(k.get("time", k.get("start"))))
    return True


# --- EDL consumer: B-roll concepts ------------------------------------------


def apply_plan_broll_concepts(
    edl: dict,
    plan: ClipPlan,
    profile: Optional[dict],
    transcript_words: Optional[list[dict]],
) -> Optional[int]:
    """Replace the EDL's keyword-planned B-roll cues with the plan's concepts.

    Smart B-Roll v2's core rule: when the planner wrote concepts for
    this clip, the concepts are the cue source — the deterministic
    keyword scan's cues are replaced wholesale, and the stock search
    will run on the concepts' own phrases, never on a bare transcript
    word. Placement reuses the EDL planner's own machinery and guards
    (hook/end guards incl. the profile's establishing guard, punch-in
    and info-card blocks, the coverage cap), with the concept-only
    additions from the design: anchors snap to the nearest spoken word
    and at least 4s of speaker sits between two concept cues.

    Returns the number of concept cues placed, or None when the
    consumer does not apply — a fallback (record-only) plan, a plan
    without concepts, or no transcript words to anchor against — in
    which case ``edl`` is left exactly as built: the keyword-planned
    cues ARE today's behaviour, and a plan without concepts must
    produce a byte-identical EDL. Note the asymmetry by design: a plan
    WITH concepts whose cues all fail placement leaves the clip with
    no B-roll rather than resurrecting keyword cues the planner has
    superseded — no insert beats a wrong insert.
    """
    if plan.source != "llm" or not plan.broll_concepts or not transcript_words:
        return None
    duration = _as_float(edl.get("duration"))
    if duration <= 0:
        return None
    concepts = [concept.to_dict() for concept in plan.broll_concepts]
    # Smart B-Roll v3 top-up: when the plan wrote fewer concepts than the
    # clip's cadence allows, phrase-level lexicon concepts fill the gap —
    # AFTER the plan's own concepts (the planner places in order, so the
    # plan keeps priority) and never within 5 s of a plan anchor.
    if broll_uses_v3(profile or {}):
        target = broll_target_count(profile or {}, duration)
        if len(concepts) < target:
            from .broll_lexicon import lexicon_concepts

            concepts = concepts + lexicon_concepts(
                profile or {}, transcript_words, duration,
                limit=3 * (target - len(concepts)),
                exclude_near=tuple(float(c.get("anchor_sec", 0) or 0) for c in concepts),
            )
    dropped: list[dict] = []
    placements = _plan_broll_segments(
        profile or {},
        transcript_words,
        duration,
        edl.get("segments") or [],
        edl.get("zoom_keyframes") or [],
        edl.get("overlays") or [],
        hook_guard_sec=max(
            BROLL_HOOK_GUARD_SEC, establishing_guard_sec(profile, duration)
        ),
        concepts=concepts,
        dropped_sink=dropped,
        # The plan's phases, so the planner's payoff/conclusion guard
        # applies at placement too: a cue whose window RUNS INTO the
        # payoff is dropped even when its anchor sits before it
        # (validation only drops anchors inside the phases).
        phases=[
            {
                "start_offset": p.start_offset,
                "end_offset": p.end_offset,
                "intent": p.intent,
            }
            for p in plan.phases
        ],
    )
    edl["broll"] = placements
    # The concepts supersede the keyword plan wholesale, so their
    # placement record supersedes its drop record too: what the record
    # shows is why THESE cues are missing, not a stale keyword's.
    if dropped:
        edl["broll_dropped"] = dropped
    else:
        edl.pop("broll_dropped", None)
    return len(placements)
