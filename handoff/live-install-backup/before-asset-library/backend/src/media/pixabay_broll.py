"""Pixabay stock B-roll for auto-director EDL renders.

The EDL builder (``auto_director/edl.py``) plans timed B-roll slots —
a search query plus a start/end window on the clip timeline — but never
touches the network. Resolution happens here, lazily, at render time:
each planned slot is searched on Pixabay (an ambiguous trigger is
searched with its spoken sense context — the slot's ``search_query``,
e.g. "train station" — never as the bare noun alone), the first hit
the relevance gate accepts (its tags must share a descriptive word
with the query noun or the words spoken in/near the cue's window, and
must not show the wrong sense of an ambiguous word — see
``_hit_mismatch``) is downloaded once into a disk cache, and the EDL
entry is stamped with ``local_path`` / ``asset_id`` / ``url`` so the
renderer can composite the cutaway and the provenance of every stock
asset stays on the EDL. When no hit passes the gate the cue is skipped
rather than inserting unrelated footage. With the vision veto enabled
(``BROLL_VISION_VETO_ENABLED``, Auto template mode only), a
tag-accepted hit must also convince a SigLIP image/text model that
its frames actually show the cue's subject (see
``media/broll_vision.py``) — tags can share the right words while the
picture shows something else.

Smart B-Roll v2, Slice 3: a concept cue (Slice 2's planner-written
``concept`` / ``search_queries`` / ``avoid`` / ``media``) is resolved
on the concept, not the bare noun — its phrases are tried in order at
8 results each, the tag gate judges hits against the concept sentence
plus the spoken context with the avoid list as negative evidence, and
a ``photo`` (or ``either``) preference also searches the Pixabay
photos endpoint, rendering a winning still into a cue-length Ken
Burns clip so the renderer still receives a video file. Entries
without concept fields resolve exactly as before: one search, 5
results, videos only, gate on the noun.

Everything is best-effort by design: without a stock API key (or on
any provider/network failure) resolution is a no-op and the EDL renders
exactly as it did before this module existed. Pixabay is the primary
stock source; Coverr (``media/coverr_broll.py``) is the second source,
asked per entry only when Pixabay is unconfigured, fails, returns
nothing, or none of its hits pass the relevance gate — one provider's
failure never blocks the other. Pexels paused new API keys, so the
profiles' B-roll ``source`` value is ``"stock"`` — "stock B-roll
wanted", resolved here — and the actual provider is recorded per
entry as ``provider: "pixabay"`` or ``provider: "coverr"``. (Stored
EDLs planned before the rename still carry the legacy ``"pexels"``
value; the resolver never branches on ``source``, so they resolve
identically.) Downloads from both
providers land in the shared stock library (``media/stock_library``),
which keeps each asset on disk exactly once with a provenance
manifest.
"""

from __future__ import annotations

import logging
from pathlib import Path
import threading
from time import perf_counter
from typing import Any, Optional

import httpx

from ..config import get_config
from . import stock_library

logger = logging.getLogger(__name__)

PIXABAY_VIDEOS_URL = "https://pixabay.com/api/videos/"
# The photos endpoint shares the videos key and the same /api/ host.
PIXABAY_PHOTOS_URL = "https://pixabay.com/api/"
HTTP_TIMEOUT = httpx.Timeout(20.0, connect=10.0)
MAX_DOWNLOAD_BYTES = 150 * 1024 * 1024
SEARCH_RESULTS_PER_QUERY = 5
# Smart B-Roll v2, Slice 3 (design §4.4): concept cues dig deeper —
# more candidates for the re-pick loop, and the concept's 2–3 phrases
# tried in order. The no-concept path keeps 5, exactly as before.
CONCEPT_SEARCH_RESULTS_PER_PAGE = 8
# Output clips are 1080x1920 portrait. Prefer a portrait rendition at
# least this wide (it is cropped to fill the frame); fall back to the
# largest rendition available when no portrait cut qualifies.
PREFERRED_MIN_WIDTH = 720
# A photo cutaway is a slow zoom over the cue, so the still becomes a
# clip of this size; the zoom range stays gentle (1.00 -> ~1.10).
KEN_BURNS_WIDTH = 1080
KEN_BURNS_HEIGHT = 1920
KEN_BURNS_FPS = 30
KEN_BURNS_ZOOM_STEP = 0.0012
KEN_BURNS_MAX_ZOOM = 1.15

# In-process search cache: one task renders several clips whose slots
# often repeat the same query, and Pixabay answers are stable enough
# for the life of a worker process. Photos keep a separate cache: the
# same query string hits a different endpoint with different results.
_search_cache: dict[str, list[dict[str, Any]]] = {}
_photo_search_cache: dict[str, list[dict[str, Any]]] = {}


# Search calls swallow their exceptions and return no hits, which used to make
# a rejected key (401/403) or a rate limit (429) indistinguishable from "the
# library has nothing relevant" on the record. A per-thread failure count lets
# the resolver stamp those cues ``provider_error`` instead of ``provider_empty``.
_SEARCH_FAILURES = threading.local()


def _note_search_failure() -> None:
    _SEARCH_FAILURES.count = getattr(_SEARCH_FAILURES, "count", 0) + 1


def search_failure_count() -> int:
    return int(getattr(_SEARCH_FAILURES, "count", 0))


def _api_key() -> str:
    try:
        return (get_config().pixabay_api_key or "").strip()
    except Exception:
        return ""


def pixabay_configured() -> bool:
    """True when a Pixabay API key is available (env PIXABAY_API_KEY)."""
    return bool(_api_key())


def broll_cache_dir() -> Path:
    # Downloads live in the shared stock library (see stock_library):
    # one persistent store for every provider, with the manifest.
    return stock_library.library_dir()


