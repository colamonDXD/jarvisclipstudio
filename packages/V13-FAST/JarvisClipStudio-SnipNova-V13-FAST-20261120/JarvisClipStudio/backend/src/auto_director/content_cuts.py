"""Editorial restraint: cut, zoom and transition only where the footage says so.

Root cause behind clip1.mp4 (handoff/quality-clip1/ROOT_CAUSE.md): build_edl
sliced every clip into EQUAL intervals sized by the profile's cuts-per-minute
target (58 s of Gaming became 21 slices, one every 2.8 s), snapped them to
the beats of the music bed the app itself added, punched in every 12 s
("rehook"), pushed in over the opening and flashed on the bed's hits, none
of it tied to anything on screen or in the speech.

With ``EDL_CONTENT_CUTS=on`` (default off until the owner approves the
corrected render) build_edl asks this module instead:

- **Segments** start only at a real reason: a source shot change
  (``clip["scene_cuts"]``, clip-relative seconds) or the end of a spoken
  sentence. The profile's cuts-per-minute becomes a CEILING; shots shorter
  than the profile's ``min_shot_sec`` (at least 2.5 s) are never made. A
  clip with no reason to cut is one segment. Every join is a plain cut.
- **Zooms**: no interval punches and no automatic opening push-in. An
  energy-peak punch survives only when the peak falls on speech, at most one
  per 8 s and never in the first or last second. Speaker-change punches
  (diarisation evidence) and a profile's declared Ken Burns drift stay.
- **Signature transition**: kept only on a join that is a real shot change.
- **Music-bed beat grid / flashes**: not applied. Snapping cuts to (or
  flashing on) a bed the app added is decoration, not editing. Grids built
  from the SOURCE audio (music-artist profiles) still apply.

Every decision is recorded on the EDL under ``edit_restraint`` so a render
can be audited cut by cut. Unset / ``off`` keeps the quota edit.
"""
from __future__ import annotations

import os
from typing import Any, Iterable, Optional

MIN_SHOT_FLOOR_SEC = 2.5
PUNCH_MIN_GAP_SEC = 8.0
PUNCH_EDGE_GUARD_SEC = 1.0
SCENE_SNAP_SEC = 0.25
_SENTENCE_END = (".", "?", "!")


def content_cuts_enabled() -> bool:
    # Default OFF until Luffy approves the corrected render (Sanji, Q15:
    # quality fixes ship switched off, enabled after the owner watches
    # the proof). The test page and harness turn it on explicitly.
    return os.getenv("EDL_CONTENT_CUTS", "off").strip().lower() in ("on", "1", "true", "yes")


def _word_text(word: dict) -> str:
    return str(word.get("text") or word.get("word") or "").strip()


def _word_times(word: dict) -> tuple[float, float]:
    return float(word.get("start", 0) or 0), float(word.get("end", 0) or 0)


def sentence_boundaries(words: list[dict], clip_duration: float) -> list[float]:
    """Mid-gap times after words that end a sentence (clip-relative)."""
    times: list[float] = []
    for index, word in enumerate(words):
        if not _word_text(word).endswith(_SENTENCE_END):
            continue
        _, end = _word_times(word)
        if index + 1 < len(words):
            next_start, _ = _word_times(words[index + 1])
            at = (end + next_start) / 2 if next_start > end else end
        else:
            at = end
        if 0 < at < clip_duration:
            times.append(round(at, 3))
    return times


def plan_content_segments(
    profile: dict,
    clip_duration: float,
    transcript_words: list[dict],
    scene_cuts: Optional[Iterable[float]] = None,
) -> tuple[list[dict], list[dict]]:
    """Segments whose every boundary has a reason; plus the decision notes."""
    pacing = profile.get("pacing", {}) or {}
    min_shot = max(MIN_SHOT_FLOOR_SEC, float(pacing.get("min_shot_sec", 0) or 0))
    target = pacing.get("target_cuts_per_min", [5, 8]) or [5, 8]
    ceiling_per_min = float(max(target)) if isinstance(target, (list, tuple)) else float(target)
    max_cuts = max(0, int(clip_duration / 60.0 * ceiling_per_min))

    cuts = sorted(
        float(c) for c in (scene_cuts or [])
        if min_shot <= float(c) <= clip_duration - min_shot
    )
    candidates: list[tuple[float, str]] = [(c, "scene_cut") for c in cuts]
    for at in sorted(set(sentence_boundaries(transcript_words, clip_duration))):
        if min_shot <= at <= clip_duration - min_shot and all(
            abs(at - c) > SCENE_SNAP_SEC for c in cuts
        ):
            candidates.append((at, "sentence_end"))
    # Shot changes win over sentence ends; then earliest first.
    candidates.sort(key=lambda item: (item[1] != "scene_cut", item[0]))

    chosen: list[tuple[float, str]] = []
    dropped: list[dict] = []
    for at, reason in candidates:
        if len(chosen) >= max_cuts:
            dropped.append({"at_sec": round(at, 3), "reason": reason, "why": "cut ceiling"})
            continue
        edges = [0.0, clip_duration] + [c for c, _ in chosen]
        if any(abs(at - e) < min_shot for e in edges):
            dropped.append({"at_sec": round(at, 3), "reason": reason, "why": "shorter than min shot"})
            continue
        chosen.append((at, reason))
    chosen.sort()

    bounds = [(0.0, "clip_start")] + chosen + [(clip_duration, "clip_end")]
    segments: list[dict] = []
    for index in range(len(bounds) - 1):
        start, reason = bounds[index]
        end = bounds[index + 1][0]
        segments.append({
            "index": index,
            "start": start,
            "end": end,
            "duration": end - start,
            "transition_in": "none" if index == 0 else "cut",
            "transition_duration": 0,
            "boundary_reason": reason,
        })
    notes = [{"at_sec": round(at, 3), "reason": reason} for at, reason in chosen]
    return segments, notes + [{"dropped": dropped}] if dropped else notes


