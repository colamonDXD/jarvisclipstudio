import shutil
import subprocess

import pytest
from pydantic import ValidationError

from src.editor_document import EditDocument, mapped_overlays, render_document

ASSET = "overlay-" + "a" * 32 + ".png"
ASSET_B = "overlay-" + "b" * 32 + ".jpg"

FOOTAGE = {
    "id": 42,
    "width": 1080,
    "height": 1920,
    "duration": 10,
    "fileUrl": "https://videos.pexels.com/video-files/42/42-hd_1080_1920_25fps.mp4",
    "pageUrl": "https://www.pexels.com/video/42/",
    "thumbnail": "https://images.pexels.com/videos/42/pictures/preview-0.jpg",
    "credit": "Some Filmmaker",
}


def doc(segments=((0, 6),), overlays=(), broll=(), **extra):
    return EditDocument.model_validate(
        {
            "segments": [{"id": f"s{i}", "start": a, "end": b} for i, (a, b) in enumerate(segments)],
            "broll": [
                {"id": f"b{i}", "start": a, "end": b, "offset": o, "footage": FOOTAGE}
                for i, (a, b, o) in enumerate(broll)
            ],
            "overlays": [
                {"id": f"o{i}", "start": a, "end": b, "asset": asset, **kw}
                for i, (a, b, asset, kw) in enumerate(overlays)
            ],
            **extra,
        }
    )


def item(start, end, asset=ASSET, **kw):
    return (start, end, asset, kw)


# --- document model ----------------------------------------------------------


def test_overlay_defaults_centre_the_image():
    loaded = doc(overlays=(item(1, 3),))
    overlay = loaded.overlays[0]
    assert (overlay.x, overlay.y, overlay.scale, overlay.name) == (0.5, 0.5, 0.4, "")


def test_old_drafts_without_overlays_still_load():
    loaded = EditDocument.model_validate({"segments": [{"id": "s", "start": 0, "end": 2}]})
    assert loaded.overlays == []


@pytest.mark.parametrize(
    "asset",
    [
        "../overlay-" + "a" * 32 + ".png",
        "overlay-" + "a" * 32 + ".gif",
        "overlay-" + "a" * 32 + ".png/extra",
        "clean.mp4",
        "https://evil.example/overlay-" + "a" * 32 + ".png",
        "overlay-short.png",
    ],
)
def test_overlay_asset_must_be_an_uploaded_editor_image(asset):
    with pytest.raises(ValidationError):
        doc(overlays=(item(1, 3, asset),))


@pytest.mark.parametrize("change", [{"x": 1.5}, {"y": -0.1}, {"scale": 0.01}, {"scale": 1.5}])
def test_overlay_placement_is_kept_inside_the_frame(change):
    with pytest.raises(ValidationError):
        doc(overlays=(item(1, 3, ASSET, **change),))


def test_overlay_shorter_than_the_minimum_is_rejected():
    with pytest.raises(ValidationError):
        doc(overlays=(item(1, 1.1),))


@pytest.mark.parametrize("start,end", [(1, 1.2), (1.8, 2), (2.8, 3)])
def test_minimum_overlay_duration_is_independent_of_float_rounding(start, end):
    doc(overlays=(item(start, end),)).validate_duration(6)


def test_overlays_are_validated_against_the_clip():
    with pytest.raises(ValueError, match="overlap"):
        doc(overlays=(item(1, 3), item(2, 4, ASSET_B))).validate_duration(6)
    with pytest.raises(ValueError, match="past the original"):
        doc(overlays=(item(5, 7),)).validate_duration(6)
    # Touching items are fine, and overlays may share time with B-roll:
    # they live on a different lane, composited above it.
    doc(overlays=(item(1, 3), item(3, 5, ASSET_B))).validate_duration(6)


def test_overlay_ids_must_be_unique():
    with pytest.raises(ValueError, match="unique"):
        EditDocument.model_validate(
            {
                "segments": [{"id": "s", "start": 0, "end": 6}],
                "overlays": [
                    {"id": "same", "start": 1, "end": 2, "asset": ASSET},
                    {"id": "same", "start": 3, "end": 4, "asset": ASSET_B},
                ],
            }
        ).validate_duration(6)


