"""Caption band consistency + the title/caption mutual rule.

The defect (MrBeast exports, 2026-11-15): the seven clips of ONE
task rendered the hook title at the bottom, lower-mid, top, and at
centre with the karaoke caption sitting directly on top of it as one
colliding block. Two independent guards caused it: the title guard
and the caption guard each avoided faces, neither knew the other
block existed, and captions re-decided their centre per chunk from
that chunk's local face geometry alone.

The fix under test: one caption band per clip, decided from the
clip's dominant geometry (the union of all face zones, fit strips
and the title block), chunks deviating only when their own shot
forces it; and the title block as a hard obstacle — with a minimum
separation gap — for every caption chunk it overlaps in time.
"""

from __future__ import annotations

import re

from src.media import captions

POS_RE = re.compile(r"\\pos\((\d+),(\d+)\)")
AN2_POS_RE = re.compile(r"\\an2\\pos\((\d+),(\d+)\)")


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
        found.append(int(match.group(2)))
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


def _title_block(ass_text):
    """(top, bottom) of the rendered hook title block, in pixels."""
    hook = next(
        line for line in ass_text.splitlines()
        if line.startswith("Dialogue:") and ",Hook," in line
    )
    style = next(
        line for line in ass_text.splitlines() if line.startswith("Style: Hook")
    )
    fields = style.split(",")
    font_px = int(fields[2])
    border_style = int(fields[15])
    outline = int(fields[16])
    margin_v = int(fields[21])
    lines = hook.count("\\N") + 1
    block_h = lines * font_px * 1.2 + (2 * outline if border_style == 3 else 0)
    moved = AN2_POS_RE.search(hook)
    if moved:
        bottom = int(moved.group(2))
        return bottom - block_h, bottom
    return float(margin_v), margin_v + block_h


WORDS_TWO_CHUNKS = _words(
    ("alpha", 0.0, 0.75), ("bravo", 0.75, 1.5),
    ("charlie", 1.5, 2.25), ("delta", 2.25, 3.0),
    ("echo", 3.0, 3.75), ("foxtrot", 3.75, 4.5),
    ("golf", 4.5, 5.25), ("hotel", 5.25, 6.0),
)


def test_caption_band_is_one_centre_across_shots(tmp_path, monkeypatch):
    # Shot A (0-3s) has a face ON the preferred centre; shot B (3-6s)
    # has its face at the top. Per-chunk guarding alone would move
    # shot A's captions and leave shot B's at the preference — two
    # heights in one clip. The band moves once, for the whole clip.
    centre_face = (340.0, 760.0, 400.0, 400.0)   # spans 760..1160
    top_face = (340.0, 60.0, 400.0, 300.0)       # spans 60..360
    ass, report = _build(
        tmp_path, monkeypatch, WORDS_TWO_CHUNKS,
        position_y_override=0.5,
        face_zones=[(0.0, 3.0, [centre_face]), (3.0, 6.0, [top_face])],
    )
    positions = _caption_positions(ass)
    assert positions, "no caption events rendered"
    assert len(set(positions)) == 1, positions
    centre = positions[0]
    # The band itself moved off the (blocked) preferred centre.
    assert abs(centre - report["caption_y"] * 1920) > 1
    half = _caption_block_half(ass)
    # The shared centre clears BOTH shots' faces (12px guard margin).
    assert centre - half >= 1160 + 12 - 1 or centre + half <= 760 - 12 + 1
    band = report["caption_band"]
    assert band["deviations"] == 0
    assert abs(band["centre"] - centre / 1920) < 0.001


def test_caption_never_collides_with_moved_title(tmp_path, monkeypatch):
    # The m7 geometry: faces above and below leave one free band in
    # the middle. The title guard takes the top of it (nearest its
    # preference); captions preferring the same band must clear the
    # title block by the separation gap, not sit on top of it.
    top_face = (340.0, 60.0, 400.0, 740.0)        # spans 60..800
    bottom_face = (340.0, 1150.0, 400.0, 710.0)  # spans 1150..1860
    ass, report = _build(
        tmp_path, monkeypatch, WORDS_TWO_CHUNKS,
        hook_title="BIG WIN",
        position_y_override=0.5,
        face_zones=[(0.0, 6.0, [top_face, bottom_face])],
    )
    _title_top, title_bottom = _title_block(ass)
    assert report["face_guard_title"]["moved"] is True  # title left the top
    half = _caption_block_half(ass)
    positions = _caption_positions(ass)
    assert positions
    for centre in positions:
        separation = (centre - half) - title_bottom
        assert separation >= (
            captions.TITLE_CAPTION_MIN_GAP_PX + 12 - 1
        ), (centre, title_bottom, separation)
    assert report["caption_band"]["title_gap"] is True


def test_caption_clears_unmoved_title_at_top(tmp_path, monkeypatch):
    # Title sits at its default top (nothing pushed it). A template
    # whose caption preference is high on the frame still has to
    # clear the title block by the gap — the title is an obstacle
    # even when the chunk's own face sits far below.
    from src.caption_templates import get_template

    template = {**get_template("default"), "position_y": 0.15}
    mid_face = (340.0, 900.0, 400.0, 400.0)
    ass, report = _build(
        tmp_path, monkeypatch, WORDS_TWO_CHUNKS,
        hook_title="BIG WIN",
        template_override=template,
        face_zones=[(0.0, 6.0, [mid_face])],
    )
    assert report["face_guard_title"]["moved"] is False
    _title_top, title_bottom = _title_block(ass)
    half = _caption_block_half(ass)
    for centre in _caption_positions(ass):
        assert (centre - half) - title_bottom >= (
            captions.TITLE_CAPTION_MIN_GAP_PX + 12 - 1
        ), (centre, title_bottom)


def test_no_zones_no_band_and_no_title_obstacle(tmp_path, monkeypatch):
    # Unguarded renders are untouched: no band stamp, and the title's
    # presence alone does not move a single caption.
    ass, report = _build(
        tmp_path, monkeypatch, WORDS_TWO_CHUNKS,
        hook_title="BIG WIN",
        position_y_override=0.5,
    )
    assert "caption_band" not in report
    assert "face_guard_captions" not in report
    plain_ass, _ = _build(
        tmp_path, monkeypatch, WORDS_TWO_CHUNKS,
        position_y_override=0.5,
    )
    assert _caption_positions(ass) == _caption_positions(plain_ass)
