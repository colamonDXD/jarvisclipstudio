"""Third-person guardrail for generated hook titles.

The app clips OTHER creators' videos: the person posting a clip is a
clipper sharing someone else's moment, never the speaker. The title
LLM copies the transcript's first person ("I met…", "I bought…", "my
routine"), which makes the clipper appear to claim the creator's
actions. Prompts ask for third person, but prompts are advice — this
module is the deterministic backstop that runs on every generated
title before it is stored or rendered.

``ensure_third_person_title`` is a pure function:

* a title with no first-person markers passes through UNCHANGED
  (byte-identical — clean titles are never mangled);
* a first-person title is rewritten deterministically: possessives
  and subjects are attributed to the creator when a name is known
  ("I Met a Millionaire" + "TJR" -> "TJR Met a Millionaire"), and
  otherwise reframed as a neutral headline fragment by dropping the
  first-person framing ("I Met a Millionaire" -> "Met a Millionaire");
* if no safe rewrite exists, a neutral descriptive title is derived
  from the moment's own words (``fallback_text`` — the segment/clip
  text); the first-person original is NEVER returned, and the result
  is never empty when any input text exists.
"""

from __future__ import annotations

import json
import re
from typing import Any, Optional

# --- detection -------------------------------------------------------------
#
# Word-boundary matching throughout, so "I" inside a word ("Incredible",
# "Island") never triggers. "us" is matched only when it is not the
# all-caps country abbreviation "US"; title-case "Us" IS the pronoun.

_CONTRACTION_RE = re.compile(r"\b[Ii]['’](m|ve|ll|d)\b")
_SUBJECT_I_RE = re.compile(r"(?<![\w'’])I(?![\w'’])")
_SUBJECT_WE_RE = re.compile(r"\b[Ww]e\b")
_POSSESSIVE_RE = re.compile(r"\b([Mm]y|[Oo]ur|[Mm]ine|[Oo]urs)\b")
_OBJECT_US_RE = re.compile(r"\b(U|u)s\b")  # not "US"
_OBJECT_ME_RE = re.compile(r"\b[Mm]e\b")
_SELF_RE = re.compile(r"\b([Mm]yself|[Oo]urselves)\b")

_FIRST_PERSON_RES = (
    _CONTRACTION_RE,
    _SUBJECT_I_RE,
    _SUBJECT_WE_RE,
    _POSSESSIVE_RE,
    _OBJECT_US_RE,
    _OBJECT_ME_RE,
    _SELF_RE,
)


def contains_first_person(title: Optional[str]) -> bool:
    """True when a title carries the speaker's first person."""
    if not title:
        return False
    return any(pattern.search(title) for pattern in _FIRST_PERSON_RES)


# --- rewrite tables --------------------------------------------------------

# Present-tense base forms whose third-person singular is irregular or
# ends in -es; a curated set of regular verbs takes a plain -s. Verbs
# outside both sets (past tense, modals, unknowns) read correctly after
# a name as-is, so they are left untouched.
_IRREGULAR_VERBS = {
    "am": "is",
    "are": "is",
    "have": "has",
    "do": "does",
    "go": "goes",
    "try": "tries",
    "fly": "flies",
    "watch": "watches",
    "teach": "teaches",
    "catch": "catches",
    "fix": "fixes",
    "pass": "passes",
    "miss": "misses",
    "push": "pushes",
    "wash": "washes",
    "say": "says",
    "pay": "pays",
    "buy": "buys",
    "play": "plays",
    "stay": "stays",
}
_REGULAR_VERBS = frozenset(
    {
        "answer", "ask", "build", "challenge", "check", "compare", "cook",
        "drive", "eat", "explore", "find", "get", "give", "hunt", "keep",
        "learn", "live", "lose", "make", "meet", "open", "rank", "rate",
        "react", "reveal", "review", "show", "spend", "start", "stop",
        "survive", "take", "test", "unbox", "visit", "win", "work",
    }
)

