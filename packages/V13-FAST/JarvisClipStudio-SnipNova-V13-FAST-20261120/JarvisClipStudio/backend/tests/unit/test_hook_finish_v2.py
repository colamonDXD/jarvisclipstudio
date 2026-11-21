"""HookFinish v2: settle-zoom composition + first-second SFX guarantee.

The switch (HOOK_FINISH_V2) defaults OFF: with it off the hook finish
builds exactly the pre-v2 filter chain and the audio plan is untouched.
With it on, the opening gains the settle-zoom (media.hook_v2) composed
with B-0023's punch-in, the EDL opening ramp stands down, and the
audio plan guarantees a first-second SFX hit ducked under the voice.
"""
from __future__ import annotations

import hashlib
import subprocess
from pathlib import Path

import numpy as np
import pytest

from src.media import edl_render, hook_finish as HF, hook_v2


@pytest.fixture(autouse=True)
def _v2_off(monkeypatch):
    monkeypatch.delenv("HOOK_FINISH_V2", raising=False)
    yield


def _enable(monkeypatch):
    monkeypatch.setenv("HOOK_FINISH_V2", "on")


# ------------------------------------------------------------ the switch

def test_switch_defaults_off_and_needs_an_explicit_on(monkeypatch):
    assert hook_v2.hook_finish_v2_enabled() is False
    monkeypatch.setenv("HOOK_FINISH_V2", "off")
    assert hook_v2.hook_finish_v2_enabled() is False
    monkeypatch.setenv("HOOK_FINISH_V2", "banana")
    assert hook_v2.hook_finish_v2_enabled() is False
    _enable(monkeypatch)
    assert hook_v2.hook_finish_v2_enabled() is True


# ------------------------------------------------------------ settle math

def test_settle_curve_is_an_ease_out_from_1_3_to_1_0():
    assert hook_v2.settle_zoom_at(0.0) == pytest.approx(1.3)
    assert hook_v2.settle_zoom_at(1.7) == pytest.approx(1.0)
    assert hook_v2.settle_zoom_at(3.0) == pytest.approx(1.0)
    samples = [hook_v2.settle_zoom_at(t / 10.0) for t in range(0, 18)]
    assert all(a > b for a, b in zip(samples, samples[1:]))  # strictly settling
    # ease-out: most of the zoom is gone by the halfway point
    assert hook_v2.settle_zoom_at(0.85) < 1.3 - 0.5 * 0.3 + 0.02


def test_settle_window_composes_with_the_b0023_punch():
    # Default profile: first punch at 2.6 s — the full settle fits.
    assert hook_v2.settle_seconds_for(2.6) == pytest.approx(hook_v2.SETTLE_SECONDS)
    # An earlier punch clamps the settle to end just before it.
    assert hook_v2.settle_seconds_for(1.2) == pytest.approx(1.2 - hook_v2.SETTLE_PUNCH_GAP)
    # A punch too early to compose with: no settle at all.
    assert hook_v2.settle_seconds_for(0.7) is None
    assert hook_v2.settle_seconds_for(None) == pytest.approx(hook_v2.SETTLE_SECONDS)


def test_settle_filter_expression_holds_the_anchor_fixed():
    f = hook_v2.settle_zoom_filter(1080, 1920, 540.0, 432.0, 1.7)
    assert "between(t,0,1.700)" in f and "eval=frame" in f
    assert "crop=1080:1920" in f
    # offsets are anchor * (z - 1) written against the crop's own input size
    assert "540.0*(iw/1080-1)" in f and "432.0*(ih/1920-1)" in f


def test_settle_anchor_follows_the_layout():
    # Facecam stack: centre of the 45% face panel at the top.
    assert hook_v2.settle_anchor({"reframe": {"layout_template": "facecam_red"}}, 1080, 1920) == (540.0, 432.0)
    assert hook_v2.settle_anchor({"reframe": {"layout_template": "facecam_gold"}}, 1080, 1920) == (540.0, 432.0)
    # No stack: the reframe crop's subject centre.
    assert hook_v2.settle_anchor({}, 1080, 1920) == (540.0, 864.0)
    assert hook_v2.settle_anchor(None, 1080, 1920) == (540.0, 864.0)


