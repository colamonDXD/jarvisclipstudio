"""Offline B-roll PLACEMENT simulation on real speech (no stock search, no keys).
Words are approximated by spreading each Whisper segment's words evenly over
the segment. Runs the production planner E._plan_broll_segments per 40 s clip."""
import sys, json, collections, copy
sys.path.insert(0, "backend")
from src.auto_director import edl as E
T = json.load(open("test-footage/references/transcripts.json", encoding="utf-8"))

def words_of(segs):
    out = []
    for a, b, text in segs:
        toks = text.split()
        if not toks: continue
        step = (b - a) / len(toks)
        for i, t in enumerate(toks):
            out.append({"word": t, "text": t, "start": a + i * step, "end": a + (i + 1) * step})
    return out

def run(video, niche, clip=40.0, density=None):
    prof = copy.deepcopy(E.load_profile(niche))
    if density is not None:
        prof["broll"]["density_per_min"] = density
    W = words_of(T[video]["segments"])
    total = W[-1]["end"] if W else 0
    placed = 0; drops = collections.Counter(); clips = 0
    t = 0.0
    while t + clip <= total:
        cw = [dict(w, start=w["start"] - t, end=w["end"] - t) for w in W if t <= w["start"] < t + clip]
        segs = [{"start": 0.0, "end": clip}]
        sink = []
        p = E._plan_broll_segments(prof, cw, clip, segs, [], [], dropped_sink=sink)
        placed += len(p); clips += 1
        drops.update(d["reason"] for d in sink)
        t += clip
    return clips, placed, dict(drops)

for video in ("IY08-3TS2o8.mp4", "BvzLYTIE3Hw.mp4"):
    for niche, dens in (("podcast_news", None), ("finance_business", None), ("podcast_news", 4.0)):
        c, p, d = run(video, niche, density=dens)
        label = f"{niche} density={'profile' if dens is None else dens}"
        print(f"{video:<18} {label:<34} clips={c:>2} placed={p:>3} ({p/c:.1f}/clip) drops={d}")
