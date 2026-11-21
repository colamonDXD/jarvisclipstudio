"""Legacy-chain Haar gate: in detect_faces_in_clip, Haar runs ONLY when
no neural detector was available on the frame.

The chain's Haar stage used to run whenever YuNet's (and the DNN's)
per-frame box list was empty, collapsing three distinct situations —
a neural stage loaded and found nothing, failed to load, raised — into
one gate. The measurement spike (LEGACY_CHAIN_SPIKE.md, 168 real frames
through the production functions) found every box Haar supplied on
neural-empty frames was a phantom (59/59, zero real-face saves), and
48/59 survived into the crop / split centers this function returns.
These tests pin the new contract, mirroring test_haar_gate.py:

- A neural stage ran (any result, empty included) -> its verdict
  stands, Haar is never called on that frame.
- No neural stage available (YuNet None / detect() raised AND the DNN
  net None / forward() raised) -> Haar runs with its historical
  parameters and its centers flow through as before.

The detectors are stubbed via _open_face_detectors and, for the DNN
cases, cv2.dnn.readNetFromTensorflow; frames come from a tiny generated
video, so the function runs end to end exactly as production calls it.
In this environment the real DNN load raises on the wheel's stub model
file, so the unpatched cases exercise the shipped dnn_net=None reality.
"""

import logging

import cv2
import numpy as np
import pytest

from src.media import reframing
from src.media.reframing import detect_faces_in_clip

# 640x360 analysis frame; the chain's own band (0.005 < rel area < 0.3)
# puts both canned faces comfortably inside.
FRAME_W, FRAME_H = 640, 360
YUNET_FACE = (100.0, 60.0, 60.0, 60.0, 0.9)  # x, y, w, h, conf
YUNET_CENTER = (130, 90)
HAAR_BOX = (200, 80, 62, 62)  # x, y, w, h
HAAR_CENTER = (231, 111)
# DNN detection row: conf 0.9, box (0.20, 0.20) - (0.35, 0.45) normalised
# -> (128, 72, 96, 90) px at 640x360.
DNN_DETECTION = np.array(
    [[[[0.0, 0.0, 0.9, 0.20, 0.20, 0.35, 0.45]]]], dtype=np.float32
)
DNN_CENTER = (176, 117)


@pytest.fixture(scope="module")
def tiny_video(tmp_path_factory):
    path = tmp_path_factory.mktemp("legacy_chain") / "tiny.avi"
    writer = cv2.VideoWriter(
        str(path), cv2.VideoWriter_fourcc(*"MJPG"), 20, (FRAME_W, FRAME_H)
    )
    assert writer.isOpened()
    for _ in range(20):  # 1.0 s at 20 fps
        writer.write(np.full((FRAME_H, FRAME_W, 3), 40, np.uint8))
    writer.release()
    return path


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


class StubDnnNet:
    """Stub DNN net: forward() returns canned detections or raises."""

    def __init__(self, detections=None, raises=False):
        self.detections = (
            detections
            if detections is not None
            else np.zeros((1, 1, 0, 7), dtype=np.float32)
        )
        self.raises = raises
        self.forward_calls = 0

    def setInput(self, blob):  # noqa: N802 - cv2 API name
        pass

    def forward(self):
        self.forward_calls += 1
        if self.raises:
            raise RuntimeError("dnn boom")
        return self.detections


def _run(monkeypatch, video, yunet, haar, dnn_net=None):
    monkeypatch.setattr(
        reframing, "_open_face_detectors", lambda: (yunet, haar)
    )
    if dnn_net is not None:
        monkeypatch.setattr(
            cv2.dnn, "readNetFromTensorflow", lambda *a, **k: dnn_net
        )
    # Window [0, 1.0): 10 sampled frames at the 0.1 s interval.
    return detect_faces_in_clip(video, 0.0, 1.0)


def _centers(result):
    return {(cx, cy) for cx, cy, _area, _conf in result}


# --- A neural stage ran: its verdict stands, Haar never runs --------------------


