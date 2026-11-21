"""B-roll consolidation: one resolution path (Smart B-Roll v2).

The scope audit found two B-roll systems live side by side: the v2
resolver (concept brain -> word-sense gate -> Pixabay/Coverr ->
vision veto) and a legacy Pexels path (profile ``source: "pexels"``,
``plan_clip_broll``'s own search/choose/download, Pexels-only editor
search/suggest/AI-edit, a Pexels-only status endpoint). Pexels paused
new API keys and the supported key set is Pixabay + Coverr, so every
legacy-only route was dead weight on a real deployment.

These tests pin the consolidated end state:

- profiles declare ``source: "stock"`` (the v2 route) with density
  and coverage exactly as shipped before the rename;
- an EDL entry still carrying the legacy ``"pexels"`` source value
  (a stored EDL, a user-saved profile) resolves through the v2
  resolver with the provider stamped, identically to a ``"stock"``
  entry;
- stored v2 placements resolve to renderer overlays from the shared
  stock library, and legacy Pexels placements keep their
  download-on-demand path;
- the editor search is answered by the v2 providers, Pixabay first,
  Coverr second;
- the configuration gates report the v2 providers.
"""

from pathlib import Path

import pytest

from src import broll
from src.api.routes.media import get_broll_status
from src.auto_director.edl import load_profile
from src.broll import broll_overlays_for, search_footage, stock_broll_configured
from src.media import coverr_broll, pixabay_broll, stock_library
from src.media.pixabay_broll import resolve_edl_broll


# --- profiles ---------------------------------------------------------------

# (enabled, density_per_min, max_coverage) exactly as shipped before
# the consolidation; the rename must not move any of them.
PROFILE_TABLE = {
    "podcast_news": (True, 1.5, None),
    "hindi_podcast": (True, 1.5, None),
    "true_crime": (True, 1.5, None),
    "education_tutorial": (True, 1.5, None),
    "vlog_lifestyle": (True, 2.0, None),
    "travel_vlog": (True, 2.0, 0.4),
    "japan_travel_cinematic": (True, 2.0, 0.4),
    "finance_business": (True, 1.5, None),
    "meme_comedy": (False, 0.0, None),
    "gaming": (False, 0.0, None),
    "sports": (False, 0.0, None),
    "football_edit": (False, 0.0, None),
    "anime_funk_edit": (False, 0.0, None),
    "music_artist": (False, 0.0, None),
}


def test_profiles_declare_the_v2_route_with_unchanged_density_and_coverage():
    assert len(PROFILE_TABLE) == 14
    for niche, (enabled, density, max_coverage) in PROFILE_TABLE.items():
        block = load_profile(niche)["broll"]
        assert block["enabled"] is enabled, niche
        assert block["density_per_min"] == density, niche
        assert block.get("max_coverage") == max_coverage, niche
        # No profile names the retired provider any more; an enabled
        # profile declares the v2 route, a disabled one stays "off".
        assert block["source"] == ("stock" if enabled else "off"), niche


# --- resolver: legacy-labelled entries ---------------------------------------


def _hit(asset_id, tags="mountain summit, peak, hiking trail, alpine"):
    return {
        "asset_id": asset_id,
        "page_url": f"https://pixabay.com/videos/{asset_id}/",
        "download_url": f"https://cdn.pixabay.com/video/{asset_id}.mp4",
        "width": 1920,
        "height": 1080,
        "duration": 12,
        "tags": tags,
        "preview_url": f"https://cdn.pixabay.com/{asset_id}_preview.mp4",
        "thumbnail_url": f"https://cdn.pixabay.com/{asset_id}.jpg",
    }


@pytest.fixture
def v2_library(monkeypatch, tmp_path):
    """Pixabay configured with faked search/download; Coverr off."""
    library = tmp_path / "stock_library"
    library.mkdir()
    monkeypatch.setattr(pixabay_broll, "_api_key", lambda: "test-key")
    monkeypatch.setattr(coverr_broll, "_api_key", lambda: "")
    monkeypatch.setattr(stock_library, "library_dir", lambda: library)
    monkeypatch.setattr(
        pixabay_broll,
        "search_pixabay_videos",
        lambda query, *, per_page, client=None: [_hit(43)],
    )

    def fake_download(client, url, target):
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(b"footage")
        return True

    monkeypatch.setattr(pixabay_broll, "_download", fake_download)
    pixabay_broll._search_cache.clear()
    return library


@pytest.mark.parametrize("source", ["pexels", "stock"])
def test_legacy_and_current_source_entries_resolve_via_v2(v2_library, source):
    # A stored EDL planned before the rename carries source "pexels";
    # a fresh one carries "stock". The resolver never branches on the
    # value, so both resolve through Pixabay with the provider stamped.
    entry = {
        "start": 4.0,
        "end": 7.0,
        "query": "mountain summit",
        "search_query": "mountain summit",
        "context": "we climbed the highest mountain last year",
        "source": source,
    }
    edl = {"template": "auto", "broll": [entry]}

    assert resolve_edl_broll(edl) == 1
    assert entry["provider"] == "pixabay"
    assert entry["asset_id"] == 43
    assert Path(entry["local_path"]).is_file()
    assert Path(entry["local_path"]).parent == v2_library


# --- overlays: both placement shapes -----------------------------------------


