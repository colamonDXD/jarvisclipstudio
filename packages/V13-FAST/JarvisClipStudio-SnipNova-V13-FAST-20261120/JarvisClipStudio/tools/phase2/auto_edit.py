"""Phase 2 one-click auto-editor harness: drives SnipNova's REAL stages end to end
on one source video, outside Docker, at zero cost.

  transcribe (faster-whisper, local)  ->  select moments (the app's own selection
  prompt, local Ollama qwen3:8b)  ->  niche EDL (cuts, zooms, transitions,
  captions, B-roll v3)  ->  stock B-roll (only with --env-file: Pixabay/Coverr
  keys read into this process, never printed; SigLIP check on, as in Docker)
  ->  create_optimized_clip (face-aware 9:16 reframe, word captions, hook
  title, EDL render)  ->  QC  ->  decision log + report.

Usage (from D:\\SnipNova-Dev\\claude-code):
  backend\\.venv\\Scripts\\python.exe tools\\phase2\\auto_edit.py --video <file> --niche finance_business --clips 2
      [--env-file "D:\\EditedTool\\JarvisClipStudio\\.env"]   (adds real stock B-roll)
Output: test-footage\\phase2\\<video-stem>\\  (clips, frames, report.json, report.md)
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
STOCK_KEYS = ("PIXABAY_API_KEY", "COVERR_API_KEY")
LLM_KEYS = ("GROQ_API_KEY",)


def _load_stock_keys(env_file: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for line in Path(env_file).read_text(encoding="utf-8", errors="ignore").splitlines():
        m = re.match(r"\s*([A-Z0-9_]+)\s*=\s*(.*)\s*$", line)
        if m and m.group(1) in STOCK_KEYS + LLM_KEYS and not line.lstrip().startswith("#"):
            out[m.group(1)] = m.group(2).strip().strip('"').strip("'")
    return out


def _probe(path: Path) -> dict:
    p = subprocess.run(["ffprobe", "-v", "error", "-show_entries",
                        "stream=codec_type,codec_name,width,height,r_frame_rate:format=duration",
                        "-of", "json", str(path)], capture_output=True, text=True)
    try:
        return json.loads(p.stdout)
    except Exception:
        return {}


def _loudness(path: Path) -> float | None:
    p = subprocess.run(["ffmpeg", "-v", "info", "-i", str(path), "-af", "ebur128=framelog=quiet", "-f", "null", "-"],
                       capture_output=True, text=True)
    m = re.findall(r"I:\s+(-?[\d.]+) LUFS", p.stderr)
    return float(m[-1]) if m else None


def _black_seconds(path: Path) -> float:
    p = subprocess.run(["ffmpeg", "-v", "info", "-i", str(path), "-vf", "blackdetect=d=0.3:pix_th=0.10",
                        "-an", "-f", "null", "-"], capture_output=True, text=True)
    return round(sum(float(d) for d in re.findall(r"black_duration:([\d.]+)", p.stderr)), 2)


def _sec(label) -> float:
    if isinstance(label, (int, float)):
        return float(label)
    parts = [float(x) for x in str(label).split(":")]
    total = 0.0
    for x in parts:
        total = total * 60 + x
    return total


def boundary_check(words: list[dict], start: float, end: float) -> dict:
    """Does the clip start and end on a complete thought? (word timings in seconds)."""
    inside = [w for w in words if start - 0.05 <= w["start"] < end]
    if not inside:
        return {"ok": False, "reason": "no speech inside the clip"}
    first, last = inside[0], inside[-1]
    before = [w for w in words if w["start"] < first["start"]]
    starts_clean = not before or str(before[-1]["text"]).rstrip().endswith((".", "?", "!")) or first["start"] - before[-1]["end"] >= 0.5
    ends_clean = str(last["text"]).rstrip().endswith((".", "?", "!")) or last["end"] <= end - 0.3
    cut_word = any(w["start"] < end < w["end"] for w in words)
    return {"ok": bool(starts_clean and ends_clean and not cut_word), "starts_on_sentence": starts_clean,
            "ends_on_sentence": ends_clean, "cuts_a_word": cut_word,
            "first_words": " ".join(w["text"] for w in inside[:6]), "last_words": " ".join(w["text"] for w in inside[-6:])}


def fixed_windows(words: list[dict], count: int, length: float = 40.0) -> list[dict]:
    """NOT AI selection: sentence-aligned ~40 s windows at 25% / 60% of the video,
    used only to exercise the edit/render/QC stages without a model."""
    if not words:
        return []
    total = words[-1]["end"]
    out = []
    for frac in (0.25, 0.6, 0.85)[:count]:
        target = total * frac
        start = next((w["start"] for p, w in zip(words, words[1:])
                      if w["start"] >= target and str(p["text"]).rstrip().endswith((".", "?", "!"))), target)
        end_target = start + length
        end = next((w["end"] for w in words if w["end"] >= end_target and str(w["text"]).rstrip().endswith((".", "?", "!"))),
                   min(total, end_target))
        end = min(end, start + 58.0)
        out.append({"start_time": round(start, 2), "end_time": round(end, 2), "hook_title": None,
                    "virality_score": None, "hook_type": "fixed-window",
                    "reasoning": "Pipeline check only: sentence-aligned window, not an AI selection."})
    return out


def _burned_captions_module():
    try:
        from src.media import burned_captions  # in the combined V12 tree (OpenCode R2)
        return burned_captions
    except ImportError:
        import importlib.util
        path = ROOT.parent / "opencode" / "backend" / "src" / "media" / "burned_captions.py"
        spec = importlib.util.spec_from_file_location("burned_captions", path)
        module = importlib.util.module_from_spec(spec)
        sys.modules["burned_captions"] = module  # dataclasses look their module up here
        spec.loader.exec_module(module)
        return module


def _keep_streamer_words(source: Path, data: dict) -> dict:
    """Drop transcript words spoken while a GAME subtitle is on screen.

    On a stream the game and the mic share one audio track, so the transcript
    holds game characters' lines too. Games subtitle their own dialogue
    (GTA VI: white text, bottom centre). Each second is checked with the
    burned-in subtitle detector (OpenCode R2, burned_captions): a
    subtitle-like band in the lower third spanning the frame centre means the
    voice at that second is the game's, and its words are not captioned.
    (A facecam mouth-movement rule was tried first: on a cutscene it kept the
    game lines, because the streamer's face moves while watching.)
    The filtered words are written to the transcript cache the caption builder
    reads; the original is kept as .transcript_cache.full.json.
    """
    import cv2
    import numpy as np

    # Game subtitles (GTA VI and most games): white text with a dark outline
    # or shadow, bottom centre. Per second, the share of bright, colourless
    # pixels touching dark ones in that zone. Measured on Clix windows:
    # seconds with a subtitle 2.4-13.7, without exactly 0.0; threshold 1.5.
    # (OpenCode's burned_captions band detector found 0/37 here: it is
    # tuned for large caption bands, not small game subtitles.)
    capture = cv2.VideoCapture(str(source))
    fps = float(capture.get(cv2.CAP_PROP_FPS) or 30.0)
    total = int(capture.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
    seconds = int(total / fps) if fps else 0
    kernel = np.ones((3, 3), np.uint8)
    game_sub = []
    for sec in range(seconds + 1):
        capture.set(cv2.CAP_PROP_POS_FRAMES, int(min(max(total - 1, 0), (sec + 0.5) * fps)))
        ok, frame = capture.read()
        score = 0.0
        if ok:
            h, w = frame.shape[:2]
            band = frame[int(h * 0.80):int(h * 0.95), int(w * 0.25):int(w * 0.75)]
            hsv = cv2.cvtColor(band, cv2.COLOR_BGR2HSV)
            white = (hsv[..., 2] > 215) & (hsv[..., 1] < 40)
            near_dark = cv2.dilate((hsv[..., 2] < 90).astype(np.uint8), kernel) > 0
            score = float((white & near_dark).mean()) * 1000.0
        game_sub.append(score >= 1.5)
    capture.release()
    kept = []
    for word in data["words"]:
        st, en = float(word.get("start", 0)) / 1000.0, float(word.get("end", 0)) / 1000.0
        secs = range(int(st), int(en) + 1)
        if not any(0 <= s_ < len(game_sub) and game_sub[s_] for s_ in secs):
            kept.append(word)
    cache = source.with_suffix(".transcript_cache.json")
    if cache.exists():
        full = source.with_suffix(".transcript_cache.full.json")
        if not full.exists():
            full.write_text(cache.read_text(encoding="utf-8"), encoding="utf-8")
        cached = json.loads(cache.read_text(encoding="utf-8"))
        cached["words"] = kept
        cache.write_text(json.dumps(cached), encoding="utf-8")
    print(f"streamer-words-only: game subtitles on screen in {sum(game_sub)}/{len(game_sub)} s; "
          f"kept {len(kept)} of {len(data['words'])} words", flush=True)
    return {**data, "words": kept}


async def main_async(a) -> int:
    video = Path(a.video).resolve()
    out = ROOT / "test-footage" / "phase2" / (video.stem + (a.out_suffix or ""))
    out.mkdir(parents=True, exist_ok=True)
    work = out / "work"
    work.mkdir(exist_ok=True)
    source = work / video.name
    if not source.exists():
        source.write_bytes(video.read_bytes())  # never write next to the original

    keys = _load_stock_keys(a.env_file) if a.env_file else {}
    stock = {k: v for k, v in keys.items() if k in STOCK_KEYS and v}
    os.environ.update({k: v for k, v in keys.items() if v})
    if a.llm == "auto":
        # Free Groq tier (the app's own clip-assured model) when the owner's key is
        # present; otherwise the local Ollama fallback.
        a.llm = "groq:openai/gpt-oss-120b" if keys.get("GROQ_API_KEY") else "ollama:qwen3:8b"
    os.environ.update({
        "TEMP_DIR": str(out / "temp"), "TRANSCRIPTION_PROVIDER": "faster_whisper",
        "LLM": a.llm, "OLLAMA_BASE_URL": a.ollama_url, "BROLL_CADENCE": "v3",
        "BROLL_VISION_VETO_ENABLED": "true",
    })
    print("keys:", {k: ("present" if keys.get(k) else "not used") for k in STOCK_KEYS + LLM_KEYS}, "| selection model:", a.llm)
    sys.path.insert(0, str(ROOT / "backend"))
    # Quality fixes under review (default off in the product until the
    # owner approves the render, Sanji Q15): the harness renders with them.
    for _switch, _value in (("EDL_CONTENT_CUTS", "on"), ("FIT_PAD", "ambient"),
                            ("REFRAME_HIRES_PROBE", "on"), ("CAPTION_LOOP_FILTER", "on"),
                            ("CAPTION_PHRASE_SPLIT", "on"), ("SELECTION_EVENTS", "on"),
                            ("CLIP_CAP_KEEP_VISUAL_TAIL", "on"), ("SCREEN_SUBJECT", "on")):
        os.environ.setdefault(_switch, _value)
    from src.ai import get_most_relevant_parts_by_transcript
    from src.auto_director import edl as E
    from src.media.transcription import get_video_transcript, load_cached_transcript_data
    from src.video_utils import create_optimized_clip
    from src.media.gaming_templates import clean_layout_credit

    timings: dict[str, float] = {}
    t = time.time()
    transcript = get_video_transcript(source)
    timings["transcribe_s"] = round(time.time() - t, 1)
    data = load_cached_transcript_data(source) or {}
    if a.streamer_words_only and data.get("words"):
        data = _keep_streamer_words(source, data)
    words = [{"text": str(w.get("text", "")).strip(), "start": float(w.get("start", 0)) / 1000.0, "end": float(w.get("end", 0)) / 1000.0}
             for w in data.get("words") or []]
    print(f"transcribed {len(words)} words in {timings['transcribe_s']}s", flush=True)

    # Visual story moments (Step 4). The SigLIP weights are reused from the
    # Phase 1 live run's cache when present (no download).
    phase1_models = ROOT / "test-footage" / "phase1" / "live" / "temp" / "models" / "huggingface"
    if phase1_models.is_dir():
        os.environ.setdefault("HF_HOME", str(phase1_models))
        os.environ.setdefault("HF_HUB_OFFLINE", "1")
    from src.auto_director import story_events as SE
    story_spans = []
    if SE.story_events_enabled():
        t = time.time()
        story_spans = SE.detect_story_events(video)
        timings["story_events_s"] = round(time.time() - t, 1)
        print(f"story events: {len(story_spans)} spans in {timings['story_events_s']}s", flush=True)

    t = time.time()
    if a.select == "whole":
        total = float(subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                                      "-of", "csv=p=0", str(video)], capture_output=True, text=True).stdout.strip() or 0)
        segments = [{"start_time": 0.0, "end_time": round(total, 2), "hook_title": a.hook_title,
                     "virality_score": None, "hook_type": "whole-window",
                     "reasoning": "The whole input window (a live moment cut from the buffer)."}]
    elif a.select == "events":
        total = words[-1]["end"] if words else 0.0
        win = SE.event_window(story_spans, words, total)
        segments = [] if win is None else [{
            "start_time": win["start_time"], "end_time": win["end_time"], "hook_title": None,
            "virality_score": None, "hook_type": "visual-event-window",
            "reasoning": "No-model fallback: strongest visual story moment with its lead-in "
                         + str([(e["start"], e["end"], e["label"]) for e in win["events"]])}]
    elif a.select == "fixed":
        # Pipeline check without an AI model: sentence-aligned windows, clearly labelled.
        segments = fixed_windows(words, a.clips)
    else:
        analysis = await get_most_relevant_parts_by_transcript(transcript, target_clip_count=a.clips)
        segments = list(analysis.most_relevant_segments or [])[: a.clips]
    timings["select_s"] = round(time.time() - t, 1)
    print(f"selected {len(segments)} moments in {timings['select_s']}s", flush=True)

    profile = E.load_profile(a.niche)
    report = {"video": str(video), "niche": a.niche, "llm": a.llm, "stock_broll": bool(stock), "timings": timings, "clips": [],
              "story_events": [e.as_dict() for e in story_spans]}
    for i, seg in enumerate(segments):
        s = seg.model_dump() if hasattr(seg, "model_dump") else dict(seg)
        start, end = _sec(s["start_time"]), _sec(s["end_time"])
        payoff = None
        if story_spans:
            # Same rule as services/video_service.py: never cut away
            # before a visual story moment finishes.
            new_end, payoff = SE.extend_to_payoff(start, end, story_spans, words)
            if payoff:
                print(f"payoff: {start:.1f}-{end:.1f} extended to {new_end:.1f} ({payoff['because']['label']})", flush=True)
                end = new_end
        dur = end - start
        rel = [{"word": w["text"], "text": w["text"], "start": round(w["start"] - start, 3), "end": round(w["end"] - start, 3)}
               for w in words if start <= w["start"] < end]
        plain = a.template == "plain"
        clip_info = {"id": f"clip{i + 1}", "start": start, "end": end, "duration": dur}
        # The harness renders with editorial restraint on (EDL_CONTENT_CUTS
        # defaults off in the product until the owner approves it).
        os.environ.setdefault("EDL_CONTENT_CUTS", "on")
        from src.auto_director.content_cuts import content_cuts_enabled, detect_clip_scene_cuts
        if content_cuts_enabled():
            # Same as services/video_service.py: real shot changes feed the planner.
            clip_info["scene_cuts"] = detect_clip_scene_cuts(video, start, dur)
        edl = E.build_edl(clip_info, rel, profile, energy_peaks=[],
                          broll_enabled=not plain)
        # Same order as services/video_service.py: Plain strips every
        # template element right after the EDL is built; Auto runs the
        # music chain (Music = Auto: the profile bed, or the Auto bed when
        # the profile has none) and resolves B-roll.
        if plain:
            from src.auto_director.template_mode import plainify_edl
            plainify_edl(edl)
        else:
            from src.media.music_library import apply_auto_music_bed
            apply_auto_music_bed(edl, a.niche)
        # Same stage the pipeline runs: the subject-aware reframe plan.
        # Plain with no explicit framing uses the plain tight crop, as
        # resolve_framing_mode does for a Plain task.
        from src.media.reframe_plan import attach_reframe_plan
        framing = None if (plain and a.framing == "auto") else a.framing
        reframed = attach_reframe_plan(edl, source, [(start, end)], plain, framing=framing)
        if stock and not plain:
            from src.media.pixabay_broll import resolve_edl_broll
            resolve_edl_broll(edl)
        clip_path = out / f"clip{i + 1}.mp4"
        layout_report: dict = {}
        t = time.time()
        ok = create_optimized_clip(source, start, end, clip_path, add_subtitles=True,
                                   caption_template=(profile.get("captions") or {}).get("preset_id") or "default",
                                   hook_title=s.get("hook_title") or a.hook_title, edl=edl, profile_name=a.niche,
                                   layout_report=layout_report, framing_mode=a.framing,
                                   layout_template=a.layout_template,
                                   layout_credit=clean_layout_credit(a.credit) if a.layout_template else None)
        render_s = round(time.time() - t, 1)
        probe = _probe(clip_path) if ok else {}
        frames = []
        if ok:
            out_dur = float((probe.get("format") or {}).get("duration") or 0)
            for frac in (0.08, 0.5, 0.9):
                f = out / f"clip{i + 1}_{int(frac * 100)}pct.png"
                subprocess.run(["ffmpeg", "-y", "-v", "error", "-ss", f"{out_dur * frac:.2f}", "-i", str(clip_path),
                                "-frames:v", "1", "-vf", "scale=360:-2", str(f)])
                frames.append(f.name)
        broll = edl.get("broll") or []
        report["clips"].append({
            "clip": clip_path.name, "render_ok": ok, "render_s": render_s,
            "source_start": start, "source_end": end, "duration": round(dur, 1),
            "selection": {"virality": s.get("virality_score"), "hook_type": s.get("hook_type"), "payoff": payoff,
                          "hook_title": s.get("hook_title"), "reasoning": s.get("reasoning")},
            "decisions": {
                "segments": len(edl.get("segments") or []),
                "transitions": [x.get("transition_in") for x in edl.get("segments") or [] if x.get("transition_in") not in (None, "none", "cut")],
                "zooms": len(edl.get("zoom_keyframes") or []),
                "broll_planned": len(broll),
                "broll_inserted": sum(1 for b in broll if b.get("local_path")),
                "broll": [{"at": b.get("start"), "said": b.get("spoken"), "searched": b.get("search_query"),
                           "layout": b.get("layout"), "inserted": bool(b.get("local_path")),
                           "why_not": (b.get("not_inserted") or {}).get("reason")} for b in broll],
                "broll_dropped": [d.get("reason") for d in edl.get("broll_dropped") or []],
                "music": (edl.get("audio") or {}).get("music_mood"),
                "music_volume": (edl.get("audio") or {}).get("music_volume_0_to_1", (edl.get("audio") or {}).get("music_volume")),
                "sfx": len(edl.get("sfx_events") or []) + len(edl.get("sfx") or []),
                "overlays": len(edl.get("overlays") or []),
                "template": edl.get("template") or "auto",
                "captions": (profile.get("captions") or {}).get("preset_id"),
            },
            "qc": {
                "ffprobe": [{k: st.get(k) for k in ("codec_type", "width", "height")} for st in probe.get("streams", [])],
                "output_seconds": round(float((probe.get("format") or {}).get("duration") or 0), 2),
                "loudness_lufs": _loudness(clip_path) if ok else None,
                "black_seconds": _black_seconds(clip_path) if ok else None,
                "boundary": boundary_check(words, start, end),
                "layout": {k: layout_report.get(k) for k in ("layout", "broll_applied", "framing") if k in layout_report},
                "reframe_plan": {"attached": reframed, "delegate": (edl.get("reframe") or {}).get("delegate"),
                                 "shots": [(sh.get("mode"), sh.get("reason")) for sh in ((edl.get("reframe") or {}).get("shots") or [])][:8]},
            },
            "frames": frames,
        })
        print(f"clip{i + 1}: {start:.1f}-{end:.1f}s render ok={ok} in {render_s}s", flush=True)

    (out / "report.json").write_text(json.dumps(report, indent=1, default=str), encoding="utf-8")
    md = [f"# Auto-edit: {video.name} ({a.niche})", "",
          f"LLM {a.llm} · stock B-roll {'ON' if stock else 'OFF (no keys)'} · transcribe {timings['transcribe_s']}s · select {timings['select_s']}s", ""]
    for c in report["clips"]:
        d, q, sel = c["decisions"], c["qc"], c["selection"]
        md += [f"## {c['clip']} — source {c['source_start']:.1f}–{c['source_end']:.1f}s ({c['duration']}s)",
               f"* Hook: **{sel['hook_title']}** · virality {sel['virality']} · {sel['hook_type']}",
               f"* Why: {sel['reasoning']}",
               f"* Edit: {d['segments']} segments, transitions {d['transitions'] or 'none'}, {d['zooms']} zooms, captions `{d['captions']}`, music `{d['music']}`",
               f"* B-roll: {d['broll_inserted']}/{d['broll_planned']} inserted — " + "; ".join(
                   f"{b['at']}s '{b['said']}'→{b['searched']} ({b['layout']}, {'in' if b['inserted'] else b['why_not']})" for b in d["broll"]),
               f"* QC: {q['output_seconds']}s, {q['ffprobe']}, loudness {q['loudness_lufs']} LUFS, black {q['black_seconds']}s, "
               f"boundary ok={q['boundary'].get('ok')} (starts on sentence {q['boundary'].get('starts_on_sentence')}, ends {q['boundary'].get('ends_on_sentence')})",
               f"* Framing: {q['reframe_plan']}",
               f"* First words: “{q['boundary'].get('first_words')}” … last: “{q['boundary'].get('last_words')}”",
               f"* Frames: {', '.join(c['frames'])}", ""]
    (out / "report.md").write_text("\n".join(md), encoding="utf-8")
    print("done:", out / "report.md")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--video", required=True)
    ap.add_argument("--niche", required=True)
    ap.add_argument("--clips", type=int, default=2)
    ap.add_argument("--env-file")
    ap.add_argument("--llm", default="auto")
    ap.add_argument("--hook-title", help="on-screen title for every clip (e.g. a creator campaign)")
    ap.add_argument("--streamer-words-only", action="store_true",
                    help="do not caption words spoken while a game subtitle is on screen (game dialogue)")
    ap.add_argument("--select", choices=("ai", "fixed", "events", "whole"), default="ai",
                    help="events = no-model fallback from visual story moments (SELECTION_EVENTS)")
    ap.add_argument("--ollama-url", default="http://localhost:11434/v1")
    ap.add_argument("--template", choices=("auto", "plain"), default="auto",
                    help="plain = cuts + captions only (no B-roll, music, SFX, effects); auto = full template")
    ap.add_argument("--framing", choices=("auto", "fit", "crop"), default="auto",
                    help="auto = one layout per clip (planner), fit = whole frame, crop = face-follow crop")
    ap.add_argument("--layout-template", choices=("facecam_red", "facecam_gold"),
                    help="streamer 'face on top, game below' template (clips with a facecam)")
    ap.add_argument("--credit", help="credit line for the template, e.g. @KaiCenat")
    ap.add_argument("--out-suffix", default="", help="write to test-footage/phase2/<stem><suffix>")
    a = ap.parse_args()
    if a.env_file and not Path(a.env_file).is_file():
        print(f"--env-file not found: {a.env_file}\n"
              "Point it at the .env that holds GROQ_API_KEY (and optionally PIXABAY_API_KEY / COVERR_API_KEY).\n"
              "Nothing was run.", file=sys.stderr)
        return 2
    return asyncio.run(main_async(a))


if __name__ == "__main__":
    sys.exit(main())
