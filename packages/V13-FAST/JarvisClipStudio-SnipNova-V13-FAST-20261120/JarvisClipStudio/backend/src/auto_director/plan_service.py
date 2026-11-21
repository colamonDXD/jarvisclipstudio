"""Viral Intelligence Engine v1: per-clip plan generation.

For each selected segment, one LLM call (through the app's existing LLM
configuration — the same model plumbing the niche classifier uses) reads
the segment's transcript and word timings, the resolved niche profile, and
cheap deterministic audio features, and returns a :class:`ClipPlan`: the
hook call, the opening treatment, the phase map, planned SFX accents and
the music call, each with its reason.

HARD FAIL-SAFE: plan generation must never fail or stall a render. Any
error — LLM down, timeout, invalid JSON, an unknown enum or SFX name —
degrades to the deterministic minimal plan (the profile's own behaviour,
i.e. exactly what the pipeline did before this engine existed) and the
failure is logged. ``generate_clip_plan`` therefore never raises.

The kill switch is the ``VIRAL_PLAN_ENABLED`` env var: set it to ``false``
(or 0/no/off) and the pipeline skips planning entirely, producing EDLs
byte-identical to the pre-engine behaviour.
"""

from __future__ import annotations

import asyncio
import logging
import os
from pathlib import Path
from typing import Any, Optional

from pydantic import BaseModel, Field
from pydantic_ai import Agent
from pydantic_ai.output import NativeOutput

from ..config import get_config
from .clip_plan import (
    ClipPlan,
    _profile_broll_concept_cap,
    minimal_plan,
    validate_plan_dict,
)
from .edl import SFX_EVENT_NAMES

logger = logging.getLogger(__name__)

# Audio-feature extraction mirrors gap_activity's windowing (50ms windows).
_AUDIO_WINDOW_SECONDS = 0.05
_PEAK_MIN_GAP_SEC = 1.5
_PEAK_MAX_COUNT = 8
_PEAK_BELOW_SPEECH_DB = 3.0
_PEAK_FLOOR_DB = -40.0
_PAUSE_DROP_DB = 18.0
_PAUSE_MIN_SECONDS = 0.7
_PAUSE_MAX_COUNT = 10


def viral_plan_enabled() -> bool:
    """VIRAL_PLAN_ENABLED=false (or 0/no/off) disables planning entirely."""
    return os.environ.get("VIRAL_PLAN_ENABLED", "").strip().lower() not in {
        "0",
        "false",
        "no",
        "off",
    }


def _plan_timeout_sec() -> float:
    try:
        value = float(os.environ.get("VIRAL_PLAN_TIMEOUT_SEC", "30"))
    except (TypeError, ValueError):
        value = 30.0
    return max(5.0, min(120.0, value))


# --- Planner output draft (structured-output shape for the LLM call) -------
#
# Deliberately permissive (plain strings, no Literal enums): the single
# authoritative gate for untrusted planner output is
# clip_plan.validate_plan_dict, which is unit-testable without an LLM.
# The draft only pins the JSON *shape* so the model returns parseable
# structure; every value is re-checked after the call.


class _PhaseDraft(BaseModel):
    start_offset: float = Field(description="Phase start, seconds from clip start")
    end_offset: float = Field(description="Phase end, seconds from clip start")
    intent: str = Field(description="One of: hook, context, build, payoff, reaction, conclusion, loop_point")


class _SfxDraft(BaseModel):
    at_offset: float = Field(description="When the accent lands, seconds from clip start")
    name: str = Field(description="Canonical SFX name")
    reason: str = Field(default="", description="Why this accent belongs here, one short phrase")


class _TransitionDraft(BaseModel):
    name: str = Field(description="Transition name from the renderer vocabulary")
    at_offset: float = Field(description="Clip time of the join this choice targets, seconds from clip start")
    reason: str = Field(default="", description="The content moment at that join that justifies this transition")


