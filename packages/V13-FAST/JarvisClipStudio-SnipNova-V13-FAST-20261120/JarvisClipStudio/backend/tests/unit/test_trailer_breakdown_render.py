"""Trend-Radar D17: trailer breakdown vs plain reaction cut, RENDERED.

The same trailer + reaction fixture (two trailer windows, two
face-cam reactions, one legible on-screen detail per freeze) is cut
twice through the real pipeline — EDL -> caption ASS -> ffmpeg
render — once as a plain reaction cut and once as a trailer
breakdown (freeze + punch-zoom + red circle + reaction beat +
cliffhanger). The frame evidence: in the breakdown the picture is
HELD across the freeze window (identical frames inside it, zoomed
early in the hold and unzoomed late in it), the red circle sits on
the real detail only during the freeze, the cliffhanger text lands
in the last 2.5 s, and the freeze window renders SILENT while the
reaction beat keeps the voice — while the plain cut moves
throughout and carries sound the whole way.

STAND-IN FIXTURE: no real Avengers: Doomsday trailer exists yet
(the final trailer is only rumoured for 16–22 Nov), so the
"trailer" is testsrc2 pattern footage and the "reaction" is the
same pattern with a different source window. What this proves is
the MECHANISM — freeze, zoom, circle, silence, cliffhanger — not
that a real trailer breakdown holds attention. Only posting data
can prove that.
"""

import json
import re
import shutil
import subprocess

import numpy as np
import pytest

from src.auto_director.edl import load_profile
from src.auto_director.trailer_breakdown import (
    HOOK_FORMULA,
    append_trailer_breakdown_overlay,
    build_breakdown_plan,
    plan_to_edl_segments,
    trailer_breakdown_eligibility,
)
from src.media.captions import build_assemblyai_ass_subtitles
from src.media.edl_render import render_edl_clip_ffmpeg
from tests.unit.test_trailer_breakdown import DURATION, REACTIONS, SPOTS, TRAILERS

SOURCE_W, SOURCE_H = 540, 960

# Clip-time sentences: the words live in the trailer and reaction
# beats only — the freeze holds are silent, so no word lands inside
# a hold (in production the transcript words come from the source
# and the same holds: a freeze's source window is one frame).
SENTENCES = [
    {"start": 0.0, "end": 2.8, "text": "Okay the new Doomsday trailer just dropped"},
    {"start": 4.6, "end": 7.3, "text": "Wait did you see the writing on the wall behind him"},
    {"start": 7.6, "end": 10.3, "text": "And here it is again in the second shot"},
    {"start": 12.1, "end": 14.8, "text": "There is a figure standing in the background the whole time"},
]

# The first detail's box, expanded a little so the crop catches the
# red ring drawn around it (margin 0.03 of the frame).
CIRCLE_BOX = (0.50, 0.25, 0.80, 0.47)
# The cliffhanger sits at y 0.78 for the last 2.5 s.
CLIFFHANGER_BAND = (0.74, 0.86)


def _words(sentences):
    words = []
    for sentence in sentences:
        toks = sentence["text"].split()
        span = (sentence["end"] - sentence["start"]) / len(toks)
        for i, tok in enumerate(toks):
            words.append(
                {
                    "word": tok,
                    "text": tok,
                    "start": sentence["start"] + i * span,
                    "end": sentence["start"] + (i + 1) * span,
                }
            )
    return words


def _make_source(path):
    subprocess.run(
        [
            "ffmpeg", "-y", "-v", "error",
            "-f", "lavfi", "-i",
            f"testsrc2=size={SOURCE_W}x{SOURCE_H}:rate=30:duration={int(DURATION)}",
            "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=44100",
            "-t", str(int(DURATION)),
            "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac",
            str(path),
        ],
        check=True,
    )


def _probe(path):
    probe = subprocess.run(
        [
            "ffprobe", "-v", "error",
            "-show_entries", "format=duration",
            "-show_entries", "stream=width,height",
            "-select_streams", "v:0", "-of", "json", str(path),
        ],
        capture_output=True, text=True, check=True,
    )
    data = json.loads(probe.stdout)
    stream = data["streams"][0]
    return float(data["format"]["duration"]), int(stream["width"]), int(stream["height"])


