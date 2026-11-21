"""Word-timed transcripts from YouTube's own captions (json3).

Approach credited to NeonClipping (MIT, see NOTICE): when a source is a
YouTube video, YouTube already serves timed captions for it — manual ones
when the creator uploaded them, automatic (ASR) ones for most popular
videos. Fetching those (a few hundred KB, seconds) beats running a speech
model over the audio on every dimension that matters here: the words are
the platform's own, the timings are word-level, and no GPU/CPU minutes
are spent. It is therefore tried BEFORE Groq's hosted Whisper and the
local faster-whisper in the VOD/live transcript selection, with automatic
fallback when captions are absent or blocked.

Two honest limits, both handled by the callers:

* YouTube captions contain EVERY voice on the track, game dialogue
  included — the same leakage a speech model has. Callers keep running
  the transcript through the existing streamer-words filter
  (``live.moments.keep_streamer_words``) before anything is burned in.
* A still-live stream has no finished captions yet, so this path simply
  returns None there and the Groq/local fallback carries on.

Timing honesty: YouTube's ASR word timings are rough (about 0.1-0.3 s
off the true onset). Words here are anchored at their segment's onset
(``tOffsetMs``) and distributed inside a segment in proportion to their
length, so a word's start never sits later than YouTube's own timing
for it — and at render time the finisher runs every transcript,
YouTube's included, through the loudness-envelope alignment in
``media.word_align`` (adapted from wordcaps), which snaps word edges
onto the actual speech blocks the karaoke highlight keys on.

Switch: ``TRANSCRIPT_YT_CAPTIONS`` (default on; ``off``/``0``/``false``/
``no`` disables). No network happens in the parser — only ``fetch`` talks
to yt-dlp, and tests drive it with recorded json3 fixtures.
"""
from __future__ import annotations

import json
import logging
import os
import re
import subprocess
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

logger = logging.getLogger(__name__)

_VIDEO_ID = re.compile(r"(?:v=|youtu\.be/|shorts/|live/)([A-Za-z0-9_-]{11})")

# video id -> parsed whisper-shaped result (a VOD session slices many
# windows out of one fetch; the recorder path never fetches at all).
_CACHE: Dict[str, Optional[Dict[str, Any]]] = {}


def yt_captions_enabled() -> bool:
    return os.getenv("TRANSCRIPT_YT_CAPTIONS", "on").strip().lower() not in (
        "off", "0", "false", "no",
    )


def youtube_video_id(url: Optional[str]) -> Optional[str]:
    if not url:
        return None
    match = _VIDEO_ID.search(str(url))
    return match.group(1) if match else None


