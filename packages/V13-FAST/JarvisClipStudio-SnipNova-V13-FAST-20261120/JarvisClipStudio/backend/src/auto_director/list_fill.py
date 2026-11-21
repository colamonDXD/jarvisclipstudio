"""List-fill clips (Trend-Radar D15): the "empty list fills in as you
talk" format, finance only.

The measured basis (Volume, 15 Nov 2026, 623 posts / 334 accounts
beating their own usual reach >= 2x): 9 accounts using this format
measured a median 16x their usual views, top example "Top 10%
Income By Age (2026)" at 69x. Those are observations of OTHER
creators' videos — a hypothesis for SnipNova, not a proven
improvement. Only posting data (the A/B on identical footage, by
views) can prove it helps. Nothing here claims an audience outcome.
What it owns is the editorial shape, deterministically:

* EXTRACTION — a moment carries a list only when the speaker
  actually enumerates it: either an announcement ("Here are three
  money habits for a rich life") followed by ordinal-marked items
  ("First, ...", "Second, ...", "Third, ..."), or a ranked-table
  announcement ("Here is the top 10 percent income by age") followed
  by by-age rows ("By thirty, ...", "By forty, ..."). Every item's
  text is the speaker's own sentence with only the ordinal marker
  stripped — nothing is invented, paraphrased in, or completed from
  the topic. A tier chart (ranked tiers with no ordinal or age
  markers) is NOT detected: the module declines it rather than
  guessing tier rows.

* TITLES — ``build_list_fill_title`` builds the title from the
  announcement sentence and NOTHING else: every title token must
  appear in that sentence (a grounding check, enforced), the
  speaker's first person can never survive into it, and the result
  must pass the style's own deterministic marker. When the moment
  announces no list, the builder returns None — the style DECLINES
  rather than fabricating a list the speaker never made. Hooks are
  never misleading; that rule outranks the style.

* ELIGIBILITY — a moment is eligible for the style only when the
  enumeration it announces is the enumeration it delivers: the
  announced count must equal the extracted items (ordinal shape),
  every item must land inside the clip (the list COMPLETES on
  screen — a partial or never-completed list is ineligible), and the
  list must fit the overlay (2..5 rows; more would collide with the
  caption band). ``resolve_list_fill_style`` is the seam: it grants
  the style only to an eligible moment in a profile that offers it,
  and otherwise falls back to the profile's own default style with
  the reason recorded.

* RENDER — the list is a timed ASS overlay appended to the caption
  ASS file the pipeline already builds: one heading line (the title)
  for the whole clip, one empty slot line per item from second 0
  until the speaker reaches it, and one item line from its reveal
  time to the end of the clip. Reveal timing tracks the transcript
  (an item appears when the speaker reaches it), never a fixed
  schedule. ``append_list_fill_overlay`` is the seam; it refuses to
  append twice to the same file.

Sentences are clip-relative timed dicts ``{text, start, end}`` —
the same shape the opening-strategy machinery uses.
``sentences_from_words`` derives them from timed words with the
codebase's own sentence boundaries. (It mirrors
``mistake_fix.sentences_from_words`` deliberately: this branch is
self-contained on 5ea71bf, where that module does not exist yet.)
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional, Sequence

#: Title caps, mirroring src.ai's HOOK_TITLE_MAX_WORDS / _CHARS (the
#: analysis path's caps) so a deterministically built title obeys the
#: same house limits an AI-written one is trimmed to.
MAX_TITLE_WORDS = 10
MAX_TITLE_CHARS = 64

#: A list row is one spoken sentence, trimmed for the overlay. Rows
#: longer than this are cut at the word boundary (their words are all
#: still the speaker's own); the cap keeps a row to at most two
#: wrapped lines.
MAX_ITEM_WORDS = 9

#: Rows the overlay can show without colliding with the caption band
#: (rows start at 28% of frame height, one step apart). More rows
#: than this means the list does not fit the format — decline, do not
#: truncate a list the speaker announced in full.
MIN_ITEMS = 2
MAX_ITEMS = 5

#: The selection-template value the finance profile declares for this
#: format (D15). The profile field governs; the recorded assignment
#: by niche is the fallback for profiles that predate the field.
LIST_FILL_TEMPLATE = "list_fill_live"

# --- Count words ----------------------------------------------------------------

_COUNT_WORDS = {
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5,
    "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10,
}

#: Nouns a count announcement counts. The noun is what the title
#: names ("three money habits", "five rules"); the outcome after it
#: ("for a rich life") is what the title promises.
_LIST_NOUNS = (
    "things", "habits", "ways", "rules", "lessons", "steps",
    "mistakes", "reasons", "moves", "levels", "tiers", "ages",
    "incomes", "brackets", "signs", "tricks", "secrets", "keys",
)

_ANNOUNCEMENT_RE = re.compile(
    r"\b(one|two|three|four|five|six|seven|eight|nine|ten|\d+)\b"
    r"(?:\s+\w+){0,2}?\s+(" + "|".join(_LIST_NOUNS) + r")\b",
    re.IGNORECASE,
)

#: A ranked-table announcement: "top 10 percent income by age",
#: "top 10% earners by age". The percentile is a rank, not a count —
#: the count is the number of age rows that follow.
_AGE_ANNOUNCEMENT_RE = re.compile(
    r"\btop\s+(\d+)\s*(%|percent)\b.*\bage\b",
    re.IGNORECASE,
)

# --- Ordinal markers ------------------------------------------------------------

_ORDINAL_WORDS = {
    "first": 1, "second": 2, "third": 3, "fourth": 4, "fifth": 5,
    "sixth": 6, "seventh": 7,
}

_ORDINAL_PREFIX_RE = re.compile(
    r"^(first|second|third|fourth|fifth|sixth|seventh)\s*[,:\-–—]?\s+",
    re.IGNORECASE,
)
_NUMBER_PREFIX_RE = re.compile(
    r"^number\s+(one|two|three|four|five|six|seven)\s*(?:is|:|,)?\s+",
    re.IGNORECASE,
)
_STEP_PREFIX_RE = re.compile(
    r"^step\s+(one|two|three|four|five|six|seven|\d+)\s*[,:\-–—]?\s+",
    re.IGNORECASE,
)
_DIGIT_PREFIX_RE = re.compile(r"^(\d+)[\.\)]\s+")

#: A by-age row: "By thirty, ...", "By age 40, ...". The age must be
#: spoken as a word or digits; the row keeps its "By <age>" key.
_AGE_ROW_RE = re.compile(
    r"^by\s+(?:age\s+)?"
    r"(twenty|thirty|forty|fifty|sixty|seventy|\d+)\s*[,:\-–—]?\s+",
    re.IGNORECASE,
)
_AGE_WORD_VALUES = {
    "twenty": 20, "thirty": 30, "forty": 40, "fifty": 50,
    "sixty": 60, "seventy": 70,
}

_ANNOUNCEMENT_LEADS = (
    "here are", "here is", "these are", "those are",
)

_FIRST_PERSON_RE = re.compile(r"\b(i|i'm|im|i've|my|mine|we|our|us)\b", re.IGNORECASE)

# Trailing words a word-boundary cap may leave dangling; a title never
# ends on one of these.
_TRAILING_STOPWORDS = frozenset(
    {"of", "the", "a", "an", "in", "on", "to", "for", "and", "or", "is", "it"}
)


@dataclass
class ListItem:
    """One enumerated item, in the speaker's own words."""

    index: int
    text: str
    #: Clip-relative seconds when the speaker reaches this item — the
    #: moment its row fills in on screen.
    reveal_start: float
    reveal_end: float
    source_text: str


