"""Live recorder: one persistent pull into stream-copied segments, with a watchdog.

``LiveRecorder(source, session_dir).start()`` runs in a background thread:

- a stream page URL (twitch.tv, youtube.com) is resolved with ``yt-dlp -g``
  to the HLS the public player uses (separate video + audio playlists on
  YouTube, one on Twitch; the user must own or be authorised to clip the
  stream) and ffmpeg reads those playlists directly; any other input (an
  HLS/RTMP URL, or a local file played in real time for tests) goes
  straight to ffmpeg;
- ffmpeg stream-copies into ``run-XXXX/seg_%06d.ts`` segments of
  ``segment_sec`` with a CSV list (no re-encoding: ~1-3% CPU);
- each (re)connection is a new run with its own wall-clock origin
  (``run.json``), so a reconnect never corrupts earlier timing; the
  playlist URL is resolved again on every reconnect (live URLs expire);
- the watchdog restarts a dropped pull with backoff until ``stop()`` or
  ``max_restarts``, and prunes the buffer to ``keep_seconds``.

The wall-clock origin is the moment the first segment's first packet is
written (taken from the first segment file's creation as soon as it
appears, minus the segment's own start offset). It is accurate to the
stream's ingest delay, which the moment fuser calibrates per stream.
"""
from __future__ import annotations

import logging
import shutil
import subprocess
import threading
import time
from pathlib import Path
from typing import List, Optional

from .buffer import SEGMENT_LIST, SegmentBuffer, write_run_meta

logger = logging.getLogger(__name__)

PAGE_HOSTS = ("twitch.tv", "youtube.com", "youtu.be")


def _is_page_url(source: str) -> bool:
    return source.startswith(("http://", "https://")) and any(h in source for h in PAGE_HOSTS)


