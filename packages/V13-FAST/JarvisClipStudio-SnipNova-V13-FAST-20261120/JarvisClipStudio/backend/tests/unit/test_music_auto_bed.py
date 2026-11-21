"""Music = Auto bed (Quality Director item 2).

The user's 2026-11-08 quality review: a podcast rendered with NO music
because Music was on Auto and the podcast_news profile carries no bed
(mood "none", volume 0). His ruling: Auto must pick a subtle library
bed, mixed low with strong ducking under the speech. Precedence stays:
explicit user pick > profile bed > Auto bed. music_artist is exempt —
its source IS music.

The render tests run real FFmpeg (Lane-4 discipline): a synthetic
speech clip with silent gaps, rendered through the actual pipeline EDL
builder + EDL renderer, must show the bed audible in the gaps and
buried under the speech; the unstamped control must stay silent.
"""

import shutil
import subprocess
from pathlib import Path

import numpy as np
import pytest

from src.media import edl_render, music_library
from src.media.gap_activity import _window_levels_db
from src.media.music_library import (
    AUTO_BED_DUCKING_DB,
    AUTO_BED_MOOD,
    AUTO_BED_VOLUME,
    apply_auto_music_bed,
    music_expectation,
    profile_audio_is_silent,
)
from src.services.video_service import VideoService


def _library(tmp_path: Path, monkeypatch, with_calm: bool = True) -> Path:
    assets = tmp_path / "assets"
    (assets / "music").mkdir(parents=True)
    (assets / "sfx").mkdir(parents=True)
    if with_calm:
        (assets / "music" / "calm.wav").touch()
    user_dir = tmp_path / "user_music"
    user_dir.mkdir()
    monkeypatch.setattr(edl_render, "ASSETS_DIR", assets)
    monkeypatch.setattr(music_library, "user_music_dir", lambda: user_dir)
    return assets


def _silent_edl():
    return {
        "duration": 12,
        "segments": [{"start": 0, "end": 12, "transition_in": "none", "transition_duration": 0}],
        "audio": {"music_mood": "none", "music_volume": 0.0, "ducking_db": 0.0},
    }


# --------------------------------------------------------------------------
# Unit: the stamp and the expectation mirror
# --------------------------------------------------------------------------


def test_stamp_applies_to_silent_profile_when_bed_available(tmp_path, monkeypatch):
    _library(tmp_path, monkeypatch)
    edl = _silent_edl()
    assert apply_auto_music_bed(edl, "podcast_news") is True
    audio = edl["audio"]
    assert audio["music_mood"] == AUTO_BED_MOOD
    assert audio["music_volume"] == AUTO_BED_VOLUME
    assert audio["music_volume_0_to_1"] == AUTO_BED_VOLUME
    assert audio["ducking_db"] == AUTO_BED_DUCKING_DB
    assert audio["music_source"] == "auto_bed"


def test_stamp_skipped_when_library_is_empty(tmp_path, monkeypatch):
    _library(tmp_path, monkeypatch, with_calm=False)
    edl = _silent_edl()
    assert apply_auto_music_bed(edl, "podcast_news") is False
    assert edl["audio"]["music_mood"] == "none"
    assert "music_source" not in edl["audio"]


def test_stamp_falls_back_to_another_mood_when_no_calm(tmp_path, monkeypatch):
    # Self-run audit issue #3: the old stamp went silent whenever the
    # "calm" stem was missing. With an upbeat stem in the library the
    # bed is stamped from it, and the fallback is recorded on the EDL.
    assets = _library(tmp_path, monkeypatch, with_calm=False)
    (assets / "music" / "upbeat.wav").touch()
    edl = _silent_edl()
    assert apply_auto_music_bed(edl, "podcast_news") is True
    audio = edl["audio"]
    assert audio["music_mood"] == "upbeat"
    assert audio["music_volume"] == AUTO_BED_VOLUME
    assert audio["ducking_db"] == AUTO_BED_DUCKING_DB
    assert audio["music_source"] == "auto_bed"
    assert audio["auto_bed_fallback"] == "upbeat"


def test_stamp_falls_back_to_any_track_when_no_mood_stem(tmp_path, monkeypatch):
    # No mood stem at all: Auto still owes the clip a bed while the
    # library is non-empty, so the first track alphabetically plays
    # by filename.
    assets = _library(tmp_path, monkeypatch, with_calm=False)
    (assets / "music" / "zebra-song.wav").touch()
    edl = _silent_edl()
    assert apply_auto_music_bed(edl, "podcast_news") is True
    audio = edl["audio"]
    assert audio["music_track"] == "zebra-song.wav"
    assert audio["music_source"] == "auto_bed"
    assert audio["auto_bed_fallback"] == "zebra-song.wav"


