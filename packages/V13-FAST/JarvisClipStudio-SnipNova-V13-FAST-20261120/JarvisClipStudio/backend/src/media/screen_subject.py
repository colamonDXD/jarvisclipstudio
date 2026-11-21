"""Step 3 of the clip1 correction: when a screen carries the story, show it.

Root cause #1b of clip1.mp4 (handoff/quality-clip1/ROOT_CAUSE.md): the
thing being reacted to (a stage screen with the nominees, a monitor, a
meme on a window) only appears in wide shots, and the framing either shows
the whole 16:9 frame small (the screen's text unreadable) or crops to a
face (the screen cut away).

``SCREEN_SUBJECT`` (default off until the owner approves the corrected
render, Sanji Q15): for every FIT shot, a few frames are checked for a
large bright rectangle (a lit screen in a darker scene). When one is
found in most of the shot's frames, the shot gets a readable-fit window
(the same ``fit_windows`` mechanism ``plan_fit_windows`` uses, drawn by
the existing compositor) that frames the screen together with the
people in the shot, so both appear larger, and neither is cut away.
Shots without a screen, or where screen + people already span most of
the frame, are left untouched. Never replaces the screen with anything:
it only decides how much of the real frame to show.

When screen and person are too far apart for one window (a stage screen
on the left, the streamer on the right of a wide shot), the shot becomes
the existing stacked split layout: the whole screen in the top tile, the
main person (largest face not on the screen) in the bottom tile.

Limits (honest): detection is a brightness heuristic, not a screen
classifier: a lit window or wall can count; screens low in the frame
(a phone, a laptop) are skipped; "main person" is the largest face, which
in an audience can be someone near the camera rather than the streamer.
"""
from __future__ import annotations

import logging
import os
import subprocess
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

logger = logging.getLogger(__name__)

PROBE_WIDTH = 960
SAMPLES_PER_SHOT = 3
MIN_AREA_FRAC = 0.03
MAX_AREA_FRAC = 0.45
MIN_FILL = 0.45
MIN_ASPECT, MAX_ASPECT = 1.1, 3.2
MARGIN_FRAC = 0.04
MAX_UNION_FRAC = 0.80     # a window wider than this gains too little to bother
MIN_FACE_AREA_FRAC = 0.0015


def screen_subject_enabled() -> bool:
    return os.getenv("SCREEN_SUBJECT", "off").strip().lower() in ("on", "1", "true", "yes")


