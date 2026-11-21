"""Hand-drawn facecam/gameplay boxes (live clipping).

Covers ``media.gaming_templates.validate_boxes`` /
``build_manual_stack_layout``, the reframing hook that honours
``MANUAL_BOXES``, the session-create API (bad boxes are 422, good boxes
round-trip through the settings JSONB), the ``GET /live/frame`` contract
(yt-dlp/ffmpeg boundary mocked), and a real ffmpeg render of a generated
1280x720 source through the manual layout (1080x1920 out, both panels
present, "bottom" swaps them).
"""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest
from fastapi import HTTPException
from sqlalchemy import text

from src.api.routes import live as routes
from src.database import AsyncSessionLocal
from src.media.gaming_templates import (
    FACE_PANEL_H,
    GAME_PANEL_H,
    MANUAL_BOXES,
    OUT_H,
    OUT_W,
    build_manual_stack_layout,
    validate_boxes,
)
from src.media.reframe_plan import build_reframe_plan
from src.media.reframing import describe_vertical_layout
from src.workers.live_worker import settings_from_row

needs_ffmpeg = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not installed")

W, H = 1280, 720
# The cam box covers the magenta block drawn into the generated source
# (x 896..1280, y 0..252); the game box is the whole frame.
BOXES = {"cam": {"x": 0.7, "y": 0.0, "w": 0.3, "h": 0.35},
         "game": {"x": 0.0, "y": 0.0, "w": 1.0, "h": 1.0}}


# ---------------------------------------------------------------------------
# validate_boxes
# ---------------------------------------------------------------------------

def test_validate_boxes_good_input_defaults_to_top():
    out = validate_boxes(BOXES)
    assert out == {"cam": {"x": 0.7, "y": 0.0, "w": 0.3, "h": 0.35},
                   "game": {"x": 0.0, "y": 0.0, "w": 1.0, "h": 1.0},
                   "arrange": "top"}


def test_validate_boxes_none_means_absent():
    assert validate_boxes(None) is None


def test_validate_boxes_clamps_to_the_frame_edge():
    out = validate_boxes({"cam": {"x": 0.9, "y": 0.9, "w": 0.5, "h": 0.5},
                          "game": {"x": 0, "y": 0, "w": 1, "h": 1}, "arrange": "bottom"})
    assert out["cam"] == {"x": 0.9, "y": 0.9, "w": 0.1, "h": 0.1}
    assert out["arrange"] == "bottom"


@pytest.mark.parametrize("bad", [
    {"game": {"x": 0, "y": 0, "w": 1, "h": 1}},                                        # cam missing
    {"cam": {"x": 0.1, "y": 0.1, "w": 0.3}, "game": {"x": 0, "y": 0, "w": 1, "h": 1}},  # cam missing a key
    {"cam": {"x": 0.1, "y": 0.1, "w": 0.3, "h": "tall"}, "game": {"x": 0, "y": 0, "w": 1, "h": 1}},
    "not-a-dict",
])
def test_validate_boxes_rejects_bad_shapes(bad):
    with pytest.raises(ValueError):
        validate_boxes(bad)


@pytest.mark.parametrize("box", [
    {"x": 1.0, "y": 0.0, "w": 0.3, "h": 0.3},    # starts outside the frame
    {"x": -0.1, "y": 0.0, "w": 0.3, "h": 0.3},   # negative origin
    {"x": 0.0, "y": 0.0, "w": 0.01, "h": 0.3},   # too small to be a panel
    {"x": 0.0, "y": 0.0, "w": 1.5, "h": 0.3},    # wider than the frame
])
def test_validate_boxes_rejects_boxes_outside_the_frame(box):
    with pytest.raises(ValueError, match="inside the frame"):
        validate_boxes({"cam": box, "game": {"x": 0, "y": 0, "w": 1, "h": 1}})


def test_validate_boxes_rejects_a_bad_arrange():
    with pytest.raises(ValueError, match="arrange"):
        validate_boxes({**BOXES, "arrange": "sideways"})


# ---------------------------------------------------------------------------
# build_manual_stack_layout
# ---------------------------------------------------------------------------

def _assert_crop_inside(crop, aspect):
    cw, ch, x, y = crop
    assert cw % 2 == 0 and ch % 2 == 0 and x % 2 == 0 and y % 2 == 0
    assert 0 <= x and 0 <= y and x + cw <= W and y + ch <= H
    assert abs(cw / ch - aspect) < 0.02 * aspect  # even rounding only


def test_manual_layout_crops_are_inside_even_and_the_panel_aspects():
    layout = build_manual_stack_layout(W, H, validate_boxes(BOXES), "facecam_red")
    assert layout["kind"] == "reaction" and layout["manual"] is True
    _assert_crop_inside(layout["face_crop"], OUT_W / FACE_PANEL_H)
    _assert_crop_inside(layout["game_crop"], OUT_W / GAME_PANEL_H)
    # The face crop stays inside the drawn cam box (896..1280, 0..252).
    _, _, fx, fy = layout["face_crop"]
    fw, fh = layout["face_crop"][0], layout["face_crop"][1]
    assert 896 <= fx and fx + fw <= W and 0 <= fy and fy + fh <= 252
    assert "[face][game]vstack=inputs=2" in layout["video_filter"]