# --- timeline mapping --------------------------------------------------------


def test_overlay_follows_cuts():
    # Cut out 2-3 s; the overlay spans the cut and becomes two pieces.
    pieces = mapped_overlays(doc(segments=((0, 2), (3, 6)), overlays=(item(1, 4),)))

    assert [(p["start"], p["duration"]) for p in pieces] == [(1, 1), (2, 1)]
    assert all(p["asset"] == ASSET for p in pieces)


def test_overlay_follows_reordered_and_duplicated_segments():
    pieces = mapped_overlays(doc(segments=((3, 6), (0, 3), (0, 3)), overlays=(item(1, 2),)))

    assert [p["start"] for p in pieces] == [4, 7]


def test_cut_out_overlay_disappears():
    assert mapped_overlays(doc(segments=((0, 2), (4, 6)), overlays=(item(2.5, 3.5),))) == []


def test_overlay_piece_keeps_its_placement():
    pieces = mapped_overlays(doc(overlays=(item(1, 3, ASSET, x=0.25, y=0.75, scale=0.6),)))

    assert pieces[0]["x"] == 0.25 and pieces[0]["y"] == 0.75 and pieces[0]["scale"] == 0.6


# --- export --------------------------------------------------------------------


def _solid(path, color, seconds, size):
    subprocess.run(
        ["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", f"color=c={color}:s={size}:r=24:d={seconds}",
         "-c:v", "libx264", "-pix_fmt", "yuv420p", str(path)],
        check=True,
    )


def _image(path, color="lime", size="40x40"):
    # ffmpeg's "green" is #008000; "lime" is the full-intensity primary.
    subprocess.run(
        ["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", f"color=c={color}:s={size}",
         "-frames:v", "1", str(path)],
        check=True,
    )


def _frame(path, at, width=320, height=180):
    raw = subprocess.run(
        ["ffmpeg", "-v", "error", "-ss", f"{at}", "-i", str(path), "-frames:v", "1",
         "-f", "rawvideo", "-pix_fmt", "rgb24", "-"],
        check=True, capture_output=True,
    ).stdout
    return raw


def _pixel(raw, x, y, width=320):
    offset = (y * width + x) * 3
    return raw[offset], raw[offset + 1], raw[offset + 2]


def _capture_commands(monkeypatch):
    commands = []

    def fake_run_render(command, directory, job_id, duration):
        commands.append(command)
        (directory / f".export-{job_id}.mp4").write_bytes(b"rendered")

    monkeypatch.setattr("src.editor_document.run_render", fake_run_render)
    return commands


