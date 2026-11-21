"""Step 2 of the clip1 correction: no blurred-copy fill, 4K-aware subject probe."""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from src.media import fit_pad
from src.media import reframing


@pytest.fixture(autouse=True)
def _clear(monkeypatch):
    monkeypatch.delenv("FIT_PAD", raising=False)
    monkeypatch.delenv("REFRAME_HIRES_PROBE", raising=False)


def test_default_is_todays_blur_byte_for_byte():
    chain = "scale=540:960:force_original_aspect_ratio=increase,crop=540:960,gblur=sigma=14:steps=2,scale=1080:1920:flags=bilinear,setsar=1,format=yuv420p"
    assert fit_pad.edl_backdrop_chain(chain) == chain
    assert fit_pad.full_frame_background(14) == (
        "[bgsrc]scale=540:960:force_original_aspect_ratio=increase,crop=540:960,"
        "gblur=sigma=14:steps=2,scale=1080:1920:flags=bilinear,setsar=1[bg];"
    )


def test_ambient_has_no_blur_and_no_picture_scale_copy(monkeypatch):
    monkeypatch.setenv("FIT_PAD", "ambient")
    for chain in (fit_pad.edl_backdrop_chain("x"), fit_pad.full_frame_background(14)):
        assert "gblur" not in chain and "boxblur" not in chain
        assert "scale=1:1:flags=area" in chain  # averaged to one colour: no imagery
    assert fit_pad.full_frame_background(14).startswith("[bgsrc]") and fit_pad.full_frame_background(14).endswith("[bg];")


def test_unknown_mode_falls_back_to_blur(monkeypatch):
    monkeypatch.setenv("FIT_PAD", "sparkles")
    assert fit_pad.fit_pad_mode() == "blur"


def test_full_frame_filter_uses_the_pad(monkeypatch):
    monkeypatch.setenv("FIT_PAD", "ambient")
    graph = reframing.build_full_frame_filter()
    assert "gblur" not in graph and "scale=1:1:flags=area" in graph


def test_ambient_backdrop_renders_flat_bars(monkeypatch, tmp_path):
    """Render a busy test pattern through the ambient pad: the bar area must be
    nearly uniform (a gradient), unlike the blurred copy of the picture."""
    monkeypatch.setenv("FIT_PAD", "ambient")
    out = tmp_path / "pad.png"
    chain = fit_pad.edl_backdrop_chain("unused")
    subprocess.run(
        ["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc2=size=1920x1080:rate=1",
         "-frames:v", "1", "-vf", chain, str(out)],
        check=True, capture_output=True,
    )
    import cv2

    image = cv2.imread(str(out))
    assert image.shape[:2] == (1920, 1080)
    top_bar = image[:600]
    # One averaged colour: the bar is flat (no imagery, no blocks).
    assert float(top_bar.reshape(-1, 3).std(axis=0).max()) < 1.0


def test_hires_probe_only_for_wide_sources(monkeypatch):
    assert reframing.analysis_proc_width(3840, 480) == 480
    monkeypatch.setenv("REFRAME_HIRES_PROBE", "on")
    assert reframing.analysis_proc_width(3840, 480) == 960
    assert reframing.analysis_proc_width(1920, 480) == 480
    assert reframing.analysis_proc_width(3840, 1280) == 1280


def test_cache_from_other_probe_mode_is_not_reused(monkeypatch, tmp_path):
    from src.media import reframe_plan as rp

    source = tmp_path / "s.mp4"
    source.write_bytes(b"x")
    rp._save_cache(source, rp._empty_cache(source, 3840, 2160, 3.0))
    assert rp._load_cache(source) is not None
    monkeypatch.setenv("REFRAME_HIRES_PROBE", "on")
    assert rp._load_cache(source) is None
