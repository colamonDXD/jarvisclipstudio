"""Per-path face-presence area floors: reframing 0.0005, speaker 0.002.

The two paths used to share FACE_PRESENCE_MIN_AREA = 0.002. The
measurement spike (MIN_AREA_SPIKE.md, 469 real frames) found that
floor discarding mostly real YuNet faces on wide shots — 95% of the
discarded boxes pointing at scene content were real (wide-shot
presenters, helmet visors, night groups) — and that 0.0005 recovers
89% of them. The podcast speaker path was not in the spike's corpus,
so it keeps 0.002 until a two-shot measurement says otherwise. These
tests pin the split:

- A ~0.001-area face is admitted by the reframing path and still
  filtered by the speaker path.
- A ~0.0004-area face is filtered by both.
- Behaviour at >= 0.002 is unchanged on both paths.
- build_layout_plan's presence re-test uses the reframing floor, so
  a face admitted at detection also counts as present in the layout
  plan (no split-brain between the two reframing sites).
"""

import json

import numpy as np

from src.media import reframe_plan
from src.media.common import FACE_PRESENCE_MIN_AREA, REFRAME_FACE_PRESENCE_MIN_AREA
from src.media.reframing import _detect_face_boxes, build_layout_plan
from src.media.speaker_framing import _detect_all_faces

# 480x854 is the spike's downstream geometry; frame area 409,920 px^2,
# so the floors sit at ~205 px^2 (reframing) and ~820 px^2 (speaker).
FRAME_W, FRAME_H = 480, 854
SMALL_FACE = (100.0, 100.0, 20.0, 20.0, 0.9)  # 400 px^2, frac ~0.001
TINY_FACE = (100.0, 100.0, 13.0, 13.0, 0.9)  # 169 px^2, frac ~0.0004
LARGE_FACE = (100.0, 100.0, 30.0, 30.0, 0.9)  # 900 px^2, frac ~0.0022


def _frame():
    return np.zeros((FRAME_H, FRAME_W, 3), np.uint8)


class StubYuNet:
    """Stub YuNet detector returning canned (x, y, w, h, conf) faces."""

    def __init__(self, faces=()):
        self.faces = list(faces)

    def detect(self, frame):
        return list(self.faces)


def test_floor_constants_pin_the_measured_split():
    assert FACE_PRESENCE_MIN_AREA == 0.002  # speaker path, unmeasured
    assert REFRAME_FACE_PRESENCE_MIN_AREA == 0.0005  # reframing, MIN_AREA_SPIKE


# --- Reframing path (_detect_face_boxes post-filter) -----------------------------


def test_reframing_admits_small_face_above_new_floor():
    yunet = StubYuNet(faces=[SMALL_FACE])
    assert _detect_face_boxes(_frame(), yunet, None) == [(100.0, 100.0, 20.0, 20.0)]


def test_reframing_still_filters_face_below_new_floor():
    yunet = StubYuNet(faces=[TINY_FACE])
    assert _detect_face_boxes(_frame(), yunet, None) == []


def test_reframing_large_face_behaviour_unchanged():
    yunet = StubYuNet(faces=[LARGE_FACE])
    assert _detect_face_boxes(_frame(), yunet, None) == [(100.0, 100.0, 30.0, 30.0)]


# --- Speaker path (_detect_all_faces) --------------------------------------------


def test_speaker_path_still_filters_small_face():
    yunet = StubYuNet(faces=[SMALL_FACE])
    # YuNet supplied a box, so the Haar cascades are never consulted.
    assert _detect_all_faces(_frame(), yunet, (None, None)) == []


def test_speaker_path_still_filters_tiny_face():
    yunet = StubYuNet(faces=[TINY_FACE])
    assert _detect_all_faces(_frame(), yunet, (None, None)) == []


def test_speaker_path_large_face_behaviour_unchanged():
    yunet = StubYuNet(faces=[LARGE_FACE])
    assert _detect_all_faces(_frame(), yunet, (None, None)) == [(100.0, 100.0, 30.0, 30.0)]


# --- build_layout_plan presence re-test agrees with the detection floor ----------


def _uniform_track(area_frac, duration=4.0, step=0.25):
    n = int(duration / step)
    return [(i * step, 0.5, area_frac) for i in range(n)]


def test_layout_plan_counts_small_face_as_present():
    plan = build_layout_plan(_uniform_track(400 / (FRAME_W * FRAME_H)), [], 4.0)
    assert [seg["kind"] for seg in plan] == ["face"]


def test_layout_plan_counts_tiny_face_as_absent():
    plan = build_layout_plan(_uniform_track(169 / (FRAME_W * FRAME_H)), [], 4.0)
    assert [seg["kind"] for seg in plan] == ["fit"]


# --- Cache: evidence computed under the old floor must not be read back ----------


def test_cache_schema_v5_rejects_v4_payload(tmp_path):
    # A v4 cache holds evidence computed under the 0.002 floor; reading
    # it back would silently keep small real faces absent. The schema
    # bump (see reframe_plan.py's version history) discards it. The
    # version has since advanced again (depicted grid screen, v6);
    # the rejection this test pins applies to the v4 payload either
    # way.
    assert reframe_plan.REFRAME_CACHE_SCHEMA_VERSION == 6
    source = tmp_path / "source.mp4"
    source.write_bytes(b"x" * 10)
    payload = {
        "version": 4,
        "source_size": 10,
        "source_mtime_ns": source.stat().st_mtime_ns,
        "width": FRAME_W, "height": FRAME_H, "sample_fps": 3.0,
        "covered_spans": [], "samples": [], "cuts": [], "context": [],
    }
    reframe_plan.reframe_cache_path(source).write_text(json.dumps(payload))
    assert reframe_plan._load_cache(source) is None