def _new_client() -> httpx.Client:
    return httpx.Client(timeout=HTTP_TIMEOUT, follow_redirects=True)


def _choose_rendition(hit: dict[str, Any]) -> Optional[dict[str, Any]]:
    """Pick the download rendition for one Pixabay hit, deterministically.

    Portrait first (the output is 9:16): the smallest portrait rendition
    at least PREFERRED_MIN_WIDTH wide, so downloads stay lean. When no
    portrait rendition qualifies, the largest rendition by pixel area.
    """
    videos = hit.get("videos") or {}
    renditions = [
        v for v in videos.values()
        if isinstance(v, dict) and v.get("url")
    ]
    if not renditions:
        return None

    def width(v: dict) -> int:
        return int(v.get("width") or 0)

    def height(v: dict) -> int:
        return int(v.get("height") or 0)

    portrait = [
        v for v in renditions
        if height(v) >= width(v) and width(v) >= PREFERRED_MIN_WIDTH
    ]
    if portrait:
        return min(portrait, key=lambda v: (width(v), height(v)))
    return max(renditions, key=lambda v: (width(v) * height(v), width(v)))


def search_pixabay_videos(
    query: str,
    *,
    per_page: int = SEARCH_RESULTS_PER_QUERY,
    client: Optional[httpx.Client] = None,
) -> list[dict[str, Any]]:
    """Parsed Pixabay hits for ``query``; empty list on any failure.

    Each hit is normalised to {asset_id, page_url, download_url, width,
    height, duration}. Results are cached in-process per query.
    """
    api_key = _api_key()
    query = " ".join((query or "").split())[:100]
    if not api_key or not query:
        return []

    cache_key = query.lower()
    if cache_key in _search_cache:
        return _search_cache[cache_key]

    own_client = client is None
    client = client or _new_client()
    try:
        response = client.get(
            PIXABAY_VIDEOS_URL,
            params={
                "key": api_key,
                "q": query,
                "per_page": per_page,
                "safesearch": "true",
            },
        )
        response.raise_for_status()
        payload = response.json()
    except Exception as exc:
        logger.warning("Pixabay search failed for %r: %s", query, type(exc).__name__)
        _note_search_failure()
        return []
    finally:
        if own_client:
            client.close()

    results: list[dict[str, Any]] = []
    for hit in payload.get("hits") or []:
        if not isinstance(hit, dict):
            continue
        rendition = _choose_rendition(hit)
        if rendition is None or hit.get("id") is None:
            continue
        renditions = [
            v for v in (hit.get("videos") or {}).values()
            if isinstance(v, dict) and v.get("url")
        ]
        preview = (
            min(
                renditions,
                key=lambda v: int(v.get("width") or 0) * int(v.get("height") or 0),
            )
            if renditions
            else None
        )
        thumbnail_url = ""
        for candidate_rendition in [rendition, *renditions]:
            thumb = candidate_rendition.get("thumbnail")
            if isinstance(thumb, str) and thumb:
                thumbnail_url = thumb
                break
        results.append({
            "asset_id": hit["id"],
            "page_url": str(hit.get("pageURL") or ""),
            "download_url": str(rendition["url"]),
            "width": int(rendition.get("width") or 0),
            "height": int(rendition.get("height") or 0),
            "duration": float(hit.get("duration") or 0),
            # Tags ride along so the relevance gate can judge the hit:
            # Pixabay's own keywords are the only description the API
            # returns for a video hit.
            "tags": str(hit.get("tags") or ""),
            # Vision-veto inputs (media/broll_vision.py): the smallest
            # rendition, whose frames represent the clip cheaply, and a
            # poster thumbnail when the payload carries one.
            "preview_url": str(preview["url"]) if preview else "",
            "thumbnail_url": thumbnail_url,
        })
    _search_cache[cache_key] = results
    return results


def search_pixabay_photos(
    query: str,
    *,
    per_page: int = CONCEPT_SEARCH_RESULTS_PER_PAGE,
    client: Optional[httpx.Client] = None,
) -> list[dict[str, Any]]:
    """Parsed Pixabay photo hits for ``query``; empty list on any failure.

    The same free key covers the photos endpoint (``pixabay.com/api/``,
    ``image_type=photo``). Each hit is normalised to the shape the
    resolver consumes from video hits, with ``"media": "photo"`` and
    ``duration`` 0: a photo has no duration of its own — the cue's
    length decides how long its Ken Burns clip runs. The large image
    is the download (it is cropped to fill a 1080x1920 frame, so the
    640 px web format would upscale); the web format doubles as the
    vision-veto thumbnail. Results are cached in-process per query,
    separately from the video cache.
    """
    api_key = _api_key()
    query = " ".join((query or "").split())[:100]
    if not api_key or not query:
        return []

    cache_key = query.lower()
    if cache_key in _photo_search_cache:
        return _photo_search_cache[cache_key]

    own_client = client is None
    client = client or _new_client()
    try:
        response = client.get(
            PIXABAY_PHOTOS_URL,
            params={
                "key": api_key,
                "q": query,
                "image_type": "photo",
                "per_page": per_page,
                "safesearch": "true",
            },
        )
        response.raise_for_status()
        payload = response.json()
    except Exception as exc:
        logger.warning("Pixabay photo search failed for %r: %s", query, type(exc).__name__)
        _note_search_failure()
        return []
    finally:
        if own_client:
            client.close()

    results: list[dict[str, Any]] = []
    for hit in payload.get("hits") or []:
        if not isinstance(hit, dict) or hit.get("id") is None:
            continue
        download_url = str(hit.get("largeImageURL") or hit.get("webformatURL") or "")
        if not download_url:
            continue
        results.append({
            "asset_id": hit["id"],
            "page_url": str(hit.get("pageURL") or ""),
            "download_url": download_url,
            "width": int(hit.get("imageWidth") or hit.get("webformatWidth") or 0),
            "height": int(hit.get("imageHeight") or hit.get("webformatHeight") or 0),
            "duration": 0.0,
            "tags": str(hit.get("tags") or ""),
            "preview_url": str(hit.get("previewURL") or ""),
            "thumbnail_url": str(hit.get("webformatURL") or ""),
            "media": "photo",
        })
    _photo_search_cache[cache_key] = results
    return results


