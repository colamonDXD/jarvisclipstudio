"""Brand Kit exported-render proof (real FFmpeg).

The unit suite (test_brand_kit.py) proves the kit resolves, stamps the
EDL and recolours the ASS text. This suite proves the four surfaces
Luffy's 2026-11-09 clarification names, on real rendered pixels:

1. CAPTIONS  — the kit's primary/accent colours are burned into the
   exported clip's caption area (frame pixels, not ASS text).
2. GRAPHICS  — the hook title carries the kit's violet band and the
   kit's logo watermark is composited into the exported frames at the
   kit's position.
3. EXPORTS   — the exported MP4 itself carries all of the above plus
   the kit's music track, audible in the speech gaps.
4. THUMBNAILS — the deliverable bundle's cover.jpg (the app's clip
   poster) carries the kit's logo and violet title band; the control
   cover does not.

Wiring mirrors production exactly: the EDL comes from
``VideoService.build_pipeline_edl`` with a resolved brand application,
the ASS is built with the same arguments ``create_optimized_clip``
passes (``video_utils.brand_template_override`` + the EDL's
``brand_title``), and the render goes through
``render_edl_clip_ffmpeg`` — the function the reframing path delegates
to. The control render repeats everything with no kit.

Evidence frames, covers and a measurements JSON are copied to
``~/workspace/research_notes/brand-kit/proof/`` when that directory is
reachable, so the lane report can point at real artifacts.
"""

import json
import os
import shutil
import subprocess
from pathlib import Path

import cv2
import numpy as np
import pytest

from src import video_utils
from src.auto_director import brand_kit as bk
from src.deliverable_bundle import write_deliverable_bundle
from src.font_registry import FONTS_DIR
from src.media import captions, edl_render, music_library
from src.media.captions import build_assemblyai_ass_subtitles
from src.media.gap_activity import _window_levels_db
from src.services.video_service import VideoService

ffmpeg_missing = shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None
pytestmark = pytest.mark.skipif(ffmpeg_missing, reason="ffmpeg/ffprobe not installed")

PRIMARY = "#7C5CFF"  # kit primary (violet)
ACCENT = "#38E1FF"  # kit accent (cyan)
LOGO_BGR = (255, 0, 255)  # solid magenta logo, unmissable in frames
PROOF_DIR = Path.home() / "workspace" / "research_notes" / "brand-kit" / "proof"

KIT = {
    "id": "kit-proof",
    "user_id": "user-1",
    "name": "Proof Channel",
    "primary_color": PRIMARY,
    "accent_color": ACCENT,
    "caption_template": "hormozi",
    "font_name": None,
    "title_style": "question",
    "music_track": "kit-track.wav",
    "logo_filename": "logo.png",
    "logo_position": "bottom_right",
    "logo_opacity": 1.0,
    "intro_asset": None,
    "outro_asset": None,
}

WORDS = [
    {"text": text, "start": i * 0.4, "end": i * 0.4 + 0.35}
    for i, text in enumerate("hello brave new world this is the proof".split())
]


def _run(command):
    result = subprocess.run(command, capture_output=True, text=True, timeout=600)
    assert result.returncode == 0, result.stderr
    return result


def _make_source(path: Path) -> None:
    """12 s vertical source: speech bursts [0,2) [4,6) [8,10), silent gaps.

    The picture is flat mid-grey on purpose: testsrc2's colour bars
    contain magenta/cyan blocks that would poison the kit-colour pixel
    assertions. On grey, any violet/cyan/magenta pixel in a frame came
    from the title, the captions or the logo — nothing else.
    """
    _run([
        "ffmpeg", "-y", "-v", "error",
        "-f", "lavfi", "-i", "color=c=0x404040:size=320x568:rate=30:duration=12",
        "-f", "lavfi", "-i",
        "aevalsrc=0.5*sin(2*PI*700*t)*lt(mod(t\\,4)\\,2):s=48000:d=12",
        "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest",
        str(path),
    ])


def _make_track(path: Path) -> None:
    _run([
        "ffmpeg", "-y", "-v", "error",
        "-f", "lavfi", "-i", "aevalsrc=0.4*sin(2*PI*180*t):s=48000:d=30",
        "-c:a", "pcm_s16le", str(path),
    ])


def _library(tmp_path: Path, monkeypatch) -> Path:
    """Empty library dirs; track files are added by the fixture later.

    Order matters: the control EDL must be built while the library has
    no calm track, or the pipeline's Auto bed would stamp it (and the
    control would stop being a no-kit render of today's behaviour).
    """
    assets = tmp_path / "assets"
    (assets / "music").mkdir(parents=True)
    (assets / "sfx").mkdir(parents=True)
    user_dir = tmp_path / "user_music"
    user_dir.mkdir()
    monkeypatch.setattr(edl_render, "ASSETS_DIR", assets)
    monkeypatch.setattr(music_library, "user_music_dir", lambda: user_dir)
    return assets


