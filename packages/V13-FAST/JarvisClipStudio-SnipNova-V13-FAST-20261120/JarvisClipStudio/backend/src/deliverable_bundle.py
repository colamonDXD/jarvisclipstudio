"""Create the small, ready-to-publish bundle that accompanies a rendered clip."""

from __future__ import annotations

import json
import os
import shutil
from pathlib import Path
from typing import Any, Mapping

from .media.cover_selection import choose_cover_timestamp
from .media.edl_render import build_drawtext_filter
from .media.ffmpeg import run_ffmpeg_command
from .media.music_library import music_credit_line


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


def _cover_filter_graph(
    title: str, edl: Mapping[str, Any] | None
) -> tuple[str, list[str]]:
    """Build the cover image graph, with Brand Kit treatment when present.

    Without a brand block this is exactly the historical graph (a
    drawtext title over the grabbed frame). With one, the title is
    drawn in the kit's treatment — a band in the kit's primary colour,
    or the text recoloured to the kit accent — and the kit's logo is
    composited in its corner, so the thumbnail/poster carries the same
    branding as the clip itself.
    """
    extra_inputs: list[str] = []
    title_filter = build_drawtext_filter(title, 0, 1, "[0:v]", "[cover]")
    brand_title = (edl or {}).get("brand_title")
    if title and isinstance(brand_title, Mapping) and brand_title.get("color"):
        color = str(brand_title["color"]).replace("#", "0x", 1)
        if brand_title.get("mode") == "band":
            title_filter = title_filter.replace(
                "boxcolor=0x000000AA", f"boxcolor={color}"
            )
        else:
            title_filter = title_filter.replace("fontcolor=white", f"fontcolor={color}")
    graph = title_filter if title else "[0:v]null[cover]"
    watermark = (edl or {}).get("brand_watermark")
    if isinstance(watermark, Mapping):
        from pathlib import Path as _Path

        logo_file = _Path(str(watermark.get("path") or ""))
        if logo_file.is_file():
            try:
                opacity = min(1.0, max(0.05, float(watermark.get("opacity", 0.85))))
                width_frac = min(0.5, max(0.05, float(watermark.get("width_frac", 0.16))))
                margin_frac = min(0.2, max(0.0, float(watermark.get("margin_frac", 0.03))))
            except (TypeError, ValueError):
                opacity, width_frac, margin_frac = 0.85, 0.16, 0.03
            position = str(watermark.get("position") or "bottom_right")
            margin_x = f"W*{margin_frac:.3f}"
            margin_y = f"H*{margin_frac:.3f}"
            positions = {
                "bottom_right": f"W-w-{margin_x}:H-h-{margin_y}",
                "bottom_left": f"{margin_x}:H-h-{margin_y}",
                "top_right": f"W-w-{margin_x}:{margin_y}",
                "top_left": f"{margin_x}:{margin_y}",
            }
            extra_inputs = ["-loop", "1", "-i", str(logo_file)]
            # Frame-relative sizing, same as the clip renderer: the
            # grabbed frame is split, one copy is the scale filter's
            # reference (``rw`` = frame width), so the logo lands at
            # width_frac of the frame — the same on-screen size
            # whatever pixel size the uploaded logo file has.
            graph += (
                f";[cover]split[coverbase][coverref]"
                f";[1:v][coverref]scale=w='rw*{width_frac:.3f}':h=-1:flags=lanczos,format=rgba,"
                f"colorchannelmixer=aa={opacity:.3f},setsar=1[brandlogo]"
                f";[coverbase][brandlogo]overlay={positions.get(position, positions['bottom_right'])}[branded]"
            )
            return graph, extra_inputs
    return graph, extra_inputs


def _place_clip_in_bundle(rendered_clip: Path, destination: Path) -> None:
    """Put the finished clip at the bundle's ``clip.mp4``: a hardlink
    when the filesystem allows it (the bundle then costs no extra disk
    and no copy time — F-P2), a full copy otherwise.

    Link safety: nothing in the pipeline rewrites a finished clip in
    place — the one post-render transform (the resolution cap) writes
    a sibling file and replaces the clip BEFORE the bundle is written,
    and deletions unlink only their own path — so the two names can
    safely share one inode. If linking ever fails (cross-device,
    filesystem without hardlinks), the copy keeps the bundle correct.
    """
    try:
        os.link(rendered_clip, destination)
    except OSError:
        shutil.copy2(rendered_clip, destination)


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
    _place_clip_in_bundle(rendered_clip, target / "clip.mp4")

    # The historical cover timestamp, upgraded to a scored choice when
    # a sampled frame is clearly better (face present, sharper, sane
    # exposure). choose_cover_timestamp never raises and returns the
    # historical value unchanged on any doubt, so this cannot fail or
    # stall the bundle.
    timestamp = _peak_timestamp(edl)
    timestamp = choose_cover_timestamp(rendered_clip, timestamp)
    title = (hook_title or str((llm_output or {}).get("title") or "")).strip()
    filter_graph, cover_extra_inputs = _cover_filter_graph(title, edl)
    cover_label = "branded" if cover_extra_inputs else "cover"
    result = run_ffmpeg_command(
        [
            "ffmpeg", "-y", "-ss", f"{timestamp:.3f}", "-i", str(rendered_clip),
            *cover_extra_inputs,
            "-filter_complex", filter_graph, "-map", f"[{cover_label}]", "-frames:v", "1",
            "-q:v", "2", str(target / "cover.jpg"),
        ],
        timeout=120,
    )
    if result.returncode != 0:
        raise RuntimeError("Could not create deliverable cover image")
    post_text = _post_text(llm_output, hook_title)
    # Licence hygiene: when the clip used an exact library track that a
    # CREDITS.json manifest covers, the post copy carries the credit line.
    # No manifest (or no exact track) leaves post.txt byte-identical.
    credit = music_credit_line(edl)
    if credit:
        post_text = f"{post_text}Music: {credit}\n"
    # Comment bait: when the clip was finished with COMMENT_BAIT on, its
    # EDL carries the pinned-comment question; the post copy prints it so
    # the poster can pin it as the first comment. No key on the EDL (the
    # switch was off) leaves post.txt byte-identical.
    question = str((edl or {}).get("comment_question") or "").strip()
    if question:
        post_text = f"{post_text}Pinned comment: {question}\n"
    (target / "post.txt").write_text(post_text, encoding="utf-8")

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