def _event_chunks(events: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """json3 events -> [{start, end, text}] chunks, ONE per event.

    An event is one caption phrase: its segments carry ``tOffsetMs``
    when YouTube timed them (always for ASR); a segment without an
    offset (the manual-caption shape: one segment holding the whole
    line) takes the span from the previous segment's end to the next
    segment's start. Newline-only segments are the rolling-caption
    artefacts of the ASR feed and are dropped, as are bracketed sound
    tags (``[music]``) which are not speech.
    """
    chunks: List[Dict[str, Any]] = []
    for event in events or []:
        segs = []
        for seg in event.get("segs") or []:
            text = str(seg.get("utf8", "")).strip()
            if not text or text == "\n":
                continue
            if text.startswith("[") and text.endswith("]"):
                continue  # [music], [applause]: sound tags, not words
            offset = seg.get("tOffsetMs")
            segs.append((float(offset) / 1000.0 if offset is not None else None, text))
        if not segs:
            continue
        ev_start = float(event.get("tStartMs", 0)) / 1000.0
        ev_end = ev_start + float(event.get("dDurationMs", 0)) / 1000.0
        parts: List[Dict[str, Any]] = []
        for index, (offset, text) in enumerate(segs):
            start = ev_start + offset if offset is not None else (
                parts[-1]["end"] if parts else ev_start
            )
            following = segs[index + 1][0] if index + 1 < len(segs) else None
            end = ev_start + following if following is not None else ev_end
            if end <= start:
                end = start + 0.05
            parts.append({"start": start, "end": end, "text": text})
        chunks.append({
            "start": parts[0]["start"],
            "end": parts[-1]["end"],
            "text": " ".join(part["text"] for part in parts),
            "parts": parts,
        })
    chunks.sort(key=lambda chunk: chunk["start"])
    return chunks


def _words_for_chunk(chunk: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Split one chunk's text into words inside its span.

    Word boundaries inside a segment are distributed in proportion to
    word length (the only signal available); the first word starts
    exactly at the chunk's onset-anchored start.
    """
    tokens = [token for token in chunk["text"].split() if token]
    if not tokens:
        return []
    start, end = float(chunk["start"]), float(chunk["end"])
    span = max(0.05, end - start)
    total_chars = sum(len(token) for token in tokens) or 1
    words: List[Dict[str, Any]] = []
    cursor = start
    for index, token in enumerate(tokens):
        share = span * (len(token) / total_chars)
        word_end = end if index == len(tokens) - 1 else cursor + share
        words.append({
            "word": token,
            "start": round(cursor, 3),
            "end": round(max(word_end, cursor + 0.02), 3),
            "probability": 1.0,
        })
        cursor = word_end
    return words


def parse_json3_captions(payload: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """A json3 caption payload -> the openai-whisper result shape.

    Returns None when the payload holds no speech at all (an empty or
    music-only track), so callers fall through to a speech model.
    """
    chunks = _event_chunks(payload.get("events") or [])
    if not chunks:
        return None
    segments: List[Dict[str, Any]] = []
    text_parts: List[str] = []
    for chunk in chunks:
        # Words are distributed inside each segment part's own span, so
        # ASR words keep the onsets YouTube reported for their segment.
        words = [word for part in chunk["parts"] for word in _words_for_chunk(part)]
        if not words:
            continue
        segments.append({
            "start": chunk["start"],
            "end": chunk["end"],
            "text": chunk["text"],
            "words": words,
        })
        text_parts.append(chunk["text"])
    if not segments:
        return None
    return {"text": " ".join(text_parts), "language": "en", "segments": segments}


def slice_result(
    result: Dict[str, Any], start: float, end: float
) -> Optional[Dict[str, Any]]:
    """The part of ``result`` inside [start, end], times made relative.

    For the finisher: a downloaded section spans [start, end] of the
    source, and its transcript cache is written in section-local time.
    A word belongs to the slice when its midpoint is inside the window.
    """
    segments: List[Dict[str, Any]] = []
    text_parts: List[str] = []
    for segment in result.get("segments") or []:
        words = []
        for word in segment.get("words") or []:
            mid = (float(word["start"]) + float(word["end"])) / 2.0
            if start - 0.25 <= mid <= end + 0.25:
                words.append({
                    "word": word["word"],
                    "start": round(max(0.0, float(word["start"]) - start), 3),
                    "end": round(max(0.02, float(word["end"]) - start), 3),
                    "probability": float(word.get("probability", 1.0)),
                })
        if words:
            segments.append({
                "start": words[0]["start"],
                "end": words[-1]["end"],
                "text": " ".join(w["word"] for w in words),
                "words": words,
            })
            text_parts.append(segments[-1]["text"])
    if not segments:
        return None
    return {"text": " ".join(text_parts), "language": result.get("language"), "segments": segments}


def words_in_window(
    result: Dict[str, Any], start: float, end: float
) -> List[Dict[str, Any]]:
    """[{text, start, end}] in ABSOLUTE seconds — the VOD picker's shape."""
    words: List[Dict[str, Any]] = []
    for segment in result.get("segments") or []:
        for word in segment.get("words") or []:
            mid = (float(word["start"]) + float(word["end"])) / 2.0
            if start - 0.25 <= mid <= end + 0.25:
                words.append({
                    "text": str(word["word"]),
                    "start": float(word["start"]),
                    "end": float(word["end"]),
                })
    words.sort(key=lambda w: w["start"])
    return words


def fetch_youtube_captions(url: Optional[str]) -> Optional[Dict[str, Any]]:
    """Fetch + parse a YouTube video's timed captions, or None.

    Manual captions win over automatic ones (yt-dlp's own ordering with
    both flags set), English first. Any failure — no captions, a block,
    a live stream with none yet — returns None so the caller falls back
    to a speech model; a clip is never lost over captions.
    """
    if not yt_captions_enabled():
        return None
    video_id = youtube_video_id(url)
    if video_id is None:
        return None
    if video_id in _CACHE:
        return _CACHE[video_id]
    result: Optional[Dict[str, Any]] = None
    try:
        from ..live import yt_dlp_binary

        with tempfile.TemporaryDirectory(prefix="yt_caps_") as tmp:
            proc = subprocess.run(
                [yt_dlp_binary(), "--no-warnings", "-q", "--skip-download",
                 "--write-subs", "--write-auto-subs",
                 "--sub-langs", "en.*", "--sub-format", "json3",
                 "-o", str(Path(tmp) / "%(id)s.%(ext)s"), str(url)],
                capture_output=True, text=True, timeout=120,
            )
            files = sorted(Path(tmp).glob("*.json3"))
            if proc.returncode == 0 and files:
                payload = json.loads(files[0].read_text(encoding="utf-8"))
                result = parse_json3_captions(payload)
    except Exception as exc:
        logger.warning("YouTube captions fetch failed (%s); using a speech model", type(exc).__name__)
        result = None
    if result is None:
        logger.info("No usable YouTube captions for %s; using a speech model", video_id)
    else:
        count = sum(len(s["words"]) for s in result["segments"])
        logger.info("YouTube captions for %s: %d words, no speech model needed", video_id, count)
    _CACHE[video_id] = result
    return result
