import json
from pathlib import Path

from src.deliverable_bundle import write_deliverable_bundle


def test_writes_publishable_bundle_from_finished_clip(monkeypatch, tmp_path):
    clip = tmp_path / "finished.mp4"
    clip.write_bytes(b"finished video bytes")

    class Result:
        returncode = 0

    def fake_ffmpeg(command, timeout):
        assert command[command.index("-ss") + 1] == "4.200"
        output = Path(command[-1])
        output.write_bytes(b"jpeg cover bytes")
        return Result()

    monkeypatch.setattr("src.deliverable_bundle.run_ffmpeg_command", fake_ffmpeg)
    edl = {
        "zoom_keyframes": [{"type": "punch_in", "time": 4.2}],
        "overlays": [
            {
                "type": "reference_image",
                "source": "open_web",
                "url": "https://images.example/tokyo.jpg",
                "local_path": "",
            },
            {"type": "info_card", "text": "40%"},
        ],
        "broll": [
            {
                "source": "pexels",
                "url": "https://video.example/city.mp4",
                "local_path": "",
                "query": "city street",
            }
        ],
    }
    bundle = write_deliverable_bundle(
        clip,
        edl,
        {"title": "Stored title", "description": "Stored description", "hashtags": ["clips", "video"]},
        "Hook title",
        profile_name="travel_vlog",
    )

    assert {path.name for path in bundle.iterdir()} == {
        "clip.mp4",
        "cover.jpg",
        "post.txt",
        "edl.json",
        "profile.txt",
        "image_sources.json",
    }
    assert (bundle / "clip.mp4").stat().st_size > 5
    assert (bundle / "cover.jpg").stat().st_size > 5
    assert "Stored description" in (bundle / "post.txt").read_text()
    assert "#clips #video" in (bundle / "post.txt").read_text()
    assert json.loads((bundle / "edl.json").read_text()) == edl
    assert (bundle / "profile.txt").read_text() == "travel_vlog\n"
    sources = json.loads((bundle / "image_sources.json").read_text())
    assert [source["kind"] for source in sources] == ["reference_image", "broll"]
    assert sources[0]["source"] == "open_web"
    assert sources[0]["url"] == "https://images.example/tokyo.jpg"
    assert sources[1]["source"] == "pexels"
    assert sources[1]["query"] == "city street"


def test_missing_llm_output_writes_title_only_post(monkeypatch, tmp_path):
    clip = tmp_path / "finished.mp4"
    clip.write_bytes(b"finished video bytes")

    class Result:
        returncode = 0

    def fake_ffmpeg(command, timeout):
        Path(command[-1]).write_bytes(b"jpeg cover bytes")
        return Result()

    monkeypatch.setattr("src.deliverable_bundle.run_ffmpeg_command", fake_ffmpeg)
    bundle = write_deliverable_bundle(clip, {}, None, "Fallback hook")
    assert (bundle / "post.txt").read_text() == "Fallback hook\n"
    assert json.loads((bundle / "image_sources.json").read_text()) == []


def _stub_cover(monkeypatch):
    class Result:
        returncode = 0

    def fake_ffmpeg(command, timeout):
        Path(command[-1]).write_bytes(b"jpeg cover bytes")
        return Result()

    monkeypatch.setattr("src.deliverable_bundle.run_ffmpeg_command", fake_ffmpeg)


def test_bundle_clip_is_a_hardlink_when_the_filesystem_allows(monkeypatch, tmp_path):
    """F-P2: the bundle's clip.mp4 shares the finished clip's inode —
    no second full copy on disk."""
    import os

    clip = tmp_path / "finished.mp4"
    clip.write_bytes(b"finished video bytes" * 64)
    _stub_cover(monkeypatch)

    bundle = write_deliverable_bundle(clip, {}, None, "Hook")

    bundled = bundle / "clip.mp4"
    assert os.stat(bundled).st_ino == os.stat(clip).st_ino
    assert bundled.read_bytes() == clip.read_bytes()


def test_deleting_the_library_clip_keeps_the_bundle_copy(monkeypatch, tmp_path):
    """Hardlink semantics: unlinking one name never destroys the other."""
    clip = tmp_path / "finished.mp4"
    payload = b"finished video bytes" * 64
    clip.write_bytes(payload)
    _stub_cover(monkeypatch)

    bundle = write_deliverable_bundle(clip, {}, None, "Hook")
    clip.unlink()

    assert (bundle / "clip.mp4").read_bytes() == payload


def test_bundle_falls_back_to_a_copy_when_linking_fails(monkeypatch, tmp_path):
    """Cross-device / no-hardlink filesystems still get a correct bundle."""
    import os

    clip = tmp_path / "finished.mp4"
    payload = b"finished video bytes" * 64
    clip.write_bytes(payload)
    _stub_cover(monkeypatch)

    def no_link(src, dst):
        raise OSError("cross-device link")

    monkeypatch.setattr("src.deliverable_bundle.os.link", no_link)
    bundle = write_deliverable_bundle(clip, {}, None, "Hook")

    bundled = bundle / "clip.mp4"
    assert bundled.read_bytes() == payload
    assert os.stat(bundled).st_ino != os.stat(clip).st_ino
