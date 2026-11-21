"""User-generated niche profiles (Reference Style Analyser, Lane H2).

Profiles created in the app from a reference video live OUTSIDE the stock
profiles tree: the stock directory ships with the code and its 14 files
must never be overwritten by user content. User profiles live in a
writable directory resolved from config (``USER_PROFILES_DIR``, default
``<TEMP_DIR>/user_profiles`` — the shared uploads volume in Docker, so
backend, worker and ai-worker all see the same styles).

Naming scheme: a user profile's public name is ``user-<slug>``, where the
slug is derived from the display name the user typed (``My Funk Edit`` ->
``user-my-funk-edit``). On disk the profile is ``<slug>.json`` with its
measurements beside it as ``<slug>.measurements.json``. The ``user-``
prefix is what the loader and the validators key on: no stock niche
carries it, so a user profile can never shadow a stock profile, stock
resolution always wins on any apparent collision, and deletion can only
ever touch files inside this directory.
"""

from __future__ import annotations

import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

USER_PROFILE_PREFIX = "user-"

_SLUG_RE = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")
_NON_SLUG_CHARS = re.compile(r"[^a-z0-9]+")


def user_profiles_dir() -> Path:
    """The configured directory holding user profile files."""
    from ..config import get_config

    return Path(get_config().user_profiles_dir)


def ensure_user_profiles_dir() -> Path:
    folder = user_profiles_dir()
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def slugify_style_name(name: str) -> str:
    """Turn a free-form display name into a filesystem-safe slug."""
    slug = _NON_SLUG_CHARS.sub("-", (name or "").lower()).strip("-")
    slug = slug[:48].strip("-")
    return slug or "reference-style"


def is_user_profile_name(value: object) -> bool:
    """True when ``value`` has the ``user-<slug>`` shape (existence aside)."""
    if not isinstance(value, str):
        return False
    if not value.startswith(USER_PROFILE_PREFIX):
        return False
    return bool(_SLUG_RE.match(value[len(USER_PROFILE_PREFIX):]))


def _slug_of(name: str) -> str:
    return name[len(USER_PROFILE_PREFIX):]


def user_profile_file(name: str) -> Optional[Path]:
    """The stored profile JSON for a user profile name, if it exists."""
    if not is_user_profile_name(name):
        return None
    candidate = user_profiles_dir() / f"{_slug_of(name)}.json"
    return candidate if candidate.is_file() else None


def user_profile_exists(name: object) -> bool:
    return user_profile_file(name) is not None  # type: ignore[arg-type]


def unique_user_slug(base_slug: str) -> str:
    """First free slug for a new profile: base, base-2, base-3, ..."""
    folder = user_profiles_dir()
    slug = base_slug
    counter = 2
    while (folder / f"{slug}.json").exists():
        slug = f"{base_slug}-{counter}"
        counter += 1
    return slug


def summarize_profile(name: str, profile: dict[str, Any]) -> dict[str, Any]:
    """The API-facing summary of one stored user profile."""
    reference = profile.get("reference_style") or {}
    return {
        "name": name,
        "display_name": reference.get("name") or _slug_of(name),
        "base_niche": reference.get("base_niche"),
        # Honesty fields for the base-niche label (None on styles created
        # before the margin was recorded): the margin over the runner-up,
        # the runner-up itself, and whether the pick was a near-tie that
        # must not be shown as a confident verdict.
        "base_niche_margin": reference.get("base_niche_margin"),
        "base_niche_runner_up": reference.get("base_niche_runner_up"),
        "base_niche_close_call": reference.get("base_niche_close_call"),
        "source_file": reference.get("source_file"),
        "headline": reference.get("headline") or {},
    }


def list_user_profiles() -> list[dict[str, Any]]:
    """Every stored user profile's summary, oldest first.

    Corrupt or half-written files are skipped rather than failing the
    whole listing — one bad file must not hide the user's other styles.
    """
    folder = user_profiles_dir()
    if not folder.is_dir():
        return []
    summaries: list[dict[str, Any]] = []
    for entry in sorted(folder.iterdir(), key=lambda p: p.name.lower()):
        if not entry.is_file() or entry.name.startswith("."):
            continue
        if entry.suffix != ".json" or entry.name.endswith(".measurements.json"):
            continue
        slug = entry.stem
        if not _SLUG_RE.match(slug):
            continue
        try:
            profile = json.loads(entry.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if not isinstance(profile, dict):
            continue
        summary = summarize_profile(f"{USER_PROFILE_PREFIX}{slug}", profile)
        summary["created_at"] = datetime.fromtimestamp(
            entry.stat().st_mtime, tz=timezone.utc
        ).isoformat()
        summaries.append(summary)
    return summaries


def _write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    os.replace(temporary, path)


def save_user_profile(
    slug: str,
    profile: dict[str, Any],
    measurements_doc: dict[str, Any],
) -> dict[str, Any]:
    """Store a profile + its measurements under ``slug``; return the summary."""
    folder = ensure_user_profiles_dir()
    _write_json_atomic(folder / f"{slug}.json", profile)
    _write_json_atomic(folder / f"{slug}.measurements.json", measurements_doc)
    return summarize_profile(f"{USER_PROFILE_PREFIX}{slug}", profile)


def delete_user_profile(name: str) -> bool:
    """Remove a user profile and its measurements. False when absent.

    Only names with the ``user-<slug>`` shape are considered, and the slug
    grammar contains no path separators, so this can never reach the
    stock tree or anywhere outside the user profiles directory.
    """
    if not is_user_profile_name(name):
        return False
    folder = user_profiles_dir()
    slug = _slug_of(name)
    profile_path = folder / f"{slug}.json"
    existed = profile_path.is_file()
    profile_path.unlink(missing_ok=True)
    (folder / f"{slug}.measurements.json").unlink(missing_ok=True)
    return existed
