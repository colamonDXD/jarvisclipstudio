"""Watch-folder music-mode reconciliation: the autopilot settings carry
the create form's music mode (auto/on/off) next to the music track
override, and the pair must map onto task-creation fields exactly the
way a manual task carries them — for every mode x track combination.

Contract: mode is validated with the task field's own
``validate_music_mode`` (None = the settings row predates the control
and behaves as Auto); the mapped pair fed through the pipeline's own
``music_expectation`` must resolve the way the render will.
"""

from __future__ import annotations

import pytest
from fastapi import HTTPException

from src.api.routes import watch_folder as routes
from src.media.music_library import (
    apply_music_off,
    music_expectation,
    validate_music_mode,
)
from src.services.watch_folder_service import music_task_fields
from tests.unit.test_watch_folder import FakeRepository, _Request, _config

TRACK = "calm-piano.mp3"


@pytest.fixture
def route_env(tmp_path, monkeypatch):
    repo = FakeRepository()
    monkeypatch.setattr(routes, "repository", repo)
    monkeypatch.setattr(routes, "get_config", lambda: _config(tmp_path))

    async def fake_user_id(request, db, config):
        return "user-1"

    monkeypatch.setattr(routes, "resolve_authenticated_user_id", fake_user_id)
    return repo


@pytest.mark.parametrize("mode", [None, "auto", "on", "off"])
@pytest.mark.parametrize("track", [None, TRACK])
def test_mapping_covers_all_mode_track_combinations(mode, track):
    settings = {"music_mode": mode, "music_override": track}
    assert music_task_fields(settings) == {
        "music_mode": mode,
        "music_override": track,
    }


def test_mapping_normalizes_like_the_task_field():
    assert music_task_fields({"music_mode": " OFF "})["music_mode"] == "off"
    # A settings dict that predates the control carries no mode key at
    # all — it must still map (to Auto behaviour), never crash.
    assert music_task_fields({}) == {"music_mode": None, "music_override": None}


def test_mapping_rejects_an_invalid_mode():
    with pytest.raises(ValueError):
        music_task_fields({"music_mode": "loud"})


@pytest.mark.parametrize(
    "mode,track,expected_source",
    [
        (None, None, "profile"),      # auto + no track: profile bed
        (None, TRACK, "override"),    # auto + track: the pick plays
        ("auto", None, "profile"),
        ("auto", TRACK, "override"),
        ("on", None, "profile"),      # on + no track: profile bed, no Auto bed
        ("on", TRACK, "override"),
        ("off", None, "none"),        # off: silence either way
        ("off", TRACK, "none"),       # off beats a picked track
    ],
)
def test_mapped_pair_resolves_like_a_manual_task(mode, track, expected_source):
    fields = music_task_fields({"music_mode": mode, "music_override": track})
    # An audible profile bed: a mood AND a positive volume (a mood
    # alone counts as silent under profile_audio_is_silent).
    profile = {"audio": {"music_mood": "chill", "music_volume": 0.3}}
    expected, _label, source = music_expectation(
        profile,
        fields["music_override"],
        profile_name="podcast_news",
        music_mode=fields["music_mode"],
    )
    assert source == expected_source
    assert expected is (expected_source != "none")


def test_off_mode_silences_the_edl_like_the_render_path():
    fields = music_task_fields({"music_mode": "off", "music_override": TRACK})
    edl = {"audio": {"music_track": "chill.mp3", "music_mood": "chill",
                     "music_volume": 0.4}}
    if fields["music_mode"] == "off":
        apply_music_off(edl)
    assert edl["audio"]["music_track"] == ""
    assert edl["audio"]["music_volume"] == 0.0
    assert edl["audio"]["music_source"] == "user_none"


async def test_settings_route_roundtrips_music_mode(route_env):
    saved = await routes.update_watch_folder_settings(
        _Request({"enabled": True, "music_mode": "off"}), None
    )
    assert saved["settings"]["music_mode"] == "off"
    assert route_env.settings["user-1"]["music_mode"] == "off"

    result = await routes.get_watch_folder(_Request(), None)
    assert result["settings"]["music_mode"] == "off"


async def test_settings_route_defaults_music_mode_to_none(route_env):
    result = await routes.get_watch_folder(_Request(), None)
    assert result["settings"]["music_mode"] is None


async def test_settings_route_rejects_bad_music_mode(route_env):
    with pytest.raises(HTTPException) as excinfo:
        await routes.update_watch_folder_settings(
            _Request({"enabled": True, "music_mode": "loud"}), None
        )
    assert excinfo.value.status_code == 422
    assert route_env.settings == {}  # nothing was persisted


def test_task_field_validator_is_the_settings_validator():
    # One validator, two surfaces: the settings route and the task
    # field must never drift apart.
    assert validate_music_mode("ON") == "on"
    with pytest.raises(ValueError):
        validate_music_mode("sometimes")
