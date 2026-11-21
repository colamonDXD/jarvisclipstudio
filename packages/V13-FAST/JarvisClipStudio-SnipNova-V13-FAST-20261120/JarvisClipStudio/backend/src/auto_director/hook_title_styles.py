"""Hook title styles: the registry, prompt guidance, and style markers.

A profile's ``hook.title_style`` names how a clip's on-screen title
should be worded. The shipped styles are ``statement``, ``question``,
``statistic``, ``contrast`` and ``story``. Trend-Radar candidate D1
(2026-11-09) adds two more, additively:

* ``problem`` — the title names the concrete problem the segment
  addresses (a cost, a mistake, a risk, something broken or wasted).
* ``curiosity_gap`` — the title names the subject and deliberately
  withholds the one specific fact the segment itself reveals, so the
  gap the title opens is a gap the footage closes.

The candidate assignment (problem: finance_business,
education_tutorial; curiosity_gap: podcast_news, true_crime,
hindi_podcast) was A/B-tested on the quality milestone's regression
fixture and ADOPTED on 2026-11-09: the shipped profiles now carry
exactly the assignment recorded in ``PROPOSED_CANDIDATE_DEFAULTS``
below (the name is kept from the proposal phase; a unit test pins the
map against the shipped profiles so the defaults cannot drift
silently). The styles also take effect wherever a user-tuned or
reference-derived profile selects them. The radar's reason for testing them is recorded in the
workstream report; nothing in this module claims or implies an
audience outcome for any style — the verdict that matters here is
editorial: a title in either new style must still be grounded in, and
delivered by, its own segment (the retention honesty guard judges it
exactly like any other hook title).

Trend-Radar D16 (2026-11-16) adds one more style, additively:

* ``mistake_fix`` — the title names the mistake the segment itself
  raises (the thing the speaker tells viewers to stop doing, avoid,
  or quit getting wrong), and the segment delivers the fix inside
  its first third. D25's correction is structural for this style:
  an open loop (the fix held back) is never a valid shape for it —
  ``auto_director.mistake_fix`` owns the detection, the grounded
  title builder and the first-third eligibility rule.

Unlike the D1 pair, ``mistake_fix`` is NOT any profile's default: it
is an AVAILABLE value in the profiles recorded in
``MISTAKE_FIX_PROFILES`` (their shipped ``hook`` blocks list it under
``available_title_styles``), proposed for A/B testing on the same
footage against the shipped defaults. The measured basis (Blossom,
14 Nov 2026) is correlation in a large library, not a promise: alone
the shape measured average, with beat-timed cuts strong, with an
open loop actively harmful. Nothing here claims an audience outcome.

Trend-Radar D15 (2026-11-16) adds one more style, additively:

* ``list_fill`` — the title is the list's own heading: "[Number]
  [things/ages/tiers] for [outcome]", built from the count the
  segment itself announces, and the clip fills the numbered list on
  screen line by line as the speaker enumerates it.
  ``auto_director.list_fill`` owns the extraction, the grounded title
  builder and the completion eligibility rule.

Unlike the D1 pair, ``list_fill`` is NOT any profile's default: it is
an AVAILABLE value in the profiles recorded in
``LIST_FILL_PROFILES`` (their shipped ``hook`` blocks list it under
``available_title_styles``), proposed for A/B testing on the same
footage against the shipped defaults. The measured basis (Volume,
15 Nov 2026: 9 accounts, median 16x their usual views; top example
69x) is an observation of OTHER creators' videos, not a promise: it
is a hypothesis for SnipNova, and only posting data on identical
footage can say whether it helps. Nothing here claims an audience
outcome.

The deterministic markers in ``title_matches_style`` are deliberately
simple and are used for A/B scoring and tests: does this title actually
state a problem / actually open a gap, in its own words?

Trend-Radar D17 (2026-11-16) adds one more style, additively:

* ``trailer_breakdown`` — the title is the format's hook formula
  ("You missed this in the first 3 seconds…"): the clip plays a few
  seconds of a trailer, freezes on a background detail the viewer
  missed, circles it, and cuts to the creator's face-cam reaction.
  The style is NOT any profile's default: it is an AVAILABLE value
  in the profiles recorded in ``TRAILER_BREAKDOWN_PROFILES`` (their
  shipped ``hook`` blocks list it under ``available_title_styles``),
  proposed for A/B testing on the same footage against the shipped
  defaults. The evidence is practitioner-only (HookMafia, crawled
  15–16 Nov 2026; no view counts) and the trailer window it rides
  on (final Avengers: Doomsday trailer, expected 16–22 Nov, most
  likely ~19 Nov) is a rumour, not an announcement — nothing here
  claims an audience outcome. ``auto_director.trailer_breakdown``
  owns the detection, the grounded detail spot, the eligibility rule
  and the freeze/zoom/circle render seam.
"""

