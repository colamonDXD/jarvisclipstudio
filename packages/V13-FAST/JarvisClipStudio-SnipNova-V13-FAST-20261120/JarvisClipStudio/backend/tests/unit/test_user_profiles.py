"""User reference-style profiles: storage, loader precedence, validation.

Lane H2 stores user-generated profiles outside the stock tree under
``user-<slug>`` names. These tests pin the guarantees the feature rests
on: a user profile loads through the real loader, it can never shadow a
stock niche, stock files are never written, and task-level validation
accepts a user style only while it exists.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from src.auto_director import edl as edl_module
from src.auto_director import user_profiles
from src.auto_director.edl import build_edl, load_profile
from src.auto_director.niche import (
    is_known_profile,
    resolve_effective_profile,
    validate_niche_override,
)

WORDS = [
    {"word": "hello", "start": 0.0, "end": 0.4},
    {"word": "world", "start": 0.5, "end": 0.9},
    {"word": "this", "start": 1.0, "end": 1.3},
    {"word": "clip", "start": 1.4, "end": 1.8},
]


@pytest.fixture
def profiles_dir(tmp_path, monkeypatch):
    folder = tmp_path / "user_profiles"
    folder.mkdir(parents=True)
    monkeypatch.setattr(user_profiles, "user_profiles_dir", lambda: folder)
    return folder


def _profile(display_name: str, base_niche: str = "gaming") -> dict:
    """A minimal analyser-shaped profile dict (starts from stock gaming)."""
    profile = json.loads(
        (edl_module.PROFILES_DIR / f"{base_niche}.json").read_text(encoding="utf-8")
    )
    profile["reference_style"] = {
        "version": 1,
        "name": display_name,
        "base_niche": base_niche,
        "source_file": "ref.mp4",
        "headline": {
            "cuts_per_min": 42.0,
            "caption_frame_fraction": 0.8,
            "music_bed_present": True,
            "tempo_bpm": 128.0,
        },
    }
    return profile


def _save(slug: str, display_name: str) -> dict:
    return user_profiles.save_user_profile(
        slug,
        _profile(display_name),
        {"measurements": {}, "mapping": {"base_niche": "gaming"}},
    )


# --------------------------------------------------------------------- names


@pytest.mark.parametrize(
    "raw,slug",
    [
        ("My Funk Edit", "my-funk-edit"),
        ("  spaced   out  ", "spaced-out"),
        ("Caps & Punctuation!", "caps-punctuation"),
        ("", "reference-style"),
        ("!!!", "reference-style"),
    ],
)
def test_slugify_style_name(raw, slug):
    assert user_profiles.slugify_style_name(raw) == slug


@pytest.mark.parametrize(
    "value,ok",
    [
        ("user-my-style", True),
        ("user-a1-b2", True),
        ("gaming", False),
        ("user-", False),
        ("user-My Style", False),
        ("user-../gaming", False),
        ("user-a/b", False),
        (None, False),
        (42, False),
    ],
)
def test_is_user_profile_name(value, ok):
    assert user_profiles.is_user_profile_name(value) is ok


# ------------------------------------------------------------------- storage


def test_save_list_delete_round_trip(profiles_dir):
    summary = _save("my-style", "My Style")
    assert summary["name"] == "user-my-style"
    assert summary["display_name"] == "My Style"
    assert summary["base_niche"] == "gaming"
    assert (profiles_dir / "my-style.json").is_file()
    assert (profiles_dir / "my-style.measurements.json").is_file()

    listed = user_profiles.list_user_profiles()
    assert [entry["name"] for entry in listed] == ["user-my-style"]
    assert listed[0]["headline"]["cuts_per_min"] == 42.0
    assert "created_at" in listed[0]

    assert user_profiles.delete_user_profile("user-my-style") is True
    assert user_profiles.list_user_profiles() == []
    assert user_profiles.delete_user_profile("user-my-style") is False


def test_unique_slug_avoids_collisions(profiles_dir):
    _save("dup", "Dup")
    assert user_profiles.unique_user_slug("dup") == "dup-2"
    _save("dup-2", "Dup Two")
    assert user_profiles.unique_user_slug("dup") == "dup-3"


def test_delete_refuses_stock_and_traversal_names(profiles_dir):
    assert user_profiles.delete_user_profile("gaming") is False
    assert user_profiles.delete_user_profile("user-../gaming") is False
    assert (edl_module.PROFILES_DIR / "gaming.json").is_file()


# -------------------------------------------------------------------- loader


def test_user_profile_loads_and_drives_build_edl(profiles_dir):
    _save("my-style", "My Style")
    loaded = load_profile("user-my-style")
    assert loaded["reference_style"]["name"] == "My Style"

    clip = {"id": "clip", "start": 0, "end": 30, "duration": 30}
    edl = build_edl(clip, WORDS, loaded, [5.0])
    assert edl["segments"]
    assert edl["captions"]["preset_id"] == loaded["captions"]["preset_id"]


def test_user_profile_cannot_shadow_stock(profiles_dir):
    stock_path = edl_module.PROFILES_DIR / "gaming.json"
    stock_hash_before = hashlib.sha256(stock_path.read_bytes()).hexdigest()

    # A user style slugged exactly like a stock niche still lives under
    # its prefixed name, in the user directory only.
    _save("gaming", "Gaming Impostor")
    loaded_stock = load_profile("gaming")
    assert "reference_style" not in loaded_stock
    assert loaded_stock["niche"] == "gaming"

    loaded_user = load_profile("user-gaming")
    assert loaded_user["reference_style"]["name"] == "Gaming Impostor"

    assert hashlib.sha256(stock_path.read_bytes()).hexdigest() == stock_hash_before
    assert not (edl_module.PROFILES_DIR / "user-gaming.json").exists()


def test_loader_unknown_and_missing_names(profiles_dir):
    with pytest.raises(ValueError):
        load_profile("not-a-niche")
    with pytest.raises(FileNotFoundError):
        load_profile("user-ghost")


# ---------------------------------------------------------------- validation


def test_override_validation_follows_existence(profiles_dir):
    assert validate_niche_override("gaming") == "gaming"
    with pytest.raises(ValueError):
        validate_niche_override("user-my-style")

    _save("my-style", "My Style")
    assert validate_niche_override("user-my-style") == "user-my-style"
    assert is_known_profile("user-my-style")

    user_profiles.delete_user_profile("user-my-style")
    with pytest.raises(ValueError):
        validate_niche_override("user-my-style")


def test_resolve_effective_profile_honours_user_styles(profiles_dir):
    assert resolve_effective_profile(project_override="user-my-style") == "podcast_news"
    _save("my-style", "My Style")
    assert resolve_effective_profile(project_override="user-my-style") == "user-my-style"
    assert resolve_effective_profile(clip_override="user-my-style") == "user-my-style"
    # Stock behaviour is unchanged.
    assert resolve_effective_profile(project_override="gaming") == "gaming"
    assert resolve_effective_profile(classifier_niche="sports") == "sports"


def test_listing_skips_corrupt_files(profiles_dir):
    _save("good", "Good")
    (profiles_dir / "broken.json").write_text("{not json", encoding="utf-8")
    listed = user_profiles.list_user_profiles()
    assert [entry["name"] for entry in listed] == ["user-good"]
