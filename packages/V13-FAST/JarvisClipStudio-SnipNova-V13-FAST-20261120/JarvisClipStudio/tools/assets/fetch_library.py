"""Download a wide, licence-tracked staging library of music and sound effects.

  python tools/assets/fetch_library.py openverse [--only sfx|music] [--pages 2]
  python tools/assets/fetch_library.py kenney
  python tools/assets/fetch_library.py freepd --pages-dir asset-staging/freepd-pages

Everything lands in ``asset-staging/`` (gitignored) and every file gets one
line in ``asset-staging/manifest.jsonl``: where it came from, who made it,
the licence the source states for it, and its sha256. Nothing here ships by
itself. ``curate_library.py`` copies a chosen subset into ``assets/`` and
``tools/assets/check_licences.py`` refuses any shipped file whose manifest
entry is missing, altered, or under a licence outside the allowlist
(Sanji's packaging rule: download wide, ship clean).

Sources are limited to ones that state a reusable licence per item:
- Openverse (api.openverse.org): audio filtered to CC0 / Public Domain Mark;
  mostly Freesound and Wikimedia Commons. Anonymous access: 20 requests a
  minute, 200 a day, 20 results a page.
- Kenney (kenney.nl): audio packs, every pack CC0.
- FreePD (CC0) via the Internet Archive mirror, only for tracks that the
  archived freepd.com category pages list (the mirror holds foreign files).

Sites with no per-item licence (meme-sound boards, Pinterest, GIF services)
are deliberately absent: nothing from them could pass the licence check.
"""
from __future__ import annotations

import argparse
import datetime as _dt
import gzip
import hashlib
import html
import json
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
STAGING = ROOT / "asset-staging"
MANIFEST = STAGING / "manifest.jsonl"
UA = "SnipNovaAssetFetcher/1.0 (open-source video tool)"

# Cue -> Openverse queries. The six canonical cues are the names the EDL
# emits today (auto_director.edl.SFX_EVENT_NAMES); the rest are meme /
# editing staples kept in staging for curation into packs.
SFX_QUERIES: dict[str, list[str]] = {
    "whoosh": ["whoosh", "swoosh", "swish", "woosh transition"],
    "pop": ["pop", "bubble pop", "cartoon pop"],
    "riser": ["riser", "uplifter", "reverse cymbal", "build up"],
    "ding": ["ding", "chime notification", "bell ding"],
    "impact": ["impact", "boom", "cinematic hit", "thud", "bass drop"],
    "sting": ["stinger", "dramatic sting", "suspense hit"],
    "scratch": ["record scratch", "vinyl scratch"],
    "horn": ["air horn", "horn honk"],
    "laugh": ["crowd laugh", "audience laughter"],
    "applause": ["applause", "crowd cheer"],
    "crickets": ["crickets"],
    "drumroll": ["drum roll"],
    "rimshot": ["rimshot", "ba dum tss"],
    "cash": ["cash register", "coins"],
    "shutter": ["camera shutter"],
    "glitch": ["glitch", "digital glitch"],
    "buzzer": ["buzzer wrong", "error buzzer"],
    "correct": ["correct answer", "success jingle"],
    "boing": ["boing", "spring cartoon"],
    "slide_whistle": ["slide whistle"],
    "slap": ["slap", "punch"],
    "typing": ["keyboard typing"],
    "click": ["mouse click", "ui click"],
    "heartbeat": ["heartbeat"],
    "tick": ["clock ticking"],
    "sparkle": ["magic sparkle", "shimmer"],
    "explosion": ["explosion"],
    "gasp": ["gasp"],
    "notification": ["phone notification", "message alert"],
    "laser": ["laser"],
}
SFX_LONG_OK = {"riser", "laugh", "applause", "crickets", "drumroll", "typing", "heartbeat", "tick"}

