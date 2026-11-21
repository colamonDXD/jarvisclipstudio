"""Probe the V13 gateway in a disposable container with only the requested keys."""
import json
import os
from pathlib import Path
import subprocess
import time
import urllib.error
import urllib.request
import uuid

from dotenv import dotenv_values


def check():
    install = Path("D:/EditedTool/JarvisClipStudio")
    root = Path(__file__).resolve().parent
    config = (root / "JarvisClipStudio/litellm_config.yaml").resolve()
    settings = dotenv_values(install / ".env")
    name = "snipnova-fallback-" + uuid.uuid4().hex[:8]
    # Force two failed providers in this container only.
    env = dict(os.environ, NVIDIA_API_KEY="disabled-for-smoke", GROQ_API_KEY="disabled-for-smoke",
               OPENROUTER_API_KEY=settings["OPENROUTER_API_KEY"], LITELLM_MASTER_KEY="sk-" + uuid.uuid4().hex)
    image = subprocess.check_output(["docker", "inspect", "--format", "{{.Image}}",
                                     "jarvisclipstudio-litellm-1"], text=True).strip()
    subprocess.run(["docker", "run", "-d", "--name", name, "-p", "127.0.0.1::4000",
                    "--mount", f"type=bind,source={config},target=/app/config.yaml,readonly",
                    "-e", "NVIDIA_API_KEY", "-e", "GROQ_API_KEY", "-e", "OPENROUTER_API_KEY", "-e", "LITELLM_MASTER_KEY",
                    "-e", "DEEPSEEK_API_KEY=disabled-for-smoke", "-e", "CLOUDFLARE_API_KEY=disabled-for-smoke",
                    "-e", "CLOUDFLARE_ACCOUNT_ID=disabled-for-smoke",
                    image, "--config", "/app/config.yaml", "--port", "4000"], env=env, check=True)
    try:
        port = subprocess.check_output(["docker", "port", name, "4000/tcp"], text=True).strip()
        base = "http://" + port
        for _ in range(45):
            try:
                with urllib.request.urlopen(base + "/health/liveliness", timeout=2):
                    break
            except OSError:
                state = subprocess.check_output(["docker", "inspect", "--format", "{{.State.Status}}", name], text=True)
                if state.strip() == "exited":
                    logs = subprocess.run(["docker", "logs", "--tail", "45", name], capture_output=True, text=True)
                    for line in (logs.stdout + logs.stderr).splitlines():
                        if any(word in line for word in ("Error:", "Exception:", "ERROR:")):
                            print(line.replace(settings["OPENROUTER_API_KEY"], "[redacted]")[:300])
                    raise RuntimeError("Disposable gateway stopped at startup")
                time.sleep(2)
        body = {"model": "clip-free", "messages": [{"role": "user", "content":
                "Return only the JSON object with title set to Kai calls for backup."}], "max_tokens": 128}
        req = urllib.request.Request(base + "/v1/chat/completions", data=json.dumps(body).encode(),
                                     headers={"Content-Type": "application/json",
                                              "Authorization": "Bearer " + env["LITELLM_MASTER_KEY"]})
        with urllib.request.urlopen(req, timeout=160) as response:
            payload = json.load(response)
            model = payload.get("model", "")
            print("HTTP", response.status, "model", model, "reply", bool(payload["choices"][0]["message"]["content"]))
            if not model.endswith(":free") or not payload["choices"][0]["message"]["content"]:
                raise RuntimeError("Expected a nonempty reply from the OpenRouter free fallback")
    finally:
        subprocess.run(["docker", "rm", "-f", name], check=True)


if __name__ == "__main__":
    check()
