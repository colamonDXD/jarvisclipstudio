"""Optional reaction-feature pass: SenseVoiceSmall tags + the shout signal.

The core picker (``selection.rank_moments``) hears reactions only through
loudness-shaped features (vocal level, pitch, a laughter heuristic). This
pass adds the missing *semantic* reaction signals as extra per-second
features (``selection.EXTRA_KEYS``), all strengths in 0..1:

* ``shout`` — zero-model DSP, adapted from NeonClipping's ``highlights.py``
  (MIT, see NOTICE): per-second 2-4 kHz energy share x loudness, locally
  z-scored. Needs no weights and no licence beyond MIT.
* ``sv_laugh`` / ``sv_applause`` / ``sv_cheer`` / ``sv_emotion`` —
  SenseVoiceSmall event/emotion tags: laughter, applause, cheering
  (derived: applause under a happy/surprised voice — the model has no
  dedicated cheering token) and non-neutral emotional speech.

The features are ADDITIVE and inert by default: they reach the picker only
when ``SENSEVOICE_FEATURES`` is on, and even then they move the ranking
only through ``selection`` params ``extra_active`` / ``extra_w``, whose
defaults (2.0 / 0.0) leave ranking output byte-identical to the core
signals alone. The switch defaults OFF because the model pass costs CPU
(~1x realtime per window on a laptop CPU) and ~900 MB of weights.

Weights are NOT in git. ``tools/live/fetch_sensevoice.py`` downloads the
official FunAudioLLM/SenseVoiceSmall weights, pinned by SHA-256.
SenseVoiceSmall is by Alibaba Group / FunAudioLLM and is distributed
under the FunASR Model Open Source License Agreement v1.1 — attribution
in NOTICE and ``docs/registries/THIRD_PARTY_LICENSES.md``, licence text
in ``docs/registries/FunASR_MODEL_LICENSE_v1.1.txt``. Inference needs the
optional ``funasr`` package; without it (or without weights) the pass
degrades to shout-only and logs a warning — it never breaks a scan.
"""
from __future__ import annotations

import logging
import math
import os
import re
import subprocess
import tempfile
from pathlib import Path
from typing import Callable, Dict, List, Optional, Protocol

import numpy as np

logger = logging.getLogger(__name__)

SWITCH = "SENSEVOICE_FEATURES"
SV_KEYS = ("sv_laugh", "sv_applause", "sv_cheer", "sv_emotion")
WINDOW_SEC = 5

# SenseVoiceSmall rich-output tokens, e.g.
# "<|en|><|HAPPY|><|Laughter|><|woitn|>text". NEUTRAL and EMO_UNKNOWN are
# deliberately not "emotional speech".
_EVENT_MAP = {"Laughter": "sv_laugh", "Applause": "sv_applause", "Cheering": "sv_cheer"}
_EMOTIONS = {"HAPPY", "SAD", "ANGRY", "FEARFUL", "DISGUSTED", "SURPRISED"}
_TOKEN_RE = re.compile(r"<\|([^|>]+)\|>")


def sensevoice_enabled() -> bool:
    return os.getenv(SWITCH, "off").strip().lower() in ("on", "1", "true", "yes")


def weights_dir() -> Path:
    env = os.getenv("SENSEVOICE_MODEL_DIR")
    if env:
        return Path(env)
    return Path.home() / ".cache" / "snipnova" / "SenseVoiceSmall"


class SenseVoiceUnavailable(RuntimeError):
    """The model pass cannot run (no funasr, or weights not fetched)."""


class WindowTagger(Protocol):
    """Inference boundary: tag strengths 0..1 for one audio window file.

    Tests inject a fake, so the suite never downloads weights or loads a
    model. Keys are a subset of ``SV_KEYS``; missing keys mean 0.
    """

    def tag_window(self, path: Path) -> Dict[str, float]:
        ...


def parse_sensevoice_text(text: str) -> Dict[str, float]:
    """Map one window's rich SenseVoice output to ``SV_KEYS`` strengths."""
    tokens = set(_TOKEN_RE.findall(text or ""))
    out = {k: 0.0 for k in SV_KEYS}
    for token, key in _EVENT_MAP.items():
        if token in tokens:
            out[key] = 1.0
    if tokens & _EMOTIONS:
        out["sv_emotion"] = 1.0
    # Cheering has no dedicated SenseVoice event token: a cheering crowd
    # surfaces as applause under a happy/surprised voice, so derive it.
    if out["sv_applause"] and tokens & {"HAPPY", "SURPRISED"}:
        out["sv_cheer"] = 1.0
    return out


class FunASRTagger:
    """Real SenseVoiceSmall inference via FunASR (optional dependency)."""

    def __init__(self, model_dir: Optional[Path] = None):
        self._dir = Path(model_dir) if model_dir else weights_dir()
        self._model = None

    def _load(self):
        if self._model is None:
            if not (self._dir / "model.pt").is_file():
                raise SenseVoiceUnavailable(
                    f"SenseVoiceSmall weights not found in {self._dir} "
                    "(run tools/live/fetch_sensevoice.py)")
            try:
                from funasr import AutoModel
            except ImportError as exc:
                raise SenseVoiceUnavailable(
                    "funasr is not installed (pip install funasr kaldi-native-fbank)") from exc
            self._model = AutoModel(model=str(self._dir), device="cpu",
                                    disable_update=True, disable_pbar=True, disable_log=True)
        return self._model

    def tag_window(self, path: Path) -> Dict[str, float]:
        res = self._load().generate(input=str(path), language="auto", use_itn=False)
        return parse_sensevoice_text(res[0].get("text", "") if res else "")


