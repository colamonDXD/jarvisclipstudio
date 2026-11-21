"""layout_report["broll_applied"] must tell the truth on the EDL path.

Self-run audit #2, finding A2-3: the flag tracked only the legacy
``broll_overlays`` parameter, so a render whose B-roll arrived inside
the EDL (the production path) reported False even when the stock
footage provably composited into the export — and the services reading
the flag then wiped the clip's recorded B-roll placements, the very
placements a later re-render carries.

Two layers of pins:

* the predicate (``edl_broll_will_composite``) against the graph
  builder's own participation rule — resolved file + positive window
  counts, everything else does not;
* the wiring, through the real ``create_optimized_clip`` seam with
  real FFmpeg: a resolved EDL entry reports True AND its footage is
  measurably in the export; an unresolved entry (the no-provider-key
  case) renders clean and honestly reports False; the legacy overlays
  parameter still reports True.
"""

import shutil
import subprocess
from pathlib import Path

import cv2
import numpy as np
import pytest

from src.media.edl_render import edl_broll_will_composite

ffmpeg_missing = shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None
pytestmark_render = pytest.mark.skipif(
    ffmpeg_missing, reason="ffmpeg/ffprobe not installed"
)

STOCK_BGR = (0, 0, 255)  # solid red stock footage


class TestEdlBrollWillComposite:
    def test_resolved_entry_counts(self, tmp_path):
        stock = tmp_path / "stock.mp4"
        stock.write_bytes(b"fake but present")
        edl = {"broll": [{"start": 2.0, "end": 4.0, "local_path": str(stock)}]}
        assert edl_broll_will_composite(edl) is True

    def test_unresolved_entry_does_not_count(self, tmp_path):
        """No local_path (planning could not resolve it — e.g. no stock
        provider key): the graph skips it, so the report must too."""
        edl = {"broll": [{"start": 2.0, "end": 4.0, "query": "city"}]}
        assert edl_broll_will_composite(edl) is False

    def test_missing_file_does_not_count(self, tmp_path):
        edl = {
            "broll": [
                {"start": 2.0, "end": 4.0, "local_path": str(tmp_path / "gone.mp4")}
            ]
        }
        assert edl_broll_will_composite(edl) is False

    def test_non_positive_window_does_not_count(self, tmp_path):
        stock = tmp_path / "stock.mp4"
        stock.write_bytes(b"fake but present")
        for window in ({"start": 4.0, "end": 4.0}, {"start": 5.0, "end": 4.0}):
            edl = {"broll": [{**window, "local_path": str(stock)}]}
            assert edl_broll_will_composite(edl) is False

    def test_junk_entries_are_skipped_not_fatal(self, tmp_path):
        stock = tmp_path / "stock.mp4"
        stock.write_bytes(b"fake but present")
        edl = {
            "broll": [
                "not-a-dict",
                {"start": "soon", "end": "later", "local_path": str(stock)},
                {"start": 1.0, "end": 2.0, "local_path": str(stock)},
            ]
        }
        assert edl_broll_will_composite(edl) is True

    def test_no_broll_no_count(self):
        assert edl_broll_will_composite(None) is False
        assert edl_broll_will_composite({}) is False
        assert edl_broll_will_composite({"broll": []}) is False


def _run(command):
    result = subprocess.run(command, capture_output=True, text=True, timeout=600)
    assert result.returncode == 0, result.stderr
    return result


def _make_clip(path: Path, colour: str, duration: float) -> None:
    _run([
        "ffmpeg", "-y", "-v", "error",
        "-f", "lavfi", "-i",
        f"color=c={colour}:size=320x568:rate=30:duration={duration}",
        "-f", "lavfi", "-i", "anullsrc=channel_layout=stereo:sample_rate=48000",
        "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac",
        "-t", str(duration), str(path),
    ])


def _frame(video: Path, at_seconds: float, out: Path) -> np.ndarray:
    _run([
        "ffmpeg", "-y", "-v", "error", "-ss", str(at_seconds), "-i", str(video),
        "-frames:v", "1", str(out),
    ])
    image = cv2.imread(str(out))
    assert image is not None
    return image


def _near(image: np.ndarray, bgr, tol: int = 45) -> int:
    target = np.array(bgr, dtype=np.int16)
    diff = np.abs(image.astype(np.int16) - target)
    return int(np.all(diff <= tol, axis=2).sum())


def _base_edl() -> dict:
    return {
        "duration": 6.0,
        "segments": [
            {"index": 0, "start": 0.0, "end": 6.0, "transition_in": "none",
             "transition_duration": 0.0},
        ],
        "zoom_keyframes": [],
        "overlays": [],
        "captions": {"preset_id": "default"},
    }


def _render(tmp_path: Path, monkeypatch, **kwargs) -> tuple[bool, dict, Path]:
    from src.video_utils import create_optimized_clip

    monkeypatch.setenv("FORCE_CPU_ENCODE", "true")
    source = tmp_path / "source.mp4"
    if not source.exists():
        _make_clip(source, "0x404040", 6.0)
    output = tmp_path / "out.mp4"
    report: dict = {}
    ok = create_optimized_clip(
        source, 0.0, 6.0, output,
        add_subtitles=False, output_format="original",
        keep_ranges=[(0.0, 6.0)], extend_to_sentence=False,
        layout_report=report, **kwargs,
    )
    return ok, report, output


@pytestmark_render
class TestBrollAppliedWiring:
    def test_edl_resolved_broll_reports_applied_and_composites(
        self, tmp_path, monkeypatch
    ):
        """The audit's case in miniature: B-roll inside the EDL, none in
        the legacy parameter. The flag must say True — and the footage
        must measurably be in the export, so the flag is not just
        relabelled."""
        stock = tmp_path / "stock.mp4"
        _make_clip(stock, "red", 4.0)
        edl = {
            **_base_edl(),
            "broll": [
                {"start": 2.0, "end": 4.0, "source_start": 0.0,
                 "local_path": str(stock), "query": "city"},
            ],
        }
        ok, report, output = _render(tmp_path, monkeypatch, edl=edl)
        assert ok and output.is_file()
        assert report["broll_applied"] is True
        inside = _frame(output, 3.0, tmp_path / "inside.png")
        outside = _frame(output, 1.0, tmp_path / "outside.png")
        total = inside.shape[0] * inside.shape[1]
        assert _near(inside, STOCK_BGR) > total * 0.8
        assert _near(outside, STOCK_BGR) < total * 0.05

    def test_edl_unresolved_broll_reports_not_applied(
        self, tmp_path, monkeypatch
    ):
        """The no-provider-key case: the entry never resolved, the
        render proceeds untouched, and the flag honestly says False."""
        edl = {
            **_base_edl(),
            "broll": [{"start": 2.0, "end": 4.0, "query": "city"}],
        }
        ok, report, output = _render(tmp_path, monkeypatch, edl=edl)
        assert ok and output.is_file()
        assert report["broll_applied"] is False
        frame = _frame(output, 3.0, tmp_path / "frame.png")
        total = frame.shape[0] * frame.shape[1]
        assert _near(frame, STOCK_BGR) < total * 0.05

    def test_legacy_overlays_parameter_still_reports_applied(
        self, tmp_path, monkeypatch
    ):
        stock = tmp_path / "stock.mp4"
        _make_clip(stock, "red", 4.0)
        ok, report, output = _render(
            tmp_path, monkeypatch,
            broll_overlays=[{"path": str(stock), "start": 2.0, "duration": 2.0}],
        )
        assert ok and output.is_file()
        assert report["broll_applied"] is True
