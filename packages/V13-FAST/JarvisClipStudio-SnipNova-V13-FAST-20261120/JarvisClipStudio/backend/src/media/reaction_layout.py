"""Separate a persistent reaction webcam from the changing source video.

Unlike speaker overlap, this is a composition decision and needs no diarization.
Require a stable corner face, a measurable inset boundary, and evidence that
the source area is a real video: either faces in the source area across the
clip (a reactor watching people) or sustained visual change outside the
inset (a streamer playing a game, whose source has no faces at all). A corner
face over a static, faceless source is not a reaction inset. Otherwise leave
the existing podcast, call, and content layouts in charge.
"""

import logging
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

import cv2
import numpy as np

from .common import UPSCALE_SHARPEN_FILTER
from .ffmpeg import clamp_even, round_to_even

logger = logging.getLogger(__name__)

Box = Tuple[float, float, float, float]
FaceFrame = Tuple[float, Sequence[Box]]
REACTOR_HEIGHT = 640
SOURCE_HEIGHT = 1280


def _inside(face: Box, region: Box) -> bool:
    x, y, w, h = region
    fx, fy, fw, fh = face
    return x <= fx + fw / 2 <= x + w and y <= fy + fh / 2 <= y + h


def _crop_inside(w: int, h: int, x: int, y: int, width: int, height: int) -> bool:
    return w >= 2 and h >= 2 and x >= 0 and y >= 0 and x + w <= width and y + h <= height


def _corner_candidates(
    width: int, height: int, samples: Sequence[FaceFrame],
) -> List[Tuple[Box, bool]]:
    """Stable upper-corner faces, per side, with their source-face evidence.

    The second element is the original gate's source-area condition: faces
    below the corner in at least 30% of samples. Corner geometry and
    stability are unchanged; candidates come right side first, as before.
    """
    if len(samples) < 8:
        return []
    candidates: List[Tuple[Box, bool]] = []
    for right in (True, False):
        found = []
        other_count = 0
        for _, faces in samples:
            corner = [f for f in faces if
                      (f[0] + f[2] / 2 > width * .76 if right else f[0] + f[2] / 2 < width * .24)
                      and f[1] + f[3] / 2 < height * .25
                      and .001 < f[2] * f[3] / (width * height) < .025]
            if len(corner) != 1:
                continue
            found.append(corner[0])
            other_count += any(f not in corner and f[1] + f[3] / 2 > height * .3 for f in faces)
        if len(found) < len(samples) * .75:
            continue
        boxes = np.asarray(found)
        centres = boxes[:, :2] + boxes[:, 2:] / 2
        # The centre bars measure the FACE's wander inside the inset, not
        # the inset's own stillness — the measured inset edges are that
        # proof (measure_reaction_inset). A real facecam face at analysis
        # scale wanders with centre-x std ~1.65% of frame width (streamer
        # fixture, pipeline samples); a face travelling across the corner
        # measures ~4%+. The 2% bar sits between the two.
        if (np.std(centres[:, 0]) > width * .02
                or np.std(centres[:, 1]) > height * .025
                or np.std(boxes[:, 2]) > np.median(boxes[:, 2]) * .25):
            continue
        candidates.append((
            tuple(float(v) for v in np.median(boxes, axis=0)),
            other_count >= len(samples) * .3,
        ))
    return candidates


def find_reactor_face(width: int, height: int, samples: Sequence[FaceFrame]) -> Optional[Box]:
    """Find a small, stationary upper-corner face, supported across the clip.

    Requires the source-face evidence as well: this is the strict gate the
    layout decision and the detail pass have always used.
    """
    for face, source_faces in _corner_candidates(width, height, samples):
        if source_faces:
            return face
    return None


def find_reactor_corner(width: int, height: int, samples: Sequence[FaceFrame]) -> Optional[Box]:
    """The corner face alone, without the source-area evidence.

    Routing-level only: the reframe plan uses it to delegate a clip to the
    plain layout path, where choose_reaction_layout makes the final call
    with pixel evidence. Never use it to compose a layout directly.
    """
    candidates = _corner_candidates(width, height, samples)
    return candidates[0][0] if candidates else None


