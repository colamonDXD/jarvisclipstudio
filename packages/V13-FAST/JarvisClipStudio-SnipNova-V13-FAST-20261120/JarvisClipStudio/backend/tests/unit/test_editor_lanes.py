import json
import shutil
import subprocess

import pytest
from pydantic import ValidationError

from src.editor_document import (
    EditDocument,
    LaneState,
    lane_hidden,
    lane_locked,
    lane_state,
    render_document,
    save_document,
    write_ass,
)

LANES = ["video", "broll", "overlays", "captions", "hook", "audio"]


def doc(**extra):
    return EditDocument.model_validate(
        {"segments": [{"id": "s0", "start": 0, "end": 6}], **extra}
    )


# --- document model ----------------------------------------------------------


def test_old_drafts_without_lanes_load_with_every_lane_open():
    loaded = doc()
    assert loaded.lanes == {}
    for lane in LANES:
        assert lane_state(loaded, lane) == LaneState()
        assert lane_hidden(loaded, lane) is False
        assert lane_locked(loaded, lane) is False


def test_lane_state_round_trips_through_dump_and_validate():
    loaded = doc(lanes={"broll": {"hidden": True}, "audio": {"locked": True}})
    dumped = loaded.model_dump()
    assert dumped["lanes"] == {
        "broll": {"hidden": True, "locked": False},
        "audio": {"hidden": False, "locked": True},
    }
    again = EditDocument.model_validate(json.loads(json.dumps(dumped)))
    assert again.lanes == loaded.lanes
    assert lane_hidden(again, "broll") is True
    assert lane_locked(again, "audio") is True
    assert lane_locked(again, "broll") is False


def test_a_partial_lane_entry_fills_the_other_control_with_its_default():
    loaded = doc(lanes={"hook": {"locked": True}})
    assert lane_state(loaded, "hook") == LaneState(hidden=False, locked=True)


def test_an_unknown_lane_is_rejected():
    with pytest.raises(ValidationError):
        doc(lanes={"subtitles": {"hidden": True}})


def test_lane_controls_survive_a_save_load_round_trip(tmp_path):
    loaded = doc(lanes={"overlays": {"hidden": True, "locked": True}})
    save_document(tmp_path, loaded, 0, 6)
    stored = json.loads((tmp_path / "draft.json").read_text())
    restored = EditDocument.model_validate(stored["document"])
    assert restored.lanes == loaded.lanes


def test_saving_a_laneless_draft_stores_the_empty_default(tmp_path):
    save_document(tmp_path, doc(), 0, 6)
    stored = json.loads((tmp_path / "draft.json").read_text())
    assert stored["document"]["lanes"] == {}
    restored = EditDocument.model_validate(stored["document"])
    assert all(not lane_hidden(restored, lane) and not lane_locked(restored, lane) for lane in LANES)


# --- export ------------------------------------------------------------------

ASSET = "overlay-" + "a" * 32 + ".png"

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


def full_doc(**extra):
    return EditDocument.model_validate(
        {
            "segments": [{"id": "s0", "start": 0, "end": 6}],
            "framing": {"aspect": "original"},
            "captions": {"enabled": False},
            "hookTitle": "A hook",
            "broll": [{"id": "b0", "start": 1, "end": 4, "offset": 0, "footage": FOOTAGE}],
            "overlays": [{"id": "o0", "start": 2, "end": 3, "asset": ASSET}],
            **extra,
        }
    )


def _solid(path, color, seconds, size):
    subprocess.run(
        ["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", f"color=c={color}:s={size}:r=24:d={seconds}",
         "-c:v", "libx264", "-pix_fmt", "yuv420p", str(path)],
        check=True,
    )


def _image(path, color="lime", size="40x40"):
    subprocess.run(
        ["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", f"color=c={color}:s={size}",
         "-frames:v", "1", str(path)],
        check=True,
    )


def _frame(path, at, width=320, height=180):
    return subprocess.run(
        ["ffmpeg", "-v", "error", "-ss", f"{at}", "-i", str(path), "-frames:v", "1",
         "-f", "rawvideo", "-pix_fmt", "rgb24", "-"],
        check=True, capture_output=True,
    ).stdout


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


