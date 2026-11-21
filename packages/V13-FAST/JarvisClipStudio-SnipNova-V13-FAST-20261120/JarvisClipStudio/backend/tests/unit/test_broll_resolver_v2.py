"""Smart B-Roll v2, Slice 3: resolver v2 — concept-driven stock search.

The design's Slice-3 contract (SMART_BROLL_V2_DESIGN.md §4.4/§6):
a concept cue is resolved on the planner's concept, not the bare
noun — its 2–3 search phrases are tried in order at 8 results each,
the tag gate judges each hit against the concept sentence plus the
spoken context with the concept's avoid list as negative evidence,
and a photo (or either) preference also searches Pixabay's photos
endpoint, rendering a winning still into a cue-length Ken Burns clip
so the renderer still receives a video file. Coverr takes the same
concept phrases and gate as the second source (video-only).

The Slice-1 golden fixture is extended here: the same TJR trading
transcript ("market" at 13.54 s) now drives a concept cue end to end,
and planted hits per sense prove the concept queries beat the bare
keyword queries. Everything is mocked HTTP (MockTransport) plus real
local ffmpeg for the Ken Burns renders — no live API calls, no keys.

The no-concept path is pinned byte-identical: one search, 5 results,
videos only, gate on the noun — the existing word-sense / Pixabay /
Coverr suites already cover its behaviour, and the tests here assert
its request shape directly so a future change cannot silently widen it.
"""

import shutil
import subprocess
from pathlib import Path

import httpx
import pytest

from src import broll
from src.auto_director.clip_plan import apply_plan_broll_concepts
from src.auto_director.edl import load_profile
from src.media import coverr_broll, pixabay_broll
from test_broll_concepts import TJR_CONCEPT, TJR_WORD_TIMINGS, _tjr_edl, _tjr_plan
from test_broll_word_sense import TJR_CONTEXT


@pytest.fixture(autouse=True)
def _clear_search_caches():
    pixabay_broll._search_cache.clear()
    pixabay_broll._photo_search_cache.clear()
    coverr_broll._search_cache.clear()
    yield
    pixabay_broll._search_cache.clear()
    pixabay_broll._photo_search_cache.clear()
    coverr_broll._search_cache.clear()


@pytest.fixture()
def stock_keys(monkeypatch):
    monkeypatch.setattr(pixabay_broll, "_api_key", lambda: "test-key")
    monkeypatch.setattr(coverr_broll, "_api_key", lambda: "")


@pytest.fixture()
def coverr_only_keys(monkeypatch):
    monkeypatch.setattr(pixabay_broll, "_api_key", lambda: "")
    monkeypatch.setattr(coverr_broll, "_api_key", lambda: "test-coverr-key")


def _video_hit(asset_id, tags):
    return {
        "id": asset_id,
        "pageURL": "https://pixabay.com/videos/x",
        "tags": tags,
        "duration": 12,
        "videos": {
            "large": {
                "url": f"https://cdn.pixabay.com/{asset_id}.mp4",
                "width": 1920,
                "height": 1080,
                "size": 1000,
            },
        },
    }


def _photo_hit(asset_id, tags, width=1920, height=1080):
    return {
        "id": asset_id,
        "pageURL": f"https://pixabay.com/photos/{asset_id}",
        "tags": tags,
        "largeImageURL": f"https://cdn.pixabay.com/photo{asset_id}.jpg",
        "webformatURL": f"https://cdn.pixabay.com/photo{asset_id}_640.jpg",
        "previewURL": f"https://cdn.pixabay.com/photo{asset_id}_150.jpg",
        "imageWidth": width,
        "imageHeight": height,
    }


def _coverr_hit(asset_id, tags):
    return {
        "id": str(asset_id),
        "title": tags,
        "tags": tags.split(", "),
        "duration": 10,
        "max_width": 1920,
        "max_height": 1080,
        "urls": {
            "mp4_download": f"https://storage.coverr.co/{asset_id}.mp4",
            "mp4_preview": f"https://storage.coverr.co/{asset_id}_preview.mp4",
        },
        "poster": f"https://storage.coverr.co/{asset_id}.jpg",
    }


STREET_VIDEO_HIT = _video_hit(
    33, "market, street, farmers, market stalls, vegetables, crowd, shopping"
)
TRADING_VIDEO_HIT = _video_hit(
    44, "stock market, trading desk, chart, monitors, finance, business"
)
CHART_PHOTO_HIT = _photo_hit(
    55, "stock market, chart, trading, monitors, finance, graph"
)
STREET_PHOTO_HIT = _photo_hit(
    66, "market, street, farmers, market stalls, vegetables, crowd"
)


