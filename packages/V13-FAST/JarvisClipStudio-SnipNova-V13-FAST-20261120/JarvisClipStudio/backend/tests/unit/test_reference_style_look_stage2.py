"""Tests for the Reference Style caption-look pass, Stage 2:
animation feel + the margin-gated font suggestion.

Layers:

* Pure unit tests for the pair-evidence builder and the animation
  classifier (fabricated records - no ffmpeg, no tesseract), and
  for the look -> override / summary wiring of the font suggestion:
  the suggestion stamps ``font_family`` only when it exists in the
  look (the pass emits it only behind its score + margin gate), and
  the summary labels it a guess, never an identification.
* Matcher unit tests over masks rendered by the app's own caption
  renderer (ffmpeg-gated).
* Planted fixtures: animation classes and a font EVALUATION across
  the bundled font set, each rendered by the app's own renderer and
  analysed end to end. The evaluation is the gate for the honesty
  contract: font identification from compressed caption pixels is
  not reliable, so what ships is a suggestion that must clear a
  score floor and a margin over the runner-up. The bars pinned here
  are set from the measured numbers in STAGE2_REPORT.md.
"""

import shutil
import subprocess
import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from src.auto_director import reference_style as rs
from src.auto_director.reference_style import (
    _animation_pair_evidence,
    _caption_look_override,
    _caption_look_summary,
    _classify_animation,
    _look_word_jaccard,
)

FFMPEG = shutil.which("ffmpeg") is not None
TESSERACT = shutil.which("tesseract") is not None


# ---------------------------------------------------------------------------
# Animation: word jaccard + pair evidence + classification
# ---------------------------------------------------------------------------

def test_word_jaccard_multiset():
    assert _look_word_jaccard(["a", "b"], ["a", "b"]) == 1.0
    assert _look_word_jaccard(["a", "b"], ["c", "d"]) == 0.0
    assert _look_word_jaccard(["a", "a", "b"], ["a", "b"]) == pytest.approx(2 / 3)
    assert _look_word_jaccard([], ["a"]) is None
    assert _look_word_jaccard(["a"], []) is None


def _record(*, centroid_y=0.78, lines=1, line_height=40.0,
            region=(100, 900, 500, 960), hx=None):
    return {
        "centroid_y": centroid_y, "lines": lines,
        "line_height": line_height, "region": region,
        "highlight_centroid_x": hx,
    }


def test_pair_evidence_highlight_move_is_karaoke_signal():
    frame = _record(hx=0.30)
    pair = _record(hx=0.52)
    ev = _animation_pair_evidence(
        frame, pair, None, ["we", "went", "down"], ["we", "went", "down"]
    )
    assert ev == {"highlight_move": True, "word_growth": False, "scale": False}


def test_pair_evidence_highlight_move_needs_same_text():
    # The pair landed in the NEXT chunk: its highlight sits further
    # right, but the words are different - no karaoke conclusion.
    frame = _record(hx=0.20)
    pair = _record(hx=0.55)
    ev = _animation_pair_evidence(
        frame, pair, None, ["we", "went", "down"], ["past", "the", "bridge"]
    )
    assert ev["highlight_move"] is False


def test_pair_evidence_highlight_move_needs_rightward_motion():
    frame = _record(hx=0.55)
    pair = _record(hx=0.30)
    ev = _animation_pair_evidence(
        frame, pair, None, ["we", "went", "down"], ["we", "went", "down"]
    )
    assert ev["highlight_move"] is False


def test_pair_evidence_word_growth_prefix_only():
    frame = _record()
    pair = _record()
    ev = _animation_pair_evidence(frame, pair, None, ["we", "went"], ["we", "went", "down"])
    assert ev["word_growth"] is True
    ev = _animation_pair_evidence(frame, pair, None, ["we", "went"], ["down", "we", "went"])
    assert ev["word_growth"] is False
    ev = _animation_pair_evidence(frame, pair, None, ["we", "went"], ["we"])
    assert ev["word_growth"] is False


