"""User-uploaded SFX render proof (real FFmpeg).

The library suites prove an uploaded effect is listed, previewable
and placeable. This suite proves the last leg of the full Studio
path: an effect that exists ONLY in the user SFX folder is actually
mixed into an exported render at its placed second, measured in the
export's audio — and the same render without the placement stays
silent there.
"""

import shutil
import subprocess
from pathlib import Path

import numpy as np
import pytest

from src.media import edl_render
from src.media.gap_activity import _window_levels_db

ffmpeg_missing = shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None
pytestmark = pytest.mark.skipif(ffmpeg_missing, reason="ffmpeg/ffprobe not installed")


def _run(command):
    result = subprocess.run(command, capture_output=True, text=True, timeout=600)
    assert result.returncode == 0, result.stderr
    return result


def _window_rms_db(path: Path, start: float, end: float) -> float:
    levels = _window_levels_db(path, 0, start, end)
    assert len(levels) > 0
    return float(10.0 * np.log10(np.mean(np.power(10.0, levels / 10.0))))


def test_uploaded_sfx_is_mixed_into_export(tmp_path, monkeypatch):
    monkeypatch.setenv("FORCE_CPU_ENCODE", "true")
    # Silent 8 s grey source: any energy in the export came from the mix.
    source = tmp_path / "source.mp4"
    _run([
        "ffmpeg", "-y", "-v", "error",
        "-f", "lavfi", "-i", "color=c=0x404040:size=320x568:rate=30:duration=8",
        "-f", "lavfi", "-i", "anullsrc=channel_layout=stereo:sample_rate=48000",
        "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-t", "8",
        str(source),
    ])
    # The effect lives ONLY in the user folder (the bundled assets tree
    # is redirected to an empty one), exactly like an in-app upload.
    user_dir = tmp_path / "user_sfx"
    user_dir.mkdir()
    _run([
        "ffmpeg", "-y", "-v", "error",
        "-f", "lavfi", "-i", "aevalsrc=0.5*sin(2*PI*900*t):s=48000:d=1",
        "-c:a", "pcm_s16le", str(user_dir / "riser.wav"),
    ])
    assets = tmp_path / "assets"
    (assets / "sfx").mkdir(parents=True)
    (assets / "music").mkdir(parents=True)
    monkeypatch.setattr(edl_render, "ASSETS_DIR", assets)
    monkeypatch.setattr(
        "src.media.sfx_library.user_sfx_dir", lambda: user_dir
    )

    base_edl = {
        "duration": 8.0,
        "segments": [
            {"index": 0, "start": 0.0, "end": 8.0, "transition_in": "none",
             "transition_duration": 0.0},
        ],
        "zoom_keyframes": [],
        "overlays": [],
        "captions": {"preset_id": "default"},
        "audio": {},
    }
    with_sfx = tmp_path / "with-sfx.mp4"
    assert edl_render.render_edl_clip_ffmpeg(
        source, with_sfx,
        {**base_edl, "sfx_events": [{"name": "riser.wav", "at_sec": 3.0, "gain_db": 0.0}]},
        assets_dir=assets,
    ) is True
    control = tmp_path / "control.mp4"
    assert edl_render.render_edl_clip_ffmpeg(
        source, control, base_edl, assets_dir=assets,
    ) is True

    placed = _window_rms_db(with_sfx, 3.1, 3.9)
    quiet = _window_rms_db(with_sfx, 5.5, 7.5)
    control_placed = _window_rms_db(control, 3.1, 3.9)
    assert placed > -40.0, f"uploaded effect missing at its second: {placed:.1f} dB"
    assert placed - quiet >= 15.0, (
        f"effect window {placed:.1f} dB vs quiet window {quiet:.1f} dB"
    )
    assert control_placed < -50.0, (
        f"control unexpectedly carries the effect: {control_placed:.1f} dB"
    )
