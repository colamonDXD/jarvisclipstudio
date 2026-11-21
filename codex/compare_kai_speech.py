"""Compare word-timed decodes of the same five-second opening in each Kai source."""
import json
import subprocess
import tempfile
from pathlib import Path

import httpx
from dotenv import dotenv_values

folder = Path(__file__).resolve().parent / "kai-proof"
settings = dotenv_values("D:/EditedTool/JarvisClipStudio/.env")
results = []
for video in sorted(folder.glob("window*.mp4")):
    with tempfile.TemporaryDirectory() as temp:
        audio = Path(temp) / "speech.flac"
        subprocess.run(["ffmpeg", "-v", "error", "-i", str(video), "-t", "8", "-vn", "-ac", "1", "-ar",
                        "16000", "-c:a", "flac", str(audio)], check=True)
        for model in ("whisper-large-v3-turbo", "whisper-large-v3"):
            with audio.open("rb") as handle:
                response = httpx.post("https://api.groq.com/openai/v1/audio/transcriptions",
                                      headers={"Authorization": "Bearer " + settings["GROQ_API_KEY"]},
                                      data={"model": model, "language": "en", "temperature": "0",
                                            "response_format": "verbose_json", "timestamp_granularities[]": "word"},
                                      files={"file": ("speech.flac", handle, "audio/flac")}, timeout=60)
            print(video.name, model, response.status_code, flush=True)
            if response.is_success:
                data = response.json()
                results.append({"file": video.name, "model": model, "text": data.get("text"),
                                "words": data.get("words", [])})
(folder / "opening-asr-comparison.json").write_text(json.dumps(results, indent=2), encoding="utf-8")
