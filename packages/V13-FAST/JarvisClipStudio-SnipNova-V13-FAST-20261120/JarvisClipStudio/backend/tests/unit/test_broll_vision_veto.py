"""B-roll vision veto: SigLIP second opinion on Pixabay hits.

The resolver wiring is exercised with a scripted fake session and an
httpx MockTransport (no network, no model) — the live-model behaviour
(scores, threshold calibration) is proven separately in the lane's
SIGLIP_VETO_REPORT.md. Frame decoding tests use real FFmpeg on tiny
synthetic media.
"""

import shutil
import subprocess
from pathlib import Path

import httpx
import pytest

from src.media import broll_vision, edl_render, pixabay_broll


@pytest.fixture(autouse=True)
def _clear_caches():
    pixabay_broll._search_cache.clear()
    broll_vision._score_cache.clear()
    yield
    pixabay_broll._search_cache.clear()
    broll_vision._score_cache.clear()


@pytest.fixture()
def api_key(monkeypatch):
    monkeypatch.setattr(pixabay_broll, "_api_key", lambda: "test-key")


@pytest.fixture()
def veto_flag(monkeypatch):
    monkeypatch.setenv("BROLL_VISION_VETO_ENABLED", "true")


# ---------------------------------------------------------------------------
# Flag, threshold, decision purity
# ---------------------------------------------------------------------------


def test_flag_default_off(monkeypatch):
    monkeypatch.delenv("BROLL_VISION_VETO_ENABLED", raising=False)
    assert broll_vision.vision_veto_enabled() is False
    assert broll_vision.VisionVetoSession().available is False


@pytest.mark.parametrize("value", ["1", "true", "TRUE", "on", "yes"])
def test_flag_on_values(monkeypatch, value):
    monkeypatch.setenv("BROLL_VISION_VETO_ENABLED", value)
    assert broll_vision.vision_veto_enabled() is True


@pytest.mark.parametrize("value", ["0", "false", "off", "no", "", "banana"])
def test_flag_off_values(monkeypatch, value):
    monkeypatch.setenv("BROLL_VISION_VETO_ENABLED", value)
    assert broll_vision.vision_veto_enabled() is False


def test_threshold_default_and_override(monkeypatch):
    monkeypatch.delenv("BROLL_VISION_VETO_THRESHOLD", raising=False)
    assert broll_vision.vision_veto_threshold() == broll_vision.DEFAULT_THRESHOLD
    monkeypatch.setenv("BROLL_VISION_VETO_THRESHOLD", "0.23")
    assert broll_vision.vision_veto_threshold() == pytest.approx(0.23)
    monkeypatch.setenv("BROLL_VISION_VETO_THRESHOLD", "banana")
    assert broll_vision.vision_veto_threshold() == broll_vision.DEFAULT_THRESHOLD
    monkeypatch.setenv("BROLL_VISION_VETO_THRESHOLD", "7")
    assert broll_vision.vision_veto_threshold() == broll_vision.DEFAULT_THRESHOLD


def test_concept_threshold_default_and_override(monkeypatch):
    """Concept sentences score lower than bare nouns, so they carry
    their own calibrated threshold (Slice 4: 0.006, not 0.048)."""
    monkeypatch.delenv("BROLL_VISION_VETO_CONCEPT_THRESHOLD", raising=False)
    assert broll_vision.vision_veto_threshold(concept=True) == (
        broll_vision.DEFAULT_CONCEPT_THRESHOLD
    )
    assert broll_vision.DEFAULT_CONCEPT_THRESHOLD < broll_vision.DEFAULT_THRESHOLD
    monkeypatch.setenv("BROLL_VISION_VETO_CONCEPT_THRESHOLD", "0.02")
    assert broll_vision.vision_veto_threshold(concept=True) == pytest.approx(0.02)
    # The concept override never leaks into the noun threshold.
    assert broll_vision.vision_veto_threshold() == broll_vision.DEFAULT_THRESHOLD
    monkeypatch.setenv("BROLL_VISION_VETO_CONCEPT_THRESHOLD", "banana")
    assert broll_vision.vision_veto_threshold(concept=True) == (
        broll_vision.DEFAULT_CONCEPT_THRESHOLD
    )
    monkeypatch.setenv("BROLL_VISION_VETO_CONCEPT_THRESHOLD", "7")
    assert broll_vision.vision_veto_threshold(concept=True) == (
        broll_vision.DEFAULT_CONCEPT_THRESHOLD
    )


