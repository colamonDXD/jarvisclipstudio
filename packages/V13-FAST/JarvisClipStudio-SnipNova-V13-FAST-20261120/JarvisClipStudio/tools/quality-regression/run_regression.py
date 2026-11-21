#!/usr/bin/env python3
"""Permanent quality-regression harness (SnipNova quality milestone).

Renders the 5 fixed ranges from fixture.json through the pipeline code of a
CHOSEN worktree (milestone-2 for BEFORE, any later branch for AFTER) using the
same call sequence the worker uses (build_edl -> attach_reframe_plan ->
create_optimized_clip), then measures every export.

The harness is deliberately branch-agnostic: it imports `src.*` from the
--worktree you point it at and records that worktree's git commit in the run
manifest, so numbers from different versions are comparable.

Interpreter (dev sandbox): the lane venv has the backend deps + faster-whisper;
cv2 comes from the render-bench venv via PYTHONPATH (numpy versions match).

    RBSP=$(~/workspace/render-bench/venv/bin/python3 -c "import site; print(site.getsitepackages()[0])")
    PYTHONPATH=$RBSP ~/workspace/scratch-lane4/venv/bin/python \
        tools/quality-regression/run_regression.py \
        --worktree ~/workspace/jcs-m2 --label before-m2 \
        --source ~/workspace/qm-source/source.mp4

Outputs go to --outdir (default
~/workspace/research_notes/quality-milestone/runs/<label>/):
  <range_id>.mp4            the exported clip (a real deliverable, not a probe)
  <range_id>.json           measured facts for that export
  frames/<range_id>_*.png   opening-frame grabs
  report.md                 human-readable run summary with the numbers table
  manifest.json             fixture + git commit + profile + environment notes

What is NOT reproduced (honesty box, also printed into report.md):
  - LLM steps of the production pipeline (moment selection, clip plan,
    hook enforcement) are upstream of these known ranges and are skipped:
    the ranges, hook titles and profile come from fixture.json (the as-run
    values) or from --profile.
  - The production music library (user assets) is not present in the dev
    sandbox; if the profile's EDL requests a music bed that is missing on
    disk, the render proceeds without it, exactly as the renderer degrades.
  - Word timings come from a local faster-whisper run over just the 5
    ranges (model: $QM_WHISPER_MODEL, default "small"), seeded into the
    pipeline's own transcript cache file format next to the per-run source
    hardlink. Production used the user's configured provider; caption
    timing spot-checks are therefore indicative, and the full caption
    audit lives in workstream E.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

RANGES_PAD_SEC = 1.5  # audio padding for the A/V sync correlation window


def parse_ts(ts: str) -> float:
    parts = [float(p) for p in ts.split(":")]
    total = 0.0
    for p in parts:
        total = total * 60 + p
    return total


def sh(cmd: list[str], **kw) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True, **kw)


def ffprobe_json(path: Path) -> dict:
    r = sh([
        "ffprobe", "-v", "error", "-show_streams", "-show_format",
        "-of", "json", str(path),
    ])
    return json.loads(r.stdout or "{}")


def git_info(worktree: Path) -> dict:
    out = {}
    for key, args in (
        ("commit", ["rev-parse", "HEAD"]),
        ("branch", ["rev-parse", "--abbrev-ref", "HEAD"]),
    ):
        r = sh(["git", "-C", str(worktree), *args])
        out[key] = (r.stdout or "").strip()
    return out


# --------------------------------------------------------------------------
# Transcript seeding: faster-whisper over each range -> pipeline cache format
# --------------------------------------------------------------------------

def build_transcript_cache(source: Path, ranges: list[dict], work: Path) -> Path:
    """Write <source>.transcript_cache.json in the pipeline's own format.

    Words are stored in milliseconds with absolute source timestamps,
    exactly as media.transcription.cache_transcript_data would write them
    from a whisper result. Only the fixture ranges are transcribed.
    """
    cache_path = source.with_suffix(".transcript_cache.json")
    if cache_path.exists():
        return cache_path
    from faster_whisper import WhisperModel
    from src.media.common import TRANSCRIPT_CACHE_SCHEMA_VERSION

    model_name = os.environ.get("QM_WHISPER_MODEL", "small")
    model = WhisperModel(model_name, device="cpu", compute_type="int8")
    words: list[dict] = []
    utterances: list[dict] = []
    texts: list[str] = []
    for rng in ranges:
        start, end = rng["_start"], rng["_end"]
        audio = work / f"audio_{rng['id']}.wav"
        r = sh([
            "ffmpeg", "-y", "-v", "error", "-ss", str(start), "-t", str(end - start),
            "-i", str(source), "-vn", "-ac", "1", "-ar", "16000", str(audio),
        ])
        if r.returncode != 0:
            raise RuntimeError(f"audio extract failed for {rng['id']}: {r.stderr[-300:]}")
        segments, _info = model.transcribe(
            str(audio), word_timestamps=True, language="en"
        )
        for seg in segments:
            seg_words = []
            for w in seg.words or []:
                item = {
                    "text": w.word,
                    "start": int((start + w.start) * 1000),
                    "end": int((start + w.end) * 1000),
                    "confidence": w.probability,
                    "speaker": None,
                }
                seg_words.append(item)
                words.append(item)
            utterances.append({
                "text": seg.text,
                "start": int((start + seg.start) * 1000),
                "end": int((start + seg.end) * 1000),
                "speaker": None,
                "words": seg_words,
            })
            texts.append(seg.text)
    payload = {
        "version": TRANSCRIPT_CACHE_SCHEMA_VERSION,
        "words": words,
        "utterances": utterances,
        "text": " ".join(texts),
        "language_code": "en",
    }
    cache_path.write_text(json.dumps(payload))
    return cache_path


# --------------------------------------------------------------------------
# Measurements (every number in the report comes from here)
# --------------------------------------------------------------------------

def measure_subject_pct(video: Path, model_path: Path) -> dict:
    """Face-bbox area as % of output frame, via the repo's YuNet model."""
    import cv2
    import numpy as np

    cap = cv2.VideoCapture(str(video))
    if not cap.isOpened():
        return {"error": "cannot open export"}
    frame_count = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
    fps = float(cap.get(cv2.CAP_PROP_FPS) or 25.0)
    duration = frame_count / fps if fps else 0.0
    detector = cv2.FaceDetectorYN.create(
        str(model_path), "", (320, 320), score_threshold=0.6
    )
    samples = []
    n = 6
    for i in range(n):
        t = duration * (i + 0.5) / n if duration else 0.0
        cap.set(cv2.CAP_PROP_POS_MSEC, t * 1000)
        ok, frame = cap.read()
        if not ok or frame is None:
            continue
        h, w = frame.shape[:2]
        detector.setInputSize((w, h))
        _rc, faces = detector.detect(frame)
        boxes = []
        if faces is not None:
            for f in faces:
                x, y, bw, bh = (float(f[0]), float(f[1]), float(f[2]), float(f[3]))
                boxes.append((x, y, x + bw, y + bh, float(f[-1])))
        if boxes:
            ux0 = min(b[0] for b in boxes); uy0 = min(b[1] for b in boxes)
            ux1 = max(b[2] for b in boxes); uy1 = max(b[3] for b in boxes)
            union_pct = 100.0 * (ux1 - ux0) * (uy1 - uy0) / (w * h)
            largest_pct = 100.0 * max(
                (b[2] - b[0]) * (b[3] - b[1]) for b in boxes
            ) / (w * h)
        else:
            union_pct, largest_pct = 0.0, 0.0
        samples.append({
            "t": round(t, 2), "faces": len(boxes),
            "union_pct": round(union_pct, 2), "largest_pct": round(largest_pct, 2),
        })
    cap.release()
    if samples:
        mean_union = sum(s["union_pct"] for s in samples) / len(samples)
        mean_largest = sum(s["largest_pct"] for s in samples) / len(samples)
    else:
        mean_union = mean_largest = 0.0
    return {
        "samples": samples,
        "mean_union_faces_pct": round(mean_union, 2),
        "mean_largest_face_pct": round(mean_largest, 2),
    }


