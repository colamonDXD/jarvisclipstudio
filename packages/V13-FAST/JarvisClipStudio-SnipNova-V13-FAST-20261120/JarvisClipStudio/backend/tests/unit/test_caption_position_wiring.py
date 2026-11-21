"""Caption position wiring — editor audit D1/D2 regression proof.

The EDL captions block's ``position`` / ``position_y`` (and its
``words_per_line``) were stamped by every builder — the niche
profiles via ``build_edl``, the Caption Studio and the per-clip AI
editor via the re-render seam — and read by none: the render's
caption height came from the template or the layout plan alone, the
explicit ``position_y`` was dropped inside ``rerender_clip`` before
it even reached the EDL, and the AI editor's summary claimed moves
that never happened.

The plan round-trip tests that let this survive pinned storage, not
rendering — so the decisive tests here are RENDER-level: real
``create_optimized_clip`` renders whose finished frames and layout
reports are measured, not plan dicts asserted.
"""

from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
from pathlib import Path

import numpy as np
import pytest

from src.auto_director.clip_edits import (
    build_edit_plan,
    caption_overrides_from_plan,
    normalize_caption_overrides,
)
from src.auto_director.edl import build_edl, load_profile
from src.services.clip_service import ClipEditingMixin
from src.video_utils import (
    caption_template_override,
    create_optimized_clip,
    edl_caption_position_request,
)

FFMPEG = shutil.which("ffmpeg") is not None


# ---------------------------------------------------------------------------
# The resolver: names, numerics, explicitness, defensive fall-through
# ---------------------------------------------------------------------------


def _request(captions):
    return edl_caption_position_request({"captions": captions})


def test_resolver_named_positions_use_the_overlay_presets():
    assert _request({"position": "top"}) == (0.18, False)
    assert _request({"position": "middle"}) == (0.52, False)
    # "center" is the EDL spelling the profiles use for the middle preset.
    assert _request({"position": "center"}) == (0.52, False)
    assert _request({"position": "bottom"}) == (0.78, False)


def test_resolver_numeric_position_y_is_explicit():
    assert _request({"position_y": 0.3}) == (0.3, True)
    assert _request({"position_y": 0.85}) == (0.85, True)
    # The numeric choice wins over a conflicting name (it is the more
    # specific instruction), exactly as the override validation treats it.
    assert _request({"position": "bottom", "position_y": 0.2}) == (0.2, True)


def test_resolver_reference_look_position_y_refines_named_position():
    # F-A: a Reference Style look measures a numeric caption height
    # (e.g. 0.66) and stamps it on the EDL as
    # reference_look_override.position_y — but the resolver never read
    # it, so the render always used the coarse named band (bottom ->
    # 0.78) and the measured value was dead. The look refines the
    # named tier: it beats the band it was measured from, because it
    # is the finer form of the same style choice — but it is a
    # style-level measurement, not a deliberate per-clip choice, so it
    # reports explicit=False: a per-clip explicit position_y and the
    # face-aware layout plan still outrank it, exactly as they outrank
    # the named band today.
    edl = {
        "captions": {"position": "bottom"},
        "reference_look_override": {"position_y": 0.66},
    }
    assert edl_caption_position_request(edl) == (0.66, False)


def test_resolver_reference_look_yields_to_explicit_position_y():
    edl = {
        "captions": {"position": "bottom", "position_y": 0.2},
        "reference_look_override": {"position_y": 0.66},
    }
    assert edl_caption_position_request(edl) == (0.2, True)


def test_resolver_reference_look_without_named_position():
    # No band to refine: the look alone is the request.
    edl = {
        "captions": {},
        "reference_look_override": {"position_y": 0.66},
    }
    assert edl_caption_position_request(edl) == (0.66, False)


def test_resolver_unusable_look_position_y_falls_through_to_named():
    base = {"captions": {"position": "top"}}
    for bad in ("high", True, float("nan"), 0.05, 0.95, None):
        edl = {**base, "reference_look_override": {"position_y": bad}}
        assert edl_caption_position_request(edl) == (0.18, False)
    # A missing, empty or non-dict look block is no look at all.
    for look in (None, {}, "nope", []):
        edl = {**base, "reference_look_override": look}
        assert edl_caption_position_request(edl) == (0.18, False)
    # An unusable look never masks a usable explicit value either.
    edl = {
        "captions": {"position_y": 0.3},
        "reference_look_override": {"position_y": "high"},
    }
    assert edl_caption_position_request(edl) == (0.3, True)