def _frame(video, at_seconds):
    duration, width, height = _probe(video)
    raw = subprocess.run(
        [
            "ffmpeg", "-v", "error", "-ss", str(at_seconds), "-i", str(video),
            "-frames:v", "1", "-f", "rawvideo", "-pix_fmt", "rgb24", "-",
        ],
        capture_output=True, check=True,
    ).stdout
    return np.frombuffer(raw, dtype=np.uint8).reshape(height, width, 3).astype(int)


def _frame_stats(video_a, at_a, video_b, at_b, box=None):
    """(mean abs diff, pixels differing by >30, by >8) between two frames.

    Frame comparisons are TOLERANCE-based, not md5 equality: the
    lossy x264 encode shimmers a few hundred pixels even between two
    frames of one held still (measured: 355 px differ by >8, 0 by
    >30, mean 0.028). A real change — a zoom landing, a moving
    picture, a ring appearing — moves tens of thousands of pixels
    by >8 with mean >0.7. The thresholds below sit between those
    two measured regimes with margin on both sides.
    """
    a = _frame(video_a, at_a)
    b = _frame(video_b, at_b)
    if box is not None:
        height, width, _ = a.shape
        x0 = int(box[0] * width)
        y0 = int(box[1] * height)
        x1 = int(box[2] * width)
        y1 = int(box[3] * height)
        a, b = a[y0:y1, x0:x1], b[y0:y1, x0:x1]
    diff = np.abs(a - b).max(axis=2)
    return float(diff.mean()), int((diff > 30).sum()), int((diff > 8).sum())


def _mean_volume(video, start, length):
    out = subprocess.run(
        [
            "ffmpeg", "-v", "info", "-ss", str(start), "-t", str(length),
            "-i", str(video), "-af", "volumedetect", "-f", "null", "-",
        ],
        capture_output=True, text=True, check=True,
    ).stderr
    match = re.search(r"mean_volume:\s*([-\d.inf]+)\s*dB", out)
    assert match, f"no mean_volume in volumedetect output: {out[-500:]}"
    value = match.group(1)
    return -120.0 if value in ("-inf", "n/a") else float(value)


def _plain_edl():
    # The same four source windows, played straight: no freeze, no
    # zoom, no circle, no cliffhanger. 12 s against the breakdown's 15.
    return {
        "duration": 12.0,
        "source_ranges": [[0.0, DURATION]],
        "segments": [
            {"start": 2.0, "end": 5.0, "transition_in": "cut"},
            {"start": 5.0, "end": 8.0, "transition_in": "cut"},
            {"start": 10.0, "end": 13.0, "transition_in": "cut"},
            {"start": 13.0, "end": 16.0, "transition_in": "cut"},
        ],
        "zoom_keyframes": [],
    }


