"""Loop-engineered and payoff-tight clip endings (viral build queue #3 + #7).

Boundaries in this app are finalised in layers: the pickers shape a window
around the moment, GA2's ``refine_window`` snaps it to speech, the render
path extends a mid-sentence end to the sentence boundary
(``media/timeline.py``) and enforces the owner's minimum length
(``video_utils.enforce_min_clip_duration``). This module is the LAST
boundary step, composed with those neighbours, never fighting them:

- ``PAYOFF_TIGHT`` (default off): trim the trailing tail after the payoff
  so the clip ends at most ``PAYOFF_TIGHT_MAX_TAIL_SEC`` after the payoff
  completes. The payoff's spoken sentence still ends whole — when the
  sentence runs past the 1 s mark, the sentence wins (the same
  snap-to-sentence rule the render path applies, with its pad and
  next-word guard from ``media/common.py``). The length floor wins over
  tightening: a trim that would take the clip under the floor is refused.
- ``LOOP_ENDINGS`` (default off): among the end candidates the tight
  anchor leaves available (sentence ends between the payoff anchor and
  the current end), prefer one whose closing spoken phrase matches the
  clip's opening phrase — and, cheaply, whose last frame's brightness /
  colour sits close to the first frame's, so the clip loops on replay.
  The preference is a bounded bonus of at most ``LOOP_ENDING_EPSILON``
  boundary-score points: it can only break near-ties (a candidate more
  than ``LOOP_ENDING_EPSILON / LOOP_DISTANCE_RATE`` seconds from the
  current end can never win on loop rhyme alone), and it can never pull
  the end before the payoff's sentence has completed.

The payoff "completes" when the reaction burst ends, extended while the
reaction-strength series is still active — the die-down rule
``refine_window`` already uses, without its pad. Without a strength
series the burst end itself is the completion point.

Everything here is pure (word lists and numbers in, a decision record
out) except ``frame_rhyme_score``, which reads two frames. Callers gate
on the switches, so with both off the boundary path is untouched.
"""
from __future__ import annotations

import os
import re
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

# The whole loop-rhyme bonus budget, in boundary-score points. A candidate
# end scores base = -LOOP_DISTANCE_RATE * |candidate - current end|; the
# incumbent end scores 0. A perfect loop match therefore pulls the end at
# most LOOP_ENDING_EPSILON / LOOP_DISTANCE_RATE = 2.5 s, and a candidate
# farther away than that can never beat a clearly better-placed end.
LOOP_ENDING_EPSILON = 0.05
LOOP_DISTANCE_RATE = 0.02  # boundary-score points per second from the current end

# Payoff-tight: the clip ends at most this long after the payoff completes
# (unless the payoff's sentence runs longer — the sentence wins).
PAYOFF_TIGHT_MAX_TAIL_SEC = 1.0

# How far past the burst end the payoff may still be completing (the
# refine_window die-down rule's cap).
_PAYOFF_DIE_DOWN_CAP_SEC = 4.0

# A pause at least this long between two words also ends a "sentence"
# for boundary purposes (the same 0.3 s refine_window calls "a pause
# between sentences"). Word sources differ: the app's AssemblyAI words
# carry punctuation (transcription requests punctuate=True), while
# faster-whisper / Groq / YouTube-caption words are bare — without the
# pause rule those transcripts would have no sentence ends at all
# before the very last word (measured on the streamer fixture: 607 real
# Whisper words, zero punctuation marks, and only one inter-word gap
# over 0.25 s in the whole file — spliced continuous speech; on such
# unsegmented speech the sentence is taken to run on, and no trim is
# made: the snap rule wins by construction).
SENTENCE_PAUSE_SEC = 0.3

# Sentence-final punctuation, the media/common.py SENTENCE_END_RE rule.
_SENTENCE_END_RE = re.compile(r"""[.!?]["')\]}]*$""")
_TOKEN_RE = re.compile(r"[a-z0-9']+")

