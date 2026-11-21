"""Fit strips: segment resolution stamps a fit shot's sharp band.

A fit renders the source sharp only inside a centred strip; the rest
of the canvas is blurred backdrop. The caption builder can only keep
captions out of the blur bands if the plan tells it where the strip
is, so ``_resolve_segment`` stamps ``reframe["fit_strips"]`` (mirroring
``face_boxes``) and ``collect_fit_strips`` lifts them to clip time.
Fabricated analysis at the ``get_source_analysis`` seam, like
test_reframe_face_boxes; everything downstream is production code.
"""

from __future__ import annotations

from pathlib import Path

from src.media import reframe_plan
from src.media.scene_context import SceneEvidence

W, H = 1920, 1080
DURATION = 6.0


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


def _faceless_fit_analysis():
    # Distributed action, no faces: a deliberate full-frame fit.
    return _analysis(
        [[]] * 18,
        dict(
            face_area=0.02, face_count=0, face_span=0.0,
            motion=0.1, outside_motion=0.5, motion_spread=0.6,
        ),
    )


def _edl():
    return {
        "clip_id": "fitstrips",
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


def _attach(monkeypatch, analysis):
    monkeypatch.setattr(
        reframe_plan, "get_source_analysis", lambda path, spans: analysis
    )
    edl = _edl()
    assert reframe_plan.attach_reframe_plan(
        edl, Path("source.mp4"), [(0.0, DURATION)], False
    ) is True
    return edl


def test_strip_rect_geometry():
    # 1920x1080 letterbox: foreground 1080x606 centred -> 657..1263.
    assert reframe_plan._fit_strip_rect(W, H) == (657, 1263)
    # A windowed fit (672px window scaled to 1080 wide) is taller.
    assert reframe_plan._fit_strip_rect(W, H, 672) == (93, 1827)
    # A portrait source fills the canvas: the strip is the whole frame.
    assert reframe_plan._fit_strip_rect(1080, 1920) == (0, 1920)
    assert reframe_plan._fit_strip_rect(0, H) is None


def test_fit_segment_stamps_the_plain_strip(monkeypatch):
    edl = _attach(monkeypatch, _faceless_fit_analysis())
    reframe = edl["segments"][0]["reframe"]
    assert reframe["fit_strips"] == [
        {"start": 0.0, "end": DURATION, "top": 657, "bottom": 1263}
    ]
    assert reframe_plan.collect_fit_strips(edl) == [
        (0.0, DURATION, 657.0, 1263.0)
    ]


def test_crop_segment_stamps_no_strip(monkeypatch):
    face = (200.0, 300.0, 180.0, 220.0)
    analysis = _analysis(
        [[face]] * 18,
        dict(face_area=0.0175, face_count=1, face_span=0.094,
             face_center=0.151),
    )
    edl = _attach(monkeypatch, analysis)
    reframe = edl["segments"][0]["reframe"]
    assert "fit_strips" not in reframe
    assert reframe_plan.collect_fit_strips(edl) == []


def test_windowed_fit_stamps_the_window_strip():
    segment = {"start": 0.0, "end": DURATION}
    shot = {
        "start": 0.0,
        "end": DURATION,
        "mode": "fit",
        "reason": "distributed_action",
        "face_src": [],
        "fit_windows": [[0.0, DURATION, 672, 624]],
    }
    crop_w, crop_h = reframe_plan.compute_vertical_crop_dims(W, H)
    reframe_plan._resolve_segment(segment, [shot], crop_w, crop_h, W, H)
    reframe = segment["reframe"]
    assert reframe["fit_strips"] == [
        {"start": 0.0, "end": DURATION, "top": 93, "bottom": 1827}
    ]