def _concept_entry(**overrides):
    """A TJR concept EDL entry, as Slice 2's planner stamps it."""
    entry = {
        "query": "market",
        "search_query": "stock market chart screen",
        "search_queries": [
            "stock market chart screen",
            "trading desk monitors",
            "candlestick graph close up",
        ],
        "concept": "a trader's hands over a glowing stock chart on multiple monitors",
        "sense": "market:financial",
        "media": "either",
        "avoid": ["street", "crowd", "farmers", "market stalls"],
        "context": TJR_CONTEXT,
        "start": 13.54,
        "end": 16.54,
        "local_path": "",
        "provider": "pixabay",
    }
    entry.update(overrides)
    return entry


def _mock_client(handler):
    return httpx.Client(transport=httpx.MockTransport(handler))


def _make_still(path: Path, color="testsrc2"):
    subprocess.run(
        [
            "ffmpeg", "-y",
            "-f", "lavfi", "-i", f"{color}=size=1280x720:rate=30:duration=1",
            "-frames:v", "1", str(path),
        ],
        check=True, capture_output=True, timeout=120,
    )


def _frame_bytes(path: Path, at: float) -> bytes:
    result = subprocess.run(
        [
            "ffmpeg", "-v", "error", "-ss", str(at), "-i", str(path),
            "-frames:v", "1", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", "64x64", "-",
        ],
        check=True, capture_output=True, timeout=120,
    )
    return result.stdout


def _frame_rgb(path: Path, at: float) -> tuple[float, float, float]:
    data = _frame_bytes(path, at)
    n = len(data) // 3
    return (
        sum(data[0::3]) / n, sum(data[1::3]) / n, sum(data[2::3]) / n,
    )


# --------------------------------------------------------------------------
# Gate: avoid-negatives (src/broll.py)
# --------------------------------------------------------------------------

def test_avoid_phrase_rejects_despite_shared_concept_word():
    # A tag salad that shares "stock market" with the concept but
    # shows the avoided street sense: the avoid check fires first.
    video = {"title": "stock market street festival crowd", "tags": []}
    reason = broll.footage_mismatch(
        video,
        "candlestick charts on a trader's monitors",
        TJR_CONTEXT,
        avoid=["street market"],
    )
    assert reason is not None
    assert "avoid" in reason


def test_avoid_single_word_rejects():
    # The TJR concept's avoid list is mostly single words ("street",
    # "crowd", "farmers"): one of them in the tags is enough. The
    # query shares "busy" with the hit so the off-topic check passes
    # and only the avoid check can reject it.
    video = {"title": "busy street market with farmers stalls", "tags": []}
    assert broll.footage_mismatch(
        video, "busy charts", TJR_CONTEXT, avoid=["street"]
    ) is not None
    assert broll.footage_mismatch(
        video, "busy charts", TJR_CONTEXT, avoid=["crowd"]
    ) is None  # "crowd" is not in these tags


def test_avoid_partial_phrase_does_not_reject():
    # An avoid phrase only fires when EVERY content word is present:
    # "market stalls" must not reject a trading-desk hit that merely
    # mentions the market.
    trading = {"title": "stock market charts on trading monitors", "tags": []}
    assert broll.footage_mismatch(
        trading, "candlestick charts", TJR_CONTEXT, avoid=["market stalls"]
    ) is None
    street = {"title": "farmers market stalls vegetables", "tags": []}
    assert broll.footage_mismatch(
        street, "candlestick charts", TJR_CONTEXT, avoid=["market stalls"]
    ) is not None


def test_avoid_absent_passes_and_empty_avoid_is_noop():
    trading = {"title": "stock market charts on trading monitors", "tags": []}
    assert broll.footage_mismatch(
        trading, "candlestick charts", TJR_CONTEXT, avoid=["street"]
    ) is None
    # No avoid list at all: today's gate, unchanged.
    assert broll.footage_mismatch(trading, "market", TJR_CONTEXT) is None
    street = {"title": "busy street market with farmers stalls", "tags": []}
    assert broll.footage_mismatch(street, "market", TJR_CONTEXT) is not None


# --------------------------------------------------------------------------
# Resolver: concept phrases tried in order, at 8 results
# --------------------------------------------------------------------------