def _media(tmp_path):
    _solid(tmp_path / "clean.mp4", "red", 6, "320x180")
    footage = tmp_path / "footage.mp4"
    _solid(footage, "blue", 8, "180x320")
    _image(tmp_path / ASSET)
    return {"pexels:42/1080x1920": footage}


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="ffmpeg required")
def test_default_and_locked_lanes_keep_the_base_graph(tmp_path, monkeypatch):
    files = _media(tmp_path)
    commands = _capture_commands(monkeypatch)
    render_document(tmp_path, "parity", full_doc(), "tiktok", files)
    render_document(
        tmp_path, "parity",
        full_doc(lanes={lane: {"hidden": False, "locked": False} for lane in LANES}),
        "tiktok", files,
    )
    render_document(
        tmp_path, "parity",
        full_doc(lanes={lane: {"locked": True} for lane in LANES}),
        "tiktok", files,
    )
    assert len(commands) == 3
    assert commands[0] == commands[1] == commands[2]
    assert "withbroll0" in _graph(commands[0]) and "img" in _graph(commands[0])


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="ffmpeg required")
def test_a_hidden_broll_lane_renders_like_no_broll(tmp_path, monkeypatch):
    files = _media(tmp_path)
    commands = _capture_commands(monkeypatch)
    render_document(tmp_path, "parity", full_doc(lanes={"broll": {"hidden": True}}), "tiktok", files)
    bare = full_doc()
    bare = bare.model_copy(update={"broll": []})
    render_document(tmp_path, "parity", bare, "tiktok", files)
    assert len(commands) == 2
    assert commands[0] == commands[1]
    assert "withbroll" not in _graph(commands[0])


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="ffmpeg required")
def test_a_hidden_overlay_lane_renders_like_no_overlays(tmp_path, monkeypatch):
    files = _media(tmp_path)
    commands = _capture_commands(monkeypatch)
    render_document(tmp_path, "parity", full_doc(lanes={"overlays": {"hidden": True}}), "tiktok", files)
    bare = full_doc().model_copy(update={"overlays": []})
    render_document(tmp_path, "parity", bare, "tiktok", files)
    assert len(commands) == 2
    assert commands[0] == commands[1]
    assert "img" not in _graph(commands[0]) and "withbroll0" in _graph(commands[0])


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="ffmpeg required")
def test_hidden_lanes_are_absent_from_the_rendered_frames(tmp_path):
    files = _media(tmp_path)

    def centre(job, draft, at):
        render_document(tmp_path, job, draft, "tiktok", files)
        return _pixel(_frame(tmp_path / f"export-{job}.mp4", at), 160, 90)

    # Control: B-roll under the overlay inside the overlay's window.
    during = centre("a" * 32, full_doc(), 2.5)
    assert during[1] > 200 and during[0] < 80 and during[2] < 80  # lime overlay
    # Overlay hidden: the B-roll shows through where the image would have been.
    during = centre("b" * 32, full_doc(lanes={"overlays": {"hidden": True}}), 2.5)
    assert during[2] > 200 and during[0] < 60  # blue B-roll
    # B-roll hidden: the overlay sits on the base picture, nothing around it.
    hidden = full_doc(lanes={"broll": {"hidden": True}})
    during = centre("c" * 32, hidden, 2.5)
    assert during[1] > 200 and during[0] < 80 and during[2] < 80  # lime overlay
    before = centre("c" * 32, hidden, 1.5)
    assert before[0] > 200 and before[2] < 60  # red base, no B-roll
    # Both hidden: plain base picture in the window.
    during = centre(
        "d" * 32,
        full_doc(lanes={"broll": {"hidden": True}, "overlays": {"hidden": True}}),
        2.5,
    )
    assert during[0] > 200 and during[2] < 60  # red base


def test_a_hidden_captions_lane_leaves_captions_out_of_the_subtitles(tmp_path):
    base = {
        "segments": [{"id": "s0", "start": 0, "end": 6}],
        "words": [{"id": "w0", "start": 0, "end": 1, "text": "captionword", "highlight": False}],
        "hookTitle": "HOOKTITLE",
    }
    write_ass(tmp_path / "visible.ass", EditDocument.model_validate(base), 320, 180)
    write_ass(
        tmp_path / "hidden.ass",
        EditDocument.model_validate({**base, "lanes": {"captions": {"hidden": True}}}),
        320, 180,
    )
    enabled = EditDocument.model_validate(base)
    disabled = enabled.model_copy(
        update={"captions": enabled.captions.model_copy(update={"enabled": False})}
    )
    write_ass(tmp_path / "disabled.ass", disabled, 320, 180)
    shown = (tmp_path / "visible.ass").read_text().lower()
    withheld = (tmp_path / "hidden.ass").read_text().lower()
    off = (tmp_path / "disabled.ass").read_text().lower()
    assert "captionword" in shown
    # A hidden captions lane must export exactly like captions switched
    # off: no caption dialogue, the hook title still present.
    assert "captionword" not in withheld and "hooktitle" in withheld
    dialogues = lambda text: [
        line for line in text.splitlines() if line.startswith("dialogue:")
    ]
    assert dialogues(withheld) == dialogues(off)


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="ffmpeg required")
def test_a_hidden_audio_lane_renders_like_muted(tmp_path, monkeypatch):
    subprocess.run(
        [
            "ffmpeg", "-v", "error", "-y",
            "-f", "lavfi", "-i", "color=c=red:s=320x180:r=30:d=2",
            "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000", "-t", "2",
            "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac",
            str(tmp_path / "clean.mp4"),
        ],
        check=True,
    )
    commands = _capture_commands(monkeypatch)
    base = {
        "segments": [{"id": "s0", "start": 0, "end": 2}],
        "framing": {"aspect": "original"},
    }
    render_document(
        tmp_path, "parity",
        EditDocument.model_validate({**base, "lanes": {"audio": {"hidden": True}}}),
        "tiktok",
    )
    render_document(
        tmp_path, "parity",
        EditDocument.model_validate({**base, "muted": True}),
        "tiktok",
    )
    render_document(tmp_path, "parity", EditDocument.model_validate(base), "tiktok")
    assert len(commands) == 3
    assert commands[0] == commands[1]
    assert "volume=0" in _graph(commands[0])
    assert commands[2] != commands[0]


def test_a_hidden_hook_leaves_the_title_out_of_the_subtitles(tmp_path):
    base = {
        "segments": [{"id": "s0", "start": 0, "end": 6}],
        "words": [{"id": "w0", "start": 0, "end": 1, "text": "captionword", "highlight": False}],
        "hookTitle": "HOOKTITLE",
    }
    visible = EditDocument.model_validate(base)
    hidden = EditDocument.model_validate({**base, "lanes": {"hook": {"hidden": True}}})
    write_ass(tmp_path / "visible.ass", visible, 320, 180)
    write_ass(tmp_path / "hidden.ass", hidden, 320, 180)
    shown = (tmp_path / "visible.ass").read_text().lower()
    withheld = (tmp_path / "hidden.ass").read_text().lower()
    assert "hooktitle" in shown and "captionword" in shown
    assert "hooktitle" not in withheld and "captionword" in withheld
    # The draft itself is untouched by hiding.
    assert hidden.hookTitle == "HOOKTITLE"
