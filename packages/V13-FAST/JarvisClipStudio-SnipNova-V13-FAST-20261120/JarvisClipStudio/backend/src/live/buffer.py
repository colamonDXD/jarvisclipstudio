"""Rolling segment buffer: wall-clock index, pruning, gaps, cut-from-the-past.

The recorder writes, per connection ("run"), a directory of stream-copied
segments plus ffmpeg's CSV segment list (``filename,start,end`` in seconds
from the start of that run) and a small ``run.json`` with the wall-clock
time the run's media timeline starts at. That gives every segment an
absolute wall-clock span, which is what chat timestamps and detections are
expressed in.

Segments are cut by ffmpeg's segmenter at keyframes, so every segment
starts decodable. ``cut_window`` concatenates the overlapping segments
WITHOUT re-encoding (seconds, not minutes) and returns where the requested
span sits inside the produced file; the clip pipeline trims exactly from
there. A gap between segments (dropped connection, skipped media) is
reported, and a window that spans a gap is flagged, never silently stitched.
"""
from __future__ import annotations

import csv
import json
import logging
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

logger = logging.getLogger(__name__)

GAP_TOLERANCE_SEC = 0.75
SEGMENT_LIST = "segments.csv"
RUN_META = "run.json"


@dataclass(frozen=True)
class Segment:
    path: Path
    run: str
    start: float  # wall-clock epoch seconds
    end: float

    @property
    def duration(self) -> float:
        return self.end - self.start


def write_run_meta(run_dir: Path, t0_wall: float, source: str) -> None:
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / RUN_META).write_text(json.dumps({"t0_wall": t0_wall, "source": source}), encoding="utf-8")


def _read_run(run_dir: Path) -> List[Segment]:
    meta_path, list_path = run_dir / RUN_META, run_dir / SEGMENT_LIST
    if not meta_path.is_file() or not list_path.is_file():
        return []
    try:
        t0 = float(json.loads(meta_path.read_text(encoding="utf-8"))["t0_wall"])
    except (ValueError, KeyError, OSError):
        return []
    segments: List[Segment] = []
    with list_path.open(newline="", encoding="utf-8") as fh:
        for row in csv.reader(fh):
            if len(row) < 3:
                continue
            try:
                start, end = float(row[1]), float(row[2])
            except ValueError:
                continue
            path = run_dir / row[0]
            if path.is_file() and end > start:
                segments.append(Segment(path, run_dir.name, t0 + start, t0 + end))
    return segments


class SegmentBuffer:
    """All completed segments of a live session, across reconnects."""

    def __init__(self, session_dir: Path):
        self.session_dir = Path(session_dir)

    def segments(self) -> List[Segment]:
        runs = sorted(p for p in self.session_dir.glob("run-*") if p.is_dir())
        out: List[Segment] = []
        for run in runs:
            out.extend(_read_run(run))
        return sorted(out, key=lambda s: s.start)

    def span(self) -> Optional[Tuple[float, float]]:
        segs = self.segments()
        return (segs[0].start, segs[-1].end) if segs else None

    def gaps(self) -> List[Tuple[float, float]]:
        segs = self.segments()
        return [(a.end, b.start) for a, b in zip(segs, segs[1:]) if b.start - a.end > GAP_TOLERANCE_SEC]

    def prune(self, keep_seconds: float, now: Optional[float] = None) -> int:
        """Delete segments that ended more than ``keep_seconds`` ago."""
        now = time.time() if now is None else now
        removed = 0
        for seg in self.segments():
            if seg.end < now - keep_seconds:
                try:
                    seg.path.unlink()
                    removed += 1
                except OSError:
                    pass
        return removed

    def cut_window(self, start_wall: float, end_wall: float, out_path: Path) -> Optional[Dict]:
        """Stream-copy the segments covering [start_wall, end_wall] into ``out_path``.

        Returns ``{path, file_start_wall, offset, duration, gaps, clipped}``:
        the requested span is ``[offset, offset + duration]`` inside the file.
        ``clipped`` says the buffer did not reach the full request (too old,
        or not recorded yet); ``gaps`` lists holes inside the window.
        """
        segs = [s for s in self.segments() if s.end > start_wall and s.start < end_wall]
        if not segs:
            return None
        gaps = [(a.end, b.start) for a, b in zip(segs, segs[1:]) if b.start - a.end > GAP_TOLERANCE_SEC]
        out_path = Path(out_path).resolve()
        out_path.parent.mkdir(parents=True, exist_ok=True)
        concat = out_path.with_suffix(".concat.txt")
        # Absolute paths: the concat demuxer resolves list entries against the
        # list file's own folder, so relative session paths broke the cut.
        concat.write_text("".join(f"file '{s.path.resolve().as_posix()}'\n" for s in segs), encoding="utf-8")
        cmd = ["ffmpeg", "-v", "error", "-y", "-f", "concat", "-safe", "0", "-i", str(concat),
               "-c", "copy", "-movflags", "+faststart", str(out_path)]
        result = subprocess.run(cmd, capture_output=True, text=True)
        concat.unlink(missing_ok=True)
        if result.returncode != 0 or not out_path.is_file():
            logger.warning("Live buffer cut failed: %s", (result.stderr or "")[-300:])
            return None
        file_start = segs[0].start
        real_start = max(start_wall, file_start)
        real_end = min(end_wall, segs[-1].end)
        return {
            "path": str(out_path),
            "file_start_wall": file_start,
            "offset": round(real_start - file_start, 3),
            "duration": round(real_end - real_start, 3),
            "gaps": gaps,
            "clipped": real_start > start_wall + 0.5 or real_end < end_wall - 0.5,
        }
