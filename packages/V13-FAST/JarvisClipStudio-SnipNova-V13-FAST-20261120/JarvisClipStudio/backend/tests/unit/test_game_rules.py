"""Per-game rules + forced hashtags (PeakCut take)."""

from src.auto_director.game_rules import (
    GENERIC,
    apply_forced_hashtags,
    detect_game,
    forced_hashtags,
    game_rules_prompt,
    name_of,
    tags_of,
)
from src.services.social_service import _force_game_hashtags, normalize_hashtags


def test_detect_gta6_from_title():
    name, tags = detect_game("Kai Cenat plays GTA 6 — day one chaos")
    assert name == "Grand Theft Auto VI"
    assert "GTA6" in tags


def test_detect_longest_alias_wins():
    name, _tags = detect_game("grand theft auto vi launch stream")
    assert name == "Grand Theft Auto VI"
    name_v, _ = detect_game("GTA Online grind")
    assert name_v == "Grand Theft Auto V"


def test_detect_unknown_game():
    assert detect_game("cooking with grandma") == (None, [])
    assert detect_game("") == (None, [])
    assert detect_game(None) == (None, [])


def test_detect_does_not_match_substrings():
    # "gta6" inside a longer word must not match.
    assert detect_game("thegta6show") == (None, [])


def test_name_and_tags_roundtrip():
    assert name_of("gta 6") == "Grand Theft Auto VI"
    assert name_of("Grand Theft Auto VI") == "Grand Theft Auto VI"
    assert "ViceCity" in tags_of("GTA6")
    assert tags_of(None) == []


def test_forced_hashtags_detected_and_generic():
    assert "#GTA6" in forced_hashtags("my gta6 clip")
    assert forced_hashtags("some untitled gaming video") == [f"#{t}" for t in GENERIC]


def test_apply_forced_hashtags_dedupes_and_keeps_existing():
    out = apply_forced_hashtags(["#funny", "gta6"], "GTA 6 highlights")
    assert out[0] == "#funny"
    # The existing "#gta6" already covers the forced GTA6 tag (case-insensitive).
    assert sum(1 for t in out if t.lower() == "#gta6") == 1
    assert "#ViceCity" in out


def test_game_rules_prompt_mentions_game_and_tags():
    prompt = game_rules_prompt("Grand Theft Auto VI")
    assert "Grand Theft Auto VI" in prompt
    assert "#GTA6" in prompt
    assert game_rules_prompt(None) == ""
    assert game_rules_prompt("unknown game xyz") == ""


def test_social_force_game_hashtags():
    clean = normalize_hashtags(["funny"])
    out = _force_game_hashtags(clean, "He finally beat GTA 6")
    assert "funny" in out
    assert "GTA6" in out and "ViceCity" in out


def test_social_force_game_hashtags_no_game_no_change():
    clean = normalize_hashtags(["funny"])
    assert _force_game_hashtags(clean, "a quiet podcast clip") == clean