def test_select_auto_bed_preference_order(tmp_path, monkeypatch):
    from src.media.music_library import select_auto_bed

    assets = _library(tmp_path, monkeypatch, with_calm=False)
    assert select_auto_bed() is None  # empty library: nothing to play
    (assets / "music" / "zebra-song.wav").touch()
    assert select_auto_bed() == ("track", "zebra-song.wav", True)
    (assets / "music" / "upbeat.wav").touch()
    assert select_auto_bed() == ("mood", "upbeat", True)
    (assets / "music" / "calm.wav").touch()
    assert select_auto_bed() == ("mood", "calm", False)


def test_stamp_never_overrides_an_explicit_pick(tmp_path, monkeypatch):
    _library(tmp_path, monkeypatch)
    edl = _silent_edl()
    edl["audio"]["music_track"] = "my-song.mp3"
    assert apply_auto_music_bed(edl, "podcast_news") is False
    assert edl["audio"]["music_mood"] == "none"


def test_stamp_leaves_profiles_with_music_untouched(tmp_path, monkeypatch):
    _library(tmp_path, monkeypatch)
    edl = _silent_edl()
    edl["audio"] = {"music_mood": "calm", "music_volume": 0.25, "ducking_db": -12.0}
    assert apply_auto_music_bed(edl, "travel_vlog") is False
    assert edl["audio"]["music_volume"] == 0.25
    assert "music_source" not in edl["audio"]


def test_stamp_exempts_music_artist(tmp_path, monkeypatch):
    _library(tmp_path, monkeypatch)
    edl = _silent_edl()
    assert apply_auto_music_bed(edl, "music_artist") is False
    assert edl["audio"]["music_mood"] == "none"


def test_profile_audio_is_silent_rules():
    assert profile_audio_is_silent({"music_mood": "none", "music_volume_0_to_1": 0.2})
    assert profile_audio_is_silent({"music_mood": "calm", "music_volume_0_to_1": 0.0})
    assert not profile_audio_is_silent(
        {"music_mood": "calm", "music_volume_0_to_1": 0.1}
    )
    assert profile_audio_is_silent(None)
    assert profile_audio_is_silent({})


def test_music_expectation_precedence(tmp_path, monkeypatch):
    _library(tmp_path, monkeypatch)
    silent_profile = {"audio": {"music_mood": "none", "music_volume_0_to_1": 0.0}}
    music_profile = {"audio": {"music_mood": "upbeat", "music_volume_0_to_1": 0.2}}

    assert music_expectation(silent_profile, "pick.mp3", "podcast_news") == (
        True, "pick.mp3", "override",
    )
    assert music_expectation(music_profile, None, "vlog_lifestyle") == (
        True, "upbeat (profile mood track)", "profile",
    )
    assert music_expectation(silent_profile, None, "podcast_news") == (
        True, "calm (auto bed)", "auto_bed",
    )
    assert music_expectation(silent_profile, None, "music_artist") == (
        False, None, "none",
    )


def test_music_expectation_without_library_is_none(tmp_path, monkeypatch):
    _library(tmp_path, monkeypatch, with_calm=False)
    silent_profile = {"audio": {"music_mood": "none", "music_volume_0_to_1": 0.0}}
    assert music_expectation(silent_profile, None, "podcast_news") == (
        False, None, "none",
    )


# --------------------------------------------------------------------------
# Pipeline wiring: build_pipeline_edl on Auto
# --------------------------------------------------------------------------


def _segment(start="00:00", end="00:12"):
    return {"start_time": start, "end_time": end, "text": "a podcast segment"}


def _words():
    return [
        {"text": "hello", "start": 0.0, "end": 0.4},
        {"text": "there", "start": 0.5, "end": 0.9},
    ]


def test_pipeline_edl_auto_stamps_bed_on_podcast(tmp_path, monkeypatch):
    _library(tmp_path, monkeypatch)
    edl = VideoService.build_pipeline_edl(
        _segment(), _words(), niche_override="podcast_news", duration=12.0
    )
    assert edl is not None
    assert edl["audio"]["music_mood"] == "calm"
    assert edl["audio"]["music_volume"] == AUTO_BED_VOLUME
    assert edl["audio"]["ducking_db"] == AUTO_BED_DUCKING_DB
    assert edl["audio"]["music_source"] == "auto_bed"


