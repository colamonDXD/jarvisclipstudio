"""Comment bait: one pinned-comment question per finished clip.

Viral-pattern research (``viral-compare/GAP_REPORT.md`` pattern #20 /
build queue #4): clips travel further when every post carries a pinned
comment with a NARROW, video-specific question — "Did he overreact or
was that clutch?" beats "What do you think?" — and the hard call to
action lives in that pinned comment, never in the spoken track. No
competitor in the matrix generates one. This module is that generator.

Two halves, one switch (``COMMENT_BAIT``, default OFF):

- **Pinned-comment question** — generated when a clip is finished (the
  task path in ``services/video_service`` and the live finisher in
  ``live/finisher``), stored on the clip's record (the EDL key
  ``comment_question``, folded into the stored clip-plan JSON beside
  ``hook_application``/``retention``, and printed into the deliverable
  bundle's ``post.txt``), and surfaced by ``SocialService.create_post``
  as the post payload's ``pinned_comment``.
- **End-question overlay** — the same question burned as a small end
  card over the final seconds of the clip (``media/end_question.py``,
  driven by the EDL key). Plain ("just cuts") clips never get the
  overlay; the post-side question still applies to them.

Generation goes through the app's LLM ladder — the same pydantic-ai
plumbing as ``content_requirements_llm`` (Groq ``openai/gpt-oss-120b``
primary; Gemini and the retired llama are denylist-refused) on the
async path, and the live package's gateway helper (``live.vod._chat``)
on the sync finisher path. When the ladder is unavailable for ANY
reason, a deterministic per-niche template answers instead — same
inputs, same question, no model involved.

Owner pins hold for every question, from either source: NO emoji
(sanitised out of model output), small and plain, and profanity is
masked by ``media/profanity`` exactly as titles are. This module never
touches titles or ``profanity.py`` itself.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import re
from typing import Any, Dict, Optional

from .. import provider_manager
from ..config import get_config
from .content_requirements_llm import is_banned_model

logger = logging.getLogger(__name__)

SWITCH = "COMMENT_BAIT"
MAX_QUESTION_CHARS = 84
_LLM_TIMEOUT_SECONDS = 45.0
_TRANSCRIPT_EXCERPT_CHARS = 1500


def comment_bait_enabled() -> bool:
    """The one switch for both halves (default OFF)."""
    return os.getenv(SWITCH, "off").strip().lower() in ("on", "1", "true", "yes")


# ---------------------------------------------------------------------------
# Sanitising — every question, model-written or template, passes through here
# ---------------------------------------------------------------------------

_EMOJI_RE = re.compile(
    "["
    "\U0001f300-\U0001faff"  # pictographs / emoticons / transport / symbols
    "\U0001f1e6-\U0001f1ff"  # regional indicators (flags)
    "\U00002600-\U000027bf"  # misc symbols + dingbats
    "\U00002b00-\U00002bff"  # stars and misc symbols/arrows
    "\U00002190-\U000021ff"  # arrows
    "\U0000fe00-\U0000fe0f"  # variation selectors
    "\u200d"  # zero-width joiner
    "\u20e3"  # combining keycap
    "]+"
)
_LABEL_RE = re.compile(r"^(pinned comment|comment|question|q)\s*[:\-–]\s*", re.IGNORECASE)
_HASHTAG_RE = re.compile(r"#\w+")
_QUOTES = "\"'`“”‘’«»"


def sanitize_question(raw: Any) -> Optional[str]:
    """One clean, owner-safe question out of model text, or None.

    Keeps only up to the first "?" (a model that adds commentary after
    its question loses the commentary), strips labels/quotes/hashtags,
    removes ALL emoji (owner rule — the poster adds their own), removes
    "%"/backslashes so the text is safe inside a drawtext textfile,
    caps the length at a word boundary, and masks profanity the same
    way titles are masked. A URL anywhere rejects the text outright.
    """
    if raw is None:
        return None
    text = " ".join(str(raw).split())
    if not text:
        return None
    if "http" in text.lower() or "www." in text.lower():
        return None
    if "?" in text:
        text = text[: text.index("?") + 1]
    text = _LABEL_RE.sub("", text).strip().strip(_QUOTES).strip()
    text = _EMOJI_RE.sub("", text)
    text = _HASHTAG_RE.sub("", text)
    text = text.replace("%", "").replace("\\", "")
    text = " ".join(text.split()).strip().strip(_QUOTES).strip()
    if len(text) > MAX_QUESTION_CHARS:
        cut = text[:MAX_QUESTION_CHARS].rsplit(" ", 1)[0].rstrip(" ,;:")
        text = cut if cut else text[:MAX_QUESTION_CHARS].rstrip()
    if text and not text.endswith("?"):
        text = text[: MAX_QUESTION_CHARS - 1].rstrip() + "?"
    if len(text) < 8 or not any(ch.isalpha() for ch in text):
        return None
    try:
        from ..media.profanity import mask_profanity

        text = mask_profanity(text)
    except Exception:  # masking must never lose the question
        logger.debug("Profanity masking unavailable for comment question", exc_info=True)
    return text or None


# ---------------------------------------------------------------------------
# Deterministic fallback — per-niche templates, picked by a stable hash
# ---------------------------------------------------------------------------

_FALLBACKS: Dict[str, tuple] = {
    "gaming": (
        "Did he overreact or was that clutch?",
        "Skill or pure luck on that play?",
        "Was that the best moment of the stream?",
    ),
    "finance": (
        "Would you buy here, or is this the top?",
        "Is he early on this, or already too late?",
        "Smart money move, or a trap?",
    ),
    "talk": (
        "Is he right about this, or is he missing something?",
        "Do you agree with that take?",
        "Which side are you on here?",
    ),
    "comedy": (
        "Funniest moment of the video, or did another part beat it?",
        "Did that joke land, or was it just awkward?",
    ),
    "education": (
        "Did that explanation finally make it click?",
        "Clear enough to use, or still confusing?",
    ),
    "default": (
        "What would you have done in his place?",
        "Best moment of the clip, or did the ending beat it?",
        "Would you have kept watching after this part?",
    ),
}

_GAMING_CREATOR_TEMPLATES = (
    "Did {creator} overreact or was that clutch?",
    "Was {creator} robbed there, or was it fair?",
)

_NICHE_FAMILIES = (
    ("gaming", ("gaming", "football", "sport", "anime")),
    ("finance", ("finance", "business", "trading")),
    ("talk", ("podcast", "crime", "news", "interview")),
    ("comedy", ("comedy", "meme")),
    ("education", ("education", "tutorial")),
)


def _niche_family(niche: Optional[str]) -> str:
    lowered = str(niche or "").lower()
    for family, needles in _NICHE_FAMILIES:
        if any(needle in lowered for needle in needles):
            return family
    return "default"


def fallback_question(
    *,
    niche: Optional[str] = None,
    hook_title: Optional[str] = None,
    transcript_text: str = "",
    creator: Optional[str] = None,
) -> str:
    """The deterministic question for a clip: per-niche template chosen
    by a stable hash of the clip's own title + transcript, so the same
    clip always gets the same question and different clips vary."""
    family = _niche_family(niche)
    templates = list(_FALLBACKS[family])
    creator_name = " ".join(str(creator or "").split()).strip().lstrip("@")
    if family == "gaming" and creator_name:
        templates = [t.format(creator=creator_name) for t in _GAMING_CREATOR_TEMPLATES] + templates
    seed = f"{family}|{hook_title or ''}|{(transcript_text or '')[:500]}"
    index = int(hashlib.sha256(seed.encode("utf-8")).hexdigest(), 16) % len(templates)
    return sanitize_question(templates[index]) or _FALLBACKS["default"][0]


# ---------------------------------------------------------------------------
# The ladder pass
# ---------------------------------------------------------------------------

QUESTION_SYSTEM_PROMPT = """You write the pinned comment for a short vertical clip (TikTok / Shorts / Reels).

