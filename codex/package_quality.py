"""Export verifiable source patches and reproducible public ranking evidence."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import zipfile

workspace = Path(__file__).resolve().parents[1]
codex = workspace / "codex"
repo = codex / "JarvisClipStudio"
out = workspace / "handoff/codex-round2"
git = ["git", "-C", str(repo)]
head = subprocess.check_output(git + ["rev-parse", "HEAD"], text=True).strip()
patch_dir = out / "patches"
patch_dir.mkdir(exist_ok=True)
patches = subprocess.check_output(git + ["format-patch", "--binary", "--no-signature",
                                         "-o", str(patch_dir), "a2f375a.." + head], text=True).splitlines()
with tempfile.TemporaryDirectory() as tmp:
    env = dict(os.environ, GIT_INDEX_FILE=str(Path(tmp) / "release.index"))
    subprocess.run(git + ["read-tree", "a2f375a"], env=env, check=True)
    for p in patches:
        subprocess.run(git + ["apply", "--cached", "--whitespace=nowarn", p], env=env, check=True)
    tree = subprocess.check_output(git + ["write-tree"], env=env, text=True).strip()
    assert tree == subprocess.check_output(git + ["rev-parse", head + "^{tree}"], text=True).strip()
data = out / "data"
data.mkdir(exist_ok=True)
for p in [codex / "context-picker-confirmation.json", codex / "matched-sources/all-three-finders.json",
          codex / "train-18/training_report.json", codex / "recent-metrics.json",
          codex / "revised-metrics.json", workspace / "hook-study/viral26.json"]:
    shutil.copyfile(p, data / p.name)
for p in (codex / "selection-run").glob("*/features.json"):
    target = data / "public-stream-features" / p.parent.name / p.name
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(p.read_bytes())
files = [out / "TO_MUSE.md", *sorted(data.rglob("*.json")), *map(Path, patches)]
checksums = {f.relative_to(out).as_posix(): hashlib.sha256(f.read_bytes()).hexdigest() for f in files}
manifest = out / "manifest.json"
manifest.write_text(json.dumps({"commit": head, "tree": tree, "sha256": checksums}, indent=2))
archive = out / "SnipNova-quality-patches.zip"
with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED, compresslevel=8) as bundle:
    for f in [*files, manifest]:
        bundle.write(f, f.relative_to(out))
with zipfile.ZipFile(archive) as bundle:
    assert bundle.testzip() is None
print(len(patches), "patches reproduce", head, "archive", archive.stat().st_size,
      "SHA256", hashlib.sha256(archive.read_bytes()).hexdigest())