def test_pipeline_edl_explicit_pick_wins_over_auto_bed(tmp_path, monkeypatch):
    assets = _library(tmp_path, monkeypatch)
    (assets / "music" / "chosen.wav").touch()
    edl = VideoService.build_pipeline_edl(
        _segment(), _words(), niche_override="podcast_news", duration=12.0,
        music_override="chosen.wav",
    )
    assert edl is not None
    assert edl["audio"]["music_track"] == "chosen.wav"
    assert "music_source" not in edl["audio"]


def test_pipeline_edl_without_library_stays_silent(tmp_path, monkeypatch):
    _library(tmp_path, monkeypatch, with_calm=False)
    edl = VideoService.build_pipeline_edl(
        _segment(), _words(), niche_override="podcast_news", duration=12.0
    )
    assert edl is not None
    assert edl["audio"]["music_mood"] == "none"
    assert "music_source" not in edl["audio"]


# --------------------------------------------------------------------------
# Exported-render proof (real FFmpeg)
# --------------------------------------------------------------------------

ffmpeg_missing = shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None
pytestmark_real = pytest.mark.skipif(ffmpeg_missing, reason="ffmpeg/ffprobe not installed")


def _run(command):
    result = subprocess.run(command, capture_output=True, text=True, timeout=600)
    assert result.returncode == 0, result.stderr
    return result


def _make_speech_source(path: Path) -> None:
    """12 s: speech bursts [0,2) [4,6) [8,10), digital-silence gaps between."""
    _run([
        "ffmpeg", "-y", "-v", "error",
        "-f", "lavfi", "-i", "testsrc2=size=320x240:rate=30:duration=12",
        "-f", "lavfi", "-i",
        "aevalsrc=0.5*sin(2*PI*700*t)*lt(mod(t\\,4)\\,2):s=48000:d=12",
        "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest",
        str(path),
    ])


def _make_bed(path: Path) -> None:
    # aevalsrc, not the sine source: sine generates at ~1/8 amplitude by
    # design, which would make the "library track" unrealistically quiet
    # (a real track sits near -11 dB mean; this one does too).
    _run([
        "ffmpeg", "-y", "-v", "error",
        "-f", "lavfi", "-i", "aevalsrc=0.4*sin(2*PI*180*t):s=48000:d=30",
        "-c:a", "pcm_s16le", str(path),
    ])


def _window_rms_db(path: Path, start: float, end: float) -> float:
    levels = _window_levels_db(path, 0, start, end)
    assert len(levels) > 0
    return float(10.0 * np.log10(np.mean(np.power(10.0, levels / 10.0))))


@pytestmark_real
def test_rendered_auto_bed_is_audible_in_gaps_and_ducked_under_speech(
    tmp_path, monkeypatch
):
    assets = _library(tmp_path, monkeypatch)
    _make_bed(assets / "music" / "calm.wav")
    source = tmp_path / "source.mp4"
    _make_speech_source(source)

    edl = VideoService.build_pipeline_edl(
        _segment(), _words(), niche_override="podcast_news", duration=12.0
    )
    assert edl is not None and edl["audio"].get("music_source") == "auto_bed"

    output = tmp_path / "out.mp4"
    ok = edl_render.render_edl_clip_ffmpeg(source, output, edl, assets_dir=assets)
    assert ok is True

    speech_rms = _window_rms_db(output, 0.2, 1.8)
    gap_rms = _window_rms_db(output, 2.2, 3.8)
    # The bed is present in the gaps (digital silence would be <= -90 dB
    # after loudnorm; the control below pins that) ...
    assert gap_rms > -50.0, f"gap RMS {gap_rms:.1f} dB — bed missing"
    # ... and buried under the speech: ducking + the low stamp volume keep
    # it well below the voice while someone is talking.
    assert speech_rms - gap_rms >= 6.0, (
        f"bed not ducked: speech {speech_rms:.1f} dB vs gap {gap_rms:.1f} dB"
    )