# A loop rhyme on one of these as the final word is meaningless ("...the"
# into "the ..."), so a match's last word must be a content word.
_FILLER_WORDS = frozenset({
    "the", "a", "an", "and", "but", "or", "so", "yeah", "yes", "no", "oh",
    "uh", "um", "hmm", "like", "you", "i", "it", "is", "to", "of", "on",
    "in", "at", "my", "me", "we", "he", "she", "they", "gonna", "wanna",
})


def loop_endings_enabled() -> bool:
    """LOOP_ENDINGS (default off until the owner approves the proof)."""
    return os.getenv("LOOP_ENDINGS", "off").strip().lower() in ("on", "1", "true", "yes")


def payoff_tight_enabled() -> bool:
    """PAYOFF_TIGHT (default off until the owner approves the proof)."""
    return os.getenv("PAYOFF_TIGHT", "off").strip().lower() in ("on", "1", "true", "yes")


# ---------------------------------------------------------------------------
# Word helpers (words are {text, start, end} in seconds)
# ---------------------------------------------------------------------------

def _word_text(word: Dict[str, Any]) -> str:
    return str(word.get("text") or word.get("word") or "").strip()


def _sorted_words(words: Optional[Sequence[Dict[str, Any]]]) -> List[Dict[str, Any]]:
    out = [w for w in (words or []) if _word_text(w)]
    out.sort(key=lambda w: (float(w.get("start", 0) or 0), float(w.get("end", 0) or 0)))
    return out


def _norm_token(text: str) -> str:
    match = _TOKEN_RE.search(text.lower())
    return match.group(0) if match else ""


def _is_sentence_end(ws: List[Dict[str, Any]], i: int) -> bool:
    """Word ``i`` ends a sentence: sentence-final punctuation, or the
    last word, or a pause of at least SENTENCE_PAUSE_SEC after it."""
    if _SENTENCE_END_RE.search(_word_text(ws[i])):
        return True
    if i + 1 >= len(ws):
        return True
    gap = float(ws[i + 1].get("start", 0) or 0) - float(ws[i].get("end", 0) or 0)
    return gap >= SENTENCE_PAUSE_SEC


def sentence_end_times(words: Sequence[Dict[str, Any]]) -> List[float]:
    """End times of every sentence in ``words`` (punctuation or pause)."""
    ws = _sorted_words(words)
    return [float(w.get("end", 0) or 0) for i, w in enumerate(ws) if _is_sentence_end(ws, i)]


def payoff_sentence_end(words: Optional[Sequence[Dict[str, Any]]], payoff_end: float) -> Optional[float]:
    """End of the sentence the payoff completes in, or None without words.

    The payoff's last word is the last word ending at or before
    ``payoff_end`` (or the word the payoff lands inside); its sentence
    ends at the first sentence-final word from there. When the payoff
    lands past every word, the last word's end is the sentence end.
    """
    ws = _sorted_words(words)
    if not ws:
        return None
    anchor = None
    for i, w in enumerate(ws):
        if float(w.get("start", 0) or 0) <= payoff_end:
            anchor = i
        else:
            break
    if anchor is None:
        return None
    for i in range(anchor, len(ws)):
        if _is_sentence_end(ws, i):
            return float(ws[i].get("end", 0) or 0)
    return float(ws[-1].get("end", 0) or 0)


def payoff_completion(reaction_end: float, strength: Optional[Sequence[float]] = None) -> float:
    """When the payoff is really over: the burst end, extended while the
    reaction-strength series is still active (refine_window's die-down
    rule, capped, without its pad)."""
    if strength is None or not len(strength):
        return float(reaction_end)
    t = int(reaction_end)
    extra = 0
    while extra < _PAYOFF_DIE_DOWN_CAP_SEC and t + extra < len(strength) and float(strength[t + extra]) > 0:
        extra += 1
    return float(reaction_end) + extra


def _padded_end(anchor: float, words: Sequence[Dict[str, Any]]) -> float:
    """``anchor`` plus the render path's clip-end pad, never starting the
    next word (media/common.py: CLIP_END_PADDING_SECONDS /
    CLIP_END_NEXT_WORD_GUARD_SECONDS, composed — not re-invented)."""
    from ..media.common import CLIP_END_NEXT_WORD_GUARD_SECONDS, CLIP_END_PADDING_SECONDS

    end = anchor + CLIP_END_PADDING_SECONDS
    later = [float(w.get("start", 0) or 0) for w in words if float(w.get("start", 0) or 0) > anchor + 0.01]
    if later:
        end = min(end, max(anchor, min(later) - CLIP_END_NEXT_WORD_GUARD_SECONDS))
    return end


