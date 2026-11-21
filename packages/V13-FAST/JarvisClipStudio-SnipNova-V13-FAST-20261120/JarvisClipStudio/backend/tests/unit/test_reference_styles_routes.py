"""Reference style routes: create (upload + URL), list, delete.

The upload test runs the real Lane H analyser on a tiny synthetic video
and then proves the stored profile drives the render path: it loads
through ``load_profile`` and ``build_edl`` accepts it. The URL path is
wired to the same storage/analysis code; its download step is stubbed
here (no network in the suite) and its failure mode is asserted to be a
specific, honest 400.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from src.api.routes import reference_styles as routes
from src.auto_director import user_profiles
from src.auto_director.edl import build_edl, load_profile
from src.database import get_db

WORDS = [
    {"word": "hello", "start": 0.0, "end": 0.4},
    {"word": "world", "start": 0.5, "end": 0.9},
    {"word": "this", "start": 1.0, "end": 1.3},
    {"word": "clip", "start": 1.4, "end": 1.8},
]


@pytest.fixture(scope="module")
def reference_video(tmp_path_factory) -> Path:
    """A 4-second synthetic clip: test pattern + a tone, two hard cuts."""
    out = tmp_path_factory.mktemp("refvideo") / "reference.mp4"
    cmd = [
        "ffmpeg", "-v", "error", "-y",
        "-f", "lavfi", "-i", "testsrc2=duration=2:size=320x568:rate=30",
        "-f", "lavfi", "-i", "smptebars=duration=2:size=320x568:rate=30",
        "-f", "lavfi", "-i", "sine=frequency=440:duration=4",
        "-filter_complex", "[0:v][1:v]concat=n=2:v=1:a=0[v]",
        "-map", "[v]", "-map", "2:a",
        "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac",
        str(out),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    return out


@pytest.fixture
def profiles_dir(tmp_path, monkeypatch):
    folder = tmp_path / "user_profiles"
    folder.mkdir(parents=True)
    monkeypatch.setattr(user_profiles, "user_profiles_dir", lambda: folder)
    return folder


@pytest.fixture
async def client(profiles_dir, monkeypatch):
    monkeypatch.setattr(
        routes,
        "_get_authenticated_user_id",
        AsyncMock(return_value="user-1"),
    )
    app = FastAPI()
    app.include_router(routes.router)
    app.dependency_overrides[get_db] = lambda: SimpleNamespace()
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as http:
        yield http


async def test_upload_creates_loadable_profile_that_builds_edl(
    client, profiles_dir, reference_video
):
    response = await client.post(
        "/reference-styles/upload",
        files={"file": ("reference.mp4", reference_video.read_bytes(), "video/mp4")},
        data={"name": "My Test Style"},
    )
    assert response.status_code == 201, response.text
    summary = response.json()
    assert summary["name"] == "user-my-test-style"
    assert summary["display_name"] == "My Test Style"
    assert summary["base_niche"]  # the analyser picked a stock base
    # The label's honesty fields ride along: margin over the runner-up,
    # the runner-up itself, and whether the pick was a near-tie. The
    # flag must agree with the margin and the documented 0.25 threshold.
    assert isinstance(summary["base_niche_margin"], float)
    assert summary["base_niche_margin"] >= 0.0
    assert isinstance(summary["base_niche_runner_up"], str)
    assert summary["base_niche_runner_up"] != summary["base_niche"]
    assert isinstance(summary["base_niche_close_call"], bool)
    assert summary["base_niche_close_call"] == (
        summary["base_niche_margin"] < 0.25
    )
    assert "cuts_per_min" in summary["headline"]
    assert (profiles_dir / "my-test-style.json").is_file()
    assert (profiles_dir / "my-test-style.measurements.json").is_file()

    # The stored profile drives the render path end to end.
    profile = load_profile("user-my-test-style")
    assert profile["niche"] == "user-my-test-style"
    clip = {"id": "clip", "start": 0, "end": 30, "duration": 30}
    edl = build_edl(clip, WORDS, profile, [5.0])
    assert edl["segments"]

    listing = await client.get("/reference-styles")
    assert [p["name"] for p in listing.json()["profiles"]] == ["user-my-test-style"]
    listed = listing.json()["profiles"][0]
    assert listed["base_niche_margin"] == summary["base_niche_margin"]
    assert listed["base_niche_runner_up"] == summary["base_niche_runner_up"]
    assert listed["base_niche_close_call"] == summary["base_niche_close_call"]

    deleted = await client.delete("/reference-styles/user-my-test-style")
    assert deleted.status_code == 200
    listing = await client.get("/reference-styles")
    assert listing.json()["profiles"] == []
    with pytest.raises(FileNotFoundError):
        load_profile("user-my-test-style")


async def test_recreating_a_style_with_the_same_name_replaces_it(
    client, profiles_dir, reference_video
):
    """One style per name: a second create under the same name updates
    the existing style instead of minting an indistinguishable
    user-<name>-2 copy (the duplicate "funky" entries, Nov 9)."""
    payload = {
        "file": ("reference.mp4", reference_video.read_bytes(), "video/mp4")
    }
    first = await client.post(
        "/reference-styles/upload", files=payload, data={"name": "My Test Style"}
    )
    assert first.status_code == 201, first.text
    second = await client.post(
        "/reference-styles/upload", files=payload, data={"name": "My Test Style"}
    )
    assert second.status_code == 201, second.text
    assert second.json()["name"] == "user-my-test-style"
    assert not (profiles_dir / "my-test-style-2.json").exists()

    listing = await client.get("/reference-styles")
    assert [p["name"] for p in listing.json()["profiles"]] == ["user-my-test-style"]


async def test_upload_rejects_bad_extension(client):
    response = await client.post(
        "/reference-styles/upload",
        files={"file": ("notes.txt", b"text", "text/plain")},
        data={"name": "Notes"},
    )
    assert response.status_code == 400
    assert "Unsupported video file type" in response.json()["detail"]


async def test_upload_rejects_unreadable_video(client):
    response = await client.post(
        "/reference-styles/upload",
        files={"file": ("fake.mp4", b"definitely not a video", "video/mp4")},
        data={"name": "Fake"},
    )
    assert response.status_code == 400
    assert "Could not analyse" in response.json()["detail"]


async def test_upload_requires_a_name(client, reference_video):
    response = await client.post(
        "/reference-styles/upload",
        files={"file": ("reference.mp4", reference_video.read_bytes(), "video/mp4")},
        data={"name": "  "},
    )
    assert response.status_code == 400
    assert "name" in response.json()["detail"].lower()


async def test_from_url_rejects_bad_url(client):
    response = await client.post(
        "/reference-styles/from-url",
        json={"url": "not a url", "name": "Web Style"},
    )
    assert response.status_code == 400
    assert "valid http(s) URL" in response.json()["detail"]


async def test_from_url_download_failure_is_specific(client, monkeypatch):
    def failing_download(url, dest_dir):
        raise RuntimeError("ERROR: [youtube] abc: Video unavailable")

    monkeypatch.setattr(routes, "_download_reference_video", failing_download)
    response = await client.post(
        "/reference-styles/from-url",
        json={"url": "https://www.youtube.com/watch?v=abc", "name": "Web Style"},
    )
    assert response.status_code == 400
    detail = response.json()["detail"]
    assert "Could not download that URL" in detail
    assert "Video unavailable" in detail


async def test_from_url_success_uses_the_same_pipeline(
    client, profiles_dir, reference_video, monkeypatch
):
    def fake_download(url, dest_dir):
        target = dest_dir / "reference.mp4"
        target.write_bytes(reference_video.read_bytes())
        return target

    monkeypatch.setattr(routes, "_download_reference_video", fake_download)
    response = await client.post(
        "/reference-styles/from-url",
        json={"url": "https://example.com/watch?v=1", "name": "Web Style"},
    )
    assert response.status_code == 201, response.text
    assert response.json()["name"] == "user-web-style"
    assert load_profile("user-web-style")["niche"] == "user-web-style"


async def test_delete_guards(client):
    stock = await client.delete("/reference-styles/gaming")
    assert stock.status_code == 400
    missing = await client.delete("/reference-styles/user-ghost")
    assert missing.status_code == 404
