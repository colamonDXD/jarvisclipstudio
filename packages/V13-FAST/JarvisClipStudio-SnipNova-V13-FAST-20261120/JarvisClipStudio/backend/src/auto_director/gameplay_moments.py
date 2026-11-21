"""Evidence-fusion moment detection for sources with no usable transcript.

``visual_moments.select_visual_moments`` ranks audio peaks and scene cuts.
For gameplay that fails in a known way: the loudest sound in a recording is
often NOT its best moment (a distant explosion, a menu sting), while the real
moments are mostly quiet changes on screen — a kill feed line appearing, a
damage flash, a banner, a character tumbling, screen shake.

This module reads the pictures. It samples the video at low resolution and
measures six game-agnostic signals per second:

* ``motion``   — mean frame-to-frame change
* ``local``    — change concentrated in a few screen cells (HUD, objects)
* ``tint``     — whole-frame colour shift (damage / flash / fade)
* ``shake``    — oscillating global image shift
* ``text``     — change in edge density of the upper-centre band (banners)
* ``novelty``  — change versus two seconds ago (things that appear and stay)

Each is turned into a robust z-score against its own local baseline, so a
game with constant camera motion and a game with a static camera are judged
against themselves. A second is "eventful" only when SEVERAL signals agree.
Eventful stretches become episodes; episodes close together become one
connected sequence. Audio then SUPPORTS an episode (a reaction, an impact) but
can never create a top-ranked one: an audio-only burst is kept only as a
low-scored last resort.

Nothing here names a game, a weapon or an event the pixels cannot show. Event
types are patterns of evidence, e.g. ``hud_event_streak`` ("repeated
on-screen HUD events with impact flashes"), never "kill".
"""

from __future__ import annotations

import logging
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterator, Optional

import numpy as np

logger = logging.getLogger(__name__)

SAMPLE_FPS = 8
FRAME_W, FRAME_H = 160, 90
_GRID_X, _GRID_Y = 8, 6
FEATURES = ("motion", "local", "tint", "shake", "text", "novelty", "pursuit")
# Absolute floors for the robust scale, so a perfectly still baseline can
# not turn noise into a huge z-score.
_SCALE_FLOOR = {
    "motion": 0.35, "local": 0.8, "tint": 0.8,
    "shake": 0.12, "text": 0.004, "novelty": 0.6, "pursuit": 1.0,
}
_WEIGHT = {
    "motion": 1.0, "local": 1.0, "tint": 1.2,
    "shake": 1.0, "text": 1.2, "novelty": 0.8, "pursuit": 1.0,
}
_BASELINE_HALF_WINDOW_SEC = 45
_ACTIVE_Z = 3.0            # a feature "agrees" above this z
_MIN_AGREEING = 2          # features that must agree in one second
_STRONG_SINGLE_Z = 9.0     # ...or one feature this far out
_PERSIST_Z = 6.0          # one signal this far out...
_PERSIST_SEC = 4          # ...for this many seconds in a row is an event
_GAP_MERGE_SEC = 3         # eventful seconds this close are one episode
_SEQUENCE_MERGE_SEC = 8    # episodes this close are one connected sequence
_MIN_EPISODE_SEC = 2
_AUDIO_ONLY_SCORE_CAP = 35.0


@dataclass
class Episode:
    start: float
    end: float
    peak: float
    strength: float
    features: dict[str, float]
    audio_support: float = 0.0
    escalation: float = 0.0
    parts: int = 1
    event_type: str = "visual_change"
    reason: str = ""
    score: float = 0.0
    extra: dict[str, Any] = field(default_factory=dict)


# --------------------------------------------------------------------------
# Frame evidence extraction
# --------------------------------------------------------------------------

def _stream_frames(video_path: Path | str) -> Iterator[np.ndarray]:
    """Yield RGB uint8 frames (H, W, 3) at SAMPLE_FPS from ffmpeg."""
    cmd = [
        "ffmpeg", "-v", "error", "-i", str(video_path), "-an",
        "-vf", f"fps={SAMPLE_FPS},scale={FRAME_W}:{FRAME_H}:flags=area,format=rgb24",
        "-f", "rawvideo", "-",
    ]
    size = FRAME_W * FRAME_H * 3
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    try:
        assert proc.stdout is not None
        while True:
            buf = proc.stdout.read(size)
            if len(buf) < size:
                break
            yield np.frombuffer(buf, dtype=np.uint8).reshape(FRAME_H, FRAME_W, 3)
    finally:
        if proc.stdout:
            proc.stdout.close()
        proc.kill()
        proc.wait()


