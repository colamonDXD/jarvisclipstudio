"""Campaign brief rules — enforceable schema + cut-time enforcement.

Adapted from Chopify (``campaign.py``, MIT — see NOTICE): a campaign
brief becomes a small rule set (``must_mention``, ``banned``, ``min_len``,
``max_len``, ``aspect``, plus ``goal`` / ``cta`` / ``tone`` context) and
the rules are ENFORCED when clips are chosen, not merely scored after
the fact. A clip that violates the brief is rejected with the reason
recorded — never silently shipped.

What is deliberately different from Chopify: no agent-CLI providers.
Brief *extraction* by a model already lives in
``content_requirements_llm`` on our own ladder; this module owns the
deterministic parts — normalisation, a keyword fallback parser for raw
brief text (labelled ``heuristic``, exactly like Chopify's warning), and
enforcement. Enforcement is pure: segment text/title/duration in,
verdict out, no I/O.

The rules ride inside the task's v2 content brief as an optional
``campaign_rules`` object (see ``content_requirements.normalize_brief_v2``,
which preserves a normalised copy), and ``VideoService._apply_brief_gate``
applies them at cut time behind the ``CAMPAIGN_RULES_ENFORCE`` switch.
"""

from __future__ import annotations

import os
import re
from typing import Any, Dict, List, Optional, Sequence, Tuple

RULE_FIELDS = (
    "goal",
    "must_mention",
    "banned",
    "min_len",
    "max_len",
    "aspect",
    "cta",
    "tone",
    "source",
)

_ASPECTS = {"16:9", "9:16", "1:1"}
_MAX_TERMS = 10


def campaign_rules_enabled() -> bool:
    """Cut-time enforcement switch (default on: rules only exist when a
    brief carries them, so an empty brief is always a no-op)."""
    return os.getenv("CAMPAIGN_RULES_ENFORCE", "on").strip().lower() not in (
        "off",
        "0",
        "false",
        "no",
    )


def _strings(value: Any) -> List[str]:
    if value is None:
        return []
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, (list, tuple)):
        return []
    out = [str(v).lower().strip() for v in value if str(v).strip()]
    return out[:_MAX_TERMS]


def _number(value: Any, default: int, low: int, high: int) -> int:
    try:
        number = int(float(value))  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return default
    return max(low, min(high, number))


def normalize_campaign_rules(data: Any) -> Optional[Dict[str, Any]]:
    """Normalise any mapping into the campaign-rule schema.

    Returns ``None`` when the input carries no enforceable rule at all
    (no must-mention, no banned term, no explicit length bound and no
    aspect), so a brief that never mentioned campaign rules stays a
    no-op. Length bounds only count when the source actually set them —
    Chopify's defaults (15/300) would otherwise reject legitimate clips
    the brief never constrained.
    """
    if not isinstance(data, dict):
        return None
    must_mention = _strings(data.get("must_mention"))
    banned = _strings(data.get("banned"))
    aspect = str(data.get("aspect") or "").strip()
    if aspect not in _ASPECTS:
        aspect = ""
    has_min = data.get("min_len") is not None
    has_max = data.get("max_len") is not None
    if not must_mention and not banned and not aspect and not has_min and not has_max:
        return None
    rules: Dict[str, Any] = {
        "goal": str(data.get("goal") or "").strip()[:500],
        "must_mention": must_mention,
        "banned": banned,
        "min_len": _number(data.get("min_len"), 1, 1, 3600) if has_min else None,
        "max_len": _number(data.get("max_len"), 3600, 1, 3600) if has_max else None,
        "aspect": aspect,
        "cta": str(data.get("cta") or "").strip()[:300],
        "tone": str(data.get("tone") or "").strip()[:300],
        "source": str(data.get("source") or "brief")[:40],
    }
    if (
        rules["min_len"] is not None
        and rules["max_len"] is not None
        and rules["min_len"] > rules["max_len"]
    ):
        rules["min_len"], rules["max_len"] = rules["max_len"], rules["min_len"]
    return rules


def campaign_rules_of(brief: Any) -> Optional[Dict[str, Any]]:
    """The normalised campaign rules a brief carries, or ``None``."""
    if not isinstance(brief, dict):
        return None
    return normalize_campaign_rules(brief.get("campaign_rules"))


# ---------------------------------------------------------------------------
# Deterministic fallback parser (Chopify ``_heuristic_parse``, adapted).
# Used when raw brief text arrives without a model pass; the result is
# labelled so callers can warn that keyword rules, not a model, read it.
# ---------------------------------------------------------------------------


def _clean_phrase(value: str) -> str:
    return re.sub(
        r"^(?:must|include|mention|show|avoid|no|don't|exclude)\s*:\s*",
        "",
        value.strip(),
        flags=re.IGNORECASE,
    ).strip()


