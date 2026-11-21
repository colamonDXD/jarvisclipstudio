"""One live clipping session: record -> flag -> hook-first window -> gate -> preview -> clip.

``LiveSession(settings, session_dir, on_event).start()`` runs three threads:

- the recorder (``LiveRecorder``), stream-copying into the rolling buffer;
- the analyst: every ``tick`` seconds the voice pass reads the newest audio
  (``VoiceTrack``), and each new reaction flag, once enough footage after it
  is on disk, gets a hook-first window (``moments.speech_window``), the
  on-camera gate (when a facecam layout is chosen), a quick preview and a
  place in the render queue;
- the renderer, which turns queued windows into finished clips one at a time
  (``finisher.render_live_clip``), so a slow render never delays detection.

Everything the UI needs is reported through ``on_event(dict)`` (called from
these threads; the worker writes it to the database). Event ``type``:
``status`` (recorder health), ``moment`` (a new clip row: ``rejected``,
``preview`` or ``queued``), ``clip`` (``ready`` or ``failed``).
"""
from __future__ import annotations

import logging
import os
import queue
import subprocess
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, Optional

from .moments import creator_on_camera, speech_window
from .preview import render_preview
from .recorder import LiveRecorder
from .voice_scan import VoiceTrack

logger = logging.getLogger(__name__)

# Clip length (Luffy: "minimum 22 to 30 sec, not 15"). The analyst waits
# until MAX_LEN of footage after a flag is on disk before deciding its window.
MIN_LEN = float(os.getenv("LIVE_CLIP_MIN_SEC", "30"))   # owner: 30 s strict minimum
MAX_LEN = max(MIN_LEN, float(os.getenv("LIVE_CLIP_MAX_SEC", "45")))
LEAD_MAX = 6.0


@dataclass
class LiveSettings:
    url: str
    niche: str = "gaming"
    layout_template: Optional[str] = "facecam_red"
    credit: Optional[str] = None
    hook_title: Optional[str] = None
    request: Optional[str] = None      # "Find moments where..." (finished streams)
    voice_fx: Optional[str] = None     # funny voice: "high" | "deep"
    boxes: Optional[Dict[str, Any]] = None  # hand-drawn {"cam", "game", "arrange"} (gaming_templates.validate_boxes)
    streamer_words_only: bool = True
    min_talk_share: float = 0.5
    max_clips: int = 30
    keep_hours: float = 2.0
    max_height: int = 1080
    tick: float = 10.0