def _global_shift(prev: np.ndarray, cur: np.ndarray) -> tuple[float, float]:
    """Sub-pixel global translation between two gray frames."""
    import cv2

    (dx, dy), _ = cv2.phaseCorrelate(prev.astype(np.float32), cur.astype(np.float32))
    return float(dx), float(dy)


def _edge_density(gray: np.ndarray) -> float:
    gx = np.abs(np.diff(gray, axis=1)).mean()
    gy = np.abs(np.diff(gray, axis=0)).mean()
    return float(gx + gy) / 255.0


def extract_frame_evidence(
    video_path: Path | str,
    mask_roi: Optional[tuple[float, float, float, float]] = None,
) -> dict[str, np.ndarray]:
    """Per-second raw feature series for a video.

    ``mask_roi`` is a normalised (x, y, w, h) rectangle — a streamer's
    facecam — that is flattened before measuring, so the streamer moving or
    laughing is not mistaken for something happening in the game.

    Returns ``{feature: array[seconds]}`` plus ``"duration"`` (seconds).
    Streaming: memory stays flat for hour-long sources.
    """
    import cv2  # noqa: F401  (fail early if OpenCV is missing)

    per_sec: dict[str, list[list[float]]] = {f: [] for f in FEATURES}
    bucket: dict[str, list[float]] = {f: [] for f in FEATURES}
    shifts_x: list[float] = []
    history: list[np.ndarray] = []  # gray frames, ring of SAMPLE_FPS*2
    prev_gray: Optional[np.ndarray] = None
    tint_hist: list[np.ndarray] = []
    band_prev: Optional[float] = None
    cell_h, cell_w = FRAME_H // _GRID_Y, FRAME_W // _GRID_X
    n = 0

    def flush() -> None:
        for f in FEATURES:
            vals = bucket[f]
            if f == "shake":
                # Oscillation: movement back and forth within the second.
                if len(shifts_x) >= 3:
                    sx = np.array(shifts_x)
                    flips = int(np.sum(np.sign(sx[1:]) * np.sign(sx[:-1]) < 0))
                    amp = float(np.mean(np.abs(sx)))
                    per_sec[f].append([amp * (1.0 + flips)])
                else:
                    per_sec[f].append([0.0])
            elif vals:
                agg = float(np.max(vals)) if f in ("local", "tint", "text", "novelty") else float(np.mean(vals))  # pursuit/motion: mean = sustained
                per_sec[f].append([agg])
            else:
                per_sec[f].append([0.0])
            bucket[f] = []
        shifts_x.clear()

    mask = None
    if mask_roi is not None:
        mx, my, mw, mh = mask_roi
        mask = (int(my * FRAME_H), int((my + mh) * FRAME_H) + 1,
                int(mx * FRAME_W), int((mx + mw) * FRAME_W) + 1)
    for frame in _stream_frames(video_path):
        if mask is not None:
            frame = frame.copy()
            frame[mask[0]:mask[1], mask[2]:mask[3]] = 128
        gray = frame.astype(np.float32).mean(axis=2)
        mean_rgb = frame.reshape(-1, 3).mean(axis=0)
        history.append(gray)
        if len(history) > SAMPLE_FPS * 2 + 1:
            history.pop(0)
        tint_hist.append(mean_rgb)
        if len(tint_hist) > SAMPLE_FPS * 6:
            tint_hist.pop(0)
        if prev_gray is not None:
            diff = np.abs(gray - prev_gray)
            bucket["motion"].append(float(diff.mean()))
            cells = diff[: _GRID_Y * cell_h, : _GRID_X * cell_w].reshape(
                _GRID_Y, cell_h, _GRID_X, cell_w).mean(axis=(1, 3)).ravel()
            bucket["local"].append(float(np.percentile(cells, 90) - np.median(cells)))
            bucket["pursuit"].append(float(cells.max() - np.median(cells)))
            dx, _dy = _global_shift(prev_gray, gray)
            shifts_x.append(dx)
        # Tint: shift of the mean colour versus the 6-second median.
        if len(tint_hist) >= SAMPLE_FPS:
            ref = np.median(np.stack(tint_hist[:-1]), axis=0)
            bucket["tint"].append(float(np.abs(mean_rgb - ref).sum()))
        # Text/banner band: edge density of the upper-centre region.
        band = gray[int(FRAME_H * 0.12): int(FRAME_H * 0.42), int(FRAME_W * 0.12): int(FRAME_W * 0.88)]
        density = _edge_density(band)
        if band_prev is not None:
            bucket["text"].append(abs(density - band_prev))
        band_prev = density
        # Novelty: change versus ~2 s ago.
        if len(history) > SAMPLE_FPS * 2:
            bucket["novelty"].append(float(np.abs(gray - history[0]).mean()))
        prev_gray = gray
        n += 1
        if n % SAMPLE_FPS == 0:
            flush()
    if n % SAMPLE_FPS:
        flush()

    out = {f: np.array([row[0] for row in per_sec[f]], dtype=np.float64) for f in FEATURES}
    length = min((len(v) for v in out.values()), default=0)
    out = {f: v[:length] for f, v in out.items()}
    out["duration"] = np.array([float(n) / SAMPLE_FPS])
    return out