class _BrollConceptDraft(BaseModel):
    """One B-roll concept (Smart B-Roll v2): the visual the planner
    writes for one spoken moment BEFORE anything searches stock."""

    anchor_sec: float = Field(description="Word timing where the illustrated line is spoken, seconds from clip start")
    spoken: str = Field(default="", description="The exact clause being illustrated")
    concept: str = Field(description="ONE filmable visual sentence — something a camera can point at")
    search_queries: list[str] = Field(
        default_factory=list,
        max_length=3,
        description="2-3 concrete multi-word stock search phrases derived from the concept; never a bare single word",
    )
    sense: Optional[str] = Field(
        default=None,
        description='Resolved sense tag "word:sense" (e.g. "market:financial") when the moment turns on a sense-trap word, else null',
    )
    media: str = Field(default="either", description="video, photo or either")
    avoid: list[str] = Field(
        default_factory=list,
        max_length=6,
        description="Wrong-sense subjects the footage must NOT show (e.g. street, crowd, farmers for a trading 'market'); for a room concept, the competing rooms' signature furniture (e.g. bed, sofa, kitchen counter for a recording studio)",
    )
    duration_sec: Optional[float] = Field(
        default=None,
        description="Optional cue length in seconds, 2.5-4.0 — only when the moment genuinely needs longer or shorter than the default 3s (a slow chart pan vs a quick product flash); omit it otherwise",
    )


class ClipPlanDraft(BaseModel):
    """Structured output for the per-clip planner."""

    hook_type: str = Field(description="The clip's hook type from the taxonomy")
    hook_timestamp: float = Field(
        default=0.0, description="Seconds from clip start where the hook moment begins"
    )
    hook_confidence: float = Field(description="Confidence in the hook call, 0.0 to 1.0", ge=0.0, le=1.0)
    hook_reason: str = Field(description="One plain sentence: why this is the hook")
    punch_zoom: str = Field(default="none", description="Opening punch zoom: none, subtle or strong")
    first_caption_emphasis: bool = Field(default=False, description="Emphasise the first caption line")
    sfx_accent: Optional[str] = Field(default=None, description="Canonical SFX name under the opening, or null")
    opening_at_offset: float = Field(default=0.0, description="Where the opening treatment lands, seconds from clip start (normally the hook timestamp)")
    opening_reason: str = Field(default="", description="The content moment that justifies the punch zoom / opening accent; required when either is set")
    transition_choices: list[_TransitionDraft] = Field(default_factory=list, max_length=6)
    caption_style_name: Optional[str] = Field(default=None, description="Caption template name when this clip genuinely suits a different style than the profile's, else null")
    caption_style_reason: str = Field(default="", description="Why this clip's content suits that caption style; required when a style is chosen")
    phases: list[_PhaseDraft] = Field(default_factory=list, max_length=8)
    sfx_events: list[_SfxDraft] = Field(default_factory=list, max_length=8)
    broll_concepts: list[_BrollConceptDraft] = Field(default_factory=list, max_length=8)
    use_profile_music: bool = Field(default=True, description="Should the profile's music play under this clip")
    music_reason: str = Field(default="", description="One short reason for the music call")
    plan_confidence: float = Field(default=0.0, description="Overall confidence in the plan, 0.0 to 1.0", ge=0.0, le=1.0)


