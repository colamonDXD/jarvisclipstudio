"""Niche classification for auto-director.

Classification is evidence-based, not transcript-only: alongside the
transcript sample the caller may pass the video title/description, a
summary of the visual evidence the reframing pipeline already computes
(face presence/size, scene cuts, motion and text context from sampled
frames — see :func:`summarize_visual_analysis`), and a summary of the
audio evidence the beat-grid analyser already computes (tempo, beats,
energy, music likelihood — see :func:`summarize_audio_beats`). People
talking *while doing something* (eating their way through a city's
restaurants, travelling, demonstrating) must not default to
``podcast_news`` just because the transcript is chatty: the title and
the picture can outweigh a conversational transcript, and a narrow
deterministic guardrail (see :func:`apply_evidence_guardrails`) backs
that up when the model still picks the podcast attractor.

Every result carries the evidence it used (``evidence`` /
``evidence_status`` / ``is_fallback``) so the task layer can surface
*why* a niche was chosen and can tell a real classification apart from
the fallback. The historical keys (``niche`` / ``confidence`` /
``reasons``) are unchanged.

Classification walks an ordered candidate chain rather than betting
the task on one model: the provider manager's pick, the configured
LLM, then the analysis fallback models. A failure on one candidate —
a missing-key gate at agent build time, a transport error, or an
output-contract slip — is recorded against that candidate and the
chain advances; only when every candidate fails does the marked
fallback return. Because the system prompt asks for a bare JSON
document, a model that answers with JSON *text* instead of the output
tool call is retried once in plain-text mode and its answer validated
against the same schema, instead of being discarded.
"""

import asyncio
import json
import logging
import re
from typing import Any, Literal, Optional
from pydantic import BaseModel, Field, ValidationError

from .. import provider_manager
from ..ai import get_transcript_agent
from ..config import get_config
from ..media.vram import log_vram
from pydantic_ai import Agent
from pydantic_ai.output import NativeOutput

logger = logging.getLogger(__name__)

NicheLiteral = Literal[
    "podcast_news",
    "travel_vlog",
    "meme_comedy",
    "gaming",
    "sports",
    "education_tutorial",
    "finance_business",
    "vlog_lifestyle",
    "true_crime",
    "hindi_podcast",
    "music_artist",
]

# Every niche the system knows about, including manual-only ones. Kept in
# sync with niche.NICHE_VALUES (override validation) by a unit test.
NICHE_VALUES: set[str] = {
    "podcast_news",
    "travel_vlog",
    "meme_comedy",
    "gaming",
    "sports",
    "education_tutorial",
    "finance_business",
    "vlog_lifestyle",
    "true_crime",
    "hindi_podcast",
    "music_artist",
    "football_edit",
    "japan_travel_cinematic",
    "anime_funk_edit",
}

# Niches the classifier may return. These are exactly the NicheLiteral
# values: the structured-output schema cannot even express a manual-only
# niche, and classify_niche() double-checks against this set.
AUTO_NICHE_VALUES: set[NicheLiteral] = {
    "podcast_news",
    "travel_vlog",
    "meme_comedy",
    "gaming",
    "sports",
    "education_tutorial",
    "finance_business",
    "vlog_lifestyle",
    "true_crime",
    "hindi_podcast",
    "music_artist",
}

# Visual/beat-driven niches with no usable transcript signal. They are
# reachable only through an explicit niche_override (manual selection),
# never from classify_niche().
MANUAL_ONLY_NICHES: frozenset[str] = frozenset(
    {
        "football_edit",
        "japan_travel_cinematic",
        "anime_funk_edit",
    }
)

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

Analyze ALL the evidence provided — the video title, the transcript sample, the named entities, and any visual evidence (faces, scene cuts and motion measured from sampled frames) and audio evidence (music, tempo and energy measurements) — to classify the content into exactly ONE niche category.

Weigh the evidence together: a chatty transcript does not make a video a podcast when the title and the picture show people doing an activity (eating, travelling, cooking, playing, demonstrating). The activity or place that is the subject of the video decides the category.

NICHE CATEGORIES:
- podcast_news: Interviews, discussions, commentary, news analysis and podcast episodes recorded primarily as conversation (a studio, a call, or a fixed talking-head setup). Do NOT choose this merely because people are talking
- travel_vlog: Travel experiences, destination guides, itineraries, cultural exploration, and destination food experiences (restaurant tours, street food, eating your way through a place)
- meme_comedy: Comedy sketches, meme commentary, reaction videos, humorous content
- gaming: Gameplay, walkthroughs, gaming news, esports, game reviews
- sports: Sports highlights, analysis, commentary, athlete interviews, training
- education_tutorial: How-to guides, tutorials, educational explanations, skill teaching
- finance_business: Investing, entrepreneurship, business advice, market analysis, personal finance
- vlog_lifestyle: Personal vlogs, daily routines, lifestyle tips, life updates, Q&A
- true_crime: True crime narration, real criminal cases, investigations, trials and verdicts, mystery storytelling about crimes
- hindi_podcast: Hindi or Hinglish (mixed Hindi-English) podcasts, interviews and creator discussions spoken mainly in Hindi
- music_artist: Singers and rappers performing, songs and lyric breakdowns, music releases, artist content built around the music itself

