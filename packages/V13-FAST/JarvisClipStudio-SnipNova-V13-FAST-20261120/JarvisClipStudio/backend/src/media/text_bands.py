"""Task-level text bands: one title band + one caption band per task.

The framing lane unified text placement per CLIP (one caption band
from the clip's dominant geometry, the title as a hard obstacle for
captions). Across the clips of one task the bands still diverged —
the MrBeast exports that opened that lane put the title at the
bottom, lower-mid, top and centre across seven clips of ONE task,
because each clip's guard saw only its own face geometry.

This module decides the bands ONCE per task:

* Geometry: for every segment of the task, the production reframe
  planner runs on a one-segment mini-EDL over the segment's source
  ranges (the source analysis is cached per source, so the render's
  own attach reuses it) and the resolved output-space face zones /
  fit strips are collected — the same geometry the render will use.
* Decision: the WORST-CASE UNION rule. The task candidate is the
  preference position guarded against the union of every clip's
  dominant obstacles (the framing lane's coverage rule, lifted to
  task level), and it engages only when the representative block —
  the widest/tallest block any clip can draw — fits face-free
  against the union of every clip's HARD obstacles (faces + the
  task title block). If the full obstacle set leaves no band, the
  candidate is retried against the hard set alone (strips yield —
  the same priority the per-clip guards apply). If it still does
  not fit, that band does not engage and the task's clips keep the
  per-clip system, unchanged.
* Safety: engagement here is necessary, not sufficient. At render
  time the ASS builder re-verifies the offered centre against the
  individual clip's real hard obstacles and real block dimensions
  before adopting it, and the clip's title guard arbitrates the
  offered title bottom the same way — a clip whose geometry rejects
  the task band falls back to its per-clip placement and the
  deviation is stamped in the clip's layout report. The task band
  can therefore never put text on a face.
* Persistence: the decision is stored by the caller in the task's
  ``source_metadata`` under ``text_bands`` (the existing JSONB
  store — no migration) and is only ever READ afterwards: a stored
  decision is never re-decided, so single-clip re-renders and
  regenerations reuse the exact bands the original render used.
  Tasks without a stored decision behave exactly as before.

Approximations, all absorbed by the render-time verification:
segmentation (the raw segment window stands in for the render's
final keep-range tiles), the caption span (the full window stands
in for the first-to-last-chunk span in the coverage rule), and the
title block (natural template sizing; a Brand Kit band can render
slightly taller).
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

from .captions import (
    BAND_DOMINANT_COVERAGE,
    PLATFORM_BOTTOM_SAFE_FRACTION,
    TITLE_CAPTION_MIN_GAP_PX,
    get_safe_vertical_position,
    get_scaled_font_size,
    get_subtitle_max_width,
    natural_hook_title_block,
    strip_band_boxes,
)
from .common import HOOK_TITLE_TOP_MARGIN_FRAC
from .face_text_guard import block_fits_face_free, guarded_centre_y
from .ffmpeg import rendered_ranges_duration
from .reframe_plan import (
    attach_reframe_plan,
    collect_face_zones,
    collect_fit_strips,
)

logger = logging.getLogger(__name__)

TEXT_BANDS_VERSION = 1

Box = Tuple[float, float, float, float]


def _coverage(intervals: Sequence[Tuple[float, float]], span: float) -> float:
    """Share of ``[0, span]`` the intervals collectively cover."""
    if span <= 0:
        return 0.0
    clipped = sorted(
        (max(0.0, a), min(span, b)) for a, b in intervals if min(span, b) > max(0.0, a)
    )
    total = 0.0
    cur_start: Optional[float] = None
    cur_end = 0.0
    for a, b in clipped:
        if cur_start is None:
            cur_start, cur_end = a, b
        elif a <= cur_end:
            cur_end = max(cur_end, b)
        else:
            total += cur_end - cur_start
            cur_start, cur_end = a, b
    if cur_start is not None:
        total += cur_end - cur_start
    return total / span


def _guard_limits(
    video_height: int, est_text_height: int
) -> Tuple[int, int]:
    """The caption guard's centre limits — mirrors the ASS builder's."""
    guard_top = max(40, int(video_height * 0.05))
    guard_bottom = min(
        video_height - max(120, int(video_height * 0.10)) - est_text_height,
        int(video_height * (1 - PLATFORM_BOTTOM_SAFE_FRACTION))
        - est_text_height // 2,
    )
    return guard_top, guard_bottom


def _caption_style_metrics(
    template: Dict[str, Any], font_px: int
) -> Tuple[int, int, int, int]:
    """(outline_px, shadow_px, style_bord, border_style) as the builder
    derives them for the caption style, for the representative block."""
    base_stroke = int(template.get("stroke_width", 3) or 0)
    has_outline = template.get("stroke_color") is not None
    outline_px = (
        max(base_stroke, round(font_px * base_stroke / 26))
        if (has_outline and base_stroke)
        else 0
    )
    shadow_px = max(2, font_px // 20) if template.get("shadow") else 0
    border_style = (
        3 if template.get("background") and template.get("background_color") else 1
    )
    style_bord = outline_px
    if border_style == 3:
        style_bord = max(outline_px, font_px // 6)
    return outline_px, shadow_px, style_bord, border_style


def _guarded_task_candidate(
    pref_centre: float,
    block_x: float,
    block_w: float,
    block_h: float,
    obstacles: Sequence[Box],
    hard_obstacles: Sequence[Box],
    video_width: int,
    video_height: int,
    top_limit: float,
    bottom_limit: float,
) -> Optional[float]:
    """Preference guarded against the full set, engaged only if the
    result fits the HARD set; on failure, retry against the hard set
    alone (strips/bands yield). Returns the centre or None."""
    candidate = guarded_centre_y(
        pref_centre,
        block_x,
        block_w,
        block_h,
        obstacles,
        video_width,
        video_height,
        top_limit=top_limit,
        bottom_limit=bottom_limit,
    )
    if block_fits_face_free(
        candidate, block_x, block_w, block_h, hard_obstacles,
        video_width, video_height,
    ):
        return candidate
    candidate = guarded_centre_y(
        pref_centre,
        block_x,
        block_w,
        block_h,
        hard_obstacles,
        video_width,
        video_height,
        top_limit=top_limit,
        bottom_limit=bottom_limit,
    )
    if block_fits_face_free(
        candidate, block_x, block_w, block_h, hard_obstacles,
        video_width, video_height,
    ):
        return candidate
    return None


def decide_bands_from_geometry(
    clips: Sequence[Dict[str, Any]],
    *,
    template: Dict[str, Any],
    font_size: Optional[int],
    video_width: int,
    video_height: int,
    preference_y: Optional[float] = None,
) -> Optional[Dict[str, Any]]:
    """Decide the task's text bands from per-clip geometry.

    ``clips`` entries: ``{"duration", "face_zones", "fit_strips",
    "hook_title"}`` in OUTPUT coordinates at (video_width,
    video_height) — the shapes collect_face_zones /
    collect_fit_strips return. Returns the storable decision dict
    (fractions rounded to 4dp; a band that did not engage is None),
    or None when there is nothing to unify (no clips, or no clip
    carries any guard geometry — without zones/strips every clip
    already places text at the shared preference).
    """
    if not clips:
        return None
    if not any(c.get("face_zones") or c.get("fit_strips") for c in clips):
        return None

    effective_font_size = int(font_size) if font_size else int(template["font_size"])
    font_px = get_scaled_font_size(effective_font_size, video_width)
    est_text_height = int(font_px * 1.5)
    guard_top, guard_bottom = _guard_limits(video_height, est_text_height)

    # ---- title band (decided first: captions need the title obstacle)
    title_bottom: Optional[float] = None
    title_block: Optional[Box] = None
    titled = [c for c in clips if c.get("hook_title")]
    if titled:
        rep_w = 0.0
        rep_h = 0.0
        for clip in titled:
            block_w, block_h = natural_hook_title_block(
                str(clip["hook_title"]), template, video_width, font_px
            )
            rep_w = max(rep_w, block_w)
            rep_h = max(rep_h, block_h)
        avoid_union: List[Box] = [
            box
            for clip in clips
            for _zs, _ze, boxes in (clip.get("face_zones") or [])
            for box in boxes
        ]
        band_union: List[Box] = [
            box
            for clip in clips
            for box in strip_band_boxes(
                clip.get("fit_strips") or [],
                0.0,
                float(clip.get("duration") or 0.0),
                video_width,
                video_height,
            )
        ]
        margin_v = max(48, int(video_height * HOOK_TITLE_TOP_MARGIN_FRAC))
        pref_centre = margin_v + rep_h / 2
        centre = _guarded_task_candidate(
            pref_centre,
            (video_width - rep_w) / 2,
            rep_w,
            rep_h,
            list(avoid_union) + list(band_union),
            avoid_union,
            video_width,
            video_height,
            top_limit=rep_h / 2 + 8,
            bottom_limit=video_height * (1 - PLATFORM_BOTTOM_SAFE_FRACTION),
        )
        if centre is not None:
            bottom_px = centre + rep_h / 2
            title_bottom = round(bottom_px / video_height, 4)
            title_block = (
                (video_width - rep_w) / 2,
                bottom_px - rep_h,
                rep_w,
                rep_h,
            )

    # ---- caption band
    caption_centre: Optional[float] = None
    outline_px, shadow_px, style_bord, border_style = _caption_style_metrics(
        template, font_px
    )
    # The widest block a chunk can draw: a full-width line at scale 1
    # plus the style padding chunk_block_dims adds.
    rep_w = get_subtitle_max_width(video_width) + 2 * (style_bord + shadow_px + 8)
    rep_h = est_text_height + 2 * (
        (style_bord if border_style == 3 else outline_px) + shadow_px + 6
    )
    pref_frac = (
        float(preference_y)
        if preference_y is not None
        else float(template.get("position_y", 0.80))
    )
    pref_centre = get_safe_vertical_position(
        video_height, est_text_height, pref_frac
    )
    obstacles: List[Box] = []
    hard_obstacles: List[Box] = []
    for clip in clips:
        duration = float(clip.get("duration") or 0.0)
        zones = clip.get("face_zones") or []
        if zones and _coverage(
            [(zs, ze) for zs, ze, _boxes in zones], duration
        ) >= BAND_DOMINANT_COVERAGE:
            boxes = [box for _zs, _ze, zb in zones for box in zb]
            obstacles.extend(boxes)
            hard_obstacles.extend(boxes)
        strips = clip.get("fit_strips") or []
        if strips and _coverage(
            [(ss, se) for ss, se, _t, _b in strips], duration
        ) >= BAND_DOMINANT_COVERAGE:
            obstacles.extend(
                strip_band_boxes(strips, 0.0, duration, video_width, video_height)
            )
    if title_block is not None:
        # The task title obstacle, built exactly as the builder builds
        # the per-clip one: the block extended to the nearest frame
        # edge plus the minimum title/caption gap on the far side.
        _bx, _by, _bw, _bh = title_block
        if _by + _bh / 2 <= video_height / 2:
            obstacle = (_bx, 0.0, _bw, _by + _bh + TITLE_CAPTION_MIN_GAP_PX)
        else:
            obstacle = (
                _bx,
                _by - TITLE_CAPTION_MIN_GAP_PX,
                _bw,
                video_height - (_by - TITLE_CAPTION_MIN_GAP_PX),
            )
        obstacles.append(obstacle)
        hard_obstacles.append(obstacle)
    centre = _guarded_task_candidate(
        pref_centre,
        video_width // 2 - rep_w / 2,
        rep_w,
        rep_h,
        obstacles,
        hard_obstacles,
        video_width,
        video_height,
        top_limit=guard_top,
        bottom_limit=guard_bottom,
    )
    if centre is not None:
        caption_centre = round(centre / video_height, 4)

    return {
        "version": TEXT_BANDS_VERSION,
        "width": int(video_width),
        "height": int(video_height),
        "caption_centre": caption_centre,
        "title_bottom": title_bottom,
        "clips_considered": len(clips),
    }


def collect_clip_geometry(
    video_path: Path,
    ranges: Sequence[Tuple[float, float]],
    *,
    plain: bool,
    hook_title: Optional[str],
) -> Optional[Dict[str, Any]]:
    """One segment's guard geometry via the production reframe planner.

    Runs attach_reframe_plan on a one-segment mini-EDL over the
    segment's source ranges and collects the resolved zones/strips.
    Returns None when planning produced nothing (the render then has
    no guard geometry for this clip either).
    """
    clean = [(float(s), float(e)) for s, e in ranges if e > s]
    if not clean:
        return None
    duration = rendered_ranges_duration(clean)
    if duration <= 0:
        return None
    mini_edl: Dict[str, Any] = {
        "duration": duration,
        "segments": [{"start": 0.0, "end": duration}],
    }
    if not attach_reframe_plan(mini_edl, Path(video_path), clean, plain):
        return None
    if (mini_edl.get("reframe") or {}).get("delegate"):
        return None
    return {
        "duration": duration,
        "face_zones": collect_face_zones(mini_edl),
        "fit_strips": collect_fit_strips(mini_edl),
        "hook_title": hook_title,
    }


def decide_task_text_bands(
    video_path: Path,
    segment_ranges: Sequence[Tuple[Sequence[Tuple[float, float]], Optional[str]]],
    *,
    plain: bool,
    template: Dict[str, Any],
    font_size: Optional[int],
    video_width: int,
    video_height: int,
    preference_y: Optional[float] = None,
) -> Optional[Dict[str, Any]]:
    """Collect every segment's geometry and decide the task's bands.

    ``segment_ranges`` is (source ranges, hook title) per segment.
    Never raises: any failure returns None and the task renders with
    the per-clip system, exactly as before this lane.
    """
    try:
        clips: List[Dict[str, Any]] = []
        for ranges, hook_title in segment_ranges:
            geometry = collect_clip_geometry(
                video_path, ranges, plain=plain, hook_title=hook_title
            )
            if geometry is not None:
                clips.append(geometry)
        return decide_bands_from_geometry(
            clips,
            template=template,
            font_size=font_size,
            video_width=video_width,
            video_height=video_height,
            preference_y=preference_y,
        )
    except Exception:
        logger.warning(
            "Task text-band decision failed; rendering with per-clip bands",
            exc_info=True,
        )
        return None
