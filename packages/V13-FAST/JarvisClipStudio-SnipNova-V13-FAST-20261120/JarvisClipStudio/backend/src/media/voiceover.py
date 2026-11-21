"""Local AI voiceover (Kokoro-82M) — opt-in, off by default.

The Audio Studio can narrate a clip with a fully local text-to-speech
model: Kokoro-82M (Apache-2.0 weights, ``kokoro`` pip package), running
on the worker's own hardware — no API, no account, no per-use cost.
The feature is gated twice, deliberately:

- the deployment flag ``VOICEOVER_ENABLED`` (default OFF — a build
  that never sets it has no voiceover surface at all), and
- a per-clip spec the user explicitly saves in the Audio Studio
  (script + voice + mode) on the clip's edit plan.

When either gate is closed the render path is untouched: the finisher
only ever sees ``edl["voiceover"]`` when a spec was stored and the
synthesis succeeded, and with no such block its graph is
byte-identical to the pre-voiceover one.

Synthesis results are cached by content hash under the shared temp
tree (``<TEMP_DIR>/voiceovers``), so re-rendering a clip with the same
script and voice never re-synthesizes. The model itself downloads once
on first use into ``<TEMP_DIR>/models/huggingface`` (the uploads
volume in Docker) — about 315 MB — and is logged when it happens.
"""

from __future__ import annotations

import hashlib
import logging
import os
import threading
from pathlib import Path
from typing import Any, Optional

from ..config import get_config

logger = logging.getLogger(__name__)

# The voices offered in the panel: Kokoro's bundled voice packs (each
# ~0.5 MB, downloaded with the model repo on first use of that voice).
# lang_code follows the voice's first letter, as Kokoro's pipeline
# requires: "a" = American English, "b" = British English.
VOICEOVER_VOICES: dict[str, dict[str, str]] = {
    "af_heart": {"label": "Heart — female (US)", "lang_code": "a"},
    "am_michael": {"label": "Michael — male (US)", "lang_code": "a"},
    "bf_emma": {"label": "Emma — female (UK)", "lang_code": "b"},
}
VOICEOVER_MODES = ("hook", "replace")
MAX_SCRIPT_CHARS = 600
MODEL_DOWNLOAD_NOTE = (
    "Voiceover: downloading the Kokoro-82M model (~315 MB) into the "
    "models cache — this happens once, on first voiceover use."
)

_pipelines: dict[str, Any] = {}
_pipeline_lock = threading.Lock()


def voiceover_enabled() -> bool:
    """VOICEOVER_ENABLED=true (or 1/on/yes) turns the feature on.

    Default OFF: unlike the other lane kill switches this is an
    opt-IN — the model download and the extra dependency surface only
    exist where the deployment asked for them.
    """
    return os.environ.get("VOICEOVER_ENABLED", "").strip().lower() in {
        "1",
        "true",
        "on",
        "yes",
    }


def voiceover_cache_dir() -> Path:
    """Where synthesized narration WAVs are cached (created on demand)."""
    override = os.getenv("VOICEOVER_DIR")
    folder = (
        Path(override)
        if override
        else Path(get_config().temp_dir) / "voiceovers"
    )
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def voiceover_models_dir() -> Path:
    """The Hugging Face cache the Kokoro weights download into."""
    override = os.getenv("VOICEOVER_MODELS_DIR")
    folder = (
        Path(override)
        if override
        else Path(get_config().temp_dir) / "models" / "huggingface"
    )
    folder.mkdir(parents=True, exist_ok=True)
    return folder


# --- Spec validation ------------------------------------------------------------

def normalize_voiceover_request(raw: Any) -> Optional[dict[str, Any]]:
    """Validate an edit payload's ``voiceover`` block.

    Returns None when the block is absent, ``{"clear": True}`` when the
    user removed the narration, or the validated spec
    ``{"script", "voice", "mode"}``. Raises ValueError (the API layer
    surfaces it as a 422) for anything malformed — including a spec
    sent while the deployment flag is off, so a disabled feature fails
    loudly at the door instead of silently storing a spec that would
    spring to life if the flag is ever flipped.
    """
    if raw is None:
        return None
    if not isinstance(raw, dict):
        raise ValueError("voiceover must be an object")
    if raw.get("clear") is True:
        return {"clear": True}
    if not voiceover_enabled():
        raise ValueError(
            "Voiceover is disabled on this deployment "
            "(set VOICEOVER_ENABLED=true to enable it)"
        )
    unknown = sorted(set(raw) - {"script", "voice", "mode", "clear"})
    if unknown:
        raise ValueError(f"unknown voiceover fields: {', '.join(unknown)}")
    script = raw.get("script")
    if not isinstance(script, str) or not script.strip():
        raise ValueError("voiceover.script must be a non-empty string")
    script = script.strip()
    if len(script) > MAX_SCRIPT_CHARS:
        raise ValueError(
            f"voiceover.script must be at most {MAX_SCRIPT_CHARS} characters"
        )
    voice = raw.get("voice") or "af_heart"
    if voice not in VOICEOVER_VOICES:
        raise ValueError(
            "voiceover.voice must be one of: "
            + ", ".join(sorted(VOICEOVER_VOICES))
        )
    mode = raw.get("mode") or "hook"
    if mode not in VOICEOVER_MODES:
        raise ValueError(
            "voiceover.mode must be one of: " + ", ".join(VOICEOVER_MODES)
        )
    return {"script": script, "voice": voice, "mode": mode}


