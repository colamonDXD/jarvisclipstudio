r"""Tests for backend/src/media/stickers.py

Run::

    Set-Location D:\SnipNova-Dev\antigravity-v12\backend
    $env:NO_PROXY="localhost"
    $env:PYTHONPATH="../tools/phase2/_winshim"
    D:\SnipNova-Dev\opencode\backend\.venv\Scripts\python.exe `
        -m pytest tests/unit/test_stickers.py --no-cov -p no:cacheprovider -q
"""
from __future__ import annotations

import hashlib
import json
import os
import struct
import subprocess
import sys
import zlib
from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# Import the module under test
# ---------------------------------------------------------------------------
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "src"))
from src.media.stickers import (
    choose_stickers,
    sticker_overlay_args,
    stickers_enabled,
)

REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent
ASSETS_DIR = REPO_ROOT / "assets"
STICKERS_DIR = ASSETS_DIR / "stickers"
EMOJI_DIR = STICKERS_DIR / "emoji"
MANIFEST_PATH = STICKERS_DIR / "STICKERS_MANIFEST.json"

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _signals(t: float, signals: list, strength: float = 0.9) -> dict:
    return {"t": t, "signals": signals, "strength": strength}


def _word(text: str, start: float = 0.0, end: float = 0.5) -> dict:
    return {"text": text, "start": start, "end": end}


def _make_png(w: int, h: int, rgba: tuple) -> bytes:
    """Generate a minimal but valid RGBA PNG for testing.

    IHDR chunk is exactly 13 bytes:
      width  (4), height (4), bit_depth (1), color_type (1=RGBA→6),
      compression (1), filter (1), interlace (1)
    """
    def chunk(tag: bytes, data: bytes) -> bytes:
        crc = zlib.crc32(tag + data) & 0xFFFF_FFFF
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", crc)

    # 13-byte IHDR payload
    ihdr_data = struct.pack(">IIBBBBB", w, h, 8, 6, 0, 0, 0)

    r, g, b, a = rgba
    raw_rows = b"".join(bytes([0]) + bytes([r, g, b, a] * w) for _ in range(h))
    compressed = zlib.compress(raw_rows)

    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", ihdr_data)
        + chunk(b"IDAT", compressed)
        + chunk(b"IEND", b"")
    )


# ---------------------------------------------------------------------------
# test_stickers_enabled
# ---------------------------------------------------------------------------

def test_stickers_enabled(monkeypatch):
    monkeypatch.delenv("STICKERS", raising=False)
    assert stickers_enabled() is False
    monkeypatch.setenv("STICKERS", "0")
    assert stickers_enabled() is False
    monkeypatch.setenv("STICKERS", "false")
    assert stickers_enabled() is False
    monkeypatch.setenv("STICKERS", "1")
    assert stickers_enabled() is True
    monkeypatch.setenv("STICKERS", "true")
    assert stickers_enabled() is True
    monkeypatch.setenv("STICKERS", "TRUE")
    assert stickers_enabled() is True


# ---------------------------------------------------------------------------
# test_rules_laugh
# ---------------------------------------------------------------------------

def test_rules_laugh():
    sigs = [_signals(1.0, ["laugh"])]
    result = choose_stickers(sigs, [], 30.0, seed="laugh-test")
    emojis = [e["emoji"] for e in result]
    assert any(em in ("😂", "🤣") for em in emojis), f"Expected laugh emoji, got {emojis}"


# ---------------------------------------------------------------------------
# test_rules_scream
# ---------------------------------------------------------------------------

def test_rules_scream():
    sigs = [_signals(2.0, ["scream"])]
    result = choose_stickers(sigs, [], 30.0, seed="scream-test")
    emojis = [e["emoji"] for e in result]
    assert "😱" in emojis, f"Expected 😱, got {emojis}"


# ---------------------------------------------------------------------------
# test_rules_words_dead
# ---------------------------------------------------------------------------

def test_rules_words_dead():
    for keyword in ("dead", "died", "rip", "fail"):
        result = choose_stickers([], [_word(keyword, 1.0)], 30.0, seed="dead-test")
        emojis = [e["emoji"] for e in result]
        assert "💀" in emojis, f"Expected 💀 for word '{keyword}', got {emojis}"


