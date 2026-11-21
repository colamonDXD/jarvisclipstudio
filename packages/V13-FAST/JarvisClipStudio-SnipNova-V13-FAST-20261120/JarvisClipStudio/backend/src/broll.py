"""
B-roll for generated clips: plan stock-footage cutaways and source them.

Each clip gets its own plan. The model reads the clip's word-timed transcript
and picks the moments where the speaker names something filmable; the cutaways
are then placed on those words and sourced through the one resolution path —
the Smart B-Roll v2 resolver (``media/pixabay_broll.resolve_edl_broll``:
Pixabay first, Coverr second, every hit judged by ``footage_mismatch`` and,
for Auto templates, the vision veto) — and composited under the captions by
the renderer (``media/broll_overlay.py``). Downloads land once in the shared
stock library (``media/stock_library.py``) and the winning provider is
stamped on every placement.

Placements are stored on the clip's source-map sidecar in clip seconds, so a
caption edit or regeneration of the same clip re-applies the same footage.

The Pexels search/download code in this module is a compatibility layer for
footage stored before the consolidation: placements and editor drafts the
legacy planner created carry ``provider: "pexels"`` and must keep rendering,
re-downloading and converting. No new placement is ever sourced from Pexels
(Pexels paused new API keys; the supported key set is Pixabay + Coverr).
"""

from __future__ import annotations

import asyncio
import logging
import math
import os
import re
import time
import uuid
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence

import httpx

from .config import get_config

logger = logging.getLogger(__name__)

PEXELS_SEARCH_URL = "https://api.pexels.com/videos/search"
PEXELS_VIDEO_URL = "https://api.pexels.com/videos/videos/{id}"

# Cutaway placement. The opening seconds stay on the speaker (and the hook
# title), and the clip ends on the speaker too.
EARLIEST_START_SECONDS = 3.0
END_GUARD_SECONDS = 1.0
MIN_CUE_SECONDS = 1.5
MAX_CUE_SECONDS = 4.0
MIN_GAP_SECONDS = 2.0
MAX_COVERAGE = 0.4
SECONDS_PER_CUE = 12.0
MAX_CUES_PER_CLIP = 4
WORD_SNAP_SECONDS = 1.0
# Spoken words around a cutaway that its footage is judged against.
CONTEXT_BEFORE_SECONDS = 5.0
CONTEXT_AFTER_SECONDS = 2.0

SEARCH_RESULTS_PER_QUERY = 10
SEARCH_CACHE_TTL_SECONDS = 6 * 3600
SEARCH_CACHE_MAX_ENTRIES = 512
MAX_DOWNLOAD_BYTES = 150 * 1024 * 1024
# Footage is re-downloaded on demand, so the cache can stay small. Files used
# recently are kept even over the cap: a task prepares every clip's footage
# before its renders run.
CACHE_MAX_AGE_SECONDS = 24 * 3600
CACHE_MAX_BYTES = 2 * 1024 * 1024 * 1024
CACHE_IN_USE_SECONDS = 2 * 3600
HTTP_TIMEOUT = httpx.Timeout(30.0, connect=10.0)

_search_cache: Dict[tuple, tuple[float, List[Dict[str, Any]]]] = {}
_rate_limited_until = 0.0
_download_locks: Dict[str, asyncio.Lock] = {}


def broll_configured() -> bool:
    """True when the legacy Pexels key is present.

    Only the stored-footage compatibility layer consults this (old
    placements re-download from Pexels). New B-roll resolution never
    does — see ``stock_broll_configured``.
    """
    return bool(get_config().pexels_api_key)


def stock_broll_configured() -> bool:
    """True when a Smart B-Roll v2 stock provider (Pixabay or Coverr) is configured.

    The gate every B-roll feature checks since the consolidation: a
    deployment holding only Pixabay + Coverr keys (the supported key
    set — Pexels paused new issuance) must see B-roll as configured.
    """
    from .media.coverr_broll import coverr_configured
    from .media.pixabay_broll import pixabay_configured

    return pixabay_configured() or coverr_configured()


def broll_cache_dir() -> Path:
    return Path(get_config().temp_dir) / "broll_cache"


# --------------------------------------------------------------------------
# Pexels search and file choice — RETIRED sourcing layer
#
# Since the B-roll consolidation nothing in production calls these:
# every new-B-roll lookup goes through the v2 resolver. They remain
# because the stored-footage compatibility layer below shares their
# rate-limit state (fetch_pexels_video) and because their tests pin
# the footage_mismatch judge the v2 gate reuses. Do not re-arm them
# for new work.
# --------------------------------------------------------------------------


async def search_pexels_videos(
    query: str,
    orientation: str = "portrait",
    per_page: int = SEARCH_RESULTS_PER_QUERY,
    page: int = 1,
) -> List[Dict[str, Any]]:
    """Pexels results for ``query`` in relevance order; empty on any failure.

    Results are cached in-process, and a 429 pauses every search until the
    quota resets instead of spending the rest of the job on failing requests.
    """
    global _rate_limited_until
    api_key = get_config().pexels_api_key
    query = " ".join((query or "").split())[:80]
    if not api_key or not query:
        return []

    key = (query.lower(), orientation, per_page, page)
    cached = _search_cache.get(key)
    if cached and time.time() - cached[0] < SEARCH_CACHE_TTL_SECONDS:
        return cached[1]
    if time.time() < _rate_limited_until:
        return []

    try:
        async with httpx.AsyncClient(timeout=HTTP_TIMEOUT) as client:
            response = await client.get(
                PEXELS_SEARCH_URL,
                params={"query": query, "orientation": orientation, "per_page": per_page, "page": page},
                headers={"Authorization": api_key},
            )
    except httpx.HTTPError as exc:
        logger.warning("Pexels search failed for %r: %s", query, type(exc).__name__)
        return []

    if response.status_code == 429:
        reset = response.headers.get("X-Ratelimit-Reset")
        try:
            _rate_limited_until = float(reset) if reset else time.time() + 3600
        except ValueError:
            _rate_limited_until = time.time() + 3600
        logger.warning("Pexels rate limit reached; B-roll paused until %s", int(_rate_limited_until))
        return []
    if response.status_code != 200:
        logger.warning("Pexels search returned HTTP %s for %r", response.status_code, query)
        return []

    try:
        videos = [video for video in response.json().get("videos", []) if isinstance(video, dict)]
    except ValueError:
        return []
    if len(_search_cache) >= SEARCH_CACHE_MAX_ENTRIES:
        _search_cache.pop(next(iter(_search_cache)))
    _search_cache[key] = (time.time(), videos)
    return videos


def _matches_orientation(width: int, height: int, orientation: str) -> bool:
    if orientation == "square":
        return True
    if orientation == "portrait":
        return height > width
    if orientation == "landscape":
        return width > height
    return True


