"""Coverr B-roll lane: second stock source + the stock library.

The Coverr client and the resolver fallback are exercised entirely
through an httpx MockTransport (no network). The library tests pin the
download-once + manifest contract the "saves to a folder" requirement
rests on.
"""

import json
from pathlib import Path

import httpx
import pytest

from src.media import coverr_broll, pixabay_broll, stock_library


@pytest.fixture(autouse=True)
def _clear_search_caches():
    coverr_broll._search_cache.clear()
    pixabay_broll._search_cache.clear()
    yield
    coverr_broll._search_cache.clear()
    pixabay_broll._search_cache.clear()


@pytest.fixture()
def coverr_key(monkeypatch):
    monkeypatch.setattr(coverr_broll, "_api_key", lambda: "coverr-key")


@pytest.fixture()
def no_pixabay_key(monkeypatch):
    monkeypatch.setattr(pixabay_broll, "_api_key", lambda: "")


@pytest.fixture()
def pixabay_key(monkeypatch):
    monkeypatch.setattr(pixabay_broll, "_api_key", lambda: "pixabay-key")


def _mock_client(handler):
    return httpx.Client(transport=httpx.MockTransport(handler))


def _coverr_hit(asset_id, title="Ocean waves", tags=("ocean", "waves"), duration=9.5):
    return {
        "id": asset_id,
        "title": title,
        "tags": list(tags),
        "duration": duration,
        "max_width": 1920,
        "max_height": 1080,
        "poster": f"https://storage.coverr.co/p/{asset_id}",
        "is_ai_generated": False,
        "urls": {
            "mp4": f"https://storage.coverr.co/videos/{asset_id}?token=x",
            "mp4_preview": f"https://storage.coverr.co/videos/{asset_id}/preview?token=x",
            "mp4_download": f"https://storage.coverr.co/videos/{asset_id}/download?token=x",
        },
    }


def _coverr_payload(*hits):
    return {"page": 0, "pages": 1, "page_size": 20, "total": len(hits), "hits": list(hits)}


# --------------------------------------------------------------------------
# Coverr client (mocked HTTP)
# --------------------------------------------------------------------------

def test_coverr_search_normalises_hits(coverr_key):
    seen = {}

    def handler(request):
        seen["url"] = str(request.url)
        seen["auth"] = request.headers.get("authorization")
        return httpx.Response(200, json=_coverr_payload(_coverr_hit("AbC123")))

    results = coverr_broll.search_coverr_videos("ocean waves", client=_mock_client(handler))

    assert "api.coverr.co/videos" in seen["url"]
    assert "query=ocean+waves" in seen["url"]
    assert "urls=true" in seen["url"]
    assert seen["auth"] == "Bearer coverr-key"
    assert len(results) == 1
    hit = results[0]
    assert hit["asset_id"] == "AbC123"
    assert hit["title"] == "Ocean waves"
    assert hit["download_url"] == "https://storage.coverr.co/videos/AbC123/download?token=x"
    assert hit["preview_url"] == "https://storage.coverr.co/videos/AbC123/preview?token=x"
    assert hit["thumbnail_url"] == "https://storage.coverr.co/p/AbC123"
    assert hit["width"] == 1920 and hit["height"] == 1080
    assert hit["duration"] == 9.5
    assert hit["tags"] == "ocean, waves"


def test_coverr_search_skips_hits_without_download_url(coverr_key):
    broken = _coverr_hit("NoUrl1")
    broken["urls"] = {"mp4_preview": "https://storage.coverr.co/only-preview"}

    def handler(request):
        return httpx.Response(200, json=_coverr_payload(broken, _coverr_hit("Good22")))

    results = coverr_broll.search_coverr_videos("ocean", client=_mock_client(handler))
    assert [hit["asset_id"] for hit in results] == ["Good22"]


def test_coverr_search_failure_is_empty_not_exception(coverr_key):
    def handler_500(request):
        return httpx.Response(500, json={})

    def handler_boom(request):
        raise httpx.ConnectError("down", request=request)

    assert coverr_broll.search_coverr_videos("ocean", client=_mock_client(handler_500)) == []
    coverr_broll._search_cache.clear()
    assert coverr_broll.search_coverr_videos("ocean", client=_mock_client(handler_boom)) == []