def _source_area_changes(
    path: Path, width: int, height: int, duration: float, inset: Box,
) -> bool:
    """True when the frame area outside the inset keeps changing.

    Median blurred frame difference outside the inset, over sampled
    half-second pairs. Calibration on the streamer fixture (gameplay under
    a live facecam): ~1.65 grey levels; the same live inset over a frozen
    source: 0.0. The 0.5 bar sits far from both. Whole-frame motion stats
    cannot make this call — the reactor's own movement inflates them
    (median 0.032 on the frozen-source control), so the inset itself is
    excluded here. Fewer than four readable pairs is not evidence.
    """
    ix, iy, iw, ih = (int(v) for v in inset)
    cap = cv2.VideoCapture(str(path))
    diffs = []
    try:
        for t in np.linspace(0, max(0., duration - .7), 6):
            pair = []
            for offset in (0., .5):
                cap.set(cv2.CAP_PROP_POS_MSEC, float(t + offset) * 1000)
                ok, frame = cap.read()
                if ok and frame.shape[:2] == (height, width):
                    pair.append(cv2.GaussianBlur(
                        cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY), (7, 7), 0))
            if len(pair) == 2:
                diff = np.abs(pair[1].astype(np.float32) - pair[0].astype(np.float32))
                outside = np.ones(diff.shape, dtype=bool)
                outside[max(0, iy):iy + ih, max(0, ix):ix + iw] = False
                diffs.append(float(diff[outside].mean()))
    finally:
        cap.release()
    return len(diffs) >= 4 and float(np.median(diffs)) >= .5


def _edge_peak(values: np.ndarray, start: int, end: int) -> Optional[int]:
    start, end = max(0, start), min(len(values), end)
    if end - start < 4:
        return None
    region = values[start:end]
    index = int(np.argmax(region))
    if region[index] < max(10., float(np.median(region)) * 2.5):
        return None
    return start + index + 1


def _source_pair(faces: Sequence[Box], width: int) -> Optional[List[Box]]:
    """Two repeatedly observed source positions in this wide section only."""
    groups: List[List[Box]] = []
    for face in sorted(faces, key=lambda f: f[0] + f[2] / 2):
        cx = face[0] + face[2] / 2
        if not groups or cx - (groups[-1][-1][0] + groups[-1][-1][2] / 2) > width * .1:
            groups.append([face])
        else:
            groups[-1].append(face)
    stable = [g for g in groups if len(g) >= max(2, len(faces) * .1)]
    if len(stable) != 2:
        return None
    pair = [tuple(float(v) for v in np.median(g, axis=0)) for g in stable]
    if pair[1][0] - pair[0][0] < width * .25 or min(f[3] for f in pair) < max(f[3] for f in pair) * .5:
        return None
    return pair


def measure_reaction_inset(
    path: Path, width: int, height: int, duration: float, face: Box,
    times: Optional[Sequence[float]] = None,
) -> Optional[Box]:
    """Locate the two inner edges of a top-corner camera rectangle.

    Median edge contrast over several times suppresses moving faces, chat,
    and cuts in the underlying source. No video-specific pixel positions are
    supplied by the caller. ``times`` (seconds in ``path``) overrides the
    default six evenly spaced reads over ``[0, duration]`` -- the reframe
    planner passes times inside the clip's ranges of the full source.
    """
    cap = cv2.VideoCapture(str(path))
    frames = []
    sample_times = (
        list(times) if times else np.linspace(0, max(0., duration - .2), 6)
    )
    try:
        for t in sample_times:
            cap.set(cv2.CAP_PROP_POS_MSEC, float(t) * 1000)
            ok, frame = cap.read()
            if ok and frame.shape[:2] == (height, width):
                frames.append(frame.astype(np.float32))
    finally:
        cap.release()
    if len(frames) < 4:
        return None
    fx, fy, fw, fh = face
    cx, cy = fx + fw / 2, fy + fh / 2
    right = cx > width / 2
    edge_height = min(height, int(cy + fh))
    vertical = np.median([
        np.abs(np.diff(f[:edge_height], axis=1)).mean(axis=(0, 2)) for f in frames
    ], axis=0)
    a, b = (cx - fw * 4.5, cx - fw * 1.3) if right else (cx + fw * 1.3, cx + fw * 4.5)
    inner_x = _edge_peak(vertical, int(a), int(b))
    if inner_x is None:
        return None
    left, crop_w = (inner_x, width - inner_x) if right else (0, inner_x)
    horizontal = np.median([
        np.abs(np.diff(f[:, left:left + crop_w], axis=0)).mean(axis=(1, 2)) for f in frames
    ], axis=0)
    bottom = _edge_peak(horizontal, int(cy + fh * .8), int(cy + fh * 3))
    if bottom is None or not (.14 * width <= crop_w <= .45 * width and .15 * height <= bottom <= .5 * height):
        return None
    region = (float(left), 0., float(crop_w), float(bottom))
    if not _inside(face, region) or fy + fh > bottom or fx < left or fx + fw > left + crop_w:
        return None
    return region


