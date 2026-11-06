"""Unit tests for auto_director EDL builder."""

import sys
from pathlib import Path

# Add backend to path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from src.auto_director.edl import load_profile, build_edl, _extract_verbatim_quotes_and_numbers


def test_load_profile_podcast_news():
    """Test loading podcast_news profile."""
    profile = load_profile("podcast_news")
    assert profile["niche"] == "podcast_news"
    assert profile["pacing"]["target_cuts_per_min"] == [8, 12]
    assert profile["transitions"]["default"] == "hard_cut"
    assert profile["transitions"]["duration_sec"] == 0.05
    assert profile["zooms"]["punch_in_scale"] == 1.25


def test_load_profile_travel_vlog():
    """Test loading travel_vlog profile."""
    profile = load_profile("travel_vlog")
    assert profile["niche"] == "travel_vlog"
    assert profile["pacing"]["target_cuts_per_min"] == [3, 5]
    assert profile["transitions"]["default"] == "cross_dissolve"
    assert profile["transitions"]["duration_sec"] == 0.8
    assert profile["zooms"]["ken_burns"] is True


def test_load_profile_unknown_niche():
    """Test loading unknown niche raises clear error."""
    try:
        load_profile("invalid_niche")
        assert False, "Should have raised ValueError"
    except ValueError as e:
        assert "invalid_niche" in str(e)
        assert "Valid niches" in str(e)


def test_build_edl_podcast_hard_cut_and_punch_in():
    """Podcast fixture -> hard cut + punch-in at energy peaks."""
    profile = load_profile("podcast_news")

    clip = {"id": "clip1", "start": 0, "end": 30, "duration": 30}
    transcript_words = [
        {"word": "The", "start": 0.5, "end": 0.6},
        {"word": "truth", "start": 0.6, "end": 0.8},
        {"word": "is", "start": 0.8, "end": 0.9},
        {"word": "we", "start": 0.9, "end": 1.0},
        {"word": "need", "start": 1.0, "end": 1.2},
        {"word": "42", "start": 1.2, "end": 1.4},
        {"word": "percent", "start": 1.4, "end": 1.7},
        {"word": "growth", "start": 1.7, "end": 2.0},
    ]
    energy_peaks = [2.5, 10.0, 20.0]

    edl = build_edl(clip, transcript_words, profile, energy_peaks)

    # Check hard cuts
    assert len(edl["segments"]) >= 3  # ~30 sec at ~10 cuts/min = 5 cuts
    for i, seg in enumerate(edl["segments"]):
        if i == 0:
            assert seg["transition_in"] == "none"
        else:
            assert seg["transition_in"] == "hard_cut"
            assert seg["transition_duration"] == 0.05

    # Check punch-in keyframes at energy peaks
    punch_ins = [z for z in edl["zoom_keyframes"] if z["type"] == "punch_in"]
    assert len(punch_ins) == 3
    for peak, pin in zip(energy_peaks, punch_ins):
        assert pin["time"] == peak
        assert pin["scale"] == 1.25
        assert pin["trigger"] == "emphasis"

    # No ken_burns for podcast
    ken_burns = [z for z in edl["zoom_keyframes"] if z["type"] == "ken_burns"]
    assert len(ken_burns) == 0


def test_build_edl_travel_fade_and_ken_burns():
    """Travel fixture -> fade 0.8 + ken_burns."""
    profile = load_profile("travel_vlog")

    clip = {"id": "clip2", "start": 0, "end": 60, "duration": 60}
    transcript_words = [
        {"word": "Tokyo", "start": 1.0, "end": 1.3},
        {"word": "is", "start": 1.3, "end": 1.4},
        {"word": "beautiful", "start": 1.4, "end": 1.8},
    ]
    energy_peaks = [15.0, 30.0]

    edl = build_edl(clip, transcript_words, profile, energy_peaks)

    # Check fade transitions (cross_dissolve 0.8s)
    for i, seg in enumerate(edl["segments"]):
        if i == 0:
            assert seg["transition_in"] == "none"
        else:
            assert seg["transition_in"] == "cross_dissolve"
            assert seg["transition_duration"] == 0.8

    # Check ken_burns keyframe
    ken_burns = [z for z in edl["zoom_keyframes"] if z["type"] == "ken_burns"]
    assert len(ken_burns) == 1
    kb = ken_burns[0]
    assert kb["start"] == 0.0
    assert kb["end"] == 60.0
    assert kb["scale_start"] == 1.0
    assert kb["scale_end"] == 1.05

    # Check punch-in at energy peaks
    punch_ins = [z for z in edl["zoom_keyframes"] if z["type"] == "punch_in"]
    assert len(punch_ins) == 2


