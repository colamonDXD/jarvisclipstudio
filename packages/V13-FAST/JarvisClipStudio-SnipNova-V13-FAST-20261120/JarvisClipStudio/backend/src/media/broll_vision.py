"""Vision veto for stock B-roll (SigLIP) — opt-in, off by default.

The Pixabay relevance gate (``media/pixabay_broll.py``) judges a stock
hit by its *tags*: sensitive themes, the word-sense judge and a shared
descriptive word. Tags can lie — a hit can carry the right words while
its frames show something else entirely (a scenic railway-bridge
landscape for a "train station" cue, an empty desert highway for a
"car" cue). This module is the second, visual opinion: before a hit is
accepted, a few of its frames are scored against the cue's intended
subject with a SigLIP image/text model, and a hit whose frames all
score below the relevance threshold is vetoed so the resolver falls
through to the next hit — or skips the cue when no hit passes, exactly
as the tag gate already does. It only ever *removes* footage; it can
never add any. Smart B-Roll v2, Slice 4: for concept cues the subject
is the planner's concept sentence (not the bare noun or the search
string), and where the concept names a wrong sense to avoid, the
frames are scored against that too — a hit that looks MORE like the
wrong sense than the right one is vetoed
(``should_veto_contrastive``), even if it clears the threshold.

Gating mirrors the separation feature's discipline:

- the deployment flag ``BROLL_VISION_VETO_ENABLED`` (default OFF — a
  build that never sets it has no veto surface at all, and no weights
  are ever downloaded), and
- Auto template mode only: the resolver never builds a session for a
  Plain — just cuts EDL (Plain strips B-roll entirely, so there is
  nothing to veto; the guard is belt-and-braces).

Failure behaviour is the contract: every failure — flag off, model
missing, download blocked, no network, CUDA out of room, a slow check
that exhausts the per-placement time budget — returns "no veto", so
the resolver behaves exactly as it did before this module existed.
The veto can never fail or stall a render.

Engine notes: ``google/siglip-base-patch16-224`` (Apache-2.0, verified
against the model card metadata on Hugging Face, 2026-11-11; ~200M
parameters). The weights are NEVER bundled with or redistributed by
this app: they are downloaded once, at first enabled use, by the
user's own machine into the shared models cache the separation
feature already uses (``media/separation.py``'s models dir), under
``HF_HOME``. The model loads lazily for one resolve pass and is
unloaded when the pass ends, so on a 6 GB card it never coexists with
a render; on CUDA it is only chosen when the free-VRAM probe leaves
real headroom (the same probe pattern as separation's device gate),
and a card that is busy skips the veto for that pass rather than
piling a second tenant onto it. Image preprocessing deliberately
avoids Pillow (not a dependency of this app): frames are decoded and
resized to the model's 224x224 input by FFmpeg as raw RGB, which is
also how the rest of this codebase touches pixels.
"""

from __future__ import annotations

import logging
import os
import subprocess
from pathlib import Path
from typing import Any, Optional, Sequence

logger = logging.getLogger(__name__)

