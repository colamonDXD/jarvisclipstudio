"""Fast clipping of long finished streams (10-20 h VODs) without downloading the video.

Luffy, 2026-11-19: "there is a 10 hours video... I need a fast clipping tool...
make something that can clip faster and clips better clips that can go viral".

The whole video of a 10 h stream is ~25 GB and hours of CPU. This path:

1. ``probe``: duration, title and YouTube's "Most replayed" heatmap (yt-dlp
   ``heatmap``: 100 points over the video, present once a video has enough
   views) - what viewers actually rewatched.
2. ``download_audio``: the audio only (~0.5 GB for 11 h).
3. ``scan_audio``: the streamer's voice evidence in parallel chunks (the same
   per-second features as live clipping) -> reaction flags (screams, laughs,
   hype), each boosted by the heatmap at its time.
4. ``transcribe`` the best ~3x candidates (60 s each) with Groq's hosted
   Whisper (free key) or the local model.
5. ``llm_pick``: the free LLM gateway reads what was said and picks the most
   viral moments with a hook start, an end (22-45 s) and a title; without a
   model the strongest signals win.
6. ``download_section``: only those pieces, 1080p, cut exactly.
7. Each piece is rendered by ``finisher.render_live_clip`` (face on top,
   streamer-only captions, the GPU when the container has one).

``VodSession`` has the same interface as ``LiveSession`` so the live worker,
API, database rows and the Live page are reused unchanged.
"""
from __future__ import annotations

import json
import logging
import os
import re
import subprocess
import tempfile
import threading
import time
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence

from . import yt_dlp_binary
from .moments import creator_on_camera, speech_window

logger = logging.getLogger(__name__)

CHUNK_SEC = 1800          # audio scan chunk (30 min)
CANDIDATE_WINDOW = (-20.0, 40.0)  # transcript around a flag
MIN_LEN, MAX_LEN = 30.0, 60.0   # owner: 30 s strict minimum
MIN_GAP = 90.0            # seconds between two picked moments


def max_vod_hours() -> float:
    try:
        return float(os.getenv("LIVE_VOD_MAX_HOURS", "24"))
    except ValueError:
        return 24.0


# --------------------------------------------------------------------------
# 1-2. Probe and audio
# --------------------------------------------------------------------------

def _ytdlp(*args: str, timeout: Optional[float] = None) -> subprocess.CompletedProcess:
    cmd = [yt_dlp_binary(), "--no-warnings", "-q", *args]
    return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)


def probe(url: str) -> Dict[str, Any]:
    out = _ytdlp("--skip-download", "-J", url, timeout=120)
    if out.returncode != 0 or not out.stdout.strip():
        raise RuntimeError((out.stderr or "yt-dlp could not read the video").strip()[-300:])
    info = json.loads(out.stdout)
    return {"duration": float(info.get("duration") or 0), "title": info.get("title"),
            "live_status": info.get("live_status"), "heatmap": info.get("heatmap") or []}


def download_audio(url: str, folder: Path) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    out = _ytdlp("-f", "bestaudio[abr<=160]/bestaudio/ba", "-N", "8", "-o", str(folder / "audio.%(ext)s"), url)
    files = sorted(p for p in folder.glob("audio.*") if not p.name.endswith((".part", ".ytdl")))
    if out.returncode != 0 or not files:
        raise RuntimeError((out.stderr or "audio download failed").strip()[-300:])
    return files[0]


# --------------------------------------------------------------------------
# 3. Audio scan (parallel) and candidate ranking
# --------------------------------------------------------------------------

def _scan_chunk(args) -> Dict[str, List[float]]:
    audio, start, length = args
    from ..auto_director.reaction_moments import extract_voice_evidence

    with tempfile.TemporaryDirectory(prefix="vod_chunk_") as tmp:
        chunk = Path(tmp) / "chunk.wav"
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-ss", f"{start:.1f}", "-t", f"{length:.1f}", "-i", str(audio),
                        "-vn", "-ac", "1", "-ar", "16000", str(chunk)], capture_output=True)
        if not chunk.is_file():
            return {}
        ev = extract_voice_evidence(str(chunk), length)
    n = min(len(ev.get(k, [])) for k in ("vocal", "f0", "laugh", "voiced_frac")) if ev else 0
    return {k: [float(x) for x in ev[k][:n]] for k in ("vocal", "f0", "laugh", "voiced_frac")} if n else {}


def scan_audio(audio: Path, duration: float, workers: int = 4,
               progress: Optional[Callable[[str], None]] = None) -> Dict[str, List[float]]:
    """Per-second voice features for the whole audio, scanned in parallel chunks."""
    jobs = [(str(audio), s, min(CHUNK_SEC, duration - s)) for s in range(0, int(duration), CHUNK_SEC)]
    series: Dict[str, List[float]] = {k: [] for k in ("vocal", "f0", "laugh", "voiced_frac")}
    done = 0
    with ProcessPoolExecutor(max_workers=max(1, workers)) as pool:
        for (_, start, length), part in zip(jobs, pool.map(_scan_chunk, jobs)):
            got = len(part.get("vocal", []))
            for k in series:
                values = part.get(k, [])
                series[k].extend(values + [0.0] * (int(length) - len(values)) if values else [0.0] * int(length))
            done += 1
            if progress:
                progress(f"Listening for reactions: {done}/{len(jobs)} parts ({got} s read)")
    return series


