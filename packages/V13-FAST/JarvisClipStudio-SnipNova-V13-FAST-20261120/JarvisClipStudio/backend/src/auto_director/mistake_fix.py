"""Mistake-fix hooks (Trend-Radar D16 + D25): detection, titles, eligibility.

The measured basis (Blossom library, pulled 14 Nov 2026, 10,459
videos): a "stop doing this" / mistake-and-fix hook ALONE is only
average (2.31x the account's usual views vs 2.28x for a typical
video). The pairing decides everything — with beat-timed cuts it
measured 5.78x, and with an OPEN LOOP (the answer held back) it
measured 1.40x, actively BELOW typical. The fix must land in the
first third of the clip. Those are correlations in a large library,
not promises; this module claims no audience outcome. What it owns
is the editorial shape, deterministically:

* DETECTION — a moment carries a mistake-fix only when the speaker
  actually raises a mistake in it (an imperative warning — "stop
  doing X", "don't", "never" — or a named mistake — "the biggest
  mistake is ...") AND goes on to deliver the fix inside the same
  moment (a corrective marker — "instead", "here's how", "the fix" —
  or a direct instruction). Both halves must be in the transcript's
  own words; nothing is inferred from the topic.

* TITLES — ``build_mistake_fix_title`` builds the title from the
  mistake sentence's own clause and NOTHING else: every title token
  must appear in that sentence (a grounding check, enforced), the
  speaker's first person can never survive into it, and the result
  must pass the style's own deterministic marker. When no genuine
  mistake is present the builder returns None — the style DECLINES
  rather than fabricating a mistake the speaker never raised.
  Hooks are never misleading; that rule outranks the style.

* ELIGIBILITY (D25 made structural) — a moment is eligible for the
  style only when its fix STARTS inside the first third of the
  clip. A moment whose resolution lands late, and a moment that
  raises a mistake it never resolves (the open-loop shape), are
  ineligible for ``mistake_fix`` while remaining perfectly eligible
  for every other style. ``resolve_mistake_fix_style`` is the seam:
  it grants the style only to an eligible moment in a profile that
  offers it, and otherwise falls back to the profile's own default
  style with the reason recorded.

Sentences are clip-relative timed dicts ``{text, start, end}`` —
the same shape the opening-strategy machinery uses.
``sentences_from_words`` derives them from timed words with the
codebase's own sentence boundaries.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Optional, Sequence

#: Title caps, mirroring src.ai's HOOK_TITLE_MAX_WORDS / _CHARS (the
#: analysis path's caps) so a deterministically built title obeys the
#: same house limits an AI-written one is trimmed to.
MAX_TITLE_WORDS = 10
MAX_TITLE_CHARS = 64

# --- Mistake detection ---------------------------------------------------------

_IMPERATIVE_HEADS = (
    "stop", "don't", "dont", "do not", "never", "avoid", "quit",
)

# Leading words that may precede the imperative inside its clause
# ("and stop wasting...", "so don't...") without changing who issues
# the warning.
_CLAUSE_FILLERS = (
    "and", "so", "but", "please", "now", "well", "just", "also",
)

_MISTAKE_NOUN_RE = re.compile(
    r"\b(biggest|big|common|huge|worst|first)\s+mistake\b"
    r"|\bmistake\s+(is|was)\b",
    re.IGNORECASE,
)

_DOING_WRONG_RE = re.compile(
    r"\byou(?:'re| are)\s+(.+?)\s+wrong\b",
    re.IGNORECASE,
)

# --- Fix detection -------------------------------------------------------------

_FIX_MARKER_RE = re.compile(
    r"\b(instead|here's how|heres how|the fix|what works|right way|"
    r"you should|the solution|do this instead)\b",
    re.IGNORECASE,
)

# Direct instructions ARE the fix in how-to content ("move half of
# it out on payday"). A later sentence whose first clause opens with
# one of these imperative verbs counts as delivering the fix.
_INSTRUCTION_HEADS = frozenset(
    {
        "move", "put", "open", "start", "take", "use", "switch", "pay",
        "save", "set", "keep", "buy", "book", "do", "try", "make",
        "build", "create", "choose", "pick", "cut", "track", "write",
        "ask", "check", "wait", "leave", "park", "split", "send",
        "explain", "practise", "practice", "read", "watch",
    }
)

_FIRST_PERSON_RE = re.compile(r"\b(i|i'm|im|i've|my|mine|we|our|us)\b", re.IGNORECASE)

# Trailing words a word-boundary cap may leave dangling; a title never
# ends on one of these.
_TRAILING_STOPWORDS = frozenset(
    {"of", "the", "a", "an", "in", "on", "to", "for", "and", "or", "is", "it"}
)


@dataclass
class MistakeFix:
    """A mistake the speaker raises in a moment, and its fix."""

    mistake_text: str
    mistake_start: float
    mistake_end: float
    fix_text: Optional[str] = None
    fix_start: Optional[float] = None
    #: True when the fix sentence carries an explicit corrective
    #: marker ("instead", "here's how", ...); False when it is a bare
    #: instruction sentence. Either way the fix is the speaker's own.
    fix_explicit: bool = False


@dataclass
class Eligibility:
    """Whether a moment may be cut under the mistake_fix style."""

    eligible: bool
    reasons: list[str] = field(default_factory=list)
    finding: Optional[MistakeFix] = None


def _norm(text: str) -> str:
    return " ".join(str(text or "").split())


def _clauses(text: str) -> list[str]:
    """Split a sentence into clauses on , ; — for imperative hunting."""
    return [part.strip() for part in re.split(r"[,;—]", text) if part.strip()]


def _strip_fillers(clause: str) -> str:
    words = clause.split()
    while words and words[0].lower().strip(".,!") in _CLAUSE_FILLERS:
        words = words[1:]
    return " ".join(words)


def _imperative_clause(text: str) -> Optional[str]:
    """The clause of ``text`` that issues a warning, if one does.

    A clause qualifies when, after leading conjunctions/fillers are
    stripped, it opens with an imperative warning head (stop /
    don't / do not / never / avoid / quit).
    """
    for clause in _clauses(text):
        stripped = _strip_fillers(clause)
        lowered = stripped.lower()
        for head in _IMPERATIVE_HEADS:
            if lowered == head or lowered.startswith(head + " "):
                return stripped
    return None


def _is_mistake_sentence(text: str) -> bool:
    if _imperative_clause(text) is not None:
        return True
    if _MISTAKE_NOUN_RE.search(text):
        return True
    if _DOING_WRONG_RE.search(text):
        return True
    return False


def _fix_kind(text: str) -> Optional[str]:
    """'explicit' / 'instruction' when a sentence delivers a fix."""
    if _FIX_MARKER_RE.search(text):
        return "explicit"
    for clause in _clauses(text):
        stripped = _strip_fillers(clause)
        first = stripped.split()[0].lower().strip(".,!") if stripped.split() else ""
        if first in _INSTRUCTION_HEADS:
            return "instruction"
    return None


def find_mistake_fix(sentences: Sequence[dict]) -> Optional[MistakeFix]:
    """The first mistake a moment raises, and the fix that follows it.

    Returns None when no sentence in the moment raises a mistake.
    When a mistake is raised but no later sentence delivers a fix,
    the finding's ``fix_start`` is None — the open-loop shape, which
    eligibility rejects for this style (D25).
    """
    norm = [
        {
            "text": _norm(s.get("text", "")),
            "start": float(s.get("start") or 0.0),
            "end": float(s.get("end") or 0.0),
        }
        for s in sentences or []
    ]
    norm = [s for s in norm if s["text"]]
    for index, sentence in enumerate(norm):
        if not _is_mistake_sentence(sentence["text"]):
            continue
        finding = MistakeFix(
            mistake_text=sentence["text"],
            mistake_start=sentence["start"],
            mistake_end=sentence["end"],
        )
        for later in norm[index + 1 :]:
            if _is_mistake_sentence(later["text"]):
                continue
            kind = _fix_kind(later["text"])
            if kind is None:
                continue
            finding.fix_text = later["text"]
            finding.fix_start = later["start"]
            finding.fix_explicit = kind == "explicit"
            break
        return finding
    return None


# --- Title building --------------------------------------------------------------


def _cap_title(title: str) -> str:
    words = title.split()
    if len(words) > MAX_TITLE_WORDS:
        words = words[:MAX_TITLE_WORDS]
    while words and words[-1].lower().strip(".,;:") in _TRAILING_STOPWORDS:
        words = words[:-1]
    title = " ".join(words)
    if len(title) > MAX_TITLE_CHARS:
        clipped = title[: MAX_TITLE_CHARS + 1]
        cut = clipped.rfind(" ")
        title = (clipped[:cut] if cut > 20 else title[:MAX_TITLE_CHARS]).rstrip(
            ".,;:-–— "
        )
    return title.strip(" .,;:")


def _tokens(text: str) -> list[str]:
    return [
        tok.strip(".,;:!?\"'").lower()
        for tok in re.findall(r"[A-Za-z0-9$%']+", text)
    ]


def _grounded(title: str, source_text: str) -> bool:
    """Every title token must appear in the source sentence.

    The anti-fabrication guard: a title built from the speaker's own
    clause can re-order nothing and introduce nothing — if a token
    is not in the sentence, the title is not this segment's title.
    """
    source = set(_tokens(source_text))
    return all(tok in source for tok in _tokens(title) if tok)


def build_mistake_fix_title(sentences: Sequence[dict]) -> Optional[str]:
    """A grounded mistake_fix title for a moment, or None (decline).

    The title is the mistake sentence's own warning clause: the
    imperative itself ("Stop spending your whole salary in the
    first week"), the named mistake ("The biggest mistake:
    memorising answers instead of understanding them"), or the
    wrongness clause with the speaker's "you're" scaffold removed
    ("Saving your money wrong"). Declines — returns None — when the
    moment raises no genuine mistake, when the built title would
    carry the speaker's first person, when it fails the style's own
    marker, or when any of its words are not the segment's words.
    """
    finding = find_mistake_fix(sentences)
    if finding is None:
        return None
    text = finding.mistake_text.rstrip(".")

    raw: Optional[str] = None
    clause = _imperative_clause(text)
    if clause is not None:
        raw = clause
    else:
        noun = re.match(
            r"(the\s+(?:biggest|big|common|huge|worst|first)\s+mistake"
            r"|the\s+mistake)\s+is\s+(.+)$",
            text,
            re.IGNORECASE,
        )
        if noun:
            raw = f"{noun.group(1)}: {noun.group(2)}"
        else:
            wrong = _DOING_WRONG_RE.search(text)
            if wrong:
                raw = f"{wrong.group(1)} wrong"
    if not raw:
        return None

    raw = raw.strip()
    raw = raw[0].upper() + raw[1:] if raw else raw
    title = _cap_title(raw)
    if not title:
        return None
    if _FIRST_PERSON_RE.search(title):
        return None
    from .hook_title_styles import title_matches_style

    if not title_matches_style(title, "mistake_fix"):
        return None
    if not _grounded(title, finding.mistake_text):
        return None
    return title


# --- Eligibility (D25, structural) -----------------------------------------------


def mistake_fix_eligibility(
    sentences: Sequence[dict], clip_duration: float
) -> Eligibility:
    """Whether a moment may be cut under the mistake_fix style.

    Eligible only when the moment raises a genuine mistake AND its
    fix starts inside the first third of the clip. The two failure
    shapes are the two the measured data condemns: no fix at all
    (the open loop, 1.40x) and a fix held back past the first third.
    Ineligibility here says nothing about the moment's worth for any
    other style.
    """
    duration = float(clip_duration or 0.0)
    finding = find_mistake_fix(sentences)
    if finding is None:
        return Eligibility(
            eligible=False,
            reasons=["no mistake is raised in this moment"],
            finding=None,
        )
    if finding.fix_start is None:
        return Eligibility(
            eligible=False,
            reasons=[
                "the mistake is raised but the moment never delivers its "
                "fix — the open-loop shape, excluded for mistake_fix (D25)"
            ],
            finding=finding,
        )
    if duration <= 0:
        return Eligibility(
            eligible=False,
            reasons=["clip duration is unknown, so the first-third rule cannot be judged"],
            finding=finding,
        )
    limit = duration / 3.0
    if finding.fix_start > limit:
        return Eligibility(
            eligible=False,
            reasons=[
                f"the fix lands at {finding.fix_start:.1f}s, past the first "
                f"third of the clip ({limit:.1f}s of {duration:.1f}s)"
            ],
            finding=finding,
        )
    return Eligibility(
        eligible=True,
        reasons=[
            f"mistake raised at {finding.mistake_start:.1f}s and its fix "
            f"lands at {finding.fix_start:.1f}s, inside the first third "
            f"({limit:.1f}s of {duration:.1f}s)"
        ],
        finding=finding,
    )


def resolve_mistake_fix_style(
    requested_style: Optional[str],
    profile: Optional[dict],
    sentences: Sequence[dict],
    clip_duration: float,
) -> tuple[str, Optional[str]]:
    """Resolve a requested title style for one moment.

    Returns ``(style_to_use, decline_reason)``. Any style other than
    ``mistake_fix`` passes through untouched — a moment rejected for
    mistake_fix stays eligible for the rest. ``mistake_fix`` itself
    is granted only when the profile offers it AND the moment is
    eligible; otherwise the profile's own default style is returned
    with the reason it declined, so the fallback is a decision on
    record, never a silent switch.
    """
    requested = str(requested_style or "")
    if requested != "mistake_fix":
        return requested, None
    from .hook_title_styles import mistake_fix_available

    hook = (profile or {}).get("hook") or {}
    default = str(hook.get("title_style") or "statement")
    if default == "mistake_fix":
        default = "statement"
    if not mistake_fix_available(profile):
        return default, "mistake_fix is not offered by this profile"
    verdict = mistake_fix_eligibility(sentences, clip_duration)
    if not verdict.eligible:
        return default, "; ".join(verdict.reasons)
    return "mistake_fix", None


def sentences_from_words(words: Sequence[dict]) -> list[dict]:
    """Clip-relative timed sentences from timed words.

    Mirrors the opening-strategy machinery: word dicts carry
    ``word``/``text`` + ``start``/``end``; sentence boundaries come
    from ``clip_openings.split_sentences``, the same rules the rest
    of the opening code uses.
    """
    from ..clip_openings import split_sentences

    norm = []
    for word in words or []:
        token = str(word.get("word") or word.get("text") or "").strip()
        if not token:
            continue
        try:
            start = float(word.get("start") or 0.0)
            end = float(word.get("end") or start)
        except (TypeError, ValueError):
            start, end = 0.0, 0.0
        norm.append({"text": token, "start": start, "end": end})
    if not norm:
        return []
    sentences = []
    for group in split_sentences([w["text"] for w in norm]):
        toks = [norm[i] for i in group]
        sentences.append(
            {
                "text": " ".join(w["text"] for w in toks),
                "start": float(toks[0]["start"]),
                "end": float(toks[-1]["end"]),
            }
        )
    return sentences