def pick_video_file(
    video: Dict[str, Any], target_short_side: int, orientation: str
) -> Optional[Dict[str, Any]]:
    """The smallest MP4 rendition that still covers the output resolution.

    Pexels' ``quality`` label is unreliable (newer uploads leave it null and
    "hd" covers both 720p and 1080p), so this goes by the real dimensions.
    Falls back to the largest rendition when none is big enough.
    """
    files = []
    for item in video.get("video_files") or []:
        try:
            width, height = int(item.get("width") or 0), int(item.get("height") or 0)
        except (TypeError, ValueError):
            continue
        if not item.get("link") or width <= 0 or height <= 0:
            continue
        if item.get("file_type") not in (None, "video/mp4"):
            continue
        files.append((width, height, item))
    oriented = [f for f in files if _matches_orientation(f[0], f[1], orientation)]
    candidates = oriented or files
    if not candidates:
        return None
    large_enough = [f for f in candidates if min(f[0], f[1]) >= target_short_side]
    if large_enough:
        width, height, item = min(large_enough, key=lambda f: f[0] * f[1])
    else:
        width, height, item = max(candidates, key=lambda f: f[0] * f[1])
    return {"link": item["link"], "width": width, "height": height}


def choose_video(
    videos: Sequence[Dict[str, Any]],
    min_duration: float,
    orientation: str,
    used_ids: Iterable[int] = (),
    query: str = "",
    context: str = "",
) -> Optional[Dict[str, Any]]:
    """The most on-topic result that is long enough, oriented right and unused.

    With a ``query``, results whose description is off-topic or carries a tone
    the spoken ``context`` does not (see ``footage_mismatch``) are skipped, and
    the one sharing the most words with the query wins; ties keep Pexels' order.
    """
    used = set(used_ids)
    best, best_score = None, -1
    for video in videos:
        if video.get("id") in used:
            continue
        try:
            duration = float(video.get("duration") or 0)
            width, height = int(video.get("width") or 0), int(video.get("height") or 0)
        except (TypeError, ValueError):
            continue
        if duration < min_duration:
            continue
        if width and height and not _matches_orientation(width, height, orientation):
            continue
        if not query:
            return video
        try:
            reason = footage_mismatch(video, query, context)
            score = footage_relevance(video, query)
        except Exception:
            # Judging is a refinement: an odd result must not cost the clip its B-roll.
            logger.warning("Could not judge B-roll %s; using it unjudged", video.get("id"), exc_info=True)
            reason, score = None, 0
        if reason:
            logger.info("Skipping B-roll %s for %r: %s", video.get("id"), query, reason)
            continue
        if score > best_score:
            best, best_score = video, score
    return best


# --------------------------------------------------------------------------
# Relevance and tone
# --------------------------------------------------------------------------

# Footage whose description carries one of these themes is used only when the
# spoken context is about the same theme: a neutral line about a dark bedroom
# must not cut to "a depressed woman lying on bed with pills". Terms ending in
# "*" match any word they start.
SENSITIVE_THEMES: Dict[str, tuple[str, ...]] = {
    "distress": (
        "depress*", "anxi*", "lonel*", "grief", "griev*", "cry", "cries", "crying", "tears",
        "sad", "sadness", "sorrow*", "despair*", "stress", "stressed", "upset", "panic*",
        "suicid*", "frustrat*", "heartbr*", "mourn*",
    ),
    "medical": (
        "pill", "pills", "medic*", "drug*", "hospital", "hospitals", "hospitalized",
        "patient", "patients", "syringe*", "inject*",
        "surgery", "surgical", "sick*", "illness", "disease*", "overdos*", "ambulance*",
        "injur*", "wound*", "therapist*", "psychiatr*",
    ),
    "substances": (
        "alcohol*", "drunk*", "cigarett*", "smoking", "vape", "vaping", "cocaine",
        "heroin", "marijuana", "cannabis", "addict*", "hangover*",
    ),
    "violence": (
        "violen*", "blood*", "gun", "guns", "weapon*", "knife", "knives", "assault*",
        "abus*", "murder*", "kill*", "crime*", "criminal*", "arrest*", "prison*",
        "explosion*", "war", "fighting",
    ),
    "death": (
        "death", "dead", "die", "dying", "funeral*", "coffin*", "grave", "graves",
        "cemetery", "corpse*",
    ),
    "accident": ("accident*", "crash*", "disaster*", "wreck*"),
    "adult": ("sexy", "lingerie", "naked", "nude", "erotic*", "seductive*"),
}

# Words that say nothing about what footage shows, so sharing one is no match.
_GENERIC_WORDS = frozenset(
    """a an and are as at be by for from in into is it its of on or over the this to
    under up with while near off out close shot view video footage stock clip scene
    background person people man men woman women guy girl boy someone wearing holding
    using doing looking""".split()
)


def _tokens(text: str) -> List[str]:
    return re.findall(r"[a-z0-9]+", (text or "").lower())


def _content_words(text: str) -> set[str]:
    words = set()
    for token in _tokens(text):
        if token in _GENERIC_WORDS or token.isdigit():
            continue
        for suffix in ("ing", "ed", "s", "y"):
            if token.endswith(suffix) and len(token) - len(suffix) >= 3:
                token = token[: -len(suffix)]
                break
        words.add(token)
    return words


# Second halves of compounds that still show their first word ("bed" -> "bedroom").
_COMPOUND_TAILS = frozenset({"room", "time", "side", "light", "shine", "rise", "set"})


def _same_word(a: str, b: str) -> bool:
    short, long = sorted((a, b), key=len)
    if a == b:
        return True
    if len(short) < 3 or not long.startswith(short):
        return False
    tail = long[len(short):]
    return (len(short) >= 4 and len(tail) <= 2) or tail in _COMPOUND_TAILS


def footage_description(video: Dict[str, Any]) -> str:
    """What a stock result shows: its title/alt/tags, else the slug of its page URL.

    Pexels page URLs read like ``/video/a-depressed-woman-lying-on-bed-with-pills-7279741/``.
    """
    parts = [str(video.get(key) or "") for key in ("title", "alt", "description")]
    tags = video.get("tags")
    if isinstance(tags, (list, tuple)):
        parts += [str(tag.get("name") if isinstance(tag, dict) else tag) for tag in tags if tag]
    match = re.search(r"/video/([^/?#]+)", str(video.get("url") or ""))
    if match:
        parts.append(re.sub(r"-?\d+$", "", match.group(1)).replace("-", " "))
    return " ".join(part for part in parts if part.strip())


def sensitive_themes(text: str) -> set[str]:
    themes = set()
    tokens = _tokens(text)
    for theme, terms in SENSITIVE_THEMES.items():
        for term in terms:
            if term.endswith("*"):
                hit = any(token.startswith(term[:-1]) for token in tokens)
            else:
                hit = term in tokens
            if hit:
                themes.add(theme)
                break
    return themes


def footage_relevance(video: Dict[str, Any], query: str) -> int:
    """How many of the query's descriptive words the footage's description shares."""
    described = _content_words(footage_description(video))
    return sum(1 for word in _content_words(query) if any(_same_word(word, d) for d in described))


# --------------------------------------------------------------------------
# Word senses: one spelling, two filmable meanings
# --------------------------------------------------------------------------