def heat_at(heatmap: Sequence[Dict[str, float]], t: float) -> float:
    for point in heatmap:
        if float(point.get("start_time", 0)) <= t < float(point.get("end_time", 0)):
            return float(point.get("value", 0.0))
    return 0.0


def find_candidates(series: Dict[str, List[float]], heatmap: Sequence[Dict[str, float]], limit: int) -> List[Dict[str, Any]]:
    """Reaction moments ranked by ``selection.rank_moments`` (trainable: a
    trained ``selection_params.json`` takes effect here), then boosted by the
    "most replayed" heatmap when the video has one, and spaced apart."""
    from .selection import load_params, rank_moments, spaced

    params = load_params()
    scored = []
    for m in rank_moments(series, params):
        heat = heat_at(heatmap, m["onset"])
        scored.append({**m, "heat": round(heat, 3), "score": round(m["score"] * (1.0 + 1.5 * heat), 2)})
    scored.sort(key=lambda f: -f["score"])
    return spaced(scored, limit, float(params.get("min_gap", MIN_GAP)))


def add_reaction_features(series: Dict[str, List[float]], audio: Path, duration: float,
                          progress: Optional[Callable[[str], None]] = None) -> Dict[str, List[float]]:
    """Merge the optional reaction-feature pass (SenseVoice tags + shout,
    ``live/sensevoice_scan.py``) into the scan series for ``rank_moments``.

    Returns the series untouched unless ``SENSEVOICE_FEATURES`` is on, and
    a failing pass never breaks the scan — the core features still rank.
    """
    from .sensevoice_scan import reaction_feature_series, sensevoice_enabled

    if not sensevoice_enabled():
        return series
    try:
        extra = reaction_feature_series(audio, duration, progress=progress)
    except Exception as exc:
        logger.warning("Reaction-feature pass failed, continuing without it: %s", exc)
        return series
    if not extra:
        return series
    merged = dict(series)
    merged.update(extra)
    return merged


# --------------------------------------------------------------------------
# 4. Transcripts of the candidates only
# --------------------------------------------------------------------------

def transcribe_window(audio: Path, start: float, end: float, source_url: Optional[str] = None) -> List[Dict[str, Any]]:
    """Words [{text, start, end}] (seconds, absolute) for [start, end] of the audio.

    Transcript selection order: YouTube's own timed captions when the
    source is a YouTube video (word-timed, no speech model — see
    ``media.yt_captions``), then Groq's hosted Whisper, then the local
    model. Any gap falls through to the next source.
    """
    if source_url:
        from ..media.yt_captions import fetch_youtube_captions, words_in_window

        captions = fetch_youtube_captions(source_url)
        if captions:
            words = words_in_window(captions, start, end)
            if words:
                return words
    from .groq_transcribe import groq_enabled, groq_transcribe_file

    with tempfile.TemporaryDirectory(prefix="vod_tr_") as tmp:
        clip = Path(tmp) / "window.mp3"
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-ss", f"{start:.2f}", "-t", f"{end - start:.2f}", "-i", str(audio),
                        "-vn", "-ac", "1", "-ar", "16000", "-b:a", "48k", str(clip)], capture_output=True)
        if not clip.is_file():
            return []
        result = groq_transcribe_file(clip) if groq_enabled() else None
        if result is None:
            result = _local_transcribe(clip)
    words = []
    for seg in (result or {}).get("segments") or []:
        for w in seg.get("words") or []:
            words.append({"text": str(w.get("word", "")).strip(), "start": start + float(w.get("start", 0)),
                          "end": start + float(w.get("end", 0))})
    return words


BATCH = 5            # windows per Groq request (free tier: 20 requests/min)
GAP_SEC = 2.0        # silence between joined windows


def transcribe_windows(audio: Path, windows: Sequence[tuple], source_url: Optional[str] = None) -> List[List[Dict[str, Any]]]:
    """Words per window.

    YouTube's own captions cover every window they reach (one fetch for
    the whole video); the windows they miss — and every window when the
    source has no captions — go through the batched speech-model path
    below, unchanged.
    """
    if not windows:
        return []
    out: List[Optional[List[Dict[str, Any]]]] = [None] * len(windows)
    missing = list(range(len(windows)))
    if source_url:
        from ..media.yt_captions import fetch_youtube_captions, words_in_window

        captions = fetch_youtube_captions(source_url)
        if captions:
            missing = []
            for i, (start, end) in enumerate(windows):
                words = words_in_window(captions, start, end)
                if words:
                    out[i] = words
                else:
                    missing.append(i)
    if missing:
        filled = _transcribe_windows_with_speech_model(audio, [windows[i] for i in missing])
        for i, words in zip(missing, filled):
            out[i] = words
    return [words or [] for words in out]


