"""Per-clip QC checks over synthetic rendered clips.

Every fixture is a real file produced by ffmpeg (tone + test pattern, black,
frozen, silent, wrong-size, music-bed variants) and every assertion runs the
real check functions against it — the check logic itself is never mocked.
"""

import hashlib
import subprocess
from pathlib import Path

import pytest

from src.media.clip_qc import ClipQcContext, run_clip_qc


def _ffmpeg(*args: str) -> None:
    subprocess.run(
        ["ffmpeg", "-v", "error", "-y", *args],
        check=True,
        capture_output=True,
        timeout=300,
    )


_TONE_SOURCE = "sine=frequency=440:sample_rate=44100"
_SILENT_SOURCE = "anullsrc=sample_rate=44100"
# Speech-like bursts: 0.6s on, 0.9s off, ~-13 dBFS while on.
_BURST_SOURCE = (
    "aevalsrc=0.3*sin(2*PI*880*t)*(lt(mod(t\\,1.5)\\,0.6)):s=44100"
)
_BED_SOURCE = "sine=frequency=196:sample_rate=44100"


def _make_clip(
    directory: Path,
    name: str,
    video_source: str,
    audio_sources: list[str],
    seconds: float,
    audio_filter: str | None = None,
) -> Path:
    """Build one synthetic clip from lavfi sources.

    With ``audio_filter`` the audio inputs are routed through it and its
    ``[a]`` output is mapped; otherwise the single audio input is mapped
    directly.
    """
    path = directory / name
    args = ["-f", "lavfi", "-i", video_source]
    for source in audio_sources:
        args += ["-f", "lavfi", "-i", source]
    if audio_filter:
        args += ["-filter_complex", audio_filter, "-map", "0:v", "-map", "[a]"]
    else:
        args += ["-map", "0:v", "-map", "1:a"]
    args += [
        "-t", str(seconds),
        "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
        "-c:a", "aac",
        str(path),
    ]
    _ffmpeg(*args)
    return path


_TONE_FILTER = "[1:a]volume=0.5[a]"
# Bursts over a continuous quiet bed: the sine source's own RMS is ~-21 dBFS,
# so volume 0.25 puts the bed near -33 dBFS inside the speech gaps.
_BED_FILTER = (
    "[2:a]volume=0.25[bed];"
    "[1:a][bed]amix=inputs=2:duration=first:normalize=0[a]"
)


@pytest.fixture(scope="module")
def media(tmp_path_factory) -> dict[str, Path]:
    d = tmp_path_factory.mktemp("clip-qc")
    return {
        "good": _make_clip(
            d, "good.mp4", "testsrc2=size=1080x1920:rate=10",
            [_TONE_SOURCE], 4, _TONE_FILTER,
        ),
        "capped": _make_clip(
            d, "capped.mp4", "testsrc2=size=720x1280:rate=10",
            [_TONE_SOURCE], 3, _TONE_FILTER,
        ),
        "small_original": _make_clip(
            d, "small.mp4", "testsrc2=size=320x568:rate=10",
            [_TONE_SOURCE], 3, _TONE_FILTER,
        ),
        "silent": _make_clip(
            d, "silent.mp4", "testsrc2=size=320x568:rate=10",
            [_SILENT_SOURCE], 3,
        ),
        "black": _make_clip(
            d, "black.mp4", "color=c=black:size=320x568:rate=10",
            [_TONE_SOURCE], 3, _TONE_FILTER,
        ),
        "frozen": _make_clip(
            d, "frozen.mp4", "color=c=red:size=320x568:rate=10",
            [_TONE_SOURCE], 3, _TONE_FILTER,
        ),
        "frozen_silent": _make_clip(
            d, "frozen_silent.mp4", "color=c=red:size=320x568:rate=10",
            [_SILENT_SOURCE], 3,
        ),
        "wrong_res": _make_clip(
            d, "wrong_res.mp4", "testsrc2=size=640x360:rate=10",
            [_TONE_SOURCE], 3, _TONE_FILTER,
        ),
        "music_absent": _make_clip(
            d, "music_absent.mp4", "testsrc2=size=320x568:rate=10",
            [_BURST_SOURCE], 6,
        ),
        "music_present": _make_clip(
            d, "music_present.mp4", "testsrc2=size=320x568:rate=10",
            [_BURST_SOURCE, _BED_SOURCE], 6, _BED_FILTER,
        ),
    }


