"""Pixabay B-roll lane: client, EDL placement, and renderer integration.

The Pixabay client is exercised entirely through an httpx MockTransport
(no network). EDL placement tests pin the hook/punch-in/coverage rules.
The render tests run real FFmpeg against synthetic sources and a fake
"downloaded" stock file.
"""

import shutil
import subprocess
from pathlib import Path

import httpx
import pytest

from src.auto_director.edl import build_edl, load_profile
from src.media import edl_render, pixabay_broll


@pytest.fixture(autouse=True)
def _clear_search_cache():
    pixabay_broll._search_cache.clear()
    yield
    pixabay_broll._search_cache.clear()


@pytest.fixture()
def api_key(monkeypatch):
    monkeypatch.setattr(pixabay_broll, "_api_key", lambda: "test-key")


# --------------------------------------------------------------------------
# Pixabay client (mocked HTTP)
# --------------------------------------------------------------------------

def _hit(asset_id, page_url, renditions):
    return {
        "id": asset_id,
        "pageURL": page_url,
        "duration": 12,
        "videos": {
            name: {"url": url, "width": w, "height": h, "size": 1000}
            for name, (url, w, h) in renditions.items()
        },
    }


def _mock_client(handler):
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_search_prefers_smallest_sufficient_portrait(api_key):
    payload = {"total": 1, "totalHits": 1, "hits": [_hit(42, "https://pixabay.com/videos/42", {
        "tiny": ("https://cdn.pixabay.com/tiny.mp4", 426, 240),
        "small": ("https://cdn.pixabay.com/small.mp4", 640, 360),
        "medium": ("https://cdn.pixabay.com/medium.mp4", 1280, 720),
        "portrait_small": ("https://cdn.pixabay.com/p720.mp4", 720, 1280),
        "portrait_large": ("https://cdn.pixabay.com/p1080.mp4", 1080, 1920),
    })]}

    def handler(request):
        assert "pixabay.com/api/videos" in str(request.url)
        return httpx.Response(200, json=payload)

    results = pixabay_broll.search_pixabay_videos("mountain lake", client=_mock_client(handler))

    assert len(results) == 1
    assert results[0]["asset_id"] == 42
    assert results[0]["page_url"] == "https://pixabay.com/videos/42"
    # Portrait beats landscape; the 720-wide cut is enough for a 1080 frame.
    assert results[0]["download_url"] == "https://cdn.pixabay.com/p720.mp4"
    assert results[0]["width"] == 720 and results[0]["height"] == 1280


def test_search_falls_back_to_largest_landscape(api_key):
    payload = {"total": 1, "totalHits": 1, "hits": [_hit(7, "https://pixabay.com/videos/7", {
        "small": ("https://cdn.pixabay.com/s.mp4", 640, 360),
        "large": ("https://cdn.pixabay.com/l.mp4", 1920, 1080),
    })]}

    def handler(request):
        return httpx.Response(200, json=payload)

    results = pixabay_broll.search_pixabay_videos("city traffic", client=_mock_client(handler))
    assert results[0]["download_url"] == "https://cdn.pixabay.com/l.mp4"


def test_no_key_disables_everything(monkeypatch, tmp_path):
    monkeypatch.setattr(pixabay_broll, "_api_key", lambda: "")
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(200, json={"hits": []})

    client = _mock_client(handler)
    assert pixabay_broll.pixabay_configured() is False
    assert pixabay_broll.search_pixabay_videos("mountain", client=client) == []
    edl = {"broll": [{"query": "mountain", "local_path": ""}]}
    assert pixabay_broll.resolve_edl_broll(edl, cache_dir=tmp_path, client=client) == 0
    assert edl["broll"][0]["local_path"] == ""
    assert calls == []  # no key -> not even a search request


def test_search_failure_is_silent(api_key):
    def handler(request):
        return httpx.Response(500, json={})

    client = _mock_client(handler)
    assert pixabay_broll.search_pixabay_videos("ocean", client=client) == []
    assert pixabay_broll.ensure_pixabay_asset("ocean", client=client) is None