def _ken_burns_filter(duration: float) -> str:
    """The zoompan filter that turns one still into a slow-zoom clip.

    The still is scaled to twice the output size and cropped to the
    9:16 frame first, so the zoom has headroom and never upscales past
    the source; zoompan then steps the zoom from 1.00 towards
    KEN_BURNS_MAX_ZOOM, centred, one frame at a time. ``d`` is the
    frame count of the whole clip, so the zoom spans the cue exactly.
    """
    frames = max(1, int(round(float(duration) * KEN_BURNS_FPS)))
    zoom = (
        f"min(zoom+{KEN_BURNS_ZOOM_STEP},{KEN_BURNS_MAX_ZOOM})"
    )
    return (
        f"scale={KEN_BURNS_WIDTH * 2}:{KEN_BURNS_HEIGHT * 2}:"
        f"force_original_aspect_ratio=increase:flags=lanczos,"
        f"crop={KEN_BURNS_WIDTH * 2}:{KEN_BURNS_HEIGHT * 2},"
        f"zoompan=z='{zoom}':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':"
        f"d={frames}:s={KEN_BURNS_WIDTH}x{KEN_BURNS_HEIGHT}:fps={KEN_BURNS_FPS},"
        f"format=yuv420p"
    )


def still_to_clip(
    photo_path: Path,
    duration: float,
    out_path: Path,
) -> bool:
    """Render a still photo into a cue-length Ken Burns mp4.

    Returns False (and leaves no partial file) when ffmpeg cannot run
    or the still is not a readable image — the caller falls through to
    the next candidate exactly as it would for a rejected video hit.
    A photo cutaway is therefore never a render failure, only a
    skipped cue.
    """
    from .ffmpeg import run_ffmpeg_command

    duration = max(0.5, float(duration))
    frames = max(1, int(round(duration * KEN_BURNS_FPS)))
    out_path.parent.mkdir(parents=True, exist_ok=True)
    # The scratch name keeps the .mp4 extension (".part.mp4", not
    # ".mp4.part") so ffmpeg can infer the output format from it.
    part = out_path.with_name(out_path.stem + ".part.mp4")
    command = [
        "ffmpeg", "-y",
        "-i", str(photo_path),
        "-vf", _ken_burns_filter(duration),
        "-frames:v", str(frames),
        "-c:v", "libx264",
        "-pix_fmt", "yuv420p",
        "-an",
        str(part),
    ]
    try:
        result = run_ffmpeg_command(command, timeout=120)
        if result.returncode != 0 or not part.is_file() or part.stat().st_size <= 0:
            raise ValueError("Ken Burns render produced no file")
        part.replace(out_path)
        return True
    except Exception as exc:
        logger.warning(
            "Ken Burns still-to-clip failed for %s: %s",
            photo_path, type(exc).__name__,
        )
        part.unlink(missing_ok=True)
        return False


def _download(client: httpx.Client, url: str, target: Path) -> bool:
    """Stream ``url`` to ``target`` (via a .part file); False on failure."""
    target.parent.mkdir(parents=True, exist_ok=True)
    part = target.with_suffix(target.suffix + ".part")
    written = 0
    try:
        with client.stream("GET", url) as response:
            response.raise_for_status()
            with open(part, "wb") as handle:
                for chunk in response.iter_bytes(chunk_size=256 * 1024):
                    written += len(chunk)
                    if written > MAX_DOWNLOAD_BYTES:
                        raise ValueError("Pixabay asset exceeds the download size cap")
                    handle.write(chunk)
        if written <= 0:
            raise ValueError("Pixabay asset download was empty")
        part.replace(target)
        return True
    except Exception as exc:
        logger.warning("Pixabay download failed for %s: %s", url, type(exc).__name__)
        part.unlink(missing_ok=True)
        return False


def _hit_mismatch(
    hit: dict[str, Any],
    query: str,
    context: str = "",
    *,
    concept: str = "",
    avoid: Any = (),
) -> Optional[str]:
    """Why a Pixabay hit does not fit the cue, or None when it does.

    Reuses the legacy B-roll system's judge (``broll.footage_mismatch``):
    a hit is rejected when its tags carry a sensitive theme the spoken
    context is not about, when its tags show the wrong sense of an
    ambiguous trigger word (a railway hit for exercise "train" speech —
    sharing the bare word does not save it), or when its tags share no
    descriptive word with the query noun *plus the words spoken in/near
    the inserted window*. Hits without tags cannot be judged and are
    allowed, exactly as the legacy system allows undescribed footage. A
    rejected hit never costs the clip its render — the caller tries the
    next hit, then skips the cue (self-run audit issue #1: hit #1 used
    to win unconditionally, which put highway traffic over unrelated
    speech).

    Smart B-Roll v2, Slice 3: when ``concept`` is given (a concept
    cue), the gate judges against the concept sentence plus the spoken
    context instead of the bare query noun — the concept is what the
    footage is supposed to show — and the concept's ``avoid`` list is
    negative evidence: a hit matching an avoid phrase is rejected even
    if it also shares a concept word. Without a concept every argument
    defaults away and the judgement is exactly today's.
    """
    try:
        from ..broll import footage_mismatch
    except Exception:
        return None
    tags = str(hit.get("tags") or "")
    pseudo_video = {
        "title": tags,
        "tags": [tag.strip() for tag in tags.split(",") if tag.strip()],
        "url": str(hit.get("page_url") or ""),
    }
    try:
        if concept:
            judge = f"{concept} {context}".strip()
            return footage_mismatch(
                pseudo_video, judge, context, avoid=list(avoid or ())
            )
        return footage_mismatch(pseudo_video, f"{query} {context}".strip(), context)
    except Exception:
        return None


