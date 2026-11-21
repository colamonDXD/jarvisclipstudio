"""Beat-grid analysis and fast-pace EDL consumption for the auto-director.

Football / anime / music edits are cut on the music, not on the speech:
the payoff lands on the drop and the cuts sit on beats. This module is
the machinery behind the profiles that opt in through a ``beat_grid``
profile section (football_edit, anime_funk_edit, music_artist, gaming,
sports). Every other profile's EDL is untouched by it.

Three layers, deliberately separated:

* **Analysis** — :func:`analyse_audio_beats` turns an audio file (the
  profile's music track, or the source itself for music-led content)
  into ``{tempo_bpm, beat_times, downbeat_offset, drop_time,
  energy_curve}`` with spectral-flux onset DSP in numpy — the same
  approach :mod:`reference_style` uses for its tempo read, extended
  with phase tracking so individual beats are located, not just the
  tempo. Results cache in a ``<file>.beat_cache.json`` sidecar keyed
  by size + mtime (the reframe-plan pattern) plus an in-process memo.

* **Clip preparation** — :func:`prepare_clip_grid` projects a track- or
  source-timeline grid onto one clip's timeline (music tracks loop from
  the clip start; source grids shift by the clip's source start), and
  :func:`resolve_clip_grid` does the file resolution the pipeline seam
  needs. Pure and unit-testable.

* **Consumption** — :func:`consume_beat_grid` rewrites an EDL segment
  list: boundaries snap to beats, the drop anchors the payoff segment
  and post-drop lengths quantise to beat multiples in the profile's
  cuts-per-minute band, and speed ramps are assigned — slowed build
  into the drop, rushed low-energy spans after it. A profile may
  instead select one of the six named velocity curves
  (``beat_grid.velocity_curve``): the curve then replaces the
  build/rush ramps as the clip's speed treatment, stamped onto the
  (snapped/quantised) segments, while the drop anchor still holds.
  Every ramp carries a reason and a timestamp; :func:`sanitize_speed_ramps`
  is the restraint validator that strips any ramp failing that law,
  reusing the Viral Intelligence Engine's reason check.
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any, Optional

import numpy as np

logger = logging.getLogger(__name__)

BEAT_CACHE_SCHEMA_VERSION = 1

# Onset-envelope DSP constants — the reference_style conventions (16 kHz
# mono decode, 2048/512 STFT) so the two analysers agree on what a beat
# is worth.
_AUDIO_RATE = 16000
_FRAME = 2048
_HOP = 512
_ENERGY_WINDOW_SEC = 0.05
_TEMPO_MIN_BPM = 60.0
_TEMPO_MAX_BPM = 200.0
_TEMPO_STRENGTH_MIN = 1.8

# Consumption defaults (profile ``beat_grid`` keys override).
DEFAULT_SNAP_TOLERANCE_SEC = 0.12
DEFAULT_BUILD_SPEED = 0.72
DEFAULT_RUSH_SPEED = 1.35
_MIN_RAMP_SPEED = 0.4
_MAX_RAMP_SPEED = 2.0
_RUSH_MIN_SEGMENT_SEC = 1.0
_RUSH_MAX_FRACTION = 0.5
_DROP_MIN_TAIL_SEC = 1.0


# ---------------------------------------------------------------------------
# Analysis: onset envelope, tempo, beat tracking, drop, energy
# ---------------------------------------------------------------------------


def onset_envelope(samples: np.ndarray) -> tuple[np.ndarray, float]:
    """Spectral-flux onset envelope. Returns (flux, envelope_rate_hz).

    ``flux[i]`` is the onset strength at time ``i / rate`` — frame zero
    is padded so indices line up with sample time directly.
    """
    env_rate = _AUDIO_RATE / _HOP
    if samples is None or len(samples) < _FRAME * 2:
        return np.zeros(0, dtype=np.float64), env_rate
    window = np.hanning(_FRAME)
    mags = []
    for start in range(0, len(samples) - _FRAME, _HOP):
        mags.append(np.abs(np.fft.rfft(samples[start:start + _FRAME] * window)))
    if len(mags) < 8:
        return np.zeros(0, dtype=np.float64), env_rate
    mags_arr = np.array(mags)
    diffs = np.maximum(0.0, np.diff(mags_arr, axis=0)).sum(axis=1)
    flux = np.concatenate([[0.0], diffs])
    return flux.astype(np.float64), env_rate


def _parabolic_peak(values: np.ndarray, index: int) -> float:
    """Sub-sample offset of a peak by parabolic interpolation."""
    if index <= 0 or index >= len(values) - 1:
        return 0.0
    left, centre, right = values[index - 1], values[index], values[index + 1]
    denom = left - 2.0 * centre + right
    if denom == 0:
        return 0.0
    offset = 0.5 * (left - right) / denom
    return float(min(1.0, max(-1.0, offset)))


def estimate_tempo(
    flux: np.ndarray, env_rate: float
) -> tuple[Optional[float], Optional[float], Optional[float]]:
    """Tempo from onset-envelope autocorrelation.

    Returns (bpm, period_seconds, strength). The autocorrelation peak
    is parabolically interpolated — at a 32 ms envelope hop the raw lag
    quantisation alone would read a 140 BPM track anywhere in
    135–145 BPM. A halving check guards the classic octave error: when
    the half-lag peak is nearly as strong, the shorter period wins.
    """
    if len(flux) < 32 or float(np.std(flux)) <= 0:
        return None, None, None
    # A 3-tap smoothing before the autocorrelation matters: at a
    # non-integer frame period (140 BPM = 13.4 frames) consecutive
    # onsets alternate 13/14-frame spacings, so the raw autocorrelation
    # aligns coherently at 2x the period while the true lag straddles
    # two integers and reads low — the classic half-tempo trap.
    smoothed = np.convolve(flux, np.array([0.25, 0.5, 0.25]), mode="same")
    centred = smoothed - smoothed.mean()
    autocorr = np.correlate(centred, centred, mode="full")[len(centred) - 1:]
    min_lag = max(1, int(env_rate * 60.0 / _TEMPO_MAX_BPM))
    max_lag = min(len(autocorr) - 1, int(env_rate * 60.0 / _TEMPO_MIN_BPM))
    if max_lag <= min_lag:
        return None, None, None
    segment = autocorr[min_lag:max_lag + 1]
    peak = int(np.argmax(segment)) + min_lag
    # Octave guard: prefer half the lag when the (interpolated)
    # autocorrelation there carries >= 88% of the peak — repeated once
    # for a possible double halving.
    lag_axis = np.arange(len(autocorr), dtype=np.float64)
    for _ in range(2):
        half = peak / 2.0
        if half < min_lag:
            break
        half_value = float(np.interp(half, lag_axis, autocorr))
        if half_value >= 0.88 * autocorr[peak]:
            peak = int(round(half))
        else:
            break
    floor = float(np.median(segment)) if len(segment) else 0.0
    denom = floor if floor > 0 else 0.02 * float(autocorr[0])
    strength = float(autocorr[peak] / denom) if denom > 0 else None
    if (
        strength is None
        or strength < _TEMPO_STRENGTH_MIN
        or autocorr[peak] < 0.1 * autocorr[0]
    ):
        return None, None, strength
    lag = peak + _parabolic_peak(autocorr, peak)
    period = lag / env_rate
    return round(60.0 / period, 1), period, strength


def track_beats(
    flux: np.ndarray, env_rate: float, period_sec: float
) -> list[float]:
    """Locate individual beats for a known period.

    A small grid search over period (±3%) and phase maximises the mean
    onset strength under the grid; each beat then snaps to the local
    flux maximum with parabolic sub-frame refinement, so beat times
    land within a few ms of the true onsets instead of the 32 ms
    envelope quantisation.
    """
    if len(flux) == 0 or period_sec <= 0:
        return []
    period_frames = period_sec * env_rate
    frame_index = np.arange(len(flux), dtype=np.float64)
    best: Optional[tuple[float, float, float]] = None
    for scale in np.linspace(0.97, 1.03, 25):
        p = period_frames * float(scale)
        n_beats = int(len(flux) / p)
        if n_beats < 4:
            continue
        for phase in np.linspace(0.0, p, 48, endpoint=False):
            positions = phase + np.arange(n_beats) * p
            score = float(np.mean(np.interp(positions, frame_index, flux)))
            if best is None or score > best[0]:
                best = (score, float(phase), float(p))
    if best is None:
        return []
    _, phase, p = best
    # An STFT frame's honest timestamp is its window CENTRE: frame i
    # covers [i*hop, i*hop + FRAME], and its flux peak fires when the
    # onset crosses the Hann-weighted middle. Measured on an isolated
    # click, centre timestamping leaves a ~15 ms residual; start-of-
    # frame timestamping reads a systematic ~80 ms early.
    centre_offset = _FRAME / (2.0 * _AUDIO_RATE)
    beats: list[float] = []
    k = 0
    while True:
        centre = phase + k * p
        if centre >= len(flux):
            break
        lo = max(0, int(centre - 0.3 * p))
        hi = min(len(flux) - 1, int(centre + 0.3 * p))
        if hi < lo:
            break
        snap = lo + int(np.argmax(flux[lo:hi + 1]))
        refined = (snap + _parabolic_peak(flux, snap)) / env_rate + centre_offset
        if not beats or refined - beats[-1] >= 0.5 * period_sec:
            beats.append(round(float(refined), 3))
        k += 1
    return beats


def energy_levels_db(samples: np.ndarray) -> np.ndarray:
    """50 ms window levels in dBFS (the gap_activity window convention)."""
    size = int(_AUDIO_RATE * _ENERGY_WINDOW_SEC)
    count = len(samples) // size
    if count == 0:
        return np.zeros(0, dtype=np.float64)
    blocks = samples[: count * size].reshape(count, size)
    return 20.0 * np.log10(np.sqrt((blocks ** 2).mean(axis=1)) + 1e-6)


def find_drop_time(
    levels_db: np.ndarray,
    beat_times: list[float],
    duration: float,
    *,
    hop_sec: float = _ENERGY_WINDOW_SEC,
) -> Optional[float]:
    """The drop: the beat where a quieter build bursts into loud payoff.

    Scores every beat in the middle stretch of the track by
    (mean level of the 2 s after) − (mean level of the 4 s before) and
    takes the best, provided the burst is real (>= 4 dB) and the payoff
    is genuinely loud (>= 55th percentile of the whole track). Returns
    None when nothing qualifies — a track without a drop gets no
    drop-anchored edit rather than a fake one.
    """
    if len(levels_db) == 0 or not beat_times or duration <= 0:
        return None

    # Power-domain means (the codebase's _power_mean_db convention):
    # an arithmetic mean of dB values lets the silent windows between
    # hits dominate and buries exactly the burst being measured.
    power = np.power(10.0, np.asarray(levels_db, dtype=np.float64) / 10.0)

    def mean_level(t0: float, t1: float) -> Optional[float]:
        i0 = max(0, int(t0 / hop_sec))
        i1 = min(len(power), max(i0 + 1, int(t1 / hop_sec)))
        if i1 <= i0:
            return None
        return float(10.0 * np.log10(np.mean(power[i0:i1]) + 1e-12))

    loud_floor = float(10.0 * np.log10(np.percentile(power, 55) + 1e-12))
    best_t: Optional[float] = None
    best_score = 0.0
    for t in beat_times:
        if t < 0.12 * duration or t > duration - 2.2:
            continue
        before = mean_level(t - 4.0, t - 0.2)
        after = mean_level(t, t + 2.0)
        if before is None or after is None:
            continue
        if after < loud_floor:
            continue
        score = after - before
        if score > best_score:
            best_score = score
            best_t = t
    if best_t is None or best_score < 4.0:
        return None
    return round(float(best_t), 3)


def _downbeat_offset(flux: np.ndarray, env_rate: float, beat_times: list[float]) -> float:
    """First downbeat: the bar phase (of 4) carrying the most onset energy."""
    if not beat_times:
        return 0.0
    if len(beat_times) < 4:
        return float(beat_times[0])
    frame_index = np.arange(len(flux), dtype=np.float64)
    best_phase, best_score = 0, float("-inf")
    for phase in range(4):
        positions = np.array([t * env_rate for t in beat_times[phase::4]])
        score = float(np.sum(np.interp(positions, frame_index, flux)))
        if score > best_score:
            best_phase, best_score = phase, score
    return float(beat_times[best_phase])


def analyse_beats_from_samples(samples: np.ndarray) -> dict[str, Any]:
    """Full beat analysis of decoded mono 16 kHz samples (pure, testable)."""
    duration = len(samples) / _AUDIO_RATE if samples is not None else 0.0
    flux, env_rate = onset_envelope(samples)
    bpm, period, strength = estimate_tempo(flux, env_rate)
    beats = track_beats(flux, env_rate, period) if period else []
    levels = energy_levels_db(samples) if samples is not None else np.zeros(0)
    drop = find_drop_time(levels, beats, duration) if beats else None
    return {
        "version": BEAT_CACHE_SCHEMA_VERSION,
        "tempo_bpm": bpm,
        "tempo_strength": round(strength, 2) if strength is not None else None,
        "beat_times": beats,
        "downbeat_offset": _downbeat_offset(flux, env_rate, beats),
        "drop_time": drop,
        "energy_curve": {
            "hop_sec": _ENERGY_WINDOW_SEC,
            "levels_db": [round(float(v), 1) for v in levels],
        },
        "duration": round(float(duration), 3),
    }


# ---------------------------------------------------------------------------
# File analysis with the sidecar cache (reframe-plan pattern)
# ---------------------------------------------------------------------------

_PROCESS_CACHE: dict[tuple[str, int, int], dict[str, Any]] = {}


def beat_cache_path(path: Path) -> Path:
    return Path(path).with_suffix(".beat_cache.json")


def _source_stamp(path: Path) -> Optional[tuple[int, int]]:
    try:
        stat = Path(path).stat()
    except OSError:
        return None
    return stat.st_size, stat.st_mtime_ns


def _load_disk_cache(path: Path) -> Optional[dict[str, Any]]:
    cache_file = beat_cache_path(path)
    if not cache_file.is_file():
        return None
    stamp = _source_stamp(path)
    if stamp is None:
        return None
    try:
        payload = json.loads(cache_file.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(payload, dict):
        return None
    if payload.get("version") != BEAT_CACHE_SCHEMA_VERSION:
        return None
    if payload.get("source_size") != stamp[0] or payload.get("source_mtime_ns") != stamp[1]:
        return None
    return payload


def _save_disk_cache(path: Path, payload: dict[str, Any]) -> None:
    """Best-effort atomic write; a read-only assets mount just skips it."""
    stamp = _source_stamp(path)
    if stamp:
        payload = {**payload, "source_size": stamp[0], "source_mtime_ns": stamp[1]}
    cache_file = beat_cache_path(path)
    tmp = cache_file.with_name(cache_file.name + ".tmp")
    try:
        tmp.write_text(json.dumps(payload), encoding="utf-8")
        os.replace(tmp, cache_file)
    except OSError as exc:
        logger.debug("Could not write beat cache %s: %s", cache_file, exc)
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass


def analyse_audio_beats(path: Path) -> Optional[dict[str, Any]]:
    """Beat analysis of an audio/video file, cached; None when unusable.

    Decoding reuses the reference-style analyser's mono 16 kHz decode so
    both analysers hear the same signal. Any failure (no audio stream,
    no tempo found is NOT a failure — it returns a grid with no beats)
    degrades to None so callers fall back to today's behaviour.
    """
    path = Path(path)
    stamp = _source_stamp(path)
    if stamp is not None:
        key = (str(path), stamp[0], stamp[1])
        if key in _PROCESS_CACHE:
            return _PROCESS_CACHE[key]
    else:
        key = None
    cached = _load_disk_cache(path)
    if cached is not None:
        result = {k: v for k, v in cached.items() if not k.startswith("source_")}
        if key is not None:
            _PROCESS_CACHE[key] = result
        return result
    try:
        from .reference_style import _decode_audio

        samples = _decode_audio(path)
    except Exception as exc:
        logger.warning("Beat analysis decode failed for %s: %s", path, exc)
        return None
    if samples is None or len(samples) == 0:
        return None
    result = analyse_beats_from_samples(samples)
    _save_disk_cache(path, result)
    if key is not None:
        _PROCESS_CACHE[key] = result
    return result


# ---------------------------------------------------------------------------
# Clip preparation: project a grid onto one clip's timeline
# ---------------------------------------------------------------------------


def prepare_clip_grid(
    grid: Optional[dict[str, Any]],
    *,
    mode: str,
    clip_start: float = 0.0,
    clip_duration: float,
) -> Optional[dict[str, Any]]:
    """Project an analysed grid onto a clip timeline.

    ``mode="track"``: a music track that starts at the clip start and
    loops (the renderer's ``-stream_loop`` behaviour), so beats and
    energy repeat every track duration. ``mode="source"``: the grid
    lives on the source timeline and the clip is the window starting
    at ``clip_start``. Returns None when the grid carries no usable
    tempo/beats.
    """
    if not grid or not grid.get("tempo_bpm") or not grid.get("beat_times"):
        return None
    if clip_duration <= 0:
        return None
    tempo = float(grid["tempo_bpm"])
    curve = grid.get("energy_curve") or {}
    hop = float(curve.get("hop_sec") or _ENERGY_WINDOW_SEC)
    levels = list(curve.get("levels_db") or [])
    beats: list[float] = []
    drop: Optional[float] = None
    out_levels: list[float] = []
    if mode == "track":
        track_duration = float(grid.get("duration") or 0)
        if track_duration <= 0:
            return None
        loops = int(clip_duration // track_duration) + 1
        for k in range(loops):
            offset = k * track_duration
            beats.extend(
                round(b + offset, 3)
                for b in grid["beat_times"]
                if b + offset <= clip_duration + hop
            )
            out_levels.extend(levels)
        out_levels = out_levels[: int(clip_duration / hop) + 1]
        raw_drop = grid.get("drop_time")
        if raw_drop is not None and float(raw_drop) <= clip_duration:
            drop = round(float(raw_drop), 3)
    else:  # source window
        end = clip_start + clip_duration
        beats = [
            round(b - clip_start, 3)
            for b in grid["beat_times"]
            if clip_start - hop <= b <= end + hop
        ]
        i0 = max(0, int(clip_start / hop))
        i1 = min(len(levels), int(end / hop) + 1)
        out_levels = levels[i0:i1]
        raw_drop = grid.get("drop_time")
        if raw_drop is not None and clip_start <= float(raw_drop) <= end:
            drop = round(float(raw_drop) - clip_start, 3)
    beats = sorted({b for b in beats if -hop <= b <= clip_duration + hop})
    if not beats:
        return None
    return {
        "tempo_bpm": tempo,
        "beat_times": beats,
        "drop_time": drop,
        "downbeat_offset": grid.get("downbeat_offset"),
        "energy_hop_sec": hop,
        "energy_levels_db": out_levels,
        "source": "music" if mode == "track" else "source",
    }


def resolve_clip_grid(
    profile: dict[str, Any],
    *,
    music_override: Optional[str],
    source_path: Optional[Path],
    clip_start: float,
    clip_duration: float,
) -> Optional[dict[str, Any]]:
    """The pipeline seam's grid: resolve the audio, analyse, project.

    Music mode resolves the track exactly like the renderer's audio
    finisher does (bundled library first, then the user's uploads) so
    the grid always describes the track that will actually be mixed.
    Any failure returns None — no grid, no beat consumption, today's
    EDL.
    """
    cfg = profile_beat_config(profile)
    if cfg is None:
        return None
    mode = str(cfg.get("source", "music"))
    try:
        if mode == "source":
            if source_path is None:
                return None
            grid = analyse_audio_beats(Path(source_path))
            return prepare_clip_grid(
                grid, mode="source", clip_start=clip_start,
                clip_duration=clip_duration,
            )
        audio = profile.get("audio", {})
        track_name = str(
            music_override
            or audio.get("music_track")
            or audio.get("music_mood")
            or ""
        ).strip()
        if not track_name or track_name.lower() == "none":
            return None
        from ..media.edl_render import ASSETS_DIR, _resolve_asset_file, _resolve_user_music_file

        track_path = _resolve_asset_file(ASSETS_DIR / "music", track_name)
        if track_path is None:
            track_path = _resolve_user_music_file(track_name)
        if track_path is None:
            return None
        grid = analyse_audio_beats(track_path)
        return prepare_clip_grid(
            grid, mode="track", clip_start=0.0, clip_duration=clip_duration
        )
    except Exception as exc:
        logger.warning("Beat grid unavailable for this clip: %s", exc)
        return None


# ---------------------------------------------------------------------------
# Consumption: snap, drop anchor, quantise, ramps
# ---------------------------------------------------------------------------


def profile_beat_config(profile: Optional[dict[str, Any]]) -> Optional[dict[str, Any]]:
    """The profile's beat_grid section when it opts in, else None."""
    if not isinstance(profile, dict):
        return None
    cfg = profile.get("beat_grid")
    if not isinstance(cfg, dict) or not cfg.get("enabled"):
        return None
    return cfg


def velocity_curve_name(cfg: Optional[dict[str, Any]]) -> Optional[str]:
    """The profile's selected velocity curve, when it names a real one.

    Optional ``beat_grid.velocity_curve`` — one of the six names in
    ``velocity_curves.CURVE_NAMES``. Missing, empty or unknown degrades
    to None: the profile keeps today's build/rush ramps, byte for byte.
    A curve name never costs the clip.
    """
    if not isinstance(cfg, dict):
        return None
    from .velocity_curves import CURVE_NAMES

    name = cfg.get("velocity_curve")
    if isinstance(name, str) and name.strip() in CURVE_NAMES:
        return name.strip()
    return None


def _boundaries(segments: list[dict]) -> list[float]:
    if not segments:
        return []
    return [float(segments[0]["start"])] + [float(s["end"]) for s in segments]


def _rebuild_segments(
    segments: list[dict], boundaries: list[float]
) -> list[dict]:
    """Re-tile segments over new boundaries, preserving per-segment fields.

    Each output segment inherits the fields of the original segment its
    start falls in (transition choices travel with the footage).
    """
    out: list[dict] = []
    for i in range(len(boundaries) - 1):
        start, end = boundaries[i], boundaries[i + 1]
        if end - start <= 0.05:
            continue
        template = segments[-1]
        for seg in segments:
            if float(seg["start"]) <= start < float(seg["end"]):
                template = seg
                break
        entry = dict(template)
        entry.update({
            "index": len(out),
            "start": round(start, 3),
            "end": round(end, 3),
            "duration": round(end - start, 3),
        })
        if out and float(out[-1]["end"]) != round(start, 3):
            out[-1]["end"] = round(start, 3)
            out[-1]["duration"] = round(start - float(out[-1]["start"]), 3)
        out.append(entry)
    return out


def snap_segments_to_beats(
    segments: list[dict],
    beats: list[float],
    tolerance: float = DEFAULT_SNAP_TOLERANCE_SEC,
) -> tuple[list[dict], int]:
    """Move internal boundaries to the nearest beat within tolerance.

    The clip start (0) and end never move — they are the user's chosen
    window. A snap is skipped when it would crush a neighbouring
    segment under 0.2 s. Returns (segments, number_of_snapped).
    """
    if len(segments) < 2 or not beats:
        return segments, 0
    bounds = _boundaries(segments)
    snapped = 0
    for i in range(1, len(bounds) - 1):
        current = bounds[i]
        nearest = min(beats, key=lambda b: abs(b - current))
        if abs(nearest - current) > tolerance:
            continue
        if nearest - bounds[i - 1] < 0.2 or bounds[i + 1] - nearest < 0.2:
            continue
        if nearest != current:
            bounds[i] = nearest
            snapped += 1
    if not snapped:
        return segments, 0
    return _rebuild_segments(segments, bounds), snapped


def _quantise_post_drop(
    segments: list[dict],
    beats: list[float],
    drop: float,
    period: float,
    cpm_band: tuple[float, float],
    duration: float,
) -> list[dict]:
    """Rebuild the post-drop segmentation on beat multiples.

    The segment containing the drop is split so a segment starts
    exactly at the drop; from there, boundaries land every
    ``beats_per_segment`` beats, with the step chosen so the resulting
    cuts-per-minute sits inside the profile's band.
    """
    lo, hi = cpm_band
    mid = (lo + hi) / 2.0 if hi > lo > 0 else 30.0
    beats_per = max(1, round((60.0 / mid) / period))
    cpm = 60.0 / (beats_per * period)
    if cpm > hi and hi > 0:
        beats_per += 1
    elif cpm < lo and beats_per > 1:
        beats_per -= 1
    step = beats_per * period

    pre = [b for b in _boundaries(segments) if b < drop - 1e-6]
    bounds = sorted({round(b, 3) for b in pre if 0 < b < duration})
    bounds.append(round(drop, 3))
    beat_index = min(range(len(beats)), key=lambda i: abs(beats[i] - drop))
    j = beat_index
    while True:
        j += beats_per
        if j >= len(beats):
            break
        b = beats[j]
        if b >= duration - 0.5 * step:
            break
        bounds.append(round(b, 3))
    bounds.append(round(duration, 3))
    full = [0.0] + bounds + []
    full = sorted({b for b in full if 0.0 <= b <= duration})
    if full[0] != 0.0:
        full.insert(0, 0.0)
    if full[-1] != round(duration, 3):
        full.append(round(duration, 3))
    return _rebuild_segments(segments, full)


def _segment_energy(
    levels: list[float], hop: float, start: float, end: float
) -> Optional[float]:
    """Power-domain mean level of a segment window, in dB."""
    if not levels or hop <= 0:
        return None
    i0 = max(0, int(start / hop))
    i1 = min(len(levels), max(i0 + 1, int(end / hop)))
    if i1 <= i0:
        return None
    power = np.power(10.0, np.asarray(levels[i0:i1], dtype=np.float64) / 10.0)
    return float(10.0 * np.log10(np.mean(power) + 1e-12))


def assign_speed_ramps(
    segments: list[dict],
    clip_grid: dict[str, Any],
    cfg: dict[str, Any],
    duration: float,
) -> list[dict]:
    """Profile-driven speed ramps around the drop (restraint-law shaped).

    * Build segments (ending at or before the drop) slow to
      ``build_speed`` — the dark slowed build of the viral grammar.
    * Post-drop segments whose mean energy sits in the clip's quietest
      40% rush at ``rush_speed`` — dead air after the drop kills the
      payoff pace.

    Every ramp records ``speed``, ``speed_reason`` (content reason) and
    ``speed_at`` (its timestamp). At most half the segments may ramp.
    """
    drop = clip_grid.get("drop_time")
    if drop is None or not cfg.get("drop_anchor"):
        return segments
    try:
        build_speed = float(cfg.get("build_speed", DEFAULT_BUILD_SPEED))
        rush_speed = float(cfg.get("rush_speed", DEFAULT_RUSH_SPEED))
    except (TypeError, ValueError):
        return segments
    build_speed = min(0.9, max(_MIN_RAMP_SPEED, build_speed))
    rush_speed = max(1.05, min(_MAX_RAMP_SPEED, rush_speed))
    levels = list(clip_grid.get("energy_levels_db") or [])
    hop = float(clip_grid.get("energy_hop_sec") or _ENERGY_WINDOW_SEC)
    quiet_cut: Optional[float] = None
    if levels:
        arr = np.array(levels, dtype=np.float64)
        # "Quiet" must mean audibly below the clip's typical level: the
        # bare 40th percentile IS the loud level whenever less than 40%
        # of the clip is quiet, so cap the cut 6 dB under the median.
        quiet_cut = min(
            float(np.percentile(arr, 40)), float(np.median(arr)) - 6.0
        )

    max_ramps = max(1, int(len(segments) * _RUSH_MAX_FRACTION))
    ramped = 0
    for seg in segments:
        if ramped >= max_ramps:
            break
        start, end = float(seg["start"]), float(seg["end"])
        if end <= drop + 1e-6 and start < drop - 1e-6:
            seg["speed"] = build_speed
            seg["speed_reason"] = (
                f"Slowed build into the drop at {drop:.1f}s"
            )
            seg["speed_at"] = round(start, 3)
            ramped += 1
        elif start >= drop + 1e-6 and quiet_cut is not None:
            # The segment starting AT the drop is the payoff itself —
            # it never rushes.
            energy = _segment_energy(levels, hop, start, end)
            if (
                energy is not None
                and energy <= quiet_cut
                and end - start >= _RUSH_MIN_SEGMENT_SEC
            ):
                seg["speed"] = rush_speed
                seg["speed_reason"] = (
                    f"Low-energy span at {start:.1f}s rushed to hold "
                    "the post-drop pace"
                )
                seg["speed_at"] = round(start, 3)
                ramped += 1
    return segments


def sanitize_speed_ramps(segments: list[dict]) -> list[dict]:
    """Restraint validator: a ramp without a real reason does not ship.

    Mirrors the Viral Intelligence Engine's law — every effect carries
    a content reason and an exact timestamp, or it is dropped back to
    1.0x. Reasons are checked with the plan validator's own
    boilerplate screen so the two systems cannot drift apart.
    """
    try:
        from .clip_plan import _reason_ok
    except Exception:
        return segments
    for seg in segments:
        if "speed" not in seg:
            continue
        try:
            speed = float(seg.get("speed"))
        except (TypeError, ValueError):
            speed = 1.0
        at = seg.get("speed_at")
        ok = (
            _MIN_RAMP_SPEED <= speed <= _MAX_RAMP_SPEED
            and speed != 1.0
            and isinstance(at, (int, float))
            and _reason_ok(seg.get("speed_reason"))
        )
        if not ok:
            for key in ("speed", "speed_reason", "speed_at"):
                seg.pop(key, None)
    return segments


def consume_beat_grid(
    segments: list[dict],
    clip_grid: dict[str, Any],
    cfg: dict[str, Any],
    profile: dict[str, Any],
    clip_duration: float,
) -> tuple[list[dict], dict[str, Any]]:
    """Apply a clip grid to a freshly built segment list.

    Returns (segments, info) where info is the summary the EDL records
    under its ``beat_grid`` key — tempo, what was snapped, where the
    drop sits — so the edit's beat decisions stay inspectable.

    A profile-selected velocity curve (``beat_grid.velocity_curve``)
    replaces the drop-anchored build/rush ramps as the clip's speed
    treatment: the curve is stamped onto the snapped/quantised
    segments and recorded as ``info["velocity_curve"]``. The curve
    does not need a drop — it also applies when ``drop_anchor`` is
    off — but it does need a resolved grid: with no grid,
    ``build_edl`` never calls this function at all. Unset or unknown
    curve name: today's behaviour, unchanged.
    """
    beats = [float(b) for b in clip_grid.get("beat_times") or []]
    tempo = float(clip_grid.get("tempo_bpm") or 0)
    try:
        tolerance = float(cfg.get("snap_tolerance_sec", DEFAULT_SNAP_TOLERANCE_SEC))
    except (TypeError, ValueError):
        tolerance = DEFAULT_SNAP_TOLERANCE_SEC
    segments, snapped = snap_segments_to_beats(segments, beats, tolerance)
    info: dict[str, Any] = {
        "tempo_bpm": tempo,
        "source": clip_grid.get("source"),
        "snapped_boundaries": snapped,
    }
    curve_name = velocity_curve_name(cfg)
    drop = clip_grid.get("drop_time")
    if (
        cfg.get("drop_anchor")
        and drop is not None
        and tempo > 0
        and 0 < float(drop) < clip_duration - _DROP_MIN_TAIL_SEC
    ):
        pacing = profile.get("pacing", {})
        band = pacing.get("target_cuts_per_min") or [25, 40]
        try:
            cpm_band = (float(band[0]), float(band[1]))
        except (TypeError, ValueError, IndexError):
            cpm_band = (25.0, 40.0)
        segments = _quantise_post_drop(
            segments, beats, float(drop), 60.0 / tempo, cpm_band, clip_duration
        )
        info["drop_time"] = round(float(drop), 3)
        info["drop_anchored"] = True
        if curve_name is None:
            segments = assign_speed_ramps(segments, clip_grid, cfg, clip_duration)
    if curve_name is not None:
        from .velocity_curves import stamp_velocity_curve

        segments = stamp_velocity_curve(segments, curve_name)
        info["velocity_curve"] = curve_name
    segments = sanitize_speed_ramps(segments)
    ramped = sum(1 for s in segments if "speed" in s)
    if ramped:
        info["speed_ramps"] = ramped
    return segments, info


# ---------------------------------------------------------------------------
# Silence tightening (fast-pace profiles): word-gap keep-range surgery
# ---------------------------------------------------------------------------


def tighten_keep_ranges(
    keep_ranges: list[tuple[float, float]],
    words: list[dict[str, Any]],
    *,
    max_gap_sec: float = 0.6,
    breath_sec: float = 0.15,
) -> list[tuple[float, float]]:
    """Drop intra-clip silent spans longer than ``max_gap_sec``.

    ``words`` are source-timeline word timings; a silent span is the
    gap between two consecutive words inside a keep range (or between
    a range edge and its nearest word). Cuts only ever land in the
    middle of a gap — a word is never cut — and ``breath_sec`` of air
    is kept on both sides so the join does not sound clipped. Ranges
    holding fewer than two words pass through unchanged: with no word
    evidence there is nothing honest to cut.
    """
    timed = sorted(
        (float(w["start"]), float(w["end"]))
        for w in words or []
        if isinstance(w, dict)
        and isinstance(w.get("start"), (int, float))
        and isinstance(w.get("end"), (int, float))
        and float(w["end"]) > float(w["start"])
    )
    if len(timed) < 2:
        return list(keep_ranges)
    out: list[tuple[float, float]] = []
    for range_start, range_end in keep_ranges:
        inside = [
            (s, e) for s, e in timed if e > range_start and s < range_end
        ]
        if len(inside) < 2:
            out.append((range_start, range_end))
            continue
        cuts: list[tuple[float, float]] = []
        first_start = inside[0][0]
        if first_start - range_start > max_gap_sec:
            cuts.append((range_start, max(range_start, first_start - breath_sec)))
        for (cur_s, cur_e), (nxt_s, nxt_e) in zip(inside, inside[1:]):
            gap = nxt_s - cur_e
            if gap > max_gap_sec:
                cut_start = cur_e + breath_sec
                cut_end = nxt_s - breath_sec
                if cut_end - cut_start >= 0.05:
                    cuts.append((cut_start, cut_end))
        last_end = inside[-1][1]
        if range_end - last_end > max_gap_sec:
            cuts.append((min(range_end, last_end + breath_sec), range_end))
        if not cuts:
            out.append((range_start, range_end))
            continue
        cursor = range_start
        for cut_start, cut_end in sorted(cuts):
            if cut_start - cursor >= 0.12:
                out.append((cursor, cut_start))
            cursor = max(cursor, cut_end)
        if range_end - cursor >= 0.12:
            out.append((cursor, range_end))
    return out
