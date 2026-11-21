"""User-imported ``.cube`` LUT storage.

LUTs live in one directory (``config.user_luts_dir``) shared by the
backend, worker and ai-worker containers, mirroring how user music is
stored (see :mod:`src.media.music_library`).  Every file in the
directory was validated by :func:`src.auto_director.lut.parse_cube_lut`
at upload time; listings re-read just the header so a library of large
cubes stays cheap to display.

The stored *filename* is the identity used everywhere else: edit plans
store it in the grade's ``lut`` field, and the re-render seam resolves
it back to an absolute path here before stamping the EDL.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Optional

from ..auto_director.lut import MAX_LUT_BYTES, parse_cube_header, parse_cube_lut
from ..config import get_config

logger = logging.getLogger(__name__)

_UNSAFE_FILENAME_CHARS = re.compile(r"[^A-Za-z0-9 ._-]+")


def user_luts_dir() -> Path:
    return Path(get_config().user_luts_dir)


def ensure_user_luts_dir() -> Path:
    directory = user_luts_dir()
    directory.mkdir(parents=True, exist_ok=True)
    return directory


def sanitize_lut_filename(raw_name: str) -> str:
    """Reduce an uploaded name to a safe ``.cube`` basename."""
    name = Path(str(raw_name or "")).name.strip()
    name = _UNSAFE_FILENAME_CHARS.sub("_", name)
    name = name.lstrip(".").strip()
    stem = Path(name).stem.strip() or "lut"
    return f"{stem}.cube"


def find_lut_file(filename: str) -> Optional[Path]:
    """Resolve a stored LUT filename to its path, or None.

    Only exact basenames inside the user LUT directory resolve — this
    is the path the renderer ultimately reads, so anything else (path
    segments, other extensions) is treated as missing.
    """
    if not filename:
        return None
    name = Path(str(filename)).name
    if name != str(filename) or not name.lower().endswith(".cube"):
        return None
    candidate = user_luts_dir() / name
    return candidate if candidate.is_file() else None


def unique_lut_filename(filename: str) -> str:
    """Return ``filename`` or a ``_2``/``_3`` variant not yet stored."""
    stem = Path(filename).stem
    candidate = filename
    counter = 2
    while find_lut_file(candidate) is not None:
        candidate = f"{stem}_{counter}.cube"
        counter += 1
    return candidate


def _entry_for(path: Path) -> Optional[dict]:
    try:
        head = path.read_bytes()[:8192].decode("utf-8", errors="replace")
        info = parse_cube_header(head)
    except (OSError, ValueError) as exc:
        logger.warning("Skipping unreadable LUT %s: %s", path.name, exc)
        return None
    return {
        "filename": path.name,
        "display_name": info.title or Path(path.name).stem,
        "size": info.size,
        "title": info.title,
        "bytes": path.stat().st_size,
    }


def list_luts() -> list[dict]:
    """List the imported LUTs (header facts only, sorted by name)."""
    directory = user_luts_dir()
    if not directory.is_dir():
        return []
    entries = []
    for path in sorted(directory.glob("*.cube")):
        entry = _entry_for(path)
        if entry is not None:
            entries.append(entry)
    return entries


def save_lut(raw_name: str, data: bytes) -> dict:
    """Validate and store one uploaded ``.cube`` file.

    Raises ``ValueError`` with a user-surfaceable message for anything
    malformed — the route turns that into a 400 and nothing is stored.
    """
    if not data:
        raise ValueError("the uploaded file is empty")
    if len(data) > MAX_LUT_BYTES:
        raise ValueError(
            f"the uploaded file is too large (limit {MAX_LUT_BYTES // (1024 * 1024)} MB)"
        )
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        raise ValueError("the uploaded file is not valid UTF-8 text") from None
    info = parse_cube_lut(text)  # raises ValueError on anything malformed
    directory = ensure_user_luts_dir()
    filename = unique_lut_filename(sanitize_lut_filename(raw_name))
    (directory / filename).write_bytes(data)
    return {
        "filename": filename,
        "display_name": info.title or Path(filename).stem,
        "size": info.size,
        "title": info.title,
        "bytes": len(data),
    }


def delete_lut(filename: str) -> bool:
    path = find_lut_file(filename)
    if path is None:
        return False
    path.unlink()
    return True
