"""Smart B-Roll v2, Slice 6: editor review surface.

The design's §4.5 contract (SMART_BROLL_V2_DESIGN.md): for each B-roll
cue the existing clip editor shows the concept + a thumbnail, with
working "Remove insert" and "Next candidate" actions. The tests here
prove the wiring, not the UI: resolved EDL entries reach the editor as
clips (with their concepts), a removal actually drops the insert from
the rendered plan, and a next candidate swaps the footage through the
same resolver the first pick came from. Everything is mocked HTTP +
real local ffmpeg — no live API calls, no keys.
"""

import shutil
import subprocess

import pytest

from src import broll
from src.broll import (
    edl_broll_entry_to_placement,
    editor_piece_to_placement,
    ensure_footage_file,
    next_broll_candidate,
    placement_to_editor_clip,
)
from src.editor_document import BRollClip, EditDocument, mapped_broll, render_document
from src.media import coverr_broll, pixabay_broll, stock_library
from test_editor_broll import FOOTAGE, _pixel, _solid, doc


@pytest.fixture(autouse=True)
def _clear_search_caches():
    pixabay_broll._search_cache.clear()
    pixabay_broll._photo_search_cache.clear()
    coverr_broll._search_cache.clear()
    yield
    pixabay_broll._search_cache.clear()
    pixabay_broll._photo_search_cache.clear()
    coverr_broll._search_cache.clear()


# --- fixtures ---------------------------------------------------------------

PIXABAY_VIDEO = {
    "provider": "pixabay",
    "id": 44,
    "width": 1920,
    "height": 1080,
    "duration": 12,
    "fileUrl": "https://cdn.pixabay.com/44.mp4",
    "pageUrl": "https://pixabay.com/videos/44/",
    "thumbnail": "https://cdn.pixabay.com/44.jpg",
    "credit": "",
}

COVER_FOOTAGE = {
    "provider": "coverr",
    "id": "AbC123",
    "width": 1920,
    "height": 1080,
    "duration": 10,
    "fileUrl": "https://storage.coverr.co/videos/AbC123/download?token=x",
    "pageUrl": "",
    "thumbnail": "https://storage.coverr.co/p/AbC123",
    "credit": "",
}


def _edl_entry(**extra):
    entry = {
        "start": 13.54,
        "end": 16.54,
        "source_start": 0.0,
        "query": "market",
        "search_query": "stock market chart screen",
        "context": "trade worth $96,000 in profit loss today execute on my strategy",
        "concept": "Candlestick charts and a profit/loss graph on a trader's monitors.",
        "sense": "market:financial",
        "search_queries": ["stock market chart screen", "trading desk monitors", "candlestick graph"],
        "media": "either",
        "avoid": ["street market", "farmers market stalls", "shopping crowd"],
        "provider": "pixabay",
        "asset_id": 44,
        "url": "https://pixabay.com/videos/44/",
        "local_path": "/nonexistent/pixabay_44.mp4",
        "width": 1920,
        "height": 1080,
        "duration": 12.0,
        "thumbnail": "https://cdn.pixabay.com/44.jpg",
        "download_url": "https://cdn.pixabay.com/44.mp4",
    }
    entry.update(extra)
    return entry


def _pixabay_hit(asset_id, tags):
    """A normalised Pixabay search hit, as search_pixabay_videos returns it."""
    return {
        "asset_id": asset_id,
        "page_url": "https://pixabay.com/videos/x",
        "download_url": f"https://cdn.pixabay.com/{asset_id}.mp4",
        "width": 1920,
        "height": 1080,
        "duration": 12,
        "tags": tags,
        "preview_url": f"https://cdn.pixabay.com/{asset_id}_preview.mp4",
        "thumbnail_url": f"https://cdn.pixabay.com/{asset_id}.jpg",
    }


