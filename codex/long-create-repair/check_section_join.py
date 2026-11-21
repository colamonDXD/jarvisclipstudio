import asyncio
import importlib.util
from pathlib import Path
import subprocess
import sys
from time import monotonic

import numpy as np
from sqlalchemy import text
from src.config import get_config
from src.database import AsyncSessionLocal

spec = importlib.util.spec_from_file_location("src._gta_join_check", "/tmp/gta-regression/youtube_source.py")
join = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = join
spec.loader.exec_module(join)
TASK = "9495f237-e7db-401c-9b91-5b51a5114cb5"


def samples(path, at, audio):
    args = ["ffmpeg", "-nostdin", "-v", "error", "-threads", "2", "-ss", str(at), "-i", str(path)]
    if audio:
        args += ["-t", "0.6", "-vn", "-ac", "1", "-ar", "8000", "-f", "f32le", "pipe:1"]
        return np.frombuffer(subprocess.check_output(args), dtype="<f4")
    args += ["-frames:v", "8", "-an", "-vf", "scale=96:54", "-pix_fmt", "gray", "-f", "rawvideo", "pipe:1"]
    return np.frombuffer(subprocess.check_output(args), dtype="uint8").reshape(-1, 54, 96)


async def main():
    cfg = get_config()
    async with AsyncSessionLocal() as db:
        result = await db.execute(text("select payload from task_processing_checkpoints where task_id=:t"), {"t": TASK})
        segments = result.scalar_one()["fast_stream_plan"]["segments"]
    ranges = join.merge_ranges(join.ranges_from_segments(segments), padding=cfg.youtube_section_padding_seconds, duration=19745.43)
    files = sorted((Path(cfg.temp_dir) / "stream-work" / TASK / "job").rglob("section-*.mp4"))
    assert len(files) == len(ranges) == 25
    signatures = {join._exact_section_signature(p, end-start) for p, (start,end) in zip(files, ranges)}
    assert len(signatures) == 1 and None not in signatures
    dest = Path(cfg.temp_dir) / "fast-recovery" / TASK / "joined.mkv"
    started = monotonic()
    mapping = join.assemble_timeline([(join.CachedPiece(a,b,p),a,b) for p,(a,b) in zip(files,ranges)], dest)
    print(f"Joined {len(files)} sections in {monotonic()-started:.1f}s", flush=True)
    for index, (source, span) in enumerate(zip(files, mapping.spans), 1):
        offset = 4.0
        raw = samples(source, offset, False)
        joined = samples(dest, span.local_start+offset-.09, False)
        assert any(np.array_equal(raw[0], frame) for frame in joined), f"Video mismatch in section {index}"
        ref = samples(source, offset, True)[800:3200]
        actual = samples(dest, span.local_start+offset, True)
        if float(np.std(ref)) > .002:
            lag = int(np.argmax(np.correlate(actual, ref, mode="valid"))) - 800
            assert abs(lag) <= 400, f"Audio timing in section {index}: {lag / 8}ms"
    print("Verified picture and sampled audio alignment across 25 sections", flush=True)


if __name__ == "__main__":
    asyncio.run(main())
