"""Brand Kit: the user's own reusable branding for their clips.

A kit is a per-user record (table ``brand_kits``) holding the choices
that make clips recognisably *theirs*: caption colours, a caption
template, a caption font, a hook-title treatment, a default music track
and a logo watermark. This module is the pure core — validation, asset
storage and the render-input resolution — with no database or HTTP in
it, so the precedence rules are unit-testable without either.

How a kit reaches a render (all through existing plumbing):

* **Captions** — the task's stored ``caption_template`` / ``font_family``
  / ``font_color`` are filled from the kit at creation time when the
  task did not choose its own, and the kit's primary/accent colours ride
  the EDL as a template override (``brand_template_override``) that the
  caption builder applies over the named template.
* **Hook title** — the EDL carries ``brand_title``: a background band in
  the kit's primary colour behind the title, or the title text itself
  recoloured to the kit's accent colour.
* **Music** — the kit track becomes the task's ``music_override`` when
  the task picked none, so it wins over profile moods and the Auto bed
  exactly like a create-bar pick.
* **Logo watermark** — the EDL carries ``brand_watermark`` and the EDL
  renderer composites the stored logo image over the whole clip at the
  kit's corner and opacity, sized as a fixed fraction of the frame
  width (never of the logo file's own pixel size, so a high-res and a
  low-res upload of the same logo render identically).
* **Cover / thumbnail** — the deliverable bundle reads the same EDL
  block and stamps the logo and the kit-coloured title band onto
  ``cover.jpg``.

Precedence, highest first, per render input: an explicit choice on the
task (create form / task row) > the kit default > the niche profile
default. :func:`resolve_brand_application` implements exactly that for
the EDL-level inputs; the creation-time fill in the tasks route covers
the stored-column inputs (caption template, font, explicit music).

Intro/outro asset names are stored on the kit and carried onto opting-in
tasks; :func:`brand_bumpers` resolves them to files at application time
and the EDL render stitches them around the finished clip (normalised to
the clip's frame, audio normalised to the clip's loudness) — any bumper
that fails to stitch leaves the plain clip, never a failed task.
"""

from __future__ import annotations

import re
import shutil
import uuid
from pathlib import Path
from typing import Any, Optional

from ..config import get_config

# The hook-title wording strategies the shipped profiles use. A kit may
# only pick one of these — the EDL records it on the hook block.
TITLE_STYLES = ("statement", "question", "statistic", "contrast", "story")

LOGO_POSITIONS = ("bottom_right", "bottom_left", "top_right", "top_left")
LOGO_EXTENSIONS = (".png", ".jpg", ".jpeg", ".webp")
MAX_LOGO_BYTES = 10 * 1024 * 1024
# Image suffixes the asset-reference validator accepts for intro/outro
# picks: video files only (a bumper is footage, not a still).
VIDEO_EXTENSIONS = (".mp4", ".mov", ".mkv", ".webm", ".m4v")

_HEX_COLOR = re.compile(r"^#[0-9A-Fa-f]{6}$")
_UNSAFE_FILENAME_CHARS = re.compile(r"[^A-Za-z0-9 ._-]+")

KIT_FIELDS = (
    "name",
    "primary_color",
    "accent_color",
    "caption_template",
    "font_name",
    "title_style",
    "music_track",
    "logo_filename",
    "logo_position",
    "logo_opacity",
    "intro_asset",
    "outro_asset",
)


# ---------------------------------------------------------------------------
# Validation (create/update payloads). Every validator returns the
# normalised value or raises ValueError; None means "no kit default".
# ---------------------------------------------------------------------------


def validate_color(value: Any, field: str) -> Optional[str]:
    """Normalise a ``#RRGGBB`` colour or raise."""
    if value is None:
        return None
    if not isinstance(value, str) or not _HEX_COLOR.match(value.strip()):
        raise ValueError(
            f"Invalid {field} '{value}'. Use a hex colour like #7C5CFF."
        )
    return value.strip().upper()


def validate_kit_name(value: Any) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("Give the brand kit a name first")
    name = value.strip()
    if len(name) > 120:
        raise ValueError("Brand kit names are limited to 120 characters")
    return name


