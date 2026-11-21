"""Reference Style Analyser: measure a reference short's editing style and
emit a reusable niche profile in the existing auto-director schema.

Give it a finished short (a Reel, a Short, a TikTok, any short-form file) and
it measures how that video was *edited* - cut rhythm, caption usage, colour,
music bed, hook shape - then maps those measurements onto a profile dict with
exactly the structure of ``profiles/*.json``, so the current engine
(``load_profile`` / ``build_edl``) can cut new videos in the same style.

The analyser never invents a measurement it cannot compute. Every number in
the measurements dict comes from one of the passes below, and each pass is
graded honestly:

MEASUREMENT METHODS (and their honesty grades)
----------------------------------------------
- Duration / resolution / fps: ffprobe. SOLID.
- Scene cuts + shot lengths: the pipeline's own single-pass analysis
  (``src.media.reframing.analyze_vertical_clip`` - 3 fps, 480 px decode,
  frame-difference spikes + scene-context cuts, face boxes in the same pass),
  unioned with a robust difference detector over the caption/colour pass's
  own frame differences (local maxima above max(16, median + 6 sigma-by-MAD),
  0.5 s refractory). The union matters: the pipeline helper's adaptive
  threshold (mean + 3.5 sigma) goes blind exactly when cuts are dense -
  at 30+ cuts/min the spikes *are* the mean - while the robust detector is
  built for finished shorts. SOLID for hard cuts; dissolves and fades are
  systematically undercounted by both detectors (a fade produces no
  single-frame spike), so cuts/min is a floor, not an exact count, on
  dissolve-heavy styles. When the reframing pass cannot run at all (its
  face-detector stack is unavailable in the current environment), cuts
  come from the robust detector alone and the face measures below report
  "no face seen" - the method string in the output says which happened.
- Captions: HEURISTIC. Burned captions are detected as concentrated
  high-frequency edge energy inside a horizontal band (lower third or
  centre band) of sampled frames (2 fps, 320 px wide): a frame counts as
  captioned when the band's strong-edge density is both above an absolute
  floor and well above the frame's own overall edge density. This detects
  *that* text-like content sits in the band, never what it says; busy
  scenery, lower-third name graphics and watermark logos can false-positive,
  and thin/low-contrast caption fonts can false-negative.
- Caption look: HEURISTIC-to-SOLID (per field; grades inline in
  ``_caption_look_pass``). A second, sparse pass decodes 8-12 caption
  instances at up to 720 px wide - chosen from the caption pass's own
  votes, speech-like moments preferred via the audio window levels -
  and reads the appearance the band vote cannot see: refined position
  (median text-box centroid), dominant text colour and highlight colour
  (k-means colour clusters scored by edge adjacency), outline presence / colour /
  width bucket (inner-vs-outer ring shells around the glyph mask),
  background-box presence / colour (a uniform surround that differs
  from the nearby scene), bucketed font size (line height against the
  templates' size classes) and - via the tesseract binary the shipped
  image already carries, invoked over subprocess exactly like
  ``media.visual_suitability`` - words-per-line and casing from word
  boxes. When the 320 px vote is too sparse to sample from - its
  absolute floor misses thin or low-contrast caption fonts; serif and
  shadow-only styles measure a 0.0 caption fraction - the pass falls
  back to an even grid scan at look resolution and lets the hi-res
  detection decide whether captions are really there. Absence is a
  result, not a failure: too few caption instances,
  no readable region, a missing OCR binary (lexical fields only) or any
  error leaves ``captions.look`` as ``None`` or the field omitted, and
  the style behaves exactly as the pacing-only analyser always did. A
  text region OCR cannot read at all is reported as
  ``script: "unknown_non_latin"`` (the shipped image carries the
  English pack only) with the pixel fields still measured. Font family
  is deliberately NOT read: from compressed, outlined caption pixels
  that is research-grade, not a measurement.
- Colour: mean HSV saturation/brightness, luma contrast (per-frame luma
  spread plus across-frame mean-luma spread) and the circular mean hue of
  saturated pixels over the same sampled frames. SOLID as statistics; the
  *interpretation* (which LUT they imply) is a mapping choice, see below.
- Music bed: HEURISTIC, mirroring the QC lane's music logic
  (``src.media.clip_qc.check_music``). Speech gaps are windows at least
  18 dB under the speech reference (75th-percentile window level, 50 ms
  windows - the ``gap_activity`` convention). A bed is reported when the
  gaps are long enough (>= 0.8 s total in runs >= 0.3 s), loud enough to be
  a real bed (gap RMS above -50 dBFS) and tonal (mean spectral flatness of
  the gap audio <= 0.35; digital silence fails the loudness test and room
  noise fails the tonality test). Fallback when a reference has no gaps at
  all: continuous, very tonal, almost-always-loud audio (flatness <= 0.25
  over the whole file, >= 85% loud windows) is reported as a bed with
  method ``continuous_tonal`` - that is the music-driven case (the track
  *is* the content). No gaps and no tonal continuity -> bed is ``None``
  (unknown), never a guess.
- Tempo: WEAK. Onset envelope (spectral flux) autocorrelation, peak lag in
  the 45-180 BPM range, accepted only when the peak stands out from the
  autocorrelation floor (strength >= 1.8). Reliable on beat-driven music
  beds; meaningless on speech or ambient audio, where it returns ``None``.
  No octave correction is applied - a reported 60 BPM may be a felt
  120 BPM half-time.
- Hook shape: face close-up in the first 3 s from the reframing pass's
  face boxes (largest face >= 6% of frame area); loud opening = first-1 s
  peak level within 3 dB of the file's 95th-percentile level; caption in
  frame 1 from the caption pass's first sample. The face measure is SOLID
  when a face is present and detectable at 480 px; it cannot see a face
  the YuNet/Haar detectors miss.

MAPPING TABLE (measurements -> profile fields, deterministic)
--------------------------------------------------------------
The emitted profile starts as a deep copy of the nearest stock profile
(the *base niche*, scored as: 0.6 x |log2(measured cuts/min / stock band
centre)|, +0.5 when the stock profile's music-bed expectation disagrees
with the measured bed, -0.5 for a ken-burns stock profile when the
reference is scenic (slow and caption-free), ties broken alphabetically).
The mapping report also records the margin between the best and
second-best donors and whether the pick is a close call (margin below
0.25, half of the scorer's smallest single step): the label is a pacing
match, never a genre verdict, and a near-tie must be shown as one — the
base niche only labels the style, the measured values drive the edit.
Pacing is deliberately the weakest term: the measured pacing band always
overrides the base's own pacing, so what the base really donates is its
style family - overlays, B-roll, title style, zoom trigger - and family
agreement should outrank a pacing gap the override erases anyway.
``cpm`` = measured cuts per minute, ``p`` = captioned-frame fraction.

pacing.target_cuts_per_min   cpm band -> [lo, hi]:
                             <6 -> [5, 8]; 6-10 -> [8, 12]; 10-14 -> [10, 16];
                             14-20 -> [12, 20]; 20-30 -> [15, 30];
                             30-45 -> [25, 40]; >=45 -> [60, 120]
pacing.min_shot_sec          exactly 60 / hi (the schema's pacing invariant)
pacing.max_shot_sec          exactly 60 / lo
pacing.silence_trim_aggressive  cpm >= 12
transitions.default          cpm >= 20 -> "cut" (0.0 s);
                             8-20 -> "hard_cut" (0.05 s);
                             <8 and captions absent -> "wipe" (0.4 s);
                             <8 and captions present -> "hard_cut"
                             (0.05 s) - a slow *talking* style, not a
                             scenic one
transitions.on_topic_change  cpm >= 20 -> "cut"; 8-20 -> "hard_cut";
                             <8 scenic -> "fade"; <8 captioned ->
                             "hard_cut"
transitions.on_punchline     face-close-up hook or cpm >= 20 ->
                             "zoom_bounce"; <8 scenic -> "wipe";
                             else "cut"
NOTE (N1, 2026-11-14): only transitions.default and duration_sec are
stamped onto EDL joins (build_edl). on_topic_change and on_punchline are
director guidance only. They are handed to the AI director in the plan
prompt (plan_service._profile_summary) and never stamped on a join, because
no per-join topic-change or punchline signal exists at build_edl time:
segments are uniform pacing cuts, and a speaker change is not a topic
change. "zoom_bounce" is not a renderer transition name at all: the
renderer degrades unknown names to a hard cut, so stamping it would
silently do nothing. Do not present these two fields as applied transitions.
zooms.punch_in_scale         cpm >= 30 -> 1.30; 20-30 -> 1.25;
                             12-20 -> 1.20; 6-12 -> 1.15; <6 -> 1.08
zooms.punch_in_trigger       tempo detected over a music bed ->
                             "energy_peak" (a tempo read on speech alone
                             is the talker's rhythm, not the edit's, and
                             is ignored here); p >= 0.4 and cpm < 12 ->
                             "emphasis"; otherwise inherited from base
zooms.ken_burns              cpm < 8 and p < 0.10 (slow, uncaptioned,
                             scenic) -> True; otherwise False
captions                     p >= 0.40: centre band -> preset
                             "large_centre_pop", 3 words/line, "center";
                             lower band -> "hormozi" when faces are on
                             screen >= 30% of samples, else
                             "bold_uppercase", 3 words/line, "bottom".
                             0.10 <= p < 0.40 -> "classic_box",
                             4 words/line, measured position.
                             p < 0.10 -> "minimal", 4 words/line,
                             "bottom" (the engine always captions from
                             the transcript; a caption-free reference maps
                             to the least intrusive preset, it cannot
                             switch captions off - the schema has no
                             such switch)
captions.look_override       only when the look pass measured the
                             reference's captions: a partial caption
                             template in the Brand Kit override shape,
                             stamped on the profile's captions block and
                             copied onto the EDL as
                             ``reference_look_override``. Fields:
                             position_y (float), font_color,
                             highlight_color, max_words_per_line,
                             uppercase, stroke_color + stroke_width
                             bucket (a measured *absent* outline stamps
                             stroke_color None / width 0; likewise a
                             measured absent box stamps background
                             False), shadow (presence only - absence is
                             not provable at this grade), background +
                             background_color (an 8-digit #RRGGBBAA
                             when the box reads as black at an estimated
                             opacity, else the effective on-screen
                             colour), font_size (nearest template size
                             class), font_family (stage 2: only from
                             the margin-gated font SUGGESTION - the
                             closest bundled font, a guess, never an
                             identification). The look also records an
                             ``animation`` feel class (static / pop /
                             karaoke / unknown) as information only:
                             it is NOT part of the override and no
                             render consumes it in this stage. Render
                             precedence: task choice >
                             Brand Kit > reference look > stock preset.
audio.music_mood             bed present: tempo >= 128 -> "energetic";
                             100-128 -> "upbeat"; tempo < 100 or no
                             tempo -> "calm". Bed absent -> "none".
                             Bed unknown or no audio -> inherited.
audio.music_volume_0_to_1    bed present -> 0.2; absent -> 0.0;
                             unknown -> inherited
audio.ducking_db             bed present: speech-like gap structure
                             (gaps exist and 5-95% of windows are loud)
                             -> -12.0; otherwise -6.0. Absent -> 0.0.
audio.sfx_from_user_library  cpm >= 15 (fast-cut styles punctuate with
                             SFX; WEAK inference)
color.lut                    saturation >= 0.45 and contrast >= 28 ->
                             "punchy"; else warm mean hue (5-45 deg)
                             and saturation >= 0.15 -> "warm_soft";
                             else "none"
NOT INFERRED (inherited from the base profile, listed in the mapping
report): ``overlays.*`` (a finished short does not reveal whether info
cards or reference images were part of the recipe), ``broll.*`` (stock
B-roll is indistinguishable from owned footage in a finished file),
``hook.title_style`` (a title's *wording* strategy is a language choice,
not an audiovisual measurement), ``hook.title_seconds`` (the hook
measurements are signals, not a measurement of how long a title stays
on screen; the title is pinned for the whole clip by default and a
shorter duration is only ever an explicit profile choice), ``niche``
(set to the base niche so the existing loader accepts the profile
unchanged), and the audio fields when the bed is unknown. Genre moods
(``phonk``, ``funk``, ``corporate``) are never emitted: nothing in these
measurements can identify a genre.

The profile carries a ``reference_style`` provenance block (source name,
base niche, the headline measurements). Extra top-level keys are ignored
by ``load_profile``/``build_edl``; the required schema sections and the
pacing invariant hold exactly, which the tests prove by loading an
emitted profile through the unmodified loader and building an EDL

from it.
"""

from __future__ import annotations

import csv
import io
import json
import math
import os
import shutil
import subprocess
from pathlib import Path
from typing import Any, Optional

import numpy as np

from .edl import load_profile
from .niche import NICHE_VALUES

ANALYSER_VERSION = 3

# --- Caption heuristic -------------------------------------------------
_VISUAL_SAMPLE_FPS = 3.0
_VISUAL_PROC_WIDTH = 320
_CAPTION_EDGE_ABS = 0.02       # >= 2% of band pixels are strong edges
_CAPTION_EDGE_RATIO = 1.6      # and >= 1.6x the frame's overall edge density
_CAPTION_EDGE_LAPLACIAN = 60.0
_LOWER_BAND = (0.60, 0.97)     # y fractions of frame height
_CENTRE_BAND = (0.34, 0.66)

# --- Caption look pass ---------------------------------------------------
# A second, sparse, higher-resolution decode that reads what the burned
# captions *look like* (see the module docstring). Instances are picked
# from the visual pass's own caption votes; 8-12 instances, native
# resolution capped at 720 px wide, the first few with a paired frame
# ~0.4 s later (karaoke highlight moves between the pair).
_LOOK_MAX_INSTANCES = 10
_LOOK_PAIRS = 3
_LOOK_PAIR_OFFSET_SEC = 0.4
_LOOK_MIN_REGION_INSTANCES = 3    # a look needs at least this much evidence
_LOOK_MAX_WIDTH = 720
_LOOK_MIN_GROUP_HEIGHT_FRAC = 0.006  # of frame height
_LOOK_HIGHLIGHT_MIN_SHARE = 0.04
_LOOK_HIGHLIGHT_MAX_SHARE = 0.55
_LOOK_HIGHLIGHT_MIN_DISTANCE = 60.0  # RGB euclidean, 0-441 scale
_LOOK_HIGHLIGHT_MIN_SATURATION = 0.35
_LOOK_RING_MIN_SHARE = 0.55       # dominant-colour share for a surround verdict
_LOOK_VERDICT_MIN_FRACTION = 0.6  # of instances for stroke / box verdicts
# Glyph extent (cap top to baseline/descender bottom, outline
# included) as read by the edge pass runs ~0.7x the ASS font size
# across the bundled faces - the pass trims a few low-edge-density
# rows at the glyphs' curved tops and bottoms. Calibrated on planted
# renders of the app's own caption renderer (three of the four look
# fixtures land their exact class; the outline-less shadow look
# lands one class low) - see tests/unit/test_reference_style_look.py.
_LOOK_GLYPH_EXTENT_PER_FONT_SIZE = 0.70

