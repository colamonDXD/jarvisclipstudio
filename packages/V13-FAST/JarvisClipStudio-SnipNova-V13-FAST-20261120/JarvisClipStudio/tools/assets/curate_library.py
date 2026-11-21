"""Curate the staging library into the shipped variant libraries + licence manifest.

  python tools/assets/curate_library.py [--sfx-per-bucket 10] [--music-per-mood 8]
  python tools/assets/check_licences.py          # must print "ship-clean"

Reads ``asset-staging/manifest.jsonl`` (written by fetch_library.py), picks a
varied subset per SFX cue / music mood, normalises each file and writes it
to ``assets/sfx/library/<bucket>/`` or ``assets/music/library/<mood>/`` (the
folders media.asset_library draws variants from). Every shipped file -
including the long-standing stem files and the synthesised originals - gets
an entry in ``assets/ASSET_MANIFEST.json`` with its source, author, licence
and sha256.

Processing (ffmpeg):
- SFX: leading silence trimmed (a whoosh must land on its cue), length
  capped, 30 ms fade-out, loudness normalised like make_sfx.py
  (I=-16 LUFS, TP -1.5), 48 kHz stereo MP3 192k. Near-silent results are
  dropped.
- Music: first 120 s (the renderer loops the bed; clips are shorter),
  2 s fade-out, loudness matched to the mood's existing stem file (or
  -12 LUFS, the level of the FreePD stems), 48 kHz stereo MP3 160k.
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))
from src.media.asset_library import MANIFEST_FILENAME, manifest_entry  # noqa: E402

STAGING = ROOT / "asset-staging"
ASSETS = ROOT / "assets"

CUES = ("whoosh", "pop", "riser", "ding", "impact", "sting")
LONG_SFX = {"riser", "laugh", "applause", "crickets", "drumroll", "heartbeat", "tick", "typing"}

# Kenney filename -> bucket. First match wins; unmatched files stay in staging.
KENNEY_RULES = [
    (r"impactPunch", "slap"),
    (r"explosion", "explosion"),
    (r"impact(Metal_heavy|Plate_heavy|Bell_heavy|Generic_light|Mining|Plank|Wood)", "impact"),
    (r"(glitch)", "glitch"),
    (r"(error)", "buzzer"),
    (r"(confirmation|maximize|question|pepSound|powerUp|threeTone|twoTone)", "ding"),
    (r"(drop_|pluck|bong)", "pop"),
    (r"(laser|zap|phaser)", "laser"),
    (r"(chips|handleCoins|chip-lay)", "cash"),
    (r"(click|select|switch_|toggle|rollover|mouseclick|tick_)", "click"),
    (r"(scratch_)", "scratch"),
    (r"jingles_(HIT|NES|PIZZI|SAX|STEEL)", "jingle"),
    (r"(forceField|phaseJump|highUp)", "riser"),
]

# FreePD archived page -> mood folder. Titles below override (calm, funk,
# corporate, phonk, which FreePD did not have as pages).
FREEPD_PAGE_MOOD = {
    "comedy": "comedy", "upbeat": "upbeat", "electronic": "energetic", "epic": "epic",
    "horror": "suspense", "romantic": "emotional", "scoring": "cinematic", "world": "world",
}
FREEPD_TITLE_MOOD = {
    **{t: "calm" for t in (
        "Study and Relax", "Infinite Peace", "River Meditation", "Aquarium", "Icicles Melting",
        "Wisdom in the Sun", "Adding the Sun", "Infinite Wonder", "Kalimba Relaxation Music",
        "Koiduuni", "Udune Maastik", "Lainete Laul", "Forest Night", "Painting Room")},
    **{t: "funk" for t in (
        "Funkeriffic", "Fake It Til You Fake It", "Bass Meant Jazz", "Groovin", "Downtown Boogie",
        "Sunday Dub", "Compy Jazz", "Flutey Jazz", "Funky Energy Loop", "Fenster's Explanation")},
    **{t: "corporate" for t in (
        "Inspiration", "Motions", "Advertime", "Fresh Focus", "From Page to Practice",
        "Inventing Flight", "Screen Saver", "Meditating Beat")},
    **{t: "phonk" for t in ("Beat Thee", "Hippety Hop", "Backbeat", "Beat One", "Blippy Trance")},
}


def _run(cmd: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True)


def _loudness(path: Path) -> float | None:
    out = _run(["ffmpeg", "-v", "info", "-i", str(path), "-af", "ebur128", "-f", "null", "-"]).stderr
    hits = re.findall(r"^\s+I:\s+(-?[\d.]+) LUFS", out, re.M)
    return float(hits[-1]) if hits else None


def _peak(path: Path) -> float | None:
    out = _run(["ffmpeg", "-v", "info", "-i", str(path), "-af", "volumedetect", "-f", "null", "-"]).stderr
    hit = re.search(r"max_volume: (-?[\d.]+) dB", out)
    return float(hit.group(1)) if hit else None


def _duration(path: Path) -> float:
    out = _run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)]).stdout
    try:
        return float(out.strip())
    except ValueError:
        return 0.0


def _process_sfx(src: Path, dst: Path, bucket: str) -> bool:
    cap = 10.0 if bucket in LONG_SFX else 4.0
    chain = (f"silenceremove=start_periods=1:start_threshold=-45dB:start_silence=0.01,"
             f"atrim=0:{cap},areverse,afade=t=in:d=0.03,areverse,"
             f"aformat=channel_layouts=stereo,loudnorm=I=-16:TP=-1.5:LRA=11")
    dst.parent.mkdir(parents=True, exist_ok=True)
    r = _run(["ffmpeg", "-v", "error", "-y", "-i", str(src), "-af", chain, "-ar", "48000",
              "-c:a", "libmp3lame", "-b:a", "192k", str(dst)])
    if r.returncode != 0 or not dst.exists():
        return False
    peak, dur = _peak(dst), _duration(dst)
    if peak is None or peak < -20 or dur < 0.06:
        dst.unlink(missing_ok=True)
        return False
    return True


def _process_music(src: Path, dst: Path, target_lufs: float) -> bool:
    chain = (f"atrim=0:120,afade=t=out:st=118:d=2,aformat=channel_layouts=stereo,"
             f"loudnorm=I={target_lufs}:TP=-1.0:LRA=11")
    dst.parent.mkdir(parents=True, exist_ok=True)
    r = _run(["ffmpeg", "-v", "error", "-y", "-i", str(src), "-af", chain, "-ar", "48000",
              "-c:a", "libmp3lame", "-b:a", "160k", str(dst)])
    return r.returncode == 0 and dst.exists() and _duration(dst) > 20


def _spread(items: list, n: int) -> list:
    """n items spread evenly through the list (variety over the first n)."""
    if len(items) <= n:
        return items
    step = len(items) / n
    return [items[int(i * step)] for i in range(n)]


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", (text or "item").lower()).strip("_")[:48] or "item"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--sfx-per-bucket", type=int, default=10)
    ap.add_argument("--music-per-mood", type=int, default=8)
    a = ap.parse_args()

    rows = [json.loads(line) for line in (STAGING / "manifest.jsonl").read_text(encoding="utf-8").splitlines() if line.strip()]
    manifest_path = ASSETS / MANIFEST_FILENAME
    manifest = json.loads(manifest_path.read_text(encoding="utf-8")) if manifest_path.exists() else {"files": {}}
    files: dict[str, dict] = manifest.setdefault("files", {})

    sfx_buckets: dict[str, list[dict]] = defaultdict(list)
    music_moods: dict[str, list[dict]] = defaultdict(list)
    for row in rows:
        src = row["source"].split("/")[0]
        if src == "openverse" and re.search(r"(^|/|_)LL-Q\d", (row.get("title") or "") + " " + row["path"]):
            continue  # Lingua Libre word pronunciations: speech, not sound effects
        if src == "openverse" and row["kind"] == "sfx":
            sfx_buckets[row["category"]].append(row)
        elif src == "kenney" and row["kind"] == "sfx":
            name = Path(row["path"]).name
            for pattern, bucket in KENNEY_RULES:
                if re.search(pattern, name):
                    sfx_buckets[bucket].append(row)
                    break
        elif src == "kenney" and row["kind"] == "music":
            for pattern, bucket in KENNEY_RULES:
                if re.search(pattern, Path(row["path"]).name):
                    sfx_buckets[bucket].append(row)
                    break
        elif src == "freepd":
            mood = FREEPD_TITLE_MOOD.get(row["title"]) or FREEPD_PAGE_MOOD.get(row.get("listed_on", ""))
            if mood:
                music_moods[mood].append(row)
        elif src == "openverse" and row["kind"] == "music":
            music_moods[row["category"]].append(row)

    shipped = 0
    for bucket, items in sorted(sfx_buckets.items()):
        # Prefer short, punchy sources for the timed cues; keep source variety.
        items.sort(key=lambda r: (r.get("duration_sec") or 0, r["path"]))
        kept = 0
        for row in _spread(items, a.sfx_per_bucket * 2):
            if kept >= a.sfx_per_bucket:
                break
            dst = ASSETS / "sfx" / "library" / bucket / f"{row['source'].split('/')[0][:2]}_{_slug(row.get('title'))}.mp3"
            if not dst.exists() and not _process_sfx(STAGING / row["path"], dst, bucket):
                continue
            files[dst.relative_to(ASSETS).as_posix()] = manifest_entry(
                dst, license=row["license"], license_url=row.get("license_url"),
                source=row["source"], source_url=row["source_url"], page_url=row.get("page_url"),
                title=row.get("title"), author=row.get("author"), staged_sha256=row["sha256"],
                processing="silence-trim, length cap, fade, loudnorm I=-16, mp3 192k",
            )
            kept += 1
            shipped += 1
        print(f"sfx/{bucket}: {kept}")

    for mood, items in sorted(music_moods.items()):
        stem = next((p for p in (ASSETS / "music").glob(f"{mood}.*")), None)
        measured = _loudness(stem) if stem else None
        target = round(measured, 1) if measured is not None else -12.0
        items.sort(key=lambda r: r["path"])
        kept = 0
        for row in _spread(items, a.music_per_mood * 2):
            if kept >= a.music_per_mood:
                break
            if row.get("title") in _stem_titles():
                continue  # already shipped as a mood stem file
            dst = ASSETS / "music" / "library" / mood / f"{_slug(row.get('title'))}.mp3"
            if not dst.exists() and not _process_music(STAGING / row["path"], dst, target):
                continue
            files[dst.relative_to(ASSETS).as_posix()] = manifest_entry(
                dst, license=row["license"], license_url=row.get("license_url"),
                source=row["source"], source_url=row["source_url"], page_url=row.get("page_url"),
                title=row.get("title"), author=row.get("author"), staged_sha256=row["sha256"],
                processing=f"first 120 s, 2 s fade-out, loudnorm I={target}, mp3 160k",
            )
            kept += 1
            shipped += 1
        print(f"music/{mood}: {kept} (target {target} LUFS)")

    _record_stems_and_originals(files)
    manifest["allowed_licenses"] = ["CC0-1.0", "PDM-1.0", "original"]
    manifest_path.write_text(json.dumps(manifest, indent=1, ensure_ascii=False, sort_keys=True) + "\n", encoding="utf-8")
    print(f"curated {shipped} files; manifest has {len(files)} entries")
    return 0


def _stem_titles() -> set[str]:
    sys.path.insert(0, str(ROOT / "tools" / "assets"))
    from fetch_freepd_music import TRACKS  # noqa: PLC0415
    return {t[3] for t in TRACKS}


def _record_stems_and_originals(files: dict[str, dict]) -> None:
    sys.path.insert(0, str(ROOT / "tools" / "assets"))
    from fetch_freepd_music import ARCHIVE, TRACKS  # noqa: PLC0415
    for mood, path, md5, title, composer, page in TRACKS:
        f = ASSETS / "music" / f"{mood}.mp3"
        if f.exists():
            files[f"music/{mood}.mp3"] = manifest_entry(
                f, license="CC0-1.0", license_url="https://creativecommons.org/publicdomain/zero/1.0/",
                source="freepd", source_url=ARCHIVE + path, archive_md5=md5, title=title,
                author=composer, page_url=f"freepd.com {page}",
            )
    for f in sorted((ASSETS / "sfx").glob("*.wav")):
        files[f"sfx/{f.name}"] = manifest_entry(
            f, license="original", source="synthesised: tools/assets/make_sfx.py", title=f.stem)
    lib = ASSETS / "sfx" / "library"
    sys.path.insert(0, str(ROOT / "tools" / "assets"))
    from make_meme_sfx import SOUNDS  # noqa: PLC0415
    for rel in SOUNDS:
        f = lib / f"{rel}.wav"
        if f.exists():
            files[f.relative_to(ASSETS).as_posix()] = manifest_entry(
                f, license="original", source="synthesised: tools/assets/make_meme_sfx.py",
                title=Path(rel).name)


if __name__ == "__main__":
    sys.exit(main())
