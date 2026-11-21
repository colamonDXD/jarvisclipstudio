"""Streamer "face on top, game below" templates (media/gaming_templates.py)."""

from __future__ import annotations

import json
import shutil
import subprocess

import numpy as np
import pytest

from src.media import facecam_rect, gaming_templates, reaction_layout, reframing
from src.media.gaming_templates import (
    FACE_PANEL_H,
    build_facecam_stack_layout,
    caption_template_for,
    clean_layout_credit,
    validate_layout_template,
)
from src.caption_templates import get_template
from src.video_utils import _layout_caption_override

needs_ffmpeg = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not installed")

W, H = 640, 360
CAM = (0.0, 120.0, 170.0, 100.0)
FACE = (55.0, 135.0, 50.0, 60.0)


@pytest.mark.parametrize("value", [None, "", "auto", "AUTO", " none "])
def test_absent_or_auto_means_the_automatic_layout(value):
    assert validate_layout_template(value) is None


def test_template_names_are_normalised_and_checked():
    assert validate_layout_template(" Facecam_Red ") == "facecam_red"
    assert validate_layout_template("facecam_gold") == "facecam_gold"
    with pytest.raises(ValueError):
        validate_layout_template("facecam_blue")
    with pytest.raises(ValueError):
        validate_layout_template(3)


def test_credit_line():
    assert clean_layout_credit("@KaiCenat") == "credit @KaiCenat"
    assert clean_layout_credit("credit @iShowSpeed") == "credit @iShowSpeed"
    assert clean_layout_credit("   ") is None
    assert clean_layout_credit(None) is None
    long = clean_layout_credit("@" + "x" * 80)
    assert long.startswith("credit @") and len(long) <= len("credit ") + gaming_templates.CREDIT_MAX_CHARS


def test_each_template_has_its_seam_caption_style():
    assert caption_template_for("facecam_red") == "seam_red"
    assert caption_template_for("facecam_gold") == "seam_gold"
    assert caption_template_for(None) is None
    red, gold = get_template("seam_red"), get_template("seam_gold")
    # Owner change 2026-11-18: the red is the divider only; captions are
    # white with a black outline ("why are captions red?").
    assert red["stroke_color"] == "#000000" and red["font_family"] == "Anton-Regular"
    assert gold["highlight_color"] == "#FFD400" and gold["stroke_color"] == "#000000"
    assert red["max_words_per_line"] <= 2 and gold["max_words_per_line"] <= 3
    # Big, with a thin outline (the outline scales with the font size).
    assert red["font_size"] >= 60 and gold["font_size"] >= 60
    assert red["stroke_width"] <= 2 and gold["stroke_width"] <= 2


def test_seam_captions_keep_their_own_words_per_line():
    profile_override = {**get_template("seam_red"), "max_words_per_line": 5}
    kept = _layout_caption_override(profile_override, "seam_red", "facecam_red")
    assert kept["max_words_per_line"] == 2
    # Without a layout template the profile grouping is untouched.
    assert _layout_caption_override(profile_override, "seam_red", None) is profile_override
    assert _layout_caption_override(None, "seam_red", "facecam_red") is None


def test_geometry_left_camera():
    layout = build_facecam_stack_layout(1920, 1080, 6, (120.0, 360.0, 80.0, 100.0),
                                        (0.0, 312.0, 396.0, 224.0), "facecam_red")
    fw, fh, fx, fy = layout["face_crop"]
    assert 0 <= fx and fx + fw <= 396 and 312 <= fy and fy + fh <= 536
    assert abs(fw / fh - 1080 / FACE_PANEL_H) < 0.02
    gw, gh, gx, gy = layout["game_crop"]
    assert gh == 1080 and gx >= 396                  # gameplay stays clear of the camera
    assert abs(gw / gh - 1080 / (1920 - FACE_PANEL_H)) < 0.02
    # Centred on the seam: the builder's centre is position - 0.75 x font px.
    from src.media.captions import get_scaled_font_size
    font_px = get_scaled_font_size(get_template("seam_red")["font_size"], 1080)
    assert layout["caption_y"] * 1920 - font_px * 0.75 == pytest.approx(FACE_PANEL_H)
    assert "color=0xE5262B" in layout["video_filter"]
    assert layout["video_filter"].endswith("[vout]")


