"""Cached, CPU-only preview renders for the extended transition library.

The catalogue API exposes one short clip per registry entry so the editor
can show what a transition looks like before a project uses it. A preview
is a 1.2 s join between two generated gradient cards: no external assets,
no source video, no GPU. Renders are cached by name under
``{TEMP_DIR}/transition_previews`` and served from there afterwards.

The registry itself only returns filter-graph fragments. This module owns
the ffmpeg invocation, the card sources and the cache path, keeping
``transition_library.py`` a standard-library-only data module.
"""

from __future__ import annotations

import os
import uuid
from pathlib import Path

from .ffmpeg import run_ffmpeg_command
from .transition_library import TRANSITIONS

PREVIEW_WIDTH = 360
PREVIEW_HEIGHT = 640
PREVIEW_FPS = 30
PREVIEW_SECONDS = 1.2
PREVIEW_TRANSITION_SECONDS = 0.5
# A builder's two inputs are ``offset + duration`` long and its output is
# ``2 * offset + duration``; 0.35 + 0.5 + 0.35 = the 1.2 s preview.
PREVIEW_OFFSET_SECONDS = (PREVIEW_SECONDS - PREVIEW_TRANSITION_SECONDS) / 2.0
PREVIEW_CARD_SECONDS = PREVIEW_OFFSET_SECONDS + PREVIEW_TRANSITION_SECONDS

PREVIEW_API_ENV = "TRANSITION_LIBRARY_API"
PREVIEW_CACHE_DIRNAME = "transition_previews"

_CARD_A = (
    "gradients=s={w}x{h}:r={fps}:c0=0x1E3A8A:c1=0x60A5FA"
    ":x0=0:y0=0:x1={w}:y1={h}:d={card}"
)
_CARD_B = (
    "gradients=s={w}x{h}:r={fps}:c0=0xD97706:c1=0xFBBF24"
    ":x0={w}:y0=0:x1=0:y1={h}:d={card}"
)


def transition_preview_enabled() -> bool:
    """Whether the catalogue and preview routes are exposed.

    ``TRANSITION_LIBRARY_API=off`` (also ``0``, ``false`` or ``no``) is the
    off switch required by the rollout rules. Anything else leaves the
    routes on, including an unset variable.
    """
    value = os.getenv(PREVIEW_API_ENV, "").strip().lower()
    return value not in {"off", "0", "false", "no"}


def preview_cache_dir(temp_dir: str | Path) -> Path:
    return Path(temp_dir) / PREVIEW_CACHE_DIRNAME


def preview_cache_path(temp_dir: str | Path, name: str) -> Path:
    """Resolve one cache file, refusing anything outside the cache directory."""
    cache_dir = preview_cache_dir(temp_dir).resolve()
    path = (cache_dir / f"{name}.mp4").resolve()
    if path.parent != cache_dir:
        raise ValueError(f"invalid transition preview name: {name!r}")
    return path


def _card_source(template: str) -> str:
    return template.format(
        w=PREVIEW_WIDTH,
        h=PREVIEW_HEIGHT,
        fps=PREVIEW_FPS,
        card=f"{PREVIEW_CARD_SECONDS:.6g}",
    )


def _build_command(name: str, output: Path) -> list[str]:
    spec = TRANSITIONS[name]
    graph = (
        "[0:v]format=yuv420p,setsar=1[a];"
        "[1:v]format=yuv420p,setsar=1[b];"
        + spec.builder(
            PREVIEW_TRANSITION_SECONDS,
            PREVIEW_OFFSET_SECONDS,
            PREVIEW_WIDTH,
            PREVIEW_HEIGHT,
            PREVIEW_FPS,
        )
    )
    return [
        "ffmpeg",
        "-y",
        "-hide_banner",
        "-loglevel",
        "error",
        "-f",
        "lavfi",
        "-i",
        _card_source(_CARD_A),
        "-f",
        "lavfi",
        "-i",
        _card_source(_CARD_B),
        "-filter_complex",
        graph,
        "-map",
        "[out]",
        "-t",
        f"{PREVIEW_SECONDS:.6g}",
        "-c:v",
        "libx264",
        "-preset",
        "veryfast",
        "-crf",
        "23",
        "-pix_fmt",
        "yuv420p",
        "-an",
        "-movflags",
        "+faststart",
        str(output),
    ]


def render_transition_preview(name: str, output: Path) -> Path:
    """Render one preview to ``output`` through ffmpeg.

    The encode goes to a unique temporary file in the same directory and is
    renamed into place only after ffmpeg succeeds, so a concurrent request
    or a crashed encode can never serve a half-written MP4.
    """
    if name not in TRANSITIONS:
        raise KeyError(name)

    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_name(
        f".{output.stem}-{uuid.uuid4().hex}.tmp{output.suffix}"
    )
    try:
        result = run_ffmpeg_command(_build_command(name, temporary), timeout=120)
        if result.returncode != 0:
            raise RuntimeError(
                f"ffmpeg failed to render transition preview {name!r}"
            )
        temporary.replace(output)
    finally:
        temporary.unlink(missing_ok=True)
    return output