def test_settle_anchor_honours_drawn_boxes_arrangement(monkeypatch):
    from src.media import gaming_templates

    token = gaming_templates.MANUAL_BOXES.set(
        {"cam": {"x": 0.7, "y": 0.0, "w": 0.25, "h": 0.33},
         "game": {"x": 0.0, "y": 0.0, "w": 1.0, "h": 1.0}, "arrange": "bottom"}
    )
    try:
        assert hook_v2.settle_anchor({}, 1080, 1920) == (540.0, 1488.0)
    finally:
        gaming_templates.MANUAL_BOXES.reset(token)


# ------------------------------------------------- EDL opening ramp gate

def test_opening_ramp_stands_down_only_when_v2_is_on(monkeypatch):
    from src.auto_director.edl import _apply_opening_ramp

    assert any(k.get("trigger") == "opening_punch" for k in _apply_opening_ramp([], 10.0))
    _enable(monkeypatch)
    assert _apply_opening_ramp([], 10.0) == []


# ------------------------------------------------------- audio guarantee

def _assets(tmp_path: Path, *sfx_names: str) -> Path:
    assets = tmp_path / "assets"
    (assets / "music").mkdir(parents=True)
    (assets / "sfx").mkdir(parents=True)
    for name in sfx_names:
        (assets / "sfx" / name).touch()
    return assets


def _edl(**overrides):
    edl = {
        "clip_id": "hookv2-test",
        "duration": 10,
        "segments": [
            {"start": 0, "end": 5, "transition_in": "none", "transition_duration": 0},
            {"start": 5, "end": 10, "transition_in": "cut", "transition_duration": 0},
        ],
        "audio": {"music_mood": "none", "music_volume": 0.0, "ducking_db": 0.0},
        "sfx_events": [],
    }
    edl.update(overrides)
    return edl


def test_guarantee_off_by_default_plan_stays_none(tmp_path):
    assets = _assets(tmp_path, "impact.wav", "whoosh.wav")
    assert edl_render.build_edl_audio_graph(_edl(), 1, assets) is None


def test_guarantee_adds_a_ducked_first_second_hit(tmp_path, monkeypatch):
    _enable(monkeypatch)
    assets = _assets(tmp_path, "impact.wav", "whoosh.wav")
    plan = edl_render.build_edl_audio_graph(_edl(), 1, assets)
    assert plan is not None
    inputs, graph, label = plan
    # No layout: the settle's own sound — a whoosh — is the pick.
    assert str(assets / "sfx" / "whoosh.wav") in inputs
    assert "adelay=50:all=1" in graph
    assert "sidechaincompress" in graph and "[a_hook_hit]" in graph
    assert "[a_voice_key]" in graph  # the voice keys the duck


def test_guarantee_picks_impact_for_a_reaction_layout(tmp_path, monkeypatch):
    _enable(monkeypatch)
    assets = _assets(tmp_path, "impact.wav", "whoosh.wav")
    edl = _edl(reframe={"layout_template": "facecam_red", "delegate": "plain"})
    plan = edl_render.build_edl_audio_graph(edl, 1, assets)
    assert plan is not None
    inputs, graph, _label = plan
    assert str(assets / "sfx" / "impact.wav") in inputs
    assert str(assets / "sfx" / "whoosh.wav") not in inputs


def test_guarantee_yields_to_a_planned_first_second_event(tmp_path, monkeypatch):
    _enable(monkeypatch)
    assets = _assets(tmp_path, "impact.wav", "whoosh.wav", "pop.wav")
    edl = _edl(sfx_events=[{"name": "pop", "at_sec": 0.4, "gain_db": -6.0}])
    plan = edl_render.build_edl_audio_graph(edl, 1, assets)
    assert plan is not None
    _inputs, graph, _label = plan
    assert "[a_hook_hit]" not in graph  # the planned pop already lands at 0.4 s


def test_guarantee_skips_plain_stripped_and_short_edls(tmp_path, monkeypatch):
    _enable(monkeypatch)
    assets = _assets(tmp_path, "impact.wav", "whoosh.wav")
    # Plain ("just cuts") template: never.
    assert edl_render.build_edl_audio_graph(_edl(template="plain"), 1, assets) is None
    # The retimed voice-only fallback strips the audio block: no new sounds.
    assert edl_render.build_edl_audio_graph(_edl(audio={}), 1, assets) is None
    # Shorter than the hook-finish floor: not a hooked clip.
    short = _edl(duration=4, segments=[{"start": 0, "end": 4, "transition_in": "none", "transition_duration": 0}])
    assert edl_render.build_edl_audio_graph(short, 1, assets) is None