def test_yunet_empty_verdict_stands_haar_never_called(monkeypatch, tiny_video):
    yunet = CountingYuNet(faces=[])
    haar = CountingHaar(boxes=[HAAR_BOX])
    assert _run(monkeypatch, tiny_video, yunet, haar) == []
    assert yunet.calls >= 1
    assert haar.calls == 0


def test_yunet_sub_minimum_boxes_still_count_as_ran(monkeypatch, tiny_video):
    # A YuNet box at/below the 30 px keep-size is filtered out of
    # detected_faces, but detect() RAN — the old `if not detected_faces:`
    # gate ran Haar here; the new gate must not.
    yunet = CountingYuNet(faces=[(10.0, 10.0, 20.0, 20.0, 0.9)])
    haar = CountingHaar(boxes=[HAAR_BOX])
    assert _run(monkeypatch, tiny_video, yunet, haar) == []
    assert haar.calls == 0


def test_yunet_faces_flow_through_haar_never_called(monkeypatch, tiny_video):
    yunet = CountingYuNet(faces=[YUNET_FACE])
    haar = CountingHaar(boxes=[HAAR_BOX])
    result = _run(monkeypatch, tiny_video, yunet, haar)
    assert result
    assert _centers(result) == {YUNET_CENTER}
    assert haar.calls == 0


def test_dnn_ran_empty_blocks_haar_when_yunet_unavailable(
    monkeypatch, tiny_video
):
    # YuNet is down, but the DNN stage is available and ran (empty):
    # its verdict stands too. This is the availability form of the
    # gate — it stays correct if a real DNN model ever ships.
    net = StubDnnNet()
    haar = CountingHaar(boxes=[HAAR_BOX])
    assert _run(monkeypatch, tiny_video, None, haar, dnn_net=net) == []
    assert net.forward_calls >= 1
    assert haar.calls == 0


def test_dnn_detection_flows_through_haar_never_called(
    monkeypatch, tiny_video
):
    net = StubDnnNet(detections=DNN_DETECTION)
    haar = CountingHaar(boxes=[HAAR_BOX])
    result = _run(monkeypatch, tiny_video, None, haar, dnn_net=net)
    assert result
    assert _centers(result) == {DNN_CENTER}
    assert haar.calls == 0


# --- No neural stage available: Haar runs exactly as before ----------------------


def test_yunet_unavailable_runs_haar_and_centers_flow(
    monkeypatch, tiny_video, caplog
):
    haar = CountingHaar(boxes=[HAAR_BOX])
    with caplog.at_level(logging.WARNING):
        result = _run(monkeypatch, tiny_video, None, haar)
    assert result
    assert _centers(result) == {HAAR_CENTER}
    assert haar.calls >= 1
    # Historical cascade parameters, unchanged.
    assert haar.kwargs["scaleFactor"] == 1.05
    assert haar.kwargs["minNeighbors"] == 3
    assert haar.kwargs["minSize"] == (40, 40)
    assert haar.kwargs["maxSize"] == (int(FRAME_W * 0.7), int(FRAME_H * 0.7))
    # The degraded path is visible in logs when Haar supplies faces.
    assert any("Haar fallback supplied 1" in r.message for r in caplog.records)


def test_yunet_raise_runs_haar(monkeypatch, tiny_video):
    yunet = CountingYuNet(raises=True)
    haar = CountingHaar(boxes=[HAAR_BOX])
    result = _run(monkeypatch, tiny_video, yunet, haar)
    assert result
    assert _centers(result) == {HAAR_CENTER}
    assert yunet.calls >= 1
    assert haar.calls >= 1


def test_dnn_raise_and_yunet_unavailable_runs_haar(monkeypatch, tiny_video):
    net = StubDnnNet(raises=True)
    haar = CountingHaar(boxes=[HAAR_BOX])
    result = _run(monkeypatch, tiny_video, None, haar, dnn_net=net)
    assert result
    assert _centers(result) == {HAAR_CENTER}
    assert net.forward_calls >= 1
    assert haar.calls >= 1


def test_both_detectors_unavailable_returns_empty(monkeypatch, tiny_video):
    # haar=None raises AttributeError inside the guarded block, exactly
    # as before the gate change; the result is an empty list.
    assert _run(monkeypatch, tiny_video, None, None) == []
