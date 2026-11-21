"""Streamer-reaction moment detection: what the streamer DOES about the game.

For a streamer VOD the clip is rarely the loudest or most explosive stretch.
It is the moment a streamer laughs until they cannot talk, screams at a jump
scare, or delivers a line nobody expected — usually about something that just
happened in the game, sometimes about nothing at all (chat, a bit, a slip).

Signals (all game-agnostic, all measured, none guessed from a game's name):

voice — measured on the MIXED audio but gated on harmonic, speech-band
        energy, so explosions, gunfire and engine noise do not count:
    * ``vocal``    — voiced loudness against the streamer's own baseline
    * ``pitch``    — pitch rising above the streamer's baseline (shouting)
    * ``laugh``    — regular, bursty voiced pulses (3–7 Hz) with real depth
face  — activity inside the facecam rectangle (found with the in-tree YuNet
        detector): head throw-backs, lunges, covering the face
game  — the on-screen events of ``gameplay_moments`` (never required)

A reaction is an episode where voice/face signals agree. It is then matched
to the nearest earlier game event: ``reaction_to_event`` (the product's core
case), ``commentary_reaction`` (funny, with no visible trigger),
``silent_facecam`` (only the face moved — weak), or ``silent_action`` (game
event, no streamer reaction — deliberately ranked low). Loud game audio can
not fake any of this because every voice signal is z-scored against the
streamer's own recent baseline and requires voicing.
"""

from __future__ import annotations

import logging
import re
import subprocess
from pathlib import Path
from typing import Any, Optional

import numpy as np

from .gameplay_moments import (
    FEATURES as GAME_FEATURES,
    Episode,
    _mmss,
    _robust_z,
    extract_frame_evidence,
    find_episodes as find_game_episodes,
    fuse_evidence as fuse_game_evidence,
)

logger = logging.getLogger(__name__)

SR = 16000
_WIN, _HOP = 640, 160            # 40 ms window, 10 ms hop
_F0_LO, _F0_HI = 80, 600          # Hz
_VOICE_BINS = (19, 217)           # ~300–3400 Hz at 1024-point FFT
VOICE = ("vocal", "pitch", "laugh", "lexical", "mic_loud")
_SCALE_FLOOR = {"vocal": 2.0, "pitch": 18.0, "laugh": 0.12, "face": 0.8, "lexical": 1.0}
_ACTIVE_Z = 3.0
_REACT_GAP_SEC = 2
_MIN_REACT_SEC = 1
_EVENT_LEAD_SEC = 7.0   # a reaction this soon after a game event is "to" it
_EVENT_SLACK_SEC = 1.5  # ...or this much before (anticipation / lag)
_WEIGHT = {"vocal": 1.0, "pitch": 1.0, "laugh": 1.6, "face": 0.8, "lexical": 1.0, "mic_loud": 1.0}


# --------------------------------------------------------------------------
# Voice evidence
# --------------------------------------------------------------------------

def _decode_blocks(video_path: Path | str, block_sec: int = 120, channel: Optional[int] = None):
    # channel=None decodes the mono mix (every stream so far); an index
    # decodes that one channel (the mic of a split mic/game recording).
    layout = ["-ac", "1"] if channel is None else ["-af", f"pan=mono|c0=c{int(channel)}"]
    cmd = ["ffmpeg", "-v", "error", "-i", str(video_path), "-vn", *layout,
           "-ar", str(SR), "-f", "s16le", "-"]
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    size = SR * block_sec * 2
    try:
        assert proc.stdout is not None
        while True:
            buf = proc.stdout.read(size)
            if len(buf) < _WIN * 2:
                break
            yield np.frombuffer(buf, dtype=np.int16).astype(np.float32) / 32768.0
    finally:
        if proc.stdout:
            proc.stdout.close()
        proc.kill()
        proc.wait()


_WINDOW_ACF = np.fft.irfft(np.abs(np.fft.rfft(np.hanning(_WIN), n=2048)) ** 2, n=2048)
_WINDOW_ACF = _WINDOW_ACF / _WINDOW_ACF[0]


def _frame_features(x: np.ndarray) -> dict[str, np.ndarray]:
    """Per 10 ms frame: voiced flag, voice-band dB, f0."""
    from numpy.lib.stride_tricks import sliding_window_view

    frames = sliding_window_view(x, _WIN)[::_HOP] * np.hanning(_WIN).astype(np.float32)
    spec = np.fft.rfft(frames, n=2048, axis=1)
    power = (spec.real ** 2 + spec.imag ** 2)
    total = power.sum(axis=1) + 1e-12
    bins = 2048 / 1024  # bins-per-1024-grid factor
    lo, hi = int(_VOICE_BINS[0] * bins), int(_VOICE_BINS[1] * bins)
    voice_power = power[:, lo:hi].sum(axis=1)
    voice_ratio = voice_power / total
    acf = np.fft.irfft(power, n=2048, axis=1)
    acf0 = acf[:, :1] + 1e-12
    lag_lo, lag_hi = int(SR / _F0_HI), int(SR / _F0_LO)
    # The Hann window shrinks the autocorrelation at long lags (low pitch);
    # dividing by the window's own autocorrelation removes that bias.
    seg = acf[:, lag_lo:lag_hi] / acf0 / _WINDOW_ACF[lag_lo:lag_hi]
    peak = seg.max(axis=1)
    lag = seg.argmax(axis=1) + lag_lo
    f0 = SR / lag
    voice_db = 10 * np.log10(voice_power + 1e-12)
    voiced = (peak > 0.45) & (voice_ratio > 0.25) & (voice_db > -55)
    return {"voiced": voiced, "voice_db": voice_db, "f0": f0, "peak": peak,
            "total_db": 10 * np.log10(total)}


def _laugh_score(env: np.ndarray) -> np.ndarray:
    """Per-second laughter score from the voiced-energy envelope (100 fps):
    depth of modulation × regularity of the 3–7 Hz pulse train, over a 2 s
    window centred on each second."""
    n_sec = len(env) // 100
    out = np.zeros(n_sec)
    for s in range(n_sec):
        a, b = max(0, s * 100 - 50), min(len(env), s * 100 + 150)
        w = env[a:b]
        if len(w) < 120 or w.max() <= 1e-6:
            continue
        w = w / (w.max() + 1e-9)
        depth = float(np.clip((w.max() - np.percentile(w, 15)), 0, 1))
        c = w - w.mean()
        denom = float((c ** 2).sum()) + 1e-9
        reg = 0.0
        for lag in range(14, 34):  # 70–330 ms → 3–7 Hz
            r = float((c[:-lag] * c[lag:]).sum()) / denom
            reg = max(reg, r)
        out[s] = depth * max(0.0, reg)
    return out


_SPLIT_CORR_MAX = 0.8        # L/R this correlated = one mix, not a split recording
_MIC_VOICED_MARGIN = 0.10    # the mic channel must be clearly more voiced


