"""Register the three completed proof exports under the owner of the original Kai session."""
import asyncio
import json
from pathlib import Path
import subprocess
import sys
import time
from uuid import NAMESPACE_URL, uuid5

sid = str(uuid5(NAMESPACE_URL, "snipnova:verified-kai:20261120:final"))
directory = Path("/app/uploads/live") / sid
url = "https://www.youtube.com/watch?v=VZqAPdUvqnQ"


async def register():
    from sqlalchemy import text
    from src.database import AsyncSessionLocal
    from src.live.library import save_live_clip
    from src.live.finisher import _duration
    events = [json.loads(s) for s in (directory / "events.jsonl").read_text().splitlines()]
    rows = [e for e in events if e.get("type") == "moment" and e.get("status") == "queued"]
    ids = []
    async with AsyncSessionLocal() as db:
        owner = await db.scalar(text("SELECT user_id FROM live_sessions WHERE id=:id AND url LIKE :video"),
                                {"id": "2296b3dd-54ae-40a6-80b4-d088fd18beb3", "video": "%VZqAPdUvqnQ%"})
        if not owner or len(rows) != 3:
            raise RuntimeError("Original owner or three verified moments unavailable")
        settings = {"mode": "vod", "niche": "gaming", "layout_template": "facecam_red",
                    "credit": "@KaiCenat", "max_clips": 3, "auto_title": True}
        await db.execute(text("INSERT INTO live_sessions(id,user_id,url,status,settings,stopped_at) "
                              "VALUES(:id,:owner,:url,'stopped',cast(:settings as jsonb),CURRENT_TIMESTAMP) "
                              "ON CONFLICT(id) DO NOTHING"),
                         {"id": sid, "owner": owner, "url": url, "settings": json.dumps(settings)})
        for row in rows:
            n = row["n"]
            target = directory / f"clip_{n:03}.mp4"
            raw = directory / Path(row["preview_path"]).name
            duration = _duration(target)
            if duration < 30 or not raw.is_file():
                raise RuntimeError("Incomplete export")
            ident = str(uuid5(NAMESPACE_URL, sid + ":" + str(n)))
            await db.execute(text("INSERT INTO live_clips(id,session_id,n,status,reason,signals,strength,talk_share,"
                                  "clip_path,preview_path,duration) VALUES(:id,:sid,:n,'ready',:reason,cast(:signals as jsonb),"
                                  ":strength,:talk,:clip,:preview,:duration) ON CONFLICT(id) DO NOTHING"),
                             {"id": ident, "sid": sid, "n": n, "reason": row["reason"],
                              "signals": json.dumps(row["signals"]), "strength": row["strength"],
                              "talk": row["talk_share"], "clip": str(target), "preview": str(raw), "duration": duration})
            ids.append(ident)
        await db.commit()
        for ident in ids:
            result = await save_live_clip(db, owner, ident)
            assert result
            print(json.dumps(result), flush=True)


if "--inside" in sys.argv:
    asyncio.run(register())
else:
    root = Path(__file__).resolve().parent / "verified-kai"
    rows = [json.loads(s) for s in (root / "events.jsonl").read_text().splitlines()]
    queue = [e for e in rows if e.get("type") == "moment" and e.get("status") == "queued"]
    assert len(queue) == 3
    deadline = time.monotonic() + 480
    while not all((root / "final" / f"clip_{r['n']:03}.json").is_file() for r in queue):
        if time.monotonic() > deadline:
            raise RuntimeError("Waiting for complete final exports timed out")
        time.sleep(3)
    def docker(*arg, **kwargs):
        return subprocess.run(["docker", *map(str, arg)], check=True, **kwargs)
    container = "supoclip-backend"
    dest = str(directory).replace("\\", "/")
    docker("exec", container, "mkdir", "-p", dest)
    files = [root / "events.jsonl"]
    for row in queue:
        raw = root / Path(row["preview_path"]).name
        files += [raw, raw.with_suffix(".transcript_cache.json"), root / "final" / f"clip_{row['n']:03}.mp4"]
    for f in files:
        docker("cp", f, f"{container}:{dest}/{f.name}")
    docker("exec", "-i", container, "/app/.venv/bin/python", "-", "--inside", input=Path(__file__).read_bytes())