def test_no_numbers_in_transcript_zero_info_cards():
    """Transcript with no numbers -> zero info cards."""
    profile = load_profile("podcast_news")  # has info_cards enabled

    clip = {"id": "clip3", "start": 0, "end": 10, "duration": 10}
    transcript_words = [
        {"word": "Hello", "start": 0.0, "end": 0.5},
        {"word": "world", "start": 0.5, "end": 1.0},
        {"word": "this", "start": 1.0, "end": 1.3},
        {"word": "is", "start": 1.3, "end": 1.4},
        {"word": "a", "start": 1.4, "end": 1.5},
        {"word": "test", "start": 1.5, "end": 1.8},
    ]
    energy_peaks = [2.0, 5.0]

    edl = build_edl(clip, transcript_words, profile, energy_peaks)

    # No numbers in transcript, so no info cards
    info_cards = [o for o in edl["overlays"] if o["type"] == "info_card"]
    assert len(info_cards) == 0


def test_numbers_in_transcript_creates_info_cards():
    """Transcript with numbers -> creates info cards (verbatim)."""
    profile = load_profile("podcast_news")

    clip = {"id": "clip4", "start": 0, "end": 10, "duration": 10}
    transcript_words = [
        {"word": "Revenue", "start": 0.0, "end": 0.4},
        {"word": "grew", "start": 0.4, "end": 0.7},
        {"word": "42", "start": 0.7, "end": 0.9},
        {"word": "percent", "start": 0.9, "end": 1.2},
        {"word": "last", "start": 1.2, "end": 1.4},
        {"word": "quarter", "start": 1.4, "end": 1.8},
    ]
    energy_peaks = [1.0]

    edl = build_edl(clip, transcript_words, profile, energy_peaks)

    info_cards = [o for o in edl["overlays"] if o["type"] == "info_card"]
    assert len(info_cards) >= 1
    # Check verbatim extraction
    card = info_cards[0]
    assert "42" in card["text"]
    assert "percent" in card["text"]
    assert card["source"] == "transcript_verbatim"


def test_extract_verbatim_quotes_and_numbers():
    """Test verbatim extraction helper."""
    transcript_words = [
        {"word": "The", "start": 0.0, "end": 0.2},
        {"word": "truth", "start": 0.2, "end": 0.5},
        {"word": "is", "start": 0.5, "end": 0.6},
        {"word": "42", "start": 0.6, "end": 0.8},
        {"word": "percent", "start": 0.8, "end": 1.1},
    ]
    candidates = _extract_verbatim_quotes_and_numbers(transcript_words)
    # Should find the number "42 percent"
    numbers = [c for c in candidates if c["type"] == "number"]
    assert len(numbers) >= 1
    assert "42" in numbers[0]["text"]
    assert "percent" in numbers[0]["text"]


def test_captions_preset_from_profile():
    """Test caption preset comes from profile."""
    profile = load_profile("meme_comedy")

    clip = {"id": "clip5", "start": 0, "end": 15, "duration": 15}
    transcript_words = [{"word": "test", "start": 0, "end": 0.5}]
    energy_peaks = []

    edl = build_edl(clip, transcript_words, profile, energy_peaks)

    assert edl["captions"]["preset_id"] == "large_centre_pop"
    assert edl["captions"]["words_per_line"] == 3
    assert edl["captions"]["position"] == "center"


def test_broll_disabled_for_meme_comedy():
    """Meme comedy has broll disabled."""
    profile = load_profile("meme_comedy")

    clip = {"id": "clip6", "start": 0, "end": 30, "duration": 30}
    transcript_words = [{"word": "funny", "start": 0, "end": 0.5}]
    energy_peaks = []

    edl = build_edl(clip, transcript_words, profile, energy_peaks)

    assert edl["broll"] == []


