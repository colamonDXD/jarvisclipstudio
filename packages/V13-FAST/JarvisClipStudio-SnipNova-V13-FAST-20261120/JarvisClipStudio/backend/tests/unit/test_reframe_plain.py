"""Plain — just cuts reframing: always a full-bleed tight crop.

The defect (Luffy, 2026-11-10): the auto planner framed his speaker
wide with headroom to spare and dropped a wide shot mid-clip as a
letterboxed strip between blurred bands (mode "fit"). His reference
render is a tight full-frame crop. The plain planner must therefore
never emit fit or split shots, must size its window from the subject
(face ≈ 40% of the window width, comfortably above the ~22% brief
floor), and must still full-bleed-crop when no face is usable.

Synthetic analyses only: samples are (source_time, [face boxes]) at
3 fps over a 6 s clip on a 1920x1080 source, whose standard full-height
9:16 window is 606x1080 (the repo's even-rounding of 607.5).
"""

from src.media.reframe_plan import (
    _resolve_segment_plain,
    build_plain_reframe_plan,
    build_reframe_plan,
)
from src.media.reframing import compute_vertical_crop_dims

STD_W, STD_H = compute_vertical_crop_dims(1920, 1080)


def _analysis(boxes_by_time, duration=6.0):
    samples = []
    t = 0.0
    while t < duration:
        samples.append((t, list(boxes_by_time(t))))
        t += 1.0 / 3.0
    return {
        "width": 1920,
        "height": 1080,
        "samples": samples,
        "context": [],
        "cuts": [],
    }


def _plan(boxes_by_time):
    return build_plain_reframe_plan(
        _analysis(boxes_by_time), [(0.0, 6.0)], 6.0
    )


def test_single_face_gets_a_tight_full_bleed_window():
    # One 150px-wide face standing left of centre — the talking head.
    plan = _plan(lambda t: [(200.0, 300.0, 150.0, 200.0)])
    assert plan is not None
    assert plan["plain"] is True
    assert plan["delegate"] is None
    (shot,) = plan["shots"]
    assert shot["mode"] == "plain"
    assert shot["reason"] == "plain_single_subject"
    # Window sized from the face: 150 / 0.40 = 375 -> 374 (even floor),
    # height 374 * 16/9 -> 664.
    assert shot["crop_w"] == 374
    assert shot["crop_h"] == 664
    face_share = 150.0 / shot["crop_w"]
    assert face_share >= 0.22
    assert shot["crop_w"] < STD_W


def test_single_face_segment_resolves_to_one_covering_crop():
    plan = _plan(lambda t: [(200.0, 300.0, 150.0, 200.0)])
    segment = {"start": 0.0, "end": 6.0}
    _resolve_segment_plain(segment, plan["shots"], 1920, 1080)
    reframe = segment["reframe"]
    assert "fit_layers" not in reframe
    assert "split_layers" not in reframe
    crop = reframe["crop"]
    assert (crop["w"], crop["h"]) == (374, 664)
    assert crop["intervals"] == [[0, 6.0]]


def test_two_close_faces_frame_the_region_not_a_split():
    plan = _plan(lambda t: [(700.0, 300.0, 150.0, 200.0),
                             (900.0, 310.0, 150.0, 200.0)])
    (shot,) = plan["shots"]
    assert shot["mode"] == "plain"
    assert shot["reason"] == "plain_group_region"
    # Union spans 700..1050 = 350 px, framed at ~85% of the window.
    union_share = 350.0 / shot["crop_w"]
    assert 0.80 <= union_share <= 0.90
    segment = {"start": 0.0, "end": 6.0}
    _resolve_segment_plain(segment, plan["shots"], 1920, 1080)
    assert "split_layers" not in segment["reframe"]


def test_two_wide_apart_faces_pick_the_dominant_subject():
    plan = _plan(lambda t: [(100.0, 300.0, 140.0, 190.0),
                             (1500.0, 300.0, 140.0, 190.0)])
    (shot,) = plan["shots"]
    assert shot["mode"] == "plain"
    # The union (1540 px) cannot fit one window: no split, no fit —
    # the dominant subject is framed tight instead.
    assert shot["reason"] == "plain_single_subject"
    assert shot["crop_w"] == 350  # 140 / 0.40
    segment = {"start": 0.0, "end": 6.0}
    _resolve_segment_plain(segment, plan["shots"], 1920, 1080)
    assert "split_layers" not in segment["reframe"]
    assert "fit_layers" not in segment["reframe"]


def test_no_faces_falls_back_to_a_standard_full_bleed_crop():
    plan = _plan(lambda t: [])
    (shot,) = plan["shots"]
    assert shot["mode"] == "plain"
    assert shot["reason"].startswith("plain_")
    assert (shot["crop_w"], shot["crop_h"]) == (STD_W, STD_H)
    segment = {"start": 0.0, "end": 6.0}
    _resolve_segment_plain(segment, plan["shots"], 1920, 1080)
    assert segment["reframe"]["crop"]["w"] == STD_W


def test_close_up_face_uses_the_standard_window_not_wider():
    # A 500px face: 500 / 0.40 = 1250 > the standard window, so the
    # window caps at standard dims and the face simply fills it.
    plan = _plan(lambda t: [(700.0, 200.0, 500.0, 650.0)])
    (shot,) = plan["shots"]
    assert (shot["crop_w"], shot["crop_h"]) == (STD_W, STD_H)
    assert 500.0 / shot["crop_w"] > 0.8


def test_tiny_face_respects_the_upscale_floor():
    # A 60px face would want a 150px window (7x upscale to 1080 out):
    # the floor holds the window at 310px instead.
    plan = _plan(lambda t: [(900.0, 400.0, 60.0, 80.0)])
    (shot,) = plan["shots"]
    assert shot["crop_w"] == 310
    assert 1080 / shot["crop_w"] <= 3.5


def test_portrait_source_skips_planning_like_the_auto_planner():
    analysis = _analysis(lambda t: [(50.0, 100.0, 100.0, 140.0)])
    analysis["width"] = 720
    analysis["height"] = 1280
    assert build_plain_reframe_plan(analysis, [(0.0, 6.0)], 6.0) is None


def test_auto_planner_unaffected_by_plain_presence():
    # The same evidence through the auto planner keeps its own modes
    # (here: the fit verdict it reaches today for evidence-less
    # context) and never carries the plain marker: nothing about the
    # plain builder leaks into the shared path.
    plan = build_reframe_plan(
        _analysis(lambda t: [(200.0, 300.0, 150.0, 200.0)]),
        [(0.0, 6.0)],
        6.0,
    )
    assert plan is not None
    assert "plain" not in plan
    assert all(shot["mode"] != "plain" for shot in plan["shots"])
    assert plan["crop_w"] == STD_W