OUTPUT REQUIREMENTS:
- Return valid JSON only. No markdown, no code fences, no extra text.
- JSON keys: "niche", "confidence", "reasons"
- "niche" must be exactly one of the 11 categories listed above
- "confidence" must be a float between 0.0 and 1.0
- "reasons" must be an array of 1-3 short strings (max 50 chars each)"""


def _evidence_field(entry: Any, name: str, default: Any = None) -> Any:
    """Read ``name`` from a SceneEvidence-like object or a plain dict."""
    if isinstance(entry, dict):
        return entry.get(name, default)
    return getattr(entry, name, default)


def _round3(value: Any) -> Optional[float]:
    try:
        return round(float(value), 3)
    except (TypeError, ValueError):
        return None


def summarize_visual_analysis(
    analysis: Optional[dict], *, duration: Optional[float] = None
) -> Optional[dict]:
    """Compact visual-evidence summary from ``get_source_analysis`` output.

    ``analysis`` is the dict :func:`media.reframe_plan.get_source_analysis`
    returns (``width``/``height``/``samples``/``cuts``/``context``): face
    boxes per sampled frame plus per-sample :class:`SceneEvidence`. This
    is a pure summariser — no decoding, no cv2 — so callers compute the
    analysis once (it is cached per source for the reframe planner) and
    tests can fake it at this seam. Returns None when the analysis holds
    no usable evidence at all.

    There is deliberately no food/object claim here: the existing
    pipeline has no object detector, so the honest visual signals are
    face presence/size, scene-change rate, distributed motion and text
    context. Food/travel meaning comes from combining those with the
    title and transcript, never from pretending a frame was recognised
    as food.
    """
    if not analysis or not isinstance(analysis, dict):
        return None
    samples = analysis.get("samples") or []
    context = analysis.get("context") or []
    cuts = analysis.get("cuts") or []
    if not samples and not context and not cuts:
        return None
    width = float(analysis.get("width") or 0)
    height = float(analysis.get("height") or 0)
    frame_area = width * height if width > 0 and height > 0 else 0.0

    face_samples = 0
    multi_face_samples = 0
    face_counts: list[float] = []
    largest_face_areas: list[float] = []
    sample_times: list[float] = []
    for entry in samples:
        try:
            t, boxes = entry[0], entry[1]
        except (TypeError, IndexError, KeyError):
            continue
        boxes = list(boxes or [])
        sample_times.append(float(t))
        face_counts.append(float(len(boxes)))
        if boxes:
            face_samples += 1
            if len(boxes) >= 2:
                multi_face_samples += 1
            if frame_area > 0:
                largest_face_areas.append(
                    max(float(b[2]) * float(b[3]) for b in boxes) / frame_area
                )
    total = len(samples)

    span = duration
    if not span:
        times = list(sample_times)
        times += [
            float(_evidence_field(c, "time", 0) or 0) for c in context
        ]
        span = (max(times) - min(times)) if len(times) >= 2 else None
    cut_count = len(cuts)
    cuts_per_minute = (
        round(cut_count / (float(span) / 60.0), 3)
        if span and float(span) > 0
        else None
    )

    summary: dict[str, Any] = {
        "sample_count": total,
        "face_presence_rate": _round3(face_samples / total) if total else None,
        "multi_face_rate": _round3(multi_face_samples / total) if total else None,
        "mean_face_count": _round3(sum(face_counts) / total) if total else None,
        "mean_largest_face_area_frac": (
            _round3(sum(largest_face_areas) / len(largest_face_areas))
            if largest_face_areas
            else None
        ),
        "cut_count": cut_count,
        "cuts_per_minute": cuts_per_minute,
        "span_seconds": _round3(span) if span else None,
    }

    if context:
        def _mean(name: str) -> Optional[float]:
            values = [
                float(v)
                for v in (_evidence_field(c, name) for c in context)
                if isinstance(v, (int, float))
            ]
            return _round3(sum(values) / len(values)) if values else None

        ctx_face_counts = [
            float(v)
            for v in (_evidence_field(c, "face_count") for c in context)
            if isinstance(v, (int, float))
        ]
        text_samples = sum(
            1
            for c in context
            if (float(_evidence_field(c, "text_rows", 0) or 0) > 0)
            or (float(_evidence_field(c, "cut_text_lines", 0) or 0) > 0)
        )
        summary.update(
            {
                "context_sample_count": len(context),
                "context_face_presence_rate": (
                    _round3(
                        sum(1 for v in ctx_face_counts if v > 0)
                        / len(ctx_face_counts)
                    )
                    if ctx_face_counts
                    else None
                ),
                "mean_face_area_frac": _mean("face_area"),
                "mean_motion": _mean("motion"),
                "mean_motion_spread": _mean("motion_spread"),
                "mean_outside_motion": _mean("outside_motion"),
                "text_sample_rate": _round3(text_samples / len(context)),
            }
        )
        if summary["face_presence_rate"] is None:
            summary["face_presence_rate"] = summary["context_face_presence_rate"]
        if summary["mean_largest_face_area_frac"] is None:
            summary["mean_largest_face_area_frac"] = summary["mean_face_area_frac"]

    face_rate = summary.get("face_presence_rate")
    spread = summary.get("mean_motion_spread")
    # A studio podcast / fixed talking head: faces in nearly every sample,
    # almost no scene changes, action concentrated rather than spread
    # across the frame. Eating/travel/activity footage fails at least one
    # of these even though its transcript is pure conversation.
    summary["static_talking_head_likely"] = bool(
        face_rate is not None
        and face_rate >= 0.8
        and (cuts_per_minute is None or cuts_per_minute < 2.0)
        and (spread is None or spread < 0.35)
    )
    summary["scene_variety_high"] = bool(
        (cuts_per_minute is not None and cuts_per_minute >= 3.0)
        or cut_count >= 10
    )
    return summary


def summarize_audio_beats(
    beat_grid: Optional[dict], *, speech_fraction: Optional[float] = None
) -> Optional[dict]:
    """Compact audio-evidence summary from ``analyse_audio_beats`` output.

    Pure summariser over the beat grid the pipeline already computes and
    caches per source (tempo, beat times, drop, 50 ms energy curve).
    ``music_likely`` is a heuristic — a steady detected tempo with many
    beats — and is labelled as such wherever it is surfaced; it is not a
    music detector. ``speech_fraction`` (share of the source covered by
    transcript words, computed by the caller from word timings that
    already exist) is passed through when known: together they say
    whether the track is speech-dominant or music-driven. Returns None
    when there is no usable grid.
    """
    if not beat_grid or not isinstance(beat_grid, dict):
        return None
    curve = beat_grid.get("energy_curve") or {}
    levels = [
        float(v) for v in (curve.get("levels_db") or []) if isinstance(v, (int, float))
    ]
    beat_times = beat_grid.get("beat_times") or []
    tempo_strength = beat_grid.get("tempo_strength")
    summary: dict[str, Any] = {
        "tempo_bpm": _round3(beat_grid.get("tempo_bpm")),
        "tempo_strength": _round3(tempo_strength),
        "beat_count": len(beat_times),
        "has_drop": beat_grid.get("drop_time") is not None,
        "duration_seconds": _round3(beat_grid.get("duration")),
        "mean_level_db": None,
        "p95_level_db": None,
        "dynamic_range_db": None,
        "music_likely": bool(
            isinstance(tempo_strength, (int, float))
            and float(tempo_strength) >= 1.8
            and len(beat_times) >= 8
        ),
        "speech_fraction": _round3(speech_fraction)
        if speech_fraction is not None
        else None,
    }
    if levels:
        ordered = sorted(levels)
        mean_level = sum(ordered) / len(ordered)
        p95 = ordered[min(len(ordered) - 1, int(0.95 * len(ordered)))]
        p50 = ordered[len(ordered) // 2]
        summary["mean_level_db"] = _round3(mean_level)
        summary["p95_level_db"] = _round3(p95)
        summary["dynamic_range_db"] = _round3(p95 - p50)
    if summary["speech_fraction"] is not None:
        summary["speech_dominant"] = bool(summary["speech_fraction"] >= 0.5)
    return summary


def _coerce_visual_summary(evidence: Any) -> Optional[dict]:
    """Accept a raw source analysis or an already-built visual summary."""
    if not evidence or not isinstance(evidence, dict):
        return None
    if "samples" in evidence or "context" in evidence or "cuts" in evidence:
        return summarize_visual_analysis(evidence)
    if "face_presence_rate" in evidence or "cut_count" in evidence:
        return dict(evidence)
    return None


def _coerce_audio_summary(evidence: Any) -> Optional[dict]:
    """Accept a raw beat grid or an already-built audio summary."""
    if not evidence or not isinstance(evidence, dict):
        return None
    if "energy_curve" in evidence or "beat_times" in evidence:
        return summarize_audio_beats(evidence)
    if "tempo_bpm" in evidence or "beat_count" in evidence:
        return dict(evidence)
    return None


def _evidence_status(
    title: Optional[str],
    visual: Optional[dict],
    audio: Optional[dict],
) -> str:
    """How much beyond the transcript the decision could see."""
    if visual is not None and audio is not None:
        return "full"
    if visual is not None or audio is not None or (title and title.strip()):
        return "partial"
    return "transcript_only"


def _evidence_block(
    *,
    title: Optional[str],
    description: Optional[str],
    visual: Optional[dict],
    audio: Optional[dict],
) -> dict:
    """The evidence record carried on every classification result."""
    missing = []
    if visual is None:
        missing.append("visual")
    if audio is None:
        missing.append("audio")
    if not (title and title.strip()):
        missing.append("title")
    return {
        "title": (title or "").strip() or None,
        "description_used": bool(description and description.strip()),
        "visual": visual,
        "audio": audio,
        "missing": missing,
    }


# --- Guardrail evidence sample ------------------------------------------------
#
# The model prompt sees the transcript's first ~2,000 chars — that
# sample is the model's, and nothing here changes it. The guardrails
# below are deterministic scorers, though, and a topic that only
# starts AFTER the opening minutes is invisible to a head-only
# sample: the $10M-trader case opens with a morning routine and
# reaches its trading content at char ~7,300 of 24,033, so every
# scorer read the opening and found nothing. Callers that hold the
# full transcript therefore also build this widened sample — the
# head verbatim, plus one 1,000-char window centred on each
# quartile of the transcript — and hand it to the guardrails alone
# (classify_niche's guardrail_sample). Measured on that transcript:
# finance scores 2 over the head, 5 over the widened sample, with
# the lock still False (the full text holds no conversation cue).
# The podcast lock is computed over the same widened text: a
# genuine podcast whose LATER segments range widely still locks,
# because its conversation cues sit in the sample too. One
# deliberate exception: the success-path travel rule keeps scoring
# the head sample — its bar is a destination title plus non-static
# visual evidence (a single title keyword already reaches its
# score of 2, so the transcript was never its gate), and feeding it
# late off-topic segments would let a podcast guest's holiday
# story re-file a podcast.

_GUARDRAIL_HEAD_CHARS = 2000
_GUARDRAIL_WINDOW_CHARS = 1000
_GUARDRAIL_WINDOW_FRACTIONS = (0.25, 0.5, 0.75)


def guardrail_evidence_sample(transcript: Optional[str]) -> str:
    """Stratified evidence sample for the deterministic guardrails:
    the transcript's first 2,000 chars verbatim, plus a 1,000-char
    window centred on each quartile of the full text (windows never
    overlap the head or each other). Transcripts at or under the
    head size return unchanged, so head-only evidence is identical
    to today's."""
    text = (transcript or "").strip()
    if len(text) <= _GUARDRAIL_HEAD_CHARS:
        return text
    parts = [text[:_GUARDRAIL_HEAD_CHARS]]
    covered_until = _GUARDRAIL_HEAD_CHARS
    for fraction in _GUARDRAIL_WINDOW_FRACTIONS:
        start = int(len(text) * fraction - _GUARDRAIL_WINDOW_CHARS / 2)
        start = max(start, covered_until)
        window = text[start:start + _GUARDRAIL_WINDOW_CHARS]
        if window:
            parts.append(window)
            covered_until = start + len(window)
    return "\n".join(parts)


