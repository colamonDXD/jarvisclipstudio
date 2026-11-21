"""Strip-aware captions: text overlapping a fit stays in the sharp band.

A fitted shot renders its source sharp only inside a centred strip
(for 1920x1080 -> 1080x1920: y 657..1263); above and below is blurred
backdrop. Before this fix the caption guard only knew face boxes, so
on a faceless fit — or below the strip's face — captions rendered at
the template's ~74-80% height, inside the bottom blur band (measured
on the TJR v4 export). The builder now takes the plan's fit strips
and treats the bands as obstacles for the same guard.
"""

from __future__ import annotations

import re

from src.media import captions

POS_RE = re.compile(r"\\pos\((\d+),(\d+)\)")

STRIP = (0.0, 6.0, 657.0, 1263.0)  # (start, end, top, bottom)


def _words(*items):
    return [
        {"text": text, "start": start, "end": end, "confidence": 0.99}
        for text, start, end in items
    ]


def _build(tmp_path, monkeypatch, words, **kwargs):
    monkeypatch.setattr(captions, "emoji_rendering_supported", lambda: False)
    output = tmp_path / "captions.ass"
    report = {}
    assert captions.build_assemblyai_ass_subtitles(
        tmp_path / "missing.mp4", 0.0, 30.0, 1080, 1920, output,
        caption_words=words, caption_layout_report=report, **kwargs,
    )
    return output.read_text(encoding="utf-8"), report


def _caption_positions(ass_text):
    found = []
    for line in ass_text.splitlines():
        if not line.startswith("Dialogue:") or ",Default," not in line:
            continue
        match = POS_RE.search(line)
        assert match, line
        stamp = line.split(",")[1]
        hours, minutes, rest = stamp.split(":")
        start = int(hours) * 3600 + int(minutes) * 60 + float(rest)
        found.append((start, int(match.group(2))))
    return found


def _caption_block_half(ass_text):
    style = next(
        line for line in ass_text.splitlines() if line.startswith("Style: Default")
    )
    fields = style.split(",")
    font_px = int(fields[2])
    outline = int(fields[16])
    shadow = int(fields[17])
    return (font_px * 1.5 + 2 * (outline + shadow + 6)) / 2


WORDS = _words(
    ("alpha", 0.0, 1.5), ("bravo", 1.5, 3.0),
    ("charlie", 3.0, 4.5), ("delta", 4.5, 6.0),
)


def test_caption_below_the_strip_moves_inside_it(tmp_path, monkeypatch):
    # No faces at all: the pre-fix guard had nothing to act on, and the
    # caption stayed at the template's low position, in the blur band.
    ass, report = _build(
        tmp_path, monkeypatch, WORDS,
        position_y_override=0.78, fit_strips=[STRIP],
    )
    positions = _caption_positions(ass)
    assert positions
    half = _caption_block_half(ass)
    for _start, y in positions:
        assert y - half >= 657 - 1, y
        assert y + half <= 1263 + 1, y
    assert report["fit_strip_captions"]["chunks"] >= 1
    assert report["face_guard_captions"]["moves"] >= 1


def test_caption_without_strips_stays_in_the_band(tmp_path, monkeypatch):
    # Control: the identical build with no strips keeps the low
    # template position — the strip is what moved the caption above.
    ass, _report = _build(
        tmp_path, monkeypatch, WORDS, position_y_override=0.78,
    )
    positions = _caption_positions(ass)
    assert positions
    assert all(y > 1263 for _start, y in positions)


def test_strip_is_time_scoped(tmp_path, monkeypatch):
    ass, _report = _build(
        tmp_path, monkeypatch, WORDS,
        position_y_override=0.78,
        fit_strips=[(0.0, 3.0, 657.0, 1263.0)],
    )
    positions = _caption_positions(ass)
    assert positions
    for start, y in positions:
        if start < 3.0:
            assert y <= 1263, (start, y)
        else:
            assert y > 1263, (start, y)


def test_caption_clears_a_face_inside_the_strip(tmp_path, monkeypatch):
    # The strip does not replace the face guard: with a face inside the
    # strip, the caption must satisfy both — inside the strip AND off
    # the face (expanded by the 12px margin: 788..1112 forbidden).
    face = (340.0, 800.0, 400.0, 300.0)
    ass, _report = _build(
        tmp_path, monkeypatch, WORDS,
        position_y_override=0.78,
        face_zones=[(0.0, 6.0, [face])],
        fit_strips=[STRIP],
    )
    positions = _caption_positions(ass)
    assert positions
    half = _caption_block_half(ass)
    for _start, y in positions:
        assert y - half >= 657 - 1, y
        assert y + half <= 1263 + 1, y
        assert y + half <= 788 + 1 or y - half >= 1112 - 1, y