def test_resolver_look_without_captions_block_is_not_a_request():
    # The resolver only reads the look alongside a captions block: a
    # look with no captions block at all is a malformed EDL, and the
    # value is not lost — caption_template_override still merges it
    # into the template, which the caption builder uses when no
    # position_y_override is passed.
    edl = {"reference_look_override": {"position_y": 0.66}}
    assert edl_caption_position_request(edl) == (None, False)
    assert (
        edl_caption_position_request(
            {"captions": "nope",
             "reference_look_override": {"position_y": 0.66}}
        )
        == (None, False)
    )


def test_resolver_unusable_values_fall_through_never_raise():
    assert _request({}) == (None, False)
    assert _request({"position": "somewhere"}) == (None, False)
    assert _request({"position_y": "high"}) == (None, False)
    assert _request({"position_y": True}) == (None, False)
    assert _request({"position_y": float("nan")}) == (None, False)
    # Out-of-band numerics are ignored, not clamped into a move the
    # EDL never asked for; a valid name behind one still resolves.
    assert _request({"position_y": 0.05}) == (None, False)
    assert _request({"position_y": 0.95, "position": "top"}) == (0.18, False)
    assert edl_caption_position_request(None) == (None, False)
    assert edl_caption_position_request({}) == (None, False)
    assert edl_caption_position_request({"captions": "nope"}) == (None, False)


# ---------------------------------------------------------------------------
# The re-render seam: position_y reaches the EDL (D2), names resolve (D1)
# ---------------------------------------------------------------------------

_CLIP = {"id": "probe", "text": "hello world this is a probe clip"}


def _rerender_edl(overrides):
    return ClipEditingMixin._build_rerender_edl(
        _CLIP, 0.0, 30.0, "podcast_news", None, None, overrides, None, None, None
    )


def test_rerender_edl_carries_explicit_position_y():
    # The audit's probe B, inverted: these two EDLs used to be identical.
    low = _rerender_edl({"position_y": 0.15})
    high = _rerender_edl({"position_y": 0.80})
    assert low["captions"]["position_y"] == 0.15
    assert high["captions"]["position_y"] == 0.80
    assert low != high
    assert edl_caption_position_request(low) == (0.15, True)
    assert edl_caption_position_request(high) == (0.80, True)


def test_rerender_edl_resolves_named_studio_choice():
    edl = _rerender_edl({"position": "top"})
    assert edl["captions"]["position"] == "top"
    # Resolved at the seam so the render can tell a deliberate choice
    # apart from the profile's own default position.
    assert edl["captions"]["position_y"] == 0.18
    assert edl_caption_position_request(edl) == (0.18, True)


def test_rerender_edl_without_overrides_is_unchanged():
    # Default/no-position pin: no override -> no position_y stamp, and
    # the captions block is exactly what build_edl produced before.
    edl = _rerender_edl(None)
    assert "position_y" not in edl["captions"]
    assert edl["captions"]["position"] == "bottom"  # podcast_news default
    assert edl_caption_position_request(edl) == (0.78, False)


def test_ai_edit_overrides_reach_the_render_request():
    # The per-clip AI editor's flow end to end, up to the render seam:
    # its payload is normalised, stored on the plan, read back as
    # overrides and stamped onto the EDL — and now the request the
    # render resolves is the one its summary reports ("height set to
    # 0.3" / "moved to the top" are true statements after this lane).
    for payload, expected in (
        ({"position_y": 0.3}, (0.3, True)),
        ({"position": "top"}, (0.18, True)),
    ):
        overrides = normalize_caption_overrides(payload)
        plan = build_edit_plan(
            [{"index": 0, "start": 0.0, "end": 30.0,
              "transition_in": "none", "transition_duration": 0.0}],
            captions=overrides,
        )
        read_back = caption_overrides_from_plan(plan)
        edl = _rerender_edl(read_back)
        assert edl_caption_position_request(edl) == expected


# ---------------------------------------------------------------------------
# words_per_line: the same EDL block's grouping field gets its consumer
# ---------------------------------------------------------------------------