# Verbs that take an object pronoun in hook titles ("Watch Me Cook"):
# without a creator name the pronoun drops and the verb goes gerund so
# the fragment still reads as a headline ("Watching the…"), never as
# the poster's own claim.
_OBJECT_VERB_GERUNDS = {
    "watch": "Watching",
    "see": "Seeing",
    "join": "Joining",
    "follow": "Following",
    "meet": "Meeting",
    "hear": "Hearing",
}

_STOPWORDS = frozenset(
    {
        "a", "an", "the", "and", "or", "but", "so", "to", "of", "in", "on",
        "at", "for", "with", "from", "by", "is", "are", "was", "were", "be",
        "been", "it", "its", "this", "that", "these", "those", "there",
        "here", "just", "really", "very", "like", "you", "your", "he",
        "she", "they", "them", "his", "her", "their", "him", "what", "when",
        "how", "why", "who", "all", "one", "two", "about", "into", "over",
        "then", "than", "too", "as", "if", "not", "no", "yes",
    }
)

_GENERIC_FALLBACK = "Must-Watch Moment"
_MAX_WORDS = 10


def _clean_creator(creator_name: Optional[str]) -> Optional[str]:
    if not creator_name:
        return None
    name = " ".join(str(creator_name).split()).strip()[:40].strip()
    if not name or contains_first_person(name):
        return None
    return name


def clean_creator_name(creator_name: Optional[str]) -> Optional[str]:
    """Normalise a creator/channel name for storage or attribution.

    Returns None for anything unusable (empty, or itself phrased in the
    first person), so callers can store/pass the result blindly.
    """
    return _clean_creator(creator_name)


def creator_name_from_metadata(metadata: Any) -> Optional[str]:
    """Read the creator name captured on a task's source metadata.

    The name is stored under ``creator_name`` in the task's
    ``source_metadata`` JSON at creation time (from the source's
    channel/uploader metadata). ``uploader``/``channel`` keys are
    accepted as fallbacks. Accepts the metadata as a dict or a JSON
    string; anything else (or a missing/unusable name) returns None,
    which callers treat as "creator unknown -> neutral titles".
    """
    if isinstance(metadata, str):
        try:
            metadata = json.loads(metadata)
        except (TypeError, ValueError):
            return None
    if not isinstance(metadata, dict):
        return None
    for key in ("creator_name", "uploader", "channel"):
        name = _clean_creator(metadata.get(key))
        if name:
            return name
    return None


def _possessive(name: str) -> str:
    return name + ("'" if name.endswith(("s", "S")) else "'s")


def _third_person_verb(word: str) -> str:
    """Best-effort 3rd-person singular for the word after a named subject."""
    lower = word.lower().strip(".,;:!?")
    if lower in _IRREGULAR_VERBS:
        fixed = _IRREGULAR_VERBS[lower]
    elif lower in _REGULAR_VERBS:
        fixed = lower + "s"
    else:
        return word
    if word[:1].isupper():
        fixed = fixed.capitalize()
    return fixed


def _fix_subject_verb_agreement(title: str, creator: str) -> str:
    """After "I/we -> <Creator>", fix the verb that followed the pronoun."""
    pattern = re.compile(r"(\b" + re.escape(creator) + r"\s+)([A-Za-z]+)")
    match = pattern.search(title)
    if not match:
        return title
    fixed = _third_person_verb(match.group(2))
    if fixed == match.group(2):
        return title
    return title[: match.start(2)] + fixed + title[match.end(2):]


def _neutral_keywords(text: Optional[str], limit: int = 6) -> list[str]:
    """Content words from the moment's own text, first-person-free."""
    if not text:
        return []
    words: list[str] = []
    for raw in re.findall(r"[A-Za-z0-9$][A-Za-z0-9$'’.-]*", text):
        word = raw.strip("'’.-")
        if not word:
            continue
        if contains_first_person(word) or word.lower() in _STOPWORDS:
            continue
        if word.lower() in {w.lower() for w in words}:
            continue
        words.append(word)
        if len(words) >= limit:
            break
    return words