def test_concept_queries_tried_in_order_until_one_resolves(stock_keys, tmp_path):
    searched = []

    def handler(request):
        url = str(request.url)
        if "pixabay.com/api/videos" in url:
            query = request.url.params.get("q")
            searched.append((query, request.url.params.get("per_page")))
            # Phrase 1 returns only the street hit (gate-rejected);
            # phrase 2 returns the trading hit.
            hits = (
                [STREET_VIDEO_HIT] if query == "stock market chart screen"
                else [TRADING_VIDEO_HIT]
            )
            return httpx.Response(200, json={"total": len(hits), "totalHits": len(hits), "hits": hits})
        return httpx.Response(200, content=b"fake-mp4-bytes")

    edl = {"broll": [_concept_entry()]}
    resolved = pixabay_broll.resolve_edl_broll(
        edl, cache_dir=tmp_path, client=_mock_client(handler)
    )
    assert resolved == 1
    assert edl["broll"][0]["asset_id"] == 44
    # Both phrases were asked, in order, at 8 results each — and the
    # bare noun "market" was never a search.
    assert searched == [
        ("stock market chart screen", "8"),
        ("trading desk monitors", "8"),
    ]
    assert all(query != "market" for query, _ in searched)


def test_concept_first_phrase_win_costs_one_search(stock_keys, tmp_path):
    searched = []

    def handler(request):
        url = str(request.url)
        if "pixabay.com/api/videos" in url:
            searched.append(request.url.params.get("q"))
            return httpx.Response(
                200, json={"total": 2, "totalHits": 2, "hits": [STREET_VIDEO_HIT, TRADING_VIDEO_HIT]}
            )
        return httpx.Response(200, content=b"fake-mp4-bytes")

    edl = {"broll": [_concept_entry()]}
    resolved = pixabay_broll.resolve_edl_broll(
        edl, cache_dir=tmp_path, client=_mock_client(handler)
    )
    assert resolved == 1
    # The first phrase resolved, so the second and third were never
    # asked — the Slice-2 request shape is preserved.
    assert searched == ["stock market chart screen"]
    assert edl["broll"][0]["asset_id"] == 44


def test_concept_gate_rejects_bare_noun_only_hit(stock_keys, tmp_path):
    # The discrimination test: a hit sharing ONLY the bare noun with
    # the spoken words, and nothing with the concept sentence, is
    # rejected by the concept gate — while today's noun gate would
    # have accepted it (the shared word "market" used to be enough).
    bare_noun_hit = _video_hit(77, "market, crowd, shopping")
    neutral_context = "the numbers kept climbing all afternoon"
    assert broll.footage_mismatch(
        {"title": "market, crowd, shopping", "tags": []},
        "market", neutral_context,
    ) is None  # old gate: the shared word passes it

    def handler(request):
        url = str(request.url)
        if "pixabay.com/api/videos" in url:
            return httpx.Response(
                200, json={"total": 2, "totalHits": 2, "hits": [bare_noun_hit, TRADING_VIDEO_HIT]}
            )
        return httpx.Response(200, content=b"fake-mp4-bytes")

    entry = _concept_entry(context=neutral_context)
    edl = {"broll": [entry]}
    resolved = pixabay_broll.resolve_edl_broll(
        edl, cache_dir=tmp_path, client=_mock_client(handler)
    )
    assert resolved == 1
    assert entry["asset_id"] == 44


def test_concept_cue_skipped_when_every_candidate_fails(stock_keys, tmp_path):
    # Terminal rule, unchanged: no insert beats a wrong insert.
    def handler(request):
        url = str(request.url)
        if "pixabay.com/api/videos" in url:
            return httpx.Response(
                200, json={"total": 1, "totalHits": 1, "hits": [STREET_VIDEO_HIT]}
            )
        if "pixabay.com/api" in url:
            return httpx.Response(200, json={"total": 0, "totalHits": 0, "hits": []})
        return httpx.Response(200, content=b"fake-mp4-bytes")

    entry = _concept_entry()
    edl = {"broll": [entry]}
    resolved = pixabay_broll.resolve_edl_broll(
        edl, cache_dir=tmp_path, client=_mock_client(handler)
    )
    assert resolved == 0
    assert entry["local_path"] == ""
    assert entry.get("asset_id") is None


# --------------------------------------------------------------------------
# No-concept path: byte-identical request shape
# --------------------------------------------------------------------------

