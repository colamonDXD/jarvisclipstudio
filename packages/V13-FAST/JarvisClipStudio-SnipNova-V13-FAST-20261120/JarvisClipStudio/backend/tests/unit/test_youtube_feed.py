"""YouTube channel RSS feed: parsing, Shorts-link detection,
resolution inputs and failure shapes.

The HTTP layer is stubbed (a fake ``requests`` module swapped into
``youtube_feed``), so no live YouTube call happens in tests.
"""

from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from src import youtube_feed
from src.youtube_feed import (
    ChannelFeedError,
    fetch_channel_feed,
    parse_channel_feed,
    resolve_channel,
)

CHANNEL_ID = "UC1234567890abcdefghijkl"  # 24 chars, UC + 22

FEED_XML = f"""<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns:yt="http://www.youtube.com/xml/schemas/2015"
      xmlns:media="http://search.yahoo.com/mrss/"
      xmlns="http://www.w3.org/2005/Atom">
  <title>Speed Clips Channel</title>
  <entry>
    <id>yt:video:vid00000001</id>
    <yt:videoId>vid00000001</yt:videoId>
    <title>Third stream VOD</title>
    <link rel="alternate" href="https://www.youtube.com/watch?v=vid00000001"/>
    <published>2026-11-10T18:00:00+00:00</published>
    <updated>2026-11-10T18:05:00+00:00</updated>
  </entry>
  <entry>
    <id>yt:video:vid00000002</id>
    <yt:videoId>vid00000002</yt:videoId>
    <title>A quick short</title>
    <link rel="alternate" href="https://www.youtube.com/shorts/vid00000002"/>
    <published>2026-11-10T12:00:00+00:00</published>
    <updated>2026-11-10T12:05:00+00:00</updated>
  </entry>
  <entry>
    <id>yt:video:vid00000003</id>
    <yt:videoId>vid00000003</yt:videoId>
    <title>First upload</title>
    <link rel="alternate" href="https://www.youtube.com/watch?v=vid00000003"/>
    <published>2026-11-09T09:30:00+00:00</published>
    <updated>2026-11-09T09:35:00+00:00</updated>
  </entry>
</feed>
""".encode()


def test_parse_feed_entries_newest_first_with_shorts_flag():
    title, entries = parse_channel_feed(FEED_XML)
    assert title == "Speed Clips Channel"
    assert [e.video_id for e in entries] == [
        "vid00000001", "vid00000002", "vid00000003",
    ]
    first, short, _third = entries
    assert first.title == "Third stream VOD"
    assert first.url == "https://www.youtube.com/watch?v=vid00000001"
    assert first.is_short_link is False
    assert first.published_at == datetime(2026, 11, 10, 18, 0, tzinfo=timezone.utc)
    assert short.is_short_link is True
    # A Shorts link still resolves to the one canonical watch URL.
    assert short.url == "https://www.youtube.com/watch?v=vid00000002"


def test_parse_feed_with_no_entries():
    title, entries = parse_channel_feed(
        b'<feed xmlns="http://www.w3.org/2005/Atom"><title>Quiet</title></feed>'
    )
    assert title == "Quiet"
    assert entries == []


def test_parse_malformed_feed_raises_plain_error():
    with pytest.raises(ChannelFeedError):
        parse_channel_feed(b"<feed><entry>not closed")


def _stub_requests(monkeypatch, *, status=200, content=FEED_XML, seen=None):
    def fake_get(url, headers=None, timeout=None):
        if seen is not None:
            seen.append(url)
        return SimpleNamespace(status_code=status, content=content)

    monkeypatch.setattr(
        youtube_feed, "requests",
        SimpleNamespace(get=fake_get, RequestException=Exception),
    )


async def test_fetch_channel_feed_uses_rss_url(monkeypatch):
    seen = []
    _stub_requests(monkeypatch, seen=seen)
    entries = await fetch_channel_feed(CHANNEL_ID)
    assert seen == [
        f"https://www.youtube.com/feeds/videos.xml?channel_id={CHANNEL_ID}"
    ]
    assert len(entries) == 3


async def test_fetch_channel_feed_http_error_is_a_feed_error(monkeypatch):
    _stub_requests(monkeypatch, status=500, content=b"")
    with pytest.raises(ChannelFeedError) as excinfo:
        await fetch_channel_feed(CHANNEL_ID)
    assert "500" in str(excinfo.value)


async def test_resolve_bare_channel_id(monkeypatch):
    _stub_requests(monkeypatch)
    channel_id, title = await resolve_channel(CHANNEL_ID)
    assert channel_id == CHANNEL_ID
    assert title == "Speed Clips Channel"


async def test_resolve_channel_url_with_id(monkeypatch):
    _stub_requests(monkeypatch)
    channel_id, _title = await resolve_channel(
        f"https://www.youtube.com/channel/{CHANNEL_ID}"
    )
    assert channel_id == CHANNEL_ID


async def test_resolve_handle_goes_through_extraction(monkeypatch):
    _stub_requests(monkeypatch)
    calls = []

    def fake_extract(url):
        calls.append(url)
        return CHANNEL_ID, "Extracted Title"

    monkeypatch.setattr(youtube_feed, "_extract_channel_sync", fake_extract)
    channel_id, title = await resolve_channel("@speedclips")
    assert channel_id == CHANNEL_ID
    # The feed's own title wins over the extraction's.
    assert title == "Speed Clips Channel"
    assert calls == ["https://www.youtube.com/@speedclips"]


async def test_resolve_handle_url_with_tab(monkeypatch):
    _stub_requests(monkeypatch)
    monkeypatch.setattr(
        youtube_feed, "_extract_channel_sync",
        lambda url: (CHANNEL_ID, "Extracted Title"),
    )
    channel_id, _title = await resolve_channel("https://www.youtube.com/@speedclips/videos")
    assert channel_id == CHANNEL_ID


async def test_resolve_junk_input_is_refused(monkeypatch):
    _stub_requests(monkeypatch)
    for junk in ["", "   ", "not a channel", "https://example.com/@someone",
                 "https://www.youtube.com/watch?v=vid00000001"]:
        with pytest.raises(ChannelFeedError):
            await resolve_channel(junk)


async def test_resolve_unfetchable_feed_is_refused(monkeypatch):
    _stub_requests(monkeypatch, status=404, content=b"")
    with pytest.raises(ChannelFeedError):
        await resolve_channel(CHANNEL_ID)