def _stereo_probe(video_path: Path | str, offsets: tuple[float, ...], seconds: float = 30.0) -> Optional[np.ndarray]:
    parts = []
    for offset in offsets:
        cmd = ["ffmpeg", "-v", "error", "-ss", f"{max(0.0, offset):.2f}", "-t", f"{seconds:.2f}",
               "-i", str(video_path), "-vn", "-ac", "2", "-ar", str(SR), "-f", "s16le", "-"]
        try:
            raw = subprocess.run(cmd, capture_output=True, timeout=120).stdout
        except Exception:
            continue
        pcm = np.frombuffer(raw, dtype=np.int16)
        if len(pcm) >= 4 * _WIN:
            parts.append(pcm[: len(pcm) // 2 * 2].reshape(-1, 2).astype(np.float32) / 32768.0)
    return np.concatenate(parts) if parts else None


def pick_voice_channel(video_path: Path | str, duration: Optional[float] = None) -> Optional[int]:
    """The mic channel of a split recording (mic on one side, game on the
    other), or None to keep the mono mix.

    Streams mix the mic into both channels, so L and R are nearly identical
    (correlation ~1) and nothing changes. When they differ, the mic is the
    channel with clearly more voiced frames; reading it alone keeps an
    explosion on the game channel from drowning the streamer's scream.
    Switch: REACTION_MIC_CHANNEL (default on).
    """
    import os

    if os.getenv("REACTION_MIC_CHANNEL", "1").strip().lower() in {"0", "false", "no", "off"}:
        return None
    total = float(duration or 0)
    if total <= 0:
        try:
            total = float(subprocess.run(
                ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(video_path)],
                capture_output=True, text=True, timeout=30,
            ).stdout.strip() or 0)
        except Exception:
            total = 0.0
    offsets = tuple(total * f for f in (0.15, 0.5, 0.8)) if total > 120 else (0.0,)
    pcm = _stereo_probe(video_path, offsets, 30.0 if total > 120 else 120.0)
    if pcm is None or pcm.shape[0] < 4 * _WIN:
        return None
    left, right = pcm[:, 0], pcm[:, 1]
    if float(np.std(left)) < 1e-4 or float(np.std(right)) < 1e-4:
        return None
    corr = float(np.corrcoef(left, right)[0, 1])
    if not np.isfinite(corr) or corr > _SPLIT_CORR_MAX:
        return None
    voiced = [float(np.mean(_frame_features(ch)["voiced"])) for ch in (left, right)]
    if max(voiced) < 0.1 or abs(voiced[0] - voiced[1]) < _MIC_VOICED_MARGIN:
        return None
    channel = int(np.argmax(voiced))
    logger.info("Split stereo (L/R corr %.2f): reading the mic on channel %d (voiced %.2f vs %.2f)",
                corr, channel, voiced[channel], voiced[1 - channel])
    return channel


def extract_voice_evidence(video_path: Path | str, duration: Optional[float] = None) -> dict[str, np.ndarray]:
    """Per-second voice features of the streamer's audio: the mic channel of
    a split recording, else the mixed audio. Empty dict when the source has
    no audio."""
    vloud: list[float] = []
    f0s: list[float] = []
    vfrac: list[float] = []
    total_db: list[float] = []
    mic_db: list[float] = []
    env_all: list[np.ndarray] = []
    channel = pick_voice_channel(video_path, duration)
    for block in _decode_blocks(video_path, channel=channel):
        if len(block) < _WIN * 2:
            continue
        ff = _frame_features(block)
        n = (len(ff["voiced"]) // 100) * 100
        voiced = ff["voiced"][:n].reshape(-1, 100)
        vdb = ff["voice_db"][:n].reshape(-1, 100)
        f0 = ff["f0"][:n].reshape(-1, 100)
        tdb = ff["total_db"][:n].reshape(-1, 100)
        amp = np.where(ff["voiced"][:n], 10 ** (ff["voice_db"][:n] / 20.0), 0.0)
        env_all.append(amp)
        for i in range(voiced.shape[0]):
            v = voiced[i]
            vfrac.append(float(v.mean()))
            vloud.append(float(np.percentile(vdb[i][v], 90)) if v.sum() >= 5 else np.nan)
            f0s.append(float(np.median(f0[i][v])) if v.sum() >= 5 else np.nan)
            total_db.append(float(np.percentile(tdb[i], 90)))
            mic_db.append(float(np.percentile(vdb[i], 98)))
    if not vloud:
        return {}
    vloud_a = np.array(vloud)
    floor = np.nanmin(vloud_a) if np.isfinite(vloud_a).any() else -60.0
    vloud_a = np.where(np.isnan(vloud_a), floor, vloud_a)
    f0_a = np.array(f0s)
    last = 150.0
    for i in range(len(f0_a)):
        if np.isnan(f0_a[i]):
            f0_a[i] = last
        else:
            last = f0_a[i]
    env = np.concatenate(env_all)
    laugh = _laugh_score(env)
    n = min(len(vloud_a), len(laugh))
    out = {"vocal": vloud_a[:n], "f0": f0_a[:n], "voiced_frac": np.array(vfrac)[:n],
            "laugh": laugh[:n], "total_db": np.array(total_db)[:n]}
    if channel is not None:
        # Only on a known mic channel: in a mixed stream the loudest
        # voice-band frames are as likely an explosion as a scream.
        out["mic_loud"] = np.array(mic_db)[:n]
    return out


# --------------------------------------------------------------------------
# Facecam
# --------------------------------------------------------------------------

def find_facecam(video_path: Path | str, duration: float, samples: int = 14) -> Optional[tuple[float, float, float, float]]:
    """Normalised (x, y, w, h) of the most consistent face rectangle, found
    with YuNet on evenly spaced frames, or None. A face that appears in under
    35% of samples, or that moves around the frame, is not a facecam."""
    try:
        import cv2
        from ..media.face_detection import YuNetFaceDetector
        det = YuNetFaceDetector()
    except Exception:
        logger.info("Face detector unavailable; facecam evidence skipped", exc_info=True)
        return None
    boxes: list[tuple[float, float, float, float]] = []
    probed = 0
    probe = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
         "stream=width,height", "-of", "csv=p=0", str(video_path)],
        capture_output=True, text=True)
    try:
        vw, vh = (int(v) for v in probe.stdout.strip().split(",")[:2])
    except ValueError:
        return None
    for i in range(samples):
        t = duration * (i + 0.5) / samples
        proc = subprocess.run(
            ["ffmpeg", "-v", "error", "-ss", f"{t:.2f}", "-i", str(video_path),
             "-frames:v", "1", "-vf", "scale=960:-2,format=bgr24", "-f", "rawvideo", "-"],
            capture_output=True)
        if proc.returncode != 0 or not proc.stdout:
            continue
        w = 960
        h = int(round(vh * 960 / vw / 2) * 2)
        if len(proc.stdout) != w * h * 3:
            continue
        frame = np.frombuffer(proc.stdout, dtype=np.uint8).reshape(h, w, 3)
        probed += 1
        for x, y, fw, fh, _score in det.detect(frame):
            boxes.append((x / w, y / h, fw / w, fh / h))
    if probed == 0:
        return None
    # A facecam is the face that sits in the SAME place in many frames; in-game
    # characters wander. Cluster face centres and take the busiest, stable one.
    clusters: list[list[tuple[float, float, float, float]]] = []
    for bx in boxes:
        cx, cy = bx[0] + bx[2] / 2, bx[1] + bx[3] / 2
        for cl in clusters:
            ccx = float(np.median([c[0] + c[2] / 2 for c in cl]))
            ccy = float(np.median([c[1] + c[3] / 2 for c in cl]))
            if abs(cx - ccx) < 0.05 and abs(cy - ccy) < 0.05:
                cl.append(bx)
                break
        else:
            clusters.append([bx])
    if not clusters:
        return None
    best = max(clusters, key=len)
    if len(best) < max(3, int(0.35 * probed)):
        return None
    arr = np.array(best)
    x0, y0 = float(np.median(arr[:, 0])), float(np.median(arr[:, 1]))
    fw, fh = float(np.median(arr[:, 2])), float(np.median(arr[:, 3]))
    # Pad: heads move; the reaction is the whole upper body.
    px, py = fw * 0.9, fh * 0.9
    x0, y0 = max(0.0, x0 - px), max(0.0, y0 - py)
    return x0, y0, min(1.0 - x0, fw + 2 * px), min(1.0 - y0, fh + 2 * py)