MUSIC_QUERIES: dict[str, list[str]] = {
    "lofi": ["lofi", "lo-fi hip hop"],
    "hiphop": ["hip hop beat", "rap instrumental"],
    "chill": ["chill", "relaxing"],
    "piano": ["piano"],
    "ambient": ["ambient"],
    "cinematic": ["cinematic", "orchestral"],
    "epic": ["epic"],
    "rock": ["rock instrumental"],
    "funk": ["funk"],
    "upbeat": ["upbeat", "happy"],
    "sad": ["sad", "melancholy"],
    "suspense": ["suspense", "tension"],
    "electronic": ["electronic", "edm"],
    "jazz": ["jazz"],
    "acoustic": ["acoustic guitar"],
    "corporate": ["corporate", "motivational"],
}

KENNEY_PACKS = (
    "interface-sounds", "impact-sounds", "ui-audio", "digital-audio",
    "sci-fi-sounds", "rpg-audio", "casino-audio", "music-jingles",
)

AUDIO_EXT = (".mp3", ".wav", ".ogg", ".flac", ".m4a", ".opus")


def _now() -> str:
    return _dt.datetime.now(_dt.timezone.utc).replace(microsecond=0).isoformat()


def _get(url: str, *, timeout: int = 120, retries: int = 3) -> bytes:
    last: Exception | None = None
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept-Encoding": "gzip"})
            with urllib.request.urlopen(req, timeout=timeout) as r:
                data = r.read()
                if r.headers.get("Content-Encoding") == "gzip":
                    data = gzip.decompress(data)
                return data
        except urllib.error.HTTPError as e:
            last = e
            if e.code == 429:
                time.sleep(30 * (attempt + 1))
                continue
            if e.code >= 500:
                time.sleep(5)
                continue
            raise
        except (urllib.error.URLError, TimeoutError, ConnectionError) as e:
            last = e
            time.sleep(5)
    raise RuntimeError(f"GET failed: {url}: {last}")


def _known() -> dict[str, dict]:
    known: dict[str, dict] = {}
    if MANIFEST.exists():
        for line in MANIFEST.read_text(encoding="utf-8").splitlines():
            if line.strip():
                row = json.loads(line)
                known[row["source_url"]] = row
    return known


def _record(row: dict) -> None:
    STAGING.mkdir(parents=True, exist_ok=True)
    with MANIFEST.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(row, ensure_ascii=False) + "\n")


def _safe(name: str, limit: int = 60) -> str:
    name = re.sub(r"[^A-Za-z0-9._-]+", "_", name).strip("._")
    return (name or "item")[:limit]


def _store(data: bytes, dest: Path, row: dict) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(data)
    row.update({
        "path": dest.relative_to(STAGING).as_posix(),
        "sha256": hashlib.sha256(data).hexdigest(),
        "bytes": len(data),
        "fetched_at": _now(),
    })
    _record(row)


# --------------------------------------------------------------------------- openverse
OV_ALLOWED = {"cc0": "CC0-1.0", "pdm": "PDM-1.0"}