def validate_caption_template(value: Any) -> Optional[str]:
    if value is None:
        return None
    from ..caption_templates import get_template_names

    if not isinstance(value, str) or value.strip() not in get_template_names():
        raise ValueError(
            f"Invalid caption_template '{value}'. Valid values: "
            + ", ".join(sorted(get_template_names()))
            + " or null."
        )
    return value.strip()


def validate_font_name(value: Any) -> Optional[str]:
    """A font the caption builder can actually load, or None.

    System fonts are checked against the registry without a user (the
    registry call itself can shell out to Fontconfig, so route code runs
    this in a thread). User-uploaded fonts are validated per user at
    upload/apply time by the same registry in the routes layer.
    """
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise ValueError("Invalid font_name. Use a font name or null.")
    name = value.strip()
    from ..font_registry import get_available_fonts

    known = {entry["name"] for entry in get_available_fonts()}
    known |= {entry["filename"] for entry in get_available_fonts()}
    if name not in known:
        raise ValueError(
            f"Invalid font_name '{value}'. It does not match any available font."
        )
    return name


def validate_title_style(value: Any) -> Optional[str]:
    if value is None:
        return None
    if not isinstance(value, str) or value.strip() not in TITLE_STYLES:
        raise ValueError(
            f"Invalid title_style '{value}'. Valid values: "
            + ", ".join(TITLE_STYLES)
            + " or null."
        )
    return value


def validate_music_track(value: Any) -> Optional[str]:
    """Reuse the music library's own validator (live library membership)."""
    if value is None:
        return None
    from ..media.music_library import validate_music_override

    return validate_music_override(value)


def validate_logo_position(value: Any) -> str:
    if value is None:
        return "bottom_right"
    if not isinstance(value, str) or value not in LOGO_POSITIONS:
        raise ValueError(
            f"Invalid logo_position '{value}'. Valid values: "
            + ", ".join(LOGO_POSITIONS)
            + "."
        )
    return value


def validate_logo_opacity(value: Any) -> float:
    if value is None:
        return 0.85
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError("Invalid logo_opacity. Use a number between 0.1 and 1.")
    opacity = float(value)
    if not 0.1 <= opacity <= 1.0:
        raise ValueError("Invalid logo_opacity. Use a number between 0.1 and 1.")
    return opacity


def validate_asset_reference(value: Any, field: str) -> Optional[str]:
    """An intro/outro pick: a bare video filename, or None.

    Existence is checked when the file is meant to be used, not here —
    the library is folder-driven and a name recorded today may be
    replaced tomorrow. The render does not consume these yet.
    """
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError(f"Invalid {field}. Use a video filename or null.")
    name = value.strip()
    if not name or name != Path(name).name:
        raise ValueError(f"Invalid {field} '{value}'. Use a bare filename.")
    if Path(name).suffix.lower() not in VIDEO_EXTENSIONS:
        raise ValueError(
            f"Invalid {field} '{value}'. Use one of: "
            + ", ".join(VIDEO_EXTENSIONS)
            + "."
        )
    return name