_FACE_FPS = 4
_FACE_PATCH = 48
_FACE_MIN_COVERAGE = 0.3


def face_activity_mode() -> str:
    """REACTION_FACE_ACTIVITY: "face" (default) measures the face itself;
    "roi" restores the whole-rectangle frame difference."""
    import os

    value = os.getenv("REACTION_FACE_ACTIVITY", "face").strip().lower()
    return "roi" if value in {"roi", "0", "off", "false", "legacy"} else "face"


def extract_face_activity(video_path: Path | str, roi: tuple[float, float, float, float]) -> np.ndarray:
    """Per-second activity of the streamer's FACE inside the facecam.

    The whole-rectangle difference (``extract_facecam_activity``) is
    dominated by whatever moves behind the streamer (a room, a window,
    lighting), so a still face over a busy background out-scored a laughing
    one. Here YuNet finds the face at 4 fps and each second scores:

    * expression change: the face patch, aligned to its own box, against the
      previous frame's (mouth, eyes, brows; head translation cancels out);
    * head movement: box-centre travel relative to the face size.

    Frames without a face fall back to the rectangle difference. When the
    face is visible in under 30 % of frames the old measure is returned.
    """
    try:
        import cv2

        from ..media.face_detection import open_yunet_detector

        detector = open_yunet_detector()
    except Exception:
        detector = None
    if detector is None:
        return extract_facecam_activity(video_path, roi)
    x, y, w, h = roi
    width = 224
    cmd = ["ffmpeg", "-v", "error", "-i", str(video_path), "-an", "-vf",
           f"fps={_FACE_FPS},crop=iw*{w:.4f}:ih*{h:.4f}:iw*{x:.4f}:ih*{y:.4f},"
           f"scale={width}:-2,format=bgr24", "-f", "rawvideo", "-"]
    probe = subprocess.run(
        ["ffmpeg", "-v", "error", "-i", str(video_path), "-an", "-frames:v", "1", "-vf",
         f"crop=iw*{w:.4f}:ih*{h:.4f}:iw*{x:.4f}:ih*{y:.4f},scale={width}:-2",
         "-f", "rawvideo", "-pix_fmt", "bgr24", "-"],
        capture_output=True, timeout=60,
    ).stdout
    height = len(probe) // (width * 3)
    if height <= 0:
        return extract_facecam_activity(video_path, roi)
    frame_size = width * height * 3
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    values: list[float] = []
    roi_values: list[float] = []
    has_face: list[bool] = []
    prev_patch = prev_box = prev_small = None
    try:
        assert proc.stdout is not None
        while True:
            buf = proc.stdout.read(frame_size)
            if len(buf) < frame_size:
                break
            frame = np.frombuffer(buf, dtype=np.uint8).reshape(height, width, 3)
            gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
            small = cv2.resize(gray, (64, 48), interpolation=cv2.INTER_AREA).astype(np.float32)
            roi_diff = float(np.abs(small - prev_small).mean()) if prev_small is not None else 0.0
            prev_small = small
            faces = [f for f in detector.detect(frame) if f[3] >= height * 0.15]
            if not faces:
                values.append(np.nan)
                roi_values.append(roi_diff)
                has_face.append(False)
                prev_patch = prev_box = None
                continue
            fx, fy, fw, fh = max(faces, key=lambda f: f[2] * f[3])[:4]
            x0, y0 = max(0, int(fx)), max(0, int(fy))
            x1, y1 = min(width, int(fx + fw)), min(height, int(fy + fh))
            patch = cv2.resize(gray[y0:y1, x0:x1], (_FACE_PATCH, _FACE_PATCH),
                               interpolation=cv2.INTER_AREA).astype(np.float32)
            score = 0.0
            if prev_patch is not None and prev_box is not None:
                expression = float(np.abs(patch - prev_patch).mean())
                travel = float(np.hypot(fx + fw / 2 - prev_box[0], fy + fh / 2 - prev_box[1]))
                score = expression + 40.0 * travel / max(fh, 1.0)
            values.append(score)
            roi_values.append(roi_diff)
            has_face.append(True)
            prev_patch, prev_box = patch, (fx + fw / 2, fy + fh / 2)
    finally:
        if proc.stdout:
            proc.stdout.close()
        proc.kill()
        proc.wait()
    if not values or float(np.mean(has_face)) < _FACE_MIN_COVERAGE:
        return extract_facecam_activity(video_path, roi)
    face_vals = np.array(values, dtype=float)
    roi_arr = np.array(roi_values, dtype=float)
    # Put the fallback frames on the face scale: the median face score over
    # the median rectangle difference of the frames that had both.
    both = np.isfinite(face_vals) & (roi_arr > 0)
    ratio = (float(np.median(face_vals[both])) / max(float(np.median(roi_arr[both])), 1e-6)) if both.any() else 1.0
    filled = np.where(np.isfinite(face_vals), face_vals, roi_arr * ratio)
    n = len(filled) // _FACE_FPS
    return np.array([float(np.mean(filled[i * _FACE_FPS:(i + 1) * _FACE_FPS])) for i in range(n)])


def extract_facecam_activity(video_path: Path | str, roi: tuple[float, float, float, float]) -> np.ndarray:
    """Per-second motion inside the facecam rectangle (mean abs diff, 8 fps)."""
    x, y, w, h = roi
    crop = f"crop=iw*{w:.4f}:ih*{h:.4f}:iw*{x:.4f}:ih*{y:.4f}"
    cmd = ["ffmpeg", "-v", "error", "-i", str(video_path), "-an", "-vf",
           f"fps=8,{crop},scale=64:48:flags=area,format=gray", "-f", "rawvideo", "-"]
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    size = 64 * 48
    diffs: list[float] = []
    prev = None
    try:
        assert proc.stdout is not None
        while True:
            buf = proc.stdout.read(size)
            if len(buf) < size:
                break
            cur = np.frombuffer(buf, dtype=np.uint8).astype(np.float32)
            if prev is not None:
                diffs.append(float(np.abs(cur - prev).mean()))
            prev = cur
    finally:
        if proc.stdout:
            proc.stdout.close()
        proc.kill()
        proc.wait()
    n = len(diffs) // 8
    return np.array([np.mean(diffs[i * 8:(i + 1) * 8]) for i in range(n)]) if n else np.zeros(0)


# --------------------------------------------------------------------------
# Fusion + correlation
# --------------------------------------------------------------------------

# Exclamations and reaction words (English, Spanish, romanised Hindi). Only
# words that are rarely neutral filler; "que"/"kya" count only with ? or !.
_EXCLAIM = re.compile(
    r"^(wait|what+|whoa+|wo+w|oh+|ah+|no+|nah+|yo+|bro+|dude|damn|hell|shit+|fuck\w*|holy|jesus|"
    r"omg|stop|run|haha\w*|hah|lol|lmao|jaja\w*|joder|hostia|ostia|madre|dios|tio|tío|"
    r"arre|bhai|bhag|abe|oye)$", re.I)
_EXCLAIM_IF_MARKED = re.compile(r"^(que|qué|kya|why|how|huh)$", re.I)


