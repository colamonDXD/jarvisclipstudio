"""SenseVoice reaction features: additive, inert by default, stubbed model.

The inference boundary (``WindowTagger``) is faked everywhere here — the
suite never downloads weights or loads the real model.
"""
from __future__ import annotations

import wave
from pathlib import Path

import numpy as np
import pytest

from src.live import sensevoice_scan as sv
from src.live.selection import DEFAULT_PARAMS, rank_moments
from src.live.vod import add_reaction_features


def _write_wav(path: Path, seconds: float, sr: int = 8000, tone_hz: float = 440.0,
               amp: float = 0.05, burst: tuple | None = None) -> Path:
    """A quiet tone, with an optional loud high burst (start, end, hz, amp)."""
    n = int(seconds * sr)
    t = np.arange(n) / sr
    x = amp * np.sin(2 * np.pi * tone_hz * t)
    if burst is not None:
        lo, hi, hz, bamp = burst
        seg = slice(int(lo * sr), int(hi * sr))
        x[seg] = bamp * np.sin(2 * np.pi * hz * t[seg])
    pcm = (np.clip(x, -1, 1) * 32767).astype("<i2")
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes(pcm.tobytes())
    return path


def _flat_series(n: int) -> dict:
    """Core features with no reaction anywhere: nothing can be active."""
    return {"vocal": [-60.0] * n, "f0": [120.0] * n, "laugh": [0.0] * n,
            "voiced_frac": [0.5] * n}


# ---------------------------------------------------------------------------
# Tag parsing
# ---------------------------------------------------------------------------

def test_parse_sensevoice_text_maps_events_and_emotions():
    tags = sv.parse_sensevoice_text("<|en|><|HAPPY|><|Laughter|><|woitn|>ha ha")
    assert tags == {"sv_laugh": 1.0, "sv_applause": 0.0, "sv_cheer": 0.0, "sv_emotion": 1.0}
    tags = sv.parse_sensevoice_text("<|en|><|SURPRISED|><|Applause|><|woitn|>wow")
    assert tags["sv_applause"] == 1.0 and tags["sv_cheer"] == 1.0 and tags["sv_emotion"] == 1.0
    # Applause under a neutral voice is not cheering; unknown emotion is not emotion.
    tags = sv.parse_sensevoice_text("<|en|><|NEUTRAL|><|Applause|><|woitn|>clap")
    assert tags["sv_applause"] == 1.0 and tags["sv_cheer"] == 0.0 and tags["sv_emotion"] == 0.0
    tags = sv.parse_sensevoice_text("<|nospeech|><|EMO_UNKNOWN|><|Event_UNK|><|woitn|>")
    assert all(v == 0.0 for v in tags.values())


# ---------------------------------------------------------------------------
# Shout (zero-model DSP)
# ---------------------------------------------------------------------------

def test_shout_series_fires_on_the_loud_high_burst_only(tmp_path):
    audio = _write_wav(tmp_path / "a.wav", 30.0, burst=(20.0, 25.0, 3000.0, 0.9))
    shout = sv.shout_series(audio, 30.0)
    assert len(shout) == 30
    assert max(shout[20:25]) > 0.5
    assert max(shout[0:15]) < max(shout[20:25])


# ---------------------------------------------------------------------------
# SenseVoice series via the stubbed boundary
# ---------------------------------------------------------------------------

class _FakeTagger:
    """Tags laughter on windows starting at >= 10 s, applause nowhere."""

    def tag_window(self, path: Path) -> dict:
        start = int(path.stem[1:])
        return {"sv_laugh": 1.0} if start >= 10 else {}


def test_sensevoice_series_spreads_window_tags_over_their_seconds(tmp_path):
    audio = _write_wav(tmp_path / "a.wav", 20.0, sr=16000)
    series = sv.sensevoice_series(audio, 20.0, tagger=_FakeTagger(), window_sec=5)
    assert set(series) == set(sv.SV_KEYS)
    assert series["sv_laugh"][:10] == [0.0] * 10
    assert series["sv_laugh"][10:] == [1.0] * 10
    assert series["sv_applause"] == [0.0] * 20


def test_reaction_feature_series_degrades_to_shout_only(tmp_path):
    class _Boom:
        def tag_window(self, path: Path) -> dict:
            raise RuntimeError("model exploded")

    audio = _write_wav(tmp_path / "a.wav", 10.0)
    out = sv.reaction_feature_series(audio, 10.0, tagger=_Boom())
    assert set(out) == {"shout"} and len(out["shout"]) == 10


def test_missing_weights_raise_unavailable(tmp_path, monkeypatch):
    monkeypatch.setenv("SENSEVOICE_MODEL_DIR", str(tmp_path / "nowhere"))
    with pytest.raises(sv.SenseVoiceUnavailable):
        sv.FunASRTagger().tag_window(tmp_path / "x.wav")


# ---------------------------------------------------------------------------
# rank_moments: additive + inert at defaults
# ---------------------------------------------------------------------------

def test_rank_moments_byte_identical_with_extra_keys_at_defaults():
    rng = np.random.RandomState(7)
    n = 600
    series = {"vocal": (-40 + rng.rand(n) * 4).tolist(), "f0": (150 + rng.rand(n) * 30).tolist(),
              "laugh": (rng.rand(n) * 0.05).tolist(), "voiced_frac": [0.6] * n}
    for t in (200, 400):
        for d in range(3):
            series["vocal"][t + d], series["f0"][t + d], series["laugh"][t + d] = -8.0, 430.0, 0.9
    base = rank_moments(series, DEFAULT_PARAMS)
    assert base  # the spikes rank, so the comparison below is not vacuous
    with_extra = dict(series)
    with_extra["shout"] = (rng.rand(n) * 0.9).tolist()
    with_extra["sv_laugh"] = [1.0 if 100 <= i < 130 else 0.0 for i in range(n)]
    assert repr(rank_moments(with_extra, DEFAULT_PARAMS)) == repr(base)
    assert repr(rank_moments(with_extra)) == repr(rank_moments(series))


def test_rank_moments_extra_features_arm_only_via_params():
    n = 300
    series = _flat_series(n)
    series["sv_laugh"] = [1.0 if 100 <= i < 110 else 0.0 for i in range(n)]
    assert rank_moments(series, DEFAULT_PARAMS) == []  # inert: no core signal, no moment
    armed = dict(DEFAULT_PARAMS, extra_active=0.5, extra_w=2.0)
    moments = rank_moments(series, armed)
    assert len(moments) == 1
    assert moments[0]["onset"] == 100.0 and "sv_laugh" in moments[0]["signals"]
    assert moments[0]["score"] > 0


# ---------------------------------------------------------------------------
# VOD wiring: switch gate
# ---------------------------------------------------------------------------

def test_add_reaction_features_is_identity_when_switch_off(tmp_path, monkeypatch):
    monkeypatch.delenv("SENSEVOICE_FEATURES", raising=False)
    series = _flat_series(10)
    assert add_reaction_features(series, tmp_path / "a.wav", 10.0) is series


def test_add_reaction_features_merges_when_switch_on(tmp_path, monkeypatch):
    monkeypatch.setenv("SENSEVOICE_FEATURES", "on")
    monkeypatch.setattr("src.live.sensevoice_scan.reaction_feature_series",
                        lambda audio, duration, progress=None: {"shout": [0.5] * 10})
    series = _flat_series(10)
    merged = add_reaction_features(series, tmp_path / "a.wav", 10.0)
    assert merged["shout"] == [0.5] * 10 and merged["vocal"] == series["vocal"]
    assert "shout" not in series  # the caller's series is not mutated
