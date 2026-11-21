"""Trend-Radar D16 + D25: mistake_fix vs statistic, RENDERED.

One finance fixture moment (the same 30 s of timed sentences the
unit tests use) is cut twice through the real pipeline — EDL ->
caption ASS -> ffmpeg render — once under a statistic hook and once
under the mistake_fix hook whose title the builder itself produced
from the moment's own words. The frame evidence: at t=1s both
renders carry their (different) hook titles burned in; and the
mistake_fix render's fix sentence is a caption inside the first
third of the clip (starts 4.5 s of 30 s), which is the eligibility
fact the style was granted on.

No audience outcome is measured or claimed — this proves the two
cuts exist, differ where the hook differs, and that the fix lands
where D25 requires it to land.
"""

import copy
import hashlib
import json
import re
import shutil
import subprocess

import pytest

from src.auto_director.edl import build_edl, load_profile
from src.auto_director.mistake_fix import (
    build_mistake_fix_title,
    mistake_fix_eligibility,
)
from src.media.captions import build_assemblyai_ass_subtitles
from src.media.edl_render import render_edl_clip_ffmpeg
from tests.unit.test_mistake_fix import FINANCE_DURATION, FINANCE_EARLY

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
            f"testsrc2=size={SOURCE_W}x{SOURCE_H}:rate=30:duration={int(FINANCE_DURATION)}",
            "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=44100",
            "-t", str(int(FINANCE_DURATION)),
            "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac",
            str(path),
        ],
        check=True,
    )


def _render_variant(tmp_path, source, profile, title, tag):
    words = _words(FINANCE_EARLY)
    edl = build_edl(
        {"start": 0.0, "end": FINANCE_DURATION, "duration": FINANCE_DURATION},
        words, profile, [],
    )
    ass = tmp_path / f"{tag}.ass"
    assert build_assemblyai_ass_subtitles(
        source, 0.0, FINANCE_DURATION, SOURCE_W, SOURCE_H, ass,
        caption_words=words,
        hook_title=title,
        hook_title_seconds=2.0,
        caption_template=(profile.get("captions") or {}).get("template"),
        profile_name=profile.get("niche"),
    )
    out = tmp_path / f"{tag}.mp4"
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
def test_finance_fixture_cut_both_ways_with_fix_in_first_third(tmp_path):
    source = tmp_path / "source.mp4"
    _make_source(source)

    # The style is granted on the eligibility fact, and its title is
    # the builder's own output — not a hand-written stand-in.
    verdict = mistake_fix_eligibility(FINANCE_EARLY, FINANCE_DURATION)
    assert verdict.eligible and verdict.finding.fix_start == 4.5
    mistake_title = build_mistake_fix_title(FINANCE_EARLY)
    assert mistake_title == "Stop spending your whole salary in the first week"

    finance = load_profile("finance_business")
    statistic_profile = copy.deepcopy(finance)
    statistic_profile["hook"]["title_style"] = "statistic"
    mistake_profile = copy.deepcopy(finance)
    mistake_profile["hook"]["title_style"] = "mistake_fix"

    stat_out, stat_ass = _render_variant(
        tmp_path, source, statistic_profile, STATISTIC_TITLE, "statistic"
    )
    fix_out, fix_ass = _render_variant(
        tmp_path, source, mistake_profile, mistake_title, "mistake_fix"
    )

    for out in (stat_out, fix_out):
        assert out.exists() and out.stat().st_size > 10_000
        assert 29.0 <= _duration(out) <= 31.0

    # Each render's caption file carries its own hook title (the ASS
    # styles word-by-word, so compare with override blocks stripped).
    def _plain(ass_path):
        text = re.sub(r"\{[^}]*\}", "", ass_path.read_text())
        return text.replace("\\N", " ")

    assert "Save Thirty Percent More" in _plain(stat_ass)
    assert "Stop spending your whole salary" in _plain(fix_ass)

    # Frame evidence at t=1s: the title band differs between the two
    # cuts (different titles burned in) and from the bare source.
    source_band = _band_hash(source, 1.0, 40, 320)
    stat_band = _band_hash(stat_out, 1.0, 40, 320)
    fix_band = _band_hash(fix_out, 1.0, 40, 320)
    assert stat_band != fix_band
    assert stat_band != source_band
    assert fix_band != source_band

    # The fix lands in the first third: in the mistake_fix render the
    # fix sentence is captioned (the caption builder chunks it, so
    # read the ordered chunk stream), its first chunk starts at
    # 4.5 s (limit: 10 s), and the caption band at t=6 s (inside the
    # fix sentence) differs from the same band at t=20 s.
    def _ass_seconds(stamp):
        hours, minutes, rest = stamp.split(":")
        return int(hours) * 3600 + int(minutes) * 60 + float(rest)

    chunks = []
    for line in fix_ass.read_text().splitlines():
        if not line.startswith("Dialogue:") or ",Hook," in line:
            continue
        parts = line.split(",", 9)
        text = " ".join(
            re.sub(r"\{[^}]*\}", "", parts[9]).replace("\\N", " ").split()
        )
        if text and (not chunks or chunks[-1][1] != text):
            chunks.append((_ass_seconds(parts[1]), text))
    stream = " ".join(text for _, text in chunks)
    assert "Instead, move half of it into a separate account on payday." in stream
    fix_start = next(start for start, text in chunks if text.startswith("Instead,"))
    assert fix_start <= FINANCE_DURATION / 3
    assert _band_hash(fix_out, 6.0, 620, 300) != _band_hash(fix_out, 20.0, 620, 300)
