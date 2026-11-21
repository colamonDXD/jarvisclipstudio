"""Beat-density audit + one-punch repair for finished clips.

Pro Shorts change something visual every 1.5–3 s; any 4–5 s stretch with
no cut, zoom, text change or motion is a retention leak (viral-pattern
study, ``docs`` GAP queue #5). Until now nothing measured a finished
clip's longest no-change stretch. This module is that measurement, plus
the bounded repair:

* **Audit** — :func:`beat_density_for` samples the finished file at
  2 fps (64x36 gray thumbnails, the ``clip_qc`` measurement size) and
  computes the frame-to-frame mean absolute difference (MAD) series.
  A step at or below :data:`BEAT_STATIC_MAD_MAX` counts as "no visual
  change". The audit reports every static stretch, the longest one, and
  a beat score: the share of the clip that did NOT sit static beyond
  :data:`BEAT_MAX_STATIC_S`. The measure is deliberately the finished
  frame as the viewer sees it — a caption change or a punch-in inside
  the window counts as a beat, exactly like the pattern study counts
  text/SFX changes as beats.

* **Repair** — when the audit finds a stretch longer than
  :data:`BEAT_MAX_STATIC_S` in a non-Plain clip, ONE gentle punch-in
  (the hook finish's own :func:`~media.hook_finish.punch_filter`, same
  1.08 zoom) is applied centred in the dead stretch, splitting it. One
  punch only: this is a floor repair, not an edit pass. Plain — just
  cuts clips are exempt (owner rule: Plain is cuts + captions only).

The whole pass is off unless ``BEAT_DENSITY=on``. With the switch off
:func:`audit_and_repair_beat_density` returns None and the render is
byte-identical to before. Every public function follows the
caption-coverage gate's contract (``media/word_align.py``): it returns
a plain dict or None and never raises — a failed audit must never cost
the user a clip.
"""
from __future__ import annotations

import logging
import os
import shutil
import subprocess
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

import numpy as np

logger = logging.getLogger(__name__)

# --- Thresholds ------------------------------------------------------------

# Sampling: 2 fps at the QC measurement size. Half-second steps are fine
# enough to locate a multi-second dead stretch within ±0.5 s, and one
# ffmpeg pass over a 60 s clip decodes 120 tiny frames — cheap.
BEAT_SAMPLE_FPS = 2.0
BEAT_FRAME_W = 64
BEAT_FRAME_H = 36
# Long clips: judge the first five minutes (clip_qc's frozen check uses
# the same cap); a Short is far shorter than this in practice.
BEAT_MAX_SAMPLE_SECONDS = 300.0

# A frame-to-frame MAD (0–255 scale) at or below this is "no visual
# change". A frozen frame re-encoded measures ~0.05–0.5 of encoder
# noise; 2.0 forgives sensor noise and micro-jitter, while any cut,
# zoom step, caption change or real subject motion at 64x36 clears it
# by a wide margin (real motion measures 5–100+).
BEAT_STATIC_MAD_MAX = 2.0

# The retention rule from the pattern study: no stretch without a
# visual change may run longer than this.
BEAT_MAX_STATIC_S = 3.5

# The repair punch: the hook finish's trained gentle zoom (1.08) held
# for two seconds — long enough to read as a deliberate reframing,
# short enough to stay a nudge. One punch per clip, ever.
BEAT_PUNCH_ZOOM = 1.08
BEAT_PUNCH_SECONDS = 2.0

# Mirrors hook_finish's floor: shorter than this there is no room for
# a hook, a repair and an ending to coexist.
BEAT_MIN_CLIP_SECONDS = 6.0


def beat_density_enabled() -> bool:
    """``BEAT_DENSITY`` (default off): audit finished clips and repair
    over-long static stretches with one punch-in."""
    return os.getenv("BEAT_DENSITY", "off").strip().lower() in ("on", "1", "true", "yes")


# --- Sampling ---------------------------------------------------------------

