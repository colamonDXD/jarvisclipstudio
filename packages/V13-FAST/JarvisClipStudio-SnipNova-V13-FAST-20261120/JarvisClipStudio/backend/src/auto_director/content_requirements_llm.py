"""LLM passes for Content Requirements: focus judgements + brief extraction.

Plumbing mirrors ``auto_director/classify.py`` exactly: a lazy cached
pydantic_ai Agent built via ``ai._build_transcript_model``, the missing
key check, NativeOutput where the model needs it, and execution through
``provider_manager.run_recorded`` when the manager is enabled.

Standing rule, enforced here by an explicit denylist guard: the focus
and extraction passes must NEVER run on Gemini or on
``llama-3.3-70b-versatile``. ``Config._infer_default_llm`` can resolve
to a Gemini model from key presence alone, so the resolved model string
is checked before any agent is built; a banned model means the pass is
skipped and callers take the deterministic-only fallback (grey-zone
verdicts labelled uncertain) rather than silently calling a banned
model. Permitted LiteLLM aliases (the deployment chain: Groq
openai/gpt-oss-120b with fallbacks) pass the guard.
"""

from __future__ import annotations

import json
import logging
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field

from .. import provider_manager
from ..config import get_config

logger = logging.getLogger(__name__)

BANNED_MODEL_SUBSTRINGS = (
    "gemini",
    "google-gla",
    "google:",
    "llama-3.3-70b-versatile",
)

PERMITTED_ALIASES = {"clip-assured", "groq-20b", "local-qwen"}


def is_banned_model(model_name: Optional[str]) -> bool:
    """True when the resolved model string names a banned model."""
    if not model_name:
        return False
    lowered = str(model_name).lower()
    if any(alias in lowered for alias in PERMITTED_ALIASES):
        return False
    return any(banned in lowered for banned in BANNED_MODEL_SUBSTRINGS)


def resolved_focus_model() -> Optional[str]:
    """The model the focus pass would use, or None when banned/unset."""
    try:
        model = get_config().llm
    except Exception:
        return None
    if not model or is_banned_model(model):
        return None
    return model


class RequirementJudgement(BaseModel):
    candidate_id: int
    requirement_id: str
    focus: Literal["major", "passing", "absent"]
    confidence: float = Field(ge=0.0, le=1.0)
    evidence_excerpt: str
    reason: str = ""


class FocusJudgements(BaseModel):
    judgements: List[RequirementJudgement] = Field(default_factory=list)


FOCUS_SYSTEM_PROMPT = """You judge whether short video clip candidates satisfy campaign content requirements.

For each candidate and requirement, decide the focus of the required topic in that candidate:
- major: the topic is a main focus of the candidate
- passing: the topic is only mentioned in passing
- absent: the topic does not appear (including via related words or context)

Rules:
- evidence_excerpt MUST be copied verbatim from that candidate's transcript text.
- Never invent evidence. If you cannot quote it, the focus is absent.
- Return valid JSON only, matching the required schema."""


_focus_agent = None
_focus_agent_signature = None
_focus_agent_model: Optional[str] = None


def get_focus_agent():
    """Lazy cached focus agent (classify.py plumbing, denylist-guarded)."""
    global _focus_agent, _focus_agent_signature, _focus_agent_model
    from pydantic_ai import Agent
    from pydantic_ai.output import NativeOutput

    runtime_config = get_config()
    selected_model = runtime_config.llm
    if is_banned_model(selected_model):
        raise RuntimeError(
            f"focus pass refused: model {selected_model!r} is on the denylist"
        )
    signature = (
        selected_model,
        runtime_config.openai_api_key,
        runtime_config.openrouter_api_key,
        runtime_config.openrouter_fallback_model,
        runtime_config.google_api_key,
        runtime_config.anthropic_api_key,
        runtime_config.ollama_base_url,
        runtime_config.ollama_api_key,
    )
    if _focus_agent is None or _focus_agent_signature != signature:
        from ..ai import (
            _build_transcript_model,
            _get_missing_llm_key_error,
            _uses_native_output,
        )
        from ..runtime_settings import apply_settings_to_process_env

        apply_settings_to_process_env(runtime_config.as_runtime_settings())
        config_error = _get_missing_llm_key_error(selected_model, runtime_config)
        if config_error:
            raise RuntimeError(config_error)
        _focus_agent = Agent(
            model=_build_transcript_model(runtime_config, selected_model),
            output_type=(
                NativeOutput(FocusJudgements, strict=True)
                if _uses_native_output(selected_model)
                else FocusJudgements
            ),
            system_prompt=FOCUS_SYSTEM_PROMPT,
            retries={'output': 2},
        )
        _focus_agent_signature = signature
        _focus_agent_model = selected_model
    return _focus_agent


