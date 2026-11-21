import os
from pathlib import Path
import subprocess
import sys
from dotenv import dotenv_values

root = Path(__file__).resolve().parent
out = root / "verified-kai"
out.mkdir(exist_ok=True)
cfg = dotenv_values("D:/EditedTool/JarvisClipStudio/.env")
env = dict(os.environ, OPENAI_API_KEY=cfg["OPENAI_API_KEY"])
final = "--final" in sys.argv
review = "--review" in sys.argv or final
program = root / ("render_review.py" if review else "run_public_vod.py")
command = ["docker", "run", "--rm", "--name", "snipnova-final-kai" if final else "snipnova-review-kai" if review else "snipnova-verified-kai", "--gpus", "all",
           "--network", "supoclip-network", "-w", "/tmp", "-e", "PYTHONPATH=/work/backend",
           "-e", "OPENAI_API_KEY", "-e", "OPENAI_BASE_URL=http://litellm:4000/v1",
           "-e", "NVIDIA_DRIVER_CAPABILITIES=compute,video,utility", "-e", "HYPE_WORD_ACCENT=on",
           "-e", "REVIEW_FOLDER=" + ("final" if final else "review"),
           "-e", "LIVE_VOD_LLM=openai:clip-free", "-e", "LIVE_OFFLINE_WHISPER=on",
           "-e", "LIVE_TRANSCRIPTION_PROVIDER=local", "-e", "TRANSCRIPTION_PROVIDER=faster_whisper",
           "-e", "STYLED_HOOK_TITLE=on", "-e", "HOOK_FINISH=on", "-e", "REACTION_PUNCH=on",
           "--mount", "type=volume,source=jarvisclipstudio_speech_runtime,target=/opt/snipnova-speech,readonly",
           "--mount", f"type=bind,source={root / 'JarvisClipStudio'},target=/work,readonly",
           "--mount", f"type=bind,source={program},target=/run.py,readonly",
           "--mount", f"type=bind,source={out},target=/proof", "--entrypoint", "/app/.venv/bin/python",
           "jarvisclipstudio-backend", "/run.py"]
with (out / ("final.log" if final else "review.log" if review else "run.log")).open("w") as log:
    subprocess.run(command, env=env, stdout=log, stderr=subprocess.STDOUT, check=True)
