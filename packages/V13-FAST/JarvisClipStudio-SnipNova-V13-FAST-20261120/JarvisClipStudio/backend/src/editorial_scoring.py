"""Editorial quality scoring v2: dimension sub-scores with reasons.

This module replaces the single headline selection number, at the
display / API layer, with a breakdown of editorial dimensions. Every
dimension is an *editorial-quality assessment* of the clip as made —
how strong its opening is, whether its story holds together, whether
the rendered picture and sound measure well — derived from evidence
the pipeline already recorded. It is not a forecast of audience
response, and no field, label or reason in this module claims or
implies one.

Evidence policy (in priority order):

1. Measured render facts first: the per-clip QC report stored on the
   clip row (``qc_report`` JSON — resolution, black/frozen frames,
   subject-in-frame sampling, audio RMS, music presence, hook-window
   outcome, cut rate, caption presence and caption hold times) and
   the per-clip plan record (``clip_plan`` JSON — hook decision and
   phase intents).
2. Transcript facts computed here from the stored clip text and
   duration: word count, sentence completeness, speech rate, and the
   share of the clip the plan's context phase covers.
3. The existing transcript-analysis sub-scores, reused as-is and
   labelled ``llm`` (or ``derived`` when blended with a measured
   fact). No new model call is made anywhere in this module:
   hook strength reuses the stored hook sub-score, emotional
   interest reuses the stored engagement sub-score, payoff reuses
   the stored value sub-score, and niche relevance reuses the
   stored relevance score.

Null policy: a dimension with no evidence returns
``{"score": None, "reason": "not measured", "source": None}``.
There are no fabricated defaults. In particular, the legacy
sub-score columns default to 0 in the database, so a clip whose
four sub-scores and total are *all* zero (or absent) is treated as
having no transcript-analysis evidence at all, rather than four
genuine zero scores.

Campaign compliance is optional by design: the dimension is
*absent* from the breakdown (not null) unless the clip carries
compliance data — a ``campaign_compliance`` / ``compliance``
record, or a QC check whose name mentions the campaign. The
compliance system itself lives elsewhere; this module only reads
a finished verdict when one is present.

Aggregate: a weighted mean of the dimensions that have a score,
with the weights below renormalised over the available dimensions
(campaign compliance joins the mean, with its weight, only when
present). The legacy selection total is never overwritten: it is
carried alongside as ``legacy_selection_total`` for the consumers
that already use it (selection ranking and the learning engine
read the stored columns directly and are untouched by this module).
"""

from __future__ import annotations

import json
import re
from typing import Any, Optional

# --- Vocabulary -----------------------------------------------------------

#: Dimensions that are always present in the breakdown, in display order.
CORE_DIMENSIONS = (
    "hook_strength",
    "story_coherence",
    "context_preservation",
    "visual_quality",
    "audio_quality",
    "caption_accuracy",
    "pacing",
    "emotional_interest",
    "payoff",
    "niche_relevance",
)

#: Optional dimension, present only when compliance data exists.
CAMPAIGN_DIMENSION = "campaign_compliance"

DIMENSION_LABELS = {
    "hook_strength": "Hook strength",
    "story_coherence": "Story coherence",
    "context_preservation": "Context preservation",
    "visual_quality": "Visual quality",
    "audio_quality": "Audio quality",
    "caption_accuracy": "Caption accuracy",
    "pacing": "Pacing",
    "emotional_interest": "Emotional interest",
    "payoff": "Payoff",
    "niche_relevance": "Niche relevance",
    "campaign_compliance": "Campaign compliance",
}

#: Aggregate weights. Without campaign data they sum to 100; when a
#: campaign verdict is present its weight joins and the mean is
#: renormalised over the scored dimensions either way.
WEIGHTS = {
    "hook_strength": 14.0,
    "story_coherence": 10.0,
    "context_preservation": 10.0,
    "visual_quality": 12.0,
    "audio_quality": 10.0,
    "caption_accuracy": 9.0,
    "pacing": 10.0,
    "emotional_interest": 8.0,
    "payoff": 9.0,
    "niche_relevance": 8.0,
    "campaign_compliance": 10.0,
}

NOT_MEASURED_REASON = "not measured"

_STATUS_POINTS = {"pass": 100.0, "warn": 50.0, "fail": 0.0}


# --- Small helpers ----------------------------------------------------------


