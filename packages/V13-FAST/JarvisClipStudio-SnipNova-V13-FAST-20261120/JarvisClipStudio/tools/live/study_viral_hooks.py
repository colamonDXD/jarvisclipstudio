"""Study the first seconds of viral streamer Shorts (10M+ views) vs our clips.

  python tools/live/study_viral_hooks.py collect --out DIR [--min-views 10000000] [--target 80]
  python tools/live/study_viral_hooks.py measure --dir DIR/videos --out DIR/viral.json
  python tools/live/study_viral_hooks.py measure --dir OUR_CLIPS --out DIR/ours.json
  python tools/live/study_viral_hooks.py compare DIR/viral.json DIR/ours.json --out DIR/HOOK_STUDY.md

Run inside the app container (ffmpeg, yt-dlp, YuNet, faster-whisper, and the
LLM gateway are there). Videos are analysed locally and are never
redistributed; ``collect`` keeps only the first 10 s of each, at 720p max.

Per clip, over the first 6 s (the hook):
- sound_start_s: first 50 ms frame above -35 dBFS (silence before the hook)
- speech_start_s / words_per_s_first6 / hook_text: faster-whisper on the first 8 s
- hit_at_start: a sharp transient (peak/RMS) in the first 0.4 s (SFX hit, bass drop, scream)
- loud_start_db: loudness of the first second minus the clip's median
- cuts_first6 / first_cut_s: scene changes (ffmpeg scene score > 0.3)
- motion_first2: mean frame difference over the first 2 s (zooms/shakes/fast action)
- text_frame0: share of near-white/yellow pixels with dark outline in the top 45% at t=0.2 s (a hook title)
- face_frame0 / face_size: YuNet face at t=0.5 s and its height share of the frame
- stacked: a strong horizontal divider at 35-60% height (face-on-top layouts)
- duration_s, views
- hook_type: the LLM's label for the opening line (question / shock-statement / mid-action / reaction /
  setup / reveal / other) when the gateway is reachable
"""
from __future__ import annotations

import argparse
import json
import os
import re
import statistics
import subprocess
import sys
import tempfile
from pathlib import Path

NICHES = {
    "gaming-streamer": ["kai cenat shorts", "ishowspeed shorts", "xqc shorts", "streamer rage #shorts", "jynxzi shorts"],
    "podcast": ["podcast clip #shorts", "joe rogan shorts", "andrew schulz shorts", "flagrant podcast shorts"],
    "comedy": ["stand up comedy #shorts", "funny skit #shorts", "comedy sketch shorts"],
    "dark-humor": ["dark humor #shorts", "dark joke comedy #shorts", "dark comedy stand up shorts"],
    "challenge": ["mrbeast shorts", "challenge #shorts", "sidemen shorts"],
    "prank": ["prank #shorts", "public prank shorts"],
    "fitness": ["gym motivation #shorts", "fitness shorts", "gym funny #shorts"],
    "finance-business": ["business advice #shorts", "money tips #shorts", "entrepreneur podcast #shorts"],
    "motivation": ["motivational speech #shorts", "motivation shorts"],
    "food": ["cooking #shorts", "street food #shorts", "food review shorts"],
    "travel": ["travel #shorts", "travel vlog shorts"],
    "sports": ["football skills #shorts", "basketball #shorts", "sports moments shorts"],
    "facts-education": ["did you know #shorts", "facts #shorts", "science shorts"],
    "reaction": ["reaction #shorts", "reacts shorts"],
    "edit-movie-anime": ["movie edit #shorts", "anime edit #shorts", "movie scene shorts"],
    "india": ["mythpat shorts", "carryminati shorts", "techno gamerz shorts", "hindi comedy #shorts"],
}
# Shorts tabs of big creators (their listings carry view counts).
CHANNELS = {
    # Big Shorts creators per niche: one request per Shorts tab returns ~30 Shorts WITH view
    # counts (search listings do not, and probing each video gets the IP bot-checked).
    "challenge": ["@MrBeast", "@Sidemen"], "gaming-streamer": ["@IShowSpeed", "@Jynxzi", "@CaseOh"],
    "comedy": ["@ZachKing", "@DanielLaBelle", "@KhabyLame"], "dark-humor": ["@MarkAngelComedy", "@jidionpremium"],
    "podcast": ["@JREClips", "@flagrant2"], "prank": ["@DharMann", "@Yes_Theory"],
    "fitness": ["@LarryWheels", "@SamSulek"], "finance-business": ["@AlexHormozi", "@GrahamStephan"],
    "motivation": ["@AlexHormozi", "@thedankoe"], "food": ["@nickdigiovanni", "@GugaFoods"],
    "travel": ["@drewbinsky", "@YesTheory"], "sports": ["@HoHighlights", "@NBA"],
    "facts-education": ["@zackdfilms", "@kurzgesagt"], "reaction": ["@KSI", "@Sidemen"],
    "edit-movie-anime": ["@Netflix", "@crunchyroll"], "india": ["@CarryMinati", "@TechnoGamerzOfficial", "@triggeredinsaan"],
}
for niche, chans in CHANNELS.items():
    NICHES[niche] = [f"channel:{c}" for c in chans]  # no per-video probing: Shorts tabs only