def build_reaction_layout(
    width: int, height: int, duration: float, face: Box, inset: Box,
    samples: Sequence[FaceFrame], scene_cuts: Sequence[float] = (),
) -> Dict[str, Any]:
    """Reactor above source; source closeups follow faces outside the inset.

    Wide shots with two stable source faces use two guest panels, for three
    faces total. Uncertain and face-free shots keep a wide source view.
    The source never includes the original inset a second time.
    """
    ix, iy, iw, ih = inset
    # The camera's lower edge. Insets measured by measure_reaction_inset start
    # at the top (iy == 0, so this is ih); a facecam window found by
    # facecam_rect can float half-way down a side.
    inset_bottom = iy + ih
    source_samples =[(t, [f for f in faces if not _inside(f, inset)]) for t, faces in samples]
    # A closeup is large enough to crop independently. Sparse/absent face
    # detections in a wide shot must not pull the crop onto the plant or inset.
    observations = []
    for t, faces in source_samples:
        main = max(faces, key=lambda f: f[2] * f[3]) if faces else None
        observations.append((t, main, faces))
    states = []
    close = False
    for t, main, faces in observations:
        shot = sum(cut <= t for cut in scene_cuts)
        if main is None:
            nearby = [(abs(t-other_t), other, others) for other_t, other, others in observations
                      if other is not None and abs(t-other_t) <= 2
                      and sum(cut <= other_t for cut in scene_cuts) == shot]
            if nearby:
                _, main, faces = min(nearby, key=lambda item: item[0])
        if main is None:
            mode = 'content'
        else:
            if main[3] >= height * .18:
                close = True
            elif main[3] < height * .13:
                close = False
            comparable = [f for f in faces if f[2]*f[3] >= main[2]*main[3]*.35]
            mode = 'close' if close and len(comparable) == 1 else 'wide'
        states.append((t, mode, main, faces))
    runs: List[List[Any]] = []
    for index, (t, mode, main, faces) in enumerate(states):
        end = states[index + 1][0] if index + 1 < len(states) else duration
        selected = list(faces) if mode == 'wide' else [main] if main else []
        at_cut = index > 0 and any(states[index-1][0] < cut <= t for cut in scene_cuts)
        if runs and runs[-1][2] == mode and not at_cut:
            runs[-1][1] = end
            runs[-1][3].extend(selected)
        else:
            runs.append([t, end, mode, selected])

    # Keep the reactor's head and shoulders, while excluding the source video.
    rw = round_to_even(int(iw))
    rh = round_to_even(int(min(ih, rw * REACTOR_HEIGHT / 1080)))
    rw = round_to_even(int(min(rw, rh * 1080 / REACTOR_HEIGHT)))
    rx = clamp_even(int(face[0] + face[2] / 2 - rw / 2), int(ix), int(ix + iw - rw))
    ry = clamp_even(int(face[1] + face[3] / 2 - rh * .44), int(iy), int(iy + ih - rh))
    # Remove the inset from the source background too, avoiding a ghost copy.
    graph = [
        '[0:v]split=3[reactor_src][source_bg][source_src]',
        f'[reactor_src]crop={rw}:{rh}:{rx}:{ry},scale=1080:{REACTOR_HEIGHT}:flags=lanczos,'
        f'{UPSCALE_SHARPEN_FILTER},setsar=1[reactor]',
        f'[source_bg]drawbox=x={int(ix)}:y={int(iy)}:w={int(iw)}:h={int(ih)}:color=black:t=fill,'
        'scale=540:640:force_original_aspect_ratio=increase,crop=540:640,'
        f'gblur=sigma=18:steps=2,scale=1080:{SOURCE_HEIGHT},setsar=1[base0]',
        '[source_src]split=' + str(len(runs)) + ''.join(f'[part{i}]' for i in range(len(runs))),
    ]
    # The space beside the inset contains a full-height, unobstructed closeup.
    safe_x, safe_w = (0, int(ix)) if ix > width / 2 else (int(ix + iw), width - int(ix + iw))
    for i, (start, end, mode, faces) in enumerate(runs):
        pair = _source_pair(faces, width) if mode == 'wide' else None
        guest_crops = []
        if pair and all(f[1] >= inset_bottom for f in pair):
            # A 9:8 panel wider than the frame cannot be placed. Two copies of
            # the full frame are not a split; the wide crop below shows both.
            y_min = round_to_even(int(inset_bottom))
            for guest in pair:
                gh = round_to_even(int(min(max(0, height-inset_bottom), max(guest[3]*3.2, guest[2]*3*640/1080))))
                gw = round_to_even(int(gh * 1080/640))
                if gw > width or y_min + gh > height:
                    guest_crops = []
                    break
                gx = clamp_even(int(guest[0]+guest[2]/2-gw/2), 0, width-gw)
                gy = clamp_even(int(guest[1]+guest[3]/2-gh*.42), y_min, height-gh)
                if not _crop_inside(gw, gh, gx, gy, width, height):
                    guest_crops = []
                    break
                guest_crops.append((gw, gh, gx, gy))
        if len(guest_crops) == 2 and guest_crops[0] != guest_crops[1]:
            graph.append(f'[part{i}]split=2[guest{i}a][guest{i}b]')
            for label, (gw, gh, gx, gy) in zip(('a', 'b'), guest_crops):
                graph.append(f'[guest{i}{label}]crop={gw}:{gh}:{gx}:{gy},scale=1080:640:flags=lanczos,setsar=1[g{i}{label}]')
            graph.append(f'[g{i}a][g{i}b]vstack=inputs=2[p{i}]')
            enable = f'gte(t\\,{start:.3f})*lt(t\\,{end:.3f})'
            graph.append(f"[base{i}][p{i}]overlay=0:0:enable='{enable}'[base{i+1}]")
            continue
        if mode == 'close':
            cx = float(np.median([f[0] + f[2] / 2 for f in faces]))
            if safe_x <= cx <= safe_x + safe_w:
                cw = round_to_even(int(min(safe_w, height * 1080 / SOURCE_HEIGHT)))
                ch = round_to_even(int(cw * SOURCE_HEIGHT / 1080))
                x = clamp_even(int(cx - cw / 2), safe_x, max(safe_x, safe_x + safe_w - cw))
                cy = float(np.median([f[1] + f[3] / 2 for f in faces]))
                y = clamp_even(int(cy-ch*.4), 0, max(0, height-ch))
            else:
                # A subject below the inset can live on the same side as the
                # reactor. Crop below the camera rather than to an empty wall.
                # Tall sources make the 9:16 window wider than the frame;
                # shrink it instead of handing ffmpeg a crop past the edge.
                y = round_to_even(int(inset_bottom))
                ch = round_to_even(max(0, height-y))
                cw = round_to_even(int(ch * 1080 / SOURCE_HEIGHT))
                if cw > width:
                    cw = round_to_even(width)
                    ch = round_to_even(int(min(max(0, height-y), cw * SOURCE_HEIGHT / 1080)))
                x = clamp_even(int(cx - cw / 2), 0, max(0, width-cw))
            if _crop_inside(cw, ch, x, y, width, height):
                crop = f'crop={cw}:{ch}:{x}:{y},scale=1080:{SOURCE_HEIGHT}:flags=lanczos'
            else:
                y = min(round_to_even(int(max(inset_bottom, 0))), round_to_even(max(0, height - 2)))
                crop = (
                    f'crop={round_to_even(width)}:{round_to_even(height - y)}:0:{y},'
                    f'scale=1080:{SOURCE_HEIGHT}:force_original_aspect_ratio=decrease:flags=lanczos'
                )
        elif mode == 'wide' and all(f[1] >= inset_bottom for f in faces):
            # Both guests in a wide shot sit below the upper-corner camera.
            y = round_to_even(int(inset_bottom))
            crop = f'crop={round_to_even(width)}:{round_to_even(height-y)}:0:{y},scale=1080:{SOURCE_HEIGHT}:force_original_aspect_ratio=decrease:flags=lanczos'
        else:
            # Keep source text and slides in full. The webcam already covers
            # this rectangle in the original; mask it instead of duplicating it.
            crop = (f'format=rgba,drawbox=x={int(ix)}:y={int(iy)}:w={int(iw)}:h={int(ih)}:color=black@0:t=fill:replace=1,'
                    f'scale=1080:{SOURCE_HEIGHT}:force_original_aspect_ratio=decrease:flags=lanczos')
        graph.append(f'[part{i}]{crop},setsar=1[p{i}]')
        enable = f'gte(t\\,{start:.3f})*lt(t\\,{end:.3f})'
        graph.append(f"[base{i}][p{i}]overlay=(W-w)/2:(H-h)/2:enable='{enable}'[base{i+1}]")
    graph.append(f'[reactor][base{len(runs)}]vstack=inputs=2[vout]')
    return {'kind': 'reaction', 'video_filter': ';'.join(graph), 'caption_y': REACTOR_HEIGHT / 1920,
            'inset': inset, 'source_runs': runs, 'upscale': 1080 / rw}