def test_should_veto_is_fail_open_and_strict():
    threshold = broll_vision.DEFAULT_THRESHOLD
    assert broll_vision.should_veto(None, threshold) is False  # unjudged
    assert broll_vision.should_veto(threshold - 0.01, threshold) is True
    assert broll_vision.should_veto(threshold, threshold) is False
    assert broll_vision.should_veto(threshold + 0.2, threshold) is False


# ---------------------------------------------------------------------------
# Device gate (mirrors the separation gate's rules)
# ---------------------------------------------------------------------------


def test_device_cpu_without_cuda(monkeypatch):
    monkeypatch.setattr(broll_vision, "_cuda_available", lambda: False)
    assert broll_vision._vision_device() == "cpu"


def test_device_cuda_with_room_or_unknown_probe(monkeypatch):
    monkeypatch.setattr(broll_vision, "_cuda_available", lambda: True)
    monkeypatch.setattr(broll_vision, "_cuda_free_vram_bytes", lambda: 6 * 1024**3)
    assert broll_vision._vision_device() == "cuda"
    monkeypatch.setattr(broll_vision, "_cuda_free_vram_bytes", lambda: None)
    assert broll_vision._vision_device() == "cuda"  # unknown -> presence-only


def test_device_skips_when_card_is_busy(monkeypatch):
    monkeypatch.setattr(broll_vision, "_cuda_available", lambda: True)
    monkeypatch.setattr(broll_vision, "_cuda_free_vram_bytes", lambda: 512 * 1024**2)
    assert broll_vision._vision_device() is None


def test_session_unavailable_when_device_declines(monkeypatch, veto_flag):
    monkeypatch.setattr(broll_vision, "_vision_device", lambda: None)
    assert broll_vision.VisionVetoSession().available is False


def test_session_available_on_cpu(monkeypatch, veto_flag):
    monkeypatch.setattr(broll_vision, "_vision_device", lambda: "cpu")
    session = broll_vision.VisionVetoSession()
    assert session.available is True
    # No frames / no subject -> unjudged, and the model never loads.
    assert session.score([], "ocean") is None
    assert session.score([object()], "") is None
    session.close()


# ---------------------------------------------------------------------------
# Scoring math against a stub model (no weights involved)
# ---------------------------------------------------------------------------


class _FakeTokenizer:
    def __call__(self, texts, padding=None, return_tensors=None):
        import torch

        return {"input_ids": torch.zeros((len(texts), 4), dtype=torch.long)}


class _FakeOutput:
    def __init__(self, image_embeds, text_embeds):
        self.image_embeds = image_embeds
        self.text_embeds = text_embeds


class _FakeModel:
    """Embeddings pinned so the cosines are known exactly."""

    def __call__(self, pixel_values=None, input_ids=None):
        import torch

        # Two frames: one parallel to the text, one orthogonal.
        image = torch.tensor([[1.0, 0.0], [0.0, 1.0]])[: pixel_values.shape[0]]
        return _FakeOutput(image, torch.tensor([[1.0, 0.0]]))


def test_score_takes_max_over_frames(monkeypatch):
    import numpy as np

    session = broll_vision.VisionVetoSession.__new__(broll_vision.VisionVetoSession)
    session.available = True
    session._device = "cpu"
    session._model = _FakeModel()
    session._tokenizer = _FakeTokenizer()
    session._load_attempted = True
    session._load_failed = False

    frames = [np.zeros((224, 224, 3), dtype=np.uint8) for _ in range(2)]
    assert session.score(frames, "ocean") == pytest.approx(1.0)
    assert session.score(frames[:1], "ocean") == pytest.approx(1.0)


