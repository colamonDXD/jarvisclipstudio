"""A still corner face only delegates to the plain path with a real inset.

Phase 2 found a two-pane video-call podcast (host top-left, guest
bottom-right) whose host face passed ``find_reactor_face``: small, still,
upper corner, another face lower down. The EDL planner then delegated the
clip to the plain path, which could not measure a facecam rectangle either
and fell back to a small-faces fit. With the source path available, the
planner now asks for the same two-part evidence the plain path's reaction
layout needs (face + measurable inset) before delegating.
"""

from __future__ import annotations

import shutil
import subprocess
from unittest.mock import patch

import pytest

from src.media import reaction_layout, reframe_plan

W, H = 1920, 1080
REACTOR = (1640.0, 90.0, 120.0, 120.0)  # small, static, top-right
SUBJECT = (860.0, 600.0, 200.0, 240.0)  # main content below


def _analysis(width=W, height=H, faces=(REACTOR, SUBJECT), count=12):
    return {
        "width": width,
        "height": height,
        "samples": [(i / 3.0, list(faces)) for i in range(count)],
        "cuts": [],
        "context": [],
    }


@pytest.fixture(autouse=True)
def _default_switch(monkeypatch):
    monkeypatch.delenv("REFRAME_REACTOR_NEEDS_INSET", raising=False)


def test_without_a_source_path_the_face_alone_still_delegates():
    plan = reframe_plan.build_reframe_plan(_analysis(), [(0.0, 4.0)], 4.0)
    assert plan == {"delegate": "plain", "shots": []}


def test_measurable_inset_delegates(tmp_path):
    with patch.object(reaction_layout, "measure_reaction_inset", return_value=(1500.0, 0.0, 420.0, 300.0)):
        plan = reframe_plan.build_reframe_plan(
            _analysis(), [(0.0, 4.0)], 4.0, source_path=tmp_path / "s.mp4"
        )
    assert plan == {"delegate": "plain", "shots": []}


def test_no_inset_rectangle_keeps_the_regular_planner(tmp_path):
    with patch.object(reaction_layout, "measure_reaction_inset", return_value=None) as measure:
        plan = reframe_plan.build_reframe_plan(
            _analysis(), [(0.0, 4.0)], 4.0, source_path=tmp_path / "s.mp4"
        )
    assert measure.called
    assert plan is not None
    assert plan["delegate"] is None
    assert plan["shots"]


@pytest.mark.parametrize("value", ["0", "false", "off", "no"])
def test_off_switch_restores_the_face_only_check(tmp_path, monkeypatch, value):
    monkeypatch.setenv("REFRAME_REACTOR_NEEDS_INSET", value)
    with patch.object(reaction_layout, "measure_reaction_inset", return_value=None) as measure:
        plan = reframe_plan.build_reframe_plan(
            _analysis(), [(0.0, 4.0)], 4.0, source_path=tmp_path / "s.mp4"
        )
    assert plan == {"delegate": "plain", "shots": []}
    assert not measure.called


def test_probe_times_spread_over_every_kept_range():
    times = reframe_plan._inset_probe_times([(10.0, 12.0), (20.0, 24.0)], count=6)
    assert times == pytest.approx([10.5, 11.5, 20.5, 21.5, 22.5, 23.5])
    assert reframe_plan._inset_probe_times([]) == []


def test_measure_reads_the_given_source_times(tmp_path):
    seen = []

    class FakeCapture:
        def __init__(self, _path):
            pass

        def set(self, _prop, msec):
            seen.append(round(msec / 1000.0, 3))

        def read(self):
            return False, None

        def release(self):
            pass

    with patch.object(reaction_layout.cv2, "VideoCapture", FakeCapture):
        assert reaction_layout.measure_reaction_inset(
            tmp_path / "s.mp4", 640, 360, 100.0, (536.0, 25.0, 32.0, 42.0), times=[40.0, 41.5]
        ) is None
    assert seen == [40.0, 41.5]


# --- Real sources: a facecam inset vs a two-pane call ---------------------------

SMALL_W, SMALL_H = 640, 360


def _render(path, graph):
    subprocess.run(
        ["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", graph,
         "-c:v", "libx264", "-pix_fmt", "yuv420p", str(path)],
        check=True, capture_output=True,
    )


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not installed")
def test_real_facecam_inset_is_delegated_and_two_pane_call_is_not(tmp_path):
    # Facecam: a red camera rectangle in the top-right corner over content.
    facecam = tmp_path / "facecam.mp4"
    _render(
        facecam,
        f"color=blue:size={SMALL_W}x{SMALL_H}:rate=10:duration=8,"
        "drawbox=x=0:y=0:w=320:h=360:color=green:t=fill,"
        "drawbox=x=480:y=0:w=160:h=120:color=red:t=fill",
    )
    reactor = (536.0, 25.0, 32.0, 42.0)
    content = (200.0, 140.0, 90.0, 100.0)
    plan = reframe_plan.build_reframe_plan(
        _analysis(SMALL_W, SMALL_H, (reactor, content), count=18),
        [(2.0, 6.0)], 4.0, source_path=facecam,
    )
    assert plan == {"delegate": "plain", "shots": []}

    # Video call: two wide panes on a flat background, host top-left, guest
    # bottom-right. The host face is small and still in the upper corner, but
    # there is no facecam rectangle around it.
    call = tmp_path / "call.mp4"
    _render(
        call,
        f"color=orange:size={SMALL_W}x{SMALL_H}:rate=10:duration=8,"
        "drawbox=x=15:y=15:w=305:h=170:color=gray:t=fill,"
        "drawbox=x=320:y=170:w=305:h=170:color=white:t=fill",
    )
    host = (90.0, 40.0, 40.0, 50.0)
    guest = (450.0, 220.0, 45.0, 55.0)
    analysis = _analysis(SMALL_W, SMALL_H, (host, guest), count=18)
    from src.media.reaction_layout import find_reactor_face

    assert find_reactor_face(SMALL_W, SMALL_H, analysis["samples"]) == host
    plan = reframe_plan.build_reframe_plan(analysis, [(2.0, 6.0)], 4.0, source_path=call)
    assert plan is not None and plan["delegate"] is None
