"""Save the owner's three named clips via the library service and prepare each editor."""
import asyncio
import json
from sqlalchemy import text
from src.database import AsyncSessionLocal
from src.live.library import save_live_clip
from src.services.editor_service import prepare_editor, editor_dir, read_state
from src.config import get_config


async def main():
    async with AsyncSessionLocal() as db:
        rows = (await db.execute(text(
            "select c.id,c.n,s.user_id from live_clips c join live_sessions s on s.id=c.session_id "
            "where s.id='2296b3dd-54ae-40a6-80b4-d088fd18beb3' and c.n in (7,8,19) order by c.n"
        ))).mappings().all()
        assert len(rows) == 3
        for row in rows:
            ids = await save_live_clip(db, row["user_id"], row["id"])
            assert ids
            task, clip = ids["task_id"], ids["clip_id"]
            filename = clip + ".mp4"
            await prepare_editor({}, task, clip, filename)
            state = read_state(editor_dir(get_config().temp_dir, task, clip, filename))
            print(json.dumps(dict(n=row["n"], task=task, clip=clip, status=state["status"],
                                  duration=state.get("duration"), error=state.get("error"))), flush=True)
            assert state["status"] == "ready"


asyncio.run(main())