PLAN_SYSTEM_PROMPT = """You are the viral editing planner for a short-form video clipper.

You receive ONE already-selected clip segment: its niche, its editing profile, its transcript with word timings (seconds from the clip's start), audio features (energy peaks and pauses), and its selection scores. Decide how THIS clip should be edited and return the plan as JSON only — no markdown, no extra text.

HOOK: pick the single strongest hook in the segment and its type, one of:
strong_statement, question, punchline, surprising_visual, controversial, emotional_reaction, reveal, sound_led, visual_transition, gameplay_moment, action_moment, before_after, none_strong.
- hook_timestamp is where that moment STARTS in the clip (use the word timings); it is often 0.0, but name the real moment when the opening seconds are weaker than a later line.
- hook_reason is ONE plain sentence a human editor would say out loud.
- If nothing in the segment is a genuine hook, say none_strong — do not invent one.

OPENING TREATMENT: punch_zoom is none, subtle or strong (strong only for a real punchline/reveal in a niche whose profile punches hard). first_caption_emphasis marks the first caption line for emphasis. sfx_accent is one canonical SFX name under the opening, or null. opening_at_offset is where the treatment lands (normally the hook timestamp). opening_reason must name the exact moment that justifies the zoom or accent — if there is no such moment, punch_zoom is none and sfx_accent is null.

TRANSITIONS: default to the profile's transition — return an empty transition_choices list unless a specific internal join in THIS clip earns a different one (a scene change, a topic shift, a reveal landing). Names must come from the renderer vocabulary: fade, dissolve, cross_dissolve, fadeblack, fadewhite, fadeslow, fadefast, fadegrays, smoothleft, smoothright, smoothup, smoothdown, slideleft, slideright, slideup, slidedown, slide, wipeleft, wiperight, wipeup, wipedown, wipe, circleopen, circleclose, radial, zoomin, pixelize, hblur, rectcrop, circlecrop, distance, squeezeh, squeezev, horzopen, horzclose, vertopen, vertclose, coverleft, coverright, coverup, coverdown, revealleft, revealright, revealup, revealdown, diagtl, diagtr, diagbl, diagbr, hlslice, hrslice, vdslice, vuslice, hlwind, hrwind, vdwind, vuwind, wipetl, wipetr, wipebl, wipebr. At most 2 non-default transitions per clip. Each choice needs at_offset (the join's time — a phase boundary or a word timing near the join) and a reason naming what changes at that join.

CAPTIONS: caption_style_name is one of: default, hormozi, mrbeast, minimal, tiktok, neon, classic_box, podcast. Leave it null unless this clip's content genuinely suits a different style than the profile's preset (a joke-dense clip under a formal profile, a quiet confessional under a loud one). caption_style_reason must name the content that suits the style.

PHASES: cover the whole clip with phases whose intent is one of: hook, context, build, payoff, reaction, conclusion, loop_point. Keep them in order, no gaps.

SFX EVENTS: canonical names ONLY: whoosh, pop, riser, ding, impact, sting. Accents punctuate — at most 4, never within 0.35s of the clip's start or end, never closer than 0.8s to each other, never on top of important dialogue (place them on pauses, peaks and reveals). Each needs a short reason. If the profile says SFX are off for this niche, return an empty list. A sting is for true-crime-style reveals only.

MUSIC: use_profile_music says whether the profile's music should play under THIS clip. Keep it true unless the moment itself argues against the profile's music (a sombre or intimate moment under an upbeat profile, a music-performance clip whose own audio is the content). Give one short reason either way.

STOCK B-ROLL CONCEPTS (broll_concepts): ONLY when the request below says stock B-roll is enabled for this profile — otherwise return an empty list. You are writing the visual BEFORE anything searches a stock library: nothing ever searches a bare transcript word. For each moment where a cutaway genuinely helps the viewer SEE what is being talked about, return ONE concept:
- anchor_sec: the word timing where the illustrated line is spoken. Prefer anchors at or after 5s. NEVER anchor on the payoff or the conclusion — the speaker's face owns those moments, and the validator drops concepts anchored there.
- spoken: the exact clause being illustrated.
- concept: ONE filmable visual sentence — something a camera can point at ("a trader's hands over a glowing stock chart on multiple monitors"). Abstract lines get NO concept: probabilities, odds, feelings, opinions and arguments cannot be filmed — leave them on the speaker's face rather than forcing a cutaway. A moment with nothing filmable gets no concept at all. Build the sentence around its DEFINING SUBJECT — a person doing the thing, or the object in use ("a gamer wearing headphones playing at a desk", "a finance chart on a screen") — never as a checklist of scenery or decoration: colours, water colour, weather, time of day, camera angle, a chart's colour or type. Footage that shows the right subject in different decoration is still the right footage: "a harbour with boats along a coast" fits a fishing harbour and a cargo port alike, while "a sandy bay with turquoise water" rejects a city harbour at night. When the moment is inherently a PLACE, anchor the sentence on the subject the place is recognised by (boats in a harbour, water pouring over rocks at a waterfall) and drop the decoration.
- search_queries: 2-3 concrete multi-word phrases a stock library would file that footage under, derived from the concept sentence. NEVER a bare single word — a stock library reads a bare word in its most common sense only.
- sense: when the illustrated line turns on a SENSE-TRAP word — a word naming more than one filmable thing — resolve which sense THIS clip means from the surrounding sentences and tag it "word:sense"; the concept sentence and the search phrases must be written in that resolved sense. Trap words: market (financial/street), bank (finance/river), match (sport/fire), crane (bird/machine), spring (season/coil/water source), cap (hat/limit), bull and bear (animal/market trend), bat (animal/sports gear), tank (vehicle/container), jam (traffic/food/music session), pitch (sports field/sales talk/throw), strike (hit/protest/baseball), bond (finance/connection), interest (finance/curiosity), short and long (finance position/size), stream (live video/water), viral (internet/illness), train (railway/exercise), race (competition/humanity), pool (swimming/billiards).
- avoid: the wrong-sense subjects the footage must NOT show (for a trading "market": street, crowd, farmers, stalls). When the concept is a ROOM or interior — a recording studio, an office, a kitchen, an apartment — rooms all look alike until their furniture is named, so the avoid list must name the COMPETING rooms' signature furniture: a recording-studio concept avoids bed, sofa, kitchen counter; an apartment concept avoids mixing desk, monitor speakers, acoustic panels; a kitchen concept avoids bed, mixing desk, acoustic panels; an office concept avoids bed, sofa, stove.
- media: video, photo, or either.
- duration_sec: optional, 2.5-4.0 seconds — only when the moment genuinely needs longer or shorter than the default 3s (a slow chart pan earns longer; a quick product flash earns shorter). Omit it otherwise; a missing value is the normal case.
At most the number of concepts the request below allows; most clips need fewer. No concept is always better than a forced one.

RESTRAINT — READ THIS TWICE: Default to NO effect. An effect is chosen only when a specific moment in the content justifies it, and its reason must name that moment (the line, the peak, the pause, the reveal). Generic reasons — "for engagement", "to make it viral", "more engaging" — justify nothing: the validator DISCARDS any effect whose reason is missing, empty or generic. No reason, no effect. A professionally edited clip is mostly clean cuts; the effects that survive must look intentional.

RULES: Never invent speech, quotes or events that are not in the transcript. Never change the meaning of what is said. Confidences are honest estimates between 0.0 and 1.0."""


