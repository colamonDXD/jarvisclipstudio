"""Composition measurement harness for the vertical-reframe quality work.

For each regression range of a source video this script:

* runs the REAL source analysis (YuNet face samples + scene context, via
  ``reframe_plan.get_source_analysis`` — cached in the source sidecar),
* records the BEFORE decision trace: the context layout plan exactly as
  the pre-fix planner consumed it (kind + reason per entry, including a
  whole-plan ``stable_context`` collapse), and
* builds the AFTER plan with ``reframe_plan.build_reframe_plan``,
* measures, per sampled frame, the subject size as a percentage of the
  1080x1920 OUTPUT frame under (a) the old blurred-fit geometry and
  (b) the new plan's layout geometry for the shot covering that frame.

Subject = union of the significant face boxes YuNet reported for that
sample (boxes covering >= 0.2% of the source frame). For split layouts
the per-face tile areas are summed (faces live in separate tiles).

It also writes before/after PNG frame grabs (mid-range timestamp) into
the frames directory: "before" is the blurred-pad fit composite, "after"
is the new layout (crop / pair crop / split stack / fit) composited the
same way the renderer does, at 540x960.

Aligned metric: for comparability with the permanent regression harness
(tools/quality-regression/run_regression.py, which measures REAL exported
MP4s), each range is also measured the harness's way — 6 evenly spaced
samples, before/after composites rendered at the full 1080x1920, YuNet
(FaceDetectorYN, score_threshold=0.6, the repo's own model file) run on
the composite, union-bbox % and largest-face % of the frame, averaged.
That is the "aligned" block in the output JSON; the per-sample geometry
numbers above remain as a supplementary full-coverage view.

Usage (cv2 venv):
    python backend/scripts/measure_composition.py \
        --source ~/workspace/qm-source/source.mp4 \
        --ranges 14:50-15:10,28:11-28:38,22:04-22:29,40:39-41:09,42:24-42:52 \
        --out /tmp/composition_measurements.json \
        --frames-dir ~/workspace/research_notes/quality-milestone/frames/composition
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import cv2  # noqa: E402
import numpy as np  # noqa: E402

from src.media import reframe_plan  # noqa: E402
from src.media.ffmpeg import output_offsets_for_ranges  # noqa: E402
from src.media.reframing import compute_vertical_crop_dims  # noqa: E402
from src.media.scene_context import build_context_layout_plan  # noqa: E402
import dataclasses  # noqa: E402

OUT_W, OUT_H = 1080, 1920
MIN_FACE_AREA_FRAC = 0.002


def parse_ts(text: str) -> float:
    parts = [float(p) for p in text.strip().split(":")]
    total = 0.0
    for part in parts:
        total = total * 60 + part
    return total


def union_box(boxes):
    if not boxes:
        return None
    x0 = min(b[0] for b in boxes)
    y0 = min(b[1] for b in boxes)
    x1 = max(b[0] + b[2] for b in boxes)
    y1 = max(b[1] + b[3] for b in boxes)
    return (x0, y0, x1 - x0, y1 - y0)


def significant_boxes(boxes, frame_area):
    return [b for b in boxes if b[2] * b[3] / frame_area >= MIN_FACE_AREA_FRAC]


def fit_area_pct(box, width, height):
    """Subject-bbox area % of the output frame under blurred-fit geometry."""
    if box is None:
        return 0.0
    scale = min(OUT_W / width, OUT_H / height)
    return (box[2] * scale) * (box[3] * scale) / (OUT_W * OUT_H) * 100.0


def crop_area_pct(box, crop_x, crop_y, crop_w, crop_h):
    """Visible subject area % under a crop window scaled to full output."""
    if box is None:
        return 0.0
    vx0 = max(box[0], crop_x)
    vy0 = max(box[1], crop_y)
    vx1 = min(box[0] + box[2], crop_x + crop_w)
    vy1 = min(box[1] + box[3], crop_y + crop_h)
    if vx1 <= vx0 or vy1 <= vy0:
        return 0.0
    scale = OUT_W / crop_w
    return (vx1 - vx0) * scale * (vy1 - vy0) * scale / (OUT_W * OUT_H) * 100.0


def split_area_pct(boxes, split):
    """Summed per-face area % under the stacked split layout (1080x960 tiles)."""
    total = 0.0
    tiles = [split["left"], split["right"]]
    for box in boxes:
        cx = box[0] + box[2] / 2
        tile = min(
            tiles,
            key=lambda t: abs((t["tile_x"] + t["tile_w"] / 2) - cx),
        )
        vx0 = max(box[0], tile["tile_x"])
        vy0 = max(box[1], tile["tile_y"])
        vx1 = min(box[0] + box[2], tile["tile_x"] + tile["tile_w"])
        vy1 = min(box[1] + box[3], tile["tile_y"] + tile["tile_h"])
        if vx1 <= vx0 or vy1 <= vy0:
            continue
        total += (
            (vx1 - vx0) * (OUT_W / tile["tile_w"])
            * (vy1 - vy0) * (960 / tile["tile_h"])
            / (OUT_W * OUT_H) * 100.0
        )
    return total


def crop_x_at(shot, t):
    """Interpolate the shot's crop x at compacted time t from its keys."""
    keys = []
    for piece in shot.get("pieces") or []:
        keys.extend((float(k[0]), float(k[1])) for k in piece.get("keys") or [])
    if not keys:
        return None
    keys.sort()
    if t <= keys[0][0]:
        return keys[0][1]
    if t >= keys[-1][0]:
        return keys[-1][1]
    for (t0, x0), (t1, x1) in zip(keys, keys[1:]):
        if t0 <= t <= t1:
            if t1 - t0 < 1e-9:
                return x1
            return x0 + (x1 - x0) * (t - t0) / (t1 - t0)
    return keys[-1][1]


