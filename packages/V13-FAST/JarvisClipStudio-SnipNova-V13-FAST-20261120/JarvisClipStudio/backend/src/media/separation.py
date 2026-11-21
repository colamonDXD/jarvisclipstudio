"""Voice/music separation (Demucs ``htdemucs``) — opt-in, off by default.

Stage 1 of the separation feature: when a task explicitly opts in, the
source's vocals stem is separated once per source and fed to
transcription instead of the raw mix. Music-heavy sources make Whisper
hallucinate words during music-only stretches; those phantom words
poison moment selection, captions and niche classification downstream —
everything in this app is downstream of the transcript.

The feature is gated twice, deliberately, mirroring the voiceover
feature's discipline:

- the deployment flag ``SEPARATION_ENABLED`` (default OFF — a build
  that never sets it has no separation surface at all, and no weights
  are ever downloaded), and
- a per-task opt-in stored on the task record (default off), honoured
  only while the deployment flag is on.

Failure behaviour is the contract: :func:`separate_vocals` returns the
cached or freshly separated vocals stem, or ``None`` on ANY failure
(disabled, no GPU/CPU path, missing model, download blocked, subprocess
timeout or crash, corrupt output). It never raises, so a task always
continues on the original audio — the same never-block pattern the
transcription audio extraction already uses.

Engine notes (see research_notes/wave4-separation/SCOPING.md): Demucs
``htdemucs`` runs in a subprocess with the VRAM-safe profile — two-stem
vocals mode, segment 7, ``PYTORCH_NO_CUDA_MEMORY_CACHING=1`` — so a
CUDA out-of-memory kills the subprocess, never the worker, and peak
VRAM stays bounded (segmented inference; ~1.5–2 GB total on the
reference card) regardless of source length. Stems are cached beside
the source as ``<stem>.vocals.flac`` / ``<stem>.accompaniment.flac``
with a ``<stem>.separation.json`` sidecar keyed by the source's
SHA-256, so re-renders and re-drops of the same file reuse them.

Licence disclosure: the Demucs code is MIT, but the licence of the
``htdemucs`` weights is explicitly unclear (the author's README states
the training data includes research-only material). The weights are
therefore NEVER bundled with or redistributed by this app: they are
downloaded once, at first enabled use, by the user's own machine, from
the model author's own Hugging Face repository — the same posture as
the Kokoro voiceover model — into the shared models cache under the
app's temp tree. The 6-stem variant (author-flagged as likely
non-commercial) is never used.
"""

from __future__ import annotations

import hashlib
import importlib.metadata
import json
import logging
import os
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from ..config import get_config
from .ffmpeg import ffprobe_duration, run_ffmpeg_command, select_audio_stream_index
from .vram import log_vram

logger = logging.getLogger(__name__)

MODEL_NAME = "htdemucs"
MODEL_DOWNLOAD_NOTE = (
    "Separation: downloading the Demucs htdemucs model (~84 MB) into "
    "the models cache — this happens once, on first separation use."
)
# Duration-scaled subprocess timeout (SCOPING §4): separation may take
# multiples of the source duration on CPU, so the ceiling scales with
# it; the floor keeps tiny clips from timing out on model load alone.
TIMEOUT_PER_SECOND = 10
TIMEOUT_BASE_SECONDS = 120
TIMEOUT_FLOOR_SECONDS = 300
TIMEOUT_UNKNOWN_DURATION_SECONDS = 1800


def separation_enabled() -> bool:
    """SEPARATION_ENABLED=true (or 1/on/yes) turns the feature on.

    Default OFF: unlike the other lane kill switches this is an
    opt-IN — the model download and the extra dependency surface only
    exist where the deployment asked for them.
    """
    return os.environ.get("SEPARATION_ENABLED", "").strip().lower() in {
        "1",
        "true",
        "on",
        "yes",
    }


def validate_separation_opt_in(value: Any) -> bool:
    """Validate a create payload's ``separation_opt_in`` flag.

    Returns False when the flag is absent. Raises ValueError (the API
    layer surfaces it as a 422) for a non-boolean value, and for an
    opt-in sent while the deployment flag is off — so a disabled
    feature fails loudly at the door instead of silently storing an
    opt-in that would spring to life if the flag is ever flipped.
    """
    if value is None:
        return False
    if not isinstance(value, bool):
        raise ValueError("separation_opt_in must be a boolean")
    if value and not separation_enabled():
        raise ValueError(
            "Voice/music separation is disabled on this deployment "
            "(set SEPARATION_ENABLED=true to enable it)"
        )
    return value