def _graph(command):
    return command[command.index("-filter_complex") + 1]


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="ffmpeg required")
def test_empty_overlay_lane_keeps_the_legacy_graph(tmp_path, monkeypatch):
    _solid(tmp_path / "clean.mp4", "red", 3, "320x180")
    commands = _capture_commands(monkeypatch)
    legacy = {"segments": [{"id": "s0", "start": 0, "end": 3}],
              "framing": {"aspect": "original"}, "captions": {"enabled": False}}
    plain = EditDocument.model_validate(legacy)
    empty = EditDocument.model_validate({**legacy, "overlays": []})

    render_document(tmp_path, "parity", plain, "tiktok")
    render_document(tmp_path, "parity", empty, "tiktok")

    assert len(commands) == 2
    assert commands[0] == commands[1]
    assert "img" not in _graph(commands[0])


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="ffmpeg required")
def test_empty_overlay_lane_keeps_the_legacy_broll_graph(tmp_path, monkeypatch):
    _solid(tmp_path / "clean.mp4", "red", 6, "320x180")
    footage = tmp_path / "footage.mp4"
    _solid(footage, "blue", 8, "180x320")
    commands = _capture_commands(monkeypatch)
    base = dict(segments=((0, 6),), broll=((1, 4, 0),),
                framing={"aspect": "original"}, captions={"enabled": False})

    render_document(tmp_path, "parity", doc(**base), "tiktok", {"pexels:42/1080x1920": footage})
    render_document(tmp_path, "parity", doc(overlays=(), **base), "tiktok", {"pexels:42/1080x1920": footage})

    assert len(commands) == 2
    assert commands[0] == commands[1]
    assert "withbroll0" in _graph(commands[0]) and "img" not in _graph(commands[0])


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="ffmpeg required")
def test_export_composites_the_overlay_above_broll_inside_its_window(tmp_path):
    _solid(tmp_path / "clean.mp4", "red", 6, "320x180")
    footage = tmp_path / "footage.mp4"
    _solid(footage, "blue", 8, "180x320")
    _image(tmp_path / ASSET)
    draft = doc(
        segments=((0, 6),),
        broll=((1, 4, 0),),
        overlays=(item(2, 3, ASSET, x=0.5, y=0.5, scale=0.25),),
        framing={"aspect": "original"},
        captions={"enabled": False},
    )

    render_document(tmp_path, "d" * 32, draft, "tiktok", {"pexels:42/1080x1920": footage})

    output = tmp_path / f"export-{'d' * 32}.mp4"
    centre_before = _pixel(_frame(output, 1.5), 160, 90)   # B-roll only
    centre_during = _pixel(_frame(output, 2.5), 160, 90)   # overlay over B-roll
    corner_during = _pixel(_frame(output, 2.5), 5, 5)      # outside the image: B-roll
    centre_after = _pixel(_frame(output, 3.5), 160, 90)    # window over, B-roll remains
    plain = _pixel(_frame(output, 5.0), 160, 90)           # neither lane
    assert centre_before[2] > 200 and centre_before[0] < 60
    assert centre_during[1] > 200 and centre_during[0] < 80 and centre_during[2] < 80
    assert corner_during[2] > 200 and corner_during[0] < 60
    assert centre_after[2] > 200 and centre_after[0] < 60
    assert plain[0] > 200 and plain[2] < 60


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="ffmpeg required")
def test_export_composites_the_overlay_without_broll(tmp_path):
    _solid(tmp_path / "clean.mp4", "red", 3, "320x180")
    _image(tmp_path / ASSET)
    draft = doc(
        segments=((0, 3),),
        overlays=(item(1, 2, ASSET),),
        framing={"aspect": "original"},
        captions={"enabled": False},
    )

    render_document(tmp_path, "e" * 32, draft, "tiktok")

    output = tmp_path / f"export-{'e' * 32}.mp4"
    before = _pixel(_frame(output, 0.5), 160, 90)
    during = _pixel(_frame(output, 1.5), 160, 90)
    after = _pixel(_frame(output, 2.5), 160, 90)
    assert before[0] > 200 and during[1] > 200 and after[0] > 200


# --- routes --------------------------------------------------------------------

import base64
from types import SimpleNamespace
from unittest.mock import AsyncMock

from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from src.api.routes import editor
from src.database import get_db
from src.editor_document import atomic_json, editor_dir

PNG_1X1 = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg=="
)


@pytest.fixture
async def client(tmp_path, monkeypatch):
    task = {"id": "task", "user_id": "owner", "status": "completed"}
    clip = {"id": "clip", "task_id": "task", "filename": "clip.mp4", "file_path": str(tmp_path / "clip.mp4")}
    service = SimpleNamespace(
        task_repo=SimpleNamespace(get_task_by_id=AsyncMock(return_value=task)),
        clip_repo=SimpleNamespace(get_clip_by_id=AsyncMock(return_value=clip)),
        config=SimpleNamespace(temp_dir=str(tmp_path)),
        _get_clip_source_ranges=lambda clip: [],
    )
    monkeypatch.setattr(editor, "TaskService", lambda db: service)
    monkeypatch.setattr("src.api.routes.tasks._get_user_id_from_headers", AsyncMock(return_value="owner"))
    app = FastAPI()
    app.include_router(editor.router)
    app.dependency_overrides[get_db] = lambda: SimpleNamespace(close=AsyncMock())
    directory = editor_dir(str(tmp_path), "task", "clip", "clip.mp4")
    draft = doc(segments=((0, 20),)).model_dump()
    atomic_json(directory / "draft.json", {"revision": 0, "document": draft})
    atomic_json(directory / "original.json", draft)
    atomic_json(directory / "asset.json", {"status": "ready", "duration": 20, "width": 1920, "height": 1080, "hasAudio": False})
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as http:
        yield http, directory


