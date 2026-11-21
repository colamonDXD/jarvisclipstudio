"""Shared media dependencies and rendering constants."""

from pathlib import Path

from typing import List, Dict, Any, Tuple, Optional

import logging

import numpy as np

from concurrent.futures import ThreadPoolExecutor

import json

import os

import re

import uuid

import shutil

import subprocess

import tempfile

import time

import cv2

import assemblyai as aai

import httpx

import srt

from datetime import timedelta

try:
    import whisper as _whisper

    _WHISPER_AVAILABLE = True
except ImportError:  # pragma: no cover - optional transcription backend
    _whisper = None
    _WHISPER_AVAILABLE = False

try:
    import ctranslate2 as _ctranslate2
    import faster_whisper as _faster_whisper

    _FASTER_WHISPER_AVAILABLE = True
except ImportError:  # pragma: no cover - optional transcription backend
    _ctranslate2 = None
    _faster_whisper = None
    _FASTER_WHISPER_AVAILABLE = False

from ..config import get_config

from ..clip_cleanup import DEFAULT_FILTERED_WORDS, clip_cleanup_enabled

from ..clip_source_map import (
    normalize_source_ranges,
    save_clip_source_ranges,
    source_range_bounds,
)

from ..caption_templates import get_template, CAPTION_TEMPLATES

from ..emoji_captions import POWER_WORDS, annotate_caption_words, normalize_token

from ..font_registry import FONTS_DIR, find_font_path, get_font_family_name

logger = logging.getLogger(__name__)

TRANSCRIPT_CACHE_SCHEMA_VERSION = 2

VALID_OUTPUT_FORMATS = {"vertical", "vertical_fit", "vertical_pan", "vertical_split", "original"}

EMOJI_FONT_NAME = "Noto Color Emoji"

CLIP_END_SENTENCE_EXTENSION_SECONDS = 5.5

# Matches the analysis cap (ai.MAX_ACCEPTED_CLIP_SECONDS). Rewinding a weak
# opening or finishing a sentence must not turn an accepted clip into a longer one.
MAX_RENDERED_CLIP_SECONDS = 60.0

CLIP_END_PADDING_SECONDS = 0.35

# The end padding stops this far before the next spoken word, so a finished
# sentence never picks up the first syllable of the next one.
CLIP_END_NEXT_WORD_GUARD_SECONDS = 0.03

# When the duration cap lands mid-sentence, the end may back off this share of
# the cap (at least 3s) to the previous sentence end instead of cutting mid-thought.
CLIP_CAP_SENTENCE_BACKOFF_FRACTION = 0.25

CLIP_START_SNAP_WINDOW_SECONDS = 3.0

CLIP_START_LEAD_IN_SECONDS = 0.1

SENTENCE_PAUSE_BOUNDARY_SECONDS = 0.8

HOOK_TEASER_MIN_SECONDS = 1.5

HOOK_TEASER_MAX_SECONDS = 5.0

HOOK_TEASER_MIN_OFFSET_SECONDS = 3.0

# Long pauses are compressed rather than cut out, so speech keeps a natural
# breath between phrases. A junction keeps PAUSE_EDGE_PADDING_SECONDS of silence
# on each side of the cut; with the junction crossfade (at most
# CROSSFADE_MAX_SECONDS, which overlaps the two sides) about
# PAUSE_RETAINED_SILENCE_SECONDS of silence remains audible. The padding is never
# shorter than the crossfade, so the fade only ever blends silence, never words.
PAUSE_RETAINED_SILENCE_SECONDS = 0.4

CROSSFADE_MAX_SECONDS = 0.15

PAUSE_EDGE_PADDING_SECONDS = (PAUSE_RETAINED_SILENCE_SECONDS + CROSSFADE_MAX_SECONDS) / 2

# Dead air kept before the first word and after the last word of a clip. The
# tail is a little longer because word end timings often land before the final
# consonant has decayed.
PAUSE_LEAD_IN_SECONDS = 0.1

PAUSE_TAIL_SECONDS = 0.25

SENTENCE_END_RE = re.compile(r"""[.!?]["')\]}]*$""")

