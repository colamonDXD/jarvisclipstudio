"""Candidate-first discovery for long Create jobs ("Faster generation").

A long source is never transcribed end to end before selection. The audio
is scanned for the streamer's own reactions, the strongest moments are
ranked, and only short windows around those moments are transcribed. The
picked footage is then fetched/stitched and handed to the normal Create
renderer, so layouts, drawn boxes, captions, music and the gaming
reaction-timing treatment all stay on the existing path.

The sparse candidate transcript is written only next to the stitched
render file. The original source's transcript cache is never populated:
a later detailed-mode job must not reuse a discovery sample as if it were
a complete source transcript.
"""
from __future__ import annotations

import asyncio
import json
import logging
import math
import os
import shutil
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Awaitable, Callable, Dict, List, Optional, Sequence

from . import vod
from ..auto_director.creation_modes import FULL_AUTO
from ..clip_duration import clip_duration
from ..media.common import TRANSCRIPT_CACHE_SCHEMA_VERSION
from ..media.timeline import seconds_to_mmss
from ..youtube_source import (
    CachedPiece,
    assemble_timeline,
    merge_ranges,
    ranges_from_segments,
    shift_transcript,
)

logger = logging.getLogger(__name__)

DEFAULT_THRESHOLD_SECONDS = 1800.0
DEFAULT_MAX_CANDIDATES = 60
DEFAULT_INITIAL_EXTRA = 6
DEFAULT_RESERVE_BATCH = 12
DEFAULT_SCAN_WORKERS = 2
WINDOW_BATCH = 5


class FastDiscoveryError(RuntimeError):
    """Discovery could not produce a usable plan; the caller reports it."""


def enabled() -> bool:
    return os.getenv("CREATE_FAST_DISCOVERY", "on").lower() in {"1", "true", "on", "yes"}


def threshold_seconds() -> float:
    try:
        return max(60.0, float(os.getenv("CREATE_FAST_DISCOVERY_SECONDS", str(DEFAULT_THRESHOLD_SECONDS))))
    except ValueError:
        return DEFAULT_THRESHOLD_SECONDS


def requested(
    *,
    processing_mode: str,
    creation_mode: Optional[str],
    manual_selection: bool,
    checkpoint: Optional[Dict[str, Any]] = None,
    content_brief: Optional[Dict[str, Any]] = None,
    cached_transcript: Optional[str] = None,
    cached_analysis_json: Optional[str] = None,
) -> bool:
    """Whether the job's mode allows candidate-first discovery at all."""
    if not enabled():
        return False
    if processing_mode != "fast":
        return False
    if manual_selection or creation_mode != FULL_AUTO:
        return False
    if content_brief:
        return False
    if cached_transcript or cached_analysis_json:
        return False
    checkpoint = checkpoint or {}
    if any(checkpoint.get(key) for key in (
        "transcript", "analysis", "segments", "candidates", "analysis_windows"
    )):
        return False
    return True


def eligible(
    *,
    processing_mode: str,
    creation_mode: Optional[str],
    manual_selection: bool,
    duration: Optional[float],
    checkpoint: Optional[Dict[str, Any]] = None,
    content_brief: Optional[Dict[str, Any]] = None,
    cached_transcript: Optional[str] = None,
    cached_analysis_json: Optional[str] = None,
) -> bool:
    """Whether this job belongs on the candidate-first path.

    Deliberately conservative: only a fresh, full-auto, faster-generation
    job with a known long duration qualifies. Manual cuts, Find-Moments
    review, content briefs, quality mode and any cached transcript or
    analysis keep the detailed pipeline untouched.
    """
    if not requested(
        processing_mode=processing_mode,
        creation_mode=creation_mode,
        manual_selection=manual_selection,
        checkpoint=checkpoint,
        content_brief=content_brief,
        cached_transcript=cached_transcript,
        cached_analysis_json=cached_analysis_json,
    ):
        return False
    if not duration or not math.isfinite(float(duration)):
        return False
    if float(duration) < threshold_seconds():
        return False
    return True


def _window(prompt_max: float) -> tuple[float, float]:
    """Transcript context around a reaction onset: enough for the hook,
    the payoff and the requested clip length, never a whole stream."""
    before = 15.0
    after = min(75.0, max(35.0, prompt_max + 15.0))
    return -before, after