class LiveRecorder:
    def __init__(self, source: str, session_dir: Path, *, segment_sec: float = 4.0,
                 keep_seconds: float = 1800.0, max_height: int = 1080,
                 realtime_file: bool = False, max_restarts: int = 50):
        self.source = source
        self.session_dir = Path(session_dir)
        self.segment_sec = segment_sec
        self.keep_seconds = keep_seconds
        self.max_height = max_height
        self.realtime_file = realtime_file  # tests: play a local file at 1x speed
        self.max_restarts = max_restarts
        self.buffer = SegmentBuffer(self.session_dir)
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._procs: List[subprocess.Popen] = []
        self.restarts = 0
        self.last_error: Optional[str] = None

    # ------------------------------------------------------------------ control
    def start(self) -> "LiveRecorder":
        self.session_dir.mkdir(parents=True, exist_ok=True)
        self._thread = threading.Thread(target=self._run_forever, name="live-recorder", daemon=True)
        self._thread.start()
        return self

    def stop(self, timeout: float = 10.0) -> None:
        self._stop.set()
        self._kill()
        if self._thread:
            self._thread.join(timeout)

    @property
    def running(self) -> bool:
        return bool(self._thread and self._thread.is_alive())

    # ------------------------------------------------------------------ internals
    def _kill(self) -> None:
        for proc in self._procs:
            try:
                proc.terminate()
                proc.wait(timeout=5)
            except Exception:
                try:
                    proc.kill()
                except Exception:
                    pass
        self._procs = []

    def _next_run_dir(self) -> Path:
        index = len([p for p in self.session_dir.glob("run-*") if p.is_dir()])
        return self.session_dir / f"run-{index:04d}"

    def _ffmpeg_cmd(self, run_dir: Path, input_args: List[str], maps: List[str]) -> List[str]:
        return ["ffmpeg", "-hide_banner", "-loglevel", "error", *input_args,
                *maps, "-c", "copy",
                "-f", "segment", "-segment_time", f"{self.segment_sec}",
                "-segment_list", str(run_dir / SEGMENT_LIST), "-segment_list_type", "csv",
                "-segment_list_flags", "+live", "-reset_timestamps", "1",
                str(run_dir / "seg_%06d.ts")]

    def _resolve_page(self) -> List[str]:
        """Fresh stream URLs for a page (they expire): [video(+audio)] HLS."""
        fmt = (f"bv*[height<={self.max_height}]+ba/b[height<={self.max_height}]/b")
        from . import yt_dlp_binary

        cmd = [yt_dlp_binary(), "-q", "--no-warnings", "-g", "-f", fmt, self.source]
        if shutil.which("node"):
            cmd[1:1] = ["--js-runtimes", "node"]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=90)
        urls = [line.strip() for line in result.stdout.splitlines() if line.strip().startswith("http")]
        if not urls:
            raise RuntimeError((result.stderr or "yt-dlp returned no stream URL").strip()[-300:])
        return urls[:2]

    def _launch(self, run_dir: Path) -> subprocess.Popen:
        if _is_page_url(self.source):
            urls = self._resolve_page()
            live_in = ["-reconnect", "1", "-reconnect_streamed", "1", "-reconnect_delay_max", "10"]
            input_args: List[str] = []
            for url in urls:
                input_args += [*live_in, "-i", url]
            maps = ["-map", "0:v:0?", "-map", "0:a:0?"] if len(urls) == 1 else ["-map", "0:v:0", "-map", "1:a:0"]
        else:
            input_args = (["-re"] if self.realtime_file else []) + ["-i", self.source]
            maps = ["-map", "0:v:0?", "-map", "0:a:0?"]
        ff = subprocess.Popen(self._ffmpeg_cmd(run_dir, input_args, maps), stderr=subprocess.PIPE)
        self._procs.append(ff)
        return ff

    def _stamp_origin(self, run_dir: Path, started: float, ff: subprocess.Popen) -> None:
        """Write run.json once the first segment exists (its wall-clock origin)."""
        first = run_dir / "seg_000000.ts"
        while not self._stop.is_set() and ff.poll() is None and not first.exists():
            time.sleep(0.05)
        origin = started
        if first.exists():
            try:
                origin = first.stat().st_ctime
            except OSError:
                pass
        write_run_meta(run_dir, origin, self.source)

    def _run_forever(self) -> None:
        backoff = 2.0
        while not self._stop.is_set():
            run_dir = self._next_run_dir()
            run_dir.mkdir(parents=True, exist_ok=True)
            started = time.time()
            try:
                ff = self._launch(run_dir)
            except FileNotFoundError as exc:
                self.last_error = f"missing tool: {exc}"
                logger.error("Live recorder cannot start: %s", exc)
                return
            except Exception as exc:  # stream offline, URL resolution failed
                self.last_error = str(exc)
                self.restarts += 1
                if self.restarts > self.max_restarts:
                    break
                logger.warning("Live recorder: cannot resolve the stream (%s); retrying in %.0fs", exc, backoff)
                self._stop.wait(backoff)
                backoff = min(backoff * 2, 60.0)
                continue
            self._stamp_origin(run_dir, started, ff)
            while not self._stop.is_set() and ff.poll() is None:
                self.buffer.prune(self.keep_seconds)
                time.sleep(1.0)
            if self._stop.is_set():
                break
            err = (ff.stderr.read().decode(errors="replace") if ff.stderr else "")[-400:]
            self.last_error = err or f"ffmpeg exited {ff.returncode}"
            self._kill()
            if self.realtime_file and ff.returncode == 0:
                logger.info("Live recorder: test source finished")
                break
            self.restarts += 1
            if self.restarts > self.max_restarts:
                logger.error("Live recorder: giving up after %d restarts (%s)", self.restarts, self.last_error)
                break
            logger.warning("Live recorder: pull dropped (%s); reconnecting in %.0fs", self.last_error, backoff)
            self._stop.wait(backoff)
            backoff = min(backoff * 2, 60.0)
