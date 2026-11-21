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
    assert "drawtext=textfile='" in graph
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


def test_drawtext_uses_textfile_sidecar_with_verbatim_text(tmp_path):
    # Inline text='…' cannot carry an apostrophe at all (the graph parser
    # treats the quote as structural however it is escaped), so card text
    # travels in a sidecar file. Every character is literal there except
    # %, which drawtext's expansion pass still processes — it is escaped
    # as \% (renders back as one literal %; doubling does not work in a
    # textfile, verified on ffmpeg 8.1).
    hostile = "50% off: it's a \\path: deal%"
    sidecar_dir = tmp_path / "cards"
    filt = edl_render.build_drawtext_filter(hostile, 0, 3, "[v0]", "[v1]", sidecar_dir=sidecar_dir)
    assert "textfile='" in filt
    assert "text='" not in filt
    files = list(sidecar_dir.glob("card_*.txt"))
    assert len(files) == 1
    assert files[0].read_text(encoding="utf-8") == hostile.replace("%", r"\%")


def test_build_graph_accepts_plain_string_input_path(monkeypatch, tmp_path):
    # Regression: the graph builder derived its drawtext sidecar dir via
    # input_path.parent, so a caller passing a plain string (the declared
    # type says Path, but nothing enforces it) crashed the whole graph
    # build with AttributeError before any filter was produced.
    monkeypatch.setattr(edl_render, "get_video_encoder", _encoder)
    edl = {
        "duration": 10,
        "segments": [{"start": 0, "end": 10}],
        "overlays": [{"type": "info_card", "time": 1, "duration": 3, "text": "42 percent growth"}],
    }

    _, graph, _, _ = edl_render.build_edl_filter_graph(edl, str(tmp_path / "in.mp4"))

    assert "drawtext=textfile='" in graph
    sidecars = list((tmp_path / f".drawtext-{__import__('os').getpid()}").glob("card_*.txt"))
    assert len(sidecars) == 1
    assert sidecars[0].read_text(encoding="utf-8") == "42 percent growth"


def test_build_graph_is_byte_deterministic_across_builds(monkeypatch, tmp_path):
    # Regression: sidecar files used to be named by counting the files
    # already in the sidecar dir, so building the same EDL twice pointed
    # the second graph at card_1.txt where the first said card_0.txt —
    # no two builds of an identical EDL ever produced the same graph.
    # Names are now derived from the card's position in the render plus
    # a digest of its content, so the equality below can genuinely fail.
    monkeypatch.setattr(edl_render, "get_video_encoder", _encoder)
    edl = {
        "duration": 10,
        "segments": [{"start": 0, "end": 10}],
        "overlays": [{"type": "info_card", "time": 1, "duration": 3, "text": "42 percent growth"}],
    }

    _, first, _, _ = edl_render.build_edl_filter_graph(edl, tmp_path / "in.mp4")
    _, second, _, _ = edl_render.build_edl_filter_graph(edl, tmp_path / "in.mp4")
    assert second == first
    # A plain-string input path builds the identical graph too.
    _, from_str, _, _ = edl_render.build_edl_filter_graph(edl, str(tmp_path / "in.mp4"))
    assert from_str == first
    # Repeated builds reuse the one deterministic sidecar, not a growing pile.
    sidecars = list((tmp_path / f".drawtext-{__import__('os').getpid()}").glob("card_*.txt"))
    assert len(sidecars) == 1
    assert sidecars[0].read_text(encoding="utf-8") == "42 percent growth"


def test_short_info_card_text_caps_and_wraps_long_quotes():
    long_text = (
        "The truth is this one simple habit changes absolutely "
        "everything about how your mornings feel every single day"
    )
    wrapped = edl_render.short_info_card_text(long_text)
    lines = wrapped.split("\n")
    assert len(lines) >= 2
    assert all(len(line) <= 34 for line in lines)
    # Verbatim rule: wrapping only breaks lines, it never alters words.
    assert wrapped.replace("\n", " ") == " ".join(long_text.split())


def test_short_info_card_text_hard_truncates_pathological_input():
    huge = "x" * 500
    wrapped = edl_render.short_info_card_text(huge)
    assert all(len(line) <= 34 for line in wrapped.split("\n"))
    assert len(wrapped.replace("\n", "")) <= 34 * 4


def test_info_card_graph_uses_wrapped_text(monkeypatch, tmp_path):
    monkeypatch.setattr(edl_render, "get_video_encoder", _encoder)
    long_text = (
        "The truth is this one simple habit changes absolutely "
        "everything about how your mornings feel"
    )
    edl = {
        "duration": 10,
        "segments": [{"start": 0, "end": 10}],
        "overlays": [{"type": "info_card", "time": 1, "duration": 3, "text": long_text}],
    }

    _, graph, _, _ = edl_render.build_edl_filter_graph(edl, tmp_path / "source.mp4")

    assert "drawtext=textfile='" in graph
    assert long_text not in graph
    sidecars = list((tmp_path / f".drawtext-{__import__('os').getpid()}").glob("card_*.txt"))
    assert len(sidecars) == 1
    card_lines = sidecars[0].read_text(encoding="utf-8").split("\n")
    assert len(card_lines) >= 2
    assert all(len(line) <= 34 for line in card_lines)


