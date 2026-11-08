"""The background-music library: bundled tracks plus user uploads.

Two roots feed every consumer (the library API in ``api.routes.music``,
the EDL renderer's music resolution and the deliverable bundle's credit
lines):

- **bundled** — ``<assets>/music``, the repo-root asset library the EDL
  renderer already resolves mood/track names against. Read-only in
  Docker (``./assets:/assets:ro``), so uploads can never land here.
- **user** — ``user_music`` under the app's shared temp tree. In Docker
  ``TEMP_DIR=/app/uploads`` is the ``uploads`` named volume, mounted at
  the same path in backend, worker and ai-worker, so a track uploaded
  through the API is visible to the render workers with no compose
  change. ``USER_MUSIC_DIR`` overrides the location.

The library is folder-driven: any audio file dropped into either root is
listed, previewable and selectable with no registration step. When both
roots hold the same filename, the bundled copy wins — it is the curated
library, and uploads get a numeric suffix instead of shadowing it.
"""

from __future__ import annotations

import json
import logging
import re
import subprocess
from pathlib import Path
from typing import Any, Optional

from ..config import get_config

logger = logging.getLogger(__name__)

CREDITS_FILENAME = "CREDITS.json"
# Extensions the upload endpoint accepts (the renderer resolves a wider
# set; uploads stick to the formats browsers record and export).
UPLOAD_EXTENSIONS = (".mp3", ".wav", ".ogg", ".m4a", ".flac")
MAX_UPLOAD_BYTES = 30 * 1024 * 1024
# Defaults applied when a user picks a track for a profile that carries
# no music of its own (e.g. podcast_news): audible but under the voice,
# with standard ducking, so the choice actually plays.
OVERRIDE_DEFAULT_VOLUME = 0.25
OVERRIDE_DEFAULT_DUCKING_DB = -12.0


def bundled_music_dir() -> Path:
    """The bundled library root (the EDL renderer's ``ASSETS_DIR``/music)."""
    from . import edl_render

    return edl_render.ASSETS_DIR / "music"


def user_music_dir() -> Path:
    """The user-upload library root (created on demand by uploads)."""
    return Path(get_config().user_music_dir)


def ensure_user_music_dir() -> Path:
    folder = user_music_dir()
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def _audio_extensions() -> tuple[str, ...]:
    from . import edl_render

    return edl_render.AUDIO_FILE_EXTENSIONS