# ---------------------------------------------------------------------------
# test_rules_words_fire
# ---------------------------------------------------------------------------

def test_rules_words_fire():
    for keyword in ("fire", "insane", "crazy", "lets go"):
        result = choose_stickers([], [_word(keyword, 2.0)], 30.0, seed="fire-test")
        emojis = [e["emoji"] for e in result]
        assert "🔥" in emojis, f"Expected 🔥 for word '{keyword}', got {emojis}"


# ---------------------------------------------------------------------------
# test_rules_words_money
# ---------------------------------------------------------------------------

def test_rules_words_money():
    for keyword in ("money", "cash", "paid"):
        result = choose_stickers([], [_word(keyword, 3.0)], 30.0, seed="money-test")
        emojis = [e["emoji"] for e in result]
        assert "💸" in emojis, f"Expected 💸 for word '{keyword}', got {emojis}"


# ---------------------------------------------------------------------------
# test_caption_band_avoided
# ---------------------------------------------------------------------------

def test_caption_band_avoided():
    """No sticker may have y in [0.40, 0.60]."""
    sigs = [
        _signals(float(i * 5), ["laugh", "scream", "f0", "vocal"], strength=0.8)
        for i in range(20)
    ]
    words = [_word(w, float(i * 2)) for i, w in
             enumerate(["dead", "fire", "money", "cash", "rip"])]
    result = choose_stickers(sigs, words, 500.0, max_per_clip=50, min_gap=0.0, seed="band-test")
    for ev in result:
        y = ev["y"]
        assert not (0.40 <= y <= 0.60), f"Sticker y={y} falls in caption band [0.40, 0.60]"


# ---------------------------------------------------------------------------
# test_max_count
# ---------------------------------------------------------------------------

def test_max_count():
    """Never more than max_per_clip stickers."""
    sigs = [_signals(float(i * 2), ["laugh"]) for i in range(20)]
    result = choose_stickers(sigs, [], 200.0, max_per_clip=3, min_gap=0.0, seed="max-test")
    assert len(result) <= 3, f"Expected ≤3 stickers, got {len(result)}"


# ---------------------------------------------------------------------------
# test_min_gap
# ---------------------------------------------------------------------------

def test_min_gap():
    """All adjacent sticker times must be ≥ min_gap apart."""
    sigs = [_signals(float(i * 0.5), ["laugh"]) for i in range(40)]
    min_gap = 5.0
    result = choose_stickers(sigs, [], 200.0, max_per_clip=10, min_gap=min_gap, seed="gap-test")
    times = sorted(ev["t"] for ev in result)
    for a, b in zip(times, times[1:]):
        assert b - a >= min_gap - 1e-6, f"Gap {b - a:.3f} < {min_gap} between t={a} and t={b}"


# ---------------------------------------------------------------------------
# test_determinism
# ---------------------------------------------------------------------------

def test_determinism():
    """Same seed → same output."""
    sigs = [_signals(float(i * 3), ["laugh", "scream", "f0"]) for i in range(10)]
    words = [_word("fire", float(i * 5)) for i in range(5)]
    r1 = choose_stickers(sigs, words, 60.0, seed="deter-seed-42")
    r2 = choose_stickers(sigs, words, 60.0, seed="deter-seed-42")
    assert r1 == r2, "choose_stickers is not deterministic for the same seed"
    r3 = choose_stickers(sigs, words, 60.0, seed="deter-seed-99")
    assert isinstance(r3, list)


# ---------------------------------------------------------------------------
# test_ffmpeg_graph_renders
# ---------------------------------------------------------------------------

