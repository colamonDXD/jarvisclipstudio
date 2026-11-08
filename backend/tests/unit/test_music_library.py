"""Music library lane: folder-driven library, preview/upload API, the
per-task music override, renderer resolution order and bundle credits.

The library has two roots — bundled ``assets/music`` (read-only in
Docker) and a user folder under the shared temp tree. Tests point both
roots at tmp dirs by patching the root resolvers in
``src.media.music_library``; every consumer (routes, renderer, bundle)
looks the roots up through that module at call time.
"""

import json
import subprocess
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from src.api.routes import music as music_routes
from src.auto_director.edl import build_edl, load_profile
from src.database import get_db
from src.deliverable_bundle import write_deliverable_bundle
from src.media import edl_render, music_library
from src.services.video_service import VideoService


@pytest.fixture
def library(tmp_path, monkeypatch):
    bundled = tmp_path / "assets" / "music"
    bundled.mkdir(parents=True)
    user = tmp_path / "user_music"
    user.mkdir(parents=True)

    def ensure_user():
        user.mkdir(parents=True, exist_ok=True)
        return user

    monkeypatch.setattr(music_library, "bundled_music_dir", lambda: bundled)
    monkeypatch.setattr(music_library, "user_music_dir", lambda: user)
    monkeypatch.setattr(music_library, "ensure_user_music_dir", ensure_user)
    return SimpleNamespace(bundled=bundled, user=user)


def _write(path: Path, payload: bytes = b"fake audio bytes") -> Path:
    path.write_bytes(payload)
    return path


# ------------------------------------------------------------------ listing


def test_listing_covers_both_roots_and_dedupes_bundled_first(library):
    _write(library.bundled / "calm.mp3")
    _write(library.bundled / "shared.mp3")
    _write(library.user / "shared.mp3")  # shadowed by the bundled copy
    _write(library.user / "my song.wav")
    _write(library.user / "notes.txt")  # not audio: never listed
    _write(library.user / ".hidden.mp3")  # dotfiles stay out

    tracks = music_library.list_music_tracks(probe_durations=False)

    assert [(t["filename"], t["source"]) for t in tracks] == [
        ("calm.mp3", "bundled"),
        ("shared.mp3", "bundled"),
        ("my song.wav", "user"),
    ]
    assert tracks[0]["display_name"] == "calm"
    assert tracks[0]["duration_sec"] is None  # probing off


def test_listing_missing_roots_is_empty_not_an_error(tmp_path, monkeypatch):
    monkeypatch.setattr(
        music_library, "bundled_music_dir", lambda: tmp_path / "nope"
    )
    monkeypatch.setattr(
        music_library, "user_music_dir", lambda: tmp_path / "also-nope"
    )
    assert music_library.list_music_tracks() == []


def test_listing_probes_duration_for_real_audio(library):
    real = library.user / "tone.wav"
    subprocess.run(
        [
            "ffmpeg", "-y", "-v", "error", "-f", "lavfi",
            "-i", "sine=frequency=440:duration=2", str(real),
        ],
        check=True,
    )
    tracks = music_library.list_music_tracks()
    assert len(tracks) == 1
    assert tracks[0]["duration_sec"] == pytest.approx(2.0, abs=0.2)


def test_find_music_file_rejects_traversal_and_unknown(library):
    _write(library.user / "track.mp3")
    assert music_library.find_music_file("track.mp3") == (
        library.user / "track.mp3",
        "user",
    )
    assert music_library.find_music_file("../track.mp3") is None
    assert music_library.find_music_file("..") is None
    assert music_library.find_music_file("missing.mp3") is None


# ------------------------------------------------------------------- routes


@pytest.fixture
async def client(library, monkeypatch):
    monkeypatch.setattr(
        music_routes,
        "_get_authenticated_user_id",
        AsyncMock(return_value="user-1"),
    )
    app = FastAPI()
    app.include_router(music_routes.router)
    app.dependency_overrides[get_db] = lambda: SimpleNamespace()
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as http:
        yield http


async def test_library_route_returns_tracks(client, library):
    _write(library.bundled / "calm.mp3")
    response = await client.get("/music/library")
    assert response.status_code == 200
    tracks = response.json()["tracks"]
    assert [(t["filename"], t["source"]) for t in tracks] == [
        ("calm.mp3", "bundled")
    ]