def build_focus_prompt(candidates: List[Dict[str, Any]], brief: Dict[str, Any]) -> str:
    requirements = [
        {
            "id": req.get("id"),
            "type": req.get("type"),
            "text": req.get("text"),
            "kind": req.get("kind"),
        }
        for req in brief.get("requirements") or []
        if req.get("type") == "topic_focus"
    ]
    payload = {"requirements": requirements, "candidates": candidates}
    return (
        "Judge each candidate against each requirement.\n"
        + json.dumps(payload, ensure_ascii=False)
    )


async def judge_focus(
    candidates: List[Dict[str, Any]], brief: Dict[str, Any]
) -> Optional[Dict[str, Dict[str, Dict[str, Any]]]]:
    """One LLM focus pass over the candidate pool.

    Returns {candidate_index: {requirement_id: judgement}} with raw
    judgements (verbatim-excerpt validation happens deterministically
    in content_requirements._merge_llm_judgement), or None when the
    pass is unavailable for ANY reason — banned model, missing key,
    provider error — in which case callers use deterministic-only
    verdicts and label grey zones uncertain. Never fakes a judgement.
    """
    if not candidates:
        return None
    if resolved_focus_model() is None:
        logger.info("Focus pass skipped: model unavailable or banned")
        return None
    try:
        agent = get_focus_agent()
        prompt = build_focus_prompt(candidates, brief)
        if provider_manager.manager_enabled():
            result = await provider_manager.run_recorded(
                agent,
                prompt,
                alias=_focus_agent_model or get_config().llm,
                purpose="requirements",
            )
        else:
            result = await agent.run(prompt)
        output = result.output
        judgements: Dict[str, Dict[str, Dict[str, Any]]] = {}
        for item in output.judgements or []:
            judgements.setdefault(str(item.candidate_id), {})[item.requirement_id] = {
                "focus": item.focus,
                "confidence": item.confidence,
                "evidence_excerpt": item.evidence_excerpt,
                "reason": item.reason,
            }
        return judgements
    except Exception as error:
        logger.warning("Focus pass failed (%s); deterministic-only mode", error)
        return None


# ---------------------------------------------------------------------------
# Paste-a-brief extraction (LLM path; deterministic fallback is the caller)
# ---------------------------------------------------------------------------


class ExtractedRequirement(BaseModel):
    text: str = ""
    type: Literal[
        "must_mention", "topic_focus", "structure_hook", "structure_cta",
        "visual_presence", "caption_rule", "info_only",
    ] = "topic_focus"
    kind: Literal["mandatory", "optional"] = "mandatory"
    phrase: Optional[str] = None
    topics: List[str] = Field(default_factory=list)
    hashtags: List[str] = Field(default_factory=list)


class ExtractedFlagPoint(BaseModel):
    # at is a timecode as typed ("03:13") or raw seconds; the v2
    # normaliser's parse_timecode accepts both.
    at: Any
    category: Literal[
        "licence_plate", "branding", "copyright_music", "sensitive_topic",
        "alcohol", "review", "other",
    ] = "other"
    note: str = ""


class ExtractedKeyTimestamp(BaseModel):
    at: Any
    description: str = ""