# --------------------------------------------------------------------------
# Fusion
# --------------------------------------------------------------------------

def _robust_z(series: np.ndarray, floor: float, rel: float = 0.25) -> np.ndarray:
    """z-score against a LOCAL robust baseline (median / MAD)."""
    n = len(series)
    z = np.zeros(n)
    half = _BASELINE_HALF_WINDOW_SEC
    for i in range(n):
        window = series[max(0, i - half): i + half + 1]
        med = float(np.median(window))
        mad = float(np.median(np.abs(window - med))) * 1.4826
        scale = max(mad, floor, rel * abs(med))
        z[i] = (series[i] - med) / scale
    return np.clip(z, -2.0, 12.0)


def audio_support_series(beat_grid: Optional[dict[str, Any]], seconds: int) -> np.ndarray:
    """Per-second audio z-score (peak level above local median). Zeros when
    there is no audio evidence."""
    curve = (beat_grid or {}).get("energy_curve") or {}
    levels = curve.get("levels_db") or []
    hop = float(curve.get("hop_sec") or 0.05)
    if len(levels) < 3 or seconds <= 0:
        return np.zeros(seconds)
    per_block = max(1, int(round(1.0 / hop)))
    arr = np.array(levels, dtype=np.float64)
    blocks = [float(arr[i: i + per_block].max()) for i in range(0, len(arr), per_block)]
    series = np.array(blocks[:seconds] + [min(blocks)] * max(0, seconds - len(blocks)))
    return _robust_z(series, 2.0)


def fuse_evidence(
    evidence: dict[str, np.ndarray], audio_z: Optional[np.ndarray] = None
) -> tuple[np.ndarray, dict[str, np.ndarray]]:
    """(per-second eventfulness, per-feature z). Eventfulness is the sum of
    weighted agreeing-feature z's, zero unless enough features agree."""
    zs = {f: _robust_z(evidence[f], _SCALE_FLOOR[f]) for f in FEATURES}
    n = min(len(v) for v in zs.values()) if zs else 0
    event = np.zeros(n)
    for i in range(n):
        active = [f for f in FEATURES if zs[f][i] >= _ACTIVE_Z]
        top = max((zs[f][i] for f in FEATURES), default=0.0)
        if len(active) >= _MIN_AGREEING or top >= _STRONG_SINGLE_Z:
            event[i] = sum(_WEIGHT[f] * (zs[f][i] - _ACTIVE_Z + 1.0) for f in active)
    # A single signal that stays far out for several seconds in a row is an
    # event too (a character tumbling is one continuous signal, not a flash).
    for f in FEATURES:
        run = 0
        for i in range(n + 1):
            if i < n and zs[f][i] >= _PERSIST_Z:
                run += 1
                continue
            if run >= _PERSIST_SEC:
                for j in range(i - run, i):
                    event[j] = max(event[j], _WEIGHT[f] * (zs[f][j] - _ACTIVE_Z + 1.0))
            run = 0
    return event, zs


def _classify(ep_feats: dict[str, float], parts: int, duration: float, escalation: float) -> tuple[str, str]:
    """Event type and a reason fragment, from evidence patterns only."""
    f = ep_feats
    if f.get("shake", 0) >= 4 and duration >= 5:
        return "sustained_escalation", (
            "sustained intense stretch with screen shake and repeated on-screen changes")
    if f.get("tint", 0) >= 8 and f.get("text", 0) >= 3:
        return "screen_flash_banner", (
            "full-screen colour flash followed by a large on-screen banner")
    if f.get("pursuit", 0) >= 6 and f.get("text", 0) < 3:
        return "erratic_motion", (
            "sustained, erratic movement of an on-screen subject")
    if f.get("local", 0) >= 5 and (parts >= 2 or f.get("text", 0) >= 3):
        return "hud_event_streak", (
            "repeated on-screen HUD events with impact flashes")
    if f.get("motion", 0) >= 3 or f.get("novelty", 0) >= 3:
        return "erratic_motion", (
            "sudden erratic movement of an on-screen subject")
    return "visual_change", "a distinct on-screen change"


