"""Blur-band guard for the pinned hook title on surviving fits.

The framing fix taught captions to treat a fitted shot's blurred pad
bands as obstacles (test_caption_fit_strips), but the hook title's
guard stayed face-only: on a surviving fit — a faceless or
screen-content shot that legitimately keeps the fit layout — the
title still rendered in its preferred top band, which on a fit is
blurred backdrop (default build: block at y 230..432 while the sharp
strip starts at 657). The title builder now receives the same band
boxes, computed by the same strip_band_boxes geometry over the
title's whole on-screen window, so the existing guard lands the
block inside the sharp strip. Crop/full-bleed shots have no strips
and keep byte-identical title placement.
"""

from __future__ import annotations

import re
from pathlib import Path

from src.media import captions, reframe_plan
from src.media.scene_context import SceneEvidence

POS_RE = re.compile(r"\\pos\((\d+),(\d+)\)")
AN2_POS_RE = re.compile(r"\\an2\\pos\((\d+),(\d+)\)")

# (start, end, top, bottom): the 1920x1080 -> 1080x1920 fit strip.
STRIP = (0.0, 30.0, 657.0, 1263.0)
TITLE = "The Food Here Is Incredible And Cheap"


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


def _hook_event(ass_text):
    for line in ass_text.splitlines():
        if line.startswith("Dialogue:") and ",Hook," in line:
            return line
    raise AssertionError("no hook event in ASS")


def _hook_block(ass_text):
    """(font_px, block_height) of the hook title, as the guard measures it.

    Mirrors build_hook_title_ass._block: lines * px * 1.2, plus the
    backing-box padding when the style is BorderStyle 3.
    """
    style = next(
        line for line in ass_text.splitlines() if line.startswith("Style: Hook")
    )
    fields = style.split(",")
    px = int(fields[2])
    border_style = int(fields[15])
    outline = int(fields[16])
    lines = _hook_event(ass_text).count("\\N") + 1
    block_h = lines * px * 1.2 + (2 * outline if border_style == 3 else 0)
    return px, block_h


def _hook_bottom(ass_text):
    match = AN2_POS_RE.search(_hook_event(ass_text))
    assert match, _hook_event(ass_text)
    return int(match.group(2))


WORDS = _words(
    ("alpha", 0.0, 1.5), ("bravo", 1.5, 3.0),
    ("charlie", 3.0, 4.5), ("delta", 4.5, 6.0),
)


def test_title_moves_below_the_top_blur_band_on_a_fit(tmp_path, monkeypatch):
    # No faces at all: the pre-fix title guard had nothing to act on,
    # and the title stayed at the top margin, inside the blur band.
    ass, report = _build(
        tmp_path, monkeypatch, WORDS, hook_title=TITLE, fit_strips=[STRIP],
    )
    _px, block_h = _hook_block(ass)
    bottom = _hook_bottom(ass)
    # The band boxes are expanded by the guard's 12px margin: the
    # block must clear 657+12 above and 1263-12 below.
    assert bottom - block_h >= 657 + 12 - 1, (bottom, block_h)
    assert bottom <= 1263 - 12 + 1, (bottom, block_h)
    assert report["face_guard_title"]["moved"] is True
    assert report["fit_strip_title"]["band_boxes"] == 2


def test_title_default_placement_is_inside_the_band(tmp_path, monkeypatch):
    # Control documenting the defect: with no strips the title keeps
    # its Alignment-8 top placement (no \pos), whose block — margin
    # 230 + two lines at 84px — sits at y 230..432, inside the band
    # a fit would blur (0..657).
    ass, report = _build(tmp_path, monkeypatch, WORDS, hook_title=TITLE)
    event = _hook_event(ass)
    assert not AN2_POS_RE.search(event), event
    style = next(
        line for line in ass.splitlines() if line.startswith("Style: Hook")
    )
    margin_v = int(style.split(",")[21])
    _px, block_h = _hook_block(ass)
    assert margin_v + block_h < 657
    assert "fit_strip_title" not in report


def test_title_byte_identical_without_strips(tmp_path, monkeypatch):
    # Crop/full-bleed shots produce no strips: placement must not
    # change at all — no strips, empty strips, and strips that never
    # overlap the title's on-screen window are all the same build.
    plain, _ = _build(tmp_path, monkeypatch, WORDS, hook_title=TITLE)
    empty, _ = _build(
        tmp_path, monkeypatch, WORDS, hook_title=TITLE, fit_strips=[],
    )
    assert empty == plain
    late, _ = _build(
        tmp_path, monkeypatch, WORDS, hook_title=TITLE,
        hook_title_seconds=3.0,
        fit_strips=[(25.0, 30.0, 657.0, 1263.0)],
    )
    assert late == _build(
        tmp_path, monkeypatch, WORDS, hook_title=TITLE,
        hook_title_seconds=3.0,
    )[0]


