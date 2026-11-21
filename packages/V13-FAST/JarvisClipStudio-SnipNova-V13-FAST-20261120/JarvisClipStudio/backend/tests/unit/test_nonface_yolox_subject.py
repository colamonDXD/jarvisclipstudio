"""Gated YOLOX person pass (BUILD-2 of the non-face spike).

People whose faces the face detector cannot see at all — helmets,
backs of heads, night shots — get a subject extent from a YOLOX
person pass that runs ONLY where it is needed: the sample has no
significant face, and the bright-extent signal (BUILD-1) did not
already claim it. The spike's rules are the contract
(NONFACE_SUBJECT_SPIKE.md): person class only, score >= 0.5, and
the winning boxes feed the SAME SceneEvidence.subject_box the
bright subject feeds — so the fallback anchor, guard envelopes and
amputation audit treat them identically. The pass costs ~0.7 s per
frame ungated; the gate tests below are the cost control.
"""

import dataclasses
import json

import numpy as np
import pytest

from src.media import person_detection, reframe_plan
from src.media.person_detection import _anchor_grids, _decode_persons
from src.media.reframe_plan import (
    _audit_subject_amputation,
    _fallback_crop,
    _subject_persistent,
)
from src.media.scene_context import ContextAnalyzer, SceneEvidence

W, H = 1920, 1080
PERSON = (0.3, 0.2, 0.55, 0.8, 0.9)  # normalized xyxy + score


class CountingPersonDetector:
    """Stub detector: records every call, returns canned persons."""

    def __init__(self, persons=()):
        self.persons = list(persons)
        self.calls = 0

    def detect_persons(self, frame):
        self.calls += 1
        return list(self.persons)


def _lit_frame():
    return np.full((270, 480, 3), 180, np.uint8)


def _dark_frame_with_blob():
    rng = np.random.default_rng(3)
    frame = np.full((270, 480, 3), 12, np.uint8)
    frame[40:110, 300:430] = rng.integers(190, 256, (70, 130, 1))
    return frame


# --- The gate --------------------------------------------------------------------


def test_gate_never_runs_when_a_face_is_present():
    stub = CountingPersonDetector([PERSON])
    evidence = ContextAnalyzer(person_detector=stub).measure(
        _lit_frame(), [(300.0, 40.0, 130.0, 70.0)], 0.0
    )
    assert stub.calls == 0
    assert evidence.face_count == 1
    assert evidence.subject_box is None
    assert evidence.subject_source is None


def test_gate_runs_on_no_face_sample_and_claims_subject():
    stub = CountingPersonDetector([PERSON])
    evidence = ContextAnalyzer(person_detector=stub).measure(
        _lit_frame(), [], 0.0
    )
    assert stub.calls == 1
    assert evidence.subject_source == "yolox"
    assert evidence.subject_box == pytest.approx(PERSON[:4])
    assert evidence.subject_cov == pytest.approx(0.25 * 0.6)


def test_gate_bright_claims_first_and_detector_never_runs():
    # The drone-portrait case: bright sees the subject, YOLOX provably
    # cannot — and bright is ~free. Precedence is bright's, and the
    # expensive pass is skipped entirely.
    stub = CountingPersonDetector([PERSON])
    evidence = ContextAnalyzer(person_detector=stub).measure(
        _dark_frame_with_blob(), [], 0.0
    )
    assert evidence.subject_source == "bright"
    assert stub.calls == 0


def test_no_detector_means_no_pass_and_no_subject():
    evidence = ContextAnalyzer().measure(_lit_frame(), [], 0.0)
    assert evidence.subject_box is None
    assert evidence.subject_source is None


def test_detector_returning_nothing_leaves_sample_subjectless():
    stub = CountingPersonDetector([])
    evidence = ContextAnalyzer(person_detector=stub).measure(
        _lit_frame(), [], 0.0
    )
    assert stub.calls == 1
    assert evidence.subject_box is None
    assert evidence.subject_cov == 0.0
    assert evidence.subject_source is None


def test_detector_failure_never_breaks_the_evidence():
    class ExplodingDetector:
        def detect_persons(self, frame):
            raise RuntimeError("net exploded")

    evidence = ContextAnalyzer(
        person_detector=ExplodingDetector()
    ).measure(_lit_frame(), [], 0.0)
    assert evidence.subject_box is None
    assert evidence.subject_source is None


def test_multiple_persons_union_into_one_subject_extent():
    stub = CountingPersonDetector(
        [(0.1, 0.2, 0.3, 0.7, 0.9), (0.6, 0.1, 0.8, 0.9, 0.8)]
    )
    evidence = ContextAnalyzer(person_detector=stub).measure(
        _lit_frame(), [], 0.0
    )
    assert evidence.subject_box == pytest.approx((0.1, 0.1, 0.8, 0.9))
    assert evidence.subject_cov == pytest.approx(0.7 * 0.8)
    assert evidence.subject_source == "yolox"


# --- Decode: person-class / confidence filtering ------------------------------------


def _raw_output():
    return np.zeros((8400, 85), dtype=np.float32)


def _plant(raw, row, center_cell, wh_px, obj, class_id, class_score, stride=8):
    # Box centre lands at (cell + raw offset) * stride; sizes are
    # exp(raw) * stride. Offsets 0.5 put the centre mid-cell.
    raw[row, 0:2] = 0.5
    raw[row, 2:4] = np.log(wh_px / stride)
    raw[row, 4] = obj
    raw[row, 5 + class_id] = class_score
    return row


