"""Snap word timings onto the loudness envelope; verify caption coverage.

Adapted from wordcaps (MIT, https://github.com/Axeeh/wordcaps, commit
ff520f4 — see NOTICE): wordcaps' whole design is *not trusting* a
transcriber's timestamps, and both halves of that design live here:

* ``refine_words`` — a word may not start before the voice does or end
  after it stops: edges that fall in silence are snapped onto the
  nearest speech block of the loudness envelope. This is what makes
  the karaoke highlight land on the spoken word for EVERY transcript
  source — faster-whisper's own edges smear into leading silence, and
  YouTube's caption timings are rough by 0.1-0.3 s (see
  ``media.yt_captions``). Words with no speech anywhere near them are
  reported as suspects instead of moved: they are usually
  hallucinations worth a second look, not timing errors.
* ``check_caption_coverage`` — the QA gate: re-read the finished
  words against the envelope and report any speech block with no
  caption over it (a dropped phrase) and any caption sitting over
  silence. The finisher runs it on every live/VOD clip and records the
  verdict in the render result.

Everything here degrades to a no-op on failure: alignment must never
cost a clip its captions. Switch: ``CAPTION_WORD_ALIGN`` (default on;
``off``/``0``/``false``/``no`` disables both halves).
"""
from __future__ import annotations

import logging
import os
import subprocess
import tempfile
import wave
from bisect import bisect_right
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

logger = logging.getLogger(__name__)

Block = Tuple[float, float]

_HOP = 0.05


def word_align_enabled() -> bool:
    return os.getenv("CAPTION_WORD_ALIGN", "on").strip().lower() not in (
        "off", "0", "false", "no",
    )


# ---------------------------------------------------------------------------
# The loudness envelope (wordcaps audio.py, adapted)
# ---------------------------------------------------------------------------

def _rms_db(samples, sample_rate: int, hop: float = _HOP):
    import numpy as np

    n = max(1, int(sample_rate * hop))
    frames = len(samples) // n
    if frames == 0:
        return np.empty(0)
    x = samples[: frames * n].reshape(frames, n)
    rms = np.sqrt((x**2).mean(axis=1) + 1e-12)
    return 20 * np.log10(rms + 1e-9)


def _auto_threshold(db) -> float:
    import numpy as np

    if len(db) == 0:
        return -32.0
    floor, peak = np.percentile(db, 10), np.percentile(db, 95)
    if peak - floor < 12:  # almost no dynamic range: someone talking non-stop
        return float(peak - 12)
    return float(floor + 0.45 * (peak - floor))


def _blocks_from_db(db, hop: float, threshold: float, max_gap: float = 0.2,
                    min_length: float = 0.10) -> List[Block]:
    import numpy as np

    if len(db) == 0:
        return []
    on = (db > threshold).astype(np.int8)
    edges = np.flatnonzero(np.diff(np.concatenate(([0], on, [0]))))
    starts, ends = edges[0::2], edges[1::2]
    gap_frames = max(1, round(max_gap / hop))
    merged: List[List[int]] = []
    for s, e in zip(starts, ends):
        if merged and s - merged[-1][1] <= gap_frames:
            merged[-1][1] = e
        else:
            merged.append([int(s), int(e)])
    return [
        (round(float(s * hop), 3), round(float(e * hop), 3))
        for s, e in merged if (e - s) * hop > min_length
    ]


def speech_blocks(media_path: Path) -> List[Block]:
    """Speech blocks [(start, end)] of a media file's loudness envelope.

    The threshold is estimated from the envelope itself (wordcaps'
    ``auto_threshold``): a fixed dB line misreads both quiet talking
    heads and loud game mixes. Raises on unreadable media; callers use
    the never-raising wrappers below.
    """
    import numpy as np

    with tempfile.TemporaryDirectory(prefix="word_align_") as tmp:
        wav = Path(tmp) / "audio.wav"
        proc = subprocess.run(
            ["ffmpeg", "-v", "error", "-nostdin", "-i", str(media_path),
             "-vn", "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le", "-y", str(wav)],
            capture_output=True, text=True, timeout=300,
        )
        if proc.returncode != 0:
            raise RuntimeError(f"ffmpeg could not read the audio of {media_path}")
        with wave.open(str(wav)) as handle:
            rate, frames, channels = handle.getframerate(), handle.getnframes(), handle.getnchannels()
            data = np.frombuffer(handle.readframes(frames), dtype=np.int16).astype(np.float64) / 32768.0
    if channels > 1:
        data = data.reshape(-1, channels).mean(axis=1)
    db = _rms_db(data, rate)
    return _blocks_from_db(db, _HOP, _auto_threshold(db))


# ---------------------------------------------------------------------------
# align.py: refine_words (wordcaps, adapted to this codebase's word dicts)
# ---------------------------------------------------------------------------

class _Blocks:
    def __init__(self, blocks: Sequence[Block], tolerance: float):
        self.blocks = sorted(blocks)
        self.starts = [s for s, _ in self.blocks]
        self.tol = tolerance

    def containing(self, t: float) -> Optional[Block]:
        i = bisect_right(self.starts, t + self.tol) - 1
        if i >= 0:
            s, e = self.blocks[i]
            if s - self.tol <= t <= e + self.tol:
                return self.blocks[i]
        return None

    def next_start_after(self, t: float) -> Optional[float]:
        i = bisect_right(self.starts, t)
        return self.starts[i] if i < len(self.starts) else None

    def prev_end_before(self, t: float) -> Optional[float]:
        ends = [e for _, e in self.blocks if e <= t]
        return ends[-1] if ends else None

    def distance(self, s: float, e: float) -> float:
        """0 if [s, e] touches speech, else the gap to the nearest block."""
        return min(
            (max(0.0, bs - e, s - be) for bs, be in self.blocks), default=float("inf")
        )