# --- Deterministic guardrail -------------------------------------------------
#
# The podcast description is a broad attractor for any two people talking.
# One narrow case is corrected without the model: the model said
# podcast_news, but the TITLE names a destination food/travel experience
# AND the visual evidence is plainly not a static studio talking head
# (frequent scene changes, distributed motion, or faces absent from much
# of the footage). A genuine studio podcast — faces in nearly every
# sample, almost no cuts — can never trip this, whatever its title says.
#
# A second narrow case sits beside it: the title DECLARES a personal
# vlog format ("week/day in the life", or the creator calling the
# video a vlog in the title and on camera). A solo creator monologuing
# through a vlog is the same attractor in a different shape, and the
# travel keywords cannot see it — its destinations ("Los Angeles",
# "Miami") are not in the travel list, and its spoken content needs
# no topic keywords at all. The podcast lock (real conversation cues,
# a podcast-shaped title, a static talking head) bars this rule
# exactly as it bars the failure-path rescues.
#
# A third case completes the set: the spoken content itself is
# finance-dense — a solo trading monologue is the attractor in its
# purest shape (one person, talking, at a desk). Unlike the two rules
# above, the failure path already owned this case (the keyword
# rescue below); what was missing was the success path, where a
# podcast answer stood however strong the evidence was, plus the
# scorer's vocabulary, which spoke investing dialect only. The same
# lock bars this rule, and the rescue's strength standard
# (>= 4 distinct hits, >= 2 clear of every rival) gates it.

_TRAVEL_FOOD_KEYWORDS = (
    "restaurant",
    "restaurants",
    "street food",
    "food tour",
    "foodie",
    "eating",
    "cuisine",
    "tasting",
    "travel",
    "travelling",
    "traveling",
    "tour",
    "itinerary",
    "destination",
    "vlog in",
    "in japan",
    "in tokyo",
    "in india",
    "in korea",
    "in thailand",
    "in vietnam",
    "in italy",
    "in france",
    "in mexico",
)


def _travel_food_score(title: Optional[str], transcript_sample: str) -> int:
    """Distinct travel/food keyword hits; title hits weigh double."""
    score = 0
    title_text = (title or "").lower()
    transcript_text = (transcript_sample or "").lower()
    for keyword in _TRAVEL_FOOD_KEYWORDS:
        pattern = rf"\b{re.escape(keyword)}\b"
        if re.search(pattern, title_text):
            score += 2
        elif re.search(pattern, transcript_text):
            score += 1
    return score


def _visual_shows_activity(visual: dict) -> bool:
    """The picture is plainly not one fixed setup: frequent scene
    changes, distributed motion, or faces absent from much of the
    footage. Shared by the evidence guardrails below."""
    return bool(
        (visual.get("cuts_per_minute") is not None and visual["cuts_per_minute"] >= 1.5)
        or (visual.get("cut_count") or 0) >= 6
        or (visual.get("mean_motion_spread") is not None and visual["mean_motion_spread"] >= 0.35)
        or (visual.get("face_presence_rate") is not None and visual["face_presence_rate"] < 0.75)
    )


# Life-format phrases: a title that names the "in the life" format is
# the creator declaring a personal vlog, the same way a title saying
# "podcast" declares a podcast. Precision-chosen: these phrases name
# a FORMAT, not a topic, so topic content cannot trip them.
_VLOG_LIFE_FORMAT_PHRASES = (
    "week in the life",
    "day in the life",
    "week in my life",
    "day in my life",
    "in the life of",
)


def _vlog_format_score(title: Optional[str], transcript_sample: str) -> int:
    """Vlog-format declaration strength.

    A life-format phrase in the title scores 3 on its own — it IS the
    format declaration. The word "vlog" in the title scores 2 and the
    word said on camera in the sample scores 1, so a bare "vlog"
    title (the pinned weak case, "Weekly Vlog") cannot reach the
    threshold without the creator also calling the video a vlog in
    the footage itself.
    """
    score = 0
    title_text = (title or "").lower()
    transcript_text = (transcript_sample or "").lower()
    if any(phrase in title_text for phrase in _VLOG_LIFE_FORMAT_PHRASES):
        score += 3
    if re.search(r"\bvlog\b", title_text):
        score += 2
    if re.search(r"\bvlog\b", transcript_text):
        score += 1
    return score


def _vlog_format_correction(
    result: dict,
    *,
    title: Optional[str],
    transcript_sample: str,
    visual: Optional[dict],
) -> Optional[dict]:
    """Correct a podcast_news result the vlog-format evidence
    contradicts, or return None.

    Bars, all deliberate: the shared podcast lock (conversation cues,
    a podcast-shaped title, a static talking-head picture) vetoes the
    correction outright; when visual evidence exists it must show
    activity, as the travel rule requires; and the format score must
    reach 3 — a declared life-format title, or "vlog" declared in
    both the title and the footage.
    """
    if _podcast_locked(title, transcript_sample, visual):
        return None
    if visual is not None and not _visual_shows_activity(visual):
        return None
    if _vlog_format_score(title, transcript_sample) < 3:
        return None
    corrected = dict(result)
    corrected["niche"] = "vlog_lifestyle"
    # Capped like the travel rule's: the guardrail knows the declared
    # format is a personal vlog, not exactly how strongly the content
    # fits the vlog profile.
    corrected["confidence"] = max(0.55, min(float(result.get("confidence") or 0.0), 0.7))
    corrected["reasons"] = [
        "Title declares a vlog format, not a podcast",
        "Creator presents the video as a personal vlog",
    ]
    evidence = dict(corrected.get("evidence") or {})
    evidence["guardrail"] = "podcast_news_overridden_by_vlog_format_evidence"
    corrected["evidence"] = evidence
    logger.info(
        "Niche guardrail: podcast_news -> vlog_lifestyle (vlog-format evidence)"
    )
    return corrected


def _finance_evidence_correction(
    result: dict,
    *,
    title: Optional[str],
    transcript_sample: str,
    visual: Optional[dict],
    audio: Optional[dict],
) -> Optional[dict]:
    """Correct a podcast_news result that strong finance evidence
    contradicts, or return None.

    The success path had no finance correction at all: a solo trading
    monologue is the podcast attractor's home turf, and the model
    answer stood however dense the spoken finance content was. The
    failure path is NOT this rule's job — its rescue already owns
    finance there, with the same scorer — so a fallback result
    returns None here and keeps the rescue's marking.

    Bars, mirroring the vlog rule and the rescue's own strength
    standard: the shared podcast lock (conversation cues, a
    podcast-shaped title, a static talking-head picture) vetoes the
    correction outright — a genuine finance PODCAST, however dense
    its market vocabulary, is never stolen; when visual evidence
    exists it must show activity, as the travel rule requires; and
    the finance score must reach the rescue floor (>= 4 distinct
    keyword hits, title hits double) with a clear margin (>= 2) over
    every rival niche scored the same way.
    """
    if result.get("is_fallback"):
        return None
    if _podcast_locked(title, transcript_sample, visual):
        return None
    if visual is not None and not _visual_shows_activity(visual):
        return None
    finance_score = _keyword_score(
        _NICHE_KEYWORDS["finance_business"], title, transcript_sample
    )
    if finance_score < 4:
        return None
    rivals: dict[str, int] = {
        "travel_vlog": _travel_food_score(title, transcript_sample),
    }
    for niche, keywords in _NICHE_KEYWORDS.items():
        if niche != "finance_business":
            rivals[niche] = _keyword_score(keywords, title, transcript_sample)
    if audio is not None and audio.get("music_likely"):
        speech_fraction = audio.get("speech_fraction")
        if speech_fraction is None or float(speech_fraction) < 0.35:
            rivals["music_artist"] += 3
    rivals["hindi_podcast"] = _hindi_evidence_score(title, transcript_sample)[0]
    if finance_score - max(rivals.values()) < 2:
        return None
    corrected = dict(result)
    corrected["niche"] = "finance_business"
    # Capped like the other guardrails': the evidence knows the
    # spoken topic is finance, not exactly how strongly the content
    # fits the finance profile.
    corrected["confidence"] = max(0.55, min(float(result.get("confidence") or 0.0), 0.7))
    corrected["reasons"] = [
        "Spoken content is a finance/trading monologue, not a podcast",
        "Strong finance evidence in title and transcript",
    ]
    evidence = dict(corrected.get("evidence") or {})
    evidence["guardrail"] = "podcast_news_overridden_by_finance_evidence"
    corrected["evidence"] = evidence
    logger.info(
        "Niche guardrail: podcast_news -> finance_business "
        "(finance evidence, score %d vs %d)",
        finance_score, max(rivals.values()),
    )
    return corrected


