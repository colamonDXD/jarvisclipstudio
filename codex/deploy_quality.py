"""Deploy the reviewed quality commit over the verified V13 installation."""
from datetime import datetime
import os
from pathlib import Path
import re
import shutil
import subprocess
import time
from urllib.request import urlopen

repo = Path(__file__).resolve().parent / "JarvisClipStudio"
install = Path("D:/EditedTool/JarvisClipStudio")
baseline = next(Path("D:/SnipNova-Dev/packages/V13-FAST").glob("*/JarvisClipStudio"))
import argparse
args = argparse.ArgumentParser()
args.add_argument("--base", required=True)
args.add_argument("--tip", required=True)
release = args.parse_args()
tip = release.tip
compose = ["docker", "compose", "-p", "jarvisclipstudio", "--project-directory", str(install),
           "-f", str(install / "docker-compose.yml"), "-f", str(install / "compose.override.yml")]


def check_idle():
    query = ("select (select count(*) from tasks where status in ('pending','queued','processing','running'))"
             "+(select count(*) from live_sessions where status in ('requested','starting','recording'))")
    active = subprocess.check_output(["docker", "exec", "supoclip-postgres", "psql", "-U", "supoclip",
                                     "-d", "supoclip", "-Atc", query], text=True).strip()
    if active != "0":
        raise RuntimeError("Active clip jobs. Deploy after they finish.")


def write_atomic(path, data):
    temp = path.with_name(path.name + ".snipnova-stage")
    with temp.open("xb") as handle:
        handle.write(data)
    os.replace(temp, path)


def main():
    git = ["git", "-C", str(repo)]
    files = subprocess.check_output(git + ["diff", "--name-only", release.base, tip], text=True).splitlines()
    files = [f for f in files if not f.startswith(("tools/", "backend/tests/")) and f != ".env.example"]
    rebuild_frontend = any(f.startswith("frontend/") or f == "CHANGELOG.md" for f in files)
    services = ["backend", "worker", "ai-worker", "live-worker"]
    if files == ["backend/src/live/finisher.py"]:
        services = ["live-worker"]
    if rebuild_frontend:
        services.append("frontend")
    if any("litellm" in f or f == "docker-compose.yml" for f in files):
        services.append("litellm")
    for rel in files:
        current = install / rel
        previous = subprocess.run(git + ["show", release.base + ":" + rel], capture_output=True)
        if (current.exists() != (previous.returncode == 0) or
            current.exists() and current.read_bytes().replace(b"\r\n", b"\n") != previous.stdout.replace(b"\r\n", b"\n")):
            raise RuntimeError("Installed file differs from the verified parent revision: " + rel)
    check_idle()
    backup = install.parent / ("SnipNova-quality-backup-" + datetime.now().strftime("%Y%m%d-%H%M%S"))
    backup.mkdir()
    for rel in [*files, ".env"]:
        if (install / rel).exists():
            dest = backup / rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(install / rel, dest)
    copied = []
    try:
        for rel in files:
            data = subprocess.check_output(git + ["show", tip + ":" + rel])
            write_atomic(install / rel, data)
            copied.append(rel)
        env = (install / ".env").read_bytes()
        # Preserve custom settings, comments, credentials and line endings.
        for key, value in [(b"STYLED_HOOK_TITLE", b"on"), (b"REACTION_PUNCH", b"on"),
                           (b"LIVE_VOD_LLM", b"openai:clip-free"), (b"LIVE_CLIP_LIBRARY", b"on"),
                           (b"LLM", b"openai:clip-free"), (b"RERANK_LLM", b"openai:clip-free"),
                           (b"AI_EDIT_MODEL", b"openai:clip-free"), (b"LIVE_OFFLINE_WHISPER", b"on"),
                           (b"HYPE_WORD_ACCENT", b"on")]:
            pattern = rb"(?m)^" + key + rb"=[^\r\n]*"
            if re.search(pattern, env):
                env = re.sub(pattern, key + b"=" + value, env)
            else:
                env += b"\r\n" + key + b"=" + value + b"\r\n"
        write_atomic(install / ".env", env)
        copied.append(".env")
        subprocess.run(compose + ["config", "--quiet"], check=True)
        if rebuild_frontend:
            subprocess.run(compose + ["build", "frontend"], check=True)
        check_idle()
        subprocess.run(compose + ["up", "-d", "--no-deps", "--force-recreate", *services], check=True)
        for url in ("http://localhost:8000/health", "http://localhost:3107/changelog"):
            for _ in range(60):
                try:
                    with urlopen(url, timeout=3) as response:
                        body = response.read()
                        if url.endswith("changelog") and b"REACTION_PUNCH" not in body:
                            raise RuntimeError("New changelog entry missing")
                    break
                except OSError:
                    time.sleep(2)
            else:
                raise RuntimeError("Health check failed")
    except Exception:
        for rel in reversed(copied):
            previous = backup / rel
            if previous.exists():
                write_atomic(install / rel, previous.read_bytes())
            else:
                (install / rel).unlink()
        if rebuild_frontend:
            subprocess.run(compose + ["build", "frontend"], check=True)
        subprocess.run(compose + ["up", "-d", "--no-deps", "--force-recreate", *services], check=True)
        raise
    print("Quality release verified. Backup", backup)


if __name__ == "__main__":
    main()
