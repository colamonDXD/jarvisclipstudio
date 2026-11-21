import json
import shutil
from pathlib import Path
from src.live.finisher import _duration, render_live_clip
from src.live.vod import cold_open
from src.media.view_sequencing import loudest_reaction_second

folder = Path("/proof/rechecked")
folder.mkdir(exist_ok=True)
source = folder / "window_002.mp4"
shutil.copyfile("/proof/window_002.mp4", source)
onset = loudest_reaction_second(source)
if onset is None:
    raise RuntimeError("No distinct reaction in source audio")
cut = cold_open(source, onset)
media = cut or source
duration = _duration(media)
if cut:
    assert abs(duration - _duration(source) - 2.5) <= 0.25
result = render_live_clip(media, folder / "clip_002.mp4", minimum_duration=30, credit="@KaiCenat",
                          peak_offset=onset + (2.5 if cut else 0), reaction_signals=["scream"],
                          quote_title={"text": "Kai loses five hundred dollars", "creator": "Kai Cenat"})
assert result["ok"], result
(folder / "result.json").write_text(json.dumps(result, indent=2))
print("Source", _duration(source), "edited input", duration, "export", result["duration"], flush=True)
