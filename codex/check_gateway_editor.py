"""Verify a synthetic typed editor operation through the installed free gateway."""
import asyncio
import os
from pathlib import Path
import subprocess
import sys

if "--inside" in sys.argv:
    from src.services.ai_edit_service import ai_editing_configured, plan_edit
    assert ai_editing_configured()
    result = asyncio.run(plan_edit("Set the audio volume to 0.5 on this 35-second demo clip. Return just one set operation."))
    assert any(op.type == "set" and op.setting == "audio.volume" and float(op.value) == 0.5
               for op in result.operations), result
    print("Validated audio.volume=0.5 through the free editor gateway.")
else:
    from dotenv import dotenv_values
    root = Path(__file__).resolve().parent
    cfg = dotenv_values("D:/EditedTool/JarvisClipStudio/.env")
    env = dict(os.environ, OPENAI_API_KEY=cfg["OPENAI_API_KEY"])
    subprocess.run(["docker", "run", "--rm", "--network", "supoclip-network", "-w", "/tmp",
                    "-e", "OPENAI_API_KEY", "-e", "OPENAI_BASE_URL=http://litellm:4000/v1",
                    "-e", "AI_EDIT_MODEL=openai:clip-free", "-e", "PYTHONPATH=/work/backend",
                    "--mount", f"type=bind,source={root / 'JarvisClipStudio'},target=/work,readonly",
                    "--mount", f"type=bind,source={Path(__file__).resolve()},target=/check.py,readonly",
                    "--entrypoint", "/app/.venv/bin/python", "jarvisclipstudio-backend", "/check.py", "--inside"],
                   env=env, check=True)
