"""Run the local speech model in a separate CUDA runtime from the video renderer."""
import json
import logging
import math
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from typing import Any, Callable, Dict, Optional

logger = logging.getLogger(__name__)
RUNTIME = Path("/opt/snipnova-speech")


class FastTranscriptionUnavailable(RuntimeError):
    """The fast decoder could not finish; callers must not silently restart
    an hours-long source on the configured (Medium) model."""

    def __init__(self, message: str, *, too_long: bool = False):
        super().__init__(message)
        self.too_long = too_long


def transcribe_local_gpu(media, *, batched=False, timeout: float = 600.0,
                         status: Optional[Dict[str, Any]] = None):
    """One bounded GPU subprocess. ``status`` receives the failure reason."""
    if os.getenv("LIVE_OFFLINE_WHISPER", "off").lower() not in {"1", "true", "on"}:
        if status is not None:
            status["error"] = "LIVE_OFFLINE_WHISPER is off"
        return None
    if not (RUNTIME / "model/model.bin").is_file():
        if status is not None:
            status["error"] = "offline model is not installed"
        return None
    libs = [str(RUNTIME / "nvidia" / name / "lib") for name in ("cublas", "cudnn", "cuda_runtime")]
    env = dict(os.environ, LD_LIBRARY_PATH=":".join([*libs, os.getenv("LD_LIBRARY_PATH", "")]),
               HF_HUB_OFFLINE="1", OMP_NUM_THREADS="2")
    with tempfile.TemporaryDirectory(prefix="snipnova-speech-") as work:
        output = Path(work) / "result.json"
        try:
            subprocess.run([sys.executable, "-m", __name__, str(media), str(output), *(["--batched"] if batched else [])],
                           env=env, check=True, timeout=max(30.0, float(timeout)), capture_output=True)
            return json.loads(output.read_text(encoding="utf-8"))
        except subprocess.TimeoutExpired:
            if status is not None:
                status["error"] = f"GPU decoder timed out after {timeout:.0f}s"
            logger.warning("Offline GPU transcription timed out after %.0fs", timeout)
            return None
        except (OSError, ValueError, subprocess.SubprocessError) as exc:
            if status is not None:
                status["error"] = f"{type(exc).__name__}: {exc}"[:300]
            logger.warning("Offline GPU transcription unavailable (%s)", type(exc).__name__)
            return None


def _shift_result(result: Dict[str, Any], offset: float, length: float) -> Dict[str, Any]:
    """Move one part's timings onto the source clock, clipped to the part."""
    for segment in result.get("segments") or []:
        for item in [segment, *(segment.get("words") or [])]:
            for edge in ("start", "end"):
                value = float(item.get(edge, 0.0))
                item[edge] = float(offset + max(0.0, min(length, value)))
    return result


def transcribe_fast_parts(media, *, part_seconds: float = 300.0,
                          progress: Optional[Callable[[str], None]] = None,
                          should_stop: Optional[Callable[[], bool]] = None,
                          max_seconds: Optional[float] = None) -> Dict[str, Any]:
    """Transcribe a short source in bounded parts with the fast GPU decoder.

    Every part is decoded by a fresh, time-limited subprocess, so a wedged
    CUDA call cannot pin the worker forever. Timestamps stay on the
    original source clock. A failed part is retried once; a second failure
    raises ``FastTranscriptionUnavailable`` instead of falling through to
    an hours-long full-source run on the configured model.
    """
    from ..media.ffmpeg import ffprobe_duration

    duration = ffprobe_duration(media)
    if not math.isfinite(duration) or duration <= 0:
        raise FastTranscriptionUnavailable(
            "Fast transcription could not read the audio duration."
        )
    if max_seconds is None:
        max_seconds = float(os.getenv("CREATE_FAST_LOCAL_MAX_SECONDS", "1800"))
    if max_seconds > 0 and duration > max_seconds + 60:
        raise FastTranscriptionUnavailable(
            f"Fast local transcription is limited to {max_seconds / 60:.0f} minutes; "
            f"this source is {duration / 60:.0f} minutes. Long sources use fast discovery.",
            too_long=True,
        )
    part_seconds = max(30.0, float(part_seconds))
    count = max(1, math.ceil(duration / part_seconds))
    segments = []
    language = None
    with tempfile.TemporaryDirectory(prefix="snipnova-fast-asr-") as scratch:
        for index in range(count):
            if should_stop and should_stop():
                raise FastTranscriptionUnavailable("Fast transcription cancelled.")
            start = index * part_seconds
            length = min(part_seconds, duration - start)
            part = Path(media)
            if count > 1:
                part = Path(scratch) / f"part-{index}.wav"
                try:
                    subprocess.run(
                        ["ffmpeg", "-nostdin", "-v", "error", "-y", "-ss", str(start),
                         "-i", str(media), "-t", str(length), "-vn", "-ac", "1",
                         "-ar", "16000", str(part)], check=True, timeout=120,
                    )
                except (OSError, subprocess.SubprocessError) as exc:
                    raise FastTranscriptionUnavailable(
                        f"Fast transcription could not cut part {index + 1}/{count}: {exc}"
                    ) from exc
            if progress:
                progress(
                    f"Transcribing part {index + 1}/{count} with the fast GPU decoder"
                )
            result = None
            error = None
            for attempt in range(2):
                status: Dict[str, Any] = {}
                result = transcribe_local_gpu(
                    part,
                    batched=True,
                    timeout=max(300.0, length * 2.0),
                    status=status,
                )
                if result is not None:
                    break
                error = status.get("error") or "GPU decoder returned no result"
                if attempt == 0:
                    logger.warning(
                        "Fast GPU transcription failed on part %d/%d (%s); retrying",
                        index + 1, count, error,
                    )
                    if progress:
                        progress(
                            f"Fast GPU decoder failed on part {index + 1}/{count}; retrying"
                        )
            if result is None:
                raise FastTranscriptionUnavailable(
                    f"Fast GPU transcription failed on part {index + 1}/{count} "
                    f"at {start:.0f}s ({error})."
                )
            language = language or result.get("language")
            _shift_result(result, start, length)
            segments.extend(result.get("segments") or [])
    return {"segments": segments, "language": language,
            "text": " ".join(str(s.get("text", "")).strip() for s in segments)}


def main():
    from faster_whisper import WhisperModel
    from ..media.transcription import _faster_whisper_result_to_whisper_dict

    model = WhisperModel(str(RUNTIME / "model"), device="cuda", compute_type="int8_float16", local_files_only=True)
    options = dict(language=os.getenv("TRANSCRIPTION_LANGUAGE") or None,
                   word_timestamps=True, vad_filter=True, condition_on_previous_text=False)
    if "--batched" in sys.argv[3:]:
        from faster_whisper import BatchedInferencePipeline
        segments, info = BatchedInferencePipeline(model=model).transcribe(
            sys.argv[1], batch_size=4, beam_size=1, **options)
    else:
        segments, info = model.transcribe(sys.argv[1], beam_size=5, **options)
    result = _faster_whisper_result_to_whisper_dict(segments, info)
    Path(sys.argv[2]).write_text(json.dumps(result), encoding="utf-8")


if __name__ == "__main__":
    main()
