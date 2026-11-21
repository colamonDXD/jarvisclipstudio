"""Variant libraries for music moods and SFX cues, plus the licence manifest.

The EDL names music by mood stem (``comedy``) and SFX by cue (``whoosh``);
the renderer used to resolve each to exactly one file,
``assets/music/comedy.mp3`` / ``assets/sfx/whoosh.wav``, so every clip of
a mood played the same song and every cut the same whoosh.

A variant library is a folder of alternatives next to the stem file::

    assets/music/library/<mood>/*.mp3
    assets/sfx/library/<cue>/*.wav
    assets/sfx/library/<pack>/<cue>/*.wav   (a profile's ``sfx_pack``)

With variants on, the pool is the stem file (when present) plus the folder,
and one member is picked by a stable hash of a seed the renderer builds from
the clip id, so a clip re-renders with the same track and SFX. A mood or cue
that has only a library folder (no stem file) now resolves too. With
``ASSET_VARIANTS=off`` the folders are ignored and resolution is exactly
the stem-file lookup it always was.

Licence manifest (``assets/ASSET_MANIFEST.json``): every audio file under
``assets/music`` and ``assets/sfx`` must have an entry with a matching
sha256 and a licence on the allowlist. ``check_manifest`` reports every
file that does not, so packaging can refuse to ship it
(``tools/assets/check_licences.py``).
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any, Iterable, Optional

LIBRARY_DIRNAME = "library"
MANIFEST_FILENAME = "ASSET_MANIFEST.json"

# Licences a shipped file may carry. "original" = made for this project
# (synthesised here), released with it.
ALLOWED_LICENSES = frozenset({"CC0-1.0", "PDM-1.0", "original"})

AUDIO_EXTENSIONS = (".mp3", ".wav", ".m4a", ".aac", ".ogg", ".flac", ".opus", ".webm")


def variants_enabled() -> bool:
    """``ASSET_VARIANTS`` (default on); ``off``/``0``/``false`` disables."""
    value = os.getenv("ASSET_VARIANTS", "on").strip().lower()
    return value not in ("off", "0", "false", "no")


def _is_plain_stem(name: str) -> bool:
    return bool(name) and not Path(name).suffix and "\\" not in name and ".." not in name


def variant_pool(folder: Path, name: str, *, pack: Optional[str] = None) -> list[Path]:
    """Every file that may stand in for ``name`` in ``folder`` (sorted, stable).

    The pack folder (``library/<pack>/<name>``) replaces the generic pool
    when it has files. The stem file joins the generic pool so the
    original choice stays one of the options.
    """
    if not _is_plain_stem(name) or "/" in name:
        return []
    base = folder / LIBRARY_DIRNAME
    if pack and _is_plain_stem(pack) and "/" not in pack:
        packed = _audio_files(base / pack / name)
        if packed:
            return packed
    pool = _audio_files(base / name)
    stem = _stem_file(folder, name)
    if stem is not None:
        pool = [stem] + pool
    return pool


def pick_variant(
    folder: Path, name: str, seed: str, *, pack: Optional[str] = None
) -> Optional[Path]:
    """One member of the pool, chosen by a stable hash of ``seed``; None when
    variants are off or the pool is empty."""
    if not variants_enabled():
        return None
    pool = variant_pool(folder, name, pack=pack)
    if not pool:
        return None
    digest = hashlib.sha256(f"{seed}|{name}".encode("utf-8")).digest()
    return pool[int.from_bytes(digest[:8], "big") % len(pool)]


def first_library_file(folder: Path, name: str) -> Optional[Path]:
    """The seedless fallback for a mood/cue that exists only as a folder."""
    if not variants_enabled() or not _is_plain_stem(name) or "/" in name:
        return None
    files = _audio_files(folder / LIBRARY_DIRNAME / name)
    return files[0] if files else None


def _audio_files(directory: Path) -> list[Path]:
    if not directory.is_dir():
        return []
    return sorted(
        p for p in directory.iterdir()
        if p.is_file() and p.suffix.lower() in AUDIO_EXTENSIONS
    )


def _stem_file(folder: Path, name: str) -> Optional[Path]:
    for ext in AUDIO_EXTENSIONS:
        candidate = folder / f"{name}{ext}"
        if candidate.is_file():
            return candidate
    return None


# --------------------------------------------------------------------------- manifest
def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def load_manifest(assets_dir: Path) -> dict[str, Any]:
    path = assets_dir / MANIFEST_FILENAME
    if not path.is_file():
        return {"files": {}}
    return json.loads(path.read_text(encoding="utf-8"))


def shipped_audio(assets_dir: Path, roots: Iterable[str] = ("music", "sfx")) -> list[Path]:
    files: list[Path] = []
    for root in roots:
        base = assets_dir / root
        if base.is_dir():
            files.extend(
                p for p in base.rglob("*")
                if p.is_file() and p.suffix.lower() in AUDIO_EXTENSIONS
            )
    return sorted(files)


def check_manifest(assets_dir: Path, roots: Iterable[str] = ("music", "sfx")) -> list[str]:
    """Problems that must block packaging; an empty list means ship-clean.

    Each shipped audio file needs a manifest entry (keyed by its path
    relative to ``assets_dir``, forward slashes) whose ``sha256`` matches
    the file and whose ``license`` is on ``ALLOWED_LICENSES``, with a
    ``source`` saying where it came from.
    """
    entries: dict[str, dict] = load_manifest(assets_dir).get("files") or {}
    problems: list[str] = []
    for path in shipped_audio(assets_dir, roots):
        key = path.relative_to(assets_dir).as_posix()
        entry = entries.get(key)
        if entry is None:
            problems.append(f"{key}: no manifest entry")
            continue
        licence = entry.get("license")
        if licence not in ALLOWED_LICENSES:
            problems.append(f"{key}: licence {licence!r} not allowed")
        if not entry.get("source"):
            problems.append(f"{key}: no source recorded")
        if entry.get("sha256") != _sha256(path):
            problems.append(f"{key}: sha256 does not match the manifest")
    return problems


def manifest_entry(path: Path, **fields: Any) -> dict[str, Any]:
    """A manifest entry for ``path`` with its current sha256 and size."""
    entry = dict(fields)
    entry["sha256"] = _sha256(path)
    entry["bytes"] = path.stat().st_size
    return entry
