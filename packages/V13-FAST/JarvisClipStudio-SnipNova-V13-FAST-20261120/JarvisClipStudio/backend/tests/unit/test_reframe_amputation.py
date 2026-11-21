"""Amputation audit: the decided crop window is measured against the
faces the detector actually saw, sample by sample.

The defect (MrBeast exports, 2026-11-15): wide/group shots shipped
with faces sliced at the crop edges — a subject half out of frame,
group members split across both edges. The decision ladder checks
spans and medians, and the trajectory guard checks faces near its
sparse keys (a static subject collapses to ONE key, guarded against
the faces present at the piece start), so a face entering later in
the piece was sliced by the held window and nothing ever measured it.

The audit repairs what it can (pair re-anchor, fit conversion),
stamps what it cannot, and flags subject-less motionless shots.
Synthetic analyses: 1920x1080 source, 3 fps samples over 6 s, whose
standard 9:16 window is 606x1080.
"""

from dataclasses import replace

from src.media.reframe_plan import (
    _audit_crop_amputation,
    _box_sliced_by_window,
    _persistent_cut_tracks,
    associate_tracks,
    build_reframe_plan,
)
from src.media.scene_context import SceneEvidence

W, H = 1920, 1080
FRAME_AREA = float(W * H)


def _context(motion=0.0):
    return [
        SceneEvidence(
            time=i / 3.0, face_area=0.05, face_count=1, face_span=0.2,
            motion=motion,
        )
        for i in range(18)
    ]


def _analysis(boxes_by_index, context=None):
    samples = [
        (i / 3.0, list(boxes_by_index(i))) for i in range(18)
    ]
    return {
        "width": W,
        "height": H,
        "samples": samples,
        "context": _context() if context is None else context,
        "cuts": [],
    }


def _plan(boxes_by_index, context=None):
    plan = build_reframe_plan(
        _analysis(boxes_by_index, context), [(0.0, 6.0)], 6.0
    )
    assert plan is not None
    return plan


SUBJECT = (150.0, 450.0, 150.0, 150.0)  # static, left of centre


def test_late_entering_face_is_reanchored_as_pair():
    # The second face enters at t=4s — long after the static key the
    # trajectory guard checked — and the held window slices it in
    # every sample it is present. Its span with the subject fits the
    # window, so the audit re-anchors the shot as a pair crop.
    late = (560.0, 450.0, 130.0, 150.0)
    plan = _plan(lambda i: [SUBJECT, late] if i >= 12 else [SUBJECT])
    (shot,) = plan["shots"]
    assert shot["mode"] == "pair"
    assert shot["amputation_avoided"]["how"] == "pair_anchor"
    assert "amputation" not in shot


def test_group_too_wide_for_window_converts_to_fit():
    # Same late entry, but the pair's combined span exceeds the
    # window: the only whole-subject layout is the full frame, and
    # neither face's backdrop copy lands in the fit's blur bands.
    subject = (60.0, 450.0, 150.0, 150.0)
    late = (560.0, 450.0, 130.0, 150.0)
    plan = _plan(lambda i: [subject, late] if i >= 12 else [subject])
    (shot,) = plan["shots"]
    assert shot["mode"] == "fit"
    assert shot["reason"] == "amputation_avoided_fit"
    assert shot["amputation_avoided"]["how"] == "fit"
    assert "amputation" not in shot


def test_unrepairable_cut_is_stamped_not_silent():
    # A pair shot (both hosts significant, span fits) whose window a
    # third, late-entering face straddles. The three-face span still
    # fits the window on medians, so no repair rule fires — the cut
    # is measured and stamped on the plan instead of shipping silent.
    host_b = (350.0, 450.0, 150.0, 150.0)
    third = (560.0, 20.0, 130.0, 110.0)
    plan = _plan(
        lambda i: [SUBJECT, host_b, third] if i >= 12 else [SUBJECT, host_b]
    )
    (shot,) = plan["shots"]
    assert shot["mode"] == "pair"
    assert shot["amputation"]["tracks_cut"] == 1
    assert shot["amputation"]["samples_cut"] >= 2
    assert "amputation_avoided" not in shot


def test_clean_single_subject_has_no_amputation_stamp():
    plan = _plan(lambda i: [SUBJECT])
    (shot,) = plan["shots"]
    assert shot["mode"] == "single"
    assert "amputation" not in shot
    assert "amputation_avoided" not in shot


def test_empty_shot_is_flagged():
    # No faces anywhere and no motion: the near-black / bare-ground
    # seconds that reached exports unremarked.
    context = [
        replace(evidence, face_area=0.0, face_count=0, face_span=0.0)
        for evidence in _context(motion=0.0)
    ]
    plan = _plan(lambda i: [], context=context)
    (shot,) = plan["shots"]
    assert shot["content_flag"] == "no_subject_low_motion"


def test_moving_empty_shot_is_not_flagged():
    # A faceless but moving shot (scenery, drone drift) is content.
    context = [
        replace(evidence, face_area=0.0, face_count=0, face_span=0.0)
        for evidence in _context(motion=0.05)
    ]
    plan = _plan(lambda i: [], context=context)
    (shot,) = plan["shots"]
    assert "content_flag" not in shot


# --- Audit machinery, directly -------------------------------------------------


def test_box_sliced_by_window_edges():
    box = (100.0, 0.0, 80.0, 80.0)
    assert _box_sliced_by_window(box, 50, 606) is False   # fully inside
    assert _box_sliced_by_window(box, 120, 606) is True   # left edge cuts it
    assert _box_sliced_by_window(box, -500, 606) is True  # right edge cuts it
    assert _box_sliced_by_window(box, 300, 606) is False  # fully outside
    # A box wider than the window is a layout decision, not an amputation.
    assert _box_sliced_by_window((0.0, 0.0, 700.0, 80.0), 10, 606) is False


def test_audit_interpolates_the_window_between_keys():
    # The window pans between two keys; midway it slices the face even
    # though both key positions keep it whole or outside.
    face = (700.0, 450.0, 130.0, 150.0)
    samples = [(0.0, [face]), (1.0, [face]), (2.0, [face])]
    shot = {
        "start": 0.0,
        "end": 2.0,
        "pieces": [
            {"start": 0.0, "end": 2.0, "keys": [[0.0, 0], [2.0, 400]]}
        ],
    }
    pieces = [(0.0, 2.0, 0.0, 2.0, 0.0, 0.0)]
    tracks = associate_tracks(samples)
    audit = _audit_crop_amputation(shot, samples, pieces, tracks, 606, FRAME_AREA)
    assert audit is not None
    # x(t=0)=0: face outside. x(t=1)=200: window 200..806 slices it.
    # x(t=2)=400: window 400..1006 contains it whole.
    assert audit["samples_cut"] == 1
    assert _persistent_cut_tracks(audit) == []  # 1 of 3 is not persistent


def test_audit_counts_persistence_per_track():
    face = (560.0, 450.0, 130.0, 150.0)
    samples = [(i / 3.0, [face]) for i in range(6)]
    shot = {
        "start": 0.0,
        "end": 2.0,
        "pieces": [{"start": 0.0, "end": 2.0, "keys": [[0.0, 0]]}],
    }
    pieces = [(0.0, 2.0, 0.0, 2.0, 0.0, 0.0)]
    tracks = associate_tracks(samples)
    audit = _audit_crop_amputation(shot, samples, pieces, tracks, 606, FRAME_AREA)
    assert audit is not None
    assert audit["samples_cut"] == 6
    assert _persistent_cut_tracks(audit) == [0]
