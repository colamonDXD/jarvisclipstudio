"""Output-space face boxes stamped on EDL segments for the text guard.

The reframe planner already computes per-shot face boxes in source
pixels; segment resolution must transform them through the same
geometry the renderer uses (crop window sweep, split tile rects,
letterbox fit) and stamp them as ``segment["reframe"]["face_boxes"]``
so the caption/title face guard can keep text off faces. Detector
evidence is fabricated at the ``get_source_analysis`` seam, exactly
like test_reframe_split_render; everything downstream is production
code (``attach_reframe_plan``).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from src.media import reframe_plan
from src.media.scene_context import SceneEvidence

W, H = 1920, 1080
LEFT = (200.0, 300.0, 180.0, 220.0)
RIGHT = (1300.0, 300.0, 180.0, 220.0)
DURATION = 6.0


def _analysis(rows, evidence_kwargs, fps=3.0):
    return {
        "width": W,
        "height": H,
        "samples": [(i / fps, list(boxes)) for i, boxes in enumerate(rows)],
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
        "clip_id": "faceboxes",
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


def _attach(monkeypatch, analysis, plain=False):
    monkeypatch.setattr(
        reframe_plan, "get_source_analysis", lambda path, spans: analysis
    )
    edl = _edl()
    assert reframe_plan.attach_reframe_plan(
        edl, Path("source.mp4"), [(0.0, DURATION)], plain
    ) is True
    return edl


def _contains(box, x, y):
    return box[0] <= x <= box[0] + box[2] and box[1] <= y <= box[1] + box[3]


def test_split_segment_face_boxes_match_tile_geometry(monkeypatch):
    edl = _attach(monkeypatch, _two_face_analysis())
    segment = edl["segments"][0]
    layer = segment["reframe"]["split_layers"][0]
    entries = segment["reframe"]["face_boxes"]
    assert len(entries) == 1
    entry = entries[0]
    assert entry["start"] == 0.0 and entry["end"] == DURATION
    assert len(entry["boxes"]) == 2
    top, bottom = entry["boxes"]
    # Each stamped box is its subject's source box through the tile:
    # centres land where the tile transform puts the face centres.
    for box, centre, tile, pane_top in (
        (top, (290.0, 410.0), layer["left"], 0),
        (bottom, (1390.0, 410.0), layer["right"], 960),
    ):
        expected_x = (centre[0] - tile["tile_x"]) / tile["tile_w"] * 1080
        expected_y = pane_top + (centre[1] - tile["tile_y"]) / tile["tile_h"] * 960
        assert _contains(box, expected_x, expected_y)
        assert box[2] == pytest.approx(180 / tile["tile_w"] * 1080, abs=2)
        assert box[3] == pytest.approx(220 / tile["tile_h"] * 960, abs=2)
    # The top face's box lives in the top pane, the bottom's below.
    assert top[1] + top[3] <= 960
    assert bottom[1] >= 960


def test_single_segment_face_boxes_match_crop_geometry(monkeypatch):
    edl = _attach(monkeypatch, _one_face_analysis())
    segment = edl["segments"][0]
    crop = segment["reframe"]["crop"]
    entries = segment["reframe"]["face_boxes"]
    assert len(entries) == 1
    (box,) = entries[0]["boxes"]
    expected_x = (290.0 - float(crop["x_expr"])) / crop["w"] * 1080
    expected_y = (410.0 - float(crop["y"])) / crop["h"] * 1920
    assert _contains(box, expected_x, expected_y)
    assert box[2] == pytest.approx(180 / crop["w"] * 1080, abs=2)
    assert box[3] == pytest.approx(220 / crop["h"] * 1920, abs=2)


def test_plain_segment_face_boxes_stamped(monkeypatch):
    edl = _attach(monkeypatch, _two_face_analysis(), plain=True)
    segment = edl["segments"][0]
    assert "split_layers" not in segment["reframe"]
    entries = segment["reframe"]["face_boxes"]
    assert len(entries) == 1
    assert entries[0]["start"] == 0
    assert entries[0]["end"] == DURATION
    assert entries[0]["boxes"], "the dominant subject's box is stamped"


def test_collect_face_zones_uses_segment_offsets():
    edl = {
        "segments": [
            {
                "start": 10.0,
                "end": 16.0,
                "reframe": {
                    "face_boxes": [
                        {"start": 1.0, "end": 4.0, "boxes": [[1, 2, 3, 4]]}
                    ]
                },
            },
            {"start": 16.0, "end": 20.0, "reframe": {"crop": {}}},
            {"start": 20.0, "end": 22.0},
        ]
    }
    zones = reframe_plan.collect_face_zones(edl)
    assert zones == [(11.0, 14.0, [(1.0, 2.0, 3.0, 4.0)])]
    assert reframe_plan.collect_face_zones(None) == []
    assert reframe_plan.collect_face_zones({}) == []