async def test_file_route_serves_both_roots(client, library):
    _write(library.bundled / "bundled.mp3", b"bundled bytes")
    _write(library.user / "mine.mp3", b"user bytes")
    bundled = await client.get("/music/file/bundled.mp3")
    assert bundled.status_code == 200
    assert bundled.content == b"bundled bytes"
    assert bundled.headers["content-type"].startswith("audio/")
    user = await client.get("/music/file/mine.mp3")
    assert user.status_code == 200
    assert user.content == b"user bytes"


async def test_file_route_rejects_traversal_and_unknown(client, library):
    _write(library.user / "track.mp3")
    traversal = await client.get("/music/file/..%2Ftrack.mp3")
    assert traversal.status_code in {400, 404}
    dotdot = await client.get("/music/file/..")
    assert dotdot.status_code in {400, 404}
    missing = await client.get("/music/file/nope.mp3")
    assert missing.status_code == 404


async def test_upload_saves_and_lists_track(client, library):
    response = await client.post(
        "/music/upload",
        files={"file": ("My Song!.mp3", b"mp3 bytes", "audio/mpeg")},
    )
    assert response.status_code == 201
    entry = response.json()
    assert entry["source"] == "user"
    assert entry["filename"].endswith(".mp3")
    assert (library.user / entry["filename"]).is_file()
    listing = await client.get("/music/library")
    assert entry["filename"] in {
        t["filename"] for t in listing.json()["tracks"]
    }


async def test_upload_rejects_bad_extension(client, library):
    response = await client.post(
        "/music/upload",
        files={"file": ("notes.txt", b"text", "text/plain")},
    )
    assert response.status_code == 400
    assert list(library.user.iterdir()) == []


async def test_upload_rejects_oversize(client, library):
    big = b"x" * (music_library.MAX_UPLOAD_BYTES + 1)
    response = await client.post(
        "/music/upload",
        files={"file": ("big.mp3", big, "audio/mpeg")},
    )
    assert response.status_code == 413
    assert list(library.user.iterdir()) == []  # no partial file left behind


async def test_upload_collision_gets_numeric_suffix(client, library):
    first = await client.post(
        "/music/upload",
        files={"file": ("song.mp3", b"one", "audio/mpeg")},
    )
    second = await client.post(
        "/music/upload",
        files={"file": ("song.mp3", b"two", "audio/mpeg")},
    )
    assert first.status_code == second.status_code == 201
    assert first.json()["filename"] == "song.mp3"
    assert second.json()["filename"] == "song_2.mp3"
    assert (library.user / "song.mp3").read_bytes() == b"one"
    assert (library.user / "song_2.mp3").read_bytes() == b"two"


# ------------------------------------------------------- override validation


def test_validate_music_override(library):
    _write(library.user / "track.mp3")
    assert music_library.validate_music_override(None) is None
    assert music_library.validate_music_override("track.mp3") == "track.mp3"
    with pytest.raises(ValueError):
        music_library.validate_music_override("missing.mp3")
    with pytest.raises(ValueError):
        music_library.validate_music_override("../track.mp3")
    with pytest.raises(ValueError):
        music_library.validate_music_override(42)


# --------------------------------------------------------- override plumbing


def _segment():
    return {"start_time": "00:00", "end_time": "00:30", "text": "hello world"}


def _words():
    return [
        {"word": "hello", "start": 0.0, "end": 0.4},
        {"word": "world", "start": 0.5, "end": 0.9},
    ]


def test_override_on_music_profile_keeps_profile_volume():
    # gaming carries music at volume 0.18 / ducking -12: the override
    # swaps the track only, never the profile's mix settings.
    edl = VideoService.build_pipeline_edl(
        _segment(), _words(), classifier_niche="gaming",
        music_override="user pick.mp3",
    )
    assert edl["audio"]["music_track"] == "user pick.mp3"
    assert edl["audio"]["music_volume"] == pytest.approx(0.18)
    assert edl["audio"]["ducking_db"] == pytest.approx(-12.0)


def test_override_on_silent_profile_gets_audible_defaults():
    # podcast_news has music_volume 0.0 / ducking 0.0: without defaults
    # the user's chosen track would render silent.
    edl = VideoService.build_pipeline_edl(
        _segment(), _words(), classifier_niche="podcast_news",
        music_override="user pick.mp3",
    )
    assert edl["audio"]["music_track"] == "user pick.mp3"
    assert edl["audio"]["music_volume"] == pytest.approx(0.25)
    assert edl["audio"]["ducking_db"] == pytest.approx(-12.0)