QUERIES = [q for qs in NICHES.values() for q in qs]
NICHE_OF = {q: n for n, qs in NICHES.items() for q in qs}


def ytdlp(*args, timeout=300):
    exe = os.getenv("YTDLP_BIN") or "yt-dlp"
    return subprocess.run([exe, "--no-warnings", "-q", *args], capture_output=True, text=True, timeout=timeout)


def collect(out: Path, min_views: int, target: int, per_query: int, per_niche: int = 4) -> None:
    videos = out / "videos"
    videos.mkdir(parents=True, exist_ok=True)
    meta_path = out / "collected.json"
    meta = json.loads(meta_path.read_text(encoding="utf-8")) if meta_path.exists() else {}
    niche_count = {}
    for m in meta.values():
        niche_count[m.get("niche")] = niche_count.get(m.get("niche"), 0) + 1
    for q in QUERIES:
        if len(meta) >= target:
            break
        if niche_count.get(NICHE_OF[q], 0) >= per_niche:
            continue
        source = (f"https://www.youtube.com/{q[8:]}/shorts" if q.startswith("channel:") else f"ytsearch{per_query}:{q}")
        depth = 300 if q.startswith("channel:") else per_query
        listing = ytdlp("--flat-playlist", "--playlist-end", str(depth), "--print",
                        "%(id)s|%(view_count)s|%(duration)s|%(channel)s", source, timeout=300)
        lines = listing.stdout.splitlines()
        if q.startswith("channel:"):
            # Shorts tabs list newest first; the 10M+ hits are older: most viewed first.
            def _views(line):
                try:
                    return -int(float(line.split("|")[1]))
                except (IndexError, ValueError):
                    return 0
            lines = sorted(lines, key=_views)
        for line in lines:
            if len(meta) >= target:
                break
            parts = line.split("|")
            if len(parts) < 4 or parts[0] in meta:
                continue
            vid, views, dur, channel = parts[0], parts[1], parts[2], parts[3]
            if channel in ("NA", "") and q.startswith("channel:"):
                channel = q[8:]
            if (views in ("NA", "") or dur in ("NA", "")) and os.getenv("STUDY_PROBE") == "1":
                # Search listings carry no view counts: ask for this one video.
                probe = ytdlp("--skip-download", "--print", "%(view_count)s|%(duration)s|%(channel)s",
                              f"https://www.youtube.com/watch?v={vid}", timeout=60)
                got = (probe.stdout.strip().splitlines() or ["NA|NA|NA"])[-1].split("|")
                if len(got) >= 3:
                    views, dur, channel = got[0], got[1], got[2]
            try:
                views_i = int(float(views))
                dur_f = float(dur) if dur not in ("NA", "") else 45.0
            except ValueError:
                continue
            if views_i < min_views or dur_f > 75 or dur_f < 8:
                continue
            if niche_count.get(NICHE_OF[q], 0) >= per_niche:
                break
            import time as _t
            _t.sleep(2.0)
            path = videos / f"{vid}.mp4"
            # The whole Short (<= 75 s) at 480p: enough to measure edits, sound and colour.
            r = ytdlp("-f", "bv*[height<=480]+ba/b[height<=480]/b", "--merge-output-format", "mp4",
                      "-o", str(path), f"https://www.youtube.com/watch?v={vid}")
            if r.returncode != 0 or not path.exists():
                continue
            meta[vid] = {"views": views_i, "duration_s": dur_f, "channel": channel, "query": q, "niche": NICHE_OF[q]}
            niche_count[NICHE_OF[q]] = niche_count.get(NICHE_OF[q], 0) + 1
            meta_path.write_text(json.dumps(meta, indent=1), encoding="utf-8")
            print(f"[{len(meta)}/{target}] {vid} {views_i / 1e6:.1f}M views {dur_f:.0f}s {channel}", flush=True)
    print(f"collected {len(meta)}", flush=True)