from __future__ import annotations

import re
from typing import Optional

#: Every hook title style the system knows (existing + D1 candidates
#: + the D15/D16/D17 candidates).
HOOK_TITLE_STYLES = (
    "statement",
    "question",
    "statistic",
    "contrast",
    "story",
    "problem",
    "curiosity_gap",
    "mistake_fix",
    "list_fill",
    "trailer_breakdown",
)

#: The D1 candidate styles.
CANDIDATE_TITLE_STYLES = ("problem", "curiosity_gap")

#: The D16 candidate style (Trend-Radar 2026-11-16).
MISTAKE_FIX_TITLE_STYLE = "mistake_fix"

#: D16 candidate assignment: the niches whose profiles OFFER
#: ``mistake_fix`` as an available ``hook.title_style`` value (listed
#: under ``hook.available_title_styles`` in the shipped profiles).
#: It is deliberately NOT a default anywhere — the shipped defaults
#: stay as ``PROPOSED_CANDIDATE_DEFAULTS`` recorded them — until an
#: A/B verdict on identical footage says otherwise.
MISTAKE_FIX_PROFILES = ("finance_business", "education_tutorial")
#: The D15 candidate style (Trend-Radar 2026-11-16).
LIST_FILL_TITLE_STYLE = "list_fill"

#: D15 candidate assignment: the niches whose profiles OFFER
#: ``list_fill`` as an available ``hook.title_style`` value (listed
#: under ``hook.available_title_styles`` in the shipped profiles).
#: It is deliberately NOT a default anywhere — the shipped defaults
#: stay as ``PROPOSED_CANDIDATE_DEFAULTS`` recorded them — until an
#: A/B verdict on identical footage says otherwise.
LIST_FILL_PROFILES = ("finance_business",)

#: The D17 candidate style (Trend-Radar 2026-11-16).
TRAILER_BREAKDOWN_TITLE_STYLE = "trailer_breakdown"

#: D17 candidate assignment: the niches whose profiles OFFER
#: ``trailer_breakdown`` as an available ``hook.title_style`` value
#: (listed under ``hook.available_title_styles`` in the shipped
#: profiles). It is deliberately NOT a default anywhere — the
#: shipped defaults stay as ``PROPOSED_CANDIDATE_DEFAULTS``
#: recorded them — until an A/B verdict on identical footage says
#: otherwise.
TRAILER_BREAKDOWN_PROFILES = ("gaming",)

#: Candidate assignment (D1): niche -> candidate style, adopted as the
#: shipped defaults on the 2026-11-09 A/B verdict. A unit test pins
#: this map against the shipped profiles so the defaults can never
#: drift silently.
PROPOSED_CANDIDATE_DEFAULTS = {
    "finance_business": "problem",
    "education_tutorial": "problem",
    "podcast_news": "curiosity_gap",
    "true_crime": "curiosity_gap",
    "hindi_podcast": "curiosity_gap",
}