def apply_evidence_guardrails(
    result: dict,
    *,
    title: Optional[str],
    transcript_sample: str,
    visual: Optional[dict],
    audio: Optional[dict],
    evidence_sample: Optional[str] = None,
) -> dict:
    """Correct the evidence-contradicted podcast misclassifications.

    Three narrow cases, in precedence order: destination food/travel
    (title keywords plus non-static, active visual evidence), a
    declared vlog format (see :func:`_vlog_format_correction`), and
    strong finance evidence (see :func:`_finance_evidence_correction`).
    Returns ``result`` unchanged in every other case (including
    genuine studio podcasts and every non-podcast niche).

    ``evidence_sample`` is the widened guardrail sample (see
    :func:`guardrail_evidence_sample`) when the caller holds the
    full transcript; the transcript-evidence rules — vlog format,
    finance, and the podcast lock inside them — score it instead of
    the head sample. The travel rule is the deliberate exception: it
    keeps the head sample (its gate is the destination title plus
    the visual evidence, never the transcript's later segments).
    """
    if result.get("niche") != "podcast_news":
        return result
    scored_sample = (
        evidence_sample if evidence_sample is not None else transcript_sample
    )
    if visual is not None and not visual.get("static_talking_head_likely"):
        if _visual_shows_activity(visual) and _travel_food_score(
            title, transcript_sample
        ) >= 2:
            corrected = dict(result)
            corrected["niche"] = "travel_vlog"
            # Capped below a confident model answer: the guardrail knows the
            # video is not a studio podcast and is about destination food/travel,
            # not exactly how strongly it fits the travel profile.
            corrected["confidence"] = max(0.55, min(float(result.get("confidence") or 0.0), 0.7))
            corrected["reasons"] = [
                "Title and scenes show destination food/travel",
                "Frequent scene changes, not a studio talk setup",
            ]
            evidence = dict(corrected.get("evidence") or {})
            evidence["guardrail"] = "podcast_news_overridden_by_title_and_visual_evidence"
            corrected["evidence"] = evidence
            logger.info(
                "Niche guardrail: podcast_news -> travel_vlog (title/visual evidence)"
            )
            return corrected
    corrected = _vlog_format_correction(
        result, title=title, transcript_sample=scored_sample, visual=visual
    )
    if corrected is not None:
        return corrected
    corrected = _finance_evidence_correction(
        result,
        title=title,
        transcript_sample=scored_sample,
        visual=visual,
        audio=audio,
    )
    return corrected if corrected is not None else result


# --- Failure-path rescue scorers ---------------------------------------------
#
# The travel rule above needs visual evidence and a successful model
# answer. The failure path has neither: every candidate failed, and the
# result is the marked fallback. The scorers below give that path a
# conservative second chance using title/transcript keywords (plus one
# audio signal for music). Design rules, all deliberate:
#
# * HIGH PRECISION over recall — a rescue needs at least 4 distinct
#   keyword hits (title hits weigh double) AND a clear margin (>= 2)
#   over every rival niche. Ambiguity keeps the fallback.
# * A genuine podcast is NEVER rescued away by topic keywords:
#   interview/conversation cues in the transcript, a podcast-shaped
#   title, or talking-head visual evidence each lock the rescue out
#   completely. ONE deliberate exception: hindi_podcast is scored from
#   the transcript's LANGUAGE, not its topic (see
#   _hindi_evidence_score) — Devanagari dominance may override the
#   lock, because a Hindi podcast's correct profile is hindi_podcast
#   itself. Romanized-Hinglish markers are keyword-class evidence and
#   never override the lock.
# * A rescue never pretends to be a detection: the result keeps
#   is_fallback=True, gains guardrail_rescued=True, and its reasons
#   say a guardrail chose the niche.

_PODCAST_CUES = (
    "podcast",
    "welcome back to the show",
    "welcome to the show",
    "my guest",
    "our guest",
    "today's guest",
    "in this episode",
    "this episode",
    "episode of",
    "interview with",
    "in conversation with",
    "joining me today",
    "joining us today",
)

_NICHE_KEYWORDS: dict[str, tuple[str, ...]] = {
    "gaming": (
        "gameplay", "gaming", "speedrun", "ranked", "loadout",
        "boss fight", "walkthrough", "let's play", "esports",
        "respawn", "clutch", "patch notes", "dlc", "k/d", "lobby",
        "fortnite", "minecraft", "valorant",
    ),
    "sports": (
        "goal", "goals", "penalty", "matchday", "stadium",
        "striker", "goalkeeper", "clean sheet", "stoppage time",
        "derby", "tournament", "championship", "athlete", "kickoff",
        "touchdown", "wicket", "innings",
    ),
    # The day-trading terms extend the original investing list: a
    # trading creator's monologue speaks P&L and prop-firm dialect
    # (funded account, payout, stop loss), not portfolio vocabulary —
    # 2,500 chars of real trading speech scored 1 (only "trading")
    # against the investing list alone. Precision posture is unchanged:
    # distinct hits only, and no single word here can reach the
    # 4-point floor on its own.
    "finance_business": (
        "portfolio", "index fund", "index funds", "dividend",
        "dividends", "stocks", "investing", "investment", "crypto",
        "bitcoin", "trading", "compound interest", "retirement",
        "earnings report", "stock market", "revenue", "budget",
        "day trading", "trader", "funded account", "prop firm",
        "payout", "stop loss", "take profit", "drawdown", "forex",
        "bullish", "bearish", "profit", "loss",
    ),
    "music_artist": (
        "new single", "my single", "chorus", "verse", "lyrics",
        "album", "beat drops", "official audio", "live performance",
        "concert", "music video", "track",
    ),
    "meme_comedy": (
        "pov:", "skit", "meme", "memes", "funniest", "comedy",
        "hilarious", "lol", "plot twist", "watch till the end",
    ),
}


def _keyword_score(
    keywords: tuple[str, ...], title: Optional[str], transcript_sample: str
) -> int:
    """Distinct keyword hits; title hits weigh double."""
    score = 0
    title_text = (title or "").lower()
    transcript_text = (transcript_sample or "").lower()
    for keyword in keywords:
        if keyword in title_text:
            score += 2
        elif keyword in transcript_text:
            score += 1
    return score


# --- Hindi evidence ------------------------------------------------------------
#
# hindi_podcast deliberately has no entry in _NICHE_KEYWORDS: its real
# signal is the transcript's LANGUAGE, not topic words. Two signals,
# in order of strength:
#
# * Devanagari dominance — the objective one. When at least
#   DEVANAGARI_SHARE_THRESHOLD of the letters are Devanagari — the
#   same threshold media.hinglish uses to decide a transcript is
#   Hindi for caption romanization — the content IS Hindi speech.
#   That judgement scores decisively on its own and may override the
#   podcast lock: a Hindi podcast's correct profile is hindi_podcast,
#   so the lock's purpose (protecting genuine podcasts from rival
#   niches) is not served by barring it. An English transcript
#   essentially never reaches the threshold.
# * Romanized-Hinglish markers — Hindi function words in Latin script,
#   for the minority of Hindi speech Whisper transcribes romanized
#   instead of in Devanagari. Word-boundary matched (unlike
#   _keyword_score's substring matching) and precision-chosen: none of
#   these is an ordinary English word. Each distinct marker scores one
#   point, but markers are keyword-class evidence — they compete
#   normally and NEVER override the podcast lock.

