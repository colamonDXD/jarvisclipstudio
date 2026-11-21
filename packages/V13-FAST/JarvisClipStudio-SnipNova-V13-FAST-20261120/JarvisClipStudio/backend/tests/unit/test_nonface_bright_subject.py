"""Bright-extent subject presence (BUILD-1 of the non-face spike).

Subjects no face detector can see — the drone light-portrait of the
MrBeast m4 exports — get a measured extent from the largest bright
component of a dark scene, and that extent feeds the same consumers
face boxes feed: the fallback anchor, the caption/title guard
envelopes, and the amputation audit. The gates are the spike's
measured ones (NONFACE_SUBJECT_SPIKE.md): dark scenes only (mean
luminance <= 90; the portrait frames measure 32/40, the sunlit
forest 107), total bright coverage >= 0.05 (present 0.12-0.20,
absent 0.037). Synthetic analyses mirror test_reframe_amputation:
1920x1080, 3 fps over 6 s, standard window 606x1080.
"""

import dataclasses
import json

import numpy as np
import pytest

from src.media import reframe_plan
from src.media.reframe_plan import (
    _audit_subject_amputation,
    _fallback_crop,
    _subject_envelope,
    _subject_persistent,
    build_plain_reframe_plan,
    build_reframe_plan,
)
from src.media.scene_context import ContextAnalyzer, SceneEvidence

W, H = 1920, 1080
FRAME_AREA = float(W * H)
SUBJECT_FACE = (150.0, 450.0, 150.0, 150.0)
# Bright subject whose pixel span (499-806) the face-following
# window's right edge (606) passes through.
SUBJECT_BOX = (0.26, 0.2, 0.42, 0.5)


def _dark_frame(blob=True, base=12):
    rng = np.random.default_rng(3)
    frame = np.full((270, 480, 3), base, np.uint8)
    if blob:
        frame[40:110, 300:430] = rng.integers(190, 256, (70, 130, 1))
    return frame


# --- measure() -----------------------------------------------------------------


def test_measure_finds_bright_subject_on_dark_scene():
    evidence = ContextAnalyzer().measure(_dark_frame(), [], 0.0)
    assert evidence.subject_source == "bright"
    assert evidence.subject_box is not None
    x0, y0, x1, y1 = evidence.subject_box
    assert x0 == pytest.approx(0.625, abs=0.01)
    assert y0 == pytest.approx(0.148, abs=0.01)
    assert x1 == pytest.approx(0.896, abs=0.01)
    assert y1 == pytest.approx(0.407, abs=0.01)
    assert evidence.subject_cov >= 0.05


def test_measure_dark_gate_rejects_lit_scene():
    # Same bright blob on a lit scene: the mask degenerates there
    # (sky/foliage), so the signal must stay silent.
    frame = _dark_frame(base=180)
    evidence = ContextAnalyzer().measure(frame, [], 0.0)
    assert evidence.subject_box is None
    assert evidence.subject_cov == 0.0
    assert evidence.subject_source is None


def test_measure_coverage_gate_rejects_specks():
    rng = np.random.default_rng(5)
    frame = np.full((270, 480, 3), 12, np.uint8)
    for _ in range(40):
        y, x = rng.integers(0, 270), rng.integers(0, 480)
        frame[y, x] = rng.integers(200, 256, 3)
    evidence = ContextAnalyzer().measure(frame, [], 0.0)
    assert evidence.subject_box is None
    assert evidence.subject_source is None


def test_measure_subject_is_independent_of_faces():
    evidence = ContextAnalyzer().measure(
        _dark_frame(), [(300.0, 40.0, 130.0, 70.0)], 0.0
    )
    assert evidence.face_count == 1
    assert evidence.subject_box is not None
    assert evidence.subject_source == "bright"


# --- Evidence plumbing -----------------------------------------------------------


def test_evidence_round_trips_through_cache_serialization():
    evidence = SceneEvidence(
        time=1.0, subject_box=(0.26, 0.2, 0.42, 0.5),
        subject_cov=0.12, subject_source="bright",
    )
    restored = SceneEvidence(**json.loads(json.dumps(dataclasses.asdict(evidence))))
    assert [round(v, 3) for v in restored.subject_box] == [0.26, 0.2, 0.42, 0.5]
    assert restored.subject_cov == 0.12
    assert restored.subject_source == "bright"


