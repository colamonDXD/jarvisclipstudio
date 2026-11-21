"""Per-game picking rules + forced hashtags for gaming/streamer tasks.

Adapted from PeakCut (``studio/games.py`` and the ``GAME_RULES`` block
in ``studio/brain.py``, MIT — see NOTICE). Knowing the game changes two
things:

- the picker is told what kind of moment it is looking at (a clutch, a
  death, a jump scare mean something different per game), and
- every post carries the tags people actually search for (``#GTA6``),
  not just ``#gaming``.

The game is read from the stream/video title or channel name only. It
is a hint for the copy, never a filter: a stream whose title says
GTA 6 but which is really a chat stream still gets clips — they just
get GTA tags.

The table is deliberately small and evidence-based: the GTA entries
(Luffy's actual Whop/streamer work) plus a generic fallback. PeakCut's
23-game table was NOT copied; add a row only when a real task needs it.
"""

from __future__ import annotations

import os
import re
from typing import List, Optional, Sequence, Tuple

# name, words that mean it, tags to put on the post
GAMES: List[Tuple[str, List[str], List[str]]] = [
    (
        "Grand Theft Auto VI",
        ["grand theft auto vi", "grand theft auto 6", "gta vi", "gta 6", "gta6"],
        ["GTA6", "GTA6Game", "ViceCity", "RockstarGames", "GTA6Launch"],
    ),
    (
        "Grand Theft Auto V",
        ["grand theft auto v", "grand theft auto 5", "gta online", "gta v", "gta 5", "gta5", "gtav"],
        ["GTA5", "GTAOnline", "RockstarGames", "GTA5RP"],
    ),
]

# for a stream whose game nobody can name
GENERIC = ["Gaming", "Gameplay", "GamingClips", "StreamHighlights"]

# Per-game picking rules (PeakCut GAME_RULES, with the GTA tag line kept
# data-driven). ``{game}`` and ``{tags}`` are filled by game_rules_prompt.
GAME_RULES = """
This stream is {game}. Pick the moments that only mean something with that game in mind:
- A play, a death, a jump scare, a glitch, a discovery, or the streamer reacting to one.
- The hook is what was said at that moment, or, if nothing was said, the name of what happened in
  two or three words ("1 VS 3", "THE SHOT", "HE GOT ME", "CLUTCH"). Never a sentence that needs
  the game explained.
- The title names the game when the clip is about it.
- The hashtags include {tags}.
"""

# longest alias first, so "gta 6" is read before "gta"
_ALIASES: List[Tuple[str, str, List[str]]] = sorted(
    ((alias, name, tags) for name, aliases, tags in GAMES for alias in aliases),
    key=lambda a: -len(a[0]),
)

_WORD = re.compile(r"[^a-z0-9]+")


def game_rules_enabled() -> bool:
    return os.getenv("GAME_RULES", "on").strip().lower() not in (
        "off",
        "0",
        "false",
        "no",
    )


def _norm(text: str) -> str:
    return _WORD.sub(" ", str(text or "").lower()).strip()


def detect_game(*texts: Optional[str]) -> Tuple[Optional[str], List[str]]:
    """The game named in the texts, and the tags that belong on its posts.

    Returns (None, []) when no known game is named.
    """
    hay = _norm(" ".join(str(t or "") for t in texts))
    if not hay:
        return None, []
    for alias, name, tags in _ALIASES:
        if re.search(r"(?:^| )" + re.escape(_norm(alias)) + r"(?: |$)", hay):
            return name, list(tags)
    return None, []


def name_of(game: Optional[str]) -> str:
    """The game's proper name, from a name, a tag or a spelling of its own."""
    if not game:
        return ""
    g = _norm(game)
    for name, aliases, _tags in GAMES:
        if g == _norm(name) or g in {_norm(a) for a in aliases}:
            return name
    return str(game)[:40]


def tags_of(game: Optional[str]) -> List[str]:
    """The tags for a game, whether named in full, short, or by one of its tags."""
    if not game:
        return []
    g = _norm(game)
    for name, aliases, tags in GAMES:
        if g == _norm(name) or g in {_norm(a) for a in aliases} or g in {_norm(t) for t in tags}:
            return list(tags)
    cleaned = re.sub(r"\W", "", str(game))[:30]
    return [cleaned] if cleaned else []


def forced_hashtags(*texts: Optional[str]) -> List[str]:
    """Hashtags (with ``#``) that must ride on posts for this source.

    A detected game forces its tags; an undetected gaming source still
    gets the generic gaming set. Callers that are not gaming-related
    should gate on the task's niche before calling.
    """
    _name, tags = detect_game(*texts)
    chosen = tags if tags else GENERIC
    return [f"#{t}" for t in chosen]


def apply_forced_hashtags(
    existing: Sequence[str], *texts: Optional[str]
) -> List[str]:
    """Existing hashtags plus the forced ones, de-duplicated, order kept.

    The game's tags are forced onto every post whatever the AI or the
    user wrote (PeakCut's rule); existing tags are never removed.
    """
    out: List[str] = []
    seen = set()
    for raw in list(existing) + forced_hashtags(*texts):
        tag = str(raw or "").strip()
        if not tag:
            continue
        if not tag.startswith("#"):
            tag = f"#{tag}"
        key = tag.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(tag)
    return out


def game_rules_prompt(game: Optional[str]) -> str:
    """The per-game picking-rules block for a picker prompt ('' if unknown)."""
    name = name_of(game)
    if not name or name not in {g[0] for g in GAMES}:
        return ""
    tags = " ".join(f"#{t}" for t in tags_of(name)) or "#Gaming"
    return GAME_RULES.format(game=name, tags=tags).strip()
