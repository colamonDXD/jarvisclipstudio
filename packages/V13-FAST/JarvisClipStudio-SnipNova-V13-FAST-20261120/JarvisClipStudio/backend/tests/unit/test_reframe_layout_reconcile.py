"""Clip-level layout reconciliation (the TJR framing flip).

Regression set for the v4 TJR export *"How $96K Winners Offset Daily
Losses"*: one clip shipped as a tight face crop for ~2.9 s and then as a
wide fit — the speaker small in a sharp strip over his own blurred
face — for the remaining ~25 s. The Auto planner decided each shot
independently: the wide shot's context reason was
``screen_content_outside_crop`` (trading boards with text rows at the
frame edge), a member of FIT_BLUR_EXEMPT_REASONS, so the fit-blur guard
never judged it, and the 2.5 s layout hold could not help because the
crop run was ~2.9 s.

The rules pinned here:

1. A content-preservation exemption ends at a grounded face track —
   the fit-blur guard judges face-bearing fits like any other.
2. In a subject clip (a grounded face track anchors a face-classified
   shot), a context fit carrying the subject is reconciled to the
   face-track layout even when the face would stay sharp in the strip:
   one clip, one framing, by construction — not by run length.
3. A fit that still survives in a subject clip records why on the
   shot (``layout_kept``): no face track to preserve, or the face
   tracks themselves decided the fit.

Geometry reference (1920x1080 -> 1080x1920): backdrop scale 1.7778,
foreground strip y 656..1264. The wide-shot face below lands ~30% of
its backdrop copy in the bands; the centred variant lands 0%.
"""

from __future__ import annotations

from src.media import reframe_plan
from src.media.scene_context import SceneEvidence

W, H = 1920, 1080
FPS = 3.0
DURATION = 28.6
CUT = 2.9

# Tight shot of the speaker (the clip's opening framing).
FACE_A = (700.0, 250.0, 320.0, 380.0)
# Wide shot of the same speaker, centre-frame.
FACE_B_BLURRING = (740.0, 270.0, 260.0, 330.0)  # ~30% of copy in bands
FACE_B_CENTRED = (740.0, 420.0, 260.0, 240.0)  # copy inside the strip


def _evidence_a(t):
    return SceneEvidence(
        time=t, face_area=0.0587, face_count=1, face_span=0.167,
        face_center=0.448,
    )


def _screen_evidence(face_area):
    def ev(t):
        return SceneEvidence(
            time=t, face_area=face_area, face_count=1, face_span=0.135,
            face_center=0.453, text_rows=4,
        )

    return ev


def _two_shot(face_b, evidence_b, evidence_a=_evidence_a):
    times = [i / FPS for i in range(int(DURATION * FPS) + 1)]
    samples = [
        (t, [FACE_A] if t < CUT else ([face_b] if face_b else []))
        for t in times
    ]
    context = [evidence_a(t) if t < CUT else evidence_b(t) for t in times]
    return {
        "width": W,
        "height": H,
        "samples": samples,
        "cuts": [CUT],
        "context": context,
    }


def _plan(analysis):
    plan = reframe_plan.build_reframe_plan(analysis, [(0.0, DURATION)], DURATION)
    assert plan is not None
    return plan


def test_tjr_sequence_plans_a_single_layout():
    # The Step-0 reproduction of the shipped defect: on the base code
    # this planned [single, fit(screen_content_outside_crop)] — the
    # exempt reason bypassed the guard although the face blurs (share
    # 0.30 >= 0.20).
    assert reframe_plan._fit_blur_share(FACE_B_BLURRING, W, H) > 0.2
    plan = _plan(_two_shot(FACE_B_BLURRING, _screen_evidence(0.0414)))
    shots = plan["shots"]
    assert [s["mode"] for s in shots] == ["single", "single"]
    assert shots[1]["reason"] == "face_blur_avoided"


def test_subject_clip_reconciles_a_non_blurring_fit():
    # The face's copy stays inside the strip, so the fit-blur guard has
    # nothing to veto — but the clip is a subject clip and the fit
    # carries the subject, so reconciliation re-decides it to the
    # face-track layout. On the base code this planned [single, fit].
    assert reframe_plan._fit_blur_share(FACE_B_CENTRED, W, H) == 0.0
    plan = _plan(_two_shot(FACE_B_CENTRED, _screen_evidence(0.0301)))
    shots = plan["shots"]
    assert shots[0]["mode"] == "single"
    assert all(s["mode"] != "fit" for s in shots)
    wide = next(s for s in shots if s["start"] >= CUT - 0.01)
    assert wide["mode"] == "single"
    assert wide.get("layout_reconciled") is True


def test_faceless_fit_survives_in_a_subject_clip_with_its_reason_recorded():
    # A genuine scene change: the wide shot has no face at all (screen
    # content only). The fit survives — and the plan records why.
    plan = _plan(_two_shot(None, _screen_evidence(0.0414)))
    shots = plan["shots"]
    assert [s["mode"] for s in shots] == ["single", "fit"]
    assert shots[1]["reason"] == "screen_content_outside_crop"
    assert shots[1].get("layout_kept") == "no_face_track"


def test_wide_group_fit_in_a_subject_clip_records_its_track_decision():
    # The wide shot's three faces are too wide to crop and none of them
    # blurs: the face-track ladder itself decides the fit. It survives,
    # recorded as a track decision (not a context override).
    faces = [
        (100.0, 450.0, 150.0, 180.0),
        (850.0, 450.0, 150.0, 180.0),
        (1600.0, 450.0, 150.0, 180.0),
    ]
    times = [i / FPS for i in range(int(DURATION * FPS) + 1)]
    samples = [(t, [FACE_A] if t < CUT else faces) for t in times]
    context = [
        _evidence_a(t)
        if t < CUT
        else SceneEvidence(
            time=t, face_area=0.013, face_count=3, face_span=0.859
        )
        for t in times
    ]
    analysis = {
        "width": W, "height": H, "samples": samples,
        "cuts": [CUT], "context": context,
    }
    plan = _plan(analysis)
    shots = plan["shots"]
    assert shots[0]["mode"] == "single"
    wide = [s for s in shots if s["start"] >= CUT - 0.01]
    assert wide and all(s["mode"] == "fit" for s in wide)
    assert all(s["reason"] == "wide_group" for s in wide)
    assert all(
        s.get("layout_kept") == "decided_from_face_tracks" for s in wide
    )


def test_faceless_clip_still_plans_fits():
    # No faces anywhere: nothing to reconcile, no subject layout to
    # protect — context fits stand as planned, unrecorded.
    def distributed(t):
        return SceneEvidence(
            time=t, face_area=0.02, face_count=0, face_span=0.0,
            motion=0.1, outside_motion=0.5, motion_spread=0.6,
        )

    def texty(t):
        return SceneEvidence(
            time=t, face_area=0.023, face_count=1, face_span=0.104,
            cut_text_lines=2,
        )

    times = [i / FPS for i in range(int(DURATION * FPS) + 1)]
    samples = [(t, []) for t in times]
    context = [distributed(t) if t < CUT else texty(t) for t in times]
    analysis = {
        "width": W, "height": H, "samples": samples,
        "cuts": [CUT], "context": context,
    }
    plan = _plan(analysis)
    assert plan["shots"]
    assert all(s["mode"] == "fit" for s in plan["shots"])
    assert all("layout_kept" not in s for s in plan["shots"])
