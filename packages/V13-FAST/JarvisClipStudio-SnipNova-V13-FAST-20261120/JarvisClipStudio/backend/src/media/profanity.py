"""Swear words masked in everything burned into a clip (owner rule, 2026-11-19:
"strictly put *** for cuss words like fuck you"; format ruled 2026-11-20:
full stars, never first-letter).

``mask_profanity("FUCK YOU!")`` -> ``"*** YOU!"``: every swear word becomes
exactly ``***`` whatever its length, so no letter of the word survives.
Matching is case-insensitive and catches the usual forms (fucking,
motherfucker, shitty, bitches...). It runs in the ASS escape every caption
and hook title goes through, and on AI-written titles.

``CAPTION_PROFANITY_FILTER`` (default on) is the switch;
``CAPTION_PROFANITY_EXTRA`` adds comma-separated words.
"""
from __future__ import annotations

import os
import re
from functools import lru_cache

# Stems: a word matches when it starts with a stem (plus letters after it),
# or equals a whole-word entry. Kept short and unambiguous: "ass" only as a
# whole word or in compounds, so "class" / "pass" / "assassin" stay.
STEMS = (
    "fuck", "fuk", "fck", "motherfuck", "shit", "bullshit", "bitch", "cunt", "nigg",
    "whore", "slut", "pussy", "bastard", "asshole", "dumbass", "jackass", "dickhead",
    "fag", "wank", "twat",
)
# Only on their own (as prefixes they hit cocktail, prickly, Dickens, retardant).
WHOLE = ("ass", "asses", "arse", "damn", "goddamn", "dammit", "wtf", "stfu", "lmfao", "mf", "mfs", "bs",
         "piss", "pissed", "dick", "dicks", "cock", "cocks", "prick", "pricks", "retard", "retarded", "retards")


def profanity_filter_enabled() -> bool:
    return os.getenv("CAPTION_PROFANITY_FILTER", "on").strip().lower() not in ("off", "0", "false", "no")


@lru_cache(maxsize=4)
def _pattern(extra: str) -> re.Pattern:
    stems = list(STEMS) + [w.strip().lower() for w in extra.split(",") if w.strip()]
    stem_part = "|".join(sorted((re.escape(s) for s in stems), key=len, reverse=True))
    whole_part = "|".join(re.escape(w) for w in WHOLE)
    # A word starting with a stem (fucking, bitches) or exactly a whole-word entry.
    return re.compile(rf"\b(?:(?:{stem_part})[a-z']*|(?:{whole_part}))\b", re.IGNORECASE)


def _mask(match: re.Match) -> str:
    word = match.group(0)
    letters = [i for i, ch in enumerate(word) if ch.isalpha()]
    if not letters:
        return word
    # The word itself becomes exactly "***"; only punctuation the match
    # swallowed (a trailing apostrophe, as in "fuckin'") survives around it.
    return word[: letters[0]] + "***" + word[letters[-1] + 1 :]


def mask_profanity(text: str) -> str:
    if not text or not profanity_filter_enabled():
        return text
    return _pattern(os.getenv("CAPTION_PROFANITY_EXTRA", "")).sub(_mask, text)
