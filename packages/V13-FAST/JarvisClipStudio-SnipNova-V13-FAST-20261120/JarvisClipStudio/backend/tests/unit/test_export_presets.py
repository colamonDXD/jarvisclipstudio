"""Export presets must be three different encodes (audit defect D4).

TikTok publishes no organic-upload bitrate target, so its 10 Mbps stays.
Reels keeps the 12 Mbps the app already used. Shorts follows YouTube's
published upload-encoding table for 1080p SDR: 8 Mbps at standard frame
rates (24/25/30), 12 Mbps at high frame rates (48/50/60), and AAC stereo
at 384 kbps. Before this fix TikTok and Shorts produced byte-identical
files on every surface.
"""
import hashlib
import shutil
import subprocess

import pytest

from src import clip_editor
from src.clip_editor import EXPORT_PRESETS, export_with_preset, preset_rates
from src.editor_document import EditDocument, render_document


def test_preset_rates_match_the_platform_tables():
    assert preset_rates("tiktok", 30) == ("10M", "192k")
    assert preset_rates("tiktok", 60) == ("10M", "192k")
    assert preset_rates("reels", 30) == ("12M", "192k")
    assert preset_rates("reels", 60) == ("12M", "192k")
    # YouTube's table: standard frame rate vs high frame rate.
    assert preset_rates("shorts", 24) == ("8M", "384k")
    assert preset_rates("shorts", 30) == ("8M", "384k")
    assert preset_rates("shorts", 60) == ("12M", "384k")
    # Unknown frame rate falls back to the standard-frame-rate row.
    assert preset_rates("shorts", None) == ("8M", "384k")


VERTICAL_PRESETS = ("tiktok", "reels", "shorts")


def test_the_three_presets_encode_differently_at_30_fps():
    # Defect D4 was about the three 9:16 presets. youtube_landscape and
    # square (added later) deliberately reuse YouTube's 1080p row, so they
    # are not part of the "all different" set.
    assert set(VERTICAL_PRESETS) <= set(EXPORT_PRESETS)
    rates = {preset_rates(name, 30) for name in VERTICAL_PRESETS}
    assert len(rates) == len(VERTICAL_PRESETS) == 3


def _export_command(monkeypatch, tmp_path, fps, preset):
    source = tmp_path / "clip.mp4"
    source.write_bytes(b"clip")
    commands = []
    geometry = type("Geometry", (), {"fps": fps})()
    monkeypatch.setattr(clip_editor, "probe_video_geometry", lambda _path: geometry)
    monkeypatch.setattr(clip_editor, "_run", lambda command: commands.append(command))
    export_with_preset(source, tmp_path, preset)
    return commands[0]


def _arg(command, flag):
    return command[command.index(flag) + 1]


def test_shorts_export_uses_youtube_rates_at_standard_frame_rate(monkeypatch, tmp_path):
    command = _export_command(monkeypatch, tmp_path, 30, "shorts")
    assert _arg(command, "-maxrate") == "8M"
    assert _arg(command, "-bufsize") == "16M"
    assert _arg(command, "-b:a") == "384k"


def test_shorts_export_uses_youtube_rates_at_high_frame_rate(monkeypatch, tmp_path):
    command = _export_command(monkeypatch, tmp_path, 60, "shorts")
    assert _arg(command, "-maxrate") == "12M"
    assert _arg(command, "-bufsize") == "24M"
    assert _arg(command, "-b:a") == "384k"


def test_shorts_export_above_60_fps_is_capped_then_uses_the_high_row(monkeypatch, tmp_path):
    command = _export_command(monkeypatch, tmp_path, 120, "shorts")
    video_filter = _arg(command, "-vf")
    assert video_filter.startswith("fps=60,")
    assert _arg(command, "-maxrate") == "12M"


def test_tiktok_and_reels_exports_keep_their_rates(monkeypatch, tmp_path):
    for fps in (30, 60):
        command = _export_command(monkeypatch, tmp_path, fps, "tiktok")
        assert _arg(command, "-maxrate") == "10M"
        assert _arg(command, "-b:a") == "192k"
        command = _export_command(monkeypatch, tmp_path, fps, "reels")
        assert _arg(command, "-maxrate") == "12M"
        assert _arg(command, "-b:a") == "192k"


def _render_command(monkeypatch, tmp_path, preset):
    commands = []

    def fake_run_render(command, directory, job_id, duration):
        commands.append(command)
        (directory / f".export-{job_id}.mp4").write_bytes(b"rendered")

    monkeypatch.setattr("src.editor_document.run_render", fake_run_render)
    document = EditDocument.model_validate(
        {
            "segments": [{"id": "s0", "start": 0, "end": 2}],
            "framing": {"aspect": "original"},
        }
    )
    render_document(tmp_path, "presetprobe", document, preset)
    return commands[0]


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="ffmpeg required")
def test_editor_render_uses_the_same_preset_table(monkeypatch, tmp_path):
    subprocess.run(
        [
            "ffmpeg", "-v", "error", "-y",
            "-f", "lavfi", "-i", "color=c=red:s=320x180:r=30:d=2",
            "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000", "-t", "2",
            "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac",
            str(tmp_path / "clean.mp4"),
        ],
        check=True,
    )
    tiktok = _render_command(monkeypatch, tmp_path, "tiktok")
    shorts = _render_command(monkeypatch, tmp_path, "shorts")
    reels = _render_command(monkeypatch, tmp_path, "reels")
    # The editor render is always 30 fps, so Shorts uses the standard row.
    assert _arg(shorts, "-maxrate") == "8M"
    assert _arg(shorts, "-bufsize") == "16M"
    assert _arg(shorts, "-b:a") == "384k"
    assert _arg(tiktok, "-maxrate") == "10M"
    assert _arg(tiktok, "-bufsize") == "20M"
    assert _arg(tiktok, "-b:a") == "192k"
    assert _arg(reels, "-maxrate") == "12M"
    assert _arg(reels, "-bufsize") == "24M"
    assert len({tuple(tiktok), tuple(shorts), tuple(reels)}) == 3


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="ffmpeg required")
def test_tiktok_and_shorts_exports_are_different_files(tmp_path):
    source = tmp_path / "source.mp4"
    # Heavy temporal noise: at CRF 18 the encoder wants far more than
    # either cap, so the presets' maxrate limits are what decide the
    # output (with clean synthetic content both files would be CRF-sized
    # and could come out the same size).
    subprocess.run(
        [
            "ffmpeg", "-v", "error", "-y", "-f", "lavfi",
            "-i", "testsrc2=size=1080x1920:rate=30:duration=3",
            "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000", "-t", "3",
            "-vf", "noise=alls=30:allf=t",
            "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac",
            str(source),
        ],
        check=True,
    )
    tiktok = export_with_preset(source, tmp_path / "tiktok", "tiktok")
    shorts = export_with_preset(source, tmp_path / "shorts", "shorts")
    sha = lambda path: hashlib.sha256(path.read_bytes()).hexdigest()
    assert sha(tiktok) != sha(shorts)
    # The 8 Mbps cap also makes the Shorts file smaller for the same clip.
    assert shorts.stat().st_size < tiktok.stat().st_size
