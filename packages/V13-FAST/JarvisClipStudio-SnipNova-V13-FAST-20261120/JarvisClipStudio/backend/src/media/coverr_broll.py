"""Coverr stock B-roll: the second source behind the EDL resolver.

Coverr (coverr.co) is wired as the fallback behind Pixabay in
``media/pixabay_broll.py``'s ``resolve_edl_broll``: an entry is asked
of Pixabay first (when its key is set) and of Coverr only when Pixabay
is unconfigured, errors, returns nothing, or none of its hits pass the
relevance gate. Either provider failing never blocks the other, and
with neither key set resolution is a no-op — the EDL renders exactly
as it did before any stock source existed.

Why Coverr (verified 2026-11-12 against Coverr's own pages): a real
public API (``api.coverr.co``) with an instantly issued free Demo key
from coverr.co/developers — no card — limited to 50 requests/hour,
which the in-process search cache and the on-disk library keep a
clipping workload far below. The licence (coverr.co/license) allows
the videos in anything you make and sell, editing and remix included,
and licenses every API tier for commercial use; the files may not be
resold standalone, used to train AI, or passed off as a competing
stock library, sponsored partner content is not covered by the
licence, and Demo-tier API apps must credit Coverr and the creator —
the stock library's manifest records the source and asset id of every
download for exactly that provenance. Coverr's catalogue is mostly
16:9 landscape (portrait is rare), which the resolver's candidates
carry as width/height exactly like Pixabay's renditions.

Downloads land in the shared stock library (``media/stock_library``)
as ``coverr_<asset_id>.mp4`` and are never downloaded twice; a fresh
download also pings Coverr's download-stats endpoint, which their
docs recommend (not require) so they can count real downloads.
"""

from __future__ import annotations

import logging
from pathlib import Path
from time import perf_counter
from typing import Any, Optional

import httpx

from ..config import get_config
from . import stock_library
from .pixabay_broll import _download, _hit_mismatch, _new_client

logger = logging.getLogger(__name__)

COVERR_VIDEOS_URL = "https://api.coverr.co/videos"
SEARCH_RESULTS_PER_QUERY = 5

_search_cache: dict[str, list[dict[str, Any]]] = {}


def _api_key() -> str:
    try:
        return (get_config().coverr_api_key or "").strip()
    except Exception:
        return ""


def coverr_configured() -> bool:
    """True when a Coverr API key is available (env COVERR_API_KEY)."""
    return bool(_api_key())


def _auth_headers() -> dict[str, str]:
    return {"Authorization": f"Bearer {_api_key()}"}


