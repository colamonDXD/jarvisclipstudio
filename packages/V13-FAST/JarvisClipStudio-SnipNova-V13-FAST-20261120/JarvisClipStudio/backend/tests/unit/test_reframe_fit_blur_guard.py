"""Fit-blur guard: a grounded face must never land in a blurred pad band.

Regression set for self-run audit issue #5. A fit (blurred-pad) composition
renders the source twice: sharp, scaled into the centred foreground strip,
and blurred, cover-scaled as the backdrop. A face high or low in the source
frame is magnified by the backdrop into the pad bands as a large blurred
face (measured on the Japan fixture: the wide-group fit at r1 12.5-20s
shows the middle subject's blurred head filling the top band). The planner
now checks every fit decision against the shot's grounded face tracks and
re-decides from the tracks when a face would land in a band.

Geometry reference for the 1920x1080 -> 1080x1920 numbers below:
backdrop scale = 1920/1080 = 1.7778 (centred, x offset 1166.7);
foreground strip = y 656..1264 (scale 0.5625, centred).
"""

from __future__ import annotations

import pytest

from src.media import reframe_plan
from src.media.scene_context import SceneEvidence

W, H = 1920, 1080


def _samples(boxes_per_sample, fps=3.0):
    return [(i / fps, list(boxes)) for i, boxes in enumerate(boxes_per_sample)]


def _evidence(t, **kwargs):
    return SceneEvidence(time=t, **kwargs)


def _analysis(rows, evidence_kwargs, fps=3.0):
    count = len(rows)
    return {
        "width": W,
        "height": H,
        "samples": _samples(rows, fps=fps),
        "cuts": [],
        "context": [_evidence(i / fps, **evidence_kwargs) for i in range(count)],
    }


# Evidence the context planner answers "distributed_action" for: real faces
# and spread motion, no text/screen risk. distributed_action is NOT one of
# the rescuable fit reasons, so these entries used to stay fit verbatim.
def _distributed(count, span):
    return dict(
        face_area=0.02, face_count=count, face_span=span,
        motion=0.1, outside_motion=0.5, motion_spread=0.6,
    )


# --- Backdrop geometry -------------------------------------------------------


def test_blur_share_top_band_face_is_fully_blurred():
    # Copy lands at y 178..604, entirely above the sharp strip (656..1264).
    assert reframe_plan._fit_blur_share((800.0, 100.0, 200.0, 240.0), W, H) == pytest.approx(1.0)


def test_blur_share_bottom_band_face_is_fully_blurred():
    # Copy lands at y 1351..1778, entirely below the sharp strip.
    assert reframe_plan._fit_blur_share((800.0, 760.0, 200.0, 240.0), W, H) == pytest.approx(1.0)


def test_blur_share_vertically_centred_face_is_zero():
    # Copy lands at y 747..1173, inside the sharp strip.
    assert reframe_plan._fit_blur_share((800.0, 420.0, 200.0, 240.0), W, H) == pytest.approx(0.0)


def test_blur_share_slight_straddle_is_below_the_threshold():
    # Copy y 613..933: 43 of 320 px in the top band (~0.13 < 0.20).
    box = (800.0, 345.0, 200.0, 180.0)
    assert reframe_plan._fit_blur_share(box, W, H) == pytest.approx(0.134, abs=0.01)
    assert not reframe_plan._faces_blurred_by_fit([box], W, H)


def test_blur_share_deep_straddle_is_above_the_threshold():
    # Copy y 533..853: 123 of 320 px in the top band (~0.38 >= 0.20).
    box = (800.0, 300.0, 200.0, 180.0)
    assert reframe_plan._fit_blur_share(box, W, H) == pytest.approx(0.383, abs=0.01)
    assert reframe_plan._faces_blurred_by_fit([box], W, H)


def test_blur_share_off_frame_copy_is_zero():
    # The backdrop's cover crop never shows this face at all.
    assert reframe_plan._fit_blur_share((100.0, 300.0, 150.0, 180.0), W, H) == pytest.approx(0.0)


def test_blur_share_on_a_43_source():
    # 1440x1080: foreground strip y 555..1365; copy of this face y 178..569.
    share = reframe_plan._fit_blur_share((600.0, 100.0, 180.0, 220.0), 1440, 1080)
    assert share > 0.9


# --- Plan-level: fit decisions with grounded faces ---------------------------


def test_fit_with_face_in_top_band_is_redecided_to_single():
    face = (800.0, 100.0, 200.0, 240.0)
    analysis = _analysis([[face]] * 18, _distributed(1, 0.104))
    plan = reframe_plan.build_reframe_plan(analysis, [(0.0, 6.0)], 6.0)
    shot = plan["shots"][0]
    assert shot["mode"] == "single"
    assert shot["reason"] == "face_blur_avoided"
    keys = shot["pieces"][0]["keys"]
    assert keys and keys[0][1] <= 900 <= keys[0][1] + plan["crop_w"]


