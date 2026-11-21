"""Caption/title face guard — RENDER-level proof.

Production pair throughout: ``build_assemblyai_ass_subtitles`` for the
ASS and ``render_edl_clip_ffmpeg`` (the EDL renderer) for the pixels,
on EDLs produced by the real ``attach_reframe_plan``. The BEFORE
renders are the same pipeline with the plan's face boxes withheld
from the caption builder (the pre-fix behaviour); the AFTER renders
pass ``collect_face_zones(edl)`` exactly as ``create_optimized_clip``
now does.

Measurement: face boxes are the plan's stamped output boxes (their
transform is validated against the white face-centre markers painted
into the synthetic sources, the split lane's method), text boxes come
from tesseract OCR over the rendered frames, and the invariant is
intersection area == 0 for every sampled frame after the fix (and
> 0 before it, proving the measurement can see the defect). The
Japan test runs the same flow on real footage with real YuNet
detection on the output frames.

Before/after frames are also written to the lane's proof directory
(~/workspace/.tmp-facecaption/render-proof) for the report.
"""

from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

import numpy as np
import pytest

from src.caption_templates import get_template
from src.media import captions, edl_render, reframe_plan
from src.media.captions import ass_fonts_dir, build_assemblyai_ass_subtitles
from src.media.face_text_guard import intersection_area
from src.media.reframe_plan import collect_face_zones
from src.media.scene_context import SceneEvidence

W, H = 1920, 1080
LEFT = (200.0, 300.0, 180.0, 220.0)
RIGHT = (1300.0, 300.0, 180.0, 220.0)
DURATION = 6.0
TITLE = "Assistant Buys Peaky Blinders Outfit For Pubs"
TITLE_WORDS = {w.upper() for w in TITLE.split()}
SPOKEN = ["alpha", "bravo", "charlie", "delta", "echo", "foxtrot"]
SPOKEN_WORDS = {w.upper() for w in SPOKEN}

PROOF_DIR = (
    Path(__file__).resolve().parents[4] / ".tmp-facecaption" / "render-proof"
)
JAPAN_SOURCE = Path.home() / "workspace" / "qm-source" / "source.mp4"
JAPAN_CACHE = JAPAN_SOURCE.with_name("source.transcript_cache.json")


# --- Fabrication (the split lane's seam and sources) ---------------------------


def _analysis(rows, evidence_kwargs, fps=3.0):
    return {
        "width": W,
        "height": H,
        "samples": [(i / fps, list(boxes)) for i, boxes in enumerate(rows)],
        "cuts": [],
        "context": [
            SceneEvidence(time=i / fps, **evidence_kwargs)
            for i in range(len(rows))
        ],
    }


def _two_face_analysis():
    return _analysis(
        [[LEFT, RIGHT]] * 18,
        dict(face_area=0.0175, face_count=2, face_span=0.667),
    )


def _one_face_analysis():
    return _analysis(
        [[LEFT]] * 18,
        dict(face_area=0.0175, face_count=1, face_span=0.094, face_center=0.151),
    )


def _edl(duration=DURATION):
    return {
        "clip_id": "faceguard-render",
        "duration": duration,
        "source_ranges": [[0.0, duration]],
        "segments": [
            {
                "index": 0,
                "start": 0.0,
                "end": duration,
                "transition_in": "none",
                "transition_duration": 0,
            }
        ],
    }


def _make_source(path: Path, two_faces: bool) -> Path:
    boxes = [
        "drawbox=x=0:y=140:w=820:h=620:color=0xCC2020:t=fill",
        "drawbox=x=260:y=380:w=60:h=60:color=white:t=fill",
    ]
    if two_faces:
        boxes += [
            "drawbox=x=1100:y=140:w=820:h=620:color=0x2040CC:t=fill",
            "drawbox=x=1360:y=380:w=60:h=60:color=white:t=fill",
        ]
    subprocess.run(
        [
            "ffmpeg", "-v", "error", "-y",
            "-f", "lavfi", "-i",
            f"color=c=0x202020:size=1920x1080:rate=30:duration={int(DURATION)}",
            "-f", "lavfi", "-i", "sine=frequency=440:duration=6",
            "-vf", ",".join(boxes),
            "-c:v", "libx264", "-preset", "veryfast", "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-shortest", str(path),
        ],
        check=True,
        capture_output=True,
    )
    return path