TITLE_STYLE_GUIDANCE: dict[str, str] = {
    "statement": "State the segment's strongest claim plainly, in the segment's own terms.",
    "question": "Ask the exact question the segment answers; do not ask one it leaves open.",
    "statistic": "Lead with a specific number the segment actually states or shows.",
    "contrast": "Set the two sides the segment itself contrasts (before/after, cheap/expensive, expected/actual).",
    "story": "Name the moment as a small story beat: who did what, in the segment's own terms.",
    "problem": (
        "Name the concrete problem this segment addresses — a cost, a mistake, a risk, "
        "something broken, wasted or overpaid — using only facts the segment states. "
        "A problem the segment never shows is not a title, it is a defect."
    ),
    "curiosity_gap": (
        "Name the subject and withhold the ONE specific fact this segment reveals "
        "(the ingredient, the amount, the reason, the outcome), so the viewer has to "
        "watch to close the gap. Never withhold something the segment does not "
        "actually deliver, and never hide the subject itself."
    ),
    "mistake_fix": (
        "Name the mistake this segment itself raises — the thing the speaker "
        "tells viewers to stop doing, avoid, or quit getting wrong — in the "
        "segment's own terms, and only when the segment ALSO delivers the fix "
        "inside its first third. A mistake the speaker never raises is not a "
        "title, it is a defect; and never hold the fix back past the first "
        "third — an open loop is the one shape this style must never take."
    ),
    "list_fill": (
        "Name the list this segment itself enumerates — \"[Number] [things/ages/tiers] "
        "for [outcome]\" — using only the count and the words the segment announces, "
        "and only when the segment lists every item it announced inside the clip, "
        "so the on-screen list can complete. A list the speaker never enumerates "
        "is not a title, it is a defect; never invent an item the speaker did not say."
    ),
    "trailer_breakdown": (
        "Use the format's hook formula — you missed a detail in the "
        "first seconds of this trailer — and only when the segment "
        "really is a trailer breakdown: a few seconds of trailer, a "
        "freeze on a background detail the footage itself contains, "
        "a circle on that detail, and the creator's face-cam "
        "reaction. A detail the frame does not contain is not a "
        "title, it is a defect."
    ),
}


def title_style_guidance(style: Optional[str]) -> str:
    """Prompt guidance for a title style ('' for an unknown style)."""
    return TITLE_STYLE_GUIDANCE.get(str(style or ""), "")


#: Which opening strategy (hook_variants.STRATEGY_KINDS) naturally
#: carries each title style, when that strategy is honestly available
#: for the clip. A mapping, not a rule: availability still decides.
OPENING_STRATEGY_FOR_TITLE_STYLE: dict[str, str] = {
    "statement": "strongest_statement",
    "question": "question",
    "statistic": "strongest_statement",
    "contrast": "before_the_event",
    "story": "before_the_event",
    "problem": "strongest_statement",
    "curiosity_gap": "question",
    "mistake_fix": "strongest_statement",
    "list_fill": "strongest_statement",
    "trailer_breakdown": "strongest_statement",
}


def opening_strategy_for_title_style(style: Optional[str]) -> Optional[str]:
    """The opening strategy that carries a title style, or None."""
    return OPENING_STRATEGY_FOR_TITLE_STYLE.get(str(style or ""))


def mistake_fix_available(profile: Optional[dict]) -> bool:
    """Whether a profile offers ``mistake_fix`` as a title style (D16).

    The shipped profiles record their available styles under
    ``hook.available_title_styles``; that list governs when present.
    A profile without the list (derived before the field existed)
    falls back to the recorded D16 assignment by niche name. A
    profile whose list exists but omits the style does NOT offer it —
    availability is explicit, never inferred from the default style.
    """
    if not profile:
        return False
    hook = profile.get("hook") or {}
    available = hook.get("available_title_styles")
    if isinstance(available, (list, tuple)):
        return MISTAKE_FIX_TITLE_STYLE in available
    return str(profile.get("niche") or "") in MISTAKE_FIX_PROFILES


def list_fill_available(profile: Optional[dict]) -> bool:
    """Whether a profile offers ``list_fill`` as a title style (D15).

    The shipped profiles record their available styles under
    ``hook.available_title_styles``; that list governs when present.
    A profile without the list (derived before the field existed)
    falls back to the recorded D15 assignment by niche name. A
    profile whose list exists but omits the style does NOT offer it —
    availability is explicit, never inferred from the default style.
    """
    if not profile:
        return False
    hook = profile.get("hook") or {}
    available = hook.get("available_title_styles")
    if isinstance(available, (list, tuple)):
        return LIST_FILL_TITLE_STYLE in available
    return str(profile.get("niche") or "") in LIST_FILL_PROFILES


