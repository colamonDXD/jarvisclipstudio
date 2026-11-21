"""Scored cover-frame selection for deliverable bundles.

The bundle cover used to be one fixed frame: the EDL's peak timestamp,
or the very first frame when the EDL carried no peak marker. These
tests pin the replacement contract:

- candidates are scored on face presence, sharpness and exposure;
- a candidate only replaces the historical choice when it is *clearly*
  better — anything else keeps today's exact behaviour;
- every failure mode (undecodable file, no candidates, sampler error)
  falls back to the historical timestamp, never raises.
"""

import shutil
import subprocess
from pathlib import Path

import cv2
import numpy as np
import pytest

from src.media.cover_selection import (
    CoverCandidate,
    _sample_times,
    choose_cover_timestamp,
    frame_candidate,
    pick_cover,
)


def _candidate(timestamp, *, face=False, area=0.0, sharp=100.0, luma=120.0, ok=True):
    return CoverCandidate(
        timestamp=timestamp,
        has_face=face,
        face_area_frac=area,
        sharpness=sharp,
        mean_luma=luma,
        exposure_ok=ok,
    )


# --- pick_cover: ranking ---------------------------------------------------


def test_face_and_sharp_beats_sharp_faceless_beats_blurry():
    default = _candidate(0.0, sharp=10.0)  # blurry, no face (today's pick)
    blurry = _candidate(1.0, sharp=12.0)
    sharp_faceless = _candidate(2.0, sharp=400.0)
    face_sharp = _candidate(3.0, face=True, area=0.08, sharp=300.0)
    winner = pick_cover([default, blurry, sharp_faceless, face_sharp], 0.0)
    assert winner is face_sharp


def test_sharp_faceless_beats_blurry_default():
    default = _candidate(0.0, sharp=10.0)
    sharp = _candidate(2.0, sharp=400.0)
    assert pick_cover([default, sharp], 0.0) is sharp


def test_face_beats_much_sharper_faceless_frame():
    default = _candidate(1.0, sharp=50.0)
    faceless = _candidate(2.0, sharp=900.0)
    faced = _candidate(3.0, face=True, area=0.05, sharp=80.0)
    assert pick_cover([default, faceless, faced], 1.0) is faced


# --- pick_cover: exposure rejection ----------------------------------------


def test_exposure_rejected_candidate_never_wins():
    default = _candidate(0.0, sharp=100.0)
    black_with_face = _candidate(1.0, face=True, area=0.2, sharp=900.0, ok=False)
    plain = _candidate(2.0, sharp=300.0)
    assert pick_cover([default, black_with_face, plain], 0.0) is plain


def test_all_candidates_rejected_keeps_default():
    default = _candidate(0.0, sharp=100.0, ok=False)
    other = _candidate(1.0, face=True, sharp=900.0, ok=False)
    assert pick_cover([default, other], 0.0) is None


def test_rejected_default_is_replaced_by_any_usable_frame():
    default = _candidate(0.0, sharp=0.0, luma=0.0, ok=False)  # black first frame
    usable = _candidate(1.5, sharp=60.0)
    assert pick_cover([default, usable], 0.0) is usable


# --- pick_cover: "clearly better" gate --------------------------------------


def test_marginal_sharpness_gain_keeps_default():
    default = _candidate(4.2, sharp=200.0)
    slightly_sharper = _candidate(6.0, sharp=220.0)  # +10% < 25% margin
    assert pick_cover([default, slightly_sharper], 4.2) is None


def test_clear_sharpness_gain_replaces_default():
    default = _candidate(4.2, sharp=200.0)
    sharper = _candidate(6.0, sharp=300.0)  # +50%
    assert pick_cover([default, sharper], 4.2) is sharper


def test_default_itself_winning_returns_none():
    default = _candidate(4.2, face=True, area=0.1, sharp=500.0)
    other = _candidate(6.0, sharp=100.0)
    assert pick_cover([default, other], 4.2) is None


def test_no_candidates_returns_none():
    assert pick_cover([], 4.2) is None


# --- frame_candidate: metrics on synthetic frames ---------------------------


def _pattern_frame(blur=False):
    rng = np.random.default_rng(7)
    frame = rng.integers(0, 255, size=(200, 200, 3), dtype=np.uint8)
    if blur:
        frame = cv2.GaussianBlur(frame, (0, 0), sigmaX=6)
    return frame


def test_sharpness_orders_sharp_above_blurry():
    sharp = frame_candidate(0.0, _pattern_frame())
    blurry = frame_candidate(0.0, _pattern_frame(blur=True))
    assert sharp.sharpness > blurry.sharpness * 4
    assert sharp.exposure_ok and blurry.exposure_ok


def test_near_black_and_near_white_frames_are_exposure_rejected():
    black = frame_candidate(0.0, np.zeros((100, 100, 3), dtype=np.uint8))
    white = frame_candidate(0.0, np.full((100, 100, 3), 255, dtype=np.uint8))
    assert black.exposure_ok is False
    assert white.exposure_ok is False


def test_face_metrics_come_from_the_injected_detector():
    frame = _pattern_frame()
    candidate = frame_candidate(
        1.0, frame, detect_faces=lambda _f: [(10.0, 10.0, 50.0, 50.0)]
    )
    assert candidate.has_face is True
    assert candidate.face_area_frac == pytest.approx(2500 / 40000, abs=0.01)