def openverse(only: str | None, pages: int, per_query_keep: int) -> int:
    known = _known()
    jobs: list[tuple[str, str, str]] = []
    if only in (None, "sfx"):
        jobs += [("sfx", cue, q) for cue, qs in SFX_QUERIES.items() for q in qs]
    if only in (None, "music"):
        jobs += [("music", mood, q) for mood, qs in MUSIC_QUERIES.items() for q in qs]
    fetched = 0
    for kind, bucket, query in jobs:
        kept = 0
        for page in range(1, pages + 1):
            params = {
                "q": query, "license": "cc0,pdm", "page_size": 20, "page": page,
            }
            # No category filter: most Freesound items carry none in Openverse.
            url = "https://api.openverse.org/v1/audio/?" + urllib.parse.urlencode(params)
            try:
                payload = json.loads(_get(url))
            except Exception as exc:  # noqa: BLE001 - staging tool, keep going
                print(f"  ! {query} p{page}: {exc}", file=sys.stderr)
                break
            time.sleep(3.2)  # anonymous burst limit: 20 a minute
            results = payload.get("results") or []
            for item in results:
                lic = str(item.get("license") or "").lower()
                if lic not in OV_ALLOWED:
                    continue
                dur_ms = item.get("duration") or 0
                if kind == "sfx":
                    max_ms = 15000 if bucket in SFX_LONG_OK else 6000
                    if not dur_ms or dur_ms > max_ms:
                        continue
                else:
                    if not dur_ms or dur_ms < 45000 or dur_ms > 600000:
                        continue
                src = item.get("url") or ""
                if not src or src in known:
                    continue
                ext = Path(urllib.parse.urlparse(src).path).suffix.lower()
                if ext not in AUDIO_EXT:
                    continue
                try:
                    data = _get(src, timeout=180)
                except Exception as exc:  # noqa: BLE001
                    print(f"  ! download {src}: {exc}", file=sys.stderr)
                    continue
                name = f"ov_{item.get('source','x')}_{_safe(item.get('title') or item['id'], 40)}_{item['id'][:8]}{ext}"
                row = {
                    "source": f"openverse/{item.get('source')}",
                    "source_url": src,
                    "page_url": item.get("foreign_landing_url"),
                    "openverse_id": item.get("id"),
                    "title": item.get("title"),
                    "author": item.get("creator"),
                    "author_url": item.get("creator_url"),
                    "license": OV_ALLOWED[lic],
                    "license_url": item.get("license_url"),
                    "kind": kind,
                    "category": bucket,
                    "query": query,
                    "duration_sec": round(dur_ms / 1000.0, 2),
                    "tags": [t.get("name") for t in (item.get("tags") or [])][:12],
                }
                _store(data, STAGING / "openverse" / kind / bucket / name, row)
                known[src] = row
                fetched += 1
                kept += 1
                if kept >= per_query_keep:
                    break
            print(f"{kind}/{bucket} [{query}] p{page}: {len(results)} results, kept {kept}", flush=True)
            if kept >= per_query_keep or len(results) < 20:
                break
    print(f"openverse: {fetched} new files")
    return 0


# --------------------------------------------------------------------------- kenney
def kenney() -> int:
    known = _known()
    fetched = 0
    for pack in KENNEY_PACKS:
        page_url = f"https://kenney.nl/assets/{pack}"
        try:
            page = _get(page_url).decode("utf-8", "replace")
        except Exception as exc:  # noqa: BLE001
            print(f"  ! {pack}: {exc}", file=sys.stderr)
            continue
        ids = re.findall(r"data-download-id='([0-9a-f]+)'", page)
        licence_ok = "CC0" in page
        if not ids or not licence_ok:
            print(f"  ! {pack}: download id={bool(ids)} cc0 stated={licence_ok}; skipped", file=sys.stderr)
            continue
        # The page's own "Continue without donating" flow: a fresh signed
        # link, requested same-origin (the endpoint wants the page's Referer).
        import http.cookiejar
        opener = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
        headers = {"User-Agent": "Mozilla/5.0", "Referer": page_url}
        opener.open(urllib.request.Request(page_url, headers=headers), timeout=60).read()
        with opener.open(urllib.request.Request(
                f"https://kenney.nl/download-link/{ids[0]}", headers=headers), timeout=60) as r:
            link = json.loads(r.read())
        signed = link.get("url") or ""
        if not signed:
            print(f"  ! {pack}: no signed link", file=sys.stderr)
            continue
        with opener.open(urllib.request.Request(signed, headers=headers), timeout=300) as r:
            blob = r.read()
        zip_url = signed.split("?")[0]
        time.sleep(2)
        pack_dir = STAGING / "kenney" / pack
        pack_dir.mkdir(parents=True, exist_ok=True)
        (pack_dir / "_pack.zip").write_bytes(blob)
        with zipfile.ZipFile(pack_dir / "_pack.zip") as zf:
            for info in zf.infolist():
                inner = info.filename
                if info.is_dir() or Path(inner).suffix.lower() not in AUDIO_EXT:
                    continue
                key = f"{zip_url}#{inner}"
                if key in known:
                    continue
                data = zf.read(info)
                row = {
                    "source": "kenney",
                    "source_url": key,
                    "page_url": page_url,
                    "title": Path(inner).stem,
                    "author": "Kenney (kenney.nl)",
                    "license": "CC0-1.0",
                    "license_url": "https://creativecommons.org/publicdomain/zero/1.0/",
                    "kind": "music" if pack == "music-jingles" else "sfx",
                    "category": pack,
                    "pack_zip_sha256": hashlib.sha256(blob).hexdigest(),
                }
                _store(data, pack_dir / _safe(Path(inner).as_posix().replace("/", "__"), 90), row)
                fetched += 1
        print(f"kenney/{pack}: ok", flush=True)
    print(f"kenney: {fetched} new files")
    return 0