def lexical_series(words: list[dict[str, Any]], seconds: int) -> np.ndarray:
    """Per-second reaction-word score from transcript words (seconds).

    Counts exclamation words, '!' marks and quick repeats ("no no no").
    The words themselves are never changed.
    """
    out = np.zeros(max(0, seconds))
    prev: Optional[tuple[str, float]] = None
    for w in words:
        t = float(w.get("start", 0.0))
        i = int(t)
        if i < 0 or i >= seconds:
            continue
        raw = str(w.get("text", ""))
        core = re.sub(r"[^\w']", "", raw).lower()
        marked = "!" in raw or "?" in raw
        score = 0.0
        if core and _EXCLAIM.match(core):
            score += 1.0
        elif core and marked and _EXCLAIM_IF_MARKED.match(core):
            score += 1.0
        if "!" in raw:
            score += 0.75
        if prev and core and core == prev[0] and t - prev[1] <= 1.5:
            score += 0.5
        out[i] += score
        prev = (core, t)
    return out


def _words_in_seconds(words: Optional[list[dict[str, Any]]], duration: float) -> Optional[list[dict[str, Any]]]:
    """Accept transcript words in seconds or in milliseconds (the app's
    cached AssemblyAI/whisper format) and return them in seconds."""
    if not words:
        return None
    ends = [float(w.get("end", w.get("start", 0)) or 0) for w in words]
    scale = 1000.0 if max(ends) > max(duration, 1.0) * 5 else 1.0
    return [{"text": w.get("text", ""), "start": float(w.get("start", 0)) / scale,
             "end": float(w.get("end", w.get("start", 0))) / scale} for w in words]


def reaction_series(voice: dict[str, np.ndarray], face: Optional[np.ndarray],
                    lexical: Optional[np.ndarray] = None) -> tuple[np.ndarray, dict[str, np.ndarray], bool]:
    """(per-second reaction strength, per-signal z, voice_present)."""
    n = len(voice["vocal"]) if voice else (len(face) if face is not None else 0)
    zs: dict[str, np.ndarray] = {}
    voice_present = bool(voice) and float(np.mean(voice["voiced_frac"])) > 0.02
    if voice_present:
        zs["vocal"] = _robust_z(voice["vocal"], _SCALE_FLOOR["vocal"], rel=0.0)  # dB: offset is arbitrary
        zs["pitch"] = _robust_z(voice["f0"], _SCALE_FLOOR["pitch"], rel=0.05)
        # Pitch only counts while the streamer is actually voicing loudly.
        zs["pitch"] = np.where(voice["voiced_frac"] > 0.25, zs["pitch"], 0.0)
        zs["laugh"] = _robust_z(voice["laugh"], _SCALE_FLOOR["laugh"])
        if fusion_enabled():
            # A laugh pulse at a speech->silence edge is an artifact: a laugh
            # needs the voice itself raised in that second.
            zs["laugh"] = np.where(zs["vocal"] >= 1.0, zs["laugh"], np.minimum(zs["laugh"], 0.0))
            if "mic_loud" in voice:
                zs["mic_loud"] = _robust_z(voice["mic_loud"], _SCALE_FLOOR["vocal"], rel=0.0)
    if lexical is not None and len(lexical) and float(np.max(lexical)) > 0:
        zs["lexical"] = _robust_z(lexical, _SCALE_FLOOR["lexical"], rel=0.0)
    if face is not None and len(face):
        m = min(n, len(face)) if n else len(face)
        zs["face"] = _robust_z(face[:m], _SCALE_FLOOR["face"])
    if not zs:
        return np.zeros(0), {}, voice_present
    n = min(len(v) for v in zs.values())
    zs = {k: v[:n] for k, v in zs.items()}
    strength = np.zeros(n)
    mic_burst = None
    if fusion_enabled() and "mic_loud" in zs and "vocal" in zs and n >= 10:
        loud = voice["mic_loud"][:n]
        mic_burst = (loud >= np.percentile(loud, 100 - _MIC_BURST_PCT)) & (zs["vocal"] >= _SCREAM_VOCAL_Z)
        # Mark those seconds as an active voice signal, so the episode is a
        # vocal burst (not a "silent facecam") and counts as the voice modality.
        zs["mic_loud"] = np.where(mic_burst, np.maximum(zs["mic_loud"], _ACTIVE_Z), zs["mic_loud"])
    for i in range(n):
        active = [k for k in zs if zs[k][i] >= _ACTIVE_Z]
        top = max(zs[k][i] for k in zs)
        voice_active = [k for k in active if k in VOICE]
        # Agreement: two signals, or a strong laugh on its own. A face-only
        # or pitch-only second never counts alone. A scream counts: pitch far
        # above the streamer's own (z >= _SCREAM_PITCH_Z) WITH a raised voice
        # (vocal z >= _SCREAM_VOCAL_Z) -- two cues of one voice, the way a
        # shout is vocal + pitch (REACTION_FUSION; off restores the old rule).
        scream = (
            fusion_enabled()
            and "pitch" in active and "vocal" in zs
            and zs["pitch"][i] >= _SCREAM_PITCH_Z and zs["vocal"][i] >= _SCREAM_VOCAL_Z
        )
        if len(active) >= 2 or (("laugh" in active) and zs["laugh"][i] >= 5.0) or scream:
            if voice_active or len(active) >= 2:
                strength[i] = sum(_WEIGHT[k] * (zs[k][i] - _ACTIVE_Z + 1.0) for k in active)
        # On a known mic channel: one of the streamer's own loudest seconds
        # (top _MIC_BURST_PCT %) with the voice raised is a burst, even when
        # their talk swings too much for a fixed z bar (per-task, contract §3).
        if strength[i] == 0 and mic_burst is not None and mic_burst[i]:
            strength[i] = 1.0 + max(0.0, float(zs["mic_loud"][i]))
    return strength, zs, voice_present


def _episodes(strength: np.ndarray, zs: dict[str, np.ndarray]) -> list[dict[str, Any]]:
    secs = [i for i in range(len(strength)) if strength[i] > 0]
    groups: list[list[int]] = []
    for i in secs:
        if groups and i - groups[-1][-1] <= _REACT_GAP_SEC:
            groups[-1].append(i)
        else:
            groups.append([i])
    out = []
    for g in groups:
        if g[-1] - g[0] + 1 < _MIN_REACT_SEC:
            continue
        a, b = g[0], g[-1] + 1
        feats = {k: float(np.max(zs[k][a:b])) for k in zs}
        agree = sorted((k for k in zs if feats[k] >= _ACTIVE_Z), key=lambda k: -feats[k])
        # Laughter must be corroborated by a loud voice: a regular pulse
        # pattern alone also appears in ordinary speech.
        kind = "laughter" if (feats.get("laugh", 0) >= 5 and feats.get("vocal", 0) >= 2) else (
            "shout" if feats.get("vocal", 0) >= 3 and feats.get("pitch", 0) >= 3 else (
                "silent_facecam" if not any(k in VOICE and feats.get(k, 0) >= _ACTIVE_Z for k in zs)
                else "vocal_burst"))
        out.append({"start": float(a), "end": float(b), "strength": float(strength[a:b].sum()),
                    "peak": float(a + int(np.argmax(strength[a:b]))), "features": feats,
                    "agree": agree, "kind": kind})
    return out


# --------------------------------------------------------------------------
# Whose voice is it? (streamer vs game dialogue)
# --------------------------------------------------------------------------