def validate_kit_payload(payload: dict, *, partial: bool = False) -> dict:
    """Validate a create (``partial=False``) or update payload.

    Returns the normalised field dict. On create, ``name`` is required
    and the logo position/opacity defaults are filled; on update only
    the keys present are validated and returned.
    """
    if not isinstance(payload, dict):
        raise ValueError("Brand kit data must be an object")
    unknown = set(payload) - set(KIT_FIELDS)
    if unknown:
        raise ValueError(f"Unknown brand kit fields: {', '.join(sorted(unknown))}")
    cleaned: dict[str, Any] = {}
    if not partial or "name" in payload:
        cleaned["name"] = validate_kit_name(payload.get("name"))
    if "primary_color" in payload:
        cleaned["primary_color"] = validate_color(
            payload.get("primary_color"), "primary_color"
        )
    if "accent_color" in payload:
        cleaned["accent_color"] = validate_color(
            payload.get("accent_color"), "accent_color"
        )
    if "caption_template" in payload:
        cleaned["caption_template"] = validate_caption_template(
            payload.get("caption_template")
        )
    if "font_name" in payload:
        cleaned["font_name"] = validate_font_name(payload.get("font_name"))
    if "title_style" in payload:
        cleaned["title_style"] = validate_title_style(payload.get("title_style"))
    if "music_track" in payload:
        cleaned["music_track"] = validate_music_track(payload.get("music_track"))
    if "logo_filename" in payload:
        # Set only through the logo upload endpoint; a payload may clear it.
        if payload.get("logo_filename") is not None:
            raise ValueError("Set the logo through the logo upload, not the kit fields")
        cleaned["logo_filename"] = None
    if "logo_position" in payload:
        cleaned["logo_position"] = validate_logo_position(payload.get("logo_position"))
    elif not partial:
        cleaned["logo_position"] = "bottom_right"
    if "logo_opacity" in payload:
        cleaned["logo_opacity"] = validate_logo_opacity(payload.get("logo_opacity"))
    elif not partial:
        cleaned["logo_opacity"] = 0.85
    if "intro_asset" in payload:
        cleaned["intro_asset"] = validate_asset_reference(
            payload.get("intro_asset"), "intro_asset"
        )
    if "outro_asset" in payload:
        cleaned["outro_asset"] = validate_asset_reference(
            payload.get("outro_asset"), "outro_asset"
        )
    return cleaned


# ---------------------------------------------------------------------------
# Logo storage — under the shared temp tree, next to user music and user
# profiles, so the API and every render worker see the same files.
# ---------------------------------------------------------------------------


def brand_assets_dir() -> Path:
    return Path(get_config().temp_dir) / "brand_assets"


def user_brand_dir(user_id: str) -> Path:
    from ..font_registry import sanitize_user_id_for_path

    return brand_assets_dir() / sanitize_user_id_for_path(user_id)


def sanitize_logo_filename(raw_name: str) -> str:
    name = Path(str(raw_name or "")).name.strip()
    name = _UNSAFE_FILENAME_CHARS.sub("_", name)
    name = name.lstrip(".").strip()
    stem = Path(name).stem.strip() or "logo"
    suffix = Path(name).suffix.lower()
    return f"{stem}{suffix}"


def unique_logo_filename(folder: Path, filename: str) -> str:
    candidate = filename
    counter = 2
    while (folder / candidate).exists():
        candidate = f"{Path(filename).stem}-{counter}{Path(filename).suffix}"
        counter += 1
    return candidate


def store_logo(user_id: str, source_path: Path, raw_name: str) -> str:
    """Copy an uploaded logo into the user's brand folder; return its name."""
    folder = user_brand_dir(user_id)
    folder.mkdir(parents=True, exist_ok=True)
    filename = unique_logo_filename(folder, sanitize_logo_filename(raw_name))
    shutil.copy2(source_path, folder / filename)
    return filename


def logo_path(user_id: str, filename: Optional[str]) -> Optional[Path]:
    """Resolve a kit's stored logo to a real file, or None."""
    if not filename or filename != Path(filename).name:
        return None
    path = user_brand_dir(user_id) / filename
    return path if path.is_file() else None


def delete_logo_file(user_id: str, filename: Optional[str]) -> None:
    path = logo_path(user_id, filename)
    if path is not None:
        path.unlink(missing_ok=True)


# ---------------------------------------------------------------------------
# Render-input resolution — the precedence core.
# ---------------------------------------------------------------------------


def brand_template_override(kit: dict) -> Optional[dict]:
    """The caption-template override carrying the kit's colours, or None.

    Only the fields the kit actually sets are overridden; everything
    else (animation, pill, glow, …) keeps coming from the named
    template the kit or task selected.
    """
    override: dict[str, Any] = {}
    if kit.get("primary_color"):
        override["font_color"] = kit["primary_color"]
    if kit.get("accent_color"):
        override["highlight_color"] = kit["accent_color"]
        override["emphasis_color"] = kit["accent_color"]
    return override or None


