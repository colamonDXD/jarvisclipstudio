"""Facecam window detection (media/facecam_rect.py) and its layout wiring.

Synthetic streams: changing noise as "gameplay" (no persistent edges) with a
bordered camera window on one side. Face detection is injected so the tests
need no real face: the fake reports a face inside any crop it is given and
(optionally) a fixed outside face on full frames.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import numpy as np
import pytest

from src.media import facecam_rect, reaction_layout, reframe_plan, reframing

W, H = 640, 360
CAM = (0, 120, 170, 100)  # x, y, w, h: left side, half-way down

needs_ffmpeg = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not installed")


@pytest.fixture(autouse=True)
def _switch_on(monkeypatch):
    monkeypatch.delenv("FACECAM_RECT_DETECT", raising=False)
    monkeypatch.delenv("REFRAME_REACTOR_NEEDS_INSET", raising=False)


def _render(path: Path, cam=CAM, border=True, duration=8):
    # "Gameplay": a smooth background with shapes moving at different speeds,
    # so no edge stays put across the sampled frames (as in a real game).
    # Per-pixel noise would not do: it puts a strong edge on every pixel of
    # every frame, which no real gameplay does.
    graph = f"color=0x406080:size={W}x{H}:rate=10:duration={duration}"
    for index, (speed, top, size, colour) in enumerate((
        (173, 30, 70, "yellow"), (97, 150, 90, "red"), (211, 260, 60, "green"),
        (131, 90, 50, "white"), (59, 300, 40, "orange"),
    )):
        graph += (
            f",drawbox=x='mod(t*{speed}+{index * 97}\\,{W})':y='{top}+20*sin(t*{index + 1})'"
            f":w={size}:h={size}:color={colour}:t=fill"
        )
    if cam is not None:
        x, y, w, h = cam
        graph += f",drawbox=x={x}:y={y}:w={w}:h={h}:color=0x303030:t=fill"
        if border:
            graph += f",drawbox=x={x}:y={y}:w={w}:h={h}:color=white:t=3"
    subprocess.run(
        ["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", graph,
         "-f", "lavfi", "-i", f"sine=frequency=440:duration={duration}",
         "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", str(path)],
        check=True, capture_output=True,
    )


def _fake_detector(outside_face=None):
    def detect(image):
        h, w = image.shape[:2]
        if (w, h) == (W, H):
            return [outside_face] if outside_face else []
        # A crop (upscaled to >= 480 px wide): one centred face.
        return [(w * 0.35, h * 0.2, w * 0.3, h * 0.5)]
    return detect


TIMES = facecam_rect.probe_times([(0.5, 7.5)], 10)


@needs_ffmpeg
def test_finds_a_side_window_half_way_down(tmp_path):
    source = tmp_path / "stream.mp4"
    _render(source)
    found = facecam_rect.find_facecam_rect(source, W, H, TIMES, detect=_fake_detector())
    assert found is not None
    assert found.side == "left"
    assert np.allclose(found.rect, CAM, atol=5)
    assert found.face_frames == found.frames == 10


@needs_ffmpeg
def test_finds_a_right_side_window(tmp_path):
    cam = (W - 160, 30, 160, 110)
    source = tmp_path / "right.mp4"
    _render(source, cam=cam)
    found = facecam_rect.find_facecam_rect(source, W, H, TIMES, detect=_fake_detector())
    assert found is not None and found.side == "right"
    assert np.allclose(found.rect, cam, atol=5)


@needs_ffmpeg
def test_no_window_means_no_facecam(tmp_path):
    source = tmp_path / "gameplay.mp4"
    _render(source, cam=None)
    assert facecam_rect.find_facecam_rect(source, W, H, TIMES, detect=_fake_detector()) is None


@needs_ffmpeg
def test_window_without_a_face_is_not_a_facecam(tmp_path):
    source = tmp_path / "hud.mp4"
    _render(source)
    assert facecam_rect.find_facecam_rect(source, W, H, TIMES, detect=lambda _img: []) is None


@needs_ffmpeg
def test_a_second_fixed_camera_means_a_call_layout(tmp_path):
    source = tmp_path / "call.mp4"
    _render(source)
    # Crop faces come back at 0.5 x crop height; the crop is 100 px tall in
    # the source, so the cam face is ~50 px. A same-size face fixed at the
    # right in every frame is another camera window.
    outside = (450.0, 200.0, 40.0, 50.0)
    detect = _fake_detector(outside_face=outside)
    assert facecam_rect.find_facecam_rect(source, W, H, TIMES, detect=detect) is None


@needs_ffmpeg
def test_off_switch(tmp_path, monkeypatch):
    source = tmp_path / "stream.mp4"
    _render(source)
    monkeypatch.setenv("FACECAM_RECT_DETECT", "0")
    assert facecam_rect.find_facecam_rect(source, W, H, TIMES, detect=_fake_detector()) is None


def test_probe_times_cover_every_range():
    assert facecam_rect.probe_times([(10.0, 12.0), (20.0, 24.0)], 6) == pytest.approx(
        [10.5, 11.5, 20.5, 21.5, 22.5, 23.5]
    )
    assert facecam_rect.probe_times([]) == []


def test_overlapping_candidates_are_collapsed():
    assert facecam_rect._iou((0, 0, 10, 10), (0, 0, 10, 10)) == pytest.approx(1.0)
    assert facecam_rect._iou((0, 0, 10, 10), (20, 20, 5, 5)) == 0.0


# --- Wiring ----------------------------------------------------------------------


def _analysis(count=24):
    # The face-first check sees nothing (as on real streams after downscaling).
    return {"width": W, "height": H, "samples": [(i / 3.0, []) for i in range(count)],
            "cuts": [], "context": []}


@needs_ffmpeg
def test_edl_planner_delegates_a_found_facecam(tmp_path, monkeypatch):
    source = tmp_path / "stream.mp4"
    _render(source)
    monkeypatch.setattr(facecam_rect, "_default_detector", lambda: _fake_detector())
    plan = reframe_plan.build_reframe_plan(_analysis(), [(0.5, 7.5)], 7.0, source_path=source)
    assert plan == {"delegate": "plain", "shots": []}

    monkeypatch.setenv("FACECAM_RECT_DETECT", "off")
    plan = reframe_plan.build_reframe_plan(_analysis(), [(0.5, 7.5)], 7.0, source_path=source)
    assert plan is None or plan.get("delegate") is None


@needs_ffmpeg
def test_plain_path_composes_a_mid_side_facecam(tmp_path, monkeypatch):
    source = tmp_path / "stream.mp4"
    _render(source)
    monkeypatch.setattr(facecam_rect, "_default_detector", lambda: _fake_detector())
    samples = [(t, []) for t in np.arange(0, 8, 1 / 3)]
    layout = reaction_layout.choose_reaction_layout(source, W, H, 8.0, samples)
    assert layout is not None and layout["kind"] == "reaction"
    x, y, w, h = layout["inset"]
    assert y > 100  # the window floats half-way down, not at the top
    # The camera's own rectangle is masked out of the source, at its real y.
    assert f"drawbox=x={int(x)}:y={int(y)}:w={int(w)}:h={int(h)}" in layout["video_filter"]

    output = tmp_path / "vertical.mp4"
    ok, out_w, out_h = reframing.render_reframed_clip_ffmpeg(
        source, output, "vertical", vertical_plan=(layout["video_filter"], "complex")
    )
    assert ok and (out_w, out_h) == (1080, 1920)
    probe = json.loads(subprocess.check_output(
        ["ffprobe", "-v", "error", "-show_streams", "-of", "json", str(output)]
    ))
    assert {s["codec_type"] for s in probe["streams"]} == {"video", "audio"}
    raw = subprocess.check_output(
        ["ffmpeg", "-v", "error", "-ss", "1", "-i", str(output), "-frames:v", "1",
         "-pix_fmt", "gray", "-f", "rawvideo", "-"]
    )
    frame = np.frombuffer(raw, dtype=np.uint8).reshape(1920, 1080)
    # Top panel = the dark camera interior (0x30), not the noisy gameplay.
    assert frame[200:440, 200:880].mean() < 90


def test_top_anchored_inset_graph_is_unchanged():
    # iy == 0: inset_bottom == ih, so the existing reaction layout is identical.
    layout = reaction_layout.build_reaction_layout(
        640, 360, 6, (536.0, 25.0, 32.0, 42.0), (480.0, 0.0, 160.0, 120.0),
        [(i / 3, [(536.0, 25.0, 32.0, 42.0)]) for i in range(18)],
    )
    assert "drawbox=x=480:y=0:w=160:h=120" in layout["video_filter"]
