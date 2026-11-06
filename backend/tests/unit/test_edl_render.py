"""Regression coverage for auto-director's FFmpeg EDL renderer."""

from pathlib import Path
from types import SimpleNamespace
import sys

from src.media import edl_render


def _encoder(*_args, **_kwargs):
    return "libx264", ["-c:v", "libx264"]


def test_podcast_edl_uses_hard_cut_and_punch_in(monkeypatch):
    monkeypatch.setattr(edl_render, "get_video_encoder", _encoder)
    edl = {
        "duration": 15,
        "segments": [
            {"start": 0, "end": 7, "transition_in": "none", "transition_duration": 0},
            {"start": 7, "end": 15, "transition_in": "cut", "transition_duration": 0},
        ],
        "zoom_keyframes": [{"type": "punch_in", "time": 8, "scale": 1.25, "duration": 0.5}],
        "overlays": [{"type": "info_card", "time": 2, "duration": 3, "text": "42 percent growth"}],
    }

    _, graph, _, _ = edl_render.build_edl_filter_graph(edl, Path("source.mp4"))

    assert "concat=n=2:v=1:a=0" in graph
    assert "xfade=" not in graph
    assert "zoompan" in graph
    assert "if(lt(on,30),1,if(lt(on,45),1.250000,1))" in graph
    assert "drawtext=text='42 percent growth'" in graph
    assert "between(t\\,2.000\\,5.000)" in graph


def test_travel_edl_uses_default_xfade_and_ken_burns(monkeypatch):
    monkeypatch.setattr(edl_render, "get_video_encoder", _encoder)
    edl = {
        "duration": 15,
        "segments": [
            {"start": 0, "end": 7.5, "transition_in": "none", "transition_duration": 0},
            {"start": 7.5, "end": 15, "transition_in": "cross_dissolve", "transition_duration": 0.8},
        ],
        "zoom_keyframes": [{"type": "ken_burns", "start": 0, "end": 15, "scale_start": 1.0, "scale_end": 1.05}],
        "overlays": [],
    }

    _, graph, _, _ = edl_render.build_edl_filter_graph(edl, Path("source.mp4"))

    assert "xfade=transition=fade:duration=0.800000:offset=6.700000" in graph
    assert graph.count("zoompan") == 2
    assert "1.000000+(1.050000-1.000000)" in graph


def test_reference_image_pip_is_bottom_right_and_capped_at_three_seconds(monkeypatch, tmp_path):
    monkeypatch.setattr(edl_render, "get_video_encoder", _encoder)
    image = tmp_path / "reference.png"
    image.touch()
    edl = {
        "duration": 5,
        "segments": [{"start": 0, "end": 5}],
        "overlays": [{"type": "reference_image", "local_path": str(image), "time": 1, "duration": 10}],
    }

    inputs, graph, _, _ = edl_render.build_edl_filter_graph(edl, Path("source.mp4"))

    assert inputs == ["-loop", "1", "-i", str(image)]
    assert "overlay=x=W-w-20:y=H-h-20" in graph
    assert "between(t\\,1.000\\,4.000)" in graph


def test_no_edl_uses_existing_reframe_renderer(monkeypatch, tmp_path):
    """The non-EDL route remains the existing standard single-filter render."""
    # The production source-limit helper is POSIX-only; the tested path never
    # calls it, so a minimal import shim keeps this unit test portable.
    monkeypatch.setitem(sys.modules, "fcntl", SimpleNamespace())
    from src.media import reframing

    standard_calls = []
    monkeypatch.setattr(reframing, "ffprobe_video_size", lambda _path: (1920, 1080))
    monkeypatch.setattr(reframing, "ffprobe_has_audio", lambda _path: True)
    monkeypatch.setattr(reframing, "build_audio_output_args", lambda _audio: [])
    monkeypatch.setattr(reframing, "build_final_video_encode_args", lambda **_kwargs: ["-c:v", "libx264"])
    monkeypatch.setattr(reframing, "run_ffmpeg_command", lambda command: standard_calls.append(command) or SimpleNamespace(returncode=0))
    monkeypatch.setattr(reframing, "render_edl_clip_ffmpeg", lambda *_args: (_ for _ in ()).throw(AssertionError("EDL renderer called")))

    ok, width, height = reframing.render_reframed_clip_ffmpeg(
        tmp_path / "input.mp4", tmp_path / "output.mp4", "vertical", vertical_plan=("scale=1080:1920", "vf"), edl=None,
    )

    assert (ok, width, height) == (True, 1080, 1920)
    assert len(standard_calls) == 1
    assert "-vf" in standard_calls[0]