def _coverr_hit(asset_id, tags):
    """A normalised Coverr search hit, as search_coverr_videos returns it."""
    return {
        "asset_id": str(asset_id),
        "title": tags,
        "page_url": "",
        "download_url": f"https://storage.coverr.co/videos/{asset_id}/download?token=x",
        "width": 1920,
        "height": 1080,
        "duration": 10,
        "tags": tags,
        "preview_url": f"https://storage.coverr.co/videos/{asset_id}/preview?token=x",
        "thumbnail_url": f"https://storage.coverr.co/p/{asset_id}",
    }


def _pexels_video(video_id, slug):
    return {
        "id": video_id,
        "url": f"https://www.pexels.com/video/{slug}-{video_id}/",
        "duration": 12,
        "width": 1080,
        "height": 1920,
        "user": {"name": "Some Filmmaker"},
        "image": f"https://images.pexels.com/videos/{video_id}/pictures/preview-0.jpg",
        "video_files": [
            {
                "link": f"https://videos.pexels.com/video-files/{video_id}/{video_id}-hd_1080_1920_25fps.mp4",
                "width": 1080,
                "height": 1920,
                "file_type": "video/mp4",
            },
        ],
    }


# --- EDL entries -> editor placements ---------------------------------------


def test_edl_entry_becomes_an_editor_placement_with_its_concept(tmp_path):
    target = tmp_path / "pixabay_44.mp4"
    target.write_bytes(b"fake-video")
    clip = placement_to_editor_clip(
        edl_broll_entry_to_placement(_edl_entry(local_path=str(target))), 0, 20.0
    )

    assert clip is not None
    assert (clip["start"], clip["end"], clip["offset"]) == (13.54, 16.54, 0.0)
    assert clip["query"] == "market"
    assert clip["concept"] == "Candlestick charts and a profit/loss graph on a trader's monitors."
    assert clip["searchQuery"] == "stock market chart screen"
    assert clip["searchQueries"] == ["stock market chart screen", "trading desk monitors", "candlestick graph"]
    assert clip["avoid"] == ["street market", "farmers market stalls", "shopping crowd"]
    assert clip["media"] == "either"
    assert clip["footage"] == PIXABAY_VIDEO
    # The placement round-trips through a document: the review surface
    # reads the same fields the planner wrote.
    validated = EditDocument.model_validate(
        {"segments": [{"id": "s", "start": 0, "end": 20}], "broll": [clip]}
    )
    assert validated.broll[0].concept == clip["concept"]
    assert validated.broll[0].searchQueries == clip["searchQueries"]


def test_edl_photo_entry_bridges_as_its_ken_burns_clip(tmp_path):
    target = tmp_path / "pixabay_55_still.mp4"
    target.write_bytes(b"fake-video")
    entry = _edl_entry(
        start=9.5,
        end=12.5,
        asset_id=55,
        media="photo",
        local_path=str(target),
        width=1080,
        height=1920,
        duration=3.0,
        thumbnail="https://cdn.pixabay.com/photo55_640.jpg",
        download_url="https://cdn.pixabay.com/photo55.jpg",
    )
    clip = placement_to_editor_clip(edl_broll_entry_to_placement(entry), 0, 20.0)

    assert clip is not None
    assert clip["footage"]["width"] == 1080 and clip["footage"]["height"] == 1920
    assert clip["footage"]["duration"] == 3.0


def test_unresolved_edl_entries_have_nothing_to_review(tmp_path):
    assert edl_broll_entry_to_placement(_edl_entry(local_path="")) is None
    assert edl_broll_entry_to_placement(_edl_entry(local_path=str(tmp_path / "missing.mp4"))) is None
    # An entry resolved before the rendition stamp existed carries no
    # dimensions: the bridge cannot describe its footage, so it stays out.
    assert edl_broll_entry_to_placement(_edl_entry(width=0, height=0, duration=0)) is None


