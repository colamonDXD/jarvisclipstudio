"""Split border trim — RENDER-level proof.

The synthetic source of the wiring suite (two feeds, white frame
lines planted inside the tile windows) is rendered through the real
``render_edl_clip_ffmpeg`` twice: once with BORDER_TRIM_ENABLED=0
(today's geometry) and once with it on. The measured invariant:

- BEFORE: the planted lines survive as near-white column runs inside
  the output's outer 90 px — top tile's left zone, bottom tile's
  right zone (and only there);
- AFTER: zero such columns in all four edge zones, while both faces
  (their white centre markers) stay fully in frame, centred on the
  plan's stamped face boxes.

The Japan test runs the same pair on real footage: the source has no
border furniture, so the enabled plan must equal the disabled plan
exactly and the renders must be equivalent frames.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import numpy as np
import pytest

from src.media import edl_render, reframe_plan
from src.media.reframe_plan import collect_face_zones
from src.media.scene_context import SceneEvidence

W, H = 1920, 1080
LEFT = (300.0, 320.0, 300.0, 360.0)
RIGHT = (1250.0, 320.0, 300.0, 360.0)
DURATION = 6.0

JAPAN_SOURCE = Path.home() / "workspace" / "qm-source" / "source.mp4"


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
            for i in range(18)
        ],
    }


def _edl(duration=DURATION):
    return {
        "clip_id": "border-trim-render",
        "duration": duration,
        "source_ranges": [[0.0, duration]],
        "segments": [
            {
                "index": 0,
                "start": 0.0,
                "end": duration,
                "transition_in": "none",
                "transition_duration": 0,
            }
        ],
    }


def _make_source(path: Path) -> Path:
    boxes = [
        "drawbox=x=52:y=140:w=848:h=620:color=0xCC2020:t=fill",
        "drawbox=x=300:y=320:w=300:h=360:color=0xD9A066:t=fill",
        "drawbox=x=420:y=470:w=60:h=60:color=white:t=fill",
        "drawbox=x=1020:y=140:w=848:h=620:color=0x2040CC:t=fill",
        "drawbox=x=1250:y=320:w=300:h=360:color=0xD9A066:t=fill",
        "drawbox=x=1370:y=470:w=60:h=60:color=white:t=fill",
        # The feeds' frame furniture, inside the tile windows.
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


def _frame_rgb(video: Path, t: float) -> np.ndarray:
    raw = subprocess.run(
        [
            "ffmpeg", "-v", "error", "-ss", str(t), "-i", str(video),
            "-frames:v", "1", "-f", "rawvideo", "-pix_fmt", "rgb24", "-",
        ],
        capture_output=True,
        check=True,
    ).stdout
    return np.frombuffer(raw, dtype=np.uint8).reshape(1920, 1080, 3).astype(np.int32)


def _white_columns(img: np.ndarray, y0: int, y1: int, x0: int, x1: int) -> int:
    """Columns in the zone that are >=85% near-pure-white pixels."""
    sub = img[y0:y1, x0:x1]
    near_white = np.abs(sub - 255).sum(axis=2) < 24
    return int((near_white.mean(axis=0) >= 0.85).sum())


def _zones_at(zones, t):
    return [
        box
        for start, end, boxes in zones
        if start <= t < end
        for box in boxes
    ]


def _attach(monkeypatch, source: Path, enabled: bool, edl=None, span=None):
    monkeypatch.setattr(
        reframe_plan, "get_source_analysis", lambda path, spans: _analysis()
    )
    if enabled:
        monkeypatch.delenv("BORDER_TRIM_ENABLED", raising=False)
    else:
        monkeypatch.setenv("BORDER_TRIM_ENABLED", "0")
    edl = edl if edl is not None else _edl()
    span = span if span is not None else (0.0, DURATION)
    assert reframe_plan.attach_reframe_plan(edl, source, [span], False)
    return edl


@pytest.fixture(scope="module")
def synthetic(tmp_path_factory):
    folder = tmp_path_factory.mktemp("bordertrim-render")
    return folder, _make_source(folder / "furniture.mp4")


def test_rendered_slivers_vanish_faces_stay(monkeypatch, synthetic, tmp_path):
    folder, source = synthetic
    renders = {}
    for variant, enabled in (("before", False), ("after", True)):
        edl = _attach(monkeypatch, source, enabled)
        out = folder / f"{variant}.mp4"
        assert edl_render.render_edl_clip_ffmpeg(source, out, edl), out
        renders[variant] = (out, collect_face_zones(edl))

    zones = {
        "top_left": (0, 960, 0, 90),
        "top_right": (0, 960, 990, 1080),
        "bottom_left": (960, 1920, 0, 90),
        "bottom_right": (960, 1920, 990, 1080),
    }
    frames = {
        variant: _frame_rgb(renders[variant][0], 2.0)
        for variant in ("before", "after")
    }
    counts = {
        variant: {
            name: _white_columns(frames[variant], *zone)
            for name, zone in zones.items()
        }
        for variant in ("before", "after")
    }
    # The defect is measurable before, and ONLY where it was planted.
    assert counts["before"]["top_left"] >= 6, counts["before"]
    assert counts["before"]["bottom_right"] >= 6, counts["before"]
    assert counts["before"]["top_right"] < 6, counts["before"]
    assert counts["before"]["bottom_left"] < 6, counts["before"]
    # After the trim: no furniture columns in any edge zone.
    assert all(value == 0 for value in counts["after"].values()), counts["after"]

    # Faces fully in frame in both variants: each stamped face box
    # holds its white centre marker; after the trim the marker's
    # centroid still sits on the box centre (the boxes moved WITH the
    # inset tiles — nothing was clipped or shifted off).
    for variant in ("before", "after"):
        boxes = _zones_at(renders[variant][1], 2.0)
        assert len(boxes) == 2, variant
        img = frames[variant]
        for box in boxes:
            x, y, w, h = (int(round(v)) for v in box)
            region = img[y:y + h, x:x + w]
            white = np.abs(region - 255).sum(axis=2) < 90
            assert int(white.sum()) >= 1500, (variant, box)
            if variant == "after":
                ys, xs = np.nonzero(white)
                assert abs(float(xs.mean()) + x - (x + w / 2)) <= 40
                assert abs(float(ys.mean()) + y - (y + h / 2)) <= 40


@pytest.mark.skipif(not JAPAN_SOURCE.exists(), reason="Japan fixture absent")
def test_japan_split_plan_and_render_unchanged(monkeypatch, tmp_path):
    # Probe the corroborated span for a real split shot (cached
    # analysis; plan shots are clip-local), exactly as the face-guard
    # render suite does.
    probe = {
        "clip_id": "japan-probe-border",
        "duration": 50.0,
        "source_ranges": [[1310.0, 1360.0]],
        "segments": [
            {
                "index": 0,
                "start": 0.0,
                "end": 50.0,
                "source_start": 1310.0,
                "transition_in": "none",
                "transition_duration": 0,
            }
        ],
    }
    assert reframe_plan.attach_reframe_plan(
        probe, JAPAN_SOURCE, [(1310.0, 1360.0)], False
    ) is True
    split_shots = [
        s for s in probe["reframe"]["shots"] if s["mode"] == "split"
    ]
    assert split_shots, "the corroborated window must contain split shots"
    shot = max(split_shots, key=lambda s: s["end"] - s["start"])
    win_start = max(1310.0, 1310.0 + shot["start"] - 5.0)
    win_end = min(1360.0, 1310.0 + shot["end"] + 5.0)
    duration = win_end - win_start

    variants = {}
    for name, enabled in (("off", False), ("on", True)):
        edl = _edl(duration)
        edl["source_ranges"] = [[win_start, win_end]]
        edl["segments"][0]["source_start"] = win_start
        if enabled:
            monkeypatch.delenv("BORDER_TRIM_ENABLED", raising=False)
        else:
            monkeypatch.setenv("BORDER_TRIM_ENABLED", "0")
        assert reframe_plan.attach_reframe_plan(
            edl, JAPAN_SOURCE, [(win_start, win_end)], False
        ) is True
        segment = edl["segments"][0]
        assert "split_layers" in segment["reframe"], "window must stay split"
        variants[name] = edl

    on = variants["on"]
    assert all(
        "border_trim" not in s for s in on["reframe"]["shots"]
    ), "real footage without furniture must carry no trim stamp"
    assert on["segments"][0]["reframe"] == variants["off"]["segments"][0]["reframe"]
    assert on["reframe"] == variants["off"]["reframe"]

    # Equivalent frames: render both plans and compare inside the
    # split interval.
    interval = on["segments"][0]["reframe"]["split_layers"][0]["intervals"][0]
    t = (interval[0] + interval[1]) / 2
    frames = {}
    for name, edl in variants.items():
        out = tmp_path / f"japan_{name}.mp4"
        assert edl_render.render_edl_clip_ffmpeg(JAPAN_SOURCE, out, edl), out
        frames[name] = _frame_rgb(out, t)
    diff = np.abs(frames["on"] - frames["off"]).mean()
    assert diff <= 1.0, f"Japan renders diverged: mean abs diff {diff}"