def test_coverr_no_key_makes_no_calls(monkeypatch):
    monkeypatch.setattr(coverr_broll, "_api_key", lambda: "")
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(200, json=_coverr_payload(_coverr_hit("X1")))

    client = _mock_client(handler)
    assert coverr_broll.coverr_configured() is False
    assert coverr_broll.search_coverr_videos("ocean", client=client) == []
    assert coverr_broll.ensure_coverr_asset("ocean", client=client) is None
    assert calls == []


# --------------------------------------------------------------------------
# Coverr ensure: gate, download, library + manifest
# --------------------------------------------------------------------------

def _library_handler(counts, *, hits=None):
    def handler(request):
        url = str(request.url)
        if request.method == "PATCH":
            counts["ping"] += 1
            return httpx.Response(204)
        if "api.coverr.co" in url:
            counts["search"] += 1
            return httpx.Response(200, json=_coverr_payload(*(hits or [_coverr_hit("AbC123")])))
        counts["download"] += 1
        return httpx.Response(200, content=b"coverr-mp4-bytes")
    return handler


def _read_manifest(root: Path) -> dict:
    return json.loads((root / "manifest.json").read_text(encoding="utf-8"))


def test_coverr_download_lands_in_library_with_manifest(coverr_key, tmp_path):
    counts = {"search": 0, "download": 0, "ping": 0}
    client = _mock_client(_library_handler(counts))

    asset = coverr_broll.ensure_coverr_asset("ocean waves", cache_dir=tmp_path, client=client)

    assert asset is not None
    assert asset["provider"] == "coverr"
    target = Path(asset["local_path"])
    assert target.parent == tmp_path
    assert target.name == "coverr_AbC123.mp4"
    assert target.read_bytes() == b"coverr-mp4-bytes"
    # A fresh download pings Coverr's stats endpoint exactly once.
    assert counts == {"search": 1, "download": 1, "ping": 1}

    manifest = _read_manifest(tmp_path)
    record = manifest["assets"]["coverr:AbC123"]
    assert record["provider"] == "coverr"
    assert record["asset_id"] == "AbC123"
    assert record["file"] == "coverr_AbC123.mp4"
    assert record["tags"] == ["ocean", "waves"]
    assert "Coverr License" in record["licence"]
    assert record["downloaded_at"]
    assert record["bytes"] == len(b"coverr-mp4-bytes")
    assert record["fetched_for"][0]["query"] == "ocean waves"


def test_coverr_second_use_is_a_library_hit_with_zero_http(coverr_key, tmp_path):
    counts = {"search": 0, "download": 0, "ping": 0}
    client = _mock_client(_library_handler(counts))
    first = coverr_broll.ensure_coverr_asset("ocean waves", cache_dir=tmp_path, client=client)
    assert first is not None

    # Even with the in-process search cache cleared, the file in the
    # library wins: no second download, no second stats ping.
    coverr_broll._search_cache.clear()
    second = coverr_broll.ensure_coverr_asset("ocean waves", cache_dir=tmp_path, client=client)
    assert second["local_path"] == first["local_path"]
    assert counts["download"] == 1
    assert counts["ping"] == 1
    manifest = _read_manifest(tmp_path)
    assert len(manifest["assets"]["coverr:AbC123"]["fetched_for"]) == 2


def test_coverr_gate_rejects_wrong_sense_hit(coverr_key, tmp_path):
    # Trading speech about the market: a street-market hit shares the
    # bare word but shows the wrong sense and must be passed over for
    # the trading-desk hit behind it.
    street = _coverr_hit("Street1", title="Street market crowd",
                         tags=("market", "street", "crowd", "shopping"))
    desk = _coverr_hit("Desk222", title="Trading desk screens",
                       tags=("market", "trading", "chart", "stock market"))
    counts = {"search": 0, "download": 0, "ping": 0}
    client = _mock_client(_library_handler(counts, hits=[street, desk]))
    context = "probabilities within the market when I execute on my strategy profit loss"

    asset = coverr_broll.ensure_coverr_asset(
        "market", cache_dir=tmp_path, client=client, context=context,
        search_query="market strategy",
    )

    assert asset is not None
    assert asset["asset_id"] == "Desk222"
    assert Path(asset["local_path"]).name == "coverr_Desk222.mp4"


# --------------------------------------------------------------------------
# Resolver: provider order + failure isolation (via pixabay_broll.resolve)
# --------------------------------------------------------------------------

