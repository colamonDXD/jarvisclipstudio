"""Pixabay stock B-roll for auto-director EDL renders.

The EDL builder (``auto_director/edl.py``) plans timed B-roll slots —
a search query plus a start/end window on the clip timeline — but never
touches the network. Resolution happens here, lazily, at render time:
each planned slot is searched on Pixabay, the best rendition is
downloaded once into a disk cache, and the EDL entry is stamped with
``local_path`` / ``asset_id`` / ``url`` so the renderer can composite
the cutaway and the provenance of every stock asset stays on the EDL.

Everything is best-effort by design: without ``PIXABAY_API_KEY`` (or on
any Pixabay/network failure) resolution is a no-op and the EDL renders
exactly as it did before this module existed. Pixabay is the only stock
source wired up — Pexels paused new API keys, so the profiles' legacy
``source: "pexels"`` value is treated as "stock B-roll wanted" and the
actual provider is recorded per entry as ``provider: "pixabay"``.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Optional

import httpx

from ..config import get_config

logger = logging.getLogger(__name__)

PIXABAY_VIDEOS_URL = "https://pixabay.com/api/videos/"
HTTP_TIMEOUT = httpx.Timeout(20.0, connect=10.0)
MAX_DOWNLOAD_BYTES = 150 * 1024 * 1024
SEARCH_RESULTS_PER_QUERY = 5
# Output clips are 1080x1920 portrait. Prefer a portrait rendition at
# least this wide (it is cropped to fill the frame); fall back to the
# largest rendition available when no portrait cut qualifies.
PREFERRED_MIN_WIDTH = 720

# In-process search cache: one task renders several clips whose slots
# often repeat the same query, and Pixabay answers are stable enough
# for the life of a worker process.
_search_cache: dict[str, list[dict[str, Any]]] = {}


def _api_key() -> str:
    try:
        return (get_config().pixabay_api_key or "").strip()
    except Exception:
        return ""


def pixabay_configured() -> bool:
    """True when a Pixabay API key is available (env PIXABAY_API_KEY)."""
    return bool(_api_key())


def broll_cache_dir() -> Path:
    return Path(get_config().temp_dir) / "pixabay_broll_cache"


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
        results.append({
            "asset_id": hit["id"],
            "page_url": str(hit.get("pageURL") or ""),
            "download_url": str(rendition["url"]),
            "width": int(rendition.get("width") or 0),
            "height": int(rendition.get("height") or 0),
            "duration": float(hit.get("duration") or 0),
        })
    _search_cache[cache_key] = results
    return results


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


def ensure_pixabay_asset(
    query: str,
    *,
    cache_dir: Optional[Path] = None,
    client: Optional[httpx.Client] = None,
) -> Optional[dict[str, Any]]:
    """Resolve ``query`` to a downloaded stock file, or None.

    Returns {asset_id, page_url, local_path}. A file already in the disk
    cache is reused without any network call — the same Pixabay asset is
    never downloaded twice.
    """
    hits = search_pixabay_videos(query, client=client)
    if not hits:
        return None
    hit = hits[0]

    cache = Path(cache_dir) if cache_dir is not None else broll_cache_dir()
    target = cache / f"pixabay_{hit['asset_id']}.mp4"
    if target.is_file() and target.stat().st_size > 0:
        return {**hit, "local_path": str(target)}

    own_client = client is None
    client = client or _new_client()
    try:
        if not _download(client, hit["download_url"], target):
            return None
    finally:
        if own_client:
            client.close()
    return {**hit, "local_path": str(target)}


def resolve_edl_broll(
    edl: dict[str, Any],
    *,
    cache_dir: Optional[Path] = None,
    client: Optional[httpx.Client] = None,
) -> int:
    """Stamp resolvable B-roll entries on ``edl`` with their stock file.

    Entries that already carry a ``local_path`` (user assets, a previous
    resolve) are left alone. Returns how many entries were resolved.
    Without a Pixabay API key this is a silent no-op.
    """
    if not edl or not pixabay_configured():
        return 0
    entries = edl.get("broll") or []
    if not entries:
        return 0

    own_client = client is None
    client = client or _new_client()
    resolved = 0
    try:
        for entry in entries:
            if not isinstance(entry, dict) or entry.get("local_path"):
                continue
            query = str(entry.get("query") or "").strip()
            if not query:
                continue
            try:
                asset = ensure_pixabay_asset(query, cache_dir=cache_dir, client=client)
            except Exception as exc:
                logger.warning("B-roll resolve failed for %r: %s", query, type(exc).__name__)
                continue
            if asset is None:
                continue
            entry["local_path"] = asset["local_path"]
            entry["asset_id"] = asset["asset_id"]
            entry["url"] = asset["page_url"]
            entry["provider"] = "pixabay"
            resolved += 1
            logger.info(
                "B-roll: Pixabay asset %s (%s) for query %r -> %s",
                asset["asset_id"], asset["page_url"], query, asset["local_path"],
            )
    finally:
        if own_client:
            client.close()
    return resolved