# ---------------------------------------------------------------------------
# Frame decoding (real FFmpeg, tiny synthetic media)
# ---------------------------------------------------------------------------


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg required")
def test_frame_decoding_from_image_bytes_and_video(tmp_path):
    image = tmp_path / "still.png"
    subprocess.run(
        ["ffmpeg", "-y", "-f", "lavfi", "-i", "color=c=red:size=320x240",
         "-frames:v", "1", str(image)],
        check=True, capture_output=True, timeout=60,
    )
    frame = broll_vision.frame_rgb_from_image_bytes(image.read_bytes())
    assert frame is not None and frame.shape == (224, 224, 3)
    assert frame[:, :, 0].mean() > 200 and frame[:, :, 1].mean() < 60

    video = tmp_path / "clip.mp4"
    subprocess.run(
        ["ffmpeg", "-y", "-f", "lavfi", "-i", "color=c=blue:size=320x240:rate=10:duration=2",
         "-c:v", "libx264", "-pix_fmt", "yuv420p", str(video)],
        check=True, capture_output=True, timeout=60,
    )
    frames = broll_vision.candidate_frames(preview_path=video, duration=2.0)
    assert len(frames) == 3
    assert all(f.shape == (224, 224, 3) for f in frames)
    assert frames[0][:, :, 2].mean() > 200

    assert broll_vision.frame_rgb_from_image_bytes(b"not an image") is None
    assert broll_vision.candidate_frames() == []


# ---------------------------------------------------------------------------
# Resolver wiring with a scripted fake session
# ---------------------------------------------------------------------------


class _FakeSession:
    """score() reads the asset id the frames sentinel carries."""

    def __init__(self, scores):
        self.available = True
        self.scores = scores
        self.calls = []
        self.closed = False

    def score(self, frames, subject):
        asset_id = frames[0] if frames else None
        self.calls.append(asset_id)
        return self.scores.get(asset_id)

    def close(self):
        self.closed = True


def _hit(asset_id, tags, base):
    return {
        "id": asset_id,
        "pageURL": f"https://pixabay.com/videos/{asset_id}",
        "duration": 8,
        "tags": tags,
        "videos": {
            "tiny": {"url": f"{base}/tiny{asset_id}.mp4", "width": 426, "height": 240, "size": 100},
            "large": {"url": f"{base}/large{asset_id}.mp4", "width": 1920, "height": 1080, "size": 9000},
        },
    }


def _two_hit_payload():
    return {"total": 2, "totalHits": 2, "hits": [
        _hit(11, "ocean, waves, sea", "https://cdn.pixabay.com"),
        _hit(22, "ocean, beach, waves", "https://cdn.pixabay.com"),
    ]}


def _mock_client(handler):
    return httpx.Client(transport=httpx.MockTransport(handler))


@pytest.fixture()
def sentinel_frames(monkeypatch):
    """Frames carry the asset id so the fake session can script scores."""
    monkeypatch.setattr(
        pixabay_broll,
        "_candidate_frames_for_hit",
        lambda hit, *, client, cache_dir: [hit["asset_id"]],
    )


def _counting_handler(payload, downloads):
    def handler(request):
        url = str(request.url)
        if "pixabay.com/api" in url:
            return httpx.Response(200, json=payload)
        downloads.append(url)
        return httpx.Response(200, content=b"video-bytes")

    return handler


def test_search_normalises_preview_url(api_key):
    payload = {"total": 1, "totalHits": 1, "hits": [
        _hit(7, "ocean", "https://cdn.pixabay.com"),
    ]}

    def handler(request):
        return httpx.Response(200, json=payload)

    results = pixabay_broll.search_pixabay_videos("ocean", client=_mock_client(handler))
    assert results[0]["preview_url"] == "https://cdn.pixabay.com/tiny7.mp4"
    assert results[0]["thumbnail_url"] == ""


