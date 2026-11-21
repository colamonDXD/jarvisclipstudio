"""Render one live window as a finished vertical clip.

The window (already cut to the hook-first span by ``moments.speech_window``)
becomes a single clip through the same engine a task uses: transcript, EDL
for the niche profile, the Auto template's music, the subject-aware reframe
plan, and ``create_optimized_clip``, with the gaming "face on top, game
below" layout when one is chosen. Captions carry the streamer's words only
when ``streamer_words_only`` is set (default for gaming).
"""
from __future__ import annotations

import json
import logging
import subprocess
from pathlib import Path
from typing import Any, Dict, Optional

from .moments import keep_streamer_words

logger = logging.getLogger(__name__)

LAYOUTS = ("facecam_red", "facecam_gold")


def _duration(path: Path) -> float:
    out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0",
                          str(path)], capture_output=True, text=True).stdout.strip()
    try:
        return float(out or 0)
    except ValueError:
        return 0.0


def render_live_clip(window: Path, out_path: Path, *, niche: str = "gaming", layout_template: Optional[str] = "facecam_red",
                     credit: Optional[str] = None, hook_title: Optional[str] = None,
                     streamer_words_only: bool = True, voice_fx: Optional[str] = None,
                     quote_title: Optional[Dict[str, Any]] = None,
                     boxes: Optional[Dict[str, Any]] = None,
                     source_url: Optional[str] = None, source_start: float = 0.0,
                     peak_offset: Optional[float] = None) -> Dict[str, Any]:
    """Render ``window`` into ``out_path``; returns ``{ok, duration, words, error}``."""
    from ..auto_director import edl as E
    from ..media.gaming_templates import clean_layout_credit
    from ..media.music_library import apply_auto_music_bed
    from ..media.reframe_plan import attach_reframe_plan
    from ..media.transcription import get_video_transcript, load_cached_transcript_data
    from ..video_utils import create_optimized_clip

    window, out_path = Path(window), Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    if layout_template not in LAYOUTS:
        layout_template = None
    total = _duration(window)
    if total <= 1.0:
        return {"ok": False, "error": "window is empty"}
    from .groq_transcribe import transcribe_with_groq

    try:
        # Transcript selection: YouTube's own timed captions when the
        # window came from a YouTube VOD (word-timed, no speech model),
        # then Groq's hosted Whisper when the key is set (no CPU load),
        # else the local model. Whichever source wins, the words still go
        # through keep_streamer_words below — YouTube captions carry the
        # game's dialogue exactly like a speech model's transcript does.
        captioned = False
        if source_url:
            from ..media.transcription import cache_transcript_data
            from ..media.yt_captions import fetch_youtube_captions, slice_result

            captions = fetch_youtube_captions(source_url)
            if captions:
                sliced = slice_result(captions, source_start, source_start + total)
                if sliced:
                    cache_transcript_data(window, sliced)
                    captioned = True
        if not captioned and not transcribe_with_groq(window):
            get_video_transcript(window)
        data = load_cached_transcript_data(window) or {}
    except Exception as exc:  # a clip without captions beats no clip
        logger.warning("Live clip transcript failed: %s", exc)
        data = {}
    if streamer_words_only and data.get("words"):
        try:
            data = keep_streamer_words(window, data)
        except Exception as exc:
            logger.warning("Live clip streamer-word filter failed: %s", exc)
    coverage: Optional[Dict[str, Any]] = None
    if data.get("words"):
        try:
            # Timing alignment + the coverage QA gate (media.word_align,
            # adapted from wordcaps): whichever transcript source won —
            # YouTube captions, Groq, local Whisper — its word edges are
            # snapped onto the window's loudness envelope so the karaoke
            # highlight lands on the voice, and the result is checked
            # for speech with no caption over it. The aligned words are
            # written back into the transcript cache, which is what the
            # caption builder reads (the keep_streamer_words pattern).
            from ..media.word_align import align_words_to_audio, caption_coverage_for

            working = []
            for w in data["words"]:
                item = dict(w)
                item["word"] = str(w.get("text", "")).strip()
                item["start"] = float(w.get("start", 0)) / 1000.0
                item["end"] = float(w.get("end", 0)) / 1000.0
                working.append(item)
            aligned, _adjustments = align_words_to_audio(working, window)
            cues: list = []
            for item in aligned:
                if cues and item["start"] - cues[-1]["end"] <= 0.4:
                    cues[-1]["end"] = max(cues[-1]["end"], item["end"])
                else:
                    cues.append({"start": item["start"], "end": item["end"]})
            coverage = caption_coverage_for(window, cues)
            if coverage is not None and not coverage["ok"]:
                logger.warning(
                    "Caption coverage: %.2fs of speech has no caption over it: %s",
                    coverage["uncovered_seconds"], coverage["uncovered"][:5],
                )
            for item in aligned:
                item["start"] = int(round(item["start"] * 1000))
                item["end"] = int(round(item["end"] * 1000))
            data = {**data, "words": aligned}
            cache = window.with_suffix(".transcript_cache.json")
            if cache.exists():
                cached = json.loads(cache.read_text(encoding="utf-8"))
                cached["words"] = aligned
                cache.write_text(json.dumps(cached), encoding="utf-8")
        except Exception as exc:
            logger.warning("Live clip word alignment failed: %s", exc)
    if data.get("words"):
        try:
            # Caption-word polish (media.caption_polish): after alignment,
            # before the caption build — the optional LLM tidy of ASR word
            # errors (CAPTION_WORD_FIX, PeakCut) and the deterministic
            # Devanagari -> Hinglish transliteration (TRANSLIT_HINGLISH,
            # NeonClipping), both off by default. Only word text may
            # change; timings are never touched. Polished words are
            # written back into the transcript cache the caption builder
            # reads, exactly like the aligned words above.
            from ..media.caption_polish import polish_caption_words, polish_needed

            if polish_needed():
                polished = polish_caption_words(data["words"], title=hook_title or "")
                if polished is not data["words"]:
                    data = {**data, "words": polished}
                    cache = window.with_suffix(".transcript_cache.json")
                    if cache.exists():
                        cached = json.loads(cache.read_text(encoding="utf-8"))
                        cached["words"] = polished
                        cache.write_text(json.dumps(cached), encoding="utf-8")
        except Exception as exc:
            logger.warning("Live clip caption polish failed: %s", exc)
    words = [{"word": str(w.get("text", "")).strip(), "text": str(w.get("text", "")).strip(),
              "start": float(w.get("start", 0)) / 1000.0, "end": float(w.get("end", 0)) / 1000.0}
             for w in data.get("words") or []]
    profile = E.load_profile(niche)
    clip_info = {"id": out_path.stem, "start": 0.0, "end": total, "duration": total}
    edl = E.build_edl(clip_info, words, profile, energy_peaks=[], broll_enabled=False)
    if peak_offset is not None:
        # The caller's recorded reaction onset, in this window's own
        # timeline: view sequencing (VIEW_SEQUENCING) anchors on it via
        # media.view_sequencing.peak_from_edl. Absent, the layout choice
        # falls back to the window's loudest second.
        edl["peak_energy_timestamp"] = float(peak_offset)
    apply_auto_music_bed(edl, niche)
    if quote_title:
        edl["quote_title"] = quote_title  # drawn by media/hook_finish.py
    # Comment bait (auto_director/comment_bait.py): with COMMENT_BAIT on,
    # the clip gets its pinned-comment question from its own words — the
    # question rides the EDL into the render (media/end_question.py
    # burns it as the end card) and is returned with the result so the
    # session record carries it. Off (the default) nothing is generated
    # and the result below keeps its historical shape exactly.
    comment_question: Optional[str] = None
    try:
        from ..auto_director import comment_bait

        if comment_bait.comment_bait_enabled():
            creator = (credit or "").strip()
            if creator.lower().startswith("credit"):
                creator = creator[len("credit"):].strip()
            comment_question = comment_bait.generate_comment_question_sync(
                niche=niche, hook_title=hook_title,
                transcript_text=" ".join(w["text"] for w in words),
                creator=creator.lstrip("@") or None)
            if comment_question:
                edl["comment_question"] = comment_question
    except Exception as exc:  # a clip without its question beats no clip
        logger.warning("Comment bait failed: %s", exc)
        comment_question = None
    from ..media.gaming_templates import MANUAL_BOXES

    token = MANUAL_BOXES.set(boxes if layout_template else None)
    layout_report: Dict[str, Any] = {}
    try:
        attach_reframe_plan(edl, window, [(0.0, total)], False, framing=None)
        ok = create_optimized_clip(window, 0.0, total, out_path, add_subtitles=True,
                               caption_template=(profile.get("captions") or {}).get("preset_id") or "default",
                               hook_title=hook_title, edl=edl, profile_name=niche, layout_report=layout_report,
                               framing_mode="auto", layout_template=layout_template,
                               layout_credit=clean_layout_credit(credit) if layout_template else None)
    finally:
        MANUAL_BOXES.reset(token)
    ok = bool(ok) and out_path.is_file()
    if ok and voice_fx:
        from ..media.voice_fx import apply_voice_fx

        apply_voice_fx(out_path, voice_fx)
    result: Dict[str, Any] = {"ok": ok, "duration": round(_duration(out_path), 2) if ok else None,
                             "words": len(words), "caption_coverage": coverage,
                             "beat_density": layout_report.get("beat_density"),
                             "error": None if ok else "render failed"}
    if comment_question:
        result["comment_question"] = comment_question
    return result
