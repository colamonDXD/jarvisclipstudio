"""Depicted-face grid screen (companion to the min-area floor fix).

Some detector "faces" are pictures of faces: printed portrait walls
whose uniform portraits sit in manufactured rows. Taken at face value
they inflate face_count / face_span, join the protect spans and close
the bright / YOLOX gates. DEPICTED_FACES_SPIKE.md verdicted all 158
sub-threshold boxes of its corpus and found exactly one cheap signal
that separates them: grid membership — a box in an aligned row of
>= 3 similar-size boxes is depicted in 39/69 depicted cases and
0/63 real cases. Stillness and texture were measured and refuted
(a motionless real person is exactly as still as a print).

The screen's contract, pinned below:
- scope is the sub-0.002 band only — the population the reframing
  floor change (0.002 -> 0.0005) newly admits. Above-band faces are
  NEVER demoted: a real row of people at speaking scale (a team
  lineup) is untouchable by this rule, by construction.
- the rule is the spike's, as measured: >= 2 row partners with
  centre-y within 0.4 x the candidate's height and width within
  +/-30% of the candidate's width, partners counted inside the
  band only.
- demote, never delete: demoted boxes leave the significant set
  (face evidence, protect spans, non-face gates) and are counted
  on SceneEvidence as depicted_count.
"""

import dataclasses
import json

import numpy as np
import pytest

from src.media import reframe_plan
from src.media.scene_context import ContextAnalyzer, SceneEvidence

W, H = 480, 854
PERSON = (0.3, 0.2, 0.55, 0.8, 0.9)  # normalized xyxy + score


def _frame():
    return np.full((H, W, 3), 180, np.uint8)


def _wall_box(x, y=100.0):
    # 20x26 px on a 480x854 frame: area fraction 0.00127 — inside the
    # sub-0.002 band, above the 0.0005 reframing floor.
    return (float(x), float(y), 20.0, 26.0)


def _wall_row(n, y=100.0, x0=40.0, step=50.0):
    return [_wall_box(x0 + i * step, y) for i in range(n)]


class CountingPersonDetector:
    """Stub detector: records every call, returns canned persons."""

    def __init__(self, persons=()):
        self.persons = list(persons)
        self.calls = 0

    def detect_persons(self, frame):
        self.calls += 1
        return list(self.persons)


# --- The demotion ------------------------------------------------------------------


def test_portrait_wall_row_is_demoted_and_counted():
    evidence = ContextAnalyzer().measure(_frame(), _wall_row(5), 0.0)
    assert evidence.depicted_count == 5
    assert evidence.face_count == 0
    assert evidence.face_span == 0.0


def test_minimum_grid_of_three_is_demoted():
    # Each box has exactly the required 2 row partners.
    evidence = ContextAnalyzer().measure(_frame(), _wall_row(3), 0.0)
    assert evidence.depicted_count == 3
    assert evidence.face_count == 0


def test_wall_demotion_keeps_a_real_presenter():
    presenter = (150.0, 60.0, 90.0, 120.0)  # area fraction 0.026
    boxes = [presenter] + _wall_row(4, y=300.0)
    evidence = ContextAnalyzer().measure(_frame(), boxes, 0.0)
    assert evidence.depicted_count == 4
    assert evidence.face_count == 1
    assert evidence.face_center == pytest.approx((150.0 + 45.0) / W)


def test_demoted_wall_does_not_join_protect_spans():
    frame = _frame()
    base = ContextAnalyzer().measure(frame, [], 0.0)
    walled = ContextAnalyzer().measure(frame, _wall_row(5), 0.0)
    assert (walled.protect_left, walled.protect_right) == (
        base.protect_left,
        base.protect_right,
    )


# --- The band scope: the adversarial guard ------------------------------------------


def test_row_of_large_faces_is_never_demoted():
    # A team lineup at speaking scale: aligned, uniform, in a row —
    # and above the band, so the rule cannot touch it.
    boxes = [(40.0 + i * 100.0, 100.0, 40.0, 60.0) for i in range(4)]
    evidence = ContextAnalyzer().measure(_frame(), boxes, 0.0)
    assert evidence.depicted_count == 0
    assert evidence.face_count == 4


