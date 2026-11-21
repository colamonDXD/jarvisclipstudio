import glob, json
from faster_whisper import WhisperModel
m = WhisperModel("base", device="cpu", compute_type="int8")
out = {}
for v in sorted(glob.glob("test-footage/references/*.mp4")):
    segs, info = m.transcribe(v, word_timestamps=False)
    out[v.split("\\")[-1].split("/")[-1]] = {"lang": info.language, "segments": [[round(s.start,1), round(s.end,1), s.text.strip()] for s in segs]}
    json.dump(out, open("test-footage/references/transcripts.json","w",encoding="utf-8"), ensure_ascii=False, indent=1)
    print("done", v, flush=True)
print("ALL DONE", flush=True)
