"""Tests for the Reference Style caption-look pass (Stage 1).

Two layers:

* Pure unit tests for the look -> template-override mapping, the
  render-path merge precedence (task > Brand Kit > reference look >
  stock preset), the EDL stamp and the Plain-mode strip. These need
  no ffmpeg and no tesseract.
* Planted fixtures: clips rendered with KNOWN caption looks using the
  app's own caption renderer, analysed end to end. The recovered look
  must match the planted values (colours within a few counts per
  channel - the video codec, not the measurement, is the noise;
  words-per-line and casing exact; outline presence exact). The
  words-per-line expectation is read back from the generated ASS
  (the renderer's own chunking is the ground truth for what was on
  screen). OCR-dependent assertions skip when the tesseract binary
  is unavailable; the pixel-field assertions always run.
"""

import copy
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from src.auto_director import reference_style as rs
from src.auto_director.edl import build_edl, load_profile
from src.auto_director.reference_style import (
    _black_box_alpha,
    _caption_look_override,
    _font_size_class,
    _kmeans_rgb,
    analyse_reference,
    build_profile,
)
from src.auto_director.template_mode import plainify_edl
from src.caption_templates import CAPTION_TEMPLATES, get_template
from src.video_utils import (
    brand_template_override,
    caption_template_override,
    edl_caption_position_request,
)

TESSERACT = shutil.which("tesseract") is not None
FFMPEG = shutil.which("ffmpeg") is not None


# ---------------------------------------------------------------------------
# Fabricated inputs
# ---------------------------------------------------------------------------

def _measurements(look_marker="absent"):
    """The fabricated-measurements shape the reference-style mapping
    tests use (mirrors tests/unit/test_reference_style.py)."""
    captions = {
        "frame_fraction": 0.9, "position": "lower",
        "from_start": True, "sampled_frames": 90,
    }
    if look_marker != "absent":
        captions["look"] = look_marker
    return {
        "version": 1,
        "source": {"file_name": "fabricated.mp4", "duration_sec": 30.0,
                   "width": 480, "height": 854, "fps": 25.0, "has_audio": True},
        "cuts": {"cut_count": 5, "cuts_per_min": 15.0,
                 "shot_sec": {"mean": 4.0, "median": 4.0, "p10": 2.0, "p90": 6.0},
                 "first_cut_sec": None, "method": "reframing"},
        "captions": captions,
        "colour": {"mean_saturation": 0.2, "mean_brightness": 0.5,
                   "luma_contrast": 20.0, "mean_hue_deg": None},
        "audio": None,
        "hook": {"opens_on_face_closeup": False, "face_in_first_3s": False,
                 "face_frame_fraction": 0.0, "loud_open": False,
                 "caption_from_start": True, "first_cut_sec": None},
    }


FAB_LOOK = {
    "position_y": {"value": 0.71, "n": 9},
    "font_color": {"value": "#FFFFFF", "n": 9},
    "highlight_color": {"value": "#00FF66", "n": 7},
    "words_per_line": {"value": 3, "n": 8},
    "uppercase": {"value": True, "n": 8},
    "font_size": {"value": 32, "n": 9},
    "stroke": {"present": True, "color": "#000000", "width_bucket": 5, "n": 9},
    "background": {"present": True, "color": "#00000099", "n": 9},
    "shadow": {"value": True, "n": 7},
}

FAB_OVERRIDE = {
    "position_y": 0.71,
    "font_color": "#FFFFFF",
    "highlight_color": "#00FF66",
    "max_words_per_line": 3,
    "uppercase": True,
    "stroke_color": "#000000",
    "stroke_width": 5,
    "shadow": True,
    "background": True,
    "background_color": "#00000099",
    "font_size": 32,
}


# ---------------------------------------------------------------------------
# Look -> override mapping
# ---------------------------------------------------------------------------

def test_look_override_mapping_full():
    assert _caption_look_override(FAB_LOOK) == FAB_OVERRIDE


def test_look_override_stamps_measured_absences():
    look = {
        "stroke": {"present": False, "color": None, "width_bucket": 0, "n": 8},
        "background": {"present": False, "color": None, "n": 8},
    }
    override = _caption_look_override(look)
    # A measured-absent outline/box is part of the look: it stamps the
    # off state so a stock preset's box/outline does not leak back in.
    assert override == {
        "stroke_color": None, "stroke_width": 0,
        "background": False, "background_color": None,
    }
    # Shadow absence is NOT provable at this grade: no key at all.
    assert "shadow" not in override