def test_download_is_cached_on_disk(api_key, tmp_path):
    payload = {"total": 1, "totalHits": 1, "hits": [_hit(99, "https://pixabay.com/videos/99", {
        "large": ("https://cdn.pixabay.com/asset99.mp4", 1920, 1080),
    })]}
    counts = {"search": 0, "download": 0}

    def handler(request):
        if "pixabay.com/api" in str(request.url):
            counts["search"] += 1
            return httpx.Response(200, json=payload)
        counts["download"] += 1
        return httpx.Response(200, content=b"fake-mp4-bytes")

    client = _mock_client(handler)
    first = pixabay_broll.ensure_pixabay_asset("waterfall", cache_dir=tmp_path, client=client)
    assert first is not None
    assert Path(first["local_path"]).read_bytes() == b"fake-mp4-bytes"
    assert counts == {"search": 1, "download": 1}

    # Second resolve of the same asset: even with the in-process search
    # cache cleared, the file on disk wins and nothing is re-downloaded.
    pixabay_broll._search_cache.clear()
    second = pixabay_broll.ensure_pixabay_asset("waterfall", cache_dir=tmp_path, client=client)
    assert second["local_path"] == first["local_path"]
    assert counts["download"] == 1


def test_resolve_stamps_provenance(api_key, tmp_path):
    payload = {"total": 1, "totalHits": 1, "hits": [_hit(5, "https://pixabay.com/videos/5", {
        "large": ("https://cdn.pixabay.com/asset5.mp4", 1920, 1080),
    })]}

    def handler(request):
        if "pixabay.com/api" in str(request.url):
            return httpx.Response(200, json=payload)
        return httpx.Response(200, content=b"bytes")

    edl = {"broll": [
        {"query": "beach sunset", "local_path": "", "asset_id": None},
        {"query": "", "local_path": ""},
        {"query": "user clip", "local_path": "/already/local.mp4"},
    ]}
    resolved = pixabay_broll.resolve_edl_broll(
        edl, cache_dir=tmp_path, client=_mock_client(handler)
    )

    assert resolved == 1
    entry = edl["broll"][0]
    assert entry["asset_id"] == 5
    assert entry["url"] == "https://pixabay.com/videos/5"
    assert entry["provider"] == "pixabay"
    assert Path(entry["local_path"]).is_file()
    assert edl["broll"][2]["local_path"] == "/already/local.mp4"


# --------------------------------------------------------------------------
# EDL placement
# --------------------------------------------------------------------------

def _words(*nouns):
    return [
        {"word": noun, "start": float(i), "end": float(i) + 0.5}
        for i, noun in enumerate(nouns)
    ]


def test_placements_respect_hook_and_coverage():
    profile = load_profile("travel_vlog")
    clip = {"id": "c1", "start": 0, "end": 60, "duration": 60}
    edl = build_edl(clip, _words("mountain", "beach", "city"), profile, [])

    assert edl["broll"], "travel vlog should plan B-roll"
    total = 0.0
    for entry in edl["broll"]:
        assert entry["start"] >= 2.0, "hook window must stay on the speaker"
        assert entry["end"] <= 59.0
        assert entry["end"] - entry["start"] >= 1.5
        assert entry["local_path"] == ""  # unresolved until render time
        assert entry["asset_id"] is None
        total += entry["end"] - entry["start"]
    assert total <= 0.30 * 60 + 1e-9, "B-roll share is capped at 30%"


def test_placements_avoid_punch_in_moments():
    profile = load_profile("travel_vlog")
    clip = {"id": "c2", "start": 0, "end": 60, "duration": 60}
    # Travel builds 6s segments; an energy peak at 6.0 puts a punch-in
    # zoom exactly on the first topic-change boundary.
    edl = build_edl(clip, _words("mountain", "beach"), profile, [6.0])

    for entry in edl["broll"]:
        assert not (entry["start"] < 6.75 and 5.75 < entry["end"]), (
            "B-roll must not cover the punch-in at 6.0s"
        )
    # The blocked slot is skipped, not shifted onto the punch-in.
    assert all(entry["start"] != 6.0 for entry in edl["broll"])


def test_placements_disabled_profile_and_short_density():
    clip = {"id": "c3", "start": 0, "end": 30, "duration": 30}
    meme = build_edl(clip, _words("funny", "cat"), load_profile("meme_comedy"), [])
    assert meme["broll"] == []

    edu = build_edl(clip, _words("chart", "data"), load_profile("education_tutorial"), [])
    assert len(edu["broll"]) == 1  # density 0.5/min over 30s rounds to one cue
    assert edu["broll"][0]["start"] >= 2.0


