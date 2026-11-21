import json
import subprocess
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from src.api.routes import media
from src.media import transition_preview
from src.media.transition_library import TRANSITIONS

LIST_FIELDS = {
    "name",
    "family",
    "description",
    "duration_default",
    "preview_url",
}


async def test_library_lists_every_entry_sorted_by_family_then_name(client):
    response = await client.get("/transitions/library")

    assert response.status_code == 200
    entries = response.json()
    assert isinstance(entries, list)
    assert len(entries) == len(TRANSITIONS)
    assert all(set(entry) == LIST_FIELDS for entry in entries)
    assert [(entry["family"], entry["name"]) for entry in entries] == sorted(
        (entry["family"], entry["name"]) for entry in entries
    )
    assert all(entry["description"] for entry in entries)
    assert all(entry["duration_default"] == pytest.approx(0.5) for entry in entries)

    sample = next(
        entry for entry in entries if entry["name"] == "iris_soft_center"
    )
    assert sample["family"] == "iris"
    assert sample["preview_url"] == (
        "/transitions/library/iris_soft_center/preview.mp4"
    )


async def test_library_family_filter_and_unknown_family(client):
    filtered = await client.get("/transitions/library?family=glitch")
    assert filtered.status_code == 200
    entries = filtered.json()
    assert len(entries) == 10
    assert {entry["family"] for entry in entries} == {"glitch"}

    missing = await client.get("/transitions/library?family=not-a-family")
    assert missing.status_code == 200
    assert missing.json() == []


async def test_library_preview_unknown_name_is_404(client):
    response = await client.get(
        "/transitions/library/not_a_transition/preview.mp4"
    )
    assert response.status_code == 404


async def test_library_preview_rejects_path_traversal(client):
    response = await client.get(
        "/transitions/library/..%2F..%2Fetc%2Fpasswd/preview.mp4"
    )
    assert response.status_code == 404

    # The route also validates names itself, for callers that do not pass
    # through FastAPI's path matcher.
    with pytest.raises(HTTPException) as exc_info:
        await media.get_transition_library_preview("../Inter")
    assert exc_info.value.status_code == 404


async def test_library_off_switch_hides_list_and_preview(client, monkeypatch):
    monkeypatch.setenv("TRANSITION_LIBRARY_API", "off")

    listing = await client.get("/transitions/library")
    preview = await client.get(
        "/transitions/library/iris_soft_center/preview.mp4"
    )
    assert listing.status_code == 404
    assert preview.status_code == 404


async def test_library_preview_renders_once_then_serves_the_cache(
    client, monkeypatch, tmp_path
):
    monkeypatch.setattr(
        media,
        "get_config",
        lambda: SimpleNamespace(temp_dir=str(tmp_path)),
    )
    monkeypatch.setenv("TRANSITION_LIBRARY_API", "on")

    calls = []
    real_run = transition_preview.run_ffmpeg_command

    def counting_run(command, timeout=900):
        calls.append(list(command))
        return real_run(command, timeout=timeout)

    monkeypatch.setattr(transition_preview, "run_ffmpeg_command", counting_run)

    first = await client.get(
        "/transitions/library/iris_soft_center/preview.mp4"
    )
    second = await client.get(
        "/transitions/library/iris_soft_center/preview.mp4"
    )

    assert first.status_code == 200
    assert first.headers["content-type"] == "video/mp4"
    assert len(first.content) > 1000
    assert second.status_code == 200
    assert second.content == first.content
    assert len(calls) == 1

    cached = tmp_path / "transition_previews" / "iris_soft_center.mp4"
    assert cached.is_file()
    assert cached.stat().st_size == len(first.content)

    probe = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "stream=width,height",
            "-show_entries",
            "format=duration",
            "-of",
            "json",
            str(cached),
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    data = json.loads(probe.stdout)
    stream = data["streams"][0]
    assert (stream["width"], stream["height"]) == (360, 640)
    assert float(data["format"]["duration"]) == pytest.approx(1.2, abs=0.05)