def test_broll_enabled_for_travel_vlog():
    """Travel vlog has broll enabled with pexels source."""
    profile = load_profile("travel_vlog")

    clip = {"id": "clip7", "start": 0, "end": 60, "duration": 60}
    transcript_words = [
        {"word": "Tokyo", "start": 0, "end": 0.5},
        {"word": "mountain", "start": 1, "end": 1.5},
    ]
    energy_peaks = []

    edl = build_edl(clip, transcript_words, profile, energy_peaks)

    assert len(edl["broll"]) >= 1
    for b in edl["broll"]:
        assert b["source"] == "pexels"
        assert b["url"] == ""  # no web scraping
        assert "local_path" in b


def test_audio_settings_from_profile():
    """Test audio settings come from profile."""
    profile = load_profile("finance_business")

    clip = {"id": "clip8", "start": 0, "end": 30, "duration": 30}
    transcript_words = [{"word": "money", "start": 0, "end": 0.5}]
    energy_peaks = []

    edl = build_edl(clip, transcript_words, profile, energy_peaks)

    assert edl["audio"]["music_mood"] == "corporate"
    assert edl["audio"]["music_volume"] == 0.15
    assert edl["audio"]["ducking_db"] == -12.0


def test_color_lut_from_profile():
    """Test color LUT comes from profile."""
    profile = load_profile("vlog_lifestyle")

    clip = {"id": "clip9", "start": 0, "end": 30, "duration": 30}
    transcript_words = [{"word": "life", "start": 0, "end": 0.5}]
    energy_peaks = []

    edl = build_edl(clip, transcript_words, profile, energy_peaks)

    assert edl["color"]["lut"] == "punchy"


def test_hook_settings_from_profile():
    """Test hook settings come from profile."""
    profile = load_profile("education_tutorial")

    clip = {"id": "clip10", "start": 0, "end": 30, "duration": 30}
    transcript_words = [{"word": "learn", "start": 0, "end": 0.5}]
    energy_peaks = []

    edl = build_edl(clip, transcript_words, profile, energy_peaks)

    assert edl["hook"]["title_style"] == "question"
    assert edl["hook"]["title_seconds"] == 4.0


def run_all_tests():
    """Run all tests and report results."""
    tests = [
        ("load_profile_podcast_news", test_load_profile_podcast_news),
        ("load_profile_travel_vlog", test_load_profile_travel_vlog),
        ("load_profile_unknown_niche", test_load_profile_unknown_niche),
        ("build_edl_podcast_hard_cut_and_punch_in", test_build_edl_podcast_hard_cut_and_punch_in),
        ("build_edl_travel_fade_and_ken_burns", test_build_edl_travel_fade_and_ken_burns),
        ("no_numbers_in_transcript_zero_info_cards", test_no_numbers_in_transcript_zero_info_cards),
        ("numbers_in_transcript_creates_info_cards", test_numbers_in_transcript_creates_info_cards),
        ("extract_verbatim_quotes_and_numbers", test_extract_verbatim_quotes_and_numbers),
        ("captions_preset_from_profile", test_captions_preset_from_profile),
        ("broll_disabled_for_meme_comedy", test_broll_disabled_for_meme_comedy),
        ("broll_enabled_for_travel_vlog", test_broll_enabled_for_travel_vlog),
        ("audio_settings_from_profile", test_audio_settings_from_profile),
        ("color_lut_from_profile", test_color_lut_from_profile),
        ("hook_settings_from_profile", test_hook_settings_from_profile),
    ]

    passed = 0
    failed = 0

    for name, test_fn in tests:
        try:
            test_fn()
            print(f"[PASS] {name}")
            passed += 1
        except Exception as e:
            print(f"[FAIL] {name}: {e}")
            import traceback
            traceback.print_exc()
            failed += 1

    print(f"\n--- Results: {passed} passed, {failed} failed ---")
    return failed == 0


if __name__ == "__main__":
    success = run_all_tests()
    sys.exit(0 if success else 1)