"""F-P3: per-clip / per-pass render stage timings.

Before this change the pipeline recorded only task-level stage totals
(download / transcription / analysis / one aggregate render_seconds), so
a slow clip could not be decomposed: the internal split of a single
render — source pre-stage stitch, caption build, filter-graph encode,
bumper stitch — was unmeasured (PERF_SANITY.md, finding F-P3).

The contract pinned here:

- ``render_edl_clip_ffmpeg`` fills a caller-supplied timings dict with
  REAL measured wall-clock for the passes that actually ran:
  ``prestage_seconds`` only on the multi-range crossfade path,
  ``broll_resolve_seconds``, ``encode_seconds``, ``bumpers_seconds``
  only when bumpers are configured, and ``total_seconds``.
- ``create_optimized_clip`` stamps the per-clip dict (its own outer
  passes — ``source_prestage_seconds`` / ``captions_seconds`` /
  ``encode_seconds`` / ``total_seconds`` — plus whatever the EDL
  renderer recorded into the same dict) into
  ``layout_report["render_timings"]``.
- task_service folds each clip's dict into the task's existing
  ``stage_timings_json`` under a ``"clips"`` list, so the timings
  persist with the task and survive a JSON round-trip. Publishing
  analytics already skips non-numeric stage values, so the list cannot
  break it.
- Timing is observation only: a timings dict that misbehaves must never
  fail a render, and ``timings=None`` keeps the old behaviour exactly.
"""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from src import video_utils
from src.media import edl_render
from src.media.common import record_pass_timing
from src.media.ffmpeg import ffprobe_duration, rendered_ranges_duration
from src.services.task_service import _collect_clip_render_timings

ffmpeg_missing = shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None
pytestmark = pytest.mark.skipif(ffmpeg_missing, reason="ffmpeg/ffprobe not installed")


def _run(command):
    result = subprocess.run(command, capture_output=True, text=True, timeout=600)
    assert result.returncode == 0, result.stderr
    return result


def _make_source(path: Path) -> None:
    """30 s small source with audio (flat colour + tone, cheap to make)."""
    _run([
        "ffmpeg", "-y", "-v", "error",
        "-f", "lavfi", "-i", "color=c=0x404040:size=320x568:rate=30:duration=30",
        "-f", "lavfi", "-i", "sine=frequency=700:sample_rate=48000:duration=30",
        "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-shortest",
        str(path),
    ])


def _make_bumper(path: Path) -> None:
    """1 s red bumper with audio."""
    _run([
        "ffmpeg", "-y", "-v", "error",
        "-f", "lavfi", "-i", "color=c=0xFF0000:size=320x568:rate=30:duration=1",
        "-f", "lavfi", "-i", "sine=frequency=300:sample_rate=48000:duration=1",
        "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-shortest",
        str(path),
    ])


def _segments(total: float, n: int, transition=None, tdur: float = 0.0):
    step = total / n
    return [
        {
            "index": i,
            "start": round(i * step, 3),
            "end": round(min((i + 1) * step, total), 3),
            "transition_in": "none" if i == 0 else (transition or "cut"),
            "transition_duration": 0 if i == 0 else tdur,
        }
        for i in range(n)
    ]


def _edl(duration, ranges, segs):
    return {
        "clip_id": "fp3",
        "duration": duration,
        "source_ranges": [[float(a), float(b)] for a, b in ranges],
        "segments": segs,
    }


@pytest.fixture(scope="module")
def source_path(tmp_path_factory) -> Path:
    path = tmp_path_factory.mktemp("fp3") / "source30.mp4"
    _make_source(path)
    return path


class _ExplodingDict(dict):
    """A timings dict whose writes always fail (timing must never cost a render)."""

    def __setitem__(self, key, value):
        raise RuntimeError("timings store is broken")


# ---------------------------------------------------------------------------
# The shared recorder: defensive by contract
# ---------------------------------------------------------------------------


def test_record_pass_timing_contract():
    import time

    # No dict -> no-op, no raise.
    record_pass_timing(None, "x_seconds", time.perf_counter())

    # A broken store -> swallowed, no raise.
    record_pass_timing(_ExplodingDict(), "x_seconds", time.perf_counter())
    record_pass_timing(["not", "a", "dict"], "x_seconds", time.perf_counter())

    timings: dict = {}
    start = time.perf_counter()
    record_pass_timing(timings, "pass_seconds", start)
    assert timings["pass_seconds"] >= 0.0

    first = timings["pass_seconds"]
    record_pass_timing(timings, "pass_seconds", time.perf_counter(), accumulate=True)
    assert timings["pass_seconds"] >= first


# ---------------------------------------------------------------------------
# EDL renderer: per-pass entries on real renders
# ---------------------------------------------------------------------------


def test_edl_two_range_render_records_prestage(source_path, tmp_path):
    ranges = [(2.0, 7.0), (10.0, 15.0)]
    duration = rendered_ranges_duration(ranges)
    edl = _edl(duration, ranges, _segments(duration, 2, "wipeleft", 0.4))
    output = tmp_path / "tworange.mp4"
    timings: dict = {}

    ok = edl_render.render_edl_clip_ffmpeg(source_path, output, edl, timings=timings)

    assert ok and output.is_file()
    # The multi-range crossfade path materialises a pre-stage stitch first.
    assert timings["prestage_seconds"] > 0.0
    assert timings["encode_seconds"] > 0.0
    assert timings["broll_resolve_seconds"] >= 0.0
    assert timings["total_seconds"] >= timings["prestage_seconds"]
    # No bumpers configured -> the pass never ran -> no entry.
    assert "bumpers_seconds" not in timings