# --------------------------------------------------------------------------

def _pcm(path, sr=16000, seconds=8.0):
    import numpy as np

    raw = subprocess.run(["ffmpeg", "-v", "error", "-t", str(seconds), "-i", str(path), "-vn", "-ac", "1", "-ar", str(sr),
                          "-f", "s16le", "-"], capture_output=True).stdout
    return np.frombuffer(raw, np.int16).astype(np.float32) / 32768.0


def _frames(path, t0, t1, fps=8, w=160, h=284):
    import numpy as np

    raw = subprocess.run(["ffmpeg", "-v", "error", "-ss", str(t0), "-t", str(t1 - t0), "-i", str(path),
                          "-vf", f"fps={fps},scale={w}:{h},format=gray", "-f", "rawvideo", "-"], capture_output=True).stdout
    n = len(raw) // (w * h)
    return np.frombuffer(raw[: n * w * h], np.uint8).reshape(n, h, w).astype(np.float32)


def _frame_bgr(path, t, w=360, h=640):
    import numpy as np

    raw = subprocess.run(["ffmpeg", "-v", "error", "-ss", str(t), "-i", str(path), "-frames:v", "1",
                          "-vf", f"scale={w}:{h}", "-f", "rawvideo", "-pix_fmt", "bgr24", "-"], capture_output=True).stdout
    if len(raw) < w * h * 3:
        return None
    return np.frombuffer(raw[: w * h * 3], np.uint8).reshape(h, w, 3)


_WHISPER = None


def _transcribe(path):
    global _WHISPER
    from faster_whisper import WhisperModel

    if _WHISPER is None:
        _WHISPER = WhisperModel("small", device="cpu", compute_type="int8")
    with tempfile.TemporaryDirectory() as tmp:
        wav = Path(tmp) / "a.wav"
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-t", "8", "-i", str(path), "-vn", "-ac", "1", "-ar", "16000", str(wav)])
        segments, _ = _WHISPER.transcribe(str(wav), word_timestamps=True, vad_filter=False)
        words = [w for s in segments for w in (s.words or [])]
    return words


