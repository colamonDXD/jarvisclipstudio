"""
AI-related functions for transcript analysis with enhanced precision and virality scoring.
"""

from pathlib import Path
from typing import Annotated, List, Dict, Any, Awaitable, Callable, Optional, Literal
import asyncio
import hashlib
import json
import logging
import math
import os
import random
import re

import httpx
from pydantic_ai import Agent, NativeOutput
from pydantic_ai.exceptions import ModelHTTPError
from pydantic_ai.models import Model
from pydantic_ai.models.anthropic import AnthropicModel
from pydantic_ai.models.groq import GroqModel
from pydantic_ai.models.ollama import OllamaModel
from pydantic_ai.models.openrouter import OpenRouterModel
from pydantic_ai.profiles.openai import OpenAIModelProfile
from pydantic_ai.profiles.anthropic import AnthropicModelProfile
from pydantic_ai.providers.anthropic import AnthropicProvider
from pydantic_ai.providers.groq import GroqProvider
from pydantic_ai.providers.ollama import OllamaProvider
from pydantic_ai.providers.openrouter import OpenRouterProvider
from pydantic import AliasChoices, BaseModel, Field, field_validator
from pydantic.json_schema import SkipJsonSchema

from . import provider_manager
from .config import Config, get_config
from .clip_duration import clip_duration
from .runtime_settings import apply_settings_to_process_env

logger = logging.getLogger(__name__)

IDEAL_CLIP_MIN_SECONDS = 25
IDEAL_CLIP_MAX_SECONDS = 50
MIN_ACCEPTED_CLIP_SECONDS = 15
MAX_ACCEPTED_CLIP_SECONDS = 60
TRANSCRIPT_ANALYSIS_CACHE_VERSION = "windowed-rerank-v6"
TRANSCRIPT_ANALYSIS_MAX_ATTEMPTS = 3
TRANSCRIPT_ANALYSIS_TIMEOUT_SECONDS = 600
# Failover candidates get a shorter per-attempt budget than the primary: the
# primary has already proven the route is slow, and several full-length
# stalls in a row would bury the task for the better part of an hour.
ANALYSIS_FAILOVER_TIMEOUT_SECONDS = 300
# Rough English token estimate: transcript text averages about four characters
# per token. Requests above this budget are split into sequential chunks so a
# low-TPM provider (for example Groq's 8k free tier) never sees one huge call.
DEFAULT_TRANSCRIPT_ANALYSIS_MAX_TOKENS = 7000
CHARS_PER_ESTIMATED_TOKEN = 4
# Provider rate limits: retry the same chunk on 413/429 rather than failing a
# multi-chunk analysis that shares one per-model token bucket.
ANALYSIS_RATE_LIMIT_STATUS_CODES = {413, 429}
ANALYSIS_RATE_LIMIT_MAX_ATTEMPTS = 6
ANALYSIS_RATE_LIMIT_INITIAL_DELAY_SECONDS = 20.0
# One chunk may spend this long in total waiting out rate limits. It is a
# separate budget from the per-call timeout: a saturated free-tier bucket
# needs minutes of patience, not a faster failure.
ANALYSIS_RATE_LIMIT_BUDGET_SECONDS = 900.0
ANALYSIS_RATE_LIMITED_MESSAGE = (
    "AI analysis was rate-limited by the AI provider (HTTP 429) and could "
    "not finish, even after slowing down and retrying. Your download and "
    "transcript are saved, so retrying this task resumes at the analysis "
    "step instead of starting over."
)
# Connection-level failures (the gateway container is down, DNS, refused):
# detect them fast instead of burning the transient retry ladder against a
# dead endpoint, and report them as what they are — not as a rate limit and
# not as a generic "temporarily unavailable".
ANALYSIS_CONNECTION_MAX_ATTEMPTS = 2
ANALYSIS_CONNECTION_RETRY_DELAY_SECONDS = 3.0
ANALYSIS_GATEWAY_UNREACHABLE_MESSAGE = (
    "AI analysis could not reach the AI gateway (LiteLLM) — it looks down "
    "or unreachable, so no AI provider could be tried at all. This is not "
    "a provider rate limit. Your download and transcript are saved, and "
    "completed analysis windows are kept: once the gateway is running "
    "again, retrying this task resumes at the analysis step."
)
DEFAULT_ANALYSIS_PACING_FLOOR_SECONDS = 2.0
DEFAULT_ANALYSIS_TPM = 8000
DEFAULT_ANALYSIS_LONGFORM_THRESHOLD_SECONDS = 1800
DEFAULT_ANALYSIS_LONGFORM_WINDOW_SECONDS = 1200
DEFAULT_ANALYSIS_LONGFORM_MAX_TOKENS = 48000
# Long transcripts are analysed in overlapping windows so every part of the
# source gets the model's full attention; candidates are then reranked together.
ANALYSIS_WINDOW_SECONDS = 12 * 60
ANALYSIS_WINDOW_OVERLAP_SECONDS = 2 * 60
ANALYSIS_SINGLE_WINDOW_MAX_SECONDS = 15 * 60
ANALYSIS_WINDOW_CONCURRENCY = 3
ANALYSIS_WINDOW_SEGMENT_TARGET = "3-5"
# Wall-clock budget for all windows together. Each window additionally keeps
# its own TRANSCRIPT_ANALYSIS_TIMEOUT_SECONDS budget once it starts running.
# Outer guard for a whole windowed analysis, failover chains included: the
# per-chunk rate-limit budget alone can legitimately spend 900s.
ANALYSIS_TOTAL_TIMEOUT_SECONDS = 2700
RERANK_TIMEOUT_SECONDS = 240
CANDIDATE_DEDUPE_OVERLAP_RATIO = 0.5
# How far a model timestamp may sit from a transcript line boundary and still be
# snapped onto it instead of the whole clip being dropped.
SEGMENT_SNAP_TOLERANCE_SECONDS = 8
# Final clip count scales with source length: one clip per five minutes,
# never fewer than the single-pass prompt's old maximum and never more than 20.
MIN_FINAL_CLIPS = 5
MAX_FINAL_CLIPS = 20
SECONDS_PER_FINAL_CLIP = 5 * 60
# An explicit per-task clip-count target (chosen on the create form) may ask
# for more than the formula's ceiling, up to this hard cap. It replaces the
# formula for that task only; unset tasks keep the formula exactly.
ABSOLUTE_MAX_FINAL_CLIPS = 30
TRANSIENT_MODEL_STATUS_CODES = {408, 429, 500, 502, 503, 504, 529}
HOOK_TITLE_MAX_CHARS = 64
HOOK_TITLE_MAX_WORDS = 10
# The transcript formatter does not wrap minutes into hours, so sources of 100+
# minutes produce labels such as "100:05".
TRANSCRIPT_SPAN_RE = re.compile(
    r"^\[(?P<start>\d{1,3}:\d{2}(?::\d{2})?)\s*-\s*"
    r"(?P<end>\d{1,3}:\d{2}(?::\d{2})?)\]\s*(?P<text>.*)$"
)
TRANSCRIPT_LINE_PATTERN = re.compile(
    r"^\[(\d{1,3}:\d{2}(?::\d{2})?)\s*-\s*(\d{1,3}:\d{2}(?::\d{2})?)\]\s*(.*)$"
)
SIGNAL_LINE_PATTERN = re.compile(r"^- \[(?P<start>\d{1,3}:\d{2}(?::\d{2})?)\s*-")
SPEAKER_PREFIX_PATTERN = re.compile(r"^Speaker [^:]+:\s*")