def test_partial_window_fit_still_guards_a_pinned_title(tmp_path, monkeypatch):
    # The title is pinned for the whole clip: a fit that exists for
    # only part of that window still blurs the band while the title
    # is on screen, so the pinned block must respect it throughout.
    ass, report = _build(
        tmp_path, monkeypatch, WORDS, hook_title=TITLE,
        fit_strips=[(10.0, 20.0, 657.0, 1263.0)],
    )
    _px, block_h = _hook_block(ass)
    bottom = _hook_bottom(ass)
    assert bottom - block_h >= 657 + 12 - 1, (bottom, block_h)
    assert bottom <= 1263 - 12 + 1, (bottom, block_h)
    assert report["fit_strip_title"]["band_boxes"] == 2


def test_title_clears_a_face_inside_the_strip(tmp_path, monkeypatch):
    # Bands join the obstacle set; they never outrank faces. A face
    # inside the strip near its top (expanded: 688..972) leaves the
    # strip's lower part as the only slot that satisfies both.
    face = (340.0, 700.0, 400.0, 260.0)
    ass, _report = _build(
        tmp_path, monkeypatch, WORDS, hook_title=TITLE,
        face_zones=[(0.0, 30.0, [face])],
        fit_strips=[STRIP],
    )
    _px, block_h = _hook_block(ass)
    bottom = _hook_bottom(ass)
    top = bottom - block_h
    assert top >= 657 + 12 - 1, (top, bottom)
    assert bottom <= 1263 - 12 + 1, (top, bottom)
    # Clear of the expanded face (688..972): fully below it (never
    # on the face to escape the band).
    assert top >= 972 - 1 or bottom <= 688 + 1, (top, bottom)


def test_title_shrinks_and_centres_when_the_strip_cannot_fit_it(
    tmp_path, monkeypatch
):
    # A 120px-tall sharp strip (900..1020) cannot hold the title at
    # any guarded size: the guard's documented fallback keeps the
    # smallest title centred in the largest free band — the strip —
    # rather than dropping or crashing.
    ass, report = _build(
        tmp_path, monkeypatch, WORDS, hook_title=TITLE,
        fit_strips=[(0.0, 30.0, 900.0, 1020.0)],
    )
    px, block_h = _hook_block(ass)
    full, _ = _build(tmp_path, monkeypatch, WORDS, hook_title=TITLE)
    full_px, _full_h = _hook_block(full)
    assert px < full_px
    bottom = _hook_bottom(ass)
    centre = bottom - block_h / 2
    assert 900 - 1 <= centre <= 1020 + 1, (centre, bottom, block_h)
    assert report["fit_strip_title"]["band_boxes"] == 2


def test_bands_never_push_the_title_onto_a_face(tmp_path, monkeypatch):
    # Priority pin: faces outrank bands. Two full-width faces tile
    # the strip's interior (expanded 657..981 and 981..1275) and a
    # third sits at the preferred top spot (expanded 188..472), so
    # faces + bands together leave no free band at all and the
    # combined pick can only fall back to the preferred position —
    # which touches the top face. The guard must then use the
    # face-only pick instead: the title may sit in a blur band when
    # nothing satisfies both, but never on a face to escape a band.
    # The result is byte-identical to the build with no strips.
    faces = [
        (0.0, 669.0, 1080.0, 300.0),
        (0.0, 993.0, 1080.0, 270.0),
        (300.0, 200.0, 480.0, 260.0),
    ]
    zones = [(0.0, 30.0, faces)]
    faces_only, _ = _build(
        tmp_path, monkeypatch, WORDS, hook_title=TITLE, face_zones=zones,
    )
    with_bands, _ = _build(
        tmp_path, monkeypatch, WORDS, hook_title=TITLE,
        face_zones=zones, fit_strips=[STRIP],
    )
    assert with_bands == faces_only
    _px, block_h = _hook_block(with_bands)
    bottom = _hook_bottom(with_bands)
    top = bottom - block_h
    # Face-clear (expanded boxes): below the second full-width face.
    assert top >= 1275 - 1, (top, bottom)
    # ...which is inside the bottom blur band: the documented
    # impossible-case outcome — band contact, never face contact.
    assert top >= 1263 - 1, (top, bottom)


