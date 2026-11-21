"""Step 4 of the clip1 correction: selection that sees non-speech moments.

Root cause #1c of clip1.mp4 (handoff/quality-clip1/ROOT_CAUSE.md): clip
selection reads only the transcript. In the clip1 source the story (the
"Best IRL Streamer" nominees on the stage screen, the announcement, the
walk to the stage and the speech) happens at 165-240 s with music and
applause but NO transcribed speech, so no transcript-only selector, AI or
not, can see it, and the clip that was cut ended right before the payoff.

This module looks at the picture. One frame every ``SAMPLE_EVERY_SEC`` is
scored with the SigLIP model the B-roll vision veto already uses
(``google/siglip-base-patch16-224``, Apache-2.0, local) against short
descriptions of story moments versus background shots. Consecutive
samples where a story description wins become event spans. They are used
three ways, all behind ``SELECTION_EVENTS`` (default off until the owner
approves the corrected render, Sanji Q15):

- ``event_signal_lines``: ``- [mm:ss - mm:ss] ...`` lines appended to the
  selector's ``clip_signals`` hints, the channel reaction hints already use;
- ``extend_to_payoff``: a selected clip whose end runs into an event keeps
  going until the event finishes (and the sentence in progress ends),
  within the length cap;
- ``event_window``: the no-model fallback picks the strongest event run
  with its lead-in, instead of a fixed window.

It never invents content: spans only describe what the frames score as,
and every span records its label and score. Any failure (no model, no
frames) returns no events and selection behaves exactly as before.
"""
from __future__ import annotations

import logging
import os
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

logger = logging.getLogger(__name__)

SAMPLE_EVERY_SEC = 2.0
IMAGE_SIZE = 224
MIN_MARGIN = 0.01        # story label must beat the best background label by this
MAX_GAP_SEC = 4.0        # samples further apart than this split a span
MIN_SPAN_SEC = 4.0

STORY_LABELS: Dict[str, str] = {
    "award": "a person on stage holding an award trophy",
    "applause": "an audience clapping and cheering",
    "screen": "a large screen showing a video, names or text",
    "stage_speech": "a person speaking into a microphone on a stage",
    "celebrate": "people hugging and celebrating",
    "laugh": "a person laughing hard",
    "gameplay": "a video game being played on screen",
}
BACKGROUND_LABELS: Dict[str, str] = {
    "seated": "people sitting at tables at an event",
    "dark": "a dark, blurry room",
    "backs": "the backs of people's heads in a crowd",
    "talking": "a person talking to the camera",
}


def story_events_enabled() -> bool:
    return os.getenv("SELECTION_EVENTS", "off").strip().lower() in ("on", "1", "true", "yes")


@dataclass
class EventSpan:
    start: float
    end: float
    label: str
    score: float  # mean margin over the span's samples

    def as_dict(self) -> Dict[str, Any]:
        return {"start": round(self.start, 2), "end": round(self.end, 2),
                "label": self.label, "description": STORY_LABELS.get(self.label, self.label),
                "score": round(self.score, 4)}