def test_look_override_none_cases():
    assert _caption_look_override(None) is None
    assert _caption_look_override("nope") is None
    assert _caption_look_override({}) is None


# ---------------------------------------------------------------------------
# Measurement helpers
# ---------------------------------------------------------------------------

def test_kmeans_rgb_recovers_two_colours():
    rng = np.random.default_rng(7)
    whites = rng.normal((250, 250, 250), 4, (600, 3))
    blacks = rng.normal((8, 8, 8), 3, (400, 3))
    clusters = _kmeans_rgb(np.concatenate([whites, blacks]), 2)
    assert len(clusters) == 2
    (c0, share0, _), (c1, share1, _) = clusters  # sorted by share desc
    assert np.allclose(c0, (250, 250, 250), atol=8)
    assert np.allclose(c1, (8, 8, 8), atol=8)
    assert share0 == pytest.approx(0.6, abs=0.05)
    assert share1 == pytest.approx(0.4, abs=0.05)


def test_black_box_alpha_recovers_opacity():
    scene = np.array([200.0, 180.0, 160.0])
    box = scene * 0.4  # a black box at 60% opacity over the scene
    assert _black_box_alpha(box, scene) == pytest.approx(0.6, abs=0.04)
    # A coloured box is not a black box: no consistent keep ratio.
    assert _black_box_alpha(np.array([200.0, 50.0, 50.0]),
                            np.array([100.0, 100.0, 100.0])) is None


def test_font_size_class_buckets():
    for size in (26, 32, 40):
        ratio = size * rs._LOOK_GLYPH_EXTENT_PER_FONT_SIZE / 560.0
        assert _font_size_class(ratio) == size


# ---------------------------------------------------------------------------
# build_profile: stamping + byte-identity when there is no look
# ---------------------------------------------------------------------------

def test_build_profile_stamps_look_override():
    profile, report = build_profile(_measurements(FAB_LOOK))
    assert profile["captions"]["look_override"] == FAB_OVERRIDE
    assert "caption_look" in report["inferred"]
    summary = profile["reference_style"]["caption_look"]
    assert summary["font_color"] == "#FFFFFF"
    assert summary["stroke"] == "#000000 width 5"
    assert summary["background"] == "#00000099"
    assert summary["shadow"] is True


def test_build_profile_without_look_is_byte_identical():
    plain_profile, plain_report = build_profile(_measurements())
    none_profile, none_report = build_profile(_measurements(None))
    assert json.dumps(plain_profile, sort_keys=True) == \
        json.dumps(none_profile, sort_keys=True)
    assert json.dumps(plain_report, sort_keys=True) == \
        json.dumps(none_report, sort_keys=True)
    assert "look_override" not in plain_profile["captions"]
    assert "caption_look" not in plain_profile["reference_style"]
    assert any(
        entry.startswith("caption_look") for entry in plain_report["not_inferred"]
    )


# ---------------------------------------------------------------------------
# EDL stamp, render-path precedence, Plain strip
# ---------------------------------------------------------------------------

_CLIP = {"id": "clip1", "start": 0, "end": 10, "duration": 10}
_WORDS = [
    {"word": "hello", "start": 0.5, "end": 0.9},
    {"word": "wide", "start": 1.0, "end": 1.4},
    {"word": "world", "start": 1.5, "end": 2.0},
]


def _profile_with_look():
    profile = load_profile("podcast_news")
    profile["captions"]["look_override"] = dict(FAB_OVERRIDE)
    return profile


def test_build_edl_carries_look_override():
    edl = build_edl(_CLIP, _WORDS, _profile_with_look(), [])
    assert edl["reference_look_override"] == FAB_OVERRIDE


def test_build_edl_without_look_stamps_nothing():
    edl = build_edl(_CLIP, _WORDS, load_profile("podcast_news"), [])
    assert "reference_look_override" not in edl