def test_fit_with_face_in_bottom_band_is_redecided_to_single():
    face = (800.0, 760.0, 200.0, 240.0)
    analysis = _analysis([[face]] * 18, _distributed(1, 0.104))
    plan = reframe_plan.build_reframe_plan(analysis, [(0.0, 6.0)], 6.0)
    shot = plan["shots"][0]
    assert shot["mode"] == "single"
    assert shot["reason"] == "face_blur_avoided"


def test_fit_with_faces_in_both_bands_is_redecided_to_split():
    # Both backdrop copies are inside the cover crop's visible slice
    # (source x 656..1313) and land one per band; the pair spans 620px,
    # wider than the 606px crop window, so the shot must split.
    top = (660.0, 100.0, 180.0, 220.0)
    bottom = (1100.0, 740.0, 180.0, 220.0)
    analysis = _analysis([[top, bottom]] * 18, _distributed(2, 0.2))
    plan = reframe_plan.build_reframe_plan(analysis, [(0.0, 6.0)], 6.0)
    shot = plan["shots"][0]
    assert shot["mode"] == "split"
    assert shot["reason"] == "face_blur_avoided"


def test_fit_with_two_band_faces_too_short_to_split_uses_dominant_single():
    top = (660.0, 100.0, 180.0, 220.0)
    bottom = (1100.0, 740.0, 180.0, 220.0)
    analysis = _analysis([[top, bottom]] * 4, _distributed(2, 0.2))
    plan = reframe_plan.build_reframe_plan(analysis, [(0.0, 4 / 3)], 4 / 3)
    shot = plan["shots"][0]
    assert shot["mode"] == "single"
    assert shot["reason"] == "face_blur_avoided"


def test_faceless_distributed_fit_stays_fit():
    analysis = _analysis([[]] * 18, _distributed(0, 0.0))
    plan = reframe_plan.build_reframe_plan(analysis, [(0.0, 6.0)], 6.0)
    shot = plan["shots"][0]
    assert shot["mode"] == "fit"
    assert shot["reason"] == "distributed_action"


def test_fit_with_vertically_centred_face_stays_fit():
    face = (800.0, 420.0, 200.0, 240.0)
    analysis = _analysis([[face]] * 18, _distributed(1, 0.104))
    plan = reframe_plan.build_reframe_plan(analysis, [(0.0, 6.0)], 6.0)
    shot = plan["shots"][0]
    assert shot["mode"] == "fit"
    assert shot["reason"] == "distributed_action"


def test_fit_with_slightly_straddling_face_stays_fit():
    face = (800.0, 345.0, 200.0, 180.0)  # ~0.13 of the copy in the band
    analysis = _analysis([[face]] * 18, _distributed(1, 0.104))
    plan = reframe_plan.build_reframe_plan(analysis, [(0.0, 6.0)], 6.0)
    shot = plan["shots"][0]
    assert shot["mode"] == "fit"
    assert shot["reason"] == "distributed_action"


def test_wide_group_with_faces_in_bands_becomes_dominant_single():
    faces = [
        (100.0, 300.0, 150.0, 180.0),
        (850.0, 300.0, 190.0, 220.0),  # the dominant (largest) subject
        (1600.0, 300.0, 150.0, 180.0),
    ]
    analysis = _analysis(
        [faces] * 18,
        dict(face_area=0.013, face_count=3, face_span=0.859),
    )
    plan = reframe_plan.build_reframe_plan(analysis, [(0.0, 6.0)], 6.0)
    shot = plan["shots"][0]
    assert shot["mode"] == "single"
    assert shot["reason"] == "face_blur_avoided"
    keys = shot["pieces"][0]["keys"]
    assert keys and keys[0][1] <= 945 <= keys[0][1] + plan["crop_w"]


def test_wide_group_vertically_centred_stays_fit():
    faces = [
        (100.0, 450.0, 150.0, 180.0),
        (850.0, 450.0, 150.0, 180.0),
        (1600.0, 450.0, 150.0, 180.0),
    ]
    analysis = _analysis(
        [faces] * 18,
        dict(face_area=0.013, face_count=3, face_span=0.859),
    )
    plan = reframe_plan.build_reframe_plan(analysis, [(0.0, 6.0)], 6.0)
    shot = plan["shots"][0]
    assert shot["mode"] == "fit"
    assert shot["reason"] == "wide_group"


def test_oversized_subject_in_band_becomes_a_crop():
    # Face wider than the 606px crop window, high in the frame: the fit
    # would magnify it into the top band, so the guard crops into the
    # close-up instead of honouring subject_exceeds_crop's fit.
    face = (600.0, 100.0, 700.0, 500.0)
    analysis = _analysis(
        [[face]] * 18,
        dict(face_area=0.169, face_count=1, face_span=0.365),
    )
    plan = reframe_plan.build_reframe_plan(analysis, [(0.0, 6.0)], 6.0)
    shot = plan["shots"][0]
    assert shot["mode"] == "single"
    assert shot["reason"] == "face_blur_avoided"