def measure_one(path: Path) -> dict:
    import cv2
    import numpy as np

    m: dict = {"file": path.name}
    dur = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)],
                         capture_output=True, text=True).stdout.strip()
    m["file_duration_s"] = float(dur or 0)
    a = _pcm(path)
    if len(a):
        hop = 800  # 50 ms
        rms = np.array([np.sqrt(np.mean(a[i:i + hop] ** 2) + 1e-12) for i in range(0, len(a) - hop, hop)])
        db = 20 * np.log10(rms + 1e-9)
        above = np.flatnonzero(db > -35)
        m["sound_start_s"] = round(float(above[0]) * 0.05, 2) if len(above) else None
        first = a[: int(0.4 * 16000)]
        m["hit_at_start"] = bool(len(first) and (np.max(np.abs(first)) / (np.sqrt(np.mean(first ** 2)) + 1e-9)) > 6.0
                                 and np.max(np.abs(first)) > 0.3)
        med = float(np.median(db)) if len(db) else -60
        m["loud_start_db"] = round(float(np.mean(db[:20])) - med, 1) if len(db) >= 20 else None
    try:
        words = _transcribe(path)
        m["speech_start_s"] = round(float(words[0].start), 2) if words else None
        first6 = [w for w in words if w.start < 6.0]
        m["words_per_s_first6"] = round(len(first6) / 6.0, 2)
        m["hook_text"] = " ".join(w.word.strip() for w in first6)[:200]
    except Exception as exc:
        m["speech_start_s"], m["hook_text"] = None, f"(transcribe failed: {exc})"
    sc = subprocess.run(["ffmpeg", "-v", "info", "-t", "6", "-i", str(path), "-vf", "select='gt(scene,0.3)',showinfo",
                         "-f", "null", "-"], capture_output=True, text=True).stderr
    cuts = [float(x) for x in re.findall(r"pts_time:([0-9.]+)", sc)]
    m["cuts_first6"] = len(cuts)
    m["first_cut_s"] = round(cuts[0], 2) if cuts else None
    f = _frames(path, 0, 2.0)
    m["motion_first2"] = round(float(np.mean(np.abs(np.diff(f, axis=0)))), 2) if len(f) > 2 else None
    total = m["file_duration_s"] or 0
    if total > 1:
        # Whole clip: transitions, black "beats", sound hits, colour.
        sc_all = subprocess.run(["ffmpeg", "-v", "info", "-i", str(path), "-vf", "select='gt(scene,0.3)',showinfo",
                                 "-f", "null", "-"], capture_output=True, text=True).stderr
        m["cuts_per_min"] = round(len(re.findall(r"pts_time:", sc_all)) / total * 60, 1)
        bd = subprocess.run(["ffmpeg", "-v", "info", "-i", str(path), "-vf", "blackdetect=d=0.12:pic_th=0.92",
                             "-an", "-f", "null", "-"], capture_output=True, text=True).stderr
        blacks = [(float(x), float(y)) for x, y in re.findall(r"black_start:([0-9.]+) black_end:([0-9.]+)", bd)]
        mid = [b for b in blacks if 0.5 < b[0] < total - 0.5]
        m["black_beats"] = len(mid)
        m["black_at"] = [round(b[0], 1) for b in mid][:5]
        full = _pcm(path, seconds=total)
        if len(full) > 1600:
            hop = 800
            env = np.array([np.sqrt(np.mean(full[i:i + hop] ** 2) + 1e-12) for i in range(0, len(full) - hop, hop)])
            rise = env[1:] / (env[:-1] + 1e-6)
            m["hits_per_min"] = round(int(np.sum((rise > 4.0) & (env[1:] > 0.08))) / total * 60, 1)
        frames = [_frame_bgr(path, t) for t in np.linspace(0.3, max(0.3, total - 0.3), 6)]
        frames = [f for f in frames if f is not None]
        if frames:
            hsv_all = np.stack([cv2.cvtColor(f, cv2.COLOR_BGR2HSV) for f in frames])
            m["saturation"] = round(float(hsv_all[..., 1].mean()), 1)
            m["brightness"] = round(float(hsv_all[..., 2].mean()), 1)
            caps = 0
            for f in frames:
                h2 = cv2.cvtColor(f, cv2.COLOR_BGR2HSV)
                band = h2[int(640 * 0.3): int(640 * 0.85)]
                bright = (band[..., 2] > 215) & (band[..., 1] < 70)
                dark = cv2.dilate((band[..., 2] < 70).astype(np.uint8), np.ones((3, 3), np.uint8)) > 0
                caps += int(float((bright & dark).mean()) * 1000 >= 1.5)
            m["captions_share"] = round(caps / len(frames), 2)
    frame = _frame_bgr(path, 0.2)
    if frame is not None:
        hsv = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)
        top = hsv[: int(640 * 0.45)]
        bright = ((top[..., 2] > 215) & (top[..., 1] < 60)) | ((top[..., 0] > 18) & (top[..., 0] < 35) & (top[..., 1] > 120) & (top[..., 2] > 200))
        dark = cv2.dilate((top[..., 2] < 70).astype(np.uint8), np.ones((3, 3), np.uint8)) > 0
        m["text_frame0"] = round(float((bright & dark).mean()) * 1000, 2)
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY).astype(np.float32)
        rows = gray[int(640 * 0.35): int(640 * 0.6)]
        jumps = np.abs(np.diff(rows.mean(axis=1)))
        m["stacked"] = bool(len(jumps) and float(np.max(jumps)) > 40 and float(np.std(rows[np.argmax(jumps)])) < 25)
    face_frame = _frame_bgr(path, 0.5)
    try:
        sys.path.insert(0, "/app")
        from src.media.face_detection import open_yunet_detector

        det = open_yunet_detector()
        faces = det.detect(face_frame) if (det is not None and face_frame is not None) else []
        m["face_frame0"] = bool(faces)
        if faces:
            box = faces[0]
            h = box[3] if isinstance(box, (list, tuple)) else getattr(box, "h", getattr(box, "height", 0))
            m["face_size"] = round(float(h) / 640.0, 3)
    except Exception as exc:
        m["face_frame0"] = None
    return m