def brand_watermark(kit: dict, user_id: Optional[str]) -> Optional[dict]:
    """The EDL watermark block for the kit's logo, or None.

    The path is only stamped when the file really exists on this
    machine — a kit whose logo was never uploaded (or was removed)
    renders unbranded rather than failing.

    ``width_frac`` is the watermark's width as a fraction of the
    FRAME width (0.16 = 16% of the frame), applied frame-relatively
    by both consumers (the EDL renderer and the bundle cover graph).
    It is deliberately not relative to the logo file's own pixel
    size: the same kit renders the same on-screen watermark whatever
    resolution the user uploaded the logo at.
    """
    path = logo_path(user_id or "", kit.get("logo_filename"))
    if path is None:
        return None
    return {
        "path": str(path),
        "position": kit.get("logo_position") or "bottom_right",
        "opacity": float(kit.get("logo_opacity") or 0.85),
        "width_frac": 0.16,
        "margin_frac": 0.03,
    }


def brand_title_block(kit: dict) -> Optional[dict]:
    """The EDL hook-title treatment block, or None when the kit sets none."""
    if kit.get("primary_color"):
        return {"mode": "band", "color": kit["primary_color"]}
    if kit.get("accent_color"):
        return {"mode": "accent_text", "color": kit["accent_color"]}
    return None


def resolve_brand_application(
    kit: Optional[dict],
    *,
    user_id: Optional[str] = None,
    task_font_color: Optional[str] = None,
    task_font_family: Optional[str] = None,
) -> dict:
    """Resolve a kit into the render inputs the EDL builders consume.

    Returns a dict with the EDL extras (``brand_template_override``,
    ``brand_title``, ``brand_watermark``), the hook ``title_style`` and
    a ``font_color`` for the plain (non-EDL) render path. Task-level
    explicit choices win: a task that set its own caption colour or
    font keeps it, and the kit only fills what the task left open —
    the profile defaults sit below both and are applied by the
    existing builders when nothing here provides a value.
    """
    empty = {
        "brand_template_override": None,
        "brand_title": None,
        "brand_watermark": None,
        "brand_bumpers": {},
        "title_style": None,
        "font_color": None,
    }
    if not kit:
        return empty
    override = brand_template_override(kit)
    if task_font_color and override is not None:
        # The user picked this task's caption colour explicitly; the
        # kit's primary must not repaint it. The accent still applies —
        # no task-level accent control exists to override it.
        override = {k: v for k, v in override.items() if k != "font_color"}
        if not override:
            override = None
    return {
        "brand_template_override": override,
        "brand_title": brand_title_block(kit),
        "brand_watermark": brand_watermark(kit, user_id),
        "brand_bumpers": brand_bumpers(kit, user_id),
        "title_style": kit.get("title_style"),
        "font_color": None if task_font_color else kit.get("primary_color"),
    }


def bumper_path(user_id: str, filename: Optional[str]) -> Optional[Path]:
    """Resolve a kit's stored intro/outro video to a real file, or None.

    Same fail-safe as the logo: a name that was never uploaded, was
    removed, or escapes the brand folder resolves to None.
    """
    if not filename or filename != Path(filename).name:
        return None
    folder = user_brand_dir(user_id)
    path = folder / filename
    if path.is_file():
        return path
    # Kits may also reference a track by bare stem (validate_asset_reference
    # accepts any video filename, and a user can name one without its
    # extension in the same way music tracks are named): resolve by stem.
    stem = Path(filename).stem
    for suffix in VIDEO_EXTENSIONS:
        candidate = folder / f"{stem}{suffix}"
        if candidate.is_file():
            return candidate
    return None


def brand_bumpers(kit: dict, user_id: Optional[str]) -> dict:
    """The EDL bumper block for the kit's intro/outro picks, or empty.

    Each side resolves independently: a missing intro never blocks a
    present outro. Returns {} when neither side resolves.
    """
    bumpers: dict[str, Any] = {}
    intro = bumper_path(user_id or "", kit.get("intro_asset"))
    if intro is not None:
        bumpers["intro"] = {"path": str(intro)}
    outro = bumper_path(user_id or "", kit.get("outro_asset"))
    if outro is not None:
        bumpers["outro"] = {"path": str(outro)}
    return bumpers


def new_kit_id() -> str:
    return uuid.uuid4().hex