@dataclass
class Enumeration:
    """A list the speaker actually enumerates in a moment."""

    kind: str  # "ordinal" | "age_table"
    announcement_text: str
    announcement_start: float
    announced_count: Optional[int]  # None for age tables (no count)
    items: list[ListItem] = field(default_factory=list)


@dataclass
class ListFillPlan:
    """The timed overlay for one eligible list_fill moment."""

    title: str
    kind: str
    items: list[ListItem]
    clip_duration: float


@dataclass
class Eligibility:
    """Whether a moment may be cut under the list_fill style."""

    eligible: bool
    reasons: list[str] = field(default_factory=list)
    plan: Optional[ListFillPlan] = None


def _norm(text: str) -> str:
    return " ".join(str(text or "").split())


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
    announcement can re-order nothing and introduce nothing — if a
    token is not in the sentence, the title is not this segment's
    title.
    """
    source = set(_tokens(source_text))
    return all(tok in source for tok in _tokens(title) if tok)


def _count_value(token: str) -> Optional[int]:
    lowered = token.lower()
    if lowered in _COUNT_WORDS:
        return _COUNT_WORDS[lowered]
    if lowered.isdigit():
        return int(lowered)
    return None


def _strip_announcement_lead(text: str) -> str:
    lowered = text.lower()
    for lead in _ANNOUNCEMENT_LEADS:
        if lowered.startswith(lead + " "):
            return text[len(lead) + 1 :]
    return text


def _ordinal_value(sentence: str) -> Optional[tuple[int, str]]:
    """(ordinal value, item text) when a sentence opens with one.

    The ordinal prefix is stripped; what remains is the item, in the
    speaker's own words. Returns None when the sentence does not open
    with an ordinal marker.
    """
    text = _norm(sentence)
    match = _ORDINAL_PREFIX_RE.match(text)
    if match:
        return _ORDINAL_WORDS[match.group(1).lower()], text[match.end():]
    match = _NUMBER_PREFIX_RE.match(text)
    if match:
        return _count_value(match.group(1)), text[match.end():]
    match = _STEP_PREFIX_RE.match(text)
    if match:
        return _count_value(match.group(1)), text[match.end():]
    match = _DIGIT_PREFIX_RE.match(text)
    if match:
        return int(match.group(1)), text[match.end():]
    return None


def _age_value(sentence: str) -> Optional[tuple[int, str]]:
    """(age, item text) when a sentence is a by-age row.

    The row keeps its "By <age>" key — "By thirty, have one year of
    salary saved" becomes "By thirty: have one year of salary saved".
    """
    text = _norm(sentence)
    match = _AGE_ROW_RE.match(text)
    if not match:
        return None
    raw = match.group(1).lower()
    age = _AGE_WORD_VALUES.get(raw)
    if age is None:
        try:
            age = int(raw)
        except ValueError:
            return None
    rest = text[match.end():]
    return age, f"By {raw}: {rest}"


def _trim_item(text: str) -> str:
    """One overlay row from an item sentence: trimmed, capitalised.

    Only the word cap applies here — unlike a title, an item row
    keeps its final word even when it is "it" ("spend it" is the
    object, not a dangling word), so no trailing-stopword rule runs.
    """
    words = _norm(text).rstrip(".").split()
    if len(words) > MAX_ITEM_WORDS:
        words = words[:MAX_ITEM_WORDS]
    trimmed = " ".join(words)
    return trimmed[0].upper() + trimmed[1:] if trimmed else trimmed


def _norm_sentences(sentences: Sequence[dict]) -> list[dict]:
    norm = [
        {
            "text": _norm(s.get("text", "")),
            "start": float(s.get("start") or 0.0),
            "end": float(s.get("end") or 0.0),
        }
        for s in sentences or []
    ]
    return [s for s in norm if s["text"]]


# --- Extraction ------------------------------------------------------------------


def find_enumeration(sentences: Sequence[dict]) -> Optional[Enumeration]:
    """The list a moment enumerates, or None when it enumerates none.

    Two shapes only, both from the speaker's own words. Ordinal: a
    count announcement followed by ordinal-marked items, in exact
    1..N order — a skipped ordinal means an item the camera (or the
    transcript) missed, so the enumeration is broken, not partial.
    Age table: a "top N% ... by age" announcement followed by by-age
    rows in ascending age order. Anything else — a topic list the
    speaker never counts, a tier chart with no markers — is not an
    enumeration here: the style declines rather than guessing rows.
    """
    norm = _norm_sentences(sentences)
    if not norm:
        return None
    for index, sentence in enumerate(norm):
        text = sentence["text"]
        announced = _ANNOUNCEMENT_RE.search(text)
        if announced:
            count = _count_value(announced.group(1))
            tail = text[announced.end():]
            # The announcement must carry an outcome ("for ...",
            # "to ...", "that ...") — a bare count of things with no
            # promise is not the format's hook.
            if re.search(r"\b(for|to|that)\b", tail):
                items: list[ListItem] = []
                broken = False
                for later in norm[index + 1 :]:
                    found = _ordinal_value(later["text"])
                    if found is None:
                        continue
                    value, item_text = found
                    if value != len(items) + 1:
                        broken = True
                        break
                    items.append(
                        ListItem(
                            index=len(items) + 1,
                            text=_trim_item(item_text),
                            reveal_start=later["start"],
                            reveal_end=later["end"],
                            source_text=later["text"],
                        )
                    )
                if not broken and items:
                    return Enumeration(
                        kind="ordinal",
                        announcement_text=text,
                        announcement_start=sentence["start"],
                        announced_count=count,
                        items=items,
                    )
                # Otherwise fall through: the same sentence may
                # announce an age table instead.
        if _AGE_ANNOUNCEMENT_RE.search(text):
            items = []
            last_age = 0
            broken = False
            for later in norm[index + 1 :]:
                found = _age_value(later["text"])
                if found is None:
                    continue
                age, item_text = found
                if age <= last_age:
                    broken = True
                    break
                last_age = age
                items.append(
                    ListItem(
                        index=len(items) + 1,
                        text=_trim_item(item_text),
                        reveal_start=later["start"],
                        reveal_end=later["end"],
                        source_text=later["text"],
                    )
                )
            if not broken and items:
                return Enumeration(
                    kind="age_table",
                    announcement_text=text,
                    announcement_start=sentence["start"],
                    announced_count=None,
                    items=items,
                )
    return None


# --- Title building --------------------------------------------------------------


def build_list_fill_title(sentences: Sequence[dict]) -> Optional[str]:
    """A grounded list_fill title for a moment, or None (decline).

    The title is the announcement sentence with its "Here are"
    lead stripped: "Three money habits for a rich life",
    "Top 10 percent income by age". Declines — returns None — when
    the moment announces no list, when the built title would carry
    the speaker's first person, when it fails the style's own
    marker, or when any of its words are not the announcement's
    words.
    """
    found = find_enumeration(sentences)
    if found is None:
        return None
    raw = _strip_announcement_lead(found.announcement_text.rstrip("."))
    raw = raw.strip()
    raw = raw[0].upper() + raw[1:] if raw else raw
    title = _cap_title(raw)
    if not title:
        return None
    if _FIRST_PERSON_RE.search(title):
        return None
    from .hook_title_styles import title_matches_style

    if not title_matches_style(title, "list_fill"):
        return None
    if not _grounded(title, found.announcement_text):
        return None
    return title


# --- Eligibility -------------------------------------------------------------------


def list_fill_eligibility(
    sentences: Sequence[dict], clip_duration: float
) -> Eligibility:
    """Whether a moment may be cut under the list_fill style.

    Eligible only when the moment enumerates a list AND the
    enumeration it delivers is the one it announced: the announced
    count must equal the extracted items (ordinal shape), the list
    must fit the overlay (2..6 rows), and the last item must end
    inside the clip — the list COMPLETES on screen. A partial list,
    a list that runs past the end of the clip, and a list the speaker
    never enumerates are all ineligible for ``list_fill`` while
    remaining perfectly eligible for every other style.
    """
    duration = float(clip_duration or 0.0)
    found = find_enumeration(sentences)
    if found is None:
        return Eligibility(
            eligible=False,
            reasons=["no listable enumerated content in this moment"],
            plan=None,
        )
    if found.kind == "ordinal":
        if found.announced_count is None:
            return Eligibility(
                eligible=False,
                reasons=["the announcement carries no count to match against"],
                plan=None,
            )
        if len(found.items) != found.announced_count:
            return Eligibility(
                eligible=False,
                reasons=[
                    f"the speaker announces {found.announced_count} items "
                    f"but only {len(found.items)} are enumerated in the "
                    "moment — a partial list never fills in"
                ],
                plan=None,
            )
    if len(found.items) < MIN_ITEMS:
        return Eligibility(
            eligible=False,
            reasons=[
                f"only {len(found.items)} item is enumerated — a list "
                "needs at least two rows"
            ],
            plan=None,
        )
    if len(found.items) > MAX_ITEMS:
        return Eligibility(
            eligible=False,
            reasons=[
                f"{len(found.items)} items are enumerated, more than the "
                f"{MAX_ITEMS} rows the overlay can show without colliding "
                "with the captions"
            ],
            plan=None,
        )
    if duration <= 0:
        return Eligibility(
            eligible=False,
            reasons=["clip duration is unknown, so completion cannot be judged"],
            plan=None,
        )
    last = found.items[-1]
    if last.reveal_end > duration:
        return Eligibility(
            eligible=False,
            reasons=[
                f"the last item ends at {last.reveal_end:.1f}s, past the "
                f"end of the {duration:.1f}s clip — the list never completes"
            ],
            plan=None,
        )
    title = build_list_fill_title(sentences)
    if title is None:
        return Eligibility(
            eligible=False,
            reasons=["no grounded list title can be built from the announcement"],
            plan=None,
        )
    return Eligibility(
        eligible=True,
        reasons=[
            f"{len(found.items)} items enumerated and completed by "
            f"{last.reveal_end:.1f}s of {duration:.1f}s"
        ],
        plan=ListFillPlan(
            title=title,
            kind=found.kind,
            items=found.items,
            clip_duration=duration,
        ),
    )


def build_list_fill_plan(
    sentences: Sequence[dict], clip_duration: float
) -> Optional[ListFillPlan]:
    """The timed overlay plan for a moment, or None (decline)."""
    verdict = list_fill_eligibility(sentences, clip_duration)
    return verdict.plan if verdict.eligible else None


def resolve_list_fill_style(
    requested_style: Optional[str],
    profile: Optional[dict],
    sentences: Sequence[dict],
    clip_duration: float,
) -> tuple[str, Optional[str]]:
    """Resolve a requested title style for one moment.

    Returns ``(style_to_use, decline_reason)``. Any style other than
    ``list_fill`` passes through untouched — a moment rejected for
    list_fill stays eligible for the rest. ``list_fill`` itself is
    granted only when the profile offers it AND the moment is
    eligible; otherwise the profile's own default style is returned
    with the reason it declined, so the fallback is a decision on
    record, never a silent switch.
    """
    requested = str(requested_style or "")
    if requested != "list_fill":
        return requested, None
    from .hook_title_styles import list_fill_available

    hook = (profile or {}).get("hook") or {}
    default = str(hook.get("title_style") or "statement")
    if default == "list_fill":
        default = "statement"
    if not list_fill_available(profile):
        return default, "list_fill is not offered by this profile"
    verdict = list_fill_eligibility(sentences, clip_duration)
    if not verdict.eligible:
        return default, "; ".join(verdict.reasons)
    return "list_fill", None


def list_fill_template_available(profile: Optional[dict]) -> bool:
    """Whether a profile declares the list_fill_live template (D15).

    The shipped profiles record their selection templates under the
    top-level ``selection_templates`` list; that list governs when
    present. A profile without the list falls back to the recorded
    D15 assignment by niche name. A profile whose list exists but
    omits the template does NOT offer it.
    """
    if not profile:
        return False
    templates = profile.get("selection_templates")
    if isinstance(templates, (list, tuple)):
        return LIST_FILL_TEMPLATE in templates
    from .hook_title_styles import LIST_FILL_PROFILES

    return str(profile.get("niche") or "") in LIST_FILL_PROFILES


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


# --- Render: the timed ASS overlay ---------------------------------------------------

#: The overlay's rows sit in the upper half of the frame, above the
#: caption band: heading at 15% of frame height, rows from 28%,
#: one fixed step apart. The step is height-relative so the layout
#: scales across render sizes. Rows are word-wrapped to at most two
#: lines each (a row is a display trim of a sentence the captions
#: carry in full, so a row never runs off the frame) — the step
#: leaves room for two lines, so a five-row list of long rows can
#: reach the caption band; the face guard does not (yet) move the
#: list, which is a known candidate limitation, not a silent rule.
_HEADING_Y_FRAC = 0.15
_ITEMS_TOP_FRAC = 0.28
_ROW_STEP_FRAC = 0.10
_WRAP_COLS = 36
_SLOT_MARK = "—"


def _wrap_row_text(text: str) -> str:
    """Word-wrap a row to at most two lines of ``_WRAP_COLS`` chars.

    Greedy word wrap; a row that would still need a third line is
    cut at the word boundary after the second. The full sentence is
    always in the captions, so the cut loses nothing the viewer
    cannot hear — but the row itself never runs off the frame (a
    49-character row rendered on one line did exactly that in the
    first rendered proof).
    """
    words = str(text or "").split()
    lines: list[str] = []
    current = ""
    for word in words:
        trial = f"{current} {word}".strip() if current else word
        if len(trial) <= _WRAP_COLS:
            current = trial
        else:
            if current:
                lines.append(current)
            current = word
            if len(lines) == 2:
                break
    if current and len(lines) < 2:
        lines.append(current)
    return "\\N".join(lines[:2])


def _ass_time(seconds: float) -> str:
    total = max(0.0, float(seconds))
    hours = int(total // 3600)
    minutes = int((total % 3600) // 60)
    secs = total % 60
    return f"{hours}:{minutes:02d}:{secs:05.2f}"


def _escape_ass_text(text: str) -> str:
    return (
        str(text or "")
        .replace("\\", "\\\\")
        .replace("{", "\\{")
        .replace("}", "\\}")
        .replace("\n", "\\N")
    )


def _ass_line_text(text: str) -> str:
    """ASS text for one wrapped row: wrap first, escape each line.

    The wrap's ``\\N`` line break must survive into the file as a
    literal backslash-N — escaping the joined text would turn it
    into ``\\\\N`` (a stray backslash on screen), so each line is
    escaped separately and the breaks are added after.
    """
    return "\\N".join(
        _escape_ass_text(part) for part in _wrap_row_text(text).split("\\N")
    )


def list_fill_ass_events(
    plan: ListFillPlan, video_width: int, video_height: int, font_name: str = "Arial"
) -> tuple[str, list[str]]:
    """The ASS style line + Dialogue lines for a list_fill overlay.

    One heading line (the title) spans the whole clip; each item has
    an empty slot line from second 0 until the speaker reaches it,
    and an item line from its reveal time to the end of the clip.
    Positions are computed from the frame size so the layout scales.
    The caller appends these to the caption ASS file the pipeline
    already builds (see ``append_list_fill_overlay``).
    """
    width = int(video_width or 540)
    height = int(video_height or 960)
    font_px = max(28, round(38 * width / 540))
    x = width // 2
    heading_y = round(_HEADING_Y_FRAC * height)
    items_top = round(_ITEMS_TOP_FRAC * height)
    step = round(_ROW_STEP_FRAC * height)
    end = _ass_time(plan.clip_duration)

    style_line = (
        f"Style: ListFill,{font_name},{font_px},&H00FFFFFF,&H000000FF,"
        f"&H00000000,&H00000000,1,0,0,0,100,100,0,0,1,2,0,8,60,60,60,1"
    )
    events: list[str] = []
    events.append(
        "Dialogue: 0,"
        f"{_ass_time(0.0)},{end},ListFill,,0,0,0,,"
        f"{{\\pos({x},{heading_y})}}"
        f"{_ass_line_text(plan.title)}"
    )
    for item in plan.items:
        y = items_top + (item.index - 1) * step
        slot_text = f"{item.index}. {_SLOT_MARK}"
        events.append(
            "Dialogue: 0,"
            f"{_ass_time(0.0)},{_ass_time(item.reveal_start)},ListFill,,0,0,0,,"
            f"{{\\pos({x},{y})}}{_escape_ass_text(slot_text)}"
        )
        events.append(
            "Dialogue: 0,"
            f"{_ass_time(item.reveal_start)},{end},ListFill,,0,0,0,,"
            f"{{\\pos({x},{y})}}"
            f"{_ass_line_text(f'{item.index}. {item.text}')}"
        )
    return style_line, events


def append_list_fill_overlay(
    ass_path: str | Path,
    plan: ListFillPlan,
    video_width: int,
    video_height: int,
) -> bool:
    """Append a list_fill overlay to an existing caption ASS file.

    Inserts the ``ListFill`` style into the [V4+ Styles] section
    (font taken from the file's own Hook style, else its Default
    style) and appends the overlay Dialogue lines to [Events].
    Returns True when appended; False when the file already carries
    a ListFill style (never append twice) or has no [Events]
    section to append to.
    """
    path = Path(ass_path)
    text = path.read_text(encoding="utf-8")
    if "ListFill" in text:
        return False
    if "[Events]" not in text:
        return False
    font_name = "Arial"
    for prefix in ("Style: Hook,", "Style: Default,"):
        for line in text.splitlines():
            if line.startswith(prefix):
                parts = line.split(",")
                if len(parts) > 1 and parts[1].strip():
                    font_name = parts[1].strip()
                break
        if font_name != "Arial":
            break
    style_line, events = list_fill_ass_events(
        plan, video_width, video_height, font_name
    )
    lines = text.splitlines()
    out: list[str] = []
    inserted = False
    for line in lines:
        out.append(line)
        if not inserted and line.startswith("Format: Name, Fontname,"):
            out.append(style_line)
            inserted = True
    if not inserted:
        return False
    out.extend(events)
    path.write_text("\n".join(out) + "\n", encoding="utf-8")
    return True