def test_above_band_neighbour_neither_condemns_nor_rescues():
    # Two in-band boxes in a row have only each other as partners;
    # the aligned above-band box in the same row does not count as a
    # partner (the spike's rule counts partners inside the band
    # only — this is its measured miss class, kept deliberately).
    boxes = _wall_row(2) + [(140.0, 100.0, 33.0, 50.0)]
    evidence = ContextAnalyzer().measure(_frame(), boxes, 0.0)
    assert evidence.depicted_count == 0
    assert evidence.face_count == 3


# --- Below the grid threshold: kept ---------------------------------------------------


def test_single_small_face_is_not_demoted():
    evidence = ContextAnalyzer().measure(_frame(), [_wall_box(40.0)], 0.0)
    assert evidence.depicted_count == 0
    assert evidence.face_count == 1


def test_two_small_faces_are_not_demoted():
    # A pair has one partner each; the rule needs two.
    evidence = ContextAnalyzer().measure(_frame(), _wall_row(2), 0.0)
    assert evidence.depicted_count == 0
    assert evidence.face_count == 2


def test_width_mismatch_breaks_the_row():
    boxes = [
        (40.0, 100.0, 20.0, 26.0),
        (90.0, 100.0, 20.0, 26.0),
        (140.0, 100.0, 44.0, 26.0),  # >30% wider than the pair
    ]
    evidence = ContextAnalyzer().measure(_frame(), boxes, 0.0)
    assert evidence.depicted_count == 0
    assert evidence.face_count == 3


def test_misaligned_centre_y_breaks_the_row():
    boxes = _wall_row(2) + [_wall_box(140.0, y=140.0)]  # centre-y off by 40
    evidence = ContextAnalyzer().measure(_frame(), boxes, 0.0)
    assert evidence.depicted_count == 0
    assert evidence.face_count == 3


# --- Gates: a depicted-only sample is a no-face sample --------------------------------


def test_demoted_only_sample_opens_the_yolox_gate():
    stub = CountingPersonDetector([PERSON])
    evidence = ContextAnalyzer(person_detector=stub).measure(
        _frame(), _wall_row(4), 0.0
    )
    assert evidence.face_count == 0
    assert evidence.depicted_count == 4
    assert stub.calls == 1
    assert evidence.subject_source == "yolox"


def test_real_face_keeps_the_gate_closed_with_wall_present():
    stub = CountingPersonDetector([PERSON])
    boxes = [(150.0, 60.0, 90.0, 120.0)] + _wall_row(4, y=300.0)
    evidence = ContextAnalyzer(person_detector=stub).measure(
        _frame(), boxes, 0.0
    )
    assert evidence.face_count == 1
    assert evidence.depicted_count == 4
    assert stub.calls == 0
    assert evidence.subject_box is None


# --- Cache: v5 evidence predates the screen -------------------------------------------


def test_depicted_count_round_trips_through_cache_serialization():
    evidence = SceneEvidence(time=1.0, depicted_count=7)
    restored = SceneEvidence(
        **json.loads(json.dumps(dataclasses.asdict(evidence)))
    )
    assert restored.depicted_count == 7


def test_cache_schema_v6_rejects_v5_payload(tmp_path):
    # A v5 cache holds evidence computed without the grid screen;
    # reading it back would silently keep wall portraits in the face
    # evidence. The schema bump (see reframe_plan.py's version
    # history) discards it.
    assert reframe_plan.REFRAME_CACHE_SCHEMA_VERSION == 6
    source = tmp_path / "source.mp4"
    source.write_bytes(b"x" * 10)
    payload = {
        "version": 5,
        "source_size": 10,
        "source_mtime_ns": source.stat().st_mtime_ns,
        "width": W, "height": H, "sample_fps": 3.0,
        "covered_spans": [], "samples": [], "cuts": [], "context": [],
    }
    reframe_plan.reframe_cache_path(source).write_text(json.dumps(payload))
    assert reframe_plan._load_cache(source) is None
