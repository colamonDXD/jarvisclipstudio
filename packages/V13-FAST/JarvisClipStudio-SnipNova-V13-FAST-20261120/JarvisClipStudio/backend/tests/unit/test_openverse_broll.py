"""Keyless B-roll: Openverse CC0/PDM photos as the third resolver source."""
from __future__ import annotations

from pathlib import Path

import httpx
import pytest

from src.media import coverr_broll, openverse_broll, pixabay_broll


def _results(*items):
    return {"results": list(items)}


def _item(i, title, tags, licence="cc0", w=2000, h=1500):
    return {
        "id": f"id{i}", "title": title, "url": f"https://img.example/{i}.jpg",
        "foreign_landing_url": f"https://example/{i}", "license": licence,
        "width": w, "height": h, "tags": [{"name": t} for t in tags],
        "thumbnail": f"https://img.example/{i}_t.jpg", "creator": "x", "source": "flickr",
    }


def _client(payload, seen):
    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        if request.url.host == "api.openverse.org":
            return httpx.Response(200, json=payload)
        return httpx.Response(200, content=b"\xff\xd8fakejpeg")
    return httpx.Client(transport=httpx.MockTransport(handler))


@pytest.fixture
def keyless(monkeypatch, tmp_path):
    monkeypatch.setattr(pixabay_broll, "_api_key", lambda: "")
    monkeypatch.setattr(coverr_broll, "_api_key", lambda: "")
    monkeypatch.setenv("BROLL_OPENVERSE", "on")
    openverse_broll._search_cache.clear()

    def fake_clip(still, duration, out):
        Path(out).write_bytes(b"mp4")
        return True

    monkeypatch.setattr(pixabay_broll, "still_to_clip", fake_clip)
    monkeypatch.setattr(pixabay_broll.stock_library, "record_use", lambda *a, **k: None)
    return tmp_path


def test_keyless_install_fills_cue_from_openverse(keyless):
    seen: list[str] = []
    client = _client(_results(_item(1, "Stack of money", ["money", "cash", "dollar"])), seen)
    edl = {"broll": [{"query": "money", "start": 1.0, "end": 3.5, "local_path": "", "asset_id": None}]}
    assert pixabay_broll.resolve_edl_broll(edl, cache_dir=keyless, client=client) == 1
    entry = edl["broll"][0]
    assert entry["provider"] == "openverse"
    assert entry["media"] == "photo"
    assert Path(entry["local_path"]).is_file()
    search = next(u for u in seen if "api.openverse.org" in u)
    assert "license=cc0%2Cpdm" in search and "mature=false" in search


def test_search_drops_non_cc0_and_small_images(keyless):
    seen: list[str] = []
    client = _client(_results(
        _item(1, "money", ["money"], licence="by"),
        _item(2, "money", ["money"], w=400, h=300),
        _item(3, "money", ["money"]),
    ), seen)
    hits = openverse_broll.search_openverse_photos("money", client=client)
    assert [h["asset_id"] for h in hits] == ["ov_id3"]


def test_off_topic_hit_is_gated(keyless):
    client = _client(_results(_item(1, "Red sports car", ["car", "vehicle", "road"])), [])
    edl = {"broll": [{"query": "money", "start": 0.0, "end": 2.0, "local_path": "", "asset_id": None}]}
    assert pixabay_broll.resolve_edl_broll(edl, cache_dir=keyless, client=client) == 0
    assert edl["broll"][0]["not_inserted"]["reason"] == "gate_rejected"


def test_video_only_cue_never_uses_photos(keyless):
    client = _client(_results(_item(1, "money", ["money"])), [])
    assert openverse_broll.ensure_openverse_asset(
        "money", cache_dir=keyless, client=client, media="video") is None


def test_off_switch_restores_no_provider_stamp(keyless, monkeypatch):
    monkeypatch.setenv("BROLL_OPENVERSE", "off")
    edl = {"broll": [{"query": "money", "local_path": "", "asset_id": None}]}
    assert pixabay_broll.resolve_edl_broll(edl, cache_dir=keyless) == 0
    assert edl["broll"][0]["not_inserted"]["reason"] == "no_provider_configured"