# --------------------------------------------------------------------------- freepd
FREEPD_ARCHIVE = "https://archive.org/download/freepd/"


def _page_titles(html_text: str) -> set[str]:
    text = html.unescape(re.sub(r"<[^>]+>", "\n", html_text))
    return {line.strip().lower() for line in text.splitlines() if line.strip()}


def freepd(pages_dir: Path, limit_per_folder: int) -> int:
    """Download archive tracks whose title the archived freepd.com page lists."""
    meta = json.loads(_get("https://archive.org/metadata/freepd"))
    files = [f for f in meta["files"] if f["name"].lower().endswith(".mp3")]
    listed: dict[str, set[str]] = {}
    authors: dict[str, str] = {}
    for line in (Path(__file__).with_name("freepd_listed.txt")).read_text(encoding="utf-8").splitlines():
        if not line.strip() or line.startswith("#"):
            continue
        page, title, _time, author = line.split("|")
        listed.setdefault(page, set()).add(title.lower())
        authors[title.lower()] = author
    all_titles = set().union(*listed.values())
    known = _known()
    per_folder: dict[str, int] = {}
    fetched = skipped = 0
    for f in files:
        name = f["name"]
        folder = name.split("/")[0] if "/" in name else "(root)"
        title = Path(name).stem
        page_hit = next((p for p, titles in listed.items() if title.lower() in titles), None)
        if page_hit is None or title.lower() not in all_titles:
            skipped += 1
            continue
        if per_folder.get(folder, 0) >= limit_per_folder:
            continue
        url = FREEPD_ARCHIVE + urllib.parse.quote(name)
        if url in known:
            per_folder[folder] = per_folder.get(folder, 0) + 1
            continue
        data = _get(url, timeout=600)
        if hashlib.md5(data).hexdigest() != f.get("md5"):
            print(f"  ! md5 mismatch {name}; not kept", file=sys.stderr)
            continue
        row = {
            "source": "freepd",
            "source_url": url,
            "page_url": f"https://web.archive.org/web/2024/https://freepd.com/{page_hit}.php",
            "title": title,
            "author": authors.get(title.lower()),
            "license": "CC0-1.0",
            "license_url": "https://creativecommons.org/publicdomain/zero/1.0/",
            "kind": "music",
            "category": folder.lower(),
            "archive_md5": f.get("md5"),
            "listed_on": page_hit,
        }
        _store(data, STAGING / "freepd" / folder.lower() / (_safe(title, 80) + ".mp3"), row)
        per_folder[folder] = per_folder.get(folder, 0) + 1
        fetched += 1
        print(f"freepd/{folder}: {title}", flush=True)
    print(f"freepd: {fetched} new, {skipped} not listed on an archived page (not taken)")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    ov = sub.add_parser("openverse")
    ov.add_argument("--only", choices=("sfx", "music"))
    ov.add_argument("--pages", type=int, default=1)
    ov.add_argument("--keep", type=int, default=12, help="max new files per query")
    sub.add_parser("kenney")
    fp = sub.add_parser("freepd")
    fp.add_argument("--pages-dir", default=str(STAGING / "freepd-pages"))
    fp.add_argument("--limit-per-folder", type=int, default=40)
    a = ap.parse_args()
    if a.cmd == "openverse":
        return openverse(a.only, a.pages, a.keep)
    if a.cmd == "kenney":
        return kenney()
    return freepd(Path(a.pages_dir), a.limit_per_folder)


if __name__ == "__main__":
    sys.exit(main())
