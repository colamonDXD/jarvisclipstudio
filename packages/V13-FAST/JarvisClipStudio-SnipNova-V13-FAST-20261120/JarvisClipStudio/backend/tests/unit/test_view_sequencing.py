"""View sequencing (media/view_sequencing.py + the sequenced stack).

The pro streamer sequence around a peak — SPLIT → GAMEPLAY-FULL →
FACE-FULL → SPLIT — behind VIEW_SEQUENCING (default off), gaming /
streamer niches only, hand-drawn boxes outranking it. Off must be the
old stack, byte for byte.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import numpy as np
import pytest

from src.media import facecam_rect, gaming_templates, reaction_layout, reframing, view_sequencing as vs
from src.media.gaming_templates import (
    FACE_PANEL_H,
    build_facecam_stack_layout,
    build_sequenced_stack_layout,
)
from src.media.view_sequencing import (
    FACE_FULL_TAIL_S,
    GAME_FULL_LEAD_S,
    loudest_reaction_second,
    peak_from_edl,
    plan_view_segments,
)

needs_ffmpeg = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not installed")

W, H = 640, 360
CAM = (0.0, 120.0, 170.0, 100.0)
FACE = (55.0, 135.0, 50.0, 60.0)


# -- the switch and the niche gate ------------------------------------------


def test_switch_defaults_off(monkeypatch):
    monkeypatch.delenv("VIEW_SEQUENCING", raising=False)
    assert vs.view_sequencing_enabled() is False
    for value in ("on", "1", "true", "YES"):
        monkeypatch.setenv("VIEW_SEQUENCING", value)
        assert vs.view_sequencing_enabled() is True
    for value in ("off", "0", "false", "no", ""):
        monkeypatch.setenv("VIEW_SEQUENCING", value)
        assert vs.view_sequencing_enabled() is False


def test_niche_gate():
    assert vs.niche_is_sequenced("gaming")
    assert vs.niche_is_sequenced(" Streamer ")
    assert not vs.niche_is_sequenced("podcast_news")
    assert not vs.niche_is_sequenced(None)
    assert not vs.niche_is_sequenced("")


# -- the segment plan ---------------------------------------------------------


def test_canonical_sequence_around_the_peak():
    assert GAME_FULL_LEAD_S == 3.0 and FACE_FULL_TAIL_S == 4.0
    segments = plan_view_segments(30.0, 15.0)
    assert segments == [
        {"view": "split", "start": 0.0, "end": 12.0},
        {"view": "game_full", "start": 12.0, "end": 15.0},
        {"view": "face_full", "start": 15.0, "end": 19.0},
        {"view": "split", "start": 19.0, "end": 30.0},
    ]


@pytest.mark.parametrize(
    "duration,peak",
    [(30.0, 15.0), (30.0, 3.0), (30.0, 26.5), (12.0, 6.0), (60.0, 59.0), (10.0, 5.0), (45.0, 0.5)],
)
def test_segments_tile_the_clip_exactly(duration, peak):
    segments = plan_view_segments(duration, peak)
    assert segments, (duration, peak)
    assert segments[0]["start"] == 0.0 and segments[-1]["end"] == duration
    for left, right in zip(segments, segments[1:]):
        assert left["end"] == right["start"]
    # Duration is never created or destroyed: the 30 s floor upstream
    # sees exactly the clip it approved.
    assert sum(s["end"] - s["start"] for s in segments) == pytest.approx(duration)


def test_peak_near_the_start_folds_the_game_view():
    segments = plan_view_segments(30.0, 0.5)
    assert [s["view"] for s in segments] == ["split", "face_full", "split"]
    assert segments[1]["start"] == 0.5


def test_peak_with_room_for_the_cause_keeps_the_game_view():
    segments = plan_view_segments(30.0, 2.0)
    assert [s["view"] for s in segments] == ["game_full", "face_full", "split"]
    assert segments[0] == {"view": "game_full", "start": 0.0, "end": 2.0}


@pytest.mark.parametrize(
    "duration,peak",
    [(30.0, None), (None, 15.0), (9.9, 5.0), (30.0, 30.0), (30.0, 29.7), (30.0, -1.0), (30.0, 31.0)],
)
def test_no_sequence_without_a_usable_peak(duration, peak):
    assert plan_view_segments(duration, peak) is None


# -- peak sources ---------------------------------------------------------------


def test_peak_from_edl_carriers():
    assert peak_from_edl({"peak_energy_timestamp": 7.5}) == 7.5
    assert peak_from_edl({"peak_energy_time": 3}) == 3.0
    assert peak_from_edl({"energy_peaks": [2.0, 9.0]}) == 2.0
    assert peak_from_edl({"zoom_keyframes": [{"type": "punch_in", "time": 4.25}]}) == 4.25
    assert peak_from_edl({"zoom_keyframes": [{"type": "slow_zoom", "time": 4.25}]}) is None
    assert peak_from_edl({}) is None
    assert peak_from_edl(None) is None
    assert peak_from_edl({"peak_energy_timestamp": -2.0}) is None


@needs_ffmpeg
def test_loudest_reaction_second_finds_the_burst(tmp_path):
    path = tmp_path / "burst.m4a"
    graph = (
        "[0:a]atrim=0:5,asetpts=PTS-STARTPTS[calm];"
        "[1:a]atrim=0:1,asetpts=PTS-STARTPTS[burst];"
        "[2:a]atrim=0:4,asetpts=PTS-STARTPTS[tail];"
        "[calm][burst][tail]concat=n=3:v=0:a=1[out]"
    )
    subprocess.run(
        ["ffmpeg", "-v", "error", "-y",
         "-f", "lavfi", "-i", "sine=frequency=200:duration=5,volume=0.02",
         "-f", "lavfi", "-i", "sine=frequency=3000:duration=1,volume=0.7",
         "-f", "lavfi", "-i", "sine=frequency=200:duration=4,volume=0.02",
         "-filter_complex", graph, "-map", "[out]", str(path)],
        check=True, capture_output=True,
    )
    peak = loudest_reaction_second(path)
    assert peak is not None and 5.0 <= peak <= 6.0


@needs_ffmpeg
def test_loudest_reaction_second_flat_clip_has_no_peak(tmp_path):
    path = tmp_path / "flat.m4a"
    subprocess.run(
        ["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i",
         "sine=frequency=440:duration=6,volume=0.2", str(path)],
        check=True, capture_output=True,
    )
    assert loudest_reaction_second(path) is None


# -- the sequenced graph --------------------------------------------------------


def _segments():
    return plan_view_segments(30.0, 15.0)


def test_sequenced_graph_switches_views_on_the_segment_bounds():
    layout = build_sequenced_stack_layout(W, H, 30.0, FACE, CAM, "facecam_red", None, _segments())
    graph = layout["video_filter"]
    assert "overlay=0:0:enable='gte(t\\,12.000)*lt(t\\,15.000)'" in graph
    assert "overlay=0:0:enable='gte(t\\,15.000)*lt(t\\,19.000)'" in graph
    assert graph.endswith("[vout]")
    assert layout["sequenced"] is True
    assert layout["layout_timeline"] == _segments()
    # Captions stay in the stack's seam band across every switch.
    stack = build_facecam_stack_layout(W, H, 30.0, FACE, CAM, "facecam_red")
    assert layout["caption_y"] == stack["caption_y"]
    assert layout["face_crop"] == stack["face_crop"]
    assert layout["game_crop"] == stack["game_crop"]


def test_sequenced_full_view_crops():
    layout = build_sequenced_stack_layout(1280, 720, 30.0, (1050.0, 60.0, 120.0, 120.0),
                                          (960.0, 0.0, 320.0, 240.0), "facecam_red", None, _segments())
    ffw, ffh, ffx, ffy = layout["face_full_crop"]
    assert 960 <= ffx and ffx + ffw <= 1280 and 0 <= ffy and ffy + ffh <= 240
    assert ffw % 2 == 0 and ffh % 2 == 0
    assert abs(ffw / ffh - 1080 / 1920) < 0.02
    gfw, gfh, gfx, gfy = layout["game_full_crop"]
    assert abs(gfw / gfh - 1080 / 1920) < 0.02
    assert gfx + gfw <= 960  # gameplay-full stays clear of the camera window


def test_sequenced_credit_is_drawn_once_on_the_composed_frame():
    layout = build_sequenced_stack_layout(W, H, 30.0, FACE, CAM, "facecam_red", "credit @x", _segments())
    assert layout["video_filter"].count("drawtext=") == 1


def test_sequenced_without_game_view_uses_three_sources():
    segments = plan_view_segments(30.0, 0.5)
    layout = build_sequenced_stack_layout(W, H, 30.0, FACE, CAM, "facecam_red", None, segments)
    assert "[0:v]split=3" in layout["video_filter"]
    assert layout["game_full_crop"] is None


# -- the layout choice ------------------------------------------------------------


def _fake_facecam(monkeypatch):
    def fake_find(path, width, height, times, detect=None):
        return facecam_rect.FacecamRect(CAM, FACE, "left", 10, 10)

    monkeypatch.setattr(facecam_rect, "find_facecam_rect", fake_find)


def _choose(tmp_path, monkeypatch, **kwargs):
    source = tmp_path / "stream.mp4"
    source.write_bytes(b"not a real video; the detector is stubbed")
    _fake_facecam(monkeypatch)
    samples = [(t / 3, []) for t in range(9)]
    return reaction_layout.choose_reaction_layout(
        source, W, H, kwargs.pop("duration", 12.0), samples,
        layout_template="facecam_red", **kwargs,
    )


def test_switch_off_is_the_old_stack_byte_for_byte(tmp_path, monkeypatch):
    monkeypatch.delenv("VIEW_SEQUENCING", raising=False)
    layout = _choose(tmp_path, monkeypatch, peak_time=6.0, niche="gaming")
    stack = build_facecam_stack_layout(W, H, 12.0, FACE, CAM, "facecam_red")
    assert layout == stack
    assert "sequenced" not in layout


def test_switch_on_but_wrong_niche_is_the_old_stack(tmp_path, monkeypatch):
    monkeypatch.setenv("VIEW_SEQUENCING", "on")
    layout = _choose(tmp_path, monkeypatch, peak_time=6.0, niche="podcast_news")
    assert layout == build_facecam_stack_layout(W, H, 12.0, FACE, CAM, "facecam_red")


def test_switch_on_gaming_sequences_around_the_peak(tmp_path, monkeypatch):
    monkeypatch.setenv("VIEW_SEQUENCING", "on")
    layout = _choose(tmp_path, monkeypatch, peak_time=6.0, niche="gaming")
    assert layout["sequenced"] is True
    assert layout["layout_timeline"] == plan_view_segments(12.0, 6.0)


def test_hand_drawn_boxes_outrank_sequencing(tmp_path, monkeypatch):
    monkeypatch.setenv("VIEW_SEQUENCING", "on")
    boxes = {"cam": {"x": 0.0, "y": 0.3, "w": 0.27, "h": 0.28},
             "game": {"x": 0.0, "y": 0.0, "w": 1.0, "h": 1.0}, "arrange": "top"}
    token = gaming_templates.MANUAL_BOXES.set(boxes)
    try:
        layout = _choose(tmp_path, monkeypatch, peak_time=6.0, niche="gaming")
    finally:
        gaming_templates.MANUAL_BOXES.reset(token)
    assert "sequenced" not in layout


# -- the live finisher wiring -------------------------------------------------------


def test_finisher_stamps_the_peak_on_the_edl(tmp_path, monkeypatch):
    from src.live import finisher, groq_transcribe
    from src.media import music_library, reframe_plan, transcription
    from src import video_utils

    captured = {}

    def fake_create(window, start, end, out_path, **kwargs):
        captured["edl"] = kwargs["edl"]
        Path(out_path).write_bytes(b"clip")
        return True

    monkeypatch.setattr(groq_transcribe, "transcribe_with_groq", lambda path: True)
    monkeypatch.setattr(transcription, "load_cached_transcript_data", lambda path: {"words": []})
    monkeypatch.setattr(reframe_plan, "attach_reframe_plan", lambda *a, **k: True)
    monkeypatch.setattr(music_library, "apply_auto_music_bed", lambda edl, niche: None)
    monkeypatch.setattr(video_utils, "create_optimized_clip", fake_create)
    monkeypatch.setattr(finisher, "_duration", lambda path: 30.0)

    window = tmp_path / "window.mp4"
    window.write_bytes(b"video")
    out = tmp_path / "clip.mp4"
    result = finisher.render_live_clip(window, out, niche="gaming", peak_offset=17.25)
    assert result["ok"]
    assert captured["edl"]["peak_energy_timestamp"] == 17.25
    # And the EDL carrier is exactly what the layout choice reads.
    assert peak_from_edl(captured["edl"]) == 17.25

    result = finisher.render_live_clip(window, out, niche="gaming")
    assert result["ok"]
    assert "peak_energy_timestamp" not in captured["edl"]


# -- a real render ------------------------------------------------------------------


def _render_source(path, seconds=12):
    x, y, w, h = (int(v) for v in CAM)
    subprocess.run(
        ["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i",
         f"color=0x2040C0:size={W}x{H}:rate=10:duration={seconds},"
         f"drawbox=x={x}:y={y}:w={w}:h={h}:color=0x802020:t=fill",
         "-f", "lavfi", "-i", f"sine=frequency=440:duration={seconds}",
         "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", str(path)],
        check=True, capture_output=True,
    )


def _frame_rgb(path, t):
    raw = subprocess.check_output(
        ["ffmpeg", "-v", "error", "-ss", str(t), "-i", str(path), "-frames:v", "1",
         "-pix_fmt", "rgb24", "-f", "rawvideo", "-"]
    )
    return np.frombuffer(raw, dtype=np.uint8).reshape(1920, 1080, 3).astype(int)


@needs_ffmpeg
def test_real_render_sequences_the_views(tmp_path):
    source, output = tmp_path / "stream.mp4", tmp_path / "sequenced.mp4"
    _render_source(source)
    segments = plan_view_segments(12.0, 6.0)
    assert [s["view"] for s in segments] == ["split", "game_full", "face_full", "split"]
    layout = build_sequenced_stack_layout(W, H, 12.0, FACE, CAM, "facecam_red", "credit @x", segments)
    ok, out_w, out_h = reframing.render_reframed_clip_ffmpeg(
        source, output, "vertical", vertical_plan=(layout["video_filter"], "complex")
    )
    assert ok and (out_w, out_h) == (1080, 1920)
    probe = json.loads(subprocess.check_output(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "json", str(output)]))
    assert float(probe["format"]["duration"]) == pytest.approx(12.0, abs=0.35)

    divider = np.array([229, 38, 43])
    split = _frame_rgb(output, 1.5)
    assert np.abs(split[FACE_PANEL_H - 2, 100:980].mean(axis=0) - divider).max() < 60
    game = _frame_rgb(output, 4.5)
    whole = game.reshape(-1, 3).mean(axis=0)
    assert whole[2] > whole[0] + 40                      # blue gameplay fills the frame
    assert np.abs(game[FACE_PANEL_H - 2, 100:980].mean(axis=0) - divider).max() > 60
    face = _frame_rgb(output, 8.0)
    whole = face.reshape(-1, 3).mean(axis=0)
    assert whole[0] > whole[2] + 20                      # the dark-red camera fills the frame
    assert np.abs(face[FACE_PANEL_H - 2, 100:980].mean(axis=0) - divider).max() > 60
    settle = _frame_rgb(output, 11.0)
    assert np.abs(settle[FACE_PANEL_H - 2, 100:980].mean(axis=0) - divider).max() < 60