def _join_words(words: Sequence[Dict[str, Any]], start: float, end: float) -> str:
    picked = [
        str(word.get("text", "")).strip()
        for word in words
        if float(word.get("end", 0)) >= start - 1.0
        and float(word.get("start", 0)) <= end + 1.0
        and str(word.get("text", "")).strip()
    ]
    return " ".join(picked)


def _fit_pick(
    pick: Dict[str, Any],
    *,
    duration: float,
    min_len: float,
    max_len: float,
    words: Sequence[Dict[str, Any]],
) -> Optional[Dict[str, Any]]:
    """Clamp a model/signal pick to the task's requested clip length."""
    try:
        onset = float(pick.get("onset", pick.get("start", 0.0)))
        start = float(pick.get("start", onset))
        end = float(pick.get("end", onset + min_len))
    except (TypeError, ValueError):
        return None
    if not math.isfinite(start) or not math.isfinite(end):
        return None
    start = max(0.0, min(start, max(0.0, duration - 1.0)))
    end = max(start + 1.0, min(end, duration))

    # Snap the edges onto nearby transcribed words when possible: a clip
    # should start on speech, not in the silence before it.
    starts = [
        float(word["start"]) for word in words
        if start - 2.5 <= float(word.get("start", 0)) <= start + 3.0
    ]
    ends = [
        float(word["end"]) for word in words
        if end - 3.0 <= float(word.get("end", 0)) <= end + 2.5
    ]
    if starts:
        start = min(starts, key=lambda value: abs(value - start))
    if ends:
        end = min(ends, key=lambda value: abs(value - end))

    if end - start < min_len:
        end = min(duration, start + min_len)
        if end - start < min_len:
            start = max(0.0, end - min_len)
    if end - start > max_len:
        end = start + max_len
    # Keep the reaction onset inside the clip, with a small lead-in.
    if onset < start:
        start = max(0.0, min(onset - 1.0, end - min_len))
    if onset > end - 1.0:
        end = min(duration, onset + min_len)
        start = max(0.0, end - max_len)
    if end - start < min_len:
        return None
    return {
        **pick,
        "start": round(start, 2),
        "end": round(end, 2),
    }


def _dedupe_picks(picks: List[Dict[str, Any]], duration: float) -> List[Dict[str, Any]]:
    """Keep the best-ranked non-overlapping picks, in rank order."""
    kept: List[Dict[str, Any]] = []
    for pick in picks:
        start, end = float(pick["start"]), float(pick["end"])
        if start < 0 or end > duration + 0.5 or end <= start:
            continue
        if any(start < other["end"] - 0.5 and end > other["start"] + 0.5 for other in kept):
            continue
        kept.append(pick)
    return kept


def _sparse_payload(words: Sequence[Dict[str, Any]], text: str = "") -> Dict[str, Any]:
    return {
        "version": TRANSCRIPT_CACHE_SCHEMA_VERSION,
        "words": [
            {
                "text": str(word.get("text", "")),
                "start": int(round(float(word.get("start", 0.0)) * 1000)),
                "end": int(round(float(word.get("end", 0.0)) * 1000)),
                "confidence": float(word.get("confidence", 1.0)),
                "speaker": None,
            }
            for word in words
            if str(word.get("text", "")).strip()
        ],
        "utterances": [],
        "text": text,
        "language_code": None,
    }


def write_sparse_sidecar(
    dest: Path,
    words: Sequence[Dict[str, Any]],
    *,
    time_map=None,
    text: str = "",
) -> None:
    """Write discovery words beside the stitched render file only."""
    payload = _sparse_payload(words, text)
    if time_map is not None:
        payload = shift_transcript(payload, time_map)
    dest.with_suffix(".transcript_cache.json").write_text(
        json.dumps(payload), encoding="utf-8"
    )


def stitch_local(
    source: Path,
    segments: Sequence[Dict[str, Any]],
    dest: Path,
    *,
    duration: float,
    padding: float,
):
    """Cut the selected ranges out of an already-local source."""
    ranges = merge_ranges(
        ranges_from_segments(segments), padding=padding, duration=duration
    )
    if not ranges:
        raise FastDiscoveryError("No valid ranges to stitch from the local source")
    dest.parent.mkdir(parents=True, exist_ok=True)
    piece = CachedPiece(0.0, float(duration), Path(source))
    return assemble_timeline([(piece, start, end) for start, end in ranges], dest)