def _transcribe_windows_with_speech_model(audio: Path, windows: Sequence[tuple]) -> List[List[Dict[str, Any]]]:
    """Words per window, transcribing several windows in ONE request.

    The windows are joined into one audio file with short silences between
    them; each word is mapped back to its window by its offset. Groq's free
    tier allows 20 requests a minute, so 30 single requests hit the limit.
    """
    from .groq_transcribe import groq_enabled, groq_transcribe_file

    if not windows:
        return []
    with tempfile.TemporaryDirectory(prefix="vod_batch_") as tmp:
        parts, offsets, cursor = [], [], 0.0
        for i, (start, end) in enumerate(windows):
            part = Path(tmp) / f"p{i}.wav"
            subprocess.run(["ffmpeg", "-v", "error", "-y", "-ss", f"{start:.2f}", "-t", f"{end - start:.2f}", "-i", str(audio),
                            "-vn", "-ac", "1", "-ar", "16000", "-af", f"apad=pad_dur={GAP_SEC}", str(part)], capture_output=True)
            parts.append(part)
            offsets.append(cursor)
            cursor += (end - start) + GAP_SEC
        listing = Path(tmp) / "list.txt"
        listing.write_text("".join(f"file '{p.as_posix()}'" + chr(10) for p in parts), encoding="utf-8")
        joined = Path(tmp) / "joined.mp3"
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "concat", "-safe", "0", "-i", str(listing),
                        "-ac", "1", "-ar", "16000", "-b:a", "48k", str(joined)], capture_output=True)
        result = groq_transcribe_file(joined) if groq_enabled() and joined.is_file() else None
        if result is None:
            return [transcribe_window(audio, s, e) for s, e in windows]
    out: List[List[Dict[str, Any]]] = [[] for _ in windows]
    for seg in result.get("segments") or []:
        for w in seg.get("words") or []:
            t = float(w.get("start", 0))
            idx = max(i for i, off in enumerate(offsets) if off <= t + 0.01) if t >= 0 else 0
            start, end = windows[idx]
            rel = t - offsets[idx]
            if rel > (end - start) + 0.5:
                continue  # inside the silence gap
            out[idx].append({"text": str(w.get("word", "")).strip(), "start": start + rel,
                             "end": start + float(w.get("end", t)) - offsets[idx]})
    return out


def _local_transcribe(clip: Path) -> Optional[Dict[str, Any]]:
    try:
        from faster_whisper import WhisperModel

        global _LOCAL_MODEL
        if _LOCAL_MODEL is None:
            _LOCAL_MODEL = WhisperModel(os.getenv("LIVE_WHISPER_MODEL", "small"), device="auto", compute_type="int8")
        # VAD on, like the main transcription path: unfiltered, this
        # model hallucinates lines over music/game noise (measured on
        # the Kai Cenat windows, 2026-11-19). When the VAD finds no
        # speech at all it can also be wrong the other way — it rejected
        # every second of one real window — so an empty VAD result is
        # retried once unfiltered, and only then.
        segments, info = _LOCAL_MODEL.transcribe(str(clip), word_timestamps=True, vad_filter=True)
        segments = list(segments)
        if not segments:
            segments, info = _LOCAL_MODEL.transcribe(str(clip), word_timestamps=True, vad_filter=False)
            segments = list(segments)
            if segments:
                logger.warning("Local transcription: VAD found no speech; unfiltered retry recovered %d segments",
                               len(segments))
        segs = []
        for s in segments:
            segs.append({"start": s.start, "end": s.end, "text": s.text,
                         "words": [{"word": w.word, "start": w.start, "end": w.end} for w in (s.words or [])]})
        try:
            # Snap the word edges onto the clip's loudness envelope
            # (media.word_align, from wordcaps): the picker's windows
            # are chosen from these timings, and the finisher re-aligns
            # the same words again at render time.
            from ..media.word_align import refine_words, speech_blocks, word_align_enabled

            if word_align_enabled():
                blocks = speech_blocks(clip)
                for seg in segs:
                    if seg["words"]:
                        seg["words"], _ = refine_words(seg["words"], blocks)
        except Exception:
            logger.warning("Local transcription word alignment failed", exc_info=True)
        return {"segments": segs, "language": getattr(info, "language", None)}
    except Exception as exc:
        logger.warning("Local transcription failed: %s", exc)
        return None


_LOCAL_MODEL = None


# --------------------------------------------------------------------------
# 5. The LLM picks what goes viral
# --------------------------------------------------------------------------

PICK_PROMPT = """You pick short-form clips (TikTok/Shorts/Reels) from a long livestream.
Each candidate below is a moment where the streamer reacted strongly (voice signals), with
"heat" = how much viewers rewatched that part (0-1) and the transcript of the 60 seconds
around it (absolute seconds in brackets).

Pick the {count} candidates most likely to go viral as standalone clips. Prefer: a strong
hook in the first 2-3 seconds, a clear funny/shocking/emotional payoff, the streamer talking
(not silent gameplay), context a stranger understands. Skip boring talk, ads, reading chat
names, and moments that need earlier context.

For each pick give start and end in absolute seconds: start right before the hook (never
on silence), end just after the payoff, {min_len:.0f}-{max_len:.0f} seconds long. Give a short
punchy title (3-8 words, no emojis) and one line why. Also write "quote": the on-screen
caption in the style of viral streamer clips, an ironic one-liner about what happens, e.g.
"Lacy swore he never crashes" or "Speed thought this was easy". ALWAYS third person with the
streamer's name ({creator}); NEVER "I", "me", "my", "we". At most 8 words, no emojis in it.
And "emojis": 1-3 emojis that mock or react to it, chosen ONLY from: {emoji_set}

Title rules (the title is burned onto the clip):
- 3-8 words, catchy, built from the words the streamer actually said in that moment -
  quote their real words or describe the moment in the third person.
- NEVER use I / me / my / we / our. No quotation marks. No emojis.
- Never invent a name, game, place or event that is not in the transcript.

{request_rule}Answer with ONLY a JSON array:
[{{"id": <candidate id>, "start": <sec>, "end": <sec>, "title": "...", "quote": "...", "emojis": ["...", "..."], "why": "...", "virality": <0-100>}}]

Candidates:
{candidates}"""