HOOK_TYPES_PROMPT = """Label how each short-form clip OPENS (its first 6 seconds of speech) with ONE of:
question, shock-statement, mid-action, reaction, setup, reveal, other.
Answer ONLY JSON: {{"<file>": "<label>", ...}}
{items}"""


def label_hooks(rows):
    base = (os.getenv("OPENAI_BASE_URL") or "").rstrip("/")
    model = (os.getenv("LLM") or "").split(":", 1)[-1]
    if not base or not model:
        return
    import httpx

    items = "\n".join(f'{r["file"]}: "{r.get("hook_text", "")}"' for r in rows)
    try:
        resp = httpx.post(f"{base}/chat/completions", timeout=180,
                          headers={"Authorization": f"Bearer {os.getenv('OPENAI_API_KEY', '')}"},
                          json={"model": model, "temperature": 0,
                                "messages": [{"role": "user", "content": HOOK_TYPES_PROMPT.format(items=items)}]})
        text = resp.json()["choices"][0]["message"]["content"]
        labels = json.loads(re.search(r"\{.*\}", text, re.S).group(0))
        for r in rows:
            r["hook_type"] = labels.get(r["file"], "other")
    except Exception as exc:
        print(f"hook labelling skipped: {exc}", flush=True)


def measure(folder: Path, out: Path, meta_file: Path = None) -> None:
    meta = json.loads(meta_file.read_text(encoding="utf-8")) if meta_file and meta_file.exists() else {}
    rows = []
    for p in sorted(folder.glob("*.mp4")):
        try:
            row = measure_one(p)
        except Exception as exc:
            print(f"skip {p.name}: {exc}", flush=True)
            continue
        row.update(meta.get(p.stem, {}))
        rows.append(row)
        print(json.dumps({k: row.get(k) for k in ("file", "sound_start_s", "speech_start_s", "first_cut_s", "hit_at_start",
                                                  "text_frame0", "stacked")}), flush=True)
    for i in range(0, len(rows), 25):
        label_hooks(rows[i:i + 25])
    out.write_text(json.dumps(rows, indent=1), encoding="utf-8")
    print(f"measured {len(rows)} -> {out}", flush=True)


def _stats(rows, key, kind="num"):
    vals = [r.get(key) for r in rows if r.get(key) is not None]
    if not vals:
        return "n/a"
    if kind == "bool":
        return f"{100 * sum(1 for v in vals if v) / len(vals):.0f}%"
    if kind == "text":
        return f"{100 * sum(1 for v in vals if float(v) >= 1.5) / len(vals):.0f}% have a title"
    vals = [float(v) for v in vals]
    return f"median {statistics.median(vals):.2f} (p25 {sorted(vals)[len(vals) // 4]:.2f}, p75 {sorted(vals)[3 * len(vals) // 4]:.2f})"