# --- Caption look stage 2: animation feel + font suggestion --------------
# A burst frame ~0.15 s after each paired instance gives the scale
# classifier a second, tighter comparison: pop/bounce entrances run
# their whole scale arc inside ~250 ms, so a +0.4 s pair alone usually
# lands after the effect has settled.
_LOOK_BURST_OFFSET_SEC = 0.15
# Animation evidence thresholds, calibrated on planted renders of the
# app's own caption renderer (see tests/unit/
# test_reference_style_look_stage2.py). The renderer's karaoke line
# entrance scales 92->100% (ratio <= 1.087), so the scale gate sits
# above it: pop's 88% entrance and bounce's 70% entrance clear 1.10,
# a karaoke entrance cannot.
_ANIM_SAME_LINE_MAX_DY = 0.03      # centroid_y distance, frame fraction
_ANIM_SAME_TEXT_MIN_JACCARD = 0.6  # pair must show the same chunk
_ANIM_HIGHLIGHT_MOVE_MIN = 0.03    # highlight centroid dx, frame fraction
_ANIM_SCALE_MIN_RATIO = 1.10
# Font suggestion gates - set from the planted-font evaluation in
# tests/unit/test_reference_style_look_stage2.py, NOT from hope: the
# suggestion is emitted only when the best candidate's glyph-mask
# score clears an absolute floor AND its margin over the runner-up
# clears the gate AND it beats its own confusion-group mates by the
# same gate. Measured on 12 planted fonts (STAGE2_REPORT.md): raw
# top-1 was 9/12 at the shipped instance count, but the two
# Montserrat weights swap places, and one swap produced a WRONG
# winner at margin 0.081 while the quietest correct decisive win
# sat at 0.122 - so the margin gate sits at 0.10, between those
# measured points. At 0.08 the evaluation emitted a wrong font; at
# 0.10 it emits 4 suggestions, all correct (Poppins, THEBOLDFONT,
# ArchivoBlack, Anton). Below any bar, the look carries no
# suggestion at all - a coin flip is never surfaced.
_FONT_SUGGESTION_MIN_SCORE = 0.55
_FONT_SUGGESTION_MIN_MARGIN = 0.10
# Six instances: distinct faces separate measurably better than at
# three (Poppins's margin 0.060 -> 0.209 at ten instances), while
# the same-family weight swap is systematic - it persists at ten
# instances, so no instance count fixes it and the margin gate, not
# sample size, is the guard. Six bounds the added analysis time.
_FONT_MATCH_MAX_INSTANCES = 6
_FONT_MATCH_MIN_TEXT_CHARS = 6
_FONT_MATCH_MIN_MASK_PIXELS = 400
# Confusion groups: faces that planted-fixture evidence (and design
# lineage) shows this matcher cannot reliably tell apart at caption
# sizes - weights of one family, condensed grotesques, and humanist
# text sans. A suggestion whose own group is breathing down its neck
# is a coin flip, whatever the runner-up says.
_FONT_CONFUSION_GROUPS = (
    frozenset({"Montserrat-Black", "Montserrat-ExtraBold",
               "Montserrat-Variable-wght"}),
    frozenset({"BebasNeue-Regular", "Anton-Regular",
               "Oswald-Variable-wght", "BarlowCondensed-Bold"}),
    frozenset({"DMSans", "Inter", "NunitoSans", "OpenSans", "Roboto",
               "Rubik", "Sora", "Urbanist", "WorkSans"}),
)
_FONT_CONFUSION_GROUP_OF = {
    stem: group for group in _FONT_CONFUSION_GROUPS for stem in group
}

# --- Audio --------------------------------------------------------------
_AUDIO_RATE = 16000
_AUDIO_WINDOW_SECONDS = 0.05   # gap_activity / clip_qc convention
_GAP_DROP_DB = 18.0            # mirrors clip_qc.MUSIC_SPEECH_DROP_DB
_GAP_RUN_MIN_SECONDS = 0.3     # mirrors clip_qc.MUSIC_GAP_RUN_MIN_SECONDS
_GAP_TOTAL_MIN_SECONDS = 0.8   # mirrors clip_qc.MUSIC_GAP_TOTAL_MIN_SECONDS
_GAP_SILENCE_DB = -50.0        # mirrors clip_qc.MUSIC_GAP_SILENCE_DB
_GAP_FLATNESS_MAX = 0.35
_CONTINUOUS_FLATNESS_MAX = 0.25
_CONTINUOUS_LOUD_FRACTION = 0.85
_TEMPO_MIN_BPM = 45.0
_TEMPO_MAX_BPM = 180.0
_TEMPO_STRENGTH_MIN = 1.8

# --- Hook ----------------------------------------------------------------
_FACE_CLOSEUP_MIN_AREA_FRAC = 0.06
_HOOK_WINDOW_SECONDS = 3.0
_LOUD_OPEN_TOLERANCE_DB = 3.0

_CPM_BANDS = (
    (6.0, (5, 8)),
    (10.0, (8, 12)),
    (14.0, (10, 16)),
    (20.0, (12, 20)),
    (30.0, (15, 30)),
    (45.0, (25, 40)),
)
_CPM_TOP_BAND = (60, 120)


# ---------------------------------------------------------------------------
# Probing
# ---------------------------------------------------------------------------

def _ffprobe_fps(path: Path) -> Optional[float]:
    """Average frame rate from ffprobe, or None when it cannot be read."""
    try:
        result = subprocess.run(
            [
                "ffprobe", "-v", "error", "-select_streams", "v:0",
                "-show_entries", "stream=avg_frame_rate,r_frame_rate",
                "-of", "json", str(path),
            ],
            capture_output=True, timeout=30,
        )
        streams = json.loads(result.stdout.decode("utf-8", "replace"))["streams"]
        for key in ("avg_frame_rate", "r_frame_rate"):
            raw = (streams[0].get(key) or "") if streams else ""
            if "/" in raw:
                num, den = raw.split("/", 1)
                if float(den) > 0:
                    return float(num) / float(den)
            elif raw:
                return float(raw)
    except Exception:
        return None
    return None


def probe_media(path: Path) -> dict[str, Any]:
    """Duration / resolution / fps / audio presence via ffprobe."""
    from ..media.ffmpeg import ffprobe_duration, ffprobe_has_audio, ffprobe_video_size

    width, height = ffprobe_video_size(path)
    return {
        "duration_sec": round(float(ffprobe_duration(path)), 3),
        "width": int(width),
        "height": int(height),
        "fps": _ffprobe_fps(path),
        "has_audio": bool(ffprobe_has_audio(path)),
    }


# ---------------------------------------------------------------------------
# Visual passes
# ---------------------------------------------------------------------------

def _reframing_pass(path: Path) -> tuple[list[float], list, str]:
    """Scene cuts + face samples from the pipeline's own analysis pass.

    Returns (cuts, face_samples, method). Falls back to the cheap
    context-free pass when the full one fails; raises only when both do.
    """
    from ..media.reframing import analyze_vertical_clip

    try:
        _, cuts, face_samples, _ = analyze_vertical_clip(path, analyze_context=True)
        return [float(c) for c in cuts], face_samples, "reframing"
    except Exception:
        _, cuts, face_samples, _ = analyze_vertical_clip(path, analyze_context=False)
        return [float(c) for c in cuts], face_samples, "reframing_no_context"


def _visual_pass(path: Path, width: int, height: int) -> dict[str, Any]:
    """Sampled-frame pass: caption bands, colour stats, fallback cut diffs.

    One sequential decode at 2 fps / 320 px wide - the frames the reframing
    pass discards are exactly the ones caption and colour measurement need.
    """
    import cv2

    proc_w = min(_VISUAL_PROC_WIDTH, width) if width else _VISUAL_PROC_WIDTH
    proc_h = max(2, int(round(proc_w * height / width))) if width and height else 0
    proc_h -= proc_h % 2
    if proc_w < 2 or proc_h < 2:
        return {"frames": 0}

    command = [
        "ffmpeg", "-v", "error", "-an", "-sn", "-i", str(path),
        "-vf", f"fps={_VISUAL_SAMPLE_FPS:.3f},scale={proc_w}:{proc_h}",
        "-pix_fmt", "bgr24", "-f", "rawvideo", "-",
    ]
    proc = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    frame_bytes = proc_w * proc_h * 3

    caption_votes: list[Optional[str]] = []
    diffs: list[tuple[float, float]] = []
    sat_values, val_values, luma_stds, frame_lumas = [], [], [], []
    hue_sin = hue_cos = hue_weight = 0.0
    prev_small = None
    idx = 0
    assert proc.stdout is not None
    while True:
        raw = proc.stdout.read(frame_bytes)
        if not raw or len(raw) < frame_bytes:
            break
        frame = np.frombuffer(raw, dtype=np.uint8).reshape(proc_h, proc_w, 3)
        t = idx / _VISUAL_SAMPLE_FPS

        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        lap = np.abs(cv2.Laplacian(gray, cv2.CV_32F))
        edges = lap > _CAPTION_EDGE_LAPLACIAN
        overall_density = float(edges.mean())

        def band_density(band: tuple[float, float]) -> float:
            y0 = int(band[0] * proc_h)
            y1 = max(y0 + 1, int(band[1] * proc_h))
            return float(edges[y0:y1].mean())

        lower = band_density(_LOWER_BAND)
        centre = band_density(_CENTRE_BAND)

        def is_text(density: float) -> bool:
            return density >= _CAPTION_EDGE_ABS and density >= _CAPTION_EDGE_RATIO * max(overall_density, 1e-6)

        if is_text(lower) and lower >= centre:
            caption_votes.append("lower")
        elif is_text(centre):
            caption_votes.append("centre")
        else:
            caption_votes.append(None)

        hsv = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV).astype(np.float64)
        sat_values.append(float(hsv[:, :, 1].mean()) / 255.0)
        val_values.append(float(hsv[:, :, 2].mean()) / 255.0)
        luma_stds.append(float(gray.astype(np.float64).std()))
        frame_lumas.append(float(gray.mean()))
        saturated = hsv[:, :, 1] > 60
        if saturated.any():
            hues = hsv[:, :, 0][saturated] * 2.0  # OpenCV hue is 0-179
            weights = hsv[:, :, 1][saturated]
            hue_sin += float((np.sin(np.radians(hues)) * weights).sum())
            hue_cos += float((np.cos(np.radians(hues)) * weights).sum())
            hue_weight += float(weights.sum())

        small = cv2.resize(gray, (32, 18)).astype(np.int16)
        if prev_small is not None:
            diffs.append((t, float(np.mean(np.abs(small - prev_small)))))
        prev_small = small
        idx += 1
    proc.stdout.close()
    proc.wait()

    frames = len(caption_votes)
    # caption_votes rides along for the caption-look pass, which samples
    # real caption instances from these votes instead of re-detecting.
    result: dict[str, Any] = {"frames": frames, "diffs": diffs, "caption_votes": caption_votes}
    if frames:
        captioned = [v for v in caption_votes if v]
        result["caption_frame_fraction"] = len(captioned) / frames
        if captioned:
            result["caption_position"] = (
                "centre" if captioned.count("centre") > captioned.count("lower") else "lower"
            )
        else:
            result["caption_position"] = None
        result["caption_from_start"] = caption_votes[0] is not None
        result["mean_saturation"] = float(np.mean(sat_values))
        result["mean_brightness"] = float(np.mean(val_values))
        temporal = float(np.std(frame_lumas)) if len(frame_lumas) > 1 else 0.0
        result["luma_contrast"] = float(np.mean(luma_stds)) + temporal
        if hue_weight > 0:
            result["mean_hue_deg"] = float(
                math.degrees(math.atan2(hue_sin, hue_cos)) % 360.0
            )
        else:
            result["mean_hue_deg"] = None
    return result



# ---------------------------------------------------------------------------
# Caption look pass
# ---------------------------------------------------------------------------

def _evenly_spaced(items: list, k: int) -> list:
    """Up to ``k`` items spread evenly across the list, order preserved."""
    if len(items) <= k:
        return list(items)
    idx = np.linspace(0, len(items) - 1, k).round().astype(int)
    return [items[i] for i in dict.fromkeys(idx.tolist())]


def _rgb_hex(rgb: Any) -> str:
    r, g, b = (int(round(float(min(255.0, max(0.0, c))))) for c in rgb)
    return f"#{r:02X}{g:02X}{b:02X}"


def _kmeans_rgb(samples: np.ndarray, k: int) -> list[tuple[np.ndarray, float, float]]:
    """Deterministic small k-means over RGB pixels.

    Returns [(centre_rgb, share, mean_saturation)] sorted by share,
    largest first. Centres are seeded from the sample luminance
    quantiles so runs are reproducible without a random source.
    """
    if len(samples) == 0:
        return []
    if len(samples) > 20000:
        samples = samples[:: int(np.ceil(len(samples) / 20000))]
    k = max(1, min(k, len(samples)))
    if k == 1:
        centres = np.array([samples.mean(axis=0)])
    else:
        order = np.argsort(samples.sum(axis=1))
        centres = np.array([
            samples[order[int(q * (len(samples) - 1))]]
            for q in np.linspace(0.0, 1.0, k)
        ], dtype=np.float64)
    labels = np.full(len(samples), -1, dtype=int)
    for _ in range(15):
        dists = ((samples[:, None, :] - centres[None, :, :]) ** 2).sum(axis=2)
        new_labels = dists.argmin(axis=1)
        if (new_labels == labels).all():
            break
        labels = new_labels
        for j in range(k):
            if (labels == j).any():
                centres[j] = samples[labels == j].mean(axis=0)
    out: list[tuple[np.ndarray, float, float]] = []
    for j in range(k):
        sel = samples[labels == j]
        if len(sel) == 0:
            continue
        mx = sel.max(axis=1)
        mn = sel.min(axis=1)
        saturation = float(np.mean((mx - mn) / np.maximum(mx, 1.0)))
        out.append((centres[j], len(sel) / len(samples), saturation))
    out.sort(key=lambda item: -item[1])
    return out