def test_vetoed_hit_falls_through_to_next(api_key, tmp_path, sentinel_frames):
    downloads = []
    client = _mock_client(_counting_handler(_two_hit_payload(), downloads))
    session = _FakeSession({11: 0.01, 22: 0.50})
    sink: dict = {}

    asset = pixabay_broll.ensure_pixabay_asset(
        "ocean", cache_dir=tmp_path, client=client,
        veto_session=session, veto_sink=sink,
    )

    assert asset is not None and asset["asset_id"] == 22
    assert sink["vetoed"] == [11]
    assert sink["scores"] == {"11": 0.01, "22": 0.5}
    assert sink["winner_score"] == 0.5
    # The vetoed asset's full rendition was never downloaded.
    assert not any("large11" in url for url in downloads)
    assert any("large22" in url for url in downloads)


def test_all_hits_vetoed_skips_the_cue(api_key, tmp_path, sentinel_frames):
    downloads = []
    client = _mock_client(_counting_handler(_two_hit_payload(), downloads))
    session = _FakeSession({11: 0.01, 22: 0.02})
    sink: dict = {}

    asset = pixabay_broll.ensure_pixabay_asset(
        "ocean", cache_dir=tmp_path, client=client,
        veto_session=session, veto_sink=sink,
    )

    assert asset is None
    assert sink["vetoed"] == [11, 22]


def test_unjudged_hits_are_accepted_fail_open(api_key, tmp_path, sentinel_frames):
    downloads = []
    client = _mock_client(_counting_handler(_two_hit_payload(), downloads))
    session = _FakeSession({})  # score() returns None for everything
    sink: dict = {}

    asset = pixabay_broll.ensure_pixabay_asset(
        "ocean", cache_dir=tmp_path, client=client,
        veto_session=session, veto_sink=sink,
    )

    assert asset is not None and asset["asset_id"] == 11
    assert sink == {}


def test_budget_exhaustion_stops_checking(api_key, tmp_path, sentinel_frames, monkeypatch):
    monkeypatch.setattr(broll_vision, "vision_veto_budget_seconds", lambda: 0.0)
    downloads = []
    client = _mock_client(_counting_handler(_two_hit_payload(), downloads))
    session = _FakeSession({11: 0.01, 22: 0.01})

    asset = pixabay_broll.ensure_pixabay_asset(
        "ocean", cache_dir=tmp_path, client=client, veto_session=session,
    )

    assert asset is not None and asset["asset_id"] == 11
    assert session.calls == []  # never even asked


def test_resolve_stamps_veto_evidence(api_key, tmp_path, sentinel_frames, monkeypatch):
    session = _FakeSession({11: 0.01, 22: 0.50})
    monkeypatch.setattr(broll_vision, "VisionVetoSession", lambda: session)
    downloads = []
    client = _mock_client(_counting_handler(_two_hit_payload(), downloads))
    edl = {"broll": [{"query": "ocean", "local_path": "", "asset_id": None}]}

    resolved = pixabay_broll.resolve_edl_broll(edl, cache_dir=tmp_path, client=client)

    assert resolved == 1
    entry = edl["broll"][0]
    assert entry["asset_id"] == 22
    assert entry["vision_veto"]["checked"] is True
    assert entry["vision_veto"]["vetoed"] == [11]
    assert entry["vision_veto"]["winner_score"] == 0.5
    assert session.closed is True


def test_resolve_never_builds_a_session_for_plain(
    api_key, tmp_path, sentinel_frames, monkeypatch
):
    built = []

    def _recorder():
        built.append(1)
        return _FakeSession({11: 0.01})

    monkeypatch.setattr(broll_vision, "VisionVetoSession", _recorder)
    downloads = []
    client = _mock_client(_counting_handler(_two_hit_payload(), downloads))
    edl = {"template": "plain", "broll": [{"query": "ocean", "local_path": "", "asset_id": None}]}

    resolved = pixabay_broll.resolve_edl_broll(edl, cache_dir=tmp_path, client=client)

    assert resolved == 1
    assert built == []  # Plain: no session, no veto, first hit wins
    assert edl["broll"][0]["asset_id"] == 11
    assert "vision_veto" not in edl["broll"][0]