def _words():
    return [
        {"text": text, "start": float(i), "end": float(i) + 0.9, "confidence": 0.99}
        for i, text in enumerate(SPOKEN)
    ]


def _build_ass(path: Path, *, zones, position_y_override=None, words=None,
               hook_title=TITLE, clip_end=DURATION):
    assert build_assemblyai_ass_subtitles(
        Path("source.mp4"), 0.0, clip_end, 1080, 1920, path,
        caption_words=words if words is not None else _words(),
        hook_title=hook_title,
        position_y_override=position_y_override,
        face_zones=zones,
    )
    return path


def _render(source: Path, edl, ass: Path, output: Path) -> Path:
    fonts_dir = ass_fonts_dir(get_template("default")["font_family"])
    assert edl_render.render_edl_clip_ffmpeg(
        source, output, edl, subtitle_ass_path=ass, fonts_dir=fonts_dir
    ), output
    return output


# --- Frame measurement ----------------------------------------------------------


def _frame_png(video: Path, t: float, out: Path) -> Path:
    subprocess.run(
        [
            "ffmpeg", "-v", "error", "-y", "-ss", str(t), "-i", str(video),
            "-frames:v", "1", str(out),
        ],
        check=True,
        capture_output=True,
    )
    return out


def _frame_rgb(video: Path, t: float) -> np.ndarray:
    raw = subprocess.run(
        [
            "ffmpeg", "-v", "error", "-ss", str(t), "-i", str(video),
            "-frames:v", "1", "-f", "rawvideo", "-pix_fmt", "rgb24", "-",
        ],
        capture_output=True,
        check=True,
    ).stdout
    return np.frombuffer(raw, dtype=np.uint8).reshape(1920, 1080, 3).astype(np.int32)


def _markers(img: np.ndarray):
    white = np.abs(img - 255).sum(axis=2) < 90
    ys, xs = np.nonzero(white)
    if len(xs) == 0:
        return []
    pts = sorted(zip(ys.tolist(), xs.tolist()))
    clusters = [[pts[0]]]
    for point in pts[1:]:
        if point[0] - clusters[-1][-1][0] > 40:
            clusters.append([point])
        else:
            clusters[-1].append(point)
    return [
        (
            float(np.mean([p[1] for p in cluster])),
            float(np.mean([p[0] for p in cluster])),
        )
        for cluster in clusters
    ]