def _render(tmp_path, source, profile, edl, clip_duration, hook_title, overlay_plan, name):
    words = _words(SENTENCES)
    ass = tmp_path / f"{name}.ass"
    assert build_assemblyai_ass_subtitles(
        source, 0.0, clip_duration, SOURCE_W, SOURCE_H, ass,
        caption_words=words,
        hook_title=hook_title,
        hook_title_seconds=3.0,
        caption_template=(profile.get("captions") or {}).get("template"),
        profile_name=profile.get("niche"),
    )
    if overlay_plan is not None:
        assert append_trailer_breakdown_overlay(ass, overlay_plan, SOURCE_W, SOURCE_H) is True
    out = tmp_path / f"{name}.mp4"
    assert render_edl_clip_ffmpeg(source, out, edl, subtitle_ass_path=ass) is True
    return out, ass


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg required")
def test_trailer_fixture_cut_both_ways_with_freeze_zoom_circle(tmp_path):
    source = tmp_path / "source.mp4"
    _make_source(source)

    # The plan is the module's own output on the fixture — nothing
    # hand-built: two beats, 15 s, hook formula, cliffhanger naming
    # the last beat's detail in its own words.
    verdict = trailer_breakdown_eligibility(TRAILERS, REACTIONS, SPOTS, DURATION)
    assert verdict.eligible and verdict.plan is not None
    plan = verdict.plan
    assert plan.clip_duration == 15.0
    assert plan.hook_text == HOOK_FORMULA
    assert plan.cliffhanger == "Did you catch the figure in the background? Part 2"
    assert build_breakdown_plan(TRAILERS, REACTIONS, SPOTS, DURATION) is not None
    edl = plan_to_edl_segments(plan, [(0.0, DURATION)])
    assert edl["duration"] == 15.0

    profile = load_profile("gaming")
    plain_out, plain_ass = _render(
        tmp_path, source, profile, _plain_edl(), 12.0,
        "He reacts to the Doomsday trailer", None, "plain",
    )
    breakdown_out, breakdown_ass = _render(
        tmp_path, source, profile, edl, 15.0, HOOK_FORMULA, plan, "breakdown",
    )
    control_out, _ = _render(
        tmp_path, source, profile, edl, 15.0, HOOK_FORMULA, None, "control",
    )

    for out, expected in ((plain_out, 12.0), (breakdown_out, 15.0), (control_out, 15.0)):
        assert out.exists() and out.stat().st_size > 10_000
        duration, _, _ = _probe(out)
        assert expected - 0.3 <= duration <= expected + 0.3

    # Each render's caption file carries its own hook: the statement
    # in A, the formula + circle + cliffhanger in B. (The circle is
    # a pure \p1 drawing — the detail label "DOOM" is the ring's
    # target, not rendered text; the ring-on-the-detail is proven
    # by the frame hashes below, not by the ASS text.)
    def _plain(ass_path):
        text = re.sub(r"\{[^}]*\}", "", ass_path.read_text())
        return text.replace("\\N", " ")

    assert "He reacts to the Doomsday trailer" in _plain(plain_ass)
    assert "TrailerBreak" not in plain_ass.read_text()
    breakdown_text = breakdown_ass.read_text()
    assert "TrailerBreak" in breakdown_text
    assert "Part 2" in _plain(breakdown_ass)
    assert "TrailerBreak" not in control_out.with_suffix(".ass").read_text()

    # Frame evidence — the first freeze holds clip 3.0–4.5 s.
    # Inside the hold the picture is static: the zoomed half
    # (3.1 vs 3.4, punch-zoom [3.0, 3.5)) and the unzoomed half
    # (4.0 vs 4.4) each match themselves within encoder shimmer...
    mean, over30, _ = _frame_stats(breakdown_out, 3.1, breakdown_out, 3.4)
    assert mean < 0.1 and over30 == 0
    mean, over30, _ = _frame_stats(breakdown_out, 4.0, breakdown_out, 4.4)
    assert mean < 0.1 and over30 == 0
    # ...but the zoomed half differs from the unzoomed half — the
    # punch-zoom visibly lands on the frozen frame...
    mean, _, over8 = _frame_stats(breakdown_out, 3.1, breakdown_out, 4.0)
    assert mean > 0.3 and over8 > 5000
    # ...and the plain cut keeps moving through the same seconds.
    mean, over30, _ = _frame_stats(plain_out, 3.1, plain_out, 3.4)
    assert mean > 2.0 and over30 > 10000
    # The breakdown's freeze frame is not the plain cut's frame at
    # the same clip time at all.
    mean, _, _ = _frame_stats(breakdown_out, 4.0, plain_out, 4.0)
    assert mean > 2.0

    # The red circle is on the real detail only during the freeze:
    # the detail region differs between breakdown and control at
    # t=4.0 (ring present vs absent) and is identical at t=1.0
    # (trailer play, no ring in either).
    _, over30, _ = _frame_stats(breakdown_out, 4.0, control_out, 4.0, CIRCLE_BOX)
    assert over30 > 100
    mean, over30, _ = _frame_stats(breakdown_out, 1.0, control_out, 1.0, CIRCLE_BOX)
    assert mean < 0.05 and over30 == 0

    # The cliffhanger text lands in the last 2.5 s: the lower band
    # differs between breakdown and control at t=14.0.
    _, over30, _ = _frame_stats(
        breakdown_out, 14.0, control_out, 14.0,
        (0.0, CLIFFHANGER_BAND[0], 1.0, CLIFFHANGER_BAND[1]),
    )
    assert over30 > 10000

    # Audio evidence — the freeze window is silent by design, the
    # reaction beat keeps the voice, and the plain cut has sound
    # through the same seconds.
    assert _mean_volume(breakdown_out, 3.2, 1.2) < -50.0
    assert _mean_volume(breakdown_out, 5.0, 2.0) > -40.0
    assert _mean_volume(plain_out, 3.2, 1.2) > -40.0