def test_resolve_without_flag_builds_no_real_session(
    api_key, tmp_path, sentinel_frames, monkeypatch
):
    monkeypatch.delenv("BROLL_VISION_VETO_ENABLED", raising=False)
    downloads = []
    client = _mock_client(_counting_handler(_two_hit_payload(), downloads))
    edl = {"broll": [{"query": "ocean", "local_path": "", "asset_id": None}]}

    resolved = pixabay_broll.resolve_edl_broll(edl, cache_dir=tmp_path, client=client)

    assert resolved == 1
    assert edl["broll"][0]["asset_id"] == 11
    assert "vision_veto" not in edl["broll"][0]


# ---------------------------------------------------------------------------
# Render-level: the vetoed stock never reaches the frame
# ---------------------------------------------------------------------------


def _make_video(path: Path, color: str, duration: int):
    subprocess.run(
        [
            "ffmpeg", "-y",
            "-f", "lavfi", "-i", f"color=c={color}:size=320x240:rate=30:duration={duration}",
            "-f", "lavfi", "-i", f"sine=frequency=440:sample_rate=48000:duration={duration}",
            "-shortest", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac",
            str(path),
        ],
        check=True, capture_output=True, timeout=120,
    )


def _frame_rgb(path: Path, at: float) -> tuple[float, float, float]:
    result = subprocess.run(
        [
            "ffmpeg", "-v", "error", "-ss", str(at), "-i", str(path),
            "-frames:v", "1", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", "64x64", "-",
        ],
        check=True, capture_output=True, timeout=120,
    )
    data = result.stdout
    n = len(data) // 3
    return (sum(data[0::3]) / n, sum(data[1::3]) / n, sum(data[2::3]) / n)


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg required")
def test_render_uses_fallthrough_hit_not_the_vetoed_one(
    api_key, tmp_path, sentinel_frames, monkeypatch
):
    red = tmp_path / "red.mp4"
    _make_video(red, "red", 4)
    blue = tmp_path / "blue.mp4"
    _make_video(blue, "blue", 4)
    bodies = {"large11": red.read_bytes(), "large22": blue.read_bytes()}

    def handler(request):
        url = str(request.url)
        if "pixabay.com/api" in url:
            return httpx.Response(200, json=_two_hit_payload())
        for name, body in bodies.items():
            if name in url:
                return httpx.Response(200, content=body)
        return httpx.Response(200, content=b"preview-bytes")

    session = _FakeSession({11: 0.01, 22: 0.50})
    monkeypatch.setattr(broll_vision, "VisionVetoSession", lambda: session)

    edl = {
        "duration": 6,
        "segments": [{"start": 0, "end": 6, "transition_in": "none", "transition_duration": 0}],
        "overlays": [],
        "broll": [{
            "query": "ocean", "start": 2.0, "end": 5.0,
            "local_path": "", "asset_id": None, "source_start": 0.0,
        }],
    }
    client = _mock_client(handler)
    assert pixabay_broll.resolve_edl_broll(edl, cache_dir=tmp_path, client=client) == 1
    entry = edl["broll"][0]
    assert entry["asset_id"] == 22
    assert entry["vision_veto"]["vetoed"] == [11]

    source = tmp_path / "source.mp4"
    _make_video(source, "green", 6)
    output = tmp_path / "out.mp4"
    assert edl_render.render_edl_clip_ffmpeg(source, output, edl) is True

    r_before, g_before, _ = _frame_rgb(output, 0.5)
    r_in, _, b_in = _frame_rgb(output, 3.5)
    assert g_before > 120  # speaker (green) before the window
    assert b_in > 120 and r_in < 90  # the BLUE fall-through hit, not the vetoed red


# ---------------------------------------------------------------------------
# Smart B-Roll v2, Slice 4: concept subject + contrastive avoid
# ---------------------------------------------------------------------------