def shot_at(shots, t):
    for shot in shots:
        if shot["start"] - 1e-6 <= t < shot["end"] + 1e-6:
            return shot
    return shots[-1] if shots else None


def new_area_pct(shot, boxes, union, crop_w, crop_h, width, height):
    if shot is None or union is None:
        return 0.0
    mode = shot["mode"]
    if mode in ("single", "pair", "static"):
        x = crop_x_at(shot, shot["_sample_t"])
        if x is None:
            return fit_area_pct(union, width, height)
        return crop_area_pct(union, x, int(shot.get("y", 0)), crop_w, crop_h)
    if mode == "split":
        return split_area_pct(boxes, shot["split"])
    # fit: honour a readable-fit zoom window when the plan carries one
    for win_start, win_end, win_crop_w, win_x in shot.get("fit_windows") or []:
        if win_start - 1e-6 <= shot["_sample_t"] < win_end + 1e-6 and win_crop_w:
            return crop_area_pct(union, int(win_x), 0, int(win_crop_w), height)
    return fit_area_pct(union, width, height)


def render_grab(frame, shot, crop_w, crop_h, canvas_w=OUT_W, canvas_h=OUT_H):
    """Composite one source frame the way each layout renders.

    Defaults to the full 1080x1920 output size (what the regression
    harness measures on real exports); pass 540x960 for grab thumbnails.
    """
    h, w = frame.shape[:2]
    if shot is not None and shot["mode"] in ("single", "pair", "static"):
        x = crop_x_at(shot, shot["_sample_t"])
        if x is not None:
            x = int(max(0, min(w - crop_w, x)))
            y = int(max(0, min(h - crop_h, int(shot.get("y", 0)))))
            crop = frame[y : y + crop_h, x : x + crop_w]
            return cv2.resize(crop, (canvas_w, canvas_h), interpolation=cv2.INTER_AREA)
    if shot is not None and shot["mode"] == "split":
        tiles = []
        for key in ("left", "right"):
            tile = shot["split"][key]
            crop = frame[
                tile["tile_y"] : tile["tile_y"] + tile["tile_h"],
                tile["tile_x"] : tile["tile_x"] + tile["tile_w"],
            ]
            tiles.append(cv2.resize(crop, (canvas_w, canvas_h // 2), interpolation=cv2.INTER_AREA))
        return np.vstack(tiles)
    # Blurred-pad fit: blurred full-frame backdrop + centred fitted foreground.
    bg = cv2.resize(frame, (canvas_w, canvas_h), interpolation=cv2.INTER_AREA)
    bg = cv2.GaussianBlur(bg, (0, 0), 18)
    # A readable-fit zoom window crops a full-height slice and scales it to
    # the canvas width, centred over the same blurred backdrop.
    if shot is not None and shot["mode"] == "fit":
        for win_start, win_end, win_crop_w, win_x in shot.get("fit_windows") or []:
            if win_crop_w and win_start - 1e-6 <= shot["_sample_t"] < win_end + 1e-6:
                slice_img = frame[:, int(win_x) : int(win_x) + int(win_crop_w)]
                fg_h = int(round(canvas_w * h / int(win_crop_w)))
                fg = cv2.resize(slice_img, (canvas_w, fg_h), interpolation=cv2.INTER_AREA)
                y0 = (canvas_h - fg_h) // 2
                src_y = max(0, -y0)
                dst_y = max(0, y0)
                copy_h = min(fg_h - src_y, canvas_h - dst_y)
                if copy_h > 0:
                    bg[dst_y : dst_y + copy_h, :] = fg[src_y : src_y + copy_h, :]
                return bg
    scale = min(canvas_w / w, canvas_h / h)
    fg = cv2.resize(frame, (int(w * scale), int(h * scale)), interpolation=cv2.INTER_AREA)
    y0 = (canvas_h - fg.shape[0]) // 2
    x0 = (canvas_w - fg.shape[1]) // 2
    bg[y0 : y0 + fg.shape[0], x0 : x0 + fg.shape[1]] = fg
    return bg


def extract_frame(source: Path, t: float):
    """One source frame as BGR ndarray via ffmpeg (cv2 cannot seek AV1)."""
    import subprocess
    import tempfile

    with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmp:
        tmp_path = tmp.name
    # AV1 seeking on a loaded machine can take minutes; retry once.
    for _attempt in range(2):
        try:
            proc = subprocess.run(
                [
                    "ffmpeg", "-v", "error", "-y", "-ss", f"{t:.3f}",
                    "-i", str(source), "-frames:v", "1", tmp_path,
                ],
                capture_output=True, timeout=600,
            )
        except subprocess.TimeoutExpired:
            continue
        if proc.returncode == 0:
            return cv2.imread(tmp_path)
    return None


def detect_pct(frame, detector):
    """Harness-aligned YuNet measurement on one rendered frame.

    Mirrors tools/quality-regression/run_regression.py measure_subject_pct:
    FaceDetectorYN with score_threshold=0.6, union bounding box and largest
    face as % of the frame area.
    """
    h, w = frame.shape[:2]
    detector.setInputSize((w, h))
    _rc, faces = detector.detect(frame)
    boxes = []
    if faces is not None:
        for f in faces:
            x, y, bw, bh = float(f[0]), float(f[1]), float(f[2]), float(f[3])
            boxes.append((x, y, x + bw, y + bh))
    if not boxes:
        return 0.0, 0.0, 0
    union = (max(b[2] for b in boxes) - min(b[0] for b in boxes)) * (
        max(b[3] for b in boxes) - min(b[1] for b in boxes)
    )
    largest = max((b[2] - b[0]) * (b[3] - b[1]) for b in boxes)
    return round(100.0 * union / (w * h), 2), round(100.0 * largest / (w * h), 2), len(boxes)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", required=True)
    parser.add_argument("--ranges", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--frames-dir", default=None)
    args = parser.parse_args()

    source = Path(args.source)
    ranges = []
    for chunk in args.ranges.split(","):
        start_text, end_text = chunk.split("-")
        ranges.append((parse_ts(start_text), parse_ts(end_text), chunk))

    frames_dir = Path(args.frames_dir) if args.frames_dir else None
    if frames_dir:
        frames_dir.mkdir(parents=True, exist_ok=True)

    # The repo's own YuNet model, driven exactly as the regression harness
    # drives it (score_threshold=0.6, no other tuning).
    model_path = (
        Path(__file__).resolve().parents[1]
        / "src" / "media" / "models" / "face_detection_yunet_2023mar.onnx"
    )
    detector = cv2.FaceDetectorYN.create(
        str(model_path), "", (320, 320), score_threshold=0.6
    )

    results = []
    for start, end, label in ranges:
        duration = end - start
        analysis = reframe_plan.get_source_analysis(source, [(start, end)])
        if analysis is None:
            results.append({"range": label, "error": "analysis_unavailable"})
            continue
        width, height = analysis["width"], analysis["height"]
        frame_area = float(width * height)
        crop_w, crop_h = compute_vertical_crop_dims(width, height)

        # BEFORE trace: the context plan as the pre-fix planner consumed it.
        offsets = output_offsets_for_ranges([(start, end)])
        compact_context = []
        for evidence in analysis["context"]:
            mapped = reframe_plan._map_source_time(evidence.time, [(start, end)], offsets)
            if mapped is not None:
                compact_context.append(dataclasses.replace(evidence, time=mapped))
        compact_context.sort(key=lambda e: e.time)
        compact_cuts = sorted(
            {
                round(m, 3)
                for c in analysis["cuts"]
                if (m := reframe_plan._map_source_time(c, [(start, end)], offsets))
                is not None and 0 < m < duration
            }
        )
        before_entries = build_context_layout_plan(
            compact_context, compact_cuts, duration, crop_w / width
        )

        plan = reframe_plan.build_reframe_plan(analysis, [(start, end)], duration)
        shots = (plan or {}).get("shots") or []

        samples_in = [
            (t, boxes) for t, boxes in analysis["samples"] if start <= t < end
        ]
        fit_pcts, new_pcts = [], []
        modes_seen = []
        for t, boxes in samples_in:
            sig = significant_boxes(boxes, frame_area)
            union = union_box(sig)
            fit_pcts.append(fit_area_pct(union, width, height))
            shot = shot_at(shots, t - start)
            if shot is not None:
                shot["_sample_t"] = t - start
                modes_seen.append(shot["mode"])
            new_pcts.append(
                new_area_pct(shot, sig, union, crop_w, crop_h, width, height)
            )

        def mean(values):
            return round(sum(values) / len(values), 3) if values else 0.0

        grab_paths = {}
        if frames_dir and samples_in:
            mid = start + duration / 2
            frame = extract_frame(source, mid)
            if frame is not None:
                shot = shot_at(shots, mid - start)
                if shot is not None:
                    shot["_sample_t"] = mid - start
                safe = label.replace(":", "").replace("-", "_")
                before_path = frames_dir / f"before_{safe}.png"
                after_path = frames_dir / f"after_{safe}.png"
                cv2.imwrite(
                    str(before_path),
                    render_grab(frame, None, crop_w, crop_h, 540, 960),
                )
                cv2.imwrite(
                    str(after_path),
                    render_grab(frame, shot, crop_w, crop_h, 540, 960),
                )
                grab_paths = {"before": str(before_path), "after": str(after_path)}

        # Harness-aligned metric: 6 evenly spaced samples, YuNet on the
        # full-res before/after composites (see module docstring).
        aligned: dict = {"before": {"samples": []}, "after": {"samples": []}}
        for i in range(6):
            rel = duration * (i + 0.5) / 6
            frame = extract_frame(source, start + rel)
            if frame is None:
                continue
            shot = shot_at(shots, rel)
            if shot is not None:
                shot["_sample_t"] = rel
            for side, img in (
                ("before", render_grab(frame, None, crop_w, crop_h)),
                ("after", render_grab(frame, shot, crop_w, crop_h)),
            ):
                union_pct, largest_pct, faces = detect_pct(img, detector)
                aligned[side]["samples"].append(
                    {
                        "t": round(rel, 2),
                        "faces": faces,
                        "union_pct": union_pct,
                        "largest_pct": largest_pct,
                    }
                )
        for side in ("before", "after"):
            side_samples = aligned[side]["samples"]
            if side_samples:
                aligned[side]["mean_union_faces_pct"] = round(
                    sum(s["union_pct"] for s in side_samples) / len(side_samples), 2
                )
                aligned[side]["mean_largest_face_pct"] = round(
                    sum(s["largest_pct"] for s in side_samples) / len(side_samples), 2
                )
            else:
                aligned[side]["mean_union_faces_pct"] = 0.0
                aligned[side]["mean_largest_face_pct"] = 0.0

        results.append(
            {
                "range": label,
                "source_start": start,
                "source_end": end,
                "width": width,
                "height": height,
                "crop_w": crop_w,
                "crop_h": crop_h,
                "samples": len(samples_in),
                "samples_with_subject": sum(1 for v in fit_pcts if v > 0),
                "before_context_plan": [
                    {
                        "start": round(e["start"], 2),
                        "end": round(e["end"], 2),
                        "kind": e["kind"],
                        "reason": e.get("reason", ""),
                    }
                    for e in before_entries
                ],
                "after_plan": [
                    {
                        "start": round(s["start"], 2),
                        "end": round(s["end"], 2),
                        "mode": s["mode"],
                        "reason": s.get("reason", ""),
                    }
                    for s in shots
                ],
                "aligned_harness_metric": aligned,
                "subject_area_pct_fit_mean": mean(fit_pcts),
                "subject_area_pct_new_mean": mean(new_pcts),
                "subject_area_pct_fit_max": round(max(fit_pcts), 3) if fit_pcts else 0.0,
                "subject_area_pct_new_max": round(max(new_pcts), 3) if new_pcts else 0.0,
                "frames": grab_paths,
            }
        )
        print(json.dumps(results[-1], indent=2))
        # Write incrementally: a late failure must not lose finished ranges.
        Path(args.out).write_text(json.dumps(results, indent=2), encoding="utf-8")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