def test_words_per_line_fills_template_grouping():
    merged = caption_template_override(
        {"captions": {"words_per_line": 2}}, "default"
    )
    assert merged is not None
    assert merged["max_words_per_line"] == 2
    # Everything else is the stock template, untouched.
    from src.caption_templates import get_template

    stock = get_template("default")
    assert merged["font_family"] == stock["font_family"]
    assert merged["position_y"] == stock["position_y"]


def test_words_per_line_yields_to_look_and_brand():
    look = {"max_words_per_line": 5}
    merged = caption_template_override(
        {"captions": {"words_per_line": 2}, "reference_look_override": look},
        "default",
    )
    assert merged["max_words_per_line"] == 5  # measured look beats profile
    brand = {"max_words_per_line": 6}
    merged = caption_template_override(
        {"captions": {"words_per_line": 2}, "brand_template_override": brand},
        "default",
    )
    assert merged["max_words_per_line"] == 6


def test_words_per_line_absent_or_unusable_keeps_none_semantics():
    assert caption_template_override({}, "default") is None
    assert caption_template_override(None, "default") is None
    assert caption_template_override({"captions": {}}, "default") is None
    assert (
        caption_template_override({"captions": {"words_per_line": "x"}}, "default")
        is None
    )
    assert (
        caption_template_override({"captions": {"words_per_line": 0}}, "default")
        is None
    )
    assert (
        caption_template_override({"captions": {"words_per_line": True}}, "default")
        is None
    )


def test_rerender_edl_carries_user_words_per_line():
    # N4: a words-per-line the user sets beats the profile default
    # build_edl stamped, and the grouping the render resolves is the
    # one the user set.
    edl = _rerender_edl({"words_per_line": 2})
    assert edl["captions"]["words_per_line"] == 2
    merged = caption_template_override(edl, "default")
    assert merged is not None
    assert merged["max_words_per_line"] == 2


def test_rerender_edl_without_words_per_line_keeps_profile_default():
    # No choice set: the profile default stands, exactly as before.
    profile = load_profile("podcast_news")
    expected = (profile.get("captions") or {}).get("words_per_line", 3)
    edl = _rerender_edl(None)
    assert edl["captions"]["words_per_line"] == expected


def test_quick_editor_words_per_line_reaches_the_render():
    # The quick editor's flow end to end, up to the render seam: the
    # value is normalised, stored on the plan, read back as overrides
    # and stamped onto the EDL, where the template override fills the
    # grouping the caption builder renders.
    overrides = normalize_caption_overrides({"words_per_line": 2})
    plan = build_edit_plan(
        [{"index": 0, "start": 0.0, "end": 30.0,
          "transition_in": "none", "transition_duration": 0.0}],
        captions=overrides,
    )
    read_back = caption_overrides_from_plan(plan)
    assert read_back == {"words_per_line": 2}
    edl = _rerender_edl(read_back)
    assert edl["captions"]["words_per_line"] == 2
    merged = caption_template_override(edl, "default")
    assert merged is not None
    assert merged["max_words_per_line"] == 2


# ---------------------------------------------------------------------------
# RENDER level: real create_optimized_clip renders, measured frames
# ---------------------------------------------------------------------------

_WORDS_4S = ["alpha", "bravo", "charlie", "delta"]
_WORDS_6S = ["alpha", "bravo", "charlie", "delta", "echo", "foxtrot"]


def _make_source(folder: Path, words, seconds: float, size="720x1280") -> Path:
    source = folder / "src.mp4"
    subprocess.run(
        [
            "ffmpeg", "-v", "error", "-y",
            "-f", "lavfi", "-i",
            f"color=c=0x202020:size={size}:rate=30:duration={int(seconds)}",
            "-f", "lavfi", "-i", f"sine=frequency=440:duration={int(seconds)}",
            "-c:v", "libx264", "-preset", "veryfast", "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-shortest", str(source),
        ],
        check=True, capture_output=True,
    )
    per_word = seconds / len(words)
    cache = {
        "words": [
            {
                "text": text,
                "start": int(i * per_word * 1000),
                "end": int((i + 1) * per_word * 1000) - 100,
                "confidence": 0.99,
            }
            for i, text in enumerate(words)
        ],
        "utterances": [],
        "text": " ".join(words),
        "language_code": "en",
    }
    source.with_suffix(".transcript_cache.json").write_text(
        json.dumps(cache), encoding="utf-8"
    )
    return source