def test_coverr_edl_entry_bridges_with_its_string_id(tmp_path):
    target = tmp_path / "coverr_AbC123.mp4"
    target.write_bytes(b"fake-video")
    entry = _edl_entry(
        provider="coverr",
        asset_id="AbC123",
        url="",
        local_path=str(target),
        width=1920,
        height=1080,
        duration=10.0,
        thumbnail="https://storage.coverr.co/p/AbC123",
        download_url="https://storage.coverr.co/videos/AbC123/download?token=x",
    )
    clip = placement_to_editor_clip(edl_broll_entry_to_placement(entry), 0, 20.0)

    assert clip is not None
    assert clip["id"] == "broll-0-AbC123"
    assert clip["footage"] == COVER_FOOTAGE
    EditDocument.model_validate(
        {"segments": [{"id": "s", "start": 0, "end": 20}], "broll": [clip]}
    )


def test_resolve_stamps_the_rendition_for_the_bridge(monkeypatch, tmp_path):
    monkeypatch.setattr(pixabay_broll, "_api_key", lambda: "test-key")
    monkeypatch.setattr(
        pixabay_broll,
        "search_pixabay_videos",
        lambda query, *, per_page, client=None: [
            _pixabay_hit(44, "stock market, trading desk, chart, monitors, finance, business"),
        ],
    )
    lib = tmp_path / "stock_library"
    lib.mkdir()
    (lib / "pixabay_44.mp4").write_bytes(b"fake-video")
    monkeypatch.setattr(stock_library, "library_dir", lambda: lib)

    edl = {"template": "plain", "clip_id": "c", "task_id": "t", "broll": [_edl_entry(local_path="")]}
    assert pixabay_broll.resolve_edl_broll(edl, cache_dir=lib) == 1

    entry = edl["broll"][0]
    assert entry["provider"] == "pixabay" and entry["asset_id"] == 44
    assert (entry["width"], entry["height"]) == (1920, 1080)
    assert entry["duration"] == 12.0
    assert entry["thumbnail"] == "https://cdn.pixabay.com/44.jpg"
    assert entry["download_url"] == "https://cdn.pixabay.com/44.mp4"


# --- placements <-> editor clips --------------------------------------------


def test_pixabay_and_coverr_placements_open_as_editable_clips():
    pixabay = {
        "start": 4.0,
        "duration": 3.0,
        "query": "market",
        "concept": "Candlestick charts on a trader's monitors.",
        "search_query": "stock market chart screen",
        "search_queries": ["stock market chart screen"],
        "avoid": ["street market"],
        "media": "either",
        "provider": "pixabay",
        "asset_id": 44,
        "page_url": "https://pixabay.com/videos/44/",
        "file_url": "https://cdn.pixabay.com/44.mp4",
        "width": 1920,
        "height": 1080,
        "footage_duration": 12.0,
        "thumbnail": "https://cdn.pixabay.com/44.jpg",
        "credit": "",
    }
    coverr = {
        "start": 9.0,
        "duration": 3.0,
        "query": "city",
        "provider": "coverr",
        "asset_id": "AbC123",
        "page_url": "",
        "file_url": "https://storage.coverr.co/videos/AbC123/download?token=x",
        "width": 1920,
        "height": 1080,
        "footage_duration": 10.0,
        "thumbnail": "https://storage.coverr.co/p/AbC123",
        "credit": "",
    }
    clips = [
        placement_to_editor_clip(pixabay, 0, 20.0),
        placement_to_editor_clip(coverr, 1, 20.0),
    ]
    assert clips[0]["footage"] == PIXABAY_VIDEO
    assert clips[0]["concept"] == "Candlestick charts on a trader's monitors."
    assert clips[1]["footage"] == COVER_FOOTAGE
    EditDocument.model_validate(
        {"segments": [{"id": "s", "start": 0, "end": 20}], "broll": clips}
    )
    # A placement pointing at another provider's CDN is not imported.
    assert placement_to_editor_clip({**pixabay, "file_url": "https://videos.pexels.com/44.mp4"}, 0, 20.0) is None
    assert placement_to_editor_clip({**coverr, "file_url": "https://cdn.coverr.co/AbC123.mp4"}, 0, 20.0) is None