def test_geometry_right_camera_and_gold_divider():
    layout = build_facecam_stack_layout(1920, 1080, 6, (1700.0, 60.0, 80.0, 100.0),
                                        (1500.0, 0.0, 420.0, 260.0), "facecam_gold")
    gw, _gh, gx, _gy = layout["game_crop"]
    assert gx + gw <= 1500
    assert "color=0xF5B700" in layout["video_filter"]


def _render_source(path):
    # Gameplay blue, camera window dark red, half-way down the left side.
    x, y, w, h = (int(v) for v in CAM)
    subprocess.run(
        ["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i",
         f"color=0x2040C0:size={W}x{H}:rate=10:duration=3,"
         f"drawbox=x={x}:y={y}:w={w}:h={h}:color=0x802020:t=fill",
         "-f", "lavfi", "-i", "sine=frequency=440:duration=3",
         "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", str(path)],
        check=True, capture_output=True,
    )


def _frame_rgb(path, t=1.0):
    raw = subprocess.check_output(
        ["ffmpeg", "-v", "error", "-ss", str(t), "-i", str(path), "-frames:v", "1",
         "-pix_fmt", "rgb24", "-f", "rawvideo", "-"]
    )
    return np.frombuffer(raw, dtype=np.uint8).reshape(1920, 1080, 3)


@needs_ffmpeg
@pytest.mark.parametrize("template,divider", [("facecam_red", (229, 38, 43)), ("facecam_gold", (245, 183, 0))])
def test_real_render_puts_face_on_top_game_below(tmp_path, template, divider):
    source, output = tmp_path / "stream.mp4", tmp_path / "stack.mp4"
    _render_source(source)
    # A credit with characters that break naive filter quoting.
    layout = build_facecam_stack_layout(W, H, 3, FACE, CAM, template, "credit @it's 100%: [ok], yes")
    ok, out_w, out_h = reframing.render_reframed_clip_ffmpeg(
        source, output, "vertical", vertical_plan=(layout["video_filter"], "complex")
    )
    assert ok and (out_w, out_h) == (1080, 1920)
    probe = json.loads(subprocess.check_output(
        ["ffprobe", "-v", "error", "-show_streams", "-of", "json", str(output)]))
    assert {s["codec_type"] for s in probe["streams"]} == {"video", "audio"}
    frame = _frame_rgb(output).astype(int)
    top = frame[100:700, 100:980].reshape(-1, 3).mean(axis=0)
    bottom = frame[1000:1700, 100:980].reshape(-1, 3).mean(axis=0)
    assert top[0] > top[2] + 40          # the dark-red camera fills the top
    assert bottom[2] > bottom[0] + 60    # the blue gameplay fills the bottom
    seam = frame[FACE_PANEL_H - 2, 100:980].mean(axis=0)
    assert np.abs(seam - np.array(divider)).max() < 60


@needs_ffmpeg
def test_choose_reaction_layout_uses_the_template_for_a_found_facecam(tmp_path, monkeypatch):
    source = tmp_path / "stream.mp4"
    _render_source(source)

    def fake_find(path, width, height, times, detect=None):
        return facecam_rect.FacecamRect(CAM, FACE, "left", 10, 10)

    monkeypatch.setattr(facecam_rect, "find_facecam_rect", fake_find)
    samples = [(t / 3, []) for t in range(9)]
    layout = reaction_layout.choose_reaction_layout(
        source, W, H, 3.0, samples, layout_template="facecam_gold", layout_credit="credit @x"
    )
    assert layout["template"] == "facecam_gold"
    default = reaction_layout.choose_reaction_layout(source, W, H, 3.0, samples)
    assert default["kind"] == "reaction" and "template" not in default