def _normalize_transcript_text(value: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9']+", " ", value.lower())).strip()


def _parse_transcript_lines(transcript: str) -> List[Dict[str, Any]]:
    lines: List[Dict[str, Any]] = []
    for raw_line in transcript.splitlines():
        match = TRANSCRIPT_LINE_PATTERN.match(raw_line.strip())
        if not match:
            continue
        line_text = SPEAKER_PREFIX_PATTERN.sub("", match.group(3).strip())
        lines.append(
            {
                "start_time": match.group(1),
                "end_time": match.group(2),
                "start_seconds": _parse_transcript_timestamp_seconds(match.group(1)),
                "end_seconds": _parse_transcript_timestamp_seconds(match.group(2)),
                "text": line_text,
            }
        )
    return lines


def _snap_bounds_to_transcript_lines(
    transcript_lines: List[Dict[str, Any]],
    start_seconds: int,
    end_seconds: int,
    tolerance_seconds: int = SEGMENT_SNAP_TOLERANCE_SECONDS,
) -> Optional[tuple[Dict[str, Any], Dict[str, Any]]]:
    """Snap model timestamps onto the nearest transcript line start and end.

    Models often pick a second that sits inside a line rather than on its
    boundary. Grounding needs exact line labels, so move each bound to the
    closest boundary within the tolerance instead of discarding the clip.
    """
    if not transcript_lines:
        return None

    start_line = min(
        transcript_lines,
        key=lambda line: (abs(line["start_seconds"] - start_seconds), line["start_seconds"]),
    )
    if abs(start_line["start_seconds"] - start_seconds) > tolerance_seconds:
        return None

    end_candidates = [
        line
        for line in transcript_lines
        if line["end_seconds"] > start_line["start_seconds"]
    ]
    if not end_candidates:
        return None
    end_line = min(
        end_candidates,
        key=lambda line: (abs(line["end_seconds"] - end_seconds), -line["end_seconds"]),
    )
    if abs(end_line["end_seconds"] - end_seconds) > tolerance_seconds:
        return None
    return start_line, end_line


def _extract_transcript_text_for_segment(
    transcript_lines: List[Dict[str, str]],
    start_time: str,
    end_time: str,
) -> Optional[str]:
    for start_index, line in enumerate(transcript_lines):
        if line["start_time"] != start_time:
            continue

        collected_parts = [line["text"]] if line["text"] else []
        if line["end_time"] == end_time:
            return " ".join(part for part in collected_parts if part).strip()

        for next_line in transcript_lines[start_index + 1 :]:
            if next_line["text"]:
                collected_parts.append(next_line["text"])
            if next_line["end_time"] == end_time:
                return " ".join(part for part in collected_parts if part).strip()

        return None

    return None


class ViralityAnalysis(BaseModel):
    """Detailed virality breakdown for a segment."""

    hook_score: int = Field(
        default=15,
        description="How strong is the opening hook (0-25)",
        ge=0,
        le=25,
    )
    engagement_score: int = Field(
        default=15,
        description="How engaging/entertaining is the content (0-25)",
        ge=0,
        le=25,
    )
    value_score: int = Field(
        default=15,
        description="Educational/informational value (0-25)",
        ge=0,
        le=25,
    )
    shareability_score: int = Field(
        default=15,
        description="Likelihood of being shared (0-25)",
        ge=0,
        le=25,
    )
    total_score: int = Field(
        default=60,
        description="Combined virality score (0-100)",
        ge=0,
        le=100,
    )
    hook_type: Optional[
        Literal["question", "statement", "statistic", "story", "contrast", "none"]
    ] = Field(
        default="none",
        description="Type of hook: question, statement, statistic, story, contrast, or none",
    )
    category: Optional[
        Literal[
            "hooks",
            "emotional_peaks",
            "opinion_bombs",
            "revelations",
            "conflict",
            "quotable_lines",
            "story_peaks",
            "practical_value",
        ]
    ] = Field(
        default=None,
        description=(
            "The single virality category this clip best fits: hooks, "
            "emotional_peaks, opinion_bombs, revelations, conflict, "
            "quotable_lines, story_peaks, or practical_value"
        ),
    )
    virality_reasoning: str = Field(
        default="The model did not provide a detailed virality breakdown.",
        description="Explanation of the virality score",
    )


def _default_virality_analysis() -> ViralityAnalysis:
    return ViralityAnalysis()


class TranscriptSegment(BaseModel):
    """Represents a relevant segment of transcript with precise timing and virality analysis."""

    start_time: str = Field(description="Start timestamp in MM:SS format")
    end_time: str = Field(description="End timestamp in MM:SS format")
    text: str = Field(
        validation_alias=AliasChoices("text", "segment"),
        description=(
            "Transcript text taken only from the selected timestamp range. "
            "Keep it verbatim or near-verbatim, and do not paraphrase or merge non-contiguous lines."
        )
    )
    relevance_score: float = Field(
        default=0.75,
        description="Relevance score from 0.0 to 1.0", ge=0.0, le=1.0
    )
    reasoning: str = Field(
        default="Selected by the AI model as a clip candidate.",
        description=(
            "Brief factual explanation of why this exact segment works as a clip. "
            "Base it only on the provided transcript content."
        )
    )
    virality: ViralityAnalysis = Field(
        default_factory=_default_virality_analysis,
        description="Detailed virality score breakdown",
    )
    hook_title: Optional[str] = Field(
        default=None,
        validation_alias=AliasChoices("hook_title", "title", "headline"),
        description=(
            "Short punchy on-screen title for the clip (3-9 words). Grounded in "
            "the segment content, no hashtags, no emojis, no surrounding quotes. "
            "THIRD PERSON ONLY: the clip is posted by a clipper sharing someone "
            "else's moment, so never write the speaker's \"I\", \"I'm\", \"my\" "
            "or \"we\" — name the subject or describe the moment neutrally."
        ),
    )

    # Campaign-brief verdict for this candidate (Content Requirements),
    # set by the brief-aware selection path; None on every non-brief task.
    # SkipJsonSchema keeps the field out of the LLM output schema, so the
    # analysis request stays byte-identical for non-brief tasks.
    brief_verdict: Annotated[Optional[Dict[str, Any]], SkipJsonSchema()] = Field(
        default=None, exclude=True
    )
    hook_start_time: Optional[str] = Field(
        default=None,
        description=(
            "Optional MM:SS start of the single strongest 2-5 second line inside "
            "the segment, replayed as a cold-open teaser. Null when the segment "
            "already opens on its best line."
        ),
    )
    hook_end_time: Optional[str] = Field(
        default=None,
        description="Optional MM:SS end of that hook line (null with hook_start_time).",
    )

    @field_validator("relevance_score", mode="before")
    @classmethod
    def _coerce_percent_relevance_score(cls, value: Any) -> Any:
        if value is None:
            return value
        try:
            numeric_value = float(value)
        except (TypeError, ValueError):
            return value
        if numeric_value > 1 and numeric_value <= 100:
            return numeric_value / 100
        return value


class TranscriptAnalysis(BaseModel):
    """Analysis result for transcript segments with virality scores."""

    most_relevant_segments: List[TranscriptSegment]
    summary: str = Field(description="Brief summary of the video content")
    key_topics: List[str] = Field(description="List of main topics discussed")
    # Brief search-outcome report (Content Requirements); None without a
    # brief. SkipJsonSchema: see TranscriptSegment.brief_verdict.
    brief_report: Annotated[Optional[Dict[str, Any]], SkipJsonSchema()] = Field(
        default=None, exclude=True
    )


# Enhanced system prompt with virality scoring
transcript_analysis_system_prompt = """You are an expert transcript analyst for short-form video editing.

Your job is extraction and ranking, not creative rewriting. You must stay fully grounded in the transcript and choose the best clip candidates that already exist in the source material.

OUTPUT CONTRACT:
- Return valid JSON only. Do not output Markdown, headings, bullets, prose, code fences, explanations, or commentary outside the JSON object.
- The top-level JSON object must include: "most_relevant_segments", "summary", and "key_topics".
- Each item in "most_relevant_segments" must include: "start_time", "end_time", "text", "relevance_score", "reasoning", "virality", and "hook_title".
- Do not use "segment" as an output field. Use "text".
- "virality" must include: "hook_score", "engagement_score", "value_score", "shareability_score", "total_score", "hook_type", "category", and "virality_reasoning".
- Every returned segment must be {minimum}-{maximum} seconds long. Prefer {ideal_minimum}-{ideal_maximum} seconds.

CORE OBJECTIVES:
1. Identify segments that would be compelling on social media platforms
2. Focus on complete thoughts, insights, or entertaining moments
3. Prioritize content with hooks, emotional moments, or valuable information
4. Each segment should be engaging and worth watching
5. Score each segment's viral potential with detailed breakdown

GROUNDING RULES:
1. Use only the provided transcript lines and timestamps
2. Never invent facts, tone, context, or transitions that are not present
3. Treat this as span selection over a timestamped transcript, not open-ended summarization
4. Each selected segment must map to one contiguous range in the transcript
5. segment.text must match the chosen span closely and must not include content from outside the chosen range
6. Do not stitch together distant moments into one clip
7. If a speaker label appears, use it only if it is part of the spoken content and helps clarity

CONTENT NEUTRALITY RULES:
1. This is clipping software for legitimate editing workflows
2. Do not judge, moralize, or downgrade a segment just because the topic is controversial, sensitive, adult, political, criminal, medical, or otherwise intense
3. Evaluate segments only on clip quality: clarity, self-contained value, hook strength, emotional impact, specificity, and shareability
4. Do not refuse analysis just because the speaker describes risky, offensive, or uncomfortable subject matter
5. Only downgrade a segment when the transcript itself is weak, confusing, repetitive, unusable, or a poor standalone clip

SEGMENT SELECTION CRITERIA:
1. STRONG HOOKS: Attention-grabbing opening lines
2. VALUABLE CONTENT: Tips, insights, interesting facts, stories
3. EMOTIONAL MOMENTS: Excitement, surprise, humor, inspiration
4. COMPLETE THOUGHTS: Self-contained ideas that make sense alone
5. ENTERTAINING: Content people would want to share
6. HIGH SIGNAL: Prefer specific, concrete language over vague discussion
7. LOW FILLER: Avoid greetings, sponsor reads, repeated setup, throat-clearing, and housekeeping unless they are unusually compelling

WHAT A GOOD CLIP FEELS LIKE:
- A viewer should understand and care without the original title, thumbnail, or previous context
- Prefer a complete mini-story or argument: setup, tension or claim, specific detail, and payoff
- Expand a great short moment to nearby contiguous lines when that adds needed setup, stakes, or payoff
- Strong picks include contrarian claims, mistakes or lessons, concrete examples, before/after moments, frameworks, surprising results, emotionally charged reactions, and complete answers to interesting questions
- Bad picks include intros, sponsor or CTA sections, vague setup, contextless quote fragments, repeated points, definitions without payoff, meandering background, and answer fragments that require unseen context

SCORING — HARD GATES FIRST, THEN THE /100 RUBRIC:
Step 1 — the four gates. A candidate that fails ANY gate is not a clip, whatever its other merits; do not return it:
(a) The first sentence needs no context: a cold viewer understands what is happening and what is at stake from the opening line alone. Back-references ("As I mentioned earlier...", "Going back to what we discussed..."), pronouns whose referent never appears inside the clip, and a first line cut mid-sentence all fail this gate.
(b) One complete idea: the window holds one whole mini-arc, with no mid-thought start or end.
(c) No intro, outro, sponsor or crosstalk material: channel furniture, ad reads, greetings and two people talking over each other are not moments.
(d) No unowned media: the clip's value cannot rest on music or footage the speaker does not own or control.

Step 2 — score each survivor out of 100:
- Hook in the first 3 seconds (25): the opening states or dramatises the stakes — a price, a claim, a question, a reaction — something that makes stopping cost the viewer information.
- Stands alone (20): fully satisfying with zero knowledge of the source video.
- Payoff / turn (20): something lands inside the window — a reveal, a verdict, a result, a twist. Setup without landing scores near 0 here.
- Emotion and energy (15): a felt peak — surprise, disbelief, laughter, tension — that visibly or audibly peaks, not just talks.
- Specifics (10): concrete numbers, names, prices, durations. Specifics are what viewers repeat to each other.
- Ending (10): the clip ends ON the payoff or a quotable line, not on trailing chatter after the peak.

Convert the rubric judgement into the four subscores:
- "hook_score" (0-25) = the Hook points.
- "engagement_score" (0-25) = Emotion and energy + Ending.
- "value_score" (0-25) = (Payoff + Specifics) scaled from 30 down to 25.
- "shareability_score" (0-25) = Stands alone scaled from 20 up to 25.
- "total_score" = the sum of the four subscores.
Decision bands: 70 and above is ship-worthy. Return a 55-69 segment only when the window offers nothing at 70+. Never return a segment under 55.

Hook calibration — judge the opening's archetype first, then its strength inside the archetype's band:
- Bold / Contrarian claim: 80-100, the strongest openers
- Curiosity Gap: 75-95
- Value Promise: 70-90
- Pattern Interrupt: 70-90
- Payoff Preview: 65-85
- Mid-Action opening: 60-80
- Hidden Knowledge: 60-80
- Weak / Generic opener: 10-40, and it caps hook_score at 10
Boosters: add +5-10 to the total when the opening carries a specific number, names a recognisable entity, or implies personal experience — always inside the component caps, never above them.

For every segment, set "category" to the single virality category it best fits: "hooks", "emotional_peaks", "opinion_bombs", "revelations", "conflict", "quotable_lines", "story_peaks", or "practical_value". "virality_reasoning" must say why this clip works in one or two sentences: name the category and the concrete moment that earns the score.

SLATE SHAPE:
- Prefer segments at least 2 minutes apart in the source and spread across different subtopics. Two segments covering the same moment waste the slate.

CALIBRATION EXAMPLES (editor-validated clips from a DIFFERENT video — a "$1 vs $10,000 restaurants in Japan" tour; calibrate your scoring against their shape, they are not candidates):
- 14:50-15:10 (20s, scored 92): opens mid-taste on a genuine reaction ("That's pretty good avocado. Wow."), then lands a concrete, surprising reveal inside the window — the avocado came from a 700-year-old tree.
- 28:11-28:38 (27s, scored 90): an absurd-but-specific production detail — the wagyu cows get jazz massages, for 30 years — a quotable fact sitting on the video's $1-vs-$10,000 contrast spine.
- 22:04-22:29 (25s, scored 88): a textbook hook-to-payoff arc — opens on the price question itself ("$400 per sandwich?"), runs a short disbelief negotiation, and ends on a second reveal held back to the final seconds: there is real gold in the sandwich.
- 40:39-41:09 (30s, scored 86): the presentation-and-reaction peak — the Kobe sandwich arrives and the reaction escalates on camera ("It's talking to me. It says eat, bro.").
- 42:24-42:52 (28s, scored 84): pure price-shock arithmetic — four pieces of sushi cost $400; a specific count against a specific price, graspable in the first seconds and fully self-contained.
Notice what they share: the first sentence IS the hook, one complete idea per clip, and the ending lands on the payoff.

HOOK TITLES ("hook_title" per segment):
- Write a short on-screen headline (3-9 words) that is burned into the top of the clip
- It must make a scrolling viewer stop: a bold claim, curiosity gap, number, or stakes taken directly from the segment
- Stay grounded: only promise what the clip actually delivers; never invent facts or numbers
- Do not simply repeat the first spoken words verbatim; reframe them as a headline
- Plain text only: no hashtags, no emojis, no quotes around the title
- Write it in the same language as the transcript
- THIRD PERSON ONLY — the person posting the clip is a CLIPPER sharing another creator's moment, not the speaker. Never write the speaker's first person: no "I", "I'm", "I've", "my", "we", "our" claiming the speaker's actions or possessions. Refer to the speaker by name when known, or describe the moment neutrally ("He Bought the Most Expensive Sandwich", "Inside a $20k Gym Tour"). Addressing the viewer ("you") is fine; claiming the speaker's "I" is not.
- Good examples: "The $40k Mistake Everyone Keeps Making", "Why nobody tells you this about VC", "Do this before your next interview"
- "problem" style (when the profile's hook title style says so): name the concrete problem this segment addresses — a cost, a mistake, a risk, something broken or overpaid — using only facts the segment states; a problem the segment never shows must not be promised
- "curiosity_gap" style (when the profile's hook title style says so): name the subject and withhold the ONE specific fact this segment reveals (the ingredient, the amount, the reason, the outcome); never withhold something the segment does not actually deliver, and never hide the subject itself

CLIP BOUNDARIES — OPENING AND ENDING:
- start_time is the first word of a sentence a new viewer can understand with no prior context
- Do not start mid-sentence, on a filler (like, dude, um, well), on a bare agreement (yes, yeah, right, exactly, totally), or on a pronoun that points at someone the viewer has not met yet (he, she, they)
- Do not start on a back-reference ("as I said", "the thing we mentioned", "that point", "before we started recording") unless the clip also contains the thing being referenced
- If the moment only makes sense with the previous sentence's noun, claim, or question, move start_time back to include that sentence
- end_time is the last word of the sentence that lands the point. Do not stop mid-clause, and do not keep the opening words of the next unrelated thought
- The clip's real first sentence is the hook. Do not choose a weak opening because a later line could be replayed in front of it
- Judge the first spoken seconds on their own, separately from hook_title: a viewer who hears only them must know the subject or claim
- Skip lead-in banter before the point (calling someone over, "No. No? Ah!", greetings, reactions to something off-screen) and start on the sentence that delivers it
- Do not start on a one-word answer to a question the clip leaves out ("Catharsis."), or on a conjunction that continues an earlier point ("But the main reason...", "And then...", "So that's why..."); start where the thought stands alone, or include the question or claim it answers
- Lower hook_score for a segment whose opening still depends on missing context

TRIPLE HOOK (the first 2 seconds):
- The strongest openings align all three at once: the spoken hook line, a hook_title that works on screen from frame 1, and something visibly changing within the first 2 seconds (a speaker switch, a gesture or reaction, a camera move, a new slide or topic)
- Prefer start times that land on or just before such a visual change, and lower hook_score when the opening seconds are visually static, even if the words are strong

VISUAL SUITABILITY:
- The clip is a video. Avoid segments where the speech says the picture is interrupted, unavailable or broken: a technical problem, "these images cannot be shown", "we'll be right back", a frozen or lost stream, a test or placeholder screen
- Those passages usually play over a still slate or black, so they are not ready-to-post highlights no matter how strong the words are

COLD-OPEN HOOK ("hook_start_time"/"hook_end_time" per segment, optional):
- Prefer null. The clip is played from start_time; a later line is not replayed in front of it unless an operator explicitly enables that
- Only set a range when a 2-5 second punchline later in the segment stands completely alone AND the real opening is already a full setup
- Use timestamps from the transcript lines inside the segment, at least 3 seconds after start_time
- Set both to null when the clip already opens on its strongest line, or when replaying the line would just repeat it

TRUE CRIME / MYSTERY NARRATION:
- When the content is a true-crime case or a mystery story, open on the strangest unexplained fact of the case, not on background, introductions or host banter
- Keep the outcome withheld: prefer segments whose resolution, verdict or reveal lands in the final 15% of the clip, and lower the score for segments that give away the outcome early

HOOK TYPES to identify:
- "question": Opens with a question that creates curiosity
- "statement": Bold claim or surprising statement
- "statistic": Uses compelling numbers or data
- "story": Starts with narrative/anecdote
- "contrast": Before/after or problem/solution framing
- "none": No clear hook pattern

TIMING GUIDELINES:
- Target {ideal_minimum}-{ideal_maximum} seconds for most clips
- Shorter picks within the requested range must still be self-contained and complete
- CRITICAL: start_time MUST be different from end_time (minimum {minimum} seconds apart)
- Focus on natural content boundaries rather than arbitrary time limits
- Include enough context for the segment to be understandable
- Stay within the requested duration range
- Start at the hook or the minimum setup needed to make the hook land, and end after the payoff
- If a highlight is only one good line, expand to include the surrounding setup and payoff rather than returning a tiny fragment
- Stop expanding when the topic drifts, the speaker repeats the same point, or the clip loses momentum

TIMESTAMP REQUIREMENTS - EXTREMELY IMPORTANT:
- Use EXACT timestamps as they appear in the transcript
- Never modify timestamp format (keep MM:SS structure)
- start_time MUST be LESS THAN end_time (start_time < end_time)
- MINIMUM segment duration: {minimum} seconds (end_time - start_time >= {minimum} seconds)
- MAXIMUM segment duration: {maximum} seconds
- IDEAL segment duration: {ideal_minimum}-{ideal_maximum} seconds
- Look at transcript ranges like [02:25 - 02:35] and use different start/end times
- NEVER use the same timestamp for both start_time and end_time
- Example: start_time: "02:25", end_time: "02:35" (NOT "02:25" and "02:25")

SCORING AND OUTPUT RULES:
- relevance_score should reflect how well the segment works as a standalone short clip, not just whether the topic is generally important
- Penalize clips that are only quotable but not self-contained, too generic, missing setup, missing payoff, or padded with filler
- virality_reasoning and reasoning should cite what is actually present in the chosen span
- summary and key_topics must also stay grounded in the transcript and should not add outside interpretation

Find the number of compelling segments requested in the task (usually 2-5) that would work well as standalone clips. Quality over quantity: choose fewer stronger segments over filling a quota. Every selected segment must be accurate, self-contained, have proper time ranges, and score high on virality metrics."""

def build_transcript_system_prompt(clip_length: str = "auto") -> str:
    bounds = clip_duration(clip_length)
    return transcript_analysis_system_prompt.format(
        minimum=bounds.minimum, maximum=bounds.maximum,
        ideal_minimum=bounds.ideal_minimum, ideal_maximum=bounds.ideal_maximum,
    )


# Lazy-loaded agents to avoid import-time failures when API keys aren't set.
# The single-model path keeps its original global; rotated analysis models get
# one cached agent each.
_transcript_agent: Optional[Agent[None, TranscriptAnalysis]] = None
_transcript_agent_signature: Optional[tuple[str | None, ...]] = None
_transcript_agents_by_model: dict[
    str, tuple[tuple[str | None, ...], Agent[None, TranscriptAnalysis]]
] = {}

SUPPORTED_LLM_PROVIDERS = {"google", "google-gla", "openai", "openai-chat", "openrouter", "anthropic", "groq", "ollama"}


def _split_llm_name(model_name: str) -> tuple[str, str | None]:
    if ":" not in model_name:
        return model_name.strip().lower(), None

    provider, provider_model_name = model_name.split(":", 1)
    return provider.strip().lower(), provider_model_name.strip() or None


def _get_missing_llm_key_error(model_name: str, runtime_config: Config) -> Optional[str]:
    """Return a clear configuration error when the selected LLM key is missing."""
    provider, provider_model_name = _split_llm_name(model_name)

    if provider not in SUPPORTED_LLM_PROVIDERS:
        return (
            f"Unsupported LLM provider '{provider}'. "
            "Use google-gla:*, openai-chat:*, openai:*, openrouter:*, anthropic:*, groq:*, or ollama:*."
        )

    if not provider_model_name:
        return (
            "Selected LLM is missing a model name. "
            "Use the format provider:model, for example ollama:gpt-oss:20b."
        )

    if provider in {"google", "google-gla"} and not runtime_config.google_api_key:
        return (
            "Selected LLM provider is Google, but GOOGLE_API_KEY is not set. "
            "Set GOOGLE_API_KEY or set LLM to openai-chat:* / anthropic:* / ollama:* with the matching API key."
        )

    if provider == "openrouter" and not runtime_config.openrouter_api_key:
        return "Selected LLM provider is OpenRouter, but OPENROUTER_API_KEY is not set."

    if provider == "openrouter" and "/" not in provider_model_name:
        return "OpenRouter model must include its vendor, for example openrouter:anthropic/claude-sonnet-5.5."

    # "openai-chat" is the canonical spelling of the OpenAI provider; the
    # deprecated "openai:" spelling still names the same provider and key.
    if provider in {"openai", "openai-chat"} and not runtime_config.openai_api_key:
        return (
            "Selected LLM provider is OpenAI, but OPENAI_API_KEY is not set. "
            "Set OPENAI_API_KEY or choose another provider with a matching API key."
        )

    if provider == "anthropic" and not runtime_config.anthropic_api_key:
        return (
            "Selected LLM provider is Anthropic, but ANTHROPIC_API_KEY is not set. "
            "Set ANTHROPIC_API_KEY or choose another provider with a matching API key."
        )

    if provider == "groq" and not os.getenv("GROQ_API_KEY"):
        return (
            "Selected LLM provider is Groq, but GROQ_API_KEY is not set. "
            "Set GROQ_API_KEY or choose another provider with a matching API key."
        )

    if provider == "ollama":
        # Ollama can run locally without an API key. OLLAMA_BASE_URL/OLLAMA_API_KEY
        # are optional and passed through as environment variables.
        return None

    return None


def _uses_native_output(model_name: str) -> bool:
    provider, name = _split_llm_name(model_name)
    return provider == "openrouter" or (
        provider == "anthropic" and name == "claude-sonnet-5-5"
    )


def _build_transcript_model(
    runtime_config: Config, model_name: str | None = None
) -> Model | str:
    model_name = model_name or runtime_config.llm
    provider, provider_model_name = _split_llm_name(model_name)
    if provider == "anthropic" and _uses_native_output(model_name):
        # The installed SDK predates Sonnet 5.5. This model requires native
        # structured output rather than forced tool calls.
        profile = AnthropicProvider.model_profile(provider_model_name).update(
            AnthropicModelProfile(supports_json_schema_output=True)
        )
        return AnthropicModel(
            provider_model_name,
            provider=AnthropicProvider(api_key=runtime_config.anthropic_api_key),
            profile=profile,
            settings={"max_tokens": 8192},
        )
    if provider == "openrouter":
        fallback = runtime_config.openrouter_fallback_model
        if not provider_model_name or "/" not in provider_model_name or "/" not in fallback:
            raise RuntimeError("OpenRouter primary and fallback models must use vendor/model IDs.")
        # The installed SDK predates Sonnet 5.5. Both selected models advertise
        # native JSON schema output; require supporting endpoints when routing.
        profile = OpenRouterProvider.model_profile(provider_model_name).update(
            OpenAIModelProfile(supports_json_schema_output=True)
        )
        return OpenRouterModel(
            provider_model_name,
            provider=OpenRouterProvider(api_key=runtime_config.openrouter_api_key),
            profile=profile,
            settings={
                "openrouter_models": [fallback] if fallback != provider_model_name else [],
                "openrouter_provider": {"allow_fallbacks": True, "require_parameters": True, "data_collection": "deny"},
                "openrouter_reasoning": {"effort": "low"},
                "max_tokens": 8192,
            },
        )
    if provider == "groq":
        return GroqModel(
            provider_model_name,
            provider=GroqProvider(api_key=os.getenv("GROQ_API_KEY")),
        )

    if provider != "ollama":
        return model_name

    if not provider_model_name:
        raise RuntimeError(
            "Selected LLM provider is Ollama, but no model name was provided. "
            "Use the format ollama:<model>, for example ollama:gpt-oss:20b."
        )

    return OllamaModel(
        provider_model_name,
        provider=OllamaProvider(
            base_url=runtime_config.resolve_ollama_base_url(),
            api_key=runtime_config.ollama_api_key,
        ),
    )


def _transcript_agent_signature_for(
    clip_length: str, model_name: str, runtime_config: Config
) -> tuple[str | None, ...]:
    return (
        clip_length,
        model_name,
        runtime_config.openai_api_key,
        runtime_config.openrouter_api_key,
        runtime_config.openrouter_fallback_model,
        runtime_config.google_api_key,
        runtime_config.anthropic_api_key,
        runtime_config.ollama_base_url,
        runtime_config.ollama_api_key,
    )


def _create_transcript_agent(
    runtime_config: Config, model_name: str, clip_length: str
) -> Agent[None, TranscriptAnalysis]:
    apply_settings_to_process_env(runtime_config.as_runtime_settings())
    config_error = _get_missing_llm_key_error(model_name, runtime_config)
    if config_error:
        raise RuntimeError(config_error)

    return Agent[None, TranscriptAnalysis](
        model=_build_transcript_model(runtime_config, model_name),
        output_type=NativeOutput(TranscriptAnalysis, strict=True)
        if _uses_native_output(model_name)
        else TranscriptAnalysis,
        system_prompt=build_transcript_system_prompt(clip_length),
        # Some local Ollama/OpenAI-compatible endpoints can return formatted
        # prose before settling on schema-valid JSON. Keep retries limited
        # while still allowing enough repair attempts for local models.
        retries={'output': 2},
    )


def _wrap_transcript_agent(
    agent: Agent[None, TranscriptAnalysis],
    model_name: str,
    runtime_config: Config,
) -> Agent[None, TranscriptAnalysis]:
    """Wrap a freshly built agent for provider-manager accounting.

    With the provider manager enabled, runs are recorded (token usage +
    failure classification feeding the cooldown registry). The wrapped agent
    is what gets cached, so cache identity is unchanged. With the manager
    disabled the agent is returned untouched.
    """
    if provider_manager.manager_enabled(runtime_config):
        return provider_manager.ManagedAgentProxy(
            agent, alias=model_name, purpose="analysis"
        )
    return agent


def get_transcript_agent(
    clip_length: str = "auto", model_name: str | None = None
) -> Agent[None, TranscriptAnalysis]:
    """Get or create a transcript analysis agent (lazy initialization).

    With ``model_name`` omitted the original single-model cache is used. A
    supplied name caches one agent per model so chunk rotation does not rebuild
    providers on every call.
    """
    global _transcript_agent, _transcript_agent_signature
    runtime_config = get_config()
    selected_model = model_name or runtime_config.llm
    signature = _transcript_agent_signature_for(
        clip_length, selected_model, runtime_config
    )

    if model_name is None:
        if _transcript_agent is None or _transcript_agent_signature != signature:
            _transcript_agent = _wrap_transcript_agent(
                _create_transcript_agent(
                    runtime_config, selected_model, clip_length
                ),
                selected_model,
                runtime_config,
            )
            _transcript_agent_signature = signature
        return _transcript_agent

    cached = _transcript_agents_by_model.get(selected_model)
    if cached is None or cached[0] != signature:
        agent = _wrap_transcript_agent(
            _create_transcript_agent(runtime_config, selected_model, clip_length),
            selected_model,
            runtime_config,
        )
        _transcript_agents_by_model[selected_model] = (signature, agent)
        return agent
    return cached[1]


class RankedCandidate(BaseModel):
    """One candidate's place in the comparative rerank."""

    candidate_id: int = Field(description="The id of the candidate being ranked")
    score: int = Field(
        description="Comparative virality score (0-100) relative to the other candidates",
        ge=0,
        le=100,
    )
    reason: str = Field(
        default="",
        description="One short sentence on why it ranks here versus the others",
    )


class CandidateRanking(BaseModel):
    """Best-first ranking of clip candidates from one source video."""

    ranking: List[RankedCandidate] = Field(
        description="Every candidate, ordered from strongest to weakest clip"
    )


candidate_rerank_system_prompt = """You are a senior short-form video editor choosing which clips from one long video to publish.

You receive candidate clips that were picked from different parts of the same video. Each one was scored in isolation, so those scores are not comparable. Your job is to compare the candidates against each other and produce one calibrated, best-first ranking.

Judge each candidate only on its own transcript text:
1. Hook: do the first one or two sentences make a scrolling viewer stop? Judge them apart from hook_title; lead-in banter, a lone one-word answer, or an opening conjunction ("But the main reason...") that leans on unseen context is a weak hook.
2. Standalone: would a viewer understand and care without the rest of the video?
3. Payoff: does it land a clear point, punchline, lesson, or story beat before it ends?
4. Specificity and energy: concrete details, numbers, strong opinions, emotion, or humor beat vague talk.
5. Shareability: would someone send this to a friend or save it?

Calibrate like the first pass: a candidate whose first sentence leans on missing context (a back-reference such as "As I mentioned earlier...", a pronoun with no referent inside the clip, a mid-sentence opening), that holds no complete idea, or that is intro, sponsor or crosstalk material ranks last whatever its first-pass score says. Judge hook strength by archetype — Bold/Contrarian openers are strongest, then Curiosity Gap, Value Promise and Pattern Interrupt; Weak/Generic openers are weakest — and reward an opening that carries a specific number, a named entity, or a personal-experience claim. Prefer a slate whose picks sit at least 2 minutes apart in the source and cover different subtopics over two picks of the same moment.

Rules:
- Stay content-neutral: never downgrade a candidate just because its topic is sensitive, controversial, or adult.
- Rank a candidate last when its speech says the picture is interrupted, unavailable or broken (a technical problem, "these images cannot be shown"); it plays over a slate, not footage.
- Rank every candidate exactly once, using the given candidate_id values.
- Scores are comparative: spread them out so better clips clearly score higher. The best candidate should usually land between 80 and 95; weak, contextless, or filler candidates belong below 50.
- Return valid JSON only, with a "ranking" list ordered from best to worst."""

_rerank_agent: Optional[Agent[None, CandidateRanking]] = None
_rerank_agent_signature: Optional[tuple[str | None, ...]] = None


def get_rerank_agent() -> Agent[None, CandidateRanking]:
    """Get or create the candidate rerank agent (lazy initialization)."""
    global _rerank_agent, _rerank_agent_signature
    runtime_config = get_config()
    model_name = getattr(runtime_config, "rerank_llm", None) or runtime_config.llm
    provider, _ = _split_llm_name(model_name)
    signature = (
        model_name,
        runtime_config.openai_api_key,
        runtime_config.openrouter_api_key,
        runtime_config.openrouter_fallback_model,
        runtime_config.google_api_key,
        runtime_config.anthropic_api_key,
        runtime_config.ollama_base_url,
        runtime_config.ollama_api_key,
    )
    if _rerank_agent is None or _rerank_agent_signature != signature:
        apply_settings_to_process_env(runtime_config.as_runtime_settings())
        config_error = _get_missing_llm_key_error(model_name, runtime_config)
        if config_error:
            raise RuntimeError(config_error)

        _rerank_agent = Agent[None, CandidateRanking](
            model=_build_transcript_model(runtime_config, model_name),
            output_type=NativeOutput(CandidateRanking, strict=True) if _uses_native_output(model_name) else CandidateRanking,
            system_prompt=candidate_rerank_system_prompt,
            retries={'output': 2},
        )
        _rerank_agent_signature = signature
    return _rerank_agent


class BRollCue(BaseModel):
    """One stock-footage cutaway inside a finished clip."""

    start: float = Field(
        description="Clip second where the cutaway starts: the timestamp of the line that names the subject"
    )
    duration: float = Field(description="Seconds to show the footage, 2 to 4")
    query: str = Field(
        description="2-4 word English stock-footage search describing what the footage shows"
    )
    fallback_query: str = Field(
        default="",
        description="Broader 1-2 word English search used when the first finds nothing",
    )


class BRollPlan(BaseModel):
    """Stock-footage cutaways for one clip, in play order."""

    cues: List[BRollCue] = Field(description="Cutaways in play order; empty when nothing fits")


broll_plan_system_prompt = """You are a short-form video editor adding stock-footage cutaways (B-roll) to a vertical talking-head clip.

The footage plays full screen for a few seconds while the speaker keeps talking and the captions stay on screen. Good B-roll shows exactly the thing being said at that moment. Loose or generic footage makes a clip feel cheap, so leave a moment uncovered rather than force a weak match.

Choose a moment only when the speaker names something concrete and filmable: an object, a place, an activity, or a scene (for example "biking to work", "Mount Kilimanjaro", "a stack of cash", "writing a novel").
Skip abstract ideas, opinions, feelings without a scene, jokes, and anything about the speaker themselves on camera.
Never cover a moment where the speaker points at something the viewer is meant to see ("like this", "look at this", "here's what it looked like", "this slide"): the original picture is the payoff there.

Rules:
- "start" is the timestamp of the line that names the subject, so the footage appears as the word is spoken.
- "duration" is 2 to 4 seconds and must end before the next cutaway starts.
- "query" is a 2-4 word English description of what the footage shows ("man cycling city street", "mountain summit hikers", "hands typing laptop"), even when the clip is in another language. No people's names, brands, logos, text, charts or screenshots: stock libraries do not have them.
- Match the tone of what is said. Describe the scene literally and neutrally ("dark quiet bedroom", not "lonely night"), and never imply illness, pills, drugs, distress, violence or death unless the speaker is talking about exactly that: stock footage tagged that way changes what the clip seems to say.
- "fallback_query" is a broader 1-2 word English search ("cycling", "mountain").
- Never place a cutaway in the opening seconds or the final second: the viewer needs to see the speaker.
- Return valid JSON only, with a "cues" list. Return an empty list when no moment has a concrete, filmable subject."""

_broll_agent: Optional[Agent[None, BRollPlan]] = None
_broll_agent_signature: Optional[tuple[str | None, ...]] = None
BROLL_PLAN_TIMEOUT_SECONDS = 60


def get_broll_agent() -> Agent[None, BRollPlan]:
    """Get or create the B-roll planning agent (lazy initialization)."""
    global _broll_agent, _broll_agent_signature
    runtime_config = get_config()
    model_name = runtime_config.llm
    signature = (
        model_name,
        runtime_config.openai_api_key,
        runtime_config.openrouter_api_key,
        runtime_config.openrouter_fallback_model,
        runtime_config.google_api_key,
        runtime_config.anthropic_api_key,
        runtime_config.ollama_base_url,
        runtime_config.ollama_api_key,
    )
    if _broll_agent is None or _broll_agent_signature != signature:
        apply_settings_to_process_env(runtime_config.as_runtime_settings())
        config_error = _get_missing_llm_key_error(model_name, runtime_config)
        if config_error:
            raise RuntimeError(config_error)
        _broll_agent = Agent[None, BRollPlan](
            model=_build_transcript_model(runtime_config, model_name),
            output_type=NativeOutput(BRollPlan, strict=True) if _uses_native_output(model_name) else BRollPlan,
            system_prompt=broll_plan_system_prompt,
            retries={'output': 2},
        )
        _broll_agent_signature = signature
    return _broll_agent


def build_broll_plan_prompt(
    timed_lines: str, clip_duration: float, max_cues: int, earliest_start: float
) -> str:
    """Ask for cutaways over one clip's transcript (timestamps in clip seconds)."""
    return (
        f"Clip length: {clip_duration:.1f} seconds.\n"
        f"Return at most {max_cues} cutaways. None may start before second "
        f"{earliest_start:.1f} or run past second {max(0.0, clip_duration - 1):.1f}.\n\n"
        "Transcript, one line per phrase, prefixed with the clip second it starts at:\n"
        f"{timed_lines}"
    )


async def plan_broll_cues(
    timed_lines: str, clip_duration: float, max_cues: int, earliest_start: float
) -> List[BRollCue]:
    """Pick stock-footage moments for one clip. Raises when the model is unavailable."""
    result = await _run_transcript_analysis(
        get_broll_agent(),
        build_broll_plan_prompt(timed_lines, clip_duration, max_cues, earliest_start),
        timeout_seconds=BROLL_PLAN_TIMEOUT_SECONDS,
    )
    return list(result.output.cues)


def _performance_history_section(performance_context: str | None) -> str:
    """The creator's measured audience history, framed as a tie-breaker only."""
    if not performance_context:
        return ""
    return (
        "\n\nCreator performance history (real audience data from clips this creator "
        "already published):\n"
        f"{performance_context}\n\n"
        "Use this history to break ties and to bias hook_type, clip length and hook "
        "style toward what has demonstrably worked for this creator. It never justifies "
        "fabricating content, stretching timestamps, or picking a weak moment."
    )


def build_candidate_rerank_prompt(
    candidates: List[TranscriptSegment], performance_context: str | None = None
) -> str:
    """Describe every candidate in full so the reranker can compare them."""
    from .utils.prompt_safety import sanitize_untrusted_text

    blocks = []
    for candidate_id, segment in enumerate(candidates):
        duration = _parse_transcript_timestamp_seconds(
            segment.end_time
        ) - _parse_transcript_timestamp_seconds(segment.start_time)
        # Segment text and hook titles are untrusted (transcript-derived):
        # sanitised so a spoken instruction cannot pose as a prompt line.
        blocks.append(
            f"candidate_id: {candidate_id}\n"
            f"time: {segment.start_time} - {segment.end_time} ({duration}s)\n"
            f"hook_title: {sanitize_untrusted_text(segment.hook_title, 200) or '(none)'}\n"
            f"text: {sanitize_untrusted_text(segment.text, 8000)}"
        )
    return (
        f"Rank these {len(candidates)} clip candidates from the same video, best first.\n\n"
        + "\n\n".join(blocks)
        + _performance_history_section(performance_context)
    )


_SOURCE_DESCRIPTION_MAX_CHARS = 600


def _source_context_section(
    source_title: str | None,
    creator_name: str | None,
    source_description: str | None,
) -> str:
    """Format the source's own metadata as a prompt block ('' when none).

    Title, channel and description tell the selector what the video is
    and who made it — context the transcript alone cannot give (a chatty
    restaurant tour reads as a podcast without its title). The block is
    framed as metadata, never transcript, so it can steer judgement and
    hook titles but can never be quoted as segment text. The
    description is whitespace-collapsed and truncated: it is context,
    not content.
    """
    from .utils.prompt_safety import sanitize_untrusted_text

    # Source metadata is untrusted text on its way into a prompt:
    # one line each, unable to fake a new section or close a tag.
    safe_title = sanitize_untrusted_text(source_title, 500)
    safe_creator = sanitize_untrusted_text(creator_name, 500)
    lines: List[str] = []
    if safe_title:
        lines.append(f"- Title: {safe_title}")
    if safe_creator:
        lines.append(f"- Creator / channel: {safe_creator}")
    if source_description and source_description.strip():
        description = " ".join(
            sanitize_untrusted_text(source_description, 100_000).split()
        )
        if len(description) > _SOURCE_DESCRIPTION_MAX_CHARS:
            description = description[:_SOURCE_DESCRIPTION_MAX_CHARS].rstrip() + "…"
        lines.append(f"- Description: {description}")
    if not lines:
        return ""
    section = (
        "\nSource video context (metadata only — it says what this video "
        "is and who made it; it is NOT transcript and must never be "
        "quoted as segment text):\n"
        + "\n".join(lines)
        + "\nUse it to judge which moments matter most to this video's "
        "audience, and to name the creator in hook titles when that "
        "reads naturally."
    )
    # Per-game picking rules (PeakCut take): when the title/channel names
    # a known game, the picker gets that game's moment rules + hashtags.
    # A hint for judgement and copy — never a filter.
    from .auto_director.game_rules import detect_game, game_rules_enabled, game_rules_prompt

    if game_rules_enabled():
        game_name, _tags = detect_game(safe_title, safe_creator)
        if game_name:
            section += "\n\n" + game_rules_prompt(game_name)
    return section


_SCENE_BOUNDARIES_MAX_PER_PROMPT = 100


def _scene_boundaries_section(
    scene_cut_times: Optional[List[float]],
    window: Optional[tuple[int, int]] = None,
) -> str:
    """Format detected scene-cut times as boundary hints ('' when none).

    The pipeline already detects scene cuts for reframing, but the
    selector never saw them and could only guess where the picture
    changes. As compact MM:SS hints they cost a few tokens per window
    and tell the model where a start or end would also land cleanly on
    a visual break. They are hints, not boundaries: a cut mid-sentence
    is not a clip edge by itself.
    """
    if not scene_cut_times:
        return ""
    cuts = sorted({int(round(cut)) for cut in scene_cut_times if cut >= 0})
    if window is not None:
        cuts = [cut for cut in cuts if window[0] <= cut < window[1]]
    if not cuts:
        return ""
    shown = cuts[:_SCENE_BOUNDARIES_MAX_PER_PROMPT]
    listing = ", ".join(_format_transcript_timestamp(cut) for cut in shown)
    if len(cuts) > len(shown):
        listing += f", … (+{len(cuts) - len(shown)} more)"
    return (
        "\n\nScene boundaries (visual cuts detected in the video) at: "
        f"{listing}\n"
        "Prefer a segment start or end that lands on or near a scene "
        "boundary when the sentence also breaks there. A cut in the "
        "middle of a sentence is not by itself a reason to start or "
        "end a clip."
    )


def build_transcript_analysis_prompt(
    transcript: str,
    clip_signals: str | None = None,
    window: tuple[int, int] | None = None,
    performance_context: str | None = None,
    clip_length: str = "auto",
    segment_target: str | None = None,
    source_context: str | None = None,
    scene_cut_times: Optional[List[float]] = None,
) -> str:
    """Build the grounded task prompt for transcript analysis.

    ``window`` marks the prompt as one (start, end) slice of a longer source.
    ``performance_context`` is the creator's measured audience history, if any.
    ``segment_target`` overrides the per-call segment ask; it is only set for
    tasks with an explicit clip-count target, so default requests stay
    byte-identical to before.
    ``source_context`` is the preformatted source-metadata block from
    :func:`_source_context_section`; empty for sources without metadata,
    which keeps those requests byte-identical to before.
    """
    from .utils.prompt_safety import sanitize_transcript_text

    # The transcript is untrusted data: sanitise it line by line (our
    # timestamp structure is preserved) so spoken text cannot smuggle
    # in fake transcript lines or prompt instructions.
    transcript = sanitize_transcript_text(transcript)
    bounds = clip_duration(clip_length)
    if segment_target is None:
        segment_target = ANALYSIS_WINDOW_SEGMENT_TARGET if window else "2-5"
    window_note = ""
    if window:
        window_note = (
            f"\nThis transcript is one window ({_format_minutes_label(window[0])} - "
            f"{_format_minutes_label(window[1])}) of a longer video. Other windows are "
            "analysed separately, so pick the best clips that exist inside this window only.\n"
        )
    performance_section = _performance_history_section(performance_context)
    signal_section = ""
    if clip_signals:
        signal_section = (
            "\n\nAdditional deterministic signals from transcript/audio analysis:\n"
            f"{clip_signals}\n\n"
            "Use these as hints only. They should influence ranking, but every final segment "
            "must still be a coherent contiguous transcript range."
        )
    scene_section = _scene_boundaries_section(scene_cut_times, window)

    return f"""Analyze this video transcript and identify the most engaging segments for short-form content.
{window_note}{source_context or ""}
The transcript is formatted as one line per timestamped span, for example:
[00:12 - 00:21] Spoken text here
[00:21 - 00:35] More spoken text here

Follow this workflow:
1. Read the transcript as a sequence of timestamped spans.
2. Select only contiguous ranges that already exist in the transcript.
3. Prefer moments with a strong hook, clear payoff, emotional charge, or concrete value.
4. For each chosen segment, use the earliest timestamp in the selected range as start_time and the latest timestamp in the selected range as end_time.
5. start_time must be the first word of a self-contained sentence: never a mid-thought fragment, a filler, a bare agreement, or a reference to something this clip does not contain. end_time must finish that thought's last sentence and must not include the next thought's opening words.

Selection target:
- Choose {segment_target} segments total.
- Most selected clips should be {bounds.ideal_minimum}-{bounds.ideal_maximum} seconds.
- Every clip must contain a full setup and payoff within {bounds.minimum}-{bounds.maximum} seconds.
- If a strong moment is shorter than {bounds.ideal_minimum} seconds, first try expanding to nearby contiguous transcript lines that add useful context.
- Skip weak standalone picks: intros, sponsor reads, CTAs, contextless quotes, repeated points, vague setup, and answer fragments that require prior context.
- Before returning a segment, ask whether a viewer would understand and care without seeing the rest of the source video.

Critical accuracy requirements:
- Do not fabricate or embellish content.
- Do not use timestamps that are not present in the transcript.
- Do not merge separate non-contiguous moments into one segment.
- segment.text must reflect only the spoken content inside the selected time range.
- If a span lacks enough context to stand alone, expand to nearby contiguous lines rather than guessing.
- If there is a tradeoff between "viral" and "accurate", choose accuracy.
- Do not reject or penalize a segment simply because of the subject matter; stay content-neutral and assess clip quality only.
{signal_section}{performance_section}{scene_section}

JSON-only output requirements:
- Return one valid JSON object and nothing else.
- No Markdown, headings, bullets, code fences, or explanatory text outside JSON.
- Top-level keys: "most_relevant_segments", "summary", "key_topics".
- Segment keys: "start_time", "end_time", "text", "relevance_score", "reasoning", "virality", "hook_title".
- Optional segment keys "hook_start_time"/"hook_end_time": null unless a later 2-5 second line stands alone and the real opening is already a complete setup. The clip is not replayed from that line by default.
- "hook_title" is a 3-9 word plain-text headline for the clip, in the transcript's language, grounded in the segment (no hashtags, emojis, or quotes). THIRD PERSON ONLY: never the speaker's "I/I'm/my/we" — a clipper posts the clip, not the speaker; name the subject or describe the moment neutrally.
- Virality keys: "hook_score", "engagement_score", "value_score", "shareability_score", "total_score", "hook_type", "category", "virality_reasoning".
- Do not return segments shorter than {bounds.minimum} seconds or longer than {bounds.maximum} seconds.

Transcript:
{transcript}"""


def _cap_hook_title(title: str) -> str:
    """Trim a hook title to the house word/char caps at a word boundary."""
    words = title.split()
    if len(words) > HOOK_TITLE_MAX_WORDS:
        words = words[:HOOK_TITLE_MAX_WORDS]
        title = " ".join(words)
    if len(title) > HOOK_TITLE_MAX_CHARS:
        clipped = title[: HOOK_TITLE_MAX_CHARS + 1]
        cut = clipped.rfind(" ")
        title = (clipped[:cut] if cut > 20 else title[:HOOK_TITLE_MAX_CHARS]).rstrip(
            ".,;:-–— "
        )
    return title


def sanitize_hook_title(
    raw: Optional[str],
    creator_name: Optional[str] = None,
    fallback_text: Optional[str] = None,
) -> Optional[str]:
    """Normalize an AI-provided hook title for on-screen rendering.

    Strips wrapping quotes/markdown, collapses whitespace, drops hashtags, and
    trims to a word-boundary length cap. Returns None when nothing usable is
    left so callers can simply skip the overlay.

    The title also passes the third-person guardrail
    (:func:`src.auto_director.title_person.ensure_third_person_title`): the
    clip is posted by a clipper sharing another creator's moment, so the
    speaker's first person ("I bought…", "my routine") must never survive
    into the stored/rendered title. ``fallback_text`` (the segment's own
    words) feeds the neutral fallback when no safe rewrite exists.
    """
    if not raw:
        return None
    title = str(raw).strip()
    title = title.strip("\"'`“”‘’").strip()
    title = re.sub(r"#\w+", "", title)
    title = re.sub(r"\s+", " ", title).strip()
    # Drop trailing sentence punctuation but keep ?/! (they carry the hook).
    title = title.rstrip(".,;:-–— ").strip()
    if not title:
        return None

    title = _cap_hook_title(title)

    from .auto_director.title_person import ensure_third_person_title

    title = ensure_third_person_title(
        title, creator_name=creator_name, fallback_text=fallback_text
    )
    if not title:
        return None
    # A creator-attributed rewrite can outgrow the caps; re-apply them.
    return _cap_hook_title(title) or None


def _sanitize_hook_span(
    segment: TranscriptSegment, start_seconds: int, end_seconds: int
) -> None:
    """Drop a cold-open hook range that is malformed or outside the segment.

    Word-level snapping and length checks happen at render time.
    """
    try:
        hook_start = _parse_transcript_timestamp_seconds(segment.hook_start_time or "")
        hook_end = _parse_transcript_timestamp_seconds(segment.hook_end_time or "")
    except ValueError:
        hook_start = hook_end = None
    if (
        hook_start is None
        or hook_end is None
        or not start_seconds < hook_start < hook_end <= end_seconds
        or hook_end - hook_start > 8
    ):
        segment.hook_start_time = None
        segment.hook_end_time = None


def _parse_transcript_timestamp_seconds(timestamp: str) -> int:
    """Parse MM:SS or HH:MM:SS transcript timestamps into seconds."""
    parts = [int(part) for part in timestamp.split(":")]
    if len(parts) == 2:
        minutes, seconds = parts
        return minutes * 60 + seconds
    if len(parts) == 3:
        hours, minutes, seconds = parts
        return hours * 3600 + minutes * 60 + seconds
    raise ValueError(f"Unsupported timestamp format: {timestamp}")


def _format_transcript_timestamp(seconds: int) -> str:
    """Format seconds as a transcript timestamp."""
    seconds = max(0, int(seconds))
    hours, remainder = divmod(seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    if hours:
        return f"{hours:02d}:{minutes:02d}:{seconds:02d}"
    return f"{minutes:02d}:{seconds:02d}"


def _format_minutes_label(seconds: int) -> str:
    """Format seconds the way transcript lines do: MM:SS with unbounded minutes."""
    minutes, seconds = divmod(max(0, int(seconds)), 60)
    return f"{minutes:02d}:{seconds:02d}"


def _parse_transcript_spans(transcript: str) -> list[dict[str, Any]]:
    """Parse timestamped transcript lines into spans."""
    spans = []
    for line in transcript.splitlines():
        match = TRANSCRIPT_SPAN_RE.match(line.strip())
        if not match:
            continue
        try:
            start_seconds = _parse_transcript_timestamp_seconds(match.group("start"))
            end_seconds = _parse_transcript_timestamp_seconds(match.group("end"))
        except ValueError:
            continue
        if end_seconds <= start_seconds:
            continue
        spans.append(
            {
                "start": start_seconds,
                "end": end_seconds,
                "text": match.group("text").strip(),
            }
        )
    return spans


def _extract_transcript_text(
    transcript_spans: list[dict[str, Any]], start_seconds: int, end_seconds: int
) -> str:
    """Return transcript text overlapping a selected time range."""
    selected_text = [
        span["text"]
        for span in transcript_spans
        if span["text"]
        and span["end"] > start_seconds
        and span["start"] < end_seconds
    ]
    return " ".join(selected_text).strip()


def _choose_repaired_bounds(
    transcript_spans: list[dict[str, Any]], start_seconds: int, end_seconds: int,
    clip_length: str = "auto",
) -> tuple[int, int] | None:
    """Repair model-selected bounds to the nearest acceptable contiguous range."""
    bounds = clip_duration(clip_length)
    if not transcript_spans:
        return None

    starts = sorted({span["start"] for span in transcript_spans})
    ends = sorted({span["end"] for span in transcript_spans})
    current_duration = end_seconds - start_seconds

    if current_duration > bounds.maximum:
        target_end = start_seconds + bounds.ideal_maximum
        candidate_ends = [
            candidate
            for candidate in ends
            if start_seconds + bounds.minimum
            <= candidate
            <= min(target_end, end_seconds)
        ]
        if candidate_ends:
            return start_seconds, max(candidate_ends)
        if start_seconds + bounds.minimum <= target_end:
            return start_seconds, target_end
        return None

    if current_duration < bounds.minimum:
        candidate_ranges: list[tuple[int, int, int]] = []
        for candidate_start in starts:
            if candidate_start > start_seconds:
                continue
            for candidate_end in ends:
                if candidate_end < end_seconds:
                    continue
                duration = candidate_end - candidate_start
                if bounds.minimum <= duration <= bounds.maximum:
                    extra_context = (start_seconds - candidate_start) + (
                        candidate_end - end_seconds
                    )
                    ideal_penalty = 0
                    if duration < bounds.ideal_minimum:
                        ideal_penalty = bounds.ideal_minimum - duration
                    elif duration > bounds.ideal_maximum:
                        ideal_penalty = duration - bounds.ideal_maximum
                    candidate_ranges.append(
                        (ideal_penalty * 1000 + extra_context, candidate_start, candidate_end)
                    )
        if candidate_ranges:
            _, repaired_start, repaired_end = min(candidate_ranges)
            return repaired_start, repaired_end

    return None


def _repair_segment_bounds(
    segment: TranscriptSegment,
    transcript_spans: list[dict[str, Any]],
    start_seconds: int,
    end_seconds: int,
    clip_length: str = "auto",
) -> tuple[int, int] | None:
    """Adjust near-miss model ranges to usable transcript-aligned bounds."""
    repaired_bounds = _choose_repaired_bounds(
        transcript_spans,
        start_seconds,
        end_seconds,
        clip_length,
    )
    if not repaired_bounds:
        return None

    repaired_start, repaired_end = repaired_bounds
    segment.start_time = _format_transcript_timestamp(repaired_start)
    segment.end_time = _format_transcript_timestamp(repaired_end)
    repaired_text = _extract_transcript_text(
        transcript_spans,
        repaired_start,
        repaired_end,
    )
    if repaired_text:
        segment.text = repaired_text
    logger.info(
        "Repaired segment duration: %s-%s -> %s-%s",
        _format_transcript_timestamp(start_seconds),
        _format_transcript_timestamp(end_seconds),
        segment.start_time,
        segment.end_time,
    )
    return repaired_start, repaired_end


def _is_transient_model_error(error: Exception) -> bool:
    """Recognize provider overload and transport failures, including SDK wrappers."""
    current: BaseException | None = error
    seen: set[int] = set()
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        if isinstance(current, ModelHTTPError):
            return current.status_code in TRANSIENT_MODEL_STATUS_CODES
        if isinstance(
            current,
            (httpx.TimeoutException, httpx.NetworkError, httpx.RemoteProtocolError, TimeoutError),
        ):
            return True
        current = current.__cause__
    return False


def _exception_headers(error: BaseException) -> dict[str, str]:
    """Return provider response headers from an error or its cause chain."""
    current: BaseException | None = error
    seen: set[int] = set()
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        headers = getattr(current, "headers", None)
        if headers:
            return {str(key).lower(): str(value) for key, value in headers.items()}
        response = getattr(current, "response", None)
        response_headers = getattr(response, "headers", None)
        if response_headers:
            return {
                str(key).lower(): str(value)
                for key, value in response_headers.items()
            }
        current = current.__cause__
    return {}


def _parse_duration_seconds(value: str) -> Optional[float]:
    """Parse values such as ``20``, ``7.5s`` or ``1m2.4s`` into seconds."""
    text = value.strip().lower()
    if not text:
        return None
    try:
        return float(text)
    except ValueError:
        pass

    total = 0.0
    matched = False
    for amount, unit in re.findall(r"(\d+(?:\.\d+)?)\s*(ms|s|m|h)", text):
        matched = True
        total += float(amount) * {"ms": 0.001, "s": 1.0, "m": 60.0, "h": 3600.0}[unit]
    return total if matched else None


def _is_rate_limit_error(error: BaseException) -> bool:
    """Recognize a 413/429 anywhere in the provider error chain."""
    current: BaseException | None = error
    seen: set[int] = set()
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        if (
            isinstance(current, ModelHTTPError)
            and current.status_code in ANALYSIS_RATE_LIMIT_STATUS_CODES
        ):
            return True
        status_code = getattr(current, "status_code", None)
        if status_code in ANALYSIS_RATE_LIMIT_STATUS_CODES and not isinstance(
            current, ModelHTTPError
        ):
            return True
        current = current.__cause__
    return False


def _is_connection_error(error: BaseException) -> bool:
    """Recognize 'no HTTP response at all' anywhere in the error chain.

    A dead gateway surfaces as ``ModelAPIError`` (status None) wrapping the
    SDK's ``APIConnectionError`` wrapping ``httpx.ConnectError`` — no status
    code ever arrives. Read timeouts are deliberately excluded: the route
    accepted the connection, so the transient ladder owns those.
    """
    current: BaseException | None = error
    seen: set[int] = set()
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        if isinstance(current, (httpx.ConnectError, httpx.ConnectTimeout)):
            return True
        if isinstance(current, ConnectionError):
            return True
        if type(current).__name__ == "APIConnectionError":
            return True
        current = current.__cause__
    return False


def _is_params_rejection_error(error: BaseException) -> bool:
    """Recognize a provider refusing an optional request parameter.

    LiteLLM raises ``UnsupportedParamsError`` when a route does not
    support a parameter (the message survives the gateway round-trip);
    a direct provider answers the same refusal with HTTP 400 naming the
    parameter. Either shape means "retry without the optional
    parameter", never "the analysis failed".
    """
    if "UnsupportedParams" in type(error).__name__:
        return True
    if getattr(error, "status_code", None) == 400 and "reasoning_effort" in str(
        error
    ):
        return True
    return False


def _rate_limit_retry_delay(error: BaseException, attempt: int) -> float:
    """Honour provider reset headers exactly; otherwise back off with jitter.

    The jitter (50-100% of the exponential step) keeps the concurrent
    windows of one analysis from retrying in lockstep into the same bucket.
    """
    headers = _exception_headers(error)
    for name in ("retry-after", "x-ratelimit-reset-tokens"):
        if name in headers:
            delay = _parse_duration_seconds(headers[name])
            if delay is not None and delay > 0:
                return delay
    base = ANALYSIS_RATE_LIMIT_INITIAL_DELAY_SECONDS * 2 ** (attempt - 1)
    return base * random.uniform(0.5, 1.0)


class AnalysisRateLimitError(RuntimeError):
    """Analysis could not finish because the provider kept rate-limiting.

    Carries a plain-words message: the task error is what the user sees, and
    "HTTP 429 from the provider" must never surface as a generic failure.
    """


class AnalysisGatewayUnreachableError(RuntimeError):
    """Analysis could not reach the AI gateway at all (connection-level).

    Kept distinct from :class:`AnalysisRateLimitError`: a dead gateway and
    a saturated provider look identical from outside unless the error says
    which one happened.
    """


class AnalysisTimeoutError(RuntimeError):
    """The analysis call exceeded its per-attempt time budget.

    A timeout is a provider failure like any other: it reaches the
    failover layer as its own type so a slow model hands the chunk to the
    next configured model instead of failing the whole task. (A bare
    RuntimeError escaped the failover catch list and killed a real task
    on 2026-11-08 after a single 600 s stall.)
    """


def _analysis_setting(name: str, default: Any) -> Any:
    """Read an analysis knob from the runtime config, with a safe default."""
    try:
        return getattr(get_config(), name, default)
    except Exception:
        return default


class _AnalysisPacer:
    """Keep calls to the same model inside its tokens-per-minute bucket."""

    def __init__(self, min_interval_seconds: float = 0.0, tpm: int = DEFAULT_ANALYSIS_TPM):
        self.min_interval_seconds = max(0.0, min_interval_seconds)
        self.tpm = max(1, tpm)
        self._last_start: dict[str, float] = {}
        self._locks: dict[str, asyncio.Lock] = {}

    def interval_for(self, prompt_tokens: int) -> float:
        """Return 0 when pacing is off, otherwise the token-safe interval."""
        if self.min_interval_seconds <= 0:
            return 0.0
        token_interval = prompt_tokens * 60.0 / self.tpm
        return max(self.min_interval_seconds, token_interval)

    async def wait(self, model_name: str, prompt_tokens: int) -> None:
        interval = self.interval_for(prompt_tokens)
        if interval <= 0:
            return
        lock = self._locks.setdefault(model_name, asyncio.Lock())
        async with lock:
            now = asyncio.get_running_loop().time()
            last_start = self._last_start.get(model_name)
            if last_start is not None:
                remaining = interval - (now - last_start)
                if remaining > 0:
                    await asyncio.sleep(remaining)
                    now = asyncio.get_running_loop().time()
            self._last_start[model_name] = now


def _build_analysis_pacer(
    runtime_config: Any,
    *,
    chunk_count: int,
    total_tokens: int,
    tpm: int,
) -> _AnalysisPacer:
    """Decide whether analysis calls pace themselves, and by how much.

    An explicit ``ANALYSIS_MIN_INTERVAL_SEC`` always wins (0 disables pacing,
    as before). When it is unset, pacing engages on its own only when a
    burst could actually overflow the per-minute token bucket: more than one
    chunk whose combined prompt tokens exceed the bucket. A single-chunk
    analysis — every short video — is never delayed.
    """
    explicit = getattr(runtime_config, "analysis_min_interval_sec", None)
    if explicit is not None:
        return _AnalysisPacer(float(explicit), tpm)
    if chunk_count > 1 and total_tokens > tpm:
        floor = getattr(
            runtime_config,
            "analysis_pacing_floor_sec",
            DEFAULT_ANALYSIS_PACING_FLOOR_SECONDS,
        )
        return _AnalysisPacer(float(floor or 0.0), tpm)
    return _AnalysisPacer(0.0, tpm)


# The final selection pass is the one call whose judgement ships: the
# comparative rerank for long videos, or the single analysis call for
# short ones (it writes the slate and the hook titles in one pass).
# GPT-OSS routes accept reasoning_effort low/medium/high and silently
# run at medium when it is omitted, so the premium pass asks for high.
# Reasoning tokens count as output against the free daily token pool,
# which is why the bulk window scans keep the provider default.
PREMIUM_REASONING_EFFORT = "high"
_PREMIUM_REASONING_PROVIDERS = {"openai", "openai-chat", "groq"}


def _premium_reasoning_settings(
    model_name: Optional[str],
) -> Optional[Dict[str, Any]]:
    """Model settings that turn reasoning effort up for one premium call.

    Only OpenAI-compatible and direct Groq routes are asked: both
    forward ``reasoning_effort`` to GPT-OSS models. Other providers
    return None — their own knobs (e.g. the OpenRouter reasoning
    setting) stay exactly as configured — and a route that still
    refuses the parameter degrades to a plain call inside
    :func:`_run_transcript_analysis`, never a failed task.
    """
    if not model_name:
        return None
    provider, _ = _split_llm_name(model_name)
    if provider not in _PREMIUM_REASONING_PROVIDERS:
        return None
    return {"extra_body": {"reasoning_effort": PREMIUM_REASONING_EFFORT}}


async def _run_transcript_analysis(
    agent: Agent,
    prompt: str,
    timeout_seconds: Optional[float] = None,
    *,
    model_name: Optional[str] = None,
    pacer: Optional[_AnalysisPacer] = None,
    model_settings: Optional[Any] = None,
) -> Any:
    """Run one analysis call, retrying temporary and rate-limit failures.

    Agent output retries handle schema repair, not HTTP failures. Retrying only
    the model step preserves completed downloads and transcription. Rate limits
    (413/429) retry the same chunk on their own generous budget, honouring
    provider reset headers exactly and jittering the exponential backoff
    otherwise; exhausting that budget raises :class:`AnalysisRateLimitError`
    so the failure is reported as a rate limit, never as a generic error.
    ``timeout_seconds`` bounds each individual attempt, not the whole retry
    schedule. Cancellation passes through.
    """
    if timeout_seconds is None:
        timeout_seconds = TRANSCRIPT_ANALYSIS_TIMEOUT_SECONDS
    prompt_tokens = _estimate_tokens(prompt)
    rate_limit_attempt = 0
    transient_attempt = 0
    connection_attempt = 0
    rate_limit_budget = float(
        _analysis_setting(
            "analysis_rate_limit_budget_sec", ANALYSIS_RATE_LIMIT_BUDGET_SECONDS
        )
    )
    loop = asyncio.get_running_loop()
    rate_limit_deadline: Optional[float] = None

    while True:
        if pacer is not None:
            await pacer.wait(model_name or "", prompt_tokens)
        try:
            async with asyncio.timeout(timeout_seconds):
                if model_settings is not None:
                    result = await agent.run(prompt, model_settings=model_settings)
                else:
                    result = await agent.run(prompt)
            if hasattr(result, "response"):
                logger.info(
                    "AI analysis completed using model %s",
                    result.response.model_name,
                )
            return result
        except TimeoutError:
            logger.error("AI analysis exceeded the %ss time budget", timeout_seconds)
            raise AnalysisTimeoutError(
                "The AI analysis service took too long to respond. Please try again shortly."
            ) from None
        except Exception as error:
            if model_settings is not None and _is_params_rejection_error(error):
                # The route refused the optional premium parameter (an
                # older gateway build without the parameter allow-listed,
                # or a fallback model without the knob). Drop it and
                # retry as a plain call: a refused knob must never fail
                # the analysis.
                logger.warning(
                    "Provider rejected premium reasoning settings (%s); "
                    "retrying the call without them",
                    type(error).__name__,
                )
                model_settings = None
                continue

            if _is_rate_limit_error(error):
                rate_limit_attempt += 1
                now = loop.time()
                if rate_limit_deadline is None:
                    rate_limit_deadline = now + rate_limit_budget
                delay = _rate_limit_retry_delay(error, rate_limit_attempt)
                if (
                    rate_limit_attempt >= ANALYSIS_RATE_LIMIT_MAX_ATTEMPTS
                    or now + delay > rate_limit_deadline
                ):
                    logger.error(
                        "AI analysis failed after %s rate-limit attempts "
                        "(HTTP status %s)",
                        rate_limit_attempt,
                        getattr(error, "status_code", None),
                    )
                    raise AnalysisRateLimitError(
                        ANALYSIS_RATE_LIMITED_MESSAGE
                    ) from error
                # Do not log provider bodies: they may contain transcript data.
                logger.warning(
                    "AI analysis rate limited (%s, HTTP status %s); "
                    "retrying attempt %s/%s in %.1fs",
                    type(error).__name__,
                    getattr(error, "status_code", None),
                    rate_limit_attempt + 1,
                    ANALYSIS_RATE_LIMIT_MAX_ATTEMPTS,
                    delay,
                )
                await asyncio.sleep(delay)
                continue

            if _is_connection_error(error):
                connection_attempt += 1
                if connection_attempt >= ANALYSIS_CONNECTION_MAX_ATTEMPTS:
                    logger.error(
                        "AI analysis endpoint unreachable after %s attempts (%s)",
                        connection_attempt,
                        type(error).__name__,
                    )
                    raise AnalysisGatewayUnreachableError(
                        ANALYSIS_GATEWAY_UNREACHABLE_MESSAGE
                    ) from error
                logger.warning(
                    "AI analysis connection failed (%s); retrying attempt %s/%s in %.1fs",
                    type(error).__name__,
                    connection_attempt + 1,
                    ANALYSIS_CONNECTION_MAX_ATTEMPTS,
                    ANALYSIS_CONNECTION_RETRY_DELAY_SECONDS,
                )
                await asyncio.sleep(ANALYSIS_CONNECTION_RETRY_DELAY_SECONDS)
                continue

            if not _is_transient_model_error(error):
                raise
            transient_attempt += 1
            if transient_attempt >= TRANSCRIPT_ANALYSIS_MAX_ATTEMPTS:
                logger.error(
                    "AI analysis unavailable after %s attempts (%s, HTTP status %s)",
                    transient_attempt,
                    type(error).__name__,
                    getattr(error, "status_code", None),
                )
                raise RuntimeError(
                    "The AI analysis service is temporarily unavailable after "
                    "several attempts. Please try again shortly."
                ) from None

            delay = 5 * 2 ** (transient_attempt - 1) + random.uniform(0, 1)
            logger.warning(
                "Temporary AI analysis failure (%s, HTTP status %s); "
                "retrying attempt %s/%s in %.1fs",
                type(error).__name__,
                getattr(error, "status_code", None),
                transient_attempt + 1,
                TRANSCRIPT_ANALYSIS_MAX_ATTEMPTS,
                delay,
            )
            await asyncio.sleep(delay)


class _AnalysisFailover:
    """Second-chance routing when a chunk's model fails on limits or reach.

    On rate-limit exhaustion the chunk moves to the next configured
    analysis model, then to the gateway's local alias. On a connection
    failure (gateway unreachable) every route sharing the dead provider is
    skipped — all ``openai-chat:*`` aliases traverse the same gateway — and the
    direct local model, which does not, goes first. Failures that are
    neither (bad request, schema, transient overload) are not rerouted:
    they fail exactly as before.
    """

    def __init__(
        self,
        configured_models: Optional[List[str]],
        fallback_model: Optional[str],
        direct_fallback_model: Optional[str],
    ) -> None:
        self._configured = [m for m in (configured_models or []) if m]
        self._fallback = fallback_model or None
        self._direct_fallback = direct_fallback_model or None
        self.dead_providers: set[str] = set()

    @staticmethod
    def _provider(model_name: Optional[str]) -> str:
        provider = model_name.split(":", 1)[0] if model_name else ""
        # The deprecated "openai:" spelling and the canonical
        # "openai-chat:" spelling name the same provider: a gateway
        # failure on one must mark the other unusable too.
        return "openai" if provider == "openai-chat" else provider

    def _note_failure(
        self, model_name: Optional[str], error: BaseException
    ) -> None:
        if isinstance(error, AnalysisGatewayUnreachableError) and model_name:
            self.dead_providers.add(self._provider(model_name))

    def candidates(
        self, failed_model: Optional[str], error: BaseException
    ) -> List[str]:
        """Ordered second-chance routes for a chunk that failed on ``failed_model``."""
        pool = list(self._configured)
        if failed_model in pool:
            index = pool.index(failed_model)
            pool = pool[index + 1 :] + pool[:index]
        terminal = [self._fallback, self._direct_fallback]
        if isinstance(error, AnalysisGatewayUnreachableError):
            terminal = [self._direct_fallback, self._fallback]
        ordered: List[str] = []
        seen: set[str] = set()
        for model in pool + [m for m in terminal if m]:
            if model == failed_model or model in seen:
                continue
            seen.add(model)
            ordered.append(model)
        return ordered

    def _usable(self, model_name: str) -> bool:
        return self._provider(model_name) not in self.dead_providers

    async def run(
        self,
        *,
        prompt: str,
        clip_length: str,
        primary_model: Optional[str],
        primary_agent: Any,
        pacer: Optional[_AnalysisPacer] = None,
        model_settings: Optional[Any] = None,
    ) -> Any:
        """Run one chunk on its primary model, failing over when configured."""
        extra = (
            {"model_name": primary_model, "pacer": pacer}
            if pacer is not None and pacer.min_interval_seconds > 0
            else {}
        )
        if model_settings is not None:
            # Premium-pass settings travel with the chunk; a failover
            # route that refuses the parameter degrades inside
            # _run_transcript_analysis instead of failing the chunk.
            extra["model_settings"] = model_settings
        try:
            return await _run_transcript_analysis(primary_agent, prompt, **extra)
        except (
            AnalysisRateLimitError,
            AnalysisGatewayUnreachableError,
            AnalysisTimeoutError,
        ) as primary_error:
            self._note_failure(primary_model, primary_error)
            for candidate in self.candidates(primary_model, primary_error):
                if not self._usable(candidate):
                    continue
                logger.warning(
                    "Analysis chunk failing over from %s to %s (%s)",
                    primary_model,
                    candidate,
                    type(primary_error).__name__,
                )
                try:
                    candidate_agent = get_transcript_agent(
                        clip_length, model_name=candidate
                    )
                except Exception:
                    continue
                candidate_extra: dict[str, Any] = {
                    "timeout_seconds": ANALYSIS_FAILOVER_TIMEOUT_SECONDS
                }
                if pacer is not None and pacer.min_interval_seconds > 0:
                    candidate_extra.update(
                        {"model_name": candidate, "pacer": pacer}
                    )
                if model_settings is not None:
                    candidate_extra["model_settings"] = model_settings
                try:
                    return await _run_transcript_analysis(
                        candidate_agent, prompt, **candidate_extra
                    )
                except (
                    AnalysisRateLimitError,
                    AnalysisGatewayUnreachableError,
                    AnalysisTimeoutError,
                ) as candidate_error:
                    self._note_failure(candidate, candidate_error)
                    continue
                except Exception:
                    # The candidate route itself is unusable (missing key,
                    # hard provider error); try the next one.
                    continue
            raise primary_error


def _build_transcript_agent_advancing(
    clip_length: str,
    preferred_model: Optional[str],
    failover: Optional[_AnalysisFailover],
) -> tuple[Any, Optional[str]]:
    """Build a transcript agent, advancing past unbuildable candidates.

    Mirrors niche classification's chain walk (and the failover's own
    candidate-build guard): a candidate whose agent cannot even be
    CONSTRUCTED — a provider client rejecting a malformed base URL
    from settings/env, a bad custom endpoint — is skipped with a
    warning instead of killing the whole analysis, which is what
    defect D-1 did (one unbuildable route errored every Full
    AutoClip / Find Moments task while classification, hitting the
    identical failure, simply moved on). The chain order comes from
    the failover's own candidate list: configured models, then the
    gateway fallback, then the direct local model. When NO candidate
    builds, the FIRST build error is re-raised, so a fully broken
    configuration fails exactly as it did before.
    """
    try:
        if preferred_model is None:
            return get_transcript_agent(clip_length), None
        return (
            get_transcript_agent(clip_length, model_name=preferred_model),
            preferred_model,
        )
    except Exception as first_error:
        logger.warning(
            "Transcript analysis candidate %s failed to build (%s); "
            "trying the next candidate",
            preferred_model or "default",
            first_error,
        )
        chain = failover.candidates(preferred_model, first_error) if failover else []
        for model in chain:
            if not model or model == preferred_model:
                continue
            try:
                return get_transcript_agent(clip_length, model_name=model), model
            except Exception:
                logger.warning(
                    "Transcript analysis candidate %s also failed to "
                    "build; continuing down the chain",
                    model,
                )
                continue
        raise first_error


def split_transcript_into_windows(
    transcript: str,
    window_seconds: int = ANALYSIS_WINDOW_SECONDS,
    overlap_seconds: int = ANALYSIS_WINDOW_OVERLAP_SECONDS,
    single_window_max_seconds: int = ANALYSIS_SINGLE_WINDOW_MAX_SECONDS,
) -> List[Dict[str, Any]]:
    """Split a formatted transcript into overlapping time windows.

    Transcripts up to ``single_window_max_seconds`` stay one window holding the
    original text unchanged. A short trailing window is folded into the one
    before it so no window is analysed with too little material.
    """
    timed_lines: List[tuple[int, str]] = []
    total_seconds = 0
    for raw_line in transcript.splitlines():
        line = raw_line.strip()
        match = TRANSCRIPT_SPAN_RE.match(line)
        if not match:
            continue
        try:
            start_seconds = _parse_transcript_timestamp_seconds(match.group("start"))
            end_seconds = _parse_transcript_timestamp_seconds(match.group("end"))
        except ValueError:
            continue
        timed_lines.append((start_seconds, line))
        total_seconds = max(total_seconds, end_seconds, start_seconds)

    if not timed_lines or total_seconds <= single_window_max_seconds:
        return [{"start": 0, "end": total_seconds, "transcript": transcript}]

    step = max(1, window_seconds - overlap_seconds)
    bounds: List[List[int]] = []
    window_start = 0
    while True:
        window_end = window_start + window_seconds
        if window_end >= total_seconds:
            bounds.append([window_start, total_seconds])
            break
        bounds.append([window_start, window_end])
        window_start += step
    if len(bounds) > 1 and bounds[-1][1] - bounds[-1][0] < window_seconds // 2:
        bounds.pop()
        bounds[-1][1] = total_seconds

    windows: List[Dict[str, Any]] = []
    for index, (window_start, window_end) in enumerate(bounds):
        is_last = index == len(bounds) - 1
        lines = [
            line
            for start_seconds, line in timed_lines
            if window_start <= start_seconds
            and (start_seconds < window_end or (is_last and start_seconds <= window_end))
        ]
        if lines:
            windows.append(
                {"start": window_start, "end": window_end, "transcript": "\n".join(lines)}
            )
    return windows


def _estimate_tokens(text: str) -> int:
    """Estimate request tokens from text length (about four chars per token)."""
    return max(1, (len(text) + CHARS_PER_ESTIMATED_TOKEN - 1) // CHARS_PER_ESTIMATED_TOKEN)


def _window_bounds_from_lines(
    lines: List[str], fallback_start: int, fallback_end: int
) -> tuple[int, int]:
    """Return the absolute start/end carried by a chunk's transcript lines."""
    starts: List[int] = []
    ends: List[int] = []
    for line in lines:
        match = TRANSCRIPT_SPAN_RE.match(line.strip())
        if not match:
            continue
        try:
            starts.append(_parse_transcript_timestamp_seconds(match.group("start")))
            ends.append(_parse_transcript_timestamp_seconds(match.group("end")))
        except ValueError:
            continue
    if starts and ends:
        return min(starts), max(ends)
    return fallback_start, fallback_end


def _split_window_for_token_budget(
    window: Dict[str, Any],
    max_tokens: int,
    clip_signals: Optional[str] = None,
    performance_context: Optional[str] = None,
    clip_length: str = "auto",
    source_context: Optional[str] = None,
    scene_cut_times: Optional[List[float]] = None,
) -> List[Dict[str, Any]]:
    """Split one duration window at line boundaries until each prompt fits.

    Splitting only happens between transcript spans, so a selected segment can
    never start or end mid-sentence because of this budget. When the window is
    already under budget it is returned unchanged, preserving the high-limit
    single-request path.
    """
    transcript = window["transcript"]
    bounds = (window["start"], window["end"])

    def prompt_for(text: str) -> str:
        return build_transcript_analysis_prompt(
            transcript=text,
            clip_signals=clip_signals,
            window=bounds,
            performance_context=performance_context,
            clip_length=clip_length,
            source_context=source_context,
            scene_cut_times=scene_cut_times,
        )

    if _estimate_tokens(prompt_for(transcript)) <= max_tokens:
        return [window]

    lines = [line for line in transcript.splitlines() if line.strip()]
    if len(lines) <= 1:
        logger.warning(
            "A transcript window exceeds the %s-token budget but has no line boundary to split on",
            max_tokens,
        )
        return [window]

    chunks: List[List[str]] = []
    current: List[str] = []
    for line in lines:
        candidate = current + [line]
        if current and _estimate_tokens(prompt_for("\n".join(candidate))) > max_tokens:
            chunks.append(current)
            current = [line]
        else:
            current = candidate
    if current:
        chunks.append(current)

    if len(chunks) <= 1:
        logger.warning(
            "A single transcript line exceeds the %s-token budget; keeping it intact",
            max_tokens,
        )
        return [window]

    result: List[Dict[str, Any]] = []
    for chunk in chunks:
        chunk_start, chunk_end = _window_bounds_from_lines(
            chunk, bounds[0], bounds[1]
        )
        result.append(
            {
                "start": chunk_start,
                "end": chunk_end,
                "transcript": "\n".join(chunk),
            }
        )
    return result


def _filter_clip_signals_for_window(
    clip_signals: Optional[str], window_start: int, window_end: int
) -> Optional[str]:
    """Keep only the signal lines that fall inside one analysis window."""
    if not clip_signals:
        return None
    header: List[str] = []
    kept: List[str] = []
    for line in clip_signals.splitlines():
        match = SIGNAL_LINE_PATTERN.match(line.strip())
        if not match:
            if not kept:
                header.append(line)
            continue
        try:
            signal_start = _parse_transcript_timestamp_seconds(match.group("start"))
        except ValueError:
            continue
        if window_start <= signal_start < window_end:
            kept.append(line)
    if not kept:
        return None
    return "\n".join(header + kept)


def _segment_bounds_seconds(segment: TranscriptSegment) -> tuple[int, int]:
    return (
        _parse_transcript_timestamp_seconds(segment.start_time),
        _parse_transcript_timestamp_seconds(segment.end_time),
    )


def _segment_rank_key(
    segment: TranscriptSegment,
    score_weights: Optional[Dict[str, float]] = None,
) -> tuple[float, float]:
    """Best-first ordering key: virality (primary), relevance (secondary).

    With learned ``score_weights`` (auto_director.learning), the primary
    key is the weighted sum of the four sub-scores instead of the plain
    total — the one place user feedback bends selection. ``None`` keeps
    the historical behaviour exactly.
    """
    if score_weights and segment.virality:
        # Imported lazily: auto_director's package init imports this
        # module (via classify), so a top-level import would be circular.
        from .auto_director.learning import weighted_total

        virality = segment.virality
        return (
            weighted_total(
                {
                    "hook_score": virality.hook_score,
                    "engagement_score": virality.engagement_score,
                    "value_score": virality.value_score,
                    "shareability_score": virality.shareability_score,
                },
                score_weights,
            ),
            segment.relevance_score,
        )
    return (
        segment.virality.total_score if segment.virality else 0,
        segment.relevance_score,
    )


def _overlap_ratio(first: tuple[int, int], second: tuple[int, int]) -> float:
    """Overlap as a share of the shorter range (1.0 = one contains the other)."""
    shorter = min(first[1] - first[0], second[1] - second[0])
    if shorter <= 0:
        return 0.0
    overlap = min(first[1], second[1]) - max(first[0], second[0])
    return max(0, overlap) / shorter


def dedupe_overlapping_segments(
    segments: List[TranscriptSegment],
    max_overlap_ratio: float = CANDIDATE_DEDUPE_OVERLAP_RATIO,
    score_weights: Optional[Dict[str, float]] = None,
) -> List[TranscriptSegment]:
    """Sort candidates best-first and drop ones that mostly repeat a stronger pick.

    Overlapping windows can surface the same moment twice; keep the higher score.
    ``score_weights`` (learned per-niche weights) only changes the sort key.
    """
    kept: List[tuple[TranscriptSegment, tuple[int, int]]] = []
    for segment in sorted(
        segments,
        key=lambda item: _segment_rank_key(item, score_weights),
        reverse=True,
    ):
        bounds = _segment_bounds_seconds(segment)
        if any(
            _overlap_ratio(bounds, kept_bounds) > max_overlap_ratio
            for _, kept_bounds in kept
        ):
            logger.info(
                "Dropping overlapping candidate %s-%s", segment.start_time, segment.end_time
            )
            continue
        kept.append((segment, bounds))
    return [segment for segment, _ in kept]


# --- Intro / teaser exclusion (Quality Director item 3) -------------------
#
# Produced podcasts open with a teaser montage: a dense run of punchy
# one-liners cut from later in the episode. It scores like a great clip
# (it was produced to), so plain best-first selection kept picking the
# source's own trailer — the user's 2026-11-08 run shipped the intro as
# a "clip". When the opening zone shows that montage signature AND
# enough real candidates exist outside it, candidates fully contained
# in the zone are dropped before the final slice. A short video that
# lives entirely inside the zone is untouched, and an opening that is
# ordinary continuous speech (sparse line boundaries) is not treated as
# a montage at all.
INTRO_ZONE_DEFAULT_SECONDS = 45
# A montage signature: at least this many transcript lines fully inside
# the zone, with a median line length at or below the one-liner bound.
INTRO_MONTAGE_MIN_LINES = 5
INTRO_MONTAGE_MEDIAN_LINE_SECONDS = 7.0


def _intro_zone_seconds() -> int:
    """ANALYSIS_INTRO_ZONE_SECONDS (default 45); 0 disables the exclusion."""
    raw = os.environ.get("ANALYSIS_INTRO_ZONE_SECONDS", "").strip()
    if not raw:
        return INTRO_ZONE_DEFAULT_SECONDS
    try:
        return max(0, int(raw))
    except ValueError:
        return INTRO_ZONE_DEFAULT_SECONDS


def _opening_is_montage(
    transcript_lines: List[Dict[str, Any]], zone_seconds: float
) -> bool:
    """Whether the opening zone looks like a produced teaser montage.

    Dense, short transcript lines (one-liners) are the signature; a
    continuous monologue produces few, long lines and is left alone.
    """
    inside = [
        line
        for line in transcript_lines or []
        if float(line.get("end_seconds") or 0) <= zone_seconds
        and float(line.get("end_seconds") or 0) > float(line.get("start_seconds") or 0)
    ]
    if len(inside) < INTRO_MONTAGE_MIN_LINES:
        return False
    durations = sorted(
        float(line["end_seconds"]) - float(line["start_seconds"]) for line in inside
    )
    median = durations[len(durations) // 2]
    return median <= INTRO_MONTAGE_MEDIAN_LINE_SECONDS


def exclude_intro_candidates(
    candidates: List[TranscriptSegment],
    transcript_lines: List[Dict[str, Any]],
    *,
    needed: int,
    source_seconds: float,
    zone_seconds: Optional[float] = None,
) -> List[TranscriptSegment]:
    """Drop teaser-zone candidates when real content can fill the slate.

    A candidate counts as intro material only when it is fully
    contained in the opening zone (``end <= zone``); a segment that
    starts in the zone and runs past it is real content and stays. The
    drop happens only when at least ``needed`` candidates survive
    outside the zone AND the zone shows the montage signature — so the
    exclusion can never shrink the slate or misfire on a monologue.
    """
    zone = _intro_zone_seconds() if zone_seconds is None else zone_seconds
    if zone <= 0 or source_seconds <= zone or not candidates:
        return candidates
    contained: List[TranscriptSegment] = []
    outside: List[TranscriptSegment] = []
    for candidate in candidates:
        _start, end = _segment_bounds_seconds(candidate)
        (contained if end <= zone else outside).append(candidate)
    if not contained or len(outside) < max(1, needed):
        return candidates
    if not _opening_is_montage(transcript_lines, zone):
        return candidates
    logger.info(
        "Intro exclusion: dropping %d teaser-zone candidate(s) inside "
        "the first %ss; %d real candidates remain",
        len(contained),
        zone,
        len(outside),
    )
    return outside


def validate_target_clip_count(value: object) -> Optional[int]:
    """Validate an explicit per-task clip-count target.

    ``None`` means Auto: the duration-based formula decides, exactly as
    before. Any other value must be a whole number between
    ``MIN_FINAL_CLIPS`` and ``ABSOLUTE_MAX_FINAL_CLIPS`` inclusive; bools
    are rejected even though ``bool`` is an ``int`` subclass.
    """
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError("target_clip_count must be a whole number of clips")
    if not MIN_FINAL_CLIPS <= value <= ABSOLUTE_MAX_FINAL_CLIPS:
        raise ValueError(
            f"target_clip_count must be between {MIN_FINAL_CLIPS} "
            f"and {ABSOLUTE_MAX_FINAL_CLIPS}"
        )
    return value


def max_final_clips_for_duration(
    source_seconds: float,
    max_clips: int = MAX_FINAL_CLIPS,
    target_clip_count: Optional[int] = None,
) -> int:
    """Scale the clip budget with source length (about one clip per 5 minutes),
    never above ``max_clips`` (the configured MAX_CLIPS).

    An explicit ``target_clip_count`` replaces the formula for that task: it
    is both the aim and the cap (hard-clamped to ``ABSOLUTE_MAX_FINAL_CLIPS``
    as a defence, though creation-time validation already enforces it)."""
    if target_clip_count is not None:
        return max(1, min(ABSOLUTE_MAX_FINAL_CLIPS, int(target_clip_count)))
    ceiling = max(1, min(MAX_FINAL_CLIPS, max_clips))
    return max(
        min(MIN_FINAL_CLIPS, ceiling),
        min(ceiling, math.ceil(max(0.0, source_seconds) / SECONDS_PER_FINAL_CLIP)),
    )


def _segment_target_for(target_clip_count: int, call_count: int) -> str:
    """Per-call segment ask that lets the candidate pool reach a target.

    The target is spread across the analysis calls with headroom for the
    candidates dedupe will drop: every call is asked for its share plus
    three extra candidates (the N+3 over-ask), so the merged pool always
    exceeds the target and the rerank chooses instead of merely
    accepting. Capped so one call never gets an absurd quota — a single
    call carrying a large target is the one shape the cap can hold
    below +3. Formatted like the built-in asks ("3-5", "2-5")."""
    per_call = math.ceil(target_clip_count / max(1, call_count)) + 3
    high = min(12, max(5, per_call))
    low = max(2, high - 3)
    return f"{low}-{high}"


def _rescale_virality(virality: ViralityAnalysis, new_total: int) -> None:
    """Set a new total while keeping the subscores' proportions and 0-25 bounds."""
    new_total = max(0, min(100, int(new_total)))
    subscores = [
        virality.hook_score,
        virality.engagement_score,
        virality.value_score,
        virality.shareability_score,
    ]
    old_total = sum(subscores)
    if old_total <= 0:
        scaled = [new_total / 4] * 4
    else:
        scaled = [score * new_total / old_total for score in subscores]
    rounded = [min(25, max(0, int(value))) for value in scaled]
    remainder = new_total - sum(rounded)
    by_fraction = sorted(range(4), key=lambda i: scaled[i] - int(scaled[i]), reverse=True)
    while remainder > 0:
        progressed = False
        for index in by_fraction:
            if remainder <= 0:
                break
            if rounded[index] < 25:
                rounded[index] += 1
                remainder -= 1
                progressed = True
        if not progressed:
            break
    (
        virality.hook_score,
        virality.engagement_score,
        virality.value_score,
        virality.shareability_score,
    ) = rounded
    virality.total_score = sum(rounded)


def apply_candidate_ranking(
    candidates: List[TranscriptSegment], ranking: CandidateRanking
) -> List[TranscriptSegment]:
    """Reorder candidates by the rerank and give them its calibrated scores.

    Scores are kept strictly decreasing down the ranking, so the displayed
    order and the displayed scores can never contradict each other: the
    rerank prompt asks for comparative scores, best first, and the UI sorts
    clips by this score, but a model may still invert a pair or tie two
    candidates. A rerank score is carried through unchanged whenever it
    already sits below the previous score; only an inversion or a tie is
    stepped down to exactly one point below the previous score. The old
    clamp (``min(score, ceiling)``) collapsed the whole slate to the first
    score on any inversion, so a reranked slate could report a flat tie
    (e.g. 86/86/86/86) that neither pass ever produced. Candidates the
    reranker skipped keep their first-pass order after the ranked ones,
    stepped down the same way.
    """
    ordered: List[tuple[TranscriptSegment, int]] = []
    seen: set[int] = set()
    for item in ranking.ranking:
        candidate_id = item.candidate_id
        if candidate_id in seen or not 0 <= candidate_id < len(candidates):
            continue
        seen.add(candidate_id)
        ordered.append((candidates[candidate_id], item.score))
    if not ordered:
        raise ValueError("Rerank returned no usable candidate ids")

    reranked: List[TranscriptSegment] = []
    # One above the 0-100 cap, so the first rerank score passes through
    # unchanged and every later score must sit at least one point below
    # the score ahead of it.
    previous = 101
    for segment, score in ordered:
        target = max(0, min(score, previous - 1))
        _rescale_virality(segment.virality, target)
        previous = target
        reranked.append(segment)
    for index, segment in enumerate(candidates):
        if index in seen:
            continue
        target = max(0, min(segment.virality.total_score, previous - 1))
        _rescale_virality(segment.virality, target)
        previous = target
        reranked.append(segment)
    return reranked


async def rerank_candidates(
    candidates: List[TranscriptSegment],
    performance_context: str | None = None,
    *,
    model_name: Optional[str] = None,
    pacer: Optional[_AnalysisPacer] = None,
) -> List[TranscriptSegment]:
    """Compare all candidates in one call; keep first-pass order if that fails.

    ``performance_context`` lets the creator's audience history shape the
    final selection too, not just each window's first pass. The rerank call
    draws on the same provider bucket as the window calls, so an active
    pacer spaces it too.
    """
    if len(candidates) < 2:
        return candidates
    try:
        agent = get_rerank_agent()
        extra = (
            {"model_name": model_name, "pacer": pacer}
            if pacer is not None and pacer.min_interval_seconds > 0
            else {}
        )
        # The rerank is the final selection pass for long videos: its
        # calibrated order is the shipped slate. It thinks at premium
        # reasoning effort; the window scans that fed it stayed default.
        runtime_config = get_config()
        effective_model = (
            model_name
            or getattr(runtime_config, "rerank_llm", None)
            or runtime_config.llm
        )
        premium_settings = _premium_reasoning_settings(effective_model)
        if premium_settings is not None:
            extra["model_settings"] = premium_settings
        result = await _run_transcript_analysis(
            agent,
            build_candidate_rerank_prompt(candidates, performance_context),
            timeout_seconds=RERANK_TIMEOUT_SECONDS,
            **extra,
        )
        reranked = apply_candidate_ranking(candidates, result.output)
    except Exception as error:
        # Do not log the error text: provider bodies may contain transcript data.
        logger.warning(
            "Candidate rerank failed (%s); keeping first-pass order", type(error).__name__
        )
        return candidates
    logger.info("Reranked %d clip candidates", len(reranked))
    return reranked


async def _analyze_transcript_windows(
    agent: Agent | None,
    windows: List[Dict[str, Any]],
    clip_signals: Optional[str],
    performance_context: Optional[str] = None,
    clip_length: str = "auto",
    *,
    model_names: Optional[List[str]] = None,
    pacer: Optional[_AnalysisPacer] = None,
    failover: Optional[_AnalysisFailover] = None,
    default_model: Optional[str] = None,
    completed_windows: Optional[Dict[int, TranscriptAnalysis]] = None,
    on_window_complete: Optional[
        Callable[[Optional[str], int, Dict[str, Any]], Awaitable[None]]
    ] = None,
    checkpoint_key: Optional[str] = None,
    segment_target: Optional[str] = None,
    source_context: Optional[str] = None,
    scene_cut_times: Optional[List[float]] = None,
) -> List[TranscriptAnalysis]:
    """Analyse windows with bounded concurrency, tolerating some failures.

    When ``model_names`` is supplied, chunks rotate round-robin across them.
    Raises the first window error only when no window produced an analysis.
    """
    semaphore = asyncio.Semaphore(ANALYSIS_WINDOW_CONCURRENCY)

    async def run_window(index: int, window: Dict[str, Any]) -> TranscriptAnalysis:
        if completed_windows and index in completed_windows:
            logger.info(
                "Reusing checkpointed analysis for window %d/%d",
                index + 1,
                len(windows),
            )
            return completed_windows[index]
        async with semaphore:
            prompt = build_transcript_analysis_prompt(
                transcript=window["transcript"],
                clip_signals=_filter_clip_signals_for_window(
                    clip_signals, window["start"], window["end"]
                ),
                window=(window["start"], window["end"]),
                performance_context=performance_context,
                clip_length=clip_length,
                segment_target=segment_target,
                source_context=source_context,
                scene_cut_times=scene_cut_times,
            )
            window_model = (
                model_names[index % len(model_names)] if model_names else None
            )
            if window_model:
                # A window model whose agent cannot be built advances
                # down the chain (D-1) instead of failing every window
                # assigned to it; the used model rides along so the
                # failover bookkeeping names the route actually built.
                window_agent, used_window_model = (
                    _build_transcript_agent_advancing(
                        clip_length, window_model, failover
                    )
                )
            else:
                window_agent, used_window_model = agent, None
            if failover is not None:
                result = await failover.run(
                    prompt=prompt,
                    clip_length=clip_length,
                    primary_model=used_window_model or default_model,
                    primary_agent=window_agent,
                    pacer=pacer,
                )
                analysis = result.output
            else:
                # Pacing is the only consumer of these kwargs. Omitting them when
                # it is off keeps the original two-argument call signature.
                extra = (
                    {"model_name": window_model, "pacer": pacer}
                    if pacer is not None and pacer.min_interval_seconds > 0
                    else {}
                )
                result = await _run_transcript_analysis(window_agent, prompt, **extra)
                analysis = result.output
            if on_window_complete is not None:
                try:
                    await on_window_complete(
                        checkpoint_key, index, analysis.model_dump()
                    )
                except Exception:
                    # Checkpointing must never fail the analysis itself.
                    logger.warning(
                        "Could not checkpoint analysis window %d/%d",
                        index + 1,
                        len(windows),
                        exc_info=True,
                    )
            return analysis

    tasks = [
        asyncio.create_task(run_window(index, window))
        for index, window in enumerate(windows)
    ]
    try:
        _, pending = await asyncio.wait(tasks, timeout=ANALYSIS_TOTAL_TIMEOUT_SECONDS)
    except asyncio.CancelledError:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        raise
    if pending:
        logger.warning(
            "%d of %d analysis windows exceeded the %ss budget",
            len(pending),
            len(windows),
            ANALYSIS_TOTAL_TIMEOUT_SECONDS,
        )
        for task in pending:
            task.cancel()
        await asyncio.gather(*pending, return_exceptions=True)

    analyses: List[TranscriptAnalysis] = []
    errors: List[BaseException] = []
    for index, task in enumerate(tasks):
        if task in pending:
            continue
        error = task.exception()
        if error is not None:
            logger.warning(
                "Analysis window %d/%d failed (%s)", index + 1, len(windows), type(error).__name__
            )
            errors.append(error)
            continue
        analyses.append(task.result())

    if not analyses:
        if errors:
            raise errors[0]
        raise RuntimeError(
            "The AI analysis service took too long to respond. Please try again shortly."
        )
    return analyses


def _snap_segment_bounds(
    segment: TranscriptSegment,
    transcript_lines: List[Dict[str, Any]],
    start_seconds: int,
    end_seconds: int,
) -> tuple[int, int]:
    """Move a segment onto transcript line boundaries when they are close."""
    snapped = _snap_bounds_to_transcript_lines(transcript_lines, start_seconds, end_seconds)
    if not snapped:
        return start_seconds, end_seconds
    start_line, end_line = snapped
    if (start_line["start_time"], end_line["end_time"]) != (
        segment.start_time,
        segment.end_time,
    ):
        logger.info(
            "Snapped segment %s-%s to transcript lines %s-%s",
            segment.start_time,
            segment.end_time,
            start_line["start_time"],
            end_line["end_time"],
        )
        segment.start_time = start_line["start_time"]
        segment.end_time = end_line["end_time"]
    return start_line["start_seconds"], end_line["end_seconds"]


def _validate_segments(
    segments: List[TranscriptSegment],
    transcript_lines: List[Dict[str, Any]],
    transcript_spans: List[Dict[str, Any]],
    clip_length: str = "auto",
    creator_name: Optional[str] = None,
) -> List[TranscriptSegment]:
    """Ground model segments onto real transcript lines and drop unusable ones."""
    bounds = clip_duration(clip_length)
    validated_segments: List[TranscriptSegment] = []
    for segment in segments:
        # Validate text content
        if not segment.text.strip() or len(segment.text.split()) < 3:
            logger.warning(
                f"Skipping segment with insufficient content: '{segment.text[:50]}...'"
            )
            continue

        # Validate timestamps - CRITICAL: start and end must be different
        if segment.start_time == segment.end_time:
            logger.warning(
                f"Skipping segment with identical start/end times: {segment.start_time}"
            )
            continue

        # Parse timestamps to validate duration
        try:
            start_seconds = _parse_transcript_timestamp_seconds(segment.start_time)
            end_seconds = _parse_transcript_timestamp_seconds(segment.end_time)
            start_seconds, end_seconds = _snap_segment_bounds(
                segment, transcript_lines, start_seconds, end_seconds
            )
            duration = end_seconds - start_seconds

            if duration < bounds.minimum or duration > bounds.maximum:
                repaired_bounds = _repair_segment_bounds(
                    segment,
                    transcript_spans,
                    start_seconds,
                    end_seconds,
                    clip_length,
                )
                if repaired_bounds:
                    # Repairs format hours as HH:MM:SS; snap back onto line labels.
                    start_seconds, end_seconds = _snap_segment_bounds(
                        segment, transcript_lines, *repaired_bounds
                    )
                    duration = end_seconds - start_seconds

            if duration <= 0:
                logger.warning(
                    f"Skipping segment with invalid duration: {segment.start_time} to {segment.end_time} = {duration}s"
                )
                continue

            if duration < bounds.minimum:
                logger.warning(
                    f"Skipping segment too short: {duration}s (min {bounds.minimum}s required)"
                )
                continue

            if duration > bounds.maximum:
                logger.warning(
                    f"Skipping segment too long: {duration}s (max {bounds.maximum}s allowed)"
                )
                continue

            grounded_text = _extract_transcript_text_for_segment(
                transcript_lines,
                segment.start_time,
                segment.end_time,
            )
            if grounded_text is None:
                logger.warning(
                    "Skipping segment with timestamps not aligned to transcript lines: %s-%s",
                    segment.start_time,
                    segment.end_time,
                )
                continue
            if _normalize_transcript_text(segment.text) != _normalize_transcript_text(
                grounded_text
            ):
                logger.warning(
                    "Adjusting segment text to transcript-backed span for %s-%s",
                    segment.start_time,
                    segment.end_time,
                )
                segment.text = grounded_text

            # Validate virality scores
            if segment.virality:
                # Ensure total score is sum of subscores
                calculated_total = (
                    segment.virality.hook_score
                    + segment.virality.engagement_score
                    + segment.virality.value_score
                    + segment.virality.shareability_score
                )
                if segment.virality.total_score != calculated_total:
                    logger.warning(
                        f"Correcting virality total: {segment.virality.total_score} -> {calculated_total}"
                    )
                    segment.virality.total_score = calculated_total

            segment.hook_title = sanitize_hook_title(
                segment.hook_title,
                creator_name=creator_name,
                fallback_text=segment.text,
            )
            # Bounds are final here (after snapping and repair): keep the teaser
            # only when it still sits inside the clip.
            _sanitize_hook_span(segment, start_seconds, end_seconds)

            validated_segments.append(segment)
            virality_info = (
                f", virality={segment.virality.total_score}"
                if segment.virality
                else ""
            )
            logger.info(
                f"Validated segment: {segment.start_time}-{segment.end_time} ({duration}s){virality_info}"
            )

        except (ValueError, IndexError) as e:
            logger.warning(
                f"Skipping segment with invalid timestamp format: {segment.start_time}-{segment.end_time}: {e}"
            )
            continue
    return validated_segments


def _merge_key_topics(analyses: List[TranscriptAnalysis], limit: int = 12) -> List[str]:
    topics: List[str] = []
    seen: set[str] = set()
    for analysis in analyses:
        for topic in analysis.key_topics or []:
            key = topic.strip().lower()
            if key and key not in seen:
                seen.add(key)
                topics.append(topic.strip())
    return topics[:limit]


async def _select_analysis_models_managed(
    runtime_config: Config, *, window_count: int
) -> list[str] | None:
    """Provider-manager model choice for one analysis run.

    Sits *above* the window/retry machinery: it warms the cooldown registry,
    probes the local model, and returns the ordered model list for this run —
    or None when nothing needs rerouting and the caller keeps today's default
    single-model path. In local-only mode it raises
    ``LocalProviderUnavailable`` (plain words) when Ollama is unreachable.
    """
    manager = provider_manager.get_manager()
    await manager.warm_up()
    local_alias = provider_manager.local_model_name(runtime_config)
    resolve_base_url = getattr(runtime_config, "resolve_ollama_base_url", None)
    base_url = (
        resolve_base_url()
        if callable(resolve_base_url)
        else "http://localhost:11434/v1"
    )
    local_healthy = await manager.probe_local_health(local_alias, base_url)
    return provider_manager.select_analysis_models(
        runtime_config, window_count=window_count, local_healthy=local_healthy
    )


def analysis_windows_fingerprint(
    transcript: str,
    windows: List[Dict[str, Any]],
    model_names: Optional[List[str]],
    max_tokens: int,
) -> str:
    """Identify one exact windowed-analysis plan for checkpoint reuse.

    Saved per-window results are only reused when the transcript, the
    window bounds, the models and the token budget all match the plan that
    produced them; anything else re-analyses from scratch.
    """
    payload = json.dumps(
        [
            TRANSCRIPT_ANALYSIS_CACHE_VERSION,
            transcript,
            [(w["start"], w["end"]) for w in windows],
            list(model_names or []),
            max_tokens,
        ],
        sort_keys=True,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _clamp_segment_to_allowed_ranges(
    segment: TranscriptSegment,
    allowed: List[tuple],
    transcript_lines: List[Dict[str, Any]],
    minimum_seconds: float,
) -> Optional[TranscriptSegment]:
    """S1 clamp: keep only the longest in-range piece of a candidate.

    Re-grounds the text on the clamped span; returns None when nothing
    usable survives (zero overlap, or the piece is under the clip
    minimum). Expansion beyond the allowed ranges is impossible here:
    the piece is an intersection, never an extension.
    """
    from .auto_director.content_requirements import clamp_segment_to_allowed

    start_seconds, end_seconds = _segment_bounds_seconds(segment)
    if any(a <= start_seconds and end_seconds <= b for a, b in allowed):
        return segment
    pieces = clamp_segment_to_allowed(start_seconds, end_seconds, allowed)
    if not pieces:
        return None
    piece_start, piece_end = max(pieces, key=lambda p: p[1] - p[0])
    inside = [
        line
        for line in transcript_lines
        if line["end_seconds"] > piece_start and line["start_seconds"] < piece_end
    ]
    if not inside:
        return None
    new_start = float(inside[0]["start_seconds"])
    new_end = float(inside[-1]["end_seconds"])
    # Intersect again on line boundaries: a line straddling the boundary
    # must not pull the candidate back outside the allowed range.
    if new_start < piece_start or new_end > piece_end:
        inside = [
            line
            for line in inside
            if line["start_seconds"] >= piece_start and line["end_seconds"] <= piece_end
        ]
        if not inside:
            return None
        new_start = float(inside[0]["start_seconds"])
        new_end = float(inside[-1]["end_seconds"])
    if new_end - new_start < minimum_seconds:
        return None
    segment.start_time = inside[0]["start_time"]
    segment.end_time = inside[-1]["end_time"]
    segment.text = " ".join(line["text"] for line in inside if line["text"]).strip()
    if segment.hook_start_time or segment.hook_end_time:
        try:
            hook_start = _parse_transcript_timestamp_seconds(segment.hook_start_time or "")
            hook_end = _parse_transcript_timestamp_seconds(segment.hook_end_time or "")
            if hook_start < new_start or hook_end > new_end:
                segment.hook_start_time = None
                segment.hook_end_time = None
        except Exception:
            segment.hook_start_time = None
            segment.hook_end_time = None
    return segment


def _clamp_segment_outside_excluded_ranges(
    segment: TranscriptSegment,
    excluded: List[tuple],
    transcript_lines: List[Dict[str, Any]],
    minimum_seconds: float,
) -> Optional[TranscriptSegment]:
    """S1 exclusion clamp: cut the brief's excluded ranges out of a
    candidate, keeping the longest surviving piece.

    Mirror of _clamp_segment_to_allowed_ranges with the opposite sign:
    a candidate that merely touches an exclusion boundary survives
    whole; one swallowed by an exclusion returns None.
    """
    from .auto_director.content_requirements import clamp_segment_outside_excluded

    start_seconds, end_seconds = _segment_bounds_seconds(segment)
    if not any(
        start_seconds < x_end and x_start < end_seconds
        for x_start, x_end in excluded
    ):
        return segment
    pieces = clamp_segment_outside_excluded(start_seconds, end_seconds, excluded)
    if not pieces:
        return None
    piece_start, piece_end = max(pieces, key=lambda p: p[1] - p[0])
    inside = [
        line
        for line in transcript_lines
        if line["end_seconds"] > piece_start and line["start_seconds"] < piece_end
    ]
    if not inside:
        return None
    new_start = float(inside[0]["start_seconds"])
    new_end = float(inside[-1]["end_seconds"])
    if new_start < piece_start or new_end > piece_end:
        inside = [
            line
            for line in inside
            if line["start_seconds"] >= piece_start and line["end_seconds"] <= piece_end
        ]
        if not inside:
            return None
        new_start = float(inside[0]["start_seconds"])
        new_end = float(inside[-1]["end_seconds"])
    if new_end - new_start < minimum_seconds:
        return None
    segment.start_time = inside[0]["start_time"]
    segment.end_time = inside[-1]["end_time"]
    segment.text = " ".join(line["text"] for line in inside if line["text"]).strip()
    if segment.hook_start_time or segment.hook_end_time:
        try:
            hook_start = _parse_transcript_timestamp_seconds(segment.hook_start_time or "")
            hook_end = _parse_transcript_timestamp_seconds(segment.hook_end_time or "")
            if hook_start < new_start or hook_end > new_end:
                segment.hook_start_time = None
                segment.hook_end_time = None
        except Exception:
            segment.hook_start_time = None
            segment.hook_end_time = None
    return segment


async def get_most_relevant_parts_by_transcript(
    transcript: str,
    clip_signals: str | None = None,
    performance_context: str | None = None,
    clip_length: str = "auto",
    include_reserve: bool = False,
    score_weights: Optional[Dict[str, float]] = None,
    target_clip_count: Optional[int] = None,
    content_brief: Optional[Dict[str, Any]] = None,
    window_checkpoint: Optional[Dict[str, Any]] = None,
    on_window_complete: Optional[
        Callable[[Optional[str], int, Dict[str, Any]], Awaitable[None]]
    ] = None,
    creator_name: Optional[str] = None,
    source_title: Optional[str] = None,
    source_description: Optional[str] = None,
    scene_cut_times: Optional[List[float]] = None,
) -> TranscriptAnalysis:
    """Get the most relevant parts of a transcript with virality scoring.

    Short transcripts are analysed in one call. Long ones are split into
    overlapping windows analysed concurrently, then deduplicated and reranked
    in one comparative call so scores are comparable across windows. Windows
    that would exceed the configured token budget are further split at
    transcript-line boundaries so each provider request stays small.
    """
    logger.info(
        f"Starting AI analysis of transcript ({len(transcript)} chars)"
    )

    try:
        runtime_config = get_config()
        transcript_lines = _parse_transcript_lines(transcript)
        transcript_spans = _parse_transcript_spans(transcript)
        total_seconds = max(
            (line["end_seconds"] for line in transcript_lines), default=0
        )

        # Rotation is opt-in: an empty ANALYSIS_MODELS keeps the original
        # single-agent path (and its cache) untouched.
        configured_models = getattr(runtime_config, "analysis_models", None) or []
        default_model = getattr(runtime_config, "llm", None)

        # Long transcripts take the long-form route: fewer, bigger windows
        # sent to one large-context model, instead of many small chunks
        # rotating across models that share per-minute token buckets.
        longform_threshold = (
            getattr(
                runtime_config,
                "analysis_longform_threshold_sec",
                DEFAULT_ANALYSIS_LONGFORM_THRESHOLD_SECONDS,
            )
            or 0
        )
        longform = longform_threshold > 0 and total_seconds > longform_threshold
        if longform:
            longform_model = getattr(
                runtime_config, "analysis_longform_model", ""
            ) or (configured_models[0] if configured_models else None)
            model_names = [longform_model] if longform_model else None
            max_tokens = (
                getattr(
                    runtime_config,
                    "analysis_longform_max_tokens",
                    DEFAULT_ANALYSIS_LONGFORM_MAX_TOKENS,
                )
                or 0
            )
            longform_window = (
                getattr(
                    runtime_config,
                    "analysis_longform_window_sec",
                    DEFAULT_ANALYSIS_LONGFORM_WINDOW_SECONDS,
                )
                or DEFAULT_ANALYSIS_LONGFORM_WINDOW_SECONDS
            )
            windows = split_transcript_into_windows(
                transcript,
                window_seconds=longform_window,
                single_window_max_seconds=longform_threshold,
            )
            analysis_tpm = getattr(
                runtime_config, "analysis_longform_tpm", 0
            ) or getattr(runtime_config, "analysis_tpm", DEFAULT_ANALYSIS_TPM)
            logger.info(
                "Long transcript (%ss) routed to %s: windows of up to %ss, "
                "%s-token budget",
                total_seconds,
                longform_model or default_model,
                longform_window,
                max_tokens,
            )
        else:
            model_names = configured_models or None
            max_tokens = getattr(
                runtime_config,
                "transcript_analysis_max_tokens",
                DEFAULT_TRANSCRIPT_ANALYSIS_MAX_TOKENS,
            )
            windows = split_transcript_into_windows(transcript)
            analysis_tpm = getattr(
                runtime_config, "analysis_tpm", DEFAULT_ANALYSIS_TPM
            )
        # Source metadata (title / channel / description) rides in every
        # analysis prompt for this video, built once so all calls carry
        # the identical block. Empty for metadata-less sources.
        source_context = _source_context_section(
            source_title, creator_name, source_description
        )

        if max_tokens and max_tokens > 0:
            budgeted_windows: List[Dict[str, Any]] = []
            for window in windows:
                budgeted_windows.extend(
                    _split_window_for_token_budget(
                        window,
                        max_tokens=max_tokens,
                        clip_signals=_filter_clip_signals_for_window(
                            clip_signals, window["start"], window["end"]
                        ),
                        performance_context=performance_context,
                        clip_length=clip_length,
                        source_context=source_context,
                        scene_cut_times=scene_cut_times,
                    )
                )
            if len(budgeted_windows) != len(windows):
                logger.info(
                    "Split %d transcript window(s) into %d token-budget chunks",
                    len(windows),
                    len(budgeted_windows),
                )
            windows = budgeted_windows

        # An explicit clip-count target raises the per-call segment ask so
        # the merged candidate pool can actually reach the target; without
        # one the asks stay at their built-in values and every request is
        # byte-identical to before. The count uses the final window list, so
        # token-budget splits are already accounted for.
        segment_target = (
            _segment_target_for(target_clip_count, len(windows))
            if target_clip_count is not None
            else None
        )

        if provider_manager.manager_enabled(runtime_config):
            # Provider-manager layer: skip cooled-down aliases, prefer the
            # healthy local model for routine (single-window) work, and keep
            # local as the final fallback for long analyses. Returns None
            # when nothing needs rerouting, preserving the path above. The
            # manager must never break an analysis: unexpected manager errors
            # fall back to the configured models (local-only mode's plain
            # LocalProviderUnavailable message is intentional and passes).
            # The long-form route is the one exception: it deliberately
            # pins a single large-context model, so rerouting is skipped
            # (the per-chunk failover below still falls back to the local
            # models on rate limits or a dead gateway) — except in
            # local-only mode, where the manager must run so the cloud is
            # never called.
            if longform and (
                provider_manager.provider_mode(runtime_config) != "local-only"
            ):
                logger.info(
                    "Long-form analysis keeps its pinned model %s; "
                    "provider-manager rerouting skipped",
                    model_names,
                )
            else:
                try:
                    model_names = await _select_analysis_models_managed(
                        runtime_config, window_count=len(windows)
                    )
                except provider_manager.LocalProviderUnavailable:
                    raise
                except Exception as manager_error:
                    logger.warning(
                        "Provider manager selection failed (%s); using configured models",
                        manager_error,
                    )
        pacer = _build_analysis_pacer(
            runtime_config,
            chunk_count=len(windows),
            total_tokens=_estimate_tokens(transcript),
            tpm=analysis_tpm,
        )
        failover = _AnalysisFailover(
            configured_models,
            getattr(runtime_config, "analysis_fallback_model", "openai-chat:local-qwen"),
            getattr(
                runtime_config, "analysis_direct_fallback_model", "ollama:qwen3:8b"
            ),
        )
        agent = None
        default_agent_model: Optional[str] = None
        if not model_names:
            # An unbuildable default route advances down the chain
            # (D-1) instead of killing the analysis at construction.
            agent, default_agent_model = _build_transcript_agent_advancing(
                clip_length, None, failover
            )

        if len(windows) == 1:
            single_model = model_names[0] if model_names else None
            if single_model:
                single_agent, single_model_used = (
                    _build_transcript_agent_advancing(
                        clip_length, single_model, failover
                    )
                )
            else:
                single_agent, single_model_used = agent, default_agent_model
            single_prompt = build_transcript_analysis_prompt(
                transcript=transcript,
                clip_signals=clip_signals,
                performance_context=performance_context,
                clip_length=clip_length,
                segment_target=segment_target,
                source_context=source_context,
                scene_cut_times=scene_cut_times,
            )
            result = await failover.run(
                prompt=single_prompt,
                clip_length=clip_length,
                primary_model=single_model_used or default_model,
                primary_agent=single_agent,
                pacer=pacer,
                # A single-window video has no separate rerank: this call
                # IS the final selection pass, so it thinks at premium
                # effort. Window scans of long videos stay at default.
                model_settings=_premium_reasoning_settings(
                    single_model_used or default_model
                ),
            )
            analyses = [result.output]
        else:
            logger.info("Analysing transcript in %d overlapping windows", len(windows))
            checkpoint_key = analysis_windows_fingerprint(
                transcript, windows, model_names, max_tokens
            )
            completed_windows: Dict[int, TranscriptAnalysis] = {}
            if window_checkpoint and window_checkpoint.get("key") == checkpoint_key:
                for raw_index, payload in (
                    window_checkpoint.get("windows") or {}
                ).items():
                    try:
                        completed_windows[int(raw_index)] = (
                            TranscriptAnalysis.model_validate(payload)
                        )
                    except Exception:
                        continue
                if completed_windows:
                    logger.info(
                        "Resuming analysis with %d checkpointed window(s)",
                        len(completed_windows),
                    )
            analyses = await _analyze_transcript_windows(
                agent,
                windows,
                clip_signals,
                performance_context,
                clip_length,
                model_names=model_names,
                pacer=pacer,
                failover=failover,
                default_model=default_model,
                completed_windows=completed_windows,
                on_window_complete=on_window_complete,
                checkpoint_key=checkpoint_key,
                segment_target=segment_target,
                source_context=source_context,
                scene_cut_times=scene_cut_times,
            )

        candidates: List[TranscriptSegment] = []
        for analysis in analyses:
            logger.info(
                f"AI analysis found {len(analysis.most_relevant_segments)} segments"
            )
            candidates.extend(
                _validate_segments(
                    analysis.most_relevant_segments,
                    transcript_lines,
                    transcript_spans,
                    clip_length,
                    creator_name=creator_name,
                )
            )

        # Content Requirements S1 (selection filter + clamp): with a brief,
        # no candidate may contain out-of-range footage, and matcher-built
        # supplementary candidates join the pool here so dedupe/rerank treat
        # them identically to model-picked ones. Without a brief this whole
        # block is skipped and selection is byte-identical to before.
        brief_stats: Dict[str, Any] = {}
        if content_brief:
            from .auto_director.content_requirements import (
                allowed_range_tuples,
                excluded_range_tuples,
                generate_key_timestamp_candidates,
                generate_requirement_candidates,
            )

            allowed = allowed_range_tuples(content_brief)
            excluded = excluded_range_tuples(content_brief)
            total_before = len(candidates)
            if allowed:
                bounds = clip_duration(clip_length)
                clamped: List[TranscriptSegment] = []
                for candidate in candidates:
                    kept = _clamp_segment_to_allowed_ranges(
                        candidate, allowed, transcript_lines, bounds.minimum
                    )
                    if kept is not None:
                        clamped.append(kept)
                candidates = clamped
            brief_stats["candidates_total"] = total_before
            brief_stats["candidates_dropped_range"] = total_before - len(candidates)
            brief_stats["candidates_in_range"] = len(candidates)
            supplementary = generate_requirement_candidates(
                transcript_lines,
                content_brief,
                clip_length=clip_length,
                allowed=allowed,
            )
            for item in supplementary:
                if not item.get("start_time") or not item.get("end_time"):
                    continue
                candidates.append(
                    TranscriptSegment(
                        start_time=item["start_time"],
                        end_time=item["end_time"],
                        text=item["text"],
                        relevance_score=item["relevance_score"],
                        reasoning=item["reasoning"],
                    )
                )
            brief_stats["supplementary_candidates"] = len(supplementary)
            key_candidates = generate_key_timestamp_candidates(
                transcript_lines,
                content_brief,
                clip_length=clip_length,
                allowed=allowed,
                excluded=excluded,
            )
            for item in key_candidates:
                if not item.get("start_time") or not item.get("end_time"):
                    continue
                candidates.append(
                    TranscriptSegment(
                        start_time=item["start_time"],
                        end_time=item["end_time"],
                        text=item["text"],
                        relevance_score=item["relevance_score"],
                        reasoning=item["reasoning"],
                    )
                )
            brief_stats["key_timestamp_candidates"] = len(key_candidates)
            if excluded:
                # Hard exclusion over the WHOLE pool (model picks,
                # requirement candidates, key-timestamp candidates):
                # no candidate may contain excluded footage.
                bounds = clip_duration(clip_length)
                before_exclusion = len(candidates)
                surviving: List[TranscriptSegment] = []
                for candidate in candidates:
                    kept = _clamp_segment_outside_excluded_ranges(
                        candidate, excluded, transcript_lines, bounds.minimum
                    )
                    if kept is not None:
                        surviving.append(kept)
                candidates = surviving
                brief_stats["candidates_dropped_excluded"] = (
                    before_exclusion - len(candidates)
                )
                brief_stats["candidates_in_range"] = len(candidates)

        # Best-first by virality (primary) then relevance (secondary), without
        # near-duplicates; then compare across windows in one rerank call.
        candidates = dedupe_overlapping_segments(
            candidates, score_weights=score_weights
        )
        if len(windows) > 1:
            if pacer.min_interval_seconds > 0:
                candidates = await rerank_candidates(
                    candidates,
                    performance_context,
                    model_name=getattr(runtime_config, "rerank_llm", None)
                    or default_model,
                    pacer=pacer,
                )
            else:
                candidates = await rerank_candidates(candidates, performance_context)

        source_seconds = max(
            (line["end_seconds"] for line in transcript_lines), default=0
        )
        limit = max_final_clips_for_duration(
            source_seconds,
            get_config().max_clips,
            target_clip_count=target_clip_count,
        )
        # Retain already-ranked alternatives until picture suitability checks
        # finish. No extra model call is needed when a slate is rejected.
        # The historic reserve cap of 24 predates explicit targets and would
        # silently break a 25- or 30-clip request, so targeted tasks keep
        # the full 2x reserve; Auto keeps the cap exactly as before.
        if target_clip_count is not None:
            reserve_limit = limit * 2
        else:
            reserve_limit = min(24, limit * 2)
        # Quality Director: never let the source's produced intro/teaser
        # take a slate slot when enough real candidates exist outside
        # the opening zone (see exclude_intro_candidates).
        candidates = exclude_intro_candidates(
            candidates,
            transcript_lines,
            needed=reserve_limit if include_reserve else limit,
            source_seconds=source_seconds,
        )
        # Content Requirements ranking: the LAST ordering step, after the
        # rerank (which rewrites scores on virality alone) and after intro
        # exclusion. One LLM focus pass over the pool, then a stable
        # mandatory-first partition; virality order survives inside tiers.
        brief_report: Optional[Dict[str, Any]] = None
        if content_brief:
            from .auto_director.content_requirements import (
                build_brief_report,
                evaluate_candidate_requirements,
                partition_by_tier,
            )
            from .auto_director.content_requirements_llm import judge_focus

            pool = [
                {
                    "candidate_id": index,
                    "start_time": candidate.start_time,
                    "end_time": candidate.end_time,
                    "text": candidate.text,
                }
                for index, candidate in enumerate(candidates)
            ]
            judgements = await judge_focus(pool, content_brief)
            llm_available = judgements is not None
            verdicts = []
            for index, candidate in enumerate(candidates):
                start_seconds, end_seconds = _segment_bounds_seconds(candidate)
                verdict = evaluate_candidate_requirements(
                    transcript_lines,
                    float(start_seconds),
                    float(end_seconds),
                    content_brief,
                    llm_judgements=(judgements or {}).get(str(index)),
                    llm_available=llm_available,
                )
                candidate.brief_verdict = verdict
                verdicts.append(verdict)
            candidates = partition_by_tier(candidates, verdicts)
            tier_a = sum(1 for v in verdicts if v["tier"] == "A")
            brief_report = build_brief_report(
                content_brief,
                candidates_total=brief_stats.get(
                    "candidates_total", len(candidates)
                ),
                candidates_in_range=brief_stats.get(
                    "candidates_in_range", len(candidates)
                ),
                candidates_compliant=tier_a,
                candidates_dropped_range=brief_stats.get("candidates_dropped_range", 0),
                candidates_dropped_mandatory=len(candidates) - tier_a,
                outcome="clips_selected" if tier_a else "no_compliant_moments",
                llm_pass="ran" if llm_available else "unavailable",
            )
        validated_segments = candidates[: reserve_limit if include_reserve else limit]

        final_analysis = TranscriptAnalysis(
            most_relevant_segments=validated_segments,
            # Windows analysed separately often return the same summary
            # text; joining verbatim repeats it ("...fixture (cached
            # ranges). Japan restaurants fixture (cached ranges).").
            # Drop exact duplicates, keeping first-seen order.
            summary=" ".join(
                dict.fromkeys(
                    analysis.summary.strip()
                    for analysis in analyses
                    if analysis.summary
                )
            ),
            key_topics=_merge_key_topics(analyses),
            brief_report=brief_report,
        )

        logger.info(f"Selected {len(validated_segments)} segments for processing")
        if validated_segments:
            top = validated_segments[0]
            logger.info(
                f"Top segment - relevance: {top.relevance_score:.2f}, virality: {top.virality.total_score if top.virality else 'N/A'}"
            )

        return final_analysis

    except (AnalysisRateLimitError, AnalysisGatewayUnreachableError):
        # Already plain-words and user-actionable; never wrap these into a
        # generic "Transcript analysis failed".
        raise
    except Exception as e:
        logger.error(f"Error in transcript analysis: {e}")
        raise RuntimeError(f"Transcript analysis failed: {str(e)}") from e


def get_most_relevant_parts_sync(transcript: str) -> TranscriptAnalysis:
    """Synchronous wrapper for the async function."""
    return asyncio.run(get_most_relevant_parts_by_transcript(transcript))