def _dominant_colour(pixels: np.ndarray) -> tuple[np.ndarray, float]:
    """(median RGB of the dominant coarse colour bin, its pixel share)."""
    if len(pixels) == 0:
        return np.zeros(3), 0.0
    bins = (pixels // 16).astype(int)
    keys, counts = np.unique(bins, axis=0, return_counts=True)
    top = int(np.argmax(counts))
    sel = pixels[(bins == keys[top]).all(axis=1)]
    return np.median(sel, axis=0), float(counts[top]) / len(pixels)


def _row_groups(mask: np.ndarray, gap: int = 2) -> list[tuple[int, int]]:
    """Contiguous True runs in a 1-D mask, bridging gaps up to ``gap``."""
    groups: list[tuple[int, int]] = []
    start: Optional[int] = None
    last = 0
    for i, value in enumerate(mask):
        if value:
            if start is None:
                start = i
            last = i
        elif start is not None and i - last > gap:
            groups.append((start, last + 1))
            start = None
    if start is not None:
        groups.append((start, last + 1))
    return groups


def _decode_look_frame(
    path: Path, t: float, width: int, height: int
) -> Optional[np.ndarray]:
    """One frame at native resolution, width capped for the look pass."""
    proc_w = min(_LOOK_MAX_WIDTH, width) if width else _LOOK_MAX_WIDTH
    proc_w -= proc_w % 2
    if proc_w < 2 or not width or not height:
        return None
    proc_h = max(2, int(round(proc_w * height / width)))
    proc_h -= proc_h % 2
    command = [
        "ffmpeg", "-v", "error", "-ss", f"{max(0.0, t):.3f}", "-i", str(path),
        "-frames:v", "1", "-an", "-sn", "-vf", f"scale={proc_w}:{proc_h}",
        "-pix_fmt", "bgr24", "-f", "rawvideo", "-",
    ]
    try:
        result = subprocess.run(command, capture_output=True, timeout=60)
    except Exception:
        return None
    expected = proc_w * proc_h * 3
    if result.returncode != 0 or len(result.stdout) < expected:
        return None
    return np.frombuffer(
        result.stdout[:expected], dtype=np.uint8
    ).reshape(proc_h, proc_w, 3)


def _tesseract_words(image_bgr: np.ndarray) -> tuple[Optional[list[dict[str, Any]]], bool]:
    """Word boxes from the tesseract binary over one region image.

    Mirrors ``media.visual_suitability``'s invocation: ``shutil.which``
    guard, PNG via stdin, ``OMP_THREAD_LIMIT=1``, timeout, silent
    degrade. Returns (words, available): ``available`` is False only
    when the binary itself is missing (the shipped image carries it;
    a bare local checkout may not), which is a different fact from
    "ran and found nothing". Words carry TSV line grouping so callers
    can count words per line.
    """
    import cv2

    binary = shutil.which("tesseract")
    if binary is None:
        return None, False
    ok, encoded = cv2.imencode(".png", image_bgr)
    if not ok:
        return None, True
    try:
        result = subprocess.run(
            [binary, "stdin", "stdout", "-l", "eng", "--psm", "6", "tsv"],
            input=encoded.tobytes(), capture_output=True, timeout=20,
            env={**os.environ, "OMP_THREAD_LIMIT": "1"},
        )
    except Exception:
        return None, True
    if result.returncode != 0:
        return None, True
    words: list[dict[str, Any]] = []
    reader = csv.DictReader(
        io.StringIO(result.stdout.decode("utf-8", "replace")),
        delimiter="\t", quoting=csv.QUOTE_NONE,
    )
    for row in reader:
        try:
            if int(row.get("level") or 0) != 5:
                continue
            conf = float(row.get("conf") or -1)
            text = (row.get("text") or "").strip()
            if conf < 40 or not text:
                continue
            words.append({
                "text": text,
                "left": float(row.get("left") or 0),
                "top": float(row.get("top") or 0),
                "width": float(row.get("width") or 0),
                "height": float(row.get("height") or 0),
                "line": (row.get("block_num"), row.get("par_num"), row.get("line_num")),
            })
        except (TypeError, ValueError):
            continue
    return words, True


def _shadow_vote(canvas_gray: np.ndarray, inner: np.ndarray, glyph: np.ndarray) -> Optional[bool]:
    """Drop-shadow evidence: dark inner-ring pixels pooled down-right.

    libass draws the shadow as a dark copy offset (+x, +y) behind the
    glyph, so with no outline in the way the dark part of the ring sits
    in the glyph's bottom-right quadrant. FAIR grade at best - the
    caller only trusts a strong cross-instance majority.
    """
    dark = inner & (canvas_gray <= 60)
    if int(dark.sum()) < 30:
        return None
    gy, gx = np.nonzero(glyph)
    if len(gx) == 0:
        return None
    dy, dx = np.nonzero(dark)
    share = float(np.mean((dx > gx.mean()) & (dy > gy.mean())))
    return bool(share >= 0.55)


def _black_box_alpha(box_rgb: np.ndarray, scene_rgb: np.ndarray) -> Optional[float]:
    """Opacity of a black background box, when the box reads as one.

    box = (1 - opacity) * scene per channel for a black box, so the
    channel ratios agree. Returns the opacity, or None when the ratios
    disagree (a coloured box, or no box at all): then only the
    effective on-screen colour is honestly reportable.
    """
    ratios = [
        float(box_rgb[c]) / float(scene_rgb[c])
        for c in range(3) if scene_rgb[c] >= 30.0
    ]
    if len(ratios) < 2 or max(ratios) - min(ratios) > 0.10:
        return None
    keep = float(np.mean(ratios))
    if keep > 0.85:
        return None
    return 1.0 - keep


def _extent_groups(
    density: np.ndarray, hi: float, lo: float, gap: int = 2
) -> list[tuple[int, int]]:
    """Row/col groups by hysteresis: seeded where density >= hi,
    extended while density >= lo (the Canny trick - glyph tops and
    baselines are edge-poor compared with the stems, so a single
    threshold clips the extent)."""
    seed = density >= hi
    return [
        (a, b) for a, b in _row_groups(density >= lo, gap=gap)
        if int(seed[a:b].sum()) >= 2
    ]


def _dark_shell_above_left_share(shell_mask: np.ndarray, fill_mask: np.ndarray) -> Optional[float]:
    """Share of shell pixels that sit above or left of the glyphs.

    For each shell pixel, find its nearest fill pixel (local search
    to 4 px): if that fill pixel lies below or to the right, the
    shell pixel sits above/left of a glyph. A surrounding outline
    scores ~0.5 (half its pixels are on the top/left sides); an
    offset drop shadow scores near 0 - its pixels sit below-right of
    the glyphs they shade. Returns None when too few shell pixels
    have a fill neighbour to judge.
    """
    ys, xs = np.nonzero(shell_mask)
    if len(ys) == 0:
        return None
    height, width = fill_mask.shape
    offsets = sorted(
        (
            (dy, dx)
            for dy in range(-4, 5) for dx in range(-4, 5)
            if 0 < dy * dy + dx * dx <= 16
        ),
        key=lambda o: o[0] ** 2 + o[1] ** 2,
    )
    found = np.zeros(len(ys), bool)
    above_left = np.zeros(len(ys), bool)
    for dy, dx in offsets:
        open_idx = np.nonzero(~found)[0]
        if len(open_idx) == 0:
            break
        ny, nx = ys[open_idx] + dy, xs[open_idx] + dx
        valid = (ny >= 0) & (ny < height) & (nx >= 0) & (nx < width)
        hit = np.zeros(len(open_idx), bool)
        hit[valid] = fill_mask[ny[valid], nx[valid]]
        if hit.any():
            hit_idx = open_idx[hit]
            above_left[hit_idx] = bool(dy > 0 or dx > 0)
            found[hit_idx] = True
    if int(found.sum()) < 0.5 * len(ys):
        return None
    return float(above_left[found].mean())


def _look_frame_record(frame: np.ndarray, band: tuple[float, float]) -> Optional[dict[str, Any]]:
    """Read one caption instance's appearance from one frame.

    Geometry (text-box extent, centroid, line count) comes from the
    band's edge rows with hysteresis thresholding. Colours come from
    k-means clusters over the region's pixels, scored by edge
    adjacency: glyph pixels live within a couple of pixels of a strong
    edge, background and box fill mostly do not, so text clusters
    separate from scene clusters. Among the text clusters the
    brightest is the font colour (the caption convention this app's
    templates - and viral captions generally - follow: light text,
    dark outline); a saturated cluster is the highlight; a dark
    near-achromatic cluster is the outline only when it hugs the fill
    and is explained by neither the scene colour nor a detected
    background box (both sit behind glyphs exactly like an outline
    does, and are the two classic false positives).
    """
    import cv2

    height, width = frame.shape[:2]
    band_y0 = int(band[0] * height)
    band_y1 = max(band_y0 + 1, int(band[1] * height))
    crop = frame[band_y0:band_y1]
    gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
    lap = np.abs(cv2.Laplacian(gray, cv2.CV_32F))
    edges = lap > _CAPTION_EDGE_LAPLACIAN
    if edges.size == 0:
        return None
    row_density = edges.mean(axis=1)
    nonzero_rows = row_density[row_density >= 0.004]
    if len(nonzero_rows) == 0:
        return None
    row_hi = max(0.008, 0.3 * float(np.percentile(nonzero_rows, 90)))
    min_h = max(3, int(round(_LOOK_MIN_GROUP_HEIGHT_FRAC * height)))
    groups = [
        g for g in _extent_groups(row_density, row_hi, max(0.0025, 0.2 * row_hi))
        if g[1] - g[0] >= min_h and int(edges[g[0]:g[1]].sum()) >= 40
    ]
    if not groups:
        return None
    gy0, gy1 = groups[0][0], groups[-1][1]
    band_edges = edges[gy0:gy1]
    col_density = band_edges.mean(axis=0)
    nonzero_cols = col_density[col_density >= 0.002]
    if len(nonzero_cols) == 0:
        return None
    col_hi = max(0.004, 0.25 * float(np.percentile(nonzero_cols, 90)))
    col_groups = _extent_groups(col_density, col_hi, max(0.0015, 0.2 * col_hi))
    if not col_groups:
        return None
    x0, x1 = col_groups[0][0], col_groups[-1][1]
    ry0, ry1 = band_y0 + gy0, band_y0 + gy1
    line_height = float(np.median([g[1] - g[0] for g in groups]))
    record: dict[str, Any] = {
        "region": (x0, ry0, x1, ry1),
        "frame_width": width,
        "centroid_y": (ry0 + ry1) / 2.0 / height,
        "line_height": line_height,
        "lines": len(groups),
        "font_centre": None,
        "highlight_centre": None,
        "highlight_centroid_x": None,
        "highlight_area": 0,
        "core_pixels": 0,
        "stroke": None,
        "shadow_vote": None,
        "box": None,
    }
    kernel = np.ones((3, 3), np.uint8)

    # Scene colour from the band's corners: the local background the
    # caption sits on, needed to tell an outline from the scene itself.
    patch = min(10, crop.shape[0] // 2, crop.shape[1] // 2)
    scene_rgb: Optional[np.ndarray] = None
    if patch >= 2:
        corners = np.concatenate([
            crop[:patch, :patch].reshape(-1, 3), crop[:patch, -patch:].reshape(-1, 3),
            crop[-patch:, :patch].reshape(-1, 3), crop[-patch:, -patch:].reshape(-1, 3),
        ], axis=0)
        scene_rgb = np.median(corners[:, ::-1].astype(np.float64), axis=0)

    # --- colour clusters over the region, scored by edge adjacency.
    reg_edges = band_edges[:, x0:x1]
    near_edge = cv2.dilate(reg_edges.astype(np.uint8), kernel, iterations=1) > 0
    reg_bgr = frame[ry0:ry1, x0:x1]
    pixels_rgb = reg_bgr.reshape(-1, 3).astype(np.float64)[:, ::-1]
    clusters = _kmeans_rgb(pixels_rgb, 5)
    if not clusters:
        return record
    # _kmeans_rgb works on a flat sample; rebuild per-cluster masks by
    # nearest-centre assignment so adjacency/shares are exact.
    centres = np.array([c[0] for c in clusters])
    dists = ((pixels_rgb[:, None, :] - centres[None, :, :]) ** 2).sum(axis=2)
    labels = dists.argmin(axis=1).reshape(reg_bgr.shape[:2])
    flat_near = near_edge.reshape(-1)
    total = len(pixels_rgb)
    text_clusters: list[dict[str, Any]] = []
    for j, (centre, _share, saturation) in enumerate(clusters):
        mask_flat = labels.reshape(-1) == j
        if not mask_flat.any():
            continue
        # The cluster centre used for reporting is the pixel MEDIAN,
        # not the k-means mean: anti-aliased fringe pixels drag a
        # mean toward the background (a planted #FFE000 read back
        # with its blue channel inflated 3x by fringe blending).
        median_centre = np.median(pixels_rgb[mask_flat], axis=0)
        # Core share: how much of the cluster survives a 1-px
        # erosion of its own mask. A painted colour fills glyph
        # interiors (letterforms are several px wide, so a healthy
        # share survives); an anti-aliased fringe cluster is a thin
        # shell and erodes to almost nothing.
        mask2d = labels == j
        core_share = float(
            cv2.erode(mask2d.astype(np.uint8), kernel).sum() / max(int(mask2d.sum()), 1)
        )
        text_clusters.append({
            "centre": median_centre,
            "share": float(mask_flat.sum()) / total,
            "saturation": saturation,
            "spread": float(median_centre.max() - median_centre.min()),
            "adjacency": float(flat_near[mask_flat].mean()),
            "core_share": core_share,
            "luminance": float(
                0.299 * median_centre[0] + 0.587 * median_centre[1] + 0.114 * median_centre[2]
            ),
            "index": j,
        })
    text_clusters = [c for c in text_clusters if c["adjacency"] >= 0.5]
    if not text_clusters:
        return record
    font = max(text_clusters, key=lambda c: c["luminance"])
    record["font_centre"] = font["centre"]
    text_total = sum(c["share"] for c in text_clusters)
    record["core_pixels"] = int(text_total * total)
    fill_mask = labels == font["index"]
    dark_candidates: list[dict[str, Any]] = []
    for cluster in text_clusters:
        if cluster is font:
            continue
        distance = float(np.linalg.norm(cluster["centre"] - font["centre"]))
        if (
            cluster["spread"] <= 40.0
            and cluster["luminance"] < font["luminance"]
            and cluster["luminance"] <= 110.0
        ):
            # Near-achromatic, darker than the text, and dark in
            # absolute terms: an outline or a drop shadow - or the
            # scene / box showing through. Guards run below. (The
            # absolute term matters: anti-aliasing fringe forms
            # mid-grey clusters at luminance 150-210 that hug the
            # glyphs exactly like an outline but are not one.)
            dark_candidates.append(cluster)
            continue
        share_of_text = cluster["share"] / text_total if text_total else 0.0
        # Anti-aliased fringe between the font colour and a dark
        # neighbour forms its own mid-blend cluster (yellow text on a
        # black box reads back an olive one); a candidate lying on
        # the RGB segment between the font colour and any other
        # cluster centre is a blend, not a painted colour.
        fringe = False
        for other_j, other in enumerate(clusters):
            if other_j == font["index"]:
                continue
            other_centre = other[0]
            seg = other_centre - font["centre"]
            seg_len2 = float(seg @ seg)
            if seg_len2 < 1.0:
                continue
            t_param = float(((cluster["centre"] - font["centre"]) @ seg) / seg_len2)
            if 0.15 <= t_param <= 0.95:
                closest = font["centre"] + t_param * seg
                if float(np.linalg.norm(cluster["centre"] - closest)) < 30.0:
                    fringe = True
                    break
        if (
            not fringe
            and cluster["core_share"] >= 0.30
            and record["highlight_centre"] is None
            and cluster["centre"].max() >= 100.0
            and _LOOK_HIGHLIGHT_MIN_SHARE <= share_of_text <= _LOOK_HIGHLIGHT_MAX_SHARE
            and distance >= _LOOK_HIGHLIGHT_MIN_DISTANCE
            and (cluster["saturation"] >= _LOOK_HIGHLIGHT_MIN_SATURATION or distance >= 110.0)
        ):
            record["highlight_centre"] = cluster["centre"]
            highlight_mask = labels == cluster["index"]
            fill_mask |= highlight_mask
            # Stage 2 animation evidence: where the highlight sits
            # inside the frame. Between a frame and its +0.4 s pair
            # the karaoke highlight moves right; its centroid is the
            # signal (the colour alone cannot say it *moved*).
            _h_ys, h_xs = np.nonzero(highlight_mask)
            if len(h_xs):
                record["highlight_centroid_x"] = round(
                    (x0 + float(h_xs.mean())) / max(width, 1), 4
                )
                record["highlight_area"] = int(len(h_xs))

    # --- background box: a uniform surround inside an expanded region
    # that differs from the scene at the band's corners. Runs before
    # the outline verdict because a box explains dark pixels behind
    # the glyphs just as an outline would.
    expand_y = int(round(0.35 * line_height))
    expand_x = int(round(0.5 * line_height))
    bx0, by0 = max(0, x0 - expand_x), max(band_y0, ry0 - expand_y)
    bx1, by1 = min(width, x1 + expand_x), min(band_y1, ry1 + expand_y)
    box_area = frame[by0:by1, bx0:bx1]
    placed = np.zeros(box_area.shape[:2], bool)
    placed[ry0 - by0:ry1 - by0, x0 - bx0:x1 - bx0] = fill_mask
    placed = cv2.dilate(placed.astype(np.uint8), kernel, iterations=2) > 0
    surround = (
        box_area[~placed][:, ::-1].astype(np.float64)
        if (~placed).any() else np.zeros((0, 3))
    )
    box_colour: Optional[np.ndarray] = None
    if len(surround) >= 60 and scene_rgb is not None:
        colour, share = _dominant_colour(surround)
        if share >= _LOOK_RING_MIN_SHARE and float(np.linalg.norm(colour - scene_rgb)) >= 30.0:
            record["box"] = ("present", colour, _black_box_alpha(colour, scene_rgb))
            box_colour = colour
        elif share >= _LOOK_RING_MIN_SHARE:
            record["box"] = ("absent",)

    # --- outline verdict, with the scene and the box as rival
    # explanations for a dark cluster behind the glyphs.
    outline = None
    if dark_candidates:
        fill_distance = cv2.distanceTransform(
            (~fill_mask).astype(np.uint8), cv2.DIST_L2, 3
        )
        for cluster in sorted(dark_candidates, key=lambda c: -c["share"]):
            centre = cluster["centre"]
            if scene_rgb is not None and float(np.linalg.norm(centre - scene_rgb)) < 25.0:
                continue  # the scene itself, seen between the glyphs
            if box_colour is not None and float(np.linalg.norm(centre - box_colour)) < 40.0:
                continue  # the box fill behind the glyphs
            near_fill = float((fill_distance[labels == cluster["index"]] <= 3.0).mean())
            if near_fill < 0.30:
                continue  # spread through the gaps - not a glyph shell
            # A drop shadow is the other dark shell a glyph can
            # wear: offset down-right, almost none of its pixels sit
            # above or left of the glyphs, where a true outline
            # surrounds on all four sides. A below/right-concentrated
            # shell is shadow evidence, not an outline.
            above_or_left = _dark_shell_above_left_share(
                labels == cluster["index"], fill_mask
            )
            if above_or_left is not None and above_or_left < 0.22:
                record["shadow_vote"] = True
                continue
            outline = cluster
            break
    glyph_mask = fill_mask.copy()
    if outline is not None:
        glyph_mask |= labels == outline["index"]
        boundary = float(np.count_nonzero(
            fill_mask & (cv2.dilate((~fill_mask).astype(np.uint8), kernel, iterations=1) > 0)
        ))
        thickness = (outline["share"] * total) / max(boundary, 1.0)
        record["stroke"] = (
            "present", outline["centre"], thickness / max(line_height, 1.0)
        )
    else:
        record["stroke"] = ("absent",)

    # Refine the region to the glyph bbox: the OCR crop and the
    # centroid should hug the text, not the edge vote's slack.
    ys, xs = np.nonzero(glyph_mask)
    if len(xs):
        gx0, gx1 = int(xs.min()), int(xs.max()) + 1
        gy0r, gy1r = int(ys.min()), int(ys.max()) + 1
        record["region"] = (x0 + gx0, ry0 + gy0r, x0 + gx1, ry0 + gy1r)
        record["centroid_y"] = (ry0 + gy0r + ry0 + gy1r) / 2.0 / height

    # --- drop shadow: only readable when no outline is in the way.
    if record["stroke"] == ("absent",):
        margin = 8
        ex0, ey0 = max(0, x0 - margin), max(0, ry0 - margin)
        ex1, ey1 = min(width, x1 + margin), min(height, ry1 + margin)
        glyph_canvas = np.zeros((ey1 - ey0, ex1 - ex0), bool)
        glyph_canvas[ry0 - ey0:ry1 - ey0, x0 - ex0:x1 - ex0] = glyph_mask
        if glyph_canvas.any():
            dil2 = cv2.dilate(glyph_canvas.astype(np.uint8), kernel, iterations=2) > 0
            inner = dil2 & ~glyph_canvas
            if inner.any():
                canvas_gray = cv2.cvtColor(frame[ey0:ey1, ex0:ex1], cv2.COLOR_BGR2GRAY)
                ring_vote = _shadow_vote(canvas_gray, inner, glyph_canvas)
                if ring_vote is not None:
                    # Combine, never clobber: the outline pass may
                    # already hold cluster-based shadow evidence for
                    # this frame, which is the stronger signal.
                    record["shadow_vote"] = bool(record["shadow_vote"] or ring_vote)

    # --- stage 2: retain the glyph-interior mask for font matching.
    # fill_mask at this point is exactly the painted letterforms
    # (font-colour cluster + accepted highlight cluster), outline
    # excluded - outlines are near-uniform across faces and would
    # only blur the comparison. Cropped to its own bbox; the origin
    # keeps it addressable in frame coordinates.
    fys, fxs = np.nonzero(fill_mask)
    if len(fxs) >= 40:
        fx0, fx1 = int(fxs.min()), int(fxs.max()) + 1
        fy0, fy1 = int(fys.min()), int(fys.max()) + 1
        record["font_mask"] = fill_mask[fy0:fy1, fx0:fx1].copy()
        record["font_mask_origin"] = (x0 + fx0, ry0 + fy0)
    return record


def _font_size_class(line_height_ratio: float) -> Optional[int]:
    """Nearest caption-template font-size class for a measured ratio.

    The renderer scales font size by frame width
    (``get_scaled_font_size``: font_px = size * width / 560) and the
    measured glyph extent runs ~0.7x the font size for the bundled
    faces, so each template size class predicts a glyph-extent /
    frame-width ratio; the nearest class in log space is the bucket.
    FAIR-to-GOOD: a bucket, never exact px.
    """
    from ..caption_templates import CAPTION_TEMPLATES

    sizes = sorted({
        int(t["font_size"]) for t in CAPTION_TEMPLATES.values() if t.get("font_size")
    })
    if not sizes or line_height_ratio <= 0:
        return None
    return min(
        sizes,
        key=lambda s: abs(math.log(
            line_height_ratio / (s * _LOOK_GLYPH_EXTENT_PER_FONT_SIZE / 560.0)
        )),
    )


def _caption_look_pass(
    path: Path,
    info: dict[str, Any],
    visual: dict[str, Any],
    audio: Optional[dict[str, Any]],
) -> Optional[dict[str, Any]]:
    """Measure the reference's caption appearance, or None.

    Never raises: any failure is an absence, and the style then behaves
    exactly as the pacing-only analyser always did. Field grades:
    position SOLID (median text-box centroid), font colour SOLID,
    highlight colour GOOD, words-per-line / casing SOLID for Latin
    script with the shipped English OCR pack, outline presence/colour
    GOOD + width FAIR (bucketed), background box GOOD (alpha only when
    the box reads as black), font size FAIR (nearest template class),
    shadow FAIR (presence only), animation feel FAIR-to-WEAK (coarse
    class from pair/burst evidence, information only - never applied
    to a render in this stage), font suggestion WEAK (render-and-diff
    against the bundled fonts, emitted only behind a score + margin
    gate and labelled a guess wherever it surfaces - font identity
    from compressed caption pixels is not reliable). Every field
    carries the instance count it rests on; fields below their
    evidence floor are omitted, never guessed.
    """
    try:
        return _caption_look_pass_impl(path, info, visual, audio)
    except Exception:
        return None


def _ocr_region_words(
    frame: np.ndarray, record: dict[str, Any]
) -> tuple[Optional[list[dict[str, Any]]], bool]:
    """OCR words for one record's region of one frame.

    The crop is binarized before tesseract sees it: its own polarity
    handling drops mid-luminance colours (a pure-green karaoke word
    inside a black outline disappeared from its output entirely on
    the planted fixtures), while an Otsu split with the text forced
    dark-on-light reads every word. Subtitle extractors binarize for
    the same reason. Returns (words, tesseract_available).
    """
    import cv2

    x0, ry0, x1, ry1 = record["region"]
    frame_h, frame_w = frame.shape[:2]
    region_crop = frame[
        max(0, ry0 - 6):min(frame_h, ry1 + 6),
        max(0, x0 - 6):min(frame_w, x1 + 6),
    ]
    if not region_crop.size:
        return None, False
    upscaled = cv2.resize(
        region_crop, None, fx=2.0, fy=2.0, interpolation=cv2.INTER_CUBIC
    )
    ocr_gray = cv2.cvtColor(upscaled, cv2.COLOR_BGR2GRAY)
    _, ocr_binary = cv2.threshold(
        ocr_gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU
    )
    if float((ocr_binary == 0).mean()) > 0.5:
        ocr_binary = 255 - ocr_binary
    return _tesseract_words(cv2.cvtColor(ocr_binary, cv2.COLOR_GRAY2BGR))


def _look_word_jaccard(a: list[str], b: list[str]) -> Optional[float]:
    """Multiset Jaccard over two word lists; None when either is empty."""
    if not a or not b:
        return None
    from collections import Counter

    ca, cb = Counter(a), Counter(b)
    union = sum((ca | cb).values())
    if not union:
        return None
    return sum((ca & cb).values()) / union


def _animation_pair_evidence(
    record: dict[str, Any],
    pair_record: dict[str, Any],
    burst_record: Optional[dict[str, Any]],
    frame_words: list[str],
    pair_words: list[str],
) -> dict[str, bool]:
    """What changed between one instance and its +0.4 s pair.

    Three independent signals, each gated on the pair still showing
    the same caption line (a pair that lands in the next chunk says
    nothing about animation):

    * ``highlight_move`` - the highlight cluster's centroid moved
      right while the text stayed the same: karaoke progression.
    * ``word_growth`` - the pair's words extend the frame's words as
      a strict prefix: word-by-word reveal (the karaoke family as
      other editors render it).
    * ``scale`` - the text block's line height changed by >= 10%
      against the burst (+0.15 s) or pair frame with content held
      constant: a pop/bounce entrance caught mid-arc. The renderer's
      karaoke line entrance tops out at a 1.087 ratio, below the
      gate, so it cannot fake this signal.
    """
    evidence = {"highlight_move": False, "word_growth": False, "scale": False}
    if abs(pair_record["centroid_y"] - record["centroid_y"]) > _ANIM_SAME_LINE_MAX_DY:
        return evidence
    a = [w.lower() for w in frame_words]
    b = [w.lower() for w in pair_words]
    jaccard = _look_word_jaccard(a, b)
    if jaccard is not None:
        same_text = jaccard >= _ANIM_SAME_TEXT_MIN_JACCARD
    else:
        # No OCR to compare (binary missing / nothing read): fall
        # back to geometry - the same chunk has the same line count
        # and a near-identical glyph-box width.
        w0 = record["region"][2] - record["region"][0]
        w1 = pair_record["region"][2] - pair_record["region"][0]
        same_text = bool(
            record.get("lines") == pair_record.get("lines")
            and w0 > 0 and 0.88 <= (w1 / max(w0, 1)) <= 1.14
        )
    hx0 = record.get("highlight_centroid_x")
    hx1 = pair_record.get("highlight_centroid_x")
    if (
        same_text and hx0 is not None and hx1 is not None
        and hx1 - hx0 >= _ANIM_HIGHLIGHT_MOVE_MIN
    ):
        evidence["highlight_move"] = True
    if a and b and len(b) > len(a) and b[:len(a)] == a:
        evidence["word_growth"] = True
    for other, needs_same_text in ((burst_record, False), (pair_record, True)):
        if other is None:
            continue
        if needs_same_text and not same_text:
            continue
        if int(other.get("lines") or 0) != int(record.get("lines") or 0):
            continue
        if abs(other["centroid_y"] - record["centroid_y"]) > _ANIM_SAME_LINE_MAX_DY:
            continue
        base = max(float(record.get("line_height") or 0.0), 1.0)
        ratio = float(other.get("line_height") or 0.0) / base
        if ratio > 0 and max(ratio, 1.0 / ratio) >= _ANIM_SCALE_MIN_RATIO:
            evidence["scale"] = True
    return evidence


def _classify_animation(anim_pairs: list[dict[str, bool]]) -> dict[str, Any]:
    """Coarse animation feel from pair evidence - information only.

    Deliberately conservative: karaoke needs one clear progression
    signal, pop one clear scale event with no karaoke signal on the
    same pair, "static" needs at least three examined pairs with no
    signal at all, and anything thinner is "unknown". The static
    floor is three pairs, not two, for a measured reason: pop and
    bounce entrances run their whole arc inside ~250-360 ms, so a
    pair samples one only ~10% of the time - on the planted pop
    fixture two quiet pairs were sampled from a video that DOES
    scale, and calling that "static" would overclaim (it now reads
    unknown; the planted static fixture's three quiet pairs still
    read static). A fade reads as static at pair sampling (its
    120 ms ramps sit at chunk edges the pairs rarely catch); that
    is stated, not hidden. Nothing in the render path consumes
    this field in this stage.
    """
    evidence = {
        "highlight_moves": sum(1 for e in anim_pairs if e["highlight_move"]),
        "word_growth": sum(1 for e in anim_pairs if e["word_growth"]),
        "scale_changes": sum(1 for e in anim_pairs if e["scale"]),
    }
    pop_votes = sum(
        1 for e in anim_pairs if e["scale"] and not e["highlight_move"]
    )
    if evidence["highlight_moves"] + evidence["word_growth"] >= 1:
        value = "karaoke"
    elif pop_votes >= 1:
        value = "pop"
    elif len(anim_pairs) >= 3:
        value = "static"
    else:
        value = "unknown"
    return {"value": value, "n": len(anim_pairs), "evidence": evidence}


# ---------------------------------------------------------------------------
# Stage 2: font suggestion (render-and-diff against the bundled fonts)
# ---------------------------------------------------------------------------

def _bundled_font_stems() -> list[str]:
    """The candidate set: every bundled font file's stem."""
    from ..font_registry import FONTS_DIR, SUPPORTED_FONT_EXTENSIONS

    try:
        return sorted(
            p.stem for p in FONTS_DIR.iterdir()
            if p.is_file() and p.suffix.lower() in SUPPORTED_FONT_EXTENSIONS
        )
    except OSError:
        return []


def _mask_widest_line(mask: np.ndarray, lines_hint: int) -> Optional[np.ndarray]:
    """The dominant text line of a glyph mask, bbox-cropped.

    Row groups are split where the glyph rows fully empty out for
    more than a small gap (a fraction of the average line height);
    the group carrying the most glyph pixels wins.
    """
    if mask is None or not mask.any():
        return None
    row_has = mask.any(axis=1)
    gap = max(2, int(round(0.18 * mask.shape[0] / max(1, lines_hint))))
    runs: list[tuple[int, int]] = []
    y, n = 0, len(row_has)
    while y < n:
        if not row_has[y]:
            y += 1
            continue
        start = last = y
        y += 1
        while y < n and (row_has[y] or y - last <= gap):
            if row_has[y]:
                last = y
            y += 1
        runs.append((start, last + 1))
    best: Optional[np.ndarray] = None
    best_pixels = -1
    for gy0, gy1 in runs:
        sub = mask[gy0:gy1]
        if int(sub.sum()) > best_pixels:
            best_pixels = int(sub.sum())
            best = sub
    if best is None or not best.any():
        return None
    ys, xs = np.nonzero(best)
    return best[int(ys.min()):int(ys.max()) + 1, int(xs.min()):int(xs.max()) + 1]


def _ocr_line_text(words: list[dict[str, Any]]) -> Optional[str]:
    """The OCR line with the most words, as one string.

    The TSV line grouping is used here only to pick WHICH line to
    render for font matching (most words = the main caption line);
    stage 1's words-per-line field deliberately avoids it.
    """
    if not words:
        return None
    groups: dict[Any, list[str]] = {}
    order: list[Any] = []
    for w in words:
        key = w.get("line")
        if key not in groups:
            groups[key] = []
            order.append(key)
        groups[key].append(str(w.get("text") or ""))
    best_key = max(order, key=lambda k: len(groups[k]))
    text = " ".join(t for t in groups[best_key] if t).strip()
    return text or None


def _render_candidate_masks(
    text: str, font_px: int, stems: list[str]
) -> dict[str, np.ndarray]:
    """Render ``text`` once per bundled font; return glyph masks.

    One ASS file with one event per font (each tagged with its own
    ``\\fn``), burned by a single ffmpeg/libass call onto a tall
    black frame - the same shaping engine the caption renderer uses,
    at the same style settings (Bold=1 included). Each font lives in
    its own horizontal band; the mask is the band's bright pixels,
    bbox-cropped. A candidate whose rendering runs off the frame's
    right edge is dropped rather than scored on a clipped mask.
    Returns {} on any failure (the suggestion is then absent).
    """
    import tempfile

    from ..font_registry import FONTS_DIR
    from ..media.captions import ass_font_name
    from ..media.ffmpeg import subtitles_filter_fragment

    clean = "".join(ch for ch in text if ch not in "{}\\\n\r").strip()
    if not clean or not stems:
        return {}
    band_h = int(font_px * 2.4) + 8
    width = 1600  # generous: candidates at one size vary ~+-30% in width
    height = band_h * len(stems)
    events = []
    for i, stem in enumerate(stems):
        name = ass_font_name(stem).replace(",", " ")
        y = i * band_h + band_h // 4
        events.append(
            "Dialogue: 0,0:00:00.00,0:00:01.00,Cand,,0,0,0,,"
            f"{{\\fn{name}\\fs{font_px}\\pos(24,{y})\\bord0\\shad0}}{clean}"
        )
    ass = "\n".join([
        "[Script Info]",
        "ScriptType: v4.00+",
        f"PlayResX: {width}",
        f"PlayResY: {height}",
        "WrapStyle: 2",
        "",
        "[V4+ Styles]",
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, "
        "OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, "
        "ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, "
        "Alignment, MarginL, MarginR, MarginV, Encoding",
        f"Style: Cand,Arial,{font_px},&H00FFFFFF,&H000000FF,&H00000000,"
        "&H00000000,1,0,0,0,100,100,0,0,1,0,0,7,0,0,0,1",
        "",
        "[Events]",
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, "
        "MarginV, Effect, Text",
        *events,
        "",
    ])
    try:
        with tempfile.TemporaryDirectory(prefix="fontmatch-") as tmp:
            ass_path = Path(tmp) / "candidates.ass"
            ass_path.write_text(ass, encoding="utf-8")
            fragment = subtitles_filter_fragment(ass_path, FONTS_DIR)
            result = subprocess.run(
                ["ffmpeg", "-v", "error", "-f", "lavfi", "-i",
                 f"color=c=black:size={width}x{height}:rate=1:duration=1",
                 "-vf", fragment, "-frames:v", "1",
                 "-pix_fmt", "gray", "-f", "rawvideo", "-"],
                capture_output=True, timeout=120,
            )
    except Exception:
        return {}
    expected = width * height
    if result.returncode != 0 or len(result.stdout) < expected:
        return {}
    frame = np.frombuffer(
        result.stdout[:expected], dtype=np.uint8
    ).reshape(height, width)
    masks: dict[str, np.ndarray] = {}
    for i, stem in enumerate(stems):
        band = frame[i * band_h:(i + 1) * band_h]
        # Threshold high: the reference mask is the k-means CORE of
        # the glyph (pure-colour pixels, anti-aliased fringe assigned
        # to its own clusters), so the candidate must be cut to its
        # core too - a soft threshold keeps the fringe and dilutes
        # every face's shape toward the same blob.
        bright = band > 200
        if not bright.any():
            continue
        ys, xs = np.nonzero(bright)
        if int(xs.max()) >= width - 1:
            continue  # clipped at the frame edge - not scoreable
        masks[stem] = bright[int(ys.min()):int(ys.max()) + 1,
                             int(xs.min()):int(xs.max()) + 1]
    return masks


def _mask_match_score(ref: np.ndarray, cand: np.ndarray) -> float:
    """Glyph-mask agreement of a candidate against the reference mask.

    Two terms, because bulk overlap alone cannot separate lookalike
    faces (their filled masks agree almost everywhere; the identity
    lives at the contours): the best IoU over a small x-shift search
    and +-4% width resamples (absorbing size-class error in the
    reference), minus a contour term - the symmetric chamfer distance
    between the two masks' edges at the best-IoU alignment,
    normalised by height. Returns a score in (-inf, 1]; identical
    masks score ~1.
    """
    import cv2

    if ref is None or cand is None or not ref.any() or not cand.any():
        return 0.0
    ref_h, ref_w = ref.shape
    scale = ref_h / max(cand.shape[0], 1)
    cand_r = cv2.resize(
        cand.astype(np.float32),
        (max(1, int(round(cand.shape[1] * scale))), ref_h),
        interpolation=cv2.INTER_AREA,
    ) > 0.5
    if not cand_r.any():
        return 0.0
    shift_max = max(2, int(round(0.05 * ref_w)))
    canvas_w = ref_w + 2 * shift_max
    ref_canvas = np.zeros((ref_h, canvas_w), bool)
    ref_canvas[:, shift_max:shift_max + ref_w] = ref
    ref_count = float(ref_canvas.sum())
    best = 0.0
    best_cand: Optional[np.ndarray] = None
    for width_scale in (0.96, 1.0, 1.04):
        if width_scale == 1.0:
            cand_w = cand_r
        else:
            cand_w = cv2.resize(
                cand_r.astype(np.float32),
                (max(1, int(round(cand_r.shape[1] * width_scale))), ref_h),
                interpolation=cv2.INTER_AREA,
            ) > 0.5
        base_x = shift_max + (ref_w - cand_w.shape[1]) // 2
        step = max(1, shift_max // 4)
        for dx in range(-shift_max, shift_max + 1, step):
            x = base_x + dx
            if x < 0 or x + cand_w.shape[1] > canvas_w:
                continue
            cand_canvas = np.zeros((ref_h, canvas_w), bool)
            cand_canvas[:, x:x + cand_w.shape[1]] = cand_w
            inter = float((ref_canvas & cand_canvas).sum())
            union = ref_count + float(cand_canvas.sum()) - inter
            if union > 0 and inter / union > best:
                best = inter / union
                best_cand = cand_canvas
    if best_cand is None:
        return 0.0
    # Contour term at the best alignment: distance from each mask's
    # edge pixels to the other mask, both ways, in units of height.
    kernel = np.ones((3, 3), np.uint8)
    ref_edge = ref_canvas & (
        cv2.dilate((~ref_canvas).astype(np.uint8), kernel, iterations=1) > 0
    )
    cand_edge = best_cand & (
        cv2.dilate((~best_cand).astype(np.uint8), kernel, iterations=1) > 0
    )
    if ref_edge.any() and cand_edge.any():
        dist_to_ref = cv2.distanceTransform(
            (~ref_canvas).astype(np.uint8), cv2.DIST_L2, 3
        )
        dist_to_cand = cv2.distanceTransform(
            (~best_cand).astype(np.uint8), cv2.DIST_L2, 3
        )
        chamfer = 0.5 * (
            float(dist_to_ref[cand_edge].mean())
            + float(dist_to_cand[ref_edge].mean())
        ) / max(ref_h, 1)
        best -= 0.5 * chamfer
    return best


def _font_match_ranking(
    records: list[dict[str, Any]],
) -> tuple[list[tuple[str, float]], int]:
    """Mean glyph-mask score per bundled font over the best records.

    Uses up to ``_FONT_MATCH_MAX_INSTANCES`` instances - the ones
    with the largest glyph masks that also carry OCR text - and the
    dominant line of each. Returns (ranking best-first, instances
    used). An empty ranking means the matcher could not run (no
    eligible instance, ffmpeg failure): absence, never a guess.
    """
    stems = _bundled_font_stems()
    if not stems:
        return [], 0
    eligible = []
    for record in records:
        mask = record.get("font_mask")
        words = record.get("ocr_words")
        if mask is None or not words:
            continue
        text = _ocr_line_text(words)
        if text is None or len(text) < _FONT_MATCH_MIN_TEXT_CHARS:
            continue
        line = _mask_widest_line(mask, int(record.get("lines") or 1))
        if line is None or int(line.sum()) < _FONT_MATCH_MIN_MASK_PIXELS:
            continue
        eligible.append((int(mask.sum()), line, text))
    eligible.sort(key=lambda item: -item[0])
    totals: dict[str, float] = {}
    counts: dict[str, int] = {}
    used = 0
    for _pixels, line, text in eligible[:_FONT_MATCH_MAX_INSTANCES]:
        font_px = int(round(line.shape[0] / _LOOK_GLYPH_EXTENT_PER_FONT_SIZE))
        font_px = max(12, min(160, font_px))
        masks = _render_candidate_masks(text, font_px, stems)
        if not masks:
            continue
        used += 1
        for stem, cand in masks.items():
            totals[stem] = totals.get(stem, 0.0) + _mask_match_score(line, cand)
            counts[stem] = counts.get(stem, 0) + 1
    if not used:
        return [], 0
    ranking = sorted(
        ((stem, totals[stem] / counts[stem]) for stem in totals),
        key=lambda item: -item[1],
    )
    return ranking, used


def _font_family_margin(
    ranking: list[tuple[str, float]], family: str
) -> Optional[float]:
    """The winner's margin over its best confusion-group mate.

    None when the winner has no group (nothing to be a coin flip
    against) or no mate was scored.
    """
    mates = _FONT_CONFUSION_GROUP_OF.get(family)
    if not mates:
        return None
    for stem, score in ranking[1:]:
        if stem in mates:
            return ranking[0][1] - score
    return None


def _gate_font_suggestion(
    ranking: list[tuple[str, float]], used: int
) -> Optional[dict[str, Any]]:
    """Apply the suggestion gates to a ranking, or return None.

    HONESTY CONTRACT: font identification from compressed caption
    pixels is not reliable, so a SUGGESTION exists only when the
    best bundled candidate clears the score floor, beats the
    runner-up by the gate margin, AND beats its own confusion
    group by the same margin. Below any bar: silence, not a guess.
    Wherever a suggestion surfaces it is labelled a guess, never
    an identification.
    """
    if len(ranking) < 2 or not used:
        return None
    (family, score), (runner_up, runner_score) = ranking[0], ranking[1]
    margin = score - runner_score
    if score < _FONT_SUGGESTION_MIN_SCORE or margin < _FONT_SUGGESTION_MIN_MARGIN:
        return None
    family_margin = _font_family_margin(ranking, family)
    if family_margin is not None and family_margin < _FONT_SUGGESTION_MIN_MARGIN:
        return None
    suggestion: dict[str, Any] = {
        "family": family,
        "score": round(score, 3),
        "margin": round(margin, 3),
        "runner_up": runner_up,
        "n": used,
    }
    if family_margin is not None:
        suggestion["family_margin"] = round(family_margin, 3)
    return suggestion


def _font_suggestion_for_records(
    records: list[dict[str, Any]],
) -> Optional[dict[str, Any]]:
    """The margin-gated font suggestion for a look, or None."""
    ranking, used = _font_match_ranking(records)
    return _gate_font_suggestion(ranking, used)


def _look_records_for_band(
    frames: list[tuple[float, np.ndarray]],
    pairs: dict[float, np.ndarray],
    band: tuple[float, float],
    bursts: Optional[dict[float, np.ndarray]] = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]], bool, int, list[dict[str, bool]]]:
    """Per-instance records for one caption band over decoded frames.

    Returns (records, colour_records, ocr_instances, ocr_available,
    total_words, anim_pairs). Paired frames (~0.4 s after the first
    few instances) contribute colour evidence - the karaoke highlight
    moves between the pair, so both frames see it - and, since stage
    2, animation evidence: the pair is OCR'd as well and compared
    against its instance (highlight movement, word growth, scale).
    Burst frames (+0.15 s) contribute scale evidence only.
    """
    records: list[dict[str, Any]] = []
    colour_records: list[dict[str, Any]] = []
    ocr_instances: list[dict[str, Any]] = []
    anim_pairs: list[dict[str, bool]] = []
    ocr_available = False
    total_words = 0
    for t, frame in frames:
        record = _look_frame_record(frame, band)
        if record is None:
            continue
        records.append(record)
        if record.get("font_centre") is not None:
            colour_records.append(record)
        words, available = _ocr_region_words(frame, record)
        ocr_available = ocr_available or available
        if words:
            total_words += len(words)
            record["ocr_words"] = words
            # Words-per-line = the words OCR found over the text
            # rows the edge pass counted in this same frame. The
            # TSV's own line grouping proved flaky (karaoke pop
            # splits one line into two groups); the row count is
            # our own measurement and is stable.
            ocr_instances.append({
                "words_per_line": len(words) / max(1, int(record.get("lines") or 1)),
                "upper": sum(ch.isupper() for w in words for ch in w["text"]),
                "lower": sum(ch.islower() for w in words for ch in w["text"]),
            })
        pair = pairs.get(t)
        if pair is not None:
            pair_record = _look_frame_record(pair, band)
            if pair_record is not None:
                if pair_record.get("font_centre") is not None:
                    colour_records.append(pair_record)
                pair_words, pair_available = _ocr_region_words(pair, pair_record)
                ocr_available = ocr_available or pair_available
                burst_record = None
                if bursts and bursts.get(t) is not None:
                    burst_record = _look_frame_record(bursts[t], band)
                anim_pairs.append(_animation_pair_evidence(
                    record, pair_record, burst_record,
                    [w["text"] for w in record.get("ocr_words") or []],
                    [w["text"] for w in pair_words or []],
                ))
    return records, colour_records, ocr_instances, ocr_available, total_words, anim_pairs


def _caption_look_pass_impl(
    path: Path,
    info: dict[str, Any],
    visual: dict[str, Any],
    audio: Optional[dict[str, Any]],
) -> Optional[dict[str, Any]]:
    width = int(info.get("width") or 0)
    height = int(info.get("height") or 0)
    duration = float(info.get("duration_sec") or 0.0)
    if width <= 0 or height <= 0 or duration <= 0:
        return None
    lo_t, hi_t = 0.05 * duration, 0.95 * duration
    votes = visual.get("caption_votes") or []
    vote_times = [
        i / _VISUAL_SAMPLE_FPS
        for i, vote in enumerate(votes)
        if vote and lo_t <= i / _VISUAL_SAMPLE_FPS <= hi_t
        and i / _VISUAL_SAMPLE_FPS < duration - 0.2
    ]
    position = visual.get("caption_position")
    vote_band = (
        _LOWER_BAND if position == "lower"
        else _CENTRE_BAND if position == "centre"
        else None
    )

    # Speech-like levels (the audio pass's window levels and 18 dB gap
    # convention), used to prefer instances over speech: captions over
    # music-only stretches are likelier lyrics or stylised text. Still
    # usable either way - the mix is recorded, not hidden.
    speech_like: dict[float, bool] = {}

    def mark_speech(times: list[float]) -> None:
        if speech_like or not info.get("has_audio"):
            return
        samples = _decode_audio(path)
        if samples is None or not len(samples):
            return
        levels = _window_levels_db(samples)
        if not len(levels):
            return
        reference = float(np.percentile(levels, 75))
        for t in times:
            idx = min(int(t / _AUDIO_WINDOW_SECONDS), len(levels) - 1)
            speech_like[t] = bool(levels[idx] >= reference - _GAP_DROP_DB)

    if vote_band is not None and len(vote_times) >= _LOOK_MIN_REGION_INSTANCES:
        sampling = "votes"
        bands = [vote_band]
        mark_speech(vote_times)
        spoken = [t for t in vote_times if speech_like.get(t)]
        pool = spoken if len(spoken) >= 4 else vote_times
        chosen = _evenly_spaced(pool, _LOOK_MAX_INSTANCES)
        if len(chosen) < _LOOK_MAX_INSTANCES and pool is not vote_times:
            rest = [t for t in vote_times if t not in chosen]
            chosen = sorted(chosen + _evenly_spaced(rest, _LOOK_MAX_INSTANCES - len(chosen)))
    else:
        # The 320 px vote is too sparse to sample from: its absolute
        # floor misses thin/low-contrast caption fonts outright (serif
        # and shadow-only styles measure a 0.0 caption fraction). Fall
        # back to an even grid at look resolution and let the hi-res
        # detection decide - absence still means < 3 readable instances.
        sampling = "grid"
        bands = [_LOWER_BAND, _CENTRE_BAND]
        chosen = [
            lo_t + (hi_t - lo_t) * i / (_LOOK_MAX_INSTANCES - 1)
            for i in range(_LOOK_MAX_INSTANCES)
        ]
        mark_speech(chosen)
    n_speech = sum(1 for t in chosen if speech_like.get(t))

    frames: list[tuple[float, np.ndarray]] = []
    pairs: dict[float, np.ndarray] = {}
    bursts: dict[float, np.ndarray] = {}
    for idx, t in enumerate(chosen):
        frame = _decode_look_frame(path, t, width, height)
        if frame is None:
            continue
        frames.append((t, frame))
        if idx < _LOOK_PAIRS:
            pair = _decode_look_frame(path, t + _LOOK_PAIR_OFFSET_SEC, width, height)
            if pair is not None:
                pairs[t] = pair
            burst = _decode_look_frame(path, t + _LOOK_BURST_OFFSET_SEC, width, height)
            if burst is not None:
                bursts[t] = burst
    if not frames:
        return None

    records: list[dict[str, Any]] = []
    colour_records: list[dict[str, Any]] = []
    ocr_instances: list[dict[str, Any]] = []
    anim_pairs: list[dict[str, bool]] = []
    ocr_available = False
    total_words = 0
    band_name = None
    for band in bands:
        records, colour_records, ocr_instances, ocr_available, total_words, anim_pairs = (
            _look_records_for_band(frames, pairs, band, bursts)
        )
        if len(records) >= _LOOK_MIN_REGION_INSTANCES:
            band_name = "lower" if band == _LOWER_BAND else "centre"
            break
    if len(records) < _LOOK_MIN_REGION_INSTANCES:
        return None

    look: dict[str, Any] = {
        "method": "edge-adjacency k-means colours + outline cluster + tesseract TSV",
        "sampling": sampling,
        "band": band_name,
        "instances": len(records),
        "frames": len(colour_records),
        "speech_like_instances": n_speech,
        "ocr": "tesseract" if ocr_available else "unavailable",
        "script": (
            ("latin" if total_words > 0 else "unknown_non_latin")
            if ocr_available else None
        ),
        "position_y": {
            "value": round(float(np.median([r["centroid_y"] for r in records])), 3),
            "n": len(records),
        },
    }
    if colour_records:
        look["font_color"] = {
            "value": _rgb_hex(np.median(
                [r["font_centre"] for r in colour_records], axis=0
            )),
            "n": len(colour_records),
        }
        highlights = [
            r["highlight_centre"] for r in colour_records
            if r.get("highlight_centre") is not None
        ]
        if len(highlights) >= max(2, int(np.ceil(0.3 * len(colour_records)))):
            look["highlight_color"] = {
                "value": _rgb_hex(np.median(highlights, axis=0)),
                "n": len(highlights),
            }
    size_class = _font_size_class(float(np.median(
        [r["line_height"] / r["frame_width"] for r in records]
    )))
    if size_class is not None:
        look["font_size"] = {"value": size_class, "n": len(records)}
    if ocr_instances:
        look["words_per_line"] = {
            "value": int(round(float(np.median(
                [o["words_per_line"] for o in ocr_instances]
            )))),
            "n": len(ocr_instances),
        }
        upper = sum(o["upper"] for o in ocr_instances)
        lower = sum(o["lower"] for o in ocr_instances)
        if upper + lower >= 20:
            frac = upper / (upper + lower)
            if frac >= 0.90:
                look["uppercase"] = {"value": True, "n": len(ocr_instances)}
            elif frac <= 0.60:
                look["uppercase"] = {"value": False, "n": len(ocr_instances)}

    verdict_floor = max(
        _LOOK_MIN_REGION_INSTANCES,
        int(np.ceil(_LOOK_VERDICT_MIN_FRACTION * len(records))),
    )
    stroke_votes = [r["stroke"] for r in records if r.get("stroke") is not None]
    if len(stroke_votes) >= verdict_floor:
        present = [v for v in stroke_votes if v[0] == "present"]
        if len(present) >= _LOOK_VERDICT_MIN_FRACTION * len(stroke_votes):
            ratio = float(np.median([v[2] for v in present]))
            look["stroke"] = {
                "present": True,
                "color": _rgb_hex(np.median([v[1] for v in present], axis=0)),
                "width_bucket": 5 if ratio >= 0.13 else 3,
                "n": len(stroke_votes),
            }
        elif len(present) <= (1 - _LOOK_VERDICT_MIN_FRACTION) * len(stroke_votes):
            look["stroke"] = {
                "present": False, "color": None, "width_bucket": 0,
                "n": len(stroke_votes),
            }
    shadow_votes = [r["shadow_vote"] for r in records if r.get("shadow_vote") is not None]
    if (
        len(shadow_votes) >= _LOOK_MIN_REGION_INSTANCES
        and float(np.mean(shadow_votes)) >= 0.75
    ):
        look["shadow"] = {"value": True, "n": len(shadow_votes)}
    box_votes = [r["box"] for r in records if r.get("box") is not None]
    if len(box_votes) >= verdict_floor:
        present = [v for v in box_votes if v[0] == "present"]
        if len(present) >= _LOOK_VERDICT_MIN_FRACTION * len(box_votes):
            alphas = [v[2] for v in present if v[2] is not None]
            if len(alphas) >= len(present) / 2:
                alpha = int(round(float(np.median(alphas)) * 255))
                look["background"] = {
                    "present": True, "color": f"#000000{alpha:02X}",
                    "n": len(box_votes),
                }
            else:
                look["background"] = {
                    "present": True,
                    "color": _rgb_hex(np.median([v[1] for v in present], axis=0)),
                    "n": len(box_votes),
                }
        elif len(present) <= (1 - _LOOK_VERDICT_MIN_FRACTION) * len(box_votes):
            look["background"] = {"present": False, "color": None, "n": len(box_votes)}

    # Stage 2: animation feel from the pair/burst evidence of the
    # winning band. Information only - no render application.
    look["animation"] = _classify_animation(anim_pairs)

    # Stage 2: the margin-gated font suggestion. Absent whenever the
    # matcher could not run or the gate did not clear - the look
    # never carries a coin flip.
    suggestion = _font_suggestion_for_records(records)
    if suggestion is not None:
        look["font_suggestion"] = suggestion
    return look


def _cuts_from_diffs_robust(diffs: list[tuple[float, float]]) -> list[float]:
    """Scene cuts from frame differences, robust for dense-cut shorts.

    The pipeline's ``_scene_cuts_from_diffs`` thresholds at mean + 3.5 sigma,
    which goes blind when cuts are frequent (the spikes become the mean).
    This detector thresholds against the *median* difference instead:
    local maxima above max(16, median + 6 sigma-by-MAD), with a 0.5 s
    refractory so one cut cannot fire twice.
    """
    if len(diffs) < 3:
        return []
    values = np.array([d for _, d in diffs], dtype=np.float64)
    median = float(np.median(values))
    mad_sigma = 1.4826 * float(np.median(np.abs(values - median)))
    threshold = max(16.0, median + 6.0 * mad_sigma)
    cuts: list[float] = []
    for i, (t, d) in enumerate(diffs):
        if d <= threshold:
            continue
        if i > 0 and d < diffs[i - 1][1]:
            continue
        if i < len(diffs) - 1 and d < diffs[i + 1][1]:
            continue
        if cuts and t - cuts[-1] < 0.5:
            continue  # refractory: one cut cannot fire twice
        cuts.append(t)
    return cuts


# ---------------------------------------------------------------------------
# Audio pass
# ---------------------------------------------------------------------------

def _decode_audio(path: Path) -> Optional[np.ndarray]:
    """Mono 16 kHz float samples, or None when there is no usable audio."""
    from ..media.ffmpeg import select_audio_stream_index

    stream = select_audio_stream_index(path)
    if stream is None:
        return None
    result = subprocess.run(
        [
            "ffmpeg", "-v", "error", "-i", str(path),
            "-map", f"0:a:{stream}", "-vn", "-ac", "1",
            "-ar", str(_AUDIO_RATE), "-f", "f32le", "-",
        ],
        capture_output=True, timeout=300,
    )
    if result.returncode != 0 or not result.stdout:
        return None
    return np.frombuffer(result.stdout, dtype=np.float32).astype(np.float64)


def _window_levels_db(samples: np.ndarray) -> np.ndarray:
    """50 ms window levels in dBFS - the gap_activity window convention."""
    size = int(_AUDIO_RATE * _AUDIO_WINDOW_SECONDS)
    count = len(samples) // size
    if count == 0:
        return np.zeros(0, dtype=np.float64)
    blocks = samples[: count * size].reshape(count, size)
    return 20.0 * np.log10(np.sqrt((blocks**2).mean(axis=1)) + 1e-6)


def _power_mean_db(levels: np.ndarray) -> float:
    """Power-domain mean of dB values (mirrors clip_qc._power_mean_db)."""
    if len(levels) == 0:
        return -120.0
    return float(10.0 * np.log10(np.mean(np.power(10.0, levels / 10.0))))


def _spectral_flatness(samples: np.ndarray) -> Optional[float]:
    """Mean spectral flatness (0 = pure tone, 1 = white noise)."""
    if len(samples) < 4096:
        return None
    frame, hop = 2048, 1024
    window = np.hanning(frame)
    values = []
    for start in range(0, min(len(samples), _AUDIO_RATE * 60) - frame, hop):
        mag = np.abs(np.fft.rfft(samples[start:start + frame] * window)) + 1e-12
        values.append(float(np.exp(np.mean(np.log(mag))) / np.mean(mag)))
    return float(np.mean(values)) if values else None


def _estimate_tempo_bpm(samples: np.ndarray) -> tuple[Optional[float], Optional[float]]:
    """Onset-envelope autocorrelation tempo. Returns (bpm, strength)."""
    frame, hop = 2048, 512
    if len(samples) < frame * 4:
        return None, None
    window = np.hanning(frame)
    mags = []
    for start in range(0, len(samples) - frame, hop):
        mags.append(np.abs(np.fft.rfft(samples[start:start + frame] * window)))
    if len(mags) < 32:
        return None, None
    mags_arr = np.array(mags)
    flux = np.maximum(0.0, np.diff(mags_arr, axis=0)).sum(axis=1)
    flux = flux - flux.mean()
    if flux.std() <= 0:
        return None, None
    autocorr = np.correlate(flux, flux, mode="full")[len(flux) - 1:]
    env_rate = _AUDIO_RATE / hop
    min_lag = max(1, int(env_rate * 60.0 / _TEMPO_MAX_BPM))
    max_lag = min(len(autocorr) - 1, int(env_rate * 60.0 / _TEMPO_MIN_BPM))
    if max_lag <= min_lag:
        return None, None
    segment = autocorr[min_lag:max_lag + 1]
    peak = int(np.argmax(segment)) + min_lag
    floor = float(np.median(segment)) if len(segment) else 0.0
    # The median autocorrelation can sit at/below zero on sparse onset
    # envelopes; fall back to a fraction of the zero-lag energy so the
    # strength stays a meaningful ratio instead of vanishing.
    denom = floor if floor > 0 else 0.02 * float(autocorr[0])
    strength = float(autocorr[peak] / denom) if denom > 0 else None
    if (
        strength is None
        or strength < _TEMPO_STRENGTH_MIN
        or autocorr[peak] < 0.1 * autocorr[0]
    ):
        return None, strength
    return round(60.0 * env_rate / peak, 1), strength


def _audio_measurements(samples: Optional[np.ndarray]) -> Optional[dict[str, Any]]:
    if samples is None or len(samples) == 0:
        return None
    levels = _window_levels_db(samples)
    if len(levels) == 0:
        return None
    speech_ref = float(np.percentile(levels, 75))
    gap_threshold = speech_ref - _GAP_DROP_DB
    win = _AUDIO_WINDOW_SECONDS
    min_run = max(1, int(_GAP_RUN_MIN_SECONDS / win))

    gap_idx: list[int] = []
    run_start: Optional[int] = None
    is_gap = levels < gap_threshold
    for i, flag in enumerate(is_gap):
        if flag and run_start is None:
            run_start = i
        elif not flag and run_start is not None:
            if i - run_start >= min_run:
                gap_idx.extend(range(run_start, i))
            run_start = None
    if run_start is not None and len(is_gap) - run_start >= min_run:
        gap_idx.extend(range(run_start, len(is_gap)))
    gap_seconds = len(gap_idx) * win
    loud_fraction = float(np.mean(levels >= gap_threshold))

    size = int(_AUDIO_RATE * win)
    gap_samples = (
        np.concatenate([samples[i * size:(i + 1) * size] for i in gap_idx])
        if gap_idx else np.zeros(0)
    )
    gap_rms = _power_mean_db(levels[gap_idx]) if gap_idx else -120.0
    gap_flatness = _spectral_flatness(gap_samples) if len(gap_samples) else None

    bed: Optional[bool] = None
    method: Optional[str] = None
    if gap_seconds >= _GAP_TOTAL_MIN_SECONDS:
        method = "speech_gaps"
        bed = bool(
            gap_rms > _GAP_SILENCE_DB
            and gap_flatness is not None
            and gap_flatness <= _GAP_FLATNESS_MAX
        )
    else:
        overall_flatness = _spectral_flatness(samples)
        if (
            overall_flatness is not None
            and overall_flatness <= _CONTINUOUS_FLATNESS_MAX
            and loud_fraction >= _CONTINUOUS_LOUD_FRACTION
            and _power_mean_db(levels) > -45.0
        ):
            bed, method = True, "continuous_tonal"

    tempo_bpm, tempo_strength = _estimate_tempo_bpm(samples)
    first_second = levels[: max(1, int(1.0 / win))]
    loud_open = bool(
        len(first_second)
        and float(first_second.max()) >= float(np.percentile(levels, 95)) - _LOUD_OPEN_TOLERANCE_DB
    )
    return {
        "music_bed_present": bed,
        "music_bed_method": method,
        "gap_seconds": round(gap_seconds, 2),
        "gap_rms_db": round(gap_rms, 2) if gap_idx else None,
        "gap_spectral_flatness": round(gap_flatness, 3) if gap_flatness is not None else None,
        "tempo_bpm": tempo_bpm,
        "tempo_strength": round(tempo_strength, 2) if tempo_strength is not None else None,
        "loud_fraction": round(loud_fraction, 3),
        "speech_reference_db": round(speech_ref, 2),
        "loud_open": loud_open,
        # Speech-like structure: real gaps between loud stretches. This is
        # what makes ducking meaningful in the mapping below.
        "speech_present": bool(
            gap_seconds >= _GAP_TOTAL_MIN_SECONDS and 0.05 <= loud_fraction <= 0.95
        ),
    }


# ---------------------------------------------------------------------------
# Measurement assembly
# ---------------------------------------------------------------------------

def _shot_stats(cuts: list[float], duration: float) -> dict[str, Any]:
    boundaries = [0.0] + sorted(c for c in cuts if 0.0 < c < duration) + [duration]
    shots = [b - a for a, b in zip(boundaries, boundaries[1:]) if b - a > 0.01]
    if not shots:
        shots = [duration]
    arr = np.array(shots, dtype=np.float64)
    return {
        "cut_count": len(boundaries) - 2,
        "cuts_per_min": round((len(boundaries) - 2) / duration * 60.0, 2) if duration > 0 else 0.0,
        "shot_sec": {
            "mean": round(float(arr.mean()), 2),
            "median": round(float(np.median(arr)), 2),
            "p10": round(float(np.percentile(arr, 10)), 2),
            "p90": round(float(np.percentile(arr, 90)), 2),
        },
        "first_cut_sec": round(float(boundaries[1]), 2) if len(boundaries) > 2 else None,
    }


def _hook_measurements(
    face_samples: list, width: int, height: int,
    audio: Optional[dict[str, Any]], caption_from_start: bool,
    first_cut_sec: Optional[float],
) -> dict[str, Any]:
    frame_area = float(width * height) if width and height else 0.0
    face_frames = 0
    closeup = False
    face_first3 = False
    for t, boxes in face_samples:
        if boxes:
            face_frames += 1
            if t <= _HOOK_WINDOW_SECONDS:
                face_first3 = True
                if frame_area and max(b[2] * b[3] for b in boxes) / frame_area >= _FACE_CLOSEUP_MIN_AREA_FRAC:
                    closeup = True
    total = len(face_samples)
    return {
        "opens_on_face_closeup": closeup,
        "face_in_first_3s": face_first3,
        "face_frame_fraction": round(face_frames / total, 3) if total else 0.0,
        "loud_open": bool(audio and audio["loud_open"]),
        "caption_from_start": bool(caption_from_start),
        "first_cut_sec": first_cut_sec,
    }


def measure_reference(path: Path) -> dict[str, Any]:
    """Measure one reference video file. See the module docstring for the
    method and honesty grade of every field."""
    path = Path(path)
    info = probe_media(path)
    duration = float(info["duration_sec"])

    face_samples: list = []
    reframing_cuts: Optional[list[float]] = None
    try:
        reframing_cuts, face_samples, _ = _reframing_pass(path)
    except Exception:
        reframing_cuts = None
    visual = _visual_pass(path, int(info["width"]), int(info["height"]))
    robust_cuts = _cuts_from_diffs_robust(visual.get("diffs") or [])
    if reframing_cuts is None:
        cuts, cuts_method = robust_cuts, "robust_diff_fallback"
    else:
        # Union with a 0.34 s dedupe window (one sample step at 3 fps):
        # the pipeline detector is authoritative where it fires, the
        # robust detector adds the dense cuts its threshold cannot see.
        cuts = list(reframing_cuts)
        for t in robust_cuts:
            if all(abs(t - kept) > 0.34 for kept in cuts):
                cuts.append(t)
        cuts.sort()
        cuts_method = "reframing+robust_diff" if len(cuts) > len(reframing_cuts) else "reframing"

    shots = _shot_stats(cuts, duration)
    audio = _audio_measurements(_decode_audio(path)) if info["has_audio"] else None
    look = _caption_look_pass(path, info, visual, audio)
    hook = _hook_measurements(
        face_samples, int(info["width"]), int(info["height"]), audio,
        bool(visual.get("caption_from_start")), shots["first_cut_sec"],
    )
    return {
        "version": ANALYSER_VERSION,
        "source": {"file_name": path.name, **info},
        "cuts": {**shots, "method": cuts_method},
        "captions": {
            "frame_fraction": round(float(visual.get("caption_frame_fraction", 0.0)), 3),
            "position": visual.get("caption_position"),
            "from_start": bool(visual.get("caption_from_start")),
            "sampled_frames": int(visual.get("frames", 0)),
            "look": look,
        },
        "colour": {
            "mean_saturation": round(float(visual.get("mean_saturation", 0.0)), 3),
            "mean_brightness": round(float(visual.get("mean_brightness", 0.0)), 3),
            "luma_contrast": round(float(visual.get("luma_contrast", 0.0)), 2),
            "mean_hue_deg": (
                round(float(visual["mean_hue_deg"]), 1)
                if visual.get("mean_hue_deg") is not None else None
            ),
        },
        "audio": audio,
        "hook": hook,
    }



# ---------------------------------------------------------------------------
# Mapping: measurements -> profile (table in the module docstring)
# ---------------------------------------------------------------------------

def _cuts_band(cpm: float) -> tuple[int, int]:
    for limit, band in _CPM_BANDS:
        if cpm < limit:
            return band
    return _CPM_TOP_BAND


# A base-niche margin below this is a near-tie, not a confident verdict.
# The scorer's smallest single step is +/-0.5 (one binary term: music-bed
# agreement or the ken-burns scenic bonus), so 0.25 is half of one step:
# when the two best donors are closer than that, the winner is decided by
# less than half of one binary judgement. Chosen against measured
# margins: the reproduced Japan-slice case sits at 0.079 and Luffy's funk
# reference at an exact 0.0 tie, while a clear pacing win (32 cpm funk
# edit) sits at 0.35. A 0.5 threshold was rejected: on a 153-case grid
# (cpm 1-80 x captions x bed) it leaves "confident" reachable only at
# 80 cpm, i.e. the confident wording would be dead in practice.
BASE_NICHE_CLOSE_CALL_MARGIN = 0.25


def score_base_niches(measurements: dict[str, Any]) -> list[dict[str, Any]]:
    """Every stock profile's base-niche score, best first.

    Same formula as the module docstring: 0.6 * |log2(measured cpm /
    stock band centre)|, +0.5 on music-bed disagreement, -0.5 for a
    ken-burns stock profile on a scenic reference. Rows are sorted by
    (raw score, niche): equal scores break alphabetically, exactly as
    select_base_niche always did. Scores are rounded to 4 decimals for
    display; the sort uses the raw score so rounding can never flip a
    winner.
    """
    cpm = max(1.0, float(measurements["cuts"]["cuts_per_min"]))
    bed = (measurements.get("audio") or {}).get("music_bed_present")
    scenic = (
        float(measurements["cuts"]["cuts_per_min"]) < 8
        and float(measurements["captions"]["frame_fraction"]) < 0.10
    )
    rows: list[tuple[float, str]] = []
    for niche in sorted(NICHE_VALUES):
        profile = load_profile(niche)
        lo, hi = profile["pacing"]["target_cuts_per_min"]
        centre = math.sqrt(lo * hi)
        score = 0.6 * abs(math.log2(cpm / centre))
        if bed is not None and (profile["audio"]["music_mood"] != "none") != bool(bed):
            score += 0.5
        if scenic and profile["zooms"]["ken_burns"]:
            score -= 0.5
        rows.append((score, niche))
    rows.sort(key=lambda row: (row[0], row[1]))
    return [{"niche": niche, "score": round(score, 4)} for score, niche in rows]


def select_base_niche(measurements: dict[str, Any]) -> str:
    """Nearest stock profile by pacing proximity, then music-bed agreement.

    Deterministic: score = 0.6 * |log2(measured cpm / stock band centre)|
    plus 0.5 when the stock profile's music expectation (mood != "none")
    disagrees with the measured bed, minus 0.5 for a ken-burns stock
    profile when the reference is scenic (slow, caption-free); ties break
    alphabetically. The winner is a pacing match, not a genre verdict:
    use score_base_niches / the mapping report's margin to tell a
    confident pick from a near-tie (margin below
    BASE_NICHE_CLOSE_CALL_MARGIN).
    """
    scored = score_base_niches(measurements)
    assert scored
    return scored[0]["niche"]


def _caption_look_override(look: Any) -> Optional[dict[str, Any]]:
    """Map a measured caption look onto a partial caption template.

    The dict is in exactly the ``brand_template_override`` shape - a
    partial template the render path merges over the named preset -
    because that seam already exists and the renderer already accepts
    every field used here. Only measured fields are stamped; a measured
    *absent* outline or box stamps its off state (stroke_color None /
    background False), which is a real part of the reference's look,
    while shadow is presence-only (its absence is not provable at
    FAIR grade) and an unmeasured look stamps nothing at all.
    """
    if not isinstance(look, dict):
        return None

    def value(field: str) -> Any:
        item = look.get(field)
        return item.get("value") if isinstance(item, dict) else None

    override: dict[str, Any] = {}
    position_y = value("position_y")
    if position_y is not None:
        override["position_y"] = round(float(position_y), 3)
    font_color = value("font_color")
    if font_color:
        override["font_color"] = font_color
    highlight_color = value("highlight_color")
    if highlight_color:
        override["highlight_color"] = highlight_color
    words_per_line = value("words_per_line")
    if words_per_line:
        override["max_words_per_line"] = int(words_per_line)
    uppercase = value("uppercase")
    if isinstance(uppercase, bool):
        override["uppercase"] = uppercase
    stroke = look.get("stroke")
    if isinstance(stroke, dict):
        if stroke.get("present"):
            override["stroke_color"] = stroke.get("color")
            override["stroke_width"] = int(stroke.get("width_bucket") or 3)
        else:
            override["stroke_color"] = None
            override["stroke_width"] = 0
    if value("shadow") is True:
        override["shadow"] = True
    background = look.get("background")
    if isinstance(background, dict):
        if background.get("present"):
            override["background"] = True
            override["background_color"] = background.get("color")
        else:
            override["background"] = False
            override["background_color"] = None
    font_size = value("font_size")
    if font_size:
        override["font_size"] = int(font_size)
    # Stage 2: the font suggestion stamps font_family in the same
    # precedence slot as the rest of the look. It exists in the look
    # only when its score + margin gate cleared, and it is a guess -
    # the summary labels it as one.
    suggestion = look.get("font_suggestion")
    if isinstance(suggestion, dict) and suggestion.get("family"):
        override["font_family"] = suggestion["family"]
    return override or None


def _caption_look_summary(look: dict[str, Any]) -> dict[str, Any]:
    """Flat measured values for the profile's provenance block."""
    summary: dict[str, Any] = {}
    for field in (
        "position_y", "font_color", "highlight_color",
        "words_per_line", "uppercase", "font_size",
    ):
        item = look.get(field)
        if isinstance(item, dict) and "value" in item:
            summary[field] = item["value"]
    stroke = look.get("stroke")
    if isinstance(stroke, dict):
        summary["stroke"] = (
            f"{stroke.get('color')} width {stroke.get('width_bucket')}"
            if stroke.get("present") else "none"
        )
    background = look.get("background")
    if isinstance(background, dict):
        summary["background"] = (
            background.get("color") if background.get("present") else "none"
        )
    if isinstance(look.get("shadow"), dict):
        summary["shadow"] = True
    animation = look.get("animation")
    if isinstance(animation, dict) and animation.get("value"):
        summary["animation"] = animation["value"]
    suggestion = look.get("font_suggestion")
    if isinstance(suggestion, dict) and suggestion.get("family"):
        # The honesty contract, verbatim, at every surface: this is
        # the closest bundled font - a guess - never an identification.
        summary["font_guess"] = (
            f"{suggestion['family']} (closest bundled font — a guess)"
        )
    return summary


def build_profile(
    measurements: dict[str, Any], name: Optional[str] = None
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Map measurements onto a profile in the existing schema.

    Returns (profile, mapping_report). The profile starts from the base
    niche's stock profile; every rule that fires is recorded in the
    report's ``inferred`` dict, everything else is listed as inherited.
    """
    # The base niche only labels the style; the measured values above
    # drive the edit. Its margin over the runner-up decides whether the
    # label may be shown as a confident pick or must be shown as the
    # close pacing match it is (see BASE_NICHE_CLOSE_CALL_MARGIN).
    scored = score_base_niches(measurements)
    base_niche = scored[0]["niche"]
    runner_up = scored[1]["niche"] if len(scored) > 1 else None
    margin = (
        round(scored[1]["score"] - scored[0]["score"], 4)
        if len(scored) > 1
        else None
    )
    close_call = margin is not None and margin < BASE_NICHE_CLOSE_CALL_MARGIN
    profile = load_profile(base_niche)
    inferred: dict[str, str] = {}

    cuts = measurements["cuts"]
    cpm = float(cuts["cuts_per_min"])
    captions = measurements["captions"]
    cap_frac = float(captions["frame_fraction"])
    colour = measurements["colour"]
    audio = measurements.get("audio")
    hook = measurements["hook"]

    # --- pacing (invariant: min_shot = 60/hi, max_shot = 60/lo) ---------
    if cuts.get("method") != "unavailable":
        lo, hi = _cuts_band(cpm)
        profile["pacing"]["target_cuts_per_min"] = [lo, hi]
        profile["pacing"]["min_shot_sec"] = round(60 / hi, 2)
        profile["pacing"]["max_shot_sec"] = round(60 / lo, 2)
        profile["pacing"]["silence_trim_aggressive"] = cpm >= 12
        inferred["pacing"] = f"cuts_per_min={cpm} -> band [{lo}, {hi}]"

    # --- transitions ------------------------------------------------------
    scenic_slow = cpm < 8 and cap_frac < 0.10
    if cpm >= 20:
        default, duration, on_topic = "cut", 0.0, "cut"
    elif cpm >= 8 or not scenic_slow:
        default, duration, on_topic = "hard_cut", 0.05, "hard_cut"
    else:
        default, duration, on_topic = "wipe", 0.4, "fade"
    profile["transitions"]["default"] = default
    profile["transitions"]["duration_sec"] = duration
    # N1: on_topic_change and on_punchline are director guidance only.
    # They are measured and stored here, handed to the AI director via
    # the plan prompt, and never stamped onto EDL joins by build_edl.
    # No per-join topic or punchline signal exists at build time, and
    # "zoom_bounce" is not a renderer transition name.
    profile["transitions"]["on_topic_change"] = on_topic
    if hook["opens_on_face_closeup"] or cpm >= 20:
        profile["transitions"]["on_punchline"] = "zoom_bounce"
    elif scenic_slow:
        profile["transitions"]["on_punchline"] = "wipe"
    else:
        profile["transitions"]["on_punchline"] = "cut"
    inferred["transitions"] = f"cuts_per_min={cpm}, face_hook={hook['opens_on_face_closeup']}"

    # --- zooms ------------------------------------------------------------
    if cpm >= 30:
        scale = 1.30
    elif cpm >= 20:
        scale = 1.25
    elif cpm >= 12:
        scale = 1.20
    elif cpm >= 6:
        scale = 1.15
    else:
        scale = 1.08
    profile["zooms"]["punch_in_scale"] = scale
    tempo = (audio or {}).get("tempo_bpm")
    if tempo and audio and audio.get("music_bed_present"):
        profile["zooms"]["punch_in_trigger"] = "energy_peak"
        inferred["zooms.punch_in_trigger"] = f"tempo {tempo} BPM over a music bed"
    elif cap_frac >= 0.4 and cpm < 12:
        profile["zooms"]["punch_in_trigger"] = "emphasis"
        inferred["zooms.punch_in_trigger"] = "caption-led talking style"
    profile["zooms"]["ken_burns"] = bool(cpm < 8 and cap_frac < 0.10)
    inferred["zooms"] = f"punch_in_scale={scale}, ken_burns={profile['zooms']['ken_burns']}"

    # --- captions -----------------------------------------------------------
    position = captions.get("position")
    if cap_frac >= 0.40:
        if position == "centre":
            preset, words, pos = "large_centre_pop", 3, "center"
        elif hook["face_frame_fraction"] >= 0.3:
            preset, words, pos = "hormozi", 3, "bottom"
        else:
            preset, words, pos = "bold_uppercase", 3, "bottom"
    elif cap_frac >= 0.10:
        preset, words = "classic_box", 4
        pos = "center" if position == "centre" else "bottom"
    else:
        preset, words, pos = "minimal", 4, "bottom"
    profile["captions"]["preset_id"] = preset
    profile["captions"]["words_per_line"] = words
    profile["captions"]["position"] = pos
    inferred["captions"] = f"frame_fraction={cap_frac}, position={position} -> {preset}"

    # --- caption look (only when the look pass measured one) --------------
    look_override = _caption_look_override(captions.get("look"))
    if look_override is not None:
        profile["captions"]["look_override"] = look_override
        inferred["caption_look"] = (
            "measured caption appearance -> template override fields: "
            + ", ".join(sorted(look_override))
        )

    # --- audio --------------------------------------------------------------
    if audio is not None and audio.get("music_bed_present") is not None:
        if audio["music_bed_present"]:
            if tempo and tempo >= 128:
                mood = "energetic"
            elif tempo and tempo >= 100:
                mood = "upbeat"
            else:
                mood = "calm"
            profile["audio"]["music_mood"] = mood
            profile["audio"]["music_volume_0_to_1"] = 0.2
            profile["audio"]["ducking_db"] = -12.0 if audio.get("speech_present") else -6.0
        else:
            profile["audio"]["music_mood"] = "none"
            profile["audio"]["music_volume_0_to_1"] = 0.0
            profile["audio"]["ducking_db"] = 0.0
        inferred["audio"] = (
            f"bed={audio['music_bed_present']} via {audio.get('music_bed_method')}, "
            f"tempo={tempo} -> mood={profile['audio']['music_mood']}"
        )
    profile["audio"]["sfx_from_user_library"] = cpm >= 15
    inferred["audio.sfx_from_user_library"] = f"cuts_per_min={cpm} >= 15 (weak inference)"

    # --- colour ---------------------------------------------------------------
    saturation = float(colour["mean_saturation"])
    contrast = float(colour["luma_contrast"])
    hue = colour.get("mean_hue_deg")
    if saturation >= 0.45 and contrast >= 28:
        lut = "punchy"
    elif hue is not None and 5.0 <= hue <= 45.0 and saturation >= 0.15:
        lut = "warm_soft"
    else:
        lut = "none"
    profile["color"]["lut"] = lut
    inferred["color.lut"] = f"saturation={saturation}, contrast={contrast}, hue={hue} -> {lut}"

    # --- hook -------------------------------------------------------------------
    # Hook title duration is deliberately NOT inferred. The hook
    # measurements are signals (face close-up, loud open, caption from
    # start, first-cut timing) — none of them measures how long a title
    # actually stays on screen in the reference, so writing 2-3 s here
    # would be a guess that silently un-pins the title (the title is
    # pinned for the whole clip by default; see auto_director/edl.py).
    # A shorter title duration is recorded only as an explicit profile
    # choice: hook.title_seconds together with
    # hook.title_seconds_explicit = true, which the EDL then honours.

    not_inferred = [
        "niche (set to the base niche so the existing loader accepts it)",
        "overlays.* (not measurable from a finished short)",
        "broll.* (stock B-roll is indistinguishable from owned footage)",
        "hook.title_style (a wording strategy, not an audiovisual measure)",
        "hook.title_seconds (title duration is not measured; the pinned-for-the-whole-clip default applies)",
    ]
    if "audio" not in inferred:
        not_inferred.append("audio.music_* (music bed unknown or no audio)")
    if look_override is None:
        not_inferred.append(
            "caption_look (no caption appearance measured - too few "
            "captions, no readable region, or analysis unavailable)"
        )
    if cuts.get("method") == "unavailable":
        not_inferred.append("pacing.* (scene-cut measurement unavailable)")

    profile["reference_style"] = {
        "version": ANALYSER_VERSION,
        "name": name,
        "base_niche": base_niche,
        "base_niche_margin": margin,
        "base_niche_runner_up": runner_up,
        "base_niche_close_call": close_call,
        "source_file": measurements["source"]["file_name"],
        "headline": {
            "cuts_per_min": cpm,
            "caption_frame_fraction": cap_frac,
            "music_bed_present": (audio or {}).get("music_bed_present"),
            "tempo_bpm": tempo,
        },
    }
    if isinstance(captions.get("look"), dict):
        profile["reference_style"]["caption_look"] = _caption_look_summary(
            captions["look"]
        )
    report = {
        "base_niche": base_niche,
        "base_niche_margin": margin,
        "base_niche_runner_up": runner_up,
        "base_niche_close_call": close_call,
        "inferred": inferred,
        "not_inferred": not_inferred,
    }
    return profile, report


def analyse_reference(
    path: Path, name: Optional[str] = None
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    """Full pipeline: (measurements, profile, mapping_report)."""
    measurements = measure_reference(Path(path))
    profile, report = build_profile(measurements, name=name)
    return measurements, profile, report