def test_ffmpeg_graph_renders(tmp_path):
    """
    Create a 3-second black clip, apply 2 stickers, assert:
      - output file exists and is non-empty
      - frames at sticker times differ from pure black (sticker pixels present)
    """
    sticker1 = tmp_path / "sticker1.png"
    sticker2 = tmp_path / "sticker2.png"
    sticker1.write_bytes(_make_png(100, 100, (255, 0, 0, 255)))   # solid red
    sticker2.write_bytes(_make_png(100, 100, (0, 255, 0, 255)))   # solid green

    # Create 3-second black video at 1080x1920
    black_clip = tmp_path / "black.mp4"
    ret = subprocess.run(
        [
            "ffmpeg", "-y",
            "-f", "lavfi", "-i", "color=black:size=1080x1920:rate=30",
            "-t", "3",
            "-c:v", "libx264", "-pix_fmt", "yuv420p",
            str(black_clip),
        ],
        capture_output=True,
        text=True,
    )
    assert ret.returncode == 0, f"ffmpeg black clip failed:\n{ret.stderr[-500:]}"

    # Build sticker events
    events = [
        {
            "t": 0.5,
            "emoji": "😂",
            "file": str(sticker1),
            "x": 0.5,
            "y": 0.1,       # top zone, outside caption band
            "scale": 0.1,   # 10% of 1080 = 108 px
            "dur": 1.0,
        },
        {
            "t": 2.0,
            "emoji": "🔥",
            "file": str(sticker2),
            "x": 0.5,
            "y": 0.8,       # bottom zone
            "scale": 0.1,
            "dur": 0.8,
        },
    ]

    inputs, fc, out_label = sticker_overlay_args(events, width=1080, height=1920)

    out_file = tmp_path / "output.mp4"
    cmd = ["ffmpeg", "-y", "-i", str(black_clip)]
    for inp in inputs:
        cmd += ["-i", inp]
    cmd += [
        "-filter_complex", fc,
        "-map", out_label,
        "-t", "3",
        "-c:v", "libx264", "-pix_fmt", "yuv420p",
        str(out_file),
    ]

    ret = subprocess.run(cmd, capture_output=True, text=True)
    assert ret.returncode == 0, f"ffmpeg sticker overlay failed:\n{ret.stderr[-1000:]}"
    assert out_file.exists() and out_file.stat().st_size > 0, "Output video not created"

    # Sample frames and confirm sticker pixels are present
    import numpy as np
    import cv2

    cap_out = cv2.VideoCapture(str(out_file))
    cap_black = cv2.VideoCapture(str(black_clip))

    def _get_frame_at(cap, t_sec: float):
        cap.set(cv2.CAP_PROP_POS_MSEC, t_sec * 1000)
        ok, frame = cap.read()
        return frame if ok else None

    frame_out_1 = _get_frame_at(cap_out, 0.7)
    frame_black_1 = _get_frame_at(cap_black, 0.7)
    frame_out_2 = _get_frame_at(cap_out, 2.1)
    frame_black_2 = _get_frame_at(cap_black, 2.1)

    cap_out.release()
    cap_black.release()

    assert frame_out_1 is not None, "Could not read output frame at t=0.7"
    assert frame_black_1 is not None, "Could not read black frame at t=0.7"
    assert frame_out_2 is not None, "Could not read output frame at t=2.1"
    assert frame_black_2 is not None, "Could not read black frame at t=2.1"

    diff1 = np.abs(frame_out_1.astype(int) - frame_black_1.astype(int)).max()
    diff2 = np.abs(frame_out_2.astype(int) - frame_black_2.astype(int)).max()

    assert diff1 > 0, "Frame at t=0.7 is identical to black (sticker 1 not rendered)"
    assert diff2 > 0, "Frame at t=2.1 is identical to black (sticker 2 not rendered)"


# ---------------------------------------------------------------------------
# test_manifest_complete
# ---------------------------------------------------------------------------

def test_manifest_complete():
    """Every PNG in STICKERS_MANIFEST.json exists, sha256 matches, license=Apache-2.0."""
    if not MANIFEST_PATH.exists():
        pytest.skip("STICKERS_MANIFEST.json not found – run tools/assets/fetch_noto_stickers.py first")

    manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    assert len(manifest) > 0, "Manifest is empty"

    for entry in manifest:
        assert entry.get("license") == "Apache-2.0", \
            f"Wrong license for {entry.get('file')}: {entry.get('license')}"

        rel_file = entry["file"]
        abs_path = REPO_ROOT / rel_file
        if not abs_path.exists():
            abs_path = Path(rel_file)
        assert abs_path.exists(), f"PNG missing on disk: {rel_file}"

        data = abs_path.read_bytes()
        actual_sha = hashlib.sha256(data).hexdigest()
        assert actual_sha == entry["sha256"], \
            f"sha256 mismatch for {rel_file}: expected {entry['sha256']}, got {actual_sha}"