def _render_card_clip(tmp_path, card_text: str, source_colour: str = "0x224466"):
    """Render a real 6s clip carrying one info card; return the output path."""
    import subprocess

    source = tmp_path / "source.mp4"
    subprocess.run(
        [
            "ffmpeg", "-y", "-f", "lavfi",
            "-i", f"color=c={source_colour}:size=540x960:r=30:d=6",
            "-f", "lavfi", "-i", "sine=frequency=440:duration=6",
            "-shortest", "-c:v", "libx264", "-pix_fmt", "yuv420p",
            "-c:a", "aac", str(source),
        ],
        check=True, capture_output=True,
    )
    edl = {
        "duration": 6,
        "segments": [{"start": 0, "end": 6}],
        "source_ranges": [[0.0, 6.0]],
        "overlays": [{"type": "info_card", "time": 1, "duration": 3, "text": card_text}],
    }
    output = tmp_path / "output.mp4"
    ok = edl_render.render_edl_clip_ffmpeg(source, output, edl)
    assert ok is True
    assert output.is_file() and output.stat().st_size > 0
    return output


def _card_text_columns(video, at_seconds: float, tmp_path) -> set:
    """Columns of the rendered frame that differ from the flat background.

    The synthetic source is a solid colour, so every non-background pixel
    in the card's band (y around h*0.85, fontsize 48) is card ink or its
    box. Returns the set of differing column indices.
    """
    import subprocess

    import cv2
    import numpy as np

    frame_path = tmp_path / f"frame_{at_seconds}.png"
    subprocess.run(
        ["ffmpeg", "-y", "-v", "error", "-ss", str(at_seconds), "-i", str(video),
         "-frames:v", "1", str(frame_path)],
        check=True, capture_output=True,
    )
    image = cv2.imread(str(frame_path))
    assert image is not None
    height, width = image.shape[:2]
    band = image[int(height * 0.78):int(height * 0.97), :, :]
    background = np.array([0x66, 0x44, 0x22], dtype=np.int16)  # BGR of 0x224466
    diff = np.abs(band.astype(np.int16) - background).max(axis=2) > 24
    return set(np.where(diff.any(axis=0))[0].tolist()), width


def test_info_card_with_hostile_quote_renders_without_drawtext_error(tmp_path):
    """Real render: a long quote full of drawtext-hostile characters must
    produce a clip, not an ffmpeg filter-parse failure."""
    hostile = (
        "It's 100%: the truth is, back\\slash paths and 'quotes' "
        "change everything about this very long morning routine"
    )
    _render_card_clip(tmp_path, hostile)


def test_apostrophe_info_card_is_visible_in_the_rendered_frame(tmp_path):
    """Bug #1 proof: an apostrophe card not only renders (exit 0) — the
    card is actually drawn inside its window and absent outside it."""
    output = _render_card_clip(tmp_path, "It's a test: 100% legit")
    inside, width = _card_text_columns(output, 2.0, tmp_path)
    outside, _ = _card_text_columns(output, 5.5, tmp_path)
    assert len(inside) > 40  # the card's box/text occupies real width
    assert outside == set()  # nothing drawn after the card's window ends
    assert min(inside) >= 8 and max(inside) <= width - 9


def test_long_info_card_text_stays_inside_the_frame(tmp_path):
    """Bug #2 proof: a 138-char card (which pre-fix spanned x=3..1079 of
    1080, clipped at both edges) now wraps — its ink stays inside the
    frame with margin on both sides."""
    long_text = (
        "The truth is this single overlooked habit quietly changes "
        "absolutely everything about how your entire morning routine "
        "feels every day!!!!!"
    )
    assert len(long_text) == 138, len(long_text)
    output = _render_card_clip(tmp_path, long_text)
    inside, width = _card_text_columns(output, 2.0, tmp_path)
    assert len(inside) > 40
    assert min(inside) >= 16, f"card ink starts at x={min(inside)}"
    assert max(inside) <= width - 17, f"card ink ends at x={max(inside)} of {width}"


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


def test_edl_render_seeks_input_to_first_kept_moment(monkeypatch, tmp_path):
    """A clip at minute 10 must not decode the first 10 minutes of source."""
    monkeypatch.setattr(edl_render, "get_video_encoder", _encoder)
    commands = []

    def _capture(command, **_kwargs):
        commands.append(command)
        return SimpleNamespace(returncode=0, stderr="")

    monkeypatch.setattr(edl_render, "run_ffmpeg_command", _capture)
    edl = {
        "duration": 5,
        "segments": [{"start": 0, "end": 5}],
        "source_ranges": [[600.0, 605.0]],
    }

    ok = edl_render.render_edl_clip_ffmpeg(
        tmp_path / "input.mp4", tmp_path / "output.mp4", edl
    )

    assert ok is True
    command = commands[0]
    input_at = command.index("-i")
    assert command[input_at - 4 : input_at] == [
        "-ss",
        "600.000",
        "-t",
        "6.000",
    ]
    graph = command[command.index("-filter_complex") + 1]
    assert "trim=start=0.000:end=5.000" in graph
    assert "trim=start=600.000" not in graph


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
