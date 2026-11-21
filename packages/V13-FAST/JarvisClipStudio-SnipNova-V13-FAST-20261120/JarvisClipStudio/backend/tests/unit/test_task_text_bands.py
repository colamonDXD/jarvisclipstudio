"""Task-level text bands (media.text_bands).

The defect (MrBeast exports, 2026-11-15): the framing lane unified
title/caption placement per CLIP, so the seven clips of one task
still rendered the hook title at four different heights — each
clip's guard decided from its own face geometry alone.

The fix under test: the bands are decided ONCE per task against the
union of all clips' dominant geometry (worst-case union rule),
stored on the task, offered to every clip's render, and reused
verbatim by re-renders/regenerations. The per-clip guard remains
the arbiter: a clip whose faces collide with the task band deviates
(with a stamp) instead of ever wearing text on a face.
"""

from __future__ import annotations

import re

import pytest

from src.caption_templates import get_template
from src.media import captions, text_bands
from src.media.face_text_guard import FACE_TEXT_MARGIN_PX, block_fits_face_free

W, H = 1080, 1920
TEMPLATE = get_template("default")

POS_RE = re.compile(r"\\pos\((\d+),(\d+)\)")


def _words(count=12, step=2.0):
    return [
        {
            "text": f"word{i}",
            "start": i * step,
            "end": i * step + 1.2,
            "confidence": 0.99,
        }
        for i in range(count)
    ]


def _zone(box, duration=24.0):
    return [(0.0, duration, [box])]


# Three clips of one task whose per-clip bands diverge: a low face
# pushes clip 1's captions up, clip 2's high face pushes its title
# down, clip 3's mid face leaves the preferences alone. A common
# face-free band exists for the union.
FACE_LOW = (390.0, 1250.0, 300.0, 360.0)
FACE_HIGH = (390.0, 300.0, 300.0, 360.0)
FACE_MID = (390.0, 1000.0, 300.0, 300.0)
# A face parked exactly where the task caption band lands.
FACE_ON_BAND = (390.0, 830.0, 300.0, 190.0)

CLIP_GEOMETRIES = [
    {"duration": 24.0, "face_zones": _zone(FACE_LOW), "fit_strips": [],
     "hook_title": "TASK TITLE"},
    {"duration": 24.0, "face_zones": _zone(FACE_HIGH), "fit_strips": [],
     "hook_title": "TASK TITLE"},
    {"duration": 24.0, "face_zones": _zone(FACE_MID), "fit_strips": [],
     "hook_title": "TASK TITLE"},
]


def _decide(clips=CLIP_GEOMETRIES, **kwargs):
    return text_bands.decide_bands_from_geometry(
        clips, template=TEMPLATE, font_size=None,
        video_width=W, video_height=H, **kwargs,
    )


def _build(tmp_path, monkeypatch, face_box, **kwargs):
    monkeypatch.setattr(captions, "emoji_rendering_supported", lambda: False)
    tmp_path.mkdir(parents=True, exist_ok=True)
    output = tmp_path / "captions.ass"
    report: dict = {}
    assert captions.build_assemblyai_ass_subtitles(
        tmp_path / "missing.mp4", 0.0, 24.0, W, H, output,
        caption_words=_words(),
        face_zones=_zone(face_box),
        hook_title="TASK TITLE",
        caption_layout_report=report,
        **kwargs,
    )
    return output.read_text(encoding="utf-8"), report


def _caption_centres(ass_text):
    centres = []
    for line in ass_text.splitlines():
        if line.startswith("Dialogue:") and ",Default," in line:
            match = POS_RE.search(line)
            assert match, line
            centres.append(int(match.group(2)))
    return centres


def _title_bottom(ass_text):
    """The rendered hook title's bottom edge, in pixels.

    Mirrors the framing lane's measurement: an unmoved title sits at
    the Hook style's MarginV plus its block height; a guard-moved
    title carries an explicit \\an2\\pos whose y IS the bottom edge.
    """
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
    moved = re.search(r"\\an2\\pos\((\d+),(\d+)\)", hook)
    if moved:
        return int(moved.group(2))
    return int(margin_v + block_h)


# ---------------------------------------------------------------------------
# The decision itself
# ---------------------------------------------------------------------------


def test_decision_engages_both_bands_for_the_union():
    bands = _decide()
    assert bands is not None
    assert bands["clips_considered"] == 3
    assert bands["caption_centre"] is not None
    assert bands["title_bottom"] is not None