def _probe_duration_sec(path: Path) -> Optional[float]:
    """Best-effort duration via ffprobe; None when probing is not cheap."""
    try:
        result = subprocess.run(
            [
                "ffprobe", "-v", "error",
                "-show_entries", "format=duration",
                "-of", "default=noprint_wrappers=1:nokey=1",
                str(path),
            ],
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0:
        return None
    try:
        duration = float((result.stdout or "").strip())
    except ValueError:
        return None
    return duration if duration > 0 else None


def _track_entry(path: Path, source: str, probe_duration: bool) -> dict[str, Any]:
    return {
        "filename": path.name,
        "display_name": path.stem,
        "source": source,
        "duration_sec": _probe_duration_sec(path) if probe_duration else None,
    }


def list_music_tracks(probe_durations: bool = True) -> list[dict[str, Any]]:
    """Every library track: bundled first, then user uploads.

    Missing roots are an empty library, never an error. Filenames are
    deduplicated with the bundled copy winning.
    """
    tracks: list[dict[str, Any]] = []
    seen: set[str] = set()
    extensions = _audio_extensions()
    for folder, source in (
        (bundled_music_dir(), "bundled"),
        (user_music_dir(), "user"),
    ):
        if not folder.is_dir():
            continue
        for entry in sorted(folder.iterdir(), key=lambda p: p.name.lower()):
            if not entry.is_file() or entry.name.startswith("."):
                continue
            if entry.suffix.lower() not in extensions:
                continue
            if entry.name in seen:
                continue
            seen.add(entry.name)
            tracks.append(_track_entry(entry, source, probe_durations))
    return tracks


def find_music_file(filename: str) -> Optional[tuple[Path, str]]:
    """Locate one library file by exact filename (bundled wins).

    Only plain basenames are considered — anything carrying a path
    separator or traversal segment simply does not exist here.
    """
    if not filename or filename != Path(filename).name or filename in {".", ".."}:
        return None
    for folder, source in (
        (bundled_music_dir(), "bundled"),
        (user_music_dir(), "user"),
    ):
        candidate = folder / filename
        if candidate.is_file():
            return candidate, source
    return None


def validate_music_override(value: object) -> Optional[str]:
    """Return a normalized music override or raise ``ValueError``.

    Mirrors ``validate_niche_override``: ``None`` means "let the profile
    decide"; anything else must name a track in the live library. The API
    layer turns the raised error into the standard 422 response.
    """
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError(
            "Invalid music_override. Valid values: a filename from the "
            "music library or null."
        )
    normalized = value.strip()
    if not normalized or normalized != Path(normalized).name:
        raise ValueError(
            f"Invalid music_override '{value}'. Valid values: a filename "
            "from the music library or null."
        )
    known = {
        track["filename"] for track in list_music_tracks(probe_durations=False)
    }
    if normalized not in known:
        raise ValueError(
            f"Invalid music_override '{value}'. It does not match any "
            "track in the music library."
        )
    return normalized


def apply_music_override(edl: dict[str, Any], filename: str) -> None:
    """Point an EDL's audio block at a user-chosen library track.

    The track always wins over the profile's mood. Volume and ducking are
    the profile's own when it carries music (volume > 0); a silent
    profile (music_volume 0/missing, e.g. podcast_news) gets audible
    defaults so the user's choice actually plays.
    """
    audio = edl.setdefault("audio", {})
    audio["music_track"] = filename
    try:
        volume = float(
            audio.get("music_volume_0_to_1", audio.get("music_volume", 0.0))
            or 0.0
        )
    except (TypeError, ValueError):
        volume = 0.0
    if volume <= 0:
        audio["music_volume"] = OVERRIDE_DEFAULT_VOLUME
        if "music_volume_0_to_1" in audio:
            audio["music_volume_0_to_1"] = OVERRIDE_DEFAULT_VOLUME
        audio["ducking_db"] = OVERRIDE_DEFAULT_DUCKING_DB


_UNSAFE_FILENAME_CHARS = re.compile(r"[^A-Za-z0-9 ._-]+")


def sanitize_music_filename(raw_name: str) -> str:
    """Reduce an uploaded name to a recognizable, safe basename.

    Path segments are dropped, characters outside a conservative set
    become underscores, and leading dots (hidden files) are stripped.
    """
    name = Path(str(raw_name or "")).name.strip()
    name = _UNSAFE_FILENAME_CHARS.sub("_", name)
    name = name.lstrip(".").strip()
    stem = Path(name).stem.strip() or "track"
    suffix = Path(name).suffix.lower()
    return f"{stem}{suffix}"


def unique_music_filename(filename: str) -> str:
    """Return ``filename`` or a ``_2``/``_3`` variant unused in both roots."""
    stem, suffix = Path(filename).stem, Path(filename).suffix
    candidate = filename
    counter = 2
    while find_music_file(candidate) is not None:
        candidate = f"{stem}_{counter}{suffix}"
        counter += 1
    return candidate


def load_music_credits() -> dict[str, str]:
    """Merge the CREDITS.json manifests from both music roots.

    Format: a JSON object mapping filename -> credit line. Bundled wins
    on conflicts, matching the library's dedupe rule. A missing or
    malformed manifest simply contributes nothing.
    """
    credits: dict[str, str] = {}
    for folder in (bundled_music_dir(), user_music_dir()):
        manifest = folder / CREDITS_FILENAME
        try:
            data = json.loads(manifest.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if not isinstance(data, dict):
            continue
        for filename, line in data.items():
            if isinstance(line, str) and line.strip():
                credits.setdefault(str(filename), line.strip())
    return credits


def music_credit_line(edl: Optional[dict[str, Any]]) -> Optional[str]:
    """The credit line for the exact track an EDL used, if one is recorded.

    Only an explicit ``music_track`` counts — a mood-resolved file is not
    recorded in the EDL, so no credit is claimed for it.
    """
    if not edl:
        return None
    audio = edl.get("audio") or {}
    track = str(audio.get("music_track") or "").strip()
    if not track:
        return None
    return load_music_credits().get(track)