def test_guarantee_without_a_library_file_keeps_the_plan_none(tmp_path, monkeypatch):
    _enable(monkeypatch)
    assets = _assets(tmp_path)  # empty sfx folder
    assert edl_render.build_edl_audio_graph(_edl(), 1, assets) is None


def test_music_plan_is_byte_identical_with_the_switch_on_when_satisfied(tmp_path, monkeypatch):
    # An EDL whose plan already has music + a first-second event must
    # produce the identical graph whether the switch is on or off.
    assets = _assets(tmp_path, "impact.wav", "whoosh.wav", "pop.wav")
    (assets / "music" / "bed.wav").touch()
    edl = _edl(
        audio={"music_track": "bed", "music_volume": 0.5, "ducking_db": -12.0},
        sfx_events=[{"name": "pop", "at_sec": 0.4, "gain_db": -6.0}],
    )
    baseline = edl_render.build_edl_audio_graph(edl, 1, assets)
    _enable(monkeypatch)
    assert edl_render.build_edl_audio_graph(edl, 1, assets) == baseline


# ------------------------------------------------------- render behaviour

def _clip(path, seconds=8):
    cmd = ["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", f"testsrc2=s=270x480:d={seconds}",
           "-f", "lavfi", "-i", f"sine=frequency=300:duration={seconds}",
           "-vf", "drawbox=t=fill:c=0x202020@0.85", "-shortest", "-c:v", "libx264",
           "-pix_fmt", "yuv420p", "-c:a", "aac", str(path)]
    subprocess.run(cmd, check=True)
    return path


def _frame(path, t):
    raw = subprocess.run(["ffmpeg", "-v", "error", "-ss", str(t), "-i", str(path), "-frames:v", "1",
                          "-vf", "format=gray", "-f", "rawvideo", "-"], capture_output=True).stdout
    return np.frombuffer(raw, np.uint8)


def _digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def test_finish_hook_off_and_unset_are_byte_identical(tmp_path, monkeypatch):
    monkeypatch.setattr("src.media.edl_render.check_nvenc_available", lambda: False, raising=False)
    unset = _clip(tmp_path / "unset.mp4")
    off = _clip(tmp_path / "off.mp4")
    assert HF.finish_hook(unset, {"segments": [{}]}, seed="x") is True
    monkeypatch.setenv("HOOK_FINISH_V2", "off")
    assert HF.finish_hook(off, {"segments": [{}]}, seed="x") is True
    assert _digest(unset) == _digest(off)


def test_finish_hook_v2_settles_then_matches_the_control(tmp_path, monkeypatch):
    monkeypatch.setattr("src.media.edl_render.check_nvenc_available", lambda: False, raising=False)
    _enable(monkeypatch)
    on = _clip(tmp_path / "on.mp4")
    assert HF.finish_hook(on, {"segments": [{}]}, seed="x") is True
    monkeypatch.delenv("HOOK_FINISH_V2")
    off = _clip(tmp_path / "off.mp4")
    assert HF.finish_hook(off, {"segments": [{}]}, seed="x") is True
    # Inside the settle window the frames differ clearly (the frame is
    # zoomed 1.24x at 0.3 s): whole regions move, so a large share of
    # pixels differs by more than rounding.
    inside = np.abs(_frame(on, 0.3).astype(int) - _frame(off, 0.3).astype(int))
    assert inside.mean() > 3.0 and (inside > 2).mean() > 0.2
    # ...and after it (before the punch) the framing is the control's
    # again. Not bit-equal: the settle stage is one extra 1:1 resample,
    # whose artefacts sit on hard edges only — measured on this clip at
    # a 0.25 mean / 2% of pixels over 2 levels, against the zoomed
    # frame's 5.9 mean / 40%. The bounds below keep that separation.
    after = np.abs(_frame(on, 2.1).astype(int) - _frame(off, 2.1).astype(int))
    assert after.mean() < 0.5 and (after > 2).mean() < 0.05
    assert _digest(on) != _digest(off)
