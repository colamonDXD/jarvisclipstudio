"""Exercise the normal upload/task endpoints and worker against an isolated test DB."""
import asyncio
import json
import os
import shutil
from pathlib import Path
from unittest.mock import patch
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
from src.database import configure_database, init_db
from src.config import Config
from src.main import create_app
from src.services.task_service import TaskService
from src.services.video_service import VideoService
from tests.conftest import FakeQueueAdapter, auth_headers
from tests.fixtures.factories import create_user

async def main():
    root = Path("/proof")
    out = root / "title-off"
    boxes = json.loads((out / "drawn-boxes.json").read_text())
    engine = create_async_engine(os.environ["DATABASE_URL"])
    configure_database(engine=engine)
    await init_db()
    config = Config()
    config.self_host = True
    config.monetization_enabled = False
    config.backend_auth_secret = "test-backend-auth-secret"
    app = create_app(config=config, queue_adapter=FakeQueueAdapter)
    async with async_sessionmaker(engine, expire_on_commit=False)() as db:
        from sqlalchemy import text
        if not (await db.execute(text("select id from users where id='user-1'"))).first():
            await create_user(db,user_id="user-1")
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver", headers=auth_headers.__wrapped__()) as client:
            with (root / "window_002.mp4").open("rb") as video:
                upload = await client.post("/upload", files={"video":("gaming.mp4",video,"video/mp4")})
            upload.raise_for_status()
            source = upload.json()["video_path"]
            req = {"source":{"url":source},"layout_template":"facecam_red","boxes":boxes,
                   "creation_mode":"manual_cutter_ai","manual_ranges":[{"start":10,"end":40}],
                   "add_subtitles":False,"template_mode":"plain","niche_override":"gaming"}
            result = await client.post("/tasks/",json=req)
            print(result.status_code,result.text[:450],flush=True)
            result.raise_for_status()
            task_id = result.json()["task_id"]
        service = TaskService(db,config)
        saved = await service.task_repo.get_task_by_id(db,task_id)
        assert saved["boxes"]["arrange"]=="bottom" and saved["hook_title_enabled"] is False
        async def cached_speech(video_path, **kwargs):
            cached=json.loads((root/"window_002.transcript_cache.json").read_text())
            Path(video_path).with_suffix(".transcript_cache.json").write_text(json.dumps(cached))
            return " ".join(w["text"] for w in cached["words"])
        with patch.object(VideoService,"generate_transcript",cached_speech):
            result = await service.process_task(task_id,source,"video_url","user-1",add_subtitles=False)
        assert result["clips_count"]==1,result
        clips=list((Path(config.temp_dir)/"clips").rglob("*.mp4"))
        assert clips,clips
        clip=max(clips,key=lambda p:p.stat().st_size)
        shutil.copyfile(clip,out/"uploaded-gameplay.mp4")
        (out/"uploaded-task.json").write_text(json.dumps({"upload":source,"task":task_id,"boxes":saved["boxes"],"clips_count":1},indent=2))
    await engine.dispose()
asyncio.run(main())
