"""From a live reaction flag to a clip worth posting.

- ``speech_window``: where the clip starts and stops. It opens at most
  ``lead_max`` seconds before the reaction, so the hook comes first (the
  owner's review: "20 sec that streamer is not even talking, where is the
  hook?"), and runs on while the streamer keeps talking.
- ``creator_on_camera``: the streamer must be visible in the facecam and
  clearly moving at the reaction. On a stream the game and the mic share one
  audio track, so a voice flag can be game characters in a cutscene.
- ``keep_streamer_words``: transcript words spoken while a game subtitle is
  on screen are the game's lines, not the streamer's, and are not captioned.
"""
from __future__ import annotations

import json
import logging
import subprocess
from pathlib import Path
from typing import Dict, Optional, Sequence, Tuple

logger = logging.getLogger(__name__)


def speech_window(voiced: Sequence[float], onset: float, end: float, *, lead_max: float = 6.0,
                  min_len: float = 12.0, max_len: float = 30.0, talk_frac: float = 0.25,
                  tail_gap: int = 2) -> Tuple[float, float, float]:
    """``(start, stop, talk_share)`` in the seconds of ``voiced``.

    ``voiced[i]`` is the voiced fraction of second ``i``. The start walks back
    from the onset over the speech leading into it (at most ``lead_max`` s,
    and at least 2 s of lead-in). The stop is the first silence longer than
    ``tail_gap`` s after the flag; the length stays in [min_len, max_len].
    ``talk_share`` is the fraction of seconds in the window with speech.
    """
    n = len(voiced)
    talking = [float(v) >= talk_frac for v in voiced]
    s = max(0, min(int(onset), n))
    start = s
    while (start - 1 >= 0 and onset - (start - 1) <= lead_max
           and (talking[start - 1] or (start - 2 >= 0 and talking[start - 2]))):
        start -= 1
    start = min(start, max(0, s - 2))
    stop, gap = min(n, int(end) + 1), 0
    while stop < n and stop - start < max_len:
        gap = 0 if talking[stop] else gap + 1
        if gap > tail_gap:
            stop -= gap - 1
            break
        stop += 1
    stop = min(max(stop, start + int(min_len)), start + int(max_len))
    if n:
        stop = min(stop, n)
    share = sum(talking[start:stop]) / max(1, stop - start)
    return float(start), float(stop), share


def _probe_size(video: Path) -> Optional[Tuple[int, int]]:
    out = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
                          "stream=width,height", "-of", "csv=p=0", str(video)], capture_output=True, text=True)
    try:
        w, h = [int(x) for x in out.stdout.strip().split(",")[:2]]
        return w, h
    except ValueError:
        return None


def creator_on_camera(window: Path, onset_in_window: float, *, min_ratio: float = 1.8, cam_box=None) -> Tuple[bool, str]:
    """Is the streamer's face visible in the facecam, and moving, at the reaction?

    Visible in >= 2 of 3 facecam frames (onset-1, +1, +3 s) and the face's
    movement at the moment >= ``min_ratio`` x its median in the rest of the
    window. Returns ``(ok, reason)``.
    """
    import numpy as np

    from ..auto_director.reaction_moments import extract_face_activity
    from ..media.face_detection import open_yunet_detector
    from ..media.facecam_rect import find_facecam_rect, probe_times

    size = _probe_size(window)
    if size is None:
        return False, "window has no video"
    w, h = size
    dur = float(subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0",
                                str(window)], capture_output=True, text=True).stdout.strip() or 0)
    if cam_box:
        x, y = int(cam_box["x"] * w), int(cam_box["y"] * h)
        cw, ch = max(2, int(cam_box["w"] * w)) // 2 * 2, max(2, int(cam_box["h"] * h)) // 2 * 2
    else:
        found = find_facecam_rect(str(window), w, h, probe_times([(0.0, max(dur, 1.0))]))
        if found is None:
            return False, "no facecam found"
        x, y, cw, ch = [int(v) for v in found.rect]
    detector = open_yunet_detector()
    hits = 0
    for t in (onset_in_window - 1, onset_in_window + 1, onset_in_window + 3):
        raw = subprocess.run(["ffmpeg", "-v", "error", "-ss", f"{max(0.0, t):.2f}", "-i", str(window),
                              "-frames:v", "1", "-vf", f"crop={cw}:{ch}:{x}:{y}", "-f", "rawvideo",
                              "-pix_fmt", "bgr24", "pipe:1"], capture_output=True).stdout
        if len(raw) >= cw * ch * 3:
            frame = np.frombuffer(raw[: cw * ch * 3], dtype=np.uint8).reshape(ch, cw, 3)
            if detector is not None and detector.detect(frame):
                hits += 1
    if hits < 2:
        return False, f"face visible in only {hits}/3 facecam frames (cutscene or game voices)"
    activity = extract_face_activity(str(window), (x / w, y / h, cw / w, ch / h))
    if len(activity) < 10:
        return True, f"face visible {hits}/3 (movement not measurable)"
    lo, hi = int(max(0, onset_in_window - 2)), int(min(len(activity), onset_in_window + 5))
    at_moment = float(np.max(activity[lo:hi])) if hi > lo else 0.0
    rest = np.concatenate([activity[:lo], activity[hi:]])
    base = float(np.median(rest)) if len(rest) else 0.0
    ratio = at_moment / max(base, 1e-6)
    if ratio >= min_ratio:
        return True, f"face visible {hits}/3 and moving at the moment ({ratio:.1f}x usual)"
    return False, f"face visible but still at the moment ({ratio:.1f}x usual): the voices were not a reaction"


