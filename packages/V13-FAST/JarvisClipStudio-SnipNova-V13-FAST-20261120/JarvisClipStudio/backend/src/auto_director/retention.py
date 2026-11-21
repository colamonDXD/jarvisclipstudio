"""Retention structure analysis for a candidate clip (Workstreams B+C).

Selection used to treat "a big sentence at the start" as a hook. This
module instead analyses the STRUCTURE of one candidate segment, the way
an editor would before picking it:

1. what happens in the first second?
2. why would someone stop scrolling?
3. what question / curiosity / open loop does the opening create?
4. does the next section reward that curiosity?
5. is there enough context to understand the moment?
6. does it build / escalate?
7. is the payoff satisfying (does the referenced event actually occur)?
8. does the ending invite a rewatch, or leave confusion?

HONESTY IS A HARD REQUIREMENT: a hook the footage does not deliver is a
DEFECT. When the opening makes a checkable promise (a question, an
announced event, a superlative claim, or a supplied hook title) and no
later sentence in the SAME segment delivers it, ``hook_honesty`` is
``overpromises`` — with the claim quoted and the footage evidence (the
searched-for terms and the closing sentence) recorded. That verdict is
deterministic and can never be upgraded by a model judgement.

This analysis informs selection only through a bounded, demote-only
influence (see ``selection_for``): it can subtract at most
``MAX_RETENTION_PENALTY`` points, it never adds points, and a candidate
is only rejected outright when it both overpromises AND lacks context —
and even then only when enough other candidates remain. It makes no
prediction about how a clip will perform; no field, string or comment
here estimates an audience outcome.

Deterministic rules first. Where a model judgement is genuinely useful
(phrasing *why* a curiosity loop works, in plain words), it is routed
through the same provider plumbing the planner/classifier use
(``plan_service`` / ``classify`` patterns, the configured LLM chain) via
an injectable seam, it may only rephrase narrative fields, and every
field it touches is marked ``model_judgement`` in ``field_sources``.
With no judge available the deterministic record stands unchanged.

The record is additive metadata: callers store it inside the clip's
existing ``clip_plan`` JSON (the ``retention`` key, next to
``hook_application``) and on the in-pipeline segment dict. No schema
change is needed for that; see the workstream report for the follow-up
if a dedicated column is ever wanted.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Any, Callable, Optional, Sequence

logger = logging.getLogger(__name__)

RETENTION_VERSION = 1

# --- Bounded selection influence -------------------------------------------
#
# Demote-only, capped. Retention can guard selection; it cannot promote
# a candidate, so it can never become a second scoring system.
MAX_RETENTION_PENALTY = 30
PENALTY_OVERPROMISE = 20
PENALTY_MISSING_CONTEXT = 10
PENALTY_THIN_CONTEXT = 5
PENALTY_NO_PAYOFF = 5
PENALTY_CONFUSING_ENDING = 5
PENALTY_ABRUPT_ENDING = 3

# --- Vocabularies ------------------------------------------------------------

STOPWORDS = frozenset(
    """
    a an the and or but if then else when while of at by for with about into
    through after over between out against during without before under around
    among is am are was were be been being have has had having do does did
    doing would should could ought i you he she it we they them his her its
    our their this that these those am an my your me him us him what which
    who whom whose why how all any both each few more most other some such
    no nor not only own same so than too very can will just should now as
    to from in on off up down here there once again further then
    """.split()
)

_REACTION_WORDS = frozenset(
    {"wow", "whoa", "oh", "ooh", "ah", "hah", "haha", "woah", "damn", "wait"}
)
_REACTION_PHRASES = (
    "no way",
    "oh my god",
    "oh my goodness",
    "are you serious",
    "you are joking",
    "i cannot believe",
    "i can't believe",
    "look at that",
    "look at this",
    "did you see that",
    "what just happened",
)
_EMOTION_WORDS = frozenset(
    {
        "angry", "furious", "terrified", "scared", "shocked", "crying",
        "laughing", "screaming", "unbelievable", "incredible", "amazing",
        "horrified", "devastated", "thrilled",
    }
)
_STRONG_MARKERS = frozenset(
    {
        "never", "nobody", "everyone", "always", "first", "only", "stop",
        "secret", "truth", "wrong", "mistake", "biggest", "best", "worst",
        "largest", "smallest", "fastest", "craziest",
    }
)
_SUPERLATIVE_RE = re.compile(
    r"\b(biggest|best|worst|largest|smallest|fastest|slowest|craziest|"
    r"most shocking|shocking|unbelievable|never seen|first time ever|"
    r"won't believe|wont believe|cannot believe|can't believe|"
    r"no one has ever|nobody has ever|greatest|hardest|easiest)\b"
)
_ESCALATION_MARKERS = frozenset(
    {
        "then", "suddenly", "finally", "even", "more", "bigger", "again",
        "another", "worse", "escalated", "kept", "still", "yet", "until",
    }
)
_RESULT_RE = re.compile(
    r"\b(worked|works|succeeded|success|won|winning|paid off|turned out|"
    r"in the end|the result was|that is how|that's how|managed to|"
    r"finished|completed|fixed it|solved|answered|revealed|because)\b"
)
_ANSWER_MARKER_RE = re.compile(
    r"\b(because|the answer is|the reason is|turned out|so it|that is why|"
    r"that's why|which means|in fact)\b"
)

# Promise / announcement patterns in an opening sentence. ``rest`` is the
# part of the sentence that names what is promised; its content words are
# the checkable expectation.
_PROMISE_PATTERNS: tuple[re.Pattern, ...] = tuple(
    re.compile(pattern, re.IGNORECASE)
    for pattern in (
        r"\bi(?:'m| am) going to (?:show|tell|prove|try|do|make|build|fix|reveal|explain)\b(?P<rest>.*)",
        r"\bwatch (?:what|how|this)\b(?P<rest>.*)",
        r"\byou (?:will|won't|wont) (?:see|believe|guess)\b(?P<rest>.*)",
        r"\blet'?s see (?:if|what|how|whether)\b(?P<rest>.*)",
        r"\bwait (?:until|till|for)\b(?P<rest>.*)",
        r"\bhere(?:'s| is) (?:why|how|what)\b(?P<rest>.*)",
        r"\bthe (?:secret|trick|reason|answer|truth) is\b(?P<rest>.*)",
        r"\bwhat happened next\b(?P<rest>.*)",
    )
)
_GENERIC_PROMISE_WORDS = frozenset(
    {
        "happened", "happens", "happen", "next", "this", "that", "what",
        "how", "you", "today", "tonight", "tomorrow",
    }
)


def _stem(token: str) -> str:
    word = token.lower().strip(".,!?\"'()[]")
    for suffix in ("ingly", "edly", "ing", "ed", "es", "s"):
        if len(word) > len(suffix) + 2 and word.endswith(suffix):
            return word[: -len(suffix)]
    if len(word) > 4 and word.endswith("e"):
        # collapse -> collaps, matching collapsed -> collaps above.
        return word[:-1]
    return word


def _content_words(text: str) -> list[str]:
    words = []
    for raw in re.findall(r"[A-Za-z0-9']+", (text or "").lower()):
        token = raw.strip("'")
        if token and token not in STOPWORDS and len(token) > 1:
            words.append(token)
    return words


def _norm_words(words: Optional[Sequence[dict]], text: str, segment_start: float) -> list[dict]:
    """Clip-relative word dicts {text, start, end}; untimed fallback from text."""
    out: list[dict] = []
    for word in words or []:
        token = str(word.get("text") or word.get("word") or "").strip()
        if not token:
            continue
        try:
            start = float(word.get("start") or 0.0)
            end = float(word.get("end") or start)
        except (TypeError, ValueError):
            start, end = 0.0, 0.0
        # Word timings may be source-absolute; segment_start rebases them.
        out.append(
            {
                "text": token,
                "start": max(0.0, start - segment_start) if start >= segment_start else start,
                "end": max(0.0, end - segment_start) if end >= segment_start else end,
            }
        )
    if out and any(w["end"] > 0 for w in out):
        return sorted(out, key=lambda w: w["start"])
    # No usable timings: split the text, timings unknown (None evidence).
    return [{"text": tok, "start": 0.0, "end": 0.0} for tok in (text or "").split()]


@dataclass
class _Sentence:
    index: int
    text: str
    start: Optional[float]
    end: Optional[float]
    content: list[str]  # raw content tokens
    stems: set[str]

    def evidence(self) -> dict[str, Any]:
        return {"quote": self.text, "start": self.start, "end": self.end}


def _sentences(words: list[dict]) -> list[_Sentence]:
    from ..clip_openings import split_sentences

    groups = split_sentences([w["text"] for w in words])
    sentences: list[_Sentence] = []
    timed = any(w["end"] > 0 for w in words)
    for index, group in enumerate(groups):
        toks = [words[i]["text"] for i in group]
        text = " ".join(toks).strip()
        if not text:
            continue
        content = _content_words(text)
        sentences.append(
            _Sentence(
                index=index,
                text=text,
                start=float(words[group[0]]["start"]) if timed else None,
                end=float(words[group[-1]]["end"]) if timed else None,
                content=content,
                stems={_stem(tok) for tok in content},
            )
        )
    return sentences


@dataclass
class RetentionAnalysis:
    """The structured retention record for one candidate segment."""

    first_second: dict[str, Any] = field(default_factory=dict)
    stop_scroll_reason: dict[str, Any] = field(default_factory=dict)
    curiosity_created: dict[str, Any] = field(default_factory=dict)
    context_sufficiency: dict[str, Any] = field(default_factory=dict)
    build_and_escalation: dict[str, Any] = field(default_factory=dict)
    payoff: dict[str, Any] = field(default_factory=dict)
    ending_type: dict[str, Any] = field(default_factory=dict)
    hook_honesty: dict[str, Any] = field(default_factory=dict)
    selection: dict[str, Any] = field(default_factory=dict)
    field_sources: dict[str, str] = field(default_factory=dict)
    source: str = "deterministic"

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": RETENTION_VERSION,
            "source": self.source,
            "first_second": self.first_second,
            "stop_scroll_reason": self.stop_scroll_reason,
            "curiosity_created": self.curiosity_created,
            "context_sufficiency": self.context_sufficiency,
            "build_and_escalation": self.build_and_escalation,
            "payoff": self.payoff,
            "ending_type": self.ending_type,
            "hook_honesty": self.hook_honesty,
            "selection": self.selection,
            "field_sources": self.field_sources,
        }

    @classmethod
    def from_dict(cls, raw: Any) -> "RetentionAnalysis":
        raw = raw if isinstance(raw, dict) else {}
        keys = (
            "first_second", "stop_scroll_reason", "curiosity_created",
            "context_sufficiency", "build_and_escalation", "payoff",
            "ending_type", "hook_honesty", "selection", "field_sources",
        )
        values = {k: raw.get(k) if isinstance(raw.get(k), dict) else {} for k in keys}
        return cls(source=str(raw.get("source") or "deterministic"), **values)


# --- Expectations (the checkable promises an opening makes) -------------------


def _expectations(
    sentences: list[_Sentence], hook_title: Optional[str]
) -> list[dict[str, Any]]:
    expectations: list[dict[str, Any]] = []
    if not sentences:
        return expectations
    opening = sentences[0]
    opening_text = opening.text

    if hook_title:
        keywords = [
            tok for tok in _content_words(hook_title)
            if tok not in _GENERIC_PROMISE_WORDS
        ]
        if keywords:
            expectations.append(
                {
                    "kind": "hook_title",
                    "claim": hook_title,
                    "keywords": keywords,
                    "stems": {_stem(tok) for tok in keywords},
                    "sentence_index": -1,  # the title is not a sentence
                    "evidence": {"quote": hook_title, "start": None, "end": None},
                }
            )

    if opening_text.rstrip().endswith("?"):
        keywords = [
            tok for tok in opening.content if tok not in _GENERIC_PROMISE_WORDS
        ]
        expectations.append(
            {
                "kind": "question",
                "claim": opening_text,
                "keywords": keywords,
                "stems": {_stem(tok) for tok in keywords},
                "sentence_index": opening.index,
                "evidence": opening.evidence(),
            }
        )

    for pattern in _PROMISE_PATTERNS:
        match = pattern.search(opening_text)
        if not match:
            continue
        rest = match.group("rest") or ""
        keywords = [
            tok for tok in _content_words(rest)
            if tok not in _GENERIC_PROMISE_WORDS
        ]
        superlative = bool(_SUPERLATIVE_RE.search(opening_text))
        if not keywords and not superlative:
            continue  # vague framing with no checkable claim and no hype
        expectations.append(
            {
                "kind": "promise",
                "claim": opening_text,
                "keywords": keywords,
                "stems": {_stem(tok) for tok in keywords},
                "sentence_index": opening.index,
                "evidence": opening.evidence(),
            }
        )
        break

    if _SUPERLATIVE_RE.search(opening_text) and not any(
        exp["sentence_index"] == opening.index for exp in expectations
    ):
        keywords = [
            tok for tok in opening.content if tok not in _GENERIC_PROMISE_WORDS
        ]
        expectations.append(
            {
                "kind": "claim",
                "claim": opening_text,
                "keywords": keywords,
                "stems": {_stem(tok) for tok in keywords},
                "sentence_index": opening.index,
                "evidence": opening.evidence(),
            }
        )
    return expectations


def _payoff_for_expectations(
    expectations: list[dict[str, Any]], sentences: list[_Sentence]
) -> tuple[Optional[_Sentence], list[dict[str, Any]]]:
    """(delivering sentence, per-expectation results).

    An expectation is delivered when a LATER sentence shares enough of
    its content stems: at least one stem for a short expectation
    (<=2 keywords), at least two otherwise. A hook title is checked
    against the whole segment instead (the title names the clip, not a
    moment inside it).
    """
    results: list[dict[str, Any]] = []
    delivering: Optional[_Sentence] = None
    for exp in expectations:
        stems: set[str] = exp["stems"]
        if not stems:
            results.append({**exp, "delivered": False, "matched": []})
            continue
        needed = 1 if len(stems) <= 2 else 2
        best: Optional[_Sentence] = None
        best_matched: list[str] = []
        for sentence in sentences:
            if exp["kind"] != "hook_title" and sentence.index <= exp["sentence_index"]:
                continue
            matched = sorted(stems & sentence.stems)
            if len(matched) >= needed and len(matched) > len(best_matched):
                best, best_matched = sentence, matched
        results.append(
            {**exp, "delivered": best is not None, "matched": best_matched,
             "delivering_sentence": best}
        )
        if best is not None and delivering is None:
            delivering = best
    return delivering, results


# --- The deterministic analysis -----------------------------------------------


def analyse_retention(
    *,
    text: str = "",
    words: Optional[Sequence[dict]] = None,
    duration: float = 0.0,
    segment_start: float = 0.0,
    audio_features: Optional[dict] = None,
    hook_title: Optional[str] = None,
    plan_phases: Optional[Sequence[dict]] = None,
    hook_type: Optional[str] = None,
    llm_judge: Optional[Callable[[str, dict], Optional[dict]]] = None,
) -> RetentionAnalysis:
    """Analyse one candidate segment's retention structure.

    ``words`` are transcript words ({text|word, start, end}); timings may
    be clip-relative or source-absolute when ``segment_start`` is given.
    ``audio_features`` is the planner's shape ({energy_peaks, pauses}).
    ``plan_phases`` (optional) are the clip plan's phases ({start_offset,
    end_offset, intent}) — a planned payoff phase is evidence, never a
    substitute for the footage delivering it.
    ``llm_judge`` is the optional model seam: a callable
    ``(prompt, deterministic_dict) -> dict | None`` that may rephrase
    narrative fields only (see module docstring).
    """
    norm = _norm_words(words, text, float(segment_start or 0.0))
    sentences = _sentences(norm)
    full_text = text or " ".join(w["text"] for w in norm)
    duration = float(duration or 0.0) or (
        max((w["end"] for w in norm), default=0.0)
    )
    sources: dict[str, str] = {}

    def det(field_name: str) -> None:
        sources[field_name] = "deterministic"

    # (1) First second ---------------------------------------------------
    timed = any(w["end"] > 0 for w in norm)
    if timed:
        first_words = [w for w in norm if w["start"] < 1.0] or norm[:3]
        first_text = " ".join(w["text"] for w in first_words)
        first_evidence = {
            "quote": first_text,
            "start": first_words[0]["start"] if first_words else None,
            "end": first_words[-1]["end"] if first_words else None,
        }
    elif sentences:
        first_text = sentences[0].text
        first_evidence = sentences[0].evidence()
    else:
        first_text, first_evidence = "", {"quote": "", "start": None, "end": None}
    first_second = {
        "summary": (
            f"The clip opens on: \"{first_text}\"" if first_text
            else "The clip opens with no speech in its first second."
        ),
        "evidence": [first_evidence],
    }
    det("first_second")

    # Expectations + payoff (computed early: stop-scroll/curiosity need them)
    expectations = _expectations(sentences, hook_title)
    delivering, exp_results = _payoff_for_expectations(expectations, sentences)

    # (2) Stop-scroll reason ----------------------------------------------
    opening = sentences[0] if sentences else None
    opening_lower = (opening.text.lower() if opening else "")
    stop_kind, stop_reason = "none", "No distinct stop-scroll signal in the opening words."
    stop_evidence = [opening.evidence()] if opening else []
    if opening:
        if opening.text.rstrip().endswith("?"):
            stop_kind = "question"
            stop_reason = "The opening asks a direct question the viewer has to stay to hear answered."
        elif any(phrase in opening_lower for phrase in _REACTION_PHRASES) or (
            opening.content and opening.content[0] in _REACTION_WORDS
        ):
            stop_kind = "reaction"
            stop_reason = "The opening is a genuine reaction — emotion lands before any explanation."
        elif any(tok in _EMOTION_WORDS for tok in opening.content):
            stop_kind = "reaction"
            stop_reason = "The opening carries a strong emotional word before any context."
        elif any(exp["kind"] == "promise" for exp in expectations):
            stop_kind = "announced_event"
            stop_reason = "The opening announces a specific event the viewer can stay to see happen."
        elif re.search(r"\d", opening.text) or any(
            tok in _STRONG_MARKERS for tok in opening.content
        ) or _SUPERLATIVE_RE.search(opening_lower):
            stop_kind = "strong_statement"
            stop_reason = "The opening makes a specific, checkable strong statement (a number, an absolute, or a superlative)."
        elif "but" in opening.content[:4] or "suddenly" in opening.content:
            stop_kind = "unexpected_turn"
            stop_reason = "The opening signals an unexpected turn in its first sentence."
    stop_scroll_reason = {"kind": stop_kind, "reason": stop_reason, "evidence": stop_evidence}
    det("stop_scroll_reason")

    # (3) Curiosity ---------------------------------------------------------
    curiosity_created_bool = bool(expectations) or stop_kind == "question"
    if expectations:
        primary = expectations[0]
        curiosity_desc = (
            f"The opening creates an open loop: {primary['claim']} "
            f"({primary['kind']}) — it is only closed if the footage later delivers it."
        )
        curiosity_evidence = [primary["evidence"]]
    elif opening and opening.text.rstrip().endswith("?"):
        curiosity_desc = "The opening question creates the open loop."
        curiosity_evidence = [opening.evidence()]
    else:
        curiosity_desc = "The opening creates no explicit question or open loop; it is a self-contained statement."
        curiosity_evidence = stop_evidence
    curiosity_created = {
        "created": curiosity_created_bool,
        "description": curiosity_desc,
        "evidence": curiosity_evidence,
    }
    det("curiosity_created")

    # (5) Context sufficiency ----------------------------------------------
    context_level, context_why = "sufficient", "The opening sentence stands on its own."
    context_evidence = stop_evidence
    if opening is None:
        context_level, context_why = "missing", "The segment has no opening sentence to judge."
    else:
        from ..clip_openings import is_dangling, normalize_token, opening_text_issue

        weak = False
        try:
            from ..media.timeline import _opening_is_weak

            weak = bool(_opening_is_weak(norm, 0))
        except Exception:
            weak = False
        issue = opening_text_issue(opening.text)
        opening_tokens = [
            normalize_token(tok) for tok in opening.text.split()[:6]
        ]
        if is_dangling([tok for tok in opening_tokens if tok]) or weak or issue:
            context_level = "missing"
            context_why = (
                "The opening depends on context the viewer has not seen "
                f"({issue or 'weak opener'}: it opens by continuing or pointing back at earlier speech)."
            )
        elif len(opening.content) < 3:
            context_level = "thin"
            context_why = (
                f"The opening sentence has only {len(opening.content)} content word(s) — "
                "enough to follow, not enough to understand the moment fully."
            )
        elif opening_tokens and opening_tokens[0] in {"it", "this", "that", "he", "she", "they"}:
            context_level = "thin"
            context_why = "The opening starts on a pronoun whose subject is not named in the sentence."
    context_sufficiency = {
        "level": context_level,
        "why": context_why,
        "evidence": context_evidence,
    }
    det("context_sufficiency")

    # (7) Payoff --------------------------------------------------------------
    payoff_status, payoff_at, payoff_why, payoff_evidence = "absent", None, "", []
    if expectations:
        if delivering is not None:
            payoff_status, payoff_at = "present", delivering.start
            payoff_why = (
                "A later sentence delivers what the opening set up "
                f"(matched: {', '.join(sorted(set(m for r in exp_results for m in r['matched'])))})."
            )
            payoff_evidence = [delivering.evidence()]
        else:
            payoff_why = (
                "No later sentence in this segment delivers the opening's "
                f"setup (searched for: {', '.join(sorted({k for r in exp_results for k in r['keywords']})) or 'the promised event'}). "
                f"The segment closes on: \"{sentences[-1].text if sentences else ''}\""
            )
            payoff_evidence = [sentences[-1].evidence()] if sentences else []
    else:
        planned_payoff = next(
            (p for p in (plan_phases or []) if isinstance(p, dict) and p.get("intent") == "payoff"),
            None,
        )
        result_sentence = next(
            (s for s in sentences[1:] if _RESULT_RE.search(s.text.lower())), None
        )
        if stop_kind == "reaction":
            payoff_status, payoff_at = "present", opening.start if opening else None
            payoff_why = (
                "The opening reaction is itself the moment the clip exists "
                "for; the opening made no promise beyond showing it."
            )
            payoff_evidence = stop_evidence
        elif result_sentence is not None:
            payoff_status, payoff_at = "present", result_sentence.start
            payoff_why = "A later sentence states the outcome of what the opening set in motion."
            payoff_evidence = [result_sentence.evidence()]
        elif planned_payoff is not None:
            payoff_status = "present"
            try:
                payoff_at = float(planned_payoff.get("start_offset"))
            except (TypeError, ValueError):
                payoff_at = None
            payoff_why = "The clip plan marks a payoff phase and no opening promise goes undelivered."
            payoff_evidence = []
        else:
            payoff_why = (
                "No sentence in the segment states an outcome or resolution; "
                "the moment stands alone without a payoff."
            )
            payoff_evidence = [sentences[-1].evidence()] if sentences else []
    payoff = {
        "status": payoff_status,
        "at_seconds": payoff_at,
        "why": payoff_why,
        "evidence": payoff_evidence,
    }
    det("payoff")

    # (4)+(6) Does the next section reward curiosity / build ------------------
    later = sentences[1:]
    escalation_hit = next(
        (s for s in later if _ESCALATION_MARKERS & set(s.content)), None
    )
    peaks = []
    if isinstance(audio_features, dict):
        for peak in audio_features.get("energy_peaks") or []:
            try:
                peaks.append(float(peak))
            except (TypeError, ValueError):
                continue
    late_peak = bool(duration and peaks and max(peaks) > 0.6 * duration and len(peaks) >= 2)
    if len(sentences) <= 1:
        build_verdict, build_why, build_evidence = (
            "single_beat", "The segment is a single sentence; there is no build.", stop_evidence,
        )
    elif delivering is not None or escalation_hit is not None or late_peak:
        build_verdict = "builds"
        if delivering is not None:
            build_why = "The segment builds from the opening setup to the sentence that delivers it."
            build_evidence = [delivering.evidence()]
        elif escalation_hit is not None:
            build_why = "Later sentences escalate the opening (escalation wording after the setup)."
            build_evidence = [escalation_hit.evidence()]
        else:
            build_why = "Audio energy peaks continue into the later part of the segment."
            build_evidence = []
    else:
        build_verdict, build_why, build_evidence = (
            "flat",
            "Later sentences continue at the same level; nothing escalates or resolves the opening.",
            [later[-1].evidence()] if later else [],
        )
    build_and_escalation = {"verdict": build_verdict, "why": build_why, "evidence": build_evidence}
    det("build_and_escalation")

    # (8) Ending ----------------------------------------------------------------
    ending_type_value, ending_why, ending_evidence = "resolved", "", []
    if sentences:
        last = sentences[-1]
        ending_evidence = [last.evidence()]
        first_stems = sentences[0].stems if len(sentences) > 1 else set()
        overlap = last.stems & first_stems
        if (
            len(sentences) > 1
            and len(overlap) >= 3
            and len(overlap) >= 0.5 * max(1, len(last.stems))
        ):
            ending_type_value = "loopable"
            ending_why = "The closing sentence repeats most of the opening's key words, so the end flows back into the start."
        elif not re.search(r"[.!?][\"']?\s*$", full_text.strip()) or len(last.content) < 3:
            ending_type_value = "abrupt"
            ending_why = "The segment ends mid-thought or on a fragment, with no closing sentence."
        elif last.text.rstrip().endswith("?"):
            ending_type_value = "confusing"
            ending_why = "The segment ends on an unanswered question, leaving the open loop unresolved."
        else:
            ending_type_value = "resolved"
            ending_why = "The segment ends on a complete sentence that closes the moment."
    ending = {"type": ending_type_value, "why": ending_why, "evidence": ending_evidence}
    det("ending_type")

    # Honesty ---------------------------------------------------------------------
    undelivered = [r for r in exp_results if not r["delivered"]]
    if expectations and undelivered:
        primary = undelivered[0]
        honesty = {
            "verdict": "overpromises",
            "claim": primary["claim"],
            "claim_evidence": [primary["evidence"]],
            "footage_evidence": payoff_evidence,
            "why": (
                f"The opening claims \"{primary['claim']}\" but no later sentence "
                f"delivers it (searched for: {', '.join(primary['keywords']) or 'the promised event'}). "
                "A hook the footage does not deliver is a defect, not a style choice."
            ),
        }
    elif expectations:
        honesty = {
            "verdict": "delivers",
            "claim": expectations[0]["claim"],
            "claim_evidence": [expectations[0]["evidence"]],
            "footage_evidence": payoff_evidence,
            "why": "The footage later delivers what the opening set up.",
        }
    else:
        honesty = {
            "verdict": "delivers",
            "claim": opening.text if opening else "",
            "claim_evidence": stop_evidence,
            "footage_evidence": stop_evidence,
            "why": "The opening makes no promise beyond the moment it shows.",
        }
    det("hook_honesty")

    record = RetentionAnalysis(
        first_second=first_second,
        stop_scroll_reason=stop_scroll_reason,
        curiosity_created=curiosity_created,
        context_sufficiency=context_sufficiency,
        build_and_escalation=build_and_escalation,
        payoff=payoff,
        ending_type=ending,
        hook_honesty=honesty,
        field_sources=sources,
    )
    record.selection = selection_for(record)
    sources["selection"] = "deterministic"

    # Optional model judgement: narrative rephrasing only. It can never
    # change a verdict, a status, a level, or the selection outcome —
    # those keys are not in the merge whitelist.
    if llm_judge is not None:
        try:
            judged = llm_judge(build_retention_prompt(full_text, record.to_dict()), record.to_dict())
        except Exception:
            logger.warning("Retention model judge failed; keeping deterministic record", exc_info=True)
            judged = None
        if isinstance(judged, dict):
            merged = _merge_model_judgement(record, judged)
            if merged:
                record.source = "llm_assisted"
    return record


_NARRATIVE_FIELDS = {
    # judge key -> (record attribute, inner key)
    "curiosity_description": ("curiosity_created", "description"),
    "build_why": ("build_and_escalation", "why"),
    "payoff_why": ("payoff", "why"),
    "stop_scroll_reason": ("stop_scroll_reason", "reason"),
}


def _merge_model_judgement(record: RetentionAnalysis, judged: dict) -> bool:
    merged = False
    for judge_key, (attr, inner) in _NARRATIVE_FIELDS.items():
        value = judged.get(judge_key)
        if isinstance(value, str) and value.strip():
            getattr(record, attr)[inner] = value.strip()[:400]
            record.field_sources[attr] = "model_judgement"
            merged = True
    return merged


def build_retention_prompt(text: str, deterministic: dict) -> str:
    """Prompt for the optional model judge (narrative fields only)."""
    return (
        "You are an experienced short-form editor reviewing ONE clip's "
        "opening structure. A deterministic analysis is below; its "
        "verdicts are final and must not be contradicted. In plain "
        "words, rephrase only: why the opening's curiosity loop works "
        "or does not, why the segment builds or stays flat, why the "
        "payoff lands or is missing, and why someone would stop "
        "scrolling. Never invent speech or events not in the transcript. "
        "Never estimate audience outcomes.\n\n"
        f"Transcript:\n{text[:3000]}\n\n"
        f"Deterministic analysis:\n{deterministic}\n\n"
        "Return JSON with any of: curiosity_description, build_why, "
        "payoff_why, stop_scroll_reason."
    )


# --- Selection influence (bounded, demote-only) --------------------------------


def selection_for(record: RetentionAnalysis) -> dict[str, Any]:
    """The bounded selection outcome for one record.

    ``penalty`` is in [0, MAX_RETENTION_PENALTY] and is only ever
    subtracted. ``action`` is ``reject`` only when the clip both
    overpromises AND lacks context; the caller must still honour
    ``keep_at_least`` (see ``apply_retention_to_segments``) so a
    rejection can never shrink a slate below its target.
    """
    penalty = 0
    reasons: list[str] = []
    honesty = (record.hook_honesty or {}).get("verdict")
    context = (record.context_sufficiency or {}).get("level")
    if honesty == "overpromises":
        penalty += PENALTY_OVERPROMISE
        reasons.append("the opening promises what the footage does not deliver")
    if context == "missing":
        penalty += PENALTY_MISSING_CONTEXT
        reasons.append("the opening depends on missing context")
    elif context == "thin":
        penalty += PENALTY_THIN_CONTEXT
        reasons.append("the opening gives thin context")
    if (record.payoff or {}).get("status") == "absent" and honesty != "overpromises":
        penalty += PENALTY_NO_PAYOFF
        reasons.append("no payoff sentence in the segment")
    ending = (record.ending_type or {}).get("type")
    if ending == "confusing":
        penalty += PENALTY_CONFUSING_ENDING
        reasons.append("the ending leaves an unanswered question")
    elif ending == "abrupt":
        penalty += PENALTY_ABRUPT_ENDING
        reasons.append("the ending is abrupt")
    penalty = min(MAX_RETENTION_PENALTY, penalty)
    action = (
        "reject"
        if honesty == "overpromises" and context == "missing"
        else ("demote" if penalty else "keep")
    )
    return {"action": action, "penalty": penalty, "reasons": reasons}


def retention_enabled() -> bool:
    import os

    return os.environ.get("RETENTION_ANALYSIS_ENABLED", "").strip().lower() not in {
        "0", "false", "no", "off",
    }


def apply_retention_to_segments(
    segments: list[dict],
    analyses: Sequence[RetentionAnalysis],
    *,
    keep_at_least: int = 0,
) -> list[dict]:
    """Apply records to in-pipeline segment dicts (bounded, slate-safe).

    Each segment gains ``retention`` (the record dict). Demotions
    subtract the penalty from ``hook_score``/``virality_score`` exactly
    like the opening review's penalty. ``reject`` segments are dropped
    only while at least ``keep_at_least`` segments would remain;
    otherwise a would-be rejection is demoted instead. The list is
    stable-re-sorted best-first only when a score actually changed.
    """
    out: list[dict] = []
    changed = False
    paired = list(zip(segments, analyses))
    reject_count = sum(
        1 for _, a in paired if a.selection.get("action") == "reject"
    )
    droppable = max(0, len(paired) - max(0, keep_at_least))
    dropped = 0
    for segment, analysis in paired:
        segment = dict(segment)
        segment["retention"] = analysis.to_dict()
        action = analysis.selection.get("action")
        penalty = int(analysis.selection.get("penalty") or 0)
        if action == "reject" and dropped < min(reject_count, droppable):
            dropped += 1
            changed = True
            continue
        if penalty:
            hook = int(segment.get("hook_score") or 0)
            reduced = max(0, hook - penalty)
            segment["hook_score"] = reduced
            segment["virality_score"] = max(
                0, int(segment.get("virality_score") or 0) - (hook - reduced)
            )
            changed = True
        out.append(segment)
    if changed:
        out = sorted(
            out, key=lambda s: int(s.get("virality_score") or 0), reverse=True
        )
    return out


# --- Optional model judge through the existing provider plumbing ----------------
#
# Mirrors plan_service.get_plan_agent: the same configured LLM chain
# (Groq openai/gpt-oss-120b primary in this deployment, with the
# configured fallbacks), lazy agent construction, and a hard fail-safe —
# any error returns the deterministic record.


_judge_agent: Any = None
_judge_agent_signature: Any = None


def _get_judge_agent():  # pragma: no cover - live provider plumbing
    global _judge_agent, _judge_agent_signature
    from pydantic import BaseModel, Field
    from pydantic_ai import Agent
    from pydantic_ai.output import NativeOutput

    from ..config import get_config

    class _JudgeDraft(BaseModel):
        curiosity_description: str = Field(default="")
        build_why: str = Field(default="")
        payoff_why: str = Field(default="")
        stop_scroll_reason: str = Field(default="")

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
    if _judge_agent is None or _judge_agent_signature != signature:
        from ..ai import _build_transcript_model, _get_missing_llm_key_error, _uses_native_output
        from ..runtime_settings import apply_settings_to_process_env

        apply_settings_to_process_env(runtime_config.as_runtime_settings())
        config_error = _get_missing_llm_key_error(runtime_config.llm, runtime_config)
        if config_error:
            raise RuntimeError(config_error)
        _judge_agent = Agent(
            model=_build_transcript_model(runtime_config),
            output_type=(
                NativeOutput(_JudgeDraft, strict=True)
                if _uses_native_output(runtime_config.llm)
                else _JudgeDraft
            ),
            system_prompt=(
                "You rephrase a deterministic clip-structure analysis in "
                "plain editor language. Verdicts are final; never invent "
                "content; never estimate audience outcomes."
            ),
            retries={'output': 2},
        )
        _judge_agent_signature = signature
        _judge_agent._draft_type = _JudgeDraft  # type: ignore[attr-defined]
    return _judge_agent


async def analyse_retention_with_judge(**kwargs: Any) -> RetentionAnalysis:
    """Deterministic analysis, optionally rephrased by the configured LLM.

    NEVER raises: any provider failure returns the deterministic record.
    Tests fake the seam by passing ``llm_judge`` to ``analyse_retention``
    instead — no live model is used here in tests.
    """
    import asyncio

    base = analyse_retention(**kwargs)

    try:
        agent = _get_judge_agent()
        prompt = build_retention_prompt(kwargs.get("text") or "", base.to_dict())
        result = await asyncio.wait_for(agent.run(prompt), timeout=30.0)
        judged = result.output.model_dump() if hasattr(result.output, "model_dump") else dict(result.output)
        if isinstance(judged, dict) and _merge_model_judgement(base, judged):
            base.source = "llm_assisted"
            base.selection = selection_for(base)
    except Exception:
        logger.warning("Retention judge unavailable; keeping deterministic record", exc_info=True)
    return base