# ---------------------------------------------------------------------------
# Loop rhyme
# ---------------------------------------------------------------------------

def _phrase(words: Sequence[Dict[str, Any]], *, opening: bool) -> List[str]:
    toks = [_norm_token(_word_text(w)) for w in words]
    toks = [t for t in toks if t]
    return toks[:3] if opening else toks[-3:]


def verbal_loop_match(opening_words: Sequence[Dict[str, Any]],
                      closing_words: Sequence[Dict[str, Any]]) -> Tuple[float, str]:
    """0..1 rhyme between the clip's opening phrase and a candidate's
    closing phrase, plus the matched phrase for the record. An exact
    3-word match scores 1.0, 2 words 0.85, a single content word 0.6."""
    opening, closing = _phrase(opening_words, opening=True), _phrase(closing_words, opening=False)
    if not opening or not closing:
        return 0.0, ""
    for k in (3, 2, 1):
        if len(opening) >= k and len(closing) >= k and opening[:k] == closing[-k:]:
            phrase = " ".join(opening[:k])
            if opening[k - 1] in _FILLER_WORDS:
                continue  # a rhyme on a filler word is not a loop
            if k == 1 and len(opening[0]) < 3:
                continue  # a lone short word ("go", "oh") is not a loop either
            return {3: 1.0, 2: 0.85, 1: 0.6}[k], phrase
    return 0.0, ""


def frame_rhyme_score(video_path: Any, start: float, end: float) -> Optional[float]:
    """0..1 closeness of the clip's first frame and its last frame (mean
    colour + brightness) — the two frames that touch when a clip loops.
    None when either frame cannot be read."""
    try:
        import cv2
        import numpy as np
    except Exception:
        return None

    def grab(t: float) -> Optional[Any]:
        cap = cv2.VideoCapture(str(video_path))
        try:
            cap.set(cv2.CAP_PROP_POS_MSEC, max(0.0, t) * 1000.0)
            ok, frame = cap.read()
            if not ok or frame is None:
                return None
            f = frame.astype(np.float64)
            return np.concatenate([f.mean(axis=(0, 1)), [f.mean()]])
        finally:
            cap.release()

    a, b = grab(start + 0.15), grab(max(start, end - 0.15))
    if a is None or b is None:
        return None
    dist = float(np.abs(a - b).mean()) / 255.0
    return round(1.0 - min(1.0, dist / 0.35), 3)


# ---------------------------------------------------------------------------
# The refinement
# ---------------------------------------------------------------------------