class LiveSession:
    def __init__(self, settings: LiveSettings, session_dir: Path, on_event: Callable[[Dict[str, Any]], None],
                 *, realtime_file: bool = False):
        self.settings = settings
        self.dir = Path(session_dir)
        self.on_event = on_event
        self.recorder = LiveRecorder(settings.url, self.dir / "buffer", segment_sec=4.0,
                                     keep_seconds=settings.keep_hours * 3600, max_height=settings.max_height,
                                     realtime_file=realtime_file)
        self.voice = VoiceTrack(baseline_sec=240.0)
        self._stop = threading.Event()
        self._analysis_stop = threading.Event()
        self._renders: "queue.Queue[Optional[Dict[str, Any]]]" = queue.Queue()
        self._threads: list = []
        self._done_flags: set = set()
        self.moments = 0
        self._rendering = False
        self._analysing = False

    # -- lifecycle ---------------------------------------------------------
    def start(self) -> "LiveSession":
        self.dir.mkdir(parents=True, exist_ok=True)
        self.recorder.start()
        for target, name in ((self._analyse_forever, "live-analyst"), (self._render_forever, "live-renderer")):
            t = threading.Thread(target=target, name=name, daemon=True)
            t.start()
            self._threads.append(t)
        return self

    def stop_recording(self) -> None:
        """Stop recording and detecting; queued clips keep rendering
        (``ended`` turns true once they are done)."""
        self._analysis_stop.set()
        self.recorder.stop()

    def stop(self, timeout: float = 15.0) -> None:
        self._analysis_stop.set()
        self._stop.set()
        self.recorder.stop()
        self._renders.put(None)
        for t in self._threads:
            t.join(timeout)

    @property
    def alive(self) -> bool:
        return not self._stop.is_set()

    @property
    def ended(self) -> bool:
        """The stream is over (the recorder gave up or the source finished)
        and every queued clip has been rendered."""
        return (not self.recorder.running and self._renders.empty() and not self._rendering
                and not self._analysing)

    def _emit(self, event: Dict[str, Any]) -> None:
        try:
            self.on_event(event)
        except Exception:
            logger.exception("Live session event handler failed")

    # -- analysis ----------------------------------------------------------
    def _analyse_forever(self) -> None:
        while not self._analysis_stop.wait(self.settings.tick):
            try:
                self.voice.update(self.recorder.buffer, self.dir)
            except Exception as exc:  # keep recording whatever happens to analysis
                logger.warning("Live voice pass failed: %s", exc)
            span = self.recorder.buffer.span()
            self._emit({"type": "status", "recording": self.recorder.running, "restarts": self.recorder.restarts,
                        "last_error": (self.recorder.last_error or "")[:300] or None,
                        "buffered_seconds": round(span[1] - span[0], 1) if span else 0.0,
                        "moments": self.moments})
            if not span:
                continue
            for flag in self.voice.onsets():
                key = round(flag["onset"])
                if key in self._done_flags or span[1] < flag["onset"] + MAX_LEN + 4:
                    continue
                self._done_flags.add(key)
                if self.moments >= self.settings.max_clips:
                    continue
                try:
                    self._analysing = True
                    self._handle_flag(flag)
                except Exception as exc:
                    logger.exception("Live moment failed")
                    self._emit({"type": "moment", "status": "rejected", "onset_wall": flag["onset"],
                                "reason": f"internal error: {exc}"[:300]})
                finally:
                    self._analysing = False

    def _handle_flag(self, flag: Dict[str, Any]) -> None:
        onset = float(flag["onset"])
        base, voiced = self.voice.voiced_between(onset - LEAD_MAX - 2, onset + MAX_LEN + 2)
        start_l, stop_l, share = speech_window(voiced, onset - base, float(flag["end"]) - base,
                                               lead_max=LEAD_MAX, min_len=MIN_LEN, max_len=MAX_LEN)
        start, stop = base + start_l, base + stop_l
        moment = {"type": "moment", "onset_wall": onset, "start_wall": start, "end_wall": stop,
                  "signals": list(flag.get("signals") or []), "strength": round(float(flag.get("peak", 0)), 2),
                  "talk_share": round(share, 2)}
        if share < self.settings.min_talk_share:
            self._emit({**moment, "status": "rejected", "reason": "streamer barely talking"})
            return
        self.moments += 1
        n = self.moments
        raw = self.recorder.buffer.cut_window(start, stop, self.dir / f"raw_{n:03d}.mp4")
        if not raw:
            self._emit({**moment, "n": n, "status": "rejected", "reason": "footage not in the buffer"})
            return
        window = self.dir / f"window_{n:03d}.mp4"
        # Re-encoded so the clip starts on the exact second (the stream-copy
        # cut starts on the keyframe before it); CRF 14 is visually lossless.
        cut = subprocess.run(["ffmpeg", "-v", "error", "-y", "-ss", f"{raw['offset']:.3f}", "-i", raw["path"],
                              "-t", f"{raw['duration']:.3f}", "-c:v", "libx264", "-preset", "veryfast", "-crf", "14",
                              "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart", str(window)],
                             capture_output=True, text=True)
        Path(raw["path"]).unlink(missing_ok=True)
        if cut.returncode != 0 or not window.is_file():
            self._emit({**moment, "n": n, "status": "rejected", "reason": "window cut failed"})
            return
        if self.settings.layout_template:
            ok, why = creator_on_camera(window, onset - start, cam_box=(self.settings.boxes or {}).get("cam"))
            moment["camera"] = why
            if not ok:
                self._emit({**moment, "n": n, "status": "rejected", "reason": why})
                return
        preview = render_preview({"path": str(window), "offset": 0.0, "duration": stop - start},
                                 self.dir / f"preview_{n:03d}.mp4")
        self._emit({**moment, "n": n, "status": "queued", "preview_path": str(preview) if preview else None,
                    "ready_after_s": round(time.time() - onset, 1)})
        self._renders.put({"n": n, "window": window, "peak_offset": onset - start})

    # -- rendering ---------------------------------------------------------
    def _render_forever(self) -> None:
        from .finisher import render_live_clip

        while True:
            job = self._renders.get()
            if job is None or self._stop.is_set():
                return
            n = job["n"]
            self._rendering = True
            out = self.dir / f"clip_{n:03d}.mp4"
            self._emit({"type": "clip", "n": n, "status": "rendering"})
            t0 = time.time()
            try:
                result = render_live_clip(job["window"], out, niche=self.settings.niche,
                                          layout_template=self.settings.layout_template, credit=self.settings.credit,
                                          hook_title=self.settings.hook_title,
                                          streamer_words_only=self.settings.streamer_words_only,
                                          voice_fx=self.settings.voice_fx, boxes=self.settings.boxes,
                                          peak_offset=job.get("peak_offset"))
            except Exception as exc:
                logger.exception("Live clip render failed")
                result = {"ok": False, "error": str(exc)[:300]}
            self._emit({"type": "clip", "n": n, "status": "ready" if result.get("ok") else "failed",
                        "clip_path": str(out) if result.get("ok") else None, "duration": result.get("duration"),
                        "error": result.get("error"), "render_s": round(time.time() - t0, 1),
                        **({"comment_question": result["comment_question"]}
                           if result.get("comment_question") else {})})
            self._rendering = False