def test_editor_pieces_round_trip_provider_and_concept():
    original = EditDocument.model_validate(
        {
            "segments": [{"id": "s", "start": 0, "end": 20}],
            "broll": [
                {
                    "id": "b",
                    "start": 4,
                    "end": 7,
                    "offset": 0,
                    "query": "market",
                    "concept": "Candlestick charts on a trader's monitors.",
                    "searchQuery": "stock market chart screen",
                    "searchQueries": ["stock market chart screen"],
                    "avoid": ["street market"],
                    "media": "either",
                    "footage": PIXABAY_VIDEO,
                },
            ],
        }
    )
    piece = mapped_broll(original)[0]
    placement = editor_piece_to_placement(piece)
    assert placement["provider"] == "pixabay" and placement["asset_id"] == 44
    assert placement["concept"] == "Candlestick charts on a trader's monitors."
    assert placement["search_queries"] == ["stock market chart screen"]
    reopened = placement_to_editor_clip(placement, 0, 20.0)
    assert reopened is not None
    assert reopened["footage"] == PIXABAY_VIDEO
    assert reopened["concept"] == placement["concept"]
    assert reopened["searchQueries"] == placement["search_queries"]


# --- the library lookup ------------------------------------------------------


async def test_ensure_footage_file_finds_library_assets(tmp_path, monkeypatch):
    lib = tmp_path / "stock_library"
    lib.mkdir()
    video = lib / "pixabay_44.mp4"
    video.write_bytes(b"fake-video")
    stock_library.record_asset(
        lib,
        provider="pixabay",
        candidate={"asset_id": 44, "page_url": "https://pixabay.com/videos/44/",
                   "download_url": "https://cdn.pixabay.com/44.mp4", "width": 1920,
                   "height": 1080, "duration": 12, "tags": "stock market, chart"},
        query="market",
        search_query="stock market chart screen",
        local_path=str(video),
        downloaded=True,
    )
    still = lib / "pixabay_55_still.mp4"
    still.write_bytes(b"fake-video")
    stock_library.record_asset(
        lib,
        provider="pixabay",
        candidate={"asset_id": 55, "page_url": "https://pixabay.com/photos/55/",
                   "download_url": "https://cdn.pixabay.com/photo55.jpg", "width": 1920,
                   "height": 1080, "duration": 3, "tags": "stock market, chart"},
        query="market",
        search_query="stock market chart screen",
        local_path=str(still),
        downloaded=True,
    )
    monkeypatch.setattr(stock_library, "library_dir", lambda: lib)

    assert await ensure_footage_file(PIXABAY_VIDEO) == video
    # A photo win's Ken Burns clip is filed under its _still name; the
    # manifest (not the conventional name) finds it.
    assert await ensure_footage_file({**PIXABAY_VIDEO, "id": 55}) == still
    # An evicted asset is a miss, not a download: the export skips the
    # cutaway rather than fetching a possibly wrong file.
    assert await ensure_footage_file({**PIXABAY_VIDEO, "id": 999}) is None


# --- next candidate ----------------------------------------------------------


async def test_next_candidate_falls_through_to_the_next_pixabay_hit(monkeypatch, tmp_path):
    monkeypatch.setattr(pixabay_broll, "_api_key", lambda: "test-key")
    monkeypatch.setattr(
        pixabay_broll,
        "search_pixabay_videos",
        lambda query, *, per_page, client=None: [
            _pixabay_hit(33, "market, street, farmers, market stalls, vegetables, crowd, shopping"),
            _pixabay_hit(44, "stock market, trading desk, chart, monitors, finance, business"),
            _pixabay_hit(55, "stock market, chart, trading screens, finance, graph"),
        ],
    )
    lib = tmp_path / "stock_library"
    lib.mkdir()
    (lib / "pixabay_55.mp4").write_bytes(b"fake-video")
    monkeypatch.setattr(stock_library, "library_dir", lambda: lib)

    clip = {
        "id": "b1",
        "start": 13.54,
        "end": 16.54,
        "offset": 0,
        "query": "market",
        "concept": "Candlestick charts and a profit/loss graph on a trader's monitors.",
        "searchQuery": "stock market chart screen",
        "searchQueries": ["stock market chart screen", "trading desk monitors", "candlestick graph"],
        "avoid": ["street market", "farmers market stalls", "shopping crowd"],
        "media": "either",
        "footage": PIXABAY_VIDEO,
    }
    replacement = await next_broll_candidate(clip)

    # The street-market hit is gate-rejected, the current asset is
    # skipped, and the next trading hit wins — same resolver, same gate.
    assert replacement is not None
    assert replacement["id"] == "b1"
    assert (replacement["start"], replacement["end"]) == (13.54, 16.54)
    assert replacement["offset"] == 0
    assert replacement["footage"]["id"] == 55
    assert replacement["footage"]["provider"] == "pixabay"
    assert replacement["footage"]["fileUrl"] == "https://cdn.pixabay.com/55.mp4"
    assert replacement["concept"] == clip["concept"]


