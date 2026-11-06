"""Niche classification for auto-director."""

from typing import Literal, Optional
from pydantic import BaseModel, Field

from ..ai import get_transcript_agent
from ..config import get_config
from pydantic_ai import Agent
from pydantic_ai.output import NativeOutput

NicheLiteral = Literal[
    "podcast_news",
    "travel_vlog",
    "meme_comedy",
    "gaming",
    "sports",
    "education_tutorial",
    "finance_business",
    "vlog_lifestyle",
]

NICHE_VALUES: set[NicheLiteral] = {
    "podcast_news",
    "travel_vlog",
    "meme_comedy",
    "gaming",
    "sports",
    "education_tutorial",
    "finance_business",
    "vlog_lifestyle",
}

FALLBACK_RESULT = {
    "niche": "podcast_news",
    "confidence": 0.0,
    "reasons": ["fallback"],
}


class NicheClassification(BaseModel):
    """Structured output for niche classification."""

    niche: NicheLiteral = Field(description="The classified niche category")
    confidence: float = Field(description="Confidence score from 0.0 to 1.0", ge=0.0, le=1.0)
    reasons: list[str] = Field(
        description="Up to 3 short reasons for the classification",
        min_length=1,
        max_length=3,
    )


# System prompt for niche classification
NICHE_CLASSIFICATION_SYSTEM_PROMPT = """You are a content classifier for short-form video editing.

Analyze the transcript sample and named entities to classify the content into exactly ONE niche category.

NICHE CATEGORIES:
- podcast_news: Interviews, discussions, commentary, news analysis, podcast episodes
- travel_vlog: Travel experiences, destination guides, itineraries, cultural exploration
- meme_comedy: Comedy sketches, meme commentary, reaction videos, humorous content
- gaming: Gameplay, walkthroughs, gaming news, esports, game reviews
- sports: Sports highlights, analysis, commentary, athlete interviews, training
- education_tutorial: How-to guides, tutorials, educational explanations, skill teaching
- finance_business: Investing, entrepreneurship, business advice, market analysis, personal finance
- vlog_lifestyle: Personal vlogs, daily routines, lifestyle tips, life updates, Q&A

OUTPUT REQUIREMENTS:
- Return valid JSON only. No markdown, no code fences, no extra text.
- JSON keys: "niche", "confidence", "reasons"
- "niche" must be exactly one of the 8 categories listed above
- "confidence" must be a float between 0.0 and 1.0
- "reasons" must be an array of 1-3 short strings (max 50 chars each)"""


def _build_niche_classification_prompt(transcript_sample: str, entities: list[str]) -> str:
    """Build the prompt for niche classification."""
    entities_str = ", ".join(entities) if entities else "none detected"
    return f"""Transcript sample:
{transcript_sample}

Named entities: {entities_str}

Classify this content into one niche category. Consider the main topic, tone, and format of the content."""


# Cached agent for niche classification (lazy initialization like in ai.py)
_niche_agent: Optional[Agent[None, NicheClassification]] = None
_niche_agent_signature: Optional[tuple] = None


def get_niche_classification_agent() -> Agent[None, NicheClassification]:
    """Get or create the niche classification agent (lazy initialization)."""
    global _niche_agent, _niche_agent_signature
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
    if _niche_agent is None or _niche_agent_signature != signature:
        from ..runtime_settings import apply_settings_to_process_env
        apply_settings_to_process_env(runtime_config.as_runtime_settings())

        # Reuse the model building logic from ai.py
        from ..ai import _build_transcript_model, _uses_native_output, _get_missing_llm_key_error

        config_error = _get_missing_llm_key_error(runtime_config.llm, runtime_config)
        if config_error:
            raise RuntimeError(config_error)

        _niche_agent = Agent[None, NicheClassification](
            model=_build_transcript_model(runtime_config),
            output_type=NativeOutput(NicheClassification, strict=True) if _uses_native_output(runtime_config.llm) else NicheClassification,
            system_prompt=NICHE_CLASSIFICATION_SYSTEM_PROMPT,
            output_retries=2,
        )
        _niche_agent_signature = signature
    return _niche_agent


async def classify_niche(transcript_sample: str, entities: list[str]) -> dict:
    """
    Classify the niche of content based on transcript sample and entities.

    Args:
        transcript_sample: A sample of the video transcript (first ~500 chars or key segments)
        entities: List of named entities extracted from the transcript

    Returns:
        Dict with keys: niche (str), confidence (float), reasons (list[str] max 3)
        On ANY failure, returns fallback: {"niche": "podcast_news", "confidence": 0.0, "reasons": ["fallback"]}
    """
    try:
        # Get the cached niche classification agent
        agent = get_niche_classification_agent()

        # Build the prompt
        prompt = _build_niche_classification_prompt(transcript_sample, entities)

        # Run the classification
        result = await agent.run(prompt)
        classification = result.output

        # Validate the niche value is in our enum
        if classification.niche not in NICHE_VALUES:
            return FALLBACK_RESULT

        # Validate confidence range
        confidence = max(0.0, min(1.0, float(classification.confidence)))

        # Validate reasons length
        reasons = classification.reasons[:3] if classification.reasons else ["fallback"]

        return {
            "niche": classification.niche,
            "confidence": confidence,
            "reasons": reasons,
        }

    except Exception:
        # On ANY failure (LLM error, parse error, validation error, etc.), return fallback
        return FALLBACK_RESULT