def test_render_merge_precedence():
    stock = get_template("hormozi")
    look = {"font_color": "#FFE000", "position_y": 0.66, "max_words_per_line": 5}
    brand = {"font_color": "#123456"}

    both = caption_template_override(
        {"reference_look_override": look, "brand_template_override": brand},
        "hormozi",
    )
    assert both["font_color"] == "#123456"      # Brand Kit beats the look
    assert both["position_y"] == 0.66           # look-only fields survive
    assert both["max_words_per_line"] == 5
    assert both["animation"] == stock["animation"]  # stock fills the rest

    look_only = caption_template_override(
        {"reference_look_override": look}, "hormozi"
    )
    assert look_only["font_color"] == "#FFE000"  # look beats the stock preset
    assert look_only["position_y"] == 0.66

    brand_only = caption_template_override(
        {"brand_template_override": brand}, "hormozi"
    )
    assert brand_only == brand_template_override(
        {"brand_template_override": brand}, "hormozi"
    )

    assert caption_template_override({}, "hormozi") is None
    assert caption_template_override(None, "hormozi") is None
    assert caption_template_override({"reference_look_override": {}}, "hormozi") is None


def test_plainify_strips_look_override():
    edl = build_edl(_CLIP, _WORDS, _profile_with_look(), [])
    assert "reference_look_override" in edl
    plainify_edl(edl)
    assert "reference_look_override" not in edl


def test_build_edl_look_position_y_reaches_the_render_request():
    # F-A end to end at the seam (no render): the profile's measured
    # look stamps reference_look_override on the EDL, and the render
    # request now resolves to the measured height (0.71) instead of
    # the coarse named band the profile also carries (bottom -> 0.78).
    edl = build_edl(_CLIP, _WORDS, _profile_with_look(), [])
    assert edl["captions"]["position"] == "bottom"  # podcast_news band
    assert edl["reference_look_override"]["position_y"] == 0.71
    assert edl_caption_position_request(edl) == (0.71, False)


def test_plainified_edl_render_request_falls_back_to_named_band():
    # Plain — just cuts strips the look (plainify_edl), so the
    # request is the named band again — the fix changes nothing
    # about Plain behaviour.
    edl = build_edl(_CLIP, _WORDS, _profile_with_look(), [])
    plainify_edl(edl)
    assert "reference_look_override" not in edl
    assert edl_caption_position_request(edl) == (0.78, False)


# ---------------------------------------------------------------------------
# Renderer proof: the merged template's float position_y actually
# reaches the caption layout (the brief's named gap (a))
# ---------------------------------------------------------------------------

@pytest.mark.skipif(not FFMPEG, reason="ffmpeg not available")
def test_renderer_position_y_reaches_layout_from_override(tmp_path, monkeypatch):
    from src.media import captions

    monkeypatch.setattr(captions, "emoji_rendering_supported", lambda: False)
    src = tmp_path / "src.mp4"
    subprocess.run(
        ["ffmpeg", "-y", "-v", "error", "-f", "lavfi",
         "-i", "color=c=0x4A5A6A:size=720x1280:rate=25:duration=2",
         "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", src],
        check=True, capture_output=True,
    )
    words = [
        {"text": "hello", "start": 0.1, "end": 0.5},
        {"text": "world", "start": 0.6, "end": 1.0},
    ]

    def build(name, **kwargs):
        report: dict = {}
        ok = captions.build_assemblyai_ass_subtitles(
            src, 0.0, 2.0, 720, 1280, tmp_path / name,
            caption_template="default", caption_words=words,
            caption_layout_report=report, **kwargs,
        )
        assert ok
        return report["caption_y"]

    base = build("base.ass")
    assert base == pytest.approx(0.78, abs=0.03)  # stock 0.80, safe-area clamped
    # The override the render path actually hands the builder: the
    # full merged template built from the EDL's look block.
    merged = caption_template_override(
        {"reference_look_override": {"position_y": 0.62}}, "default"
    )
    moved = build("moved.ass", template_override=merged)
    assert moved == pytest.approx(0.62, abs=0.03)  # the look moved it
    explicit = build(
        "explicit.ass",
        template_override=merged,
        position_y_override=0.70,
    )
    assert explicit == pytest.approx(0.70, abs=0.03)  # per-clip value still wins
    assert explicit > moved + 0.05


# ---------------------------------------------------------------------------
# Planted fixtures: known looks, rendered by the app's own renderer
# ---------------------------------------------------------------------------

