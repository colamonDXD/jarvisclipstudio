"""Unit tests for the six new auto-director niches.

Auto-detected (transcript pipeline): true_crime, hindi_podcast, music_artist.
Manual-only (explicit override, never classified): football_edit,
japan_travel_cinematic, anime_funk_edit.
"""

import sys
from pathlib import Path

# Add backend to path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from src.auto_director.edl import load_profile, build_edl

ALL_NICHES = [
    "podcast_news", "travel_vlog", "meme_comedy", "gaming",
    "sports", "education_tutorial", "finance_business", "vlog_lifestyle",
    "true_crime", "hindi_podcast", "music_artist",
    "football_edit", "japan_travel_cinematic", "anime_funk_edit",
]

WORDS = [{"word": "test", "start": 0, "end": 0.5}]


def _edl(niche, duration=30, peaks=None):
    profile = load_profile(niche)
    clip = {"id": "clip", "start": 0, "end": duration, "duration": duration}
    return build_edl(clip, WORDS, profile, peaks or [])


def test_all_fourteen_profiles_schema_and_pacing_consistency():
    """Every profile (old + new) loads, carries the full key structure, and
    keeps the pacing invariant min_shot = 60/hi-cuts, max_shot = 60/lo-cuts."""
    required_sections = {
        "pacing", "transitions", "zooms", "captions",
        "overlays", "broll", "audio", "color", "hook",
    }
    # Mirrors TRANSITION_MAP in src/media/edl_render.py.
    renderable_defaults = {"hard_cut", "cut", "none", "cross_dissolve", "fade", "slide", "wipe"}

    for niche in ALL_NICHES:
        profile = load_profile(niche)
        assert profile["niche"] == niche
        assert required_sections <= set(profile.keys()), niche
        pacing = profile["pacing"]
        lo, hi = pacing["target_cuts_per_min"]
        assert 0 < lo < hi, niche
        assert abs(pacing["min_shot_sec"] - 60 / hi) <= 0.1, niche
        assert abs(pacing["max_shot_sec"] - 60 / lo) <= 0.1, niche
        assert profile["hook"]["title_seconds"] <= 3.0, niche
        assert profile["transitions"]["default"] in renderable_defaults, niche


def test_true_crime_dark_captions_and_sting_markers():
    """True crime: documentary classic_box captions, near-dry audio (no music
    bed) with user-library sting markers at energy peaks."""
    edl = _edl("true_crime", peaks=[5.0, 20.0])

    assert edl["captions"]["preset_id"] == "classic_box"
    assert edl["audio"]["music_mood"] == "none"
    assert edl["audio"]["music_volume"] == 0.0
    assert [s["time"] for s in edl["sfx"]] == [5.0, 20.0]
    assert all(s["source"] == "user_library" for s in edl["sfx"])
    # Podcast-family pacing and hard cuts
    assert edl["segments"][1]["transition_in"] == "hard_cut"


def test_hindi_podcast_punch_zoom_and_calm_bed():
    """Hindi podcast: Shamani-style 1.15x punch-ins on emphasis, karaoke
    captions, low ducked calm bed, SFX markers on."""
    profile = load_profile("hindi_podcast")
    edl = _edl("hindi_podcast", peaks=[10.0])

    punch_ins = [z for z in edl["zoom_keyframes"] if z["type"] == "punch_in"]
    assert len(punch_ins) == 1
    assert punch_ins[0]["scale"] == 1.15
    assert punch_ins[0]["trigger"] == "emphasis"
    assert edl["captions"]["preset_id"] == "hormozi"
    assert edl["audio"]["music_mood"] == "calm"
    assert edl["audio"]["music_volume"] == 0.15
    assert edl["audio"]["ducking_db"] == -12.0
    assert profile["audio"]["sfx_from_user_library"] is True


def test_music_artist_lyric_karaoke_no_added_audio():
    """Music artist: neon lyric-karaoke captions centred, no music bed and
    no SFX layered over the track itself."""
    edl = _edl("music_artist")

    assert edl["captions"]["preset_id"] == "neon"
    assert edl["captions"]["position"] == "center"
    assert edl["audio"]["music_mood"] == "none"
    assert edl["sfx"] == []
    assert edl["broll"] == []
    # 30s at 15-25 cuts/min -> ~10 segments
    assert len(edl["segments"]) == 10


def test_football_edit_velocity_pacing_and_phonk():
    """Football edit (manual-only): 25-40 cuts/min post-drop pacing, one-word
    uppercase impact captions, phonk bed resolved from the user library."""
    edl = _edl("football_edit", peaks=[7.5])

    # 30s at avg 32.5 cuts/min -> 16 segments
    assert len(edl["segments"]) == 16
    assert edl["segments"][1]["transition_in"] == "cut"
    assert edl["captions"]["preset_id"] == "bold_uppercase"
    assert edl["captions"]["words_per_line"] == 2
    assert edl["audio"]["music_mood"] == "phonk"
    punch_ins = [z for z in edl["zoom_keyframes"] if z["type"] == "punch_in"]
    assert punch_ins[0]["scale"] == 1.3
    assert punch_ins[0]["trigger"] == "energy_peak"


def test_japan_travel_cinematic_wipe_ken_burns_classic_box():
    """Japan cinematic (manual-only): wipe-family transitions, slow ken
    burns push-in, single classic_box location line, calm bed."""
    edl = _edl("japan_travel_cinematic", duration=60, peaks=[30.0])

    for i, seg in enumerate(edl["segments"]):
        if i == 0:
            assert seg["transition_in"] == "none"
        else:
            assert seg["transition_in"] == "wipe"
            assert seg["transition_duration"] == 0.4
    ken_burns = [z for z in edl["zoom_keyframes"] if z["type"] == "ken_burns"]
    assert len(ken_burns) == 1
    assert edl["captions"]["preset_id"] == "classic_box"
    assert edl["audio"]["music_mood"] == "calm"


def test_anime_funk_edit_beat_density_and_funk():
    """Anime funk (manual-only): every-beat post-drop cutting density,
    minimal captions, funk bed from the user library."""
    edl = _edl("anime_funk_edit", duration=10)

    # 10s at avg 90 cuts/min -> 15 segments
    assert len(edl["segments"]) == 15
    assert edl["captions"]["preset_id"] == "minimal"
    assert edl["audio"]["music_mood"] == "funk"
    assert edl["audio"]["music_volume"] == 0.3