# Trigger words whose spelling names more than one thing a stock library
# can show. The EDL planner's vocabulary lists "train" with the travel
# nouns, but speech uses the same spelling for the exercise verb:
# "train for Diamond Gym" planned a railway cue, the bare-noun search
# returned a scenic railway bridge, and the relevance gate let it
# through because the hit's tags share the word "train" itself. Sense
# markers are matched on RAW tokens (no suffix stemming): "training"
# must stay an exercise signal instead of collapsing into "train".
#
# Each entry names the sense the trigger vocabulary means ("vocab"),
# the marker words that indicate each sense in speech or in a hit's
# tags, and what the planner does when an occurrence clearly indicates
# another sense: "skip" drops that occurrence, "resense:<word>" plans
# the other sense under its own unambiguous name. The inventory was
# built by reviewing the whole trigger vocabulary (base list + curated
# extension in auto_director/edl.py): these are its words with a
# realistic competing sense that dominates stock results. ("bass" is
# the classic example of the class but is not a trigger word, so it
# can never fire and needs no entry.)
WORD_SENSES: Dict[str, Dict[str, Any]] = {
    "train": {
        "vocab": "railway",
        "markers": {
            "railway": (
                "railway", "railroad", "station", "stations", "platform",
                "platforms", "locomotive", "passenger", "passengers",
                "metro", "subway", "commute", "commuter", "ticket",
                "tickets", "freight", "conductor",
            ),
            "exercise": (
                "gym", "workout", "workouts", "fitness", "exercise",
                "exercises", "training", "muscle", "muscles", "cardio",
                "weights", "lifting", "athlete", "athletes", "marathon",
                "coach", "practice", "reps", "squat", "squats",
                "deadlift", "dumbbell", "dumbbells", "treadmill",
            ),
        },
        # "train for the gym / a marathon" is the verb, whatever nouns
        # surround it.
        "bigrams": {("train", "for"): ("exercise", 2)},
        "actions": {"exercise": "skip"},
    },
    "race": {
        "vocab": "competition",
        "markers": {
            "competition": (
                "running", "runner", "runners", "marathon", "sprint",
                "sprints", "finish", "lap", "laps", "championship",
                "racing", "racer", "racers", "triathlon", "cycling",
                "swimming", "medal", "medals",
            ),
            "humanity": ("human", "mankind", "humankind"),
        },
        "actions": {"humanity": "skip"},
    },
    "pool": {
        "vocab": "swimming",
        "markers": {
            "swimming": (
                "swim", "swimming", "swimmer", "swimmers", "dive",
                "diving", "lap", "laps", "lifeguard", "chlorine",
                "poolside", "backstroke",
            ),
            "billiards": (
                "billiard", "billiards", "snooker", "cue", "pocket",
                "pockets", "rack",
            ),
        },
        "actions": {"billiards": "resense:billiards"},
    },
    "market": {
        "vocab": "financial",
        "markers": {
            "financial": (
                "profit", "profits", "loss", "losses", "trade",
                "trades", "trading", "trader", "strategy", "odds",
                "win", "wins", "winning", "winner", "winners",
                "losing", "losers", "position", "positions",
                "portfolio", "price", "prices", "chart", "charts",
                "stock", "stocks", "invest", "investing",
                "investment", "finance", "financial", "crypto",
            ),
            "street": (
                "farmers", "farmer", "street", "streets", "stall",
                "stalls", "bazaar", "groceries", "grocery",
                "vegetables", "vegetable", "fruits", "fruit",
                "vendors", "vendor", "produce", "flea", "shopping",
                "shoppers", "handmade", "souvenirs",
            ),
        },
        # A street-sense occurrence plans normally: the bare noun
        # already retrieves street-market footage, so the cue keeps
        # its own name and the search string carries the street
        # markers instead ("market farmers"). What must never happen
        # is the reverse pairing — trading speech planned bare, its
        # street hits waved through by the shared word "market".
        "actions": {"street": "resense:market"},
    },
}


def indicated_sense(word: str, text: str) -> Optional[str]:
    """The sense of ``word`` that ``text`` points at, or None.

    None when the word has only one sense, when no marker of any sense
    is present, or when the markers tie — no clear signal must never
    veto or skip anything.
    """
    entry = WORD_SENSES.get(word)
    if not entry:
        return None
    tokens = _tokens(text)
    counts: Dict[str, int] = {}
    for sense, markers in entry["markers"].items():
        marker_set = set(markers)
        counts[sense] = sum(1 for token in tokens if token in marker_set)
    for (first, second), (sense, weight) in entry.get("bigrams", {}).items():
        counts[sense] = counts.get(sense, 0) + weight * sum(
            1 for a, b in zip(tokens, tokens[1:]) if (a, b) == (first, second)
        )
    best = max(counts.values()) if counts else 0
    if best <= 0:
        return None
    winners = [sense for sense, count in counts.items() if count == best]
    return winners[0] if len(winners) == 1 else None


def footage_sense_mismatch(query: str, context: str, description: str) -> Optional[str]:
    """Why a hit is the wrong sense of a trigger word, or None.

    Fires only when BOTH sides give a clear signal and they disagree:
    the spoken ``context`` indicates one sense of an ambiguous word in
    the ``query`` while the hit's own ``description`` (its tags/title)
    indicates another. Sharing the bare word is not enough to pass —
    that is exactly how a railway bridge landed on "train for the gym".
    """
    query_tokens = set(_tokens(query))
    for word in WORD_SENSES:
        if word not in query_tokens:
            continue
        spoken = indicated_sense(word, context) if context else None
        shown = indicated_sense(word, description)
        if spoken and shown and spoken != shown:
            return (
                f"footage shows the {shown} sense of '{word}', "
                f"speech means {spoken}"
            )
    return None


def broll_search_query(noun: str, context: str) -> str:
    """The stock search string for a planned cue.

    An unambiguous noun searches as-is (a bare "avocado" already
    retrieves on-topic footage). For an ambiguous trigger the search
    carries the indicated sense in the speech's own words — the sense
    markers actually spoken around the cue, in spoken order — so a
    railway "train" is searched as "train station", never as the bare
    word a stock library reads one way only.
    """
    entry = WORD_SENSES.get(noun)
    if not entry:
        return noun
    sense = indicated_sense(noun, context)
    if not sense:
        return noun
    markers = set(entry["markers"].get(sense, ()))
    extras: List[str] = []
    for token in _tokens(context):
        if token in markers and token != noun and token not in extras:
            extras.append(token)
        if len(extras) >= 2:
            break
    return " ".join([noun, *extras]) if extras else noun


def _avoid_phrase_hit(description: str, avoid: Iterable[str]) -> Optional[str]:
    """The avoid phrase a hit's description matches, or None.

    One phrase of the concept's avoid list is enough to reject a hit:
    every content word of the phrase must be present in the hit's
    description (a "stock market street festival" tag salad must not
    pass on "stock market" just because it also shares a concept word).
    A phrase with no content words left after stop-word stripping
    cannot judge anything and is skipped.
    """
    described = _content_words(description)
    if not described:
        return None
    for phrase in avoid:
        words = _content_words(str(phrase or ""))
        if not words:
            continue
        if all(any(_same_word(word, d) for d in described) for word in words):
            return f"shows {str(phrase).strip()!r}, which the concept says to avoid"
    return None