def _candidate_frames_for_hit(
    hit: dict[str, Any],
    *,
    client: httpx.Client,
    cache_dir: Path,
) -> list:
    """Representative frames of one hit for the vision veto.

    The poster thumbnail when the payload carries one (a single small
    image download); otherwise frames cut from the hit's smallest
    rendition, downloaded once into a ``vision_preview`` scratch area
    of the B-roll cache. Any failure returns no frames, which the veto
    reads as "unjudged" — never as a veto.
    """
    from . import broll_vision

    thumbnail_bytes: Optional[bytes] = None
    thumbnail_url = str(hit.get("thumbnail_url") or "")
    if thumbnail_url:
        try:
            response = client.get(thumbnail_url)
            response.raise_for_status()
            thumbnail_bytes = response.content
        except Exception as exc:
            logger.debug(
                "Vision veto: thumbnail fetch failed for hit %s: %s",
                hit.get("asset_id"), type(exc).__name__,
            )
            thumbnail_bytes = None

    preview_path: Optional[Path] = None
    if not thumbnail_bytes:
        preview_url = str(hit.get("preview_url") or hit.get("download_url") or "")
        if preview_url:
            scratch = (
                Path(cache_dir)
                / "vision_preview"
                / f"pixabay_{hit['asset_id']}_preview.mp4"
            )
            if scratch.is_file() and scratch.stat().st_size > 0:
                preview_path = scratch
            elif _download(client, preview_url, scratch):
                preview_path = scratch
    return broll_vision.candidate_frames(
        thumbnail_bytes=thumbnail_bytes,
        preview_path=preview_path,
        duration=float(hit.get("duration") or 0),
    )


def _vision_check(
    session: Any,
    hit: dict[str, Any],
    subject: str,
    *,
    client: httpx.Client,
    cache_dir: Path,
    deadline: Optional[float],
    avoid_subject: str = "",
    concept_subject: bool = False,
) -> tuple[bool, Optional[float], Optional[float]]:
    """(vetoed, concept_score, avoid_score) for one tag-accepted hit.

    ``score`` None means unjudged — budget exhausted, no frames, model
    trouble — and unjudged never vetoes (fail-open). Scores are cached
    in-process per (asset, subject), so a repeated resolve of the same
    cue costs no downloads and no inference. Smart B-Roll v2, Slice 4:
    ``subject`` is the concept sentence for concept cues (the caller
    picks it); when ``avoid_subject`` names the wrong sense, the same
    frames are scored against it too and the veto is the contrastive
    rule (``broll_vision.should_veto_contrastive``) — a hit that looks
    more like the wrong sense than the right one is vetoed even above
    threshold. Without an avoid subject this is exactly today's
    threshold check.
    """
    from . import broll_vision

    asset_id = hit.get("asset_id")
    if session is None or asset_id is None:
        return False, None, None
    if deadline is not None and perf_counter() > deadline:
        return False, None, None
    frames: Optional[list] = None

    def _score_one(text: str) -> Optional[float]:
        nonlocal frames
        cached = broll_vision.cached_score(asset_id, text)
        if cached is not None:
            return cached
        if frames is None:
            frames = _candidate_frames_for_hit(hit, client=client, cache_dir=cache_dir)
        score = session.score(frames, text)
        if score is not None:
            broll_vision.store_score(asset_id, text, score)
        return score

    score = _score_one(subject)
    avoid_score = _score_one(avoid_subject) if avoid_subject.strip() else None
    if score is None:
        return False, None, avoid_score
    vetoed = broll_vision.should_veto_contrastive(
        score,
        avoid_score,
        broll_vision.vision_veto_threshold(concept=concept_subject),
    )
    return vetoed, score, avoid_score


def _accept_hit(
    hit: dict[str, Any],
    query: str,
    context: str,
    *,
    concept: str = "",
    avoid: Any = (),
    veto_session: Any = None,
    veto_subject: str = "",
    client: httpx.Client,
    cache_dir: Path,
    deadline: Optional[float],
    veto_sink: Optional[dict[str, Any]] = None,
) -> bool:
    """Gate + vision veto for one candidate hit; False means try the next.

    The relevance gate (``_hit_mismatch``) runs first; a tag-accepted
    hit with a veto session must also survive the vision check. Smart
    B-Roll v2, Slice 4: the veto subject is the concept sentence for
    concept cues (``veto_subject``, chosen by the caller), and the
    concept's ``avoid`` list is scored as the contrastive subject — a
    hit that looks more like the wrong sense than the right one is
    vetoed even above threshold. The gate is the Slice 3 change — it
    judges concept cues against the concept + avoid list.
    """
    reason = _hit_mismatch(hit, query, context, concept=concept, avoid=avoid)
    if reason:
        logger.info(
            "Skipping Pixabay hit %s for %r: %s",
            hit.get("asset_id"), query, reason,
        )
        return False
    if veto_session is not None:
        avoid_subject = ", ".join(
            str(item).strip() for item in (avoid or ()) if str(item).strip()
        )
        vetoed, score, avoid_score = _vision_check(
            veto_session,
            hit,
            veto_subject,
            client=client,
            cache_dir=cache_dir,
            deadline=deadline,
            avoid_subject=avoid_subject,
            concept_subject=bool(concept),
        )
        if score is not None and veto_sink is not None:
            veto_sink.setdefault("scores", {})[str(hit["asset_id"])] = round(score, 4)
        if avoid_score is not None and veto_sink is not None:
            veto_sink.setdefault("avoid_scores", {})[str(hit["asset_id"])] = round(
                avoid_score, 4
            )
        if vetoed:
            if veto_sink is not None:
                veto_sink.setdefault("vetoed", []).append(hit["asset_id"])
            logger.info(
                "Vision veto: Pixabay hit %s for %r scored %.4f"
                + (" (avoid %.4f)" if avoid_score is not None else "")
                + "; falling through to the next hit",
                hit.get("asset_id"), query, score,
                *([avoid_score] if avoid_score is not None else []),
            )
            return False
        if score is not None and veto_sink is not None:
            veto_sink["winner_score"] = round(score, 4)
    return True