async def test_next_candidate_returns_none_when_the_pool_is_exhausted(monkeypatch, tmp_path):
    monkeypatch.setattr(pixabay_broll, "_api_key", lambda: "test-key")
    monkeypatch.setattr(
        pixabay_broll,
        "search_pixabay_videos",
        lambda query, *, per_page, client=None: [
            _pixabay_hit(44, "stock market, trading desk, chart, monitors, finance, business"),
        ],
    )
    lib = tmp_path / "stock_library"
    lib.mkdir()
    monkeypatch.setattr(stock_library, "library_dir", lambda: lib)
    clip = {
        "id": "b1",
        "start": 13.54,
        "end": 16.54,
        "offset": 0,
        "query": "market",
        "concept": "Candlestick charts on a trader's monitors.",
        "searchQuery": "stock market chart screen",
        "footage": PIXABAY_VIDEO,
    }
    assert await next_broll_candidate(clip) is None
    assert await next_broll_candidate(clip, exclude_ids=[44, 45]) is None


async def test_next_candidate_falls_through_on_coverr(monkeypatch, tmp_path):
    monkeypatch.setattr(coverr_broll, "_api_key", lambda: "test-key")
    monkeypatch.setattr(
        coverr_broll,
        "search_coverr_videos",
        lambda query, *, per_page, client=None: [
            _coverr_hit("AbC123", "stock market, trading, charts"),
            _coverr_hit("XyZ789", "stock market, trading desk, finance"),
        ],
    )
    lib = tmp_path / "stock_library"
    lib.mkdir()
    (lib / "coverr_XyZ789.mp4").write_bytes(b"fake-video")
    monkeypatch.setattr(stock_library, "library_dir", lambda: lib)
    clip = {
        "id": "b1",
        "start": 4.0,
        "end": 7.0,
        "offset": 0,
        "query": "market",
        "searchQuery": "stock market chart screen",
        "footage": COVER_FOOTAGE,
    }
    replacement = await next_broll_candidate(clip)

    assert replacement is not None
    assert replacement["footage"]["id"] == "XyZ789"
    assert replacement["footage"]["provider"] == "coverr"
    assert replacement["footage"]["fileUrl"] == "https://storage.coverr.co/videos/XyZ789/download?token=x"


async def test_next_candidate_migrates_a_pexels_cue_to_v2(monkeypatch, tmp_path):
    # Since the B-roll consolidation, a legacy Pexels cue's next
    # candidate comes from the v2 resolver: the cue migrates provider
    # instead of re-searching the retired Pexels path.
    monkeypatch.setattr(pixabay_broll, "_api_key", lambda: "test-key")
    monkeypatch.setattr(
        pixabay_broll,
        "search_pixabay_videos",
        lambda query, *, per_page, client=None: [
            _pixabay_hit(43, "mountain summit, peak, hiking trail, alpine"),
        ],
    )
    lib = tmp_path / "stock_library"
    lib.mkdir()
    (lib / "pixabay_43.mp4").write_bytes(b"fake-video")
    monkeypatch.setattr(stock_library, "library_dir", lambda: lib)
    clip = {
        "id": "b1",
        "start": 4.0,
        "end": 7.0,
        "offset": 0,
        "query": "mountain summit",
        "footage": FOOTAGE,
    }
    replacement = await next_broll_candidate(clip)

    assert replacement is not None
    assert replacement["footage"]["id"] == 43
    assert replacement["footage"]["provider"] == "pixabay"
    assert replacement["footage"]["fileUrl"] == "https://cdn.pixabay.com/43.mp4"
    assert (replacement["start"], replacement["end"]) == (4.0, 7.0)
    assert replacement["offset"] == 0


