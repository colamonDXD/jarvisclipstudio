"""Step 5 of the clip1 correction: captions that read as complete thoughts.

Root cause #6 of clip1.mp4 (handoff/quality-clip1/ROOT_CAUSE.md):
(a) the transcript repeated "I'm like, I just want to have a war" about a
    dozen times over loud crowd audio, a speech-to-text repetition loop,
    not what was said; and
(b) captions were cut by a hard word count, leaving orphans ("just",
    "have a...").

``CAPTION_LOOP_FILTER`` (default off until approved): drop a phrase of 3+
words that repeats back-to-back 3+ times; keep the first occurrence. The
audio is untouched; only the captions stop repeating.

``CAPTION_PHRASE_SPLIT`` (default off until approved): line breaks prefer
sentence ends, pauses and commas; a sentence is never left with a single
orphan word on its own line when a balanced split exists.
"""
from __future__ import annotations

import os
import re
from typing import Any, Dict, List, Tuple

LOOP_MIN_PHRASE = 3
LOOP_MAX_PHRASE = 14
LOOP_MIN_REPEATS = 3
_SENTENCE_END = re.compile(r"[.!?…]+[\"')\]]*$")
_CLAUSE_END = re.compile(r"[,;:—–-]+[\"')\]]*$")


def _on(name: str) -> bool:
    return os.getenv(name, "off").strip().lower() in ("on", "1", "true", "yes")


def loop_filter_enabled() -> bool:
    return _on("CAPTION_LOOP_FILTER")


def phrase_split_enabled() -> bool:
    return _on("CAPTION_PHRASE_SPLIT")


def _norm(word: Dict[str, Any]) -> str:
    return re.sub(r"[^\w']+", "", str(word.get("text", "")).lower())


def drop_repetition_loops(words: List[Dict[str, Any]]) -> Tuple[List[Dict[str, Any]], int]:
    """Words with back-to-back repeats of a 3+ word phrase removed.

    Returns ``(kept_words, dropped_count)``. Single repeated words
    ("no no no") and two-word echoes are real speech and are kept.
    """
    tokens = [_norm(w) for w in words]
    keep = [True] * len(words)
    i = 0
    n_words = len(words)
    while i < n_words:
        best = None
        for size in range(LOOP_MIN_PHRASE, LOOP_MAX_PHRASE + 1):
            if i + size * LOOP_MIN_REPEATS > n_words:
                break
            phrase = tokens[i:i + size]
            if not all(phrase):
                continue
            repeats = 1
            j = i + size
            while j + size <= n_words and tokens[j:j + size] == phrase:
                repeats += 1
                j += size
            if repeats >= LOOP_MIN_REPEATS and (best is None or j - i > best[1] - i):
                best = (size, j)
        if best is None:
            i += 1
            continue
        size, end = best
        for k in range(i + size, end):
            keep[k] = False
        i = end
    kept = [w for w, k in zip(words, keep) if k]
    return kept, n_words - len(kept)


def split_word_spans_phrase(
    words: List[Dict[str, Any]], max_words: int, pause_break_seconds: float
) -> List[Tuple[int, int]]:
    """``(start, stop)`` spans that follow the phrasing of the speech."""
    if not words:
        return []
    max_words = max(1, int(max_words))
    # 1) Sentences / pause-separated runs.
    runs: List[Tuple[int, int]] = []
    start = 0
    for index in range(len(words)):
        stop = index + 1
        last = stop == len(words)
        if not last:
            gap = float(words[stop]["start"]) - float(words[index]["end"])
        if last or _SENTENCE_END.search(str(words[index].get("text", "")).strip()) or gap >= pause_break_seconds:
            runs.append((start, stop))
            start = stop
    # 2) Within a run: break at clause ends when the line has 2+ words,
    #    else balance the run so no line holds a lone leftover word.
    spans: List[Tuple[int, int]] = []
    for run_start, run_stop in runs:
        cursor = run_start
        while cursor < run_stop:
            remaining = run_stop - cursor
            if remaining <= max_words:
                spans.append((cursor, run_stop))
                break
            # A clause end inside the next max_words words (keeping 2+ per line).
            cut = None
            for k in range(cursor + 1, min(cursor + max_words, run_stop - 1)):
                if _CLAUSE_END.search(str(words[k].get("text", "")).strip()) and k + 1 - cursor >= 2:
                    cut = k + 1
            if cut is None:
                lines = -(-remaining // max_words)
                cut = cursor + -(-remaining // lines)
            spans.append((cursor, cut))
            cursor = cut
    return spans
