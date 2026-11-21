"""Live audio excitement: loudness per half second, against a rolling baseline.

Each new buffer segment is measured once (two cheap ffmpeg passes: overall
RMS and >1 kHz RMS, the band shouting and laughter push up most), and the
points are kept on the wall clock. ``zscores`` compares recent points with
the robust baseline (median / MAD) of the last ``baseline_sec`` excluding
the most recent ``exclude_recent_sec``, so a long exciting stretch does not
become its own baseline straight away.
"""
from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import numpy as np

from .buffer import Segment, SegmentBuffer

STEP_SEC = 0.5
SR = 16000
_RMS_RE = re.compile(r"lavfi\.astats\.Overall\.RMS_level=(-?[\d.]+|-inf)")


def _levels(path: Path, highpass: bool) -> List[float]:
    chain = (f"aresample={SR},{'highpass=f=1000,' if highpass else ''}"
             f"asetnsamples={int(SR * STEP_SEC)},astats=metadata=1:reset=1,"
             "ametadata=print:key=lavfi.astats.Overall.RMS_level:file=-")
    out = subprocess.run(["ffmpeg", "-v", "error", "-i", str(path), "-vn", "-af", chain, "-f", "null", "-"],
                         capture_output=True, text=True).stdout
    values = []
    for match in _RMS_RE.finditer(out):
        raw = match.group(1)
        values.append(-120.0 if raw == "-inf" else max(-120.0, float(raw)))
    return values


@dataclass
class AudioTrack:
    baseline_sec: float = 300.0
    exclude_recent_sec: float = 10.0
    times: List[float] = field(default_factory=list)
    level: List[float] = field(default_factory=list)
    high: List[float] = field(default_factory=list)
    _seen: set = field(default_factory=set)

    def add_segment(self, seg: Segment) -> int:
        key = str(seg.path)
        if key in self._seen:
            return 0
        self._seen.add(key)
        lv, hi = _levels(seg.path, False), _levels(seg.path, True)
        n = min(len(lv), len(hi))
        for i in range(n):
            self.times.append(seg.start + i * STEP_SEC)
            self.level.append(lv[i])
            self.high.append(hi[i])
        return n

    def update(self, buffer: SegmentBuffer) -> int:
        added = 0
        for seg in buffer.segments():
            added += self.add_segment(seg)
        order = np.argsort(self.times)
        self.times = [self.times[i] for i in order]
        self.level = [self.level[i] for i in order]
        self.high = [self.high[i] for i in order]
        return added

    def zscores(self, now: Optional[float] = None) -> List[Tuple[float, float]]:
        """[(wall time, excitement z)]: each point against the baseline BEFORE it.

        Causal: a point's z uses the ``baseline_sec`` that preceded it
        (excluding the last ``exclude_recent_sec``), so a later change of
        level never rewrites history (fake-live demo: a moment at 4 s only
        "appeared" 341 s later when old points were re-scored against the
        current baseline). Computed once per point and cached. z = max of
        the level z and the >1 kHz z; 0 until 20 baseline points exist.
        """
        t = np.asarray(self.times)
        lv, hi = np.asarray(self.level), np.asarray(self.high)
        cache = getattr(self, "_zcache", {})
        out: List[Tuple[float, float]] = []
        for i in range(len(t)):
            key = (float(t[i]),)
            if key in cache:
                out.append((float(t[i]), cache[key]))
                continue
            lo = np.searchsorted(t, t[i] - self.baseline_sec)
            hi_idx = np.searchsorted(t, t[i] - self.exclude_recent_sec)
            if hi_idx - lo < 20:
                z = 0.0
            else:
                z = 0.0
                for series in (lv, hi):
                    ref = series[lo:hi_idx]
                    med = float(np.median(ref))
                    mad = float(np.median(np.abs(ref - med))) * 1.4826
                    z = max(z, (float(series[i]) - med) / max(mad, 1.5))
            cache[key] = z
            out.append((float(t[i]), z))
        self._zcache = cache
        return out