def find_bright_screen(frame_bgr: Any) -> Optional[Tuple[float, float, float, float]]:
    """(left, right, top, bottom) fractions of the largest lit rectangle, or None."""
    import cv2
    import numpy as np

    h, w = frame_bgr.shape[:2]
    gray = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2GRAY)
    threshold = max(100.0, float(np.percentile(gray, 80)))
    hsv = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2HSV)
    edges = cv2.Canny(gray, 60, 160)
    if float(np.percentile(gray, 50)) > 140:  # a bright scene overall: no lit-screen contrast
        return None
    mask = (gray >= threshold).astype(np.uint8) * 255
    kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (max(3, w // 60), max(3, h // 60)))
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, kernel)
    count, labels, stats, _ = cv2.connectedComponentsWithStats(mask, 8)
    best = None
    for index in range(1, count):
        x, y, bw, bh, area = stats[index]
        box_area = bw * bh
        if box_area < MIN_AREA_FRAC * w * h or box_area > MAX_AREA_FRAC * w * h:
            continue
        if area / max(1, box_area) < MIN_FILL:
            continue
        if (y + bh / 2) / h > 0.5:
            # Stage screens and monitors sit above the people in a wide
            # shot; a bright patch at chest height (a jacket panel) is
            # not a screen. Limit: a phone/laptop screen low in frame is missed.
            continue
        aspect = bw / max(1, bh)
        if not (MIN_ASPECT <= aspect <= MAX_ASPECT):
            continue
        # A screen SHOWS something: colour or detail inside. A white
        # jacket panel or a glare of stage lights is flat and colourless.
        inner = (slice(y, y + bh), slice(x, x + bw))
        saturation = float(hsv[inner][..., 1].mean())
        detail = float((edges[inner] > 0).mean())
        if saturation < 60 and detail < 0.05:
            continue
        if best is None or box_area > best[4]:
            best = (x, y, bw, bh, box_area)
    if best is None:
        return None
    x, y, bw, bh, _ = best
    return x / w, (x + bw) / w, y / h, (y + bh) / h


def _frame_at(source: Path, t: float) -> Optional[Any]:
    import numpy as np

    try:
        probe = subprocess.run(
            ["ffmpeg", "-v", "error", "-ss", f"{max(0.0, t):.3f}", "-i", str(source),
             "-frames:v", "1", "-vf", f"scale={PROBE_WIDTH}:-2", "-f", "rawvideo",
             "-pix_fmt", "bgr24", "pipe:1"],
            capture_output=True, timeout=60,
        )
    except Exception:
        return None
    data = probe.stdout
    if probe.returncode != 0 or not data:
        return None
    height = len(data) // (PROBE_WIDTH * 3)
    if height <= 0:
        return None
    return np.frombuffer(data[: PROBE_WIDTH * 3 * height], dtype=np.uint8).reshape(height, PROBE_WIDTH, 3)


def _face_boxes(frame: Any) -> List[Tuple[float, float, float, float]]:
    """(x, y, w, h) fractions of the main person's face and comparable ones."""
    try:
        from .face_detection import open_yunet_detector

        detector = open_yunet_detector()
        if detector is None:
            return []
        h, w = frame.shape[:2]
        boxes = [b for b in detector.detect(frame) if b[2] * b[3] >= MIN_FACE_AREA_FRAC * w * h]
        if not boxes:
            return []
        # The main person (largest face) and anyone comparable; the rest
        # of the audience is not who the shot is about.
        largest = max(b[2] * b[3] for b in boxes)
        return [(b[0] / w, b[1] / h, b[2] / w, b[3] / h) for b in boxes if b[2] * b[3] >= 0.3 * largest]
    except Exception:
        return []


def _faces(frame: Any) -> List[Tuple[float, float]]:
    return [(x, x + bw) for x, _y, bw, _bh in _face_boxes(frame)]


def _screen_tile(screen: Tuple[float, float, float, float], width: int, height: int) -> Dict[str, int]:
    """Top tile (1080x960 shape) holding the whole screen, in source px."""
    aspect = 1080.0 / 960.0
    left, right, top, bottom = screen
    sw, sh = (right - left) * width, (bottom - top) * height
    tile_w, tile_h = sw * 1.06, sh * 1.06
    if tile_w / tile_h > aspect:
        tile_h = tile_w / aspect
    else:
        tile_w = tile_h * aspect
    if tile_h > height:  # cannot be taller than the frame: trim width instead
        tile_h, tile_w = float(height), height * aspect
    if tile_w > width:
        tile_w, tile_h = float(width), width / aspect
    cx, cy = (left + right) / 2 * width, (top + bottom) / 2 * height
    x = min(max(0.0, cx - tile_w / 2), width - tile_w)
    y = min(max(0.0, cy - tile_h / 2), height - tile_h)
    even = lambda v: int(v) // 2 * 2
    return {"tile_x": even(x), "tile_y": even(y), "tile_w": even(tile_w), "tile_h": even(tile_h)}


RUN_SAMPLE_SEC = 1.5


def clip_to_source(t: float, ranges: Sequence[Tuple[float, float]]) -> float:
    """Clip-timeline seconds -> source seconds through the keep ranges."""
    for start, end in ranges:
        length = end - start
        if t <= length:
            return start + max(0.0, t)
        t -= length
    return ranges[-1][1] if ranges else t


def _sample(source: Path, t_source: float) -> Tuple[Optional[Tuple[float, float, float, float]], List[Tuple[float, float, float, float]]]:
    frame = _frame_at(source, t_source)
    if frame is None:
        return None, []
    found = find_bright_screen(frame)
    if not found:
        return None, []
    # Faces on the screen are its content, not the reactor.
    faces = [b for b in _face_boxes(frame)
             if not (found[0] <= b[0] + b[2] / 2 <= found[1] and found[2] <= b[1] + b[3] / 2 <= found[3])]
    return found, faces


def decide(screens: Sequence[Tuple[float, float, float, float]],
           faces: Sequence[Tuple[float, float, float, float]],
           src_width: int, src_height: int) -> Optional[Dict[str, Any]]:
    """Window or stack for a run of samples that showed a screen, or None."""
    if len(screens) < 2 or src_width <= 0 or src_height <= 0:
        return None
    ordered = sorted(screens, key=lambda b: (b[1] - b[0]) * (b[3] - b[2]))
    screen = ordered[len(ordered) // 2]  # median-sized detection
    left, right = screen[0], screen[1]
    for f in faces:
        left, right = min(left, f[0]), max(right, f[0] + f[2])
    left, right = max(0.0, left - MARGIN_FRAC), min(1.0, right + MARGIN_FRAC)
    min_frac = (1080.0 * src_height / 1920.0) / src_width
    span = max(right - left, min_frac)
    info = {"screen": [round(float(v), 3) for v in screen], "faces": len(faces), "samples": len(screens)}
    if span <= MAX_UNION_FRAC:
        crop_w = int(round(span * src_width)) // 2 * 2
        center = (left + right) / 2 * src_width
        x = int(round(min(max(0.0, center - crop_w / 2), src_width - crop_w)))
        return {"mode": "fit_window", "crop_w": crop_w, "x": x, **info}
    if not faces:
        return None
    # Screen and person too far apart for one window: stack them, screen
    # on top (readable), the main person below.
    from .reframe_plan import _split_region

    main = max(faces, key=lambda b: b[2] * b[3])
    face_px = (main[0] * src_width, main[1] * src_height, main[2] * src_width, main[3] * src_height)
    return {"mode": "stack", "top": _screen_tile(screen, src_width, src_height),
            "bottom": _split_region(face_px, src_width, src_height), **info}


def screen_runs(source: Path, ranges: Sequence[Tuple[float, float]], duration: float,
                src_width: int, src_height: int) -> List[Dict[str, Any]]:
    """[{start, end, decision}] on the clip timeline where a screen is shown."""
    times = [RUN_SAMPLE_SEC / 2 + k * RUN_SAMPLE_SEC for k in range(max(1, int(duration / RUN_SAMPLE_SEC)))]
    hits = [(t, *_sample(source, clip_to_source(t, ranges))) for t in times]
    runs: List[Dict[str, Any]] = []
    current: Optional[Dict[str, Any]] = None
    misses = 0
    for t, screen, faces in hits:
        if screen is not None:
            if current is None:
                current = {"start": t, "end": t, "screens": [], "faces": []}
            current["end"] = t
            current["screens"].append(screen)
            current["faces"].extend(faces)
            misses = 0
        elif current is not None:
            misses += 1
            if misses > 1:  # one missed sample does not end a run
                runs.append(current)
                current, misses = None, 0
    if current is not None:
        runs.append(current)
    # Runs a few samples apart are one screen moment (a reaction cutaway,
    # a missed detection): merge them so one shot is never split by a gap.
    merged: List[Dict[str, Any]] = []
    for run in runs:
        if merged and run["start"] - merged[-1]["end"] <= 3 * RUN_SAMPLE_SEC:
            merged[-1]["end"] = run["end"]
            merged[-1]["screens"].extend(run["screens"])
            merged[-1]["faces"].extend(run["faces"])
        else:
            merged.append(run)
    runs = merged
    out = []
    for run in runs:
        decision = decide(run["screens"], run["faces"], src_width, src_height)
        if decision is not None:
            out.append({"start": max(0.0, run["start"] - RUN_SAMPLE_SEC / 2),
                        "end": min(duration, run["end"] + RUN_SAMPLE_SEC / 2), "decision": decision})
    return out


def _split_fit_shots_at(plan: Dict[str, Any], edges: Sequence[float]) -> None:
    """Cut fit shots at run edges so a run maps onto whole shots."""
    out: List[Dict[str, Any]] = []
    for shot in plan.get("shots") or []:
        if shot.get("mode") != "fit" or shot.get("fit_windows"):
            out.append(shot)
            continue
        cuts = [e for e in edges if float(shot["start"]) + 0.5 < e < float(shot["end"]) - 0.5]
        bounds = [float(shot["start"])] + sorted(cuts) + [float(shot["end"])]
        for a, b in zip(bounds, bounds[1:]):
            out.append({**shot, "start": a, "end": b})
    plan["shots"] = out


def apply_screen_windows(plan: Dict[str, Any], source: Path,
                         ranges: Optional[Sequence[Tuple[float, float]]] = None,
                         all_modes: bool = False,
                         runs: Optional[List[Dict[str, Any]]] = None) -> int:
    """Frame every shot inside a screen run; returns how many shots changed.

    Shots and runs are on the CLIP timeline (``ranges`` maps them to the
    source). Fit shots always qualify (and are cut at run edges first);
    with ``all_modes`` a face-crop shot mostly inside a run qualifies too
    (the crop would cut the screen away). Existing split shots
    (two-person stacks) are left alone.
    """
    width, height = int(plan.get("width") or 0), int(plan.get("height") or 0)
    shots = plan.get("shots") or []
    if not shots:
        return 0
    duration = max(float(sh["end"]) for sh in shots)
    if runs is None:
        runs = screen_runs(source, list(ranges or [(0.0, duration)]), duration, width, height)
    if not runs:
        return 0
    _split_fit_shots_at(plan, [edge for r in runs for edge in (r["start"], r["end"])])
    applied = 0
    for shot in plan["shots"]:
        mode = shot.get("mode")
        if mode == "split" or shot.get("fit_windows"):
            continue
        if mode != "fit" and not all_modes:
            continue
        start, end = float(shot["start"]), float(shot["end"])
        overlaps = [(min(end, r["end"]) - max(start, r["start"]), r) for r in runs]
        overlaps = [(o, r) for o, r in overlaps if o > 0]
        if not overlaps:
            continue
        best_overlap, run = max(overlaps, key=lambda item: item[0])
        if best_overlap < 0.5 * (end - start):
            continue  # the screen is only a small part of this shot
        decision = run["decision"]
        if decision["mode"] == "fit_window":
            shot["mode"] = "fit"
            shot["reason"] = "screen_subject_fit"
            shot["fit_windows"] = [[start, end, decision["crop_w"], decision["x"]]]
        else:
            shot["mode"] = "split"
            shot["reason"] = "screen_subject_stack"
            shot["split"] = {"left": dict(decision["top"]), "right": dict(decision["bottom"])}
        shot["screen_subject"] = decision
        applied += 1
    if applied:
        logger.info("Screen subject: %d shot(s) framed in %d screen run(s): %s", applied, len(runs),
                    [(round(r["start"], 1), round(r["end"], 1), r["decision"]["mode"]) for r in runs])
    return applied