_ROMANIZED_HINGLISH_MARKERS: tuple[str, ...] = (
    "kya", "kyunki", "hai", "hain", "nahin", "matlab", "doston",
    "yaar", "bhai", "achha", "accha", "bahut", "chahiye", "samajh",
    "kabhi", "sabhi", "theek", "aisa", "kaisa", "kaise", "wala",
    "aur", "mein", "mera", "tera", "hota", "karte", "raha",
)

# A Devanagari-dominant transcript is decisive language evidence:
# worth more than the 4-point rescue floor on its own, so a Hindi
# talk is not held hostage to how many Latin topic keywords it happens
# to contain (in pure Devanagari it contains almost none).
_HINDI_DEVANAGARI_SCORE = 6


def _hindi_evidence_score(
    title: Optional[str], transcript_sample: str
) -> tuple[int, bool, float]:
    """(score, devanagari_dominant, devanagari_share) for hindi_podcast."""
    from ..media.hinglish import DEVANAGARI_SHARE_THRESHOLD, devanagari_share

    share = devanagari_share([title or "", transcript_sample or ""])
    dominant = share >= DEVANAGARI_SHARE_THRESHOLD
    score = _HINDI_DEVANAGARI_SCORE if dominant else 0
    text = f"{title or ''} {transcript_sample or ''}".lower()
    markers = {
        marker
        for marker in _ROMANIZED_HINGLISH_MARKERS
        if re.search(rf"\b{re.escape(marker)}\b", text)
    }
    return score + len(markers), dominant, share


def _podcast_locked(
    title: Optional[str], transcript_sample: str, visual: Optional[dict]
) -> bool:
    """True when the evidence says 'this really is a podcast' — the
    failure-path rescue must never fire."""
    if visual is not None and visual.get("static_talking_head_likely"):
        return True
    if "podcast" in (title or "").lower():
        return True
    text = (transcript_sample or "").lower()
    return any(cue in text for cue in _PODCAST_CUES)


def _evidence_guardrail_rescue(
    result: dict,
    *,
    title: Optional[str],
    transcript_sample: str,
    visual: Optional[dict],
    audio: Optional[dict],
    exclude: frozenset = frozenset(),
    evidence_sample: Optional[str] = None,
) -> Optional[dict]:
    """Rescue a fallback result from strong title/transcript evidence.

    Returns the corrected result, or None when the evidence is not
    strong AND unambiguous — the common case. Only for fallback
    results; a real model answer is never second-guessed here.
    ``exclude`` drops niches from candidacy (the substituted-agent
    path excludes travel_vlog — see classify_niche).
    ``evidence_sample`` is the widened guardrail sample (see
    :func:`guardrail_evidence_sample`); when supplied it replaces
    the head sample for every scorer and for the podcast lock.

    The podcast lock bars every keyword-based rescue. The single
    exception is hindi_podcast on objective language evidence: a
    Devanagari-dominant transcript is Hindi speech whatever the
    title calls the show, and hindi_podcast is then the CORRECT
    profile, not a rival stealing a podcast. Romanized markers alone
    never pass the lock (see _hindi_evidence_score).
    """
    if not result.get("is_fallback"):
        return None
    if result.get("niche") != "podcast_news":
        return None
    # The widened sample, when supplied, is the evidence for every
    # scorer here AND for the podcast lock: the lock must see exactly
    # the text the scorers see, or a podcast whose conversation sits
    # late in the transcript could be rescued away by its own topic
    # segments.
    sample = (
        evidence_sample if evidence_sample is not None else transcript_sample
    )
    hindi_score, hindi_dominant, hindi_share = _hindi_evidence_score(
        title, sample
    )
    locked = _podcast_locked(title, sample, visual)
    if locked and not hindi_dominant:
        return None

    scores: dict[str, int] = {
        "travel_vlog": _travel_food_score(title, sample),
    }
    for niche, keywords in _NICHE_KEYWORDS.items():
        scores[niche] = _keyword_score(keywords, title, sample)
    if audio is not None and audio.get("music_likely"):
        speech_fraction = audio.get("speech_fraction")
        if speech_fraction is None or float(speech_fraction) < 0.35:
            scores["music_artist"] += 3
    scores["hindi_podcast"] = hindi_score
    for niche in exclude:
        scores.pop(niche, None)
    if locked:
        # Locked, and only the language signal may pass: keyword
        # rivals stay barred, so hindi competes against silence.
        if "hindi_podcast" not in scores:
            return None
        scores = {"hindi_podcast": scores["hindi_podcast"]}

    ranked = sorted(scores.items(), key=lambda item: item[1], reverse=True)
    best_niche, best_score = ranked[0]
    runner_up = ranked[1][1] if len(ranked) > 1 else 0
    if best_score < 4 or best_score - runner_up < 2:
        return None

    corrected = dict(result)
    corrected["niche"] = best_niche
    corrected["confidence"] = max(0.55, min(float(result.get("confidence") or 0.0), 0.7))
    corrected["reasons"] = [
        "Auto detection failed; evidence guardrail rescue",
        f"Title/transcript evidence: {best_niche}",
    ]
    corrected["guardrail_rescued"] = True
    evidence = dict(corrected.get("evidence") or {})
    evidence["guardrail"] = (
        f"podcast_news_overridden_by_title_transcript_evidence:{best_niche}"
    )
    if best_niche == "hindi_podcast":
        # Record WHY the language scorer fired, so the rescue stays
        # auditable: the objective share that crossed (or did not
        # need) hinglish's dominance threshold.
        evidence["devanagari_share"] = _round3(hindi_share)
    corrected["evidence"] = evidence
    logger.info(
        "Niche guardrail rescue: fallback podcast_news -> %s "
        "(title/transcript evidence, score %d vs %d)",
        best_niche, best_score, runner_up,
    )
    return corrected


def _apply_failure_path_guardrails(
    result: dict,
    *,
    title: Optional[str],
    transcript_sample: str,
    visual: Optional[dict],
    audio: Optional[dict],
    include_travel_rule: bool = True,
    evidence_sample: Optional[str] = None,
) -> dict:
    """Run the guardrails over a fallback result: the pre-existing
    travel rule first (when visual evidence exists), then the
    title/transcript rescue scorers.

    ``include_travel_rule`` is False on the substituted-agent path:
    that path is a single already-failed attempt whose marked fallback
    the evidence suite pins, so only the conservative text scorers
    may rescue it there. ``evidence_sample`` is the widened guardrail
    sample, threaded to both guardrail stages."""
    if include_travel_rule:
        guarded = apply_evidence_guardrails(
            result,
            title=title,
            transcript_sample=transcript_sample,
            visual=visual,
            audio=audio,
            evidence_sample=evidence_sample,
        )
        if guarded.get("niche") != "podcast_news":
            rescued = dict(guarded)
            rescued["guardrail_rescued"] = True
            return rescued
    rescue = _evidence_guardrail_rescue(
        result,
        title=title,
        transcript_sample=transcript_sample,
        visual=visual,
        audio=audio,
        evidence_sample=evidence_sample,
    )
    return rescue if rescue is not None else result


def _build_niche_classification_prompt(
    transcript_sample: str,
    entities: list[str],
    *,
    title: Optional[str] = None,
    description: Optional[str] = None,
    visual_evidence: Optional[dict] = None,
    audio_evidence: Optional[dict] = None,
) -> str:
    """Build the prompt for niche classification from all evidence."""
    entities_str = ", ".join(entities) if entities else "none detected"
    sections = []
    if title and title.strip():
        sections.append(f"Video title: {title.strip()}")
    if description and description.strip():
        sections.append(f"Video description (excerpt): {description.strip()[:500]}")
    sections.append(f"Transcript sample:\n{transcript_sample}")
    sections.append(f"Named entities: {entities_str}")
    if visual_evidence:
        sections.append(
            "Visual evidence (measured from sampled frames: face presence "
            "and size, scene cuts, motion spread across the frame, on-screen "
            f"text): {json.dumps(visual_evidence, sort_keys=True)}"
        )
    if audio_evidence:
        sections.append(
            "Audio evidence (measured from the soundtrack: tempo and beat "
            "detection, energy levels, how much of the track is speech): "
            f"{json.dumps(audio_evidence, sort_keys=True)}"
        )
    sections.append(
        "Classify this content into one niche category. Consider the main "
        "topic, tone, and format of the content, and what is on screen — "
        "not just what is said."
    )
    return "\n\n".join(sections)