def sample_frame_diffs(path: Path, duration: float) -> Optional[List[float]]:
    """Frame-to-frame MAD series of ``path`` at :data:`BEAT_SAMPLE_FPS`.

    One ffmpeg pass, gray thumbnails. ``diffs[i]`` is the change between
    the frames at ``i / fps`` and ``(i + 1) / fps`` seconds. None when
    the file cannot be sampled (fewer than two frames decoded).
    """
    sample_seconds = min(duration, BEAT_MAX_SAMPLE_SECONDS)
    if sample_seconds <= 0:
        return None
    try:
        raw = subprocess.run(
            [
                "ffmpeg", "-v", "error", "-i", str(path),
                "-t", f"{sample_seconds:.3f}", "-an",
                "-vf", f"fps={BEAT_SAMPLE_FPS:g},scale={BEAT_FRAME_W}:{BEAT_FRAME_H},format=gray",
                "-f", "rawvideo", "-",
            ],
            capture_output=True, timeout=180,
        ).stdout
    except Exception:
        logger.warning("Beat-density sampling failed for %s", path, exc_info=True)
        return None
    frame_size = BEAT_FRAME_W * BEAT_FRAME_H
    count = len(raw) // frame_size
    if count < 2:
        return None
    frames = np.frombuffer(raw[: count * frame_size], dtype=np.uint8).reshape(
        count, BEAT_FRAME_H, BEAT_FRAME_W
    ).astype(np.float32)
    changes = np.abs(np.diff(frames, axis=0)).mean(axis=(1, 2))
    return [float(value) for value in changes]


# --- Analysis (pure) --------------------------------------------------------

def analyse_beat_density(
    diffs: Sequence[float],
    duration: float,
    *,
    fps: float = BEAT_SAMPLE_FPS,
    static_mad_max: float = BEAT_STATIC_MAD_MAX,
    max_static_s: float = BEAT_MAX_STATIC_S,
) -> Dict[str, Any]:
    """Turn a MAD series into the audit dict. Pure; no I/O.

    A run of ``n`` consecutive static steps (frames ``a`` … ``a + n``
    mutually unchanged) is a static stretch of ``(n + 1) / fps`` seconds
    starting at ``a / fps`` — the picture holds from the first frame of
    the run until the frame after the last static step.
    """
    stretches: List[Dict[str, float]] = []
    run_start: Optional[int] = None

    def close_run(first_step: int, last_step: int) -> None:
        start = first_step / fps
        end = min(duration, start + (last_step - first_step + 2) / fps)
        stretches.append(
            {"start": round(start, 2), "end": round(end, 2), "seconds": round(end - start, 2)}
        )

    for index, value in enumerate(diffs):
        if value <= static_mad_max:
            if run_start is None:
                run_start = index
        elif run_start is not None:
            close_run(run_start, index - 1)
            run_start = None
    if run_start is not None:
        close_run(run_start, len(diffs) - 1)

    longest = max(stretches, key=lambda s: s["seconds"], default=None)
    longest_s = float(longest["seconds"]) if longest else 0.0
    overlong_s = round(
        sum(max(0.0, s["seconds"] - max_static_s) for s in stretches), 2
    )
    beat_score = (
        round(max(0.0, 1.0 - overlong_s / duration), 3) if duration > 0 else 0.0
    )
    return {
        "ok": longest_s <= max_static_s,
        "duration_s": round(duration, 2),
        "sample_fps": fps,
        "static_mad_max": static_mad_max,
        "max_static_s": max_static_s,
        "changes": sum(1 for value in diffs if value > static_mad_max),
        "longest_static_s": longest_s,
        "longest_static": longest,
        "static_stretches": [s for s in stretches if s["seconds"] >= 1.0],
        "overlong_static_s": overlong_s,
        "beat_score": beat_score,
    }


def beat_density_for(path: Path) -> Optional[Dict[str, Any]]:
    """The beat-density audit for one finished clip. Never raises.

    None = not run (switch off, unprobeable, or unsamplable), following
    ``caption_coverage_for``'s contract.
    """
    if not beat_density_enabled():
        return None
    try:
        from .ffmpeg import ffprobe_duration

        duration = ffprobe_duration(Path(path))
        if duration <= 0:
            return None
        diffs = sample_frame_diffs(Path(path), duration)
        if diffs is None or len(diffs) < 2:
            return None
        return analyse_beat_density(diffs, duration)
    except Exception:
        logger.warning("Beat-density audit failed to run for %s", path, exc_info=True)
        return None


# --- Repair -----------------------------------------------------------------

def plan_beat_repair(
    audit: Optional[Dict[str, Any]], *, plain: bool
) -> Optional[Dict[str, Any]]:
    """The one repair punch for an audit, or None when none is owed.

    No repair when the audit passed, when the clip is Plain (exempt by
    owner rule), when the clip is too short to hold a punch, or when
    the audit carries no longest stretch to centre on.
    """
    if not audit or audit.get("ok"):
        return None
    if plain:
        return None
    if float(audit.get("duration_s") or 0.0) < BEAT_MIN_CLIP_SECONDS:
        return None
    stretch = audit.get("longest_static")
    if not stretch:
        return None
    seconds = min(BEAT_PUNCH_SECONDS, float(stretch["seconds"]))
    centre = (float(stretch["start"]) + float(stretch["end"])) / 2.0
    at = min(max(0.0, centre - seconds / 2.0), max(0.0, float(audit["duration_s"]) - seconds))
    return {
        "at": round(at, 2),
        "seconds": round(seconds, 2),
        "zoom": BEAT_PUNCH_ZOOM,
        "stretch": stretch,
    }