def search_coverr_videos(
    query: str,
    *,
    per_page: int = SEARCH_RESULTS_PER_QUERY,
    client: Optional[httpx.Client] = None,
) -> list[dict[str, Any]]:
    """Parsed Coverr hits for ``query``; empty list on any failure.

    Each hit is normalised to the shape the resolver consumes from
    Pixabay: {asset_id, title, page_url, download_url, width, height,
    duration, tags (comma-joined), preview_url, thumbnail_url}.
    Results are cached in-process per query.
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
            COVERR_VIDEOS_URL,
            params={
                "query": query,
                "urls": "true",
                "sort": "popular",
                "page_size": per_page,
            },
            headers=_auth_headers(),
        )
        response.raise_for_status()
        payload = response.json()
    except Exception as exc:
        logger.warning("Coverr search failed for %r: %s", query, type(exc).__name__)
        return []
    finally:
        if own_client:
            client.close()

    results: list[dict[str, Any]] = []
    for hit in payload.get("hits") or []:
        if not isinstance(hit, dict) or hit.get("id") is None:
            continue
        urls = hit.get("urls") or {}
        download_url = str(urls.get("mp4_download") or urls.get("mp4") or "")
        if not download_url:
            continue
        tags = hit.get("tags") or []
        results.append({
            "asset_id": str(hit["id"]),
            "title": str(hit.get("title") or ""),
            # Coverr's API returns no canonical page URL for a hit; the
            # manifest's provider + asset id is the provenance record.
            "page_url": "",
            "download_url": download_url,
            "width": int(hit.get("max_width") or 0),
            "height": int(hit.get("max_height") or 0),
            "duration": float(hit.get("duration") or 0),
            "tags": ", ".join(str(tag) for tag in tags),
            "preview_url": str(urls.get("mp4_preview") or ""),
            "thumbnail_url": str(hit.get("poster") or ""),
            "is_ai_generated": bool(hit.get("is_ai_generated")),
        })
    _search_cache[cache_key] = results
    return results


def _ping_download(client: httpx.Client, asset_id: Any) -> None:
    """Tell Coverr a download happened (recommended by their docs).

    Best-effort: any failure is ignored, and the ping is only sent for
    a genuinely fresh download, never for a library cache hit.
    """
    try:
        client.patch(
            f"{COVERR_VIDEOS_URL}/{asset_id}/stats/downloads",
            headers=_auth_headers(),
        )
    except Exception as exc:
        logger.debug("Coverr download ping failed for %s: %s", asset_id, type(exc).__name__)


def _candidate_frames_for_hit(
    hit: dict[str, Any],
    *,
    client: httpx.Client,
    cache_dir: Path,
) -> list:
    """Representative frames of one hit for the vision veto.

    Mirrors the Pixabay helper (poster thumbnail first, else frames
    from the smallest preview rendition, cached in a ``vision_preview``
    scratch area); any failure returns no frames, which the veto reads
    as "unjudged" — never as a veto.
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
                "Vision veto: thumbnail fetch failed for Coverr hit %s: %s",
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
                / f"coverr_{hit['asset_id']}_preview.mp4"
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
    """(vetoed, concept_score, avoid_score) for one tag-accepted hit — the same fail-open
    semantics as the Pixabay check (unjudged never vetoes), with
    Coverr candidate frames. Scores are cached in-process per
    (asset, subject) in the shared veto cache. Smart B-Roll v2, Slice 4:
    ``subject`` is the concept sentence for concept cues; when
    ``avoid_subject`` names the wrong sense, the same frames are
    scored against it too and the veto is the contrastive rule."""
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