_FIRST_PERSON = {"i", "me", "my", "mine", "we", "our", "ours", "us", "i'm", "im", "i've", "i'll", "i'd", "we're", "we've"}
_EMOJI = re.compile("[\U0001F000-\U0001FAFF\U00002600-\U000027BF\U0001F1E6-\U0001F1FF\uFE0F]")


def clean_pick_title(raw: Any) -> Optional[str]:
    """A model-written pick title -> one safe to burn, or None.

    Owner pins (2026-11-19): no emojis, never first person (third
    person / quote style), short. The Kai Cenat clips this lane started
    from carried titles with the model's surrounding quote characters
    burned in verbatim ('"Kaicenat hollas for independent women"'), so
    quotes and markdown are stripped here, at the source, not left to
    whatever renders the title later. A title that cannot be cleaned
    (empty, one word, first person even after stripping) is dropped,
    never repaired into words the model did not write.
    """
    if not isinstance(raw, str):
        return None
    title = raw.strip().strip("`").strip()
    if len(title) >= 2 and title[0] == title[-1] and title[0] in "\"'":
        title = title[1:-1].strip()
    title = _EMOJI.sub("", title)
    title = re.sub(r"\s+", " ", title).strip(" .!?,")
    words = title.split()
    if len(words) < 2:
        return None
    if any(word.strip("\"'.,!?").lower() in _FIRST_PERSON for word in words):
        return None
    return " ".join(words[:8])


def _transcript_lines(words: List[Dict[str, Any]]) -> str:
    lines, current, line_start = [], [], None
    for w in words:
        if line_start is None:
            line_start = w["start"]
        current.append(w["text"])
        if w["text"].endswith((".", "!", "?")) or len(current) >= 14:
            lines.append(f"[{line_start:.0f}] {' '.join(current)}")
            current, line_start = [], None
    if current:
        lines.append(f"[{line_start:.0f}] {' '.join(current)}")
    return "\n".join(lines) or "(no speech)"


def _chat(prompt: str, timeout: float = 180.0) -> Optional[str]:
    """One completion from the app's LLM gateway (free models), or None."""
    base = (os.getenv("OPENAI_BASE_URL") or "").rstrip("/")
    key = os.getenv("OPENAI_API_KEY") or ""
    model = (os.getenv("LIVE_VOD_LLM") or os.getenv("LLM") or "").split(":", 1)[-1]
    if not base or not model:
        return None
    import httpx

    try:
        response = httpx.post(f"{base}/chat/completions", timeout=timeout,
                              headers={"Authorization": f"Bearer {key}"} if key else {},
                              json={"model": model, "temperature": 0.2,
                                    "messages": [{"role": "user", "content": prompt}]})
        return response.json()["choices"][0]["message"]["content"]
    except Exception as exc:
        logger.warning("LLM gateway unavailable (%s)", exc)
        return None


REQUEST_RULE = """The user asked for: "{request}".
Only pick candidates that clearly match this request (it decides, before virality). Return
fewer than {count} if fewer match, or an empty array if none does.

"""


def _parse_picks(text: str) -> Optional[List[Dict[str, Any]]]:
    """The model's JSON array; when it is cut off or broken, every complete
    {...} object inside it is still used (a 30-clip answer broke at char 3625)."""
    match = re.search(r"\[.*\]", text, re.S)
    if match:
        try:
            data = json.loads(match.group(0))
            if isinstance(data, list):
                return data
        except ValueError:
            pass
    objects = []
    for chunk in re.findall(r"\{[^{}]*\}", text, re.S):
        try:
            obj = json.loads(chunk)
        except ValueError:
            continue
        if isinstance(obj, dict) and "id" in obj:
            objects.append(obj)
    return objects or None