_PW, _PH, _SECONDS = 720, 1280, 12
# No POWER_WORDS / digits in either text: the builder paints those
# with the template's emphasis colour - a second accent the look
# schema deliberately does not model.
_WORDS_H = "we went down to the river and watched the water move past the bridge".split()
_WORDS_C = "the sea is calm and the sky is gold at dusk".split()

_SIZE_CLASSES = sorted({t["font_size"] for t in CAPTION_TEMPLATES.values()})


def _run(cmd):
    result = subprocess.run(
        [str(c) for c in cmd], capture_output=True, text=True
    )
    assert result.returncode == 0, result.stderr[-800:]
    return result


@pytest.fixture(scope="module")
def planted(tmp_path_factory):
    if not FFMPEG:
        pytest.skip("ffmpeg not available")
    from src.media import captions
    from src.media.captions import ass_fonts_dir, build_assemblyai_ass_subtitles
    from src.media.ffmpeg import subtitles_filter_fragment

    root = tmp_path_factory.mktemp("planted_looks")
    original = captions.emoji_rendering_supported
    captions.emoji_rendering_supported = lambda: False
    try:
        bg = root / "bg.mp4"
        _run([
            "ffmpeg", "-y", "-v", "error",
            "-f", "lavfi", "-i",
            f"color=c=0x4A5A6A:size={_PW}x{_PH}:rate=25:duration={_SECONDS}",
            "-f", "lavfi", "-i",
            "aevalsrc=0.5*sin(2*PI*220*t)*lt(mod(t\\,2)\\,1.2):s=44100:d=12",
            "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-shortest", bg,
        ])

        def plant(name, words, *, template="default", override=None,
                  step=0.9, dur=0.8):
            ass = root / f"{name}.ass"
            timed = [
                {"text": w, "start": i * step, "end": i * step + dur}
                for i, w in enumerate(words)
            ]
            ok = build_assemblyai_ass_subtitles(
                bg, 0.0, float(_SECONDS), _PW, _PH, ass,
                caption_template=template, template_override=override,
                caption_words=timed,
            )
            assert ok, f"ASS build failed for {name}"
            out = root / f"{name}.mp4"
            font = (override or get_template(template))["font_family"]
            frag = subtitles_filter_fragment(ass, ass_fonts_dir(font))
            _run([
                "ffmpeg", "-y", "-v", "error", "-i", bg, "-vf", frag,
                "-c:a", "copy", "-c:v", "libx264", "-preset", "ultrafast",
                "-crf", "23", out,
            ])
            return out, ass

        hormozi_override = dict(get_template("hormozi"))
        hormozi_override["word_box"] = False  # the pill would own the colours
        no_highlight_override = dict(get_template("default"))
        no_highlight_override.update({
            "highlight_color": "#FFFFFF", "emphasis_color": "#FFFFFF",
            "emoji": False, "word_pop": False, "uppercase": True,
        })
        shadow_override = dict(get_template("default"))
        shadow_override.update({
            "stroke_color": None, "stroke_width": 0, "shadow": True,
            "highlight_color": "#FFFFFF", "emphasis_color": "#FFFFFF",
            "emoji": False, "word_pop": False, "uppercase": False,
            "animation": "none",
        })
        specs = {
            "harmozi": plant("harmozi", _WORDS_H, template="hormozi",
                             override=hormozi_override),
            "classic_box": plant("classic_box", _WORDS_C, template="classic_box",
                                 step=0.75, dur=0.65),
            "no_highlight": plant("no_highlight", _WORDS_H,
                                  override=no_highlight_override),
            "shadow": plant("shadow", _WORDS_C, override=shadow_override,
                            step=0.75, dur=0.65),
            "no_caption": (bg, None),
        }
        results = {}
        for name, (video, ass) in specs.items():
            measurements, profile, _report = analyse_reference(video, name=name)
            results[name] = {
                "video": video, "ass": ass,
                "measurements": measurements, "profile": profile,
                "look": measurements["captions"].get("look"),
            }
        return results
    finally:
        captions.emoji_rendering_supported = original


def _channels(value):
    digits = value.lstrip("#")
    return tuple(int(digits[i:i + 2], 16) for i in range(0, len(digits), 2))


def _assert_colour_close(measured, planted, tol=12):
    got, want = _channels(measured), _channels(planted)
    assert len(got) == len(want)
    for g, w in zip(got, want):
        assert abs(g - w) <= tol, f"{measured} vs planted {planted}"