def _parse_json(value: Any) -> dict:
    if isinstance(value, dict):
        return value
    if isinstance(value, str) and value.strip():
        try:
            parsed = json.loads(value)
        except ValueError:
            return {}
        return parsed if isinstance(parsed, dict) else {}
    return {}


def _num(value: Any) -> Optional[float]:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _dim(score: Optional[float], reason: str, source: Optional[str]) -> dict:
    if score is None:
        return {"score": None, "reason": NOT_MEASURED_REASON, "source": None}
    return {
        "score": int(round(max(0.0, min(100.0, score)))),
        "reason": reason[:240],
        "source": source,
    }


def _not_measured() -> dict:
    return _dim(None, NOT_MEASURED_REASON, None)


def _blend(parts: list[tuple[float, float]]) -> Optional[float]:
    """Weighted mean of (score, weight) parts; None when empty."""
    usable = [(s, w) for s, w in parts if s is not None and w > 0]
    if not usable:
        return None
    return sum(s * w for s, w in usable) / sum(w for _, w in usable)


def _source_for(kinds: set[str]) -> Optional[str]:
    """Collapse the evidence kinds used into the public source label."""
    if not kinds:
        return None
    if len(kinds) == 1:
        return next(iter(kinds))
    return "derived"


# --- Evidence extraction ----------------------------------------------------


def _qc_checks(clip: dict) -> dict[str, dict]:
    report = _parse_json(clip.get("qc_report"))
    checks: dict[str, dict] = {}
    for entry in report.get("checks") or []:
        if isinstance(entry, dict) and entry.get("name"):
            checks[str(entry["name"])] = entry
    return checks


def _plan(clip: dict) -> dict:
    return _parse_json(clip.get("clip_plan"))


def _llm_subscores(clip: dict) -> Optional[dict[str, float]]:
    """The stored transcript-analysis sub-scores, or None if absent.

    Supports both the flat clip-row shape (``hook_score`` ...) and a
    nested segment shape (``virality: {hook_score: ...}``). The
    all-zero sentinel (database defaults on clips that never got an
    analysis) counts as absent — see the module docstring.
    """
    nested = clip.get("virality") if isinstance(clip.get("virality"), dict) else {}
    raw: dict[str, Any] = {}
    for field in ("hook_score", "engagement_score", "value_score", "shareability_score"):
        raw[field] = clip.get(field, nested.get(field))
    total = clip.get("virality_score", nested.get("total_score"))
    values = {field: _num(value) for field, value in raw.items()}
    if all(v is None or v == 0 for v in values.values()) and (
        _num(total) is None or _num(total) == 0
    ):
        return None
    return {field: float(v or 0.0) for field, v in values.items()}


def _text_facts(clip: dict) -> dict:
    text = str(clip.get("text") or "").strip()
    words = text.split()
    sentences = [s for s in re.findall(r"[^.!?]+[.!?]+", text) if len(s.split()) >= 2]
    return {
        "text": text,
        "word_count": len(words),
        "complete_sentences": len(sentences),
        "ends_complete": bool(text) and text.rstrip("\"'”’) ").endswith((".", "!", "?", "…")),
        "duration": _num(clip.get("duration")) or 0.0,
    }


def _phases(plan: dict) -> list[dict]:
    return [p for p in (plan.get("phases") or []) if isinstance(p, dict)]


def _check_points(check: Optional[dict], *, warn: float = 50.0) -> Optional[float]:
    if not check:
        return None
    status = str(check.get("status") or "")
    if status == "skip":
        return None
    if status == "warn":
        return warn
    return _STATUS_POINTS.get(status)


# --- Dimensions ---------------------------------------------------------------


def _hook_strength(clip: dict, checks: dict, subs: Optional[dict]) -> dict:
    parts: list[tuple[float, float]] = []
    kinds: set[str] = set()
    notes: list[str] = []
    if subs is not None:
        scaled = subs["hook_score"] * 4.0
        parts.append((scaled, 0.7))
        kinds.add("llm")
        notes.append(f"transcript-analysis hook sub-score {subs['hook_score']:g}/25")
    hook_check = checks.get("hook_window")
    if hook_check and hook_check.get("status") != "skip":
        evidence = hook_check.get("evidence") or {}
        hook_status = str(evidence.get("hook_status") or "")
        if hook_check.get("status") == "pass":
            points = 100.0
        elif hook_status == "kept_original_context":
            points = 55.0
        elif hook_status == "no_hook":
            points = 20.0
        else:
            points = 40.0
        parts.append((points, 0.3))
        kinds.add("measured")
        at = evidence.get("hook_timestamp")
        notes.append(
            f"QC hook window {hook_check.get('status')}"
            + (f" (hook at {float(at):.1f}s)" if _num(at) is not None else "")
            + (f" [{hook_status}]" if hook_status else "")
        )
    score = _blend(parts)
    if score is None:
        return _not_measured()
    return _dim(score, "; ".join(notes), _source_for(kinds))


