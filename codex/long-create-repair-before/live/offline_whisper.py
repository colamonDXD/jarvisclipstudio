"""Run the local speech model in a separate CUDA runtime from the video renderer."""
import json
import logging
import os
from pathlib import Path
import subprocess
import sys
import tempfile

logger = logging.getLogger(__name__)
RUNTIME = Path("/opt/snipnova-speech")


def transcribe_local_gpu(media, *, batched=False):
    if os.getenv("LIVE_OFFLINE_WHISPER", "off").lower() not in {"1", "true", "on"}:
        return None
    if not (RUNTIME / "model/model.bin").is_file():
        return None
    libs = [str(RUNTIME / "nvidia" / name / "lib") for name in ("cublas", "cudnn", "cuda_runtime")]
    env = dict(os.environ, LD_LIBRARY_PATH=":".join([*libs, os.getenv("LD_LIBRARY_PATH", "")]),
               HF_HUB_OFFLINE="1", OMP_NUM_THREADS="2")
    with tempfile.TemporaryDirectory(prefix="snipnova-speech-") as work:
        output = Path(work) / "result.json"
        try:
            subprocess.run([sys.executable, "-m", __name__, str(media), str(output), *(["--batched"] if batched else [])],
                           env=env, check=True, timeout=600, capture_output=True)
            return json.loads(output.read_text(encoding="utf-8"))
        except (OSError, ValueError, subprocess.SubprocessError) as exc:
            logger.warning("Offline GPU transcription unavailable (%s)", type(exc).__name__)
            return None


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
