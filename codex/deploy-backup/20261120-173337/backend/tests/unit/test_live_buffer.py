"""Live MVP: rolling buffer index, gaps, pruning, cut-from-the-past, recorder."""
from __future__ import annotations

import json
import subprocess
import time
from pathlib import Path

import pytest

from src.live import live_clipping_enabled
from src.live.buffer import SegmentBuffer, write_run_meta
from src.live.recorder import LiveRecorder, _is_page_url


def _fake_run(session: Path, name: str, t0: float, spans):
    run = session / name
    run.mkdir(parents=True)
    write_run_meta(run, t0, "test")
    rows = []
    for i, (a, b) in enumerate(spans):
        f = f"seg_{i:06d}.ts"
        (run / f).write_bytes(b"x")
        rows.append(f"{f},{a},{b}\n")
    (run / "segments.csv").write_text("".join(rows), encoding="utf-8")


def test_index_spans_runs_in_wall_clock_and_reports_the_reconnect_gap(tmp_path):
    _fake_run(tmp_path, "run-0000", 1000.0, [(0, 4), (4, 8), (8, 12)])
    _fake_run(tmp_path, "run-0001", 1015.0, [(0, 4), (4, 8)])  # 3 s outage
    buf = SegmentBuffer(tmp_path)
    segs = buf.segments()
    assert [round(s.start) for s in segs] == [1000, 1004, 1008, 1015, 1019]
    assert buf.span() == (1000.0, 1023.0)
    assert buf.gaps() == [(1012.0, 1015.0)]


def test_prune_keeps_only_the_recent_window(tmp_path):
    _fake_run(tmp_path, "run-0000", 1000.0, [(0, 4), (4, 8), (8, 12)])
    buf = SegmentBuffer(tmp_path)
    assert buf.prune(keep_seconds=5, now=1014.0) == 2
    assert [round(s.start) for s in buf.segments()] == [1008]


def test_page_urls_go_through_yt_dlp():
    assert _is_page_url("https://www.twitch.tv/somechannel")
    assert _is_page_url("https://www.youtube.com/watch?v=abc")
    assert not _is_page_url("https://cdn.example/stream.m3u8")
    assert not _is_page_url("D:/test.mp4")


def test_switch_default_off(monkeypatch):
    monkeypatch.delenv("LIVE_CLIPPING", raising=False)
    assert not live_clipping_enabled()


def _probe_duration(path: str) -> float:
    out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                          "-of", "csv=p=0", path], capture_output=True, text=True).stdout
    return float(out.strip())


@pytest.fixture
def fake_live_source(tmp_path):
    """A 30 s test video (H.264/AAC, keyframe every 1 s) played in real time."""
    src = tmp_path / "source.ts"
    subprocess.run(
        ["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc2=size=640x360:rate=30:duration=30",
         "-f", "lavfi", "-i", "sine=frequency=440:duration=30", "-c:v", "libx264", "-preset", "ultrafast",
         "-g", "30", "-c:a", "aac", "-shortest", str(src)],
        check=True, capture_output=True)
    return src


def test_recorder_buffers_live_and_cuts_footage_from_before_the_detection(tmp_path, fake_live_source):
    session = tmp_path / "session"
    rec = LiveRecorder(str(fake_live_source), session, segment_sec=2.0, realtime_file=True).start()
    try:
        time.sleep(16.0)  # 16 s of "live" stream
        detected_at = time.time()  # a moment detected NOW ...
        span = rec.buffer.span()
        assert span is not None and span[1] - span[0] >= 10.0
        # ... and the clip needs the 8 s BEFORE it (pre-roll from the buffer).
        cut = rec.buffer.cut_window(detected_at - 10.0, detected_at - 2.0, tmp_path / "window.mp4")
        assert cut is not None and not cut["gaps"]
        produced = _probe_duration(cut["path"])
        assert produced >= cut["offset"] + cut["duration"] - 0.1
        assert 6.0 <= cut["duration"] <= 8.5
        assert rec.running  # still recording while we cut
    finally:
        rec.stop()


def test_recorder_reconnects_after_a_drop_and_reports_the_gap(tmp_path, fake_live_source):
    session = tmp_path / "session"
    rec = LiveRecorder(str(fake_live_source), session, segment_sec=2.0, realtime_file=True)
    rec.realtime_file = True
    rec.start()
    try:
        time.sleep(6.0)
        # Simulate a dropped connection: kill the pull mid-stream.
        rec._procs[-1].kill()
        deadline = time.time() + 20
        while time.time() < deadline and len(list(session.glob("run-*"))) < 2:
            time.sleep(0.5)
        time.sleep(6.0)
        runs = sorted(session.glob("run-*"))
        assert len(runs) >= 2 and rec.restarts >= 1
        assert json.loads((runs[1] / "run.json").read_text())["t0_wall"] > \
            json.loads((runs[0] / "run.json").read_text())["t0_wall"]
        assert rec.buffer.gaps()  # the outage is visible, never stitched over
    finally:
        rec.stop()