_MOUTH_FPS = 8
_MOUTH_RATIO_MIN = 1.25     # reaction-span mouth movement vs the window's quiet level
_MOUTH_MIN_COVERAGE = 0.5   # face+landmarks found in at least half the frames


def mouth_motion(video_path: Path | str, roi: tuple[float, float, float, float],
                 t0: float, t1: float) -> Optional[np.ndarray]:
    """Per-second variability of the facecam mouth opening (YuNet landmarks)
    between t0 and t1. None when the face is not visible often enough to say."""
    try:
        from ..media.active_speaker import mouth_aperture
        from ..media.face_detection import YuNetFaceDetector
        det = YuNetFaceDetector()
    except Exception:
        return None
    x, y, w, h = roi
    size = 256
    dur = max(1.0, t1 - t0)
    cmd = ["ffmpeg", "-v", "error", "-ss", f"{max(0.0, t0):.2f}", "-t", f"{dur:.2f}", "-i", str(video_path),
           "-an", "-vf", f"fps={_MOUTH_FPS},crop=iw*{w:.4f}:ih*{h:.4f}:iw*{x:.4f}:ih*{y:.4f},"
           f"scale={size}:{size},format=bgr24", "-f", "rawvideo", "-"]
    proc = subprocess.run(cmd, capture_output=True, timeout=120)
    raw = proc.stdout
    n = len(raw) // (size * size * 3)
    if n < _MOUTH_FPS * 2:
        return None
    ap: list[Optional[float]] = []
    for i in range(n):
        frame = np.frombuffer(raw[i * size * size * 3:(i + 1) * size * size * 3], dtype=np.uint8).reshape(size, size, 3)
        faces = det.detect_with_landmarks(frame)
        if not faces:
            ap.append(None)
            continue
        box, lm = max(faces, key=lambda f: f[0][2] * f[0][3])
        ap.append(mouth_aperture(box[:4], lm))
    found = sum(a is not None for a in ap)
    if found < _MOUTH_MIN_COVERAGE * n:
        return None
    per_sec = []
    for s in range(n // _MOUTH_FPS):
        vals = [a for a in ap[s * _MOUTH_FPS:(s + 1) * _MOUTH_FPS] if a is not None]
        per_sec.append(float(np.std(vals)) if len(vals) >= 3 else np.nan)
    return np.array(per_sec)


def voice_is_streamers(mouth: Optional[np.ndarray], span: tuple[int, int]) -> Optional[bool]:
    """True when the mouth moves clearly more during the reaction span than in
    the quieter rest of the window; False when it does not; None when unknown."""
    if mouth is None or not np.isfinite(mouth).any():
        return None
    a, b = span
    inside = mouth[a:b]
    inside = inside[np.isfinite(inside)]
    rest = np.concatenate([mouth[:a], mouth[b:]])
    rest = rest[np.isfinite(rest)]
    if len(inside) == 0 or len(rest) < 2:
        return None
    base = max(float(np.percentile(rest, 25)), 1e-3)
    return float(np.median(inside)) / base >= _MOUTH_RATIO_MIN


def check_voice_source(items: list[dict[str, Any]], video_path: Path | str,
                       roi: Optional[tuple[float, float, float, float]]) -> None:
    """Demote voice-led reactions whose voice is probably GAME audio: the
    facecam mouth does not move with it and the face itself is calm."""
    if roi is None or not Path(str(video_path)).is_file():
        return
    for it in items:
        if it["type"] not in ("reaction_to_event", "commentary_reaction"):
            continue
        feats = it.get("features") or {}
        if feats.get("face", 0) >= 3.0:
            it["voice_source"] = "streamer (facecam reacts)"
            continue
        t0 = max(0.0, it["start"] - 6.0)
        mouth = mouth_motion(video_path, roi, t0, it["end"] + 2.0)
        verdict = voice_is_streamers(mouth, (int(it["start"] - t0), int(it["end"] - t0) + 1))
        if verdict is False:
            it["voice_source"] = "likely game audio (facecam mouth still)"
            it["features"] = {k: (0.0 if k in VOICE else v) for k, v in feats.items()}
            it["mult"] *= 0.5
        else:
            it["voice_source"] = "streamer" if verdict else "unverified"



def correlate(reactions: list[dict[str, Any]], game_eps: list[Episode], streamer_present: bool) -> list[dict[str, Any]]:
    """Attach the triggering game event to each reaction; keep unreacted game
    events as low-ranked ``silent_action`` candidates."""
    used: set[int] = set()
    items: list[dict[str, Any]] = []
    for r in reactions:
        match: Optional[tuple[int, Episode]] = None
        for idx, g in enumerate(game_eps):
            # Event begins before/at the reaction and the reaction starts
            # within _EVENT_LEAD_SEC of the event's end (or slightly before).
            if g.start - _EVENT_SLACK_SEC <= r["start"] <= g.end + _EVENT_LEAD_SEC:
                if match is None or g.end > match[1].end:
                    match = (idx, g)
        if match is not None:
            used.add(match[0])
            kind = "reaction_to_event" if r["kind"] != "silent_facecam" else "silent_facecam"
            mult = 1.35 if kind == "reaction_to_event" else 0.6
            items.append({**r, "type": kind, "event": match[1], "mult": mult})
        else:
            if r["kind"] == "silent_facecam":
                items.append({**r, "type": "silent_facecam", "event": None, "mult": 0.5})
            else:
                items.append({**r, "type": "commentary_reaction", "event": None, "mult": 1.0})
    if streamer_present:
        for idx, g in enumerate(game_eps):
            if idx not in used:
                items.append({"start": g.start, "end": g.end, "strength": g.strength * 0.25,
                              "peak": g.peak, "features": {}, "agree": [], "kind": "silent_action",
                              "type": "silent_action", "event": g, "mult": 0.45})
    return items


_VOICE_ONLY_CAP = 55.0
_SCREAM_PITCH_Z = 4.0
_SCREAM_VOCAL_Z = 1.5
_SILENT_ACTION_CAP = 40.0
_COINCIDENCE_KEEP = 0.25
_MIC_BURST_PCT = 1.0


def fusion_enabled() -> bool:
    """REACTION_FUSION (default on): the scream rule and coincidence ranking
    of unreacted game events. Off restores the previous behaviour."""
    import os

    return os.getenv("REACTION_FUSION", "1").strip().lower() not in {"0", "false", "no", "off"}


def reaction_level(zs: dict[str, np.ndarray], start: float, end: float) -> float:
    """How much the streamer reacts in [start, end): the strongest of their
    own signals (face, voice, reaction words) mapped to 0-1 (z 6 = 1).
    0 when every measured signal stays at or below the streamer's normal."""
    best = 0.0
    for key in ("face", "vocal", "pitch", "laugh", "lexical", "mic_loud"):
        series = zs.get(key)
        if series is None or len(series) == 0:
            continue
        a = max(0, int(np.floor(start)))
        b = min(len(series), max(a + 1, int(np.ceil(end))))
        if a < len(series):
            best = max(best, float(np.max(series[a:b])))
    return float(np.clip(best / 6.0, 0.0, 1.0))


def rank_unreacted_events(items: list[dict[str, Any]], zs: dict[str, np.ndarray],
                          game_eps: list[Episode]) -> None:
    """Coincidence for game events with no reaction episode (contract §2:
    a top rank needs reaction evidence AND context, never one alone).

    Each ``silent_action`` item gets ``coincidence`` = min(event strength
    relative to the task's strongest event, the streamer's reaction level
    around it). The loudest explosion while the streamer sits still and
    silent therefore ranks below a weaker event the streamer visibly or
    audibly responds to.
    """
    peak = max((g.strength for g in game_eps), default=0.0)
    for it in items:
        if it.get("type") != "silent_action" or it.get("event") is None:
            continue
        # Square root: event strengths are heavy-tailed, and one huge
        # explosion must not flatten every other event in the task.
        event_level = float(np.sqrt(float(it["event"].strength) / peak)) if peak > 0 else 0.0
        level = reaction_level(zs, it["start"] - 1.0, it["end"] + 3.0)
        it["coincidence"] = round(min(event_level, level), 3)


def modalities(it: dict[str, Any]) -> set[str]:
    """Which independent kinds of evidence back this moment."""
    f = it.get("features") or {}
    mods: set[str] = set()
    if any(f.get(k, 0) >= _ACTIVE_Z for k in VOICE):
        mods.add("voice")
    if f.get("face", 0) >= 2.5:
        mods.add("face")
    if it.get("event") is not None and it["type"] != "silent_action":
        mods.add("context")
    return mods


def _score(it: dict[str, Any]) -> float:
    base = 38.0 + 7.0 * float(np.sqrt(max(it["strength"], 0.0)))
    if it["type"] == "silent_action":
        if "coincidence" in it:
            return float(_SILENT_ACTION_CAP * it["coincidence"])
        return float(min(_SILENT_ACTION_CAP, base * it["mult"]))
    if it["type"] == "face_reaction":
        cap = _FACE_LED_WITH_EVENT_CAP if it.get("event") is not None else _FACE_LED_CAP
        return float(min(cap, base * it["mult"]))
    kind_bonus = {"laughter": 8.0, "shout": 6.0, "vocal_burst": 3.0, "silent_facecam": 0.0}.get(it["kind"], 0.0)
    score = float(min(98.0, (base + kind_bonus) * it["mult"]))
    # Coincidence rule: a top-ranked moment needs at least TWO modalities —
    # voice, facecam, or a visible context event. One modality alone caps.
    return min(score, _VOICE_ONLY_CAP) if len(modalities(it)) < 2 else score


# --- Face-led reactions (Sanji Q2, 2026-11-18; switch REACTION_FACE_LED) ---
#
# The R2 case: the face carries the moment while the game is quiet. Voice
# and fusion cannot see it, and the 4 fps patch measure does not separate it
# on handheld facecam footage. Measured as Sanji did on his fixture: YuNet at
# 10 Hz on the facecam, mouth-corner width and head jump between frames,
# both divided by face height so any resolution compares. A run with either
# at z >= 4 for >= 2 s is a face-led candidate: capped at 45 alone (below
# voice-only 55), up to 55 when a game event coincides.

_FACE_LED_FPS = 10
_FACE_LED_Z = 4.0
_FACE_LED_MIN_SEC = 2
_FACE_LED_CAP = 45.0
_FACE_LED_WITH_EVENT_CAP = 55.0


def face_led_enabled() -> bool:
    import os

    return os.getenv("REACTION_FACE_LED", "1").strip().lower() not in {"0", "false", "no", "off"}


def extract_face_dynamics(video_path: Path | str, roi: tuple[float, float, float, float],
                          duration: float = 0.0) -> Optional[dict[str, np.ndarray]]:
    """Per-second facecam dynamics: p90 mouth-corner width and p90 head jump
    (both / face height), YuNet at 10 Hz (5 Hz past 30 minutes). None when
    the face is visible in under 30 % of frames."""
    try:
        from ..media.face_detection import YuNetFaceDetector

        detector = YuNetFaceDetector()
    except Exception:
        return None
    fps = _FACE_LED_FPS if duration <= 1800 else 5
    x, y, w, h = roi
    width = 384
    crop = f"crop=iw*{w:.4f}:ih*{h:.4f}:iw*{x:.4f}:ih*{y:.4f},scale={width}:-2"
    probe = subprocess.run(
        ["ffmpeg", "-v", "error", "-i", str(video_path), "-an", "-frames:v", "1", "-vf", crop,
         "-f", "rawvideo", "-pix_fmt", "bgr24", "-"], capture_output=True, timeout=60).stdout
    height = len(probe) // (width * 3)
    if height <= 0:
        return None
    size = width * height * 3
    proc = subprocess.Popen(
        ["ffmpeg", "-v", "error", "-i", str(video_path), "-an", "-vf", f"fps={fps},{crop},format=bgr24",
         "-f", "rawvideo", "-"], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    mouth: list[float] = []
    jump: list[float] = []
    seen = 0
    prev = None
    try:
        assert proc.stdout is not None
        while True:
            buf = proc.stdout.read(size)
            if len(buf) < size:
                break
            frame = np.frombuffer(buf, dtype=np.uint8).reshape(height, width, 3)
            faces = [f for f in detector.detect_with_landmarks(frame) if f[0][3] >= height * 0.12]
            if not faces:
                mouth.append(np.nan)
                jump.append(np.nan)
                prev = None
                continue
            seen += 1
            (fx, fy, fw, fh, _score), lm = max(faces, key=lambda f: f[0][2] * f[0][3])
            mouth.append(float(np.hypot(lm[3][0] - lm[4][0], lm[3][1] - lm[4][1]) / max(fh, 1.0)))
            centre = (fx + fw / 2, fy + fh / 2)
            jump.append(float(np.hypot(centre[0] - prev[0], centre[1] - prev[1]) / max(fh, 1.0)) if prev else np.nan)
            prev = centre
    finally:
        if proc.stdout:
            proc.stdout.close()
        proc.kill()
        proc.wait()
    if not mouth or seen < 0.3 * len(mouth):
        return None
    n = len(mouth) // fps

    def per_second(values: list[float]) -> np.ndarray:
        out = np.zeros(n)
        for s in range(n):
            chunk = np.array(values[s * fps:(s + 1) * fps], dtype=float)
            chunk = chunk[np.isfinite(chunk)]
            out[s] = float(np.percentile(chunk, 90)) if len(chunk) else np.nan
        fill = np.nanmedian(out) if np.isfinite(out).any() else 0.0
        return np.where(np.isfinite(out), out, fill)

    return {"mouth": per_second(mouth), "jump": per_second(jump)}


def face_led_items(dynamics: dict[str, np.ndarray], items: list[dict[str, Any]],
                   game_eps: list[Episode]) -> list[dict[str, Any]]:
    """Face-led candidates that no existing reaction item already covers."""
    z_mouth = _robust_z(dynamics["mouth"], 0.02, rel=0.0)
    z_jump = _robust_z(dynamics["jump"], 0.02, rel=0.0)
    face_z = np.maximum(z_mouth, z_jump)
    taken = [(i["start"], i["end"]) for i in items if i.get("type") != "silent_action"]
    out: list[dict[str, Any]] = []
    run: list[int] = []
    for s in list(range(len(face_z))) + [len(face_z)]:
        if s < len(face_z) and face_z[s] >= _FACE_LED_Z:
            run.append(s)
            continue
        if len(run) >= _FACE_LED_MIN_SEC:
            a, b = float(run[0]), float(run[-1] + 1)
            if not any(min(b, e) - max(a, st) > 0 for st, e in taken):
                event = next((g for g in game_eps if g.start - _EVENT_SLACK_SEC <= a <= g.end + _EVENT_LEAD_SEC), None)
                peak = float(np.max(face_z[run[0]:run[-1] + 1]))
                out.append({
                    "start": a, "end": b, "strength": peak, "peak": a, "kind": "facecam",
                    "features": {"face": peak, "face_mouth": float(np.max(z_mouth[run[0]:run[-1] + 1])),
                                 "face_jump": float(np.max(z_jump[run[0]:run[-1] + 1]))},
                    "agree": ["face"], "type": "face_reaction", "event": event,
                    "mult": 1.0, "face_led": True,
                })
        run = []
    return out


_REASON = {
    "face_reaction": "the facecam shows a big {kind} reaction (mouth and head) while the game is quiet",
    "reaction_to_event": "the streamer reacts ({kind}) right after something happens on screen",
    "commentary_reaction": "the streamer has a strong vocal reaction ({kind}) with no visible game trigger — commentary or a bit",
    "silent_facecam": "the facecam shows a big physical reaction but the streamer stays quiet",
    "silent_action": "something happens on screen but the streamer does not react",
}


# --- REACTION_API_CONTRACT §2 (Sanji, 2026-11-17): additive moment fields ---
#
# signals.* are 0-1 floats or None. None means "not measured" (no facecam,
# no voice track, no transcript), never 0; a measured 0 is evidence (the
# decoy: a loud explosion with a still face and a silent mic). Measured over
# the moment's own window from the same per-second z-scores the picker
# ranks with; z >= _CONTRACT_Z_FULL maps to 1. gameplay_event is the
# window's peak eventfulness over the task's peak (normalised per task, as
# the contract allows). fused_score is the picker's own reaction x context
# score (0-100, capped when only one modality fired).

_CONTRACT_Z_FULL = 6.0
_CONTRACT_LAYOUT = {
    "stacked_reactor": "stacked_reactor",
    "pip_preserved": "pip_preserved",
    "full": "gameplay_only",
}
_SIGNAL_WORDS = {
    "facecam_reaction": "a facecam reaction",
    "voice_burst": "a voice burst",
    "transcript_emotion": "emotional words",
    "gameplay_event": "an on-screen gameplay event",
}


def contract_fields_enabled() -> bool:
    import os

    return os.getenv("REACTION_CONTRACT_FIELDS", "1").strip().lower() not in {"0", "false", "no", "off"}


def _window_peak(series: Optional[np.ndarray], start: float, end: float) -> Optional[float]:
    if series is None or len(series) == 0:
        return None
    a = max(0, int(np.floor(start)))
    b = min(len(series), max(a + 1, int(np.ceil(end))))
    if a >= len(series):
        return 0.0
    return float(np.max(series[a:b]))


def _z01(z: Optional[float]) -> Optional[float]:
    if z is None:
        return None
    return round(float(np.clip(z / _CONTRACT_Z_FULL, 0.0, 1.0)), 3)


def contract_fields(
    start: float, end: float, score: float, zs: dict[str, np.ndarray],
    voice_present: bool, transcript_present: bool, facecam_present: bool,
    game_event: Optional[np.ndarray],
) -> dict[str, Any]:
    """signals / fused_score / reason for one moment window (contract §2)."""
    acoustic = [zs[k] for k in ("vocal", "pitch", "laugh", "mic_loud") if k in zs]
    voice_z = (
        max(_window_peak(s, start, end) or 0.0 for s in acoustic)
        if voice_present and acoustic else None
    )
    if transcript_present:
        lexical_z = _window_peak(zs.get("lexical"), start, end) if "lexical" in zs else 0.0
    else:
        lexical_z = None
    face_z = _window_peak(zs.get("face"), start, end) if facecam_present and "face" in zs else None
    gameplay = None
    if game_event is not None and len(game_event):
        peak = float(np.max(game_event))
        window = _window_peak(game_event, start, end) or 0.0
        gameplay = round(float(np.clip(window / peak, 0.0, 1.0)), 3) if peak > 0 else 0.0
    signals = {
        "facecam_reaction": _z01(face_z),
        "voice_burst": _z01(voice_z),
        "transcript_emotion": _z01(lexical_z),
        "gameplay_event": gameplay,
    }
    fired = [_SIGNAL_WORDS[k] for k, v in signals.items() if v is not None and v >= 0.5]
    reacted = [w for w in fired if w != _SIGNAL_WORDS["gameplay_event"]]
    if reacted and len(fired) > len(reacted):
        reason = f"The streamer reacts ({', '.join(reacted)}) to {_SIGNAL_WORDS['gameplay_event']}."
    elif reacted:
        reason = f"The streamer reacts ({', '.join(reacted)}) with no clear on-screen trigger."
    elif fired:
        reason = "Something happens in the game, but the streamer does not react."
    else:
        reason = "A weak moment: no signal stands out."
    return {"signals": signals, "fused_score": round(float(score), 1), "reason": reason}


def apply_contract_layout(moments: list[dict[str, Any]]) -> None:
    """layout_recommendation from the recommendation pass, in contract names."""
    for moment in moments:
        if "fused_score" not in moment:
            continue
        layout = ((moment.get("edit_recommendations") or {}).get("framing") or {}).get("layout")
        mapped = _CONTRACT_LAYOUT.get(layout)
        if mapped:
            moment["layout_recommendation"] = mapped


def shape_window(it: dict[str, Any], duration: float, clip_seconds: float) -> tuple[float, float, float, float]:
    """(start, end, lead, tail). Keep the trigger AND the full reaction."""
    r_start, r_end = it["start"], it["end"]
    ev: Optional[Episode] = it.get("event")
    if it["type"] == "reaction_to_event" and ev is not None:
        start = min(ev.start, r_start) - 3.5
    elif it["type"] == "silent_action" and ev is not None:
        start = ev.start - 4.0
    else:
        start = r_start - 6.0  # the setup: what made them say it
    tail = 3.5 if it["kind"] == "laughter" else 3.0
    end = r_end + tail
    max_len = max(clip_seconds * 1.6, 20.0)
    if end - start > max_len:
        start = max(start, end - max_len)
    min_len = min(12.0, duration)
    if end - start < min_len:
        start -= (min_len - (end - start)) * 0.6
        end += (min_len - (end - start)) * 0.4 if end - start < min_len else 0
    start, end = max(0.0, start), min(duration, end)
    return start, end, max(0.0, r_start - start), max(0.0, end - r_end)


def select_streamer_moments(
    video_path: Path | str,
    duration: float,
    *,
    target_count: int,
    clip_seconds: float,
    voice: Optional[dict[str, np.ndarray]] = None,
    face: Optional[np.ndarray] = None,
    facecam_roi: Optional[tuple[float, float, float, float]] = None,
    game_evidence: Optional[dict[str, np.ndarray]] = None,
    words: Optional[list[dict[str, Any]]] = None,
    profanity: str = "mask",
    reason_context: str = "streamer reaction analysis",
) -> list[dict[str, Any]]:
    """Moments where a streamer's reaction makes the clip, with reasons.

    Returns [] when there is no streamer evidence (no voice, no facecam) so the
    caller can fall back to the gameplay-only selector.
    """
    duration = float(duration or 0)
    if duration <= 0 or target_count <= 0:
        return []
    try:
        if voice is None:
            voice = extract_voice_evidence(video_path)
        roi = facecam_roi
        if face is None:
            roi = roi or find_facecam(video_path, duration)
            face = (
                (extract_face_activity if face_activity_mode() == "face" else extract_facecam_activity)(video_path, roi)
                if roi else None
            )
        words_s = _words_in_seconds(words, duration)
        lexical = lexical_series(words_s, int(duration) + 1) if words_s else None
        strength, zs, voice_present = reaction_series(voice, face, lexical)
        streamer_present = voice_present or (face is not None and len(face) > 0)
        if not streamer_present or len(strength) < 5:
            return []
        if game_evidence is None:
            game_evidence = extract_frame_evidence(video_path, mask_roi=roi)
        g_event, g_zs = fuse_game_evidence(game_evidence)
        game_eps = find_game_episodes(g_event, g_zs, None)
        items = correlate(_episodes(strength, zs), game_eps, streamer_present)
        check_voice_source(items, video_path, roi)
        if fusion_enabled():
            rank_unreacted_events(items, zs, game_eps)
        if face_led_enabled() and roi:
            dynamics = extract_face_dynamics(video_path, roi, duration)
            if dynamics is not None:
                items.extend(face_led_items(dynamics, items, game_eps))
    except Exception:
        logger.warning("Streamer-reaction detection failed for %s", video_path, exc_info=True)
        return []

    clip_seconds = max(1.0, min(float(clip_seconds or 30), duration))
    moments: list[dict[str, Any]] = []
    accepted: list[tuple[float, float]] = []
    # Game events the streamer ignored are only a last resort: when the
    # streamer reacted to anything, an unreacted explosion is not a clip.
    if any(i["type"] in ("reaction_to_event", "commentary_reaction") for i in items):
        # Under REACTION_FUSION an event the streamer partly responds to
        # (coincidence >= _COINCIDENCE_KEEP) stays, ranked below real reactions.
        items = [i for i in items if i["type"] != "silent_action"
                 or (fusion_enabled() and i.get("coincidence", 0.0) >= _COINCIDENCE_KEEP)]
    for it in sorted(items, key=lambda i: -_score(i)):
        score = _score(it)
        if it["type"] == "silent_facecam" and score < 45:
            continue
        start, end, lead, tail = shape_window(it, duration, clip_seconds)
        if any(min(end, b) - max(start, a) > 0.25 * (end - start) for a, b in accepted):
            continue
        accepted.append((start, end))
        trig = ""
        if it.get("event") is not None:
            trig = f" Trigger: {it['event'].reason.split(' between')[0]}."
        agree = it.get("agree") or []
        reason = _REASON[it["type"]].format(kind=it["kind"].replace("_", " "))
        moments.append({
            "start_time": _mmss(start), "end_time": _mmss(end), "text": "",
            "relevance_score": round(min(0.95, score / 100.0), 2),
            "reasoning": (
                f"Streamer reaction ({reason_context}): {reason} at {_mmss(it['start'])}"
                f"{' (signals: ' + ', '.join(agree) + ')' if agree else ''}.{trig} "
                f"Window {_mmss(start)}–{_mmss(end)} keeps {lead:.0f}s before the reaction and {tail:.0f}s after."),
            "virality_score": int(round(score)),
            "hook_score": int(round(score * 0.9)),
            "engagement_score": int(round(score * 0.8)),
            "value_score": 0,
            "shareability_score": int(round(score * 0.7)),
            "hook_type": it["type"],
            "hook_title": None,
            "evidence": {
                "detector": "streamer_reaction_v1",
                "moment_type": it["type"], "reaction_kind": it["kind"],
                "voice_source": it.get("voice_source", "unverified"),
                "modalities": sorted(modalities(it)),
                "signals": {k: round(v, 1) for k, v in it.get("features", {}).items()},
                "reaction_start_sec": round(it["start"], 1), "reaction_end_sec": round(it["end"], 1),
                "game_event": (None if it.get("event") is None else {
                    "start_sec": round(it["event"].start, 1), "end_sec": round(it["event"].end, 1),
                    "type": it["event"].event_type}),
                "lead_in_sec": round(lead, 1), "tail_sec": round(tail, 1),
            },
        })
        if contract_fields_enabled():
            moments[-1].update(contract_fields(
                start, end, score, zs, voice_present,
                transcript_present=words_s is not None,
                facecam_present=face is not None and len(face) > 0,
                game_event=g_event,
            ))
        if len(moments) >= target_count:
            break
    try:
        from .edit_recommendations import attach_recommendations
        from .reaction_hooks import apply_ga2

        apply_ga2(moments, duration, words=words_s, strength=strength,
                  facecam_roi=roi, profanity=profanity)
        attach_recommendations(moments, facecam_roi=roi, strength=strength, words=words_s)
    except Exception:
        logger.warning("Reaction hook/recommendation pass failed", exc_info=True)
    # Endings (viral queue #3/#7): the last boundary step, after GA2 has
    # snapped the window to speech. Both switches default off; off leaves
    # every window byte-identical. The picker's own shaping floor
    # (shape_window's min length) is the floor here — the render path's
    # CLIP_MIN_SECONDS floor applies downstream, untouched.
    try:
        from .endings import (
            frame_rhyme_score,
            loop_endings_enabled,
            payoff_tight_enabled,
            refine_clip_end,
        )

        if loop_endings_enabled() or payoff_tight_enabled():
            def _sec(label: str) -> float:
                parts = [float(p) for p in str(label).split(":")]
                total = 0.0
                for p in parts:
                    total = total * 60 + p
                return total

            for m in moments:
                ev = m.get("evidence") or {}
                if "reaction_end_sec" not in ev:
                    continue
                s, e = _sec(m["start_time"]), _sec(m["end_time"])
                visual = None
                if loop_endings_enabled():
                    visual = lambda c, _s=s: frame_rhyme_score(video_path, _s, c)  # noqa: B023
                new_end, rec = refine_clip_end(
                    s, e, payoff_end=float(ev["reaction_end_sec"]), words=words_s,
                    duration=duration, min_seconds=min(12.0, duration),
                    strength=strength, visual=visual)
                ev["ending"] = rec
                if rec["applied"]:
                    m["end_time"] = _mmss(new_end)
                    notes = ev.setdefault("boundary_notes", [])
                    notes.append(f"ending: {rec['reason']}")
    except Exception:
        logger.warning("Endings refinement failed", exc_info=True)
    if contract_fields_enabled():
        apply_contract_layout(moments)
    moments.sort(key=lambda m: m["start_time"])
    return moments


def build_reaction_hints(
    video_path: Path | str, duration: float, limit: int = 10,
    words: Optional[list[dict[str, Any]]] = None,
) -> str:
    """Text hints for the transcript ranker: where the streamer reacted, and
    to what. Same shape as ``build_clip_signal_summary`` lines. Empty string
    when there is no streamer evidence or anything fails."""
    if words is None:
        try:
            from ..media.transcription import load_cached_transcript_data

            words = (load_cached_transcript_data(Path(video_path)) or {}).get("words")
        except Exception:
            words = None
    moments = select_streamer_moments(
        video_path, duration, target_count=limit, clip_seconds=30, words=words)
    moments = [m for m in moments if m["hook_type"] != "silent_action"]
    if not moments:
        return ""
    lines = ["Measured streamer-reaction signals (voice, facecam and on-screen events; "
             "prefer these over plain loudness):"]
    for m in sorted(moments, key=lambda m: -m["virality_score"]):
        ev = m["evidence"]
        trig = ""
        if ev["game_event"]:
            trig = f"; triggered by an on-screen event at {_mmss(ev['game_event']['start_sec'])}"
        lines.append(
            f"- [{m['start_time']} - {m['end_time']}] score={m['virality_score'] / 10:.1f}: "
            f"{ev['moment_type'].replace('_', ' ')} ({ev['reaction_kind'].replace('_', ' ')}) at "
            f"{_mmss(ev['reaction_start_sec'])}{trig}")
    return chr(10).join(lines)
