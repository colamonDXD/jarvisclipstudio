"""YouTube channel monitoring, Stage 2: the yt-dlp fallback
detector's extraction shape — tab merge, dedupe, entry fields —
with yt-dlp stubbed at the module seam. No live YouTube call.
"""

from __future__ import annotations

import pytest

from src import youtube_feed
from src.youtube_feed import ChannelFeedError, fetch_channel_entries_via_ytdlp

CHANNEL_ID = "UC1234567890abcdefghijkl"


class _FakeYDL:
    """Stands in for yt_dlp.YoutubeDL: serves canned tab payloads
    keyed by the tab in the URL, or raises for a tab in ``fail``."""

    payloads: dict = {}
    fail: set = set()
    seen_urls: list = []

    def __init__(self, opts):
        self.opts = opts

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def extract_info(self, url, download=False):
        type(self).seen_urls.append(url)
        tab = url.rsplit("/", 1)[-1]
        if tab in type(self).fail:
            raise RuntimeError(f"{tab} tab exploded")
        return {"entries": type(self).payloads.get(tab, [])}


@pytest.fixture
def fake_ydl(monkeypatch):
    _FakeYDL.payloads = {}
    _FakeYDL.fail = set()
    _FakeYDL.seen_urls = []
    monkeypatch.setattr(youtube_feed.yt_dlp, "YoutubeDL", _FakeYDL)
    return _FakeYDL


def _item(video_id, title="A video", url=None):
    return {
        "id": video_id,
        "title": title,
        "url": url or f"https://www.youtube.com/watch?v={video_id}",
    }


async def test_fallback_merges_tabs_and_dedupes(fake_ydl):
    fake_ydl.payloads = {
        "videos": [_item("vid00000001"), _item("vid00000002")],
        "streams": [_item("vid00000002"), _item("vod00000001")],
    }
    entries = await fetch_channel_entries_via_ytdlp(CHANNEL_ID)
    assert [e.video_id for e in entries] == [
        "vid00000001", "vid00000002", "vod00000001",
    ]
    assert all(e.published_at is None for e in entries)
    assert not any(e.is_short_link for e in entries)
    assert len(fake_ydl.seen_urls) == 2
    assert fake_ydl.seen_urls[0].endswith("/videos")
    assert fake_ydl.seen_urls[1].endswith("/streams")


async def test_fallback_flags_short_links_from_their_url(fake_ydl):
    fake_ydl.payloads = {
        "videos": [
            _item("short000001", url="https://www.youtube.com/shorts/short000001")
        ],
        "streams": [],
    }
    entries = await fetch_channel_entries_via_ytdlp(CHANNEL_ID)
    assert len(entries) == 1
    assert entries[0].is_short_link is True
    assert entries[0].url == "https://www.youtube.com/watch?v=short000001"


async def test_fallback_survives_one_tab_failing(fake_ydl):
    fake_ydl.payloads = {"videos": [_item("vid00000009")]}
    fake_ydl.fail = {"streams"}
    entries = await fetch_channel_entries_via_ytdlp(CHANNEL_ID)
    assert [e.video_id for e in entries] == ["vid00000009"]


async def test_fallback_raises_when_both_tabs_fail(fake_ydl):
    fake_ydl.fail = {"videos", "streams"}
    with pytest.raises(ChannelFeedError):
        await fetch_channel_entries_via_ytdlp(CHANNEL_ID)