def test_pair_evidence_scale_from_burst_and_karaoke_entrance_guard():
    frame = _record(line_height=40.0)
    pair = _record(line_height=40.0)
    # Pop entrance caught mid-arc: 88% scale -> ratio 1/0.88 = 1.136.
    burst = _record(line_height=35.2)
    ev = _animation_pair_evidence(frame, pair, burst, ["we", "went"], ["we", "went"])
    assert ev["scale"] is True
    # The karaoke line entrance (92% -> 100%, ratio 1.087) must NOT
    # read as a scale animation.
    burst = _record(line_height=36.8)
    ev = _animation_pair_evidence(frame, pair, burst, ["we", "went"], ["we", "went"])
    assert ev["scale"] is False


def test_pair_evidence_scale_from_pair_needs_same_text():
    frame = _record(line_height=40.0)
    pair = _record(line_height=46.0)  # ratio 1.15
    ev = _animation_pair_evidence(frame, pair, None, ["we", "went"], ["past", "bridge"])
    assert ev["scale"] is False
    ev = _animation_pair_evidence(frame, pair, None, ["we", "went"], ["we", "went"])
    assert ev["scale"] is True


def test_pair_evidence_different_line_position_says_nothing():
    frame = _record(centroid_y=0.78, hx=0.30)
    pair = _record(centroid_y=0.45, hx=0.60)
    ev = _animation_pair_evidence(
        frame, pair, None, ["we", "went"], ["we", "went", "down"]
    )
    assert ev == {"highlight_move": False, "word_growth": False, "scale": False}


def _ev(move=False, growth=False, scale=False):
    return {"highlight_move": move, "word_growth": growth, "scale": scale}


def test_classify_animation_rules():
    assert _classify_animation([])["value"] == "unknown"
    assert _classify_animation([_ev()])["value"] == "unknown"  # one quiet pair is thin
    # Two quiet pairs are still not "static": a pop/bounce entrance
    # arc (~250-360 ms) is sampled only ~10% of the time per pair,
    # so two quiet pairs cannot rule motion out (measured on the
    # planted pop fixture - it read static at a two-pair floor).
    assert _classify_animation([_ev(), _ev()])["value"] == "unknown"
    assert _classify_animation([_ev(), _ev(), _ev()])["value"] == "static"
    assert _classify_animation([_ev(move=True), _ev()])["value"] == "karaoke"
    assert _classify_animation([_ev(growth=True)])["value"] == "karaoke"
    assert _classify_animation([_ev(scale=True), _ev()])["value"] == "pop"
    # Karaoke wins over pop when both fire (karaoke templates with a
    # word-pop entrance produce scale evidence too).
    assert _classify_animation([_ev(move=True, scale=True)])["value"] == "karaoke"
    result = _classify_animation([_ev(move=True), _ev(scale=True)])
    assert result["n"] == 2
    assert result["evidence"] == {
        "highlight_moves": 1, "word_growth": 0, "scale_changes": 1,
    }


# ---------------------------------------------------------------------------
# Font suggestion wiring: override + summary
# ---------------------------------------------------------------------------

SUGGESTION = {
    "family": "BebasNeue-Regular", "score": 0.71, "margin": 0.19,
    "runner_up": "Anton-Regular", "n": 2,
}


def test_override_stamps_font_family_from_suggestion():
    override = _caption_look_override({"font_suggestion": dict(SUGGESTION)})
    assert override == {"font_family": "BebasNeue-Regular"}


def test_override_without_suggestion_stamps_no_font_family():
    override = _caption_look_override({
        "position_y": {"value": 0.71, "n": 9},
        "font_size": {"value": 32, "n": 9},
    })
    assert override is not None
    assert "font_family" not in override


