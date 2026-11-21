"""Live clip captions through Groq's hosted Whisper (free tier) instead of the CPU.

Speech-to-text was the heaviest local load of a live session: every clip
window was transcribed on the laptop's CPU. When ``GROQ_API_KEY`` is set,
the window's audio (16 kHz mono, about 30 s) goes to Groq's
OpenAI-compatible ``/audio/transcriptions`` endpoint with word timestamps,
and the result is written to the same transcript cache the caption builder
reads. Any failure (no key, rate limit, network) returns False and the
caller falls back to the local model, so a clip is never lost.

``LIVE_TRANSCRIPTION_PROVIDER=local`` turns this off; ``LIVE_GROQ_MODEL``
picks the model (default ``whisper-large-v3-turbo``).
"""
from __future__ import annotations

import logging
import os
import subprocess
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

GROQ_URL = "https://api.groq.com/openai/v1/audio/transcriptions"


# Streamer slang and meme words the speech model mishears without a hint
# ("lapiz" came out as "my face"). Whisper uses the prompt as context.
SLANG = ("Lapiz, lapis lazuli, chat, bro, GG, W, L, aura, rizz, gyatt, skibidi, sigma, fanum tax, Ohio, mewing, "
         "ong, fr, no cap, bussin, sus, cap, OMEGALUL, KEKW, pog, poggers, Kai Cenat, IShowSpeed, Lacy, xQc, GTA 6, Lucia")


def slang_prompt() -> str:
    return os.getenv("LIVE_ASR_PROMPT", SLANG)[:800]


def groq_enabled() -> bool:
    if os.getenv("LIVE_TRANSCRIPTION_PROVIDER", "").strip().lower() == "local":
        return False
    return bool(os.getenv("GROQ_API_KEY", "").strip())


def to_whisper_result(payload: Dict[str, Any]) -> Dict[str, Any]:
    """Groq verbose_json (top-level ``words`` + ``segments``) -> the
    openai-whisper result shape the transcript cache already understands."""
    words: List[Dict[str, Any]] = [
        {"word": str(w.get("word", "")).strip(), "start": float(w.get("start", 0.0)),
         "end": float(w.get("end", 0.0)), "probability": 1.0}
        for w in payload.get("words") or [] if str(w.get("word", "")).strip()
    ]
    segments = []
    for seg in payload.get("segments") or []:
        s, e = float(seg.get("start", 0.0)), float(seg.get("end", 0.0))
        segments.append({"start": s, "end": e, "text": str(seg.get("text", "")).strip(),
                         "words": [w for w in words if s - 0.05 <= w["start"] < e]})
    assigned = sum(len(seg["words"]) for seg in segments)
    if not segments or assigned < len(words):
        # No segments, or words outside them: one segment holding every word.
        segments = [{"start": words[0]["start"] if words else 0.0, "end": words[-1]["end"] if words else 0.0,
                     "text": str(payload.get("text", "")).strip(), "words": words}]
    return {"text": str(payload.get("text", "")).strip(), "language": payload.get("language"), "segments": segments}


def groq_transcribe_file(audio: Path, timeout: float = 60.0) -> Optional[Dict[str, Any]]:
    """Whisper-shaped result for an audio file, or None (no key, limit, network)."""
    if not groq_enabled():
        return None
    import httpx

    try:
        with Path(audio).open("rb") as fh:
            response = httpx.post(
                GROQ_URL,
                headers={"Authorization": f"Bearer {os.environ['GROQ_API_KEY'].strip()}"},
                data={"model": os.getenv("LIVE_GROQ_MODEL", "whisper-large-v3-turbo"),
                      "response_format": "verbose_json",
                      "timestamp_granularities[]": ["word", "segment"], "prompt": slang_prompt()},
                files={"file": (Path(audio).name, fh, "audio/mpeg")},
                timeout=timeout,
            )
    except Exception as exc:
        logger.warning("Groq transcription failed (%s)", type(exc).__name__)
        return None
    if response.status_code != 200:
        logger.warning("Groq transcription HTTP %s (%s)", response.status_code, response.text[:160])
        return None
    try:
        return to_whisper_result(response.json())
    except Exception as exc:
        logger.warning("Groq transcription: unreadable answer (%s)", exc)
        return None


def transcribe_with_groq(video: Path, timeout: float = 60.0) -> bool:
    """Transcribe ``video`` with Groq and write its transcript cache. True on success."""
    if not groq_enabled():
        return False
    import httpx

    from ..media.transcription import cache_transcript_data

    with tempfile.TemporaryDirectory(prefix="live_groq_") as tmp:
        audio = Path(tmp) / "audio.mp3"
        cut = subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(video), "-vn", "-ac", "1", "-ar", "16000",
                              "-b:a", "48k", str(audio)], capture_output=True, text=True)
        if cut.returncode != 0 or not audio.is_file():
            logger.warning("Groq transcription: audio extract failed: %s", (cut.stderr or "")[-200:])
            return False
        try:
            with audio.open("rb") as fh:
                response = httpx.post(
                    GROQ_URL,
                    headers={"Authorization": f"Bearer {os.environ['GROQ_API_KEY'].strip()}"},
                    data={"model": os.getenv("LIVE_GROQ_MODEL", "whisper-large-v3-turbo"),
                          "response_format": "verbose_json",
                          "timestamp_granularities[]": ["word", "segment"], "prompt": slang_prompt()},
                    files={"file": ("audio.mp3", fh, "audio/mpeg")},
                    timeout=timeout,
                )
        except Exception as exc:  # network, timeout
            logger.warning("Groq transcription failed (%s); using the local model", type(exc).__name__)
            return False
    if response.status_code != 200:
        # Never log the body verbatim beyond a short prefix: no secrets in it,
        # but keep logs small. 429 = free-tier limit reached.
        logger.warning("Groq transcription HTTP %s (%s); using the local model",
                       response.status_code, response.text[:160])
        return False
    try:
        result = to_whisper_result(response.json())
    except Exception as exc:
        logger.warning("Groq transcription: unreadable answer (%s)", exc)
        return False
    cache_transcript_data(Path(video), result)
    words = sum(len(s["words"]) for s in result["segments"])
    logger.info("Groq transcription: %d words (%s)", words, result.get("language"))
    return True
