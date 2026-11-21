"""Train the long-stream moment ranking against what viewers actually rewatched.

  python tools/live/train_selection.py collect --out D:/SnipNova-Dev/selection-data --target 40
  python tools/live/train_selection.py train   --data D:/SnipNova-Dev/selection-data --minutes 90

Labels: YouTube's "Most replayed" heatmap (100 points per video; the Mr. HiSum
dataset, NeurIPS 2023, shows it is a reliable highlight label aggregated over
50k+ viewers). The heatmap is ONLY the answer key: the ranker sees nothing but
the streamer's audio features.

collect: searches YouTube for long streams of many kinds (gaming, IRL, just
chatting, podcasts), keeps 40 min - 6 h videos that have a heatmap, downloads
the audio only, extracts the per-second voice features (the same scan the
product runs), saves features + heatmap, deletes the audio.

train: autoresearch-style loop. Proposes parameter changes, scores them on the
training streams, keeps a change only when it helps, and reports the score on
HELD-OUT streams it never trained on. Score of a video = mean heat percentile
(0-1, random = 0.5) of the top-K spaced picks. The params file is written only
when the held-out score improves over the shipped defaults.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import random
import shutil
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))
sys.path.insert(0, str(ROOT / "tools" / "phase2" / "_winshim"))

QUERIES = [
    "gta 6 stream full vod", "gta 6 livestream day 1 full", "kai cenat stream full", "ishowspeed stream full",
    "xqc stream vod", "just chatting stream full vod", "irl stream full vod", "minecraft stream full vod",
    "fortnite stream full vod", "valorant stream full vod", "podcast full episode reaction", "horror game stream full",
    "elden ring stream full", "speedrun stream full vod", "react stream full vod", "subathon stream vod",
]


MAX_PER_CHANNEL = 4


def _ytdlp():
    import os

    if os.getenv("YTDLP_BIN"):
        return os.environ["YTDLP_BIN"]
    exe = ROOT / "backend" / ".venv" / "Scripts" / "yt-dlp.exe"
    if exe.is_file():
        return str(exe)
    import shutil as sh

    return sh.which("yt-dlp") or "yt-dlp"


def _run(args, timeout=None):
    return subprocess.run([_ytdlp(), "--no-update", "--no-warnings", "-q", "--js-runtimes", "node", *args],
                          capture_output=True, text=True, timeout=timeout)


def collect(out: Path, target: int, per_query: int, min_sec: float, max_sec: float) -> None:
    from src.live.vod import scan_audio

    out.mkdir(parents=True, exist_ok=True)
    done = {p.parent.name for p in out.glob("*/features.json")}
    seen = set(done)
    per_channel = {}
    for f in out.glob("*/features.json"):
        try:
            ch = json.loads(f.read_text(encoding="utf-8")).get("channel") or "?"
        except ValueError:
            continue
        per_channel[ch] = per_channel.get(ch, 0) + 1
    print(f"collect: {len(done)} videos already in {out}", flush=True)
    for query in QUERIES:
        if len(done) >= target:
            break
        listing = _run(["--flat-playlist", "--print", "%(id)s|%(duration)s", f"ytsearch{per_query}:{query}"], timeout=120)
        for line in listing.stdout.splitlines():
            if len(done) >= target:
                break
            vid, _, dur = line.partition("|")
            if not vid or vid in seen:
                continue
            seen.add(vid)
            try:
                d = float(dur)
            except ValueError:
                continue
            if not (min_sec <= d <= max_sec):
                continue
            info = _run(["--skip-download", "-J", f"https://www.youtube.com/watch?v={vid}"], timeout=120)
            try:
                meta = json.loads(info.stdout)
            except ValueError:
                continue
            if not isinstance(meta, dict):
                continue  # yt-dlp answered "null" (private, removed or region-locked)
            heat = meta.get("heatmap") or []
            if len(heat) < 50:
                continue
            channel = meta.get("channel") or "?"
            if per_channel.get(channel, 0) >= MAX_PER_CHANNEL:
                continue  # variety: one streamer must not dominate the training
            folder = out / vid
            folder.mkdir(exist_ok=True)
            t0 = time.time()
            dl = _run(["-f", "bestaudio[abr<=160]/bestaudio/ba", "-N", "8", "-o", str(folder / "audio.%(ext)s"),
                       f"https://www.youtube.com/watch?v={vid}"], timeout=1800)
            audio = next((p for p in folder.glob("audio.*") if p.suffix not in (".part", ".ytdl")), None)
            if dl.returncode != 0 or audio is None:
                shutil.rmtree(folder, ignore_errors=True)
                continue
            series = scan_audio(audio, float(meta.get("duration") or d), workers=6)
            (folder / "features.json").write_text(json.dumps({
                "id": vid, "title": meta.get("title"), "channel": meta.get("channel"), "query": query,
                "duration": meta.get("duration"), "view_count": meta.get("view_count"), "heatmap": heat,
                "series": {k: [round(x, 4) for x in v] for k, v in series.items()}}), encoding="utf-8")
            audio.unlink(missing_ok=True)
            done.add(vid)
            per_channel[channel] = per_channel.get(channel, 0) + 1
            print(f"[{len(done)}/{target}] {vid} {d / 3600:.1f} h '{(meta.get('title') or '')[:60]}' "
                  f"({query}) in {time.time() - t0:.0f}s", flush=True)
    print(f"collect: {len(done)} videos", flush=True)


# --------------------------------------------------------------------------
# Training
# --------------------------------------------------------------------------

SPACE = {
    "baseline_sec": [120.0, 180.0, 240.0, 360.0],
    "active_z": (1.8, 4.5), "need_active": [1.0, 2.0], "laugh_z": (2.5, 8.0),
    "scream_f0_z": (2.5, 6.0), "scream_vocal_z": (0.3, 3.0), "merge_gap": (1.0, 8.0),
    "talk_base": (0.1, 1.2), "talk_w": (0.0, 2.0), "length_w": (0.0, 1.5), "energy_w": (0.0, 3.0),
    "min_gap": [60.0, 90.0, 150.0, 240.0],
}


def _heat_rank(heatmap):
    values = [float(h.get("value", 0)) for h in heatmap]
    order = sorted(range(len(values)), key=lambda i: values[i])
    ranks = [0.0] * len(values)
    for r, i in enumerate(order):
        ranks[i] = r / max(1, len(values) - 1)
    return [(float(h["start_time"]), float(h["end_time"]), ranks[i]) for i, h in enumerate(heatmap)]


def _rank_at(buckets, t):
    for s, e, r in buckets:
        if s <= t < e:
            return r
    return 0.5


def load(data: Path):
    videos = []
    for f in sorted(data.glob("*/features.json")):
        d = json.loads(f.read_text(encoding="utf-8"))
        d["buckets"] = _heat_rank(d["heatmap"])
        d["k"] = max(5, min(15, int(float(d["duration"]) / 600)))
        videos.append(d)
    return videos


def score_video(video, params, zcache):
    from src.live.selection import rank_moments, spaced

    key = int(params["baseline_sec"])
    z = zcache[(video["id"], key)]
    picks = spaced(rank_moments(video["series"], params, z=z), video["k"], params["min_gap"])
    if not picks:
        return 0.0
    # The clip starts a few seconds before the reaction and runs ~30 s: score
    # the heat where the clip plays (onset .. onset+20 s, sampled).
    vals = [sum(_rank_at(video["buckets"], m["onset"] + d) for d in (0, 10, 20)) / 3 for m in picks]
    return sum(vals) / len(picks) * min(1.0, len(picks) / video["k"])


def evaluate(videos, params, zcache):
    return sum(score_video(v, params, zcache) for v in videos) / max(1, len(videos))


def propose(best, rng, step):
    cand = dict(best)
    for name in rng.sample(list(SPACE), k=rng.choice([1, 1, 2, 3])):
        space = SPACE[name]
        if isinstance(space, list):
            cand[name] = rng.choice(space)
        else:
            lo, hi = space
            if rng.random() < 0.3:
                cand[name] = rng.uniform(lo, hi)
            else:
                cand[name] = min(hi, max(lo, best[name] + rng.gauss(0, (hi - lo) * step)))
    return cand


def train(data: Path, minutes: float, seed: int) -> None:
    from src.live.selection import DEFAULT_PARAMS, PARAMS_FILE, zscores

    videos = load(data)
    if len(videos) < 6:
        sys.exit(f"need at least 6 videos with features, found {len(videos)}")
    split = lambda v: int(hashlib.sha256(v["id"].encode()).hexdigest(), 16) % 10 < 7
    tr, va = [v for v in videos if split(v)], [v for v in videos if not split(v)]
    print(f"train {len(tr)} videos / held-out {len(va)} videos", flush=True)
    t0 = time.time()
    zcache = {}
    for v in videos:
        for b in SPACE["baseline_sec"]:
            zcache[(v["id"], int(b))] = zscores(v["series"], dict(DEFAULT_PARAMS, baseline_sec=b))
    print(f"z-scores precomputed in {time.time() - t0:.0f}s", flush=True)
    base_tr, base_va = evaluate(tr, DEFAULT_PARAMS, zcache), evaluate(va, DEFAULT_PARAMS, zcache)
    print(f"shipped defaults: train {base_tr:.4f}  held-out {base_va:.4f}  (random = ~0.50)", flush=True)
    rng = random.Random(seed)
    best, best_tr = dict(DEFAULT_PARAMS), base_tr
    log = [{"step": 0, "train": base_tr, "heldout": base_va, "params": best}]
    deadline, step, i, kept = time.time() + minutes * 60, 0.25, 0, 0
    while time.time() < deadline:
        i += 1
        cand = propose(best, rng, step)
        s = evaluate(tr, cand, zcache)
        if s > best_tr + 1e-4:
            best, best_tr, kept = cand, s, kept + 1
            va_s = evaluate(va, best, zcache)
            log.append({"step": i, "train": s, "heldout": va_s, "params": best})
            print(f"#{i}: kept (train {s:.4f}, held-out {va_s:.4f})", flush=True)
        if i % 200 == 0:
            step = max(0.05, step * 0.85)
            print(f"#{i}: {kept} kept, best train {best_tr:.4f}, {int((deadline - time.time()) / 60)} min left", flush=True)
    best_va = evaluate(va, best, zcache)
    report = {"videos": len(videos), "train_videos": [v["id"] for v in tr], "heldout_videos": [v["id"] for v in va],
              "experiments": i, "kept": kept, "defaults": {"train": base_tr, "heldout": base_va},
              "trained": {"train": best_tr, "heldout": best_va}, "params": best, "log": log}
    (data / "training_report.json").write_text(json.dumps(report, indent=1), encoding="utf-8")
    gain = (best_va - base_va) / max(base_va, 1e-6)
    print(f"done: {i} experiments, {kept} kept. held-out {base_va:.4f} -> {best_va:.4f} ({gain * 100:+.1f}%)", flush=True)
    if gain >= 0.02:
        PARAMS_FILE.write_text(json.dumps({"trained_on": len(tr), "heldout": len(va), "heldout_score": best_va,
                                           "defaults_heldout_score": base_va, "params": best}, indent=1), encoding="utf-8")
        print(f"wrote {PARAMS_FILE}", flush=True)
    else:
        print("held-out did not improve by 2%: params NOT written (defaults stay)", flush=True)


def main() -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")  # titles with emoji on a Windows console
        except Exception:
            pass
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("collect")
    c.add_argument("--out", required=True)
    c.add_argument("--target", type=int, default=40)
    c.add_argument("--per-query", type=int, default=12)
    c.add_argument("--min-sec", type=float, default=2400)
    c.add_argument("--max-sec", type=float, default=6 * 3600)
    t = sub.add_parser("train")
    t.add_argument("--data", required=True)
    t.add_argument("--minutes", type=float, default=90)
    t.add_argument("--seed", type=int, default=7)
    a = ap.parse_args()
    if a.cmd == "collect":
        collect(Path(a.out), a.target, a.per_query, a.min_sec, a.max_sec)
    else:
        train(Path(a.data), a.minutes, a.seed)
    return 0


if __name__ == "__main__":
    sys.exit(main())
