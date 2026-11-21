"""The SFX library: bundled effects plus user uploads.

Two roots feed every consumer (the library API in ``api.routes.sfx``,
the clip editor's SFX panel state, and the EDL renderer that mixes
``sfx_events`` into exports):

- **bundled** — ``assets/sfx``, the repo-root asset library. Read-only
  in Docker (``./assets:/assets:ro``), so uploads can never land here.
- **user** — ``user_sfx`` under the app's shared temp tree (the Docker
  ``uploads`` volume, mounted at the same path in backend, worker and
  ai-worker), exactly parallel to user music. ``USER_SFX_DIR``
  overrides the location.

The library is folder-driven: any audio file dropped into either root
is listed, previewable and placeable with no registration step. When
both roots hold the same filename, the bundled copy wins — it is the
curated library, and uploads get a numeric suffix instead of shadowing
it. Resolution mirrors the renderer exactly (``_resolve_asset_file``
over the winning root), so anything the panel offers is guaranteed
placeable — there is no second library that could drift from what the
export can actually use.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Any, Optional

from ..config import get_config
from .edl_render import ASSETS_DIR, AUDIO_FILE_EXTENSIONS, _resolve_asset_file
from .ffmpeg import ffprobe_duration

logger = logging.getLogger(__name__)

# Extensions the upload endpoint accepts. Kept in sync with the
# placement validator (auto_director.sfx.SFX_EXTENSIONS) and the
# renderer's AUDIO_FILE_EXTENSIONS so anything uploadable is also
# listed, placeable and renderable.
UPLOAD_EXTENSIONS = (
    ".mp3", ".wav", ".m4a", ".aac", ".ogg", ".flac", ".opus", ".webm",
)
MAX_UPLOAD_BYTES = 10 * 1024 * 1024


def sfx_dir() -> Path:
    return ASSETS_DIR / "sfx"


def user_sfx_dir() -> Path:
    """The user-upload SFX root (created on demand by uploads)."""
    return Path(get_config().user_sfx_dir)


def ensure_user_sfx_dir() -> Path:
    folder = user_sfx_dir()
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def _roots() -> list[tuple[Path, str]]:
    return [(sfx_dir(), "bundled"), (user_sfx_dir(), "user")]


def find_sfx_file(name: str) -> Optional[Path]:
    """Resolve a placement name exactly as the renderer would.

    The bundled root wins a filename clash, matching the listing's
    dedupe rule; a name found in neither root does not exist here.
    """
    for folder, _source in _roots():
        resolved = _resolve_asset_file(folder, name)
        if resolved is not None:
            return resolved
    return None


def list_sfx_tracks(probe_durations: bool = True) -> list[dict[str, Any]]:
    """Every placeable SFX file: ``{filename, display_name, duration, source}``.

    Bundled files list first, then user uploads; filenames are
    deduplicated with the bundled copy winning. A missing folder is an
    empty contribution, never an error.
    """
    tracks: list[dict[str, Any]] = []
    seen: set[str] = set()
    for folder, source in _roots():
        if not folder.is_dir():
            continue
        for entry in sorted(folder.iterdir(), key=lambda p: p.name.lower()):
            if not entry.is_file() or entry.name.startswith("."):
                continue
            if entry.suffix.lower() not in AUDIO_FILE_EXTENSIONS:
                continue
            if entry.name in seen:
                continue
            seen.add(entry.name)
            duration: Optional[float] = None
            if probe_durations:
                try:
                    duration = ffprobe_duration(entry)
                except Exception:
                    logger.warning("Could not probe SFX duration: %s", entry.name)
                    duration = None
            tracks.append(
                {
                    "filename": entry.name,
                    "display_name": entry.stem.replace("_", " ").replace("-", " "),
                    "duration": duration,
                    "source": source,
                }
            )
    return tracks


_UNSAFE_FILENAME_CHARS = re.compile(r"[^A-Za-z0-9 ._-]+")


def sanitize_sfx_filename(raw_name: str) -> str:
    """Reduce an uploaded name to a recognizable, safe basename.

    Path segments are dropped, characters outside a conservative set
    become underscores, and leading dots (hidden files) are stripped —
    the same treatment music uploads get.
    """
    name = Path(str(raw_name or "")).name.strip()
    name = _UNSAFE_FILENAME_CHARS.sub("_", name)
    name = name.lstrip(".").strip()
    stem = Path(name).stem.strip() or "effect"
    suffix = Path(name).suffix.lower()
    return f"{stem}{suffix}"


def unique_sfx_filename(filename: str) -> str:
    """Return ``filename`` or a ``_2``/``_3`` variant unused in both roots."""
    stem, suffix = Path(filename).stem, Path(filename).suffix
    candidate = filename
    counter = 2
    while find_sfx_file(candidate) is not None:
        candidate = f"{stem}_{counter}{suffix}"
        counter += 1
    return candidate