def test_should_veto_contrastive_threshold_and_avoid():
    threshold = broll_vision.DEFAULT_THRESHOLD
    # Below threshold vetoes even when the avoid score is lower.
    assert broll_vision.should_veto_contrastive(0.01, 0.001, threshold) is True
    # Above threshold but looking more like the wrong sense vetoes.
    assert broll_vision.should_veto_contrastive(0.50, 0.60, threshold) is True
    # Above threshold and looking more like the right sense keeps.
    assert broll_vision.should_veto_contrastive(0.50, 0.10, threshold) is False
    # Exactly at threshold with the avoid side higher still vetoes.
    assert broll_vision.should_veto_contrastive(threshold, threshold + 0.1, threshold) is True
    # Exactly at threshold with the avoid side lower keeps.
    assert broll_vision.should_veto_contrastive(threshold, threshold - 0.01, threshold) is False


def test_should_veto_contrastive_is_fail_open():
    threshold = broll_vision.DEFAULT_THRESHOLD
    # Unjudged concept never vetoes, whatever the avoid side says.
    assert broll_vision.should_veto_contrastive(None, 0.90, threshold) is False
    assert broll_vision.should_veto_contrastive(None, None, threshold) is False
    # Unjudged avoid degrades to the plain threshold check.
    assert broll_vision.should_veto_contrastive(0.01, None, threshold) is True
    assert broll_vision.should_veto_contrastive(0.50, None, threshold) is False


def test_score_cache_accepts_string_asset_ids():
    """Coverr asset ids are strings ("AbC123"); the cache must not
    int() them — that used to raise ValueError inside the veto check
    and break Coverr resolution for the whole cue (Slice 4 find)."""
    broll_vision.store_score("AbC123", "Some Concept", 0.42)
    assert broll_vision.cached_score("AbC123", "some concept") == pytest.approx(0.42)
    assert broll_vision.cached_score("AbC123", "other subject") is None
    broll_vision.store_score(11, "Ocean", 0.5)
    assert broll_vision.cached_score(11, "ocean") == pytest.approx(0.5)
    assert broll_vision.cached_score("11", "ocean") == pytest.approx(0.5)


class _SubjectSession:
    """score() reads (asset id, subject) pairs from a scripted table."""

    def __init__(self, scores):
        self.available = True
        self.scores = scores
        self.calls = []
        self.closed = False

    def score(self, frames, subject):
        asset_id = frames[0] if frames else None
        self.calls.append((asset_id, subject))
        return self.scores.get((asset_id, subject))

    def close(self):
        self.closed = True


def _market_concept_payload():
    return {"total": 2, "totalHits": 2, "hits": [
        _hit(11, "stock market, chart, trading", "https://cdn.pixabay.com"),
        _hit(22, "stock market, candlestick, graph", "https://cdn.pixabay.com"),
    ]}


def test_concept_cue_is_judged_on_the_concept_sentence(api_key, tmp_path, sentinel_frames):
    """The veto subject is the concept, not the search string; a hit
    that looks more like the wrong sense is vetoed even above threshold."""
    concept = "Candlestick charts and a profit and loss graph on a trader's monitors."
    avoid = ["street market", "farmers market stalls"]
    avoid_subject = "street market, farmers market stalls"
    session = _SubjectSession({
        (11, concept): 0.50, (11, avoid_subject): 0.60,  # right-side high, wrong-side higher
        (22, concept): 0.55, (22, avoid_subject): 0.10,
    })
    downloads = []
    client = _mock_client(_counting_handler(_market_concept_payload(), downloads))
    sink: dict = {}

    asset = pixabay_broll.ensure_pixabay_asset(
        "market", cache_dir=tmp_path, client=client,
        context="trade worth profit loss strategy",
        search_query="market strategy",
        concept=concept, avoid=avoid,
        search_queries=["stock market chart screen", "trading desk monitors"],
        veto_session=session, veto_sink=sink,
    )

    assert asset is not None and asset["asset_id"] == 22
    assert sink["vetoed"] == [11]
    assert sink["scores"] == {"11": 0.5, "22": 0.55}
    assert sink["avoid_scores"] == {"11": 0.6, "22": 0.1}
    assert sink["winner_score"] == 0.55
    # The session was asked about the concept and the avoid text — never
    # about the search string or the bare noun.
    subjects = {subject for _, subject in session.calls}
    assert concept in subjects and avoid_subject in subjects
    assert "market strategy" not in subjects and "market" not in subjects
    # The vetoed asset's full rendition was never downloaded.
    assert not any("large11" in url for url in downloads)
    assert any("large22" in url for url in downloads)