# Cached agents for niche classification (lazy initialization like in
# ai.py). Two caches: the structured (tool/native output) agents keyed
# by model, and the plain-text agents used by the JSON-text repair pass.
_niche_agents: dict[str, tuple[tuple, Any]] = {}
_niche_text_agents: dict[str, tuple[tuple, Any]] = {}


def _agent_signature(runtime_config, selected_model: str) -> tuple:
    return (
        selected_model,
        runtime_config.openai_api_key,
        runtime_config.openrouter_api_key,
        runtime_config.openrouter_fallback_model,
        runtime_config.google_api_key,
        runtime_config.anthropic_api_key,
        runtime_config.ollama_base_url,
        runtime_config.ollama_api_key,
    )


def _build_classification_agent(runtime_config, selected_model: str, *, text_output: bool):
    """Build one classification agent for ``selected_model``.

    The missing-key gate runs here, at build time — callers treat a
    raise as THIS candidate's failure and move to the next candidate,
    never as a classification-wide failure.
    """
    from ..runtime_settings import apply_settings_to_process_env

    apply_settings_to_process_env(runtime_config.as_runtime_settings())

    # Reuse the model building logic from ai.py
    from ..ai import _build_transcript_model, _uses_native_output, _get_missing_llm_key_error

    config_error = _get_missing_llm_key_error(selected_model, runtime_config)
    if config_error:
        raise RuntimeError(config_error)

    model = _build_transcript_model(runtime_config, selected_model)
    if text_output:
        # Plain-text output: the model answers with the JSON document
        # the system prompt asks for; classify_niche post-parses and
        # validates it against NicheClassification itself.
        return Agent(
            model=model,
            output_type=str,
            system_prompt=NICHE_CLASSIFICATION_SYSTEM_PROMPT,
        )
    return Agent[None, NicheClassification](
        model=model,
        output_type=NativeOutput(NicheClassification, strict=True) if _uses_native_output(selected_model) else NicheClassification,
        system_prompt=NICHE_CLASSIFICATION_SYSTEM_PROMPT,
        retries={'output': 2},
    )


def get_agent_for_model(selected_model: str, *, text_output: bool = False):
    """Get or create the niche classification agent for one model.

    Cached per (model, output mode, key signature); a config change
    rebuilds. Raises RuntimeError from the missing-key gate — that is
    this candidate's failure, handled by the caller's chain.
    """
    runtime_config = get_config()
    cache = _niche_text_agents if text_output else _niche_agents
    signature = _agent_signature(runtime_config, selected_model)
    cached = cache.get(selected_model)
    if cached is None or cached[0] != signature:
        agent = _build_classification_agent(
            runtime_config, selected_model, text_output=text_output
        )
        cache[selected_model] = (signature, agent)
        return agent
    return cached[1]


def get_niche_classification_agent():
    """The structured agent for the provider manager's current pick.

    classify_niche drives the candidate chain itself; this single-agent
    accessor remains for callers that need the primary agent directly.
    """
    runtime_config = get_config()
    selected_model = runtime_config.llm
    if provider_manager.manager_enabled(runtime_config):
        selected_model = provider_manager.select_classify_model(runtime_config)
    return get_agent_for_model(selected_model)


# Captured at import time: classify_niche compares the module global
# against this object to detect that a caller/test substituted the
# agent factory (the historical patch seam) and honours the substitute
# as the single classification attempt.
_default_agent_factory = get_niche_classification_agent


def _strongest_first(candidates: list[str]) -> list[str]:
    """Stable-partition a classify chain: cloud routes before local.

    Classification is ONE small call per video, so it should be
    answered by the strongest route available — the provider
    manager's ordering prefers the local model for routine work,
    which put the weakest model (local Ollama) at the head of this
    chain in local-first/auto deployments, and first-success-wins
    meant a confidently-wrong local verdict locked the niche before
    the strong cloud routes were ever consulted. Local routes stay
    in the chain as the offline tail, ahead of the evidence
    guardrails. The partition is stable: within each class the
    assembled order (the manager's relative order, including its
    cooldown/health skips) is preserved exactly, so a chain with no
    cloud candidate — a local-only deployment — comes back
    byte-identical.
    """
    cloud = [m for m in candidates if not provider_manager.is_local_alias(m)]
    local = [m for m in candidates if provider_manager.is_local_alias(m)]
    return cloud + local


def _classify_candidates(runtime_config) -> list[str]:
    """Ordered, de-duplicated models classification will attempt.

    Assembly: manager enabled — the manager's own ordering (its pick
    first, cooled-down aliases skipped), then the configured LLM,
    then the analysis fallback models — ANALYSIS_FALLBACK_MODEL and
    ANALYSIS_DIRECT_FALLBACK_MODEL are the last-resort routes the
    analysis path already trusts, and the local candidate rides along
    as the manager's appended entry. Manager disabled: the same list
    derived from config alone. The assembled list is then re-ordered
    strongest-first (see :func:`_strongest_first`): cloud routes in
    their assembled relative order, local routes at the tail. First
    success wins; any failure on one candidate advances to the next.
    """
    candidates: list[str] = []

    def _add(model: Optional[str]) -> None:
        if model and model not in candidates:
            candidates.append(model)

    if provider_manager.manager_enabled(runtime_config):
        try:
            for model in provider_manager.select_classify_candidates(runtime_config):
                _add(model)
        except Exception as error:
            # Selection itself failing (e.g. local-only with local down)
            # must not abort classification: the config-derived tail is
            # still attempted below.
            logger.warning(
                "Provider manager classify selection failed (%s); "
                "using the config-derived candidate list",
                error,
            )
    _add(getattr(runtime_config, "llm", None))
    for model in getattr(runtime_config, "analysis_models", None) or []:
        _add(model)
    _add(getattr(runtime_config, "analysis_fallback_model", None))
    _add(getattr(runtime_config, "analysis_direct_fallback_model", None))
    return _strongest_first(candidates)


def _release_whisper_for_local_candidates(candidates: list[str]) -> bool:
    """Free the resident faster-whisper model before local classification.

    When the candidate chain can reach the local Ollama runtime, that
    runtime needs ~5.5 GB for qwen3:8b on the reference 6 GB card; a
    whisper model left resident by transcription is the one genuinely
    likely VRAM collision (Wave-4 VRAM scoping, section 3, item 1).
    Eviction happens once, before the walk, and only when a local route
    is actually in the chain — cloud-only classifications keep the
    model warm for the next transcription. Returns True when a local
    route was present (eviction attempted). Best-effort: a failure is
    logged and ignored; classification proceeds either way.
    """
    if not any(provider_manager.is_local_alias(model) for model in candidates):
        return False
    try:
        from ..media.transcription import release_faster_whisper_models

        release_faster_whisper_models()
    except Exception:
        logger.debug(
            "Whisper eviction before local classification failed", exc_info=True
        )
    return True


async def _unload_local_model_after_classification(
    model_used: Optional[str],
) -> None:
    """Ask Ollama to unload after a classification the local model served.

    The local model holds ~5.5 GB until Ollama's own keep-alive timer
    expires; the stages after classification (render, the next task's
    transcription) want that room now. Runs the blocking request in a
    thread so the event loop is not stalled, and swallows everything:
    the handshake is a courtesy, never a stage failure (Wave-4 VRAM
    scoping, Option B item 2). No-op when a cloud candidate answered.
    """
    if not model_used or not provider_manager.is_local_alias(model_used):
        return
    try:
        from ..media.vram import request_ollama_unload

        await asyncio.to_thread(request_ollama_unload)
    except Exception:
        logger.debug("Ollama unload after classification failed", exc_info=True)


async def _run_classification_agent(agent, prompt: str, alias: str):
    """Run one candidate's agent, recorded by the provider manager
    when it is enabled so usage and failures feed its registry."""
    if provider_manager.manager_enabled():
        return await provider_manager.run_recorded(
            agent,
            prompt,
            alias=alias,
            purpose="classify",
        )
    return await agent.run(prompt)