def _story_coherence(clip: dict, facts: dict, plan: dict) -> dict:
    parts: list[tuple[float, float]] = []
    notes: list[str] = []
    words = facts["word_count"]
    if words >= 3:
        if words < 8:
            text_score = 30.0
        elif facts["complete_sentences"] >= 2 and facts["ends_complete"]:
            text_score = 90.0
        elif facts["complete_sentences"] >= 1 and facts["ends_complete"]:
            text_score = 75.0
        elif facts["complete_sentences"] >= 1:
            text_score = 60.0
        elif facts["ends_complete"]:
            text_score = 55.0
        else:
            text_score = 40.0
        parts.append((text_score, 0.6))
        notes.append(
            f"{words} transcript words, {facts['complete_sentences']} complete "
            f"sentence(s), ends {'complete' if facts['ends_complete'] else 'mid-thought'}"
        )
    phases = _phases(plan)
    if phases:
        intents = [str(p.get("intent") or "") for p in phases]
        phase_score = 0.0
        if "hook" in intents:
            phase_score += 20.0
        if any(i in intents for i in ("context", "build")):
            phase_score += 40.0
        if any(i in intents for i in ("payoff", "conclusion")):
            phase_score += 40.0
        parts.append((phase_score, 0.4))
        notes.append(f"edit-plan phases cover: {', '.join(i for i in intents if i)}")
    score = _blend(parts)
    if score is None:
        return _not_measured()
    return _dim(score, "; ".join(notes), "derived")


def _context_preservation(clip: dict, checks: dict, facts: dict, plan: dict) -> dict:
    parts: list[tuple[float, float]] = []
    kinds: set[str] = set()
    notes: list[str] = []
    hook_check = checks.get("hook_window")
    if hook_check and hook_check.get("status") != "skip":
        hook_status = str((hook_check.get("evidence") or {}).get("hook_status") or "")
        if hook_status == "kept_original_context":
            parts.append((90.0, 0.5))
            kinds.add("measured")
            notes.append("QC records the hook was deliberately kept in place to preserve context")
    phases = _phases(plan)
    duration = facts["duration"]
    context_share = 0.0
    if phases and duration > 0:
        context_seconds = sum(
            max(0.0, (_num(p.get("end_offset")) or 0.0) - (_num(p.get("start_offset")) or 0.0))
            for p in phases
            if p.get("intent") == "context"
        )
        context_share = context_seconds / duration
        if context_share > 0:
            points = 85.0 if context_share >= 0.4 else 70.0 if context_share >= 0.25 else 55.0
            parts.append((points, 0.3))
            kinds.add("derived")
            notes.append(f"context phase covers {context_share:.0%} of the clip")
    words = facts["word_count"]
    if words >= 5:
        points = 75.0 if words >= 50 else 65.0 if words >= 30 else 50.0 if words >= 15 else 35.0
        parts.append((points, 0.2))
        kinds.add("derived")
        notes.append(f"{words} transcript words of context retained")
    score = _blend(parts)
    if score is None:
        return _not_measured()
    return _dim(score, "; ".join(notes), _source_for(kinds))


def _visual_quality(checks: dict) -> dict:
    names = ("resolution", "black_frames", "frozen_frame", "subject_in_frame")
    parts: list[tuple[float, float]] = []
    notes: list[str] = []
    for name in names:
        points = _check_points(checks.get(name))
        if points is None:
            continue
        parts.append((points, 1.0))
        check = checks[name]
        evidence = check.get("evidence") or {}
        extra = ""
        if name == "resolution" and evidence.get("width"):
            extra = f" ({evidence.get('width')}x{evidence.get('height')})"
        if name == "subject_in_frame" and "found" in evidence:
            extra = f" (face in {evidence.get('found')}/{evidence.get('measured')} samples)"
        notes.append(f"{name} {check.get('status')}{extra}")
    score = _blend(parts)
    if score is None:
        return _not_measured()
    return _dim(score, "QC " + "; ".join(notes), "measured")