def test_placements_are_deterministic():
    profile = load_profile("travel_vlog")
    clip = {"id": "c4", "start": 0, "end": 45, "duration": 45}
    words = _words("mountain", "beach", "hotel")
    first = build_edl(clip, words, profile, [10.0])["broll"]
    second = build_edl(clip, words, profile, [10.0])["broll"]
    assert first == second


# --------------------------------------------------------------------------
# Renderer graph + real render
# --------------------------------------------------------------------------

def _graph_edl():
    return {
        "duration": 6,
        "segments": [{"start": 0, "end": 6, "transition_in": "none", "transition_duration": 0}],
        "overlays": [],
        "broll": [],
    }


def test_graph_ignores_unresolved_entries(tmp_path):
    edl = _graph_edl()
    edl["broll"] = [{"query": "ocean", "start": 2.0, "end": 5.0, "local_path": ""}]
    inputs, graph, _, _ = edl_render.build_edl_filter_graph(edl, tmp_path / "in.mp4")
    baseline_inputs, baseline_graph, _, _ = edl_render.build_edl_filter_graph(
        _graph_edl(), tmp_path / "in.mp4"
    )
    assert inputs == baseline_inputs == []
    assert graph == baseline_graph


def test_graph_composites_resolved_entry(tmp_path):
    stock = tmp_path / "stock.mp4"
    stock.touch()
    edl = _graph_edl()
    edl["broll"] = [{
        "query": "ocean", "start": 2.0, "end": 5.0,
        "local_path": str(stock), "source_start": 1.0,
    }]
    inputs, graph, _, _ = edl_render.build_edl_filter_graph(edl, tmp_path / "in.mp4")

    assert inputs == ["-stream_loop", "-1", "-i", str(stock)]
    assert "[1:v]trim=start=1.000:end=4.000" in graph
    assert "setpts=PTS-STARTPTS+2.000/TB" in graph
    assert "overlay=x=0:y=0:enable='between(t\\,2.000\\,5.000)'" in graph


def test_graph_broll_shifts_reference_image_input_index(tmp_path):
    stock = tmp_path / "stock.mp4"
    stock.touch()
    image = tmp_path / "ref.png"
    image.touch()
    edl = _graph_edl()
    edl["broll"] = [{"query": "ocean", "start": 2.0, "end": 5.0, "local_path": str(stock)}]
    edl["overlays"] = [{
        "type": "reference_image", "time": 1.0, "duration": 3.0, "local_path": str(image),
    }]
    inputs, graph, _, _ = edl_render.build_edl_filter_graph(edl, tmp_path / "in.mp4")

    assert inputs.count("-i") == 2
    # B-roll took input 1, so the reference image must read input 2.
    assert "[2:v]scale=360:-1" in graph


def _make_video(path: Path, color: str, duration: int, audio_freq: int = 440):
    subprocess.run(
        [
            "ffmpeg", "-y",
            "-f", "lavfi", "-i", f"color=c={color}:size=320x240:rate=30:duration={duration}",
            "-f", "lavfi", "-i", f"sine=frequency={audio_freq}:sample_rate=48000:duration={duration}",
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
    return (
        sum(data[0::3]) / n, sum(data[1::3]) / n, sum(data[2::3]) / n,
    )


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg required")
def test_render_with_fake_downloaded_broll(tmp_path):
    source = tmp_path / "source.mp4"
    _make_video(source, "red", 6)
    stock = tmp_path / "pixabay_123.mp4"
    _make_video(stock, "blue", 4, audio_freq=1200)

    edl = _graph_edl()
    edl["broll"] = [{
        "query": "ocean", "start": 2.0, "end": 5.0,
        "local_path": str(stock), "asset_id": 123,
    }]
    output = tmp_path / "out.mp4"
    assert edl_render.render_edl_clip_ffmpeg(source, output, edl) is True
    assert output.is_file() and output.stat().st_size > 0

    # Before the window the speaker (red) is on screen; inside the
    # window the stock cutaway (blue) covers the frame.
    r_before, _, b_before = _frame_rgb(output, 0.5)
    r_in, _, b_in = _frame_rgb(output, 3.5)
    assert r_before > 120 and b_before < 90
    assert b_in > 120 and r_in < 90