MODEL_ID = "google/siglip-base-patch16-224"
MODEL_DOWNLOAD_NOTE = (
    "B-roll vision veto: downloading the SigLIP model (~810 MB) into "
    "the models cache — this happens once, on first veto use."
)
# Cosine-similarity threshold on SigLIP embeddings, calibrated on the
# lane's planted fixture set (11 pairs through this exact frame path;
# full table in SIGLIP_VETO_REPORT.md): every planted mismatch scored
# at or below 0.0442 and every true pair at or above 0.0517 with the
# production subject text, so the default sits mid-gap. Applies to
# NOUN / search-string subjects only (cues without a concept): those
# short phrasings score higher across the board.
DEFAULT_THRESHOLD = 0.048
# Concept-sentence threshold (Smart B-Roll v2, Slice 4). Concept
# sentences score LOWER than bare nouns with SigLIP base — on the
# moving-stock calibration (44 videos, 11 trap words, 220 scored
# pairs; full table in BROLL_SLICE4_REPORT.md) true matches ran as low
# as 0.0131 while wrong-sense mismatches reached 0.0481, so the old
# 0.048 threshold would have false-vetoed 9/44 true matches. 0.006 is
# the measured concept mid-gap (match-min -0.0357 on a mislabeled
# video, mismatch-max 0.0481): at this value the threshold leg vetoes
# 1/44 true matches and the contrastive leg vetoes 0/44 wrong-sense
# inserts. Env-overridable per deployment
# (BROLL_VISION_VETO_CONCEPT_THRESHOLD).
DEFAULT_CONCEPT_THRESHOLD = 0.006
# Hard wall-clock budget for the vision checks of ONE placement (one
# EDL cue): when it runs out, the remaining candidates of that cue are
# accepted on the tag gate alone. Keeps a slow model or a slow network
# from ever stalling a render.
DEFAULT_BUDGET_SECONDS = 20.0
# SigLIP base working set is ~0.9 GB (fp32 weights + one small frame
# batch); requiring 1.5 GB free leaves the driver-context headroom the
# separation gate also insists on.
CUDA_MIN_FREE_MB_DEFAULT = 1500
# Representative frames sampled per candidate clip.
FRAME_FRACTIONS = (0.25, 0.5, 0.75)
IMAGE_SIZE = 224

# In-process score cache: one worker resolves many clips whose cues
# repeat, and a (asset, subject) pair never needs scoring twice.
# Asset ids are Pixabay ints but Coverr strings ("AbC123"), so the key
# is the string form — int(asset_id) used to raise ValueError on
# Coverr hits and break the veto check (found by the Slice 4 Coverr
# test; the Pixabay path never tripped it because its ids are ints).
_score_cache: dict[tuple[str, str], float] = {}


def vision_veto_enabled() -> bool:
    """BROLL_VISION_VETO_ENABLED=true (or 1/on/yes) turns the veto on.

    Default OFF: an opt-IN, like separation — the model download and
    the extra inference surface only exist where the deployment asked.
    """
    return os.environ.get("BROLL_VISION_VETO_ENABLED", "").strip().lower() in {
        "1",
        "true",
        "on",
        "yes",
    }


def vision_veto_threshold(concept: bool = False) -> float:
    """The cosine threshold below which a candidate is vetoed.

    ``BROLL_VISION_VETO_THRESHOLD`` overrides the noun-subject
    default; ``BROLL_VISION_VETO_CONCEPT_THRESHOLD`` overrides the
    concept-sentence default. An unparsable or out-of-range value
    falls back to the calibrated default for that subject form.
    The two forms score on different scales with SigLIP base, so
    one threshold cannot serve both — see DEFAULT_THRESHOLD and
    DEFAULT_CONCEPT_THRESHOLD.
    """
    if concept:
        raw = os.environ.get("BROLL_VISION_VETO_CONCEPT_THRESHOLD", "")
        default = DEFAULT_CONCEPT_THRESHOLD
    else:
        raw = os.environ.get("BROLL_VISION_VETO_THRESHOLD", "")
        default = DEFAULT_THRESHOLD
    try:
        value = float(raw) if raw.strip() else default
    except ValueError:
        value = default
    if not -1.0 < value < 1.0:
        value = default
    return value


def vision_veto_budget_seconds() -> float:
    """Per-placement wall-clock budget for vision checks, in seconds."""
    raw = os.environ.get("BROLL_VISION_VETO_BUDGET_SECONDS", "")
    try:
        value = float(raw) if raw.strip() else DEFAULT_BUDGET_SECONDS
    except ValueError:
        value = DEFAULT_BUDGET_SECONDS
    return value if value > 0 else DEFAULT_BUDGET_SECONDS


def should_veto(score: Optional[float], threshold: float) -> bool:
    """The pure decision: only a real score below the threshold vetoes.

    None (unjudged — any failure anywhere) never vetoes: fail-open is
    the contract, in this one place so it cannot drift.
    """
    return score is not None and score < threshold