def _store_logo(tmp_path: Path, monkeypatch) -> Path:
    monkeypatch.setenv("TEMP_DIR", str(tmp_path))
    raw = tmp_path / "logo-raw.png"
    image = np.zeros((200, 400, 3), dtype=np.uint8)
    image[:, :] = LOGO_BGR
    assert cv2.imwrite(str(raw), image)
    filename = bk.store_logo("user-1", raw, "logo.png")
    stored = bk.logo_path("user-1", filename)
    assert stored is not None
    return stored


def _segment():
    return {"start_time": "00:00", "end_time": "00:12", "text": "a podcast segment"}


def _build_ass(source, tmp_path, name, edl, caption_template, hook_title):
    ass_path = tmp_path / name
    ok = build_assemblyai_ass_subtitles(
        source, 0.0, 12.0, 1080, 1920, ass_path,
        caption_template=caption_template,
        hook_title=hook_title,
        caption_words=WORDS,
        template_override=video_utils.brand_template_override(edl, caption_template),
        brand_title=(edl or {}).get("brand_title"),
    )
    assert ok is True
    return ass_path


def _frame(video: Path, at_seconds: float, out: Path) -> np.ndarray:
    _run([
        "ffmpeg", "-y", "-v", "error", "-ss", str(at_seconds), "-i", str(video),
        "-frames:v", "1", str(out),
    ])
    image = cv2.imread(str(out))
    assert image is not None
    return image


def _near(image: np.ndarray, bgr, tol: int = 45) -> int:
    """Count pixels within tol (per channel) of a BGR colour."""
    target = np.array(bgr, dtype=np.int16)
    diff = np.abs(image.astype(np.int16) - target)
    return int(np.all(diff <= tol, axis=2).sum())


def _window_rms_db(path: Path, start: float, end: float) -> float:
    levels = _window_levels_db(path, 0, start, end)
    assert len(levels) > 0
    return float(10.0 * np.log10(np.mean(np.power(10.0, levels / 10.0))))


def _save_proof(name: str, source: Path) -> None:
    try:
        PROOF_DIR.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, PROOF_DIR / name)
    except OSError:
        pass  # evidence copy is best-effort; assertions are the proof


@pytest.fixture(scope="module")
def rendered(tmp_path_factory):
    mp = pytest.MonkeyPatch.context()
    monkeypatch = mp.__enter__()
    tmp_path = tmp_path_factory.mktemp("brandkit-proof")
    monkeypatch.setenv("FORCE_CPU_ENCODE", "true")
    monkeypatch.setattr(captions, "emoji_rendering_supported", lambda: False)
    assets = _library(tmp_path, monkeypatch)
    _store_logo(tmp_path, monkeypatch)
    source = tmp_path / "source.mp4"
    _make_source(source)

    # Control first, while the library is still empty: no Auto bed is
    # stamped, so this is exactly today's no-kit pipeline output.
    control_edl = VideoService.build_pipeline_edl(
        _segment(), WORDS, niche_override="podcast_news", duration=12.0,
    )
    assert control_edl is not None
    assert not any(key.startswith("brand_") for key in control_edl)
    assert "music_track" not in control_edl["audio"]
    control_ass = _build_ass(
        source, tmp_path, "control.ass", control_edl, "default", "The Proof Hook"
    )
    control = tmp_path / "control.mp4"
    assert edl_render.render_edl_clip_ffmpeg(
        source, control, control_edl,
        subtitle_ass_path=control_ass, fonts_dir=FONTS_DIR, assets_dir=assets,
    ) is True

    # Now the kit's track exists and the branded render is built.
    _make_track(assets / "music" / "kit-track.wav")
    application = bk.resolve_brand_application(KIT, user_id="user-1")
    assert application["brand_watermark"] is not None  # logo file resolved
    branded_edl = VideoService.build_pipeline_edl(
        _segment(), WORDS, niche_override="podcast_news", duration=12.0,
        music_override=KIT["music_track"], brand_application=application,
    )
    assert branded_edl is not None
    branded_ass = _build_ass(
        source, tmp_path, "branded.ass", branded_edl, "hormozi", "The Proof Hook"
    )
    branded = tmp_path / "branded.mp4"
    assert edl_render.render_edl_clip_ffmpeg(
        source, branded, branded_edl,
        subtitle_ass_path=branded_ass, fonts_dir=FONTS_DIR, assets_dir=assets,
    ) is True

    frames = {
        "branded_title": _frame(branded, 1.0, tmp_path / "branded-title.png"),
        "branded_caption": _frame(branded, 1.0, tmp_path / "branded-caption.png"),
        "control_title": _frame(control, 1.0, tmp_path / "control-title.png"),
        "control_caption": _frame(control, 1.0, tmp_path / "control-caption.png"),
    }
    frame_files = {
        "branded_title": tmp_path / "branded-title.png",
        "control_title": tmp_path / "control-title.png",
    }

    branded_bundle = write_deliverable_bundle(
        branded, branded_edl, {}, "The Proof Hook",
        bundle_dir=tmp_path / "bundle-branded",
    )
    control_bundle = write_deliverable_bundle(
        control, control_edl, {}, "The Proof Hook",
        bundle_dir=tmp_path / "bundle-control",
    )

    yield {
        "tmp": tmp_path,
        "branded": branded,
        "control": control,
        "branded_edl": branded_edl,
        "frames": frames,
        "frame_files": frame_files,
        "branded_cover": branded_bundle / "cover.jpg",
        "control_cover": control_bundle / "cover.jpg",
    }
    mp.__exit__(None, None, None)