def apply_beat_punch(path: Path, plan: Dict[str, Any]) -> bool:
    """Apply the planned punch-in to ``path`` in place. Never raises.

    Reuses the hook finish's punch filter and its encode pattern
    (GPU encoder with one CPU retry); the audio is copied untouched.
    False — with the original file untouched — when anything fails.
    """
    try:
        from .edl_render import CPU_FALLBACK_CRF, CPU_FALLBACK_PRESET, get_video_encoder
        from .ffmpeg import build_final_video_encode_args, ffprobe_video_size
        from .hook_finish import punch_filter

        path = Path(path)
        width, height = ffprobe_video_size(path)
        if not width or not height:
            return False
        video_chain = punch_filter(
            width, height, float(plan["at"]), float(plan["seconds"]), float(plan["zoom"])
        )
        out = path.with_name(path.stem + ".beats" + path.suffix)

        def command(encoder_args: List[str]) -> List[str]:
            args = list(encoder_args)
            if not any(a == "-c:v" for a in args):
                args = ["-c:v", "libx264", *args]
            return [
                "ffmpeg", "-v", "error", "-y", "-i", str(path),
                "-filter_complex", f"[0:v]{video_chain}[v]",
                "-map", "[v]", "-map", "0:a?", "-c:a", "copy",
                *args, "-pix_fmt", "yuv420p", "-movflags", "+faststart", str(out),
            ]

        encoder, encoder_args = get_video_encoder(True)
        result = subprocess.run(command(encoder_args), capture_output=True, text=True, timeout=600)
        if (result.returncode != 0 or not out.is_file() or out.stat().st_size == 0) and encoder != "libx264":
            cpu_args = build_final_video_encode_args(crf=CPU_FALLBACK_CRF, preset=CPU_FALLBACK_PRESET)
            result = subprocess.run(command(cpu_args), capture_output=True, text=True, timeout=600)
        if result.returncode != 0 or not out.is_file() or out.stat().st_size == 0:
            logger.warning(
                "Beat-density repair failed; keeping the clip as rendered: %s",
                (result.stderr or "")[-300:],
            )
            out.unlink(missing_ok=True)
            return False
        shutil.move(str(out), str(path))
        return True
    except Exception:
        logger.warning("Beat-density repair failed for %s", path, exc_info=True)
        return False


def audit_and_repair_beat_density(
    clip_path: Path, edl: Optional[Dict[str, Any]] = None
) -> Optional[Dict[str, Any]]:
    """Audit a finished clip and apply the one-punch repair when owed.

    The render seam's entry point. Returns the record stored with the
    render — ``{"audit": …, "repair": … | None, "after": … | None}``
    where ``after`` is the re-audit of the repaired file — or None when
    the pass did not run (switch off or audit unmeasurable). Never
    raises: a clip is never lost to its own audit.
    """
    if not beat_density_enabled():
        return None
    try:
        audit = beat_density_for(clip_path)
        if audit is None:
            return None
        record: Dict[str, Any] = {"audit": audit, "repair": None, "after": None}
        plain = (edl or {}).get("template") == "plain"
        plan = plan_beat_repair(audit, plain=plain)
        if plan is not None:
            applied = apply_beat_punch(Path(clip_path), plan)
            record["repair"] = {**plan, "applied": applied}
            if applied:
                record["after"] = beat_density_for(clip_path)
                logger.info(
                    "Beat density: punch-in at %.1fs split a %.1fs static stretch in %s "
                    "(longest now %.1fs)",
                    plan["at"], plan["stretch"]["seconds"], clip_path,
                    (record["after"] or {}).get("longest_static_s", -1),
                )
        elif not audit["ok"]:
            record["repair"] = {
                "applied": False,
                "reason": (
                    "Plain — just cuts clips are exempt from the repair"
                    if plain
                    else "clip is too short to hold a repair punch"
                ),
            }
        return record
    except Exception:
        logger.warning("Beat-density pass failed for %s", clip_path, exc_info=True)
        return None
