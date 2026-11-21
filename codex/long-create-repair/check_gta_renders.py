"""Decode this recovered job's published MP4s and sample a frame from each."""
import asyncio
import json
from io import BytesIO
from pathlib import Path
import re
import subprocess

from PIL import Image, ImageDraw
from sqlalchemy import text
from src.database import AsyncSessionLocal

TASK = "9495f237-e7db-401c-9b91-5b51a5114cb5"


async def main():
    async with AsyncSessionLocal() as db:
        result = await db.execute(text(
            "select clip_order, file_path from generated_clips "
            "where task_id=:task order by clip_order"
        ), {"task": TASK})
        clips = result.all()
    if len(clips) != 25:
        raise ValueError(f"Expected 25 published clips, found {len(clips)}")
    sheet = Image.new("RGB", (5 * 164, 5 * 306), "black")
    labels = ImageDraw.Draw(sheet)
    for index, (order, filename) in enumerate(clips):
        path = Path(filename)
        info = json.loads(subprocess.check_output([
            "ffprobe", "-v", "error", "-show_streams", "-show_format", "-of", "json", str(path)
        ]))
        video = next(stream for stream in info["streams"] if stream["codec_type"] == "video")
        assert video["height"] > video["width"]
        duration = float(info["format"]["duration"])
        checked = subprocess.run([
            "ffmpeg", "-nostdin", "-nostats", "-v", "info", "-xerror", "-threads", "2",
            "-i", str(path), "-map", "0:v:0", "-map", "0:a:0", "-af", "volumedetect",
            "-f", "null", "-"
        ], check=True, capture_output=True, text=True)
        level = re.search(r"max_volume: ([-.\d]+) dB", checked.stderr)
        if not level or float(level[1]) <= -90:
            raise ValueError(f"Clip {order} has no measurable audio")
        frame = subprocess.check_output([
            "ffmpeg", "-v", "error", "-ss", str(duration / 2), "-threads", "2",
            "-i", str(path), "-frames:v", "1", "-vf", "scale=162:288",
            "-f", "image2pipe", "-c:v", "mjpeg", "pipe:1"
        ])
        x, y = (index % 5) * 164, (index // 5) * 306
        sheet.paste(Image.open(BytesIO(frame)).convert("RGB"), (x, y))
        labels.text((x + 3, y + 288), f"{order}    {duration:.1f}s")
        print(f"{order}: decoded {duration:.2f}s, {video['width']}x{video['height']}, peak {level[1]} dB", flush=True)
    sheet.save("/tmp/gta-render-check.jpg", quality=91)


if __name__ == "__main__":
    asyncio.run(main())