def choose_reaction_layout(
    path: Path, width: int, height: int, duration: float, samples: Sequence[FaceFrame],
    scene_cuts: Sequence[float] = (),
    layout_template: Optional[str] = None,
    layout_credit: Optional[str] = None,
    peak_time: Optional[float] = None,
    niche: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    """The reaction layout for a clip with a facecam, or None.

    ``layout_template`` (``media.gaming_templates``: ``facecam_red`` /
    ``facecam_gold``) swaps the default reactor-above-source layout for the
    "face on top, game below" stack with its divider and credit line.

    The composed gate (Sanji x Phase 2): the corner-face branches engage
    only on engagement evidence — faces in the source area, or the
    source area visibly changing outside the inset
    (``_source_area_changes``). When the face-first check misses
    entirely, the camera window is located by its persistent border
    (``media.facecam_rect``, FACECAM_RECT_DETECT); that detector's own
    face-inside verification is the engagement evidence for its branch.
    """
    def _sequenced(face: Box, inset: Box) -> Optional[Dict[str, Any]]:
        """The sequenced stack when VIEW_SEQUENCING applies, else None.

        Gaming/streamer niches only, and hand-drawn boxes outrank it
        entirely (the manual layout is the user's own composition; it
        never reaches this branch from the render path, which returns
        it earlier — the check keeps direct callers honest too).
        """
        from . import view_sequencing as vs
        from .gaming_templates import MANUAL_BOXES

        if not vs.view_sequencing_enabled() or not vs.niche_is_sequenced(niche):
            return None
        if MANUAL_BOXES.get() is not None:
            return None
        peak = peak_time if peak_time is not None else vs.loudest_reaction_second(path)
        segments = vs.plan_view_segments(duration, peak)
        if not segments:
            return None
        from .gaming_templates import build_sequenced_stack_layout

        layout = build_sequenced_stack_layout(
            width, height, duration, face, inset, layout_template, layout_credit, segments
        )
        logger.info(
            "View sequencing (%s): peak %.2fs, timeline %s",
            layout_template, peak, segments,
        )
        return layout

    def _build(face: Box, inset: Box) -> Dict[str, Any]:
        if layout_template:
            from .gaming_templates import LAYOUT_TEMPLATES, build_facecam_stack_layout

            if layout_template in LAYOUT_TEMPLATES:
                return _sequenced(face, inset) or build_facecam_stack_layout(
                    width, height, duration, face, inset, layout_template, layout_credit
                )
        return build_reaction_layout(width, height, duration, face, inset, samples, scene_cuts)

    candidates = _corner_candidates(width, height, samples)
    # The original path first: a corner face with faces in the source area
    # composes exactly as it always has, no further evidence needed.
    for face, source_faces in candidates:
        if source_faces:
            inset = measure_reaction_inset(path, width, height, duration, face)
            if inset is None:
                return None
            return _build(face, inset)
    # Faceless source (gameplay under a facecam): the corner face composes
    # only if the source area itself is visibly changing. A static source
    # under a corner face — a portrait overlay, a frozen frame — is not a
    # reaction inset, so the existing layouts stay in charge.
    if candidates:
        face = candidates[0][0]
        inset = measure_reaction_inset(path, width, height, duration, face)
        if inset is not None:
            if not _source_area_changes(path, width, height, duration, inset):
                return None
            return _build(face, inset)
        # The face is there but its rectangle cannot be measured from it:
        # fall through to the border-based window detection below.
    # Face-first check missed (small facecam face, a camera half-way down
    # a side, no corner face at all): look for the camera window itself.
    # The detector is its own engagement evidence: it only returns a
    # window with a persistent border AND a face verified inside it in
    # most probed frames, with no second fixed camera — so a found
    # window composes. (A lecture's presenter, whom the analysis pass
    # does see, never reaches this branch: the corner-face path above
    # applies the source-change check and keeps it on the plain fit.)
    from .facecam_rect import find_facecam_rect, probe_times

    found = find_facecam_rect(path, width, height, probe_times([(0.0, duration)]))
    if found is None:
        return None
    face, inset = found.face, found.rect
    return _build(face, inset)