def should_veto_contrastive(
    concept_score: Optional[float],
    avoid_score: Optional[float],
    threshold: float,
) -> bool:
    """The concept + contrastive-avoid decision (Smart B-Roll v2, Slice 4).

    Veto when the frames fail the concept sentence (``concept_score``
    below threshold) OR when they look MORE like the wrong sense than
    the right one (``avoid_score`` above ``concept_score``) — the
    contrastive check of SMART_BROLL_V2_DESIGN.md §4.4. Either score
    being None means that side is unjudged, and unjudged never vetoes:
    a missing avoid score degrades to the plain threshold check, and
    a missing concept score is fail-open, exactly like ``should_veto``.
    """
    if concept_score is None:
        return False
    if concept_score < threshold:
        return True
    return avoid_score is not None and avoid_score > concept_score


def cached_score(asset_id: Any, subject: str) -> Optional[float]:
    return _score_cache.get((str(asset_id), subject.strip().lower()))


def store_score(asset_id: Any, subject: str, score: float) -> None:
    _score_cache[(str(asset_id), subject.strip().lower())] = score


# --- Device choice (mirrors media/separation.py) ---------------------------


def _cuda_available() -> bool:
    try:
        import torch

        return bool(torch.cuda.is_available())
    except Exception:
        return False


def _cuda_free_vram_bytes() -> Optional[int]:
    """Free VRAM on the first CUDA device, or None when unprobeable.

    None means UNKNOWN — the caller keeps presence-only behaviour
    rather than skipping on a healthy machine because a probe hiccuped
    (the separation gate's rule, kept identical here).
    """
    try:
        import torch

        if not torch.cuda.is_available():
            return None
        free, _total = torch.cuda.mem_get_info()
        return int(free)
    except Exception:
        return None


def _cuda_min_free_bytes() -> int:
    raw = os.environ.get("BROLL_VISION_VETO_CUDA_MIN_FREE_MB", "")
    try:
        mb = int(raw) if raw.strip() else CUDA_MIN_FREE_MB_DEFAULT
    except ValueError:
        mb = CUDA_MIN_FREE_MB_DEFAULT
    if mb <= 0:
        mb = CUDA_MIN_FREE_MB_DEFAULT
    return mb * 1024 * 1024


def _vision_device() -> Optional[str]:
    """Pick the inference device, or None to skip the veto this pass.

    CUDA only when it exists AND has room; a present-but-busy card
    skips the veto (logged by the caller) instead of squeezing in next
    to the speech model. No CUDA at all means CPU, like separation.
    """
    if not _cuda_available():
        return "cpu"
    free = _cuda_free_vram_bytes()
    min_free = _cuda_min_free_bytes()
    if free is not None and free < min_free:
        logger.warning(
            "B-roll vision veto: only %.0f MB VRAM free, below the %d MB "
            "floor; skipping the veto for this resolve pass",
            free / (1024 * 1024),
            min_free // (1024 * 1024),
        )
        return None
    return "cuda"


# --- Frame decoding (FFmpeg -> raw RGB, no Pillow anywhere) ----------------

_SCALE_VF = f"scale={IMAGE_SIZE}:{IMAGE_SIZE}:flags=bicubic,format=rgb24"


def frame_rgb_from_image_bytes(data: bytes) -> Optional[Any]:
    """Decode one image (JPEG/PNG/WebP bytes) to a 224x224 RGB array.

    FFmpeg does the decode and the resize, so thumbnails of any format
    the stock API serves work without an image library. Returns a
    numpy uint8 array of shape (224, 224, 3), or None on any failure.
    """
    if not data:
        return None
    try:
        import numpy as np

        result = subprocess.run(
            [
                "ffmpeg", "-v", "error", "-i", "pipe:0",
                "-vf", _SCALE_VF, "-frames:v", "1",
                "-f", "rawvideo", "pipe:1",
            ],
            input=data,
            capture_output=True,
            timeout=30,
        )
        return _raw_to_array(result, np)
    except Exception as exc:
        logger.debug("Vision veto: thumbnail decode failed: %s", exc)
        return None