def _audio_quality(checks: dict) -> dict:
    parts: list[tuple[float, float]] = []
    notes: list[str] = []
    audio = checks.get("audio")
    points = _check_points(audio, warn=35.0)
    if points is not None and audio:
        parts.append((points, 1.0))
        rms = (audio.get("evidence") or {}).get("rms_db")
        notes.append(
            f"audio {audio.get('status')}"
            + (f" (overall RMS {float(rms):.1f} dBFS)" if _num(rms) is not None else "")
        )
    music = checks.get("music")
    points = _check_points(music, warn=30.0)
    if points is not None and music:
        parts.append((points, 0.7))
        notes.append(f"music bed {music.get('status')}")
    score = _blend(parts)
    if score is None:
        return _not_measured()
    return _dim(score, "QC " + "; ".join(notes), "measured")


def _caption_accuracy(checks: dict) -> dict:
    parts: list[tuple[float, float]] = []
    notes: list[str] = []
    presence = checks.get("caption_presence")
    points = _check_points(presence, warn=10.0)
    if points is not None and presence:
        parts.append((points, 1.0))
        chunks = (presence.get("evidence") or {}).get("chunks")
        notes.append(
            f"caption presence {presence.get('status')}"
            + (f" ({chunks} chunks burned)" if chunks is not None else "")
        )
    hold = checks.get("caption_hold")
    points = _check_points(hold, warn=40.0)
    if points is not None and hold:
        parts.append((points, 1.0))
        evidence = hold.get("evidence") or {}
        if evidence.get("max_hold_seconds") is not None:
            notes.append(
                f"longest caption hold {float(evidence['max_hold_seconds']):.1f}s "
                f"vs {float(evidence.get('cap_seconds') or 0):.1f}s cap"
            )
        else:
            notes.append(f"caption hold {hold.get('status')}")
    score = _blend(parts)
    if score is None:
        return _not_measured()
    notes.append("presence/pacing measured; caption wording was not compared to the transcript")
    return _dim(score, "QC " + "; ".join(notes), "measured")


def _pacing(checks: dict, facts: dict) -> dict:
    parts: list[tuple[float, float]] = []
    kinds: set[str] = set()
    notes: list[str] = []
    cut = checks.get("cut_rate")
    points = _check_points(cut, warn=45.0)
    if points is not None and cut:
        parts.append((points, 1.0))
        kinds.add("measured")
        evidence = cut.get("evidence") or {}
        if evidence.get("cuts_per_min") is not None:
            band = evidence.get("band") or [None, None]
            notes.append(
                f"cut rate {float(evidence['cuts_per_min']):.0f}/min vs profile band "
                f"{band[0]}-{band[1]}/min ({cut.get('status')})"
            )
        else:
            notes.append(f"cut rate {cut.get('status')}")
    hold = checks.get("caption_hold")
    points = _check_points(hold, warn=40.0)
    if points is not None and hold:
        parts.append((points, 0.6))
        kinds.add("measured")
        notes.append(f"caption hold {hold.get('status')}")
    words, duration = facts["word_count"], facts["duration"]
    if words >= 5 and duration > 0:
        wpm = words / duration * 60.0
        rate = 95.0 if 130 <= wpm <= 190 else 70.0 if 110 <= wpm < 130 or 190 < wpm <= 220 else 45.0 if 80 <= wpm < 110 or 220 < wpm <= 260 else 25.0
        parts.append((rate, 0.8))
        kinds.add("derived")
        notes.append(f"speech rate {wpm:.0f} words/min over {duration:.0f}s")
    score = _blend(parts)
    if score is None:
        return _not_measured()
    return _dim(score, "; ".join(notes), _source_for(kinds))


def _emotional_interest(subs: Optional[dict]) -> dict:
    if subs is None:
        return _not_measured()
    scaled = subs["engagement_score"] * 4.0
    return _dim(
        scaled,
        f"transcript-analysis engagement sub-score {subs['engagement_score']:g}/25, "
        "reused as the emotional-interest assessment",
        "llm",
    )


