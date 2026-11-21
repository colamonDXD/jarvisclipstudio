"""Live clipping: the hook-first window and the streamer-only caption filter."""
from __future__ import annotations

from src.live.moments import drop_game_words, speech_window


def test_window_opens_on_the_lead_in_and_ends_at_the_first_silence():
    # silence 0-9, talk 10-17, silence 18-20, reaction at 20, talk 21-32, silence after
    voiced = [0.0] * 10 + [0.6] * 8 + [0.0] * 3 + [0.7] * 12 + [0.0] * 10
    start, stop, share = speech_window(voiced, 20.0, 22.0)
    assert start == 18.0  # 2 s lead-in: never the old 20 s pre-roll
    assert stop == 33.0  # ends where he stops talking
    assert share >= 0.8


def test_window_walks_back_over_speech_at_most_lead_max():
    voiced = [0.8] * 100
    start, stop, _ = speech_window(voiced, 40.0, 41.0, lead_max=6.0, max_len=30.0)
    assert start == 34.0
    assert stop - start == 30.0  # capped


def test_window_keeps_the_minimum_length_and_reports_silence():
    start, stop, share = speech_window([0.0] * 60, 20.0, 21.0)
    assert stop - start == 12.0
    assert share == 0.0  # the caller rejects it: "streamer barely talking"


def test_window_never_runs_past_the_recorded_seconds():
    voiced = [0.9] * 25
    start, stop, _ = speech_window(voiced, 20.0, 21.0)
    assert stop <= 25.0 and start < 20.0


def test_game_subtitle_seconds_drop_their_words():
    words = [
        {"text": "bro", "start": 500, "end": 900},         # second 0: streamer
        {"text": "Lucia", "start": 1200, "end": 1600},     # second 1: game subtitle
        {"text": "crazy", "start": 2900, "end": 3300},     # spans 2-3: subtitle at 3
        {"text": "what", "start": 4100, "end": 4400},      # second 4: streamer
    ]
    kept = drop_game_words(words, [False, True, False, True, False])
    assert [w["text"] for w in kept] == ["bro", "what"]


def test_yt_dlp_is_found_next_to_python_when_not_on_path(monkeypatch, tmp_path):
    import shutil
    import sys

    from src.live import yt_dlp_binary

    exe = tmp_path / "yt-dlp"
    exe.write_text("")
    monkeypatch.setattr(shutil, "which", lambda name: None)
    monkeypatch.setattr(sys, "executable", str(tmp_path / "python"))
    assert yt_dlp_binary() == str(exe)