def _checks_by_name(report: dict) -> dict[str, dict]:
    return {entry["name"]: entry for entry in report["checks"]}


def test_good_vertical_clip_passes_everything(media):
    report = run_clip_qc(
        media["good"],
        ClipQcContext(expected_duration=4.0, output_format="vertical"),
    )
    assert report["status"] == "pass", report
    assert report["hard_fail"] is False
    checks = _checks_by_name(report)
    for name in ("file", "duration", "resolution", "audio", "black_frames",
                 "frozen_frame", "duplicate"):
        assert checks[name]["status"] == "pass", (name, checks[name])
    # Music was not expected, so the music check must not run at all.
    assert "music" not in checks
    assert report["file"]["width"] == 1080
    assert report["file"]["height"] == 1920


def test_capped_resolution_passes_with_matching_short_side(media):
    report = run_clip_qc(
        media["capped"],
        ClipQcContext(
            expected_duration=3.0, output_format="vertical", max_short_side=720
        ),
    )
    checks = _checks_by_name(report)
    assert checks["resolution"]["status"] == "pass", checks["resolution"]


def test_capped_file_fails_when_cap_was_not_applied(media):
    report = run_clip_qc(
        media["good"],
        ClipQcContext(
            expected_duration=4.0, output_format="vertical", max_short_side=720
        ),
    )
    checks = _checks_by_name(report)
    assert checks["resolution"]["status"] == "fail"
    assert report["hard_fail"] is True


def test_wrong_resolution_fails(media):
    report = run_clip_qc(
        media["wrong_res"],
        ClipQcContext(expected_duration=3.0, output_format="vertical"),
    )
    checks = _checks_by_name(report)
    assert checks["resolution"]["status"] == "fail", checks["resolution"]
    assert report["status"] == "fail"


def test_silent_audio_warns_but_does_not_fail(media):
    report = run_clip_qc(
        media["silent"],
        ClipQcContext(expected_duration=3.0, output_format="original"),
    )
    checks = _checks_by_name(report)
    assert checks["audio"]["status"] == "warn", checks["audio"]
    assert checks["audio"]["evidence"]["rms_db"] <= -55.0
    assert report["status"] == "warn"
    assert report["hard_fail"] is False


def test_all_black_clip_fails(media):
    report = run_clip_qc(
        media["black"],
        ClipQcContext(expected_duration=3.0, output_format="original"),
    )
    checks = _checks_by_name(report)
    assert checks["black_frames"]["status"] == "fail", checks["black_frames"]
    assert all(
        value is not None and value <= 10.0
        for value in checks["black_frames"]["evidence"]["sample_luma"]
    )
    assert report["hard_fail"] is True


def test_frozen_clip_with_audio_only_warns(media):
    report = run_clip_qc(
        media["frozen"],
        ClipQcContext(expected_duration=3.0, output_format="original"),
    )
    checks = _checks_by_name(report)
    assert checks["frozen_frame"]["status"] == "warn", checks["frozen_frame"]
    assert report["hard_fail"] is False


def test_frozen_and_silent_clip_fails(media):
    report = run_clip_qc(
        media["frozen_silent"],
        ClipQcContext(expected_duration=3.0, output_format="original"),
    )
    checks = _checks_by_name(report)
    assert checks["frozen_frame"]["status"] == "fail", checks["frozen_frame"]
    assert report["hard_fail"] is True


