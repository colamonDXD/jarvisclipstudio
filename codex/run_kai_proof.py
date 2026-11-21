"""Give the isolated renderer access to the free transcription endpoint and scratch media."""
import os
from pathlib import Path
import subprocess
from dotenv import dotenv_values

root = Path(__file__).resolve().parent
repo = root / "JarvisClipStudio"
proof = root / "kai-proof"
proof.mkdir(exist_ok=True)
install = Path("D:/EditedTool/JarvisClipStudio")
settings = dotenv_values(install / ".env")
env = dict(os.environ, GROQ_API_KEY=settings["GROQ_API_KEY"])
cmd = ["docker", "run", "--rm", "--gpus", "all", "-w", "/tmp",
       "-e", "PYTHONPATH=/work/backend", "-e", "GROQ_API_KEY", "-e", "REACTION_PUNCH=on",
       "-e", "STYLED_HOOK_TITLE=on", "-e", "HOOK_FINISH=on", "-e", "TEMP_DIR=/tmp",
       "-e", "LIVE_GROQ_MODEL=whisper-large-v3",
       "--mount", "type=volume,source=jarvisclipstudio_uploads,target=/original,readonly",
       "--mount", f"type=bind,source={repo},target=/work,readonly",
       "--mount", f"type=bind,source={proof},target=/proof",
       "--mount", f"type=bind,source={root / 'render_kai_windows.py'},target=/run-proof.py,readonly",
       "--entrypoint", "/app/.venv/bin/python", "jarvisclipstudio-backend", "/run-proof.py"]
with (proof / "render.log").open("w", encoding="utf-8") as log:
    subprocess.run(cmd, env=env, stdout=log, stderr=subprocess.STDOUT, check=True)
