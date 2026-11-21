"""Persistent stock B-roll library shared by the stock providers.

Every stock asset the resolver downloads — Pixabay today, Coverr as the
second source — lands here exactly once and is reused from disk on
every later cue, task and render. The library lives under the app's
``temp_dir`` (``<temp_dir>/stock_library``): that tree is the app's
writable, persistent data area — user music, sound effects, profiles
and autopilot state already live there and survive container restarts —
whereas the ``./assets`` mount is read-only on the target machine and
must never be written to.

A sidecar ``manifest.json`` in the library root records, per asset: the
source provider and asset id, the file name, title/tags/dimensions, the
page and download URLs, the licence the asset was fetched under, when
it was downloaded, which queries/concepts fetched it, and which clips
used it. The manifest is provenance bookkeeping (the Coverr Demo tier
in particular asks API apps to credit Coverr and the creator for the
library content they show), never a render input: every function here
is best-effort and swallows its own failures, so a broken manifest can
never break a render.
"""

from __future__ import annotations

import json
import logging
import re
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from ..config import get_config

logger = logging.getLogger(__name__)

MANIFEST_NAME = "manifest.json"
MANIFEST_VERSION = 1
_MAX_HISTORY = 100

# The licence each provider's assets are recorded under. Kept in words,
# next to the asset, so the provenance survives in the manifest even if
# a provider's terms page moves.
PROVIDER_LICENCES: dict[str, str] = {
    "pixabay": (
        "Pixabay Content License — free for commercial and "
        "non-commercial use, no attribution required"
    ),
    "coverr": (
        "Coverr License — free for commercial and non-commercial use in "
        "anything you make and sell (editing/remix permitted); files "
        "may not be resold standalone or used to train AI; Demo-tier "
        "API apps must credit Coverr and the creator"
    ),
}

_lock = threading.Lock()


def library_dir() -> Path:
    """The library root: ``<temp_dir>/stock_library``."""
    return Path(get_config().temp_dir) / "stock_library"


def _safe_asset_id(asset_id: Any) -> str:
    return re.sub(r"[^A-Za-z0-9_-]+", "_", str(asset_id))


def asset_filename(provider: str, asset_id: Any) -> str:
    return f"{provider}_{_safe_asset_id(asset_id)}.mp4"


def asset_path(provider: str, asset_id: Any, *, root: Optional[Path] = None) -> Path:
    base = Path(root) if root is not None else library_dir()
    return base / asset_filename(provider, asset_id)


def lookup_asset_file(provider: str, asset_id: Any, *, root: Optional[Path] = None) -> Optional[Path]:
    """The downloaded file for one library asset, or None.

    The manifest is the authority (a photo win's Ken Burns clip is
    filed under a ``_still`` name, not the plain video name), with the
    conventional ``<provider>_<id>.mp4`` name as the fallback. Only a
    file inside the library root is ever returned — a manifest entry
    naming anything outside it is ignored, never followed.
    """
    base = Path(root) if root is not None else library_dir()
    try:
        root_resolved = base.resolve()
    except OSError:
        return None
    data = _load_manifest(base)
    record = data.get("assets", {}).get(_key(provider, asset_id)) or {}
    names: list[str] = []
    stored = record.get("file")
    if isinstance(stored, str) and stored:
        names.append(Path(stored).name)
    names.append(asset_filename(provider, asset_id))
    for name in names:
        candidate = root_resolved / name
        try:
            resolved = candidate.resolve()
        except OSError:
            continue
        if not resolved.is_relative_to(root_resolved):
            continue
        if resolved.is_file() and resolved.stat().st_size > 0:
            return resolved
    return None


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _manifest_path(root: Path) -> Path:
    return Path(root) / MANIFEST_NAME


def _load_manifest(root: Path) -> dict[str, Any]:
    try:
        data = json.loads(_manifest_path(root).read_text(encoding="utf-8"))
        if isinstance(data, dict) and isinstance(data.get("assets"), dict):
            return data
    except Exception:
        pass
    return {"version": MANIFEST_VERSION, "assets": {}}


