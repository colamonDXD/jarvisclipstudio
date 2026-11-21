"""YouTube channel RSS feed: fetch, parse, and channel resolution.

Channel monitoring polls the published uploads feed
(``https://www.youtube.com/feeds/videos.xml?channel_id=UC...``): no
API key, no quota, and YouTube serves it with a 15-minute cache
header, which is also the watcher's poll cadence. The feed carries
the 15 most recent uploads, newest first, Shorts and live archives
included. A Short is recognisable by its ``/shorts/`` link form —
that is the filter signal, because the feed has no duration field
(durations come from the yt-dlp preflight at task-creation time).

Handles are not feed parameters (``?handle=`` is a 400), so a channel
is resolved to its ID once, at add time: an ID in the pasted input is
used directly, anything else goes through one flat yt-dlp extraction
of the channel page. Resolution is only accepted when a real feed
fetch for the resolved ID returns and parses, so junk input can
never reach the monitors table.

The same yt-dlp machinery doubles as the fallback detector
(:func:`fetch_channel_entries_via_ytdlp`): when the feed errors or
answers empty, one flat pass over the channel's ``videos`` and
``streams`` tabs recovers the newest uploads. It is metered by the
watcher, never the primary path.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import datetime
from typing import Optional
from urllib.parse import urlparse
from xml.etree import ElementTree

import requests
import yt_dlp

from .utils.async_helpers import run_in_thread
from .youtube_utils import canonical_youtube_url

logger = logging.getLogger(__name__)

FEED_URL_TEMPLATE = "https://www.youtube.com/feeds/videos.xml?channel_id={channel_id}"

_ATOM = "http://www.w3.org/2005/Atom"
_YT = "http://www.youtube.com/xml/schemas/2015"

_CHANNEL_ID_RE = re.compile(r"^UC[A-Za-z0-9_-]{22}$")
_HANDLE_RE = re.compile(r"^@[A-Za-z0-9._-]{3,30}$")
_CHANNEL_PATH_RE = re.compile(r"^/channel/(UC[A-Za-z0-9_-]{22})/?$")
_RESOLVABLE_PATH_RE = re.compile(r"^/(?:@[^/]+|c/[^/]+|user/[^/]+)/?(?:videos|streams|shorts|featured)?/?$")

_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)
_FEED_TIMEOUT_SECONDS = 30


class ChannelFeedError(Exception):
    """A feed or resolution failure with a plain, user-safe message."""


@dataclass(frozen=True)
class FeedEntry:
    video_id: str
    title: str
    url: str
    published_at: Optional[datetime]
    is_short_link: bool


def feed_url_for(channel_id: str) -> str:
    return FEED_URL_TEMPLATE.format(channel_id=channel_id)


def _parse_published(raw: str | None) -> Optional[datetime]:
    if not raw:
        return None
    try:
        return datetime.fromisoformat(raw.strip().replace("Z", "+00:00"))
    except ValueError:
        return None


def parse_channel_feed(xml_bytes: bytes) -> tuple[Optional[str], list[FeedEntry]]:
    """Parse one uploads-feed document into (channel title, entries).

    Entries keep the feed's own order (newest first). Malformed XML
    raises :class:`ChannelFeedError` — a feed that cannot be read is
    a health event for the channel, never "no new videos".
    """
    try:
        root = ElementTree.fromstring(xml_bytes)
    except ElementTree.ParseError as exc:
        raise ChannelFeedError(
            "YouTube returned a channel feed that could not be read."
        ) from exc

    title_el = root.find(f"{{{_ATOM}}}title")
    channel_title = title_el.text.strip() if title_el is not None and title_el.text else None

    entries: list[FeedEntry] = []
    for entry_el in root.findall(f"{{{_ATOM}}}entry"):
        video_el = entry_el.find(f"{{{_YT}}}videoId")
        video_id = video_el.text.strip() if video_el is not None and video_el.text else ""
        if not video_id:
            continue
        entry_title_el = entry_el.find(f"{{{_ATOM}}}title")
        title = (
            entry_title_el.text.strip()
            if entry_title_el is not None and entry_title_el.text
            else ""
        )
        link = ""
        for link_el in entry_el.findall(f"{{{_ATOM}}}link"):
            if link_el.get("rel") in (None, "alternate"):
                link = link_el.get("href") or ""
                break
        published_el = entry_el.find(f"{{{_ATOM}}}published")
        entries.append(FeedEntry(
            video_id=video_id,
            title=title,
            url=canonical_youtube_url(link) or f"https://www.youtube.com/watch?v={video_id}",
            published_at=_parse_published(
                published_el.text if published_el is not None else None
            ),
            is_short_link="/shorts/" in link,
        ))
    return channel_title, entries


def _fetch_feed_sync(channel_id: str) -> tuple[Optional[str], list[FeedEntry]]:
    try:
        response = requests.get(
            feed_url_for(channel_id),
            headers={"User-Agent": _USER_AGENT, "Accept": "application/atom+xml"},
            timeout=_FEED_TIMEOUT_SECONDS,
        )
    except requests.RequestException as exc:
        raise ChannelFeedError(
            f"Could not reach the channel's YouTube feed ({exc.__class__.__name__})."
        ) from exc
    if response.status_code != 200:
        raise ChannelFeedError(
            f"YouTube answered the channel feed with HTTP {response.status_code}."
        )
    return parse_channel_feed(response.content)


async def fetch_channel_feed(
    channel_id: str, *, include_shorts_feed: bool = False
) -> list[FeedEntry]:
    """The channel's 15 most recent uploads, newest first.

    ``include_shorts_feed`` is reserved for the Stage-2 long-form /
    Shorts playlist feeds: Stage 1 always reads the uploads feed and
    filters Shorts by link form, so the flag changes nothing yet.
    """
    _title, entries = await run_in_thread(_fetch_feed_sync, channel_id)
    return entries


async def fetch_channel_feed_with_title(
    channel_id: str,
) -> tuple[Optional[str], list[FeedEntry]]:
    """One feed fetch that also returns the channel's own title —
    the validation fetch used when a channel is added."""
    return await run_in_thread(_fetch_feed_sync, channel_id)


def _extract_tab_entries_sync(channel_id: str, tab: str) -> list[FeedEntry]:
    """One flat yt-dlp extraction of a channel tab (``videos`` or
    ``streams``): the newest entries with their ids and titles. The
    tabs are pre-filtered by YouTube — ``videos`` carries no Shorts,
    ``streams`` carries live archives — and flat entries carry no
    publish timestamp, so ``published_at`` stays None."""
    ydl_opts = {
        "quiet": True,
        "no_warnings": True,
        "extract_flat": True,
        "playlistend": 15,
        "skip_download": True,
        "socket_timeout": 30,
        # Same choice as the resolver and the metadata path: a
        # proxied deployment must not fail on its proxy's chain.
        "nocheckcertificate": True,
        "http_headers": {"User-Agent": _USER_AGENT},
    }
    url = f"https://www.youtube.com/channel/{channel_id}/{tab}"
    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=False) or {}
    except Exception as exc:
        raise ChannelFeedError(
            f"yt-dlp could not read the channel's {tab} tab "
            f"({exc.__class__.__name__})."
        ) from exc
    entries: list[FeedEntry] = []
    for item in info.get("entries") or []:
        video_id = str(item.get("id") or "").strip()
        if not video_id:
            continue
        raw_url = item.get("url") or item.get("webpage_url") or ""
        entries.append(FeedEntry(
            video_id=video_id,
            title=str(item.get("title") or ""),
            url=canonical_youtube_url(raw_url)
            or f"https://www.youtube.com/watch?v={video_id}",
            published_at=None,
            is_short_link="/shorts/" in raw_url,
        ))
    return entries


def _extract_channel_entries_sync(channel_id: str) -> list[FeedEntry]:
    """Fallback detection: the channel's newest videos from its
    ``videos`` and ``streams`` tabs, deduplicated, newest-tab order
    preserved. One tab failing is survivable while the other
    answers; both failing is a detection failure."""
    merged: list[FeedEntry] = []
    seen: set[str] = set()
    failures: list[Exception] = []
    for tab in ("videos", "streams"):
        try:
            tab_entries = _extract_tab_entries_sync(channel_id, tab)
        except ChannelFeedError as exc:
            failures.append(exc)
            continue
        for item in tab_entries:
            if item.video_id not in seen:
                seen.add(item.video_id)
                merged.append(item)
    if len(failures) == 2:
        raise failures[0]
    return merged[:30]


async def fetch_channel_entries_via_ytdlp(channel_id: str) -> list[FeedEntry]:
    """The yt-dlp fallback detector: one flat extraction pass over
    the channel's tabs. Heavier than the RSS feed and the most
    likely route to draw rate limiting, so the watcher meters it
    (at most one pass per channel per hour, only while the feed is
    failing or answering empty) — the RSS feed stays primary."""
    return await run_in_thread(_extract_channel_entries_sync, channel_id)


def _channel_id_from_input(source_input: str) -> Optional[str]:
    """The channel ID when the pasted input carries one directly."""
    value = source_input.strip()
    if _CHANNEL_ID_RE.match(value):
        return value
    if "://" in value or value.startswith("youtube.com") or value.startswith("www.youtube.com"):
        try:
            parsed = urlparse(value if "://" in value else f"https://{value}")
        except ValueError:
            return None
        match = _CHANNEL_PATH_RE.match(parsed.path or "")
        if match:
            return match.group(1)
    return None


def _resolvable_url(source_input: str) -> Optional[str]:
    """A channel-page URL to resolve through yt-dlp, or None when
    the input is not a recognisable channel reference at all."""
    value = source_input.strip()
    if _HANDLE_RE.match(value):
        return f"https://www.youtube.com/{value}"
    if "://" not in value:
        value = f"https://{value}"
    try:
        parsed = urlparse(value)
    except ValueError:
        return None
    host = (parsed.hostname or "").lower().removeprefix("www.")
    if host != "youtube.com":
        return None
    if _RESOLVABLE_PATH_RE.match(parsed.path or ""):
        return f"https://www.youtube.com{parsed.path.rstrip('/')}"
    return None


def _extract_channel_sync(url: str) -> tuple[str, Optional[str]]:
    """One flat yt-dlp extraction of a channel page: its channel ID
    and title. The same tool the app already trusts for metadata."""
    ydl_opts = {
        "quiet": True,
        "no_warnings": True,
        "extract_flat": True,
        "playlistend": 1,
        "skip_download": True,
        "socket_timeout": 30,
        # Same choice as youtube_utils' metadata options: a proxied
        # deployment must not fail resolution on its proxy's chain.
        "nocheckcertificate": True,
        "http_headers": {"User-Agent": _USER_AGENT},
    }
    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=False) or {}
    except Exception as exc:
        raise ChannelFeedError(
            "That channel link could not be opened on YouTube. "
            "Check it and try again."
        ) from exc
    channel_id = info.get("channel_id") or info.get("id") or ""
    if not _CHANNEL_ID_RE.match(str(channel_id)):
        raise ChannelFeedError(
            "That link did not resolve to a YouTube channel. "
            "Paste a channel URL or @handle."
        )
    title = info.get("channel") or info.get("uploader") or info.get("title")
    return str(channel_id), (str(title).strip() if title else None)


async def resolve_channel(source_input: str) -> tuple[str, str]:
    """Resolve pasted input to ``(channel_id, channel_title)``.

    Accepted inputs: a ``/channel/UC...`` URL, an ``/@handle``,
    ``/c/name`` or ``/user/name`` URL, a bare ``@handle``, or a bare
    ``UC...`` ID. The resolution is validated by a real feed fetch
    for the resolved ID: anything that does not fetch and parse is
    refused with a plain reason and no row is ever written.
    """
    if not isinstance(source_input, str) or not source_input.strip():
        raise ChannelFeedError("Paste a YouTube channel URL or @handle first.")

    channel_id = _channel_id_from_input(source_input)
    extracted_title: Optional[str] = None
    if channel_id is None:
        url = _resolvable_url(source_input)
        if url is None:
            raise ChannelFeedError(
                "That does not look like a YouTube channel. "
                "Paste a channel URL or @handle."
            )
        channel_id, extracted_title = await run_in_thread(_extract_channel_sync, url)

    feed_title, _entries = await fetch_channel_feed_with_title(channel_id)
    return channel_id, (feed_title or extracted_title or channel_id)
