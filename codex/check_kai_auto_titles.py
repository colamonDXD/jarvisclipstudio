"""Exercise the development title generator via the already configured free gateway."""
import json
import os
from pathlib import Path
import subprocess
import sys

if __name__ == "__main__" and "--inside" in sys.argv:
    from src.live.finisher import gaming_scene_title
    results = []
    for path in sorted(Path("/proof").glob("window*.groq.json")):
        data = json.loads(path.read_text())
        words = [{"text":w["word"]} for s in data["segments"] for w in s["words"]]
        results.append({"source":path.stem, "automatic_title":gaming_scene_title(words,"@KaiCenat")})
    print(json.dumps(results),flush=True)
elif __name__ == "__main__":
    from dotenv import dotenv_values
    base = Path(__file__).resolve().parent
    proof = base/"verified-kai/caption-proof"
    env = dict(os.environ,OPENAI_API_KEY=dotenv_values("D:/EditedTool/JarvisClipStudio/.env")["OPENAI_API_KEY"])
    cmd = ["docker","run","--rm","--network","supoclip-network","-w","/tmp","-e","OPENAI_API_KEY",
           "-e","OPENAI_BASE_URL=http://litellm:4000/v1","-e","LIVE_VOD_LLM=openai:clip-free",
           "-e","PYTHONPATH=/work/backend","-v",str(base/"JarvisClipStudio")+":/work:ro",
           "-v",str(proof)+":/proof:ro","-v",str(Path(__file__).resolve())+":/title.py:ro",
           "--entrypoint","/app/.venv/bin/python","jarvisclipstudio-backend","/title.py","--inside"]
    result = subprocess.run(cmd,env=env,capture_output=True,text=True,check=True,timeout=180)
    values = json.loads(result.stdout.strip().splitlines()[-1])
    (proof/"release/automatic-titles.json").write_text(json.dumps(values,indent=2),encoding="utf8")
    print(values)