def _edl(seconds: float, captions=None):
    edl = {
        "clip_id": "position-wiring",
        "duration": seconds,
        "segments": [
            {
                "index": 0,
                "start": 0.0,
                "end": seconds,
                "transition_in": "none",
                "transition_duration": 0,
            }
        ],
    }
    if captions is not None:
        edl["captions"] = captions
    return edl


def _render(source: Path, edl, out: Path):
    report: dict = {}
    ok = create_optimized_clip(
        source, 0.0, float(edl["duration"]), out,
        add_subtitles=True,
        caption_template="default",
        output_format="vertical",
        keep_ranges=[(0.0, float(edl["duration"]))],
        extend_to_sentence=False,
        layout_report=report,
        edl=edl,
    )
    assert ok, f"render failed for {out.name}"
    return report


def _frame_rgb(video: Path, t: float, width=1080, height=1920) -> np.ndarray:
    raw = subprocess.run(
        [
            "ffmpeg", "-v", "error", "-ss", str(t), "-i", str(video),
            "-frames:v", "1", "-f", "rawvideo", "-pix_fmt", "rgb24", "-",
        ],
        capture_output=True, check=True,
    ).stdout
    return np.frombuffer(raw, dtype=np.uint8).reshape(height, width, 3).astype(np.int32)


def _text_box(img: np.ndarray):
    """Bounding box (x, y, w, h) of the bright caption glyphs."""
    bright = (np.abs(img - 255).sum(axis=2) < 120)
    ys, xs = np.nonzero(bright)
    if len(xs) == 0:
        return None
    return (
        float(xs.min()), float(ys.min()),
        float(xs.max() - xs.min() + 1), float(ys.max() - ys.min() + 1),
    )


@pytest.fixture(scope="module")
def renders(tmp_path_factory):
    if not FFMPEG:
        pytest.skip("ffmpeg not available")
    folder = tmp_path_factory.mktemp("capos")
    source = _make_source(folder, _WORDS_4S, 4.0)
    out = {}
    variants = {
        "top": _edl(4.0, {"position": "top"}),
        "bottom": _edl(4.0, {"position": "bottom"}),
        "y015": _edl(4.0, {"position_y": 0.15}),
        "y080": _edl(4.0, {"position_y": 0.80}),
        "no_block": _edl(4.0),
        "preset_only": _edl(4.0, {"preset_id": "default"}),
    }
    for name, edl in variants.items():
        path = folder / f"{name}.mp4"
        out[name] = (path, _render(source, edl, path))
    # The captions blocks build_edl actually stamps for two profiles
    # whose authored positions differ (bottom vs center), carried
    # verbatim onto the minimal renderable EDL.
    words = [
        {"word": text, "start": float(i), "end": float(i) + 0.5}
        for i, text in enumerate(_WORDS_4S)
    ]
    clip = {"id": "c1", "start": 0, "end": 4, "duration": 4}
    for name, niche in (("profile_bottom", "podcast_news"),
                        ("profile_center", "anime_funk_edit")):
        full = build_edl(clip, words, load_profile(niche), [])
        edl = _edl(4.0, full["captions"])
        path = folder / f"{name}.mp4"
        out[name] = (path, _render(source, edl, path))
    return out


def test_render_top_vs_bottom_moves_the_caption_band(renders):
    top_path, top_report = renders["top"]
    bottom_path, bottom_report = renders["bottom"]
    # The layout report the render itself produced: caption centre as
    # a fraction of frame height, from the actual ASS build.
    assert top_report["caption_y"] == pytest.approx(0.18, abs=0.04)
    assert bottom_report["caption_y"] == pytest.approx(0.78, abs=0.04)
    # And the finished pixels agree: the caption glyph band sits in
    # the top fifth for "top" and the bottom quarter for "bottom".
    top_box = _text_box(_frame_rgb(top_path, 1.0))
    bottom_box = _text_box(_frame_rgb(bottom_path, 1.0))
    assert top_box is not None and bottom_box is not None
    top_centre = top_box[1] + top_box[3] / 2
    bottom_centre = bottom_box[1] + bottom_box[3] / 2
    assert top_centre < 0.35 * 1920
    assert bottom_centre > 0.60 * 1920
    assert bottom_centre - top_centre > 500
    assert top_path.read_bytes() != bottom_path.read_bytes()