def find_episodes(
    event: np.ndarray, zs: dict[str, np.ndarray], audio_z: Optional[np.ndarray] = None
) -> list[Episode]:
    n = len(event)
    active_secs = [i for i in range(n) if event[i] > 0]
    groups: list[list[int]] = []
    for i in active_secs:
        if groups and i - groups[-1][-1] <= _GAP_MERGE_SEC:
            groups[-1].append(i)
        else:
            groups.append([i])
    episodes: list[Episode] = []
    for g in groups:
        if g[-1] - g[0] + 1 < _MIN_EPISODE_SEC:
            continue
        start, end = float(g[0]), float(g[-1] + 1)
        seg = event[g[0]: g[-1] + 1]
        peak = float(g[0] + int(np.argmax(seg)))
        feats = {f: float(np.max(zs[f][g[0]: g[-1] + 1])) for f in FEATURES}
        # Escalation: later half stronger than the earlier half = build-up
        # to a payoff rather than a one-off spike.
        half = max(1, len(seg) // 2)
        esc = float(seg[half:].mean() - seg[:half].mean()) if len(seg) >= 4 else 0.0
        support = 0.0
        if audio_z is not None and len(audio_z):
            lo, hi = max(0, g[0] - 1), min(len(audio_z), g[-1] + 3)
            support = float(max(0.0, np.max(audio_z[lo:hi]))) if hi > lo else 0.0
        strength = float(seg.sum())
        episodes.append(Episode(
            start=start, end=end, peak=peak, strength=strength, features=feats,
            audio_support=support, escalation=esc,
        ))
    # Connect close episodes into one sequence (a story, not fragments).
    merged: list[Episode] = []
    for ep in episodes:
        if merged and ep.start - merged[-1].end <= _SEQUENCE_MERGE_SEC:
            prev = merged[-1]
            prev.end = ep.end
            prev.strength += ep.strength
            prev.parts += 1
            if ep.strength > 0 and ep.peak:
                prev.peak = ep.peak if ep.strength >= prev.strength / prev.parts else prev.peak
            prev.features = {f: max(prev.features[f], ep.features[f]) for f in FEATURES}
            prev.audio_support = max(prev.audio_support, ep.audio_support)
            prev.escalation = max(prev.escalation, ep.escalation)
        else:
            merged.append(ep)
    for ep in merged:
        dur = ep.end - ep.start
        ep.event_type, frag = _classify(ep.features, ep.parts, dur, ep.escalation)
        agree = sorted((f for f in FEATURES if ep.features[f] >= _ACTIVE_Z),
                       key=lambda f: -ep.features[f])
        audio_txt = (
            f"; audio reaction supports it (+{ep.audio_support:.1f}σ)" if ep.audio_support >= 2.5
            else "; no distinct audio reaction")
        ep.reason = (
            f"{frag} between {_mmss(ep.start)} and {_mmss(ep.end)} "
            f"(visual signals agreeing: {', '.join(agree)}{audio_txt})")
        base = min(100.0, 40.0 + 6.0 * np.sqrt(max(ep.strength, 0.0)))
        base += min(10.0, 2.5 * max(0.0, ep.escalation) / max(1.0, dur / 4))
        base += min(8.0, 2.0 * min(ep.audio_support, 4.0))
        base += min(6.0, 3.0 * (ep.parts - 1))
        ep.score = float(min(98.0, base))
    return merged


def _mmss(seconds: float) -> str:
    total = max(0, int(round(seconds)))
    return f"{total // 60:02d}:{total % 60:02d}"


# --------------------------------------------------------------------------
# Window shaping and selection
# --------------------------------------------------------------------------

def shape_window(
    ep: Episode, duration: float, clip_seconds: float, event: np.ndarray
) -> tuple[float, float, float, float]:
    """(start, end, lead, tail): lead-in a viewer needs, end on the payoff.

    Lead is a quarter of the action (3–6 s) so the build-up is visible; tail
    runs to where the eventfulness settles, plus a beat. The window is never
    shorter than 12 s or longer than 1.6x the target clip length; when the
    action is longer than that, the window centres on its peak.
    """
    action = ep.end - ep.start
    lead = float(min(6.0, max(3.0, 0.25 * action)))
    tail = 2.5
    start, end = ep.start - lead, ep.end + tail
    max_len = max(clip_seconds * 1.6, 20.0)
    min_len = min(12.0, duration)
    if end - start > max_len:
        start = max(ep.peak - 0.6 * max_len, ep.start - lead)
        end = start + max_len
    if end - start < min_len:
        extra = min_len - (end - start)
        start -= extra * 0.4
        end += extra * 0.6
    start = max(0.0, start)
    end = min(duration, end)
    if end - start < min_len:
        start = max(0.0, end - min_len)
    return start, end, max(0.0, ep.start - start), max(0.0, end - ep.end)


def select_gameplay_moments(
    video_path: Path | str,
    duration: float,
    *,
    target_count: int,
    clip_seconds: float,
    beat_grid: Optional[dict[str, Any]] = None,
    evidence: Optional[dict[str, np.ndarray]] = None,
    reason_context: str = "no usable transcript",
) -> list[dict[str, Any]]:
    """Pick up to ``target_count`` moments from fused visual + audio evidence.

    Returns segments in the pipeline's shape, each with a reason grounded in
    the measured evidence and an ``evidence`` block (signals, lead/tail,
    event type, confidence). Returns [] when the frames cannot be read, so the
    caller falls back to the audio/scene-cut selector.
    """
    duration = float(duration or 0)
    if duration <= 0 or target_count <= 0:
        return []
    try:
        if evidence is None:
            evidence = extract_frame_evidence(video_path)
        seconds = len(evidence["motion"])
        if seconds < 5:
            return []
        audio_z = audio_support_series(beat_grid, seconds)
        event, zs = fuse_evidence(evidence, audio_z)
        episodes = find_episodes(event, zs, audio_z)
    except Exception:
        logger.warning("Frame-evidence detection failed for %s", video_path, exc_info=True)
        return []

    clip_seconds = max(1.0, min(float(clip_seconds or 30), duration))
    moments: list[dict[str, Any]] = []
    accepted: list[tuple[float, float]] = []
    for ep in sorted(episodes, key=lambda e: -e.score):
        start, end, lead, tail = shape_window(ep, duration, clip_seconds, event)
        # Endings (viral queue #3/#7): payoff-tight / loop refinement of
        # the shaped end. Both switches default off; off is a no-op. No
        # transcript exists on this path, so the loop rhyme is visual
        # only. The shaping floor (shape_window's min length) binds here;
        # the render path's CLIP_MIN_SECONDS floor applies downstream.
        ending_rec = None
        try:
            from .endings import (
                frame_rhyme_score,
                loop_endings_enabled,
                payoff_tight_enabled,
                refine_clip_end,
            )

            if loop_endings_enabled() or payoff_tight_enabled():
                visual = None
                if loop_endings_enabled():
                    visual = lambda c, _s=start: frame_rhyme_score(video_path, _s, c)  # noqa: B023
                end, ending_rec = refine_clip_end(
                    start, end, payoff_end=float(ep.end), duration=duration,
                    min_seconds=min(12.0, duration), visual=visual)
                tail = max(0.0, end - ep.end)
        except Exception:
            logger.warning("Endings refinement failed", exc_info=True)
        if any(min(end, b) - max(start, a) > 0.25 * (end - start) for a, b in accepted):
            continue
        accepted.append((start, end))
        agree = [f for f in FEATURES if ep.features[f] >= _ACTIVE_Z]
        confidence = float(min(0.95, 0.35 + 0.1 * len(agree) + 0.05 * min(ep.parts, 3)))
        score = ep.score
        moments.append({
            "start_time": _mmss(start),
            "end_time": _mmss(end),
            "text": "",
            "relevance_score": round(min(0.95, score / 100.0), 2),
            "reasoning": (
                f"Visual selection ({reason_context}): {ep.reason}; window "
                f"{_mmss(start)}–{_mmss(end)} keeps {lead:.0f}s of lead-in and "
                f"{tail:.0f}s after the payoff."),
            "virality_score": int(round(score)),
            "hook_score": int(round(score * 0.9)),
            "engagement_score": int(round(score * 0.7)),
            "value_score": 0,
            "shareability_score": int(round(score * 0.6)),
            "hook_type": ep.event_type,
            "hook_title": None,
            "evidence": {
                "event_type": ep.event_type,
                "signals": {f: round(ep.features[f], 1) for f in FEATURES},
                "agreeing_signals": agree,
                "audio_support_sigma": round(ep.audio_support, 1),
                "parts": ep.parts,
                "action_start_sec": round(ep.start, 1),
                "action_end_sec": round(ep.end, 1),
                "peak_sec": round(ep.peak, 1),
                "lead_in_sec": round(lead, 1),
                "tail_sec": round(tail, 1),
                "confidence": round(confidence, 2),
                "detector": "frame_evidence_v1",
            },
        })
        if ending_rec is not None:
            moments[-1]["evidence"]["ending"] = ending_rec
        if len(moments) >= target_count:
            break
    moments.sort(key=lambda m: m["start_time"])
    return moments