def footage_mismatch(video: Dict[str, Any], query: str, context: str = "", avoid: Iterable[str] = ()) -> Optional[str]:
    """Why a stock result does not fit the cue, or None when it does.

    Footage is rejected when its description brings a sensitive theme that
    neither the query nor the spoken ``context`` is about, when it shows
    the wrong sense of an ambiguous trigger word (see
    ``footage_sense_mismatch``), or when it is described and shares no
    descriptive word with the query. Results without a description cannot
    be judged and are allowed.

    ``avoid`` (Smart B-Roll v2, Slice 3) is the concept's wrong-sense
    list: a hit matching any avoid phrase is rejected first, before
    the other checks — the concept's own "must not show" outranks a
    shared word. Callers without concepts pass nothing and the gate
    behaves exactly as before.
    """
    description = footage_description(video)
    if not description:
        return None
    avoid_reason = _avoid_phrase_hit(description, avoid)
    if avoid_reason:
        return avoid_reason
    unwanted = sensitive_themes(description) - sensitive_themes(f"{query} {context}")
    if unwanted:
        return f"implies {', '.join(sorted(unwanted))}"
    conflict = footage_sense_mismatch(query, context, description)
    if conflict:
        return conflict
    described = _content_words(description)
    wanted = _content_words(query)
    if described and wanted and not any(_same_word(w, d) for w in wanted for d in described):
        return "off-topic"
    return None


# --------------------------------------------------------------------------
# Download cache
# --------------------------------------------------------------------------


def broll_file_path(entry: Dict[str, Any]) -> Path:
    return broll_cache_dir() / (
        f"pexels_{int(entry['pexels_id'])}_{int(entry['width'])}x{int(entry['height'])}.mp4"
    )


def prune_broll_cache(
    max_age_seconds: float = CACHE_MAX_AGE_SECONDS,
    max_bytes: int = CACHE_MAX_BYTES,
    in_use_seconds: float = CACHE_IN_USE_SECONDS,
) -> None:
    """Drop footage unused for a day, then the oldest until under the size cap.

    Footage used in the last ``in_use_seconds`` is never removed.
    """
    directory = broll_cache_dir()
    if not directory.is_dir():
        return
    cutoff = time.time() - max_age_seconds
    kept = []
    for path in directory.glob("pexels_*.mp4"):
        try:
            stat = path.stat()
            if stat.st_mtime < cutoff:
                path.unlink(missing_ok=True)
            else:
                kept.append((stat.st_mtime, stat.st_size, path))
        except OSError:
            continue
    total = sum(size for _, size, _ in kept)
    protect_after = time.time() - in_use_seconds
    for mtime, size, path in sorted(kept):
        if total <= max_bytes or mtime >= protect_after:
            break
        path.unlink(missing_ok=True)
        total -= size


async def _download(url: str, destination: Path) -> bool:
    destination.parent.mkdir(parents=True, exist_ok=True)
    partial = destination.with_name(f".{destination.name}.{uuid.uuid4().hex}.part")
    received = 0
    try:
        async with httpx.AsyncClient(timeout=HTTP_TIMEOUT, follow_redirects=True) as client:
            async with client.stream("GET", url) as response:
                if response.status_code != 200:
                    logger.warning("B-roll download returned HTTP %s", response.status_code)
                    return False
                with open(partial, "wb") as handle:
                    async for chunk in response.aiter_bytes(1 << 16):
                        received += len(chunk)
                        if received > MAX_DOWNLOAD_BYTES:
                            logger.warning("B-roll download exceeded %s bytes", MAX_DOWNLOAD_BYTES)
                            return False
                        handle.write(chunk)
        if received == 0:
            return False
        os.replace(partial, destination)
        return True
    except (httpx.HTTPError, OSError) as exc:
        logger.warning("B-roll download failed: %s", type(exc).__name__)
        return False
    finally:
        partial.unlink(missing_ok=True)


async def ensure_broll_file(entry: Dict[str, Any]) -> Optional[Path]:
    """Local copy of a placement's footage, downloading it when not cached."""
    try:
        path = broll_file_path(entry)
    except (KeyError, TypeError, ValueError):
        return None
    lock = _download_locks.setdefault(str(path), asyncio.Lock())
    async with lock:
        if path.is_file() and path.stat().st_size > 0:
            path.touch()
            return path
        if not entry.get("file_url") or not await _download(entry["file_url"], path):
            return None
        # A 200 can still be an error page or a truncated file, and one bad
        # input would fail the whole clip render.
        if not await asyncio.to_thread(_is_playable_video, path):
            logger.warning("B-roll download for %s is not a playable video", entry.get("pexels_id"))
            path.unlink(missing_ok=True)
            return None
        return path


def _is_playable_video(path: Path) -> bool:
    from .media.ffmpeg import ffprobe_duration, ffprobe_video_size

    try:
        width, height = ffprobe_video_size(path)
        return width > 0 and height > 0 and ffprobe_duration(path) > 0
    except Exception:
        return False


# --------------------------------------------------------------------------
# Planning
# --------------------------------------------------------------------------


def max_cues_for_duration(clip_duration: float) -> int:
    return max(1, min(MAX_CUES_PER_CLIP, round(clip_duration / SECONDS_PER_CUE)))


def timed_lines_from_words(words: Sequence[Dict[str, Any]], max_words: int = 8) -> str:
    """Clip transcript as short phrases, each prefixed with its start second."""
    lines: List[str] = []
    current: List[str] = []
    line_start = 0.0
    previous_end: Optional[float] = None
    for word in words:
        text = str(word.get("text") or "").strip()
        if not text:
            continue
        start = float(word.get("start") or 0.0)
        if current and (
            len(current) >= max_words
            or (previous_end is not None and start - previous_end > 0.6)
            or current[-1].endswith((".", "?", "!"))
        ):
            lines.append(f"[{line_start:.1f}] {' '.join(current)}")
            current = []
        if not current:
            line_start = max(0.0, start)
        current.append(text)
        previous_end = float(word.get("end") or start)
    if current:
        lines.append(f"[{line_start:.1f}] {' '.join(current)}")
    return "\n".join(lines)