def _ass_pos_y_fraction(ass_path):
    text = ass_path.read_text(encoding="utf-8", errors="replace")
    ys = [float(m.group(2)) for m in re.finditer(r"\\pos\((\d+(?:\.\d+)?),(\d+(?:\.\d+)?)\)", text)]
    assert ys, "no \\pos found in the planted ASS"
    return float(np.median(ys)) / _PH


def _ass_max_chunk_words(ass_path):
    best = 0
    for line in ass_path.read_text(encoding="utf-8", errors="replace").splitlines():
        if not line.startswith("Dialogue:"):
            continue
        text = line.split(",", 9)[-1]
        text = re.sub(r"\{[^}]*\}", "", text).strip()
        if text:
            best = max(best, len(text.split()))
    assert best > 0, "no dialogue text in the planted ASS"
    return best


def _assert_common(planted, name, *, font_size):
    look = planted[name]["look"]
    assert look is not None, f"{name}: no look measured"
    assert abs(
        look["position_y"]["value"] - _ass_pos_y_fraction(planted[name]["ass"])
    ) <= 0.03
    measured_idx = _SIZE_CLASSES.index(look["font_size"]["value"])
    planted_idx = _SIZE_CLASSES.index(font_size)
    assert abs(measured_idx - planted_idx) <= 1  # bucketed at FAIR grade
    if TESSERACT:
        assert look["words_per_line"]["value"] == \
            _ass_max_chunk_words(planted[name]["ass"])
    return look


def test_planted_harmozi(planted):
    look = _assert_common(planted, "harmozi", font_size=32)
    _assert_colour_close(look["font_color"]["value"], "#FFFFFF")
    _assert_colour_close(look["highlight_color"]["value"], "#00FF66")
    assert look["stroke"]["present"] is True
    _assert_colour_close(look["stroke"]["color"], "#000000")
    assert look["stroke"]["width_bucket"] in (3, 5)  # planted width 4
    assert look.get("background", {}).get("present") in (False, None)
    if TESSERACT:
        assert look["uppercase"]["value"] is True
    # End to end: the profile carries the measured look as an override.
    override = planted["harmozi"]["profile"]["captions"]["look_override"]
    _assert_colour_close(override["highlight_color"], "#00FF66")


def test_planted_classic_box(planted):
    look = _assert_common(planted, "classic_box", font_size=26)
    _assert_colour_close(look["font_color"]["value"], "#FFE000")
    assert "highlight_color" not in look  # none planted, none invented
    assert look["stroke"]["present"] is False
    background = look["background"]
    assert background["present"] is True
    r, g, b, a = _channels(background["color"])
    assert (r, g, b) == (0, 0, 0)
    assert abs(a - 0x99) <= 0x18  # planted #00000099, alpha re-estimated
    if TESSERACT:
        assert look["uppercase"]["value"] is False


def test_planted_no_highlight(planted):
    look = _assert_common(planted, "no_highlight", font_size=32)
    _assert_colour_close(look["font_color"]["value"], "#FFFFFF")
    assert "highlight_color" not in look
    assert look["stroke"]["present"] is True
    _assert_colour_close(look["stroke"]["color"], "#000000")
    assert look["stroke"]["width_bucket"] == 3  # planted width 3
    if TESSERACT:
        assert look["uppercase"]["value"] is True


def test_planted_shadow(planted):
    look = _assert_common(planted, "shadow", font_size=32)
    _assert_colour_close(look["font_color"]["value"], "#FFFFFF")
    assert "highlight_color" not in look
    assert look["stroke"]["present"] is False  # the shadow is not an outline
    assert look["shadow"]["value"] is True
    if TESSERACT:
        assert look["uppercase"]["value"] is False


def test_planted_no_caption(planted):
    entry = planted["no_caption"]
    assert entry["look"] is None
    assert "look_override" not in entry["profile"]["captions"]
    # The style is what it would have been before the look pass: the
    # same measurements minus the look key build the identical profile.
    measurements = copy.deepcopy(entry["measurements"])
    del measurements["captions"]["look"]
    rebuilt, _ = build_profile(measurements, name="no_caption")
    assert json.dumps(rebuilt, sort_keys=True) == \
        json.dumps(entry["profile"], sort_keys=True)