# --- rendered proof: remove drops the insert, swap changes it ---------------


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="ffmpeg required")
def test_removing_a_clip_drops_it_from_the_render(tmp_path):
    _solid(tmp_path / "clean.mp4", "red", 6, "320x180")
    footage = tmp_path / "footage.mp4"
    _solid(footage, "blue", 8, "180x320")
    draft = doc(
        segments=((0, 6),), broll=((1, 4, 0),),
        framing={"aspect": "original"}, captions={"enabled": False},
    )
    render_document(tmp_path, "keep", draft, "tiktok", {"pexels:42/1080x1920": footage})
    red, _, blue = _pixel(tmp_path / "export-keep.mp4", 2.5)
    assert blue > 200 and red < 60

    # The review surface's Remove is a document edit, not a hide flag:
    # the clip leaves the draft, so the re-render has no cutaway at all.
    removed = draft.model_copy(update={"broll": []})
    render_document(tmp_path, "removed", removed, "tiktok", {"pexels:42/1080x1920": footage})
    red, _, blue = _pixel(tmp_path / "export-removed.mp4", 2.5)
    assert red > 200 and blue < 60


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="ffmpeg required")
async def test_next_candidate_swap_changes_the_rendered_footage(tmp_path, monkeypatch):
    _solid(tmp_path / "clean.mp4", "red", 6, "320x180")
    blue = tmp_path / "blue.mp4"
    _solid(blue, "blue", 8, "180x320")
    green = tmp_path / "green.mp4"
    _solid(green, "green", 8, "180x320")

    # The draft's cue holds legacy Pexels footage; since the
    # consolidation its next candidate resolves through Pixabay and
    # the cue migrates provider — the swap must still change the
    # rendered frames.
    monkeypatch.setattr(pixabay_broll, "_api_key", lambda: "test-key")
    monkeypatch.setattr(
        pixabay_broll,
        "search_pixabay_videos",
        lambda query, *, per_page, client=None: [
            _pixabay_hit(43, "mountain summit, peak, hiking trail, alpine"),
        ],
    )
    lib = tmp_path / "stock_library"
    lib.mkdir()
    (lib / "pixabay_43.mp4").write_bytes(b"fake-video")
    monkeypatch.setattr(stock_library, "library_dir", lambda: lib)
    draft = doc(
        segments=((0, 6),), broll=((1, 4, 0),),
        framing={"aspect": "original"}, captions={"enabled": False},
    )
    # The cue needs its search text: the doc() helper leaves query blank.
    draft = draft.model_copy(
        update={"broll": [draft.broll[0].model_copy(update={"query": "mountain summit"})]}
    )
    render_document(tmp_path, "before", draft, "tiktok", {"pexels:42/1080x1920": blue})
    _, _, blue_before = _pixel(tmp_path / "export-before.mp4", 2.5)
    assert blue_before > 200

    swapped = await next_broll_candidate(draft.broll[0].model_dump())
    assert swapped is not None and swapped["footage"]["id"] == 43
    assert swapped["footage"]["provider"] == "pixabay"
    swapped_doc = draft.model_copy(update={"broll": [BRollClip.model_validate(swapped)]})
    footage = swapped["footage"]
    key = f"{footage['provider']}:{footage['id']}/{footage['width']}x{footage['height']}"
    render_document(tmp_path, "after", swapped_doc, "tiktok", {key: green})
    red, green_after, blue_after = _pixel(tmp_path / "export-after.mp4", 2.5)
    assert green_after > 100 and red < 60 and blue_after < 60