def llm_pick_batched(candidates: List[Dict[str, Any]], count: int, request: Optional[str] = None,
                     creator: Optional[str] = None, batch: int = 15) -> Optional[List[Dict[str, Any]]]:
    """Ask the model in batches of ``batch`` candidates (one big prompt for 90
    moments came back truncated), each for its share of the clips, then keep
    the best overall."""
    if len(candidates) <= batch:
        return llm_pick(candidates, count, request=request, creator=creator)
    picks: List[Dict[str, Any]] = []
    answered = False
    groups = [candidates[i:i + batch] for i in range(0, len(candidates), batch)]
    share = max(1, -(-count // len(groups)) + 1)
    for group in groups:
        got = llm_pick(group, share, request=request, creator=creator)
        if got is not None:
            answered = True
            picks.extend(got)
    if not answered:
        return None
    picks.sort(key=lambda p: -(p.get("virality") or 0))
    return picks[:count]


def _emoji_set() -> List[str]:
    try:
        from ..media.edl_render import ASSETS_DIR

        data = json.loads((Path(ASSETS_DIR) / "stickers" / "STICKERS_MANIFEST.json").read_text(encoding="utf-8"))
        return [e["emoji"] for e in data if e.get("emoji")]
    except Exception:
        return []


def llm_pick(candidates: List[Dict[str, Any]], count: int,
             request: Optional[str] = None, creator: Optional[str] = None) -> Optional[List[Dict[str, Any]]]:
    """The model's picks; None when the model is unavailable (the caller then
    ranks by signals). With a ``request`` an empty list means nothing matched."""
    blocks = []
    for c in candidates:
        blocks.append(f"### id {c['id']} - moment at {c['onset']:.0f}s ({time.strftime('%H:%M:%S', time.gmtime(c['onset']))}), "
                      f"signals {','.join(c.get('signals') or []) or 'none'}, strength {c.get('peak', 0):.1f}, "
                      f"heat {c.get('heat', 0):.2f}\n"
                      f"{_transcript_lines(c.get('words') or [])}")
    rule = REQUEST_RULE.format(request=request.replace('"', "'"), count=count) if request else ""
    emojis = _emoji_set()
    prompt = PICK_PROMPT.format(count=count, min_len=MIN_LEN, max_len=MAX_LEN, candidates="\n\n".join(blocks),
                                request_rule=rule, creator=creator or "the streamer",
                                emoji_set=" ".join(emojis) or "😭 💀 🔥 😂")
    text = _chat(prompt)
    if text is None:
        return None
    picks = _parse_picks(text)
    if picks is None:
        logger.warning("Clip picking model gave no usable answer; using the strongest signals")
        return None
    by_id = {c["id"]: c for c in candidates}
    out = []
    for p in picks:
        c = by_id.get(p.get("id"))
        if c is None:
            continue
        try:
            start, end = float(p["start"]), float(p["end"])
        except (KeyError, TypeError, ValueError):
            continue
        lo, hi = c["onset"] + CANDIDATE_WINDOW[0] - 10, c["onset"] + CANDIDATE_WINDOW[1] + 15
        start = max(lo, min(start, c["onset"]))
        end = min(hi, max(end, start + MIN_LEN))
        end = min(end, start + MAX_LEN)
        picked_emojis = [e for e in (p.get("emojis") or []) if isinstance(e, str) and (not emojis or e in emojis)][:3]
        out.append({**c, "start": round(start, 2), "end": round(end, 2), "title": clean_pick_title(p.get("title")),
                    "quote": str(p.get("quote") or "")[:80] or None, "emojis": picked_emojis,
                    "why": str(p.get("why") or "")[:200], "virality": p.get("virality")})
    if request:
        return out[:count]  # [] = nothing matched the request: an honest answer
    return out[:count] or None


# --------------------------------------------------------------------------
# "Find moments where..." (a request typed by the user)
# --------------------------------------------------------------------------

EXPAND_PROMPT = """A user wants clips from a long gaming/IRL livestream that match: "{request}".
List 15-30 lowercase words or short phrases a streamer would SAY at such moments (slang,
exclamations, game terms, synonyms), and how much loud reactions matter for this request
(0 = not at all, e.g. "when he talks about his mom"; 1 = a lot, e.g. "every time he rages").
Answer with ONLY JSON: {{"keywords": ["...", "..."], "reaction_weight": 0.0}}"""

STOPWORDS = frozenset("a an the and or of to in on at for with when where every time he she they his her their is are was "
                      "be that this it moments moment clip clips find show me all any about from".split())


def expand_request(request: str) -> Dict[str, Any]:
    """Search words for a request (the model expands it; the request's own words always count)."""
    own = [w for w in re.findall(r"[a-z0-9']+", request.lower()) if w not in STOPWORDS and len(w) > 2]
    keywords, weight = list(own), 0.5
    text = _chat(EXPAND_PROMPT.format(request=request.replace('"', "'")), timeout=90)
    if text:
        try:
            data = json.loads(re.search(r"\{.*\}", text, re.S).group(0))
            keywords += [str(k).lower().strip() for k in data.get("keywords") or [] if str(k).strip()]
            weight = max(0.0, min(1.0, float(data.get("reaction_weight", weight))))
        except Exception:
            pass
    seen, uniq = set(), []
    for k in keywords:
        if k and k not in seen:
            seen.add(k)
            uniq.append(k)
    return {"keywords": uniq[:40], "reaction_weight": weight}


def transcribe_full(audio: Path, duration: float, progress: Optional[Callable[[str], None]] = None,
                    chunk_sec: float = 600.0) -> Optional[List[Dict[str, Any]]]:
    """The whole stream's speech as segments [{start, end, text}], on the GPU.

    Uses the transformers Whisper pipeline (batched, fp16): the GPU-ready
    path in the app image (faster-whisper's CUDA build needs cuBLAS 12, the
    image ships CUDA 13). None when there is no GPU or the model cannot load:
    the caller then searches the reaction candidates only.
    """
    try:
        import numpy as np
        import torch
        from transformers import pipeline

        if not torch.cuda.is_available():
            return None
        name = os.getenv("LIVE_SEARCH_ASR_MODEL", "openai/whisper-large-v3-turbo")
        asr = pipeline("automatic-speech-recognition", model=name, dtype=torch.float16, device="cuda:0")
    except Exception as exc:
        logger.warning("GPU transcription unavailable (%s); searching the reaction moments only", exc)
        return None
    segments: List[Dict[str, Any]] = []
    t0 = time.time()
    try:
        for start in range(0, int(duration), int(chunk_sec)):
            length = min(chunk_sec, duration - start)
            raw = subprocess.run(["ffmpeg", "-v", "error", "-ss", f"{start:.1f}", "-t", f"{length:.1f}", "-i", str(audio),
                                  "-vn", "-ac", "1", "-ar", "16000", "-f", "s16le", "-"], capture_output=True).stdout
            if len(raw) < 32000:
                continue
            samples = np.frombuffer(raw, np.int16).astype(np.float32) / 32768.0
            out = asr({"raw": samples, "sampling_rate": 16000}, chunk_length_s=30, batch_size=16,
                      return_timestamps=True, generate_kwargs={"task": "transcribe"})
            for chunk in out.get("chunks") or []:
                ts = chunk.get("timestamp") or (None, None)
                if ts[0] is None:
                    continue
                s0 = start + float(ts[0])
                s1 = start + float(ts[1] if ts[1] is not None else ts[0] + 5)
                segments.append({"start": s0, "end": s1, "text": str(chunk.get("text", "")).strip()})
            if progress:
                done = min(duration, start + length)
                rate = done / max(time.time() - t0, 1e-6)
                progress(f"Reading the whole stream: {done / 3600:.1f}/{duration / 3600:.1f} h ({rate:.0f}x real time)")
    finally:
        del asr
        try:
            torch.cuda.empty_cache()
        except Exception:
            pass
    return segments


def _segment_words(seg: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Approximate word timings inside a transcript segment (even spread)."""
    words = seg["text"].split()
    if not words:
        return []
    step = max(0.05, (seg["end"] - seg["start"]) / len(words))
    return [{"text": w, "start": seg["start"] + i * step, "end": seg["start"] + (i + 1) * step} for i, w in enumerate(words)]


def search_windows(segments: List[Dict[str, Any]], expansion: Dict[str, Any], series: Dict[str, List[float]],
                   heatmap: Sequence[Dict[str, float]], limit: int, window: float = 60.0, step: float = 20.0) -> List[Dict[str, Any]]:
    """Candidate moments for a request: windows ranked by matching words, the
    streamer's reaction strength there (weighted by the request) and heat."""
    keywords = expansion.get("keywords") or []
    weight = float(expansion.get("reaction_weight", 0.5))
    if not segments or not keywords:
        return []
    from .voice_scan import VoiceTrack

    track = VoiceTrack(baseline_sec=240.0)
    n = min(len(series.get(k, [])) for k in ("vocal", "f0", "laugh")) if series else 0
    for sec in range(n):
        track.times.append(float(sec))
        for k in ("vocal", "f0", "laugh"):
            track.feats[k].append(series[k][sec])
    flags = track.onsets() if n else []
    patterns = [re.compile(rf"\b{re.escape(k)}", re.I) for k in keywords]
    end_time = max(s["end"] for s in segments)
    scored = []
    t = 0.0
    while t < end_time:
        inside = [s for s in segments if s["end"] > t and s["start"] < t + window]
        text = " ".join(s["text"] for s in inside)
        hits = sum(len(p.findall(text)) for p in patterns)
        if hits:
            near = [f for f in flags if t <= f["onset"] < t + window]
            peak = max((float(f["peak"]) for f in near), default=0.0)
            onset = max(near, key=lambda f: f["peak"])["onset"] if near else (inside[0]["start"] if inside else t)
            score = hits * (1.0 + 1.5 * heat_at(heatmap, onset)) + weight * peak
            words = [w for s in inside for w in _segment_words(s)]
            scored.append({"onset": float(onset), "end": float(onset) + 1.0, "peak": peak,
                           "signals": sorted({x for f in near for x in f.get("signals") or []}),
                           "heat": round(heat_at(heatmap, onset), 3), "score": round(score, 2), "hits": hits,
                           "words": words})
        t += step
    scored.sort(key=lambda c: -c["score"])
    picked: List[Dict[str, Any]] = []
    for c in scored:
        if all(abs(c["onset"] - p["onset"]) >= MIN_GAP for p in picked):
            picked.append(c)
        if len(picked) >= limit:
            break
    return picked


def signal_pick(candidates: List[Dict[str, Any]], voiced: Sequence[float], count: int) -> List[Dict[str, Any]]:
    out = []
    for c in candidates[:count]:
        start, stop, _ = speech_window(voiced, c["onset"], float(c.get("end", c["onset"])),
                                       lead_max=6.0, min_len=MIN_LEN, max_len=45.0)
        out.append({**c, "start": start, "end": stop, "title": None, "why": "strongest voice reaction", "virality": None})
    return out


def refine_pick_endings(picks: List[Dict[str, Any]], candidates: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Endings (viral queue #3/#7) for VOD picks: payoff-tight / loop
    refinement of each pick's end, with the candidate's burst end as the
    payoff and the candidate's transcribed words as the speech evidence.
    Both switches default off — off returns the picks untouched. The
    floor here is the owner's CLIP_MIN_SECONDS: this layer's picks are
    final render spans. No frames are read (the sections download
    later), so the loop rhyme is verbal only."""
    try:
        from ..auto_director.endings import loop_endings_enabled, payoff_tight_enabled, refine_clip_end
    except Exception:
        return picks
    if not (loop_endings_enabled() or payoff_tight_enabled()):
        return picks
    by_id = {c.get("id"): c for c in candidates}
    out = []
    for p in picks:
        c = by_id.get(p.get("id")) or {}
        payoff = float(c.get("end", p.get("onset", p["start"])))
        new_end, rec = refine_clip_end(float(p["start"]), float(p["end"]), payoff_end=payoff,
                                       words=p.get("words") or c.get("words"))
        out.append({**p, "end": round(new_end, 2), "ending": rec} if rec["applied"] else p)
    return out


# --------------------------------------------------------------------------
# 6. Download only the picked pieces
# --------------------------------------------------------------------------

COLD_OPEN_TEASER_S = 2.5


def cold_open(section: Path, payoff_at: float, teaser: float = COLD_OPEN_TEASER_S, min_offset: float = 8.0) -> Optional[Path]:
    """Start the clip on its payoff, then play it from the top (the viral loop:
    the reaction first, the build-up after, the payoff again at the end).
    Only when the payoff is at least ``min_offset`` s in; returns the new file."""
    if os.getenv("LIVE_COLD_OPEN", "on").strip().lower() in ("off", "0", "false", "no") or payoff_at < min_offset:
        return None
    t0 = max(0.0, payoff_at - 0.8)
    out = section.with_name(section.stem + "_cold.mp4")
    graph = (f"[0:v]trim={t0:.2f}:{t0 + teaser:.2f},setpts=PTS-STARTPTS[tv];[0:a]atrim={t0:.2f}:{t0 + teaser:.2f},asetpts=PTS-STARTPTS[ta];"
             f"[0:v]setpts=PTS-STARTPTS[bv];[0:a]asetpts=PTS-STARTPTS[ba];[tv][ta][bv][ba]concat=n=2:v=1:a=1[v][a]")
    result = subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(section), "-filter_complex", graph, "-map", "[v]", "-map", "[a]",
                             "-c:v", "libx264", "-preset", "veryfast", "-crf", "16", "-c:a", "aac", "-b:a", "192k", str(out)],
                            capture_output=True, text=True)
    if result.returncode != 0 or not out.is_file():
        logger.warning("Cold open failed; keeping the plain order: %s", (result.stderr or "")[-200:])
        return None
    return out


def download_section(url: str, start: float, end: float, out: Path, max_height: int = 1080) -> Optional[Path]:
    out.parent.mkdir(parents=True, exist_ok=True)
    fmt = f"bv*[height<={max_height}]+ba/b[height<={max_height}]/b"
    result = _ytdlp("-f", fmt, "--download-sections", f"*{start:.2f}-{end:.2f}", "--force-keyframes-at-cuts",
                    "--merge-output-format", "mp4", "-o", str(out.with_suffix(".%(ext)s")), url)
    produced = [p for p in out.parent.glob(out.stem + ".*") if p.suffix in (".mp4", ".mkv", ".webm")]
    if result.returncode != 0 or not produced:
        logger.warning("Section download failed: %s", (result.stderr or "")[-300:])
        return None
    return produced[0]


# --------------------------------------------------------------------------
# The session (same interface as LiveSession)
# --------------------------------------------------------------------------

class _Status:
    def __init__(self):
        self.running = True
        self.last_error: Optional[str] = None
        self.restarts = 0


@dataclass
class VodPlan:
    clips: int = 10
    candidates_per_clip: int = 3


class VodSession:
    def __init__(self, settings, session_dir: Path, on_event: Callable[[Dict[str, Any]], None]):
        self.settings = settings
        self.dir = Path(session_dir)
        self.on_event = on_event
        self.recorder = _Status()  # the worker reads .running / .last_error / .restarts
        self.moments = 0
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None

    # interface -----------------------------------------------------------
    def start(self) -> "VodSession":
        self.dir.mkdir(parents=True, exist_ok=True)
        self._thread = threading.Thread(target=self._run_safe, name="vod-session", daemon=True)
        self._thread.start()
        return self

    def stop_recording(self) -> None:
        self._stop.set()

    def stop(self, timeout: float = 15.0) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout)

    @property
    def ended(self) -> bool:
        return not self.recorder.running

    def _emit(self, event: Dict[str, Any]) -> None:
        try:
            self.on_event(event)
        except Exception:
            logger.exception("VOD session event handler failed")

    def _progress(self, text: str, seconds: float = 0.0) -> None:
        self._emit({"type": "status", "recording": True, "restarts": 0, "last_error": None,
                    "buffered_seconds": seconds, "moments": self.moments, "progress": text})

    # pipeline ------------------------------------------------------------
    def _run_safe(self) -> None:
        try:
            self._run()
        except Exception as exc:
            logger.exception("VOD session failed")
            self.recorder.last_error = f"{type(exc).__name__}: {exc}"[:300]
        finally:
            self.recorder.running = False

    def _run(self) -> None:
        s = self.settings
        want = max(1, min(int(getattr(s, "max_clips", 10) or 10), 30))
        t0 = time.time()
        self._progress("Reading the video's details")
        info = probe(s.url)
        duration = info["duration"]
        if duration <= 0 or duration > max_vod_hours() * 3600:
            raise RuntimeError(f"video length {duration / 3600:.1f} h is outside 0-{max_vod_hours():.0f} h")
        self._progress(f"Downloading the audio only ({duration / 3600:.1f} h)")
        audio = download_audio(s.url, self.dir / "audio")
        if self._stop.is_set():
            return
        series = scan_audio(audio, duration, workers=int(os.getenv("LIVE_VOD_SCAN_WORKERS", "4")),
                            progress=lambda text: self._progress(text, duration))
        series = add_reaction_features(series, audio, duration,
                                       progress=lambda text: self._progress(text, duration))
        request = (getattr(s, "request", None) or "").strip() or None
        candidates: List[Dict[str, Any]] = []
        if request:
            # "Find moments where...": read the whole stream on the GPU and
            # search it; without a GPU, fall back to more reaction moments.
            self._progress(f'Finding "{request}": preparing the search', duration)
            expansion = expand_request(request)
            segments = transcribe_full(audio, duration, progress=lambda text: self._progress(text, duration))
            if segments:
                candidates = search_windows(segments, expansion, series, info["heatmap"],
                                            want * VodPlan.candidates_per_clip)
                logger.info("VOD search %r: %d segments, %d keywords -> %d candidates",
                            request, len(segments), len(expansion["keywords"]), len(candidates))
        if not candidates:
            candidates = find_candidates(series, info["heatmap"],
                                         want * (VodPlan.candidates_per_clip + (2 if request else 0)))
        # The first minutes of a stream are the intro screen / "starting soon".
        skip = float(os.getenv("LIVE_VOD_SKIP_INTRO_SEC", "300")) if duration > 3600 else 0.0
        candidates = [c for c in candidates if c["onset"] >= skip]
        if not candidates:
            raise RuntimeError("no reactions found in the audio")
        for i, c in enumerate(candidates):
            c["id"] = i + 1
        need = [i for i, c in enumerate(candidates) if not c.get("words")]
        windows = [(max(0.0, candidates[i]["onset"] + CANDIDATE_WINDOW[0]),
                    min(duration, candidates[i]["onset"] + CANDIDATE_WINDOW[1])) for i in need]
        for batch_start in range(0, len(need), BATCH):
            if self._stop.is_set():
                return
            self._progress(f"Reading what was said: {min(batch_start + BATCH, len(need))}/{len(need)} moments", duration)
            batch = list(range(batch_start, min(batch_start + BATCH, len(need))))
            words = transcribe_windows(audio, [windows[j] for j in batch], source_url=s.url)
            for j, w in zip(batch, words):
                candidates[need[j]]["words"] = w
        self._progress(f'Picking the moments that match "{request}"' if request
                       else "Picking the clips most likely to go viral", duration)
        creator = (getattr(s, "credit", None) or "").strip().lstrip("@").replace("credit", "").strip() or None
        picks = llm_pick_batched(candidates, want, request=request, creator=creator)
        if picks is None:
            picks = signal_pick(candidates, series.get("voiced_frac") or [], want)
        if not picks:
            self._progress(f'No moment matched "{request}"', duration)
        picks = refine_pick_endings(picks, candidates)
        logger.info("VOD %s: %d candidates -> %d picks in %.0fs", s.url, len(candidates), len(picks), time.time() - t0)
        from .finisher import render_live_clip

        for pick in sorted(picks, key=lambda p: -(p.get("virality") or p.get("score") or 0)):
            if self._stop.is_set():
                return
            self.moments += 1
            n = self.moments
            self._progress(f"Making clip {n}/{len(picks)}", duration)
            section = download_section(s.url, pick["start"], pick["end"], self.dir / f"window_{n:03d}.mp4")
            # The pick's reaction onset in the rendered file's timeline:
            # its offset inside the section, shifted by the teaser when
            # the cold open prepends one. View sequencing anchors on it.
            peak_offset = float(pick["onset"]) - float(pick["start"])
            if section is not None:
                cold = cold_open(section, peak_offset)
                if cold is not None:
                    section = cold
                    peak_offset += COLD_OPEN_TEASER_S
            stamp = time.strftime("%H:%M:%S", time.gmtime(pick["start"]))
            label = (pick.get("title") or "Reaction") + (f" - {pick['why']}" if pick.get("why") else "")
            # Times are positions in the video, not wall-clock: they travel in
            # the reason text ("[03:12:05] title - why"), the wall fields stay empty.
            moment = {"type": "moment", "n": n, "signals": list(pick.get("signals") or []),
                      "strength": round(float(pick.get("peak", 0)), 2), "talk_share": pick.get("talk"),
                      "reason": f"[{stamp}] {label}"[:300]}
            if section is None:
                self._emit({**moment, "status": "rejected", "reason": f"[{stamp}] could not download this part"})
                continue
            if s.layout_template:
                ok, why = creator_on_camera(section, pick["onset"] - pick["start"], min_ratio=0.0,
                                            cam_box=(s.boxes or {}).get("cam"))
                if not ok:  # face-on-top needs the streamer's camera (intro screens, cutscenes)
                    self._emit({**moment, "status": "rejected", "reason": f"[{stamp}] {why}"})
                    continue
            self._emit({**moment, "status": "queued", "preview_path": str(section)})
            self._emit({"type": "clip", "n": n, "status": "rendering"})
            t = time.time()
            out = self.dir / f"clip_{n:03d}.mp4"
            try:
                quote = ({"text": pick["quote"], "emojis": pick.get("emojis") or [], "creator": creator}
                         if pick.get("quote") else None)
                result = render_live_clip(section, out, niche=s.niche, layout_template=s.layout_template,
                                          credit=s.credit, quote_title=quote,
                                          hook_title=None if quote else (s.hook_title if s.hook_title else None),
                                          streamer_words_only=s.streamer_words_only,
                                          voice_fx=getattr(s, "voice_fx", None), boxes=getattr(s, "boxes", None),
                                          source_url=s.url, source_start=pick["start"],
                                          peak_offset=peak_offset)
            except Exception as exc:
                result = {"ok": False, "error": str(exc)[:300]}
            self._emit({"type": "clip", "n": n, "status": "ready" if result.get("ok") else "failed",
                        "clip_path": str(out) if result.get("ok") else None, "duration": result.get("duration"),
                        "error": result.get("error"), "render_s": round(time.time() - t, 1),
                        **({"comment_question": result["comment_question"]}
                           if result.get("comment_question") else {})})
        try:
            audio.unlink()  # the audio was only needed to find the moments
        except OSError:
            pass
        self._progress(f"Done: {self.moments} clips in {(time.time() - t0) / 60:.0f} min", duration)
