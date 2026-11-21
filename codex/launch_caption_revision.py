from pathlib import Path
import os
import subprocess
import sys
from dotenv import dotenv_values

root = Path(__file__).resolve().parent
proof = root / "verified-kai/caption-proof"
env = dict(os.environ, GROQ_API_KEY=dotenv_values("D:/EditedTool/JarvisClipStudio/.env")["GROQ_API_KEY"])
cmd = ["docker","run","--rm","--gpus","all","-w","/tmp","-e","GROQ_API_KEY",
       "-e","NVIDIA_DRIVER_CAPABILITIES=compute,video,utility","-e","STYLED_HOOK_TITLE=on",
       "-e","HOOK_FINISH=on","-e","REACTION_PUNCH=on","-e","LIVE_OFFLINE_WHISPER=on","-e","PYTHONPATH=/work/backend",
       "-v","jarvisclipstudio_speech_runtime:/opt/snipnova-speech:ro",
       "-v",str(root/"JarvisClipStudio")+":/work:ro","-v",str(proof)+":/proof",
       "-v",str(root/"render_caption_revision.py")+":/render.py:ro",
       "--entrypoint","/app/.venv/bin/python","jarvisclipstudio-backend","/render.py"]
if "--full" in sys.argv:
    cmd[2:2] = ["-e","PROOF_FULL=1"]
if "--third" in sys.argv:
    cmd[2:2] = ["-e","PROOF_ONLY=3"]
if "--first" in sys.argv:
    cmd[2:2] = ["-e","PROOF_ONLY=1"]
with (proof/("full.log" if "--full" in sys.argv else "render.log")).open("w") as log:
    subprocess.run(cmd,env=env,stdout=log,stderr=subprocess.STDOUT,check=True)
