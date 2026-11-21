"""Brand Kit gap closures (real FFmpeg): frame-relative watermark size
and branded hook-variant previews.

GAP 1 — the watermark used to be scaled as ``scale=iw*<frac>`` on the
logo input stream, i.e. a fraction of the logo FILE's width: the same
kit rendered a huge watermark from a 2000 px logo and a tiny one from
a 200 px logo. Both consumers (the EDL renderer and the bundle cover
graph) now scale against a split copy of the picture itself
(``scale=w='rw*<frac>'``), so the rendered width is a fixed fraction
of the FRAME. Proof: the same clip rendered with the same logo stored
at 200 px and at 2000 px wide must show the same measured watermark
width; a no-kit control stays clean; the cover graph gets the same
treatment on a real one-frame render.

GAP 2 — hook-variant previews were built by
``ClipEditingMixin.generate_hook_previews`` without Brand Kit stamping, so
the preview lacked the title band, watermark and kit caption colours
that picking the variant produces (the apply path re-renders through
``rerender_clip``, which stamps). Both paths now share
``ClipEditingMixin._stamp_brand_blocks``. Proof: unit tests for the helper
(stamps / plain / no-kit / fail-safe), plus a frame pair — a
preview-shaped render (rerender EDL + stamp + preview truncation,
rendered with the same ASS arguments the preview path uses) vs the
full render at the same timestamp must both carry the band and the
watermark, while the same preview EDL without the stamp carries
neither.

The preview EDL is built with music_mode="off": the music chain is
orthogonal to branding (kit music reaches previews through the
creation-time music_override fill, covered elsewhere) and an empty
sandbox library keeps the Auto-bed path out of this proof.
"""

import asyncio
import json
import shutil
import subprocess
from pathlib import Path

import cv2
import numpy as np
import pytest

from src import video_utils
from src.auto_director import brand_kit as bk
from src.auto_director import hook_variants as hv
from src.deliverable_bundle import _cover_filter_graph
from src.font_registry import FONTS_DIR
from src.media import captions, edl_render, music_library
from src.media.captions import build_assemblyai_ass_subtitles
from src.media.edl_render import build_edl_filter_graph
from src.repositories.brand_kit_repository import BrandKitRepository
from src.services.clip_service import ClipEditingMixin
from src.services.video_service import VideoService

ffmpeg_missing = shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None
pytestmark = pytest.mark.skipif(ffmpeg_missing, reason="ffmpeg/ffprobe not installed")

PRIMARY = "#7C5CFF"  # kit primary (violet)
PRIMARY_BGR = (0xFF, 0x5C, 0x7C)
LOGO_BGR = (255, 0, 255)  # solid magenta logo, unmissable in frames
PROOF_DIR = Path.home() / "workspace" / "research_notes" / "brand-kit" / "proof-gaps"
HOOK_TITLE = "The Proof Hook"

KIT_BASE = {
    "id": "kit-proof",
    "user_id": "user-1",
    "name": "Proof Channel",
    "primary_color": PRIMARY,
    "accent_color": "#38E1FF",
    "caption_template": "hormozi",
    "font_name": None,
    "title_style": "question",
    "music_track": None,
    "logo_position": "bottom_right",
    "logo_opacity": 1.0,
    "intro_asset": None,
    "outro_asset": None,
}

WORDS = [
    {"text": text, "start": i * 0.4, "end": i * 0.4 + 0.35}
    for i, text in enumerate("hello brave new world this is the proof".split())
]

TASK = {
    "brand_kit_id": "kit-proof",
    "user_id": "user-1",
    "template_mode": None,
    "music_mode": "off",
    "music_override": None,
}


def _run(command):
    result = subprocess.run(command, capture_output=True, text=True, timeout=600)
    assert result.returncode == 0, result.stderr
    return result


