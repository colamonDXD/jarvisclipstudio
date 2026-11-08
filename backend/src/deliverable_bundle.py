"""Create the small, ready-to-publish bundle that accompanies a rendered clip."""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any, Mapping

from .media.edl_render import build_drawtext_filter
from .media.ffmpeg import run_ffmpeg_command


def _peak_timestamp(edl: Mapping[str, Any] | None) -> float:
    """Return the clip-relative energy/emphasis time already represented by an EDL."""
    if not edl:
        return 0.0
    for key in ("peak_energy_timestamp", "peak_energy_time"):
        value = edl.get(key)
        if isinstance(value, (int, float)) and value >= 0:
            return float(value)
    peaks = edl.get("energy_peaks")
    if isinstance(peaks, list):
        for peak in peaks:
            if isinstance(peak, (int, float)) and peak >= 0:
                return float(peak)
    for keyframe in edl.get("zoom_keyframes") or []:
        if keyframe.get("type") == "punch_in" and isinstance(keyframe.get("time"), (int, float)):
            return max(0.0, float(keyframe["time"]))
    return 0.0


def _post_text(llm_output: Mapping[str, Any] | None, hook_title: str | None) -> str:
    """Format stored LLM copy without ever asking a model for more text."""
    data = llm_output or {}
    title = str(data.get("title") or data.get("hook_title") or hook_title or "Untitled clip").strip()
    description = str(data.get("description") or data.get("caption") or "").strip()
    hashtags = data.get("hashtags") or data.get("tags") or ""
    if isinstance(hashtags, (list, tuple)):
        hashtags = " ".join(str(tag) if str(tag).startswith("#") else f"#{tag}" for tag in hashtags)
    lines = [title]
    if description:
        lines.extend(["", description])
    if str(hashtags).strip():
        lines.extend(["", str(hashtags).strip()])
    return "\n".join(lines) + "\n"


def _image_sources(edl: Mapping[str, Any] | None) -> list[dict[str, Any]]:
    """Return every reference image and B-roll asset the EDL asked for."""
    if not edl:
        return []
    sources: list[dict[str, Any]] = []
    for overlay in edl.get("overlays") or []:
        if not isinstance(overlay, Mapping) or overlay.get("type") != "reference_image":
            continue
        sources.append(
            {
                "kind": "reference_image",
                "source": overlay.get("source") or "unknown",
                "url": overlay.get("url") or "",
                "local_path": overlay.get("local_path") or "",
            }
        )
    for broll in edl.get("broll") or []:
        if not isinstance(broll, Mapping):
            continue
        sources.append(
            {
                "kind": "broll",
                "source": broll.get("source") or "unknown",
                "url": broll.get("url") or "",
                "local_path": broll.get("local_path") or "",
                "query": broll.get("query") or "",
            }
        )
    return sources


def read_recorded_profile(rendered_clip: Path) -> str | None:
    """Return the niche profile recorded beside a rendered clip, if any.

    ``write_deliverable_bundle`` stores the effective profile in
    ``profile.txt`` inside the bundle folder; readers use it to report the
    niche a clip was actually rendered with.
    """
    try:
        text = (Path(rendered_clip).with_suffix("") / "profile.txt").read_text(
            encoding="utf-8"
        ).strip()
    except OSError:
        return None
    return text or None


def write_deliverable_bundle(
    rendered_clip: Path,
    edl: Mapping[str, Any] | None,
    llm_output: Mapping[str, Any] | None,
    hook_title: str | None,
    *,
    bundle_dir: Path | None = None,
    profile_name: str | None = None,
) -> Path:
    """Write the publishable clip bundle and auto-director logs for one clip.

    Besides ``clip.mp4``, ``cover.jpg`` and ``post.txt``, the folder records the
    exact EDL (``edl.json``), the effective niche profile (``profile.txt``), and
    every image/B-roll source the EDL referenced (``image_sources.json``).

    ``llm_output`` is intentionally data-only: this module never invokes an LLM.
    The returned folder is deterministic beside the rendered clip unless supplied.
    """
    if not rendered_clip.is_file():
        raise FileNotFoundError(rendered_clip)
    target = bundle_dir or rendered_clip.with_suffix("")
    target.mkdir(parents=True, exist_ok=True)
    shutil.copy2(rendered_clip, target / "clip.mp4")

    timestamp = _peak_timestamp(edl)
    title = (hook_title or str((llm_output or {}).get("title") or "")).strip()
    filter_graph = build_drawtext_filter(title, 0, 1, "[0:v]", "[cover]") if title else "[0:v]null[cover]"
    result = run_ffmpeg_command(
        [
            "ffmpeg", "-y", "-ss", f"{timestamp:.3f}", "-i", str(rendered_clip),
            "-filter_complex", filter_graph, "-map", "[cover]", "-frames:v", "1",
            "-q:v", "2", str(target / "cover.jpg"),
        ],
        timeout=120,
    )
    if result.returncode != 0:
        raise RuntimeError("Could not create deliverable cover image")
    (target / "post.txt").write_text(_post_text(llm_output, hook_title), encoding="utf-8")

    # Logging only: these files describe what the render used and never feed
    # back into how the clip is produced.
    (target / "edl.json").write_text(
        json.dumps(edl or {}, indent=2, sort_keys=True, default=str) + "\n",
        encoding="utf-8",
    )
    (target / "profile.txt").write_text(
        f"{profile_name}\n" if profile_name else "", encoding="utf-8"
    )
    (target / "image_sources.json").write_text(
        json.dumps(_image_sources(edl), indent=2) + "\n", encoding="utf-8"
    )
    return target