def test_manual_layout_bottom_swaps_the_stack_order():
    top = build_manual_stack_layout(W, H, validate_boxes(BOXES), "facecam_gold")
    bottom = build_manual_stack_layout(W, H, validate_boxes({**BOXES, "arrange": "bottom"}), "facecam_gold")
    assert "[face][game]vstack=inputs=2" in top["video_filter"]
    assert "[game][face]vstack=inputs=2" in bottom["video_filter"]
    # The seam (and its captions) moves with the face panel.
    assert bottom["caption_y"] > top["caption_y"]


def test_manual_layout_credit_is_drawn_on_the_game_panel():
    # The credit text itself never enters the graph (user text rides in a
    # drawtext textfile); the game chain gains the drawtext segment.
    with_credit = build_manual_stack_layout(W, H, validate_boxes(BOXES), "facecam_red", credit="@Someone")
    without = build_manual_stack_layout(W, H, validate_boxes(BOXES), "facecam_red")
    assert "drawtext=textfile=" in with_credit["video_filter"]
    assert "drawtext=" not in without["video_filter"]


# ---------------------------------------------------------------------------
# The reframing / planning hooks honour MANUAL_BOXES
# ---------------------------------------------------------------------------

def test_reframe_plan_delegates_to_plain_when_boxes_are_set():
    token = MANUAL_BOXES.set(validate_boxes(BOXES))
    try:
        plan = build_reframe_plan({"width": W, "height": H}, [(0.0, 3.0)], 3.0,
                                  source_path=Path("unused.mp4"))
    finally:
        MANUAL_BOXES.reset(token)
    assert plan == {"delegate": "plain", "shots": []}


# ---------------------------------------------------------------------------
# API: session create validation + settings round-trip, frame endpoint
# ---------------------------------------------------------------------------

@pytest.fixture()
async def clean_live():
    async def wipe():
        async with AsyncSessionLocal() as db:
            await db.execute(text("DELETE FROM live_sessions WHERE user_id LIKE 'user-%' OR user_id LIKE 'live-test-%'"))
            await db.commit()

    await wipe()
    yield
    await wipe()


@pytest.fixture()
def live_on(monkeypatch, tmp_path):
    monkeypatch.setenv("LIVE_CLIPPING", "on")
    monkeypatch.setenv("TEMP_DIR", str(tmp_path))
    monkeypatch.setattr(routes, "probe_live_status", lambda url: "is_live")


@pytest.mark.parametrize("boxes,fragment", [
    ({**BOXES, "arrange": "sideways"}, "arrange"),
    ({"cam": {"x": 1.0, "y": 0.0, "w": 0.3, "h": 0.3}, "game": {"x": 0, "y": 0, "w": 1, "h": 1}}, "inside the frame"),
    ({"game": {"x": 0, "y": 0, "w": 1, "h": 1}}, "boxes.cam is required"),
])
async def test_bad_boxes_are_422(client, auth_headers, live_on, clean_live, boxes, fragment):
    response = await client.post("/live/sessions", headers=auth_headers,
                                 json={"url": "https://www.twitch.tv/x", "boxes": boxes})
    assert response.status_code == 422, response.text
    assert fragment in response.text


async def test_good_boxes_round_trip_through_the_settings_jsonb(client, auth_headers, live_on, clean_live):
    response = await client.post("/live/sessions", headers=auth_headers,
                                 json={"url": "https://www.youtube.com/watch?v=abc", "boxes": BOXES})
    assert response.status_code == 201, response.text
    cleaned = validate_boxes(BOXES)
    session = response.json()["session"]
    assert session["settings"]["boxes"] == cleaned
    # The worker rebuilds LiveSettings from the stored row: boxes survive.
    settings = settings_from_row({"url": session["url"], "settings": session["settings"]})
    assert settings.boxes == cleaned


async def test_no_boxes_stays_none(client, auth_headers, live_on, clean_live):
    response = await client.post("/live/sessions", headers=auth_headers,
                                 json={"url": "https://www.youtube.com/watch?v=abc"})
    assert response.status_code == 201, response.text
    assert response.json()["session"]["settings"]["boxes"] is None


async def test_frame_route_is_closed_while_the_switch_is_off(client, auth_headers, monkeypatch):
    monkeypatch.delenv("LIVE_CLIPPING", raising=False)
    response = await client.get("/live/frame", headers=auth_headers,
                                params={"url": "https://www.twitch.tv/x", "at": 0})
    assert response.status_code == 403


async def test_frame_rejects_a_non_stream_url(client, auth_headers, live_on):
    response = await client.get("/live/frame", headers=auth_headers,
                                params={"url": "https://example.com/video", "at": 0})
    assert response.status_code == 422