def _pixabay_hit(asset_id=5):
    return {
        "id": asset_id,
        "pageURL": f"https://pixabay.com/videos/{asset_id}",
        "duration": 12,
        "tags": "ocean, waves",
        "videos": {
            "large": {"url": f"https://cdn.pixabay.com/asset{asset_id}.mp4",
                      "width": 1920, "height": 1080, "size": 1000},
        },
    }


def _split_handler(*, pixabay_response, coverr_hits, calls):
    def handler(request):
        url = str(request.url)
        if request.method == "PATCH":
            calls.append("coverr-ping")
            return httpx.Response(204)
        if "pixabay.com/api" in url:
            calls.append("pixabay-search")
            return pixabay_response
        if "api.coverr.co" in url:
            calls.append("coverr-search")
            return httpx.Response(200, json=_coverr_payload(*coverr_hits))
        calls.append("download")
        return httpx.Response(200, content=b"stock-bytes")
    return handler


def test_resolver_falls_back_to_coverr_when_pixabay_empty(
    pixabay_key, coverr_key, tmp_path
):
    calls = []
    handler = _split_handler(
        pixabay_response=httpx.Response(200, json={"hits": []}),
        coverr_hits=[_coverr_hit("AbC123")],
        calls=calls,
    )
    edl = {"clip_id": "clip-9", "broll": [{"query": "ocean waves", "local_path": ""}]}
    resolved = pixabay_broll.resolve_edl_broll(
        edl, cache_dir=tmp_path, client=_mock_client(handler)
    )

    assert resolved == 1
    entry = edl["broll"][0]
    assert entry["provider"] == "coverr"
    assert entry["asset_id"] == "AbC123"
    assert Path(entry["local_path"]).name == "coverr_AbC123.mp4"
    assert calls.index("pixabay-search") < calls.index("coverr-search")
    # The clip's use of the asset is in the manifest.
    manifest = _read_manifest(tmp_path)
    uses = manifest["assets"]["coverr:AbC123"]["uses"]
    assert uses and uses[0]["clip_id"] == "clip-9"


def test_resolver_falls_back_to_coverr_when_pixabay_down(
    pixabay_key, coverr_key, tmp_path
):
    calls = []
    handler = _split_handler(
        pixabay_response=httpx.Response(500, json={}),
        coverr_hits=[_coverr_hit("AbC123")],
        calls=calls,
    )
    edl = {"broll": [{"query": "ocean waves", "local_path": ""}]}
    resolved = pixabay_broll.resolve_edl_broll(
        edl, cache_dir=tmp_path, client=_mock_client(handler)
    )
    assert resolved == 1
    assert edl["broll"][0]["provider"] == "coverr"


def test_resolver_prefers_pixabay_and_never_calls_coverr(
    pixabay_key, coverr_key, tmp_path
):
    calls = []
    handler = _split_handler(
        pixabay_response=httpx.Response(200, json={"hits": [_pixabay_hit(5)]}),
        coverr_hits=[_coverr_hit("AbC123")],
        calls=calls,
    )
    edl = {"broll": [{"query": "ocean waves", "local_path": ""}]}
    resolved = pixabay_broll.resolve_edl_broll(
        edl, cache_dir=tmp_path, client=_mock_client(handler)
    )

    assert resolved == 1
    entry = edl["broll"][0]
    assert entry["provider"] == "pixabay"
    assert entry["asset_id"] == 5
    assert "coverr-search" not in calls
    # Pixabay downloads are in the same library + manifest now.
    manifest = _read_manifest(tmp_path)
    assert manifest["assets"]["pixabay:5"]["file"] == "pixabay_5.mp4"


def test_resolver_coverr_only_when_pixabay_unconfigured(
    no_pixabay_key, coverr_key, tmp_path
):
    calls = []
    handler = _split_handler(
        pixabay_response=httpx.Response(200, json={"hits": [_pixabay_hit(5)]}),
        coverr_hits=[_coverr_hit("AbC123")],
        calls=calls,
    )
    edl = {"broll": [{"query": "ocean waves", "local_path": ""}]}
    resolved = pixabay_broll.resolve_edl_broll(
        edl, cache_dir=tmp_path, client=_mock_client(handler)
    )
    assert resolved == 1
    assert edl["broll"][0]["provider"] == "coverr"
    assert "pixabay-search" not in calls


