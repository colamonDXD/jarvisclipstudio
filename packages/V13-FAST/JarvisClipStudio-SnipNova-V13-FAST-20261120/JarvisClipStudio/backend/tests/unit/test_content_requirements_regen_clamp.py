"""Regeneration render clamp (campaign brief enforcement lane).

The settings-regeneration chain — ``regenerate_all_clips_for_task`` ->
``VideoService.create_video_clips`` -> ``create_clips_with_transitions``
-> ``create_clips_from_segments`` -> ``create_optimized_clip`` — used to
carry NO brief ranges at all, while ``create_clips_from_segments`` runs
the sentence-boundary extension unconditionally on the carried
keep_ranges. A brief-clamped clip whose final range ended mid-sentence
just before an excluded range therefore re-exported footage INSIDE the
excluded range on regeneration, and nothing audited it (the main
pipeline's R1/R2 clamps live on the create_single_clip path only).

These tests drive the real ``create_clips_from_segments`` with tiny
renders, mirroring test_content_requirements_render_clamp.py: the
sidecar (render truth) must show 100% in-range footage, and the
no-brief call shape must keep its historical behaviour (extension
still applies — the clamp is inert without ranges).
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from src.clip_source_map import load_clip_source_ranges
from src.video_utils import create_clips_from_segments

ffmpeg_missing = shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None
pytestmark = pytest.mark.skipif(ffmpeg_missing, reason="ffmpeg/ffprobe not installed")


def _make_source(path: Path, seconds: int = 30) -> None:
    result = subprocess.run(
        [
            "ffmpeg", "-v", "error", "-y",
            "-f", "lavfi", "-i", f"testsrc2=size=640x360:rate=10:duration={seconds}",
            "-f", "lavfi", "-i", f"sine=frequency=440:sample_rate=44100:duration={seconds}",
            "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
            "-c:a", "aac", str(path),
        ],
        capture_output=True, text=True, timeout=300,
    )
    assert result.returncode == 0, result.stderr


def _seed_words(path: Path, upto: float = 30.0) -> None:
    words = []
    t = 0.0
    while t < upto:
        text = "word"
        if abs(t - 17.5) < 0.26:
            text = "done."
        words.append(
            {"text": text, "start": int(t * 1000), "end": int((t + 0.5) * 1000),
             "confidence": 1.0, "speaker": None}
        )
        t += 0.5
    path.with_suffix(".transcript_cache.json").write_text(
        json.dumps(
            {
                "words": words,
                "utterances": [],
                "text": " ".join(w["text"] for w in words),
            }
        ),
        encoding="utf-8",
    )


def _segment(start: str, end: str, keep_ranges):
    return {
        "start_time": start,
        "end_time": end,
        "text": "word " * 30,
        "keep_ranges": keep_ranges,
        "reasoning": "regen clamp fixture",
        "relevance_score": 0.9,
        "virality_score": 90,
        "hook_score": 88,
        "engagement_score": 87,
        "value_score": 86,
        "shareability_score": 85,
        "hook_type": None,
        "hook_title": None,
    }


def _render(tmp_path, segment, **kwargs):
    source = tmp_path / "source.mp4"
    _make_source(source)
    _seed_words(source)
    out = tmp_path / "out"
    out.mkdir(exist_ok=True)
    return create_clips_from_segments(
        source,
        [segment],
        out,
        caption_template="default",
        output_format="vertical",
        add_subtitles=True,
        **kwargs,
    )


def test_regen_blocks_sentence_extension_leak_into_excluded(tmp_path):
    # Carried keep range ends at 14s mid-sentence; the extension wants
    # to run to the sentence end at ~18s. The brief excludes [15, 25].
    clips = _render(
        tmp_path,
        _segment("00:05", "00:14", [[5.0, 14.0]]),
        excluded_ranges=[(15.0, 25.0)],
    )
    assert len(clips) == 1
    ranges = load_clip_source_ranges(Path(clips[0]["path"]))
    assert ranges
    for start, end in ranges:
        assert end <= 15.0 + 0.05, f"extension leaked excluded footage: {ranges}"


def test_regen_segment_fully_inside_excluded_renders_nothing(tmp_path):
    clips = _render(
        tmp_path,
        _segment("00:16", "00:20", [[16.0, 20.0]]),
        excluded_ranges=[(15.0, 25.0)],
    )
    assert clips == []
    assert not list((tmp_path / "out").glob("*.mp4"))


def test_regen_intersects_allowed_ranges(tmp_path):
    clips = _render(
        tmp_path,
        _segment("00:00", "00:14", [[0.0, 14.0]]),
        allowed_ranges=[(5.0, 12.0)],
    )
    assert len(clips) == 1
    ranges = load_clip_source_ranges(Path(clips[0]["path"]))
    assert ranges
    for start, end in ranges:
        assert start >= 5.0 - 0.05 and end <= 12.0 + 0.05


def test_regen_without_brief_ranges_keeps_historical_extension(tmp_path):
    # No ranges passed (every non-brief regeneration): the sentence
    # extension still applies exactly as before — the clamp is inert.
    clips = _render(tmp_path, _segment("00:05", "00:14", [[5.0, 14.0]]))
    assert len(clips) == 1
    ranges = load_clip_source_ranges(Path(clips[0]["path"]))
    assert ranges
    assert max(end for _s, end in ranges) > 17.0