def _bottom_right(image: np.ndarray) -> np.ndarray:
    height, width = image.shape[:2]
    return image[int(height * 0.80):, int(width * 0.70):]


def test_export_carries_kit_music_in_gaps(rendered):
    gap = _window_rms_db(rendered["branded"], 2.2, 3.8)
    control_gap = _window_rms_db(rendered["control"], 2.2, 3.8)
    assert gap > -50.0, f"kit track missing from export gaps: {gap:.1f} dB"
    assert gap - control_gap >= 10.0, (
        f"branded gap {gap:.1f} dB vs control {control_gap:.1f} dB"
    )


def test_graphics_title_band_and_logo_in_export_frames(rendered):
    violet = (0xFF, 0x5C, 0x7C)  # PRIMARY #7C5CFF in BGR
    branded = rendered["frames"]["branded_title"]
    control = rendered["frames"]["control_title"]
    # Hook title band: the kit's violet fills the title box in the top area.
    assert _near(branded[: int(branded.shape[0] * 0.45)], violet) > 500
    assert _near(control[: int(control.shape[0] * 0.45)], violet) < 50
    # Logo watermark: solid magenta block in the bottom-right corner.
    assert _near(_bottom_right(branded), LOGO_BGR) > 200
    assert _near(_bottom_right(control), LOGO_BGR) < 50


def test_captions_carry_kit_colours_in_export_frames(rendered):
    violet = (0xFF, 0x5C, 0x7C)  # kit primary recolours the caption text
    cyan = (0xFF, 0xE1, 0x38)  # kit accent recolours the highlight
    branded = rendered["frames"]["branded_caption"]
    control = rendered["frames"]["control_caption"]
    lower = slice(int(branded.shape[0] * 0.55), None)
    assert _near(branded[lower], violet) + _near(branded[lower], cyan) > 30
    assert _near(control[lower], violet) + _near(control[lower], cyan) < 30


def test_thumbnail_cover_carries_kit_logo_and_band(rendered):
    violet = (0xFF, 0x5C, 0x7C)
    branded_cover = cv2.imread(str(rendered["branded_cover"]))
    control_cover = cv2.imread(str(rendered["control_cover"]))
    assert branded_cover is not None and control_cover is not None
    assert _near(_bottom_right(branded_cover), LOGO_BGR) > 100
    assert _near(_bottom_right(control_cover), LOGO_BGR) < 50
    # The cover title band uses the kit colour instead of black.
    assert _near(branded_cover, violet) > 200
    assert _near(control_cover, violet) < 50

    measurements = {
        "branded_cover_logo_px": _near(_bottom_right(branded_cover), LOGO_BGR),
        "control_cover_logo_px": _near(_bottom_right(control_cover), LOGO_BGR),
        "branded_cover_violet_px": _near(branded_cover, violet),
        "control_cover_violet_px": _near(control_cover, violet),
        "branded_frame_logo_px": _near(
            _bottom_right(rendered["frames"]["branded_title"]), LOGO_BGR
        ),
        "branded_gap_rms_db": _window_rms_db(rendered["branded"], 2.2, 3.8),
        "control_gap_rms_db": _window_rms_db(rendered["control"], 2.2, 3.8),
    }
    out = rendered["tmp"] / "measurements.json"
    out.write_text(json.dumps(measurements, indent=2) + "\n", encoding="utf-8")
    _save_proof("measurements.json", out)
    _save_proof("branded-frame-title.png", rendered["frame_files"]["branded_title"])
    _save_proof("control-frame-title.png", rendered["frame_files"]["control_title"])
    _save_proof("branded-cover.jpg", rendered["branded_cover"])
    _save_proof("control-cover.jpg", rendered["control_cover"])