async def test_overlays_for_resolves_v2_placements_from_the_stock_library(
    monkeypatch, tmp_path
):
    library = tmp_path / "stock_library"
    library.mkdir()
    file = library / "pixabay_44.mp4"
    file.write_bytes(b"footage")
    stock_library.record_asset(
        library,
        provider="pixabay",
        candidate={
            "asset_id": 44,
            "page_url": "https://pixabay.com/videos/44/",
            "download_url": "https://cdn.pixabay.com/44.mp4",
            "width": 1920,
            "height": 1080,
            "duration": 12,
            "tags": "mountain",
        },
        query="mountain",
        local_path=str(file),
        downloaded=True,
    )
    monkeypatch.setattr(stock_library, "library_dir", lambda: library)

    overlays = await broll_overlays_for(
        [
            {"start": 3.0, "duration": 2.5, "provider": "pixabay", "asset_id": 44},
            # An evicted asset is skipped, not re-fetched.
            {"start": 8.0, "duration": 2.5, "provider": "coverr", "asset_id": "gone"},
        ]
    )

    assert overlays == [
        {"path": file, "start": 3.0, "duration": 2.5, "offset": 0.0}
    ]


async def test_overlays_for_keeps_the_legacy_pexels_path(monkeypatch, tmp_path):
    file = tmp_path / "pexels_9_1080x1920.mp4"
    file.write_bytes(b"footage")

    async def fake_ensure(entry):
        return file

    monkeypatch.setattr(broll, "ensure_broll_file", fake_ensure)

    overlays = await broll_overlays_for(
        [{"start": 1.0, "duration": 3.0, "provider": "pexels", "pexels_id": 9,
          "width": 1080, "height": 1920, "file_url": "https://videos.pexels.com/9.mp4"}]
    )

    assert overlays == [{"path": file, "start": 1.0, "duration": 3.0, "offset": 0.0}]


# --- editor search ------------------------------------------------------------


async def test_search_footage_answers_from_pixabay_then_coverr(monkeypatch):
    ensured = []

    def fake_ensure_pixabay(hit, query=""):
        ensured.append(("pixabay", hit["asset_id"]))
        return {**hit, "local_path": "/tmp/pixabay_43.mp4"}

    monkeypatch.setattr(pixabay_broll, "pixabay_configured", lambda: True)
    monkeypatch.setattr(pixabay_broll, "search_pixabay_videos", lambda q, per_page=18: [_hit(43)])
    monkeypatch.setattr(pixabay_broll, "ensure_pixabay_hit", fake_ensure_pixabay)
    monkeypatch.setattr(coverr_broll, "coverr_configured", lambda: False)

    results = await search_footage("mountain summit")

    assert [r["provider"] for r in results] == ["pixabay"]
    assert results[0]["id"] == 43
    assert results[0]["fileUrl"] == "https://cdn.pixabay.com/video/43.mp4"
    assert ensured == [("pixabay", 43)]

    # Pixabay unconfigured: Coverr answers instead.
    monkeypatch.setattr(pixabay_broll, "pixabay_configured", lambda: False)
    monkeypatch.setattr(coverr_broll, "coverr_configured", lambda: True)
    monkeypatch.setattr(
        coverr_broll,
        "search_coverr_videos",
        lambda q, per_page=18: [
            {
                "asset_id": "AbC123",
                "title": "mountain summit",
                "page_url": "",
                "download_url": "https://storage.coverr.co/videos/AbC123/download?token=x",
                "width": 1920,
                "height": 1080,
                "duration": 10,
                "tags": "mountain, summit",
                "preview_url": "",
                "thumbnail_url": "https://storage.coverr.co/p/AbC123",
            }
        ],
    )
    monkeypatch.setattr(
        coverr_broll,
        "ensure_coverr_hit",
        lambda hit, query="": {**hit, "local_path": "/tmp/coverr_AbC123.mp4", "provider": "coverr"},
    )

    results = await search_footage("mountain summit")

    assert [r["provider"] for r in results] == ["coverr"]
    assert results[0]["id"] == "AbC123"


# --- configuration gates -------------------------------------------------------


def test_stock_broll_configured_matrix(monkeypatch):
    monkeypatch.setattr(pixabay_broll, "pixabay_configured", lambda: True)
    monkeypatch.setattr(coverr_broll, "coverr_configured", lambda: False)
    assert stock_broll_configured() is True

    monkeypatch.setattr(pixabay_broll, "pixabay_configured", lambda: False)
    monkeypatch.setattr(coverr_broll, "coverr_configured", lambda: True)
    assert stock_broll_configured() is True

    monkeypatch.setattr(coverr_broll, "coverr_configured", lambda: False)
    assert stock_broll_configured() is False


async def test_broll_status_reports_the_v2_providers(monkeypatch):
    monkeypatch.setattr(pixabay_broll, "pixabay_configured", lambda: True)
    monkeypatch.setattr(coverr_broll, "coverr_configured", lambda: True)
    monkeypatch.setattr(broll, "broll_configured", lambda: False)
    assert await get_broll_status() == {"configured": True, "provider": "pixabay"}

    monkeypatch.setattr(pixabay_broll, "pixabay_configured", lambda: False)
    assert await get_broll_status() == {"configured": True, "provider": "coverr"}

    # A legacy Pexels key alone: stored footage still works, and the
    # status says so honestly.
    monkeypatch.setattr(coverr_broll, "coverr_configured", lambda: False)
    monkeypatch.setattr(broll, "broll_configured", lambda: True)
    assert await get_broll_status() == {"configured": True, "provider": "pexels"}

    monkeypatch.setattr(broll, "broll_configured", lambda: False)
    assert await get_broll_status() == {"configured": False, "provider": None}