Write ONE question that viewers will actually answer in the comments. The best pinned questions are narrow and specific to THIS clip's moment — a stranger who just watched it can answer in a few words. "Did he overreact or was that clutch?" beats "What do you think?".

Rules:
- One question only. No hashtags, no emoji, no quotation marks, no "comment below" or any other call to action — the question itself does the work.
- Ground it in the transcript and title provided. Never invent people, events or details that are not there.
- Talk about the person on screen in the third person (he / she / they, or the creator's name when given) — never as "I", and never as the poster.
- Under 14 words.
Return only the question text."""


def build_question_prompt(
    *,
    niche: Optional[str] = None,
    hook_title: Optional[str] = None,
    transcript_text: str = "",
    creator: Optional[str] = None,
) -> str:
    """The user prompt: a JSON payload, transcript sanitised first
    (takes-1's prompt-safety pass, so speech cannot pose as an
    instruction) and capped — the question needs the moment, not the
    whole video."""
    from ..utils.prompt_safety import sanitize_transcript_text

    excerpt = sanitize_transcript_text((transcript_text or "")[:_TRANSCRIPT_EXCERPT_CHARS])
    payload = {
        "niche": str(niche or "general"),
        "clip_title": " ".join(str(hook_title or "").split())[:200],
        "creator": " ".join(str(creator or "").split()).lstrip("@")[:60],
        "transcript": excerpt,
    }
    return (
        "Write the pinned-comment question for this clip.\n"
        + json.dumps(payload, ensure_ascii=False)
    )


def resolved_question_model() -> Optional[str]:
    """The model the question pass would use, or None when banned/unset
    (the content-requirements denylist: never Gemini, never the retired
    llama — the caller's deterministic fallback answers instead)."""
    try:
        model = get_config().llm
    except Exception:
        return None
    if not model or is_banned_model(model):
        return None
    return model


_question_agent = None
_question_agent_signature = None
_question_agent_model: Optional[str] = None


def get_question_agent():
    """Lazy cached question agent (content_requirements_llm plumbing,
    denylist-guarded, plain-text output)."""
    global _question_agent, _question_agent_signature, _question_agent_model
    from pydantic_ai import Agent

    runtime_config = get_config()
    selected_model = runtime_config.llm
    if is_banned_model(selected_model):
        raise RuntimeError(
            f"comment-bait pass refused: model {selected_model!r} is on the denylist"
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
    if _question_agent is None or _question_agent_signature != signature:
        from ..ai import _build_transcript_model, _get_missing_llm_key_error
        from ..runtime_settings import apply_settings_to_process_env

        apply_settings_to_process_env(runtime_config.as_runtime_settings())
        config_error = _get_missing_llm_key_error(selected_model, runtime_config)
        if config_error:
            raise RuntimeError(config_error)
        _question_agent = Agent(
            model=_build_transcript_model(runtime_config, selected_model),
            output_type=str,
            system_prompt=QUESTION_SYSTEM_PROMPT,
            retries=1,
        )
        _question_agent_signature = signature
        _question_agent_model = selected_model
    return _question_agent


async def _llm_question(prompt: str) -> Optional[str]:
    """One ladder completion, sanitised; None on ANY failure — banned
    model, missing key, provider error, unusable output."""
    if resolved_question_model() is None:
        logger.info("Comment-bait pass skipped: model unavailable or banned")
        return None
    try:
        agent = get_question_agent()
        if provider_manager.manager_enabled():
            result = await asyncio.wait_for(
                provider_manager.run_recorded(
                    agent,
                    prompt,
                    alias=_question_agent_model or get_config().llm,
                    purpose="comment_bait",
                ),
                timeout=_LLM_TIMEOUT_SECONDS,
            )
        else:
            result = await asyncio.wait_for(
                agent.run(prompt), timeout=_LLM_TIMEOUT_SECONDS
            )
        return sanitize_question(getattr(result, "output", None))
    except Exception as error:
        logger.warning("Comment-bait LLM pass failed (%s); using the template", error)
        return None


def _llm_question_sync(prompt: str) -> Optional[str]:
    """The sync twin for the live finisher: the live package's own
    gateway helper (``live.vod._chat`` — the app's LLM gateway, free
    models), system prompt folded into the single user message."""
    try:
        from ..live.vod import _chat

        return sanitize_question(_chat(f"{QUESTION_SYSTEM_PROMPT}\n\n{prompt}"))
    except Exception as error:
        logger.warning("Comment-bait sync pass failed (%s); using the template", error)
        return None


async def generate_comment_question(
    *,
    niche: Optional[str] = None,
    hook_title: Optional[str] = None,
    transcript_text: str = "",
    creator: Optional[str] = None,
) -> Optional[str]:
    """The clip's pinned-comment question, or None when COMMENT_BAIT
    is off. Ladder first, deterministic template on any failure — a
    finished clip always gets its question while the switch is on."""
    if not comment_bait_enabled():
        return None
    prompt = build_question_prompt(
        niche=niche, hook_title=hook_title,
        transcript_text=transcript_text, creator=creator,
    )
    question = await _llm_question(prompt)
    if question:
        return question
    return fallback_question(
        niche=niche, hook_title=hook_title,
        transcript_text=transcript_text, creator=creator,
    )


def generate_comment_question_sync(
    *,
    niche: Optional[str] = None,
    hook_title: Optional[str] = None,
    transcript_text: str = "",
    creator: Optional[str] = None,
) -> Optional[str]:
    """Sync twin of :func:`generate_comment_question` for the live
    finisher (a sync render path — no event loop to await on)."""
    if not comment_bait_enabled():
        return None
    prompt = build_question_prompt(
        niche=niche, hook_title=hook_title,
        transcript_text=transcript_text, creator=creator,
    )
    question = _llm_question_sync(prompt)
    if question:
        return question
    return fallback_question(
        niche=niche, hook_title=hook_title,
        transcript_text=transcript_text, creator=creator,
    )