def test_render_explicit_position_y_moves_the_caption_band(renders):
    _low_path, low_report = renders["y015"]
    _high_path, high_report = renders["y080"]
    assert low_report["caption_y"] == pytest.approx(0.15, abs=0.04)
    assert high_report["caption_y"] == pytest.approx(0.80, abs=0.04)
    assert high_report["caption_y"] - low_report["caption_y"] > 0.4


def test_render_profile_positions_now_take_effect(renders):
    _bp, bottom_report = renders["profile_bottom"]
    _cp, center_report = renders["profile_center"]
    assert bottom_report["caption_y"] == pytest.approx(0.78, abs=0.04)
    assert center_report["caption_y"] == pytest.approx(0.52, abs=0.04)


def test_render_without_position_is_byte_identical_to_before(renders):
    # Default pin: an EDL whose captions block carries no position
    # request renders exactly the file the pre-fix code produced —
    # proven by the no-block render, whose code path is unchanged.
    no_block_path, no_block_report = renders["no_block"]
    preset_path, preset_report = renders["preset_only"]
    assert hashlib.sha256(no_block_path.read_bytes()).hexdigest() == (
        hashlib.sha256(preset_path.read_bytes()).hexdigest()
    )
    assert no_block_report["caption_y"] == preset_report["caption_y"]


# ---------------------------------------------------------------------------
# RENDER level: the face guard still outranks an explicit request
# ---------------------------------------------------------------------------


def _one_face_analysis():
    from src.media.scene_context import SceneEvidence

    rows = [[(200.0, 300.0, 180.0, 220.0)]] * 18
    return {
        "width": 1920,
        "height": 1080,
        "samples": [(i / 3.0, list(boxes)) for i, boxes in enumerate(rows)],
        "cuts": [],
        "context": [
            SceneEvidence(
                time=i / 3.0, face_area=0.0175, face_count=1,
                face_span=0.094, face_center=0.151,
            )
            for i in range(len(rows))
        ],
    }


@pytest.mark.skipif(not FFMPEG, reason="ffmpeg not available")
def test_render_face_guard_still_overrides_explicit_position(tmp_path, monkeypatch):
    from src.media import reframe_plan
    from src.media.face_text_guard import intersection_area
    from src.media.reframe_plan import collect_face_zones

    source = _make_source(tmp_path, _WORDS_6S, 6.0, size="1920x1080")
    captions = {"position_y": 0.5}  # deliberate centre request

    # Control: no face zones anywhere near — the request is honoured.
    plain_path = tmp_path / "plain.mp4"
    plain_report = _render(source, _edl(6.0, dict(captions)), plain_path)
    assert "face_guard_captions" not in plain_report
    plain_box = _text_box(_frame_rgb(plain_path, 1.0))
    assert plain_box is not None
    plain_centre = plain_box[1] + plain_box[3] / 2
    assert plain_centre == pytest.approx(0.5 * 1920, abs=0.08 * 1920)

    # Guarded: the production attach path stamps the plan's face
    # zones onto the same EDL; the request now collides with a face
    # and the guard — not the request — has the final say.
    edl = _edl(6.0, dict(captions))
    original = reframe_plan.get_source_analysis
    monkeypatch.setattr(
        reframe_plan, "get_source_analysis",
        lambda path, spans: _one_face_analysis(),
    )
    try:
        attached = reframe_plan.attach_reframe_plan(
            edl, source, [(0.0, 6.0)], False
        )
    finally:
        monkeypatch.setattr(reframe_plan, "get_source_analysis", original)
    assert attached is True
    zones = collect_face_zones(edl)
    assert zones, "the attach must stamp face zones for this proof"

    guarded_path = tmp_path / "guarded.mp4"
    guarded_report = _render(source, edl, guarded_path)
    assert guarded_report["face_guard_captions"]["moves"] >= 1
    guarded_box = _text_box(_frame_rgb(guarded_path, 1.0))
    assert guarded_box is not None
    faces = [box for start, end, boxes in zones if start <= 1.0 < end
             for box in boxes]
    assert faces
    for face in faces:
        assert intersection_area(guarded_box, face) == 0