def test_stage1_look_shape_unchanged_by_stage2_wiring():
    # A Stage-1-shaped look (no stage-2 fields) must map to exactly
    # the override Stage 1 produced: stage 2 adds keys only when its
    # own fields exist in the look.
    look = {
        "position_y": {"value": 0.71, "n": 9},
        "font_color": {"value": "#FFFFFF", "n": 9},
        "font_size": {"value": 32, "n": 9},
        "stroke": {"present": True, "color": "#000000", "width_bucket": 5, "n": 9},
    }
    assert _caption_look_override(look) == {
        "position_y": 0.71, "font_color": "#FFFFFF",
        "stroke_color": "#000000", "stroke_width": 5, "font_size": 32,
    }


def test_summary_labels_suggestion_a_guess():
    summary = _caption_look_summary({
        "font_suggestion": dict(SUGGESTION),
        "animation": {"value": "karaoke", "n": 3,
                      "evidence": {"highlight_moves": 2, "word_growth": 0,
                                   "scale_changes": 0}},
    })
    assert summary["font_guess"] == \
        "BebasNeue-Regular (closest bundled font — a guess)"
    assert "identified" not in summary["font_guess"]
    assert "detected" not in summary["font_guess"]
    assert summary["animation"] == "karaoke"


def test_summary_without_suggestion_has_no_font_guess():
    summary = _caption_look_summary({"position_y": {"value": 0.7, "n": 5}})
    assert "font_guess" not in summary
    assert "animation" not in summary


# ---------------------------------------------------------------------------
# Font matcher: mask scoring, candidate rendering, the gate
# ---------------------------------------------------------------------------

from src.auto_director.reference_style import (  # noqa: E402
    _font_match_ranking,
    _font_suggestion_for_records,
    _mask_match_score,
    _render_candidate_masks,
)


def test_mask_match_score_identity_and_disjoint():
    rng = np.random.default_rng(3)
    mask = rng.random((40, 200)) > 0.6
    assert _mask_match_score(mask, mask) > 0.95
    other = np.zeros((40, 200), bool)
    other[:, :20] = True
    far = np.zeros((40, 200), bool)
    far[:, 180:] = True
    assert _mask_match_score(other, far) < 0.2
    assert _mask_match_score(mask, np.zeros((40, 200), bool)) == 0.0


@pytest.mark.skipif(not FFMPEG, reason="ffmpeg not available")
def test_render_candidate_masks_covers_bundled_fonts():
    stems = rs._bundled_font_stems()
    # The bundled set as re-counted 2026-11-19: the scoping counted 25;
    # series A then added the OFL/Apache pack and dropped THEBOLDFONT
    # (licence unverifiable), landing on 34.
    assert len(stems) == 34
    masks = _render_candidate_masks("the quick brown fox", 48, stems)
    assert len(masks) >= 20
    for mask in masks.values():
        assert mask.any()


@pytest.mark.skipif(not FFMPEG, reason="ffmpeg not available")
def test_font_match_ranking_recovers_distinctive_font():
    # Self-consistency at unit level (clean mask, no codec): the
    # ranking must put the font the mask was rendered with first.
    # The planted-fixture EVALUATION below is the real gate.
    text = "the quick brown fox"
    masks = _render_candidate_masks(text, 48, rs._bundled_font_stems())
    record = {
        "font_mask": masks["Bangers-Regular"],
        "lines": 1,
        "ocr_words": [
            {"text": w, "line": (1, 1, 1)} for w in text.split()
        ],
    }
    ranking, used = _font_match_ranking([record])
    assert used == 1
    assert ranking[0][0] == "Bangers-Regular"


