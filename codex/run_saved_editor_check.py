from pathlib import Path
import subprocess

root = Path(__file__).resolve().parent
with (root / "saved-editors.log").open("wb") as output:
    subprocess.run(["docker", "exec", "-i", "supoclip-worker", "/app/.venv/bin/python", "-"],
                   input=(root / "verify_saved_editors.py").read_bytes(), stdout=output,
                   stderr=subprocess.STDOUT, check=True)