@pytestmark_real
def test_rendered_auto_bed_falls_back_when_no_calm_stem(
    tmp_path, monkeypatch
):
    # Self-run audit issue #3, render leg: the library holds an upbeat
    # bed but no "calm" stem. Auto must still carry a measurable bed —
    # the same gap/speech shape as the calm render, stamped upbeat.
    assets = _library(tmp_path, monkeypatch, with_calm=False)
    _make_bed(assets / "music" / "upbeat.wav")
    source = tmp_path / "source.mp4"
    _make_speech_source(source)

    edl = VideoService.build_pipeline_edl(
        _segment(), _words(), niche_override="podcast_news", duration=12.0
    )
    assert edl is not None
    assert edl["audio"]["music_mood"] == "upbeat"
    assert edl["audio"]["music_source"] == "auto_bed"
    assert edl["audio"]["auto_bed_fallback"] == "upbeat"

    output = tmp_path / "out.mp4"
    ok = edl_render.render_edl_clip_ffmpeg(source, output, edl, assets_dir=assets)
    assert ok is True

    speech_rms = _window_rms_db(output, 0.2, 1.8)
    gap_rms = _window_rms_db(output, 2.2, 3.8)
    assert gap_rms > -50.0, f"gap RMS {gap_rms:.1f} dB — fallback bed missing"
    assert speech_rms - gap_rms >= 6.0, (
        f"bed not ducked: speech {speech_rms:.1f} dB vs gap {gap_rms:.1f} dB"
    )


@pytestmark_real
def test_rendered_control_without_stamp_keeps_gaps_silent(tmp_path, monkeypatch):
    assets = _library(tmp_path, monkeypatch)
    _make_bed(assets / "music" / "calm.wav")
    source = tmp_path / "source.mp4"
    _make_speech_source(source)

    # The pre-fix EDL: podcast profile audio verbatim, no stamp.
    edl = _silent_edl()
    edl["segments"] = [
        {"start": 0, "end": 4, "transition_in": "none", "transition_duration": 0},
        {"start": 4, "end": 8, "transition_in": "cut", "transition_duration": 0.05},
        {"start": 8, "end": 12, "transition_in": "cut", "transition_duration": 0.05},
    ]
    output = tmp_path / "out.mp4"
    ok = edl_render.render_edl_clip_ffmpeg(source, output, edl, assets_dir=assets)
    assert ok is True

    gap_rms = _window_rms_db(output, 2.2, 3.8)
    assert gap_rms <= -55.0, f"control gap RMS {gap_rms:.1f} dB — fixture leaks"


# --------------------------------------------------------------------------
# Music mode (create-form control): the stored task-level instruction
# --------------------------------------------------------------------------


def test_validate_music_mode():
    from src.media.music_library import validate_music_mode

    assert validate_music_mode(None) is None
    assert validate_music_mode("auto") == "auto"
    assert validate_music_mode("ON") == "on"
    assert validate_music_mode(" off ") == "off"
    for bad in ("loud", "", 3, ["on"]):
        with pytest.raises(ValueError):
            validate_music_mode(bad)


def test_apply_music_off_stamps_the_user_none_silence():
    from src.media.music_library import apply_music_off

    edl = {
        "audio": {
            "music_track": "song.mp3",
            "music_mood": "upbeat",
            "music_volume": 0.3,
            "music_volume_0_to_1": 0.3,
            "ducking_db": -10.0,
            "music_source": "auto_bed",
        }
    }
    apply_music_off(edl)
    audio = edl["audio"]
    assert audio["music_track"] == ""
    assert audio["music_mood"] == "none"
    assert audio["music_volume"] == 0.0
    assert audio["music_volume_0_to_1"] == 0.0
    assert audio["music_source"] == "user_none"


def test_music_expectation_honours_the_mode(tmp_path, monkeypatch):
    _library(tmp_path, monkeypatch)
    silent_profile = {"audio": {"music_mood": "none", "music_volume_0_to_1": 0.0}}
    music_profile = {"audio": {"music_mood": "upbeat", "music_volume_0_to_1": 0.2}}

    # Off expects silence even when a track was picked and the profile
    # carries a bed — QC must never demand music the user switched off.
    assert music_expectation(
        music_profile, "pick.mp3", "gaming", music_mode="off"
    ) == (False, None, "none")
    # On with a pick is the ordinary override expectation.
    assert music_expectation(
        silent_profile, "pick.mp3", "meme_comedy", music_mode="on"
    ) == (True, "pick.mp3", "override")
    # On without a pick expects the profile's own bed…
    assert music_expectation(
        music_profile, None, "gaming", music_mode="on"
    ) == (True, "upbeat (profile mood track)", "profile")
    # On without a pick on a bed-less profile now falls back to the same
    # Auto-bed selection Auto would make (self-run audit issue #3): the
    # user explicitly asked for music, so silence would contradict the
    # instruction.
    assert music_expectation(
        silent_profile, None, "meme_comedy", music_mode="on"
    ) == (True, "calm (auto bed)", "auto_bed")
    # Auto (and pre-control rows) keep the historic precedence.
    assert music_expectation(
        silent_profile, None, "podcast_news", music_mode="auto"
    ) == (True, "calm (auto bed)", "auto_bed")
    assert music_expectation(silent_profile, None, "podcast_news") == (
        True, "calm (auto bed)", "auto_bed",
    )


