"""Face-on-top (plain-path) clips get the EDL's music/SFX in an audio-only pass."""
from __future__ import annotations

import hashlib
import subprocess

import pytest

from src.media import audio_finish as AF
import src.media.edl_render as ER


@pytest.fixture()
def clip(tmp_path):
    path = tmp_path / "clip.mp4"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "sine=frequency=300:duration=4",
                    "-f", "lavfi", "-i", "color=c=black:s=160x284:d=4", "-shortest", "-c:v", "libx264",
                    "-c:a", "aac", str(path)], check=True)
    return path


def _digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_no_music_plan_leaves_the_clip_untouched(clip, monkeypatch):
    before = _digest(clip)
    monkeypatch.setattr(ER, "build_edl_audio_graph", lambda *a, **k: None)
    assert AF.finish_plain_path_audio(clip, {"segments": [{}]}) is False
    assert _digest(clip) == before


def test_music_plan_is_mixed_and_video_copied(clip, monkeypatch, tmp_path):
    tone = tmp_path / "music.wav"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "sine=frequency=900:duration=4", str(tone)], check=True)
    seen = {}

    def plan(edl, first_input_index, assets_dir=None, audio_source_label="[0:a]"):
        seen.update(index=first_input_index, label=audio_source_label)
        return (["-i", str(tone)], f"{audio_source_label}[{first_input_index}:a]amix=inputs=2[aout]", "[aout]")

    monkeypatch.setattr(ER, "build_edl_audio_graph", plan)
    before = _digest(clip)
    assert AF.finish_plain_path_audio(clip, {"segments": [{}], "audio": {"music_mood": "energetic"}}) is True
    assert seen == {"index": 1, "label": "[0:a]"}
    assert _digest(clip) != before and clip.stat().st_size > 0
    codecs = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "stream=codec_type", "-of", "csv=p=0", str(clip)],
                            capture_output=True, text=True).stdout.split()
    assert codecs.count("video") == 1 and codecs.count("audio") == 1


def test_switch_off(clip, monkeypatch):
    monkeypatch.setenv("PLAIN_PATH_MUSIC", "off")
    assert AF.finish_plain_path_audio(clip, {"segments": [{}]}) is False


def test_a_failed_mix_keeps_the_clip(clip, monkeypatch):
    before = _digest(clip)
    monkeypatch.setattr(ER, "build_edl_audio_graph", lambda *a, **k: (["-i", "missing.wav"], "[0:a][1:a]amix[aout]", "[aout]"))
    assert AF.finish_plain_path_audio(clip, {"segments": [{}]}) is False
    assert _digest(clip) == before