# Legacy hook-title cap. No longer applied anywhere: since 2026-11-09 the
# hook title is pinned for the whole clip by default, and an explicit
# shorter duration flows through the EDL (hook.title_seconds) instead.
# Kept defined for compatibility with older imports.
HOOK_TITLE_SECONDS = 4.0

HOOK_TITLE_MIN_SECONDS = 1.5

HOOK_TITLE_SIDE_MARGIN_FRAC = 0.07

# Clears the platform's top UI (tabs, search) with room to spare.
HOOK_TITLE_TOP_MARGIN_FRAC = 0.12

ANALYSIS_SEGMENT_MIN_WORDS = 8

ANALYSIS_SEGMENT_MAX_WORDS = 8

ANALYSIS_SEGMENT_MAX_DURATION_MS = 12_000

ANALYSIS_LONG_UTTERANCE_MAX_WORDS = 24

ANALYSIS_LONG_UTTERANCE_MAX_DURATION_MS = 20_000

ANALYSIS_UTTERANCE_SPLIT_THRESHOLD_MS = 45_000

ANALYSIS_UTTERANCE_SPLIT_THRESHOLD_WORDS = 80

_WHISPER_MODEL_CACHE: Dict[str, Any] = {}

_FASTER_WHISPER_MODEL_CACHE: Dict[str, Any] = {}

FINAL_VIDEO_CRF = 19

FINAL_VIDEO_PRESET = "medium"

# NVENC counterpart of the libx264 final pass: p4 is NVENC's balanced
# quality/speed preset, and CQ runs a few points above CRF for a comparable
# bitrate on typical sources (CRF 19 -> CQ 22, the value the EDL path used).
FINAL_VIDEO_NVENC_PRESET = "p4"

FINAL_VIDEO_NVENC_CQ_OFFSET = 3

# The EDL path's NVENC CQ. That path has no CRF to offset from, so the
# value is stated directly; it matches CRF 19 + FINAL_VIDEO_NVENC_CQ_OFFSET,
# the quality point the plain final-encode path uses.
NVENC_BALANCED_CQ = 22

# RENDER_PROFILE=speed values: a slightly cheaper final encode in exchange
# for wall time -- NVENC preset p2 / CQ 24, libx264 CRF 21 (veryfast).
# Balanced (the default) never touches the caller's quality settings.
NVENC_SPEED_PRESET = "p2"

NVENC_SPEED_CQ = 24

CPU_SPEED_PRESET = "veryfast"

CPU_SPEED_CRF = 21

# NVENC pre-stage (intermediate stitch) encode: the visually-lossless-class
# counterpart of the libx264 CRF 16 intermediate. NVENC CQ runs a few
# points above x264 CRF for a comparable bitrate
# (FINAL_VIDEO_NVENC_CQ_OFFSET), so CQ 18 keeps a one-point quality
# margin over the CRF 16 + offset = CQ 19 point -- the pre-stage is
# re-encoded by the main pass, so its quality must survive a second
# encode. Preset p5 is NVENC's balanced quality/speed point; p6 buys
# little visible quality for a measurably slower intermediate, which
# would give back the wall-clock this change exists to save.
PRESTAGE_NVENC_PRESET = "p5"

PRESTAGE_NVENC_CQ = 18


def record_pass_timing(
    timings: Optional[Dict[str, Any]],
    key: str,
    started: float,
    *,
    accumulate: bool = False,
) -> None:
    """Record one measured render pass into ``timings`` (F-P3).

    ``started`` is a ``time.perf_counter()`` reading taken before the
    pass ran; the stored value is the real elapsed wall-clock in
    seconds, rounded to milliseconds. With ``accumulate`` the elapsed
    time is added to any value already stored under ``key`` (a pass
    that ran several times, e.g. an encode retried on a fallback
    encoder).

    Observation only, by contract: timing must never cost a render, so
    a missing dict, a broken store or a bad ``started`` value are all
    swallowed silently.
    """
    if timings is None or started is None:
        return
    try:
        elapsed = round(max(time.perf_counter() - float(started), 0.0), 3)
        if accumulate:
            elapsed = round(float(timings.get(key, 0.0)) + elapsed, 3)
        timings[key] = elapsed
    except Exception:
        pass