def _on_speech(t: float, words: list[dict], pad: float = 0.15) -> bool:
    for word in words:
        start, end = _word_times(word)
        if start - pad <= t <= end + pad:
            return True
    return False


def restrain_zooms(
    keyframes: list[dict], transcript_words: list[dict], clip_duration: float
) -> tuple[list[dict], list[dict]]:
    """Keep only zooms with evidence; return (kept, dropped-with-reason)."""
    kept: list[dict] = []
    dropped: list[dict] = []
    last_punch = float("-inf")
    for kf in sorted(keyframes, key=lambda k: float(k.get("time", k.get("start", 0)) or 0)):
        kind = kf.get("type")
        if kind == "ken_burns":
            kept.append(kf)
            continue
        if kind != "punch_in":
            kept.append(kf)
            continue
        t = float(kf.get("time", 0) or 0)
        trigger = str(kf.get("trigger") or "")
        if trigger == "rehook":
            dropped.append({"time": round(t, 3), "why": "interval punch (no emphasis)"})
            continue
        if t < PUNCH_EDGE_GUARD_SEC or t > clip_duration - PUNCH_EDGE_GUARD_SEC:
            dropped.append({"time": round(t, 3), "why": "inside first/last second"})
            continue
        if trigger != "speaker_change" and not _on_speech(t, transcript_words):
            dropped.append({"time": round(t, 3), "why": "loudness peak without speech"})
            continue
        if t - last_punch < PUNCH_MIN_GAP_SEC:
            dropped.append({"time": round(t, 3), "why": f"within {PUNCH_MIN_GAP_SEC:.0f}s of another punch"})
            continue
        kept.append(kf)
        last_punch = t
    return kept, dropped


def signature_on_shot_change(segments: list[dict], index: int) -> bool:
    """A signature transition may land only on a real source shot change."""
    if 0 <= index < len(segments):
        return segments[index].get("boundary_reason") == "scene_cut"
    return False


def decorative_grid(beat_cfg: Optional[dict]) -> bool:
    """True when the profile's beat grid follows the added music bed."""
    return bool(beat_cfg) and str(beat_cfg.get("source", "music")) == "music"


def detect_clip_scene_cuts(
    video_path: Any, start: float, duration: float, threshold: float = 0.25
) -> list[float]:
    """Source shot changes inside [start, start+duration], clip-relative.

    Same scene score as ``visual_moments.detect_scene_cut_times`` (ffmpeg
    ``select=gt(scene,t)``), decoded at 320 px wide over the clip window
    only. Threshold 0.25, not that module's 0.4: in the clip1 source (dark
    venue footage) the real camera cuts score 0.28-0.36 and 0.4 found none. Any failure returns [] (the planner then cuts on sentences alone).
    """
    import re
    import subprocess

    try:
        result = subprocess.run(
            [
                "ffmpeg", "-hide_banner", "-ss", f"{max(0.0, float(start)):.3f}",
                "-t", f"{max(0.0, float(duration)):.3f}", "-i", str(video_path),
                "-an", "-filter:v", f"scale=320:-2,select='gt(scene,{threshold})',showinfo",
                "-f", "null", "-",
            ],
            capture_output=True, text=True, timeout=300,
        )
    except Exception:
        return []
    if result.returncode != 0:
        return []
    return sorted(
        round(float(t), 3) for t in re.findall(r"pts_time:([0-9.]+)", result.stderr)
        if 0 < float(t) < float(duration)
    )