def select_cues(
    cues: Sequence[Any],
    words: Sequence[Dict[str, Any]],
    clip_duration: float,
    max_cues: int,
) -> List[Dict[str, Any]]:
    """Validate the model's cues against the clip: snap, clamp, space, cap."""
    word_starts = sorted(
        float(word["start"]) for word in words if isinstance(word.get("start"), (int, float))
    )
    latest_end = clip_duration - END_GUARD_SECONDS
    candidates = []
    for cue in cues:
        query = " ".join(str(getattr(cue, "query", "") or "").split())[:80]
        try:
            start = float(getattr(cue, "start", 0))
            duration = float(getattr(cue, "duration", 0))
        except (TypeError, ValueError):
            continue
        if not query or not math.isfinite(start) or not math.isfinite(duration):
            continue
        # Land on the spoken word the model pointed at, a beat before it.
        nearest = min(word_starts, key=lambda s: abs(s - start), default=None)
        if nearest is not None and abs(nearest - start) <= WORD_SNAP_SECONDS:
            start = nearest - 0.1
        start = max(start, EARLIEST_START_SECONDS)
        duration = min(max(duration, MIN_CUE_SECONDS), MAX_CUE_SECONDS)
        end = min(start + duration, latest_end)
        if end - start < MIN_CUE_SECONDS:
            continue
        candidates.append(
            {
                "start": round(start, 3),
                "duration": round(end - start, 3),
                "query": query,
                "fallback_query": " ".join(str(getattr(cue, "fallback_query", "") or "").split())[:80],
            }
        )

    selected: List[Dict[str, Any]] = []
    covered = 0.0
    for cue in sorted(candidates, key=lambda c: c["start"]):
        if len(selected) >= max_cues:
            break
        if selected:
            previous = selected[-1]
            if cue["start"] < previous["start"] + previous["duration"] + MIN_GAP_SECONDS:
                continue
        if covered + cue["duration"] > clip_duration * MAX_COVERAGE:
            continue
        selected.append(cue)
        covered += cue["duration"]
    return selected


def cue_context(words: Sequence[Dict[str, Any]], start: float, duration: float) -> str:
    """What the speaker says around a cutaway, for judging its footage."""
    first, last = start - CONTEXT_BEFORE_SECONDS, start + duration + CONTEXT_AFTER_SECONDS
    return " ".join(
        str(word.get("text") or "").strip()
        for word in words
        if isinstance(word.get("start"), (int, float)) and first <= word["start"] <= last
    )


class BRollSession:
    """Per-task state, so one task never repeats the same stock clip."""

    def __init__(self) -> None:
        self.used_ids: set[int] = set()
        # (provider, asset_id) pairs already placed by this task. The v2
        # providers do not share one int id space (Coverr ids are
        # strings, and a Pixabay int could collide with a legacy Pexels
        # id in used_ids), so resolved placements dedupe on the pair;
        # used_ids remains the legacy/editor-seeding space.
        self.used_assets: set = set()


async def _resolve_cue_via_v2(
    query: str,
    *,
    start: float,
    duration: float,
    context: str,
) -> Optional[Dict[str, Any]]:
    """One cue resolved through the Smart B-Roll v2 resolver, as a placement.

    Builds the one-entry EDL the resolver consumes (the same shape the
    auto-director planner stamps), resolves it in a thread — Pixabay
    first, Coverr second, the shared ``footage_mismatch`` gate (sensitive
    themes, wrong senses, off-topic) and, for the Auto template, the
    vision veto — and converts the stamped entry with the Slice 6
    bridge. The query is sense-resolved first, exactly as the EDL
    planner resolves its keyword cues. Returns None when no hit passes:
    the cue is skipped, never filled with unrelated footage.
    """
    from .media.pixabay_broll import resolve_edl_broll

    entry = {
        "start": start,
        "end": start + duration,
        "query": query,
        "search_query": broll_search_query(query, context),
        "context": context,
        "source": "stock",
    }
    probe = {"template": "auto", "broll": [entry]}
    await asyncio.to_thread(resolve_edl_broll, probe)
    return edl_broll_entry_to_placement(entry)


async def _source_cue(
    cue: Dict[str, Any],
    orientation: str,
    target_short_side: int,
    session: BRollSession,
) -> Optional[Dict[str, Any]]:
    """Source one planned cue through the v2 resolver (Pixabay -> Coverr).

    ``orientation`` / ``target_short_side`` are accepted for signature
    compatibility with the legacy Pexels sourcing this replaced: the v2
    resolver's renditions are portrait stock sized for the vertical
    canvas, so neither steers the choice any more. The cue's fallback
    query is tried when the primary resolves nothing, and a session
    never places the same asset twice — an asset an earlier cue (or an
    earlier clip of the task) already used counts as unresolved and
    falls through to the fallback query.
    """
    for query in dict.fromkeys(q for q in (cue["query"], cue.get("fallback_query")) if q):
        placement = await _resolve_cue_via_v2(
            query,
            start=cue["start"],
            duration=cue["duration"],
            context=cue.get("context", ""),
        )
        if placement is None:
            continue
        provider = str(placement.get("provider") or "")
        asset_id = placement.get("asset_id")
        if (provider, asset_id) in session.used_assets or (
            provider == "pixabay"
            and isinstance(asset_id, int)
            and asset_id in session.used_ids
        ):
            continue
        session.used_assets.add((provider, asset_id))
        if provider == "pixabay" and isinstance(asset_id, int):
            session.used_ids.add(asset_id)
        return placement
    return None


async def plan_clip_broll(
    words: Sequence[Dict[str, Any]],
    clip_duration: float,
    orientation: str = "portrait",
    target_short_side: int = 1080,
    session: Optional[BRollSession] = None,
) -> List[Dict[str, Any]]:
    """Plan and download the cutaways for one clip.

    ``words`` are on the clip timeline (seconds from the clip start). Returns
    placements whose footage the v2 resolver downloaded into the shared
    stock library; an empty list whenever no v2 stock provider is
    configured, nothing in the clip is filmable, or a provider fails.
    The clip always renders, with or without B-roll.
    """
    if not stock_broll_configured() or not words:
        return []
    if clip_duration < EARLIEST_START_SECONDS + MIN_CUE_SECONDS + END_GUARD_SECONDS:
        return []
    session = session or BRollSession()
    max_cues = max_cues_for_duration(clip_duration)

    from .ai import plan_broll_cues

    try:
        raw_cues = await plan_broll_cues(
            timed_lines_from_words(words), clip_duration, max_cues, EARLIEST_START_SECONDS
        )
    except Exception as exc:
        logger.warning("B-roll planning failed (%s); rendering without B-roll", type(exc).__name__)
        return []

    cues = select_cues(raw_cues, words, clip_duration, max_cues)
    for cue in cues:
        cue["context"] = cue_context(words, cue["start"], cue["duration"])
    if not cues:
        logger.info("No B-roll moments in this clip")
        return []

    await asyncio.to_thread(prune_broll_cache)
    # Cues resolve in order so a later cue never takes the stock clip an
    # earlier one picked. Each placement the resolver returns already
    # names a file downloaded into the stock library (the Slice 6 bridge
    # converts only entries whose file exists), so there is no separate
    # download pass.
    placements = []
    for cue in cues:
        placement = await _source_cue(cue, orientation, target_short_side, session)
        if placement:
            placements.append(placement)
        else:
            logger.info("No stock footage found for B-roll query %r", cue["query"])
    logger.info(
        "B-roll: %d/%d cutaways sourced (%s)",
        len(placements),
        len(cues),
        ", ".join(p["query"] for p in placements) or "none",
    )
    return placements