def parse_campaign_brief_text(text: str) -> Optional[Dict[str, Any]]:
    """Keyword-parse raw campaign brief text into campaign rules."""
    if not isinstance(text, str) or not text.strip():
        return None
    text = text.strip()[:8000]
    lower = text.lower()
    must: List[str] = []
    for match in re.finditer(r"(?:must|include|mention|show)\s*:?\s*([^.\n]+)", lower):
        must += [
            p
            for p in (_clean_phrase(v) for v in re.split(r"[,/]", match.group(1)))
            if p
        ]
    banned: List[str] = []
    for match in re.finditer(r"(?:avoid|no|don't|exclude)\s*:?\s*([^.\n]+)", lower):
        banned += [
            p
            for p in (_clean_phrase(v) for v in re.split(r"[,/]", match.group(1)))
            if p
        ]
    span = re.search(r"(\d+)\s*[-–]\s*(\d+)\s*s", lower)
    cta = re.search(r"(?:cta|call to action)\s*:?\s*([^\n]+)", lower)
    aspect = re.search(r"\b(9:16|16:9|1:1)\b", text)
    rules = normalize_campaign_rules(
        {
            "goal": text.splitlines()[0] if text.splitlines() else "",
            "must_mention": must[:_MAX_TERMS],
            "banned": banned[:_MAX_TERMS],
            "min_len": span.group(1) if span else None,
            "max_len": span.group(2) if span else None,
            "aspect": aspect.group(1) if aspect else "",
            "cta": cta.group(1) if cta else "",
            "tone": "",
            "source": "heuristic",
        }
    )
    if rules is not None:
        rules["warning"] = "Brief parsed with keyword rules (no model pass)."
    return rules


# ---------------------------------------------------------------------------
# Enforcement (Chopify ``apply_rules`` hard checks, minus its LLM rescore:
# ordering is the picker's job — this layer only keeps or rejects).
# ---------------------------------------------------------------------------


def _phrase_tokens(phrase: str) -> List[str]:
    return [t for t in phrase.lower().split() if len(t) > 2]


def _matches(text: str, phrase: str) -> bool:
    """Chopify's rule: every significant token of the phrase appears."""
    tokens = _phrase_tokens(phrase)
    return bool(tokens) and all(t in text for t in tokens)


def evaluate_segment_against_rules(
    *,
    text: str,
    title: str = "",
    duration: Optional[float] = None,
    aspect: Optional[str] = None,
    rules: Dict[str, Any],
) -> Dict[str, Any]:
    """Verdict for one candidate segment: ``{"ok": bool, "reasons": [...]}``.

    Every violation is listed (not just the first) so the rejection
    record explains itself. ``aspect`` is only checked when both the
    rules and the caller know the target aspect.
    """
    reasons: List[str] = []
    haystack = f"{title}\n{text}".lower()
    for phrase in rules.get("banned", []):
        if phrase and phrase in haystack:
            reasons.append(f"banned term present: {phrase!r}")
    for phrase in rules.get("must_mention", []):
        if phrase and not _matches(haystack, phrase):
            reasons.append(f"must-mention missing: {phrase!r}")
    if duration is not None:
        min_len = rules.get("min_len")
        max_len = rules.get("max_len")
        if min_len is not None and duration < float(min_len):
            reasons.append(
                f"clip too short for the brief: {duration:.1f}s < {min_len}s"
            )
        if max_len is not None and duration > float(max_len):
            reasons.append(
                f"clip too long for the brief: {duration:.1f}s > {max_len}s"
            )
    wanted_aspect = rules.get("aspect") or ""
    if wanted_aspect and aspect and aspect != wanted_aspect:
        reasons.append(f"aspect {aspect} does not match brief aspect {wanted_aspect}")
    return {"ok": not reasons, "reasons": reasons}


def enforce_campaign_rules(
    segments: Sequence[Dict[str, Any]],
    rules: Optional[Dict[str, Any]],
    *,
    target_aspect: Optional[str] = None,
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """Split candidate segment dicts into (kept, rejected).

    Segment dicts use the pipeline's shape (``start_time`` / ``end_time``
    in seconds or ``MM:SS`` strings, ``text``, ``hook_title``). Rejected
    entries are copies carrying ``campaign_rejection`` — the list of
    human-readable reasons — so the caller can record exactly why each
    clip did not ship. Order is preserved in both lists; nothing is
    re-ranked here.
    """
    if not rules:
        return list(segments), []
    kept: List[Dict[str, Any]] = []
    rejected: List[Dict[str, Any]] = []
    for segment in segments:
        start = _seconds(segment.get("start_time"))
        end = _seconds(segment.get("end_time"))
        duration = (
            end - start if start is not None and end is not None and end >= start else None
        )
        verdict = evaluate_segment_against_rules(
            text=str(segment.get("text") or ""),
            title=str(segment.get("hook_title") or segment.get("title") or ""),
            duration=duration,
            aspect=target_aspect,
            rules=rules,
        )
        if verdict["ok"]:
            kept.append(segment)
        else:
            rejected.append(
                {**segment, "campaign_rejection": verdict["reasons"]}
            )
    return kept, rejected


def _seconds(value: Any) -> Optional[float]:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip()
    if not text:
        return None
    try:
        return float(text)
    except ValueError:
        pass
    parts = text.split(":")
    try:
        total = 0.0
        for part in parts:
            total = total * 60 + float(part)
        return total
    except ValueError:
        return None
