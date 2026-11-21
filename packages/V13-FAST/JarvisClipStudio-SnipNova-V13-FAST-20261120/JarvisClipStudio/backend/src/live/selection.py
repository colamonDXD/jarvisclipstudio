"""Trainable moment ranking for long streams (the VOD clipper's first stage).

The same function serves the product (``vod.find_candidates``) and the
trainer (``tools/live/train_selection.py``), so what is measured is what runs.

``rank_moments(series, params)`` turns the per-second voice features (vocal
level, pitch, laughter, voiced share) into scored reaction moments:

1. causal z-scores per signal: each second against the median/MAD of the
   ``baseline_sec`` before it (excluding the last ``exclude_sec``), the same
   rule as ``voice_scan.VoiceTrack`` - vectorised with numpy so 11 h ranks in
   about a second;
2. a second is "active" when ``need_active`` signals reach ``active_z``, or
   laughter reaches ``laugh_z``, or the scream rule fires (pitch z >=
   ``scream_f0_z`` with vocal z >= ``scream_vocal_z``);
3. active seconds closer than ``merge_gap`` merge into one moment;
4. score = peak z x (``talk_base`` + ``talk_w`` x talk share around it)
   x (1 + ``length_w`` x min(length, 10) / 10)
   + ``energy_w`` x mean vocal z over the 20 s after the onset.

``DEFAULT_PARAMS`` reproduce the hand-set rules shipped before training. A
trained file (``selection_params.json`` next to this module, or
``LIVE_SELECTION_PARAMS``) overrides them; the trainer writes it only when
held-out streams improve.

Extra features: a series may also carry the ``EXTRA_KEYS`` per-second
strengths (0..1) produced by the optional reaction-feature pass
(``live/sensevoice_scan.py``: the zero-model shout signal and SenseVoice
event/emotion tags). They are not z-scored — they arrive normalised — and
they act only through ``extra_active`` / ``extra_w``, whose defaults keep
them completely inert, so ranking with the keys present (or absent) at
default params is byte-identical to the core three signals alone.
"""
from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any, Dict, List, Optional

import numpy as np

logger = logging.getLogger(__name__)

FLOORS = {"vocal": 2.0, "f0": 18.0, "laugh": 0.12}
EXTRA_KEYS = ("shout", "sv_laugh", "sv_applause", "sv_cheer", "sv_emotion")
PARAMS_FILE = Path(__file__).with_name("selection_params.json")

DEFAULT_PARAMS: Dict[str, float] = {
    "baseline_sec": 240.0, "exclude_sec": 8.0, "min_baseline": 20.0,
    "active_z": 3.0, "need_active": 2.0, "laugh_z": 5.0, "scream_f0_z": 4.0, "scream_vocal_z": 1.5,
    "merge_gap": 3.0, "talk_base": 0.6, "talk_w": 0.8, "length_w": 0.0, "energy_w": 0.0,
    "min_gap": 90.0,
    # Extra features (EXTRA_KEYS, strengths 0..1): a second is extra-active
    # when any extra feature reaches extra_active, and extra_w weights the
    # mean extra strength after the onset into the score. The defaults are
    # inert on purpose (2.0 is unreachable for a 0..1 feature, 0.0 adds
    # nothing); the trainer / an approved params file arms them.
    "extra_active": 2.0, "extra_w": 0.0,
}


def load_params() -> Dict[str, float]:
    path = Path(os.getenv("LIVE_SELECTION_PARAMS") or PARAMS_FILE)
    params = dict(DEFAULT_PARAMS)
    if path.is_file():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            params.update({k: float(v) for k, v in (data.get("params") or {}).items() if k in DEFAULT_PARAMS})
        except Exception as exc:
            logger.warning("Ignoring unreadable selection params %s: %s", path, exc)
    return params


def causal_z(values: np.ndarray, floor: float, baseline: int, exclude: int, min_baseline: int) -> np.ndarray:
    """z[t] = (v[t] - median(v[t-baseline : t-exclude])) / max(1.4826 MAD, floor/4)."""
    n = len(values)
    out = np.zeros(n, dtype=np.float32)
    width = baseline - exclude
    if n == 0 or width < min_baseline:
        return out
    v = np.asarray(values, dtype=np.float64)
    v = np.where(np.isnan(v), np.nanmedian(v) if np.isfinite(v).any() else 0.0, v)
    padded = np.concatenate([np.full(baseline, np.nan), v])
    from numpy.lib.stride_tricks import sliding_window_view

    windows = sliding_window_view(padded, width)[:n]           # windows[t] = v[t-baseline : t-exclude]
    counts = np.sum(~np.isnan(windows), axis=1)
    import warnings

    with np.errstate(all="ignore"), warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)  # the first seconds have an all-NaN baseline
        med = np.nanmedian(windows, axis=1)
        mad = np.nanmedian(np.abs(windows - med[:, None]), axis=1) * 1.4826
    z = (v - med) / np.maximum(mad, floor * 0.25)
    z[counts < min_baseline] = 0.0
    out[:] = np.nan_to_num(z, nan=0.0)
    return out


