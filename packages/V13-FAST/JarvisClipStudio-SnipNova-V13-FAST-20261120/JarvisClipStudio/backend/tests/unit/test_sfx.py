"""Tests for SFX placements: validation, plan storage, library listing."""

from __future__ import annotations

import pytest

from src.auto_director.clip_edits import build_edit_plan
from src.auto_director.sfx import (
    normalize_sfx_placements,
    sfx_placements_from_plan,
)
from src.media import sfx_library


# --- validation --------------------------------------------------------------------


def test_normalize_keeps_valid_placements():
    placements = normalize_sfx_placements(
        [
            {"name": "pop.wav", "at_sec": 1.25, "gain_db": -3},
            {"name": "whoosh.mp3", "at_sec": 4, "gain_db": 2.5},
        ]
    )
    assert placements == [
        {"name": "pop.wav", "at_sec": 1.25, "gain_db": -3.0},
        {"name": "whoosh.mp3", "at_sec": 4.0, "gain_db": 2.5},
    ]


def test_normalize_defaults_gain_and_time():
    assert normalize_sfx_placements([{"name": "pop.wav"}]) == [
        {"name": "pop.wav", "at_sec": 0.0, "gain_db": 0.0}
    ]


def test_normalize_none_and_empty_are_distinct():
    # None = "not mentioned" (carry forward); [] = "clear everything".
    assert normalize_sfx_placements(None) is None
    assert normalize_sfx_placements([]) == []


@pytest.mark.parametrize(
    "bad",
    [
        [{"name": "../pop.wav", "at_sec": 0}],
        [{"name": "sub/pop.wav", "at_sec": 0}],
        [{"name": "pop.txt", "at_sec": 0}],
        [{"name": "", "at_sec": 0}],
        [{"name": "pop.wav", "at_sec": -1}],
        [{"name": "pop.wav", "at_sec": 0, "gain_db": 99}],
        [{"name": "pop.wav", "at_sec": 0, "gain_db": -99}],
        [{"name": "pop.wav", "at_sec": "soon"}],
        [{"name": "pop.wav", "at_sec": 0, "volume": 3}],
        [{"name": "pop.wav", "at_sec": float("nan")}],
        ["pop.wav"],
        [{"name": "pop.wav", "at_sec": 0}] * 17,
    ],
)
def test_normalize_rejects_bad_placements(bad):
    with pytest.raises(ValueError):
        normalize_sfx_placements(bad)


def test_normalize_rejects_non_list():
    with pytest.raises(ValueError):
        normalize_sfx_placements({"name": "pop.wav"})


# --- plan storage ---------------------------------------------------------------------


def _segments():
    return [
        {
            "index": 0,
            "start": 0.0,
            "end": 4.0,
            "duration": 4.0,
            "transition_in": "none",
            "transition_duration": 0.0,
        }
    ]


def test_plan_stores_placements_and_empty_clear():
    plan = build_edit_plan(
        _segments(), sfx=[{"name": "pop.wav", "at_sec": 1.0, "gain_db": 0.0}]
    )
    assert sfx_placements_from_plan(plan) == [
        {"name": "pop.wav", "at_sec": 1.0, "gain_db": 0.0}
    ]
    cleared = build_edit_plan(_segments(), sfx=[])
    assert cleared["sfx"] == []
    assert sfx_placements_from_plan(cleared) == []
    untouched = build_edit_plan(_segments())
    assert "sfx" not in untouched
    assert sfx_placements_from_plan(untouched) is None


def test_plan_with_corrupt_sfx_block_reads_as_absent():
    plan = {"sfx": [{"name": "pop.wav", "at_sec": -5}]}
    assert sfx_placements_from_plan(plan) is None


# --- library ---------------------------------------------------------------------------


@pytest.fixture()
def sfx_dir(tmp_path, monkeypatch):
    folder = tmp_path / "assets" / "sfx"
    folder.mkdir(parents=True)
    (folder / "pop.wav").write_bytes(b"RIFF")
    (folder / "whoosh.mp3").write_bytes(b"ID3")
    (folder / "notes.txt").write_text("not audio")
    monkeypatch.setattr(sfx_library, "ASSETS_DIR", tmp_path / "assets")
    return folder


def test_library_lists_only_audio_with_durations(sfx_dir, monkeypatch):
    monkeypatch.setattr(sfx_library, "ffprobe_duration", lambda path: 0.4)
    tracks = sfx_library.list_sfx_tracks()
    assert [track["filename"] for track in tracks] == ["pop.wav", "whoosh.mp3"]
    assert tracks[0]["duration"] == 0.4
    assert tracks[0]["display_name"] == "pop"


def test_library_missing_folder_is_empty(tmp_path, monkeypatch):
    monkeypatch.setattr(sfx_library, "ASSETS_DIR", tmp_path / "nope")
    assert sfx_library.list_sfx_tracks() == []


# --- user uploads (the full Studio path) ---------------------------------
#
# The bundled assets/sfx folder is read-only in Docker, so user effects
# live in a second, writable root (parallel to user music). Listing
# merges both roots, find resolves both, and the bundled copy wins a
# filename clash — the same rules as the music library.


@pytest.fixture()
def sfx_two_roots(tmp_path, monkeypatch):
    bundled = tmp_path / "assets" / "sfx"
    bundled.mkdir(parents=True)
    (bundled / "pop.wav").write_bytes(b"RIFF-bundled")
    user = tmp_path / "user_sfx"
    user.mkdir(parents=True)
    (user / "riser.wav").write_bytes(b"RIFF-user")
    (user / "pop.wav").write_bytes(b"RIFF-shadowed")
    monkeypatch.setattr(sfx_library, "ASSETS_DIR", tmp_path / "assets")
    monkeypatch.setattr(sfx_library, "user_sfx_dir", lambda: user)
    monkeypatch.setattr(sfx_library, "ffprobe_duration", lambda path: 0.4)
    return bundled, user


def test_library_merges_user_uploads_with_source(sfx_two_roots):
    tracks = sfx_library.list_sfx_tracks()
    by_name = {track["filename"]: track for track in tracks}
    assert set(by_name) == {"pop.wav", "riser.wav"}
    assert by_name["pop.wav"]["source"] == "bundled"
    assert by_name["riser.wav"]["source"] == "user"


def test_find_resolves_user_uploads_and_bundled_wins(sfx_two_roots):
    bundled, user = sfx_two_roots
    assert sfx_library.find_sfx_file("riser.wav") == user / "riser.wav"
    assert sfx_library.find_sfx_file("riser") == user / "riser.wav"
    assert sfx_library.find_sfx_file("pop.wav") == bundled / "pop.wav"


def test_sanitize_and_unique_sfx_filenames(sfx_two_roots):
    assert sfx_library.sanitize_sfx_filename("../My Pop!.WAV") == "My Pop_.wav"
    assert sfx_library.unique_sfx_filename("fresh.wav") == "fresh.wav"
    assert sfx_library.unique_sfx_filename("pop.wav") == "pop_2.wav"
    assert sfx_library.unique_sfx_filename("riser.wav") == "riser_2.wav"


def test_find_sfx_file_resolves_like_the_renderer(sfx_dir):
    assert sfx_library.find_sfx_file("pop.wav") is not None
    assert sfx_library.find_sfx_file("pop") is not None  # stem resolution
    assert sfx_library.find_sfx_file("missing.wav") is None