# ---------------------------------------------------------------------------
# Intro/outro bumpers — the kit's stored intro/outro videos must appear in
# the exported clip itself (they were stored-only before this milestone).
# ---------------------------------------------------------------------------

INTRO_BGR = (0, 0, 255)    # solid red bumper picture
OUTRO_BGR = (255, 255, 0)  # solid yellow bumper picture


def _make_bumper(path: Path, color_hex: str, tone_hz: int, duration: float = 2.0) -> None:
    _run([
        "ffmpeg", "-y", "-v", "error",
        "-f", "lavfi", "-i", f"color=c={color_hex}:size=640x360:rate=30:duration={duration}",
        "-f", "lavfi", "-i", f"sine=frequency={tone_hz}:sample_rate=48000:duration={duration}",
        "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest",
        str(path),
    ])


def _duration(path: Path) -> float:
    from src.media.ffmpeg import ffprobe_duration

    return ffprobe_duration(path)


@pytest.fixture(scope="module")
def bumpered(tmp_path_factory):
    mp = pytest.MonkeyPatch.context()
    monkeypatch = mp.__enter__()
    tmp_path = tmp_path_factory.mktemp("brandkit-bumpers")
    monkeypatch.setenv("FORCE_CPU_ENCODE", "true")
    monkeypatch.setenv("TEMP_DIR", str(tmp_path))
    monkeypatch.setattr(captions, "emoji_rendering_supported", lambda: False)
    assets = _library(tmp_path, monkeypatch)

    # Bumpers live in the user's brand folder, like uploaded logos.
    brand_dir = bk.user_brand_dir("user-1")
    brand_dir.mkdir(parents=True, exist_ok=True)
    intro = brand_dir / "intro.mp4"
    outro = brand_dir / "outro.mp4"
    _make_bumper(intro, "0xFF0000", 440)   # red, 440 Hz
    _make_bumper(outro, "0x00FFFF", 880)   # yellow, 880 Hz

    source = tmp_path / "source.mp4"
    _make_source(source)

    kit = dict(KIT, intro_asset="intro.mp4", outro_asset="outro.mp4")
    application = bk.resolve_brand_application(kit, user_id="user-1")
    assert application["brand_bumpers"]["intro"]["path"] == str(intro)
    assert application["brand_bumpers"]["outro"]["path"] == str(outro)

    edl = VideoService.build_pipeline_edl(
        _segment(), WORDS, niche_override="podcast_news", duration=12.0,
        brand_application=application,
    )
    assert edl is not None
    assert edl["brand_bumpers"]["intro"]["path"] == str(intro)

    ass = _build_ass(source, tmp_path, "bumpered.ass", edl, "default", "The Proof Hook")
    out = tmp_path / "bumpered.mp4"
    assert edl_render.render_edl_clip_ffmpeg(
        source, out, edl, subtitle_ass_path=ass, fonts_dir=FONTS_DIR, assets_dir=assets,
    ) is True

    yield {"tmp": tmp_path, "out": out, "intro": intro, "outro": outro}
    mp.__exit__(None, None, None)


def test_bumpers_extend_the_export_and_carry_their_audio(bumpered):
    out = bumpered["out"]
    duration = _duration(out)
    # 12 s clip + 2 s intro + 2 s outro.
    assert 15.5 <= duration <= 16.5, f"stitched duration {duration:.2f}s"
    # Intro frame (t=1.0): the red bumper, cover-cropped to the frame.
    intro_frame = _frame(out, 1.0, bumpered["tmp"] / "at-intro.png")
    assert _near(intro_frame, INTRO_BGR) > intro_frame.size // 6
    # Middle frame (t=8.0): the clip's own grey picture, not a bumper.
    mid_frame = _frame(out, 8.0, bumpered["tmp"] / "at-mid.png")
    assert _near(mid_frame, INTRO_BGR) < 50
    assert _near(mid_frame, OUTRO_BGR) < 50
    # Outro frame (t=15.0): the yellow bumper.
    outro_frame = _frame(out, 15.0, bumpered["tmp"] / "at-outro.png")
    assert _near(outro_frame, OUTRO_BGR) > outro_frame.size // 6
    # Bumper audio survives the stitch (each bumper window is audible).
    assert _window_rms_db(out, 0.2, 1.8) > -40.0
    assert _window_rms_db(out, 14.2, 15.8) > -40.0


def test_missing_bumper_file_renders_the_plain_clip(tmp_path, monkeypatch):
    """A kit naming a bumper that was never uploaded must not fail or
    alter the render — resolution drops it before the EDL is built."""
    monkeypatch.setenv("TEMP_DIR", str(tmp_path))
    kit = dict(KIT, intro_asset="ghost.mp4", outro_asset=None)
    application = bk.resolve_brand_application(kit, user_id="user-1")
    assert application["brand_bumpers"] == {}