def ensure_coverr_asset(
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
    # Smart B-Roll v2, Slice 6 (editor "Next candidate"): asset ids to
    # skip without judging them — the footage already shown for this
    # cue. Empty by default: every existing caller resolves exactly as
    # before.
    exclude_ids: Any = (),
    # Retention lane: the caller's outcome tally (same contract as
    # ensure_pixabay_asset's) — counts only, never changes behaviour.
    outcome_sink: Optional[dict[str, Any]] = None,
) -> Optional[dict[str, Any]]:
    """Resolve ``query`` to a downloaded Coverr file, or None.

    Same contract as ``ensure_pixabay_asset``: returns the hit dict
    plus ``local_path`` and ``provider``. The relevance gate (shared
    with Pixabay) judges hits in Coverr's order; the vision veto, when
    a session is given, applies identically. The file lands in the
    stock library and an asset already in the library is reused with
    no network call beyond the search itself.

    Smart B-Roll v2, Slice 3: a concept cue's phrases are tried in
    order at 8 results each (``concept`` / ``search_queries`` given),
    and the gate judges on the concept sentence + avoid list, exactly
    as on Pixabay. Coverr is video-only, so a ``media: photo`` concept
    never reaches it (None, no request). Without a concept the call
    is exactly today's: one search, 5 results, gate on the noun.
    """
    media_pref = (media or "either").strip().lower()
    if media_pref == "photo":
        # Coverr has no photos endpoint; the Pixabay photos pass (or
        # the skip-the-cue rule) owns this cue.
        return None
    concept_mode = bool(concept or search_queries)
    queries = [
        str(text).strip()
        for text in (search_queries or [])
        if str(text).strip()
    ]
    if not queries:
        queries = [search_query or query]
    per_page = 8 if concept_mode else SEARCH_RESULTS_PER_QUERY

    cache = Path(cache_dir) if cache_dir is not None else stock_library.library_dir()
    own_client = client is None
    client = client or _new_client()
    veto_deadline: Optional[float] = None
    if veto_session is not None:
        from .broll_vision import vision_veto_budget_seconds

        veto_deadline = perf_counter() + vision_veto_budget_seconds()
    # Smart B-Roll v2, Slice 4: a concept cue is judged on the
    # planner's concept sentence; without one, the sense-resolved
    # search string (else the planned query) as before.
    veto_subject = (concept or search_query or query).strip()
    excluded = {str(item) for item in (exclude_ids or ())}

    def _tally(key: str) -> None:
        if outcome_sink is not None:
            outcome_sink[key] = int(outcome_sink.get(key, 0)) + 1

    try:
        for search_text in queries:
            hits = search_coverr_videos(
                search_text, per_page=per_page, client=client
            )
            for hit in hits:
                _tally("hits")
                if str(hit.get("asset_id")) in excluded:
                    _tally("excluded")
                    continue
                reason = _hit_mismatch(
                    hit, query, context, concept=concept, avoid=list(avoid or ())
                )
                if reason:
                    _tally("gate_rejected")
                    logger.info(
                        "Skipping Coverr hit %s for %r: %s",
                        hit.get("asset_id"), query, reason,
                    )
                    continue
                if veto_session is not None:
                    avoid_subject = ", ".join(
                        str(item).strip()
                        for item in (avoid or ())
                        if str(item).strip()
                    )
                    vetoed, score, avoid_score = _vision_check(
                        veto_session,
                        hit,
                        veto_subject,
                        client=client,
                        cache_dir=cache,
                        deadline=veto_deadline,
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
                        _tally("vetoed")
                        if veto_sink is not None:
                            veto_sink.setdefault("vetoed", []).append(hit["asset_id"])
                        logger.info(
                            "Vision veto: Coverr hit %s for %r scored %.4f"
                            + (" (avoid %.4f)" if avoid_score is not None else "")
                            + "; falling through to the next hit",
                            hit.get("asset_id"), query, score,
                            *([avoid_score] if avoid_score is not None else []),
                        )
                        continue
                    if score is not None and veto_sink is not None:
                        veto_sink["winner_score"] = round(score, 4)
                target = stock_library.asset_path("coverr", hit["asset_id"], root=cache)
                if target.is_file() and target.stat().st_size > 0:
                    stock_library.record_asset(
                        cache, provider="coverr", candidate=hit, query=query,
                        search_query=search_text, local_path=str(target),
                        downloaded=False,
                    )
                    return {**hit, "local_path": str(target), "provider": "coverr"}
                if _download(client, hit["download_url"], target):
                    stock_library.record_asset(
                        cache, provider="coverr", candidate=hit, query=query,
                        search_query=search_text, local_path=str(target),
                        downloaded=True,
                    )
                    _ping_download(client, hit["asset_id"])
                    return {**hit, "local_path": str(target), "provider": "coverr"}
                _tally("download_failed")
    finally:
        if own_client:
            client.close()
    logger.warning(
        "No Coverr hit for %r passed the relevance gate; skipping the B-roll cue",
        query,
    )
    return None


def ensure_coverr_hit(
    hit: dict[str, Any],
    *,
    cache_dir: Optional[Path] = None,
    client: Optional[httpx.Client] = None,
    query: str = "",
) -> Optional[dict[str, Any]]:
    """Download one already-searched Coverr hit into the stock library.

    The editor-search counterpart of ``ensure_pixabay_hit``: no search
    and no relevance gate (the user picked the hit), the same library
    file name and manifest record the resolver writes, and the same
    download ping ``ensure_coverr_asset`` sends. Returns the hit plus
    ``local_path`` and ``provider``, or None when the download fails.
    """
    asset_id = hit.get("asset_id")
    download_url = str(hit.get("download_url") or "")
    if asset_id is None or not download_url:
        return None
    cache = Path(cache_dir) if cache_dir is not None else stock_library.library_dir()
    own_client = client is None
    client = client or _new_client()
    try:
        target = stock_library.asset_path("coverr", asset_id, root=cache)
        if target.is_file() and target.stat().st_size > 0:
            stock_library.record_asset(
                cache, provider="coverr", candidate=hit, query=query,
                local_path=str(target), downloaded=False,
            )
            return {**hit, "local_path": str(target), "provider": "coverr"}
        if _download(client, download_url, target):
            stock_library.record_asset(
                cache, provider="coverr", candidate=hit, query=query,
                local_path=str(target), downloaded=True,
            )
            _ping_download(client, asset_id)
            return {**hit, "local_path": str(target), "provider": "coverr"}
        return None
    finally:
        if own_client:
            client.close()