def ensure_pixabay_asset(
    query: str,
    *,
    cache_dir: Optional[Path] = None,
    client: Optional[httpx.Client] = None,
    context: str = "",
    search_query: str = "",
    veto_session: Any = None,
    veto_sink: Optional[dict[str, Any]] = None,
    concept: str = "",
    avoid: Any = (),
    search_queries: Any = (),
    media: str = "either",
    cue_duration: float = 3.0,
    # Smart B-Roll v2, Slice 6 (editor "Next candidate"): asset ids to
    # skip without judging them — the footage already shown for this
    # cue. Compared as strings, so an int Pixabay id and a string
    # Coverr id exclude the same way. Empty by default: every existing
    # caller resolves exactly as before.
    exclude_ids: Any = (),
    # Retention lane: when given, a tally of what the candidate pool
    # did (hits / excluded / gate_rejected / vetoed / download_failed)
    # so the caller can stamp WHY a cue went unfilled instead of
    # leaving a bare entry. Counts only; never changes behaviour.
    outcome_sink: Optional[dict[str, Any]] = None,
) -> Optional[dict[str, Any]]:
    """Resolve ``query`` to a downloaded stock file, or None.

    Returns {asset_id, page_url, local_path}. A file already in the disk
    cache is reused without any network call — the same Pixabay asset is
    never downloaded twice. Hits are tried in Pixabay's relevance order
    and the first one the relevance gate accepts wins; when every hit
    fails the gate the cue is skipped (None) rather than inserting
    unrelated footage. The gate always judges against ``query`` (the
    planned noun) plus ``context``; ``search_query``, when given, only
    changes the string sent to Pixabay — the planner stamps it with the
    trigger's spoken sense context so an ambiguous noun is not searched
    bare.

    When ``veto_session`` is given (the resolver builds one only for
    Auto template EDLs with the vision veto enabled), each tag-accepted
    hit must also survive the vision check in ``media/broll_vision.py``
    before it is downloaded: a vetoed hit falls through to the next
    candidate exactly like a tag-rejected one. ``veto_sink``, when
    given, collects the evidence (per-hit scores, vetoed asset ids,
    the winner's score) for the EDL stamp.

    Smart B-Roll v2, Slice 3 — concept cues (``concept`` /
    ``search_queries`` given): the concept's 2–3 search phrases are
    tried in order (each at 8 results, not 5), the gate judges each hit
    against the concept sentence + spoken context with the concept's
    ``avoid`` list as negative evidence, and the ``media`` preference
    decides the sources: ``video`` searches videos only, ``photo``
    searches the photos endpoint only (each winning still is rendered
    into a cue-length Ken Burns clip, so ``local_path`` is always a
    playable video for the overlay graph), ``either`` tries videos
    first, then photos. The first phrase that resolves wins, so a cue
    whose first phrase already finds its footage costs exactly one
    search, as before. Without a concept every new argument defaults
    away: one search, 5 results, videos only, gate on the noun.
    """
    concept_mode = bool(concept or search_queries)
    queries = [
        str(text).strip()
        for text in (search_queries or [])
        if str(text).strip()
    ]
    if not queries:
        queries = [search_query or query]
    per_page = (
        CONCEPT_SEARCH_RESULTS_PER_PAGE if concept_mode
        else SEARCH_RESULTS_PER_QUERY
    )
    media_pref = (media or "either").strip().lower()
    if media_pref not in ("video", "photo", "either"):
        media_pref = "either"

    cache = Path(cache_dir) if cache_dir is not None else broll_cache_dir()
    own_client = client is None
    client = client or _new_client()
    veto_deadline: Optional[float] = None
    if veto_session is not None:
        from .broll_vision import vision_veto_budget_seconds

        veto_deadline = perf_counter() + vision_veto_budget_seconds()
    # The subject the veto judges frames against. Smart B-Roll v2,
    # Slice 4: a concept cue is judged on the planner's concept
    # sentence (the richest phrasing of what the footage should show);
    # a cue without a concept keeps today's sense-resolved search
    # string, else the planned query itself.
    veto_subject = (concept or search_query or query).strip()
    excluded = {str(item) for item in (exclude_ids or ())}

    def _tally(key: str) -> None:
        if outcome_sink is not None:
            outcome_sink[key] = int(outcome_sink.get(key, 0)) + 1

    def _judge(hit: dict[str, Any]) -> bool:
        """_accept_hit plus outcome tallying (veto vs gate split)."""
        vetoed_before = len((veto_sink or {}).get("vetoed") or [])
        accepted = _accept_hit(
            hit, query, context,
            concept=concept, avoid=avoid,
            veto_session=veto_session,
            veto_subject=veto_subject,
            client=client, cache_dir=cache,
            deadline=veto_deadline, veto_sink=veto_sink,
        )
        if not accepted:
            vetoed_now = len((veto_sink or {}).get("vetoed") or [])
            _tally("vetoed" if vetoed_now > vetoed_before else "gate_rejected")
        return accepted

    try:
        if media_pref != "photo":
            for search_text in queries:
                hits = search_pixabay_videos(
                    search_text, per_page=per_page, client=client
                )
                for hit in hits:
                    _tally("hits")
                    if str(hit.get("asset_id")) in excluded:
                        _tally("excluded")
                        continue
                    if not _judge(hit):
                        continue
                    target = cache / f"pixabay_{hit['asset_id']}.mp4"
                    if target.is_file() and target.stat().st_size > 0:
                        stock_library.record_asset(
                            cache, provider="pixabay", candidate=hit, query=query,
                            search_query=search_text, local_path=str(target),
                            downloaded=False,
                        )
                        return {**hit, "local_path": str(target)}
                    if _download(client, hit["download_url"], target):
                        stock_library.record_asset(
                            cache, provider="pixabay", candidate=hit, query=query,
                            search_query=search_text, local_path=str(target),
                            downloaded=True,
                        )
                        return {**hit, "local_path": str(target)}
                    _tally("download_failed")
        if media_pref != "video":
            for search_text in queries:
                hits = search_pixabay_photos(
                    search_text, per_page=per_page, client=client
                )
                for hit in hits:
                    _tally("hits")
                    if str(hit.get("asset_id")) in excluded:
                        _tally("excluded")
                        continue
                    if not _judge(hit):
                        continue
                    # A photo has no duration of its own: the still is
                    # rendered into a cue-length slow-zoom clip, and
                    # the clip — not the still — is what the renderer
                    # composites. The still file is named distinctly
                    # from the video cache (``pixabay_<id>.mp4``) so a
                    # photo id can never collide with a video id.
                    still_target = cache / f"pixabay_{hit['asset_id']}_still.jpg"
                    clip_target = cache / f"pixabay_{hit['asset_id']}_still.mp4"
                    if clip_target.is_file() and clip_target.stat().st_size > 0:
                        stock_library.record_asset(
                            cache, provider="pixabay",
                            candidate={**hit, "duration": cue_duration},
                            query=query, search_query=search_text,
                            local_path=str(clip_target), downloaded=False,
                        )
                        return {
                            **hit, "local_path": str(clip_target),
                            "media": "photo",
                        }
                    if not still_target.is_file() or still_target.stat().st_size <= 0:
                        if not _download(client, hit["download_url"], still_target):
                            _tally("download_failed")
                            continue
                    if still_to_clip(still_target, cue_duration, clip_target):
                        stock_library.record_asset(
                            cache, provider="pixabay",
                            candidate={**hit, "duration": cue_duration},
                            query=query, search_query=search_text,
                            local_path=str(clip_target), downloaded=True,
                        )
                        return {
                            **hit, "local_path": str(clip_target),
                            "media": "photo",
                        }
    finally:
        if own_client:
            client.close()
    logger.warning(
        "No Pixabay hit for %r passed the relevance gate; skipping the B-roll cue",
        query,
    )
    return None


