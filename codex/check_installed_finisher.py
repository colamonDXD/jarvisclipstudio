"""Run the installed worker on the reviewed VOD window in container scratch space."""
from pathlib import Path
import subprocess
import tempfile
import shutil

root = Path(__file__).resolve().parent
original = root/"verified-kai/caption-proof"
output = root/"deployed-8ee10e2"
output.mkdir(exist_ok=True)
worker = "supoclip-live-worker"
with tempfile.TemporaryDirectory(dir=output) as directory:
    stage = Path(directory)
    for name in ["window_002.mp4", *[p.name for p in original.glob("window_002.*.audio-asr.json")]]:
        shutil.copyfile(original/name,stage/name)
    stage.joinpath("run.py").write_text("""
from pathlib import Path
import json
from src.live.finisher import render_live_clip
from src.api.routes.live import validate_settings
d=Path(__file__).resolve().parent
settings=validate_settings({})
assert not settings["auto_title"] and not settings["hook_title"]
r=render_live_clip(d/"window_002.mp4",d/"kai.mp4",minimum_duration=30,auto_headline=settings["auto_title"],hook_title=settings["hook_title"],credit="@KaiCenat")
assert r["ok"] and r["applied_game_shots"],r.get("error")
assert len(r["burned_words"])==r["expected_words_in_edit"]
(d/"render.json").write_text(json.dumps(r,indent=2,default=str))
print(r["duration"],len(r["burned_words"]),r["applied_game_shots"])
""")
    dest="/tmp/"+stage.name
    subprocess.run(["docker","cp",str(stage),worker+":"+dest],check=True)
    subprocess.run(["docker","exec","-e","PYTHONPATH=/app",worker,"/app/.venv/bin/python",dest+"/run.py"],check=True)
    for name in ["kai.mp4","kai.edit.json","render.json"]:
        subprocess.run(["docker","cp",worker+":"+dest+"/"+name,str(output/name)],check=True)