def test_decode_keeps_only_confident_persons():
    grids, expanded = _anchor_grids()
    raw = _raw_output()
    # Stride-8 grid is 80x80, row-major (y, x): cell (40, 40) -> row 3240.
    _plant(raw, 3240, (40, 40), 160.0, 0.95, 0, 0.95)   # person 0.90
    _plant(raw, 810, (10, 10), 160.0, 0.99, 2, 0.99)    # car 0.98
    _plant(raw, 4860, (60, 60), 160.0, 0.6, 0, 0.6)     # person 0.36
    persons = _decode_persons(raw, 1.0, 640, 640, grids, expanded)
    assert len(persons) == 1
    x0, y0, x1, y1, score = persons[0]
    assert score == pytest.approx(0.9025, abs=0.01)
    assert (x0, y0, x1, y1) == pytest.approx(
        (244 / 640, 244 / 640, 404 / 640, 404 / 640), abs=0.01
    )


def test_decode_empty_output_is_no_persons():
    grids, expanded = _anchor_grids()
    assert _decode_persons(_raw_output(), 1.0, 640, 640, grids, expanded) == []


# --- Lazy detector -------------------------------------------------------------------


def test_lazy_detector_opens_once_and_delegates(monkeypatch):
    stub = CountingPersonDetector([PERSON])
    opens = []

    def fake_open():
        opens.append(1)
        return stub

    monkeypatch.setattr(person_detection, "open_yolox_detector", fake_open)
    lazy = person_detection._LazyYoloxPersonDetector()
    assert lazy.detect_persons(_lit_frame()) == [PERSON]
    assert lazy.detect_persons(_lit_frame()) == [PERSON]
    assert opens == [1]
    assert stub.calls == 2


def test_lazy_detector_failure_disables_without_retry(monkeypatch):
    opens = []

    def fake_open():
        opens.append(1)
        return None

    monkeypatch.setattr(person_detection, "open_yolox_detector", fake_open)
    lazy = person_detection._LazyYoloxPersonDetector()
    assert lazy.detect_persons(_lit_frame()) == []
    assert lazy.detect_persons(_lit_frame()) == []
    assert opens == [1]


# --- Evidence plumbing -----------------------------------------------------------------


def test_yolox_evidence_round_trips_through_cache_serialization():
    evidence = SceneEvidence(
        time=1.0, subject_box=(0.3, 0.2, 0.55, 0.8),
        subject_cov=0.15, subject_source="yolox",
    )
    restored = SceneEvidence(**json.loads(json.dumps(dataclasses.asdict(evidence))))
    assert [round(v, 3) for v in restored.subject_box] == [0.3, 0.2, 0.55, 0.8]
    assert restored.subject_cov == 0.15
    assert restored.subject_source == "yolox"


def test_cache_schema_v4_rejects_v3_payload(tmp_path):
    # Advanced one version by the min-area floor fix (cache schema
    # v5) and again by the depicted grid screen (v6): the rejection
    # now applies to every older payload, v3 included.
    assert reframe_plan.REFRAME_CACHE_SCHEMA_VERSION == 6
    source = tmp_path / "source.mp4"
    source.write_bytes(b"x" * 10)
    payload = {
        "version": 3,
        "source_size": 10,
        "source_mtime_ns": source.stat().st_mtime_ns,
        "width": W, "height": H, "sample_fps": 3.0,
        "covered_spans": [], "samples": [], "cuts": [], "context": [],
    }
    reframe_plan.reframe_cache_path(source).write_text(json.dumps(payload))
    assert reframe_plan._load_cache(source) is None


# --- Consumers, via the yolox source -----------------------------------------------------


def _yolox_context(box, count=18):
    return [
        SceneEvidence(
            time=i / 3.0, subject_box=box, subject_cov=0.15,
            subject_source="yolox",
        )
        for i in range(count)
    ]


def test_fallback_crop_anchors_on_yolox_subject_centre():
    entry = {"start": 0.0, "end": 6.0}
    x, reason = _fallback_crop(
        entry, _yolox_context((0.7, 0.2, 0.9, 0.5)), W, 606, None
    )
    assert reason == "saliency_centroid"
    assert x == pytest.approx(0.8 * W - 303, abs=2)


def test_audit_counts_sliced_yolox_subject():
    shot = {
        "start": 0.0, "end": 2.0,
        "pieces": [{"start": 0.0, "end": 2.0, "keys": [[0.0, 0]]}],
    }
    context = _yolox_context((0.26, 0.2, 0.42, 0.5), count=6)
    audit = _audit_subject_amputation(shot, context, 606, W)
    assert audit == {"samples": 6, "samples_cut": 6}
    assert _subject_persistent(audit)


def test_single_segment_guard_boxes_include_yolox_subject(monkeypatch):
    # One face at the left; a yolox person just right of it, well
    # inside any window the face decides. The resolved segment's
    # guard boxes must carry BOTH envelopes, like the bright case.
    context = []
    for i in range(18):
        context.append(
            SceneEvidence(
                time=i / 3.0, face_area=0.0175, face_count=1,
                face_span=0.094, face_center=0.151,
                subject_box=(0.10, 0.30, 0.20, 0.55),
                subject_cov=0.15, subject_source="yolox",
            )
        )
    analysis = {
        "width": W,
        "height": H,
        "samples": [
            (i / 3.0, [(200.0, 300.0, 180.0, 220.0)]) for i in range(18)
        ],
        "context": context,
        "cuts": [],
    }
    monkeypatch.setattr(
        reframe_plan, "get_source_analysis", lambda path, spans: analysis
    )
    edl = {
        "clip_id": "yolox-subject",
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
        edl, Path("source.mp4"), [(0.0, 6.0)]
    ) is True
    entries = edl["segments"][0]["reframe"]["face_boxes"]
    assert len(entries) == 1
    assert len(entries[0]["boxes"]) == 2
