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
    bundle = write_deliverable_bundle(
        clip,
        {"zoom_keyframes": [{"type": "punch_in", "time": 4.2}]},
        {"title": "Stored title", "description": "Stored description", "hashtags": ["clips", "video"]},
        "Hook title",
    )

    assert {path.name for path in bundle.iterdir()} == {"clip.mp4", "cover.jpg", "post.txt"}
    assert (bundle / "clip.mp4").stat().st_size > 5
    assert (bundle / "cover.jpg").stat().st_size > 5
    assert "Stored description" in (bundle / "post.txt").read_text()
    assert "#clips #video" in (bundle / "post.txt").read_text()


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
