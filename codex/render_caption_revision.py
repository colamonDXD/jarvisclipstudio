"""Render the three exact source windows after importing the two genuine audio decodes."""
import json
from pathlib import Path
import time
import os
import tempfile
from src.live.burn_transcript import audio_signature
from src.live.finisher import render_live_clip, _write_cache
from src.live.groq_transcribe import slang_prompt

root = Path("/proof")
titles = ["Kai worries he might not be the father", "Kai spots a change in the water", "Kai hears a warning in the field"]
full = os.getenv("PROOF_FULL") == "1"
folder = root / ("full" if full else "release")
folder.mkdir(exist_ok=True)
for n, source in enumerate(sorted(root.glob("window*.mp4")), 1):
    if os.getenv("PROOF_ONLY") and str(n) != os.environ["PROOF_ONLY"]:
        continue
    for provider, suffix in [("groq", ".groq.json"), ("offline-gpu", ".offline.json")]:
        path = source.with_suffix(suffix)
        deadline = time.monotonic() + 300
        while not path.exists() and time.monotonic() < deadline:
            time.sleep(2)
        if path.is_file():
            key = [audio_signature(source), provider, slang_prompt() if provider == "groq" else "", 1]
            _write_cache(source.with_suffix("." + provider + ".audio-asr.json"),
                         {"key": key, "result": json.loads(path.read_text())})
    target = folder / f"clip_{n:03}.mp4"
    with tempfile.TemporaryDirectory(dir=folder) as tmp:
        staged = Path(tmp) / target.name
        result = render_live_clip(source, staged, minimum_duration=30, hook_title=titles[n-1], credit="@KaiCenat", preserve_window=full)
        print(target.name, result["ok"], result.get("error"), result.get("decoder_word_counts"), len(result.get("burned_words", [])), flush=True)
        assert result["ok"] and result["burned_words"]
        staged.replace(target)
        _write_cache(target.with_suffix(".json"),result)