def test_no_concept_path_uses_one_search_at_five_results(stock_keys, tmp_path):
    searched = []

    def handler(request):
        url = str(request.url)
        if "pixabay.com/api/videos" in url:
            searched.append(
                (request.url.params.get("q"), request.url.params.get("per_page"))
            )
            return httpx.Response(
                200, json={"total": 2, "totalHits": 2, "hits": [STREET_VIDEO_HIT, TRADING_VIDEO_HIT]}
            )
        return httpx.Response(200, content=b"fake-mp4-bytes")

    # A keyword-planned entry: no concept fields at all.
    entry = {
        "query": "market",
        "search_query": "market trade profit",
        "context": TJR_CONTEXT,
        "start": 13.54,
        "end": 16.54,
        "local_path": "",
    }
    edl = {"broll": [entry]}
    resolved = pixabay_broll.resolve_edl_broll(
        edl, cache_dir=tmp_path, client=_mock_client(handler)
    )
    assert resolved == 1
    assert entry["asset_id"] == 44
    assert searched == [("market trade profit", "5")]
    # The photos endpoint was never consulted for a no-concept cue.
    assert not (tmp_path / "pixabay_55_still.mp4").exists()


# --------------------------------------------------------------------------
# Photos endpoint + Ken Burns stills
# --------------------------------------------------------------------------

def _photo_handler(still_bytes, video_hits=(), photo_hits=()):
    """MockTransport handler: videos endpoint, photos endpoint, downloads."""
    calls = {"videos": [], "photos": [], "downloads": 0}

    def handler(request):
        url = str(request.url)
        if "pixabay.com/api/videos" in url:
            calls["videos"].append(
                (request.url.params.get("q"), request.url.params.get("per_page"))
            )
            return httpx.Response(
                200, json={"total": len(video_hits), "totalHits": len(video_hits), "hits": list(video_hits)}
            )
        if "pixabay.com/api" in url:
            calls["photos"].append(
                (
                    request.url.params.get("q"),
                    request.url.params.get("per_page"),
                    request.url.params.get("image_type"),
                )
            )
            return httpx.Response(
                200, json={"total": len(photo_hits), "totalHits": len(photo_hits), "hits": list(photo_hits)}
            )
        calls["downloads"] += 1
        return httpx.Response(200, content=still_bytes)

    return handler, calls


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg required")
def test_photo_concept_resolves_from_photos_endpoint(stock_keys, tmp_path):
    still = tmp_path / "still.jpg"
    _make_still(still)
    handler, calls = _photo_handler(still.read_bytes(), photo_hits=[CHART_PHOTO_HIT])
    entry = _concept_entry(media="photo")
    edl = {"broll": [entry]}
    resolved = pixabay_broll.resolve_edl_broll(
        edl, cache_dir=tmp_path, client=_mock_client(handler)
    )
    assert resolved == 1
    assert entry["asset_id"] == 55
    assert entry["provider"] == "pixabay"
    assert entry["media"] == "photo"
    local = Path(entry["local_path"])
    assert local.is_file()
    assert local.suffix == ".mp4"  # a playable clip, not the still
    # The photos endpoint was asked (image_type=photo, 8 results);
    # the videos endpoint was never consulted for a photo concept.
    assert calls["videos"] == []
    assert calls["photos"] == [("stock market chart screen", "8", "photo")]
    # The Ken Burns clip is the cue length, portrait, 30 fps.
    probe = subprocess.run(
        [
            "ffprobe", "-v", "error",
            "-show_entries", "format=duration",
            "-show_entries", "stream=width,height,r_frame_rate",
            "-of", "default=nw=1", str(local),
        ],
        check=True, capture_output=True, text=True, timeout=120,
    )
    assert "width=1080" in probe.stdout
    assert "height=1920" in probe.stdout
    assert "r_frame_rate=30/1" in probe.stdout
    assert "duration=3.000000" in probe.stdout


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg required")
def test_either_falls_back_to_photo_when_video_fails(stock_keys, tmp_path):
    still = tmp_path / "still.jpg"
    _make_still(still)
    handler, calls = _photo_handler(
        still.read_bytes(),
        video_hits=[STREET_VIDEO_HIT],
        photo_hits=[CHART_PHOTO_HIT],
    )
    entry = _concept_entry(media="either")
    edl = {"broll": [entry]}
    resolved = pixabay_broll.resolve_edl_broll(
        edl, cache_dir=tmp_path, client=_mock_client(handler)
    )
    assert resolved == 1
    assert entry["asset_id"] == 55
    assert entry["media"] == "photo"
    assert Path(entry["local_path"]).suffix == ".mp4"
    # Every video phrase was tried first (all gate-rejected), then the
    # photo phrases — the photo win did not skip the video pass.
    assert [q for q, _ in calls["videos"]] == [
        "stock market chart screen",
        "trading desk monitors",
        "candlestick graph close up",
    ]
    assert calls["photos"][0][0] == "stock market chart screen"