def _profile_summary(profile: dict) -> str:
    pacing = profile.get("pacing", {})
    zooms = profile.get("zooms", {})
    audio = profile.get("audio", {})
    hook = profile.get("hook", {})
    transitions = profile.get("transitions", {})
    captions = profile.get("captions", {})
    from .hook_title_styles import title_style_guidance
    from .scene_rules import scene_rule_summary_lines

    guidance = title_style_guidance(hook.get("title_style"))
    return "\n".join(
        [
            f"Profile: {profile.get('niche', 'unknown')}",
            f"Cuts per minute: {pacing.get('target_cuts_per_min')}; transition on punchline: {transitions.get('on_punchline')}; transition on topic change: {transitions.get('on_topic_change')}",
            f"Default transition: {transitions.get('default')} ({transitions.get('duration_sec')}s); caption preset: {captions.get('preset_id')}",
            f"Punch-in zoom: scale {zooms.get('punch_in_scale')} on {zooms.get('punch_in_trigger')}",
            f"Hook title style: {hook.get('title_style')} ({hook.get('title_seconds')}s)"
            + (f" — {guidance}" if guidance else ""),
            f"Music: mood {audio.get('music_mood')} at volume {audio.get('music_volume_0_to_1')} (ducking {audio.get('ducking_db')} dB)",
            f"SFX from user library allowed: {bool(audio.get('sfx_from_user_library'))}",
            *scene_rule_summary_lines(profile),
        ]
    )


def _words_for_prompt(words: list[dict], limit: int = 150) -> str:
    parts: list[str] = []
    for word in words[:limit]:
        text = str(word.get("word") or word.get("text") or "").strip()
        if not text:
            continue
        try:
            start = float(word.get("start", 0.0) or 0.0)
        except (TypeError, ValueError):
            start = 0.0
        parts.append(f"[{start:.1f}] {text}")
    return " ".join(parts)


def _broll_prompt_line(profile: dict, duration: float) -> str:
    """The per-request stock B-roll instruction for the planner.

    The cap comes from the same helper validation enforces
    (``_profile_broll_concept_cap``), so the planner is never told a
    number validation would then cut. A profile with B-roll off is
    told so plainly — its concepts would be dropped anyway.
    """
    cap = _profile_broll_concept_cap(profile or {}, duration)
    if cap <= 0:
        return (
            "Stock B-roll: DISABLED for this profile — return an empty "
            "broll_concepts list."
        )
    return (
        f"Stock B-roll: ENABLED for this profile — return up to {cap} "
        "broll_concepts for this clip (fewer is fine; none is fine)."
    )


