"""Live captions via Groq's hosted Whisper: conversion, cache write, fallbacks."""
from __future__ import annotations

import json
import subprocess

import httpx
import pytest

from src.live import groq_transcribe as G
from src.media.transcription import load_cached_transcript_data

PAYLOAD = {
    "text": "bro what is this",
    "language": "English",
    "segments": [{"start": 0.0, "end": 1.6, "text": "bro what"}, {"start": 1.6, "end": 3.0, "text": "is this"}],
    "words": [{"word": "bro", "start": 0.1, "end": 0.4}, {"word": "what", "start": 0.6, "end": 1.0},
              {"word": "is", "start": 1.8, "end": 2.0}, {"word": "this", "start": 2.1, "end": 2.6}],
}


def test_groq_answer_becomes_the_whisper_shape():
    result = G.to_whisper_result(PAYLOAD)
    assert [[w["word"] for w in s["words"]] for s in result["segments"]] == [["bro", "what"], ["is", "this"]]
    assert result["segments"][0]["words"][0] == {"word": "bro", "start": 0.1, "end": 0.4, "probability": 1.0}


def test_words_without_segments_are_kept_in_one_segment():
    result = G.to_whisper_result({"text": "hey", "words": [{"word": "hey", "start": 0.2, "end": 0.5}]})
    assert len(result["segments"]) == 1 and result["segments"][0]["words"][0]["word"] == "hey"


@pytest.fixture()
def window(tmp_path):
    video = tmp_path / "window_001.mp4"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "sine=frequency=440:duration=3",
                    "-f", "lavfi", "-i", "color=c=black:s=320x180:d=3", "-shortest", "-c:v", "libx264",
                    "-c:a", "aac", str(video)], check=True)
    return video


class _Response:
    def __init__(self, status, body):
        self.status_code, self._body = status, body
        self.text = json.dumps(body)

    def json(self):
        return self._body


def test_success_writes_the_cache_the_captions_read(window, monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "test-key")
    seen = {}

    def fake_post(url, headers, data, files, timeout):
        seen.update(url=url, auth=headers["Authorization"], model=data["model"], name=files["file"][0])
        return _Response(200, PAYLOAD)

    monkeypatch.setattr(httpx, "post", fake_post)
    assert G.transcribe_with_groq(window) is True
    assert seen == {"url": G.GROQ_URL, "auth": "Bearer test-key", "model": "whisper-large-v3-turbo", "name": "audio.mp3"}
    cached = load_cached_transcript_data(window)
    assert [w["text"] for w in cached["words"]] == ["bro", "what", "is", "this"]
    assert cached["words"][0]["start"] == 100 and cached["words"][0]["end"] == 400  # ms, like every provider


def test_rate_limit_falls_back_to_local(window, monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "test-key")
    monkeypatch.setattr(httpx, "post", lambda *a, **k: _Response(429, {"error": "rate limit"}))
    assert G.transcribe_with_groq(window) is False
    assert load_cached_transcript_data(window) is None


def test_off_without_a_key_or_when_forced_local(monkeypatch):
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    assert G.groq_enabled() is False
    monkeypatch.setenv("GROQ_API_KEY", "k")
    assert G.groq_enabled() is True
    monkeypatch.setenv("LIVE_TRANSCRIPTION_PROVIDER", "local")
    assert G.groq_enabled() is False