def separation_models_dir() -> Path:
    """The Hugging Face cache the Demucs weights download into.

    Shared with the voiceover feature's models area (the uploads
    volume in Docker) so every runtime-downloaded model lives in one
    place under the app's temp tree — never on the system drive.
    """
    override = os.getenv("SEPARATION_MODELS_DIR")
    folder = (
        Path(override)
        if override
        else Path(get_config().temp_dir) / "models" / "huggingface"
    )
    folder.mkdir(parents=True, exist_ok=True)
    return folder


# --- Stem artifacts -------------------------------------------------------------


def vocals_stem_path(source_path: Path) -> Path:
    """Where the cached vocals stem for ``source_path`` lives."""
    return source_path.with_name(f"{source_path.stem}.vocals.flac")


def accompaniment_stem_path(source_path: Path) -> Path:
    """Where the cached accompaniment stem for ``source_path`` lives."""
    return source_path.with_name(f"{source_path.stem}.accompaniment.flac")


def separation_sidecar_path(source_path: Path) -> Path:
    """Where the separation provenance sidecar for the source lives."""
    return source_path.with_name(f"{source_path.stem}.separation.json")


def stem_companion_names(source_name: str) -> list[str]:
    """File names of every cached artifact derived from a source name.

    The naming rule lives here and nowhere else: media storage's
    deletion path uses these names so a removed source takes its
    stems with it (see :func:`stem_companion_paths`).
    """
    stem = Path(source_name).stem
    return [
        f"{stem}.vocals.flac",
        f"{stem}.accompaniment.flac",
        f"{stem}.separation.json",
    ]


def stem_companion_paths(source_path: Path) -> list[Path]:
    """Paths of every cached artifact separation derived from a source.

    Deletion paths use this so a removed source never leaves its
    stems behind: the FLAC pair is ~640 MB per hour of source, and
    before this helper existed nothing ever deleted them — task
    deletion removed the source and its transcript sidecars only, so
    every deleted separation task leaked its stems permanently.
    """
    source_path = Path(source_path)
    return [source_path.with_name(name) for name in stem_companion_names(source_path.name)]


def _source_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _weights_version() -> str:
    try:
        return importlib.metadata.version("demucs")
    except importlib.metadata.PackageNotFoundError:
        return "unknown"


