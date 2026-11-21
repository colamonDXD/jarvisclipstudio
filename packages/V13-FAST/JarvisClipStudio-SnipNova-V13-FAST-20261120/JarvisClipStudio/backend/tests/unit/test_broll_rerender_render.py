"""B-roll carry render proof (real FFmpeg).

The unit suite (test_clip_rerender.py) proves a re-render's EDL
carries the clip's saved B-roll placements. This suite proves the
carried entries actually composite into the exported file: a clip
rendered through the EDL renderer with a carried entry (the shape
``ClipService._carried_broll_entries`` produces) shows the stock
footage full-frame inside its window and the plain clip picture
outside it, while the same render without the entry never shows the
stock colour.
"""

import shutil
import subprocess
from pathlib import Path

import cv2
import numpy as np
import pytest

from src.media import edl_render

ffmpeg_missing = shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None
pytestmark = pytest.mark.skipif(ffmpeg_missing, reason="ffmpeg/ffprobe not installed")

STOCK_BGR = (0, 0, 255)  # solid red stock footage


def _run(command):
    result = subprocess.run(command, capture_output=True, text=True, timeout=600)
    assert result.returncode == 0, result.stderr
    return result


def _make_clip(path: Path, colour: str, duration: float = 8.0) -> None:
    _run([
        "ffmpeg", "-y", "-v", "error",
        "-f", "lavfi", "-i",
        f"color=c={colour}:size=320x568:rate=30:duration={duration}",
        "-f", "lavfi", "-i", "anullsrc=channel_layout=stereo:sample_rate=48000",
        "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac",
        "-t", str(duration), str(path),
    ])


def _frame(video: Path, at_seconds: float, out: Path) -> np.ndarray:
    _run([
        "ffmpeg", "-y", "-v", "error", "-ss", str(at_seconds), "-i", str(video),
        "-frames:v", "1", str(out),
    ])
    image = cv2.imread(str(out))
    assert image is not None
    return image


def _near(image: np.ndarray, bgr, tol: int = 45) -> int:
    target = np.array(bgr, dtype=np.int16)
    diff = np.abs(image.astype(np.int16) - target)
    return int(np.all(diff <= tol, axis=2).sum())


def _render(tmp_path: Path, name: str, edl: dict) -> Path:
    source = tmp_path / "source.mp4"
    if not source.exists():
        _make_clip(source, "0x404040")
    out = tmp_path / name
    assert edl_render.render_edl_clip_ffmpeg(source, out, edl) is True
    return out


def _base_edl() -> dict:
    return {
        "duration": 8.0,
        "segments": [
            {"index": 0, "start": 0.0, "end": 8.0, "transition_in": "none",
             "transition_duration": 0.0},
        ],
        "zoom_keyframes": [],
        "overlays": [],
        "captions": {"preset_id": "default"},
    }


def test_carried_broll_entry_composites_into_export(tmp_path, monkeypatch):
    monkeypatch.setenv("FORCE_CPU_ENCODE", "true")
    stock = tmp_path / "stock.mp4"
    _make_clip(stock, "red", duration=4.0)

    # The exact entry shape the re-render carry stamps.
    carried = {
        "start": 3.0,
        "end": 5.5,
        "source_start": 0.0,
        "duration": 2.5,
        "footage_offset": 0.0,
        "provider": "pixabay",
        "id": 4242,
        "local_path": str(stock),
        "query": "city",
    }
    with_broll = _render(tmp_path, "with-broll.mp4", {**_base_edl(), "broll": [carried]})
    without = _render(tmp_path, "without-broll.mp4", _base_edl())

    inside = _frame(with_broll, 4.0, tmp_path / "inside.png")
    outside = _frame(with_broll, 1.0, tmp_path / "outside.png")
    control = _frame(without, 4.0, tmp_path / "control.png")

    total = inside.shape[0] * inside.shape[1]
    assert _near(inside, STOCK_BGR) > total * 0.8, "stock window not composited"
    assert _near(outside, STOCK_BGR) < total * 0.05, "stock leaked outside its window"
    assert _near(control, STOCK_BGR) < total * 0.05, "control unexpectedly shows stock"