def voiceover_spec_from_plan(
    plan: Optional[dict[str, Any]],
) -> Optional[dict[str, Any]]:
    """The stored voiceover spec of an edit plan, or None.

    Stored specs carry the synthesized file's cache name; a spec whose
    file has been evicted simply fails its existence check at render
    time and the clip renders without narration.
    """
    if not isinstance(plan, dict):
        return None
    raw = plan.get("voiceover")
    if not isinstance(raw, dict):
        return None
    script = raw.get("script")
    voice = raw.get("voice")
    mode = raw.get("mode")
    filename = raw.get("file")
    if (
        not isinstance(script, str)
        or not script.strip()
        or voice not in VOICEOVER_VOICES
        or mode not in VOICEOVER_MODES
        or not isinstance(filename, str)
        or not filename
        or filename != Path(filename).name
    ):
        return None
    return {
        "script": script,
        "voice": voice,
        "mode": mode,
        "file": filename,
    }


def cache_filename(script: str, voice: str) -> str:
    """The content-addressed cache name for one script + voice pair."""
    digest = hashlib.sha256(f"{voice}\n{script}".encode("utf-8")).hexdigest()
    return f"vo_{digest[:24]}.wav"


# --- Synthesis ------------------------------------------------------------------

def _pipeline(lang_code: str) -> Any:
    """The process-wide Kokoro pipeline for a language, built lazily."""
    with _pipeline_lock:
        pipeline = _pipelines.get(lang_code)
        if pipeline is None:
            models_dir = voiceover_models_dir()
            if not any(models_dir.rglob("kokoro-v1_0.pth")):
                logger.info(MODEL_DOWNLOAD_NOTE)
            # Kokoro/Hugging Face read HF_HOME at import time; point it
            # at the shared models cache unless the deployment set one.
            os.environ.setdefault("HF_HOME", str(models_dir))
            from kokoro import KPipeline

            pipeline = KPipeline(lang_code=lang_code)
            _pipelines[lang_code] = pipeline
        return pipeline


def synthesize_voiceover(script: str, voice: str) -> Path:
    """Synthesize (or fetch from cache) the narration WAV for a spec.

    Returns the cached file path. Raises whatever the TTS stack raises
    — the caller (the re-render seam) turns a failure into "render
    without narration" rather than losing the clip.
    """
    target = voiceover_cache_dir() / cache_filename(script, voice)
    if target.is_file():
        return target
    lang_code = VOICEOVER_VOICES[voice]["lang_code"]
    pipeline = _pipeline(lang_code)

    import numpy as np
    import soundfile as sf

    chunks = [
        audio
        for _graphemes, _phonemes, audio in pipeline(script, voice=voice)
    ]
    if not chunks:
        raise ValueError("Voiceover synthesis produced no audio")
    audio = np.concatenate(chunks)
    temp = target.with_suffix(".tmp.wav")
    sf.write(str(temp), audio, 24000)
    temp.replace(target)
    logger.info(
        "Voiceover synthesized: voice=%s chars=%d duration=%.2fs file=%s",
        voice,
        len(script),
        len(audio) / 24000.0,
        target.name,
    )
    return target


def voiceover_edl_block(spec: dict[str, Any]) -> Optional[dict[str, Any]]:
    """Resolve a stored spec into the EDL ``voiceover`` block, or None.

    Synthesis happens here (in the worker, on the re-render path): the
    cached file is produced if needed, then its absolute path is what
    the audio finisher mixes. Any synthesis failure returns None — the
    render proceeds without narration.
    """
    try:
        path = synthesize_voiceover(spec["script"], spec["voice"])
    except Exception:
        logger.warning(
            "Voiceover synthesis failed; rendering without narration",
            exc_info=True,
        )
        return None
    return {"path": str(path), "mode": spec["mode"]}