def _save_manifest(root: Path, data: dict[str, Any]) -> None:
    root.mkdir(parents=True, exist_ok=True)
    target = _manifest_path(root)
    part = target.with_suffix(".json.part")
    part.write_text(json.dumps(data, indent=2, sort_keys=True), encoding="utf-8")
    part.replace(target)


def _key(provider: str, asset_id: Any) -> str:
    return f"{provider}:{asset_id}"


def _tags_list(candidate: dict[str, Any]) -> list[str]:
    tags = candidate.get("tags")
    if isinstance(tags, list):
        return [str(tag).strip() for tag in tags if str(tag).strip()]
    return [tag.strip() for tag in str(tags or "").split(",") if tag.strip()]


def record_asset(
    root: Optional[Path],
    *,
    provider: str,
    candidate: dict[str, Any],
    query: str,
    search_query: str = "",
    local_path: str,
    downloaded: bool,
) -> None:
    """Upsert one asset's manifest record after a resolution.

    Called on fresh downloads (``downloaded=True`` stamps the download
    time and file size) and on disk-cache hits (metadata is refreshed
    and the fetching query is appended, but the original download time
    stands — the file was downloaded once, and the manifest says so).
    """
    if root is None:
        root = library_dir()
    root = Path(root)
    asset_id = candidate.get("asset_id")
    if asset_id is None:
        return
    try:
        with _lock:
            data = _load_manifest(root)
            key = _key(provider, asset_id)
            record = data["assets"].get(key) or {}
            record.update({
                "provider": provider,
                "asset_id": asset_id,
                "file": Path(local_path).name,
                "title": str(candidate.get("title") or ""),
                "tags": _tags_list(candidate),
                "page_url": str(candidate.get("page_url") or ""),
                "download_url": str(candidate.get("download_url") or ""),
                "width": int(candidate.get("width") or 0),
                "height": int(candidate.get("height") or 0),
                "duration": float(candidate.get("duration") or 0),
                "licence": PROVIDER_LICENCES.get(provider, ""),
            })
            if downloaded:
                record["downloaded_at"] = _now()
                try:
                    record["bytes"] = Path(local_path).stat().st_size
                except Exception:
                    record["bytes"] = 0
            fetched = record.setdefault("fetched_for", [])
            fetched.append({
                "query": query,
                "search_query": search_query or query,
                "at": _now(),
            })
            record["fetched_for"] = fetched[-_MAX_HISTORY:]
            record.setdefault("uses", [])
            data["assets"][key] = record
            _save_manifest(root, data)
    except Exception as exc:
        logger.warning(
            "Stock library manifest update failed for %s:%s: %s",
            provider, asset_id, type(exc).__name__,
        )


def record_use(
    root: Optional[Path],
    *,
    provider: str,
    asset_id: Any,
    clip_id: Any = None,
    task_id: Any = None,
    query: str = "",
) -> None:
    """Note that one rendered clip used an asset (provenance only)."""
    if root is None:
        root = library_dir()
    root = Path(root)
    if asset_id is None:
        return
    try:
        with _lock:
            data = _load_manifest(root)
            key = _key(provider, asset_id)
            record = data["assets"].get(key)
            if record is None:
                record = {
                    "provider": provider,
                    "asset_id": asset_id,
                    "file": asset_filename(provider, asset_id),
                    "licence": PROVIDER_LICENCES.get(provider, ""),
                    "fetched_for": [],
                    "uses": [],
                }
                data["assets"][key] = record
            uses = record.setdefault("uses", [])
            uses.append({
                "clip_id": clip_id,
                "task_id": task_id,
                "query": query,
                "at": _now(),
            })
            record["uses"] = uses[-_MAX_HISTORY:]
            _save_manifest(root, data)
    except Exception as exc:
        logger.warning(
            "Stock library use note failed for %s:%s: %s",
            provider, asset_id, type(exc).__name__,
        )