async def _classify(
    text: str,
    *,
    title: Optional[str],
    description: Optional[str],
) -> Optional[Dict[str, Any]]:
    try:
        from ..auto_director.classify import classify_niche, guardrail_evidence_sample

        sample = (text or "").strip()[:2000]
        if not sample and not title:
            return None
        # Text evidence only: discovery already has the words, and the
        # render path's classification can use the stitched file later
        # without re-reading the whole source.
        return await classify_niche(
            sample,
            [],
            title=title,
            description=description,
            guardrail_sample=guardrail_evidence_sample(text or ""),
        )
    except Exception:
        logger.warning("Fast-discovery niche classification failed", exc_info=True)
        return None


async def discover(
    *,
    audio_path: Path,
    duration: float,
    target_count: Optional[int],
    clip_length: str,
    layout_template: Optional[str],
    boxes: Optional[Dict[str, Any]],
    source_title: Optional[str] = None,
    source_description: Optional[str] = None,
    project_niche_override: Optional[str] = None,
    creator_name: Optional[str] = None,
    source_url: Optional[str] = None,
    heatmap: Optional[Sequence[Dict[str, float]]] = None,
    progress: Optional[Callable[[int, str], Awaitable[None]]] = None,
    should_cancel: Optional[Callable[[], Awaitable[bool]]] = None,
    checkpoint: Optional[Dict[str, Any]] = None,
    save_checkpoint: Optional[Callable[[Dict[str, Any]], Awaitable[None]]] = None,
) -> Dict[str, Any]:
    """Rank moments, transcribe short windows, and return a Create plan."""
    started = time.time()
    want = max(1, min(int(target_count or 10), 50))
    bounds = clip_duration(clip_length)
    prompt_min = float(bounds.ideal_minimum)
    prompt_max = float(bounds.ideal_maximum)
    fit_min = float(bounds.minimum)
    fit_max = float(bounds.maximum)
    before, after = _window(prompt_max)
    loop = asyncio.get_running_loop()
    phase = {"pct": 12}
    report: Dict[str, Any] = {
        "mode": "fast_discovery",
        "threshold_seconds": threshold_seconds(),
        "duration_seconds": round(float(duration), 2),
        "clip_length": clip_length,
        "requested_clips": want,
        "candidate_windows_transcribed": 0,
        "candidate_audio_seconds": 0.0,
        "reserve_rounds": 0,
        "picks_selected": 0,
    }

    async def announce(message: str, pct: Optional[int] = None) -> None:
        if pct is not None:
            phase["pct"] = pct
        if progress is not None:
            await progress(phase["pct"], message)

    def thread_progress(message: str) -> None:
        if progress is None:
            return
        try:
            asyncio.run_coroutine_threadsafe(progress(phase["pct"], message), loop)
        except RuntimeError:
            pass

    def cancelled() -> bool:
        if should_cancel is None:
            return False
        try:
            return bool(asyncio.run_coroutine_threadsafe(should_cancel(), loop).result(timeout=5))
        except Exception:
            return False

    async def check_cancel() -> None:
        if should_cancel is not None and await should_cancel():
            raise FastDiscoveryError("Task cancelled")

    # 1. Reaction scan -----------------------------------------------------
    await announce("Scanning the audio for reactions...", 12)
    workers = int(os.getenv("CREATE_FAST_SCAN_WORKERS", str(DEFAULT_SCAN_WORKERS)))
    scan_started = time.time()
    series = await asyncio.to_thread(
        vod.scan_audio,
        Path(audio_path),
        float(duration),
        workers=max(1, min(4, workers)),
        progress=thread_progress,
        should_stop=cancelled,
    )
    await check_cancel()
    if not series.get("vocal"):
        raise FastDiscoveryError("The audio scan returned no voice evidence")
    report["scan_seconds"] = round(time.time() - scan_started, 2)

    await announce("Adding reaction features...", 20)
    series = await asyncio.to_thread(
        vod.add_reaction_features,
        series,
        Path(audio_path),
        float(duration),
        progress=thread_progress,
    )
    await check_cancel()

    skip = (
        max(0.0, float(os.getenv("LIVE_VOD_SKIP_INTRO_SEC", "300")))
        if duration > 3600 else 0.0
    )
    max_candidates = max(
        want + DEFAULT_INITIAL_EXTRA,
        min(int(os.getenv("CREATE_FAST_MAX_CANDIDATES", str(DEFAULT_MAX_CANDIDATES))), DEFAULT_MAX_CANDIDATES),
    )
    candidates = await asyncio.to_thread(
        vod.find_candidates,
        series,
        list(heatmap or []),
        max_candidates,
        skip,
    )
    candidates = [candidate for candidate in candidates if candidate["onset"] >= skip]
    if not candidates:
        raise FastDiscoveryError(
            "No reaction moments were found in this source; detailed selection "
            "may still find moments."
        )
    for index, candidate in enumerate(candidates):
        candidate["id"] = index + 1
    report["candidate_count"] = len(candidates)
    await announce(
        f"Ranked {len(candidates)} candidate moments; transcribing the strongest",
        24,
    )

    # 2. Window transcription, in rounds so reserves are only paid for
    #    when the first slate is short. -----------------------------------
    transcribed: List[int] = []
    rounds = 0

    async def transcribe(indices: Sequence[int]) -> None:
        pending = [index for index in indices if not candidates[index].get("words")]
        total = len(pending)
        done = 0
        for batch_start in range(0, total, WINDOW_BATCH):
            await check_cancel()
            batch = pending[batch_start:batch_start + WINDOW_BATCH]
            windows = [
                (
                    max(0.0, candidates[index]["onset"] + before),
                    min(float(duration), candidates[index]["onset"] + after),
                )
                for index in batch
            ]
            await announce(
                f"Transcribing window {done + 1}/{total}", 24 + int(20 * done / max(1, total))
            )
            batch_report: Dict[str, Any] = {}
            words = await asyncio.to_thread(
                vod.transcribe_windows,
                Path(audio_path),
                windows,
                source_url if source_url and source_url.startswith("http") else None,
                batch_report,
                cancelled,
            )
            for index, window, spoken in zip(batch, windows, words):
                candidates[index]["words"] = spoken
                report["candidate_windows_transcribed"] += 1
                report["candidate_audio_seconds"] += max(0.0, window[1] - window[0])
                transcribed.append(index)
            decoder = batch_report.get("window_decoder")
            if decoder:
                report["window_decoder"] = decoder
            if batch_report.get("window_decoder_error"):
                report["window_decoder_error"] = batch_report["window_decoder_error"]
            done += len(batch)
            await announce(f"Transcribing window {done}/{total}", phase["pct"])
        if save_checkpoint is not None:
            try:
                await save_checkpoint({"fast_candidates": candidates})
            except Exception:
                logger.warning("Could not checkpoint fast-discovery candidates", exc_info=True)

    def pick() -> List[Dict[str, Any]]:
        spoken = [candidate for candidate in candidates if candidate.get("words")]
        if not spoken:
            spoken = list(candidates)
        eligible_candidates = spoken
        if layout_template and vod.needs_facecam(layout_template, boxes):
            eligible_candidates = vod.prefer_spoken_candidates(spoken, want)
        picks = vod.llm_pick_batched(
            eligible_candidates,
            want,
            creator=creator_name,
            min_len=prompt_min,
            max_len=prompt_max,
        )
        if picks is None:
            picks = vod.signal_pick(
                eligible_candidates,
                series.get("voiced_frac") or [],
                want,
                min_len=fit_min,
                max_len=fit_max,
            )
        elif len(picks) < want:
            selected = {pick_item.get("id") for pick_item in picks}
            remaining = [
                candidate for candidate in eligible_candidates
                if candidate.get("id") not in selected
            ]
            picks.extend(
                vod.signal_pick(
                    remaining,
                    series.get("voiced_frac") or [],
                    want - len(picks),
                    min_len=fit_min,
                    max_len=fit_max,
                )
            )
        return vod.refine_pick_endings(picks, candidates)

    initial = min(len(candidates), want + DEFAULT_INITIAL_EXTRA)
    await transcribe(range(initial))
    rounds = 1
    await announce("Selecting clips from the transcribed moments", 45)
    picks = await asyncio.to_thread(pick)
    while len(picks) < want and len(transcribed) < len(candidates) and rounds < 4:
        await check_cancel()
        rounds += 1
        start = len(transcribed)
        batch = min(DEFAULT_RESERVE_BATCH, len(candidates) - start)
        await announce(
            f"Trying {batch} reserve moments ({len(picks)}/{want} picked)",
            44 + min(8, rounds * 2),
        )
        await transcribe(range(start, start + batch))
        picks = await asyncio.to_thread(pick)
    report["reserve_rounds"] = max(0, rounds - 1)

    # 3. Fit the requested clip length and build Create segments ----------
    all_words: List[Dict[str, Any]] = []
    seen_words = set()
    for candidate in candidates:
        for word in candidate.get("words") or []:
            key = (
                str(word.get("text", "")),
                round(float(word.get("start", 0.0)), 2),
                round(float(word.get("end", 0.0)), 2),
            )
            if key in seen_words:
                continue
            seen_words.add(key)
            all_words.append(word)
    all_words.sort(key=lambda word: float(word.get("start", 0.0)))

    fitted: List[Dict[str, Any]] = []
    for pick_item in picks:
        fitted_pick = _fit_pick(
            pick_item,
            duration=float(duration),
            min_len=fit_min,
            max_len=fit_max,
            words=all_words,
        )
        if fitted_pick is not None:
            fitted.append(fitted_pick)
    fitted = _dedupe_picks(fitted, float(duration))[:want]

    segments: List[Dict[str, Any]] = []
    for pick_item in fitted:
        start, end = float(pick_item["start"]), float(pick_item["end"])
        text = _join_words(all_words, start, end)
        title = pick_item.get("quote") or pick_item.get("title")
        virality = pick_item.get("virality")
        if virality is None:
            virality = min(100.0, max(0.0, float(pick_item.get("score") or 0.0)))
        segments.append({
            "start_time": seconds_to_mmss(start),
            "end_time": seconds_to_mmss(end),
            "text": text,
            "relevance_score": round(float(virality) / 100.0, 3),
            "reasoning": (
                f"Fast discovery picked this ranked reaction"
                + (f": {pick_item['why']}" if pick_item.get("why") else "")
            )[:300],
            "virality_score": virality,
            "hook_score": 0,
            "engagement_score": 0,
            "value_score": 0,
            "shareability_score": 0,
            "hook_type": None,
            "hook_title": title,
            "hook_start_time": None,
            "hook_end_time": None,
        })

    if not segments:
        raise FastDiscoveryError(
            f"Fast discovery found {len(candidates)} candidates but none fit a "
            f"{bounds.minimum}-{bounds.maximum}s clip."
        )

    transcript_text = "\n".join(
        f"[{segment['start_time']}] {segment['text']}" for segment in segments
        if segment.get("text")
    )
    classification = None
    if not project_niche_override:
        classification = await _classify(
            transcript_text,
            title=source_title,
            description=source_description,
        )
    if classification and not classification.get("is_fallback"):
        niche = classification.get("niche")
        confidence = classification.get("confidence")
        for segment in segments:
            if niche:
                segment["classifier_niche"] = niche
                segment["classifier_confidence"] = confidence

    report.update(
        picks_selected=len(segments),
        candidate_audio_minutes=round(report["candidate_audio_seconds"] / 60.0, 2),
        discovery_seconds=round(time.time() - started, 2),
    )
    if len(segments) < want:
        report["shortfall_reason"] = (
            f"Only {len(segments)} of {want} requested clips fit the source "
            f"from {len(candidates)} ranked candidates."
        )
    logger.info(
        "Fast discovery: %d candidates, %d windows (%.1f min audio), "
        "%d/%d clips in %.1fs",
        len(candidates),
        report["candidate_windows_transcribed"],
        report["candidate_audio_seconds"] / 60.0,
        len(segments),
        want,
        report["discovery_seconds"],
    )
    return {
        "segments": segments,
        "picks": fitted,
        "report": report,
        "classification": classification,
        "summary": (
            f"Fast discovery ranked {len(candidates)} moments and selected "
            f"{len(segments)} of {want} requested clips."
        ),
        "key_topics": [],
        "transcript_text": transcript_text,
        "words": all_words,
    }


def work_dir(task_id: Optional[str]) -> Path:
    from ..config import get_config

    root = Path(get_config().temp_dir) / "fast_create"
    return root / (task_id or f"unscoped-{os.getpid()}")