def test_decision_caption_centre_clears_every_clips_faces():
    bands = _decide()
    centre = bands["caption_centre"] * H
    # The representative block the decision itself guards (the
    # module's own metrics): the widest chunk block any clip can
    # draw, centred. The exact per-clip verification with real chunk
    # dimensions happens in the builder tests below.
    font_px = captions.get_scaled_font_size(int(TEMPLATE["font_size"]), W)
    outline_px, shadow_px, style_bord, border_style = (
        text_bands._caption_style_metrics(TEMPLATE, font_px)
    )
    rep_w = captions.get_subtitle_max_width(W) + 2 * (style_bord + shadow_px + 8)
    rep_h = int(font_px * 1.5) + 2 * (
        (style_bord if border_style == 3 else outline_px) + shadow_px + 6
    )
    for clip in CLIP_GEOMETRIES:
        for _zs, _ze, boxes in clip["face_zones"]:
            for box in boxes:
                assert block_fits_face_free(
                    centre, W // 2 - rep_w / 2, rep_w, rep_h, [box], W, H
                ), f"task centre {centre} touches {box}"


def test_decision_title_bottom_clears_every_clips_faces():
    bands = _decide()
    bottom = bands["title_bottom"] * H
    block_w, block_h = captions.natural_hook_title_block(
        "TASK TITLE", TEMPLATE, W,
        captions.get_scaled_font_size(int(TEMPLATE["font_size"]), W),
    )
    for clip in CLIP_GEOMETRIES:
        for _zs, _ze, boxes in clip["face_zones"]:
            for box in boxes:
                assert block_fits_face_free(
                    bottom - block_h / 2, (W - block_w) / 2, block_w,
                    block_h, [box], W, H,
                ), f"task title bottom {bottom} touches {box}"


def test_decision_no_common_caption_band_engages_nothing():
    # Faces whose union (with the guard margin) blocks every centre
    # the guard limits allow: the caption band must NOT engage.
    clips = [
        {"duration": 24.0, "face_zones": _zone((390.0, 600.0, 300.0, 900.0)),
         "fit_strips": [], "hook_title": None},
        {"duration": 24.0, "face_zones": _zone((390.0, 100.0, 300.0, 600.0)),
         "fit_strips": [], "hook_title": None},
    ]
    bands = _decide(clips)
    assert bands is not None
    assert bands["caption_centre"] is None


def test_decision_nothing_to_unify_returns_none():
    assert _decide([]) is None
    bare = [
        {"duration": 24.0, "face_zones": [], "fit_strips": [],
         "hook_title": "TASK TITLE"}
    ]
    assert _decide(bare) is None


def test_decision_honours_the_preference():
    clear = [
        {"duration": 24.0, "face_zones": _zone((390.0, 1500.0, 300.0, 300.0)),
         "fit_strips": [], "hook_title": None}
    ]
    bands = _decide(clear, preference_y=0.52)
    # The engaged centre is the builder's own safe-position resolution
    # of the preference fraction (the same function the builder uses
    # for its per-clip preference), untouched by the distant face.
    font_px = captions.get_scaled_font_size(int(TEMPLATE["font_size"]), W)
    expected = captions.get_safe_vertical_position(
        H, int(font_px * 1.5), 0.52
    ) / H
    assert bands["caption_centre"] == pytest.approx(expected, abs=0.001)


# ---------------------------------------------------------------------------
# The builder: task bands applied, deviations stamped, legacy unchanged
# ---------------------------------------------------------------------------


def test_task_bands_unify_caption_and_title_across_clips(tmp_path, monkeypatch):
    bands = _decide()
    centres = []
    bottoms = []
    for index, box in enumerate((FACE_LOW, FACE_HIGH, FACE_MID)):
        ass, report = _build(
            tmp_path / str(index), monkeypatch, box,
            task_caption_centre=bands["caption_centre"],
            task_title_bottom=bands["title_bottom"],
        )
        band = report["caption_band"]
        assert band["source"] == "task"
        assert band["task_deviated"] is False
        # Same centre in pixels (the stored fraction is rounded to
        # 4dp, so the re-derived stamp can differ in the last digit).
        assert band["centre"] * H == pytest.approx(
            bands["caption_centre"] * H, abs=1
        )
        assert report["face_guard_title"]["task_bottom"] == bands["title_bottom"]
        assert report["face_guard_title"]["task_deviated"] is False
        centres.append(set(_caption_centres(ass)))
        bottoms.append(_title_bottom(ass))
    # Every chunk of every clip sits at the one task centre, and every
    # title bottom is identical across the task.
    assert centres[0] == centres[1] == centres[2]
    assert len(centres[0]) == 1
    assert bottoms[0] == bottoms[1] == bottoms[2]