def refine_words(
    words: Sequence[Dict[str, Any]],
    blocks: Sequence[Block],
    *,
    tolerance: float = 0.05,
    min_duration: float = 0.08,
    suspect_gap: float = 0.3,
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """Snap word edges that fall in silence onto the nearest speech edge.

    ``words`` are this codebase's dicts (``word``/``text`` + ``start``/
    ``end`` in seconds); the input is not mutated. Returns the corrected
    words and a log of every change — including ``suspect`` entries for
    words with no speech within ``suspect_gap`` seconds, which are kept
    but flagged (usually hallucinations worth checking).
    """
    out: List[Dict[str, Any]] = []
    log: List[Dict[str, Any]] = []
    if not blocks:
        return [dict(w) for w in words], log
    idx = _Blocks(blocks, tolerance)
    prev_end = 0.0
    for w in words:
        s, e = float(w["start"]), float(w["end"])
        notes = []
        if idx.containing(s) is None:
            nxt = idx.next_start_after(s)
            if nxt is not None and nxt < e:
                s = nxt
                notes.append("start moved to speech onset")
        if idx.containing(e) is None:
            prv = idx.prev_end_before(e)
            if prv is not None and prv > s:
                e = prv
                notes.append("end moved to speech offset")
        if idx.distance(s, e) > suspect_gap:
            notes.append("suspect: no speech near this word, check it")
        s = max(s, prev_end)
        e = max(e, s + min_duration)
        if notes or (s, e) != (float(w["start"]), float(w["end"])):
            log.append({
                "word": w.get("word", w.get("text", "")),
                "before": (float(w["start"]), float(w["end"])),
                "after": (round(s, 3), round(e, 3)),
                "note": ", ".join(notes) or "overlap with previous word removed",
            })
        refined = dict(w)
        refined["start"] = round(s, 4)
        refined["end"] = round(e, 4)
        out.append(refined)
        prev_end = e
    return out, log


def align_words_to_audio(
    words: Sequence[Dict[str, Any]], media_path: Path
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """``refine_words`` against ``media_path``'s own envelope. Never raises."""
    if not word_align_enabled() or not words:
        return [dict(w) for w in words], []
    try:
        blocks = speech_blocks(media_path)
        refined, log = refine_words(words, blocks)
        suspects = sum(1 for entry in log if "suspect" in entry["note"])
        if log:
            logger.info(
                "Word alignment on %s: %d/%d words adjusted, %d suspect",
                Path(media_path).name, len(log), len(words), suspects,
            )
        return refined, log
    except Exception:
        logger.warning("Word alignment failed; keeping the original timings", exc_info=True)
        return [dict(w) for w in words], []


# ---------------------------------------------------------------------------
# verify.py: check_caption_coverage (wordcaps, adapted)
# ---------------------------------------------------------------------------

def _covered(starts: List[float], cues: Sequence[Dict[str, Any]], t: float, tol: float) -> bool:
    i = bisect_right(starts, t + tol) - 1
    for j in range(max(0, i - 2), i + 1):
        c = cues[j]
        if c["start"] - tol <= t < c["end"] + tol:
            return True
    return False


def check_caption_coverage(
    blocks: Sequence[Block],
    cues: Sequence[Dict[str, Any]],
    *,
    step: float = 0.05,
    tolerance: float = 0.1,
) -> Dict[str, Any]:
    """Does every moment of speech have a caption over it?

    ``cues`` are {start, end} spans (seconds). Returns a report dict:
    ``ok`` (no uncovered speech), ``uncovered`` speech spans with no
    caption, ``uncovered_seconds``, and ``silent_cues`` — captions
    sitting over silence. Sampled every ``step`` seconds; ``tolerance``
    forgives captions that start or end slightly off the envelope edge.
    """
    ordered = sorted(cues, key=lambda c: c["start"])
    starts = [c["start"] for c in ordered]
    uncovered: List[Block] = []
    for a, b in blocks:
        t, run_start = a, None
        while t < b:
            hit = bool(ordered) and _covered(starts, ordered, t, tolerance)
            if not hit and run_start is None:
                run_start = t
            if hit and run_start is not None:
                uncovered.append((round(float(run_start), 2), round(float(t), 2)))
                run_start = None
            t += step
        if run_start is not None:
            uncovered.append((round(float(run_start), 2), round(float(b), 2)))
    silent = [
        {"start": c["start"], "end": c["end"]}
        for c in ordered
        if not any(e > c["start"] + 0.1 and s < c["end"] - 0.1 for s, e in blocks)
    ]
    return {
        "ok": not uncovered,
        "uncovered": uncovered,
        "uncovered_seconds": round(sum(e - s for s, e in uncovered), 2),
        "silent_cues": silent,
    }


def caption_coverage_for(
    media_path: Path, cues: Sequence[Dict[str, Any]]
) -> Optional[Dict[str, Any]]:
    """The coverage gate for one media file. Never raises; None = not run."""
    if not word_align_enabled():
        return None
    try:
        return check_caption_coverage(speech_blocks(media_path), cues)
    except Exception:
        logger.warning("Caption coverage check failed to run", exc_info=True)
        return None
