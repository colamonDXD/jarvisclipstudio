"""Wide and square export profiles keep the whole vertical picture.

The three original presets are 9:16 only. ``youtube_landscape``
(1920x1080) and ``square`` (1080x1080) export a 9:16 clip into a wider
frame by fitting the entire picture inside it -- never stretching or
cropping it -- and filling the remaining bands with a blurred copy of
the same frame. Captions burned into the source therefore stay inside
the frame with the rest of the picture.

YouTube publishes one 1080p SDR row: 8 Mbps at standard frame rates
(24/25/30), 12 Mbps above, AAC stereo at 384 kbps. Square is a
1080-class frame, so it uses the same row. The existing three presets
keep their geometry, rates and black letterboxing unchanged.
"""

import json
import shutil
import subprocess

import pytest

from src import clip_editor
from src.clip_editor import EXPORT_PRESETS, export_with_preset, preset_rates


def _export_command(monkeypatch, tmp_path, preset, fps=30):
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


def test_the_five_profiles_have_the_expected_frames():
    expected = {
        "tiktok": (1080, 1920),
        "reels": (1080, 1920),
        "shorts": (1080, 1920),
        "youtube_landscape": (1920, 1080),
        "square": (1080, 1080),
    }
    assert {name: (p.width, p.height) for name, p in EXPORT_PRESETS.items()} == expected


def test_youtube_landscape_follows_the_1080p_sdr_row():
    assert preset_rates("youtube_landscape", 24) == ("8M", "384k")
    assert preset_rates("youtube_landscape", 30) == ("8M", "384k")
    assert preset_rates("youtube_landscape", 60) == ("12M", "384k")
    assert preset_rates("youtube_landscape", None) == ("8M", "384k")


def test_square_uses_the_1080_class_row():
    assert preset_rates("square", 30) == ("8M", "384k")
    assert preset_rates("square", 60) == ("12M", "384k")


def test_the_original_presets_are_untouched():
    assert preset_rates("tiktok", 30) == ("10M", "192k")
    assert preset_rates("reels", 30) == ("12M", "192k")
    assert preset_rates("shorts", 30) == ("8M", "384k")
    assert preset_rates("shorts", 60) == ("12M", "384k")
    for name in ("tiktok", "reels", "shorts"):
        assert EXPORT_PRESETS[name].background == "pad"


@pytest.mark.parametrize("preset,width,height", [
    ("youtube_landscape", 1920, 1080),
    ("square", 1080, 1080),
])
def test_wide_profiles_fit_the_whole_picture_inside_a_blurred_fill(
    monkeypatch, tmp_path, preset, width, height
):
    command = _export_command(monkeypatch, tmp_path, preset)
    assert "-vf" not in command
    assert _arg(command, "-map") == "[outv]"
    graph = _arg(command, "-filter_complex")
    # The foreground is scaled down to fit and centred: no increase
    # (crop) and no pad (bars) is applied to the picture itself.
    assert (
        f"[fg]scale={width}:{height}:force_original_aspect_ratio=decrease"
        in graph
    )
    assert "[blurred][fit]overlay=(W-w)/2:(H-h)/2" in graph
    assert f"[bg]scale={width}:{height}:force_original_aspect_ratio=increase" in graph
    assert "crop=" in graph and "boxblur=" in graph
    # Audio is carried across; the video comes from the graph.
    assert command[command.index("-map", command.index("-map") + 1) + 1] == "0:a?"


def test_wide_profile_above_60_fps_is_capped_and_uses_the_high_row(monkeypatch, tmp_path):
    command = _export_command(monkeypatch, tmp_path, "youtube_landscape", fps=120)
    assert _arg(command, "-maxrate") == "12M"
    assert _arg(command, "-bufsize") == "24M"
    assert _arg(command, "-filter_complex").startswith("fps=60,")


def test_legacy_profiles_still_letterbox_on_black(monkeypatch, tmp_path):
    command = _export_command(monkeypatch, tmp_path, "tiktok")
    assert "-filter_complex" not in command
    video_filter = _arg(command, "-vf")
    assert "force_original_aspect_ratio=decrease" in video_filter
    assert "pad=1080:1920" in video_filter


@pytest.mark.skipif(
    not shutil.which("ffmpeg") or not shutil.which("ffprobe"),
    reason="needs real ffmpeg/ffprobe",
)
def test_all_five_profiles_export_a_real_9x16_clip(tmp_path):
    source = tmp_path / "source.mp4"
    subprocess.run(
        [
            "ffmpeg", "-v", "error", "-y",
            "-f", "lavfi", "-i", "color=c=0xB03A2E:s=540x960:r=30:d=1",
            "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000:d=1",
            "-t", "1", "-c:v", "libx264", "-preset", "veryfast",
            "-pix_fmt", "yuv420p", "-c:a", "aac", str(source),
        ],
        check=True,
    )
    expected = {
        "tiktok": (1080, 1920),
        "reels": (1080, 1920),
        "shorts": (1080, 1920),
        "youtube_landscape": (1920, 1080),
        "square": (1080, 1080),
    }
    for preset, size in expected.items():
        output = export_with_preset(source, tmp_path / preset, preset)
        probe = subprocess.run(
            [
                "ffprobe", "-v", "error",
                "-show_entries", "stream=codec_type,width,height",
                "-show_entries", "format=duration",
                "-of", "json", str(output),
            ],
            check=True, capture_output=True, text=True,
        )
        payload = json.loads(probe.stdout)
        video = next(s for s in payload["streams"] if s["codec_type"] == "video")
        assert (video["width"], video["height"]) == size
        assert abs(float(payload["format"]["duration"]) - 1.0) <= 0.15
        assert any(s["codec_type"] == "audio" for s in payload["streams"])

    # The wide output's side bands are the blurred source, not black bars.
    landscape = next((tmp_path / "youtube_landscape").glob("*.mp4"))
    frame = subprocess.run(
        [
            "ffmpeg", "-v", "error", "-ss", "0.5", "-i", str(landscape),
            "-frames:v", "1", "-vf", "crop=80:80:4:4,format=gray",
            "-f", "rawvideo", "-",
        ],
        check=True, capture_output=True,
    ).stdout
    assert frame and sum(frame) / len(frame) > 10