def _make_source(path: Path) -> None:
    """12 s vertical grey source with speech bursts (house pattern:
    flat grey so any violet/magenta pixel came from the branding)."""
    _run([
        "ffmpeg", "-y", "-v", "error",
        "-f", "lavfi", "-i", "color=c=0x404040:size=320x568:rate=30:duration=12",
        "-f", "lavfi", "-i",
        "aevalsrc=0.5*sin(2*PI*700*t)*lt(mod(t\\,4)\\,2):s=48000:d=12",
        "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest",
        str(path),
    ])


def _store_logo_sized(tmp_path: Path, width: int, filename: str) -> str:
    raw = tmp_path / f"raw-{filename}"
    image = np.zeros((width // 2, width, 3), dtype=np.uint8)
    image[:, :] = LOGO_BGR
    assert cv2.imwrite(str(raw), image)
    stored_name = bk.store_logo("user-1", raw, filename)
    assert bk.logo_path("user-1", stored_name) is not None
    return stored_name


def _build_ass(source, tmp_path, name, edl, caption_template):
    ass_path = tmp_path / name
    ok = build_assemblyai_ass_subtitles(
        source, 0.0, 12.0, 1080, 1920, ass_path,
        caption_template=caption_template,
        hook_title=HOOK_TITLE,
        caption_words=WORDS,
        template_override=video_utils.brand_template_override(edl, caption_template),
        brand_title=(edl or {}).get("brand_title"),
    )
    assert ok is True
    return ass_path


def _render(source, out, edl, ass_path, assets):
    assert edl_render.render_edl_clip_ffmpeg(
        source, out, edl,
        subtitle_ass_path=ass_path, fonts_dir=FONTS_DIR, assets_dir=assets,
    ) is True
    return out


def _frame(video: Path, at_seconds: float, out: Path) -> np.ndarray:
    _run([
        "ffmpeg", "-y", "-v", "error", "-ss", str(at_seconds), "-i", str(video),
        "-frames:v", "1", str(out),
    ])
    image = cv2.imread(str(out))
    assert image is not None
    return image


def _near(image: np.ndarray, bgr, tol: int = 45) -> int:
    target = np.array(bgr, dtype=np.int16)
    diff = np.abs(image.astype(np.int16) - target)
    return int(np.all(diff <= tol, axis=2).sum())


def _logo_bbox(image: np.ndarray):
    """Bounding box (x0, y0, x1, y1) of logo-coloured pixels, or None."""
    target = np.array(LOGO_BGR, dtype=np.int16)
    diff = np.abs(image.astype(np.int16) - target)
    mask = np.all(diff <= 45, axis=2)
    ys, xs = np.nonzero(mask)
    if len(xs) < 50:
        return None
    return int(xs.min()), int(ys.min()), int(xs.max()), int(ys.max())


def _bottom_right(image: np.ndarray) -> np.ndarray:
    return image[int(image.shape[0] * 0.8):, int(image.shape[1] * 0.7):]


# ---------------------------------------------------------------------------
# GAP 1 — graph fragments (no render)
# ---------------------------------------------------------------------------

def _watermark_edl(tmp_path: Path) -> dict:
    logo = tmp_path / "logo.png"
    logo.write_bytes(b"png")
    return {
        "duration": 12,
        "segments": [{
            "start": 0, "end": 12,
            "transition_in": "none", "transition_duration": 0,
        }],
        "brand_watermark": {
            "path": str(logo), "position": "bottom_right",
            "opacity": 1.0, "width_frac": 0.16, "margin_frac": 0.03,
        },
    }


def test_gap1_edl_graph_scales_logo_against_the_frame(tmp_path):
    _, graph, _, _ = build_edl_filter_graph(
        _watermark_edl(tmp_path), tmp_path / "in.mp4"
    )
    logo_chain = [f for f in graph.split(";") if "brandlogo" in f]
    assert logo_chain, graph
    assert any("scale=w='rw*0.160'" in f for f in logo_chain)
    assert "scale=iw*0.160" not in graph  # the old logo-relative form
    assert any("split" in f and "brandref" in f for f in graph.split(";"))
    assert "brandwm" in graph


def test_gap1_cover_graph_scales_logo_against_the_frame(tmp_path):
    graph, extra = _cover_filter_graph("A Title", _watermark_edl(tmp_path))
    assert "scale=w='rw*0.160'" in graph
    assert "scale=iw*0.160" not in graph
    assert "[cover]split[coverbase][coverref]" in graph
    assert "[branded]" in graph and "brandlogo" in graph
    assert extra[0:2] == ["-loop", "1"]


# ---------------------------------------------------------------------------
# GAP 2 — the shared stamping helper
# ---------------------------------------------------------------------------

class _FakeRepo:
    def __init__(self, kit=None, error=None):
        self.kit = kit
        self.error = error
        self.calls = 0

    async def get_kit(self, db, user_id, kit_id):
        self.calls += 1
        if self.error is not None:
            raise self.error
        return self.kit


def _service_with_repo(monkeypatch, tmp_path, repo):
    monkeypatch.setenv("TEMP_DIR", str(tmp_path))
    stored = _store_logo_sized(tmp_path, 200, "logo.png")
    kit = {**KIT_BASE, "logo_filename": stored}
    if repo.kit is not None:
        repo.kit = kit
    monkeypatch.setattr(BrandKitRepository, "get_kit", repo.get_kit)
    service = object.__new__(ClipEditingMixin)
    service.db = None
    return service


def test_gap2_helper_stamps_all_brand_blocks(monkeypatch, tmp_path):
    repo = _FakeRepo(kit=True)  # replaced with the real kit dict below
    service = _service_with_repo(monkeypatch, tmp_path, repo)
    edl = {"hook": {"title": HOOK_TITLE}}
    asyncio.run(service._stamp_brand_blocks(edl, dict(TASK)))
    assert repo.calls == 1
    assert edl["brand_template_override"]["font_color"] == PRIMARY
    assert edl["brand_title"]  # band/accent block present
    assert edl["brand_watermark"]["width_frac"] == 0.16
    assert edl["hook"]["title_style"] == "question"


def test_gap2_helper_stamps_nothing_for_plain_task(monkeypatch, tmp_path):
    repo = _FakeRepo(kit=True)
    service = _service_with_repo(monkeypatch, tmp_path, repo)
    edl = {"hook": {}}
    asyncio.run(
        service._stamp_brand_blocks(edl, {**TASK, "template_mode": "plain"})
    )
    assert repo.calls == 0
    assert not any(key.startswith("brand_") for key in edl)


def test_gap2_helper_stamps_nothing_without_a_kit(monkeypatch, tmp_path):
    repo = _FakeRepo(kit=True)
    service = _service_with_repo(monkeypatch, tmp_path, repo)
    edl = {"hook": {}}
    asyncio.run(
        service._stamp_brand_blocks(edl, {**TASK, "brand_kit_id": None})
    )
    assert repo.calls == 0
    assert not any(key.startswith("brand_") for key in edl)


def test_gap2_helper_fails_safe_when_the_kit_cannot_load(monkeypatch, tmp_path):
    repo = _FakeRepo(error=RuntimeError("db down"))
    service = _service_with_repo(monkeypatch, tmp_path, repo)
    edl = {"hook": {}}
    asyncio.run(service._stamp_brand_blocks(edl, dict(TASK)))  # no raise
    assert not any(key.startswith("brand_") for key in edl)


# ---------------------------------------------------------------------------
# Real renders — GAP 1 sizes + GAP 2 preview parity
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def renders(tmp_path_factory):
    mp = pytest.MonkeyPatch.context()
    monkeypatch = mp.__enter__()
    tmp_path = tmp_path_factory.mktemp("brandkit-gaps")
    monkeypatch.setenv("FORCE_CPU_ENCODE", "true")
    monkeypatch.setenv("TEMP_DIR", str(tmp_path))
    monkeypatch.setattr(captions, "emoji_rendering_supported", lambda: False)
    assets = tmp_path / "assets"
    (assets / "music").mkdir(parents=True)
    (assets / "sfx").mkdir(parents=True)
    user_dir = tmp_path / "user_music"
    user_dir.mkdir()
    monkeypatch.setattr(edl_render, "ASSETS_DIR", assets)
    monkeypatch.setattr(music_library, "user_music_dir", lambda: user_dir)

    small_logo = _store_logo_sized(tmp_path, 200, "logo-small.png")
    big_logo = _store_logo_sized(tmp_path, 2000, "logo-big.png")
    kit_small = {**KIT_BASE, "logo_filename": small_logo}
    kit_big = {**KIT_BASE, "logo_filename": big_logo}

    source = tmp_path / "source.mp4"
    _make_source(source)
    segment = {"start_time": "00:00", "end_time": "00:12", "text": "a podcast segment"}

    def pipeline_render(kit, name):
        edl = VideoService.build_pipeline_edl(
            segment, WORDS, niche_override="podcast_news", duration=12.0,
            brand_application=(
                bk.resolve_brand_application(kit, user_id="user-1") if kit else None
            ),
        )
        assert edl is not None
        template = kit["caption_template"] if kit else "default"
        ass = _build_ass(source, tmp_path, f"{name}.ass", edl, template)
        return _render(source, tmp_path / f"{name}.mp4", edl, ass, assets), edl

    control, _ = pipeline_render(None, "control")
    full_small, _ = pipeline_render(kit_small, "full-small")
    full_big, _ = pipeline_render(kit_big, "full-big")

    # Preview-shaped renders: rerender EDL (the builder previews use),
    # with and without the shared brand stamp, truncated like a preview.
    repo = _FakeRepo(kit=kit_small)
    monkeypatch.setattr(BrandKitRepository, "get_kit", repo.get_kit)
    service = object.__new__(ClipEditingMixin)
    service.db = None
    clip = {"id": "clip-proof", "text": "hello brave new world this is the proof"}

    def preview_render(stamp: bool, name: str):
        edl = ClipEditingMixin._build_rerender_edl(
            clip, 0.0, 12.0, "podcast_news", None, None, None, "off", None
        )
        if stamp:
            asyncio.run(service._stamp_brand_blocks(edl, dict(TASK)))
            assert edl.get("brand_watermark") and edl.get("brand_title")
        else:
            assert not any(key.startswith("brand_") for key in edl)
        hv.truncate_edl_for_preview(edl)
        ass = _build_ass(source, tmp_path, f"{name}.ass", edl, "hormozi")
        return _render(source, tmp_path / f"{name}.mp4", edl, ass, assets), edl

    preview, preview_edl = preview_render(True, "preview")
    preview_plain, _ = preview_render(False, "preview-unstyled")

    frames = {
        name: _frame(video, 1.0, tmp_path / f"frame-{name}.png")
        for name, video in [
            ("control", control), ("full-small", full_small),
            ("full-big", full_big), ("preview", preview),
            ("preview-unstyled", preview_plain),
        ]
    }
    frames["full-small-mid"] = _frame(full_small, 4.0, tmp_path / "frame-full-small-mid.png")
    frames["full-big-mid"] = _frame(full_big, 4.0, tmp_path / "frame-full-big-mid.png")
    frames["control-mid"] = _frame(control, 4.0, tmp_path / "frame-control-mid.png")

    measurements = {
        "watermark_bbox_full_small": _logo_bbox(frames["full-small-mid"]),
        "watermark_bbox_full_big": _logo_bbox(frames["full-big-mid"]),
        "control_logo_pixels": _near(frames["control-mid"], LOGO_BGR),
        "band_pixels_full": _near(frames["full-small"], PRIMARY_BGR),
        "band_pixels_preview": _near(frames["preview"], PRIMARY_BGR),
        "band_pixels_preview_unstamped": _near(frames["preview-unstyled"], PRIMARY_BGR),
        "logo_pixels_preview_corner": _near(_bottom_right(frames["preview"]), LOGO_BGR),
        "logo_pixels_unstamped_corner": _near(
            _bottom_right(frames["preview-unstyled"]), LOGO_BGR
        ),
    }
    try:
        PROOF_DIR.mkdir(parents=True, exist_ok=True)
        for name in ("full-small", "preview"):
            cv2.imwrite(str(PROOF_DIR / f"gaps-{name}-t1.png"), frames[name])
        (PROOF_DIR / "gaps-measurements.json").write_text(
            json.dumps(measurements, indent=2, default=str)
        )
    except OSError:
        pass  # evidence copy is best-effort; assertions are the proof

    yield {"frames": frames, "measurements": measurements,
           "preview_edl": preview_edl, "tmp_path": tmp_path,
           "source": source, "kit_small": kit_small}
    mp.__exit__(None, None, None)


def _bbox_width(bbox) -> int:
    assert bbox is not None
    return bbox[2] - bbox[0] + 1


def test_gap1_watermark_width_matches_for_200px_and_2000px_logos(renders):
    m = renders["measurements"]
    width_small = _bbox_width(m["watermark_bbox_full_small"])
    width_big = _bbox_width(m["watermark_bbox_full_big"])
    # 0.16 of the 1080-wide frame = ~173 px, whatever the file size.
    assert abs(width_small - 173) <= 5, width_small
    assert abs(width_big - 173) <= 5, width_big
    assert abs(width_small - width_big) <= 3


def test_gap1_no_kit_control_has_no_watermark(renders):
    assert renders["measurements"]["control_logo_pixels"] < 50


def test_gap1_cover_watermark_matches_for_both_logo_sizes(renders):
    tmp_path = renders["tmp_path"]
    widths = {}
    for label, logo_name in (("small", "logo-small.png"), ("big", "logo-big.png")):
        logo = bk.logo_path("user-1", logo_name)
        edl = {"brand_watermark": {
            "path": str(logo), "position": "bottom_right", "opacity": 1.0,
            "width_frac": 0.16, "margin_frac": 0.03,
        }}
        graph, extra = _cover_filter_graph("Proof Cover", edl)
        out = tmp_path / f"cover-{label}.png"
        _run([
            "ffmpeg", "-y", "-v", "error", "-ss", "4",
            "-i", str(tmp_path / "full-small.mp4"),
            *extra, "-filter_complex", graph, "-map", "[branded]",
            "-frames:v", "1", str(out),
        ])
        image = cv2.imread(str(out))
        assert image is not None
        widths[label] = _bbox_width(_logo_bbox(image))
    assert abs(widths["small"] - 173) <= 5, widths
    assert abs(widths["big"] - 173) <= 5, widths
    assert abs(widths["small"] - widths["big"]) <= 3


def test_gap2_preview_frame_carries_the_same_branding_as_the_full_render(renders):
    m = renders["measurements"]
    # Title band (kit primary) present in both, absent without the stamp.
    assert m["band_pixels_full"] > 500
    assert m["band_pixels_preview"] > 500
    assert m["band_pixels_preview_unstamped"] < 50
    # Watermark in the preview's bottom-right corner, like the export.
    assert m["logo_pixels_preview_corner"] > 1000
    assert m["logo_pixels_unstamped_corner"] < 50


def test_gap2_preview_edl_uses_the_kit_caption_treatment(renders):
    # The ASS the preview path builds from the stamped EDL carries the
    # kit's colours via the template override (same seam as the export).
    override = video_utils.brand_template_override(
        renders["preview_edl"], "hormozi"
    )
    assert override is not None
    assert override["font_color"] == PRIMARY