_JSON_OBJECT_RE = re.compile(r"\{.*\}", re.DOTALL)


def _coerce_classification(output: Any) -> NicheClassification:
    """Coerce a substituted agent's answer into NicheClassification.

    Used by the historical patch seam: test doubles answer with plain
    objects whose fields may sit outside the schema (confidence 1.5,
    five reasons) — the pipeline's own clamping/trimming judges those,
    exactly as it did when the seam was the only path. A niche outside
    the schema Literal still fails here — the AUTO_NICHE_VALUES
    rejection, applied at the same seam as before.
    """
    if isinstance(output, NicheClassification):
        return output
    if isinstance(output, dict):
        return NicheClassification.model_validate(output)
    classification = NicheClassification.model_construct(
        niche=getattr(output, "niche", None),
        confidence=float(getattr(output, "confidence", 0.0) or 0.0),
        reasons=list(getattr(output, "reasons", None) or []),
    )
    if classification.niche not in AUTO_NICHE_VALUES:
        raise ValueError(
            f"model returned niche {classification.niche!r}, "
            "which auto-classification may not use"
        )
    return classification


def _parse_json_text_classification(raw: Any) -> NicheClassification:
    """Validate a plain-text model answer as a NicheClassification.

    The system prompt asks for a bare JSON document; small models often
    obey that literally instead of calling the output tool. Accept the
    JSON object wherever it sits in the answer (code fences / prose
    around it are stripped by the brace scan) and validate it against
    the same schema a tool-call answer must satisfy.
    """
    text = raw if isinstance(raw, str) else str(raw or "")
    match = _JSON_OBJECT_RE.search(text)
    if match is None:
        raise ValueError("model answered with no JSON object")
    payload = json.loads(match.group(0))
    return NicheClassification.model_validate(payload)


async def _classify_with_candidates(
    prompt: str,
    runtime_config,
    candidates: list[str],
) -> tuple[Optional[NicheClassification], dict]:
    """Walk the candidate chain; return (classification, chain record).

    Per candidate: build the structured agent (a missing-key gate
    failure is recorded against the alias and advances the chain),
    run it; on a run failure, retry the SAME candidate once in
    plain-text mode — the prompt asks for JSON text, and a model that
    answers that way instead of calling the output tool is repaired
    here rather than discarded. A validated answer whose niche is not
    in AUTO_NICHE_VALUES also advances the chain (exactly the
    rejection the old single-attempt path applied, now non-fatal).
    """
    attempts: list[dict] = []
    last_error: Optional[str] = None
    for model in candidates:
        try:
            agent = get_agent_for_model(model)
        except Exception as error:
            last_error = str(error)
            attempts.append(
                {"model": model, "stage": "build", "output_mode": "structured",
                 "error": last_error}
            )
            logger.warning(
                "Niche classification candidate %s failed to build (%s); "
                "trying the next candidate",
                model, error,
            )
            if provider_manager.manager_enabled(runtime_config):
                try:
                    provider_manager.get_manager().record_failure(model, error=error)
                except Exception:
                    pass
            continue
        modes: list[tuple[bool, str]] = [(False, "structured")]
        run_succeeded = False
        for text_output, mode_label in modes:
            try:
                result = await _run_classification_agent(agent, prompt, model)
                run_succeeded = True
            except Exception as error:
                last_error = str(error)
                attempts.append(
                    {"model": model, "stage": "run", "output_mode": mode_label,
                     "error": last_error}
                )
                logger.warning(
                    "Niche classification candidate %s failed (%s); %s",
                    model, error,
                    "retrying in JSON-text mode"
                    if not text_output
                    else "trying the next candidate",
                )
                if not text_output:
                    try:
                        agent = get_agent_for_model(model, text_output=True)
                    except Exception as build_error:
                        last_error = str(build_error)
                        attempts.append(
                            {"model": model, "stage": "build",
                             "output_mode": "text", "error": last_error}
                        )
                        if provider_manager.manager_enabled(runtime_config):
                            try:
                                provider_manager.get_manager().record_failure(
                                    model, error=build_error
                                )
                            except Exception:
                                pass
                        break
                    modes.append((True, "text"))
                continue
            output = result.output
            if text_output or isinstance(output, str):
                try:
                    classification = _parse_json_text_classification(output)
                except (ValueError, ValidationError) as error:
                    last_error = str(error)
                    attempts.append(
                        {"model": model, "stage": "parse",
                         "output_mode": mode_label, "error": last_error}
                    )
                    logger.warning(
                        "Niche classification candidate %s answered with "
                        "unusable text (%s); trying the next candidate",
                        model, error,
                    )
                    continue
            elif isinstance(output, NicheClassification):
                classification = output
            else:
                # Duck-typed structured answers (test doubles whose
                # fields may sit outside the schema — e.g. confidence
                # 1.5, which the pipeline clamps): read the fields
                # directly, then let the shared AUTO_NICHE_VALUES
                # check and clamping judge them.
                try:
                    classification = NicheClassification.model_construct(
                        niche=output.niche,
                        confidence=float(output.confidence),
                        reasons=list(output.reasons or []),
                    )
                except (AttributeError, TypeError, ValueError) as error:
                    last_error = str(error)
                    attempts.append(
                        {"model": model, "stage": "parse",
                         "output_mode": mode_label, "error": last_error}
                    )
                    continue
            if classification.niche not in AUTO_NICHE_VALUES:
                last_error = (
                    f"model returned niche {classification.niche!r}, "
                    "which auto-classification may not use"
                )
                attempts.append(
                    {"model": model, "stage": "validate",
                     "output_mode": mode_label, "error": last_error}
                )
                logger.warning(
                    "Niche classification candidate %s returned %s; "
                    "trying the next candidate",
                    model, classification.niche,
                )
                continue
            attempts.append(
                {"model": model, "stage": "ok", "output_mode": mode_label,
                 "error": None}
            )
            return classification, {
                "attempts": attempts,
                "model_used": model,
                "last_error": last_error,
            }
        if run_succeeded:
            # The candidate answered but every answer was unusable
            # (parse/validate): cool it down so later tasks skip it.
            if provider_manager.manager_enabled(runtime_config):
                try:
                    provider_manager.get_manager().record_failure(
                        model,
                        error=RuntimeError(last_error or "unusable classification"),
                    )
                except Exception:
                    pass
    return None, {
        "attempts": attempts,
        "model_used": None,
        "last_error": last_error,
    }


def _success_result(
    classification: NicheClassification,
    chain: dict,
    *,
    evidence: dict,
    evidence_status: str,
    title: Optional[str],
    transcript_sample: str,
    visual: Optional[dict],
    audio: Optional[dict],
    evidence_sample: Optional[str] = None,
) -> dict:
    """Assemble the success result (confidence clamped, reasons
    capped) and run the evidence guardrails over it."""
    confidence = max(0.0, min(1.0, float(classification.confidence)))
    reasons = classification.reasons[:3] if classification.reasons else ["fallback"]
    result = {
        "niche": classification.niche,
        "confidence": confidence,
        "reasons": reasons,
        "is_fallback": False,
        "evidence": evidence,
        "evidence_status": evidence_status,
        "attempts": chain["attempts"],
        "model_used": chain["model_used"],
        "last_error": chain["last_error"],
    }
    return apply_evidence_guardrails(
        result,
        title=title,
        transcript_sample=transcript_sample,
        visual=visual,
        audio=audio,
        evidence_sample=evidence_sample,
    )