def test_duration_mismatch_fails(media):
    report = run_clip_qc(
        media["small_original"],
        ClipQcContext(expected_duration=5.5, output_format="original"),
    )
    checks = _checks_by_name(report)
    assert checks["duration"]["status"] == "fail", checks["duration"]
    assert checks["duration"]["evidence"]["delta_seconds"] > 0.5


def test_music_expected_but_absent_warns_loudly(media):
    report = run_clip_qc(
        media["music_absent"],
        ClipQcContext(
            expected_duration=6.0,
            output_format="original",
            music_expected=True,
            music_label="calm (profile mood track)",
        ),
    )
    checks = _checks_by_name(report)
    music = checks["music"]
    assert music["status"] == "warn", music
    assert "MUSIC MISSING" in music["detail"]
    assert "calm" in music["detail"]
    assert music["evidence"]["gap_rms_db"] <= -50.0
    assert music["evidence"]["gap_seconds"] >= 0.8
    # A missing music bed is a warning, never a hard failure.
    assert report["hard_fail"] is False


def test_music_expected_and_present_passes(media):
    report = run_clip_qc(
        media["music_present"],
        ClipQcContext(
            expected_duration=6.0,
            output_format="original",
            music_expected=True,
            music_label="calm (profile mood track)",
        ),
    )
    checks = _checks_by_name(report)
    music = checks["music"]
    assert music["status"] == "pass", music
    assert music["evidence"]["gap_rms_db"] > -50.0


def test_missing_file_fails(media, tmp_path):
    report = run_clip_qc(
        tmp_path / "does-not-exist.mp4",
        ClipQcContext(expected_duration=3.0, output_format="original"),
    )
    assert report["status"] == "fail"
    assert report["hard_fail"] is True
    assert report["checks"][0]["name"] == "file"
    assert report["checks"][0]["status"] == "fail"


def test_corrupt_file_fails(tmp_path):
    bogus = tmp_path / "corrupt.mp4"
    bogus.write_bytes(b"this is not a video file at all" * 16)
    report = run_clip_qc(
        bogus, ClipQcContext(expected_duration=3.0, output_format="original")
    )
    assert report["status"] == "fail"
    assert report["hard_fail"] is True
    assert report["checks"][0]["status"] == "fail"


def test_duplicate_source_range_warns(media):
    report = run_clip_qc(
        media["small_original"],
        ClipQcContext(
            expected_duration=3.0,
            output_format="original",
            source_start=10.0,
            source_end=20.0,
            siblings=(
                {"start": 12.0, "end": 22.0, "sha256": None, "label": "clip 2"},
            ),
        ),
    )
    checks = _checks_by_name(report)
    duplicate = checks["duplicate"]
    assert duplicate["status"] == "warn", duplicate
    assert duplicate["evidence"]["iou"] >= 0.5


def test_duplicate_file_hash_warns(media):
    digest = hashlib.sha256(media["small_original"].read_bytes()).hexdigest()
    report = run_clip_qc(
        media["small_original"],
        ClipQcContext(
            expected_duration=3.0,
            output_format="original",
            source_start=100.0,
            source_end=110.0,
            siblings=(
                {"start": 0.0, "end": 5.0, "sha256": digest, "label": "clip 1"},
            ),
        ),
    )
    checks = _checks_by_name(report)
    duplicate = checks["duplicate"]
    assert duplicate["status"] == "warn", duplicate
    assert duplicate["evidence"]["match"] == "sha256"


def test_distinct_sibling_ranges_pass_duplicate_check(media):
    report = run_clip_qc(
        media["small_original"],
        ClipQcContext(
            expected_duration=3.0,
            output_format="original",
            source_start=100.0,
            source_end=110.0,
            siblings=(
                {"start": 0.0, "end": 5.0, "sha256": None, "label": "clip 1"},
            ),
        ),
    )
    checks = _checks_by_name(report)
    assert checks["duplicate"]["status"] == "pass", checks["duplicate"]
