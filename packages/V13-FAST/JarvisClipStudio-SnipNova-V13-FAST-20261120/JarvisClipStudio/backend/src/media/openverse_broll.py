"""Keyless B-roll: CC0 / public-domain photos from Openverse as Ken Burns clips.

Pixabay and Coverr need API keys; a fresh install without them used to
stamp every Auto B-roll cue ``no_provider_configured`` and insert nothing.
Openverse (api.openverse.org, the WordPress Foundation's open-media index:
Flickr, Wikimedia Commons, museums, ...) answers anonymously, so this is the
third source in ``pixabay_broll.resolve_edl_broll``: asked only when neither
keyed provider produced a hit.

- Only ``license=cc0,pdm`` results are requested: no attribution owed, no
  share-alike, safe for commercial reuse. ``mature=false`` is sent.
- Each hit goes through the same relevance gate and vision veto as a
  Pixabay hit (``pixabay_broll._accept_hit``), then is rendered into a
  cue-length slow-zoom clip with ``pixabay_broll.still_to_clip``.
- Anonymous limits are 20 requests/minute and 200/day per IP, so a clip
  spends at most one search per cue phrase; a 429 or any error just skips
  the cue.

Switch: ``BROLL_OPENVERSE`` (default on; ``off`` restores the old
no-provider behaviour).
"""
from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any, Optional

import httpx

logger = logging.getLogger(__name__)

OPENVERSE_IMAGES_URL = "https://api.openverse.org/v1/images/"
USER_AGENT = "SnipNova/1.0 (open-source video tool)"
RESULTS_PER_QUERY = 12
MIN_SHORT_SIDE = 720  # stills are cropped to fill 1080x1920; tiny images blur

_search_cache: dict[str, list[dict[str, Any]]] = {}


def openverse_enabled() -> bool:
    return os.getenv("BROLL_OPENVERSE", "on").strip().lower() not in ("off", "0", "false", "no")


def search_openverse_photos(query: str, *, client: httpx.Client) -> list[dict[str, Any]]:
    """CC0/PDM photo hits for ``query`` in the resolver's hit shape; [] on failure."""
    query = " ".join((query or "").split())[:100]
    if not query:
        return []
    key = query.lower()
    if key in _search_cache:
        return _search_cache[key]
    try:
        response = client.get(
            OPENVERSE_IMAGES_URL,
            params={
                "q": query,
                "license": "cc0,pdm",
                "mature": "false",
                "page_size": RESULTS_PER_QUERY,
            },
            headers={"User-Agent": USER_AGENT},
        )
        response.raise_for_status()
        payload = response.json()
    except Exception as exc:
        logger.warning("Openverse search failed for %r: %s", query, type(exc).__name__)
        return []
    hits: list[dict[str, Any]] = []
    for item in payload.get("results") or []:
        if not isinstance(item, dict) or not item.get("id") or not item.get("url"):
            continue
        licence = str(item.get("license") or "").lower()
        if licence not in ("cc0", "pdm"):
            continue
        width, height = int(item.get("width") or 0), int(item.get("height") or 0)
        if width and height and min(width, height) < MIN_SHORT_SIDE:
            continue
        tags = [str(t.get("name") or "") for t in (item.get("tags") or []) if isinstance(t, dict)]
        hits.append({
            "asset_id": f"ov_{item['id']}",
            "page_url": str(item.get("foreign_landing_url") or ""),
            "download_url": str(item["url"]),
            "width": width,
            "height": height,
            "duration": 0.0,
            "tags": ", ".join([str(item.get("title") or "")] + [t for t in tags if t]),
            "thumbnail_url": str(item.get("thumbnail") or ""),
            "preview_url": str(item.get("thumbnail") or ""),
            "media": "photo",
            "license": licence,
            "creator": str(item.get("creator") or ""),
            "source": str(item.get("source") or ""),
        })
    _search_cache[key] = hits
    return hits


def ensure_openverse_asset(
    query: str,
    *,
    cache_dir: Path,
    client: httpx.Client,
    context: str = "",
    search_query: str = "",
    veto_session: Any = None,
    veto_sink: Optional[dict[str, Any]] = None,
    concept: str = "",
    avoid: Any = (),
    search_queries: Any = (),
    media: str = "either",
    cue_duration: float = 3.0,
    exclude_ids: Any = (),
    outcome_sink: Optional[dict[str, Any]] = None,
) -> Optional[dict[str, Any]]:
    """Resolve a cue to a Ken Burns clip of a CC0/PDM photo, or None."""
    from . import pixabay_broll as pb

    if (media or "either").strip().lower() == "video":
        return None
    queries = [str(q).strip() for q in (search_queries or []) if str(q).strip()]
    if not queries:
        queries = [search_query or query]
    excluded = {str(item) for item in (exclude_ids or ())}
    veto_subject = (concept or search_query or query).strip()
    deadline: Optional[float] = None
    if veto_session is not None:
        from time import perf_counter

        from .broll_vision import vision_veto_budget_seconds

        deadline = perf_counter() + vision_veto_budget_seconds()

    def _tally(name: str) -> None:
        if outcome_sink is not None:
            outcome_sink[name] = int(outcome_sink.get(name, 0)) + 1

    for text in queries:
        for hit in search_openverse_photos(text, client=client):
            _tally("hits")
            if hit["asset_id"] in excluded:
                _tally("excluded")
                continue
            if not pb._accept_hit(
                hit, query, context, concept=concept, avoid=avoid,
                veto_session=veto_session, veto_subject=veto_subject,
                client=client, cache_dir=cache_dir, deadline=deadline,
                veto_sink=veto_sink,
            ):
                _tally("gate_rejected")
                continue
            ext = Path(hit["download_url"].split("?")[0]).suffix.lower()
            ext = ext if ext in (".jpg", ".jpeg", ".png", ".webp") else ".jpg"
            still = cache_dir / f"{hit['asset_id']}_still{ext}"
            clip = cache_dir / f"{hit['asset_id']}_still.mp4"
            if not (clip.is_file() and clip.stat().st_size > 0):
                if not (still.is_file() and still.stat().st_size > 0):
                    if not pb._download(client, hit["download_url"], still):
                        _tally("download_failed")
                        continue
                if not pb.still_to_clip(still, cue_duration, clip):
                    _tally("download_failed")
                    continue
            return {**hit, "local_path": str(clip), "media": "photo"}
    return None