def test_per_clip_bands_diverge_without_task_bands(tmp_path, monkeypatch):
    centres = []
    bottoms = []
    for index, box in enumerate((FACE_LOW, FACE_HIGH, FACE_MID)):
        ass, report = _build(tmp_path / str(index), monkeypatch, box)
        assert report["caption_band"]["source"] == "clip"
        assert "task_centre" not in report["caption_band"]
        assert "task_bottom" not in report["face_guard_title"]
        centres.append(set(_caption_centres(ass)))
        bottoms.append(_title_bottom(ass))
    # The pre-fix behaviour the lane removes: clip 1's captions are
    # pushed off the shared preference and clip 2's title is pushed
    # down — the task's clips disagree with each other.
    assert centres[0] != centres[1]
    assert bottoms[1] != bottoms[0]


def test_clip_whose_face_collides_with_task_band_deviates(tmp_path, monkeypatch):
    bands = _decide()
    ass, report = _build(
        tmp_path, monkeypatch, FACE_ON_BAND,
        task_caption_centre=bands["caption_centre"],
        task_title_bottom=bands["title_bottom"],
    )
    band = report["caption_band"]
    assert band["task_deviated"] is True
    assert band["centre"] != bands["caption_centre"]
    # The deviating clip's captions still clear its face: no chunk
    # block (centre ± half height, margin-expanded face) overlaps it.
    style = next(
        line for line in ass.splitlines() if line.startswith("Style: Default")
    )
    fields = style.split(",")
    font_px = int(fields[2])
    half = font_px * 1.5 / 2
    fx, fy, fw, fh = FACE_ON_BAND
    for centre in _caption_centres(ass):
        assert centre + half <= fy - FACE_TEXT_MARGIN_PX or (
            centre - half >= fy + fh + FACE_TEXT_MARGIN_PX
        ), f"caption at {centre} overlaps the face {FACE_ON_BAND}"


def test_title_deviation_is_stamped_when_the_clip_guard_moves_it(
    tmp_path, monkeypatch
):
    bands = _decide()
    # A face covering the whole top band incl. the task title bottom:
    # this clip's own title guard must move the title, stamped.
    ass, report = _build(
        tmp_path, monkeypatch, (390.0, 0.0, 300.0, 700.0),
        task_caption_centre=bands["caption_centre"],
        task_title_bottom=bands["title_bottom"],
    )
    assert report["face_guard_title"]["task_bottom"] == bands["title_bottom"]
    assert report["face_guard_title"]["task_deviated"] is True


def test_rerender_with_stored_bands_is_byte_identical(tmp_path, monkeypatch):
    bands = _decide()
    kwargs = {
        "task_caption_centre": bands["caption_centre"],
        "task_title_bottom": bands["title_bottom"],
    }
    first, _ = _build(tmp_path / "a", monkeypatch, FACE_MID, **kwargs)
    # A later single-clip re-render of the same clip, handed the same
    # stored bands, produces the identical subtitle file.
    second, _ = _build(tmp_path / "b", monkeypatch, FACE_MID, **kwargs)
    assert first == second


# ---------------------------------------------------------------------------
# Persistence: re-render seams read the stored decision, never re-decide
# ---------------------------------------------------------------------------


class _FakeTaskRepo:
    def __init__(self, metadata):
        self._metadata = metadata

    async def load_source_metadata(self, db, task_id):
        return dict(self._metadata)


@pytest.mark.asyncio
async def test_load_task_text_bands_reads_stored_decision():
    from src.services.clip_service import ClipEditingMixin

    bands = _decide()
    svc = ClipEditingMixin.__new__(ClipEditingMixin)
    svc.db = object()
    svc.task_repo = _FakeTaskRepo({"text_bands": bands, "other": 1})
    assert await svc._load_task_text_bands("task-1") == bands


@pytest.mark.asyncio
async def test_load_task_text_bands_legacy_task_has_none():
    from src.services.clip_service import ClipEditingMixin

    svc = ClipEditingMixin.__new__(ClipEditingMixin)
    svc.db = object()
    svc.task_repo = _FakeTaskRepo({"output_format": "vertical"})
    assert await svc._load_task_text_bands("task-1") is None
    svc.task_repo = _FakeTaskRepo({"text_bands": {}})
    assert await svc._load_task_text_bands("task-1") is None
    svc.db = None
    assert await svc._load_task_text_bands("task-1") is None
