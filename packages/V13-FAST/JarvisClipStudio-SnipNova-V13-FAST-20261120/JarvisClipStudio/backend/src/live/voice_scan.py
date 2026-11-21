"""Fast live pass: streamer voice evidence on the newest audio only.

The full reaction picker on a 120 s buffer window costs ~100 s on this
laptop's CPU, almost all of it facecam analysis; the voice evidence
(``reaction_moments.extract_voice_evidence``: voice-band level, pitch,
laughter, mic channel) costs ~5 s per 120 s. So the live loop runs this
pass on just the audio recorded since the last tick, flags reaction
onsets with the picker's own rules (two voice signals at z >= 3, a strong
laugh, or the scream rule: pitch z >= 4 with vocal z >= 1.5), and only then
asks the full picker to confirm on a short window around the flag
(``confirm_with_picker``), which also names the on-screen trigger.

Baselines are causal (each second against the seconds before it), like
``live.signals.AudioTrack``.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

from .buffer import SegmentBuffer

logger = logging.getLogger(__name__)

FLOORS = {"vocal": 2.0, "f0": 18.0, "laugh": 0.12}
ACTIVE_Z = 3.0


@dataclass
class VoiceTrack:
    baseline_sec: float = 180.0
    exclude_recent_sec: float = 8.0
    min_baseline: int = 20
    times: List[float] = field(default_factory=list)
    feats: Dict[str, List[float]] = field(default_factory=lambda: {k: [] for k in FLOORS})
    # Voiced fraction per second (not scored): where the streamer is talking,
    # for the hook-first clip window (moments.speech_window).
    voiced: List[float] = field(default_factory=list)
    processed_until: Optional[float] = None
    _z: Dict[float, Dict[str, float]] = field(default_factory=dict)

    def update(self, buffer: SegmentBuffer, work_dir: Path, min_new_sec: float = 6.0) -> int:
        """Analyse the audio recorded since the last call; returns seconds added."""
        from ..auto_director.reaction_moments import extract_voice_evidence

        span = buffer.span()
        if not span:
            return 0
        start = span[0] if self.processed_until is None else self.processed_until
        if span[1] - start < min_new_sec:
            return 0
        window = buffer.cut_window(start, span[1], Path(work_dir) / "voice_window.mp4")
        if not window:
            return 0
        try:
            ev = extract_voice_evidence(window["path"], window["offset"] + window["duration"])
        except Exception as exc:
            logger.warning("Live voice pass failed: %s", exc)
            return 0
        base = window["file_start_wall"]
        n = min(len(ev.get(k, [])) for k in FLOORS)
        added = 0
        for sec in range(n):
            t = base + sec
            if t < start - 0.01 or t >= span[1]:
                continue  # outside the new stretch (the cut starts on a keyframe)
            self.times.append(t)
            for k in FLOORS:
                self.feats[k].append(float(ev[k][sec]))
            vf = ev.get("voiced_frac")
            self.voiced.append(float(vf[sec]) if vf is not None and sec < len(vf) else 0.0)
            added += 1
        self.processed_until = span[1]
        return added

    def voiced_between(self, start: float, end: float) -> Tuple[float, List[float]]:
        """``(first_second_wall, voiced fractions)`` for the seconds in [start, end)."""
        idx = [i for i, t in enumerate(self.times) if start <= t < end]
        if not idx:
            return start, []
        return self.times[idx[0]], [self.voiced[i] if i < len(self.voiced) else 0.0 for i in idx]

    def zscores(self) -> List[Tuple[float, Dict[str, float]]]:
        t = np.asarray(self.times)
        # Built once per call (not once per second): an 11 h stream has 40k
        # seconds, and rebuilding the arrays inside the loop took minutes.
        arrays = {k: np.asarray(self.feats[k]) for k in FLOORS}
        out = []
        for i, ti in enumerate(self.times):
            if ti in self._z:
                out.append((ti, self._z[ti]))
                continue
            lo = int(np.searchsorted(t, ti - self.baseline_sec))
            hi = int(np.searchsorted(t, ti - self.exclude_recent_sec))
            z: Dict[str, float] = {}
            for k, floor in FLOORS.items():
                series = arrays[k]
                if hi - lo < self.min_baseline:
                    z[k] = 0.0
                    continue
                ref = series[lo:hi]
                med = float(np.nanmedian(ref))
                mad = float(np.nanmedian(np.abs(ref - med))) * 1.4826
                value = float(series[i])
                z[k] = 0.0 if np.isnan(value) else (value - med) / max(mad, floor * 0.25)
            self._z[ti] = z
            out.append((ti, z))
        return out

    def onsets(self, merge_gap: float = 3.0) -> List[Dict[str, Any]]:
        """Reaction flags: [{onset, end, peak, signals}] from the picker's own rules."""
        flags: List[Dict[str, Any]] = []
        for t, z in self.zscores():
            active = [k for k, v in z.items() if v >= ACTIVE_Z]
            scream = z.get("f0", 0) >= 4.0 and z.get("vocal", 0) >= 1.5
            if len(active) >= 2 or z.get("laugh", 0) >= 5.0 or scream:
                strength = max(z.values())
                if flags and t - flags[-1]["end"] <= merge_gap:
                    flags[-1]["end"] = t
                    flags[-1]["peak"] = max(flags[-1]["peak"], strength)
                    flags[-1]["signals"] = sorted(set(flags[-1]["signals"]) | set(active) | ({"scream"} if scream else set()))
                else:
                    flags.append({"onset": t, "end": t, "peak": strength,
                                  "signals": sorted(set(active) | ({"scream"} if scream else set()))})
        return flags


def confirm_with_picker(buffer: SegmentBuffer, work_dir: Path, onset: float,
                        before: float = 15.0, after: float = 15.0) -> Optional[Dict[str, Any]]:
    """Run the full reaction picker on [onset-before, onset+after]; the best
    reacted moment there (on the wall clock), or None when it finds none."""
    from .reaction_scan import scan_reactions

    found = scan_reactions(buffer, work_dir, window_sec=before + after, now=onset + after, target_count=2)
    near = [r for r in found if abs(r["onset"] - onset) <= max(before, after)]
    return max(near, key=lambda r: r["score"]) if near else None