def frame_rgb_from_video(video_path: Path, at_seconds: float) -> Optional[Any]:
    """Extract one frame of ``video_path`` as a 224x224 RGB array."""
    try:
        import numpy as np

        result = subprocess.run(
            [
                "ffmpeg", "-v", "error", "-ss", f"{max(0.0, at_seconds):.3f}",
                "-i", str(video_path),
                "-vf", _SCALE_VF, "-frames:v", "1",
                "-f", "rawvideo", "pipe:1",
            ],
            capture_output=True,
            timeout=30,
        )
        return _raw_to_array(result, np)
    except Exception as exc:
        logger.debug("Vision veto: frame extract failed: %s", exc)
        return None


def _raw_to_array(result: "subprocess.CompletedProcess[bytes]", np: Any) -> Optional[Any]:
    expected = IMAGE_SIZE * IMAGE_SIZE * 3
    if result.returncode != 0 or not result.stdout or len(result.stdout) < expected:
        return None
    return np.frombuffer(result.stdout[:expected], dtype=np.uint8).reshape(
        IMAGE_SIZE, IMAGE_SIZE, 3
    )


def candidate_frames(
    *,
    thumbnail_bytes: Optional[bytes] = None,
    preview_path: Optional[Path] = None,
    duration: float = 0.0,
) -> list:
    """Representative frames of one stock candidate, best source first.

    A served thumbnail is the cheapest representative frame there is;
    without one, frames are cut from the candidate's smallest video
    rendition at the FRAME_FRACTIONS points of its duration. Any
    failure yields fewer (or zero) frames — zero means unjudged.
    """
    if thumbnail_bytes:
        frame = frame_rgb_from_image_bytes(thumbnail_bytes)
        if frame is not None:
            return [frame]
    frames = []
    if preview_path is not None and Path(preview_path).is_file():
        length = duration if duration and duration > 0 else 3.0
        for fraction in FRAME_FRACTIONS:
            frame = frame_rgb_from_video(Path(preview_path), length * fraction)
            if frame is not None:
                frames.append(frame)
    return frames


# --- The scoring session -----------------------------------------------------


class VisionVetoSession:
    """One resolve pass's worth of SigLIP scoring.

    Constructing a session is cheap and loads nothing: the model loads
    lazily on the first score request and is released by :meth:`close`
    (the resolver closes the session when its pass ends), so the
    weights never sit in memory — or VRAM — across a render.

    ``available`` is False when the feature is off or the device gate
    declined; every public method then behaves as "no veto". Nothing
    in this class raises.
    """

    def __init__(self) -> None:
        self._model: Any = None
        self._tokenizer: Any = None
        self._device: Optional[str] = None
        self._load_attempted = False
        self._load_failed = False
        self.available = False
        if not vision_veto_enabled():
            return
        self._device = _vision_device()
        if self._device is None:
            return
        self.available = True

    # -- model lifecycle -------------------------------------------------

    def _ensure_loaded(self) -> bool:
        if self._model is not None:
            return True
        if self._load_attempted:
            return False
        self._load_attempted = True
        try:
            from ..media.separation import separation_models_dir

            os.environ.setdefault("HF_HOME", str(separation_models_dir()))
            import torch
            from transformers import AutoModel

            logger.info(
                "B-roll vision veto: loading %s (device=%s). %s",
                MODEL_ID,
                self._device,
                MODEL_DOWNLOAD_NOTE,
            )
            self._tokenizer = _load_tokenizer()
            model = AutoModel.from_pretrained(MODEL_ID)
            model.to(self._device)
            model.eval()
            self._model = model
            return True
        except Exception as exc:
            self._load_failed = True
            logger.warning(
                "B-roll vision veto unavailable (model load failed: %s); "
                "continuing without it",
                exc,
            )
            return False

    def close(self) -> None:
        """Release the model and any CUDA memory it held."""
        self._model = None
        self._tokenizer = None
        try:
            import gc

            gc.collect()
            import torch

            if torch.cuda.is_available():
                torch.cuda.empty_cache()
        except Exception:
            pass

    def __enter__(self) -> "VisionVetoSession":
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()

    # -- scoring -----------------------------------------------------------

    def score(self, frames: Sequence[Any], subject: str) -> Optional[float]:
        """Cosine similarity of ``subject`` to the best-matching frame.

        The decision takes the MAX over frames: a stock clip counts as
        on-subject when any representative frame clearly shows the
        subject, so a veto fires only when *no* frame matches — the
        conservative direction for a veto. Returns None (unjudged) on
        any failure: no frames, empty subject, model unavailable.
        """
        if not self.available or not frames or not (subject or "").strip():
            return None
        if not self._ensure_loaded():
            return None
        try:
            return float(self._score_loaded(frames, subject.strip()))
        except Exception as exc:
            logger.warning("B-roll vision veto scoring failed: %s", exc)
            return None

    def _score_loaded(self, frames: Sequence[Any], subject: str) -> float:
        import numpy as np
        import torch

        pixels = np.stack([np.asarray(frame) for frame in frames])
        pixel_values = torch.from_numpy(pixels).permute(0, 3, 1, 2).float()
        pixel_values = (pixel_values / 255.0 - 0.5) / 0.5
        pixel_values = pixel_values.to(self._device)

        text_inputs = self._tokenizer(
            [subject], padding="max_length", return_tensors="pt"
        )
        input_ids = text_inputs["input_ids"].to(self._device)

        with torch.no_grad():
            image_features, text_features = _embed(
                self._model, pixel_values, input_ids
            )
        image_features = image_features / image_features.norm(dim=-1, keepdim=True)
        text_features = text_features / text_features.norm(dim=-1, keepdim=True)
        similarities = (image_features @ text_features.T).squeeze(-1)
        return float(similarities.max().item())