def test_video_media_never_calls_photos(stock_keys, tmp_path):
    handler, calls = _photo_handler(b"fake-mp4-bytes", video_hits=[])
    entry = _concept_entry(media="video")
    edl = {"broll": [entry]}
    resolved = pixabay_broll.resolve_edl_broll(
        edl, cache_dir=tmp_path, client=_mock_client(handler)
    )
    assert resolved == 0
    assert calls["photos"] == []
    assert entry["local_path"] == ""


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg required")
def test_photo_clip_is_reused_from_disk_without_redownload(stock_keys, tmp_path):
    still = tmp_path / "still.jpg"
    _make_still(still)
    handler, calls = _photo_handler(still.read_bytes(), photo_hits=[CHART_PHOTO_HIT])
    client = _mock_client(handler)
    entry = _concept_entry(media="photo")
    edl = {"broll": [entry]}
    assert pixabay_broll.resolve_edl_broll(edl, cache_dir=tmp_path, client=client) == 1
    first_path = entry["local_path"]
    assert calls["downloads"] == 1  # the still, once

    # Second resolve with the search caches cleared: the search runs
    # again, but the still is not re-downloaded and the clip is not
    # re-rendered.
    pixabay_broll._search_cache.clear()
    pixabay_broll._photo_search_cache.clear()
    entry2 = _concept_entry(media="photo")
    edl2 = {"broll": [entry2]}
    assert pixabay_broll.resolve_edl_broll(edl2, cache_dir=tmp_path, client=client) == 1
    assert entry2["local_path"] == first_path
    assert calls["downloads"] == 1


# --------------------------------------------------------------------------
# Ken Burns still -> clip, direct
# --------------------------------------------------------------------------

@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg required")
def test_still_to_clip_renders_portrait_zoom(tmp_path):
    still = tmp_path / "still.jpg"
    _make_still(still)
    out = tmp_path / "kb.mp4"
    assert pixabay_broll.still_to_clip(still, 3.0, out) is True
    assert out.is_file() and out.stat().st_size > 0
    probe = subprocess.run(
        [
            "ffprobe", "-v", "error",
            "-show_entries", "format=duration",
            "-show_entries", "stream=width,height,r_frame_rate,codec_name",
            "-of", "default=nw=1", str(out),
        ],
        check=True, capture_output=True, text=True, timeout=120,
    )
    assert "codec_name=h264" in probe.stdout
    assert "width=1080" in probe.stdout
    assert "height=1920" in probe.stdout
    assert "duration=3.000000" in probe.stdout
    # The zoom moves: the first and last frames of the clip differ
    # pixel by pixel (channel means barely move under a zoom, so the
    # comparison is per-pixel, not per-channel).
    first = _frame_bytes(out, 0.1)
    last = _frame_bytes(out, 2.9)
    mad = sum(abs(a - b) for a, b in zip(first, last)) / len(first)
    assert mad > 1.0, f"Ken Burns clip is static (per-pixel MAD {mad:.2f})"


def test_still_to_clip_bad_image_fails_open(tmp_path):
    not_an_image = tmp_path / "still.jpg"
    not_an_image.write_bytes(b"this is not an image")
    out = tmp_path / "kb.mp4"
    assert pixabay_broll.still_to_clip(not_an_image, 3.0, out) is False
    assert not out.exists()
    assert not list(tmp_path.glob("*.part.mp4"))  # no partial left behind


# --------------------------------------------------------------------------
# Coverr: same concept phrases and gate, video-only
# --------------------------------------------------------------------------

def test_coverr_tries_concept_phrases_in_order(coverr_only_keys, tmp_path):
    searched = []

    def handler(request):
        url = str(request.url)
        if "api.coverr.co/videos" in url and request.method == "GET":
            query = request.url.params.get("query")
            searched.append((query, request.url.params.get("page_size")))
            hits = (
                [_coverr_hit(71, "market, street, farmers, stalls, crowd")]
                if query == "stock market chart screen"
                else [_coverr_hit(72, "stock market, trading desk, chart, monitors")]
            )
            return httpx.Response(200, json={"total": len(hits), "hits": hits})
        if "api.coverr.co" in url:
            return httpx.Response(200, json={})  # download-stats ping
        return httpx.Response(200, content=b"fake-mp4-bytes")

    edl = {"broll": [_concept_entry()]}
    resolved = pixabay_broll.resolve_edl_broll(
        edl, cache_dir=tmp_path, client=_mock_client(handler)
    )
    assert resolved == 1
    entry = edl["broll"][0]
    assert entry["provider"] == "coverr"
    assert entry["asset_id"] == "72"
    assert searched == [
        ("stock market chart screen", "8"),
        ("trading desk monitors", "8"),
    ]