def test_resolver_no_keys_no_calls(monkeypatch, tmp_path):
    monkeypatch.setattr(pixabay_broll, "_api_key", lambda: "")
    monkeypatch.setattr(coverr_broll, "_api_key", lambda: "")
    calls = []
    handler = _split_handler(
        pixabay_response=httpx.Response(200, json={"hits": [_pixabay_hit(5)]}),
        coverr_hits=[_coverr_hit("AbC123")],
        calls=calls,
    )
    edl = {"broll": [{"query": "ocean waves", "local_path": ""}]}
    assert pixabay_broll.resolve_edl_broll(
        edl, cache_dir=tmp_path, client=_mock_client(handler)
    ) == 0
    assert edl["broll"][0]["local_path"] == ""
    assert calls == []


# --------------------------------------------------------------------------
# Stock library units
# --------------------------------------------------------------------------

def test_library_asset_path_and_manifest_roundtrip(tmp_path):
    path = stock_library.asset_path("coverr", "a/b c", root=tmp_path)
    assert path == tmp_path / "coverr_a_b_c.mp4"
    path.write_bytes(b"1234")

    candidate = {
        "asset_id": "a/b c", "title": "T", "tags": "x, y",
        "page_url": "", "download_url": "https://example.invalid/v",
        "width": 640, "height": 360, "duration": 3.0,
    }
    stock_library.record_asset(
        tmp_path, provider="coverr", candidate=candidate, query="ocean",
        search_query="ocean waves", local_path=str(path), downloaded=True,
    )
    stock_library.record_use(
        tmp_path, provider="coverr", asset_id="a/b c", clip_id="c1", query="ocean",
    )
    manifest = _read_manifest(tmp_path)
    record = manifest["assets"]["coverr:a/b c"]
    assert record["bytes"] == 4
    assert record["tags"] == ["x", "y"]
    assert record["uses"][0]["clip_id"] == "c1"
    assert stock_library.library_dir().name == "stock_library"


# ---------------------------------------------------------------------------
# Smart B-Roll v2, Slice 4: concept subject + contrastive avoid (Coverr)
# ---------------------------------------------------------------------------

class _SubjectSession:
    """score() reads (asset id, subject) pairs from a scripted table."""

    def __init__(self, scores):
        self.available = True
        self.scores = scores
        self.calls = []

    def score(self, frames, subject):
        asset_id = frames[0] if frames else None
        self.calls.append((asset_id, subject))
        return self.scores.get((asset_id, subject))

    def close(self):
        pass


def test_coverr_concept_cue_contrastive_veto(coverr_key, tmp_path, monkeypatch):
    """Coverr applies the same Slice 4 rule: the veto judges the concept
    sentence, and a hit that looks more like the wrong sense is vetoed
    even when its concept score clears the threshold."""
    from src.media import broll_vision

    broll_vision._score_cache.clear()
    monkeypatch.setattr(
        coverr_broll,
        "_candidate_frames_for_hit",
        lambda hit, *, client, cache_dir: [hit["asset_id"]],
    )
    concept = "Candlestick charts and a profit and loss graph on a trader's monitors."
    avoid_subject = "street market, farmers market stalls"
    session = _SubjectSession({
        ("AbC123", concept): 0.50, ("AbC123", avoid_subject): 0.60,
        ("DeF456", concept): 0.55, ("DeF456", avoid_subject): 0.10,
    })
    hits = [
        _coverr_hit("AbC123", tags=("stock market", "chart", "trading")),
        _coverr_hit("DeF456", tags=("stock market", "candlestick", "graph")),
    ]
    counts = {"search": 0, "download": 0, "ping": 0}
    client = _mock_client(_library_handler(counts, hits=hits))
    sink: dict = {}

    asset = coverr_broll.ensure_coverr_asset(
        "market", cache_dir=tmp_path, client=client,
        context="trade worth profit loss strategy",
        search_query="market strategy",
        concept=concept, avoid=["street market", "farmers market stalls"],
        search_queries=["stock market chart screen"],
        veto_session=session, veto_sink=sink,
    )

    assert asset is not None and asset["asset_id"] == "DeF456"
    assert sink["vetoed"] == ["AbC123"]
    assert sink["avoid_scores"] == {"AbC123": 0.6, "DeF456": 0.1}
    assert sink["winner_score"] == 0.55
    subjects = {subject for _, subject in session.calls}
    assert concept in subjects and avoid_subject in subjects
    assert "market strategy" not in subjects and "market" not in subjects
    broll_vision._score_cache.clear()
