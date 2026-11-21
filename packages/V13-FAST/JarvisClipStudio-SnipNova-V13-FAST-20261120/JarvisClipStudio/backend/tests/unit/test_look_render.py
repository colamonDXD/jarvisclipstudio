"""Studio look in the EDL renderer: graph pins and per-effect fragments."""

from pathlib import Path

from src.auto_director.look import NEUTRAL_GRADE, NEUTRAL_VFX
from src.media import edl_render


def _encoder(*_args, **_kwargs):
    return "libx264", ["-c:v", "libx264"]


def _base_edl(**extra):
    edl = {
        "duration": 15,
        "segments": [
            {"start": 0, "end": 7.5, "transition_in": "none", "transition_duration": 0},
            {"start": 7.5, "end": 15, "transition_in": "cross_dissolve", "transition_duration": 0.8},
        ],
        "zoom_keyframes": [],
        "overlays": [{"type": "info_card", "time": 2, "duration": 3, "text": "42 percent growth"}],
    }
    edl.update(extra)
    return edl


def _graph(monkeypatch, edl, **kwargs):
    monkeypatch.setattr(edl_render, "get_video_encoder", _encoder)
    _, graph, _, _ = edl_render.build_edl_filter_graph(edl, Path("source.mp4"), **kwargs)
    return graph


def _look(grade=None, vfx=None):
    return {
        "grade": {**NEUTRAL_GRADE, **(grade or {})},
        "vfx": {**NEUTRAL_VFX, **(vfx or {})},
    }


# --- Byte-identity pins -------------------------------------------------------

def test_no_look_key_leaves_graph_unchanged(monkeypatch):
    baseline = _graph(monkeypatch, _base_edl())
    for fragment in ("eq=", "curves=", "colorbalance=", "noise=", "vignette=", "unsharp=", "drawbox=", "blend=", "gblur="):
        assert fragment not in baseline


def test_fully_neutral_look_is_byte_identical(monkeypatch):
    baseline = _graph(monkeypatch, _base_edl())
    with_neutral = _graph(monkeypatch, _base_edl(look=_look()))
    assert with_neutral == baseline


def test_malformed_look_is_ignored(monkeypatch):
    baseline = _graph(monkeypatch, _base_edl())
    assert _graph(monkeypatch, _base_edl(look="junk")) == baseline
    assert _graph(monkeypatch, _base_edl(look={"grade": "junk", "vfx": 3})) == baseline


# --- Grade chain ----------------------------------------------------------------

def test_grade_chain_values():
    chain = edl_render.build_grade_filter_chain(
        {"exposure": 10, "contrast": 10, "saturation": 50}
    )
    assert chain == "eq=brightness=0.030:contrast=1.060:saturation=1.500"

    chain = edl_render.build_grade_filter_chain({"temperature": 100})
    assert chain == "colorbalance=rm=0.200:gm=0.040:bm=-0.280"

    chain = edl_render.build_grade_filter_chain({"tint": 100})
    assert chain == "colorbalance=rm=0.060:gm=-0.200:bm=0.060"

    chain = edl_render.build_grade_filter_chain({"shadows": 100, "highlights": -100})
    assert chain == "curves=all='0/0.150 0.25/0.320 0.75/0.680 1/0.880'"

    assert edl_render.build_grade_filter_chain(dict(NEUTRAL_GRADE)) == ""
    assert edl_render.build_grade_filter_chain(None) == ""


def test_grade_applies_before_subtitles_and_overlays(monkeypatch, tmp_path):
    ass = tmp_path / "captions.ass"
    ass.write_text("[Script Info]\n")
    graph = _graph(
        monkeypatch,
        _base_edl(look=_look(grade={"saturation": 60})),
        subtitle_ass_path=ass,
    )
    assert "eq=brightness=0.000:contrast=1.000:saturation=1.600" in graph
    # The grade sits on the picture; caption burn and info-card text come
    # after it so neither is graded.
    assert graph.index("eq=") < graph.index("subtitles=")
    assert graph.index("eq=") < graph.index("drawtext=")


# --- VFX fragments ---------------------------------------------------------------

def test_each_vfx_adds_its_filter(monkeypatch):
    cases = [
        ({"grain": 50}, "noise=alls=12:allf=t"),
        ({"vignette": 100}, "vignette=angle=0.450"),
        ({"sharpen": 50}, "unsharp=5:5:0.60:5:5:0.0"),
        ({"letterbox": 40}, "drawbox=x=0:y=0:w=iw:h='ih*0.200'"),
        ({"shake": 100}, "crop=w='iw-20':h='ih-20'"),
        ({"glow": 50}, "blend=all_mode=screen:all_opacity=0.40"),
    ]
    for vfx, expected in cases:
        graph = _graph(monkeypatch, _base_edl(look=_look(vfx=vfx)))
        assert expected in graph, f"{vfx} missing {expected}"


def test_glow_fragment_is_a_split_blur_screen_blend(monkeypatch):
    graph = _graph(monkeypatch, _base_edl(look=_look(vfx={"glow": 100})))
    assert "split[" in graph
    assert "gblur=sigma=32.0" in graph
    assert "blend=all_mode=screen:all_opacity=0.65" in graph


def test_shake_scales_up_then_crops_back(monkeypatch):
    graph = _graph(monkeypatch, _base_edl(look=_look(vfx={"shake": 100})))
    assert "scale=iw+20:ih+20" in graph
    assert "sin(2*PI*t*1.31)" in graph
    assert "cos(2*PI*t*1.73)" in graph


def test_letterbox_draws_top_and_bottom_bars(monkeypatch):
    graph = _graph(monkeypatch, _base_edl(look=_look(vfx={"letterbox": 40})))
    assert "drawbox=x=0:y=0:w=iw:h='ih*0.200':color=black:t=fill" in graph
    assert "drawbox=x=0:y='ih-ih*0.200':w=iw:h='ih*0.200':color=black:t=fill" in graph


def test_vfx_order_is_shake_sharpen_glow_grain_vignette_letterbox(monkeypatch):
    graph = _graph(
        monkeypatch,
        _base_edl(
            look=_look(
                grade={"contrast": 20},
                vfx={"grain": 10, "vignette": 10, "glow": 10, "sharpen": 10, "letterbox": 10, "shake": 10},
            )
        ),
    )
    positions = [
        graph.index("eq="),
        graph.index("crop=w="),
        graph.index("unsharp="),
        graph.index("blend="),
        graph.index("noise="),
        graph.index("vignette="),
        graph.index("drawbox="),
    ]
    assert positions == sorted(positions)