def test_font_suggestion_gate(monkeypatch):
    def fake_ranking(score, margin):
        return lambda records: (
            [("FontA", score), ("FontB", score - margin)], 2
        )

    monkeypatch.setattr(rs, "_font_match_ranking", fake_ranking(0.30, 0.30))
    assert _font_suggestion_for_records([{}]) is None  # score floor
    monkeypatch.setattr(rs, "_font_match_ranking", fake_ranking(0.90, 0.01))
    assert _font_suggestion_for_records([{}]) is None  # margin gate
    monkeypatch.setattr(rs, "_font_match_ranking", fake_ranking(0.90, 0.30))
    suggestion = _font_suggestion_for_records([{}])
    assert suggestion == {
        "family": "FontA", "score": 0.9, "margin": 0.3,
        "runner_up": "FontB", "n": 2,
    }
    monkeypatch.setattr(rs, "_font_match_ranking", lambda records: ([], 0))
    assert _font_suggestion_for_records([{}]) is None  # matcher could not run
    assert _font_suggestion_for_records([]) is None


# ---------------------------------------------------------------------------
# Planted fixtures: animation classes, rendered by the app's renderer
# ---------------------------------------------------------------------------

_PW, _PH, _SECONDS = 720, 1280, 12
_WORDS_A = "we went down to the river and watched the water move past the bridge".split()


def _run(cmd):
    result = subprocess.run(
        [str(c) for c in cmd], capture_output=True, text=True
    )
    assert result.returncode == 0, result.stderr[-800:]
    return result


