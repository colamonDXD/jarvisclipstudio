"""Trend-Radar D15: list_fill vs statistic, RENDERED.

One finance fixture moment (the same 30 s of timed sentences the
unit tests use) is cut twice through the real pipeline — EDL ->
caption ASS -> ffmpeg render — once under a statistic hook and once
under the list_fill overlay whose title and rows the module itself
produced from the moment's own words. The frame evidence: at t=1s
the list_fill render shows the heading with all three slots still
empty; at t=10s the first two rows have filled in as the speaker
reached them; at t=25s the list is complete — while the statistic
render shows only its title in the same region.

No audience outcome is measured or claimed — this proves the two
cuts exist, differ where the format differs, and that the list
fills progressively on the transcript's timing and completes inside
the clip.
"""

import hashlib
import json
import re
import shutil
import subprocess

import pytest

from src.auto_director.edl import build_edl, load_profile
from src.auto_director.list_fill import (
    append_list_fill_overlay,
    build_list_fill_plan,
    build_list_fill_title,
    list_fill_eligibility,
)
from src.media.captions import build_assemblyai_ass_subtitles
from src.media.edl_render import render_edl_clip_ffmpeg
from tests.unit.test_list_fill import LIST_FILL, LIST_FILL_DURATION

STATISTIC_TITLE = "Save Thirty Percent More Without Earning Extra"
SOURCE_W, SOURCE_H = 540, 960


def _words(sentences):
    words = []
    for sentence in sentences:
        toks = sentence["text"].split()
        span = (sentence["end"] - sentence["start"]) / len(toks)
        for i, tok in enumerate(toks):
            words.append(
                {
                    # Both keys, as production transcript words carry:
                    # the EDL reads "word", the caption builder "text".
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
            f"testsrc2=size={SOURCE_W}x{SOURCE_H}:rate=30:duration={int(LIST_FILL_DURATION)}",
            "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=44100",
            "-t", str(int(LIST_FILL_DURATION)),
            "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac",
            str(path),
        ],
        check=True,
    )


def _render_statistic(tmp_path, source, profile):
    words = _words(LIST_FILL)
    edl = build_edl(
        {"start": 0.0, "end": LIST_FILL_DURATION, "duration": LIST_FILL_DURATION},
        words, profile, [],
    )
    ass = tmp_path / "statistic.ass"
    assert build_assemblyai_ass_subtitles(
        source, 0.0, LIST_FILL_DURATION, SOURCE_W, SOURCE_H, ass,
        caption_words=words,
        hook_title=STATISTIC_TITLE,
        hook_title_seconds=2.0,
        caption_template=(profile.get("captions") or {}).get("template"),
        profile_name=profile.get("niche"),
    )
    out = tmp_path / "statistic.mp4"
    assert render_edl_clip_ffmpeg(source, out, edl, subtitle_ass_path=ass) is True
    return out, ass


def _render_list_fill(tmp_path, source, profile, plan):
    words = _words(LIST_FILL)
    edl = build_edl(
        {"start": 0.0, "end": LIST_FILL_DURATION, "duration": LIST_FILL_DURATION},
        words, profile, [],
    )
    ass = tmp_path / "list_fill.ass"
    # No separate hook title: the list heading IS the hook for this
    # cut, and it stays on screen for the whole clip.
    assert build_assemblyai_ass_subtitles(
        source, 0.0, LIST_FILL_DURATION, SOURCE_W, SOURCE_H, ass,
        caption_words=words,
        hook_title=None,
        hook_title_seconds=2.0,
        caption_template=(profile.get("captions") or {}).get("template"),
        profile_name=profile.get("niche"),
    )
    assert append_list_fill_overlay(ass, plan, SOURCE_W, SOURCE_H) is True
    out = tmp_path / "list_fill.mp4"
    assert render_edl_clip_ffmpeg(source, out, edl, subtitle_ass_path=ass) is True
    return out, ass


def _duration(path):
    probe = subprocess.run(
        [
            "ffprobe", "-v", "error", "-show_entries", "format=duration",
            "-of", "json", str(path),
        ],
        capture_output=True, text=True, check=True,
    )
    return float(json.loads(probe.stdout)["format"]["duration"])


def _band_hash(video, at_seconds, y, height):
    raw = subprocess.run(
        [
            "ffmpeg", "-v", "error", "-ss", str(at_seconds), "-i", str(video),
            "-frames:v", "1", "-vf", f"crop={SOURCE_W}:{height}:0:{y}",
            "-f", "rawvideo", "-pix_fmt", "gray", "-",
        ],
        capture_output=True, check=True,
    ).stdout
    return hashlib.md5(raw).hexdigest()


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg required")
def test_finance_fixture_cut_both_ways_with_list_filling_live(tmp_path):
    source = tmp_path / "source.mp4"
    _make_source(source)

    # The style is granted on the eligibility fact, and its title and
    # rows are the module's own output — not hand-written stand-ins.
    verdict = list_fill_eligibility(LIST_FILL, LIST_FILL_DURATION)
    assert verdict.eligible and verdict.plan is not None
    assert verdict.plan.items[-1].reveal_end == 20.0
    title = build_list_fill_title(LIST_FILL)
    assert title == "Three money habits for a rich life"
    assert verdict.plan.title == title
    plan = build_list_fill_plan(LIST_FILL, LIST_FILL_DURATION)
    assert plan is not None

    profile = load_profile("finance_business")
    stat_out, stat_ass = _render_statistic(tmp_path, source, profile)
    fill_out, fill_ass = _render_list_fill(tmp_path, source, profile, plan)

    for out in (stat_out, fill_out):
        assert out.exists() and out.stat().st_size > 10_000
        assert 29.0 <= _duration(out) <= 31.0

    # Each render's caption file carries its own hook: the statistic
    # title in A, the list heading + rows in B (the ASS styles
    # word-by-word, so compare with override blocks stripped).
    def _plain(ass_path):
        text = re.sub(r"\{[^}]*\}", "", ass_path.read_text())
        return text.replace("\\N", " ")

    assert "Save Thirty Percent More" in _plain(stat_ass)
    assert "ListFill" in fill_ass.read_text()
    fill_plain = _plain(fill_ass)
    assert "Three money habits for a rich life" in fill_plain
    assert "Automate your savings on payday" in fill_plain
    assert "Keep one month of spending in a separate account" in fill_plain
    assert "Invest the rest before you can spend it" in fill_plain
    # The slots are there too: empty rows waiting to be filled.
    assert fill_plain.count("—") == 3

    # Frame evidence — the list region (y 140..520) holds the heading
    # at ~144 and the rows at ~269/365/461; the statistic cut has
    # nothing there but the bare source pattern.
    # t=1s: heading + three empty slots.
    # t=10s: rows 1-2 filled (revealed at 3s and 8s), slot 3 empty.
    # t=25s: all three rows filled (last revealed at 14s).
    fill_t1 = _band_hash(fill_out, 1.0, 140, 380)
    fill_t10 = _band_hash(fill_out, 10.0, 140, 380)
    fill_t25 = _band_hash(fill_out, 25.0, 140, 380)
    assert fill_t1 != fill_t10
    assert fill_t10 != fill_t25
    assert fill_t1 != fill_t25
    assert fill_t1 != _band_hash(source, 1.0, 140, 380)
    assert fill_t25 != _band_hash(source, 25.0, 140, 380)
    assert fill_t1 != _band_hash(stat_out, 1.0, 140, 380)
    assert fill_t25 != _band_hash(stat_out, 25.0, 140, 380)