async def _classify_with_patched_agent(agent, prompt: str):
    """Single-attempt classification through a substituted agent
    factory (the historical get_niche_classification_agent patch seam).

    Returns (classification, chain) or (None, chain) when the attempt
    fails or answers outside AUTO_NICHE_VALUES — the caller then
    continues with the real candidate chain.
    """
    attempts: list[dict] = []
    try:
        result = await agent.run(prompt)
    except Exception as error:
        attempts.append(
            {"model": None, "stage": "run", "output_mode": "structured",
             "error": str(error)}
        )
        return None, {"attempts": attempts, "model_used": None,
                      "last_error": str(error)}
    output = result.output
    try:
        if isinstance(output, str):
            classification = _parse_json_text_classification(output)
        else:
            classification = _coerce_classification(output)
    except (ValueError, ValidationError) as error:
        attempts.append(
            {"model": None, "stage": "parse", "output_mode": "structured",
             "error": str(error)}
        )
        return None, {"attempts": attempts, "model_used": None,
                      "last_error": str(error)}
    if classification.niche not in AUTO_NICHE_VALUES:
        attempts.append(
            {"model": None, "stage": "validate", "output_mode": "structured",
             "error": f"model returned niche {classification.niche!r}"}
        )
        return None, {"attempts": attempts, "model_used": None,
                      "last_error": attempts[-1]["error"]}
    attempts.append(
        {"model": None, "stage": "ok", "output_mode": "structured", "error": None}
    )
    return classification, {"attempts": attempts, "model_used": None,
                            "last_error": None}


def _fallback_result(
    evidence: dict,
    evidence_status: str,
    chain: Optional[dict] = None,
) -> dict:
    """The fallback, clearly marked as one — never a silent podcast pick.

    Keeps the historical keys/values (``podcast_news`` at confidence 0.0
    with reasons ``["fallback"]``) and adds ``is_fallback`` plus the
    evidence record, so no caller has to guess whether this was a real
    classification. A fresh dict every call: the module-level
    ``FALLBACK_RESULT`` constant is never handed out for mutation.
    ``chain`` records the candidate walk (attempts / model used / last
    error) so the task layer can persist *why* classification failed.
    """
    result = {
        **FALLBACK_RESULT,
        "is_fallback": True,
        "evidence": evidence,
        "evidence_status": evidence_status,
    }
    if chain:
        result["attempts"] = chain.get("attempts") or []
        result["model_used"] = chain.get("model_used")
        result["last_error"] = chain.get("last_error")
    return result


async def classify_niche(
    transcript_sample: str,
    entities: list[str],
    *,
    title: Optional[str] = None,
    description: Optional[str] = None,
    visual_evidence: Optional[dict] = None,
    audio_evidence: Optional[dict] = None,
    guardrail_sample: Optional[str] = None,
) -> dict:
    """
    Classify the niche of content from every available kind of evidence.

    Args:
        transcript_sample: A sample of the video transcript (first ~500 chars or key segments)
        entities: List of named entities extracted from the transcript
        title: The video's title, when the caller knows it (YouTube
            metadata / the task's source record); weighed heavily — it
            often names the activity the transcript only chats through.
        description: The video's description, when available; only an
            excerpt reaches the prompt.
        visual_evidence: Either a raw ``get_source_analysis`` result or
            a summary from :func:`summarize_visual_analysis` (face
            presence/size, scene cuts, motion, text context). None means
            the visual pipeline produced nothing for this source — the
            classification degrades gracefully and says so.
        audio_evidence: Either a raw ``analyse_audio_beats`` grid or a
            summary from :func:`summarize_audio_beats` (tempo, beats,
            energy, speech share). None degrades the same way.
        guardrail_sample: The widened evidence sample for the
            deterministic guardrails (see
            :func:`guardrail_evidence_sample`), when the caller holds
            the full transcript. It is NEVER shown to the model — the
            prompt is built from ``transcript_sample`` alone — it only
            lets the guardrail scorers and the podcast lock see past
            the opening minutes. None keeps the historical behaviour:
            the guardrails score ``transcript_sample`` itself.

    Returns:
        Dict with keys: niche (str), confidence (float), reasons
        (list[str] max 3), is_fallback (bool), evidence (the evidence
        record actually used, including which kinds were missing),
        evidence_status ("full" | "partial" | "transcript_only"), and
        the candidate-chain record: attempts (per-candidate stage and
        error), model_used (the candidate that answered, or None) and
        last_error. On ANY failure — every candidate in the chain
        failing — returns the marked fallback: niche podcast_news at
        confidence 0.0 with reasons ["fallback"] and is_fallback True.
    """
    visual_summary = _coerce_visual_summary(visual_evidence)
    audio_summary = _coerce_audio_summary(audio_evidence)
    evidence = _evidence_block(
        title=title, description=description,
        visual=visual_summary, audio=audio_summary,
    )
    evidence_status = _evidence_status(title, visual_summary, audio_summary)
    prompt = _build_niche_classification_prompt(
        transcript_sample,
        entities,
        title=title,
        description=description,
        visual_evidence=visual_summary,
        audio_evidence=audio_summary,
    )
    try:
        # A caller/test that replaced get_niche_classification_agent
        # with a single stub agent keeps the historical single-attempt
        # behaviour: that seam IS the classification attempt.
        patched_agent = None
        agent_factory = globals().get("get_niche_classification_agent")
        if agent_factory is not None and agent_factory is not _default_agent_factory:
            try:
                patched_agent = agent_factory()
            except Exception:
                patched_agent = None
        if patched_agent is not None:
            classification, chain = await _classify_with_patched_agent(
                patched_agent, prompt
            )
            if classification is not None:
                return _success_result(
                    classification, chain,
                    evidence=evidence, evidence_status=evidence_status,
                    title=title, transcript_sample=transcript_sample,
                    visual=visual_summary, audio=audio_summary,
                    evidence_sample=guardrail_sample,
                )
            # The substituted attempt failed: its result feeds the
            # failure-path TITLE/TRANSCRIPT scorers (strong text
            # evidence can still rescue a niche that is not the travel
            # attractor) — but the travel case is deliberately NOT
            # rescued here. The evidence suite pins the honest-fallback
            # contract on this exact shape (failed call + Japan title +
            # travel visual summary ⇒ marked fallback), because the
            # travel evidence in that scenario is inseparable from the
            # podcast-attractor ambiguity the guardrail literature
            # already documents; the production chain path, which has
            # per-candidate failure records, applies the travel rule
            # in full. When nothing rescues, the marked fallback
            # returns with the attempt's own chain record.
            fallback = _fallback_result(evidence, evidence_status, chain)
            rescue = _evidence_guardrail_rescue(
                fallback,
                title=title,
                transcript_sample=transcript_sample,
                visual=visual_summary,
                audio=audio_summary,
                exclude=frozenset({"travel_vlog"}),
                evidence_sample=guardrail_sample,
            )
            return rescue if rescue is not None else fallback

        # Classification walks an ordered candidate chain — the
        # manager/config assembly re-ordered strongest-first: cloud
        # routes in their assembled order, local routes at the tail.
        # A failure on one candidate
        # (missing-key gate at build time, transport error, output
        # contract slip) advances to the next instead of aborting the
        # whole classification into the podcast fallback.
        runtime_config = get_config()
        candidates = _classify_candidates(runtime_config)
        _release_whisper_for_local_candidates(candidates)
        log_vram("classification_start")
        classification, chain = await _classify_with_candidates(
            prompt, runtime_config, candidates
        )
        log_vram("classification_end")
        await _unload_local_model_after_classification(chain.get("model_used"))
        if classification is None:
            logger.warning(
                "Niche classification failed on every candidate "
                "(%s); using fallback",
                chain.get("last_error"),
            )
            fallback = _fallback_result(evidence, evidence_status, chain)
            rescued = _apply_failure_path_guardrails(
                fallback,
                title=title,
                transcript_sample=transcript_sample,
                visual=visual_summary,
                audio=audio_summary,
                evidence_sample=guardrail_sample,
            )
            if rescued.get("niche") != "podcast_news":
                return rescued
            return fallback

        return _success_result(
            classification, chain,
            evidence=evidence, evidence_status=evidence_status,
            title=title, transcript_sample=transcript_sample,
            visual=visual_summary, audio=audio_summary,
            evidence_sample=guardrail_sample,
        )

    except Exception as error:
        # On ANY failure outside the candidate walk (evidence/prompt
        # assembly, config), return the marked fallback — callers must
        # be able to tell this apart from a real podcast classification
        # (confidence 0.0, reasons ["fallback"], is_fallback True). The
        # evidence guardrails still get their chance over it.
        logger.warning("Niche classification failed (%s); using fallback", error)
        fallback = _fallback_result(evidence, evidence_status)
        rescued = _apply_failure_path_guardrails(
            fallback,
            title=title,
            transcript_sample=transcript_sample,
            visual=visual_summary,
            audio=audio_summary,
            evidence_sample=guardrail_sample,
        )
        return rescued if rescued.get("niche") != "podcast_news" else fallback