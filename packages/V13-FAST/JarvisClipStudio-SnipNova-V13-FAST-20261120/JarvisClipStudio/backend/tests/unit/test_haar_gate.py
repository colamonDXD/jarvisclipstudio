"""Haar fallback gate: Haar runs ONLY when YuNet is unavailable.

The Haar cascade used to run whenever YuNet's raw box list was empty,
collapsing three distinct situations — YuNet loaded and found nothing,
YuNet failed to load, YuNet raised — into one gate. The measurement
spike (HAAR_FP_SPIKE.md, 158 real frames at production 480w) found
every box Haar supplied on YuNet-empty frames was a phantom (17/17,
zero real-face saves), and one phantom face pre-empts the bright /
YOLOX subject gates downstream. These tests pin the new contract:

- YuNet ran (any result, empty included) -> its verdict stands, Haar
  is never called.
- YuNet unavailable (detector None, or detect() raised) -> Haar runs
  with its historical parameters and its boxes flow through the same
  shared post-filter as before.
"""

import logging

import numpy as np

from src.media.reframing import _detect_face_boxes

# 480x270 is the production analysis size; REFRAME_FACE_PRESENCE_MIN_AREA
# (0.0005) puts the shared post-filter floor at ~65 px^2 there.
FRAME_W, FRAME_H = 480, 270
YUNET_FACE = (100.0, 60.0, 60.0, 60.0, 0.9)  # x, y, w, h, conf
HAAR_BOX = (200, 80, 62, 62)  # x, y, w, h


def _frame():
    return np.zeros((FRAME_H, FRAME_W, 3), np.uint8)


class CountingYuNet:
    """Stub YuNet detector: records calls, returns canned faces or raises."""

    def __init__(self, faces=(), raises=False):
        self.faces = list(faces)
        self.raises = raises
        self.calls = 0

    def detect(self, frame):
        self.calls += 1
        if self.raises:
            raise RuntimeError("yunet boom")
        return list(self.faces)


class CountingHaar:
    """Stub Haar cascade: records calls, returns canned boxes."""

    def __init__(self, boxes=()):
        self.boxes = list(boxes)
        self.calls = 0
        self.kwargs = None

    def detectMultiScale(self, gray, **kwargs):
        self.calls += 1
        self.kwargs = kwargs
        return list(self.boxes)


# --- YuNet ran: its verdict stands, Haar never runs ------------------------------


def test_yunet_empty_verdict_stands_haar_never_called():
    yunet = CountingYuNet(faces=[])
    haar = CountingHaar(boxes=[HAAR_BOX])
    assert _detect_face_boxes(_frame(), yunet, haar) == []
    assert yunet.calls == 1
    assert haar.calls == 0


def test_yunet_sub_candidate_boxes_still_count_as_ran():
    # A YuNet box too small to enter the candidate list (fw/fh <= 10)
    # still means YuNet RAN — the old `if not boxes:` gate ran Haar
    # here; the new gate must not.
    yunet = CountingYuNet(faces=[(10.0, 10.0, 8.0, 8.0, 0.9)])
    haar = CountingHaar(boxes=[HAAR_BOX])
    assert _detect_face_boxes(_frame(), yunet, haar) == []
    assert haar.calls == 0


def test_yunet_boxes_pass_through_unchanged_haar_never_called():
    yunet = CountingYuNet(faces=[YUNET_FACE])
    haar = CountingHaar(boxes=[HAAR_BOX])
    assert _detect_face_boxes(_frame(), yunet, haar) == [(100.0, 60.0, 60.0, 60.0)]
    assert haar.calls == 0


# --- YuNet unavailable: Haar runs exactly as before -------------------------------


def test_yunet_unavailable_runs_haar_and_boxes_flow_through(caplog):
    haar = CountingHaar(boxes=[HAAR_BOX])
    with caplog.at_level(logging.WARNING):
        result = _detect_face_boxes(_frame(), None, haar)
    assert result == [(200.0, 80.0, 62.0, 62.0)]
    assert haar.calls == 1
    # Historical cascade parameters, unchanged.
    assert haar.kwargs["scaleFactor"] == 1.2
    assert haar.kwargs["minNeighbors"] == 3
    assert haar.kwargs["minSize"] == (19, 19)  # max(14, 4% of 480)
    assert haar.kwargs["maxSize"] == (336, 189)  # 70% of frame
    # The degraded path is visible in logs when Haar supplies boxes.
    assert any("Haar fallback supplied 1" in r.message for r in caplog.records)


def test_yunet_raise_runs_haar():
    yunet = CountingYuNet(raises=True)
    haar = CountingHaar(boxes=[HAAR_BOX])
    assert _detect_face_boxes(_frame(), yunet, haar) == [(200.0, 80.0, 62.0, 62.0)]
    assert yunet.calls == 1
    assert haar.calls == 1


def test_yunet_unavailable_haar_boxes_still_hit_shared_min_area_filter():
    tiny = (10, 10, 7, 7)  # 49 px^2 < the ~65 px^2 reframing floor at 480x270
    haar = CountingHaar(boxes=[tiny])
    assert _detect_face_boxes(_frame(), None, haar) == []
    assert haar.calls == 1


def test_both_detectors_unavailable_returns_empty():
    # haar=None raises AttributeError inside the guarded block, exactly
    # as before the gate change; the result is an empty list.
    assert _detect_face_boxes(_frame(), None, None) == []