def test_cache_schema_v3_rejects_v2_payload(tmp_path):
    # Advanced one version by BUILD-2 (cache schema v4), again by
    # the min-area floor fix (v5) and again by the depicted grid
    # screen (v6): the rejection this test pins now applies to every
    # older payload, v2 included.
    assert reframe_plan.REFRAME_CACHE_SCHEMA_VERSION == 6
    source = tmp_path / "source.mp4"
    source.write_bytes(b"x" * 10)
    payload = {
        "version": 2,
        "source_size": 10,
        "source_mtime_ns": source.stat().st_mtime_ns,
        "width": W, "height": H, "sample_fps": 3.0,
        "covered_spans": [], "samples": [], "cuts": [], "context": [],
    }
    reframe_plan.reframe_cache_path(source).write_text(json.dumps(payload))
    assert reframe_plan._load_cache(source) is None


# --- Fallback anchor ---------------------------------------------------------------


def test_fallback_crop_anchors_on_subject_centre():
    entry = {"start": 0.0, "end": 6.0}
    with_subject = [
        SceneEvidence(
            time=i / 3.0, subject_box=(0.7, 0.2, 0.9, 0.5),
            subject_cov=0.1, subject_source="bright",
        )
        for i in range(18)
    ]
    x, reason = _fallback_crop(entry, with_subject, W, 606, None)
    assert reason == "saliency_centroid"
    assert x == pytest.approx(0.8 * W - 303, abs=2)
    without = [SceneEvidence(time=i / 3.0) for i in range(18)]
    x_plain, _ = _fallback_crop(entry, without, W, 606, None)
    assert x_plain == pytest.approx(0.5 * W - 303, abs=2)
    assert x != x_plain


# --- Audit machinery, directly -----------------------------------------------------


def _subject_context(box=SUBJECT_BOX, count=18):
    return [
        SceneEvidence(
            time=i / 3.0, subject_box=box, subject_cov=0.12,
            subject_source="bright",
        )
        for i in range(count)
    ]


def test_subject_envelope_unions_shot_boxes():
    context = _subject_context((0.1, 0.1, 0.3, 0.4), count=1) + [
        SceneEvidence(
            time=0.5, subject_box=(0.5, 0.2, 0.7, 0.6),
            subject_cov=0.1, subject_source="bright",
        )
    ]
    assert _subject_envelope(context, 0.0, 1.0, W, H) == [
        [192.0, 108.0, 1152.0, 540.0]
    ]
    assert _subject_envelope(context, 2.0, 3.0, W, H) == []
    assert _subject_envelope([], 0.0, 1.0, W, H) == []


def test_audit_subject_amputation_counts_sliced_samples():
    shot = {
        "start": 0.0, "end": 2.0,
        "pieces": [{"start": 0.0, "end": 2.0, "keys": [[0.0, 0]]}],
    }
    audit = _audit_subject_amputation(shot, _subject_context(count=6), 606, W)
    assert audit == {"samples": 6, "samples_cut": 6}
    assert _subject_persistent(audit)
    shot["pieces"][0]["keys"] = [[0.0, 400]]
    audit = _audit_subject_amputation(shot, _subject_context(count=6), 606, W)
    assert audit == {"samples": 6, "samples_cut": 0}
    assert not _subject_persistent(audit)
    assert _audit_subject_amputation(shot, [], 606, W) is None


# --- Plan level ---------------------------------------------------------------------


def _analysis(boxes_by_index, context):
    return {
        "width": W,
        "height": H,
        "samples": [(i / 3.0, list(boxes_by_index(i))) for i in range(18)],
        "context": context,
        "cuts": [],
    }


def _context(subject_box=None, **kwargs):
    base = dict(face_area=0.05, face_count=1, face_span=0.2)
    base.update(kwargs)
    if subject_box is not None:
        base.update(
            subject_box=subject_box, subject_cov=0.12, subject_source="bright"
        )
    return [SceneEvidence(time=i / 3.0, **base) for i in range(18)]


def test_single_plus_wide_subject_converts_to_fit():
    # The crop follows the face and its edge slices the bright
    # subject in every sample; face + subject span (656px) exceeds
    # the window, and the fit would not blur the face — the only
    # whole-subject layout is the full frame.
    plan = build_reframe_plan(
        _analysis(lambda i: [SUBJECT_FACE], _context(SUBJECT_BOX)),
        [(0.0, 6.0)], 6.0,
    )
    (shot,) = plan["shots"]
    assert shot["mode"] == "fit"
    assert shot["reason"] == "amputation_avoided_fit"
    assert shot["subject_amputation_avoided"] == {"how": "fit"}
    assert "subject_amputation" not in shot
    assert shot["subject_src"], "the subject envelope rides along for the guards"