def test_pipeline_edl_music_off_silences_a_music_bearing_profile():
    from src.auto_director.edl import load_profile

    profile = load_profile("education_tutorial")  # carries a calm bed
    assert not profile_audio_is_silent(profile["audio"])
    edl = VideoService.build_pipeline_edl(
        _segment(), _words(), niche_override="education_tutorial",
        duration=12.0, music_mode="off",
    )
    assert edl is not None
    audio = edl["audio"]
    assert audio["music_mood"] == "none"
    assert audio["music_volume"] == 0.0
    assert audio["music_source"] == "user_none"


def test_pipeline_edl_music_off_beats_an_explicit_pick(tmp_path, monkeypatch):
    assets = _library(tmp_path, monkeypatch)
    (assets / "music" / "chosen.wav").touch()
    edl = VideoService.build_pipeline_edl(
        _segment(), _words(), niche_override="podcast_news", duration=12.0,
        music_override="chosen.wav", music_mode="off",
    )
    assert edl is not None
    assert edl["audio"]["music_track"] == ""
    assert edl["audio"]["music_source"] == "user_none"


def test_pipeline_edl_music_on_with_pick_applies_the_pick(tmp_path, monkeypatch):
    assets = _library(tmp_path, monkeypatch)
    (assets / "music" / "chosen.wav").touch()
    edl = VideoService.build_pipeline_edl(
        _segment(), _words(), niche_override="podcast_news", duration=12.0,
        music_override="chosen.wav", music_mode="on",
    )
    assert edl is not None
    assert edl["audio"]["music_track"] == "chosen.wav"
    # The pick's audible defaults for a silent profile still apply.
    assert edl["audio"]["music_volume"] > 0


def test_pipeline_edl_music_on_without_pick_keeps_profile_bed():
    edl = VideoService.build_pipeline_edl(
        _segment(), _words(), niche_override="education_tutorial",
        duration=12.0, music_mode="on",
    )
    assert edl is not None
    assert not profile_audio_is_silent(edl["audio"])
    assert edl["audio"].get("music_source") != "auto_bed"


def test_pipeline_edl_music_on_without_pick_falls_back_to_auto_bed(
    tmp_path, monkeypatch
):
    # true_crime carries no bed by design; mode On is the user's
    # explicit instruction for music, so it gets the Auto selection
    # rather than silence (self-run audit issue #3). (This used
    # meme_comedy until 2026-11-18, when meme_comedy got its own comedy
    # bed at Luffy's request; the rule under test is unchanged.)
    _library(tmp_path, monkeypatch)
    edl = VideoService.build_pipeline_edl(
        _segment(), _words(), niche_override="true_crime", duration=12.0,
        music_mode="on",
    )
    assert edl is not None
    assert edl["audio"]["music_mood"] == "calm"
    assert edl["audio"]["music_source"] == "auto_bed"


def test_pipeline_edl_music_auto_unchanged_for_silent_profile(
    tmp_path, monkeypatch
):
    _library(tmp_path, monkeypatch)
    edl = VideoService.build_pipeline_edl(
        _segment(), _words(), niche_override="podcast_news", duration=12.0,
        music_mode="auto",
    )
    assert edl is not None
    assert edl["audio"]["music_source"] == "auto_bed"


@pytest.mark.asyncio
async def test_create_task_rejects_invalid_music_mode_with_422(monkeypatch):
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    from fastapi import HTTPException

    from src.api.routes import tasks

    monkeypatch.setattr(
        tasks, "_get_user_id_from_headers", AsyncMock(return_value="user-1")
    )
    monkeypatch.setattr(
        "src.source_submission.record_source_submission", AsyncMock()
    )

    with pytest.raises(HTTPException) as error:
        await tasks.create_task(
            SimpleNamespace(
                json=AsyncMock(
                    return_value={
                        "source": {
                            "url": "https://www.youtube.com/watch?v=abcdefghijk"
                        },
                        "music_mode": "loud",
                    }
                ),
                headers={},
            ),
            db=None,
        )

    assert error.value.status_code == 422
    assert "music_mode" in error.value.detail
