"""Offline speech benchmark on the three source windows, with real word probabilities."""
import json
import time
from pathlib import Path

from faster_whisper import WhisperModel

folder = Path("/proof")
started = time.monotonic()
model = WhisperModel("/model", device="cuda", compute_type="int8_float16", local_files_only=True)
print("CUDA model ready", round(time.monotonic() - started, 1), flush=True)
for source in sorted(folder.glob("window*.mp4")):
    begin = time.monotonic()
    segments, info = model.transcribe(str(source), language="en", word_timestamps=True,
                                      vad_filter=True, beam_size=5, condition_on_previous_text=False)
    rows = [{"start": s.start, "end": s.end, "text": s.text, "no_speech_prob": s.no_speech_prob,
             "avg_logprob": s.avg_logprob,
             "words": [{"word": w.word, "start": w.start, "end": w.end, "probability": w.probability}
                       for w in s.words or []]} for s in segments]
    data = {"language": info.language, "text": " ".join(s["text"].strip() for s in rows), "segments": rows}
    (folder / (source.stem + ".local-asr.json")).write_text(json.dumps(data, indent=2))
    print(source.name, "words", sum(len(s["words"]) for s in rows),
          "elapsed", round(time.monotonic() - begin, 1), flush=True)
