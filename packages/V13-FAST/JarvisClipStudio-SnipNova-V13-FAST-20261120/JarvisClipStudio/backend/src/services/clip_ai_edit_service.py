"""AI edit assistant for the clip editor (Lane-5 editor seam).

A user types a plain-language instruction ("make the intro shorter, drop
the last segment, captions at the top"); this service turns it into the
clip editor's own operations and hands them back for queueing on the
EXACT path a manual edit takes (the ``edit_clip_task`` worker job ->
``ClipService.edit_clip`` -> ``rerender_clip``). There is no second
render path and no second op vocabulary: the contract lives in
``src/auto_director/ai_edit.py`` and allows only the editor's segment
operations, Caption Studio overrides, and a music-library track.

Fail-safe contract: the model's reply is parsed, schema-validated,
checked against the live caption/music registries, and DRY-RUN through
the editor's own applier against the clip's current plan before this
service returns anything. Any failure — LLM unreachable, unparseable
reply, unknown operation, impossible edit — raises and leaves the clip
completely untouched; the caller queues nothing.

The LLM is the app's configured one: the agent is built with the same
model factory and runtime config the pipeline's own agents use
(``src/ai.py``'s ``_build_transcript_model`` over ``get_config()``), so
whatever provider the deployment configured (Groq via LiteLLM in
production) is what answers here. No new keys, no separate provider.

Note this is the CLIP editor's assistant; the clip studio's document AI
edit (``src/services/ai_edit_service.py``) is a separate upstream feature
for a separate editor.
"""

from __future__ import annotations

import logging
from typing import Any, Awaitable, Callable, Optional

from ..auto_director.ai_edit import (
    AI_EDIT_SYSTEM_PROMPT,
    build_ai_edit_prompt,
    describe_ai_edit,
    parse_ai_edit_response,
)
from ..auto_director.clip_edits import apply_segment_operations
from ..caption_templates import get_template, get_template_names
from ..config import get_config
from ..font_registry import get_available_fonts
from ..media.music_library import validate_music_override

logger = logging.getLogger(__name__)

MAX_INSTRUCTION_CHARS = 600

# An async callable (system_prompt, user_prompt) -> raw reply text. The
# default builds the app's configured pydantic-ai agent; tests inject a
# fake so no live model is ever called.
LlmCall = Callable[[str, str], Awaitable[str]]


class AiEditRejected(ValueError):
    """The instruction cannot become a valid edit; the clip is untouched."""


class AiEditUnavailable(RuntimeError):
    """The configured LLM is missing or unreachable; clip untouched."""


_ai_edit_agent: Any = None
_ai_edit_agent_signature: Optional[tuple] = None


def get_ai_edit_agent():
    """Get or create the AI edit agent (lazy, config-cached like ai.py's).

    Built exactly like the niche classifier's agent: runtime settings are
    applied to the process env, a missing provider key is a clear error,
    and the model comes from ``_build_transcript_model`` over the app's
    configured ``llm`` — the same plumbing the pipeline uses.
    """
    global _ai_edit_agent, _ai_edit_agent_signature
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
    if _ai_edit_agent is None or _ai_edit_agent_signature != signature:
        from pydantic_ai import Agent

        from ..ai import _build_transcript_model, _get_missing_llm_key_error
        from ..runtime_settings import apply_settings_to_process_env

        apply_settings_to_process_env(runtime_config.as_runtime_settings())
        config_error = _get_missing_llm_key_error(
            runtime_config.llm, runtime_config
        )
        if config_error:
            raise AiEditUnavailable(config_error)
        _ai_edit_agent = Agent(
            model=_build_transcript_model(runtime_config),
            system_prompt=AI_EDIT_SYSTEM_PROMPT,
        )
        _ai_edit_agent_signature = signature
    return _ai_edit_agent


async def _default_llm_call(system_prompt: str, user_prompt: str) -> str:
    # The agent carries AI_EDIT_SYSTEM_PROMPT as its own system prompt;
    # the parameter exists so injected fakes see the full contract.
    del system_prompt
    agent = get_ai_edit_agent()
    result = await agent.run(user_prompt)
    return result.output


class ClipAiEditService:
    """Translate one instruction into a validated, dry-run editor edit."""

    def __init__(self, task_service: Any, llm_call: Optional[LlmCall] = None):
        self._task_service = task_service
        self._llm_call = llm_call or _default_llm_call

    async def build_edit(
        self, task_id: str, clip_id: str, instruction: str
    ) -> dict[str, Any]:
        """Return ``{operations, captions, music_override, summary}``.

        Raises AiEditRejected for an unusable instruction or reply,
        AiEditUnavailable when the LLM cannot answer, and ValueError from
        the plan loader when the clip/source is unusable. On ANY raise,
        nothing has been queued and the clip is untouched.
        """
        instruction = (instruction or "").strip()
        if not instruction:
            raise AiEditRejected("Describe the edit you want first.")
        if len(instruction) > MAX_INSTRUCTION_CHARS:
            raise AiEditRejected(
                f"Keep the instruction under {MAX_INSTRUCTION_CHARS} "
                "characters — one clear edit at a time."
            )

        plan_response = await self._task_service.get_clip_edit_plan(
            task_id, clip_id
        )
        plan = plan_response["plan"]
        segments = plan.get("segments") or []

        prompt = build_ai_edit_prompt(plan, instruction)
        try:
            raw = await self._llm_call(AI_EDIT_SYSTEM_PROMPT, prompt)
        except AiEditUnavailable:
            raise
        except Exception as exc:
            logger.warning(
                "AI edit LLM call failed for clip %s: %s", clip_id, exc
            )
            raise AiEditUnavailable(
                "The AI editor could not be reached. Try again in a moment."
            ) from exc

        try:
            edit = parse_ai_edit_response(raw)
        except ValueError as exc:
            raise AiEditRejected(
                f"The AI's reply was not a usable edit: {exc}"
            ) from exc

        captions = edit.get("captions")
        if captions:
            self._validate_caption_registries(captions)
        music_override = edit.get("music_override")
        if music_override is not None:
            try:
                music_override = validate_music_override(music_override)
            except ValueError as exc:
                raise AiEditRejected(str(exc)) from exc
            edit["music_override"] = music_override

        operations = edit.get("operations")
        if operations:
            # The dry-run IS the guarantee: the editor's own applier must
            # accept every operation against the clip's current plan, in
            # order, before anything is queued for the worker.
            try:
                apply_segment_operations(segments, operations)
            except ValueError as exc:
                raise AiEditRejected(
                    f"The AI's edit does not fit this clip: {exc}"
                ) from exc

        edit["summary"] = describe_ai_edit(
            segments,
            operations,
            captions=captions,
            music_override=music_override,
        )
        return edit

    @staticmethod
    def _validate_caption_registries(captions: dict[str, Any]) -> None:
        """Mirror the edit route's template/font registry gate."""
        template = captions.get("template")
        if template is not None and template not in get_template_names():
            raise AiEditRejected(
                f"Unknown caption template '{template}'. Valid values: "
                f"{sorted(get_template_names())}."
            )
        font_family = captions.get("font_family")
        if font_family is not None:
            known = any(
                entry.get("filename") == font_family
                for entry in get_available_fonts()
            )
            if not known:
                for name in get_template_names():
                    if get_template(name).get("font_family") == font_family:
                        known = True
                        break
            if not known:
                raise AiEditRejected(
                    f"Unknown caption font_family '{font_family}'."
                )