def test_edl_single_range_render_records_no_prestage(source_path, tmp_path):
    ranges = [(2.0, 7.0)]
    duration = rendered_ranges_duration(ranges)
    edl = _edl(duration, ranges, _segments(duration, 1))
    output = tmp_path / "single.mp4"
    timings: dict = {}

    ok = edl_render.render_edl_clip_ffmpeg(source_path, output, edl, timings=timings)

    assert ok and output.is_file()
    assert "prestage_seconds" not in timings
    assert timings["encode_seconds"] > 0.0
    assert timings["total_seconds"] >= timings["encode_seconds"]


def test_edl_bumper_render_records_bumpers(source_path, tmp_path):
    bumper = tmp_path / "intro.mp4"
    _make_bumper(bumper)
    ranges = [(2.0, 7.0)]
    duration = rendered_ranges_duration(ranges)
    edl = _edl(duration, ranges, _segments(duration, 1))
    edl["brand_bumpers"] = {"intro": {"path": str(bumper)}, "outro": {}}
    output = tmp_path / "branded.mp4"
    timings: dict = {}

    ok = edl_render.render_edl_clip_ffmpeg(source_path, output, edl, timings=timings)

    assert ok and output.is_file()
    assert timings["bumpers_seconds"] >= 0.0
    # The 1 s bumper really was stitched: ~5 s clip + ~1 s bumper.
    assert ffprobe_duration(output) == pytest.approx(duration + 1.0, abs=0.6)


def test_edl_render_survives_broken_timings_dict(source_path, tmp_path):
    ranges = [(2.0, 7.0)]
    duration = rendered_ranges_duration(ranges)
    edl = _edl(duration, ranges, _segments(duration, 1))
    output = tmp_path / "brokentimings.mp4"

    ok = edl_render.render_edl_clip_ffmpeg(
        source_path, output, edl, timings=_ExplodingDict()
    )

    assert ok and output.is_file()


# ---------------------------------------------------------------------------
# create_optimized_clip: the report-back seam (layout_report)
# ---------------------------------------------------------------------------


def test_create_optimized_clip_plain_path_reports_timings(source_path, tmp_path):
    output = tmp_path / "plain.mp4"
    layout_report: dict = {}

    ok = video_utils.create_optimized_clip(
        source_path, 2.0, 7.0, output,
        add_subtitles=False,
        output_format="vertical",
        keep_ranges=[(2.0, 7.0)],
        layout_report=layout_report,
    )

    assert ok and output.is_file()
    timings = layout_report["render_timings"]
    # The plain path renders its source intermediate first, then encodes.
    assert timings["source_prestage_seconds"] > 0.0
    assert timings["encode_seconds"] > 0.0
    assert timings["total_seconds"] >= timings["encode_seconds"]
    # No captions were built (subtitles off, no hook title) -> no entry.
    assert "captions_seconds" not in timings


def test_create_optimized_clip_edl_path_reports_timings(source_path, tmp_path):
    ranges = [(2.0, 7.0), (10.0, 15.0)]
    duration = rendered_ranges_duration(ranges)
    edl = _edl(duration, ranges, _segments(duration, 2, "wipeleft", 0.4))
    output = tmp_path / "direct_edl.mp4"
    layout_report: dict = {}

    ok = video_utils.create_optimized_clip(
        source_path, 2.0, 15.0, output,
        add_subtitles=False,
        output_format="vertical",
        keep_ranges=ranges,
        layout_report=layout_report,
        edl=edl,
    )

    assert ok and output.is_file()
    timings = layout_report["render_timings"]
    # Direct-from-source EDL: the renderer's own pre-stage ran (recorded
    # by edl_render into the shared dict); no intermediate was rendered.
    assert timings["prestage_seconds"] > 0.0
    assert "source_prestage_seconds" not in timings
    assert timings["encode_seconds"] > 0.0
    assert timings["total_seconds"] >= timings["encode_seconds"]


# ---------------------------------------------------------------------------
# Persistence: per-clip entries fold into the task's stage_timings_json
# ---------------------------------------------------------------------------


def test_task_collects_clip_timings_and_json_roundtrip():
    stage_timings: dict = {"render_seconds": 42.0}
    clip_info = {
        "filename": "clip_1.mp4",
        "render_timings": {"encode_seconds": 3.25, "total_seconds": 4.5},
    }

    _collect_clip_render_timings(stage_timings, 1, clip_info)

    assert stage_timings["clips"] == [
        {
            "position": 1,
            "filename": "clip_1.mp4",
            "encode_seconds": 3.25,
            "total_seconds": 4.5,
        }
    ]
    # Round-trips through the exact JSON the task row persists.
    parsed = json.loads(json.dumps(stage_timings))
    assert parsed == stage_timings
    # Publishing analytics sums only numeric stage values; the clips list
    # must be the one non-numeric value and every clip value numeric.
    for stage, value in parsed.items():
        assert isinstance(value, (int, float)) or stage == "clips"
    for entry in parsed["clips"]:
        for key, value in entry.items():
            assert isinstance(value, (int, float)) or key in ("position", "filename")


def test_task_collect_skips_clips_without_timings():
    stage_timings: dict = {}
    _collect_clip_render_timings(stage_timings, 1, {"filename": "a.mp4"})
    _collect_clip_render_timings(stage_timings, 2, None)
    _collect_clip_render_timings(
        stage_timings, 3, {"filename": "b.mp4", "render_timings": "junk"}
    )
    _collect_clip_render_timings(
        stage_timings, 4, {"filename": "c.mp4", "render_timings": {}}
    )
    assert "clips" not in stage_timings