def _ocr_words(png: Path):
    """(text, box) per OCR word with usable confidence."""
    out = subprocess.run(
        ["tesseract", str(png), "stdout", "--psm", "11", "tsv"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    lines = out.splitlines()
    header = lines[0].split("\t")
    idx = {name: i for i, name in enumerate(header)}
    words = []
    for line in lines[1:]:
        parts = line.split("\t")
        if len(parts) < len(header):
            continue
        text = parts[idx["text"]].strip()
        try:
            conf = float(parts[idx["conf"]])
            box = tuple(
                float(parts[idx[key]]) for key in ("left", "top", "width", "height")
            )
        except ValueError:
            continue
        if text and conf >= 40:
            words.append((text.upper().strip(".,!?"), box))
    return words


def _union_box(boxes):
    x0 = min(b[0] for b in boxes)
    y0 = min(b[1] for b in boxes)
    x1 = max(b[0] + b[2] for b in boxes)
    y1 = max(b[1] + b[3] for b in boxes)
    return (x0, y0, x1 - x0, y1 - y0)


def _text_boxes(png: Path):
    """Union OCR boxes for the caption words and the title words."""
    words = _ocr_words(png)
    caption = [box for text, box in words if text in SPOKEN_WORDS]
    title = [box for text, box in words if text in TITLE_WORDS]
    return (
        _union_box(caption) if caption else None,
        _union_box(title) if title else None,
    )


def _zones_at(zones, t):
    return [
        box
        for start, end, boxes in zones
        if start <= t < end
        for box in boxes
    ]


def _max_intersection(text_box, face_boxes):
    if text_box is None:
        return 0.0
    return max(
        (intersection_area(text_box, face) for face in face_boxes),
        default=0.0,
    )


def _edge_white_run(img: np.ndarray) -> int:
    """Longest near-pure-white vertical run in the two edge columns."""
    best = 0
    for column in (img[:, 0, :], img[:, 1079, :]):
        near_white = (np.abs(column - 255).sum(axis=1) < 24)
        run = 0
        for value in near_white:
            run = run + 1 if value else 0
            best = max(best, run)
    return best


# --- Synthetic renders ------------------------------------------------------------


@pytest.fixture(scope="module")
def proof(tmp_path_factory):
    folder = tmp_path_factory.mktemp("faceguard")
    PROOF_DIR.mkdir(parents=True, exist_ok=True)
    original = reframe_plan.get_source_analysis
    made = {}

    def attach_with(analysis, source):
        reframe_plan.get_source_analysis = lambda path, spans: analysis
        try:
            edl = _edl()
            assert reframe_plan.attach_reframe_plan(
                edl, source, [(0.0, DURATION)], False
            ) is True
            return edl
        finally:
            reframe_plan.get_source_analysis = original

    for name, two in (("single", False), ("split", True)):
        source = _make_source(folder / f"{name}_source.mp4", two)
        analysis = _two_face_analysis() if two else _one_face_analysis()
        edl = attach_with(analysis, source)
        zones = collect_face_zones(edl)
        assert zones, name
        override = 0.5 if name == "single" else None
        for variant, zone_arg in (("before", None), ("after", zones)):
            ass = _build_ass(
                folder / f"{name}_{variant}.ass",
                zones=zone_arg,
                position_y_override=override,
            )
            made[f"{name}_{variant}"] = _render(
                source, edl, ass, folder / f"{name}_{variant}.mp4"
            )
        made[f"{name}_zones"] = zones
        made[f"{name}_edl"] = edl
    return made


def _sample(video: Path, tag: str, t: float) -> Path:
    png = PROOF_DIR / f"{tag}_t{t}.png"
    return _frame_png(video, t, png)


def test_single_caption_render_moves_off_face(proof):
    zones = proof["single_zones"]
    results = {}
    for variant in ("before", "after"):
        worst_caption = 0.0
        for t in (1.5, 2.5, 5.0):
            png = _sample(proof[f"single_{variant}"], f"single_{variant}", t)
            caption_box, _title_box = _text_boxes(png)
            assert caption_box is not None, (variant, t)
            worst_caption = max(
                worst_caption,
                _max_intersection(caption_box, _zones_at(zones, t)),
            )
            if variant == "after":
                # The stamped face box must contain the rendered
                # face-centre marker (the transform is honest). White
                # text glyphs also cluster, so pick the cluster nearest
                # the stamped box's centre.
                markers = _markers(_frame_rgb(proof["single_after"], t))
                assert markers, t
                face = _zones_at(zones, t)[0]
                cx = face[0] + face[2] / 2
                cy = face[1] + face[3] / 2
                mx, my = min(
                    markers,
                    key=lambda m: (m[0] - cx) ** 2 + (m[1] - cy) ** 2,
                )
                assert abs(mx - cx) <= 40
                assert abs(my - cy) <= 40
        results[variant] = worst_caption
    assert results["before"] > 0, "pre-fix render must show the defect"
    assert results["after"] == 0


def test_split_title_and_caption_render_clear_faces(proof):
    zones = proof["split_zones"]
    results = {"before": 0.0, "after": 0.0}
    for variant in ("before", "after"):
        for t in (1.5, 2.5):
            video = proof[f"split_{variant}"]
            png = _sample(video, f"split_{variant}", t)
            caption_box, title_box = _text_boxes(png)
            faces = _zones_at(zones, t)
            assert len(faces) == 2
            assert title_box is not None, (variant, t)
            results[variant] = max(
                results[variant],
                _max_intersection(title_box, faces),
                _max_intersection(caption_box, faces),
            )
            # Edge columns: no foreign slivers at x=0 / x=1079.
            assert _edge_white_run(_frame_rgb(video, t)) < 20
    assert results["before"] > 0, "pre-fix split render must show the defect"
    assert results["after"] == 0


# --- Real footage: Japan split shots ---------------------------------------------


@pytest.mark.skipif(not JAPAN_SOURCE.exists(), reason="Japan fixture absent")
def test_japan_split_render_keeps_text_off_real_faces(tmp_path):
    from src.media.face_detection import open_yunet_detector

    # Find a real split shot through the production attach path on the
    # corroborated span (cached analysis; plan shots are clip-local).
    probe = {
        "clip_id": "japan-probe",
        "duration": 50.0,
        "source_ranges": [[1310.0, 1360.0]],
        "segments": [
            {
                "index": 0,
                "start": 0.0,
                "end": 50.0,
                "source_start": 1310.0,
                "transition_in": "none",
                "transition_duration": 0,
            }
        ],
    }
    assert reframe_plan.attach_reframe_plan(
        probe, JAPAN_SOURCE, [(1310.0, 1360.0)], False
    ) is True
    split_shots = [
        s for s in probe["reframe"]["shots"] if s["mode"] == "split"
    ]
    assert split_shots, "the corroborated window must contain split shots"
    shot = max(split_shots, key=lambda s: s["end"] - s["start"])
    # Re-plan over a context window around the split shot on the
    # ORIGINAL source (planning is span-dependent; with its original
    # neighbours present the split decision reproduces exactly, and
    # the cached analysis makes it cheap). The renderer cuts the
    # window from the source itself via source_ranges.
    win_start = max(1310.0, 1310.0 + shot["start"] - 5.0)
    win_end = min(1360.0, 1310.0 + shot["end"] + 5.0)
    duration = win_end - win_start

    edl = _edl(duration)
    edl["source_ranges"] = [[win_start, win_end]]
    edl["segments"][0]["source_start"] = win_start
    assert reframe_plan.attach_reframe_plan(
        edl, JAPAN_SOURCE, [(win_start, win_end)], False
    ) is True
    segment = edl["segments"][0]
    assert "split_layers" in segment["reframe"], "window must stay split"
    split_interval = segment["reframe"]["split_layers"][0]["intervals"][0]
    zones = collect_face_zones(edl)
    assert zones

    cache = json.loads(JAPAN_CACHE.read_text(encoding="utf-8"))
    words = [
        {
            "text": w["text"].strip(),
            "start": w["start"] / 1000.0 - win_start,
            "end": w["end"] / 1000.0 - win_start,
            "confidence": w.get("confidence", 0.99),
        }
        for w in cache["words"]
        if win_start <= w["start"] / 1000.0 < win_end and w["text"].strip()
    ]
    if not words:
        words = _words()

    detector = open_yunet_detector()
    assert detector is not None
    spoken = {w["text"].upper().strip(".,!?") for w in words}
    title_words = {w.upper() for w in "Japan Food Guide".split()}

    outcomes = {}
    for variant, zone_arg in (("before", None), ("after", zones)):
        ass = _build_ass(
            tmp_path / f"japan_{variant}.ass",
            zones=zone_arg,
            words=words,
            hook_title="Japan Food Guide",
            clip_end=duration,
        )
        video = _render(
            JAPAN_SOURCE, edl, ass, tmp_path / f"japan_{variant}.mp4"
        )
        worst = 0.0
        # Sample inside the split shot, where both faces are on screen
        # and the pinned title is up.
        lo, hi = split_interval
        for t in (lo + 0.6, (lo + hi) / 2, hi - 0.6):
            png = _sample(video, f"japan_{variant}", t)
            import cv2

            faces = [
                tuple(float(v) for v in face[:4])
                for face in detector.detect(cv2.imread(str(png)))
            ]
            assert faces, (variant, t)
            ocr = _ocr_words(png)
            caption = [b for text, b in ocr if text in spoken]
            title = [b for text, b in ocr if text in title_words]
            for group in (caption, title):
                if group:
                    worst = max(
                        worst,
                        _max_intersection(_union_box(group), faces),
                    )
        outcomes[variant] = worst
    # The invariant on real faces: after the fix, no sampled frame has
    # caption or title pixels on a detected face.
    assert outcomes["after"] == 0, outcomes