def zscores(series: Dict[str, List[float]], params: Dict[str, float]) -> Dict[str, np.ndarray]:
    return {k: causal_z(np.asarray(series[k], dtype=np.float64), floor, int(params["baseline_sec"]),
                        int(params["exclude_sec"]), int(params["min_baseline"]))
            for k, floor in FLOORS.items()}


def rank_moments(series: Dict[str, List[float]], params: Optional[Dict[str, float]] = None,
                 z: Optional[Dict[str, np.ndarray]] = None) -> List[Dict[str, Any]]:
    """Scored moments [{onset, end, peak, signals, talk, score}], best first (no spacing)."""
    p = dict(DEFAULT_PARAMS, **(params or {}))
    z = z if z is not None else zscores(series, p)
    n = min(len(z["vocal"]), len(z["f0"]), len(z["laugh"]))
    if n == 0:
        return []
    zv, zf, zl = z["vocal"][:n], z["f0"][:n], z["laugh"][:n]
    active_count = (zv >= p["active_z"]).astype(int) + (zf >= p["active_z"]).astype(int) + (zl >= p["active_z"]).astype(int)
    scream = (zf >= p["scream_f0_z"]) & (zv >= p["scream_vocal_z"])
    active = (active_count >= int(round(p["need_active"]))) | (zl >= p["laugh_z"]) | scream
    # Extra features (shout / SenseVoice tags), when the series carries them:
    # their max strength can make a second active, under extra_active alone.
    extra_arrays: Dict[str, np.ndarray] = {}
    extra = None
    for k in EXTRA_KEYS:
        values = series.get(k)
        if values:
            arr = np.asarray(values, dtype=np.float64)
            if len(arr) < n:
                arr = np.concatenate([arr, np.zeros(n - len(arr))])
            extra_arrays[k] = arr[:n]
    if extra_arrays:
        extra = np.maximum.reduce(list(extra_arrays.values()))
        active = active | (extra >= p["extra_active"])
    idx = np.flatnonzero(active)
    if len(idx) == 0:
        return []
    peak_all = np.maximum(np.maximum(zv, zf), zl)
    voiced = np.asarray(series.get("voiced_frac") or [0.5] * n, dtype=np.float64)[:n]
    talking = (voiced >= 0.25).astype(np.float64)
    talk_cum = np.concatenate([[0.0], np.cumsum(talking)])
    zv_cum = np.concatenate([[0.0], np.cumsum(np.clip(zv, 0, None))])
    extra_cum = np.concatenate([[0.0], np.cumsum(extra)]) if extra is not None else None
    moments = []
    start = prev = int(idx[0])
    for t in list(idx[1:]) + [None]:
        if t is not None and t - prev <= p["merge_gap"]:
            prev = int(t)
            continue
        seg = slice(start, prev + 1)
        peak = float(np.max(peak_all[seg]))
        lo, hi = max(0, start - 5), min(n, start + 25)
        talk = float((talk_cum[hi] - talk_cum[lo]) / max(1, hi - lo))
        e_hi = min(n, start + 20)
        energy = float((zv_cum[e_hi] - zv_cum[start]) / max(1, e_hi - start))
        length = prev - start + 1
        score = (peak * (p["talk_base"] + p["talk_w"] * talk) * (1.0 + p["length_w"] * min(length, 10) / 10.0)
                 + p["energy_w"] * energy)
        if extra_cum is not None and p["extra_w"]:
            score += p["extra_w"] * float((extra_cum[e_hi] - extra_cum[start]) / max(1, e_hi - start))
        signals = sorted({name for name, arr in (("vocal", zv), ("f0", zf), ("laugh", zl))
                          if float(np.max(arr[seg])) >= p["active_z"]}
                         | ({"scream"} if bool(np.any(scream[seg])) else set()))
        if extra_arrays and p["extra_active"] <= 1.0:
            signals = sorted(set(signals) | {k for k, arr in extra_arrays.items()
                                             if float(np.max(arr[seg])) >= p["extra_active"]})
        moments.append({"onset": float(start), "end": float(prev), "peak": peak, "signals": signals,
                        "talk": round(talk, 2), "score": float(score)})
        if t is not None:
            start = prev = int(t)
    moments.sort(key=lambda m: -m["score"])
    return moments


def spaced(moments: List[Dict[str, Any]], limit: int, min_gap: float) -> List[Dict[str, Any]]:
    picked: List[Dict[str, Any]] = []
    for m in moments:
        if all(abs(m["onset"] - q["onset"]) >= min_gap for q in picked):
            picked.append(m)
        if len(picked) >= limit:
            break
    return picked