def test_no_override_matches_patch10_edl_exactly():
    segment = _segment()
    words = _words()
    profile = load_profile("travel_vlog")
    expected = build_edl(
        {"id": "clip", "start": 0.0, "end": 30.0, "duration": 30.0},
        words,
        profile,
        [],
    )
    actual = VideoService.build_pipeline_edl(
        segment, words, classifier_niche="travel_vlog"
    )
    assert actual == expected
    assert "music_track" not in actual["audio"]


# --------------------------------------------------------- render resolution


def _music_edl(track: str):
    return {
        "duration": 10.0,
        "segments": [{"start": 0.0, "end": 10.0}],
        "audio": {
            "music_track": track,
            "music_volume": 0.5,
            "ducking_db": -12.0,
        },
    }


def _plan_inputs(plan):
    return plan[0] if plan else []


def test_resolution_bundled_wins_over_user(library, tmp_path):
    assets = tmp_path / "render-assets"
    (assets / "music").mkdir(parents=True)
    _write(assets / "music" / "shared.mp3")
    _write(library.user / "shared.mp3")
    plan = edl_render.build_edl_audio_graph(
        _music_edl("shared.mp3"), 1, assets_dir=assets
    )
    assert str(assets / "music" / "shared.mp3") in _plan_inputs(plan)


def test_resolution_falls_back_to_user_folder(library, tmp_path):
    assets = tmp_path / "render-assets"
    (assets / "music").mkdir(parents=True)
    _write(library.user / "only mine.mp3")
    plan = edl_render.build_edl_audio_graph(
        _music_edl("only mine.mp3"), 1, assets_dir=assets
    )
    assert str(library.user / "only mine.mp3") in _plan_inputs(plan)


def test_resolution_by_mood_stem_reaches_user_folder(library, tmp_path):
    assets = tmp_path / "render-assets"
    (assets / "music").mkdir(parents=True)
    _write(library.user / "calm.wav")
    edl = _music_edl("calm")  # a mood stem, not a filename
    plan = edl_render.build_edl_audio_graph(edl, 1, assets_dir=assets)
    assert str(library.user / "calm.wav") in _plan_inputs(plan)


# ------------------------------------------------------------------- credits


def _fake_ffmpeg_ok(monkeypatch):
    class Result:
        returncode = 0

    def fake_ffmpeg(command, timeout):
        Path(command[-1]).write_bytes(b"jpeg cover bytes")
        return Result()

    monkeypatch.setattr(
        "src.deliverable_bundle.run_ffmpeg_command", fake_ffmpeg
    )


def _bundle_post(tmp_path, edl):
    clip = tmp_path / "finished.mp4"
    clip.write_bytes(b"finished video bytes")
    bundle = write_deliverable_bundle(
        clip, edl, {"title": "A title"}, "A title",
        profile_name="podcast_news",
    )
    return (bundle / "post.txt").read_text(encoding="utf-8")


def test_credit_line_appended_for_manifest_track(library, monkeypatch, tmp_path):
    _fake_ffmpeg_ok(monkeypatch)
    (library.user / "CREDITS.json").write_text(
        json.dumps({"user pick.mp3": "Sunset Drive by Kevin MacLeod"}),
        encoding="utf-8",
    )
    edl = {"audio": {"music_track": "user pick.mp3"}}
    post = _bundle_post(tmp_path, edl)
    assert post.endswith("Music: Sunset Drive by Kevin MacLeod\n")
    assert post.startswith("A title\n")


def test_no_manifest_leaves_post_byte_identical(library, monkeypatch, tmp_path):
    _fake_ffmpeg_ok(monkeypatch)
    edl = {"audio": {"music_track": "user pick.mp3"}}
    post = _bundle_post(tmp_path, edl)
    assert post == "A title\n"


def test_mood_only_edl_claims_no_credit(library, monkeypatch, tmp_path):
    _fake_ffmpeg_ok(monkeypatch)
    (library.bundled / "CREDITS.json").write_text(
        json.dumps({"calm.mp3": "Calm by Someone"}), encoding="utf-8"
    )
    # Mood-resolved music has no exact music_track: no credit is claimed.
    edl = {"audio": {"music_mood": "calm"}}
    post = _bundle_post(tmp_path, edl)
    assert post == "A title\n"