def _cached_vocals_stem(source_path: Path, source_hash: str) -> Optional[Path]:
    """The cached vocals stem when its sidecar matches this source."""
    stem = vocals_stem_path(source_path)
    sidecar = separation_sidecar_path(source_path)
    if not stem.exists() or stem.stat().st_size == 0 or not sidecar.exists():
        return None
    try:
        meta = json.loads(sidecar.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if meta.get("model") != MODEL_NAME or meta.get("source_sha256") != source_hash:
        return None
    return stem


# --- The separation run ---------------------------------------------------------


def _cuda_available() -> bool:
    try:
        import torch

        return bool(torch.cuda.is_available())
    except Exception:
        return False


# Demucs device choice (Wave-4 VRAM scoping, Option B item 3): picking
# CUDA on presence alone turns a starved card into a doomed subprocess
# that burns minutes before failing. The htdemucs working set is in
# the ~1.5 GB class (measured in the separation scoping); requiring
# ~2.2 GB free leaves headroom for the driver context on top.
DEMUCS_CUDA_MIN_FREE_MB_DEFAULT = 2250


def _cuda_free_vram_bytes() -> Optional[int]:
    """Free VRAM on the first CUDA device, or None when unprobeable.

    None means UNKNOWN (no torch, no CUDA device, probe failed) — the
    caller keeps the historical presence-only behaviour rather than
    routing to CPU on a healthy machine because a probe hiccuped.
    """
    try:
        import torch

        if not torch.cuda.is_available():
            return None
        free, _total = torch.cuda.mem_get_info()
        return int(free)
    except Exception:
        return None


def _demucs_cuda_min_free_bytes() -> int:
    """The free-VRAM floor for choosing CUDA for a separation run.

    ``SEPARATION_CUDA_MIN_FREE_MB`` (megabytes) overrides; an
    unparsable or non-positive value falls back to the default.
    """
    raw = os.environ.get("SEPARATION_CUDA_MIN_FREE_MB", "")
    try:
        mb = int(raw) if raw.strip() else DEMUCS_CUDA_MIN_FREE_MB_DEFAULT
    except ValueError:
        mb = DEMUCS_CUDA_MIN_FREE_MB_DEFAULT
    if mb <= 0:
        mb = DEMUCS_CUDA_MIN_FREE_MB_DEFAULT
    return mb * 1024 * 1024


def _separation_device() -> str:
    """Pick the Demucs device: CUDA only when it exists AND has room."""
    if not _cuda_available():
        return "cpu"
    free = _cuda_free_vram_bytes()
    min_free = _demucs_cuda_min_free_bytes()
    if free is not None and free < min_free:
        logger.warning(
            "Separation: only %.0f MB VRAM free, below the %d MB floor; "
            "using CPU instead of a likely-doomed CUDA attempt",
            free / (1024 * 1024),
            min_free // (1024 * 1024),
        )
        return "cpu"
    return "cuda"


def _separation_timeout(source_path: Path) -> int:
    duration = ffprobe_duration(source_path)
    if not duration or duration <= 0:
        return TIMEOUT_UNKNOWN_DURATION_SECONDS
    return max(
        TIMEOUT_FLOOR_SECONDS,
        int(duration * TIMEOUT_PER_SECOND) + TIMEOUT_BASE_SECONDS,
    )


def _extract_audio_for_separation(source_path: Path, target: Path) -> bool:
    """Extract the source's audio track as 44.1 kHz stereo FLAC.

    Demucs is an audio model: feeding it the extracted track (the same
    stream selection transcription uses) keeps the separator away from
    container quirks and guarantees it hears exactly what the renderer
    and transcriber hear.
    """
    audio_stream = select_audio_stream_index(source_path)
    command = [
        "ffmpeg",
        "-y",
        "-i",
        str(source_path),
        *(["-map", f"0:a:{audio_stream}"] if audio_stream is not None else []),
        "-vn",
        "-ac",
        "2",
        "-ar",
        "44100",
        str(target),
    ]
    try:
        result = run_ffmpeg_command(command, timeout=900)
    except FileNotFoundError:
        logger.warning("ffmpeg is not available; cannot prepare separation input")
        return False
    return result.returncode == 0 and target.exists() and target.stat().st_size > 0


def _demucs_command(input_audio: Path, out_dir: Path, device: str) -> list[str]:
    """The VRAM-safe Demucs invocation (SCOPING §3).

    ``--two-stems vocals`` yields vocals + accompaniment directly;
    segment 7 bounds peak VRAM independently of track length.
    """
    return [
        sys.executable,
        "-m",
        "demucs",
        "-n",
        MODEL_NAME,
        "--two-stems",
        "vocals",
        "--segment",
        "7",
        "-d",
        device,
        "--flac",
        "-o",
        str(out_dir),
        str(input_audio),
    ]


def _run_demucs(input_audio: Path, out_dir: Path, device: str, timeout: int) -> bool:
    env = dict(os.environ)
    # A CUDA OOM must not leave a caching allocator holding VRAM, and
    # the weights belong in the app's models cache (downloaded on first
    # use from the model author's own repo — never bundled).
    env["PYTORCH_NO_CUDA_MEMORY_CACHING"] = "1"
    env["HF_HOME"] = str(separation_models_dir())
    try:
        result = subprocess.run(
            _demucs_command(input_audio, out_dir, device),
            capture_output=True,
            text=True,
            timeout=timeout,
            env=env,
        )
    except subprocess.TimeoutExpired:
        logger.warning(
            "Separation timed out after %ss for %s; using original audio",
            timeout,
            input_audio,
        )
        return False
    except Exception as exc:  # subprocess failed to even start, etc.
        logger.warning("Separation subprocess failed for %s: %s", input_audio, exc)
        return False
    if result.returncode != 0:
        tail = (result.stderr or "").strip().splitlines()
        logger.warning(
            "Separation failed for %s (exit %s): %s",
            input_audio,
            result.returncode,
            tail[-1] if tail else "no error output",
        )
        return False
    return True


def _place_stem(raw: Path, target: Path) -> bool:
    """Move a Demucs output stem onto its sidecar path (FLAC)."""
    if raw.suffix == ".flac":
        shutil.move(str(raw), str(target))
        return target.exists() and target.stat().st_size > 0
    try:
        result = run_ffmpeg_command(
            ["ffmpeg", "-y", "-i", str(raw), str(target)], timeout=900
        )
    except FileNotFoundError:
        return False
    return result.returncode == 0 and target.exists() and target.stat().st_size > 0


def separate_vocals(source_path: Path) -> Optional[Path]:
    """Separate ``source_path`` and return its vocals stem, or None.

    Returns the cached stem when the sidecar proves it came from this
    exact source. Returns None — with a warning logged — when the
    feature is disabled or anything at all goes wrong. This function
    must never raise: transcription falls back to the original audio
    whenever it gets None.
    """
    try:
        if not separation_enabled():
            return None
        source_path = Path(source_path)
        if not source_path.exists() or source_path.stat().st_size == 0:
            logger.warning("Separation skipped: source %s is missing", source_path)
            return None

        source_hash = _source_sha256(source_path)
        cached = _cached_vocals_stem(source_path, source_hash)
        if cached is not None:
            logger.info("Separation cache hit: %s", cached)
            log_vram("separation_cache_hit")
            return cached

        work_dir = source_path.parent / f".separation-{source_hash[:12]}"
        shutil.rmtree(work_dir, ignore_errors=True)
        work_dir.mkdir(parents=True, exist_ok=True)
        try:
            input_audio = work_dir / "input.flac"
            if not _extract_audio_for_separation(source_path, input_audio):
                logger.warning(
                    "Separation input extraction failed for %s; using original audio",
                    source_path,
                )
                return None

            log_vram("separation_start")
            device = _separation_device()
            logger.info(
                "Separating vocals for %s (device=%s, model=%s). %s",
                source_path,
                device,
                MODEL_NAME,
                MODEL_DOWNLOAD_NOTE,
            )
            out_dir = work_dir / "out"
            timeout = _separation_timeout(source_path)
            if not _run_demucs(input_audio, out_dir, device, timeout):
                if device != "cuda":
                    return None
                # One CPU retry before giving up: a CUDA failure here
                # is most often VRAM pressure from a co-resident model,
                # and the never-fail contract prefers a slower success
                # over silently keeping the original audio.
                logger.warning(
                    "Separation CUDA attempt failed for %s; retrying once on CPU",
                    source_path,
                )
                if not _run_demucs(input_audio, out_dir, "cpu", timeout):
                    return None

            produced = out_dir / MODEL_NAME / input_audio.stem
            vocals_raw = produced / "vocals.flac"
            if not vocals_raw.exists():
                vocals_raw = produced / "vocals.wav"
            accompaniment_raw = produced / "no_vocals.flac"
            if not accompaniment_raw.exists():
                accompaniment_raw = produced / "no_vocals.wav"
            if not vocals_raw.exists() or not accompaniment_raw.exists():
                logger.warning(
                    "Separation produced no stems for %s; using original audio",
                    source_path,
                )
                return None
            if not _place_stem(vocals_raw, vocals_stem_path(source_path)):
                logger.warning(
                    "Separation output for %s is unusable; using original audio",
                    source_path,
                )
                return None
            # A failed accompaniment placement never blocks the vocals
            # stem — Stage 1 only consumes the vocals side.
            _place_stem(accompaniment_raw, accompaniment_stem_path(source_path))

            # The sidecar is written LAST: its presence with a matching
            # hash is what makes the cache trustworthy, so a crashed or
            # partial run can never leave a false cache entry behind.
            separation_sidecar_path(source_path).write_text(
                json.dumps(
                    {
                        "model": MODEL_NAME,
                        "weights_version": _weights_version(),
                        "source_sha256": source_hash,
                        "created_at": datetime.now(timezone.utc).isoformat(),
                    },
                    indent=2,
                ),
                encoding="utf-8",
            )
            logger.info(
                "Separation complete for %s -> %s",
                source_path,
                vocals_stem_path(source_path),
            )
            return vocals_stem_path(source_path)
        finally:
            log_vram("separation_end")
            shutil.rmtree(work_dir, ignore_errors=True)
    except Exception as exc:  # the never-fail contract
        logger.warning(
            "Separation failed unexpectedly for %s: %s; using original audio",
            source_path,
            exc,
        )
        return None


def resolve_render_vocals_stem(
    separation_opt_in: bool, video_path: Optional[Path]
) -> Optional[Path]:
    """Resolve the vocals stem a render of ``video_path`` should draw from.

    The single shared resolution for every render path (main pipeline,
    re-render seam, hook previews): an opted-in task's clips draw their
    audio from the source's vocals stem, so the source's own background
    music never reaches the export. The stem is resolved once per render
    call — transcription already separated this source, so this is
    normally a cache hit — and the caller carries the path down.

    Returns None — never raises — on ANY failure (flag off, separator
    unavailable, stem missing), so the caller renders with the original
    audio exactly as before. Callers pass the kwarg only when this
    returns a path, so a non-opted-in task's render call is
    byte-identical to the historical one.
    """
    if not separation_opt_in or video_path is None:
        return None
    try:
        if separation_enabled():
            return separate_vocals(Path(video_path))
    except Exception:
        logger.warning(
            "Vocals-stem resolution for render failed; "
            "rendering with the original audio",
            exc_info=True,
        )
    return None