def get_render_profile() -> str:
    """Active render profile: "speed" or "balanced" (RENDER_PROFILE env).

    Unset means balanced. Unknown values fall back to balanced with a
    warning: a typo must never silently change encode quality.
    """
    raw = os.environ.get("RENDER_PROFILE", "").strip().lower()
    if raw in ("", "balanced"):
        return "balanced"
    if raw == "speed":
        return "speed"
    logger.warning("Unknown RENDER_PROFILE %r; using the balanced profile", raw)
    return "balanced"

INTERMEDIATE_CRF = 16

OUTPUT_FPS = 30

AUDIO_BITRATE = "192k"

LOUDNORM_FILTER = "loudnorm=I=-14:TP=-1.5:LRA=11"

_EMOJI_SUPPORT_CACHE: Optional[bool] = None

# Face-presence area floors: the smallest face box, as a fraction of
# frame area, that counts as a face being present. Split per path —
# the two paths were measured separately and the values are NOT
# interchangeable (MIN_AREA_SPIKE.md, 2026-11-17):
#
# - Speaker path (FACE_PRESENCE_MIN_AREA, speaker_framing.py): stays
#   at the historical 0.002. The spike's corpus held no podcast
#   two-shots, so this path is unmeasured; do not lower it — and do
#   not "unify" the two constants — without that measurement.
# - Reframing path (REFRAME_FACE_PRESENCE_MIN_AREA): 0.0005. Measured
#   on 469 real frames: of the YuNet boxes the 0.002 floor discarded,
#   95% of those pointing at scene content were real faces (wide-shot
#   presenters, helmet visors, night groups); 0.0005 recovers 89% of
#   them at negligible cost. The primary layout planners bind on
#   their own higher bars (context 0.008 rule, track bars
#   0.004/0.002), so the lower floor moves the evidence layer, not
#   layout decisions. BOTH reframing sites must use this one value
#   together — the _detect_face_boxes post-filter and
#   build_layout_plan's presence re-test: a detection-only change is
#   silently nullified by the re-test.
FACE_PRESENCE_MIN_AREA = 0.002

REFRAME_FACE_PRESENCE_MIN_AREA = 0.0005

FACE_RATE_WINDOW = 2.0

FACE_PRESENCE_RATE = 0.25

MIN_LAYOUT_SECONDS = 1.5

LAYOUT_SNAP_WINDOW = 0.6

# Readable fit: while a content shot's detail sits inside part of the width
# (code in an editor window, a diagram on a plain background), the fitted
# foreground crops the empty margins and grows instead of staying a 16:9 strip.
# Zoom steps through FIT_VISIBLE_LEVELS (fraction of the width kept), each zoom
# holds for at least FIT_ZOOM_HOLD_SECONDS, and a clip uses at most
# FIT_MAX_ZOOM_LEVELS sizes. FIT_DETAIL_MARGIN_FRAC pads the detail per side.
FIT_VISIBLE_LEVELS = (0.6, 0.7, 0.8, 0.9)

FIT_DETAIL_MARGIN_FRAC = 0.03

FIT_ZOOM_HOLD_SECONDS = 5.0

FIT_MAX_ZOOM_LEVELS = 3

# Tallest zoomed foreground: clears the hook title above and captions at 0.80.
FIT_MAX_FOREGROUND_H = 1040

KENBURNS_ZOOM_DELTA = 0.05
# Vertical point the punch-in zooms toward (0 = top, 1 = bottom): slightly
# above centre, where faces sit.
KENBURNS_FOCUS_Y = 0.35

KENBURNS_MIN_SECONDS = 3.0

KENBURNS_SUPERSAMPLE_W = 1620

KENBURNS_SUPERSAMPLE_H = 2880

# Mild luma-only sharpen applied after the 9:16 upscale; vertical crops are
# usually stretched 1.3-2.7x from the source, which softens edges.
UPSCALE_SHARPEN_FILTER = "unsharp=5:5:0.6:5:5:0.0"

VERTICAL_SCALE_CHAIN = f"scale=1080:1920:flags=lanczos,{UPSCALE_SHARPEN_FILTER},setsar=1"