def _build_plan_prompt(
    *,
    niche: str,
    profile: dict,
    duration: float,
    text: str,
    words: list[dict],
    audio_features: Optional[dict],
    selection_scores: Optional[dict],
) -> str:
    features = audio_features or {}
    peaks = features.get("energy_peaks") or []
    pauses = features.get("pauses") or []
    pause_text = ", ".join(
        f"{p.get('start', 0):.1f}-{p.get('end', 0):.1f}s" for p in pauses[:6]
    ) or "none detected"
    scores = {
        key: value
        for key, value in (selection_scores or {}).items()
        if value is not None
    }
    timed_words = _words_for_prompt(words or [])
    return f"""Niche: {niche}
Clip duration: {duration:.1f} seconds

{_profile_summary(profile)}

{_broll_prompt_line(profile, duration)}

Selection scores (from the highlight picker): {scores or 'not available'}

Audio features: energy peaks at {peaks or 'none detected'}; pauses at {pause_text}.

Transcript:
{text[:3000]}

Word timings (seconds from clip start):
{timed_words or 'not available'}

Return the editing plan for this clip as JSON."""


def _draft_to_plan_dict(draft: ClipPlanDraft) -> dict[str, Any]:
    return {
        "hook": {
            "type": draft.hook_type,
            "timestamp": draft.hook_timestamp,
            "confidence": draft.hook_confidence,
            "reason": draft.hook_reason,
        },
        "opening_treatment": {
            "punch_zoom": draft.punch_zoom,
            "first_caption_emphasis": draft.first_caption_emphasis,
            "sfx_accent": draft.sfx_accent,
            "at_offset": draft.opening_at_offset,
            "reason": draft.opening_reason,
        },
        "transition_choices": [
            {"name": t.name, "at_offset": t.at_offset, "reason": t.reason}
            for t in draft.transition_choices
        ],
        "caption_style": (
            {"name": draft.caption_style_name, "reason": draft.caption_style_reason}
            if draft.caption_style_name
            else None
        ),
        "phases": [
            {"start_offset": p.start_offset, "end_offset": p.end_offset, "intent": p.intent}
            for p in draft.phases
        ],
        "sfx_events": [
            {"at_offset": e.at_offset, "name": e.name, "reason": e.reason}
            for e in draft.sfx_events
        ],
        "broll_concepts": [
            {
                "anchor_sec": c.anchor_sec,
                "spoken": c.spoken,
                "concept": c.concept,
                "search_queries": list(c.search_queries),
                "sense": c.sense,
                "media": c.media,
                "avoid": list(c.avoid),
                "duration_sec": c.duration_sec,
            }
            for c in draft.broll_concepts
        ],
        "music_decision": {
            "use_profile_music": draft.use_profile_music,
            "reason": draft.music_reason,
        },
        "plan_confidence": draft.plan_confidence,
    }


# Cached agent for plan generation (lazy initialization like in classify.py)
_plan_agent: Optional[Agent[None, ClipPlanDraft]] = None
_plan_agent_signature: Optional[tuple] = None


def get_plan_agent() -> Agent[None, ClipPlanDraft]:
    """Get or create the planner agent (lazy initialization)."""
    global _plan_agent, _plan_agent_signature
    runtime_config = get_config()
    signature = (
        runtime_config.llm,
        runtime_config.openai_api_key,
        runtime_config.openrouter_api_key,
        runtime_config.openrouter_fallback_model,
        runtime_config.google_api_key,
        runtime_config.anthropic_api_key,
        runtime_config.ollama_base_url,
        runtime_config.ollama_api_key,
    )
    if _plan_agent is None or _plan_agent_signature != signature:
        from ..runtime_settings import apply_settings_to_process_env

        apply_settings_to_process_env(runtime_config.as_runtime_settings())

        # Reuse the model building logic from ai.py — the same LLM
        # configuration the niche classifier runs on. No new keys.
        from ..ai import _build_transcript_model, _get_missing_llm_key_error, _uses_native_output

        config_error = _get_missing_llm_key_error(runtime_config.llm, runtime_config)
        if config_error:
            raise RuntimeError(config_error)

        _plan_agent = Agent[None, ClipPlanDraft](
            model=_build_transcript_model(runtime_config),
            output_type=(
                NativeOutput(ClipPlanDraft, strict=True)
                if _uses_native_output(runtime_config.llm)
                else ClipPlanDraft
            ),
            system_prompt=PLAN_SYSTEM_PROMPT,
            retries={'output': 2},
        )
        _plan_agent_signature = signature
    return _plan_agent


