"""Split border trim — plan wiring proof.

A synthetic two-speaker source is fabricated with the source's own
border furniture planted around the feeds (white frame lines, like
the YMH canvas in the Bert clip): a vertical line inside the left
tile's window, a horizontal line across the left feed, and a vertical
line inside the right tile's window. The analysis is fabricated (the
caption face-guard lane's seam), planning is the real
``attach_reframe_plan``, and the assertions are on the resolved
segment: the trim is stamped per shot and per layer, and the tile
windows are inset by exactly it.

Controls: with BORDER_TRIM_ENABLED=0 the same attach produces the
untrimmed tiles and no stamps; on a furniture-free source the enabled
attach resolves byte-identically to the disabled one.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from src.media import reframe_plan
from src.media.scene_context import SceneEvidence

W, H = 1920, 1080
LEFT = (300.0, 320.0, 300.0, 360.0)
RIGHT = (1250.0, 320.0, 300.0, 360.0)
DURATION = 6.0

# _split_region on the fabricated faces: tile_w = 920, tile_h = 788.
LEFT_TILE = {"tile_x": 0, "tile_y": 170, "tile_w": 920, "tile_h": 788}
RIGHT_TILE = {"tile_x": 940, "tile_y": 170, "tile_w": 920, "tile_h": 788}


def _analysis():
    rows = [[LEFT, RIGHT]] * 18
    return {
        "width": W,
        "height": H,
        "samples": [(i / 3.0, list(boxes)) for i, boxes in enumerate(rows)],
        "cuts": [],
        "context": [
            SceneEvidence(
                time=i / 3.0, face_area=0.052, face_count=2, face_span=0.651
            )
            for i in range(len(rows))
        ],
    }


def _edl():
    return {
        "clip_id": "border-trim",
        "duration": DURATION,
        "source_ranges": [[0.0, DURATION]],
        "segments": [
            {
                "index": 0,
                "start": 0.0,
                "end": DURATION,
                "transition_in": "none",
                "transition_duration": 0,
            }
        ],
    }


def _make_source(path: Path, furniture: bool) -> Path:
    boxes = [
        # Feeds, heads, face-centre markers (the split lane's fabric).
        "drawbox=x=52:y=140:w=848:h=620:color=0xCC2020:t=fill",
        "drawbox=x=300:y=320:w=300:h=360:color=0xD9A066:t=fill",
        "drawbox=x=420:y=470:w=60:h=60:color=white:t=fill",
        "drawbox=x=1020:y=140:w=848:h=620:color=0x2040CC:t=fill",
        "drawbox=x=1250:y=320:w=300:h=360:color=0xD9A066:t=fill",
        "drawbox=x=1370:y=470:w=60:h=60:color=white:t=fill",
    ]
    if furniture:
        boxes += [
            # The feeds' frame lines, inside the tile windows.
            "drawbox=x=40:y=0:w=12:h=1080:color=white:t=fill",
            "drawbox=x=52:y=200:w=848:h=10:color=white:t=fill",
            "drawbox=x=1840:y=0:w=12:h=1080:color=white:t=fill",
        ]
    subprocess.run(
        [
            "ffmpeg", "-v", "error", "-y",
            "-f", "lavfi", "-i",
            f"color=c=0x202020:size=1920x1080:rate=30:duration={int(DURATION)}",
            "-f", "lavfi", "-i", "sine=frequency=440:duration=6",
            "-vf", ",".join(boxes),
            "-c:v", "libx264", "-preset", "veryfast", "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-shortest", str(path),
        ],
        check=True,
        capture_output=True,
    )
    return path


def _attach(monkeypatch, source: Path, enabled: bool):
    monkeypatch.setattr(
        reframe_plan, "get_source_analysis", lambda path, spans: _analysis()
    )
    if enabled:
        monkeypatch.delenv("BORDER_TRIM_ENABLED", raising=False)
    else:
        monkeypatch.setenv("BORDER_TRIM_ENABLED", "0")
    edl = _edl()
    assert reframe_plan.attach_reframe_plan(edl, source, [(0.0, DURATION)], False)
    return edl


@pytest.fixture(scope="module")
def sources(tmp_path_factory):
    folder = tmp_path_factory.mktemp("bordertrim")
    return {
        "furniture": _make_source(folder / "furniture.mp4", True),
        "clean": _make_source(folder / "clean.mp4", False),
    }


def test_trims_stamped_and_tiles_inset(monkeypatch, sources):
    edl = _attach(monkeypatch, sources["furniture"], enabled=True)
    shot = edl["reframe"]["shots"][0]
    assert shot["mode"] == "split"
    trims = shot["border_trim"]
    # Left tile: vertical line ends at source x=52 (window col 52);
    # horizontal line ends at window row 40 (+ up to 3 transition px).
    assert 48 <= trims["left"]["left"] <= 55
    assert 37 <= trims["left"]["top"] <= 43
    assert trims["left"]["right"] == 0
    assert trims["left"]["bottom"] == 0
    # Right tile: line's far side is 20 px from the window's right edge.
    assert 17 <= trims["right"]["right"] <= 23
    assert trims["right"]["left"] == 0

    layer = edl["segments"][0]["reframe"]["split_layers"][0]
    assert layer["border_trim"] == trims
    left, right = layer["left"], layer["right"]
    assert left["tile_x"] == LEFT_TILE["tile_x"] + trims["left"]["left"]
    assert left["tile_w"] % 2 == 0
    assert abs(left["tile_w"] - (920 - trims["left"]["left"] - trims["left"]["right"])) <= 1
    assert left["tile_y"] == LEFT_TILE["tile_y"] + trims["left"]["top"]
    assert right["tile_x"] == RIGHT_TILE["tile_x"]
    assert right["tile_w"] % 2 == 0
    assert abs(right["tile_w"] - (920 - trims["right"]["right"])) <= 1
    # The face guard boxes are stamped through the INSET tiles, so
    # they still describe the rendered faces.
    assert edl["segments"][0]["reframe"]["face_boxes"]


def test_disabled_env_restores_untrimmed_geometry(monkeypatch, sources):
    edl = _attach(monkeypatch, sources["furniture"], enabled=False)
    shot = edl["reframe"]["shots"][0]
    assert "border_trim" not in shot
    layer = edl["segments"][0]["reframe"]["split_layers"][0]
    assert "border_trim" not in layer
    assert all(layer["left"][k] == v for k, v in LEFT_TILE.items())
    assert all(layer["right"][k] == v for k, v in RIGHT_TILE.items())


def test_clean_source_plans_identically_on_and_off(monkeypatch, sources):
    off = _attach(monkeypatch, sources["clean"], enabled=False)
    on = _attach(monkeypatch, sources["clean"], enabled=True)
    assert "border_trim" not in on["reframe"]["shots"][0]
    assert on["segments"][0]["reframe"] == off["segments"][0]["reframe"]
    assert on["reframe"] == off["reframe"]


def test_inset_tile_guards():
    tile = {"tile_x": 10, "tile_y": 20, "tile_w": 200, "tile_h": 160}
    assert reframe_plan._inset_tile(tile, None) is tile
    assert reframe_plan._inset_tile(tile, {"left": 0, "right": 0, "top": 0, "bottom": 0}) is tile
    inset = reframe_plan._inset_tile(tile, {"left": 12, "right": 8, "top": 4, "bottom": 6})
    assert inset == {"tile_x": 22, "tile_y": 24, "tile_w": 180, "tile_h": 150}
    # A trim that would leave a degenerate tile is refused whole.
    assert reframe_plan._inset_tile(tile, {"left": 150, "right": 0, "top": 0, "bottom": 0}) is tile
