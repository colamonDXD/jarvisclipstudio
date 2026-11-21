"""Step 3: a lit screen that carries the story is framed, never cropped away."""
from __future__ import annotations

import numpy as np

from src.media import screen_subject as ss


def _dark_frame(w=960, h=540):
    rng = np.random.default_rng(0)
    return (rng.integers(10, 40, size=(h, w, 3))).astype(np.uint8)


def _textured_screen(frame, x0, y0, x1, y1):
    frame[y0:y1, x0:x1] = (230, 120, 40)  # bright, saturated (BGR blue-ish)
    frame[y0 + 10:y1 - 10:12, x0 + 10:x1 - 10] = (255, 255, 255)  # text-like lines
    return frame


def test_detects_a_lit_content_screen_high_in_frame():
    frame = _textured_screen(_dark_frame(), 40, 20, 440, 280)
    found = ss.find_bright_screen(frame)
    assert found is not None
    left, right, top, bottom = found
    assert left < 0.06 and 0.44 < right < 0.48 and top < 0.06 and 0.5 < bottom < 0.54


def test_flat_white_patch_and_low_bright_patch_are_not_screens():
    flat = _dark_frame()
    flat[20:280, 40:440] = 245  # flat, colourless (a white wall / jacket)
    assert ss.find_bright_screen(flat) is None
    low = _textured_screen(_dark_frame(), 40, 300, 440, 530)  # chest height
    assert ss.find_bright_screen(low) is None


def test_screen_tile_holds_the_whole_screen_in_the_tile_shape():
    tile = ss._screen_tile((0.0, 0.466, 0.0, 0.578), 3840, 2160)
    assert abs(tile["tile_w"] / tile["tile_h"] - 1080 / 960) < 0.01
    assert tile["tile_w"] >= 0.466 * 3840 * 0.99 or tile["tile_h"] == 2160 // 2 * 2
    assert tile["tile_x"] >= 0 and tile["tile_x"] + tile["tile_w"] <= 3840
    assert tile["tile_y"] >= 0 and tile["tile_y"] + tile["tile_h"] <= 2160


def test_apply_turns_shots_inside_screen_runs_into_window_or_stack(monkeypatch, tmp_path):
    window = {"mode": "fit_window", "crop_w": 2000, "x": 100}
    stack = {"mode": "stack", "top": {"tile_x": 0, "tile_y": 0, "tile_w": 1894, "tile_h": 1684},
             "bottom": {"tile_x": 2796, "tile_y": 1034, "tile_w": 754, "tile_h": 646}}
    runs = [{"start": 0.0, "end": 10.0, "decision": window}, {"start": 10.0, "end": 20.0, "decision": stack}]
    seen = {}
    monkeypatch.setattr(ss, "screen_runs", lambda src, ranges, dur, w, h: seen.update(ranges=ranges) or runs)
    plan = {"width": 3840, "height": 2160, "shots": [
        {"start": 0.0, "end": 10.0, "mode": "fit"},
        {"start": 10.0, "end": 15.0, "mode": "fit"},
        {"start": 15.0, "end": 20.0, "mode": "fit"},
        {"start": 20.0, "end": 30.0, "mode": "fit"},
        {"start": 30.0, "end": 40.0, "mode": "single"},
    ]}
    assert ss.apply_screen_windows(plan, tmp_path / "src.mp4", [(141.0, 181.0)]) == 3
    assert seen["ranges"] == [(141.0, 181.0)]
    a, b, c, d, e = plan["shots"]
    assert a["mode"] == "fit" and a["fit_windows"] == [[0.0, 10.0, 2000, 100]]
    assert b["mode"] == c["mode"] == "split" and b["split"] == c["split"]  # one continuous stack
    assert b["split"]["left"]["tile_w"] == 1894  # top tile = the screen
    assert d == {"start": 20.0, "end": 30.0, "mode": "fit"}
    assert e["mode"] == "single"


def test_clip_time_maps_through_keep_ranges():
    assert ss.clip_to_source(5.0, [(141.0, 201.0)]) == 146.0
    assert ss.clip_to_source(12.0, [(10.0, 20.0), (50.0, 60.0)]) == 52.0


def test_decide_prefers_one_window_when_screen_and_person_are_close():
    screens = [(0.10, 0.40, 0.0, 0.5)] * 3
    near = [(0.42, 0.3, 0.05, 0.08)]
    far = [(0.85, 0.3, 0.05, 0.08)]
    assert ss.decide(screens, near, 3840, 2160)["mode"] == "fit_window"
    assert ss.decide(screens, far, 3840, 2160)["mode"] == "stack"
    assert ss.decide(screens[:1], near, 3840, 2160) is None  # one sighting is not a run


def test_switch_default_off(monkeypatch):
    monkeypatch.delenv("SCREEN_SUBJECT", raising=False)
    assert not ss.screen_subject_enabled()


def test_crop_shots_showing_a_screen_are_stacked_only_with_all_modes(monkeypatch, tmp_path):
    stack = {"mode": "stack", "top": {"tile_x": 0, "tile_y": 0, "tile_w": 1080, "tile_h": 960},
             "bottom": {"tile_x": 2000, "tile_y": 1000, "tile_w": 540, "tile_h": 480}}
    monkeypatch.setattr(ss, "screen_runs", lambda *_a: [{"start": 0.0, "end": 18.0, "decision": stack}])
    plan = {"width": 3840, "height": 2160, "shots": [{"start": 0.0, "end": 9.0, "mode": "single"},
                                                    {"start": 9.0, "end": 18.0, "mode": "split", "split": {}}]}
    assert ss.apply_screen_windows(plan, tmp_path / "s.mp4") == 0
    assert ss.apply_screen_windows(plan, tmp_path / "s.mp4", all_modes=True) == 1
    assert plan["shots"][0]["mode"] == "split" and plan["shots"][0]["reason"] == "screen_subject_stack"
    assert plan["shots"][1]["split"] == {}  # an existing two-person stack is never touched