def grab_frames(video: Path, outdir: Path, range_id: str) -> list[str]:
    outdir.mkdir(parents=True, exist_ok=True)
    paths = []
    for tag, t in (("open0", 0.1), ("open1", 0.5), ("open2", 1.0), ("mid", None)):
        name = f"{range_id}_{tag}.png"
        dest = outdir / name
        if t is None:
            probe = ffprobe_json(video)
            dur = float(probe.get("format", {}).get("duration") or 0)
            t = dur / 2 if dur else 0.0
        r = sh([
            "ffmpeg", "-y", "-v", "error", "-ss", str(t), "-i", str(video),
            "-frames:v", "1", str(dest),
        ])
        if r.returncode == 0 and dest.exists():
            paths.append(str(dest))
    return paths


def measure_av_sync(export: Path, source: Path, start: float, end: float, work: Path, range_id: str) -> dict:
    """Cross-correlate export audio against the source range audio.

    Returns the estimated offset in ms (export audio relative to source:
    positive = export audio lags the source timeline) and the normalised
    correlation peak (low peak = weak evidence, treat offset with care).
    """
    import numpy as np

    def decode(path: Path, ss: float | None, t: float | None, dest: Path) -> np.ndarray:
        cmd = ["ffmpeg", "-y", "-v", "error"]
        if ss is not None:
            cmd += ["-ss", str(ss)]
        cmd += ["-i", str(path)]
        if t is not None:
            cmd += ["-t", str(t)]
        cmd += ["-vn", "-ac", "1", "-ar", "4000", "-f", "f32le", str(dest)]
        r = sh(cmd)
        if r.returncode != 0 or not dest.exists():
            return np.zeros(0, dtype=np.float32)
        return np.fromfile(dest, dtype=np.float32)

    exp = decode(export, None, None, work / f"sync_exp_{range_id}.raw")
    src = decode(source, max(0.0, start - RANGES_PAD_SEC), (end - start) + 2 * RANGES_PAD_SEC, work / f"sync_src_{range_id}.raw")
    if exp.size == 0 or src.size == 0:
        return {"error": "audio decode failed"}
    rate = 4000
    # Envelope at 100 Hz for robustness and speed.
    win = rate // 100
    def envelope(x: np.ndarray) -> np.ndarray:
        n = (x.size // win) * win
        return np.abs(x[:n].reshape(-1, win)).mean(axis=1)
    e, s = envelope(exp), envelope(src)
    if e.size == 0 or s.size == 0:
        return {"error": "envelope failed"}
    e = e - e.mean(); s = s - s.mean()
    corr = np.correlate(s, e, mode="full")
    lags = np.arange(-(e.size - 1), s.size)  # in 10ms steps, src index - exp index
    k = int(np.argmax(corr))
    lag_steps = int(lags[k])
    denom = float(np.sqrt((s ** 2).sum() * (e ** 2).sum()) or 1.0)
    peak = float(corr[k] / denom)
    # lag_steps>0 means the export's audio starts lag_steps into the source
    # window; the source window began RANGES_PAD_SEC before the range start,
    # so a perfectly synced export has lag == RANGES_PAD_SEC.
    offset_ms = (lag_steps * 10) - int(RANGES_PAD_SEC * 1000)
    return {
        "offset_ms": int(offset_ms),
        "correlation_peak": round(peak, 3),
        "note": "offset>0: export audio lags source; measured on envelopes at 100 Hz, resolution ±10 ms",
    }


def summarise_reframe(edl: dict) -> dict:
    plan = edl.get("reframe") or {}
    shots = []
    for shot in plan.get("shots") or []:
        shots.append({
            "start": shot.get("start"), "end": shot.get("end"),
            "mode": shot.get("mode"), "reason": shot.get("reason"),
        })
    seg_modes = []
    for seg in edl.get("segments") or []:
        rf = seg.get("reframe") or {}
        modes = []
        if rf.get("crop"):
            modes.append("crop")
        if rf.get("fit_layers"):
            modes.append("fit")
        if rf.get("split_layers"):
            modes.append("split")
        seg_modes.append({
            "start": seg.get("start"), "end": seg.get("end"),
            "transition_in": seg.get("transition_in"), "modes": modes,
        })
    return {"delegate": plan.get("delegate"), "shots": shots, "segments": seg_modes}


# --------------------------------------------------------------------------
# Main
# --------------------------------------------------------------------------

def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--worktree", required=True, help="checkout whose backend code renders")
    ap.add_argument("--label", required=True, help="run label, e.g. before-m2")
    ap.add_argument("--source", required=True, help="downloaded source mp4")
    ap.add_argument("--profile", default=None, help="profile override (default: fixture as-run profile)")
    ap.add_argument("--outdir", default=None)
    ap.add_argument("--only", default=None, help="comma range ids to render, default all")
    ap.add_argument("--cache-only", action="store_true",
                    help="only seed the transcript cache (whisper-only process), then exit")
    args = ap.parse_args()

    worktree = Path(args.worktree).expanduser().resolve()
    backend = worktree / "backend"
    sys.path.insert(0, str(backend))
    os.chdir(backend)

    fixture_path = Path(__file__).with_name("fixture.json")
    fixture = json.loads(fixture_path.read_text())
    profile_name = args.profile or fixture["default_profile"]
    source_orig = Path(args.source).expanduser().resolve()
    if not source_orig.exists():
        print(f"source not found: {source_orig}", file=sys.stderr)
        return 2

    outdir = Path(args.outdir) if args.outdir else (
        Path.home() / "workspace" / "research_notes" / "quality-milestone" / "runs" / args.label
    )
    outdir.mkdir(parents=True, exist_ok=True)
    work = outdir / "_work"
    work.mkdir(exist_ok=True)

    # Per-run hardlink of the source so each version's caches (transcript,
    # reframe analysis) stay separate and version-true.
    source = work / f"source-{args.label}.mp4"
    if not source.exists():
        try:
            os.link(source_orig, source)
        except OSError:
            shutil.copy2(source_orig, source)

    ranges = []
    for rng in fixture["ranges"]:
        item = dict(rng)
        item["_start"] = parse_ts(rng["start"])
        item["_end"] = parse_ts(rng["end"])
        ranges.append(item)
    ranges_all = list(ranges)
    if args.only:
        wanted = {x.strip() for x in args.only.split(",")}
        ranges = [r for r in ranges if r["id"] in wanted]

    build_transcript_cache(source, ranges, work)
    if args.cache_only:
        print(f"transcript cache seeded for {args.label}: {len(ranges)} range(s)")
        return 0

    # Pipeline imports (from the chosen worktree's code).
    from src.auto_director.edl import build_edl, load_profile
    from src.media.captions import caption_hold_cap_for_profile
    from src.media.reframe_plan import attach_reframe_plan
    from src.media.timeline import get_words_in_range
    from src.media.transcription import load_cached_transcript_data
    from src.video_utils import create_optimized_clip

    profile = load_profile(profile_name)
    hold_cap = caption_hold_cap_for_profile(profile)
    yunet = backend / "src" / "media" / "models" / "face_detection_yunet_2023mar.onnx"

    manifest = {
        "fixture_id": fixture["fixture_id"],
        "label": args.label,
        "worktree": str(worktree),
        "git": git_info(worktree),
        "profile": profile_name,
        "caption_hold_cap_sec": hold_cap,
        "whisper_model": os.environ.get("QM_WHISPER_MODEL", "small"),
        "honesty_notes": [
            "LLM pipeline steps (selection, clip plan, hook enforcement) not reproduced; ranges/titles/profile are the as-run fixture values.",
            "Production music assets are absent in the dev sandbox; missing beds degrade silently in the renderer, as in production.",
            "Word timings: local faster-whisper over the 5 ranges only, seeded into the pipeline cache format.",
        ],
    }
    (outdir / "manifest.json").write_text(json.dumps(manifest, indent=2))

    transcript_data = load_cached_transcript_data(source) or {}
    rows = []
    for rng in ranges:
        start, end = rng["_start"], rng["_end"]
        # Mirror the production seam (video_service._build_pipeline_edl):
        # clip-relative words from the cached transcript, normalised to
        # {"word","start","end"}, build_edl with empty energy peaks.
        rel_words = get_words_in_range(transcript_data, start, end)
        words = [
            {"word": w.get("text", ""), "start": w.get("start", 0.0), "end": w.get("end", 0.0)}
            for w in rel_words
        ]
        clip = {"id": rng["id"], "start": start, "end": end, "duration": end - start}
        edl = build_edl(clip, words, profile, [], beat_grid=None)
        attach_reframe_plan(edl, source, [(start, end)])
        out_mp4 = outdir / f"{rng['id']}.mp4"
        t0 = time.time()
        ok = create_optimized_clip(
            source, start, end, out_mp4,
            add_subtitles=True,
            keep_ranges=[(start, end)],
            hook_title=rng.get("hook_title"),
            extend_to_sentence=False,
            edl=edl,
            profile_name=profile_name,
            max_caption_seconds=hold_cap,
        )
        render_sec = round(time.time() - t0, 1)
        record: dict = {
            "range_id": rng["id"], "start": start, "end": end,
            "hook_title": rng.get("hook_title"), "render_ok": bool(ok),
            "render_seconds": render_sec,
            "words_in_range": len(words),
            "edl_cut_list": [
                {"start": s.get("start"), "end": s.get("end"),
                 "transition_in": s.get("transition_in")}
                for s in edl.get("segments") or []
            ],
            "reframe": summarise_reframe(edl),
        }
        if ok and out_mp4.exists():
            probe = ffprobe_json(out_mp4)
            vstream = next(
                (s for s in probe.get("streams", []) if s.get("codec_type") == "video"),
                {},
            )
            record["specs"] = {
                "codec": vstream.get("codec_name"),
                "width": vstream.get("width"), "height": vstream.get("height"),
                "fps": vstream.get("avg_frame_rate"),
                "duration_sec": round(float(probe.get("format", {}).get("duration") or 0), 2),
                "size_bytes": out_mp4.stat().st_size,
            }
            record["subject"] = measure_subject_pct(out_mp4, yunet)
            record["av_sync"] = measure_av_sync(out_mp4, source_orig, start, end, work, rng["id"])
            record["frames"] = grab_frames(out_mp4, outdir / "frames", rng["id"])
        (outdir / f"{rng['id']}.json").write_text(json.dumps(record, indent=2))
        rows.append(record)
        print(f"[{args.label}] {rng['id']}: ok={ok} render={render_sec}s "
              f"subject={record.get('subject', {}).get('mean_union_faces_pct')}")

    # Resumability: fold in per-range records from earlier partial
    # invocations under the same label (same outdir), so an interrupted
    # run (e.g. sandbox OOM) can be finished range-by-range without
    # re-rendering ranges that already completed.
    rendered_ids = {r["range_id"] for r in rows}
    report_rows = list(rows)
    resumed_ids = []
    for rng in ranges_all:
        if rng["id"] in rendered_ids:
            continue
        prior = outdir / f"{rng['id']}.json"
        if prior.exists():
            try:
                report_rows.append(json.loads(prior.read_text()))
                resumed_ids.append(rng["id"])
            except (ValueError, OSError):
                pass
    order = {rng["id"]: i for i, rng in enumerate(ranges_all)}
    report_rows.sort(key=lambda r: order.get(r["range_id"], 99))

    lines = [
        f"# Regression run: {args.label}",
        "",
        f"- Fixture: {fixture['fixture_id']} (permanent — see tools/quality-regression/fixture.json)",
        f"- Code: {manifest['git']['branch']} @ {manifest['git']['commit'][:12]} ({worktree})",
        f"- Profile: {profile_name} · caption hold cap: {hold_cap}s · whisper: {manifest['whisper_model']}",
        f"- Ranges rendered this invocation: {', '.join(sorted(rendered_ids)) or 'none'}"
        + (f" · resumed from an earlier partial run: {', '.join(resumed_ids)}" if resumed_ids else ""),
        "",
        "## Honesty notes",
        *[f"- {n}" for n in manifest["honesty_notes"]],
        "",
        "## Results",
        "",
        "| Range | Render | Sec | Subject union % | Largest face % | A/V offset ms (peak) | Layout modes |",
        "|---|---|---|---|---|---|---|",
    ]
    for r in report_rows:
        subj = r.get("subject", {})
        sync = r.get("av_sync", {})
        modes = sorted({
            m for seg in r.get("reframe", {}).get("segments", []) for m in seg.get("modes", [])
        } | {s.get("mode") for s in r.get("reframe", {}).get("shots", []) if s.get("mode")})
        lines.append(
            f"| {r['range_id']} ({r['start']:.0f}–{r['end']:.0f}s) | {'OK' if r['render_ok'] else 'FAIL'} "
            f"| {r['render_seconds']} | {subj.get('mean_union_faces_pct', '—')} "
            f"| {subj.get('mean_largest_face_pct', '—')} "
            f"| {sync.get('offset_ms', '—')} ({sync.get('correlation_peak', '—')}) "
            f"| {', '.join(modes) or '—'} |"
        )
    lines += ["", "Per-range detail (cut list, reframe shots with reasons, frame paths, specs): see <range_id>.json in this folder.", ""]
    (outdir / "report.md").write_text("\n".join(lines))
    print(f"run complete: {outdir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