def ensure_pixabay_hit(
    hit: dict[str, Any],
    *,
    cache_dir: Optional[Path] = None,
    client: Optional[httpx.Client] = None,
    query: str = "",
) -> Optional[dict[str, Any]]:
    """Download one already-searched Pixabay hit into the stock library.

    The editor's B-roll search shows specific hits the user picks from,
    so — unlike ``ensure_pixabay_asset`` — there is no search and no
    relevance gate here: the user is the judge. The file lands in the
    stock library under the same name the resolver uses, so a picked
    hit previews and exports through the library like any resolved
    asset. Returns the hit plus ``local_path``, or None when the
    download fails (the caller then simply does not offer the hit).
    """
    asset_id = hit.get("asset_id")
    download_url = str(hit.get("download_url") or "")
    if asset_id is None or not download_url:
        return None
    cache = Path(cache_dir) if cache_dir is not None else broll_cache_dir()
    own_client = client is None
    client = client or _new_client()
    try:
        target = cache / f"pixabay_{asset_id}.mp4"
        if target.is_file() and target.stat().st_size > 0:
            stock_library.record_asset(
                cache, provider="pixabay", candidate=hit, query=query,
                local_path=str(target), downloaded=False,
            )
            return {**hit, "local_path": str(target)}
        if _download(client, download_url, target):
            stock_library.record_asset(
                cache, provider="pixabay", candidate=hit, query=query,
                local_path=str(target), downloaded=True,
            )
            return {**hit, "local_path": str(target)}
        return None
    finally:
        if own_client:
            client.close()
def _not_inserted_reason(outcome: dict[str, Any], provider_error: bool) -> str:
    """Compose the stamped reason a cue went unfilled, from its tally."""
    hits = int(outcome.get("hits", 0))
    if hits == 0:
        return "provider_error" if provider_error else "provider_empty"
    if int(outcome.get("download_failed", 0)) > 0:
        return "download_failed"
    judged = int(outcome.get("gate_rejected", 0)) + int(outcome.get("vetoed", 0))
    if int(outcome.get("excluded", 0)) > 0 and judged == 0:
        # Every candidate was already committed to a sibling clip of
        # this task (the caller's exclusion set) — fewer inserts,
        # never a silent repeat.
        return "duplicate_in_task"
    if int(outcome.get("vetoed", 0)) > 0 and int(outcome.get("gate_rejected", 0)) == 0:
        return "veto_exhausted"
    return "gate_rejected"


