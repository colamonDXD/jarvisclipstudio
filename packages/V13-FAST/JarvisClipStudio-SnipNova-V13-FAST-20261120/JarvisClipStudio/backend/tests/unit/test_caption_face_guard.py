"""Caption/title face guard — ASS-level behaviour.

The guard consumes output-space face zones (from the reframe plan)
and must keep caption blocks and the pinned hook title off faces:
colliding placements move, clear placements are byte-identical to
the unguarded build, editor placements are never touched, and zones
only affect the caption chunks they overlap in time.
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
    """(start_seconds, centre_y) for every Default-style dialogue."""
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


def _hook_event(ass_text):
    for line in ass_text.splitlines():
        if line.startswith("Dialogue:") and ",Hook," in line:
            return line
    raise AssertionError("no hook event in ASS")


def _caption_block_half(ass_text):
    """Half the caption block height, from the ASS Default style.

    Mirrors the builder's own block estimate: text height font_px*1.5
    plus outline + shadow + slack padding on each side.
    """
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
CENTRE_FACE = (340.0, 760.0, 400.0, 400.0)  # centre (540, 960)


def test_caption_moves_off_centre_face(tmp_path, monkeypatch):
    ass, report = _build(
        tmp_path, monkeypatch, WORDS,
        position_y_override=0.5,
        face_zones=[(0.0, 6.0, [CENTRE_FACE])],
    )
    positions = _caption_positions(ass)
    assert positions
    half = _caption_block_half(ass)
    for _start, y in positions:
        # The block must clear the face box expanded by the 12px
        # safety margin: face spans 760..1160 -> clear outside 748/1172.
        assert y + half <= 748 + 1 or y - half >= 1172 - 1, y
    assert report["face_guard_captions"]["moves"] >= 1


def test_caption_unchanged_when_face_clear(tmp_path, monkeypatch):
    zones = [(0.0, 6.0, [(340.0, 60.0, 400.0, 300.0)])]  # face at top
    guarded, _ = _build(
        tmp_path, monkeypatch, WORDS,
        position_y_override=0.5, face_zones=zones,
    )
    plain, _ = _build(tmp_path, monkeypatch, WORDS, position_y_override=0.5)
    assert guarded == plain


def test_caption_zones_are_time_scoped(tmp_path, monkeypatch):
    words = _words(
        ("alpha", 0.0, 1.5), ("bravo", 1.5, 3.0),
        ("charlie", 6.0, 7.5), ("delta", 7.5, 9.0),
        ("echo", 9.0, 10.5), ("foxtrot", 10.5, 12.0),
    )
    ass, _ = _build(
        tmp_path, monkeypatch, words,
        position_y_override=0.5,
        face_zones=[(0.0, 3.0, [CENTRE_FACE])],
    )
    positions = _caption_positions(ass)
    half = _caption_block_half(ass)
    early = [y for start, y in positions if start < 3.0]
    late = [y for start, y in positions if start >= 5.0]
    assert early and all(
        y + half <= 748 + 1 or y - half >= 1172 - 1 for y in early
    )
    # Late chunks keep the unguarded centre: identical to a no-zone build.
    plain, _ = _build(tmp_path, monkeypatch, words, position_y_override=0.5)
    plain_late = [y for start, y in _caption_positions(plain) if start >= 5.0]
    assert late and late == plain_late


def test_editor_placements_bypass_guard(tmp_path, monkeypatch):
    placements = [{"font_px": 60, "x": 540, "y": 960} for _ in range(8)]
    zones = [(0.0, 6.0, [CENTRE_FACE])]
    guarded, report = _build(
        tmp_path, monkeypatch, WORDS,
        caption_placements=placements, face_zones=zones,
    )
    plain, _ = _build(
        tmp_path, monkeypatch, WORDS, caption_placements=placements,
    )
    assert guarded == plain
    assert "face_guard_captions" not in report


def test_title_moves_into_split_gap(tmp_path, monkeypatch):
    # Split geometry as the planner stamps it: the top tile's face sits
    # almost at the tile's top edge (the tile is 2.4x the face height,
    # face centred at 42%), so there is no headroom above it and the
    # inter-tile gap is the only band that fits the title.
    zones = [(
        0.0, 6.0,
        [(300.0, 40.0, 480.0, 380.0), (300.0, 1000.0, 480.0, 380.0)],
    )]
    ass, report = _build(
        tmp_path, monkeypatch, WORDS,
        hook_title="Assistant Buys Peaky Blinders Outfit For Pubs",
        face_zones=zones,
    )
    event = _hook_event(ass)
    match = AN2_POS_RE.search(event)
    assert match, event
    bottom = int(match.group(2))
    hook_style = next(
        line for line in ass.splitlines() if line.startswith("Style: Hook")
    )
    hook_px = int(hook_style.split(",")[2])
    line_count = event.count("\\N") + 1
    block_h = line_count * hook_px * 1.2
    # Faces expanded: top 28..432, bottom 988..1392. The title block
    # sits fully inside the gap band between them.
    assert bottom <= 988 + 1
    assert bottom - block_h >= 432 - 1
    assert report["face_guard_title"]["moved"] is True


def test_title_moves_above_face_when_headroom_exists(tmp_path, monkeypatch):
    # Single-speaker geometry with real headroom above the face: the
    # nearest face-free band is above the face, inside the top area.
    zones = [(0.0, 6.0, [(300.0, 320.0, 480.0, 380.0)])]
    ass, report = _build(
        tmp_path, monkeypatch, WORDS,
        hook_title="Assistant Buys Peaky Blinders Outfit For Pubs",
        face_zones=zones,
    )
    match = AN2_POS_RE.search(_hook_event(ass))
    assert match
    bottom = int(match.group(2))
    assert bottom <= 320 - 12 + 1  # block bottom clears the expanded face
    assert report["face_guard_title"]["moved"] is True


def test_title_unchanged_when_clear_or_no_zones(tmp_path, monkeypatch):
    title = "Assistant Buys Peaky Blinders Outfit For Pubs"
    plain, _ = _build(tmp_path, monkeypatch, WORDS, hook_title=title)
    # A mid-frame face (700..1000) clears both the title band
    # (230..~410) and the default caption block (~1430..1550).
    clear_face, report = _build(
        tmp_path, monkeypatch, WORDS, hook_title=title,
        face_zones=[(0.0, 6.0, [(300.0, 700.0, 480.0, 300.0)])],
    )
    assert clear_face == plain
    assert report["face_guard_title"]["moved"] is False


def test_title_shrinks_when_no_band_fits_at_full_size(tmp_path, monkeypatch):
    title = "Assistant Buys Peaky Blinders Outfit For Pubs"
    zones = [(
        0.0, 6.0,
        [(0.0, 0.0, 1080.0, 700.0), (0.0, 850.0, 1080.0, 1070.0)],
    )]
    ass, _ = _build(
        tmp_path, monkeypatch, WORDS, hook_title=title, face_zones=zones,
    )
    event = _hook_event(ass)
    assert AN2_POS_RE.search(event), event
    hook_style = next(
        line for line in ass.splitlines() if line.startswith("Style: Hook")
    )
    full, _ = _build(tmp_path, monkeypatch, WORDS, hook_title=title)
    full_style = next(
        line for line in full.splitlines() if line.startswith("Style: Hook")
    )
    assert int(hook_style.split(",")[2]) < int(full_style.split(",")[2])


def test_shift_zones_for_join_plan():
    zones = [(1.0, 5.0, [(0.0, 0.0, 10.0, 10.0)])]
    plan = [{"source_boundary": 3.0, "fade": 0.4}]
    shifted = captions.shift_zones_for_join_plan(zones, plan)
    assert shifted[0][0] == 1.0
    assert shifted[0][1] == 5.0 - 0.4
    assert captions.shift_zones_for_join_plan(zones, None) == zones
    assert captions.shift_zones_for_join_plan(zones, []) == zones
