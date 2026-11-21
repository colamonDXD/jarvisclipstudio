"""Reaction fusion fixes found with Sanji's streamer fixture v2 (2026-11-17).

* Split stereo (mic on one channel, game on the other): the mic is read alone.
* A scream counts: pitch far above the streamer's own with a raised voice.
* On a known mic channel, the streamer's own loudest seconds are bursts.
* A laugh pulse needs the voice raised in that second.
* Unreacted game events rank by coincidence (event AND reaction), so the
  loudest explosion with a still, silent streamer is not a top pick.
* REACTION_FUSION=0 restores the previous behaviour.
"""

import shutil
import subprocess

import numpy as np
import pytest

from src.auto_director import reaction_moments as rm
from src.auto_director.gameplay_moments import Episode

needs_ffmpeg = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not installed")


@pytest.fixture(autouse=True)
def _defaults(monkeypatch):
    for key in ("REACTION_FUSION", "REACTION_MIC_CHANNEL", "REACTION_FACE_ACTIVITY"):
        monkeypatch.delenv(key, raising=False)


def _stereo(path, left, right, seconds=8):
    subprocess.run(
        ["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", f"{left}:duration={seconds}",
         "-f", "lavfi", "-i", f"{right}:duration={seconds}",
         "-filter_complex", "[0:a][1:a]join=inputs=2:channel_layout=stereo[a]",
         "-map", "[a]", "-ar", "16000", str(path)],
        check=True, capture_output=True,
    )


@needs_ffmpeg
def test_split_stereo_reads_the_voiced_channel(tmp_path):
    split = tmp_path / "split.wav"
    _stereo(split, "anoisesrc=color=pink:amplitude=0.3", "sine=frequency=440:sample_rate=16000")
    assert rm.pick_voice_channel(split, 8.0) == 1
    same = tmp_path / "same.wav"
    _stereo(same, "sine=frequency=440:sample_rate=16000", "sine=frequency=440:sample_rate=16000")
    assert rm.pick_voice_channel(same, 8.0) is None


@needs_ffmpeg
def test_mic_channel_switch_off(tmp_path, monkeypatch):
    split = tmp_path / "split.wav"
    _stereo(split, "anoisesrc=color=pink:amplitude=0.3", "sine=frequency=440:sample_rate=16000")
    monkeypatch.setenv("REACTION_MIC_CHANNEL", "0")
    assert rm.pick_voice_channel(split, 8.0) is None


def _voice(seconds=60):
    rng = np.random.default_rng(7)
    return {
        "vocal": 30 + rng.normal(0, 0.5, seconds),
        "f0": 140 + rng.normal(0, 3, seconds),
        "voiced_frac": np.full(seconds, 0.6),
        "laugh": np.abs(rng.normal(0.1, 0.02, seconds)),
        "total_db": np.full(seconds, 40.0),
    }


def test_scream_rule_needs_high_pitch_and_a_raised_voice(monkeypatch):
    v = _voice()
    v["f0"][30:32] += 140          # pitch far above the streamer's own
    v["vocal"][30:32] += 4.5       # voice raised, but below the shout bar
    strength, zs, _ = rm.reaction_series(v, None, None)
    assert zs["pitch"][30] >= rm._SCREAM_PITCH_Z and zs["vocal"][30] < rm._ACTIVE_Z
    assert strength[30] > 0
    monkeypatch.setenv("REACTION_FUSION", "0")
    strength_off, _, _ = rm.reaction_series(v, None, None)
    assert strength_off[30] == 0


def test_mic_burst_makes_a_vocal_burst_episode():
    v = _voice(150)
    rng = np.random.default_rng(8)
    v["mic_loud"] = 40 + rng.normal(0, 2.2, 150)    # talk that swings a lot
    v["mic_loud"][107:109] = 46.5                   # the loudest seconds of the file
    v["vocal"][107:109] += 4.5
    strength, zs, _ = rm.reaction_series(v, None, None)
    assert strength[107] > 0 and strength[108] > 0
    episodes = rm._episodes(strength, zs)
    assert any(e["start"] <= 107 <= e["end"] and e["kind"] == "vocal_burst" for e in episodes)


def test_laugh_needs_a_raised_voice():
    v = _voice()
    v["laugh"][20] = 1.5           # a pulse at a speech -> silence edge
    _, zs, _ = rm.reaction_series(v, None, None)
    assert zs["laugh"][20] <= 0.0


def _episode(start, end, strength):
    return Episode(start=start, end=end, peak=start + 1, strength=strength,
                   features={}, event_type="action")


def test_coincidence_ranks_a_reacted_event_above_a_louder_ignored_one():
    zs = {"vocal": np.zeros(60), "face": np.zeros(60)}
    zs["face"][12:15] = 3.0        # the streamer reacts to the small event
    loud, small = _episode(40, 46, 200.0), _episode(10, 13, 50.0)
    items = [
        {"type": "silent_action", "start": 40, "end": 46, "event": loud, "strength": 50.0, "mult": 0.45},
        {"type": "silent_action", "start": 10, "end": 13, "event": small, "strength": 12.5, "mult": 0.45},
    ]
    rm.rank_unreacted_events(items, zs, [loud, small])
    ignored, reacted = items
    assert ignored["coincidence"] == 0.0
    assert reacted["coincidence"] > 0.25
    assert rm._score(reacted) > rm._score(ignored)