def _payoff(subs: Optional[dict], plan: dict, facts: dict) -> dict:
    parts: list[tuple[float, float]] = []
    kinds: set[str] = set()
    notes: list[str] = []
    if subs is not None:
        parts.append((subs["value_score"] * 4.0, 0.5))
        kinds.add("llm")
        notes.append(f"transcript-analysis value sub-score {subs['value_score']:g}/25")
    duration = facts["duration"]
    payoff_phases = [p for p in _phases(plan) if p.get("intent") == "payoff"]
    if payoff_phases and duration > 0:
        end_share = max((_num(p.get("end_offset")) or 0.0) for p in payoff_phases) / duration
        points = 90.0 if end_share >= 0.8 else 70.0 if end_share >= 0.6 else 50.0
        parts.append((points, 0.5))
        kinds.add("derived")
        notes.append(f"edit plan places a payoff phase ending at {end_share:.0%} of the clip")
    score = _blend(parts)
    if score is None:
        return _not_measured()
    return _dim(score, "; ".join(notes), _source_for(kinds))


def _niche_relevance(clip: dict, plan: dict) -> dict:
    relevance = _num(clip.get("relevance_score"))
    if relevance is None:
        return _not_measured()
    niche = (
        clip.get("niche_override")
        or clip.get("niche_profile")
        or plan.get("niche")
        or clip.get("niche")
    )
    note = f"segment relevance score {relevance:.2f} from transcript analysis"
    if niche:
        note += f"; clip rendered for niche '{niche}'"
    return _dim(relevance * 100.0, note, "llm")


def _campaign_compliance(clip: dict, checks: dict) -> Optional[dict]:
    """The optional dimension: a result only when compliance data exists."""
    raw = clip.get("campaign_compliance", clip.get("compliance"))
    if isinstance(raw, dict):
        score = _num(raw.get("score"))
        if score is not None:
            reason = str(raw.get("reason") or raw.get("detail") or "campaign brief check recorded a score")
            return _dim(score, reason, "derived")
        status = str(raw.get("status") or "")
        if status in _STATUS_POINTS or status == "warn":
            reason = str(raw.get("reason") or raw.get("detail") or f"campaign brief check: {status}")
            return _dim(_check_points({"status": status}), reason, "derived")
        return None
    if _num(raw) is not None:
        return _dim(_num(raw), "campaign brief check recorded a score", "derived")
    for name, check in checks.items():
        if "campaign" in name or "compliance" in name:
            points = _check_points(check)
            if points is None:
                return None
            return _dim(points, f"QC {name}: {check.get('detail') or check.get('status')}", "measured")
    return None


# --- Public entry point -------------------------------------------------------


def build_editorial_quality(clip: dict) -> dict:
    """Build the editorial-quality breakdown for one stored clip dict.

    ``clip`` is the repository clip shape (flat sub-score fields plus
    ``qc_report`` / ``clip_plan`` JSON strings or dicts). Pure and
    defensive: malformed stored JSON degrades the affected dimensions
    to "not measured" instead of raising.
    """
    clip = clip if isinstance(clip, dict) else {}
    checks = _qc_checks(clip)
    plan = _plan(clip)
    subs = _llm_subscores(clip)
    facts = _text_facts(clip)

    dimensions: dict[str, dict] = {
        "hook_strength": _hook_strength(clip, checks, subs),
        "story_coherence": _story_coherence(clip, facts, plan),
        "context_preservation": _context_preservation(clip, checks, facts, plan),
        "visual_quality": _visual_quality(checks),
        "audio_quality": _audio_quality(checks),
        "caption_accuracy": _caption_accuracy(checks),
        "pacing": _pacing(checks, facts),
        "emotional_interest": _emotional_interest(subs),
        "payoff": _payoff(subs, plan, facts),
        "niche_relevance": _niche_relevance(clip, plan),
    }
    campaign = _campaign_compliance(clip, checks)
    if campaign is not None:
        dimensions[CAMPAIGN_DIMENSION] = campaign

    labelled = {
        key: {"label": DIMENSION_LABELS[key], **value}
        for key, value in dimensions.items()
    }
    scored = [
        (float(value["score"]), WEIGHTS[key])
        for key, value in dimensions.items()
        if value["score"] is not None
    ]
    aggregate = None
    if scored:
        aggregate = int(round(sum(s * w for s, w in scored) / sum(w for _, w in scored)))

    legacy_total = _num(clip.get("virality_score"))
    if legacy_total is None and isinstance(clip.get("virality"), dict):
        legacy_total = _num(clip["virality"].get("total_score"))

    return {
        "label": "Editorial quality",
        "note": (
            "Editorial quality assessment only, based on the evidence "
            "cited for each dimension."
        ),
        "aggregate": aggregate,
        "dimensions": labelled,
        "scored_dimensions": len(scored),
        "legacy_selection_total": int(legacy_total) if legacy_total is not None else None,
    }
