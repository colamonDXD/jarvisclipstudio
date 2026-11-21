"""Back up, install and verify only the files changed on the reviewed release branch."""
from datetime import datetime
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import os

root = Path(__file__).resolve().parent
repo = root / "JarvisClipStudio"
install = Path("D:/EditedTool/JarvisClipStudio").resolve()
target = "8ee10e2"
def git(*args):
    return subprocess.check_output(["git","-C",str(repo),*args])
def sha(data):
    return hashlib.sha256(data).hexdigest()
files = git("diff","--name-only","a2f375a",target).decode().splitlines()
def destination(rel):
    p=(install/rel).resolve()
    assert p.is_relative_to(install) and not any(x in {".env","compose.override.yml",".git","backups"} for x in p.parts)
    return p
def current(path):
    return sha(path.read_bytes()) if path.exists() else None
def scan():
    return [{"file":f,"old":current(destination(f)),"target":sha(git("show",target+":"+f))} for f in files]
if sys.argv[1]=="backup":
    rows=scan()
    folder=root/"deploy-backup"/datetime.now().strftime("%Y%m%d-%H%M%S")
    folder.mkdir(parents=True)
    for row in rows:
        if row["old"]==row["target"] or not row["old"]: continue
        f=row["file"]
        dest=folder/f
        dest.parent.mkdir(parents=True,exist_ok=True)
        shutil.copy2(destination(f),dest)
        assert current(dest)==row["old"]
    (folder/"manifest.json").write_text(json.dumps(rows,indent=2))
    print(folder, len(rows),"checked;",sum(r["old"]!=r["target"] for r in rows),"to install")
elif sys.argv[1] in {"apply","verify"}:
    folder=Path(sys.argv[2]).resolve()
    assert folder.parent==root/"deploy-backup"
    rows=json.loads((folder/"manifest.json").read_text())
    assert [r["file"] for r in rows]==files
    if sys.argv[1]=="apply":
        for r in rows:
            p=destination(r["file"])
            assert current(p) in (r["old"],r["target"]),r["file"]
            if r["old"] and r["old"]!=r["target"]: assert current(folder/r["file"])==r["old"]
        for r in rows:
            p=destination(r["file"])
            if current(p)==r["target"]: continue
            data=git("show",target+":"+r["file"])
            assert sha(data)==r["target"]
            p.parent.mkdir(parents=True,exist_ok=True)
            with tempfile.NamedTemporaryFile(dir=p.parent,delete=False) as h:
                staged=Path(h.name)
                h.write(data)
            try:
                os.replace(staged,p)
            finally:
                staged.unlink(missing_ok=True)
    verified=scan()
    assert all(r["old"]==r["target"] for r in verified)
    (folder/"installed-files.md").write_text("# Installed files at 8ee10e2\n\nEvery installed SHA-256 below matches the Git blob bytes.\n\n| File | Installed SHA-256 |\n|---|---|\n"+"".join(f"| `{r['file']}` | `{r['target']}` |\n" for r in verified))
    print("Verified",len(verified),"release files")