def test_guard_judges_observed_boxes_not_the_synthetic_median_box():
    # A drifting face alternating between two positions: half its real
    # boxes land deep in the top band (share 0.35), the other half barely
    # straddle (0.08). The per-coordinate median box is the straddling
    # variant (share 0.08), so a median-box check would keep the fit —
    # but half the shot shows the face blurred, so the guard must fire.
    high = (800.0, 310.0, 200.0, 170.0)
    low = (800.0, 350.0, 200.0, 230.0)
    assert reframe_plan._fit_blur_share(high, W, H) > 0.3
    assert reframe_plan._fit_blur_share(low, W, H) < 0.1
    rows = [[high] if i % 2 == 0 else [low] for i in range(18)]
    analysis = _analysis(rows, _distributed(1, 0.104))
    plan = reframe_plan.build_reframe_plan(analysis, [(0.0, 6.0)], 6.0)
    shot = plan["shots"][0]
    assert shot["mode"] == "single"
    assert shot["reason"] == "face_blur_avoided"


# --- Content fits: the exemption ends at a grounded face --------------------
#
# Deliberate content-preservation fits (text / screen) used to be exempt
# from the fit-blur guard wholesale. That exemption shipped the TJR
# framing flip — a talking head re-decided to a blur-band fit because
# trading boards at the frame edge tripped a text/screen reason — so it
# now covers only faceless fits: with a grounded face track, the guard
# judges the fit like any other and the face wins.


def test_text_heavy_fit_with_grounded_face_is_redecided():
    face = (800.0, 100.0, 200.0, 240.0)  # would fully land in the top band
    analysis = _analysis(
        [[face]] * 18,
        dict(face_area=0.023, face_count=1, face_span=0.104, cut_text_lines=2),
    )
    plan = reframe_plan.build_reframe_plan(analysis, [(0.0, 6.0)], 6.0)
    shot = plan["shots"][0]
    assert shot["mode"] == "single"
    assert shot["reason"] == "face_blur_avoided"


def test_screen_content_fit_with_grounded_face_is_redecided():
    face = (900.0, 100.0, 200.0, 240.0)
    analysis = _analysis(
        [[face]] * 18,
        dict(face_area=0.023, face_count=1, face_span=0.104,
             face_center=0.55, text_rows=4),
    )
    plan = reframe_plan.build_reframe_plan(analysis, [(0.0, 6.0)], 6.0)
    shot = plan["shots"][0]
    assert shot["mode"] == "single"
    assert shot["reason"] == "face_blur_avoided"


def test_text_heavy_fit_without_a_face_stays_fit():
    # The exemption's remaining scope: no grounded face track, so there
    # is nothing for the pad bands to blur — the content fit stands.
    analysis = _analysis(
        [[]] * 18,
        dict(face_area=0.023, face_count=1, face_span=0.104, cut_text_lines=2),
    )
    plan = reframe_plan.build_reframe_plan(analysis, [(0.0, 6.0)], 6.0)
    shot = plan["shots"][0]
    assert shot["mode"] == "fit"
    assert shot["reason"] == "text_heavy"


def test_screen_content_fit_with_unblurred_face_stays_fit():
    # A grounded face whose backdrop copy stays inside the sharp strip:
    # the guard has nothing to veto, and a lone fit is the clip's only
    # layout (no subject clip to reconcile against).
    face = (900.0, 420.0, 200.0, 240.0)  # copy lands inside the strip
    analysis = _analysis(
        [[face]] * 18,
        dict(face_area=0.023, face_count=1, face_span=0.104,
             face_center=0.55, text_rows=4),
    )
    plan = reframe_plan.build_reframe_plan(analysis, [(0.0, 6.0)], 6.0)
    shot = plan["shots"][0]
    assert shot["mode"] == "fit"
    assert shot["reason"] == "screen_content_outside_crop"


# --- Fit windows must not exclude a grounded face ----------------------------


def test_fit_window_that_excludes_a_face_is_dropped():
    # Centred face at the right edge: its backdrop copy is off-frame (blur
    # share 0), so the fit itself is fine — but the detail evidence sits at
    # the left, and the readable-fit window [0, 1152] would cut the face
    # out of the sharp layer entirely, leaving only the blurred backdrop.
    face = (1450.0, 420.0, 200.0, 240.0)
    evidence = _distributed(1, 0.104)
    evidence.update(detail_left=0.05, detail_right=0.3)
    analysis = _analysis([[face]] * 18, evidence)
    plan = reframe_plan.build_reframe_plan(analysis, [(0.0, 6.0)], 6.0)
    shot = plan["shots"][0]
    assert shot["mode"] == "fit"
    zoomed = [w for w in shot.get("fit_windows") or [] if w[2] is not None]
    assert zoomed == []
