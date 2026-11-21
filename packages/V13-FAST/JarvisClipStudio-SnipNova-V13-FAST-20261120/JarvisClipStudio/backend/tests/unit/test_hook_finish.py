"""Hook finish: punch-in, brightness lift for dark clips, whoosh; Plain untouched."""
from __future__ import annotations

import hashlib
import subprocess

import numpy as np
import pytest

from src.media import hook_finish as HF


def _clip(path, color="0x202020", seconds=8, audio=True):
    cmd = ["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", f"testsrc2=s=270x480:d={seconds}"]
    if audio:
        cmd += ["-f", "lavfi", "-i", f"sine=frequency=300:duration={seconds}"]
    vf = f"drawbox=t=fill:c={color}@0.85"
    cmd += ["-vf", vf, "-shortest", "-c:v", "libx264", "-pix_fmt", "yuv420p"]
    if audio:
        cmd += ["-c:a", "aac"]
    cmd.append(str(path))
    subprocess.run(cmd, check=True)
    return path


def _frame(path, t):
    raw = subprocess.run(["ffmpeg", "-v", "error", "-ss", str(t), "-i", str(path), "-frames:v", "1",
                          "-vf", "format=gray", "-f", "rawvideo", "-"], capture_output=True).stdout
    return np.frombuffer(raw, np.uint8)


def test_dark_clip_gets_lift_punch_and_keeps_length(tmp_path, monkeypatch):
    monkeypatch.setattr("src.media.edl_render.check_nvenc_available", lambda: False, raising=False)
    clip = _clip(tmp_path / "dark.mp4")
    before_luma = HF.mean_luma(clip, 8.0)
    before_mid = _frame(clip, 3.0)
    assert HF.finish_hook(clip, {"segments": [{}]}, seed="x") is True
    info = HF._probe(clip)
    assert abs(info["duration"] - 8.0) < 0.3 and info["width"] == 270 and info["audio"]
    assert HF.mean_luma(clip, 8.0) > before_luma + 5  # lifted
    assert not np.array_equal(_frame(clip, 3.0), before_mid)  # punched in at 2.6-3.8 s


def test_bright_clip_is_not_lifted():
    assert HF.brightness_filter(130.0) is None
    f = HF.brightness_filter(80.0)
    assert f.startswith("eq=brightness=") and float(f.split("=")[2].split(":")[0]) <= 0.12


def test_plain_short_and_switch_off_are_untouched(tmp_path, monkeypatch):
    clip = _clip(tmp_path / "c.mp4")
    digest = hashlib.sha256(clip.read_bytes()).hexdigest()
    assert HF.finish_hook(clip, {"template": "plain", "segments": [{}]}) is False
    monkeypatch.setenv("HOOK_FINISH", "off")
    assert HF.finish_hook(clip, {"segments": [{}]}) is False
    monkeypatch.delenv("HOOK_FINISH")
    short = _clip(tmp_path / "s.mp4", seconds=4)
    assert HF.finish_hook(short, {"segments": [{}]}) is False
    assert hashlib.sha256(clip.read_bytes()).hexdigest() == digest


def test_punch_filter_expression():
    f = HF.punch_filter(1080, 1920, 2.6, 1.2, 1.08)
    assert "between(t,2.60,3.80)" in f and "eval=frame" in f and f.endswith("crop=1080:1920:(iw-1080)/2:(ih-1920)/2")
