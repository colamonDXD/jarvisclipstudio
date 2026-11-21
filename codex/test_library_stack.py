"""Run the live and editor regressions against a disposable PostgreSQL database."""
from pathlib import Path
import secrets
import subprocess
import time

root = Path(__file__).resolve().parent / "JarvisClipStudio"
suffix = secrets.token_hex(5)
network = "snipnova-test-" + suffix
name = "snipnova-db-" + suffix
password = secrets.token_hex(18)
docker = lambda *args, **kwargs: subprocess.run(["docker", *args], check=True, **kwargs)
docker("network", "create", "--internal", network)
try:
    image = subprocess.check_output(["docker", "inspect", "--format", "{{.Image}}", "supoclip-postgres"], text=True).strip()
    docker("run", "--rm", "-d", "--network", network, "--name", name, "--network-alias", "testdb",
           "-e", "POSTGRES_PASSWORD=" + password, image)
    try:
        for _ in range(40):
            if not subprocess.run(["docker", "exec", name, "pg_isready", "-U", "postgres"],
                                  capture_output=True).returncode:
                break
            time.sleep(1)
        docker("run", "--rm", "--network", network, "-w", "/tmp", "-e", "PYTHONPATH=/work/backend",
               "-e", "TEST_DATABASE_URL=postgresql+asyncpg://postgres:" + password + "@testdb/postgres",
               "--mount", f"type=bind,source={root},target=/work,readonly",
               "--entrypoint", "/app/.venv/bin/python", "jarvisclipstudio-backend", "-m", "pytest",
               "-o", "addopts=", "-o", "cache_dir=/tmp/cache", "-q",
               *["/work/backend/tests/unit/test_" + name + ".py" for name in (
                   "live_api", "live_buffer", "live_vod", "live_groq_transcribe", "caption_polish",
                   "hook_finish", "hook_finish_v2", "view_sequencing", "gaming_templates",
                   "live_moments", "live_selection", "live_worker", "yt_captions", "hook_title_styles",
                   "caption_word_timing", "word_align", "edl_render_audio", "live_voice_scan",
                   "ai_edit_assistant", "ai_edit_ollama", "ai_edit_stress", "ai_edit")])
    finally:
        docker("stop", name)
finally:
    docker("network", "rm", network)
