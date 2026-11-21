"""Quick live preview: a vertical clip of a candidate window in seconds.

Speed over polish (the final clip goes through the normal pipeline): the
buffer window is trimmed to the candidate span, centre-cropped to 9:16 at
720x1280, encoded with x264 ultrafast. The final render later re-uses the
same window file with the full framing/captions engine.
"""
from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Dict, Optional


def render_preview(window: Dict, out_path: Path, max_len: float = 60.0) -> Optional[Path]:
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    duration = min(float(window["duration"]), max_len)
    cmd = ["ffmpeg", "-v", "error", "-y", "-ss", f"{float(window['offset']):.3f}", "-i", str(window["path"]),
           "-t", f"{duration:.3f}",
           "-vf", "crop='min(iw,ih*9/16)':ih,scale=720:1280:flags=bilinear,setsar=1",
           "-c:v", "libx264", "-preset", "ultrafast", "-crf", "26", "-c:a", "aac", "-b:a", "128k",
           "-movflags", "+faststart", str(out_path)]
    result = subprocess.run(cmd, capture_output=True, text=True)
    return out_path if result.returncode == 0 and out_path.is_file() else None
