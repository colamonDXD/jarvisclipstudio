"""OCR for brief screenshots (brief-image lane).

Campaign briefs usually arrive as screenshots, not text. This module
turns an uploaded brief image into plain text with the tesseract binary
the shipped image already carries — the same invocation contract as
``auto_director.reference_style._tesseract_words`` and
``media.visual_suitability`` (``shutil.which`` guard, PNG via stdin,
``OMP_THREAD_LIMIT=1``, timeout, silent degrade). The text then feeds
the EXISTING paste-a-brief extraction; there is deliberately no vision
model anywhere in this path.

Returns ``(text, available)``: ``available`` is False only when the
tesseract binary itself is missing (the shipped image carries it; a
bare local checkout may not), which is a different fact from "ran and
found nothing".
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
from typing import Optional, Tuple

logger = logging.getLogger(__name__)

MAX_BRIEF_IMAGE_BYTES = 10 * 1024 * 1024
BRIEF_IMAGE_EXTENSIONS = (".png", ".jpg", ".jpeg", ".webp")
_MIN_USEFUL_CHARS = 20


def _run_tesseract(png_bytes: bytes) -> Optional[str]:
    binary = shutil.which("tesseract")
    if binary is None:
        return None
    try:
        result = subprocess.run(
            [binary, "stdin", "stdout", "-l", "eng", "--psm", "6"],
            input=png_bytes,
            capture_output=True,
            timeout=30,
            env={**os.environ, "OMP_THREAD_LIMIT": "1"},
        )
    except Exception:
        return None
    if result.returncode != 0:
        return None
    return result.stdout.decode("utf-8", "replace")


def ocr_image_bytes(data: bytes) -> Tuple[Optional[str], bool]:
    """OCR one uploaded image's bytes to plain text.

    ``(None, False)`` = tesseract missing; ``(None, True)`` = the bytes
    are not a readable image or OCR produced nothing usable. A first
    pass that yields almost nothing is retried once at 2x upscale,
    which rescues small phone screenshots.
    """
    if shutil.which("tesseract") is None:
        return None, False
    try:
        import cv2
        import numpy as np

        image = cv2.imdecode(
            np.frombuffer(data, dtype=np.uint8), cv2.IMREAD_COLOR
        )
    except Exception:
        return None, True
    if image is None:
        return None, True

    def encode(img) -> Optional[bytes]:
        ok, buffer = cv2.imencode(".png", img)
        return buffer.tobytes() if ok else None

    png = encode(image)
    text = _run_tesseract(png) if png else None
    if text is None or len(text.strip()) < _MIN_USEFUL_CHARS:
        height, width = image.shape[:2]
        if max(height, width) < 2000:
            try:
                upscaled = cv2.resize(
                    image, (width * 2, height * 2), interpolation=cv2.INTER_CUBIC
                )
            except Exception:
                upscaled = None
            if upscaled is not None:
                png_up = encode(upscaled)
                retry = _run_tesseract(png_up) if png_up else None
                if retry and len(retry.strip()) > len((text or "").strip()):
                    text = retry
    if text is None or not text.strip():
        return None, True
    return text, True