def _stamp_not_inserted(entry: dict[str, Any], reason: str, outcome: Optional[dict[str, Any]] = None) -> None:
    """Record on the entry itself why it will not appear in the export.

    The stamp is the retention lane's core rule: a planned insert that
    does not land leaves an explanation on the EDL record (mirroring
    the vision-veto evidence stamp), never a bare absence.
    """
    stamp: dict[str, Any] = {"stage": "resolve", "reason": reason}
    if outcome:
        stamp["candidates"] = {
            key: int(outcome.get(key, 0))
            for key in ("hits", "excluded", "gate_rejected", "vetoed", "download_failed")
            if int(outcome.get(key, 0)) > 0
        }
    entry["not_inserted"] = stamp


def resolve_edl_broll(
    edl: dict[str, Any],
    *,
    cache_dir: Optional[Path] = None,
    client: Optional[httpx.Client] = None,
    exclude_ids: Any = (),
) -> int:
    """Stamp resolvable B-roll entries on ``edl`` with their stock file.

    Entries that already carry a ``local_path`` (user assets, a previous
    resolve) are left alone. Returns how many entries were resolved.
    Without any stock API key the entries cannot be filled, and each is
    stamped ``not_inserted`` (reason ``no_provider_configured``) rather
    than left bare. Each entry is
    asked of Pixabay first and of Coverr (the second source) only when
    Pixabay is unconfigured, fails, or no hit passes the gate; the
    winning provider is stamped on the entry. A concept entry (Slice 2
    fields: ``concept`` / ``search_queries`` / ``avoid`` / ``media``)
    is resolved on its concept phrases with the concept-aware gate;
    entries without them resolve exactly as before. ``exclude_ids``
    (asset ids already committed elsewhere — a task's earlier clips)
    are skipped at both providers, so a later clip falls to the next
    candidate instead of repeating footage.
    """
    from .coverr_broll import coverr_configured

    if not edl:
        return 0
    entries = edl.get("broll") or []
    if not entries:
        return 0
    if not (pixabay_configured() or coverr_configured()):
        for entry in entries:
            if (
                isinstance(entry, dict)
                and not entry.get("local_path")
                and str(entry.get("query") or "").strip()
            ):
                _stamp_not_inserted(entry, "no_provider_configured")
        return 0
    # Planning-time resolution stamps local_path on every entry it can, so
    # at render time this must be a cheap no-op: when nothing is left
    # unresolved, return before a client is even constructed — no HTTP,
    # no network setup on the render path.
    unresolved = [
        entry
        for entry in entries
        if isinstance(entry, dict)
        and not entry.get("local_path")
        and str(entry.get("query") or "").strip()
    ]
    if not unresolved:
        return 0

    # The vision veto (media/broll_vision.py) exists only for Auto
    # template EDLs with the deployment flag on: a Plain EDL carries no
    # B-roll entries at all, and the template check keeps it that way
    # even for hand-built EDLs. Building the session is cheap (the
    # model loads lazily, on the first candidate) and any failure to
    # build one simply means no veto for this pass.
    veto_session: Any = None
    if edl.get("template") != "plain":
        try:
            from .broll_vision import VisionVetoSession

            candidate_session = VisionVetoSession()
            if candidate_session.available:
                veto_session = candidate_session
            else:
                candidate_session.close()
        except Exception as exc:
            logger.warning("B-roll vision veto unavailable: %s", type(exc).__name__)
            veto_session = None

    own_client = client is None
    client = client or _new_client()
    resolved = 0
    # Winners within THIS resolve join the exclusion set as they land:
    # two cues of one clip must not repeat footage either — the
    # no-repeat rule is per task, and a clip is part of its task.
    live_exclusions = {str(item) for item in (exclude_ids or ())}
    try:
        for entry in entries:
            if not isinstance(entry, dict) or entry.get("local_path"):
                continue
            query = str(entry.get("query") or "").strip()
            if not query:
                continue
            veto_sink: dict[str, Any] = {}
            # The words spoken in/near this cue's window: the
            # relevance gate judges the hit against them as well
            # as the query noun.
            context = str(entry.get("context") or "")
            # The planner-stamped search string (sense context
            # included for ambiguous triggers); absent on
            # entries planned before it existed or by hand.
            entry_search_query = str(entry.get("search_query") or "")
            # Smart B-Roll v2, Slice 3: a concept cue carries the
            # planner's concept sentence, its 2–3 search phrases, the
            # wrong-sense list and the media preference — the resolver
            # consumes them, and the gate judges on the concept instead
            # of the bare noun. Entries without them (keyword-planned,
            # hand-built, or planned before Slice 2) pass empty
            # values and resolve exactly as before.
            entry_concept = str(entry.get("concept") or "")
            entry_avoid = [
                str(item) for item in (entry.get("avoid") or [])
                if str(item).strip()
            ]
            entry_search_queries = [
                str(item).strip()
                for item in (entry.get("search_queries") or [])
                if str(item).strip()
            ]
            entry_media = str(entry.get("media") or "either")
            try:
                entry_end = float(entry.get("end") or 0)
                entry_start = float(entry.get("start") or 0)
                entry_duration = entry_end - entry_start
            except (TypeError, ValueError):
                entry_duration = 0.0
            if entry_duration <= 0:
                entry_duration = 3.0
            asset: Optional[dict[str, Any]] = None
            provider = ""
            outcome: dict[str, Any] = {}
            provider_error = False
            failures_before = search_failure_count()
            if pixabay_configured():
                try:
                    asset = ensure_pixabay_asset(
                        query,
                        cache_dir=cache_dir,
                        client=client,
                        context=context,
                        search_query=entry_search_query,
                        veto_session=veto_session,
                        veto_sink=veto_sink,
                        concept=entry_concept,
                        avoid=entry_avoid,
                        search_queries=entry_search_queries,
                        media=entry_media,
                        cue_duration=entry_duration,
                        exclude_ids=tuple(live_exclusions),
                        outcome_sink=outcome,
                    )
                except Exception as exc:
                    logger.warning("B-roll resolve failed for %r: %s", query, type(exc).__name__)
                    asset = None
                    provider_error = True
                if asset is not None:
                    provider = "pixabay"
            if asset is None:
                # Second source: Pixabay unconfigured, down, empty, or
                # fully gate-rejected. Coverr gets the same query,
                # context and veto discipline; its own failure is just
                # a skipped cue, exactly like Pixabay's. Coverr is
                # video-only, so a photo-only concept never reaches it.
                from .coverr_broll import coverr_configured, ensure_coverr_asset

                if coverr_configured():
                    try:
                        asset = ensure_coverr_asset(
                            query,
                            cache_dir=cache_dir,
                            client=client,
                            context=context,
                            search_query=entry_search_query,
                            veto_session=veto_session,
                            veto_sink=veto_sink,
                            concept=entry_concept,
                            avoid=entry_avoid,
                            search_queries=entry_search_queries,
                            media=entry_media,
                            exclude_ids=tuple(live_exclusions),
                            outcome_sink=outcome,
                        )
                    except Exception as exc:
                        logger.warning(
                            "B-roll Coverr resolve failed for %r: %s",
                            query, type(exc).__name__,
                        )
                        asset = None
                        provider_error = True
                    if asset is not None:
                        provider = "coverr"
            if veto_sink:
                # The veto's evidence rides on the EDL next to the
                # asset provenance: which candidates were scored, which
                # were vetoed, and the winner's score. An entry whose
                # every candidate was vetoed keeps the stamp (and no
                # local_path), so the skipped cue is explainable.
                entry["vision_veto"] = {"checked": True, **veto_sink}
            if asset is None:
                # A search that raised (bad key, rate limit, outage) and
                # returned nothing is a provider error, not an empty library.
                if search_failure_count() > failures_before:
                    provider_error = True
                _stamp_not_inserted(
                    entry, _not_inserted_reason(outcome, provider_error), outcome
                )
                continue
            entry.pop("not_inserted", None)
            entry["local_path"] = asset["local_path"]
            entry["asset_id"] = asset["asset_id"]
            entry["url"] = asset["page_url"]
            entry["provider"] = provider
            if asset.get("asset_id") is not None:
                live_exclusions.add(str(asset["asset_id"]))
            # Rendition provenance for the editor bridge (Slice 6,
            # broll.edl_broll_entry_to_placement): the dimensions,
            # footage length, poster and download URL of the asset that
            # actually rendered, so the review surface can show it and
            # "Next candidate" can replace it without re-searching. A
            # photo win's file is the cue-length Ken Burns clip
            # (1080x1920, cue duration), not the still it was rendered
            # from — the still's dimensions and zero duration would
            # describe the wrong file.
            if asset.get("media") == "photo":
                entry["width"] = KEN_BURNS_WIDTH
                entry["height"] = KEN_BURNS_HEIGHT
                entry["duration"] = entry_duration
            else:
                entry["width"] = int(asset.get("width") or 0)
                entry["height"] = int(asset.get("height") or 0)
                entry["duration"] = float(asset.get("duration") or 0) or entry_duration
            entry["thumbnail"] = str(asset.get("thumbnail_url") or "")
            entry["download_url"] = str(asset.get("download_url") or "")
            # A photo win resolves the entry's media to what actually
            # arrived (a Ken Burns clip); a video win leaves the
            # planner's preference untouched.
            if asset.get("media"):
                entry["media"] = asset["media"]
            stock_library.record_use(
                Path(cache_dir) if cache_dir is not None else broll_cache_dir(),
                provider=provider,
                asset_id=asset["asset_id"],
                clip_id=edl.get("clip_id"),
                task_id=edl.get("task_id"),
                query=query,
            )
            resolved += 1
            logger.info(
                "B-roll: %s asset %s (%s) for query %r -> %s",
                provider, asset["asset_id"], asset["page_url"], query, asset["local_path"],
            )
    finally:
        if own_client:
            client.close()
        if veto_session is not None:
            veto_session.close()
    return resolved


