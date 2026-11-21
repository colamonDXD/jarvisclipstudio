"""Fetch the bundled music beds (FreePD, CC0) into assets/music and verify them.

  python tools/assets/fetch_freepd_music.py [--dest assets/music]

FreePD.com closed in 2025. Its catalogue is mirrored on the Internet Archive
(item ``freepd``). Every track below was checked against the archived
freepd.com page that listed it, under the site-wide licence statement
"This music is all licensed CC0 1.0 Universal Public Domain Dedication"
(https://web.archive.org/web/20240104044209/https://freepd.com/). Each file
is downloaded from the archive and must match the archive's published MD5
before it is written as ``<mood>.mp3``, the stem the EDL renderer resolves
a profile's ``music_mood`` against. Anything that does not verify is not
written. Provenance details: docs/assets/MUSIC_PROVENANCE.md.
"""
from __future__ import annotations

import argparse
import hashlib
import sys
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
ARCHIVE = "https://archive.org/download/freepd/"

# (mood stem, archive path, archive md5, title, composer, freepd page)
TRACKS = [
    ("comedy", "comedy/Wakka Wakka.mp3", "dec522c5b475f74af7732c089e5b6ec1", "Wakka Wakka", "Bryan Teoh", "comedy.php"),
    ("energetic", "electronic/Goodnightmare.mp3", "197b28aae16e36c5ac41a3e0f3e73124", "Goodnightmare", "Kevin MacLeod", "electronic.php"),
    ("upbeat", "electronic/Arpent.mp3", "b1a53b51752ca0b3dc92c6f83bbd99c1", "Arpent", "Kevin MacLeod", "electronic.php"),
    ("corporate", "electronic/Meditating Beat.mp3", "d27d2c4906bc409ed6259f349c712edf", "Meditating Beat", "Kevin MacLeod", "electronic.php"),
    ("calm", "featured/Study and Relax.mp3", "2c6f64b4065f2e71f09a7337e2880872", "Study and Relax", "Kevin MacLeod", "(home page, featured)"),
    ("funk", "comedy/Fenster's Explanation.mp3", "0ba77f5905c35a4618e311b5c2ff9d81", "Fenster's Explanation", "Kevin MacLeod", "comedy.php"),
    ("phonk", "electronic/Beat One.mp3", "1a068ec590ebc360a5357e539f8a9764", "Beat One", "Kevin MacLeod", "electronic.php"),
]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dest", default=str(ROOT / "assets" / "music"))
    a = ap.parse_args()
    dest = Path(a.dest)
    dest.mkdir(parents=True, exist_ok=True)
    failures = 0
    for mood, path, md5, title, composer, _page in TRACKS:
        target = dest / f"{mood}.mp3"
        if target.exists() and hashlib.md5(target.read_bytes()).hexdigest() == md5:
            print(f"ok (present)  {target.name}  {title} - {composer}")
            continue
        url = ARCHIVE + urllib.parse.quote(path)
        request = urllib.request.Request(url, headers={"User-Agent": "snipnova-asset-fetch"})
        with urllib.request.urlopen(request, timeout=300) as response:
            data = response.read()
        got = hashlib.md5(data).hexdigest()
        if got != md5:
            print(f"FAILED        {mood}: md5 {got} != {md5}; not written", file=sys.stderr)
            failures += 1
            continue
        target.write_bytes(data)
        print(f"ok (fetched)  {target.name}  {title} - {composer}  sha256 {hashlib.sha256(data).hexdigest()[:16]}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