# Game subtitles (GTA VI and most games): white text with a dark outline,
# bottom centre. Per second, the share of bright colourless pixels touching
# dark ones in that zone x 1000. Measured on stream windows: 2.4-13.7 with a
# subtitle, exactly 0.0 without.
GAME_SUBTITLE_SCORE = 1.5


def game_subtitle_seconds(video: Path) -> list:
    import cv2
    import numpy as np

    capture = cv2.VideoCapture(str(video))
    fps = float(capture.get(cv2.CAP_PROP_FPS) or 30.0)
    total = int(capture.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
    seconds = int(total / fps) if fps else 0
    kernel = np.ones((3, 3), np.uint8)
    flags = []
    for sec in range(seconds + 1):
        capture.set(cv2.CAP_PROP_POS_FRAMES, int(min(max(total - 1, 0), (sec + 0.5) * fps)))
        ok, frame = capture.read()
        score = 0.0
        if ok:
            h, w = frame.shape[:2]
            band = frame[int(h * 0.80):int(h * 0.95), int(w * 0.25):int(w * 0.75)]
            hsv = cv2.cvtColor(band, cv2.COLOR_BGR2HSV)
            white = (hsv[..., 2] > 215) & (hsv[..., 1] < 40)
            near_dark = cv2.dilate((hsv[..., 2] < 90).astype(np.uint8), kernel) > 0
            score = float((white & near_dark).mean()) * 1000.0
        flags.append(score >= GAME_SUBTITLE_SCORE)
    capture.release()
    return flags


def drop_game_words(words: list, game_sub: Sequence[bool]) -> list:
    """Words (ms timings) not overlapping any second with a game subtitle."""
    kept = []
    for word in words:
        st, en = float(word.get("start", 0)) / 1000.0, float(word.get("end", 0)) / 1000.0
        secs = range(int(st), int(en) + 1)
        if not any(0 <= s < len(game_sub) and game_sub[s] for s in secs):
            kept.append(word)
    return kept


def keep_streamer_words(source: Path, data: Dict) -> Dict:
    """Filter the transcript (and its cache, which the caption builder reads)."""
    words = data.get("words") or []
    if not words:
        return data
    kept = drop_game_words(words, game_subtitle_seconds(source))
    cache = source.with_suffix(".transcript_cache.json")
    if cache.exists():
        full = source.with_suffix(".transcript_cache.full.json")
        if not full.exists():
            full.write_text(cache.read_text(encoding="utf-8"), encoding="utf-8")
        cached = json.loads(cache.read_text(encoding="utf-8"))
        cached["words"] = kept
        cache.write_text(json.dumps(cached), encoding="utf-8")
    logger.info("Live captions: kept %d of %d words (game subtitles removed)", len(kept), len(words))
    return {**data, "words": kept}