def _cut_window(audio: Path, start: float, length: float, out: Path) -> bool:
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-ss", f"{start:.1f}", "-t", f"{length:.1f}",
                    "-i", str(audio), "-vn", "-ac", "1", "-ar", "16000", str(out)],
                   capture_output=True)
    return out.is_file()


def sensevoice_series(audio: Path, duration: float, tagger: Optional[WindowTagger] = None,
                      window_sec: int = WINDOW_SEC,
                      progress: Optional[Callable[[str], None]] = None) -> Dict[str, List[float]]:
    """Per-second SenseVoice tag strengths for the whole audio.

    The model emits one emotion + event set per inference, so the audio is
    tagged in ``window_sec`` windows and each window's tags are spread over
    its seconds. Raises ``SenseVoiceUnavailable`` when the model cannot run
    at all; a single bad window is skipped (logged), not fatal.
    """
    tagger = tagger if tagger is not None else FunASRTagger()
    n = int(math.ceil(duration))
    series: Dict[str, List[float]] = {k: [0.0] * n for k in SV_KEYS}
    starts = list(range(0, n, window_sec))
    attempted = succeeded = 0
    with tempfile.TemporaryDirectory(prefix="sensevoice_") as tmp:
        for i, start in enumerate(starts):
            length = min(float(window_sec), duration - start)
            if length <= 0:
                break
            wav = Path(tmp) / f"w{start}.wav"
            if _cut_window(audio, float(start), length, wav):
                attempted += 1
                try:
                    tags = tagger.tag_window(wav)
                    succeeded += 1
                except SenseVoiceUnavailable:
                    raise
                except Exception as exc:
                    logger.warning("SenseVoice window at %ss failed: %s", start, exc)
                    tags = {}
                for k in SV_KEYS:
                    v = float(tags.get(k, 0.0) or 0.0)
                    if v:
                        for sec in range(start, min(n, start + int(math.ceil(length)))):
                            series[k][sec] = max(series[k][sec], v)
            if progress:
                progress(f"Listening for laughter and cheers: {i + 1}/{len(starts)} parts")
    if attempted and not succeeded:
        # A tagger that fails on every window is a broken model, not a
        # quiet stream: report the pass as unavailable (shout-only).
        raise SenseVoiceUnavailable(f"SenseVoice failed on all {attempted} windows")
    return series


# ---------------------------------------------------------------------------
# Shout: zero-model fallback (NeonClipping highlights.py, MIT — see NOTICE)
# ---------------------------------------------------------------------------

def _local_z(x: np.ndarray, n: int, win: int = 120) -> List[float]:
    """Local z-score vs a rolling baseline, scaled so z of 3.5 = 1, clipped 0..1."""
    out = np.zeros(n)
    if not len(x):
        return out.tolist()
    y = np.zeros(n)
    y[: min(n, len(x))] = x[:n]
    win = max(1, min(win, n))
    k = np.ones(win) / win
    mu = np.convolve(y, k, "same")
    sd = np.sqrt(np.maximum(np.convolve(y * y, k, "same") - mu * mu, 0)) + 1e-6
    return np.clip((y - mu) / sd / 3.5, 0, 1).tolist()


def shout_series(audio: Path, duration: float, chunk_sec: int = 600) -> List[float]:
    """Per-second shout strength 0..1: 2-4 kHz energy share x loudness.

    Voices raised, screams and impacts push energy into 2-4 kHz; weighting
    the band share by the second's RMS keeps quiet hiss from counting.
    Decoded at 8 kHz in chunks so an 11 h stream never sits in memory.
    """
    n = int(math.ceil(duration))
    rms = np.zeros(n)
    hf = np.zeros(n)
    start = 0.0
    while start < duration:
        length = min(float(chunk_sec), duration - start)
        raw = subprocess.run(["ffmpeg", "-v", "error", "-ss", f"{start:.1f}", "-t", f"{length:.1f}",
                              "-i", str(audio), "-vn", "-ac", "1", "-ar", "8000",
                              "-f", "s16le", "pipe:1"], capture_output=True).stdout
        x = np.frombuffer(raw[: len(raw) // 2 * 2], dtype=np.int16).astype(np.float32) / 32768.0
        secs = len(x) // 8000
        if secs:
            frames = x[: secs * 8000].reshape(secs, 8000)
            base = int(round(start))
            take = min(secs, n - base)
            if take > 0:
                frames = frames[:take]
                rms[base: base + take] = np.sqrt((frames ** 2).mean(1))
                spec = np.abs(np.fft.rfft(frames * np.hanning(8000), axis=1)) ** 2  # 1 Hz bins
                hf[base: base + take] = spec[:, 2000:4000].sum(1) / (spec.sum(1) + 1e-9)
        start += length
    return _local_z(hf * rms, n)


def reaction_feature_series(audio: Path, duration: float, tagger: Optional[WindowTagger] = None,
                            progress: Optional[Callable[[str], None]] = None) -> Dict[str, List[float]]:
    """The pass: extra per-second features for ``selection.rank_moments``.

    Always the zero-model ``shout``; the SenseVoice ``sv_*`` features are
    added when the model loads. Any SenseVoice failure degrades to
    shout-only (logged) — this pass never breaks a scan.
    """
    out: Dict[str, List[float]] = {"shout": shout_series(audio, duration)}
    try:
        out.update(sensevoice_series(audio, duration, tagger=tagger, progress=progress))
    except Exception as exc:
        logger.warning("SenseVoice tags unavailable, shout-only features: %s", exc)
    return out