def test_pair_with_fitting_subject_span_is_stamped_not_repaired():
    # Both hosts fit the window and the three-way span (face pair +
    # subject = 541px) fits too, so no repair rule fires — the cut
    # is measured and stamped instead of shipping silent.
    host_b = (350.0, 450.0, 150.0, 150.0)
    context = _context(
        (0.29, 0.2, 0.36, 0.5), face_count=2, face_span=0.182
    )
    plan = build_reframe_plan(
        _analysis(lambda i: [SUBJECT_FACE, host_b], context),
        [(0.0, 6.0)], 6.0,
    )
    (shot,) = plan["shots"]
    assert shot["mode"] == "pair"
    assert shot["subject_amputation"]["samples_cut"] >= 2
    assert "subject_amputation_avoided" not in shot


def test_static_fallback_reanchors_on_subject():
    # The context says a face-classified shot, but no face is ever
    # tracked: the fallback ladder decides. The subject appears only
    # in the last third, too late to dominate the anchor average —
    # the audit catches the held window slicing it and re-anchors.
    context = []
    for i in range(18):
        kwargs = dict(face_area=0.05, face_count=1, face_span=0.2)
        if i >= 12:
            kwargs.update(
                subject_box=(0.62, 0.2, 0.85, 0.5),
                subject_cov=0.12, subject_source="bright",
            )
        context.append(SceneEvidence(time=i / 3.0, **kwargs))
    plan = build_reframe_plan(
        _analysis(lambda i: [], context), [(0.0, 6.0)], 6.0
    )
    (shot,) = plan["shots"]
    assert shot["mode"] == "static"
    assert shot["subject_amputation_avoided"] == {"how": "subject_anchor"}
    assert "subject_amputation" not in shot
    keys = [key for piece in shot["pieces"] for key in piece["keys"]]
    # Re-anchored on the subject centre (0.735 * 1920 - 303).
    assert all(x == pytest.approx(0.735 * W - 303, abs=2) for _t, x in keys)


def test_plain_stamps_subject_amputation_without_repair():
    # Plain never re-anchors or letterboxes: a persistently sliced
    # bright subject is stamped, and the shot stays a plain crop.
    # The tight plain window for this face is [38, 412]; the subject
    # (380-700px) straddles its right edge in every sample.
    plan = build_plain_reframe_plan(
        _analysis(
            lambda i: [SUBJECT_FACE], _context((0.198, 0.2, 0.365, 0.5))
        ),
        [(0.0, 6.0)], 6.0,
    )
    (shot,) = plan["shots"]
    assert shot["mode"] == "plain"
    assert shot["subject_amputation"]["samples_cut"] >= 2
    assert "subject_amputation_avoided" not in shot
    assert shot["subject_src"]


# --- Guard envelopes on resolved segments -------------------------------------------


def _attach(monkeypatch, analysis, **kwargs):
    monkeypatch.setattr(
        reframe_plan, "get_source_analysis", lambda path, spans: analysis
    )
    edl = {
        "clip_id": "bright-subject",
        "duration": 6.0,
        "source_ranges": [[0.0, 6.0]],
        "segments": [
            {
                "index": 0, "start": 0.0, "end": 6.0,
                "transition_in": "none", "transition_duration": 0,
            }
        ],
    }
    from pathlib import Path

    assert reframe_plan.attach_reframe_plan(
        edl, Path("source.mp4"), [(0.0, 6.0)], **kwargs
    ) is True
    return edl


def _guard_analysis():
    # One face at the left; a bright subject just right of it, well
    # inside any window the face decides.
    return _analysis(
        lambda i: [(200.0, 300.0, 180.0, 220.0)],
        _context(
            (0.10, 0.30, 0.20, 0.55),
            face_area=0.0175, face_span=0.094, face_center=0.151,
        ),
    )


def test_single_segment_guard_boxes_include_subject(monkeypatch):
    edl = _attach(monkeypatch, _guard_analysis())
    entries = edl["segments"][0]["reframe"]["face_boxes"]
    assert len(entries) == 1
    # Face envelope AND subject envelope, both through the crop.
    assert len(entries[0]["boxes"]) == 2


def test_fit_plan_guard_boxes_include_subject(monkeypatch):
    edl = _attach(monkeypatch, _guard_analysis(), framing="fit")
    entries = edl["segments"][0]["reframe"]["face_boxes"]
    assert len(entries) == 1
    assert len(entries[0]["boxes"]) == 2
