"""Prepare isolated copies of the three public Kai windows and obtain word-timed Groq audio reads."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

from dotenv import dotenv_values

root = Path(__file__).resolve().parent
sys.path.insert(0, str(root / "JarvisClipStudio/backend"))
from src.live.groq_transcribe import groq_transcribe_file

base = root / "verified-kai"
out = base / "caption-proof"
out.mkdir(exist_ok=True)
os.environ["GROQ_API_KEY"] = dotenv_values("D:/EditedTool/JarvisClipStudio/.env").get("GROQ_API_KEY") or ""
os.environ["LIVE_GROQ_MODEL"] = "whisper-large-v3-turbo"
for name in ("window_001", "window_002", "window_003_cold"):
    video = out / (name + ".mp4")
    shutil.copy2(base / video.name, video)
    with tempfile.TemporaryDirectory() as tmp:
        audio = Path(tmp) / "speech.mp3"
        subprocess.run(["ffmpeg", "-v", "error", "-i", str(video), "-vn", "-ac", "1", "-ar", "16000", "-b:a", "48k", str(audio)], check=True)
        result = groq_transcribe_file(audio)
    if result and any(s["words"] for s in result["segments"]):
        video.with_suffix(".groq.json").write_text(json.dumps(result), encoding="utf8")
        print(name, sum(len(s["words"]) for s in result["segments"]), flush=True)