async def test_upload_stores_the_image_and_serves_it_back(client):
    http, directory = client
    response = await http.post(
        "/tasks/task/clips/clip/editor/overlays/upload",
        files={"file": ("sticker.png", PNG_1X1, "image/png")},
    )
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["asset"].startswith("overlay-") and payload["asset"].endswith(".png")
    assert (payload["width"], payload["height"]) == (1, 1)
    assert payload["name"] == "sticker.png"
    assert (directory / payload["asset"]).read_bytes() == PNG_1X1

    served = await http.get(f"/tasks/task/clips/clip/editor/media/{payload['asset']}")
    assert served.status_code == 200
    assert served.headers["content-type"] == "image/png"
    assert served.content == PNG_1X1


async def test_upload_rejects_non_images_and_oversize_files(client):
    http, _ = client
    not_image = await http.post(
        "/tasks/task/clips/clip/editor/overlays/upload",
        files={"file": ("notes.txt", b"plain text", "text/plain")},
    )
    assert not_image.status_code == 400
    oversized = await http.post(
        "/tasks/task/clips/clip/editor/overlays/upload",
        files={"file": ("big.png", PNG_1X1 + b"\0" * (8 * 1024 * 1024), "image/png")},
    )
    assert oversized.status_code == 400


async def test_upload_and_media_require_ownership(client, monkeypatch):
    http, directory = client
    (directory / ASSET).write_bytes(PNG_1X1)
    monkeypatch.setattr("src.api.routes.tasks._get_user_id_from_headers", AsyncMock(return_value="outsider"))
    response = await http.post(
        "/tasks/task/clips/clip/editor/overlays/upload",
        files={"file": ("sticker.png", PNG_1X1, "image/png")},
    )
    assert response.status_code == 403
    assert (await http.get(f"/tasks/task/clips/clip/editor/media/{ASSET}")).status_code == 403


async def test_media_rejects_names_that_are_not_uploaded_overlays(client):
    http, directory = client
    (directory / "overlay-notauuid.png").write_bytes(PNG_1X1)
    assert (await http.get("/tasks/task/clips/clip/editor/media/overlay-notauuid.png")).status_code == 404
    assert (await http.get("/tasks/task/clips/clip/editor/media/draft.json")).status_code == 404


async def test_overlays_save_through_the_api(client):
    http, _ = client
    draft = doc(segments=((0, 20),), overlays=(item(1, 3),)).model_dump()
    response = await http.patch("/tasks/task/clips/clip/editor", json={
        "basis": "clip.mp4", "revision": 0, "document": draft,
    })
    assert response.status_code == 200, response.text
    state = (await http.get("/tasks/task/clips/clip/editor")).json()
    assert state["draft"]["document"]["overlays"][0]["asset"] == ASSET
    assert state["draft"]["document"]["overlays"][0]["scale"] == 0.4


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="ffmpeg required")
def test_missing_overlay_upload_is_left_out_not_fatal(tmp_path):
    _solid(tmp_path / "clean.mp4", "red", 3, "320x180")
    draft = doc(
        segments=((0, 3),),
        overlays=(item(1, 2, ASSET),),
        framing={"aspect": "original"},
        captions={"enabled": False},
    )

    render_document(tmp_path, "f" * 32, draft, "tiktok")

    output = tmp_path / f"export-{'f' * 32}.mp4"
    assert _pixel(_frame(output, 1.5), 160, 90)[0] > 200