@pytest.fixture(scope="module")
def planted_anim(tmp_path_factory):
    if not FFMPEG:
        pytest.skip("ffmpeg not available")
    from src.auto_director.reference_style import analyse_reference
    from src.caption_templates import get_template
    from src.media import captions
    from src.media.captions import ass_fonts_dir, build_assemblyai_ass_subtitles
    from src.media.ffmpeg import subtitles_filter_fragment

    root = tmp_path_factory.mktemp("planted_anim")
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

        def plant(name, *, template="default", override=None, step=0.9, dur=0.8):
            ass = root / f"{name}.ass"
            timed = [
                {"text": w, "start": i * step, "end": i * step + dur}
                for i, w in enumerate(_WORDS_A)
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
            return out

        def flat_override(**extra):
            override = dict(get_template("default"))
            override.update({
                "highlight_color": "#FFFFFF", "emphasis_color": "#FFFFFF",
                "emoji": False, "word_pop": False,
            })
            override.update(extra)
            return override

        hormozi_override = dict(get_template("hormozi"))
        hormozi_override["word_box"] = False
        specs = {
            "static": plant("static", override=flat_override(animation="none")),
            "karaoke": plant("karaoke", template="hormozi",
                             override=hormozi_override, step=0.5, dur=0.45),
            "fade": plant("fade", override=flat_override(animation="fade")),
            "pop": plant("pop", template="large_centre_pop"),
        }
        results = {}
        for name, video in specs.items():
            measurements, _profile, _report = analyse_reference(video, name=name)
            look = measurements["captions"].get("look")
            results[name] = (look or {}).get("animation")
        return results
    finally:
        captions.emoji_rendering_supported = original


def test_planted_animation_static(planted_anim):
    animation = planted_anim["static"]
    assert animation is not None
    assert animation["value"] == "static"
    assert animation["n"] >= 3


def test_planted_animation_karaoke(planted_anim):
    animation = planted_anim["karaoke"]
    assert animation is not None
    assert animation["value"] == "karaoke"
    assert animation["evidence"]["highlight_moves"] >= 1


def test_planted_animation_fade_is_not_overread(planted_anim):
    # A fade's ramps sit at chunk edges the pairs rarely catch; the
    # honest outcomes are static or unknown - never karaoke or pop.
    animation = planted_anim["fade"]
    assert animation is not None
    assert animation["value"] in ("static", "unknown")


def test_planted_animation_pop_never_reads_as_karaoke(planted_anim):
    # Pop entrances run their whole scale arc inside ~250 ms, so
    # sparse pair sampling catches them only sometimes: the planted
    # outcome is reported in STAGE2_REPORT.md, and what is pinned
    # here is the restraint - no highlight exists to move, so a pop
    # render must never be classified karaoke.
    animation = planted_anim["pop"]
    assert animation is not None
    assert animation["value"] in ("pop", "static", "unknown")
    assert animation["evidence"]["highlight_moves"] == 0


# ---------------------------------------------------------------------------
# Planted EVALUATION: the font matcher against 12 bundled fonts.
#
# This suite is the honesty gate for the whole stage. The bars below
# are set from the measured numbers in STAGE2_REPORT.md - the raw
# matcher is mediocre at exact-face identification (that is the
# scoping's verdict, confirmed), so what is pinned is (a) the raw
# rates, honestly low, (b) class-level correctness: the winner is
# always the planted face, its runner-up, or a member of its
# confusion group, and (c) the GATE's precision: every suggestion
# the shipped gates actually emit is exactly right, and the
# same-family coin flips stay silent.
# ---------------------------------------------------------------------------

_FONT_EVAL_FONTS = [
    ("Montserrat-Black", "heavy"), ("Montserrat-ExtraBold", "heavy"),
    ("Poppins-ExtraBold", "heavy"), ("BarlowCondensed-Bold", "heavy"),
    ("ArchivoBlack-Regular", "heavy"),
    ("BebasNeue-Regular", "display"), ("Anton-Regular", "display"),
    ("Bangers-Regular", "display"),
    ("NotoSerif-Regular", "serif"),
    ("Inter", "text"), ("Roboto", "text"), ("OpenSans", "text"),
]
_FONT_EVAL_WORDS = "the quick brown fox jumps over the lazy dog".split()


@pytest.fixture(scope="module")
def font_eval(tmp_path_factory):
    if not FFMPEG:
        pytest.skip("ffmpeg not available")
    if not TESSERACT:
        pytest.skip("font matching needs OCR text")
    from src.auto_director.reference_style import analyse_reference
    from src.caption_templates import get_template
    from src.media import captions
    from src.media.captions import ass_fonts_dir, build_assemblyai_ass_subtitles
    from src.media.ffmpeg import subtitles_filter_fragment

    root = tmp_path_factory.mktemp("font_eval")
    original = captions.emoji_rendering_supported
    captions.emoji_rendering_supported = lambda: False
    captured: list = []
    real_ranking = rs._font_match_ranking

    def capturing(records):
        ranking, used = real_ranking(records)
        captured.append((ranking, used))
        return ranking, used

    rs._font_match_ranking = capturing
    try:
        bg = root / "bg.mp4"
        _run([
            "ffmpeg", "-y", "-v", "error",
            "-f", "lavfi", "-i",
            f"color=c=0x4A5A6A:size={_PW}x{_PH}:rate=25:duration=10",
            "-f", "lavfi", "-i",
            "aevalsrc=0.5*sin(2*PI*220*t)*lt(mod(t\\,2)\\,1.2):s=44100:d=10",
            "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-shortest", bg,
        ])
        results = {}
        for stem, group in _FONT_EVAL_FONTS:
            override = dict(get_template("default"))
            override.update({
                "font_family": stem, "animation": "none", "word_pop": False,
                "emoji": False, "highlight_color": "#FFFFFF",
                "emphasis_color": "#FFFFFF",
                "uppercase": group in ("heavy", "display"),
                "font_size": 34, "max_words_per_line": 5,
            })
            timed = [
                {"text": w, "start": i * 0.9, "end": i * 0.9 + 0.8}
                for i, w in enumerate(_FONT_EVAL_WORDS)
            ]
            ass = root / f"{stem}.ass"
            ok = build_assemblyai_ass_subtitles(
                bg, 0.0, 10.0, _PW, _PH, ass,
                caption_template="default", template_override=override,
                caption_words=timed,
            )
            assert ok, f"ASS build failed for {stem}"
            video = root / f"{stem}.mp4"
            frag = subtitles_filter_fragment(ass, ass_fonts_dir(stem))
            _run([
                "ffmpeg", "-y", "-v", "error", "-i", bg, "-vf", frag,
                "-c:a", "copy", "-c:v", "libx264", "-preset", "ultrafast",
                "-crf", "23", video,
            ])
            before = len(captured)
            measurements, profile, _report = analyse_reference(video, name=stem)
            ranking, used = captured[before] if len(captured) > before else ([], 0)
            results[stem] = {
                "group": group,
                "ranking": ranking,
                "used": used,
                "look": measurements["captions"].get("look"),
                "profile": profile,
            }
        return results
    finally:
        rs._font_match_ranking = real_ranking
        captions.emoji_rendering_supported = original


def _class_correct(entry, planted):
    """Winner is the planted face, or the planted face is the
    runner-up, or winner and planted share a confusion group."""
    ranking = entry["ranking"]
    if not ranking:
        return False
    if ranking[0][0] == planted:
        return True
    if len(ranking) > 1 and ranking[1][0] == planted:
        return True
    group = rs._FONT_CONFUSION_GROUP_OF.get(ranking[0][0])
    return bool(group and planted in group)


def test_font_eval_raw_top1_rates(font_eval):
    hits = {
        stem: bool(e["ranking"]) and e["ranking"][0][0] == stem
        for stem, e in font_eval.items()
    }
    # Measured (STAGE2_REPORT.md, shipped configuration): raw top-1
    # 9/12 overall, 3/5 in the heavy group - the heavy misses are
    # exactly the two Montserrat weights swapping places. The pins
    # sit below the measured values to absorb sampling wobble; they
    # pin the HONEST rate, not an aspirational one.
    # Re-based 2026-11-19 on the 34-font bundle (series A dropped
    # THEBOLDFONT for licence reasons; BarlowCondensed-Bold from the
    # OFL/Apache pack takes its eval slot): measured again at exactly
    # 9/12 overall and 3/5 heavy, same misses (the Montserrat pair
    # swapping, NotoSerif landing runner-up).
    assert sum(hits.values()) >= 7                      # measured 9 / 12
    heavy = [s for s, e in font_eval.items() if e["group"] == "heavy"]
    assert sum(hits[s] for s in heavy) >= 3             # measured 3 / 5
    assert hits["BarlowCondensed-Bold"] and hits["ArchivoBlack-Regular"]


def test_font_eval_class_level_correctness(font_eval):
    # The matcher always lands on the right face's neighbourhood:
    # exact face, runner-up, or confusion-group mate. This is the
    # property that makes a GATED suggestion meaningful at all.
    wrong_class = [
        stem for stem, e in font_eval.items() if not _class_correct(e, stem)
    ]
    assert wrong_class == []


def test_font_eval_gate_precision_and_silence(font_eval):
    emitted = {}
    for stem, entry in font_eval.items():
        suggestion = rs._gate_font_suggestion(entry["ranking"], entry["used"])
        if suggestion is not None:
            emitted[stem] = suggestion["family"]
    # Every emitted suggestion is exactly right - no exceptions.
    assert all(family == stem for stem, family in emitted.items())
    # Non-vacuous: the decisive silhouettes do fire.
    assert emitted.get("BarlowCondensed-Bold") == "BarlowCondensed-Bold"
    assert len(emitted) >= 2
    # The measured coin flips stay silent: the two Montserrat
    # weights swap places in the raw ranking, so neither may emit.
    assert "Montserrat-Black" not in emitted
    assert "Montserrat-ExtraBold" not in emitted


def test_font_eval_end_to_end_stamping(font_eval):
    bold = font_eval["BarlowCondensed-Bold"]
    assert bold["look"]["font_suggestion"]["family"] == "BarlowCondensed-Bold"
    override = bold["profile"]["captions"]["look_override"]
    assert override["font_family"] == "BarlowCondensed-Bold"
    summary = bold["profile"]["reference_style"]["caption_look"]
    assert summary["font_guess"] == \
        "BarlowCondensed-Bold (closest bundled font — a guess)"
    mont = font_eval["Montserrat-Black"]
    assert "font_suggestion" not in mont["look"]
    assert "font_family" not in mont["profile"]["captions"]["look_override"]