def refine_clip_end(
    start: float,
    end: float,
    *,
    payoff_end: float,
    words: Optional[Sequence[Dict[str, Any]]] = None,
    duration: Optional[float] = None,
    min_seconds: Optional[float] = None,
    strength: Optional[Sequence[float]] = None,
    visual: Optional[Callable[[float], Optional[float]]] = None,
    loop_on: Optional[bool] = None,
    tight_on: Optional[bool] = None,
) -> Tuple[float, Dict[str, Any]]:
    """Refine a clip's end boundary. Returns ``(new_end, record)``.

    ``payoff_end`` is when the moment the clip was picked for completes
    (extended over the strength die-down). ``min_seconds`` is the length
    floor tightening must respect; the default is the owner's
    ``CLIP_MIN_SECONDS`` via ``video_utils.min_clip_seconds`` — a caller
    whose layer carries its own shaping floor passes it explicitly.
    ``visual`` maps a candidate end to its frame-rhyme score (0..1)
    against the clip's first frame, when the caller can read frames.
    With both switches off the end is returned untouched.
    """
    loop_on = loop_endings_enabled() if loop_on is None else loop_on
    tight_on = payoff_tight_enabled() if tight_on is None else tight_on
    record: Dict[str, Any] = {"applied": False, "loop_endings": bool(loop_on),
                              "payoff_tight": bool(tight_on), "old_end": round(float(end), 3),
                              "new_end": round(float(end), 3), "moved_sec": 0.0, "reason": ""}
    if not (loop_on or tight_on):
        record["reason"] = "switches off"
        return float(end), record
    if min_seconds is None:
        from ..video_utils import min_clip_seconds

        min_seconds = min_clip_seconds()
    ws = _sorted_words(words)
    complete = payoff_completion(payoff_end, strength)
    s_end = payoff_sentence_end(ws, complete)
    anchor_time = max(complete, s_end) if s_end is not None else complete
    anchor = _padded_end(anchor_time, ws)
    if duration is not None:
        anchor = min(anchor, float(duration))
    record.update({"payoff_complete": round(complete, 3),
                   "sentence_end": None if s_end is None else round(s_end, 3),
                   "anchor_end": round(anchor, 3)})
    if end <= anchor:
        record["reason"] = "end already at or before the payoff anchor"
        return float(end), record

    incumbent = float(end)
    reasons: List[str] = []
    tight_applied = False

    # --- Payoff-tight: the deterministic trim -----------------------------
    if tight_on:
        if anchor - start < float(min_seconds):
            record["tight_refused"] = (f"tight end {anchor:.2f} would break the "
                                       f"{float(min_seconds):.0f}s floor from start {start:.2f}")
        else:
            incumbent = anchor
            tight_applied = True
            reasons.append(
                f"payoff tail trimmed: end {end:.2f} -> {anchor:.2f} "
                f"(payoff complete {complete:.2f}"
                + (f", sentence ends {s_end:.2f}" if s_end is not None else "")
                + (", sentence past the 1s mark wins" if s_end is not None and s_end - complete > PAYOFF_TIGHT_MAX_TAIL_SEC else "")
                + ")")

    # --- Loop endings: a bounded bonus among near-equal candidates --------
    # Skipped when the tight trim already placed the end on the payoff
    # anchor: every legal candidate sits at or before that anchor, so the
    # loop has nothing left to choose (the features compose, tight first).
    if loop_on and not tight_applied:
        opening = [w for w in ws if float(w.get("start", 0) or 0) >= start - 0.01][:3]

        def candidate_total(c: float) -> Tuple[float, str]:
            # Distance is measured from the ORIGINAL end: the bonus may
            # only break ties among ends that were near-equal before this
            # refinement ran.
            base = -LOOP_DISTANCE_RATE * abs(c - float(end))
            closing = [w for w in ws if float(w.get("end", 0) or 0) <= c + 0.01]
            verbal, phrase = verbal_loop_match(opening, closing) if ws else (0.0, "")
            visual_score = visual(c) if visual is not None else None
            combined = (0.7 * verbal + 0.3 * float(visual_score)) if visual_score is not None else verbal
            note = ""
            if combined > 0:
                note = (f"loop match: closing '{phrase}' rhymes with the opening"
                        if phrase else "loop match: end frame rhymes with the first frame")
                if visual_score is not None:
                    note += f" (words {verbal:.2f}, frame {float(visual_score):.2f})"
            return base + LOOP_ENDING_EPSILON * combined, note

        candidates = {round(float(end), 3), round(anchor, 3)}
        for t in sentence_end_times(ws):
            if anchor_time - 0.01 <= t <= end:
                c = _padded_end(t, ws)
                if anchor - 0.01 <= c <= end:
                    candidates.add(round(c, 3))
        best_end, (best_total, best_note) = incumbent, candidate_total(incumbent)
        for c in sorted(candidates):
            if c == incumbent or c - start < float(min_seconds):
                continue
            total, note = candidate_total(c)
            if total > best_total + 1e-9:
                best_end, best_total, best_note = c, total, note
        if best_end != incumbent:
            incumbent = best_end
            reasons.append(best_note)

    if incumbent != float(end):
        record.update({"applied": True, "new_end": round(incumbent, 3),
                       "moved_sec": round(float(end) - incumbent, 3),
                       "reason": "; ".join(reasons)})
    else:
        record["reason"] = "; ".join(reasons) if reasons else "no better ending found"
    return incumbent, record