def test_detector_failure_means_no_face_not_an_exception():
    def boom(_frame):
        raise RuntimeError("detector exploded")

    candidate = frame_candidate(0.0, _pattern_frame(), detect_faces=boom)
    assert candidate.has_face is False


# --- _sample_times ------------------------------------------------------------


def test_sample_times_cover_the_clip_and_include_the_default():
    times = _sample_times(20.0, 4.2)
    assert len(times) <= 25  # 24 grid candidates + the default
    assert any(abs(t - 4.2) < 0.05 for t in times)
    assert min(times) >= 0.0 and max(times) < 20.0


def test_sample_times_are_capped_on_long_clips():
    times = _sample_times(3600.0, 100.0)
    assert len(times) <= 25


def test_sample_times_short_clip():
    times = _sample_times(1.0, 0.0)
    assert times and all(0.0 <= t < 1.0 for t in times)


# --- choose_cover_timestamp: hard fallback ------------------------------------


def test_choose_falls_back_when_sampler_finds_nothing(tmp_path):
    clip = tmp_path / "clip.mp4"
    clip.write_bytes(b"not a video")
    assert choose_cover_timestamp(clip, 4.2) == 4.2


def test_choose_falls_back_when_sampler_raises(tmp_path):
    clip = tmp_path / "clip.mp4"
    clip.write_bytes(b"bytes")

    def boom(_path, _default):
        raise RuntimeError("sampler exploded")

    assert choose_cover_timestamp(clip, 4.2, sampler=boom) == 4.2


def test_choose_returns_winner_timestamp(tmp_path):
    clip = tmp_path / "clip.mp4"
    clip.write_bytes(b"bytes")
    candidates = [
        _candidate(4.2, sharp=100.0),
        _candidate(9.0, face=True, area=0.1, sharp=250.0),
    ]
    chosen = choose_cover_timestamp(
        clip, 4.2, sampler=lambda _p, _d: candidates
    )
    assert chosen == 9.0


def test_choose_keeps_default_when_nothing_clearly_better(tmp_path):
    clip = tmp_path / "clip.mp4"
    clip.write_bytes(b"bytes")
    candidates = [_candidate(4.2, sharp=100.0), _candidate(9.0, sharp=110.0)]
    chosen = choose_cover_timestamp(
        clip, 4.2, sampler=lambda _p, _d: candidates
    )
    assert chosen == 4.2


# --- bundle plumbing ------------------------------------------------------------


def test_bundle_uses_the_chosen_timestamp(monkeypatch, tmp_path):
    from src import deliverable_bundle

    clip = tmp_path / "finished.mp4"
    clip.write_bytes(b"finished video bytes")

    class Result:
        returncode = 0

    def fake_ffmpeg(command, timeout):
        assert command[command.index("-ss") + 1] == "7.500"
        Path(command[-1]).write_bytes(b"jpeg cover bytes")
        return Result()

    monkeypatch.setattr("src.deliverable_bundle.run_ffmpeg_command", fake_ffmpeg)
    monkeypatch.setattr(
        "src.deliverable_bundle.choose_cover_timestamp", lambda _p, _d: 7.5
    )
    bundle = deliverable_bundle.write_deliverable_bundle(
        clip, {"zoom_keyframes": [{"type": "punch_in", "time": 4.2}]}, None, "Hook"
    )
    assert (bundle / "cover.jpg").is_file()


# --- real video end to end -------------------------------------------------------

ffmpeg_missing = shutil.which("ffmpeg") is None


@pytest.mark.skipif(ffmpeg_missing, reason="ffmpeg not installed")
def test_real_bundle_cover_avoids_the_black_opening(tmp_path, monkeypatch):
    """A clip that opens on black frames: today's fixed choice (t=0)
    yields a black cover; the scorer must pick a real frame instead."""
    from src import deliverable_bundle

    clip = tmp_path / "black_open.mp4"
    subprocess.run(
        [
            "ffmpeg", "-y", "-v", "error",
            "-f", "lavfi", "-i", "color=c=black:size=320x568:rate=30:duration=4",
            "-f", "lavfi", "-i", "testsrc2=size=320x568:rate=30:duration=4",
            "-filter_complex",
            "[0:v]trim=0:1.5[a];[1:v]trim=0:2.5[b];[a][b]concat=n=2:v=1:a=0",
            "-c:v", "libx264", "-pix_fmt", "yuv420p", str(clip),
        ],
        check=True, capture_output=True, timeout=120,
    )

    def cover_mean(bundle_dir):
        image = cv2.imread(str(bundle_dir / "cover.jpg"))
        assert image is not None
        return float(image.mean())

    # Control: historical behaviour (the scorer bypassed, default kept).
    monkeypatch.setattr(
        "src.deliverable_bundle.choose_cover_timestamp", lambda _p, d: d
    )
    control = deliverable_bundle.write_deliverable_bundle(
        clip, {}, None, "", bundle_dir=tmp_path / "control",
    )
    monkeypatch.undo()
    # New behaviour.
    scored = deliverable_bundle.write_deliverable_bundle(
        clip, {}, None, "", bundle_dir=tmp_path / "scored",
    )
    assert cover_mean(control) < 5.0  # the black first frame
    assert cover_mean(scored) > 40.0  # a real test-pattern frame