def _load_tokenizer() -> Any:
    """The SigLIP tokenizer, with or without SentencePiece installed.

    The reference ``AutoTokenizer`` build needs the SentencePiece
    library, which this app does not carry. The model repo also ships
    ``tokenizer.json`` — the complete fast serialization of the same
    Unigram model (same normalizer, same ``</s>`` post-processing) —
    which the ``tokenizers`` library (already a transformers
    dependency) loads directly. Prefer the standard build where it
    works; fall back to the file build where it cannot.
    """
    from transformers import AutoTokenizer

    try:
        return AutoTokenizer.from_pretrained(MODEL_ID)
    except Exception:
        from huggingface_hub import hf_hub_download
        from transformers import PreTrainedTokenizerFast

        tokenizer_file = hf_hub_download(MODEL_ID, "tokenizer.json")
        tokenizer = PreTrainedTokenizerFast(tokenizer_file=tokenizer_file)
        # The repo's tokenizer_config: pad and eos are both "</s>",
        # sequences pad/truncate to the model's 64-token window.
        tokenizer.pad_token = "</s>"
        tokenizer.eos_token = "</s>"
        tokenizer.model_max_length = 64
        return tokenizer


def _embed(model: Any, pixel_values: Any, input_ids: Any) -> tuple[Any, Any]:
    """(image_embeds, text_embeds) across transformers versions.

    The full forward's ``image_embeds``/``text_embeds`` are the stable
    surface (present since SigLIP landed in transformers, and what
    current builds return). The fallback uses the get_*_features
    helpers for builds whose forward lacks the embeds; a helper that
    returns a tower output instead of a tensor is unwrapped and run
    through the model's projection when it exposes one.
    """
    outputs = model(pixel_values=pixel_values, input_ids=input_ids)
    image_features = getattr(outputs, "image_embeds", None)
    text_features = getattr(outputs, "text_embeds", None)
    if image_features is not None and text_features is not None:
        return image_features, text_features
    return (
        _features_via_getter(model, "get_image_features", "visual_projection", pixel_values),
        _features_via_getter(model, "get_text_features", "text_projection", input_ids),
    )


def _features_via_getter(model: Any, getter_name: str, projection_name: str, inputs: Any) -> Any:
    import torch

    result = getattr(model, getter_name)(inputs)
    if isinstance(result, torch.Tensor):
        return result
    pooled = getattr(result, "pooler_output", None)
    projection = getattr(model, projection_name, None)
    if pooled is not None and projection is not None:
        return projection(pooled)
    raise RuntimeError(f"{getter_name} returned no usable embeddings")