def test_concept_cue_without_avoid_uses_concept_threshold(api_key, tmp_path, sentinel_frames):
    """No avoid list: the veto degrades to the concept threshold alone
    (0.006) — a hit at 0.001 is vetoed, one at 0.50 wins."""
    concept = "Candlestick charts and a profit and loss graph on a trader's monitors."
    session = _SubjectSession({(11, concept): 0.001, (22, concept): 0.50})
    downloads = []
    client = _mock_client(_counting_handler(_market_concept_payload(), downloads))
    sink: dict = {}

    asset = pixabay_broll.ensure_pixabay_asset(
        "market", cache_dir=tmp_path, client=client,
        context="trade worth profit loss strategy",
        search_query="market strategy",
        concept=concept, avoid=[],
        search_queries=["stock market chart screen"],
        veto_session=session, veto_sink=sink,
    )

    assert asset is not None and asset["asset_id"] == 22
    assert sink["vetoed"] == [11]
    assert "avoid_scores" not in sink
    assert sink["winner_score"] == 0.5


def test_non_concept_cue_keeps_the_search_string_subject(api_key, tmp_path, sentinel_frames):
    """No concept fields: the veto subject stays the sense-resolved
    search string, exactly as before Slice 4 (regression pin)."""
    session = _SubjectSession({(11, "ocean waves"): 0.01, (22, "ocean waves"): 0.50})
    downloads = []
    client = _mock_client(_counting_handler(_two_hit_payload(), downloads))
    sink: dict = {}

    asset = pixabay_broll.ensure_pixabay_asset(
        "ocean", cache_dir=tmp_path, client=client,
        search_query="ocean waves",
        veto_session=session, veto_sink=sink,
    )

    assert asset is not None and asset["asset_id"] == 22
    assert sink["vetoed"] == [11]
    subjects = {subject for _, subject in session.calls}
    assert subjects == {"ocean waves"}


def test_concept_cue_uses_the_concept_threshold(api_key, tmp_path, sentinel_frames):
    """A concept hit scoring 0.02 — below the noun threshold 0.048 but
    above the concept threshold 0.006 — is KEPT: the old threshold
    would have false-vetoed 9/44 true matches on concept subjects
    (Slice 4 calibration)."""
    concept = "Candlestick charts and a profit and loss graph on a trader's monitors."
    session = _SubjectSession({(11, concept): 0.02, (11, "street market"): 0.01})
    downloads = []
    client = _mock_client(_counting_handler(_market_concept_payload(), downloads))
    sink: dict = {}

    asset = pixabay_broll.ensure_pixabay_asset(
        "market", cache_dir=tmp_path, client=client,
        context="trade worth profit loss strategy",
        search_query="market strategy",
        concept=concept, avoid=["street market"],
        search_queries=["stock market chart screen"],
        veto_session=session, veto_sink=sink,
    )

    assert asset is not None and asset["asset_id"] == 11
    assert sink.get("vetoed", []) == []
    assert sink["winner_score"] == 0.02


def test_non_concept_cue_still_uses_the_noun_threshold(api_key, tmp_path, sentinel_frames):
    """The same 0.02 score on a search-string subject IS vetoed: the
    noun threshold 0.048 still governs cues without a concept, so the
    planted suite (mismatches <= 0.0442) does not regress."""
    session = _SubjectSession({(11, "ocean waves"): 0.02, (22, "ocean waves"): 0.50})
    downloads = []
    client = _mock_client(_counting_handler(_two_hit_payload(), downloads))
    sink: dict = {}

    asset = pixabay_broll.ensure_pixabay_asset(
        "ocean", cache_dir=tmp_path, client=client,
        search_query="ocean waves",
        veto_session=session, veto_sink=sink,
    )

    assert asset is not None and asset["asset_id"] == 22
    assert sink["vetoed"] == [11]
