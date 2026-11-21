"""Per-clip audio overrides for the Audio Studio panel.

A clip's audio overrides are the user's restyle of one finished clip's
sound: the music bed (track / volume / ducking), voice cleanup (noise
reduction, presence clarity), the loudness target and edge fades. They
are stored on the clip's edit plan next to the caption and look
overrides (see ``clip_edits.build_edit_plan``) and consumed by the
Lane-5 re-render seam, which stamps a render-ready copy onto the EDL
(``edl["audio_studio"]``) for the audio finisher
(``media.edl_render.build_edl_audio_graph``).

The stored form is deliberately PARTIAL: only the fields the user
actually set are kept, so untouched fields keep following the profile /
EDL audio block the clip was built with. Fields whose neutral value
means "off" (cleanup strengths, fades) drop their zeroes at validation
time; fields where zero is a real choice (music volume 0 = mute,
ducking 0 = no ducking) keep them.

This module is pure (no I/O, no ffmpeg): it validates plain dicts and
maps them to the exact ffmpeg filter fragments the renderer splices
into the audio graph, and every impossible value raises ``ValueError``
with a message the API layer can surface as a 422 — the same contract
as ``auto_director.look`` and ``clip_edits.normalize_caption_overrides``.
"""

from __future__ import annotations

import math
from typing import Any, Optional

# --- Field space -------------------------------------------------------------
# Music volume is a percentage of the bed's full level; ducking is the
# reduction (in dB) the sidechain compressor is asked to pull the bed
# down by under speech — the same amount space the profiles' own
# ``ducking_db`` uses once its sign is dropped (see edl_render).
MUSIC_VOLUME_MIN = 0
MUSIC_VOLUME_MAX = 100
DUCKING_DB_MIN = 0
DUCKING_DB_MAX = 30
CLEANUP_MIN = 0
CLEANUP_MAX = 100
FADE_MAX_SECONDS = 5.0

# Loudness choices: "auto" is today's in-graph loudnorm (-14 LUFS,
# media.common.LOUDNORM_FILTER), "-16" the quieter podcast target and
# "off" no loudness normalisation at all.
LOUDNESS_AUTO = "auto"
LOUDNESS_QUIET = "-16"
LOUDNESS_OFF = "off"
LOUDNESS_CHOICES = (LOUDNESS_AUTO, LOUDNESS_QUIET, LOUDNESS_OFF)
LOUDNORM_AUTO_FILTER = "loudnorm=I=-14:TP=-1.5:LRA=11"
LOUDNORM_QUIET_FILTER = "loudnorm=I=-16:TP=-1.5:LRA=11"

# Music track sentinels: "auto" clears an explicit pick (the profile /
# Auto-bed logic decides, exactly like a task with no music_override)
# and "none" silences the bed for this clip.
MUSIC_TRACK_AUTO = "auto"
MUSIC_TRACK_NONE = "none"

_AUDIO_FIELDS = {
    "music_track",
    "music_volume",
    "ducking_db",
    "noise_reduction",
    "voice_clarity",
    "loudness",
    "fade_in",
    "fade_out",
}


# --- Validation ---------------------------------------------------------------