def sample_frames(video_path: Path, start: float = 0.0, duration: Optional[float] = None,
                  every: float = SAMPLE_EVERY_SEC) -> List[Tuple[float, Any]]:
    """(time, 224x224 RGB array) every ``every`` seconds, in one decode."""
    import numpy as np

    cmd = ["ffmpeg", "-v", "error"]
    if start > 0:
        cmd += ["-ss", f"{start:.3f}"]
    cmd += ["-i", str(video_path)]
    if duration:
        cmd += ["-t", f"{duration:.3f}"]
    cmd += ["-an", "-vf", f"fps=1/{every},scale={IMAGE_SIZE}:{IMAGE_SIZE}",
            "-pix_fmt", "rgb24", "-f", "rawvideo", "pipe:1"]
    try:
        raw = subprocess.run(cmd, capture_output=True, timeout=900).stdout
    except Exception as exc:
        logger.warning("Story events: frame sampling failed: %s", exc)
        return []
    size = IMAGE_SIZE * IMAGE_SIZE * 3
    frames = []
    for index in range(len(raw) // size):
        arr = np.frombuffer(raw[index * size:(index + 1) * size], dtype=np.uint8).reshape(IMAGE_SIZE, IMAGE_SIZE, 3)
        frames.append((start + index * every, arr))
    return frames


def score_frames(frames: Sequence[Any], labels: Sequence[str]) -> Optional[Any]:
    """Cosine-similarity matrix [frames x labels] from SigLIP; None if unavailable."""
    if not frames:
        return None
    try:
        import numpy as np
        import torch
        from transformers import AutoModel

        from ..media import broll_vision as bv
        from ..media.separation import separation_models_dir

        os.environ.setdefault("HF_HOME", str(separation_models_dir()))
        device = bv._vision_device() or "cpu"
        tokenizer = bv._load_tokenizer()
        model = AutoModel.from_pretrained(bv.MODEL_ID).to(device).eval()
        text = tokenizer(list(labels), padding="max_length", max_length=64,
                         truncation=True, return_tensors="pt")
        rows = []
        with torch.no_grad():
            for i in range(0, len(frames), 32):
                batch = np.stack([np.asarray(f) for f in frames[i:i + 32]])
                pixels = torch.from_numpy(batch).permute(0, 3, 1, 2).float()
                pixels = ((pixels / 255.0 - 0.5) / 0.5).to(device)
                image_emb, text_emb = bv._embed(model, pixels, text["input_ids"].to(device))
                image_emb = image_emb / image_emb.norm(dim=-1, keepdim=True)
                text_emb = text_emb / text_emb.norm(dim=-1, keepdim=True)
                rows.append((image_emb @ text_emb.T).cpu().numpy())
        del model
        return np.concatenate(rows, axis=0)
    except Exception as exc:
        logger.warning("Story events: SigLIP scoring unavailable (%s)", exc)
        return None


def spans_from_scores(times: Sequence[float], sims: Any) -> List[EventSpan]:
    """Merge consecutive samples whose best story label beats the background."""
    story_keys = list(STORY_LABELS)
    n_story = len(story_keys)
    spans: List[EventSpan] = []
    current: Optional[Dict[str, Any]] = None
    for t, row in zip(times, sims):
        story = row[:n_story]
        best = int(story.argmax())
        margin = float(story[best] - row[n_story:].max())
        if margin >= MIN_MARGIN:
            label = story_keys[best]
            if current and t - current["last"] <= MAX_GAP_SEC:
                current["last"] = t
                current["margins"].append(margin)
                current["labels"].append(label)
                continue
            if current:
                spans.append(_close(current))
            current = {"start": t, "last": t, "margins": [margin], "labels": [label]}
    if current:
        spans.append(_close(current))
    return [s for s in spans if s.end - s.start >= MIN_SPAN_SEC]


def _close(current: Dict[str, Any]) -> EventSpan:
    labels = current["labels"]
    label = max(set(labels), key=labels.count)
    margins = current["margins"]
    return EventSpan(current["start"], current["last"] + SAMPLE_EVERY_SEC, label,
                     sum(margins) / len(margins))


def detect_story_events(video_path: Path, start: float = 0.0,
                        duration: Optional[float] = None) -> List[EventSpan]:
    frames = sample_frames(video_path, start, duration)
    if not frames:
        return []
    sims = score_frames([f for _, f in frames], list(STORY_LABELS.values()) + list(BACKGROUND_LABELS.values()))
    if sims is None:
        return []
    return spans_from_scores([t for t, _ in frames], sims)


def _mmss(seconds: float) -> str:
    s = max(0, int(seconds))
    return f"{s // 60:02d}:{s % 60:02d}"


def _speech_seconds(words: Sequence[Dict[str, Any]], start: float, end: float) -> float:
    total = 0.0
    for w in words:
        a, b = max(start, float(w["start"])), min(end, float(w["end"]))
        if b > a:
            total += b - a
    return total


def event_signal_lines(spans: Sequence[EventSpan], words: Sequence[Dict[str, Any]]) -> str:
    """Hint lines in the selector's clip_signals format."""
    lines = []
    for s in spans:
        quiet = _speech_seconds(words, s.start, s.end) < 0.25 * (s.end - s.start)
        lines.append(
            f"- [{_mmss(s.start)} - {_mmss(s.end)}] visual story moment: "
            f"{STORY_LABELS.get(s.label, s.label)}"
            + (" (little or no transcribed speech: the picture carries this moment)" if quiet else "")
        )
    if not lines:
        return ""
    return "Visual story moments (sampled frames, SigLIP):\n" + "\n".join(lines)


def _sentence_end_after(words: Sequence[Dict[str, Any]], t: float, limit: float) -> float:
    for w in words:
        if float(w["end"]) >= t and str(w.get("text", "")).rstrip().endswith((".", "?", "!")):
            return min(float(w["end"]), limit)
    return t


def extend_to_payoff(start: float, end: float, spans: Sequence[EventSpan],
                     words: Sequence[Dict[str, Any]], max_len: float = 60.0,
                     reach: float = 4.0) -> Tuple[float, Optional[Dict[str, Any]]]:
    """New end so a clip ending inside/just before an event sees it finish."""
    limit = start + max_len
    new_end = end
    used = None
    for s in sorted(spans, key=lambda s: s.start):
        if s.start <= new_end + reach and s.end > new_end:
            new_end = min(s.end + 1.0, limit)
            used = s
    if used is None:
        return end, None
    new_end = _sentence_end_after(words, new_end, limit) if any(
        float(w["start"]) < new_end < float(w["end"]) + 1.5 for w in words) else new_end
    return round(new_end, 2), {"extended_from": round(end, 2), "to": round(new_end, 2),
                               "because": used.as_dict()}


def event_window(spans: Sequence[EventSpan], words: Sequence[Dict[str, Any]],
                 total: float, max_len: float = 60.0, lead: float = 25.0) -> Optional[Dict[str, Any]]:
    """No-model fallback: the strongest run of events with its lead-in."""
    if not spans:
        return None
    ordered = sorted(spans, key=lambda s: s.start)
    runs: List[List[EventSpan]] = []
    for s in ordered:
        if runs and s.start - runs[-1][-1].end <= 12.0:
            runs[-1].append(s)
        else:
            runs.append([s])

    def strength(run: List[EventSpan]) -> float:
        # Duration x visual strength. A run the transcript cannot see
        # (little speech) gets a 1.5x bonus: that is exactly the moment a
        # transcript-only selector misses. Label variety is NOT rewarded:
        # it made scattered weak spans outrank one sustained moment.
        base = sum((s.end - s.start) * s.score for s in run)
        span = run[-1].end - run[0].start
        quiet = _speech_seconds(words, run[0].start, run[-1].end) < 0.25 * span
        return base * (1.5 if quiet else 1.0)

    best = max(runs, key=strength)
    start = max(0.0, best[0].start - lead)
    # Begin on a sentence start up to 10 s earlier (the setup line that
    # introduces the moment, e.g. "The nominees for ... are"), so the
    # stimulus is in the clip, not just the reaction.
    for w_prev, w in zip(words, words[1:]):
        if start - 10.0 <= float(w["start"]) <= start and str(w_prev.get("text", "")).rstrip().endswith((".", "?", "!")):
            start = float(w["start"])
            break
    # The end is computed after the start settles, so the cap holds.
    end = min(total, best[-1].end + 2.0, start + max_len)
    end = _sentence_end_after(words, end, start + max_len) if any(
        float(w["start"]) < end < float(w["end"]) + 1.5 for w in words) else end
    return {"start_time": round(start, 2), "end_time": round(end, 2),
            "events": [s.as_dict() for s in best]}


def words_from_transcript_data(data: Optional[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Cached transcript words as ``{text, start, end}`` in seconds."""
    words = []
    for w in (data or {}).get("words") or []:
        try:
            words.append({"text": str(w.get("text", "")), "start": float(w.get("start", 0)) / 1000.0,
                          "end": float(w.get("end", 0)) / 1000.0})
        except (TypeError, ValueError, AttributeError):
            continue
    return words


def _parse_mmss(value: Any) -> Optional[float]:
    try:
        parts = [float(p) for p in str(value).split(":")]
    except ValueError:
        return None
    seconds = 0.0
    for part in parts:
        seconds = seconds * 60 + part
    return seconds


def _fmt_mmss(seconds: float) -> str:
    total = int(round(seconds))
    if total >= 3600:
        return f"{total // 3600:02d}:{total % 3600 // 60:02d}:{total % 60:02d}"
    return f"{total // 60:02d}:{total % 60:02d}"


def extend_segments_to_payoff(segments: Sequence[Any], spans: Sequence[EventSpan],
                              words: Sequence[Dict[str, Any]], max_len: float = 60.0) -> int:
    """Apply ``extend_to_payoff`` to selected segments in place; returns how many moved."""
    moved = 0
    for segment in segments or []:
        getter = segment.get if isinstance(segment, dict) else (lambda k, s=segment: getattr(s, k, None))
        start, end = _parse_mmss(getter("start_time")), _parse_mmss(getter("end_time"))
        if start is None or end is None or end <= start:
            continue
        new_end, why = extend_to_payoff(start, end, spans, words, max_len=max(max_len, end - start))
        if why is None or new_end <= end:
            continue
        stamp = _fmt_mmss(new_end)
        reason = f" Extended to {stamp} so the visual moment ({why['because']['description']}) finishes."
        if isinstance(segment, dict):
            segment["end_time"] = stamp
            segment["reasoning"] = str(segment.get("reasoning") or "") + reason
        else:
            try:
                segment.end_time = stamp
                segment.reasoning = str(getattr(segment, "reasoning", "") or "") + reason
            except Exception:
                continue
        moved += 1
        logger.info("Payoff: segment %s-%s extended to %s (%s)", getter("start_time"), _fmt_mmss(end),
                    stamp, why["because"]["label"])
    return moved
