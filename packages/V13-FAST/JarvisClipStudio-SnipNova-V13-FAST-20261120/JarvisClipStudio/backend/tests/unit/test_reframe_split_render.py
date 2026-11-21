"""Viral Scout item 4: split-screen layouts — render-level proof.

STEP-0 premise outcome (see research_notes/viral-scout/SPLIT_REPORT.md):
a true two-pane split ALREADY exists end-to-end in the Auto production
path — ``build_reframe_plan`` decides ``mode="split"`` for a shot with
two significant face tracks whose combined span exceeds the 9:16 crop
window (shot >= ``SPLIT_MIN_SECONDS``), ``_resolve_segment`` stamps
``segment["reframe"]["split_layers"]``, and the EDL renderer crops both
face tiles, scales each to 1080x960 and vstacks them (left subject on
top, right subject on the bottom). What did NOT exist is any
render-level proof that the split reaches real pixels, plus boundary
pins for the trigger discipline. This suite is that proof; it changes
no product behaviour.

Method: the detector evidence is fabricated at the established seam
(``reframe_plan.get_source_analysis`` is monkeypatched with synthetic
samples/context, exactly like ``test_reframe_wire``), while everything
downstream is the real production code — ``attach_reframe_plan`` (the
call ``video_service`` makes for every EDL render), the real planner
and the real ``render_edl_clip_ffmpeg``. The synthetic source paints a
white marker square exactly at each fabricated face centre, so the
marker centroid in an output frame IS the face-box centre measurement:
if a face is framed in its pane, its marker lands at the position the
resolved tile/crop geometry predicts.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import numpy as np
import pytest

from src.media import edl_render, reframe_plan
from src.media.scene_context import SceneEvidence

W, H = 1920, 1080
LEFT = (200.0, 300.0, 180.0, 220.0)    # face box: x, y, w, h
RIGHT = (1300.0, 300.0, 180.0, 220.0)
LEFT_CENTRE = (LEFT[0] + LEFT[2] / 2, LEFT[1] + LEFT[3] / 2)      # (290, 410)
RIGHT_CENTRE = (RIGHT[0] + RIGHT[2] / 2, RIGHT[1] + RIGHT[3] / 2)  # (1390, 410)
DURATION = 6.0

RED = (204, 32, 32)
BLUE = (32, 64, 204)


# --- Fabricated detector evidence (the planner tests' seam) -------------------


def _samples(rows, fps=3.0):
    return [(i / fps, list(boxes)) for i, boxes in enumerate(rows)]


def _analysis(rows, evidence_kwargs, fps=3.0):
    return {
        "width": W,
        "height": H,
        "samples": _samples(rows, fps=fps),
        "cuts": [],
        "context": [
            SceneEvidence(time=i / fps, **evidence_kwargs) for i in range(len(rows))
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


def _edl():
    return {
        "clip_id": "splitproof",
        "duration": DURATION,
        "source_ranges": [[0.0, DURATION]],
        "segments": [
            {
                "index": 0,
                "start": 0.0,
                "end": DURATION,
                "transition_in": "none",
                "transition_duration": 0,
            }
        ],
    }


def _attach(monkeypatch, analysis, source: Path, plain=False):
    monkeypatch.setattr(
        reframe_plan, "get_source_analysis", lambda path, spans: analysis
    )
    edl = _edl()
    assert reframe_plan.attach_reframe_plan(
        edl, source, [(0.0, DURATION)], plain
    ) is True
    return edl


# --- Synthetic sources + frame measurement -------------------------------------


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
            "color=c=0x202020:size=1920x1080:rate=30:duration=6",
            "-f", "lavfi", "-i", "sine=frequency=440:duration=6",
            "-vf", ",".join(boxes),
            "-c:v", "libx264", "-preset", "veryfast", "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-shortest", str(path),
        ],
        check=True,
        capture_output=True,
    )
    return path


def _frame(path: Path, t: float = 3.0) -> np.ndarray:
    raw = subprocess.run(
        [
            "ffmpeg", "-v", "error", "-ss", str(t), "-i", str(path),
            "-frames:v", "1", "-f", "rawvideo", "-pix_fmt", "rgb24", "-",
        ],
        capture_output=True,
        check=True,
    ).stdout
    return np.frombuffer(raw, dtype=np.uint8).reshape(1920, 1080, 3).astype(np.int32)


def _markers(img: np.ndarray):
    """White-marker clusters as (centroid_x, centroid_y, pixel_count)."""
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
            len(cluster),
        )
        for cluster in clusters
    ]


def _colour_share(img: np.ndarray, rgb) -> float:
    """Share of pixels dominated by the given colour family."""
    r, g, b = (img[:, :, i] for i in range(3))
    if rgb == RED:
        mask = (r > 150) & (r > g + 60) & (r > b + 60)
    else:
        mask = (b > 150) & (b > r + 60) & (b > g + 40)
    return float(mask.mean())


def _tile_expected(centre, tile, pane_top: int):
    """Where a face centre lands in the output given its resolved tile."""
    x = (centre[0] - tile["tile_x"]) / tile["tile_w"] * 1080
    y = pane_top + (centre[1] - tile["tile_y"]) / tile["tile_h"] * 960
    return x, y


@pytest.fixture(scope="module")
def sources(tmp_path_factory):
    folder = tmp_path_factory.mktemp("split_sources")
    return {
        "two": _make_source(folder / "two_face.mp4", True),
        "one": _make_source(folder / "one_face.mp4", False),
    }


@pytest.fixture(scope="module")
def renders(tmp_path_factory, sources):
    """The four production-path renders, built once for the module."""
    out_dir = tmp_path_factory.mktemp("split_renders")
    made = {}
    original = reframe_plan.get_source_analysis

    def attach_with(analysis, source, plain=False):
        reframe_plan.get_source_analysis = lambda path, spans: analysis
        try:
            edl = _edl()
            assert reframe_plan.attach_reframe_plan(
                edl, source, [(0.0, DURATION)], plain
            ) is True
            return edl
        finally:
            reframe_plan.get_source_analysis = original

    split_edl = attach_with(_two_face_analysis(), sources["two"])
    single_edl = attach_with(_one_face_analysis(), sources["one"])
    plain_edl = attach_with(_two_face_analysis(), sources["two"], plain=True)
    made["split_edl"] = split_edl
    made["single_edl"] = single_edl
    made["plain_edl"] = plain_edl
    for name, source, edl in (
        ("split", sources["two"], split_edl),
        ("single", sources["one"], single_edl),
        ("plain", sources["two"], plain_edl),
        ("legacy", sources["one"], _edl()),
    ):
        output = out_dir / f"{name}.mp4"
        assert edl_render.render_edl_clip_ffmpeg(source, output, edl), name
        made[name] = output
    return made


# --- Attach/graph level ---------------------------------------------------------


def test_split_plan_stamps_split_layers(monkeypatch, sources):
    edl = _attach(monkeypatch, _two_face_analysis(), sources["two"])
    assert [s["mode"] for s in edl["reframe"]["shots"]] == ["split"]
    layers = edl["segments"][0]["reframe"]["split_layers"]
    assert len(layers) == 1
    layer = layers[0]
    assert layer["intervals"] == [[0.0, DURATION]]
    # Left subject's tile is the left region; the two tiles never overlap.
    assert layer["left"]["tile_x"] < layer["right"]["tile_x"]
    assert (
        layer["left"]["tile_x"] + layer["left"]["tile_w"]
        <= layer["right"]["tile_x"]
    )


def _graph(edl, source: Path) -> str:
    _extra, graph, _enc, _label = edl_render.build_edl_filter_graph(edl, source)
    return graph


def test_split_graph_composes_vstack_tiles(monkeypatch, sources):
    edl = _attach(monkeypatch, _two_face_analysis(), sources["two"])
    joined = _graph(edl, sources["two"])
    layer = edl["segments"][0]["reframe"]["split_layers"][0]
    for tile in (layer["left"], layer["right"]):
        fragment = (
            f"crop={tile['tile_w']}:{tile['tile_h']}:"
            f"{tile['tile_x']}:{tile['tile_y']},scale=1080:960"
        )
        assert fragment in joined
    assert "vstack" in joined


# --- Render level -----------------------------------------------------------------


def test_two_face_render_puts_each_face_in_its_pane(renders):
    img = _frame(renders["split"])
    top, bottom = img[:960], img[960:]
    # Each pane is filled by its own subject's colour, with no bleed of
    # the other subject's colour across the seam.
    assert _colour_share(top, RED) > 0.90
    assert _colour_share(bottom, BLUE) > 0.90
    assert _colour_share(top, BLUE) < 0.01
    assert _colour_share(bottom, RED) < 0.01
    # Both face centres land where the resolved tiles predict: the left
    # face centred in the top pane, the right face centred in the bottom.
    layer = renders["split_edl"]["segments"][0]["reframe"]["split_layers"][0]
    markers = _markers(img)
    assert len(markers) == 2
    markers.sort(key=lambda m: m[1])
    exp_top = _tile_expected(LEFT_CENTRE, layer["left"], 0)
    exp_bottom = _tile_expected(RIGHT_CENTRE, layer["right"], 960)
    for marker, expected in zip(markers, (exp_top, exp_bottom)):
        assert abs(marker[0] - expected[0]) <= 40
        assert abs(marker[1] - expected[1]) <= 40


def test_one_face_render_is_the_single_crop_unchanged(renders):
    edl = renders["single_edl"]
    assert [s["mode"] for s in edl["reframe"]["shots"]] == ["single"]
    segment_reframe = edl["segments"][0]["reframe"]
    assert "split_layers" not in segment_reframe
    assert "vstack" not in _graph(edl, renders["single"])
    img = _frame(renders["single"])
    markers = _markers(img)
    assert len(markers) == 1
    crop = segment_reframe["crop"]
    expected_x = (LEFT_CENTRE[0] - float(crop["x_expr"])) / crop["w"] * 1080
    expected_y = (LEFT_CENTRE[1] - float(crop["y"])) / crop["h"] * 1920
    assert abs(markers[0][0] - expected_x) <= 40
    assert abs(markers[0][1] - expected_y) <= 40


def test_one_face_legacy_control_misses_the_face(renders):
    # Without the plan, the legacy centre crop (x 657..1263 on a 1920
    # source) excludes the left face entirely: the marker the single
    # render frames is absent here. This pins that the single render
    # above is the plan's crop — today's behaviour, unchanged.
    assert _markers(_frame(renders["legacy"])) == []


def test_plain_render_of_two_face_source_stays_single_full_bleed(renders):
    edl = renders["plain_edl"]
    assert edl["reframe"].get("plain") is True
    assert [s["mode"] for s in edl["reframe"]["shots"]] == ["plain"]
    assert "split_layers" not in edl["segments"][0]["reframe"]
    img = _frame(renders["plain"])
    # One continuous full-bleed crop of the dominant (left) face: its
    # colour fills the whole frame height, the other subject's colour
    # never appears, and exactly one face marker is framed.
    assert _colour_share(img, BLUE) < 0.01
    assert _colour_share(img[:960], RED) > 0.5
    assert _colour_share(img[960:], RED) > 0.5
    markers = _markers(img)
    assert len(markers) == 1
    crop = edl["segments"][0]["reframe"]["crop"]
    expected_x = (LEFT_CENTRE[0] - float(crop["x_expr"])) / crop["w"] * 1080
    expected_y = (LEFT_CENTRE[1] - float(crop["y"])) / crop["h"] * 1920
    assert abs(markers[0][0] - expected_x) <= 40
    assert abs(markers[0][1] - expected_y) <= 40


# --- Trigger discipline (planner level) ------------------------------------------


def test_split_needs_the_second_face_present_for_a_third_of_the_shot():
    # TRACK_MIN_PRESENCE_SHARE = 0.35 of the shot. At 5/18 samples
    # (0.28) the second face is a passer-by: no split, the dominant
    # subject takes a single crop. At 7/18 (0.39) it is a co-subject
    # and the shot splits.
    evidence = dict(face_area=0.0175, face_count=2, face_span=0.667)
    below = _analysis([[LEFT, RIGHT]] * 5 + [[LEFT]] * 13, evidence)
    plan = reframe_plan.build_reframe_plan(below, [(0.0, DURATION)], DURATION)
    assert [s["mode"] for s in plan["shots"]] == ["single"]
    above = _analysis([[LEFT, RIGHT]] * 7 + [[LEFT]] * 11, evidence)
    plan = reframe_plan.build_reframe_plan(above, [(0.0, DURATION)], DURATION)
    assert [s["mode"] for s in plan["shots"]] == ["split"]


def test_split_needs_a_two_second_shot():
    # SPLIT_MIN_SECONDS = 2.0: a 1.5 s two-shot is too short to earn
    # the split's visual cost and falls through to a full-frame fit.
    analysis = _analysis(
        [[LEFT, RIGHT]] * 5,
        dict(face_area=0.0175, face_count=2, face_span=0.667),
    )
    plan = reframe_plan.build_reframe_plan(analysis, [(0.0, 1.5)], 1.5)
    assert plan is not None
    assert all(s["mode"] != "split" for s in plan["shots"])