class BrollUsageLedger:
    """Task-level memory of stock assets already committed to a task.

    Named by Luffy's complaint (2026-11-15): sibling clips of one task
    carried the SAME B-roll, because each clip's EDL resolved
    independently and the disk cache made the same top hit the instant
    winner every time. One ledger per task: :meth:`resolve` runs the
    EDL resolution with every previously committed asset id excluded
    (both providers fall to their next candidate), then commits the
    new winners. The lock is held across the resolve on purpose —
    clips of a task render concurrently, and a snapshot/commit race
    would hand two clips the same asset; serialising this planning
    step keeps the guarantee absolute while the renders themselves
    stay parallel. When the candidate pool is exhausted the cue goes
    unfilled and is stamped ``duplicate_in_task`` by the resolver —
    fewer inserts, never a silent repeat.
    """

    def __init__(self) -> None:
        self._used_ids: set[str] = set()
        self._lock = threading.Lock()

    @property
    def used_ids(self) -> frozenset:
        with self._lock:
            return frozenset(self._used_ids)

    def resolve(
        self,
        edl: dict[str, Any],
        *,
        cache_dir: Optional[Path] = None,
        client: Optional[httpx.Client] = None,
    ) -> int:
        """Resolve ``edl``'s entries against the ledger; commit winners."""
        with self._lock:
            resolved = resolve_edl_broll(
                edl,
                cache_dir=cache_dir,
                client=client,
                exclude_ids=tuple(self._used_ids),
            )
            for entry in edl.get("broll") or []:
                if isinstance(entry, dict) and entry.get("local_path"):
                    asset_id = entry.get("asset_id")
                    if asset_id is not None:
                        self._used_ids.add(str(asset_id))
            return resolved