def _title_case_keywords(words: list[str]) -> str:
    titled = []
    for word in words:
        if word[:1].isalpha() and not any(ch.isupper() for ch in word[1:]):
            titled.append(word[:1].upper() + word[1:])
        else:
            titled.append(word)
    return " ".join(titled)


def _fallback_title(
    original: str, fallback_text: Optional[str]
) -> str:
    """A neutral descriptive title from the moment's own words."""
    for source in (fallback_text, original):
        keywords = _neutral_keywords(source)
        if len(keywords) >= 2:
            return _title_case_keywords(keywords)
    return _GENERIC_FALLBACK


def ensure_third_person_title(
    title: Optional[str],
    creator_name: Optional[str] = None,
    fallback_text: Optional[str] = None,
) -> Optional[str]:
    """Return ``title`` guaranteed free of the speaker's first person.

    Clean titles pass through unchanged. First-person titles are
    rewritten (creator-attributed when ``creator_name`` is known,
    neutral otherwise); when no safe rewrite exists, a neutral title
    is derived from ``fallback_text``. None/empty input returns None.
    """
    if not title:
        return None
    original = " ".join(str(title).split()).strip()
    if not original:
        return None
    if not contains_first_person(original):
        return title if title == original else original

    creator = _clean_creator(creator_name)
    text = original

    # Contractions: "I'm X-ing" -> "<Creator> Is X-ing" / "X-ing".
    def _contraction(match: re.Match) -> str:
        if not creator:
            return ""
        return f"{creator} " + {
            "m": "Is",
            "ve": "Has",
            "ll": "Will",
            "d": "Would",
        }[match.group(1).lower()]

    text = _CONTRACTION_RE.sub(_contraction, text)

    # Possessives: "my/our" -> "<Creator>'s" / "the"; "mine/ours" alike.
    def _possessive_repl(match: re.Match) -> str:
        if creator:
            return _possessive(creator)
        return "the"

    text = _POSSESSIVE_RE.sub(_possessive_repl, text)
    text = _SELF_RE.sub(creator or "", text)

    # Subjects: "I"/"we" -> creator name (verb agreement fixed after),
    # or dropped to a neutral headline fragment.
    if creator:
        text = _SUBJECT_I_RE.sub(creator, text)
        text = _SUBJECT_WE_RE.sub(creator, text)
        text = _fix_subject_verb_agreement(text, creator)
    else:
        text = _SUBJECT_I_RE.sub("", text)
        text = _SUBJECT_WE_RE.sub("", text)

    # Objects: "Watch Me Cook" -> "Watch <Creator> Cook"; without a
    # creator, "Watch Me Cook" -> "Watching Cook" is broken, so the
    # lead verb goes gerund ("Watching …") or the pronoun just drops.
    if creator:
        text = _OBJECT_ME_RE.sub(creator, text)
        text = _OBJECT_US_RE.sub(creator, text)
    else:
        lead = re.match(r"\s*([A-Za-z]+)\s+", text)
        if lead and lead.group(1).lower() in _OBJECT_VERB_GERUNDS:
            gerund = _OBJECT_VERB_GERUNDS[lead.group(1).lower()]
            text = gerund + text[lead.end(1):]
        text = _OBJECT_ME_RE.sub("", text)
        text = _OBJECT_US_RE.sub("", text)

    text = " ".join(text.split()).strip(" ,;:-–—")
    if text:
        text = text[:1].upper() + text[1:]

    words = text.split()
    if len(words) > _MAX_WORDS:
        text = " ".join(words[:_MAX_WORDS]).rstrip(".,;:-–— ")
        words = text.split()

    # Strict gate: anything still first-person, or too little substance
    # left to describe the moment ("Did It"), is replaced by a neutral
    # title derived from the moment's own words.
    content_words = [w for w in words if w.lower().strip(".,;:!?") not in _STOPWORDS]
    if not text or contains_first_person(text) or len(content_words) < 2:
        return _fallback_title(original, fallback_text)
    return text
