"""YouTube json3 captions as the transcript: parsing, timing, selection.

The fixture is a recorded-shape ASR payload (word segments carrying
``tOffsetMs``, a rolling-caption newline event, a [music] tag, and one
manual-style phrase event without offsets). No network: fetch is tested
with the subprocess boundary mocked.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.media import yt_captions as Y

FIXTURE = Path(__file__).parent.parent / "fixtures" / "yt_captions_asr.json3"


@pytest.fixture()
def payload():
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


@pytest.fixture()
def result(payload):
    return Y.parse_json3_captions(payload)


def test_video_id_from_the_usual_url_shapes():
    assert Y.youtube_video_id("https://www.youtube.com/watch?v=YE7VzlLtp-4") == "YE7VzlLtp-4"
    assert Y.youtube_video_id("https://youtu.be/YE7VzlLtp-4") == "YE7VzlLtp-4"
    assert Y.youtube_video_id("https://www.youtube.com/shorts/YE7VzlLtp-4") == "YE7VzlLtp-4"
    assert Y.youtube_video_id("https://example.com/watch?v=nope") is None
    assert Y.youtube_video_id(None) is None


def test_asr_words_keep_their_own_offsets(result):
    first = result["segments"][0]
    assert [w["word"] for w in first["words"]] == ["oh", "my", "god", "chat", "he", "is", "coming", "right", "at", "me"]
    # Anchored at the segment onsets YouTube reported (event start 2.4 s).
    assert first["words"][0]["start"] == pytest.approx(2.4)
    assert first["words"][2]["start"] == pytest.approx(2.4 + 0.48)
    assert first["words"][-1]["start"] == pytest.approx(2.4 + 2.64)


def test_sound_tags_and_rolling_newlines_are_not_speech(result):
    text = result["text"]
    assert "[music]" not in text
    assert "\n" not in text
    assert text.startswith("oh my god chat")


def test_manual_phrase_event_gets_words_inside_its_span(result):
    segment = result["segments"][2]
    assert segment["text"] == "Do whatever it is you have to do, dude."
    words = segment["words"]
    assert words[0]["start"] == pytest.approx(9.0)
    assert words[-1]["end"] == pytest.approx(12.0)
    starts = [w["start"] for w in words]
    assert starts == sorted(starts)


def test_word_starts_never_leave_their_event(result):
    for segment in result["segments"]:
        for word in segment["words"]:
            assert segment["start"] - 0.001 <= word["start"] < word["end"] <= segment["end"] + 0.001


def test_empty_payload_means_no_captions():
    assert Y.parse_json3_captions({"events": []}) is None
    assert Y.parse_json3_captions({"events": [{"tStartMs": 0, "dDurationMs": 10, "segs": [{"utf8": "[music]"}]}]}) is None
    assert Y.parse_json3_captions({}) is None


def test_slice_result_shifts_times_and_keeps_boundary_words(result):
    # The window starts after the previous phrase ends (5.28 s); the
    # ±0.25 s midpoint tolerance in slice_result is deliberate (it
    # protects a clip's first/last spoken words) and this window start
    # sits outside it.
    sliced = Y.slice_result(result, 5.5, 13.0)
    texts = [w["word"] for s in sliced["segments"] for w in s["words"]]
    assert texts[:4] == ["run", "run", "get", "in"]
    # "run" starts at 5.4 s absolute, 0.1 s before the window: kept by
    # the midpoint rule, its start clamped to the window's zero.
    assert sliced["segments"][0]["words"][0]["start"] == pytest.approx(0.0)
    assert "dude." in texts  # the phrase event at 9-12 s is inside
    assert Y.slice_result(result, 20.0, 30.0) is None


def test_words_in_window_uses_absolute_seconds(result):
    words = Y.words_in_window(result, 5.5, 8.5)
    assert [w["text"] for w in words] == ["run", "run", "get", "in", "the", "car", "get", "in", "the", "car"]
    assert words[0]["start"] == pytest.approx(5.4)


def test_switch_off_disables_fetch(monkeypatch):
    monkeypatch.setenv("TRANSCRIPT_YT_CAPTIONS", "off")
    assert Y.yt_captions_enabled() is False
    assert Y.fetch_youtube_captions("https://www.youtube.com/watch?v=YE7VzlLtp-4") is None


def test_fetch_parses_the_downloaded_json3(monkeypatch, tmp_path, payload):
    class _Proc:
        returncode = 0

    def fake_run(cmd, **kwargs):
        out = Path(cmd[cmd.index("-o") + 1].replace("%(id)s", "YE7VzlLtp-4").replace("%(ext)s", "en.json3"))
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(payload), encoding="utf-8")
        return _Proc()

    monkeypatch.setattr(Y.subprocess, "run", fake_run)
    monkeypatch.setattr("src.live.yt_dlp_binary", lambda: "yt-dlp")
    Y._CACHE.clear()
    result = Y.fetch_youtube_captions("https://www.youtube.com/watch?v=YE7VzlLtp-4")
    assert result is not None
    assert sum(len(s["words"]) for s in result["segments"]) == 35
    # Second call is served from the session cache (no new download).
    monkeypatch.setattr(Y.subprocess, "run", lambda *a, **k: (_ for _ in ()).throw(AssertionError("refetch")))
    assert Y.fetch_youtube_captions("https://www.youtube.com/watch?v=YE7VzlLtp-4") is result


def test_fetch_failure_falls_back_to_none(monkeypatch):
    class _Proc:
        returncode = 1

    monkeypatch.setattr(Y.subprocess, "run", lambda *a, **k: _Proc())
    monkeypatch.setattr("src.live.yt_dlp_binary", lambda: "yt-dlp")
    Y._CACHE.clear()
    assert Y.fetch_youtube_captions("https://www.youtube.com/watch?v=YE7VzlLtp-4") is None