def test_coverr_skipped_for_photo_concept(coverr_only_keys, tmp_path):
    seen = []

    def handler(request):
        seen.append(str(request.url))
        return httpx.Response(200, json={"total": 0, "totalHits": 0, "hits": []})

    # Pixabay has no key here, so no Pixabay request is possible; the
    # photo concept must not reach Coverr either (video-only).
    entry = _concept_entry(media="photo")
    edl = {"broll": [entry]}
    resolved = pixabay_broll.resolve_edl_broll(
        edl, cache_dir=tmp_path, client=_mock_client(handler)
    )
    assert resolved == 0
    assert entry["local_path"] == ""
    assert not any("coverr.co" in url for url in seen)


def test_coverr_concept_gate_rejects_street_hit(stock_keys, tmp_path, monkeypatch):
    # Pixabay fails outright; Coverr returns the street hit first.
    monkeypatch.setattr(coverr_broll, "_api_key", lambda: "test-coverr-key")

    def handler(request):
        url = str(request.url)
        if "pixabay.com/api" in url:
            return httpx.Response(500, json={})
        if "api.coverr.co/videos" in url:
            return httpx.Response(200, json={
                "total": 2,
                "hits": [
                    _coverr_hit(71, "market, street, farmers, stalls, crowd"),
                    _coverr_hit(72, "stock market, trading desk, chart, monitors"),
                ],
            })
        if "api.coverr.co" in url:
            return httpx.Response(200, json={})
        return httpx.Response(200, content=b"fake-mp4-bytes")

    edl = {"broll": [_concept_entry()]}
    resolved = pixabay_broll.resolve_edl_broll(
        edl, cache_dir=tmp_path, client=_mock_client(handler)
    )
    assert resolved == 1
    entry = edl["broll"][0]
    assert entry["provider"] == "coverr"
    assert entry["asset_id"] == "72"


# --------------------------------------------------------------------------
# Golden fixture end to end: the real TJR transcript, concept cue
# --------------------------------------------------------------------------

@pytest.fixture
def v2_cadence(monkeypatch):
    """Smart B-Roll v2 cadence (``BROLL_CADENCE=legacy``): the test below pins
    v2's exact cue count for the TJR concept. Under v3 (default since
    2026-11-17) the lexicon tops the plan up to the profile's cadence; that
    behaviour is covered in test_broll_v3_cadence.py.
    """
    monkeypatch.setenv("BROLL_CADENCE", "legacy")


@pytest.mark.usefixtures("v2_cadence")
def test_tjr_concept_end_to_end_from_real_transcript(stock_keys, tmp_path):
    # Slice 2's planner output for the real 96-word clip, resolved by
    # the Slice-3 resolver: the concept's first phrase is searched,
    # the street hit is rejected, the trading hit wins.
    edl = _tjr_edl()
    assert apply_plan_broll_concepts(
        edl, _tjr_plan(), load_profile("podcast_news"), TJR_WORD_TIMINGS
    ) == 1
    entry = edl["broll"][0]
    assert entry["query"] == "market"
    assert entry["start"] == 13.54
    assert entry["search_queries"] == TJR_CONCEPT["search_queries"]

    searched = []

    def handler(request):
        url = str(request.url)
        if "pixabay.com/api/videos" in url:
            searched.append(request.url.params.get("q"))
            return httpx.Response(
                200, json={"total": 2, "totalHits": 2, "hits": [STREET_VIDEO_HIT, TRADING_VIDEO_HIT]}
            )
        return httpx.Response(200, content=b"fake-mp4-bytes")

    resolved = pixabay_broll.resolve_edl_broll(
        edl, cache_dir=tmp_path, client=_mock_client(handler)
    )
    assert resolved == 1
    assert searched == ["stock market chart screen"]
    assert entry["asset_id"] == 44
    assert entry["provider"] == "pixabay"
    assert Path(entry["local_path"]).is_file()