async def broll_overlays_for(placements: Iterable[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Renderer overlays for stored placements, re-downloading evicted footage.

    Smart B-Roll v2 placements (provider pixabay/coverr + ``asset_id``)
    name a file in the shared stock library — the resolver downloaded
    it when the cue was planned, and a library miss means the file was
    evicted, so the cutaway is skipped rather than re-fetched sight
    unseen (the same rule the editor export follows in
    ``ensure_footage_file``). Legacy Pexels placements keep their
    download-on-demand path.
    """
    overlays = []
    for placement in placements or []:
        provider = str(placement.get("provider") or "pexels")
        if provider in ("pixabay", "coverr"):
            from .media import stock_library

            path = await asyncio.to_thread(
                stock_library.lookup_asset_file, provider, placement.get("asset_id")
            )
            if path is None:
                logger.warning(
                    "B-roll footage %s:%s unavailable; skipping it",
                    provider,
                    placement.get("asset_id"),
                )
                continue
        else:
            path = await ensure_broll_file(placement)
            if path is None:
                logger.warning("B-roll footage %s unavailable; skipping it", placement.get("pexels_id"))
                continue
        overlays.append(
            {
                "path": path,
                "start": placement["start"],
                "duration": placement["duration"],
                "offset": float(placement.get("footage_offset") or 0.0),
            }
        )
    return overlays


def shift_broll_placements(
    placements: Iterable[Dict[str, Any]] | None, offset: float, duration: float
) -> List[Dict[str, Any]]:
    """Placements for a cut of the clip that starts ``offset`` seconds in.

    Cutaways that end up mostly outside the new clip are dropped; the rest are
    clipped to it. A cutaway cut at its head resumes its footage where the
    cut falls (``footage_offset``) instead of replaying it from the start.
    """
    shifted = []
    for placement in placements or []:
        start = float(placement["start"]) - offset
        end = start + float(placement["duration"])
        footage_offset = float(placement.get("footage_offset") or 0.0) + max(0.0, -start)
        start, end = max(0.0, start), min(duration, end)
        if end - start < MIN_CUE_SECONDS:
            continue
        moved = {**placement, "start": round(start, 3), "duration": round(end - start, 3)}
        if footage_offset > 0:
            moved["footage_offset"] = round(footage_offset, 3)
        shifted.append(moved)
    return shifted


# --------------------------------------------------------------------------
# Editor: search, lookup and conversion between placements and editor clips
# --------------------------------------------------------------------------


def footage_from_video(video: Dict[str, Any], target_short_side: int, orientation: str) -> Optional[Dict[str, Any]]:
    """Editor footage (``BRollFootage`` fields) for a Pexels video, or None."""
    chosen = pick_video_file(video, target_short_side, orientation)
    if not chosen or not str(chosen["link"]).startswith("https://videos.pexels.com/"):
        return None
    try:
        duration = float(video.get("duration") or 0)
    except (TypeError, ValueError):
        return None
    if duration <= 0:
        return None
    user = video.get("user") or {}
    return {
        "provider": "pexels",
        "id": int(video["id"]),
        "width": chosen["width"],
        "height": chosen["height"],
        "duration": duration,
        "fileUrl": chosen["link"],
        "pageUrl": str(video.get("url") or "")[:500],
        "thumbnail": str(video.get("image") or "")[:500],
        "credit": str(user.get("name") or "")[:200],
    }


async def search_footage(
    query: str, orientation: str = "portrait", target_short_side: int = 1080, page: int = 1
) -> List[Dict[str, Any]]:
    """Editor search results (``BRollFootage`` fields) from the v2 providers.

    Pixabay is searched first; Coverr answers when Pixabay is
    unconfigured, returns nothing, or none of its hits can be fetched —
    the resolver's order. Every hit returned is downloaded into the
    shared stock library first: the editor previews a pick from the
    library and the export looks it up there, so a hit that cannot be
    downloaded is not offered. ``orientation`` / ``target_short_side``
    are accepted for signature compatibility with the Pexels search
    this replaced; the v2 hits are portrait stock sized for the
    vertical canvas. ``page`` walks the provider's results in pages
    of 18.
    """
    from .media import coverr_broll, pixabay_broll

    page = max(1, page)
    per_page = 18 * page
    start = 18 * (page - 1)

    def _ensure_page(provider: str, hits: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        ensure = (
            pixabay_broll.ensure_pixabay_hit
            if provider == "pixabay"
            else coverr_broll.ensure_coverr_hit
        )
        results = []
        for hit in hits[start : start + 18]:
            asset = ensure(hit, query=query)
            if asset is None:
                continue
            footage = _footage_from_stock_asset(provider, asset)
            if footage:
                results.append(footage)
        return results

    if pixabay_broll.pixabay_configured():
        hits = await asyncio.to_thread(
            pixabay_broll.search_pixabay_videos, query, per_page=per_page
        )
        if hits:
            results = await asyncio.to_thread(_ensure_page, "pixabay", hits)
            if results:
                return results
    if coverr_broll.coverr_configured():
        hits = await asyncio.to_thread(
            coverr_broll.search_coverr_videos, query, per_page=per_page
        )
        if hits:
            return await asyncio.to_thread(_ensure_page, "coverr", hits)
    return []


async def fetch_pexels_video(video_id: int) -> Optional[Dict[str, Any]]:
    """One Pexels video by id (to re-resolve a rendition's download link)."""
    api_key = get_config().pexels_api_key
    if not api_key or time.time() < _rate_limited_until:
        return None
    try:
        async with httpx.AsyncClient(timeout=HTTP_TIMEOUT) as client:
            response = await client.get(
                PEXELS_VIDEO_URL.format(id=int(video_id)), headers={"Authorization": api_key}
            )
    except httpx.HTTPError:
        return None
    if response.status_code != 200:
        return None
    try:
        video = response.json()
    except ValueError:
        return None
    return video if isinstance(video, dict) else None


async def ensure_footage_file(footage: Dict[str, Any]) -> Optional[Path]:
    """Local copy of editor footage; re-resolves the link if the stored one fails.

    Pixabay/Coverr footage is never re-downloaded here: the resolver put
    the file in the shared stock library when the cue was planned (or
    re-resolved), and the library manifest is the authority for where it
    lives — a photo win's Ken Burns clip is filed under a ``_still``
    name, not the plain video name. A library miss means the file was
    evicted; the export then skips that cutaway rather than fetching a
    possibly wrong file (see ``render_document``). Pexels keeps its
    existing download path, with the stored link re-resolved on failure.
    """
    provider = str(footage.get("provider") or "pexels")
    if provider in ("pixabay", "coverr"):
        from .media import stock_library

        return await asyncio.to_thread(
            stock_library.lookup_asset_file, provider, footage["id"]
        )
    entry = {
        "pexels_id": footage["id"],
        "width": footage["width"],
        "height": footage["height"],
        "file_url": footage.get("fileUrl"),
    }
    path = await ensure_broll_file(entry)
    if path is not None:
        return path
    video = await fetch_pexels_video(int(footage["id"]))
    for item in (video or {}).get("video_files") or []:
        link = str(item.get("link") or "")
        if (
            item.get("width") == footage["width"]
            and item.get("height") == footage["height"]
            and link.startswith("https://videos.pexels.com/")
        ):
            return await ensure_broll_file({**entry, "file_url": link})
    return None


def placement_to_editor_clip(placement: Dict[str, Any], index: int, clip_duration: float) -> Optional[Dict[str, Any]]:
    """An editor B-roll clip from a generated clip's stored placement.

    The editor's clean.mp4 is rendered from the same ranges as the generated
    clip, so placement seconds are already on its timeline.

    Placements carry their provider: legacy Pexels placements (``pexels_id``
    + a Pexels CDN ``file_url``) and Smart B-Roll v2 placements from a
    resolved EDL entry (``provider`` pixabay/coverr + ``asset_id`` + that
    provider's CDN ``file_url``). A placement whose footage URL is not on
    its own provider's CDN is not imported — a draft must never point
    the renderer at an arbitrary host. The review-surface fields
    (``concept`` / ``search_query`` / ``search_queries`` / ``avoid`` /
    ``media``) ride through untouched; a placement without them (legacy
    or manual) yields a clip whose panel falls back to ``query``.
    """
    from .editor_document import BROLL_CONTINUATION_MIN_SECONDS, BROLL_MIN_SECONDS, STOCK_CDN_HOSTS, TIME_EPSILON

    try:
        start = max(0.0, float(placement["start"]))
        end = min(clip_duration, start + float(placement["duration"]))
        offset = float(placement.get("footage_offset") or 0.0)
        file_url = str(placement.get("file_url") or "")
        duration = float(placement.get("footage_duration") or 0) or offset + end - start
        fade_in = placement.get("fade_in", True) is not False
        fade_out = placement.get("fade_out", True) is not False
        minimum = BROLL_MIN_SECONDS if fade_in and fade_out else BROLL_CONTINUATION_MIN_SECONDS
        if end - start < minimum - TIME_EPSILON:
            return None
        provider = str(placement.get("provider") or "pexels")
        if provider not in STOCK_CDN_HOSTS:
            return None
        if not file_url.startswith(f"https://{STOCK_CDN_HOSTS[provider]}/"):
            return None
        if provider == "pexels":
            asset_id = int(placement["pexels_id"])
            page_url = str(placement.get("pexels_url") or "")[:500]
            credit = str(placement.get("credit") or "")[:200]
        else:
            asset_id = placement["asset_id"]
            if isinstance(asset_id, str) and (not asset_id or len(asset_id) > 100):
                return None
            if isinstance(asset_id, int) and asset_id <= 0:
                return None
            page_url = str(placement.get("page_url") or "")[:500]
            credit = str(placement.get("credit") or "")[:200]
        search_queries = [
            str(item).strip()
            for item in (placement.get("search_queries") or [])
            if str(item).strip()
        ][:3]
        avoid = [
            str(item).strip()
            for item in (placement.get("avoid") or [])
            if str(item).strip()
        ][:20]
        return {
            "id": f"broll-{index}-{asset_id}",
            "start": round(start, 3),
            "end": round(end, 3),
            "offset": round(offset, 3),
            "query": str(placement.get("query") or "")[:120],
            "fadeIn": fade_in,
            "fadeOut": fade_out,
            "fadeDuration": placement.get("fade_duration"),
            "concept": str(placement.get("concept") or "")[:500],
            "searchQuery": str(placement.get("search_query") or "")[:200],
            "searchQueries": search_queries,
            "avoid": avoid,
            "media": str(placement.get("media") or "")[:20],
            "footage": {
                "provider": provider,
                "id": asset_id,
                "width": int(placement["width"]),
                "height": int(placement["height"]),
                "duration": round(duration, 3),
                "fileUrl": file_url,
                "pageUrl": page_url,
                "thumbnail": str(placement.get("thumbnail") or "")[:500],
                "credit": credit,
            },
        }
    except (KeyError, TypeError, ValueError):
        return None


def editor_piece_to_placement(piece: Dict[str, Any]) -> Dict[str, Any]:
    """A clip placement (sidecar format) for a rendered editor B-roll piece.

    The provider rides along (a Pixabay/Coverr piece keeps its provider
    and asset id; a Pexels piece keeps its legacy ``pexels_id`` shape),
    as do the review-surface fields — a re-render carries the saved
    cutaway's concept and re-resolution inputs, never just its footage.
    """
    footage = piece["footage"]
    footage = footage.model_dump() if hasattr(footage, "model_dump") else footage
    provider = str(footage.get("provider") or "pexels")
    placement = {
        "start": round(piece["start"], 3),
        "duration": round(piece["duration"], 3),
        "footage_offset": round(piece.get("offset") or 0.0, 3),
        "query": piece.get("query") or "",
        "concept": piece.get("concept") or "",
        "search_query": piece.get("searchQuery") or "",
        "search_queries": list(piece.get("searchQueries") or []),
        "avoid": list(piece.get("avoid") or []),
        "media": piece.get("media") or "",
        "provider": provider,
        "file_url": footage["fileUrl"],
        "width": footage["width"],
        "height": footage["height"],
        "footage_duration": footage["duration"],
        "thumbnail": footage.get("thumbnail"),
        "credit": footage.get("credit"),
        "fade_in": piece.get("fade_in", True),
        "fade_out": piece.get("fade_out", True),
        "fade_duration": piece.get("fade_duration"),
    }
    if provider == "pexels":
        placement["pexels_id"] = footage["id"]
        placement["pexels_url"] = footage.get("pageUrl")
    else:
        placement["asset_id"] = footage["id"]
        placement["page_url"] = footage.get("pageUrl")
    return placement


def edl_broll_entry_to_placement(entry: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """A sidecar placement for a resolved EDL B-roll entry, or None.

    The EDL renderer composites exactly the entries that resolved to a
    file on disk; this converts one of those into the placement the
    editor opens (and the re-render carry consumes), so the review
    surface shows the cutaway the viewer actually saw — concept, search
    phrases, avoid list and media preference included. An unresolved
    entry (no file, or no rendition dimensions stamped at resolve time)
    yields None: the cue was skipped at render time, exactly as
    designed, and there is no footage to review.
    """
    try:
        local_path = str(entry.get("local_path") or "")
        if not local_path or not Path(local_path).is_file():
            return None
        start = float(entry["start"])
        end = float(entry["end"])
        if end <= start:
            return None
        provider = str(entry.get("provider") or "pixabay")
        if provider not in ("pixabay", "coverr"):
            return None
        width = int(entry.get("width") or 0)
        height = int(entry.get("height") or 0)
        footage_duration = float(entry.get("duration") or 0)
        if width <= 0 or height <= 0 or footage_duration <= 0:
            return None
        file_url = str(entry.get("download_url") or "")
        if not file_url:
            return None
        search_queries = [
            str(item).strip()
            for item in (entry.get("search_queries") or [])
            if str(item).strip()
        ][:3]
        avoid = [
            str(item).strip()
            for item in (entry.get("avoid") or [])
            if str(item).strip()
        ][:20]
        return {
            "start": round(start, 3),
            "duration": round(end - start, 3),
            "footage_offset": round(float(entry.get("source_start") or 0.0), 3),
            "query": str(entry.get("query") or "")[:120],
            "concept": str(entry.get("concept") or "")[:500],
            "search_query": str(entry.get("search_query") or "")[:200],
            "search_queries": search_queries,
            "avoid": avoid,
            "media": str(entry.get("media") or "either")[:20],
            "provider": provider,
            "asset_id": entry["asset_id"],
            "page_url": str(entry.get("url") or "")[:500],
            "file_url": file_url,
            "width": width,
            "height": height,
            "footage_duration": round(footage_duration, 3),
            "thumbnail": str(entry.get("thumbnail") or "")[:500],
            "credit": "",
            "fade_in": True,
            "fade_out": True,
            "fade_duration": None,
        }
    except (KeyError, TypeError, ValueError):
        return None


def _footage_from_stock_asset(provider: str, asset: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Editor footage (``BRollFootage`` fields) for a resolved stock asset."""
    try:
        width = int(asset.get("width") or 0)
        height = int(asset.get("height") or 0)
        duration = float(asset.get("duration") or 0)
        file_url = str(asset.get("download_url") or "")
        if width <= 0 or height <= 0 or duration <= 0 or not file_url:
            return None
        return {
            "provider": provider,
            "id": asset["asset_id"],
            "width": width,
            "height": height,
            "duration": round(duration, 3),
            "fileUrl": file_url,
            "pageUrl": str(asset.get("page_url") or "")[:500],
            "thumbnail": str(asset.get("thumbnail_url") or "")[:500],
            "credit": "",
        }
    except (KeyError, TypeError, ValueError):
        return None


async def next_broll_candidate(
    clip: Dict[str, Any],
    *,
    exclude_ids: Iterable[Any] = (),
    orientation: str = "portrait",
    target_short_side: int = 1080,
) -> Optional[Dict[str, Any]]:
    """The next stock candidate for one editor B-roll clip, or None.

    Re-resolves the cue through the same resolver its current footage
    came from, skipping every id in ``exclude_ids`` (the footage already
    shown for this cue, plus the draft's other clips when the caller
    passes them), so "Next candidate" always offers a different asset.
    A legacy Pexels cue migrates: it re-resolves through the v2 chain
    (Pixabay first, Coverr second) with the cue's sense-resolved
    search string, and the replacement carries the winning v2
    provider. A Pixabay/Coverr cue re-runs its resolver with the
    cue's concept phrases, avoid list and media preference, and the
    vision veto when the deployment has it on. None means the pool is
    exhausted — the caller keeps the current footage; no candidate beats
    keeping the one the user already has, and a wrong insert is never
    substituted for it.

    Returns a replacement ``BRollClip`` dict (same id/timing, offset
    reset, concept fields preserved) or None.
    """
    from .editor_document import STOCK_CDN_HOSTS

    footage = clip.get("footage") or {}
    provider = str(footage.get("provider") or "pexels")
    if provider not in STOCK_CDN_HOSTS:
        return None
    query = str(clip.get("query") or "")
    search_query = str(clip.get("searchQuery") or "")
    concept = str(clip.get("concept") or "")
    search_queries = [
        str(item).strip()
        for item in (clip.get("searchQueries") or [])
        if str(item).strip()
    ][:3]
    avoid = [
        str(item).strip()
        for item in (clip.get("avoid") or [])
        if str(item).strip()
    ][:20]
    media = str(clip.get("media") or "either")
    cue_duration = float(clip.get("end", 0) or 0) - float(clip.get("start", 0) or 0)
    if cue_duration <= 0:
        return None
    excluded = {str(item) for item in exclude_ids}
    excluded.add(str(footage.get("id")))
    replacement: Optional[Dict[str, Any]] = None

    veto_session = None
    try:
        from .media.broll_vision import VisionVetoSession, vision_veto_enabled

        if vision_veto_enabled():
            candidate_session = VisionVetoSession()
            if candidate_session.available:
                veto_session = candidate_session
            else:
                candidate_session.close()
    except Exception as exc:
        logger.warning("B-roll next-candidate veto unavailable: %s", type(exc).__name__)
        veto_session = None
    try:
        if provider == "pexels":
            # Legacy cue: its footage came from the retired Pexels path.
            # "Next candidate" migrates the cue onto the v2 resolver —
            # Pixabay first, Coverr second, the cue's own search strings
            # — and the replacement carries the winning v2 provider.
            # The exclusion list names Pexels ids, which say nothing
            # about v2 assets, so it is not carried across; once the cue
            # holds v2 footage, later candidates exclude per provider
            # exactly like any other v2 cue.
            from .media.coverr_broll import coverr_configured, ensure_coverr_asset
            from .media.pixabay_broll import ensure_pixabay_asset, pixabay_configured

            asset = None
            winning_provider = ""
            for text in dict.fromkeys(q for q in (search_query, query) if q):
                if asset is None and pixabay_configured():
                    asset = await asyncio.to_thread(
                        ensure_pixabay_asset,
                        query,
                        context="",
                        search_query=text,
                        veto_session=veto_session,
                        concept=concept,
                        avoid=avoid,
                        search_queries=search_queries,
                        media=media,
                        cue_duration=cue_duration,
                    )
                    if asset is not None:
                        winning_provider = "pixabay"
                if asset is None and coverr_configured():
                    asset = await asyncio.to_thread(
                        ensure_coverr_asset,
                        query,
                        context="",
                        search_query=text,
                        veto_session=veto_session,
                        concept=concept,
                        avoid=avoid,
                        search_queries=search_queries,
                        media=media,
                    )
                    if asset is not None:
                        winning_provider = "coverr"
                if asset is not None:
                    break
            if asset is not None:
                replacement = _footage_from_stock_asset(winning_provider, asset)
        elif provider == "pixabay":
            from .media.pixabay_broll import ensure_pixabay_asset

            asset = await asyncio.to_thread(
                ensure_pixabay_asset,
                query,
                context="",
                search_query=search_query,
                veto_session=veto_session,
                concept=concept,
                avoid=avoid,
                search_queries=search_queries,
                media=media,
                cue_duration=cue_duration,
                exclude_ids=excluded,
            )
            if asset is not None:
                replacement = _footage_from_stock_asset(provider, asset)
        else:
            from .media.coverr_broll import ensure_coverr_asset

            asset = await asyncio.to_thread(
                ensure_coverr_asset,
                query,
                context="",
                search_query=search_query,
                veto_session=veto_session,
                concept=concept,
                avoid=avoid,
                search_queries=search_queries,
                media=media,
                exclude_ids=excluded,
            )
            if asset is not None:
                replacement = _footage_from_stock_asset(provider, asset)
    finally:
        if veto_session is not None:
            veto_session.close()

    if replacement is None:
        return None
    end = float(clip["end"])
    start = float(clip["start"])
    end = min(end, start + float(replacement["duration"]))
    if end - start < 0.2:
        return None
    return {
        **clip,
        "end": round(end, 3),
        "offset": 0,
        "footage": replacement,
    }