def compare(viral_path: Path, ours_path: Path, out: Path) -> None:
    viral = json.loads(viral_path.read_text(encoding="utf-8"))
    ours = json.loads(ours_path.read_text(encoding="utf-8"))
    rows = [
        ("Sound starts at (s)", "sound_start_s", "num"),
        ("First word at (s)", "speech_start_s", "num"),
        ("Words per second, first 6 s", "words_per_s_first6", "num"),
        ("Hit/SFX in first 0.4 s", "hit_at_start", "bool"),
        ("First-second loudness vs rest (dB)", "loud_start_db", "num"),
        ("Cuts in first 6 s", "cuts_first6", "num"),
        ("First cut at (s)", "first_cut_s", "num"),
        ("Motion first 2 s", "motion_first2", "num"),
        ("Text hook on frame 1", "text_frame0", "text"),
        ("Face on frame 1", "face_frame0", "bool"),
        ("Face height share", "face_size", "num"),
        ("Face-on-top stack", "stacked", "bool"),
        ("Clip length (s)", "duration_s", "num"),
        ("Cuts per minute (whole clip)", "cuts_per_min", "num"),
        ("Black beats mid-clip (dark-humour cuts)", "black_beats", "num"),
        ("Sound hits per minute (SFX/drops)", "hits_per_min", "num"),
        ("Saturation (0-255)", "saturation", "num"),
        ("Brightness (0-255)", "brightness", "num"),
        ("Frames with captions", "captions_share", "num"),
    ]
    lines = [f"# Hook study: {len(viral)} viral Shorts (10M+ views) vs {len(ours)} SnipNova clips", "",
             "| First 6 seconds | Viral | Ours |", "|---|---|---|"]
    for label, key, kind in rows:
        ours_key = "file_duration_s" if key == "duration_s" else key
        lines.append(f"| {label} | {_stats(viral, key, kind)} | {_stats(ours, ours_key, kind)} |")
    for name, data in (("Viral", viral), ("Ours", ours)):
        types = {}
        for r in data:
            types[r.get("hook_type", "?")] = types.get(r.get("hook_type", "?"), 0) + 1
        lines.append(f"\n**{name} opening types:** " + ", ".join(f"{k} {v}" for k, v in sorted(types.items(), key=lambda x: -x[1])))
    lines.append("\n## Per niche (viral)\n")
    lines.append("| Niche | n | first word (s) | cuts/min | hits/min | black beats | saturation |")
    lines.append("|---|---|---|---|---|---|---|")
    for n in sorted({r.get("niche", "?") for r in viral}):
        g = [r for r in viral if r.get("niche", "?") == n]
        def med(k):
            vals = [float(r[k]) for r in g if r.get(k) is not None]
            return f"{statistics.median(vals):.1f}" if vals else "n/a"
        lines.append(f"| {n} | {len(g)} | {med('speech_start_s')} | {med('cuts_per_min')} | {med('hits_per_min')} | {med('black_beats')} | {med('saturation')} |")
    lines.append("\n## Viral openings (first 6 s of speech)\n")
    for r in sorted(viral, key=lambda r: -(r.get("views") or 0))[:25]:
        lines.append(f"- {(r.get('views') or 0) / 1e6:.0f}M · {r.get('channel', '')} · [{r.get('hook_type', '')}] \"{r.get('hook_text', '')}\"")
    lines.append("\n## Our openings\n")
    for r in ours:
        lines.append(f"- {r['file']} · [{r.get('hook_type', '')}] \"{r.get('hook_text', '')}\"")
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(out.read_text(encoding="utf-8"))


def main() -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("collect")
    c.add_argument("--out", required=True)
    c.add_argument("--min-views", type=int, default=10_000_000)
    c.add_argument("--target", type=int, default=80)
    c.add_argument("--per-query", type=int, default=25)
    c.add_argument("--per-niche", type=int, default=4)
    me = sub.add_parser("measure")
    me.add_argument("--dir", required=True)
    me.add_argument("--out", required=True)
    me.add_argument("--meta")
    co = sub.add_parser("compare")
    co.add_argument("viral")
    co.add_argument("ours")
    co.add_argument("--out", required=True)
    a = ap.parse_args()
    if a.cmd == "collect":
        collect(Path(a.out), a.min_views, a.target, a.per_query, a.per_niche)
    elif a.cmd == "measure":
        measure(Path(a.dir), Path(a.out), Path(a.meta) if a.meta else None)
    else:
        compare(Path(a.viral), Path(a.ours), Path(a.out))
    return 0


if __name__ == "__main__":
    sys.exit(main())