def trailer_breakdown_available(profile: Optional[dict]) -> bool:
    """Whether a profile offers ``trailer_breakdown`` as a title style (D17).

    The shipped profiles record their available styles under
    ``hook.available_title_styles``; that list governs when present.
    A profile without the list (derived before the field existed)
    falls back to the recorded D17 assignment by niche name. A
    profile whose list exists but omits the style does NOT offer it —
    availability is explicit, never inferred from the default style.
    """
    if not profile:
        return False
    hook = profile.get("hook") or {}
    available = hook.get("available_title_styles")
    if isinstance(available, (list, tuple)):
        return TRAILER_BREAKDOWN_TITLE_STYLE in available
    return str(profile.get("niche") or "") in TRAILER_BREAKDOWN_PROFILES


# --- Deterministic style markers (A/B scoring + tests) -------------------------

_PROBLEM_RE = re.compile(
    r"\b(problem|mistake|wrong|trap|risk|risky|cost|costs|costly|expensive|"
    r"overpaid|overpaying|waste|wasted|wasting|broken|fails?|failed|failing|"
    r"scam|ripoff|rip-off|debt|loss|loses|losing|damage|ruin|ruins|danger|"
    r"warning|avoid|stop|never|too much|too expensive|price of|the cost of)\b"
    r"|\$\s?\d",
    re.IGNORECASE,
)

_CURIOSITY_RE = re.compile(
    r"\?|\b(secret|nobody|no one|actually|really|the truth|the reason|"
    r"what happened|what happens|why|how|which|who|this one|that one|"
    r"one thing|the one|hidden|revealed|won't tell|never tells|"
    r"they don't want|dont want)\b",
    re.IGNORECASE,
)

# A mistake_fix title must NAME the mistake in its own words: an
# imperative warning (stop / don't / never / avoid / quit), the word
# "mistake" itself, or the wrongness it calls out ("... done wrong").
_MISTAKE_FIX_RE = re.compile(
    r"\b(stop|don't|dont|do not|never|avoid|quit|mistake|mistakes|wrong)\b",
    re.IGNORECASE,
)

# A list_fill title must be the list's own heading: it opens with a
# count (a number word, digits, or "top" for a ranked table — an
# optional leading "the" is the speaker's own article) and names
# the outcome with "for", "by" or "to" ("Three money habits for a
# rich life", "Top 10 percent income by age", "5 rules to retire
# early").
_LIST_FILL_RE = re.compile(
    r"^(the\s+)?(one|two|three|four|five|six|seven|eight|nine|ten|\d+|top)\b"
    r".*\b(for|by|to)\b",
    re.IGNORECASE,
)

# A trailer_breakdown title must USE the format's hook formula: the
# viewer missed something in the first seconds of the trailer.
_TRAILER_BREAKDOWN_RE = re.compile(
    r"\b(you missed|missed this|missed it|did you (catch|spot|see)|"
    r"you didn't (catch|spot|see|notice)|hidden in|in the background)\b",
    re.IGNORECASE,
)

# A curiosity-gap title must still NAME its subject: at least one
# content word that is not itself a gap marker / function word.
_GAP_ONLY_WORDS = frozenset(
    {
        "why", "how", "what", "which", "who", "the", "a", "an", "this",
        "that", "one", "thing", "secret", "actually", "really", "nobody",
        "no", "they", "dont", "don't", "want", "you", "to", "know",
    }
)


def title_matches_style(title: Optional[str], style: Optional[str]) -> bool:
    """Whether a title actually does what its style claims, in its words.

    Only the candidate styles have markers; other styles return False
    (no marker vocabulary is claimed for them).
    """
    text = (title or "").strip()
    if not text:
        return False
    if style == "problem":
        return bool(_PROBLEM_RE.search(text))
    if style == "mistake_fix":
        return bool(_MISTAKE_FIX_RE.search(text))
    if style == "list_fill":
        return bool(_LIST_FILL_RE.search(text))
    if style == "trailer_breakdown":
        return bool(_TRAILER_BREAKDOWN_RE.search(text))
    if style == "curiosity_gap":
        if not _CURIOSITY_RE.search(text):
            return False
        subject_words = [
            tok
            for tok in re.findall(r"[A-Za-z0-9$']+", text.lower())
            if tok not in _GAP_ONLY_WORDS and len(tok) > 2
        ]
        return bool(subject_words)
    return False