class ExtractedBrief(BaseModel):
    requirements: List[ExtractedRequirement] = Field(default_factory=list)
    allowed_ranges: List[Dict[str, Any]] = Field(default_factory=list)
    excluded_ranges: List[Dict[str, Any]] = Field(default_factory=list)
    flag_points: List[ExtractedFlagPoint] = Field(default_factory=list)
    key_timestamps: List[ExtractedKeyTimestamp] = Field(default_factory=list)
    example_url: Optional[str] = None


EXTRACTION_SYSTEM_PROMPT = """You extract a structured campaign brief (schema version 2) from raw campaign text.

Requirement types:
- must_mention: an exact phrase (brand, platform, promo code) must appear; set phrase.
- topic_focus: a named topic/angle must be featured; set topics when several are listed.
- structure_hook: a hook is required within the first N seconds.
- structure_cta: a call to action is required near the end.
- visual_presence: the product/gameplay must actually be shown.
- caption_rule: required hashtags or caption phrases; set hashtags.
- info_only: campaign facts (payouts, audience, platforms) — never checked.

Also extract allowed_ranges from time patterns (start/end as typed, end null for "to the end")
and example_url when a link is present.

Screenshot-style briefs carry three more structures:
- excluded_ranges: ranges under a "content to avoid" / "do not use" instruction
  (e.g. "0:47 - 0:53 (this should not be included in any content)"). These are
  NEVER allowed_ranges.
- flag_points: single timestamps listed for human review, each with a category —
  licence_plate, branding, copyright_music, sensitive_topic, alcohol, review
  (explicit "flag for review") or other — and the brief's note for that point.
- key_timestamps: a "Key timestamps" list of named moments (time + description).
- A theme sentence like "every clip should connect back to natural diamonds"
  is a mandatory topic_focus whose topics are the thing clips must connect to.

Return valid JSON only, matching the schema."""

_extraction_agent = None
_extraction_agent_signature = None
_extraction_agent_model: Optional[str] = None


def get_extraction_agent():
    global _extraction_agent, _extraction_agent_signature, _extraction_agent_model
    from pydantic_ai import Agent
    from pydantic_ai.output import NativeOutput

    runtime_config = get_config()
    selected_model = runtime_config.llm
    if is_banned_model(selected_model):
        raise RuntimeError(
            f"extraction refused: model {selected_model!r} is on the denylist"
        )
    signature = (selected_model, runtime_config.openai_api_key, runtime_config.google_api_key)
    if _extraction_agent is None or _extraction_agent_signature != signature:
        from ..ai import (
            _build_transcript_model,
            _get_missing_llm_key_error,
            _uses_native_output,
        )
        from ..runtime_settings import apply_settings_to_process_env

        apply_settings_to_process_env(runtime_config.as_runtime_settings())
        config_error = _get_missing_llm_key_error(selected_model, runtime_config)
        if config_error:
            raise RuntimeError(config_error)
        _extraction_agent = Agent(
            model=_build_transcript_model(runtime_config, selected_model),
            output_type=(
                NativeOutput(ExtractedBrief, strict=True)
                if _uses_native_output(selected_model)
                else ExtractedBrief
            ),
            system_prompt=EXTRACTION_SYSTEM_PROMPT,
            retries={'output': 2},
        )
        _extraction_agent_signature = signature
        _extraction_agent_model = selected_model
    return _extraction_agent


async def extract_brief_with_llm(brief_text: str) -> Optional[Dict[str, Any]]:
    """LLM extraction draft (raw dict), or None on any failure/ban."""
    if resolved_focus_model() is None:
        return None
    try:
        agent = get_extraction_agent()
        if provider_manager.manager_enabled():
            result = await provider_manager.run_recorded(
                agent,
                brief_text,
                alias=_extraction_agent_model or get_config().llm,
                purpose="brief_extract",
            )
        else:
            result = await agent.run(brief_text)
        return result.output.model_dump()
    except Exception as error:
        logger.warning("Brief extraction LLM failed (%s); deterministic fallback", error)
        return None
