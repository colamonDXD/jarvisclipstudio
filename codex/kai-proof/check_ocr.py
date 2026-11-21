import json
from pathlib import Path
from src.live.moments import game_subtitle_lines, remove_matching_dialogue

root = Path("/proof")
result = {}
for path in sorted(root.glob("window_*.mp4")):
    full = json.loads(path.with_suffix(".transcript_cache.full.json").read_text())
    prior = json.loads(path.with_suffix(".transcript_cache.json").read_text())
    words = full["words"]
    lines = game_subtitle_lines(path, {int(w["start"] / 1000) for w in words})
    kept = remove_matching_dialogue(words, lines)
    result[path.name] = dict(raw=len(words), previous=len(prior["words"]), kept=len(kept), lines=lines)
    path.with_suffix(".ocr.json").write_text(json.dumps({**full, "words": kept}))
    print(path.name, len(words), len(prior["words"]), len(kept), flush=True)
(root / "ocr-comparison.json").write_text(json.dumps(result, indent=2))
