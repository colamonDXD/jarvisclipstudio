"""Content Requirements & Clip Targeting — pure deterministic core.

A task may carry a campaign brief (schema version 2): typed requirements
plus allowed source ranges. This module owns everything about the brief
that can be decided without a model or a database: timecode parsing,
validation and v1->v2 normalisation, range algebra (the hard-enforcement
helpers), deterministic term extraction, exact/fuzzy transcript search,
focus scoring, candidate ranking tiers, typed post-render compliance
evaluation, and the deterministic paste-a-brief extractor.

Briefs that arrive as screenshots (the common real-world form) add three
more v2 fields, all optional and additive: ``excluded_ranges`` (source
ranges that must never appear — hard-enforced exactly like allowed
ranges, with the opposite sign), ``flag_points`` (single timestamps
carrying a review category — licence plates, branding, uncleared music,
sensitive topics — stamped onto any clip that contains them as
needs-review, never auto-fixed), and ``key_timestamps`` (the brief's own
named moments, which join the candidate pool as supplementary picks).

The LLM focus/extraction passes live in ``content_requirements_llm.py``;
their judgements are always labelled model judgements and never replace
the deterministic transcript evidence computed here.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional, Tuple

BRIEF_VERSION = 2
MAX_REQUIREMENTS = 10
MAX_RANGES = 8
MAX_REQUIREMENT_CHARS = 500
MAX_FLAG_POINTS = 40
MAX_KEY_TIMESTAMPS = 20
MAX_POINT_NOTE_CHARS = 200

# Review-flag categories (brief-image lane): what a flag point asks a
# human to look at. Flags never change a render; they mark the clip
# needs-review in the compliance report.
FLAG_CATEGORIES = (
    "licence_plate",
    "branding",
    "copyright_music",
    "sensitive_topic",
    "alcohol",
    "review",
    "other",
)

REQUIREMENT_TYPES = (
    "must_mention",
    "topic_focus",
    "structure_hook",
    "structure_cta",
    "visual_presence",
    "source_restriction",
    "caption_rule",
    "info_only",
)

# Focus thresholds (design §3.4). One constants block so the tuning round
# after the first real campaign run touches exactly one place.
FOCUS_MAJOR_MIN_HIT_LINES = 2
FOCUS_MAJOR_SPAN_SHARE = 0.25
FOCUS_MAJOR_MANY_LINES = 3
FOCUS_GREY_MIN_SHARE = 0.10
CLUSTER_GAP_SECONDS = 20.0
MIN_FRAGMENT_SECONDS = 0.5

CTA_GENERIC_PHRASES = ("play", "download", "use code", "link in bio", "check out")

RangeTuple = Tuple[float, float]  # end may be math.inf for "to end of source"


# ---------------------------------------------------------------------------
# Timecodes and ranges
# ---------------------------------------------------------------------------


def format_timecode(seconds: float) -> str:
    """Display label: HH:MM:SS at/above an hour, else MM:SS."""
    total = max(0, int(round(float(seconds))))
    hours, remainder = divmod(total, 3600)
    minutes, secs = divmod(remainder, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{secs:02d}"
    return f"{minutes:02d}:{secs:02d}"


def parse_timecode(value: Any) -> float:
    """Parse one user-typed timecode into seconds (strict, named errors).

    Accepted: raw seconds (number or numeric string), ``m:ss`` with
    unbounded minutes, ``h:mm:ss``, and a fractional tail on the last
    component only. Anything else raises ``ValueError`` — a typo must
    never silently become 0 (the existing timeline parser's failure
    mode this function deliberately does not reuse).
    """
    if isinstance(value, bool):
        raise ValueError("time must be a number of seconds or an m:ss / h:mm:ss timecode")
    if isinstance(value, (int, float)):
        number = float(value)
        if not math.isfinite(number) or number < 0:
            raise ValueError("time must be a finite, non-negative number of seconds")
        return number
    if not isinstance(value, str):
        raise ValueError("time must be a number of seconds or an m:ss / h:mm:ss timecode")
    text = value.strip()
    if not text:
        raise ValueError("time is empty")
    if ":" not in text:
        try:
            number = float(text)
        except ValueError:
            raise ValueError(f"could not parse time {value!r}") from None
        if not math.isfinite(number) or number < 0:
            raise ValueError("time must be a finite, non-negative number of seconds")
        return number
    parts = text.split(":")
    if len(parts) > 3:
        raise ValueError(f"time {value!r} has too many ':' separators")
    for component in parts[:-1]:
        if not component.isdigit():
            raise ValueError(f"time {value!r} has a non-numeric component {component!r}")
    last = parts[-1]
    if not re.fullmatch(r"\d+(\.\d+)?", last):
        raise ValueError(f"time {value!r} has a non-numeric component {last!r}")
    numbers = [int(p) for p in parts[:-1]] + [float(last)]
    if len(numbers) == 2:
        minutes, seconds = numbers
        if seconds >= 60:
            raise ValueError(f"time {value!r}: seconds component must be under 60")
        return minutes * 60 + seconds
    hours, minutes, seconds = numbers
    if minutes >= 60 or seconds >= 60:
        raise ValueError(f"time {value!r}: minutes and seconds must be under 60")
    return hours * 3600 + minutes * 60 + seconds


def merge_ranges(ranges: Iterable[RangeTuple]) -> List[RangeTuple]:
    """Sort and merge overlapping or touching (gap < 0.05s) ranges."""
    merged: List[RangeTuple] = []
    for start, end in sorted(ranges):
        if end <= start:
            continue
        if merged and start <= merged[-1][1] + 0.05:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((float(start), float(end)))
    return merged


def _parse_one_range(item: Any, source_duration: Optional[float]) -> Dict[str, Any]:
    if not isinstance(item, dict):
        raise ValueError("each allowed range must be an object with start and end")
    raw_start = item.get("start", item.get("start_seconds"))
    if raw_start is None:
        raise ValueError("each allowed range needs a start")
    start = parse_timecode(raw_start)
    raw_end = item.get("end", item.get("end_seconds"))
    end: Optional[float]
    if raw_end is None or (isinstance(raw_end, str) and raw_end.strip().lower() in ("", "end")):
        end = None
    else:
        end = parse_timecode(raw_end)
    if end is not None and end <= start:
        raise ValueError("range end must be after its start")
    if source_duration is not None:
        if start >= source_duration:
            raise ValueError("range start is beyond the end of the source video")
        if end is not None and end > source_duration:
            end = float(source_duration)
    start_label = raw_start if isinstance(raw_start, str) and raw_start.strip() else format_timecode(start)
    if end is None:
        end_label = None
    elif isinstance(raw_end, str) and raw_end.strip():
        end_label = raw_end.strip()
    else:
        end_label = format_timecode(end)
    return {
        "start_seconds": float(start),
        "end_seconds": None if end is None else float(end),
        "start_label": str(start_label),
        "end_label": end_label,
    }


def parse_allowed_ranges(
    value: Any, *, source_duration: Optional[float] = None
) -> List[Dict[str, Any]]:
    """Validate + normalise the allowed-ranges list (sorted, merged)."""
    if value is None:
        return []
    if not isinstance(value, list):
        raise ValueError("allowed_ranges must be a list")
    if len(value) > MAX_RANGES:
        raise ValueError(f"at most {MAX_RANGES} allowed ranges are supported")
    parsed = [_parse_one_range(item, source_duration) for item in value]
    tuples = [
        (r["start_seconds"], math.inf if r["end_seconds"] is None else r["end_seconds"])
        for r in parsed
    ]
    normalised: List[Dict[str, Any]] = []
    for start, end in merge_ranges(tuples):
        match = next(
            (r for r in parsed if r["start_seconds"] == start),
            None,
        )
        normalised.append(
            {
                "start_seconds": float(start),
                "end_seconds": None if math.isinf(end) else float(end),
                "start_label": match["start_label"] if match else format_timecode(start),
                "end_label": (
                    None
                    if math.isinf(end)
                    else (match["end_label"] if match and match["end_seconds"] == end else format_timecode(end))
                ),
            }
        )
    return normalised


def parse_excluded_ranges(
    value: Any, *, source_duration: Optional[float] = None
) -> List[Dict[str, Any]]:
    """Validate + normalise the excluded-ranges list (sorted, merged).

    Same item shape and parsing as allowed ranges; the semantics are
    the mirror image — footage inside these ranges must never be used.
    """
    if value is None:
        return []
    if not isinstance(value, list):
        raise ValueError("excluded_ranges must be a list")
    if len(value) > MAX_RANGES:
        raise ValueError(f"at most {MAX_RANGES} excluded ranges are supported")
    parsed = [_parse_one_range(item, source_duration) for item in value]
    tuples = [
        (r["start_seconds"], math.inf if r["end_seconds"] is None else r["end_seconds"])
        for r in parsed
    ]
    normalised: List[Dict[str, Any]] = []
    for start, end in merge_ranges(tuples):
        match = next(
            (r for r in parsed if r["start_seconds"] == start),
            None,
        )
        normalised.append(
            {
                "start_seconds": float(start),
                "end_seconds": None if math.isinf(end) else float(end),
                "start_label": match["start_label"] if match else format_timecode(start),
                "end_label": (
                    None
                    if math.isinf(end)
                    else (match["end_label"] if match and match["end_seconds"] == end else format_timecode(end))
                ),
            }
        )
    return normalised


def allowed_range_tuples(brief_or_ranges: Any) -> List[RangeTuple]:
    """Allowed ranges as (start, end) tuples; open ends become infinity."""
    if brief_or_ranges is None:
        return []
    ranges = (
        brief_or_ranges.get("allowed_ranges")
        if isinstance(brief_or_ranges, dict)
        else brief_or_ranges
    )
    tuples: List[RangeTuple] = []
    for item in ranges or []:
        if isinstance(item, dict):
            start = float(item.get("start_seconds", item.get("start", 0)) or 0)
            end_raw = item.get("end_seconds", item.get("end"))
            end = math.inf if end_raw is None else float(end_raw)
        else:
            start, end = float(item[0]), float(item[1])
        if end > start:
            tuples.append((start, end))
    return merge_ranges(tuples)


def excluded_range_tuples(brief_or_ranges: Any) -> List[RangeTuple]:
    """Excluded ranges as (start, end) tuples; open ends become infinity."""
    if brief_or_ranges is None:
        return []
    ranges = (
        brief_or_ranges.get("excluded_ranges")
        if isinstance(brief_or_ranges, dict)
        else brief_or_ranges
    )
    tuples: List[RangeTuple] = []
    for item in ranges or []:
        if isinstance(item, dict):
            start = float(item.get("start_seconds", item.get("start", 0)) or 0)
            end_raw = item.get("end_seconds", item.get("end"))
            end = math.inf if end_raw is None else float(end_raw)
        else:
            start, end = float(item[0]), float(item[1])
        if end > start:
            tuples.append((start, end))
    return merge_ranges(tuples)


# ---------------------------------------------------------------------------
# Range algebra — the hard-enforcement helpers (design §2.5)
# ---------------------------------------------------------------------------


def intersect_ranges(
    ranges: Iterable[RangeTuple], allowed: List[RangeTuple]
) -> List[RangeTuple]:
    """Set intersection of keep ranges with the allowed ranges.

    Fragments shorter than 0.5s are dropped (mirrors the keep-range
    floor in build_clip_keep_ranges). Output is sorted and merged.
    """
    if not allowed:
        return []
    pieces: List[RangeTuple] = []
    for start, end in ranges or []:
        for a_start, a_end in allowed:
            lo, hi = max(float(start), a_start), min(float(end), a_end)
            if hi - lo >= MIN_FRAGMENT_SECONDS:
                pieces.append((lo, hi))
    return merge_ranges(pieces)


def ranges_within(
    ranges: Iterable[RangeTuple], allowed: List[RangeTuple], tol: float = 0.05
) -> Tuple[bool, List[List[float]]]:
    """Audit form: (all_within, violations) for rendered ranges."""
    violations: List[List[float]] = []
    for start, end in ranges or []:
        covered = any(
            start >= a_start - tol and end <= a_end + tol for a_start, a_end in allowed
        )
        if not covered:
            violations.append([float(start), float(end)])
    return (not violations, violations)


def clamp_segment_to_allowed(
    start: float, end: float, allowed: List[RangeTuple]
) -> List[RangeTuple]:
    """In-range pieces of one candidate span (caller keeps the longest)."""
    return intersect_ranges([(float(start), float(end))], allowed)


def subtract_ranges(
    ranges: Iterable[RangeTuple], excluded: List[RangeTuple]
) -> List[RangeTuple]:
    """Keep-range pieces with the excluded ranges cut out.

    Fragments shorter than 0.5s are dropped (same floor as
    intersect_ranges). Output is sorted and merged. With no exclusions
    the input passes through normalised but otherwise untouched.
    """
    pieces: List[RangeTuple] = []
    for start, end in ranges or []:
        spans = [(float(start), float(end))]
        for x_start, x_end in excluded or []:
            remaining: List[RangeTuple] = []
            for s, e in spans:
                if x_end <= s or x_start >= e:
                    remaining.append((s, e))
                    continue
                if x_start > s:
                    remaining.append((s, x_start))
                if x_end < e:
                    remaining.append((x_end, e))
            spans = remaining
            if not spans:
                break
        pieces.extend(
            (s, e) for s, e in spans if e - s >= MIN_FRAGMENT_SECONDS
        )
    return merge_ranges(pieces)


def clamp_segment_outside_excluded(
    start: float, end: float, excluded: List[RangeTuple]
) -> List[RangeTuple]:
    """Surviving pieces of one candidate span after exclusions are cut.

    Caller keeps the longest piece, mirroring clamp_segment_to_allowed.
    A span that merely touches an exclusion boundary survives whole.
    """
    return subtract_ranges([(float(start), float(end))], excluded)


def excluded_overlap_pieces(
    ranges: Iterable[RangeTuple], excluded: List[RangeTuple]
) -> List[List[float]]:
    """Audit form: the actual overlap pieces between ranges and the
    excluded ranges (empty when the ranges are clean)."""
    pieces: List[List[float]] = []
    for start, end in ranges or []:
        for x_start, x_end in excluded or []:
            lo, hi = max(float(start), x_start), min(float(end), x_end)
            if hi > lo:
                pieces.append([float(lo), float(hi)])
    return pieces


def localize_allowed_ranges(
    allowed: List[RangeTuple], time_map: Any
) -> List[RangeTuple]:
    """Re-base source-time allowed ranges onto a stitched-sections timeline.

    Uses the time_map's own range conversion (intersections per span, so
    footage between downloaded sections never becomes allowed), falling
    back to endpoint conversion for map-like objects.
    """
    if time_map is None:
        return list(allowed)
    spans = getattr(time_map, "spans", None)
    if spans:
        local: List[RangeTuple] = []
        for a_start, a_end in allowed:
            for span in spans:
                lo = max(a_start, float(span.original_start))
                hi = min(a_end, float(span.original_end))
                if hi - lo >= MIN_FRAGMENT_SECONDS:
                    local.append(
                        (
                            float(span.local_start) + (lo - float(span.original_start)),
                            float(span.local_start) + (hi - float(span.original_start)),
                        )
                    )
        return merge_ranges(local)
    converter = getattr(time_map, "to_local_ranges", None)
    if callable(converter):
        return merge_ranges([(float(s), float(e)) for s, e in converter(allowed)])
    return list(allowed)


# ---------------------------------------------------------------------------
# Requirement classification helpers (deterministic)
# ---------------------------------------------------------------------------

_MAJOR_PATTERNS = (
    r"major\s+focus",
    r"main\s+(focus|topic)",
    r"major\s+topic",
    r"clearly\s+mention",
    r"must\s+mention",
    r"primary\s+focus",
    r"central\s+(focus|topic|theme)",
)
_PRESENTATION_PATTERNS = (
    r"caption",
    r"overlay",
    r"on[-\s]screen",
    r"hashtag",
    r"subtitle",
    r"title\s+card",
    r"thumbnail\s+text",
)


def requires_major_focus(text: str) -> bool:
    lowered = (text or "").lower()
    return any(re.search(p, lowered) for p in _MAJOR_PATTERNS)


def is_presentation_text(text: str) -> bool:
    lowered = (text or "").lower()
    return any(re.search(p, lowered) for p in _PRESENTATION_PATTERNS)


# ---------------------------------------------------------------------------
# Term extraction (design §3.1)
# ---------------------------------------------------------------------------

_STOPWORDS = {
    "content", "part", "moments", "moment", "video", "clip", "clips",
    "streamer", "he", "she", "they", "it", "the", "a", "an", "of", "in",
    "on", "about", "where", "when", "that", "this", "with", "for",
    "discusses", "discuss", "talks", "talk", "mentions", "mention",
    "find", "show", "shows", "part", "section", "stream",
}
_BOILERPLATE_RES = (
    r"must\s+be\s+a\s+major\s+focus(\s+of\s+the\s+content)?",
    r"must\s+(clearly\s+)?mention",
    r"must\s+be\s+mentioned",
    r"find\s+(moments\s+|the\s+part\s+)?where\s+(he|she|they|the\s+streamer)\s+(talks\s+about|discusses|mentions)",
    r"find\s+(moments|the\s+part)",
    r"only\s+select\s+moments\s+from",
    r"the\s+clip\s+must",
    r"the\s+content\s+must",
    r"talks\s+about",
    r"discusses",
)


def _split_camel(token: str) -> str:
    return re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", token)


def _clean_phrase(text: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9' ]+", " ", text.lower())).strip()


def extract_terms(requirement_text: str) -> List[Dict[str, str]]:
    """Deterministic search terms for one requirement's text.

    Priority: quoted phrases, hashtags, capitalised mid-sentence
    sequences; if none of those fire, boilerplate-stripped topic
    residue. An empty result means a judgement-only requirement.
    """
    text = requirement_text or ""
    terms: List[Dict[str, str]] = []
    seen: set = set()

    def add(phrase: str, source: str) -> None:
        cleaned = _clean_phrase(phrase)
        if cleaned and cleaned not in seen and cleaned not in _STOPWORDS:
            seen.add(cleaned)
            terms.append({"phrase": cleaned, "source": source})

    for match in re.findall(r"[\"“]([^\"”]{1,80})[\"”]", text):
        add(match, "quoted")
    for match in re.findall(r"'([^']{2,80})'", text):
        add(match, "quoted")
    for match in re.findall(r"#(\w+)", text):
        add(_split_camel(match), "hashtag")

    # Capitalised sequences: runs of Capitalised words. A word counts
    # when it is capitalised mid-sentence, or when the same token is
    # capitalised mid-sentence anywhere in the text.
    words = re.findall(r"[A-Za-z][A-Za-z'’]*", text)
    mid_sentence_caps: set = set()
    sentence_start = True
    tokens_with_flags: List[Tuple[str, bool]] = []
    for raw in re.finditer(r"[A-Za-z][A-Za-z'’]*|[.!?]", text):
        token = raw.group(0)
        if token in ".!?":
            sentence_start = True
            continue
        is_start = sentence_start
        sentence_start = False
        tokens_with_flags.append((token, is_start))
        if token[:1].isupper() and not is_start:
            mid_sentence_caps.add(token.lower())
    run: List[str] = []
    for token, is_start in tokens_with_flags + [("", True)]:
        qualifies = token[:1].isupper() and (
            not is_start or token.lower() in mid_sentence_caps
        )
        if qualifies:
            run.append(token)
        elif run:
            add(" ".join(run), "proper")
            run = []
    _ = words  # (token scan above is authoritative)

    if terms:
        return terms

    residue = text.lower()
    for pattern in _BOILERPLATE_RES:
        residue = re.sub(pattern, " ", residue)
    residue = re.sub(r"[^a-z0-9, ]+", " ", residue)
    for chunk in re.split(r",|\band\b", residue):
        chunk = " ".join(w for w in chunk.split() if w not in _STOPWORDS)
        if chunk:
            add(chunk, "topic")
    return terms


# ---------------------------------------------------------------------------
# Transcript search (design §3.2) — exact + ASR-wobble fuzzy
# ---------------------------------------------------------------------------


def _singular(token: str) -> str:
    if len(token) >= 4 and token.endswith("s") and not token.endswith("ss"):
        return token[:-1]
    return token


def normalize_tokens(text: str) -> List[str]:
    raw = re.sub(r"[^a-z0-9']+", " ", (text or "").lower()).split()
    return [_singular(t) for t in raw]


def _levenshtein(a: str, b: str) -> int:
    if abs(len(a) - len(b)) > 1:
        return 2
    previous = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        current = [i]
        for j, cb in enumerate(b, 1):
            current.append(
                min(previous[j] + 1, current[j - 1] + 1, previous[j - 1] + (ca != cb))
            )
        previous = current
    return previous[-1]


def _phrase_match_kind(line_tokens: List[str], phrase_tokens: List[str]) -> Optional[str]:
    if not phrase_tokens or len(line_tokens) < len(phrase_tokens):
        return None
    fuzzy = False
    for i in range(len(line_tokens) - len(phrase_tokens) + 1):
        window = line_tokens[i : i + len(phrase_tokens)]
        ok = True
        window_fuzzy = False
        for got, want in zip(window, phrase_tokens):
            if got == want:
                continue
            if len(want) >= 5 and _levenshtein(got, want) == 1:
                window_fuzzy = True
                continue
            ok = False
            break
        if ok:
            fuzzy = fuzzy or window_fuzzy
            if not window_fuzzy:
                return "exact"
    return "fuzzy" if fuzzy else None


def line_bounds(line: Dict[str, Any]) -> Tuple[float, float]:
    start = line.get("start_seconds", line.get("start", 0.0))
    end = line.get("end_seconds", line.get("end", start))
    return float(start or 0.0), float(end or 0.0)


def search_lines(
    lines: List[Dict[str, Any]], terms: List[Dict[str, str]]
) -> List[Dict[str, Any]]:
    """All hits of the terms in the lines, with verbatim excerpts."""
    hits: List[Dict[str, Any]] = []
    tokenised = [(line, normalize_tokens(str(line.get("text", "")))) for line in lines]
    for term in terms:
        phrase_tokens = normalize_tokens(term["phrase"])
        for index, (line, tokens) in enumerate(tokenised):
            kind = _phrase_match_kind(tokens, phrase_tokens)
            if kind:
                start, end = line_bounds(line)
                hits.append(
                    {
                        "line_index": index,
                        "start_seconds": start,
                        "end_seconds": end,
                        "excerpt": str(line.get("text", "")),
                        "term": term["phrase"],
                        "match": kind,
                    }
                )
    return hits


def lines_in_window(
    lines: List[Dict[str, Any]], start: float, end: float
) -> List[Dict[str, Any]]:
    return [
        line
        for line in lines
        if line_bounds(line)[1] > start and line_bounds(line)[0] < end
    ]


# ---------------------------------------------------------------------------
# Focus scoring (design §3.4)
# ---------------------------------------------------------------------------


def evaluate_focus(
    window_lines: List[Dict[str, Any]],
    terms: List[Dict[str, str]],
    *,
    window_start: float,
    window_end: float,
) -> Dict[str, Any]:
    """Deterministic focus verdict for one requirement over one window."""
    hits = search_lines(window_lines, terms) if terms else []
    hit_line_indexes = {h["line_index"] for h in hits}
    # line_index is relative to window_lines here (search over the slice).
    hit_lines = len(hit_line_indexes)
    hit_duration = 0.0
    for idx in hit_line_indexes:
        s, e = line_bounds(window_lines[idx])
        hit_duration += max(0.0, e - s)
    duration = max(0.0, window_end - window_start)
    span_share = (hit_duration / duration) if duration > 0 else 0.0
    terms_hit = {h["term"] for h in hits}
    coverage = (len(terms_hit) / len(terms)) if terms else 0.0
    first_offset = (
        min(h["start_seconds"] for h in hits) - window_start if hits else None
    )
    if not hits:
        focus = "absent"
    elif hit_lines >= FOCUS_MAJOR_MIN_HIT_LINES and (
        span_share >= FOCUS_MAJOR_SPAN_SHARE or hit_lines >= FOCUS_MAJOR_MANY_LINES
    ) and coverage >= 0.5:
        focus = "major"
    else:
        focus = "passing"
    grey_zone = bool(
        hit_lines == 1 and FOCUS_GREY_MIN_SHARE <= span_share < FOCUS_MAJOR_SPAN_SHARE
    )
    return {
        "focus": focus,
        "grey_zone": grey_zone,
        "hit_count": len(hits),
        "hit_lines": hit_lines,
        "span_share": round(span_share, 4),
        "term_coverage": round(coverage, 4),
        "first_hit_offset": first_offset,
        "hits": hits,
        "terms": [t["phrase"] for t in terms],
    }


def requirement_terms(requirement: Dict[str, Any]) -> List[Dict[str, str]]:
    """Search terms for a normalised v2 requirement, by type."""
    rtype = requirement.get("type", "topic_focus")
    if rtype == "must_mention":
        phrase = str(requirement.get("phrase") or "").strip()
        if phrase:
            return [{"phrase": _clean_phrase(phrase), "source": "field"}]
        return extract_terms(requirement.get("text", ""))
    if rtype == "topic_focus":
        topics = requirement.get("topics") or []
        if topics:
            return [
                {"phrase": _clean_phrase(str(t)), "source": "field"}
                for t in topics
                if _clean_phrase(str(t))
            ]
        return extract_terms(requirement.get("text", ""))
    if rtype == "caption_rule":
        terms: List[Dict[str, str]] = []
        for phrase in requirement.get("phrases") or []:
            cleaned = _clean_phrase(str(phrase))
            if cleaned:
                terms.append({"phrase": cleaned, "source": "field"})
        for name in requirement.get("names") or []:
            cleaned = _clean_phrase(str(name))
            if cleaned:
                terms.append({"phrase": cleaned, "source": "field"})
        return terms
    if rtype == "structure_cta":
        return [
            {"phrase": _clean_phrase(str(p)), "source": "field"}
            for p in (requirement.get("phrases") or [])
            if _clean_phrase(str(p))
        ]
    return extract_terms(requirement.get("text", ""))


def focus_satisfaction(focus_eval: Dict[str, Any], requirement: Dict[str, Any]) -> str:
    """satisfied | failed | uncertain for one deterministic focus eval."""
    if focus_eval["focus"] == "absent":
        return "failed"
    if focus_eval.get("grey_zone"):
        return "uncertain"
    if requirement.get("requires_major"):
        return "satisfied" if focus_eval["focus"] == "major" else "failed"
    return "satisfied"


# ---------------------------------------------------------------------------
# Brief validation + v1 -> v2 normalisation (Addendum A.1)
# ---------------------------------------------------------------------------


def _validate_requirement(raw: Any, index: int) -> Dict[str, Any]:
    if isinstance(raw, str):
        raw = {"text": raw}
    if not isinstance(raw, dict):
        raise ValueError("each requirement must be an object with text")
    text = str(raw.get("text") or "").strip()
    rtype = raw.get("type") or "topic_focus"
    if rtype not in REQUIREMENT_TYPES:
        raise ValueError(f"unknown requirement type {rtype!r}")
    if rtype == "source_restriction":
        raise ValueError(
            "source_restriction is expressed via allowed_ranges, not as a requirement"
        )
    if not text and rtype not in ("structure_hook", "structure_cta", "caption_rule"):
        raise ValueError("each requirement needs text")
    if len(text) > MAX_REQUIREMENT_CHARS:
        raise ValueError(
            f"requirement text is limited to {MAX_REQUIREMENT_CHARS} characters"
        )
    req: Dict[str, Any] = {"id": f"r{index}", "type": rtype, "text": text}
    if rtype == "info_only":
        return req
    kind = raw.get("kind") or "mandatory"
    if kind not in ("mandatory", "optional"):
        raise ValueError("requirement kind must be mandatory or optional")
    req["kind"] = kind
    if rtype == "must_mention":
        phrase = raw.get("phrase")
        if phrase is not None and not isinstance(phrase, str):
            raise ValueError("must_mention phrase must be text")
        if not phrase:
            terms = extract_terms(text)
            phrase = terms[0]["phrase"] if terms else ""
        req["phrase"] = str(phrase)
        check_in = raw.get("check_in") or ["transcript", "captions", "title_copy"]
        if not isinstance(check_in, list) or not check_in:
            raise ValueError("must_mention check_in must be a non-empty list")
        unknown = set(check_in) - {"transcript", "captions", "title_copy"}
        if unknown:
            raise ValueError(f"unknown must_mention check_in sources: {sorted(unknown)}")
        req["check_in"] = list(check_in)
    elif rtype == "topic_focus":
        topics = raw.get("topics") or []
        if not isinstance(topics, list) or any(not isinstance(t, str) for t in topics):
            raise ValueError("topic_focus topics must be a list of text")
        req["topics"] = [t.strip() for t in topics if t.strip()]
        min_topics = raw.get("min_topics", 1)
        if isinstance(min_topics, bool) or not isinstance(min_topics, int) or min_topics < 1:
            raise ValueError("topic_focus min_topics must be a positive whole number")
        req["min_topics"] = min_topics
        if "requires_major" in raw:
            if not isinstance(raw["requires_major"], bool):
                raise ValueError("requires_major must be true or false")
            req["requires_major"] = raw["requires_major"]
        else:
            req["requires_major"] = requires_major_focus(text)
    elif rtype == "structure_hook":
        within = raw.get("within_seconds", 2)
        if isinstance(within, bool) or not isinstance(within, (int, float)) or within <= 0:
            raise ValueError("structure_hook within_seconds must be a positive number")
        req["within_seconds"] = float(within)
    elif rtype == "structure_cta":
        phrases = raw.get("phrases") or []
        if not isinstance(phrases, list) or any(not isinstance(p, str) for p in phrases):
            raise ValueError("structure_cta phrases must be a list of text")
        req["phrases"] = [p.strip() for p in phrases if p.strip()]
        req["window"] = "final_stretch"
    elif rtype == "visual_presence":
        req["subject"] = str(raw.get("subject") or text)
    elif rtype == "caption_rule":
        hashtags = raw.get("hashtags") or []
        phrases = raw.get("phrases") or []
        names = raw.get("names") or []
        for label, values in (("hashtags", hashtags), ("phrases", phrases), ("names", names)):
            if not isinstance(values, list) or any(not isinstance(v, str) for v in values):
                raise ValueError(f"caption_rule {label} must be a list of text")
        req["hashtags"] = [
            h if h.startswith("#") else f"#{h}" for h in (x.strip() for x in hashtags) if h
        ]
        req["phrases"] = [p.strip() for p in phrases if p.strip()]
        req["names"] = [n.strip() for n in names if n.strip()]
        if not (req["hashtags"] or req["phrases"] or req["names"]):
            terms = extract_terms(text)
            req["phrases"] = [t["phrase"] for t in terms]
    return req


def _parse_point_time(item: Dict[str, Any], source_duration: Optional[float], label: str) -> Tuple[float, str]:
    raw_at = item.get("at", item.get("at_seconds", item.get("time")))
    if raw_at is None:
        raise ValueError(f"each {label} needs an 'at' timecode")
    at = parse_timecode(raw_at)
    if source_duration is not None and at >= source_duration:
        raise ValueError(f"{label} time is beyond the end of the source video")
    at_label = raw_at if isinstance(raw_at, str) and raw_at.strip() else format_timecode(at)
    return float(at), str(at_label)


def _validate_flag_points(
    value: Any, *, source_duration: Optional[float] = None
) -> List[Dict[str, Any]]:
    """Normalise flag points: [{at_seconds, at_label, category, note}].

    Categories are descriptive review labels, not enforcement switches,
    so an unrecognised category coerces to ``other`` (the note keeps the
    brief's own words) instead of rejecting the whole brief.
    """
    if value is None:
        return []
    if not isinstance(value, list):
        raise ValueError("flag_points must be a list")
    if len(value) > MAX_FLAG_POINTS:
        raise ValueError(f"at most {MAX_FLAG_POINTS} flag points are supported")
    points: List[Dict[str, Any]] = []
    for item in value:
        if not isinstance(item, dict):
            raise ValueError("each flag point must be an object with at/category")
        at, at_label = _parse_point_time(item, source_duration, "flag point")
        category = str(item.get("category") or "other").strip().lower()
        if category not in FLAG_CATEGORIES:
            category = "other"
        note = str(item.get("note") or "").strip()
        if len(note) > MAX_POINT_NOTE_CHARS:
            raise ValueError(
                f"flag point note is limited to {MAX_POINT_NOTE_CHARS} characters"
            )
        points.append(
            {
                "at_seconds": at,
                "at_label": at_label,
                "category": category,
                "note": note,
            }
        )
    points.sort(key=lambda p: p["at_seconds"])
    return points


def _validate_key_timestamps(
    value: Any, *, source_duration: Optional[float] = None
) -> List[Dict[str, Any]]:
    """Normalise key timestamps: [{at_seconds, at_label, description}]."""
    if value is None:
        return []
    if not isinstance(value, list):
        raise ValueError("key_timestamps must be a list")
    if len(value) > MAX_KEY_TIMESTAMPS:
        raise ValueError(f"at most {MAX_KEY_TIMESTAMPS} key timestamps are supported")
    keys: List[Dict[str, Any]] = []
    for item in value:
        if not isinstance(item, dict):
            raise ValueError("each key timestamp must be an object with at/description")
        at, at_label = _parse_point_time(item, source_duration, "key timestamp")
        description = str(item.get("description") or "").strip()
        if len(description) > MAX_POINT_NOTE_CHARS:
            raise ValueError(
                f"key timestamp description is limited to {MAX_POINT_NOTE_CHARS} characters"
            )
        keys.append(
            {
                "at_seconds": at,
                "at_label": at_label,
                "description": description,
            }
        )
    keys.sort(key=lambda k: k["at_seconds"])
    return keys


def flag_points_of(brief: Any) -> List[Dict[str, Any]]:
    """A brief's flag points (normalised dicts), [] when absent."""
    if not isinstance(brief, dict):
        return []
    points = brief.get("flag_points") or []
    return [p for p in points if isinstance(p, dict) and "at_seconds" in p]


def key_timestamps_of(brief: Any) -> List[Dict[str, Any]]:
    """A brief's key timestamps (normalised dicts), [] when absent."""
    if not isinstance(brief, dict):
        return []
    keys = brief.get("key_timestamps") or []
    return [k for k in keys if isinstance(k, dict) and "at_seconds" in k]


def normalize_brief_v2(value: Any, *, source_duration: Optional[float] = None) -> Optional[Dict[str, Any]]:
    """Normalise any stored/incoming brief to schema version 2.

    ``None``/empty -> ``None`` (feature off). A v1 brief (requirements
    without ``type``) maps each requirement to ``topic_focus`` with its
    text, except presentation-targeting text, which maps to
    ``caption_rule`` (Addendum A.1). Pure: safe at read time, no data
    migration.
    """
    if value is None:
        return None
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except ValueError:
            raise ValueError("content_brief is not valid JSON") from None
    if not isinstance(value, dict):
        raise ValueError("content_brief must be an object")
    raw_requirements = value.get("requirements") or []
    if not isinstance(raw_requirements, list):
        raise ValueError("content_brief requirements must be a list")
    if len(raw_requirements) > MAX_REQUIREMENTS:
        raise ValueError(f"at most {MAX_REQUIREMENTS} requirements are supported")
    requirements: List[Dict[str, Any]] = []
    for index, raw in enumerate(raw_requirements, start=1):
        if isinstance(raw, dict) and "type" not in raw and value.get("version", 1) == 1:
            text = str(raw.get("text") or "")
            if is_presentation_text(text):
                raw = {**raw, "type": "caption_rule"}
            else:
                raw = {**raw, "type": "topic_focus"}
        requirements.append(_validate_requirement(raw, index))
    ranges = parse_allowed_ranges(value.get("allowed_ranges"), source_duration=source_duration)
    excluded = parse_excluded_ranges(
        value.get("excluded_ranges"), source_duration=source_duration
    )
    flag_points = _validate_flag_points(
        value.get("flag_points"), source_duration=source_duration
    )
    key_timestamps = _validate_key_timestamps(
        value.get("key_timestamps"), source_duration=source_duration
    )
    # Campaign rules (Chopify take): an optional enforceable rule set
    # (must-mention / banned / length / aspect) carried inside the brief
    # and applied at cut time by the S2 gate. Normalised here so a stored
    # brief can never smuggle an unvalidated rule shape to the gate.
    from .campaign_rules import normalize_campaign_rules

    campaign_rules = normalize_campaign_rules(value.get("campaign_rules"))
    if not requirements and not ranges and not excluded and not flag_points and not key_timestamps and not campaign_rules:
        return None
    brief: Dict[str, Any] = {
        "version": BRIEF_VERSION,
        "requirements": requirements,
        "allowed_ranges": ranges,
    }
    if excluded:
        brief["excluded_ranges"] = excluded
    if flag_points:
        brief["flag_points"] = flag_points
    if key_timestamps:
        brief["key_timestamps"] = key_timestamps
    if campaign_rules:
        brief["campaign_rules"] = campaign_rules
    example_url = value.get("example_url")
    if isinstance(example_url, str) and example_url.strip():
        brief["example_url"] = example_url.strip()
    return brief


def validate_content_brief(
    value: Any, *, source_duration: Optional[float] = None
) -> Optional[Dict[str, Any]]:
    """Create-time validator: returns the normalised v2 brief or None."""
    return normalize_brief_v2(value, source_duration=source_duration)


def parse_stored_brief(value: Any) -> Optional[Dict[str, Any]]:
    """Read-time reader for a stored brief; malformed stored data -> None
    is NOT acceptable for enforcement, so callers that enforce should use
    normalize_brief_v2 and handle ValueError. This helper is for display
    paths and never raises."""
    try:
        return normalize_brief_v2(value)
    except (ValueError, TypeError):
        return None


def brief_fingerprint(brief: Optional[Dict[str, Any]]) -> Optional[str]:
    if not brief:
        return None
    canonical = json.dumps(brief, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def brief_summary_text(brief: Dict[str, Any]) -> str:
    parts = []
    for req in brief.get("requirements") or []:
        if req.get("type") != "info_only":
            parts.append(req.get("text") or req.get("type", ""))
    ranges = brief.get("allowed_ranges") or []
    if ranges:
        parts.append(
            "ranges "
            + ", ".join(
                f"{r['start_label']} to {r['end_label'] or 'end'}" for r in ranges
            )
        )
    excluded = brief.get("excluded_ranges") or []
    if excluded:
        parts.append(
            "never use "
            + ", ".join(
                f"{r['start_label']} to {r['end_label'] or 'end'}" for r in excluded
            )
        )
    flags = brief.get("flag_points") or []
    if flags:
        parts.append(f"{len(flags)} review flag point(s)")
    keys = brief.get("key_timestamps") or []
    if keys:
        parts.append(f"{len(keys)} key timestamp(s)")
    return "; ".join(p for p in parts if p)[:200]


# ---------------------------------------------------------------------------
# Candidate evaluation + ranking tiers (selection time)
# ---------------------------------------------------------------------------


def evaluate_candidate_requirements(
    lines: List[Dict[str, Any]],
    start: float,
    end: float,
    brief: Dict[str, Any],
    llm_judgements: Optional[Dict[str, Dict[str, Any]]] = None,
    llm_available: bool = True,
) -> Dict[str, Any]:
    """Selection-time verdicts for one candidate window.

    Only transcript-evidence types gate selection (topic_focus,
    must_mention, source ranges). Other types are post-render checks.
    ``llm_judgements`` maps requirement id -> validated judgement dict.
    """
    window_lines = lines_in_window(lines, start, end)
    window_text = " ".join(str(l.get("text", "")) for l in window_lines)
    entries: List[Dict[str, Any]] = []
    satisfied_mandatory = 0
    total_mandatory = 0
    for req in brief.get("requirements") or []:
        rtype = req.get("type")
        if rtype in ("info_only", "structure_hook", "structure_cta", "visual_presence", "caption_rule"):
            continue
        entry: Dict[str, Any] = {"id": req["id"], "type": rtype, "kind": req.get("kind")}
        if rtype == "must_mention":
            terms = requirement_terms(req)
            hits = search_lines(window_lines, terms) if terms else []
            entry.update(
                {
                    "focus": "major" if hits else "absent",
                    "status": "satisfied" if hits else "failed",
                    "hit_count": len(hits),
                    "evidence": hits[:3],
                    "terms": [t["phrase"] for t in terms],
                    "judgement_source": "deterministic",
                    "decided_by": "deterministic",
                }
            )
        else:  # topic_focus
            topics = req.get("topics") or []
            if topics:
                per_topic = []
                major_topics = 0
                for topic in topics:
                    terms = [{"phrase": _clean_phrase(topic), "source": "field"}]
                    ev = evaluate_focus(
                        window_lines, terms, window_start=start, window_end=end
                    )
                    status = focus_satisfaction(ev, req)
                    if ev["focus"] == "major" or (not req.get("requires_major") and ev["focus"] != "absent"):
                        major_topics += 1
                    per_topic.append({"topic": topic, "focus": ev["focus"], "status": status, "hits": ev["hits"][:2]})
                needed = int(req.get("min_topics", 1))
                status = "satisfied" if major_topics >= needed else "failed"
                entry.update(
                    {
                        "focus": "major" if status == "satisfied" else ("passing" if any(p["focus"] != "absent" for p in per_topic) else "absent"),
                        "status": status,
                        "topics": per_topic,
                        "major_topics": major_topics,
                        "min_topics": needed,
                        "judgement_source": "deterministic",
                        "decided_by": "deterministic",
                    }
                )
            else:
                terms = requirement_terms(req)
                ev = evaluate_focus(window_lines, terms, window_start=start, window_end=end)
                status = focus_satisfaction(ev, req)
                entry.update(
                    {
                        "focus": ev["focus"],
                        "grey_zone": ev["grey_zone"],
                        "status": status,
                        "hit_count": ev["hit_count"],
                        "hit_lines": ev["hit_lines"],
                        "evidence": ev["hits"][:3],
                        "terms": ev["terms"],
                        "judgement_source": "deterministic",
                        "decided_by": "deterministic",
                    }
                )
                # LLM merge for the single-topic form.
                judgement = (llm_judgements or {}).get(req["id"])
                if judgement:
                    merged = _merge_llm_judgement(entry, judgement, window_text)
                    if merged is not None:
                        entry = merged
                elif not llm_available and ev["grey_zone"]:
                    entry["judgement_source"] = "deterministic_only"
                    entry["status"] = "uncertain"
        if req.get("kind") == "mandatory":
            total_mandatory += 1
            if entry["status"] == "satisfied":
                satisfied_mandatory += 1
        entries.append(entry)
    if total_mandatory == 0 or satisfied_mandatory == total_mandatory:
        tier = "A"
    elif satisfied_mandatory > 0:
        tier = "B"
    else:
        tier = "C"
    return {
        "requirements": entries,
        "tier": tier,
        "satisfied_mandatory": satisfied_mandatory,
        "total_mandatory": total_mandatory,
    }


def _merge_llm_judgement(
    entry: Dict[str, Any], judgement: Dict[str, Any], window_text: str
) -> Optional[Dict[str, Any]]:
    """Validate + merge one LLM judgement into a deterministic entry.

    Returns the merged entry, or None when the judgement is discarded
    (invented evidence, or a low-confidence promotion of a zero-hit
    candidate). Deterministic hit evidence is never deleted.
    """
    excerpt = str(judgement.get("evidence_excerpt") or "")
    focus = judgement.get("focus")
    if focus not in ("major", "passing", "absent"):
        return None
    normalised_text = " ".join(normalize_tokens(window_text))
    normalised_excerpt = " ".join(normalize_tokens(excerpt))
    if not normalised_excerpt or normalised_excerpt not in normalised_text:
        return None
    try:
        confidence = float(judgement.get("confidence", 0.0))
    except (TypeError, ValueError):
        return None
    if entry.get("focus") == "absent" and focus == "major" and confidence < 0.6:
        return None
    merged = dict(entry)
    merged["focus"] = focus
    merged["grey_zone"] = False
    merged["judgement_source"] = "llm"
    merged["decided_by"] = "llm"
    merged["llm"] = {
        "focus": focus,
        "confidence": confidence,
        "evidence_excerpt": excerpt,
        "reason": str(judgement.get("reason") or "")[:200],
    }
    req_major = bool(entry.get("requires_major"))
    if focus == "absent":
        merged["status"] = "failed"
    elif req_major and focus != "major":
        merged["status"] = "failed"
    else:
        merged["status"] = "satisfied"
    return merged


def partition_by_tier(items: List[Any], verdicts: List[Dict[str, Any]]) -> List[Any]:
    """Stable mandatory-first partition: Tier A, then B (count desc), then C.

    Order inside a tier is the incoming (virality/rerank) order, untouched.
    """
    tier_a = [it for it, v in zip(items, verdicts) if v["tier"] == "A"]
    tier_b_pairs = [
        (it, v) for it, v in zip(items, verdicts) if v["tier"] == "B"
    ]
    tier_b_pairs.sort(key=lambda pair: -pair[1]["satisfied_mandatory"])
    tier_c = [it for it, v in zip(items, verdicts) if v["tier"] == "C"]
    return tier_a + [it for it, _ in tier_b_pairs] + tier_c


def generate_requirement_candidates(
    lines: List[Dict[str, Any]],
    brief: Dict[str, Any],
    *,
    clip_length: str = "auto",
    allowed: Optional[List[RangeTuple]] = None,
) -> List[Dict[str, Any]]:
    """Supplementary candidates built from requirement hit clusters.

    The analysis model picks for virality, blind to the brief; without
    these, a compliant-but-not-viral stretch could never enter the pool
    and the feature would report false no-matches.
    """
    from ..clip_duration import clip_duration

    bounds = clip_duration(clip_length)
    terms: List[Dict[str, str]] = []
    for req in brief.get("requirements") or []:
        if req.get("type") in ("topic_focus", "must_mention"):
            terms.extend(requirement_terms(req))
    if not terms or not lines:
        return []
    hits = search_lines(lines, terms)
    if not hits:
        return []
    hit_line_indexes = sorted({h["line_index"] for h in hits})
    clusters: List[List[int]] = [[hit_line_indexes[0]]]
    for idx in hit_line_indexes[1:]:
        prev = clusters[-1][-1]
        gap = line_bounds(lines[idx])[0] - line_bounds(lines[prev])[1]
        if gap <= CLUSTER_GAP_SECONDS:
            clusters[-1].append(idx)
        else:
            clusters.append([idx])
    allowed = allowed if allowed is not None else allowed_range_tuples(brief)
    candidates: List[Dict[str, Any]] = []
    for cluster in clusters:
        first, last = cluster[0], cluster[-1]
        # Expand outward to whole lines until the ideal minimum is met,
        # never past the maximum and never across an allowed boundary.
        while True:
            start = line_bounds(lines[first])[0]
            end = line_bounds(lines[last])[1]
            if end - start >= bounds.ideal_minimum or end - start >= bounds.maximum:
                break
            grew = False
            if first > 0:
                cand_start = line_bounds(lines[first - 1])[0]
                if not allowed or any(a <= cand_start and line_bounds(lines[first - 1])[1] <= b for a, b in allowed):
                    if end - cand_start <= bounds.maximum:
                        first -= 1
                        grew = True
            if last < len(lines) - 1 and (end - start) < bounds.ideal_minimum:
                cand_end = line_bounds(lines[last + 1])[1]
                if not allowed or any(a <= line_bounds(lines[last + 1])[0] and cand_end <= b for a, b in allowed):
                    if cand_end - start <= bounds.maximum:
                        last += 1
                        grew = True
            if not grew:
                break
        start = line_bounds(lines[first])[0]
        end = line_bounds(lines[last])[1]
        if end - start < bounds.minimum:
            continue
        if allowed and not any(a - 0.05 <= start and end <= b + 0.05 for a, b in allowed):
            continue
        text = " ".join(str(lines[i].get("text", "")) for i in range(first, last + 1))
        density = len(cluster) / max(1, last - first + 1)
        candidates.append(
            {
                "start_seconds": float(start),
                "end_seconds": float(end),
                "start_time": lines[first].get("start_time"),
                "end_time": lines[last].get("end_time"),
                "text": text,
                "relevance_score": round(min(1.0, 0.5 + density), 4),
                "reasoning": "Requirement match: "
                + ", ".join(sorted({h["term"] for h in hits if h["line_index"] in cluster})),
            }
        )
    return candidates


def generate_key_timestamp_candidates(
    lines: List[Dict[str, Any]],
    brief: Dict[str, Any],
    *,
    clip_length: str = "auto",
    allowed: Optional[List[RangeTuple]] = None,
    excluded: Optional[List[RangeTuple]] = None,
) -> List[Dict[str, Any]]:
    """Supplementary candidates built from the brief's key timestamps.

    A key timestamp names a moment the campaign itself picked; the
    window is the transcript lines around it, expanded to the clip
    duration bounds exactly like requirement candidates, and held to
    the same allowed/excluded range rules. Without transcript lines
    covering the timestamp there is nothing honest to build.
    """
    from ..clip_duration import clip_duration

    keys = key_timestamps_of(brief)
    if not keys or not lines:
        return []
    bounds = clip_duration(clip_length)
    allowed = allowed if allowed is not None else allowed_range_tuples(brief)
    excluded = excluded if excluded is not None else excluded_range_tuples(brief)
    candidates: List[Dict[str, Any]] = []
    for key in keys:
        at = float(key["at_seconds"])
        index = next(
            (
                i
                for i, line in enumerate(lines)
                if line_bounds(line)[0] <= at < line_bounds(line)[1]
            ),
            None,
        )
        if index is None:
            continue
        first = last = index
        while True:
            start = line_bounds(lines[first])[0]
            end = line_bounds(lines[last])[1]
            if end - start >= bounds.ideal_minimum or end - start >= bounds.maximum:
                break
            grew = False
            if first > 0:
                cand_start = line_bounds(lines[first - 1])[0]
                if end - cand_start <= bounds.maximum:
                    first -= 1
                    grew = True
            if last < len(lines) - 1 and (end - start) < bounds.ideal_minimum:
                cand_end = line_bounds(lines[last + 1])[1]
                if cand_end - start <= bounds.maximum:
                    last += 1
                    grew = True
            if not grew:
                break
        start = line_bounds(lines[first])[0]
        end = line_bounds(lines[last])[1]
        if end - start < bounds.minimum:
            continue
        if allowed and not any(a - 0.05 <= start and end <= b + 0.05 for a, b in allowed):
            continue
        if excluded:
            pieces = clamp_segment_outside_excluded(start, end, excluded)
            if not pieces:
                continue
            piece_start, piece_end = max(pieces, key=lambda p: p[1] - p[0])
            if piece_end - piece_start < bounds.minimum:
                continue
            start, end = piece_start, piece_end
        text = " ".join(str(lines[i].get("text", "")) for i in range(first, last + 1))
        label = key.get("at_label") or format_timecode(at)
        description = str(key.get("description") or "").strip()
        candidates.append(
            {
                "start_seconds": float(start),
                "end_seconds": float(end),
                "start_time": lines[first].get("start_time"),
                "end_time": lines[last].get("end_time"),
                "text": text,
                "relevance_score": 0.9,
                "reasoning": f"Brief key timestamp {label}"
                + (f": {description}" if description else ""),
            }
        )
    return candidates


# ---------------------------------------------------------------------------
# Post-render compliance (design §3.6 + Addendum A.2/A.3)
# ---------------------------------------------------------------------------


def _phrase_in_text(phrase: str, text: str) -> bool:
    tokens = normalize_tokens(text)
    phrase_tokens = normalize_tokens(phrase)
    if not phrase_tokens:
        return False
    return any(
        tokens[i : i + len(phrase_tokens)] == phrase_tokens
        for i in range(len(tokens) - len(phrase_tokens) + 1)
    )


def _hashtag_in_text(hashtag: str, text: str) -> bool:
    """Exact-token hashtag match: the '#' is required."""
    target = hashtag.lower()
    if not target.startswith("#"):
        target = f"#{target}"
    tokens = re.findall(r"#\w+", (text or "").lower())
    return target in tokens


def _cta_window_lines(
    lines: List[Dict[str, Any]], clip_start: float, clip_end: float
) -> Tuple[List[Dict[str, Any]], float]:
    duration = max(0.0, clip_end - clip_start)
    window = max(duration * 0.2, 10.0)
    return lines_in_window(lines, clip_end - window, clip_end), window


def evaluate_clip_compliance(
    brief: Dict[str, Any],
    actual_ranges: List[RangeTuple],
    transcript_lines: List[Dict[str, Any]],
    *,
    caption_text: Optional[str] = None,
    title_copy: Optional[str] = None,
    hook_facts: Optional[Dict[str, Any]] = None,
    visual_judgement: Optional[Dict[str, Any]] = None,
    selection_judgements: Optional[Dict[str, Dict[str, Any]]] = None,
    llm_available: bool = True,
    checked_at: Optional[str] = None,
) -> Dict[str, Any]:
    """Judge the rendered clip (from its actual source ranges) vs the brief.

    ``caption_text`` is the generated caption text (render-facts caption
    chunks); ``title_copy`` is the clip's title/caption copy when one
    exists at check time — absent copy is recorded as ``absent``, never
    assumed. ``hook_facts`` is the Quality Director's recorded hook
    outcome; it is read, never re-derived.
    """
    allowed = allowed_range_tuples(brief)
    within, violations = (True, []) if not allowed else ranges_within(actual_ranges, allowed)
    excluded = excluded_range_tuples(brief)
    excl_violations = (
        excluded_overlap_pieces(actual_ranges, excluded) if excluded else []
    )
    # Review flags: a clip containing a flag point is stamped, never
    # auto-fixed — a human decides about plates/branding/music.
    flag_hits: List[Dict[str, Any]] = []
    for point in flag_points_of(brief):
        at = float(point["at_seconds"])
        if any(s - 0.05 <= at < e + 0.05 for s, e in actual_ranges):
            flag_hits.append(
                {
                    "at_seconds": at,
                    "at_label": point.get("at_label") or format_timecode(at),
                    "category": point.get("category", "other"),
                    "note": point.get("note", ""),
                    "status": "needs_review",
                }
            )
    clip_start = min((s for s, _ in actual_ranges), default=0.0)
    clip_end = max((e for _, e in actual_ranges), default=0.0)
    clip_lines = [
        line
        for line in transcript_lines
        if any(
            line_bounds(line)[1] > s and line_bounds(line)[0] < e
            for s, e in actual_ranges
        )
    ]
    clip_text = " ".join(str(l.get("text", "")) for l in clip_lines)
    entries: List[Dict[str, Any]] = []
    overall_failed = bool(violations) or bool(excl_violations)
    overall_uncertain = False

    for req in brief.get("requirements") or []:
        rtype = req.get("type", "topic_focus")
        if rtype == "info_only":
            continue  # never checked (Addendum A.1)
        entry: Dict[str, Any] = {
            "id": req.get("id"),
            "type": rtype,
            "text": req.get("text", ""),
            "kind": req.get("kind"),
        }
        if rtype == "topic_focus":
            verdict = evaluate_candidate_requirements(
                transcript_lines,
                clip_start,
                clip_end,
                {"requirements": [req]},
                llm_judgements=(
                    {req["id"]: (selection_judgements or {}).get(req["id"])}
                    if selection_judgements and (selection_judgements or {}).get(req["id"])
                    else None
                ),
                llm_available=llm_available,
            )
            inner = verdict["requirements"][0] if verdict["requirements"] else {}
            entry.update(inner)
            entry["requires_major"] = bool(req.get("requires_major"))
            status = inner.get("status", "uncertain")
        elif rtype == "must_mention":
            phrase = str(req.get("phrase") or "")
            sources: Dict[str, str] = {}
            hit_sources: List[str] = []
            source_texts = {
                "transcript": clip_text,
                "captions": caption_text,
                "title_copy": title_copy,
            }
            for source in req.get("check_in") or ["transcript", "captions", "title_copy"]:
                text_value = source_texts.get(source)
                if text_value is None:
                    sources[source] = "absent"
                elif phrase and _phrase_in_text(phrase, text_value):
                    sources[source] = "hit"
                    hit_sources.append(source)
                else:
                    sources[source] = "searched_no_hit"
            entry.update(
                {
                    "phrase": phrase,
                    "sources": sources,
                    "hit_sources": hit_sources,
                    "judgement_source": "deterministic",
                    "decided_by": "deterministic",
                }
            )
            if hit_sources:
                status = "satisfied"
            elif sources.get("title_copy") == "absent":
                # The phrase could still be satisfied by title/caption
                # copy that does not exist at check time: honest
                # uncertain, never satisfied (Addendum A.8).
                status = "uncertain"
                entry["detail"] = "title/caption copy was absent at check time"
            else:
                status = "failed"
        elif rtype == "caption_rule":
            evidence_text = "\n".join(
                part for part in (caption_text, title_copy) if part
            )
            sources = {
                "captions": "absent" if caption_text is None else "searched",
                "title_copy": "absent" if title_copy is None else "searched",
            }
            hashtag_results = {
                tag: _hashtag_in_text(tag, evidence_text)
                for tag in req.get("hashtags") or []
            }
            phrase_results = {
                p: _phrase_in_text(p, evidence_text) for p in req.get("phrases") or []
            }
            name_results = {
                n: _phrase_in_text(n, evidence_text) for n in req.get("names") or []
            }
            all_ok = all(
                list(hashtag_results.values())
                + list(phrase_results.values())
                + list(name_results.values())
            )
            entry.update(
                {
                    "sources": sources,
                    "hashtags": hashtag_results,
                    "phrases": phrase_results,
                    "names": name_results,
                    "accuracy": "presence_only",
                    "judgement_source": "deterministic",
                    "decided_by": "deterministic",
                }
            )
            if caption_text is None and title_copy is None:
                status = "uncertain"
                entry["detail"] = "no caption text or title copy was recorded at check time"
            else:
                status = "satisfied" if all_ok else "failed"
        elif rtype == "structure_hook":
            within_seconds = float(req.get("within_seconds", 2))
            entry["within_seconds"] = within_seconds
            facts = hook_facts or {}
            hook_after = facts.get("hook_timestamp_after")
            entry["hook_timestamp_after"] = hook_after
            entry["hook_status"] = facts.get("status")
            entry["judgement_source"] = "recorded_facts"
            entry["decided_by"] = "deterministic"
            if hook_after is None:
                status = "uncertain"
                entry["detail"] = "no hook outcome was recorded for this render"
            elif facts.get("status") == "no_hook":
                status = "failed"
            else:
                status = "satisfied" if float(hook_after) <= within_seconds else "failed"
        elif rtype == "structure_cta":
            window_lines, window_seconds = _cta_window_lines(
                clip_lines, clip_start, clip_end
            )
            spoken_text = " ".join(str(l.get("text", "")) for l in window_lines)
            phrases = [str(p) for p in req.get("phrases") or []]
            matched_by = "configured_phrases"
            if not phrases:
                phrases = list(CTA_GENERIC_PHRASES)
                matched_by = "generic_cta_pattern"
            spoken_hit = any(_phrase_in_text(p, spoken_text) for p in phrases)
            caption_hit = bool(
                caption_text is not None
                and any(_phrase_in_text(p, caption_text) for p in phrases)
            ) or bool(
                title_copy is not None
                and any(_phrase_in_text(p, title_copy) for p in phrases)
            )
            form = (
                "both" if spoken_hit and caption_hit
                else "spoken" if spoken_hit
                else "caption_text" if caption_hit
                else "none"
            )
            entry.update(
                {
                    "form": form,
                    "window_seconds": round(window_seconds, 3),
                    "matched_by": matched_by,
                    "phrases": phrases,
                    "judgement_source": "deterministic",
                    "decided_by": "deterministic",
                }
            )
            status = "satisfied" if form != "none" else "failed"
        elif rtype == "visual_presence":
            entry["subject"] = req.get("subject") or req.get("text", "")
            entry["judgement_source"] = "model_judgement"
            entry["decided_by"] = "llm" if visual_judgement else "none"
            if visual_judgement and visual_judgement.get("present") is True:
                status = "satisfied"
                entry["visual"] = visual_judgement
            elif visual_judgement and visual_judgement.get("present") is False:
                status = "failed"
                entry["visual"] = visual_judgement
            else:
                status = "uncertain"
                entry["detail"] = "no visual-presence judgement was available"
        else:  # source_restriction: decided by the range audit above
            entry["judgement_source"] = "deterministic"
            entry["decided_by"] = "deterministic"
            status = "satisfied" if within else "failed"
        entry["status"] = status
        if req.get("kind") == "mandatory":
            if status == "failed":
                overall_failed = True
            elif status in ("uncertain", "not_checked"):
                overall_uncertain = True
        entries.append(entry)

    if overall_failed:
        status_overall = "non_compliant"
    elif overall_uncertain or flag_hits:
        status_overall = "uncertain"
    else:
        status_overall = "compliant"
    return {
        "version": 1,
        "checked_at": checked_at
        or datetime.now(timezone.utc).isoformat(),
        "status": status_overall,
        "needs_review": bool(flag_hits),
        "flags": flag_hits,
        "ranges": {
            "allowed": [
                [s, None if math.isinf(e) else e] for s, e in allowed
            ],
            "excluded": [
                [s, None if math.isinf(e) else e] for s, e in excluded
            ],
            "actual": [[float(s), float(e)] for s, e in actual_ranges],
            "violations": violations,
            "excluded_violations": excl_violations,
        },
        "requirements": entries,
        "source_timestamps": {
            "start": format_timecode(clip_start),
            "end": format_timecode(clip_end),
        },
    }


def compliance_summary_for(report: Any) -> Optional[Dict[str, Any]]:
    """Small additive badge summary beside the raw compliance report."""
    if report is None:
        return None
    if isinstance(report, str):
        try:
            report = json.loads(report)
        except ValueError:
            return None
    if not isinstance(report, dict):
        return None
    failed, uncertain = [], []
    for entry in report.get("requirements") or []:
        if entry.get("kind") != "mandatory":
            continue
        if entry.get("status") == "failed":
            failed.append(entry.get("id"))
        elif entry.get("status") in ("uncertain", "not_checked"):
            uncertain.append(entry.get("id"))
    summary: Dict[str, Any] = {
        "status": report.get("status"),
        "mandatory_failed": failed,
        "mandatory_uncertain": uncertain,
    }
    flags = report.get("flags")
    if flags:
        summary["needs_review"] = True
        summary["flag_points"] = len(flags)
        summary["flag_categories"] = sorted({f.get("category", "other") for f in flags})
    return summary


def build_brief_report(
    brief: Dict[str, Any],
    *,
    candidates_total: int,
    candidates_in_range: int,
    candidates_compliant: int,
    candidates_dropped_range: int,
    candidates_dropped_mandatory: int,
    outcome: str,
    llm_pass: str = "not_run",
    requirement_stats: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    if requirement_stats is None:
        requirement_stats = []
        for req in brief.get("requirements") or []:
            if req.get("type") == "info_only":
                continue
            terms = requirement_terms(req)
            requirement_stats.append(
                {
                    "id": req.get("id"),
                    "terms": [t["phrase"] for t in terms],
                    "term_source": terms[0]["source"] if terms else "none",
                }
            )
    return {
        "version": 1,
        "searched_ranges": brief.get("allowed_ranges") or [],
        "requirements": requirement_stats,
        "candidates_total": candidates_total,
        "candidates_in_range": candidates_in_range,
        "candidates_compliant": candidates_compliant,
        "candidates_dropped_range": candidates_dropped_range,
        "candidates_dropped_mandatory": candidates_dropped_mandatory,
        "outcome": outcome,
        "llm_pass": llm_pass,
    }


# ---------------------------------------------------------------------------
# Deterministic paste-a-brief extraction (Addendum A.4 fallback)
# ---------------------------------------------------------------------------

_TIME_NEAR_RE = re.compile(
    r"(?:from|after|only|starting\s+at)\s+(\d{1,3}:\d{2}(?::\d{2})?)\s*(?:to\s+(?:the\s+)?end|onwards?)?",
    re.IGNORECASE,
)
_RANGE_PAIR_RE = re.compile(
    r"(\d{1,3}:\d{2}(?::\d{2})?)\s*(?:to|–|-)\s*(\d{1,3}:\d{2}(?::\d{2})?)",
    re.IGNORECASE,
)
_INFO_KEYWORDS = (
    "payout", "views", "audience", "platform", "tiktok", "reels", "shorts",
    "must be over", "years old", "english",
)
_URL_RE = re.compile(r"https?://\S+")

# Brief-image sections (screenshot briefs carry headed sections the
# paste path must respect: a range pair under "Content to avoid" is an
# EXCLUSION, not the only allowed footage — the pre-section extractor
# read it exactly backwards).
_AVOID_HEADING_RE = re.compile(r"content\s+to\s+avoid", re.IGNORECASE)
_KEY_HEADING_RE = re.compile(r"key\s+timestamps?", re.IGNORECASE)
_KEY_LINE_RE = re.compile(
    r"^[^\d\n]{0,3}(\d{1,3}:\d{2}(?::\d{2})?)\s*[—–-]\s*(.+?)\s*$",
    re.MULTILINE,
)
_POINT_RE = re.compile(r"\d{1,3}:\d{2}(?::\d{2})?")
_LABEL_START_RE = re.compile(r"[A-Za-z][A-Za-z\- ]{1,40}:")
_THEME_RE = re.compile(r"connects?\s+back\s+to\s+([^.,;\n]+)", re.IGNORECASE)
_FLAG_CATEGORY_PATTERNS = (
    (re.compile(r"licen[cs]e|number\s*plate", re.IGNORECASE), "licence_plate"),
    (re.compile(r"brand|logo", re.IGNORECASE), "branding"),
    (re.compile(r"music|audio", re.IGNORECASE), "copyright_music"),
    (re.compile(r"sensitive", re.IGNORECASE), "sensitive_topic"),
    (re.compile(r"alcohol", re.IGNORECASE), "alcohol"),
)


def _flag_category_from_text(text: str) -> Optional[str]:
    for pattern, category in _FLAG_CATEGORY_PATTERNS:
        if pattern.search(text or ""):
            return category
    return None


def _split_avoid_section(text: str) -> Tuple[str, str]:
    """(head, avoid) split at the 'Content to avoid' heading."""
    match = _AVOID_HEADING_RE.search(text)
    if not match:
        return text, ""
    return text[: match.start()], text[match.start():]


def _extract_key_timestamps(head_text: str) -> List[Dict[str, Any]]:
    """'MM:SS — description' lines, only under a Key timestamps heading."""
    if not _KEY_HEADING_RE.search(head_text):
        return []
    return [
        {"at": match.group(1), "description": match.group(2).strip()}
        for match in _KEY_LINE_RE.finditer(head_text)
    ]


def _extract_avoid_section(
    avoid_text: str,
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """Excluded ranges + flag points from the avoid/flag section.

    The section is split at its own labels ('Licence plates:', 'Other:')
    and at ';', so each labelled list is judged with its own category.
    Endpoints of an excluded range pair are not also flag points.
    """
    excluded: List[Dict[str, Any]] = []
    for match in _RANGE_PAIR_RE.finditer(avoid_text):
        excluded.append({"start": match.group(1), "end": match.group(2)})

    flat = re.sub(r"\s+", " ", avoid_text)
    pieces: List[str] = []
    label_starts = [m.start() for m in _LABEL_START_RE.finditer(flat)]
    if label_starts and label_starts[0] > 0:
        pieces.append(flat[: label_starts[0]])
    for index, start in enumerate(label_starts):
        end = label_starts[index + 1] if index + 1 < len(label_starts) else len(flat)
        pieces.append(flat[start:end])
    if not label_starts:
        pieces = [flat]

    flags: List[Dict[str, Any]] = []
    for piece in pieces:
        for segment in piece.split(";"):
            segment = segment.strip()
            points = list(_POINT_RE.finditer(segment))
            if not points:
                continue
            pair_spans = [
                (m.start(), m.end()) for m in _RANGE_PAIR_RE.finditer(segment)
            ]
            kept = [
                p
                for p in points
                if not any(s <= p.start() < e for s, e in pair_spans)
            ]
            if not kept:
                continue
            label_match = _LABEL_START_RE.match(segment)
            label = label_match.group(0) if label_match else ""
            lowered = segment.lower()
            if "flag for review" in lowered:
                category = "review"
            else:
                category = (
                    _flag_category_from_text(label)
                    or _flag_category_from_text(segment)
                    or "other"
                )
            tail = segment[kept[-1].end():]
            tail_note = ""
            dash = re.search(r"[—–]\s*(.+)$", tail)
            if dash:
                tail_note = dash.group(1).strip().rstrip(".")
            for point in kept:
                note = ""
                paren = re.match(r"\s*\(([^)]{1,80})\)", segment[point.end():])
                if paren:
                    note = paren.group(1).strip()
                elif tail_note:
                    note = tail_note
                point_category = category
                if note and point_category in ("other", "review"):
                    note_category = _flag_category_from_text(note)
                    if note_category:
                        point_category = note_category
                flags.append(
                    {"at": point.group(0), "category": point_category, "note": note}
                )
    return excluded, flags


def extract_brief_deterministically(brief_text: str) -> Dict[str, Any]:
    """Best-effort structured draft from raw campaign text. Never raises
    on ordinary prose; the result is a DRAFT for user review."""
    text = brief_text or ""
    requirements: List[Dict[str, Any]] = []
    ranges: List[Dict[str, Any]] = []

    # Headed sections first: key timestamps and the avoid/flag list are
    # read from their own sections; everything else (ranges, phrases,
    # topics) reads the head, so avoid-section timecodes can never leak
    # into allowed ranges or requirements.
    head_text, avoid_text = _split_avoid_section(text)
    key_timestamps = _extract_key_timestamps(head_text)
    excluded_ranges, flag_points = (
        _extract_avoid_section(avoid_text) if avoid_text else ([], [])
    )

    for match in _RANGE_PAIR_RE.finditer(head_text):
        ranges.append({"start": match.group(1), "end": match.group(2)})
    if not ranges:
        for match in _TIME_NEAR_RE.finditer(head_text):
            ranges.append({"start": match.group(1), "end": None})

    example_url = None
    url_match = _URL_RE.search(text)
    if url_match:
        example_url = url_match.group(0).rstrip(").,")

    seen_phrases: set = set()
    for phrase in re.findall(r"[\"“]([^\"”]{1,80})[\"”]", head_text):
        key = phrase.lower()
        if key not in seen_phrases:
            seen_phrases.add(key)
            requirements.append(
                {
                    "text": f"The clip must mention {phrase}.",
                    "type": "must_mention",
                    "kind": "mandatory",
                    "phrase": phrase,
                }
            )
    for token in re.findall(r"\b[A-Z]{3,}\b", head_text):
        if token.lower() not in seen_phrases and token not in ("THE", "AND", "FOR"):
            seen_phrases.add(token.lower())
            requirements.append(
                {
                    "text": f"The clip must mention {token}.",
                    "type": "must_mention",
                    "kind": "mandatory",
                    "phrase": token,
                }
            )
    hashtags = re.findall(r"#\w+", head_text)
    if hashtags:
        requirements.append(
            {
                "text": "Captions must use " + ", ".join(hashtags) + ".",
                "type": "caption_rule",
                "kind": "mandatory",
                "hashtags": hashtags,
            }
        )
    # Theme sentence ("every clip should connect back to X"): the
    # campaign's connective tissue, as a mandatory topic focus on X.
    flat_text = re.sub(r"\s+", " ", head_text)
    theme_match = _THEME_RE.search(flat_text)
    if theme_match:
        topic = re.split(
            r"\s+and\s+(?:our|the)\s", theme_match.group(1)
        )[0].strip()
        sent_start = 0
        for separator in (". ", "! ", "? "):
            idx = flat_text.rfind(separator, 0, theme_match.start())
            if idx != -1:
                sent_start = max(sent_start, idx + 2)
        sent_end = flat_text.find(".", theme_match.end())
        sentence = flat_text[
            sent_start: sent_end + 1 if sent_end != -1 else len(flat_text)
        ].strip()
        if topic:
            if not sentence or len(sentence) > 480:
                sentence = f"Every clip must connect back to {topic}."
            requirements.append(
                {
                    "text": sentence if sentence.endswith(".") else sentence + ".",
                    "type": "topic_focus",
                    "kind": "mandatory",
                    "topics": [topic],
                }
            )
    for line in re.split(r"[\n.;]", head_text):
        stripped = line.strip()
        if not stripped:
            continue
        lowered = stripped.lower()
        if any(keyword in lowered for keyword in _INFO_KEYWORDS) and (
            "payout" in lowered or "audience" in lowered or "%" in stripped
            or "platform" in lowered or "views" in lowered
        ):
            requirements.append({"text": stripped + ".", "type": "info_only"})
    if not any(r.get("type") == "topic_focus" for r in requirements):
        for line in re.split(r"[\n.]", head_text):
            stripped = line.strip()
            if len(stripped) < 20:
                continue
            lowered = stripped.lower()
            if lowered.startswith(
                ("find", "feature", "show", "only select", "clips must", "the clip must")
            ) or "major focus" in lowered or " must be " in lowered:
                requirements.append(
                    {"text": stripped + ".", "type": "topic_focus", "kind": "mandatory"}
                )
    draft: Dict[str, Any] = {"requirements": requirements, "allowed_ranges": ranges}
    if excluded_ranges:
        draft["excluded_ranges"] = excluded_ranges
    if flag_points:
        draft["flag_points"] = flag_points
    if key_timestamps:
        draft["key_timestamps"] = key_timestamps
    if example_url:
        draft["example_url"] = example_url
    normalised = normalize_brief_v2(draft) or {
        "version": BRIEF_VERSION,
        "requirements": [],
        "allowed_ranges": [],
    }
    if example_url and "example_url" not in normalised:
        normalised["example_url"] = example_url
    return normalised