def test_editor_styled_title_is_never_guarded(tmp_path, monkeypatch):
    # A user's own editor placement wins over every guard, bands
    # included — the title event and style are identical with and
    # without strips. (The captions in the same build DO move for
    # the strip — that is the framing lane's shipped behaviour —
    # so the comparison is scoped to the title.)
    style = {"base_px": 60, "scale": 1.0, "x": 0.0, "y": 0.0,
             "start": 0.0, "end": 10.0}
    plain, _ = _build(
        tmp_path, monkeypatch, WORDS, hook_title=TITLE,
        hook_editor_style=style,
    )
    guarded, _ = _build(
        tmp_path, monkeypatch, WORDS, hook_title=TITLE,
        hook_editor_style=style, fit_strips=[STRIP],
    )
    assert _hook_event(guarded) == _hook_event(plain)
    hook_style = [
        line for line in guarded.splitlines() if line.startswith("Style: Hook")
    ]
    assert hook_style == [
        line for line in plain.splitlines() if line.startswith("Style: Hook")
    ]


# --- Production path: plan -> collect -> build, as the renderer calls it ---


def _analysis(rows, evidence_kwargs, fps=3.0):
    return {
        "width": 1920,
        "height": 1080,
        "samples": [(i / fps, list(boxes)) for i, boxes in enumerate(rows)],
        "cuts": [],
        "context": [
            SceneEvidence(time=i / fps, **evidence_kwargs)
            for i in range(len(rows))
        ],
    }


def _planned_edl(monkeypatch, analysis):
    """An EDL planned by the real attach path (fabricated analysis)."""
    monkeypatch.setattr(
        reframe_plan, "get_source_analysis", lambda path, spans: analysis
    )
    edl = {
        "clip_id": "title-bands-wiring",
        "duration": 6.0,
        "source_ranges": [[0.0, 6.0]],
        "segments": [
            {
                "index": 0,
                "start": 0.0,
                "end": 6.0,
                "transition_in": "none",
                "transition_duration": 0,
            }
        ],
    }
    assert reframe_plan.attach_reframe_plan(
        edl, Path("source.mp4"), [(0.0, 6.0)], False
    ) is True
    return edl


def test_production_path_fit_plan_moves_title_into_strip(
    tmp_path, monkeypatch
):
    # The renderer (video_utils.create_optimized_clip) collects the
    # plan's strips and zones and hands them to the ASS builder; a
    # faceless fit plan must land the title inside the sharp strip
    # through that exact hand-off, not just when the builder is fed
    # strips by hand.
    edl = _planned_edl(
        monkeypatch,
        _analysis(
            [[]] * 18,
            dict(
                face_area=0.02, face_count=0, face_span=0.0,
                motion=0.1, outside_motion=0.5, motion_spread=0.6,
            ),
        ),
    )
    strips = reframe_plan.collect_fit_strips(edl)
    assert strips == [(0.0, 6.0, 657.0, 1263.0)]
    zones = reframe_plan.collect_face_zones(edl) or None
    ass, report = _build(
        tmp_path, monkeypatch, WORDS, hook_title=TITLE,
        face_zones=zones, fit_strips=strips,
    )
    _px, block_h = _hook_block(ass)
    bottom = _hook_bottom(ass)
    assert bottom - block_h >= 657 + 12 - 1, (bottom, block_h)
    assert bottom <= 1263 - 12 + 1, (bottom, block_h)
    assert report["fit_strip_title"]["band_boxes"] == 2


def test_production_path_crop_plan_title_matches_unwired_build(
    tmp_path, monkeypatch
):
    # A crop plan stamps no strips: collecting from it yields [], and
    # the build through the production hand-off is byte-identical to
    # building with the same face zones and no strips at all — the
    # band wiring is inert wherever the layout is not a fit.
    face = (200.0, 300.0, 180.0, 220.0)
    edl = _planned_edl(
        monkeypatch,
        _analysis(
            [[face]] * 18,
            dict(
                face_area=0.0175, face_count=1, face_span=0.094,
                face_center=0.151,
            ),
        ),
    )
    strips = reframe_plan.collect_fit_strips(edl)
    assert strips == []
    zones = reframe_plan.collect_face_zones(edl) or None
    assert zones
    wired, report = _build(
        tmp_path, monkeypatch, WORDS, hook_title=TITLE,
        face_zones=zones, fit_strips=strips,
    )
    unwired, _ = _build(
        tmp_path, monkeypatch, WORDS, hook_title=TITLE, face_zones=zones,
    )
    assert wired == unwired
    assert "fit_strip_title" not in report
