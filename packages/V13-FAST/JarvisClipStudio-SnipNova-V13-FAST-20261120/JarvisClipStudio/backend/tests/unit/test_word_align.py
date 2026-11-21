"""Word-timing alignment + caption coverage (media.word_align, from wordcaps).

Pure-function tests use hand-built envelopes; the media-level tests use
a generated WAV (tone bursts in silence) so ffmpeg/wave do real work.
"""
from __future__ import annotations

import subprocess
import wave
from pathlib import Path

import numpy as np
import pytest

from src.media import word_align as W

BLOCKS = [(1.0, 2.0), (3.0, 4.2)]


def _word(text, start, end):
    return {"word": text, "text": text, "start": start, "end": end}


def test_word_starting_in_silence_snaps_to_the_speech_onset():
    words = [_word("hello", 0.55, 1.4)]
    refined, log = W.refine_words(words, BLOCKS)
    assert refined[0]["start"] == pytest.approx(1.0)
    assert refined[0]["end"] == pytest.approx(1.4)
    assert log and "onset" in log[0]["note"]
    assert words[0]["start"] == 0.55  # input untouched


def test_word_ending_after_the_voice_stops_snaps_back():
    refined, log = W.refine_words([_word("hello", 1.2, 2.6)], BLOCKS)
    assert refined[0]["end"] == pytest.approx(2.0)
    assert "offset" in log[0]["note"]


def test_word_with_no_speech_nearby_is_kept_but_flagged_suspect():
    refined, log = W.refine_words([_word("ghost", 6.0, 6.4)], BLOCKS)
    assert refined[0]["start"] == pytest.approx(6.0)
    assert log and "suspect" in log[0]["note"]


def test_words_never_overlap_after_refining():
    words = [_word("one", 0.9, 1.5), _word("two", 1.2, 1.9)]
    refined, _ = W.refine_words(words, BLOCKS)
    assert refined[1]["start"] >= refined[0]["end"]


def test_no_blocks_means_no_changes():
    words = [_word("hello", 0.5, 1.0)]
    refined, log = W.refine_words(words, [])
    assert refined == words and log == []


def test_coverage_ok_when_speech_is_captioned():
    cues = [{"start": 0.95, "end": 2.05}, {"start": 2.95, "end": 4.25}]
    report = W.check_caption_coverage(BLOCKS, cues)
    assert report["ok"] and report["uncovered"] == []


def test_coverage_fails_on_a_dropped_phrase():
    report = W.check_caption_coverage(BLOCKS, [{"start": 0.95, "end": 2.05}])
    assert not report["ok"]
    assert report["uncovered_seconds"] == pytest.approx(1.2, abs=0.15)
    assert report["uncovered"][0][0] == pytest.approx(3.0, abs=0.15)


def test_coverage_reports_captions_over_silence():
    cues = [{"start": 0.95, "end": 2.05}, {"start": 3.0, "end": 4.2}, {"start": 6.0, "end": 6.5}]
    report = W.check_caption_coverage(BLOCKS, cues)
    assert report["silent_cues"] == [{"start": 6.0, "end": 6.5}]


@pytest.fixture()
def tone_wav(tmp_path):
    """4 s WAV: silence, a loud second, silence, a loud second."""
    rate = 16000
    t = np.arange(rate * 4) / rate
    samples = np.zeros(rate * 4)
    tone = 0.5 * np.sin(2 * np.pi * 440 * t)
    samples[rate:2 * rate] = tone[rate:2 * rate]
    samples[3 * rate:4 * rate] = tone[3 * rate:4 * rate]
    path = tmp_path / "tone.wav"
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(rate)
        handle.writeframes((samples * 32767).astype(np.int16).tobytes())
    return path


def test_speech_blocks_finds_the_tone_bursts(tone_wav):
    blocks = W.speech_blocks(tone_wav)
    assert len(blocks) == 2
    assert blocks[0][0] == pytest.approx(1.0, abs=0.1)
    assert blocks[0][1] == pytest.approx(2.0, abs=0.1)
    assert blocks[1][0] == pytest.approx(3.0, abs=0.1)


def test_align_words_to_audio_moves_an_early_word_onto_the_voice(tone_wav):
    words = [_word("early", 0.3, 1.3), _word("late", 3.1, 3.9)]
    refined, log = W.align_words_to_audio(words, tone_wav)
    assert refined[0]["start"] == pytest.approx(1.0, abs=0.1)
    assert refined[1]["start"] == pytest.approx(3.1, abs=0.1)
    assert log


def test_align_is_a_noop_when_the_switch_is_off(monkeypatch, tone_wav):
    monkeypatch.setenv("CAPTION_WORD_ALIGN", "off")
    words = [_word("early", 0.3, 1.3)]
    refined, log = W.align_words_to_audio(words, tone_wav)
    assert refined == words and log == []
    assert W.caption_coverage_for(tone_wav, [{"start": 0.3, "end": 1.3}]) is None


def test_coverage_for_a_real_file(tone_wav):
    report = W.caption_coverage_for(tone_wav, [{"start": 0.95, "end": 2.05}])
    assert report is not None and not report["ok"]
