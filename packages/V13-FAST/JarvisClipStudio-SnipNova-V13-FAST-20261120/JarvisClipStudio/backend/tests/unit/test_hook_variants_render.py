"""Real-ffmpeg proof for hook-variant previews (Lane K).

Each derived variant's EDL — built, plan-consumed and truncated exactly
the way the preview service builds it — renders through
``render_edl_clip_ffmpeg`` (the production EDL renderer, with its seek
prestage) into a real ~6s file, and the three openings are visibly
different files: the cold open starts on different footage and the
punch variant reframes the same start.
"""

import copy
import hashlib
import json
import shutil
import subprocess
from pathlib import Path

import pytest

from src.auto_director import hook_variants as hv
from src.auto_director.clip_edits import attach_source_shares, kept_source_ranges
from src.auto_director.clip_plan import (
    ClipPlan,
    HookDecision,
    OpeningTreatment,
)
from src.auto_director.edl import build_edl, load_profile
from src.clip_source_map import normalize_source_ranges, slice_source_ranges
from src.media import edl_render

ffmpeg_missing = (
    shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None
)
pytestmark = pytest.mark.skipif(
    ffmpeg_missing, reason="ffmpeg/ffprobe not installed"
)

CLIP_RANGES = [(2.0, 18.0)]  # a 16s clip inside the 20s source
HOOK_TS = 4.0  # inside the 6s preview window, late enough to reorder


def _run(command: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(command, capture_output=True, timeout=600)


def _text(result: subprocess.CompletedProcess) -> str:
    return (result.stdout + result.stderr).decode("utf-8", "replace")


def _make_source(path: Path, duration: int = 20) -> None:
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


def _plan() -> ClipPlan:
    return ClipPlan(
        niche="podcast_news",
        source="llm",
        hook=HookDecision(
            type="bold_claim",
            timestamp=HOOK_TS,
            confidence=0.9,
            reason="The strongest claim lands here",
        ),
        opening_treatment=OpeningTreatment(
            punch_zoom="subtle", at_offset=HOOK_TS, reason="the hook moment"
        ),
    )


def _variant_edl(spec: dict, base_edl: dict, plan, profile) -> dict:
    """The service's preview EDL construction, step for step."""
    edl = copy.deepcopy(base_edl)
    edl["segments"] = copy.deepcopy(spec["segments"])
    edl["duration"] = 16.0
    hv.consume_plan_for_variant(edl, spec, plan, profile)
    hv.truncate_edl_for_preview(edl)
    full_ranges = kept_source_ranges(spec["segments"], CLIP_RANGES)
    edl["source_ranges"] = slice_source_ranges(
        normalize_source_ranges(full_ranges), 0.0, hv.PREVIEW_SECONDS
    )
    return edl


def test_hook_variant_previews_render_as_distinct_short_files(tmp_path):
    source = tmp_path / "source.mp4"
    _make_source(source)
    profile = load_profile("podcast_news")
    plan = _plan()
    words = [
        {"word": f"word{i}", "start": i * 1.0, "end": i * 1.0 + 0.8}
        for i in range(16)
    ]
    base_edl = build_edl(
        {"start": 2.0, "end": 18.0, "duration": 16.0}, words, profile, []
    )
    segments = attach_source_shares(
        [
            {
                "start": 0.0,
                "end": 16.0,
                "duration": 16.0,
                "transition_in": "none",
                "transition_duration": 0.0,
            }
        ],
        CLIP_RANGES,
    )
    specs = hv.derive_variants(segments, plan, profile)
    assert [spec["variant"] for spec in specs] == [
        "current",
        "cold_open",
        "punch",
    ]

    hashes = {}
    for spec in specs:
        edl = _variant_edl(spec, base_edl, plan, profile)
        output = tmp_path / f"preview_{spec['variant']}.mp4"
        ok = edl_render.render_edl_clip_ffmpeg(source, output, edl)
        assert ok is True and output.is_file()
        duration = _duration(output)
        assert duration == pytest.approx(hv.PREVIEW_SECONDS, abs=1.0)
        hashes[spec["variant"]] = hashlib.sha256(
            output.read_bytes()
        ).hexdigest()

    # Three openings, three different files: the cold open starts on the
    # hook footage and the punch variant reframes the same start.
    assert len(set(hashes.values())) == 3
