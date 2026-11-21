"""End-question overlay: the comment-bait question burned over the
final seconds of a finished clip (media/end_question.py).

Real ffmpeg renders, mirroring test_hook_finish.py: the pass must
change ONLY the final seconds, and with the switch off / a Plain EDL /
no question on the EDL the file stays byte-identical.
"""
from __future__ import annotations

import hashlib
import subprocess

import numpy as np
import pytest

from src.media import end_question as EQ


def _clip(path, seconds=10):
    cmd = ["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i",
           f"testsrc2=s=270x480:d={seconds}",
           "-f", "lavfi", "-i", f"sine=frequency=300:duration={seconds}",
           "-shortest", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", str(path)]
    subprocess.run(cmd, check=True)
    return path


def _frame(path, t):
    raw = subprocess.run(
        ["ffmpeg", "-v", "error", "-ss", str(t), "-i", str(path), "-frames:v", "1",
         "-vf", "format=gray", "-f", "rawvideo", "-"], capture_output=True).stdout
    return np.frombuffer(raw, np.uint8)


def _digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


@pytest.fixture(autouse=True)
def _bait_on(monkeypatch):
    monkeypatch.setenv("COMMENT_BAIT", "on")
    monkeypatch.setattr(
        "src.media.edl_render.check_nvenc_available", lambda: False, raising=False)


def _img(path, t):
    return _frame(path, t).reshape(480, 270).astype(int)


def test_end_question_burns_only_in_the_final_seconds(tmp_path):
    import shutil

    clip = _clip(tmp_path / "clip.mp4")
    pristine = tmp_path / "pristine.mp4"
    shutil.copy2(clip, pristine)
    assert EQ.apply_end_question(clip, {"comment_question": "Was that clutch or just luck?"}) is True
    # Mid-clip: re-encode noise only, no picture change.
    mid_diff = np.abs(_img(clip, 5.0) - _img(pristine, 5.0)).mean()
    assert mid_diff < 2.0
    # Final seconds: the card lands in the bottom strip; the top of the
    # frame (where a face would be) stays clean.
    end_after, end_before = _img(clip, 9.2), _img(pristine, 9.2)
    assert np.abs(end_after[408:] - end_before[408:]).mean() > 5.0
    assert np.abs(end_after[:240] - end_before[:240]).mean() < 2.0
    info = EQ._probe(clip)
    assert abs(info["duration"] - 10.0) < 0.3 and info["audio"]


def test_switch_off_leaves_the_file_byte_identical(tmp_path, monkeypatch):
    clip = _clip(tmp_path / "clip.mp4")
    before = _digest(clip)
    monkeypatch.setenv("COMMENT_BAIT", "off")
    assert EQ.apply_end_question(clip, {"comment_question": "Was that clutch?"}) is False
    assert _digest(clip) == before


def test_plain_and_questionless_and_short_are_untouched(tmp_path):
    clip = _clip(tmp_path / "clip.mp4")
    before = _digest(clip)
    assert EQ.apply_end_question(clip, {"template": "plain",
                                        "comment_question": "Was that clutch?"}) is False
    assert EQ.apply_end_question(clip, {"segments": []}) is False
    assert EQ.apply_end_question(clip, None) is False
    assert _digest(clip) == before
    short = _clip(tmp_path / "short.mp4", seconds=5)
    short_before = _digest(short)
    assert EQ.apply_end_question(short, {"comment_question": "Was that clutch?"}) is False
    assert _digest(short) == short_before


def test_wrap_question_lines():
    assert EQ.wrap_question_lines("Was that clutch?") == ["Was that clutch?"]
    lines = EQ.wrap_question_lines("Did he overreact to that play or was it actually deserved?")
    assert 1 <= len(lines) <= 2
    assert all(len(line) <= EQ.MAX_LINE_CHARS + 8 for line in lines)
    assert EQ.wrap_question_lines("") == []
    long = EQ.wrap_question_lines("word " * 40 + "end?")
    assert len(long) == 2 and long[1].endswith("…")