async def generate_clip_plan(
    *,
    niche: str,
    profile: dict,
    duration: float,
    text: str,
    words: Optional[list[dict]] = None,
    audio_features: Optional[dict] = None,
    selection_scores: Optional[dict] = None,
) -> ClipPlan:
    """Generate the plan for one selected segment. NEVER raises.

    On success the returned plan has ``source == "llm"`` and has passed
    ``validate_plan_dict``. On ANY failure (LLM error, timeout, invalid
    output) the deterministic minimal plan is returned with
    ``source == "fallback"`` and the failure is logged.
    """
    try:
        agent = get_plan_agent()
        prompt = _build_plan_prompt(
            niche=niche,
            profile=profile or {},
            duration=duration,
            text=text or "",
            words=words or [],
            audio_features=audio_features,
            selection_scores=selection_scores,
        )
        result = await asyncio.wait_for(agent.run(prompt), timeout=_plan_timeout_sec())
        raw = _draft_to_plan_dict(result.output)
        return validate_plan_dict(
            raw,
            niche=niche,
            duration=duration,
            profile=profile,
            pauses=(audio_features or {}).get("pauses"),
        )
    except Exception:
        logger.warning(
            "Clip plan generation failed for niche %s; using the profile-default plan",
            niche,
            exc_info=True,
        )
        return minimal_plan(niche, profile, duration)


# --- Cheap deterministic audio features -------------------------------------


def compute_audio_features(video_path: Any, start: float, end: float) -> dict[str, Any]:
    """Energy peaks and pauses over a source range, from 50ms window levels.

    Reuses gap_activity's window-level decode (one short ffmpeg pass over
    the segment range only). Returns {} on any failure — the planner works
    from the transcript alone rather than failing. Offsets in the result
    are seconds relative to ``start``.
    """
    try:
        import numpy as np

        from ..media.ffmpeg import select_audio_stream_index
        from ..media.gap_activity import _window_levels_db

        path = Path(video_path)
        if end <= start or not path.exists():
            return {}
        stream = select_audio_stream_index(path)
        if stream is None:
            return {}
        levels = _window_levels_db(path, stream, float(start), float(end))
        if len(levels) < 4:
            return {}

        speech_db = float(np.percentile(levels, 75))

        peaks: list[float] = []
        threshold = max(speech_db - _PEAK_BELOW_SPEECH_DB, _PEAK_FLOOR_DB)
        last_peak_index = -(int(_PEAK_MIN_GAP_SEC / _AUDIO_WINDOW_SECONDS) + 1)
        min_gap_windows = int(_PEAK_MIN_GAP_SEC / _AUDIO_WINDOW_SECONDS)
        for index in range(len(levels)):
            if levels[index] < threshold:
                continue
            left = levels[index - 1] if index > 0 else levels[index]
            right = levels[index + 1] if index + 1 < len(levels) else levels[index]
            if levels[index] < left or levels[index] < right:
                continue
            if index - last_peak_index < min_gap_windows:
                continue
            peaks.append(round((index + 0.5) * _AUDIO_WINDOW_SECONDS, 2))
            last_peak_index = index
            if len(peaks) >= _PEAK_MAX_COUNT:
                break

        pauses: list[dict[str, float]] = []
        pause_threshold = speech_db - _PAUSE_DROP_DB
        min_run = max(1, int(_PAUSE_MIN_SECONDS / _AUDIO_WINDOW_SECONDS))
        run_start: Optional[int] = None
        for index in range(len(levels) + 1):
            quiet = index < len(levels) and levels[index] < pause_threshold
            if quiet and run_start is None:
                run_start = index
            elif not quiet and run_start is not None:
                if index - run_start >= min_run:
                    pauses.append(
                        {
                            "start": round(run_start * _AUDIO_WINDOW_SECONDS, 2),
                            "end": round(index * _AUDIO_WINDOW_SECONDS, 2),
                        }
                    )
                run_start = None
            if len(pauses) >= _PAUSE_MAX_COUNT:
                break

        return {
            "energy_peaks": peaks,
            "pauses": pauses,
            "speech_level_db": round(speech_db, 1),
        }
    except Exception:
        logger.warning("Audio feature extraction failed; planning without audio features", exc_info=True)
        return {}
