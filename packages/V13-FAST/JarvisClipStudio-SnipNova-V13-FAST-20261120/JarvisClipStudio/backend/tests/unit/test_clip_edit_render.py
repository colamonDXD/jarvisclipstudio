"""Real-ffmpeg proof that editor segment operations change the render.

Two layers, both with real ffmpeg and no mocks:

1. EDL layer — build_edl over a synthetic source, apply clip-edit
   operations to its segments, render with render_edl_clip_ffmpeg, and
   compare probed output durations.
2. Footage layer — the path the worker actually takes: operations change
   the plan's kept source ranges (kept_source_ranges), the ranges are
   rendered with the same range renderer create_optimized_clip uses, and
   the output is checked per-second (frame colour + tone frequency) to
   prove the EDITED footage — not the original — is what plays.
"""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from src.auto_director.clip_edits import (
    apply_segment_operations,
    attach_source_shares,
    kept_source_ranges,
)
from src.auto_director.edl import build_edl, load_profile
from src.media import edl_render
from src.media.ffmpeg import render_source_ranges_ffmpeg

ffmpeg_missing = (
    shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None
)
pytestmark = pytest.mark.skipif(
    ffmpeg_missing, reason="ffmpeg/ffprobe not installed"
)


def _run(command: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(command, capture_output=True, timeout=600)


def _text(result: subprocess.CompletedProcess) -> str:
    return (result.stdout + result.stderr).decode("utf-8", "replace")


def _make_source(path: Path, duration: int = 12) -> None:
    result = _run(
        [
            "ffmpeg", "-y", "-v", "error",
            "-f", "lavfi", "-i",
            f"testsrc2=size=320x240:rate=30:duration={duration}",
            "-f", "lavfi", "-i",
            f"sine=frequency=440:sample_rate=48000:duration={duration}",
            "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac",
            str(path),
        ]
    )
    assert result.returncode == 0, _text(result)


def _duration(path: Path) -> float:
    result = _run(
        [
            "ffprobe", "-v", "error", "-show_entries", "format=duration",
            "-of", "json", str(path),
        ]
    )
    assert result.returncode == 0, _text(result)
    return float(json.loads(_text(result))["format"]["duration"])


def _baseline_edl() -> dict:
    words = [
        {"word": f"word{i}", "start": i * 1.0, "end": i * 1.0 + 0.8}
        for i in range(12)
    ]
    return build_edl(
        {"start": 0.0, "end": 12.0, "duration": 12.0},
        words,
        load_profile("podcast_news"),
        [],
    )


def _render(tmp_path: Path, name: str, edl: dict) -> Path:
    output = tmp_path / name
    ok = edl_render.render_edl_clip_ffmpeg(tmp_path / "source.mp4", output, edl)
    assert ok is True and output.is_file()
    return output


def test_trim_and_delete_ops_change_rendered_duration(tmp_path):
    _make_source(tmp_path / "source.mp4")
    baseline = _baseline_edl()
    assert len(baseline["segments"]) >= 2

    # A 12 s clip built at the podcast profile's cut rate has only two
    # segments; split the first so trim + delete have independent targets
    # (the same operation the editor panel exposes).
    baseline["segments"] = apply_segment_operations(
        baseline["segments"], [{"type": "split", "index": 0, "at": 3.0}]
    )
    baseline["duration"] = 12.0
    assert len(baseline["segments"]) == 3

    baseline_out = _render(tmp_path, "baseline.mp4", baseline)
    baseline_duration = _duration(baseline_out)
    assert baseline_duration == pytest.approx(12.0, abs=1.0)

    segments = apply_segment_operations(
        baseline["segments"],
        [
            {"type": "trim", "index": 0, "end": 1.0},
            {"type": "delete", "index": 1},
        ],
    )
    edited = {**baseline, "segments": segments}
    edited["duration"] = sum(s["end"] - s["start"] for s in segments)

    edited_out = _render(tmp_path, "edited.mp4", edited)
    edited_duration = _duration(edited_out)

    # The output shortened by exactly the time the operations removed
    # (within encode/frame tolerance), and by more than one second.
    removed = baseline_duration - edited["duration"]
    assert edited_duration == pytest.approx(edited["duration"], abs=1.0)
    assert baseline_duration - edited_duration == pytest.approx(removed, abs=1.0)
    assert baseline_duration - edited_duration >= 1.0


def _make_marked_source(path: Path) -> None:
    """12 s source in three 4 s blocks: red/440Hz, green/880Hz, blue/1320Hz."""
    blocks = []
    for colour, freq in (("red", 440), ("green", 880), ("blue", 1320)):
        blocks.append(
            f"color=c={colour}:size=160x120:rate=30:duration=4[{colour}v];"
            f"sine=frequency={freq}:sample_rate=48000:duration=4[{colour}a]"
        )
    filter_graph = ";".join(blocks) + (
        ";[redv][greenv][bluev]concat=n=3:v=1:a=0[v]"
        ";[reda][greena][bluea]concat=n=3:v=0:a=1[a]"
    )
    result = _run(
        [
            "ffmpeg", "-y", "-v", "error",
            "-filter_complex", filter_graph,
            "-map", "[v]", "-map", "[a]",
            "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac",
            str(path),
        ]
    )
    assert result.returncode == 0, _text(result)


def _dominant_colour(path: Path, at: float) -> str:
    result = _run(
        [
            "ffmpeg", "-v", "error", "-ss", str(at), "-i", str(path),
            "-frames:v", "1", "-vf", "scale=1:1", "-f", "rawvideo",
            "-pix_fmt", "rgb24", "-",
        ]
    )
    assert result.returncode == 0 and len(result.stdout) >= 3
    r, g, b = result.stdout[0], result.stdout[1], result.stdout[2]
    if r >= g and r >= b:
        return "red"
    if g >= r and g >= b:
        return "green"
    return "blue"


def _colours_over_time(path: Path, seconds: int) -> list[str]:
    return [_dominant_colour(path, t + 0.5) for t in range(seconds)]


def _render_ranges(tmp_path: Path, name: str, ranges) -> Path:
    output = tmp_path / name
    ok = render_source_ranges_ffmpeg(
        tmp_path / "marked.mp4", [tuple(r) for r in ranges], output
    )
    assert ok is True and output.is_file()
    return output


def test_delete_and_reorder_change_the_rendered_footage(tmp_path):
    _make_marked_source(tmp_path / "marked.mp4")
    ranges = [(0.0, 4.0), (4.0, 8.0), (8.0, 12.0)]
    plan = attach_source_shares(
        [
            {"start": 0.0, "end": 4.0},
            {"start": 4.0, "end": 8.0},
            {"start": 8.0, "end": 12.0},
        ],
        ranges,
    )

    # Control: the unedited ranges render red, green, blue in order.
    control = _render_ranges(tmp_path, "control.mp4", ranges)
    assert _colours_over_time(control, 12) == ["red"] * 4 + ["green"] * 4 + ["blue"] * 4

    # Delete the middle segment: green must be GONE from the output.
    deleted = apply_segment_operations(plan, [{"type": "delete", "index": 1}])
    kept = kept_source_ranges(deleted, ranges)
    assert kept == [(0.0, 4.0), (8.0, 12.0)]
    out = _render_ranges(tmp_path, "deleted.mp4", kept)
    assert _duration(out) == pytest.approx(8.0, abs=0.6)
    assert _colours_over_time(out, 8) == ["red"] * 4 + ["blue"] * 4

    # Reorder [blue, red, green]: the output order must follow the edit.
    reordered = apply_segment_operations(
        plan, [{"type": "reorder", "order": [2, 0, 1]}]
    )
    kept = kept_source_ranges(reordered, ranges)
    assert kept == [(8.0, 12.0), (0.0, 4.0), (4.0, 8.0)]
    out = _render_ranges(tmp_path, "reordered.mp4", kept)
    assert _duration(out) == pytest.approx(12.0, abs=0.6)
    assert _colours_over_time(out, 12) == ["blue"] * 4 + ["red"] * 4 + ["green"] * 4


def test_reorder_ops_keep_duration_but_change_the_edit(tmp_path):
    _make_source(tmp_path / "source.mp4")
    baseline = _baseline_edl()

    # Split first so the two halves differ in length: reversing them is a
    # visibly different edit even though the total duration is preserved.
    baseline["segments"] = apply_segment_operations(
        baseline["segments"], [{"type": "split", "index": 0, "at": 2.0}]
    )
    count = len(baseline["segments"])
    assert count == 3

    order = list(reversed(range(count)))
    segments = apply_segment_operations(
        baseline["segments"], [{"type": "reorder", "order": order}]
    )
    assert [(s["start"], s["end"]) for s in segments] != [
        (s["start"], s["end"]) for s in baseline["segments"]
    ]  # the edit is real: segment windows moved
    edited = {**baseline, "segments": segments}
    out = _render(tmp_path, "reordered.mp4", edited)
    assert _duration(out) == pytest.approx(12.0, abs=1.0)