async def test_frame_returns_the_grabbed_jpeg(client, auth_headers, live_on, monkeypatch):
    seen = {}

    def fake_grab(url, at):
        seen["url"], seen["at"] = url, at
        return b"\xff\xd8\xff\xe0fake-jpeg"

    monkeypatch.setattr(routes, "grab_frame_jpeg", fake_grab)
    response = await client.get("/live/frame", headers=auth_headers,
                                params={"url": "https://www.twitch.tv/someone", "at": 1200})
    assert response.status_code == 200, response.text
    assert response.headers["content-type"] == "image/jpeg"
    assert response.content == b"\xff\xd8\xff\xe0fake-jpeg"
    assert seen == {"url": "https://www.twitch.tv/someone", "at": 1200.0}


async def test_frame_surfaces_a_grab_failure_as_502(client, auth_headers, live_on, monkeypatch):
    def failing_grab(url, at):
        raise HTTPException(status_code=502, detail="Could not open that stream link")

    monkeypatch.setattr(routes, "grab_frame_jpeg", failing_grab)
    response = await client.get("/live/frame", headers=auth_headers,
                                params={"url": "https://www.youtube.com/watch?v=abc", "at": 0})
    assert response.status_code == 502


# ---------------------------------------------------------------------------
# Real render: a generated 1280x720 source through the manual layout
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def box_source(tmp_path_factory):
    """1280x720, cyan everywhere except a magenta block where the cam box is."""
    out = tmp_path_factory.mktemp("boxes") / "source.mp4"
    cmd = ["ffmpeg", "-y", "-v", "error",
           "-f", "lavfi", "-i", "color=c=0x00FFFF:s=1280x720:d=3:r=30",
           "-f", "lavfi", "-i", "color=c=0xFF00FF:s=384x252:d=3:r=30",
           "-f", "lavfi", "-i", "sine=frequency=440:d=3",
           "-filter_complex", "[0:v][1:v]overlay=896:0[vout]",
           "-map", "[vout]", "-map", "2:a",
           "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", str(out)]
    subprocess.run(cmd, check=True, capture_output=True, text=True)
    return out


def _render(source: Path, video_filter: str, out: Path) -> None:
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-i", str(source),
                    "-filter_complex", video_filter, "-map", "[vout]", "-map", "0:a",
                    "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", str(out)],
                   check=True, capture_output=True, text=True)


def _probe_dims(path: Path):
    out = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0",
                          "-show_entries", "stream=width,height", "-of", "json", str(path)],
                         check=True, capture_output=True, text=True).stdout
    stream = json.loads(out)["streams"][0]
    return stream["width"], stream["height"]


def _centre_pixel(path: Path, x: int, y: int):
    raw = subprocess.run(["ffmpeg", "-v", "error", "-i", str(path),
                          "-vf", f"crop=16:16:{x}:{y},scale=1:1", "-frames:v", "1",
                          "-f", "rawvideo", "-pix_fmt", "rgb24", "-"],
                         check=True, capture_output=True).stdout
    assert len(raw) == 3
    return tuple(raw)


def _is_magenta(px):
    r, g, b = px
    return r > 150 and g < 110 and b > 150


def _is_cyan(px):
    r, g, b = px
    return r < 110 and g > 150 and b > 150


@needs_ffmpeg
def test_describe_honours_boxes_and_the_render_is_1080x1920(box_source, tmp_path):
    boxes = validate_boxes(BOXES)
    token = MANUAL_BOXES.set(boxes)
    try:
        described = describe_vertical_layout(box_source, W, H, layout_template="facecam_red")
    finally:
        MANUAL_BOXES.reset(token)
    assert described["kind"] == "reaction"
    assert described["video_filter"] == build_manual_stack_layout(W, H, boxes, "facecam_red")["video_filter"]

    out = tmp_path / "manual_top.mp4"
    _render(box_source, described["video_filter"], out)
    assert _probe_dims(out) == (OUT_W, OUT_H)
    face_centre = (OUT_W // 2 - 8, FACE_PANEL_H // 2 - 8)
    game_centre = (OUT_W // 2 - 8, FACE_PANEL_H + GAME_PANEL_H // 2 - 8)
    assert _is_magenta(_centre_pixel(out, *face_centre))  # the drawn cam box fills the top panel
    assert _is_cyan(_centre_pixel(out, *game_centre))     # gameplay below


@needs_ffmpeg
def test_render_bottom_puts_the_game_on_top(box_source, tmp_path):
    boxes = validate_boxes({**BOXES, "arrange": "bottom"})
    layout = build_manual_stack_layout(W, H, boxes, "facecam_red")
    out = tmp_path / "manual_bottom.mp4"
    _render(box_source, layout["video_filter"], out)
    assert _probe_dims(out) == (OUT_W, OUT_H)
    game_centre = (OUT_W // 2 - 8, GAME_PANEL_H // 2 - 8)
    face_centre = (OUT_W // 2 - 8, GAME_PANEL_H + FACE_PANEL_H // 2 - 8)
    assert _is_cyan(_centre_pixel(out, *game_centre))
    assert _is_magenta(_centre_pixel(out, *face_centre))