def _int_value(value: Any, name: str, minimum: int, maximum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{name} must be a number")
    number = float(value)
    if not math.isfinite(number):
        raise ValueError(f"{name} must be a finite number")
    rounded = int(round(number))
    if not minimum <= rounded <= maximum:
        raise ValueError(f"{name} must be between {minimum} and {maximum}")
    return rounded


def _fade_value(value: Any, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{name} must be a number")
    number = float(value)
    if not math.isfinite(number):
        raise ValueError(f"{name} must be a finite number")
    rounded = round(number, 1)
    if not 0.0 <= rounded <= FADE_MAX_SECONDS:
        raise ValueError(f"{name} must be between 0 and {FADE_MAX_SECONDS}")
    return rounded


def normalize_audio_overrides(raw: Any) -> Optional[dict[str, Any]]:
    """Validate an audio override block; None input stays None.

    Returns the PARTIAL block to store on the edit plan, with only the
    fields the input actually set (zero cleanup strengths and zero
    fades dropped — absence already means "off"). A block that sets
    nothing normalises to None so callers can treat "no audio
    overrides" uniformly. The music filename itself is only shape
    checked here (a plain basename); the API layer validates it
    against the live library, mirroring ``music_override``.
    """
    if raw is None:
        return None
    if not isinstance(raw, dict):
        raise ValueError("audio must be an object")
    unknown = sorted(set(raw) - _AUDIO_FIELDS)
    if unknown:
        raise ValueError(f"unknown audio fields: {', '.join(unknown)}")
    audio: dict[str, Any] = {}
    if raw.get("music_track") is not None:
        track = raw["music_track"]
        if not isinstance(track, str) or not track.strip():
            raise ValueError("audio.music_track must be a non-empty string")
        track = track.strip()
        if track not in (MUSIC_TRACK_AUTO, MUSIC_TRACK_NONE):
            from pathlib import Path

            if track != Path(track).name or track in {".", ".."}:
                raise ValueError(
                    "audio.music_track must be a music library filename"
                )
        audio["music_track"] = track
    if raw.get("music_volume") is not None:
        audio["music_volume"] = _int_value(
            raw["music_volume"],
            "audio.music_volume",
            MUSIC_VOLUME_MIN,
            MUSIC_VOLUME_MAX,
        )
    if raw.get("ducking_db") is not None:
        audio["ducking_db"] = _int_value(
            raw["ducking_db"],
            "audio.ducking_db",
            DUCKING_DB_MIN,
            DUCKING_DB_MAX,
        )
    if raw.get("noise_reduction") is not None:
        value = _int_value(
            raw["noise_reduction"],
            "audio.noise_reduction",
            CLEANUP_MIN,
            CLEANUP_MAX,
        )
        if value:
            audio["noise_reduction"] = value
    if raw.get("voice_clarity") is not None:
        value = _int_value(
            raw["voice_clarity"],
            "audio.voice_clarity",
            CLEANUP_MIN,
            CLEANUP_MAX,
        )
        if value:
            audio["voice_clarity"] = value
    if raw.get("loudness") is not None:
        loudness = raw["loudness"]
        if loudness not in LOUDNESS_CHOICES:
            raise ValueError(
                "audio.loudness must be one of: " + ", ".join(LOUDNESS_CHOICES)
            )
        audio["loudness"] = loudness
    for field in ("fade_in", "fade_out"):
        if raw.get(field) is not None:
            value = _fade_value(raw[field], f"audio.{field}")
            if value > 0:
                audio[field] = value
    return audio or None


def audio_overrides_from_plan(
    plan: Optional[dict[str, Any]],
) -> Optional[dict[str, Any]]:
    """The (validated) audio override block of a stored edit plan, or None."""
    if not isinstance(plan, dict):
        return None
    raw = plan.get("audio")
    if not isinstance(raw, dict):
        return None
    try:
        return normalize_audio_overrides(raw)
    except ValueError:
        # Plans are machine-written through normalize_audio_overrides; a
        # block that no longer validates (older build, hand-edited
        # sidecar) must not break the render — it is simply not an
        # override.
        return None


# --- Resolution: plan block -> EDL stamp --------------------------------------

def build_edl_audio_studio(
    overrides: Optional[dict[str, Any]],
) -> Optional[dict[str, Any]]:
    """Resolve stored overrides into the EDL ``audio_studio`` block.

    The stamp carries only what the audio finisher must do differently:
    music volume as a 0..1 fraction, ducking as a dB amount, cleanup
    strengths, a non-default loudness choice and the fades. ``loudness
    == "auto"`` is dropped — it IS today's -14 LUFS graph. Music track
    choice is not part of the stamp: the re-render seam applies it to
    the EDL's audio block through the music library helpers, exactly
    like ``music_override``. Returns None when nothing render-relevant
    remains, so a default render stays byte-identical to today.
    """
    if not overrides:
        return None
    studio: dict[str, Any] = {}
    if overrides.get("music_volume") is not None:
        studio["music_volume"] = round(overrides["music_volume"] / 100.0, 6)
    if overrides.get("ducking_db") is not None:
        studio["ducking_db"] = float(overrides["ducking_db"])
    if overrides.get("noise_reduction"):
        studio["noise_reduction"] = int(overrides["noise_reduction"])
    if overrides.get("voice_clarity"):
        studio["voice_clarity"] = int(overrides["voice_clarity"])
    loudness = overrides.get("loudness")
    if loudness in (LOUDNESS_QUIET, LOUDNESS_OFF):
        studio["loudness"] = loudness
    for field in ("fade_in", "fade_out"):
        if overrides.get(field):
            studio[field] = float(overrides[field])
    return studio or None


# --- Filter mapping (consumed by media.edl_render) -----------------------------

def noise_reduction_filter(strength: int) -> str:
    """afftdn for a 0..100 strength: up to 30 dB of FFT noise reduction.

    ``nf=-25`` is the speech-content noise-floor estimate; the default
    -50 floor leaves hiss largely untouched at moderate ``nr`` values.
    """
    nr = max(0.01, min(97.0, strength * 0.3))
    return f"afftdn=nr={nr:.2f}:nf=-25"


def voice_clarity_filters(strength: int) -> list[str]:
    """Presence clarity: an 80 Hz rumble cut plus a 3 kHz presence lift.

    The lift scales with the slider, up to +6 dB at full strength.
    """
    gain = max(0.0, min(12.0, strength * 0.06))
    return [
        "highpass=f=80",
        f"equalizer=f=3000:width_type=o:width=1.5:g={gain:.2f}",
    ]


def voice_cleanup_chain(studio: Optional[dict[str, Any]]) -> str:
    """The voice-chain fragment for a studio block, "" when no cleanup.

    Returned with leading commas so the renderer can splice it straight
    after the voice stream's format normalisation:
    ``{voice}{_AUDIO_NORMALIZE}{cleanup}[a_voice]``.
    """
    if not studio:
        return ""
    filters: list[str] = []
    clarity = studio.get("voice_clarity")
    if clarity:
        filters.extend(voice_clarity_filters(int(clarity)))
    noise = studio.get("noise_reduction")
    if noise:
        filters.append(noise_reduction_filter(int(noise)))
    return "".join(f",{entry}" for entry in filters)


def loudnorm_filter(studio: Optional[dict[str, Any]]) -> Optional[str]:
    """The finishing loudnorm for a studio block.

    No studio block (or loudness "auto") is today's -14 LUFS filter;
    "off" returns None — the graph then ends without loudnorm.
    """
    if not studio:
        return LOUDNORM_AUTO_FILTER
    loudness = studio.get("loudness")
    if loudness == LOUDNESS_OFF:
        return None
    if loudness == LOUDNESS_QUIET:
        return LOUDNORM_QUIET_FILTER
    return LOUDNORM_AUTO_FILTER


def fade_filters(studio: Optional[dict[str, Any]], duration: float) -> list[str]:
    """afade fragments for the finished mix, clamped to the clip length."""
    if not studio or duration <= 0:
        return []
    filters: list[str] = []
    fade_in = float(studio.get("fade_in") or 0.0)
    if fade_in > 0:
        filters.append(f"afade=t=in:st=0:d={min(fade_in, duration):.3f}")
    fade_out = float(studio.get("fade_out") or 0.0)
    if fade_out > 0:
        fade_out = min(fade_out, duration)
        start = max(0.0, duration - fade_out)
        filters.append(f"afade=t=out:st={start:.3f}:d={fade_out:.3f}")
    return filters


def studio_needs_finishing(studio: Optional[dict[str, Any]]) -> bool:
    """Whether a studio block forces an audio-finishing plan on its own.

    Music volume / ducking alone cannot conjure a plan (they only
    retune a bed that exists); cleanup, a loudness choice or fades
    change the voice/mix itself, so the finisher must run even when
    the EDL carries no music or SFX.
    """
    if not studio:
        return False
    return any(
        studio.get(field)
        for field in (
            "noise_reduction",
            "voice_clarity",
            "loudness",
            "fade_in",
            "fade_out",
        )
    )
